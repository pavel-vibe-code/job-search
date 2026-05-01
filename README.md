# AI 50 Job Search

A Claude Code plugin that runs a weekly job search across the [Forbes AI 50](https://www.forbes.com/lists/ai50/) plus companies you flag as favorites, scores results against a personalised rubric, and writes qualifying matches into your Notion workspace. Designed to run unattended as a [Cloud Routine](https://claude.ai/code/routines).

```
Every Monday at 08:00:
  ├─ Fetch all 50+ companies' ATS feeds in parallel
  ├─ Diff against last week's state — only NEW jobs surface
  ├─ Filter by your role types, languages, location rules
  ├─ Score against your rubric (criteria + bonuses you defined in setup)
  ├─ Write qualifying jobs to your Notion tracker
  └─ Drop a "🔥 Hot Jobs — <date>" digest in your Notion sidebar
```

You wake up Monday with 0–10 new candidates pre-vetted. No more manually trawling 50 careers pages.

---

## Quick start

```bash
# 1. Install
git clone https://github.com/<owner>/ai50-job-search.git
claude --plugin-dir ./ai50-job-search

# 2. Mint a Notion integration token at notion.so/profile/integrations,
#    share one Notion page with it (the integration's "anchor"), then:
"set up the plugin"        # ~10 questions, ~5 minutes
"run the job search"       # first run; populates the tracker
```

For Cloud Routine setup (scheduled weekly runs), see [INSTALL.md](INSTALL.md).

---

## What it does, in detail

The pipeline has five passes per run:

| Pass | Component | What it does |
|---|---|---|
| 1 | `search-roles` agent | Fetches each company's ATS API directly (Ashby / Greenhouse / Lever / Comeet); diffs against the State DB so only NEW jobs surface |
| 2 | `validate-urls` agent | Confirms each candidate listing is still live (drops postings closed since last week) |
| 3 | `compile-write` agent | Applies your hard exclusions (language, role category, location), scores the survivors against your rubric, writes qualifying rows to your Tracker DB |
| 4 | (orchestrator) | Persists the run's job-ID state to the State DB so next week's diff works |
| 5 | `notify-hot` agent | Creates a dated digest page with the current run's highest-scoring matches |

Total runtime per fire: 60–90 seconds for 50 companies.

---

## What ends up in your Notion workspace

```
📄 AI 50 Job Search                      ← parent page
├── 📊 Job Tracker (database)            ← one row per qualifying job
│      Title │ Company │ Score │ Location │ Status │ URL │
│      Department │ Source │ Date Added │ Why Fits
├── 📁 Hot Lists                         ← weekly digest pages live here
│      └── 🔥 Hot Jobs — 2026-04-30
│      └── 🔥 Hot Jobs — 2026-05-07
│      └── ...
├── 📊 AI50 State (database)             ← per-company job-ID state (diff key)
├── 📄 AI 50 Profile                     ← your profile (JSON in body, edit anytime)
└── 📄 AI 50 Favorites                   ← your favorite companies (JSON in body)
```

To tune the scoring rubric or change role types, edit the **AI 50 Profile** Notion page directly. Changes apply on the next run.

---

## Why this exists

Most job-search tooling assumes you want a high-volume firehose with weak filters: LinkedIn alerts, Indeed digests, generic AI/ML newsletters. For senior candidates targeting a specific archetype (Senior PM at AI-native, Director CX at Series B, Forward Deployed Engineer at scale-up labs), the real bottleneck is **filtering, not finding** — there are maybe 5–10 genuinely interesting roles a week across the entire AI 50, but scattered across 50 careers pages.

This plugin owns the scattered-source problem: fetches everything, runs your filter, hands you the 5–10. You spend zero clicks on the 4,000 jobs that aren't a match.

---

## Documentation

- **[INSTALL.md](INSTALL.md)** — installation + Cloud Routine setup, two paths (local interactive, cloud scheduled)
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — full technical reference: pipeline, discovery layer, scoring, failure handling, technology choices
- **[CHANGELOG.md](CHANGELOG.md)** — release notes; v2.3.0 is the current public release

---

## Design highlights

- **Names live in the repo, IDs live in your local cache.** The plugin source is generic — anyone can clone and use it without forking. The IDs of YOUR Notion artifacts are resolved at run time from the names in `config/connectors.json[notion.names]`, with a self-healing cache fallback (`state/cached-ids.json`).
- **Two auth paths:** Notion MCP (OAuth, plug-and-play, fine for laptop runs) or API token (deterministic, recommended for Cloud Routines where >95% per-run success matters).
- **Markdown fallback for resilience.** If Notion writes fail mid-run (auth blip, API outage), the orchestrator emits the rows to `outputs/<date>-tracker-fallback.md` so results aren't lost; state is auto-corrected so next run retries.
- **Notion-only data layer, no external DB.** State, profile, favorites all live in your Notion workspace. No Postgres, no S3, no infra. The user already has Notion; the plugin just uses it.
- **150 unit tests pin the filter logic.** The tests cover eight common candidate personas (home-region only, open to relocation, multi-region remote, etc.) so future changes can't silently break filtering for one archetype while looking fine for another.

---

## Status

v2.3.0 — distribution-ready. Runs reliably as a weekly Cloud Routine. Tested end-to-end against a fresh Notion workspace.

Known limitations (see ARCHITECTURE.md §14):
- `removed_jobs_pending` (closures the agent didn't reach mid-failure) currently relies on next run's diff to re-surface; rare edge case can lose a closure.
- Cloud Routine env vars are visible to anyone with edit access on the routine — rotate the Notion token periodically if you share access.

---

## Requirements

- [Claude Code](https://claude.ai/code) installed (CLI / desktop / web)
- A Notion account
- Python 3 (always present on macOS / most Linux)

That's it. No `pip install`, no Postgres, no Redis, no Docker.

---

## Contributing

Issues and PRs welcome. The most useful contributions tend to be:
- Adding companies to `config/companies.json` (especially when their ATS slug changes)
- Adding region keywords to `scripts/fetch-and-diff.py` for personas in regions the current keyword list under-represents
- Adding persona-scenario tests to `tests/test_personas.py` if you find a candidate archetype the current filter doesn't handle well

For larger architectural changes (new connector type, new ATS provider), open an issue first to discuss.

---

## License

MIT (see LICENSE file).

---

## Acknowledgments

Built atop Claude Code's plugin system + Notion's REST API. The diff-against-state pattern was inspired by static-site-generator incremental builds. The persona-scenario test approach came out of an end-to-end retro that surfaced filter bugs invisible to single-persona testing.
