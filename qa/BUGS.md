# QA Bug Ledger

Severity: **P0** wrong numbers shown as fact, data loss/corruption, crash on a main flow, exploitable hole ·
**P1** feature broken or misleading for some tickers/sectors, wrong data matching · **P2** degraded UX, missing
validation, inconsistent formatting, misleading docs, slowness · **P3** cosmetic or wording.

**Open:** P0 0 · P1 1 · P2 0 · P3 2 · NEEDS-USER 1 · Verified: 1

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
Status: OPEN
Found: iteration 2 via DOCS-02 (real-data run of the guide's CSV examples), relates to MATCH-05 | Fixed: — | Regression test: —
Repro: collect two batches into the same `--output-dir`, then read `output/csv/summary.csv`.
Expected: one row per collected company (README: "Summary metrics for all tickers") with a stable column set.
Actual: each run overwrites it with only that run's tickers. Golden: 4 of 19 (ARM TSM SPY ZZZZ). **Your real data: 2 of 127 (BSX, KKR).** The columns are the union of that run's keys, so the `calc_*` columns vanish when no ticker in the run has calculated metrics. The guide's screening examples then raise `KeyError: ['calc_ev_to_ebitda'] not in index` or see only 2 companies.
Root cause: to confirm. `CSVExporter.export` (`src/exporters/csv_exporter.py:34`) writes `summary.csv` from the current run's `List[StockData]` with columns = union of keys.
Notes: expected fix direction: build the summary from all stored tickers (JSON dir or DB) with a fixed column order. Check `financial_history.csv` for the same pattern.

### Q1 [UX] NEEDS-USER: show all companies on /screener before a filter is chosen
Status: NEEDS-USER
Context: `src/webapp/static/screener.js` auto-runs only when the URL has a query string, so a bare `/screener` shows filters and no rows, which looks broken.
Proposal: run the default (unfiltered) screen on load. Small effort, low risk. Awaiting the user's decision.

---

## Leads (unconfirmed; discovery units confirm or drop them)

- L1 [GATHER/VALID] XOM, TSM and ARM finished without `calculated_metrics` in their sources (every other golden ticker has it). XOM is a plain US 10-K filer. (iter 1)
- L2 [GATHER] SPY: Yahoo answered 404 "No fundamentals data" 3 times, yet the run recorded 0 warnings / 0 errors (silent gap). (iter 1)
- L3 [CLI] A batch containing a failed ticker (ZZZZ, errors=1) still exited 0. Check the intended exit-code semantics. (iter 1)
- L4 [VAL/VALID] `dividend_history.years_of_data` is the span from the first to the last payment (`yahoo_handler.py:710`): AAPL shows 39.2 years, including its 1995–2012 suspension. Check whether DDM or anything else treats it as an unbroken record. (iter 2)
