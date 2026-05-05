---
name: run-job-search
description: >
  This skill should be used when the user wants to run the AI 50 job search,
  find new job openings, scan for roles, update the job tracker, or check what's
  new in the AI job market. Trigger phrases include: "run the job search",
  "scan for jobs", "check for new roles", "run AI 50 search", "update my job tracker",
  "find jobs", "what's new on the AI 50 list".
metadata:
  version: "3.4.0"
  author: "Pavel Malyshev"
  edition: "Claude Code / Routines"
---

Orchestrate the AI 50 job search pipeline. Designed for **Claude Code Routines** — stateless cloud execution where the local filesystem may not persist between runs.

State storage:
- **Notion state DB** (required for Routines, recommended even locally) — schema in `agents/compile-write.md`. State persists in the cloud and survives re-deploys.
- **Local file** (fallback if state DB not created during setup) — state lives at `./state/companies.json`. Suitable for laptop use only; Routine cold-starts wipe it.

Per-user IDs are NOT in the repo. They live in `./state/cached-ids.json` (gitignored). The runtime resolves them via `notion-api.py discover` on every run — fast on cache hit, self-healing on cache miss.

## Pre-flight

### Step P-0 — Setup check

Look for `./state/.setup_complete`.

If it does **not** exist, this is a first run. Print:

```
Welcome to AI 50 Job Search! Before the first search, we need to configure your profile.
```

Immediately execute the full setup wizard at `./skills/setup/SKILL.md`. The wizard handles deployment-mode choice, profile collection, scoring, Notion auth, and creates the sentinel + cached-ids.json on completion.

If setup is abandoned mid-way (no sentinel created), stop and tell the user: *"Setup incomplete. Type 'run the job search' to try again, or 'run onboarding' to launch the wizard directly."*

In a **Cloud Routine** the sentinel is reset on every cold start; the Routine setup script (configured at the Routine env level) is responsible for re-creating the sentinel deterministically before this orchestrator runs. If the sentinel is missing in a Routine context, the orchestrator should NOT trigger the interactive wizard — it should fail loudly with: *"Setup sentinel missing in non-interactive Routine context. The Routine setup script should create state/.setup_complete before the agent fires."*

### Step P-1 — Confirm config files exist

1. Confirm `./config/connectors.json` and `companies.json` exist and parse as valid JSON. (`profile.json` and `custom-companies.json` may be local-only — they're hydrated from Notion in cloud mode, see P-3.)
2. Read `connectors.json[connector_type]`. For Routine compatibility this should be `"notion"` (markdown is fallback only).
3. Read `./state/.setup_complete` to determine `deployment_mode` (cloud / local) and `auth_method` (mcp / api_token).

**`connectors.json` is read-only at runtime.** The setup wizard owns this file; the orchestrator and all downstream scripts must never write to it. In Routine cold-starts the Routine setup script re-creates `state/.setup_complete` with the correct `auth_method` — connectors.json may still show `null` for that field (it is the shipped template). Always use the sentinel as the authoritative source for `auth_method` and `deployment_mode`; do not attempt to "fix" connectors.json if those values differ.

### Step P-2 — MCP prefix re-probe (auth_method == "mcp" only)

Connector-installed Notion uses a UUID server-id that can rotate on reconnect, so the cached `mcp_tool_prefix` may be stale. Cheap probe:
- Call `ToolSearch` with query `"notion-search"`. Read returned tool names.
- Pull the prefix portion (everything before `notion-search`). If it differs from `connectors.json[notion.mcp_tool_prefix]`, update the file in-place and refresh `mcp_tool_prefix_resolved_at`.
- If `ToolSearch` returns no Notion tools → abort with: *"Notion MCP not available in this session. Re-run 'set up the plugin' or restore the Notion connection (claude.ai/code Connectors → Notion, or `claude mcp add notion --transport sse https://mcp.notion.com/sse`)."*

Skip this step entirely if `auth_method == "api_token"`.

### Step P-3 — Resolve Notion artifact IDs (cloud connector only)

The plugin's 6 Notion artifacts (parent page, tracker DB, hot-list page, state DB, profile page, extended-companies page) need their IDs resolved before the pipeline can run. We use a 3-tier approach: cache → discover-by-name → recreate.

```bash
python3 ./scripts/notion-api.py discover \
  --config     ./config/connectors.json \
  --cache-file ./state/cached-ids.json
```

The script returns JSON with one entry per artifact. Each entry has:
- `name`, `id`, `kind` (page or database), `recreate_policy` (recreate_ok or abort_if_missing)
- `status`: one of `cached`, `discovered`, `missing`, `no_access`

**Per-artifact action based on status:**

| Status | recreate_policy = recreate_ok | recreate_policy = abort_if_missing |
|---|---|---|
| `cached`     | use the cached ID                            | use the cached ID |
| `discovered` | use the discovered ID (cache file updated)   | use the discovered ID |
| `missing`    | RECREATE (see below)                         | ABORT — direct user to re-run setup |
| `no_access`  | ABORT — token can't see this page (workspace mismatch?) | ABORT — same |

**Recreating a missing artifact** (only `recreate_ok` types — parent_page, tracker_db, hot_list_page, state_db):

1. **Determine the parent** for non-parent_page artifacts: parent = the resolved `parent_page` ID from this discover result.

2. **If `parent_page` is itself missing**, this is a serious situation:
   - **In an interactive session** (no `NOTION_PARENT_ANCHOR_ID` env var, but human present): print a loud warning and ask the user to confirm before recreating the hierarchy under a workspace anchor.
   - **In a Routine** (no human, no `NOTION_PARENT_ANCHOR_ID`): ABORT with the error: *"Parent page missing and no NOTION_PARENT_ANCHOR_ID configured for non-interactive recreation. Set the env var to a known anchor page ID, or re-run setup interactively."*
   - **In any context with `NOTION_PARENT_ANCHOR_ID` set**: recreate the parent page under that anchor.

3. **Recreate** using `notion-api.py` with the canonical JSON schemas at `./scripts/schemas/`:
   ```bash
   # tracker_db
   python3 ./scripts/notion-api.py create-database \
     --parent-page-id <parent_id> \
     --title          "<connectors.json[notion.names.tracker_db]>" \
     --schema         ./scripts/schemas/tracker_db.json

   # state_db
   python3 ./scripts/notion-api.py create-database \
     --parent-page-id <parent_id> \
     --title          "<connectors.json[notion.names.state_db]>" \
     --schema         ./scripts/schemas/state_db.json

   # parent_page or hot_list_page (no schema; just create as a page)
   #   prepare a 1-element JSON array [{"properties": {"title": "<name>"}, "content": "..."}]
   #   then call: notion-api.py create-pages --pages <file> --parent-id <parent> --parent-type page
   ```

   The schemas are the **single source of truth** — both the setup wizard and this recreate path read them from `scripts/schemas/`. Don't inline schemas anywhere else.

4. **After creation**, re-run `notion-api.py discover` to pick up the new IDs into `cached-ids.json`. (Cheaper alternative: write the new ID directly into the cache file using the same field-name convention as `DISCOVER_KEYS` in `notion-api.py` — but the discover-after-create path is simpler and verifies the result.)

**Aborting on `abort_if_missing` (profile_page, extended_companies_page):** print

```
ERROR: AI 50 Profile / AI 50 Favorites page is missing or inaccessible.

These pages contain your profile and custom-companies JSON — recreating them
empty would lose your customizations. Please:
  1. Open Notion and check whether the page was archived or deleted.
  2. If lost, re-run 'set up the plugin' to recreate from scratch.
  3. If just archived, un-archive in Notion and re-run the search.
```

Then exit non-zero. Do NOT proceed to hydration.

After this step, all six IDs (or all four if local mode without profile / extended-companies pages) are populated in `state/cached-ids.json` and ready for use by subsequent steps.

### Step P-4 — Hydrate user data into /tmp/

`deployment_mode` (read in P-1) determines whether profile/custom-companies come from Notion or local files.

All page/DB IDs in this section come from `state/cached-ids.json` (resolved in Step P-3).

#### Profile

**Cloud mode:** fetch the Notion page at `cached-ids.profile_page_id`. Extract the JSON code block from the page body (should be a complete `profile.json` document). Write it to `/tmp/profile.json`.

If parsing fails (page edited to invalid JSON, missing code block), abort with:
> "Profile page in Notion does not contain valid JSON. Edit the page (notion.so/{profile_page_id}) and try again. The page body must contain a single ```json code block holding the full profile."

**Local mode:** no action needed — agents read `./config/profile.json` directly.

#### Favorites

**Cloud mode:** fetch the page at `cached-ids.extended_companies_page_id`, extract the JSON code block (array of custom-tracked company objects), write to `/tmp/custom-companies.json`. If the array is empty, write `[]`.

**Local mode:** no action needed — scripts read `./config/custom-companies.json` directly.

#### State

**Cloud mode** (state DB): the schema is:

| Column | Type | Purpose |
|---|---|---|
| Company key | TITLE | e.g. `ashby:cohere` |
| Last checked | DATE | ISO date of last run |
| Job count | NUMBER | Convenience — number of IDs stored in the body |
| Notes | RICH_TEXT | Free-form notes; not used by the plugin |

**Job IDs are stored in the page body**, not in a property. Body content: a single fenced ```json code block holding a JSON array of job-ID strings. This avoids Notion's 2000-char per-rich-text-block limit, which silently truncated state for high-volume companies (Cohere/Anthropic/Cursor) in v2.1.0. The token-mode helper script (`scripts/notion-api.py`) splits content across multiple rich_text elements inside the same code block to support arrays > 2000 chars.

#### Hydration — branch on auth_method

Use `auth_method` resolved from the sentinel in P-1 — do **not** re-read `connectors.json[notion.auth_method]` here. In a Routine cold-start that field may be `null` (shipped template value) even when the sentinel correctly records `"api_token"`.

**`auth_method == "api_token"` (Routine-friendly path):**

Read `cached-ids.tracker_state_database_id`. One Bash call does the entire hydration in parallel:

```bash
python3 ./scripts/notion-api.py hydrate-state \
  --database-id <tracker_state_database_id from cached-ids.json> \
  --output      /tmp/ai50-state.json \
  --max-workers 10
```

The script queries the database, parallel-fetches every row's body via threading, parses each code block, and writes the assembled state JSON. Typical 50-company DB: ~5–10 seconds end-to-end. This is the recommended path for Routine cold starts because it doesn't depend on agent runtime parallelism.

**`auth_method == "mcp"`:**

Query the database via the resolved MCP prefix (`<prefix>notion-search` with `data_source_url`), then **parallel-dispatch** `<prefix>notion-fetch` calls for every row's body in a SINGLE message containing ≤ 10 tool-use blocks per batch. For 50 companies that's 5 batches of 10 parallel fetches — about 30–60 seconds total instead of 5+ minutes serial.

The orchestrator MUST issue each batch as parallel tool-use blocks in one message. Sequential dispatch (one fetch, wait for response, next fetch) is what made v2.2.1 hydration take 5+ minutes — do not regress.

For each row, extract the JSON code block from the body and assemble:

```json
{
  "ashby:cohere": {
    "last_checked": "2026-04-25",
    "company_name": "Cohere",
    "jobs": {"<job_id>": {"title": "", "url": "", "company": "Cohere"}}
  },
  "_meta": { "notifications": {...}, "last_run": "2026-04-25" }
}
```

If the body of any row fails to parse as JSON, **abort with a hard error** — do not continue the run silently. Truncated state is worse than no state because it produces phantom "new" jobs every run.

Title/url for *removed* jobs is unavailable when the state-DB backend is in use — fine because compile-write only needs the ID to mark a row Closed.

Write to `/tmp/ai50-state.json`. If empty database, write `{}`.

**Local mode:** copy `./state/companies.json` to `/tmp/ai50-state.json` (or write `{}` if missing).

## Pipeline

### Pass 1 — Fetch & Diff (search-roles agent)

Invoke **search-roles**. In **cloud mode**, pass all three hydrated paths:

```
--state-file /tmp/ai50-state.json --profile-file /tmp/profile.json --custom-companies-file /tmp/custom-companies.json
```

In **local mode**, pass only `--state-file /tmp/ai50-state.json` (the script defaults profile / custom-companies to the repo paths).

Prompt:
> "Run fetch-and-diff.py with the flags above to fetch all configured ATS endpoints in parallel, diff against stored state, and filter new jobs by profile keywords and location rules. Return: `candidates` (filter-passed), `filtered_out` (filter-rejected, for stats only), `removed_jobs` (the script's diff-based removed array — `[]` on first run, never anything else), `static_notifications`, `external_companies`, `skipped_companies`, and the path to the updated state file. **Do NOT conflate `filtered_out` with `removed_jobs`** — only the latter goes to compile-write for tracker-Closed updates. The script supports `--help` if you need to see all flags."

Typical runtime: 10–30 seconds for 50 companies. The script writes the updated state back to `/tmp/ai50-state.json` on completion.

Supported ATS types: `ashby`, `greenhouse`, `comeet`, `html_static`, `static_roles` (notification-only), `external` (notification-only), `skip`. **There is no separate browser-fetch pass** — Chrome MCP is no longer required.

### Pass 2 — URL Validation (validate-urls agent)

Write the `candidates` array from Pass 1 to a file (e.g. `/tmp/pass2-candidates.json`), then invoke **validate-urls** with the path AND the same companies / custom-companies files Pass 1 used (NOT plugin_root defaults — see historical note below):

> "Run validate-jobs.py on `/tmp/pass2-candidates.json`, passing `--companies-file <same-as-Pass-1>` and `--custom-companies-file <same-as-Pass-1>`. Returns live / closed / uncertain. Pass `live` AND `uncertain` forward to compile-write — uncertains get written to the tracker with `Status: Uncertain` so the user can spot-check them in Notion. Closed entries don't get passed (they're handled separately as removed_jobs from Pass 1's diff)."

**Cloud mode flag values:**
```
--companies-file /tmp/companies.json    (Notion-hydrated, same as Pass 1)
--custom-companies-file /tmp/custom-companies.json    (Notion-hydrated, same as Pass 1)
```

**Local mode flag values (or omit for plugin_root defaults):**
```
--companies-file ./config/companies.json
--custom-companies-file ./config/custom-companies.json
```

**Why this matters (historical bug, fixed v3.0.6).** Earlier versions of `validate-jobs.py` hardcoded its index source to `plugin_root/config/{companies,custom-companies}.json`. In cloud mode that's the **shipped template** (generic baseline). The user's actual custom-tracked companies live in Notion, hydrated to `/tmp/custom-companies.json` by P-4. Pass 1 used the hydrated data; Pass 2 silently used the template; custom-tracked companies (Parloa, Nebius, JetBrains, Make, etc.) became `company_name_not_in_index` in Pass 2 even though Pass 1 happily fetched their jobs. v3.0.6 made the file paths explicit args; orchestrator passes the same paths to both passes.

**Why uncertains now get written (v2.5.2):** Pre-v2.5.2, uncertains were dropped at this boundary — only `live` went forward. But Pass 4 still persisted their job IDs to state, which meant the next run's diff treated them as "seen" and they were silently consumed. User never saw them. Now uncertains travel with live to compile-write and land in the tracker with a distinct status, preserving review opportunity.

The agent uses an **API-based validator** (`scripts/validate-jobs.py`) that queries each ATS's posting API directly — same endpoints `fetch-and-diff.py` uses to enumerate jobs. This replaces v2.2.0's WebFetch + HTML closure-signal approach, which produced ~65% false-negatives on SPA-rendered ATS (Ashby, Lever) because non-JS clients see only an empty shell.

If candidates list is empty: write `[]` to the file and skip the agent invocation.

The validator is fast — one API call per unique `(ats, slug)` group, parallelised. A 49-candidate run across 14 companies completes in under 10 seconds.

### Pass 3 — Score & Write (compile-write agent)

Invoke **compile-write** with both live and uncertain candidates from Pass 2, the `removed_jobs` array from Pass 1, AND the resolved IDs from `cached-ids.json`:

```
Inputs to pass (write to /tmp/pass3-input.json):
{
  "live":            [<candidates Pass 2 confirmed live>],
  "uncertain":       [<candidates Pass 2 couldn't validate either way>],
  "removed_jobs":    [<from Pass 1 — closures>],
  "tracker_db_id":   "<cached-ids.tracker_database_id>"
}

Other paths (passed inline):
  - Profile path:         /tmp/profile.json (cloud mode) or config/profile.json (local)
  - Connectors path:      ./config/connectors.json
  - auth_method:          <value from sentinel, resolved in P-1 — "api_token" or "mcp">
  - mcp_tool_prefix:      <value from connectors.json[notion.mcp_tool_prefix], only if auth_method == "mcp">
```

The orchestrator MUST pass `auth_method` explicitly in the prompt so the agent does not need to infer it from `connectors.json` (which may show `null` in a Routine cold-start). See the read-only note in P-1.

Prompt:
> "Score each candidate using the profile rubric (max_score, criteria, bonuses, exclusion_rules from profile.json — see agents/compile-write.md Step 3 for the algorithm). Apply hard exclusions FIRST (see Step 2). Write qualifying live jobs with `Status: New`. Write uncertains that pass hard exclusions with `Status: Uncertain` (no scoring required for uncertains — user triages in Notion). Mark removed_jobs as Closed. Return newly written jobs (live AND uncertain).
>
> Notion writes: auth_method is **{auth_method}** (passed explicitly — do not re-read from connectors.json). Use scripts/notion-api.py for api_token, or mcp_tool_prefix **{mcp_tool_prefix}** for mcp. On Notion write failure, do NOT fall back to markdown silently — abort and report. The orchestrator decides fallback strategy."

`static_notifications` and `external_companies` are **not** written to the tracker — they're surfaced in the final output only.

**Failure handling — markdown fallback contract:** the orchestrator inspects compile-write's response. The discriminator is the presence of a `fallback_file` key:

- **Success response** (no `fallback_file`): proceed normally to Pass 4.
- **Failure response** (`{"error": ..., "fallback_file": "/tmp/compile-write-failed.json"}`): trigger fallback handling below. See `agents/compile-write.md` § Failure contract for the exact schema.
- **Malformed / missing / unparseable response**: treat as failure with `error="agent_crashed_no_response"`. SKIP the un-poison step (we don't know which IDs failed) and surface a P0 warning ("manually inspect tracker DB and State DB before next run"). DO NOT proceed to Pass 4 — the state file may already be in a corrupt mid-write condition; better to abort the run than risk persisting bad state.

#### Compile-write fallback handler (orchestrator runs this inline)

1. **Read** `/tmp/compile-write-failed.json`. Verify `schema_version == 1`. If unrecognized, fall back to "agent_crashed_no_response" handling above.

2. **Un-poison state** — CRITICAL. Read `/tmp/ai50-state.json` and REMOVE every ID in `failed.get("failed_ats_job_ids", [])` from each company's `jobs` dict. The agent guarantees these are the exact `id` values from the candidates input (NOT URL-parsed), so they match what fetch-and-diff stored. Without this step, every job compile-write failed to write is silently lost forever (Pass 4 would persist it as known-state, Pass 1 of the next run wouldn't surface it as new). The `closed_ats_job_ids` and `removed_jobs_pending` lists are pass-through from the orchestrator's input — no transformation, no echo-fidelity risk. Neither requires state action: closed jobs are already absent (Pass 1 omits them), and pending closures will be re-surfaced by the next run's diff.

   ```python
   try:
       state = json.load(open("/tmp/ai50-state.json"))
   except (FileNotFoundError, json.JSONDecodeError) as e:
       # State file missing or corrupt — surface and abort (don't continue to Pass 4)
       raise RuntimeError(f"State file unreadable during fallback: {e}") from e

   for ats_id in failed.get("failed_ats_job_ids", []):
       for company in state.values():
           if not isinstance(company, dict):
               continue
           jobs = company.get("jobs")
           if isinstance(jobs, dict):  # guard against null / list / corruption
               jobs.pop(ats_id, None)
   json.dump(state, open("/tmp/ai50-state.json", "w"), indent=2)
   ```

3. **Build markdown** from `rows_to_write[].properties` (one row per job: Title, Company, Score, Location, URL, Why Fits, Date Added). At the top, note: *"## Notion tracker write failed — N jobs in fallback below, M jobs already in Notion (rows_already_written), K removed jobs pending Closed."* Default any missing list field to `[]`.

4. **Append "## Closed jobs not yet marked"** listing `removed_jobs_pending` IDs (or "none" if empty).

5. **Write** to `./outputs/<YYYY-MM-DD>-tracker-fallback.md`. On same-day re-runs (file exists), suffix with `-2`, `-3`, …; never overwrite a previous fallback file.

6. **Surface loud warning** in the final run summary: *"⚠️  Notion tracker writes failed — N jobs saved to outputs/<date>-tracker-fallback.md. Error: \<code\> / \<detail\>. Failed IDs unmarked from state — next run will retry. If `removed_jobs_pending` is non-empty: M close-marks pending; investigate."*

7. **Continue to Pass 4** — Pass 4 now persists the un-poisoned state. Pass 5 still runs (notify-hot doesn't depend on tracker writes).

### Pass 4 — Persist state

**After Pass 3, before the hot list**, persist the updated state.

Read `/tmp/ai50-state.json`.

**If `notion.tracker_state_database_id` is set:**

> **History:** v2.1.0 stored job IDs in a single rich_text property and was silently truncated at 2000 chars per company. v2.2.0 moved the IDs into the page body to dodge that limit, but tried to do the writes via a subagent — which stalled on bookkeeping ("how do I pass 4×35 KB chunks without bloating context") and produced zero rows. **v2.2.1 (this version) does the writes inline from the orchestrator** with chunk files prepared deterministically on disk.

#### Step 4.1 — Build chunk files (one Bash call)

Run the helper to convert `/tmp/ai50-state.json` into small per-chunk payload files. Default chunk size is 5; pages have no 2000-char body limit but the *MCP tool result* echoes the page properties for every created row, so smaller chunks keep agent transcripts manageable.

```bash
python3 ./scripts/build-state-chunks.py \
  --state-file /tmp/ai50-state.json \
  --output-dir  /tmp/state-chunks \
  --chunk-size  5 \
  --date        $(date +%Y-%m-%d)
```

The script writes `/tmp/state-chunks/manifest.json` plus `/tmp/state-chunks/chunk-{N}.json`. The manifest lists every chunk's path, row count, and company keys (so you know what's in each chunk without reading it).

Each `chunk-{N}.json` is a JSON array shaped exactly for `notion-create-pages.pages` — properties already expanded (`date:Last checked:start`), body already rendered (fenced ```json code block holding the job-ID array).

#### Step 4.2 — Existing-row map (only on subsequent runs)

If this is **not** the first run, build a `Company key → page_id` map first. Query the data source via `notion-search` with `data_source_url = "collection://<tracker_state_data_source_id>"`. For each existing row, record its page_id. Skip rows for company_keys absent from the state file.

For first runs (state DB empty), no existing-row map needed.

#### Step 4.3 — Write chunks inline (one tool call per chunk)

For each chunk file in `manifest.chunks` (in order):

1. `Read` the chunk file (small — typically 1–10 KB once content is broken across chunk-size=5).
2. **Inline call** `notion-create-pages` (for new rows) or a sequence of `notion-update-page` calls (for existing rows from the map in 4.2). The orchestrator does this in main context — **do NOT delegate to a subagent**, do NOT fan out via parallel tool calls. Sequential, one chunk at a time.
3. From the response, record per row: `page_id`, `Company key`, `Job count`. Discard the rest.

For 50 companies at chunk-size 5 that's 10 sequential tool calls. Acknowledged main-context cost: roughly 5–10 KB per call (chunk content + Notion's response). Total adds ~50–100 KB to the transcript — significantly more than zero, but bounded and predictable, unlike the subagent approach which stalls indeterminately.

For removed companies (key in state DB but absent from `/tmp/ai50-state.json` *and* now `skip` in companies.json): delete the row via `notion-update-page` with archive (or skip — leaving stale rows is harmless because the diff key set is what matters).

The `_meta.notifications` blob can be stored as a row with `Company key = "_meta"` (page body holds the notifications JSON), or skipped — not strictly needed for diff correctness.

#### Step 4.4 — Verify after write

Pick 3 random rows across the chunks (vary chunk index and row count — include at least one row with high `Job count` to catch truncation). For each:

1. `notion-fetch` the page id.
2. Parse the JSON code block from the body.
3. Confirm `len(parsed_array) == Job count` from properties.
4. Set-equality check against `/tmp/ai50-state.json[<company_key>]["jobs"]` keys.

Mismatch = silent truncation; abort the run with a clear error citing the failing key + counts.

**Else (local file backend):**
- Copy `/tmp/ai50-state.json` back to `./state/companies.json`.

Note: profile and custom-companies are **read-only** during a run, even in cloud mode. The user updates them by editing the Notion pages directly between runs (or via the `extend-companies` skill). Do not write profile / custom-companies back to Notion in any pass.

### Pass 5 — Hot List (notify-hot agent)

Invoke **notify-hot** with the newly written jobs from Pass 3 and the resolved hot-list parent page ID:

```
Inputs to pass:
  - Newly written jobs:        /tmp/newly-written.json
  - Static notifications:      from Pass 1
  - External companies:        from Pass 1
  - Hot-list parent page ID:   <cached-ids.hot_list_parent_page_id>
  - Profile thresholds:        from /tmp/profile.json (hot_score_threshold, max_score)
```

Prompt:
> "Create a hot-list digest page under the parent page (ID provided). Filter newly-written jobs to score ≥ hot_score_threshold. Format the digest (per the template in agents/notify-hot.md). Include static_notifications and external_companies as trailing sections.
>
> On Notion-create failure: abort and report; do NOT silently fall back to markdown. The orchestrator handles fallback."

**Empty-run skip (v3.4.0+):** if newly written jobs list is empty AND no static notifications AND no external companies: still invoke notify-hot, but expect it to return `{"hot_matches": 0, "document_created": false, "connector_status": "skipped_empty"}`. Do NOT create a digest page in that case — log "no hot matches this run" inline in the run summary instead. Pre-v3.4.0 behavior was to create an empty digest page every run; that junked the user's Notion with ~52 empty pages/year.

**Failure handling — markdown fallback contract:** if notify-hot's response contains `{"error": ..., "fallback_file": "/tmp/notify-hot-failed.json"}` (see `agents/notify-hot.md` § Failure contract):

1. Read `/tmp/notify-hot-failed.json`. Verify `schema_version == 1`; on unknown version, treat as `agent_crashed_no_response` and skip step 2.
2. Write `digest.body_markdown` verbatim to `./outputs/<YYYY-MM-DD>-hotlist-fallback.md`. On same-day re-runs (file exists), suffix with `-2`, `-3`, … — never overwrite a previous fallback.
3. Surface a loud warning in the final run summary: *"⚠️  Hot-list page creation failed — digest saved to outputs/<date>-hotlist-fallback.md. Error: <code> / <detail>. Investigate before next run."*

**Malformed / missing notify-hot response:** treat as `agent_crashed_no_response`. The hot-list digest for this run is lost (no markdown to write), but the run continues — notify-hot doesn't mutate state, so there's nothing to un-poison. Surface a P0 warning in the run summary.

The fallback writes preserve everything the user would have read in Notion; the run exits 0 so the Routine doesn't reschedule a retry — the sticky summary warning is the human signal to investigate.

### Pass 6 — Feedback recycle (optional, v3.0.3+)

After Pass 5 completes successfully, optionally invoke the **feedback-recycle skill** to process any user-labeled tracker entries (Match Quality + Feedback Comment) since the last cycle. This converts disagreements between LLM verdict and user verdict into anti-patterns + few-shot examples that improve subsequent Pass 3 scoring runs.

**Gating logic:**

```python
# Only run Pass 6 if all conditions are met
should_recycle = (
    deployment_mode == "cloud"  # local users invoke manually when ready
    and profile_has_cv_json     # legacy profiles use structured rubric, no recycle
    and (last_recycle_age_days >= 7 or last_recycle_missing)  # don't recycle every fire
)
```

State tracking:
- `./state/last_recycle.json` is written by feedback-recycle on every successful run with a timestamp.
- Pass 6 reads this file. Missing or older than 7 days → trigger.
- File is gitignored (per-installation, like cached-ids.json).

If gating allows: invoke feedback-recycle skill. The skill self-handles "no new labels" gracefully (prints message and exits without writes), so Pass 6 invocation is safe even when there's nothing to do.

If gating blocks: skip silently. The user can always invoke `recycle feedback` manually when they have new labels.

**Why this is Pass 6, not Pass 5.5 or pre-Pass-1:** the recycle should run AFTER scoring and BEFORE the next fire. Running it within the same Routine context (rather than in a separate local Claude Code session) avoids the `cached-ids.json` drift problem — the cache the Routine just used to write entries is the same cache the recycle reads to find them. No dual-state risk.

**Manual invocation always works** regardless of Pass 6 gating. The user can run `recycle feedback` locally any time; if local cached-ids drifts from cloud, the skill's defensive `discover` (Step 1) handles it.

## Output

```
## AI 50 Job Search — {date}

Fetch: {N} companies checked | {N} skipped | {N} errored
Total jobs in ATS: {N} | New this run: {N} raw → {N} after filter
Validation: {N} live confirmed (of {N} checked)
Tracker: {N} new entries written | {N} marked closed
State: {Notion DB ✓ | local file ✓}

🔥 Hot matches ({N} at score ≥ {threshold} OR Match: High in v3 path):
  • {Score or Match} — {Company}: {Title} [{location}]

Hot list: {Notion page URL or file path}
Tracker connector: {connected / fallback}

━━━ Token usage ━━━
Pass 3 (compile-write):     {input}K input ({cache_read}K cached), {output}K output  | model: {model}{ + thinking budget}
Pass 5 (notify-hot):        {input}K input, {output}K output                            | model: {model}
Pass 6 (feedback-recycle):  {input}K input, {output}K output                            | model: {model}
                            (or "skipped — gate not met: last cycle {N} days ago")
─────
Total:    {total_input}K input ({total_cache_read}K cached), {total_output}K output
Estimated cost: ${X.XX} ({per-pass breakdown if multi-model})

If no LLM calls were made this run (e.g. zero candidates, all hard-excluded), print "Token usage: no LLM calls this run" instead of the breakdown above.

Static-roles notifications (low-confidence, not saved to tracker):
  • {Company}: {Title} — {one-line role description}

External companies (no scrapeable endpoint — check 3rd-party source):
  • {Company}: {external_source} → {external_url}

Permanently skipped:
  {names with ats=skip}
```

If zero qualifying roles this run, say so explicitly. Don't pad.

### Token + cost aggregation (v3.0.5+)

The orchestrator collects `usage` objects from each pass's response envelope (compile-write returns it after Pass 3; notify-hot after Pass 5; feedback-recycle after Pass 6 if invoked). Aggregate the totals; compute estimated cost using Anthropic's published rates:

| Model | Input ($/Mtok) | Output ($/Mtok) | Cache read ($/Mtok) |
|---|---|---|---|
| `claude-opus-4-7` | 15 | 75 | 1.50 |
| `claude-sonnet-4-6` | 3 | 15 | 0.30 |
| `claude-haiku-4-5-20251001` | 0.80 | 4 | 0.08 |

Cost formula per pass: `(input_tokens - cache_read_input_tokens) × input_rate + cache_read_input_tokens × cache_rate + output_tokens × output_rate + (thinking_tokens, if any) × output_rate`. Use whichever model the pass actually invoked.

Sum across passes for total run cost. Round to 2 decimal places in display.

If a pass returns `usage: null` (e.g. notify-hot in legacy path that just renders templates without LLM calls): omit it from the breakdown and skip it in the aggregate.

If `usage` is missing entirely from a pass response (older agent format): omit that pass's row entirely from the display rather than printing partial/garbled output. Don't print version stamps inline — they confuse the user-facing summary.

If ALL passes have null/missing usage (e.g. early-exit run with no LLM activity), print one line: `Token usage: no LLM calls this run` and omit the table.

---

## First-time setup

Setup is a one-time interactive flow. From inside the cloned repo, run `claude` and then say `"set up the plugin"` — the wizard handles profile collection, Notion auth, and database creation.

For Cloud Routine setup (allowed domains, env vars, setup script, schedule, trigger prompt) see **[INSTALL.md §3](../../../INSTALL.md)**. The orchestrator's runtime contract for Routine context is:

```
Run the AI 50 job search.

Routine context (no human in the loop):
- Auth: use NOTION_API_TOKEN from the environment.
- Config: artifact NAMES in connectors.json[notion.names] are authoritative; IDs
  are resolved at run time via notion-api.py discover (see Step P-3).
- Setup sentinel was created by the Routine setup script. Do NOT trigger the
  setup wizard.
- Do not ask any interactive questions. If something is ambiguous, pick the
  documented default. If genuinely blocked, fail loudly and exit non-zero.

Then execute the run-job-search skill end-to-end and print the canonical run summary.
```

**State DB requirement:** the State DB MUST exist (created by setup, name from `connectors.json[notion.names.state_db]`). Without it, a Routine starts from empty state on every cold start and re-writes every job as new. The Step P-3 discover flow recreates an empty State DB shell if missing — but on first miss the run produces a "no removed jobs" report and over-reports new jobs. Set up properly the first time and you won't see this.
