# AI 50 Job Search — Installation

Two paths:

- **Path A — Local interactive use only.** Five minutes. You run the search manually whenever you want.
- **Path B — Cloud Routine (scheduled, unattended).** Path A first, then ~10 more minutes to schedule weekly runs.

Pick A if you want to try the plugin first; switch to B once you're happy with the output.

---

## Prerequisites

| Requirement | Why |
|---|---|
| Claude Code installed (CLI, desktop app, or claude.ai/code) | Plugin runtime |
| Notion account | The plugin's data lives there |
| Python 3 (always present on macOS / most Linux) | Helper scripts run in the plugin |

---

## Path A — Local interactive use

### A.1 Install the plugin

```bash
# Option 1 — Claude Code marketplace (when listed)
# Find "AI 50 Job Search" in the marketplace and click Install.

# Option 2 — Git clone
git clone https://github.com/<owner>/ai50-job-search.git
cd ai50-job-search
claude --plugin-dir .
```

### A.2 Mint a Notion integration token

1. Open https://www.notion.so/profile/integrations
2. Click **"+ New integration"**
3. Name it `AI 50 Job Search` (or anything you'll recognise)
4. Workspace: pick the workspace where you want the tracker to live
5. Capabilities: leave defaults (Read content, Update content, Insert content)
6. Click **Save**, then copy the **Internal Integration Token** (starts with `secret_` or `ntn_`)

### A.3 Share a parent page with the integration

The integration sees nothing by default. Pick one Notion page to act as the workspace anchor (e.g. an existing "Work" or "Job search" page) and grant the integration access to it:

1. Open the page
2. Click **•••** (top right) → **Connections** → **Add connections**
3. Search for `AI 50 Job Search` and click **Add**

The integration now has read/write access to that page and everything under it.

### A.4 Run setup

In Claude Code:

```
set up the plugin
```

The wizard asks ~10 questions:

| Question | Example answer |
|---|---|
| Which deployment mode? | "Local" for path A; "Cloud Routine" for path B |
| What city and country are you based in? | "Berlin, Germany" |
| Are you open to relocating? Where? | "EU only", "no", "open to US/Canada" |
| Preferred work mode + nuances? | "remote in EU only, hybrid Berlin OK" |
| Target role types? | "VP Customer Success, Director Support" |
| Seniority level? | "Director or VP, 10+ years" |
| Languages you speak? | "English, German" |
| Background context? | Free-form description |
| Scoring criteria + priorities? | "Seniority: high — must. AI-native: high. Location: high. Series B+: medium." |
| Customize Notion artifact names? | Defaults usually fine |
| Auth method? | "API token (recommended)" |
| Paste your Notion token | (the `ntn_...` from A.2) |
| Pick parent page | Pick your "Work" / "Job search" page from the list |
| Optional favorites? | Comma-list of extra companies, or skip |

When the wizard finishes, the plugin has created in Notion:

- A parent page **AI 50 Job Search**
- A **Job Tracker** database (one row per qualifying job)
- A **Hot Lists** parent page (weekly digests go here as child pages)
- An **AI50 State** database (per-company job ID tracking)
- An **AI 50 Profile** page (your profile JSON; edit this directly to update)
- An **AI 50 Favorites** page (your additional companies; edit directly to update)

### A.5 First run

```
run the job search
```

Takes 60-90 seconds. First run treats every job as new (empty state). You should see:

```
Fetch: 50 companies checked | N errored | 1 external
Total jobs in ATS: ~5,000 | New: ~5,000 → ~5-15 candidates after filter
Validation: ~5-15 live confirmed
Tracker: ~5-15 new entries written
State: Notion DB ✓ (50 rows persisted)

🔥 Hot matches (N at score ≥ <threshold>):
  • <Score> — <Company>: <Title> [<location>]

Hot list: <Notion page URL>
```

That's it. Type `run the job search` whenever you want a fresh scan; the state DB ensures only NEW jobs get added.

### A.6 Updating your profile

Edit the **AI 50 Profile** page in Notion directly. The profile is a JSON code block in the page body — edit the JSON, save the page, and the next run picks up the changes. Don't break the JSON or the run will fail loudly.

---

## Path B — Cloud Routine (scheduled, unattended)

You must complete Path A first (the wizard creates the Notion structure on your laptop). Then:

### B.1 Permission allowlist (already shipped)

Cloud Routines run unattended — each Bash, Read, Write, and Edit tool call must be pre-approved or the run stalls silently with no prompt to recover.

The repo ships **`.claude/settings.json`** at its root with the right allowlist (Bash patterns for the plugin's Python scripts, Read of config/state/schemas, Write/Edit of state/outputs/tmp). The Routine clones this with the rest of the repo and applies it automatically — **no action needed for Routine setup itself**.

For Path A (clone + `cd ai50-job-search` + `claude --plugin-dir .`), Claude Code reads the same `./.claude/settings.json` from your CWD, so local interactive runs are pre-approved too.

If you install the plugin into a *different* project directory (e.g. via the marketplace once listed) so that your CWD is not the plugin repo, the shipped settings.json won't apply to that foreign session — copy the rules into your project's own `.claude/settings.json`.

> **Why the wildcard form.** The Bash patterns use `*/scripts/<name>.py` rather than an absolute path. `${CLAUDE_PLUGIN_ROOT}` does **not** expand inside `Bash()` permission patterns, so the wildcard is the portable form that matches both your local laptop path and the Routine container path.

### B.2 Create a Routine environment

Go to **claude.ai/code → Settings → Environments → New environment**.

#### B.2a — Environment variables

In the **Environment variables** field (`.env` format, one `KEY=value` per line):

```
NOTION_API_TOKEN=ntn_<your-token-from-step-A.2>
NOTION_PARENT_ANCHOR_ID=<32-char-page-id>
```

| Variable | Required? | What it does |
|---|---|---|
| `NOTION_API_TOKEN` | Yes | Auth for all Notion calls |
| `NOTION_PARENT_ANCHOR_ID` | Recommended | Fallback anchor if the parent page goes missing. Without it, the Routine aborts on missing-parent (since there's no human to pick a new anchor). With it, the runtime auto-recreates under the anchor. |

To find a page ID: open the page in Notion → click **•••** → **Copy link to view**. The 32-character string in the URL is the ID. Pick any page in your workspace where the integration has access; the plugin will recreate the hierarchy under it if needed.

> **Security caveat:** Notion warns that environment variables are visible to anyone with edit access on the environment. For a personal account where only you have access, that's fine. For shared accounts: rotate the token regularly, or don't share the environment.

#### B.2b — Allowed domains

The Routine container has restricted egress; you allowlist hosts the plugin needs:

```
api.notion.com
*.ashbyhq.com
*.greenhouse.io
*.lever.co
*.comeet.com
surgehq.ai
```

Wildcards are allowed in the Routine UI's domains field.

#### B.2c — Setup script

In the **Setup script** field, paste this:

```bash
# Find the plugin root (find scans the container; takes ~1 second)
NOTION_API=$(find / -path '*/scripts/notion-api.py' -type f 2>/dev/null | head -1)
PLUGIN_ROOT=$(dirname "$(dirname "$NOTION_API")")

# Create the setup sentinel so run-job-search doesn't trigger the wizard
mkdir -p "$PLUGIN_ROOT/state"
printf '{"setup_completed":"%s","method":"routine","deployment_mode":"cloud","auth_method":"api_token"}\n' \
  "$(date +%Y-%m-%d)" > "$PLUGIN_ROOT/state/.setup_complete"

# Auth pre-check — fail fast if the token is wrong
python3 "$NOTION_API" users-me >/dev/null || {
  echo "ERROR: NOTION_API_TOKEN invalid" >&2
  exit 1
}
echo "Setup OK; plugin root: $PLUGIN_ROOT"
```

This runs once per Routine fire, before the agent starts. It:
1. Discovers where the plugin lives in the container.
2. Creates `state/.setup_complete` (the wizard would create this on a normal install; Routine containers don't persist files between runs).
3. Verifies the Notion token works.

### B.3 Create the Routine

Go to **claude.ai/code → Routines → New Routine**.

| Field | Value |
|---|---|
| **Trigger / prompt** | (see below) |
| **Schedule** | Weekly (e.g. Monday 08:00 your TZ) |
| **Plugin** | `ai50-job-search` |
| **Environment** | The one you created in B.2 |

**Trigger prompt** (paste verbatim):

```
Run the AI 50 job search.

Routine context (no human in the loop):
- Auth: use NOTION_API_TOKEN from the environment.
- Config: artifact NAMES in connectors.json[notion.names] are authoritative;
  IDs are resolved at run time via notion-api.py discover (Step P-3).
- Setup sentinel was created by the Routine setup script. Do NOT trigger the
  setup wizard.
- Do not ask any interactive questions. If something is ambiguous, pick the
  documented default. If genuinely blocked, fail loudly and exit non-zero.

Then execute the run-job-search skill end-to-end and print the canonical run summary.
```

### B.4 Test-fire the Routine

In the Routine UI, click **Run now**. Watch the logs. A successful run looks like:

```
Setup OK; plugin root: /workspace/...
[orchestrator] P-3 discover: ok=true, 6 artifacts resolved
[orchestrator] P-4 hydrate: 50 companies in state
[Pass 1] search-roles: 50 fetched, ~5-15 candidates
[Pass 2] validate-urls: ~5-15 live
[Pass 3] compile-write: N rows written to tracker
[Pass 4] state persist: 50 rows
[Pass 5] notify-hot: digest page created
Run summary: ...
```

If it fails:
- Check the Routine logs for the error.
- The fallback section in `skills/run-job-search/SKILL.md` covers most failure modes.
- You can always re-run interactively on your laptop with `run the job search` to compare.

### B.5 Maintenance

The Routine fires on its schedule with no further action needed. Things to do periodically:

- **Rotate the Notion token** every few months. Mint a new one, paste it into the Routine environment, revoke the old one at notion.so/profile/integrations.
- **Review the tracker**. New rows have `Status = "New"`. Update to `Reviewed / Applied / Not interested` as you triage.
- **Tune the rubric** by editing the AI 50 Profile Notion page. Increase weights for criteria you're seeing too few of; decrease where you're seeing too much noise.
- **Add favorites** by editing the AI 50 Favorites Notion page (JSON array of `{name, ats, slug, source}`).

---

## Troubleshooting

### Setup fails with "object_not_found"

The integration doesn't have access to the parent page. Re-do step A.3 (Connections → Add connections).

### First run shows 0 candidates

Probably your role-type keywords are too narrow, OR your `excluded_countries` accidentally includes a meta-phrase like "all non-EU" (v2.2.x bug; v2.3 has a defensive guard but the wizard could still emit it under unusual phrasing).

Edit the AI 50 Profile page in Notion and check `excluded_countries` — it should contain only canonical country names ("United Kingdom", "Ireland", etc.), nothing like "all non-EU" or "anything outside Europe".

### "Notion MCP not available in this session"

If you picked `auth_method = "mcp"` during setup but the Notion MCP isn't connected in the current Claude Code session: either reconnect Notion (claude.ai/code Connectors → Notion), or re-run setup and pick `auth_method = "api_token"` instead.

### Routine fires but does nothing

Check the Routine logs. Most common causes:
- Permission allowlist missing entries → Bash calls hang waiting for approval.
- `NOTION_API_TOKEN` env var not set or expired → setup script fails the auth pre-check.
- Allowed domains missing the ATS hosts → fetch fails silently for some companies.

### State drift (jobs missing from tracker but marked "seen" in state DB)

Pre-v2.3 behavior on compile-write failures. v2.3 has the un-poisoning fallback handler. If you're on v2.3+ and seeing this: check `outputs/<date>-tracker-fallback.md` — your missing rows are there, and the next run will retry them.

### Help

- Architecture details: [ARCHITECTURE.md](ARCHITECTURE.md)
- Release history: [CHANGELOG.md](CHANGELOG.md)
- File issues / questions: <repo issues URL>
