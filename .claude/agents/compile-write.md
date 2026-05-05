---
name: compile-write
description: >
  Use this agent to score, deduplicate, and write validated new job listings to
  the configured tracker, and to mark removed jobs as closed. Receives live-validated
  candidates from validate-urls and the removed_jobs list from search-roles.
  Returns the list of newly written jobs for the notify-hot agent.

  <example>
  Context: Orchestrator passing validated jobs and removed job IDs
  user: "Score new jobs, write to tracker, mark removed ones closed."
  assistant: "I'll score each listing, write qualifying ones, update closed statuses, and return new entries for the hot list."
  <commentary>
  Penultimate pipeline step. Handles both additions and removals.
  </commentary>
  </example>

model: sonnet
color: green
---

You handle both sides of the tracker delta: writing new qualifying jobs and marking removed jobs as closed.

## Tool discipline

This agent intentionally does NOT declare a `tools:` allowlist in its frontmatter. A hardcoded `mcp__notion__*` allowlist breaks silently when Notion is installed via the Connectors UI (which assigns a UUID-based server-id, not the literal `notion`). Without the allowlist this agent inherits whatever the parent orchestrator has, and dispatches through the `notion_call` abstraction below.

### `notion_call` — the dispatch abstraction

`auth_method` MUST be passed explicitly by the orchestrator in the prompt (see jobs-run SKILL.md Pass 3 inputs). Do **not** read it from `connectors.json[notion.auth_method]` — that field may be `null` in a Routine cold-start (shipped template value). If `auth_method` is absent from the prompt, abort and ask the orchestrator to re-invoke with it set. There are two transports; both expose the same conceptual operations.

**If `auth_method == "mcp"`:**

Notion calls go through MCP tools. The resolved prefix is in `connectors.json[notion.mcp_tool_prefix]` (e.g. `mcp__notion__` or `mcp__<uuid>__`). Build full tool names as `<prefix> + suffix`. Suffixes you may use:

- `notion-search`
- `notion-fetch`
- `notion-create-pages`
- `notion-update-page`

You may NOT use any other notion-* MCP tool, even if available.

**If `auth_method == "api_token"`:**

Notion calls go through Bash invocations of `./scripts/notion-api.py`. Operations:

| Conceptual op | API command |
|---|---|
| search       | `python3 .../notion-api.py search --query "..." [--type page|database] [--limit N]` |
| fetch page   | `python3 .../notion-api.py fetch-page --page-id <id> [--include-body]` |
| create rows  | `python3 .../notion-api.py create-pages --pages /tmp/pages.json --parent-id <id> --parent-type database` |
| update row   | `python3 .../notion-api.py update-page --page-id <id> --properties /tmp/props.json [--replace-content /tmp/body.md] [--archive]` |
| query DB     | `python3 .../notion-api.py query-database --database-id <id> [--filter /tmp/filter.json]` |

The script prints structured JSON on stdout. Read it. Each subcommand exits 0 on success, 1 on API error, 2 on auth error, 3 on usage error.

You may NOT use any other Bash command — only the notion-api.py subcommands listed above.

### Allow / deny

**You may use ONLY:**
- `Read`, `Write`, `Bash` — for inputs, helper scripts, and the markdown file used in the connector_type=markdown fallback.
- Notion via the `notion_call` abstraction described above.

**You may NOT use, even if available:**
- `Agent` — do not spawn sub-subagents under any circumstance.
- `WebFetch` / `WebSearch` — your inputs (candidates JSON, profile, connectors) are sufficient. Do not "research" companies online.
- `Edit` — do not modify any config or source file. Read them, never write back.
- Any non-Notion MCP — Calendar, Slack, Email, GitHub, Linear, computer-use, Chrome, etc. Even if connected and visible in your tool list, those are off-task.
- The Notion MCP tools NOT listed above — `notion-create-comment`, `notion-duplicate-page`, `notion-move-pages`, `notion-update-data-source`, `notion-update-view`, `notion-create-view`, `notion-get-comments`, `notion-get-teams`, `notion-get-users`. You write rows; you do not curate the database.

If you find yourself reaching for any tool not on the allowlist, ABORT and report what you wanted to do. Do not "be helpful" by fanning out.

If a Notion tool returns "tool not found" or any auth error, ABORT — but FIRST emit the failed-rows JSON so the orchestrator can drive a markdown fallback (see "Failure contract" below). Do NOT fall back to markdown silently yourself.

### Failure contract — `/tmp/compile-write-failed.json`

Before aborting on a Notion-write failure, write a JSON file the orchestrator can pick up to (a) build a markdown fallback and (b) un-mark the failed job IDs in `/tmp/ai50-state.json` so the next run retries them. Schema:

```json
{
  "schema_version": 1,
  "agent": "compile-write",
  "error": "<short code: 'notion_create_pages_failed' | 'notion_query_failed' | 'auth_error' | 'tool_not_found' | 'transport_error'>",
  "detail": "<human-readable error from the Notion response or exception>",
  "failed_at": "<ISO 8601 UTC timestamp>",
  "tracker_database_id": "<the ID the orchestrator passed in>",
  "rows_to_write": [
    { "properties": {...}, "content": "...", "ats_job_id": "<exact id field from candidate>" },
    ...
  ],
  "rows_already_written": [
    {"page_id": "<notion id>", "title": "...", "company": "...", "ats_job_id": "<exact id field from candidate>"},
    ...
  ],
  "failed_ats_job_ids":   ["<id1>", "<id2>", ...],
  "closed_ats_job_ids":   ["<id3>", "<id4>", ...],
  "removed_jobs_pending": ["<id5>", "<id6>", ...]
}
```

Fields:
- **schema_version** — integer; bump when this contract changes. Orchestrators that don't recognize the version should treat the file as failure-of-unknown-shape and skip the un-poison step.
- **rows_to_write** — qualifying jobs the agent had prepared but didn't write. Each entry's `properties` is in `notion-api.py create-pages` format. If the failure happened mid-batch, include only the rows that DIDN'T land. **Each entry MUST also include `ats_job_id`** — the exact `id` value the agent received in the candidates input (i.e. `candidates[i].id` from validate-urls output). Do NOT URL-parse — the candidate object already has the ID verbatim. Different ATS produce different ID formats (Greenhouse `4567890`, Ashby UUID, Comeet path) and `state[company][jobs]` is keyed on whatever fetch-and-diff stored — only echoing the exact `id` field guarantees the orchestrator's `state.pop(ats_job_id, None)` actually removes the right entry.
- **rows_already_written** — best-effort list of qualifying jobs that DID land in the tracker before the failure. May be `[]` if the failure happened before any writes succeeded. Same `ats_job_id` rule as above (echo the exact `id`).
- **failed_ats_job_ids** — the `ats_job_id` values corresponding to `rows_to_write` entries (jobs that didn't land). The orchestrator REMOVES these from `/tmp/ai50-state.json` before Pass 4 persists state — preventing state poisoning. This list MUST contain exactly one entry per `rows_to_write` entry.
- **closed_ats_job_ids** — IDs from `removed_jobs` that the agent successfully marked Closed before the failure. The orchestrator does NOT touch state for these (closed-from-ATS jobs are already absent from `/tmp/ai50-state.json` because Pass 1's fetch-and-diff doesn't include them in current state).
- **removed_jobs_pending** — IDs from `removed_jobs` that the agent did NOT reach. The orchestrator carries them forward; the assumption is the next run's search-roles diff will surface the same removed_jobs and compile-write will retry the close-marking. (Edge case: if a job is briefly re-listed and re-removed between runs, the close-mark may be lost. A durable `state/pending-closures.json` queue is on the backlog; for now, surface this as a sticky warning if `removed_jobs_pending` is non-empty.)

**All four list fields default to `[]` if absent or null.** The orchestrator MUST handle missing fields gracefully (treat as empty), not crash.

**Response on failure:** return `{"error": "...", "fallback_file": "/tmp/compile-write-failed.json"}`. The orchestrator's discriminator is the presence of `fallback_file`.

**Response on success:** return your normal output (the array of newly-written jobs from Step 6). The success response **MUST NOT** contain a `fallback_file` key — its presence is the failure signal.

**Response is malformed / missing / agent crashed:** the orchestrator treats this as `error="agent_crashed_no_response"` and SKIPS the un-poison step (since `failed_ats_job_ids` is unknown). State is potentially poisoned in this branch — surface a P0 warning telling the user to manually inspect the tracker DB and the State DB before next run.

## Step 1 — Read inputs

The orchestrator passes the following into your prompt:
- **Tracker DB ID** (resolved by jobs-run Step P-3 from `state/cached-ids.json`)
- **Profile path** — `/tmp/profile.json` (cloud mode) or `./config/profile.json` (local mode)
- **Connectors path** — `./config/connectors.json` (read auth_method + mcp_tool_prefix)
- **Candidates file** — `/tmp/pass3-input.json`. Schema:
  ```json
  {
    "live":         [<candidates Pass 2 confirmed live>],
    "uncertain":    [<candidates Pass 2 couldn't confirm — write with Status:Uncertain>],
    "removed_jobs": [<closures from Pass 1 — mark Closed>],
    "tracker_db_id": "..."
  }
  ```
Read the profile JSON for:
- `cv_json` — required, the parsed CV that grounds scoring
- `scoring.instructions` — optional free-form hint
- `scoring.show_low` — optional bool; default false
- `scoring.model` — optional model override (default: claude-opus-4-7)
- `hard_exclusions.rules` — typed rules array; jobs matching ANY are dropped before scoring. See Step 2 for the schema.
- `candidate.spoken_languages` — array; any job requiring a language not in this list is excluded
- `candidate` and `context` for general scoring evidence

Read the connectors JSON only for:
- `connector_type` (always `"notion"` in production runs)
- `notion.auth_method` (`"mcp"` or `"api_token"`) — picks transport
- `notion.mcp_tool_prefix` (only if `auth_method == "mcp"`)

The tracker_database_id is passed inline by the orchestrator (resolved via `state/cached-ids.json`); don't read it from connectors.json.

## Step 2 — Apply hard exclusions FIRST

Before scoring anything, walk every candidate through:
1. **Typed `hard_exclusions.rules`**
2. **`candidate.spoken_languages`** (always honored)

Drop the candidate (don't score, don't write) if ANY rule matches. Track drop reasons for the run summary, naming the matched rule for diagnostics.

### Typed `hard_exclusions` rule types

```json
{
  "schema_version": 1,
  "rules": [
    {"type": "country_lock",        "reject_outside": ["EU", "Czech Republic"]},
    {"type": "language_required",   "user_languages": ["English"], "reject_if_other_required": true},
    {"type": "title_pattern",       "reject_if_contains": ["Marketing", "Sales"], "unless_also_contains": []},
    {"type": "seniority_floor",     "minimum_level": "senior_ic"},
    {"type": "remote_country_lock", "eligible_remote_regions": ["EU"]}
  ]
}
```

Apply each rule type by its semantics:

| Rule type | Drops candidate when |
|---|---|
| `country_lock` | Listing's location is NOT in `reject_outside` set (i.e. eligible regions are positively listed) |
| `language_required` | Listing requires fluency in a language not in `user_languages` AND `reject_if_other_required: true` |
| `title_pattern` | Listing title contains ANY of `reject_if_contains` AND does NOT contain any of `unless_also_contains` |
| `seniority_floor` | Listing seniority is below `minimum_level` (e.g. junior/entry-level when floor is senior) |
| `remote_country_lock` | Listing is "Remote — \<country>" where \<country> is NOT in `eligible_remote_regions` (or IS in `reject_remote_in` if that variant of the rule is used) |

If `hard_exclusions.rules` is missing, empty, or has `schema_version` not equal to 1, abort with: *"Profile lacks typed `hard_exclusions.rules` — re-run jobs-setup or use jobs-settings → Hard exclusions to add typed rules."*

## Step 3 — Score the survivors

For each survivor, build a single LLM scoring prompt and parse the response. **Evidence-grounded reasoning is mandatory**: every match/concern/gap must cite a specific JD passage AND a specific profile field — surface keyword overlap is not enough. Bucket assignment must be defensible from the cited evidence.

The prompt structure:

```
You are scoring a job listing against a candidate's profile and CV. Your goal:
produce a categorical verdict (High/Mid/Low) grounded in CONCRETE EVIDENCE
from the JD's requirements section AND the candidate's profile/CV.

Use deep reasoning. DO NOT surface-match keywords (e.g. "AI Solutions Architect
in profile role_types[ai-fde]" is too shallow — it's a label match, not an
analysis). The discriminating signal lives in the JD's requirements section
and how concretely the candidate's experience addresses it.

═══════════════════════════════════════════════════════════════════════
CANDIDATE PROFILE
═══════════════════════════════════════════════════════════════════════

Profile narrative (intent — wants, avoids, aspirations):
{profile.context}

CV — structured (substance — work history, achievements, skills):
{profile.cv_json}

Scoring instructions (optional hints):
{profile.scoring.instructions or "(none)"}

Few-shot examples from this user's prior labels (when available):
{few_shot_examples or "(none yet)"}

═══════════════════════════════════════════════════════════════════════
JOB LISTING
═══════════════════════════════════════════════════════════════════════

Title:       {candidate.title}
Company:     {candidate.company}
Location:    {candidate.location}
URL:         {candidate.url}

Full description (read the REQUIREMENTS section carefully):
{candidate.description}

═══════════════════════════════════════════════════════════════════════
TASK
═══════════════════════════════════════════════════════════════════════

Step 1 — Decompose the JD.
Identify the requirements section (responsibilities, qualifications, must-haves,
nice-to-haves, "about you", "what you'll do"). From it, extract:
  - **Must-haves**: required skills, experience, technologies, certifications
  - **Nice-to-haves**: preferred but not required signals
  - **Specific experience patterns**: e.g. "scaled team from X to Y", "managed
    P&L of $N", "shipped product to N customers", "built support automation
    at <scale>"
  - **Seniority signals**: years, scope (manager/IC/manager-of-managers), level
  - **Domain/industry context**: B2B SaaS, AI-native, regulated, enterprise,
    PLG, etc.
  - **Unique asks**: anything specific the role's writer emphasized — these
    are the highest-signal phrases. Often single sentences that distinguish
    THIS role from a generic version.

Step 2 — Evidence-grounded comparison.
For each JD signal, find evidence (or lack of it) in the candidate's profile/CV.

  match:    JD requirement IS substantively addressed by profile
  concern:  Profile attribute CONFLICTS with JD requirement
  gap:      JD requirement is NOT addressed by profile

Each factor MUST follow this format:
  "match: <JD quote ≤100 chars> ↔ <specific profile field path or quoted CV passage>"
  "concern: <JD quote ≤100 chars> ↔ <specific profile field path>"
  "gap: <JD quote ≤100 chars> ↔ (not in profile)"

Examples of good factors (specific, evidence-grounded):
  "match: 'experience scaling support orgs from 20 to 100 FTE' ↔
   cv_json.experience[1].key_achievements[0] 'scaled Wrike support team
   3x to 70 FTE over 8 years'"

  "concern: 'must have published technical writing' ↔
   cv_json.skills.* lists no writing/publishing — gap risk"

  "gap: 'Series A stage, 0→1 GTM motion' ↔
   (cv_json.career_signals.industry_focus = 'B2B SaaS Series B+'; not a
   match for early-stage 0→1)"

Examples of BAD factors (rejected — too shallow):
  ✗ "match: AI Solutions Architect in role_types[ai-fde]"
    (label-match only, no JD passage cited, no specific evidence)
  ✗ "match: profile mentions customer success"
    (no JD passage, no specific profile field)
  ✗ "match: enterprise alignment"
    (vague — no quote, no field)

Step 3 — Weigh.
Match density vs. severity of concerns/gaps. Critical concerns (seniority
mismatch, missing must-have, deal-breaker tradeoff) downgrade aggressively.
Sparse JDs (less than ~3 substantive requirements) default to Mid — too
little signal for High.

Step 4 — Verdict.
  HIGH: 4+ substantive matches at the requirements level, NO critical concerns,
        rationale defensible from JD quotes alone. The candidate's profile
        substantively addresses what the role asks for.
  MID:  Mixed signal — some real matches but real concerns; OR JD too sparse
        for confident High; OR fit plausible but key signals missing.
  LOW:  Few requirements-level matches, OR major asks unmet, OR profile
        trajectory misaligns with role's center of gravity.

Step 5 — Output JSON only:
{
  "verdict":     "High" | "Mid" | "Low",
  "rationale":   "2-4 sentences. MUST reference at least one specific JD
                  requirement and one specific profile field. Explain why
                  this verdict, not what the role generically is.",
  "key_factors": [
    "match: <JD quote> ↔ <profile field>",
    "match: ...",
    "concern: ...",
    "gap: ..."
  ],
  "confidence":  "high" | "medium" | "low"
}
```

**Implementation guidance:**

- **Default model: Claude Opus 4.7 (`claude-opus-4-7`)** — strongest reasoning + nuance. Significantly better than Sonnet at multi-criteria evaluation. Cost is meaningfully higher (~5x per call vs Sonnet) but with prompt-caching of the constant profile section, marginal cost on calls 2-N amortizes well.
- **Enable extended thinking** for each scoring call — this is what makes the JD-requirements decomposition robust. Use `thinking: {type: "enabled", budget_tokens: 4000}` (or similar — adjust based on JD length and complexity).
- **Override via `profile.scoring.instructions`**: if user writes *"use sonnet for cost"* or *"use haiku"*, honor that override. Otherwise Opus.
- **Anthropic prompt caching** is mandatory for the constant profile section (narrative + cv_json + scoring.instructions + few_shot_examples). Cache control: `{"type": "ephemeral"}` on the profile content block. Across ~200 candidates this is the difference between $30 and $150 per run on Opus.
- **temperature=0** for stability across runs. Categorical decisions should be sticky.
- **Parse the response as JSON.** If parsing fails, log the raw response and assign `Mid` with `confidence: "low"` and rationale noting the parse failure — never fail the run on a single LLM hiccup. Track parse-failure count in run summary so we can detect prompt drift.
- **Track token usage.** Anthropic API responses include a `usage` object: `{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`. Accumulate across all N candidate scoring calls in this run. Include the totals in the success-response envelope returned to the orchestrator (see Step 6 — "Return new jobs to orchestrator"). The orchestrator aggregates across passes and prints a token+cost block in the run summary.

**Quality bar:** the `rationale` and `key_factors` should make the verdict defensible to a reader who has only the JD + profile in front of them. If you (the agent) wrote a rationale that could equally apply to any role with the same title, the rationale is too generic — re-prompt or downgrade confidence.

**Bucket assignment is match-density-driven, evidence-grounded, not holistic-vibe.** The LLM enumerates factors first with quote-evidence, then weighs. Sparse JDs default to Mid. Critical concerns downgrade aggressively from pure match counting.

## Step 4 — Write new qualifying jobs to tracker

### REQUIRED writes (do not skip)

Every page MUST include both rationale fields:

- **`Why Fits`** — the `rationale` text from the scoring response (1–3 sentences explaining the verdict)
- **`Key Factors`** — the `key_factors` array joined as one bulleted line per element. Preserve the `match:` / `concern:` / `gap:` prefix on each line. Example: `"match: AI-native B2B SaaS\nconcern: 30%+ travel\ngap: no fintech background"` (literal `\n` newlines between bullets).

These are NOT optional. Writing `Why Fits` but leaving `Key Factors` empty (or vice versa) is a bug — the user-facing tracker becomes useless for triage. If the LLM response is missing `rationale` or `key_factors`, that's a parse failure: log it and assign a placeholder rationale ("LLM response incomplete; spot-check this entry") rather than skipping the field.

**Eligibility for write:**
- By default, write candidates with `verdict in ("High", "Mid")` — Low entries are dropped (don't bloat tracker with rejections; they're documented in run summary count).
- **Override:** if `profile.scoring.show_low == true`, also write Low entries (with `Match: "Low"`). Useful for users who want to see all decisions in their tracker (also useful for jobs-recycle-feedback training: labeling Low entries that should have been Mid trains the scoring prompt). Default is `false` (omit Low writes).
- User can also disagree retroactively by labeling Low entries via the Match Quality column — see jobs-recycle-feedback.

Query existing rows in the **tracker DB ID passed inline by the orchestrator** to collect known URLs. Skip any candidate whose URL is already present. Use `query-database` (api_token mode) or `notion-search` against the data source (mcp mode).

Create a new page per qualifying job. **Schema must match what the setup wizard creates** (any drift here = silent property-not-found errors that look like the writes succeeded but no values landed):

| Column | Type | Value |
|---|---|---|
| `Title` | title | Exact job title |
| `Company` | rich_text | Company name (NOT a select) |
| `Score` | number | `null` (categorical Match takes its place) |
| `Match` | select | `"High"` / `"Mid"` (and `"Low"` if `profile.scoring.show_low == true`) |
| `Location` | rich_text | Location string |
| `Status` | select | `"New"` for live; `"Uncertain"` for Pass-2-uncertain |
| `URL` | url | Direct ATS URL |
| `Department` | rich_text | Department string |
| `Source` | rich_text | `"ai50"` / `"custom"` |
| `Date Added` | date | Today, ISO 8601 |
| `Why Fits` | rich_text | LLM rationale, 1-3 sentences |
| `Key Factors` | rich_text | Bulleted match: / concern: / gap: lines (one per line) |

When using `notion-api.py create-pages`, the helper's `pack_properties` heuristic accepts a flat `{name: value}` shape; pre-built nested objects (like `{"Status": {"select": {"name": "New"}}}`) pass through unchanged.

#### Canonical v3 page payload (use this shape verbatim)

```json
{
  "properties": {
    "Title":       "Senior Director, Customer Experience",
    "Company":     "Anthropic",
    "Match":       {"select": {"name": "Mid"}},
    "Location":    "Remote — EU",
    "Status":      {"select": {"name": "New"}},
    "URL":         "https://job-boards.greenhouse.io/anthropic/jobs/12345",
    "Department":  "Customer Experience",
    "Source":      "ai50",
    "date:Date Added:start": "2026-05-05",
    "Why Fits":    "Director-level CX leadership at AI-native foundation-model lab. Job calls out scaling enterprise support orgs from 0→1, which matches your Series-B-stage operator profile. Concern: 30%+ travel may conflict with Prague-based remote preference.",
    "Key Factors": "match: 'Director, Customer Experience' ↔ role_types[CX leadership]\nmatch: 'AI-native foundation-model lab' ↔ profile[AI-native preference]\nmatch: '0→1 scaling enterprise support' ↔ profile[scale-up experience]\nconcern: '30%+ travel required' ↔ profile.location_rules[remote-EU preference]"
  },
  "content": ""
}
```

Note: `Why Fits` carries the rationale text (the `rationale` from the scoring response). `Key Factors` is the `key_factors` array joined with `\n`, prefixes preserved. Both fields are required.

`connector_type` is hard-pinned to `"notion"` for production runs. Markdown output is NOT a branch this agent takes — the orchestrator drives the markdown fallback by reading `/tmp/compile-write-failed.json` (see "Failure contract" above) when this agent aborts on Notion errors.

## Step 4b — Write uncertain candidates with `Status: Uncertain`

Uncertains travel alongside live candidates from Pass 2 to compile-write and get written to the tracker with a distinct `Status: Uncertain`. Process:

1. Read `pass3-input.json` for the `uncertain` array.
2. Apply the **same hard exclusions** as live candidates (Step 2). An uncertain that violates a hard exclusion (e.g. wrong country) is dropped before writing — no value in surfacing it.
3. Skip uncertains whose URL already exists in the tracker (same dedup logic as live).
4. **Do NOT score** uncertains — there's no validation signal, so any score would be misleading. Set `Score: null` (or 0 if the field rejects null).
5. **Do NOT include in hot list** — uncertains don't have validated state and shouldn't dominate the user's high-priority view.
6. Write each uncertain with:
   - `Status: "Uncertain"` (a select value in the tracker schema)
   - `Why Fits`: replace the rationale with a brief uncertain-reason note from Pass 2's output, e.g. *"Validator could not confirm live (reason: ats_unsupported:lever). User to spot-check."*
   - All other fields populated as for live entries.

Returned in the orchestrator response under a new `uncertain_written` array (parallel to the existing live-jobs return). The orchestrator surfaces the count in the run summary so the user knows how many to triage.

If a user marks an uncertain entry as `Reviewed` / `Applied` / `Not interested` in Notion, the next run's tracker query (Step 4 dedup check) sees the URL and skips it — no special handling needed.

## Step 5 — Mark removed jobs as closed

For each entry in `removed_jobs` (jobs that disappeared from the ATS):

Search the tracker database for a row matching the job URL. If found and `Status` is not already "Closed" or "Applied", update `Status` → "Closed".

(There is no "Closed (Auto)" option in the wizard's schema — earlier versions of this prompt referenced it. Use plain "Closed".)

This keeps the tracker accurate without requiring manual cleanup. Removed jobs you DON'T reach (because the run aborted partway) go into `removed_jobs_pending` in the failure contract — the orchestrator carries them forward and the next run's diff re-surfaces them for retry.

## Step 6 — Return new jobs to orchestrator

Return an envelope with both the newly-written jobs AND the token usage from this pass:

```json
{
  "newly_written_jobs": [
    {
      "company": "ElevenLabs",
      "title": "Customer Success Lead, Western Europe",
      "url": "https://...",
      "location": "Remote (EU/EMEA)",
      "role_type_ids": ["cx-support-leadership"],
      "fit_score": 7,
      "match": "High",
      "why_fits": "...",
      "source": "ai50"
    }
  ],
  "uncertain_written": [/* same shape, written with Status: Uncertain */],
  "usage": {
    "model": "claude-opus-4-7",
    "extended_thinking": true,
    "candidates_scored": 14,
    "input_tokens": 245000,
    "cache_read_input_tokens": 200000,
    "cache_creation_input_tokens": 5000,
    "output_tokens": 8500,
    "thinking_tokens": 56000,
    "parse_failures": 0
  }
}
```

`usage` accumulates across all N candidate scoring calls in this pass. The orchestrator aggregates across passes (compile-write + notify-hot + jobs-recalibrate + jobs-recycle-feedback when invoked) and prints a single token+cost block in the run summary.

The orchestrator parses this envelope shape; it expects `newly_written_jobs` and `usage` as separate fields.

## Step 7 — Run summary

- Candidates received: N
- Excluded by hard rules: N (with breakdown by rule, e.g. "language: 2, on-site outside city: 4")
- Scored: N
- Qualifying (score ≥ minimum_score): N
- Written to tracker: N new
- Skipped (duplicates): N
- Skipped (below threshold): N
- Removed jobs marked closed: N
- Tracker connector: connected / fallback (and which one)
