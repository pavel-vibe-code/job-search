# Changelog

All notable changes to the AI 50 Job Search plugin. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses semantic-ish versioning (MAJOR.MINOR.PATCH where PATCH bumps land alongside in-place edits to the v2.2.0 plugin folder).

---

## [2.5.0] — 2026-05-03

### Validator URL-dispatch (fixes name-based "uncertain" misclassifications)

`scripts/validate-jobs.py` was the source of a real bug surfaced by the v2.4.0 cloud-routine fire: companies whose listings had unambiguously-Greenhouse or unambiguously-Ashby URLs (e.g. `job-boards.greenhouse.io/<co>/...`) were being marked uncertain because the validator only did **name-index lookup** (companies.json + favorites.json by lowercased company name). If the entry was missing or had `ats: "skip"`, the listing got dropped to uncertain even though the URL itself revealed the ATS.

- **New** — `ats_from_url()` in `validate-jobs.py`. Regex matches against known ATS host patterns (`jobs.ashbyhq.com`, `job-boards.ashbyhq.com`, `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`, `www.comeet.com/jobs/...`). Returns `(ats, slug)` deterministically. Used as the **primary** dispatch signal; name-index lookup is the fallback.
- **Fixed** — companies/favorites precedence inconsistency. Pre-v2.5.0, `validate-jobs.py` had favorites overriding companies (last-writer-wins), while `fetch-and-diff.py` had companies overriding favorites (companies-wins). Same data, opposite behavior. Unified to **companies-wins** in both scripts.
- **Improved** — uncertain reason codes split. Was a single string `no_api_for_ats_or_company_unknown` conflating two distinct failure modes. Now: `company_name_not_in_index` (data-hygiene problem in user's favorites) vs `ats_unsupported:<value>` (code limitation; e.g. lever, workable, personio). Each candidate's `url` field is also preserved in the uncertain output for diagnostics.
- **New** — 14 unit tests in `tests/test_validate_jobs.py` pinning URL-pattern behavior across both classic and new-subdomain forms (`jobs.ashbyhq.com` AND `job-boards.ashbyhq.com`, `boards.greenhouse.io` AND `job-boards.greenhouse.io`).

### Favorites schema gains `careers_url` field

`config/favorites.json` entries can now carry an optional `careers_url` field. When present, `validate-favorites.py` derives ATS+slug deterministically from the URL, bypassing the slow slug-variant-probing loop entirely. Faster, miss-proof, forward-compatible (URL preserved even for ATS types not yet supported by the fetcher — when support is added later, no reconfiguration needed).

The wizard rewrite in v2.5.1 will make this the default capture mechanism: user provides URL when adding favorites; ATS detected immediately.

```json
{
  "name": "Together AI",
  "slug": "togetherai",
  "ats": "greenhouse",
  "careers_url": "https://job-boards.greenhouse.io/togetherai",
  "source": "user_added"
}
```

### Typed `hard_exclusions` schema defined (consumers in v2.5.1)

`profile.json` may now include a `hard_exclusions` block — typed exclusion rules that downstream code applies deterministically at Pass 1, before any LLM scoring. Schema documented in `ARCHITECTURE.md` §7.2. Five rule types defined:

- `country_lock` — `{reject_outside: ["EU", "Czech Republic"]}`
- `language_required` — `{user_languages: [...], reject_if_other_required: true}`
- `title_pattern` — `{reject_if_contains: [...], unless_also_contains: [...]}`
- `seniority_floor` — `{minimum_level: "senior_ic"}`
- `remote_country_lock` — `{eligible_remote_regions: ["EU"]}` (reject "Remote — US only" style listings)

Code consumers (fetch-and-diff and compile-write reading the typed block) ship in v2.5.1 alongside the wizard rewrite that generates them. v2.5.0 ships only the schema + documentation. Existing profiles without `hard_exclusions` continue to use legacy `exclusion_rules` free-text — full backward compatibility.

### Versions

- Plugin: 2.4.0 → 2.5.0
- Tests: 150 → 164

---

## [2.4.0] — 2026-05-01

### Project-scoped layout (skills auto-register in cloud Routines)

First successful Cloud Routine fire (v2.3.4 environment) revealed the agent reporting `Unknown skill: run-job-search` and improvising the pipeline by reading `SKILL.md` as English prose. Root cause: Cloud Routines do not enable plugins from cloned repos — they only auto-discover skills at three locations: `~/.claude/skills/`, `.claude/skills/` (project-scoped), or a plugin's `skills/` directory **when the plugin is explicitly enabled**. v2.3.x shipped as a plugin (skills at `<repo>/skills/`), so cloud auto-discovery never registered them.

This release moves to **project-scoped layout** — skills + agents live under `.claude/`, and the project's working directory IS the discovery root. Cloud Routines clone the repo, the agent runtime starts with cwd = repo root, and `.claude/skills/` registers automatically. Plugin manifest is dropped.

- **Moved** — `skills/` → `.claude/skills/`, `agents/` → `.claude/agents/`. Standard project-scoped paths.
- **Removed** — `.claude-plugin/plugin.json` and the entire `.claude-plugin/` directory. The repo is no longer a "plugin"; it's a project that ships skills + agents directly. Version tracking moves to git tags + CHANGELOG (no in-repo `version` field).
- **Replaced** — `${CLAUDE_PLUGIN_ROOT}` references throughout `.claude/skills/*/SKILL.md`, `.claude/agents/*.md`, and `config/connectors.json`. Was undefined in cloud Routine context anyway (only set when plugin loads via `--plugin-dir`). Switched to relative paths (e.g. `./scripts/notion-api.py`) which work anywhere `cwd` is the repo root — both the cloud Routine container and a local `cd job-search && claude` session.

### Personal config files removed from Git

`config/profile.json` and `config/favorites.json` were tracked in Git, which v2.3.4's first Routine run revealed was a real leak vector — the cloud agent improvised by bootstrapping Notion from these local files instead of honoring the `abort_if_missing` policy. Even on a private repo, having user-curated content in git history is a hygiene problem; for any future public flip, it's a hard blocker.

- **Untracked** — `git rm --cached config/profile.json config/favorites.json`. Files remain on disk; just no longer tracked.
- **Added to `.gitignore`** — `config/profile.json` and `config/favorites.json`. Future wizard writes (in local mode) won't accidentally land in commit history.
- **Architectural rule** — these files are EITHER absent (cloud mode, profile lives in Notion) OR locally-edited and gitignored (local mode). Never in Git, even as samples.

### Install flow simplified

The local install incantation drops `--plugin-dir`:

```bash
# v2.3.x and earlier
git clone <url> && cd job-search && claude --plugin-dir .

# v2.4.0
git clone <url> && cd job-search && claude
```

The Routine UI's `Plugin` field — which we documented in §3.3 of v2.3.2 INSTALL.md — was always wishful thinking; the official Routines UI has no such field. Removed the row.

### Versions

- Plugin: 2.3.4 → 2.4.0 (minor bump — directory layout change is more than a bugfix)
- Plugin manifest: removed; version now lives in git tags + this CHANGELOG only

### Migration notes (v2.3.x → v2.4.0)

If you have a v2.3.x clone:

```bash
cd /path/to/job-search
git pull origin main
# Skills+agents are now under .claude/
# Your local config/profile.json + config/favorites.json (if any) survive — they're gitignored now
# Re-open Claude Code from this directory: just `claude` (no --plugin-dir)
```

If you have a Cloud Routine running v2.3.x: nothing to do beyond the next fire — the Routine clones the latest `main` automatically and the new layout works.

---

## [2.3.4] — 2026-05-01

### Setup script no longer attempts auth pre-check (can't work in pre-init context)

Second test-fire of the Cloud Routine got past the line-continuation fix from v2.3.3 but failed with `{"error": "no_token"}` from `notion-api.py users-me`. Root cause: the Routine's setup-script context runs **before** the agent runtime is loaded, and **does not see custom environment variables** — only Claude-cloud and system vars. So the `python3 ... users-me` auth pre-check could never have worked from the setup script regardless of token validity.

The auth pre-check belongs in the agent context (where env vars ARE visible). Removing it from setup means a malformed token now surfaces a few seconds later (at the orchestrator's first Notion call) instead of in the setup phase — same failure mode, slightly less fast-fail. Acceptable given that the setup-context restriction makes the cleaner pre-check impossible.

- **Fixed** — `INSTALL.md` §3.2c setup script: removed the `python3 "$NOTION_API" users-me ...` line. Setup now only writes the `state/.setup_complete` sentinel and exits.
- **New** — `INSTALL.md` §3.0 ("How a Routine actually runs") now explicitly lists this as a Routine-architectural quirk: setup-script context vs. agent runtime context have different env-var visibility, and custom env vars are only available in the latter.
- **New** — `INSTALL.md` §3.2c warning callout: do NOT invoke anything that needs `NOTION_API_TOKEN` from the setup script; it will always fail with `no_token`.
- **Updated** — `skills/run-job-search/SKILL.md` setup-script reference matches.

### Versions

- Plugin: 2.3.3 → 2.3.4

---

## [2.3.3] — 2026-05-01

### Setup script fix — backslash line-continuation breaks in Routine UI

First test-fire of the Cloud Routine failed with exit 127 ("command not found: 2026-05-01"). Diagnosis: the setup script's `printf ... \` continuation line in the Routine UI's text-input field had its trailing backslash stripped, causing bash to treat the date-substitution line as its own command.

- **Fixed** — `INSTALL.md` §3.2c and `skills/run-job-search/SKILL.md` setup script: extracted `DATE=$(date +%Y-%m-%d)` to its own line; collapsed the `printf` to a single line (no `\` continuation). Behaviour identical when it works; reliable in web text inputs.
- **New** — explanatory note in INSTALL.md §3.2c about why backslash continuations are fragile in routine setup scripts (web inputs may normalize whitespace / line endings). Same advice applies to any user customisations.

### Versions

- Plugin: 2.3.2 → 2.3.3

---

## [2.3.2] — 2026-05-01

### Two-persona INSTALL flow + Routine-clone concept doc

The `Path A (local) → Path B (cloud)` install split conflated two orthogonal axes: deployment mode and user persona. Restructured around the persona axis (Quick vs Advanced) so both deployment modes work for both personas. Plus filled in three Routine-clone concept gaps that v2.3.0/v2.3.1 INSTALL.md left implicit.

- **Restructured** — `INSTALL.md` is now: §1 source acquisition (Quick: clone upstream / Advanced: fork+clone+upstream-remote), §2 install + Notion setup (shared), §3 Cloud Routine (shared, Repository field differs), §4 stay-in-sync with upstream (Quick: `git pull`; Advanced: `git fetch upstream && git merge && git push`).
- **New** — §3.0 "How a Routine actually runs" — concept block at the top of §3 explaining: clones-fresh-each-run, GitHub access prereq, public-vs-private-repo behaviour, what persists across runs vs not (only Notion), where the permission allowlist comes from. Closes the conceptual gap that left users guessing whether code changes need a "deploy" step (they don't — push to repo, next run picks it up).
- **New** — Repository field documented in §3.3 Routine-create table. Previously the table jumped Trigger/Schedule/Plugin/Environment without ever telling the user which GitHub repo gets cloned.
- **New** — `/web-setup` pointer for users whose claude.ai account isn't GitHub-connected.
- **New** — Troubleshooting entry for "Routine clone fails with 404" (private-repo auth diagnosis).
- **Updated** — Quick path's `git clone` URL is `pavel-vibe-code/job-search` (no longer a `<owner>` placeholder).

### Plugin renamed to match repo

The plugin's technical identifier in `.claude-plugin/plugin.json` was `ai50-job-search` while the repo on GitHub is `job-search`. The discrepancy was harmless functionally but cosmetically confusing in the Routine UI's Plugin field and the install instructions.

- **Renamed** — `plugin.json["name"]`: `ai50-job-search` → `job-search`. Display name "AI 50 Job Search" (in README, ARCHITECTURE, branding) is unchanged — only the technical identifier moved.
- **Updated** — `skills/run-job-search/SKILL.md` references to the plugin name (was `ai50-job-search-code` — also dropped the stale `-code` suffix carried over from a pre-v2.3 working folder).
- **Updated** — `README.md` Quick start clone URL + version refs (v2.3.0 → v2.3.2).
- **Not changed** — `~/.config/ai50-job-search/notion-token` filesystem path. Renaming would force users to migrate existing token files; the plugin-name discrepancy from the path is acceptable since the path is internal to the user's machine.
- **Not changed** — Python scripts' `User-Agent` strings (`ai50-job-search/X.Y`). Cosmetic, low-impact, deferred.

### Versions

- Plugin: 2.3.1 → 2.3.2

---

## [2.3.1] — 2026-05-01

### Ship `.claude/settings.json` for Routine support

v2.3.0's CHANGELOG and INSTALL §B.1 stated that plugins "cannot ship `.claude/settings.json` permissions" and required users to write their own allowlist. That was based on a partial reading of Claude Code permissions — accurate for plugins installed into a *foreign* project directory, but wrong for the canonical Path A flow (clone + `cd` + `claude --plugin-dir .`) and for Cloud Routines (which clone the repo and apply its `.claude/settings.json` automatically — see [code.claude.com/docs/en/routines](https://code.claude.com/docs/en/routines.md)).

- **New** — `.claude/settings.json` committed at the repo root with the canonical allowlist for the Routine execution path:
  - `Bash(python3 */scripts/{notion-api,fetch-and-diff,validate-jobs,build-state-chunks}.py *)` — the four scripts the orchestrator + agents invoke at run time. `*/scripts/...` rather than an absolute path because `${CLAUDE_PLUGIN_ROOT}` does not expand inside `Bash()` permission patterns; wildcard is the portable form across local CWDs and Routine container paths.
  - `Bash(mkdir -p *)`, `Bash(date *)` — shell utilities the orchestrator's setup scaffolding uses.
  - `Read(**/config/**)`, `Read(**/state/**)`, `Read(**/scripts/schemas/**)`, `Read(/tmp/**)` — config files, state files, schema files, inter-pass scratch.
  - `Write(**/state/**)`, `Write(**/outputs/**)`, `Write(/tmp/**)` — state DB, fallback markdown, scratch.
  - `Edit(**/state/**)`, `Edit(**/outputs/**)`, `Edit(/tmp/**)` — same scopes for in-place edits.
- **Updated** — `INSTALL.md` §B.1 reflects the shipped settings.json. Local Path A and Routine Path B both work without manual permission setup. Foreign-CWD installs (marketplace, not yet active) noted as the one case where users still copy rules into their own project's settings.json.
- **Updated** — `skills/run-job-search/SKILL.md` § Routine setup — replaced the stale "can't be shipped with the plugin" note with a pointer to the committed `.claude/settings.json`.

### What's NOT included

- No `Bash(python3 */scripts/validate-favorites.py *)` — invoked only by the setup wizard skill, which Routines never run (sentinel skips it).
- No `Bash(python3 */scripts/detect-notion-mcp.py *)` — same, setup-only.
- No `WebFetch(domain:...)` rules — network egress to ATS/Notion happens inside the Python scripts via `urllib`, not via the WebFetch tool. Domain allowlisting is handled at the Routine UI's "Allowed domains" field (network layer), not at the Claude Code permissions layer.
- No `mcp__*` rules — Routines run in `auth_method=api_token` mode (no Notion MCP). MCP tools are only used by the local `auth_method=mcp` path.
- No `AskUserQuestion` — would prompt and break unattended execution.

### Versions

- Plugin: 2.3.0 → 2.3.1

---

## [2.3.0] — 2026-04-30

### Architecture refactor — community-distribution-ready

Per-user Notion IDs no longer live in the repo. Names live in `connectors.json[notion.names]` (shipped, generic); IDs auto-resolve via `discover` at run time. Community users no longer fork or commit personal IDs.

- **New** — `scripts/notion-api.py discover` subcommand. Cache-first → fall through to name search → list parent's children. Self-healing: `recreate_ok` artifacts (parent / tracker DB / state DB / hot-list page) are recreated on miss; `abort_if_missing` artifacts (profile / favorites pages) emit a loud error directing the user to re-run setup.
- **New** — `state/cached-ids.json` (gitignored). Per-user ID cache regenerated by discover. Includes `_workspace_id` sanity check — token rotation across workspaces invalidates the cache automatically.
- **New** — `scripts/schemas/{tracker_db,state_db}.json` — single source of truth for Notion DB schemas, used by both setup wizard and the orchestrator's recreate path. Replaces inline DDL pseudocode that previously drifted between files. `cmd_create_database` now strips `_comment` keys before sending to the API.
- **New** — `NOTION_PARENT_ANCHOR_ID` env-var convention for non-interactive parent-page recreation in Cloud Routines.

### Setup wizard rewrite

- Removed "Setup mode" question (always guided).
- Q2/Q3 restructured into "open to relocation?" + free-form "preferred work mode + nuances" — no more mutually-non-exclusive choices.
- Q6 (languages) flipped from "languages to penalize" to "languages I speak" — hard exclusion for jobs requiring unspoken languages, no soft penalty.
- New scoring-rubric flow: user describes criteria + priority (high/medium/low) in plain English; agent reflects and proposes weights + thresholds; user approves / adjusts / re-thinks. Replaces the prior 6-criterion default rubric.
- Removed the connector-type question — Notion is the only fully-supported connector. Markdown is now an automatic fallback the orchestrator drives on Notion write failures (not a user choice).
- **Wizard hygiene** — `excluded_countries` must contain only canonical country names. No more meta-phrases like `"all non-EU"` (those break `classify_region` — see filter bugs below).

### Markdown-fallback contract (was unwired)

Previously the orchestrator referenced "the failed-rows JSON the agent left behind" but agents only said "abort and report" without specifying a file format. The fallback was dead code.

- **New** — `agents/{compile-write,notify-hot}.md` § Failure contract — explicit JSON schema for `/tmp/{compile-write,notify-hot}-failed.json` (schema_version, error code, failed_at, failed_ats_job_ids that must be the exact `id` field from candidates input, rows_to_write, rows_already_written, removed_jobs_pending, etc.).
- **New** — orchestrator un-poisons state on compile-write failure: removes failed_ats_job_ids from `/tmp/ai50-state.json` before Pass 4 persists, preventing future runs from treating failed-but-not-written jobs as "seen" and silently dropping them forever.
- Filename collision policy: same-day re-runs append `-2`, `-3` suffix to fallback files; never overwrite.
- Malformed/missing agent response handled as `agent_crashed_no_response` — orchestrator skips the un-poison step (since failed IDs are unknown) and surfaces a P0 warning rather than continuing with potentially-corrupt state.

### Filter bugs (carried over from v2.2.x — fixed)

- **Bug 1** — `classify_region` falsely matched "EU" inside "all non-EU" (hyphen is a regex word boundary). Wizard-emitted meta-phrases would silently classify the candidate's home region as excluded, zeroing out remote scores. Fixed: defensive guard against negation prefixes ("non-", "not ", "no ", "exclud-"). Wizard hygiene also tightened.
- **Bug 2** — `build_score_table` gave hybrid jobs in `eligible_regions` (but not in `home_region`) a score of 0, so a candidate open to EU relocation would never see "Hybrid Berlin / Paris / Munich" roles even though they're explicitly EU-relocation-friendly. Onsite already had a relocation downgrade path scoring 1; hybrid now matches.
- **Real impact verified** — for a Lisbon-based Senior PM persona open to EU relocation, the candidate funnel went from 2 → 7 (3.5×) on a real fetch across all 50 AI companies.

### Compile-write agent fixes

- Tracker schema in agent now matches what the wizard creates exactly. Earlier versions wrote to `Job Title / Fit Score / Job URL / Discovered / Brief Description`; wizard creates `Title / Score / URL / Date Added / Department / Source / Why Fits` and no `Brief Description`. Schema mismatch silently lost properties on every prior run.
- Removed `language_requirement` penalty path (replaced by hard-exclusion in the new wizard's spoken_languages model).
- Reads `scoring.criteria` + `scoring.bonuses` (new schema). The runtime no longer falls back to a built-in default rubric — `profile.json` is the single source of truth.

### Permissions

- Plugin cannot ship `.claude/settings.json` permissions (per Claude Code docs); users add their own.
- Recommended allowlist documented in `skills/run-job-search/SKILL.md` § Routine setup, using `*/scripts/<name>.py *` wildcard form (works for both local testing and cloud Routine paths since `${CLAUDE_PLUGIN_ROOT}` does NOT expand inside Bash() permission patterns).

### Versions

- Plugin: 2.2.2 → 2.3.0
- run-job-search skill: 2.2.0-code → 2.3.0
- setup skill: 2.1.0 (mid-cycle) → 2.3.0
- validate-favorites skill: 1.0.0 (unchanged)

### Migration notes (v2.2.x → v2.3.0)

- `connectors.json[notion.tracker_database_id]` and the other ID fields are no longer read by the runtime. They can be left in place (harmless) or removed. The runtime reads from `state/cached-ids.json`, auto-populated by the first `discover` call.
- Existing users running the search will see one extra ~2-second delay on the first run (cold-cache discover). Subsequent runs hit the cache and resolve in <1 second.
- Cloud Routine users: add `NOTION_PARENT_ANCHOR_ID` to the Routine env config as a safety net. Without it, missing-parent-page situations abort the run; with it, the runtime auto-recreates under the anchor.

---

## [2.2.2] — 2026-04-29

Distribution-readiness pass. v2.2.1 surfaced a Staff-level review identifying twelve issues; this release ships fixes for all of them. The plugin is now Routine-compatible (via the API-token auth path) and ships as a clean template for new users.

### Added

- **`scripts/notion-api.py`** (~700 lines) — Notion REST API helper enabling the `auth_method = "api_token"` path. Subcommands: `users-me`, `search`, `create-database`, `create-pages`, `update-page`, `fetch-page`, `fetch-page-body`, `query-database`, `delete-page`, `hydrate-state`. Body content auto-splits across rich_text elements within a single code block to support state arrays > 2000 chars. Token resolution order: `--token` flag → `NOTION_API_TOKEN` env var → `~/.config/ai50-job-search/notion-token` file. Used by Cloud Routines (where MCP is unavailable) and as an alternate path for local users who prefer integration-token auth.
- **`hydrate-state` subcommand in notion-api.py** — parallel-fetches all state-DB row bodies in one Bash call. Cuts cloud-mode hydration from ~5 minutes (50 serial MCP fetches) to ~5 seconds.
- **Auth-method fork in setup wizard** (`skills/setup/SKILL.md` Step 5-pre + Step 5a-token) — user picks "MCP" (interactive) or "API token" (Routines). Token path walks through minting at notion.so/profile/integrations, validating via `users-me`, persisting to file with chmod 0600, plus the per-page Connections grant that Notion integrations require.
- **`notion_call` dispatch abstraction** in `agents/compile-write.md` and `agents/notify-hot.md` — agents now read `connectors.json[notion.auth_method]` and dispatch to MCP tools (using resolved prefix) or to `scripts/notion-api.py` Bash invocations. Single agent prompt covers both transports.
- **Adaptive chunking** in `build-state-chunks.py` — companies with > `--big-row-threshold` jobs (default 200) get their own chunk, smaller companies batch at `--small-chunk-size` (default 5). Manifest reports `kind: big | small` per chunk. Eliminates the 25k-token Read-tool overflow that hit Databricks (829), OpenAI (651), Anthropic (453) in v2.2.1.
- **`build_score_table()`** in `fetch-and-diff.py` — score-remote table parameterised on home region + eligible/excluded sets derived from profile.json. Replaces the hardcoded PRAGUE-as-privileged-region table.
- **Test classes for non-Prague home regions** in `tests/test_region.py`: `ScoreTableBerlinTests`, `ScoreTableNYCTests`, `ScoreTableUnknownHomeTests`. Verifies the parameterised table behaves correctly for any home region.
- **`--description-limit N`** flag in `fetch-and-diff.py` — default 600 chars (was hardcoded 2500). Keeps a 49-candidate output under ~40 KB so downstream agents can Read the file without hitting the 25k-token tool limit.
- **`filtered_out` field** in search-roles' agent output — distinct from `removed_jobs` (the script's diff output). Eliminates the v2.2.1 confusion where filter-rejected jobs were occasionally treated as ATS-disappeared and marked Closed.

### Changed

- **`config/connectors.json`** — reset to template state. All Pavel's database/page IDs removed; replaced with `SETUP_REQUIRED` / `null`. New fields: `auth_method` (mcp / api_token / null), `api_token_env_var`, `api_token_file`. Now ships as a true empty template.
- **`config/profile.json`** — reset to the Berlin-based sample (was Pavel's actual profile after the v2.2.1 e2e). New users get a generic template they can edit during setup.
- **`scripts/fetch-and-diff.py` `score_remote()`** — now accepts optional `home_region`, `eligible_regions`, `excluded_regions` kwargs for explicit overrides (used by tests). Module-level defaults still load from `PROFILE_FILE` at import.
- **`agents/search-roles.md` Step 3c** — rewrote the score-remote prose to describe the parameterised table with three worked examples (Prague, Berlin, NYC). Removed the "PRAGUE = home" implicit assumption.
- **`agents/compile-write.md` Step 2** — deleted the inline 8-point rubric. Agent now reads `profile.json[scoring.criteria]` as the source of truth, applies user-configured weights and penalties.
- **`skills/run-job-search/SKILL.md` Pass 2 hydration** — branches on `auth_method`. API-token mode uses `notion-api.py hydrate-state`. MCP mode uses parallel-dispatched MCP fetches in batches of ≤10 per message (instead of serial 1-by-1).
- **`settings.json`** — added allowlist entries for `validate-jobs.py`, `build-state-chunks.py`, `detect-notion-mcp.py`, `notion-api.py`, `claude mcp list`. Without these, Routines would prompt for permission per script invocation and fail unattended.
- **`.claude-plugin/plugin.json`** — version bumped to 2.2.2. Description rewritten to honestly describe the dual auth-method support; removed misleading "Two deployment modes" framing that implied MCP-mode Routines worked (they don't).

### Fixed

- **PRAGUE hardcoded as privileged region.** v2.2.1 baked a Prague-centric score table into `fetch-and-diff.py` and `agents/search-roles.md`. A Berlin- or NYC-based candidate would have their home location score 1 or 2 instead of 3, missing the obvious "this role is in your city" signal. Parameterised by candidate location read from `profile.json`.
- **Plugin shipped with developer's personal data.** `connectors.json` had Pavel's Notion database IDs; `profile.json` had Pavel's actual profile; `state/.setup_complete` skipped the wizard. Anyone installing v2.2.1 fork would write to Pavel's workspace. Reverted to template state.
- **Permissions allowlist incomplete for Routines.** `settings.json` listed only 2 of the 6 plugin scripts. Routines hit a permission prompt on every invocation of the others and stalled. Allowlist now covers all helpers.
- **Read-tool 25k-token overflow on outlier companies.** Databricks (829 jobs) → ~26KB chunk file → exceeded Read limit, agents had to manually split. Adaptive chunking + 600-char description default eliminate the overflow.
- **Cloud Routines materially incompatible.** `plugin.json` claimed dual deployment modes, but MCP-mode Routines fail because Routine containers have no IDE-side Notion MCP. v2.2.2 ships the API-token path that actually works in Routines.
- **`removed_jobs` semantic conflation.** v2.2.1 search-roles output reused `removed_jobs` for both "diff-disappeared" (script output) and "filter-rejected" (agent's filter step). The latter would cause compile-write to mark thousands of live listings as Closed. Renamed filter-rejected to `filtered_out`; reserved `removed_jobs` strictly for the script's authoritative diff array.

### Removed

- **Hardcoded `PRAGUE` references** throughout the codebase (now resolved via profile).
- **Pavel's Notion database IDs** from `connectors.json`.
- **`outputs/tracker-delta-2026-04-28.md`** stray e2e artefact.

### Documented

- Inline-rubric vs profile-driven-rubric history annotated in `agents/compile-write.md`.
- Score-table parameterisation history in `tests/test_region.py` module docstring.
- Dispatch-abstraction history in `agents/compile-write.md` + `agents/notify-hot.md` Tool discipline sections.

### Known limitations

- **Cloud Routine path requires manual setup-on-laptop first.** The Routine container can't run the setup wizard interactively — the user has to complete onboarding locally (which writes the token file) and then export the token to a Routine env var. v2.3 may explore Routine-side first-time setup but it's not a quick fix.
- **Notion integration tokens require per-page Connections grants.** This is a Notion product behaviour, not a plugin issue, but it's the most common reason API calls fail with `object_not_found`. The setup wizard's Step 5a-token walks through it but users still miss it sometimes.
- **MCP path UUID still rotates on reconnect.** The orchestrator re-probes at run start, but a mid-pipeline reconnect could still cause a single pass to fail. Mitigation: API-token mode for any unattended use case.

---

## [2.2.1] — 2026-04-28

End-to-end testing of v2.2.0 surfaced four real bugs and several design issues. v2.2.1 ships fixes for the bugs that block normal usage; remaining items are tracked in `BACKLOG.md`.

### Added

- **`scripts/validate-jobs.py`** — API-based candidate validator. Replaces the v2.2.0 WebFetch + HTML closure-signal approach with direct calls to each ATS's posting API (Ashby, Greenhouse, Comeet). Groups candidates by `(ats, slug)`, makes one parallelised API call per group, tests each candidate's ID against the active set. ~10s for typical 49-candidate runs.
- **`scripts/build-state-chunks.py`** — deterministic chunk builder for Pass 4 state-DB persistence. Reads `/tmp/ai50-state.json`, produces small per-chunk page payloads on disk + a manifest. Replaces the v2.2.0 subagent-orchestrated approach that stalled at the watchdog timeout.
- **`scripts/detect-notion-mcp.py`** — CLI fallback for Notion MCP detection during setup. Parses `claude mcp list` output to capture the resolved server name and infer install method (CLI vs. UUID-based connector).
- **Notion MCP detection cascade** in the setup wizard (`skills/setup/SKILL.md` Step 5a). Four-step probe: live ToolSearch → CLI registry → auto-install offer → manual fallback. Catches both CLI-installed and connector-installed Notion before any database creation.
- **Run-start prefix re-probe** in the orchestrator (`skills/run-job-search/SKILL.md` pre-flight step 3). Connector-installed Notion UUIDs can rotate on reconnect; the orchestrator now refreshes `connectors.json[notion.mcp_tool_prefix]` at the start of every run if it has changed.
- **`BACKLOG.md`** documenting v2.2.2 / v2.3 work items with rationale.
- **`CHANGELOG.md`** (this file).

### Changed

- **`agents/search-roles.md`** — Step 1 ("Run the fetcher") now includes:
  - The script command matches what the orchestrator passes (`--state-file`), instead of conflicting with it.
  - An explicit "**run exactly once**" guard with rationale: the script is destructively stateful, and a second run with now-populated state will report `new_jobs: 0` (which looks like a bug but isn't).
  - Disambiguation between `stats.new_jobs` (unfiltered diff, can be thousands on first run) and `candidates` (Step 3's filtered output). The summary should say *"5,045 new jobs from diff → N candidates after profile filter"*, never *"5,045 new jobs after filter"*.
- **`agents/validate-urls.md`** — completely rewritten. Tools changed from `["WebFetch"]` to `["Bash", "Read"]`. Prompt now invokes `validate-jobs.py` instead of fetching pages and looking for closure phrases. Documents why API-based validation is necessary (SPA-rendered ATS like Ashby return empty shells to non-JS clients). Explains the four failure modes the script handles.
- **`agents/compile-write.md`** — removed the hardcoded `tools: [..., "mcp__notion__*", ...]` allowlist (which silently broke under UUID-prefixed connector installs). Added a "Tool discipline" section that:
  - Inherits parent's full tool set so any Notion server-id works.
  - Names the resolved prefix from `connectors.json` as the source of truth.
  - Explicitly enumerates allowed tools (Read/Write/Bash + 4 Notion tool suffixes) and forbidden ones (Agent, WebFetch, Edit, non-Notion MCPs, Notion's admin/curation tools).
  - Mandates abort-and-report rather than silent markdown fallback on tool errors.
- **`agents/notify-hot.md`** — same shape as compile-write, with an extra-strict ban on side-channel notification tools (Slack, Email, Calendar, Discord, SMS, GitHub Issues, Linear tickets) even if connected. The word "notify" should not become a license to broadcast.
- **`skills/run-job-search/SKILL.md`** Pass 2 — orchestrator now writes candidates to a file path and passes the path to validate-urls (instead of inlining the JSON), since the agent uses Bash-driven validation.
- **`skills/run-job-search/SKILL.md`** Pass 4 — rewritten as the v2.2.1 inline-write flow. Pseudo-code:
  1. Run `build-state-chunks.py` to produce per-chunk payload files.
  2. Build existing-row map (only on subsequent runs) via `notion-search`.
  3. Sequential, in-orchestrator-context `notion-create-pages` (or `notion-update-page`) calls — one per chunk. Subagent delegation explicitly forbidden.
  4. Verify by sampling 3 random rows; abort on mismatch.
  Acknowledged context cost (~50–100KB transcript bloat for 50 rows) is the tradeoff for predictable, non-stalling execution.
- **`config/connectors.json`** — schema additions:
  - `notion.install_method` ("cli" / "connector" / null) — set by setup wizard.
  - `notion.mcp_tool_prefix` — now dynamically resolved at setup + re-probed at run start, not hardcoded. Comments updated to reflect this.
  - `notion.mcp_tool_prefix_resolved_at` (date) — for staleness debugging.
- **`skills/setup/SKILL.md`** Step 5 — restructured. New 5a (Notion MCP detection cascade) runs before any database creation. Old database-creation flow renamed to 5b. Added a 5a.5 advisory about Routines portability.

### Fixed

- **search-roles double-fetched the script.** v2.2.0's prompt didn't forbid retries, so the agent would re-run `fetch-and-diff.py` to "verify" — the second run saw populated state and emitted `new_jobs: 0`, which the agent then surfaced as the run's result. Top-line symptom: an e2e run reported "0 candidates" despite 5,000+ active listings across 51 companies. **Fix:** explicit "run once, do not retry" guard with rationale (see `agents/search-roles.md` Step 1).
- **Pass 4 state-DB writes via subagent stalled at the 600s watchdog.** v2.2.0 delegated state persistence to a subagent that paralysed on bookkeeping ("how do I pass 4×35KB chunks without bloating context") and produced zero rows. **Fix:** orchestrator does the writes inline using deterministic chunk files prepared by `build-state-chunks.py` (see Pass 4 in `skills/run-job-search/SKILL.md`).
- **validate-urls produced ~65% false-negatives on Ashby pages.** v2.2.0's WebFetch + closure-signal approach received empty HTML shells for SPA-rendered ATS (Ashby, Lever) because non-JS clients don't get JS-rendered content. The agent treated "no closure signals + no apply button" as "closed for insufficient validation," eliminating real candidates. On the live e2e: 32/49 candidates wrongly closed. **Fix:** `validate-jobs.py` queries each ATS's API directly and tests for ID membership in the active set.
- **compile-write and notify-hot subagents fell back to markdown silently.** Their hardcoded `tools: ["mcp__notion__*", ...]` arrays didn't match the actual UUID-based prefix when Notion was installed via the IDE Connectors panel — the framework saw "no such tool" and the agents wrote markdown without telling the user. **Fix:** removed the hardcoded `tools:` arrays + added Tool discipline guardrails + setup-time prefix detection (Step 5a) + run-start re-probe.
- **Notion MCP server-id can rotate mid-session.** The connector UUID changed twice in this e2e (`...7108...` → `...18ab...`). Anything cached at setup time would have been stale. **Fix:** orchestrator's pre-flight step 3 re-probes via `ToolSearch "notion-search"` and rewrites `connectors.json[notion.mcp_tool_prefix]` if it has changed.

### Documented

- The v2.1.0 → v2.2.0 → v2.2.1 history of state-DB persistence is now annotated in `skills/run-job-search/SKILL.md` Pass 4 so future maintainers don't accidentally regress to a known-broken pattern.

---

## [2.2.0] — 2026-Q1

Initial release of the v2.2.x line. Replaced the v2.1.0 single-property state with a body-based JSON approach to dodge Notion's silent rich_text truncation.

### Added

- Body-based job-ID storage in the state DB (one fenced ```json code block per row, holding the full job-ID array). Notion's per-block 2000-char limit doesn't apply to page bodies, so this scales to companies with hundreds of jobs.
- `Job count` number column on state DB rows as a verification tripwire (should equal the length of the JSON array in the body — mismatch = silent truncation).
- Cloud Routine deployment mode — profile, favorites, and state can all live in Notion, so the plugin repo stays generic and shareable.
- Auto-create flow in setup wizard: parent page + Job Tracker DB + Hot Lists page + AI50 State DB + (cloud-mode) Profile + Favorites pages.

### Removed

- **Chrome MCP dependency.** All ATS data now flows through JSON APIs (Ashby, Greenhouse, Comeet) via `fetch-and-diff.py`. No browser automation pass.
- **`v2.1.0` rich_text storage of job IDs.** See "Fixed" below.

### Fixed (vs v2.1.0)

- **Silent state truncation for high-volume companies.** v2.1.0 stored job IDs as a single `Job IDs` rich_text property, which Notion truncates at 2000 characters per text block. Cohere (~115 jobs), Anthropic (~450 jobs), Cursor, etc. exceeded that limit, so state was silently corrupted — every subsequent run saw "phantom new jobs" because the diff couldn't recognise jobs that had been truncated out of state. Move to body-based storage eliminated the limit.

### Known issues at release (now fixed in v2.2.1)

- search-roles double-fetch
- Pass 4 subagent stall
- validate-urls false-negatives on SPA ATS
- compile-write / notify-hot MCP prefix mismatch
- Adaptive chunk size for outlier companies (still open — see `BACKLOG.md`)

---

## [2.1.0] — 2025

Added Notion-DB persistence as an alternative to the local state file, intended to enable cloud Routine runs.

### Added

- Notion state database backend (one row per company; `Job IDs` rich_text, `Last checked` date, `Notes`).
- Per-row state I/O via `notion-update-page` (replace_content) — with a row-by-row chunked approach to avoid bundling all 50 rows into a single MCP call.

### Known to be broken (fixed in v2.2.0)

- `Job IDs` rich_text property silently truncated at 2000 chars per block, corrupting state for companies with > ~50 jobs. v2.2.0 moved IDs into the page body to bypass the limit.

---

## Unreleased — v2.2.2 backlog

Tracked in `BACKLOG.md`. Highlights:
- **Routine-friendly Notion auth** via integration token + REST API helper script (`scripts/notion-api.py`). Lets the plugin run in ephemeral Routine containers where MCP server-ids aren't predictable.
- **Adaptive chunk size** for state DB writes — companies with > 300 jobs become single-row chunks so chunk files don't exceed the 25k-token Read limit.
- **Region classifier improvements** — "Riyadh" / continent-name-as-location strings currently fall through to UNKNOWN with score=3 (false positives). Expand `classify_region` lookups + test cases.
- **search-roles `removed_jobs` semantic cleanup** — agent currently overloads the field with filter-rejections; rename to `filtered_out` to disambiguate from script's diff-based removed list.

## Future — v2.3 candidate

- **Retire MCP entirely in favor of API token auth** — pending v2.2.2 stability. Notion integration tokens work in any environment without server-id lookups, but require explicit per-page permission grants which is less smooth UX than OAuth via the connector panel. Decision deferred.

---

## Versioning notes

The plugin folder is named `ai50-job-search-v2.2.0` for path stability. PATCH bumps (v2.2.1, v2.2.2) edit files in place rather than creating new folders, since the plugin is loaded by directory name. The version inside `plugin.json` and this changelog are the authoritative source for which patch level is in effect.
