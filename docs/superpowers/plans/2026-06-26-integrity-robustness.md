# Integrity-Check Robustness (B + C + D) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three independent robustness fixes from the 10-stock test — make the cash check tolerant of discontinued ops (B), stop requiring `operating_income` (C), and collapse quarterly-sum cascades to one finding per year (D).

**Architecture:** B touches `canonical.py` (FX tag) + `integrity.py` (`check_cashflow_reconciliation`); C touches `quality.py` (required-field set); D touches `integrity.py` (`check_quarterly_sums`). Each is a separate task. No metrics/parser/schema change.

**Tech Stack:** Python 3.9+, pytest, ruff, mypy.

## Global Constraints

- Python floor **3.9** — no `X | Y` unions, no `match`.
- ruff line-length **120**, select `E, F, W, I`, imports at top. Long XBRL tags use implicit string concatenation (adjacent `"..."` `"..."`), no `# noqa`.
- mypy clean; `quality.py`/`integrity.py` (via the `src/validation` directory entry) and `canonical.py` (explicit) are in the mypy `files` list — do not touch pyproject.
- Flag-only; checks never mutate data. Materiality floor `$1,000,000`; scored window recent 5 years.
- New constant `_CASH_GROSS_TOL = 0.05`; `_CASH_TOL` stays `0.01`; `_QUARTERLY_TOL` stays `0.01`.
- All existing tests stay green (some are updated where behavior legitimately changes — noted per task).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `feat/integrity-robustness` is already checked out.

---

### Task 1: B — cash-flow check tolerant of discontinued ops

**Files:**
- Modify: `src/mappings/canonical.py` (`fx_effect_on_cash` tags)
- Modify: `src/validation/integrity.py` (`check_cashflow_reconciliation` + `_CASH_GROSS_TOL`)
- Test: `tests/test_integrity.py`, `tests/test_canonical_sectors.py`

**Interfaces:**
- Produces: `check_cashflow_reconciliation` now flags only when the residual is material vs both the net-change scale AND gross cash activity; `fx_effect_on_cash` resolves the `…DisposalGroupIncludingDiscontinuedOperations` variant.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integrity.py`:

```python
def test_cashflow_imbalance_silent_when_residual_immaterial_to_gross():
    # Insurer/discontinued-ops shape: net change != OCF+ICF+FCF, but the residual is
    # tiny vs the GROSS cash activity (huge OCF/ICF that nearly cancel) -> not flagged.
    annual = {"2024": {"net_change_in_cash": -571.0e6, "operating_cash_flow": 17000.0e6,
                       "investing_cash_flow": -17000.0e6, "financing_cash_flow": 26.0e6}}
    # sections sum to 26e6; residual = -597e6; gross = 34026e6; |residual|/gross = 1.8% < 5%
    assert check_cashflow_reconciliation(annual, {"2024"}) == []
```

Append to `tests/test_canonical_sectors.py`:

```python
def test_disposal_group_fx_variant_resolves():
    fx_tag = ("EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
              "DisposalGroupIncludingDiscontinuedOperations")
    facts = _facts(**{fx_tag: _annual_usd(-136)})
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["fx_effect_on_cash"] == -136
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_integrity.py::test_cashflow_imbalance_silent_when_residual_immaterial_to_gross tests/test_canonical_sectors.py::test_disposal_group_fx_variant_resolves -v`
Expected: FAIL — the cash test currently flags (single condition: |residual|/denom = 105% > 1%), and the FX variant doesn't resolve (`KeyError: 'fx_effect_on_cash'`).

- [ ] **Step 3: Add the FX variant tag in canonical.py**

In `src/mappings/canonical.py`, the `fx_effect_on_cash` field's tag tuple currently is:

```python
        (
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
            "IncludingDisposalGroupAndDiscontinuedOperations",
            "EffectOfExchangeRateOnCashAndCashEquivalents",
            "EffectOfExchangeRateOnCashAndCashEquivalentsContinuingOperations",
        ),
```

Add the `DisposalGroupIncludingDiscontinuedOperations` variant (different word order) as the third entry:

```python
        (
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
            "IncludingDisposalGroupAndDiscontinuedOperations",
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
            "DisposalGroupIncludingDiscontinuedOperations",
            "EffectOfExchangeRateOnCashAndCashEquivalents",
            "EffectOfExchangeRateOnCashAndCashEquivalentsContinuingOperations",
        ),
```

- [ ] **Step 4: Add the gross-scale condition in integrity.py**

In `src/validation/integrity.py`, add the new constant next to `_CASH_TOL`:

```python
_CASH_GROSS_TOL = 0.05
```

In `check_cashflow_reconciliation`, the flag block currently is:

```python
        fx = _num(period, "fx_effect_on_cash") or 0.0
        expected = ocf + icf + fcf + fx
        denom = max(abs(net_change), abs(expected))
        if denom < _MATERIALITY:
            continue
        residual = net_change - expected
        if abs(residual) / denom > _CASH_TOL:
            findings.append(Finding(
                MEDIUM, "cashflow_imbalance",
                f"reported net change in cash ({net_change:,.0f}) != operating+"
                f"investing+financing+FX ({expected:,.0f}); residual {residual:,.0f}.",
                year,
            ))
```

Replace it with (add `gross` and the second condition; both use multiplication, no division):

```python
        fx = _num(period, "fx_effect_on_cash") or 0.0
        expected = ocf + icf + fcf + fx
        denom = max(abs(net_change), abs(expected))
        if denom < _MATERIALITY:
            continue
        residual = net_change - expected
        gross = abs(ocf) + abs(icf) + abs(fcf)
        # Flag only when the residual is material BOTH vs the net change and vs gross
        # cash activity -- so a small discontinued-ops/FX leftover on a company with
        # huge gross flows (e.g. an insurer) is not a false positive, while a
        # mis-resolved section (large vs gross) still is.
        if abs(residual) > _CASH_TOL * denom and abs(residual) > _CASH_GROSS_TOL * gross:
            findings.append(Finding(
                MEDIUM, "cashflow_imbalance",
                f"reported net change in cash ({net_change:,.0f}) != operating+"
                f"investing+financing+FX ({expected:,.0f}); residual {residual:,.0f}.",
                year,
            ))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_integrity.py -k cashflow tests/test_canonical_sectors.py::test_disposal_group_fx_variant_resolves -v`
Expected: PASS — the new immaterial-to-gross test is silent; the FX variant resolves; the existing `test_cashflow_imbalance_fires_when_sections_dont_match_net_change` still fires (its residual 0.5e9 is 71% of gross 0.7e9 > 5%); the silent/skip tests still pass.

- [ ] **Step 6: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite; +2 tests).

Run: `python -m ruff check src/mappings/canonical.py src/validation/integrity.py tests/test_integrity.py tests/test_canonical_sectors.py && python -m mypy`
Expected: no errors (every tag line ≤120 via implicit concatenation).

- [ ] **Step 7: Commit**

```bash
git add src/mappings/canonical.py src/validation/integrity.py tests/test_integrity.py tests/test_canonical_sectors.py
git commit -m "fix: cash check tolerant of discontinued-ops (residual vs gross flows) + FX variant" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: C — `operating_income` no longer required

**Files:**
- Modify: `src/validation/quality.py` (required set; remove energy special-case + import)
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces: `_GENERAL_REQUIRED` no longer contains `operating_income`; `REQUIRED_BY_SECTOR` has no `ENERGY` key (energy falls through to the relaxed GENERAL set).

- [ ] **Step 1: Replace the energy test with a sector-agnostic one**

In `tests/test_quality.py`, REPLACE the existing `test_energy_required_set_drops_operating_income` (its assertion that the *general* sector still flags a missing `operating_income` is no longer true) with:

```python
def test_operating_income_not_required():
    # No sector requires operating_income: diversified multinationals (JNJ, XOM)
    # report pretax income by geography rather than a tagged operating-income line.
    period = {"revenue": 100.0, "net_income": 10.0, "total_assets": 200.0,
              "total_liabilities": 120.0, "total_equity": 80.0, "operating_cash_flow": 20.0}
    annual = {"2024": period}
    for sector in ("general", "energy"):
        report = assess_annual(annual, sector=sector)
        assert not any(f.code == "missing_field" for f in report.findings)
        assert report.score == 100
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_quality.py::test_operating_income_not_required -v`
Expected: FAIL — under the current `_GENERAL_REQUIRED`, the `general` sector requires `operating_income`, so the period (which lacks it) produces a `missing_field` finding and `score` 90, not 100.

- [ ] **Step 3: Drop operating_income from the required set and remove the energy special-case**

In `src/validation/quality.py`:

Change the sectors import from:

```python
from ..mappings.sectors import BANK, ENERGY, GENERAL, INSURANCE, REIT
```

to:

```python
from ..mappings.sectors import BANK, GENERAL, INSURANCE, REIT
```

Change `_GENERAL_REQUIRED` from:

```python
_GENERAL_REQUIRED = (
    "revenue", "net_income", "operating_income",
    "total_assets", "total_liabilities", "total_equity", "operating_cash_flow",
)
```

to (drop `operating_income`):

```python
_GENERAL_REQUIRED = (
    "revenue", "net_income",
    "total_assets", "total_liabilities", "total_equity", "operating_cash_flow",
)
```

Delete the now-redundant `_ENERGY_REQUIRED` constant block (its comment and the tuple) entirely — it is identical to the new `_GENERAL_REQUIRED`.

In `REQUIRED_BY_SECTOR`, delete the `ENERGY: _ENERGY_REQUIRED,` line (energy now falls through to `_GENERAL_REQUIRED` via the `.get(sector or GENERAL, _GENERAL_REQUIRED)` default).

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_quality.py -v`
Expected: PASS — `test_operating_income_not_required` passes for both sectors; all other quality tests pass. (If any other existing test asserts a general company is flagged for missing `operating_income`, update it: that is a legitimate behavior change — operating_income is no longer required.)

- [ ] **Step 5: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check src/validation/quality.py tests/test_quality.py && python -m mypy`
Expected: no errors (in particular, no F401 for the removed `ENERGY` import).

- [ ] **Step 6: Commit**

```bash
git add src/validation/quality.py tests/test_quality.py
git commit -m "fix: operating_income no longer required (supersedes energy special-case)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: D — quarterly-sum: one finding per fiscal year

**Files:**
- Modify: `src/validation/integrity.py` (`check_quarterly_sums`)
- Test: `tests/test_integrity.py`

**Interfaces:**
- Produces: `check_quarterly_sums` emits one `quarterly_sum_mismatch` `Finding` per fiscal year (not per field).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integrity.py`:

```python
def test_quarterly_sum_aggregates_to_one_finding_per_year():
    # Two flow fields both mismatch in 2024 -> exactly ONE finding for the year.
    annual = {"2024": {"revenue": 1.0e9, "net_income": 1.0e9}}
    q = lambda fq, rev, ni: {"fiscal_year": 2024, "fiscal_quarter": fq,
                             "revenue": rev, "net_income": ni}
    quarterly = {  # each field's quarters sum to 0.8e9 vs annual 1.0e9 (20% off)
        "2024-03-31": q(1, 0.20e9, 0.20e9), "2024-06-30": q(2, 0.20e9, 0.20e9),
        "2024-09-30": q(3, 0.20e9, 0.20e9), "2024-12-31": q(4, 0.20e9, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert len(findings) == 1
    assert findings[0].code == "quarterly_sum_mismatch"
    assert findings[0].period == "2024"
    assert "revenue" in findings[0].message and "net_income" in findings[0].message
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_integrity.py::test_quarterly_sum_aggregates_to_one_finding_per_year -v`
Expected: FAIL — the current check emits one finding per field, so `len(findings) == 2`, not 1.

- [ ] **Step 3: Aggregate emission to one finding per year**

In `src/validation/integrity.py`, in `check_quarterly_sums`, the per-year inner loop currently appends a `Finding` inside the `for key in _FLOW_FIELDS` loop. Replace the inner loop + append with this (collect mismatched fields, then emit once):

```python
        mismatched: List[str] = []
        for key in _FLOW_FIELDS:
            ann_val = _num(ann, key)
            if ann_val is None or abs(ann_val) < _MATERIALITY:
                continue
            q_vals = [_num(quarters[q], key) for q in (1, 2, 3, 4)]
            if any(v is None for v in q_vals):
                continue
            sum_q = sum(v for v in q_vals if v is not None)
            if abs(sum_q - ann_val) / abs(ann_val) > _QUARTERLY_TOL:
                mismatched.append(key)
        if mismatched:
            preview = ", ".join(mismatched[:5])
            if len(mismatched) > 5:
                preview += ", ..."
            findings.append(Finding(
                MEDIUM, "quarterly_sum_mismatch",
                f"FY{year}: {len(mismatched)} flow field(s) whose four quarters don't "
                f"sum to the annual figure ({preview}).",
                year,
            ))
```

(The surrounding grouping, the `set(quarters) != {1, 2, 3, 4}` guard, the `$1M` floor, and `_QUARTERLY_TOL` are unchanged — only the emission is aggregated.)

- [ ] **Step 4: Run the quarterly tests to verify they pass**

Run: `python -m pytest tests/test_integrity.py -k quarterly -v`
Expected: PASS — the new aggregation test (one finding), plus the existing single-field fires test (one field → one finding, message now `FY...` style) and the exact-ladder/three-quarters silent tests.

Note: the existing `test_quarterly_sum_mismatch_fires` asserts the finding tuple `(code, period, severity)` — that still holds (one mismatched field → one finding). It does NOT assert the message text, so the message-format change is safe. If that test asserts message text, update its expected message to the new `FY{year}: 1 flow field(s) ... (revenue).` form.

- [ ] **Step 5: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite; +1 test).

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "fix: quarterly_sum_mismatch emits one finding per fiscal year" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the energy note**

In `README.md`, find the "Energy & utilities" note added by the prior energy PR (it currently says the energy required-field set drops `operating_income`). Replace that note with one that reflects the generalized behavior:

```markdown
**Energy & utilities.** Both use the general operating-company schema. `operating_income` is
**not a required field** for any sector — integrated oil & gas majors and other diversified
multinationals (e.g. JNJ) report pretax income by geography rather than a tagged
`OperatingIncomeLoss` line, so its absence is not a data-quality penalty (it is still captured
when reported). Oil-major `roic`/`inventory_turnover` are left empty rather than approximated;
the `inventory` line resolves from energy-specific tags
(`EnergyRelatedInventory`, `InventoryCrudeOilProductsAndMerchandise`).
```

- [ ] **Step 2: Verify the full suite and linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: operating_income not required; cash check uses gross-flow scale" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks) — the merge gate

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] **Live smoke (the validation):** run
  `python -m src.main GOOGL NVDA COST JNJ GS MET AMT CVX V CAT --no-yahoo --formats json sqlite --db <scratch>/r.db --output-dir <scratch>/r --workers 10`,
  then inspect each ticker's `data_quality.findings` and `collection_runs.quality_score`.
  Expected:
  - **MET no longer has any `cashflow_imbalance`** (recovers toward 100);
  - **JNJ has no `missing_field(operating_income)`**, and its FY2023 quarterly mismatch is **one**
    `quarterly_sum_mismatch` finding, not 19 (score improves substantially);
  - GOOGL/NVDA/COST/AMT/CVX/CAT still score 100;
  - **the broader clean set still passes:** `python -m src.main AAPL MSFT JPM PLD --no-yahoo` shows
    no `cashflow_imbalance` on any of them.
- [ ] If MET still shows a `cashflow_imbalance` (or a clean company newly does), adjust
  `_CASH_GROSS_TOL` and re-run — the smoke is the decisive validation for that constant.
- [ ] Only after the smoke is clean: merge to `main` + clean up the branch.
