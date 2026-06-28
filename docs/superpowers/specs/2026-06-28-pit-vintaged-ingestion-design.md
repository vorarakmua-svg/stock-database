# Point-in-Time: Vintaged Annual Ingestion + Storage (Sub-Project 1) — Design Spec

**Date:** 2026-06-28
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/pit-vintaged-ingestion` (off `main`)

## Context

The pipeline stores exactly one value per annual period — `_resolve_canonical` collapses every
filing's view of a period to the most-recently-filed value ("restatements supersede"), and
`financials_annual` is keyed `PRIMARY KEY (ticker, fiscal_year)`. That means a backtest reading the
database for, say, FY2019 sees the *restated* numbers, not what was knowable when FY2019 was
originally filed — **look-ahead bias**.

The user wants true point-in-time backtesting: store every filing's version of each period (a
*vintage*) and later query "as of date D." This is large, so it is decomposed into three
sub-projects, each with its own spec → plan → implementation:

1. **Vintaged annual ingestion + storage** (this spec) — capture and persist every filing's view of
   each annual period.
2. **As-of-date query API** — `value(ticker, fiscal_year, field, as_of=D) = latest vintage filed ≤ D`.
3. **Point-in-time metrics** — ratios computed on as-of data.

This sub-project lands the data and the index sub-project 2 needs; it adds no query API and changes
no existing behavior.

### Decisions (from brainstorming)
- **Full vintaged history** (not just original-vs-latest) is the target capability.
- **Annual only** for this sub-project; quarterly is a later fast-follow with the identical mechanism.
- **One row per filing** (per `accn`): each filing's view of a period is its own vintage row, keyed by
  `(ticker, fiscal_year, accn)` with its `filed_date`. No dedup-on-change (storage is trivial — order
  thousands of rows for the basket — and the as-of query is exact either way).
- **SQLite-only** exposure for vintages; not added to per-ticker JSON/CSV.

### Key fact that makes this cheap
SEC `companyfacts` already retains every historical instance of a fact, each carrying its own `accn`
(accession = filing id) and `filed` date. (Confirmed in-session: PRU's FY2021 net income appears as
`fy=2020/2021/2022` instances across the FY2021/FY2022/FY2023 10-Ks; HON likewise.) So vintaged
ingestion reads the **already-fetched** `companyfacts` — **no additional network**.

## 1. Ingestion — a parallel vintage extractor

Add to `src/parsers/xbrl_parser.py`:

```python
def extract_annual_vintages(
    self, facts: Dict[str, Any], years_back: Optional[int] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Every filing's view of each annual period: {fiscal_year: {accn: period_dict}}."""
```

Behavior:
- Iterate `us-gaap` facts as `_resolve_canonical` does, restricted to forms `{"10-K", "10-K/A"}` and
  full-year duration / instant facts (`_is_full_year`).
- **Bucket by `(fiscal_year, accn)`** rather than collapsing to latest-filed. `fiscal_year` is derived
  with the existing date rule `_fiscal_year_from_end(entry["end"])` (falling back to `_period_year`
  when `end` is absent), consistent with `extract_annual_financials`.
- Within each `(fiscal_year, accn)` bucket, resolve each canonical field by candidate-tag priority
  (highest-priority tag present *in that filing* wins; no cross-filing comparison).
- Each vintage `period_dict` carries: the resolved canonical values, a `_source_tags` map, and
  metadata `accn`, `filed_date` (the accession's `filed`), `period_end` (the fact `end`), `form`,
  `fiscal_year`, and `calendar_year` (from the SEC `frame`, via the existing `_apply_calendar`
  helper / frame logic).
- `years_back` trims to the most recent N fiscal years (consistent with `extract_annual_financials`).

Implementation note: factor the shared per-entry resolution so `extract_annual_vintages` and
`_resolve_canonical` do not duplicate the candidate-tag/priority logic verbatim (DRY). A reviewer
should reject a copy-pasted resolution loop; extract a small helper both call, or parameterize the
existing one with the bucket key.

## 2. Model + pipeline wiring

- `src/models/stock_data.py`: add `financials_annual_vintages: Dict[str, Any] = field(default_factory=dict)`
  (shape `{fiscal_year: {accn: period_dict}}`). It is populated in memory and consumed directly by the
  SQLite exporter; it is deliberately **NOT** added to `to_dict` (which backs the JSON export), keeping
  vintages out of JSON per §4.
- `src/fetchers/stock_data_fetcher.py`: after `extract_annual_financials`, call
  `extract_annual_vintages(facts, years_back=years_back)` and store on the stock. In
  `_clean_and_derive`, run `apply_derivations` on **each vintage** `period_dict` (same as it already
  does for `financials_annual`/`financials_quarterly`) so each vintage is a self-contained snapshot.
- The existing `financials_annual` ("latest" view) and every downstream consumer are unchanged.

## 3. Storage — additive table

In `src/exporters/sqlite_store.py`, add a new table (purely additive — no change to existing tables,
keys, or queries):

```sql
CREATE TABLE IF NOT EXISTS financials_annual_vintages (
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    accn TEXT NOT NULL,
    filed_date TEXT,
    period_end TEXT,
    form TEXT,
    calendar_year INTEGER,
    <canonical columns: _cols_ddl(_CANONICAL_COLUMNS)>,
    PRIMARY KEY (ticker, fiscal_year, accn)
);
CREATE INDEX IF NOT EXISTS idx_fav_asof
    ON financials_annual_vintages (ticker, fiscal_year, filed_date);
```

- Register it in `_migrate`'s `expected` map (so an existing DB gains the table's canonical columns as
  the registry grows), mirroring `financials_annual`.
- In `_write_stock`, add a loop: for each `(fiscal_year, accn)` vintage, upsert a row keyed
  `["ticker", "fiscal_year", "accn"]`, reusing `_canonical_values(period)` for the numeric columns.
- Idempotent: re-export updates rows in place; row count is stable across re-runs.

## Architecture / scope

- **Modified:** `src/parsers/xbrl_parser.py` (new extractor + shared resolution helper),
  `src/models/stock_data.py` (new field + round-trip), `src/fetchers/stock_data_fetcher.py`
  (call + per-vintage derivations), `src/exporters/sqlite_store.py` (table + migrate + write loop),
  tests, `README.md` (document the new table + the point-in-time decomposition).
- **Untouched:** `financials_annual`/`metrics_annual`/quarterly/ttm tables and all existing queries,
  the fiscal-year/calendar logic, the validation layer, sector logic.

## Out of scope (this sub-project)

- As-of-date query API (sub-project 2 — this spec only lands the data + the `idx_fav_asof` index).
- Quarterly vintages (later fast-follow, identical mechanism).
- Point-in-time metrics (sub-project 3).
- Vintage exposure in JSON/CSV (SQLite-only).

## Testing (TDD)

Pure unit tests with synthetic `companyfacts` (no network):

- **Restatement → two vintages:** a fiscal year reported by two filings (distinct `accn`, distinct
  `filed`, a changed value) → `extract_annual_vintages` returns `{fy: {accn1: ..., accn2: ...}}` with
  each filing's own value and `filed_date`.
- **Within-filing priority:** a single filing tagging one field under two candidate tags → the
  higher-priority tag's value is the vintage's value for that field.
- **Date-rule label + derivations:** an early-January year-end vintage gets the correct
  `fiscal_year` (end-year − 1); after `apply_derivations`, a derivable field (e.g.
  `total_liabilities = total_assets − total_equity`) is filled and marked `derived` in that vintage.
- **Store idempotency + additivity:** exporting a stock with vintages creates the expected rows in
  `financials_annual_vintages`; a second export yields the same row count (upsert, no duplicates);
  `financials_annual` content is unchanged by the presence of vintages.
- **JSON stays clean:** `stock.to_dict()` does NOT contain a `financials_annual_vintages` key (vintages
  are SQLite-only).
- Full suite + ruff + bare `mypy` green.

## Global constraints

- Python 3.9 (no `X | Y` unions); ruff (line-length 120; E,F,W,I; imports at top — avoid E402).
- mypy gate is **bare `mypy`** (project scopes via `pyproject` `files=[...]`). `src/exporters/sqlite_store.py`
  is in that list; `xbrl_parser.py`/`stock_data.py`/`stock_data_fetcher.py` are not gated but must stay
  clean when checked explicitly. Do NOT run `mypy src`.
- Idempotent upserts; additive-only schema (no change to existing tables). FLAG-only validation layer
  is untouched.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Rebuild the DB and re-run the 50-stock basket
(`python -m src.main JPM BAC WFC C GS USB PGR TRV ALL MET PRU CB PLD AMT EQIX SPG O PSA NEE DUK SO D
XOM CVX COP SLB AAPL MSFT GOOGL NVDA AVGO ORCL WMT COST HD PG KO MCD JNJ UNH PFE ABBV MRK CAT HON GE
BA VZ T TMUS --no-yahoo`) and confirm:

- `financials_annual_vintages` is populated for the basket (many rows per ticker).
- At least one known restater shows **multiple vintages with differing values** for a fiscal year —
  e.g. a year whose `accn` rows differ on a core field (query
  `SELECT fiscal_year, COUNT(*) c, COUNT(DISTINCT net_income) d FROM financials_annual_vintages
  WHERE ticker='PRU' GROUP BY fiscal_year HAVING d > 1`).
- The existing tables are **unchanged**: `financials_annual` row counts/values and all 50 data-quality
  scores match the pre-change run (purely additive).
- Full suite + ruff + bare `mypy` green.

Only then merge.
