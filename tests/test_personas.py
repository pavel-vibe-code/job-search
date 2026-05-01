"""Persona-scenario tests for the location/remote-mode filter.

Each class fixtures a realistic candidate persona (home region + eligibility +
exclusions) and pins how a representative job in each (workplace_type, region)
combination should score.

These tests are the regression net for the kinds of candidates the plugin
is intended to serve. They were added in v2.3 after the Maria-persona E2E run
showed that an EU-based Senior PM open to relocation was surfacing only 2 of
~40 expected candidates due to two filter bugs (negation false-positive in
classify_region; hybrid in eligible_regions scoring 0). Fixing the bugs without
adding scenario tests would have left the same shape of bug latent for the
next archetype that exercises a different code path.

Personas covered:
- HOME_CITY_NO_RELOCATION:  "I want to stay where I am; remote anywhere is OK."
- HOME_CITY_HYBRID_ONLY:    "Hybrid in my city only; no remote, no relocation."
- HOME_REGION_NO_RELOCATION: "Remote in my region OK; no on-site outside city, no
                              relocation."
- HOME_REGION_OPEN_RELOCATION: "Open to relocation within my region; hybrid
                                cities-other-than-mine are fine."
- MULTI_REGION_REMOTE:      "Remote-only across multiple regions (EU + NA)."
- GLOBAL_REMOTE_ANYWHERE:   "Anywhere remote, no commute. UNKNOWN home."
- ONSITE_SPECIFIC_CITY:     "On-site in one city only; nothing else."
- HOME_REGION_EXCLUDE_NEIGHBOUR: "EU-eligible BUT explicitly exclude UK and US."
"""

import unittest

from tests._helpers import load_fad

fad = load_fad()


class HomeCityNoRelocationTests(unittest.TestCase):
    """Persona: 'Lisbon-based; will not relocate; remote anywhere is fine.'
    eligible_regions: empty (nothing outside home except global-remote)
    excluded_regions: empty
    """

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions=set(),
            excluded_regions=set(),
        )

    def test_remote_home_full(self):
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 3)

    def test_remote_global_full(self):
        self.assertEqual(self.table[("remote", "GLOBAL_REMOTE")], 3)

    def test_remote_other_region_keeps_low_signal(self):
        """Not eligible, not excluded → low signal (1) — surfaces in tracker but
        not flagged hot. NORTH_AMERICA gets 2 because of the timezone-tier bias."""
        self.assertEqual(self.table[("remote", "APAC")], 1)
        self.assertEqual(self.table[("remote", "NORTH_AMERICA")], 2)

    def test_hybrid_home_full(self):
        self.assertEqual(self.table[("hybrid", "EU_NON_UK")], 3)

    def test_hybrid_other_region_zero(self):
        """No relocation → hybrid in another region = 0 (can't commute, won't move)."""
        self.assertEqual(self.table[("hybrid", "PRAGUE")], 0)
        self.assertEqual(self.table[("hybrid", "NORTH_AMERICA")], 0)

    def test_onsite_home_full(self):
        self.assertEqual(self.table[("onsite", "EU_NON_UK")], 3)

    def test_onsite_other_region_zero(self):
        self.assertEqual(self.table[("onsite", "APAC")], 0)


class HomeCityHybridOnlyTests(unittest.TestCase):
    """Persona: 'Hybrid in my city only; no remote, no relocation.'
    eligible_regions: empty
    Note: eligible_modes is enforced by the agent layer, not the score table.
    The score table here just shows the home region is the only viable score.
    """

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="PRAGUE",
            eligible_regions=set(),
            excluded_regions=set(),
        )

    def test_hybrid_home_full(self):
        self.assertEqual(self.table[("hybrid", "PRAGUE")], 3)

    def test_remote_home_full(self):
        """Even a 'hybrid only' candidate gets 3 for remote-home (it's a strict
        superset — being able to work remotely from Prague is fine for someone
        based in Prague)."""
        self.assertEqual(self.table[("remote", "PRAGUE")], 3)

    def test_remote_global_full(self):
        """Global-remote roles always score 3 — they accept everyone."""
        self.assertEqual(self.table[("remote", "GLOBAL_REMOTE")], 3)


class HomeRegionNoRelocationTests(unittest.TestCase):
    """Persona: 'Remote in my region OK; no on-site outside my city, no relocation.'

    The score table treats EU_NON_UK as the home region (full 3 across all
    workplace types). The 'no on-site outside my city' restriction is the
    agent layer's job — `location_rules.excluded_cities` filters specific cities
    AFTER region classification. The score table just answers 'how good is this
    region overall'.

    eligible_regions: {home_region} only — no relocation outside.
    """

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK"},
            excluded_regions=set(),
        )

    def test_remote_eu_full(self):
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 3)

    def test_hybrid_eu_full_home_match(self):
        """Hybrid in home region → 3. Per-city ('Hybrid Berlin' for a Lisbon
        candidate) is handled by the agent's excluded_cities filter, not here."""
        self.assertEqual(self.table[("hybrid", "EU_NON_UK")], 3)

    def test_onsite_eu_full_home_match(self):
        """Same: onsite in home region → 3. Per-city restriction is downstream."""
        self.assertEqual(self.table[("onsite", "EU_NON_UK")], 3)

    def test_remote_other_region_low(self):
        """Not eligible → low signal (NA=2 timezone bias, others=1)."""
        self.assertEqual(self.table[("remote", "APAC")], 1)
        self.assertEqual(self.table[("remote", "NORTH_AMERICA")], 2)

    def test_hybrid_other_region_zero(self):
        """No relocation → hybrid outside home region = 0."""
        self.assertEqual(self.table[("hybrid", "PRAGUE")], 0)
        self.assertEqual(self.table[("hybrid", "APAC")], 0)


class HomeRegionOpenRelocationTests(unittest.TestCase):
    """Persona: 'Lisbon now, open to EU relocation. Remote, hybrid, or on-site
    anywhere in EU all work.'
    """

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK", "PRAGUE"},
            excluded_regions={"UK_IE", "NORTH_AMERICA"},
        )

    def test_remote_eu_full(self):
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 3)

    def test_hybrid_eu_full_when_home(self):
        """Hybrid in EU_NON_UK (which is also home) → home wins, full 3."""
        self.assertEqual(self.table[("hybrid", "EU_NON_UK")], 3)

    def test_hybrid_prague_relocation(self):
        """Hybrid in PRAGUE (eligible non-home) → relocation downgrade 1."""
        self.assertEqual(self.table[("hybrid", "PRAGUE")], 1)

    def test_onsite_prague_relocation(self):
        """Same logic for onsite."""
        self.assertEqual(self.table[("onsite", "PRAGUE")], 1)

    def test_remote_uk_excluded(self):
        """Excluded UK → 0 across the board."""
        self.assertEqual(self.table[("remote", "UK_IE")], 0)
        self.assertEqual(self.table[("hybrid", "UK_IE")], 0)
        self.assertEqual(self.table[("onsite", "UK_IE")], 0)

    def test_remote_north_america_excluded(self):
        self.assertEqual(self.table[("remote", "NORTH_AMERICA")], 0)


class MultiRegionRemoteTests(unittest.TestCase):
    """Persona: 'Remote-only across EU + North America. No relocation, no on-site.'"""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK", "NORTH_AMERICA", "GLOBAL_REMOTE"},
            excluded_regions=set(),
        )

    def test_remote_eu_full(self):
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 3)

    def test_remote_north_america_full(self):
        """NA is in eligible_regions → 3 (overriding the default NA timezone
        downgrade of 2 that applies when NA is unknown territory)."""
        self.assertEqual(self.table[("remote", "NORTH_AMERICA")], 3)

    def test_remote_apac_low(self):
        """Not eligible → 1 (low signal)."""
        self.assertEqual(self.table[("remote", "APAC")], 1)

    def test_hybrid_north_america_relocation(self):
        """Hybrid in NA = 1 because it's in eligible_regions (relocation
        downgrade), even though candidate is in EU. The agent layer applies
        eligible_modes=['remote'] separately to drop hybrid altogether."""
        self.assertEqual(self.table[("hybrid", "NORTH_AMERICA")], 1)


class GlobalRemoteAnywhereTests(unittest.TestCase):
    """Persona: 'Anywhere remote, no commute, no relocation.' Home unknown
    (e.g. nomadic, profile location empty)."""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="UNKNOWN",
            eligible_regions=set(),
            excluded_regions=set(),
        )

    def test_remote_unknown_full(self):
        """UNKNOWN remote = 3 (treats unspecified location as remote-anywhere)."""
        self.assertEqual(self.table[("remote", "UNKNOWN")], 3)

    def test_remote_global_full(self):
        self.assertEqual(self.table[("remote", "GLOBAL_REMOTE")], 3)

    def test_hybrid_anywhere_zero(self):
        """No home → hybrid never works (can't promise commutability)."""
        for region in fad.ALL_REGIONS:
            self.assertEqual(self.table[("hybrid", region)], 0)

    def test_onsite_anywhere_zero(self):
        """No home → onsite never works."""
        for region in fad.ALL_REGIONS:
            self.assertEqual(self.table[("onsite", region)], 0)


class OnsiteSpecificCityTests(unittest.TestCase):
    """Persona: 'On-site in Berlin only. No remote, no relocation, nothing else.'
    Home region = EU_NON_UK; eligible empty (Berlin specifically lives in
    EU_NON_UK, but the score table is per-region not per-city; per-city
    matching is handled in the agent prompt via excluded_cities)."""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK"},
            excluded_regions=set(),
        )

    def test_onsite_eu_full(self):
        """A "Berlin onsite" job classifies to EU_NON_UK → home → 3.
        Per-city exclusion ('not Munich, not Frankfurt') is the agent's job."""
        self.assertEqual(self.table[("onsite", "EU_NON_UK")], 3)

    def test_remote_eu_full(self):
        """Even though candidate said onsite-only, score table doesn't enforce
        eligible_modes — that's the agent layer's job. Score table is
        the answer to 'how good is this region+mode for this candidate'."""
        self.assertEqual(self.table[("remote", "EU_NON_UK")], 3)

    def test_onsite_uk_zero(self):
        """UK is not eligible → 0."""
        self.assertEqual(self.table[("onsite", "UK_IE")], 0)


class HomeRegionExcludeNeighbourTests(unittest.TestCase):
    """Persona: 'EU-based, EU-eligible BUT explicitly exclude UK and US.'
    Tests the precedence: if a region is in BOTH eligible AND excluded, the
    excluded sets win across all workplace types."""

    @classmethod
    def setUpClass(cls):
        cls.table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK"},
            excluded_regions={"UK_IE", "NORTH_AMERICA"},
        )

    def test_uk_zero_across_all_modes(self):
        for wt in ("remote", "hybrid", "onsite"):
            self.assertEqual(self.table[(wt, "UK_IE")], 0,
                             f"UK should be excluded for workplace_type={wt}")

    def test_us_zero_across_all_modes(self):
        for wt in ("remote", "hybrid", "onsite"):
            self.assertEqual(self.table[(wt, "NORTH_AMERICA")], 0,
                             f"US should be excluded for workplace_type={wt}")

    def test_eu_remains_full(self):
        """Eligible-and-not-excluded continues to score 3 across the board
        when EU_NON_UK is the home region."""
        for wt in ("remote", "hybrid", "onsite"):
            self.assertEqual(self.table[(wt, "EU_NON_UK")], 3)


# Funnel-comparison tests for the bug fixes themselves — pin the change
# precisely so future regressions are caught.

class V2_3_FilterFixesTests(unittest.TestCase):
    """Pin the v2.3 bug fixes:
    - Bug 1: classify_region defensive guard against negation prefixes.
    - Bug 2: hybrid in eligible_regions (not just home_region) scores 1.

    These tests would have caught both bugs before they shipped if they had
    existed in v2.2.x."""

    def test_bug1_negation_does_not_match_eu(self):
        """The pre-fix regex `\\beu\\b` matched the EU in 'all non-EU' because
        the hyphen is a regex word boundary."""
        for variant in ("all non-EU", "non-EU countries", "non EU", "no EU jobs",
                        "not EU", "excluding EU"):
            self.assertEqual(
                fad.classify_region(variant), "UNKNOWN",
                f"Negation phrase {variant!r} must NOT classify as a real region — "
                f"otherwise it ends up wrongly excluding the candidate's home region."
            )

    def test_bug1_real_eu_strings_still_match(self):
        """Sanity check the negation guard didn't over-trigger."""
        for variant in ("EU", "Remote (EU)", "EMEA", "Berlin, Germany"):
            self.assertEqual(
                fad.classify_region(variant), "EU_NON_UK",
                f"Real EU string {variant!r} must still classify as EU_NON_UK."
            )

    def test_bug2_hybrid_in_eligible_region_scores_one(self):
        """Hybrid in eligible (non-home) region must be 1 (relocation), not 0.
        Pre-fix returned 0 because the hybrid branch lacked the eligible-region
        check that the onsite branch already had."""
        table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK", "PRAGUE"},
            excluded_regions=set(),
        )
        self.assertEqual(table[("hybrid", "PRAGUE")], 1,
                         "Hybrid in eligible region should be 1 (relocation downgrade) post-v2.3")

    def test_bug2_hybrid_outside_eligible_still_zero(self):
        """The fix mustn't over-correct — hybrid in non-eligible non-home
        regions stays 0."""
        table = fad.build_score_table(
            home_region="EU_NON_UK",
            eligible_regions={"EU_NON_UK"},
            excluded_regions=set(),
        )
        self.assertEqual(table[("hybrid", "APAC")], 0,
                         "Hybrid in non-eligible region must remain 0")
        self.assertEqual(table[("hybrid", "NORTH_AMERICA")], 0,
                         "Hybrid in non-eligible region must remain 0")


if __name__ == "__main__":
    unittest.main()
