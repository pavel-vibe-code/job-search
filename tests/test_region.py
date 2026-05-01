"""
Tests for classify_region(), build_score_table(), and score_remote().

History:
- v2.1.0: region scoring lived as prose inside agents/search-roles.md, evaluated
  by an LLM. Bug B (non-EU roles slipping through the prefilter on a Prague-base
  profile) motivated moving the logic into Python.
- v2.2.0–2.2.1: SCORE_REMOTE_TABLE was a static dict pinning PRAGUE as the
  privileged home region.
- v2.2.2: parameterised. Home region + eligible + excluded sets are loaded from
  profile.json at module import; a different profile produces a different table.
  These tests pin the table-builder logic for each plausible home region so a
  Berlin-based or NYC-based user gets the same correctness guarantees that a
  Prague-based one gets.
"""

import unittest

from tests._helpers import load_fad

fad = load_fad()


class ClassifyRegionTests(unittest.TestCase):
    """One test method per region, plus edge cases for ambiguous strings."""

    def test_empty_returns_unknown(self):
        self.assertEqual(fad.classify_region(""), "UNKNOWN")
        self.assertEqual(fad.classify_region(None), "UNKNOWN")

    # PRAGUE — narrowest, must beat EU_NON_UK
    def test_prague_city(self):
        self.assertEqual(fad.classify_region("Prague, Czechia"), "PRAGUE")

    def test_praha_native_spelling(self):
        self.assertEqual(fad.classify_region("Praha 4"), "PRAGUE")

    def test_czech_republic_country_only(self):
        self.assertEqual(fad.classify_region("Remote — Czech Republic"), "PRAGUE")

    # UK_IE — must beat EU_NON_UK and NORTH_AMERICA
    def test_london(self):
        self.assertEqual(fad.classify_region("London, UK"), "UK_IE")

    def test_dublin_is_uk_ie_not_eu(self):
        """Critical: Dublin must NOT be EU_NON_UK or it gets the EU benefit."""
        self.assertEqual(fad.classify_region("Dublin, Ireland"), "UK_IE")

    def test_united_kingdom_full(self):
        self.assertEqual(fad.classify_region("Remote — United Kingdom"), "UK_IE")

    def test_uk_token(self):
        self.assertEqual(fad.classify_region("Manchester, UK"), "UK_IE")

    def test_uk_does_not_match_ukraine(self):
        """Word-boundary check: 'Ukraine' must not match the 'uk' token."""
        self.assertEqual(fad.classify_region("Kyiv, Ukraine"), "UNKNOWN")

    # APAC
    def test_singapore(self):
        self.assertEqual(fad.classify_region("Singapore"), "APAC")

    def test_tokyo(self):
        self.assertEqual(fad.classify_region("Tokyo, Japan"), "APAC")

    def test_australia(self):
        self.assertEqual(fad.classify_region("Sydney, Australia"), "APAC")
        self.assertEqual(fad.classify_region("Australia"), "APAC")

    def test_bengaluru(self):
        self.assertEqual(fad.classify_region("Bengaluru, India"), "APAC")

    # LATAM
    def test_brazil(self):
        self.assertEqual(fad.classify_region("São Paulo, Brazil"), "LATAM")

    def test_chile(self):
        self.assertEqual(fad.classify_region("Remote — Chile"), "LATAM")

    def test_mexico_city(self):
        self.assertEqual(fad.classify_region("Mexico City"), "LATAM")

    # MEA
    def test_tel_aviv(self):
        self.assertEqual(fad.classify_region("Tel Aviv, Israel"), "MEA")

    def test_dubai(self):
        self.assertEqual(fad.classify_region("Dubai, UAE"), "MEA")

    # NORTH_AMERICA
    def test_san_francisco(self):
        self.assertEqual(fad.classify_region("San Francisco, CA"), "NORTH_AMERICA")

    def test_new_york(self):
        self.assertEqual(fad.classify_region("New York, NY"), "NORTH_AMERICA")

    def test_canada(self):
        self.assertEqual(fad.classify_region("Toronto, Canada"), "NORTH_AMERICA")

    def test_us_token(self):
        self.assertEqual(fad.classify_region("Remote, US"), "NORTH_AMERICA")

    def test_united_states_full(self):
        self.assertEqual(fad.classify_region("United States"), "NORTH_AMERICA")

    # EU_NON_UK — broad, checked after UK_IE
    def test_berlin(self):
        self.assertEqual(fad.classify_region("Berlin, Germany"), "EU_NON_UK")

    def test_amsterdam(self):
        self.assertEqual(fad.classify_region("Amsterdam, Netherlands"), "EU_NON_UK")

    def test_eu_token(self):
        self.assertEqual(fad.classify_region("Remote — EU"), "EU_NON_UK")

    def test_emea_token(self):
        self.assertEqual(fad.classify_region("Remote — EMEA"), "EU_NON_UK")

    def test_europe_word(self):
        self.assertEqual(fad.classify_region("Europe"), "EU_NON_UK")

    # GLOBAL_REMOTE
    def test_global(self):
        self.assertEqual(fad.classify_region("Remote — Global"), "GLOBAL_REMOTE")

    def test_anywhere(self):
        self.assertEqual(fad.classify_region("Anywhere"), "GLOBAL_REMOTE")

    def test_worldwide(self):
        self.assertEqual(fad.classify_region("Worldwide"), "GLOBAL_REMOTE")

    def test_fully_remote(self):
        self.assertEqual(fad.classify_region("Fully remote"), "GLOBAL_REMOTE")

    def test_plain_remote_is_unknown(self):
        self.assertEqual(fad.classify_region("Remote"), "UNKNOWN")

    # Precedence checks — narrow wins.
    def test_prague_beats_eu(self):
        self.assertEqual(fad.classify_region("Prague, Europe"), "PRAGUE")

    def test_uk_beats_eu(self):
        self.assertEqual(fad.classify_region("London, Europe"), "UK_IE")

    # Negation-guard tests (v2.3 fix for Bug 1)
    # Without the guard, the regex `\beu\b` matches the EU in "non-EU" because
    # the hyphen is a word boundary. Wizard meta-phrases would then classify
    # as a real region and silently end up in EXCLUDED_REGIONS, suppressing
    # the candidate's own home region. These tests pin the fix.
    def test_negation_all_non_eu_returns_unknown(self):
        self.assertEqual(fad.classify_region("all non-EU"), "UNKNOWN")

    def test_negation_non_european_returns_unknown(self):
        self.assertEqual(fad.classify_region("Non-European countries"), "UNKNOWN")

    def test_negation_no_relocation_returns_unknown(self):
        self.assertEqual(fad.classify_region("no Germany — visa issues"), "UNKNOWN")

    def test_negation_not_open_returns_unknown(self):
        self.assertEqual(fad.classify_region("not open to NORTH_AMERICA"), "UNKNOWN")

    def test_negation_excluding_returns_unknown(self):
        self.assertEqual(fad.classify_region("excluding all of EMEA"), "UNKNOWN")

    def test_legitimate_eu_still_works(self):
        """Sanity: real EU strings still classify correctly post-fix."""
        self.assertEqual(fad.classify_region("Berlin, Germany"), "EU_NON_UK")
        self.assertEqual(fad.classify_region("Remote (EU)"), "EU_NON_UK")
        self.assertEqual(fad.classify_region("EMEA"), "EU_NON_UK")


class ScoreTablePragueTests(unittest.TestCase):
    """Score table for a Prague-based candidate (the original v2.2.1 fixture)."""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="PRAGUE",
            eligible_regions={"EU_NON_UK", "GLOBAL_REMOTE", "PRAGUE"},
            excluded_regions={"UK_IE"},
        )

    # Remote — score 3 in home / EU / global / unknown
    def test_remote_prague_full(self):
        self.assertEqual(self.table[("remote", "PRAGUE")], 3)

    def test_remote_eu_full(self):
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 3)

    def test_remote_global_full(self):
        self.assertEqual(self.table[("remote", "GLOBAL_REMOTE")], 3)

    def test_remote_unknown_full(self):
        self.assertEqual(self.table[("remote", "UNKNOWN")], 3)

    def test_remote_north_america_downgraded(self):
        """Not eligible, not excluded, not home — North America gets the
        time-zone downgrade rather than full filter-out."""
        self.assertEqual(self.table[("remote", "NORTH_AMERICA")], 2)

    def test_remote_uk_excluded(self):
        """User excluded UK — remote-UK should be 0, not 1."""
        self.assertEqual(self.table[("remote", "UK_IE")], 0)

    def test_remote_apac_one(self):
        self.assertEqual(self.table[("remote", "APAC")], 1)

    def test_remote_latam_one(self):
        self.assertEqual(self.table[("remote", "LATAM")], 1)

    def test_remote_mea_one(self):
        self.assertEqual(self.table[("remote", "MEA")], 1)

    # Hybrid — home full, eligible regions get relocation downgrade (v2.3+),
    # everything else filtered out
    def test_hybrid_prague_full(self):
        self.assertEqual(self.table[("hybrid", "PRAGUE")], 3)

    def test_hybrid_eu_relocation(self):
        """v2.3 fix: hybrid in an eligible region (EU is in this candidate's
        eligible_regions) gets the 1-point relocation downgrade — matching
        the existing onsite logic. Pre-v2.3 wrongly returned 0, suppressing
        all 'Hybrid Berlin / Paris' style listings for relocation-open
        candidates."""
        self.assertEqual(self.table[("hybrid", "EU_NON_UK")], 1)

    def test_hybrid_north_america_filter_out(self):
        """Not eligible — hybrid still 0."""
        self.assertEqual(self.table[("hybrid", "NORTH_AMERICA")], 0)

    def test_hybrid_apac_filter_out(self):
        self.assertEqual(self.table[("hybrid", "APAC")], 0)

    def test_hybrid_uk_filter_out(self):
        """UK is excluded — hybrid 0 even though it'd otherwise be EU territory."""
        self.assertEqual(self.table[("hybrid", "UK_IE")], 0)

    # Onsite — home full, eligible downgrade, rest filter out
    def test_onsite_prague(self):
        self.assertEqual(self.table[("onsite", "PRAGUE")], 3)

    def test_onsite_eu_relocation_downgrade(self):
        self.assertEqual(self.table[("onsite", "EU_NON_UK")], 1)

    def test_onsite_uk_excluded(self):
        self.assertEqual(self.table[("onsite", "UK_IE")], 0)

    def test_onsite_north_america_filter_out(self):
        self.assertEqual(self.table[("onsite", "NORTH_AMERICA")], 0)

    def test_empty_workplace_type_equals_onsite(self):
        self.assertEqual(self.table[("", "PRAGUE")], 3)
        self.assertEqual(self.table[("", "EU_NON_UK")], 1)
        self.assertEqual(self.table[("", "NORTH_AMERICA")], 0)


class ScoreTableBerlinTests(unittest.TestCase):
    """Score table for a Berlin-based candidate. Home region = EU_NON_UK,
    eligible adds Czechia and global remote, excludes UK."""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK", "GLOBAL_REMOTE", "PRAGUE"},
            excluded_regions={"UK_IE"},
        )

    def test_remote_berlin_home_full(self):
        """A Berlin-based candidate's home is EU_NON_UK — remote-EU is 3, not 2."""
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 3)

    def test_hybrid_berlin_full(self):
        """Hybrid in home region works — Berlin candidate to Berlin job."""
        self.assertEqual(self.table[("hybrid", "EU_NON_UK")], 3)

    def test_onsite_berlin_full(self):
        """Onsite in home region works — Berlin candidate to Berlin job, no relocation."""
        self.assertEqual(self.table[("onsite", "EU_NON_UK")], 3)

    def test_hybrid_prague_relocation(self):
        """v2.3: A Berlin candidate with PRAGUE in eligible_regions gets the
        1-point relocation downgrade for Hybrid Prague — they CAN take the
        job by relocating, even though commute isn't viable from Berlin.
        Pre-v2.3 was 0; that suppressed legitimate matches for any candidate
        open to relocating in their eligible region."""
        self.assertEqual(self.table[("hybrid", "PRAGUE")], 1)

    def test_onsite_prague_relocation(self):
        """Czechia is eligible, so onsite-Prague gets the 1-point relocation downgrade."""
        self.assertEqual(self.table[("onsite", "PRAGUE")], 1)

    def test_remote_north_america_downgraded(self):
        self.assertEqual(self.table[("remote", "NORTH_AMERICA")], 2)


class ScoreTableNYCTests(unittest.TestCase):
    """Score table for a NYC-based candidate. Home region = NORTH_AMERICA,
    no eligible non-NA regions, no exclusions."""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="NORTH_AMERICA",
            eligible_regions={"NORTH_AMERICA", "GLOBAL_REMOTE"},
            excluded_regions=set(),
        )

    def test_remote_north_america_home_full(self):
        """NYC candidate, remote-US listing — must be 3, not 2.
        v2.2.1 had this hardcoded as 2 (downgrade) which assumed Prague-base."""
        self.assertEqual(self.table[("remote", "NORTH_AMERICA")], 3)

    def test_hybrid_north_america_full(self):
        self.assertEqual(self.table[("hybrid", "NORTH_AMERICA")], 3)

    def test_onsite_north_america_full(self):
        self.assertEqual(self.table[("onsite", "NORTH_AMERICA")], 3)

    def test_remote_eu_one(self):
        """EU is not eligible for this candidate — remote-EU is 1 (low priority kept)."""
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 1)

    def test_hybrid_eu_filter_out(self):
        self.assertEqual(self.table[("hybrid", "EU_NON_UK")], 0)


class ScoreTableUnknownHomeTests(unittest.TestCase):
    """Profile.json missing or malformed → home_region defaults to UNKNOWN.
    Conservative behaviour: only global-remote and explicit-eligible roles surface."""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="UNKNOWN",
            eligible_regions=set(),
            excluded_regions=set(),
        )

    def test_remote_unknown_full(self):
        """Remote+UNKNOWN gets 3 because the rule treats UNKNOWN locations
        as 'we don't know enough to filter, but it's likely remote-anywhere'."""
        self.assertEqual(self.table[("remote", "UNKNOWN")], 3)

    def test_remote_global_full(self):
        self.assertEqual(self.table[("remote", "GLOBAL_REMOTE")], 3)

    def test_hybrid_anywhere_zero(self):
        """When home is unknown, hybrid never scores — we can't promise commutability."""
        for region in fad.ALL_REGIONS:
            self.assertEqual(self.table[("hybrid", region)], 0)

    def test_onsite_anywhere_zero(self):
        """When home is unknown, onsite never scores — relocation+commute both fail."""
        for region in fad.ALL_REGIONS:
            self.assertEqual(self.table[("onsite", region)], 0)


class ScoreRemoteAPITests(unittest.TestCase):
    """The convenience wrapper score_remote(). Tests the explicit-overrides path."""

    def test_score_remote_with_explicit_home(self):
        """Pass home_region explicitly — ignores any module-level profile."""
        self.assertEqual(
            fad.score_remote("Hybrid", "EU_NON_UK", home_region="EU_NON_UK", eligible_regions=set(), excluded_regions=set()),
            3,
        )

    def test_score_remote_lowercase_workplace_type(self):
        self.assertEqual(
            fad.score_remote("remote", "EU_NON_UK", home_region="EU_NON_UK", eligible_regions=set(), excluded_regions=set()),
            3,
        )

    def test_score_remote_uppercase_workplace_type(self):
        self.assertEqual(
            fad.score_remote("HYBRID", "PRAGUE", home_region="PRAGUE", eligible_regions=set(), excluded_regions=set()),
            3,
        )

    def test_score_remote_unmapped_workplace_type_zero(self):
        self.assertEqual(
            fad.score_remote("Asynchronous", "EU_NON_UK", home_region="PRAGUE", eligible_regions=set(), excluded_regions=set()),
            0,
        )


class IntegrationTests(unittest.TestCase):
    """End-to-end: take a real Ashby job dict, normalise it, classify the
    location, score it. These pin the full pipeline of normalise→classify→score
    so a regression anywhere in the chain trips a test."""

    def test_hybrid_nyc_ashby_job_for_prague_candidate_scores_zero(self):
        """v2.1.0 Bug B regression — Hybrid-NYC must score 0 for a Prague candidate."""
        company = {"name": "Acme", "source": "ai50"}
        ashby_job = {"id": "1", "title": "Eng", "workplaceType": "Hybrid",
                     "location": "New York", "isRemote": True}
        normalised = fad.normalise_ashby(ashby_job, company)
        region = fad.classify_region(normalised["location"])
        score = fad.score_remote(normalised["workplace_type"], region,
                                 home_region="PRAGUE",
                                 eligible_regions={"EU_NON_UK", "GLOBAL_REMOTE"},
                                 excluded_regions={"UK_IE"})
        self.assertFalse(normalised["is_remote"])
        self.assertEqual(region, "NORTH_AMERICA")
        self.assertEqual(score, 0)

    def test_remote_eu_ashby_job_for_prague_candidate_scores_three(self):
        company = {"name": "Acme", "source": "ai50"}
        ashby_job = {"id": "2", "title": "Eng", "workplaceType": "Remote",
                     "location": "Remote — Europe", "isRemote": True}
        normalised = fad.normalise_ashby(ashby_job, company)
        region = fad.classify_region(normalised["location"])
        score = fad.score_remote(normalised["workplace_type"], region,
                                 home_region="PRAGUE",
                                 eligible_regions={"EU_NON_UK", "GLOBAL_REMOTE"},
                                 excluded_regions={"UK_IE"})
        self.assertTrue(normalised["is_remote"])
        self.assertEqual(region, "EU_NON_UK")
        self.assertEqual(score, 3)

    def test_remote_us_ashby_job_for_prague_candidate_scores_two(self):
        company = {"name": "Acme", "source": "ai50"}
        ashby_job = {"id": "3", "title": "Eng", "workplaceType": "Remote",
                     "location": "Remote, US", "isRemote": True}
        normalised = fad.normalise_ashby(ashby_job, company)
        region = fad.classify_region(normalised["location"])
        score = fad.score_remote(normalised["workplace_type"], region,
                                 home_region="PRAGUE",
                                 eligible_regions={"EU_NON_UK", "GLOBAL_REMOTE"},
                                 excluded_regions={"UK_IE"})
        self.assertEqual(region, "NORTH_AMERICA")
        self.assertEqual(score, 2)

    def test_remote_us_ashby_job_for_nyc_candidate_scores_three(self):
        """v2.2.2 fix — a NYC candidate looking at a remote-US listing should
        score 3, not 2. v2.2.1's hardcoded PRAGUE bias gave 2 for everyone."""
        company = {"name": "Acme", "source": "ai50"}
        ashby_job = {"id": "4", "title": "Eng", "workplaceType": "Remote",
                     "location": "Remote, US", "isRemote": True}
        normalised = fad.normalise_ashby(ashby_job, company)
        region = fad.classify_region(normalised["location"])
        score = fad.score_remote(normalised["workplace_type"], region,
                                 home_region="NORTH_AMERICA",
                                 eligible_regions={"NORTH_AMERICA", "GLOBAL_REMOTE"},
                                 excluded_regions=set())
        self.assertEqual(score, 3)


if __name__ == "__main__":
    unittest.main()
