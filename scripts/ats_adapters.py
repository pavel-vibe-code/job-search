#!/usr/bin/env python3
"""Shared ATS adapter registry — single source of truth for ATS support.

Purpose: extracted from validate-jobs.py / validate-favorites.py (legacy slug-variant probing) / fetch-and-diff.py
in v3.1.0 so adding a new ATS is a one-place change. Each adapter is a dict
entry that defines:

  - URL pattern: regex matching listing URLs for this ATS, with slug capture group
  - active_ids_fetcher: callable(slug, **kwargs) -> (set[str], err) — used by
    validate-jobs.py to confirm a candidate's ID is still in the active job set
  - active_validate_supported: bool — True if validate-jobs can use this ATS's
    API for confirmation. False means we recognize the URL but don't have a way
    to confirm live state (would mark candidates "uncertain" if no fallback).

The fetcher counterpart (full-job fetch + normalize for fetch-and-diff.py) lives
in fetch-and-diff.py's own ATS_FETCHERS dict — adding a new ATS needs both
sides registered. v3.1.0 ships the validate side here; v3.1.1 ships the
fetch-and-diff side for Lever/Teamtailor/Homerun.

URL patterns intentionally extract only the slug (not the job ID); active-ID
fetching uses the slug-keyed API endpoint and returns the full active set,
against which Pass 2 tests each candidate's job ID for set membership.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple


# HTTP helper — shared with fetch-and-diff.py / validate-jobs.py / validate-favorites.py (legacy slug-variant probing)
# User-Agent: a custom string ("ai50-job-search/...") trips bot-filters on some
# boards (notably OpenAI's Ashby endpoint at api.ashbyhq.com/posting-api/job-board/openai
# returns 403 for it). Use a real-browser UA to avoid that class of block. We're
# making polite, low-volume read-only calls to public job boards — this is well
# within their ToS as long as we don't hammer them. Identifying as a custom
# scraper invited 403s without making us "more legitimate."
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT_S  = 20


def http_get(url: str, accept: str = "application/json") -> Tuple[Optional[bytes], Optional[str]]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except urllib.error.URLError as e:
        return None, f"urlerror:{getattr(e, 'reason', e)}"
    except Exception as e:
        return None, f"error:{e}"


# === API endpoints (v3.1.0 + extensions in v3.1.1) ============================

ASHBY_API      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

# Greenhouse: classic + EU data residency. Same path structure, different host.
GREENHOUSE_API_HOSTS = [
    "https://boards-api.greenhouse.io",       # classic / US
    "https://boards-api.eu.greenhouse.io",    # EU data residency
]
GREENHOUSE_API_PATH = "/v1/boards/{slug}/jobs"

COMEET_TOKEN_RE = re.compile(r'"token"\s*:\s*"([^"]+)"')
COMEET_API     = "https://www.comeet.co/careers-api/2.0/company/{company_id}/positions?token={token}&details=full"

LEVER_API      = "https://api.lever.co/v0/postings/{slug}?mode=json"

# v3.1.1 additions:
TEAMTAILOR_API = "https://{slug}.teamtailor.com/api/v1/jobs?page%5Bsize%5D=200"
HOMERUN_API    = "https://api.homerun.co/v1/jobs/?company_subdomain={slug}"


# === Active-ID fetchers — one per supported ATS ===============================

def fetch_active_ids_ashby(slug: str, **_) -> Tuple[set, Optional[str]]:
    data, err = http_get(ASHBY_API.format(slug=slug))
    if err:
        return set(), err
    try:
        return {str(j.get("id")) for j in json.loads(data.decode("utf-8")).get("jobs", []) if j.get("id")}, None
    except Exception as e:
        return set(), f"parse:{e}"


def fetch_active_ids_greenhouse(slug: str, **_) -> Tuple[set, Optional[str]]:
    """Try classic API first; on 404, try EU data-residency API.

    A company on Greenhouse-EU returns 404 from the classic boards-api and
    vice-versa. Trying both eliminates the per-ATS region branch.
    """
    last_err = None
    for host in GREENHOUSE_API_HOSTS:
        data, err = http_get(host + GREENHOUSE_API_PATH.format(slug=slug))
        if not err:
            try:
                return {str(j.get("id")) for j in json.loads(data.decode("utf-8")).get("jobs", []) if j.get("id")}, None
            except Exception as e:
                return set(), f"parse:{e}"
        last_err = err
        if not err.startswith("http_404"):
            # Non-404 error (5xx, network) — don't try the other host
            return set(), err
    return set(), last_err  # 404 from both hosts


def fetch_active_ids_comeet(slug: str, company_id: str = "", careers_url: str = "", **_) -> Tuple[set, Optional[str]]:
    if not company_id:
        return set(), "missing_company_id"
    careers_url = careers_url or f'https://www.comeet.com/jobs/{slug}/{company_id}'
    page, err = http_get(careers_url, accept="text/html")
    if err:
        return set(), f"page_{err}"
    page_text = page.decode("utf-8", errors="replace")
    m = COMEET_TOKEN_RE.search(page_text)
    if not m:
        return set(), "token_not_found"
    token = m.group(1)
    api_data, err = http_get(COMEET_API.format(company_id=company_id, token=token))
    if err:
        return set(), f"api_{err}"
    try:
        positions = json.loads(api_data.decode("utf-8"))
    except Exception as e:
        return set(), f"parse:{e}"
    if not isinstance(positions, list):
        return set(), "unexpected_shape"
    return {str(p.get("uid") or p.get("id")) for p in positions if (p.get("uid") or p.get("id"))}, None


def fetch_active_ids_lever(slug: str, **_) -> Tuple[set, Optional[str]]:
    data, err = http_get(LEVER_API.format(slug=slug))
    if err:
        return set(), err
    try:
        # Lever returns a flat array of postings, each with an `id` field (UUID).
        postings = json.loads(data.decode("utf-8"))
        if not isinstance(postings, list):
            return set(), "unexpected_shape"
        return {str(p.get("id")) for p in postings if p.get("id")}, None
    except Exception as e:
        return set(), f"parse:{e}"


def fetch_active_ids_teamtailor(slug: str, **_) -> Tuple[set, Optional[str]]:
    """Teamtailor JSON:API — returns active jobs at <slug>.teamtailor.com/api/v1/jobs.

    Each job has an `id` field (numeric string). Pagination via JSON:API links.meta.
    For most company boards under 200 active jobs, single-page fetch suffices.
    """
    data, err = http_get(TEAMTAILOR_API.format(slug=slug))
    if err:
        return set(), err
    try:
        body = json.loads(data.decode("utf-8"))
        items = body.get("data", [])
        return {str(j.get("id")) for j in items if j.get("id")}, None
    except Exception as e:
        return set(), f"parse:{e}"


def fetch_active_ids_homerun(slug: str, **_) -> Tuple[set, Optional[str]]:
    """Homerun — public job-board API at api.homerun.co/v1/jobs/?company_subdomain=<slug>.

    Returns array of jobs with `id` field. Companies use <slug>.homerun.co for
    user-facing pages but the API is centralized.
    """
    data, err = http_get(HOMERUN_API.format(slug=slug))
    if err:
        return set(), err
    try:
        body = json.loads(data.decode("utf-8"))
        # Response shape may be {"jobs": [...]} or a bare array — handle both
        items = body.get("jobs", body) if isinstance(body, dict) else body
        if not isinstance(items, list):
            return set(), "unexpected_shape"
        return {str(j.get("id")) for j in items if j.get("id")}, None
    except Exception as e:
        return set(), f"parse:{e}"


# === Adapter registry =========================================================

# Each entry: ats_name -> {url_pattern, active_ids_fetcher, active_validate_supported}
# Adding a new ATS = add an entry here + add fetch + normalize in fetch-and-diff.py.
ATS_ADAPTERS: dict = {
    "ashby": {
        "url_pattern": re.compile(r'^https?://(?:jobs|job-boards)\.ashbyhq\.com/([^/]+)'),
        "active_ids_fetcher": fetch_active_ids_ashby,
        "active_validate_supported": True,
    },
    "greenhouse": {
        # Classic + EU data residency
        "url_pattern": re.compile(r'^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([^/]+)'),
        "active_ids_fetcher": fetch_active_ids_greenhouse,
        "active_validate_supported": True,
    },
    "comeet": {
        "url_pattern": re.compile(r'^https?://www\.comeet\.com/jobs/([^/]+)'),
        "active_ids_fetcher": fetch_active_ids_comeet,
        "active_validate_supported": True,
    },
    "lever": {
        "url_pattern": re.compile(r'^https?://jobs\.lever\.co/([^/]+)'),
        "active_ids_fetcher": fetch_active_ids_lever,
        "active_validate_supported": True,
    },
    "teamtailor": {
        # <slug>.teamtailor.com/jobs/<id>-<slug-of-title>
        "url_pattern": re.compile(r'^https?://([a-z0-9-]+)\.teamtailor\.com/jobs/'),
        "active_ids_fetcher": fetch_active_ids_teamtailor,
        "active_validate_supported": True,
    },
    "homerun": {
        # <slug>.homerun.co/<path> — user-facing pages on subdomain
        "url_pattern": re.compile(r'^https?://([a-z0-9-]+)\.homerun\.co/'),
        "active_ids_fetcher": fetch_active_ids_homerun,
        "active_validate_supported": True,
    },
    "scrape": {
        # Generic LLM-extracted careers-page fallback. v3.2.0 implemented this in
        # Python via direct urllib calls to api.anthropic.com (required ANTHROPIC_API_KEY).
        # v4.0.0 reimplemented as a Claude Code agent (.claude/agents/scrape-extract.md)
        # so users don't need an API key. Per-company opt-in via {ats: "scrape",
        # careers_url: "..."} in custom-companies.
        #
        # Pipeline shape (v4.0.0): fetch-and-diff.py emits a needs_scraping.json
        # entry; the search-roles agent dispatches scrape-extract per company in
        # parallel; scripts/diff-scrape.py computes the new/removed delta against
        # state. URL pattern is None — never auto-dispatched from a listing URL;
        # only used when explicitly tagged in custom-companies.
        # Validate side: not supported by an API, so candidates fetched via scrape
        # land as Status: Uncertain in the tracker (user spot-checks).
        "url_pattern": None,
        "active_ids_fetcher": None,
        "active_validate_supported": False,
    },
}


def ats_from_url(url: Optional[str]) -> Optional[Tuple[str, str]]:
    """Parse a listing URL to derive (ats_name, slug). Returns None if no pattern matches.

    Used as the primary dispatch signal in validate-jobs.py and validate-favorites.py (legacy slug-variant probing).
    Adapters with `url_pattern: None` (e.g. "scrape") are skipped — they're only
    matched when explicitly tagged in the custom-companies entry, not by URL inspection.
    """
    if not url:
        return None
    for ats, adapter in ATS_ADAPTERS.items():
        pattern = adapter.get("url_pattern")
        if pattern is None:
            continue
        m = pattern.match(url)
        if m:
            return ats, m.group(1)
    return None


def active_ids_for(ats: str, slug: str, **kwargs) -> Tuple[set, Optional[str]]:
    """Dispatch to the right ATS's active-id fetcher. Returns (set_of_ids, error_or_none)."""
    adapter = ATS_ADAPTERS.get(ats)
    if not adapter:
        return set(), f"unsupported_ats:{ats}"
    if not adapter["active_validate_supported"]:
        return set(), f"validate_not_supported:{ats}"
    return adapter["active_ids_fetcher"](slug, **kwargs)


def supported_ats_for_validate() -> set:
    """Names of ATS types that validate-jobs can confirm via API."""
    return {ats for ats, adapter in ATS_ADAPTERS.items() if adapter["active_validate_supported"]}
