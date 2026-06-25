# Cash-Reconciliation Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unsound balance-sheet-vs-flows cash check with an exact internal-consistency check (`net_change_in_cash ≈ OCF+ICF+FCF+FX`), so clean filings stop false-flagging `cashflow_imbalance`.

**Architecture:** Add two registry-driven canonical fields (`net_change_in_cash`, `fx_effect_on_cash`), exclude them from the volatile-prone magnitude-outlier set, and rewrite `check_cashflow_reconciliation` to reconcile the cash-flow statement against itself. No parser/schema change. On the in-flight `feat/integrity-hardening` branch (amends PR #9).

**Tech Stack:** Python 3.9+, pytest, ruff, mypy.

## Global Constraints

- Python floor **3.9** — no `X | Y` unions, no `match`.
- ruff line-length **120**, select `E, F, W, I`, imports at top (E402). **XBRL tags here exceed 120 chars** — split long tag/key string literals with implicit string concatenation (adjacent `"..."` `"..."`) so every line is ≤120; do **not** use `# noqa`.
- mypy must pass clean; `integrity.py` is already covered by the `"src/validation"` directory entry — **do not** add it (or `canonical.py`, already listed) explicitly to mypy `files`.
- Flag-only: checks never mutate data. Materiality floor **$1,000,000**; scoring window recent **5** fiscal years.
- New tolerance: `_CASH_TOL = 0.01` (was 0.05). New reconciliation: `net_change_in_cash` vs `OCF+ICF+FCF+(FX or 0)`.
- All existing tests stay green; the live smoke (AAPL/MSFT/JPM/PLD) must show **no** `cashflow_imbalance`.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `feat/integrity-hardening` is already checked out.

---

### Task 1: Add the two canonical fields + exclude them from magnitude-outlier

Adding `net_change_in_cash` / `fx_effect_on_cash` as USD fields makes the registry auto-extract them — but it also pulls them into `_USD_FIELDS` (the magnitude-outlier candidate set), where these volatile net-residuals would false-positive. This task adds the fields **and** excludes them from that one check.

**Files:**
- Modify: `src/mappings/canonical.py` (add 2 fields to the cash-flow section)
- Modify: `src/validation/integrity.py` (`_OUTLIER_EXCLUDE` + filter `_USD_FIELDS`)
- Test: `tests/test_canonical_sectors.py` (field resolution), `tests/test_integrity.py` (outlier exclusion)

**Interfaces:**
- Produces: canonical keys `net_change_in_cash`, `fx_effect_on_cash`; module constant `_OUTLIER_EXCLUDE: frozenset` in `integrity.py`. `_FLOW_FIELDS` continues to include both new fields (they are USD DURATION cash-flow fields); `_USD_FIELDS` excludes them.

- [ ] **Step 1: Write the field-resolution test**

Append to `tests/test_canonical_sectors.py` (it already imports `XBRLParser` and defines `_facts` / `_annual_usd`):

```python
def test_cashflow_reconciliation_fields_resolve():
    net_tag = ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
               "PeriodIncreaseDecreaseIncludingExchangeRateEffect")
    facts = _facts(**{net_tag: _annual_usd(5000),
                      "EffectOfExchangeRateOnCashAndCashEquivalents": _annual_usd(-40)})
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["net_change_in_cash"] == 5000
    assert annual["fx_effect_on_cash"] == -40
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_canonical_sectors.py::test_cashflow_reconciliation_fields_resolve -v`
Expected: FAIL with `KeyError: 'net_change_in_cash'`

- [ ] **Step 3: Add the two canonical fields**

In `src/mappings/canonical.py`, in the `# ---------------- Cash flow (duration) ----------------` section (e.g. immediately after the `debt_repaid` field), add:

```python
    CanonicalField(
        "net_change_in_cash", "Net Change in Cash", CASHFLOW, UNIT_USD, DURATION,
        (
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
            "PeriodIncreaseDecreaseIncludingExchangeRateEffect",
            "CashAndCashEquivalentsPeriodIncreaseDecrease",
        ),
        description="Reported total net change in cash (restricted-cash-inclusive, "
                    "incl. FX); basis for the cash-flow internal-consistency check.",
    ),
    CanonicalField(
        "fx_effect_on_cash", "FX Effect on Cash", CASHFLOW, UNIT_USD, DURATION,
        (
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
            "IncludingDisposalGroupAndDiscontinuedOperations",
            "EffectOfExchangeRateOnCashAndCashEquivalents",
            "EffectOfExchangeRateOnCashAndCashEquivalentsContinuingOperations",
        ),
    ),
```

- [ ] **Step 4: Run the resolution test to verify it passes**

Run: `python -m pytest tests/test_canonical_sectors.py::test_cashflow_reconciliation_fields_resolve -v`
Expected: PASS

- [ ] **Step 5: Write the magnitude-outlier exclusion test**

Append to `tests/test_integrity.py` (it already imports `check_field_outliers` at the top):

```python
def test_magnitude_outlier_excludes_volatile_cashflow_residuals():
    # net_change_in_cash legitimately swings >100x; it must NOT be flagged
    # (excluded from the outlier candidate set).
    annual = {
        "2021": {"net_change_in_cash": 5.0e7},
        "2022": {"net_change_in_cash": 5.0e7},
        "2023": {"net_change_in_cash": 5.0e7},
        "2024": {"net_change_in_cash": 2.0e10},  # 400x swing
    }
    assert check_field_outliers(annual, {"2021", "2022", "2023", "2024"}) == []
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/test_integrity.py::test_magnitude_outlier_excludes_volatile_cashflow_residuals -v`
Expected: FAIL — `net_change_in_cash` is now a USD field, so the outlier check flags the 400× 2024 value (one `magnitude_outlier` finding; the asserted `== []` fails).

- [ ] **Step 7: Exclude the two fields from `_USD_FIELDS`**

In `src/validation/integrity.py`, replace the `_USD_FIELDS` definition:

```python
# USD "level" fields (income/balance/cash-flow amounts); excludes per-share and
# share-count fields. Candidates for the magnitude-outlier check.
_USD_FIELDS = tuple(f.key for f in CANONICAL_FIELDS if f.unit == UNIT_USD)
```

with (add the exclusion set just above it, then filter):

```python
# Volatile net-residual flows that legitimately swing far more than the outlier
# factor year-to-year (a near-zero year beside a multi-billion one); excluded from
# the magnitude-outlier candidate set, but still checked by the quarterly-sum check.
_OUTLIER_EXCLUDE = frozenset({"net_change_in_cash", "fx_effect_on_cash"})

# USD "level" fields (income/balance/cash-flow amounts); excludes per-share and
# share-count fields and the volatile residuals above. Candidates for magnitude-outlier.
_USD_FIELDS = tuple(
    f.key for f in CANONICAL_FIELDS
    if f.unit == UNIT_USD and f.key not in _OUTLIER_EXCLUDE
)
```

(Leave `_FLOW_FIELDS` unchanged — the two new fields stay in the quarterly-sum check.)

- [ ] **Step 8: Run the exclusion test to verify it passes**

Run: `python -m pytest tests/test_integrity.py::test_magnitude_outlier_excludes_volatile_cashflow_residuals -v`
Expected: PASS

- [ ] **Step 9: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS. (If a test asserts an exact canonical field/tag count, update it to include the two new fields — that's a legitimate change. None is expected.)

Run: `python -m ruff check src/mappings/canonical.py src/validation/integrity.py tests/test_canonical_sectors.py tests/test_integrity.py && python -m mypy`
Expected: no errors (every tag-string line is ≤120 via implicit concatenation).

- [ ] **Step 10: Commit**

```bash
git add src/mappings/canonical.py src/validation/integrity.py tests/test_canonical_sectors.py tests/test_integrity.py
git commit -m "feat: add net_change_in_cash + fx_effect_on_cash fields; exclude from outlier" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Rewrite the cash-flow reconciliation check

Replace the balance-sheet-vs-flows logic with the internal-consistency check, and tighten the tolerance.

**Files:**
- Modify: `src/validation/integrity.py` (`check_cashflow_reconciliation`, `_CASH_TOL`)
- Test: `tests/test_integrity.py` (replace the three `cashflow` tests)

**Interfaces:**
- Consumes: canonical keys `net_change_in_cash`, `operating_cash_flow`, `investing_cash_flow`, `financing_cash_flow`, `fx_effect_on_cash` (Task 1 + existing).
- Produces: same signature `check_cashflow_reconciliation(annual, scored_years) -> List[Finding]`, new internal-consistency semantics.

- [ ] **Step 1: Replace the three cashflow tests**

In `tests/test_integrity.py`, DELETE the `_cf` helper and the three existing `test_cashflow_*` functions (they test the old balance-sheet formula and read `cash_and_equivalents`). Add these three in their place:

```python
def test_cashflow_imbalance_fires_when_sections_dont_match_net_change():
    # sections+FX = 0.5e9 but the reported net change is 1.0e9 -> 50% residual.
    annual = {"2024": {"net_change_in_cash": 1.0e9, "operating_cash_flow": 0.4e9,
                       "investing_cash_flow": -0.1e9, "financing_cash_flow": 0.2e9}}
    findings = check_cashflow_reconciliation(annual, {"2024"})
    assert [(f.code, f.period, f.severity) for f in findings] == [
        ("cashflow_imbalance", "2024", "medium")]


def test_cashflow_reconcile_silent_when_consistent_including_fx():
    # net_change == OCF+ICF+FCF+FX exactly, with a large non-zero FX -> silent.
    annual = {"2024": {"net_change_in_cash": -42.0e6, "operating_cash_flow": 30.0e6,
                       "investing_cash_flow": -20.0e6, "financing_cash_flow": -12.0e6,
                       "fx_effect_on_cash": -40.0e6}}  # 30 - 20 - 12 - 40 = -42
    assert check_cashflow_reconciliation(annual, {"2024"}) == []


def test_cashflow_reconcile_skips_when_net_change_or_section_absent():
    # net_change absent -> skip
    a1 = {"2024": {"operating_cash_flow": 1.0e9, "investing_cash_flow": 0.0,
                   "financing_cash_flow": 0.0}}
    assert check_cashflow_reconciliation(a1, {"2024"}) == []
    # a section absent -> skip
    a2 = {"2024": {"net_change_in_cash": 1.0e9, "operating_cash_flow": 1.0e9,
                   "financing_cash_flow": 0.0}}
    assert check_cashflow_reconciliation(a2, {"2024"}) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_integrity.py -k cashflow -v`
Expected: FAIL — the old function reads `cash_and_equivalents` (absent here) and returns `[]`, so the "fires" test's expected one finding is missing.

- [ ] **Step 3: Rewrite the function and tolerance**

In `src/validation/integrity.py`, change the tolerance constant:

```python
_CASH_TOL = 0.05
```

to:

```python
_CASH_TOL = 0.01
```

Then replace the entire existing `check_cashflow_reconciliation` function with:

```python
def check_cashflow_reconciliation(
    annual: Dict[str, Any], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag when the reported net change in cash != operating+investing+financing+FX.

    An internal-consistency check on the cash-flow statement itself: its reported
    net-change line equals the sum of its three sections plus the FX effect by
    construction, so the residual is ~0 for a correctly-tagged filing. (Comparing
    against the balance-sheet cash line is unsound — that line excludes restricted
    cash and the FX effect, producing systematic false positives.)
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    for year in scored:
        period = annual.get(year)
        if not period:
            continue
        net_change = _num(period, "net_change_in_cash")
        ocf = _num(period, "operating_cash_flow")
        icf = _num(period, "investing_cash_flow")
        fcf = _num(period, "financing_cash_flow")
        if net_change is None or ocf is None or icf is None or fcf is None:
            continue
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
    return findings
```

- [ ] **Step 4: Run the cashflow tests to verify they pass**

Run: `python -m pytest tests/test_integrity.py -k cashflow -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite)

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "fix: reconcile cash flow internally (net change vs sections + FX)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the cash-flow row in the integrity-checks table**

In `README.md`, find the "### Integrity checks (data-quality score)" table (added for PR #9). Replace the cash-flow reconciliation row:

```markdown
| Cash-flow reconciliation | a cash-flow statement that doesn't explain the change in balance-sheet cash | residual > 5% | −10 |
```

with:

```markdown
| Cash-flow consistency | the cash-flow statement's reported net change != its own sections + FX effect | residual > 1% | −10 |
```

- [ ] **Step 2: Verify the full suite and linters**

Run: `python -m pytest -q`
Expected: PASS — full suite (was 142; now 142 + the resolution test + the outlier-exclusion test; the three cashflow tests were replaced 1:1).

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: cash-flow check is now internal-consistency based" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks) — the merge gate

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] **Live smoke (the whole reason for this redesign):** run
  `python -m src.main AAPL MSFT JPM PLD --no-yahoo --formats sqlite --db <scratch>/v2.db --output-dir <scratch>/v2 --workers 4`,
  then for each ticker inspect `collection_runs.quality_score` and the data-quality findings.
  Expected: **no `cashflow_imbalance` findings** on any of the four, and `quality_score` 100
  (or unchanged from pre-feature) — i.e. the false positives are gone.
- [ ] Only after the smoke is clean: merge PR #9 + clean up the branch.
