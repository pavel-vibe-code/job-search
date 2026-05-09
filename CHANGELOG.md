# Changelog

All notable changes to the AI 50 Job Search plugin. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Versioning:** v1.0.0 is the first public release. Pre-v1.0.0 development is preserved in git log and the historical tags `v2.3.0` → `v3.3.0`, which remain on the repo as a development trail. Public release cadence starts at v1.0.0; future releases bump v1.x.x.

---

## [1.1.1] — 2026-05-09 — doc patch

Backfills CHANGELOG entries for v1.0.1, v1.0.2, and v1.1.0, which were tagged and released without their corresponding changelog updates. No code changes.

### Documentation
- CHANGELOG: missing entries added for v1.0.1, v1.0.2, v1.1.0

---

## [1.1.0] — 2026-05-09 — 12 ATS adapters + scrape resilience

**Bumps the deterministic-ATS surface from 7 to 12** and fixes two existing adapters that were silently producing zero jobs due to upstream API changes. Plus scrape-extract resilience that bypasses Cloudflare-style bot gating, Notion API 2025-09-03 support, and parallel batched scoring.

### Added — new ATS adapters
- **SmartRecruiters** (`api.smartrecruiters.com/v1/companies/<slug>/postings`) — paginated, capped at 20 pages = 2000 jobs. URL synth: `jobs.smartrecruiters.com/<slug>/<id>` (the documented `jobAdUrl` field comes back null in practice).
- **Workable** (`apply.workable.com/api/v1/widget/accounts/<slug>`) — v1 widget endpoint (the v3 `/api/v3/accounts/<slug>/jobs` path in older Workable docs returns 404 on the apply.* host).
- **Recruitee** (`<slug>.recruitee.com/api/offers/`) — full description + requirements inline.
- **Personio** (`<slug>.jobs.personio.de/xml`) — XML feed; `.de` TLD canonical, `.com` / `.es` / `.it` accepted equivalently.
- **BambooHR** (`<slug>.bamboohr.com/careers/list`) — nested location decoding (`location: {city, state}` + `atsLocation: {country, state, province, city}`).
- New `score-batch` agent for parallel batched scoring (sub-agent dispatched by compile-write).

### Added — scrape resilience
- `scrape-extract` agent: **sitemap-first probe** across `/career-sitemap.xml`, `/careers-sitemap.xml`, `/jobs-sitemap.xml`, `/sitemap_index.xml`, `/sitemap.xml`, and `/robots.txt` before falling through to page fetch. Bypasses Cloudflare's IP-based bot gating since sitemaps are publicly cached.
- `diff-scrape.py`: **sustained-failure streak counter** (`state/scrape-streaks.json`, gitignored). Threshold ≥3 consecutive failures surfaces a `warning` field in the diff output. Successful extractions reset; honest 0-job results don't count as failures.
- Envelope gains `source: "sitemap" | "page"` field for provenance tracking.

### Changed — silently broken adapters fixed
- **Teamtailor**: `/api/v1/jobs` (returns 404 across every board tested in May 2026) → `/jobs.rss`. RSS items carry richer data: full HTML description, multi-location entries via the `https://teamtailor.com/locations` namespace, remote-status, UUID `guid` we now use as the stable ID. ⚠ ID namespace changes from numeric → UUID; first post-upgrade run will report all currently-tracked Teamtailor jobs as new and the prior numeric IDs as removed.
- **Homerun**: `api.homerun.co/v1/jobs/?company_subdomain=<slug>` intermittently 404s for boards that exist. New Atom-feed fallback at `feed.homerun.co/<slug>` mirrored on both fetch-and-diff and validate-jobs sides. Same one-time ID-namespace jolt on first post-upgrade run.

### Changed — infrastructure
- **Notion API**: `Notion-Version` bumped from `2022-06-28` to `2025-09-03`. New `_resolve_data_source_ids()` helper + dispatch through `POST /data_sources/<ds_id>/query`. Falls back to the legacy `POST /databases/<id>/query` endpoint for pre-migration DBs. `_resolve_parent("data_source", id)` now returns a real `{data_source_id: id}` parent (was previously a stub that fell through to `database_id`).
- **`compile-write`**: Pass 3 scoring now splits survivors into batches of ~10 and dispatches parallel `score-batch` sub-agents. ~4-6× wall-clock speedup, sharper rationales (each sub-agent fresh-context).
- **`jobs-rescore`**: explicit "scoring is in-context reasoning, not SDK shell-out" Tool Discipline section added — prevents the agent from improvising `import anthropic` / `ANTHROPIC_API_KEY` calls.
- Cost framing in docs: tokens, not dollars (~500K-1M per run on Opus, ~250-500K on Sonnet).

### Tests
- 176 unit tests passing (was 172). New URL-recognition tests for each new ATS pattern + dispatch sanity for SmartRecruiters / Workable / Recruitee / Personio / BambooHR.

### Notes
- The v1.1.0 release dropped on the same day Pavel discovered his tracker DB had become accidentally multi-source via a stray Notion-UI click. The Notion API 2025-09-03 fix was scoped specifically to unblock that — but it's defensive: it works for any tracker DB, single-source or multi-source.

---

## [1.0.2] — 2026-05-05 — repo-flip-to-public release

Cleanup release immediately preceding the GitHub-visibility flip (2026-05-06). Stripped legacy code paths, corrected misleading framing in docs, and added a user-facing GUIDE.md.

### Changed
- **Cadence framing across docs**: corrected from "automatic weekly cadence" to "plugin runs when invoked; Routines / cron / hooks schedule it." The plugin itself owns no scheduler — that's deliberate design.
- **Notion framing**: rewritten across README / INSTALL / ARCHITECTURE as a deliberate design choice (chosen as easy-to-set, easy-to-maintain for non-coders) rather than a "user has Notion already" precondition.
- **Cost framing**: tokens, not dollars (docs reflect that runs draw from Claude.ai subscription quota, not USD billing).
- **ARCHITECTURE.md**: pre-v1.0 internal version history (v2.x, v3.x development trail) removed for public-facing readability.
- Legacy code paths removed: pre-v1.0 wizard support, deprecated CLI flag compat shims.

### Added
- **GUIDE.md** — 475-line user guide: what the plugin does, first-run walkthrough, command reference, common workflows, tracker layout, cost guide, troubleshooting.
- README links to GUIDE.md from the first-run section.

---

## [1.0.1] — 2026-05-05 — first patch release

Architectural fix + skill polish + scoring discipline.

### Added
- **`jobs-settings`** skill: edit any single profile field via dialogue; preserves all other fields on write (merge-on-write so partial edits don't blow away unedited config).
- **`jobs-rescore`** skill: re-evaluate tracker rows in place against the current profile + scoring rubric. Manual upgrade path for past entries when scoring criteria change or model upgrades land.
- **`/jobs`** menu skill: lists every `jobs-*` command organized by category for discoverability.
- **`show_low: true`** profile option: exposes Low-verdict matches in the tracker (default: hidden — keeps signal high).

### Changed
- All skills namespaced under the `jobs-` prefix. Browse them via the `/jobs` menu or list with `/<skill-name>`.
- `compile-write` now enforces v3 categorical scoring writes: Match + Why Fits + Key Factors all required, no half-populated rows.
- Tracker schema: `Reasoning` column dropped — consolidated to `Why Fits` as the single rationale field.
- `http_get`: switched to a real-browser User-Agent to avoid bot-filter 403s on certain ATS endpoints (notably OpenAI's Ashby board).

### Fixed
- **Architectural**: orchestrator no longer mutates `connectors.json` at runtime. Config files (`config/`) are read-only; runtime state lives in `state/`.

---

## [1.0.0] — 2026-05-04 — first public release

**Architecture milestone.** Reframes favorites as "extended companies", reimplements scrape ATS as a Claude Code agent (no API key required), and bundles the doc-accuracy + bug-fix work iterated through pre-public development.

### Reframe: favorites → extended companies

User feedback: "favorites" was internal jargon that didn't reflect the actual mental model. The plugin tracks the AI 50 baseline by default; "favorites" was actually *custom companies you extend the baseline with*. Rename throughout for clarity:

- **Skill**: `manage-favorites` → `extend-companies` (directory + frontmatter + trigger phrases). Trigger phrases now: *"extend companies"*, *"add company"*, *"manage companies"*, etc.
- **Notion page**: "AI 50 Favorites" → "Extended Companies List". `connectors.json` key `favorites_page` → `extended_companies_page`.
- **Local file**: `config/favorites.json` → `config/custom-companies.json`. The legacy filename is still accepted as a fallback by `fetch-and-diff.py` and `validate-jobs.py` for in-place upgrades from pre-v1.0.0 installs.
- **Tracker `Source` column**: values `"ai50"` / `"favorites"` → `"ai50"` / `"custom"`.
- **CLI flags**: `--favorites-file` → `--custom-companies-file` (legacy flag preserved as alias on `fetch-and-diff.py` and `validate-jobs.py`).
- **Internal terminology** in skills, agents, scripts, docs: "favorites" → "custom companies" / "extended companies" depending on context. Historical references (e.g. validate-favorites.py filename, pre-v1.0.0 changelog entries) preserved verbatim.

### Setup wizard Step 7 reshaped

Earlier development: Step 7 went straight into "paste favorite URLs". v1.0.0: Step 7 first asks the user whether to add custom companies *now* (paste URLs) or *later* (skip and use the `extend-companies` skill on demand). Most users picking "skip" reach the first run faster; power users who want to track specific companies upfront can still opt in. After either path, the wizard prints a hint about the `extend-companies` skill so users know where to find the flow later.

### Scrape ATS reimplemented as Claude Code agent

Earlier development (introduced internal v3.2.0): scrape called `api.anthropic.com/v1/messages` directly from `fetch-and-diff.py` via `urllib.request` with `x-api-key` header. Required `ANTHROPIC_API_KEY` env var. The `fetch_scrape()` docstring claimed this was "already required by v3 LLM scoring" — that was wrong; the scoring agents (compile-write, notify-hot, feedback-recycle) run as Claude Code agents and use Claude as their substrate, not direct API calls.

v1.0.0 reimplements scrape as a Claude Code agent (`.claude/agents/scrape-extract.md`):

- **Tool surface**: `WebFetch` for page HTML, `Read` / `Write` for IO. No `Bash`, no API call.
- **Model**: declared `haiku` in frontmatter; runs against the user's Claude.ai subscription quota the same way other agents do.
- **Pipeline integration**: `fetch-and-diff.py` no longer fetches scrape companies. It writes them to `/tmp/needs_scraping.json` and emits a `scrape_pending` count in its output. The `search-roles` agent dispatches `scrape-extract` per company in parallel (Agent tool batched-call form), and `scripts/diff-scrape.py` (NEW) computes the new/removed delta against state for each result.
- **Standalone use**: NEW skill `.claude/skills/scrape-page/SKILL.md` wraps `scrape-extract` for ad-hoc extraction-quality testing. User pastes a URL → skill invokes the agent → prints the extracted job array. Useful before committing to track a company on a custom careers page.

User-facing impact:

- **No more `ANTHROPIC_API_KEY` requirement** anywhere in the plugin. Users on Pro/Max subscriptions get scrape extraction "for free" (against quota). Users on direct API-key auth pay the same per-token rate but through their existing Claude Code billing.
- **No more `api.anthropic.com` in the Routine allowlist.** Removed from INSTALL.md `§3.2b` and ARCHITECTURE.md `§11`.
- The user-facing flow in `extend-companies` is unchanged — the "scrape vs skip" choice still appears for unsupported ATSes; just the underlying execution path is different.

### Bundled forward from v3.4.0 (committed locally, never publicly released)

The internal v3.4.0 work (doc-accuracy + bug fixes from the public-release-prep audit) is included verbatim:

- README/INSTALL/ARCHITECTURE corrected from v2.4.0 framing
- Pipeline table corrected from 5 to 6 passes
- ATS list corrected (Teamtailor, Homerun, scrape were missing)
- Test count corrected (claimed 150, actual 172)
- `pack_properties()` ISO-date heuristic
- `validate-favorites.py` probe rate cap (`MAX_VARIANT_ATTEMPTS = 12`)
- Token-usage display defensiveness (removed garbled "(usage d.0.5)" output)
- Hot-digest empty-run skip (no more 52 empty digests/year)
- Cost framing in 3 places
- `validate-favorites.py` consolidated to import `ats_from_url` from `ats_adapters.py`
- Orphan `validate-favorites` skill deleted (unused since v3.x)
- `run-job-search/SKILL.md` "First-time setup" collapsed (~50 lines of duplicated INSTALL.md content → pointer)

### NOT in v1.0.0 (filed as backlog)

- **Multi-source backend (GSheets, etc.)**: `connectors.json` is structured to accept `"connector_type": "gsheets"` etc. Notion is the only currently-implemented destination. v4+ candidate when there's clear demand for non-Notion users (e.g. biz users on Google Workspace).
- **Aggregator search direction (Otta, etc.)**: filed earlier as v4 backlog. Not in v1.0.0 scope.
- **Setup wizard split**: 891 lines is a lot. Risky to restructure an interactive flow; deferred until a wizard end-to-end test harness exists.

### Versions

- **First public release: v1.0.0.** Built on internal iteration v2.3.0 → v3.3.0 (those tags remain in the repo as historical context). Going forward, only v1.x.x tags.
- Skills: `setup`, `run-job-search`, `extend-companies` (renamed from manage-favorites), `feedback-recycle`, `recalibrate-scoring`, **`scrape-page` (NEW)**
- Agents: `search-roles`, `validate-urls`, `compile-write`, `notify-hot`, **`scrape-extract` (NEW)**
- Tests: 172 (unchanged — no functional changes to filter/dispatch logic; refactor was structural)

---

## [3.4.0] — 2026-05-04

**First public release.** Comprehensive doc-accuracy + code-quality pass on top of v3.3.0; no breaking changes for existing users. Bundles ~12 internal release iterations (v2.3.0 → v3.3.0) into a public-ready release-candidate.

### Documentation accuracy

The v2.x → v3.x trail of releases left the user-facing docs out of date. This release reconciles them:

- **README.md** — bumped from v2.4.0 framing to v3.4.0; pipeline table corrected from 5 to 6 passes (Pass 6 = `feedback-recycle` auto-trigger added in v3.0.5); ATS list expanded to 7 (added Teamtailor, Homerun, scrape from v3.1.1 / v3.2.0); scoring narrative reframed for CV-grounded categorical (v3.0.0 default) instead of structured rubric (legacy); test count corrected (claimed 150, actual 172); added a "What's new in v3.x" capsule for the public landing.
- **INSTALL.md** — Routine allowed-domains list expanded with `api.anthropic.com` (required for v3 LLM scoring + scrape ATS — was silently missing, would cause Routine fires to hard-fail), `*.teamtailor.com`, `*.homerun.co`. Added a domain table explaining what each host is used for. Maintenance section updated to surface the `manage-favorites` skill (v3.3.0) and the auto-triggered `feedback-recycle` (v3.0.0+) instead of pointing users at JSON editing.
- **ARCHITECTURE.md** — systematic refresh: skills list updated (`setup, run-job-search, manage-favorites, feedback-recycle, recalibrate-scoring`); pipeline section rewritten for 6 passes; new §7.6 documents the v3.0 feedback-recycle learning loop (Match Quality / Feedback Comment / Recycled tracker columns); §10.3 ATS list updated to 7 adapters; new §10.7 documents the `ats_adapters.py` registry pattern (v3.1.0); cost framing added (~$20–50/run Opus, ~$5–15 Sonnet, ~$1–2 Haiku); test count + plugin-manifest transition + scrape-SPA limitation noted. Net +413 words across the file.

### Latent bug fixes

- **`pack_properties()` ISO-date heuristic** (`scripts/notion-api.py`). Plain ISO date strings (`"2026-05-04"`) and datetimes (`"2026-05-04T08:00:00Z"`) passed to `pack_properties()` were silently packed as `rich_text`, causing Notion to reject the property update with "expected to be date". Callers had to use the `date:Name:start` prefix syntax. Now: a regex heuristic auto-detects ISO 8601 strings and packs them as `{date: {start: ...}}`. The prefix syntax still works for users who want explicit control over `start`/`end`/`is_datetime`.
- **`fetch_scrape()` retry on transient errors** (`scripts/fetch-and-diff.py`). Previously, a single 429/5xx response or timeout from `api.anthropic.com` would fail the entire favorite for the run. Now: one retry on `[429, 500, 502, 503, 504]` and on timeout/connection errors. Permanent errors (400-class) still fail fast.
- **`validate-favorites.py` probe rate cap** (`scripts/validate-favorites.py`). The slug-variant fallback could trigger up to 33 sequential HTTP probes per failed entry with no rate limit. Capped at 12 attempts via new `MAX_VARIANT_ATTEMPTS` constant; gives up cleanly with a `gave up after N variants` error message instead of silently hanging on bad URLs.
- **Token-usage display defensiveness** (`.claude/skills/run-job-search/SKILL.md`). The "(usage data unavailable — agent pre-v3.0.5)" inline note could surface as garbled output ("(usage d.0.5)") on no-LLM-call runs. Removed the version-stamp from the section header (now just "Token usage"); orchestrator now omits the row entirely for missing-usage passes, or prints a single "Token usage: no LLM calls this run" line when ALL passes had no activity.

### Code quality

- **`scripts/validate-favorites.py` consolidated** to import `ats_from_url` from `ats_adapters.py` (the v3.1.0 single-source-of-truth registry) instead of duplicating the URL→ATS regex patterns locally. Eliminates a class of drift bug where adding an ATS to the registry wouldn't update validate-favorites' detection set. Bonus: dropped the local `re` import (unused after consolidation) and the parallel `ATS_URL_PATTERNS` table.
- **Orphan skill deleted**: `.claude/skills/validate-favorites/SKILL.md` (v1.0.0, last touched in early v2.x; never invoked from `run-job-search` or `manage-favorites`; referenced a nonexistent `chrome` ATS). The Python script `scripts/validate-favorites.py` stays as a slug-variant probing fallback used by the setup wizard's Step 7 favorites collection.
- **`run-job-search/SKILL.md` collapsed** the "First-time setup" section that duplicated INSTALL.md content. Replaced ~50 lines of stale Routine-config instructions (with `--plugin-dir` syntax abandoned in v2.4.0 and an outdated allowed-domains list) with a one-paragraph pointer to INSTALL.md §3. Kept the State DB requirement note and the orchestrator's runtime contract (Routine prompt template).
- **Hot-digest empty-run skip** (`.claude/agents/notify-hot.md`). Pre-v3.4.0 behavior was to create a dated digest page on every run, even when zero hot matches surfaced. Over 52 weekly runs that junked users' Notion sidebars with empty digests. Now: if `hot_matches == 0` AND no static notifications AND no external companies to surface, skip page creation; orchestrator logs "no hot matches this run" inline.
- **Cost framing added** in three places — README "Status" section, INSTALL maintenance section, and `setup/SKILL.md` Step 4 — surfacing that the default Opus 4.7 + extended-thinking scoring path costs ~$20–50/run, with the one-line `profile.scoring.model: "claude-sonnet-4-6"` override path cutting that to ~$5–15/run. Default unchanged (Opus delivers the quality the design assumes); just made the trade-off visible upfront so new users aren't surprised by their first invoice.

### What's NOT in v3.4.0

Filed but deferred to keep this release scope narrow:

- **Setup wizard split** (891 lines is a lot; could split into sub-procedures). Risky to restructure an interactive flow without integration testing of every branch — deferred until we have a wizard end-to-end test harness.
- **Aggregator search direction (post-v1.0 backlog)** — leveraging Otta / Y Combinator Work-at-a-Startup / etc. as adapters in the `ats_adapters` registry. Memory-noted; not built. Trigger to revisit: if users routinely add favorites because they discovered the company elsewhere.
- **`recalibrate-scoring` ↔ `feedback-recycle` consolidation** — both mutate the profile/prompt based on tracker labels (one manual, one auto). Mental-model friction is real; needs a real design pass, not a cleanup edit.
- **Hardcoded `MAX_RESPONSE_BYTES = 20MB`** — fine in practice; would need a streaming parser for proper fix, not worth it at current usage scale.
- **`pack_properties()` finalised-date `is_datetime` flag** — stored at `expanded_dates[name]["_is_datetime"]` but never consulted when building the final `date_obj`. Notion accepts the right format string regardless (datetime ISO strings get parsed as datetime; date-only as date), so the unused flag is dead weight. Could be removed in a future cleanup; not load-bearing.

### Versions

- Plugin: 3.3.0 → **3.4.0** (minor bump — public-release prep, no breaking changes)
- Skills: `setup`, `run-job-search` (3.4.0), `manage-favorites` (3.3.0), `feedback-recycle` (3.0.0), `recalibrate-scoring` (2.5.2). Removed: `validate-favorites` (orphan)
- Agents: `search-roles`, `validate-urls`, `compile-write` (3.0.2), `notify-hot` (3.4.0 — empty-skip)
- Tests: 172 (unchanged — no functional changes to filter/dispatch logic; doc + cleanup only)

---

## [3.3.0] — 2026-05-04

### New skill: `manage-favorites` (dialogue-based, no JSON editing)

User feedback: editing favorites by pasting into a Notion JSON code block is friction-prone — typos break JSON, no validation feedback, no auto-detection of ATS from URL. Especially painful when cleaning up `ats: skip` entries (auto-detection failures from initial setup) or batch-adding new companies.

`manage-favorites` is an interactive skill that handles add/remove/update/list/cleanup via dialog:

- **Add (single or bulk)**: paste careers page URLs, one per line. The skill calls `ats_adapters.ats_from_url()` (v3.1.0 helper) on each, derives `{ats, slug, careers_url}` deterministically for the 6 supported ATS (Ashby/Greenhouse incl. EU/Comeet/Lever/Teamtailor/Homerun). Falls back to `ats: "scrape"` (v3.2.0) for unsupported ATS or custom domains, or `ats: "skip"` placeholder if the user wants to come back later.
- **Remove**: by name match (exact or partial). Shows match preview before deletion.
- **Update**: change one field (ats / slug / careers_url / name) on an existing entry, or paste a new URL to re-derive ATS+slug.
- **List**: show all favorites sorted by name, grouped by ATS, with skip-count callout. Read-only.
- **Cleanup walkthrough** (Step 2e): iterate through every `ats: skip` entry one-by-one, offering paste-URL / mark-scrape / remove / skip options per entry. Designed for the post-v3.1.1+v3.2.0 cleanup pass when you want to upgrade legacy auto-detection failures.

### Persistence

- **Cloud mode**: writes the updated array back to the AI 50 Favorites Notion page body (JSON code block), via `notion-api.py update-page --replace-content`.
- **Local mode**: writes to `./config/favorites.json` (gitignored).

Refreshes `cached-ids.json` via `discover` first (defensive — same pattern as feedback-recycle Step 1; v3.0.3+).

### Edge cases handled

- Hash anchors in URLs preserved (`https://adfin.com/careers#open-positions`)
- JD-specific URLs vs. careers-index URLs (regex extracts slug correctly from either)
- Dedup on company name (case-insensitive) before write
- Companies.json overlap warning (companies.json wins per precedence rule)

### Versions

- Plugin: 3.2.0 → 3.3.0 (minor bump — new skill, additive, no breaking changes)
- Skills: run-job-search, setup, validate-favorites, recalibrate-scoring, feedback-recycle, **manage-favorites (new)**
- Tests: 172 (skill is markdown — no Python tests)

---

## [3.2.0] — 2026-05-04

### `scrape` ATS — LLM-extracted careers-page fallback

For companies whose ATS isn't in the supported set (Ashby/Greenhouse/Comeet/Lever/Teamtailor/Homerun), v3.2.0 introduces a generic fallback: per-favorite opt-in via `{ats: "scrape", careers_url: "..."}`. Each Routine fire calls Claude with the careers page HTML and asks for structured extraction.

This expands "supported" from the deterministic-API set to **anything with a public HTML careers page**. Workable, Personio, Recruitee, custom-built careers pages, niche-EU ATSes — all become trackable without writing per-ATS adapters.

### Implementation

- **New** — `fetch_scrape()` in `scripts/fetch-and-diff.py`. Pulls page HTML (truncated to ~50K tokens for cost control), calls Claude API at `api.anthropic.com/v1/messages` with a structured-extraction prompt asking for `{id, title, url, location, department}` per job. Default model: `claude-haiku-4-5-20251001` (cheap; structured extraction doesn't need Opus). Override per-favorite via `scrape_model` field.
- **New** — `normalise_scrape()` — pass-through normaliser since the LLM prompt already returns canonical shape. Adds `extraction: "llm"` marker so downstream knows this came from scrape, not deterministic API.
- **Registry entry** — `"scrape"` adapter in `ats_adapters.py` with `url_pattern: None` (never auto-dispatched from listing URL — only matched when explicitly tagged in favorites) and `active_validate_supported: False` (no API to confirm liveness, so scrape candidates go to tracker as `Status: Uncertain` per v2.5.2 envelope).
- **Wizard updated** — `.claude/skills/setup/SKILL.md` Step 7 favorites flow offers two paths when user provides a careers_url that doesn't match a known ATS pattern: `ats: scrape` (LLM-extract) or `ats: skip` (just remember the URL). Default suggestion is scrape if page looks like a careers listing.

### Cost framing

A typical careers page = 50–200K chars HTML → 12–50K input tokens. With Haiku rates (~$0.80/Mtok input, $4/Mtok output) that's **~$0.01–0.04 per page**. For 5–10 scrape favorites per fire, **~$0.05–$0.40 marginal cost** on top of the v3 LLM scoring. Negligible vs. Opus scoring's $20–30/run. If you want stronger extraction (e.g. for JS-rendered SPAs that produce sparse static HTML), per-favorite override `scrape_model: "claude-sonnet-4-6"`.

### Requirements

- `ANTHROPIC_API_KEY` must be in the environment (already required by v3 LLM scoring path; no new requirement).
- Page must serve meaningful content to non-JS HTTP clients. Pure-SPA careers pages (Workday-style) won't work — the static HTML returned to a curl-style fetcher is empty shell. For those, the user is better off either (a) finding the underlying API endpoint and adding it as a real ATS adapter, or (b) using `ats: skip` and tracking manually.

### What's NOT in v3.2.0

- **Per-favorite description fetch** — `description` field is empty for scrape entries (the careers page lists titles + URLs but rarely full JD bodies). v3 LLM scoring reads `candidate.description` for match-density assessment; for scrape entries it gets less signal. Future patch: a second-pass fetch of the JD URL for scrape candidates that pass hard exclusions, augmenting the candidate before Pass 3 scoring.
- **Pagination** — scrape extraction is single-page. Careers pages with >50 active jobs may be truncated. Acceptable for current usage; revisit if we see truncation in the wild.
- **Caching of LLM responses** — every fire re-extracts even if the page hasn't changed. With weekly cadence and Haiku pricing, the cost is negligible. If we ship daily fires with many scrape favorites, prompt-caching the page HTML (Anthropic's ephemeral cache, 5-min TTL) would help.

### Versions

- Plugin: 3.1.1 → 3.2.0
- Tests: 172 (no new tests — scrape fetcher requires real Claude API + careers page; integration-tested only)

---

## [3.1.1] — 2026-05-04

### Lever, Teamtailor, Homerun fetch + normalise (Pass 1 support)

v3.1.0 made Pass 2 (validate-jobs) work for Lever/Teamtailor/Homerun via the shared `ats_adapters` registry. v3.1.1 completes the loop by adding fetch + normalize support to `fetch-and-diff.py` (Pass 1) so favorites with these ATS types actually produce candidates.

- **New** — `fetch_lever()` + `normalise_lever()`. Lever's v0 public API at `api.lever.co/v0/postings/<slug>?mode=json`. Returns flat array of postings with `id` (UUID), `text`, `hostedUrl`, `categories.{location,department}`, `workplaceType`, `descriptionPlain`. Pre-v3.1.1 lever was detected by validate-favorites but had no fetcher — favorites with `ats: "lever"` produced zero jobs.
- **New** — `fetch_teamtailor()` + `normalise_teamtailor()`. JSON:API at `<slug>.teamtailor.com/api/v1/jobs?page[size]=200`. Returns JSON:API envelope with each job carrying `attributes.{title, body, pitch, location, remote-status, careersite-job-url, created-at, department}`. Single-page fetch handles up to 200 active jobs; multi-page pagination is a v3.1.x backlog item.
- **New** — `fetch_homerun()` + `normalise_homerun()`. Central API at `api.homerun.co/v1/jobs/?company_subdomain=<slug>`. Companies use `<slug>.homerun.co` for user-facing pages. Response shape may be `{jobs: [...]}` or bare array — handler accepts both.
- **Fixed** — `fetch_greenhouse()` now tries classic + EU API hosts (mirrors v3.0.6's validate-jobs fix). Previously, Pass 1 missed jobs from Parloa/JetBrains because they're on `boards-api.eu.greenhouse.io` and the classic host returned 404. Now Pass 1 and Pass 2 both handle EU data residency uniformly.
- **Updated** — `FETCHER_DISPATCH` registers the three new ATS alongside ashby/greenhouse/comeet/html_static/static_roles. Adding a 4th new ATS in the future is a one-place change in fetch-and-diff (registry entry) + one-place change in ats_adapters (URL pattern + active-id fetcher).

### Cross-pass parity now complete

Both Pass 1 (fetch) and Pass 2 (validate) recognize the same six ATS via the same URL patterns. Same data flows, no silent data loss when adding a favorite of any supported ATS:

| ATS | URL pattern | Fetch (Pass 1) | Validate (Pass 2) |
|---|---|---|---|
| Ashby | `(jobs\|job-boards).ashbyhq.com/<slug>` | ✓ | ✓ |
| Greenhouse | `(boards\|job-boards)(.eu)?.greenhouse.io/<slug>` | ✓ | ✓ |
| Comeet | `comeet.com/jobs/<slug>` | ✓ | ✓ |
| **Lever** | `jobs.lever.co/<slug>` | ✓ (new) | ✓ (new in v3.1.0) |
| **Teamtailor** | `<slug>.teamtailor.com/jobs/...` | ✓ (new) | ✓ (new in v3.1.0) |
| **Homerun** | `<slug>.homerun.co/...` | ✓ (new) | ✓ (new in v3.1.0) |

### Versions

- Plugin: 3.1.0 → 3.1.1
- Tests: 172 (no test changes — fetcher/normalizer logic is integration-tested via real ATS APIs which the test suite intentionally doesn't hit; URL patterns covered by v3.1.0's tests)

---

## [3.1.0] — 2026-05-04

### Shared `ats_adapters` module — single source of truth for ATS support

Adding a new ATS pre-v3.1.0 required touching three scripts and duplicating regex/URL/API logic across them. v3.1.0 extracts ATS knowledge into a single shared module so the next ATS (Workable, Personio, Recruitee, etc.) is a one-place change.

- **New** — `scripts/ats_adapters.py`. Single registry (`ATS_ADAPTERS` dict) keyed by ATS name, with each entry defining: URL regex, active-id fetcher callable, and `active_validate_supported` flag. Plus dispatch helpers (`ats_from_url()`, `active_ids_for()`, `supported_ats_for_validate()`).
- **Refactored** — `scripts/validate-jobs.py` now imports from `ats_adapters` instead of defining its own URL patterns, API constants, and per-ATS fetcher functions. Re-exports for backward-compat with tests + external callers.
- **Migrated** — Ashby, Greenhouse, Comeet, Lever moved from inline-in-script definitions to registry entries. Greenhouse's classic+EU-data-residency dual-host fetcher (added in v3.0.6) consolidated into one function in the registry.
- **New ATS support added in registry (validate side only — fetch side ships in v3.1.1):**
  - **Lever** — `jobs.lever.co/<slug>`. Active-ID fetcher uses `api.lever.co/v0/postings/<slug>?mode=json`. Pre-v3.1.0 the URL pattern existed but was marked as "recognized but unsupported" in the dispatch logic; now first-class.
  - **Teamtailor** — `<slug>.teamtailor.com/jobs/<id>-<title-slug>`. JSON:API at `<slug>.teamtailor.com/api/v1/jobs?page[size]=200`. Used by Botify and many EU companies.
  - **Homerun** — `<slug>.homerun.co/...` user-facing pages, central API at `api.homerun.co/v1/jobs/?company_subdomain=<slug>`.

### What v3.1.0 does NOT do

- **fetch-and-diff.py is unchanged** for the new ATS. v3.1.0 makes Pass 2 (validation) work for Lever/Teamtailor/Homerun, but Pass 1 (fetch) doesn't have fetchers + normalizers for them yet. That ships in **v3.1.1**.
- **Until v3.1.1 ships**, adding a Lever/Teamtailor/Homerun favorite still produces zero jobs in your tracker — the validation path improvement only matters once fetch can produce candidates for those ATS.

### Versions

- Plugin: 3.0.6 → 3.1.0
- Tests: 167 → 172 (+5: 4 new URL patterns, 1 supported_ats_for_validate set assertion)

---

## [3.0.6] — 2026-05-04

### Fix: Pass 2 used the wrong favorites source in cloud mode

User testing surfaced four entries (Parloa, Nebius, JetBrains, Make) that Pass 1 successfully fetched but Pass 2 marked `company_name_not_in_index`. Same data, opposite behavior. Root cause: `validate-jobs.py` hardcoded its index source to `plugin_root/config/{companies,favorites}.json` — the shipped template. In cloud mode the user's actual favorites live in Notion, hydrated to `/tmp/favorites.json` by orchestrator P-4. Pass 1 used the hydrated data via `fetch-and-diff.py --favorites-file`; Pass 2 silently used the template, so user-added favorites were invisible.

- **Fixed** — `scripts/validate-jobs.py` accepts `--companies-file` and `--favorites-file` CLI args (matching `fetch-and-diff.py`'s interface). Defaults to `plugin_root/config/...` when not specified.
- **Updated** — `.claude/skills/run-job-search/SKILL.md` Pass 2 invocation now passes the same files Pass 1 uses (`/tmp/companies.json` + `/tmp/favorites.json` in cloud mode).
- Net: Pass 1 and Pass 2 always read from the same source-of-truth. Same data, same behavior.

### URL patterns: EU Greenhouse subdomain coverage

`job-boards.eu.greenhouse.io/<slug>/jobs/<id>` and `boards.eu.greenhouse.io/<slug>/jobs/<id>` are how Greenhouse serves data-residency-EU customers (Parloa, JetBrains, etc.). The pre-v3.0.6 regex `(boards|job-boards)\.greenhouse\.io` missed the `.eu.` segment.

- **Fixed** — `scripts/validate-jobs.py` `ATS_URL_PATTERNS` now matches `(boards|job-boards)(?:\.eu)?\.greenhouse\.io`. Same regex change recommended for `validate-favorites.py` (next time we touch it).
- **New tests** — `tests/test_validate_jobs.py` adds 3 cases:
  - `boards.eu.greenhouse.io/<slug>/jobs/<id>` → `("greenhouse", slug)`
  - `job-boards.eu.greenhouse.io/<slug>/jobs/<id>` → `("greenhouse", slug)`
  - Custom domains with `?gh_jid=<id>` (Nebius, Make) → returns None (these fall through to name-index lookup, which works correctly post-v3.0.6 since the index is now Notion-sourced)

### What's still NOT covered

- **Custom-domain Greenhouse-backed listings** (Nebius `careers.nebius.com`, Make `make.com/en/careers-detail`) where the URL has `?gh_jid=<id>` but no slug-revealing path. URL dispatch returns None; name-index fallback is the only recovery. This works post-v3.0.6 because the index is the right one. But: if a user removes the favorite mid-cycle, the listing becomes uncertain again. Acceptable cost for the simpler regex.
- **Lever fetcher** still not implemented in `fetch-and-diff.py` — `validate-favorites.py` can detect lever, fetch can't. Lever-tagged favorites silently produce zero jobs. Documentation gap; track for a future patch.
- **`gh_jid` regex extraction** as a fallback signal — could detect Greenhouse-backing for any custom domain by sniffing the query param, then use favorite's slug for the API call. Not in v3.0.6 — adds complexity for marginal coverage gain over the index-source fix.

### Versions

- Plugin: 3.0.5 → 3.0.6
- Tests: 164 → 167 (+3 EU subdomain + custom-domain coverage)

---

## [3.0.5] — 2026-05-03

### Token tracking per run (cost observability)

v3's LLM scoring path makes per-run cost a real consideration — Opus 4.7 with extended thinking on ~200 candidates is meaningfully more expensive than Sonnet without. v3.0.5 surfaces token usage and cost estimate in every run summary so the user can see what they're paying and decide if model choice / scope tightens needed.

### Pass-level usage envelopes

Each LLM-calling agent now returns a `usage` object in its response envelope:

```json
{
  "usage": {
    "model":                       "claude-opus-4-7",
    "extended_thinking":           true,
    "candidates_scored":           14,
    "input_tokens":                245000,
    "cache_read_input_tokens":     200000,
    "cache_creation_input_tokens": 5000,
    "output_tokens":               8500,
    "thinking_tokens":             56000,
    "parse_failures":              0
  }
}
```

Agents updated:
- `compile-write.md` Step 6 — return envelope `{newly_written_jobs, uncertain_written, usage}`. Backcompat: legacy array-shape responses still accepted.
- `notify-hot.md` Step 5 — return envelope with summary fields + usage. `usage: null` if no LLM calls were made (pure-template path).
- `recalibrate-scoring/SKILL.md` Step 6 — usage printed inline (manual invocation only).
- `feedback-recycle/SKILL.md` Step 6 — usage in envelope when invoked from Pass 6 auto-trigger; printed inline when manually invoked.

### Run-summary token block

Orchestrator's run summary (run-job-search SKILL.md § Output) now includes a token + cost block that aggregates across passes:

```
━━━ Token usage ━━━
Pass 3 (compile-write):     245K input (200K cached), 8.5K output  | opus-4-7 + thinking
Pass 5 (notify-hot):        12K input, 1.2K output                  | sonnet-4-6
Pass 6 (feedback-recycle):  skipped — gate not met: last cycle 2 days ago
─────
Total: 257K input (200K cached), 9.7K output
Estimated cost: $1.42 (Opus) + $0.05 (Sonnet) = $1.47
```

### Cost calculation

Anthropic published rates table embedded in the orchestrator's aggregation logic. Cost formula per pass: `(input - cache_read) × input_rate + cache_read × cache_rate + output × output_rate + thinking × output_rate`. Summed across passes for total. Two-decimal display.

### Backward compat

If an agent returns the pre-v3.0.5 array shape (no envelope, no usage): orchestrator treats as `{newly_written_jobs: <array>, usage: null}` and prints *"(usage data unavailable — agent pre-v3.0.5)"* in the breakdown. No crash on missing keys. As Routine clones latest `main`, all agents pick up the v3.0.5 contract on next fire.

### What's NOT in v3.0.5

- **Persistence** — token usage is print-only; not yet persisted to a Notion Run Log page or local jsonl. Trend analysis over time isn't possible from within the system. If you want it, copy the run summary into a spreadsheet manually for now. Persistence ships in a later patch if there's appetite.
- **Per-candidate cost breakdown** — aggregate is per-pass not per-candidate. Adding finer granularity is straightforward but doesn't seem necessary at current usage levels.
- **Cost guardrails / budget alerts** — system reports cost but doesn't enforce limits. If budget protection is needed, run-job-search SKILL.md could gain a "abort if estimated > $X" check; not in this patch.

### Versions

- Plugin: 3.0.4 → 3.0.5
- Tests: 164 (no test changes — agent prompt + run-summary formatting only)

---

## [3.0.4] — 2026-05-03

### Fix: `update-page --replace-content` failed without `--properties`

Pre-v3.0.4 guard at the start of `cmd_update_page` rejected calls that passed only `--replace-content` because it built the PATCH payload from `--properties` and `--archive`, then errored on empty payload before checking content-replacement intent. This blocked feedback-recycle from updating the Notion profile page (content-only update — no metadata properties to PATCH) and forced a workaround in the user's session.

- **Fixed** — `scripts/notion-api.py` `cmd_update_page` accepts any of three update modes: `--properties`, `--archive`, OR `--replace-content`. Skips the PATCH `/pages/<id>` call entirely when only content replacement was requested (the children-replace path uses `/blocks/<id>/children` separately). Final ok-response uses `args.page_id` as fallback when no PATCH body was returned.
- Surfaced by user during second feedback-recycle test (continuing the session that produced v3.0.3).

### Versions

- Plugin: 3.0.3 → 3.0.4
- Tests: 164 (no test changes; bug is in CLI argument handling not covered by current Python suite)

---

## [3.0.3] — 2026-05-03

### feedback-recycle hardening + Pass 6 auto-trigger

First end-to-end test of `recycle feedback` (after the v3.0.0 ship) surfaced three issues. All fixed.

**Issue 1: `notion-api.py _summarise_properties()` stripped rich_text and checkbox values "for brevity".** Pre-v3 those omissions were fine; v3 schemas made them load-bearing — Feedback Comment, Key Factors, Reasoning, Recycled all got dropped from query summaries. The recycle skill couldn't see the data it needed and had to write a custom Python helper at runtime to query directly. Bug surfaced by user during first recycle invocation; fix is a small additive patch.

- Fixed — `scripts/notion-api.py` `_summarise_properties()` now handles `rich_text`, `checkbox`, `multi_select`, and `status` types alongside the existing `title` / `number` / `select` / `url` / `date` cases. Comment block updated to explain why "for brevity" was wrong post-v3.

**Issue 2: feedback-recycle blindly trusted `cached-ids.json` for tracker DB ID.** Same recycle invocation discovered the cached ID was stale (different from the actual current Job Tracker DB). The agent self-healed via discover, but only because the v2.3 self-healing discovery layer caught the miss. The skill should be defensive about this from the start.

- Fixed — `.claude/skills/feedback-recycle/SKILL.md` Step 1 now mandates `notion-api.py discover` as the first action, refreshing cached-ids.json before any tracker query. Eliminates the dual-state risk between local and cloud cached-ids.

**Issue 3: feedback-recycle had no auto-trigger from the orchestrator** — only manual invocation worked. v3.0.0 documented this as future work; v3.0.3 implements it.

- New — `.claude/skills/run-job-search/SKILL.md` Pass 6 (optional) invokes feedback-recycle after Pass 5 with a 3-condition gate:
  1. `deployment_mode == "cloud"` (local users invoke manually)
  2. `profile.cv_json` present (legacy profiles use structured rubric, no recycle path)
  3. `state/last_recycle.json` is missing OR has timestamp > 7 days ago
- New — feedback-recycle Step 5 now writes `state/last_recycle.json` with timestamp + counts, enabling the gate.
- `.gitignore` — `state/last_recycle.json` and `state/few_shot_examples.json` added.

### Why "run in same Routine context" matters

User insight that surfaced the design improvement: feedback-recycle reads tracker entries via the Notion API, but it needs the right tracker DB ID to query. `cached-ids.json` is per-installation — your laptop's cache and the cloud Routine container's cache drift independently. Running recycle locally when the routine is cloud-mode means the local cache may point at a stale DB. The defensive `discover` (Issue 2 fix) makes both contexts robust; the Pass 6 auto-trigger (Issue 3 fix) makes the cloud context the primary path.

### Versions

- Plugin: 3.0.2 → 3.0.3
- Tests: 164 (no test changes; bug fix in notion-api.py is a property-summary helper that's not directly unit-tested today)

---

## [3.0.2] — 2026-05-03

### v3 LLM scoring quality lift — Opus + JD-requirements-focused prompt

End-to-end v3.0.0 testing surfaced a real issue: while the architecture works (categorical buckets, hard exclusions firing, no irrelevant choices in tracker), the scoring **rationales were too shallow**. Examples like *"match: AI Solutions Architect in profile role_types[ai-fde]"* are label-overlap matches, not analysis. The LLM was reading both inputs but not doing the substantive comparison the architecture promised.

Two corrective changes:

**1. Default model: Sonnet 4.6 → Opus 4.7 with extended thinking.** Opus is meaningfully stronger at multi-criteria evaluation and nuanced JD-requirements decomposition. Extended thinking (`{type: "enabled", budget_tokens: 4000}`) gives the model space to actually decompose the JD's requirements section and trace evidence — the kind of reasoning that produces *"match: 'must have scaled support 20→100 FTE' ↔ cv_json.experience[1].key_achievements 'scaled Wrike support team 3x to 70 FTE'"* instead of *"match: customer success keyword"*.

Cost: ~5x per scoring call vs Sonnet. Prompt-caching of the constant profile section (which was already mandatory for v3) makes calls 2-N much cheaper, so end-to-end run cost is more like 3-4x not 5x. Users who want Sonnet/Haiku for cost can override via `profile.scoring.instructions: "use sonnet for cost"`.

**2. Restructured prompt: JD-requirements decomposition + evidence-grounded factors.** The previous prompt asked the LLM to "list match: / concern: / gap: factors" — open enough that label-match satisfied the instruction. The new prompt:

- Explicitly says "do NOT surface-match keywords" with examples of bad and good factor formats
- Adds a Step 1: **Decompose the JD** — extract must-haves, nice-to-haves, specific experience patterns, seniority signals, **unique asks** (the highest-signal phrases that distinguish THIS role from a generic version)
- Mandates factor format: `"match: <JD quote ≤100 chars> ↔ <specific profile field path or CV passage>"` — every match must cite both sides
- Provides reject-examples (label-only, no quote, vague) to guide the model away from shallow output
- Defends quality: rationale must be defensible to someone who has only the JD + profile in front of them, not generically applicable to any role with the same title

### Implementation details

- `profile.scoring.instructions` accepts model overrides as plain English ("use sonnet for cost", "use haiku"). Default is Opus.
- Prompt caching unchanged from v3.0-rc1 — still mandatory for the constant profile section.
- Parse-failure handling unchanged: log + assign Mid with confidence:low.
- New: track parse-failure count in run summary so prompt drift is detectable.

### Cost framing

A typical week with ~200 candidates surviving hard exclusions:
- Sonnet 4.6 (v3.0.0-rc1 default): ~$5–7/run
- **Opus 4.7 (v3.0.2 default): ~$20–30/run** — higher rationale quality
- Haiku 4.5 (cost-conservative override): ~$1–2/run — degraded quality

For weekly Routines, $20-30/week ≈ $80-120/month. If quality justifies it, fine; if not, override to Sonnet.

### Versions

- Plugin: 3.0.0 → 3.0.2
- Tests: 164 (no test changes — agent prompt edits only)
- Skipped: 3.0.1 (reserved for the queued token-tracking work; that ships with first non-cosmetic infra change)

---

## [3.0.0] — 2026-05-03

### Notion-feedback learning loop

Closes the v3 architectural goal: user labels in Notion → automated profile updates → improved scoring on next run, without requiring the user to hand-edit profile rules.

The mechanism: tracker entries gain user-feedback columns (`Match Quality`: Great/OK/Bad — same vocabulary as LLM `Match` for clean comparison; `Feedback Comment`: free text). A new `feedback-recycle` skill reads labels since last cycle, prioritizes **disagreements** between LLM and user, and turns them into (a) anti-patterns appended to profile, (b) few-shot examples included in the next Pass 3 LLM scoring prompt for generalization.

### Tracker DB schema additions

- **`Match Quality`** (select: `Great` / `OK` / `Bad`) — user-labeled feedback. Vocabulary matches `Match` (LLM verdict) intentionally — when `Match ≠ Match Quality` it's a disagreement, the highest-leverage signal for the recycle pipeline.
- **`Feedback Comment`** (rich text) — user explanation. Read by recycle skill to extract rationale.
- **`Recycled`** (checkbox) — set to true once feedback-recycle has processed this label. Prevents double-counting on subsequent runs.

### New skill: `feedback-recycle`

`.claude/skills/feedback-recycle/SKILL.md` — six-step pipeline:

1. Read tracker entries with `Match Quality` set and `Recycled` unchecked
2. Categorize each by agreement / disagreement (with strong-disagreement priority)
3. Synthesize **anti-patterns** from rejection clusters (3+ Bad entries sharing a pattern → rule added to `profile.context` with user approval)
4. Curate **few-shot examples** (3-5 representative `{job, llm_verdict, user_label, comment, lesson}` quads stored in `state/few_shot_examples.json` (local) or a dedicated Notion page (cloud), capped at 10)
5. Mark each entry `Recycled = true`
6. Print summary

The few-shot examples are the highest-leverage piece. They get included in every subsequent Pass 3 LLM scoring prompt — the LLM generalizes from concrete labeled cases without requiring user to hand-write rules.

### Disagreement signal hierarchy

| LLM `Match` | User `Match Quality` | Signal strength |
|---|---|---|
| High | Bad | **Strongest** — LLM said hot, user rejected |
| Low | Great | **Strongest** — LLM rejected, user wants |
| High/Mid/Low | (matches user label) | Confirmation — calibration check |
| High | OK / Mid | OK | Mild — gradient adjustment |

The recycle skill prioritizes strong-disagreements when synthesizing anti-patterns and few-shot examples.

### Auto-trigger integration (v3.0.0+)

The orchestrator's `run-job-search` skill can optionally invoke `feedback-recycle` after Pass 5 (gated on cloud mode + ≥7 days since last cycle). Manual invocation always works regardless of the auto-trigger gate. Local users invoke explicitly: *"recycle feedback"*, *"update profile from labels"*.

### Architectural completeness

v3.0.0 closes the loop:

```
Pass 1: hard exclusions + broad role-bucket pre-filter (cheap, deterministic)
Pass 2: live/uncertain/closed validation (v2.5.2 envelope)
Pass 3: LLM-judged categorical (v3.0-rc1) WITH few-shot examples from feedback (v3.0.0)
Pass 4: state persistence
Pass 5: hot-list digest = High bucket (v3.0-rc1)
Pass 6: feedback-recycle — anti-patterns + few-shot store updates (v3.0.0)
```

Each pass informs the next; the feedback loop closes the cycle. User effort: label tracker entries occasionally with Match Quality. System effort: everything else.

### Versions

- Plugin: 3.0.0-rc1 → 3.0.0
- Tests: 164 (no test changes — schema + skill markdown only; LLM and Notion interactions can't be unit-tested locally)
- Skills: run-job-search, setup, validate-favorites, recalibrate-scoring, **feedback-recycle (new)**

### Migration from rc1

Existing v3.0-rc1 tracker DBs need the three new columns added (`Match Quality`, `Feedback Comment`, `Recycled`). Two ways:
- Manually add via Notion DB settings (each takes ~10 seconds)
- Recreate tracker DB by deleting it and letting the orchestrator's `recreate_ok` discovery path rebuild it (only viable if the tracker is recently populated and you don't mind losing manually-added rows)

### Migration from v2.x

Profiles without `cv_json` continue using legacy structured rubric — no breaking change. To migrate to the v3 hybrid path, re-run setup wizard and opt into CV upload at Step 3.5.

---

## [3.0.0-rc1] — 2026-05-03

### Hybrid LLM-judged categorical scoring (architectural shift)

The structured numeric rubric (`scoring.criteria` × weights → 0-N score) is replaced — for profiles that opt in via CV upload — with **categorical LLM judgment** (`High` / `Mid` / `Low` buckets) against a CV-grounded free-text profile. Hard exclusions still filter aggressively first (using v2.5's typed `hard_exclusions` schema); LLM judgment runs on survivors.

**Why this matters:**

- Numeric scores hide imprecision (no real difference between 6 and 7) and drift across runs
- Title-keyword role_types miss nuance — a "Senior Applied AI Engineer" matched user's `ai-fde` keywords but is wrong-fit because it's pure-eng-IC, not customer-facing FDE (real bug surfaced by v2.4 first run)
- Wizard's translation of free-text intent into structured rules is lossy — even after v2.5.1's improvements, edge cases slip through
- LLM judgment with explicit `match:` / `concern:` / `gap:` factors makes the bucket assignment **inspectable** — user reads key_factors in tracker and immediately sees why something landed where it did

### Backward compatibility

**Profiles without `cv_json` continue using the legacy structured rubric path** (§7.1–§7.4 in ARCHITECTURE.md). Compile-write picks the path based on profile shape — no breaking change for existing v2.x profiles. To migrate, re-run `set up the plugin`, opt in to CV upload at Step 3.5, and the new path activates.

### Profile schema additions

- `cv_json` — top-level structured CV (extracted from PDF upload during Step 3.5). See ARCHITECTURE.md §7.5 for full schema. Includes `experience` (work history with achievements + technologies), `skills` (categorized), `career_signals` (seniority, years, geographic_base), and `extracted_keywords` (~30 phrases used both for Pass 3 LLM grounding and as future-Pass-1 keyword-density signal).
- `scoring.instructions` — optional free-text hint to the LLM scorer. e.g. *"be strict on AI-native vs AI-bolted-on"*. Replaces the structured `criteria` + `bonuses` blocks for v3 profiles.
- `context` (existing field) is the narrative source — wants/avoids/aspirations from wizard Q7. Read by v3 LLM scoring as candidate intent.

### Tracker DB schema additions

- **`Match`** (select: `High` / `Mid` / `Low`) — LLM verdict. Replaces numeric `Score` for v3 entries (legacy entries keep `Score`, set `Match` to null).
- **`Reasoning`** (rich text) — LLM rationale, 1-3 sentences explaining the bucket assignment.
- **`Key Factors`** (rich text) — bulleted `match:` / `concern:` / `gap:` lines comparing profile attrs to JD requirements.
- `Score` retained for legacy backward compat.

`Match Quality` (user feedback, same High/Mid/Low vocabulary) and `Feedback Comment` columns ship in v3.0.0 alongside the feedback-recycle skill.

### Setup wizard CV upload step

New Step 3.5 in `.claude/skills/setup/SKILL.md`:
- Asks user to paste path to CV or LinkedIn 'Save to PDF' export (or skip for legacy path)
- Reads PDF via Claude Code's native PDF reading
- One-shot LLM extraction call converts to canonical JSON schema (preserves achievement metrics, generates ~30 extracted_keywords)
- Shows extracted JSON to user for review/correction before saving
- If CV captured: Step 4 (scoring rubric) skips criteria/weights elicitation, asks only for optional `scoring.instructions` hint

### Compile-write rewrite

- Step 3 picks the scoring path based on `profile.cv_json` presence
- Step 3.v3: builds LLM scoring prompt (profile narrative + cv_json + scoring.instructions + few-shot examples + listing), calls Claude with prompt-caching enabled for the constant profile section, parses `{verdict, rationale, key_factors, confidence}`
- Step 3.legacy: unchanged (structured rubric scoring)
- Step 4: write rules updated for both paths — v3 sets Match/Reasoning/Key Factors/Score=null, legacy sets Score and leaves new columns null

### Notify-hot redefined

- v3 path: Hot = `Match: "High"` entries, ordered by `confidence` (high → first). Renders Reasoning + Key Factors bullets.
- Legacy path: unchanged (score ≥ hot_score_threshold).

### Ships in v3.0-rc1

This is a release candidate. The full v3.0.0 release will include:
- Notion-feedback learning loop (`feedback-recycle` skill)
- `Match Quality` + `Feedback Comment` tracker columns
- Auto-recycling of user-labeled disagreements into profile anti-patterns + few-shot examples store

Migrating to v3 is opt-in via CV upload during setup. Test the v3 path in a clean Notion workspace before swapping your production profile.

### Versions

- Plugin: 2.5.2 → 3.0.0-rc1
- Tests: 164 (no test changes — schema + agent prompt changes are markdown/JSON only; LLM-scoring path can't be unit-tested locally without API calls)
- Skills: run-job-search, setup, validate-favorites, recalibrate-scoring (all updated for v3 path or carry through unchanged)

---

## [2.5.2] — 2026-05-03

### Uncertain-job tracker handling (fixes silent state-poisoning)

Pre-v2.5.2 bug surfaced by the v2.4.0 first-run: 41 jobs from Deel / JetBrains / Back Market that Pass 2 couldn't validate either-way got dropped at the orchestrator → compile-write boundary, but Pass 4 still persisted their job IDs to state. Net effect: state DB thought "we've seen these," next run's diff treated them as historical, **user never saw them**. Permanent invisibility for any company whose ATS the validator can't probe.

- **New** — `Uncertain` value in the Tracker DB Status enum (purple). `scripts/schemas/tracker_db.json` updated; setup wizard creates DBs with the new option included; existing trackers can have the option added manually in Notion (or recreate via the orchestrator's `recreate_ok` path).
- **Updated** — `.claude/skills/run-job-search/SKILL.md` Pass 2 → Pass 3 wiring: pass3-input.json schema is now `{live, uncertain, removed_jobs, tracker_db_id}` envelope (previously a flat live-only array). Backward compat: agents accepting the old flat-array form treat it as `{live: <array>, uncertain: [], ...}`.
- **Updated** — `.claude/agents/compile-write.md` § Step 4b: process uncertains with the same hard exclusions as live, write with `Status: Uncertain`, `Score: null`, `Why Fits` populated with Pass 2's uncertain reason. Don't include in hot list.
- **Updated** — Run summary surfaces uncertain count alongside live + closed counts so users know how many to triage.

### New skill: `recalibrate-scoring`

Manual-dialog skill for tuning the scoring rubric based on what the user saw in recent runs. Foundation for the v3.0 learning loop — same conceptual flow (snapshot → critique → propose → confirm → write), just user-driven dialogue instead of automated label-recycling.

- **New** — `.claude/skills/recalibrate-scoring/SKILL.md`. Six steps: read context, surface critique-friendly view (top of hot, bottom of hot, just-below-threshold), translate feedback into specific mutations (with predicted score deltas), show diff, get approval, write updated profile to source-of-truth.
- **Promotion logic** — when user feedback implies a binary requirement ("must be EU", "never if Marketing"), the skill explicitly proposes promoting the rule from `scoring.criteria` to `hard_exclusions.rules` rather than just tweaking weights. Catches the same wizard-translation bugs that v2.5.1 prevents at capture time.
- **Invocation** — explicit user command: *"recalibrate the scoring"*, *"adjust the scoring rubric"*, *"the hot list looks off"*, etc.
- **Bridge to v3.0** — skill SKILL.md documents the path forward: same flow becomes automated via Notion `Match Quality` labels + `feedback-recycle` skill in v3.0.

### Versions

- Plugin: 2.5.1 → 2.5.2
- Tests: 164 (no test changes — markdown + schema-only changes)
- Skills: run-job-search, setup, validate-favorites, **recalibrate-scoring** (new)

---

## [2.5.1] — 2026-05-03

### Wizard generates typed `hard_exclusions` rules

The setup wizard now captures the **symmetric exclusion** that v2.4.x missed: in addition to asking where the user IS eligible (Q1-Q3), it now explicitly asks where the user actively REJECTS, even for remote roles. This was the wizard-translation bug that produced the v2.4.0 first-run problem of US/India/Japan-remote roles ending up in the hot list because `excluded_countries` was empty.

- **New** — Q3.5 in `.claude/skills/setup/SKILL.md` § Step 2: *"Are there countries or regions you'd reject even for remote roles?"* Free-text answer maps to typed `remote_country_lock` rule (with either `eligible_remote_regions` or `reject_remote_in` form).
- **New** — Step 6 generates `hard_exclusions` block in `profile.json` alongside legacy fields. Includes `language_required`, `country_lock`, `remote_country_lock`, `title_pattern` rules as derived from wizard answers.
- **New** — Step 7.5 sanity-check prompt: shows the user the typed exclusion rules in plain English BEFORE writing the sentinel. Catches mistranslations at capture time. *"Are these correct? (yes / let me adjust)"* — full-validation-with-sample-listings comes in v2.5.2's recalibrate-scoring skill.

### Compile-write agent honors typed exclusions

`.claude/agents/compile-write.md` § Step 2 rewritten to interpret typed `hard_exclusions.rules` with semantics per rule type. Falls back to legacy free-text `exclusion_rules` when typed block is absent/empty. Both forms can coexist: typed handles deterministic patterns, free-text handles judgment-call rules.

Each of the 5 rule types has explicit drop semantics documented in the agent prompt — no LLM guesswork on what `country_lock` vs `remote_country_lock` mean.

### Favorites collected with `careers_url`

Step 7 of the wizard now invites users to paste careers-page URLs alongside company names. URL parses to `(ats, slug)` deterministically using the v2.5.0 patterns; bypasses ATS auto-detection entirely. URL is preserved in the favorites entry even when ATS is derived deterministically — forward-compatible for future ATS support additions.

```
Format examples:
  Together AI, https://job-boards.greenhouse.io/togetherai
  Cohere, https://jobs.lever.co/cohere
  Anthropic                                    ← name only is fine too
```

If user provides URL but it doesn't match a known ATS (e.g. workable.com), entry is stored with `ats: "skip"` plus the URL — fetcher skips today, but URL is there to re-parse when support is added.

### Versions

- Plugin: 2.5.0 → 2.5.1
- Tests: 164 (no test changes — wizard + agent prompts are markdown-only changes)

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
