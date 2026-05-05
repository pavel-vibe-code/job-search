#!/usr/bin/env python3
"""
validate-favorites.py — slug-variant probing fallback for the setup wizard.

Tests every entry in custom-companies.json against its configured ATS JSON API
endpoint. For entries that return 404, tries common slug variants across the
three platforms it knows about (ashby / greenhouse / lever). Outputs a JSON
report to stdout. (Filename retained for git-history continuity; the data
store is custom-companies.json.)

Usage:
    python3 validate-favorites.py [--plugin-root /path/to/plugin]
"""

import importlib.util
import json
import os
import sys
import urllib.request
import urllib.error
from itertools import product

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "--plugin-root" in sys.argv:
    idx = sys.argv.index("--plugin-root")
    PLUGIN_ROOT = sys.argv[idx + 1]

CUSTOM_COMPANIES_FILE = os.path.join(PLUGIN_ROOT, "config", "custom-companies.json")

# Load shared ATS registry — single source of truth for URL→ATS detection.
# Same loader pattern as validate-jobs.py (the dash in script filenames
# prevents ordinary `import ats_adapters`).
_ADAPTERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ats_adapters.py")
_spec = importlib.util.spec_from_file_location("ats_adapters", _ADAPTERS_PATH)
_ats_adapters = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ats_adapters)
ats_from_url = _ats_adapters.ats_from_url

ASHBY_API      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API      = "https://api.lever.co/v0/postings/{slug}?mode=json"

FETCH_TIMEOUT = 12
MAX_VARIANT_ATTEMPTS = 12  # cap probe HTTP requests per entry to avoid hangs on bad URLs


def build_url(ats: str, slug: str) -> str:
    if ats == "ashby":
        return ASHBY_API.format(slug=slug)
    elif ats == "greenhouse":
        return GREENHOUSE_API.format(slug=slug)
    elif ats == "lever":
        return LEVER_API.format(slug=slug)
    return ""


def probe(ats: str, slug: str) -> tuple[int, int, str]:
    """
    Returns (http_code, job_count, error).
    job_count is -1 if the response could not be parsed.
    """
    url = build_url(ats, slug)
    if not url:
        return 0, -1, f"unknown_ats:{ats}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ai50-job-search/1.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read(2_000_000).decode("utf-8"))
            jobs = data.get("jobs", [])
            return 200, len(jobs), ""
    except urllib.error.HTTPError as e:
        return e.code, -1, ""
    except Exception as e:
        return 0, -1, str(e)


def slug_variants(name: str, original_slug: str) -> list[tuple[str, str]]:
    """
    Generate (ats, slug) candidates to try when the configured endpoint fails.
    """
    # Derive candidate slugs from company name
    base = name.lower().strip()
    words = base.split()
    candidates = list({
        original_slug,
        base.replace(" ", ""),
        base.replace(" ", "-"),
        base.replace(" ", "_"),
        "".join(words),
        "-".join(words),
        # Common suffixes/prefixes
        "".join(words) + "ai",
        "-".join(words) + "-ai",
        "".join(words) + "hq",
        "-".join(words) + "-hq",
        "".join(words) + "labs",
        "join" + "".join(words),
        "".join(words[:-1]) if len(words) > 1 else "",  # drop last word (e.g. "AI", "Labs")
    })
    candidates = [s for s in candidates if s and len(s) >= 2]

    platforms = ["ashby", "greenhouse", "lever"]
    return [(ats, slug) for ats, slug in product(platforms, candidates)]


def validate_entry(entry: dict) -> dict:
    name        = entry.get("name", "Unknown")
    ats         = entry.get("ats", "")
    slug        = entry.get("slug", "") or ""
    careers_url = entry.get("careers_url", "") or ""
    source      = entry.get("source", "favorites")

    result = {
        "name":     name,
        "source":   source,
        "status":   None,      # "ok" | "empty" | "failed" | "misconfigured" | "chrome" | "ok_via_url"
        "ats":      ats,
        "slug":     slug,
        "job_count": None,
        "suggestion": None,
        "error":    None,
    }

    # Path 1 (v2.5.0): if careers_url is provided, derive ats+slug deterministically
    # from URL pattern. Bypasses the slow slug-variant probe loop entirely.
    # If URL maps to a supported ATS, override entry's ats/slug with parsed values.
    if careers_url:
        url_resolved = ats_from_url(careers_url)
        if url_resolved is not None:
            url_ats, url_slug = url_resolved
            ats  = url_ats
            slug = url_slug
            result["ats"]  = ats
            result["slug"] = slug
            result["resolved_via"] = "url"
        else:
            # URL provided but unrecognized (e.g. workable.com, lever was matched but
            # we may add personio/etc later) — keep going with entry's ats/slug if any
            result["resolved_via"] = "url_unrecognized_fallback_to_entry_ats"

    # Chrome-only entry — nothing to probe via API
    if ats == "chrome":
        result["status"] = "chrome"
        result["error"]  = "Requires Claude in Chrome connector — no API to validate"
        return result

    # Missing required fields
    if not ats or not slug:
        result["status"] = "misconfigured"
        result["error"]  = "Missing 'ats'/'slug' field and no parseable careers_url"
        return result

    if ats not in ("ashby", "greenhouse", "lever"):
        result["status"] = "misconfigured"
        result["error"]  = f"Unknown ats value '{ats}' — must be ashby, greenhouse, lever, or chrome"
        return result

    # Probe configured endpoint
    code, count, err = probe(ats, slug)

    if code == 200:
        result["status"]    = "ok" if count > 0 else "empty"
        result["job_count"] = count
        if count == 0:
            result["error"] = "Endpoint is valid but returned 0 jobs — company may have no open roles, or the slug may be wrong"
        return result

    # Failed — try variants (capped at MAX_VARIANT_ATTEMPTS to avoid hangs on bad URLs)
    result["error"] = f"http_{code}" if code else err
    tried = set()
    tried.add((ats, slug))
    attempts = 0

    for try_ats, try_slug in slug_variants(name, slug):
        if (try_ats, try_slug) in tried:
            continue
        tried.add((try_ats, try_slug))
        attempts += 1
        if attempts > MAX_VARIANT_ATTEMPTS:
            result["error"] = f"{result['error']} | gave up after {MAX_VARIANT_ATTEMPTS} variants"
            break
        try_code, try_count, _ = probe(try_ats, try_slug)
        if try_code == 200:
            result["status"]     = "failed_with_suggestion"
            result["suggestion"] = {"ats": try_ats, "slug": try_slug, "job_count": try_count}
            return result

    result["status"] = "failed"
    return result


def main():
    if not os.path.exists(CUSTOM_COMPANIES_FILE):
        print(json.dumps({"error": f"custom-companies.json not found at {CUSTOM_COMPANIES_FILE}"}))
        sys.exit(1)

    with open(CUSTOM_COMPANIES_FILE) as f:
        entries = json.load(f)

    # Skip leading _meta entry (v2.x convention preserved through v4.0.0).
    if entries and isinstance(entries[0], dict) and entries[0].get("_meta"):
        entries = entries[1:]

    results  = [validate_entry(e) for e in entries]
    ok       = [r for r in results if r["status"] == "ok"]
    empty    = [r for r in results if r["status"] == "empty"]
    suggest  = [r for r in results if r["status"] == "failed_with_suggestion"]
    failed   = [r for r in results if r["status"] == "failed"]
    misconf  = [r for r in results if r["status"] == "misconfigured"]
    chrome   = [r for r in results if r["status"] == "chrome"]

    output = {
        "results": results,
        "summary": {
            "total":       len(entries),
            "ok":          len(ok),
            "empty":       len(empty),
            "suggestion":  len(suggest),
            "failed":      len(failed),
            "misconfigured": len(misconf),
            "chrome_only": len(chrome),
        },
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
