# Point-in-Time: Metrics (Sub-Project 3) — Design Spec

**Date:** 2026-06-30
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/pit-metrics` (off `main`)

## Context

This completes the three-part point-in-time arc:

1. **Vintaged annual ingestion + storage** — shipped (PR #17): the `financials_annual_vintages` table
   keeps every filing's view of each annual period.
2. **As-of-date query API** — shipped (PR #18): `AsOfReader` resolves `(ticker, fiscal_year, as_of=D)`
   to the latest vintage filed ≤ D (no look-ahead).
3. **Point-in-time metrics** (this spec) — ratios computed on the as-of financials.

Today the pipeline computes ratios (ROIC, ROE, margins, leverage, …) from the **latest-restated**
annual financials and stores them in `metrics_annual`. A backtest reading those sees ratios that
incorporate restatements made *after* the date being modeled — look-ahead bias at the ratio level. SP3
removes it by computing the same ratio suite on the **as-of** financials SP2 exposes.

The existing engine makes this thin: `CalculatedMetrics.calculate_all(financials, sector=...)` computes
the full ratio suite from a **single** period's canonical fields — exactly the shape
`AsOfReader.as_of_annual` returns — and is stateless across periods. So the as-of metric for a year is
essentially `calculate_all(as_of_annual(ticker, fy, D), sector=...)`.

### Decisions (from brainstorming)
- **On-demand compute** (not a materialized metrics-vintages table): a pure-read composition layer.
  Works for any as-of date, always uses the current calculator, and keeps SP2's clean read-only
  boundary — zero write-path or schema change. (Rejected: materializing per-vintage metrics, which
  reintroduces ingestion coupling and freezes stored values to the calculator version at ingestion.)
- **Sector auto-read** from the `companies` table (`sector_class`), with an optional caller override.
  `sector_class` is a stable company attribute, not per-filing, so reading it once is correct and gives
  banks/insurers/REITs their sector ratios.
- **Fundamental ratios only** — valuation/EV/price ratios are excluded (see §2).

## 1. Architecture, module, and the compose rule

New pure-read class `PointInTimeMetrics` in `src/query/pit_metrics.py`. It does **no** ratio math and
**no** SQL resolution itself — it composes the two existing pieces:

```python
class PointInTimeMetrics:
    def __init__(self, reader: AsOfReader,
                 calculator: Optional[CalculatedMetrics] = None) -> None: ...

    @classmethod
    def from_path(cls, db_path) -> "PointInTimeMetrics": ...   # builds its own AsOfReader
```

**Compose rule** — the metrics for `(ticker, fiscal_year)` as of date `D`:

1. `period = self.reader.as_of_annual(ticker, fiscal_year, D)` — the latest filing's view filed ≤ D
   (or `None` → not yet filed as of D).
2. `sector = self._sector(ticker)` — read `companies.sector_class` once via the reader's read-only
   connection (unless the caller passes an explicit `sector=`).
3. `return self.calculator.calculate_all(period, sector=sector)` — the **same** engine the pipeline
   uses, fed the as-of financials instead of the latest-restated ones.

No duplication: SP2 owns resolution, `CalculatedMetrics` owns the ratio math. Because `calculate_all`
is single-period and stateless, an as-of period dict drops straight in.

`_sector(ticker)` runs `SELECT sector_class FROM companies WHERE ticker = ?` on the reader's connection
and returns the value (or `None` if absent). Caller-supplied `sector` short-circuits the lookup.

## 2. Method surface and the valuation boundary

Mirrors SP2's shape — a primary resolver plus two conveniences:

```python
def metrics_as_of(self, ticker, fiscal_year, as_of_date, sector=None) -> Optional[Dict[str, Any]]
```
Primary. The compose rule → the full ratio dict (same keys as the `metrics_annual` table), or `None`
if the year was not filed as of `D`. `sector=None` auto-reads; an explicit value overrides.

```python
def metric_as_of(self, ticker, fiscal_year, name, as_of_date, sector=None) -> Any
```
Scalar convenience: `metrics_as_of(...).get(name)`, with `None` passthrough.

```python
def metrics_history_as_of(self, ticker, as_of_date, years_back=None, sector=None)
    -> Dict[int, Dict[str, Any]]
```
Multi-year: reuses `reader.history_as_of(ticker, as_of_date, years_back)` for each year's as-of period,
runs the calculator per year (sector looked up once), keyed by `fiscal_year` (int), newest first.

### Valuation boundary (important)

The point-in-time metrics are the **fundamental** ratios — everything `calculate_all` derives from the
financial statements alone (profitability, returns, margins, capital structure, coverage, efficiency,
and the bank/insurer/REIT sector ratios). The **valuation/EV** ratios (`enterprise_value`,
`ev_to_ebitda`, `ev_to_revenue`, `ev_to_fcf`, `fcf_yield`) are **excluded**, because they need the share
price *as of D* and the project stores no historical per-date price series (`market_snapshots` holds
only as-collected snapshots). This falls out for free by calling `calculate_all` **without**
`market_data`, so those keys never appear. The docstring and README state this explicitly so their
absence is not mistaken for a bug.

### Edge cases
- **Not yet filed as of `D`** → `None` (resolver) / absent from history. Inherited from `as_of_annual`.
- **Unknown ticker / fiscal_year** → `None` / `{}`.
- **Unknown sector** (`sector_class` NULL/missing) → generic suite (the `None`-sector no-op path), the
  pipeline's default behavior.
- **`calculate_all` raising** on a sparse as-of period → mirror `calculate_historical`: in
  `metrics_history_as_of`, catch per-year and store `{"error": str(e)}` for that year (never abort the
  batch); in the single `metrics_as_of`, let it surface (one deliberate lookup the caller chose).

## 3. README — usage + boundary

Extend the existing "Point-in-time as-of queries" subsection with a `PointInTimeMetrics` example
(as-of ROE/ROIC for a restated year flipping as the date crosses the restatement) and one sentence on
the valuation exclusion.

## Architecture / scope

- **New:** `src/query/pit_metrics.py` (`PointInTimeMetrics`), `tests/test_pit_metrics.py`, README
  addition, the module added to the pyproject `[tool.mypy] files` list.
- **Reused unchanged:** `AsOfReader` (SP2), `CalculatedMetrics` / `apply_sector` (existing engine).
- **Untouched:** the writer (`sqlite_store.py`), the schema, the parser, the fetcher, validation. SP3
  adds zero write-path or schema change — pure read, like SP2.

## Out of scope (YAGNI)

- Valuation/EV/price ratios (no as-of price series).
- A materialized metrics-vintages table (rejected in favor of on-demand).
- Quarterly point-in-time metrics; CLI subcommand.

## Testing (TDD)

Pure unit tests with a hand-built temp DB (vintages + a `companies` row for sector) via `SQLiteStore`
(no network):

- **Metrics reflect the as-of vintage:** a year with two vintages whose `net_income` differs →
  `metric_as_of(..., "roe", D)` (or another differing ratio) returns the value computed from the
  **original** financials before the restatement filing, and from the **restated** financials on/after
  it. The point-in-time switch, proven at the ratio level.
- **Not-yet-filed → `None`**; unknown ticker/year → `None` / `{}`.
- **Sector correctness:** a `sector_class='bank'` company gets bank ratios (e.g. `net_interest_margin`
  present) with generic ones suppressed; an explicit `sector=` override beats the table value.
- **Valuation excluded:** the result dict contains no `enterprise_value` / `ev_to_ebitda` / `fcf_yield`
  keys.
- **`metrics_history_as_of` + `years_back`:** correct per-year metrics, newest-first, trim; a per-year
  calculator error is captured as `{"error": ...}`, not raised.
- **Delegation:** `metric_as_of` delegates with `None` / missing-field passthrough.
- Full suite + ruff + bare `mypy` green.

## Global constraints

- Python 3.9 (no `X | Y` unions); ruff (line-length 120; E,F,W,I; imports at top — avoid E402).
- mypy gate is **bare `mypy`** (project scopes via `pyproject` `[tool.mypy] files=[...]`). **Add
  `src/query/pit_metrics.py` to that `files` list** so the new module is type-gated and stays clean. Do
  NOT run `mypy src`. (`CalculatedMetrics` in `src/parsers/calculated_metrics.py` is not type-gated and
  is consumed only as a silent import — keep `pit_metrics.py` itself clean.)
- Read-only DB access (reuses the `AsOfReader` `mode=ro` connection); no mutation.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Against the existing 50-stock `data/output/stock.db`, on a known restater (**PRU**, FY2010 — `net_income`
$3.195B → $3.001B across filings, confirmed in the SP2 gate):

- Identify a ratio that differs between the original and restated vintages (e.g. `roe` or `net_margin`).
  Confirm `metric_as_of("PRU", 2010, <ratio>, D)` computes from the **original** value before the
  restatement filing date and from the **restated** value on/after it — the ratio itself is
  point-in-time.
- Confirm a bank in the basket (e.g. **JPM**) yields bank sector ratios (`net_interest_margin` present).
- Full suite + ruff + bare `mypy` green.

Only then merge.
