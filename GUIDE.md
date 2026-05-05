# AI 50 Job Search — User Guide

A practical guide to using the plugin. Aimed at users who've completed setup and want to know "what can I actually do here?"

For installation and Cloud Routine setup, see [INSTALL.md](INSTALL.md). For technical architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Contents

- [What this plugin does for you](#what-this-plugin-does-for-you)
- [First-run walkthrough](#first-run-walkthrough)
- [Available commands (the `jobs-` skills)](#available-commands)
- [Common workflows](#common-workflows)
- [Understanding your tracker](#understanding-your-tracker)
- [Cost guide](#cost-guide)
- [Troubleshooting](#troubleshooting)
- [Where to go deeper](#where-to-go-deeper)

---

## What this plugin does for you

You give it a profile (your CV + criteria) and a list of companies. Each time it runs (you trigger it manually, or you wire it up to fire on a schedule via Cloud Routines / cron / a hook — the plugin itself has no built-in cadence), it:

1. Fetches every job currently posted by those companies
2. Filters out jobs that conflict with your hard rules (wrong language, excluded country, etc.)
3. Has Claude evaluate each remaining job against your CV — verdict: **High** / **Mid** / **Low**
4. Writes High + Mid matches to a Notion tracker with a rationale ("Why Fits") and bulleted evidence ("Key Factors")
5. Drops a "Hot Jobs" digest in your Notion sidebar with the day's top picks
6. Learns from your tracker labels over time (the more you triage, the smarter the scoring gets)

**Default scope:** Forbes AI 50 baseline (~50 leading AI companies). You can extend with custom companies on top.

**Where data lives:** Your Notion workspace (cloud mode) or local JSON files (local mode). No external database, no third-party storage.

---

## First-run walkthrough

This is what you'll see when you run setup + first search for the first time.

### 1. Run setup

In Claude Code, type:
```
set up the plugin
```

The wizard asks ~10 questions across these areas:
- **Where you're based** + relocation preferences
- **Languages you speak** (jobs requiring others get filtered out)
- **Role types** you're targeting (search keywords + descriptions)
- **CV upload** — a PDF or pasted text. The wizard parses it into structured JSON ("cv_json") that powers v3 categorical scoring.
- **Scoring criteria** + free-form instructions (e.g. "cap non-Berlin EU at Mid")
- **Notion auth** — pick API token (recommended) or Notion MCP
- **Initial favorites** — opt-in: add custom companies now, or skip and add later via `extend companies`

When the wizard finishes, you'll see a confirmation summary like:
```
Setup complete ✅
Profile: Berlin, Germany · 5 role types · API token · Cloud Routine
Notion artifacts:
  • AI 50 Job Search (parent)
  • Job Tracker (database)
  • AI50 State (database)
  • Hot Lists (page)
  • AI 50 Profile (page)
  • Extended Companies List (page)
Next steps:
  Run your first search:    run the job search
  Add custom companies:     extend companies
```

The wizard stops here — it doesn't auto-fire the search. You decide when to run it.

### 2. Run the first search

```
run the job search
```

Takes 60–90 seconds. The pipeline runs six passes:

| Pass | What happens |
|---|---|
| **1a** | Fetches each company's jobs from their ATS API (Ashby/Greenhouse/Lever/Comeet/Teamtailor/Homerun) |
| **1b** | For any company tagged `ats: scrape`, dispatches an extraction agent on its careers page |
| **2** | Re-checks each new candidate URL is still live (drops postings closed since the diff) |
| **3** | Applies your hard exclusions, then has Claude score each survivor: High / Mid / Low. Writes High + Mid to your Notion tracker with rationale + evidence. |
| **4** | Persists the run's job-ID state so next week's diff works (only NEW jobs surface next time) |
| **5** | Creates a dated "Hot Jobs" digest with the run's High-bucket matches |
| **6** | (Once per 7 days) Recycles your tracker labels — Match Quality + Feedback Comment — into the next run's scoring prompt |

You'll see a run summary like:
```
Fetch:    52 companies | 3 errored | 1 external | + 2 scrape-extracted
Total jobs in ATS: 6,200 | New this run: 4,800 → 38 after profile filter
Validation: 36 live | 2 uncertain
Tracker: 38 new entries (35 New + 3 Uncertain)
🔥 Hot matches (5 at Match: High):
  • Anthropic Paris: Forward Deployed Engineer
  • Cohere London: Solutions Engineer (EU remote)
  • ...
Hot list: <Notion URL>
```

### 3. Review the tracker

Open your **Job Tracker** Notion database. You'll see a row per qualifying match with:
- **Match**: High / Mid (Low is hidden by default — see [show_low](#how-to-show-low-bucket-entries))
- **Why Fits**: 1–3 sentence rationale
- **Key Factors**: bulleted match/concern/gap evidence
- **Status**: New (your action: triage)
- **Match Quality**: empty (your action: label after applying or rejecting)

Spend 10 minutes triaging. As you label rows, the feedback loop trains scoring on your judgments.

---

## Available commands

Every plugin command is namespaced under `jobs-` for slash-completion. Type `/jobs-` in Claude Code to see them all. Or invoke by natural-English trigger phrase.

### `/jobs` — menu

```
jobs
```
Prints a one-screen menu of every available command, organized by use case. Useful when you forget what's available.

### `/jobs-setup` — initial install / full reconfigure

```
set up the plugin
```
Runs the setup wizard. Use this for first-time install or if you want to fully redo your profile from scratch (changes location, role types, CV, etc. — wipes manually-added options like `show_low`). For ad-hoc tweaks, use `jobs-settings` instead (preserves customizations).

### `/jobs-run` — fire the pipeline

```
run the job search
```
Runs the full 6-pass pipeline once. ~60–90 seconds. Use this for on-demand search; if you've wired up a Cloud Routine (or any other scheduling mechanism) to fire it automatically, you don't need to trigger it manually except for ad-hoc runs.

### `/jobs-extend-companies` — manage custom companies

```
extend companies
```
Interactive dialog to add / remove / update / list / cleanup custom-tracked companies (companies on top of the AI 50 baseline). Auto-detects ATS from pasted careers URLs. Bulk paste supported. No JSON editing required.

**Five sub-modes:**
1. Add — paste careers URL(s), one per line
2. Remove — by name match
3. Update — change ATS / slug / careers_url for an existing entry
4. List — see all custom companies sorted by ATS
5. Cleanup walkthrough — iterate through `ats: skip` entries one by one to upgrade or remove

### `/jobs-scrape-page` — preview a careers page extraction

```
scrape this page: https://example.com/careers
```
Runs the scrape-extract agent on a single URL and prints the extracted job array. **No tracking, no Notion writes** — pure preview. Useful before adding a company as scrape-tracked, to confirm extraction quality on its careers page.

If extraction quality is good (correct titles, real URLs), you can then add it via `extend companies`. If quality is bad (page is a JS-only SPA, agent returns no_static_content), mark the company as `ats: skip` instead — scrape would always return empty for it.

### `/jobs-rescore` — re-evaluate tracker rows

```
re-score
```
Re-runs the scoring prompt on existing tracker rows. Five scope modes:
1. Empty rationales only — fix rows with empty Why Fits / Key Factors
2. Date-bounded — rescore everything since a given date
3. Specific Match bucket — e.g. all Mid (cheapest expansion)
4. Specific company
5. All rows (expensive — confirm cost first)

**Crucially preserves your labels** (Match Quality, Status, Feedback Comment) — only rewrites Match + Why Fits + Key Factors.

Use cases:
- Fix rows from past runs that have empty rationale columns
- Rescore after changing your profile (criteria, hard exclusions)
- Upgrade past Sonnet-scored entries to Opus quality

### `/jobs-recycle-feedback` — train scoring on your labels

```
recycle feedback
```
Reads tracker entries you've labeled with Match Quality (Great / OK / Bad) + Feedback Comment, derives anti-patterns from disagreements between LLM verdict and your label, folds them into the next run's scoring prompt as few-shot examples.

**Auto-fires** at the end of every weekly Routine run if 7+ days since last cycle. Manual trigger with this command if you want to recycle sooner (e.g. after labeling a big batch of rows you want reflected in the next fire).

### `/jobs-recalibrate` — guided scoring criteria tuning

```
recalibrate the scoring
```
Interactive "let me adjust / re-think" dialog for tuning your scoring rubric. Useful after seeing a few real runs and noticing patterns ("the Mid bucket is too lenient"; "I want compensation weighted higher"). Different from `jobs-settings` — this is guided coaching, not just field edits.

### `/jobs-settings` — edit any single profile field

```
settings
```
Surface every profile field as an editable settings menu. Pick a category (Identity / Role types / Location rules / Hard exclusions / Scoring / CV / etc.), drill in, edit one field at a time. **Every write preserves all other fields** — manually-added options like `show_low` survive.

Use this for ad-hoc edits without re-running the setup wizard or hand-editing JSON.

---

## Common workflows

### How to add a custom company

```
extend companies
```
- Pick "1. Add"
- Paste careers URL (e.g. `https://job-boards.greenhouse.io/cohere/`)
- Skill auto-detects ATS, asks you to confirm display name
- Confirm → company added
- Next pipeline run picks it up

For unsupported ATSes (Workable, Personio, custom domain), the skill offers `scrape` (Claude extracts from HTML each fire — no API key needed) or `skip` (URL remembered, not fetched).

### How to fix empty rationale rows

If past runs left tracker rows with empty Why Fits / Key Factors:
```
re-score
```
- Pick "1. Empty rationales only"
- Optionally choose Sonnet for cheaper rescoring
- Skill re-runs scoring on every empty-rationale row
- Updates Why Fits + Key Factors in place; preserves your labels

Cost: roughly ~150K–300K tokens on Sonnet for ~40 rows; ~400K–800K on Opus. Counts against your Claude.ai subscription quota.

### How to show Low-bucket entries

By default, Low-verdict matches are dropped from your tracker. To include them (Match: Low rows visible alongside High / Mid):

```
settings
```
- Pick "5. Scoring"
- Pick `show_low`
- Toggle to true
- Save

Next run will write Low entries too. They're useful as negative training data — labeling Low entries you'd actually consider as "Match Quality: OK" trains scoring to surface similar patterns higher next time.

### How to switch from Opus to Sonnet for cheaper runs

```
settings
```
- Pick "5. Scoring"
- Pick `model`
- Pick `claude-sonnet-4-6`
- Save

Cuts subscription quota usage by ~75%. Quality drop is usually small for clear-fit candidates; the model excels at obvious High/Low calls but is slightly less nuanced on borderline Mid cases.

### How to update your hard exclusions

```
settings
```
- Pick "4. Hard exclusions"
- Add/remove/edit individual rules
- Save

Rule types: language fluency required, country/city lock, role pattern drop (e.g. "title contains Sales BDR AE quota"), seniority minimum, custom free-form rule.

After updating, run `re-score` (scope: "All rows since installation") to retroactively apply new rules to existing tracker rows.

### How to train scoring on your labels

After a few runs, you'll have triaged some tracker rows. Set their **Match Quality** column:
- **Great** = "this match is genuinely interesting; I'd apply"
- **OK** = "fine; not exciting"
- **Bad** = "the LLM was wrong; this is not a fit"

Optionally fill in **Feedback Comment** with a free-text reason (most useful for Bad: "ignored the 30%+ travel requirement", "hard exclusion missed").

The next pipeline run auto-recycles these labels into anti-patterns and few-shot examples that improve subsequent scoring. Or trigger manually:
```
recycle feedback
```

### How to test extraction quality on a careers page

Before committing to track an unsupported-ATS company:
```
scrape this page: https://example.com/careers
```
You'll see the extracted job array. If the agent returns `extraction_quality: "no_static_content"` (page is JS-only), the company should be `ats: skip` rather than scrape. If quality is good, proceed to `extend companies` to add it.

### How to wire up automatic runs (Cloud Routine)

The plugin itself has no built-in scheduler — it runs when something invokes it. Cloud Routines are one way to schedule that invocation (the canonical one for unattended use); cron / shell hooks / external triggers also work. See [INSTALL.md §3](INSTALL.md) for the Cloud Routine setup. After that, runs fire on whatever cadence you configured — no further action required.

### How to re-run setup if something gets stuck

Setup wizard creates `state/.setup_complete` on completion. To re-trigger setup:
```bash
rm state/.setup_complete
```
Then in Claude Code:
```
run the job search
```
The orchestrator detects the missing sentinel and triggers the setup wizard again. **Note**: re-running setup wipes your manual customizations (e.g. `show_low: true`). For partial reconfigure, use `jobs-settings` instead.

### How to see what commands are available

```
jobs
```
or:
```
/jobs
```
Prints the menu of all 9 plugin commands.

---

## Understanding your tracker

Your **Job Tracker** Notion database has these columns:

| Column | Type | What it means |
|---|---|---|
| **Title** | text | Job title from the source ATS |
| **Company** | text | Company name |
| **Score** | number | Reserved (always null) — kept in schema for compatibility |
| **Match** | select | `High` / `Mid` / `Low` (categorical verdict from LLM; the primary signal) |
| **Location** | text | Location string from the source |
| **Status** | select | `New` (your action: triage) / `Reviewed` / `Applied` / `Closed` (auto-set when ATS removes the listing) / `Not interested` / `Uncertain` (validation could not confirm live, e.g. scrape-tracked) |
| **URL** | url | Direct link to the job description |
| **Department** | text | Team / function from the source |
| **Source** | text | `ai50` (Forbes baseline) or `custom` (extended-companies entry) |
| **Date Added** | date | When this row was written to your tracker |
| **Why Fits** | text | LLM rationale, 1–3 sentences |
| **Key Factors** | text | Bulleted match: / concern: / gap: lines, one per line |
| **Match Quality** | select | **Your** label after triage: `Great` / `OK` / `Bad`. Used by feedback-recycle. |
| **Feedback Comment** | text | **Your** free-text reason for the label. Used by feedback-recycle. |
| **Recycled** | checkbox | Auto-set when feedback-recycle has processed the label (for incremental training) |

**Triaging workflow:**
1. Open the tracker. Filter to `Status: New`.
2. For each row, read **Why Fits** + **Key Factors** + click the URL if interested.
3. Set **Status** based on action: Reviewed (still considering), Applied, Not interested.
4. Set **Match Quality** based on whether the LLM's call was right: Great / OK / Bad.
5. Optionally add **Feedback Comment** explaining the label (especially for Bad).

The next pipeline run (whenever it fires) auto-folds your Match Quality + Feedback Comment into the scoring prompt as anti-patterns + few-shot examples — scoring sharpens with each iteration of label-then-run.

---

## Cost guide

The plugin runs on **Claude as the LLM substrate** — billed against your Claude.ai subscription quota (Pro / Max), not pay-per-token. No Anthropic API key required.

### Per pipeline run

| Component | Subscription quota usage |
|---|---|
| Pass 1 fetch (deterministic ATSes) | None (HTTP only) |
| Pass 1b scrape-extract (per scrape company) | ~12K input + ~500 output tokens, Haiku — ~$0.01–0.04 equivalent |
| Pass 2 validate-urls | None (HTTP only; agent does no LLM work) |
| Pass 3 compile-write (the big one) | ~12K input × N candidates × Opus + extended thinking |
| Pass 5 notify-hot | ~5K input + 1K output tokens, Sonnet — small |
| Pass 6 feedback-recycle | ~10K input + 2K output, Sonnet — small (auto-fires weekly) |

**Total per run:**
- Opus default: ~500K–1M tokens
- Sonnet override (`profile.scoring.model: "claude-sonnet-4-6"`): ~250K–500K tokens
- Haiku override: ~100K–250K tokens (lower quality on borderline calls)

**Your subscription's perspective:**
- **Pro plan** (5-hour rolling window): each pipeline run is a meaningful chunk of the cap. If you're firing weekly via a Cloud Routine and also using Claude Code for daily work, expect to feel quota pressure. Loosen by switching to Sonnet for scoring (see below).
- **Max plan** (5x Pro): comfortable headroom for routine pipeline fires + daily Claude Code use, regardless of cadence.
- **API-key auth** (Claude Code wired to direct Anthropic API key): pay-per-token at the rates above.

### Per-skill costs

| Skill | Cost per invocation |
|---|---|
| `jobs-settings`, `jobs-extend-companies`, `jobs` (menu) | Near-zero (deterministic; no LLM scoring) |
| `jobs-scrape-page` | One Haiku call per URL, ~$0.01–0.04 equivalent |
| `jobs-rescore` | Same per-row cost as Pass 3; multiply by N rows. Sonnet override recommended for bulk rescoring. |
| `jobs-recycle-feedback` | One Sonnet call per recycle batch — small |
| `jobs-recalibrate` | Multiple LLM rounds, depending on how much adjustment you do |
| `jobs-setup` | One Haiku call to parse CV (if you upload one); rest is deterministic |

### How to manage quota

1. **Default to Sonnet**: `settings` → Scoring → model → `claude-sonnet-4-6`. ~75% cheaper. Re-evaluate quality after a few runs; switch back to Opus if you need it.
2. **Don't `re-score` the whole tracker frivolously**: rescoring 200+ rows on Opus is expensive. Use scope filters (date-bounded, specific bucket) to keep batches small.
3. **Hard exclusions are free**: tighten them to drop more candidates pre-scoring. Each candidate dropped pre-Pass-3 is a Pass-3 LLM call you save.
4. **Watch the per-run token block**: every run prints token counts at the end. If a run spikes unexpectedly (e.g. 200+ candidates), tighten filters.

---

## Troubleshooting

### "Setup not yet completed"
The orchestrator can't find `state/.setup_complete`. Run:
```
set up the plugin
```

### "Notion artifacts missing"
The plugin's Notion pages were moved or deleted. Re-run setup:
```
set up the plugin
```
The wizard will discover existing artifacts by name and re-cache their IDs, or recreate missing ones. **Profile and Extended Companies pages are NOT auto-recreated** (they hold your content); you'll be guided to recreate them manually if missing.

### "no candidates this run"
Two common causes:
1. **First-run state was empty, then state file was wiped**. The diff finds nothing new because state has rolled forward. Fine; next week brings new postings.
2. **Hard exclusions too strict**. Check `settings` → Hard exclusions. Common pitfall: meta-phrases like "all non-EU" in `excluded_countries` — these aren't canonical country names. Replace with explicit list ("United States", "Canada", etc.).

### "0 High matches; only Mid"
Your hard exclusions + criteria might be unusually strict, or the AI 50 baseline + favorites genuinely have nothing perfect-fit this week. Mid is the realistic top of funnel for many user profiles. Don't worry about hitting High every week.

If you want to relax: `settings` → Scoring → `instructions` → adjust the hint, or `recalibrate the scoring` for guided dialog.

### "Reasoning column is empty"
Tracker rows from very early runs may have empty rationale columns. Fix with:
```
re-score
```
Pick "Empty rationales only" scope. Sonnet override recommended for cost.

### "Cloud Routine is failing"
- **Check Routine env vars**: `NOTION_API_TOKEN` is required; `NOTION_PARENT_ANCHOR_ID` is recommended. See INSTALL.md §3.2.
- **Allowed domains**: must include `api.notion.com` + your tracked ATSes' wildcards. INSTALL.md §3.2b lists them.
- **Setup script**: must create `state/.setup_complete`. INSTALL.md §3.2c has the canonical script.

### "An ATS company is 403/404'ing"
Some companies' boards filter bots or rotate slugs. Workaround: tag the company as `ats: scrape` (extracts via Claude agent on the HTML careers page instead of the API):
```
extend companies
```
Pick "Update", change ATS to `scrape`, save. Next run fetches via scrape-extract.

### "I want to start over"
Delete the sentinel + caches + (optionally) the Notion artifacts:
```bash
rm -rf state/.setup_complete state/cached-ids.json state/companies.json
# (Optionally delete the AI 50 Job Search Notion page hierarchy from your workspace)
```
Then in Claude Code:
```
set up the plugin
```

---

## Where to go deeper

- **[README.md](README.md)** — short overview + quick start
- **[INSTALL.md](INSTALL.md)** — installation paths (Quick / Advanced) + Cloud Routine setup
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — full technical reference: pipeline internals, ATS adapter registry, scoring algorithm, schema design, failure handling
- **[CHANGELOG.md](CHANGELOG.md)** — release history

If something here is unclear or missing, file an issue on the repo. The user-facing docs improve based on real questions.
