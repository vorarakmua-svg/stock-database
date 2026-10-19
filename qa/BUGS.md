# QA Bug Ledger

Severity: **P0** wrong numbers shown as fact, data loss/corruption, crash on a main flow, exploitable hole ·
**P1** feature broken or misleading for some tickers/sectors, wrong data matching · **P2** degraded UX, missing
validation, inconsistent formatting, misleading docs, slowness · **P3** cosmetic or wording.

**Open:** P0 0 · P1 1 · P2 3 · P3 2 · NEEDS-USER 3 · Verified: 3

Valuation severity calibration (iteration 4): P0 = systematic error beyond tolerance across many tickers; P1 = large errors for some tickers; P2 = small systematic or scenario-only errors.

---

### BUG-001 [P2][DOCS] USAGE_GUIDE examples read field names that don't exist
Status: VERIFIED
Found: bootstrap (seeded) via DOCS-02 | Fixed: iteration 2 (see LOG) | Regression test: `tests/test_docs_usage_guide.py::test_usage_guide_examples_run_against_the_current_export` (+ fixture guard `test_fixture_is_shaped_like_the_current_export`)
Repro: `python -c "import json; d=json.load(open('data/output/json/AAPL.json')); fa=d['financials_annual']; print(fa[max(fa)].get('Revenue'))"` prints `None`.
Expected: the guide's examples print real values (data keys are `revenue`, `net_income`, `total_assets`, `operating_cash_flow`, …).
Actual: they look up `'Revenue'`, `'Net Income'`, `'Total Assets'`, `'Operating Cash Flow'`, which are absent, and `.get(..., 0)` silently yields 0.
Root cause: USAGE_GUIDE predates the canonical snake_case schema (`src/mappings/canonical.py`).
Notes: verified 2026-10-20 against `data/output/json/AAPL.json`. The RED run of the new docs test found 12 missing keys and 2 crashing examples: Accessing Dividend History (`KeyError: 'years_of_dividends'`; the field is `years_of_data`) and Graham Number (`KeyError: 'eps'`, a knock-on effect of `'Total Stockholders Equity'` reading 0). Fixed in USAGE_GUIDE.md (12 lookups plus the stale section list) and in README's sample JSON. Evidence: `qa/evidence/iter02-bug001-{red,green,real-output}.txt`. The real-data run prints FY2025 revenue $416.16B and net income $112.01B (matches the 10-K).

### BUG-002 [P3][CODE] Test runs emit StarletteDeprecationWarning (httpx + starlette.testclient)
Status: OPEN
Found: bootstrap (seeded) via CODE-11 | Fixed: — | Regression test: —
Repro: `pytest` ends with "1 warning": `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
Expected: clean test output, or a documented decision to defer.
Actual: the warning on every run (Starlette 1.3.1, httpx 0.28.1).
Root cause: Starlette 1.3 deprecates its httpx-based TestClient; `fastapi.testclient` re-exports it.
Notes: options are migrating per Starlette's guidance, or a dated `filterwarnings` entry. Must stay green on CI's Python 3.9.

### BUG-003 [P3][UI] Palette arrow keys may act on a stale list (suspected)
Status: OPEN (suspected; confirm with `tests/js/palette_driver.js`)
Found: bootstrap (seeded) via UI-02 | Fixed: — | Regression test: —
Repro: open the palette, type `AAPL`, press ArrowDown before the 150 ms debounce or the company search finishes, then Enter.
Expected: the selection applies to the current text's results.
Actual (suspected): ArrowDown moves within the old list; the new results then land with `selected = 0`, losing the keypress.
Root cause: to confirm. The arrow handlers in `src/webapp/static/palette.js` don't check `itemsQuery === input.value` the way `enter()` does.

### BUG-004 [P1][STORE] summary.csv keeps only the last collection run's tickers, and its columns depend on that run
Status: VERIFIED
Found: iteration 2 via DOCS-02 (real-data run of the guide's CSV examples), relates to MATCH-05 | Fixed: iteration 3 (see LOG) | Regression test: `tests/test_csv_exporter.py` (5 tests)
Repro: collect two batches into the same `--output-dir`, then read `output/csv/summary.csv`.
Expected: one row per collected company (README: "Summary metrics for all tickers") with a stable column set.
Actual: each run overwrites it with only that run's tickers. Golden: 4 of 19 (ARM TSM SPY ZZZZ). **Your real data: 2 of 127 (BSX, KKR).** The columns are the union of that run's keys, so the `calc_*` columns vanish when no ticker in the run has calculated metrics. The guide's screening examples then raise `KeyError: ['calc_ev_to_ebitda'] not in index` or see only 2 companies.
Root cause: both CSV writers opened their file with mode `'w'` and wrote only the current run's `List[StockData]`. `financial_history.csv` had the same overwrite. `StockData.to_summary()` added each section's keys only when the section was non-empty, and the columns were the union of *this run's* keys. Fix: `to_summary()` always emits every key (None when empty); `export()` and `export_financial_history()` merge with the existing file (a ticker or ticker-year replaces the same one; everything else is kept; same rule as the JSON merge), sorted. An unreadable old file logs a warning and falls back to this run's data. Evidence: RED `qa/evidence/iter03-bug004-red.txt` (5 failures), GREEN `iter03-bug004-green.txt`. Live CLI: `stock-data KO` then `stock-data JPM` into one dir gives summary.csv with JPM **and** KO and all 7 calc_* columns, and financial_history.csv with both (39 rows).
Notes: **NEEDS-USER:** the fix doesn't repair your existing `data/output/csv/summary.csv` (still 2 of 127 tickers). Future runs merge into it, but a complete file needs a one-off rebuild from the 127 JSON files, which writes to the real data, so it's the user's call.

### BUG-005 [P0][VAL] DDM dividend growth uses the whole dividend history, not the spec's ≤10 years
Status: VERIFIED
Found: iteration 4 via VAL-02 (`qa/oracle/valuation_oracle.py` Level 2) | Fixed: iteration 5 (see LOG) | Regression test: `tests/test_valuation_models.py::test_value_ddm_cagr_uses_only_the_last_10_years`
Repro: `python qa/oracle/valuation_oracle.py --db <realcopy> --level 2`, or read `valuations.assumptions` for model ddm.
Expected: spec "Dividend growth = min(hist dividend CAGR (≤10 yrs), analyst growth), clamped [0%, 10%]".
Actual: `cagr_years` 22–63 (KO 63, XOM 63, NEE 52, JPM 41, AAPL 38 spanning its 1995–2012 suspension). 40 of 106 DDM rows in the real DB differ from the ≤10-yr oracle by more than 0.5%, in both directions: JPM 111.6 → 137.9 (+24%), KO 53.15 → 42.29 (−20%), XOM 91.14 → 79.62 (−13%), AAPL 22.94 → 20.30 (−12%).
Root cause: `value_ddm` (`src/valuation/models.py`) built the CAGR from every complete calendar year of dividends; only FY records were truncated to 10 (`inputs.py`, `_MAX_FY_HISTORY`). Fix: `DIVIDEND_CAGR_YEARS = 10`, a calendar window `anchor.year - 10 <= year < anchor.year` (the same 10-value / 9-year span as the FY models), keeping the calendar-span exponent. Verified: backfill of fresh copies with the fixed code, then the oracle: DDM mismatches 41 → 0 (real), 7 → 0 (golden). Impact on the real data: 41/106 DDM values move (C +38%, JPM +24%, DHR −35%, WFC −29%), the 5-model median moves for 10/127 companies, and no verdict flips (`qa/evidence/iter05-bug005-impact.txt`).
Notes: evidence `qa/evidence/iter04-val-{golden,realcopy}.txt`. Explains lead L4 (AAPL `years_of_data` 39.2).

### BUG-006 [P2][VAL] Split adjustment of per-share history uses observed share-count jumps, not declared split ratios
Status: OPEN
Found: iteration 4 via VAL-04 | Fixed: — | Regression test: —
Repro: AAPL Lynch: stored `hist_cagr` 14.64% vs 15.26% with the declared 4:1 split (FY2016 EPS 8.31/4 = 2.08 → FY2025 7.46).
Expected: per-share values moved to today's basis with the `split_events` ratios.
Actual: the implied factor is 3.808 = FY2018 weighted shares 20.0B (restated) ÷ FY2017 5.252B, which includes buybacks. Lynch base AAPL 109.18 vs 111.90 (−2.4%); NEE −2.0%, WMT −2.5% (all recent splitters).
Root cause: to confirm in the fix iteration. The stored history also mixes bases within one year (AAPL FY2018: weighted shares restated, shares_outstanding not), see VALID-04.
Notes: may also explain some multiples mismatches (lead L6).

### BUG-007 [P2][VAL] Graham bear case ignores loss years
Status: OPEN, needs a decision (NEEDS-USER on the intended behavior)
Found: iteration 4 via VAL-03 | Fixed: — | Regression test: —
Repro: BA EPS FY2023 −3.67, FY2024 −18.36, FY2025 +2.48 → stored eps_bear = eps_base = eps_bull = 2.48.
Expected: spec "bear = min(EPS over last 3 FYs)" = −18.36, so Graham's bear value is undefined (√ of a negative).
Actual: loss years are dropped, so the bear case equals the base case. 8 companies affected (ALL BA BMY GEV MRVL MU VRTX WDC).
Notes: decide whether the bear should be N/A (excluded from the bear median) or 0 when a loss year is in the window.

### BUG-008 [P1][VAL] When the historical CAGR is undefined, growth falls back to a quarterly "analyst" figure
Status: OPEN
Found: iteration 4 via VAL-01 / VAL-04 | Fixed: — | Regression test: —
Repro: DELL Lynch `growth_source: analyst_only`, `hist_cagr: None`, `analyst_growth: 2.825`, giving growth 15% (cap) and base $130.2. With DELL's 6 years of EPS (2020→2026: 6.3%/yr) the oracle gets $54.3.
Expected: spec growth = min(hist CAGR, analyst), conservative by design; it never says to use analyst growth alone.
Actual: 31 rows (15 DCF, 16 Lynch) use analyst-only growth: MU 1,368.5%, CEG 1,091%, CVX 322.9%, PLTR 325%, DELL 282.5% (all → 15% cap); UBER −84.6%, COP −20.2%, TSLA −3% (→ 0%). The oracle lists 33 rows whose spec inputs are undefined but were still valued.
Root cause: to confirm. The CAGR is None when the window starts on a missing or non-positive value (DELL EPS null for 2017–2019; negative early FCF), and the code then falls back to analyst-only growth.
Notes: fix direction: compute the CAGR over the available positive history (≥4 FYs) or fall back conservatively; never let a quarterly spike set long-term growth. See Q2.

### BUG-009 [P2][VAL] DDM growth treats a dividend's initiation year (or a one-off special payment) as a full year
Status: OPEN
Found: iteration 5 while verifying BUG-005 (VAL-02) | Fixed: — | Regression test: —
Repro: TMUS: one regular payment in 2023 (initiation, $0.65), then 4 a year → stored `hist_cagr` 137% over 2 years. Its 2013 $4.06 is a one-off merger distribution, not a regular dividend.
Expected: growth from comparable full years (skip a partial first year; exclude special payments).
Actual: CAGR 2023→2025 = (3.66 / 0.65)^(1/2) − 1 = 137%. Masked here by min() with analyst 5.3%, but without analyst growth it would hit the 10% cap. Special payments also inflate `ttm_dps` when they fall in the window.
Root cause: `_annual_dividend_totals` sums every event per calendar year; there's no notion of partial or special years.
Notes: fix direction: drop the first calendar year when it has fewer payments than the steady cadence; detect specials as outliers vs the regular amount (needs care). Edge case for VAL-09.

### Q2 [VAL] NEEDS-USER: "analyst growth" is Yahoo's quarterly YoY earnings growth, not an analyst forecast
Status: NEEDS-USER
Context: the spec uses `analyst_snapshots.earnings_growth`, which is yfinance `earningsGrowth` (trailing quarterly YoY): values run from −84.6% to +1,368.5%. `min(hist, analyst)` mostly masks it, but it is not a long-term growth estimate.
Proposal: use a forward growth estimate if one is available, or drop the analyst term and rely on the clamped historical CAGR. This is a policy change, so it's the user's call.

### Q1 [UX] NEEDS-USER: show all companies on /screener before a filter is chosen
Status: NEEDS-USER
Context: `src/webapp/static/screener.js` auto-runs only when the URL has a query string, so a bare `/screener` shows filters and no rows, which looks broken.
Proposal: run the default (unfiltered) screen on load. Small effort, low risk. Awaiting the user's decision.

---

## Leads (unconfirmed; discovery units confirm or drop them)

- L1 [GATHER/VALID] XOM, TSM and ARM finished without `calculated_metrics` in their sources (every other golden ticker has it). XOM is a plain US 10-K filer. (iter 1)
- L2 [GATHER] SPY: Yahoo answered 404 "No fundamentals data" 3 times, yet the run recorded 0 warnings / 0 errors (silent gap). (iter 1)
- L3 [CLI] A batch containing a failed ticker (ZZZZ, errors=1) still exited 0. Check the intended exit-code semantics. (iter 1)
- L5 [MATCH] Web UPDATE DATA jobs export SQLite only (`src/webapp/jobs.py:246`, `formats=["sqlite"]`), so JSON and the CSVs go stale after web updates. Possibly by design; confirm in MATCH-05. (iter 3)
- L6 [VAL] Multiples/Lynch Level-2 mismatches not yet explained: ANET APH AVGO LRCX NOW (recent splits), WDC (Sandisk spin-off), SCCO (stock dividends), SPGI, GE (1:8 reverse split + spin-offs; Lynch 92%), T (Lynch 28%). Adjudicate oracle vs code. (iter 4)
- L7 [VALID] `eps_diluted` is null for early FYs while net income and shares exist (DELL 2015–2019, SCCO 2014–2018; GOOGL weighted shares before 2022): canonical mapping gap. (iter 4)
- L8 [VAL] Graham/P/B BVPS uses weighted diluted shares and total equity including preferred (JPM basis $130.30 vs about $124 common). The spec is ambiguous. (iter 4)
- L9 [VAL] KKR graham/multiples: the oracle can't derive the inputs; check the data. (iter 4)
- L4 [VAL/VALID] `dividend_history.years_of_data` is the span from the first to the last payment (`yahoo_handler.py:710`): AAPL shows 39.2 years, including its 1995–2012 suspension. Check whether DDM or anything else treats it as an unbroken record. (iter 2)
