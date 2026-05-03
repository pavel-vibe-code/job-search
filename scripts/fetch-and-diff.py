#!/usr/bin/env python3
"""
fetch-and-diff.py

Fetches job listings for all configured companies in parallel, diffs against
stored state, and outputs new/removed jobs as JSON to stdout.

Supported `ats` types in companies.json:
  - ashby           : api.ashbyhq.com posting API (JSON)
  - greenhouse      : boards-api.greenhouse.io (JSON)
  - comeet          : Comeet careers HTML scrape (no public API token)
  - html_static     : Generic static-HTML scrape with configurable link regex
  - static_roles    : Inline role list from companies.json (no HTTP). Surfaced as
                      a low-confidence notification, only when the inline role list
                      or profile changes — not saved to the tracker.
  - external        : Company has no scrapeable endpoint; emit a pointer to a
                      third-party source (e.g. Wellfound). Notification only.
  - skip            : Permanently ignored.
  - chrome          : Legacy. Treated as `skip` with a deprecation note.

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
    p.add_argument("--favorites-file", metavar="PATH", default=None,
                   help="favorites.json path. Default: <plugin-root>/config/favorites.json.")
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
FAVORITES_FILE = _ARGS.favorites_file or os.path.join(PLUGIN_ROOT, "config", "favorites.json")
PROFILE_FILE   = _ARGS.profile_file   or os.path.join(PLUGIN_ROOT, "config", "profile.json")
STATE_FILE     = _ARGS.state_file     or os.path.join(PLUGIN_ROOT, "state", "companies.json")

ASHBY_API      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

FETCH_TIMEOUT      = 20
MAX_WORKERS        = 10
DESCRIPTION_LIMIT  = _ARGS.description_limit  # default 600; CLI-configurable
MAX_RESPONSE_BYTES = 20_000_000  # bumped from 6 MB so OpenAI (~10 MB) fits
TODAY              = date.today().isoformat()
USER_AGENT         = "ai50-job-search/2.0"

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


# ── Teamtailor (v3.1.1) ─────────────────────────────────────────────────────
TEAMTAILOR_API = "https://{slug}.teamtailor.com/api/v1/jobs?page%5Bsize%5D=200"


def fetch_teamtailor(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(TEAMTAILOR_API.format(slug=company["slug"]))
    if err:
        return [], err
    try:
        body = json.loads(data.decode("utf-8"))
        # JSON:API envelope: {data: [...], included: [...], meta: {...}}
        return body.get("data", []), None
    except Exception as e:
        return [], f"parse_error:{e}"


# ── Homerun (v3.1.1) ────────────────────────────────────────────────────────
HOMERUN_API = "https://api.homerun.co/v1/jobs/?company_subdomain={slug}"


def fetch_homerun(company: dict) -> Tuple[list, Optional[str]]:
    data, err = http_get(HOMERUN_API.format(slug=company["slug"]))
    if err:
        return [], err
    try:
        body = json.loads(data.decode("utf-8"))
        items = body.get("jobs", body) if isinstance(body, dict) else body
        if not isinstance(items, list):
            return [], "unexpected_shape"
        return items, None
    except Exception as e:
        return [], f"parse_error:{e}"


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
    """Teamtailor JSON:API entity:
      {id, type: "jobs", attributes: {title, body, pitch, location, language, status,
       remote-status, employment-type, created-at, updated-at, careersite-job-url}}
    """
    attrs = job.get("attributes", {}) or {}
    remote_status = (attrs.get("remote-status") or "").strip().lower()
    location = attrs.get("location") or ""
    is_remote = remote_status in ("fully-remote", "remote", "remote-only") or "remote" in str(location).lower()
    return {
        "id":             str(job.get("id", "")),
        "company":        company["name"],
        "title":          attrs.get("title", ""),
        "url":            attrs.get("careersite-job-url") or "",
        "location":       location if isinstance(location, str) else (location or {}).get("name", ""),
        "is_remote":      is_remote,
        "workplace_type": "Remote" if is_remote else ("Hybrid" if remote_status == "hybrid" else ""),
        "department":     attrs.get("department") or "",
        "published_at":   attrs.get("created-at") or "",
        "source":         company.get("source", "unknown"),
        "ats":            "teamtailor",
        # Teamtailor's body has HTML; pitch is plain. Prefer pitch when present.
        "description":    (attrs.get("pitch") or attrs.get("body") or "")[:DESCRIPTION_LIMIT],
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


FETCHER_DISPATCH = {
    "ashby":        (fetch_ashby,        normalise_ashby),
    "greenhouse":   (fetch_greenhouse,   normalise_greenhouse),
    "comeet":       (fetch_comeet,       normalise_comeet),
    "lever":        (fetch_lever,        normalise_lever),         # v3.1.1
    "teamtailor":   (fetch_teamtailor,   normalise_teamtailor),    # v3.1.1
    "homerun":      (fetch_homerun,      normalise_homerun),       # v3.1.1
    "html_static":  (fetch_html_static,  normalise_html_static),
    "static_roles": (fetch_static_roles, normalise_static_role),
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

    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE) as f:
            favorites = json.load(f)
        existing_names = {c["name"] for c in companies}
        for fav in favorites:
            if isinstance(fav, dict) and fav.get("name") not in existing_names and fav.get("ats"):
                companies.append(fav)

    state = load_state()
    profile_hash = profile_role_hash()
    notif_state = state.get("_meta", {}).get("notifications", {})

    fetchable     = [c for c in companies if c.get("ats") in FETCHER_DISPATCH]
    external      = [c for c in companies if c.get("ats") == "external"]
    skipped       = [c for c in companies if c.get("ats") in ("skip", "chrome", "none")]

    all_new_jobs: list = []
    all_removed:  list = []
    static_notifications: list = []
    errors: list = []
    stats = {
        "companies_total":     len(companies),
        "companies_fetchable": len(fetchable),
        "companies_external":  len(external),
        "companies_skipped":   len(skipped),
        "companies_errored":   0,
        "total_jobs_fetched":  0,
        "new_jobs":            0,
        "removed_jobs":        0,
        "run_date":            TODAY,
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
