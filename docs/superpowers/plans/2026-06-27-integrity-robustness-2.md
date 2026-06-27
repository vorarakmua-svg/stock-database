# Integrity-Check Robustness Pass 2 (E + F + G) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three independent robustness fixes from the 20-stock test — quarterly-sum skips lumpy event-driven flows (E), banks no longer require `noninterest_income` (F), and `roe`/`roic` aren't flagged out-of-bounds on a buyback-depleted equity base (G).

**Architecture:** E + G touch `src/validation/integrity.py`; F touches `src/validation/quality.py`; G also updates the one call site in `src/fetchers/stock_data_fetcher.py`. Each is a separate task. The metrics layer, schema, and parser are untouched.

**Tech Stack:** Python 3.9+, pytest, ruff, mypy.

## Global Constraints

- Python floor **3.9** — no `X | Y` unions, no `match`.
- ruff line-length **120**, select `E, F, W, I`, imports at top.
- mypy clean; `integrity.py`/`quality.py` (via the `src/validation` directory entry) and `stock_data_fetcher.py` (explicit) are in the mypy `files` list — do not touch pyproject.
- Flag-only; checks never mutate data. Materiality floor `$1,000,000`.
- New constant `_EQUITY_FLOOR = 0.05`. `_QUARTERLY_TOL` stays `0.01`.
- The `roe` metric VALUE is never changed — G only suppresses the false flag.
- All existing tests stay green (some are updated where behavior legitimately changes — noted per task).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `feat/integrity-robustness-2` is already checked out.

---

### Task 1: E — quarterly-sum skips event-driven flows

**Files:**
- Modify: `src/validation/integrity.py` (`_OUTLIER_EXCLUDE` → shared `_EVENT_DRIVEN_FLOWS`; `check_quarterly_sums`)
- Test: `tests/test_integrity.py`

**Interfaces:**
- Produces: module-level `_EVENT_DRIVEN_FLOWS: frozenset[str]`; `_OUTLIER_EXCLUDE` now derived from it; `check_quarterly_sums` skips event-driven flows.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integrity.py`:

```python
def test_quarterly_sum_skips_event_driven_flows():
    # A lumpy event-driven flow (debt_repaid) whose quarters don't sum to annual is NOT
    # flagged; a core field (revenue) mismatch in the same year IS -- skip is field-scoped.
    annual = {"2024": {"debt_repaid": 1.0e9, "revenue": 1.0e9}}

    def _q(fq, dr, rev):
        return {"fiscal_year": 2024, "fiscal_quarter": fq, "debt_repaid": dr, "revenue": rev}

    quarterly = {  # each field's four quarters sum to 0.8e9 vs annual 1.0e9 (20% off)
        "2024-03-31": _q(1, 0.20e9, 0.20e9), "2024-06-30": _q(2, 0.20e9, 0.20e9),
        "2024-09-30": _q(3, 0.20e9, 0.20e9), "2024-12-31": _q(4, 0.20e9, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert len(findings) == 1
    assert "revenue" in findings[0].message
    assert "debt_repaid" not in findings[0].message
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_integrity.py::test_quarterly_sum_skips_event_driven_flows -v`
Expected: FAIL — currently `debt_repaid` is in `_FLOW_FIELDS` and mismatches, so it appears in the (aggregated) finding's message; `"debt_repaid" not in message` fails.

- [ ] **Step 3: Extract `_EVENT_DRIVEN_FLOWS` and reuse it in `_OUTLIER_EXCLUDE`**

In `src/validation/integrity.py`, replace the current `_OUTLIER_EXCLUDE` assignment block (the comment lines plus the `_OUTLIER_EXCLUDE = frozenset({ ... })` literal containing `net_change_in_cash`, `fx_effect_on_cash`, and the six event-driven keys) with:

```python
# Event-driven / lumpy flows: financing transactions, buybacks, M&A, one-time charges.
# Too noisy for the magnitude-outlier and quarterly-sum consistency checks (they spike in a
# single year and legitimately don't reconcile quarter-to-annual), but fully captured.
_EVENT_DRIVEN_FLOWS = frozenset({
    "debt_issued", "debt_repaid", "share_repurchases", "acquisitions",
    "restructuring", "impairment",
})
# Volatile net residuals (cash-flow reconciliation inputs) plus the event-driven flows.
_OUTLIER_EXCLUDE = _EVENT_DRIVEN_FLOWS | frozenset({"net_change_in_cash", "fx_effect_on_cash"})
```

(`_USD_FIELDS` still filters `f.key not in _OUTLIER_EXCLUDE`, and `_OUTLIER_EXCLUDE` still contains the same eight keys, so the magnitude-outlier check is behavior-unchanged.)

- [ ] **Step 4: Skip event-driven flows in `check_quarterly_sums`**

In `check_quarterly_sums`, the inner loop begins `for key in _FLOW_FIELDS:`. Add the skip as the first statement inside that loop, immediately before `ann_val = _num(ann, key)`:

```python
        for key in _FLOW_FIELDS:
            if key in _EVENT_DRIVEN_FLOWS:
                continue
            ann_val = _num(ann, key)
```

(The rest of the loop body — the `$1M` floor, four-quarter `_num` reads, the `_QUARTERLY_TOL` test, and the per-year aggregation — is unchanged.)

- [ ] **Step 5: Run the quarterly tests to verify they pass**

Run: `python -m pytest tests/test_integrity.py -k quarterly -v`
Expected: PASS — the new skip test plus the existing aggregation/fires/silent quarterly tests (those use core fields like `revenue`, unaffected).

- [ ] **Step 6: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite; +1 test).

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "fix: quarterly_sum skips event-driven flows (shared _EVENT_DRIVEN_FLOWS)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: F — drop `noninterest_income` from bank-required

**Files:**
- Modify: `src/validation/quality.py` (`REQUIRED_BY_SECTOR` BANK tuple)
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces: the `BANK` required-field tuple no longer contains `noninterest_income`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_quality.py`:

```python
def test_bank_not_required_to_report_noninterest_income():
    # Broker-dealers classified as banks (e.g. Schwab) report fee/trading/commission
    # components without a noninterest-income aggregate -> its absence is not a defect.
    period = {"revenue": 100.0, "net_income": 10.0, "net_interest_income": 40.0,
              "total_assets": 1000.0, "total_liabilities": 900.0, "total_equity": 100.0,
              "total_deposits": 500.0, "operating_cash_flow": 20.0}
    report = assess_annual({"2024": period}, sector="bank")
    assert not any(f.code == "missing_field" for f in report.findings)
    assert report.score == 100
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_quality.py::test_bank_not_required_to_report_noninterest_income -v`
Expected: FAIL — the BANK required set currently includes `noninterest_income`, so the period (which lacks it) yields a `missing_field` finding and `score` 90.

- [ ] **Step 3: Drop `noninterest_income` from the BANK tuple**

In `src/validation/quality.py`, in `REQUIRED_BY_SECTOR`, change the `BANK` entry from:

```python
    BANK: (
        "revenue", "net_income", "net_interest_income", "noninterest_income",
        "total_assets", "total_liabilities", "total_equity", "total_deposits",
        "operating_cash_flow",
    ),
```

to (remove `"noninterest_income"`):

```python
    BANK: (
        "revenue", "net_income", "net_interest_income",
        "total_assets", "total_liabilities", "total_equity", "total_deposits",
        "operating_cash_flow",
    ),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_quality.py -v`
Expected: PASS — the new test passes; all other quality tests pass. (If any existing test asserts a bank is flagged for a missing `noninterest_income`, update it — that is a legitimate behavior change.)

- [ ] **Step 5: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check src/validation/quality.py tests/test_quality.py && python -m mypy`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/validation/quality.py tests/test_quality.py
git commit -m "fix: banks no longer required to report a noninterest_income aggregate" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: G — suppress `roe`/`roic` bounds-flag on a weak equity base

**Files:**
- Modify: `src/validation/integrity.py` (`_EQUITY_FLOOR`, `_weak_equity_base`, `check_ratio_bounds` signature + guard)
- Modify: `src/fetchers/stock_data_fetcher.py` (call site)
- Test: `tests/test_integrity.py`

**Interfaces:**
- Consumes: `_num(period, key)` (existing helper in `integrity.py`).
- Produces: `check_ratio_bounds(historical, annual, scored_years)` — NOTE the new middle `annual` parameter.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integrity.py`:

```python
def test_ratio_bounds_suppresses_roe_on_weak_equity():
    # roe = 14.5 is out of bounds, but equity is ~1% of assets (buyback-depleted) -> the
    # extreme value is a denominator artifact, not a data error -> no finding.
    historical = {"2024": {"roe": 14.5}}
    weak = {"2024": {"total_equity": 1.0e9, "total_assets": 100.0e9}}
    assert check_ratio_bounds(historical, weak, {"2024"}) == []
    # the same roe on a NORMAL equity base is a genuine error -> still flagged.
    normal = {"2024": {"total_equity": 50.0e9, "total_assets": 100.0e9}}
    findings = check_ratio_bounds(historical, normal, {"2024"})
    assert [(f.code, f.message[:5]) for f in findings] == [("ratio_out_of_bounds", "'roe'")]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_integrity.py::test_ratio_bounds_suppresses_roe_on_weak_equity -v`
Expected: FAIL — `check_ratio_bounds` currently takes `(historical, scored_years)`, so the 3-argument call raises `TypeError`.

- [ ] **Step 3: Add the equity guard and the `annual` parameter**

In `src/validation/integrity.py`:

First, ensure `Optional` is imported — the `from typing import ...` line must include `Optional` (add it if absent).

Add a constant and helper just above `check_ratio_bounds` (after `_BOUNDED_METRICS`):

```python
_EQUITY_FLOOR = 0.05


def _weak_equity_base(period: Optional[Dict[str, Any]]) -> bool:
    """True when the equity denominator is too small/negative for roe/roic to be meaningful."""
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

Change `check_ratio_bounds` to accept `annual` and skip the `roe`/`roic` flag on a weak equity base:

```python
def check_ratio_bounds(
    historical: Dict[str, Dict[str, Any]],
    annual: Dict[str, Dict[str, Any]],
    scored_years: Iterable[str],
) -> List[Finding]:
    """Flag a computed metric that falls outside its mathematically-plausible range."""
    scored = set(scored_years)
    findings: List[Finding] = []
    for year in scored:
        metrics = historical.get(year)
        if not isinstance(metrics, dict):
            continue
        weak_equity = _weak_equity_base(annual.get(year))
        for metric in _BOUNDED_METRICS:
            if metric in ("roe", "roic") and weak_equity:
                continue
            v = metrics.get(metric)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and _out_of_bounds(metric, float(v)):
                findings.append(Finding(
                    LOW, "ratio_out_of_bounds",
                    f"'{metric}' = {float(v):.4f} is outside its plausible range.",
                    year,
                ))
    return findings
```

- [ ] **Step 4: Update the call site**

In `src/fetchers/stock_data_fetcher.py`, the line currently reads:

```python
            report.findings.extend(check_ratio_bounds(historical, scored_years))
```

Change it to pass `annual` (already defined a few lines above as `annual = stock.financials_annual or {}`):

```python
            report.findings.extend(check_ratio_bounds(historical, annual, scored_years))
```

- [ ] **Step 5: Update existing `check_ratio_bounds` tests for the new signature**

In `tests/test_integrity.py`, any existing call to `check_ratio_bounds(historical, scored_years)` now needs an `annual` argument. Add `{}` as the middle argument to each such existing call — `annual.get(year)` returns `None`, so `_weak_equity_base` is `False` and those tests keep their prior behavior:

```python
    # e.g. check_ratio_bounds(historical, scored)  ->  check_ratio_bounds(historical, {}, scored)
```

- [ ] **Step 6: Run the bounds tests + full suite + linters**

Run: `python -m pytest tests/test_integrity.py -k "ratio or bounds" -v`
Expected: PASS — the new suppression test plus the updated existing bounds tests.

Run: `python -m pytest -q`
Expected: PASS (full suite; +1 test).

Run: `python -m ruff check src/validation/integrity.py src/fetchers/stock_data_fetcher.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/validation/integrity.py src/fetchers/stock_data_fetcher.py tests/test_integrity.py
git commit -m "fix: don't flag roe/roic out-of-bounds on a buyback-depleted equity base" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the integrity-check notes**

In `README.md`, in the "### Integrity checks (data-quality score)" area, make these accurate updates (Read the table first; match the existing row wording):

1. **Quarterly-sum row** — note that event-driven flows are excluded, mirroring the magnitude-outlier row. Append to that row's description: `event-driven flows (debt issuance/repayment, buybacks, M&A, impairments/restructuring) are excluded, as for the magnitude-outlier check`.
2. **Ratio-bounds row** — note the weak-equity carve-out. Append: `roe/roic are not flagged when the equity base is negligible or negative (a buyback-depleted denominator makes the ratio meaningless, not wrong)`.
3. **Bank required fields** — wherever the docs list bank-sector required fields (or the sector-metrics section), ensure `noninterest_income` is described as captured-when-reported, not required (broker-dealers classified as banks report fee/trading components without the aggregate).

- [ ] **Step 2: Verify the full suite and linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: quarterly-sum/ratio-bounds carve-outs; noninterest_income not bank-required" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks) — the merge gate

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] **Live smoke (the validation):** run
  `python -m src.main KO PG UNH PFE MRK ABBV TMO HD NKE DIS T VZ SO INTC ORCL CRM UNP SCHW LMT SBUX --no-yahoo --formats json sqlite --db <scratch>/r2.db --output-dir <scratch>/r2 --workers 10`,
  then inspect each ticker's `data_quality.findings` and `score`. Expected:
  - **PG / MRK / TMO / SBUX** no longer have `quarterly_sum_mismatch` → 100;
  - **SCHW** no longer has `missing_field(noninterest_income)` → 100;
  - **HD / ORCL** no longer have `ratio_out_of_bounds(roe)` → 100, and the `roe` value is still present in their output;
  - the 13 already-clean companies stay 100.
- [ ] **Regression — real bank:** run `python -m src.main JPM BAC --no-yahoo ...`; confirm both score 100, still expose `noninterest_income`, and gain no new findings.
- [ ] Only after the smoke is clean: merge to `main` + clean up the branch.
