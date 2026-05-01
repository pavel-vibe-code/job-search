# Tests

Unit tests for `scripts/fetch-and-diff.py`. No external dependencies — uses only Python's stdlib `unittest` and `unittest.mock`. No `pip install` required.

## Run all tests

From the plugin root:

```bash
python3 -m unittest discover tests/ -v
```

Or use the convenience script:

```bash
./tests/run.sh
```

## What's covered

| File | Subject | Why it matters |
|---|---|---|
| `test_normalise.py` | `normalise_ashby` + `normalise_greenhouse` | **Bug A regression** — Hybrid roles must never be flagged `is_remote=True` regardless of upstream API quirks. Without this, EU-only profiles silently get Hybrid-NYC roles in the hot list. |
| `test_diff.py` | `diff_company` | First-run, no-change, add+remove, and malformed-state cases. Diff bugs cause phantom "new" jobs (re-noises the user) or silent drops (user misses a real opening). |
| `test_fetchers.py` | `fetch_html_static`, `fetch_comeet`, `fetch_static_roles` | HTML scraping is fragile. Pinning behavior to fixture HTML catches site redesigns the next time we run tests, not the next time we run a job search. |

## What's *not* covered (yet)

- **search-roles agent's region classifier (Bug B fix)** — currently lives in prose inside `agents/search-roles.md`, executed by an LLM. To unit-test, it needs to move into Python (e.g. a `classify_region(location)` helper in fetch-and-diff.py). Worth doing on the next refactor pass.
- **State persistence (Pass 4)** — the chunked Notion writes live in the orchestrator skill, also LLM-executed. Same constraint.
- **End-to-end pipeline run** — would require a Claude Code harness; manual smoke-test is fine for a personal plugin.

## Adding a test

1. Open or create `test_<area>.py` in `tests/`.
2. Subclass `unittest.TestCase`, write methods named `test_*`.
3. Use the existing helpers:
   - `from tests._helpers import load_fad, read_fixture` — `load_fad()` returns the imported `fetch-and-diff.py` module despite its dash-in-filename.
   - `read_fixture("name.html")` reads a file from `tests/fixtures/`.
4. Mock external I/O with `unittest.mock.patch.object(fad, "http_get", return_value=(b"...", None))`.
5. Run `python3 -m unittest tests.test_<area> -v` to iterate.

## Fixtures

`tests/fixtures/` holds canned HTML snippets and JSON responses used as test inputs. Real responses are large (Cyera HTML is 1.1 MB); we keep snippets small but representative — just enough to exercise the parsing code path.

To capture a fresh fixture from a live site:
```bash
curl -s -A "Mozilla/5.0" https://surgehq.ai/careers \
  | grep -E 'careers/[a-z]' | head -50 > tests/fixtures/surge-snippet.html
```
Trim aggressively. Tests should fail when the parser breaks — they should not depend on full-fidelity captures.
