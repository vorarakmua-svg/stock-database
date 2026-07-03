# Terminal Workstation — Bloomberg-style UX + expanded per-stock data

**Date:** 2026-07-03
**Status:** Approved (design), pending implementation plan

## Context

The web app (merged in PR #20) browses the fundamentals SQLite store: dashboard, company deep-dive (statements + metric charts), screener/compare, as-of explorer, quality, collection trigger. The user wants:

1. A **Bloomberg Terminal-style UX** — dark, dense, keyboard-driven, with a command line.
2. A much richer **per-stock data set**, modeled on Yahoo-Finance-style sections: summary/quote, interactive chart with indicators, financials, analyst research/earnings, statistics, historical prices + dividends, holders/insider activity, company profile.

Exploration finding that shapes everything: the pipeline **already fetches** most of the requested data (quote/day-trading fields, valuation ratios, share/short statistics, analyst targets & recommendations, dividend history, institutional/mutual-fund holders, insider transactions, officers/profile) — but it lands only in per-ticker JSON exports. The SQLite store (which the web app reads) has none of it. Missing entirely: daily OHLCV price bars (a fetcher method `get_detailed_history()` exists, unused), earnings-surprise history, and stock splits.

## Decisions (locked with user)

- **Freshness:** snapshot model + per-ticker on-demand "Refresh quote" button. No streaming/real-time (free delayed sources; batch pipeline). Every quote shows an "as of <timestamp>" stamp.
- **Scope:** sections 1–7 + company profile. **Out of scope: options chain, news** (never stored — they expire in minutes and don't fit a stored fundamentals DB), and intraday bars (1-day chart granularity is daily).
- **Terminal depth:** full app reskin + Bloomberg-style command line with function codes.
- **Architecture: Approach A** — extend SQLite as the single source of truth. No JSON read path in the web app. New data becomes queryable/screenable across companies, consistent with the project's north star (standardized, comparable, trustworthy).

## 1. Data layer

### New tables (created `IF NOT EXISTS` by `SQLiteStore`, idempotent upserts)

| Table | Natural key | Columns (beyond key) | Source |
|---|---|---|---|
| `price_bars` | (ticker, date) | open, high, low, close, volume | new pipeline step using existing `YahooHandler.get_detailed_history(period="max", interval="1d")`, folded into `fetch_all` so it reuses the rate limiter |
| `analyst_snapshots` | (ticker, collected_at) | target_price_low/mean/median/high, recommendation, recommendation_mean, number_of_analysts, earnings_date, forward_eps, forward_pe, earnings_growth, revenue_growth, upside_potential | already fetched (`analyst_estimates`) |
| `earnings_history` | (ticker, quarter) | eps_estimate, eps_actual, surprise_pct | **new fetch** `stock.earnings_history` (~4–8 quarters per run); rows accumulate across collection runs — never deleted |
| `dividend_events` | (ticker, date) | amount | already fetched (`dividend_history.dividend_payments`, full history) |
| `split_events` | (ticker, date) | ratio | **new fetch** `stock.splits` |
| `holders` | (ticker, holder_type, holder) | shares, date_reported, pct_held, value, collected_at | already fetched; `holder_type ∈ {institutional, mutualfund}`; **replace-per-run** (delete ticker's rows, insert fresh) — it is a "current top holders" list, not history |
| `insider_transactions` | (ticker, insider, start_date, text) | position, shares, value, ownership, collected_at | already fetched; upsert on the composite key (dedupes re-fetches, accumulates history) |
| `officers` | (ticker, name) | title, age, total_pay | already fetched (`company_info.officers`); replace-per-run |

Benchmark: collect `^GSPC` (S&P 500) `price_bars` as pseudo-ticker `^GSPC` each run (bars only — skip SEC/fundamentals for it) so charts can overlay "vs S&P 500".

### Widened existing tables (in-place migration)

`SQLiteStore` gains `_ensure_columns(conn, table, columns)`: `PRAGMA table_info` + `ALTER TABLE ... ADD COLUMN` for any missing column. Non-destructive; upgrades an existing DB on the next run. Applied to:

- **`market_snapshots`** — add: previous_close, open, day_high, day_low, volume, avg_volume, avg_volume_10d, fifty_two_week_high, fifty_two_week_low, ma_50, ma_200, post_market_price, pre_market_price, peg_ratio, price_to_sales, dividend_rate, payout_ratio, ex_dividend_date (TEXT), debt_to_equity, current_ratio, quick_ratio, shares_outstanding, float_shares, shares_short, short_ratio, short_percent_of_float, insider_percent, institutional_percent. (`_SNAPSHOT_COLUMNS` stays the authoritative list; extend it.)
- **`companies`** — add: description, address, hq_city, hq_state. (Country already exists as `country`; no new country column.)

### Pipeline changes (`yahoo_handler.fetch_all` + `stock_data_fetcher`)

- Add `_get_price_bars` (period="max", daily), `_get_earnings_history`, `_get_splits` to `fetch_all` (reusing the shared `yf.Ticker` + rate limiter). Add `postMarketPrice`/`preMarketPrice` to `_get_market_data`.
- `StockData` gains fields for the new payloads; `SQLiteStore.export` writes them.
- Bars strategy: full `period="max"` refetch each run with `INSERT OR REPLACE` (simple, idempotent; ~11k rows/ticker, ~0.5M rows total — SQLite handles this in seconds). No incremental logic in v1.
- Cost: ~3 extra Yahoo calls per ticker per run ≈ +6 min on a 50-ticker collection (batch — acceptable). DB grows ~30–50 MB.

## 2. Read layer + JSON API

`Reader` (webapp `repository.py`) gains typed, parameterized methods; routes stay SQL-free:

- `quote(ticker)` — latest (widened) `market_snapshots` row + change vs previous_close.
- `price_bars(ticker, start=None, end=None)` — ascending bars; range presets resolved in the route (1M/3M/6M/YTD/1Y/5Y/MAX). Down-sampling to weekly/monthly for long ranges done in pandas.
- `analyst_snapshot(ticker)` (latest), `earnings_history(ticker)`, `dividend_events(ticker)`, `split_events(ticker)`, `holders(ticker, holder_type)`, `insider_transactions(ticker, limit)`, `profile(ticker)` (companies row + officers).

New endpoints under `/api/stocks/{ticker}/`: `quote`, `bars?range=&interval=`, `indicators?range=` (server-side pandas: MA50, MA200, RSI-14, MACD 12-26-9 — returned as date-aligned series), `analyst`, `earnings`, `dividends`, `splits`, `holders`, `insiders`, `profile`, plus `compare-bars?tickers=A,B&range=` (normalized %-change series, includes `^GSPC`). CSV export added for bars (`/api/export/stock/{ticker}/bars.csv?range=`) and dividends, reusing the existing export pattern. 404 for unknown ticker; empty lists for known-ticker-no-data.

**Quote refresh:** web threads never write the DB (single-writer rule). `CollectionJobManager` gains a `mode="quote"` job: Yahoo-info-only fetch for one ticker (no SEC, no bars), upserting one `market_snapshots` row (+ analyst snapshot). Endpoint `POST /api/stocks/{ticker}/refresh-quote` → 202 + job id (existing poll endpoints). Gated by a new `allow_quote_refresh: bool = True` setting (`STOCK_WEB_ALLOW_QUOTE_REFRESH` env; independent of the full-collection `allow_collection` gate, which stays default-off).

## 3. Terminal UX

- **Global reskin** (every page): near-black background (#0a0a0a), amber accent (#ff9900), green/red signed numbers, dense monospace numeric grids (tabular-nums), thin borders, uppercase panel headers. Replaces `app.css`; template class tweaks where needed. Existing URLs and features all keep working.
- **Command bar**: fixed top input, focus via `` ` `` or `/`. Grammar (client-side parse, ~50 lines JS): `[TICKER] [CODE]` — `AAPL` → workstation DES; `AAPL GP` → chart. Codes: **DES** summary+profile, **GP** chart, **FA** financials, **ERN** earnings/analyst, **STAT** statistics, **HP** historical prices, **DVD** dividends+splits, **HDS** holders, **INS** insider; global: **SCR** screener, **ASOF**, **QM** quality, **COL** collect, **HELP** overlay listing all codes. Unknown input falls back to ticker autocomplete (existing `/api/companies/search`).
- **Stock workstation** `/stocks/{ticker}` — function tabs (HTMX fragments, one route per panel):
  - **DES**: quote header — large price, colored Δ and Δ%, after-hours if present, "as of <collected_at>" + REFRESH button (polls quote job) — then the summary grid (open, day range, 52-wk range bar with current-price marker, volume/avg volume, market cap, P/E (trailing/forward), EPS, beta, dividend yield + ex-div date), business description, officers, next earnings date.
  - **GP**: Plotly candlestick/line toggle; range buttons 1M 3M 6M YTD 1Y 5Y MAX; indicator toggles MA50/MA200 (overlays), RSI, MACD (subplots); compare overlay vs any covered ticker or `^GSPC` (normalized %).
  - **FA**: existing statements tabs (annual/quarterly/TTM/metrics) restyled into the terminal grid.
  - **ERN**: earnings-surprise chart (estimate vs actual bars + surprise %), analyst price-target range bar (low–mean–high, current-price marker), buy/hold/sell gauge from recommendation_mean (1–5), growth estimates, next earnings date.
  - **STAT**: dense grid — profitability (margins, ROA/ROE), leverage/liquidity (D/E, current, quick), valuation (PEG, P/S, P/B, EV ratios), share stats (outstanding, float, short interest/ratio/% float, insider %, institutional %).
  - **HP**: OHLCV table (range filter, newest first) + Download CSV.
  - **DVD**: dividend payment table, annual-dividend bar chart, dividend CAGR/consistency stats, splits table.
  - **HDS**: institutional + mutual-fund holders tables. **INS**: insider transactions table.
- The old `/companies/{ticker}` deep-dive page **redirects** to `/stocks/{ticker}` (FA tab preserves its content); companies list, dashboard, screener, as-of, quality, collect all restyled in place.
- Every panel renders a graceful empty state ("no data collected — refresh this ticker") when fields are missing; non-covered tickers 404 as today.

## 4. Testing

House rules unchanged: no network in tests; fixture DB (`web_db`) extended with synthetic bars (enough for MA/RSI/MACD windows), analyst/earnings/dividends/splits/holders/insider/officers rows including `^GSPC` bars; known-value unit tests for RSI/MACD/MA math; migration test (build old-schema DB, run new store, assert added columns + preserved rows); Reader method tests; API shape tests (404/empty semantics); fake-fetcher test for the quote job mode (gating on/off, single-writer serialization preserved); smoke test per function screen + command-bar page; ruff + bare mypy + pytest green on 3.9-compatible code. Starlette lesson applied: verify against freshly upgraded deps before claiming CI-green.

## 5. Rollout phases

1. Schema: new tables + `_ensure_columns` migration + widened `_SNAPSHOT_COLUMNS`/companies; store writes from StockData; pipeline fetch additions (bars, earnings history, splits, pre/post-market, `^GSPC`).
2. Reader methods + `/api/stocks/*` endpoints + indicators service.
3. Terminal reskin (all pages) + command bar + HELP.
4. Workstation shell + DES + GP.
5. FA + STAT + ERN.
6. HP + DVD + HDS + INS + CSV exports.
7. Quote-refresh job mode + button + gating.
8. Screener whitelist widened with new snapshot columns (e.g. dividend_yield, short_percent_of_float) + README/docs + polish.

Each phase independently shippable; same subagent-TDD + per-task review workflow as PR #20.

## Risks / notes

- **yfinance API instability**: `earnings_history`, `insider_transactions`, holder DataFrames change shape occasionally. Mitigation: defensive extraction (as `yahoo_handler` already does), tolerate-and-log missing sections, never fail a ticker on an optional section.
- **Yahoo rate limits**: +3 calls/ticker within the existing rate limiter; quote-refresh is single-ticker and serialized through the job executor.
- **`^GSPC` pseudo-ticker** must be excluded from screener/companies listings (bars-only row; no `companies` entry or a flagged one — decision: no `companies` row; `price_bars` alone).
- **Data licensing**: personal/local use of delayed Yahoo data — unchanged from current usage.
