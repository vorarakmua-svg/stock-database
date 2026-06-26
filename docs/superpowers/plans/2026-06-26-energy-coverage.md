# Energy-Sector Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop penalizing energy companies for a structurally-absent `operating_income`, and resolve the inventory line oil majors actually report — so ExxonMobil scores 100 instead of 50.

**Architecture:** Two small, independent changes: (1) give ENERGY its own required-field set in `quality.py` (GENERAL minus `operating_income`); (2) add the energy inventory tags to the canonical `inventory` field. No metrics/parser/schema change; `roic`/`inventory_turnover` stay NULL for oil majors by design (not fabricated).

**Tech Stack:** Python 3.9+, pytest, ruff, mypy.

## Global Constraints

- Python floor **3.9** — no `X | Y` unions, no `match`.
- ruff line-length **120**, select `E, F, W, I`, imports at top (keep the sectors import alphabetical).
- mypy clean; `quality.py` (via the `src/validation` directory entry) and `canonical.py` (explicit) are both in the mypy `files` list — do not touch pyproject.
- UTILITY is intentionally **not** changed (validated: DUK/NEE report operating income, score 100).
- Do **not** map `CostsAndExpenses` to `cost_of_revenue`; `roic`/`inventory_turnover` remain NULL for oil majors.
- All existing tests stay green.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `feat/energy-coverage` is already checked out.

---

### Task 1: ENERGY required-field set + energy inventory tags

**Files:**
- Modify: `src/validation/quality.py` (import ENERGY; add `_ENERGY_REQUIRED`; add to `REQUIRED_BY_SECTOR`)
- Modify: `src/mappings/canonical.py` (inventory field candidate tags)
- Test: `tests/test_quality.py`, `tests/test_canonical_sectors.py`

**Interfaces:**
- Produces: `REQUIRED_BY_SECTOR["energy"]` = the GENERAL set minus `operating_income`; canonical
  `inventory` resolves from `EnergyRelatedInventory` / `InventoryCrudeOilProductsAndMerchandise`
  (after `InventoryNet`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quality.py` (it already imports `assess_annual` at the top; if not, add
`from src.validation.quality import assess_annual` to the existing top-of-file import):

```python
def test_energy_required_set_drops_operating_income():
    # An energy company reports everything required EXCEPT operating_income
    # (integrated oil majors don't file OperatingIncomeLoss).
    period = {"revenue": 100.0, "net_income": 10.0, "total_assets": 200.0,
              "total_liabilities": 120.0, "total_equity": 80.0, "operating_cash_flow": 20.0}
    annual = {"2024": period}
    energy = assess_annual(annual, sector="energy")
    assert not any(f.code == "missing_field" for f in energy.findings)
    assert energy.score == 100
    # The SAME data under the general sector still requires operating_income.
    general = assess_annual(annual, sector="general")
    assert any(f.code == "missing_field" and "operating_income" in f.message
               for f in general.findings)
```

Append to `tests/test_canonical_sectors.py` (it already imports `XBRLParser` and defines
`_facts` / `_instant_usd`):

```python
def test_energy_inventory_tag_resolves():
    facts = _facts(EnergyRelatedInventory=_instant_usd(21800))
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["inventory"] == 21800


def test_inventory_prefers_inventorynet_over_energy_tag():
    facts = _facts(InventoryNet=_instant_usd(500),
                   EnergyRelatedInventory=_instant_usd(21800))
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["inventory"] == 500
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_quality.py::test_energy_required_set_drops_operating_income tests/test_canonical_sectors.py::test_energy_inventory_tag_resolves -v`
Expected: FAIL — energy currently inherits the GENERAL required set (so `operating_income` IS
flagged missing and `energy.score` is 90, not 100), and `EnergyRelatedInventory` doesn't resolve
to `inventory` (KeyError).

- [ ] **Step 3: Add the ENERGY required-field set in quality.py**

In `src/validation/quality.py`, change the sectors import:

```python
from ..mappings.sectors import BANK, GENERAL, INSURANCE, REIT
```

to (alphabetical):

```python
from ..mappings.sectors import BANK, ENERGY, GENERAL, INSURANCE, REIT
```

Immediately after the `_GENERAL_REQUIRED = ( ... )` tuple, add:

```python
# Integrated oil & gas majors don't file a clean OperatingIncomeLoss; ENERGY uses
# the general set minus operating_income (utilities keep the general set).
_ENERGY_REQUIRED = (
    "revenue", "net_income",
    "total_assets", "total_liabilities", "total_equity", "operating_cash_flow",
)
```

In the `REQUIRED_BY_SECTOR` dict, add an `ENERGY` entry (e.g. right after the `GENERAL` line):

```python
    GENERAL: _GENERAL_REQUIRED,
    ENERGY: _ENERGY_REQUIRED,
```

(Do not add a UTILITY entry — utilities correctly fall through to `_GENERAL_REQUIRED`.)

- [ ] **Step 4: Add the energy inventory tags in canonical.py**

In `src/mappings/canonical.py`, change the `inventory` field's tag tuple from:

```python
        "inventory", "Inventory", BALANCE, UNIT_USD, INSTANT, ("InventoryNet",),
```

to:

```python
        "inventory", "Inventory", BALANCE, UNIT_USD, INSTANT,
        ("InventoryNet", "EnergyRelatedInventory", "InventoryCrudeOilProductsAndMerchandise"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_quality.py tests/test_canonical_sectors.py -v`
Expected: PASS (the two new quality/canonical tests + all existing tests in those files).

- [ ] **Step 6: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check src/validation/quality.py src/mappings/canonical.py tests/test_quality.py tests/test_canonical_sectors.py && python -m mypy`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/validation/quality.py src/mappings/canonical.py tests/test_quality.py tests/test_canonical_sectors.py
git commit -m "feat: energy required-field set (no operating_income) + energy inventory tags" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an energy note to the sector coverage section**

In `README.md`, find the sector-coverage / "Sector-aware" section (search for the sector
table or the bank/insurer/REIT coverage discussion) and add this note after it:

```markdown
**Energy & utilities.** Both use the general operating-company schema, but integrated oil &
gas majors don't file a clean `OperatingIncomeLoss`, so the **energy** sector's required-field
set drops `operating_income` (utilities keep it — they report it). Oil-major `roic` and
`inventory_turnover` are left empty rather than approximated, since the underlying operating
income and a clean cost-of-goods figure aren't reported. The `inventory` line resolves from
energy-specific tags (`EnergyRelatedInventory`, `InventoryCrudeOilProductsAndMerchandise`).
```

- [ ] **Step 2: Verify the full suite and linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: note energy required-field relaxation + inventory tags" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks) — the merge gate

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] **Live smoke:** run
  `python -m src.main WMT BAC PGR O XOM DUK NEE --no-yahoo --formats json sqlite --db <scratch>/e.db --output-dir <scratch>/e --workers 7`,
  then per ticker inspect `collection_runs.quality_score`, the `data_quality.findings`, and
  `financials_annual.inventory`.
  Expected: **XOM recovers to 100** with no `missing_field` findings and `inventory` ≈ $21.8B
  (from `EnergyRelatedInventory`); WMT/BAC/PGR/O unchanged at 100; DUK/NEE unchanged at 100;
  XOM `roic`/`inventory_turnover` still NULL (expected, not fabricated).
- [ ] Only after the smoke is clean: merge to `main` + clean up the branch.
