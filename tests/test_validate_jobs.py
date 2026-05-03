"""Tests for validate-jobs.py — primarily the URL-based ATS dispatch
introduced in v2.5.0.

The previous validator architecture did name-index lookup only, which
created a real bug: jobs whose listing URL was unambiguously e.g.
job-boards.greenhouse.io/<co>/... could be marked uncertain because
the company's index entry had ats="skip" or wasn't there at all.

These tests pin the URL → (ats, slug) regex behavior."""
import unittest

from _helpers import load_script


class AtsFromUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_script("validate-jobs.py", "validate_jobs")

    def test_ashby_classic_subdomain(self):
        self.assertEqual(
            self.mod.ats_from_url("https://jobs.ashbyhq.com/togetherai/abc-123"),
            ("ashby", "togetherai"),
        )

    def test_ashby_new_subdomain(self):
        # Ashby launched job-boards.ashbyhq.com as an alternative host — same product.
        self.assertEqual(
            self.mod.ats_from_url("https://job-boards.ashbyhq.com/foo/job/xyz"),
            ("ashby", "foo"),
        )

    def test_greenhouse_classic_subdomain(self):
        self.assertEqual(
            self.mod.ats_from_url("https://boards.greenhouse.io/cohere/jobs/12345"),
            ("greenhouse", "cohere"),
        )

    def test_greenhouse_new_subdomain(self):
        # Greenhouse's job-boards.greenhouse.io is the newer interface; same backend API.
        self.assertEqual(
            self.mod.ats_from_url("https://job-boards.greenhouse.io/togetherai/jobs/5070981007"),
            ("greenhouse", "togetherai"),
        )

    def test_lever(self):
        # Lever is recognized as an ATS but not currently supported by the validator;
        # returns (lever, slug) so downstream code can dispatch or mark unsupported.
        self.assertEqual(
            self.mod.ats_from_url("https://jobs.lever.co/somecompany/abc-123"),
            ("lever", "somecompany"),
        )

    def test_comeet(self):
        self.assertEqual(
            self.mod.ats_from_url("https://www.comeet.com/jobs/wrike/A1.B2C/Frontend-Engineer"),
            ("comeet", "wrike"),
        )

    def test_http_protocol_matches(self):
        # Pattern accepts both http and https.
        self.assertEqual(
            self.mod.ats_from_url("http://boards.greenhouse.io/cohere/jobs/123"),
            ("greenhouse", "cohere"),
        )

    def test_unrecognized_host_returns_none(self):
        self.assertIsNone(self.mod.ats_from_url("https://example.com/careers/abc"))

    def test_workable_unsupported_returns_none(self):
        # Workable / Personio / etc. — not yet recognized; falls through to name-index.
        self.assertIsNone(self.mod.ats_from_url("https://apply.workable.com/foo/j/abc"))

    def test_empty_url_returns_none(self):
        self.assertIsNone(self.mod.ats_from_url(""))

    def test_none_url_returns_none(self):
        self.assertIsNone(self.mod.ats_from_url(None))

    def test_malformed_url_returns_none(self):
        # Not a URL at all.
        self.assertIsNone(self.mod.ats_from_url("just-some-text"))

    def test_ashby_without_path_returns_none(self):
        # Slug must be present after the host. Bare domain is not a listing URL.
        self.assertIsNone(self.mod.ats_from_url("https://jobs.ashbyhq.com/"))

    def test_greenhouse_company_with_hyphens(self):
        self.assertEqual(
            self.mod.ats_from_url("https://boards.greenhouse.io/back-market/jobs/4567"),
            ("greenhouse", "back-market"),
        )


if __name__ == "__main__":
    unittest.main()
