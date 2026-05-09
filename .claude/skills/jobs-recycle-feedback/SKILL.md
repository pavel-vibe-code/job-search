---
name: jobs-recycle-feedback
description: Reads recent user feedback labels in the Notion tracker (Match Quality + Feedback Comment columns), synthesizes anti-patterns and few-shot examples from disagreements between LLM verdict (Match) and user verdict (Match Quality), and updates the profile so future Pass 3 LLM scoring runs incorporate the learning. Closes the learning loop introduced in v1.0.0. Invoke explicitly via "recycle feedback" or "update profile from labels", or as Pass 6 of the orchestrator (gated to fire at most once per 7 days).
---

## What this skill does

The v3.0-rc1 architecture has the LLM scoring each candidate categorically (High/Mid/Low) with rationale + key_factors. The user labels tracker entries in Notion with their own verdict (`Match Quality`: Great/OK/Bad) plus optional `Feedback Comment`. This skill reads those labels, prioritizes **disagreements** between LLM and user, and turns them into:

1. **Anti-patterns** — text descriptions of what the user systematically rejects, appended to the profile so future scoring runs honor them.
2. **Few-shot examples** — concrete `{job_summary, llm_verdict, user_label, comment}` quads stored as a separate Notion page (or local JSON file in local mode), included in the Pass 3 LLM scoring prompt for generalization.

Both are how the system learns without requiring the user to manually re-edit profile rules every time.

## When NOT to invoke

- Tracker has fewer than ~5 labeled entries — too sparse to extract patterns. Wait for more data.
- No disagreements (LLM verdict matches user labels everywhere) — nothing to learn from. Skill should still run but produce a "no changes needed" output.
- Right after a profile change — give the new profile at least one full run cycle before recycling, so feedback reflects the current rubric.

## Step 1 — Read context

Inputs:

- **Profile path** — `/tmp/profile.json` (cloud) or `./config/profile.json` (local). Determined by `state/.setup_complete[deployment_mode]`.
- **Tracker DB ID** — **DO NOT** trust `state/cached-ids.json` blindly. The cache file is per-installation (your laptop's cache and the cloud Routine container's cache drift independently), and stale IDs lead to querying the wrong DB. **Run `notion-api.py discover` first** to refresh the cache, then read the freshly-resolved ID:

  ```bash
  python3 ./scripts/notion-api.py discover \
    --config     ./config/connectors.json \
    --cache-file ./state/cached-ids.json
  ```

  This ensures cached-ids reflects the current state of the Notion workspace. Skipping this step risks "stale tracker ID, missing labels" errors when DBs have been recreated or migrated.

- **Few-shot examples store path:**
  - Local mode: `./state/few_shot_examples.json` (gitignored).
  - Cloud mode: a dedicated Notion page (created at first run; ID stored in `cached-ids.json[few_shot_examples_page_id]`).

Query the tracker for entries where:
- `Match Quality` is set (i.e. user labeled it)
- `Recycled` is unchecked (i.e. we haven't processed this label yet)

Use `notion-api.py query-database --filter` (api_token mode) or `notion-search` (mcp mode). The query returns a `properties_summary` for each row that includes all relevant property types — `rich_text` (Feedback Comment, Key Factors, Why Fits), `checkbox` (Recycled), `select` (Match, Match Quality, Status).

If zero results: print *"No new feedback to recycle. Run when you've labeled some entries in the tracker."* and exit.

## Step 2 — Categorize

For each labeled entry, compare `Match` (LLM) vs `Match Quality` (user). Three buckets:

| Match (LLM) | Match Quality (user) | Category | Signal |
|---|---|---|---|
| High | Great | Agreement (positive) | Confirms LLM is calibrated correctly |
| High | OK | Mild disagreement | LLM slightly over-confident |
| **High** | **Bad** | **Strong disagreement** | LLM said Hot but user rejected — highest-leverage training signal |
| Mid | OK | Agreement | Confirms middle judgment |
| Mid | Great | Disagreement | LLM under-rated; user wants more like this |
| Mid | Bad | Mild disagreement | LLM should have been more skeptical |
| **Low** | **Great** | **Strong disagreement** | LLM rejected something user wants — high-leverage |
| Low | OK | Mild disagreement | LLM was too strict |
| Low | Bad | Agreement | (Note: Low entries normally aren't in the tracker — but if user wrote one in manually they can still label it) |

**Strong disagreements get priority.** They're the most informative for profile updates.

## Step 3 — Synthesize anti-patterns from rejections

Look at entries where `Match Quality: Bad` (regardless of LLM verdict). The user explicitly doesn't want these.

For each, examine:
- Job title pattern
- Company / industry
- Location
- LLM's `Key Factors` (which factors did the LLM cite as matches that the user disagreed with?)
- User's `Feedback Comment` (the rationale)

Cluster the rejections — if 3+ entries share a pattern (e.g. all Marketing-leadership roles, all US-locked, all under 5 years experience requirement), extract that pattern as an anti-pattern.

Format anti-patterns as concise rejection rules ready to append to the profile:

```
- Marketing-leadership roles (e.g. "VP Marketing", "Director Brand", "Head of Growth") — user
  has rejected 4 such entries. Rejection rationale: "outside my function".

- Roles requiring US authorization — user has rejected 3 entries. Rejection rationale:
  "EU-only candidate".
```

Show these candidate anti-patterns to the user with predicted impact: *"Adding these would have dropped 7 of 14 Bad-labeled entries before scoring on the previous run. Apply?"*

If the user confirms, append the anti-patterns to:
- `profile.context` (cloud mode: edit Notion profile page; local mode: edit `config/profile.json`'s `context` field)
- The anti-patterns block can be wrapped in a `[learned anti-patterns — auto-recycled <date>]` marker for transparency.

## Step 4 — Curate few-shot examples

Pick 3-5 representative entries from this batch as few-shot examples for the next Pass 3 LLM scoring. Selection criteria:

- Strong disagreements (LLM=High + user=Bad, OR LLM=Low + user=Great)
- Distinct enough to exemplify a pattern
- Have meaningful `Feedback Comment` from user

Format each example as:

```json
{
  "title":          "Senior Applied AI Engineer",
  "company":        "ExampleCo",
  "location":       "Remote, US",
  "llm_verdict":    "High",
  "user_label":     "Bad",
  "user_comment":   "Title is 'AI Engineer' — primary identity is engineering, not customer-facing. I'm not a developer.",
  "key_factors_at_scoring": ["match: AI-native company", "match: senior IC level", "concern: requires Python development"],
  "lesson":         "When title's primary identity is Engineer/Developer/Architect, downgrade even if other factors match."
}
```

Append these to the few-shot store. Cap the store at ~10 examples (oldest evicted first when adding new) to keep Pass 3's prompt size bounded.

In cloud mode: write the few-shot store as a JSON code block in the dedicated Notion page (auto-create the page on first run if `cached-ids[few_shot_examples_page_id]` is unset).

## Step 5 — Mark recycled, update profile, record run timestamp

For every tracker entry processed:
- Set `Recycled: true` (the v3.0.0 schema's checkbox column)

This prevents double-counting in future recycle runs.

**Also write `state/last_recycle.json`** with the run timestamp + counts:

```json
{
  "timestamp":           "2026-05-03T14:32:00Z",
  "entries_processed":   N,
  "anti_patterns_added": M,
  "few_shot_added":      K
}
```

This file gates the orchestrator's optional Pass 6 auto-trigger (jobs-run SKILL.md): Pass 6 fires only if last_recycle is missing or > 7 days old. Without this stamp, every Routine fire would invoke recycle even when there's nothing new — wasteful.

File is gitignored (per-installation; `state/` directory is already in .gitignore).

Profile update summary printed:

```
━━━ Feedback recycle complete ━━━

Processed labels:    {N entries}
Strong disagreements: {N}
Anti-patterns added:  {N} (with user approval)
Few-shot examples:    {N} (added to store, total now {M}/10)

Next Pass 3 run will use the updated profile + few-shot store
automatically. Watch the next tracker fire — patterns the user
rejected should appear less; patterns the user wanted should
appear more or surface higher.
```

## Step 6 — Token usage + summary (v3.0.5+)

Track total token usage across all LLM calls made in this skill's run (typically: anti-pattern synthesis + few-shot example curation, possibly small profile-rewrite calls). Include in the final summary printed to the orchestrator (or to the user, if invoked manually):

```
━━━ Feedback recycle complete ━━━
Processed labels:    {N entries}
Strong disagreements: {N}
Anti-patterns added:  {N}
Few-shot examples:    {N} (total now {M}/10)

Token usage:  {input_tokens} input ({cache_read} cached), {output} output
              model: {model_id}, est. cost: ${X.XX}
```

If invoked from jobs-run Pass 6, return the usage in an envelope so the orchestrator's aggregate token block reflects jobs-recycle-feedback's contribution:

```json
{
  "entries_processed": N,
  "anti_patterns_added": M,
  "few_shot_added": K,
  "usage": {
    "model": "claude-sonnet-4-6",
    "input_tokens": 8000,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "output_tokens": 1500
  }
}
```

If invoked manually, suggest *"Run jobs-recycle-feedback again after your next routine fire to compound the learning. The more labels you provide, the better the signal."*

If invoked automatically (cloud Routine Pass 6), just log the recycle and continue.

---

## Auto-trigger integration (v3.0.0+)

When the orchestrator's jobs-run skill finishes Pass 5, it can optionally invoke this skill to recycle any new labels before the next fire. Implementation: add `Pass 6 — jobs-recycle-feedback (optional)` to the orchestrator. Gate it on:
- `state/.setup_complete[deployment_mode] == "cloud"` (cloud-only — local users invoke manually when ready)
- `state/last_recycle.json` shows ≥7 days since last cycle (don't recycle every fire — gives user time to label new entries)

Manual invocation always works regardless of the auto-trigger gate.

---

## Bridge to future versions

- v3.1: extracted_keywords from CV gets updated based on which keywords appeared in user-labeled-Greats. The CV-extracted-keywords list becomes self-tuning.
- v3.2: confidence calibration — if LLM consistently emits `confidence: high` on entries the user labels Bad, prompt-tune to reduce confidence on similar patterns.
- post-v1.0 backlog: cross-user shared anti-pattern store (opt-in, anonymous) — community-level learning of what doesn't work.

These are speculative future work; v3.0.0 ships only the within-single-user jobs-recycle-feedback described in Steps 1-5.
