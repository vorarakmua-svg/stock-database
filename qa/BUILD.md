# Build board: the GuruFocus roadmap, milestones 0-5

- Plan: `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` (approved 2026-10-21)
- Protocol: `qa/BUILD_LOOP_PROMPT.md`
- Status: TODO | DOING | DONE | NEEDS-USER | BLOCKED (why)
- A milestone without a design spec starts with its design task. That task writes the spec and
  replaces its own row with the milestone's task rows.

**Counts:** TODO 4 · DOING 1 · DONE 9 · NEEDS-USER 0 · BLOCKED 0

## M0: the levered-FCF double count

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M0-1 | `_calculate_levered_fcf` stops subtracting interest again; `load_inputs` reads `free_cash_flow`; RED tests, mutants, spec/README/oracle updated; BUG logged and verified; a DCF and verdict diff on a DB copy | DONE | 59e0678 (oracle), this commit (fix) | `qa/evidence/levfcf-*.txt` |

## M1: valuation history and a historical fair value (spec: `docs/superpowers/specs/2026-10-21-valuation-history-design.md`)

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M1-1 | `src/valuation/history_inputs.py`: share series normalization, price factors, known dates, field choice, `load_history_inputs`; with tests | DONE | this commit | `qa/evidence/valhist-m1-1-*.txt` |
| M1-2 | `src/valuation/history.py`: multiples, window statistics, implied prices, the fair-value line, labels, `compute_history`; with tests | DONE | this commit | `qa/evidence/valhist-m1-2-*.txt` |
| M1-3 | Storage (`valuation_history`, `valuation_ranges`, `export_valuation_history`) and the engine hook in `compute_and_store`, covering backfill; with tests | DONE | this commit | `qa/evidence/valhist-m1-3-*.txt` |
| M1-4 | Real-data validation: `qa/oracle/history_vs_yahoo.py` on a backfilled DB copy; the spec's gates; split continuity; jumps traced; misses explained | DONE | this commit | `qa/evidence/valhist-m1-4-*.txt` |
| M1-5 | Repository readers and `GET /api/stocks/{t}/valuation-history`; with tests | DONE | this commit | `qa/evidence/valhist-m1-5-*.txt` |
| M1-6 | The VAL tab section (fragment, `renderValHistory`), checked in Chrome on a scratch server; with tests | DONE | this commit | `qa/evidence/valhist-m1-6-*` |
| M1-7 | Screener columns (`*_pct_10y`, `price_to_hist_value`), filters, CSV; with tests | DONE | this commit | `qa/evidence/valhist-m1-7-*.txt` |
| M1-8 | Docs: README "Valuation history", USAGE_GUIDE, the stale "no price series" text; the multiples-band basis bug logged (NEEDS-USER) | DONE | this commit | README, USAGE_GUIDE; `qa/BUGS.md` BUG-172 |
| M1-9 | Milestone close: code review 15 (opus, 16f3005..HEAD) with Critical and Important findings fixed; then backup and offline backfill of the user's DB; verdict changes reported | DOING (code review 15 running) | | `qa/evidence/m1-9-dryrun-*.txt` |

## M2: quality scores and ranks

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M2-0 | Design M2 (plan section "Milestones 2-5"): spec in `docs/superpowers/specs/`, then the task rows here | TODO | | |

## M3: SEC ownership (Form 4 insiders, 13F gurus)

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M3-0 | Design M3: spec, then the task rows | TODO | | |

## M4: more valuation tools and market pages

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M4-0 | Design M4: spec, then the task rows | TODO | | |

## M5: a wider universe and automation

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M5-0 | Design M5: spec, then the task rows | TODO | | |

## NEEDS-USER (each also recorded in `qa/BUGS.md`)

- Q10 [OPS]: a draft PR so CI runs on the branch. Default: no PR; local gates and ruff's py39 target.
- BUG-172 [VAL]: the multiples band's basis and outliers (GE's bull case $8,498.58); fixing it moves the median. Default: unchanged.

## Log

### Build iteration 0: 2026-10-21: setup
Did: the approved plan copied into the repo; the M1 spec written from the design pass; this board and
`qa/BUILD_LOOP_PROMPT.md` created; the Ralph loop started.
Next: M0-1.

### Build iteration 1: 2026-10-21 14:20: M0-1
Did:
- RED tests: `levered_fcf` 180 where 200 is expected, 230 where 250 is; the basis read 70 where 80 is.
- The oracle was put right first (59e0678), reading `free_cash_flow` as the spec says; it then flagged 54 DCF rows.
- The fix: `_calculate_levered_fcf` returns FCF, and `load_inputs` reads `free_cash_flow`; the spec is updated.
- Real data on two backfilled copies: 66 DCF rows change (28 by 10% or more), two verdicts move to fair
  (MCD, PFE), and the oracle agrees (level 1 0/522, level 2 8/487, all Graham bear cases, BUG-007).
- BUG-169 opened as P1 and verified. Q10 opened: no PR is open, so CI doesn't run on the branch.
- The BUGS header's NEEDS-USER count had stayed 11 after Q6's decision; with Q10 it is 11 again, now right.
Evidence: `qa/evidence/levfcf-{red,green,oracle-before,oracle-flags,oracle-after,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1195 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: 59e0678 test(qa): the valuation oracle reads the DCF basis as OCF - capex, as the spec says; (this commit) fix(parsers): levered FCF stops taking off interest that operating cash flow already bears
Next: M1-1.

### Build iteration 2: 2026-10-21 15:05: M1-1
Did:
- `src/valuation/history_inputs.py`: `load_history_inputs` reads the month-end closes, each TTM row's
  fundamentals through `CalculatedMetrics` (sector suppression applied; FCF only with capex), the day each
  became known, and one share field per quarter on the bars' split basis.
- The bars-to-price factor moved out of `load_inputs` into `inputs.bars_to_price_factor` (behaviour unchanged).
- TDD: 17 tests (RED: the module missing), and 15 mutants, each killed.
- Real data (a backup-API copy, read-only): 122 of 127 load; N/A for 3 without quarterly share counts
  (BRK-B, V, one more), BX by the Yahoo guard (1.49x, Q7) and SPCX (no TTM). The latest count is within
  5% of Yahoo's for 117 of 123; 4 splits applied (APH and CRWD after their latest quarter, BKNG, GOOGL);
  C's latest trailing year is stale (2025-12-31).
- Leads for M1-4: TSLA's quarterly weighted basic count (3.24B) lags its period-end 3.95B, about 18% low at
  the current point; the oldest quarters (2009-2013) of PG, LLY, GE and HON lack the chosen share field, so
  their history starts in 2011-2013.
Evidence: `qa/evidence/valhist-m1-1-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1212 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(valuation): history inputs put every quarter's shares on the price bars' basis
Next: M1-2.

### Build iteration 3: 2026-10-21 16:05: M1-2
Did:
- `src/valuation/history.py`: each month's multiples on totals (P/E, P/S, P/B, P/FCF, EV/EBITDA, P/FFO,
  dividend yield), the trailing 10-year window statistics (36 months needed), and the historical value.
  The value is the median across the sector's metrics of the trailing-median multiple applied to that
  month's trailing year; low and high use the quartiles. The labels use the approved cutoffs.
- TDD: 24 tests (RED: the module missing); 16 mutants, each killed. The first run left "full-window
  medians" alive, as a one-month change can't move a median; the look-ahead test now doubles the price for
  most of the series.
- A preview on the real-data copy (read-only), 6.7 s for 127 tickers:
  - The historical value is set for 119.
  - Labels: far above 40, above 24, near 27, below 17, far below 11.
  - Current multiples against Yahoo's: P/E 100/119 within 5% and 110 within 10%; P/S 109/121 and 115;
    P/B 101/113 and 107. All three pass M1-4's gates already.
  - Misses beyond 20%: COF P/S, JNJ P/E (29.7 vs 39.4) and TSLA P/S and P/B (the share-count lag noted in
    M1-1). These are for M1-4.
Evidence: `qa/evidence/valhist-m1-2-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1236 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(valuation): the valuation history's multiples, 10-year ranges and historical value
Next: M1-3.

### Build iteration 4: 2026-10-21 16:45: M1-3
Did:
- New tables `valuation_history` (one row per month) and `valuation_ranges` (each metric's window at the
  latest month, and the historical value). `SQLiteStore.export_valuation_history` replaces a ticker's
  rows in one transaction.
- `compute_and_store` computes the history after the valuations, in its own try/except, so CLI
  collection, web jobs and the backfill all write it. A failure is logged and leaves the valuations
  stored. `MEDIAN_MODELS` and the six rows are unchanged.
- The ranges column is `current_value`, since CURRENT is an SQL keyword; the spec now says so.
- The real-data copy showed 34,800 stored months without a market cap (bars back to 1962). The series
  now starts at the first market cap, or the latest 10-year window if that is earlier (RED test first).
- TDD: 8 store and engine tests and 1 trim test (RED: no such tables, no hook); 7 mutants, each killed.
- Real data: a fresh backup-API copy backfilled in 6.4 s for 127 tickers.
  - Valuations and summaries are identical to M0's backfilled copy.
  - 23,287 monthly rows for 122 tickers: 22,764 with a market cap, 17,667 with a historical value.
  - 1,016 ranges rows; the historical value is set for 119.
Evidence: `qa/evidence/valhist-m1-3-{red,trim-red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1245 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(valuation): store the valuation history and compute it with the valuations
Next: M1-4.

### Build iteration 5: 2026-10-21 18:10: M1-4
Did:
- `qa/oracle/history_vs_yahoo.py` (imports nothing from `src`) checks the spec's gates on a backfilled
  backup-API copy. All pass:
  - P/E within 10% of Yahoo's for 110/120, P/S 115/122, P/B 108/114; EV/EBITDA within 20% for 72/87.
  - Share counts within 5% for 118/122; trailing years at fiscal year ends against `metrics_annual`
    within 1% for 4,196/4,245.
  - Market cap across all 10 share splits within 5% (0.989 to 1.001).
  - The valuations and summaries are identical to the M0 copy's.
- Its first run found 9 monthly market-cap moves it couldn't trace; three fixes in the history inputs,
  each RED first (6 mutants, each killed):
  - PRU (8 moves): share-field coverage now counts the latest 40 quarters, and a count from EPS under $0.10
    is dropped as rounding noise.
  - UBER (1 move): quarters ending before the first price bar (pre-IPO counts) are left out.
  - TMUS, CAT, CVX and ORCL: a quarter lacking the long-term debt the 10-Ks report has no net debt, so no EV
    and no EV/EBITDA, rather than one short by up to $122B.
  After them: 78 moves, each traced to a filed share-count change or a spin-off factor.
- The 12 remaining misses are each explained in `qa/evidence/valhist-m1-4-explained.txt`:
  - EBITDA normalized by Yahoo: INTC, MRK, PFE, MO, GEV.
  - Definitions: COF's revenue, WELL as a REIT.
  - The share-count lag: TSLA.
  - JNJ: ours matches its filings.
  - ABBV and DHR: our D&A lacks the intangible amortization they tag apart.
- Two upstream data bugs opened, for the QA queue: BUG-170 (D&A without intangible amortization) and
  BUG-171 (10-Q quarters without long-term debt).
- The restatement look-ahead, measured: revenue restated by more than 1% in 8% of years, net income in 4%.
Evidence: `qa/evidence/valhist-m1-4-{red,oracle-before,oracle,misses,mutants,explained}.txt`
Gates: ruff ✓ mypy ✓ pytest 1250 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(valuation): validate the valuation history against Yahoo's figures and fix what it found
Next: M1-5.

### Build iteration 6: 2026-10-21 18:40: M1-5
Did:
- `Reader.valuation_history` and `Reader.valuation_ranges` return [] before the tables exist.
- `GET /api/stocks/{t}/valuation-history` returns:
  - the historical value against the live quote: value, low, high, label, price/value, the metrics
    combined, and the cutoffs;
  - the ranges, in the fixed metric order;
  - the monthly series: closes, historical values and the seven multiples.
  An unknown ticker or ^GSPC gets 404; a ticker not computed yet gets "not computed yet".
- TDD: 6 tests (RED: 4 failing, the route missing); 6 mutants, each killed.
- Real data (the backfilled copy, through the test client): AAPL "far above" ($339.50 against $240.66),
  NVDA "below", JPM "above", O "below"; GEV, BRK-B, SPCX and BX give their reasons. About 14 ms and
  30-35 KB a request.
Evidence: `qa/evidence/valhist-m1-5-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1256 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(web): the valuation history's API route
Next: M1-6.

### Build iteration 7: 2026-10-21 19:30: M1-6
Did:
- The VAL tab loads a "Historical Value" section (`/ui/stocks/{t}/val-history`, `fragments/val_history.html`)
  with `hx-trigger="load"`. It holds:
  - the label pill against the live price, the value with its range, and price / value;
  - the price against the historical value, with bands at the cutoffs;
  - a picker of the applicable multiples, each drawn against its 10-year low, median and high;
  - the table of each multiple's range (shorter windows labelled "since YYYY-MM (n months)");
  - the method note.
- `renderValHistory` in `static/app.js` uses the SLATE helpers and a 10-year default range. The inline
  config escapes "</".
- TDD: 8 tests (RED: 7 failing, the route missing); 6 mutants, each killed; `node --check` passes.
- In the browser: the Claude in Chrome extension isn't connected, so the installed Chrome ran headless on a
  scratch server over the backfilled copy (collection and quote refresh off, a scratch profile). Screenshots
  of AAPL ("far above") and GE ("near") look right. AAPL, JPM, O, GEV and NVDA render with no console
  errors, and GEV shows "Not available" with its reason. The server is stopped and port 8765 freed.
Evidence: `qa/evidence/valhist-m1-6-{red,mutants}.txt`, `valhist-m1-6-AAPL-val.png`, `valhist-m1-6-GE-val.png`
Gates: ruff ✓ mypy ✓ pytest 1264 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(web): the VAL tab's valuation history section and chart
Next: M1-7.

### Build iteration 8: 2026-10-21 20:05: M1-7
Did:
- The screener's `HISTORY_EXPRS` fields: `pe_pct_10y` through `div_yield_pct_10y`, each multiple's percentile
  in its 10-year window, and `price_to_hist_value`, the live price over the historical value.
  - They read fixed-literal LEFT JOINs of `valuation_ranges`, applicable rows only, and are whitelisted
    like the other fields.
  - They are joined only when the table exists. A database from before the history (the user's, until
    M1-9's backfill) screens on with them NULL, where an unconditional join would have failed with "no such
    table".
  - Wired into `SCREEN_COLUMNS` (so the CSV), `METRIC_KINDS`, a "Historical value" group in the picker, and
    two sortable result columns.
- TDD: 9 tests (RED: the import missing); 6 mutants, each killed. The `applicable = 1` guard needed a test
  row that isn't applicable but holds a stray percentile.
- Real data (read-only):
  - The backfilled copy: 33 stocks have P/E in the bottom quarter of their 10 years (EQIX, GOOGL, MCD,
    AMZN...); 28 trade below 0.9x their historical value (ADBE 0.30, CRM 0.38, INTU 0.41...); 42 above
    1.3x; 120 of 126 have a price / value.
  - A fresh copy without the tables screens all 126, with the fields NULL.
Evidence: `qa/evidence/valhist-m1-7-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1273 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(web): screen by each multiple's 10-year percentile and by price / historical value
Next: M1-8.

### Build iteration 9: 2026-10-21 20:40: M1-8
Did:
- README: a "Valuation history" section under the valuation layer (method, no look-ahead and its
  restatement exception, ranges, the historical value and its labels, tables, VAL tab, API, screener,
  the check against Yahoo, limitations). Also:
  - a feature bullet;
  - the API row;
  - the new tables, with a screening SQL example;
  - the stale "no historical per-date price series is stored" now points at the history.
- USAGE_GUIDE: a "Valuation history" subsection (tables, two SQL examples, where it shows) and a
  contents entry. Prose and SQL only, as `test_docs_usage_guide` runs the Python blocks.
- `src/query/pit_metrics.py`: the docstring's stale "no price series" claim is put right.
- BUG-172 recorded as NEEDS-USER. GE's multiples band is 12.18x to 1,044x (bull $8,498.58): spin-adjusted
  closes over as-reported EPS, and one tiny-earnings year (FY2022, EPS $0.05) setting the max. A fix moves
  the median, so the choice is the user's.
- All three SQL examples run on the backfilled copy: 22 companies in the README's screen, 121 AAPL months,
  120 historical values.
Evidence: the docs test (2 passed); the SQL examples run read-only on the backfilled copy (this log)
Gates: ruff ✓ mypy ✓ pytest 1273 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) docs: the valuation history in the README and the usage guide
Next: M1-9 (milestone close: code review 15, then the user's DB backfill).

### Build iteration 10: 2026-10-21 21:05: M1-9 (started)
Did:
- Code review 15 dispatched: an opus reviewer, read-only, over 16f3005..a3c8a77. That is the QA fixes since
  review 14 (BUG-140, BUG-141, BUG-029, Q6, BLK), M0 and M1, about 2,600 lines in 33 files.
- The live step's dry run, on a fresh backup-API copy backfilled with HEAD (5.4 s):
  - Verdicts: fair 16 to 18 and expensive 108 to 106. MCD and PFE move from expensive to fair, from M0's
    fix. The median upside goes from -58.3% to -56.1%; the median value moves for 24 tickers.
  - Every oracle gate passes, and all 78 monthly moves are traced.
- Waiting on the review. Its Critical and Important findings are fixed before the live backfill.
Evidence: `qa/evidence/m1-9-dryrun-{diff,oracle}.txt`
Gates: unchanged since a3c8a77 (no code change)
Commit: (this commit) chore(qa): build iteration 10, code review 15 dispatched and the backfill's dry run
Next: M1-9 (the review's findings, then the live backfill). While the review runs: M2-0.
