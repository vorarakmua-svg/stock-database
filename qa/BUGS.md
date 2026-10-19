# QA Bug Ledger

Severity: **P0** wrong numbers shown as fact, data loss/corruption, crash on a main flow, exploitable hole ·
**P1** feature broken or misleading for some tickers/sectors, wrong data matching · **P2** degraded UX, missing
validation, inconsistent formatting, misleading docs, slowness · **P3** cosmetic or wording.

**Open:** P0 0 · P1 3 · P2 4 · P3 2 · NEEDS-USER 7 · Verified: 8

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
Status: VERIFIED
Found: iteration 4 via VAL-04 | Fixed: iteration 7 (see LOG) | Regression tests: `tests/test_valuation_inputs.py::test_split_factor_uses_the_declared_ratio_not_the_share_jump`, `::test_split_factor_falls_back_to_the_share_jump_without_declared_splits`, `::test_declared_ratio_matches_a_run_of_splits_and_ignores_unrelated_ones`
Repro: AAPL Lynch: stored `hist_cagr` 14.64% vs 15.26% with the declared 4:1 split (FY2016 EPS 8.31/4 = 2.08 → FY2025 7.46).
Expected: per-share values moved to today's basis with the `split_events` ratios.
Actual: the implied factor is 3.808 = FY2018 weighted shares 20.0B (restated) ÷ FY2017 5.252B, which includes buybacks. Lynch base AAPL 109.18 vs 111.90 (−2.4%); NEE −2.0%, WMT −2.5% (all recent splitters).
Root cause: `_normalize_splits` (`src/valuation/inputs.py`) multiplied in the observed seam ratio (newer ÷ older share count), which includes that year's buybacks or issuance. Its docstring said `split_events` was 'empty for most tickers'; that is stale: 107 of 127 companies have rows, and 17 of the 18 companies with a detected split have declared ratios. Detection is kept (the seam is where the stored data switches basis, not the split date), but a declared ratio, or run of consecutive ratios, after the older year within ±25% of the jump now replaces it; otherwise the jump is kept. Verified on a backfilled copy: golden Level 2 now 0 mismatches; real 26 → 18 (AAPL/NEE/WMT/RTX Lynch and ANET/APH/AVGO/LRCX multiples resolved). Six values move 0.7–3.5% (AAPL Lynch 109.18 → 111.90, the oracle's value); `qa/evidence/iter07-bug006-impact.txt`.
Notes: it did: four of the eight L6 multiples mismatches were this bug. Mixed bases within one year (AAPL FY2018: weighted shares restated, shares_outstanding not) remain a VALID-04 topic. Code review 1 (iteration 10): splits dated after the latest fiscal year are never applied at all (KLAC, BKNG): BUG-014. RTX's "resolved" Lynch is agreement on a shared reading (its 1.589 is a spin-off price factor; see L12), not proof.

### BUG-007 [P2][VAL] Graham bear case ignores loss years
Status: NEEDS-USER (decide the intended bear case; parked in iteration 8 so the priority rule stops picking it)
Found: iteration 4 via VAL-03 | Fixed: — | Regression test: —
Repro: BA EPS FY2023 −3.67, FY2024 −18.36, FY2025 +2.48 → stored eps_bear = eps_base = eps_bull = 2.48.
Expected: spec "bear = min(EPS over last 3 FYs)" = −18.36, so Graham's bear value is undefined (√ of a negative).
Actual: loss years are dropped, so the bear case equals the base case. 8 companies affected (ALL BA BMY GEV MRVL MU VRTX WDC).
Notes: decide whether the bear should be N/A (excluded from the bear median) or 0 when a loss year is in the window.

### BUG-008 [P1][VAL] When the historical CAGR is undefined, growth falls back to a quarterly "analyst" figure
Status: VERIFIED, but the fix changed a planned rule without the user's decision: BUG-016 restores the plan's rule and Q5 asks
Found: iteration 4 via VAL-01 / VAL-04 | Fixed: iteration 6 (see LOG) | Regression tests: `tests/test_valuation_assumptions.py::test_derive_growth_never_uses_analyst_growth_alone`, `tests/test_valuation_models.py::test_value_dcf_growth_is_flat_when_fcf_history_starts_negative`, `::test_value_lynch_na_when_eps_history_starts_with_losses`
Repro: DELL Lynch `growth_source: analyst_only`, `hist_cagr: None`, `analyst_growth: 2.825`, giving growth 15% (cap) and base $130.2. With DELL's 6 years of EPS (2020→2026: 6.3%/yr) the oracle gets $54.3.
Expected: spec growth = min(hist CAGR, analyst), conservative by design; it never says to use analyst growth alone.
Actual: 31 rows (15 DCF, 16 Lynch) use analyst-only growth: MU 1,368.5%, CEG 1,091%, CVX 322.9%, PLTR 325%, DELL 282.5% (all → 15% cap); UBER −84.6%, COP −20.2%, TSLA −3% (→ 0%). The oracle lists 33 rows whose spec inputs are undefined but were still valued.
Root cause: `derive_growth` (`src/valuation/assumptions.py`) had an `analyst_only` branch for an undefined CAGR. The CAGR is undefined when the 10-FY window starts on a loss: DELL FY2017–2019 net losses enter through `FYRecord.eps()`'s net income ÷ shares fallback; negative early FCF does the same for DCF. That branch was deliberate (a test asserted it), so this fix **reverses an earlier design choice**, on the grounds that it contradicts the spec (growth = min(hist, analyst); only the reverse fallback is specified) and the module's own principle ('biased toward not overpaying'). Fix: no measurable CAGR gives `growth_source: none`, so DCF, DDM and owner earnings project flat 0%, and Lynch is N/A ('no usable earnings-growth history', its existing path). Verified on a backfilled copy: no row uses analyst-only growth; all 16 DCF rows now re-derive and match the oracle. Impact (`qa/evidence/iter06-bug008-impact.txt`): Lynch N/A for 16 companies; 11 DCF values drop 27–58% (CVX $293 → $124, DELL $317 → $142); 5 owner-earnings values drop 58%; the median moves for 19/127; no verdict flips.
Notes: alternative the user may prefer: start the CAGR at the first profitable year when at least 4 FYs remain (DELL would get 6.3%). Rejected here because a post-loss trough start inflates growth (CVX FCF would hit the 15% cap again). See Q2 for the analyst figure itself.

### BUG-009 [P2][VAL] DDM growth treats a dividend's initiation year (or a one-off special payment) as a full year
Status: VERIFIED for the partial first year; the special-payment half moved to Q3
Found: iteration 5 while verifying BUG-005 (VAL-02) | Fixed: iteration 8 (see LOG) | Regression tests: `tests/test_valuation_models.py::test_value_ddm_cagr_starts_at_the_first_full_year_of_payments`, `::test_value_ddm_keeps_a_full_first_year_when_a_later_year_has_a_special`
Repro: TMUS: one regular payment in 2023 (initiation, $0.65), then 4 a year → stored `hist_cagr` 137% over 2 years. Its 2013 $4.06 is a one-off merger distribution, not a regular dividend.
Expected: growth from comparable full years (skip a partial first year; exclude special payments).
Actual: CAGR 2023→2025 = (3.66 / 0.65)^(1/2) − 1 = 137%. Masked here by min() with analyst 5.3%, but without analyst growth it would hit the 10% cap. Special payments also inflate `ttm_dps` when they fall in the window.
Root cause: `value_ddm` measured growth from calendar-year totals (`_annual_dividend_totals`) with no notion of a partial year. The first window year is partial when a dividend started or restarted mid-year (TMUS, GOOGL, MU, GEV, DELL, HWM) or an ex-date slipped into the year before (BMY 2016: 3 payments, 2015: 5). Fix: when the first window year has fewer payments than the window's median count, growth starts at the next year. Comparing with the next year instead would drop COST's full 2016, because its 2017 special makes 5 payments (a guard test covers this). Verified on backfilled copies: the oracle applies the rule independently and agrees on every DDM row (real and golden). Impact (`qa/evidence/iter08-bug009-impact.txt`): MU growth 10% → 1.1% (DDM −29%), BMY 9.0% → 6.0% (−12%); GOOGL and GEV have one full year, so no measurable growth: 10% → flat (−34%, −35%). TMUS, DELL, HWM and PGR keep their growth (cap- or analyst-bound). The 5-model median moves only for MU; no verdict flips.
Notes: special payments are a policy question, now Q3. A short LAST year is deliberately not dropped: it can be a real cut or suspension, and hiding it would overstate growth.

### BUG-010 [P2][VAL] DDM dividend CAGR is computed from fewer than 4 fiscal years
Status: VERIFIED
Found: iteration 8 via VAL-02 (while fixing BUG-009) | Fixed: iteration 10 (see LOG) | Regression test: `tests/test_valuation_models.py::test_value_ddm_growth_needs_4_full_years_of_dividends` (3 full years measure nothing; 4 do)
Repro: TMUS has full dividend years 2024 and 2025 only → stored `hist_cagr` 29.3% over 1 year. BKNG, CRM and META also span 1 year, DELL 2.
Expected: spec, shared assumptions: the historical CAGR "uses up to 10 FYs of the model's basis metric (FCF for DCF, dividends for DDM, EPS for Lynch); requires ≥ 4 FYs". With fewer, the CAGR is undefined, so growth is flat 0% (BUG-008's rule).
Actual: `value_ddm` passes any ≥ 2 complete years to `derive_growth`. DCF and Lynch enforce ≥ 4 FYs; DDM doesn't (the plan left it out, and the oracle shares the gap).
Fix: with fewer than 4 full years left after BUG-009's first-year rule, `value_ddm` hands `derive_growth` no history, so no CAGR is measured (`_cagr_span` now returns None for an empty history instead of raising IndexError). Applicability is unchanged: the DDM section's "N/A when < 3 calendar years" still decides it. The oracle took the spec's rule in its own commit first and flagged exactly BKNG, CRM, DELL and TMUS against the pre-fix code (`qa/evidence/iter10-oracle-before-fix.txt`); after the fix it agrees on every DDM row. Impact (`qa/evidence/iter10-bug010-impact.txt`), under today's flat fallback for an unmeasured CAGR (which BUG-016 may revert): BKNG −34%, CRM −16%, DELL −34%, TMUS −21%; META unchanged; no median moves, no verdict flips.

### BUG-011 [P2][DATA] DHR's 2016 Fortive spin-off is stored as a $24.56 cash dividend (and as a 1.319 split)
Status: OPEN
Found: iteration 8 via VAL-02 | Fixed: — | Regression test: —
Repro: `dividend_events` DHR 2016-07-05 24.56, between regular payments of about 0.11; `split_events` DHR 2016-07-05 ratio 1.319 (the same spin-off).
Expected: a spin-off is not a cash dividend; DHR's 2016 total ≈ 0.44.
Actual: the 2016 total is 25.0, so DHR's 10-year dividend CAGR is −28.1% and DDM growth clamps to 0% (≈ 10% without it; the error is in the conservative direction).
Root cause: to confirm (Yahoo's dividend series vs the collector).
Notes: a "dividend" dated the same day as a non-integer split is the spin-off signature. DHR's 2023 Veralto spin-off appears only as a 1.128 split.

### BUG-012 [P1][GATHER] CB's stored dividends are missing the Sep and Dec 2025 payments
Status: VERIFIED for detection; the gap itself is upstream, and the DDM's handling of it is Q4 (NEEDS-USER)
Found: iteration 8 (dividend-gap scan, `qa/evidence/iter08-dividend-gaps.txt`) | Fixed: iteration 9 (see LOG) | Regression tests: `tests/test_integrity.py::test_dividend_check_flags_payments_yahoo_is_missing` plus 4 guard tests, `tests/test_fetcher_concurrency.py::test_assess_flags_dividend_payments_missing_from_yahoo`
Repro: `dividend_events` CB: 2025-03-14 0.91, 2025-06-13 0.97, then 2026-03-13 0.97, 2026-06-12 1.02: a 273-day gap where the usual is 91. Every other year in the window has 4 payments.
Expected: 4 payments a year; TTM ≈ $3.93.
Actual: TTM dividends are $1.99, so CB's DDM (the primary model for insurers) is about half; 2025 has 2 payments.
Root cause: upstream, confirmed. The collector stores Yahoo's full dividend series on every run (upsert; `yahoo_handler._get_dividend_history` → yfinance `Ticker.dividends` = `history(period="max")`, repair off), and CB's 2026-08-04 JSON export has the same gap, so none of its three runs (Jun 28, Jul 16, Aug 4 2026) received the payments. A read-only live check on 2026-10-20 (2 requests) shows Yahoo still lacks them in both the max and the 2-year series. Three independent sources show CB paid: SEC declared $3.82/share for FY2025 (0.91 + 3 × 0.97), Yahoo's own cash-flow statement (dividends paid $390M in Q3 and $384M in Q4 2025) and its dividend rate ($4.08). The program's defect was silence: nothing compared the series with the SEC figure it already stores. Fix: a flag-only integrity check, `check_dividend_completeness`, raises a MEDIUM finding (−10) when a gap in Yahoo's payment schedule begins near a fiscal year and Yahoo's payments for that year are below 80% of the SEC-declared dividends per share. Verified: on the real-data copy it fires for CB FY2025 only (109 dividend payers checked); re-assessing CB's real export offline drops its quality score 100 → 90 and adds a collection warning.
Notes: the first version also flagged MU FY2021 (a first dividend declared before the year ended and paid after it, following a gap since 1996), so the gap must now begin near the year. Re-collecting CB won't help while Yahoo lacks the payments, and CB's DDM stays about half until Q4 is decided. The iteration-8 scan's other gap (SCCO Feb → Aug 2024, probably a dividend paid in shares) isn't flagged.

### BUG-013 [P2][VALID] SEC annual dividends per share holds one quarter's (or half-year's) amount for some filers
Status: OPEN
Found: iteration 9 while calibrating the dividend completeness check (`qa/evidence/iter09-dividend-prototype.txt`) | Fixed: — | Regression test: —
Repro: real-data copy, `financials_annual.dividends_per_share` vs the year's Yahoo payments: V FY2023–2025 0.45/0.52/0.59 vs 1.80/2.08/2.36 (exactly ×4); CEG FY2023–2025 ×4; BAC FY2023 0.24 vs 0.92; DIS FY2024–2025 ×2 (a semiannual payer); PLD FY2024 0.01 and FY2025 0.03 vs ≈ 3.84 and 4.04.
Expected: the fiscal year's declared dividends per share (V FY2025 ≈ 2.36).
Actual: one period's amount. V's quarterly ladder shows the same pattern: 0.59 for the quarter ending 2024-12-31, then 0.0 for the following quarters (YTD differences).
Root cause: to confirm: the SEC parser's choice of duration context for `CommonStockDividendsPerShareDeclared` vs how these filers tag it.
Notes: the valuations don't use it; whether the web app displays it is unverified (MATCH-06). The dividend completeness check reads it only to confirm a shortfall, so a too-low figure can hide a finding but can't create one.

### BUG-014 [P1][VAL] Splits after the latest fiscal year are never applied, so per-share models value KLAC and BKNG on the pre-split basis
Status: OPEN
Found: iteration 10 by code review 1; verified on the real-data copy | Fixed: — | Regression test: —
Repro: KLAC split 10:1 on 2026-06-12 and its latest FY ended 2025-06-30: Graham base $154.83 and Lynch $446.44 against a price of $191.50 (post-split: $15.48 and $44.64, which would move the 5-model median from $70.00 to about $44.64). BKNG split 25:1 on 2026-04-06 (latest FY 2025-12-31): Lynch $2,483.55 against $193.64 (≈ $99.34; median $216.36 → ≈ $147.64).
Expected: per-share inputs on the same basis as today's price; the `FYRecord` docstring promises "the same convention Yahoo's adjusted price bars use".
Actual: `_normalize_splits` restates history onto the latest FY record's basis, so a split dated after that FY's period end is never applied.
Root cause: `src/valuation/inputs.py` (`_normalize_splits`, `load_inputs`): "current basis" means the latest FY's, not today's.
Notes: the review calls it P0-class (wrong numbers on screen); by the iteration-4 valuation calibration (large errors for some tickers) it's P1, and every company that splits shows it until its next annual report. The oracle shares the reading (`to_current_basis` aims at the latest FY's share count), so it couldn't catch this. Other declared "splits" after the latest FY: CRWD 4:1 (only its DCF applies, and that uses current shares), HON 0.9535 and SPGI 1.057 (spin-off price factors, see L12), SCCO 1.008 and 1.01 (stock dividends).

### BUG-015 [P1][STORE] The CSV merge can silently drop every accumulated row
Status: OPEN
Found: iteration 10 by code review 1; the BOM case reproduced in scratch (`qa/evidence/iter10-review-csv-bom.txt`) | Fixed: — | Regression test: —
Repro: export AAA and BBB; save `summary.csv` from Excel as "CSV UTF-8" (which adds a byte-order mark); export CCC: the file now holds only CCC.
Expected: earlier rows kept (BUG-004's guarantee), or, when the old file can't be read, the file left untouched with an error.
Actual: `_read_existing` reads with `utf-8`, so the BOM turns the header into `\ufeffticker` and `row.get("ticker")` drops every row. Per the review, an Excel ANSI (cp1252) save with one non-ASCII character logs a warning and is overwritten with this run's rows, and an exception mid-write leaves a truncated file.
Root cause: `src/exporters/csv_exporter.py` (`_read_existing`, `export`; BUG-004's fix, 42f8a01): BOM-intolerant read, overwrite when unreadable, non-atomic write.
Notes: before BUG-004's fix a failure lost one run; now the file holds every run, so a failure loses all of them (rebuildable from SQLite or JSON, but nothing says so). Fix direction (review): read with `utf-8-sig`; back up or refuse instead of overwriting an unreadable file; write to a temp file and `os.replace`.

### BUG-016 [P1][VAL] Iteration 6 changed a planned valuation rule without the user's decision (loop rule 6)
Status: OPEN
Found: iteration 10 by code review 1; verified against the plan and the oracle's history | Fixed: — | Regression test: —
Repro: the plan (`docs/superpowers/plans/2026-07-13-valuation-layer.md`, lines 390–396 and 485–489) specifies and tests an analyst-only fallback when the historical CAGR is undefined. f7feea4 (BUG-008) replaced it with flat 0% ("none") and, in the same commit, changed the oracle's `growth()` from "not derivable" (None) to 0.0, so its "16 DCF rows now match" evidence was circular.
Expected: loop rule 6: valuation policy is recorded as NEEDS-USER and not changed. The spec is silent on this case, so the plan's rule stands until the user decides.
Actual: the change is on the branch and moved 31 valuations (CVX DCF $293 → $124, five owner-earnings values −58%, Lynch N/A for 16 companies). Iteration 6's summary flagged it, but the loop made the change before asking.
Fix: restore the plan's fallback, its test and the oracle's "not derivable" reading; the decision is Q5. The change can be re-applied from f7feea4 if the user approves it.
Notes: the stale comment at `models.py:273` and the `analyst_only` rows in existing databases follow whatever Q5 decides.

### BUG-017 [P2][VAL] A 13-payment last year inflates DDM growth (O)
Status: OPEN
Found: iteration 10 by code review 1; verified on the real-data copy | Fixed: — | Regression test: —
Repro: Realty Income pays monthly; its 2025 has 13 ex-dates because the 2025-12-31 ex-date belongs to January's payment (the next is 2026-01-30). Stored `hist_cagr` is 4.58% over 9 years; with a 12-payment 2025 it's 3.66%, so the DDM base is about 4% high.
Expected: comparable full years at both ends of the CAGR.
Actual: BUG-009's rule fixes only a short FIRST year; a long LAST year still counts as one year.
Notes: a short last year is deliberately kept (it can be a real cut), but a long one inflates growth. The review also notes that a partial year, then a suspension, then a partial restart year measures growth from the restart (synthetic: 20.6%, capped at 10%).

### BUG-018 [P2][STORE] The CSV merge lets a failed re-collection blank a good row
Status: OPEN
Found: iteration 10 by code review 1, from the code (`csv_exporter.py` replaces a ticker's row with this run's summary unconditionally); not reproduced yet | Fixed: — | Regression test: —
Repro: collect AAPL, then re-collect it while Yahoo and SEC fail: the run's mostly empty summary replaces AAPL's good row.
Expected: the JSON merge's rule, which keeps `company_info` and financials when a re-collection fails.
Actual: `rows.update(new_rows)` replaces the row. Invalid tickers (ZZZZ) also keep a permanent row, and columns dropped from `to_summary` stay in the file.

### Q2 [VAL] NEEDS-USER: "analyst growth" is Yahoo's quarterly YoY earnings growth, not an analyst forecast
Status: NEEDS-USER
Context: the spec uses `analyst_snapshots.earnings_growth`, which is yfinance `earningsGrowth` (trailing quarterly YoY): values run from −84.6% to +1,368.5%. `min(hist, analyst)` mostly masks it, but it is not a long-term growth estimate.
Proposal: use a forward growth estimate if one is available, or drop the analyst term and rely on the clamped historical CAGR. This is a policy change, so it's the user's call.

### Q1 [UX] NEEDS-USER: show all companies on /screener before a filter is chosen
Status: NEEDS-USER
Context: `src/webapp/static/screener.js` auto-runs only when the URL has a query string, so a bare `/screener` shows filters and no rows, which looks broken.
Proposal: run the default (unfiltered) screen on load. Small effort, low risk. Awaiting the user's decision.

### Q3 [VAL] NEEDS-USER: how should DDM treat special and variable dividends?
Status: NEEDS-USER
Context: `value_ddm` counts every payment in the TTM basis and the yearly totals. Today: PGR's TTM is $13.90, of which $13.60 is one January 2026 payment (its annual variable dividend; the regular one is $0.10 a quarter), and DDM is the primary model for insurers. NVDA's latest payment is $0.25 after years of $0.01 (a raise or a special?). COST ($7, $10, $15), PSA ($13.15) and COP specials sit mid-window, so they affect neither the CAGR nor the TTM today, but would at a window edge. (DHR's spin-off is a data bug: BUG-011.)
Options: (a) keep every payment (today); (b) exclude one-off outliers (e.g. > 3× the surrounding regular payments) from the TTM and the CAGR endpoints; (c) like (b), but keep recurring variable dividends such as PGR's. This is policy, so it's the user's call.

### Q4 [VAL] NEEDS-USER: what should DDM do when Yahoo's dividend history is missing payments?
Status: NEEDS-USER
Context: the dividend completeness check (BUG-012) flags CB FY2025: Yahoo lacks its Sep and Dec 2025 payments, so the DDM's TTM dividend is $1.99 instead of ≈ $3.93 and CB's DDM is about half. The spec bases the DDM on TTM dividends from `dividend_events` (Yahoo).
Options: (a) keep the Yahoo TTM and rely on the data-quality warning (today); (b) when the check fires, use SEC's declared dividends per share for the trailing four quarters (needs BUG-013 fixed first); (c) make DDM N/A ("dividend history incomplete") for flagged companies. (b) and (c) change the spec's DDM basis or N/A rules, so it's the user's call.

### Q5 [VAL] NEEDS-USER: when a model's historical growth can't be measured, what growth should it use?
Status: NEEDS-USER
Context: the CAGR is undefined when the 10-FY window starts on a loss or negative FCF (DELL, CVX, MU, PLTR, AMD and others). The plan uses yfinance's one-quarter "analyst" figure alone, clamped to the cap (seen from −85% to +1,369%). Iteration 6 switched to flat 0%, with Lynch N/A; BUG-016 restores the plan until you decide. The switch moved 31 valuations (CVX DCF $293 → $124).
Options: (a) the plan's analyst-only fallback; (b) flat 0% (iteration 6); (c) measure from the first profitable year when at least 4 FYs remain (DELL 6.3%; CVX's FCF would hit the 15% cap again). Related: Q2 (what the "analyst" figure is).

---

## Leads (unconfirmed; discovery units confirm or drop them)

- L1 [GATHER/VALID] XOM, TSM and ARM finished without `calculated_metrics` in their sources (every other golden ticker has it). XOM is a plain US 10-K filer. (iter 1)
- L2 [GATHER] SPY: Yahoo answered 404 "No fundamentals data" 3 times, yet the run recorded 0 warnings / 0 errors (silent gap). (iter 1)
- L3 [CLI] A batch containing a failed ticker (ZZZZ, errors=1) still exited 0. Check the intended exit-code semantics. (iter 1)
- L5 [MATCH] Web UPDATE DATA jobs export SQLite only (`src/webapp/jobs.py:246`, `formats=["sqlite"]`), so JSON and the CSVs go stale after web updates. Possibly by design; confirm in MATCH-05. (iter 3)
- L6 [VAL] Level-2 mismatches not yet explained: NOW multiples (271%), WDC multiples (Sandisk spin-off), SCCO Lynch + multiples (stock dividends; EPS gaps, see L7), SPGI Lynch + multiples, GE Lynch (1:8 reverse split + spin-offs; 94%), T Lynch (28%). ANET/APH/AVGO/LRCX were BUG-006, fixed in iteration 7. Adjudicate oracle vs code. (iter 4, updated iter 7)
- L10 [VAL] APP: a split was detected and applied though `split_events` declares none, and AppLovin has never split. Possible false positive rescaling its EPS history. (iter 7; review 1: BX, CRWD, LIN, PLTR and UBER also keep a jump with no declared split, likely IPO, merger or conversion seams)
- L7 [VALID] `eps_diluted` is null for early FYs while net income and shares exist (DELL 2015–2019, SCCO 2014–2018; GOOGL weighted shares before 2022): canonical mapping gap. (iter 4)
- L8 [VAL] Graham/P/B BVPS uses weighted diluted shares and total equity including preferred (JPM basis $130.30 vs about $124 common). The spec is ambiguous. (iter 4)
- L9 [VAL] KKR graham/multiples: the oracle can't derive the inputs; check the data. (iter 4)
- L4 [VAL/VALID] `dividend_history.years_of_data` is the span from the first to the last payment (`yahoo_handler.py:710`): AAPL shows 39.2 years, including its 1995–2012 suspension. Check whether DDM or anything else treats it as an unbroken record. (iter 2)
- L12 [VAL] `split_events` mixes share splits with spin-off price factors (GE 1.281/1.253, DHR 1.319/1.128, HON 0.9535, SPGI 1.057, RTX 1.589), and `_declared_ratio` treats them alike: RTX's 1.589 was applied at its 2020 merger seam (observed 1.5717). Also: with no `period_end` every split ever recorded is a candidate; one declared split can match more than one seam; CRWD's IPO seam (3.12) only just misses its 2026 4:1 split; no tests for reverse splits or a missing `period_end`. (review 1, iter 10)
- L13 [GATHER] `stock_data_fetcher.py:398-407`: `results["csv"].append` raises KeyError when the summary export returns None (e.g. Excel has the file locked), which also skips the SQLite export and the valuation recompute. (review 1, iter 10)
- L14 [QA] Oracle independence: besides BUG-014's basis, the oracle shares the code's reading of spin-off ratios as splits, takes the DDM `ttm_anchor` from the stored output, and buckets dividends by the ex-date's calendar year. Oracle rule changes now go in their own commit first (loop prompt §7). (review 1, iter 10)
- L15 [CODE] `tests/test_docs_usage_guide.py` would run the guide's `fig.show()`, opening a browser during pytest, if plotly were installed (it isn't here). (review 1, iter 10)
