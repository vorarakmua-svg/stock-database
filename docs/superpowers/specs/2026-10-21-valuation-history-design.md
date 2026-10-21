# Valuation history and a historical fair value: design (Milestone 1)

- Date: 2026-10-21
- Status: approved, with the label cutoffs, through the plan
  `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md`
- Built by: the build loop (`qa/BUILD_LOOP_PROMPT.md`)

## Goal

Each stock gets a 10-year history of its valuation multiples, and a transparent "historical fair
value" drawn against its price. This is our analogue of GuruFocus's GF Value line. It differs in four
ways:
- Every rule is published.
- The windows are trailing only, so there's no look-ahead.
- No unpublished adjustment factor is applied.
- Nothing changes the existing six models, `MEDIAN_MODELS` or the verdicts.

## Data facts (verified read-only on 2026-10-21)

**Price basis**
- `price_bars` has no adj_close column. `dividend_adjusted` is NULL on all 1.25M rows (market closes
  from `history(auto_adjust=False)`, `src/fetchers/yahoo_handler.py:820`).
- The closes are split-adjusted to today's basis. AAPL goes 124.81 to 129.04 across 2020-08-31's 4:1
  split, and NVDA 120.89 to 121.79 across 2024-06-10's 10:1.
- The closes are also divided by Yahoo's non-split factors (spin-offs, stock dividends). GE stays
  continuous across its factors of 1.281 (2023-01-04) and 1.253 (2024-04-02). `split_events` holds
  these as ratios that aren't simple splits (`_simple_ratio`, `src/valuation/inputs.py:265`).

**Share counts and dates**
- Filed share counts sit on a split basis that differs by field and by year:
  - AAPL's diluted-share seam for the 2020 split shows at 2017-12-30.
  - NVDA's 2023-01-29 row has shares outstanding on the pre-10:1 basis but the weighted average on the
    post-10:1 basis.
  - Never divide a price by a filed EPS.
- Quarters carry no filing date. `financials_quarterly.filed_date` and `form` exist
  (`src/exporters/sqlite_store.py:187-188`) but are NULL in all 8,906 rows, and `financials_ttm` has
  no such column. Only `financials_annual_vintages` has real filing dates: the 10-K lag is median 51
  days, 95th percentile 60, 99th percentile 62.
- The stored values are the latest restated figures (`src/parsers/xbrl_parser.py:823-850`).

**Yahoo's figures**
- Yahoo's EV/EBITDA is not in `market_snapshots`: the `ev_to_ebitda` stored there is ours. Yahoo's
  `enterpriseToEbitda` is only in `data/output/json/{T}.json` at `valuation.ev_to_ebitda`.

**The design prototype** (read-only), current point against Yahoo:
- P/S: 106/125 within 5%, 115/125 within 10%.
- P/B: 97/117 within 5%, 109/117 within 10%.
- P/E: 97/123 within 5%, 112/123 within 10%.
- Our share counts: 118/125 within 5% of Yahoo's.
- TTM EBITDA, FCF and net debt at fiscal year end: within 1% of `metrics_annual` for about 97% of
  ticker-years.

## Inputs: `src/valuation/history_inputs.py`

`load_history_inputs(conn, ticker) -> HistoryInputs`

**`HistoryInputs`** is a dataclass with these fields:
- ticker and sector_class;
- the month-end samples (date, close) plus the latest bar;
- the TTM rows with their known dates and computed fundamentals;
- the normalized share series;
- the price factors and dividends;
- Yahoo's implied share count (`market_cap / current_price`);
- `na_reason`.

Helpers, each unit-tested:
- `is_share_split(ratio)`: whether a split-event ratio is a simple split; otherwise it's a non-split
  factor.
- `price_factor_after(day, factors, last_bar) -> float`: PF(d), the product of the non-split factors
  dated in (d, last bar].
- `known_on(period_end, fiscal_quarter, first_10k_filed, collected_on) -> date`:
  - Fiscal Q4 is known on the first 10-K `filed_date` in the vintages, when that date is within 120
    days of period end; otherwise on period end + 75 days.
  - Q1-Q3 are known on period end + 45 days.
  - The result is capped at the latest collection day.
  - A known row more than 200 days old at a date is stale there.
- `pick_share_field(rows) -> Optional[str]`: one field per ticker, the first with at least 60%
  coverage, in this order: `weighted_avg_shares_basic`, `shares_outstanding`,
  `weighted_avg_shares_diluted`, then `net_income / eps_diluted`. Diluted counts flip in loss
  quarters, as BX and PANW show.
- `normalize_share_series(ends, counts, eps, splits, yahoo_count, revenue, listed) ->
  (counts_on_bar_basis, applied)`:
  - Drop a one-quarter spike: a move above 15% that comes back within 5%.
  - Walk newest to oldest. A quarter-to-quarter ratio within 1.25x of a run of declared splits dated
    after the older quarter, and nearer that run than 1.0, folds the run in.
  - An undeclared seam of 1.5x or more folds by its own ratio only on a split's marks (BUG-175):
    - EPS of one sign on both sides moved against the count by at least half the seam's ratio, in
      log terms. A missing or zero EPS, or one that changes sign, says nothing.
    - Revenue, where both quarters have it, didn't move with the count by as much.
    - A merger stays organic, as COF's 1.56x and DUK's 1.567x (EPS 0.99 to 0.86, revenue 1.88x)
      do, and so does an exchange offer (Citigroup's 2.242x in 2009, EPS changing sign).
  - The quarter of the listing (the first ending on or after the first bar, within 98 days of it)
    averages the private count with the public one, so an undeclared seam right after it folds:
    the public count applies back to it, as to every bar (UBER's 2019 Q2: 1.11B against 1.70B).
  - Fill gaps by carrying the last normalized count forward.
  - Splits after the latest quarter apply only when they move our count toward Yahoo's implied count
    (APH, 2026-09-03).
- **N/A guard**: when our latest count and Yahoo's implied count differ by more than 1.25x, the ticker
  is N/A with a reason. That covers BX at 1.49x and BRK-B at about 1,300x (share classes; Q7).
  The guard and the later-split check read the latest snapshot with a price, since a run whose quote
  failed leaves one without; with none, the ticker is N/A too (BUG-176).

**Fundamentals**
- Each TTM row goes through `CalculatedMetrics().calculate_all(row, sector=sector_class)`
  (`src/parsers/calculated_metrics.py`).
- That gives EBITDA, net debt, FCF (only when capex is present) and the REIT FFO proxy, with the
  sector rules' suppression applied (`src/parsers/sector_metrics.py:116-141`).
- Common equity = total equity − preferred equity.

**Month ends**: the last bar of each month, plus the latest bar as the current point.
- Bars flagged `dividend_adjusted` are undone with `_yahoo_dividend_factors` (:371).
- Dividends pass through `_drop_spin_off_dividends` (:441) and `_dividend_onto` (:414).
- The bars-to-price factor (:628-639) moves into a shared helper both modules use.

**Market cap**: mc(d) = close(d) × PF(d) × S(q), where q is the latest quarter known at d. Its share
count is normalized onto the bars' split basis, and PF puts the close back on the as-traded basis.
Every ratio is taken on totals.

## Computation: `src/valuation/history.py`

Constants:
- `METRICS = ("pe", "ps", "pb", "pfcf", "ev_ebitda", "pffo", "div_yield")`
- `WINDOW_YEARS = 10`
- `MIN_MONTHS = 36`
- `MIN_FV_METRICS = 2`
- `HISTORICAL_VALUE_CUTOFFS = (0.7, 0.9, 1.1, 1.3)`

**Multiples**: `multiples_at(market_cap, close, fundamentals, ttm_dps) -> Dict[str, Optional[float]]`
- P/E = mc / net income.
- P/S = mc / revenue.
- P/B = mc / common equity.
- P/FCF = mc / FCF.
- P/FFO = mc / FFO.
- EV/EBITDA = (mc + (net debt or 0)) / EBITDA, as `calculated_metrics.py:491-511` defines EV.
- A denominator of zero or less gives None.
- Dividend yield = trailing 365-day dividends / close. It is None when no dividends were paid.

**Window statistics**: `window_stats(values, current) -> Optional[Dict[str, float]]`
- The window is the months in (d − 10 years, d].
- It needs at least 36 non-None values; otherwise the metric is N/A.
- It returns min, p25, median, p75 and max, the quartiles from
  `statistics.quantiles(method="inclusive")`.
- `pct_rank` is the share of window values at or below the current value.

**Metrics combined into the fair value**: `fair_value_metrics(sector) -> Tuple[str, ...]`. These are
the price multiples the sector rules don't suppress, with P/FFO in place of P/E for REITs (as
`src/valuation/models.py:363-368` does).

| sector_class | combined metrics |
|---|---|
| general, utility, energy | pe, ps, pb, pfcf, ev_ebitda |
| bank | pe, ps, pb |
| insurance | pe, ps, pb, pfcf |
| reit | pffo, ps, pb, ev_ebitda |

Dividend yield is shown but never combined.

**Implied price**: `implied_price(metric, multiple, fundamentals, shares_bars) -> Optional[float]`
- It is multiple × fundamental / shares_bars, where shares_bars = S × PF.
- For EV/EBITDA it is (multiple × EBITDA − net debt) / shares_bars.
- The result is per share on the bars' basis, so the line overlays the chart's closes.

**Historical fair value**: FV(d) is the median, across the sector's metrics with at least 36 months,
of `implied_price` at each metric's trailing-window median multiple.
- It needs at least two metrics.
- Low and high use the p25 and p75 multiples in place of the median.
- No fitted weights, because they would bring in look-ahead. The median already damps one bad metric.

**Label**: `historical_value_label(price, fair_value) -> Optional[str]`, with r = price / FV.

| r | label |
|---|---|
| r < 0.7 | far below |
| 0.7 ≤ r < 0.9 | below |
| 0.9 ≤ r ≤ 1.1 | near |
| 1.1 < r ≤ 1.3 | above |
| r > 1.3 | far above |

- The label is computed at read time against the live price.
- The chart bands are FV × each cutoff.
- The cutoffs were approved with the plan.

`compute_history(inputs) -> HistoryResult` returns:
- the monthly series rows;
- a ranges row per metric, plus a 'fair_value' row;
- the N/A reasons.

## Storage (`src/exporters/sqlite_store.py`, `_create_schema` at 146-338)

```sql
valuation_history(ticker, date, close, market_cap, enterprise_value, ttm_period_end,
  pe, ps, pb, pfcf, ev_ebitda, pffo, div_yield, fair_value, fair_value_n,
  PRIMARY KEY(ticker, date))
valuation_ranges(ticker, metric, applicable INTEGER NOT NULL, na_reason, as_of, window_start,
  n_months, current_value, pct_rank, min, p25, median, p75, max, implied_value, in_fair_value,
  n_metrics, computed_at TEXT NOT NULL, PRIMARY KEY(ticker, metric))
```

- `metric` also takes the value 'fair_value'. That row's current_value, p25 and p75 are on the
  latest snapshot's price basis, multiplied by `bars_to_price`. (The column is `current_value`, not
  `current`, because CURRENT is an SQL keyword.)
- `SQLiteStore.export_valuation_history(ticker, result, computed_at)` deletes and re-inserts the
  ticker's rows in one transaction.
- An N/A ticker gets ranges rows that carry the reason.
- `CREATE TABLE IF NOT EXISTS` is enough; no `_migrate` step is needed.

## Engine

The historical value is a separate indicator, not a seventh model, for three reasons:
- Its output is a series plus ranges, not a bear/base/bull triple.
- `run_valuations` promises six rows, and tests pin that.
- The VAL tab already special-cases owner earnings.

It hooks in after `export_valuations` in `compute_and_store` (`src/valuation/engine.py:169`), inside
its own try/except, so a history failure never blocks the valuations. One hook covers:
- CLI collection (`stock_data_fetcher.py:563-572`);
- web jobs (`jobs.py:266-279`, `360-368`);
- `python -m src.valuation.backfill`.

`MEDIAN_MODELS` (`engine.py:53`) is untouched.

## Web

- **Repository readers**: `valuation_history(ticker)` and `valuation_ranges(ticker)`. Both return []
  when the table is missing, as `repository.py:371-395` does.
- **API**: `GET /api/stocks/{t}/valuation-history` returns:
  - `as_of`, `price`, `computed_at`;
  - `historical_value`: `{value, low, high, metrics, label, price_to_value, cutoffs}`;
  - `ranges[]`;
  - `series`: `{dates, close, fair_value, <each multiple>}`.

  An unknown ticker, ^GSPC included, gets 404. A ticker not computed yet gets an empty-state payload.
- **VAL tab**: a section loaded lazily (`hx-get="/ui/stocks/{t}/val-history" hx-trigger="load"`,
  `templates/fragments/val_history.html`), so the tab bar stays the same. It holds:
  - the label pill;
  - price vs FV with four shaded bands;
  - the metrics table (current, 10y min, median and max, percentile);
  - a single-multiple chart with min/median/max lines;
  - method notes and N/A reasons.

  `renderValHistory` in `static/app.js` builds on the SLATE/slateLayout/slateAxis helpers (:7-33). It
  defaults to a 10-year range, and escapes `</` in the inline config as `workstation.py:1044-1050`
  does. `/ui/companies/{t}/metric-chart` is not reusable, because it charts fiscal-year series.
- **Screener** (`src/webapp/screener.py`):
  - `HISTORY_EXPRS` goes after `VALUATION_EXPRS` (:47-52). `pe_pct_10y`, `ps_pct_10y`, `pb_pct_10y`,
    `pfcf_pct_10y`, `ev_ebitda_pct_10y`, `pffo_pct_10y` and `div_yield_pct_10y` map to
    `vr_<m>.pct_rank`, and `price_to_hist_value` to `ms.current_price / NULLIF(vr_fv.current, 0)`.
  - It uses fixed-literal LEFT JOINs on `valuation_ranges`, in both the main and the count queries,
    and resolves them in `_qualify_column`.
  - `SCREEN_COLUMNS` (the CSV follows) and `METRIC_KINDS` gain the new fields.
  - `routes/pages.py:486-492` gains a "Historical value" option group, and the results gain two
    sortable columns.

## Tests (TDD order)

1. History inputs:
   - An AAPL-like 3.97x seam folds the declared 4:1 split.
   - A COF-like 1.56x merger stays organic, and so do a DUK-like merger with an EPS dip, a seam
     whose EPS changes sign or is missing, and one whose revenue rose with the count (BUG-175).
   - A listing quarter takes the public count; a first quarter long after the listing doesn't.
   - A one-quarter spike is dropped.
   - An APH-like split after the latest quarter is confirmed by Yahoo's count.
   - A 0.125 GE reverse split stays out of PF, and a 1.281 spin-off factor goes into it.
   - The filing-lag rule and its cap.
   - The share-field choice.
2. History computation:
   - A denominator of zero or less gives None.
   - 35 months is N/A; 36 is enough.
   - `pct_rank` at the window's min and max.
   - A window that starts on Feb 29.
   - FV needs two metrics. Each sector's metric set.
   - Label boundaries at exactly 0.7, 0.9, 1.1 and 1.3.
   - No look-ahead: changing a later month leaves earlier FV values unchanged.
3. End to end on a synthetic DB built through `SQLiteStore.export` (as the conftest `web_db` does),
   with TTM rows from `compute_ttm`:
   - P/E is continuous across a 4:1 split and correct across a spin-off.
   - Bank, REIT and short-history cases.
4. Store: the tables are created, rows are replaced per ticker, and an old DB upgrades.
5. Engine and backfill:
   - History is written.
   - A history failure doesn't block the valuations.
   - The six rows and `MEDIAN_MODELS` are unchanged.
6. Web:
   - repository readers;
   - API payload, 404 and not-computed-yet;
   - the VAL fragment, its hook, escaping and N/A;
   - screener whitelist and disjointness, SQL, filter and sort, and columns.

Mutants that must each fail a test:
- no split fold;
- no filing lag;
- full-window medians in place of trailing ones;
- a label cutoff moved.

## Real-data validation (on a backup-API copy)

Run `python -m src.valuation.backfill --db <copy>`, then `qa/oracle/history_vs_yahoo.py`. The oracle
imports nothing from `src`.

| Check | Gate |
|---|---|
| P/E vs Yahoo | ≥ 75% within 5%, ≥ 88% within 10% |
| P/S and P/B vs Yahoo | ≥ 80% within 5%, ≥ 90% within 10% |
| EV/EBITDA vs Yahoo's JSON value | ≥ 55% within 10%, ≥ 75% within 20%; Yahoo normalizes EBITDA (MRK, PFE, ABBV) |
| Our share count vs Yahoo's | ≥ 90% within 5% |
| TTM at fiscal year end vs `metrics_annual` | ≥ 95% within 1% |

Every miss beyond 20% (35% for EV/EBITDA) gets an explanation in the evidence. The run also checks:
- Market-cap continuity at every declared share split's seam (BUG-177). The seam is the jump of about
  the split's ratio in the filed counts, up to three years before the split. Where the history's
  trailing quarter crosses it, the market cap moves within 10% of the price. No monthly move may sit
  within 3% of a nearby declared split's ratio. A check at the split dates alone can't see a seam left
  unfolded, since bars and counts both sit on today's basis there; it passed with NFLX 10x off.
- Every monthly market-cap jump above 12% that the price doesn't explain is traced to an event. The
  prototype found 67, among them BMY 2020, LIN 2019 and SCHW 2021.
- `valuation_summary` is unchanged by this milestone.
- The size of the restatement look-ahead, measured from the vintages.

Evidence goes to `qa/evidence/valhist-*.txt`.

## Edge cases

| Case | Handling |
|---|---|
| GEV (28 months), SNDK (21) | Multiples shown; statistics and FV are N/A |
| CEG (~57 months) | Statistics over the shorter window, labeled as such |
| SPCX | No TTM, so N/A; not in the screener |
| V, BRK-B, BX | N/A by the share guard (Q7) |
| SPG, KKR | P/S reads low (partnership units not counted); noted |
| Negative earnings (BA, INTC) | P/E is None; FV comes from the remaining metrics |
| Banks | No EV/EBITDA or P/FCF |
| REITs | The FFO proxy |
| ^GSPC | Never computed |
| XOM and BLK | Continuity checked at the predecessor splice quarter |
| Splits Yahoo missed (BUG-153) | Caught only by the EPS test |
| Spin-offs | Restated revenue makes pre-spin P/S read high; noted |
| SCCO stock dividends | Market cap up to ~7% high |
| Missing quarterly D&A (MSFT: 8 of 76 quarters) | That metric N/A at those dates |

## Out of scope (recorded for later)

- **The multiples band's basis**: the 5-FY band (`src/valuation/models.py:371-408`) divides
  spin-adjusted closes by as-reported EPS. GE's band reads 12.2x to 1,044x, because the FY2022 close
  of 52.20 is really $83.8 / 1.605, over EPS of $0.05. Fixing it moves the median, so it is a
  NEEDS-USER decision.
- **Restatement look-ahead**: history uses the latest restated quarters. A later fix would persist
  each quarter's first filing date.
- **Verdicts**: whether the historical value joins the verdict or the median is the user's decision.
