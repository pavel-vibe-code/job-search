---
name: jobs-setup
description: >
  First-time setup wizard for the AI 50 Job Search plugin. Guides the user through
  configuring their candidate profile, search preferences, and Notion connector.
  Trigger phrases include: "set up the plugin", "run setup", "configure the plugin",
  "run onboarding", "guided setup", "start onboarding", "walk me through setup",
  "first time setup", "setup wizard", "I just installed this".
metadata:
  version: "2.3.0"
  edition: "Claude Code / Routines"
---

Interactive setup wizard. Collects profile, ranking logic, additional context, and Notion connector info. Writes config files and creates a setup sentinel so this wizard is skipped on future runs. Can also be triggered manually at any time to reconfigure.

## Step 0 — Check if already configured

Check for `./state/.setup_complete`.

If it exists and this skill was **explicitly triggered by the user** (not auto-invoked from jobs-run), print:

```
✅ Plugin already configured (setup completed on {date from file}).

To reconfigure from scratch: delete state/.setup_complete and run "set up the plugin" again.
To update your profile: edit config/profile.json directly (local mode) or the AI 50 Profile page in Notion (cloud mode).
To update your connector: edit config/connectors.json directly.
```

Then stop.

If the sentinel already exists and setup was auto-invoked from jobs-run, skip all steps and return immediately so the search can proceed.

---

## Step 1 — Welcome + deployment mode

Print this header exactly:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AI 50 Job Search — First-time Setup
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

I'll walk you through ~10 questions to configure:
  1. Your profile (location, work mode, target roles, languages)
  2. Your background & context (used in scoring)
  3. Scoring criteria + priorities (you describe; I propose weights)
  4. Notion connector (auth + databases)

All answers go into config/profile.json + config/connectors.json (local mode),
or into Notion pages (Cloud Routine mode). You can edit either at any time.
```

Then print:

```
This plugin can run in two modes:

  • Cloud Routine — your profile, custom companies, and tracking state all live in
    Notion. The plugin repo stays generic and shareable. Pick this if you want
    scheduled cloud runs via Claude Code Routines (claude.ai/code/routines).
    To update your profile later, edit the Notion page directly.

  • Local — your profile and custom companies live in this folder's config/*.json
    files. State goes either to a local file or to a Notion database.
    Pick this for interactive use on your own machine.
```

Call `AskUserQuestion`:

- Question: "Which deployment mode?"
- Header: "Mode"
- Options:
  - label: "Cloud Routine", description: "Profile + custom companies + state in Notion. Repo stays generic. Best for scheduled runs."
  - label: "Local", description: "Config in this folder. State local or Notion."

Record the choice as `deployment_mode` (cloud / local). This affects:
- Step 5 — cloud mode auto-creates Profile + Favorites pages in addition to Tracker DB / Hot Lists / State DB; local mode creates only the latter (and state DB is optional).
- Step 6 — cloud mode writes profile/custom-companies JSON into the Notion pages instead of into config/*.json.

Continue to Step 2.

---

## Step 2 — Candidate profile

Ask these questions **one at a time** (wait for each answer before asking the next). Do not suggest or pre-fill values as defaults — present only labeled examples. Do **not** ask for the candidate's name — it isn't used in scoring or output.

**Q1:** "What city and country are you based in?
(Example: 'Berlin, Germany' or 'Singapore')"

**Q2:** "Are you open to relocating for a job? If yes, where would you consider moving — region, countries, or specifics?
(Examples: 'EU only — visa OK', 'no, I want to stay where I am', 'open to US/Canada or EU', 'open to any English-speaking country')"

Parse into:
- `candidate.open_to_relocation.flag`: bool
- `candidate.open_to_relocation.regions`: array of regions/countries the user named (empty if flag is false)

**Q3:** "What's your preferred work mode? Describe in free form — include any nuances about location, residency, or country requirements.
(Examples:
  'remote only, must be open to EU residents',
  'hybrid in Prague preferred, EU-remote also fine',
  'on-site OK if relocation is covered, otherwise remote-EU',
  'hybrid only, my city')"

This is where the user expresses any country-residency restrictions for remote roles ('remote, but only EU' style). Parse into:
- `location_rules.work_mode_description`: full free-form answer (verbatim, passed to the scoring agent)
- `location_rules.eligible_modes`: derive `["remote", "hybrid", "on-site"]` subset based on what they said
- `location_rules.eligible_regions`: derived array of CANONICAL region/country names (e.g. `["EU"]`, or `["Portugal", "Spain", "EU"]`). Maps cleanly through `classify_region()` in fetch-and-diff.py — keep entries to country names, region tags ("EU", "EMEA"), or city names. NO meta-phrases.
- `location_rules.excluded_countries`: ONLY explicit countries the user named to exclude (e.g. `["United Kingdom", "Ireland"]`).

**Critical wizard hygiene:** `excluded_countries` MUST contain only canonical country names. Do NOT emit meta-phrases like `"all non-EU"`, `"anything outside Europe"`, `"no other parts of the world"` — these break `classify_region()` (the `non-EU` string falsely matches `\beu\b` because the hyphen is a word boundary, leading to the candidate's HOME region being silently excluded). The implicit semantics of "EU only" come from `eligible_regions = ["EU"]` being narrow; you don't need to enumerate the negative space. If the user says "EU only, nothing else", emit:
```json
"eligible_regions": ["EU"],
"excluded_countries": ["United Kingdom", "Ireland"]
```
NOT:
```json
"excluded_countries": ["United Kingdom", "Ireland", "all non-EU countries"]
```

If anything is ambiguous, re-ask one targeted clarifying question rather than guessing.

**Q3.5 — Hard exclusions for remote roles:** "Are there countries or regions you'd reject even for remote roles? I.e. if a job is 'Remote — US only' or 'Remote — Brazil', do you want it dropped before scoring, or is geography irrelevant for remote?

(Examples:
  'EU only — drop anything country-locked outside EU',
  'no, anywhere remote is fine for me',
  'reject US, Brazil, India, anything Asia-Pacific',
  'open to anywhere except UK and Ireland')"

This is the *symmetric* question to Q3. Q3 captured what the user is eligible for (positive list); Q3.5 captures what they actively reject (negative list). Both are needed because they answer different questions: "where can I work?" vs. "where will I never work?"

Parse Q3.5 into a **typed `hard_exclusions` rule** the runtime applies deterministically before scoring (introduced v2.5.0 schema):

| User intent | Rule emitted |
|---|---|
| "EU only, drop everything else" | `{"type": "remote_country_lock", "eligible_remote_regions": ["EU"]}` |
| "no, geography irrelevant for remote" | (no `remote_country_lock` rule) |
| "reject US, India, APAC" | `{"type": "remote_country_lock", "reject_remote_in": ["United States", "India", "Asia-Pacific"]}` |
| "open to anywhere except UK and Ireland" | `{"type": "remote_country_lock", "reject_remote_in": ["United Kingdom", "Ireland"]}` |

Hold this rule in memory; it gets written into `profile.hard_exclusions.rules` in Step 6.

**Critical:** without this question, free-text Q3 like "EU only" gets translated only into a positive `eligible_regions` list — non-EU remote slips through silently. Q3.5 forces the symmetric capture.

**Q4:** "What types of roles are you targeting? Describe them in plain language — I'll structure them into search keywords.

(Examples: 'VP or Director level Customer Success or Support', 'Head of AI or AI Operations', 'Founding PM at early-stage AI companies')

List as many role types as you want, one per line. These become the keyword filters for every search run."

For each role type described, generate:
- `id`: a slug (e.g. `cx-leadership`)
- `label`: a short human-readable label
- `description`: one sentence
- `search_keywords`: 6–10 relevant job title strings to match against ATS listings

Show the generated role_types back to the user and ask them to confirm or adjust before moving on.

**Q5:** "What seniority level are you targeting? Describe briefly.
(Examples: 'Director or VP level, 10+ years experience', 'Senior IC, 6 years', 'C-suite or VP only')"

**Q6:** "Which languages do you speak well enough to work in?
List all of them. **Any job that explicitly requires fluency in a language NOT on this list will be filtered out entirely** — the plugin won't include it in your tracker or hot list.
(Examples: 'English', 'English, Czech', 'English, German, French')"

Parse into `candidate.spoken_languages` (array of canonical language names). Note: this is a **hard exclusion**, not a soft penalty — make sure the user knows this before answering.

---

## Step 3 — Background & context

**Q7:** "Tell me about your background, goals, and anything else that should influence how jobs are filtered and ranked. This is passed to the scoring agent on every run.

Be as specific as you want. Examples:
- Years of experience and current role level
- Industries or domains you've worked in
- Types of companies you prefer (stage, size, sector)
- Specific tools, skills, or approaches that are relevant
- Anything you want to prioritize or avoid

This is a free-form field — write as much or as little as you like."

Store the full answer verbatim as `"context"` in profile.json. This field has no structure requirement — it's passed as-is to the scoring agent.

---

## Step 3.5 — CV / LinkedIn upload (required)

Print:

```
Upload your CV or LinkedIn profile ('Save to PDF' export). The scoring
system uses this as the substantive grounding for every tracker verdict —
each entry will show which of your actual experiences match the JD and
which are gaps.

Either:
  - Paste a file path to a PDF
    (e.g. /Users/me/Documents/cv-2026.pdf or ~/Downloads/Linkedin-Profile.pdf)
  - Or paste your CV/LinkedIn text directly (start typing, end with a blank line)
```

Wait for input. CV is mandatory — without it, the scoring system has no
substance to match against. If the user truly resists, explain again and
ask: *"A CV is required for the plugin to score jobs against your profile.
Without one, every job would just match against your free-form context
paragraph (sparse signal). Try a quick PDF export from LinkedIn (~10 sec)
or paste any text version of your work history. Even a partial CV beats
none."*

If user provides a path:

1. **Read the PDF** using Claude Code's native PDF reading capability. The Read tool accepts `.pdf` paths and returns extracted text + structure.

2. **Extract structured JSON** by invoking your own LLM judgment on the extracted text. Build a single one-shot prompt like:

   ```
   Convert this CV text into the canonical JSON schema below. Be specific in
   achievements (preserve numbers and metrics). For extracted_keywords, list
   ~30 phrases — both technical (tools, technologies, methodologies) and
   functional (role types, industry segments, leadership scope, domain expertise).

   Schema:
   {
     "extracted_at": "{today, ISO 8601}",
     "source_format": "{cv | linkedin_pdf}",
     "summary": "...",
     "experience": [...],
     "skills": {...},
     "education": [...],
     "career_signals": {...},
     "extracted_keywords": [...]
   }

   CV text:
   {extracted_text}
   ```

   See `ARCHITECTURE.md §7.5` for the full canonical schema with field semantics.

3. **Show the extracted JSON to the user** in a readable form (don't dump raw JSON; format the highlights):

   ```
   ━━━ CV extracted ━━━
   Summary:    {summary}
   Experience: {N entries spanning {from} → {to}}
   Skills:     {leadership, technical, domain counts}
   Keywords:   {first 10 of extracted_keywords}, +{N} more
   Career signal: {seniority_level}, {years_experience_total} years,
                  {function_focus[0]}, based in {geographic_base}

   Anything missing or wrong? (yes / looks good)
   ```

4. **If user says "yes"** — ask which field they want to fix, take their correction, regenerate that section, show again. Loop until "looks good."

5. **Store as `profile.cv_json`** in memory; written to profile during Step 6.

**If PDF read fails** (corrupt, password-protected, etc.): tell the user *"Couldn't read that PDF. Try a different file (e.g. re-export from LinkedIn), or paste your CV text directly."* Do NOT proceed without a CV.

---

## Step 4 — Scoring instructions (optional hint to the LLM)

Now that the CV is captured, scoring is grounded in the structured cv_json
plus your free-form context paragraph from Step 3. The LLM-judged categorical
verdict (High / Mid / Low) emerges from those two — no manual rubric needed.

Optionally ask:

```
Any scoring hints you want to give the LLM? Free-form, 1-2 sentences max.

Examples:
  - "Be strict on AI-native vs. AI-bolt-on; downgrade non-AI-core roles."
  - "Reward customer-facing leadership over IC roles."
  - "Cap any non-Berlin EU role at Mid — I prefer hybrid local."
  - "" (skip — let the LLM infer entirely from CV + context)
```

Store the response (or empty string) as `profile.scoring.instructions`. The LLM uses this as a top-level steering hint when scoring; concise rules are best.

**Cost framing (CV-grounded scoring):** the LLM-judged categorical scoring uses Claude Opus 4.7 with extended thinking (4000-token budget) by default — the highest-quality model for multi-criteria evaluation. Per pipeline run, expect on the order of subscription-quota use equivalent to **$20–50 in pay-per-token rates** for ~50 candidates. To cut quota usage ~75% with a small quality drop, the user can later edit their AI 50 Profile Notion page to add `"scoring.model": "claude-sonnet-4-6"`. Mention this at the end of setup so the user has the context; don't make it a wizard question (Opus is the right default for most users).

---

## Step 5 — Notion connector

The plugin's only fully-supported destination connector is Notion. Markdown output exists as a **fallback** for when Notion writes fail (network errors, auth drift, API outages) — not as a deliberate user choice. Skip directly to auth-method selection.

### Step 5-pre — Auth method

Print:

```
Notion can authenticate two ways. Both work locally AND in Cloud Routines —
Routines do support connectors. The real difference is reliability.

  • MCP (OAuth, no token to manage)
    - Plug-and-play if you already have Notion connected in Claude Code
      Connectors, or installed via the CLI.
    - Each Notion operation is a separate agent tool call. A typical run
      makes 100+ tool calls. Per-call failure rates compound: at 99.5%
      per-call success, expect ~50% run-success at this volume.
    - Fine for occasional interactive laptop use; risky for scheduled
      Routines where partial failures need to be diagnosed by hand.
    - Tools live under a server-specific prefix that the orchestrator
      resolves at run time (connector UUIDs can rotate on reconnect).

  • API token (recommended, especially for Routines)
    - You mint an integration token at notion.so/profile/integrations.
    - Plugin uses scripts/notion-api.py — bulk Notion operations happen
      via threaded HTTP inside one CLI call instead of fanning out as 100+
      agent tool calls. Run-success >95% even on a 50-company scan.
    - Works everywhere — laptop, Routine container, CI.
    - One extra setup step: share each parent page with the integration
      via Notion's Connections menu (otherwise the integration sees
      nothing).
```

Call `AskUserQuestion`:
- Question: "How should the plugin authenticate to Notion?"
- Header: "Auth method"
- Options:
  - label: "MCP (OAuth)" — description: "No token to mint, but ~50% run-success at 100+ tool calls. Fine for laptop; fragile for Routines."
  - label: "API token (recommended)" — description: "One CLI call replaces 100+ tool calls. >95% run-success. Required if you want reliable scheduled Routines."

Record the answer as `connectors.notion.auth_method` (`"mcp"` or `"api_token"`).

If `auth_method == "api_token"`: skip Step 5a entirely. Jump to **Step 5a-token** below.

If `auth_method == "mcp"`: continue to Step 5a (the existing MCP detection cascade).

#### Step 5a-token — API token onboarding (auth_method == "api_token")

Print:

```
You'll need a Notion integration token. Step-by-step:

  1. Open https://www.notion.so/profile/integrations
  2. Click "+ New integration"
  3. Name: "AI 50 Job Search" (or any name you'll recognise)
  4. Workspace: pick the workspace where you want the tracker to live
  5. Capabilities: leave defaults (Read content, Update content, Insert content)
  6. Click "Save", then copy the "Internal Integration Token"
     (starts with 'secret_' or 'ntn_')

After you have the token, you'll also need to GIVE the integration access to
the parent page where the plugin will create databases. That's a separate step:

  1. Open the Notion page you want to use as the parent (e.g. "Job Search")
  2. Click ••• in the top right → Connections → Add connections
  3. Search for "AI 50 Job Search" and click Add
  4. Confirm. The integration now has read/write access to that page tree.

Without this permission grant, the integration sees no pages — every API call
will return "object_not_found".
```

Ask the user to paste the token. Once received:

1. **Validate** by calling `python3 ./scripts/notion-api.py users-me --token <pasted>`. If exit 0 with `ok: true` and a workspace name → token is valid. If exit 2 → token is invalid; ask them to re-paste.
2. **Persist**:
   - Write the token to `~/.config/ai50-job-search/notion-token`. `chmod 0600`.
   - Set `connectors.notion.api_token_file = "~/.config/ai50-job-search/notion-token"`.
   - Tell the user: *"Saved. For Cloud Routines, also set `NOTION_API_TOKEN` as a routine env var so the Routine container can read it without your local file."*
3. **Skip 5a.1–5a.4** — no MCP detection needed in token mode. Set `mcp_tool_prefix = null` and `install_method = null` to make the absence explicit.

Then jump to Step 5b (database setup), but use the API helper (`scripts/notion-api.py search`, `create-database`, `create-pages`) instead of MCP tool calls. The data flow is the same — only the transport differs.

#### Step 5a — Detect (or install) the Notion MCP (auth_method == "mcp")

Before any database creation, confirm the Notion MCP is reachable from this Claude Code installation. The plugin needs to know the **actual tool prefix** because Claude Code namespaces MCP tools as `mcp__<server-id>__<tool-name>` and the `<server-id>` differs depending on install method:
- **CLI install** (`claude mcp add notion ...`) → server-id is the literal `notion` → prefix is `mcp__notion__`.
- **Connector install** (Connectors panel in the IDE) → server-id is a UUID generated at install time → prefix is `mcp__<32-hex>__`. UUIDs can change on server reconnect.

If we don't capture the right prefix now, agent calls to `mcp__notion__notion-create-pages` will silently 404 at write time and the plugin will fall back to markdown without telling the user.

**Detection cascade — try each step in order; stop at the first one that succeeds:**

##### 5a.1 — Probe the live tool inventory (covers connector installs)

Use `ToolSearch` with query `"notion-search"` (or `"notion"` more broadly). Read the returned tool names. If any have the form `mcp__<server-id>__notion-search`, capture `<server-id>` — that's the resolved server name. The prefix is `mcp__<server-id>__`.

Heuristic for `install_method`:
- If `<server-id>` matches `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` → `connector`.
- Otherwise (e.g. `notion`, `notionhq`, etc.) → `cli`.

Skip 5a.2 if this step succeeds.

##### 5a.2 — CLI registry fallback (covers CLI installs that haven't loaded into the live session yet)

Run the detector script:

```bash
python3 ./scripts/detect-notion-mcp.py
```

Returns JSON: `{"detected": bool, "name": "...", "prefix": "...", "install_method": "cli"|"connector", ...}`. Exit 0 = found, 1 = not found, 2 = `claude` CLI itself unavailable.

If found, capture `prefix` and `install_method` from the output.

##### 5a.3 — Offer auto-install (only if 5a.1 AND 5a.2 both came back empty)

Print:

```
I couldn't find Notion MCP in this Claude Code installation. I can install it
for you via the CLI:

  claude mcp add notion --transport sse https://mcp.notion.com/sse

After install, you'll need to authenticate Notion in the browser tab that opens.
```

Call `AskUserQuestion`:
- Question: "Install Notion MCP via CLI now?"
- Header: "Install MCP"
- Options:
  - label: "Yes, install it" — description: "Run `claude mcp add notion ...` and walk through OAuth"
  - label: "I'll handle it manually" — description: "Pause setup; I'll install Notion MCP myself and re-run setup later"

If the user picks "Yes, install":
1. Run `claude mcp add notion --transport sse https://mcp.notion.com/sse` via Bash.
2. Tell the user to complete OAuth in the browser, then say "ready" when done.
3. Re-run the detection cascade from 5a.1.

If after retry the cascade still fails, OR the user picked "I'll handle it manually":
- Stop setup. Print: *"Notion MCP not available. Install it (`claude mcp add notion --transport sse https://mcp.notion.com/sse` or via the Connectors panel in the IDE), then re-run 'set up the plugin' to continue from this step."*
- Do NOT create the sentinel file — setup is incomplete.

##### 5a.4 — Capture results to `connectors.json`

Once detection succeeds, store BOTH the install method AND the actual prefix:

```json
{
  "notion": {
    "install_method": "cli" | "connector",
    "mcp_tool_prefix": "mcp__notion__" | "mcp__<uuid>__",
    "mcp_tool_prefix_resolved_at": "<today>",
    ...
  }
}
```

Agents will read `mcp_tool_prefix` at run time to construct tool names. The orchestrator should also **re-probe at the start of each pipeline run** (one cheap `ToolSearch "notion-search"` call) and refresh `mcp_tool_prefix` if the UUID has rotated — connector reconnects can change the UUID mid-deploy.

##### 5a.5 — Routines special case

If the user selected "Cloud Routine" deployment mode in Step 1 AND chose `auth_method = "mcp"`: warn that MCP works in Routines (Routine containers can attach connectors) but the per-call error compounding means a meaningful share of scheduled runs will fail partway. The connector UUID seen during local setup may also differ from the one assigned to the Routine container, so the orchestrator's `mcp_tool_prefix` re-probe (in jobs-run) is what actually keeps the run portable. Recommend switching to `auth_method = "api_token"` for any cadence tighter than weekly. Note this in the run logs so the user has the context if Routine runs start failing.

#### Step 5b — Names + database setup

##### Step 5b.0 — Confirm artifact names

Read the default names from `./config/connectors.json[notion.names]`. Defaults:

| Key | Default name |
|---|---|
| `parent_page` | AI 50 Job Search |
| `tracker_db` | Job Tracker |
| `hot_list_page` | Hot Lists |
| `state_db` | AI50 State |
| `profile_page` | AI 50 Profile |
| `extended_companies_page` | Extended Companies List |

Print the defaults and ask:

```
The plugin will create 1 parent page + 2 databases + 3 child pages in your
Notion workspace. Defaults shown above.

These names matter because the runtime can RE-DISCOVER your IDs by name if
something gets moved or accidentally archived. Custom names work fine; just
keep them consistent (don't rename the artifacts in Notion later without
also editing connectors.json[notion.names]).
```

Call `AskUserQuestion`:
- Question: "Use these default names, or customize?"
- Header: "Names"
- Options:
  - label: "Use defaults", description: "Recommended — fewer moving parts"
  - label: "Customize names", description: "I'll prompt for each one"

If "Customize": ask one at a time for each name, accept the user's input, write all six back into `connectors.json[notion.names]` before proceeding.

If "Use defaults": leave `connectors.json[notion.names]` as shipped.

##### Step 5b.1 — Create or paste

**Q-N0:** "Do you want me to create the required Notion artifacts for you, or paste IDs of existing ones?

  c) Create them for me (recommended) — I'll create the 1 parent + 2 DBs + 3 child pages
     under whichever existing Notion page you choose as the workspace anchor.
  e) I'll paste IDs of artifacts I already created."

If they pick (c) — **Create artifacts automatically:**

1. Search Notion for an existing page using `notion-search` (any page the user wants to nest the AI 50 setup under). Show the top 3 candidates and ask which one to use, or let them paste an ID. As a last resort, fall back to a workspace-level page (some Notion API setups disallow this — handle the 400 error gracefully and re-ask).
2. Create the parent page using the configured name (`names.parent_page`) under the chosen anchor.
3. Create the Job Tracker database (named `names.tracker_db`) under the parent page using the schema at `./scripts/schemas/tracker_db.json`:
   ```bash
   python3 ./scripts/notion-api.py create-database \
     --parent-page-id <parent_page_id> \
     --title          "<names.tracker_db>" \
     --schema         ./scripts/schemas/tracker_db.json
   ```
4. Create the Hot Lists page (named `names.hot_list_page`) under the parent page.
5. Create the State database (named `names.state_db`) under the parent page using the schema at `./scripts/schemas/state_db.json`:
   ```bash
   python3 ./scripts/notion-api.py create-database \
     --parent-page-id <parent_page_id> \
     --title          "<names.state_db>" \
     --schema         ./scripts/schemas/state_db.json
   ```
   **Important:** Job IDs are stored in each row's **page body** as a fenced ```json code block, not as a property. This avoids Notion's 2000-char per-rich-text-block silent truncation. The `Job count` number column is a convenience for at-a-glance verification (and a tripwire — `Job count` should equal the length of the JSON array in the body).

   **In Cloud Routine mode this database is REQUIRED — create it without asking.** Skipping it means cold-start Routine runs would have no state and re-emit every job as new on every run.

   **In local mode this database is optional.** Ask the user before creating it: *"Create the State database too? Recommended for resilience even locally — without it, deleted state file means re-emitting every job."* If they say no, skip — `cached-ids.json` will simply not contain a `tracker_state_database_id` key.

6. **Cloud-mode only — create Profile + Favorites pages.** Skip these in local mode.
   - Create page named `names.profile_page` under the parent. Body: a single ```json code block containing the full profile.json that will be assembled in Step 6 (write the page content during Step 6 once the JSON is built).
   - Create page named `names.extended_companies_page` under the parent. Body: a single ```json code block containing the custom-companies array (default `[]` if user picks "Skip" in Step 7).
   - Both pages will be edited by the user later via Notion directly. The plugin reads them on every run; it never writes back. **They are NEVER auto-recreated by the runtime** — if they're accidentally deleted, the user must re-run setup.

7. Capture all IDs and write them to `./state/cached-ids.json` (Step 6 below). The repo's `connectors.json` does NOT receive these IDs — IDs are per-user, not per-plugin.

If they pick (e) — **Paste existing IDs:**

Ask the user to confirm the artifact names match what's in their workspace (or update `connectors.json[notion.names]` first), then paste:

```
Paste these IDs from Notion. To find an ID: Open the page or database
→ click ••• → "Copy link to view". The 32-character string in the URL is the ID.

Q-N1: Parent page ID (the 'AI 50 Job Search' page that holds all the others):
Q-N2: Tracker database ID (where each qualifying job becomes a row):
Q-N3: Hot-list parent page ID (where weekly digests are created as child pages):
Q-N4 (REQUIRED for Cloud Routines, optional locally): State database ID:
       Schema: Company key TITLE, Last checked DATE, Job count NUMBER, Notes RICH_TEXT.
       The Company key TITLE uses the format <ats>:<slug> — e.g.
         "ashby:cohere", "greenhouse:databricks", "comeet:cyera"
       (this is what fetch-and-diff.py emits as the diff key; if you create the
       DB by hand and use any other naming, the diff will not match and every
       run will re-emit all jobs as new.)
       Job IDs live in each row's page body as a JSON code block.
       Press Enter to skip in local mode — but state survives Routine runs only
       with this DB.
Q-N5 (cloud mode only): Profile page ID:
Q-N6 (cloud mode only): Favorites page ID:
```

After paste, the wizard validates each ID by calling `notion-api.py fetch-page` (for pages) or `notion-api.py query-database --limit 1` (for databases). If validation fails for any, prompt for correction.

**Q-N7 (MCP mode only):** "Add the Notion MCP if you haven't already:

```bash
claude mcp add notion --transport sse https://mcp.notion.com/sse
```

Press Enter when done."

---

## Step 6 — Write config files

Build `profile.json` from all collected answers:

```json
{
  "_comment": "Candidate profile and search rules. Edit this file (local mode) or the AI 50 Profile Notion page (cloud mode) at any time.",

  "candidate": {
    "current_location": "{Q1}",
    "open_to_relocation": {
      "flag": {true if Q2 indicates yes, else false},
      "regions": [{regions/countries the user named in Q2, empty if no}]
    },
    "spoken_languages": [{Q6 — array of canonical language names}]
  },

  "context": "{Q7 verbatim — full background and preferences text}",

  "location_rules": {
    "work_mode_description": "{Q3 verbatim — full free-form answer}",
    "eligible_modes": {derived from Q3, e.g. ["remote", "hybrid"] or ["remote", "hybrid", "on-site"]},
    "eligible_regions": {derived from Q2 + Q3, e.g. ["EU"] or ["any"]},
    "excluded_cities": [],
    "excluded_countries": {derived from Q3 if user named exclusions, else []}
  },

  "role_types": [{array of role type objects from Q4, confirmed by user}],

  "cv_json": {Step 3.5 extracted-and-confirmed CV JSON — required},

  "scoring": {
    "instructions": "{Step 4's optional hint, or empty string}"
  },

  "hard_exclusions": {
    "schema_version": 1,
    "rules": [
      {"type": "language_required", "user_languages": [{Q6}], "reject_if_other_required": true},
      {if Q3.5 captured a remote_country_lock rule: emit it here verbatim},
      {if eligible_regions is narrow (e.g. just ["EU"]): also emit "country_lock" rule with reject_outside set to those regions},
      {any title-pattern exclusions the wizard derived from Step 4b moves: e.g. {"type": "title_pattern", "reject_if_contains": ["Marketing", "Sales"], "unless_also_contains": []}}
    ]
  },

  "ats_platforms": {
    "ashby":      "https://jobs.ashbyhq.com/{company}",
    "greenhouse": "https://boards.greenhouse.io/{company}",
    "lever":      "https://jobs.lever.co/{company}"
  }
}
```

**In local mode:** write the assembled `profile.json` to `./config/profile.json`. Leave `config/custom-companies.json` for Step 7.

**In cloud mode:** do **not** write `config/profile.json`. Instead:
- Use `notion-update-page` (replace_content) to set the body of the AI 50 Profile page (created in Step 5b) to a single ```json code block containing the assembled profile JSON.
- Leave `config/profile.json` as the shipped template (sample data) — the plugin will hydrate from Notion at run time, ignoring this file.
- Tell the user: "Profile saved to Notion. To update later, edit the AI 50 Profile page directly — changes apply on the next run. Don't break the JSON or the search will fail loudly."

**Update `connectors.json`** with the auth-method choice — but NOT with IDs. Per-user IDs live in `state/cached-ids.json`, not in the repo. Set:

```json
{
  "notion": {
    "auth_method":        "{mcp | api_token}",
    "names":              { ... possibly customized in Step 5b.0 ... },
    "install_method":     "{cli | connector | null}",
    "mcp_tool_prefix":    "{resolved prefix | null}",
    "mcp_tool_prefix_resolved_at": "{today | null}"
  }
}
```

**Write `state/cached-ids.json`** — this is the per-user ID cache. The runtime reads this first; on miss, falls back to discover-by-name; on miss again, recreates safe artifacts (or aborts on profile / extended-companies pages).

```json
{
  "parent_page_id":              "<resolved parent>",
  "tracker_database_id":         "<resolved tracker DB>",
  "hot_list_parent_page_id":     "<resolved hot lists page>",
  "tracker_state_database_id":   "<resolved state DB | omit if not created>",
  "profile_page_id":             "<cloud mode only: omit in local mode>",
  "extended_companies_page_id":  "<cloud mode only: omit in local mode>",
  "_resolved_at":                "{today, ISO 8601}",
  "_workspace_id":               "<from notion-api.py users-me, for sanity check>"
}
```

This file is `.gitignore`d — it's per-user state, not part of the plugin. The runtime is allowed to overwrite it.

**Create sentinel:**

```bash
mkdir -p "./state"
printf '{"setup_completed":"%s","method":"guided","deployment_mode":"%s","auth_method":"%s"}\n' \
  "$(date +%Y-%m-%d)" "{cloud|local}" "{mcp|api_token}" \
  > "./state/.setup_complete"
```

(The wizard substitutes `{cloud|local}` and `{mcp|api_token}` with the actual values from Step 1 / Step 5 before running this. The date comes from `date +%Y-%m-%d` at sentinel-write time, NOT a literal placeholder.)

---

## Step 7 — Custom companies (optional opt-in)

Print:

```
✅ Configuration saved.

The plugin tracks the Forbes AI 50 baseline by default — ~50 leading
AI companies (Anthropic, OpenAI, Cohere, Mistral, Perplexity, ...).
For most users, that's the right scope.

You can extend this list with custom companies you want to track on
top — your target employer, a competitor, an AI-native startup in
your niche. They run through the same pipeline as AI 50 entries:
weekly fetch, diff, score, into your tracker.

Want to add custom companies now, or skip and add later?
  - "now": paste careers URLs (one per line); I'll auto-detect the ATS
  - "skip": continue with AI 50 baseline only. You can extend anytime
            by typing "extend companies" — opens an interactive flow.
```

Call `AskUserQuestion`:
- Question: "Add custom companies now?"
- Options:
  - label: "Now", description: "I'll paste URLs"
  - label: "Skip", description: "AI 50 baseline only — I'll extend later if I need to"

### If "Skip"

Print:
```
Got it. You'll run with the AI 50 baseline. After your first run, type
"extend companies" anytime to add custom-tracked companies via the
jobs-extend-companies skill.
```

In **cloud mode**: create the Extended Companies List Notion page with body `[]` (empty JSON array in a code block). The page exists so the runtime discovery can find it, but it's empty.

In **local mode**: create `./config/custom-companies.json` with `[]`.

Move on to Step 7.5.

### If "Now"

Print:

```
Paste careers URL(s) for each company you want to track. One per line.
Press Enter twice when done.

  https://job-boards.greenhouse.io/togetherai   (auto-detects: greenhouse)
  https://botify.teamtailor.com/jobs/            (auto-detects: teamtailor)
  https://example.com/careers                    (custom domain → I'll ask)
  Anthropic                                       (name only — I'll ask for the URL)
```

For each entry:

1. **If careers_url provided:** call `ats_adapters.ats_from_url(url)`:
   - Returns `(ats, slug)` for any of the 6 supported ATSes (Ashby / Greenhouse incl. EU / Lever / Comeet / Teamtailor / Homerun) → store `{name, ats, slug, careers_url, source: "user_added"}`.
   - Returns `None` (URL doesn't match a known ATS) → offer the user two options:
     - **`ats: "scrape"`** — Claude Code agent extraction at fire time (the `scrape-extract` agent — runs on your Claude.ai subscription quota, no API key needed). Each fire dispatches scrape-extract per scrape-tracked company; ~12–50K input tokens per page, Haiku-equivalent.
     - **`ats: "skip"`** — just record the URL but don't fetch. Use this if the careers page is a JS-heavy SPA that scrape-extract won't handle, OR the user wants to track manually.

   Ask: *"This company's careers page isn't on a supported ATS. Want me to extract listings with the scrape-extract agent (Claude Code agent, ~Haiku tokens per page), or skip fetching and just remember the URL?"* Default suggestion is **scrape** if the page looks like a typical careers listing; **skip** if it's clearly a single-job blurb or a redirect.

2. **If careers_url not provided** (just a name): ask the user to paste the URL. If they don't have it, store as `{name, ats: "skip", source: "user_added"}` placeholder — they can update later via `jobs-extend-companies`.

After processing all entries, show the proposed list and confirm before writing.

**Local mode:** write the resulting array to `./config/custom-companies.json`.

**Cloud mode:** use `notion-update-page` (replace_content) to set the body of the Extended Companies List page to a single ```json code block containing the array.

### After either path

Print a hint to the user:

```
You can add/remove/update custom companies anytime by typing:
  "extend companies"   (interactive flow — paste URLs, edit, list, cleanup)

To preview extraction quality on a careers page before committing it
as scrape-tracked, type:
  "scrape this page: <url>"   (one-off extraction, no tracking)
```

---

## Step 7.5 — Confirm typed exclusion rules (sanity check)

Before declaring setup complete, show the user the typed `hard_exclusions` rules generated from Q3.5 + Q6 + any wizard-derived exclusions. This is the catch-it-now moment if the wizard mistranslated free-text intent.

Print (substituting actual values from the in-memory profile):

```
━━━ Hard exclusions ━━━
The following filters will be applied BEFORE scoring on every run.
Anything matching these gets dropped, never scored.

  1. Language: jobs requiring fluency in a language other than {Q6}
     will be excluded.
  
  {if remote_country_lock rule with eligible_remote_regions:}
  2. Remote location: only remote roles eligible to {eligible_remote_regions}
     will pass. "Remote — US only", "Remote — Brazil" etc. will be
     dropped.
  
  {if remote_country_lock rule with reject_remote_in:}
  2. Remote location: roles locked to {reject_remote_in} will be
     dropped (even if title fits and seniority matches).

  {if title_pattern rule:}
  3. Title patterns: roles whose title primarily indicates
     {reject_if_contains} will be dropped.

Are these correct? (yes / let me adjust)
```

If user says "let me adjust" — re-ask Q3.5 (or the relevant question) and regenerate the rules. If "yes" — proceed to Step 8.

This is the lightweight version of post-wizard validation. The fuller version (showing 5 sample listings and asking which the user would include/exclude) ships with the jobs-recalibrate skill in v2.5.2.

---

## Step 8 — Done

Print:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Setup complete ✅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Profile:     {Q1}
Roles:       {role type labels}
Languages:   {Q6 — spoken languages}
Scoring:     min {minimum_score}/{max_score} to save · {hot_score_threshold}/{max_score} for hot list
Auth:        {Notion MCP / Notion API token}
Mode:        {Cloud Routine / Local}

To run your first search:  run the job search
To add custom companies:   extend companies
To preview a careers page: scrape this page: <url>
To schedule weekly runs:   claude.ai/code/routines
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Do not automatically start the search. Let the user decide.
