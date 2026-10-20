# QA Test Matrix

Generated from `qa/LOOP_PROMPT.md` §6. Full test method and pass criterion for each ID live there.

**Status counts:** TODO 81 · PASS 13 · FAIL 12 · N-A 0 · BLOCKED 0 · NEEDS-USER 3  
**Consecutive clean sweeps:** 0

Status values: `TODO | PASS | FAIL→BUG-n | N-A (reason) | BLOCKED (why) | NEEDS-USER`

## VAL: Financial valuation correctness (highest priority)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| VAL-01 | DCF (two-stage FCF) | FAIL→BUG-039 | iter 33 | Level 1 ✓ and Level 2 ✓ on every derivable row; BUG-035 fixed (a share-count gap under 5% is settled by the filings: MRVL's DCF $10.59 -> $10.85); 16 rows rest on Q5; left: BUG-039 (P3, no Yahoo count) |
| VAL-02 | DDM (multi-stage) | NEEDS-USER | iter 39 | how DDM reads the payment history goes to the user (BUG-049: code review 5 found BUG-017's third fix misreads a special next to a window end, and four other shapes misread at both revisions; §10 after three fixes); BUG-040 (P3) and Q3 depend on the decision. No current DDM value differs from 61f8a56; the oracle agrees on every derivable row (iter 31) |
| VAL-03 | Graham Number | NEEDS-USER | iter 26 | Level 1 ✓; Level 2 ✓ on every derivable row (XOM and KKR derived with the period-end count since 0f47b5f); left: the bear case with loss years (BUG-007, NEEDS-USER, 8 rows) |
| VAL-04 | Peter Lynch | NEEDS-USER | iter 28 | Level 1 ✓; Level 2 ✓ on every derivable row (SCCO was the oracle's positional window, e4b6a1f); 16 rows incl. DELL rest on Q5 (growth when the CAGR can't be measured); BUG-032 fixed: once re-collected, SCCO measures FY2016–25 (20.2%, capped at 15% as before) |
| VAL-05 | Historical multiples band | PASS | iter 65 | the market close at each FY end: Yahoo's dividend-adjusted bars are rebuilt for the dividends they were adjusted for, up to the last of them, so a run that stores dividends but no bars no longer lifts FY-end prices (BUG-084 fixed: the stale copies' bases back to the user's data's, 0 of 889 rows move on the real copy); the oracle at levels 1 and 2 agrees on every derivable row of the real copy revalued (the known 8 only) and of the golden basket (0); P/E, P/B for banks and insurers, P/FFO for REITs over the last 5 FYs, at least 3 years (BUG-075, BUG-031 fixed); a split between runs whose bar request failed is BUG-094's (GATHER-06) (`qa/evidence/iter65-bug084-*`, `iter53-bug075-*`) |
| VAL-06 | Owner earnings | PASS | iter 26 | 127 real and 19 golden rows: every N/A reason and the 8-of-10 gate match the spec recomputed from raw tables; volatility fields, buy_below = 0.70 × base, discount = max(rf, 7%), no beta, no SBC add-back; excluded from the median (it would move 81 summaries); the API's verdict follows the spec for all; values recompute exactly (iter 22). UX note for REPORT: UBER's 'insufficient history' is decided either way (5 of 9 years positive) (`qa/evidence/iter26-val06.txt`) |
| VAL-07 | Summary | PASS | iter 22 | summary rows equal the medians of the applicable models (127 real, 19 golden; 32 with an even count); the screener's upside, verdict and Buffett verdict equal the valuation API's for every company (126, 14), and sorting by upside ranks by those numbers; the verdict rule matches the spec (code read); `qa/evidence/iter22-discovery-val.txt` |
| VAL-08 | Invariant scan over the whole real-DB copy | PASS | iter 22 | over 762 real and 114 golden rows: bear ≤ base ≤ bull, no missing/NaN/infinite value, na_reason exactly when not applicable, DCF/Lynch/owner earnings only for general/utility/energy, assumptions JSON parses; values recompute from their assumptions (oracle Level 1: 519 rows; owner earnings: 81), none beyond 0.5%; `qa/evidence/iter22-discovery-val.txt` |
| VAL-09 | Edge cases | FAIL→BUG-050 | iter 61 | code review 5's scenario-only split cases, with no current case: a post-filing split applied twice through a merger or IPO seam (BUG-047, fixed in iter 42), a pre-window split taken by a merger seam (BUG-059, fixed in iter 44) a stale pre-split price after a failed quote (BUG-048, fixed in iter 43) and its variants where the filing restated the split (BUG-060, fixed in iter 45) and where the company has no SEC annual financials (BUG-078, fixed in iter 61), each with an oracle rule first; left: post-filing factors judged all or nothing (BUG-050, P3). Iter 37's data checks hold: over 146 companies every edge case follows the spec: banks, insurers and REITs never get DCF, Lynch or owner earnings and get P/B or P/FFO; negative 3-year FCF -> DCF N/A (7); equity <= 0 -> Graham N/A (10, MCD among them); growth within its caps and Lynch's P/E within 5-25 (NVDA and 33 others capped); fewer than 4 FYs -> DCF and Lynch N/A; no beta -> 1.0 flagged; no analyst growth -> historical; splits restated inside the window agree with the oracle (32 companies); every row ordered and finite (`qa/evidence/iter37-val09.txt`). It surfaced the data gaps BUG-042..044 (GATHER-02) |
| VAL-10 | Rates | PASS | iter 36 | units, floors, caps and the ERP ✓ (iter 22); key-less runs take Treasury.gov's latest day (BUG-041); the assumptions record the rate's day and source and flag the static estimate (BUG-037); live 2026-10-19 5.15% on the VAL tab (`qa/evidence/iter36-bug037-e2e.txt`) |
| VAL-11 | Reproducibility | PASS | iter 22 | a second backfill reproduces all 889 valuation and summary rows exactly; the post-collection path (`compute_and_store` with the collected tickers) gives the backfill's rows; `qa/evidence/iter22-discovery-val.txt` |
| VAL-12 | Presentation | PASS | iter 37 | 146 companies' VAL tabs equal /api/stocks/{T}/valuation: verdict pill, median, price, upside, 579 model rows, 89 owner-earnings chips with buy-below; all 100 DCF sensitivity grids have the base at their centre and the bear and bull at their corners; the screener matches the API (iter 22) (`qa/evidence/iter37-val12.txt`) |
| VAL-13 | Hand-worked examples (a general company, a bank, a dividend payer) | PASS | iter 53 | re-worked on the golden basket collected live with the fix (iteration 53): iteration 46's hand-worked script (no `src`, no oracle) gives AAPL (general), JPM (bank) and KO (dividend payer) all 14 applicable models' bear, base and bull values and the three summaries equal to the stored ones (0.000%), the multiples steps on market closes (AAPL 146.92, JPM 158.35, KO 59.21 at FY2021's end); readings as in `qa/GOLDEN.md` (`qa/evidence/iter53-val13-handworked.txt`) |

## VALID: Data validation (parsing, standardization, quality)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| VALID-01 | Canonical mapping | TODO | iter 28 | BUG-032 fixed: the combined BasicAndDiluted EPS and share concepts fill both fields (132 values for 9 filers, none changed); the coverage_report run over the basket is untested |
| VALID-02 | Accounting identities | FAIL→BUG-062 | iter 51 | over the user's 2,312 company-years: EBITDA = operating income + D&A and FCF = OCF - |capex| hold everywhere; the cash-flow sections sum to the net change in all but 14 of 2,101 under one of two conventions (discontinued operations or FX inside the sections or not), and AMD FY2021, a later filing's sign flips, now sums (BUG-061); balance-sheet misses are temporary equity, NCI tags not mapped (BUG-063) or restated fields mixed with unrestated ones (BUG-062); gross-profit misses are presentation (excise taxes at MO and PM, Costco's membership fees, ORCL's split cost lines) or BUG-062 (DHR) (`qa/evidence/iter47-*`, `iter51-bug061-*`) |
| VALID-03 | Sign conventions | PASS | iter 47 | capex, dividends paid, buybacks, debt repaid and acquisitions are positive magnitudes in every annual, quarterly and TTM row; D&A is negative only where insurers net in accretion (ALL, PRU, KKR) and in LLY FY2007; negative quarterly D&A at CRM and seven others goes to VALID-06 (lead L19); sign flips of other fields come from later filings' tagging errors (BUG-061) (`qa/evidence/iter47-*`) |
| VALID-04 | Units and scale | FAIL→BUG-020 | iter 51 | no share-scale errors: diluted EPS x diluted shares = net income within 20% except preferred and TARP dividends (C, BAC, MS 2009), Blackstone's partnership units and IPO-year participating securities; latest FY counts match today's snapshots except six classes or post-filing splits (DELL, GOOGL, HON, BKNG, KLAC, CRWD); ratios are fractions (dividend yield = rate / price for all 106); a later filing's scale errors (STX FY2022 DPS x10^6, BRK-B FY2008 and MRK FY2010 shares) now keep the earlier filings' figure (BUG-061); quarter-sized annual DPS from filers' own tags remain (BUG-020) (`qa/evidence/iter47-*`, `iter51-bug061-*`) |
| VALID-05 | Periods | FAIL→BUG-020 | iter 15 | BUG-013 fixed (point-in-time facts no longer stand in for a duration field's year; GS FY2008 and 15 dividend years corrected); left: V/CEG/PLD's own tagging (BUG-020, P3); the rest of the item is untested |
| VALID-06 | Quarterly ladder | FAIL→BUG-092 | iter 62 | a year's rungs filed at different scales give negative or far-off quarterly counts (MCD's Q4 2021 -2.22B; BUG-092, P2); done: each quarter's per-share figures and counts are on the split basis its year's annual figures are filed on, Q4 derived on it, a trailing year on its latest quarter's (BUG-082 fixed: 28 cached filers' quarters move and no annual figure; quarterly counts more than 2x off their year's 214 -> 21, quarters bigger than their whole year 154 -> 80); a quarter's weighted-average count is its own filed 3-month figure (99.9% of 5,779 quarters match the filed ones), Q4's the share-days difference, a TTM count an average (BUG-074); a later filing's mistagged year no longer reaches Q4 or the year-end checkpoint (BUG-061); also open: 16-week fourth quarters never derived (COST, PEP; BUG-095, P2) and share-only quarters from own 3-month counts (BUG-096, P3); untested: a missing 9-month rung, 52/53-week ladders, fiscal-quarter labels, 10-Q/A amendments (`qa/evidence/iter62-bug082-*`, `iter56-bug074-*`) |
| VALID-07 | Integrity checks (`validation/integrity.py`, `quality.py`) | TODO | iter 9 | new dividend completeness check (BUG-012): of 109 dividend payers only CB FY2025 fires; README row added; the other four checks are untested |
| VALID-08 | Sector classification by SIC | TODO | |  |
| VALID-09 | Derived fields (`parsers/derived_fields.py`) only fill gaps, never overwrite reported valu… | TODO | |  |
| VALID-10 | Point-in-time | TODO | |  |
| VALID-11 | The `/quality` dashboard numbers equal direct SQL on the same DB.… | TODO | |  |
| VALID-12 | Sanity scan over the real-DB copy (read-only) | PASS | iter 51 | every hit triaged: BAC FY2008's total assets now keep the $1,818B the earlier filings report (BUG-061); BX FY2008's negative revenue (investment losses), PSA FY2022's net income above revenue (a gain on its PSB stake), early VRTX, TSLA, CRWD and PLTR margins below -100% and TSLA's P/E of 342 are real; CEG FY2020-21 (before its spin-off) and CRWD FY2020 carry zero share counts as filed; no negative prices, future dates, future bars or impossible ratios (`qa/evidence/iter47-*`; the user's stored copy keeps the 0 until BAC is re-collected, L20) |

## MATCH: Data matching and reconciliation

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| MATCH-01 | Identity | TODO | |  |
| MATCH-02 | Cross-source fundamentals | TODO | |  |
| MATCH-03 | Market math | TODO | |  |
| MATCH-04 | Price history | TODO | |  |
| MATCH-05 | Store consistency | FAIL→BUG-038 | iter 32 | after merge re-runs the JSON, CSV and SQLite agree on company fields (BUG-034 fixed, tested across all three stores) and on sections a run lacked (BUG-018); left: EV and rate differ after a failed run (BUG-038, P3) |
| MATCH-06 | UI vs DB | TODO | |  |
| MATCH-07 | Peers and sector medians are computed within the right sector and the medians are correct.… | TODO | |  |
| MATCH-08 | Dedup | TODO | |  |

## GATHER: Data gathering (`src/fetchers/*`, `main.py`, `webapp/jobs.py`)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| GATHER-01 | Yahoo | TODO | |  |
| GATHER-02 | SEC CIK resolution | FAIL→BUG-097 | iter 63 | the empty-record explanation loses the currency when one filing isn't in it (BUG-097, P3, code review 9; synthetic); done: a filer's currency is decided filing by filing (BUG-077), an ADR's ratio read against market cap / price (BUG-076), IFRS and new-registrant gaps explained (BUG-066, BUG-044, BUG-042); untested: dual-class and punctuated tickers, renamed, delisted, ETF and index cases |
| GATHER-03 | companyfacts parsing | TODO | |  |
| GATHER-04 | Rates | FAIL→BUG-052 | iter 39 | without a FRED key: Treasury.gov's latest day (BUG-041) with its day and source, and the static estimate flagged (BUG-037), checked live; the FRED-key path and January's prior-year fallback are untested; code review 5 (stubbed): any failure of the current-year request falls back to last year's year-end rate, and a blank latest 10-year gives no rate (BUG-052, P3) |
| GATHER-05 | Rate limiting and retries | TODO | |  |
| GATHER-06 | Failure modes (mocked) | FAIL→BUG-094 | iter 63 | a later run whose Yahoo quote fails (`Ticker.info` raising) stores the split and not the dividends, so DDM reads ten times its value, with 0 errors and 0 warnings (BUG-094, P2, code review 9); done: a failed SEC submissions request keeps the stored sector (BUG-064), a failed SEC request is an error (BUG-045); untested: DNS failure, 403, 404, 429, 500, malformed JSON, empty yfinance frames (`qa/evidence/iter63-review9-rerun.txt`) |
| GATHER-07 | SEC cache | TODO | |  |
| GATHER-08 | Idempotency | TODO | |  |
| GATHER-09 | Ticker input | TODO | |  |
| GATHER-10 | `--workers 1` and `--workers 4` produce identical DB content apart from timestamps.… | TODO | |  |
| GATHER-11 | Flag combinations | TODO | |  |
| GATHER-12 | Web jobs on a scratch server | TODO | |  |
| GATHER-13 | Quote refresh (`POST /api/stocks/{T}/refresh-quote`) writes only the snapshot; `STOCK_WEB_… | PASS | iter 64 | a refresh writes one market snapshot (and an analyst snapshot when Yahoo has estimates) and nothing else, in all 19 tables; after a split since the ticker's last collection with Yahoo it writes nothing and asks for a full collection (BUG-093 fixed); `STOCK_WEB_ALLOW_QUOTE_REFRESH=0` gives 409 (`tests/test_web_quote_refresh.py`, `qa/evidence/iter64-bug093-e2e.txt`) |

## STORE: Persistence and queries

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| STORE-01 | Schema migrations applied to the real-DB copy are idempotent and non-destructive (row coun… | FAIL→BUG-098 | iter 63 | two first runs at once after an upgrade: the loser's whole batch fails on a duplicate column, and only a log line says so (BUG-098, P3, code review 9; 1 of 3 tries); done: the flag migration is atomic and survives hard kills (BUG-083); untested: row counts unchanged by every migration on the real-DB copy (`qa/evidence/iter63-review9-rerun.txt`) |
| STORE-02 | A collection killed mid-run on scratch leaves a DB that opens, with `PRAGMA integrity_chec… | TODO | iter 60 | done: the first export's migration killed (TerminateProcess) at every moment tried leaves a whole copy of the user's data, with no column or every bar flagged, and `integrity_check` ok (BUG-083 fixed; the unfixed code left it torn); untested: a kill during a multi-ticker export's writes, and during the fetches (`qa/evidence/iter60-bug083-e2e.txt`) |
| STORE-03 | WAL handling | TODO | |  |
| STORE-04 | The web Reader (`mode=ro`) never writes; per-request connections survive threadpool hoppin… | PASS | iter 54 | the Reader opens `mode=ro` under any folder name (BUG-080 fixed: '#', '%41' and, off Windows, '?' tested; local and UNC paths probed) and rejects writes; in the real app a database served under `web#1` keeps its 19 tables and 220,663 rows unchanged and nothing appears beside it; per-request connections survive threadpool hopping (`tests/test_web_repository.py::test_reader_survives_threadpool_hopping`) (`qa/evidence/iter54-bug080-{realapp,uri-forms}.txt`) |
| STORE-05 | The as-of API and UI equal `AsOfReader`; edge dates (before the first filing, today, the f… | TODO | |  |
| STORE-06 | CSV exports | FAIL→BUG-027 | iter 24 | BUG-004, -015, -018 and -021 fixed; left: kept copies can collide within a second (BUG-027, P3); also an empty-string company field reaches JSON and SQLite but not the CSV (BUG-055, P3; iter 39) |
| STORE-07 | The real-DB copy passes `PRAGMA integrity_check` and `foreign_key_check`.… | TODO | | Bootstrap: integrity_check ok on realcopy, but the schema declares no foreign keys (0 of 19 tables), so foreign_key_check is vacuous; needs an orphan-row join check. |

## API: HTTP API (`/docs`, every `/api/*` route)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| API-01 | The OpenAPI schema is valid, every route responds, and responses match their declared mode… | TODO | |  |
| API-02 | Status codes on every error path (400/404/409/422) with consistent JSON error bodies; no 5… | TODO | |  |
| API-03 | Limit, offset, sort and filter params at their bounds | TODO | |  |
| API-04 | Every CSV export equals its JSON endpoint.… | TODO | |  |
| API-05 | `/api/health` reflects the DB state, including a missing DB.… | TODO | |  |
| API-06 | Collection API validation | TODO | |  |

## SEC: Security

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| SEC-01 | SQL injection through screener filters and sort, search `q`, and ticker path params | TODO | |  |
| SEC-02 | XSS | TODO | |  |
| SEC-03 | Path traversal via `/static/..` and export paths; the ticker regex enforced on every route… | TODO | |  |
| SEC-04 | Cross-site requests | TODO | |  |
| SEC-05 | Binding | TODO | |  |
| SEC-06 | Secrets | TODO | |  |
| SEC-07 | Known-vulnerable dependencies | TODO | | pip-audit not installed; run it from a scratch venv (rule 10). |

## UI: Web UI/UX (Claude in Chrome on a scratch-DB server, port 8765)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| UI-01 | Every page and tab for AAPL, JPM, a REIT, an insurer, a sparse ticker, a non-payer and a t… | TODO | |  |
| UI-02 | Command palette | TODO | |  |
| UI-03 | The HELP overlay lists exactly the codes the palette and the TABS registry support.… | TODO | |  |
| UI-04 | Screener | TODO | |  |
| UI-05 | Charts | TODO | |  |
| UI-06 | Watchlist | TODO | |  |
| UI-07 | UPDATE DATA and fetch-page flows on scratch | TODO | |  |
| UI-08 | Empty, error and loading states | TODO | |  |
| UI-09 | Responsive layout at widths 375, 768, 1280 and 1920 (`resize_window`) | TODO | |  |
| UI-10 | Accessibility | TODO | |  |
| UI-11 | Formatting | TODO | |  |
| UI-12 | Performance | TODO | |  |
| UI-13 | CDN dependencies (Plotly, htmx, Google Fonts) | TODO | |  |
| UI-14 | Copy and methodology | TODO | |  |
| UI-15 | Heuristic review (Nielsen's 10) | TODO | |  |

## CLI: Command line and scripts

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| CLI-01 | `stock-data --help` matches the README; every flag works; exit codes for success, partial… | TODO | |  |
| CLI-02 | `python -m src.valuation.backfill [--db PATH] [TICKERS…]`, including an unknown ticker and… | TODO | |  |
| CLI-03 | `python diagnose.py TICKER -v` (reads `data/output/json/`, so run it read-only or from a s… | TODO | |  |
| CLI-04 | Ctrl+C mid-collection | TODO | |  |

## CODE: Engineering health

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| CODE-01 | Gates match CI (ruff, mypy, pytest); no Python 3.10+ syntax anywhere (CI's minimum is 3.9)… | TODO | |  |
| CODE-02 | Test-suite health | TODO | |  |
| CODE-03 | Coverage gaps | TODO | | coverage 7.13 is installed; baseline report in qa/evidence/iter01-baseline-coverage.txt. |
| CODE-04 | Error handling | TODO | |  |
| CODE-05 | Type bugs in modules mypy doesn't check | TODO | |  |
| CODE-06 | Numerical robustness | TODO | |  |
| CODE-07 | Concurrency | TODO | |  |
| CODE-08 | Resource leaks | TODO | |  |
| CODE-09 | Config and env | TODO | |  |
| CODE-10 | Cross-platform | TODO | |  |
| CODE-11 | Deprecations | TODO | |  |
| CODE-12 | Logging | TODO | |  |

## PKG: Packaging and install

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| PKG-01 | In a scratch venv, a non-editable `pip install .` then `stock-web` from another directory… | TODO | |  |
| PKG-02 | Entry points `stock-data` and `stock-web` resolve outside the repo; `find_packages` includ… | TODO | |  |
| PKG-03 | `pip install -e ".[dev,web]"` in a clean venv; the dev extras are enough to run the gates.… | TODO | |  |

## OPS: Reliability and operations

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| OPS-01 | After 1000 mixed requests | TODO | |  |
| OPS-02 | Startup with a missing, locked or corrupt DB gives a clear error message.… | TODO | |  |
| OPS-03 | Time handling | TODO | |  |
| OPS-04 | Scale | TODO | |  |

## DOCS: Documentation accuracy

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| DOCS-01 | README | TODO | |  |
| DOCS-02 | USAGE_GUIDE | PASS | iter 2 | `tests/test_docs_usage_guide.py` runs every block against the current export; RED→GREEN in `qa/evidence/iter02-bug001-*` (BUG-001) |
| DOCS-03 | Quantitative claims (e.g. "100% core coverage for a 41-company basket") are reproducible.… | TODO | |  |
| DOCS-04 | In-app help, tooltips and the HELP overlay match actual behavior.… | TODO | |  |

## UX: Evidence-backed improvements

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| UX-01 | Gather the improvement ideas found during testing into `qa/REPORT.md` as proposals, each w… | TODO | |  |
