# Valuation Layer — Design

**Date:** 2026-07-13
**Status:** Approved design, pending implementation plan
**Stage:** 1 of 3 (valuation models → quality scores → composite ranking). Stages 2–3 are out of scope here.

## Problem

The database collects everything the README promises for valuation — FCF, ROIC, EBITDA,
risk-free rate, beta, analyst estimates, dividend history — but no valuation model exists.
The webapp shows *what the numbers are*, never *what the company is worth*. This design adds
that layer: automated, sector-aware fair-value estimates with honest uncertainty.

## Goals

- Every company gets at least one applicable valuation model (sector-aware, all-sectors north star).
- Output is a **bear/base/bull range + verdict**, never a single falsely-precise number.
- Every stored value carries the **exact assumptions** that produced it (trust anchor).
- Fully automated: assumptions derived by conservative mechanical rules, no user input required.
- Intrinsic values are stored at collection time; the price comparison happens at read time,
  so verdicts never go stale.

## Non-goals

- Composite ranking / scoring (stage 3), quality scores (stage 2).
- Interactive assumption editing (sliders) — the workstation sensitivity grid covers "what if".
- Valuation history/vintages — the `valuations` table is latest-only (YAGNI).

## Architecture

New pure package **`src/valuation/`**, modeled after `parsers/calculated_metrics.py`:
pure functions, no I/O, fully unit-testable.

```
src/valuation/
  engine.py      # orchestrates: inputs bundle -> [ValuationResult per model]
  models.py      # dcf, ddm, graham, lynch, multiples — pure functions
  assumptions.py # conservative-rule derivation (growth, discount rate, spreads)
  inputs.py      # ValuationInputs dataclass + SQLite reader that builds it
  backfill.py    # CLI: recompute valuations for all tickers already in the DB
```

**Single code path:** the engine always reads from SQLite (via `inputs.py`), never from
in-memory `StockData`. The pipeline hook is a post-export step in `src/main.py` /
`StockDataFetcher`: after `SQLiteStore.export(...)` succeeds, run the engine for the
collected tickers and upsert the `valuations` table. `backfill.py` runs the same engine
over every ticker in `companies` — no re-fetching.

Sector routing uses `companies.sector_class` (`general`, `bank`, `insurance`, `reit`,
`utility`, `energy` from `src/mappings/sectors.py`).

## Models

Each model is a pure function returning a `ValuationResult`:

```python
@dataclass
class ValuationResult:
    model: str                      # 'dcf' | 'ddm' | 'graham' | 'lynch' | 'multiples'
    applicable: bool
    na_reason: Optional[str]        # set iff not applicable
    value_bear: Optional[float]     # per-share fair value
    value_base: Optional[float]
    value_bull: Optional[float]
    assumptions: Dict[str, Any]     # every input incl. fallbacks, serialized to JSON
    basis_fiscal_year: Optional[int]
```

### Shared conservative assumptions (`assumptions.py`)

- **Growth (base)** = `min(historical CAGR, analyst growth)`, clamped to **[0%, 15%]**.
  Historical CAGR uses up to 10 FYs of the model's basis metric (FCF for DCF, dividends
  for DDM, EPS for Lynch); requires ≥ 4 FYs. Analyst growth = `analyst_snapshots.earnings_growth`
  (latest row); if absent, historical CAGR alone.
- **Terminal growth** = 2.5%.
- **Discount rate** = CAPM cost of equity: `risk_free_rate + beta × 4.5% ERP`,
  clamped to **[8%, 14%]**. `risk_free_rate` from the latest `market_snapshots` row;
  missing beta → **1.0, flagged** in assumptions (`"beta_fallback": true`).
- **Scenario spreads:** bear = growth −3pp (floor 0%) and discount +1pp;
  bull = growth +3pp (cap 15%) and discount −1pp.
- Every derived number and its provenance goes into `assumptions`
  (e.g. `"growth_base": 0.082, "growth_source": "analyst", "hist_cagr": 0.114, ...`).

### DCF — sectors: general, utility, energy

- Basis FCF = **median of the last 3 annual `levered_fcf`** (falls back to
  `free_cash_flow` when `levered_fcf` is NULL). FCF here is equity free cash flow
  (OCF − capex, interest already through OCF), so it is discounted at the CAPM cost
  of equity with **no net-debt adjustment**.
- Projection: years 1–5 at base growth, years 6–10 linear fade to terminal 2.5%,
  Gordon terminal value at year 10.
- Per-share: divide equity value by `market_snapshots.shares_outstanding` (latest).
- **N/A when:** median 3-yr FCF ≤ 0 ("average FCF is negative"); < 4 FYs of FCF history
  ("insufficient FCF history"); shares outstanding missing.

### Dividend Discount (multi-stage Gordon) — sectors: bank, insurance; also any company with ≥ 3 years of dividends

- Basis = trailing 12-month dividends per share from `dividend_events`.
- Dividend growth = `min(hist dividend CAGR (≤10 yrs), analyst growth)`, clamped **[0%, 10%]**.
- Stage 1: 5 years at that growth; terminal Gordon at 2.5%. Same CAPM discount.
- **N/A when:** < 3 calendar years of dividend history, or TTM dividends = 0.

### Graham Number — all sectors

- `√(22.5 × EPS × BVPS)`; EPS = latest FY net income / shares, BVPS = latest FY equity / shares
  (from `financials_annual`).
- Bear/base/bull from EPS variants: bear = min(EPS over last 3 FYs), base = latest FY,
  bull = max(last 3 FYs), BVPS fixed at latest.
- **N/A when:** EPS ≤ 0 or BVPS ≤ 0.

### Peter Lynch fair value — sectors: general, utility, energy

- Fair P/E = growth rate (as a number, e.g. 12% → 12), clamped **[5, 25]**;
  fair value = fair P/E × latest FY EPS. Growth = the shared conservative base
  growth computed on EPS history.
- Bear/bull via the ±3pp growth spread.
- **N/A when:** EPS ≤ 0 or growth history insufficient (< 4 FYs).

### Historical multiples band — all sectors

- Multiple by sector: **P/E** (general, utility, energy), **P/B** (bank, insurance),
  **P/FFO** (reit, using `metrics_annual.ffo_per_share`).
- For each of the last ≤ 5 FYs: multiple = FY-end price (nearest `price_bars` close on or
  before fiscal year end) ÷ that FY's per-share basis. Requires ≥ 3 valid FY multiples.
- Band = (min, median, max) of those multiples; fair value = band × latest FY per-share basis
  → bear/base/bull.
- **N/A when:** < 3 valid historical multiples (e.g. negative EPS years, missing price bars),
  or latest per-share basis ≤ 0.

### Overall verdict (computed at read time, not stored)

Across applicable models: `B` = median of `value_bear`, `U` = median of `value_bull`,
`V` = median of `value_base`. Verdict vs latest price `P`:

- `P < B` → **looks cheap**; `B ≤ P ≤ U` → **fairly valued**; `P > U` → **looks expensive**.
- Upside % = `(V − P) / P`.
- No applicable models → verdict **not valued**, with the per-model reasons listed.

## Storage

One new table in `sqlite_store.py` (same `_ensure_schema` reconciliation as existing tables):

```sql
CREATE TABLE IF NOT EXISTS valuations (
    ticker TEXT NOT NULL,
    model TEXT NOT NULL,
    applicable INTEGER NOT NULL,
    na_reason TEXT,
    value_bear REAL, value_base REAL, value_bull REAL,
    assumptions TEXT,               -- JSON
    basis_fiscal_year INTEGER,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (ticker, model)
);
```

Rows are upserted per (ticker, model) on every collection/backfill. Non-applicable models
still get rows so "not valued" is distinguishable from "never computed".

A companion table stores the cross-model medians per ticker (SQLite has no MEDIAN
aggregate, and the screener needs these in SQL). Medians of intrinsic values are
price-independent, so storing them keeps the compare-live principle intact:

```sql
CREATE TABLE IF NOT EXISTS valuation_summary (
    ticker TEXT PRIMARY KEY,
    n_applicable INTEGER NOT NULL,
    median_bear REAL, median_base REAL, median_bull REAL,
    computed_at TEXT NOT NULL
);
```

## Webapp

- **Repository** (`webapp/repository.py`): `valuations(ticker)` returning all model rows,
  plus a batch variant for the screener join.
- **API** (`webapp/routes/stocks_api.py` or a small `valuation_api.py`): valuation payload =
  model rows + computed verdict/upside using the latest price (existing summary/price source).
- **Stock page — Valuation panel:** horizontal range chart (slate Plotly theme via the shared
  `slateLayout` helpers): one bar per applicable model spanning bear→bull with a base marker,
  a vertical line at the current price, verdict chip in the panel header. Non-applicable models
  listed with reasons. Assumptions expandable per model (from the JSON). Empty state when the
  ticker has no valuation rows yet ("run collection or backfill").
- **Workstation — Valuation tab:** per-model breakdown table (bear/base/bull, basis FY,
  assumptions), plus for DCF a sensitivity grid: growth ∈ base ± {0,1,2,3}pp ×
  discount ∈ base ± {0,1}pp → per-share value, computed server-side on request from the
  stored assumptions (pure function reuse — no stored sensitivity data).
- **Screener:** two new columns — `Upside %` (sortable) and `Valuation` verdict chip
  (filterable like existing chips). Computed by joining stored `valuations` medians against
  the latest price in the existing screener query path; rows without valuations show "—".

## Failure handling

- Model-level N/A reasons are user-facing copy, stated plainly ("DCF not applicable:
  average FCF is negative") — no number is better than a fake number.
- Beta fallback (1.0) and any other input fallback is flagged inside `assumptions`.
- Stale risk-free rate: `fred_handler` already warns loudly at fetch time; the assumption
  JSON stores the rate and its as-of date so staleness is visible in the UI.
- Engine failures for one ticker/model are logged and skipped; they never abort a
  collection run or the backfill.

## Testing

TDD throughout (superpowers workflow):

- **Unit:** each model against hand-computed fixtures (deterministic input bundles →
  exact expected values); N/A paths for every documented reason; assumption-derivation
  rules (clamps, min-of-growth, fallback flags).
- **Property sanity:** higher growth ⇒ higher value; higher discount ⇒ lower value;
  bear ≤ base ≤ bull always; price inside median band ⇒ "fairly valued".
- **Integration:** backfill over a small fixture SQLite DB (general + bank + REIT tickers)
  produces expected `valuations` rows; pipeline hook writes rows after export.
- **Webapp:** route tests for the valuation payload, stock-page panel fragment,
  workstation tab, and screener columns/filter.
- Constraints: ruff, mypy, Python 3.9 compatibility.

## Rollout

1. Engine + models + storage + backfill (CLI-verifiable end to end).
2. Pipeline hook after export.
3. Webapp: API + stock page panel → workstation tab → screener columns.
