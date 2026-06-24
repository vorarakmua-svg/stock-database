# Integrity / Trust Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four flag-only integrity checks (magnitude outlier, cash-flow reconciliation, quarterly-sum, ratio bounds) that feed the existing 0–100 data-quality score, so confidently-wrong values surface instead of silently entering a screened-upon database.

**Architecture:** A new `src/validation/integrity.py` holds four pure check functions returning `quality.Finding` lists. `quality.py` exposes a small `score_for(findings)`. The fetcher's `_validate_and_score` splits into `_clean_and_derive` → (`_compute_metrics`) → `_assess`, so one quality pass sees cleaned data **and** computed metrics. No schema change; no data mutation.

**Tech Stack:** Python 3.9+, pytest, ruff, mypy, stdlib `statistics`.

## Global Constraints

- Python floor **3.9** — no `X | Y` unions, no `match`.
- ruff line-length **120**, lint select `E, F, W, I`; **all imports at top of file** (a mid-file import trips E402).
- mypy must pass clean; add `src/validation/integrity.py` to `[tool.mypy] files`; fully annotate it.
- **Flag-only:** checks NEVER mutate, null, or correct a value — they only emit `Finding`s.
- Materiality floor **$1,000,000** and scoring window **recent 5 fiscal years** apply to every check.
- Thresholds (verbatim): outlier `≥ 100×` field median; cash residual `> 5%`; quarterly per-field `> 1%`; ratio bounds per the table in Task 5.
- Severities/penalties (reuse `quality._PENALTY`): magnitude_outlier **HIGH (−25)**, cashflow_imbalance **MEDIUM (−10)**, quarterly_sum_mismatch **MEDIUM (−10)**, ratio_out_of_bounds **LOW (−3)**.
- All existing tests stay green; clean filings must keep score **100** (no false-positive regressions).
- Every commit message ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `feat/integrity-hardening` is already checked out.

---

### Task 1: Extract `score_for` in quality.py

Pull the score computation out of `assess_annual` into a reusable function so the fetcher can recompute the score after appending integrity findings.

**Files:**
- Modify: `src/validation/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces: `score_for(findings: List[Finding]) -> int` — `max(0, 100 − Σ _PENALTY[severity])`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_quality.py`:

```python
from src.validation.quality import (
    HIGH, LOW, MEDIUM, Finding, score_for,
)


def test_score_for_no_findings_is_100():
    assert score_for([]) == 100


def test_score_for_sums_penalties_and_clamps():
    findings = [Finding(HIGH, "x", "m"), Finding(MEDIUM, "y", "m"), Finding(LOW, "z", "m")]
    assert score_for(findings) == 100 - 25 - 10 - 3  # 62
    # clamps at 0
    assert score_for([Finding(HIGH, "x", "m")] * 10) == 0
```

(If `tests/test_quality.py` already imports some of these names at the top, merge into the existing top-of-file import rather than adding a second import line.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality.py -k score_for -v`
Expected: FAIL — `ImportError: cannot import name 'score_for'`

- [ ] **Step 3: Add `score_for` and use it in `assess_annual`**

In `src/validation/quality.py`, add this module-level function (place it just after the `_num` helper):

```python
def score_for(findings: List[Finding]) -> int:
    """0-100 quality score: 100 minus summed severity penalties, clamped at 0."""
    penalty = sum(_PENALTY.get(f.severity, 0) for f in findings)
    return max(0, 100 - penalty)
```

Then replace the score computation at the end of `assess_annual`:

```python
    penalty = sum(_PENALTY.get(f.severity, 0) for f in report.findings)
    report.score = max(0, 100 - penalty)
    return report
```

with:

```python
    report.score = score_for(report.findings)
    return report
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_quality.py -v`
Expected: PASS (new score_for tests + all existing quality tests unchanged)

Run: `python -m mypy && python -m ruff check src/validation/quality.py tests/test_quality.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/validation/quality.py tests/test_quality.py
git commit -m "refactor: extract score_for from assess_annual" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: integrity.py + magnitude-outlier check

Create the module with shared constants and the first check.

**Files:**
- Create: `src/validation/integrity.py`
- Modify: `pyproject.toml` (add to mypy `files`)
- Test: `tests/test_integrity.py`

**Interfaces:**
- Consumes: `Finding`, `HIGH`, `MEDIUM`, `LOW`, `_num` from `quality` (Task 1's module).
- Produces:
  - `check_field_outliers(annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]) -> List[Finding]`
  - Module constants `_MATERIALITY = 1_000_000.0`, `_OUTLIER_FACTOR = 100.0`, `_USD_FIELDS`, `_FLOW_FIELDS`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_integrity.py`:

```python
"""Integrity checks: magnitude outliers, cash reconciliation, quarterly sums, ratio bounds."""

from src.validation.integrity import check_field_outliers


def _yr(revenue, assets):
    return {"revenue": revenue, "total_assets": assets}


def test_outlier_fires_on_1000x_spike():
    annual = {
        "2021": _yr(1.0e9, 2.0e9),
        "2022": _yr(1.1e9, 2.1e9),
        "2023": _yr(1.2e9, 2.2e9),
        "2024": _yr(1.2e12, 2.3e9),  # revenue 1000x its own median
    }
    findings = check_field_outliers(annual, {"2024", "2023", "2022", "2021"})
    codes = [(f.code, f.period) for f in findings]
    assert ("magnitude_outlier", "2024") in codes
    assert all(f.severity == "high" for f in findings)


def test_outlier_silent_on_real_growth_and_small_series():
    # A real ~3x trend over 4 years: no field is 100x its own median.
    annual = {
        "2021": _yr(1.0e9, 2.0e9), "2022": _yr(1.5e9, 2.5e9),
        "2023": _yr(2.2e9, 3.0e9), "2024": _yr(3.0e9, 3.5e9),
    }
    assert check_field_outliers(annual, {"2021", "2022", "2023", "2024"}) == []
    # Fewer than 3 data points -> no median basis -> silent.
    assert check_field_outliers({"2024": _yr(1.0e9, 2.0e9)}, {"2024"}) == []


def test_outlier_only_flags_scored_years():
    annual = {
        "2019": _yr(1.0e9, 2.0e9), "2020": _yr(1.1e9, 2.1e9),
        "2021": _yr(1.2e9, 2.2e9), "2022": _yr(9.9e14, 2.3e9),  # spike, but out of window
    }
    # 2022 not in the scored set -> not flagged.
    assert check_field_outliers(annual, {"2021", "2020", "2019"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integrity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.validation.integrity'`

- [ ] **Step 3: Create the module**

Create `src/validation/integrity.py`:

```python
"""Integrity checks over standardized financials and computed metrics.

Catches confidently-wrong values that per-period validation in ``quality.py``
cannot: magnitude outliers vs a field's own history, cash-flow statements that
don't reconcile to the change in balance-sheet cash, discrete quarters that
don't sum to the annual figure, and metrics outside plausible bounds. Every
check is FLAG-ONLY — it emits Findings and never mutates data.
"""

import statistics
from typing import Any, Dict, Iterable, List

from ..mappings.canonical import CANONICAL_FIELDS, CASHFLOW, DURATION, INCOME, UNIT_USD
from .quality import HIGH, Finding, _num

# Ignore sub-$1M figures (rounding/noise) across all checks.
_MATERIALITY = 1_000_000.0

# USD "level" fields (income/balance/cash-flow amounts); excludes per-share and
# share-count fields. Candidates for the magnitude-outlier check.
_USD_FIELDS = tuple(f.key for f in CANONICAL_FIELDS if f.unit == UNIT_USD)

# Flow fields whose quarters should sum to the annual figure.
_FLOW_FIELDS = tuple(
    f.key for f in CANONICAL_FIELDS
    if f.unit == UNIT_USD and f.kind == DURATION and f.statement in (INCOME, CASHFLOW)
)

_OUTLIER_FACTOR = 100.0


def check_field_outliers(
    annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag a USD field whose magnitude is >=100x its own across-year median.

    A value 100x above its field's median is almost certainly a mis-resolved tag
    or filing error (real year-over-year growth never approaches 100x). Uses all
    available years for the median but only flags periods in ``scored_years``.
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    for key in _USD_FIELDS:
        points = []  # (year, signed_value, magnitude)
        for year, period in annual.items():
            v = _num(period, key)
            if v is not None and v != 0:
                points.append((year, v, abs(v)))
        mags = [m for _, _, m in points]
        if len(mags) < 3:
            continue
        median = statistics.median(mags)
        if median < _MATERIALITY:
            continue
        for year, value, mag in points:
            if year in scored and mag / median >= _OUTLIER_FACTOR:
                findings.append(Finding(
                    HIGH, "magnitude_outlier",
                    f"'{key}' = {value:,.0f} is {mag / median:.0f}x its median "
                    f"({median:,.0f}); likely a mis-resolved tag or filing error.",
                    year,
                ))
    return findings
```

- [ ] **Step 4: Add the module to mypy's checked files**

In `pyproject.toml`, inside `[tool.mypy] files = [ ... ]`, add (near the other `src/validation` entry):

```toml
    "src/validation/integrity.py",
```

- [ ] **Step 5: Run tests + linters to verify**

Run: `python -m pytest tests/test_integrity.py -v`
Expected: PASS (3 tests)

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/validation/integrity.py pyproject.toml tests/test_integrity.py
git commit -m "feat: magnitude-outlier integrity check" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: cash-flow reconciliation check

**Files:**
- Modify: `src/validation/integrity.py`
- Test: `tests/test_integrity.py`

**Interfaces:**
- Consumes: `MEDIUM` from `quality`; `_MATERIALITY` from this module.
- Produces: `check_cashflow_reconciliation(annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]) -> List[Finding]`; constant `_CASH_TOL = 0.05`.

- [ ] **Step 1: Write the failing test**

Update the top import of `tests/test_integrity.py` to add the new name:

```python
from src.validation.integrity import check_cashflow_reconciliation, check_field_outliers
```

Append these tests:

```python
def _cf(cash, ocf, icf, fcf):
    return {"cash_and_equivalents": cash, "operating_cash_flow": ocf,
            "investing_cash_flow": icf, "financing_cash_flow": fcf}


def test_cashflow_imbalance_fires_when_flows_miss_delta_cash():
    annual = {
        "2023": _cf(1.00e9, 0, 0, 0),
        # delta_cash = +1.0e9, but flows sum to +0.5e9 -> 50% residual.
        "2024": _cf(2.00e9, 0.4e9, -0.1e9, 0.2e9),
    }
    findings = check_cashflow_reconciliation(annual, {"2024", "2023"})
    assert [(f.code, f.period, f.severity) for f in findings] == [
        ("cashflow_imbalance", "2024", "medium")]


def test_cashflow_reconcile_tolerates_small_gap():
    # delta_cash = +1.0e9; flows sum to +0.97e9 -> 3% residual, within 5%.
    annual = {"2023": _cf(1.0e9, 0, 0, 0), "2024": _cf(2.0e9, 0.9e9, -0.1e9, 0.17e9)}
    assert check_cashflow_reconciliation(annual, {"2024", "2023"}) == []


def test_cashflow_reconcile_silent_on_missing_fields():
    annual = {"2023": {"cash_and_equivalents": 1.0e9},
              "2024": {"cash_and_equivalents": 2.0e9}}  # no flow fields
    assert check_cashflow_reconciliation(annual, {"2024", "2023"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integrity.py -k cashflow -v`
Expected: FAIL — `ImportError: cannot import name 'check_cashflow_reconciliation'`

- [ ] **Step 3: Add the function**

In `src/validation/integrity.py`, add `MEDIUM` to the quality import:

```python
from .quality import HIGH, MEDIUM, Finding, _num
```

Add the constant near `_OUTLIER_FACTOR`:

```python
_CASH_TOL = 0.05
```

Add the function:

```python
def check_cashflow_reconciliation(
    annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag when change in balance-sheet cash != operating+investing+financing flows.

    The 5% tolerance absorbs the foreign-exchange-effect-on-cash line (not a
    canonical field) and minor restricted-cash reclassifications.
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    years = sorted(annual.keys())
    for prev, curr in zip(years, years[1:]):
        if curr not in scored:
            continue
        cash_curr = _num(annual[curr], "cash_and_equivalents")
        cash_prev = _num(annual[prev], "cash_and_equivalents")
        ocf = _num(annual[curr], "operating_cash_flow")
        icf = _num(annual[curr], "investing_cash_flow")
        fcf = _num(annual[curr], "financing_cash_flow")
        if (cash_curr is None or cash_prev is None or ocf is None
                or icf is None or fcf is None):
            continue
        delta = cash_curr - cash_prev
        flow_sum = ocf + icf + fcf
        denom = max(abs(delta), abs(flow_sum))
        if denom < _MATERIALITY:
            continue
        residual = delta - flow_sum
        if abs(residual) / denom > _CASH_TOL:
            findings.append(Finding(
                MEDIUM, "cashflow_imbalance",
                f"change in cash ({delta:,.0f}) != operating+investing+financing "
                f"cash flow ({flow_sum:,.0f}); residual {residual:,.0f}.",
                curr,
            ))
    return findings
```

- [ ] **Step 4: Run tests + linters**

Run: `python -m pytest tests/test_integrity.py -v`
Expected: PASS (all outlier + cashflow tests)

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "feat: cash-flow reconciliation integrity check" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: quarterly-sum check

**Files:**
- Modify: `src/validation/integrity.py`
- Test: `tests/test_integrity.py`

**Interfaces:**
- Consumes: `_FLOW_FIELDS`, `_MATERIALITY`, `MEDIUM`.
- Produces: `check_quarterly_sums(annual: Dict[str, Dict[str, Any]], quarterly: Dict[str, Dict[str, Any]], scored_years: Iterable[str]) -> List[Finding]`; constant `_QUARTERLY_TOL = 0.01`.

- [ ] **Step 1: Write the failing test**

Update the top import of `tests/test_integrity.py`:

```python
from src.validation.integrity import (
    check_cashflow_reconciliation,
    check_field_outliers,
    check_quarterly_sums,
)
```

Append these tests:

```python
def _q(fy, fq, revenue):
    return {"fiscal_year": fy, "fiscal_quarter": fq, "revenue": revenue}


def test_quarterly_sum_mismatch_fires():
    annual = {"2024": {"revenue": 1.0e9}}
    quarterly = {  # quarters sum to 0.80e9, annual says 1.0e9 -> 20% off
        "2024-03-31": _q(2024, 1, 0.20e9), "2024-06-30": _q(2024, 2, 0.20e9),
        "2024-09-30": _q(2024, 3, 0.20e9), "2024-12-31": _q(2024, 4, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert [(f.code, f.period, f.severity) for f in findings] == [
        ("quarterly_sum_mismatch", "2024", "medium")]


def test_quarterly_sum_exact_ladder_is_silent():
    annual = {"2024": {"revenue": 1.0e9}}
    quarterly = {
        "2024-03-31": _q(2024, 1, 0.25e9), "2024-06-30": _q(2024, 2, 0.25e9),
        "2024-09-30": _q(2024, 3, 0.25e9), "2024-12-31": _q(2024, 4, 0.25e9),
    }
    assert check_quarterly_sums(annual, quarterly, {"2024"}) == []


def test_quarterly_sum_silent_with_only_three_quarters():
    annual = {"2024": {"revenue": 1.0e9}}
    quarterly = {
        "2024-03-31": _q(2024, 1, 0.25e9), "2024-06-30": _q(2024, 2, 0.25e9),
        "2024-09-30": _q(2024, 3, 0.25e9),  # Q4 missing
    }
    assert check_quarterly_sums(annual, quarterly, {"2024"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integrity.py -k quarterly -v`
Expected: FAIL — `ImportError: cannot import name 'check_quarterly_sums'`

- [ ] **Step 3: Add the function**

In `src/validation/integrity.py`, add the constant near `_CASH_TOL`:

```python
_QUARTERLY_TOL = 0.01
```

Add the function:

```python
def check_quarterly_sums(
    annual: Dict[str, Dict[str, Any]],
    quarterly: Dict[str, Dict[str, Any]],
    scored_years: Iterable[str],
) -> List[Finding]:
    """Flag a flow field whose four discrete quarters don't sum to the annual figure.

    Validates the cumulative-ladder differencing. Only runs for a fiscal year that
    has all four discrete quarters and is in the scored window.
    """
    scored = set(scored_years)
    by_fy: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for period in quarterly.values():
        fq = period.get("fiscal_quarter")
        fy = period.get("fiscal_year")
        if fq in (1, 2, 3, 4) and fy is not None:
            by_fy.setdefault(int(fy), {})[int(fq)] = period

    findings: List[Finding] = []
    for year in scored:
        if not str(year).isdigit():
            continue
        quarters = by_fy.get(int(year))
        ann = annual.get(year)
        if not quarters or set(quarters) != {1, 2, 3, 4} or not ann:
            continue
        for key in _FLOW_FIELDS:
            ann_val = _num(ann, key)
            if ann_val is None or abs(ann_val) < _MATERIALITY:
                continue
            q_vals = [_num(quarters[q], key) for q in (1, 2, 3, 4)]
            if any(v is None for v in q_vals):
                continue
            sum_q = sum(v for v in q_vals if v is not None)
            if abs(sum_q - ann_val) / abs(ann_val) > _QUARTERLY_TOL:
                findings.append(Finding(
                    MEDIUM, "quarterly_sum_mismatch",
                    f"'{key}': four quarters sum to {sum_q:,.0f} but annual FY{year} "
                    f"is {ann_val:,.0f}.",
                    year,
                ))
    return findings
```

- [ ] **Step 4: Run tests + linters**

Run: `python -m pytest tests/test_integrity.py -v`
Expected: PASS (outlier + cashflow + quarterly tests)

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "feat: quarterly-sum integrity check" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: ratio-bounds check

**Files:**
- Modify: `src/validation/integrity.py`
- Test: `tests/test_integrity.py`

**Interfaces:**
- Consumes: `LOW` from `quality`.
- Produces: `check_ratio_bounds(historical: Dict[str, Dict[str, Any]], scored_years: Iterable[str]) -> List[Finding]`.

- [ ] **Step 1: Write the failing test**

Update the top import of `tests/test_integrity.py`:

```python
from src.validation.integrity import (
    check_cashflow_reconciliation,
    check_field_outliers,
    check_quarterly_sums,
    check_ratio_bounds,
)
```

Append these tests:

```python
def test_ratio_bounds_fire_on_impossible_values():
    historical = {"2024": {"gross_margin": 2.5, "efficiency_ratio": -0.3, "roe": 0.2}}
    findings = check_ratio_bounds(historical, {"2024"})
    metrics_flagged = {f.message.split("'")[1] for f in findings}
    assert "gross_margin" in metrics_flagged      # >1.01 impossible
    assert "efficiency_ratio" in metrics_flagged   # <=0 impossible
    assert "roe" not in metrics_flagged            # 20% is fine
    assert all(f.severity == "low" and f.code == "ratio_out_of_bounds" for f in findings)


def test_ratio_bounds_silent_on_strong_but_real_values():
    # Apple-like: 82% ROIC, 26% net margin, 45% gross margin — all plausible.
    historical = {"2024": {"roic": 0.82, "net_margin": 0.26, "gross_margin": 0.45}}
    assert check_ratio_bounds(historical, {"2024"}) == []


def test_ratio_bounds_skip_none_and_unscored_years():
    historical = {"2024": {"gross_margin": None}, "2019": {"gross_margin": 9.0}}
    assert check_ratio_bounds(historical, {"2024"}) == []  # None skipped, 2019 unscored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integrity.py -k ratio_bounds -v`
Expected: FAIL — `ImportError: cannot import name 'check_ratio_bounds'`

- [ ] **Step 3: Add the function**

In `src/validation/integrity.py`, add `LOW` to the quality import:

```python
from .quality import HIGH, LOW, MEDIUM, Finding, _num
```

Add the function (the `_out_of_bounds` helper encodes the bounds table from the spec):

```python
def _out_of_bounds(metric: str, v: float) -> bool:
    if metric in ("gross_margin", "operating_margin", "ebitda_margin"):
        return v > 1.01
    if metric in ("net_margin", "fcf_margin"):
        return abs(v) > 2.0
    if metric in ("roe", "roa", "roic"):
        return abs(v) > 5.0
    if metric == "efficiency_ratio":
        return v <= 0 or v > 2.0
    if metric in ("loss_ratio", "combined_ratio"):
        return v < 0 or v > 3.0
    if metric in ("loan_to_deposit", "ffo_payout"):
        return v < 0 or v > 5.0
    if metric == "net_interest_margin":
        return v < 0 or v > 0.25
    return False


_BOUNDED_METRICS = (
    "gross_margin", "operating_margin", "ebitda_margin", "net_margin", "fcf_margin",
    "roe", "roa", "roic", "efficiency_ratio", "loss_ratio", "combined_ratio",
    "loan_to_deposit", "ffo_payout", "net_interest_margin",
)


def check_ratio_bounds(
    historical: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag a computed metric that falls outside its mathematically-plausible range."""
    scored = set(scored_years)
    findings: List[Finding] = []
    for year in scored:
        metrics = historical.get(year)
        if not isinstance(metrics, dict):
            continue
        for metric in _BOUNDED_METRICS:
            v = metrics.get(metric)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and _out_of_bounds(metric, float(v)):
                findings.append(Finding(
                    LOW, "ratio_out_of_bounds",
                    f"'{metric}' = {float(v):.4f} is outside its plausible range.",
                    year,
                ))
    return findings
```

- [ ] **Step 4: Run tests + linters**

Run: `python -m pytest tests/test_integrity.py -v`
Expected: PASS (all four checks' tests)

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "feat: ratio-bounds integrity check" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Wire integrity into the fetcher's quality pass

Split `_validate_and_score` into `_clean_and_derive` + `_assess`, run them around `_compute_metrics`, and merge the four integrity checks into the single report.

**Files:**
- Modify: `src/fetchers/stock_data_fetcher.py`
- Test: `tests/test_fetcher_concurrency.py`

**Interfaces:**
- Consumes: `assess_annual`, `score_for` (quality); the four `check_*` functions (integrity); `StockData`.
- Produces: `StockDataFetcher._clean_and_derive(self, stock) -> None` and `StockDataFetcher._assess(self, stock) -> None` (replacing `_validate_and_score`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetcher_concurrency.py`:

```python
def test_assess_flags_magnitude_outlier_end_to_end(tmp_path):
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="BAD", cik="000", company_name="Bad Inc.")
    # Full required-field set so the only finding is the revenue outlier, not
    # unrelated missing-field findings.
    base = {"net_income": 1.0e8, "operating_income": 1.5e8, "total_assets": 2.0e9,
            "total_liabilities": 1.0e9, "total_equity": 1.0e9, "operating_cash_flow": 2.0e8}
    stock.financials_annual = {
        "2021": {"fiscal_year": 2021, "revenue": 1.0e9, **base},
        "2022": {"fiscal_year": 2022, "revenue": 1.1e9, **base},
        "2023": {"fiscal_year": 2023, "revenue": 1.2e9, **base},
        "2024": {"fiscal_year": 2024, "revenue": 1.2e12, **base},  # 1000x revenue spike
    }
    fetcher._clean_and_derive(stock)
    fetcher._compute_metrics(stock)
    fetcher._assess(stock)
    codes = [f["code"] for f in stock.data_quality["findings"]]
    assert "magnitude_outlier" in codes
    assert stock.data_quality["score"] < 100


def test_assess_clean_company_scores_100(tmp_path):
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="OK", cik="000", company_name="OK Inc.")
    stock.financials_annual = {
        str(y): {"fiscal_year": y, "revenue": 1.0e9 + (y - 2021) * 1.0e8,
                 "net_income": 1.0e8, "operating_income": 1.5e8,
                 "total_assets": 2.0e9, "total_liabilities": 1.0e9,
                 "total_equity": 1.0e9, "operating_cash_flow": 2.0e8}
        for y in (2021, 2022, 2023, 2024)
    }
    fetcher._clean_and_derive(stock)
    fetcher._compute_metrics(stock)
    fetcher._assess(stock)
    integrity_codes = {"magnitude_outlier", "cashflow_imbalance",
                       "quarterly_sum_mismatch", "ratio_out_of_bounds"}
    codes = {f["code"] for f in stock.data_quality["findings"]}
    assert not (codes & integrity_codes)   # no integrity findings on clean data
    assert stock.data_quality["score"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetcher_concurrency.py -k assess -v`
Expected: FAIL — `AttributeError: 'StockDataFetcher' object has no attribute '_clean_and_derive'`

- [ ] **Step 3: Update imports**

In `src/fetchers/stock_data_fetcher.py`, change the quality import and add the integrity import. The current line:

```python
from ..validation.quality import assess_annual
```

becomes:

```python
from ..validation.integrity import (
    check_cashflow_reconciliation,
    check_field_outliers,
    check_quarterly_sums,
    check_ratio_bounds,
)
from ..validation.quality import assess_annual, score_for
```

(Keep these grouped with the other `..` imports, alphabetically: `integrity` before `quality`.)

- [ ] **Step 4: Replace `_validate_and_score` with `_clean_and_derive` + `_assess`**

Replace the entire `_validate_and_score` method:

```python
    def _validate_and_score(self, stock: StockData) -> None:
        """Derive identities, validate/coerce periods, attach a data-quality report."""
        # Derive missing fields (e.g. total_liabilities = assets - equity), then
        # validate + coerce each period; surface validation errors as warnings.
        for attr in ("financials_annual", "financials_quarterly"):
            periods = getattr(stock, attr)
            if not periods:
                continue
            cleaned = {}
            for period_key, period in periods.items():
                apply_derivations(period)
                clean, errors = validate_period(period)
                cleaned[period_key] = clean
                for err in errors:
                    stock.add_warning(f"validation {attr} {period_key}: {err}")
            setattr(stock, attr, cleaned)

        # Trailing-twelve-month series from the (cleaned) discrete quarters.
        if stock.financials_quarterly:
            stock.financials_ttm = compute_ttm(stock.financials_quarterly)

        # Score annual financials (sector-aware) and record findings.
        report = assess_annual(stock.financials_annual, sector=stock.sector_class)
        stock.data_quality = report.as_dict()
        stock.data_quality["unmapped_tag_count"] = len(stock.unmapped_facts)
        for message in report.warning_messages():
            stock.add_warning(message)
```

with these two methods:

```python
    def _clean_and_derive(self, stock: StockData) -> None:
        """Derive identities, validate/coerce each period, and build the TTM series."""
        for attr in ("financials_annual", "financials_quarterly"):
            periods = getattr(stock, attr)
            if not periods:
                continue
            cleaned = {}
            for period_key, period in periods.items():
                apply_derivations(period)
                clean, errors = validate_period(period)
                cleaned[period_key] = clean
                for err in errors:
                    stock.add_warning(f"validation {attr} {period_key}: {err}")
            setattr(stock, attr, cleaned)

        if stock.financials_quarterly:
            stock.financials_ttm = compute_ttm(stock.financials_quarterly)

    def _assess(self, stock: StockData) -> None:
        """Attach a data-quality report: sector checks + integrity checks + score.

        Runs after metrics are computed so the ratio-bounds check can see them.
        """
        annual = stock.financials_annual or {}
        quarterly = stock.financials_quarterly or {}
        historical = (stock.calculated_metrics or {}).get("historical", {})
        scored_years = set(sorted(annual.keys(), reverse=True)[:5])

        report = assess_annual(annual, sector=stock.sector_class)
        report.findings.extend(check_field_outliers(annual, scored_years))
        report.findings.extend(check_cashflow_reconciliation(annual, scored_years))
        report.findings.extend(check_quarterly_sums(annual, quarterly, scored_years))
        report.findings.extend(check_ratio_bounds(historical, scored_years))
        report.score = score_for(report.findings)

        stock.data_quality = report.as_dict()
        stock.data_quality["unmapped_tag_count"] = len(stock.unmapped_facts)
        for message in report.warning_messages():
            stock.add_warning(message)
```

- [ ] **Step 5: Rewire the three call sites in `fetch_ticker`**

Find this block:

```python
        # Validate/coerce standardized financials and assess data quality.
        if stock.financials_annual or stock.financials_quarterly:
            try:
                self._validate_and_score(stock)
            except Exception as e:
                self.logger.warning(f"Data-quality validation error for {ticker}: {e}")
                stock.add_warning(f"Data quality: {str(e)}")
```

Replace it with (clean/derive only):

```python
        # Clean, derive identities, and build the TTM series.
        if stock.financials_annual or stock.financials_quarterly:
            try:
                self._clean_and_derive(stock)
            except Exception as e:
                self.logger.warning(f"Data cleaning error for {ticker}: {e}")
                stock.add_warning(f"Data cleaning: {str(e)}")
```

Then find the metrics block:

```python
        # Calculate derived metrics (generic + sector-aware).
        if stock.financials_annual:
            try:
                self._compute_metrics(stock)
            except Exception as e:
                self.logger.warning(f"Metrics calculation error for {ticker}: {e}")
                stock.add_warning(f"Calculated metrics: {str(e)}")
```

and insert the assessment block immediately AFTER it:

```python
        # Assess data quality (sector + integrity checks), now that metrics exist.
        if stock.financials_annual or stock.financials_quarterly:
            try:
                self._assess(stock)
            except Exception as e:
                self.logger.warning(f"Data-quality assessment error for {ticker}: {e}")
                stock.add_warning(f"Data quality: {str(e)}")
```

- [ ] **Step 6: Run tests + linters**

Run: `python -m pytest tests/test_fetcher_concurrency.py -v`
Expected: PASS (existing concurrency/sector tests + the two new assess tests)

Run: `python -m ruff check src/fetchers/stock_data_fetcher.py tests/test_fetcher_concurrency.py`
Expected: no errors. (Note: `stock_data_fetcher.py` is not in the mypy `files` list.)

- [ ] **Step 7: Commit**

```bash
git add src/fetchers/stock_data_fetcher.py tests/test_fetcher_concurrency.py
git commit -m "feat: run integrity checks in the fetcher quality pass" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Documentation + full-suite verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a README subsection**

Find the data-quality / validation section in `README.md` (search for the quality-score or validation heading) and add this subsection after it. If no such section exists, add it after the "Sector-aware metrics" section:

```markdown
### Integrity checks (data-quality score)

Beyond required-field and accounting-identity checks, the quality layer runs four
**flag-only** integrity checks (they surface issues via findings + the 0–100 score; they
never alter data):

| Check | Catches | Threshold | Penalty |
|---|---|---|---|
| Magnitude outlier | a USD field wildly inconsistent with its own history (mis-resolved tag / filing error) | ≥ 100× the field's median | −25 |
| Cash-flow reconciliation | a cash-flow statement that doesn't explain the change in balance-sheet cash | residual > 5% | −10 |
| Quarterly-sum | discrete quarters that don't sum to the annual figure | per-field > 1% | −10 |
| Ratio bounds | a computed metric outside its plausible range (e.g. >100% gross margin) | impossibility bounds | −3 |

Thresholds are deliberately wide (a $1M materiality floor; the most recent 5 fiscal years are
scored), so clean filings keep a score of 100. Findings appear in `data_quality.findings` and,
for medium+ severity, in `warnings`.
```

- [ ] **Step 2: Verify the full suite and linters**

Run: `python -m pytest -q`
Expected: PASS — full suite (was 125; now 125 + the new integrity/quality/fetcher tests). A pre-existing `pytest-asyncio` startup DeprecationWarning is environment noise, not a failure.

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document data-quality integrity checks" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] Live smoke (clean large-cap should stay 100): `python -m src.main AAPL MSFT --no-yahoo` then
  `SELECT ticker, quality_score FROM collection_runs ORDER BY collected_at DESC LIMIT 2;`
  Expected: quality_score 100 (or unchanged from pre-feature) — i.e. no false-positive regressions on clean filings.
- [ ] Confirm `data_quality.findings` carries integrity codes only when warranted (spot-check the JSON export for a company with a known restatement vs. a clean one).
