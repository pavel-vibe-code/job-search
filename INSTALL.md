# AI 50 Job Search — Installation

A 5-minute setup. Pick an install style:

| Style | If you want to... |
|---|---|
| **Quick** | Install + run as-is, never edit code. Track upstream automatically. |
| **Advanced** | Edit code, add ATS adapters, customise the orchestrator, contribute back. Maintain your own fork. |

Both styles support local interactive use AND scheduled Cloud Routines. They differ only in §1 (how you get the source) and §4 (how you stay current).

---

## Prerequisites

| Requirement | Why |
|---|---|
| Claude Code installed (CLI, desktop app, or claude.ai/code) | Plugin runtime |
| Notion account | The plugin's data lives there |
| Python 3 (always present on macOS / most Linux) | Helper scripts run in the plugin |
| GitHub account | Required for Advanced (to fork); useful for Quick (Routine on private upstream forks needs auth) |

---

## 1. Get the plugin onto your machine

### 1.A — Quick

```bash
git clone https://github.com/pavel-vibe-code/job-search.git
cd job-search
```

That's it for §1. You're ready for §2.

### 1.B — Advanced

```bash
# 1. Fork pavel-vibe-code/job-search via the "Fork" button on GitHub.
#    This creates github.com/<your-username>/job-search under your account.

# 2. Clone YOUR fork (not upstream):
git clone https://github.com/<your-username>/job-search.git
cd job-search

# 3. Add upstream as a remote so you can pull in upstream changes later:
git remote add upstream https://github.com/pavel-vibe-code/job-search.git

# Verify
git remote -v
# origin    https://github.com/<your-username>/job-search.git (fetch/push)
# upstream  https://github.com/pavel-vibe-code/job-search.git (fetch/push)
```

---

## 2. Install + Notion setup (same for both styles)

### 2.1 — Load the plugin

```bash
claude --plugin-dir .
```

### 2.2 — Mint a Notion integration token

1. Open https://www.notion.so/profile/integrations
2. Click **"+ New integration"**
3. Name it `AI 50 Job Search` (or anything you'll recognise)
4. Workspace: pick the workspace where you want the tracker to live
5. Capabilities: leave defaults (Read content, Update content, Insert content)
6. Click **Save**, then copy the **Internal Integration Token** (starts with `secret_` or `ntn_`)

### 2.3 — Share a parent page with the integration

The integration sees nothing by default. Pick one Notion page to act as the workspace anchor (e.g. an existing "Work" or "Job search" page) and grant the integration access to it:

1. Open the page
2. Click **•••** (top right) → **Connections** → **Add connections**
3. Search for `AI 50 Job Search` and click **Add**

The integration now has read/write access to that page and everything under it.

### 2.4 — Run setup

In Claude Code:

```
set up the plugin
```

The wizard asks ~10 questions:

| Question | Example answer |
|---|---|
| Which deployment mode? | "Local" for laptop-only; "Cloud Routine" if you'll schedule (you can switch later) |
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
| Paste your Notion token | (the `ntn_...` from §2.2) |
| Pick parent page | Pick your "Work" / "Job search" page from the list |
| Optional favorites? | Comma-list of extra companies, or skip |

When the wizard finishes, the plugin has created in Notion:

- A parent page **AI 50 Job Search**
- A **Job Tracker** database (one row per qualifying job)
- A **Hot Lists** parent page (weekly digests go here as child pages)
- An **AI50 State** database (per-company job ID tracking)
- An **AI 50 Profile** page (your profile JSON; edit this directly to update)
- An **AI 50 Favorites** page (your additional companies; edit directly to update)

### 2.5 — First run

```
run the job search
```

Takes 60–90 seconds. First run treats every job as new (empty state). You should see:

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

That's it for laptop-only use. Type `run the job search` whenever you want a fresh scan; the state DB ensures only NEW jobs get added.

### 2.6 — Updating your profile

Edit the **AI 50 Profile** page in Notion directly. The profile is a JSON code block in the page body — edit the JSON, save the page, and the next run picks up the changes. Don't break the JSON or the run will fail loudly.

---

## 3. Cloud Routine (optional — same for both styles)

Once §2 is done, you can schedule weekly unattended runs.

### 3.0 — How a Routine actually runs

A Cloud Routine is a sandboxed container that fires on your schedule (e.g. every Monday 08:00). Each run:

1. **Clones your repo from GitHub.** Whatever's on `main` at fire time gets pulled fresh into a new container. Code changes you push land in the next run automatically; no other "deploy" step.
2. **Runs your setup script** (§3.2c) which scaffolds `state/.setup_complete` so the orchestrator skips the wizard. Containers don't persist between runs, so this scaffolds fresh state each fire.
3. **Loads the agent runtime + your environment.** This is when your custom env vars (`NOTION_API_TOKEN`, `NOTION_PARENT_ANCHOR_ID`) and the network egress allowlist become visible. **Setup script does NOT have access to your custom env vars** — it runs in a constrained pre-init context. Don't try to do auth pre-checks or anything that needs your token from inside the setup script.
4. **Executes the trigger prompt** — runs the `run-job-search` orchestrator skill end-to-end with no human in the loop. Auth failures (wrong token, revoked integration) surface here at the first Notion call.
5. **Tears down.** Nothing persists outside Notion.

The repo's `.claude/settings.json` (shipped since v2.3.1) supplies the tool-permission allowlist; without it the Routine would silently stall on the first Bash call. You don't have to write or edit it.

**GitHub access prereq.** Routines clone via your claude.ai account's GitHub connection. Public repos work without auth. Private repos require the connection to have read access to the repo. If your claude.ai isn't GitHub-connected, run `/web-setup` in Claude Code to do the OAuth flow. Verify which GitHub account is linked at https://claude.ai/settings.

**Quick vs Advanced for the Routine.** The Routine clones whatever repo URL you select in §3.3. Quick users select `pavel-vibe-code/job-search` (upstream) and get upstream improvements automatically. Advanced users select their own fork and decide when to pull upstream into their fork (see §4.B). The plugin code itself is identical either way; the difference is only who owns the cloned source.

### 3.1 — Permission allowlist (already shipped)

Cloud Routines run unattended — each Bash, Read, Write, and Edit tool call must be pre-approved or the run stalls silently with no prompt to recover.

The repo ships **`.claude/settings.json`** at its root with the right allowlist (Bash patterns for the plugin's Python scripts, Read of `config`/`state`/`schemas`, Write/Edit of `state`/`outputs`/`tmp`). The Routine clones this with the rest of the repo and applies it automatically — **no action needed for Routine setup itself**.

For local Path A (clone + `cd job-search` + `claude --plugin-dir .`), Claude Code reads the same `./.claude/settings.json` from your CWD, so local interactive runs are pre-approved too.

If you install the plugin into a *different* project directory (e.g. via the marketplace once listed) so that your CWD is not the plugin repo, the shipped settings.json won't apply to that foreign session — copy the rules into your project's own `.claude/settings.json`.

> **Why the wildcard form.** The Bash patterns use `*/scripts/<name>.py` rather than an absolute path. `${CLAUDE_PLUGIN_ROOT}` does **not** expand inside `Bash()` permission patterns, so the wildcard is the portable form that matches both your local laptop path and the Routine container path.

### 3.2 — Create a Routine environment

Go to **claude.ai/code → Settings → Environments → New environment**.

Name it something like `job-search-prod`.

#### 3.2a — Environment variables

In the **Environment variables** field (`.env` format, one `KEY=value` per line):

```
NOTION_API_TOKEN=ntn_<your-token-from-step-2.2>
NOTION_PARENT_ANCHOR_ID=<32-char-page-id>
```

| Variable | Required? | What it does |
|---|---|---|
| `NOTION_API_TOKEN` | Yes | Auth for all Notion calls |
| `NOTION_PARENT_ANCHOR_ID` | Recommended | Fallback anchor if the parent page goes missing. Without it, the Routine aborts on missing-parent (since there's no human to pick a new anchor). With it, the runtime auto-recreates under the anchor. |

To find a page ID: open the page in Notion → click **•••** → **Copy link to view**. The 32-character string in the URL is the ID. The hyphenated form (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`) and the un-hyphenated form both work; if one is rejected, try the other.

> **Security caveat:** Notion warns that environment variables are visible to anyone with edit access on the environment. For a personal account where only you have access, that's fine. For shared accounts: rotate the token regularly, or don't share the environment.

#### 3.2b — Allowed domains

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

#### 3.2c — Setup script

In the **Setup script** field, paste this:

```bash
# Find the plugin root (find scans the container; takes ~1 second)
NOTION_API=$(find / -path '*/scripts/notion-api.py' -type f 2>/dev/null | head -1)
PLUGIN_ROOT=$(dirname "$(dirname "$NOTION_API")")

# Create the setup sentinel so run-job-search doesn't trigger the wizard
mkdir -p "$PLUGIN_ROOT/state"
DATE=$(date +%Y-%m-%d)
printf '{"setup_completed":"%s","method":"routine","deployment_mode":"cloud","auth_method":"api_token"}\n' "$DATE" > "$PLUGIN_ROOT/state/.setup_complete"

echo "Setup OK; plugin root: $PLUGIN_ROOT"
```

This runs once per Routine fire, before the agent starts. It:
1. Discovers where the plugin lives in the container.
2. Creates `state/.setup_complete` (the wizard would create this on a normal install; Routine containers don't persist files between runs).

> **Why no auth pre-check in the setup script.** Earlier versions ran `python3 "$NOTION_API" users-me` here as a fail-fast check. That was wrong: the Routine's setup-script context **does not see custom environment variables** (it runs in a constrained pre-init context with only Claude-cloud and system vars). Custom env vars like `NOTION_API_TOKEN` are only visible in the agent / orchestrator runtime context. The auth check now happens implicitly the moment the orchestrator makes its first Notion call — same loud failure, just a few seconds later in the run. Don't try to invoke `notion-api.py` from the setup script; it will always fail with `no_token`.

> **Why no backslash line-continuations.** Web text inputs (including the Routine UI's setup-script field) can strip trailing whitespace or normalize line endings in ways that break `\`-continuation. Keep each shell statement on a single line; extract values to their own variables (like `DATE=$(date ...)`) instead of using inline `\` joins.

### 3.3 — Create the Routine

Go to **claude.ai/code → Routines → New Routine**.

| Field | Value |
|---|---|
| **Repository** | Quick: `pavel-vibe-code/job-search`. Advanced: `<your-username>/job-search` (your fork). |
| **Plugin** | `job-search` (the plugin name from `.claude-plugin/plugin.json`, not the repo name) |
| **Environment** | The one you created in §3.2 |
| **Schedule** | Weekly (e.g. Monday 08:00 your TZ) |
| **Trigger / prompt** | (see below) |

> **Tip — CLI alternative.** You can create the Routine from inside Claude Code instead of the web UI: run `/schedule` and walk through the prompts. It hits the same backend as the web form, so the resulting Routine appears at claude.ai/code/routines just the same. Useful when you want to script Routine creation alongside other terminal work; the web UI is more transparent for first-time setup. If `/schedule` reports your account isn't connected to GitHub, run `/web-setup` first.

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

### 3.4 — Test-fire the Routine

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

### 3.5 — Maintenance

The Routine fires on its schedule with no further action needed. Things to do periodically:

- **Rotate the Notion token** every few months. Mint a new one, paste it into the Routine environment, revoke the old one at notion.so/profile/integrations.
- **Review the tracker**. New rows have `Status = "New"`. Update to `Reviewed / Applied / Not interested` as you triage.
- **Tune the rubric** by editing the AI 50 Profile Notion page. Increase weights for criteria you're seeing too few of; decrease where you're seeing too much noise.
- **Add favorites** by editing the AI 50 Favorites Notion page (JSON array of `{name, ats, slug, source}`).

---

## 4. Staying in sync with upstream

### 4.A — Quick

Your local clone tracks upstream directly (since you cloned `pavel-vibe-code/job-search`):

```bash
cd /path/to/job-search
git pull origin main
```

Your Cloud Routine clones upstream fresh on every fire, so it always picks up the latest `main` automatically — no action needed for the cloud side.

### 4.B — Advanced — sync your fork

Your local clone tracks YOUR fork. Pull upstream changes through your fork:

```bash
cd /path/to/job-search

# Pull upstream into your local main:
git fetch upstream
git merge upstream/main          # or: git rebase upstream/main

# Push the merge to your fork so the Routine sees it:
git push origin main
```

The Routine clones your fork (per the Repository field in §3.3), so any upstream changes you want it to pick up need to land on your fork's `main`. If you want to pin to a specific upstream version and review changes manually, skip the merge and work off a branch.

To contribute changes back to upstream: push to a branch on your fork, then open a PR from your fork to `pavel-vibe-code/job-search`.

---

## Troubleshooting

### Setup fails with "object_not_found"

The integration doesn't have access to the parent page. Re-do step §2.3 (Connections → Add connections).

### First run shows 0 candidates

Probably your role-type keywords are too narrow, OR your `excluded_countries` accidentally includes a meta-phrase like "all non-EU" (v2.2.x bug; v2.3 has a defensive guard but the wizard could still emit it under unusual phrasing).

Edit the AI 50 Profile page in Notion and check `excluded_countries` — it should contain only canonical country names ("United Kingdom", "Ireland", etc.), nothing like "all non-EU" or "anything outside Europe".

### "Notion MCP not available in this session"

If you picked `auth_method = "mcp"` during setup but the Notion MCP isn't connected in the current Claude Code session: either reconnect Notion (claude.ai/code Connectors → Notion), or re-run setup and pick `auth_method = "api_token"` instead.

### Routine clone fails with 404

Your claude.ai account's GitHub connection doesn't have read access to the repo:
- Confirm which GitHub account is connected at https://claude.ai/settings.
- For private repos, the connection must specifically have access to that repo. If you authenticated via a GitHub App, check installation permissions at https://github.com/settings/installations.
- Re-run `/web-setup` in Claude Code to re-authorize.

### Routine fires but does nothing

Check the Routine logs. Most common causes:
- `.claude/settings.json` missing or stale → some Bash/Read/Write call is silently denied. Pull the latest from upstream (Routine clones latest, but a stale fork might lag).
- `NOTION_API_TOKEN` env var not set or expired → setup script fails the auth pre-check.
- Allowed domains missing the ATS hosts → fetch fails silently for some companies.

### State drift (jobs missing from tracker but marked "seen" in state DB)

Pre-v2.3 behavior on compile-write failures. v2.3+ has the un-poisoning fallback handler. If you're on v2.3+ and seeing this: check `outputs/<date>-tracker-fallback.md` — your missing rows are there, and the next run will retry them.

### Help

- Architecture details: [ARCHITECTURE.md](ARCHITECTURE.md)
- Release history: [CHANGELOG.md](CHANGELOG.md)
- File issues / questions: <repo issues URL>
