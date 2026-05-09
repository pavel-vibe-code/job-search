#!/usr/bin/env python3
"""
fetch-and-diff.py

Fetches job listings for all configured companies in parallel, diffs against
stored state, and outputs new/removed jobs as JSON to stdout.

Supported `ats` types in companies.json:
  - ashby           : api.ashbyhq.com posting API (JSON)
  - greenhouse      : boards-api.greenhouse.io (classic + EU residency, JSON)
  - lever           : api.lever.co/v0 postings (JSON)
  - comeet          : Comeet careers HTML scrape (no public API token)
  - teamtailor      : <slug>.teamtailor.com/api/v1 (JSON:API)
  - homerun         : api.homerun.co/v1 (JSON)
  - smartrecruiters : api.smartrecruiters.com/v1 postings (JSON, paginated)
  - workable        : apply.workable.com/api/v1/widget (JSON)
  - recruitee       : <slug>.recruitee.com/api/offers (JSON)
  - personio        : <slug>.jobs.personio.de/xml (XML)
  - bamboohr        : <slug>.bamboohr.com/careers/list (JSON)
  - html_static     : Generic static-HTML scrape with configurable link regex
  - static_roles    : Inline role list from companies.json (no HTTP). Surfaced as
                      a low-confidence notification, only when the inline role list
                      or profile changes — not saved to the tracker.
  - external        : Company has no scrapeable endpoint; emit a pointer to a
                      third-party source (e.g. Wellfound). Notification only.
  - skip            : Permanently ignored (URL recorded but no fetch attempted).

Usage:
    python3 fetch-and-diff.py [--plugin-root /path/to/plugin] [--state-file /path/to/state.json]

Output (stdout): JSON object with keys:
    new_jobs, removed_jobs, static_notifications, external_companies,
    skipped_companies, fetch_errors, stats

State file: read + written. Default {plugin_root}/state/companies.json.
"""

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Optional, Tuple
from urllib.parse import urljoin

# ── Argument parsing ──────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(
        prog="fetch-and-diff.py",
        description="Fetch job listings for all configured companies, diff against stored state, "
                    "and emit new/removed jobs as JSON on stdout. Used by the search-roles agent.",
        epilog="Output goes to stdout as a single JSON object. Run via the search-roles agent in "
               "normal use; calling this script directly is for debugging only.",
    )
    p.add_argument("--plugin-root", metavar="PATH", default=default_root,
                   help="Plugin directory (defaults to parent of scripts/).")
    p.add_argument("--state-file", metavar="PATH", default=None,
                   help="State JSON path (read + write). Default: <plugin-root>/state/companies.json.")
    p.add_argument("--profile-file", metavar="PATH", default=None,
                   help="profile.json path. Default: <plugin-root>/config/profile.json. "
                        "Override when the orchestrator hydrates profile from a non-default location "
                        "(e.g. /tmp/profile.json built from a Notion page in cloud mode).")
    p.add_argument("--custom-companies-file", metavar="PATH", default=None,
                   dest="custom_companies_file",
                   help="custom-companies.json path (additional companies on top of "
                        "AI 50 baseline). Default: <plugin-root>/config/custom-companies.json.")
    p.add_argument("--companies-file", metavar="PATH", default=None,
                   help="companies.json path. Default: <plugin-root>/config/companies.json.")
    p.add_argument("--description-limit", metavar="N", type=int, default=600,
                   help="Truncate each job's description text to N characters in the output. "
                        "Default 600 — keeps a 50-candidate output under ~40 KB so downstream "
                        "agents can Read() the file without hitting the 25k-token tool limit. "
                        "Raise to 2500 for richer scoring context if your runs are small "
                        "(under ~10 candidates).")
    # parse_known_args() instead of parse_args() so the module is importable
    # under unittest/pytest, which inject their own sys.argv flags. Unknown
    # flags are silently ignored at import time and only matter when the
    # script is invoked directly.
    args, _ = p.parse_known_args()
    return args


_ARGS = _parse_args()
PLUGIN_ROOT = _ARGS.plugin_root

COMPANIES_FILE = _ARGS.companies_file or os.path.join(PLUGIN_ROOT, "config", "companies.json")
CUSTOM_COMPANIES_FILE = _ARGS.custom_companies_file or os.path.join(PLUGIN_ROOT, "config", "custom-companies.json")
PROFILE_FILE   = _ARGS.profile_file   or os.path.join(PLUGIN_ROOT, "config", "profile.json")
STATE_FILE     = _ARGS.state_file     or os.path.join(PLUGIN_ROOT, "state", "companies.json")

ASHBY_API      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

FETCH_TIMEOUT      = 20
MAX_WORKERS        = 10
DESCRIPTION_LIMIT  = _ARGS.description_limit  # default 600; CLI-configurable
MAX_RESPONSE_BYTES = 20_000_000  # bumped from 6 MB so OpenAI (~10 MB) fits
TODAY              = date.today().isoformat()
USER_AGENT         = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# Use a real-browser UA. A custom string ("ai50-job-search/...") trips bot-filters
# on some boards — observed: OpenAI's Ashby returns 403 for it while other Ashby
# boards work fine. We're making polite, low-volume read-only calls to public job
# boards; identifying as a generic browser is well within their ToS.

# ── HTTP helper ───────────────────────────────────────────────────────────────

def http_get(url: str, accept: str = "application/json") -> Tuple[Optional[bytes], Optional[str]]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        })
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            content_length = int(resp.headers.get("Content-Length", 0) or 0)
            if content_length > MAX_RESPONSE_BYTES:
                return None, f"too_large:{content_length}b"
            data = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                return None, f"too_large:>{MAX_RESPONSE_BYTES}b"
            return data, None
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except urllib.error.URLError as e:
        return None, f"url_error:{e.reason}"
    except Exception as e:
        return None, f"error:{e}"


# ── Region classification + remote scoring ───────────────────────────────────
#
# These two helpers were previously prose instructions inside agents/search-roles.md,
# evaluated by an LLM. Moved into Python so they're unit-testable and can't drift
# between releases. The agent doc now points here.

REGION_KEYWORDS = [
    # (region, [regex patterns]) — order matters: narrower regions checked first.
    # Note: PRAGUE absorbs all Czech locations (Brno etc.) for simplicity. Most
    # Czech jobs are Prague-based; parse_region returns PRAGUE for Czechia so
    # the score table can favor candidates based there.
    ("PRAGUE", [
        r"\bprague\b", r"\bpraha\b", r"\bczech(ia)?\b", r"\bczech republic\b",
    ]),
    # UK_IE checked BEFORE EU_NON_UK so Dublin/London don't fall into EU_NON_UK.
    ("UK_IE", [
        r"\blondon\b", r"\bmanchester\b", r"\bbirmingham\b", r"\bedinburgh\b",
        r"\bglasgow\b", r"\bleeds\b", r"\bbristol\b", r"\bbelfast\b",
        r"\bdublin\b", r"\bcork\b", r"\bgalway\b",
        r"\bunited kingdom\b", r"\buk\b", r"\bireland\b",
        r"\bengland\b", r"\bscotland\b", r"\bwales\b",
    ]),
    ("APAC", [
        r"\bsingapore\b", r"\btokyo\b", r"\bseoul\b", r"\bbeijing\b",
        r"\bshanghai\b", r"\bhong ?kong\b", r"\bsydney\b", r"\bmelbourne\b",
        r"\bbengaluru\b", r"\bbangalore\b", r"\bmumbai\b", r"\bindia\b",
        r"\bjapan\b", r"\bkorea\b", r"\baustralia\b", r"\bchina\b",
    ]),
    ("LATAM", [
        r"\bbrazil\b", r"\bmexico\b", r"\bargentina\b", r"\bchile\b",
        r"\bcolombia\b", r"\bsão paulo\b", r"\bsao paulo\b",
        r"\bmexico city\b", r"\bbuenos aires\b",
    ]),
    ("MEA", [
        r"\bisrael\b", r"\btel aviv\b", r"\bdubai\b", r"\buae\b",
        r"\bsaudi\b", r"\bsouth africa\b",
    ]),
    ("NORTH_AMERICA", [
        r"\bunited states\b", r"\busa\b", r"\bu\.s\.\b", r"\bus\b",
        r"\bcanada\b", r"\btoronto\b", r"\bvancouver\b",
        r"\bnew york\b", r"\bnyc\b", r"\bsan francisco\b", r"\bsf\b",
        r"\bbay area\b", r"\bseattle\b", r"\bboston\b",
        r"\baustin\b", r"\bchicago\b", r"\blos angeles\b",
        r"\bdenver\b", r"\batlanta\b",
    ]),
    ("EU_NON_UK", [
        r"\bgermany\b", r"\bfrance\b", r"\bspain\b", r"\bnetherlands\b",
        r"\bbelgium\b", r"\bpoland\b", r"\bsweden\b", r"\bdenmark\b",
        r"\bfinland\b", r"\bnorway\b", r"\bportugal\b", r"\bitaly\b",
        r"\baustria\b", r"\bberlin\b", r"\bparis\b", r"\bmadrid\b",
        r"\bamsterdam\b", r"\bmunich\b", r"\bstockholm\b",
        r"\bcopenhagen\b", r"\bzurich\b", r"\bgeneva\b",
        r"\bbarcelona\b", r"\bbrussels\b", r"\bvienna\b",
        r"\beu\b", r"\bemea\b", r"\beurope\b",
    ]),
    ("GLOBAL_REMOTE", [
        r"\bglobal\b", r"\banywhere\b", r"\bworldwide\b",
        r"\bfully remote\b",
    ]),
]
_REGION_RE_CACHE = [(r, [re.compile(p, re.IGNORECASE) for p in pats])
                    for r, pats in REGION_KEYWORDS]


_NEGATION_RE = re.compile(r"\b(non[- ]|not[- ]|no[- ]|exclud)", re.IGNORECASE)


def classify_region(location: str) -> str:
    """Map a free-form location string to one of the canonical regions.
    Returns 'UNKNOWN' for empty input or no keyword match.

    Order of precedence (narrow → broad): PRAGUE, UK_IE, APAC, LATAM, MEA,
    NORTH_AMERICA, EU_NON_UK, GLOBAL_REMOTE. Picking UK_IE before EU_NON_UK
    is essential — Dublin and London must not get EU benefits if the user
    has the UK excluded.

    Defensive: strings containing negation prefixes ("non-", "not ", "no ",
    "exclud-") return UNKNOWN. This catches wizard-emitted meta-phrases like
    "all non-EU" — without this guard the regex `\\beu\\b` matches the EU in
    "non-EU" (hyphen is a word boundary), wrongly flagging the candidate's
    home region as excluded. Real country/city names never contain these
    negation prefixes, so the guard has no false positives.
    """
    if not location:
        return "UNKNOWN"
    if _NEGATION_RE.search(location):
        return "UNKNOWN"
    for region, regexes in _REGION_RE_CACHE:
        for r in regexes:
            if r.search(location):
                return region
    return "UNKNOWN"


ALL_REGIONS = (
    "PRAGUE", "EU_NON_UK", "UK_IE", "NORTH_AMERICA",
    "APAC", "LATAM", "MEA", "GLOBAL_REMOTE", "UNKNOWN",
)


def build_score_table(home_region: str, eligible_regions: set, excluded_regions: set) -> dict:
    """Build a (workplace_type, region) → 0..3 score table parameterised on the
    candidate's home region.

    Earlier versions hardcoded `PRAGUE` as the privileged region, which silently
    broke the plugin for any candidate not based in Czechia. This builder
    derives the table from the candidate's profile, so a Berlin-based user gets
    EU_NON_UK as their home, a NYC-based user gets NORTH_AMERICA, etc.

    Score philosophy:
      Working-from-home (remote in your own region):    3
      Remote in any eligible region OR global/unknown:  3
      Remote in NORTH_AMERICA (when not home/eligible): 2  (time-zone downgrade)
      Remote in any other non-excluded region:          1  (low priority, kept)
      Remote in an excluded region:                     0  (filter out)
      Hybrid in your home region:                       3
      Hybrid anywhere else:                             0  (can't commute)
      Onsite in your home region:                       3
      Onsite in any eligible region:                    1  (relocation downgrade)
      Onsite in excluded or any other region:           0
      Empty workplace_type behaves like Onsite.

    Args:
        home_region: e.g. 'PRAGUE', 'EU_NON_UK', 'NORTH_AMERICA'. Output of
            classify_region(profile.candidate.current_location). Falls back to
            'UNKNOWN' if the candidate's location can't be classified — in that
            case the home-region bonus disappears, but global-remote and
            eligible-region rules still work.
        eligible_regions: set of region labels the candidate is willing to
            consider, derived from profile.location_rules.eligible_regions.
        excluded_regions: set of region labels the candidate has explicitly
            excluded (e.g. UK_IE for "United Kingdom"), derived from
            profile.location_rules.excluded_countries.

    Returns:
        dict keyed by (workplace_type_lowercase, region) → score. Empty
        string for workplace_type is treated as Onsite (alias).
    """
    # If home_region is UNKNOWN (profile missing or location unclassifiable),
    # treat home-region matches as no-bonus — we can't claim hybrid-X is
    # commutable when we don't know where the candidate actually is. Onsite
    # and Hybrid all score 0 in that case; only remote-anywhere works.
    home_known = home_region != "UNKNOWN"

    table = {}
    for region in ALL_REGIONS:
        # Remote
        if region in excluded_regions:
            table[("remote", region)] = 0
        elif (home_known and region == home_region) or region in eligible_regions or region in ("GLOBAL_REMOTE", "UNKNOWN"):
            table[("remote", region)] = 3
        elif region == "NORTH_AMERICA":
            table[("remote", region)] = 2
        else:
            table[("remote", region)] = 1
        # Hybrid: home-region commute = full match (3); other eligible regions
        # are reachable via relocation = downgrade (1); excluded or out-of-scope
        # = 0. Mirrors the onsite logic so candidates open to relocation see
        # hybrid roles in their target region (e.g. "Hybrid Berlin" for a
        # Lisbon-based candidate willing to relocate within the EU).
        if region in excluded_regions:
            table[("hybrid", region)] = 0
        elif home_known and region == home_region:
            table[("hybrid", region)] = 3
        elif region in eligible_regions:
            table[("hybrid", region)] = 1  # relocation downgrade
        else:
            table[("hybrid", region)] = 0
        # Onsite + empty (empty = onsite alias)
        for wt in ("onsite", ""):
            if region in excluded_regions:
                table[(wt, region)] = 0
            elif home_known and region == home_region:
                table[(wt, region)] = 3
            elif region in eligible_regions:
                table[(wt, region)] = 1  # relocation downgrade
            else:
                table[(wt, region)] = 0
    return table


def _resolve_profile_locations() -> tuple:
    """Load HOME_REGION + eligible/excluded sets from PROFILE_FILE.
    Falls back to ('UNKNOWN', set(), set()) if the file is missing/malformed —
    the score table will then treat the candidate as having no home advantage,
    which is conservative (only global-remote and explicit-eligible roles surface).
    """
    try:
        with open(PROFILE_FILE) as f:
            profile = json.load(f)
    except Exception:
        return "UNKNOWN", set(), set()
    candidate_loc = (profile.get("candidate") or {}).get("current_location", "")
    home = classify_region(candidate_loc) or "UNKNOWN"
    rules = profile.get("location_rules") or {}
    eligible = {classify_region(r) for r in (rules.get("eligible_regions") or []) if r}
    eligible.discard("UNKNOWN")  # don't pollute with unmappable strings
    excluded = {classify_region(c) for c in (rules.get("excluded_countries") or []) if c}
    excluded.discard("UNKNOWN")
    return home, eligible, excluded


HOME_REGION, ELIGIBLE_REGIONS, EXCLUDED_REGIONS = _resolve_profile_locations()
SCORE_REMOTE_TABLE = build_score_table(HOME_REGION, ELIGIBLE_REGIONS, EXCLUDED_REGIONS)


def score_remote(workplace_type: str, region: str, home_region: str = None,
                 eligible_regions: set = None, excluded_regions: set = None) -> int:
    """Return a regional remote score 0..3 for a job's (workplace_type, region).

    By default uses the module-level HOME_REGION / ELIGIBLE_REGIONS / EXCLUDED_REGIONS
    loaded from PROFILE_FILE at import time. Tests and callers wanting to evaluate
    against a different profile can pass explicit overrides — in that case a fresh
    score table is built per call (cheap; the table is small).

    0 means 'filter out'; 1..3 means 'keep, with this preference weight'.
    """
    wt = (workplace_type or "").lower()
    if home_region is None and eligible_regions is None and excluded_regions is None:
        return SCORE_REMOTE_TABLE.get((wt, region), 0)
    table = build_score_table(
        home_region or HOME_REGION,
        eligible_regions if eligible_regions is not None else ELIGIBLE_REGIONS,
        excluded_regions if excluded_regions is not None else EXCLUDED_REGIONS,
    )
    return table.get((wt, region), 0)


# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_ashby(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(ASHBY_API.format(slug=company["slug"]))
    if err:
        return [], err
    try:
        return json.loads(data.decode("utf-8")).get("jobs", []), None
    except Exception as e:
        return [], f"parse_error:{e}"


GREENHOUSE_API_HOSTS = [
    "https://boards-api.greenhouse.io",       # classic / US
    "https://boards-api.eu.greenhouse.io",    # EU data residency (v3.1.1+)
]


def fetch_greenhouse(company: dict) -> Tuple[list, Optional[str]]:
    """Try classic Greenhouse API first; on 404, try EU data-residency API.

    A company on Greenhouse-EU returns 404 from the classic boards-api and
    vice-versa. v3.1.1 added EU host fallback (mirrors validate-jobs.py from
    v3.0.6) so Parloa, JetBrains, etc. (companies on EU data residency) can
    be fetched.
    """
    last_err = None
    for host in GREENHOUSE_API_HOSTS:
        data, err = http_get(host + f"/v1/boards/{company['slug']}/jobs")
        if not err:
            try:
                return json.loads(data.decode("utf-8")).get("jobs", []), None
            except Exception as e:
                return [], f"parse_error:{e}"
        last_err = err
        if not err.startswith("http_404"):
            return [], err
    return [], last_err


# ── Lever (v3.1.1) ──────────────────────────────────────────────────────────
LEVER_API = "https://api.lever.co/v0/postings/{slug}?mode=json"


def fetch_lever(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(LEVER_API.format(slug=company["slug"]))
    if err:
        return [], err
    try:
        postings = json.loads(data.decode("utf-8"))
        if not isinstance(postings, list):
            return [], "unexpected_shape"
        return postings, None
    except Exception as e:
        return [], f"parse_error:{e}"


# ── Teamtailor (v3.1.1, RSS in feature/more-ats) ───────────────────────────
# Public read surface migrated from /api/v1/jobs (JSON:API) to /jobs.rss
# sometime before 2026-05. The JSON endpoint returns 404 across every
# board tested; the RSS feed includes title, full-HTML description, link,
# guid (UUID), pubDate, remoteStatus, and a custom Teamtailor namespace
# https://teamtailor.com/locations carrying <locations>/<location> and
# <department>/<role> fields. We materialise items into dicts during
# parse so normalise_teamtailor stays a pure transform.
TEAMTAILOR_API = "https://{slug}.teamtailor.com/jobs.rss"
TT_NS          = {"tt": "https://teamtailor.com/locations"}


def fetch_teamtailor(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(TEAMTAILOR_API.format(slug=company["slug"]), accept="application/rss+xml")
    if err:
        return [], err
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return [], f"parse_error:{e}"
    out = []
    for item in root.findall(".//item"):
        # Decode <tt:locations>/<tt:location> nested children. Each <location>
        # has flat children (name/city/country/zip/address) under no namespace.
        locs = []
        locs_el = item.find("tt:locations", TT_NS)
        if locs_el is not None:
            for loc in locs_el.findall("tt:location", TT_NS):
                ch = {}
                for c in loc:
                    tag = c.tag.split("}", 1)[1] if "}" in c.tag else c.tag
                    ch[tag] = (c.text or "").strip()
                if ch:
                    locs.append(ch)
        out.append({
            "guid":          (item.findtext("guid") or "").strip(),
            "title":         (item.findtext("title") or "").strip(),
            "link":          (item.findtext("link") or "").strip(),
            "description":   (item.findtext("description") or "").strip(),
            "pubDate":       (item.findtext("pubDate") or "").strip(),
            "remoteStatus":  (item.findtext("remoteStatus") or "").strip().lower(),
            "department":    (item.findtext("tt:department", "", TT_NS) or "").strip(),
            "role":          (item.findtext("tt:role", "", TT_NS) or "").strip(),
            "locations":     locs,
        })
    return out, None


# ── Homerun (v3.1.1 + feed fallback feature/more-ats) ──────────────────────
# Two endpoint shapes seen in the wild:
#   - api.homerun.co/v1/jobs/?company_subdomain={slug} — older/internal API,
#     intermittently returns 404 even for boards that exist (gradium 2026-05).
#   - feed.homerun.co/{slug} — Atom XML feed, publicly stable, embedded in
#     every Homerun board's page header. We try the API first (it carries
#     more fields), and fall back to the Atom feed when the API 404s. Feed
#     entries get normalised into the same dict shape the API would have
#     returned so the existing normalise_homerun() works unchanged.
HOMERUN_API  = "https://api.homerun.co/v1/jobs/?company_subdomain={slug}"
HOMERUN_FEED = "https://feed.homerun.co/{slug}"


def _fetch_homerun_feed(slug: str) -> Tuple[list, Optional[str]]:
    """Atom-feed fallback for Homerun. Returns dicts shaped like the API
    response so normalise_homerun() works without branching."""
    data, err = http_get(HOMERUN_FEED.format(slug=slug), accept="application/atom+xml")
    if err:
        return [], err
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return [], f"parse_error:{e}"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", "", ns) or "").strip()
        link_el = entry.find("a:link", ns)
        url = link_el.get("href") if link_el is not None else ""
        # Atom <id> is unique per entry; Homerun's id is a tag URN — strip to
        # the last path segment for a stable, short ID. Falls back to URL slug.
        atom_id = (entry.findtext("a:id", "", ns) or "").strip()
        eid = atom_id.rsplit("/", 1)[-1] if atom_id else url.rstrip("/").rsplit("/", 1)[-1]
        summary = (entry.findtext("a:summary", "", ns) or "").strip()
        out.append({
            "id":          eid,
            "title":       title,
            "url":         url,
            "apply_url":   url,
            "location":    "",   # Atom feed doesn't carry it; normalise_homerun degrades gracefully
            "department":  "",
            "remote":      None,
            "description": summary,
            "created_at":  (entry.findtext("a:updated", "", ns) or "").strip(),
        })
    return out, None


def fetch_homerun(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(HOMERUN_API.format(slug=company["slug"]))
    if err:
        # Try the Atom-feed fallback. Surface the API error too if both fail.
        feed_jobs, feed_err = _fetch_homerun_feed(company["slug"])
        if feed_err:
            return [], f"api:{err}|feed:{feed_err}"
        return feed_jobs, None
    try:
        body = json.loads(data.decode("utf-8"))
    except Exception as e:
        # API returned non-JSON (e.g. HTML 404 page) — treat as miss + try feed.
        feed_jobs, feed_err = _fetch_homerun_feed(company["slug"])
        if feed_err:
            return [], f"api_parse:{e}|feed:{feed_err}"
        return feed_jobs, None
    items = body.get("jobs", body) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return [], "unexpected_shape"
    return items, None


# ── SmartRecruiters (feature/more-ats Easy tier) ────────────────────────────
SMARTRECRUITERS_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}"


def fetch_smartrecruiters(company: dict) -> Tuple[list, Optional[str]]:
    """SmartRecruiters: paginate via offset until totalFound is exhausted.

    Cap at 20 pages (2000 jobs) — large customers like Bosch can have
    thousands; the cap keeps a single run bounded while capturing the
    practical hiring set for any one slice.
    """
    out: list = []
    offset = 0
    last_err: Optional[str] = None
    for _page in range(20):
        data, err = http_get(SMARTRECRUITERS_API.format(slug=company["slug"], offset=offset))
        if err:
            last_err = err
            break
        try:
            body = json.loads(data.decode("utf-8"))
        except Exception as e:
            return out, f"parse_error:{e}"
        content = body.get("content", [])
        if not isinstance(content, list):
            return out, "unexpected_shape"
        out.extend(content)
        total = body.get("totalFound", 0)
        offset += len(content)
        if not content or offset >= total:
            break
    if last_err and not out:
        return [], last_err
    return out, None


# ── Workable (feature/more-ats Easy tier) ─────────────────────────────────
# Public widget endpoint — the v3 accounts API in older docs returns 404 on
# apply.workable.com; the v1 widget at /api/v1/widget/accounts/{slug} is what
# real boards serve. Response: {"name", "description", "jobs": [...]}.
WORKABLE_API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


def fetch_workable(company: dict) -> Tuple[list, Optional[str]]:
    """Workable widget API. Returns the `jobs` array."""
    data, err = http_get(WORKABLE_API.format(slug=company["slug"]))
    if err:
        return [], err
    try:
        body = json.loads(data.decode("utf-8"))
    except Exception as e:
        return [], f"parse_error:{e}"
    items = body.get("jobs") if isinstance(body, dict) else body
    if not isinstance(items, list):
        return [], "unexpected_shape"
    return items, None


# ── Recruitee (feature/more-ats Easy tier) ─────────────────────────────────
RECRUITEE_API = "https://{slug}.recruitee.com/api/offers/"


def fetch_recruitee(company: dict) -> Tuple[list, Optional[str]]:
    """Recruitee public offers API. Returns {offers: [...]}.

    Each offer has stable numeric `id` plus string `slug` used in URLs.
    """
    data, err = http_get(RECRUITEE_API.format(slug=company["slug"]))
    if err:
        return [], err
    try:
        body = json.loads(data.decode("utf-8"))
    except Exception as e:
        return [], f"parse_error:{e}"
    items = body.get("offers", []) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return [], "unexpected_shape"
    return items, None


# ── Personio (feature/more-ats Medium tier) ─────────────────────────────
# XML feed at <slug>.jobs.personio.de/xml. Root <workzag-jobs> contains
# <position> children with <id>, <name>, <office>, <department>,
# <recruitingCategory>, <employmentType>, <jobDescriptions>, etc.
PERSONIO_API = "https://{slug}.jobs.personio.de/xml"


def fetch_personio(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(PERSONIO_API.format(slug=company["slug"]), accept="application/xml")
    if err:
        return [], err
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return [], f"parse_error:{e}"
    # Materialize each <position> as a dict so the normaliser doesn't have to
    # re-walk the tree. Concatenate jobDescription fragments into a single
    # description string so the existing description-truncation logic applies.
    out = []
    for pos in root.iter("position"):
        descs = []
        for jd in pos.findall(".//jobDescription"):
            n = (jd.findtext("name") or "").strip()
            v = (jd.findtext("value") or "").strip()
            if n or v:
                descs.append(f"{n}\n{v}" if n else v)
        addl_offices = [o.text for o in pos.findall(".//additionalOffices/office") if o.text]
        out.append({
            "id":                 (pos.findtext("id") or "").strip(),
            "name":               (pos.findtext("name") or "").strip(),
            "subcompany":         (pos.findtext("subcompany") or "").strip(),
            "office":             (pos.findtext("office") or "").strip(),
            "additional_offices": addl_offices,
            "department":         (pos.findtext("department") or "").strip(),
            "recruiting_category": (pos.findtext("recruitingCategory") or "").strip(),
            "employment_type":    (pos.findtext("employmentType") or "").strip(),
            "schedule":           (pos.findtext("schedule") or "").strip(),
            "seniority":          (pos.findtext("seniority") or "").strip(),
            "years_of_experience": (pos.findtext("yearsOfExperience") or "").strip(),
            "office_remote":      (pos.findtext("officeRemote") or "").strip(),
            "created_at":         (pos.findtext("createdAt") or "").strip(),
            "description":        "\n\n".join(descs),
        })
    return out, None


# ── BambooHR (feature/more-ats Medium tier) ─────────────────────────────
# {slug}.bamboohr.com/careers/list returns {result: [...], meta: {totalCount}}.
BAMBOOHR_API = "https://{slug}.bamboohr.com/careers/list"


def fetch_bamboohr(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(BAMBOOHR_API.format(slug=company["slug"]))
    if err:
        return [], err
    try:
        body = json.loads(data.decode("utf-8"))
    except Exception as e:
        return [], f"parse_error:{e}"
    items = body.get("result", []) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return [], "unexpected_shape"
    return items, None


# ── Scrape — Claude Code agent fallback ──────────────────────────────────
# fetch-and-diff doesn't fetch scrape companies inline. It emits
# needs_scraping.json listing them; the search-roles agent dispatches
# scrape-extract per company (parallel via the Agent tool batched-call
# form) and uses scripts/diff-scrape.py to compute the new/removed delta
# against state. See agents/search-roles.md for the orchestration. This
# design avoids requiring an Anthropic API key — extraction runs against
# the user's Claude.ai subscription quota the same way other agents do.

NEEDS_SCRAPING_FILE = "/tmp/needs_scraping.json"


COMEET_TOKEN_RE  = re.compile(r'"token"\s*:\s*"([^"]+)"')
COMEET_API       = "https://www.comeet.co/careers-api/2.0/company/{company_id}/positions?token={token}&details=full"


def fetch_comeet(company: dict) -> Tuple[list, Optional[str]]:
    """Comeet's posting page is client-rendered, but the bootstrap HTML embeds a
    public read-only token. We extract it, then call the careers-api with that
    token to get a clean JSON listing."""
    careers_url = company.get("careers_url") or f'https://www.comeet.com/jobs/{company["slug"]}/{company.get("company_id", "")}'
    company_id = company.get("company_id")
    if not company_id:
        return [], "missing_company_id"

    page, err = http_get(careers_url, accept="text/html")
    if err:
        return [], f"page_{err}"
    try:
        page_text = page.decode("utf-8", errors="replace")
    except Exception as e:
        return [], f"decode_error:{e}"

    m = COMEET_TOKEN_RE.search(page_text)
    if not m:
        return [], "token_not_found"
    token = m.group(1)

    api_data, err = http_get(COMEET_API.format(company_id=company_id, token=token))
    if err:
        return [], f"api_{err}"
    try:
        positions = json.loads(api_data.decode("utf-8"))
    except Exception as e:
        return [], f"api_parse_error:{e}"
    if not isinstance(positions, list):
        return [], "api_unexpected_shape"

    out = []
    for p in positions:
        uid = p.get("uid") or p.get("id")
        if not uid:
            continue
        loc = p.get("location") or {}
        loc_name = " / ".join(filter(None, [
            (loc or {}).get("city", ""),
            (loc or {}).get("country", ""),
        ])) if isinstance(loc, dict) else str(loc)
        url = p.get("url_active_post_url") or urljoin(careers_url + "/", uid)
        out.append({
            "id":             str(uid),
            "title":          p.get("name", ""),
            "url":            url,
            "location":       loc_name,
            "department":     (p.get("department") or {}).get("name", "") if isinstance(p.get("department"), dict) else "",
            "is_remote":      bool(p.get("location_supports_remote")) if isinstance(loc, dict) else False,
        })
    return out, None


def fetch_html_static(company: dict) -> Tuple[list, Optional[str]]:
    """Generic static-HTML scrape. Requires `careers_url` and `link_pattern`
    (regex matching the href portion of each job link)."""
    careers_url = company.get("careers_url")
    pattern = company.get("link_pattern")
    if not (careers_url and pattern):
        return [], "missing_careers_url_or_pattern"
    data, err = http_get(careers_url, accept="text/html")
    if err:
        return [], err
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception as e:
        return [], f"decode_error:{e}"

    link_re = re.compile(pattern)
    # Allow nested HTML inside the anchor — the title may live in a child element.
    anchor_re = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    title_attr_re = re.compile(r'data-job="title"[^>]*>([^<]+)<', re.IGNORECASE)
    tag_strip_re = re.compile(r'<[^>]+>')
    seen = {}
    for href, inner in anchor_re.findall(text):
        if not link_re.search(href):
            continue
        full_url = href if href.startswith("http") else urljoin(careers_url, href)
        job_id = href.rstrip("/").split("/")[-1] or href
        if job_id in seen:
            continue
        # Prefer data-job="title" if present, else strip tags from inner content.
        m = title_attr_re.search(inner)
        title = m.group(1) if m else tag_strip_re.sub(" ", inner)
        title = html_lib.unescape(re.sub(r"\s+", " ", title)).strip()
        if not title:
            title = job_id.replace("-", " ").title()
        seen[job_id] = {"id": job_id, "title": title, "url": full_url}
    return list(seen.values()), None


def fetch_static_roles(company: dict) -> Tuple[list, Optional[str]]:
    """Hardcoded role list from companies.json. No HTTP. Each entry should have
    `id`, `title`, `description` (optional), `category` (optional)."""
    roles = company.get("static_roles") or []
    out = []
    for r in roles:
        rid = r.get("id") or hashlib.sha1(r.get("title", "").encode()).hexdigest()[:12]
        out.append({
            "id":    rid,
            "title": r.get("title", ""),
            "url":   company.get("careers_url", ""),
            "description": r.get("description", ""),
            "category":    r.get("category", ""),
        })
    return out, None


# ── Normalise ─────────────────────────────────────────────────────────────────

def normalise_ashby(job: dict, company: dict) -> dict:
    # Ashby's `isRemote=true` only means "this role supports remote as one
    # option" — the role can still actually be Hybrid in a specific city.
    # Use `workplaceType == "Remote"` (or an explicit "remote" in the location
    # string) as the authoritative signal. Hybrid roles will have is_remote=False
    # even if the API claims isRemote=true.
    workplace_type = (job.get("workplaceType") or "").strip()
    location_str   = (job.get("location") or "").strip()
    is_remote = (
        workplace_type.lower() == "remote"
        or "remote" in location_str.lower()
    ) and workplace_type.lower() != "hybrid"
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          job.get("title", ""),
        "url":            job.get("jobUrl") or job.get("applyUrl") or "",
        "location":       location_str,
        "is_remote":      is_remote,
        "workplace_type": workplace_type,
        "department":     job.get("department") or "",
        "published_at":   job.get("publishedAt") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "ashby",
        "description":    (job.get("descriptionPlain") or "")[:DESCRIPTION_LIMIT],
    }


def normalise_greenhouse(job: dict, company: dict) -> dict:
    loc = job.get("location") or {}
    loc_name = loc.get("name", "") if isinstance(loc, dict) else str(loc)
    depts = job.get("departments") or []
    dept = depts[0].get("name", "") if depts else ""
    loc_lower = loc_name.lower()
    # "Remote" wins only if "hybrid" isn't also in the location string.
    is_remote = "remote" in loc_lower and "hybrid" not in loc_lower
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          job.get("title", ""),
        "url":            job.get("absolute_url") or "",
        "location":       loc_name,
        "is_remote":      is_remote,
        "workplace_type": "Hybrid" if "hybrid" in loc_lower else ("Remote" if is_remote else ""),
        "department":     dept,
        "published_at":   job.get("updated_at") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "greenhouse",
        "description":    (job.get("content") or "")[:DESCRIPTION_LIMIT],
    }


def normalise_comeet(job: dict, company: dict) -> dict:
    return {
        "id":             job["id"],
        "company":        company["name"],
        "title":          job.get("title", ""),
        "url":            job.get("url", ""),
        "location":       job.get("location", ""),
        "is_remote":      bool(job.get("is_remote", False)),
        "workplace_type": "",
        "department":     job.get("department", ""),
        "published_at":   "",
        "source":         company.get("source", "unknown"),
        "ats":            "comeet",
        "description":    "",
    }


def normalise_html_static(job: dict, company: dict) -> dict:
    return {
        "id":             job["id"],
        "company":        company["name"],
        "title":          job.get("title", ""),
        "url":            job.get("url", ""),
        "location":       "",
        "is_remote":      False,
        "workplace_type": "",
        "department":     "",
        "published_at":   "",
        "source":         company.get("source", "unknown"),
        "ats":            "html_static",
        "description":    "",
    }


def normalise_static_role(job: dict, company: dict) -> dict:
    return {
        "id":             job["id"],
        "company":        company["name"],
        "title":          job.get("title", ""),
        "url":            job.get("url", ""),
        "location":       "",
        "is_remote":      False,
        "workplace_type": "",
        "department":     job.get("category", ""),
        "published_at":   "",
        "source":         company.get("source", "unknown"),
        "ats":            "static_roles",
        "description":    job.get("description", ""),
        "notification_only": True,
        "confidence":     "low",
    }


def normalise_lever(job: dict, company: dict) -> dict:
    """Lever posting structure (v0 API):
      {id (UUID), text, hostedUrl, applyUrl, categories: {location, team, department, commitment},
       descriptionPlain, workplaceType: "remote"|"hybrid"|"on-site", createdAt}
    """
    cats = job.get("categories", {}) or {}
    location = cats.get("location") or ""
    workplace_type = (job.get("workplaceType") or "").strip().lower()
    is_remote = workplace_type == "remote" or "remote" in location.lower()
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          job.get("text", ""),
        "url":            job.get("hostedUrl") or job.get("applyUrl") or "",
        "location":       location,
        "is_remote":      is_remote and workplace_type != "hybrid",
        "workplace_type": workplace_type.capitalize() if workplace_type else "",
        "department":     cats.get("department") or cats.get("team") or "",
        "published_at":   str(job.get("createdAt") or ""),
        "source":         company.get("source", "unknown"),
        "ats":            "lever",
        "description":    (job.get("descriptionPlain") or "")[:DESCRIPTION_LIMIT],
    }


def normalise_teamtailor(job: dict, company: dict) -> dict:
    """Teamtailor RSS item (already materialised by fetch_teamtailor):
      {guid, title, link, description, pubDate, remoteStatus, department,
       role, locations: [{name, city, country, zip, address}]}.
    `link` is the user-facing URL of the form /jobs/{numeric-id}-{slug};
    `guid` is a UUID we use as the stable internal ID.
    """
    locs = job.get("locations") or []
    if locs:
        first = locs[0]
        location = first.get("name") or " / ".join(filter(None, [
            first.get("city", ""), first.get("country", ""),
        ]))
    else:
        location = ""
    remote = (job.get("remoteStatus") or "").lower()
    is_remote = remote == "fully"
    is_hybrid = remote == "hybrid"
    return {
        "id":             str(job.get("guid") or job.get("link", "")),
        "company":        company["name"],
        "title":          job.get("title", ""),
        "url":            job.get("link", ""),
        "location":       location,
        "is_remote":      is_remote,
        "workplace_type": "Remote" if is_remote else ("Hybrid" if is_hybrid else ""),
        "department":     job.get("department") or job.get("role") or "",
        "published_at":   job.get("pubDate") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "teamtailor",
        # Teamtailor's RSS description is HTML — keep raw, downstream
        # truncation handles length.
        "description":    (job.get("description") or "")[:DESCRIPTION_LIMIT],
    }


def normalise_homerun(job: dict, company: dict) -> dict:
    """Homerun job structure (api.homerun.co/v1/jobs/):
      {id, title, location, employment_type, remote, description, url (or apply_url), created_at}
    Homerun's response shape varies; this normaliser handles the documented core fields
    and degrades gracefully for any company-specific extensions.
    """
    location = job.get("location") or ""
    if isinstance(location, dict):
        location = location.get("name", "") or " / ".join(filter(None, [
            location.get("city", ""), location.get("country", "")
        ]))
    is_remote = bool(job.get("remote") or job.get("is_remote")) or "remote" in str(location).lower()
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          job.get("title", ""),
        "url":            job.get("url") or job.get("apply_url") or "",
        "location":       str(location),
        "is_remote":      is_remote,
        "workplace_type": "Remote" if is_remote else "",
        "department":     job.get("department") or job.get("team") or "",
        "published_at":   job.get("created_at") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "homerun",
        "description":    (job.get("description") or "")[:DESCRIPTION_LIMIT],
    }


def normalise_smartrecruiters(job: dict, company: dict) -> dict:
    """SmartRecruiters posting structure:
      {id, name, uuid, jobAdId, refNumber, ref, company, releasedDate,
       location: {city, region, country, remote, hybrid, fullLocation},
       department: {id, label}, function: {label}, typeOfEmployment: {label},
       experienceLevel, customField, visibility, language, ...}
    `jobAdUrl` is documented but observed as None in real responses; the
    canonical user-facing URL is jobs.smartrecruiters.com/{slug}/{id}.
    """
    loc = job.get("location") or {}
    if isinstance(loc, dict):
        loc_name = loc.get("fullLocation") or " / ".join(filter(None, [
            loc.get("city", ""), loc.get("country", "")
        ]))
        is_remote = bool(loc.get("remote"))
        is_hybrid = bool(loc.get("hybrid"))
    else:
        loc_name = str(loc)
        is_remote = "remote" in loc_name.lower()
        is_hybrid = "hybrid" in loc_name.lower()
    department = job.get("department") or {}
    dept_label = department.get("label", "") if isinstance(department, dict) else str(department)
    job_id = str(job.get("id", ""))
    url = job.get("jobAdUrl") or (
        f"https://jobs.smartrecruiters.com/{company['slug']}/{job_id}" if job_id else ""
    )
    return {
        "id":             job_id,
        "company":        company["name"],
        "title":          job.get("name", ""),
        "url":            url,
        "location":       loc_name,
        "is_remote":      is_remote and not is_hybrid,
        "workplace_type": "Remote" if (is_remote and not is_hybrid) else ("Hybrid" if is_hybrid else ""),
        "department":     dept_label,
        "published_at":   job.get("releasedDate") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "smartrecruiters",
        # No description in the postings index — only the per-job detail call
        # carries it. Skip here; downstream search-roles can backfill via JD
        # fetch if a profile match warrants it.
        "description":    "",
    }


def normalise_workable(job: dict, company: dict) -> dict:
    """Workable v3 jobs entry:
      {id, title, full_title, shortcode, code, state, department, url, application_url,
       shortlink, location: {country, country_code, region, city, zip_code, telecommuting,
       workplace}, created_at, employment_type, language, ...}
    """
    loc = job.get("location") or {}
    if isinstance(loc, dict):
        loc_name = " / ".join(filter(None, [
            loc.get("city", ""), loc.get("region", ""), loc.get("country", "")
        ]))
        # Workable's `telecommuting=true` is the canonical remote flag;
        # `workplace` may be "remote" / "hybrid" / "on_site" on newer accounts.
        workplace = (loc.get("workplace") or "").lower()
        is_remote = bool(loc.get("telecommuting")) or workplace == "remote"
    else:
        loc_name = str(loc)
        workplace = ""
        is_remote = "remote" in loc_name.lower()
    return {
        "id":             str(job.get("shortcode") or job.get("id") or ""),
        "company":        company["name"],
        "title":          job.get("title") or job.get("full_title", ""),
        "url":            job.get("url") or job.get("shortlink") or job.get("application_url") or "",
        "location":       loc_name,
        "is_remote":      is_remote,
        "workplace_type": "Remote" if is_remote else ("Hybrid" if workplace == "hybrid" else ""),
        "department":     job.get("department") or "",
        "published_at":   job.get("created_at") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "workable",
        # Workable's index returns no description — `description` requires a
        # per-job detail call. Same trade-off as SmartRecruiters: keep empty,
        # let downstream backfill on demand.
        "description":    "",
    }


def normalise_recruitee(job: dict, company: dict) -> dict:
    """Recruitee offer structure:
      {id, slug, position, title, description, requirements, location, country_code,
       city, remote, department, careers_apply_url, careers_url, employment_type_code,
       created_at, ...}
    """
    location = " / ".join(filter(None, [
        job.get("city", "") or "",
        job.get("country_code", "") or "",
    ]))
    if not location:
        location = job.get("location") or ""
    is_remote = bool(job.get("remote")) or "remote" in str(location).lower()
    description = job.get("description") or ""
    if job.get("requirements"):
        description = (description + "\n\n" + job["requirements"])[:DESCRIPTION_LIMIT * 2]
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          job.get("title") or job.get("position", ""),
        "url":            job.get("careers_apply_url") or job.get("careers_url") or "",
        "location":       location,
        "is_remote":      is_remote,
        "workplace_type": "Remote" if is_remote else "",
        "department":     job.get("department") or "",
        "published_at":   job.get("created_at") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "recruitee",
        "description":    description[:DESCRIPTION_LIMIT],
    }


def normalise_personio(job: dict, company: dict) -> dict:
    """Personio position (already materialised by fetch_personio).

    Personio doesn't publish per-position user-facing URLs in the XML feed,
    so we construct the canonical board URL: {slug}.jobs.personio.de/job/{id}.
    `officeRemote` carries values like "fully", "partially", "no". `office`
    is the primary location; additional_offices captures multi-location postings.
    """
    location_parts = [job.get("office", "")]
    location_parts.extend(job.get("additional_offices") or [])
    location = " / ".join([p for p in location_parts if p])
    office_remote = (job.get("office_remote") or "").lower()
    is_remote = office_remote == "fully"
    is_hybrid = office_remote == "partially"
    department = job.get("department", "") or job.get("recruiting_category", "")
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          job.get("name", ""),
        "url":            f"https://{company['slug']}.jobs.personio.de/job/{job.get('id', '')}",
        "location":       location,
        "is_remote":      is_remote,
        "workplace_type": "Remote" if is_remote else ("Hybrid" if is_hybrid else ""),
        "department":     department,
        "published_at":   job.get("created_at") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "personio",
        # Personio's jobDescriptions tend to be HTML — keep raw, downstream
        # description-trim handles length. Pass it through DESCRIPTION_LIMIT
        # for parity with the other normalisers.
        "description":    (job.get("description") or "")[:DESCRIPTION_LIMIT],
    }


def normalise_bamboohr(job: dict, company: dict) -> dict:
    """BambooHR job summary fields (verified shape):
      {id, jobOpeningName, departmentId, departmentLabel, employmentStatusLabel,
       location: {city, state}, atsLocation: {country, state, province, city},
       isRemote, locationType}
    The /careers/list endpoint omits description, atsUrl, and datePosted; we
    construct the user-facing URL as {slug}.bamboohr.com/careers/{id} which
    is the canonical pattern across BambooHR career pages.

    `locationType`: BambooHR's API uses an opaque numeric code; "2" appears
    to be on-site; values for remote/hybrid not yet documented. Trust
    `isRemote` boolean when present; otherwise infer from location string.
    """
    loc = job.get("location") or {}
    ats_loc = job.get("atsLocation") or {}
    if isinstance(loc, dict):
        primary_loc = " / ".join(filter(None, [loc.get("city") or "", loc.get("state") or ""]))
    else:
        primary_loc = str(loc)
    if isinstance(ats_loc, dict):
        ats_loc_str = " / ".join(filter(None, [
            ats_loc.get("city") or "",
            ats_loc.get("state") or ats_loc.get("province") or "",
            ats_loc.get("country") or "",
        ]))
    else:
        ats_loc_str = ""
    location = primary_loc or ats_loc_str
    location_lower = location.lower()
    is_remote = bool(job.get("isRemote")) or "remote" in location_lower
    is_hybrid = "hybrid" in location_lower
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          job.get("jobOpeningName", ""),
        "url":            f"https://{company['slug']}.bamboohr.com/careers/{job.get('id', '')}",
        "location":       location,
        "is_remote":      is_remote and not is_hybrid,
        "workplace_type": "Remote" if (is_remote and not is_hybrid) else ("Hybrid" if is_hybrid else ""),
        "department":     job.get("departmentLabel") or "",
        "published_at":   "",
        "source":         company.get("source", "unknown"),
        "ats":            "bamboohr",
        # No description in /careers/list — only the per-job detail call has it.
        "description":    "",
    }


FETCHER_DISPATCH = {
    "ashby":           (fetch_ashby,           normalise_ashby),
    "greenhouse":      (fetch_greenhouse,      normalise_greenhouse),
    "comeet":          (fetch_comeet,          normalise_comeet),
    "lever":           (fetch_lever,           normalise_lever),           # v3.1.1
    "teamtailor":      (fetch_teamtailor,      normalise_teamtailor),      # v3.1.1
    "homerun":         (fetch_homerun,         normalise_homerun),         # v3.1.1
    "smartrecruiters": (fetch_smartrecruiters, normalise_smartrecruiters), # feature/more-ats Easy
    "workable":        (fetch_workable,        normalise_workable),        # feature/more-ats Easy
    "recruitee":       (fetch_recruitee,       normalise_recruitee),       # feature/more-ats Easy
    "personio":        (fetch_personio,        normalise_personio),        # feature/more-ats Medium
    "bamboohr":        (fetch_bamboohr,        normalise_bamboohr),        # feature/more-ats Medium
    # "scrape" intentionally absent (v4.0.0): scrape companies are handled
    # by the search-roles agent dispatching the scrape-extract Claude Code
    # agent, then diffing via scripts/diff-scrape.py. They land in the
    # `scrape_pending` output bucket below, not in `fetchable`.
    "html_static":     (fetch_html_static,     normalise_html_static),
    "static_roles":    (fetch_static_roles,    normalise_static_role),
}


def fetch_company(company: dict) -> Tuple[dict, list, Optional[str]]:
    ats = company.get("ats")
    fetcher = FETCHER_DISPATCH.get(ats)
    if fetcher is None:
        return company, [], "unsupported_ats"
    raw_jobs, err = fetcher[0](company)
    if err:
        return company, [], err
    norm = [fetcher[1](j, company) for j in raw_jobs]
    return company, norm, None


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def diff_company(company_key: str, current_jobs: list, state: dict) -> Tuple[list, list]:
    # Tolerate malformed state from earlier broken runs — bad entry = empty.
    company_state = state.get(company_key, {})
    if not isinstance(company_state, dict):
        company_state = {}
    known = company_state.get("jobs", {})
    if not isinstance(known, dict):
        known = {}
    current_by_id = {j["id"]: j for j in current_jobs}
    new_ids     = set(current_by_id) - set(known)
    removed_ids = set(known) - set(current_by_id)
    new_jobs = [current_by_id[jid] for jid in new_ids]
    removed_jobs = [{"id": jid, **known[jid]} for jid in removed_ids]
    return new_jobs, removed_jobs


# ── Show-once notifications (static_roles) ────────────────────────────────────

def profile_role_hash() -> str:
    """Hash of the candidate's role_types — used to detect when static_roles
    notifications should be re-shown after profile changes."""
    try:
        with open(PROFILE_FILE) as f:
            profile = json.load(f)
        relevant = json.dumps(profile.get("role_types", []), sort_keys=True)
        return hashlib.sha1(relevant.encode()).hexdigest()[:16]
    except Exception:
        return ""


def notification_signature(company_key: str, jobs: list, profile_hash: str) -> str:
    job_ids = sorted(j["id"] for j in jobs)
    blob = f"{company_key}|{profile_hash}|{','.join(job_ids)}"
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(COMPANIES_FILE) as f:
        companies_cfg = json.load(f)
    companies = list(companies_cfg["companies"])

    if os.path.exists(CUSTOM_COMPANIES_FILE):
        with open(CUSTOM_COMPANIES_FILE) as f:
            custom_companies = json.load(f)
        existing_names = {c["name"] for c in companies}
        for entry in custom_companies:
            if isinstance(entry, dict) and entry.get("name") not in existing_names and entry.get("ats"):
                companies.append(entry)

    state = load_state()
    profile_hash = profile_role_hash()
    notif_state = state.get("_meta", {}).get("notifications", {})

    fetchable      = [c for c in companies if c.get("ats") in FETCHER_DISPATCH]
    scrape_pending = [c for c in companies if c.get("ats") == "scrape"]
    external       = [c for c in companies if c.get("ats") == "external"]
    skipped        = [c for c in companies if c.get("ats") in ("skip", "chrome", "none")]

    # Emit scrape_pending list for the search-roles agent to pick up. It will
    # dispatch scrape-extract per entry (parallel via Agent tool batches), then
    # diff each result against state via scripts/diff-scrape.py.
    if scrape_pending:
        scrape_payload = [
            {
                "name":          c.get("name", ""),
                "careers_url":   c.get("careers_url", ""),
                "scrape_model":  c.get("scrape_model"),  # Optional per-company override
                "company_key":   f"scrape:{(c.get('slug') or c.get('name', '')).lower().replace(' ', '_')}",
                "source":        c.get("source", "unknown"),
            }
            for c in scrape_pending
            if c.get("careers_url")  # entries without careers_url are unfetchable; quietly skip
        ]
        with open(NEEDS_SCRAPING_FILE, "w") as f:
            json.dump(scrape_payload, f, indent=2, ensure_ascii=False)

    all_new_jobs: list = []
    all_removed:  list = []
    static_notifications: list = []
    errors: list = []
    stats = {
        "companies_total":      len(companies),
        "companies_fetchable":  len(fetchable),
        "companies_scrape_pending": len(scrape_pending),
        "companies_external":   len(external),
        "companies_skipped":    len(skipped),
        "companies_errored":    0,
        "total_jobs_fetched":   0,
        "new_jobs":             0,
        "removed_jobs":         0,
        "run_date":             TODAY,
    }

    new_state = dict(state)
    new_notif_state = dict(notif_state)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_company, c): c for c in fetchable}
        for future in as_completed(futures):
            company, jobs, error = future.result()
            company_key = f"{company['ats']}:{company.get('slug') or company.get('company_id') or company['name'].lower()}"

            if error:
                errors.append({"company": company["name"], "ats": company.get("ats"), "error": error})
                stats["companies_errored"] += 1
                continue

            stats["total_jobs_fetched"] += len(jobs)

            # Static_roles → emit as notifications, not as new_jobs. Show-once logic.
            if company.get("ats") == "static_roles":
                sig = notification_signature(company_key, jobs, profile_hash)
                prev_sig = new_notif_state.get(company_key, {}).get("signature")
                if sig != prev_sig:
                    static_notifications.append({
                        "company":     company["name"],
                        "careers_url": company.get("careers_url", ""),
                        "note":        company.get("note", "Always-hiring roles list — evaluate against profile, then mark seen."),
                        "roles":       jobs,
                    })
                    new_notif_state[company_key] = {"signature": sig, "last_shown": TODAY}
                continue

            # Real diff for ashby/greenhouse/comeet/html_static
            new_jobs, removed_jobs = diff_company(company_key, jobs, state)
            all_new_jobs.extend(new_jobs)
            all_removed.extend(removed_jobs)
            stats["new_jobs"]     += len(new_jobs)
            stats["removed_jobs"] += len(removed_jobs)

            new_state[company_key] = {
                "last_checked": TODAY,
                "company_name": company["name"],
                "jobs": {
                    j["id"]: {"title": j["title"], "url": j["url"], "company": j["company"]}
                    for j in jobs
                },
            }

    new_state.setdefault("_meta", {})["notifications"] = new_notif_state
    new_state["_meta"]["last_run"] = TODAY
    save_state(new_state)

    output = {
        "new_jobs":              all_new_jobs,
        "removed_jobs":          all_removed,
        "static_notifications":  static_notifications,
        "external_companies": [
            {
                "name":            c["name"],
                "careers_url":     c.get("careers_url", ""),
                "external_source": c.get("external_source", ""),
                "external_url":    c.get("external_url", ""),
                "note":            c.get("note", ""),
            }
            for c in external
        ],
        "scrape_pending": {
            "count":     len(scrape_pending),
            "companies": [c.get("name", "") for c in scrape_pending],
            "needs_scraping_file": NEEDS_SCRAPING_FILE if scrape_pending else None,
            "_note":     "These companies need scrape-extract agent dispatch. The "
                          "search-roles agent reads needs_scraping.json, invokes the "
                          "agent per entry, then runs scripts/diff-scrape.py to merge "
                          "the new/removed delta into all_new_jobs / all_removed and "
                          "persist the updated state.",
        },
        "skipped_companies": [
            {"name": c["name"], "ats": c.get("ats"), "reason": c.get("_skip_reason") or c.get("_note", "")}
            for c in skipped
        ],
        "fetch_errors": errors,
        "stats":        stats,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
