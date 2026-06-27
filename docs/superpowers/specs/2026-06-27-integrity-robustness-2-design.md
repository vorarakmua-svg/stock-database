# Integrity-Check Robustness Pass 2 (E + F + G) — Design Spec

**Date:** 2026-06-27
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/integrity-robustness-2` (off `main`)

## Context

A 20-stock live test (KO PG UNH PFE MRK ABBV TMO HD NKE DIS T VZ SO INTC ORCL CRM UNP SCHW LMT
SBUX) scored 13/20 at 100 and surfaced three independent over-sensitivity / coverage issues in
the data-quality checks. Each is fixed below; they become separate implementation tasks on one
branch.

- **E — `quarterly_sum_mismatch` over-sensitive on lumpy event-driven flows (PG, MRK, TMO, SBUX
  → 90).** Each is a single field, always `debt_repaid` or `acquisitions` — the same event-driven
  flows already excluded from `magnitude_outlier`. Their quarterly figures legitimately don't
  reconcile to the annual (gross-vs-net, refinancing reclassification, mid-quarter deals).
- **F — SCHW missing `noninterest_income` (score 50).** Charles Schwab is classified `bank` and
  the bank required-set demands `noninterest_income`, but Schwab is a broker-dealer: it reports
  `revenue` and `net_interest_income` (both resolve) plus granular fee/trading/commission lines,
  with **no traditional noninterest-income aggregate**. Same shape as the `operating_income` fix.
- **G — `roe` flagged out-of-bounds on buyback-depleted equity (HD → 94, ORCL → 97, LOW).**
  Confirmed correct data: HD FY2024 net income $15.1B on $1.0B equity → `roe` 14.5. Years of
  buybacks shrank book equity, so ROE explodes. The bounds check (meant to catch data errors)
  fires on a legitimately-extreme but accurate ratio.

## Decisions (from brainstorming)

E: exclude the event-driven flows from the quarterly-sum check. F: drop `noninterest_income` from
the bank required-set. G: suppress the `roe`/`roic` bounds-flag when the equity denominator is
weak (keep the value).

## E — quarterly-sum skips event-driven flows

**`src/validation/integrity.py`:** extract the event-driven flow keys into a shared constant and
reuse it in `_OUTLIER_EXCLUDE` (DRY):

```python
# Event-driven / lumpy flows: financing transactions, buybacks, M&A, one-time charges.
# Too noisy for the magnitude-outlier and quarterly-sum consistency checks (they spike in a
# single year and legitimately don't reconcile quarter-to-annual), but fully captured.
_EVENT_DRIVEN_FLOWS = frozenset({
    "debt_issued", "debt_repaid", "share_repurchases", "acquisitions",
    "restructuring", "impairment",
})
# Volatile net residuals (cash-flow reconciliation inputs) + the event-driven flows.
_OUTLIER_EXCLUDE = _EVENT_DRIVEN_FLOWS | frozenset({"net_change_in_cash", "fx_effect_on_cash"})
```

In `check_quarterly_sums`, inside the `for key in _FLOW_FIELDS` loop, add at the top:

```python
        if key in _EVENT_DRIVEN_FLOWS:
            continue
```

Core income/balance flows (revenue, cost_of_revenue, net_income, …) and the additive cash
residuals (`net_change_in_cash`, `fx_effect_on_cash` — which DO reconcile quarter-to-annual) stay
checked. `_USD_FIELDS` (the outlier candidate set) is unchanged in behavior because
`_OUTLIER_EXCLUDE` still contains the same eight keys.

## F — drop `noninterest_income` from bank-required

**`src/validation/quality.py`:** in `REQUIRED_BY_SECTOR`, the `BANK` tuple drops
`"noninterest_income"`, leaving:

```python
    BANK: (
        "revenue", "net_income", "net_interest_income",
        "total_assets", "total_liabilities", "total_equity", "total_deposits",
        "operating_cash_flow",
    ),
```

`noninterest_income` is still computed/captured wherever a bank tags it (and bank ratios that use
it are unaffected); its absence is simply no longer a `missing_field` penalty. No other sector
set changes.

## G — suppress `roe`/`roic` bounds-flag on a weak equity base

**`src/validation/integrity.py`:** change the signature to
`check_ratio_bounds(historical, annual, scored_years)`. Add a constant `_EQUITY_FLOOR = 0.05`. For
the metrics `roe` and `roic` only, suppress the out-of-bounds finding when the equity base is too
small for the ratio to be meaningful:

```python
def _weak_equity_base(period: Optional[Dict[str, Any]]) -> bool:
    """Equity denominator too small/negative for roe/roic to be a meaningful ratio."""
    if not isinstance(period, dict):
        return False
    equity = _num(period, "total_equity")
    if equity is None:
        return False
    if equity <= 0:
        return True
    assets = _num(period, "total_assets")
    return assets is not None and abs(equity) < _EQUITY_FLOOR * abs(assets)
```

In the bounds loop, when `metric in ("roe", "roic")` and `_weak_equity_base(annual.get(year))`,
skip the finding. **The metric value is untouched** in `historical` (still stored/exported) — only
the false data-quality flag is suppressed. All other metrics, and a genuinely-erroneous `roe`
(extreme value on a *normal* equity base), still fire.

**`src/fetchers/stock_data_fetcher.py`:** update the call site (currently
`check_ratio_bounds(historical, scored_years)`) to pass `annual`:
`check_ratio_bounds(historical, annual, scored_years)` (`annual` is already in scope there).

## Architecture / scope

- **Modified:** `src/validation/integrity.py` (E: `_EVENT_DRIVEN_FLOWS` + quarterly skip; G:
  `check_ratio_bounds` signature + equity guard), `src/validation/quality.py` (F: bank set),
  `src/fetchers/stock_data_fetcher.py` (G: call site), tests, `README.md`.
- **Untouched:** the metrics layer (roe still computed), the cash/outlier checks' behavior, the
  parser, the schema, all sector ratios.

## Testing (TDD)

- **E:** a fiscal year where a lumpy flow (`debt_repaid`) quarters don't sum to annual → **no**
  finding; a core field (`revenue`) mismatch in the same fixture → still one finding (proving the
  skip is scoped to event-driven flows).
- **F:** a bank-sector company missing only `noninterest_income` but otherwise complete → no
  `missing_field`, score 100; a real bank fixture that *does* report it is unaffected.
- **G:** a year with `roe` = 14.5 and `total_equity` ≈ 1% of `total_assets` → **no** finding;
  the same `roe` = 14.5 with a *normal* equity base → still flagged; `roa`/other ratios still
  bounds-checked. (Update the existing `check_ratio_bounds` tests for the new `annual` argument.)
- Full suite + ruff + mypy green.

## Global constraints

Python 3.9 (no `X|Y`); ruff 120 / E,F,W,I / imports at top; mypy clean (`integrity.py`/`quality.py`
via the `src/validation` dir entry, `stock_data_fetcher.py` explicit). Flag-only; checks never
mutate data. Materiality floor `$1,000,000`. Commit trailer
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Re-run `python -m src.main KO PG UNH PFE MRK ABBV TMO HD NKE DIS T VZ SO INTC ORCL CRM UNP SCHW
LMT SBUX --no-yahoo` and confirm:
- **PG/MRK/TMO/SBUX** no longer have `quarterly_sum_mismatch` → 100;
- **SCHW** no longer has `missing_field(noninterest_income)` → 100;
- **HD/ORCL** no longer have `ratio_out_of_bounds(roe)` → 100 (roe value still present in output);
- the 13 already-clean companies stay 100;
- **regression:** a real bank still captures `noninterest_income` and shows no new findings
  (`python -m src.main JPM BAC --no-yahoo` → 100, `noninterest_income` present);
- full suite + ruff + mypy green.
Only then merge.
