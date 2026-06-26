# Energy-Sector Coverage — Design Spec

**Date:** 2026-06-26
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/energy-coverage` (off `main`)

## Context

A 5-sector live test scored **ExxonMobil (XOM)** at **50** (sector `energy`), with five
`missing_field` findings for `operating_income` (FY2021-2025) and NULL `roic` /
`inventory_turnover`. Root cause: integrated oil majors don't file a clean `OperatingIncomeLoss`
tag, but ENERGY inherits the GENERAL required-field set, which demands it. The `unmapped_facts`
worklist surfaced the tags XOM actually uses — `CostsAndExpenses` ($78B), `EnergyRelatedInventory`
($21.8B), `InventoryCrudeOilProductsAndMerchandise` ($6.9B).

A follow-up test confirmed the scope: **utilities are fine** — DUK and NEE (sector `utility`)
both report `operating_income` ($8.6B / $8.3B), score 100, and have real ROIC. So only ENERGY
needs treatment; UTILITY stays on the GENERAL set.

## Goal

Stop penalizing energy companies for a structurally-absent `operating_income`, and capture the
inventory line oil majors actually report — without fabricating misleading ratios.

### Non-goals

- No change to UTILITY (validated correct on the GENERAL set).
- Do **not** map `CostsAndExpenses` to `cost_of_revenue`. It is *total* costs (production +
  SG&A + D&A + non-income taxes), not COGS; using it would compute a misleading
  `inventory_turnover`. `roic` (needs operating income → NOPAT) and `inventory_turnover` (needs a
  clean COGS) therefore stay **NULL** for oil majors — honestly uncomputable, not fabricated.
- No new sector-specific metrics for energy (out of scope).

## Decisions (from brainstorming)

1. Relax ENERGY's required-field set (drop `operating_income`); leave UTILITY on GENERAL.
2. Add the energy inventory tags to the canonical `inventory` field.
3. Leave `roic`/`inventory_turnover` NULL for oil majors rather than approximate them.

## Changes

### 1. ENERGY required-field set — `src/validation/quality.py`

- Import `ENERGY` from `..mappings.sectors` (the module currently imports `BANK, GENERAL,
  INSURANCE, REIT`).
- Add a module constant:
  ```python
  _ENERGY_REQUIRED = (
      "revenue", "net_income",
      "total_assets", "total_liabilities", "total_equity", "operating_cash_flow",
  )
  ```
  (= `_GENERAL_REQUIRED` minus `operating_income`.)
- Add `ENERGY: _ENERGY_REQUIRED` to `REQUIRED_BY_SECTOR`. UTILITY is intentionally not added —
  it falls through to `_GENERAL_REQUIRED`, which is correct for utilities.

XOM then has all required fields present → zero `missing_field` findings → score 100.

### 2. Energy inventory tags — `src/mappings/canonical.py`

Extend the `inventory` field's candidate tags from `("InventoryNet",)` to:

```python
("InventoryNet", "EnergyRelatedInventory", "InventoryCrudeOilProductsAndMerchandise")
```

`InventoryNet` stays first (the standard tag most companies use). `EnergyRelatedInventory` is the
oil major's total inventory line (XOM = $21.8B); `InventoryCrudeOilProductsAndMerchandise` is a
fallback. The two new tags leave `ALL_MAPPED_TAGS` (so they drop off the unmapped worklist) — the
intended promote-from-worklist loop.

## Architecture / scope

- **Modified:** `src/validation/quality.py` (import + `_ENERGY_REQUIRED` + `REQUIRED_BY_SECTOR`
  entry), `src/mappings/canonical.py` (inventory tags), tests (below), `README.md` (a line noting
  energy's relaxed required set / that oil-major ROIC/inventory-turnover are absent by structure).
- **Untouched:** the metrics layer (roic/inventory_turnover stay NULL via existing logic), the
  integrity checks, the parser (registry-driven), the schema. UTILITY behavior.

## Testing (TDD)

- **`quality.py`:** an `energy`-sector annual set lacking `operating_income` but with the other six
  required fields produces **no `missing_field`** finding and scores 100; the same set under
  `general` still flags missing `operating_income` (proves the relaxation is energy-scoped).
- **`canonical.py`:** a fact under `EnergyRelatedInventory` resolves to `inventory` (and
  `InventoryNet` still wins when both present, i.e. priority order preserved).
- Full suite + ruff + mypy green; existing sector/quality tests unchanged.

## Global constraints

Python 3.9 (no `X|Y`); ruff 120 / E,F,W,I / imports at top; mypy clean (quality.py and canonical.py
are both in the mypy `files` list / directory). Commit trailer
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Re-run `python -m src.main WMT BAC PGR O XOM DUK NEE --no-yahoo` and confirm:
- **XOM recovers to score 100** with no `missing_field` findings and `inventory` resolved
  (≈$21.8B from `EnergyRelatedInventory`);
- WMT/BAC/PGR/O unchanged at 100; DUK/NEE unchanged at 100 (UTILITY untouched);
- `roic`/`inventory_turnover` remain NULL for XOM (expected — not fabricated);
- full suite + ruff + mypy green.
Only then merge.
