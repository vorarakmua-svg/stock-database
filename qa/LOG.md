# QA Loop Log

One entry per iteration, newest last (template in `qa/LOOP_PROMPT.md` §8).

## Iteration 1: 2026-10-20 00:20: bootstrap
Picked: bootstrap, because the `qa/` state files were missing (§4.1 → §5)
Did: Branched `qa/full-loop` from `origin/main` (21f6159; PR #27 already merged). Created MATRIX (109 checks, 14
areas), BUGS (3 seeded bugs + Q1), GOLDEN and LOG; gitignored `qa/evidence/`. Built SCRATCH, including a
backup-API copy of the real DB. Ran the baseline gates under coverage. Started the golden-basket live collection
(4 batches, `--workers 2`) in the background.
Evidence: `qa/evidence/iter01-baseline-pytest.log`, `iter01-baseline-coverage.txt`, `iter01-golden-batch{1..4}.log`
Result:
- realcopy `integrity_check` ok (127 companies, 135 MB). The schema declares no foreign keys (0 of 19 tables), so
  `foreign_key_check` is vacuous and STORE-07 needs an orphan-row check.
- Baseline: 852 passed, 1 warning (BUG-002), 78 s under coverage. Total coverage 91%; valuation 92–98%. Weakest:
  csv_exporter 16%, fred_handler 22%, sec_handler 38%, yahoo_handler 48%, models/stock_data 62%,
  collection_api 64%, stock_data_fetcher 65%. No slow tests (max 0.55 s).
- Golden batch 1 (AAPL MSFT NVDA WMT JNJ) exit 0 in 128 s, 0 errors. Batch 2 mostly done; batches 3–4 still
  running at commit time. Lead: XOM's sources lack `calculated_metrics` (noted in GOLDEN.md).
- Versions: Python 3.13.2, fastapi 0.139.0, starlette 1.3.1, uvicorn 0.34.0, pandas 2.2.3, numpy 2.5.0,
  yfinance 1.0, requests 2.32.3, pydantic 2.10.4, jinja2 3.1.6, httpx 0.28.1, pytest 8.3.4, ruff 0.8.6,
  mypy 2.1.0, coverage 7.13.0, Node v24.12.0. pip-audit is not installed (SEC-07: scratch venv).
Gates: ruff ✓ mypy ✓ pytest 852 passed | CI: pending first push
Commit: (this commit) chore(qa): bootstrap QA loop state
Next: (1) record the remaining golden results (batches 2–4 logs) in GOLDEN.md and triage the per-ticker warnings
into leads; (2) fix BUG-001, the highest-severity open bug (P2, docs-only, doesn't need the golden data).

## Iteration 2: 2026-10-20 00:55: fix
Picked: BUG-001, the highest-severity open bug (P2), plus iteration 1's carry-over (record golden results)
Did: Recorded all 19 golden results (4 batches, exit 0) and four leads (L1–L4 in BUGS.md). Wrote
`tests/test_docs_usage_guide.py`: it runs USAGE_GUIDE's 28 code blocks notebook-style against a trimmed real export
(`tests/fixtures/usage_guide/AAPL.json`, 77 KB, key presence identical to the full export) and a `summary.csv`
written by the current CSVExporter. A tracking `json.load` records missing-key lookups, and blocks needing
streamlit/plotly/openpyxl are skipped when absent (openpyxl isn't in CI). Fixed 12 lookups and the stale section
list in USAGE_GUIDE, plus README's sample `years_of_dividends`.
Evidence: `qa/evidence/iter02-bug001-red.txt` (12 missing keys; KeyError in Dividend History and Graham Number),
`iter02-bug001-green.txt` (2 passed), `iter02-bug001-real-output.txt` (real AAPL: revenue $416.16B, NI $112.01B).
Mutation: planting `"Revenue"` in the fixture fails the guard; restored byte-identical.
Result: BUG-001 VERIFIED; DOCS-02 PASS. **New BUG-004 (P1):** summary.csv holds only the last run's tickers (your
real data: 2 of 127) and its columns vary by run, so MATCH-05 FAIL. Lead L4: `years_of_data` spans dividend gaps.
Prompt: §4.4 now says background subagents can't be awaited inside an iteration (this iteration worked inline).
Gates: ruff ✓ mypy ✓ pytest 854 passed (1 known warning) | CI: PR #28 bootstrap run pending at start
Commit: (this commit) docs: USAGE_GUIDE examples use the export's real field names
Next: BUG-004 (P1), the highest-severity open bug.
