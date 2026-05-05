---
name: jobs-rescore
description: Re-evaluate existing tracker rows by re-running the v3 scoring prompt against them. Use this to fix rows with empty Why Fits / Key Factors (data debt from earlier runs that didn't populate them), to rescore after a profile change, or to upgrade past Sonnet-scored entries to Opus quality. Updates Why Fits, Key Factors, and Match in place; preserves user-set fields (Match Quality, Status, Feedback Comment, Recycled). Trigger phrases include "jobs-rescore", "re-evaluate tracker", "fix empty rationales", "re-run scoring on tracker", "update rationales".
version: 1.0.0
---

## What this skill does

Existing tracker rows have their LLM-derived fields (`Match`, `Why Fits`, `Key Factors`) populated only at creation time. Subsequent pipeline runs add NEW rows but never update existing ones (deduped by URL). This means:

- Rows from buggy past runs stay broken until something explicitly updates them
- Profile changes (criteria, hard exclusions) don't retroactively re-evaluate already-tracked roles
- Model upgrades (e.g. Sonnet → Opus) don't lift past entries to higher quality

`jobs-rescore` solves all three by selectively re-running the scoring prompt on existing tracker rows and updating in place. Non-destructive — preserves every user-edited column (Match Quality labels, Status changes, Feedback Comments).

## When NOT to use it

- For NEW jobs from this week's pipeline — that's `jobs-run`'s job; no need to re-run scoring on already-fresh writes.
- For deleting bad rows — jobs-rescore updates; doesn't delete. Use Notion's row-delete UI or extend with `archive` mode if you need it.
- For rescoring closed rows — by default skips rows where `Status: Closed` (they're no longer actionable; rescoring wastes tokens). Override with `--include-closed` if you really want to rescore historical entries.

## Step 0 — Determine deployment mode + load context

Read `state/.setup_complete[deployment_mode]` and `[auth_method]`.

In **cloud mode**: hydrate profile from the AI 50 Profile Notion page to `/tmp/profile.json` (same as jobs-run Pass P-4). The profile MUST have `cv_json` for the v3 scoring path; otherwise abort with: *"jobs-rescore requires CV-grounded categorical scoring (cv_json in profile). Run setup again with the CV upload step, or use the legacy structured-rubric scoring path which already runs at create-time and isn't bug-prone."*

In **local mode**: read `./config/profile.json` directly.

Run `notion-api.py discover` first to refresh `state/cached-ids.json` (defensive — same pattern jobs-recycle-feedback uses; per-installation caches drift). Capture the resolved `tracker_database_id`.

## Step 1 — Pick scope

Print:

```
━━━ Re-score tracker rows ━━━

Scope:
  1. Empty rationales only       (rows where Why Fits is empty)
  2. All rows since YYYY-MM-DD   (date-bounded — most recent N runs)
  3. Specific Match bucket       (e.g. all Mid, or all High)
  4. Specific company            (filter by Company name)
  5. All rows                    (expensive — confirm cost first)

You can also describe in plain English ("all my Adapty entries", "everything from last week", "rows I haven't reviewed yet").
```

Wait for response. Map the user's choice to a Notion `query-database` filter:

- **(1) Empty rationales**: `{property: "Why Fits", rich_text: {is_empty: true}}`
- **(2) Date-bounded**: `{property: "Date Added", date: {on_or_after: <date>}}`
- **(3) Match bucket**: `{property: "Match", select: {equals: "<bucket>"}}`
- **(4) Company**: `{property: "Company", rich_text: {contains: "<name>"}}`
- **(5) All rows**: no filter (potentially huge — confirm cost)

By default, AND-combine the user's filter with `{property: "Status", select: {does_not_equal: "Closed"}}` to skip closed entries. Override with `--include-closed` flag if user explicitly asks.

Run the query:

```bash
python3 ./scripts/notion-api.py query-database \
  --database-id <tracker_database_id> \
  --filter /tmp/re-score-filter.json
```

Save matching rows (URL, Title, Company, Location, Department, current Match, Status, page_id) to `/tmp/re-score-targets.json`.

## Step 2 — Preview cost

Compute estimated cost based on:
- Row count (N from Step 1)
- Profile.scoring.model (Opus 4.7 default; user can override per row or globally)
- Approximate token use per call: ~12K input (profile + cv + per-candidate context), ~500 output, ~4K thinking budget (Opus path)

Print:

```
Found 40 rows matching your scope.

Re-scoring on Claude Opus 4.7 (default — change via profile.scoring.model):
  Input tokens (est):       ~480K  (12K × 40)
  Output tokens (est):       ~20K  (500 × 40)
  Thinking tokens (est):    ~160K  (4K × 40)
  Subscription quota cost:  ~$8-15 equivalent

Use Sonnet to cut ~75%:
  Input/output equivalent:  ~$2-3 equivalent

Proceed? (yes / use-sonnet / cancel)
```

If user says "use-sonnet", set the per-call model override to `claude-sonnet-4-6` for this skill invocation only (don't mutate profile.json).

## Step 3 — Optionally re-fetch JD bodies

For best scoring quality, re-fetch the candidate's full JD from its URL. Three paths based on `ats`:

- **Deterministic ATS** (ashby/greenhouse/lever/comeet/teamtailor/homerun): hit the per-job endpoint to get a fresh JD body. If the endpoint 404s (job closed), skip the row and report it as failed-fetch.
- **Scrape ATS**: re-dispatch the `scrape-extract` agent on the careers URL, then look up the matching job by URL match.
- **Static / unsupported**: skip JD re-fetch; use what's already in the tracker (Title + Department + Location).

Re-fetching gives the LLM real context but is slower + costs ATS API calls. Offer the user a choice:

```
JD re-fetch options:
  A. Re-fetch from ATS API     (slower, more accurate scoring — recommended)
  B. Use only what's in tracker  (faster, less accurate — skips JD detail)

Pick:
```

Default A. For users with many scrape-tracked rows (each re-fetch is a Haiku call), B might be preferable to manage cost.

If A: write fetched JDs to `/tmp/re-score-jds.json` keyed by URL.

## Step 4 — Re-score each row

For each row in `/tmp/re-score-targets.json`:

1. Build a synthetic candidate payload: `{title, company, location, department, url, description: <fetched JD or stub>, ats, region, regional_remote_score (recompute or carry forward)}`.

2. Call the v3 scoring prompt (same as compile-write Step 3.v3). Parse response: `{verdict, rationale, key_factors, confidence}`.

3. **Apply hard exclusions** (same as compile-write Step 2). If a row that was originally Mid is now hard-excluded by an updated profile, mark its Status as "Not interested" automatically and add a Why Fits note: *"Re-score: now hard-excluded by profile [reason]. Manual review."* — don't silently drop it; the user should see what changed.

4. Build the property-update payload:
   ```json
   {
     "Match": {"select": {"name": "<verdict>"}},
     "Why Fits": "<rationale>",
     "Key Factors": "<key_factors joined by \\n>"
   }
   ```
   **Do NOT include**: Status (preserve user's triage), Match Quality (preserve user's labels), Feedback Comment (preserve), Recycled (preserve), Date Added (preserve). The skill ONLY updates LLM-derived columns.

5. Call `notion-api.py update-page --page-id <row_id> --properties /tmp/re-score-update-<i>.json`.

6. Track verdict-change events: if the new verdict differs from the original Match, record it in `verdict_changes`.

If the user picked `show_low: true` in their profile but a row's new verdict is Low and the old was Mid/High, write the Low to the tracker. If `show_low: false` (default) and a row downgrades to Low, leave the Match column populated as Low but flag the row in the summary — don't auto-archive.

## Step 5 — Summary

```
━━━ Re-score complete ━━━
Updated: 38 rows
  Verdict changed: 4
    • Anthropic FDE Paris: Mid → High  (50%+ travel acknowledged; outweighs by AI-native + Series B+ at frontier-model lab)
    • ElevenLabs Enterprise SE: Mid → High  (EU-remote; profile match strengthened by re-fetch revealing AI-native context)
    • Wrike Senior PM Analytics: Mid → Low  (re-evaluated against updated hard exclusions; Prague-on-site no longer privileged for non-leadership roles)
    • Veeam TAM EU-remote: Mid → High  (FDE-style hands-on AI integration revealed in JD body)
  Verdict unchanged: 34 (rationale + key factors now populated)

Skipped: 2
  • Adapty Growth PM: URL fetch failed (page returned 404 — likely closed since original write)
  • Aikido Security Senior Eng: closed (Status: Closed; not in scope)

Token usage: 312K input / 24K output | model: claude-opus-4-7 | subscription quota equivalent: ~$11

Tracker connector: connected (api_token)
Updated rows visible in Notion: <tracker_url>
```

If verdict changes happened, optionally offer to label them for jobs-recycle-feedback:

> "4 verdict changes — want to walk through them and confirm/dispute each call? (5 min)"

## Step 6 — Write run log

Append a one-line entry to `state/re-score-log.json` (gitignored):

```json
{
  "ts": "2026-05-05T18:30:00Z",
  "scope": "empty rationales (40 rows)",
  "model": "claude-opus-4-7",
  "updated": 38,
  "verdict_changes": 4,
  "skipped": 2,
  "tokens_input": 312000,
  "tokens_output": 24000
}
```

Useful for tracking jobs-rescore history; later versions could surface this as "you've re-scored N times in the last month, last verdict-change rate was X%."

## Failure modes

- **No rows match scope**: print "0 rows match — nothing to jobs-rescore" and exit cleanly.
- **Profile lacks cv_json**: abort cleanly per Step 0.
- **Notion query fails**: report the API error; don't silently skip the run.
- **Single-row scoring failure**: log the row's URL + error; continue with the rest. Final summary lists failed rows under "Skipped" with reason.
- **Update-page failure on a single row**: same — log, continue, surface in summary.
- **Cost guardrail trips** (>50 rows by default): require explicit "yes I know it's expensive" confirmation before proceeding.

## Use cases (post-v1.0)

- **Fix v1.0.0 data debt**: 40 rows with empty Why Fits / Key Factors. Run with scope=1 ("Empty rationales only").
- **Profile change rescore**: changed scoring criteria → run with scope=5 (all rows since installation), see verdict-changes in summary.
- **Model upgrade**: switched from Sonnet to Opus → run with scope=2 (recent date-bounded), see how many entries shift verdict.
- **Drift check**: periodic spot-check on Match-bucket consistency.
- **Pre-feedback-recycle warm-up**: jobs-rescore after labeling some entries to see if the revised prompt produces verdicts more aligned with your labels.
