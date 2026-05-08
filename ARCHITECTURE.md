# AI 50 Job Search — Architecture

A Claude Code plugin that runs a weekly job search across the Forbes AI 50 baseline plus any custom companies the user adds on top, scores results against a CV-grounded categorical rubric, and writes them to the user's Notion workspace. Designed to run unattended as a Cloud Routine.

This document describes how the plugin is built and why. For installation and Routine setup see [INSTALL.md](INSTALL.md). For the changelog see [CHANGELOG.md](CHANGELOG.md).

---

## 1. System overview

```
┌────────────────────────────────────────────────────────────────────┐
│ Plugin source (Git, generic, shareable)                             │
│   .claude/skills/     jobs (menu), jobs-setup, jobs-run,            │
│                       jobs-extend-companies, jobs-scrape-page,      │
│                       jobs-recycle-feedback, jobs-recalibrate,      │
│                       jobs-rescore, jobs-settings                   │
│   .claude/agents/     search-roles, validate-urls,                  │
│                       compile-write, notify-hot, scrape-extract     │
│   scripts/            notion-api.py, fetch-and-diff.py,             │
│                       validate-jobs.py, ats_adapters.py,            │
│                       build-state-chunks.py, validate-favorites.py, │
│                       diff-scrape.py                                │
│   scripts/schemas/    tracker_db.json, state_db.json                │
│   config/             companies.json (AI 50 list),                  │
│                       connectors.json (names + auth),               │
│                       profile.json (sample),                        │
│                       custom-companies.json (sample)                │
│   tests/              unit tests for filter logic                   │
└────────────────────────────────────────────────────────────────────┘
                              │
                              │ install / clone
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│ User's environment (per-user, gitignored)                           │
│   ~/.config/ai50-job-search/notion-token   (chmod 0600, secret)    │
│   <plugin>/state/.setup_complete            (sentinel)              │
│   <plugin>/state/cached-ids.json            (per-user Notion IDs)   │
│   <plugin>/.claude/settings.json            (their permission allow)│
└────────────────────────────────────────────────────────────────────┘
                              │
                              │ user data lives in
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│ User's Notion workspace                                             │
│   📄 AI 50 Job Search                       (parent page, anchor)   │
│   ├── 📊 Job Tracker                        (database)              │
│   │     • Title / Company / Score / Match / Location / Status /    │
│   │       URL / Department / Source / Date Added / Why Fits /      │
│   │       Key Factors / Match Quality / Feedback Comment / Recycled│
│   │     • One row per qualifying job                                │
│   ├── 📁 Hot Lists                          (parent page)           │
│   │     • One child page per run = dated digest                     │
│   ├── 📊 AI50 State                         (database)              │
│   │     • One row per company                                       │
│   │     • Job IDs in page body (JSON code block, multi-rich_text)   │
│   ├── 📄 AI 50 Profile (cloud mode only)    (page; JSON in body)    │
│   └── 📄 Extended Companies List (cloud only) (page; JSON in body)  │
└────────────────────────────────────────────────────────────────────┘
```

The plugin ships nine skills: `jobs` (menu), `jobs-setup`, `jobs-run`, `jobs-extend-companies`, `jobs-scrape-page`, `jobs-recycle-feedback`, `jobs-recalibrate`, `jobs-rescore`, and `jobs-settings`.

Per-user data lives in three places: a token file (the secret), `cached-ids.json` (the Notion IDs the runtime resolved), and the Notion workspace itself (the actual job content + per-run state). The plugin source ships with sample/template data only — no per-user state in Git.

---

## 2. Two deployment modes

The user picks one during setup. Both run the same pipeline; the difference is where profile / custom-companies / state live.

|                       | **Local mode**                          | **Cloud Routine mode**                       |
|-----------------------|------------------------------------------|----------------------------------------------|
| Profile lives at      | `config/profile.json` (in repo)         | "AI 50 Profile" Notion page (JSON in body)   |
| Custom companies at   | `config/custom-companies.json` (gitignored) | "Extended Companies List" Notion page (JSON in body) |
| State lives at        | `state/companies.json` or Notion DB     | "AI50 State" Notion DB (required)            |
| Token resolution      | File or env var                          | Env var (`NOTION_API_TOKEN`) — file irrelevant |
| Setup wizard runs on  | User's laptop, interactively             | One-time on laptop; Routine fires unattended thereafter |
| Update profile via    | Edit JSON file                           | Edit Notion page directly (changes apply on next run) |

**Why two modes?**

The Notion-based mode keeps the plugin repo generic and shareable. A Cloud Routine container is ephemeral — the local filesystem is wiped between runs — so any per-user data MUST live in a durable backing store. Notion is that store: it provides a usable read/edit UI out of the box, and there's no separate database to provision. See §10.2 for the full design rationale on Notion as the backing store.

Local mode exists for two reasons: (a) it's the one-step onboarding path before a user wants to commit to the Routine, and (b) it lets developers iterate on the plugin without a Notion round-trip on every test.

---

## 3. Two Notion auth methods

The user picks one during setup. Both are functionally equivalent; the difference is reliability.

|                        | **MCP (OAuth)**                                  | **API token (recommended)**                |
|------------------------|--------------------------------------------------|--------------------------------------------|
| Setup                  | Plug-and-play if Notion is connected             | Mint integration token, share parent pages |
| Token management       | None (OAuth)                                     | User mints; plugin stores file or env var  |
| Per-Notion-op cost     | One agent tool call                              | One HTTP request from `notion-api.py`      |
| Bulk ops               | Sequential agent tool calls                      | Threaded HTTP inside one CLI call          |
| Per-run tool calls     | 100+                                             | ~20                                        |
| Compounding error rate | ~0.5% per call → ~50% run-success at 100+ calls | ~0.1% per call → >95% run-success          |
| Use case               | Occasional laptop runs                           | Reliable Routines + bulk ops               |

**Why API token wins for unattended Routines:**

Each agent tool call has a small but non-zero chance of failing — Anthropic API blips, Notion 5xx, transport errors. With 100+ calls per run, those compound: at 99.5% per-call success, total run success = 0.995¹⁰⁰ ≈ 60%. That's "Routine fails most weeks" territory.

The API-token path consolidates bulk operations inside `notion-api.py` (a single Python process making concurrent HTTP requests). The agent runtime sees one tool call per bulk operation; the script absorbs network blips internally with retries / timeouts. Per-run tool count drops to ~20, total success rate stays >95%.

**Why MCP is still supported:**

MCP has nicer ergonomics for one-off interactive sessions — no token to mint, OAuth handles everything. Some users will only ever run the search manually on their laptop. For them, the reliability tax doesn't bite.

---

## 4. The pipeline — six passes per run

```
┌──────────────────┐
│  Pre-flight      │  P-0  Setup-sentinel check (or trigger setup wizard)
│  ~5 seconds      │  P-1  Load connectors.json + sentinel metadata
│  cold start      │  P-2  MCP prefix re-probe (mcp mode only)
│                  │  P-3  Resolve Notion IDs (cache → search → recreate)
│                  │  P-4  Hydrate profile/custom-companies/state into /tmp/
└──────────────────┘
        │
        ▼
┌──────────────────┐
│  Pass 1          │  search-roles agent invokes fetch-and-diff.py:
│  ~20 seconds     │   • Fetch all 50 companies in parallel (threaded HTTP)
│  fetch & diff    │   • Diff against /tmp/ai50-state.json
│                  │   • Apply title / region / workplace_type filters
│                  │   • Output: candidates, removed_jobs, filtered_out,
│                  │             static_notifications, external_companies
└──────────────────┘
        │ candidates (typically 5-20)
        ▼
┌──────────────────┐
│  Pass 2          │  validate-urls agent invokes validate-jobs.py:
│  ~10 seconds     │   • Group candidates by (ats, company-slug)
│  URL validation  │   • One API call per group, parallel
│                  │   • Returns live / closed / uncertain
└──────────────────┘
        │ live candidates
        ▼
┌──────────────────┐
│  Pass 3          │  compile-write agent:
│  ~30-60 seconds  │   • Apply hard exclusions (language, role category)
│  score & write   │   • Score against CV-grounded profile (LLM-judged
│                  │     categorical: High / Mid / Low buckets)
│                  │   • Query tracker DB for dedup (skip existing URLs)
│                  │   • Write rows with verdict ∈ {High, Mid}
│                  │     (also Low if profile.scoring.show_low == true)
│                  │   • Mark removed_jobs as Closed
│                  │   • On failure: emit /tmp/compile-write-failed.json
└──────────────────┘
        │ newly written
        ▼
┌──────────────────┐
│  Pass 4          │  Orchestrator (inline, not subagent):
│  ~30 seconds     │   • Build state chunks (build-state-chunks.py)
│  state persist   │   • Write each chunk to State DB sequentially
│                  │   • Verify 3 random rows (count + body parse)
└──────────────────┘
        │
        ▼
┌──────────────────┐
│  Pass 5          │  notify-hot agent:
│  ~10 seconds     │   • Filter newly-written to High bucket (or
│  hot list digest │     score ≥ hot_threshold on the structured path)
│                  │   • Format markdown digest, ordered by confidence
│                  │   • Create child page under hot-list parent
│                  │   • Always create one (even on "no hot matches")
└──────────────────┘
        │
        ▼
┌──────────────────┐
│  Pass 6          │  jobs-recycle-feedback skill (auto-trigger):
│  optional        │   • Gate: cloud mode AND profile.cv_json present
│  ~10-20 seconds  │     AND state/last_recycle.json older than 7 days
│  feedback loop   │   • Read tracker rows with Match Quality + !Recycled
│                  │   • Synthesize anti-patterns + few-shot examples
│                  │   • Append to profile.context / few_shot_examples store
│                  │   • Mark Recycled = true; write state/last_recycle.json
└──────────────────┘
        │
        ▼
   Run summary printed (incl. token usage + cost estimate)
```

**Total runtime:** 60-90 seconds for a typical run on 50 companies (Pass 6 adds ~10-20s when it fires; skipped most weeks).

---

## 5. Step P-3 — the discovery layer

The discovery layer replaces "user commits Notion IDs to their fork" with "plugin resolves IDs from names at run time."

### 5.1 The problem it solves

If `connectors.json` contained the user's tracker DB ID, hot-list page ID, etc. — values generated during setup, unique per user — sharing the plugin would mean every user has to fork the repo and commit their personal IDs. Friction; merge pain on updates; private data accidentally pushed to public forks.

### 5.2 The solution — three-tier resolution

```
1. Try cache             (fast path: /pages/<id> or /databases/<id>)
   ├─ HTTP 200, not archived → status=cached
   ├─ HTTP 404               → status=missing  → fall through
   ├─ HTTP 401/403           → status=no_access (token regression)
   └─ HTTP 429/5xx           → status=transient (keep cached, don't re-resolve)

2. If parent_page is missing → search by name
   └─ POST /v1/search { query: <name>, filter: page }
      ├─ exactly one title match → status=discovered
      ├─ multiple matches        → status=ambiguous (don't auto-pick)
      └─ no match                → status=missing

3. If parent_id resolved, list its children → match by title + block type
   └─ GET /v1/blocks/<parent>/children (paginated)
      ├─ child_page block, title match    → page artifacts
      ├─ child_database block, title match → DB artifacts
      └─ no match                          → status=missing
```

After resolution, the orchestrator dispatches per-artifact:

| Status        | recreate_ok policy            | abort_if_missing policy |
|---------------|-------------------------------|-------------------------|
| `cached`      | use cached ID                 | use cached ID           |
| `discovered`  | use discovered ID; cache it   | use discovered ID; cache it |
| `transient`   | use cached ID (transient API issue) | use cached ID    |
| `missing`     | RECREATE empty shell          | ABORT (user JSON content lost) |
| `no_access`   | ABORT (token regression)      | ABORT                   |
| `ambiguous`   | ABORT (don't auto-pick)       | ABORT                   |

**`recreate_ok`** artifacts: parent page, tracker DB, hot-list page, state DB. These are container-only — recreating them just gets you an empty shell, which the next run repopulates from the ATS.

**`abort_if_missing`** artifacts: AI 50 Profile, Extended Companies List. These hold user-edited JSON content. Recreating them would silently destroy the user's customizations. Safer to fail loudly.

### 5.3 Cache file (`state/cached-ids.json`)

```json
{
  "parent_page_id":            "...",
  "tracker_database_id":       "...",
  "hot_list_parent_page_id":   "...",
  "tracker_state_database_id": "...",
  "profile_page_id":           "...",
  "extended_companies_page_id": "...",
  "_resolved_at":              "2026-04-30T15:28:29+00:00",
  "_workspace_id":             "9a236f76-66be-813b-b0bb-00033fc4ae8c",
  "_workspace_name":           "Pavel M's Space"
}
```

Gitignored. Atomic-write via `tempfile.mkstemp` + `os.replace`. Workspace ID is a sanity check — if the user rotates the integration token to a different workspace, the next discover detects the mismatch and invalidates the cache.

### 5.4 Cold-start cost

For a Cloud Routine container with no persistent storage, every run starts cold:
- `/users/me` (~300ms — workspace identity check)
- `/search` for parent page (~500ms)
- `/blocks/<parent>/children` paginated (~1s for typical hierarchy)

≈ 2 seconds added to cold-start latency. Negligible vs the 60-90s pipeline.

---

## 6. The filter pipeline (Pass 1 details)

Three sequential filters reduce ATS-fetched jobs (typically 4,000-6,000 across 50 companies) to a candidate set (typically 5-20).

### 6.1 Stage A — title match (hard filter)

For each job, case-insensitive substring match against ANY keyword from ANY of the user's `role_types[].search_keywords` lists. Drop if no match.

Typical drop rate: 95-99% of fetched jobs. AI companies post engineering-heavy job lists; only a small slice matches a given role-type set.

### 6.2 Stage B — region eligibility

`classify_region(location)` maps a free-form location string to one of: `PRAGUE`, `UK_IE`, `APAC`, `LATAM`, `MEA`, `NORTH_AMERICA`, `EU_NON_UK`, `GLOBAL_REMOTE`, `UNKNOWN`. Order of precedence is narrow→broad (PRAGUE before EU_NON_UK; UK_IE before EU_NON_UK so London doesn't get EU benefits).

A defensive guard rejects strings containing negation prefixes (`non-`, `not `, `no `, `exclud-`) — without it, wizard meta-phrases like `"all non-EU"` would match the EU regex (hyphen is a word boundary) and silently exclude the candidate's home region.

### 6.3 Stage C — regional remote score

`build_score_table(home_region, eligible_regions, excluded_regions)` returns a `(workplace_type, region) → 0..3` lookup. Drop jobs scoring 0.

```
Remote:
  excluded            → 0
  home or eligible    → 3
  GLOBAL_REMOTE       → 3
  NORTH_AMERICA       → 2 (timezone-tier downgrade)
  other               → 1 (low signal)

Hybrid:
  excluded            → 0
  home_region match   → 3 (commute viable)
  eligible region     → 1 (relocation downgrade)
  other               → 0

Onsite:
  excluded            → 0
  home_region match   → 3
  eligible region     → 1 (relocation)
  other               → 0
```

The hybrid relocation downgrade ensures listings like "Hybrid Berlin" still surface for a Lisbon candidate explicitly open to EU relocation, rather than scoring 0 and being suppressed.

### 6.4 What the agent does vs what the script does

`fetch-and-diff.py` does the heavy lifting (network fetch, parallel parse, state diff, region classification). The orchestrator agent applies the filter pipeline to the script's `new_jobs` output:

```
script: new_jobs (raw diff) ──► agent applies filters ──► candidates
                                                       └─► filtered_out (count only, for stats)
```

**Why this split:** classification and scoring lookups are pure functions tested by `tests/test_region.py` + `tests/test_personas.py`. The agent only orchestrates; correctness of the rules lives in versioned, tested Python.

---

## 7. Scoring rubric (Pass 3 details)

Scoring is **CV-grounded LLM judgment**: hard exclusions filter aggressively first (deterministic, free), then surviving candidates get LLM-judged categorical scoring (`High` / `Mid` / `Low`) against the candidate's CV + free-form context. The Match column is the primary signal in the tracker; the numeric Score column is reserved (always null).

The plugin also closes the loop with a **Notion-feedback learning layer** — the `jobs-recycle-feedback` skill processes user-labeled tracker rows on a 7-day cadence, deriving anti-patterns and few-shot examples that get injected into the next run's Pass 3 LLM prompt. See §7.3.

### 7.1 Hard exclusions (applied BEFORE scoring)

Typed schema:

**Typed schema:** `profile.json[hard_exclusions]`, a structured block consumers apply deterministically at Pass 1 (fetch-and-diff) before any LLM scoring.

```json
{
  "schema_version": 1,
  "rules": [
    {"type": "country_lock", "reject_outside": ["EU", "Czech Republic"]},
    {"type": "language_required", "user_languages": ["English"], "reject_if_other_required": true},
    {"type": "title_pattern", "reject_if_contains": ["Marketing", "Sales", "Engineering Manager"], "unless_also_contains": []},
    {"type": "seniority_floor", "minimum_level": "senior_ic"},
    {"type": "remote_country_lock", "eligible_remote_regions": ["EU", "Czech Republic"], "_note": "Reject 'Remote — US-only' style listings; allow 'Remote — EU/Anywhere'."}
  ]
}
```

Compile-write applies these first; matched jobs are dropped without scoring. Counts are reported in the run summary so the user can sanity-check the filter.

### 7.2 CV-grounded LLM scoring path

The categorical Match column (High / Mid / Low) is the primary signal. The numeric Score column is reserved (always null) — kept in the schema only because removing a Notion property is awkward.

**Profile schema:**

The wizard's free-text background answer (stored as `profile.context`) serves as the narrative — wants/avoids/aspirations/target patterns. The LLM scoring path reads `profile.context` as the candidate intent.

```json
{
  "context": "Free-text background, goals, preferences. Read by LLM scoring as the candidate intent.",

  "cv_json": {
    "extracted_at": "2026-05-03",
    "source_format": "linkedin_pdf",
    "summary": "2-sentence headline",
    "experience": [
      { "role": "...", "company": "...", "from": "...", "to": "...",
        "scope": "...", "key_achievements": ["..."], "technologies_tools": ["..."] }
    ],
    "skills": { "leadership": [...], "technical": [...], "domain": [...], "languages_spoken": [...] },
    "education": [{"degree": "...", "institution": "...", "year": ...}],
    "career_signals": {
      "seniority_level": "...", "years_experience_total": N,
      "industry_focus": [...], "function_focus": [...], "geographic_base": "..."
    },
    "extracted_keywords": [/* ~30 phrases — technical + functional + domain */]
  },

  "hard_exclusions": { /* typed schema */ },

  "scoring": {
    "instructions": "Optional free-text LLM scoring hint, e.g. 'be strict on AI-native vs AI-bolt-on; reward customer-facing leadership over IC roles'."
  }
}
```

**Pass 3 flow on the LLM path:**

1. **Hard exclusions** (typed `hard_exclusions.rules`): drop candidate before any LLM call.
2. **Build LLM scoring prompt** for each survivor:
   - Profile (cv_json + narrative + hard_exclusions for context)
   - Few-shot examples from the feedback-recycle store (when available)
   - Listing (title, company, location, full JD)
   - Task: list `match:` / `concern:` / `gap:` factors comparing profile attrs to JD requirements; assign `High` / `Mid` / `Low`.
3. **Single model call per candidate.** Output: `{verdict, rationale, key_factors, confidence}`.
4. **Write to tracker** with: `Match` (verdict), `Key Factors` (bulleted match/concern/gap), `Score: null` (column unused on this path), `Why Fits` populated with rationale (back-compat for users who filter by it).
5. **Hot list = High bucket.** `notify-hot` digest renders High entries ordered by `confidence` (high → first).

**Bucket assignment rule:** Match density between profile attributes and JD requirements drives the bucket, with critical-concern weighting. High = matches dominate AND no critical concerns. Mid = mixed or sparse-JD default. Low = few matches or major requirements unmet. The LLM prompt forces explicit factor enumeration (the `key_factors` field) so bucket assignment is inspectable.

**Default model:** Claude Opus with extended thinking enabled (`{type: "enabled", budget_tokens: 4000}`). Opus is meaningfully stronger at multi-criteria evaluation and JD-decomposition than Sonnet/Haiku. Users who prefer cost over quality can override via `profile.scoring.instructions: "use sonnet for cost"` (or `"use haiku"`).

**Cost framing:** per pipeline run on Opus with extended thinking (typical run, ~50–200 candidates surviving hard exclusions): roughly **500K–1.5M tokens** total. Sonnet override: ~250K–700K tokens. Haiku: ~100K–300K tokens, with degraded rationale quality. Anthropic prompt caching is mandatory for the constant profile section (~8K of profile + cv_json + few-shot examples) — N-1 cache hits per run keep marginal-call cost low. Run summary prints aggregate token usage per pass.

### 7.3 Feedback-recycle learning loop (Pass 6)

The LLM path becomes a learning loop via the `jobs-recycle-feedback` skill, auto-triggered as Pass 6 of the orchestrator (gated on cloud mode + `profile.cv_json` present + ≥7 days since last cycle).

**Tracker DB columns supporting feedback:**

- `Match Quality` (select: `Great` / `OK` / `Bad`) — user feedback. Vocabulary deliberately matches `Match` (LLM verdict) so disagreements are direct comparisons.
- `Feedback Comment` (rich text) — user explanation, free text.
- `Recycled` (checkbox) — set true once jobs-recycle-feedback has processed this row; prevents double-counting.

**The recycle pipeline:**

1. Read tracker rows where `Match Quality` is set AND `Recycled` is unchecked.
2. Categorize by agreement / disagreement. Strong disagreements (`High`+`Bad`, `Low`+`Great`) get top weight.
3. Synthesize anti-patterns from clusters of ≥3 `Bad` entries sharing a pattern → propose to append to `profile.context` (user approves).
4. Curate 3-5 few-shot examples (`{job, llm_verdict, user_label, comment, lesson}` quads), persist to `state/few_shot_examples.json` (local) or a dedicated Notion page (cloud), capped at 10 — older entries roll off.
5. Mark each tracker row `Recycled = true`.
6. Write `state/last_recycle.json` with timestamp + counts (gates the auto-trigger).

The few-shot examples are the highest-leverage piece — they get included in every subsequent Pass 3 LLM scoring prompt, so the model generalizes from concrete labeled cases without the user hand-writing rules.

Manual invocation always works regardless of the auto-trigger gate (e.g. *"recycle feedback"*, *"update profile from labels"*).

---

## 8. State persistence (Pass 4 details)

The State DB has one row per company. Schema:

```
"Company key" (TITLE)         e.g. "ashby:cohere"
"Last checked" (DATE)         ISO date of last run
"Job count" (NUMBER)          length of the JSON array in body
"Notes" (RICH_TEXT)           free-form
```

**Job IDs live in the page body**, not as a property. Each row's body is one fenced ```json code block holding the array of job-ID strings. The notion-api.py helper splits the array across multiple `rich_text` elements within the same code block — Notion enforces a 2000-char limit per element, but multiple elements within one code block are concatenated transparently.

### Why body, not property

Storing job IDs in a single `rich_text` property hits Notion's 2000-char-per-element silent truncation. High-volume companies (Cohere with 200+ jobs, OpenAI with 651, Databricks with 829) have their state corrupted invisibly — every run treats them as "fresh" and re-emits every job as new. Body storage with multi-element splitting avoids this; the `Job count` property is a tripwire that should equal the body array length.

### Chunking + sequential writes

`build-state-chunks.py` packages the state into per-chunk page payloads:
- Default chunk size: 5 rows
- Big-row threshold: 200 jobs (companies with > 200 IDs get their own chunk to keep Read tool result size manageable)

The orchestrator then issues one `notion-api.py create-pages` call per chunk, sequentially. Sequential because the agent's transcript receives a small response per write, and parallel writes can interleave with rate-limit windows (Notion permits ~3 req/s sustained).

### Verification

After all chunks land, the orchestrator picks 3 random rows, fetches each body, parses the JSON, confirms `len(parsed_array) == Job count`, and confirms set-equality against `/tmp/ai50-state.json`. Mismatch = silent truncation; abort with a loud error.

---

## 9. Markdown fallback contract (failure handling)

Two agents (compile-write, notify-hot) write to Notion. Both can fail mid-run on transient Notion errors, auth regressions, or API outages. The orchestrator handles fallback so results aren't lost.

### 9.1 Failure response from agents

Both agents emit a structured failure file before aborting:

```
/tmp/compile-write-failed.json    /tmp/notify-hot-failed.json
```

Each contains: `schema_version`, error code, ISO timestamp, the ID the orchestrator passed, the rows the agent prepared but didn't land, and (for compile-write) the **`failed_ats_job_ids`** — the exact `id` field from the candidates input, so the orchestrator can un-poison `/tmp/ai50-state.json` before Pass 4 persists it.

The agent returns `{"error": "...", "fallback_file": "/tmp/<agent>-failed.json"}` to the orchestrator. The discriminator is the presence of `fallback_file` — success responses never contain that key.

### 9.2 The state-poisoning bug this prevents

Without this fallback handler, a subtle correctness hole exists: when compile-write fails, the orchestrator continues to Pass 4. Pass 4 persists `/tmp/ai50-state.json` (which already contains the failed job IDs as "seen" — Pass 1 added them when fetching). Future runs would treat those IDs as already-handled and never retry them, even though they never made it to the tracker.

The fallback handler removes `failed_ats_job_ids` from `/tmp/ai50-state.json` BEFORE Pass 4 persists. Next run's diff sees them as new and retries the write. Self-healing.

### 9.3 Orchestrator behavior on missing/malformed agent response

If an agent crashes without writing the fallback file, the orchestrator's parser can't find `fallback_file`. Treats this as `agent_crashed_no_response` — surfaces a P0 warning, skips the un-poison step (no IDs to un-poison), and aborts the run rather than persisting potentially-corrupt state.

---

## 10. Technology choices and rationale

### 10.1 Python stdlib only (no SDK, no dependencies)

`scripts/notion-api.py` uses `urllib.request` directly instead of the `notion-client` SDK. Three reasons:

1. **Zero install friction.** The plugin runs in a Cloud Routine container with no `pip install` step. Python 3 + stdlib is always present.
2. **Auditability.** ~700 lines of vanilla HTTP. A user (or a CI pipeline) can read it cover-to-cover. SDK code is opaque and pulls in transitive dependencies that need security review.
3. **Predictable behavior under failure.** Stdlib `urlopen` raises `URLError` / `HTTPError` with stable types; SDKs add their own exception hierarchies and retry policies that interact unpredictably with Claude Code's tool-call timeout.

`fetch-and-diff.py` and `validate-jobs.py` follow the same convention.

### 10.2 Notion as the backing store

Considered but rejected: SQLite, Postgres, S3, GitHub-stored YAML.

Notion was chosen as the destination because:
- **Low setup friction**: a free account + one integration token is enough to get started; no database/server provisioning, no schema migrations, no infra to maintain.
- **Editable UI for free**: users can triage, label, and add notes directly in Notion's familiar block editor — no custom UI to build.
- **JSON-in-page-body pattern**: profile and custom-companies live as JSON code blocks the user can edit in Notion's UI; richer than a hidden config file and discoverable next to the rest of the workspace's data.
- **No vendor lock-in for the plugin**: the plugin repo stays generic; per-user data lives in the user's own Notion workspace, not in any backend the plugin operator runs.
- **Workable for non-coders**: anyone comfortable with Notion (or willing to learn its block editor) can use the plugin without writing code.
- **Stable, free API**: the Notion API is straightforward and has no rate-limit surprises at this scale; Cloud Routines need durable storage and Notion provides it without any infra.

The trade-off: `rich_text` per-element 2000-char limit forced the multi-element-in-code-block hack for state. Worth it.

### 10.3 ATS APIs over scraping

JavaScript-driven scraping of SPA-rendered ATS sites (Ashby, Lever) yields a high false-negative rate for closed-job detection because non-JS clients see only an empty shell. The plugin uses the providers' public posting APIs instead. Eleven ATS are first-class supported (fetch + validate via deterministic API), plus a generic LLM-extracted fallback (`scrape`):

- **ashby** — `api.ashbyhq.com/posting-api/job-board/<slug>`
- **greenhouse** — `boards-api.greenhouse.io/v1/boards/<slug>/jobs` (classic) and `boards-api.eu.greenhouse.io/...` (EU data residency). The fetcher tries both hosts so customers like Parloa / JetBrains on the EU subdomain don't silently 404.
- **lever** — `api.lever.co/v0/postings/<slug>?mode=json`
- **comeet** — `www.comeet.com/careers-api/2.0/company/<id>/positions?token=...` (token scraped from the public careers HTML)
- **teamtailor** — `<slug>.teamtailor.com/api/v1/jobs?page[size]=200` (JSON:API)
- **homerun** — `api.homerun.co/v1/jobs/?company_subdomain=<slug>`
- **smartrecruiters** — `api.smartrecruiters.com/v1/companies/<slug>/postings?limit=100&offset=...`. Paginated (offset-based, capped at 20 pages = 2000 jobs to bound a runaway). User-facing URL constructed as `jobs.smartrecruiters.com/<slug>/<id>` since the documented `jobAdUrl` field comes back null in practice.
- **workable** — `apply.workable.com/api/v1/widget/accounts/<slug>` (the v3 `/api/v3/accounts/<slug>/jobs` endpoint surfaced in older docs returns 404 on the apply.* host; the v1 widget is what real boards serve). Returns `{name, description, jobs: [...]}`. Keys on `shortcode`.
- **recruitee** — `<slug>.recruitee.com/api/offers/`. Returns full description + requirements inline.
- **personio** — `<slug>.jobs.personio.de/xml`. XML feed with `<workzag-jobs>` root and `<position>` children. The `.de` TLD is canonical; `.com` / `.es` / `.it` accept the same path. `<jobDescription>` fragments concatenated for the description body.
- **bamboohr** — `<slug>.bamboohr.com/careers/list`. Returns `{result: [...], meta: {totalCount}}` with nested `location: {city, state}` + `atsLocation: {country, state, province, city}`. Description deferred to per-job detail call.
- **scrape** — generic agent-extracted careers-page fallback. For companies whose ATS isn't in the supported set (custom-built careers pages, Workday boards under `<tenant>.wd*.myworkdayjobs.com`, SAP SuccessFactors career sites, Greenhouse boards behind a non-standard wrapper, etc.), the user opts in per-company via `{ats: "scrape", careers_url: "..."}` in their custom-companies list. Implemented as a Claude Code agent (`.claude/agents/scrape-extract.md`) so users don't need a separate Anthropic API key — extraction runs against their Claude.ai subscription quota the same way other agents do. Pipeline: `fetch-and-diff.py` emits `/tmp/needs_scraping.json`; the `search-roles` agent dispatches `scrape-extract` per company in parallel; `scripts/diff-scrape.py` computes the new/removed delta against state. Standalone use: the `jobs-scrape-page` skill wraps the same agent for ad-hoc extraction-quality testing on a single URL. No deterministic API to validate against, so scrape candidates land as `Status: Uncertain` in the tracker.

These are public (no auth required for read where possible), well-documented, and stable. The plugin caches no data from them — every run fetches fresh.

Adding a new deterministic ATS is a one-place change: add a registry entry to `scripts/ats_adapters.py` (URL pattern + active-id fetcher + `active_validate_supported` flag) and add a fetch + normalize pair to `fetch-and-diff.py`'s `FETCHER_DISPATCH`. See §10.7 below.

### 10.4 Threaded HTTP via stdlib

`fetch-and-diff.py` fetches all 50 companies in parallel via `concurrent.futures.ThreadPoolExecutor`. Each fetch is ~500ms; serial would be 25 seconds, parallel is 3-5 seconds. Network-bound work; the GIL is fine.

### 10.5 Tests: stdlib `unittest`

`tests/run.sh` uses `python3 -m unittest discover` — no pytest install required. Tests run in ~3ms. The test layer pins region classification and score-table semantics, which are the two places where bugs hide longest. URL-pattern + ATS-registry tests guard the shared adapter module as new ATS are folded in.

### 10.6 Deterministic CLI helpers + LLM agents for synthesis only

The agents (search-roles, validate-urls, compile-write, notify-hot) do orchestration + LLM-shaped work (writing fit rationales, formatting digests). All deterministic logic — region classification, score tables, ATS fetching, state diffing — lives in Python scripts the agents invoke. This split keeps the hot path testable and reduces compounding-error surface area (see §3 on auth methods).

### 10.7 `ats_adapters.py` — single source of truth for ATS support

ATS knowledge is centralized in `scripts/ats_adapters.py` rather than duplicated across `validate-jobs.py`, `validate-favorites.py`, and `fetch-and-diff.py`.

The module exports:

- `ATS_ADAPTERS` — registry dict keyed by ATS name. Each entry holds a `url_pattern` regex (with slug capture group), an `active_ids_fetcher` callable, and an `active_validate_supported` flag.
- `ats_from_url(url)` — primary URL→`(ats_name, slug)` dispatch; returns `None` for URLs matching no pattern. Adapters with `url_pattern: None` (e.g. `scrape`) are skipped — they're only matched when explicitly tagged in a custom-company entry, not by URL inspection.
- `active_ids_for(ats, slug, **kwargs)` — dispatch to the right fetcher; returns `(set_of_ids, error_or_none)`.
- `supported_ats_for_validate()` — set of ATS names whose live state can be confirmed via API.

`validate-jobs.py`, `validate-favorites.py`, and the `jobs-extend-companies` skill all call `ats_from_url()` to derive `(ats, slug)` from a pasted careers URL. The fetcher counterpart for full job lists (used by Pass 1) lives in `fetch-and-diff.py`'s `FETCHER_DISPATCH` — adding a new deterministic ATS needs both sides registered. The `scrape` ATS is special: registry entry stays for visibility, but `active_ids_fetcher` is `None` (handled by the scrape-extract agent post-fetch via `diff-scrape.py`).

---

## 11. User journey

```
Stage 1 — Install + interactive setup    (one-time, laptop, ~10 minutes)
─────────────────────────────────────────────────────────────────────────
  1. Install plugin (claude.ai marketplace, or git clone)
  2. Mint Notion integration token at notion.so/profile/integrations
     and share parent page with the integration via Connections menu
  3. Type "set up the plugin"
     → Wizard asks ~10 questions (location, work mode, role types,
       seniority, languages, scoring criteria + priorities)
     → Wizard creates 1 parent page + 2 DBs + 3 child pages in Notion
     → Wizard writes connectors.json, cached-ids.json, sentinel
  4. Type "run the job search" (verifies wiring; populates state DB)

Stage 2 — Optional: Schedule a Cloud Routine    (one-time)
─────────────────────────────────────────────────────────────────────────
  1. claude.ai/code/routines → New Routine
  2. Environment: NOTION_API_TOKEN + (optional) NOTION_PARENT_ANCHOR_ID
  3. Allowed domains: api.notion.com,
                      *.ashbyhq.com, *.greenhouse.io,
                      *.lever.co, *.comeet.com,
                      *.teamtailor.com, *.homerun.co, surgehq.ai
     (api.anthropic.com is NOT needed — all LLM work runs through
     Claude Code agents, not direct urllib calls.)
  4. Setup script (~10 lines) — discovers plugin path, creates
     sentinel, runs auth pre-check
  5. Trigger prompt: "Run the AI 50 job search. Non-interactive
     Routine — fail fast."
  6. Schedule weekly (e.g. Mon 08:00)

Stage 3 — Ongoing                                (no-op for the user)
─────────────────────────────────────────────────────────────────────────
  Each Routine fire (or other trigger):
    ├─ Routine container fires
    ├─ Setup script creates sentinel + verifies auth
    ├─ Plugin runs the 6-pass pipeline (~60-90s; Pass 6 gates on 7-day cadence)
    ├─ New qualifying jobs land in Tracker DB
    ├─ Hot-list digest page created with the week's matches
    ├─ jobs-recycle-feedback (when gate met) updates profile from labels
    └─ User checks Notion when ready
```

To update the profile or rubric, the user edits the AI 50 Profile Notion page directly. Changes apply on the next run. The plugin reads but never writes back to that page; it's user-owned.

---

## 12. Configuration model

### 12.1 What ships with the plugin

- `config/connectors.json` — `notion.names` (default artifact names) + `auth_method` slot (set during setup)
- `config/companies.json` — the AI 50 list with ATS details (slugs, etc.)
- `config/profile.json` — sample data for local-mode template
- `config/custom-companies.json` — sample data (gitignored on per-user installs)
- `scripts/schemas/{tracker_db,state_db}.json` — Notion DB schemas

### 12.2 What the user generates

- `state/.setup_complete` — sentinel
- `state/cached-ids.json` — resolved Notion IDs
- `~/.config/ai50-job-search/notion-token` — secret
- `.claude/settings.json` — permission allowlist

### 12.3 What lives in Notion

- Profile page body — JSON code block, full profile
- Extended Companies List page body — JSON code block, array of custom-tracked companies
- State DB rows — per-company job IDs
- Tracker DB rows — per-job entries
- Hot Lists child pages — weekly digests

---

## 13. Versioning

v1.0.0 is the first public release. Public versioning starts at v1.0.0; future patches and minor versions follow semver:

- **Major** bumps for breaking changes to the user-visible flow.
- **Minor** bumps for new features (new ATS support, new skill) or significant refactors.
- **Patch** bumps for bug fixes.

The canonical version lives in CHANGELOG.md's top entry; SKILL.md frontmatter versions are kept in sync per skill (each skill bumps independently when it changes).

A two-track development workflow uses `dev` (integration) and per-feature branches.

---

## 14. Failure modes and known limitations

| Failure | Behavior | Recovery |
|---------|----------|----------|
| Notion archived parent page | Discover detects, recreate-or-abort policy applies | Set `NOTION_PARENT_ANCHOR_ID` env var |
| Notion deleted profile / extended-companies page | Abort with `user_content_missing` | Re-run setup wizard |
| Notion API outage | Compile-write/notify-hot emit fallback files; orchestrator writes markdown to `outputs/` | Investigate; next run retries |
| ATS endpoint down | Fetch-and-diff records error in `fetch_errors`, continues with other companies | Self-healing on next run |
| Token rotated to different workspace | Workspace-ID sanity check invalidates cache | Discover re-resolves cleanly |
| Race condition: two Routine fires simultaneously | Atomic cache write ensures one wins; the other sees its IDs cached or re-discovers | Schedule routines further apart |

### Known limitations (roadmap)

- `removed_jobs_pending` (closures the agent didn't reach before failing) currently relies on the next run's diff to re-surface the same removed_jobs. Edge case: if a job is briefly re-listed and re-removed in the gap, the close-mark is lost. A durable `state/pending-closures.json` queue is on the backlog.
- No retry policy at the agent level on transient 5xx — the orchestrator-level fallback handles this, but a per-call retry inside the agent would be tighter.
- Cloud Routine env-var visibility: `NOTION_API_TOKEN` is visible to anyone with edit access on the routine. Rotate periodically if you share access. (No `ANTHROPIC_API_KEY` is needed — the scrape ATS runs as a Claude Code agent against your Claude.ai subscription quota, not a direct API call.)
- Scrape adapter (`ats: scrape`) is single-page and HTML-only. Pure-SPA careers pages (Workday-style) return empty shells to non-JS clients (the scrape-extract agent surfaces this as `extraction_quality: "no_static_content"`); the user is better off finding the underlying API endpoint and adding a real ATS adapter, or marking the company `ats: skip`.
- Token usage tracking is print-only — not persisted to a Notion Run Log or local jsonl. Trend analysis over time requires manual copy-paste into a spreadsheet.

---

## 15. Testing

```
tests/
  run.sh                  Entry point — `bash tests/run.sh`
  _helpers.py             Shared utilities (load fetch-and-diff as a module)
  fixtures/               Sample ATS responses for fetcher tests
  test_region.py          classify_region + build_score_table
  test_personas.py        Persona-scenario score-table tests
  test_diff.py            diff_company logic
  test_fetchers.py        Per-ATS normalisation
  test_normalise.py       Job-shape canonical form
```

Tests run in <1 second. No external dependencies. Run on every change to `scripts/fetch-and-diff.py`, `scripts/ats_adapters.py`, or related logic.

The persona-scenario suite (`test_personas.py`) pins eight common candidate archetypes (home-city / home-region / multi-region / global-remote / on-site-only / etc.) so archetype-specific filter bugs are caught — a single home-region test set isn't enough to surface relocation-friendly persona regressions.

---

For installation and Routine setup, see [INSTALL.md](INSTALL.md).
For the full release history, see [CHANGELOG.md](CHANGELOG.md).
