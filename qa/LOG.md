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

## Iteration 10: 2026-10-20 05:45: fix
Picked: BUG-010 (P2), the oldest open P2 (no P0/P1 open; CI: 9c22ef8 running). Code review 1 was still running.
Did: The spec's shared assumptions say the historical CAGR "requires ≥ 4 FYs"; DCF and Lynch enforce it, the DDM
didn't. Test first, as a boundary pair: 3 full years must measure nothing (RED: 0.05 == None); 4 still measure
(passed before and after). Fix: with fewer than 4 full years, `derive_growth` gets no history; `_cagr_span` returns
None for an empty history (the mutant without that guard raises IndexError). Mutants with a minimum of 3 and of 5
each fail a boundary case; restored byte-identical. Following the review's advice, the oracle took the spec's rule
in its own commit first: run on iteration 8's pre-fix valuations, it flagged exactly BKNG, CRM, DELL and TMUS.
Evidence: `qa/evidence/iter10-bug010-{red,green,mutants,impact}.txt`, `iter10-oracle-before-fix.txt`,
`iter10-oracle-{golden,real}.txt`
Result: BUG-010 VERIFIED. Oracle after the fix: golden 0/59 at both levels; real L1 0/503, L2 the same 18 (no DDM
rows). Impact under today's flat fallback (BUG-016 may revert it): BKNG −34%, CRM −16%, DELL −34%, TMUS −21%; no
median or verdict changes.
Gates: ruff ✓ mypy ✓ pytest 876 passed (1 known warning) | CI: pending for this push
Commit: chore(qa): the oracle rule, then (this commit) fix(valuation): DDM growth needs 4 full years of dividends

## Iteration 10 (continued): code review 1 recorded
Review: an opus reviewer covered e51c4f8..abed57a (6 fixes since the bootstrap; it was due after the fifth). No
critical issues in the range; it called 112aba5's declared-ratio snapping and the CSV's stable columns sound. Each
finding was re-checked before recording: the KLAC/BKNG values on the real-data copy, the BOM data loss in scratch,
the plan's fallback and the oracle's history in git, and O's 13-payment year.
Recorded: BUG-014 (P1, splits after the latest FY never applied: KLAC Graham/Lynch ×10, BKNG Lynch ×25), BUG-015
(P1, the CSV merge drops every row after an Excel UTF-8 save), BUG-016 (P1, iteration 6 changed the plan's growth
fallback without the user's decision: restore it, decision Q5), BUG-017 (P2, O's 13-payment last year inflates DDM
growth), BUG-018 (P2, a failed re-collection blanks a CSV row), Q5, leads L12–L15, L10 extended. VAL-01's PASS is
withdrawn until BUG-016 is resolved. Loop prompt §7: oracle rule changes go in their own commit, before the fix.
Review 2 is due after 5 fixes past abed57a (2 so far: 9c22ef8 and BUG-010's).
Next: BUG-014 (P1, the largest wrong numbers on screen), then BUG-015, then BUG-016

## Iteration 11: 2026-10-20 02:30: fix
Picked: BUG-014 (P1), the oldest open P1 (CI: d162b1f running at the start).
Environment: around 02:00 every file under `%LOCALAPPDATA%\Temp\stockdb-qa` was deleted (golden DB, real-DB copy,
every iteration copy; the folders stayed). It wasn't this session: a copy read fine minutes earlier, and nothing in
between touched it; most likely Windows' scheduled maintenance. The real data and the repo are untouched. SCRATCH
moved to `%LOCALAPPDATA%\stockdb-qa` (loop prompt §3); the real-DB copy was rebuilt (integrity ok, 127 companies),
and the golden basket was re-collected live: 4 batches, all exit 0 (87, 79, 106 and 79 s), 19 companies, errors only for the invalid ZZZZ, and SPY's three Yahoo 404s again counted as 0 warnings (lead L2).
Did: Root cause: `_normalize_splits` stops at the latest FY's basis, and filings restate per-share figures only
for splits before they're issued (KLAC's FY2025 10-K, filed 2025-08-08, predates its 2026-06-12 10:1 split). Oracle
first (c5f6e72, per §7): today's basis is today's share count from `market_snapshots`, and only ratios of at least
3:2 (or 1:2) are share splits. On the unfixed code it flagged exactly KLAC Graham and Lynch and BKNG Lynch, and
seven L6 mismatches disappeared (they were the oracle's own spin-off handling). Tests first: the KLAC case failed
with [1.0, 1.0, 1.0]; two guards (a split before the filing; HON's 0.9535 spin-off factor) passed before and after.
Fix: `_apply_later_splits` applies declared share splits dated after the latest row's `filed_date` to every year.
Three mutants (period end instead of filing date, any ratio counts, only the latest year moves) each fail their
test; restored byte-identical.
Evidence: `qa/evidence/iter11-bug014-{red,green,mutants,impact}.txt`, `iter11-oracle-before-fix-real.txt`,
`iter11-oracle-real.txt`, `iter11-oracle-golden.txt`, `iter11-golden-batch{1..4}.log`
Result: BUG-014 VERIFIED. Real copy: Level 1 0/503; Level 2 11 left (BUG-007's 8 Graham bears, NOW multiples, SCCO
Lynch, XOM Graham). Golden: Level 1 and Level 2 0/59 after a backfill with the fix. Impact: KLAC Graham $154.83 → $15.48 and Lynch $446.44 → $44.64, BKNG
Lynch $2,483.55 → $99.34; medians KLAC $70.00 → $44.64, BKNG $216.36 → $147.64; no verdict changes.
Gates: ruff ✓ mypy ✓ pytest 879 passed (1 known warning) | CI: pending for this push
Commit: chore(qa) oracle basis (c5f6e72), then (this commit) fix(valuation): per-share history follows splits after
the latest filing
Next: BUG-015 (P1, the CSV merge can drop every row), then BUG-016

## Iteration 12: 2026-10-20 02:20: fix
Picked: BUG-015 (P1), the only open P1 (CI: 694f135 running at the start).
Did: Root cause per code review 1 and the iteration-10 repro: `_read_existing` read plain UTF-8 (an Excel BOM hid
the `ticker` column, so every row was dropped), an unreadable file was overwritten with this run's rows, and both
exports rewrote their file in place. Design: move an unreadable file aside instead of refusing, because a `None`
return trips lead L13's KeyError and skips the SQLite export. Tests first: 4 RED for the right reasons (BOM summary:
['CCC'] only; BOM history: BBB only; cp1252: no backup kept; a mid-write OSError: header-only file). Fix: `utf-8-sig`
reads; an unparsable file moved aside as `<name>.unreadable-<timestamp>.csv`; writes through a `.tmp` file and
`os.replace`, shared by both exports. Mutants (plain utf-8 read, no move-aside, write straight to the target, temp
file kept on failure) each fail their test; restored byte-identical. End to end through the fetcher's export path
on real golden StockData (offline): the UTF-8 and ANSI re-save cases behave as specified.
Tooling note: test code written through a Bash heredoc lost a level of backslash escapes (a cp1252 literal and two
BOM byte literals), so code with escapes now goes through script files.
Evidence: `qa/evidence/iter12-bug015-{red,green,mutants,e2e}.txt`
Result: BUG-015 VERIFIED. MATCH-05 → FAIL→BUG-018 (a failed re-collection still blanks a row).
Gates: ruff ✓ mypy ✓ pytest 883 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(exporters): the CSV merge never loses earlier rows
Next: BUG-016 (P1, restore the plan's growth fallback until the user decides Q5)

## Iteration 13: 2026-10-20 02:35: fix
Picked: BUG-016 (P1), the only open P1 (CI: 694f135 ✓; d946d18 running).
Did: Restored the plan's growth fallback that iteration 6 replaced without the user's decision. Oracle first
(630b52f): its `growth()` goes back to "not derivable" for an unmeasurable CAGR, which is BUG-016's recorded fix. A
first attempt instead taught the oracle the plan's analyst-only rule; the auto-mode permission classifier then
denied a second edit to the oracle's Lynch branch ("Modify Shared Resources"). Re-reading BUG-016, the
"not derivable" reading was the right call anyway (the spec is silent, so the oracle shouldn't encode either
rule), and only the `growth()` line was needed. Tests first: the plan's `analyst_only` assertion, restored, failed
('none' == 'analyst_only'); `derive_growth` restored byte-for-byte; the two flat-rule model tests removed (their
expectation is the unapproved rule).
Evidence: `qa/evidence/iter13-bug016-{red,impact}.txt`, `iter13-oracle-before-fix-real.txt`,
`iter13-oracle-{golden,real}.txt`
Result: BUG-016 VERIFIED; BUG-008 REVERTED (decision: Q5). Real copy: Level 1 0/519; Level 2 12 (the 11 known plus
DELL Lynch, lead L7); not derivable 40 (16 DCF, 7 DDM, 15 Lynch rows on the plan's rule, plus KKR). Golden: Level 1
and Level 2 0 mismatches; not derivable: TSLA DCF and Lynch, GOOGL DDM. Impact: iteration 6 undone (11 DCF values
up, e.g. CVX $124 → $293; 5 owner-earnings values +141%; Lynch applicable again for 16), and the 6 DDM rows that
BUG-009/BUG-010 left without a CAGR take the analyst figure capped at 10%; 19 medians move, no verdict changes.
VAL-01 → NEEDS-USER (Q5); VAL-04 → TODO.
Gates: ruff ✓ mypy ✓ pytest 880 passed (1 known warning; 3 flat-rule tests removed) | CI: pending for this push
Commit: chore(qa) oracle reading (630b52f), then (this commit) fix(valuation): restore the plan's growth fallback
pending a decision
Next: code review 2 is due (5 fixes past abed57a), dispatched in the background; then BUG-011 (P2, oldest open P2)

## Iteration 14: 2026-10-20 02:55: fix
Picked: BUG-011 (P2), the oldest open P2 (CI: e8c3aa1 running). Code review 2 was still running.
Did: Root cause: upstream; Yahoo's dividend series itself has DHR's `2016-07-05 24.56`, beside the same day's 1.319
split-history factor. A scan of both copies found 22 dividends dated on a split-history day: spin-off values many
times the nearby payments (DHR ×228, ETN ×24, ABT ×12) and ordinary dividends beside stock dividends (SCCO, SPG,
HON, TSM), so a date-only rule would have dropped SCCO's current dividends. Oracle first (bffce5f): it drops a
payment over 5× the median within 400 days on a day with a factor between 2:3 and 3:2, and now builds the TTM and
yearly totals in Python; against the unfixed code its only new finding was DHR's DDM. Tests first: the DHR case
failed (24.56 still among the dividends); guards for SCCO's dividend beside a stock dividend and for a COST-style
special passed before and after. Fix: `_drop_spin_off_dividends` in `load_inputs`. Mutants (large payments dropped
on any day; any payment dropped on a factor day) each fail a guard; restored byte-identical.
Evidence: `qa/evidence/iter14-bug011-{red,green,mutants,impact}.txt`, `iter14-oracle-before-fix-{golden,real}.txt`,
`iter14-oracle-{golden,real}.txt`
Result: BUG-011 VERIFIED for the valuation. Real copy: Level 1 0/519, Level 2 the known 12; golden 0/57. Impact:
DHR DDM $22.66 → $34.86 (growth 0% → 10%, the cap); nothing else changes. New: BUG-019 (P3, the web dividend panel
still shows the $24.56).
Gates: ruff ✓ mypy ✓ pytest 883 passed (1 known warning) | CI: pending for this push
Commit: chore(qa) oracle rule (bffce5f), then (this commit) fix(valuation): a spin-off recorded as a dividend stays
out of the DDM
Next: record code review 2's findings when it reports; then BUG-013 (P2, the oldest open P2)

## Iteration 15: 2026-10-20 03:15: fix
Picked: BUG-013 (P2), the oldest open P2 (CI: eb63f81 running). Code review 2 was still running.
Did: Root cause from cached companyfacts (golden cache for PLD; the user's `data/cache`, read-only, for V, CEG,
BAC and DIS). Two causes. Parser: `_is_full_year` passes point-in-time facts (meant for the balance sheet) and was
applied to every field, so BAC's per-declaration facts (last: 0.24) outranked its full-year 0.92 under the
fallback tag, and DIS's semiannual payments stood in for the year. Filers: V and CEG tag each quarter's rate with
a full-year context; PLD files a stray 0.01 "declared". Tests first: two annual-path tests failed (0.24 == 0.92; a
0.5 where there should be nothing); a vintage test, written after the fix to the second path, failed when its
guard was removed. Fix: `_matches_kind` (a duration field needs a fact with a start) in `_resolve_canonical` and
`extract_annual_vintages`. Impact measured by parsing all 129 cached filings before and after.
Evidence: `qa/evidence/iter15-bug013-{red,green,mutants,impact}.txt`
Result: BUG-013 VERIFIED for the parser defect: 60 values change, all corrections or honest gaps (15 dividend
years corrected, e.g. BAC FY2023 0.24 → 0.92, JNJ FY2020 1.01 → 3.98; 9 now empty; GS FY2008's 13 fields no
longer show the one-month transition loss, so net income is $2.322B; 21 point-in-time zero impairments empty).
New: BUG-020 (P3, the filers' own tagging). Stored data changes on the next collection; no valuation reads these
fields.
Gates: ruff ✓ mypy ✓ pytest 886 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): a duration field never takes a point-in-time fact
Next: record code review 2's findings when it reports; then BUG-017 (P2) or BUG-018 (P2), oldest first: BUG-017

## Iteration 16: 2026-10-20 03:35: fix
Picked: BUG-017 (P2), the oldest open P2 (CI: 0940188 running). Code review 2 was still running.
Did: Measured both endings on the real-data copy: only O (a 13-payment 2025) and PGR (three annual-only years at the
start) change. Oracle first (3dd48f1): every short leading year skipped, and a last year with extra payments counted
at the usual count; against the unfixed code it flagged exactly O's DDM. Tests first: the restart case failed (7
years, not 6) and the O case failed (3.92%, not 3%). Fix in `value_ddm`: a `while` over short leading years, and a
last year above the usual count scaled to it. A guard written after the fix (a short last year stays as it is) and
three mutants (one leading year only, no scaling, short years scaled too) each fail their test; restored
byte-identical.
Evidence: `qa/evidence/iter16-bug017-{red,green,mutants,impact}.txt`, `iter16-oracle-before-fix-{golden,real}.txt`,
`iter16-oracle-{golden,real}.txt`
Result: BUG-017 VERIFIED. Real copy: Level 1 0/519, Level 2 the known 12; golden 0/57. Impact: O DDM $66.19 → $63.57
(growth 4.58% → 3.66%); PGR's measured CAGR 27.98% → 9.68% with its growth still the analyst's 5%; no median moves.
VAL-02 → NEEDS-USER (only Q3, Q4 and Q5 remain).
Gates: ruff ✓ mypy ✓ pytest 889 passed (1 known warning) | CI: pending for this push
Commit: chore(qa) oracle rule (3dd48f1), then (this commit) fix(valuation): DDM growth compares full years at both
ends
Next: record code review 2's findings when it reports; then BUG-018 (P2, the last open P2)

## Iteration 17: 2026-10-20 04:00: fix
Picked: BUG-018 (P2), the last open P2 (CI: 83495c2 running). Code review 2 reported mid-iteration (recorded below).
Did: Reproduced on all three stores rather than the CSV alone: a good AAPL, then a failed re-collection, (a)
everything failed, (b) Yahoo failed but SEC worked. Every store lost data: the JSON its company info and market data,
and in (a) all 20 annual years (its merge starts from the new run); the CSV row was blanked; SQLite's `companies` row
got NULL name, CIK, sector and industry. The ledger's premise (the JSON merge keeps data) was wrong, so BUG-018 was
widened and upgraded to P1. Tests first, one per store, each RED for the right reason ('' == 'AAA Inc.'; None ==
'Test Co.'; (None, None, None)). Fix: data a run didn't get keeps its earlier value in each store (CSV empty fields,
JSON empty sections plus the quality report of kept financials, SQLite `companies` via COALESCE); the run's own
diagnostics stay the new run's. Four mutants each fail their test; restored byte-identical.
Evidence: `qa/evidence/iter17-bug018-{repro,red,green,mutants,e2e}.txt` (e2e notes a key error in the repro
script's JSON line, re-checked directly)
Result: BUG-018 VERIFIED; both failure shapes now keep everything in all three stores. MATCH-05 → TODO (its value
comparison is still untested). No valuation changes.
Gates: ruff ✓ mypy ✓ pytest 892 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(exporters): a failed re-collection never erases stored data

## Iteration 17 (continued): code review 2 recorded
Review: an opus reviewer covered abed57a..e8c3aa1 (5 fixes). No critical issues; it confirmed the oracle-first
ordering, the exact BUG-016 restoration, and the `filed_date` rule (36 of 36 historical share splits replay
correctly). Each finding was re-checked before recording (`qa/evidence/iter17-review2-checks.txt`,
`iter17-l13-check.txt`): the CSV no-`ticker` repro (both variants), CRM's DDM, the GOOGL/DELL/HON share counts, the
ABT/DELL/RTX ratios, `_assess`'s catch-all, and lead L13, reproduced as a crash.
Recorded: BUG-026 (P0, a locked summary.csv crashes the export before SQLite: L13 confirmed), BUG-021 (P1, the CSV
merge drops every row from a file without a `ticker` column), BUG-022 (P1, DCF/owner earnings use a single-class
share count for GOOGL and DELL, halved for HON: values about 2× high), BUG-023 (P2, spin-off/merger factors ≥ 3:2
pass as splits), BUG-024, BUG-025, BUG-027, BUG-028 (P3). BUG-010's impact note and Q5's context updated for the
restored fallback (CRM DDM +28%, not −16%). L13 promoted; L14 extended. VAL-01 → FAIL→BUG-022, GATHER-06 →
FAIL→BUG-026, STORE-06 → FAIL→BUG-021. Not acted on: the stale oracle Lynch comment (an edit to it was denied by
the permission classifier in iteration 13).
Review 3 is due after 5 fixes past e8c3aa1 (4 so far: eb63f81, 0940188, 83495c2 and BUG-018's).
Next: BUG-026 (P0)

## Iteration 18: 2026-10-20 04:20: fix
Picked: BUG-026 (P0), the only open P0 (CI: 83495c2 ✓; 0850065 running).
Did: Root cause (confirmed in iteration 17): `StockDataFetcher.export` set `results["csv"]` only when the summary
export succeeded, then appended the history file's path to it. Test first: with the summary export returning None,
`fetcher.export(..., formats=["csv", "sqlite"])` raised KeyError: 'csv'. Fix: `results.setdefault("csv", [])`. The
mutant (the old append) raises again; restored byte-identical. Real path: with `os.replace` refused for summary.csv
(as Windows does while Excel has it open), the export logs the CSV error and returns json, financial_history.csv and
sqlite, with the database written.
Evidence: `qa/evidence/iter18-bug026-{red,green,mutants,e2e}.txt`
Result: BUG-026 VERIFIED. GATHER-06 → TODO (the rest of its failure modes are untested).
Gates: ruff ✓ mypy ✓ pytest 893 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(fetchers): a failed summary CSV no longer stops the other exports
Review cadence: 5 fixes past e8c3aa1 (eb63f81, 0940188, 83495c2, 2aba934 and this one), so code review 3 is
dispatched in the background on e8c3aa1..HEAD.
Next: BUG-021 (P1, the oldest open P1), then BUG-022
