# Build board: the GuruFocus roadmap, milestones 0-5

- Plan: `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` (approved 2026-10-21)
- Protocol: `qa/BUILD_LOOP_PROMPT.md`
- Status: TODO | DOING | DONE | NEEDS-USER | BLOCKED (why)
- A milestone without a design spec starts with its design task. That task writes the spec and
  replaces its own row with the milestone's task rows.

**Counts:** TODO 12 · DOING 0 · DONE 19 · NEEDS-USER 0 · BLOCKED 0

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
| M1-9 | Milestone close: code review 15 (opus, 16f3005..HEAD) with Critical and Important findings fixed; then backup and offline backfill of the user's DB; verdict changes reported | DONE | 7189bf7, 61181e7, 0ffde44, 5efe18e, e226b31, this commit (live backfill) | `qa/evidence/m1-9-*.txt`, `qa/evidence/review15-*` |

## M2: quality scores and ranks

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M2-0 | Design M2: `docs/superpowers/specs/2026-10-21-quality-scores-design.md`, then these rows | DONE | this commit | the spec |
| M2-1 | `src/scores/fundamentals.py`: per-share series on today's split basis, 3/5/10-year CAGRs; with tests | DONE | this commit | `qa/evidence/scores-m2-1-*.txt` |
| M2-2 | `src/scores/scores.py`: Piotroski F (9 tests), Altman Z, Beneish M, Sloan, ROIC vs WACC, predictability stars; with tests | DONE | this commit | `qa/evidence/scores-m2-2-*.txt` |
| M2-3 | `src/scores/signs.py`: the 20 warning signs; with tests | DONE | this commit | `qa/evidence/scores-m2-3-*.txt` |
| M2-4 | Storage (`per_share_annual`, `quality_scores`, `warning_signs`), the engine hooks, `rank_universe` (ranks, composite, momentum, Magic Formula); with tests | DONE | this commit | `qa/evidence/scores-m2-4-*.txt` |
| M2-5 | Independent oracle `qa/oracle/scores_oracle.py` and real-data validation (distributions, severe signs traced) | DONE | a312d54 (BUG-185), this commit | `qa/evidence/scores-m2-5-*.txt`, `qa/evidence/bug185-*.txt` |
| M2-6 | `GET /api/stocks/{t}/scores` and the STAT tab's Quality section (radar, ranks, tests, growth, signs), browser-checked; with tests | DONE | this commit | `qa/evidence/scores-m2-6-*.txt` |
| M2-7 | Screener fields from `quality_scores` (table-exists switch) and the preset screens; with tests | DONE | this commit | `qa/evidence/scores-m2-7-*.txt` |
| M2-8 | Docs: README and USAGE_GUIDE | TODO | | |
| M2-9 | Milestone close: code review 16 (opus) with Critical and Important findings fixed; backup and offline backfill of the user's DB | TODO | | |

## M3: SEC ownership (Form 4 insiders, 13F gurus)

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M3-0 | Design M3: `docs/superpowers/specs/2026-10-22-sec-ownership-design.md`, then these rows | DONE | this commit | the spec |
| M3-1 | `src/ownership/form4.py`: the Form 4 parser (owners and roles, non-derivative and derivative rows, 10b5-1, amendments), with real-document fixtures; with tests | TODO | | |
| M3-2 | Form 4 collection (full submissions feeds back three years, cached XML, incremental), storage (`insider_filings`, `insider_trades`) and `stock-data ownership --insiders`; with tests | TODO | | |
| M3-3 | Insider aggregates, the INS tab, `GET /api/stocks/{t}/insiders`, screener fields; checked against Yahoo's insider rows, browser-checked; with tests | TODO | | |
| M3-4 | 13F: the guru list (`src/ownership/gurus.py`, each CIK checked), the information-table parser with its checksums, storage; with tests | TODO | | |
| M3-5 | The CUSIP map (curated for our universe, OpenFIGI for the rest) and quarter-over-quarter changes; with tests | TODO | | |
| M3-6 | Guru pages (`/gurus`, `/gurus/{cik}`, `/gurus/portfolio`), the HDS tab's Gurus section, the API, browser-checked; with tests | TODO | | |
| M3-7 | Docs: README and USAGE_GUIDE | TODO | | |
| M3-8 | Milestone close: code review 17 (opus) with Critical and Important findings fixed; backup, then the live ownership collection into the user's DB, checked against Yahoo and the 13F checksums | TODO | | |

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
- Q11 [VAL]: the quality composite's weights. Default: equal.

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
  - Market cap across all 10 share splits within 5% (0.989 to 1.001). Later found blind to seams left
    unfolded (BUG-177, build iteration 19).
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

### Build iteration 11: 2026-10-21 21:30: M2-0 (while code review 15 runs)
Did:
- M1-9 waits on code review 15, still running, so this iteration took the next task that doesn't depend
  on it.
- M2's design, `docs/superpowers/specs/2026-10-21-quality-scores-design.md`, gives every formula as
  published:
  - Piotroski F's nine tests; Altman Z, the original (Z'' waits for M5's SIC codes); Beneish M's eight
    variables; Sloan; ROIC vs WACC on the DCF's CAPM.
  - Predictability stars: our own formula, the R² of log-linear revenue/share and EBITDA/share trends over
    10 years, 1 star on any EBITDA loss or a falling trend.
  - 20 warning signs, five percentile ranks (within the sector when it has 10 peers), a composite with
    equal weights, and the Magic Formula.
  - Storage, the engine hooks, the API, the STAT section, the screener fields and 5 preset screens,
    validated by an independent oracle.
- Nine task rows (M2-1 to M2-9) replace M2-0. Q11 (the composite's weights) is recorded as NEEDS-USER,
  default equal.
Evidence: the spec
Gates: unchanged (docs only)
Commit: (this commit) docs: Milestone 2's design, quality scores and ranks
Next: M1-9 when the review reports; otherwise M2-1.

### Build iteration 12: 2026-10-21 22:10: M2-1 (code review 15 still running)
Did:
- `src/scores/fundamentals.py`: each fiscal year's figures, its per-share series on today's split basis
  (revenue, EPS, EBITDA, FCF, book) and their 3/5/10-year CAGRs. A CAGR needs both ends positive and its
  start exactly that many years back.
- `load_inputs(..., max_years=)` keeps 10 years by default, so the valuations stay identical (checked on a
  backfilled copy); the scores take 11.
- The real data showed GOOGL's revenue/share falling 34% a year. FY2020's count was the cover's 0.675B as
  filed, while its EPS was restated for the 2022 20:1 split. The scores now count each year on its EPS's
  basis: net income / EPS without a weighted count, if EPS is $0.10 or more. GOOGL now reads +19.9%.
  BUG-173 is opened for the same fallback in `load_inputs`.
- `src/scores` is added to mypy's files.
- TDD: 9 tests (RED: the package missing, then the GOOGL-like basis); 6 mutants, each killed.
- Real data: CAGRs for 121 tickers (revenue/share 5y, median 9.4%; 10y for 108). The only year-on-year
  moves beyond 3x are real (BX and KKR carry, MU's memory year, WDC's SanDisk separation).
Evidence: `qa/evidence/scores-m2-1-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1282 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(scores): per-share series on today's split basis and their CAGRs
Next: M1-9 when the review reports; otherwise M2-2.

### Build iteration 13: 2026-10-21 23:00: M2-2 (code review 15 still running)
Did:
- `src/scores/scores.py`, each score as the spec gives it:
  - Piotroski F: nine strict tests; a test without inputs isn't counted, so F is "out of N tested".
  - Altman Z (original) and its zone; Beneish M (SGAI and DEPI neutral when missing); Sloan.
  - WACC on the DCF's CAPM cost of equity, the debt cost clamped to [rf, rf + 10%], the tax 0-35% (21% when
    unknown). ROIC - WACC.
  - Predictability stars: `stars_for` rounds 1 + 4·min R² down to the half star; any loss year or a
    falling trend gives 1; banks and insurers use EPS.
  - NCAV per share, and the Magic Formula's earnings yield and Greenblatt return on capital.
  - Sector exclusions carry reasons.
- No `type: ignore`: Optional values are narrowed into locals.
- TDD: 20 tests (RED: the module missing); 12 mutants, each killed. "Round to the nearest" survived the
  noisy predictability case, so the star rule became `stars_for`, pinned at R² 0.70 (3.5 stars, not 4).
- Real data (read-only):
  - F peaks at 5-7 (22 N/A: banks and insurers).
  - Altman: 66 safe, 8 grey, 9 distress (BA among them).
  - Predictability: 26 at 4.5 stars, 36 at 1 star.
  - Beneish flags 5 hyper-growers or restructurers (NVDA, LRCX, MRVL, SNDK, WDC); Sloan beyond 25% for 4.
  - WACC median 9.2% (6.2-14.0%).
  - AAPL: F 8/9, Z 12.7, 4.5 stars.
Evidence: `qa/evidence/scores-m2-2-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1302 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(scores): Piotroski F, Altman Z, Beneish M, Sloan, ROIC vs WACC and predictability
Next: M1-9 when the review reports; otherwise M2-3.

### Build iteration 14: 2026-10-21 23:40: M2-3 (code review 15 still running, started about 20 minutes before)
Did:
- `src/scores/signs.py`: the spec's 20 warning signs. Each carries its severity, a message stating the
  figure, the figure and its threshold; severe signs come first. `HistoryContext` carries M1's price / value
  and P/E percentile.
- TDD: 24 tests (RED: the module missing). A healthy company fires none, and each sign fires alone on its own
  rule. 20 mutants, one per rule, each killed.
- Real data: 18 severe signs. Altman flagged every regulated utility as in distress (D 0.67, DUK 0.68,
  NEE 1.10, SO 0.97), a known misfit of the original model, so Altman Z is now N/A for utilities (RED test
  first; Z'' waits for M5's SIC codes). Telecoms stay flagged (T, VZ, TMUS). Beneish's 5 and Sloan's 4 are
  hyper-growers. Medium signs most often: goodwill over half of equity (43, the buyback-thinned equity of
  large caps), price far above value (42). 23 companies fire none.
Evidence: `qa/evidence/scores-m2-3-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1327 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(scores): twenty warning signs, and no Altman Z for utilities
Next: M1-9 when the review reports; otherwise M2-4.

### Build iteration 15: 2026-10-22 00:30: M2-4; code review 15 reported
Did:
- New tables `per_share_annual`, `quality_scores` (`QUALITY_COLUMNS`) and `warning_signs`.
  `export_quality_scores` replaces a ticker's rows in one transaction; `update_quality_ranks` writes the rank
  pass.
- `src/scores/engine.py` computes a ticker's row: scores, CAGRs, momentum, the rank components (JSON) and the
  signs. `src/scores/ranks.py` holds momentum, the component percentiles (within the sector at 10 peers), the
  ranks, the composite and the Magic Formula.
- `compute_and_store` stores the scores after each ticker's history, in its own try/except, then re-ranks
  every ticker after the loop. Valuations and the history are unchanged.
- TDD: 8 rank and 7 store tests (RED: the modules missing); 10 mutants, each killed. "One-component ranks
  need two" first survived: the requirement had been capped at the component count, so `REQUIRED` is now the
  single rule.
- Real data (a fresh backfilled copy): 6.2 s with the history and the scores. 127 score rows, 1,358
  per-share rows, 249 signs. Composite for 118 (median 56): SCHW 90, MU 86 and NVDA 84 lead; BA, SO and IBM
  trail. 49 companies eligible for the Magic Formula. Valuations identical to the earlier copies.
- Code review 15 came back while this ran. It is "with fixes": C1 (split seams fold only within 120 days of
  their split: NFLX's history is 10x off, PANW's 2x), I1 (mergers folded as splits: DUK, C), I2 (a price-less
  latest snapshot switches off the share guard), I3 (the oracle can't see seam defects; the README overclaims
  continuity), and six minors. M1-9 takes them next, before the live backfill.
Evidence: `qa/evidence/scores-m2-4-{red,mutants,realdata}.txt`; the review's report is in this session's
transcript, and its findings go to `qa/BUGS.md` in M1-9
Gates: ruff ✓ mypy ✓ pytest 1342 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(scores): store the quality scores, rank them across the universe, compute them with the valuations
Next: M1-9 (code review 15's findings: C1, I1, I2, I3 fixed test-first, the minors logged; then the live backfill).

### Build iteration 16: 2026-10-22 01:10: M1-9 (code review 15's C1)
Did:
- Recorded code review 15's findings: BUG-174 (P1, C1); BUG-175, BUG-176, BUG-177 and BUG-179 (P2); BUG-178
  and BUG-180 to BUG-183 (P3).
- Fixed BUG-174 (7189bf7). A split's seam now matches its declared split up to three years and 120 days
  later, the reach of later filings' restatements. Ties go to the earliest split, so APH's 2026 split stays
  for after its latest quarter.
- Tests: four (two RED); three mutants, each killed. "Unbounded" survived until the bound's test checked the
  latest quarter.
- Real data: only NFLX ($49.34 to $152.84) and PANW ($74.72 to $139.82) move by more than 2%. 41 declared
  splits fold (4 before).
- A gate miss:
  - 7189bf7 was committed and pushed with a ruff E501 (a test line of 121 characters) and without the full
    pytest run.
  - Cause: the commit sat on its own line after a heredoc, so it ran although the gate chain before it had
    failed, and the step recording BUG-174 never ran.
  - This commit wraps the line, runs every gate (green) and records BUG-174. Commits now take their message
    from a file, so the gates and the commit run as one `&&` chain.
Evidence: `qa/evidence/review15-c1-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1346 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: 7189bf7 fix(valuation): a split's seam years before it folds by the declared split (BUG-174); (this commit) chore(qa): wrap a test line, record BUG-174 verified
Next, still M1-9:
- BUG-175 (mergers folded as splits), BUG-176 (a price-less latest snapshot), BUG-177 (the oracle's seam gate
  and the README).
- Then the oracle and the dry run again, and the live backfill.

### Build iteration 17: 2026-10-21 22:05: M1-9 (code review 15's BUG-176)
Did:
- Fixed BUG-176: the valuation history reads the latest snapshot with a price, for the price, its date and
  Yahoo's implied count. A run whose quote failed leaves a later row without them, which switched off the
  share guard and the later-split check. With no priced snapshot at all, the history is N/A with a reason.
- Tests: two RED; two mutants, each killed. The README's limitation and the spec's guard say so.
- Real data: given a price-less latest row, HEAD's code drops APH's 2026-09-03 split and computes BX; the fix
  keeps both right. No ticker has a price-less latest row today, so all 127 fair values are unchanged.
Evidence: `qa/evidence/review15-bug176-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1348 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) fix(valuation): the history checks shares against the latest priced quote (BUG-176)
Next, still M1-9: BUG-175 (mergers folded as splits), BUG-177 (the oracle's seam gate and the README); then
the oracle and the dry run again, and the live backfill.

### Build iteration 18: 2026-10-21 22:22: M1-9 (code review 15's BUG-175)
Did:
- Fixed BUG-175. An undeclared share seam of 1.5x or more folds only on a split's marks: same-sign EPS that
  moved against the count at least halfway (log terms), and revenue that didn't move with it as far. The
  listing's quarter takes the public count, so the six IPO quarters the old fallback folded stay as they were.
- Tests: six (eight failing cases first); nine mutants, each killed. The spec and the README say so.
- Real data: four tickers change. DUK, Citigroup and TMUS only before 2013. HON's 174 months to 2026-04 double
  back to the filed counts: its 2026 reverse split hides in Yahoo's 0.9535 price factor. HON's 10-year P/E
  median goes from 12.0 to 23.8 and its historical value from $212.50 to $416.24, as its trailing year still
  holds the business before that day's distribution. Opened BUG-184 (P3): one HON month at half.
Evidence: `qa/evidence/review15-bug175-{scan,red,green,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1357 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) fix(valuation): an unexplained share jump folds only on a split's marks (BUG-175)
Next, still M1-9: BUG-177 (the oracle's seam gate and the README's continuity claim); then the oracle and the
dry run again, and the live backfill.

### Build iteration 19: 2026-10-21 22:32: M1-9 (code review 15's BUG-177; the dry run again)
Did:
- Fixed BUG-177 in `qa/oracle/history_vs_yahoo.py`. Gate 4 checks market cap where the history crosses
  each declared share split's seam in the filed quarters. The tracer flags a move at a nearby split's ratio
  as a seam left unfolded, and a new gate wants none. The README's continuity claim and the spec's gate now
  say what's checked.
- RED: on the copy backfilled before BUG-174's fix, both gates fail on NFLX (x10.001) and PANW (x2.010) alone.
- The live step's dry run again, on a fresh backup-API copy backfilled with HEAD's code (7 s). Every
  oracle gate passes: 40/40 seams, 80/80 moves traced. The verdicts move as before, fair 16 to 18 and
  expensive 108 to 106: MCD and PFE from expensive to fair (M0). Now all 127 tickers also get
  `quality_scores` (248 warning signs), which M2-9 recomputes.
- Review 15's Critical and Important findings are all fixed (BUG-174 to BUG-177).
Evidence: `qa/evidence/review15-bug177-{red,green}.txt`, `qa/evidence/m1-9-dryrun2-{diff,oracle}.txt`
Gates: ruff ✓ mypy ✓ pytest 1357 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) fix(qa): the history's oracle checks market cap at every split's seam (BUG-177)
Next, M1-9's live step: back up the user's DB, run the offline backfill on it, check it with the oracle and
report the verdict changes; record code review 15 in `qa/LOG.md`; then M1-9 is DONE.

### Build iteration 20: 2026-10-21 22:38: M1-9 (the live backfill; M0 and M1 closed)
Did:
- Backed up the user's DB to `%LOCALAPPDATA%\stockdb-backups\2026-10-21-e`: the backup API from a mode=ro
  connection, plus json/ and csv/. Integrity ok; every table's row count and all 131 files match.
- Ran `python -m src.valuation.backfill --db data/output/stock.db` (offline, 6 s). It stored valuations for 127
  tickers, the valuation history (23,310 months, 1,016 ranges), and M2's tables: 1,358 per-share rows, 127
  quality scores, 248 warning signs. M2-9 recomputes M2's tables.
- Checked: every oracle gate passes on the live DB (40/40 split seams, 80/80 moves traced). Every backfilled
  row equals the dry run's, timestamps aside.
- What changed for the user:
  - Two verdicts, from M0's levered-FCF fix: MCD and PFE from expensive to fair (fair 16 to 18, expensive
    108 to 106). The median upside goes from -58.3% to -56.1%, and the median value moved for 24 tickers.
  - New: each company's valuation history and historical value, in the VAL tab, the API and the screener.
- Code review 15 is recorded in `qa/LOG.md` as the QA loop's review 15 (iteration 103). Its Critical and
  Important findings are fixed (BUG-174 to BUG-177). Its six Minor findings stay open as bugs: BUG-178 to
  BUG-183, with BUG-179 at P2.
Evidence: `qa/evidence/m1-9-live-{backup,backfill,oracle,diff,vs-dryrun}.txt`, `qa/evidence/review15-report.md`
Gates: ruff ✓ mypy ✓ pytest 1357 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) chore(build): close M1, with the user's DB backed up and backfilled
Next: M2-5 (the scores oracle and real-data validation).

### Build iteration 21: 2026-10-21 22:46: M2-5 (started)
Did:
- Dispatched an opus worker to write `qa/oracle/scores_oracle.py` from the spec alone, importing nothing from
  `src`. It puts shares on today's split basis its own way (the latest vintage's count times the declared splits
  after its filing), so it also checks the code's seam folding. It gates every score, the CAGRs, predictability,
  the ranks and the composite, and reports the distributions.
- Judged the 14 severe warning signs on the backfilled copy (identical to the user's DB). None traces to a
  data error:
  - Altman distress: T, VZ and TMUS are capital-heavy telecoms (asset turnover 0.30-0.40), which the original
    model reads as distress, as it does utilities. BA (thin equity, weak EBIT) and ORCL (negative retained
    earnings, heavy liabilities) are genuine readings.
  - Beneish: hypergrowth (NVDA sales +65%, SNDK x2.75, MRVL's receivables) and WDC's non-cash gain.
  - Sloan: securities purchases in investing cash flow (PLTR, MU, NVDA) and WDC's gain.
Evidence: `qa/evidence/scores-m2-5-severe-judged.txt`
Gates: ruff ✓ mypy ✓ pytest 1357 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) chore(build): build iteration 21, the scores oracle's worker dispatched
Next: M2-6 while the oracle's worker runs; then M2-5's results (gates, disagreements, distributions).

### Build iteration 22: 2026-10-21 22:58: M2-6 (while M2-5's oracle worker runs)
Did:
- `src/webapp/quality.py` builds one view of the stored scores for the API and the STAT tab: the tests in the
  spec's order with labels, Altman's zone, Beneish's flag, Sloan's band, ROIC vs WACC, the stars, the five ranks
  and the composite, the growth table, the per-share rows, and the signs, severe first. N/A reasons come from
  `na_reasons`. Three reader methods return nothing before their tables exist.
- `GET /api/stocks/{t}/scores` (404 for an unknown ticker or ^GSPC; "not computed yet" before a row exists).
- The STAT tab loads a Quality section (`/ui/stocks/{t}/scores`): the composite, a Plotly radar of the ranks
  (a missing rank is a gap the polygon skips, not a zero), and the ranks, scores, tests, growth and signs tables.
- Tests: 16 (14 failing first); 98 web tests pass.
- Browser (headless Chrome, a scratch server on the backfilled copy, collection and refresh off): AAPL, JPM,
  NVDA and O render with no console errors. Fixed what the screenshots showed: a clipped radar label, and a bank's
  empty Magic Formula row now says banks aren't ranked. The server was stopped and port 8765 is free.
Evidence: `qa/evidence/scores-m2-6-{red,green}.txt`; screenshots in `%LOCALAPPDATA%\stockdb-qa\build\m0\shots\*-stat.png`
Gates: ruff ✓ mypy ✓ pytest 1373 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(web): the quality scores' API and the STAT tab's Quality section
Next: M2-5's results when its worker reports (the oracle is written; its run is pending); then M2-7.

### Build iteration 23: 2026-10-22 02:47: M2-7 (and M2-5's oracle reported)
Did:
- Screener: 17 fields of `quality_scores` and price / NCAV (none when NCAV isn't positive), joined only when the
  table exists; the Lynch model's upside beside the median's and owner earnings'; all in the CSV. The page lists
  them under "Quality scores" and "Valuation models"; the results add Composite and F-score columns.
- The spec's five preset screens fill the filters from a dropdown: Buffett-Munger, Peter Lynch, Graham net-net,
  Magic Formula (a rank of at least 1 leaves out banks, insurers, utilities and REITs) and Undervalued-predictable.
  On the backfilled copy: 13, 2, 0, 49 and 13 matches.
- Tests: 17 (the module failing to import first); three mutants. Two survived at first: the fixture's schema
  already holds an empty `quality_scores`, so the test now drops it; the Lynch test now counts AAA's rows. Then
  all three were killed.
- Browser (headless Chrome, scratch server, collection and refresh off): the Magic Formula preset's URL renders
  its chip and 49 ranked rows, with no console errors. Server stopped; port 8765 free.
- M2-5's worker reported (the rate limit had paused it): 5 of its 9 gates pass. Piotroski, Altman, Sloan, the
  ranks, the composite and the Magic Formula rank agree exactly. The misses: the annual share normalization
  (`inputs._normalize_splits`) folds share jumps that aren't splits (mergers, unit swings, listing jumps, spin
  factors) for 8 tickers. The oracle's own count is approximate where no weighted count is filed (GOOGL, XOM). The
  vintages hold 5 counts in thousands (COP, AMT). Beneish reads a missing long-term debt as zero.
Evidence: `qa/evidence/scores-m2-7-{red,green,mutants,presets}.txt`, `qa/evidence/scores-m2-5-oracle.txt`
Gates: ruff ✓ mypy ✓ pytest 1390 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(web): screen on the quality scores, with the five preset screens
Next: M2-5. Diagnose the annual path's folds (is it BUG-175 again, and does fixing it move a verdict?), refine
the oracle where its method is approximate, and settle the long-term-debt reading.

### Build iteration 24: 2026-10-22 02:57: M2-5 (BUG-185 found by the oracle, and fixed)
Did:
- The oracle's first run (5 of 9 gates) traced its misses to three causes:
  - The FY records' share normalization folded jumps that aren't splits (8 tickers): BUG-185, the annual twin
    of BUG-175.
  - Its own approximate counts where no weighted count is filed (GOOGL, XOM), and vintages in thousands (COP).
  - A reading: Beneish with a missing long-term debt.
- Prototyped BUG-175's rules on a scratch copy first: no median and no verdict moves. Then fixed it test-first
  (a312d54): only declared share splits are candidates; an undeclared jump folds on a split's marks when net
  income / EPS moved with it; years before the listing fold into the public count. Five tests, six mutants
  killed. On a copy, RTX's Lynch bull goes from $29.02 to $24.80; the scores of BX, DELL, LIN and RTX move.
- Decided: Beneish reads a missing long-term debt as zero (debt-free filers tag none); Piotroski's leverage test
  stays untested without it. The spec says so.
Evidence: `qa/evidence/bug185-{red,green,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1395 passed, 1 skipped | CI: no PR (Q10)
Commit: a312d54 fix(valuation): the fiscal years fold only a split's share jump, as the history does (BUG-185)
Next: M2-5's oracle refinements and its final run.

### Build iteration 25: 2026-10-22 03:06: M2-5 (DONE)
Did:
- The oracle's refinements, by its worker:
  - Each year's count comes from its latest filing: the diluted weighted count, else net income / diluted EPS,
    else shares outstanding. A count 100x from its reference is in other units (COP, AMT).
  - Beneish reads a missing long-term debt as zero.
  - Years before the first bar + 1 year are not compared (the listing fold is an approximation).
- Reviewed the oracle myself against the spec: every formula, the readings, the ranks' percentiles, the severe-sign
  traces. It imports nothing from `src`.
- Ran it on a copy backfilled with BUG-185's fix: all 9 gates pass (6,630 per-share values, 1,367 CAGRs, 1,397 Piotroski results, 321 predictability values, 889 ranks; Altman, Beneish and Sloan for all 127), the oracle exiting 0.
- Distributions on that copy:
  - Piotroski F 3-9 (median 6).
  - Altman: distress 5, grey 7, safe 66, N/A 49.
  - Beneish: 5 flags.
  - Predictability: 1 to 4.5 stars, none at 5.
  - Composite: 30 to 90 (quartiles 48 / 56 / 67.5).
- The 14 severe signs are each recomputed from the raw tables and explained (`scores-m2-5-severe-judged.txt`):
  telecoms under the original Altman, hypergrowth and one gain under Beneish, securities purchases under Sloan.
Evidence: `qa/evidence/scores-m2-5-{oracle,severe-judged}.txt`
Gates: ruff ✓ mypy ✓ pytest 1395 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) test(qa): the quality scores' independent oracle (M2-5)
Next: M2-8 (docs: README and USAGE_GUIDE for the quality scores).

### Build iteration 30: 2026-10-22 03:16: M3-0
Did:
- Wrote M3's design, `docs/superpowers/specs/2026-10-22-sec-ownership-design.md`, from the plan's outline and
  verified facts:
  - The cached submissions feeds hold 6,507 Form 4s (median 54 a company over about eight months), so three
    years is some 30,000 small XML fetches, about 1.7 hours at a polite rate.
  - Every Form 4's primary document is a rendered `xslF345X0n/` path over the raw XML.
  - Every company's feed carries its SIC code (for M5).
- Form 4: parsed from each filing's XML, cached, incremental, amendments superseding. Aggregates: open-market
  net buying, CEO/CFO buys, cluster buys, 10b5-1 sales. The INS tab, the API and the screener.
- 13F: a checked list of about 30 value investors in the code, eight quarters, values put in dollars across the
  SEC's 2023 change, each filing checked against its own entry count and value total. A CUSIP map with OpenFIGI.
  Guru pages, the HDS tab's section, the aggregated portfolio.
- Replaced the M3-0 row with M3-1 to M3-8. Logged Q12 (the SEC's bulk data sets) and Q13 (the guru list) as
  NEEDS-USER, each with a default that changes nothing.
- M2-8's docs worker and code review 16 run meanwhile.
Evidence: the spec's "Facts" section (read-only queries of the SEC cache)
Gates: ruff ✓ mypy ✓ pytest 1395 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) docs: Milestone 3's design, SEC ownership
Next: M2-8's docs when its worker reports; M2-9's review findings; then M3-1.
