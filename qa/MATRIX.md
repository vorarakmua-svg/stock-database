# QA Test Matrix

Generated from `qa/LOOP_PROMPT.md` §6. Full test method and pass criterion for each ID live there.

**Status counts:** TODO 99 · PASS 5 · FAIL 3 · N-A 0 · BLOCKED 0 · NEEDS-USER 2  
**Consecutive clean sweeps:** 0

Status values: `TODO | PASS | FAIL→BUG-n | N-A (reason) | BLOCKED (why) | NEEDS-USER`

## VAL: Financial valuation correctness (highest priority)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| VAL-01 | DCF (two-stage FCF) | NEEDS-USER | iter 20 | Level 1 ✓ and Level 2 ✓ on every derivable DCF row, with every share class counted (BUG-022 fixed); left: the rows without a measurable CAGR follow the plan's fallback, which the spec doesn't define (Q5) |
| VAL-02 | DDM (multi-stage) | NEEDS-USER | iter 16 | BUG-005, -009, -010, -011 and -017 fixed; the oracle agrees on every derivable DDM row. Left for the user: growth when no CAGR is measurable (Q5, 7 rows), CB's missing payments (Q4), special dividends (Q3) |
| VAL-03 | Graham Number | FAIL→BUG-007 | iter 11 | Level 1 ✓; BUG-014 fixed (KLAC on today's basis; oracle agrees); left: the bear case ignores loss years (BUG-007, NEEDS-USER) and XOM's Level-2 mismatch (lead L6) |
| VAL-04 | Peter Lynch | TODO | iter 13 | BUG-006 and BUG-014 fixed; BUG-016 restored the plan's rule, so 16 Lynch rows use the analyst figure again (not derivable from the spec: Q5); left: SCCO and DELL Level-2 mismatches (leads L6, L7) |
| VAL-05 | Historical multiples band | TODO | iter 11 | Level 1 ✓; BUG-006 resolved ANET/APH/AVGO/LRCX; SCCO, SPGI and WDC were the oracle's spin-off handling (fixed in iteration 11); NOW remains: lead L6 |
| VAL-06 | Owner earnings | TODO | iter 22 | values recompute exactly from their assumptions with the oracle's projection (81 real, 8 golden; `qa/evidence/iter22-discovery-val.txt`); every share class counted (BUG-022); the predictability gate, volatility fields and own verdict are untested |
| VAL-07 | Summary | PASS | iter 22 | summary rows equal the medians of the applicable models (127 real, 19 golden; 32 with an even count); the screener's upside, verdict and Buffett verdict equal the valuation API's for every company (126, 14), and sorting by upside ranks by those numbers; the verdict rule matches the spec (code read); `qa/evidence/iter22-discovery-val.txt` |
| VAL-08 | Invariant scan over the whole real-DB copy | PASS | iter 22 | over 762 real and 114 golden rows: bear ≤ base ≤ bull, no missing/NaN/infinite value, na_reason exactly when not applicable, DCF/Lynch/owner earnings only for general/utility/energy, assumptions JSON parses; values recompute from their assumptions (oracle Level 1: 519 rows; owner earnings: 81), none beyond 0.5%; `qa/evidence/iter22-discovery-val.txt` |
| VAL-09 | Edge cases | TODO | iter 8 | partial first dividend year covered (BUG-009 tests); rest untested |
| VAL-10 | Rates | PASS | iter 22 | risk-free rates stored as decimals (0.0419–0.0475); every DCF/DDM discount equals clamp(rf + β × 4.5%, 8%, 14%) with the snapshot's rf (196 real, 25 golden); fallbacks flagged (`rf_fallback`/`beta_fallback`); owner earnings uses max(rf, 7%); the ERP is in the spec and in every assumptions record; `qa/evidence/iter22-discovery-val.txt` |
| VAL-11 | Reproducibility | PASS | iter 22 | a second backfill reproduces all 889 valuation and summary rows exactly; the post-collection path (`compute_and_store` with the collected tickers) gives the backfill's rows; `qa/evidence/iter22-discovery-val.txt` |
| VAL-12 | Presentation | TODO | iter 22 | the screener's upside, verdict and Buffett verdict equal the API's (see VAL-07); the VAL tab and the DCF sensitivity grid are untested |
| VAL-13 | Hand-worked examples (a general company, a bank, a dividend payer) | TODO | |  |

## VALID: Data validation (parsing, standardization, quality)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| VALID-01 | Canonical mapping | TODO | |  |
| VALID-02 | Accounting identities | TODO | |  |
| VALID-03 | Sign conventions | TODO | |  |
| VALID-04 | Units and scale | TODO | |  |
| VALID-05 | Periods | FAIL→BUG-020 | iter 15 | BUG-013 fixed (point-in-time facts no longer stand in for a duration field's year; GS FY2008 and 15 dividend years corrected); left: V/CEG/PLD's own tagging (BUG-020, P3); the rest of the item is untested |
| VALID-06 | Quarterly ladder | TODO | |  |
| VALID-07 | Integrity checks (`validation/integrity.py`, `quality.py`) | TODO | iter 9 | new dividend completeness check (BUG-012): of 109 dividend payers only CB FY2025 fires; README row added; the other four checks are untested |
| VALID-08 | Sector classification by SIC | TODO | |  |
| VALID-09 | Derived fields (`parsers/derived_fields.py`) only fill gaps, never overwrite reported valu… | TODO | |  |
| VALID-10 | Point-in-time | TODO | |  |
| VALID-11 | The `/quality` dashboard numbers equal direct SQL on the same DB.… | TODO | |  |
| VALID-12 | Sanity scan over the real-DB copy (read-only) | TODO | |  |

## MATCH: Data matching and reconciliation

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| MATCH-01 | Identity | TODO | |  |
| MATCH-02 | Cross-source fundamentals | TODO | |  |
| MATCH-03 | Market math | TODO | |  |
| MATCH-04 | Price history | TODO | |  |
| MATCH-05 | Store consistency | TODO | iter 17 | BUG-004, BUG-015 and BUG-018 fixed (the stores accumulate across runs, survive Excel re-saves, and keep data a failed re-collection didn't get); JSON vs SQLite vs CSV values untested |
| MATCH-06 | UI vs DB | TODO | |  |
| MATCH-07 | Peers and sector medians are computed within the right sector and the medians are correct.… | TODO | |  |
| MATCH-08 | Dedup | TODO | |  |

## GATHER: Data gathering (`src/fetchers/*`, `main.py`, `webapp/jobs.py`)

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| GATHER-01 | Yahoo | TODO | |  |
| GATHER-02 | SEC CIK resolution | TODO | |  |
| GATHER-03 | companyfacts parsing | TODO | |  |
| GATHER-04 | Rates | TODO | |  |
| GATHER-05 | Rate limiting and retries | TODO | |  |
| GATHER-06 | Failure modes (mocked) | TODO | iter 18 | BUG-018 and BUG-026 fixed (a failed re-collection keeps stored data; a locked summary.csv no longer stops the SQLite export); the rest of the item is untested |
| GATHER-07 | SEC cache | TODO | |  |
| GATHER-08 | Idempotency | TODO | |  |
| GATHER-09 | Ticker input | TODO | |  |
| GATHER-10 | `--workers 1` and `--workers 4` produce identical DB content apart from timestamps.… | TODO | |  |
| GATHER-11 | Flag combinations | TODO | |  |
| GATHER-12 | Web jobs on a scratch server | TODO | |  |
| GATHER-13 | Quote refresh (`POST /api/stocks/{T}/refresh-quote`) writes only the snapshot; `STOCK_WEB_… | TODO | |  |

## STORE: Persistence and queries

| ID | Check | Status | Last run | Evidence / notes |
|---|---|---|---|---|
| STORE-01 | Schema migrations applied to the real-DB copy are idempotent and non-destructive (row coun… | TODO | |  |
| STORE-02 | A collection killed mid-run on scratch leaves a DB that opens, with `PRAGMA integrity_chec… | TODO | |  |
| STORE-03 | WAL handling | TODO | |  |
| STORE-04 | The web Reader (`mode=ro`) never writes; per-request connections survive threadpool hoppin… | TODO | |  |
| STORE-05 | The as-of API and UI equal `AsOfReader`; edge dates (before the first filing, today, the f… | TODO | |  |
| STORE-06 | CSV exports | FAIL→BUG-027 | iter 19 | BUG-004, BUG-015, BUG-018 and BUG-021 fixed (the CSVs accumulate, survive Excel re-saves in any form, and keep data a failed run didn't get); left: kept copies can collide within a second (BUG-027, P3) |
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
