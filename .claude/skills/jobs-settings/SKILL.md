---
name: jobs-settings
description: Edit any field in your AI 50 Profile interactively, one field at a time. Use this for ad-hoc updates without re-running the full setup wizard or hand-editing JSON. Preserves every field you don't explicitly change. Trigger phrases include "settings", "edit profile", "update profile", "change my settings", "tune my profile", "/jobs-settings".
---

## What this skill does

Your profile (location, role types, scoring options, hard exclusions, CV, etc.) is a single JSON document — in your AI 50 Profile Notion page (cloud mode) or `config/profile.json` (local mode). This skill surfaces every field as a settings menu so you can edit one thing at a time without:

- Hand-editing the JSON (typo risk; no validation)
- Re-running the full `jobs-setup` wizard (overwrites everything from your latest answers; forgets manually-added fields like `scoring.show_low`)
- Touching files you didn't intend to change

**Crucial property**: every write is a merge. The skill reads your full profile, updates only the field you edited, writes back. Fields you didn't touch — including manually-added ones the wizard doesn't ask about — are preserved.

## When NOT to use it

- Bulk re-design (e.g. completely rethinking your role types) — `jobs-setup` is faster for that
- Scoring-criteria tuning with guided dialogue and "let me adjust / re-think" loops — that's `jobs-recalibrate`'s specialty
- Tracker rows / extended companies — those are different artifacts (`jobs-rescore`, `jobs-extend-companies` respectively)

## Step 0 — Load context

Read `state/.setup_complete[deployment_mode]`:
- `cloud` — profile lives in the **AI 50 Profile** Notion page body. Run `notion-api.py discover` first (defensive cache refresh), then `fetch-page-body --page-id <profile_page_id>` and parse the JSON code block.
- `local` — profile lives in `./config/profile.json`. Read directly.

Print a one-line summary:

```
━━━ AI 50 Job Search — settings ━━━
Profile: {current_location} · {N} role types · {auth_method} · {deployment_mode}
CV-grounded categorical scoring (Match: High / Mid / Low) — `cv_json` present
```

## Step 1 — Top-level category menu

Print:

```
What would you like to change?

  1. Identity            — your location, languages, relocation preferences
  2. Role types          — what jobs to surface (titles, keywords, descriptions)
  3. Location rules      — work mode, regions, country/city exclusions
  4. Hard exclusions     — typed filter rules (language fluency, location locks, custom rules)
  5. Scoring             — model choice, show_low, instructions
  6. CV / context        — re-upload CV, edit narrative context paragraph
  7. Show full profile JSON  — read-only view, with copy-to-clipboard option
  8. Edit raw JSON       — power-user override; pastes full new profile (validates first)

Pick a number, or describe in plain English ("change my location", "show me hard exclusions", "switch to Sonnet").
```

Wait for response. Plain-English answers route to the corresponding category. Examples:
- "change my location" → Identity → current_location
- "switch to sonnet" → Scoring → model
- "show me my hard exclusions" → Hard exclusions → list (read-only display first)

## Step 2 — Dispatch on category

### Step 2a — Identity

Show current values, offer per-field edit:

```
Identity:
  • current_location:           "Prague, Czech Republic"
  • open_to_relocation.flag:    true
  • open_to_relocation.regions: ["EU", "UK"]
  • spoken_languages:           ["English", "Czech"]

Pick a field to edit (1-4), or 'back' to return.
```

**Per-field input handling:**

- `current_location` (string) — show current, ask for new value. Format: "City, Country" canonical (e.g. "Berlin, Germany" not "berlin germany"). Validate that the country is a real country name; suggest correction if obvious typo.
- `open_to_relocation.flag` (bool) — show current, ask yes/no. Toggle.
- `open_to_relocation.regions` (list[string]) — show current, ask for new comma-separated list. Empty list = "no regions specified". Use canonical region names (EU, UK, US, Canada, etc.).
- `spoken_languages` (list[string]) — show current, ask for new comma-separated list. Validate against common language names; suggest correction for typos. Important: this is a HARD filter — jobs requiring a language not on this list get dropped pre-scoring.

### Step 2b — Role types

```
Role types ({N} total):
  1. CX/Support/Services Leadership
  2. Head of AI / AI Ops
  3. AI Transformation
  4. Product Ops / non-traditional PM
  5. Chief of Staff
  6. Senior IC AI-first/FDE
  7. Operations Leadership
  8. Strategy & Ops

Pick one to drill into (1-{N}), or:
  + add a new role type
  - remove a role type (by number)
  e edit-via-JSON (paste full new array — replaces all)
  back
```

**Drill-into a role type:**

```
Role: CX/Support/Services Leadership
  • id: cx_leadership
  • label: "CX/Support/Services Leadership"
  • description: "Director/VP roles in customer experience, support, services..."
  • search_keywords: ["VP Customer Success", "Director Support", "Head of CX", "Customer Experience Director"]
  • priority: high

Pick a field to edit, or 'back'.
```

Each field is a free-text edit (string) or list-edit (search_keywords).

### Step 2c — Location rules

```
Location rules:
  • work_mode_description:  "remote in EU only, hybrid Berlin OK"  (free-form, drives the parser)
  • eligible_modes:         ["remote", "hybrid"]
  • eligible_regions:       ["EU"]
  • excluded_cities:        []
  • excluded_countries:     ["United Kingdom", "Ireland"]

Pick a field to edit, or 'back'.
```

**Special handling for `work_mode_description`**: it's free-form prose that the wizard's parser uses to derive `eligible_modes`, `eligible_regions`, `excluded_countries`. If the user edits this, ask: *"Want me to re-parse this and update eligible_modes / eligible_regions / excluded_countries to match? Or are you setting those separately?"*

**Validation for `excluded_countries`**: warn against meta-phrases ("all non-EU", "anything outside Europe") — these aren't canonical country names and break the regional matcher. Offer to expand: "all non-EU" → suggest the actual list of non-EU countries the user wants to drop ("United States, Canada, Australia, ...").

### Step 2d — Hard exclusions

The typed rule schema. Show current rules:

```
Hard exclusions ({N} rules):
  1. language_fluency_required: candidates require ["English"] (drop if other language fluency required)
  2. location_country_lock: drop if Country in {United Kingdom, Ireland}
  3. role_pattern_drop: drop if title contains any of {Sales, BDR, AE quota}

Pick a rule to edit (1-{N}), or:
  + add a new rule
  - remove a rule (by number)
  back
```

**Adding a rule** (Step 2d.add):

```
Pick a rule type:
  1. language_fluency_required   — drop if job requires non-listed language
  2. location_country_lock       — drop if job's country is in a list
  3. role_pattern_drop           — drop if title matches any of N keywords
  4. seniority_minimum           — drop below a seniority level
  5. (custom) free-form text rule — describe in plain English; the LLM applies it pre-scoring

Pick a number.
```

For each type, ask the type-specific parameters (e.g. for `location_country_lock`: comma-separated country list).

Validate canonical values (country names, language names, seniority labels).

### Step 2e — Scoring

```
Scoring settings:
  • model:        claude-opus-4-7  (default; ~$20-50 per pipeline run equivalent)
                  options: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-20251001
  • show_low:     false  (Low entries dropped from tracker by default)
  • instructions: "cap non-Prague EU at Mid"  (free-form hint to scoring prompt)

Pick a field to edit, or 'back'.
```

**Per-field edit:**

- `model` — show options, ask user to pick. Store the canonical model ID. Offer cost framing: "Opus is ~5× more expensive than Sonnet; Haiku is cheap but lower quality. Most users stick with Opus."
- `show_low` — toggle bool. Show effect: *"When true: every candidate's verdict (including Low) is written to your tracker, with Match: Low. When false: Low entries are dropped from tracker but counted in the run summary. Default false."*
- `instructions` — free-form edit. Show current as preview. Note: this gets injected into the scoring prompt; keep concise (1-2 sentences).

### Step 2f — CV / context

```
CV / context:
  • context: (~340 chars)
    "I'm a Director of Customer Experience with 8 years in B2B SaaS. Background
    in support ops, scaling teams 0→1, and AI-native tooling. Currently based..."
    (full text — show first ~200 chars, offer 'view full' / 'edit' / 'back')

  • cv_json: present (parsed from CV upload during setup)
    summary: 6 work experiences, 3 education entries, ~40 skills extracted.
    Options: re-upload CV (PDF or text), view current cv_json.
```

**Re-upload CV**: ask the user to drop a file path or paste text. Send to the same parsing pipeline used in setup Step 3.5; show the extracted JSON for confirmation; replace `cv_json`.

**Note**: cv_json cannot be removed via this skill — the scoring system requires it. To replace your CV, use re-upload. To start over with a different profile, run `jobs-setup`.

### Step 2g — Show full profile JSON

Read-only display. Pretty-print with line numbers. At the end:

```
This is your full profile. Type 'edit' to dive into a category, 'back' to return to menu, or 'copy' to copy the JSON to your clipboard (if Bash supports it).
```

### Step 2h — Edit raw JSON (power user)

```
⚠️  Power-user mode. Paste your full new profile JSON below.
This will validate the structure (must be valid JSON, must have required keys: candidate, location_rules, role_types, scoring) and ABORT if validation fails.

Paste now (end with a blank line):
```

Validate:
- Must parse as JSON
- Must have `candidate`, `location_rules`, `role_types`, `scoring` top-level keys
- `candidate.current_location` must be non-empty string
- `candidate.spoken_languages` must be non-empty list
- Every `role_types[].id` must be a non-empty string

If validation fails: print errors and re-prompt.
If validation passes: write back, print confirmation.

## Step 3 — Persist (merge, never full overwrite)

After any field edit:

1. Re-read the full profile (in case anything changed since Step 0)
2. Update only the edited field (or array entry / nested object)
3. Write back the full merged JSON

**Cloud mode:**
```bash
echo '```json' > /tmp/profile_new.md
python3 -c "import json; print(json.dumps(updated_profile, indent=2, ensure_ascii=False))" >> /tmp/profile_new.md
echo '```' >> /tmp/profile_new.md

python3 ./scripts/notion-api.py update-page \
  --page-id <profile_page_id> \
  --replace-content /tmp/profile_new.md
```

**Local mode:**
```bash
python3 -c "import json; json.dump(updated_profile, open('./config/profile.json', 'w'), indent=2, ensure_ascii=False)"
```

Verify the write by re-reading and confirming the change landed.

## Step 4 — Summary print

```
━━━ Settings updated ━━━

Changed: 1 field
  scoring.show_low: false → true

Other 47 fields unchanged.

The next pipeline run (or "run the job search" now) will use the updated setting.
```

## Step 5 — Suggest follow-ups

If user changed scoring-related fields (model, show_low, instructions, criteria):
- Suggest running `re-score` to re-evaluate existing tracker rows against the new setting

If user changed identity / location / role types:
- Suggest running `run the job search` to see how the new filter affects this run's candidates

If user changed hard_exclusions:
- Note: these only apply to NEW candidates in future runs; existing tracker rows are not retroactively filtered. To retroactively check: run `re-score` (it re-applies hard exclusions).

## Failure modes

- **Notion fetch failure**: report cleanly, don't proceed with edits
- **JSON parse failure on existing profile**: profile is corrupt; abort with directions to re-run `jobs-setup` (don't try to repair automatically)
- **Validation failure on user input**: re-prompt, don't write
- **Notion write failure**: report; the profile in Notion is unchanged. Don't fall back to local file (would create source-of-truth drift between cloud and local).

## Token usage tracking

Mostly deterministic — the skill is dialog + JSON manipulation. No LLM calls expected unless:
- Re-uploading a CV → uses the same CV parser as setup Step 3.5 (one Haiku call)
- Custom hard-exclusion rule type 5 → user describes a rule in plain English; LLM converts to typed rule structure (one small Sonnet call)

If any LLM calls were made, print a usage block in Step 4 summary.
