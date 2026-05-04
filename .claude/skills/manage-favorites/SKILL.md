---
name: manage-favorites
description: Add, remove, or update entries in the user's AI 50 Favorites list — interactive dialogue, no JSON editing. Auto-detects ATS from pasted careers-page URLs via the v3.1.0 ats_adapters registry. Supports bulk-paste of URLs (all-at-once add) and inline editing. Persists changes to Notion (cloud mode) or config/favorites.json (local mode). Invoke when the user says "manage favorites", "add a favorite", "remove a favorite", "change favorites", "edit favorites", or similar phrasing.
version: 3.3.0
---

## What this skill does

Replaces the friction-prone "edit the JSON code block in the Notion AI 50 Favorites page" workflow with an interactive dialog:

- **Add**: paste a careers-page URL → skill derives ATS+slug deterministically, proposes the entry, confirms, saves.
- **Bulk add**: paste several URLs (one per line) → skill processes each, shows a single confirmation diff, saves all atomically.
- **Remove**: by name match (exact or partial), with confirmation showing what'll be removed.
- **Update**: change ATS / slug / careers_url for an existing entry without retyping the rest.
- **List**: show current favorites in readable form with summary stats (N entries, M with `ats: skip`, etc.) — useful before deciding what to change.

The skill knows about the v2.5.0 `careers_url` field and the v3.2.0 `scrape` ATS, so it composes properly-formed entries that the rest of the pipeline (fetch-and-diff, validate-jobs) consumes correctly.

## When NOT to invoke

- Initial setup of the plugin — that's the `setup` skill's job (Step 7 collects favorites as part of the wizard flow).
- Bulk re-categorization of >50 entries — at that scale, dump-and-reload via direct JSON edit is faster than dialog. (Threshold judgment call; ask user if you're unsure.)
- The user wants to permanently delete a favorite from history — this skill removes from the active list but doesn't audit-trail. Soft delete only.

## Step 0 — Determine deployment mode + load context

Read `state/.setup_complete[deployment_mode]`:
- `"cloud"` — favorites live in the **AI 50 Favorites Notion page** body (JSON code block). Source-of-truth.
- `"local"` — favorites live in `./config/favorites.json` (gitignored since v2.4.0). Source-of-truth.

Look up the favorites page ID:
1. **Run `notion-api.py discover`** first to refresh `state/cached-ids.json` (defensive — same as feedback-recycle Step 1; per-installation caches drift).
2. Read `state/cached-ids.json[favorites_page_id]`.

Load the current favorites array. Cloud: `notion-api.py fetch-page-body --page-id <favorites_page_id>` and parse the JSON code block. Local: `json.load(open('./config/favorites.json'))`.

If the data has a `_meta` first entry (the v2.x convention): preserve it through writes, but skip it when listing/searching.

## Step 1 — Ask intent

Print:

```
━━━ AI 50 Favorites — manage ━━━
Currently {N} entries ({M} with ats=skip — auto-detection failed for those at setup time)

What would you like to do?
  1. Add new favorite(s) — paste a careers URL or list of URLs
  2. Remove a favorite — by name
  3. Update a favorite — change ATS / slug / careers_url
  4. List all favorites (sorted) — see what's there
  5. Cleanup `ats: skip` entries — go through each and either upgrade or remove

Pick a number, or describe what you want in plain English.
```

Wait for user response. Plain-English answers ("add Adfin", "remove the skip ones") get parsed into the appropriate intent.

## Step 2 — Dispatch on intent

### Step 2a — Add (single or bulk)

Print:

```
Paste careers page URL(s). One per line. Press Enter twice when done.
You can mix supported and unsupported ATS — I'll figure out which is which:

  https://job-boards.greenhouse.io/cohere/  (auto-detected: greenhouse, slug=cohere)
  https://botify.teamtailor.com/jobs/        (auto-detected: teamtailor, slug=botify)
  https://example.com/careers                (custom domain → ats=scrape)
  Anthropic                                  (no URL → I'll ask you for it)
```

For each line:

1. **If line looks like a URL**: call `ats_adapters.ats_from_url(line)`:
   - Returns `(ats, slug)` → derive `{name: ?, ats, slug, careers_url: line}`. Ask user to confirm the company name (the URL's slug isn't always the right display name — e.g. `togetherai` should be `Together AI`).
   - Returns `None` → URL doesn't match a known ATS pattern. Offer two options:
     - `ats: "scrape"` (LLM extraction at fire time, ~$0.01-0.04 per fire)
     - `ats: "skip"` (preserved as a placeholder, doesn't fetch — user can come back later)
2. **If line is just a name (no URL)**: ask user to paste the careers URL for that company.

After processing all lines, show the proposed batch:

```
Proposed additions ({N} entries):
  1. Cohere  (greenhouse, slug=cohere)             ← auto-detected from URL
  2. Botify  (teamtailor, slug=botify)              ← auto-detected from URL
  3. Adfin   (scrape, careers_url=https://adfin.com/careers#open-positions)
  4. (skipped — Anthropic, no URL provided)

Confirm? (yes / let me adjust / cancel)
```

On `yes`: append to favorites array, write back to source-of-truth, print success summary.
On `let me adjust`: ask which entry, take the correction, regenerate diff.
On `cancel`: exit without writes.

### Step 2b — Remove

Print:

```
Which favorite(s) to remove? Type a name (exact or partial match) or "ats=skip" to remove all skip-tagged entries:
```

Show match preview before deletion:

```
You typed "adfin". This matches:
  • Adfin  (ats=scrape, careers_url=https://adfin.com/careers#open-positions)

Remove? (yes / cancel)
```

If multiple matches: list them, ask user to refine OR confirm batch removal of all matches.

### Step 2c — Update

Ask which entry to update (by name match), then which field to change. Common patterns:

```
Update "Adfin":
  Current: {name: "Adfin", ats: "scrape", slug: "adfin", careers_url: "https://adfin.com/careers#open-positions"}

What would you like to change?
  - ATS         (currently: scrape)
  - Slug        (currently: adfin)
  - careers_url (currently: https://adfin.com/careers#open-positions)
  - Name        (currently: Adfin)

Or paste a new careers URL — I'll re-derive ATS+slug from it.
```

If user pastes a new URL: re-run `ats_from_url` and propose the resulting entry; confirm.

### Step 2d — List

Print all favorites sorted by name, grouped by ATS:

```
━━━ AI 50 Favorites (64 entries) ━━━

ashby (12):
  • Together AI       slug=togetherai
  • ...

greenhouse (18):
  • Cohere            slug=cohere
  • Parloa            slug=parloa  (EU subdomain)
  • ...

teamtailor (3):
  • Botify            slug=botify
  • ...

skip (25):  ← needs manual cleanup; consider running step 2e to address
  • Adfin
  • Aikido Security
  • ...

scrape (6):
  • Bondio            careers_url=https://bondio.com/careers
  • ...
```

Read-only — no writes. Useful as a precursor to remove/update.

### Step 2e — Cleanup `ats: skip` (interactive walkthrough)

For each `ats: skip` entry, in order:

```
[3 of 25] Aikido Security  (currently ats=skip)

Options:
  1. Paste a careers page URL — I'll auto-detect ATS
  2. Mark as scrape with a careers URL (LLM extraction every fire)
  3. Remove this favorite entirely
  4. Skip (keep as ats=skip, decide later)

What would you like to do?
```

Loop through all skip-tagged entries. Track changes in memory; write back at end of walkthrough or whenever user says "save and continue later" / "save now".

## Step 3 — Persist changes

Build the updated favorites array:
- Preserve any `_meta` first-entry the original had.
- Apply add/remove/update mutations.
- Sort by name (case-insensitive) for display readability when next opened in Notion.

Write back:

**Cloud mode:**
```bash
# Wrap the array in a JSON code block (matches what setup wizard wrote originally).
echo '```json' > /tmp/favorites_new.md
python3 -c "import json; print(json.dumps(updated_favorites, indent=2, ensure_ascii=False))" >> /tmp/favorites_new.md
echo '```' >> /tmp/favorites_new.md

python3 ./scripts/notion-api.py update-page \
  --page-id <favorites_page_id> \
  --replace-content /tmp/favorites_new.md
```

**Local mode:**
```bash
python3 -c "import json; json.dump(updated_favorites, open('./config/favorites.json', 'w'), indent=2, ensure_ascii=False)"
```

Verify the write by re-reading and confirming the change landed.

## Step 4 — Summary print

```
━━━ Favorites updated ━━━

Added:    {N entries — list names}
Removed:  {N entries — list names}
Updated:  {N entries — list names}

Total favorites: {old_count} → {new_count}

The next Routine fire (or local "run the job search") will use the updated list.
{If any added entries are scrape: "Note: scrape entries use Claude Haiku per fire (~$0.01-0.04 each) — see CHANGELOG v3.2.0 for cost framing."}
```

## Step 5 — Suggest follow-ups

If the user added scrape entries: suggest watching the next Routine fire's tracker output to confirm the LLM extraction quality is acceptable; iterate via this skill if not.

If they cleaned up many skip entries: print *"Want to recycle feedback after your next fire? The new favorites' jobs will surface in the tracker — labeling a few will train the LLM scoring on this expanded pattern set."*

---

## Token usage tracking (v3.0.5+)

Track LLM calls if any were made (typically: skill is mostly deterministic — URL regex + JSON manipulation. Only LLM call would be if the user wants help inferring company name from URL or similar). Print usage block in Step 4 summary if `usage > 0`; omit if zero.

## Common edge cases

- **User pastes a URL with hash anchor** (e.g. `https://adfin.com/careers#open-positions`): hash anchors are valid in URLs and don't affect ATS detection — pass the URL through as-is to `ats_from_url`. The hash is preserved in `careers_url`.
- **User pastes a JD-specific URL instead of careers index** (e.g. `boards.greenhouse.io/cohere/jobs/12345` instead of `boards.greenhouse.io/cohere`): the regex extracts the slug correctly (group 1 = `cohere`); the `careers_url` field is set to the JD-specific URL. fetch-and-diff calls the API with the slug regardless of what the user-facing URL points at, so this works. Optionally normalize careers_url to the index page for tidiness.
- **Same company added twice**: dedup by name (case-insensitive) before write. Show user *"You already have Cohere in favorites — update existing entry?"*
- **Company is in companies.json (the AI 50 baseline) AND user adds it as favorite**: warn user — companies.json wins per fetch-and-diff precedence, so the favorites entry would be ignored. Suggest skipping or use companies.json updates instead (rare case, just the AI 50 list anyway).
