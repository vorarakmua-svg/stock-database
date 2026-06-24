# Sector-Aware Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the derived-metrics layer sector-aware so banks/insurers/REITs get the ratios that actually describe them, and generic ratios that are meaningless for a sector are stored `NULL` instead of misleading numbers.

**Architecture:** A new leaf module `metric_utils.py` holds the shared field resolver. A new `sector_metrics.py` holds per-sector ratio functions, a suppression map, and an `apply_sector` orchestrator. `CalculatedMetrics.calculate_all` gains an optional `sector` param that merges sector extras and applies suppression; `sector=None` is exactly today's behavior. The fetcher passes `stock.sector_class` through; the SQLite store gains the new ratio columns (auto-migrated).

**Tech Stack:** Python 3.9+, pytest, ruff, mypy, sqlite3 (stdlib).

## Global Constraints

- Python floor: **3.9** (`requires-python >=3.8`; mypy/ruff target `py39`). No 3.10+ syntax (no `X | Y` unions, no `match`).
- ruff: line-length **120**, lint select `E, F, W, I`.
- mypy: only files in the `[tool.mypy] files` list are checked; **new modules must be added there** and must be fully type-annotated.
- All **107 existing tests must stay green**. The general-company path (`sector=None`) must be byte-for-byte unchanged in behavior.
- **No changes** to `src/mappings/canonical.py` or `src/parsers/xbrl_parser.py`. Every ratio is computed from canonical fields that already exist.
- Metric convention: a missing/unresolvable input yields `None` (never raises); all division denominators guarded `> 0`.
- Keys are `snake_case`. Suppression keys must match the exact strings `calculate_all` emits (e.g. `days_inventory_outstanding`, not `dio`).
- Every commit message ends with the trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Work happens on branch `feat/sector-aware-metrics` (already checked out).

---

### Task 1: Shared field resolver (`metric_utils.py`)

Extract the existing `CalculatedMetrics._get_value` body into a standalone module-level function so `sector_metrics.py` can reuse it **without importing `calculated_metrics`** (which would create a circular import once `calculate_all` imports `apply_sector`).

**Files:**
- Create: `src/parsers/metric_utils.py`
- Modify: `src/parsers/calculated_metrics.py` (add import; make `_get_value` delegate)
- Modify: `pyproject.toml` (add `metric_utils.py` to mypy `files`)
- Test: `tests/test_metric_utils.py`

**Interfaces:**
- Produces: `field_value(data: Dict[str, Any], keys: List[str]) -> Optional[float]` — returns the first present, numeric-coercible value among `keys`, else `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_metric_utils.py`:

```python
"""The shared canonical-field resolver used by both metric modules."""

from src.parsers.metric_utils import field_value


def test_returns_first_present_key():
    data = {"b": 2.0, "c": 3.0}
    assert field_value(data, ["a", "b", "c"]) == 2.0


def test_skips_none_and_coerces_numeric_strings():
    data = {"a": None, "b": "4"}
    assert field_value(data, ["a", "b"]) == 4.0


def test_missing_keys_return_none():
    assert field_value({"a": 1.0}, ["x", "y"]) is None


def test_non_numeric_value_is_skipped():
    assert field_value({"a": "not-a-number", "b": 5.0}, ["a", "b"]) == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_metric_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.parsers.metric_utils'`

- [ ] **Step 3: Create the module**

Create `src/parsers/metric_utils.py`:

```python
"""Shared helper for resolving a canonical financial field by candidate keys.

Lives in its own leaf module so both ``calculated_metrics`` (generic ratios) and
``sector_metrics`` (bank/insurer/REIT ratios) can resolve fields through one
implementation without importing each other.
"""

from typing import Any, Dict, List, Optional


def field_value(data: Dict[str, Any], keys: List[str]) -> Optional[float]:
    """Return the first present, numeric-coercible value among ``keys``, else None.

    Args:
        data: A flat dict of ``canonical_key -> value`` (one fiscal period).
        keys: Candidate keys in order of preference.
    """
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return float(data[key])
            except (ValueError, TypeError):
                continue
    return None
```

- [ ] **Step 4: Make `_get_value` delegate to the shared function**

In `src/parsers/calculated_metrics.py`, add the import near the top (after the existing `from typing ...` line):

```python
from .metric_utils import field_value
```

Replace the existing `_get_value` method body (the `for key in keys: ...` loop) with a one-line delegation:

```python
    def _get_value(
        self, data: Dict[str, Any], keys: List[str]
    ) -> Optional[float]:
        """Get value from data dictionary, trying multiple possible keys."""
        return field_value(data, keys)
```

- [ ] **Step 5: Add the module to mypy's checked files**

In `pyproject.toml`, inside `[tool.mypy] files = [ ... ]`, add this line (keep the list alphabetical-ish, near the other `src/parsers/` entries):

```toml
    "src/parsers/metric_utils.py",
```

- [ ] **Step 6: Run tests + linters to verify**

Run: `python -m pytest tests/test_metric_utils.py tests/test_calculated_metrics.py -v`
Expected: PASS (new resolver tests pass; the existing metrics tests still pass through the delegating method)

Run: `python -m ruff check src/parsers/metric_utils.py && python -m mypy`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/parsers/metric_utils.py src/parsers/calculated_metrics.py pyproject.toml tests/test_metric_utils.py
git commit -m "refactor: extract shared field_value resolver into metric_utils" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `sector_metrics.py` infrastructure + bank ratios

Create the sector module with the suppression map (all three sectors — it is pure data), the `apply_sector` orchestrator, and the first sector function (`bank_metrics`).

**Files:**
- Create: `src/parsers/sector_metrics.py`
- Modify: `pyproject.toml` (add `sector_metrics.py` to mypy `files`)
- Test: `tests/test_sector_metrics.py`

**Interfaces:**
- Consumes: `field_value` from Task 1.
- Produces:
  - `bank_metrics(financials: Dict[str, Any]) -> Dict[str, Optional[float]]` → keys `net_interest_margin`, `efficiency_ratio`, `loan_to_deposit`.
  - `SUPPRESSED_BY_SECTOR: Dict[str, frozenset]` — generic keys to null per sector.
  - `SECTOR_EXTRAS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Optional[float]]]]`.
  - `apply_sector(metrics: Dict[str, Any], financials: Dict[str, Any], sector: Optional[str]) -> Dict[str, Any]` — merges extras, nulls suppressed keys, records `metrics["_basis"]` notes for present proxies. No-op for `None`/general/utility/energy.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sector_metrics.py`:

```python
"""Sector-aware ratio functions and the suppression/orchestration layer."""

from src.parsers.sector_metrics import apply_sector, bank_metrics


def test_bank_metrics_compute_from_canonical_fields():
    f = {
        "net_interest_income": 50.0, "noninterest_income": 30.0,
        "noninterest_expense": 48.0, "total_assets": 1000.0,
        "total_loans": 600.0, "total_deposits": 900.0,
    }
    m = bank_metrics(f)
    assert m["efficiency_ratio"] == 48.0 / 80.0           # exact
    assert m["loan_to_deposit"] == 600.0 / 900.0          # exact
    assert m["net_interest_margin"] == 50.0 / 1000.0      # proxy


def test_bank_metrics_missing_inputs_are_none():
    assert bank_metrics({})["efficiency_ratio"] is None
    assert bank_metrics({})["loan_to_deposit"] is None


def test_apply_sector_bank_adds_and_suppresses():
    metrics = {
        "roic": 0.20, "inventory_turnover": 5.0, "ebitda": 100.0,
        "interest_coverage": 8.0, "net_debt": 10.0,
        "roe": 0.15, "net_margin": 0.10,
    }
    f = {
        "net_interest_income": 50.0, "noninterest_income": 30.0,
        "noninterest_expense": 48.0, "total_assets": 1000.0,
        "total_loans": 600.0, "total_deposits": 900.0,
    }
    apply_sector(metrics, f, "bank")
    # generic ratios that don't apply to a bank are nulled
    assert metrics["roic"] is None
    assert metrics["inventory_turnover"] is None
    assert metrics["ebitda"] is None
    assert metrics["interest_coverage"] is None
    assert metrics["net_debt"] is None
    # universally meaningful ones are kept
    assert metrics["roe"] == 0.15
    assert metrics["net_margin"] == 0.10
    # bank ratios are added
    assert metrics["efficiency_ratio"] == 48.0 / 80.0
    # proxy basis recorded
    assert "net_interest_margin" in metrics["_basis"]


def test_apply_sector_none_is_noop():
    metrics = {"roic": 0.2, "inventory_turnover": 5.0}
    apply_sector(metrics, {}, None)
    assert metrics == {"roic": 0.2, "inventory_turnover": 5.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sector_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.parsers.sector_metrics'`

- [ ] **Step 3: Create the module**

Create `src/parsers/sector_metrics.py`:

```python
"""Sector-aware financial ratios for banks, insurers, and REITs.

The generic ratio suite (``CalculatedMetrics``) assumes an operating company.
For banks/insurers/REITs many of those ratios are meaningless (a bank has no
inventory or invested capital; interest expense is a core cost, not a coverage
denominator). This module:

* computes the ratios that DO describe each sector, from canonical fields that
  already exist in the registry, and
* declares, per sector, which generic ratios to suppress (store ``None`` =
  "not applicable") so cross-sector screens don't compare on a broken metric.

Some ratios are documented proxies (see ``_BASIS``): the registry doesn't split
out real-estate-specific D&A, gains on property sales, or an earning-assets
line, so FFO/AFFO/combined-ratio/NIM use the closest available inputs.
"""

from typing import Any, Callable, Dict, Optional

from ..mappings.sectors import BANK, INSURANCE, REIT
from .metric_utils import field_value


def _f(financials: Dict[str, Any], key: str) -> Optional[float]:
    """Resolve a single canonical field (thin wrapper over field_value)."""
    return field_value(financials, [key])


def bank_metrics(financials: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Bank ratios: net interest margin (proxy), efficiency ratio, loan/deposit."""
    nii = _f(financials, "net_interest_income")
    noninterest_income = _f(financials, "noninterest_income")
    noninterest_expense = _f(financials, "noninterest_expense")
    total_assets = _f(financials, "total_assets")
    total_loans = _f(financials, "total_loans")
    total_deposits = _f(financials, "total_deposits")

    nim: Optional[float] = None
    if nii is not None and total_assets and total_assets > 0:
        nim = nii / total_assets

    revenue = (nii or 0.0) + (noninterest_income or 0.0)
    efficiency_ratio: Optional[float] = None
    if noninterest_expense is not None and revenue > 0:
        efficiency_ratio = noninterest_expense / revenue

    loan_to_deposit: Optional[float] = None
    if total_loans is not None and total_deposits and total_deposits > 0:
        loan_to_deposit = total_loans / total_deposits

    return {
        "net_interest_margin": nim,
        "efficiency_ratio": efficiency_ratio,
        "loan_to_deposit": loan_to_deposit,
    }


# Generic ratio keys to null per sector (must match keys emitted by
# CalculatedMetrics.calculate_all).
SUPPRESSED_BY_SECTOR: Dict[str, frozenset] = {
    BANK: frozenset({
        "ebitda", "ebit", "ebitda_margin", "debt_to_ebitda",
        "roic", "nopat", "invested_capital", "interest_coverage",
        "gross_margin", "operating_margin",
        "inventory_turnover", "days_inventory_outstanding",
        "receivables_turnover", "days_sales_outstanding",
        "asset_turnover", "working_capital",
        "net_debt", "total_debt",
        "free_cash_flow", "fcf_margin", "levered_fcf",
    }),
    INSURANCE: frozenset({
        "ebitda", "ebitda_margin", "debt_to_ebitda",
        "roic", "nopat", "invested_capital",
        "inventory_turnover", "days_inventory_outstanding",
        "gross_margin", "asset_turnover", "working_capital",
    }),
    REIT: frozenset({
        "roic", "nopat", "invested_capital",
        "inventory_turnover", "days_inventory_outstanding",
        "receivables_turnover", "days_sales_outstanding",
        "gross_margin", "asset_turnover",
        "free_cash_flow", "fcf_margin", "levered_fcf",
    }),
}

# Approximation provenance for proxy ratios; attached to metrics["_basis"] only
# when the corresponding metric was actually computed.
_BASIS: Dict[str, str] = {
    "net_interest_margin": "proxy: net_interest_income / total_assets (no earning-assets line)",
    "combined_ratio": "proxy: benefits_and_expenses / premiums_earned (no separate underwriting expense)",
    "ffo": "proxy: net_income + total D&A (not RE-specific; no gains-on-sale adjustment)",
    "affo": "proxy: ffo - total capex (not maintenance capex)",
}

# Registered in later tasks: INSURANCE -> insurer_metrics, REIT -> reit_metrics.
SECTOR_EXTRAS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Optional[float]]]] = {
    BANK: bank_metrics,
}


def apply_sector(
    metrics: Dict[str, Any], financials: Dict[str, Any], sector: Optional[str]
) -> Dict[str, Any]:
    """Merge sector ratios into ``metrics`` and null the suppressed generic ones.

    A ``None``/general/utility/energy ``sector`` is a no-op, so operating
    companies are unaffected. Mutates and returns ``metrics``.
    """
    extras_fn = SECTOR_EXTRAS.get(sector) if sector else None
    if extras_fn is None:
        return metrics

    metrics.update(extras_fn(financials))
    for key in SUPPRESSED_BY_SECTOR.get(sector, frozenset()):
        metrics[key] = None

    basis = metrics.setdefault("_basis", {})
    for key, note in _BASIS.items():
        if metrics.get(key) is not None:
            basis[key] = note
    return metrics
```

- [ ] **Step 4: Add the module to mypy's checked files**

In `pyproject.toml`, inside `[tool.mypy] files = [ ... ]`, add:

```toml
    "src/parsers/sector_metrics.py",
```

- [ ] **Step 5: Run tests + linters to verify**

Run: `python -m pytest tests/test_sector_metrics.py -v`
Expected: PASS (4 tests)

Run: `python -m ruff check src/parsers/sector_metrics.py && python -m mypy`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/parsers/sector_metrics.py pyproject.toml tests/test_sector_metrics.py
git commit -m "feat: sector_metrics module with bank ratios + suppression map" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Insurer ratios

Add `insurer_metrics` and register it.

**Files:**
- Modify: `src/parsers/sector_metrics.py`
- Test: `tests/test_sector_metrics.py`

**Interfaces:**
- Produces: `insurer_metrics(financials: Dict[str, Any]) -> Dict[str, Optional[float]]` → keys `loss_ratio`, `combined_ratio`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sector_metrics.py`:

```python
from src.parsers.sector_metrics import insurer_metrics


def test_insurer_metrics_compute():
    f = {"premiums_earned": 200.0, "claims_incurred": 150.0,
         "benefits_and_expenses": 190.0}
    m = insurer_metrics(f)
    assert m["loss_ratio"] == 150.0 / 200.0          # exact
    assert m["combined_ratio"] == 190.0 / 200.0      # proxy


def test_apply_sector_insurance_adds_and_suppresses():
    metrics = {"roic": 0.2, "inventory_turnover": 5.0, "ebitda": 100.0,
               "interest_coverage": 8.0, "roe": 0.12}
    f = {"premiums_earned": 200.0, "claims_incurred": 150.0,
         "benefits_and_expenses": 190.0}
    apply_sector(metrics, f, "insurance")
    assert metrics["roic"] is None
    assert metrics["inventory_turnover"] is None
    assert metrics["ebitda"] is None
    assert metrics["interest_coverage"] == 8.0   # kept for insurers
    assert metrics["loss_ratio"] == 150.0 / 200.0
    assert "combined_ratio" in metrics["_basis"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sector_metrics.py -k insurer -v`
Expected: FAIL with `ImportError: cannot import name 'insurer_metrics'`

- [ ] **Step 3: Add the function and register it**

In `src/parsers/sector_metrics.py`, add after `bank_metrics`:

```python
def insurer_metrics(financials: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Insurer ratios: loss ratio (exact) and combined ratio (proxy)."""
    premiums = _f(financials, "premiums_earned")
    claims = _f(financials, "claims_incurred")
    benefits_and_expenses = _f(financials, "benefits_and_expenses")

    loss_ratio: Optional[float] = None
    if claims is not None and premiums and premiums > 0:
        loss_ratio = claims / premiums

    combined_ratio: Optional[float] = None
    if benefits_and_expenses is not None and premiums and premiums > 0:
        combined_ratio = benefits_and_expenses / premiums

    return {"loss_ratio": loss_ratio, "combined_ratio": combined_ratio}
```

Update the `SECTOR_EXTRAS` dict to add the INSURANCE entry:

```python
SECTOR_EXTRAS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Optional[float]]]] = {
    BANK: bank_metrics,
    INSURANCE: insurer_metrics,
}
```

- [ ] **Step 4: Run tests + linters to verify**

Run: `python -m pytest tests/test_sector_metrics.py -v`
Expected: PASS (6 tests)

Run: `python -m ruff check src/parsers/sector_metrics.py && python -m mypy`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/parsers/sector_metrics.py tests/test_sector_metrics.py
git commit -m "feat: insurer loss/combined ratios" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: REIT ratios

Add `reit_metrics` (FFO/AFFO/per-share/payout) and register it.

**Files:**
- Modify: `src/parsers/sector_metrics.py`
- Test: `tests/test_sector_metrics.py`

**Interfaces:**
- Produces: `reit_metrics(financials: Dict[str, Any]) -> Dict[str, Optional[float]]` → keys `ffo`, `affo`, `ffo_per_share`, `ffo_payout`.

Note: `capex` and `dividends_paid` are stored as **positive magnitudes** (canonical `SIGN_ABS`), so `affo = ffo - capex` and `ffo_payout = dividends_paid / ffo` use them directly.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sector_metrics.py`:

```python
from src.parsers.sector_metrics import reit_metrics


def test_reit_ffo_and_derivatives():
    f = {"net_income": 100.0, "depreciation_amortization": 40.0,
         "capex": 10.0, "dividends_paid": 84.0,
         "weighted_avg_shares_diluted": 70.0}
    m = reit_metrics(f)
    assert m["ffo"] == 140.0                 # net_income + D&A
    assert m["affo"] == 130.0                # ffo - capex
    assert m["ffo_per_share"] == 2.0         # 140 / 70
    assert m["ffo_payout"] == 84.0 / 140.0   # dividends / ffo


def test_apply_sector_reit_adds_and_suppresses():
    metrics = {"roic": 0.2, "inventory_turnover": 5.0, "ebitda": 100.0,
               "interest_coverage": 8.0, "roe": 0.06}
    f = {"net_income": 100.0, "depreciation_amortization": 40.0,
         "capex": 10.0, "dividends_paid": 84.0,
         "weighted_avg_shares_diluted": 70.0}
    apply_sector(metrics, f, "reit")
    assert metrics["roic"] is None
    assert metrics["inventory_turnover"] is None
    assert metrics["ebitda"] == 100.0          # kept for REITs
    assert metrics["interest_coverage"] == 8.0  # kept for REITs
    assert metrics["ffo"] == 140.0
    assert "ffo" in metrics["_basis"]
    assert "affo" in metrics["_basis"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sector_metrics.py -k reit -v`
Expected: FAIL with `ImportError: cannot import name 'reit_metrics'`

- [ ] **Step 3: Add the function and register it**

In `src/parsers/sector_metrics.py`, add after `insurer_metrics`:

```python
def reit_metrics(financials: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """REIT ratios (proxies): FFO, AFFO, FFO/share, FFO payout.

    FFO ~= net income + total D&A (a pure REIT's D&A is almost all real estate);
    exact NAREIT FFO would also subtract gains on property sales, which the
    registry does not split out. AFFO ~= FFO - total capex.
    """
    net_income = _f(financials, "net_income")
    dna = _f(financials, "depreciation_amortization")
    capex = _f(financials, "capex")
    dividends_paid = _f(financials, "dividends_paid")
    shares = _f(financials, "weighted_avg_shares_diluted")

    ffo: Optional[float] = None
    if net_income is not None and dna is not None:
        ffo = net_income + dna

    affo: Optional[float] = None
    if ffo is not None and capex is not None:
        affo = ffo - capex

    ffo_per_share: Optional[float] = None
    if ffo is not None and shares and shares > 0:
        ffo_per_share = ffo / shares

    ffo_payout: Optional[float] = None
    if ffo is not None and ffo > 0 and dividends_paid is not None:
        ffo_payout = dividends_paid / ffo

    return {
        "ffo": ffo, "affo": affo,
        "ffo_per_share": ffo_per_share, "ffo_payout": ffo_payout,
    }
```

Update `SECTOR_EXTRAS` to add the REIT entry:

```python
SECTOR_EXTRAS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Optional[float]]]] = {
    BANK: bank_metrics,
    INSURANCE: insurer_metrics,
    REIT: reit_metrics,
}
```

- [ ] **Step 4: Run tests + linters to verify**

Run: `python -m pytest tests/test_sector_metrics.py -v`
Expected: PASS (8 tests)

Run: `python -m ruff check src/parsers/sector_metrics.py && python -m mypy`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/parsers/sector_metrics.py tests/test_sector_metrics.py
git commit -m "feat: REIT FFO/AFFO/payout ratios (labeled proxies)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Thread `sector` through `CalculatedMetrics`

Add the optional `sector` param to `calculate_all` and `calculate_historical`, and apply the sector layer. `sector=None` stays identical to today.

**Files:**
- Modify: `src/parsers/calculated_metrics.py`
- Test: `tests/test_calculated_metrics.py`

**Interfaces:**
- Consumes: `apply_sector` from Task 2.
- Produces (changed signatures):
  - `calculate_all(financials, market_data=None, valuation=None, sector=None) -> Dict[str, Any]`
  - `calculate_historical(annual_financials, sector=None) -> Dict[str, Dict[str, Any]]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calculated_metrics.py`:

```python
def test_calculate_all_general_sector_is_unchanged():
    calc = CalculatedMetrics()
    financials = {"revenue": 1000.0, "net_income": 100.0, "total_assets": 2000.0,
                  "total_equity": 800.0, "operating_income": 150.0,
                  "inventory": 50.0, "cost_of_revenue": 600.0}
    base = calc.calculate_all(financials)            # sector=None
    same = calc.calculate_all(financials, sector="general")
    # No sector keys leak in, nothing is suppressed.
    assert "efficiency_ratio" not in base
    assert "ffo" not in same
    assert base["roic"] == same["roic"]
    assert same["inventory_turnover"] is not None


def test_calculate_all_bank_sector_suppresses_and_adds():
    calc = CalculatedMetrics()
    financials = {"net_interest_income": 50.0, "noninterest_income": 30.0,
                  "noninterest_expense": 48.0, "total_assets": 1000.0,
                  "total_loans": 600.0, "total_deposits": 900.0,
                  "inventory": 5.0, "cost_of_revenue": 10.0,
                  "net_income": 20.0, "total_equity": 120.0}
    m = calc.calculate_all(financials, sector="bank")
    assert m["efficiency_ratio"] == 48.0 / 80.0
    assert m["roic"] is None
    assert m["inventory_turnover"] is None
    assert m["roe"] == 20.0 / 120.0      # kept


def test_calculate_historical_threads_sector():
    calc = CalculatedMetrics()
    annual = {"2024": {"net_income": 100.0, "depreciation_amortization": 40.0,
                       "capex": 10.0, "revenue": 500.0, "total_assets": 2000.0,
                       "total_equity": 800.0}}
    hist = calc.calculate_historical(annual, sector="reit")
    assert hist["2024"]["ffo"] == 140.0
    assert hist["2024"]["roic"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calculated_metrics.py -k "sector" -v`
Expected: FAIL — `calculate_all() got an unexpected keyword argument 'sector'`

- [ ] **Step 3: Add the import**

In `src/parsers/calculated_metrics.py`, add near the other relative imports (top of file, after `from .metric_utils import field_value`):

```python
from .sector_metrics import apply_sector
```

- [ ] **Step 4: Add `sector` to `calculate_all`**

Change the `calculate_all` signature to add the parameter:

```python
    def calculate_all(
        self,
        financials: Dict[str, Any],
        market_data: Optional[Dict[str, Any]] = None,
        valuation: Optional[Dict[str, Any]] = None,
        sector: Optional[str] = None,
    ) -> Dict[str, Any]:
```

At the **end** of `calculate_all`, immediately before `return metrics`, insert:

```python
        # Sector overlay: merge bank/insurer/REIT ratios and null the generic
        # ratios that don't apply. A None/general sector is a no-op.
        apply_sector(metrics, financials, sector)

        return metrics
```

- [ ] **Step 5: Add `sector` to `calculate_historical`**

Change `calculate_historical` to accept and thread `sector`:

```python
    def calculate_historical(
        self,
        annual_financials: Dict[str, Dict[str, Any]],
        sector: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
```

Inside its loop, change the `calculate_all` call to pass the sector:

```python
                metrics = self.calculate_all(financials, sector=sector)
```

- [ ] **Step 6: Run tests + linters to verify**

Run: `python -m pytest tests/test_calculated_metrics.py -v`
Expected: PASS (existing tests + 3 new)

Run: `python -m mypy && python -m ruff check src/parsers/calculated_metrics.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/parsers/calculated_metrics.py tests/test_calculated_metrics.py
git commit -m "feat: sector param on calculate_all/calculate_historical" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Wire the fetcher to pass `sector`

Extract the inline metric-calculation block in `fetch_ticker` into a testable `_compute_metrics` helper that passes `stock.sector_class`.

**Files:**
- Modify: `src/fetchers/stock_data_fetcher.py` (replace inline block at lines ~187-211; add `_compute_metrics` method)
- Test: `tests/test_fetcher_concurrency.py`

**Interfaces:**
- Consumes: `CalculatedMetrics.calculate_all/calculate_historical` with `sector` (Task 5); `stock.sector_class` (already set during SEC fetch).
- Produces: `StockDataFetcher._compute_metrics(self, stock: StockData) -> None` — populates `stock.calculated_metrics` (incl. `historical`) using the stock's sector.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetcher_concurrency.py`:

```python
def test_compute_metrics_is_sector_aware(tmp_path):
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="RIT", cik="000", company_name="R Inc.")
    stock.sector_class = "reit"
    stock.financials_annual = {
        "2024": {"net_income": 100.0, "depreciation_amortization": 40.0,
                 "capex": 10.0, "revenue": 500.0, "total_assets": 2000.0,
                 "total_equity": 800.0},
    }
    fetcher._compute_metrics(stock)
    cm = stock.calculated_metrics
    assert cm["ffo"] == 140.0                 # REIT ratio present
    assert cm["roic"] is None                 # suppressed for REITs
    assert cm["historical"]["2024"]["ffo"] == 140.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetcher_concurrency.py::test_compute_metrics_is_sector_aware -v`
Expected: FAIL — `AttributeError: 'StockDataFetcher' object has no attribute '_compute_metrics'`

- [ ] **Step 3: Replace the inline block with a helper call**

In `src/fetchers/stock_data_fetcher.py`, replace the entire block (currently lines ~187-211):

```python
        # Calculate derived metrics (FCF, EBITDA, ROIC, etc.)
        if stock.financials_annual:
            try:
                # Get the most recent year's financials
                years = sorted(stock.financials_annual.keys(), reverse=True)
                if years:
                    latest_financials = stock.financials_annual[years[0]]

                    # Calculate metrics
                    metrics = self.metrics_calculator.calculate_all(
                        financials=latest_financials,
                        market_data=stock.market_data,
                        valuation=stock.valuation
                    )

                    # Add historical metrics for all years
                    metrics["historical"] = self.metrics_calculator.calculate_historical(
                        stock.financials_annual
                    )

                    stock.merge_calculated_metrics(metrics)

            except Exception as e:
                self.logger.warning(f"Metrics calculation error for {ticker}: {e}")
                stock.add_warning(f"Calculated metrics: {str(e)}")
```

with:

```python
        # Calculate derived metrics (generic + sector-aware).
        if stock.financials_annual:
            try:
                self._compute_metrics(stock)
            except Exception as e:
                self.logger.warning(f"Metrics calculation error for {ticker}: {e}")
                stock.add_warning(f"Calculated metrics: {str(e)}")
```

- [ ] **Step 4: Add the `_compute_metrics` method**

Add this method to the `StockDataFetcher` class (e.g. immediately after `fetch_ticker`):

```python
    def _compute_metrics(self, stock: StockData) -> None:
        """Populate generic + sector-aware derived metrics on the stock.

        Uses the company's ``sector_class`` so banks/insurers/REITs get their
        own ratios and the generic ratios that don't apply are stored as None.
        """
        years = sorted((stock.financials_annual or {}).keys(), reverse=True)
        if not years:
            return
        latest_financials = stock.financials_annual[years[0]]
        metrics = self.metrics_calculator.calculate_all(
            financials=latest_financials,
            market_data=stock.market_data,
            valuation=stock.valuation,
            sector=stock.sector_class,
        )
        metrics["historical"] = self.metrics_calculator.calculate_historical(
            stock.financials_annual,
            sector=stock.sector_class,
        )
        stock.merge_calculated_metrics(metrics)
```

- [ ] **Step 5: Run tests to verify**

Run: `python -m pytest tests/test_fetcher_concurrency.py -v`
Expected: PASS (existing concurrency tests + the new sector-aware test)

Run: `python -m ruff check src/fetchers/stock_data_fetcher.py`
Expected: no errors. (Note: `stock_data_fetcher.py` is not in the mypy `files` list — no mypy change needed here.)

- [ ] **Step 6: Commit**

```bash
git add src/fetchers/stock_data_fetcher.py tests/test_fetcher_concurrency.py
git commit -m "feat: fetcher computes sector-aware metrics via _compute_metrics" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Persist the new ratio columns

Add the nine sector ratio columns to `_METRIC_COLUMNS`. The write loop and `_migrate` already iterate `_METRIC_COLUMNS`, so persistence and migration are automatic — only the list changes.

**Files:**
- Modify: `src/exporters/sqlite_store.py` (extend `_METRIC_COLUMNS`)
- Test: `tests/test_sqlite_store.py`

**Interfaces:**
- Consumes: per-year metrics dicts in `stock.calculated_metrics["historical"]` now contain the sector keys + suppressed `None`s (Tasks 5-6).
- Produces: `metrics_annual` columns `net_interest_margin, efficiency_ratio, loan_to_deposit, loss_ratio, combined_ratio, ffo, affo, ffo_per_share, ffo_payout` (all `REAL`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sqlite_store.py`:

```python
def test_sector_metrics_persist_with_suppressed_nulls(tmp_path):
    s = StockData(ticker="BNK", cik="000", company_name="Bank Inc.")
    s.sector_class = "bank"
    s.financials_annual = {"2024": {"fiscal_year": 2024, "net_income": 20.0,
                                    "total_assets": 1000.0, "total_equity": 120.0}}
    # Simulate what calculate_historical(sector="bank") produces: a bank ratio set
    # plus the generic ratios it suppresses (stored as None).
    s.calculated_metrics = {"historical": {"2024": {
        "efficiency_ratio": 0.6, "loan_to_deposit": 0.67,
        "net_interest_margin": 0.05, "roe": 0.16,
        "roic": None, "inventory_turnover": None,
    }}}
    s.add_source("sec_edgar")
    SQLiteStore(tmp_path / "stock.db").export([s])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        eff, roic = conn.execute(
            "SELECT efficiency_ratio, roic FROM metrics_annual "
            "WHERE ticker='BNK' AND fiscal_year=2024"
        ).fetchone()
        assert eff == 0.6
        assert roic is None          # suppressed -> NULL
    finally:
        conn.close()
```

Also extend the existing `test_migrate_adds_missing_columns` — after the existing assertions on `metrics`/financials columns, add an assertion that the new metric columns are migrated in. Add these lines inside that test's `try` block:

```python
        mcols = {row[1] for row in conn.execute("PRAGMA table_info(metrics_annual)")}
        assert "efficiency_ratio" in mcols   # sector ratio column migrated in
        assert "ffo" in mcols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sqlite_store.py -k "sector_metrics_persist or migrate_adds" -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: efficiency_ratio`

- [ ] **Step 3: Extend `_METRIC_COLUMNS`**

In `src/exporters/sqlite_store.py`, extend the `_METRIC_COLUMNS` list by appending the sector ratios:

```python
_METRIC_COLUMNS = [
    "ebitda", "ebit", "nopat", "free_cash_flow", "fcf_margin", "levered_fcf",
    "net_debt", "total_debt", "working_capital", "invested_capital",
    "roic", "roa", "roe", "interest_coverage", "debt_to_ebitda",
    "asset_turnover", "inventory_turnover", "receivables_turnover",
    "gross_margin", "operating_margin", "net_margin", "ebitda_margin",
    # Sector-aware ratios (NULL where not applicable to the company's sector).
    "net_interest_margin", "efficiency_ratio", "loan_to_deposit",
    "loss_ratio", "combined_ratio",
    "ffo", "affo", "ffo_per_share", "ffo_payout",
]
```

- [ ] **Step 4: Run tests + linters to verify**

Run: `python -m pytest tests/test_sqlite_store.py -v`
Expected: PASS (all store tests, incl. new persistence + extended migration)

Run: `python -m mypy && python -m ruff check src/exporters/sqlite_store.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/exporters/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat: persist sector-aware ratio columns in metrics_annual" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Documentation

Document the sector ratios, the suppression policy, and the proxy bases in the README.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a README section**

Find the existing standardization/sector section in `README.md` (search for the sector-coverage heading) and add a new subsection after it. Use this exact content:

```markdown
### Sector-aware metrics

Ratios are computed per the company's sector (classified from its SIC code), so
cross-company screening compares like with like:

| Sector | Added ratios | Suppressed generic ratios (stored `NULL` = not applicable) |
|---|---|---|
| **Bank** | net interest margin\*, efficiency ratio, loan-to-deposit | EBITDA family, ROIC/NOPAT/invested capital, interest coverage, gross/operating margin, inventory & receivables turnover, asset turnover, working capital, net/total debt, FCF family |
| **Insurer** | loss ratio, combined ratio\* | EBITDA family, ROIC/NOPAT/invested capital, inventory turnover, gross margin, asset turnover, working capital |
| **REIT** | FFO\*, AFFO\*, FFO/share, FFO payout | ROIC/NOPAT/invested capital, inventory & receivables turnover, gross margin, asset turnover, FCF family |

General operating companies (and utilities/energy) get the full generic ratio
suite unchanged. A suppressed ratio is stored as `NULL`, so a screen such as
`WHERE roic > 0.15` automatically excludes sectors where ROIC is undefined
instead of returning a misleading value.

\* **Proxy** (the registry doesn't split out the exact inputs):
net interest margin = `net_interest_income / total_assets`;
combined ratio = `benefits_and_expenses / premiums_earned`;
FFO = `net_income + total D&A` (no real-estate-specific D&A or gains-on-sale
adjustment); AFFO = `FFO − total capex`. Each proxy is flagged in the metrics
JSON under `_basis`.
```

- [ ] **Step 2: Verify the docs render and nothing else broke**

Run: `python -m pytest -q`
Expected: PASS — full suite (was 107; now 107 + the new sector/metric/store/util tests).

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document sector-aware metrics and proxy bases" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] Live smoke (bank): `python -m src.main JPM --no-yahoo` then
  `sqlite3` → `SELECT efficiency_ratio, loan_to_deposit, roic FROM metrics_annual WHERE ticker='JPM' ORDER BY fiscal_year DESC LIMIT 1;`
  Expected: `efficiency_ratio`/`loan_to_deposit` populated, `roic` NULL.
- [ ] Live smoke (REIT): `python -m src.main PLD --no-yahoo` then
  `SELECT ffo, ffo_per_share, inventory_turnover FROM metrics_annual WHERE ticker='PLD' ORDER BY fiscal_year DESC LIMIT 1;`
  Expected: `ffo`/`ffo_per_share` populated, `inventory_turnover` NULL.
- [ ] Live smoke (general regression): `python -m src.main AAPL --no-yahoo` then
  `SELECT roic, inventory_turnover, ffo FROM metrics_annual WHERE ticker='AAPL' ORDER BY fiscal_year DESC LIMIT 1;`
  Expected: `roic`/`inventory_turnover` populated, `ffo` NULL.
