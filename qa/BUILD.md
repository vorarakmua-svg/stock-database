# Build board: the GuruFocus roadmap, milestones 0-5

- Plan: `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` (approved 2026-10-21)
- Protocol: `qa/BUILD_LOOP_PROMPT.md`
- Status: TODO | DOING | DONE | NEEDS-USER | BLOCKED (why)
- A milestone without a design spec starts with its design task. That task writes the spec and
  replaces its own row with the milestone's task rows.

**Counts:** TODO 5 · DOING 0 · DONE 39 · NEEDS-USER 0 · BLOCKED 0

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
| M2-8 | Docs: README and USAGE_GUIDE | DONE | this commit | README "Quality scores", USAGE_GUIDE "Quality scores" |
| M2-9 | Milestone close: code review 16 (opus) with Critical and Important findings fixed; backup and offline backfill of the user's DB | DONE | d26990d, e340bd4, 9b9a34b, 86f3c05, this commit | `qa/evidence/review16-report.md`, `qa/evidence/bug18[6-9]-*`, `bug19[01]-*`, `m2-9-*` |

## M3: SEC ownership (Form 4 insiders, 13F gurus)

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M3-0 | Design M3: `docs/superpowers/specs/2026-10-22-sec-ownership-design.md`, then these rows | DONE | this commit | the spec |
| M3-1 | `src/ownership/form4.py`: the Form 4 parser (owners and roles, non-derivative and derivative rows, 10b5-1, amendments), with real-document fixtures; with tests | DONE | this commit | `qa/evidence/ownership-m3-1-*.txt` |
| M3-2 | Form 4 collection (full submissions feeds back three years, cached XML, incremental), storage (`insider_filings`, `insider_trades`) and `python -m src.ownership.collect --insiders`; with tests | DONE | this commit | `qa/evidence/ownership-m3-2-*.txt` |
| M3-3 | Insider aggregates, the INS tab, `GET /api/stocks/{t}/form4`, screener fields; checked against Yahoo's insider rows, browser-checked; with tests | DONE | this commit | `qa/evidence/ownership-m3-3-*.txt` |
| M3-4 | 13F: the guru list (`src/ownership/gurus.py`, each CIK checked), the information-table parser with its checksums, storage; with tests | DONE | this commit | `qa/evidence/ownership-m3-4-*.txt` |
| M3-5 | The CUSIP map (curated for our universe, OpenFIGI for the rest) and quarter-over-quarter changes; with tests | DONE | this commit | `qa/evidence/ownership-m3-5-*.txt` |
| M3-6 | Guru pages (`/gurus`, `/gurus/{cik}`, `/gurus/portfolio`), the HDS tab's Gurus section, the API, browser-checked; with tests | DONE | this commit | `qa/evidence/ownership-m3-6-*.txt` |
| M3-7 | Docs: README and USAGE_GUIDE | DONE | this commit | `qa/evidence/m3-7-gates.txt` |
| M3-8 | Milestone close: code review 17 (opus) with Critical and Important findings fixed; backup, then the live ownership collection into the user's DB, checked against Yahoo and the 13F checksums | DONE | ee0d510, 3ed24eb, this commit | `qa/evidence/review17-report.md`, `review17-fixes-*`, `bug220-fix-*`, `m3-8-*` |

## M4: more valuation tools and market pages

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M4-0 | Design M4: `docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md`, then these rows | DONE | this commit | the spec |
| M4-1 | `src/valuation/indicators.py`: the reverse DCF, EPV, NCAV and the net-net, the median P/S value, the Graham number on tangible book; storage (`valuation_indicators`), the engine hook after the scores, the backfill; a test pinning `MEDIAN_MODELS` and the valuations; with tests | DONE | this commit | `qa/evidence/indicators-m4-1-*` |
| M4-2 | The VAL tab's "Other indicators" section (the reverse DCF at the live price), `GET /api/stocks/{t}/valuation-indicators`, the screener fields, browser-checked; with tests | DONE | this commit | `qa/evidence/indicators-m4-2-*` |
| M4-3 | `src/market/`: the `.xls` reader (`xls.py`), the FRED, Shiller and Treasury sources with their cache, storage (`market_series`, `market_fetches`), `python -m src.market.collect`; with tests | DONE | this commit | `qa/evidence/market-m4-3-*` |
| M4-4 | The market page (`/market`: the Buffett indicator and its zones, Shiller's CAPE, the yield curve) and `GET /api/market`, a nav link, browser-checked; with tests | DONE | this commit | `qa/evidence/market-m4-4-*` |
| M4-5 | Independent oracle `qa/oracle/indicators_oracle.py` and real-data validation: the indicators on a backfilled copy, the reader against the sheet's own CAPE and the app's `^GSPC`, the Buffett ratio by hand, the valuations untouched | DONE | this commit | `qa/evidence/oracle-m4-5-*` |
| M4-6 | Docs: README and USAGE_GUIDE | DONE | this commit | README "Other valuation indicators", "Market data"; USAGE_GUIDE "Other valuation indicators", "Market Data"; `qa/evidence/docs-m4-6-*` |
| M4-7 | Milestone close: code review 18 (opus) with Critical and Important findings fixed; backup, then the offline backfill of the indicators and the market collection into the user's DB | DONE | 4d48de6, this commit | `qa/evidence/review18-report.md`, `review18-fixes-*`, `m4-7-*` |

## M5: a wider universe and automation

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M5-0 | Design M5: `docs/superpowers/specs/2026-10-25-universe-automation-design.md`, then these rows | DONE | this commit | the spec |
| M5-1 | The universe: `universe/core.txt`, the S&P 500 list (`src/universe/sp500.py`: Wikipedia's table parsed and checked, one request; the dated `universe/sp500-<date>.csv` saved), ticker normalization, `stock-data --tickers-file` and `--skip-existing`; with tests | DONE | this commit | `qa/evidence/universe-m5-1-*`, `universe/` |
| M5-2 | SIC codes and industries: `companies.sic`/`sic_description` (collected, and filled offline by `python -m src.universe.sic`), industry groups (3-digit SIC, else 2-digit, at least 10 companies), industry ranks in `rank_all`, the DES and STAT tabs, the scores API, the screener's industry filter and fields, the scores oracle's industry gate; with tests | TODO | | |
| M5-3 | `stock-data refresh`: quotes and bars daily, the SEC's new filings weekly, the full collections they call for, insiders and gurus weekly, the market data daily, the recomputation; `refresh_log`, a lock, a log file, exit codes; a real run on a scratch copy; with tests | TODO | | |
| M5-4 | The scale pre-flight: a synthetic copy of about 500 companies, the pages' and APIs' timings, the query plans, the indexes they need (every page under 1 s, the screener under 0.5 s); with tests | TODO | | |
| M5-5 | Docs: README and USAGE_GUIDE (the universe, `--tickers-file`, SIC and industries, the refresh command, Task Scheduler instructions) | TODO | | |
| M5-6 | Milestone close: code review 19 (opus) with Critical and Important findings fixed; the dry run, the backup, then the live collection of the S&P 500's new members, their SIC and Form 4s, the recomputation and the checks at about 500 companies | TODO | | |

## NEEDS-USER (each also recorded in `qa/BUGS.md`)

- Q10 [OPS]: a draft PR so CI runs on the branch. Default: no PR; local gates and ruff's py39 target.
- BUG-172 [VAL]: the multiples band's basis and outliers (GE's bull case $8,498.58); fixing it moves the median. Default: unchanged.
- Q11 [VAL]: the quality composite's weights. Default: equal.
- Q12 [OPS]: the SEC's bulk insider and 13F data sets. Default: no bulk downloads; one filing at a time.
- Q13 [OPS]: the guru list. Default: the 34 value investors in `src/ownership/gurus.py`.
- Q14 [OPS]: installing `xlrd` to read Shiller's `.xls`. Default: nothing installed; M4's own reader.
- Q15 [OPS]: installing a Windows scheduled task for `stock-data refresh`. Default: not installed; the README gives the command.

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

### Build iteration 31: 2026-10-22 03:24: M3-1
Did:
- Fetched 63 real Form 4 documents (five issuers' latest, and three 4/A) through the app's own SEC handler (its
  User-Agent and rate limiter), into a scratch folder. Six became fixtures: a director's purchase (PFE), a CEO
  buying through a trust (INTC), a 10b5-1 sale (AAPL), an exercise with its withholding, sales, a gift and the
  derivative row (AAPL), an officer's gift (BA), an amendment (PG). None holds an email address.
- `src/ownership/form4.py` parses the `ownershipDocument`:
  - the issuer and each reporting owner with their role and title;
  - every non-derivative and derivative transaction (code, shares, price, acquired or disposed, holding after,
    direct or indirect; for derivatives the exercise price and underlying);
  - the 10b5-1 flag (both 1/0 and true/false), and an amendment's original filing day.
  A footnote-only price is None with the footnote's id. A document that isn't one raises `Form4Error`.
- Tests: 8 (the module missing first); five mutants, each killed. All 63 sampled documents parse, with every
  trade dated, coded and counted. JPM's cached feed held no Form 4 among its latest 100 filings (structured-note
  prospectuses), which the full-feed fetch of M3-2 covers.
- M2-8's docs worker and code review 16 still run.
Evidence: `qa/evidence/ownership-m3-1-{red,green,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1403 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(ownership): read insiders' Form 4 filings from their XML (M3-1)
Next: M2-8's docs when its worker reports; M2-9's review findings; M3-2 (Form 4 collection and storage).

### Build iteration 32: 2026-10-22 07:50: M2-8, and M2-9 started
Did:
- A sonnet worker wrote the docs from the spec, the code's constants and the oracle's evidence: the README's "Quality scores" (every formula, cutoff and N/A rule, the 20 signs, the ranks and composite, the tables, the STAT tab, API and screener presets, the oracle's result, the limits) and the usage guide's (the columns and four SQL examples, each run read-only on the copy: 10, 17, 3 and 11 rows). The spec now matches the code in four places the worker found it silent or looser.
- Reviewed them myself against the spec and the code; ran every SQL example read-only on the backfilled copy;
  the docs test passes.
- Started M2-9 while the docs were written: code review 16 dispatched (opus, read-only, a3c8a77..0356db8: M2
  and the fixes since review 15). M3-0 and M3-1 were committed meanwhile; a usage limit paused both workers
  for a while, and each resumed where it stopped.
Evidence: README.md and USAGE_GUIDE.md ("Quality scores")
Gates: ruff ✓ mypy ✓ pytest 1411 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) docs: the quality scores in the README and the usage guide (M2-8)
Next: M2-9's review findings when it reports; meanwhile M3-2.

### Build iteration 32: 2026-10-22 07:53: M3-2
Did:
- `src/ownership/collect.py`: a company's submissions feed back N years (the `recent` block, then the older
  pages whose filings reach the cutoff), each Form 4 and 4/A's raw XML through the app's SEC handler (a new
  public `SECHandler.get_raw`), parsed, and cached once it parses. A failed fetch or parse is reported and left
  out. `python -m src.ownership.collect --insiders [--years 3] [--db PATH] [TICKERS...]`.
- Storage (`insider_filings`, one row per filing and owner; `insider_trades`): each filing's rows replaced by
  accession, so a filing an earlier run stored stays when this one can't fetch it. Each 4/A marks the original it
  amends (the same owner's Form 4 filed on its original day, for the same period).
- Tests: 8 (the module missing first); five mutants, each killed (one caught a rule to keep: a document is cached
  only once it parses).
- Live, into a scratch copy with a scratch cache: PFE and AAPL, three years, 452 filings in 107 s, 0 errors.
  PFE's two amendments each supersede their original. AAPL: 116 sales, 40 filings under a 10b5-1 plan. PFE: six
  open-market purchases ($2.96M over the last year).
- M2-8 committed (707c06b); code review 16 runs.
Evidence: `qa/evidence/ownership-m3-2-{red,green,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1411 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(ownership): collect and store insiders' Form 4 filings (M3-2)
Next: M2-9's review findings when it reports; meanwhile M3-3 (the insider aggregates, the INS tab, the API).

### Build iteration 33: 2026-10-22 08:06: M3-3
Did:
- `src/ownership/insiders.py` (pure):
  - Open-market purchases and sales (P and S) over 3, 6 and 12 months: shares, dollars, net, buyers and sellers.
  - CEO and CFO purchases. A title naming a unit, such as BA's "EVP, Pres. & CEO, BGS", belongs to that unit's
    head; the flag is stored per filing for the screener.
  - Cluster buys: 3 or more insiders within 30 days.
  - The share of sales made under a 10b5-1 plan, where the filings say.
- The web (`src/webapp/insiders.py`, shared):
  - `GET /api/stocks/{t}/form4`. The existing `/insiders` keeps serving Yahoo's rows, which its clients and
    tests rely on; the spec now says so.
  - The INS tab's SEC section (summary, cluster and CEO/CFO lines, the trades with their codes' meanings),
    with Yahoo's table only where no Form 4 is stored.
  - Four screener fields (net buying, buyers, CEO/CFO buys, the largest 30-day cluster, all over 12 months),
    each NULL when the tables don't exist.
- Tests: 25 (the modules missing first); six mutants. One was equivalent and the redundant filter it touched is
  gone; one exposed a test gap (no superseded purchase), now covered. All six killed.
- Real data (PFE, AAPL; scratch copy):
  - PFE: three insiders bought $2.96M in August 2026, a cluster, including the CEO's $1.00M.
  - AAPL: $207M sold by 8 insiders, 56% of it under 10b5-1 plans.
  - Against Yahoo's rows where both cover the same months: PFE 26 of 34 and AAPL 80 of 100 match an SEC trade
    on the same day with the same shares, or the day's total for that insider and code. The rest are kinds
    Yahoo labels or sums differently: option conversions, multi-day sale ranges, rows without text.
- Browser (headless Chrome, scratch server): PFE's INS tab renders the SEC section; MSFT, without Form 4s, falls
  back to Yahoo's table; no console errors. Server stopped; port 8765 free.
- Code review 16 reported (no Critical; I1-I6 Important); M2-9 fixes them next.
Evidence: `qa/evidence/ownership-m3-3-{red,green,mutants,realdata}.txt`; `shots/PFE-ins.png`
Gates: ruff ✓ mypy ✓ pytest 1436 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(ownership): insider aggregates on the INS tab, the API and the screener (M3-3)
Next: M2-9: record code review 16 and fix I1-I6 test-first, then M2's live backfill.

### Build iteration 34: 2026-10-22 08:09: M2-9 (code review 16 recorded)
Did:
- Code review 16 (opus, read-only, a3c8a77..0356db8 and the docs commit) reported "with fixes": no Critical
  issue, six Important, eleven Minor. Its report is `qa/evidence/review16-report.md`.
  - Confirmed: the six models' medians and verdicts are unchanged apart from RTX's documented Lynch bull.
  - It recomputed, independently, every rank component, WACC, NCAV, earnings yield, return on capital,
    momentum and all 248 warning signs: no disagreement.
- Recorded them as bugs:
  - P2: BUG-186 (an untested Piotroski test shown as Fail), BUG-187 (the scores read a price-less snapshot),
    BUG-188 (the listing rule folds DELL's merger), BUG-189 (PGR's phantom zero debt), BUG-190 (debt-free
    filers lose ROIC), BUG-191 (the Magic Formula ranks 49 of 93, silently).
  - P3: BUG-192 to BUG-202 (the minors).
Evidence: `qa/evidence/review16-report.md`
Gates: ruff ✓ mypy ✓ pytest 1436 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) chore(qa): record code review 16
Next: M2-9: BUG-186 to BUG-191, each test-first; then M2's live backfill.

### Build iteration 35: 2026-10-22 09:03: M2-9 (review 16's fixes, M2's live backfill)
Did:
- Fixed code review 16's six Important findings, each test-first, with mutants:
  - BUG-186: an untested Piotroski test reads "Not tested", not "Fail" (d26990d).
  - BUG-187: the scores read the latest snapshot with a price (d26990d).
  - BUG-188: a year that ended before the first bar on the private count, which only the listing's jump folds,
    has no per-share figures in the scores. A merger around the listing jumps the same way (DELL's EMC), and
    total assets can't separate them (CRWD's and APP's moved with the count too). The listing's own year keeps
    the public count, as the history's listing quarter does; the valuations still fold (e340bd4).
  - BUG-189 and BUG-190: `read_debt` reads a year with interest paid and no debt tagged as unknown debt (PGR), and
    one with neither, outside banks and insurers, as zero debt with ROIC on equity less cash (ISRG, ANET, PANW,
    PLTR, VRTX). The ROIC - WACC row gives the reason ROIC is missing. The scores' own debt/EBITDA feeds the
    Buffett-Munger preset, and a scores table from before that column screens with the field NULL (9b9a34b).
  - BUG-191: Greenblatt's net working capital counts as 0 when negative, and the STAT row and the API say why a
    company has no Magic Formula rank. The tag gaps behind the rest are open as BUG-203 (86f3c05).
- Each fix left every valuation row, median and verdict unchanged on a backfilled copy; the scores oracle
  passed 9 of 9 after each.
- M2's live step:
  - Dry run on a backup-API copy of the user's DB. The scores oracle passed 9 of 9 and the history oracle all
    gates.
  - Against the user's DB: valuations change only in RTX's Lynch row (BUG-185's bull, $29.02 to $24.80); no
    median or verdict moves. Scores change for 126 of 127: the Magic Formula ranks 61 (was 49), the composite
    moves for 23 (APP 68 to 60, PGR 68 to 65, VRTX 70 to 72...), warning signs 248 to 249 (PANW's ROIC below
    WACC), and Buffett-Munger gains ISRG (14).
  - Web smoke test on the dry-run copy (scratch server; port freed): ANET's STAT tab shows "4 / 8" with one
    "Not tested", ROIC - WACC 18.3% and Magic Formula rank 54. JNJ's rows say "no ROIC: no operating income"
    and "not ranked: no operating income (EBIT)".
  - Backup to `%LOCALAPPDATA%\stockdb-backups\2026-10-22`: the DB by the backup API, integrity ok, row counts
    equal, plus json/ (128 files) and csv/ (3).
  - Ran `python -m src.valuation.backfill --db data/output/stock.db` (offline). The ten tables are identical
    to the dry run's apart from timestamps; the scores oracle passes 9 of 9 on the live DB.
- Opened BUG-204 (P3): the STAT tab's metric cards show `metrics_annual`'s ROIC and debt/EBITDA ("—" for a
  debt-free filer) above the Quality section's reading of the debt.
Evidence: `qa/evidence/bug186-*`, `bug187-*`, `bug188-*`, `bug189-190-*`, `bug191-*`, `m2-9-{gates-a..d,dryrun,
dryrun-valuations,dryrun-scores,dryrun-summary,oracles,web,backup,live,live-vs-dry}.txt`;
`%LOCALAPPDATA%\stockdb-qa\build\m29\shots\ANET-stat.png`
Gates: ruff ✓ mypy ✓ pytest 1460 passed, 1 skipped | CI: no PR (Q10)
Commit: (this commit) chore(qa): close M2-9: review 16's fixes and M2's live backfill
Next: M3-4 (13F: the guru list, the information-table parser, storage).

### Build iteration 36: 2026-10-22 13:23: M3-4
Did:
- The guru list (`src/ownership/gurus.py`, mine): 34 gurus, each CIK checked against its submissions feed (name,
  13F-HR filings). A guru may have earlier filers, each with its own name: Pershing Square Inc. reports Bill
  Ackman's portfolio from Q2 2026, when Pershing Square Capital Management's filing became a 13F-NT notice (before,
  the two reported different holdings). DME Capital Management files David Einhorn's since Greenlight's last 13F
  (Q4 2023). Scion stopped after Q3 2025.
- Two design decisions, now in the spec:
  - Each filing is read from its full submission text, which types every document; `index.json` doesn't.
  - The eight-quarter window goes by period of report.
- The 13F code (an opus worker, reviewed; its first run stopped at the usage limit and was resumed):
  - `src/ownership/thirteenf.py`: the documents split by type, tags matched on local names, values in dollars.
  - Storage: `guru_filers`, `guru_filings`, `guru_holdings`.
  - `collect.py`: a shared filer source, `--gurus [--quarters 8]`, a guru's quarter the union of its filers'
    13F-HRs.
- The real data decided two rules (mine):
  - The checksum allows rounding: entries exact, the value sum within half a unit per entry plus half. The 13
    failures of the first run were gaps of 1 to 4 units.
  - Units by the holdings' prices where the filing date gives impossible ones. Baupost and Duquesne still state
    thousands after the 2023 switch, so their totals read $5.4M instead of $5.4B. The multiplier used is stored.
- `qa/oracle/thirteenf_oracle.py` (new, mine, no `src` imports) re-reads every cached filing with plain patterns.
- Tests: 47 new, each failing first (the worker's 44, my 3). Mutants: 60, each killed (the worker's 59, re-run by
  me, plus one).
- Real data (a scratch copy, re-run by me):
  - 248 filings, 20,538 holdings, 34 gurus, 0 errors, 0 checksum failures; 13 totals off by rounding only.
  - Units inferred for Baupost's 7 and Duquesne's 7, nothing else.
  - Berkshire's Q2 2026: 89 entries, $299,253,556,246 on both sides.
  - The oracle passes all its gates.
Evidence: `qa/evidence/ownership-m3-4-{gurus-check,gurus-red,gurus-green,red,green,mutants,mutants-rerun,realdata,
oracle,filers-red,filers-green,gates}.txt`
Gates: ruff ✓ mypy ✓ pytest 1507 passed, 1 skipped | CI: no PR (Q10)
Commit: (this commit) feat(ownership): the gurus' 13F holdings: the curated list, the parser with its checksum,
storage and collection (M3-4)
Next: M3-5 (the CUSIP map and quarter-over-quarter changes). Its aggregation also takes the latest RESTATEMENT of a
period and adds its NEW HOLDINGS amendments.

### Build iteration 37: 2026-10-22 18:20: M3-5
Did:
- The universe map (`src/ownership/cusips.py`, mine): 124 codes for 121 tickers.
  - Every one of the 1,941 13F codes was looked up on OpenFIGI, and each match to our tickers read against its
    13F issuer name.
  - OpenFIGI finds a foreign issuer's CINS (a code starting with a letter: Chubb, Linde, Seagate) only as
    `ID_CINS`.
  - Added by hand: GOOG and BRK-A; Honeywell's and Exxon Mobil Corp's codes from before they changed; Trian's typo
    of Allstate's.
  - No guru holds ANET, CRWD, DUK, ETN, HWM, MRVL, PRU or SO.
- The code (an opus worker, reviewed; its run stopped at the usage limit once and was resumed):
  - `src/ownership/openfigi.py`: keyless, 10 codes a request 2.5 s apart, cached.
  - The `cusip_map` table, `map_cusips`, `--gurus` mapping after collecting (`--no-openfigi` to skip).
  - `src/ownership/portfolio.py`: a guru's quarters and their changes.
- My refinements, each from the real data:
  - Positions are keyed by the mapped ticker, else the CUSIP, so a changed code isn't a sale plus a purchase.
  - A NEW HOLDINGS amendment's rows that repeat the base are left out one for one. First Eagle's Q2 2026
    amendment repeats its whole report: the quarter read $120.7B instead of $60.75B, and MSFT +193% instead of
    +46%.
  - OpenFIGI's "Invalid idValue format" (a filer's bad check digit) is an answer, mapped 'none'.
- The worker found and fixed one more: a job error failed its request's 9 other codes.
- Tests: 87 new, each failing first (the worker's 83, my 4). Mutants: 103, each killed; I re-ran them.
- Real data (the scratch copy):
  - Map: 124 universe, 1,796 OpenFIGI, 21 'none', 0 errors.
  - Quarters: all 235 equal their filings' totals; none incomplete.
  - Berkshire Q2 2026: GOOG/GOOGL, DAL and M added; COF, NUE and KR reduced.
  - Pershing: Q1 2026 sums both filers (HHH 18.85M + 9M), and Q2 reads unchanged.
  - Oaktree Q1 2026: the latest restatement is used.
  - Splits: NFLX's 10:1 and BKNG's 25:1 adjust (+2,460% unadjusted, +2.4% adjusted).
  - My SQL recomputation of First Eagle's Q2 2026 agrees: $60,750,982,695, MSFT +46.4%.
- Records:
  - HON's reverse split, inside a combined Yahoo factor, reads as a 50% sale. Added to BUG-184 (same cause).
  - BUG-205 (P3): the edge cases left.
Evidence: `qa/evidence/ownership-m3-5-{universe-review,red,green,mutants,mutants-rerun,realdata,gates}.txt`
Gates: ruff ✓ mypy ✓ pytest 1587 passed, 1 skipped | CI: no PR (Q10)
Commit: (this commit) feat(ownership): the CUSIP map and each guru's quarter-over-quarter changes (M3-5)
Next: M3-6 (guru pages, the HDS tab's Gurus section, the API; the cross-guru aggregates).

### Build iteration 38: 2026-10-23 01:10: M3-6
Did:
- My design decision: the gurus' quarters are materialized when collecting. The holders tab, the aggregated
  portfolio and a screener field all need every guru's latest quarter; that's too slow per request, and SQL can't
  call Python.
  - `guru_quarters` and `guru_positions` (plus sold-out rows), written after `map_cusips`.
  - `python -m src.ownership.collect --positions` rebuilds them offline.
  - The spec says so.
- The code (an opus worker, reviewed; resumed once after the usage limit):
  - `src/webapp/gurus.py`: the list, a guru's quarter, the gurus holding a stock (with its other share class),
    the aggregated portfolio.
  - Pages `/gurus`, `/gurus/{cik}` (`?period=`) and `/gurus/portfolio`, and a nav link.
  - The HDS tab's Gurus section.
  - `/api/gurus`, `/api/gurus/portfolio`, `/api/gurus/{cik}`, `/api/stocks/{t}/gurus`.
  - Screener `gurus_holding` and `gurus_buying`, shown as one "Gurus (buying)" column.
  - The worker found an oversized CIK in a URL giving a 500; it's a 404 now.
- Tests: 43 new, each failing first or killed by its rule's mutant. Mutants: 79, each killed; I re-ran them.
- Real data (a scratch copy):
  - `--positions` in 3.3 s: 235 quarters, 16,813 position rows.
  - Stored rows against `load_guru` for all 34 gurus: 0 mismatches.
  - AAPL: 8 gurus. GOOGL with GOOG: 20.
  - The portfolio's top: GOOGL 16, GOOG 15, BRK-B 14, V/AMZN/MSFT 13.
  - The screener's counts equal direct counts (AAPL 8, GOOGL 20, KO 4, PFE 4).
- Browser (headless Chrome, scratch server; port freed):
  - Nine screenshots, four looked at by me: the list, Berkshire's Q2 2026, GOOGL's holders tab, the portfolio.
  - Pages answer in 61 ms or less, with no server errors.
Evidence: `qa/evidence/ownership-m3-6-{red,green,mutants,mutants-rerun,realdata,gates,gates-rerun}.txt`;
`%LOCALAPPDATA%\stockdb-qa\build\m36\shots\*.png`
Gates: ruff ✓ mypy ✓ pytest 1630 passed, 1 skipped | CI: no PR (Q10)
Commit: (this commit) feat(ownership): the guru pages, the holders tab's Gurus section, the API and the screener
fields (M3-6)
Next: M3-7 (README and USAGE_GUIDE for Milestone 3).

### Build iteration 39: 2026-10-23 07:29: M3-7
Did:
- Milestone 3's docs (a sonnet worker, reviewed; resumed once after the usage limit):
  - README: a new section "SEC ownership: insiders and gurus", with insiders (Form 4), gurus (13F), collecting
    (commands, options, what each touches and takes) and limitations.
  - README, shorter updates: the features list, the data sources (OpenFIGI added), the project tree, the tables
    sentence, the tab table, the JSON API table (five rows) and the web features.
  - USAGE_GUIDE: a new "SEC Ownership" section with every table's columns, the pages, the API and the screener
    fields. Its 14 SQL examples run read-only on the scratch copies, with sensible rows (GOOGL's gurus holding
    and buying read (20, 9), as the screener does).
- I read the README section against the code and the evidence. Its Yahoo cross-check wording ("same day, shares
  within 1%") matches M3-3's evidence.
- The worker found two quirks in the insider aggregates. Both are documented as limitations and recorded:
  - BUG-206: a joint Form 4 counts one buyer on the INS tab and the API, every owner in the screener.
  - BUG-207: a title like "CEO, Chairman" isn't counted as a CEO buy.
Evidence: `qa/evidence/m3-7-gates.txt`
Gates: ruff ✓ mypy ✓ pytest 1630 passed, 1 skipped (the docs tests included) | CI: no PR (Q10)
Commit: (this commit) docs: SEC ownership in the README and the usage guide (M3-7)
Next: M3-8 (code review 17, then the backup and the live ownership collection into the user's DB).

### Build iteration 40: 2026-10-24 10:46: M3-8 (Milestone 3 closed)
Did:
- Code review 17 (opus, read-only) covered Milestone 3 and M2-9's four fixes, and judged it "ready with fixes".
  - C1 (BUG-208, P1): a company's own Form 4s as another issuer's owner were stored as its insiders' trades
    (PFE's $1.23B Cerevel filing).
  - I1 (BUG-209): a 4/A hid its whole original, though both real 4/As only add a trade.
  - I2 (BUG-210): XOM and BLK ignored their predecessors' Form 4s.
  - Nine minors, recorded as BUG-211 to BUG-219.
  - It confirmed the 13F pipeline, the quarters, the screener counts, SQL safety and M2's fixes independently.
- The fixes (an opus worker, reviewed; resumed twice after usage limits), each test-first:
  - an issuer filter;
  - amendments by trade, a code correction included (my refinement);
  - the predecessors' feeds;
  - a store failure that stays with its company (ee0d510).
- The first full dry run was stopped by the system for low memory at 114 of 127 companies. You approved the
  restart. The second run found BUG-220: a filer's dropped decimal point (the Lilly Endowment's $1,031,414 for
  $1,031.41) made LLY's insiders sell $78.4B. Prices 1,000 times off the day's close are now flagged and left out of
  dollars (3ed24eb).
- Gates: 1,687 passed. Mutants: 69, each killed; I re-ran them.
- The final dry run (a fresh copy, 1m41s from the cache):
  - 31,562 Form 4 filings for all 127 companies, 591 of other issuers left out (BAC 179, BRK-B 79, KKR 68, BX
    62), 0 errors;
  - XOM 121 filings, from its predecessor's CIK; BLK 95 of its 301 from its predecessor's;
  - 102,817 trades, 362 restated by a 4/A, 3 prices flagged (LLY's);
  - 248 13F filings, 0 checksum failures; the CUSIP map 124 / 1,796 / 21; 235 quarters, 16,813 positions;
  - Yahoo's rows match an SEC trade 68% of the time; the 13F oracle passes all its gates.
- Backup: `%LOCALAPPDATA%\stockdb-backups\2026-10-24` (integrity ok, row counts equal, json/ and csv/ copied).
- The live collection (`python -m src.ownership.collect --insiders --gurus --db data/output/stock.db`, 1m56s):
  - the same counts, 0 errors;
  - every ownership table identical to the dry run's apart from timestamps; the other tables untouched;
  - the 13F oracle passes on the live DB.
- Web on a copy of the live DB (scratch server; port freed): every page and API answers 200 in 0.1 s or less.
  LLY's 12-month net selling reads $2.59B; BRK-B's insiders tab has no OXY filing.
Evidence: `qa/evidence/review17-report.md`, `review17-fixes-*`, `bug220-fix-*`,
`m3-8-{dryrun-check,dryrun3-check,dryrun-13f-oracle,backup,live.log,live-vs-dry,live-13f-oracle}.txt`
Gates: ruff ✓ mypy ✓ pytest 1687 passed, 1 skipped | CI: no PR (Q10)
Commit: (this commit) chore(build): close M3, with the user's DB holding the insiders' and the gurus' filings
Next: M4-0 (design Milestone 4: more valuation tools and the market pages).

### Build iteration 41: 2026-10-24 10:55: M4-0 (Milestone 4's design)
Did:
- Wrote `docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md` after checking its facts against the code and the sources:
  - the DCF row's stored assumptions are enough to solve for the growth a price implies;
  - M1's P/S range already holds the median P/S value; M2's rows hold EPV's and NCAV's inputs and the WACC;
  - FRED's keyless CSVs give the Buffett indicator's two series; Treasury.gov's yearly CSV, which the app already
    reads for the 10-year rate, has the whole curve;
  - Shiller's `ie_data.xls` is a legacy Excel workbook and no reader for it is installed.
- Indicators, display-only like owner earnings (`MEDIAN_MODELS` and the verdicts untouched, a test pinning both):
  the reverse DCF, the earnings power value, NCAV with Graham's net-net, the median P/S value and the Graham
  number on tangible book, each with its formula, bear/base/bull and N/A reasons, in `valuation_indicators`.
- The market page: the Buffett indicator with its trend zones, Shiller's CAPE, the yield curve and its spreads,
  from `market_series`, with `python -m src.market.collect`. The workbook is read by a small reader of our own.
- Replaced the M4-0 row with M4-1 to M4-7. Logged Q14 (`xlrd`) as NEEDS-USER, its default installing nothing,
  and listed Q12 and Q13 on the board, which iteration 30 recorded only in `qa/BUGS.md`.
Evidence: the spec's "Facts" section
Gates: ruff ✓ mypy ✓ pytest 1687 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) docs: Milestone 4's design
Next: M4-1 (the indicators).

### Build iteration 42: 2026-10-24 19:52: M4-1
Did:
- An opus worker (resumed once after a usage limit) built `src/valuation/indicators.py` test-first:
  - the reverse DCF: a bisection for the growth at which the DCF's own inputs give the snapshot's price;
  - EPV, NCAV with Graham's net-net, the median P/S value and the Graham number on tangible book;
  - `valuation_indicators` with `export_valuation_indicators`, and the engine hook after the scores, isolated.
  - 49 tests; RED: 48 failed against stubs, on assertions. With my two, 51.
- My review found the code as designed, with one refinement, made test-first: EPV was N/A for 13 large caps that
  file no operating income (XOM, CVX, JNJ, LLY, PFE ...). A year without it now takes pretax income plus interest
  expense, GuruFocus's EBIT, and the years are listed. EPV now applies to 93 companies, not 80.
- Mutants: 49, each killed (45 the worker's, 4 mine); I ran them all.
- Real data, on a backup-API copy (the backfill takes 8 s):
  - the seven existing tables (valuations, the summary, the history and ranges, the scores, per-share rows,
    signs) are identical before and after;
  - 635 indicator rows: the reverse DCF applies to 91 companies, EPV 93, NCAV 15 (83 negative), the P/S value 120,
    the tangible Graham number 81 (33 have a negative tangible book);
  - the worker's checks: EPV's WACC, NCAV, the P/S base and the reverse DCF's inputs equal the stored figures,
    and hand recomputations agree.
- Logged BUG-221 (P3): five current-securities tags aren't read as short-term investments, so NVDA's latest year
  has none after $34.6B the year before.
Evidence: `qa/evidence/indicators-m4-1-{red,ebit-red,gates,mutants,realdata,realdata2,same-tables,done}.txt`
Gates: ruff ✓ mypy ✓ pytest 1738 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(valuation): the reverse DCF, EPV, NCAV, the median P/S value and the tangible Graham number (M4-1)
Next: M4-2 (the VAL tab's section, the API and the screener fields).

### Build iteration 43: 2026-10-24 20:51: M4-2
Did:
- An opus worker built the web side test-first (RED: 44 failed, 4 passed against stubs):
  - `src/webapp/valuation_indicators.py`, the view shared by the API and the VAL tab: the five rows in order, with
    their labels, methods and notes, and each base's upside from the live price;
  - the reverse DCF solved again at the live price from its stored inputs, even on an N/A row;
  - the VAL tab's "Other indicators" section, loaded lazily after Owner Earnings;
  - `GET /api/stocks/{t}/valuation-indicators`, and five screener fields over fixed joins, NULL without the table:
    `reverse_dcf_growth`, `epv_upside_pct`, `ps_value_upside_pct`, `graham_tangible_upside_pct`, `price_to_nnwc`.
  - 48 tests.
- The API path differs from the spec's: `/api/stocks/{t}/indicators` is already the price chart's MA/RSI/MACD bundle,
  and a test pins it. I agreed, and the spec now names the new path.
- Mutants: 67, each killed; I ran them again.
- Web check (scratch server on a copy of M4-1's backfilled copy; the port freed afterwards):
  - all 127 tickers answer 200, and the 309 upsides equal base / price - 1;
  - the 91 live solves give back the price through an independent DCF;
  - with a moved quote, AAPL at $300 reads 24.1% live against 26.5% stored;
  - the screener's fields equal the API's on every row, and the CSV carries them;
  - the server logged no traceback and no 5xx; headless Chrome logged no console errors.
- My review of the diff and screenshots: the section sits between Owner Earnings and DCF Sensitivity as designed.
  The screener's table is now wider than the 1200px page, so its verdict column scrolls out of view: BUG-222 (P3).
Evidence: `qa/evidence/indicators-m4-2-{red,gates,mutants,mutants-rerun,web,done}.txt`, `indicators-m4-2-val-{AAPL,JPM,O,GEV}.png`, `indicators-m4-2-screener-reverse-dcf.png`
Gates: ruff ✓ mypy ✓ pytest 1786 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(web): the other indicators on the VAL tab, in the API and the screener (M4-2)
Next: M4-3 (the market data: the `.xls` reader, FRED, Shiller and Treasury, `market_series`, the CLI).

### Build iteration 44: 2026-10-24 22:02: M4-3
Did:
- An opus worker built `src/market/` test-first (RED: 70 failed, 1 passed against stubs):
  - `xls.py`, our own reader of Shiller's legacy workbook: the OLE2 container (FAT, DIFAT, directory, mini
    stream; chains guarded) and the BIFF8 records the `Data` sheet uses (SST across CONTINUE records, NUMBER, RK,
    MULRK, LABELSST, LABEL, FORMULA, BOOLERR);
  - `sources.py`: FRED's, Shiller's and Treasury.gov's parsers, and a cached fetcher on one session with requests'
    own User-Agent (never the SEC one), a request a second and retries;
  - `market_series` and `market_fetches` with `export_market_series`, and `python -m src.market.collect`.
  - 72 tests, run on workbooks the tests write; no copy of Shiller's data is in the repo.
- The real-data run (a scratch copy and cache) found that today's FRED files leave a missing value empty, not ".".
  `NCBEILQ027S` is annual before 1951-Q4, so that source failed while the others stored, as designed. The worker
  fixed it test-first, and the next run stored all four.
- Real data:
  - the Buffett ratio for 2026-04-01 reads 255.0%;
  - Shiller's own CAPE column, recomputed from his price, earnings and CPI, matches within 1e-15 in all 1,750
    months, on the 120 months ending the month before;
  - his monthly price against the mean of the app's `^GSPC` closes: a median difference of 0.004% over 1,187
    months, two months off by more than 1%, both explained;
  - the Treasury's 10-year on 2026-10-20 (5.12%) equals the stored risk-free rate (0.0512);
  - 99,894 Treasury rows from 1990 over 14 maturities. The first full run takes about 12 minutes, because
    Treasury.gov answers slowly; a second run serves everything from the cache in 3 s.
- Mutants: 83, each killed; I ran them again. My review of the diff: the reader's guards, the cache rules, the
  per-source isolation and the exit codes are as designed. The spec now records the findings.
Evidence: `qa/evidence/market-m4-3-{red,gates,mutants,mutants-rerun,collect,realdata,done}.txt`
Gates: ruff ✓ mypy ✓ pytest 1858 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(market): a reader for Shiller's workbook, FRED, Shiller and Treasury sources, storage and the CLI (M4-3)
Next: M4-4 (the market page and its API).

### Build iteration 45: 2026-10-24 23:53: M4-4
Did:
- An opus worker built the market page test-first (RED: 46 failed, 1 passed against stubs):
  - `src/webapp/market.py`, the view shared by `/market` and `GET /api/market`, computed at read time;
  - the Buffett indicator against the least-squares trend of its log, with z and the five zones;
  - Shiller's CAPE with its mean, median, extremes and percentile (M1's `window_stats`);
  - the yield curve against a year earlier, and the 10y-2y and 10y-3m spreads since 1990;
  - the page's four Plotly charts, the empty states naming `python -m src.market.collect`, and a Market nav link.
- My review of the first version: "latest inverted run" picked one-day blips, a single day in September 2024
  for 10y-2y. At my request the worker replaced it test-first with the last inverted day and the latest
  sustained inversion: runs joined across gaps of at most 20 days, at least 20 inverted days. 10y-2y now reads
  2022-07-06 to 2024-09-05 (539 days); 10y-3m reads 2025-02-26 to 2025-09-15 (85 days, 2025's re-inversions),
  the 2022-2024 one before it.
- Mutants: 110, each killed; I ran them again.
- Real data (scratch server on a copy of M4-3's DB; the port freed afterwards):
  - 37 of 37 figures recomputed by hand agree;
  - the Buffett ratio is 255.05% (by hand 255.046%), its trend 147.6% rising 1.8% a year, z +1.68: "modestly
    overvalued", though the percentile reads 100%, a record. The whole-history fit's spread puts +2 sd at 283%.
    That is as designed and published;
  - CAPE is 40.71 (mean 17.80, median 16.61, percentile 98.9%);
  - the yield curve's 10-year on 2026-10-20 equals the models' rate;
  - no console errors; the empty page shows the three commands.
Evidence: `qa/evidence/market-m4-4-{red,red-episodes,gates,mutants,mutants-rerun,web,done}.txt`, `market-m4-4-api.json`, `market-m4-4-{page,empty}.png`
Gates: ruff ✓ mypy ✓ pytest 1912 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(web): the market page: the Buffett indicator, Shiller's CAPE and the yield curve (M4-4)
Next: M4-5 (the indicators' independent oracle and the real-data validation).

### Build iteration 46: 2026-10-25 00:23: M4-5
Did:
- An opus worker wrote two oracles that read SQLite only and never import `src`:
  - `qa/oracle/indicators_oracle.py` re-derives the five indicators: its own DCF and Brent root finder; EPV's EBIT,
    tax, WACC (CAPM and M2's debt reading) and per-share values; NCAV and the net-net; the P/S scaling; tangible
    book. It takes as stored only the inputs earlier oracles validated (the DCF and Graham rows, the P/S range,
    the per-share rows);
  - `qa/oracle/market_oracle.py` checks CAPE against Shiller's own price, earnings and CPI, his price against the
    app's `^GSPC` closes, the 10-year rate, and (with the API's answer) the Buffett figures and the yield curve.
- On a backup-API copy of M4-3's DB, backfilled with the final code:
  - the seven existing tables are identical before and after;
  - the indicators oracle passes 12 of 12 gates: applicability agrees for 127 of 127 companies on each indicator,
    and 1,048 EPV figures, 241 NCAV, 360 P/S and 357 tangible Graham figures agree, as do the reverse DCF's 91
    growths (largest gap 4.5e-6 points);
  - the market oracle passes 6 of 6: CAPE in 1,750 of 1,750 months; the price in 99.83% of 1,187 months, the two
    misses explained; the Buffett ratio, trend, z and zone; every yield-curve figure.
  - On a deliberately altered copy both oracles fail, each naming the altered row; a stress copy exercises the
    reverse DCF's out-of-range branch.
- Of the worker's notes, two EPV points I fixed test-first (RED: 2 failed):
  - the 5-year window goes by fiscal-year labels: a missing year narrows it rather than reaching back;
  - without today's count (after a failed quote), EPV divides by the latest fiscal year's count, as the DCF does,
    and records the count's source.
  Mutants for M4-1's spec: 54, each killed. The oracle passes again on the re-backfilled copy, and the spec says
  both.
- Logged BUG-223 (P3): V files EPS and share counts only by class, so it has none in any year. That is why its
  Graham, Lynch, multiples, NCAV, tangible Graham and P/S value are N/A.
- Noted, not changed: M2's WACC takes the absolute value of a negative interest expense (only DIS, whose Rd is the
  risk-free rate either way).
Evidence: `qa/evidence/oracle-m4-5-{untouched,indicators,indicators-rerun,market,broken,stress,epv-red,epv-mutants,done}.txt`
Gates: ruff ✓ mypy ✓ pytest 1914 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) test(oracle): Milestone 4's independent oracles, and EPV's window and count settled (M4-5)
Next: M4-6 (docs: README and USAGE_GUIDE).

### Build iteration 47: 2026-10-25 00:54: M4-6
Did:
- A sonnet worker documented Milestone 4:
  - README: "Other valuation indicators" under the valuation layer (each formula, its N/A rules, the table, the VAL
    tab, the API, the screener fields, the oracle's results, the limitations); a new "Market data" section (the
    sources and storage, our `.xls` reader and Q14, the cache and network rules, the CLI with its options and exit
    codes, the market page's methods with the 2026-10-24 readings, the oracle, the limitations); the data sources,
    the web interface's features and endpoints, and the project structure;
  - USAGE_GUIDE: "Other valuation indicators" and "Market Data" in prose, bash and SQL, and the table of contents.
- Its checks:
  - the docs test passes, and every SQL example ran on the M4-5 scratch copy;
  - every documented endpoint answered through the TestClient;
  - 253 identifiers checked against the code and the schema.
- My review of the new sections: accurate, in the earlier milestones' voice.
- The worker found that `--treasury-from` with a later year deletes the earlier stored yields, since each series is
  replaced whole (the cache keeps them, and a default run restores them). The docs say so; logged as BUG-224 (P3)
  for the milestone close.
Evidence: `qa/evidence/docs-m4-6-{tests,sql,done}.txt`
Gates: ruff ✓ mypy ✓ pytest 1914 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) docs: the other valuation indicators and the market data in the README and the usage guide (M4-6)
Next: M4-7 (milestone close: code review 18, then the backup, the backfill and the market collection into the user's DB).

### Build iteration 48: 2026-10-25 02:36: M4-7 (code review 18 and its fixes)
Did:
- Code review 18 (opus, read-only) covered Milestone 4 (26d74e4..f7e721a): "ready with fixes", no Critical. It
  confirmed the formulas, units, zones, oracles and that nothing reaches the valuations. Its findings:
  - I-1 (BUG-225, P2): a Treasury answer without yields was accepted, cached and stored whole, deleting a year's
    yields for good. It would have bitten M4-7's own first, cacheless collection;
  - I-2 (BUG-226, P2): Shiller's workbook link was trusted whatever its scheme or host, then stored and rendered as
    the CAPE's source link (stored XSS);
  - I-3 (BUG-227, P3): a crafted workbook's FAT could make the reader allocate gigabytes;
  - five minors (BUG-228 for four of them; m-4 recorded as BUG-229).
- The fixes (an opus worker, reviewed), each test-first (RED in `review18-fixes-red.txt`), with 23 new tests:
  - Treasury files with rows but without the 3-month, 2-year or 10-year column, and past years without rows, are
    failed answers that fall back to the cached copy;
  - only an https link to the workbook is taken, and the page links only http(s) URLs;
  - the reader accepts only MS-CFB's sector sizes and refuses an inflated or repeated FAT before allocating;
  - a narrower `--treasury-from` keeps the earlier yields (BUG-224);
  - a "Stale:" summary line lists fallbacks to a cached copy, and the docs no longer say a layout change "fails
    loudly" once a copy exists;
  - negative per-share cells read "-$5.88";
  - tests pin the sources' User-Agent (requests' own) and timeout.
- Mutants: 43 new, each killed (I ran them again); M4-2's 67, M4-3's 83 and M4-4's 110 re-run, each killed.
  The review's probes no longer reproduce. A real-data run on a copy left all 109,739 market rows intact, and the
  market oracle passes 6 of 6.
- Logged, open (P3): BUG-229, downloads have no size cap; BUG-230, a FRED answer with only its header replaces the
  cached copy (the stored rows stay, and the next day's fetch recovers).
- Noted: a `--treasury-from` before 1990 now fails the Treasury source: those years have no rows.
- An outside process emptied this session's scratchpad overnight. The worker recreated the mutant runner and the
  probes; I recreated my scripts.
Evidence: `qa/evidence/review18-report.md`, `review18-fixes-{red,green,gates,gates-rerun,mutants,mutants-rerun,mutants-m42,mutants-m43,mutants-m44,probes,realdata,done}.txt`
Gates: ruff ✓ mypy ✓ pytest 1937 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) fix(market): Treasury answers without yields, Shiller's link, the reader's FAT and a narrower Treasury run (code review 18)
Next: M4-7's live step: a dry run on a fresh copy, the backup, then the backfill and the market collection into the user's DB.

### Build iteration 49: 2026-10-25 02:55: M4-7 (Milestone 4 closed)
Did:
- The dry run, on two backup-API copies of the user's DB and a copy of M4-3's warm cache (`m4-7-dryrun*.txt`):
  - the backfill stored 127 tickers in 7 s; the market collection stored 4 of 4 sources, with "Stale: none" and
    "Failed: none";
  - all 32 existing tables are identical before and after (timestamps aside);
  - the indicators oracle passes 12 of 12, and the market oracle, with the API's answer, 6 of 6.
- Backup: `%LOCALAPPDATA%\stockdb-backups\2026-10-25` (integrity ok, 32 tables' row counts equal, json/ 128 files and
  csv/ 3 files identical).
- Live:
  - `python -m src.valuation.backfill`: 127 tickers, 7 s;
  - `python -m src.market.collect` (the default DB and cache, a cold first run): 691 s, exit 0, 4 of 4 sources
    stored. 37 Treasury years were fetched fresh with the review's fixes in place, nothing stale or failed.
- Checks on a backup-API copy of the live DB (`m4-7-live-check.txt`):
  - its 32 existing tables are identical to the backup's: the valuations, the history, the scores and the
    ownership tables are untouched;
  - the three new tables equal the dry run's: `valuation_indicators` 635 rows, `market_series` 109,739,
    `market_fetches` 4;
  - both oracles pass (12 of 12, 6 of 6). The Buffett indicator reads 255.05% for Q2 2026 (z +1.68, modestly
    overvalued) and CAPE 40.71 for 2026-10; the yield curve runs to 2026-10-23;
  - the web smoke (the TestClient on the copy): /market, /api/market, each indicators API and VAL fragment
    tried, the screener's new fields and the CSV all answer 200 in 232 ms or less, and ZZZZ answers 404.
- Deleted Milestone 4's intermediate scratch copies (m41, m42, m44, m45, review18; about 1.6 GB); m43, m47 and
  review18-fixes stay.
Evidence: `qa/evidence/m4-7-{dryrun,dryrun-check,backup,live,live-market,live-check}.txt`
Gates: ruff ✓ mypy ✓ pytest 1937 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) chore(build): close M4, with the user's DB holding the indicators and the market data
Next: M5-0 (design Milestone 5: a wider universe and automation).

### Build iteration 50: 2026-10-25 03:03: M5-0 (Milestone 5's design)
Did:
- Wrote `docs/superpowers/specs/2026-10-25-universe-automation-design.md` after checking its facts in the code and the data:
  - `stock-data` takes positional tickers only; its collection ends in `compute_and_store`;
  - a full collection takes about 6 s a ticker warm and 20 s cold;
  - the SIC code is fetched (it decides `sector_class`) but not stored, while the JSON exports keep it;
  - M2's ranks group by sector class;
  - the web's quote refresh fetches no bars;
  - the SEC cache lasts 7 days;
  - the DB is 183 MB and the exports 330 MB for 127 companies.
- The design:
  - a universe in the repo: `core.txt` and the S&P 500 list from Wikipedia's page, saved dated, with
    `--tickers-file` and `--skip-existing`;
  - SIC codes stored, and industry groups (3-digit, else 2-digit, at least 10 companies) with industry ranks
    beside M2's;
  - `stock-data refresh`, with daily quotes and bars, weekly SEC filings, insiders and gurus, daily market data and
    the recomputation, plus a lock, a log and exit codes;
  - a scale pre-flight on a synthetic 500-company copy;
  - the live step: about 380 new companies and their Form 4s, in the background.
- Replaced the M5-0 row with M5-1 to M5-6. Logged Q15 (installing the scheduled task) as NEEDS-USER, its default
  installing nothing.
Evidence: the spec's "Facts" section
Gates: ruff ✓ mypy ✓ pytest 1937 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) docs: Milestone 5's design
Next: M5-1 (the universe files, the S&P 500 list and `--tickers-file`).

### Build iteration 51: 2026-10-25 03:59: M5-1
Did:
- An opus worker built the universe test-first (RED: 91 failed, 2 passed against stubs):
  - `src/universe/tickers.py`: Yahoo's form (BRK.B as BRK-B), `.txt` and `.csv` tickers files;
  - `src/universe/sp500.py`: Wikipedia's S&P 500 table parsed with `html.parser`, refused when a column is missing,
    a CIK malformed or the rows outside 480-520; one company a CIK; written without overwriting;
  - `stock-data --tickers-file` (repeatable) and `--skip-existing`, with clear errors.
  - 96 tests on a synthetic page shaped like Wikipedia's; no copy of the page is in the repo.
- The list: one request to Wikipedia (HTTP 200, our User-Agent without an email accepted):
  - 503 rows read, 500 kept (GOOG, FOX and NWS left out beside GOOGL, FOXA and NWSA);
  - every CIK equals the SEC's own ticker map's (503 of 503);
  - saved as `universe/sp500-2026-10-25.csv` with `universe/README.md` (the source, its revision, the CC BY-SA
    attribution). `universe/core.txt` holds the DB's 127.
- Of the DB's 127, 125 are members (SCCO and SPCX aren't). `--skip-existing` resolves the other 375, checked with a
  recording fetcher that collected nothing.
- Mutants: 91, each killed; I ran them again. My review of the diff: positional tickers are now validated, so `^GSPC`
  is refused there. That is harmless: the benchmark is fetched once per collection run, never as a company.
- Logged BUG-231 (P3): `one_per_cik` keeps the first row in the page's order, and `--skip-existing` compares
  tickers. A page sorted by symbol would keep GOOG and collect a second Alphabet. The saved file is right.
Evidence: `qa/evidence/universe-m5-1-{red,gates,mutants,mutants-rerun,fetch,check,done}.txt`
Gates: ruff ✓ mypy ✓ pytest 2033 passed, 1 skipped, 1 warning | CI: no PR (Q10)
Commit: (this commit) feat(universe): the S&P 500 list, the universe files and stock-data --tickers-file (M5-1)
Next: M5-2 (SIC codes, industry groups and ranks).
