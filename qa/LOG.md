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

## Iteration 3: 2026-10-20 01:25: fix
Picked: BUG-004 (P1), the highest-severity open bug (CI pending, not red)
Did: Root cause: both CSV writers overwrote their file with the current run's tickers only (`financial_history.csv`
too). `to_summary()` emitted a section's keys only when that section was non-empty, so columns followed the run.
Tests first (`tests/test_csv_exporter.py`, 5 tests): all failed for the predicted reasons. Fix: `to_summary()`
always emits every key; `export()` and `export_financial_history()` merge with the existing file using the JSON merge
rule (the newest ticker / ticker-year wins, everything else kept), sorted; an unreadable old file falls back with a
warning.
Evidence: `qa/evidence/iter03-bug004-{red,green}.txt`. Live CLI: `stock-data KO` then `stock-data JPM` into one
scratch dir gives summary.csv = JPM + KO with all 7 calc_* columns, and financial_history.csv = both (39 rows).
Result: BUG-004 VERIFIED. MATCH-05 back to TODO (full JSON/SQLite/CSV value check still to do). New lead L5: web
UPDATE DATA jobs write SQLite only. NEEDS-USER: your real summary.csv still has 2 of 127 tickers; rebuilding it
writes real data.
Gates: ruff ✓ mypy ✓ pytest 859 passed (1 known warning) | CI: pending
Commit: (this commit) fix(exporters): CSV summaries keep every collected ticker
Next: no P0–P2 bugs open, so a discovery unit in the riskiest area: VAL-01..05 (build `qa/oracle/valuation_oracle.py`
and check the five models on the golden basket).

## Iteration 4: 2026-10-20 02:10: discovery
Picked: VAL-01..05 (riskiest area; no P0–P2 open). Prompt tuned first: §4.2 now ranks open P3 bugs *after*
discovery, so cosmetic fixes can't delay finding serious bugs. CI was green: `gh pr checks` "pending 0" just meant
a run in progress.
Did: Built `qa/oracle/valuation_oracle.py` from the spec (SQLite only, never imports `src`). Level 1 re-runs each
model's formula from its stored assumptions; Level 2 re-derives the inputs from raw tables (≤10-FY windows, median
3-yr FCF, CAPM discount, TTM dividends, reported diluted EPS on today's basis via declared splits, FY-end prices).
Three oracle bugs fixed along the way: EPS is now reported diluted EPS (handles preferred dividends); the share basis
is judged from implied shares when weighted shares are missing (GOOGL); rows with undefined spec inputs are listed,
not skipped.
Evidence: `qa/evidence/iter04-val-{golden,realcopy}.txt`
Result: Level 1: **0 of 579 applicable rows differ** (golden 60, real 519). Projection, fade, Gordon terminal value,
scenario spreads (not re-clamped, as the spec words it), Graham, Lynch and the multiples band all match. Level 2
(real data): 67 of 486 rows differ by more than 0.5%, and 33 more were valued despite undefined spec inputs. New
bugs: **BUG-005 (P0)** DDM CAGR window 22–63 yrs (40/106 rows, up to 24%); **BUG-008 (P1)** analyst-only fallback
(31 rows, quarterly growth up to 1,368%); **BUG-006 (P2)** split factor taken from share jumps; **BUG-007 (P2)**
Graham bear ignores loss years (needs a decision). Q2 (NEEDS-USER): "analyst growth" is a quarterly YoY figure.
Leads L6–L9. VAL-01..04 FAIL; VAL-05 Level 1 ✓, still TODO pending L6.
Gates: no `src` change; oracle passes ruff | CI: green on 451b529 and e51c4f8; 42f8a01 in progress
Commit: (this commit) chore(qa): valuation oracle and VAL-01..05 findings
Next: BUG-005 (P0), the highest-severity open bug.
