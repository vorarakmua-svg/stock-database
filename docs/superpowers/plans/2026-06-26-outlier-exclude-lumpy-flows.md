# Magnitude-Outlier: Exclude Event-Driven Flows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `magnitude_outlier` from false-flagging legitimately-lumpy event-driven flows (debt issuance, buybacks, M&A, one-time charges), while still catching one-off anomalies in recurring fields.

**Architecture:** Add six event-driven flow keys to the existing `_OUTLIER_EXCLUDE` frozenset in `integrity.py` (already filtered out of `_USD_FIELDS`, the magnitude-outlier candidate set). One test + one README row. No other code changes.

**Tech Stack:** Python 3.9+, pytest, ruff, mypy.

## Global Constraints

- Python floor **3.9** — no `X | Y` unions, no `match`.
- ruff line-length **120**, select `E, F, W, I`, imports at top.
- mypy clean; `integrity.py` is covered by the `src/validation` mypy directory entry — do not touch pyproject.
- Flag-only; the excluded fields stay in `_FLOW_FIELDS` (quarterly-sum check) and stay fully captured — only the magnitude-outlier check skips them.
- All existing tests stay green; the six previously-clean smoke companies keep score 100.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `fix/outlier-exclude-lumpy-flows` is already checked out.

---

### Task 1: Extend `_OUTLIER_EXCLUDE` with event-driven flows

**Files:**
- Modify: `src/validation/integrity.py` (`_OUTLIER_EXCLUDE`)
- Test: `tests/test_integrity.py`

**Interfaces:**
- Produces: `_OUTLIER_EXCLUDE` now also contains `debt_issued`, `debt_repaid`, `share_repurchases`, `acquisitions`, `restructuring`, `impairment`. `check_field_outliers` signature/behavior unchanged except that these fields are no longer candidates.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integrity.py`:

```python
def test_outlier_excludes_event_driven_lumpy_flows():
    # debt_issued spikes 1000x in an interior year and reverts (a real one-off bond
    # issuance) -> excluded, not flagged. revenue spikes the same way -> still flagged,
    # proving the exclusion is field-scoped, not global.
    annual = {
        "2021": {"debt_issued": 1.0e7, "revenue": 1.0e9},
        "2022": {"debt_issued": 1.0e10, "revenue": 1.2e12},  # both spike here
        "2023": {"debt_issued": 1.0e7, "revenue": 1.1e9},    # both revert
        "2024": {"debt_issued": 1.2e7, "revenue": 1.2e9},
    }
    findings = check_field_outliers(annual, {"2021", "2022", "2023", "2024"})
    fields = [f.message.split("'")[1] for f in findings]
    assert "revenue" in fields              # recurring field still flagged
    assert "debt_issued" not in fields      # event-driven flow excluded
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_integrity.py::test_outlier_excludes_event_driven_lumpy_flows -v`
Expected: FAIL — under the current `_OUTLIER_EXCLUDE` (only `net_change_in_cash`/`fx_effect_on_cash`), `debt_issued` is still a candidate and its 1000×-and-revert spike at 2022 IS flagged, so `"debt_issued" not in fields` fails.

- [ ] **Step 3: Extend `_OUTLIER_EXCLUDE`**

In `src/validation/integrity.py`, replace the existing block:

```python
# Volatile net-residual flows that legitimately swing far more than the outlier
# factor year-to-year (a near-zero year beside a multi-billion one); excluded from
# the magnitude-outlier candidate set, but still checked by the quarterly-sum check.
_OUTLIER_EXCLUDE = frozenset({"net_change_in_cash", "fx_effect_on_cash"})
```

with:

```python
# Fields excluded from the magnitude-outlier check because they legitimately spike
# far more than the outlier factor in a single year. The check only makes sense for
# recurring, relatively stable fields (revenue, income, assets, equity, capex,
# dividends, D&A). These stay in the quarterly-sum check and stay fully captured.
_OUTLIER_EXCLUDE = frozenset({
    # Volatile net residuals (cash-flow reconciliation inputs).
    "net_change_in_cash", "fx_effect_on_cash",
    # Event-driven / lumpy flows: financing transactions, buybacks, M&A, one-time charges.
    "debt_issued", "debt_repaid", "share_repurchases", "acquisitions",
    "restructuring", "impairment",
})
```

(`_USD_FIELDS` already filters `f.key not in _OUTLIER_EXCLUDE`, so no other change is needed.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_integrity.py::test_outlier_excludes_event_driven_lumpy_flows -v`
Expected: PASS

- [ ] **Step 5: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite; +1 test).

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "fix: exclude event-driven flows from magnitude_outlier" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the magnitude-outlier row**

In `README.md`, in the "### Integrity checks (data-quality score)" table, replace the magnitude-outlier row:

```markdown
| Magnitude outlier | a USD field that spikes then reverts to its prior level — a one-off filing/tag error (persistent step-changes like M&A goodwill are not flagged) | spike ≥ 100× both adjacent years | −25 |
```

with:

```markdown
| Magnitude outlier | a recurring USD field that spikes then reverts — a one-off filing/tag error (persistent M&A step-changes, and event-driven flows like debt issuance, buybacks, M&A, and impairments, are not flagged) | spike ≥ 100× both adjacent years | −25 |
```

- [ ] **Step 2: Verify the full suite and linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: magnitude_outlier excludes event-driven flows" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks) — the merge gate

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] **Live smoke:** run
  `python -m src.main GOOGL NVDA COST JNJ GS MET AMT CVX V CAT --no-yahoo --formats json sqlite --db <scratch>/lf.db --output-dir <scratch>/lf --workers 10`,
  then per ticker inspect the `data_quality.findings` for `magnitude_outlier` codes.
  Expected: **JNJ no longer has `magnitude_outlier(debt_issued)`** (nor any magnitude finding);
  no other company gains/loses a `magnitude_outlier`; GOOGL/NVDA/COST/AMT/CVX/CAT still score 100.
  (JNJ will not reach 100 from this fix alone — its `operating_income` and quarterly-cascade
  issues are separate, out-of-scope items.)
- [ ] Only after the smoke is clean: merge to `main` + clean up the branch.
