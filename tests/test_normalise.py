"""
Unit tests for normalise_ashby and normalise_greenhouse.

Bug A regression suite: ensures Hybrid roles never get is_remote=True regardless
of what the upstream ATS field claims. This was the root cause of Hybrid-NYC,
Hybrid-Berlin etc. appearing in the v2.1.0 hot list for an EU-only profile.
"""

import unittest

from tests._helpers import load_fad

fad = load_fad()
COMPANY = {"name": "Acme", "source": "ai50"}


class NormaliseAshbyTests(unittest.TestCase):
    # Hybrid roles must never be flagged as remote.

    def test_hybrid_in_nyc_is_not_remote(self):
        """Bug A — Ashby's isRemote=true for a Hybrid-NYC role is misleading."""
        job = {"id": "1", "title": "Eng", "workplaceType": "Hybrid",
               "location": "New York", "isRemote": True}
        out = fad.normalise_ashby(job, COMPANY)
        self.assertFalse(out["is_remote"])
        self.assertEqual(out["workplace_type"], "Hybrid")

    def test_hybrid_in_berlin_is_not_remote(self):
        job = {"id": "2", "title": "Eng", "workplaceType": "Hybrid",
               "location": "Berlin, Germany", "isRemote": True}
        self.assertFalse(fad.normalise_ashby(job, COMPANY)["is_remote"])

    def test_hybrid_with_isremote_false_is_not_remote(self):
        job = {"id": "3", "title": "Eng", "workplaceType": "Hybrid",
               "location": "Amsterdam", "isRemote": False}
        self.assertFalse(fad.normalise_ashby(job, COMPANY)["is_remote"])

    # Genuine remote roles must still be marked is_remote=True.

    def test_remote_workplace_type_is_remote(self):
        job = {"id": "4", "title": "Eng", "workplaceType": "Remote",
               "location": "United States", "isRemote": True}
        self.assertTrue(fad.normalise_ashby(job, COMPANY)["is_remote"])

    def test_remote_in_location_string_is_remote(self):
        """If the location literally says 'remote', trust it even when
        workplaceType is empty (common for older Ashby boards)."""
        job = {"id": "5", "title": "Eng", "workplaceType": "",
               "location": "Remote — EU", "isRemote": False}
        self.assertTrue(fad.normalise_ashby(job, COMPANY)["is_remote"])

    def test_remote_global_is_remote(self):
        job = {"id": "6", "title": "Eng", "workplaceType": "Remote",
               "location": "Remote — Global", "isRemote": True}
        self.assertTrue(fad.normalise_ashby(job, COMPANY)["is_remote"])

    # Onsite / unspecified must be is_remote=False.

    def test_onsite_in_sf_is_not_remote(self):
        job = {"id": "7", "title": "Eng", "workplaceType": "Onsite",
               "location": "San Francisco, CA", "isRemote": False}
        self.assertFalse(fad.normalise_ashby(job, COMPANY)["is_remote"])

    def test_empty_workplace_and_location_is_not_remote(self):
        job = {"id": "8", "title": "Eng", "workplaceType": "",
               "location": "", "isRemote": False}
        self.assertFalse(fad.normalise_ashby(job, COMPANY)["is_remote"])

    # Field passthrough sanity.

    def test_required_fields_present(self):
        job = {"id": "9", "title": "Director CX", "jobUrl": "https://x/9",
               "workplaceType": "Remote", "location": "Remote — EU",
               "department": "CS", "publishedAt": "2026-01-15",
               "descriptionPlain": "lorem"}
        out = fad.normalise_ashby(job, COMPANY)
        self.assertEqual(out["id"], "9")
        self.assertEqual(out["company"], "Acme")
        self.assertEqual(out["title"], "Director CX")
        self.assertEqual(out["url"], "https://x/9")
        self.assertEqual(out["department"], "CS")
        self.assertEqual(out["ats"], "ashby")
        self.assertEqual(out["source"], "ai50")


class NormaliseGreenhouseTests(unittest.TestCase):
    def _job(self, location_name):
        return {"id": 100, "title": "Eng",
                "absolute_url": "https://gh/100",
                "location": {"name": location_name},
                "departments": [{"name": "Eng"}],
                "updated_at": "2026-04-01",
                "content": "desc"}

    def test_remote_in_location_is_remote(self):
        out = fad.normalise_greenhouse(self._job("Remote — Europe"), COMPANY)
        self.assertTrue(out["is_remote"])
        self.assertEqual(out["workplace_type"], "Remote")

    def test_hybrid_in_location_is_not_remote(self):
        """Greenhouse often uses 'Remote, US (Hybrid)' or similar — must NOT
        count as remote."""
        out = fad.normalise_greenhouse(self._job("Remote, NYC (Hybrid)"), COMPANY)
        self.assertFalse(out["is_remote"])
        self.assertEqual(out["workplace_type"], "Hybrid")

    def test_onsite_only_is_not_remote(self):
        out = fad.normalise_greenhouse(self._job("San Francisco, CA"), COMPANY)
        self.assertFalse(out["is_remote"])
        self.assertEqual(out["workplace_type"], "")

    def test_empty_location_is_not_remote(self):
        out = fad.normalise_greenhouse(self._job(""), COMPANY)
        self.assertFalse(out["is_remote"])

    def test_string_location_is_handled(self):
        """Some Greenhouse boards send `location` as a bare string."""
        job = {"id": 101, "title": "Eng", "absolute_url": "u",
               "location": "Remote — Worldwide",
               "departments": [], "updated_at": "", "content": ""}
        out = fad.normalise_greenhouse(job, COMPANY)
        self.assertTrue(out["is_remote"])


if __name__ == "__main__":
    unittest.main()
