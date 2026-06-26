# Magnitude-Outlier: Exclude Event-Driven Flows — Design Spec

**Date:** 2026-06-26
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `fix/outlier-exclude-lumpy-flows` (off `main`)

## Context

A 10-stock live test flagged a `magnitude_outlier` on Johnson & Johnson's `debt_issued`:
$7.4B in 2021 (a real one-off bond issuance) versus ~$0 the years around it — "spikes to 2477x
the prior year and 1486x the next." This is a false positive: financing transactions are
*event-driven* and legitimately lumpy.

The spike-and-revert check's premise — "a value 100× its neighbors that reverts ⇒ likely an
error" — holds only for **recurring, relatively stable** fields (revenue, income, assets, equity,
capex, dividends, D&A). It breaks on **event-driven flows** that legitimately spike in a single
year: debt issuance/repayment (bond issues, maturities, refinancings), buybacks (programs that
run then stop), acquisitions (episodic M&A), and one-time charges (impairments, restructuring).
This is the same class already handled for `net_change_in_cash` / `fx_effect_on_cash` (the
volatile cash residuals), which are excluded from the outlier check via `_OUTLIER_EXCLUDE`.

## Goal

Stop `magnitude_outlier` from false-flagging legitimately-lumpy, event-driven flow fields, while
still catching genuine one-off anomalies in recurring/level fields.

### Non-goals

- The other findings from the 10-stock test (MET cash-flow on discontinued ops; JNJ
  `operating_income` not tagged; quarterly-sum cascades on spinoff years) are **separate** items,
  not addressed here.
- No new mechanism — reuse the existing `_OUTLIER_EXCLUDE` denylist (YAGNI; don't add a
  per-field "volatile" flag to the registry dataclass).

## Decision (from brainstorming)

Extend `_OUTLIER_EXCLUDE` to also exclude six event-driven flow fields. **Keep** checking
`capex` and `dividends_paid` (relatively smooth — a 100× spike-revert there is genuinely worth
flagging) and the section totals (`operating/investing/financing_cash_flow`) — the 100× bar plus
the spike-revert requirement make section-total false positives very unlikely.

## Change — `src/validation/integrity.py`

`_OUTLIER_EXCLUDE` becomes:

```python
_OUTLIER_EXCLUDE = frozenset({
    # Volatile net residuals (cash-flow reconciliation inputs).
    "net_change_in_cash", "fx_effect_on_cash",
    # Event-driven / lumpy flows that legitimately spike in a single year:
    # financing transactions, buybacks, M&A, and one-time charges.
    "debt_issued", "debt_repaid", "share_repurchases", "acquisitions",
    "restructuring", "impairment",
})
```

`_USD_FIELDS` already filters out `_OUTLIER_EXCLUDE`, so no other code changes. The excluded
fields remain in `_FLOW_FIELDS` (the quarterly-sum check) where summation is still valid, and
remain fully captured/queryable — only the magnitude-outlier check skips them. The comment on
`_OUTLIER_EXCLUDE` is updated to state the principle (recurring fields are checked; event-driven
flows are not).

All six keys exist in the canonical registry (`debt_issued`, `debt_repaid`, `share_repurchases`,
`acquisitions`, `restructuring`, `impairment`).

## Testing (TDD)

In `tests/test_integrity.py`:
- **Fires guard (kept):** the existing `test_outlier_fires_on_1000x_spike` (a `revenue` interior
  spike) still fires — a recurring field is unaffected.
- **New:** a `debt_issued` series with a 100×-and-revert spike in an interior, scored year
  produces **no** `magnitude_outlier` finding (it's excluded). Pair it with a co-located
  `revenue` spike in the same fixture to assert revenue is still flagged while debt_issued is not
  — proving the exclusion is field-scoped, not global.

Full suite + ruff + mypy green.

## Global constraints

Python 3.9 (no `X|Y`); ruff 120 / E,F,W,I / imports at top; mypy clean (`integrity.py` covered by
the `src/validation` directory entry). Flag-only. Commit trailer
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Documentation

Update the README magnitude-outlier row to note that event-driven flows (debt issuance/repayment,
buybacks, M&A, impairments/restructuring) are excluded because they spike legitimately.

## Merge gate (empirical)

Re-run `python -m src.main GOOGL NVDA COST JNJ GS MET AMT CVX V CAT --no-yahoo` and confirm:
- **JNJ no longer shows `magnitude_outlier(debt_issued)`** (or any magnitude finding);
- no other company in the set gains or loses a `magnitude_outlier` finding;
- the six previously-clean companies (GOOGL/NVDA/COST/AMT/CVX/CAT) still score 100;
- full suite + ruff + mypy green.
(JNJ will not reach 100 from this fix alone — its `operating_income` and quarterly-cascade issues
are separate, out-of-scope items.)
