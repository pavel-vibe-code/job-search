---
name: recalibrate-scoring
description: Tune the scoring rubric in profile.json based on what you saw in the last run. Show the top + bottom of the hot list, ask the user what's off, propose concrete profile mutations, write the updated profile back to Notion (cloud mode) or to ./config/profile.json (local mode). Manual-dialog version of the v3.0 learning loop. Invoke when the user says "recalibrate the scoring", "adjust the scoring rubric", "the hot list looks off", or similar.
version: 2.5.2
---

## What this skill does

After a few runs the user has empirical evidence of whether the scoring rubric matches their intent. This skill provides a structured dialogue that:

1. Pulls the most recent tracker entries grouped by score band (hot, qualifying, just-below-threshold).
2. Surfaces them in a critique-friendly view (top vs. bottom contrast).
3. Asks the user to point out specific entries that scored too high or too low, in plain English.
4. Translates the user's feedback into concrete profile mutations (criterion weight changes, new exclusion rules, threshold adjustments).
5. Shows predicted score deltas so the user can see what each change would do before approving.
6. Writes the updated profile back to its source of truth.

This is the manual / single-shot version. The v3.0 release adds an automated learning loop (`feedback-recycle`) that reads `Match Quality` labels users add in Notion and recycles them into profile updates without requiring a dialogue every time.

## When NOT to invoke

- Right after a fresh setup (no tracker data yet — nothing to recalibrate against).
- If the user says "the search isn't running" — that's a configuration / wiring issue, not a scoring issue. Point them at run-job-search troubleshooting instead.
- If the user wants to add a new role type or rewrite their profile from scratch — that's the setup wizard's job. Have them run "set up the plugin" again.

## Step 1 — Read context

Inputs you need:

- **Profile path** — `/tmp/profile.json` (cloud mode) or `./config/profile.json` (local mode). Determined by `state/.setup_complete`'s `deployment_mode` field.
- **Tracker DB ID** — read from `./state/cached-ids.json[tracker_database_id]`.
- **Recent tracker entries** — query the tracker DB filtered to entries from the last 14 days (or last N entries if date-filtering is awkward — pick a sensible window). Use `notion-api.py query-database --filter` (api_token mode) or `notion-search` (mcp mode).

Group the entries by band:

| Band | Criterion |
|---|---|
| Hot | Score ≥ `profile.scoring.hot_score_threshold` |
| Qualifying — high | (hot_score_threshold - 1) to hot_score_threshold (inclusive of low end) |
| Qualifying — low | `profile.scoring.minimum_score` to (hot_score_threshold - 2) |
| Just below threshold | If you can find recent un-written entries (would require Pass 3 logs / fallback files) — optional, skip if unavailable |

You don't need to score anything yourself; just read what's already in the tracker.

## Step 2 — Surface critique-friendly view

Show the user:

```
━━━ Scoring snapshot — last {N} days ━━━

Top of hot list ({M} entries at score ≥ {hot_threshold}):
  {score} — {company}: {title} [{location}]
  {score} — {company}: {title} [{location}]
  {score} — {company}: {title} [{location}]
  ... show up to 8

Bottom of hot list ({M2} entries just at threshold):
  {score} — {company}: {title} [{location}]
  ... show up to 5 of the lowest-scoring hot entries

Just below threshold (qualifying but not hot):
  {score} — {company}: {title} [{location}]
  ... show 3-5

Where do you think the rubric is off? Anything that:
  - scored TOO HIGH (shouldn't be in hot, or shouldn't be in tracker at all)?
  - scored TOO LOW (should have been higher, or should have been hot)?
  - feels right but you have a tweak in mind?

Tell me in plain English. Examples:
  - "Mistral FDE shouldn't be hot — too engineering-IC for me"
  - "Anything scoring 5 should drop to 3 unless it's in Czechia"
  - "I want a +1 bonus for any role with 'CX' or 'support' in the title"
```

Wait for user response. Don't pre-fill suggestions — they bias the answer.

## Step 3 — Translate feedback into mutations

For each piece of user feedback, identify the scoring lever it points at and propose a specific mutation. Common patterns:

| User feedback | Likely mutation |
|---|---|
| "X scored too high" | Lower the criterion weight that gave X points; OR add an exclusion rule that drops X's pattern; OR raise minimum_score |
| "X should have been hot" | Raise the criterion weight that DIDN'T give X enough; OR lower hot_score_threshold; OR add a bonus for X's discriminating feature |
| "Anything matching pattern P should drop" | Add an exclusion rule (typed `title_pattern` if surface-level; free-text `exclusion_rules` if nuanced) |
| "Pattern P should always score high" | Add a bonus criterion for P |
| "Location should matter more" | Raise location-fit weight (or move location from criterion to hard exclusion if user implies binary intent — see Step 4) |

For each proposed mutation, calculate the **predicted score delta** for the entries that surfaced the issue. E.g. *"Raising location-fit from weight=2 to weight=3 would have moved Mistral FDE from 5 to 7 and Cohere TPM from 4 to 6."*

If the user's feedback implies a *binary* requirement (their words: "must be EU", "never if Marketing", "only if Director-level"), promote the rule from `scoring.criteria` to `hard_exclusions.rules`. Document the demotion explicitly: *"You said 'must be EU' — that's binary, not gradient. Moving from scoring criterion (weight 2) to hard exclusion (drops before scoring). The tracker entries that would no longer pass: {list 3-5 examples}. Confirm?"*

This is where the wizard often gets it wrong (translates binary intent as gradient criterion); the recalibrate skill is the catch-it-later mechanism.

## Step 4 — Show diff and get approval

Present the proposed changes as a unified diff against current `profile.json`:

```json
{
  "scoring": {
    "criteria": {
      "location-fit": {
-       "weight": 2,
+       "weight": 3,
        "priority": "high",
        ...
      }
    }
  },
  "hard_exclusions": {
    "rules": [
+     {"type": "title_pattern", "reject_if_contains": ["AI Engineer", "ML Engineer"], "unless_also_contains": ["Forward Deployed", "Customer"]},
      ...existing
    ]
  }
}
```

Then ask: *"Apply these changes? (yes / let me adjust / cancel)"*

If "let me adjust" — re-enter Step 3 with the user's refinement. If "cancel" — exit without writing.

## Step 5 — Write updated profile

On approval:

**Cloud mode** (deployment_mode == "cloud"):
- Use `notion-api.py update-page` (api_token mode) or `notion-update-page` (mcp mode) to replace the JSON code block in the AI 50 Profile page.
- Verify by reading the page back and confirming the diff applied.

**Local mode**:
- Write to `./config/profile.json`.
- Print: *"Profile updated. Re-run `run the job search` to apply."*

In both modes, append a brief one-line entry to a session log (could be `state/recalibrate-log.json` or just printed) noting what changed and when:

```
{"timestamp": "2026-05-03", "changes_applied": [
  "scoring.criteria.location-fit.weight: 2 → 3",
  "hard_exclusions.rules: added title_pattern (reject AI Engineer / ML Engineer unless customer-facing)"
]}
```

This builds a paper trail for future recalibrations — useful when the user is iterating over multiple sessions and wants to undo a change.

## Step 6 — Done

Print:

```
━━━ Recalibration complete ━━━

Changes applied to profile:
  {bullet list of changes}

Token usage:  {input_tokens} input ({cache_read} cached), {output} output
              model: {model_id}, est. cost: ${X.XX}

Next run will use the updated rubric. Type "run the job search"
to test, or come back to recalibrate again after seeing the
updated tracker.
```

Track total token usage across all LLM calls made in this skill (typically: feedback summarization + mutation proposal). Include in the final summary printed to the user. Recalibrate-scoring is always invoked manually (not from the orchestrator's Pass 6), so the usage is reported inline rather than returned in an envelope.

Don't auto-trigger the search — the user decides.

---

## Bridge to v3.0

This skill is the **manual** version of the learning loop. v3.0 introduces:

- `Match Quality` column in the tracker DB (Good / OK / Bad — same vocabulary as LLM verdict)
- `Feedback Comment` column for free-form rationale
- A `feedback-recycle` skill that runs at end of each fire (or on demand) and:
  - Reads recent labels
  - Synthesizes anti-patterns + few-shot examples
  - Auto-updates the profile (with user review/confirm)

Until v3.0 lands, this `recalibrate-scoring` skill is how you tune the rubric — invoke it as often as needed. The dialogue this skill produces is also useful raw material for v3.0's auto-recycler when it ships.
