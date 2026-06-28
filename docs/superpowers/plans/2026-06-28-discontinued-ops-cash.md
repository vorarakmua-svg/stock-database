# Discontinued-Operations Cash Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the cash flow from discontinued operations (aggregate tag, or a derived sum of the three split components) and make the cash-flow consistency check robust to the continuing-vs-total basis ambiguity, so PRU (and equivalently-shaped filers) stop drawing a false `cashflow_imbalance`.

**Architecture:** Add four cash-flow canonical fields so the raw tags are captured; add a `derived_fields` rule that sums the three components into the aggregate when the aggregate tag is absent; update `check_cashflow_reconciliation` to pass when the statement reconciles with OR without the discontinued line; exclude the four new (lumpy/residual) fields from the magnitude-outlier and quarterly-sum checks via a shared constant.

**Tech Stack:** Python 3.9, pytest. Changes in `src/mappings/canonical.py`, `src/parsers/derived_fields.py`, `src/validation/integrity.py` + tests + README.

## Global Constraints

- Python 3.9 floor — no `X | Y` union syntax (use `Optional[...]`, `Tuple[...]`).
- ruff clean: line-length 120; rules E, F, W, I; imports at top of file (avoid E402).
- mypy gate is **bare `mypy`** (project scopes via pyproject `files=[...]`; `canonical.py`, `derived_fields.py`, and `src/validation` ARE in that list — keep them clean). Do NOT run `mypy src` (it ignores the scoping and reports 66 pre-existing unrelated errors).
- All checks remain FLAG-ONLY — never mutate data. Materiality floor `$1,000,000`.
- New fields are signed (no `SIGN_ABS`) and not added to any sector's required-field set.
- Commit trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Capture discontinued-operations cash (canonical fields + derived sum)

**Files:**
- Modify: `src/mappings/canonical.py` (add 4 fields in the cash-flow duration section, after `fx_effect_on_cash`, ~line 304)
- Modify: `src/parsers/derived_fields.py` (add `_discontinued_ops_cash` + a `DERIVATIONS` entry)
- Test: `tests/test_canonical.py` (registry presence), `tests/test_derived_fields.py` (derivation)

**Interfaces:**
- Consumes: existing `CanonicalField`, `CASHFLOW`, `UNIT_USD`, `DURATION` (already imported in `canonical.py`); `apply_derivations`, `_num` (in `derived_fields.py`).
- Produces: canonical keys `cash_from_discontinued_operations`, `discontinued_operating_cash_flow`, `discontinued_investing_cash_flow`, `discontinued_financing_cash_flow`; a derivation that fills `cash_from_discontinued_operations` from the sum of present components.

- [ ] **Step 1: Write the failing registry test**

Append to `tests/test_canonical.py`:

```python
def test_discontinued_ops_cash_fields_registered():
    from src.mappings.canonical import CANONICAL_BY_KEY
    expected = {
        "cash_from_discontinued_operations": "NetCashProvidedByUsedInDiscontinuedOperations",
        "discontinued_operating_cash_flow":
            "CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations",
        "discontinued_investing_cash_flow":
            "CashProvidedByUsedInInvestingActivitiesDiscontinuedOperations",
        "discontinued_financing_cash_flow":
            "CashProvidedByUsedInFinancingActivitiesDiscontinuedOperations",
    }
    for key, primary_tag in expected.items():
        assert key in CANONICAL_BY_KEY, key
        field = CANONICAL_BY_KEY[key]
        assert field.tags[0] == primary_tag
        assert field.statement == "cashflow"
        assert field.kind == "duration"
```

- [ ] **Step 2: Run the registry test to verify it fails**

Run: `python -m pytest tests/test_canonical.py::test_discontinued_ops_cash_fields_registered -v`
Expected: FAIL — `assert 'cash_from_discontinued_operations' in CANONICAL_BY_KEY` (KeyError/assert).

Note: if `field.statement == "cashflow"` / `field.kind == "duration"` raises because the constants differ, check the actual values of `CASHFLOW`/`DURATION` in `canonical.py` and compare against those constants instead of string literals.

- [ ] **Step 3: Add the four canonical fields**

In `src/mappings/canonical.py`, immediately after the `fx_effect_on_cash` `CanonicalField(...)` entry (~line 304), add:

```python
    CanonicalField(
        "cash_from_discontinued_operations", "Cash Flow from Discontinued Operations",
        CASHFLOW, UNIT_USD, DURATION,
        ("NetCashProvidedByUsedInDiscontinuedOperations",),
        description="Net cash flow from discontinued operations; when only the split "
                    "operating/investing/financing components are filed, this is derived "
                    "as their sum. Included in the cash-flow consistency check.",
    ),
    CanonicalField(
        "discontinued_operating_cash_flow", "Discontinued Operations — Operating Cash Flow",
        CASHFLOW, UNIT_USD, DURATION,
        ("CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations",),
    ),
    CanonicalField(
        "discontinued_investing_cash_flow", "Discontinued Operations — Investing Cash Flow",
        CASHFLOW, UNIT_USD, DURATION,
        ("CashProvidedByUsedInInvestingActivitiesDiscontinuedOperations",),
    ),
    CanonicalField(
        "discontinued_financing_cash_flow", "Discontinued Operations — Financing Cash Flow",
        CASHFLOW, UNIT_USD, DURATION,
        ("CashProvidedByUsedInFinancingActivitiesDiscontinuedOperations",),
    ),
```

- [ ] **Step 4: Run the registry test to verify it passes**

Run: `python -m pytest tests/test_canonical.py::test_discontinued_ops_cash_fields_registered -v`
Expected: PASS.

- [ ] **Step 5: Write the failing derivation tests**

Append to `tests/test_derived_fields.py`:

```python
def test_discontinued_ops_cash_derived_from_components():
    period = {
        "discontinued_operating_cash_flow": -1500.0,
        "discontinued_investing_cash_flow": -500.0,
        "discontinued_financing_cash_flow": -71.0,
    }
    derived = apply_derivations(period)
    assert period["cash_from_discontinued_operations"] == -2071.0
    assert "cash_from_discontinued_operations" in derived
    assert period["_source_tags"]["cash_from_discontinued_operations"] == "derived"


def test_discontinued_ops_cash_not_overwritten_when_reported():
    period = {
        "cash_from_discontinued_operations": -2071.0,
        "_source_tags": {"cash_from_discontinued_operations":
                         "NetCashProvidedByUsedInDiscontinuedOperations"},
        "discontinued_operating_cash_flow": -1500.0,
    }
    apply_derivations(period)
    assert period["cash_from_discontinued_operations"] == -2071.0  # untouched
    assert period["_source_tags"]["cash_from_discontinued_operations"] == \
        "NetCashProvidedByUsedInDiscontinuedOperations"


def test_discontinued_ops_cash_sums_only_present_components():
    period = {"discontinued_operating_cash_flow": -1500.0}  # only one component
    apply_derivations(period)
    assert period["cash_from_discontinued_operations"] == -1500.0


def test_discontinued_ops_cash_absent_when_no_components():
    period = {"revenue": 100.0}
    derived = apply_derivations(period)
    assert "cash_from_discontinued_operations" not in period
    assert "cash_from_discontinued_operations" not in derived
```

- [ ] **Step 6: Run the derivation tests to verify they fail**

Run: `python -m pytest tests/test_derived_fields.py -k discontinued -v`
Expected: FAIL — `cash_from_discontinued_operations` is not derived (key absent).

- [ ] **Step 7: Add the derivation**

In `src/parsers/derived_fields.py`, add this function after `_bank_revenue` (~line 48):

```python
def _discontinued_ops_cash(p: Dict[str, Any]) -> Optional[float]:
    """Aggregate discontinued-operations cash = sum of the operating/investing/financing
    discontinued components, used when the single aggregate tag is not filed."""
    parts = [
        _num(p, "discontinued_operating_cash_flow"),
        _num(p, "discontinued_investing_cash_flow"),
        _num(p, "discontinued_financing_cash_flow"),
    ]
    present = [v for v in parts if v is not None]
    return sum(present) if present else None
```

Then add an entry to the `DERIVATIONS` list (after the `gross_profit` entry, ~line 55):

```python
    ("cash_from_discontinued_operations", _discontinued_ops_cash,
     "discontinued operating + investing + financing cash flows"),
```

- [ ] **Step 8: Run the derivation tests to verify they pass**

Run: `python -m pytest tests/test_derived_fields.py -k discontinued -v`
Expected: PASS (4 tests).

- [ ] **Step 9: Commit**

```bash
git add src/mappings/canonical.py src/parsers/derived_fields.py tests/test_canonical.py tests/test_derived_fields.py
git commit -m "feat: capture discontinued-operations cash (fields + derived sum)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Cash-flow check — either-basis reconciliation + exclusions

**Files:**
- Modify: `src/validation/integrity.py` (`_DISCONTINUED_FIELDS` constant; `_OUTLIER_EXCLUDE`; `check_quarterly_sums` skip; `_exceeds_tolerance` helper; `check_cashflow_reconciliation` body)
- Test: `tests/test_integrity.py`

**Interfaces:**
- Consumes: canonical key `cash_from_discontinued_operations` (Task 1), populated upstream by capture/derivation; existing `_num`, `_MATERIALITY`, `_CASH_TOL`, `_CASH_GROSS_TOL`, `_EVENT_DRIVEN_FLOWS`, `Finding`, `MEDIUM`.
- Produces: no new public signatures; `check_cashflow_reconciliation` now passes when the statement reconciles with or without the discontinued line; the four discontinued fields are excluded from outlier + quarterly-sum checks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integrity.py` (the module already imports `check_cashflow_reconciliation`, `check_field_outliers`, `check_quarterly_sums`):

```python
def test_cashflow_reconcile_silent_with_continuing_sections_plus_discontinued():
    # PRU-shaped: OCF/ICF/FCF are continuing-only; a separate discontinued line and FX
    # make up the difference to the reported net change. Adding the discontinued line
    # reconciles, so no finding.
    annual = {"2021": {
        "net_change_in_cash": -921.0e6,
        "operating_cash_flow": 9812.0e6,
        "investing_cash_flow": -5342.0e6,
        "financing_cash_flow": -3011.0e6,
        "fx_effect_on_cash": -309.0e6,
        "cash_from_discontinued_operations": -2071.0e6,
    }}  # 9812 - 5342 - 3011 - 309 - 2071 = -921
    assert check_cashflow_reconciliation(annual, {"2021"}) == []


def test_cashflow_reconcile_silent_total_basis_no_discontinued():
    # Sections already include everything; no discontinued field -> still silent (regression).
    annual = {"2024": {"net_change_in_cash": -42.0e6, "operating_cash_flow": 30.0e6,
                       "investing_cash_flow": -20.0e6, "financing_cash_flow": -12.0e6,
                       "fx_effect_on_cash": -40.0e6}}
    assert check_cashflow_reconciliation(annual, {"2024"}) == []


def test_cashflow_imbalance_fires_when_neither_basis_reconciles():
    # A genuine break: neither expected nor expected+disc matches the net change.
    annual = {"2024": {"net_change_in_cash": 5.0e9, "operating_cash_flow": 0.4e9,
                       "investing_cash_flow": -0.1e9, "financing_cash_flow": 0.2e9,
                       "cash_from_discontinued_operations": 0.1e9}}
    findings = check_cashflow_reconciliation(annual, {"2024"})
    assert [(f.code, f.period) for f in findings] == [("cashflow_imbalance", "2024")]


def test_outlier_skips_discontinued_ops_fields():
    # cash_from_discontinued_operations spikes 100x and reverts, but is excluded.
    annual = {
        "2021": {"cash_from_discontinued_operations": 1.0e7, "revenue": 1.0e9},
        "2022": {"cash_from_discontinued_operations": 1.0e12, "revenue": 1.1e9},
        "2023": {"cash_from_discontinued_operations": 1.1e7, "revenue": 1.2e9},
    }
    findings = check_field_outliers(annual, {"2021", "2022", "2023"})
    assert all("discontinued" not in f.message for f in findings)
    assert findings == []  # revenue is a smooth trend; nothing else flags


def test_quarterly_sum_skips_discontinued_ops_fields():
    annual = {"2024": {"cash_from_discontinued_operations": 1.0e9, "revenue": 1.0e9}}

    def _qd(fq, disc, rev):
        return {"fiscal_year": 2024, "fiscal_quarter": fq,
                "cash_from_discontinued_operations": disc, "revenue": rev}

    quarterly = {  # each field's quarters sum to 0.8e9 vs annual 1.0e9 (20% off)
        "2024-03-31": _qd(1, 0.20e9, 0.20e9), "2024-06-30": _qd(2, 0.20e9, 0.20e9),
        "2024-09-30": _qd(3, 0.20e9, 0.20e9), "2024-12-31": _qd(4, 0.20e9, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert len(findings) == 1
    assert "revenue" in findings[0].message
    assert "discontinued" not in findings[0].message
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_integrity.py -k "discontinued or neither_basis or continuing_sections or total_basis" -v`
Expected: FAIL — the PRU-shaped case currently fires (`expected` excludes the discontinued line), and the discontinued fields are not yet excluded from the outlier/quarterly checks.

- [ ] **Step 3: Add the shared constant and extend the exclusion set**

In `src/validation/integrity.py`, after the `_EVENT_DRIVEN_FLOWS` definition (~line 24), add:

```python
# Discontinued-operations cash: lumpy/event-driven, and the aggregate is a cash-flow
# reconciliation residual. Captured, but excluded from the magnitude-outlier and
# quarterly-sum checks (like the event-driven flows and the cash residuals).
_DISCONTINUED_FIELDS = frozenset({
    "cash_from_discontinued_operations",
    "discontinued_operating_cash_flow",
    "discontinued_investing_cash_flow",
    "discontinued_financing_cash_flow",
})
```

Then change `_OUTLIER_EXCLUDE` (currently
`_OUTLIER_EXCLUDE = _EVENT_DRIVEN_FLOWS | frozenset({"net_change_in_cash", "fx_effect_on_cash"})`) to:

```python
_OUTLIER_EXCLUDE = (
    _EVENT_DRIVEN_FLOWS | _DISCONTINUED_FIELDS
    | frozenset({"net_change_in_cash", "fx_effect_on_cash"})
)
```

- [ ] **Step 4: Skip the discontinued fields in the quarterly-sum check**

In `check_quarterly_sums`, change the existing skip (currently):

```python
        for key in _FLOW_FIELDS:
            if key in _EVENT_DRIVEN_FLOWS:
                continue
```

to:

```python
        for key in _FLOW_FIELDS:
            if key in _EVENT_DRIVEN_FLOWS or key in _DISCONTINUED_FIELDS:
                continue
```

- [ ] **Step 5: Add the tolerance helper and the either-basis reconciliation**

In `src/validation/integrity.py`, add a module-level helper (place it just above `check_cashflow_reconciliation`):

```python
def _exceeds_cash_tolerance(residual: float, denom: float, gross: float) -> bool:
    """True when a cash residual is material BOTH vs the net change and vs gross activity."""
    return abs(residual) > _CASH_TOL * denom and abs(residual) > _CASH_GROSS_TOL * gross
```

Then replace the body of `check_cashflow_reconciliation` from `fx = _num(...)` through the
`findings.append(...)` block (current lines ~110-127) with:

```python
        fx = _num(period, "fx_effect_on_cash") or 0.0
        disc = _num(period, "cash_from_discontinued_operations") or 0.0
        expected = ocf + icf + fcf + fx
        denom = max(abs(net_change), abs(expected))
        if denom < _MATERIALITY:
            continue
        gross = abs(ocf) + abs(icf) + abs(fcf)
        # Two valid bases: the section subtotals may already include discontinued
        # operations (reconciles against `expected`), or be continuing-only (then the
        # separately-reported discontinued-ops cash line must be added -> `expected +
        # disc`). Flag only when NEITHER basis reconciles, so a filer like PRU that
        # reports continuing-only sections plus a separate discontinued line is not a
        # false positive. When `disc` is 0 the two bases are identical (behavior
        # unchanged for every filer without discontinued operations).
        residual = net_change - expected
        residual_disc = net_change - (expected + disc)
        denom_disc = max(abs(net_change), abs(expected + disc))
        if (_exceeds_cash_tolerance(residual, denom, gross)
                and _exceeds_cash_tolerance(residual_disc, denom_disc, gross)):
            findings.append(Finding(
                MEDIUM, "cashflow_imbalance",
                f"reported net change in cash ({net_change:,.0f}) != operating+"
                f"investing+financing+FX ({expected:,.0f}); residual {residual:,.0f}.",
                year,
            ))
```

- [ ] **Step 6: Run the integrity tests to verify they pass**

Run: `python -m pytest tests/test_integrity.py -v`
Expected: PASS — the new tests pass and every pre-existing integrity test still passes (the `disc == 0` path is identical to the old `expected`-only logic).

- [ ] **Step 7: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "fix: reconcile cash flow with or without the discontinued-ops line

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Docs, full verification, and live merge gate

**Files:**
- Modify: `README.md` (the "Cross-sector coverage" / cash-flow integrity area, ~lines 354-420)

**Interfaces:**
- Consumes: the completed Tasks 1-2.
- Produces: nothing new (verification + docs only).

- [ ] **Step 1: Update the README**

In `README.md`, in the integrity-checks table row for the cash-flow consistency check (the `Cash-flow consistency` row, ~line 417), append to its "Catches" cell a parenthetical noting the discontinued-ops handling. Change the cell text:

```markdown
| Cash-flow consistency | the cash-flow statement's reported net change != its own sections + FX effect | residual > 1% | −10 |
```

to:

```markdown
| Cash-flow consistency | the cash-flow statement's reported net change != its own sections + FX effect (a continuing-only statement reconciles once the separately-reported `cash_from_discontinued_operations` line is added) | residual > 1% both bases | −10 |
```

- [ ] **Step 2: Run the full suite + linters**

Run:
```bash
python -m pytest -q
ruff check src tests
mypy
```
Expected: all tests pass; ruff clean; bare `mypy` reports "Success".

- [ ] **Step 3: Commit the docs**

```bash
git add README.md
git commit -m "docs: note discontinued-ops reconciliation in the cash-flow check

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Rebuild the DB and run the live 50-stock merge gate**

```bash
rm -f data/output/stock.db
python -m src.main JPM BAC WFC C GS USB PGR TRV ALL MET PRU CB PLD AMT EQIX SPG O PSA NEE DUK SO D XOM CVX COP SLB AAPL MSFT GOOGL NVDA AVGO ORCL WMT COST HD PG KO MCD JNJ UNH PFE ABBV MRK CAT HON GE BA VZ T TMUS --no-yahoo
```

- [ ] **Step 5: Verify PRU fixed, capture coverage, and check for regressions**

Run:
```bash
python - <<'PY'
import json, glob
reporters = {"PRU","DUK","GE","PFE","PG","VZ","HON","COP","TMUS","KO","SLB","MRK","O","T","C","EQIX"}
comp_only = {"GE","PFE","PG","VZ","HON","COP","TMUS"}
rows = []
for fp in glob.glob("data/output/json/*.json"):
    d = json.load(open(fp, encoding="utf-8")); t = d.get("ticker")
    dq = d.get("data_quality") or {}; s = dq.get("score")
    if s is None:
        continue
    rows.append((t, s, [f.get("code") for f in dq.get("findings", [])]))
by = {t: (s, f) for t, s, f in rows}
print("PRU:", by.get("PRU"))
assert by["PRU"][0] == 100, ("PRU not 100", by["PRU"])
assert "cashflow_imbalance" not in by["PRU"][1], by["PRU"]
# cash_from_discontinued_operations populated for all 16 reporters (any annual year)
missing = []
for fp in glob.glob("data/output/json/*.json"):
    d = json.load(open(fp, encoding="utf-8")); t = d.get("ticker")
    if t not in reporters:
        continue
    fa = d.get("financials_annual") or {}
    if not any((p.get("cash_from_discontinued_operations") is not None) for p in fa.values()):
        missing.append(t)
print("reporters missing aggregate:", missing or "NONE")
assert not missing, missing
# component-only filers must populate via the derived path
for t in comp_only:
    d = json.load(open(f"data/output/json/{t}.json", encoding="utf-8"))
    fa = d.get("financials_annual") or {}
    derived = [y for y, p in fa.items()
               if (p.get("_source_tags") or {}).get("cash_from_discontinued_operations") == "derived"]
    print(f"  {t}: derived years = {len(derived)}")
    assert derived, f"{t} should have a derived aggregate"
sc = [s for _, s, _ in rows]
print(f"\nN={len(sc)} min={min(sc)} mean={round(sum(sc)/len(sc),1)} max={max(sc)}")
for t, s, f in sorted(rows, key=lambda r: r[1]):
    if s < 100:
        print("  <100:", t, s, f)
PY
```
Expected:
- **PRU score 100**, no `cashflow_imbalance`.
- `cash_from_discontinued_operations` populated for all 16 reporters; the 7 component-only filers (GE, PFE, PG, VZ, HON, COP, TMUS) populate it via the `derived` path.
- The only remaining sub-100 score is **PSA (97)** (REIT EBITDA proxy — out of scope). No other company is below 100 and no company regressed versus the prior run (which had PRU 80 + PSA 97).
- Full suite + ruff + bare `mypy` green.

If any reporter is missing the aggregate, or any non-PSA company is below 100, capture the ticker/finding and investigate before merging — do not merge over a regression.

- [ ] **Step 6: Push and open the PR (only after the gate is green)**

```bash
git push -u origin feat/discontinued-ops-cash
gh pr create --base main --title "Capture discontinued-operations cash flow" \
  --body "Captures cash flow from discontinued operations (aggregate tag, or a derived sum of the operating/investing/financing components for the 7 basket filers that report only the split lines) and makes the cash-flow consistency check pass when a statement reconciles with OR without that line. Fixes PRU's false cashflow_imbalance (80 -> 100). Spec/plan in docs/superpowers/.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review

**Spec coverage:**
- 4 canonical fields (aggregate + 3 components) → Task 1 Steps 3. ✓
- Derived sum when aggregate absent → Task 1 Steps 7. ✓
- Reconciliation either-basis (with/without disc) → Task 2 Step 5. ✓
- Cannot-regress (disc==0 identical to old) → guarded by `test_cashflow_reconcile_silent_total_basis_no_discontinued` + the unchanged existing tests. ✓
- Exclusions from magnitude-outlier + quarterly-sum via shared `_DISCONTINUED_FIELDS` → Task 2 Steps 3-4, tests Step 1. ✓
- README update → Task 3 Step 1. ✓
- Merge gate (PRU 80→100; all 16 populated; component-only via derived; no regression) → Task 3 Steps 4-5. ✓
- Global constraints (3.9, ruff, bare mypy, signed fields, not required, flag-only) → Global Constraints block + tasks. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows full code. The one conditional note (Task 1 Step 2) gives an explicit fallback action, not a vague instruction. ✓

**Type consistency:** `_discontinued_ops_cash(p: Dict[str, Any]) -> Optional[float]` matches the other `derived_fields` helpers and the `DERIVATIONS` tuple shape `(key, fn, formula)`. `_exceeds_cash_tolerance(residual, denom, gross) -> bool` is defined before its two call sites in `check_cashflow_reconciliation`. `_DISCONTINUED_FIELDS` (frozenset) is referenced in `_OUTLIER_EXCLUDE` and the quarterly skip with matching `in` semantics. Field keys are identical across canonical.py, derived_fields.py, integrity.py, and the tests. ✓
