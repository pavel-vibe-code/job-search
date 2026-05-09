#!/usr/bin/env python3
"""
diff-scrape.py — diff a scrape-extract result against state, update state.

Used by the search-roles agent (v4.0.0+) after dispatching scrape-extract for
a scrape-tracked company. Replaces the per-company diff that pre-v4.0.0
fetch-and-diff.py did inline for `ats: scrape` entries.

Usage:
    python3 scripts/diff-scrape.py \\
        --extracted /tmp/scrape-extract-adfin.json \\
        --state state/companies.json \\
        --company-key scrape:adfin \\
        --company-name Adfin

Reads:
    --extracted     scrape-extract agent's output envelope (the file scrape-extract
                    wrote — has shape {company, careers_url, jobs: [...], ...}).
    --state         the state file to read+update. Default: state/companies.json.
    --company-key   state-file key for this company (e.g. "scrape:adfin"). The
                    fetch-and-diff naming convention is "<ats>:<slug-or-name>".
    --company-name  display name of the company (for diff output enrichment).

Writes (stdout):
    JSON envelope with new_jobs, removed_jobs, and a summary. Same field shapes
    as fetch-and-diff.py's per-company contribution to its full-run output.

Mutates:
    The state file at --state, replacing state[company_key] with the new
    snapshot {last_checked, company_name, jobs: {...}}.

Exit codes:
    0 on success (even if extraction had zero jobs), 1 on usage/IO errors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_state(state: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# Streak threshold for surfacing a sustained-failure warning. 3 consecutive
# error fires means we're not just hitting a transient blip — the company's
# extraction path is reliably broken and the user should reconsider its
# entry (try a different careers_url, switch to a deterministic ATS adapter
# if there is one, or remove from the tracked set).
SCRAPE_STREAK_THRESHOLD = 3
DEFAULT_STREAK_FILE     = "state/scrape-streaks.json"


def _is_failure_signal(envelope: dict) -> bool:
    """A scrape result counts as a 'failure' for streak purposes when it
    yields zero usable jobs AND the path through scrape-extract was not
    a clean success. Specifically:
      - envelope has an `error` field (fetch_failed / non_html / empty_page)
      - extraction_quality is "no_static_content" with zero jobs
    A successful 0-job extraction (extraction_quality "ok", jobs=[]) is
    NOT a failure — the company genuinely has no openings right now and
    that's a stable state worth recording, not warning about.
    """
    if envelope.get("error"):
        return True
    jobs = envelope.get("jobs") or []
    quality = envelope.get("extraction_quality")
    if quality == "no_static_content" and not jobs:
        return True
    return False


def _update_streak(streak_path: str, company_key: str, is_failure: bool) -> dict:
    """Read+update the per-company streak counter. Returns the streak record
    {streak: int, last_failure_at: str|None, threshold_breached: bool}."""
    streaks = load_json(streak_path) if os.path.exists(streak_path) else {}
    rec = streaks.get(company_key, {"streak": 0, "last_failure_at": None})
    if is_failure:
        rec["streak"] = int(rec.get("streak", 0)) + 1
        rec["last_failure_at"] = date.today().isoformat()
    else:
        rec["streak"] = 0
        # keep last_failure_at as historical breadcrumb; don't clear
    streaks[company_key] = rec
    save_state(streaks, streak_path)
    rec["threshold_breached"] = rec["streak"] >= SCRAPE_STREAK_THRESHOLD
    return rec


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--extracted", required=True, help="scrape-extract envelope JSON path")
    p.add_argument("--state", default="state/companies.json", help="state file path")
    p.add_argument("--company-key", required=True, help="state-file key, e.g. 'scrape:adfin'")
    p.add_argument("--company-name", required=True, help="display name for diff entries")
    p.add_argument("--streak-file", default=DEFAULT_STREAK_FILE,
                   help="per-company sustained-failure streak counter (JSON). "
                        "Default: state/scrape-streaks.json. Tracks consecutive "
                        "failed extractions; threshold breach surfaces a warning "
                        "in the output for the orchestrator to relay to the user.")
    args = p.parse_args()

    # Update the failure-streak counter regardless of envelope shape.
    streak_rec = _update_streak(
        args.streak_file, args.company_key,
        is_failure=_is_failure_signal(load_json(args.extracted)) if os.path.exists(args.extracted) else True,
    )

    if not os.path.exists(args.extracted):
        print(json.dumps({"error": "extracted_file_missing", "path": args.extracted}), file=sys.stderr)
        return 1

    envelope = load_json(args.extracted)

    # If scrape-extract returned an error envelope, propagate it cleanly so the
    # orchestrator can record this in fetch_errors and continue.
    if envelope.get("error"):
        out = {
            "company_key":  args.company_key,
            "company_name": args.company_name,
            "new_jobs":     [],
            "removed_jobs": [],
            "error":        envelope.get("error"),
            "detail":       envelope.get("detail", ""),
            "streak":       streak_rec["streak"],
        }
        if streak_rec["threshold_breached"]:
            out["warning"] = (
                f"{args.company_name}: {streak_rec['streak']} consecutive failed "
                f"extractions. The careers URL or scrape strategy isn't working "
                f"for this company — consider reclassifying ats type, supplying a "
                f"different careers_url, or removing from the tracked set."
            )
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0  # not a usage error; the orchestrator decides what to do

    extracted_jobs = envelope.get("jobs", []) or []

    # Load (or initialise) state.
    if os.path.exists(args.state):
        state = load_json(args.state)
    else:
        state = {}

    known_company = state.get(args.company_key, {})
    known_jobs    = known_company.get("jobs", {}) or {}

    # Compute diff. extracted_jobs entries are canonical-shape from scrape-extract:
    # {id, title, url, location, department}. Treat extracted as the new
    # full set of active jobs for this company.
    extracted_by_id = {str(j.get("id", "")): j for j in extracted_jobs if j.get("id")}
    new_ids     = set(extracted_by_id.keys()) - set(known_jobs.keys())
    removed_ids = set(known_jobs.keys())     - set(extracted_by_id.keys())

    new_jobs = []
    for jid in sorted(new_ids):
        j = extracted_by_id[jid]
        new_jobs.append({
            "id":             jid,
            "company":        args.company_name,
            "title":          j.get("title", ""),
            "url":            j.get("url", ""),
            "location":       j.get("location", ""),
            "is_remote":      "remote" in str(j.get("location", "")).lower(),
            "workplace_type": "Remote" if "remote" in str(j.get("location", "")).lower() else "",
            "department":     j.get("department", "") or "",
            "published_at":   "",
            "source":         envelope.get("source", "scrape"),
            "ats":            "scrape",
            "description":    "",  # scrape doesn't fetch JD bodies; compile-write may re-fetch
            "extraction":     "agent",  # marker: extracted via scrape-extract agent (v4.0.0+)
        })

    removed_jobs = []
    for jid in sorted(removed_ids):
        prior = known_jobs.get(jid, {})
        removed_jobs.append({
            "id":      jid,
            "company": args.company_name,
            "title":   prior.get("title", ""),
            "url":     prior.get("url", ""),
            "ats":     "scrape",
        })

    # Update state for this company.
    state[args.company_key] = {
        "last_checked": date.today().isoformat(),
        "company_name": args.company_name,
        "jobs": {
            jid: {"title": j.get("title", ""), "url": j.get("url", ""), "company": args.company_name}
            for jid, j in extracted_by_id.items()
        },
    }
    save_state(state, args.state)

    out = {
        "company_key":  args.company_key,
        "company_name": args.company_name,
        "new_jobs":     new_jobs,
        "removed_jobs": removed_jobs,
        "extraction_quality": envelope.get("extraction_quality", "ok"),
        "source":       envelope.get("source", "page"),
        "streak":       streak_rec["streak"],
        "summary": (
            f"{args.company_name}: {len(extracted_jobs)} extracted "
            f"({len(new_jobs)} new, {len(removed_jobs)} removed)"
        ),
    }
    if streak_rec["threshold_breached"]:
        # Reaches here when the envelope itself is a failure signal (no_static_content
        # with empty jobs) — a successful extraction would have reset the streak above.
        out["warning"] = (
            f"{args.company_name}: {streak_rec['streak']} consecutive failed extractions."
        )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
