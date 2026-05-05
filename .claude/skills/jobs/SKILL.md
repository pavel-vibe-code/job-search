---
name: jobs
description: Show the menu of all AI 50 Job Search commands available in this Claude Code session. Use this when the user is unsure what they can do, or wants a quick reference of available skills. Trigger phrases include "jobs", "job search help", "what can the job search do", "show me the commands", "menu".
version: 1.0.0
---

## What this skill does

Prints a one-screen menu of every skill in the AI 50 Job Search plugin, organised by use case. Read-only; no Notion calls, no file writes.

## When to use

- New users wondering what's available
- Returning users who forgot a command name
- Anyone wanting to verify the full surface of the plugin

## When NOT to use

- For actual work — invoke the specific skill instead (e.g. `extend companies`, `re-score`, `run the job search`)

## The menu

Print exactly this (substituting the user's deployment-mode notes if you can detect them from `state/.setup_complete`):

```
━━━ AI 50 Job Search — available commands ━━━

Run the pipeline:
  • run the job search           — fire the weekly pipeline manually  (`/jobs-run`)

Manage what's tracked:
  • extend companies             — add/remove/update tracked companies   (`/jobs-extend-companies`)
  • scrape this page: <url>      — preview a careers-page extraction     (`/jobs-scrape-page`)

Tune scoring quality:
  • re-score                     — re-evaluate tracker rows; fix empty rationales,
                                    rescore after profile changes, upgrade past
                                    Sonnet entries to Opus                (`/jobs-rescore`)
  • recalibrate the scoring      — adjust scoring criteria interactively  (`/jobs-recalibrate`)
  • recycle feedback             — fold tracker labels into next-run prompt
                                    (auto-fires weekly; manual trigger here) (`/jobs-recycle-feedback`)

Edit profile settings:
  • settings                     — change any single profile field (location,
                                    role types, scoring options, hard exclusions,
                                    CV, etc.) — preserves everything else   (`/jobs-settings`)

Setup / re-config:
  • set up the plugin            — run setup wizard (initial install or full re-config)  (`/jobs-setup`)

Help:
  • jobs                         — show this menu                          (`/jobs`)

Tip: every skill above is namespaced under /jobs- in slash-completion, so typing
/jobs- in Claude Code surfaces all of them.

Full docs: README.md (overview) · INSTALL.md (Cloud Routine setup) · ARCHITECTURE.md (deep technical reference)
```

## Optional context-aware additions

If you can read `state/.setup_complete` cleanly:
- Show the user's `deployment_mode` (Local / Cloud Routine) at the top of the menu
- If `Cloud Routine`: include a hint *"Your Cloud Routine fires on schedule; for an on-demand run, use `run the job search` here in CLI."*

If you detect that the user has never run setup (no sentinel):
- Lead with: *"You haven't completed setup yet. Start with `set up the plugin` — it'll walk you through the ~5-minute onboarding flow."*

If `state/last_recycle.json` shows the feedback loop hasn't fired in 14+ days AND user has labeled rows:
- Add a footer hint: *"⏰ Heads up: you have N labeled tracker rows that haven't been recycled in <X> days. Type `recycle feedback` when convenient — it'll fold your labels into next run's scoring prompt."*

These are read-only nudges, not interactive prompts. Don't ask for confirmation; just surface useful state.

## Output

Return to the orchestrator (or to the user directly if invoked standalone): nothing structured — this is a display skill. Just print the menu and stop.
