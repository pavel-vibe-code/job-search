---
name: validate-favorites
description: >
  This skill should be used when the user wants to validate their favorites.json
  ATS endpoints, add a new favorite company, check if their ATS links are working,
  or fix a broken favorite. Trigger phrases include: "validate favorites",
  "check my ATS links", "add a favorite company", "test my favorites",
  "my favorite isn't working", "validate ATS", "check favorites".
metadata:
  version: "1.0.0"
---

Validate every entry in `favorites.json` against its configured ATS API endpoint.
For failures, try common slug variants automatically. Present a clear report and
offer to fix any issues in place.

## Step 1 — Run the validator

```bash
python3 ./scripts/validate-favorites.py --plugin-root .
```

## Step 2 — Present results

Show a concise table:

```
Favorites validation — {date}

✅  Linear          ashby/linear          21 jobs
✅  n8n             ashby/n8n             42 jobs
⚠️  Acme AI         ashby/acmeai          0 jobs   ← valid endpoint, no open roles right now
❌  BadSlug Co      ashby/badslug         404      → suggestion: greenhouse/badslugco (14 jobs)
🔧  MissingFields   (no ats/slug)         —        ← entry incomplete
🔵  Cyera           chrome                —        ← requires Claude in Chrome, no API
```

Status key:
- ✅ `ok` — endpoint live, jobs found
- ⚠️ `empty` — endpoint valid but 0 jobs (company may have no openings, or slug may still be wrong)
- ❌ `failed` — all slug variants tried, none worked — needs manual research
- ❌→✅ `failed_with_suggestion` — found a working alternative slug automatically
- 🔧 `misconfigured` — entry is missing `ats` or `slug` fields
- 🔵 `chrome` — `ats: chrome` entry, requires browser connector, nothing to validate via API

## Step 3 — Offer fixes

For each `failed_with_suggestion` entry: ask the user if they want to apply the suggested fix to `favorites.json`.

For each `misconfigured` entry: ask the user for the correct `ats` and `slug` values, then update the entry.

For each `failed` entry with no suggestion: tell the user the company's careers page needs manual inspection to find their ATS platform and slug. Offer to help find it if they provide the careers URL — read the page source and look for ATS embed patterns.

## Step 4 — Apply approved fixes

For any fixes the user approves, update `favorites.json` in place using the Edit tool. Preserve all existing fields (name, source, _comment, etc.) — only update `ats` and `slug`.

After applying fixes, re-run the validator to confirm the corrected entries now pass.

## Adding a new favorite

If the user wants to add a new company, ask for:
1. Company name
2. Careers page URL (to find the ATS)

Then:
- Fetch the careers page source and look for ATS embed patterns (Ashby, Greenhouse, Lever script tags)
- If found: extract the slug, probe the API endpoint to confirm, add to `favorites.json`
- If not found: run validate-favorites to try slug variants derived from the company name
- If still not found: add as `ats: chrome` with the careers URL, note it requires the Chrome connector
