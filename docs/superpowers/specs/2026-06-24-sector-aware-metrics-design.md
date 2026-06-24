# Sector-Aware Financial Metrics — Design Spec

**Date:** 2026-06-24
**Status:** Approved (brainstorming) → ready for implementation plan

## Context

The data-acquisition and standardization layers are mature: a canonical registry
resolves firm-specific XBRL tag variability, sector-specific raw fields are captured for
banks/insurers/REITs, fiscal/calendar alignment is handled, and granular quarters + TTM
exist. The remaining seam is the **metrics layer**.

`CalculatedMetrics.calculate_all()` (`src/parsers/calculated_metrics.py`) computes 22
generic ratios — `roic`, `inventory_turnover`, `gross_margin`, `interest_coverage`,
`ebitda`, etc. — **identically for every company regardless of sector.** For a bank those
are meaningless or actively misleading (interest expense is a core revenue-generating
cost, so "interest coverage" is nonsense; a bank has no inventory or invested capital in
the operating-company sense). Meanwhile the sector-specific raw fields that were
deliberately standardized — `net_interest_income`, `noninterest_expense`,
`total_deposits`, `total_loans`, `premiums_earned`, `claims_incurred`,
`benefits_and_expenses`, `real_estate_net`, `rental_revenue` — are referenced by **zero**
metrics today.

Net effect: the comparability the standardization layer guarantees at the *raw-field*
level breaks at the *ratio* level, which is the level people actually screen on. A screen
such as `ROIC > 15%` silently mixes meaningful operating-company numbers with garbage
bank/REIT numbers.

## Goal

Make the metrics layer **sector-aware** so that:

1. Bank/insurer/REIT sector ratios are computed from the canonical fields already
   captured.
2. Generic ratios that are meaningless for a given sector are **suppressed (stored
   `NULL`)** rather than emitted as misleading numbers.
3. General operating companies (and any unclassified/utility/energy company) keep
   **exactly today's behavior** — fully backward compatible.

### Non-goals (explicitly out of scope this round)

- No changes to the canonical registry (`canonical.py`) or the XBRL parser. Every ratio
  below is computable from fields already in the registry.
- No exact NAREIT FFO (needs real-estate-specific D&A and gains-on-sale, which the
  registry does not split out) — see "Approximations" below.
- No utility/energy-specific ratios this round (they fall through to the generic suite).
- No point-in-time/restatement handling and no incremental-update work.

## Decisions locked during brainstorming

1. **Labeled proxies, not scope expansion.** FFO/AFFO and the insurer combined ratio are
   shipped as documented approximations. Each carries a `_basis` note. The registry/parser
   are **not** touched. Exact-FFO inputs become a future worklist item.
2. **Scope is the metrics layer + persistence + tests only.**

## Architecture

### New module: `src/parsers/sector_metrics.py`

Pure functions (no network, no class state), fully unit-testable:

- `bank_metrics(financials) -> Dict[str, Optional[float]]`
  → `net_interest_margin`, `efficiency_ratio`, `loan_to_deposit`
- `insurer_metrics(financials) -> Dict[str, Optional[float]]`
  → `loss_ratio`, `combined_ratio`
- `reit_metrics(financials) -> Dict[str, Optional[float]]`
  → `ffo`, `affo`, `ffo_per_share`, `ffo_payout`
- `SUPPRESSED_BY_SECTOR: Dict[str, frozenset[str]]` — the applicability map (see below).
- `SECTOR_EXTRAS: Dict[str, Callable]` — maps a sector to its metrics function.
- `apply_sector(metrics, financials, sector) -> Dict[str, Any]` — orchestrator: merges the
  sector extras into `metrics`, sets every key in `SUPPRESSED_BY_SECTOR[sector]` to `None`,
  and records approximation notes under `metrics["_basis"]` (a `Dict[str, str]`). Returns
  the mutated dict. A `None`/unknown/general/utility/energy sector is a no-op.

### Shared resolver refactor

`CalculatedMetrics._get_value(data, keys)` body is extracted to a module-level function
`field_value(data, keys) -> Optional[float]` in `calculated_metrics.py`. The method becomes
a thin delegator. `sector_metrics.py` imports `field_value` so both modules resolve fields
through one implementation (no duplication).

### Signature change

- `CalculatedMetrics.calculate_all(financials, market_data=None, valuation=None, sector=None)`
  - Computes the generic 22 exactly as today.
  - If `sector` is one of `bank`/`insurance`/`reit`, calls `apply_sector(...)` to merge
    extras and apply suppression.
  - `sector=None` (default) → identical to current behavior.
- `CalculatedMetrics.calculate_historical(annual_financials, sector=None)` threads `sector`
  into each per-year `calculate_all`.

Sector-name constants come from `src/mappings/sectors.py` (`BANK`, `INSURANCE`, `REIT`).

## Sector ratio definitions

All denominators are guarded `> 0`; any missing input yields `None` (existing convention).

| Key | Formula | Basis |
|---|---|---|
| `net_interest_margin` | `net_interest_income / total_assets` | **proxy** — no earning-assets line in registry; total assets used as denominator |
| `efficiency_ratio` | `noninterest_expense / (net_interest_income + noninterest_income)` | exact |
| `loan_to_deposit` | `total_loans / total_deposits` | exact |
| `loss_ratio` | `claims_incurred / premiums_earned` | exact |
| `combined_ratio` | `benefits_and_expenses / premiums_earned` | **proxy** — uses total benefits/losses/expenses as numerator (no separate underwriting-expense line) |
| `ffo` | `net_income + depreciation_amortization` | **proxy** — total D&A (not RE-specific), no gains-on-sale adjustment |
| `affo` | `ffo - capex` | **proxy** — total capex, not maintenance capex |
| `ffo_per_share` | `ffo / weighted_avg_shares_diluted` | derived from `ffo` |
| `ffo_payout` | `dividends_paid / ffo` | derived from `ffo` |

`_basis` is populated for `net_interest_margin`, `combined_ratio`, `ffo`, `affo` with the
text from the Basis column above.

## Suppression map

Suppressed generic metric → stored `NULL` = "not applicable for this sector." A
cross-sector screen filtering on a generic ratio (e.g. `WHERE roic > 0.15`) therefore
**automatically excludes** sectors where that ratio is undefined, instead of returning a
misleading value.

**BANK** — suppress:
`ebitda`, `ebit`, `ebitda_margin`, `debt_to_ebitda`, `roic`, `nopat`, `invested_capital`,
`interest_coverage`, `gross_margin`, `operating_margin`, `inventory_turnover`,
`days_inventory_outstanding`, `receivables_turnover`, `days_sales_outstanding`,
`asset_turnover`, `working_capital`, `net_debt`, `total_debt`, `free_cash_flow`,
`fcf_margin`, `levered_fcf`.
Kept: `roe`, `roa`, `net_margin` (+ added bank ratios).

**INSURANCE** — suppress:
`ebitda`, `ebitda_margin`, `debt_to_ebitda`, `roic`, `nopat`, `invested_capital`,
`inventory_turnover`, `days_inventory_outstanding`, `gross_margin`, `asset_turnover`,
`working_capital`.
Kept: `roe`, `roa`, `net_margin`, `net_debt`, `total_debt`, `interest_coverage` (+ added
insurer ratios).

**REIT** — suppress:
`roic`, `nopat`, `invested_capital`, `inventory_turnover`, `days_inventory_outstanding`,
`receivables_turnover`, `days_sales_outstanding`, `gross_margin`, `asset_turnover`,
`free_cash_flow`, `fcf_margin`, `levered_fcf`.
Kept: `roe`, `roa`, `net_margin`, `ebitda`, `debt_to_ebitda`, `interest_coverage` (+ added
REIT ratios).

The exact suppression-key strings must match the keys produced by `calculate_all` (e.g.
`days_inventory_outstanding`, not `dio`).

## Data flow

```
fetcher → stock.sector_class (already set via classify_submissions)
        → CalculatedMetrics.calculate_all(financials, market_data, valuation, sector=sector_class)
        → generic 22  +  sector extras  −  suppressed (NULL)  +  _basis notes
        → stock.merge_calculated_metrics(...)
        → SQLiteStore.metrics_annual (numeric columns)  +  JSON export (incl. _basis)
```

Wiring change is at `src/fetchers/stock_data_fetcher.py:196` and `:203`: pass
`sector=stock.sector_class` into `calculate_all` and `calculate_historical`.

## Persistence

`src/exporters/sqlite_store.py`:

- Append to `_METRIC_COLUMNS` (all `REAL`): `net_interest_margin`, `efficiency_ratio`,
  `loan_to_deposit`, `loss_ratio`, `combined_ratio`, `ffo`, `affo`, `ffo_per_share`,
  `ffo_payout`.
- `_migrate` already adds any missing column via `PRAGMA table_info` + `ALTER TABLE ADD
  COLUMN`, so existing DBs gain these columns on next write — no manual migration.
- `metrics_annual` stays purely numeric (one row per `ticker, fiscal_year`). The `_basis`
  approximation notes are **not** persisted to SQL; they live in the in-memory/JSON metrics
  dict (surfaced by the JSON exporter and `diagnose.py`). The store only reads the known
  `_METRIC_COLUMNS` keys, so the non-numeric `_basis` entry is ignored by persistence.
- `metrics_annual` rows are populated from `calculated_metrics["historical"]` (one entry
  per fiscal year), so **every year receives the same sector extras + suppression** — the
  queryable surface is sector-correct across all years, not just the latest snapshot.
- Suppressed generic metrics persist as `NULL`.

## Error handling

- Any missing input → the metric is `None`; no exceptions raised (existing convention).
- Division denominators guarded `> 0`.
- `sector` that is `None`, unknown, `general`, `utility`, or `energy` → generic suite only,
  no extras, no suppression (a no-op in `apply_sector`).
- The existing `try/except` around metric calculation in the fetcher is retained.

## Testing (TDD — tests written before implementation)

New `tests/test_sector_metrics.py` (using the existing `tests/conftest.py` `usd` helper and
the `_facts`/`_annual_usd` fact-builders pattern from `test_canonical_sectors.py`):

- **bank:** synthetic facts → `efficiency_ratio` and `loan_to_deposit` exact, `nim` proxy
  value correct; assert suppressed keys (`roic`, `inventory_turnover`, `ebitda`,
  `interest_coverage`) are `None`.
- **insurer:** `loss_ratio` exact, `combined_ratio` computed; suppressions hold.
- **reit:** `ffo == net_income + depreciation_amortization`, `affo == ffo - capex`,
  `ffo_per_share`, `ffo_payout`; suppressions (`roic`, `inventory_turnover`) hold;
  `_basis["ffo"]` present.
- **general regression:** `sector=None` and `sector="general"` → all 22 generic metrics
  present, no sector keys added, nothing suppressed.

Extend `tests/test_sqlite_store.py`:

- New columns persisted for a sector company.
- Migration: an old DB without the new columns gains them on next export.
- A bank row has `NULL` `roic` and non-null `efficiency_ratio`.

All 107 existing tests must stay green. `ruff` + `mypy` clean (add `sector_metrics.py` to
the mypy `files` list in `pyproject.toml`).

## Scope / files

- **New:** `src/parsers/sector_metrics.py`, `tests/test_sector_metrics.py`.
- **Modified:** `src/parsers/calculated_metrics.py` (extract `field_value`, add `sector`
  param), `src/fetchers/stock_data_fetcher.py` (pass sector), `src/exporters/sqlite_store.py`
  (columns; migration is automatic), `tests/test_sqlite_store.py` (assertions),
  `pyproject.toml` (mypy files), `README.md` (document sector ratios + approximation basis).
- **Untouched:** canonical registry, XBRL parser, fetchers' acquisition logic.

## Future work (not in this spec)

- Exact NAREIT FFO via new canonical fields (real-estate-specific D&A, gains on property
  sales) and a statutory insurer expense ratio — surfaced through the unmapped-tag worklist.
- Utility/energy sector ratios.
- A `provision_ratio` (`provision_for_credit_losses / total_loans`) bank credit-cost metric.
