#!/usr/bin/env python3
"""Shared ATS adapter registry — single source of truth for ATS support.

Purpose: extracted from validate-jobs.py / validate-favorites.py / fetch-and-diff.py
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
import xml.etree.ElementTree as ET
from typing import Callable, Optional, Tuple


# HTTP helper — shared with fetch-and-diff.py / validate-jobs.py / validate-favorites.py
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
# Teamtailor's previously-documented /api/v1/jobs JSON:API endpoint returns
# 404 for every board tested 2026-05 (Botify, Klarna, Polestar, Oneflow);
# their public read path is now the RSS feed at /jobs.rss with a custom
# https://teamtailor.com/locations namespace carrying location + department.
# Switched here as primary; the RSS feed is well-documented and stable.
TEAMTAILOR_API = "https://{slug}.teamtailor.com/jobs.rss"
HOMERUN_API    = "https://api.homerun.co/v1/jobs/?company_subdomain={slug}"

# feature/more-ats: SmartRecruiters / Workable / Recruitee (Easy tier)
# Public read-only JSON APIs. SmartRecruiters paginates via offset/limit; the
# others return everything in one page for typical company sizes.
SMARTRECRUITERS_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}"
# Workable: use the public widget endpoint (Workable's apparent public surface
# for embed/listing). The /api/v3/accounts/{slug}/jobs path appears in older
# docs but returns 404 on apply.workable.com; the v1 widget endpoint is what
# active boards actually serve. Returns {"name", "description", "jobs": [...]}.
WORKABLE_API        = "https://apply.workable.com/api/v1/widget/accounts/{slug}"
RECRUITEE_API       = "https://{slug}.recruitee.com/api/offers/"

# Medium tier: Personio (XML feed) and BambooHR (JSON).
# Personio's job board lives under {slug}.jobs.personio.de — the .de TLD is
# canonical; .com / .es / .it variants exist but the .de XML feed is what
# every Personio tenant exposes regardless of UI language. Root element is
# <workzag-jobs> with child <position> entries.
PERSONIO_API  = "https://{slug}.jobs.personio.de/xml"
# BambooHR's /careers/list returns {"result": [...], "meta": {"totalCount": N}}.
# Each result is a minimal job summary; full body needs a separate /jobs/{id}.json call.
BAMBOOHR_API  = "https://{slug}.bamboohr.com/careers/list"


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
    """Teamtailor: active jobs via per-board RSS feed at <slug>.teamtailor.com/jobs.rss.

    The previously-used /api/v1/jobs JSON:API endpoint returns 404 across
    every Teamtailor board tested in 2026-05; their public read path is now
    the RSS feed. Each <item> has a <guid> (UUID) we use as the stable ID.
    """
    data, err = http_get(TEAMTAILOR_API.format(slug=slug), accept="application/rss+xml")
    if err:
        return set(), err
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return set(), f"parse:{e}"
    return {(item.findtext("guid") or "").strip() for item in root.iter("item") if item.findtext("guid")}, None


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


def fetch_active_ids_smartrecruiters(slug: str, **_) -> Tuple[set, Optional[str]]:
    """SmartRecruiters public postings API — paginated.

    Returns {"content": [...], "totalFound": N, "limit": 100, "offset": ...}.
    Companies range from a handful of jobs to multiple thousands (SAP, Bosch),
    so we must paginate via offset until totalFound is exhausted. Hard cap at
    20 pages (2000 jobs) to bound a runaway response.
    """
    ids: set = set()
    offset = 0
    for _page in range(20):
        data, err = http_get(SMARTRECRUITERS_API.format(slug=slug, offset=offset))
        if err:
            return set(), err
        try:
            body = json.loads(data.decode("utf-8"))
        except Exception as e:
            return set(), f"parse:{e}"
        content = body.get("content", [])
        if not isinstance(content, list):
            return set(), "unexpected_shape"
        for j in content:
            if j.get("id"):
                ids.add(str(j["id"]))
        total = body.get("totalFound", 0)
        offset += len(content)
        if not content or offset >= total:
            break
    return ids, None


def fetch_active_ids_workable(slug: str, **_) -> Tuple[set, Optional[str]]:
    """Workable public widget API at apply.workable.com/api/v1/widget/accounts/{slug}.

    Response: {"name": "<company>", "description": "...", "jobs": [{...}, ...]}.
    Each job is keyed by `shortcode` (e.g. "ABC123") which appears in the
    user-facing URL — that's what we use as the stable ID.
    """
    data, err = http_get(WORKABLE_API.format(slug=slug))
    if err:
        return set(), err
    try:
        body = json.loads(data.decode("utf-8"))
    except Exception as e:
        return set(), f"parse:{e}"
    items = body.get("jobs") if isinstance(body, dict) else body
    if not isinstance(items, list):
        return set(), "unexpected_shape"
    return {str(j.get("shortcode") or j.get("id")) for j in items if (j.get("shortcode") or j.get("id"))}, None


def fetch_active_ids_recruitee(slug: str, **_) -> Tuple[set, Optional[str]]:
    """Recruitee public offers API at {slug}.recruitee.com/api/offers/.

    Response: {"offers": [{id, slug, title, ...}, ...]}. Recruitee uses
    numeric `id` plus a string `slug`; we key on `id` since it's stable.
    """
    data, err = http_get(RECRUITEE_API.format(slug=slug))
    if err:
        return set(), err
    try:
        body = json.loads(data.decode("utf-8"))
    except Exception as e:
        return set(), f"parse:{e}"
    items = body.get("offers", []) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return set(), "unexpected_shape"
    return {str(j.get("id")) for j in items if j.get("id")}, None


def fetch_active_ids_personio(slug: str, **_) -> Tuple[set, Optional[str]]:
    """Personio public XML feed at {slug}.jobs.personio.de/xml.

    Root <workzag-jobs> contains <position> children. Each position has an
    <id> child carrying the numeric ID we use as the stable key.

    Note on TLD: Personio publishes .de / .com / .es / .it variants of the
    job board, but the XML feed is mirrored across all of them; .de is the
    historical canonical and works for every tenant regardless of UI locale.
    """
    data, err = http_get(PERSONIO_API.format(slug=slug), accept="application/xml")
    if err:
        return set(), err
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return set(), f"parse:{e}"
    ids = set()
    # Positions can sit either directly under root (<workzag-jobs>) or wrapped
    # in <positions>; iterate both shapes defensively.
    for pos in root.iter("position"):
        pid = pos.findtext("id")
        if pid:
            ids.add(str(pid).strip())
    return ids, None


def fetch_active_ids_bamboohr(slug: str, **_) -> Tuple[set, Optional[str]]:
    """BambooHR public careers list at {slug}.bamboohr.com/careers/list.

    Response: {"meta": {"totalCount": N}, "result": [{id, jobOpeningName,
    departmentLabel, locationCity, locationState, locationCountry,
    employmentStatusLabel, atsUrl, ...}, ...]}.
    """
    data, err = http_get(BAMBOOHR_API.format(slug=slug))
    if err:
        return set(), err
    try:
        body = json.loads(data.decode("utf-8"))
    except Exception as e:
        return set(), f"parse:{e}"
    items = body.get("result", []) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return set(), "unexpected_shape"
    return {str(j.get("id")) for j in items if j.get("id")}, None


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
    "smartrecruiters": {
        # Two listing surfaces:
        #   careers.smartrecruiters.com/<company>/<job-id>-<slug>
        #   jobs.smartrecruiters.com/<company>/<job-id>
        "url_pattern": re.compile(r'^https?://(?:careers|jobs)\.smartrecruiters\.com/([^/]+)'),
        "active_ids_fetcher": fetch_active_ids_smartrecruiters,
        "active_validate_supported": True,
    },
    "workable": {
        # apply.workable.com/<slug>/[j/<shortcode>/...]
        "url_pattern": re.compile(r'^https?://apply\.workable\.com/([^/]+)'),
        "active_ids_fetcher": fetch_active_ids_workable,
        "active_validate_supported": True,
    },
    "recruitee": {
        # <slug>.recruitee.com/o/<offer-slug>
        "url_pattern": re.compile(r'^https?://([a-z0-9-]+)\.recruitee\.com/'),
        "active_ids_fetcher": fetch_active_ids_recruitee,
        "active_validate_supported": True,
    },
    "personio": {
        # <slug>.jobs.personio.de/job/<id> — slug captured before .jobs.
        # TLDs: .de canonical; .com / .es / .it accepted equivalently.
        "url_pattern": re.compile(r'^https?://([a-z0-9-]+)\.jobs\.personio\.(?:de|com|es|it)/'),
        "active_ids_fetcher": fetch_active_ids_personio,
        "active_validate_supported": True,
    },
    "bamboohr": {
        # <slug>.bamboohr.com/jobs/view.php?id=<id> or /careers/<id>
        "url_pattern": re.compile(r'^https?://([a-z0-9-]+)\.bamboohr\.com/'),
        "active_ids_fetcher": fetch_active_ids_bamboohr,
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

    Used as the primary dispatch signal in validate-jobs.py and validate-favorites.py.
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
