"""
Tests for the HTML-based fetchers — comeet (token extraction) and html_static
(generic anchor scraping). These are integration-flavored unit tests: we mock
http_get so no network is hit, but we exercise the real parsing code paths.

Why this matters: HTML scraping is fragile. A site redesign that changes class
names or anchor structure will silently return zero jobs. These tests pin
the parsing behavior to known-good fixture HTML so future regressions surface
loudly.
"""

import json
import unittest
from unittest.mock import patch

from tests._helpers import load_fad, read_fixture

fad = load_fad()


class HtmlStaticFetcherTests(unittest.TestCase):
    def test_surge_html_extracts_titles_via_data_job_attr(self):
        """Surge AI uses nested HTML inside the anchor — title lives in a
        data-job='title' div, not in the anchor's plain text. The fetcher
        must dig past the wrappers and use the data-job attribute."""
        company = {
            "name": "Surge AI",
            "ats": "html_static",
            "careers_url": "https://surgehq.ai/careers",
            "link_pattern": r"^/?careers/[a-z][a-z0-9-]+/?$",
            "source": "ai50",
        }
        html = read_fixture("surge-careers-snippet.html").encode("utf-8")
        with patch.object(fad, "http_get", return_value=(html, None)):
            jobs, err = fad.fetch_html_static(company)

        self.assertIsNone(err)
        titles = sorted(j["title"] for j in jobs)
        self.assertEqual(titles, [
            "AI Operations Engineer",
            "Account Manager",
            "Forward Deployed Engineer",
        ])
        # Footer links (/about-us, /privacy) must NOT match the pattern.
        slugs = sorted(j["id"] for j in jobs)
        self.assertEqual(slugs, [
            "account-manager", "ai-operations-engineer", "forward-deployed-engineer",
        ])

    def test_html_static_returns_error_when_pattern_missing(self):
        """Misconfigured company entry (no link_pattern) must error, not
        silently match every anchor on the page."""
        company = {"name": "X", "ats": "html_static", "careers_url": "https://x"}
        with patch.object(fad, "http_get", return_value=(b"<a href='/a'>x</a>", None)):
            jobs, err = fad.fetch_html_static(company)
        self.assertEqual(jobs, [])
        self.assertEqual(err, "missing_careers_url_or_pattern")

    def test_html_static_propagates_http_errors(self):
        company = {
            "name": "X", "ats": "html_static",
            "careers_url": "https://x", "link_pattern": r"^/jobs/.+",
        }
        with patch.object(fad, "http_get", return_value=(None, "http_503")):
            jobs, err = fad.fetch_html_static(company)
        self.assertEqual(jobs, [])
        self.assertEqual(err, "http_503")


class ComeetFetcherTests(unittest.TestCase):
    def test_extracts_token_from_company_data_blob(self):
        """The Comeet careers page is JS-rendered, but the bootstrap HTML
        embeds COMPANY_DATA with a public read-only `token`. The fetcher
        scrapes that token, then calls the careers-api with it."""
        company = {
            "name": "Acme", "ats": "comeet", "slug": "acme",
            "company_id": "17.008",
            "careers_url": "https://www.comeet.com/jobs/acme/17.008",
            "source": "ai50",
        }
        page_html = read_fixture("comeet-bootstrap-snippet.html").encode("utf-8")
        api_response = json.dumps([
            {"uid": "X1.AAA", "name": "Senior CSM",
             "location": {"city": "Berlin", "country": "DE"},
             "department": {"name": "CS"},
             "url_active_post_url": "https://www.comeet.com/jobs/acme/17.008/csm/X1.AAA"},
            {"uid": "X2.BBB", "name": "Sales Engineer",
             "location": {"city": "London", "country": "GB"}},
        ]).encode("utf-8")

        # http_get is called twice — once for the HTML page, once for the API.
        # patch.side_effect lets us return different values per call.
        with patch.object(fad, "http_get", side_effect=[
            (page_html, None),
            (api_response, None),
        ]):
            jobs, err = fad.fetch_comeet(company)

        self.assertIsNone(err)
        self.assertEqual(len(jobs), 2)
        first = next(j for j in jobs if j["id"] == "X1.AAA")
        self.assertEqual(first["title"], "Senior CSM")
        self.assertEqual(first["location"], "Berlin / DE")
        self.assertEqual(first["department"], "CS")

    def test_missing_company_id_errors_loudly(self):
        company = {"name": "X", "ats": "comeet", "slug": "x",
                   "careers_url": "https://example.com"}
        jobs, err = fad.fetch_comeet(company)
        self.assertEqual(jobs, [])
        self.assertEqual(err, "missing_company_id")

    def test_token_not_found_errors(self):
        """If the bootstrap HTML doesn't contain a token (e.g. Comeet
        redesigns), error out rather than silently returning [] which would
        look like 'no jobs' on the next run."""
        company = {"name": "X", "ats": "comeet", "slug": "x",
                   "company_id": "1.000", "careers_url": "https://x"}
        with patch.object(fad, "http_get", return_value=(b"<html>no token here</html>", None)):
            jobs, err = fad.fetch_comeet(company)
        self.assertEqual(jobs, [])
        self.assertEqual(err, "token_not_found")


class StaticRolesFetcherTests(unittest.TestCase):
    def test_static_roles_returns_inline_list(self):
        """static_roles ats type just returns whatever is in the company
        config — Midjourney's hardcoded role list."""
        company = {
            "name": "Midjourney",
            "ats": "static_roles",
            "careers_url": "https://midjourney.com/careers",
            "static_roles": [
                {"id": "r1", "title": "AI Researcher", "category": "Research", "description": "x"},
                {"id": "r2", "title": "Data Engineer", "category": "Frontend", "description": "y"},
            ],
            "source": "ai50",
        }
        jobs, err = fad.fetch_static_roles(company)
        self.assertIsNone(err)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["title"], "AI Researcher")
        self.assertEqual(jobs[0]["url"], "https://midjourney.com/careers")

    def test_static_roles_empty_list_is_ok(self):
        company = {"name": "X", "ats": "static_roles", "static_roles": []}
        jobs, err = fad.fetch_static_roles(company)
        self.assertEqual(jobs, [])
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
