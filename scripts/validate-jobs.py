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
import importlib.util
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

# Load ats_adapters module from sibling file (the dash in script names prevents
# ordinary `import ats_adapters` from working since validate-jobs is also dashed
# and not a package).
_ADAPTERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ats_adapters.py")
_spec = importlib.util.spec_from_file_location("ats_adapters", _ADAPTERS_PATH)
ats_adapters = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ats_adapters)

# Re-export for backward compat with tests + external callers
ats_from_url             = ats_adapters.ats_from_url
ATS_ADAPTERS             = ats_adapters.ATS_ADAPTERS
supported_ats_for_validate = ats_adapters.supported_ats_for_validate
active_ids_for           = ats_adapters.active_ids_for


def load_companies_index(companies_file: str, custom_companies_file: str) -> Dict[str, dict]:
    """Map company name (lowercased) → company config dict (with ats, slug, etc.).

    Precedence: AI 50 baseline (companies.json) wins on duplicate names —
    consistent with fetch-and-diff.py. The custom-companies file extends the
    baseline; if a user adds a company already in the baseline, the baseline's
    config wins (the user's entry is silently ignored).

    Historical context: pre-v4.0.0 this file was named `favorites.json` and
    the CLI flag was `--favorites-file`. Renamed in v4.0.0 to better reflect
    the conceptual role (extending the AI 50 baseline). The legacy filename
    is still accepted as a fallback for in-place upgrades.
    """
    idx: Dict[str, dict] = {}
    # Load custom companies first, baseline second — baseline wins on duplicate.
    for path in (custom_companies_file, companies_file):
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
    p.add_argument("--plugin-root", required=True, help="Plugin root (used for default companies/custom-companies file paths if explicit args not given)")
    p.add_argument("--companies-file", default=None,
                   help="companies.json path (the AI 50 baseline). Default: <plugin-root>/config/companies.json. "
                        "In cloud mode the orchestrator should pass /tmp/companies.json (Notion-hydrated).")
    p.add_argument("--custom-companies-file", "--favorites-file", default=None,
                   dest="custom_companies_file",
                   help="custom-companies.json path (additional companies on top of "
                        "AI 50 baseline). Default: <plugin-root>/config/custom-companies.json. "
                        "In cloud mode the orchestrator should pass /tmp/custom-companies.json "
                        "(Notion-hydrated). The legacy --favorites-file flag is accepted as an "
                        "alias for backward compat with pre-v4.0.0 orchestrators.")
    p.add_argument("--output", default="/tmp/validate-output.json", help="Where to write validation results")
    p.add_argument("--max-workers", type=int, default=10)
    args = p.parse_args()

    companies_file = args.companies_file or os.path.join(args.plugin_root, "config", "companies.json")
    custom_companies_file = args.custom_companies_file or os.path.join(args.plugin_root, "config", "custom-companies.json")
    # Legacy fallback: pre-v4.0.0 file was config/favorites.json
    if not os.path.exists(custom_companies_file):
        _legacy = os.path.join(args.plugin_root, "config", "favorites.json")
        if os.path.exists(_legacy):
            custom_companies_file = _legacy

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

    companies = load_companies_index(companies_file, custom_companies_file)

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

    supported_ats = supported_ats_for_validate()

    for c in candidates:
        # 1. URL-based dispatch (primary)
        url_resolved = ats_from_url(c.get("url"))
        if url_resolved is not None:
            ats, slug = url_resolved
            if ats in supported_ats:
                groups[(ats, slug)].append({**c, "_cfg": {"ats": ats, "slug": slug, "_resolved_via": "url"}})
                continue
            # URL recognized but ATS not supported by validate (rare; ATS_ADAPTERS
            # would have to mark active_validate_supported=False) — fall through.

        # 2. Name-index dispatch (fallback)
        resolved = slug_for(c, companies)
        if resolved is None:
            unknown_company.append((c, "company_name_not_in_index"))
            continue
        _, cfg = resolved
        ats = cfg.get("ats")
        slug = cfg.get("slug") or cfg.get("company_id") or ""
        if ats not in supported_ats:
            unknown_company.append((c, f"ats_unsupported:{ats}"))
            continue
        groups[(ats, slug)].append({**c, "_cfg": cfg})

    # Fetch active id sets per group, in parallel
    active: Dict[Tuple[str, str], set] = {}
    fetch_errors: Dict[Tuple[str, str], str] = {}

    def fetch_group(key: Tuple[str, str]) -> Tuple[Tuple[str, str], set, Optional[str]]:
        ats, slug = key
        # Pass adapter-specific kwargs (only comeet currently needs extras).
        # Looking up the group's first item gives access to its _cfg, which carries
        # comeet's company_id + careers_url. Other adapters ignore extra kwargs.
        cfg = groups[key][0].get("_cfg", {})
        kwargs = {}
        if ats == "comeet":
            kwargs["company_id"]  = cfg.get("company_id", "")
            kwargs["careers_url"] = cfg.get("careers_url") or f'https://www.comeet.com/jobs/{slug}/{cfg.get("company_id", "")}'
        ids, err = active_ids_for(ats, slug, **kwargs)
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
