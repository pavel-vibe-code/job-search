#!/usr/bin/env python3
"""Validate candidate job listings against each ATS's authoritative JSON API.

Replaces the previous WebFetch + HTML-closure-signal validation, which was
unreliable for SPA-rendered ATS like Ashby (pages return empty shells to
non-JS clients, so "no closure signal" couldn't be distinguished from "no
content at all").

This validator instead asks each ATS API directly: "is this job ID still in
your active listings set?" — exactly the same question fetch-and-diff.py
asks during the initial fetch. Reuses the same API endpoints.

Usage:
    python3 validate-jobs.py --candidates /tmp/candidates.json \\
        --plugin-root /path/to/plugin \\
        --output /tmp/validate-output.json

Input file (--candidates): a JSON array of candidate objects from search-roles.
Each MUST have at minimum: id, url, company, title, ats. Other fields are
preserved verbatim for live entries.

Output:
    {
      "live": [<candidates that are still in their ATS's active set>],
      "closed": [{id, title, company, reason}],
      "uncertain": [{id, title, company, reason}],
      "summary": "Checked N: L live, C closed, U uncertain"
    }
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

ASHBY_API      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
COMEET_TOKEN_RE = re.compile(r'"token"\s*:\s*"([^"]+)"')
COMEET_API     = "https://www.comeet.co/careers-api/2.0/company/{company_id}/positions?token={token}&details=full"

USER_AGENT     = "Mozilla/5.0 (compatible; ai50-job-search/1.0)"
TIMEOUT_S      = 20

# URL-based ATS detection. Each entry: (compiled regex, ats_name, slug_capture_group).
# Patterns match the listing's URL field to derive ATS deterministically, even
# when the company name in the candidate doesn't match any index entry.
# Introduced v2.5.0 to fix the class of "uncertain" failures where index lookup
# misses (typos, casing, "skip"-marked-but-jobs-still-fetched) but URL is
# unambiguous. URL-based dispatch is the primary signal; name-index is fallback.
ATS_URL_PATTERNS = [
    (re.compile(r'^https?://(?:jobs|job-boards)\.ashbyhq\.com/([^/]+)'),                  'ashby',      1),
    # Greenhouse: classic + new + EU subdomain (data residency for EU customers)
    (re.compile(r'^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([^/]+)'),     'greenhouse', 1),
    (re.compile(r'^https?://jobs\.lever\.co/([^/]+)'),                                     'lever',      1),
    (re.compile(r'^https?://www\.comeet\.com/jobs/([^/]+)'),                               'comeet',     1),
]


def ats_from_url(url: Optional[str]) -> Optional[Tuple[str, str]]:
    """Parse a listing URL to derive (ats_name, slug). Returns None if no pattern matches.

    Used as the primary dispatch signal in validate-jobs (v2.5.0+). Replaces
    name-index lookup as the first attempt; name-index is fallback.
    """
    if not url:
        return None
    for pattern, ats, group_idx in ATS_URL_PATTERNS:
        m = pattern.match(url)
        if m:
            return ats, m.group(group_idx)
    return None


def http_get(url: str, accept: str = "application/json") -> Tuple[Optional[bytes], Optional[str]]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except urllib.error.URLError as e:
        return None, f"urlerror:{getattr(e, 'reason', e)}"
    except Exception as e:
        return None, f"error:{e}"


def fetch_active_ids_ashby(slug: str) -> Tuple[set, Optional[str]]:
    data, err = http_get(ASHBY_API.format(slug=slug))
    if err:
        return set(), err
    try:
        return {str(j.get("id")) for j in json.loads(data.decode("utf-8")).get("jobs", []) if j.get("id")}, None
    except Exception as e:
        return set(), f"parse:{e}"


def fetch_active_ids_greenhouse(slug: str) -> Tuple[set, Optional[str]]:
    data, err = http_get(GREENHOUSE_API.format(slug=slug))
    if err:
        return set(), err
    try:
        return {str(j.get("id")) for j in json.loads(data.decode("utf-8")).get("jobs", []) if j.get("id")}, None
    except Exception as e:
        return set(), f"parse:{e}"


def fetch_active_ids_comeet(slug: str, company_id: str, careers_url: str) -> Tuple[set, Optional[str]]:
    if not company_id:
        return set(), "missing_company_id"
    page, err = http_get(careers_url, accept="text/html")
    if err:
        return set(), f"page_{err}"
    page_text = page.decode("utf-8", errors="replace")
    m = COMEET_TOKEN_RE.search(page_text)
    if not m:
        return set(), "token_not_found"
    token = m.group(1)
    api_data, err = http_get(COMEET_API.format(company_id=company_id, token=token))
    if err:
        return set(), f"api_{err}"
    try:
        positions = json.loads(api_data.decode("utf-8"))
    except Exception as e:
        return set(), f"parse:{e}"
    if not isinstance(positions, list):
        return set(), "unexpected_shape"
    return {str(p.get("uid") or p.get("id")) for p in positions if (p.get("uid") or p.get("id"))}, None


def load_companies_index(companies_file: str, favorites_file: str) -> Dict[str, dict]:
    """Map company name (lowercased) → company config dict (with ats, slug, etc.).

    Precedence: companies.json wins on duplicate names — consistent with
    fetch-and-diff.py. Pre-v2.5.0 this script had favorites overriding
    companies (last-writer-wins by load order), creating a rare-but-real bug
    where a job that fetched fine via companies.json's `ats: greenhouse` would
    get marked uncertain in validate via favorites.json's `ats: skip` for the
    same company.

    Pre-v3.0.6 this function read paths hardcoded as `plugin_root/config/...`
    — fine in local mode, but in cloud mode the user's actual favorites live
    in Notion (hydrated to /tmp/favorites.json by orchestrator's P-4) while
    plugin_root/config/favorites.json stays as the shipped template. Pass 1
    used the hydrated data via fetch-and-diff's --favorites-file arg; Pass 2
    silently used the template, so user's actual favorites (Parloa, Nebius,
    JetBrains, Make, etc.) wouldn't be found and got marked
    `company_name_not_in_index`. v3.0.6 makes the file paths explicit args
    that the orchestrator passes per deployment mode.
    """
    idx: Dict[str, dict] = {}
    # Load favorites first, companies second — companies wins on duplicate.
    for path in (favorites_file, companies_file):
        if not path or not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)
        items = data.get("companies", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        for c in items:
            if isinstance(c, dict) and c.get("name"):
                idx[c["name"].strip().lower()] = c
    return idx


def slug_for(candidate: dict, companies: Dict[str, dict]) -> Optional[Tuple[str, dict]]:
    """Resolve (company_key, company_cfg) for a candidate by .company name."""
    name = (candidate.get("company") or "").strip().lower()
    if not name:
        return None
    cfg = companies.get(name)
    if cfg is None:
        return None
    return name, cfg


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidates", required=True, help="Path to candidates JSON array")
    p.add_argument("--plugin-root", required=True, help="Plugin root (used for default companies/favorites file paths if explicit args not given)")
    p.add_argument("--companies-file", default=None,
                   help="companies.json path. Default: <plugin-root>/config/companies.json. "
                        "In cloud mode the orchestrator should pass /tmp/companies.json (Notion-hydrated).")
    p.add_argument("--favorites-file", default=None,
                   help="favorites.json path. Default: <plugin-root>/config/favorites.json. "
                        "In cloud mode the orchestrator should pass /tmp/favorites.json (Notion-hydrated). "
                        "Pre-v3.0.6 this defaulted hardcoded to plugin-root, causing user-added favorites "
                        "in Notion to be invisible to Pass 2 (silently marking them company_name_not_in_index).")
    p.add_argument("--output", default="/tmp/validate-output.json", help="Where to write validation results")
    p.add_argument("--max-workers", type=int, default=10)
    args = p.parse_args()

    companies_file = args.companies_file or os.path.join(args.plugin_root, "config", "companies.json")
    favorites_file = args.favorites_file or os.path.join(args.plugin_root, "config", "favorites.json")

    with open(args.candidates) as f:
        candidates = json.load(f)

    if not isinstance(candidates, list):
        print(json.dumps({"error": "candidates file must be a JSON array"}), file=sys.stderr)
        sys.exit(2)

    if not candidates:
        out = {"live": [], "closed": [], "uncertain": [], "summary": "Checked 0: 0 live, 0 closed, 0 uncertain"}
        with open(args.output, "w") as f:
            json.dump(out, f, indent=2)
        print(json.dumps(out))
        return

    companies = load_companies_index(companies_file, favorites_file)

    # Group candidates by (ats, slug). One API call per group fetches the full
    # active-id set, against which we test every candidate in that group.
    #
    # Dispatch order (v2.5.0+):
    #   1. Try URL-based ATS detection (parses candidate.url against known ATS host
    #      patterns). Deterministic; works even when the company name doesn't match
    #      any index entry.
    #   2. Fall back to name-index lookup (legacy path).
    # Both paths converge on the same (ats, slug) group key.
    groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    unknown_company: List[Tuple[dict, str]] = []  # (candidate, reason_hint)

    for c in candidates:
        # 1. URL-based dispatch (primary)
        url_resolved = ats_from_url(c.get("url"))
        if url_resolved is not None:
            ats, slug = url_resolved
            if ats in ("ashby", "greenhouse", "comeet"):
                groups[(ats, slug)].append({**c, "_cfg": {"ats": ats, "slug": slug, "_resolved_via": "url"}})
                continue
            # URL recognized but ATS not supported (e.g. lever) — name-index might still help
            # if the company is registered with an alternate ats; fall through to name lookup.

        # 2. Name-index dispatch (fallback)
        resolved = slug_for(c, companies)
        if resolved is None:
            unknown_company.append((c, "company_name_not_in_index"))
            continue
        _, cfg = resolved
        ats = cfg.get("ats")
        slug = cfg.get("slug") or cfg.get("company_id") or ""
        if ats not in ("ashby", "greenhouse", "comeet"):
            unknown_company.append((c, f"ats_unsupported:{ats}"))
            continue
        groups[(ats, slug)].append({**c, "_cfg": cfg})

    # Fetch active id sets per group, in parallel
    active: Dict[Tuple[str, str], set] = {}
    fetch_errors: Dict[Tuple[str, str], str] = {}

    def fetch_group(key: Tuple[str, str]) -> Tuple[Tuple[str, str], set, Optional[str]]:
        ats, slug = key
        if ats == "ashby":
            ids, err = fetch_active_ids_ashby(slug)
        elif ats == "greenhouse":
            ids, err = fetch_active_ids_greenhouse(slug)
        elif ats == "comeet":
            cfg = groups[key][0]["_cfg"]
            careers_url = cfg.get("careers_url") or f'https://www.comeet.com/jobs/{slug}/{cfg.get("company_id", "")}'
            ids, err = fetch_active_ids_comeet(slug, cfg.get("company_id", ""), careers_url)
        else:
            ids, err = set(), f"unsupported_ats:{ats}"
        return key, ids, err

    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futs = [ex.submit(fetch_group, k) for k in groups]
        for fut in as_completed(futs):
            key, ids, err = fut.result()
            if err:
                fetch_errors[key] = err
            else:
                active[key] = ids

    # Classify each candidate
    live: List[dict] = []
    closed: List[dict] = []
    uncertain: List[dict] = []

    for key, items in groups.items():
        if key in fetch_errors:
            for c in items:
                c.pop("_cfg", None)
                uncertain.append({"id": c.get("id"), "title": c.get("title"), "company": c.get("company"), "reason": f"api_{fetch_errors[key]}"})
            continue
        ids = active.get(key, set())
        for c in items:
            c.pop("_cfg", None)
            if str(c.get("id")) in ids:
                live.append(c)
            else:
                closed.append({"id": c.get("id"), "title": c.get("title"), "company": c.get("company"), "reason": "id_not_in_active_set"})

    for c, reason in unknown_company:
        uncertain.append({"id": c.get("id"), "title": c.get("title"), "company": c.get("company"), "url": c.get("url"), "reason": reason})

    out = {
        "live": live,
        "closed": closed,
        "uncertain": uncertain,
        "summary": f"Checked {len(candidates)}: {len(live)} live, {len(closed)} closed, {len(uncertain)} uncertain",
        "groups": {f"{a}:{s}": {"count": len(items), "fetch_error": fetch_errors.get((a, s))} for (a, s), items in groups.items()},
    }
    with open(args.output, "w") as f:
        json.dump(out, f, indent=2)
    # Stdout: brief one-line summary so the agent can quote it without parsing the file
    print(out["summary"])


if __name__ == "__main__":
    main()
