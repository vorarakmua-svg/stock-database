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

## Iteration 19: 2026-10-20 04:35: fix
Picked: BUG-021 (P1), the oldest open P1 (CI: 0850065 ✓; 8dc494b running). Code review 3 was still running.
Did: Root cause (code review 2, reproduced): `_read_existing` parsed a `;`-separated Excel save, or a header renamed
`Ticker`, without error, but no row had a `ticker` key, so every row was filtered out and the file rewritten. Test
first, parametrized over both variants: no kept copy existed (RED). Fix: a file whose header lacks `ticker` takes
the move-aside path, checked after the file is closed (Windows can't rename an open file). The mutant without the
check fails both variants; restored byte-identical. Review 2's repro now keeps the earlier file aside in both cases,
with a warning that quotes the header.
Evidence: `qa/evidence/iter19-bug021-{red,green,mutants,e2e}.txt`
Result: BUG-021 VERIFIED. STORE-06 → FAIL→BUG-027 (P3, same-second kept copies).
Gates: ruff ✓ mypy ✓ pytest 895 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(exporters): a CSV without a ticker column is kept aside, not dropped
Next: record code review 3 when it reports; then BUG-022 (P1, single-class share counts in the DCF)

## Iteration 20: 2026-10-20 05:00: fix
Picked: BUG-022 (P1), the only open P1 (CI: 2d92381 running). Code review 3 was still running.
Did: Measured first, over 124 companies: snapshot / latest-FY-diluted ratios put DELL, GOOGL and HON at ≈ 0.48 and
everyone else at 0.857 or above; market cap ÷ price exceeds the snapshot count for multi-class companies and
partnership units too (META 15%, APP 10%, V 10%, PLTR 4%). Oracle first (a285446): total shares are market cap ÷
price when more than 2% above the snapshot, or the latest FY's diluted count on today's basis when still under
3/4 of it; against the unfixed code it flagged the DCF of APP, GOOGL, HON, META, MRVL and V and nothing else.
Tests first, four shapes (GOOGL, HON, and AAPL/COF guards): RED. Fix: `_total_shares` in `load_inputs` plus a
`shares_source` input that DCF and owner earnings record. Mutants (no market-cap check; no FY check; FY count
whenever larger) each fail; restored byte-identical.
Evidence: `qa/evidence/iter20-bug022-{red,green,mutants,impact}.txt`, `iter20-oracle-before-fix-{golden,real}.txt`,
`iter20-oracle-{golden,real}.txt`
Result: BUG-022 VERIFIED. Real copy: Level 1 0/519, Level 2 the known 12; golden 0/57. Impact: DCF GOOGL $277.59 →
$133.18, DELL $316.92 → $159.43, HON $182.45 → $89.93, META −13%, APP −9%, V −9%, PLTR −4%, MRVL −2%; owner earnings
for DELL, HON, META and V likewise; 6 medians move; no verdict changes. VAL-01 → NEEDS-USER (only Q5 left).
Gates: ruff ✓ mypy ✓ pytest 899 passed (1 known warning) | CI: pending for this push
Commit: chore(qa) oracle share count (a285446), then (this commit) fix(valuation): per-share values count every share
class
Next: record code review 3 when it reports; then BUG-023 (P2, the only open P2)

## Iteration 21: 2026-10-20 05:20: fix
Picked: BUG-023 (P2), the only open P2 (CI: 2d92381 ✓; c6678d0 running). Code review 3 was still running.
Did: The risk is latent (no spin-off or merger factor sits after a latest filing today), so the test is synthetic:
an ABT-like 2.084 after the filing, with today's share count unchanged, was applied as a split (RED); a KLAC-like
10:1 confirmed by 1.31B shares today is the guard. Fix: `_apply_later_splits` requires today's share count (the
larger of Yahoo's count and market cap ÷ price) within DECLARED_SPLIT_TOLERANCE of the filed count × the ratio, and
applies as before when no count exists. The snapshot query moved ahead of it in `load_inputs`. Mutants (no
confirmation; a check that never accepts) each fail one test; restored byte-identical. The oracle needed no change:
its basis already follows today's share count.
Evidence: `qa/evidence/iter21-bug023-{red,green,mutants,impact}.txt`, `iter21-oracle-{golden,real}.txt`
Result: BUG-023 VERIFIED for the post-filing rule: zero valuation changes on the real-data copy, BKNG/KLAC/CRWD's
splits still applied; the oracle is unchanged (golden 0, real the known 12). No P0–P2 bug is open now.
Gates: ruff ✓ mypy ✓ pytest 901 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): a post-filing split must show in the share count
Next: record code review 3 when it reports; then discovery (§4.2.3), riskiest area first: VAL

## Iteration 22: 2026-10-20 05:35: discovery
Picked: VAL-07, VAL-08, VAL-10, VAL-11 (no P0–P2 bug open; the riskiest area first; CI: c6678d0 ✓, 4998ec8
running). Code review 3 was still running.
Did: One scan over fresh backfilled copies (real and golden) using the code's own definitions and the app's APIs
in-process (`create_app(db_path=copy)`): VAL-11 backfilled twice and through the post-collection path; VAL-08
checked ordering, finiteness, reasons, sector rules and JSON on every row; VAL-07 recomputed every summary and
compared the screener with the valuation API ticker by ticker; VAL-10 checked rate units, the CAPM clamp, the ERP,
rf provenance and fallback flags. Owner earnings, which the oracle's Level 1 skips, was recomputed from its stored
assumptions with the oracle's own projection.
Evidence: `qa/evidence/iter22-discovery-val.txt`
Result: VAL-07, VAL-08, VAL-10, VAL-11 PASS; no new bug. VAL-06 and VAL-12 got partial evidence and stay TODO.
Gates: ruff ✓ mypy ✓ pytest 901 passed (no code change) | CI: pending for this push
Commit: (this commit) chore(qa): discovery VAL-07, VAL-08, VAL-10, VAL-11 pass
Next: record code review 3 when it reports; then discovery VAL-06, VAL-09, VAL-12, VAL-13

## Iteration 23: 2026-10-20 05:55: code review 3 recorded
Started a discovery unit (VAL-06, VAL-09, VAL-12), but code review 3 reported with open P1/P2 findings, which come
first (§4.2), so this iteration records it; the discovery unit waits.
Review: an opus reviewer covered e8c3aa1..8dc494b (5 fixes). No critical issues; it reproduced every recorded
impact (DDM, parser, spin-off rule, BUG-026) and confirmed the oracle-first order. I re-ran its three key repros on
the current code (`qa/evidence/iter23-review3-checks.txt`): all three hold.
Recorded: BUG-018 reopened (P1): a Yahoo failure with a rate still fetched writes a price-less latest snapshot
(AAPL DCF $127.45 → $140.06), and the CSV's per-field rule keeps stale values after successful runs (P/E 25 beside
a loss). BUG-017 reopened (P2): a payment-frequency increase reads as extra payments (true 5% → −5% to −10%, growth
0%; no current row). New: BUG-029 (P3, values a re-parse empties stay in SQLite); BUG-019, L14 extended; L16 (the
JSON's in-place write). VAL-02, MATCH-05, GATHER-06, STORE-06 → FAIL. My iteration-17 claim that BUG-018 was fixed
in every store was wrong; its repro lacked the rate a real failed run still fetches.
Review 4 is due after 5 fixes past 8dc494b (3 so far: 2d92381, c6678d0, 4998ec8).
Next: BUG-018 (P1)

## Iteration 24: 2026-10-20 06:15: fix
Picked: BUG-018 (P1, reopened by code review 3) (CI: ce126a8 ✓; e945cee running).
Did: Two parts, tested separately. Snapshot: `SQLiteStore.export` wrote a snapshot whenever any snapshot column had
a value, and the risk-free rate (fetched even when Yahoo fails) counted, so a rate-only row became the latest.
Now it needs a Yahoo market column (`_MARKET_COLUMNS`). CSV: the per-field keep rule kept stale values after
successful runs. Now a column keeps its earlier value only when its whole section came back empty
(`SUMMARY_SECTIONS`, mirroring the JSON), identifiers are kept when missing, and run columns are always new. Tests
first: a rate-only failed run left a price-less latest snapshot; a successful run kept P/E 25 and yield 2.1%
beside a loss; the section map didn't exist (all RED). Four mutants (any column makes a snapshot; keep every
section; keep none; drop identifier keeping) each fail a test; restored byte-identical. Review 3's two repros now
behave.
Evidence: `qa/evidence/iter24-bug018-{red,green,mutants,e2e}.txt`
Result: BUG-018 VERIFIED (reopened parts). New: BUG-030 (P3, the JSON's price-history snapshot label after a
failed run, moved out of BUG-018). MATCH-05 and GATHER-06 → TODO; STORE-06 → FAIL→BUG-027 (P3).
Gates: ruff ✓ mypy ✓ pytest 904 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(exporters): a failed fetch keeps the latest snapshot, and the CSV keeps only sections
it didn't get
Next: BUG-017 (P2, reopened: payment-frequency increases)

## Iteration 25: 2026-10-20 03:44: fix
Picked: BUG-017 (P2, reopened by code review 3), the only open P2 (CI: 23fb062 ✓).
Did: Measured candidate rules on every payer first (`iter25-rule-design{,-2}.txt`): judging each end of the window
by the cadence of the 3 years next to it keeps O's 13-payment 2025 scaled and restores PGR's and TSM's annual years;
a size-only test misread HWM's first year, so a lower-frequency year is one whose year before had as many payments.
Oracle first (e21a0b0): on a golden copy whose KO pays monthly from 2021, XOM has a 2025 special, MSFT a slipped
2016 payment and AAPL annual 2016-18 payments, it flagged exactly those four DDM rows against the unfixed code (the
old oracle passed them). Tests: 8 RED against the unfixed code (six cadences, a slipped first-year payment, a
last-year special). Fix in `value_ddm`; seven mutants each fail a test; restored byte-identical.
Evidence: `qa/evidence/iter25-bug017-{red,green,mutants,impact}.txt`, `iter25-cadence-db.txt`,
`iter25-oracle-old-rule-cadence.txt`, `iter25-oracle-before-fix-{cadence,golden,real}.txt`,
`iter25-oracle-{cadence,golden,real}.txt`
Result: BUG-017 VERIFIED. Oracle after the fix: demo and golden Level 1 0/60, Level 2 0/57; real Level 1 0/519,
Level 2 the known 12 (identical list). Impact: no value changes on real or golden data; PGR's measured CAGR 9.68%
(6y) → 20.90% (9y), TSM's 12.84% (5y) → 14.41% (9y), growth unchanged. Demo: KO $32.19 → $38.87, AAPL $18.76 →
$20.30 (= the untouched copy), MSFT and XOM likewise. VAL-02 → NEEDS-USER (Q3, Q4, Q5). Note: the oracle commit's
first gate run hid pytest's summary (`-q` twice); re-run on that tree: 904 passed, exit 0.
Gates: ruff ✓ mypy ✓ pytest 912 passed (1 known warning) | CI: pending for this push
Commit: chore(qa) oracle cadence rule (e21a0b0), then (this commit) fix(valuation): DDM growth survives a change in
how often a company pays
Review 4 is due (5 fixes past 8dc494b: 2d92381, c6678d0, 4998ec8, 23fb062 and this commit): dispatched to opus.
Next: record code review 4 when it reports; meanwhile discovery VAL-06, VAL-09, VAL-12, VAL-13

## Iteration 26: 2026-10-20 04:03: discovery
Picked: VAL-03, VAL-04, VAL-05, VAL-06, because no P0–P2 bug is open and VAL comes first: the oracle's unexplained
Level-2 rows (leads L6, L7, L9) and the untested parts of owner earnings (CI: 61f8a56 ✓). Code review 4 is running.
Did: Hand-checked each disagreement from the raw rows. NOW's multiples is a code bug (BUG-031): its declared 5:1 split
matches the FY2022→FY2023 seam, but the EPS-direction check vetoes it (a $723M tax benefit lifted 2023 EPS past the
split ratio); a scan of every company finds no other case. SCCO's Lynch was the oracle's own positional window (10
values back to FY2010, exponent 9 for 15 years): oracle fixed (e4b6a1f). XOM's Graham, DELL's Lynch and KKR: the
oracle demanded a diluted share count these filers don't report; with the period-end count (0f47b5f) XOM and KKR
agree and DELL's Lynch rests on Q5. L7's EPS gaps: SEC's combined BasicAndDiluted concepts aren't mapped (BUG-032;
9 filers; no value change). VAL-06: gate, N/A reasons, volatility, buy_below, discount, the median exclusion and the
API's verdict all follow the spec on 127 real and 19 golden rows.
Evidence: `qa/evidence/iter26-{leads-xom-now,leads-dell-scco,split-veto-scan,basic-and-diluted,val06}.txt`,
`iter26-oracle-{fy-window,shares-fallback}-{real,golden}.txt`
Result: VAL-03 → NEEDS-USER (BUG-007 only); VAL-04 → NEEDS-USER (Q5: 16 rows incl. DELL); VAL-05 → FAIL→BUG-031;
VAL-06 → PASS; VALID-01 → FAIL→BUG-032. Real copy: Level 1 0/519, Level 2 9 beyond 0.5% (the 8 BUG-007 rows and
NOW), 39 not derivable (Q5); golden 0/57. Leads L6, L7 and L9 resolved. New: BUG-031 (P1), BUG-032 (P2).
Gates: ruff ✓ mypy ✓ pytest 912 passed (1 known warning) | CI: 61f8a56 ✓; pending for this push
Commit: oracle e4b6a1f and 0f47b5f, then (this commit) chore(qa): discovery VAL-03 to VAL-06
Next: BUG-031 (P1); record code review 4 when it reports

## Iteration 27: 2026-10-20 04:13: fix
Picked: BUG-031 (P1), the only open P0–P1 (CI: 5bb51b4 running). Code review 4 is still running.
Did: No oracle change was needed (it already gave NOW's expected band). Tests first: NOW's shape (a declared 5:1
matching a seam where EPS rose) failed, and so did a guard (a declared 2:1 must not also explain an older acquisition
seam): FY2017 came out 4x, which exposed a second defect, the EPS test reading the newer year after it was already
rescaled. A third test pins the EPS test for undeclared jumps (none existed). Fix in `_normalize_splits`: declared
matches skip the EPS test, the test reads as-reported EPS, and each declared split explains one seam. Five mutants
each fail a test; restored byte-identical.
Evidence: `qa/evidence/iter27-bug031-{red,green,mutants,impact,api}.txt`, `iter27-oracle-{real,golden}.txt`
Result: BUG-031 VERIFIED. One row of 762 changes (NOW multiples $153.19 → $258.45 base); no summary, verdict or golden
row moves. Oracle: real Level 2 8 beyond 0.5% (BUG-007 only), golden 0/57. VAL-05 → PASS.
Gates: ruff ✓ mypy ✓ pytest 915 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): a declared split counts even when EPS rose across its seam
Next: BUG-032 (P2); record code review 4 when it reports

## Iteration 28: 2026-10-20 04:21: fix
Picked: BUG-032 (P2), the only open P0–P2 (CI: a8fb8e4 running). Code review 4 is still running.
Did: The parser resolves tags per period, so the fix is the registry: SEC's combined BasicAndDiluted EPS and share
concepts become the second candidate of `eps_basic`, `eps_diluted` and both weighted share counts. Tests first: a
year filed only under the combined concepts had no record at all, and a basic count came only from the combined one
(both RED); the separate concepts keep priority. Three mutants (no diluted fallback; the combined tag first; no
basic-count fallback) each fail a test; restored byte-identical. Verified end to end: the cached filings of all nine
filers gain 132 values and change none; a CLI collection of TSLA from a scratch SEC cache stores its FY2012–14 EPS.
Evidence: `qa/evidence/iter28-bug032-{red,green,mutants,parse,cli,impact}.txt`, `iter28-oracle-real.txt`
Result: BUG-032 VERIFIED. On a real-data copy with the values filled, no valuation or summary row moves (SCCO's
Lynch now measures FY2016–25: 20.2%, capped at 15% as before); oracle Level 2 still only the 8 BUG-007 rows.
VALID-01 → TODO (the coverage_report run is left).
Gates: ruff ✓ mypy ✓ pytest 917 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): EPS and share counts filed as BasicAndDiluted fill both fields
Next: no P0–P2 is open: discovery VAL-09, VAL-12, VAL-13; record code review 4 when it reports

## Iteration 29: 2026-10-20 04:27: discovery (code review 4)
Picked: recording code review 4, which reported after iteration 28 (no P0–P2 bug was open) (CI: a8fb8e4 ✓; 1810180
running).
Did: Read each of the review's repro scripts, then ran adapted copies against the current code (1810180) on fresh
scratch copies, with yfinance mocked and no network: a failed quote refresh writes a price-less latest snapshot and
reports success; summary.csv blanks company fields a run lacked; a BX-like and a KLAC-like post-filing split are refused;
the DDM cadence cases misread frequency changes and a same-count restart; MRVL's count comes from market cap ÷ price.
Read the spec and code for the rate provenance and the missing-count path.
Evidence: `qa/evidence/iter29-review4-checks.txt` (the review's scripts and outputs are under SCRATCH/review4; the adapted copies wrote to SCRATCH/iter29)
Result: New: BUG-033 (P1), BUG-034–037 (P2), BUG-038–039 (P3); BUG-017 reopened (P2); BUG-018 and BUG-022 notes
corrected. VAL-01 → FAIL→BUG-035, VAL-02 → FAIL→BUG-017, VAL-09 → FAIL→BUG-036, VAL-10 → FAIL→BUG-037, MATCH-05 →
FAIL→BUG-034, GATHER-13 → FAIL→BUG-033. The review also confirmed the baseline gates, iteration 25's oracle results,
VAL-07, VAL-08 and VAL-11, and the code's and oracle's share counts on 146 companies.
Gates: ruff ✓ mypy ✓ pytest 917 passed (1 known warning) | CI: pending for this push
Commit: (this commit) chore(qa): record code review 4 findings
Next: BUG-033 (P1); then the P2s oldest first (BUG-017, BUG-034, BUG-035, BUG-036, BUG-037)

## Iteration 30: 2026-10-20 04:38: fix
Picked: BUG-033 (P1), the only open P0–P1 (CI: 1810180 ✓; 63ffeaf running).
Did: Tests first, all RED: an empty quote and a price-history-only export each made a price-less latest snapshot,
and a rate-limited refresh through the real YahooHandler (yfinance mocked) counted as a success. Fix: a snapshot needs
this run's price in both `SQLiteStore.export` and `upsert_quote`, and `fetch_quote` reports an error when it got no
price. A fourth test then showed the page still said "Quote refreshed." for that failed job (RED); the quote job now
keeps each failed ticker's message and the status fragment shows it. Five mutants each fail a test; restored
byte-identical. Re-ran the review's scenarios on fresh golden copies; checked that a good refresh writes only the two
snapshot rows.
Evidence: `qa/evidence/iter30-bug033-{red,green,mutants,e2e}.txt`, `iter30-gather13-only-snapshot.txt`
Result: BUG-033 VERIFIED. GATHER-13 → PASS.
Gates: ruff ✓ mypy ✓ pytest 921 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(exporters): a market snapshot needs a price, and a failed quote refresh says so
Next: BUG-017 (P2, the oldest open P2; a third fix attempt, oracle rule first)

## Iteration 31: 2026-10-20 04:46: fix
Picked: BUG-017 (P2), the oldest open P2; its third fix attempt (CI: 5e64093 ✓).
Did: Weighed the design first (systematic-debugging 4.5): two heuristic fixes each exposed new end-of-window shapes;
the alternatives (SEC dividends per share, or only the initiation-year rule) change the spec's input or bring back
the slip bias, so this attempt is the review's bounded two-condition rule, with the alternatives recorded for the
user if it fails. Oracle first (0345344): on a golden copy with KO, MSFT, AAPL and XOM rewritten into the review's
shapes it flagged exactly those four DDM rows (the old oracle passed them). Tests: three cadence shapes and a
same-count restart RED; an HWM guard added. Fix in `value_ddm`; four mutants each fail a test; restored
byte-identical.
Evidence: `qa/evidence/iter31-bug017-{red,green,mutants,impact}.txt`, `iter31-cadence-db.txt`,
`iter31-oracle-old-rule-cadence.txt`, `iter31-oracle-before-fix-{cadence,real,golden}.txt`,
`iter31-oracle-{cadence,golden,real}.txt`
Result: BUG-017 VERIFIED. No real or golden valuation or summary row moves; the demo rows return to the growth of their
untouched records. Oracle: demo and golden 0/57, real the 8 BUG-007 rows. New: BUG-040 (P3, review 4's #7 and #8,
moved out of BUG-017). VAL-02 → FAIL→BUG-040.
Gates: ruff ✓ mypy ✓ pytest 926 passed (1 known warning) | CI: pending for this push
Commit: oracle 0345344, then (this commit) fix(valuation): a schedule change next to a DDM window end is no slipped
payment
Next: BUG-034 (P2, the oldest open P2)

## Iteration 32: 2026-10-20 04:50: fix
Picked: BUG-034 (P2), the oldest open P2 (CI: e84da96 running).
Did: The JSON merges company_info field by field and SQLite COALESCEs the companies row, while the CSV's section rule
(iteration 24) replaced the whole company section. Test first, across all three stores: two runs, the second
lacking industry, country, employees and website: the JSON and SQLite kept them, the CSV blanked them (RED). Fix: the
CSV keeps each company column's earlier value the run lacked, as it does for identifiers; the other sections keep
the section rule. A mutant restoring the old rule fails the test; restored byte-identical.
Evidence: `qa/evidence/iter32-bug034-{red,green,mutants,e2e}.txt`
Result: BUG-034 VERIFIED; the review's scenario shows the three stores agreeing. MATCH-05 → FAIL→BUG-038 (P3 left).
Gates: ruff ✓ mypy ✓ pytest 927 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(exporters): summary.csv keeps company fields a run lacked, as the JSON and SQLite do
Next: BUG-035 (P2, MRVL's share-class gap)

## Iteration 33: 2026-10-20 04:57: fix
Picked: BUG-035 (P2), the oldest open P2 (CI: f75ce66 running).
Did: Measured first (`iter33-share-rule-scan.txt`): the review's 25% bound would also move SPG (17%, its FY count
excludes partnership units) and so decide the unit question; every gap of 5% or more is a class or units, so the
bound is 5%. Oracle first (73d4a15): a gap under 5% is settled by whichever figure is nearer the latest FY count; it
flagged exactly MRVL's DCF against the unfixed code. Tests: the MRVL shape RED; PLTR and SPG guards. Fix in
`_total_shares` (`SHARE_SOURCE_GAP`); three mutants each fail a test; restored byte-identical.
Evidence: `qa/evidence/iter33-{share-rule-scan,bug035-red,bug035-green,bug035-mutants,bug035-impact}.txt`,
`iter33-oracle-before-fix-{real,golden}.txt`, `iter33-oracle-{real,golden}.txt`
Result: BUG-035 VERIFIED. One row moves: MRVL's DCF base $10.59 → $10.85 (median $12.97 → $13.10). Oracle: real the 8
BUG-007 rows, golden 0/57. VAL-01 → FAIL→BUG-039 (P3 left).
Gates: ruff ✓ mypy ✓ pytest 930 passed (1 known warning) | CI: pending for this push
Commit: oracle 73d4a15, then (this commit) fix(valuation): a small share-count gap is settled by the filings' count
Next: BUG-036 (P2, the post-filing split check)

## Iteration 34: 2026-10-20 05:06: fix
Picked: BUG-036 (P2), the oldest open P2 (CI: 5ed05dc ✓).
Did: Weighed (b) with data: all 430 simple factors of 3:2 or more in the real split history are share splits and the
9 others are spin-off or merger price factors, so a simple ratio after the latest filing is a split even while
Yahoo's count lags; a non-simple factor needs either count to confirm it. Oracle first (f1dbf87): on a scratch DB
with a lagging KLA-like and a BX-like company it flagged KLA's three per-share rows against the unfixed code (the
old oracle shared the lag). Tests: both shapes RED; ABT's spin-off guard kept. Fix in `_apply_later_splits`
(`_simple_ratio`, `today_counts`); three mutants each fail a test; restored byte-identical.
Evidence: `qa/evidence/iter34-{bug036-red,bug036-green,bug036-mutants,bug036-impact}.txt`,
`iter34-oracle-old-rule-splits.txt`, `iter34-oracle-before-fix-{splits,real,golden}.txt`,
`iter34-oracle-{splits,golden,real}.txt`
Result: BUG-036 VERIFIED. No real or golden row moves; the demo's KLA and BXL now value on the post-split basis.
Oracle: demo 0/6, golden 0/57, real the 8 BUG-007 rows. VAL-09 → TODO (BUG-036 fixed; the rest untested).
Gates: ruff ✓ mypy ✓ pytest 932 passed (1 known warning) | CI: pending for this push
Commit: oracle f1dbf87, then (this commit) fix(valuation): a split after the latest filing applies while Yahoo's
count lags
Next: BUG-037 (P2, the last open P2: the rate's date and static-estimate flag)

## Iteration 35: 2026-10-20 05:13: fix
Picked: BUG-037 (P2, the last open P2) (CI: 3fef75b running), then switched to BUG-041 (P0) found while tracing it:
the priority rule puts a P0 first.
Did: `_fetch_treasury_gov_year` read the feed's last row as the latest. Without network the stored rates couldn't
settle the order (the user's data came from FRED), so one fetch of the 2026 feed did: newest first (10/19/2026 5.15%
... 01/02/2026 4.19%), and the golden run's 0.0419 is January 2's. Tests first in a new `tests/test_fred_handler.py`
(the handler had none): the newest-first feed gave 0.0419 (RED), and the row's date wasn't kept (RED). Fix: the
latest-dated row, whatever the order, with its date; a dated row without yields is no rate. Three mutants each fail
a test; restored byte-identical. Live: 2026-10-19's 5.15%.
Evidence: `qa/evidence/iter35-{treasury-feed-order,bug041-red,bug041-green,bug041-mutants,bug041-live,bug041-impact}.txt`
Result: BUG-041 (P0) found and VERIFIED. With the right rate the golden copy moves 13 of 114 rows and 7 of 19 summaries
(AAPL's DCF base $140.53 → $121.67); the user's FRED-sourced data is unaffected. GATHER-04 → FAIL→BUG-037. The golden
DB still holds the stale 0.0419: re-collect it in the next sweep.
Gates: ruff ✓ mypy ✓ pytest 935 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(fetchers): the key-less risk-free rate is Treasury.gov's latest day, not January 2's
Next: BUG-037 (P2: carry the rate's date and source into the stores and assumptions)

## Iteration 36: 2026-10-20 05:23: fix
Picked: BUG-037 (P2), the last open P2 (CI: f0db42a running). Code review 5 is running (dispatched at the end of
iteration 35: six fixes since review 4).
Did: Traced the rate from the fetch to the assumptions: the handler returned a bare float and the fetcher labelled
every key-less rate "fallback". Tests first across the four layers (handler, snapshot, inputs, models), all RED. Fix:
the rate travels with its day and source into the JSON, two new snapshot columns (carried by quote refreshes), the
valuation inputs, and the DCF, DDM and owner-earnings assumptions (`rf_estimate` for the static estimate); the
inputs read the snapshot with `SELECT *` because the backfill doesn't migrate. Six mutants each fail a test; all
restored byte-identical. One live key-less collection of AAPL (golden basket) checked it end to end.
Evidence: `qa/evidence/iter36-bug037-{red,green,mutants,e2e,impact}.txt`
Result: BUG-037 VERIFIED; no P0–P2 bug is open. VAL-10 → PASS; GATHER-04 stays TODO (FRED path, January fallback).
No existing value moves.
Gates: ruff ✓ mypy ✓ pytest 942 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): valuations record the risk-free rate's day and source
Next: record code review 5 when it reports; otherwise discovery (VAL-09, VAL-12, VAL-13)

## Iteration 37: 2026-10-20 05:29: discovery
Picked: VAL-12 and VAL-09, because no P0–P2 bug was open and VAL comes first (CI: f0db42a ✓; 8feac32 running). Code
review 5 is still running.
Did: VAL-12: rendered every company's VAL tab in-process on the iteration-36 copies and compared each number with the
API, both formatted by the app's formatters, and checked each DCF sensitivity grid's centre and corners. VAL-09:
swept both copies for every listed edge case against the spec's N/A, sector, cap and flag rules. The short-history
case surfaced three gathering gaps, read from the golden SEC cache: XOM now maps to ExxonMobil Holdings Corp's new
CIK (filings since 2026-07, no 10-K yet); ARM files US-GAAP 20-Fs, which the parser skips; TSM files IFRS; and none
of the three is explained.
Evidence: `qa/evidence/iter37-{val12,val09}.txt`
Result: VAL-12 PASS (0 differences over 146 tabs, 579 model rows, 100 grids); VAL-09 PASS (0 problems). New: BUG-042 (P1),
BUG-043 (P1), BUG-044 (P2); lead L1 resolved. GATHER-02 → FAIL→BUG-042.
Gates: ruff ✓ mypy ✓ pytest 942 passed (1 known warning) | CI: pending for this push
Commit: (this commit) chore(qa): discovery VAL-09 and VAL-12 pass; XOM, ARM and TSM gathering gaps
Next: BUG-042 or BUG-043 (P1, oldest first: BUG-042, whose fix direction is a design question); record code review 5
when it reports

## Iteration 38: 2026-10-20 06:11: fix
Picked: BUG-042, the oldest open P1. Its "explained by name" outcome is the mechanism BUG-044 needs, so one change
fixes both; keeping the predecessor's history is a design choice, put to the user as Q6. CI: ce6e232 running at the
start.
Did: TDD (12 tests, each RED first). `explain_no_annual_financials` (xbrl_parser) says why SEC data gave no annual
financials: no XBRL, IFRS, a foreign issuer's forms, no 10-K yet (with the first filing date), or no full year in the
10-K facts. The fetcher adds it as a warning naming the ticker, CIK and registrant, or says the companyfacts request
failed. `collection_runs` gains a `warnings` column (JSON), added in place by the store's migration: on a fresh
real-DB copy, twice, 19 tables and 1,337,302 rows unchanged, integrity ok. The Reader returns the latest run's
warnings (none on databases without the column), `GET /api/quality/runs` returns them as a list, and the FA and VAL
tabs show the note. 12 mutants each fail a test. Live: offline collections from the golden SEC cache (XOM, TSM, ARM
and AAPL into a fresh DB; XOM into the real-DB copy), then a server on port 8765: each note shows on the FA and VAL
tabs, none for AAPL or JPM, and XOM keeps its 20 stored years. In Chrome the note is one muted line above the empty
table, with no console errors and no sideways scroll (screenshots timed out twice, so the DOM was read instead).
Found: BUG-045 (P2: a failed companyfacts or submissions request is not an error) and BUG-046 (P3: `stock-web
--help` starts the server). My lookup started one on port 8000 against the user's database; it served no request
and was stopped; `stock.db` and its WAL are unmodified (the `-shm` index changed at 05:48 from this iteration's
read-only backup, before the server started at 05:50). Code review 5 reported (61f8a56..f0db42a): saved to
`qa/evidence/review5-report.md`; its findings (I1–I3 P2, M1–M8 P3) are recorded next iteration, after re-running
their key claims.
Evidence: `qa/evidence/iter38-bug042-{red,green,green2,mutants,migration,e2e}.txt`, `qa/evidence/review5-report.md`
Result: BUG-042 VERIFIED (explained gap; the history is Q6), BUG-044 VERIFIED; new BUG-045, BUG-046 and Q6.
GATHER-02 → FAIL→BUG-043.
Gates: ruff ✓ mypy ✓ pytest 953 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(fetchers): say why a filer's SEC data gave no annual financials
Next: record code review 5 (re-run I1–I3 first), then BUG-043 (P1). Fixes since review 5's range: 2 (8feac32, this).

## Iteration 39: 2026-10-20 06:20: review
Picked: recording code review 5 (LOOP_PROMPT §4.6), which reported at the end of iteration 38; BUG-043 (P1) is next.
CI: 98013f4 running at the start (ce6e232 ✓).
Did: re-ran every claim on an archive of 98013f4 and on the reviewer's base (61f8a56), from copies of the review's
scripts (§4.4). All 11 claims hold on HEAD: I1 (split factor 4.0 against 2.0; Lynch $18.60 against $7.75, and the
oracle flags it), its variant, I2 (Graham $14.21 against $142.08 beside a $1,000 price; DDM $9.45 at both), I3 (7.75%
against 5.12%; four more shapes misread at both revisions; the oracle gives the same numbers), and M1–M8. Current
impact checked independently: no post-filing split at a seam (CRWD a near miss), no split after the latest priced
snapshot, and no DDM value different from 61f8a56 (99 real, 14 golden payers), so I1 and I2 stay P2. Under §10,
BUG-017's three fixes send the DDM payment-cadence design to the user (BUG-049, with options) rather than a fourth
patch. The review's units policy note becomes Q7. The drift scan also showed HON's FY count at 2.03× Yahoo's (L17).
Process note from the review: list every commit of the range in the next review brief (review 5's brief missed five).
Evidence: `qa/evidence/iter39-review5-rerun.txt`, `qa/evidence/review5-report.md`
Result: new BUG-047 and BUG-048 (P2), BUG-049 (P2, NEEDS-USER), BUG-050 to BUG-056 (P3), Q7 and L17. VAL-02 →
NEEDS-USER; VAL-09 → FAIL→BUG-047; GATHER-04 → FAIL→BUG-052; STORE-06 notes BUG-055.
Gates: ruff ✓ mypy ✓ pytest 953 passed (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): record code review 5; the DDM cadence design goes to the user
Next: BUG-043 (P1: read ARM's US-GAAP 20-F), then BUG-047 and BUG-048 (P2, oracle commit first for BUG-048), then
BUG-045. Fixes since review 5's range: 2 (8feac32, 98013f4).

## Iteration 40: 2026-10-20 06:38: fix
Picked: BUG-043, the only open P1. CI: 98013f4 ✓, 9892af9 (records only) running at the start.
Did: TDD (7 tests, RED first). A foreign issuer's annual report (20-F, 20-F/A, 40-F, 40-F/A) now counts as annual for
the annual record, the vintages and the unmapped-tag worklist. Since the parser reads each field in its dollar unit,
a filer reporting in another currency would resolve only its share counts; such a record is dropped, and the run says
"it reports in CNY (20-F), which isn't converted to dollars". The explanation's 20-F wording was updated (a 6-K-only
filer: "no annual report (20-F) yet"). Impact over all 143 cached companyfacts files (the user's 126 and the golden
basket's): 142 records unchanged, ARM 0 → 6 fiscal years. Measuring that needed a deterministic digest: two runs of
the unchanged code disagreed, which traced to BUG-057 (a set-ordered frame choice). Six mutants each fail a test. Live:
ARM collected (Yahoo live, SEC from the golden cache) has FY2021–26 matching its reports, four applicable models
(median $13.73 against $228.05), the oracle agreeing at both levels, and an FA tab with six years.
Found: BUG-057 (P3: calendar labels in hash order; 3 vintage periods' stored calendar_year flips, WMT and TJX FY2012)
and BUG-058 (P3: a 20-F filer's 6-K interims aren't read, so ARM has no quarterly or TTM rows).
Evidence: `qa/evidence/iter40-bug043-{red,green,mutants,impact,e2e}.txt`
Result: BUG-043 VERIFIED; no P0 or P1 open. GATHER-02 → TODO (its foreign-filer cases done; the other ticker kinds
untested). New BUG-057, BUG-058.
Gates: ruff ✓ mypy ✓ pytest 958 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): read a foreign issuer's annual report (20-F, 40-F)
Next: BUG-047 or BUG-048 (P2, oldest first: BUG-047; for BUG-048 the oracle rule changes first), then BUG-045. Fixes
since review 5's range: 3 (8feac32, 98013f4, this); review 6 after two more.

## Iteration 41: 2026-10-20 06:45: fix
Picked: BUG-045, the oldest open P2 (iteration 40's "Next" put BUG-047 first, against the oldest-first rule; corrected
here). CI: 9892af9 ✓, 14ac7c6 running at the start.
Did: TDD (4 tests, RED first: the summary read 1 successful and 0 with errors). `merge_sec_data` records a failed
companyfacts or submissions request as an error, and the SEC handler stops logging "Successfully fetched SEC data"
after one. The exporters don't branch on a run's errors, so only the counts and the summary change. Three mutants
each fail a test. Real app: the `stock-data` CLI entry point run with companyfacts blocked by a stubbed 403 (no
request sent to SEC) prints "Successfully fetched: 0 / With errors: 1", stores `error_count` 1 and the error in the
JSON, logs "SEC data for AAPL is incomplete", and the FA note names the 403. It also exits 0 with every ticker
failed: lead L18 for CLI-01.
Evidence: `qa/evidence/iter41-bug045-{red,green,mutants,e2e}.txt`
Result: BUG-045 VERIFIED. Open: P2 2 (BUG-047, BUG-048), P3 23.
Gates: ruff ✓ mypy ✓ pytest 962 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(fetchers): a failed SEC request is an error, not a success
Next: BUG-047 (P2), then BUG-048 (P2; the oracle rule changes first). Fixes since review 5's range: 4 (8feac32,
98013f4, 14ac7c6, this); review 6 after the next fix.

## Iteration 42: 2026-10-20 06:53: fix
Picked: BUG-047, the oldest open P2 with BUG-048 (both iteration 39; lower number first). CI: 522da04 running at the
start (14ac7c6 ✓).
Did: TDD (the review's merger-then-split shape through `load_inputs`, RED: FY2016 split factor 4.0, EPS 0.50).
`load_inputs` now offers `_normalize_splits` only the splits dated on or before the latest filing, the complement of
`_apply_later_splits`, so a post-filing split moves every year once. Two mutants each fail a test. The review's case
end to end: Lynch $7.75 on 5.0% (was $18.60 on 13.4%), and the oracle, which flagged it, now agrees at both levels.
Impact: fresh copies of the user's database and the golden one, backfilled with 9892af9's code and with the fix: 0 of
762 and 0 of 114 valuation rows change. The review's variant (a split whose own seam lies before the window) is a
different mechanism and still reproduces: split off as BUG-059 (P2), with per-field filing dates from the vintages as
the fix direction; a scan finds no current case (NOW's genuine seam is the only match the EPS test would refuse).
Evidence: `qa/evidence/iter42-bug047-{red,green,mutants,e2e,impact}.txt`, `qa/evidence/iter42-bug059-scan.txt`
Result: BUG-047 VERIFIED; new BUG-059. VAL-09 → FAIL→BUG-048.
Gates: ruff ✓ mypy ✓ pytest 963 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(valuation): a split after the latest filing never explains an older seam
Next: code review 6 (five fixes since review 5's range: 8feac32, 98013f4, 14ac7c6, 522da04, this), dispatched in the
background after this push; then BUG-048 (P2; the oracle rule changes first), then BUG-059.

## Iteration 43: 2026-10-20 07:06: fix
Picked: BUG-048, the oldest open P2. CI: f9bbc91 ✓. Code review 6 is still running.
Did: every valuation input was already on today's basis (per-share figures, Yahoo's split-adjusted dividends and bars,
the 0.75 rule's count) except the snapshot price, which every view compares them with; so the values now stay on
that price's basis. The oracle took the rule first, in its own commit (30b3c5a, citing VAL-03, VAL-07 and VAL-09): on
the whole scenario valued by the unfixed code it flagged all five applicable models, 10x off, and its verdicts on the
real and golden copies were unchanged. Then TDD (2 tests; the stale case RED at split factor 10.0, EPS 3.037; the
control guards the normal case): `_apply_later_splits` applies a post-filing split only up to the price's day, and
Yahoo's dividends and FY-end bars are multiplied back by a split after both the filing and that day. Three mutants
each fail a test. The scenario valued by the fix: median $277.31 beside $1,000 (was $27.73), the oracle agreeing at
both levels; the real and golden copies revalued: 0 of 762 and 0 of 114 rows change. A split between the price's day
and a filing that restated it is a different case; reproduced and recorded as BUG-060 (P2).
Evidence: `qa/evidence/iter43-bug048-{oracle,red,green,mutants,e2e,impact}.txt`, `qa/evidence/iter43-bug060-repro.txt`
Result: BUG-048 VERIFIED; new BUG-060. VAL-09 → FAIL→BUG-059.
Gates: ruff ✓ mypy ✓ pytest 965 passed (1 known warning) | CI: pending for this push
Commits: 30b3c5a chore(qa): oracle keeps per-share values on the latest price's basis; (this commit) fix(valuation):
per-share values stay on the latest price's basis
Next: record code review 6 when it reports; then BUG-059 (P2) or BUG-060 (P2), oldest first: BUG-059.

## Iteration 44: 2026-10-20 07:18: fix
Picked: BUG-059, the oldest open P2. CI: 65a0602 running at the start (f9bbc91 ✓). Code review 6 is still running.
Did: a filing restates its figures for every share split made before it, so whether a year's figure reflects a split
follows from when it was filed. Feasibility first: with each year's filing date per field from the vintages, every
simple-ratio seam the code matches today lies between its two years' filings (22 real, 8 golden); RTX's 1.589
spin-off factor (L12) is the one outside, so the rule covers simple ratios only. The oracle took it first (fd449ef):
for a simple split it now reads the figure's own filing date instead of the nearer-to-today's-count guess; on the
variant (rebuilt with vintages) valued by the unfixed code it flagged Lynch ($34.80 against $14.50), and its output on
the real and golden copies stayed identical. Then TDD (2 tests: the variant RED at split factor 2.0 and EPS 1.0; a
NOW-like control whose EPS rises across a genuine seam, so only the declared match explains it): `load_inputs` reads
each year's detection-field filing date and `_normalize_splits` offers a simple share split to a seam only if it lies
between the two filings. Three mutants each fail a test. The variant valued by the fix: Lynch $14.50 on 4.2%, with the
oracle agreeing; the real and golden copies revalued: 0 of 762 and 0 of 114 rows change.
Evidence: `qa/evidence/iter44-bug059-{seam-dates,oracle,red,green,mutants,e2e,impact}.txt`
Result: BUG-059 VERIFIED. Open P2: BUG-060 only. VAL-09 → FAIL→BUG-060.
Gates: ruff ✓ mypy ✓ pytest 967 passed (1 known warning) | CI: pending for this push
Commits: fd449ef chore(qa): oracle reads a split's restatement from each figure's filing; (this commit)
fix(valuation): a share split explains only a seam between its filings
Next: record code review 6 when it reports; then BUG-060 (P2; the oracle first). Fixes since review 6's range
(f0db42a..f9bbc91): 2 (65a0602, this).

## Iteration 45: 2026-10-20 07:26: fix
Picked: BUG-060, the only open P2. CI: 72568ea running at the start (65a0602 ✓). Code review 6 is still running.
Did: values stay on the price's basis when the latest snapshot predates a split the latest filing already restated.
The oracle took the rule first (5070282): its date reading already reached today's basis, but its closeness reading
compared with the snapshot's count, which isn't on today's basis here; it now moves that count by the splits restated
since the price's day and takes every year back at the end, and takes Yahoo's series back for every split after the
price's day. On the scenario (rebuilt with consistent filing dates, with and without vintages) valued by the unfixed
code it flagged all five applicable models, 10x off, and its output elsewhere (real, golden, BUG-048's and BUG-059's
scenarios) was unchanged. Then TDD (2 tests: RED at split factor 1.0, EPS 3.037 beside a pre-split price; the control
has the snapshot after the split): `_undo_splits_after_price` takes such a share split back out of every year, and
Yahoo's series go back for every split after the price's day. Three mutants each fail a test. Valued by the fix: median
$277.31 beside $1,000 (was $27.73), with the oracle agreeing in both variants; the other split scenarios still agree;
the real and golden copies revalued: 0 of 762 and 0 of 114 rows change.
Evidence: `qa/evidence/iter45-bug060-{oracle,red,green,mutants,e2e,impact}.txt`
Result: BUG-060 VERIFIED. No P0-P2 bug is open (P3 23, NEEDS-USER 10). VAL-09 → FAIL→BUG-050 (P3).
Gates: ruff ✓ mypy ✓ pytest 969 passed (1 known warning) | CI: pending for this push
Commits: 5070282 chore(qa): oracle takes a split restated after the price's day back out; (this commit)
fix(valuation): a split restated after the price's day is taken back out
Next: record code review 6 when it reports; otherwise a discovery unit (VAL-13 hand-worked examples, then VALID).
Fixes since review 6's range (f0db42a..f9bbc91): 3 (65a0602, 72568ea, this).

## Iteration 46: 2026-10-20 07:34: discovery
Picked: VAL-13, the last TODO in VAL (no P0-P2 bug open). CI: 144dcdf queued at the start. Code review 6 is still
running.
Did: worked AAPL (general), JPM (bank) and KO (dividend payer) step by step from the golden copy's raw tables
(financials, vintages for the split basis, snapshot, analyst, dividends, price bars), with the formulas taken from the
two specs' text and nothing imported from `src` or the oracle. Every applicable model (AAPL 6, JPM 3, KO 6; JPM's DCF,
Lynch and owner earnings correctly N/A as a bank) and every summary equals the stored values to 0.000%. The specs leave
four readings open; the stored values follow these, each recorded since iteration 4 and now written into GOLDEN.md:
reported diluted EPS (JPM's preferred dividends), BVPS on diluted weighted shares (L8), a ±1pp scenario discount not
re-clamped to 8-14% (KO's bull runs at 7%), and year-end discounting with a linear fade.
Evidence: `qa/evidence/iter46-val13-handworked.txt`; `qa/GOLDEN.md` → Reference values
Result: VAL-13 PASS. No new bugs.
Gates: ruff ✓ mypy ✓ pytest 969 passed (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): discovery VAL-13 passes; hand-worked AAPL, JPM and KO
Next: record code review 6 when it reports; otherwise discovery in VALID (riskiest TODO area after VAL).

## Iteration 47: 2026-10-20 07:45: discovery
Picked: VALID-02, -03, -04 and -12, a read-only sweep of the stored fundamentals (no P0-P2 bug open; VALID is the
riskiest area left). CI: 6d3024c queued at the start. Code review 6 is still running.
Did: swept a fresh copy of the user's database (2,312 company-years, 126 companies) and the golden copy for the
accounting identities, sign conventions, units and scale, and a sanity scan, then triaged every hit, re-reading the
cached companyfacts and re-parsing with today's parser to tell current behaviour from stale data. A vintage scan (each
field's latest filing against the ones before) found the one real defect class: a later 10-K's tagging error in a
comparative year (a sign flip, a power-of-ten scale, a zero) overrides the figure the earlier filings agree on (AMD
FY2021 cash flows, STX FY2022 dividend x10^6, DIS FY2021 interest expense, BAC FY2008 assets, BRK-B FY2008 shares).
Most of the 63 such field-years are genuine 10:1 split restatements or corrections. Everything else traced to real
facts or presentation: temporary equity, unmapped NCI tags (BX), restated fields mixed with unrestated ones (MSFT
FY2016), excise taxes in revenue (MO, PM), declared-vs-paid dividend timing (PGR), insurers' accretion. Two stored
values are stale (the August collection predates BUG-013's fix: AMGN FY2020 DPS).
Evidence: `qa/evidence/iter47-{valid-sweep-real,valid-sweep-golden,vintage-flips,valid-triage}.txt`
Result: VALID-03 PASS; VALID-02, -04 and -12 → FAIL→BUG-061. New: BUG-061 (P2), BUG-062 and BUG-063 (P3), leads L19
and L20; BUG-020 notes three more filers.
Gates: ruff ✓ mypy ✓ pytest 969 passed (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): discovery VALID-02, -03, -04, -12; later filings' tagging errors win
Next: BUG-061 (P2), unless code review 6 reports first.

## Iteration 48: 2026-10-20 07:56: review
Picked: BUG-061 (the only open P2), then code review 6 reported mid-iteration with two proposed P1s, before any code
changed; so this iteration records the review (§4.6), and BUG-061 waits behind the P1s. CI: 3eee7b6 ✓.
Did: saved the report (`qa/evidence/review6-report.md`) and re-ran every claim on an archive of 3eee7b6 with the
reviewer's offline scripts (SEC from the read-only caches, Yahoo off, the rate stubbed). All hold on today's code: the
ADR and convenience-translation demos (ARM as a 5-share ADS: DCF, Graham and Lynch unchanged beside a 5x price; TSM's
facts relabelled as US GAAP: 10 years kept, median $20.40 against $421.70), the failed-submissions demos (PLD's FFO per
share and P/FFO band, JPM's NIM and efficiency ratio, CB's combined ratio wiped from every stored year), the IFRS
switcher (FY2015-19 valued, no note), and M1-M6. Both surviving mutants pass the full suite (969 passed each).
Evidence: `qa/evidence/iter48-review6-rerun.txt`, `qa/evidence/review6-report.md`
Result: new BUG-064 (P1, I2), BUG-065 (P1, I1), BUG-066 (P2, I3), BUG-067 to BUG-072 (P3: M1-M7), Q8. GATHER-02 →
FAIL→BUG-065; GATHER-06 → FAIL→BUG-064.
Gates: ruff ✓ mypy ✓ pytest 969 passed (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): record code review 6; ADRs, convenience translations and a wiping failure path
Next: BUG-064 (P1: keep the stored sector when the submissions request fails), then BUG-065 (P1; oracle first), then
BUG-061 and BUG-066 (P2). Fixes since review 6's range: 3 (65a0602, 72568ea, 144dcdf).

## Iteration 49: 2026-10-20 08:02: fix
Picked: BUG-064, the older-numbered of the two open P1s (both iteration 48). CI: 9c72efe running at the start.
Did: TDD (RED: the run's sector None, so a REIT's FFO isn't computed). When the submissions data is missing,
`fetch_ticker` now takes the sector from the stored record before computing metrics: the database's companies row
(read-only), else the JSON export's `sector_class` (a second RED test covers a `--formats json` run), with a warning
saying so. Three mutants each fail a test (no fallback; a lookup ignoring the ticker, caught by a second stored company;
no JSON fallback). The review's own demos on the fixed tree: PLD, JPM and CB keep every stored year's FFO per share, net
interest margin and combined ratio (17 of 18, 18 of 19, 19 of 20; 0 before), and PLD's P/FFO band and summary stay.
Evidence: `qa/evidence/iter49-bug064-{red,green,mutants,e2e}.txt`
Result: BUG-064 VERIFIED. GATHER-06 → TODO (its other failure modes untested).
Gates: ruff ✓ mypy ✓ pytest 971 passed (1 known warning) | CI: pending for this push
Commit: (this commit) fix(fetchers): a failed submissions request keeps the stored sector
Next: BUG-065 (P1: ADRs and convenience translations; the oracle rule first). Fixes since review 6's range: 4
(65a0602, 72568ea, 144dcdf, this).

## Iteration 50: 2026-10-20 08:16: fix
Picked: BUG-065, the only open P1. CI: e87da0e running at the start (9c72efe ✓).
Did: two parts. (B, the parser) TDD (2 tests RED): a filer's reporting currency is the one most of its monetary
facts are in, so US$ convenience translations no longer make a TWD or CNY reporter's record, in the annual record, the
vintages or the quarters (BUG-071 with it), and the explanation names the currency; 0 of 143 cached companyfacts
records change. (A, the ADS basis) the oracle took the rule first (8d8154c, citing VAL-03 and VAL-07): for a 20-F or
40-F filer a filed count within 5% of a whole multiple of Yahoo's is the ADS ratio, and per-share figures move to
the ADS basis; it flagged the unfixed code on the review's 5-share ADS demo and kept its output elsewhere. Then TDD
(3 tests: a 20-F ADR RED; a 10-K filer and a 1:1 20-F as controls): `load_inputs` applies the ratio before the share
count is chosen, and the engine records `ads_ratio` in each applicable model's assumptions. Six mutants each fail a
test. Valued by the fix: the ADS demo's DCF, Graham and Lynch are five times the per-share values and its upside
equals the 1:1 original's, with the oracle agreeing; TSM relabelled as US GAAP keeps no convenience-translated years.
Impact: 0 of 762 real, 114 golden and 6 ARM valuation rows change.
Evidence: `qa/evidence/iter50-bug065-{red,red-a,green,green-a,mutants,oracle,impact-parser,e2e,impact}.txt`
Result: BUG-065 and BUG-071 VERIFIED. No P0 or P1 open; P2: BUG-061, BUG-066. GATHER-02 → FAIL→BUG-066.
Gates: ruff ✓ mypy ✓ pytest 976 passed (1 known warning) | CI: pending for this push
Commits: 8d8154c chore(qa): oracle values a foreign issuer's per-share figures per ADS; (this commit) fix(valuation):
a foreign issuer is valued per ADS, and convenience translations aren't dollars
Next: code review 7 (five fixes since review 6's range: 65a0602, 72568ea, 144dcdf, e87da0e, this), dispatched in the
background after this push; then BUG-061 (P2), BUG-066 (P2).

## Iteration 51: 2026-10-20 08:58: fix
Picked: BUG-061, the oldest open P2 (no P0 or P1 open; code review 7 running in the background). CI: 0ab5c81 running
at the start.
Did: TDD (5 tests RED, 3 controls green): the parser keeps the latest filing's figure for a year unless it is a
tagging error of the one every earlier filing reported (its negative, x10^3 or more, or a zero of a total), and the
year's net change in cash (its cash-flow sections) or the two years on each side (the rest) side with the earlier
filings. Each figure put back becomes a run warning. A first scan put back 20 figures; two were later filings' genuine
zeros (T-Mobile's reverse merger, Prologis before ASC 842), so a zero counts only for a total (6 fewer). The quarterly
ladder had the same error in its year-end rung (AMD's Q4 cash flows, BAC's 2008 checkpoint): 2 tests RED, then it
reads the restored figure. Relaxing the earlier-filings-agree rule adds one case (TJX's -, +, - filings), kept as a
test with two other guard tests; twelve mutants each fail a test. Impact: 14 figures at 12 of 143 cached filers, each
triaged; 14 annual and 13 quarterly digests change, no vintages; through the fetcher offline, TTM rows and four
companies' metrics follow (WMT's 2009 ROIC 25.1% to 16.5%); no valuation input changes. Found on the way: eight
fetcher tests (mine, iterations 41-51) made a live SEC request through an unstubbed insider lookup; an autouse guard
now fails any test that connects off the machine (RED: 8 errors), and the helper stubs the lookup (BUG-073). Found:
quarterly weighted-average share counts are year-to-date differences (BUG-074, P2: AAPL's Q2-Q4 read about -0.05B).
Code review 7 reported after the work was done; recording it is the next unit.
Evidence: `qa/evidence/iter51-bug061-{red,red-quarterly,green,mutants,repairs,relaxed,impact-parser,impact-records,
impact-valuation}.txt`, `iter51-bug073-{netguard,red,green}.txt`, `iter51-bug074-repro.txt`
Result: BUG-061 and BUG-073 VERIFIED; BUG-074 new (P2). VALID-12 → PASS; VALID-02 → FAIL→BUG-062; VALID-04 →
FAIL→BUG-020; VALID-06 → FAIL→BUG-074.
Gates: ruff ✓ mypy ✓ pytest 989 passed at 2964555, 990 at this commit (1 known warning; the network guard on) |
CI: pending for this push
Commits: 2964555 fix(parsers): a later filing's tagging error doesn't override the figure earlier filings agree on;
(this commit) test: the suite refuses the network, and fetcher tests stop calling SEC
Next: record code review 7 (f9bbc91..0ab5c81), verifying each claim: it proposes a P0 (the FY-end prices behind the
multiples band are Yahoo's dividend-adjusted closes) and four P2s (ADS-ratio drift, a two-class 20-F read as an ADS,
the reporting currency decided over a filer's whole history, Yahoo's split undo needing a filing date); then by
priority.

## Iteration 52: 2026-10-20 09:19: review
Picked: recording code review 7 (f9bbc91..0ab5c81), which reported as iteration 51 ended with a proposed P0 (§4.6:
valid findings become bugs; each claim re-run first). CI: 69c5a7e ✓ (Python 3.9-3.12, the network guard on).
Did: read each of the reviewer's offline scripts, then re-ran every claim on today's code (69c5a7e; valuation
unchanged since 0ab5c81) with my own output folder. All hold: C1 (yfinance 1.0's `history()` defaults to
`auto_adjust=True`; the market closes rebuilt from the stored bars equal the published ones to the cent; 98 of 118
multiples rows and 20 of 127 medians move; USB's verdict flips), I1 (the drifted ADS demo returns BUG-065's values),
I2 (GOOGL relabelled 20-F reads an ADS of 2), I3 (the currency switcher keeps no year), I4 (DDM 41.84 against 418.38),
M1 (Graham 45.21 against 452.06), M2, and M3 (9 of 9 mutants survive the whole suite on 69c5a7e). M2 is
wider than reported: the web app's Reader and the as-of reader build the same unescaped URI, so a '#' in the database
path cuts `mode=ro` off, the app answers 500 on every page and an empty file appears at the truncated path (BUG-080,
P1). Added the review's lesson to §7 (an oracle rule that encodes the fix's heuristic says so and is probed on real
distributions first) and a caveat to GOLDEN.md's VAL-13 section.
Evidence: `qa/evidence/review7-report.md`, `qa/evidence/iter52-review7-rerun.txt`
Result: new BUG-075 (P0, C1), BUG-080 (P1, M2 widened), BUG-076 (P2, I1 and I2), BUG-077 (P2, I3), BUG-078 (P2, I4),
BUG-079 and BUG-081 (P3, M1 and M3). VAL-05 and VAL-13 → FAIL→BUG-075; STORE-04 → FAIL→BUG-080; GATHER-02 notes
BUG-076 and BUG-077.
Gates: ruff ✓ mypy ✓ pytest 990 passed (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): record code review 7; dividend-adjusted FY-end prices, ADS ratios and '#' paths
Next: BUG-075 (P0: the FY-end market close, oracle rule first), then BUG-080 (P1), then the P2s oldest first (BUG-066,
BUG-074, BUG-076, BUG-077, BUG-078). Fixes since review 7's range: 2 (2964555, 69c5a7e).

## Iteration 53: 2026-10-20 09:43: fix
Picked: BUG-075, the only open P0. CI: d6c8be7 ✓.
Did: probe first (section 7, since code and oracle would share a reconstruction): the golden copy's stored bars
rebuilt by undoing Yahoo's factor 1 - d / (the close before the ex-date) per later dividend equal Yahoo's
unadjusted closes, fetched live for the 18 tickers, within 0.0001% at every FY-end bar (KO 2021-12-31: 51.546 →
59.21). Then the oracle rule (5c1b693): the FY-end price is the market close, rebuilt for a bar flagged
dividend-adjusted or from a database before the flag; it flagged 101 more multiples rows on the real copy and 11
on the golden one valued by the unfixed code, and read the four synthetic KLA scenarios' bars as adjusted (one
row each). Then TDD (4 tests RED, 1 control green): the fetch asks for market prices (`auto_adjust=False`), the
migration flags bars already stored once, a re-collection clears the flag, and `load_inputs` rebuilds a flagged
bar's FY-end market close. Nine mutants each fail a test. Valued by the fix, the copies change only in the
multiples rows: real 103 rows, 20 medians beyond 0.5% (C +17.8%, JPM +12.6%), USB expensive → fair; golden 12
rows (JPM +13.2%, PGR +6.5%). The oracle then agrees everywhere (real: the known 8; golden and scenarios: 0).
End to end: the golden basket collected live with the fix stores market prices only (KO 59.21, NVDA 20.365); on it
the oracle passes at both levels, VAL-13's hand-worked script equals all 14 models to 0.000%, and bars.csv and the
VAL API show the market close and KO's new band. Treasury.gov's rate there is October 19's 5.15% (iteration 46's
golden copy had January 2's 4.19%, BUG-041): no new issue. New lead L21 (moving averages and the price-history
statistics still read the dividend-adjusted history).
Evidence: `qa/evidence/iter53-bug075-{probe,oracle,red,green,mutants,impact,e2e}.txt`,
`qa/evidence/iter53-val13-handworked.txt`
Result: BUG-075 VERIFIED; VAL-05 and VAL-13 → PASS. No P0 open; P1: BUG-080.
Gates: ruff ✓ mypy ✓ pytest 995 passed (1 known warning) | CI: pending for this push
Commits: 5c1b693 chore(qa): oracle takes the market close at each FY end, not Yahoo's dividend-adjusted price;
(this commit) fix(valuation): FY-end prices are market closes, not Yahoo's dividend-adjusted ones
Next: BUG-080 (P1: a '#' or '%xx' in the database path). The user's valuations come right on the next backfill
or collection (`python -m src.valuation.backfill`), and its price charts on re-collection (L20). Fixes since
review 7's range: 3 (2964555, 69c5a7e, this).

## Iteration 54: 2026-10-20 09:54: fix
Picked: BUG-080, the only open P1. CI: a80b143 ✓.
Did: probe first: the review proposed `Path.as_uri()`, but on this machine it opens no UNC path at all, where
today's form opens local and UNC paths alike and fails only on '#' and '%41'; escaping just '%', '#' and '?' in
today's form opens all six cases read-only (`qa/evidence/iter54-bug080-uri-forms.txt`). TDD (6 tests RED, 2
controls green, the '?' case off Windows): one helper, `sqlite_store.read_only_uri`, for the web Reader, the
AsOfReader and the stored-sector fallback. Eight mutants each fail a test (`as_uri()` among them, caught by the UNC
test). In the real app, on the fresh golden collection copied under `web#1` (port 8765, collection and quote
refresh off, stopped after; no process or port left): every page tried answers 200, nothing appears beside the
folder, and the served copy's content equals its source's. STORE-04 checked whole with it.
Evidence: `qa/evidence/iter54-bug080-{red,green,mutants,uri-forms,realapp}.txt`
Result: BUG-080 VERIFIED; STORE-04 → PASS. No P0 or P1 open.
Gates: ruff ✓ mypy ✓ pytest 1004 passed, 1 skipped ('?' on Windows; 1 known warning) | CI: pending for this push
Commit: (this commit) fix(store): read-only connections open a database under any folder name
Next: the P2s, oldest first: BUG-066, BUG-074, BUG-076, BUG-077, BUG-078. Fixes since review 7's range: 4
(2964555, 69c5a7e, a80b143, this); code review 8 after the next fix.

## Iteration 55: 2026-10-20 10:03: fix
Picked: BUG-066, the oldest open P2 (no P0 or P1 open). CI: 57218f1 ✓.
Did: TDD (4 tests RED: the explainer missing, so the fetcher test failed too, and the tabs without the note; run file
by file, since the parser test file's import error hid the rest): `explain_stale_annual_financials` reads ifrs-full
annual-report facts for periods after the record's last one, the run records "annual financials stop at FY... :
later annual reports are in IFRS (...)", and the FA and VAL tabs show it beside the empty-record note. Five mutants
each fail a test. No cached filer gets it (TSM, the only one with IFRS facts, has no US-GAAP record). End to end:
ARM's real facts cut after the FY2024 20-F, the rest moved to ifrs-full, through the fetcher offline, the store, the
valuation and the web app in-process: FY2021-24 kept, the note recorded, both tabs showing it.
Evidence: `qa/evidence/iter55-bug066-{red,green,mutants,impact,e2e}.txt`
Result: BUG-066 VERIFIED; GATHER-02 → FAIL→BUG-076.
Gates: ruff ✓ mypy ✓ pytest 1009 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(gather): a record that stops where IFRS reports begin says so
Next: code review 8 (five fixes since review 7's range: 2964555, 69c5a7e, a80b143, 57218f1, this), dispatched in the
background after this push; then BUG-074, BUG-076, BUG-077, BUG-078 (P2).

## Iteration 56: 2026-10-20 10:25: fix
Picked: BUG-074, the oldest open P2 (code review 8 running in the background). CI: fea2b70 ✓.
Did: TDD (2 tests RED: Q2 read -50M, the TTM count the sum): the ladder stops differencing weighted-average counts,
and a TTM count is the four quarters' average. Checked against the filed 3-month figures, the share-days formula
alone put 93.6% of 5,613 quarters within 0.5% but left 69 beyond 10%, where year-to-date rungs disagree, so a third
test RED: a quarter takes its own filed 3-month figure first (99.9% within 0.5%), with a fourth test for one the
ladder skips. Through the fetcher's pipeline for 23 split-heavy tickers, quarters off their year's average by 2x
fall from 1,086 of 1,529 to 140 of 1,559; across every cached filer (parser output) from 75.4% to 3.3%. No annual
value, metric or valuation moves; 15 false share-scale rescales are gone. Six mutants each fail a test. Found:
quarters keep each filing's split basis, so AMZN's Q4 2020 EPS reads -25.63 (BUG-082, P2, pre-existing).
Evidence: `qa/evidence/iter56-bug074-{red,green,mutants,check,impact}.txt`, `qa/evidence/iter56-bug082-repro.txt`
Result: BUG-074 VERIFIED; BUG-082 new (P2); VALID-06 → FAIL→BUG-082.
Gates: ruff ✓ mypy ✓ pytest 1013 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): a quarter's weighted-average share count is its own, not a year-to-date difference
Next: record code review 8 when it reports; then BUG-076, BUG-077, BUG-078, BUG-082 (P2).

## Iteration 57: 2026-10-20 10:41: fix
Picked: BUG-076, the oldest open P2 (code review 8 still running). CI: 2bb2a24 ✓.
Did: probe first (section 7: a threshold the code and oracle share): over the real copy's 124 companies, none with
an ADS, three pairings of filed and market counts, then the candidate rule's misreads and unreadables for an ADS of
1-10 shares. The period-end count over market cap / price halves the drift (median 1.0%) and ends the class
misreads (DELL, GOOGL at 2.1 with Yahoo's count); 1:1 needs a wide window, and 8- or 10-share ADSs a cap of a
quarter share. Then the oracle rule (2de0046): it flagged the unfixed code on both of code review 7's demos, and
its output elsewhere was unchanged. Then TDD (3 tests RED, 3 BUG-065 tests green): the ratio from the period-end
count over market cap / price, the windows, and an unreadable ratio leaving DCF, Graham, Lynch and owner earnings
N/A with the reason. A unit test of the windows and a check that DDM and multiples keep their own reasons came
next; eight mutants each fail a test. Valued by the fix, the drifted ADS's four models say why, the two-class
filer reads 1, and the oracle agrees; no row of the real or golden copy changes.
Evidence: `qa/evidence/iter57-bug076-{probe,oracle,red,green,mutants,e2e,impact}.txt`
Result: BUG-076 VERIFIED; GATHER-02 → FAIL→BUG-077.
Gates: ruff ✓ mypy ✓ pytest 1024 passed, 1 skipped (1 known warning) | CI: pending for this push
Commits: 2de0046 chore(qa): oracle reads an ADS ratio against market cap / price, and leaves an unreadable one
underivable; (this commit) fix(valuation): an ADS ratio is read against every class, and one that can't be read
says so
Next: record code review 8 when it reports; then BUG-077, BUG-078, BUG-082 (P2). Fixes since review 8's range: 2
(2bb2a24, this).

## Iteration 58: 2026-10-20 10:53: fix
Picked: BUG-077, the oldest open P2 (code review 8 running). CI: 80ea6d9 ✓.
Did: TDD (2 tests RED: a currency switcher both ways, built in companyfacts' shape): the currency is now decided
filing by filing, a filing with no more USD facts than another currency's left out whole, so a filer that switched
to dollars keeps its dollar filings and one that left them keeps its dollar years and says why they stop. BUG-065's
and BUG-071's currency tests stay green (a tie counts against dollars). Six mutants each fail a test. No cached
filer has a filing in another currency: 0 of 143 records change. Code review 8 reported as this work finished
(`qa/evidence/review8-report.md`): it proposes a P1 (C1: the migration that flags pre-fix bars as dividend-adjusted
commits the column before the flags, so a first collection that fails or is interrupted leaves BUG-075's prices for
good) and a P2 (I1: dividends stored by a run whose bars failed are undone from older bars).
Evidence: `qa/evidence/iter58-bug077-{red,green,mutants,impact}.txt`, `qa/evidence/review8-report.md`
Result: BUG-077 VERIFIED; GATHER-02 → TODO (no open bug; its untested cases remain).
Gates: ruff ✓ mypy ✓ pytest 1026 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): a filer's reporting currency is decided filing by filing
Next: record code review 8 (verify each claim), then its C1 (proposed P1) at once. Until C1 is fixed, only the
backfill (which never runs the migration) is safe on the user's database; a collection or quote refresh isn't.

## Iteration 59: 2026-10-20 11:01: review
Picked: recording code review 8 (0ab5c81..fea2b70), which reported during iteration 58 with a proposed P1 (section
4.6: valid findings become bugs; each claim re-run first). CI: 0b061c0 running at the start.
Did: read each of the reviewer's scripts (copies, scratch folders and loopback only), then re-ran every claim on
today's code. All hold: C1 (a pre-flag copy of the user's data whose first export fails, by a lock or Ctrl+C, keeps
the new column and loses all 1,246,064 flags, so 98 multiples rows return to BUG-075's figures and the oracle sees
only its known 8), I1 (October's dividends stored without bars lift KO's, JPM's and XOM's FY-end prices 0.4-0.7%;
the oracle agrees), M1 (the as-of API returns AMD's flipped FY2021 cash flows), M2 (BRK-B FY2009, on the reviewer's
archive since today's parser dropped the helper its script calls), M3 (the `ex >= day` mutant passes all 1026
tests), M4 (the guard probe's 5 tests pass), M5 (the oracle on a '#' path leaves a file `or`), M6 (`//srv/...` an
invalid authority) and M7 (README.md:590). The review's strengths (BUG-061's 14 repairs among 50 candidates, the
rebuild exact on the user's own August bars, every bar route answering 200 on a flagged copy) are its own.
Evidence: `qa/evidence/review8-report.md`, `qa/evidence/iter59-review8-rerun.txt`
Result: new BUG-083 (P1, C1), BUG-084 (P2, I1), BUG-085 to BUG-091 (P3: M1-M7). VAL-05 → FAIL→BUG-084; STORE-02 →
FAIL→BUG-083.
Gates: ruff ✓ mypy ✓ pytest 1026 passed, 1 skipped (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): record code review 8; a non-atomic flag migration and dividends without bars
Next: BUG-083 (P1) at once: until it's fixed, only the backfill is safe on the user's database. Then BUG-078,
BUG-082, BUG-084 (P2). Fixes since review 8's range: 3 (2bb2a24, 80ea6d9, 0b061c0).

## Iteration 60: 2026-10-20 11:14: fix
Picked: BUG-083, the only open P1. CI: 7648ec5 ✓.
Did: TDD (2 tests RED: a first export that fails while writing, by a lock or Ctrl+C, kept the column and lost
every flag): the migration adds the column and flags the bars in one `BEGIN IMMEDIATE` transaction, committed
before the run's writes and rolled back whole on any failure. A mutant (the `ALTER` outside the transaction)
survived the first tests, since they fail after the migration, so a third test interrupts the migration itself as
the flags are written, RED on 7648ec5's migration. On pre-flag copies of the user's data: the review's abort
scenario keeps every flag, the revalued copy matches the fixed one, and hard kills of the migration leave a whole
copy every time, where the unfixed code killed at the same moments leaves the column with every flag empty.
Evidence: `qa/evidence/iter60-bug083-{red,green,mutants,e2e}.txt`
Result: BUG-083 VERIFIED; STORE-02 → TODO (the migration's kills done; a kill mid-export untested). No P0 or P1 open.
Gates: ruff ✓ mypy ✓ pytest 1029 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(store): the dividend-adjusted flags arrive with their column, or neither does
Next: the P2s, oldest first: BUG-078, BUG-082, BUG-084. The user's database can take a collection again; a run whose
price history fails beside new dividends still inflates FY-end prices about 0.5% (BUG-084). Fixes since review 8's
range: 4 (2bb2a24, 80ea6d9, 0b061c0, this).

## Iteration 61: 2026-10-20 11:30: fix
Picked: BUG-078, the oldest open P2 (no P0 or P1 open). CI: 1d762ba ✓.
Did: the oracle first (2d9b9dc): Yahoo's series go back to the price's basis whatever the SEC record holds. On
code review 7's scenario valued by the unfixed code it flags the DDM (41.84 stored, 418.4 derived), and its output
on the real, golden and ten earlier scenario databases is unchanged. Then TDD (2 tests RED: no annual rows, and
rows without a filing date): the step back no longer needs a filing date. Three mutants each fail a test. The
scenario's DDM is now 418.38, through the backfill CLI, the valuation API and the VAL tab of a scratch server;
0 of 762 valuation rows move on the user's copy and 0 of 114 on the golden basket.
Evidence: `qa/evidence/iter61-bug078-{oracle,red,green,mutants,impact}.txt`
Result: BUG-078 VERIFIED; VAL-09 noted, still FAIL→BUG-050 (P3).
Gates: ruff ✓ mypy ✓ pytest 1031 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: 2d9b9dc chore(qa): oracle takes Yahoo's series back to the price's basis without a filing date; (this
commit) fix(valuation): Yahoo's series go back to the price's basis without a filing date
Next: BUG-082 (P2), then BUG-084 (P2). This is the fifth fix since review 8's range (2bb2a24, 80ea6d9, 0b061c0,
1d762ba, this), so code review 9 (fea2b70..this commit) runs in the background; record it when it reports.

## Iteration 62: 2026-10-20 12:26: fix
Picked: BUG-082, the oldest open P2 (no P0 or P1 open). CI: f0690bc ✓.
Did: a probe first: the filings show their own split restatements (a count two filings give for one period at a
simple ratio), 37 of them Yahoo's splits on the cached filers. TDD (four tests RED, on AMZN's own figures: Q4 2020
-25.63, Q2 2021 -14.24): each quarter's per-share figures and counts go onto the basis of its year's annual figures
before Q4 is derived, and a trailing year onto its latest quarter's. The impact run showed TMUS's MetroPCS quarters
moved by its reverse merger, so the count tolerance went from 2% to 0.5% (a test RED first), which keeps every real
split. Eleven mutants: ten fail a test, and the survivor is equivalent in practice. Over 126 cached filers collected
offline, 28 change and no annual figure does; quarterly counts more than 2x off their year's 214 -> 21. Code review 9
reported meanwhile (`qa/evidence/review9-report.md`): a proposed P1 (C1: a quote refresh after a split values the
models on the old basis against the new price), two P2s and P3s, all unverified yet.
Evidence: `qa/evidence/iter62-bug082-{probe,red,green,mutants,impact}.txt`, `qa/evidence/review9-report.md`
Result: BUG-082 VERIFIED; new BUG-092 (P2: a year's counts filed at different scales); VALID-06 → FAIL→BUG-092.
Gates: ruff ✓ mypy ✓ pytest 1037 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): a year's quarters are on the split basis its year is filed on
Next: record code review 9 (re-run each claim first), then its C1 at once if it holds as a P1. Fixes since review
9's range: 1 (this).

## Iteration 63: 2026-10-20 12:39: review
Picked: recording code review 9 (fea2b70..f0690bc), which reported during iteration 62 with a proposed P1 (section
4.6: valid findings become bugs; each claim re-run first). CI: 51091ce running at the start.
Did: read each of the reviewer's scripts (copies, scratch folders, the network blocked), then re-ran every claim on
51091ce. All hold: C1 (after a 10:1 split, a quote refresh shows +420.3% and "cheap" where a full collection gives
-48.0%, and a backfill doesn't help), I1 (with Yahoo's `info` failing, the stored split takes back dividends never
refetched: DDM 4,183.77 against 418.38, the multiples band 9,833.66 with the bars failing too, 0 errors and 0
warnings), I2 (COST and PEP: no Q4, no TTM revenue), M1 (= BUG-092), M2 (ADI's share-only quarter costs two TTM
rows; a regression of 2bb2a24), M3 (the currency gone from the explanation; synthetic; a regression of 0b061c0), M4
(1 of 3 tries), M5 (the nine survivors survive the whole suite on 51091ce too) and M6 (three figures iteration 59's
records took from review 8 unchecked; re-run now, all three hold).
Evidence: `qa/evidence/review9-report.md`, `qa/evidence/iter63-review9-rerun.txt`
Result: new BUG-093 (P1, C1), BUG-094 (P2, I1), BUG-095 (P2, I2), BUG-096 to BUG-098 (P3: M2-M4); M5 added to
BUG-081; M6's figures re-run into BUG-084, BUG-085 and BUG-087; lead L22. GATHER-13 → FAIL→BUG-093 (it was PASS),
GATHER-06 → FAIL→BUG-094, GATHER-02 → FAIL→BUG-097, STORE-01 → FAIL→BUG-098.
Gates: ruff ✓ mypy ✓ pytest 1037 passed, 1 skipped (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): record code review 9; a quote refresh after a split, and dividends a failed quote
never refetched
Next: BUG-093 (P1) at once. Until it's fixed: after a company splits, collect it in full rather than refreshing its
quote. Then the P2s, oldest first: BUG-084, BUG-092, BUG-094, BUG-095. Fixes since review 9's range: 1 (51091ce).

## Iteration 64: 2026-10-20 13:04: fix
Picked: BUG-093, the only open P1. CI: 440d5e7 ✓.
Did: TDD (two tests RED: a refresh after a split since the last collection saved the post-split price; and the quote
had no split history): the quote fetch reads Yahoo's split history, and the quote job saves nothing when Yahoo shows
a split missing from `split_events` and dated on or after the ticker's last collection with Yahoo, telling the user
to collect the ticker in full. A first version took any collection as the last one; the user's copy has 50
SEC-only runs, which bring no split history, so it reads the last run with Yahoo (else the latest bar). Five
controls and seven mutants, each failing a test. End to end through the app's refresh route on review 9's fake
market: the refresh after the split is refused and the page keeps -47.8%, "expensive", where it showed +420.3%,
"cheap"; nothing in any table changes; after a full collection a refresh saves one market snapshot, as before.
Evidence: `qa/evidence/iter64-bug093-{red,green,mutants,e2e}.txt`
Result: BUG-093 VERIFIED; GATHER-13 → PASS. No P0 or P1 open.
Gates: ruff ✓ mypy ✓ pytest 1045 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(web): a quote refresh after a split since the last collection is refused, not saved
Next: the P2s, oldest first: BUG-084, BUG-092, BUG-094, BUG-095. Fixes since review 9's range: 2 (51091ce, this).

## Iteration 65: 2026-10-20 13:26: fix
Picked: BUG-084, the oldest open P2 (no P0 or P1 open). CI: 6ebfe38 ✓.
Did: the oracle first (27ce753): Yahoo's stored bars count as adjusted for the dividends up to the last adjusted bar,
which dates when they were served; on review 8's stale-dividend copy valued by the unfixed code it flags KO's and
XOM's multiples. Then TDD (three tests RED: the rebuild undid a dividend after the last bar, in a flagged database and
one from before the flag; a failed bar request left no warning): the factors stop at the last adjusted bar, and a
failed price-history request is a run warning. A boundary test pins a dividend on the last bar's own day; five
mutants, one surviving as equivalent in practice. The stale copies' multiples come back to the user's data's (76.94,
212.32, 76.06), nothing moves on the real (0 of 889) or golden (0 of 133) copies, and a run whose bar request fails
now says so. The real-app check also found BUG-094's family with only the bars failing (multiples 10x), added there.
Evidence: `qa/evidence/iter65-bug084-{oracle,red,green,mutants,impact}.txt`
Result: BUG-084 VERIFIED; VAL-05 → PASS.
Gates: ruff ✓ mypy ✓ pytest 1049 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: 27ce753 chore(qa): oracle undoes only the dividends Yahoo's stored bars were adjusted for; (this commit)
fix(valuation): the market-close rebuild undoes only the dividends Yahoo's bars were adjusted for
Next: the P2s, oldest first: BUG-092, BUG-094, BUG-095. Fixes since review 9's range: 3 (51091ce, 6ebfe38, this).

## Iteration 66: 2026-10-20 14:04: fix
Picked: BUG-092, the oldest open P2 (no P0 or P1 open). CI: 341f785 ✓.
Did: TDD (four tests RED on MCD's and COP's own figures: Q4 2021 -2,223.01M; a rung without EPS; the zeros): a
year's weighted-count rungs go onto the scale their own net income and EPS imply (share_scale's rule) before Q4, a
rung without a usable EPS onto its year's, and no count is at or below zero. The impact run caught the first version
rescaling TMO's correct 437.1M to 437.1 on a 10-Q whose EPS is tagged x10^6, so a correction now needs another of the
year's rungs near it (a test RED first); a surviving mutant got a test too. Six mutants, each failing a test. Over
126 cached filers: 10 change, no annual or per-share figure; counts at or below zero 9 -> 0, more than 2x off their
year's 16 -> 10, trailing counts more than 10% off 10 -> 4.
Evidence: `qa/evidence/iter66-bug092-{red,green,mutants,impact}.txt`
Result: BUG-092 VERIFIED; VALID-06 → FAIL→BUG-095.
Gates: ruff ✓ mypy ✓ pytest 1055 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): a year's share counts are on one scale before its fourth quarter
Next: BUG-094, BUG-095 (P2). Fixes since review 9's range: 4 (51091ce, 6ebfe38, 341f785, this); one more and code
review 10 is due.

## Iteration 67: 2026-10-20 14:30: fix
Picked: BUG-094, the older open P2 (no P0 or P1 open). CI: 51b90e0 ✓.
Did: the oracle first (db4820b): the stored bars count as served on the basis of the splits up to their last day,
a premise probed on real data first (every ticker's last bar 0-1 days before its last collection with Yahoo); on
review 9's scenarios valued by the unfixed code it flags the multiples. Then TDD (five tests RED): the dividends come
from the chart with the splits whatever the quote does, a run whose dividends fail stores no split, the bars take
the splits between their last day and the price's, and a failed quote or dividend history is a run warning; two
fixtures gained the day their bars were served. Six mutants, each failing a test. Review 9's four scenarios come
right through the real fetcher (DDM 418.38 where it read 4,183.77), each failure warned; nothing moves on the real
(0 of 762) or golden (0 of 114) copies.
Evidence: `qa/evidence/iter67-bug094-{oracle,red,green,mutants,impact}.txt`
Result: BUG-094 VERIFIED; GATHER-06 → TODO (no open bug; its other failure modes untested).
Gates: ruff ✓ mypy ✓ pytest 1060 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: db4820b chore(qa): oracle reads the stored bars' split basis from the day they reach; (this commit)
fix(valuation): Yahoo's stored series stay on one split basis when a run's requests fail
Next: this is the fifth fix since review 9's range (51091ce, 6ebfe38, 341f785, 51b90e0, this): code review 10
(f0690bc..this commit) runs in the background. Then BUG-095 (P2), the last open P2.

## Iteration 68: 2026-10-20 14:46: fix
Picked: BUG-095, the last open P2 (no P0 or P1 open; code review 10 running). CI: 8c34a86 ✓.
Did: TDD (two tests RED: a COST-like 12-12-12-16-week ladder derived no Q4, and no trailing year crossed its
112-day step): a quarter runs up to 120 days in the ladder and the trailing years, which takes a 17-week quarter
and leaves out two quarters (160 days or more). A guard test keeps a missing rung from making a two-quarter figure,
and the existing gap test, whose assertion could never fail, now pins the window over the gap. Four mutants, each
failing a test. Over 126 cached filers only COST and PEP change: 17 and 18 fourth quarters, 64 and 68 trailing years
with net income, every FY2023-25 adding up.
Evidence: `qa/evidence/iter68-bug095-{red,green,mutants,impact}.txt`
Result: BUG-095 VERIFIED; VALID-06 → FAIL→BUG-096 (P3). No P0, P1 or P2 open.
Gates: ruff ✓ mypy ✓ pytest 1063 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): a 16-week fourth quarter is a quarter
Next: record code review 10 when it reports (fixes since review 9's range: 5, reviewed). With no P0-P2 open, a
discovery unit over the TODO rows, riskiest area first (section 4.2.3: VAL > VALID > MATCH > GATHER > ...).

## Iteration 69: 2026-10-20 15:09: discovery
Picked: VALID-01, -08 and -09, the canonical mapping, sector classification and derived fields (no P0-P2 open; VAL
has no TODO row left). CI: fe20fd6 ✓. Code review 10 is still running.
Did: ran the coverage report over the basket from a scratch copy of the golden cache, and probed the 126 cached
filers (read-only) and a copy of the user's database: every boundary SIC code, each stored class against its SIC,
the sector ratios in all 2,312 company-years, every derivation's fill against the reported values, and the revenue
each filer's tags give. The universal core resolves for every US-GAAP filer with data, the classes match SIC, and no
derivation overwrites or goes untagged. But revenue's tag order takes a part of AXP's and BLK's revenue (AXP's ASC
606 contract revenue, 57% of its total), a derived total liabilities counts the non-controlling interest (INTC
FY2025 +14%), SCHW gets no efficiency ratio, and three asset managers and UNH are valued as banks and an insurer.
Evidence: `qa/evidence/iter69-{coverage,coverage-stderr,sectors,bankfields,sector_rest,derived,revenue}.txt`
Result: VALID-01 → FAIL→BUG-099, VALID-08 → FAIL→BUG-101, VALID-09 → FAIL→BUG-100. New: BUG-099 and BUG-100
(P2), BUG-101 (P3), Q9 (NEEDS-USER).
Gates: ruff ✓ mypy ✓ pytest 1063 passed, 1 skipped (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): discovery VALID-01, -08, -09; AXP's revenue is a part, derived liabilities hold the NCI
Next: BUG-099 (P2), unless code review 10 reports first; then BUG-100.

## Iteration 70: 2026-10-20 15:43: fix
Picked: BUG-099, the older of the two open P2s (no P0 or P1 open). CI: 143cfba ✓. Code review 10 reported during the
iteration (C1 a proposed P1); it is recorded next, its claims re-run first.
Did: rule probes over the 126 cached filers' 2,132 filer-years before any code: the largest figure, filing support and
the latest filing's tags each broke right figures (MA's net revenue, PM's net of excise taxes, restatements), and the
year's own income statement told every case apart. TDD (8 tests RED, 6 guards): the totals first in revenue's tags;
of two figures 25% or more apart, one the income statement doesn't add up to gives way to one it does, else a zero or
a figure below cost of revenue to one that isn't; a figure set aside ranks last (a filing with no other keeps it) in
the annual record, the vintages and the quarterly ladder; `noninterest_income` drops the net-revenue tag. The fix
found the same defect at PG FY2012-14 and ETN FY2011-16 (negative gross profit and Q4s). 14 mutants, each failing a
test; the first digest caught two regressions (vintages losing their only revenue, a total 1% off set aside), put
right by the pairwise rule and the ranking. Real app: stock-web on an offline-collected scratch database.
Evidence: `qa/evidence/iter70-bug099-{red,green,mutants,rules,impact,app}.txt`; code review 10's report,
`qa/evidence/review10-report.md`
Result: BUG-099 VERIFIED; VALID-01 PASS. Leads L23 (19 negative quarterly revenues, mostly a restated rung less one
as first filed) and L24 (`qa/oracle/sec_oracle.py` was never written).
Gates: ruff ✓ mypy ✓ pytest 1077 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(parsers): a year's revenue is its total, not a part of it
Next: record code review 10 (C1, a proposed P1, then I1 and I2, P2s), re-running each claim first; then the
highest-severity open bug (C1 if confirmed, else BUG-100). Fixes since review 10's range: 2.

## Iteration 71: 2026-10-20 15:55: review
Picked: recording code review 10 (f0690bc..8c34a86), which reported during iteration 70 with a proposed P1 (section
4.6: valid findings become bugs; each claim re-run first). CI: 679a35d running at the start.
Did: read the reviewer's scripts and the code each finding names, then re-ran every claim on an archive of 679a35d
with the review's own scripts (copies of the user's data made with the backup API, the network blocked). Since
8c34a86 only the parser, the TTM step and the canonical mapping changed. All hold: C1 (after a collection whose
quote failed, or all of whose Yahoo requests did, a refresh shows +411.2% and "cheap" where a backfill gives -48.6%;
in the second case the backfill stays wrong too), I1 (only the dividend request failing: Graham 634.76 and Lynch
346.50 against 63.48 and 34.65, +221.8% cheap), I2 (KO's rebuilt FY-end prices 12.80% to 0.66% low, the band 2.48%),
M1 (16 rungs at 4 filers; GOOGL's counts 664.7M, 13,242M, 658.8M, 13,078M), M2 (SCCO's, HON's and SPGI's refreshes
refused as splits), M3, M4 (BX's and CEG's dropped counts stay after a re-collection), M5, M6 (see BUG-081) and M7.
Evidence: `qa/evidence/review10-report.md`, `qa/evidence/iter71-review10-rerun.txt`
Result: new BUG-102 (P1, C1), BUG-103 and BUG-104 (P2, I1 and I2), BUG-106 (P2, M2), BUG-105, BUG-107, BUG-108 and
BUG-109 (P3: M1, M3, M5, M7); M4 added to BUG-029 and M6 to BUG-081; lead L25 (pandas 2.1). GATHER-13 → FAIL→BUG-102
(it was PASS), GATHER-06 → FAIL→BUG-103, VAL-05 → FAIL→BUG-104 (it was PASS); VALID-06's notes take BUG-105.
Gates: ruff ✓ mypy ✓ pytest 1077 passed, 1 skipped (1 known warning); no code changed | CI: pending for this push
Commit: (this commit) chore(qa): record code review 10; a refresh after a failed-quote collection, and splits a
failed dividend request drops
Next: BUG-102 (P1) at once. Until it's fixed: after a company splits, if a collection warns "quote unavailable",
collect it in full again rather than refreshing its quote. Then the P2s, oldest first: BUG-100, BUG-103, BUG-104,
BUG-106. Fixes since review 10's range: 2.

## Iteration 72: 2026-10-20 16:09: fix
Picked: BUG-102, the only open P1. CI: 4fc00ca ✓.
Did: TDD (three tests RED: a split a later collection stored while its quote failed, and one it never stored because
all its Yahoo requests failed, after the stored price and on its day): the refusal now reads the valuations' basis
as the engine does, from the latest snapshot's price, so a split after that price's day is refused stored or not,
and a missing one from that day or the last collection with Yahoo, whichever is earlier. Four guards, BUG-093's seven
cases unchanged, seven mutants each failing a test. The message drops "since the last full collection". Real app:
code review 10's three scenarios through the app's own route are refused, the page stays at -47.8%; iteration 64's
end-to-end check still refuses after the split and saves after a full collection.
Evidence: `qa/evidence/iter72-bug102-{red,green,mutants,e2e}.txt`
Result: BUG-102 VERIFIED; GATHER-13 → FAIL→BUG-106 (P2, the stock-dividend refusal, left). No P0 or P1 open.
Gates: ruff ✓ mypy ✓ pytest 1084 passed, 1 skipped (1 known warning) | CI: pending for this push
Commit: (this commit) fix(web): a refresh after a split the stored price predates is refused
Next: the P2s, oldest first: BUG-100, BUG-103, BUG-104, BUG-106. Fixes since review 10's range: 3.
