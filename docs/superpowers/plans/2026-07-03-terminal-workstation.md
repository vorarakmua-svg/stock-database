# Terminal Workstation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bloomberg-terminal-style UX (dark reskin + command line + per-stock workstation) over an expanded SQLite store that adds price bars, quotes, analyst/earnings, dividends/splits, holders/insider, and profile data.

**Architecture:** Approach A from the spec (`docs/superpowers/specs/2026-07-03-terminal-workstation-design.md`): extend SQLite as the single source of truth — new tables + widened `market_snapshots`/`companies` written by `SQLiteStore` from data the Yahoo handler (mostly) already fetches; webapp `Reader` + `/api/stocks/*` endpoints on top; server-side pandas indicators; full terminal reskin with a command bar; quote refresh runs through the existing single-writer `CollectionJobManager`.

**Tech Stack:** Python 3.9+, FastAPI + Jinja2 + HTMX + Plotly (CDN, no build step), SQLite (WAL, `mode=ro` readers), pandas, yfinance.

## Global Constraints

- Python **3.9** compatible: every new/modified module starts with `from __future__ import annotations`; use `typing.Optional/List/Dict/Any/Tuple/Sequence/Iterator/FrozenSet` — never runtime `X | None`.
- Ruff (E/F/W/I, 120 cols) on `src/ tests/`; **bare** `mypy` must stay clean (`src/webapp` and `src/exporters/sqlite_store.py`, `src/query/*` are in the `files` list). Verify mypy **after upgrading web deps** (`python -m pip install -U starlette fastapi`) — CI installs fresh (see memory: webapp-ci-version-drift).
- SQL security: column names interpolated into SQL come ONLY from module-level whitelists (`_CANONICAL_COLUMNS`/`_METRIC_COLUMNS`/`_SNAPSHOT_COLUMNS`/new lists defined in tasks); ALL user-supplied values are bound `?` parameters. `ValueError` on non-whitelisted names, mapped to 400 in routes.
- Single-writer rule: web request threads never open the DB writable; only the `CollectionJobManager` worker (max_workers=1) writes.
- Tests: NO network; extend the `web_db` fixture; pristine output; run full gate (`ruff check src/ tests/`, bare `mypy`, `pytest -q`) before every commit.
- Routes read via `Depends(get_reader)`; SQL only in `repository.py`/`screener.py`/`sqlite_store.py`; number formatting via `src/webapp/formatting.py` helpers (Jinja globals already registered).
- Do NOT `git add` anything under `.superpowers/`.
- Migration safety: schema changes are `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN` only — never DROP/rewrite; an existing `stock.db` must upgrade in place losslessly.
- `^GSPC` benchmark exists ONLY in `price_bars` — never in `companies` or any listing/screen.

## File Structure

```
src/mappings? (none)                      src/exporters/sqlite_store.py   MODIFY: migration helper, widened _SNAPSHOT_COLUMNS,
src/models/stock_data.py  MODIFY: +3 fields                              8 new tables, new export writes
src/fetchers/yahoo_handler.py  MODIFY: +3 fetches, pre/post market      src/fetchers/stock_data_fetcher.py  MODIFY: benchmark bars
src/webapp/indicators.py   CREATE: MA/RSI/MACD/normalize (pure pandas)
src/webapp/repository.py   MODIFY: +10 Reader methods                   src/webapp/schemas.py  MODIFY: quote/bars/etc models
src/webapp/routes/stocks_api.py  CREATE: /api/stocks/* JSON             src/webapp/routes/workstation.py  CREATE: /stocks/{t} pages + /ui fragments
src/webapp/routes/pages.py MODIFY: redirect old page, restyle hooks     src/webapp/jobs.py  MODIFY: quote mode
src/webapp/routes/collection_api.py + export_api.py  MODIFY: refresh endpoint, bars/dividends CSV
src/webapp/settings.py     MODIFY: allow_quote_refresh                  src/webapp/app.py  MODIFY: wire routers
src/webapp/static/app.css  REWRITE: terminal theme                      src/webapp/static/terminal.js  CREATE: command bar
src/webapp/templates/…     MODIFY all + CREATE stock.html, fragments/{des,gp,ern,stat,hp,dvd,hds,ins}.html, help.html
src/webapp/screener.py     MODIFY: snapshot-column whitelist            tests/: test_store_migration.py, test_web_indicators.py,
                                                                        test_web_api_stocks.py, test_web_workstation.py,
                                                                        test_web_quote_refresh.py (+ extend conftest web_db)
```

---

### Task 1: Store migration + new tables + export writes (already-fetched data)

**Files:**
- Modify: `src/exporters/sqlite_store.py`
- Test: `tests/test_store_migration.py`

**Interfaces:**
- Produces: `_ensure_columns(conn, table: str, columns: Dict[str, str]) -> None` (PRAGMA table_info + ALTER ADD COLUMN for missing); widened `_SNAPSHOT_COLUMNS` (REAL) + new `_SNAPSHOT_TEXT_COLUMNS = ["ex_dividend_date"]`; tables `analyst_snapshots`, `dividend_events`, `holders`, `insider_transactions`, `officers` (DDL below); `companies` gains TEXT cols `description, address, hq_city, hq_state`.
- Consumes: existing `StockData` fields `market_data`, `valuation`, `shareholders`, `analyst_estimates`, `dividend_history`, `company_info` (read `src/fetchers/yahoo_handler.py` for exact keys).

**Schema (exact):**

```sql
CREATE TABLE IF NOT EXISTS analyst_snapshots (
    ticker TEXT NOT NULL, collected_at TEXT NOT NULL,
    target_price_low REAL, target_price_mean REAL, target_price_median REAL, target_price_high REAL,
    recommendation TEXT, recommendation_mean REAL, number_of_analysts INTEGER,
    earnings_date TEXT, forward_eps REAL, forward_pe REAL,
    earnings_growth REAL, revenue_growth REAL, upside_potential REAL,
    PRIMARY KEY (ticker, collected_at));
CREATE TABLE IF NOT EXISTS dividend_events (
    ticker TEXT NOT NULL, date TEXT NOT NULL, amount REAL, PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS holders (
    ticker TEXT NOT NULL, holder_type TEXT NOT NULL, holder TEXT NOT NULL,
    shares REAL, date_reported TEXT, pct_held REAL, value REAL, collected_at TEXT,
    PRIMARY KEY (ticker, holder_type, holder));
CREATE TABLE IF NOT EXISTS insider_transactions (
    ticker TEXT NOT NULL, insider TEXT NOT NULL, start_date TEXT NOT NULL, text TEXT NOT NULL,
    position TEXT, shares REAL, value REAL, ownership TEXT, collected_at TEXT,
    PRIMARY KEY (ticker, insider, start_date, text));
CREATE TABLE IF NOT EXISTS officers (
    ticker TEXT NOT NULL, name TEXT NOT NULL, title TEXT, age INTEGER, total_pay REAL,
    PRIMARY KEY (ticker, name));
```

`_SNAPSHOT_COLUMNS` additions (REAL): `previous_close, open, day_high, day_low, volume, avg_volume, avg_volume_10d, fifty_two_week_high, fifty_two_week_low, ma_50, ma_200, post_market_price, pre_market_price, peg_ratio, price_to_sales, eps_forward, dividend_rate, payout_ratio, debt_to_equity, current_ratio, quick_ratio, shares_outstanding, float_shares, shares_short, shares_short_prior_month, short_ratio, short_percent_of_float, insider_percent, institutional_percent`.

Export behavior: snapshot row = merged `{**market_data, **valuation, **shareholders}` picked by column lists (existing pattern). `holders`: DELETE ticker's rows then INSERT from `shareholders["institutional_holders"]` (type `institutional`) and `["mutualfund_holders"]` (type `mutualfund`) — normalize keys defensively (`Holder`, `Shares`, `Date Reported`, `pctHeld`, `Value` — verify against `_df_to_list` output; skip records missing a holder name). `insider_transactions`: upsert on the 4-part key from `shareholders["insider_transactions"]` (keys like `Insider`, `Position`, `Start Date`, `Shares`, `Value`, `Text`, `Ownership`; coerce missing text to `""`). `officers`: replace-per-run from `company_info["officers"]`. `analyst_snapshots`: one row per collection from `analyst_estimates`. `dividend_events`: upsert all of `dividend_history["dividend_payments"]` (`{date, amount}`). `companies` extra fields from `company_info` (`description`/`longBusinessSummary`, `address1`→address, `city`→hq_city, `state`→hq_state — verify keys in `_get_company_info`).

- [ ] **Step 1: Write failing tests** in `tests/test_store_migration.py`: (a) build a DB with the CURRENT schema by copying the old `_SNAPSHOT_COLUMNS` (hardcode the original 13-column list in the test) and creating `market_snapshots` manually, insert one row; run `SQLiteStore(path).export([stock_data_fixture])`; assert new columns exist (`PRAGMA table_info`), old row preserved, new tables exist. (b) export writes holders/insider/officers/analyst/dividend rows from a `StockData` populated with the shapes above (crib fixture-building from `tests/test_sqlite_store.py`). (c) `_ensure_columns` is idempotent (run twice, no error, no dup columns).
- [ ] **Step 2: Run** `pytest tests/test_store_migration.py -q` — FAIL (no `_ensure_columns`, missing tables).
- [ ] **Step 3: Implement** in `sqlite_store.py` (migration helper, DDL, export writes as specified).
- [ ] **Step 4: Full gate** (`ruff check src/ tests/` + bare `mypy` + `pytest -q`) — PASS, all existing tests still green.
- [ ] **Step 5: Commit** `feat(store): migration helper, widened snapshots, analyst/dividend/holders/insider/officers tables`

### Task 2: Pipeline — price bars, earnings history, splits, pre/post-market, ^GSPC benchmark

**Files:**
- Modify: `src/fetchers/yahoo_handler.py`, `src/models/stock_data.py`, `src/exporters/sqlite_store.py`, `src/fetchers/stock_data_fetcher.py`
- Test: `tests/test_store_migration.py` (extend), `tests/test_yahoo_new_sections.py`

**Interfaces:**
- Produces: `StockData.price_bars: List[Dict[str, Any]]` (`{date, open, high, low, close, volume}` ISO date, ascending), `StockData.earnings_history: List[Dict[str, Any]]` (`{quarter, eps_estimate, eps_actual, surprise_pct}`), `StockData.splits: List[Dict[str, Any]]` (`{date, ratio}`); tables `price_bars` (PK ticker,date; open/high/low/close REAL, volume REAL), `earnings_history` (PK ticker,quarter), `split_events` (PK ticker,date); `SQLiteStore.export_benchmark_bars(symbol: str, bars: List[Dict[str, Any]]) -> None`; `YahooHandler.fetch_benchmark_bars(symbol: str = "^GSPC") -> List[Dict[str, Any]]`.
- Consumes: Task 1's `_ensure_columns`/table patterns.

Implementation notes: add `_get_price_bars(stock)` (uses `stock.history(period="max", interval="1d")`, converts index to ISO dates), `_get_earnings_history(stock)` (`stock.earnings_history` DataFrame → records; columns vary — extract `epsEstimate`/`epsActual`/`surprisePercent` defensively, quarter = index/`quarter` column as ISO date), `_get_splits(stock)` (`stock.splits` Series → `{date, ratio}`) into `fetch_all` (reuses rate limiter + one `yf.Ticker`). Add `post_market_price: info.get("postMarketPrice")`, `pre_market_price: info.get("preMarketPrice")` to `_get_market_data`. `StockDataFetcher.fetch_and_export` (or `export`) calls `yahoo.fetch_benchmark_bars()` once per run and `store.export_benchmark_bars("^GSPC", bars)` — wrap in try/except (benchmark failure must never fail a run). All new sections tolerate-and-log missing data (return `[]`).

- [ ] **Step 1: Failing tests** — `tests/test_yahoo_new_sections.py`: monkeypatch `yf.Ticker` with a fake exposing `history()`/`earnings_history`/`splits`/`info`; assert `fetch_all` output contains the three new keys with normalized shapes (no network). Extend `tests/test_store_migration.py`: export a `StockData` with 30 synthetic bars + 4 earnings rows + 1 split → rows land in the three tables; `export_benchmark_bars("^GSPC", bars)` writes bars and does NOT create a `companies` row.
- [ ] **Step 2: Run both test files** — FAIL.
- [ ] **Step 3: Implement**; **Step 4: full gate PASS**; 
- [ ] **Step 5: Commit** `feat(pipeline): price bars, earnings history, splits, pre/post-market, ^GSPC benchmark`

### Task 3: Reader methods + response schemas

**Files:**
- Modify: `src/webapp/repository.py`, `src/webapp/schemas.py`, `tests/conftest.py`
- Test: `tests/test_web_repository.py` (extend)

**Interfaces (produces — exact signatures):**
```python
Reader.quote(ticker: str) -> Optional[Dict[str, Any]]            # latest market_snapshots row + computed: change, change_pct (vs previous_close; None-safe)
Reader.price_bars(ticker: str, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict[str, Any]]   # ascending by date
Reader.analyst_snapshot(ticker: str) -> Optional[Dict[str, Any]] # latest by collected_at
Reader.earnings_history(ticker: str) -> List[Dict[str, Any]]     # ascending by quarter
Reader.dividend_events(ticker: str) -> List[Dict[str, Any]]      # ascending by date
Reader.split_events(ticker: str) -> List[Dict[str, Any]]         # ascending by date
Reader.holders(ticker: str, holder_type: str) -> List[Dict[str, Any]]   # ValueError unless holder_type in {"institutional","mutualfund"}; desc by pct_held
Reader.insider_transactions(ticker: str, limit: int = 100) -> List[Dict[str, Any]]  # desc by start_date
Reader.profile(ticker: str) -> Optional[Dict[str, Any]]          # {"company": companies row, "officers": [...]}; None if unknown ticker
```
`conftest.py` `web_db` gains: ≥260 daily bars for AAA (deterministic synthetic walk — enough for MA200), 30 bars for `^GSPC`, 1 analyst snapshot, 4 earnings rows (mixed beat/miss), 6 dividend events + 1 split, 3 institutional + 2 mutualfund holders, 3 insider transactions, 2 officers, AAA description/address. Schemas: `QuoteOut`, `BarOut(date, open, high, low, close, volume)`, `AnalystOut`, `EarningsRow`, `DividendEvent`, `SplitEvent`, `HolderRow`, `InsiderRow`, `ProfileOut` — small models; full rows stay `Dict[str, Any]` where wide.

- [ ] **Step 1: Failing tests** — extend `tests/test_web_repository.py`: each method against the enriched fixture (values, ordering, `holders` ValueError guard, unknown-ticker → None/[]).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (parameterized SQL only). **Step 4: full gate PASS.**
- [ ] **Step 5: Commit** `feat(webapp): Reader methods + schemas for quote/bars/analyst/earnings/dividends/holders/insiders/profile`

### Task 4: Indicators service (pure pandas)

**Files:**
- Create: `src/webapp/indicators.py`
- Test: `tests/test_web_indicators.py`

**Interfaces (produces):**
```python
def moving_average(closes: List[float], window: int) -> List[Optional[float]]     # simple MA, None until warm
def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]           # Wilder smoothing, 0..100, None until warm
def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, List[Optional[float]]]  # {"macd","signal","hist"}
def normalize_pct(closes: List[float]) -> List[Optional[float]]                   # (c/first_non_null - 1); for compare overlays
def indicator_bundle(bars: List[Dict[str, Any]]) -> Dict[str, Any]                # {"dates":[...], "close":[...], "ma_50":[...], "ma_200":[...], "rsi":[...], "macd":{...}}
```
Implementation: pandas Series; MA = `rolling(window).mean()`; RSI = Wilder (`ewm(alpha=1/period, adjust=False)` over up/down moves); MACD = `ewm(span=fast).mean() - ewm(span=slow).mean()`, signal = `ewm(span=signal)` of macd. NaN → None in output lists.

- [ ] **Step 1: Failing tests** with exact expectations: `moving_average([1..10], 5)` → `[None]*4 + [3.0,4.0,5.0,6.0,7.0,8.0]`; RSI bounds 0–100 with strictly-rising series → RSI > 70 after warmup and strictly-falling → < 30; flat series → 50±ε or None (assert not-crash + bounded); MACD of a linearly rising series → macd > 0 after warmup and `hist == macd - signal` elementwise (±1e-9); `normalize_pct([100,110,90])` → `[0.0, 0.10, -0.10]`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: full gate PASS.**
- [ ] **Step 5: Commit** `feat(webapp): pandas indicator service (MA/RSI/MACD/normalize)`

### Task 5: /api/stocks router + CSV exports

**Files:**
- Create: `src/webapp/routes/stocks_api.py`
- Modify: `src/webapp/app.py` (include router), `src/webapp/routes/export_api.py`
- Test: `tests/test_web_api_stocks.py`

**Endpoints** (router prefix `/api/stocks`, all via `Depends(get_reader)`):

| Method/Path | Params | Returns / semantics |
|---|---|---|
| GET `/{ticker}/quote` | — | QuoteOut-ish dict; **404** unknown ticker (via `get_company`), 404 detail "no quote" if known but no snapshot |
| GET `/{ticker}/bars` | `range` ∈ {1M,3M,6M,YTD,1Y,5Y,MAX} (default 1Y); `interval` ∈ {auto,1d,1wk,1mo} (default auto: 5Y→1wk, MAX→1mo, else 1d) | `[BarOut]`; range resolved to a start date vs today; weekly/monthly bars via pandas `resample` (O=first, H=max, L=min, C=last, V=sum); invalid range/interval → 400 |
| GET `/{ticker}/indicators` | `range` (same) | `indicator_bundle` computed on FULL history then sliced to range (so MA200 is correct at range start) |
| GET `/{ticker}/compare-bars` | `others` = comma tickers (may include `^GSPC`), `range` | `{"series": {ticker: {"dates": [...], "pct": [...]}, ...}}` normalized per series |
| GET `/{ticker}/analyst` `/earnings` `/dividends` `/splits` `/profile` | — | dict/list; profile 404 on unknown ticker; others `[]`/404-on-none matching Reader |
| GET `/{ticker}/holders` | `type` ∈ {institutional,mutualfund} (default institutional) | `[HolderRow]`; bad type → 400 (ValueError map) |
| GET `/{ticker}/insiders` | `limit` (default 100) | `[InsiderRow]` |

`export_api.py` additions: `GET /api/export/stock/{ticker}/bars.csv?range=` and `/dividends.csv` — 404 unknown ticker (existing `get_company` guard pattern), text/csv attachment.

- [ ] **Step 1: Failing tests**: shapes + values against fixture; range slicing (1M returns subset); indicators endpoint MA warm at slice start (ma_50 non-None on first returned point when full history suffices); compare includes `^GSPC`; 400s (bad range, bad holder type); 404s (unknown ticker quote/profile/CSV); CSV parses with header.
- [ ] **Step 2: RED. Step 3: implement. Step 4: full gate PASS.**
- [ ] **Step 5: Commit** `feat(webapp): /api/stocks endpoints (quote/bars/indicators/compare/analyst/earnings/dividends/holders/insiders/profile) + CSV`

### Task 6: Terminal reskin + command bar

**Files:**
- Rewrite: `src/webapp/static/app.css` (terminal theme, keep class names so templates mostly untouched)
- Create: `src/webapp/static/terminal.js`, `src/webapp/templates/fragments/help.html`
- Modify: `src/webapp/templates/base.html` (command bar markup, HELP trigger, load terminal.js), light class tweaks in other templates as needed
- Test: `tests/test_web_smoke.py` (extend)

**Interfaces:**
- Produces: command grammar (client-side): input `[TICKER] [CODE]` case-insensitive. CODE→URL map: DES→`/stocks/{t}`, GP→`/stocks/{t}?tab=gp`, FA→`?tab=fa`, ERN→`?tab=ern`, STAT→`?tab=stat`, HP→`?tab=hp`, DVD→`?tab=dvd`, HDS→`?tab=hds`, INS→`?tab=ins`; bare TICKER→DES; global codes (no ticker): SCR→`/screener`, ASOF→`/asof`, QM→`/quality`, COL→`/collect`, HELP→toggle overlay. Unknown single token that isn't a global code → treat as ticker. (Workstation routes arrive in Task 7 — command bar may 404 until then; acceptable inside this branch.)
- Theme tokens: bg `#0a0a0a`, panel `#141414`, border `#2a2a2a`, text `#e6e3dc`, accent `#ff9900` (headers, focus, links), up `#00e676`, down `#ff5252`, muted `#8a8a8a`; monospace stack `"IBM Plex Mono", "Cascadia Mono", Consolas, monospace` for ALL numerics + labels; uppercase 11px panel headers; dense rows (4–6px vertical padding); `tabular-nums` everywhere.

Command bar markup in `base.html`: `<input id="cmd" placeholder="TICKER FUNCTION  ·  e.g. AAPL GP  ·  ` or / to focus  ·  HELP">` fixed top, amber caret; `terminal.js`: keydown ``` ` ```/`/` focuses (unless typing in an input), Enter parses+navigates, Escape clears/closes HELP; ticker autocomplete reuses `GET /api/companies/search?q=` rendering a dropdown under the bar; HELP overlay = static `fragments/help.html` include toggled by JS listing every code.

- [ ] **Step 1: Failing tests** (extend smoke): `/` still 200 and body contains `id="cmd"` and `terminal.js`; help fragment content present in base (or fetched route 200); all existing pages still 200 (reskin must not break rendering).
- [ ] **Step 2: RED. Step 3: implement** (CSS rewrite ~250–350 lines; keep selectors compatible). **Step 4: full gate PASS** + eyeball via `stock-web` locally.
- [ ] **Step 5: Commit** `feat(webapp): terminal reskin + command bar with function codes + HELP`

### Task 7: Workstation shell + DES + GP

**Files:**
- Create: `src/webapp/routes/workstation.py`, `src/webapp/templates/stock.html`, `src/webapp/templates/fragments/des.html`, `src/webapp/templates/fragments/gp.html`
- Modify: `src/webapp/app.py` (include router), `src/webapp/static/app.js` (chart renderers)
- Test: `tests/test_web_workstation.py`

**Interfaces:**
- Produces: `GET /stocks/{ticker}` (page; 404 unknown; `?tab=` picks initial tab, default `des`) renders `stock.html`: header strip (name, ticker, sector) + tab bar (DES GP FA ERN STAT HP DVD HDS INS) where each tab button is `hx-get="/ui/stocks/{t}/{tab}" hx-target="#panel"`; initial tab loads via `hx-trigger="load"`. Fragment routes this task: `GET /ui/stocks/{ticker}/des`, `GET /ui/stocks/{ticker}/gp`.
- DES fragment: big price + colored Δ/Δ% (green/red class from sign), after-hours line if `post_market_price`, "AS OF {collected_at}" stamp + REFRESH button placeholder (wired in Task 10: render the button disabled with title "enable in Task 10" only if refresh setting absent — implement as: button present but `hx-post` added later; acceptable to render static disabled button now); summary grid (open, day range "low – high", 52-wk range with a CSS position marker, volume, avg volume, market cap, P/E trailing/forward, EPS, beta, dividend yield + ex-div date — all via `fmt_*` helpers); description ¶; officers table; next earnings date from analyst snapshot.
- GP fragment: range buttons (1M 3M 6M YTD 1Y 5Y MAX) + type toggle (line/candle) + indicator checkboxes (MA50 MA200 RSI MACD) + compare `<select>` (covered tickers + S&P 500 = `^GSPC`); a `<div id="gp-chart">`; inline script calls `renderGP(cfg)` in `app.js` which fetches `/api/stocks/{t}/bars|indicators|compare-bars` and builds the Plotly figure: candlestick or line main trace, MA overlays, RSI/MACD as stacked subplots (`Plotly` subplot rows), compare as normalized % on line mode.

- [ ] **Step 1: Failing tests**: `/stocks/AAA` 200 contains tab bar + `hx-get="/ui/stocks/AAA/des"`; `/stocks/ZZZ` 404 (HTML error page); `/ui/stocks/AAA/des` 200 contains formatted price, "52", officers name, description snippet; `/ui/stocks/AAA/gp` 200 contains `renderGP` and range buttons; `?tab=gp` makes GP the load-triggered tab.
- [ ] **Step 2: RED. Step 3: implement. Step 4: full gate PASS** + manual look with real DB.
- [ ] **Step 5: Commit** `feat(webapp): stock workstation shell + DES + GP panels`

### Task 8: FA + STAT + ERN panels + legacy redirect

**Files:**
- Modify: `src/webapp/routes/workstation.py`, `src/webapp/routes/pages.py` (redirect), `src/webapp/static/app.js` (ERN charts)
- Create: `templates/fragments/stat.html`, `templates/fragments/ern.html` (FA reuses the existing statements fragment)
- Test: `tests/test_web_workstation.py` (extend)

**Interfaces:**
- `GET /ui/stocks/{ticker}/fa` → re-render the EXISTING statements fragment mechanism (call the same helper `pages.py` uses; inner period tabs annual/quarterly/ttm/metrics preserved).
- `GET /ui/stocks/{ticker}/stat` → dense grid from `quote` + latest `metrics_annual` row: profitability (gross/operating/net/EBITDA margins, ROA, ROE, ROIC), leverage/liquidity (D/E, current, quick, interest coverage, debt/EBITDA), valuation (P/E t/f, PEG, P/S, P/B, EV/EBITDA), share stats (outstanding, float, short: shares/ratio/%float, insider %, institutional %) — every value through `fmt_value` with correct kind.
- `GET /ui/stocks/{ticker}/ern` → earnings-surprise section (table: quarter, est, actual, surprise% colored; plus `renderERN` grouped-bar Plotly est-vs-actual), analyst section (target range bar low—mean—high with current-price marker as CSS/Plotly, buy/hold/sell gauge: horizontal scale 1–5 with needle at `recommendation_mean`, `number_of_analysts`, growth rows, next earnings date).
- `GET /companies/{ticker}` (pages.py) → `RedirectResponse(f"/stocks/{ticker}?tab=fa", status_code=307)`; update companies-list row links to `/stocks/{t}`; the old `/ui/companies/{ticker}/statements` fragment stays (FA uses it).

- [ ] **Step 1: Failing tests**: fa fragment 200 contains "Revenue" and period tabs; stat 200 contains "ROE" + a short-interest label; ern 200 contains surprise table with colored class + `renderERN`; `/companies/AAA` → 307 with Location `/stocks/AAA?tab=fa`; empty-data ticker (add a bare company to fixture) renders "no data" states not 500s.
- [ ] **Step 2: RED. Step 3: implement. Step 4: full gate PASS.**
- [ ] **Step 5: Commit** `feat(webapp): FA/STAT/ERN workstation panels + legacy deep-dive redirect`

### Task 9: HP + DVD + HDS + INS panels

**Files:**
- Modify: `src/webapp/routes/workstation.py`
- Create: `templates/fragments/hp.html`, `dvd.html`, `hds.html`, `ins.html`
- Test: `tests/test_web_workstation.py` (extend)

**Interfaces:**
- `GET /ui/stocks/{ticker}/hp?range=1Y` → OHLCV table newest-first (date, O, H, L, C, volume; `fmt_price`-style 2dp) + range buttons re-fetching the fragment + "DOWNLOAD CSV" link to `/api/export/stock/{t}/bars.csv?range=`.
- `GET /ui/stocks/{ticker}/dvd` → dividend stats header (rate, yield, payout, ex-div, CAGR, consistency — compute CAGR/consistency in the route from `dividend_events` with the same formulas as `yahoo_handler._get_dividend_history`: annual sums, CAGR over positive years, share of non-decreasing years), annual-dividends Plotly bar (`renderDVD`), payments table, splits table, CSV link.
- `GET /ui/stocks/{ticker}/hds` → two tables (institutional, mutual fund): holder, shares, % held, value, date reported.
- `GET /ui/stocks/{ticker}/ins` → insider table: date, insider, position, transaction text, shares, value; red/green tint by buy/sell keyword in text ("Buy"/"Purchase" green, "Sale"/"Sell" red, else neutral).

- [ ] **Step 1: Failing tests**: hp 200 contains a bar date + CSV href; range param changes row count; dvd 200 contains an amount + `renderDVD`; hds 200 contains fixture holder name; ins 200 contains fixture insider name; all four graceful on the bare ticker.
- [ ] **Step 2: RED. Step 3: implement. Step 4: full gate PASS.**
- [ ] **Step 5: Commit** `feat(webapp): HP/DVD/HDS/INS workstation panels`

### Task 10: Quote refresh (job mode + endpoint + DES button)

**Files:**
- Modify: `src/webapp/jobs.py`, `src/webapp/routes/collection_api.py` (or stocks_api), `src/webapp/settings.py`, `src/webapp/app.py`, `src/fetchers/yahoo_handler.py`, `src/exporters/sqlite_store.py`, `templates/fragments/des.html`
- Test: `tests/test_web_quote_refresh.py`

**Interfaces:**
- `YahooHandler.fetch_quote(ticker: str) -> Dict[str, Any]` — ONE rate-limited `yf.Ticker(ticker)`; returns `{"market_data": ..., "valuation": ..., "shareholders": <stats-only dict, no holder lists>, "analyst_estimates": ...}` (reuses the private extractors; no history call, no bars).
- `SQLiteStore.upsert_quote(ticker: str, quote: Dict[str, Any], collected_at: str) -> None` — writes one `market_snapshots` + one `analyst_snapshots` row only.
- `CollectionJobManager.submit(tickers, ..., mode: str = "full") -> str`; `mode="quote"` path in `_run` calls `fetch_quote` + `upsert_quote` per ticker (still inside the single worker; same JobStatus lifecycle; summary notes mode).
- `POST /api/stocks/{ticker}/refresh-quote` → 409 if `not settings.allow_quote_refresh`; 404 unknown ticker; else 202 `{job_id}` (existing poll endpoints serve status).
- `WebSettings.allow_quote_refresh: bool = True`; env `STOCK_WEB_ALLOW_QUOTE_REFRESH` in `from_env` (same truthy parse as allow_collection; default TRUE when unset — note asymmetry with allow_collection).
- DES button wiring: `hx-post="/api/stocks/{t}/refresh-quote"` then poll fragment `GET /ui/stocks/{t}/refresh-status/{job_id}` (`hx-trigger="load, every 2s"`, stops on terminal state, then re-loads the DES fragment via `hx-get` on done).

- [ ] **Step 1: Failing tests** (fake fetcher via `job_manager.fetcher_factory`-style injection — add a parallel `quote_fetcher_factory` attribute): 409 when disabled; 202 + `queued→done` with fake; `market_snapshots` row count increases and DES shows the new `collected_at`; two refreshes serialize (reuse Tracking pattern); full-mode jobs unaffected (existing collection tests still green).
- [ ] **Step 2: RED. Step 3: implement. Step 4: full gate PASS.**
- [ ] **Step 5: Commit** `feat(webapp): on-demand quote refresh via serialized quote job mode`

### Task 11: Screener widening + docs + polish

**Files:**
- Modify: `src/webapp/screener.py`, `src/webapp/repository.py` (screen join), `templates/screener.html`, `README.md`
- Test: `tests/test_web_screener.py` (extend)

**Interfaces:**
- `SNAPSHOT_SCREEN_COLUMNS: List[str] = ["pe_trailing", "pe_forward", "dividend_yield", "price_to_book", "peg_ratio", "price_to_sales", "market_cap", "beta", "short_percent_of_float", "insider_percent", "institutional_percent", "debt_to_equity", "current_ratio"]` (subset of widened `_SNAPSHOT_COLUMNS`; import-derived assert in test).
- `build_screen_query`: filters/sort may name either a metric column (qualified `ma."col"`, as today) or a snapshot column (qualified `ms."col"`); add a LEFT JOIN to latest-snapshot-per-ticker: `LEFT JOIN (SELECT ticker, MAX(collected_at) mx FROM market_snapshots GROUP BY ticker) lms ON ...` + `LEFT JOIN market_snapshots ms ON ms.ticker = ma.ticker AND ms.collected_at = lms.mx`. Same ValueError discipline; `SCREEN_COLUMNS` output extends with the snapshot columns; `METRIC_KINDS` gains kinds for them (pct: dividend_yield, short_percent_of_float, insider_percent, institutional_percent; money: market_cap; mult: pe_*, peg_ratio, price_to_sales, price_to_book, debt_to_equity(raw ok→mult), current_ratio; raw: beta).
- `^GSPC` never appears (it has no companies/metrics rows — the INNER JOIN on metrics_annual already excludes it; add an explicit test).
- Screener UI: metric `<select>` gains an optgroup "MARKET / VALUATION" with the new columns. README: terminal section (command codes table, workstation tabs, STOCK_WEB_ALLOW_QUOTE_REFRESH, new tables, +collection-time note).

- [ ] **Step 1: Failing tests**: `dividend_yield gte` filter returns the fixture ticker with a snapshot yield (add yields to fixture snapshots); injection guard still rejects junk; sort by snapshot column works with NULLs last; `^GSPC` absent from any screen; GET shorthand `?dividend_yield_gte=0.01` works.
- [ ] **Step 2: RED. Step 3: implement. Step 4: full gate PASS.**
- [ ] **Step 5: Commit** `feat(webapp): screener market/valuation columns + terminal docs`

---

## Final verification (after all tasks)

1. Full gate with **upgraded deps**: `python -m pip install -U starlette fastapi && ruff check src/ tests/ && python -m mypy && pytest -q`.
2. Live smoke against the real DB: run one full collection for 2–3 tickers (`stock-data AAPL MSFT --formats sqlite`) to exercise migration + new fetches on the real schema; then `STOCK_WEB_DB_PATH=... stock-web`; verify: command bar (`AAPL GP` ⏎), every workstation tab renders real data, chart indicators + S&P overlay, quote refresh round-trips, screener filters on dividend_yield, old `/companies/AAPL` redirects.
3. Whole-branch review (most capable model) + fix wave, then finishing-a-development-branch (PR).
