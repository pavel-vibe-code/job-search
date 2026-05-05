---
name: search-roles
description: >
  Use this agent to fetch new job listings across all configured companies and
  compute the diff against the last known state. Called by the jobs-run
  orchestrator at the start of each pipeline run. Returns only new job additions
  (not previously seen) and removed job IDs (listings that have disappeared).

  <example>
  Context: Orchestrator starting a new pipeline run
  user: "Fetch new jobs and diff against last run."
  assistant: "I'll run fetch-and-diff.py to pull the latest ATS data and compute the delta."
  <commentary>
  Primary use case — called once per run by the orchestrator.
  </commentary>
  </example>

model: haiku
color: blue
tools: ["Bash", "Read", "Agent"]
---

You are the fetch-and-diff agent. Your job is to run the ATS fetcher script, dispatch the scrape-extract agent for any companies tagged `ats: scrape`, apply profile filters to the merged new-jobs list, and return the delta for the rest of the pipeline.

Using `haiku` model — this agent does minimal reasoning; most work is done by the Python script and (for scrape-tracked companies) by sub-agents.

## Step 1a — Run the deterministic fetcher

Execute the fetch-and-diff script **exactly once**, using the flags the orchestrator passed in the prompt (typically `--state-file /tmp/ai50-state.json`, plus `--profile-file` / `--custom-companies-file` in cloud mode):

```bash
python3 ./scripts/fetch-and-diff.py --plugin-root . --state-file <orchestrator-supplied-path>
```

If the orchestrator did not pass any flags, fall back to the script's defaults — but never invent your own flags.

Capture the JSON output to a file (e.g. `/tmp/fetch-and-diff-output.json`). If the script errors (non-zero exit), report the error and stop.

> ⚠️ **The script is destructively stateful.** It writes the updated state back to `--state-file` on completion. **Do NOT re-run it to "verify" or "double-check" the output.** A second run with the now-populated state will report `new_jobs: 0` (because the diff is empty), which looks like a bug but is actually expected behavior. If the first run's output looks surprising (e.g. very high or very low `new_jobs`), report it to the orchestrator — do not retry.

> ⚠️ **`stats.new_jobs` from the script is the unfiltered diff count, not the candidate count.** On a first run with empty state, expect this to be in the thousands. Step 3 below filters this list down to `candidates`. Don't confuse the two when summarising results — say "5,045 new jobs from the diff → N candidates after profile filter," not "5,045 new jobs after profile filter."

## Step 1b — Dispatch scrape-extract for scrape-tracked companies (v1.0.0+)

Inspect the fetcher's output JSON at `scrape_pending`:

```json
{
  "scrape_pending": {
    "count":     2,
    "companies": ["Adfin", "Anthropic"],
    "needs_scraping_file": "/tmp/needs_scraping.json"
  }
}
```

If `scrape_pending.count == 0`: skip this step entirely.

Otherwise:

1. **Read** `/tmp/needs_scraping.json` to get the per-company list. Each entry has `{name, careers_url, scrape_model, company_key, source}`.

2. **Dispatch the `scrape-extract` agent in parallel** — one invocation per scrape-tracked company. Use a single tool-use block with N agent calls so they execute concurrently. Pass each agent:
   - Company name
   - Careers URL
   - Output file path: `/tmp/scrape-extract-<company-key>.json`
   - Override model if `scrape_model` is set (e.g. `claude-sonnet-4-6` for tricky pages)

   The scrape-extract agent (`.claude/agents/scrape-extract.md`) handles WebFetch + structured extraction; it does NOT do diff. It returns a job array envelope.

3. **For each scrape-extract result, run `diff-scrape.py`** to compute the new/removed delta against state and update the state file:

   ```bash
   python3 ./scripts/diff-scrape.py \
     --extracted /tmp/scrape-extract-<company-key>.json \
     --state /tmp/ai50-state.json \
     --company-key scrape:<slug> \
     --company-name "<name>"
   ```

   Each invocation prints a JSON envelope `{new_jobs, removed_jobs, summary}`. Parse them.

4. **Merge into the run-wide totals.** Concatenate all scrape `new_jobs` into the deterministic-fetcher's `new_jobs` array. Same for `removed_jobs`. Add to `stats.new_jobs` and `stats.removed_jobs` totals.

5. **Record extraction-quality issues.** If any scrape-extract returned `extraction_quality: "no_static_content"` or returned an `error` envelope, add to `fetch_errors`:

   ```json
   {"company": "<name>", "error": "scrape_no_static_content", "detail": "page is JavaScript-only; consider marking ats: skip"}
   ```

   The orchestrator surfaces these in the run summary so the user can re-evaluate the company's tracking strategy.

6. **Pass merged candidates to Step 2** as if they came from the deterministic fetcher.

### Cost notes (v1.0.0+)

scrape-extract runs against your Claude.ai subscription quota (Haiku model). Per careers page: ~12–50K input tokens, ~200–500 output tokens. For 5–10 scrape-tracked companies per run, total scrape token use is small relative to Pass 3 (compile-write) which spends most of the run's tokens on scoring.

## Step 2 — Read the profile filters

Read `./config/profile.json`. Extract:
- `role_types[].search_keywords` — terms to match against job titles
- `location_rules` — eligible modes (remote/hybrid) and excluded cities/countries
- `hard_exclusions.rules` — typed hard-filter rules

## Step 3 — Filter and score new_jobs

For each job in `new_jobs`, compute three filter signals — title match, location eligibility, and a regional remote score — then drop or keep accordingly. The regional remote score is preserved on the candidate so compile-write can use it later.

### 3a — Title match (hard filter)

Drop jobs whose title doesn't match at least one keyword from any `role_types[].search_keywords` (case-insensitive substring match).

### 3b — Region classification

The script exposes two helpers you should call rather than re-deriving the logic in prose: `classify_region(location)` returns the canonical region label, and `score_remote(workplace_type, region)` returns 0–3. Both are unit-tested in `tests/test_region.py` — that's the source of truth.

Conceptually they classify a location string into one of these canonical region labels, with PRAGUE / UK_IE / APAC / LATAM / MEA checked before NORTH_AMERICA / EU_NON_UK so narrow tokens (e.g. Dublin, Australia) don't fall through to broader buckets. Every candidate gets the SAME taxonomy of region labels — what changes per profile is which one is the candidate's *home region* (the privileged one in the score table). PRAGUE is just the label for "this Czech location" — it's not implicitly the candidate's home unless their profile says so.

- **PRAGUE**: location contains "prague", "praha", "czech", or "czechia"
- **EU_NON_UK**: any EU/EMEA city or country *other than* UK/Ireland — Germany, France, Spain, Netherlands, Belgium, Poland, Sweden, Denmark, Finland, Norway, Portugal, Italy, Austria, Ireland-the-country-only-when-not-Northern-Ireland-context (be conservative — leave Dublin/Cork out, see UK_IE), Berlin, Paris, Madrid, Amsterdam, Munich, Stockholm, etc.
- **UK_IE**: London, Manchester, Birmingham, Edinburgh, Glasgow, Leeds, Bristol, Belfast, Dublin, Cork, Galway, "United Kingdom", "UK", "Ireland", "England", "Scotland", "Wales"
- **NORTH_AMERICA**: "United States", "USA", "US", "Canada", "Toronto", "Vancouver", "New York", "San Francisco", "SF", "NYC", "Bay Area", "Seattle", "Boston", "Austin", "Chicago", "Los Angeles", "LA", "Denver", "Atlanta"
- **APAC**: "Singapore", "Tokyo", "Seoul", "Beijing", "Shanghai", "Hong Kong", "Sydney", "Melbourne", "Bengaluru", "Bangalore", "Mumbai", "India", "Japan", "Korea", "Australia", "China"
- **LATAM**: "Brazil", "Mexico", "Argentina", "Chile", "Colombia", "São Paulo", "Mexico City", "Buenos Aires"
- **MEA**: "Israel", "Tel Aviv", "Dubai", "UAE", "Saudi", "South Africa"
- **GLOBAL_REMOTE**: "global", "anywhere", "worldwide", "remote — global", "fully remote" with no region qualifier
- **UNKNOWN**: empty location string

### 3c — Regional remote score

Call `score_remote(workplace_type, region)` to get an integer 0–3. The score table is **dynamically built from the user's profile** at module import time (v2.2.2 — earlier versions hardcoded PRAGUE as the privileged home region, breaking the plugin for any non-Prague candidate).

The table builder (`build_score_table` in `scripts/fetch-and-diff.py`) takes three inputs:

- **`home_region`** — derived from `classify_region(profile.candidate.current_location)`. e.g. a Prague-based candidate gets `PRAGUE`; a Berlin-based one gets `EU_NON_UK`; a NYC-based one gets `NORTH_AMERICA`. If the candidate's location is unclassifiable or the profile is missing, this is `UNKNOWN` and the conservative fallback applies.
- **`eligible_regions`** — set derived from `profile.location_rules.eligible_regions`, each entry passed through `classify_region()`. Tells the builder which regions are acceptable beyond the home region.
- **`excluded_regions`** — set derived from `profile.location_rules.excluded_countries`, each passed through `classify_region()`. e.g. `["United Kingdom", "Ireland"]` → `{UK_IE}`.

The table follows this generic logic (concrete cells differ per profile):

| `workplace_type` | Region | Score |
|---|---|---|
| Remote | excluded region | 0 — filter out |
| Remote | home region | 3 |
| Remote | eligible region OR `GLOBAL_REMOTE` OR `UNKNOWN` | 3 |
| Remote | `NORTH_AMERICA` (when not home/eligible) | 2 — time-zone downgrade |
| Remote | any other region | 1 — low priority, kept |
| Hybrid | home region | 3 |
| Hybrid | anywhere else | 0 — can't commute |
| Onsite (or empty workplace_type) | excluded region | 0 |
| Onsite | home region | 3 |
| Onsite | eligible region | 1 — relocation downgrade |
| Onsite | anywhere else | 0 |

**Worked examples:**

- **Prague-based candidate** with `eligible_regions=["EU","Czechia","Global remote"]`, `excluded_countries=["United Kingdom","Ireland"]`:
  → home=PRAGUE, eligible={PRAGUE, EU_NON_UK, GLOBAL_REMOTE}, excluded={UK_IE}
  → Hybrid-PRAGUE=3, Hybrid-Berlin=0, Onsite-Berlin=1 (relocation), Remote-UK=0 (excluded), Remote-NYC=2.
- **Berlin-based candidate** with `eligible_regions=["EU","Global remote"]`:
  → home=EU_NON_UK, eligible={EU_NON_UK, GLOBAL_REMOTE}, excluded={}
  → Hybrid-Berlin=3, Hybrid-Prague=0 (out of commute), Onsite-Berlin=3, Onsite-Prague=1, Remote-NYC=2.
- **NYC-based candidate** with `eligible_regions=["NORTH_AMERICA","Global remote"]`:
  → home=NORTH_AMERICA, eligible={NORTH_AMERICA, GLOBAL_REMOTE}
  → Remote-NYC=3 (not 2 — it's home), Hybrid-NYC=3, Onsite-Berlin=0, Remote-Berlin=1.

If you need to verify a specific cell at runtime, call `score_remote(workplace_type, region)` — it uses the module-level table loaded from the user's profile. For tests or what-if analysis, pass explicit overrides: `score_remote(wt, region, home_region=..., eligible_regions=..., excluded_regions=...)`.

### 3d — Output the filtered list

Keep jobs where `regional_remote_score >= 1` and the title matched. Set on each candidate:

- `region`: one of the labels above
- `regional_remote_score`: 0–3
- `role_type_ids`: array of role_type ids whose keywords matched

Drop jobs flagged as **filter out** in the table above. Drop jobs that fail title match. Drop jobs whose location explicitly matches an `excluded_cities` or `excluded_countries` entry from profile.json.

Don't filter on `hard_exclusions.rules` here — those are applied by compile-write at Pass 3 (before scoring).

## Step 4 — Output

Return to the orchestrator:

```json
{
  "candidates": [...],
  "removed_jobs": [...],
  "filtered_out": [...],
  "static_notifications": [
    {
      "company": "Midjourney",
      "careers_url": "https://www.midjourney.com/careers",
      "note": "Always-hiring categories — surfaced once per profile change.",
      "roles": [{"id": "...", "title": "...", "category": "...", "description": "..."}]
    }
  ],
  "external_companies": [
    {
      "name": "Genspark",
      "careers_url": "https://www.genspark.ai/careers",
      "external_source": "wellfound",
      "external_url": "https://wellfound.com/jobs?company=genspark",
      "note": "..."
    }
  ],
  "skipped_companies": [
    {"name": "Midjourney", "ats": "static_roles", "reason": "..."}
  ],
  "stats": {
    "companies_total": 50,
    "companies_fetchable": 48,
    "companies_external": 1,
    "companies_skipped": 1,
    "companies_errored": 0,
    "total_jobs_fetched": 312,
    "new_jobs": 18,
    "candidates_after_filter": 6,
    "filtered_out": 12,
    "removed_jobs": 2,
    "run_date": "2026-04-28"
  },
  "fetch_errors": [{"company": "OpenAI", "error": "too_large"}]
}
```

### Field semantics — read carefully, do NOT conflate

- `candidates` — new jobs that PASSED Step 3's filter (title match + regional eligibility). These go to validate-urls. Each carries `region` and `regional_remote_score` (0–3) for downstream scoring.
- `removed_jobs` — jobs that DISAPPEARED from the ATS since the last run (the script's diff-based output, from `stats.removed_jobs`). These go to compile-write to mark **Closed** in the tracker. **On first run with empty state, this MUST be `[]` — there is no prior state to diff against.** Never populate this field with anything other than the script's authoritative `removed_jobs` array.
- `filtered_out` — new jobs that FAILED Step 3's filter (wrong region, no title-keyword match, excluded country, etc.). These are NOT closed; they're simply not surfaced. They're recorded here only so the orchestrator can report `filtered_out` count in the run summary. **Do NOT pass `filtered_out` to compile-write.** Confusing `filtered_out` with `removed_jobs` would silently mark thousands of live listings as Closed.
- `static_notifications` — surfaced once per profile change; **not** written to the tracker.
- `external_companies` — companies with no scrapeable endpoint; surface as a pointer in the digest.

Step 3's filter pipeline produces both `candidates` (passed) and `filtered_out` (rejected) — the union equals the script's `new_jobs`. `static_notifications` and `external_companies` pass through unchanged.
