---
name: scrape-page
description: Extract structured job listings from any careers-page URL via the scrape-extract agent. Useful for testing extraction quality before adding a company as scrape-tracked, OR for ad-hoc one-off lookups outside the regular pipeline. Trigger phrases: "scrape this page", "extract jobs from this URL", "what's listed at <url>", "test scrape on".
version: 4.0.0
---

## What this skill does

Calls the `scrape-extract` agent (`.claude/agents/scrape-extract.md`) on a single careers-page URL and prints the resulting job array. No tracking, no diff, no Notion writes — pure extraction-and-display.

## When to use it

- **Before adding a company as scrape-tracked**: paste the careers URL, see what extraction returns. If quality is good (correct titles, real URLs, reasonable structure), proceed to add via `extend-companies`. If not, the page is probably SPA-only and tracking won't work; add as `ats: skip` instead.
- **Ad-hoc curiosity**: see what's listed at a given careers page right now without committing to track it.
- **Triaging a tracked-company quality issue**: if a scrape-tracked company is consistently producing weird tracker entries, run this skill on its URL to inspect what extraction is returning vs. what the page actually shows.

## When NOT to use it

- Recurring tracking — that's `extend-companies` + the regular pipeline. This skill is one-shot.
- Pages on a supported deterministic ATS (Ashby, Greenhouse, Lever, Comeet, Teamtailor, Homerun) — the deterministic adapters are higher-quality. Just add via `extend-companies` directly; scrape is only the fallback for unsupported ATSes.

## Step 1 — Get the URL

Ask the user for a careers-page URL if they didn't already paste one:

```
Paste the careers page URL you want to extract from:
```

Validate it's a URL (`http://` or `https://` prefix). If they passed something that's clearly not a careers page (404, login wall, marketing site root): warn but still pass it through — extraction will return `no_static_content` cleanly and the user will see that.

## Step 2 — Dispatch scrape-extract

Invoke the `scrape-extract` agent with this prompt:

```
Company: <user-supplied-name or "ad-hoc">
Careers URL: <url>

Output file: /tmp/scrape-page-result.json
```

Wait for the agent to return its summary envelope.

## Step 3 — Display results

Read `/tmp/scrape-page-result.json` and print to the user:

```
━━━ Scrape result for <careers_url> ━━━
Extraction quality: ok | partial | no_static_content
Jobs found: <N>

  1. <Job title>
     📍 <location>  · 🏷 <department>
     🔗 <url>

  2. <Job title>
     📍 <location>  · 🏷 <department>
     🔗 <url>

  ...

<If extraction_quality is partial or no_static_content, print the "notes" field
 from the envelope and offer:>
  - "Add as scrape-tracked anyway" → run extend-companies with this URL
  - "Add as skip" → record URL but don't fetch
  - "Don't add" → exit
```

Cap at first 20 jobs in display. If more, suffix with "...and N more — see /tmp/scrape-page-result.json for the full array."

## Step 4 — Optional add-to-tracked

If the extraction quality is `ok` or `partial`, ask:

```
Want to add <company> as a scrape-tracked company so it shows up in your
weekly run? (yes / no)
```

On `yes`: hand off to `extend-companies` skill with the URL pre-filled.
On `no`: exit. The user can always come back later.

## Cost

One careers-page extraction. ~12–50K input tokens, ~200–500 output tokens, billed against your Claude.ai subscription quota at Haiku rates (~$0.01–0.04 equivalent). Subsequent calls on the same URL re-run extraction — there's no caching, so don't run this in a loop on the same page.

## Failure modes

- Agent returns `error: fetch_failed` → page unreachable or DNS issue. Print error, suggest user check the URL.
- Agent returns `error: non_html` → URL responded with JSON / PDF / image. Print error.
- Agent returns `extraction_quality: no_static_content` with empty jobs array → page is JavaScript-only. Suggest the user mark as `skip` instead of scrape (scrape would always return zero jobs).
