"""
Unit tests for diff_company — the function that decides which jobs are new,
which are removed, and which are unchanged across runs. Diff bugs are silent
killers: they cause phantom 'new' jobs (re-noise the user) or silent drops
(user never sees a real new opening).
"""

import unittest

from tests._helpers import load_fad

fad = load_fad()


def _job(jid, title="t", url="u", company="Acme"):
    return {"id": jid, "title": title, "url": url, "company": company}


class DiffCompanyTests(unittest.TestCase):
    KEY = "ashby:acme"

    def test_first_run_all_jobs_are_new(self):
        """Empty state + 3 current jobs = 3 new, 0 removed."""
        new, removed = fad.diff_company(
            self.KEY,
            [_job("a"), _job("b"), _job("c")],
            state={},
        )
        self.assertEqual({j["id"] for j in new}, {"a", "b", "c"})
        self.assertEqual(removed, [])

    def test_no_change_no_diff(self):
        """Same job IDs in state and current = nothing new, nothing removed."""
        state = {self.KEY: {"jobs": {
            "a": {"title": "t", "url": "u", "company": "Acme"},
            "b": {"title": "t", "url": "u", "company": "Acme"},
        }}}
        new, removed = fad.diff_company(self.KEY, [_job("a"), _job("b")], state)
        self.assertEqual(new, [])
        self.assertEqual(removed, [])

    def test_one_added_one_removed(self):
        """State had {a,b}; now we see {b,c}. → new=[c], removed=[a]."""
        state = {self.KEY: {"jobs": {
            "a": {"title": "Title A", "url": "url-a", "company": "Acme"},
            "b": {"title": "Title B", "url": "url-b", "company": "Acme"},
        }}}
        new, removed = fad.diff_company(self.KEY, [_job("b"), _job("c")], state)
        self.assertEqual([j["id"] for j in new], ["c"])
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["id"], "a")
        # Removed jobs must carry their stored title+url so compile-write
        # can mark a tracker row Closed without a re-fetch.
        self.assertEqual(removed[0]["title"], "Title A")
        self.assertEqual(removed[0]["url"], "url-a")

    def test_unrelated_company_state_ignored(self):
        """diff_company must scope to its own KEY; other companies' state
        in the same dict must not bleed in."""
        state = {
            self.KEY: {"jobs": {"a": {"title": "t", "url": "u", "company": "Acme"}}},
            "ashby:other": {"jobs": {"x": {"title": "t", "url": "u", "company": "Other"}}},
        }
        new, removed = fad.diff_company(self.KEY, [_job("a")], state)
        self.assertEqual(new, [])
        self.assertEqual(removed, [])

    def test_malformed_state_treated_as_empty(self):
        """If a company's state entry is missing or wrong type, treat as empty
        rather than crashing — earlier runs may have written garbage."""
        state = {self.KEY: "not-a-dict"}
        new, removed = fad.diff_company(self.KEY, [_job("a")], state)
        self.assertEqual({j["id"] for j in new}, {"a"})
        self.assertEqual(removed, [])

    def test_state_without_jobs_key(self):
        state = {self.KEY: {"last_checked": "2026-04-01"}}  # no 'jobs'
        new, removed = fad.diff_company(self.KEY, [_job("a"), _job("b")], state)
        self.assertEqual({j["id"] for j in new}, {"a", "b"})
        self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
