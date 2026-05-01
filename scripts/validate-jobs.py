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


def load_companies_index(plugin_root: str) -> Dict[str, dict]:
    """Map company name (lowercased) → company config dict (with ats, slug, etc.)."""
    idx: Dict[str, dict] = {}
    for path in (
        os.path.join(plugin_root, "config", "companies.json"),
        os.path.join(plugin_root, "config", "favorites.json"),
    ):
        if not os.path.exists(path):
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
    p.add_argument("--plugin-root", required=True, help="Plugin root (for companies.json / favorites.json)")
    p.add_argument("--output", default="/tmp/validate-output.json", help="Where to write validation results")
    p.add_argument("--max-workers", type=int, default=10)
    args = p.parse_args()

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

    companies = load_companies_index(args.plugin_root)

    # Group candidates by (ats, slug). One API call per group fetches the full
    # active-id set, against which we test every candidate in that group.
    groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    unknown_company: List[dict] = []
    for c in candidates:
        resolved = slug_for(c, companies)
        if resolved is None:
            unknown_company.append(c)
            continue
        _, cfg = resolved
        ats = cfg.get("ats")
        slug = cfg.get("slug") or cfg.get("company_id") or ""
        if ats not in ("ashby", "greenhouse", "comeet"):
            # html_static / static_roles / external — fall through; we can't validate via API
            unknown_company.append(c)
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

    for c in unknown_company:
        uncertain.append({"id": c.get("id"), "title": c.get("title"), "company": c.get("company"), "reason": "no_api_for_ats_or_company_unknown"})

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
