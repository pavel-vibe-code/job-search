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

    def test_greenhouse_eu_subdomain_classic(self):
        # Greenhouse EU data residency — boards.eu.greenhouse.io
        self.assertEqual(
            self.mod.ats_from_url("https://boards.eu.greenhouse.io/parloa/jobs/4799672101"),
            ("greenhouse", "parloa"),
        )

    def test_greenhouse_eu_subdomain_new(self):
        # Greenhouse EU data residency — job-boards.eu.greenhouse.io (the form Parloa/JetBrains use)
        self.assertEqual(
            self.mod.ats_from_url("https://job-boards.eu.greenhouse.io/jetbrains/jobs/4708584101"),
            ("greenhouse", "jetbrains"),
        )

    def test_greenhouse_custom_domain_with_gh_jid_returns_none(self):
        # Custom domains with gh_jid query param (Nebius, Make) — URL pattern doesn't reveal the slug,
        # so URL dispatch returns None. These fall through to name-index lookup, which now uses the
        # hydrated favorites file (post-v3.0.6 orchestrator wiring).
        self.assertIsNone(self.mod.ats_from_url("https://careers.nebius.com/?gh_jid=4809236101"))
        self.assertIsNone(self.mod.ats_from_url("https://www.make.com/en/careers-detail?gh_jid=6657197003"))

    # === v3.1.0 — new ATS adapters via shared module ============================

    def test_teamtailor_subdomain(self):
        self.assertEqual(
            self.mod.ats_from_url("https://botify.teamtailor.com/jobs/7365872-revenue-operations-manager-emea"),
            ("teamtailor", "botify"),
        )

    def test_teamtailor_with_hyphenated_slug(self):
        self.assertEqual(
            self.mod.ats_from_url("https://some-co.teamtailor.com/jobs/12345-role-name"),
            ("teamtailor", "some-co"),
        )

    def test_homerun_subdomain(self):
        self.assertEqual(
            self.mod.ats_from_url("https://gradium.homerun.co/"),
            ("homerun", "gradium"),
        )

    def test_homerun_with_path(self):
        self.assertEqual(
            self.mod.ats_from_url("https://acme.homerun.co/jobs/some-role"),
            ("homerun", "acme"),
        )


class SupportedAtsForValidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_script("validate-jobs.py", "validate_jobs")

    def test_v310_supports_six_ats_for_validate(self):
        # v3.1.0 expanded validate-able ATS set from {ashby, greenhouse, comeet}
        # to include lever, teamtailor, homerun. This test pins that count so a
        # regression that drops one is caught.
        supported = self.mod.supported_ats_for_validate()
        self.assertEqual(supported, {"ashby", "greenhouse", "comeet", "lever", "teamtailor", "homerun"})

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
