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

## Iteration 5: 2026-10-20 02:45: fix
Picked: BUG-005 (P0), the only open P0 (CI: 42f8a01 ✓; 0ee32b9 running, QA docs only)
Did: Root cause in `value_ddm`: its CAGR history was every complete calendar year of dividends; only FY records
were truncated to 10 (`inputs.py`). Test first: `test_value_ddm_cagr_uses_only_the_last_10_years` (12%/yr until
2013, then 3%/yr) failed with `cagr_years` 31 ≠ 9. Fix: `DIVIDEND_CAGR_YEARS = 10`, a calendar window
`anchor.year - 10 <= year < anchor.year`. The oracle's DDM window was also wrong (last 10 *payment years* reached
back to TMUS's 2013 special distribution), so it now uses the spec's calendar window and span.
Evidence: `qa/evidence/iter05-bug005-{red,green,impact}.txt`, `iter05-oracle-{golden,real}.txt`. Backfilled
fresh copies with the fixed code: oracle DDM mismatches 41 → 0 (real), 7 → 0 (golden); Level 1 still 0/579.
Impact on the real data: 41/106 DDM values move (C +38%, JPM +24%, DHR −35%, WFC −29%); the 5-model median moves
for 10/127 companies; no verdict flips.
Result: BUG-005 VERIFIED. New **BUG-009 (P2)**: the DDM initiation year (and special payments) count as full years
(TMUS 137% CAGR, masked by analyst growth). VAL-02 → FAIL→BUG-009.
Gates: ruff ✓ mypy ✓ pytest 860 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): DDM dividend growth uses at most 10 years
Next: BUG-008 (P1), the highest-severity open bug.

## Iteration 6: 2026-10-20 03:20: fix
Picked: BUG-008 (P1), the highest-severity open bug (CI: 0ee32b9 ✓; 563cf05 running)
Did: Root cause: `derive_growth`'s `analyst_only` branch. A 10-FY window starting on a loss (DELL FY2017–2019 via
`FYRecord.eps()`'s net income ÷ shares fallback; negative early FCF) made the CAGR undefined, and yfinance's
one-quarter YoY figure (up to 1,368%) stood in for it, clamped to 15% or 0%. That branch was deliberate (an existing
test asserted it), so this reverses an earlier design choice: it contradicts the spec's min(hist, analyst) and the
module's "not overpaying" principle. Tests first: 3 new tests failed with 0.15 / applicable. Fix: undefined CAGR →
`growth_source: none` (flat 0% for DCF/DDM/owner earnings; Lynch N/A on its existing path). The old assertion was
replaced by a dedicated test. Oracle: an undefined CAGR now means flat 0% (Lynch N/A), mirroring the documented rule.
Evidence: `qa/evidence/iter06-bug008-{red,green,impact}.txt`, `iter06-oracle-{golden,real}.txt`. Backfilled copies:
no row uses analyst-only growth; all 16 DCF rows re-derive and match; Level 1 still 0 mismatches (503 real, 59 golden).
Impact: Lynch N/A for 16 companies; 11 DCF values −27% to −58% (CVX $293 → $124); 5 owner-earnings values −58%
(AVGO AMD HWM MU VRTX); the median moves for 19/127; no verdict flips.
Result: BUG-008 VERIFIED; VAL-01 PASS; VAL-04 → FAIL→BUG-006. Alternative noted in BUGS.md (CAGR from the first
profitable year) for the user.
Gates: ruff ✓ mypy ✓ pytest 863 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): analyst growth never stands in for a missing growth history
Next: no P0/P1 open → BUG-006 (P2, oldest open P2)

## Iteration 7: 2026-10-20 03:50: fix
Picked: BUG-006, the oldest open P2 (no P0/P1 open; CI: 563cf05 ✓; f7feea4 running)
Did: Root cause: `_normalize_splits` multiplied in the observed share-count seam ratio, which includes a year of
buybacks (AAPL 3.808 vs 4). It ignored `split_events` as "empty for most tickers", but 107/127 companies have rows,
and 17 of the 18 with a detected split have declared ratios. The seam isn't the split date (SEC restates ~3 years),
so detection stays; a declared ratio or run of ratios within ±25% of the jump now replaces it, else the jump is kept.
Tests first: the declared-ratio test failed with 3.8095; the fallback guard passed (today's behavior). A helper test
covers runs (NVDA 4×10), tolerance and the date filter; removing the tolerance fails it, restored byte-identical.
Evidence: `qa/evidence/iter07-bug006-{red,green,impact}.txt`, `iter07-oracle-{golden,real}.txt`
Result: BUG-006 VERIFIED. Golden Level 2: **0 mismatches** (was 2). Real: 26 → 18 (AAPL/NEE/WMT/RTX Lynch and
ANET/APH/AVGO/LRCX multiples resolved). Six values move 0.7–3.5% (AAPL Lynch 109.18 → 111.90); no applicability
changes. VAL-04/05 back to TODO pending L6 (GE, SCCO, SPGI, T, NOW, WDC). New lead L10: APP split detected with
none declared.
Gates: ruff ✓ mypy ✓ pytest 866 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): split adjustment uses declared split ratios
Next: BUG-007 (P2) needs a user decision on the bear case, so take BUG-009 (P2) next; BUG-007 is recorded as
NEEDS-USER.

## Iteration 8: 2026-10-20 04:25: fix
Picked: BUG-009 (P2). BUG-007 (P2) needs a user decision, so it's now NEEDS-USER instead of OPEN, so the
priority rule stops picking it (CI: f7feea4 ✓, 112aba5 ✓)
Did: Measured first: 8 DDM rows start their CAGR on a short year: initiations or restarts (TMUS GOOGL MU GEV DELL
HWM), a slipped ex-date (BMY 2016: 3 payments) and PGR's annual-only years. Rule: when the first window year has
fewer payments than the window's median count, growth starts at the next year. Rejected: comparing with the next
year. COST's 2017 special makes 5 payments, so that rule would drop COST's full 2016; a guard test covers it, and
the next-year mutant fails it. The first RED test (TMUS's 2 full years) was rewritten on a longer history so it
stays valid under BUG-010. RED (6 ≠ 5) on HEAD's models.py → GREEN; mutant red; restored byte-identical. The oracle
applies the rule independently (payment counts per year in SQL).
Evidence: `qa/evidence/iter08-bug009-{red,green,impact}.txt`, `iter08-oracle-{golden,real}.txt`,
`iter08-dividend-gaps.txt`
Result: BUG-009 VERIFIED for the partial first year. Oracle: golden L1/L2 0/59; real L1 0/503, L2 still 18 (no DDM
rows; the same Graham/Lynch/multiples set). Impact: MU growth 10% → 1.1% (DDM −29%), BMY 9.0% → 6.0% (−12%), GOOGL
and GEV → flat (−34%, −35%); the median moves only for MU; no verdict flips. Specials split out as Q3 (policy:
PGR's $13.60 variable dividend dominates its TTM). New: BUG-010 (P2, DDM CAGR from < 4 FYs, 5 rows), BUG-011 (P2,
DHR spin-off stored as a $24.56 dividend), BUG-012 (P1, CB missing two 2025 payments: TTM $1.99 vs ≈ $3.93).
Gates: ruff ✓ mypy ✓ pytest 868 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): DDM growth starts at the first full year of dividends
Next: BUG-012 (P1), the only open P1

## Iteration 9: 2026-10-20 05:10: fix
Picked: BUG-012 (P1), the only open P1 (CI: 112aba5 ✓, abed57a ✓). Review cadence: 6 fixes since the bootstrap
and no review yet, so an opus reviewer was dispatched in the background on e51c4f8..abed57a; its findings are
recorded when they arrive.
Did: Root cause: upstream. The collector stores Yahoo's full dividend series on every run (upsert), CB's 2026-08-04
JSON has the same gap, and a read-only live check today (2 requests) shows Yahoo still lacks the Sep and Dec 2025
payments in both the max and the 2-year series. Three sources confirm CB paid: SEC declared $3.82 for FY2025,
Yahoo's own cash-flow statement ($390M and $384M paid in Q3/Q4 2025) and its dividend rate ($4.08). The program's
defect: nothing compared the series with the SEC figure it already stores. Prototype over 300 FYs: amounts alone
can't separate CB (0.49) from PGR's December-declared, January-paid variable dividend (0.23–0.35), so the check needs
two signals: a gap in the payment schedule near the year, and payments < 80% of declared. Tests first: the wiring
test failed `[] == ['2025']`, and the unit tests failed at import. Real data then showed MU FY2021 (a first dividend
declared before the year ended and paid after it, following a gap since 1996): a test, RED, then the "gap begins
near the year" guard. Four mutants (shortfall alone, gap alone, no split skip, gap from any time) each fail their
guard test; restored byte-identical.
Evidence: `qa/evidence/iter09-bug012-{red,red-mu,green,mutants,realcopy,e2e}.txt`, `iter09-cb-yahoo-live.txt`,
`iter09-dividend-prototype.txt`
Result: BUG-012 VERIFIED for detection. On the real-data copy the check fires for CB FY2025 only (109 dividend
payers); re-assessing CB's real export offline: quality score 100 → 90 plus a collection warning. The README's
integrity table gains a fifth row. The DDM's handling is Q4 (NEEDS-USER); CB's DDM stays about half until then.
New: BUG-013 (P2, SEC annual dividends per share is one period's amount for V/CEG/BAC/DIS/PLD; VALID-05 FAIL).
Gates: ruff ✓ mypy ✓ pytest 874 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(validation): flag dividend payments missing from Yahoo against SEC filings
Next: record the reviewer's findings (if they arrived), then BUG-010 (P2, oldest open P2)
