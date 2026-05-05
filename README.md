# AI 50 Job Search

A Claude Code plugin that runs a job search across the [Forbes AI 50](https://www.forbes.com/lists/ai50/) — and any custom companies you add on top — scores results against your CV + criteria, and writes qualifying matches into your Notion workspace. The plugin runs when invoked (manually, or via whatever scheduling you wire up). Designed to work unattended via [Cloud Routines](https://claude.ai/code/routines), cron, or any other trigger.

```
Each pipeline run (manual or scheduled):
  ├─ Fetch all 50+ companies' ATS feeds in parallel
  ├─ Diff against last run's state — only NEW jobs surface
  ├─ Filter by your role types, languages, hard-exclusion rules
  ├─ Score against your CV (LLM-judged High / Mid / Low buckets)
  ├─ Write qualifying jobs to your Notion tracker
  ├─ Drop a "🔥 Hot Jobs — <date>" digest in your Notion sidebar
  └─ Recycle your tracker labels into next run's scoring (when it's been ≥ 7 days since last cycle)
```

A typical setup: wire it up via Cloud Routine to fire weekly. You wake up Monday with 0–10 new candidates pre-vetted. No more manually trawling 50+ careers pages. (Daily / monthly / event-triggered cadences also work — the plugin doesn't care, it runs when invoked.)

**What v1.0 ships with** (after iteration through internal v2.x → v4.x): CV-grounded LLM-judged categorical scoring (High / Mid / Low buckets, not numeric rubric); 6 deterministic ATS adapters (Ashby, Greenhouse incl. EU subdomain, Lever, Comeet, Teamtailor, Homerun) plus a Claude Code agent–based scrape fallback for any HTML careers page (no API key required); Notion-feedback learning loop that improves scoring week-over-week from your tracker labels; `jobs-extend-companies` skill for dialogue-based add/remove/update of custom-tracked companies on top of the AI 50 baseline (no JSON editing); `jobs-scrape-page` skill for ad-hoc extraction-quality testing; per-run token + cost tracking against your Claude.ai subscription quota. See [CHANGELOG.md](CHANGELOG.md) for the full development trail.

---

## Quick start

```bash
# 1. Install
git clone https://github.com/pavel-vibe-code/job-search.git
cd job-search
claude

# 2. Mint a Notion integration token at notion.so/profile/integrations,
#    share one Notion page with it (the integration's "anchor"), then:
"set up the plugin"        # ~10 questions, ~5 minutes
"run the job search"       # first run; populates the tracker
```

For Cloud Routine setup (one common way to fire the pipeline on a schedule), see [INSTALL.md](INSTALL.md).

---

## What it does, in detail

The pipeline has six passes per run:

| Pass | Component | What it does |
|---|---|---|
| 1 | `search-roles` agent | Fetches each company's ATS API directly (Ashby / Greenhouse incl. EU / Lever / Comeet / Teamtailor / Homerun); for any company tagged `ats: scrape`, dispatches the `scrape-extract` Claude Code agent (Haiku) to extract jobs from the HTML careers page. Diffs all results against the State DB so only NEW jobs surface. |
| 2 | `validate-urls` agent | Confirms each candidate listing is still live (drops postings closed since last week) |
| 3 | `compile-write` agent | Applies typed hard exclusions (language, location, custom rules), scores survivors with LLM-judged High/Mid/Low against your CV + criteria, writes qualifying rows to your Tracker DB |
| 4 | (orchestrator) | Persists the run's job-ID state to the State DB so next week's diff works |
| 5 | `notify-hot` agent | Creates a dated digest page with the current run's High-bucket matches (skips the page if there's nothing hot to report) |
| 6 | `jobs-recycle-feedback` skill | Auto-triggers if 7+ days since last cycle: reads your tracker labels (Match Quality, Feedback Comment), derives anti-patterns + few-shot examples, feeds them into next run's scoring prompt |

Total runtime per fire: 60–90 seconds for ~50 companies plus a few seconds per scrape-tracked company. **Cost** runs against your Claude.ai subscription quota (the agents use Claude as their substrate — no Anthropic API key needed). For users who run Claude Code via direct API key auth instead of Claude.ai login, the equivalent pay-per-token cost is roughly **$20–50 per run on Opus default, $5–15 on Sonnet** (override via `profile.scoring.model`).

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
└── 📄 Extended Companies List           ← companies you track on top of AI 50
```

To tune scoring criteria or change role types, edit the **AI 50 Profile** Notion page directly (changes apply on next run). To add/remove/update custom companies on top of the AI 50 baseline, run the `jobs-extend-companies` skill (dialogue-based, no JSON editing). To preview extraction quality on a careers page before adding it as scrape-tracked, run `jobs-scrape-page`.

---

## Why this exists

Most job-search tooling assumes you want a high-volume firehose with weak filters: LinkedIn alerts, Indeed digests, generic AI/ML newsletters. For senior candidates targeting a specific archetype (Senior PM at AI-native, Director CX at Series B, Forward Deployed Engineer at scale-up labs), the real bottleneck is **filtering, not finding** — there are maybe 5–10 genuinely interesting roles a week across the entire AI 50, but scattered across 50 careers pages.

This plugin owns the scattered-source problem: fetches everything, runs your filter, hands you the 5–10. You spend zero clicks on the 4,000 jobs that aren't a match.

---

## Documentation

- **[INSTALL.md](INSTALL.md)** — installation + Cloud Routine setup, two paths (local interactive, cloud scheduled)
- **[GUIDE.md](GUIDE.md)** — user guide: every command explained, common workflows, tracker layout, cost guide, troubleshooting
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — full technical reference: pipeline, discovery layer, scoring, failure handling, technology choices
- **[CHANGELOG.md](CHANGELOG.md)** — release notes

---

## Design highlights

- **Names live in the repo, IDs live in your local cache.** The plugin source is generic — anyone can clone and use it without forking. The IDs of YOUR Notion artifacts are resolved at run time from the names in `config/connectors.json[notion.names]`, with a self-healing cache fallback (`state/cached-ids.json`).
- **Two auth paths:** Notion MCP (OAuth, plug-and-play, fine for laptop runs) or API token (deterministic, recommended for Cloud Routines where >95% per-run success matters).
- **Markdown fallback for resilience.** If Notion writes fail mid-run (auth blip, API outage), the orchestrator emits the rows to `outputs/<date>-tracker-fallback.md` so results aren't lost; state is auto-corrected so next run retries.
- **Notion-only data layer, no external DB.** State, profile, favorites all live in the Notion workspace this plugin sets up for you. No Postgres, no S3, no infra. Notion was chosen as the data layer because it gives non-coders a usable database UI without any server or schema-migration setup; users without prior Notion experience can sign up free at notion.so during setup.
- **172 unit tests pin the filter and dispatch logic.** The tests cover eight common candidate personas (home-region only, open to relocation, multi-region remote, etc.) plus URL→ATS dispatch coverage, so future changes can't silently break filtering for one archetype or break a connector while looking fine for another.

---

## Status

v1.0.x — first public release line. Runs reliably under a Cloud Routine (or any other invocation mechanism). Tested end-to-end against fresh Notion workspaces. Public versioning starts at v1.0.0; future releases follow semver.

Known limitations:
- `removed_jobs_pending` (closures the agent didn't reach mid-failure) currently relies on next run's diff to re-surface; rare edge case can lose a closure.
- Cloud Routine env vars are visible to anyone with edit access on the routine — rotate the Notion token periodically if you share access.
- `scrape` ATS (Claude Code agent extraction of HTML careers pages) only works on pages that serve meaningful HTML to non-JS clients; pure SPAs return an empty shell and the agent returns `extraction_quality: no_static_content`. Use the `jobs-scrape-page` skill to test extraction quality on a careers page before committing to track it.
- Default scoring uses Claude Opus 4.7 with extended thinking — premium quality. Cost runs against your Claude.ai subscription quota (Pro: meaningful chunk of weekly cap; Max: comfortable headroom). To cut quota use ~75% with a small quality drop, set `profile.scoring.model: "claude-sonnet-4-6"` in your AI 50 Profile page.

---

## Requirements

- [Claude Code](https://claude.ai/code) installed (CLI / desktop / web), with a Claude.ai subscription (Pro or Max recommended) OR direct API key auth
- A Notion account (free at notion.so — the plugin uses Notion as its data layer; no prior Notion experience needed)
- Python 3 (always present on macOS / most Linux)

That's it. No `pip install`, no Anthropic API key required (the LLM work runs on Claude as the agent substrate), no Postgres, no Redis, no Docker.

---

## Contributing

Issues and PRs welcome. The most useful contributions tend to be:
- Adding companies to `config/companies.json` (the curated AI 50 baseline shipped with the plugin) — especially when their ATS slug changes. End-users add personal companies via the `jobs-extend-companies` skill, not by editing companies.json.
- Adding ATS adapters to `scripts/ats_adapters.py` (the registry pattern — one new entry per ATS, plus a fetcher in `fetch-and-diff.py` and a normaliser)
- Adding region keywords to `scripts/fetch-and-diff.py` for personas in regions the current keyword list under-represents
- Adding persona-scenario tests to `tests/test_personas.py` if you find a candidate archetype the current filter doesn't handle well

For larger architectural changes (new connector type, new auth method), open an issue first to discuss.

---

## License

MIT (see LICENSE file).

---

## Acknowledgments

Built atop Claude Code's plugin system + Notion's REST API. The diff-against-state pattern was inspired by static-site-generator incremental builds. The persona-scenario test approach came out of an end-to-end retro that surfaced filter bugs invisible to single-persona testing.
