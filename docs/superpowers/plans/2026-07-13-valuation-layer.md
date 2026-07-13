# Valuation Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automated, sector-aware fair-value estimates (bear/base/bull + verdict) computed from stored fundamentals, persisted in SQLite, and surfaced in the workstation VAL tab, the DES overview, and the screener.

**Architecture:** A pure package `src/valuation/` (mirrors `parsers/calculated_metrics.py`: pure functions, no I/O in the models) computes five models per ticker from a `ValuationInputs` bundle loaded from SQLite. Results are stored in two new tables (`valuations` per model, `valuation_summary` per ticker medians) at collection/backfill time; the webapp compares stored intrinsic values against the latest price at read time.

**Tech Stack:** Python 3.9-compatible stdlib (sqlite3, statistics, dataclasses), FastAPI + Jinja2 + htmx webapp, Plotly via existing `slateLayout` helpers, pytest.

**Spec:** `docs/superpowers/specs/2026-07-13-valuation-layer-design.md` — read it before starting any task.

## Global Constraints

- Python 3.9 compatibility: no `X | Y` union syntax, no `match`; use `typing.Optional/List/Dict/Tuple`.
- `ruff check src tests` and `mypy src` must pass after every task (run both before each commit).
- All rates are **decimals** (0.045 = 4.5%): `risk_free_rate` is stored as a decimal, yfinance `earnings_growth` is a decimal.
- Constants (copy verbatim, defined once in `src/valuation/assumptions.py`): GROWTH_CAP=0.15, DDM_GROWTH_CAP=0.10, TERMINAL_GROWTH=0.025, ERP=0.045, DISCOUNT_FLOOR=0.08, DISCOUNT_CAP=0.14, GROWTH_SPREAD=0.03, DISCOUNT_SPREAD=0.01, DEFAULT_BETA=1.0, DEFAULT_RF=0.045.
- Sector classes come from `src/mappings/sectors.py`: `general`, `bank`, `insurance`, `reit`, `utility`, `energy`. DCF and Lynch apply to `general/utility/energy` only; Graham, DDM, multiples run for every sector.
- Model keys (exact strings, used as DB values and API keys): `dcf`, `ddm`, `graham`, `lynch`, `multiples`.
- Verdict strings (exact): `cheap`, `fair`, `expensive`; UI labels "Looks cheap" / "Fairly valued" / "Looks expensive" / "Not valued".
- N/A reasons are user-facing copy — use the exact strings given in each task.
- Never abort a collection/backfill because one ticker or model fails: log and continue.
- Commit after every task (message prefixes shown per task).

---

### Task 1: ValuationInputs bundle + SQLite loader

**Files:**
- Create: `src/valuation/__init__.py` (empty, one-line docstring `"""Automated sector-aware valuation models."""`)
- Create: `src/valuation/inputs.py`
- Test: `tests/test_valuation_inputs.py`

**Interfaces:**
- Consumes: existing SQLite schema (`companies`, `financials_annual`, `metrics_annual`, `market_snapshots`, `analyst_snapshots`, `dividend_events`, `price_bars`).
- Produces (used by Tasks 3–7):
  - `FYRecord` dataclass: `fiscal_year: int`, `period_end: Optional[str]`, `net_income`, `total_equity`, `eps_diluted`, `shares`, `fcf`, `ffo_per_share` (all `Optional[float]`), with methods `eps() -> Optional[float]` (eps_diluted, else net_income/shares) and `bvps() -> Optional[float]` (total_equity/shares).
  - `ValuationInputs` dataclass: `ticker: str`, `sector_class: str`, `fy_records: List[FYRecord]` (ascending fiscal_year, at most 10), `shares_outstanding: Optional[float]`, `beta: Optional[float]`, `risk_free_rate: Optional[float]`, `analyst_growth: Optional[float]`, `dividends: List[Tuple[str, float]]` (ISO date, per-share amount, ascending), `fy_end_prices: Dict[int, float]`.
  - `load_inputs(conn: sqlite3.Connection, ticker: str) -> ValuationInputs` (requires `conn.row_factory = sqlite3.Row`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_valuation_inputs.py
"""ValuationInputs loader: builds the per-ticker bundle from SQLite."""
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.inputs import FYRecord, load_inputs


@pytest.fixture
def val_db(tmp_path):
    """Minimal DB with one general company, 5 FYs, snapshot, analyst, dividends, bars."""
    db_path = tmp_path / "val.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute(
        "INSERT INTO companies (ticker, sector_class) VALUES ('AAA', 'general')"
    )
    for i, fy in enumerate(range(2019, 2024)):
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted) "
            "VALUES ('AAA', ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", 100.0 + 10 * i, 500.0, 1.0 + 0.1 * i, 100.0),
        )
        conn.execute(
            "INSERT INTO metrics_annual (ticker, fiscal_year, free_cash_flow, "
            "levered_fcf) VALUES ('AAA', ?, ?, ?)",
            (fy, 80.0 + 5 * i, 70.0 + 5 * i),
        )
        conn.execute(
            "INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', ?, ?)",
            (f"{fy}-12-30", 20.0 + i),
        )
    conn.execute(
        "INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, "
        "beta, risk_free_rate) VALUES ('AAA', '2024-01-05T00:00:00', 100.0, 1.2, 0.043)"
    )
    conn.execute(
        "INSERT INTO analyst_snapshots (ticker, collected_at, earnings_growth) "
        "VALUES ('AAA', '2024-01-05T00:00:00', 0.08)"
    )
    conn.execute(
        "INSERT INTO dividend_events (ticker, date, amount) VALUES "
        "('AAA', '2023-03-01', 0.25), ('AAA', '2023-09-01', 0.25)"
    )
    conn.commit()
    conn.close()
    return db_path


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_load_inputs_builds_fy_records_ascending(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.ticker == "AAA"
    assert inputs.sector_class == "general"
    assert [r.fiscal_year for r in inputs.fy_records] == [2019, 2020, 2021, 2022, 2023]
    assert inputs.fy_records[-1].net_income == 140.0


def test_fcf_prefers_levered_fcf(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.fy_records[0].fcf == 70.0  # levered_fcf, not free_cash_flow


def test_market_analyst_and_dividends_loaded(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.shares_outstanding == 100.0
    assert inputs.beta == 1.2
    assert inputs.risk_free_rate == 0.043
    assert inputs.analyst_growth == 0.08
    assert inputs.dividends == [("2023-03-01", 0.25), ("2023-09-01", 0.25)]


def test_fy_end_prices_use_close_on_or_before_period_end(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.fy_end_prices[2019] == 20.0
    assert inputs.fy_end_prices[2023] == 24.0


def test_missing_company_defaults_sector_general(val_db):
    conn = _connect(val_db)
    conn.execute("UPDATE companies SET sector_class = NULL WHERE ticker = 'AAA'")
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.sector_class == "general"


def test_fyrecord_eps_and_bvps_fallbacks():
    rec = FYRecord(fiscal_year=2023, period_end=None, net_income=100.0,
                   total_equity=500.0, eps_diluted=None, shares=50.0,
                   fcf=None, ffo_per_share=None)
    assert rec.eps() == 2.0
    assert rec.bvps() == 10.0
    rec2 = FYRecord(fiscal_year=2023, period_end=None, net_income=None,
                    total_equity=None, eps_diluted=1.5, shares=None,
                    fcf=None, ffo_per_share=None)
    assert rec2.eps() == 1.5
    assert rec2.bvps() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_inputs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.valuation'`

- [ ] **Step 3: Implement `src/valuation/inputs.py`**

```python
"""ValuationInputs: the per-ticker input bundle for the valuation models.

Pure-read composition over the existing SQLite schema. ``load_inputs`` gathers
everything a model needs — annual fundamentals joined with calculated metrics,
the latest market snapshot (beta / risk-free / shares), the latest analyst
growth estimate, the full dividend history, and each fiscal year's on-or-before
period-end closing price — so the models themselves never touch the database.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_MAX_FY_HISTORY = 10


@dataclass
class FYRecord:
    """One fiscal year of fundamentals relevant to valuation."""

    fiscal_year: int
    period_end: Optional[str]
    net_income: Optional[float]
    total_equity: Optional[float]
    eps_diluted: Optional[float]
    shares: Optional[float]
    fcf: Optional[float]
    ffo_per_share: Optional[float]

    def eps(self) -> Optional[float]:
        """Diluted EPS as reported, else net income / shares."""
        if self.eps_diluted is not None:
            return self.eps_diluted
        if self.net_income is not None and self.shares:
            return self.net_income / self.shares
        return None

    def bvps(self) -> Optional[float]:
        """Book value (total equity) per share."""
        if self.total_equity is not None and self.shares:
            return self.total_equity / self.shares
        return None


@dataclass
class ValuationInputs:
    """Everything the model suite needs for one ticker."""

    ticker: str
    sector_class: str = "general"
    fy_records: List[FYRecord] = field(default_factory=list)
    shares_outstanding: Optional[float] = None
    beta: Optional[float] = None
    risk_free_rate: Optional[float] = None
    analyst_growth: Optional[float] = None
    dividends: List[Tuple[str, float]] = field(default_factory=list)
    fy_end_prices: Dict[int, float] = field(default_factory=dict)


def load_inputs(conn: sqlite3.Connection, ticker: str) -> ValuationInputs:
    """Build the ValuationInputs bundle for *ticker*.

    ``conn.row_factory`` must be ``sqlite3.Row``. Missing rows degrade to
    ``None``/empty fields — applicability is the models' concern, not the loader's.
    """
    row = conn.execute(
        "SELECT sector_class FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()
    sector = (row["sector_class"] if row is not None else None) or "general"

    fy_rows = conn.execute(
        "SELECT fa.fiscal_year, fa.period_end, fa.net_income, fa.total_equity, "
        "fa.eps_diluted, fa.weighted_avg_shares_diluted, fa.shares_outstanding, "
        "ma.levered_fcf, ma.free_cash_flow, ma.ffo_per_share "
        "FROM financials_annual fa "
        "LEFT JOIN metrics_annual ma "
        "  ON ma.ticker = fa.ticker AND ma.fiscal_year = fa.fiscal_year "
        "WHERE fa.ticker = ? AND fa.fiscal_year IS NOT NULL "
        "ORDER BY fa.fiscal_year ASC",
        (ticker,),
    ).fetchall()
    records: List[FYRecord] = []
    for r in fy_rows[-_MAX_FY_HISTORY:]:
        fcf = r["levered_fcf"] if r["levered_fcf"] is not None else r["free_cash_flow"]
        shares = (
            r["weighted_avg_shares_diluted"]
            if r["weighted_avg_shares_diluted"] is not None
            else r["shares_outstanding"]
        )
        records.append(
            FYRecord(
                fiscal_year=int(r["fiscal_year"]),
                period_end=r["period_end"],
                net_income=r["net_income"],
                total_equity=r["total_equity"],
                eps_diluted=r["eps_diluted"],
                shares=shares,
                fcf=fcf,
                ffo_per_share=r["ffo_per_share"],
            )
        )

    snap = conn.execute(
        "SELECT shares_outstanding, beta, risk_free_rate FROM market_snapshots "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    analyst = conn.execute(
        "SELECT earnings_growth FROM analyst_snapshots "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    dividends = [
        (r["date"], r["amount"])
        for r in conn.execute(
            "SELECT date, amount FROM dividend_events "
            "WHERE ticker = ? AND amount IS NOT NULL ORDER BY date ASC",
            (ticker,),
        ).fetchall()
    ]

    fy_end_prices: Dict[int, float] = {}
    for rec in records:
        if rec.period_end is None:
            continue
        bar = conn.execute(
            "SELECT close FROM price_bars WHERE ticker = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (ticker, rec.period_end),
        ).fetchone()
        if bar is not None and bar["close"] is not None:
            fy_end_prices[rec.fiscal_year] = bar["close"]

    return ValuationInputs(
        ticker=ticker,
        sector_class=sector,
        fy_records=records,
        shares_outstanding=snap["shares_outstanding"] if snap else None,
        beta=snap["beta"] if snap else None,
        risk_free_rate=snap["risk_free_rate"] if snap else None,
        analyst_growth=analyst["earnings_growth"] if analyst else None,
        dividends=dividends,
        fy_end_prices=fy_end_prices,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_inputs.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`
Expected: clean.

```bash
git add src/valuation tests/test_valuation_inputs.py
git commit -m "feat(valuation): ValuationInputs bundle + SQLite loader"
```

---

### Task 2: Conservative assumption derivation

**Files:**
- Create: `src/valuation/assumptions.py`
- Test: `tests/test_valuation_assumptions.py`

**Interfaces:**
- Produces (used by Tasks 3–6):
  - Constants: `GROWTH_CAP = 0.15`, `DDM_GROWTH_CAP = 0.10`, `TERMINAL_GROWTH = 0.025`, `ERP = 0.045`, `DISCOUNT_FLOOR = 0.08`, `DISCOUNT_CAP = 0.14`, `GROWTH_SPREAD = 0.03`, `DISCOUNT_SPREAD = 0.01`, `DEFAULT_BETA = 1.0`, `DEFAULT_RF = 0.045`.
  - `historical_cagr(values: Sequence[Optional[float]]) -> Optional[float]` — None unless ≥2 values and both endpoints positive.
  - `derive_growth(history: Sequence[Optional[float]], analyst_growth: Optional[float], *, cap: float = GROWTH_CAP) -> Tuple[float, Dict[str, Any]]` — base growth (clamped to `[0, cap]`) + assumptions meta. Callers enforce their own minimum-history N/A rules *before* calling.
  - `derive_discount(risk_free: Optional[float], beta: Optional[float]) -> Tuple[float, Dict[str, Any]]` — CAPM, clamped `[DISCOUNT_FLOOR, DISCOUNT_CAP]`, fallbacks flagged.
  - `growth_scenarios(base: float, *, cap: float = GROWTH_CAP) -> Tuple[float, float, float]` — (bear, base, bull).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_valuation_assumptions.py
"""Conservative-rule assumption derivation: clamps, min-of, fallback flags."""
import pytest

from src.valuation.assumptions import (
    DEFAULT_BETA,
    DEFAULT_RF,
    derive_discount,
    derive_growth,
    growth_scenarios,
    historical_cagr,
)


def test_historical_cagr_basic():
    # 100 -> 121 over 2 periods = 10% CAGR
    assert historical_cagr([100.0, 105.0, 121.0]) == pytest.approx(0.10)


def test_historical_cagr_rejects_nonpositive_endpoints_and_short_series():
    assert historical_cagr([100.0]) is None
    assert historical_cagr([-5.0, 100.0]) is None
    assert historical_cagr([100.0, 0.0]) is None
    assert historical_cagr([100.0, None]) is None


def test_derive_growth_takes_min_of_hist_and_analyst():
    base, meta = derive_growth([100.0, 105.0, 121.0], 0.05)
    assert base == pytest.approx(0.05)
    assert meta["growth_source"] == "min(hist,analyst)"
    assert meta["hist_cagr"] == pytest.approx(0.10)
    assert meta["analyst_growth"] == 0.05


def test_derive_growth_clamps_to_cap_and_floor():
    base, _ = derive_growth([100.0, 400.0], None)  # 300% growth
    assert base == 0.15
    base, _ = derive_growth([100.0, 50.0], None)  # negative CAGR -> floor 0
    assert base == 0.0


def test_derive_growth_sources():
    _, meta = derive_growth([100.0, 110.0], None)
    assert meta["growth_source"] == "hist_only"
    _, meta = derive_growth([-1.0, 5.0], 0.07)  # cagr None -> analyst only
    assert meta["growth_source"] == "analyst_only"
    base, meta = derive_growth([-1.0, 5.0], None)
    assert base == 0.0
    assert meta["growth_source"] == "none"


def test_derive_discount_capm_and_clamps():
    rate, meta = derive_discount(0.043, 1.2)
    assert rate == pytest.approx(0.043 + 1.2 * 0.045)
    assert "beta_fallback" not in meta
    rate, _ = derive_discount(0.01, 0.2)  # raw 1.9% -> floor
    assert rate == 0.08
    rate, _ = derive_discount(0.06, 3.0)  # raw 19.5% -> cap
    assert rate == 0.14


def test_derive_discount_fallbacks_flagged():
    rate, meta = derive_discount(None, None)
    assert meta["beta_fallback"] is True
    assert meta["rf_fallback"] is True
    assert meta["beta"] == DEFAULT_BETA
    assert meta["risk_free_rate"] == DEFAULT_RF
    assert rate == pytest.approx(0.09)  # 0.045 + 1.0*0.045


def test_growth_scenarios_spread_and_clamps():
    assert growth_scenarios(0.08) == (pytest.approx(0.05), 0.08, pytest.approx(0.11))
    bear, base, bull = growth_scenarios(0.01)
    assert bear == 0.0
    bear, base, bull = growth_scenarios(0.14)
    assert bull == 0.15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_assumptions.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `src.valuation.assumptions`)

- [ ] **Step 3: Implement `src/valuation/assumptions.py`**

```python
"""Conservative mechanical assumption rules shared by the valuation models.

Growth = min(historical CAGR, analyst estimate), clamped to [0, cap] — biased
toward not overpaying. Discount = CAPM cost of equity with hard floor/cap.
Every derived number and every fallback is recorded in a meta dict that the
models fold into their stored ``assumptions`` JSON (the trust anchor).
"""

from typing import Any, Dict, Optional, Sequence, Tuple

GROWTH_CAP = 0.15
DDM_GROWTH_CAP = 0.10
TERMINAL_GROWTH = 0.025
ERP = 0.045
DISCOUNT_FLOOR = 0.08
DISCOUNT_CAP = 0.14
GROWTH_SPREAD = 0.03
DISCOUNT_SPREAD = 0.01
DEFAULT_BETA = 1.0
DEFAULT_RF = 0.045


def historical_cagr(values: Sequence[Optional[float]]) -> Optional[float]:
    """CAGR from first to last value; None unless both endpoints are positive."""
    if len(values) < 2:
        return None
    first, last = values[0], values[-1]
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    years = len(values) - 1
    return (last / first) ** (1.0 / years) - 1.0


def derive_growth(
    history: Sequence[Optional[float]],
    analyst_growth: Optional[float],
    *,
    cap: float = GROWTH_CAP,
) -> Tuple[float, Dict[str, Any]]:
    """Base growth = min(hist CAGR, analyst), clamped to [0, cap].

    Callers enforce their own minimum-history applicability rules BEFORE
    calling; with no usable candidate this conservatively returns 0.0
    (flat projection) with ``growth_source: "none"``.
    """
    cagr = historical_cagr(list(history))
    candidates = [g for g in (cagr, analyst_growth) if g is not None]
    if cagr is not None and analyst_growth is not None:
        source = "min(hist,analyst)"
    elif cagr is not None:
        source = "hist_only"
    elif analyst_growth is not None:
        source = "analyst_only"
    else:
        source = "none"
    raw = min(candidates) if candidates else 0.0
    base = min(max(raw, 0.0), cap)
    meta: Dict[str, Any] = {
        "growth_base": base,
        "growth_raw": raw,
        "growth_source": source,
        "hist_cagr": cagr,
        "analyst_growth": analyst_growth,
        "growth_cap": cap,
    }
    return base, meta


def derive_discount(
    risk_free: Optional[float], beta: Optional[float]
) -> Tuple[float, Dict[str, Any]]:
    """CAPM cost of equity: rf + beta * ERP, clamped to [floor, cap]."""
    meta: Dict[str, Any] = {}
    rf = risk_free
    if rf is None:
        rf = DEFAULT_RF
        meta["rf_fallback"] = True
    b = beta
    if b is None:
        b = DEFAULT_BETA
        meta["beta_fallback"] = True
    raw = rf + b * ERP
    rate = min(max(raw, DISCOUNT_FLOOR), DISCOUNT_CAP)
    meta.update(
        {
            "risk_free_rate": rf,
            "beta": b,
            "erp": ERP,
            "discount_raw": raw,
            "discount_base": rate,
        }
    )
    return rate, meta


def growth_scenarios(
    base: float, *, cap: float = GROWTH_CAP
) -> Tuple[float, float, float]:
    """(bear, base, bull) growth: base -/+ GROWTH_SPREAD, clamped to [0, cap]."""
    bear = max(base - GROWTH_SPREAD, 0.0)
    bull = min(base + GROWTH_SPREAD, cap)
    return bear, base, bull
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_assumptions.py -v`
Expected: 8 passed

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/valuation/assumptions.py tests/test_valuation_assumptions.py
git commit -m "feat(valuation): conservative assumption derivation (growth, CAPM discount, spreads)"
```

---

### Task 3: ValuationResult + DCF model

**Files:**
- Create: `src/valuation/models.py`
- Test: `tests/test_valuation_models.py`

**Interfaces:**
- Consumes: `ValuationInputs`/`FYRecord` (Task 1), everything from `assumptions.py` (Task 2).
- Produces (used by Tasks 4–7, 8, 12):
  - `ValuationResult` dataclass: `model: str`, `applicable: bool`, `na_reason: Optional[str]`, `value_bear: Optional[float]`, `value_base: Optional[float]`, `value_bull: Optional[float]`, `assumptions: Dict[str, Any]`, `basis_fiscal_year: Optional[int]`.
  - `DCF_SECTORS = ("general", "utility", "energy")` (also used by Lynch).
  - `dcf_per_share(fcf0: float, shares: float, growth: float, discount: float, terminal_growth: float = TERMINAL_GROWTH) -> float` — 10-year two-stage (5 years at `growth`, years 6–10 linear fade to `terminal_growth`), Gordon terminal at year 10. Also reused by Task 12's sensitivity grid.
  - `value_dcf(inputs: ValuationInputs) -> ValuationResult`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_valuation_models.py
"""Valuation models: hand-checked values, N/A paths, scenario ordering."""
import pytest

from src.valuation.inputs import FYRecord, ValuationInputs
from src.valuation.models import dcf_per_share, value_dcf


def _fy(fy, fcf=None, net_income=None, equity=None, eps=None, shares=100.0,
        ffo_ps=None, period_end=None):
    return FYRecord(fiscal_year=fy, period_end=period_end, net_income=net_income,
                    total_equity=equity, eps_diluted=eps, shares=shares,
                    fcf=fcf, ffo_per_share=ffo_ps)


def _inputs(**kwargs):
    defaults = dict(ticker="AAA", sector_class="general", fy_records=[],
                    shares_outstanding=100.0, beta=1.0, risk_free_rate=0.045,
                    analyst_growth=None, dividends=[], fy_end_prices={})
    defaults.update(kwargs)
    return ValuationInputs(**defaults)


# ---- dcf_per_share: closed-form perpetuity check ----
def test_dcf_per_share_zero_growth_is_perpetuity():
    # growth = terminal = 0, discount 10%: PV of flat 100/yr forever = 1000
    v = dcf_per_share(100.0, 100.0, growth=0.0, discount=0.10, terminal_growth=0.0)
    assert v == pytest.approx(10.0, rel=1e-9)


def test_dcf_per_share_monotonic_in_growth_and_discount():
    lo = dcf_per_share(100.0, 100.0, 0.02, 0.10)
    hi = dcf_per_share(100.0, 100.0, 0.08, 0.10)
    assert hi > lo
    cheap_money = dcf_per_share(100.0, 100.0, 0.05, 0.08)
    dear_money = dcf_per_share(100.0, 100.0, 0.05, 0.12)
    assert cheap_money > dear_money


# ---- value_dcf ----
def test_value_dcf_happy_path_scenario_ordering():
    recs = [_fy(fy, fcf=100.0 * 1.05 ** i) for i, fy in enumerate(range(2019, 2024))]
    res = value_dcf(_inputs(fy_records=recs))
    assert res.applicable is True
    assert res.model == "dcf"
    assert res.basis_fiscal_year == 2023
    assert res.value_bear < res.value_base < res.value_bull
    assert res.assumptions["growth_source"] == "hist_only"
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)


def test_value_dcf_na_wrong_sector():
    res = value_dcf(_inputs(sector_class="bank"))
    assert res.applicable is False
    assert res.na_reason == "not applicable to sector 'bank'"


def test_value_dcf_na_insufficient_history():
    recs = [_fy(fy, fcf=100.0) for fy in (2021, 2022, 2023)]
    res = value_dcf(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "insufficient FCF history (need >= 4 fiscal years)"


def test_value_dcf_na_negative_fcf():
    recs = [_fy(fy, fcf=-50.0) for fy in range(2019, 2024)]
    res = value_dcf(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "median 3-year FCF is not positive"


def test_value_dcf_na_missing_shares():
    recs = [_fy(fy, fcf=100.0) for fy in range(2019, 2024)]
    res = value_dcf(_inputs(fy_records=recs, shares_outstanding=None))
    assert res.applicable is False
    assert res.na_reason == "shares outstanding unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_models.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `src.valuation.models`)

- [ ] **Step 3: Implement `src/valuation/models.py` (result type + DCF)**

```python
"""Valuation models: DCF, DDM, Graham, Lynch, historical multiples band.

Each model is a pure function ``ValuationInputs -> ValuationResult`` producing
a bear/base/bull per-share fair-value range plus the exact assumptions used.
A model that does not apply returns ``applicable=False`` with a user-facing
``na_reason`` — no number is better than a fake number.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .assumptions import (
    DISCOUNT_SPREAD,
    TERMINAL_GROWTH,
    derive_discount,
    derive_growth,
    growth_scenarios,
)
from .inputs import ValuationInputs

DCF_SECTORS = ("general", "utility", "energy")


@dataclass
class ValuationResult:
    """Outcome of one model for one ticker."""

    model: str
    applicable: bool
    na_reason: Optional[str] = None
    value_bear: Optional[float] = None
    value_base: Optional[float] = None
    value_bull: Optional[float] = None
    assumptions: Dict[str, Any] = field(default_factory=dict)
    basis_fiscal_year: Optional[int] = None


def _na(model: str, reason: str,
        basis_fy: Optional[int] = None) -> ValuationResult:
    return ValuationResult(model=model, applicable=False, na_reason=reason,
                           basis_fiscal_year=basis_fy)


def dcf_per_share(fcf0: float, shares: float, growth: float, discount: float,
                  terminal_growth: float = TERMINAL_GROWTH) -> float:
    """Two-stage 10-year DCF on equity free cash flow, per share.

    Years 1-5 grow at ``growth``; years 6-10 fade linearly to
    ``terminal_growth``; Gordon terminal value at year 10. ``discount`` must
    exceed ``terminal_growth`` (guaranteed by the DISCOUNT_FLOOR clamp).
    """
    value = 0.0
    fcf = fcf0
    for t in range(1, 11):
        rate = growth if t <= 5 else growth + (terminal_growth - growth) * (t - 5) / 5.0
        fcf *= 1.0 + rate
        value += fcf / (1.0 + discount) ** t
    terminal = fcf * (1.0 + terminal_growth) / (discount - terminal_growth)
    value += terminal / (1.0 + discount) ** 10
    return value / shares


def value_dcf(inputs: ValuationInputs) -> ValuationResult:
    """FCF DCF for operating companies (general/utility/energy)."""
    if inputs.sector_class not in DCF_SECTORS:
        return _na("dcf", f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if r.fcf is not None]
    if len(recs) < 4:
        return _na("dcf", "insufficient FCF history (need >= 4 fiscal years)")
    basis_fy = recs[-1].fiscal_year
    fcf_hist = [r.fcf for r in recs]
    basis = statistics.median([f for f in fcf_hist[-3:] if f is not None])
    if basis <= 0:
        return _na("dcf", "median 3-year FCF is not positive", basis_fy=basis_fy)
    shares = inputs.shares_outstanding
    if not shares or shares <= 0:
        return _na("dcf", "shares outstanding unavailable", basis_fy=basis_fy)

    growth, gmeta = derive_growth(fcf_hist, inputs.analyst_growth)
    discount, dmeta = derive_discount(inputs.risk_free_rate, inputs.beta)
    g_bear, g_base, g_bull = growth_scenarios(growth)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update(dmeta)
    assumptions.update({
        "fcf_basis": basis,
        "fcf_years": len(fcf_hist),
        "terminal_growth": TERMINAL_GROWTH,
        "shares_outstanding": shares,
    })
    return ValuationResult(
        model="dcf",
        applicable=True,
        value_bear=dcf_per_share(basis, shares, g_bear, discount + DISCOUNT_SPREAD),
        value_base=dcf_per_share(basis, shares, g_base, discount),
        value_bull=dcf_per_share(basis, shares, g_bull, discount - DISCOUNT_SPREAD),
        assumptions=assumptions,
        basis_fiscal_year=basis_fy,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_models.py -v`
Expected: 7 passed

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/valuation/models.py tests/test_valuation_models.py
git commit -m "feat(valuation): ValuationResult + two-stage FCF DCF model"
```

---

### Task 4: Dividend Discount model

**Files:**
- Modify: `src/valuation/models.py` (append)
- Test: `tests/test_valuation_models.py` (append)

**Interfaces:**
- Produces: `ddm_per_share(ttm_dps: float, growth: float, discount: float, terminal_growth: float = TERMINAL_GROWTH) -> float` (5 growth years + Gordon terminal), `value_ddm(inputs: ValuationInputs) -> ValuationResult`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_valuation_models.py`)**

```python
from src.valuation.models import ddm_per_share, value_ddm


def _quarterly_dividends(start_year, end_year, start_amount, growth_per_year):
    """Four equal payments per calendar year, growing annually."""
    events = []
    amount = start_amount
    for year in range(start_year, end_year + 1):
        for month in ("03", "06", "09", "12"):
            events.append((f"{year}-{month}-15", amount / 4.0))
        amount *= 1.0 + growth_per_year
    return events


def test_ddm_per_share_zero_growth_is_perpetuity():
    # growth = terminal = 0, discount 10%: 1/yr forever = 10.0
    assert ddm_per_share(1.0, 0.0, 0.10, terminal_growth=0.0) == pytest.approx(10.0)


def test_value_ddm_happy_path_for_bank():
    divs = _quarterly_dividends(2019, 2023, 1.00, 0.05)
    res = value_ddm(_inputs(sector_class="bank", dividends=divs))
    assert res.applicable is True
    assert res.model == "ddm"
    assert res.value_bear < res.value_base < res.value_bull
    # Growth clamps to DDM cap 10%, hist CAGR ~5% -> hist wins the min()
    assert res.assumptions["growth_cap"] == 0.10
    assert res.assumptions["ttm_dps"] == pytest.approx(1.00 * 1.05 ** 4)


def test_value_ddm_na_no_dividends():
    res = value_ddm(_inputs(sector_class="bank", dividends=[]))
    assert res.applicable is False
    assert res.na_reason == "no dividend history"


def test_value_ddm_na_too_short():
    divs = _quarterly_dividends(2022, 2023, 1.0, 0.0)
    res = value_ddm(_inputs(sector_class="bank", dividends=divs))
    assert res.applicable is False
    assert res.na_reason == "insufficient dividend history (need >= 3 calendar years)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_models.py -v -k ddm`
Expected: FAIL with `ImportError: cannot import name 'ddm_per_share'`

- [ ] **Step 3: Append to `src/valuation/models.py`**

Add imports at the top of the file: `from datetime import date, timedelta` and extend the `.assumptions` import with `DDM_GROWTH_CAP`. Then append:

```python
def ddm_per_share(ttm_dps: float, growth: float, discount: float,
                  terminal_growth: float = TERMINAL_GROWTH) -> float:
    """Multi-stage Gordon DDM: 5 years at ``growth``, then terminal growth."""
    value = 0.0
    d = ttm_dps
    for t in range(1, 6):
        d *= 1.0 + growth
        value += d / (1.0 + discount) ** t
    terminal = d * (1.0 + terminal_growth) / (discount - terminal_growth)
    value += terminal / (1.0 + discount) ** 5
    return value


def _annual_dividend_totals(dividends: "list[tuple[str, float]]") -> "list[tuple[int, float]]":
    totals: Dict[int, float] = {}
    for date_str, amount in dividends:
        year = int(date_str[:4])
        totals[year] = totals.get(year, 0.0) + amount
    return sorted(totals.items())


def value_ddm(inputs: ValuationInputs) -> ValuationResult:
    """Multi-stage dividend discount model — any steady payer, and the primary
    model for banks/insurers where FCF is meaningless."""
    if not inputs.dividends:
        return _na("ddm", "no dividend history")
    annual = _annual_dividend_totals(inputs.dividends)
    if len(annual) < 3:
        return _na("ddm", "insufficient dividend history (need >= 3 calendar years)")

    anchor = date.fromisoformat(inputs.dividends[-1][0][:10])
    cutoff = (anchor - timedelta(days=365)).isoformat()
    ttm = sum(a for d, a in inputs.dividends if d[:10] > cutoff)
    if ttm <= 0:
        return _na("ddm", "no dividends in trailing 12 months")

    # CAGR over complete calendar years only (the anchor year is likely partial).
    complete = [total for year, total in annual if year < anchor.year]
    growth, gmeta = derive_growth(complete, inputs.analyst_growth, cap=DDM_GROWTH_CAP)
    discount, dmeta = derive_discount(inputs.risk_free_rate, inputs.beta)
    g_bear, g_base, g_bull = growth_scenarios(growth, cap=DDM_GROWTH_CAP)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update(dmeta)
    assumptions.update({
        "ttm_dps": ttm,
        "ttm_anchor": anchor.isoformat(),
        "dividend_years": len(annual),
        "terminal_growth": TERMINAL_GROWTH,
    })
    return ValuationResult(
        model="ddm",
        applicable=True,
        value_bear=ddm_per_share(ttm, g_bear, discount + DISCOUNT_SPREAD),
        value_base=ddm_per_share(ttm, g_base, discount),
        value_bull=ddm_per_share(ttm, g_bull, discount - DISCOUNT_SPREAD),
        assumptions=assumptions,
        basis_fiscal_year=anchor.year,
    )
```

(Note: the `"list[tuple[...]]"` string annotations keep Python 3.9 happy; alternatively use `List[Tuple[str, float]]` from `typing` — prefer the `typing` form to match the rest of the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_models.py -v`
Expected: all pass (11 tests)

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/valuation/models.py tests/test_valuation_models.py
git commit -m "feat(valuation): multi-stage dividend discount model"
```

---

### Task 5: Graham Number + Peter Lynch models

**Files:**
- Modify: `src/valuation/models.py` (append)
- Test: `tests/test_valuation_models.py` (append)

**Interfaces:**
- Produces: `value_graham(inputs) -> ValuationResult`, `value_lynch(inputs) -> ValuationResult`.

- [ ] **Step 1: Write the failing tests (append)**

```python
import math

from src.valuation.models import value_graham, value_lynch


def test_value_graham_hand_computed():
    recs = [_fy(fy, net_income=None, equity=2000.0, eps=e, shares=100.0)
            for fy, e in ((2021, 2.0), (2022, 2.5), (2023, 3.0))]
    res = value_graham(_inputs(fy_records=recs))
    assert res.applicable is True
    # base: sqrt(22.5 * 3.0 * 20.0); bear uses min EPS 2.0; bull max EPS 3.0
    assert res.value_base == pytest.approx(math.sqrt(22.5 * 3.0 * 20.0))
    assert res.value_bear == pytest.approx(math.sqrt(22.5 * 2.0 * 20.0))
    assert res.value_bull == res.value_base
    assert res.basis_fiscal_year == 2023


def test_value_graham_na_negative_eps():
    recs = [_fy(2023, equity=2000.0, eps=-1.0, shares=100.0)]
    res = value_graham(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "EPS is not positive"


def test_value_graham_na_no_data():
    res = value_graham(_inputs(fy_records=[]))
    assert res.applicable is False
    assert res.na_reason == "EPS or book value unavailable"


def test_value_lynch_hand_computed():
    # EPS CAGR 100->121 over 4 yrs with values in between = 4.88%/yr;
    # analyst 12% -> min is hist. fair P/E = growth*100 clamped [5,25].
    eps_hist = [2.00, 2.10, 2.20, 2.31, 2.42]
    recs = [_fy(2019 + i, eps=e, shares=100.0) for i, e in enumerate(eps_hist)]
    res = value_lynch(_inputs(fy_records=recs, analyst_growth=0.12))
    assert res.applicable is True
    g = res.assumptions["growth_base"]
    expected_pe = min(max(g * 100.0, 5.0), 25.0)
    assert res.value_base == pytest.approx(expected_pe * 2.42)
    assert res.value_bear < res.value_base < res.value_bull


def test_value_lynch_fair_pe_floor_applies():
    recs = [_fy(2019 + i, eps=2.0, shares=100.0) for i in range(5)]  # 0% growth
    res = value_lynch(_inputs(fy_records=recs))
    assert res.applicable is True
    assert res.value_base == pytest.approx(5.0 * 2.0)  # P/E floor 5


def test_value_lynch_na_sector_and_history():
    res = value_lynch(_inputs(sector_class="reit"))
    assert res.na_reason == "not applicable to sector 'reit'"
    recs = [_fy(2023, eps=2.0, shares=100.0)]
    res = value_lynch(_inputs(fy_records=recs))
    assert res.na_reason == "insufficient EPS history (need >= 4 fiscal years)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_models.py -v -k "graham or lynch"`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Append to `src/valuation/models.py`**

Add `import math` at the top, and `GROWTH_SPREAD` to the `.assumptions` import. Append:

```python
LYNCH_PE_FLOOR = 5.0
LYNCH_PE_CAP = 25.0


def value_graham(inputs: ValuationInputs) -> ValuationResult:
    """Graham Number: sqrt(22.5 * EPS * BVPS). Bear/bull vary EPS over 3 FYs."""
    recs = [r for r in inputs.fy_records
            if r.eps() is not None and r.bvps() is not None]
    if not recs:
        return _na("graham", "EPS or book value unavailable")
    latest = recs[-1]
    eps = latest.eps()
    bvps = latest.bvps()
    assert eps is not None and bvps is not None  # narrowed by filter above
    if eps <= 0:
        return _na("graham", "EPS is not positive", basis_fy=latest.fiscal_year)
    if bvps <= 0:
        return _na("graham", "book value per share is not positive",
                   basis_fy=latest.fiscal_year)
    last3 = [e for e in (r.eps() for r in recs[-3:]) if e is not None and e > 0]
    eps_bear, eps_bull = min(last3), max(last3)
    assumptions: Dict[str, Any] = {
        "eps_base": eps, "eps_bear": eps_bear, "eps_bull": eps_bull,
        "bvps": bvps, "multiplier": 22.5,
    }
    return ValuationResult(
        model="graham",
        applicable=True,
        value_bear=math.sqrt(22.5 * eps_bear * bvps),
        value_base=math.sqrt(22.5 * eps * bvps),
        value_bull=math.sqrt(22.5 * eps_bull * bvps),
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )


def _lynch_fair_pe(growth: float) -> float:
    return min(max(growth * 100.0, LYNCH_PE_FLOOR), LYNCH_PE_CAP)


def value_lynch(inputs: ValuationInputs) -> ValuationResult:
    """Peter Lynch fair value: growth-rate-as-fair-P/E times latest EPS."""
    if inputs.sector_class not in DCF_SECTORS:
        return _na("lynch", f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if r.eps() is not None]
    if len(recs) < 4:
        return _na("lynch", "insufficient EPS history (need >= 4 fiscal years)")
    latest = recs[-1]
    eps = latest.eps()
    assert eps is not None
    if eps <= 0:
        return _na("lynch", "EPS is not positive", basis_fy=latest.fiscal_year)
    growth, gmeta = derive_growth([r.eps() for r in recs], inputs.analyst_growth)
    g_bear, g_base, g_bull = growth_scenarios(growth)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update({
        "eps_base": eps,
        "fair_pe_base": _lynch_fair_pe(g_base),
        "fair_pe_floor": LYNCH_PE_FLOOR,
        "fair_pe_cap": LYNCH_PE_CAP,
    })
    return ValuationResult(
        model="lynch",
        applicable=True,
        value_bear=_lynch_fair_pe(g_bear) * eps,
        value_base=_lynch_fair_pe(g_base) * eps,
        value_bull=_lynch_fair_pe(g_bull) * eps,
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_models.py -v`
Expected: all pass (17 tests)

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/valuation/models.py tests/test_valuation_models.py
git commit -m "feat(valuation): Graham Number and Peter Lynch fair value models"
```

---

### Task 6: Historical multiples band model

**Files:**
- Modify: `src/valuation/models.py` (append)
- Test: `tests/test_valuation_models.py` (append)

**Interfaces:**
- Produces: `value_multiples(inputs) -> ValuationResult`. Multiple by sector: P/E (`general`, `utility`, `energy` — basis `FYRecord.eps()`), P/B (`bank`, `insurance` — basis `bvps()`), P/FFO (`reit` — basis `ffo_per_share`).

- [ ] **Step 1: Write the failing tests (append)**

```python
from src.valuation.models import value_multiples


def test_value_multiples_pe_band_hand_computed():
    # FY-end P/E multiples: 10, 12, 14 -> band (10, 12, 14) * latest EPS 2.0
    recs = [_fy(2021, eps=2.0), _fy(2022, eps=2.0), _fy(2023, eps=2.0)]
    prices = {2021: 20.0, 2022: 24.0, 2023: 28.0}
    res = value_multiples(_inputs(fy_records=recs, fy_end_prices=prices))
    assert res.applicable is True
    assert res.value_bear == pytest.approx(20.0)
    assert res.value_base == pytest.approx(24.0)
    assert res.value_bull == pytest.approx(28.0)
    assert res.assumptions["multiple_kind"] == "pe"


def test_value_multiples_reit_uses_pffo():
    recs = [_fy(fy, ffo_ps=3.0) for fy in (2021, 2022, 2023)]
    prices = {2021: 30.0, 2022: 36.0, 2023: 42.0}
    res = value_multiples(_inputs(sector_class="reit", fy_records=recs,
                                  fy_end_prices=prices))
    assert res.applicable is True
    assert res.assumptions["multiple_kind"] == "pffo"
    assert res.value_base == pytest.approx(12.0 * 3.0)


def test_value_multiples_na_insufficient_history():
    recs = [_fy(2022, eps=2.0), _fy(2023, eps=2.0)]
    prices = {2022: 24.0, 2023: 28.0}
    res = value_multiples(_inputs(fy_records=recs, fy_end_prices=prices))
    assert res.applicable is False
    assert res.na_reason == "insufficient multiple history (need >= 3 fiscal years)"


def test_value_multiples_na_negative_latest_basis():
    recs = [_fy(2020, eps=2.0), _fy(2021, eps=2.0), _fy(2022, eps=2.0),
            _fy(2023, eps=-1.0)]
    prices = {2020: 20.0, 2021: 20.0, 2022: 20.0, 2023: 20.0}
    res = value_multiples(_inputs(fy_records=recs, fy_end_prices=prices))
    assert res.applicable is False
    assert res.na_reason == "latest per-share basis is not positive"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_models.py -v -k multiples`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Append to `src/valuation/models.py`**

Add `Callable, Optional` availability (already imported `Optional`; add `Callable` to the `typing` import) plus `from .inputs import FYRecord` alongside `ValuationInputs`. Append:

```python
def _multiple_basis(sector_class: str) -> "Tuple[str, Callable[[FYRecord], Optional[float]]]":
    if sector_class in ("bank", "insurance"):
        return "pb", lambda r: r.bvps()
    if sector_class == "reit":
        return "pffo", lambda r: r.ffo_per_share
    return "pe", lambda r: r.eps()


def value_multiples(inputs: ValuationInputs) -> ValuationResult:
    """Price vs the company's own 5-year band of its sector-appropriate multiple."""
    kind, basis_fn = _multiple_basis(inputs.sector_class)
    recs = inputs.fy_records[-5:]
    multiples = []
    for r in recs:
        b = basis_fn(r)
        p = inputs.fy_end_prices.get(r.fiscal_year)
        if b is not None and b > 0 and p is not None and p > 0:
            multiples.append(p / b)
    if len(multiples) < 3:
        return _na("multiples",
                   "insufficient multiple history (need >= 3 fiscal years)")
    latest = inputs.fy_records[-1]
    latest_basis = basis_fn(latest)
    if latest_basis is None or latest_basis <= 0:
        return _na("multiples", "latest per-share basis is not positive",
                   basis_fy=latest.fiscal_year)
    band_low, band_mid, band_high = (min(multiples),
                                     statistics.median(multiples),
                                     max(multiples))
    assumptions: Dict[str, Any] = {
        "multiple_kind": kind,
        "band_low": band_low, "band_median": band_mid, "band_high": band_high,
        "n_years": len(multiples),
        "latest_basis": latest_basis,
    }
    return ValuationResult(
        model="multiples",
        applicable=True,
        value_bear=band_low * latest_basis,
        value_base=band_mid * latest_basis,
        value_bull=band_high * latest_basis,
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )
```

(`Tuple` is already imported via `typing` if not, extend the import. The string annotation on `_multiple_basis` keeps line length manageable; a plain annotation is equally fine.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_models.py -v`
Expected: all pass (21 tests)

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/valuation/models.py tests/test_valuation_models.py
git commit -m "feat(valuation): historical multiples band model (P/E, P/B, P/FFO)"
```

---

### Task 7: Engine — model routing, medians, verdict

**Files:**
- Create: `src/valuation/engine.py`
- Test: `tests/test_valuation_engine.py`

**Interfaces:**
- Consumes: all five `value_*` functions, `ValuationResult`, `ValuationInputs`.
- Produces (used by Tasks 8, 9, 11, 12, 14):
  - `run_valuations(inputs: ValuationInputs, logger: Optional[logging.Logger] = None) -> List[ValuationResult]` — always returns exactly 5 results, order `dcf, ddm, graham, lynch, multiples`; a model exception becomes an N/A row `internal error: <msg>` and is logged, never raised.
  - `intrinsic_summary(results: List[ValuationResult]) -> Dict[str, Any]` — keys `n_applicable: int`, `median_bear`, `median_base`, `median_bull` (each `Optional[float]`, None when `n_applicable == 0`).
  - `verdict(median_bear: Optional[float], median_bull: Optional[float], price: Optional[float]) -> Optional[str]` — `'cheap' | 'fair' | 'expensive'`, None if any input is None or price <= 0.
  - `upside_pct(median_base: Optional[float], price: Optional[float]) -> Optional[float]` — `(base - price) / price`, None on missing/zero price.
  - `VERDICT_LABELS: Dict[Optional[str], str]` — `{"cheap": "Looks cheap", "fair": "Fairly valued", "expensive": "Looks expensive", None: "Not valued"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_valuation_engine.py
"""Engine: routing, cross-model medians, verdict rules."""
import pytest

from src.valuation.engine import (
    VERDICT_LABELS,
    intrinsic_summary,
    run_valuations,
    upside_pct,
    verdict,
)
from src.valuation.inputs import FYRecord, ValuationInputs
from src.valuation.models import ValuationResult


def _inputs(**kwargs):
    defaults = dict(ticker="AAA", sector_class="general", fy_records=[],
                    shares_outstanding=100.0, beta=1.0, risk_free_rate=0.045,
                    analyst_growth=None, dividends=[], fy_end_prices={})
    defaults.update(kwargs)
    return ValuationInputs(**defaults)


def test_run_valuations_returns_all_five_models_in_order():
    results = run_valuations(_inputs())
    assert [r.model for r in results] == ["dcf", "ddm", "graham", "lynch", "multiples"]
    # Empty inputs: every model is N/A with a reason, none raises
    assert all(r.applicable is False and r.na_reason for r in results)


def test_run_valuations_bank_marks_dcf_lynch_sector_na():
    results = {r.model: r for r in run_valuations(_inputs(sector_class="bank"))}
    assert results["dcf"].na_reason == "not applicable to sector 'bank'"
    assert results["lynch"].na_reason == "not applicable to sector 'bank'"


def _res(model, bear, base, bull):
    return ValuationResult(model=model, applicable=True, value_bear=bear,
                           value_base=base, value_bull=bull)


def test_intrinsic_summary_medians():
    results = [
        _res("dcf", 80.0, 100.0, 120.0),
        _res("graham", 60.0, 90.0, 110.0),
        _res("multiples", 70.0, 110.0, 130.0),
        ValuationResult(model="ddm", applicable=False, na_reason="x"),
    ]
    s = intrinsic_summary(results)
    assert s["n_applicable"] == 3
    assert s["median_bear"] == 70.0
    assert s["median_base"] == 100.0
    assert s["median_bull"] == 120.0


def test_intrinsic_summary_empty():
    s = intrinsic_summary([ValuationResult(model="dcf", applicable=False)])
    assert s == {"n_applicable": 0, "median_bear": None,
                 "median_base": None, "median_bull": None}


def test_verdict_rules():
    assert verdict(70.0, 120.0, 50.0) == "cheap"
    assert verdict(70.0, 120.0, 90.0) == "fair"
    assert verdict(70.0, 120.0, 70.0) == "fair"   # boundary inclusive
    assert verdict(70.0, 120.0, 121.0) == "expensive"
    assert verdict(None, 120.0, 90.0) is None
    assert verdict(70.0, 120.0, None) is None
    assert verdict(70.0, 120.0, 0.0) is None


def test_upside_pct():
    assert upside_pct(120.0, 100.0) == pytest.approx(0.20)
    assert upside_pct(None, 100.0) is None
    assert upside_pct(120.0, 0.0) is None


def test_verdict_labels_complete():
    assert VERDICT_LABELS["cheap"] == "Looks cheap"
    assert VERDICT_LABELS[None] == "Not valued"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_engine.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/valuation/engine.py`**

```python
"""Valuation engine: run every model for a ticker, summarize, verdict.

``run_valuations`` produces exactly one ``ValuationResult`` per model (five
rows) so "not applicable" is always distinguishable from "never computed".
``intrinsic_summary`` collapses the applicable models into per-ticker medians
(stored — price-independent); ``verdict``/``upside_pct`` compare those medians
to a live price at read time.
"""

import logging
import statistics
from typing import Any, Callable, Dict, List, Optional, Tuple

from .inputs import ValuationInputs
from .models import (
    ValuationResult,
    value_dcf,
    value_ddm,
    value_graham,
    value_lynch,
    value_multiples,
)

_MODEL_FNS: List[Tuple[str, Callable[[ValuationInputs], ValuationResult]]] = [
    ("dcf", value_dcf),
    ("ddm", value_ddm),
    ("graham", value_graham),
    ("lynch", value_lynch),
    ("multiples", value_multiples),
]

VERDICT_LABELS: Dict[Optional[str], str] = {
    "cheap": "Looks cheap",
    "fair": "Fairly valued",
    "expensive": "Looks expensive",
    None: "Not valued",
}


def run_valuations(inputs: ValuationInputs,
                   logger: Optional[logging.Logger] = None) -> List[ValuationResult]:
    """All five models for one ticker. A model crash becomes an N/A row."""
    log = logger or logging.getLogger(__name__)
    results: List[ValuationResult] = []
    for name, fn in _MODEL_FNS:
        try:
            results.append(fn(inputs))
        except Exception as e:  # never abort the run for one model
            log.warning(f"{inputs.ticker}: {name} valuation failed: {e}")
            results.append(ValuationResult(
                model=name, applicable=False, na_reason=f"internal error: {e}"))
    return results


def intrinsic_summary(results: List[ValuationResult]) -> Dict[str, Any]:
    """Cross-model medians of bear/base/bull over applicable models."""
    app = [r for r in results if r.applicable]
    if not app:
        return {"n_applicable": 0, "median_bear": None,
                "median_base": None, "median_bull": None}
    return {
        "n_applicable": len(app),
        "median_bear": statistics.median([r.value_bear for r in app]),
        "median_base": statistics.median([r.value_base for r in app]),
        "median_bull": statistics.median([r.value_bull for r in app]),
    }


def verdict(median_bear: Optional[float], median_bull: Optional[float],
            price: Optional[float]) -> Optional[str]:
    """Where the live price sits vs the median fair-value range."""
    if median_bear is None or median_bull is None or price is None or price <= 0:
        return None
    if price < median_bear:
        return "cheap"
    if price > median_bull:
        return "expensive"
    return "fair"


def upside_pct(median_base: Optional[float],
               price: Optional[float]) -> Optional[float]:
    """(median base fair value - price) / price."""
    if median_base is None or price is None or price <= 0:
        return None
    return (median_base - price) / price
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_engine.py -v`
Expected: 8 passed

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/valuation/engine.py tests/test_valuation_engine.py
git commit -m "feat(valuation): engine with model routing, medians, verdict"
```

---

### Task 8: Storage — valuations tables + compute_and_store

**Files:**
- Modify: `src/exporters/sqlite_store.py` (DDL in `_create_schema`, new `export_valuations` method)
- Modify: `src/valuation/engine.py` (append `compute_and_store`)
- Test: `tests/test_valuation_store.py`

**Interfaces:**
- Consumes: `SQLiteStore._create_schema`/`_upsert` (existing), `load_inputs`, `run_valuations`, `intrinsic_summary`.
- Produces (used by Tasks 9–14):
  - Tables `valuations` (PK `(ticker, model)`; columns `ticker, model, applicable INTEGER, na_reason TEXT, value_bear REAL, value_base REAL, value_bull REAL, assumptions TEXT, basis_fiscal_year INTEGER, computed_at TEXT`) and `valuation_summary` (PK `ticker`; `n_applicable INTEGER, median_bear REAL, median_base REAL, median_bull REAL, computed_at TEXT`).
  - `SQLiteStore.export_valuations(ticker: str, results: List[ValuationResult], summary: Dict[str, Any], computed_at: str) -> None` — opens its own connection, ensures schema, upserts 5 model rows + 1 summary row, commits. (Import `ValuationResult` under `TYPE_CHECKING` only, to keep `exporters` free of a runtime dependency on `valuation`; annotate with a string.)
  - `compute_and_store(db_path: Union[str, Path], tickers: Optional[List[str]] = None, logger: Optional[logging.Logger] = None) -> int` in `engine.py` — loads inputs per ticker (all `companies` tickers when None), runs models, stores; per-ticker failures are logged and skipped; returns the count of tickers successfully stored.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_valuation_store.py
"""valuations/valuation_summary persistence + compute_and_store end to end."""
import json
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.engine import compute_and_store


@pytest.fixture
def seeded_db(tmp_path):
    """One 'general' ticker with enough history for DCF/Graham/Lynch/multiples."""
    db_path = tmp_path / "store.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute(
        "INSERT INTO companies (ticker, sector_class) VALUES ('AAA', 'general')"
    )
    for i, fy in enumerate(range(2019, 2024)):
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted) "
            "VALUES ('AAA', ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", 200.0, 1000.0, 2.0 + 0.1 * i, 100.0),
        )
        conn.execute(
            "INSERT INTO metrics_annual (ticker, fiscal_year, levered_fcf) "
            "VALUES ('AAA', ?, ?)",
            (fy, 100.0 * 1.05 ** i),
        )
        conn.execute(
            "INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', ?, ?)",
            (f"{fy}-12-30", 30.0 + i),
        )
    conn.execute(
        "INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, "
        "beta, risk_free_rate, current_price) "
        "VALUES ('AAA', '2024-01-05T00:00:00', 100.0, 1.0, 0.045, 35.0)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_compute_and_store_writes_five_model_rows_and_summary(seeded_db):
    n = compute_and_store(seeded_db)
    assert n == 1
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM valuations WHERE ticker='AAA' ORDER BY model"
    ).fetchall()
    assert [r["model"] for r in rows] == ["dcf", "ddm", "graham", "lynch", "multiples"]
    by_model = {r["model"]: r for r in rows}
    assert by_model["dcf"]["applicable"] == 1
    assert by_model["dcf"]["value_bear"] < by_model["dcf"]["value_bull"]
    assumptions = json.loads(by_model["dcf"]["assumptions"])
    assert assumptions["growth_source"] == "hist_only"
    assert by_model["ddm"]["applicable"] == 0
    assert by_model["ddm"]["na_reason"] == "no dividend history"
    summary = conn.execute(
        "SELECT * FROM valuation_summary WHERE ticker='AAA'"
    ).fetchone()
    assert summary["n_applicable"] == 4  # dcf, graham, lynch, multiples
    assert summary["median_base"] is not None
    assert summary["computed_at"]
    conn.close()


def test_compute_and_store_is_idempotent_upsert(seeded_db):
    compute_and_store(seeded_db)
    compute_and_store(seeded_db)
    conn = sqlite3.connect(seeded_db)
    count = conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0]
    assert count == 5
    conn.close()


def test_compute_and_store_skips_broken_ticker(seeded_db):
    conn = sqlite3.connect(seeded_db)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('BBB', 'general')")
    conn.commit()
    conn.close()
    n = compute_and_store(seeded_db)  # BBB has no data at all -> still stored (all N/A)
    assert n == 2
    conn = sqlite3.connect(seeded_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM valuations WHERE ticker='BBB' AND applicable=0"
    ).fetchone()[0]
    assert count == 5
    conn.close()


def test_compute_and_store_explicit_ticker_list(seeded_db):
    n = compute_and_store(seeded_db, tickers=["AAA"])
    assert n == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_and_store'`

- [ ] **Step 3: Add DDL + `export_valuations` to `sqlite_store.py`**

In `_create_schema`'s `executescript`, after the `collection_runs` table, add:

```sql
            CREATE TABLE IF NOT EXISTS valuations (
                ticker TEXT NOT NULL,
                model TEXT NOT NULL,
                applicable INTEGER NOT NULL,
                na_reason TEXT,
                value_bear REAL, value_base REAL, value_bull REAL,
                assumptions TEXT,
                basis_fiscal_year INTEGER,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (ticker, model)
            );

            CREATE TABLE IF NOT EXISTS valuation_summary (
                ticker TEXT PRIMARY KEY,
                n_applicable INTEGER NOT NULL,
                median_bear REAL, median_base REAL, median_bull REAL,
                computed_at TEXT NOT NULL
            );
```

At the top of the file add (after the existing imports):

```python
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime exporters -> valuation dependency
    from ..valuation.models import ValuationResult
```

(Merge with the existing `typing` import line rather than duplicating it.) Then add the method to `SQLiteStore` (after `export_benchmark_bars`):

```python
    def export_valuations(self, ticker: str, results: "List[ValuationResult]",
                          summary: Dict[str, Any], computed_at: str) -> None:
        """Upsert one ticker's per-model valuation rows + the medians summary."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            for res in results:
                self._upsert(conn, "valuations", ["ticker", "model"], {
                    "ticker": ticker,
                    "model": res.model,
                    "applicable": 1 if res.applicable else 0,
                    "na_reason": res.na_reason,
                    "value_bear": res.value_bear,
                    "value_base": res.value_base,
                    "value_bull": res.value_bull,
                    "assumptions": json.dumps(res.assumptions),
                    "basis_fiscal_year": res.basis_fiscal_year,
                    "computed_at": computed_at,
                })
            self._upsert(conn, "valuation_summary", ["ticker"], {
                "ticker": ticker,
                "n_applicable": summary["n_applicable"],
                "median_bear": summary["median_bear"],
                "median_base": summary["median_base"],
                "median_bull": summary["median_bull"],
                "computed_at": computed_at,
            })
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Append `compute_and_store` to `src/valuation/engine.py`**

Add imports at the top: `import sqlite3`, `from datetime import datetime, timezone`, `from pathlib import Path`, extend `typing` import with `Union`, plus `from ..exporters.sqlite_store import SQLiteStore` and `from .inputs import load_inputs`. Append:

```python
def compute_and_store(db_path: "Union[str, Path]",
                      tickers: Optional[List[str]] = None,
                      logger: Optional[logging.Logger] = None) -> int:
    """Compute and persist valuations for *tickers* (default: every company).

    Per-ticker failures are logged and skipped — a bad ticker never aborts
    the batch. Returns the number of tickers successfully stored.
    """
    log = logger or logging.getLogger(__name__)
    store = SQLiteStore(db_path=Path(db_path), logger=log)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if tickers is None:
            tickers = [r["ticker"] for r in conn.execute(
                "SELECT ticker FROM companies ORDER BY ticker").fetchall()]
        computed_at = datetime.now(timezone.utc).isoformat()
        stored = 0
        for ticker in tickers:
            try:
                inputs = load_inputs(conn, ticker)
                results = run_valuations(inputs, logger=log)
                summary = intrinsic_summary(results)
                store.export_valuations(ticker, results, summary, computed_at)
                stored += 1
            except Exception as e:
                log.warning(f"Valuation failed for {ticker}: {e}")
        return stored
    finally:
        conn.close()
```

Note: `export_valuations` opening its own connection while `conn` is open is fine under WAL mode (reader + writer coexist).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_store.py tests/test_sqlite_store.py tests/test_store_migration.py -v`
Expected: all pass (existing store tests must not regress)

- [ ] **Step 6: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/exporters/sqlite_store.py src/valuation/engine.py tests/test_valuation_store.py
git commit -m "feat(valuation): persist valuations + summary tables, compute_and_store"
```

---

### Task 9: Backfill CLI

**Files:**
- Create: `src/valuation/backfill.py`
- Test: `tests/test_valuation_store.py` (append)

**Interfaces:**
- Consumes: `compute_and_store` (Task 8), `StorageConfig` from `src/config.py`.
- Produces: `python -m src.valuation.backfill [--db PATH] [tickers ...]` and `main(argv: Optional[List[str]] = None) -> int` (exit code 0 on success, 1 when nothing was stored).

- [ ] **Step 1: Write the failing tests (append to `tests/test_valuation_store.py`)**

```python
from src.valuation.backfill import main as backfill_main


def test_backfill_cli_runs_on_explicit_db(seeded_db, capsys):
    rc = backfill_main(["--db", str(seeded_db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 ticker" in out
    conn = sqlite3.connect(seeded_db)
    assert conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0] == 5
    conn.close()


def test_backfill_cli_ticker_filter(seeded_db):
    rc = backfill_main(["--db", str(seeded_db), "AAA"])
    assert rc == 0


def test_backfill_cli_empty_db_returns_1(tmp_path):
    from src.exporters.sqlite_store import SQLiteStore
    empty = tmp_path / "empty.db"
    store = SQLiteStore(db_path=empty)
    conn = store._connect()
    store._create_schema(conn)
    conn.commit()
    conn.close()
    rc = backfill_main(["--db", str(empty)])
    assert rc == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_store.py -v -k backfill`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/valuation/backfill.py`**

```python
"""Backfill valuations for tickers already in the SQLite store.

Usage:
    python -m src.valuation.backfill                  # every company, default DB
    python -m src.valuation.backfill --db path.db     # explicit DB
    python -m src.valuation.backfill AAPL MSFT        # only these tickers

Reads only what collection already stored — no network fetches.
"""

import argparse
import logging
import sys
from typing import List, Optional

from ..config import StorageConfig
from .engine import compute_and_store


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute and store valuations from existing collected data."
    )
    parser.add_argument("tickers", nargs="*",
                        help="Tickers to value (default: every company in the DB)")
    parser.add_argument("--db", default=None,
                        help="SQLite DB path (default: the standard store location)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db_path = args.db or str(StorageConfig().database_path)
    tickers = args.tickers or None
    stored = compute_and_store(db_path, tickers=tickers)
    print(f"Valuations stored for {stored} ticker{'s' if stored != 1 else ''} "
          f"in {db_path}")
    return 0 if stored > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_store.py -v`
Expected: all pass

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/valuation/backfill.py tests/test_valuation_store.py
git commit -m "feat(valuation): backfill CLI over existing collected data"
```

---

### Task 10: Pipeline hook after export

**Files:**
- Modify: `src/fetchers/stock_data_fetcher.py` (in `fetch_and_export`, after the benchmark-bars block, before `end_time = datetime.now()`)
- Test: `tests/test_fetcher_concurrency.py` is unrelated — add the hook test to `tests/test_valuation_store.py` (append)

**Interfaces:**
- Consumes: `compute_and_store` (Task 8), `self.sqlite_store.db_path`, `resolved_formats`, `data` (list of `StockData`).

- [ ] **Step 1: Write the failing test (append to `tests/test_valuation_store.py`)**

```python
def test_fetch_and_export_triggers_valuations(monkeypatch):
    """fetch_and_export calls compute_and_store for the collected tickers."""
    from src.fetchers import stock_data_fetcher as sdf

    calls = {}

    def fake_compute_and_store(db_path, tickers=None, logger=None):
        calls["db_path"] = db_path
        calls["tickers"] = tickers
        return len(tickers or [])

    monkeypatch.setattr(
        "src.valuation.engine.compute_and_store", fake_compute_and_store
    )

    fetcher = sdf.StockDataFetcher.__new__(sdf.StockDataFetcher)
    # Only the attributes fetch_and_export touches:
    import logging as _logging

    class _FakeStore:
        db_path = "fake.db"

        def export_benchmark_bars(self, *a, **k):
            pass

    class _FakeStock:
        ticker = "AAA"
        errors = []
        warnings = []

    fetcher.logger = _logging.getLogger("test")
    fetcher.sqlite_store = _FakeStore()
    fetcher.config = type("C", (), {"output_formats": ["sqlite"]})()
    monkeypatch.setattr(fetcher, "fetch_multiple", lambda *a, **k: [_FakeStock()])
    monkeypatch.setattr(fetcher, "export", lambda *a, **k: {"sqlite": ["fake.db"]})
    monkeypatch.setattr(
        fetcher, "yahoo_handler",
        type("Y", (), {"fetch_benchmark_bars": lambda self: []})(),
        raising=False,
    )

    summary = fetcher.fetch_and_export(["AAA"])
    assert calls["tickers"] == ["AAA"]
    assert calls["db_path"] == "fake.db"
    assert summary["tickers_fetched"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_valuation_store.py -v -k fetch_and_export`
Expected: FAIL — `calls` stays empty (`KeyError: 'tickers'`)

- [ ] **Step 3: Add the hook in `fetch_and_export`**

In `src/fetchers/stock_data_fetcher.py`, immediately after the benchmark-bars `try/except` block (which ends with `self.logger.warning(f"Benchmark bars fetch/export failed: {e}")`), insert:

```python
        # Valuations: recompute for the just-collected tickers so the stored
        # fair-value ranges always reflect the newest fundamentals. Import is
        # local + lazy, and a valuation failure must never fail the run.
        if "sqlite" in resolved_formats and data:
            try:
                from ..valuation import engine as valuation_engine
                valuation_engine.compute_and_store(
                    self.sqlite_store.db_path,
                    tickers=[s.ticker for s in data],
                    logger=self.logger,
                )
            except Exception as e:
                self.logger.warning(f"Valuation computation failed: {e}")
```

(The lazy module import means the monkeypatch on `src.valuation.engine.compute_and_store` takes effect; do not use `from ..valuation.engine import compute_and_store`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_store.py tests/test_fetcher_concurrency.py -v`
Expected: all pass

- [ ] **Step 5: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/fetchers/stock_data_fetcher.py tests/test_valuation_store.py
git commit -m "feat(valuation): recompute valuations after each collection export"
```

---

### Task 11: Reader methods + `/api/stocks/{ticker}/valuation`

**Files:**
- Modify: `src/webapp/repository.py` (two new Reader methods, place after `analyst_snapshot`)
- Modify: `src/webapp/routes/stocks_api.py` (new endpoint after `analyst`)
- Test: `tests/test_web_api_valuation.py`

**Interfaces:**
- Consumes: `valuations`/`valuation_summary` tables (Task 8), `engine.verdict`/`upside_pct`/`VERDICT_LABELS` (Task 7), existing `Reader.quote`, `get_reader` dependency, `web_db`/`client` fixtures from `tests/conftest.py`.
- Produces (used by Tasks 12–13):
  - `Reader.valuations(ticker: str) -> List[Dict[str, Any]]` — all model rows ordered by model, `assumptions` still the raw JSON string.
  - `Reader.valuation_summary(ticker: str) -> Optional[Dict[str, Any]]`.
  - `GET /api/stocks/{ticker}/valuation` → `{"ticker", "price", "verdict", "verdict_label", "upside_pct", "summary": {...} | None, "models": [{model, applicable, na_reason, value_bear, value_base, value_bull, assumptions: dict, basis_fiscal_year, computed_at}]}`; 404 for unknown ticker.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_api_valuation.py
"""/api/stocks/{ticker}/valuation: stored models + live verdict."""
import json
import sqlite3

import pytest


def _seed_valuations(db_path, ticker="AAA"):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS valuations (
            ticker TEXT NOT NULL, model TEXT NOT NULL,
            applicable INTEGER NOT NULL, na_reason TEXT,
            value_bear REAL, value_base REAL, value_bull REAL,
            assumptions TEXT, basis_fiscal_year INTEGER,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (ticker, model)
        );
        CREATE TABLE IF NOT EXISTS valuation_summary (
            ticker TEXT PRIMARY KEY, n_applicable INTEGER NOT NULL,
            median_bear REAL, median_base REAL, median_bull REAL,
            computed_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'dcf', 1, NULL, 80.0, 100.0, "
        "120.0, ?, 2023, '2024-01-05T00:00:00')",
        (ticker, json.dumps({"growth_base": 0.05, "discount_base": 0.09})),
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'ddm', 0, 'no dividend history', "
        "NULL, NULL, NULL, '{}', NULL, '2024-01-05T00:00:00')",
        (ticker,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuation_summary VALUES (?, 1, 80.0, 100.0, 120.0, "
        "'2024-01-05T00:00:00')",
        (ticker,),
    )
    conn.commit()
    conn.close()


def test_valuation_endpoint_returns_models_and_verdict(client, web_db):
    _seed_valuations(web_db)
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAA"
    assert len(body["models"]) == 2
    dcf = next(m for m in body["models"] if m["model"] == "dcf")
    assert dcf["applicable"] is True
    assert dcf["assumptions"]["growth_base"] == 0.05  # JSON parsed to dict
    ddm = next(m for m in body["models"] if m["model"] == "ddm")
    assert ddm["na_reason"] == "no dividend history"
    # web_db's AAA snapshot has a current_price; verdict must be derivable
    assert body["verdict"] in ("cheap", "fair", "expensive")
    assert body["verdict_label"]
    assert body["summary"]["median_base"] == 100.0


def test_valuation_endpoint_no_rows_yet(client):
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"] == []
    assert body["verdict"] is None
    assert body["verdict_label"] == "Not valued"
    assert body["summary"] is None


def test_valuation_endpoint_unknown_ticker_404(client):
    resp = client.get("/api/stocks/ZZZ/valuation")
    assert resp.status_code == 404
```

Note: `web_db` seeds ticker AAA with a `market_snapshots.current_price` — check `tests/conftest.py` for the exact price when asserting; if AAA's price is not in the 80–120 band, the first test's verdict assertion still passes because it accepts all three verdicts.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_api_valuation.py -v`
Expected: FAIL (404 route not found → the first two tests fail; unknown-ticker may pass by accident, that is fine)

- [ ] **Step 3: Add Reader methods (`src/webapp/repository.py`, after `analyst_snapshot`)**

```python
    def valuations(self, ticker: str) -> List[Dict[str, Any]]:
        """All ``valuations`` model rows for a ticker, ordered by model.

        Returns [] when the table does not exist yet (DB predates the
        valuation layer) — callers treat that the same as "not computed".
        """
        try:
            cur = self._conn.execute(
                "SELECT * FROM valuations WHERE ticker = ? ORDER BY model",
                (ticker,),
            )
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in cur.fetchall()]

    def valuation_summary(self, ticker: str) -> Optional[Dict[str, Any]]:
        """The ``valuation_summary`` medians row, or None."""
        try:
            cur = self._conn.execute(
                "SELECT * FROM valuation_summary WHERE ticker = ?", (ticker,)
            )
        except sqlite3.OperationalError:
            return None
        row = cur.fetchone()
        return dict(row) if row is not None else None
```

- [ ] **Step 4: Add the endpoint (`src/webapp/routes/stocks_api.py`, after `analyst`)**

Add imports at the top: `import json` and `from ...valuation.engine import VERDICT_LABELS, upside_pct, verdict` (relative import depth: `stocks_api` is in `src/webapp/routes/`, so three dots reach `src`).

```python
@router.get("/{ticker}/valuation")
def valuation(ticker: str, r: Reader = Depends(get_reader)) -> Dict[str, Any]:
    """Stored per-model fair-value ranges + live verdict vs the latest price."""
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    rows = r.valuations(ticker)
    summary = r.valuation_summary(ticker)
    quote = r.quote(ticker)
    price = quote.get("current_price") if quote else None

    models = []
    for row in rows:
        try:
            assumptions = json.loads(row.get("assumptions") or "{}")
        except ValueError:
            assumptions = {}
        models.append({
            "model": row["model"],
            "applicable": bool(row["applicable"]),
            "na_reason": row.get("na_reason"),
            "value_bear": row.get("value_bear"),
            "value_base": row.get("value_base"),
            "value_bull": row.get("value_bull"),
            "assumptions": assumptions,
            "basis_fiscal_year": row.get("basis_fiscal_year"),
            "computed_at": row.get("computed_at"),
        })

    v = verdict(
        summary.get("median_bear") if summary else None,
        summary.get("median_bull") if summary else None,
        price,
    )
    return {
        "ticker": ticker,
        "price": price,
        "verdict": v,
        "verdict_label": VERDICT_LABELS[v],
        "upside_pct": upside_pct(
            summary.get("median_base") if summary else None, price
        ),
        "summary": summary,
        "models": models,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_api_valuation.py tests/test_web_api_stocks.py tests/test_web_repository.py -v`
Expected: all pass

- [ ] **Step 6: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/webapp/repository.py src/webapp/routes/stocks_api.py tests/test_web_api_valuation.py
git commit -m "feat(webapp): valuation Reader methods + /api/stocks/{ticker}/valuation"
```

---

### Task 12: Workstation VAL tab (fragment, chart, sensitivity grid)

**Files:**
- Modify: `src/webapp/routes/workstation.py` (TABS entry + `val_fragment` route)
- Create: `src/webapp/templates/fragments/val.html`
- Modify: `src/webapp/static/app.js` (add `renderVAL` after `renderDVD`)
- Test: `tests/test_web_workstation.py` (append)

**Interfaces:**
- Consumes: `Reader.valuations`/`valuation_summary`/`quote` (Task 11), `engine.verdict`/`upside_pct`/`VERDICT_LABELS`, `models.dcf_per_share` + `assumptions.GROWTH_SPREAD/DISCOUNT_SPREAD/GROWTH_CAP` (Tasks 2–3), existing `templates`, `fmt_price`/`fmt_pct` from `formatting.py`, `slateLayout` JS helper.
- Produces: tab key `val` (label "Valuation", code "VAL"), route `GET /ui/stocks/{ticker}/val`, JS `renderVAL(elId, cfg)`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_web_workstation.py`)**

```python
# ---- VAL fragment ----

def _seed_val_rows(web_db, ticker="AAA"):
    import json as _json
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(web_db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS valuations (
            ticker TEXT NOT NULL, model TEXT NOT NULL,
            applicable INTEGER NOT NULL, na_reason TEXT,
            value_bear REAL, value_base REAL, value_bull REAL,
            assumptions TEXT, basis_fiscal_year INTEGER,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (ticker, model)
        );
        CREATE TABLE IF NOT EXISTS valuation_summary (
            ticker TEXT PRIMARY KEY, n_applicable INTEGER NOT NULL,
            median_bear REAL, median_base REAL, median_bull REAL,
            computed_at TEXT NOT NULL
        );
        """
    )
    dcf_assumptions = _json.dumps({
        "growth_base": 0.05, "discount_base": 0.09, "fcf_basis": 100.0,
        "shares_outstanding": 100.0, "terminal_growth": 0.025,
        "growth_cap": 0.15,
    })
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'dcf', 1, NULL, 80.0, 100.0, "
        "120.0, ?, 2023, '2024-01-05T00:00:00')", (ticker, dcf_assumptions))
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'ddm', 0, 'no dividend "
        "history', NULL, NULL, NULL, '{}', NULL, '2024-01-05T00:00:00')", (ticker,))
    conn.execute(
        "INSERT OR REPLACE INTO valuation_summary VALUES (?, 1, 80.0, 100.0, "
        "120.0, '2024-01-05T00:00:00')", (ticker,))
    conn.commit()
    conn.close()


def test_val_tab_in_tab_bar(client):
    resp = client.get("/stocks/AAA")
    assert resp.status_code == 200
    assert "VAL" in resp.text
    assert '/ui/stocks/AAA/val' in resp.text


def test_val_fragment_renders_chart_and_models(client, web_db):
    _seed_val_rows(web_db)
    resp = client.get("/ui/stocks/AAA/val")
    assert resp.status_code == 200
    assert "renderVAL" in resp.text
    assert "no dividend history" in resp.text        # N/A reason listed
    assert "Sensitivity" in resp.text                # DCF grid present
    assert "growth_base" in resp.text                # assumptions shown


def test_val_fragment_empty_state(client):
    resp = client.get("/ui/stocks/AAA/val")
    assert resp.status_code == 200
    assert "No valuations computed yet" in resp.text


def test_val_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/val")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_workstation.py -v -k val`
Expected: FAIL (`/ui/stocks/AAA/val` → 404, no VAL in tab bar)

- [ ] **Step 3: Register the tab + route in `workstation.py`**

Append to `TABS`:

```python
    ("Valuation", "VAL", "val"),
```

Add imports at the top: `import json`, `from ...valuation.assumptions import DISCOUNT_SPREAD, GROWTH_CAP, GROWTH_SPREAD`, `from ...valuation.engine import VERDICT_LABELS, upside_pct, verdict`, `from ...valuation.models import dcf_per_share`. Add the fragment route (after the DVD fragment, following the same shape):

```python
# ---------------------------------------------------------------------------
# VAL fragment
# ---------------------------------------------------------------------------


def _dcf_sensitivity(assumptions: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Growth x discount grid of DCF per-share values from stored assumptions.

    Returns None when the stored assumptions lack the required inputs
    (pre-valuation rows, or a hand-edited DB).
    """
    try:
        fcf = float(assumptions["fcf_basis"])
        shares = float(assumptions["shares_outstanding"])
        g0 = float(assumptions["growth_base"])
        d0 = float(assumptions["discount_base"])
    except (KeyError, TypeError, ValueError):
        return None
    growth_offsets = [-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03]
    growths = sorted({min(max(g0 + off, 0.0), GROWTH_CAP) for off in growth_offsets})
    discounts = [d0 - DISCOUNT_SPREAD, d0, d0 + DISCOUNT_SPREAD]
    rows = []
    for d in discounts:
        rows.append({
            "discount": d,
            "values": [dcf_per_share(fcf, shares, g, d) for g in growths],
        })
    return {"growths": growths, "rows": rows}


@router.get("/ui/stocks/{ticker}/val", response_class=HTMLResponse)
def val_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """VAL panel: fair-value ranges vs price, per-model detail, DCF sensitivity."""
    company = r.get_company(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    rows = r.valuations(ticker)
    summary = r.valuation_summary(ticker)
    quote = r.quote(ticker)
    price = quote.get("current_price") if quote else None

    model_labels = {"dcf": "DCF", "ddm": "Dividend Discount", "graham": "Graham Number",
                    "lynch": "Peter Lynch", "multiples": "Multiples Band"}
    applicable = []
    not_applicable = []
    sensitivity = None
    for row in rows:
        try:
            assumptions = json.loads(row.get("assumptions") or "{}")
        except ValueError:
            assumptions = {}
        entry = {
            "model": row["model"],
            "label": model_labels.get(row["model"], row["model"]),
            "na_reason": row.get("na_reason"),
            "bear": row.get("value_bear"),
            "base": row.get("value_base"),
            "bull": row.get("value_bull"),
            "bear_fmt": fmt_price(row.get("value_bear")),
            "base_fmt": fmt_price(row.get("value_base")),
            "bull_fmt": fmt_price(row.get("value_bull")),
            "basis_fy": row.get("basis_fiscal_year"),
            "assumptions": assumptions,
        }
        if row["applicable"]:
            applicable.append(entry)
            if row["model"] == "dcf":
                sensitivity = _dcf_sensitivity(assumptions)
        else:
            not_applicable.append(entry)

    v = verdict(
        summary.get("median_bear") if summary else None,
        summary.get("median_bull") if summary else None,
        price,
    )
    upside = upside_pct(summary.get("median_base") if summary else None, price)

    val_cfg = {
        "models": [{"label": e["label"], "bear": e["bear"], "base": e["base"],
                    "bull": e["bull"]} for e in applicable],
        "price": price,
    }
    computed_at = rows[0].get("computed_at") if rows else None
    return templates.TemplateResponse(
        request,
        "fragments/val.html",
        {
            "request": request,
            "ticker": ticker,
            "has_rows": bool(rows),
            "applicable": applicable,
            "not_applicable": not_applicable,
            "verdict": v,
            "verdict_label": VERDICT_LABELS[v],
            "upside_fmt": fmt_pct(upside),
            "price_fmt": fmt_price(price),
            "summary": summary,
            "median_base_fmt": fmt_price(summary.get("median_base") if summary else None),
            "sensitivity": sensitivity,
            "computed_at": computed_at,
            "val_cfg_json": json.dumps(val_cfg),
        },
    )
```

`fmt_price`/`fmt_pct` are already imported in `workstation.py` (verify; if not, extend the existing `..formatting` import). `Dict`/`Any`/`Optional` come from the module's existing `typing` import — extend if missing.

- [ ] **Step 4: Create `src/webapp/templates/fragments/val.html`**

```html
{% if not has_rows %}
<div class="empty-state">
  No valuations computed yet. Run a collection, or backfill from existing data:
  <code>python -m src.valuation.backfill</code>
</div>
{% else %}
<section class="section">
  <h2 class="section-heading">Fair Value vs Price</h2>
  <div class="chip-row">
    <span class="pill verdict-{{ verdict or 'none' }}">{{ verdict_label }}</span>
    <span class="text-muted">Median fair value {{ median_base_fmt }} ·
      Price {{ price_fmt }} · Upside {{ upside_fmt }}</span>
  </div>
  {% if applicable %}
  <div id="val-chart" class="gp-chart"></div>
  <script>
    var _valCfg = {{ val_cfg_json | safe }};
    renderVAL('val-chart', _valCfg);
  </script>
  {% else %}
  <div class="empty-state">No model applies to this company — see reasons below.</div>
  {% endif %}
  <p class="text-muted mt-2">Computed {{ computed_at }} from stored fundamentals.
    Ranges are bear/base/bull scenarios from conservative mechanical assumptions —
    not forecasts.</p>
</section>

<section class="section">
  <h2 class="section-heading">Models</h2>
  {% if applicable %}
  <div class="table-wrap">
    <table class="data-table">
      <thead><tr><th>Model</th><th class="num">Bear</th><th class="num">Base</th>
        <th class="num">Bull</th><th class="num">Basis FY</th><th>Assumptions</th></tr></thead>
      <tbody>
        {% for m in applicable %}
        <tr>
          <td class="row-label">{{ m.label }}</td>
          <td class="num">{{ m.bear_fmt }}</td>
          <td class="num">{{ m.base_fmt }}</td>
          <td class="num">{{ m.bull_fmt }}</td>
          <td class="num">{{ m.basis_fy or "—" }}</td>
          <td>
            <details>
              <summary>show</summary>
              <table class="data-table">
                <tbody>
                  {% for k, v in m.assumptions.items() %}
                  <tr><td class="row-label">{{ k }}</td><td class="num">{{ v }}</td></tr>
                  {% endfor %}
                </tbody>
              </table>
            </details>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
  {% if not_applicable %}
  <h3 class="section-heading mt-2">Not applicable</h3>
  <ul>
    {% for m in not_applicable %}
    <li><strong>{{ m.label }}</strong> — {{ m.na_reason }}</li>
    {% endfor %}
  </ul>
  {% endif %}
</section>

{% if sensitivity %}
<section class="section">
  <h2 class="section-heading">DCF Sensitivity</h2>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>Discount \ Growth</th>
          {% for g in sensitivity.growths %}<th class="num">{{ "%.1f%%" | format(g * 100) }}</th>{% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for row in sensitivity.rows %}
        <tr>
          <td class="row-label">{{ "%.1f%%" | format(row.discount * 100) }}</td>
          {% for v in row["values"] %}<td class="num">{{ "%.2f" | format(v) }}</td>{% endfor %}
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
{% endif %}
{% endif %}
```

(Note `row["values"]` — Jinja's `row.values` would resolve to the dict method.)

- [ ] **Step 5: Add `renderVAL` to `src/webapp/static/app.js` (after `renderDVD`)**

```javascript
function renderVAL(elId, cfg) {
  var labels = cfg.models.map(function (m) { return m.label; });
  var bears = cfg.models.map(function (m) { return m.bear; });
  var spans = cfg.models.map(function (m) { return m.bull - m.bear; });
  var bases = cfg.models.map(function (m) { return m.base; });

  var range = {
    y: labels, x: spans, base: bears,
    type: 'bar', orientation: 'h', name: 'Bear–Bull',
    marker: { color: 'rgba(94, 129, 172, 0.35)' },
    hovertemplate: '%{y}: %{base:.2f} – %{x:.2f}<extra></extra>',
  };
  var baseMarks = {
    y: labels, x: bases,
    type: 'scatter', mode: 'markers', name: 'Base',
    marker: { size: 10, symbol: 'line-ns-open', color: '#88c0d0' },
    hovertemplate: 'Base: %{x:.2f}<extra></extra>',
  };
  var layout = slateLayout({
    barmode: 'overlay',
    showlegend: false,
    xaxis: slateAxis({ title: 'Per-share value' }),
    yaxis: slateAxis({ automargin: true }),
    margin: { t: 8, r: 8, b: 32, l: 8 },
  });
  if (cfg.price !== null && cfg.price !== undefined) {
    layout.shapes = [{
      type: 'line', x0: cfg.price, x1: cfg.price, y0: -0.5,
      y1: labels.length - 0.5,
      line: { color: '#bf616a', width: 2, dash: 'dot' },
    }];
    layout.annotations = [{
      x: cfg.price, y: labels.length - 0.5, yanchor: 'bottom',
      text: 'Price', showarrow: false, font: { color: '#bf616a' },
    }];
  }
  Plotly.newPlot(elId, [range, baseMarks], layout,
                 { displayModeBar: false, responsive: true });
}
```

Before writing, open `app.js` and mirror `renderDVD`'s exact conventions (how it calls `slateLayout`/`slateAxis`, config object, colors from CSS variables if that is what neighbors do) — the snippet above is the required structure; the styling tokens must match the file's existing idiom.

Also add verdict chip colors to `src/webapp/static/app.css` next to the existing `.pill` styles:

```css
.pill.verdict-cheap { color: var(--up, #a3be8c); border-color: currentColor; }
.pill.verdict-expensive { color: var(--down, #bf616a); border-color: currentColor; }
.pill.verdict-fair, .pill.verdict-none { color: var(--muted, #8892a6); border-color: currentColor; }
```

(Match the actual CSS variable names used by `.pill.up` / `.pill.down` in `app.css` — open the file and reuse the same tokens rather than the fallback hexes.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_workstation.py tests/test_web_smoke.py -v`
Expected: all pass (existing workstation tests must not regress)

- [ ] **Step 7: Lint, type-check, commit**

Run: `ruff check src tests && mypy src`

```bash
git add src/webapp/routes/workstation.py src/webapp/templates/fragments/val.html src/webapp/static/app.js src/webapp/static/app.css tests/test_web_workstation.py
git commit -m "feat(webapp): workstation VAL tab with range chart and DCF sensitivity"
```

---

### Task 13: DES overview verdict strip

**Files:**
- Modify: `src/webapp/routes/workstation.py` (`des_fragment` context)
- Modify: `src/webapp/templates/fragments/des.html` (two summary-grid items)
- Test: `tests/test_web_workstation.py` (append)

**Interfaces:**
- Consumes: `Reader.valuation_summary`, `engine.verdict`/`upside_pct`/`VERDICT_LABELS` (already imported in Task 12).

- [ ] **Step 1: Write the failing tests (append to `tests/test_web_workstation.py`)**

```python
def test_des_fragment_shows_valuation_verdict(client, web_db):
    _seed_val_rows(web_db)
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    assert "Valuation" in resp.text
    # One of the three verdict labels (depends on AAA's seeded price)
    assert ("Looks cheap" in resp.text or "Fairly valued" in resp.text
            or "Looks expensive" in resp.text)


def test_des_fragment_not_valued_without_rows(client):
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    assert "Not valued" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_workstation.py -v -k "verdict or not_valued"`
Expected: FAIL (no "Not valued" in DES output)

- [ ] **Step 3: Extend `des_fragment`**

Inside `des_fragment`, after `analyst = r.analyst_snapshot(ticker)` add:

```python
    val_summary = r.valuation_summary(ticker)
    val_verdict = verdict(
        val_summary.get("median_bear") if val_summary else None,
        val_summary.get("median_bull") if val_summary else None,
        current_price,
    )
```

(`current_price` is defined a few lines below in the existing code — move this block to after `current_price = ...` instead.) Then add to the returned template context:

```python
            "val_verdict": val_verdict,
            "val_verdict_label": VERDICT_LABELS[val_verdict],
            "val_upside_fmt": fmt_pct(upside_pct(
                val_summary.get("median_base") if val_summary else None,
                current_price,
            )),
```

- [ ] **Step 4: Extend `des.html`**

In the Summary `summary-grid` div, append two items after the last existing `summary-item`:

```html
    <div class="summary-item"><span class="summary-label">Valuation</span>
      <span class="summary-value"><span class="pill verdict-{{ val_verdict or 'none' }}">{{ val_verdict_label }}</span></span></div>
    <div class="summary-item"><span class="summary-label">Upside (median)</span>
      <span class="summary-value">{{ val_upside_fmt }}</span></div>
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `python -m pytest tests/test_web_workstation.py -v && ruff check src tests && mypy src`
Expected: all pass

```bash
git add src/webapp/routes/workstation.py src/webapp/templates/fragments/des.html tests/test_web_workstation.py
git commit -m "feat(webapp): DES overview shows valuation verdict + median upside"
```

---

### Task 14: Screener upside column + verdict filter

**Files:**
- Modify: `src/webapp/screener.py` (valuation join, `val_upside_pct` expression column, `verdict` spec field)
- Modify: `src/webapp/routes/screener_api.py` (annotate rows with `val_verdict` before rendering)
- Modify: `src/webapp/templates/fragments/screener_results.html` (two columns)
- Modify: `src/webapp/templates/screener.html` + `src/webapp/static/screener.js` (verdict select, wired exactly like the existing `sector-select`)
- Test: `tests/test_web_screener.py` (append)

**Interfaces:**
- Consumes: `valuation_summary` table, `engine.verdict`.
- Produces:
  - `SNAPSHOT_SCREEN_COLUMNS` gains `"current_price"` (whitelisted, kind `"raw"`).
  - `VALUATION_EXPRS: Dict[str, str]` with `"val_upside_pct"` → `'((vsum."median_base" - ms."current_price") / NULLIF(ms."current_price", 0))'`; the field is filterable/sortable and selected as `AS val_upside_pct`; kind `"pct"`.
  - `SCREEN_COLUMNS` gains `median_bear`, `median_base`, `median_bull`, `val_upside_pct` (after the snapshot columns).
  - `ScreenSpec.verdict: Optional[str]` (`cheap|fair|expensive`); `parse_screen_params` reserves the `verdict` key.

- [ ] **Step 1: Write the failing tests (append to `tests/test_web_screener.py`)**

```python
# ---- valuation columns + verdict filter ----
import sqlite3


def _seed_summary(web_db, ticker, bear, base, bull):
    conn = sqlite3.connect(str(web_db))
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS valuation_summary ("
        "ticker TEXT PRIMARY KEY, n_applicable INTEGER NOT NULL, "
        "median_bear REAL, median_base REAL, median_bull REAL, "
        "computed_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuation_summary VALUES (?, 3, ?, ?, ?, "
        "'2024-01-05T00:00:00')",
        (ticker, bear, base, bull),
    )
    conn.commit()
    conn.close()


def test_screen_returns_valuation_columns(client, web_db):
    _seed_summary(web_db, "AAA", 80.0, 100.0, 120.0)
    resp = client.get("/api/screen", params={"limit": 10})
    assert resp.status_code == 200
    items = resp.json()["items"]
    aaa = next(i for i in items if i["ticker"] == "AAA")
    assert aaa["median_base"] == 100.0
    assert aaa["val_upside_pct"] is not None


def test_screen_sort_by_upside(client, web_db):
    _seed_summary(web_db, "AAA", 80.0, 100.0, 120.0)
    resp = client.get("/api/screen", params={"sort": "val_upside_pct",
                                             "sort_dir": "desc", "limit": 10})
    assert resp.status_code == 200


def test_screen_verdict_filter_cheap(client, web_db):
    # AAA priced far below its bear median -> cheap
    _seed_summary(web_db, "AAA", 1e6, 2e6, 3e6)
    resp = client.get("/api/screen", params={"verdict": "cheap", "limit": 10})
    assert resp.status_code == 200
    tickers = [i["ticker"] for i in resp.json()["items"]]
    assert "AAA" in tickers
    resp = client.get("/api/screen", params={"verdict": "expensive", "limit": 10})
    assert "AAA" not in [i["ticker"] for i in resp.json()["items"]]


def test_screen_verdict_filter_invalid_400(client):
    resp = client.get("/api/screen", params={"verdict": "bogus"})
    assert resp.status_code == 400


def test_screen_results_fragment_has_upside_header(client, web_db):
    _seed_summary(web_db, "AAA", 80.0, 100.0, 120.0)
    resp = client.get("/ui/screen")
    assert resp.status_code == 200
    assert "Upside" in resp.text
```

Before writing these, open `tests/test_web_screener.py` and match its existing request style (`/api/screen` GET vs POST, param names) — the tests above assume the GET shorthand endpoint exists as `/api/screen`; adjust paths to the file's existing conventions if they differ (e.g. `/api/screen` vs `/api/screener`), keeping the assertions identical.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_screener.py -v -k "valuation or upside or verdict"`
Expected: FAIL (`KeyError: 'median_base'`, 400s missing, etc.)

- [ ] **Step 3: Extend `src/webapp/screener.py`**

1. Append `"current_price"` to `SNAPSHOT_SCREEN_COLUMNS`.
2. Add after `_SNAPSHOT_COL_SET`:

```python
# Valuation medians (stored in valuation_summary) exposed to the screener.
# val_upside_pct is a computed expression, not a raw column, so it lives in
# its own whitelist mapping field name -> SQL expression.
VALUATION_EXPRS: Dict[str, str] = {
    "val_upside_pct":
        '((vsum."median_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
}
VALUATION_SELECT_COLUMNS: List[str] = ["median_bear", "median_base", "median_bull"]
ALLOWED_VERDICTS = ("cheap", "fair", "expensive")
```

3. Extend `SCREEN_COLUMNS`:

```python
SCREEN_COLUMNS: List[str] = (
    ["ticker", "company_name", "sector_class", "fiscal_year"]
    + list(_METRIC_COLUMNS)
    + list(SNAPSHOT_SCREEN_COLUMNS)
    + VALUATION_SELECT_COLUMNS
    + list(VALUATION_EXPRS)
)
```

4. Add `METRIC_KINDS` entries: `"val_upside_pct": "pct"`, `"current_price": "raw"`, `"median_bear": "raw"`, `"median_base": "raw"`, `"median_bull": "raw"`.
5. Add the join constant next to `_SNAPSHOT_JOIN`:

```python
# Per-ticker valuation medians; LEFT JOIN so unvalued tickers still appear.
_VALUATION_JOIN: str = (
    "LEFT JOIN valuation_summary vsum ON vsum.ticker = ma.ticker"
)
```

6. In `_qualify_column`, before the final `raise`:

```python
    if field_name in VALUATION_EXPRS:
        return VALUATION_EXPRS[field_name]
```

and update the error message to mention the valuation whitelist.
7. Add `verdict: Optional[str] = None` to `ScreenSpec`.
8. In `_build_where`, after the sector clause:

```python
    if spec.verdict is not None:
        if spec.verdict not in ALLOWED_VERDICTS:
            raise ValueError(
                f"Invalid verdict {spec.verdict!r}: must be one of "
                f"{list(ALLOWED_VERDICTS)}."
            )
        price = 'ms."current_price"'
        if spec.verdict == "cheap":
            clauses.append(f'{price} < vsum."median_bear"')
        elif spec.verdict == "expensive":
            clauses.append(f'{price} > vsum."median_bull"')
        else:
            clauses.append(
                f'{price} >= vsum."median_bear" AND {price} <= vsum."median_bull"'
            )
```

9. In `build_screen_query`, extend the SELECT list and joins:

```python
    valuation_cols_sql = ", ".join(f'vsum."{c}"' for c in VALUATION_SELECT_COLUMNS)
    valuation_exprs_sql = ", ".join(
        f"{expr} AS {name}" for name, expr in VALUATION_EXPRS.items()
    )
    select_sql = (
        f"c.ticker, c.company_name, c.sector_class, ma.fiscal_year, "
        f"{metric_cols_sql}, {snapshot_cols_sql}, "
        f"{valuation_cols_sql}, {valuation_exprs_sql}"
    )
```

and add `f"{_VALUATION_JOIN}\n"` after `{_SNAPSHOT_JOIN}\n` in **both** `build_screen_query` and `build_count_query` (the verdict filter references `vsum` in WHERE).
10. In `parse_screen_params`: add `"verdict"` to `RESERVED`, parse `verdict = params.get("verdict") or None`, validate early (`if verdict is not None and verdict not in ALLOWED_VERDICTS: raise ValueError(...)` — same message as `_build_where`), and pass `verdict=verdict` to the returned `ScreenSpec`.

- [ ] **Step 4: Annotate rows and render columns**

In `src/webapp/routes/screener_api.py`, add `from ...valuation.engine import verdict as valuation_verdict` and a helper used by every code path that renders `screener_results.html` or returns screen JSON (both `post_screen` and `get_screen` — check `pages.py` too if it renders the fragment):

```python
def _annotate_verdicts(items: List[Dict[str, Any]]) -> None:
    for row in items:
        row["val_verdict"] = valuation_verdict(
            row.get("median_bear"), row.get("median_bull"),
            row.get("current_price"),
        )
```

Call it on `result["items"]` right after each `r.screen(spec)` call.

In `screener_results.html`:
- extend `sortable_cols` with `('val_upside_pct', 'Upside %')`,
- add a plain `<th>Valuation</th>` after the sortable columns loop,
- add the row cells after the `debt_to_ebitda` cell:

```html
        <td>{{ fmt_pct(row.get("val_upside_pct")) }}</td>
        <td>{% if row.get("val_verdict") == "cheap" %}<span class="pill verdict-cheap">Looks cheap</span>
            {% elif row.get("val_verdict") == "expensive" %}<span class="pill verdict-expensive">Looks expensive</span>
            {% elif row.get("val_verdict") == "fair" %}<span class="pill verdict-fair">Fairly valued</span>
            {% else %}—{% endif %}</td>
```

(Column count note: the `Upside %` `<th>` comes from `sortable_cols`, so only the `Valuation` `<th>` is added by hand — keep header and body cell counts equal.)

In `screener.html`, add a verdict select next to `#sector-select`:

```html
    <select id="verdict-select" class="form-control form-control-sm">
      <option value="">Any valuation</option>
      <option value="cheap">Looks cheap</option>
      <option value="fair">Fairly valued</option>
      <option value="expensive">Looks expensive</option>
    </select>
```

In `src/webapp/static/screener.js`, wire `#verdict-select` exactly the way `#sector-select` is wired: grep `screener.js` for every occurrence of `sector` (change handler, param serialization into the screen URL, URL/deep-link sync, saved-screen round-trip) and add the parallel `verdict` handling at each site, using param key `verdict`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_screener.py tests/test_web_export.py -v`
Expected: all pass — including the pre-existing screener tests (whitelist disjointness, sort validation) and CSV export (which uses `SCREEN_COLUMNS` and now includes the new columns).

- [ ] **Step 6: Lint, type-check, full suite, commit**

Run: `ruff check src tests && mypy src && python -m pytest -q`
Expected: everything passes.

```bash
git add src/webapp/screener.py src/webapp/routes/screener_api.py src/webapp/templates/fragments/screener_results.html src/webapp/templates/screener.html src/webapp/static/screener.js tests/test_web_screener.py
git commit -m "feat(webapp): screener upside column + valuation verdict filter"
```

---

### Task 15: Live verification + docs

**Files:**
- Modify: `README.md` (Valuation section under Features), `USAGE_GUIDE.md` (backfill command + VAL tab)

- [ ] **Step 1: End-to-end smoke on the real DB**

```bash
python -m src.valuation.backfill
```

Expected: `Valuations stored for N tickers in data\output\stock.db` (N = number of collected companies). Then start the webapp (`python -m src.webapp`) and verify by hand:
1. `/stocks/<ticker>` shows the VAL tab; the range chart renders; assumptions expand; a bank ticker shows DCF/Lynch as sector-N/A with DDM populated.
2. DES tab shows the Valuation pill + upside.
3. `/screener` shows Upside % (sortable) and the verdict chips; the verdict dropdown filters.
4. `GET /api/stocks/<ticker>/valuation` returns the JSON payload.

- [ ] **Step 2: Update docs**

README Features: add a "Valuation Models" bullet group (5 models, bear/base/bull ranges, stored assumptions, sector-aware N/A). USAGE_GUIDE: document `python -m src.valuation.backfill [--db PATH] [tickers ...]`, note valuations recompute automatically on collection, and describe the VAL tab + screener columns.

- [ ] **Step 3: Full suite + commit**

Run: `ruff check src tests && mypy src && python -m pytest -q`

```bash
git add README.md USAGE_GUIDE.md
git commit -m "docs: valuation layer usage (backfill, VAL tab, screener columns)"
```

---

## Self-Review Notes (already applied)

- Spec coverage: models (T3–6), conservative assumptions (T2), storage incl. summary medians (T8), backfill (T9), pipeline hook (T10), API + stock-page/workstation surfaces (T11–13), screener (T14), failure handling (N/A rows everywhere, engine try/except, missing-table guards in Reader), testing (unit fixtures, property-ish monotonicity, integration via compute_and_store, route tests).
- The spec's "stock page Valuation panel" and "workstation Valuation tab" are one surface in this codebase — the stock page *is* the workstation. The VAL tab (T12) is the full panel; the DES strip (T13) is the at-a-glance verdict.
- Type consistency spot-checks: `ValuationResult` fields match T8's DB columns and T11's API payload; `dcf_per_share(fcf0, shares, growth, discount)` signature identical in T3 and T12; verdict strings `cheap|fair|expensive` consistent across engine, screener SQL, templates.
