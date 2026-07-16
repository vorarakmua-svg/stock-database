# Owner Earnings ("Buffett mode") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-earnings valuation model following Buffett's stated method (owner earnings, Treasury discount, margin of safety on price, refuse to forecast erratic businesses), stored as a sixth model but excluded from the cross-model median so it can never silently move the existing verdicts.

**Architecture:** Extends the existing pure `src/valuation/` package. `inputs.py` gains two fields (D&A, capex); `models.py` gains `value_owner_earnings`, reusing the existing `dcf_per_share` discounting so there is one discounting implementation; `engine.py` registers it in the model list but restricts `intrinsic_summary` to a new `MEDIAN_MODELS` constant. No schema change — it reuses the `valuations` table.

**Tech Stack:** Python 3.9 stdlib (statistics, dataclasses), FastAPI + Jinja2 + htmx, Plotly, pytest.

**Spec:** `docs/superpowers/specs/2026-07-14-owner-earnings-design.md` — read it before starting any task.

## Global Constraints

- Python 3.9 compatibility: no `X | Y` unions, no runtime `list[...]`; use `typing.Optional/List/Dict/Tuple`.
- Gates after every task: `ruff check src tests`, bare `mypy` (NOT `mypy src` — bare uses the pyproject `files` allowlist; `mypy src` surfaces ~63 pre-existing legacy errors and is the wrong gate), and `python -m pytest -q`. Baseline at plan start: **774 passed**.
- Model key (exact string, used as a DB value and API key): `owner_earnings`.
- Constants (exact, defined once in `src/valuation/assumptions.py`): `OE_DISCOUNT_FLOOR = 0.07`, `MARGIN_OF_SAFETY = 0.30`, `OE_MIN_POSITIVE_YEARS = 8`, `OE_HISTORY_WINDOW = 10`.
- Reuses existing constants: `TERMINAL_GROWTH = 0.025`, `GROWTH_CAP = 0.15`.
- Sector applicability: `general`, `utility`, `energy` only (the existing `DCF_SECTORS` tuple in `models.py`). Banks/insurers/REITs are N/A.
- N/A reasons are user-facing copy — use the exact strings given in each task, byte-for-byte.
- Owner-earnings verdict strings reuse the existing `cheap` / `fair` / `expensive` and `VERDICT_LABELS`.
- **Beta is not used anywhere in this model.** Stock-based comp is **not** added back.
- The median carve-out is load-bearing: `owner_earnings` must never enter `intrinsic_summary`.
- Commit after every task.

---

### Task 1: Carry D&A and capex into ValuationInputs

**Files:**
- Modify: `src/valuation/inputs.py` (FYRecord fields + the `load_inputs` SELECT and FYRecord construction)
- Test: `tests/test_valuation_inputs.py` (append)

**Interfaces:**
- Consumes: existing `financials_annual` columns `depreciation_amortization` and `capex` (both already collected; D&A present for 48/50 tickers).
- Produces (used by Task 2): `FYRecord.depreciation_amortization: Optional[float]` and `FYRecord.capex: Optional[float]`, both **absolute** dollar figures. They are split-invariant (unlike per-share fields), so `_normalize_splits` must NOT touch them — a test enforces this.

- [ ] **Step 1: Write the failing tests (append to `tests/test_valuation_inputs.py`)**

```python
def test_load_inputs_carries_da_and_capex(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    rec = inputs.fy_records[-1]
    assert rec.depreciation_amortization == 40.0
    assert rec.capex == -60.0  # as filed: capex is negative in the cash-flow statement


def test_split_normalization_leaves_da_and_capex_alone():
    """D&A and capex are absolute dollars, not per-share — a split does not
    restate them, so the normalizer must not scale them."""
    from src.valuation.inputs import FYRecord, _normalize_splits
    recs = [
        FYRecord(fiscal_year=2020, period_end=None, net_income=100.0,
                 total_equity=500.0, eps_diluted=8.0, shares=100.0, fcf=None,
                 ffo_per_share=None, depreciation_amortization=40.0, capex=-60.0),
        FYRecord(fiscal_year=2021, period_end=None, net_income=100.0,
                 total_equity=500.0, eps_diluted=2.0, shares=400.0, fcf=None,
                 ffo_per_share=None, depreciation_amortization=44.0, capex=-66.0),
    ]
    survivors, truncated = _normalize_splits(recs, [100.0, 400.0])
    assert truncated is False
    assert survivors[0].split_factor == 4.0          # 4:1 split detected
    assert survivors[0].eps_diluted == 2.0           # per-share restated
    assert survivors[0].depreciation_amortization == 40.0   # absolute, untouched
    assert survivors[0].capex == -60.0                      # absolute, untouched
```

The `val_db` fixture at the top of this file must also gain the two columns. Find its
`INSERT INTO financials_annual (...)` statement and extend it so each fiscal year also
writes `depreciation_amortization` and `capex`:

```python
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted, "
            "depreciation_amortization, capex) "
            "VALUES ('AAA', ?, ?, ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", 100.0 + 10 * i, 500.0, 1.0 + 0.1 * i, 100.0,
             40.0, -60.0),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_valuation_inputs.py -v -k "da_and_capex or leaves_da"`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'depreciation_amortization'`

- [ ] **Step 3: Add the fields to `FYRecord`**

In `src/valuation/inputs.py`, add two fields to the `FYRecord` dataclass, placed
**before** `split_factor` (which has a default, so all non-default fields must precede it):

```python
    fcf: Optional[float]
    ffo_per_share: Optional[float]
    depreciation_amortization: Optional[float] = None
    capex: Optional[float] = None
    split_factor: float = 1.0
```

Extend the `FYRecord` docstring's per-share note with one sentence:

```
    ``depreciation_amortization`` and ``capex`` are absolute dollar figures, not
    per-share, so they are never split-adjusted.
```

- [ ] **Step 4: Load them in `load_inputs`**

In `load_inputs`, extend the `financials_annual` SELECT column list with the two
columns, and pass them when constructing each `FYRecord`. The SELECT currently reads
`"SELECT fa.fiscal_year, fa.period_end, fa.net_income, fa.total_equity, "` — add
`fa.depreciation_amortization, fa.capex, ` to it, and in the `FYRecord(...)` call add:

```python
                depreciation_amortization=r["depreciation_amortization"],
                capex=r["capex"],
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_valuation_inputs.py -v`
Expected: all pass (existing tests + 2 new)

- [ ] **Step 6: Gates and commit**

Run: `ruff check src tests && mypy && python -m pytest -q`
Expected: clean; 776 passed.

```bash
git add src/valuation/inputs.py tests/test_valuation_inputs.py
git commit -m "feat(valuation): carry D&A and capex into ValuationInputs"
```

---

### Task 2: Owner-earnings model

**Files:**
- Modify: `src/valuation/assumptions.py` (four new constants)
- Modify: `src/valuation/models.py` (append the model)
- Test: `tests/test_valuation_models.py` (append)

**Interfaces:**
- Consumes: `FYRecord.depreciation_amortization` / `.capex` (Task 1); existing `dcf_per_share`, `derive_growth`, `growth_scenarios`, `_na`, `DCF_SECTORS`, `TERMINAL_GROWTH`, `GROWTH_CAP` from the same package.
- Produces (used by Tasks 3-6):
  - Constants in `assumptions.py`: `OE_DISCOUNT_FLOOR = 0.07`, `MARGIN_OF_SAFETY = 0.30`, `OE_MIN_POSITIVE_YEARS = 8`, `OE_HISTORY_WINDOW = 10`.
  - `owner_earnings(rec: FYRecord) -> Optional[float]` — `net_income + D&A - min(D&A, abs(capex))`; None when net_income or D&A is missing.
  - `value_owner_earnings(inputs: ValuationInputs) -> ValuationResult` with `model="owner_earnings"`.
  - Assumptions dict keys: everything from `derive_growth`'s meta, plus `owner_earnings_basis`, `maintenance_capex`, `growth_capex_added_back`, `discount_base`, `discount_floor`, `risk_free_rate`, `rf_fallback` (only when the rate was missing), `beta_used` (always `False`), `sbc_added_back` (always `False`), `margin_of_safety`, `buy_below`, `positive_years`, `terminal_growth`, `shares_outstanding`, `shares_source`, `history_truncated`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_valuation_models.py`)**

```python
from src.valuation.assumptions import MARGIN_OF_SAFETY, OE_DISCOUNT_FLOOR
from src.valuation.models import owner_earnings, value_owner_earnings


def _oe_fy(fy, ni=200.0, da=50.0, capex=-60.0, shares=100.0):
    """A fiscal year with the cash-flow figures owner earnings needs."""
    rec = _fy(fy, net_income=ni, equity=1000.0, eps=2.0, shares=shares)
    rec.depreciation_amortization = da
    rec.capex = capex
    return rec


def test_owner_earnings_adds_back_growth_capex():
    # capex 60 exceeds D&A 50 -> maintenance is 50, the other 10 is growth spend
    # owner earnings = 200 + 50 - 50 = 200   (plain FCF would be 200 + 50 - 60 = 190)
    assert owner_earnings(_oe_fy(2023, ni=200.0, da=50.0, capex=-60.0)) == 200.0


def test_owner_earnings_caps_maintenance_at_actual_capex():
    # capex 30 is BELOW D&A 50 -> you cannot spend more on maintenance than you spent
    # owner earnings = 200 + 50 - 30 = 220
    assert owner_earnings(_oe_fy(2023, ni=200.0, da=50.0, capex=-30.0)) == 220.0


def test_owner_earnings_none_without_inputs():
    rec = _fy(2023, net_income=200.0, shares=100.0)  # no D&A, no capex
    assert owner_earnings(rec) is None


def test_value_owner_earnings_happy_path():
    recs = [_oe_fy(fy, ni=200.0 * 1.05 ** i) for i, fy in enumerate(range(2016, 2026))]
    res = value_owner_earnings(_inputs(fy_records=recs, risk_free_rate=0.045))
    assert res.applicable is True
    assert res.model == "owner_earnings"
    assert res.value_bear < res.value_base < res.value_bull
    a = res.assumptions
    assert a["discount_base"] == OE_DISCOUNT_FLOOR   # 4.5% rf floors at 7%
    assert a["beta_used"] is False
    assert a["sbc_added_back"] is False
    assert a["margin_of_safety"] == MARGIN_OF_SAFETY
    assert a["buy_below"] == pytest.approx(res.value_base * 0.70)
    assert a["positive_years"] == 10
    assert a["maintenance_capex"] == 50.0
    assert a["growth_capex_added_back"] == pytest.approx(10.0)


def test_value_owner_earnings_uses_treasury_above_the_floor():
    recs = [_oe_fy(fy) for fy in range(2016, 2026)]
    res = value_owner_earnings(_inputs(fy_records=recs, risk_free_rate=0.09))
    assert res.assumptions["discount_base"] == 0.09  # above the 7% floor -> used as-is


def test_value_owner_earnings_na_erratic_earnings():
    """The predictability gate: Buffett declines to forecast what he cannot predict."""
    recs = []
    for i, fy in enumerate(range(2016, 2026)):
        ni = 200.0 if i % 2 == 0 else -150.0  # only 5 of 10 years positive
        recs.append(_oe_fy(fy, ni=ni))
    res = value_owner_earnings(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "owner earnings too erratic to forecast"


def test_value_owner_earnings_na_wrong_sector():
    res = value_owner_earnings(_inputs(sector_class="bank"))
    assert res.applicable is False
    assert res.na_reason == "not applicable to sector 'bank'"


def test_value_owner_earnings_na_insufficient_history():
    recs = [_oe_fy(fy) for fy in (2023, 2024, 2025)]
    res = value_owner_earnings(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "insufficient history (need >= 4 fiscal years)"


def test_value_owner_earnings_na_missing_shares():
    recs = [_oe_fy(fy, shares=None) for fy in range(2016, 2026)]
    res = value_owner_earnings(_inputs(fy_records=recs, shares_outstanding=None))
    assert res.applicable is False
    assert res.na_reason == "shares outstanding unavailable"
```

Note: `_fy` returns an `FYRecord`; `_oe_fy` sets the two new attributes on it. If the
file's `_fy` helper does not accept `net_income=`/`equity=`/`eps=` keywords under those
exact names, read it and adapt the wrapper — do not change `_fy` itself.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_valuation_models.py -v -k owner_earnings`
Expected: FAIL with `ImportError: cannot import name 'owner_earnings'`

- [ ] **Step 3: Add the constants to `src/valuation/assumptions.py`**

Append to the constants block at the top of the file:

```python
#: Owner-earnings ("Buffett mode") discount floor. Buffett describes discounting
#: at the long-term government rate, but has also said he would not mechanically
#: discount at absurdly low rates when yields collapse — so the Treasury yield is
#: used, floored here. Beta plays no part.
OE_DISCOUNT_FLOOR = 0.07

#: The safety lives in the PRICE, not in a padded discount rate: buy only at a
#: discount to intrinsic value.
MARGIN_OF_SAFETY = 0.30

#: Predictability gate — owner earnings must be positive in at least this many of
#: the last OE_HISTORY_WINDOW fiscal years, or the business is refused as
#: unforecastable rather than valued with false precision.
OE_MIN_POSITIVE_YEARS = 8
OE_HISTORY_WINDOW = 10
```

- [ ] **Step 4: Append the model to `src/valuation/models.py`**

Extend the existing `.assumptions` import with `MARGIN_OF_SAFETY`, `OE_DISCOUNT_FLOOR`,
`OE_HISTORY_WINDOW`, `OE_MIN_POSITIVE_YEARS`, and the existing `.inputs` import already
provides `FYRecord`. Then append:

```python
def owner_earnings(rec: FYRecord) -> Optional[float]:
    """Buffett's owner earnings: net income + non-cash charges - MAINTENANCE capex.

    Maintenance capex is the one figure he leaves to judgment; depreciation is the
    accounting estimate of the same thing (what it costs to stand still), so we use
    ``min(D&A, |capex|)`` — capped at what the company actually spent, since you
    cannot spend more maintaining assets than you spent in total. Capex above that
    is growth spending and is NOT subtracted: the whole point of the measure is to
    stop treating expansion as a cost of standing still.

    Stock-based compensation is deliberately NOT added back. It is a real cost of
    running the business, whatever its cash character.
    """
    if rec.net_income is None or rec.depreciation_amortization is None:
        return None
    da = rec.depreciation_amortization
    capex = abs(rec.capex) if rec.capex is not None else da
    maintenance = min(da, capex)
    return rec.net_income + da - maintenance


def value_owner_earnings(inputs: ValuationInputs) -> ValuationResult:
    """Owner earnings discounted at the Treasury rate, with a margin of safety.

    Deliberately unlike ``value_dcf``: no beta, no equity-risk premium, growth capex
    credited back, and a refusal to value a business whose earnings cannot be
    forecast. Its verdict is its own (see ``engine.owner_earnings_verdict``) and it is
    excluded from the cross-model median — averaging a Treasury-discounted value with
    CAPM-discounted ones would silently drag every verdict toward "cheap".
    """
    if inputs.sector_class not in DCF_SECTORS:
        return _na("owner_earnings",
                   f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if owner_earnings(r) is not None]
    if len(recs) < 4:
        return _na("owner_earnings",
                   "insufficient history (need >= 4 fiscal years)")
    basis_fy = recs[-1].fiscal_year

    window = recs[-OE_HISTORY_WINDOW:]
    oe_hist = [owner_earnings(r) for r in window]
    positive_years = sum(1 for v in oe_hist if v is not None and v > 0)
    if positive_years < OE_MIN_POSITIVE_YEARS:
        return _na("owner_earnings", "owner earnings too erratic to forecast",
                   basis_fy=basis_fy)

    basis = statistics.median([v for v in oe_hist[-3:] if v is not None])
    if basis <= 0:
        return _na("owner_earnings",
                   "median 3-year owner earnings is not positive", basis_fy=basis_fy)

    shares = inputs.shares_outstanding
    shares_source = "market_snapshot"
    if not shares or shares <= 0:
        shares = None
        for r in reversed(recs):
            if r.shares is not None and r.shares > 0:
                shares = r.shares
                shares_source = "financials_annual"
                break
        if not shares:
            return _na("owner_earnings", "shares outstanding unavailable",
                       basis_fy=basis_fy)

    growth, gmeta = derive_growth(
        oe_hist, inputs.analyst_growth,
        periods=[r.fiscal_year for r in window],
    )
    g_bear, g_base, g_bull = growth_scenarios(growth)

    # Buffett's discount rate: the long-term government rate, floored. No beta.
    rf = inputs.risk_free_rate
    rf_fallback = rf is None
    if rf is None:
        rf = OE_DISCOUNT_FLOOR
    discount = max(rf, OE_DISCOUNT_FLOOR)

    # The discount rate is held FIXED across scenarios: under this method it is an
    # observable market fact, not a risk knob. Only growth varies.
    latest = recs[-1]
    da = latest.depreciation_amortization or 0.0
    latest_capex = abs(latest.capex) if latest.capex is not None else da
    maintenance = min(da, latest_capex)

    value_base = dcf_per_share(basis, shares, g_base, discount)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update({
        "owner_earnings_basis": basis,
        "maintenance_capex": maintenance,
        "growth_capex_added_back": latest_capex - maintenance,
        "discount_base": discount,
        "discount_floor": OE_DISCOUNT_FLOOR,
        "risk_free_rate": rf,
        "beta_used": False,
        "sbc_added_back": False,
        "margin_of_safety": MARGIN_OF_SAFETY,
        "buy_below": value_base * (1.0 - MARGIN_OF_SAFETY),
        "positive_years": positive_years,
        "history_truncated": inputs.history_truncated,
        "terminal_growth": TERMINAL_GROWTH,
        "shares_outstanding": shares,
        "shares_source": shares_source,
    })
    if rf_fallback:
        assumptions["rf_fallback"] = True
    return ValuationResult(
        model="owner_earnings",
        applicable=True,
        value_bear=dcf_per_share(basis, shares, g_bear, discount),
        value_base=value_base,
        value_bull=dcf_per_share(basis, shares, g_bull, discount),
        assumptions=assumptions,
        basis_fiscal_year=basis_fy,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_valuation_models.py -v`
Expected: all pass (existing + 9 new)

- [ ] **Step 6: Gates and commit**

Run: `ruff check src tests && mypy && python -m pytest -q`

```bash
git add src/valuation/assumptions.py src/valuation/models.py tests/test_valuation_models.py
git commit -m "feat(valuation): owner-earnings model (Buffett method)"
```

---

### Task 3: Register the model and protect the median carve-out

**Files:**
- Modify: `src/valuation/engine.py`
- Test: `tests/test_valuation_engine.py` (append)

**Interfaces:**
- Consumes: `value_owner_earnings` (Task 2), the existing `_MODEL_FNS` list, `intrinsic_summary`, `verdict`.
- Produces (used by Tasks 4-6):
  - `MEDIAN_MODELS: Tuple[str, ...] = ("dcf", "ddm", "graham", "lynch", "multiples")` — the models that feed the cross-model median. `owner_earnings` is deliberately absent.
  - `run_valuations` now returns **six** results, order `dcf, ddm, graham, lynch, multiples, owner_earnings`.
  - `intrinsic_summary` restricted to `MEDIAN_MODELS`.
  - `owner_earnings_verdict(result: Optional[ValuationResult], price: Optional[float]) -> Optional[str]` — `'cheap'` iff `price < assumptions["buy_below"]`, `'expensive'` iff `price > value_base`, else `'fair'`; `None` when the result is missing/not applicable, or price is missing/non-positive.

- [ ] **Step 1: Write the failing tests (append to `tests/test_valuation_engine.py`)**

```python
from src.valuation.engine import MEDIAN_MODELS, owner_earnings_verdict


def test_run_valuations_returns_six_models_in_order():
    results = run_valuations(_inputs())
    assert [r.model for r in results] == [
        "dcf", "ddm", "graham", "lynch", "multiples", "owner_earnings"]


def test_median_models_excludes_owner_earnings():
    assert "owner_earnings" not in MEDIAN_MODELS
    assert set(MEDIAN_MODELS) == {"dcf", "ddm", "graham", "lynch", "multiples"}


def test_owner_earnings_never_moves_the_median():
    """The load-bearing carve-out: a Treasury-discounted value must not be averaged
    into a median of CAPM-discounted ones, or every existing verdict silently drifts
    toward 'cheap'. This is the invariant a future sixth model is most likely to break.
    """
    five = [
        _res("dcf", 80.0, 100.0, 120.0),
        _res("ddm", 60.0, 90.0, 110.0),
        _res("graham", 70.0, 110.0, 130.0),
        _res("lynch", 75.0, 105.0, 125.0),
        _res("multiples", 85.0, 115.0, 135.0),
    ]
    without = intrinsic_summary(five)
    # An owner-earnings row 10x higher than everything else must change nothing.
    with_oe = intrinsic_summary(five + [_res("owner_earnings", 800.0, 1000.0, 1200.0)])
    assert with_oe == without
    assert with_oe["n_applicable"] == 5


def test_owner_earnings_verdict_margin_of_safety():
    res = ValuationResult(
        model="owner_earnings", applicable=True,
        value_bear=80.0, value_base=100.0, value_bull=120.0,
        assumptions={"buy_below": 70.0},
    )
    assert owner_earnings_verdict(res, 60.0) == "cheap"       # below the MOS threshold
    assert owner_earnings_verdict(res, 70.0) == "fair"        # at the threshold
    assert owner_earnings_verdict(res, 100.0) == "fair"       # at intrinsic value
    assert owner_earnings_verdict(res, 101.0) == "expensive"  # above intrinsic value
    assert owner_earnings_verdict(res, None) is None
    assert owner_earnings_verdict(res, 0.0) is None
    assert owner_earnings_verdict(None, 60.0) is None
    na = ValuationResult(model="owner_earnings", applicable=False, na_reason="x")
    assert owner_earnings_verdict(na, 60.0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_valuation_engine.py -v -k "six_models or median_models or never_moves or owner_earnings_verdict"`
Expected: FAIL with `ImportError: cannot import name 'MEDIAN_MODELS'`

- [ ] **Step 3: Implement in `src/valuation/engine.py`**

Extend the `.models` import with `value_owner_earnings`. Add `owner_earnings` to
`_MODEL_FNS` (last), and add the `MEDIAN_MODELS` constant directly beneath it:

```python
_MODEL_FNS: List[Tuple[str, Callable[[ValuationInputs], ValuationResult]]] = [
    ("dcf", value_dcf),
    ("ddm", value_ddm),
    ("graham", value_graham),
    ("lynch", value_lynch),
    ("multiples", value_multiples),
    ("owner_earnings", value_owner_earnings),
]

#: The models whose values are averaged into the cross-model median that drives the
#: headline verdict, the upside column and the screener.
#:
#: ``owner_earnings`` is deliberately ABSENT. It discounts at the Treasury rate while
#: the others discount at a CAPM cost of equity; averaging the two averages two
#: incompatible views of risk, and because the Treasury-discounted value is roughly
#: twice as large it would silently drag every verdict toward "cheap" without the
#: reader understanding why. It carries its own verdict instead
#: (``owner_earnings_verdict``). A test enforces this.
MEDIAN_MODELS: Tuple[str, ...] = ("dcf", "ddm", "graham", "lynch", "multiples")
```

Restrict `intrinsic_summary` to those models — change its first line from
`app = [r for r in results if r.applicable]` to:

```python
    app = [r for r in results if r.applicable and r.model in MEDIAN_MODELS]
```

Update its docstring to `"""Cross-model medians over the applicable MEDIAN_MODELS."""`.
Then append:

```python
def owner_earnings_verdict(result: Optional[ValuationResult],
                           price: Optional[float]) -> Optional[str]:
    """Where the price sits against owner-earnings intrinsic value.

    Unlike the shared ``verdict``, the safety margin is applied to the PRICE: a
    business is only "cheap" when it trades below intrinsic value by the margin of
    safety, which is where Buffett puts the protection rather than in a padded
    discount rate.
    """
    if result is None or not result.applicable or result.value_base is None:
        return None
    if price is None or price <= 0:
        return None
    buy_below = result.assumptions.get("buy_below")
    if buy_below is None:
        return None
    if price < buy_below:
        return "cheap"
    if price > result.value_base:
        return "expensive"
    return "fair"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_valuation_engine.py tests/test_valuation_store.py -v`
Expected: all pass. `test_compute_and_store_writes_five_model_rows_and_summary` in
`tests/test_valuation_store.py` asserts the model list and a summary `n_applicable`;
it now legitimately sees SIX rows. Update that test: its `assert [r["model"] for r in
rows] == [...]` must become the six-model list **in alphabetical order** (the query
orders by `model`: `dcf, ddm, graham, lynch, multiples, owner_earnings`), and its
`assert count == 5` (idempotency test) becomes `== 6`. Its `summary["n_applicable"]`
assertion must NOT change — that is the carve-out working, and if it changes, the
carve-out is broken.

- [ ] **Step 5: Gates and commit**

Run: `ruff check src tests && mypy && python -m pytest -q`

```bash
git add src/valuation/engine.py tests/test_valuation_engine.py tests/test_valuation_store.py
git commit -m "feat(valuation): register owner-earnings, excluded from the cross-model median"
```

---

### Task 4: API — owner-earnings verdict in the valuation payload

**Files:**
- Modify: `src/webapp/routes/stocks_api.py` (the `valuation` endpoint)
- Test: `tests/test_web_api_valuation.py` (append)

**Interfaces:**
- Consumes: `owner_earnings_verdict` (Task 3), the existing `Reader.valuations`.
- Produces (used by Task 5): `GET /api/stocks/{ticker}/valuation` gains two top-level keys — `owner_earnings_verdict` (`'cheap'|'fair'|'expensive'|None`) and `owner_earnings_verdict_label` (from the existing `VERDICT_LABELS`). The `models` list already carries the `owner_earnings` row automatically.

- [ ] **Step 1: Write the failing test (append to `tests/test_web_api_valuation.py`)**

```python
def test_valuation_endpoint_returns_owner_earnings_verdict(client, web_db):
    import json as _json
    import sqlite3 as _sqlite3
    _seed_valuations(web_db)
    conn = _sqlite3.connect(str(web_db))
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES ('AAA', 'owner_earnings', 1, NULL, "
        "200.0, 250.0, 300.0, ?, 2025, '2024-01-05T00:00:00')",
        (_json.dumps({"buy_below": 175.0, "discount_base": 0.07,
                      "beta_used": False}),),
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    body = resp.json()
    # AAA's seeded price is 105.0, well below buy_below 175 -> cheap on this method,
    # even though the five-model median says otherwise. That divergence is the point.
    assert body["owner_earnings_verdict"] == "cheap"
    assert body["owner_earnings_verdict_label"] == "Looks cheap"
    oe = next(m for m in body["models"] if m["model"] == "owner_earnings")
    assert oe["assumptions"]["beta_used"] is False


def test_valuation_endpoint_owner_earnings_absent(client):
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    assert resp.json()["owner_earnings_verdict"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_web_api_valuation.py -v -k owner_earnings`
Expected: FAIL with `KeyError: 'owner_earnings_verdict'`

- [ ] **Step 3: Implement in `src/webapp/routes/stocks_api.py`**

Extend the existing `from ...valuation.engine import ...` line with
`owner_earnings_verdict`, and import `ValuationResult`:
`from ...valuation.models import ValuationResult`.

Inside the `valuation` endpoint, after the `models` list is built and before the
`return`, add:

```python
    oe_row = next((r for r in rows if r["model"] == "owner_earnings"), None)
    oe_result = None
    if oe_row is not None:
        oe_result = ValuationResult(
            model="owner_earnings",
            applicable=bool(oe_row["applicable"]),
            value_base=oe_row.get("value_base"),
            assumptions=next(
                (m["assumptions"] for m in models if m["model"] == "owner_earnings"),
                {},
            ),
        )
    oe_v = owner_earnings_verdict(oe_result, price)
```

and add these two keys to the returned dict:

```python
        "owner_earnings_verdict": oe_v,
        "owner_earnings_verdict_label": VERDICT_LABELS[oe_v],
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_web_api_valuation.py -v`
Expected: all pass

- [ ] **Step 5: Gates and commit**

Run: `ruff check src tests && mypy && python -m pytest -q`

```bash
git add src/webapp/routes/stocks_api.py tests/test_web_api_valuation.py
git commit -m "feat(webapp): owner-earnings verdict in the valuation API payload"
```

---

### Task 5: VAL tab — Owner Earnings section

**Files:**
- Modify: `src/webapp/routes/workstation.py` (the `val_fragment` route)
- Modify: `src/webapp/templates/fragments/val.html` (append a section)
- Test: `tests/test_web_workstation.py` (append)

**Interfaces:**
- Consumes: `owner_earnings_verdict` (Task 3), `Reader.valuations`, the existing `fmt_price`/`fmt_pct`, `VERDICT_LABELS`, and the existing `.pill.verdict-*` CSS classes.
- Produces: the VAL fragment renders an "Owner Earnings (Buffett)" section below the five-model chart, or its N/A reason.

- [ ] **Step 1: Write the failing tests (append to `tests/test_web_workstation.py`)**

```python
def _seed_owner_earnings(web_db, ticker="AAA", applicable=1, na_reason=None):
    import json as _json
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(web_db))
    a = _json.dumps({
        "buy_below": 175.0, "discount_base": 0.07, "risk_free_rate": 0.045,
        "beta_used": False, "sbc_added_back": False, "margin_of_safety": 0.30,
        "maintenance_capex": 50.0, "growth_capex_added_back": 10.0,
        "positive_years": 10,
    })
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'owner_earnings', ?, ?, "
        "200.0, 250.0, 300.0, ?, 2025, '2024-01-05T00:00:00')",
        (ticker, applicable, na_reason, a),
    )
    conn.commit()
    conn.close()


def test_val_fragment_shows_owner_earnings_section(client, web_db):
    _seed_val_rows(web_db)
    _seed_owner_earnings(web_db)
    resp = client.get("/ui/stocks/AAA/val")
    assert resp.status_code == 200
    assert "Owner Earnings" in resp.text
    assert "Buy below" in resp.text          # the margin-of-safety threshold
    assert "Looks cheap" in resp.text        # price 105 < buy_below 175
    assert "growth_capex_added_back" in resp.text   # assumptions on show


def test_val_fragment_owner_earnings_na_reason_is_shown(client, web_db):
    _seed_val_rows(web_db)
    _seed_owner_earnings(web_db, applicable=0,
                         na_reason="owner earnings too erratic to forecast")
    resp = client.get("/ui/stocks/AAA/val")
    assert resp.status_code == 200
    assert "owner earnings too erratic to forecast" in resp.text


def test_val_fragment_without_owner_earnings_row_still_renders(client, web_db):
    _seed_val_rows(web_db)
    resp = client.get("/ui/stocks/AAA/val")
    assert resp.status_code == 200
    assert "Fair Value vs Price" in resp.text   # the existing five-model panel intact
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_web_workstation.py -v -k owner_earnings`
Expected: FAIL — "Owner Earnings" not in the fragment

- [ ] **Step 3: Extend `val_fragment` in `src/webapp/routes/workstation.py`**

Extend the existing `from ...valuation.engine import ...` line with
`owner_earnings_verdict`. Inside `val_fragment`, the loop that builds `applicable` /
`not_applicable` currently folds every model row in. Owner earnings must be pulled OUT
of those two lists so it does not appear in the five-model chart or table — it is a
separate lens, not a sixth bar. After that loop, add:

```python
    oe = next((e for e in applicable + not_applicable
               if e["model"] == "owner_earnings"), None)
    applicable = [e for e in applicable if e["model"] != "owner_earnings"]
    not_applicable = [e for e in not_applicable if e["model"] != "owner_earnings"]

    oe_verdict = None
    oe_ctx = None
    if oe is not None:
        if oe["base"] is not None:
            oe_result = ValuationResult(
                model="owner_earnings", applicable=True, value_base=oe["base"],
                assumptions=oe["assumptions"],
            )
            oe_verdict = owner_earnings_verdict(oe_result, price)
        oe_ctx = {
            "na_reason": oe["na_reason"],
            "bear_fmt": oe["bear_fmt"],
            "base_fmt": oe["base_fmt"],
            "bull_fmt": oe["bull_fmt"],
            "buy_below_fmt": fmt_price(oe["assumptions"].get("buy_below")),
            "assumptions": oe["assumptions"],
            "verdict": oe_verdict,
            "verdict_label": VERDICT_LABELS[oe_verdict],
            # The method gap is the insight: same company, two philosophies.
            "dcf_base_fmt": next(
                (e["base_fmt"] for e in applicable if e["model"] == "dcf"), "—"),
        }
```

Import `ValuationResult`: extend the existing `from ...valuation.models import ...`
line (it already imports `dcf_per_share`) with `ValuationResult`.

Add `"oe": oe_ctx,` to the template context dict, and rebuild `val_cfg` AFTER the
owner-earnings row has been filtered out of `applicable` (it must not become a bar in
the chart).

- [ ] **Step 4: Append the section to `src/webapp/templates/fragments/val.html`**

Insert this immediately BEFORE the `{% if sensitivity %}` block (so it sits below the
five-model chart and table, above the DCF sensitivity grid):

```html
{% if oe %}
<section class="section">
  <h2 class="section-heading">Owner Earnings (Buffett)</h2>
  {% if oe.na_reason %}
  <div class="empty-state">Not applicable — {{ oe.na_reason }}</div>
  {% else %}
  <div class="chip-row">
    <span class="pill verdict-{{ oe.verdict or 'none' }}">{{ oe.verdict_label }}</span>
    <span class="text-muted">Intrinsic {{ oe.base_fmt }} ·
      Buy below {{ oe.buy_below_fmt }} (30% margin of safety) ·
      Price {{ price_fmt }}</span>
  </div>
  <p class="text-muted mt-2">Academic DCF {{ oe.dcf_base_fmt }} ·
    Owner earnings {{ oe.base_fmt }} · Price {{ price_fmt }} — the gap between the two
    is the method, not the business: this model credits back growth capex and discounts
    at the Treasury rate instead of a beta-derived cost of equity.</p>
  <div class="table-wrap">
    <table class="data-table">
      <thead><tr><th>Bear</th><th class="num">Base</th><th class="num">Bull</th></tr></thead>
      <tbody>
        <tr>
          <td class="num">{{ oe.bear_fmt }}</td>
          <td class="num">{{ oe.base_fmt }}</td>
          <td class="num">{{ oe.bull_fmt }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <details class="mt-2">
    <summary>Assumptions</summary>
    <table class="data-table">
      <tbody>
        {% for k, v in oe.assumptions.items() %}
        <tr><td class="row-label">{{ k }}</td><td class="num">{{ v }}</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </details>
  {% endif %}
</section>
{% endif %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_web_workstation.py tests/test_web_smoke.py -v`
Expected: all pass — the existing VAL tests must not regress (the five-model chart must
still show exactly five bars, not six).

- [ ] **Step 6: Gates and commit**

Run: `ruff check src tests && mypy && python -m pytest -q`

```bash
git add src/webapp/routes/workstation.py src/webapp/templates/fragments/val.html tests/test_web_workstation.py
git commit -m "feat(webapp): VAL tab shows the owner-earnings lens beside the academic DCF"
```

---

### Task 6: Screener — Buffett upside column and verdict filter

**Files:**
- Modify: `src/webapp/screener.py`
- Modify: `src/webapp/routes/screener_api.py` (`_annotate_verdicts`)
- Modify: `src/webapp/templates/fragments/screener_results.html`
- Modify: `src/webapp/templates/screener.html` + `src/webapp/static/screener.js`
- Test: `tests/test_web_screener.py` (append)

**Interfaces:**
- Consumes: the `valuations` table (`model='owner_earnings'`), `owner_earnings_verdict`.
- Produces: sortable `oe_upside_pct` column; `oe_verdict` filter (`cheap|fair|expensive`).

**Security invariant (do not weaken):** column names reaching SQL come ONLY from
whitelists; every user value is a bound `?` parameter; the verdict filter validates
against an allow-list and then selects a FIXED clause.

- [ ] **Step 1: Write the failing tests (append to `tests/test_web_screener.py`)**

```python
def _seed_oe(web_db, ticker, base, buy_below):
    import json as _json
    conn = sqlite3.connect(str(web_db))
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS valuations ("
        "ticker TEXT NOT NULL, model TEXT NOT NULL, applicable INTEGER NOT NULL, "
        "na_reason TEXT, value_bear REAL, value_base REAL, value_bull REAL, "
        "assumptions TEXT, basis_fiscal_year INTEGER, computed_at TEXT NOT NULL, "
        "PRIMARY KEY (ticker, model));"
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'owner_earnings', 1, NULL, "
        "?, ?, ?, ?, 2025, '2024-01-05T00:00:00')",
        (ticker, base * 0.8, base, base * 1.2,
         _json.dumps({"buy_below": buy_below})),
    )
    conn.commit()
    conn.close()


def test_screen_returns_owner_earnings_upside(client, web_db):
    _seed_oe(web_db, "AAA", base=200.0, buy_below=140.0)
    resp = client.get("/api/screen", params={"limit": 10})
    assert resp.status_code == 200
    aaa = next(i for i in resp.json()["items"] if i["ticker"] == "AAA")
    assert aaa["oe_upside_pct"] is not None   # price 105 vs base 200 -> ~+90%


def test_screen_sort_by_owner_earnings_upside(client, web_db):
    _seed_oe(web_db, "AAA", base=200.0, buy_below=140.0)
    resp = client.get("/api/screen", params={"sort": "oe_upside_pct",
                                             "sort_dir": "desc", "limit": 10})
    assert resp.status_code == 200


def test_screen_owner_earnings_verdict_filter(client, web_db):
    # price 105 < buy_below 140 -> cheap on the Buffett method
    _seed_oe(web_db, "AAA", base=200.0, buy_below=140.0)
    resp = client.get("/api/screen", params={"oe_verdict": "cheap", "limit": 10})
    assert resp.status_code == 200
    assert "AAA" in [i["ticker"] for i in resp.json()["items"]]
    resp = client.get("/api/screen", params={"oe_verdict": "expensive", "limit": 10})
    assert "AAA" not in [i["ticker"] for i in resp.json()["items"]]


def test_screen_owner_earnings_verdict_invalid_400(client):
    resp = client.get("/api/screen", params={"oe_verdict": "bogus"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_web_screener.py -v -k "owner_earnings or oe_"`
Expected: FAIL with `KeyError: 'oe_upside_pct'`

- [ ] **Step 3: Extend `src/webapp/screener.py`**

1. Add the join constant next to `_VALUATION_JOIN`:

```python
# Owner-earnings row per ticker. Its own LEFT JOIN so an unvalued ticker still appears.
_OWNER_EARNINGS_JOIN: str = (
    "LEFT JOIN valuations oe ON oe.ticker = ma.ticker "
    "AND oe.model = 'owner_earnings' AND oe.applicable = 1"
)
```

2. Add to `VALUATION_EXPRS`:

```python
    "oe_upside_pct":
        '((oe."value_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
```

3. Add `"oe_upside_pct": "pct"` to `METRIC_KINDS`, and extend
`VALUATION_SELECT_COLUMNS` handling: add a separate list so the raw base value and the
JSON assumptions come back for the verdict annotation:

```python
OWNER_EARNINGS_SELECT: List[str] = ["value_base", "assumptions"]
```

Select them aliased, in `build_screen_query`'s `select_sql`:

```python
    oe_cols_sql = 'oe."value_base" AS oe_base, oe."assumptions" AS oe_assumptions'
```

and append `, {oe_cols_sql}` to the SELECT list. Extend `SCREEN_COLUMNS` with
`["oe_base", "oe_assumptions", "oe_upside_pct"]`.

4. Add `ALLOWED_VERDICTS`-style validation for the new filter. Add
`verdict_oe: Optional[str] = None` to `ScreenSpec`. In `_build_where`, after the
existing verdict block:

```python
    if spec.verdict_oe is not None:
        if spec.verdict_oe not in ALLOWED_VERDICTS:
            raise ValueError(
                f"Invalid oe_verdict {spec.verdict_oe!r}: must be one of "
                f"{list(ALLOWED_VERDICTS)}."
            )
        price = 'ms."current_price"'
        # buy_below lives in the assumptions JSON; SQLite's json_extract reads it.
        buy_below = 'json_extract(oe."assumptions", \'$.buy_below\')'
        if spec.verdict_oe == "cheap":
            clauses.append(f'{price} > 0 AND {price} < {buy_below}')
        elif spec.verdict_oe == "expensive":
            clauses.append(f'{price} > 0 AND {price} > oe."value_base"')
        else:
            clauses.append(
                f'{price} > 0 AND {price} >= {buy_below} '
                f'AND {price} <= oe."value_base"'
            )
```

5. Add `_OWNER_EARNINGS_JOIN` to **both** `build_screen_query` and
`build_count_query` (the filter references `oe` in WHERE — a missing join in the count
query is a runtime error).

6. In `parse_screen_params`: add `"oe_verdict"` to `RESERVED`, parse
`verdict_oe = params.get("oe_verdict") or None`, validate it against
`ALLOWED_VERDICTS` early with the same message, and pass `verdict_oe=verdict_oe` to
the returned `ScreenSpec`.

- [ ] **Step 4: Annotate the verdict and render the column**

In `src/webapp/routes/screener_api.py`, extend `_annotate_verdicts` to also compute
the owner-earnings verdict from the row's `oe_base` and `oe_assumptions` JSON:

```python
def _annotate_verdicts(items: List[Dict[str, Any]]) -> None:
    for row in items:
        row["val_verdict"] = valuation_verdict(
            row.get("median_bear"), row.get("median_bull"), row.get("current_price"),
        )
        oe_base = row.get("oe_base")
        price = row.get("current_price")
        buy_below = None
        try:
            buy_below = json.loads(row.get("oe_assumptions") or "{}").get("buy_below")
        except ValueError:
            buy_below = None
        oe_v = None
        if oe_base is not None and price and price > 0 and buy_below is not None:
            if price < buy_below:
                oe_v = "cheap"
            elif price > oe_base:
                oe_v = "expensive"
            else:
                oe_v = "fair"
        row["oe_verdict"] = oe_v
```

Add `import json` to that module if absent.

In `screener_results.html`, extend `sortable_cols` with `('oe_upside_pct', 'Buffett upside %')`
and add the row cell after the existing `val_verdict` cell:

```html
        <td>{{ fmt_pct(row.get("oe_upside_pct")) }}</td>
```

(The header comes from the `sortable_cols` loop, so header and body counts stay equal.)

In `screener.html`, add a second select beside the existing `#verdict-select`:

```html
    <select id="oe-verdict-select" class="form-control form-control-sm">
      <option value="">Any owner-earnings verdict</option>
      <option value="cheap">Buffett: cheap</option>
      <option value="fair">Buffett: fair</option>
      <option value="expensive">Buffett: expensive</option>
    </select>
```

In `src/webapp/static/screener.js`, wire `#oe-verdict-select` exactly the way
`#verdict-select` is wired: grep the file for every occurrence of `verdict` (state
defaults, `parseQS`, `buildQS`, saved-screen load, change handler, init-from-URL) and
add the parallel `oeVerdict` handling at each site, using the URL param key
`oe_verdict`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_web_screener.py tests/test_web_export.py -v && node --check src/webapp/static/screener.js`
Expected: all pass — including the pre-existing whitelist-disjointness and
sort-validation tests, and the CSV export (which uses `SCREEN_COLUMNS`).

- [ ] **Step 6: Gates and commit**

Run: `ruff check src tests && mypy && python -m pytest -q`

```bash
git add src/webapp/screener.py src/webapp/routes/screener_api.py src/webapp/templates/fragments/screener_results.html src/webapp/templates/screener.html src/webapp/static/screener.js tests/test_web_screener.py
git commit -m "feat(webapp): screener Buffett upside column and owner-earnings verdict filter"
```

---

### Task 7: Live verification and docs

**Files:**
- Modify: `README.md`, `USAGE_GUIDE.md`

- [ ] **Step 1: Backfill against the real database and check the spec's three claims**

The worktree's `data/output/stock.db` holds the real 50-ticker collection (gitignored;
safe to rewrite — never touch a DB outside the worktree).

Run: `python -m src.valuation.backfill`

Then verify each of the spec's verification criteria with SQL/Python and report the
actual numbers:

1. **Cyclicals are refused.** SLB and COP must carry
   `owner earnings too erratic to forecast` (their earnings swing through losses).
2. **Growth investors are revalued upward.** GOOGL and MSFT must show a materially
   higher `owner_earnings` base than their `dcf` base — their growth capex is no
   longer subtracted.
3. **The carve-out holds.** The existing five-model verdicts must be **unchanged**.
   Capture `valuation_summary` (ticker, n_applicable, median_base) BEFORE the backfill
   into a temp table or dict, re-run the backfill, and assert every row is identical.
   `n_applicable` must still max out at 5, never 6.

- [ ] **Step 2: Update the docs**

README: add owner earnings to the valuation model table (sector: general/utility/energy;
basis: net income + D&A − maintenance capex; discount: Treasury floored at 7%, no beta;
30% margin of safety; refuses erratic businesses) and note it is a separate lens, not
part of the headline verdict.

USAGE_GUIDE: document the VAL tab's Owner Earnings section, the `Buffett upside %`
screener column and its verdict filter, and state plainly why the two verdicts can
disagree (different discount philosophies — the gap is the method, not the business).

- [ ] **Step 3: Full gates and commit**

Run: `ruff check src tests && mypy && python -m pytest -q`

```bash
git add README.md USAGE_GUIDE.md
git commit -m "docs: owner-earnings (Buffett) lens"
```

---

## Self-Review Notes (already applied)

- **Spec coverage:** owner-earnings formula + maintenance-capex cap (T2), SBC not added back (T2, `sbc_added_back: false`), Treasury discount with 7% floor and no beta (T2), fixed discount across scenarios (T2), margin of safety on price (T2/T3), predictability gate (T2), all five N/A strings (T2), median carve-out + its guard test (T3), no schema change (T3 reuses `valuations`), API field (T4), VAL section with the method-gap line (T5), screener column + filter (T6), live verification of the three spec claims (T7).
- **Data dependency:** the model needs `depreciation_amortization` and `capex`, which are collected but were not previously loaded into `ValuationInputs` — hence Task 1. D&A is present for 48/50 tickers; the two without it get `insufficient history` honestly.
- **Type consistency:** `owner_earnings(rec)` and `value_owner_earnings(inputs)` signatures match across T2/T3; `MEDIAN_MODELS` is a `Tuple[str, ...]` used identically in T3's engine and its test; `owner_earnings_verdict(result, price)` has the same signature in T3, T4 and T5; the assumptions key `buy_below` is written in T2 and read in T3/T4/T5/T6.
- **Ordering trap flagged in T3:** `Reader.valuations` orders by `model`, so the six rows come back alphabetically (`dcf, ddm, graham, lynch, multiples, owner_earnings`) — the existing store test asserts that list and must be updated, but its `n_applicable` assertion must NOT change, since that is the carve-out under test.
