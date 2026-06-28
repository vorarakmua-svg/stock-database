# Discontinued-Operations Cash Capture — Design Spec

**Date:** 2026-06-28
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/discontinued-ops-cash` (off `main`)

## Context

The 50-stock cross-sector live smoke flagged **PRU** with a `cashflow_imbalance` finding (score 80).
Root cause: PRU's cash-flow statement reports its operating/investing/financing subtotals on a
**continuing-operations** basis and reports the cash flow from **discontinued operations** as a
separate line (`NetCashProvidedByUsedInDiscontinuedOperations` = −2,071M for FY2021), while the
reported net-change-in-cash line includes discontinued ops. The pipeline does not capture the
discontinued-ops line, so the consistency check's `expected = OCF + ICF + FCF + FX` is short by
exactly the discontinued-ops cash flow, producing a residual and a false finding. The data is also
genuinely dropped — a real cash-flow line we don't store.

This is a separate, narrower issue from the (already-shipped) 52/53-week fiscal-year fix and from the
backlog point-in-time/restatement work.

### Empirical grounding (cached `companyfacts`, 50-stock basket)

Of the 16 basket companies that report discontinued-ops cash:

| Reporting style | Count | Companies |
|---|---|---|
| Aggregate tag only | 2 | PRU, DUK |
| Split components only | 7 | GE, PFE, PG, VZ, HON, COP, TMUS |
| Both | 7 | KO, SLB, MRK, O, T, C, EQIX |

So a single-tag capture would miss the **7 component-only** filers. The capture must handle both the
aggregate tag and the three split components.

Also confirmed: the tag name does **not** reliably indicate basis — PRU tags its *continuing-only*
operating cash flow under the plain `NetCashProvidedByUsedInOperatingActivities` (no
`ContinuingOperations` suffix). So whether the section subtotals already include discontinued ops
cannot be inferred from `_source_tags`.

## Decision (from brainstorming)

1. Capture discontinued-ops cash completely: the aggregate tag, plus a **derived sum of the three
   split components** when the aggregate is absent (covers all 16 reporters).
2. Make the cash-flow consistency check robust to the total-vs-continuing-basis ambiguity by
   accepting the statement if it reconciles **with or without** the discontinued-ops line added.
3. Exclude the new (lumpy, residual) fields from the magnitude-outlier and quarterly-sum checks.

## 1. Data capture

### Canonical fields (`src/mappings/canonical.py`)

Add four cash-flow (`CASHFLOW`, `UNIT_USD`, `DURATION`, signed — no `SIGN_ABS`) fields:

- `cash_from_discontinued_operations` — aggregate. Candidate tags:
  - `NetCashProvidedByUsedInDiscontinuedOperations`
- `discontinued_operating_cash_flow` — `CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations`
- `discontinued_investing_cash_flow` — `CashProvidedByUsedInInvestingActivitiesDiscontinuedOperations`
- `discontinued_financing_cash_flow` — `CashProvidedByUsedInFinancingActivitiesDiscontinuedOperations`

Each new field carries a short `description`. None is added to any sector's required-field set.

### Derived sum (`src/parsers/derived_fields.py`)

In `apply_derivations`, when `cash_from_discontinued_operations` is `None` **and** at least one of the
three component fields is present, set it to the sum of the present components and record it as
`derived` in `_source_tags` (same pattern as the existing identity derivations such as
`total_liabilities = total_assets − total_equity`). When the aggregate tag is itself reported, it
wins and no derivation occurs (no double counting).

## 2. Cash-flow reconciliation check (`src/validation/integrity.py`)

`check_cashflow_reconciliation` currently flags when
`|net_change − (OCF + ICF + FCF + FX)| > tolerance`. Update it to treat the statement as consistent
if **either** form reconciles:

- `net_change ≈ OCF + ICF + FCF + FX`  (section subtotals already include discontinued ops), **or**
- `net_change ≈ OCF + ICF + FCF + FX + cash_from_discontinued_operations`  (continuing-only subtotals).

Concretely: compute both residuals (the existing one, and one that adds
`cash_from_discontinued_operations`, treating a missing value as 0). Flag only when **both** exceed
the materiality-scaled tolerance (the existing `_CASH_TOL` / `_CASH_GROSS_TOL` logic applies to each).

**Cannot-regress property:** the change only adds an additional way to pass, so every filing that
passes today still passes. The 14 currently-100 discontinued-ops filers are unaffected; only PRU's
(and any equivalently-shaped filer's) false residual is resolved. The 1% tolerance keeps the extra
degree of freedom from masking a material real error.

## 3. Check exclusions

The four new fields are USD/`DURATION`/`CASHFLOW`, so they are auto-included by `_USD_FIELDS`
(magnitude-outlier) and `_FLOW_FIELDS` (quarterly-sum). Discontinued-ops cash is lumpy/event-driven
and the aggregate is a reconciliation residual (like `net_change_in_cash`/`fx_effect_on_cash`), so:

- Add the four new field keys to `_OUTLIER_EXCLUDE` (removes them from magnitude-outlier candidates).
- Skip them in `check_quarterly_sums` (same treatment as `_EVENT_DRIVEN_FLOWS`).

Implementation note: introduce a shared constant (e.g. `_DISCONTINUED_FIELDS`) so the same set is
reused by `_OUTLIER_EXCLUDE` and the quarterly-sum skip (DRY), mirroring how `_EVENT_DRIVEN_FLOWS` is
shared today.

## Architecture / scope

- **Modified:** `src/mappings/canonical.py` (4 fields), `src/parsers/derived_fields.py` (derived sum),
  `src/validation/integrity.py` (reconciliation either-form + exclusions), tests, `README.md`
  (cross-sector / cash-flow notes).
- **Untouched:** the parser's tag-resolution and fiscal-year logic, sector classification/metrics,
  the DB schema (canonical columns are data-driven), `_period_year`/`_fiscal_year_from_end`.

## Out of scope (YAGNI)

- Income-statement discontinued-operations handling (this is cash-flow only).
- Point-in-time / restatement / look-ahead (separate backlog spec).
- Making any new field required, or adding sector-specific discontinued-ops ratios.

## Testing (TDD)

Pure unit tests with synthetic fixtures (no network):

- **Derived sum:** aggregate absent + the three components present → `cash_from_discontinued_operations`
  equals their sum, `_source_tags` marks it `derived`. Aggregate present → it wins, no derivation,
  components still captured. Aggregate absent + only one component present → equals that component.
- **Reconciliation either-form:** a PRU-shaped fixture (continuing OCF/ICF/FCF + separate disc
  aggregate + FX, net change including disc) → **no** finding. A clean total-basis fixture (no disc)
  → **no** finding (regression guard). A fixture where neither form reconciles (residual beyond
  tolerance both ways) → **one** finding.
- **Exclusions:** a discontinued-ops field that spikes 100× / whose quarters don't sum → **no**
  magnitude-outlier and **no** quarterly_sum finding; a core field in the same fixture still flags
  (proving the skip is scoped to the discontinued fields).
- Full suite + ruff + bare `mypy` green.

## Global constraints

- Python 3.9 (no `X | Y` unions); ruff (line-length 120; E,F,W,I; imports at top — avoid E402).
- mypy gate is **bare `mypy`** (project scopes via `pyproject` `files=[...]`); `canonical.py`,
  `derived_fields.py`, and `src/validation` are all in that list, so they ARE type-gated — keep them
  clean. Do NOT use `mypy src` (it surfaces pre-existing errors in unrelated legacy files).
- All checks remain FLAG-ONLY — they never mutate data. Materiality floor `$1,000,000`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Rebuild the DB and re-run the 50-stock basket
(`python -m src.main JPM BAC WFC C GS USB PGR TRV ALL MET PRU CB PLD AMT EQIX SPG O PSA NEE DUK SO D
XOM CVX COP SLB AAPL MSFT GOOGL NVDA AVGO ORCL WMT COST HD PG KO MCD JNJ UNH PFE ABBV MRK CAT HON GE
BA VZ T TMUS --no-yahoo`) and confirm:

- **PRU 80 → 100** (no `cashflow_imbalance`).
- The other 15 discontinued-ops reporters keep their prior scores; no company regresses; no new
  findings anywhere.
- `cash_from_discontinued_operations` is populated for all 16 reporters, including the 7
  component-only filers (GE, PFE, PG, VZ, HON, COP, TMUS) via the derived sum.
- Full suite + ruff + bare `mypy` green.

Only then merge.
