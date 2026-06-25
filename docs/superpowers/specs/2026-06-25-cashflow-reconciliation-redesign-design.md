# Cash-Reconciliation Redesign (cashflow_reconcile v2) — Design Spec

**Date:** 2026-06-25
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/integrity-hardening` (amends in-flight PR #9; the v1 check shipped on this branch is unsound)

## Context

The integrity-hardening feature (PR #9) added a `cashflow_reconcile` check that compared
Δ(balance-sheet `cash_and_equivalents`) against `operating + investing + financing` cash
flow, flagging a >5% residual as `cashflow_imbalance` (MEDIUM). A live smoke run exposed it
as a **systematic false positive** on clean large-caps:

| Ticker | v1 score | v1 `cashflow_imbalance` findings |
|---|---|---|
| JPM | 100 | 0 |
| MSFT | 90 | 1 |
| AAPL | 70 | 3 |
| PLD | 60 | 4 |

Root cause: it compared two **different cash bases**. Since ASU 2016-18, the cash-flow
statement reconciles to a *broader* total (cash + **restricted cash**) and its net change
**includes the foreign-exchange effect** — neither of which is the narrow balance-sheet
`cash_and_equivalents` line. So the residual was real restricted-cash + FX movement, not an
error. For PLD FY2021 the FX effect alone was −$39.6M on a −$42M change.

The other three integrity checks (`magnitude_outlier`, `quarterly_sum_mismatch`,
`ratio_out_of_bounds`) fired on **none** of these companies — they are sound. Only the cash
check is broken.

## Empirical validation (the fix, proven before building)

Reconciling the cash-flow statement against **itself** — its reported net-change line vs the
sum of its own sections plus FX — yields a residual of **exactly 0.00% for every company,
every year** tested (AAPL, MSFT, JPM, PLD, FY2020–FY2025):

```
expected = OCF + ICF + FCF + FX
residual = net_change_in_cash − expected      →  0.00% in all 24 company-years
```

PLD FY2021: net = −$42M, expected = −$42M, residual $0. AAPL (no FX line): residual $0 too,
because its net-change tag and its OCF/ICF/FCF are all on the restricted-inclusive basis.
This is an *internal-consistency* check (the statement's total equals the sum of its parts by
construction), so it has no basis mismatch and is near-exact.

## Goal

Replace the unsound balance-sheet-vs-flows comparison with the internal-consistency check, so
`cashflow_imbalance` fires only on a genuine inconsistency (a section that didn't resolve, or
a real tagging error) — and clean filings score 100 again.

### Non-goals

- Still **annual only**; no quarterly cash reconciliation.
- Do not map the `…PeriodIncreaseDecreaseExcludingExchangeRateEffect` net-change variant; a
  filer that reports *only* that (rare) is skipped rather than mis-reconciled.
- No parser change (the XBRL parser is registry-driven — adding canonical fields auto-extracts
  them). No schema change.

## Decisions locked during brainstorming

1. Reconcile the **reported net-change line** against `OCF + ICF + FCF + FX`, not balance-sheet
   cash. Requires two new canonical fields.
2. Tolerance tightened **5% → 1%** (empirically residual is 0.00%; 1% leaves margin for rare
   rounding / FX-tag-variant nuance).
3. When `net_change_in_cash` is absent, **skip** the check (no fallback to the old balance-sheet
   comparison).

## New canonical fields

Added to the cash-flow section of `CANONICAL_FIELDS` in `src/mappings/canonical.py`. Both are
`CASHFLOW`, `UNIT_USD`, `DURATION`, `SIGN_AS_REPORTED` (can be positive or negative). Tag lists
are validated against real AAPL/MSFT/JPM/PLD filings.

- **`net_change_in_cash`** — "Net Change in Cash" — reported total net change in cash
  (restricted-cash-inclusive, including FX). Tags, in priority order:
  1. `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect`
  2. `CashAndCashEquivalentsPeriodIncreaseDecrease`
- **`fx_effect_on_cash`** — "FX Effect on Cash" — effect of exchange-rate changes on cash. Tags:
  1. `EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`
  2. `EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations`
  3. `EffectOfExchangeRateOnCashAndCashEquivalents`
  4. `EffectOfExchangeRateOnCashAndCashEquivalentsContinuingOperations`

Both tags are now also captured for general querying and (via the registry-driven parser) for
quarterly periods, as a side benefit.

## Rewritten check

`check_cashflow_reconciliation(annual, scored_years)` in `src/validation/integrity.py`,
annual-only, per scored year `y`:

- Read `net_change_in_cash`, `operating_cash_flow`, `investing_cash_flow`,
  `financing_cash_flow`, `fx_effect_on_cash` from `annual[y]`.
- If `net_change_in_cash` is `None`, **skip** `y` (can't reconcile).
- If any of OCF / ICF / FCF is `None`, **skip** `y` (incomplete sections).
- `expected = ocf + icf + fcf + (fx_effect or 0.0)`.
- `residual = net_change_in_cash − expected`; `denom = max(|net_change_in_cash|, |expected|)`;
  skip if `denom < $1M` (materiality floor).
- Flag `Finding(MEDIUM, "cashflow_imbalance", ..., y)` when `|residual| / denom > _CASH_TOL`.

`_CASH_TOL` becomes `0.01`. The finding message names the reported net change and the expected
sum. The check no longer reads `cash_and_equivalents`. Flag-only (no mutation), as before.

## Architecture / scope

- **Modified:** `src/mappings/canonical.py` (+2 fields), `src/validation/integrity.py`
  (rewrite `check_cashflow_reconciliation`; `_CASH_TOL` 0.05→0.01; **exclude the two new fields
  from the magnitude-outlier candidate set** — see below), `tests/test_integrity.py` (rewrite
  the three cashflow tests + a magnitude-outlier regression), `README.md` (update the cash-flow
  row to "internal cash-flow consistency").

  **Critical interaction — magnitude-outlier exclusion:** `_USD_FIELDS` (the magnitude-outlier
  candidate set) currently derives as *every* `UNIT_USD` field, so it would automatically pick
  up `net_change_in_cash` and `fx_effect_on_cash`. Those are **volatile net residuals** that
  legitimately swing far more than 100× year-to-year (a near-zero year next to a multi-billion
  year), so including them would create a NEW false-positive vector in `magnitude_outlier`.
  They must be **excluded** from `_USD_FIELDS`: add a module constant
  `_OUTLIER_EXCLUDE = frozenset({"net_change_in_cash", "fx_effect_on_cash"})` and define
  `_USD_FIELDS = tuple(f.key for f in CANONICAL_FIELDS if f.unit == UNIT_USD and f.key not in
  _OUTLIER_EXCLUDE)`. They REMAIN in `_FLOW_FIELDS` (the quarterly-sum check), where they sum
  correctly (discrete quarters of a flow sum to the annual value) and a mismatch is a genuine
  inconsistency worth flagging.
- **Untouched:** the XBRL parser (registry-driven), the SQLite schema (canonical columns are
  added by the existing `_migrate`), the other three integrity checks.
- Stays on `feat/integrity-hardening` so PR #9 ships correct.

## Testing (TDD)

Rewrite the three `cashflow_*` tests in `tests/test_integrity.py` to the new fields/formula:

- **fires:** a period where `net_change_in_cash` disagrees with `OCF+ICF+FCF+FX` by >1%
  (e.g. net_change = 1.0e9 but sections+FX = 0.5e9) → one MEDIUM `cashflow_imbalance`.
- **silent (incl. FX):** `net_change_in_cash = OCF+ICF+FCF+FX` exactly, with a non-zero
  `fx_effect_on_cash` → no finding (proves FX is included).
- **skips:** `net_change_in_cash` absent → no finding; and a section (e.g. ICF) absent → no
  finding.

A registry test (or the existing canonical coverage test) should confirm the two new fields
resolve from their primary tags.

**Magnitude-outlier exclusion test:** a multi-year series where `net_change_in_cash` swings
>100× (e.g. years of ~$50M next to a $20B year) produces **no** `magnitude_outlier` finding —
proving the volatile cash-flow residuals are excluded from that check.

Full suite + ruff + mypy stay green.

## Verification (live smoke — the merge gate)

After implementation, re-run `python -m src.main AAPL MSFT JPM PLD --no-yahoo` and confirm
**no `cashflow_imbalance` findings** on these clean filers and their `quality_score` returns to
100 (modulo any unrelated finding). Only then merge PR #9.

## Global constraints (carried into the plan)

Python 3.9 floor; ruff 120 / E,F,W,I / imports at top; mypy clean (integrity.py is covered by
the existing `"src/validation"` directory entry — do **not** add it explicitly). Flag-only.
All existing tests stay green. Commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
