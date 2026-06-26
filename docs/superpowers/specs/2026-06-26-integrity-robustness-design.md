# Integrity-Check Robustness Pass (B + C + D) — Design Spec

**Date:** 2026-06-26
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/integrity-robustness` (off `main`)

## Context

A 10-stock live test surfaced three independent issues where the data-quality checks
mis-handle legitimately-messy real filings. Each is fixed below; they are independent and
become separate implementation tasks on one branch.

- **B — `cashflow_imbalance` false-positives on discontinued-ops companies (MET, score 50).**
  MET's reported net change in cash includes discontinued-operations cash flows that the
  continuing OCF+ICF+FCF don't, plus a foreign-exchange variant tag we don't capture. The
  residual is large vs the small *net* change but tiny vs the *gross* cash activity
  (≈$0.4B residual on ≈$34B of gross flows).
- **C — `operating_income` isn't universally tagged (JNJ score 0; also XOM).** Diversified
  multinationals report pretax income split by geography with no `OperatingIncomeLoss`. The
  GENERAL required-field set demands it, producing false `missing_field` penalties. The
  earlier energy-only relaxation was too narrow.
- **D — `quarterly_sum_mismatch` cascades per-field on spinoff/restatement years.** JNJ's
  Kenvue-spinoff year (2023) emits 19 separate findings (one per flow field) — disproportionate
  for what is really one phenomenon (that year's quarters don't reconcile to the recast annual).

## Decisions (from brainstorming)

B: scale the residual to gross cash flow + add the FX variant tag. C: drop `operating_income`
from the required set (and remove the redundant energy special-case). D: collapse to one finding
per fiscal year.

## B — cash-flow check tolerant of discontinued ops

**`src/mappings/canonical.py`:** add MET's FX-on-cash variant to the `fx_effect_on_cash`
candidate tags (it has a different word order than the existing
`…IncludingDisposalGroupAndDiscontinuedOperations` variant):

```
EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsDisposalGroupIncludingDiscontinuedOperations
```

(>120 chars — split with implicit string concatenation per the registry's existing style.)

**`src/validation/integrity.py` `check_cashflow_reconciliation`:** require the residual to be
material **two ways** before flagging. Add `_CASH_GROSS_TOL = 0.05`. Using multiplication
(no division):

```python
gross = abs(ocf) + abs(icf) + abs(fcf)
# ... after computing residual, denom (= max(|net_change|, |expected|), with the $1M floor):
if abs(residual) > _CASH_TOL * denom and abs(residual) > _CASH_GROSS_TOL * gross:
    findings.append(Finding(MEDIUM, "cashflow_imbalance", ...))
```

A real mis-resolved section (~100% of its own magnitude) is large vs both the net change and
gross activity → still flagged. MET's disc-ops/FX residual (≈$0.4B) is < 5% of ≈$34B gross →
not flagged. When `gross == 0`, the second condition reduces to `|residual| > 0`, so a cash
change with no flows still flags (a genuine inconsistency).

**Empirical tuning (the one number to pin):** `_CASH_GROSS_TOL` (starting at 0.05) is validated
on real data — MET must pass, the clean set (AAPL/MSFT/JPM/PLD etc.) stays at ~0% residual, and a
synthetic mis-resolved-section fixture must still fire. Adjust the constant if the smoke shows a
clean company over the line or MET under it.

## C — `operating_income` no longer required

**`src/validation/quality.py`:**
- Remove `"operating_income"` from `_GENERAL_REQUIRED` (leaving revenue, net_income,
  total_assets, total_liabilities, total_equity, operating_cash_flow).
- Remove the `_ENERGY_REQUIRED` constant and its `ENERGY: _ENERGY_REQUIRED` entry in
  `REQUIRED_BY_SECTOR` — now redundant (energy falls through to the relaxed GENERAL set).
- Remove `ENERGY` from the `from ..mappings.sectors import …` line (no longer referenced; else
  ruff F401).

`operating_income` is still attempted and captured wherever a company reports it; its absence is
simply no longer a data-quality penalty. The bank/insurance/REIT required sets already omit it,
so no other set changes. (The energy PR's *inventory* tag additions in `canonical.py` are
unaffected and stay.)

## D — quarterly-sum: one finding per fiscal year

**`src/validation/integrity.py` `check_quarterly_sums`:** instead of appending a `Finding` per
`(field, year)`, collect the mismatched flow fields for each fiscal year and emit **one**
`Finding(MEDIUM, "quarterly_sum_mismatch", …, year)` per year, whose message names the count and
the fields, e.g. `"FY2023: 19 flow field(s) whose four quarters don't sum to the annual figure
(revenue, cost_of_revenue, gross_profit, ...)."`. The grouping, all-four-quarters requirement,
`$1M` floor, and `_QUARTERLY_TOL` are unchanged — only the emission is aggregated.

## Architecture / scope

- **Modified:** `src/mappings/canonical.py` (B: FX tag), `src/validation/integrity.py`
  (B: `check_cashflow_reconciliation` + `_CASH_GROSS_TOL`; D: `check_quarterly_sums`),
  `src/validation/quality.py` (C: required set + remove energy special-case), tests, `README.md`.
- **Untouched:** the metrics layer, the magnitude-outlier/ratio-bounds checks, the parser, the
  schema. Energy inventory tags (from the prior PR) stay.

## Testing (TDD)

- **B:** a fixture where `net_change ≠ OCF+ICF+FCF+FX` but the residual is < 5% of gross flows →
  **no** finding (insurer/disc-ops shape); a fixture where a section is mis-resolved (residual is
  a large fraction of gross) → fires. The existing "fires on 20% net residual" test is updated so
  its residual is also material vs gross (so it still fires).
- **C:** an energy/general company missing `operating_income` but otherwise complete → no
  `missing_field`, score 100; remove/adjust the now-obsolete energy-required test from the prior
  PR (general no longer requires operating_income, so the "general still flags it" assertion is
  no longer true — update it to assert general no longer flags a missing operating_income).
- **D:** a fiscal year with **two or more** mismatched flow fields → exactly **one**
  `quarterly_sum_mismatch` finding (not one per field). The existing single-field fires/silent
  tests still pass (one field → one finding).
- Full suite + ruff + mypy green.

## Global constraints

Python 3.9 (no `X|Y`); ruff 120 / E,F,W,I / imports at top (long XBRL tags use implicit
concatenation); mypy clean (`quality.py`/`integrity.py` via the `src/validation` dir entry,
`canonical.py` explicit). Flag-only. Commit trailer
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Re-run `python -m src.main GOOGL NVDA COST JNJ GS MET AMT CVX V CAT --no-yahoo` and confirm:
- **MET no longer has `cashflow_imbalance`** (recovers toward 100);
- **JNJ no longer has `missing_field(operating_income)`**, and its FY2023 quarterly mismatch is
  **one** finding, not 19 (score improves substantially);
- GS/V quarterly mismatches become one-per-year (unchanged count since they had 1 each);
- the previously-clean companies (GOOGL/NVDA/COST/AMT/CVX/CAT) still score 100;
- the broader clean set (AAPL/MSFT/JPM/PLD) still shows no `cashflow_imbalance`;
- full suite + ruff + mypy green.
Only then merge.
