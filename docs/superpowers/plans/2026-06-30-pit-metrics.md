# Point-in-Time Metrics (Sub-Project 3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure-read `PointInTimeMetrics` that computes the fundamental ratio suite on as-of-date financials, so a backtest reads ratios (ROE, ROIC, margins, sector ratios) as they were knowable on a given date — no look-ahead.

**Architecture:** A thin composition layer over the two existing pieces: `AsOfReader` (SP2 — point-in-time data) supplies the as-of period dict; `CalculatedMetrics.calculate_all(financials, sector=...)` (the existing engine) computes the ratios. SP3 owns no ratio math and no SQL resolution — it composes. Sector is auto-read from the `companies` table. No write-path or schema change.

**Tech Stack:** Python 3.9, SQLite (stdlib `sqlite3`), pytest.

## Global Constraints

- Python 3.9 floor — no `X | Y` union syntax (use `Optional[...]`, `Dict[...]`, `Union[...]`, `Any`).
- ruff clean: line-length 120; rules E, F, W, I; imports at top of file (avoid E402).
- mypy gate is **bare `mypy`** (project scopes via pyproject `[tool.mypy] files=[...]`). `src/query/pit_metrics.py` MUST be added to that `files` list. `CalculatedMetrics` (`src/parsers/calculated_metrics.py`) is NOT type-gated and is consumed as a silent import — keep `pit_metrics.py` itself clean. Do NOT run `mypy src`.
- Read-only DB access (reuses the `AsOfReader` `mode=ro` connection); never mutate.
- Fundamental ratios only — call `calculate_all` WITHOUT `market_data`, so valuation/EV keys (`enterprise_value`, `ev_to_ebitda`, `ev_to_revenue`, `ev_to_fcf`, `fcf_yield`) never appear.
- Commit trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `src/query/asof.py` — add a public `conn` property (read-only connection accessor) so the metrics layer can read the `companies` table without reaching into a private attribute. No other change.
- `src/query/pit_metrics.py` — new: the `PointInTimeMetrics` class (compose layer + the three methods).
- `tests/test_pit_metrics.py` — unit tests against a hand-built temp DB.
- `pyproject.toml` — add `src/query/pit_metrics.py` to `[tool.mypy] files`.
- `README.md` — extend the "Point-in-time as-of queries" subsection with a metrics example.

---

### Task 1: `conn` accessor + `PointInTimeMetrics` skeleton (compose + sector lookup)

Expose the reader's connection, then build the class shell: `__init__`, `from_path`, `close`/context-manager, and `_sector`. No metric methods yet.

**Files:**
- Modify: `src/query/asof.py` (add a `conn` property)
- Create: `src/query/pit_metrics.py`
- Create: `tests/test_pit_metrics.py`
- Modify: `pyproject.toml` (`[tool.mypy] files`)

**Interfaces:**
- Consumes: `AsOfReader` (SP2 — `as_of_annual`, `history_as_of`, `close`, read-only `_conn` with `row_factory = sqlite3.Row`); `CalculatedMetrics` from `src/parsers/calculated_metrics.py` (`calculate_all(financials, market_data=None, valuation=None, sector=None) -> Dict[str, Any]`).
- Produces: `AsOfReader.conn` (property → `sqlite3.Connection`); `PointInTimeMetrics(reader, calculator=None, logger=None)` with `from_path(db_path)`, `close()`, `__enter__`/`__exit__`, and `_sector(ticker) -> Optional[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pit_metrics.py`:

```python
"""Tests for point-in-time metrics (ratios computed on as-of-date financials)."""

import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.query.pit_metrics import PointInTimeMetrics


def _build_db(tmp_path, vintages, ticker="PRU", sector_class=None):
    """Write a StockData (vintages + optional sector_class) and return the db path.

    vintages: {fy_str: {accn: period_dict}} as StockData.financials_annual_vintages.
    """
    s = StockData(ticker=ticker, cik="1", company_name="Co")
    if sector_class is not None:
        s.sector_class = sector_class
    s.financials_annual_vintages = vintages
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([s])
    return str(db)


_ONE_VINTAGE = {
    "2019": {"a": {"fiscal_year": 2019, "accn": "a", "filed_date": "2020-02-15",
                   "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                   "net_income": 100.0, "total_equity": 1000.0, "revenue": 1000.0}},
}


def test_sector_auto_read_from_companies(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE, sector_class="bank")
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm._sector("PRU") == "bank"


def test_sector_none_when_absent_or_unknown(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE)  # no sector_class set
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm._sector("PRU") is None       # column is NULL
        assert pm._sector("ZZZZ") is None       # no such company row


def test_from_path_builds_reader_and_closes(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE)
    pm = PointInTimeMetrics.from_path(db)
    assert pm.reader is not None
    pm.close()
    # After close, the reader's connection is unusable.
    with pytest.raises(sqlite3.ProgrammingError):
        pm.reader.conn.execute("SELECT 1")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pit_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.query.pit_metrics'`.

- [ ] **Step 3: Add the `conn` property to `AsOfReader`**

In `src/query/asof.py`, add this property to `AsOfReader` immediately after `__exit__` (before `_norm_date`):

```python
    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying read-only connection (for sibling readers over the same store)."""
        return self._conn
```

- [ ] **Step 4: Create `PointInTimeMetrics` (skeleton only)**

Create `src/query/pit_metrics.py`:

```python
"""Point-in-time metrics: the fundamental ratio suite computed on as-of-date financials.

A pure-read composition layer. ``AsOfReader`` (see ``query/asof.py``) supplies each
annual period as it was known on a date ``D`` (the latest filing made on or before ``D``);
``CalculatedMetrics`` (see ``parsers/calculated_metrics.py``) computes the ratios. So the
metrics for a year as of ``D`` are ``calculate_all(as_of_annual(ticker, fy, D), sector=...)``
— the same engine the pipeline uses, fed the as-of financials instead of the latest-restated
ones, with no look-ahead.

Only the **fundamental** ratios are produced (profitability, returns, margins, capital
structure, coverage, efficiency, and the bank/insurer/REIT sector ratios). Valuation/EV
ratios are intentionally absent: they need the share price as of ``D`` and the project stores
no historical per-date price series. (``calculate_all`` is called without ``market_data``,
so those keys never appear.)
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..parsers.calculated_metrics import CalculatedMetrics
from .asof import AsOfReader


class PointInTimeMetrics:
    """Compute the fundamental ratio suite on point-in-time (as-of-date) financials."""

    def __init__(self, reader: AsOfReader,
                 calculator: Optional[CalculatedMetrics] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.reader = reader
        self.calculator = calculator or CalculatedMetrics()
        self.logger = logger or logging.getLogger(__name__)

    @classmethod
    def from_path(cls, db_path: Union[str, Path]) -> "PointInTimeMetrics":
        """Build an instance over a DB path (constructs its own read-only AsOfReader)."""
        return cls(AsOfReader(db_path))

    def close(self) -> None:
        self.reader.close()

    def __enter__(self) -> "PointInTimeMetrics":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _sector(self, ticker: str) -> Optional[str]:
        """The company's ``sector_class`` from the companies table, or ``None``."""
        cur = self.reader.conn.execute(
            "SELECT sector_class FROM companies WHERE ticker = ?", (ticker,)
        )
        row = cur.fetchone()
        return row["sector_class"] if row is not None else None
```

- [ ] **Step 5: Add the module to the mypy files list**

In `pyproject.toml`, under `[tool.mypy]`, add `"src/query/pit_metrics.py",` to the `files = [...]` list, after the `"src/query/asof.py",` entry:

```toml
    "src/query/asof.py",
    "src/query/pit_metrics.py",
]
```

- [ ] **Step 6: Run the tests + linters to verify green**

Run: `python -m pytest tests/test_pit_metrics.py -v && ruff check src tests && mypy`
Expected: 3 tests PASS; ruff clean; bare `mypy` Success (now covering `src/query/pit_metrics.py`).

- [ ] **Step 7: Commit**

```bash
git add src/query/asof.py src/query/pit_metrics.py tests/test_pit_metrics.py pyproject.toml
git commit -m "feat(query): PointInTimeMetrics skeleton + AsOfReader.conn accessor

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `metrics_as_of` + `metric_as_of` (the compose rule)

Implement the primary metrics resolver and its scalar convenience.

**Files:**
- Modify: `src/query/pit_metrics.py` (add `metrics_as_of`, `metric_as_of`)
- Test: `tests/test_pit_metrics.py` (append)

**Interfaces:**
- Consumes: `self.reader.as_of_annual` (SP2), `self._sector` (Task 1), `self.calculator.calculate_all`.
- Produces:
  - `metrics_as_of(ticker, fiscal_year, as_of_date, sector=None) -> Optional[Dict[str, Any]]`
  - `metric_as_of(ticker, fiscal_year, name, as_of_date, sector=None) -> Any`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pit_metrics.py`. The restated fixture: FY2019 net_income 100 (orig, filed 2020-02-15) vs 90 (restated, filed 2021-02-15), equity 1000 both → roe 0.10 vs 0.09:

```python
_RESTATED = {
    "2019": {
        "orig": {"fiscal_year": 2019, "accn": "orig", "filed_date": "2020-02-15",
                 "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                 "net_income": 100.0, "total_equity": 1000.0, "revenue": 1000.0},
        "restate": {"fiscal_year": 2019, "accn": "restate", "filed_date": "2021-02-15",
                    "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                    "net_income": 90.0, "total_equity": 1000.0, "revenue": 1000.0},
    },
}


def test_metric_is_point_in_time(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        # Between the two filings: ROE from the original (100/1000).
        assert pm.metric_as_of("PRU", 2019, "roe", "2020-06-30") == 0.10
        # On/after the restatement: ROE from the restated value (90/1000).
        assert pm.metric_as_of("PRU", 2019, "roe", "2021-02-15") == 0.09


def test_not_yet_filed_returns_none(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm.metrics_as_of("PRU", 2019, "2020-02-14") is None
        assert pm.metric_as_of("PRU", 2019, "roe", "2020-02-14") is None


def test_unknown_ticker_or_year_returns_none(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm.metrics_as_of("ZZZZ", 2019, "2025-01-01") is None
        assert pm.metrics_as_of("PRU", 1990, "2025-01-01") is None


def test_valuation_ratios_excluded(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        m = pm.metrics_as_of("PRU", 2019, "2020-06-30")
        for k in ("enterprise_value", "ev_to_ebitda", "ev_to_revenue",
                  "ev_to_fcf", "fcf_yield"):
            assert k not in m
        # Fundamental ratios ARE present.
        assert m["roe"] == 0.10
        assert m["net_margin"] == 0.10


def test_sector_auto_applies_bank_ratios(tmp_path):
    # A bank vintage: net_interest_income + total_assets -> net_interest_margin computed,
    # and generic roic suppressed (set to None) by the sector overlay.
    bank_vintage = {
        "2019": {"a": {"fiscal_year": 2019, "accn": "a", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "total_equity": 1000.0,
                       "net_interest_income": 50.0, "total_assets": 2000.0}},
    }
    db = _build_db(tmp_path, bank_vintage, ticker="JPM", sector_class="bank")
    with PointInTimeMetrics.from_path(db) as pm:
        m = pm.metrics_as_of("JPM", 2019, "2020-06-30")
        assert m["net_interest_margin"] == 50.0 / 2000.0   # bank ratio present
        assert m["roic"] is None                            # generic ratio suppressed


def test_explicit_sector_overrides_table(tmp_path):
    # Table says bank, but the caller overrides with "general": no bank ratios added.
    bank_vintage = {
        "2019": {"a": {"fiscal_year": 2019, "accn": "a", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "total_equity": 1000.0,
                       "net_interest_income": 50.0, "total_assets": 2000.0}},
    }
    db = _build_db(tmp_path, bank_vintage, ticker="JPM", sector_class="bank")
    with PointInTimeMetrics.from_path(db) as pm:
        m = pm.metrics_as_of("JPM", 2019, "2020-06-30", sector="general")
        assert "net_interest_margin" not in m   # override won; generic suite only
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pit_metrics.py -k "point_in_time or not_yet or unknown or valuation or sector_auto or override" -v`
Expected: FAIL — `AttributeError: 'PointInTimeMetrics' object has no attribute 'metrics_as_of'`.

- [ ] **Step 3: Implement `metrics_as_of` and `metric_as_of`**

In `src/query/pit_metrics.py`, add these methods to `PointInTimeMetrics` (after `_sector`):

```python
    def metrics_as_of(
        self, ticker: str, fiscal_year: int, as_of_date: Any, sector: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """The fundamental ratio suite for ``(ticker, fiscal_year)`` as known on ``as_of_date``.

        Resolves the as-of financials via ``AsOfReader`` and runs the standard calculator on
        them. Returns ``None`` if the year had not been filed yet as of that date. ``sector``
        defaults to the company's ``sector_class`` (auto-read); pass a value to override.
        """
        period = self.reader.as_of_annual(ticker, fiscal_year, as_of_date)
        if period is None:
            return None
        sec = sector if sector is not None else self._sector(ticker)
        return self.calculator.calculate_all(period, sector=sec)

    def metric_as_of(
        self, ticker: str, fiscal_year: int, name: str, as_of_date: Any,
        sector: Optional[str] = None
    ) -> Any:
        """A single ratio's value as known on ``as_of_date`` (or ``None``)."""
        metrics = self.metrics_as_of(ticker, fiscal_year, as_of_date, sector=sector)
        return metrics.get(name) if metrics is not None else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_pit_metrics.py -v`
Expected: PASS (Task 1 + Task 2 tests).

- [ ] **Step 5: Run linters**

Run: `ruff check src tests && mypy`
Expected: ruff clean; bare `mypy` Success.

- [ ] **Step 6: Commit**

```bash
git add src/query/pit_metrics.py tests/test_pit_metrics.py
git commit -m "feat(query): metrics_as_of + metric_as_of (point-in-time ratios)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `metrics_history_as_of` (multi-year, per-year error capture)

Compute metrics for every year known as of `D`, reusing the resolver, never aborting the batch on a per-year error.

**Files:**
- Modify: `src/query/pit_metrics.py` (add `metrics_history_as_of`)
- Test: `tests/test_pit_metrics.py` (append)

**Interfaces:**
- Consumes: `self.reader.history_as_of` (SP2), `self._sector`, `self.calculator.calculate_all`.
- Produces: `metrics_history_as_of(ticker, as_of_date, years_back=None, sector=None) -> Dict[int, Dict[str, Any]]` — `{fiscal_year: metrics}`, newest first, only years filed ≤ `D`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pit_metrics.py`. Three years (2018/2019/2020), 2019 restated, each filed the February after close:

```python
def _three_years(tmp_path):
    def period(fy, accn, filed, ni):
        return {"fiscal_year": fy, "accn": accn, "filed_date": filed,
                "period_end": f"{fy}-12-31", "form": "10-K", "calendar_year": fy,
                "net_income": ni, "total_equity": 1000.0, "revenue": 1000.0}
    return _build_db(tmp_path, {
        "2018": {"k18": period(2018, "k18", "2019-02-15", 80.0)},
        "2019": {"k19": period(2019, "k19", "2020-02-15", 100.0),
                 "k19r": period(2019, "k19r", "2021-02-15", 90.0)},
        "2020": {"k20": period(2020, "k20", "2021-02-15", 110.0)},
    })


def test_history_metrics_only_filed_years_and_pit(tmp_path):
    db = _three_years(tmp_path)
    with PointInTimeMetrics.from_path(db) as pm:
        hist = pm.metrics_history_as_of("PRU", "2020-06-30")
        assert set(hist.keys()) == {2018, 2019}          # 2020 not yet filed
        assert hist[2019]["roe"] == 0.10                  # pre-restatement
        assert hist[2018]["roe"] == 0.08


def test_history_metrics_newest_first_and_years_back(tmp_path):
    db = _three_years(tmp_path)
    with PointInTimeMetrics.from_path(db) as pm:
        hist = pm.metrics_history_as_of("PRU", "2021-02-15", years_back=2)
        assert list(hist.keys()) == [2020, 2019]          # newest first, trimmed
        assert hist[2019]["roe"] == 0.09                  # restated by this date


def test_history_metrics_unknown_ticker_empty(tmp_path):
    db = _three_years(tmp_path)
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm.metrics_history_as_of("ZZZZ", "2025-01-01") == {}


class _RaisingCalculator:
    """Stub calculator that always raises — to exercise per-year error capture."""

    def calculate_all(self, financials, market_data=None, valuation=None, sector=None):
        raise ValueError("boom")


def test_history_metrics_captures_per_year_errors(tmp_path):
    from src.query.asof import AsOfReader
    db = _three_years(tmp_path)
    pm = PointInTimeMetrics(AsOfReader(db), calculator=_RaisingCalculator())
    hist = pm.metrics_history_as_of("PRU", "2021-02-15")
    pm.close()
    assert set(hist.keys()) == {2018, 2019, 2020}
    for fy in (2018, 2019, 2020):
        assert hist[fy] == {"error": "boom"}              # captured, not raised
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pit_metrics.py -k history -v`
Expected: FAIL — `AttributeError: 'PointInTimeMetrics' object has no attribute 'metrics_history_as_of'`.

- [ ] **Step 3: Implement `metrics_history_as_of`**

In `src/query/pit_metrics.py`, add to `PointInTimeMetrics` (after `metric_as_of`):

```python
    def metrics_history_as_of(
        self, ticker: str, as_of_date: Any, years_back: Optional[int] = None,
        sector: Optional[str] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Fundamental ratios for every fiscal year known as of ``as_of_date``.

        Each year uses its as-of financials (latest vintage filed ≤ the date). Keyed by
        fiscal_year, newest first; ``years_back`` trims to the most recent N. A per-year
        calculator failure is captured as ``{"error": ...}`` rather than aborting the batch.
        """
        periods = self.reader.history_as_of(ticker, as_of_date, years_back=years_back)
        if not periods:
            return {}
        sec = sector if sector is not None else self._sector(ticker)
        result: Dict[int, Dict[str, Any]] = {}
        for fy, period in periods.items():
            try:
                result[fy] = self.calculator.calculate_all(period, sector=sec)
            except Exception as e:  # noqa: BLE001 - mirror calculate_historical: never abort the batch
                self.logger.warning("PIT metrics error for %s FY%s: %s", ticker, fy, e)
                result[fy] = {"error": str(e)}
        return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_pit_metrics.py -v`
Expected: PASS (full file).

- [ ] **Step 5: Run linters**

Run: `ruff check src tests && mypy`
Expected: ruff clean; bare `mypy` Success.

- [ ] **Step 6: Commit**

```bash
git add src/query/pit_metrics.py tests/test_pit_metrics.py
git commit -m "feat(query): metrics_history_as_of multi-year point-in-time ratios

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: README docs, full verification, and live merge gate

**Files:**
- Modify: `README.md` (extend the "Point-in-time as-of queries" subsection)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: nothing new (docs + verification).

- [ ] **Step 1: Update the README**

In `README.md`, in the "### Point-in-time as-of queries" subsection, immediately after the line
`` `as_of_date` accepts an ISO `YYYY-MM-DD` string or a `datetime.date`. A year not yet filed `` …
`` as of that date resolves to `None` (and is absent from `history_as_of`). ``, add:

````markdown
For point-in-time **ratios** (computed on the as-of financials), use `PointInTimeMetrics`:

```python
from src.query.pit_metrics import PointInTimeMetrics

with PointInTimeMetrics.from_path("data/output/stock.db") as pm:
    roe = pm.metric_as_of("PRU", 2010, "roe", "2012-06-30")   # ROE as known on that date
    m = pm.metrics_as_of("PRU", 2010, "2012-06-30")           # full ratio dict, or None
    hist = pm.metrics_history_as_of("PRU", "2012-06-30")      # {fiscal_year: metrics}
```

Sector is auto-read from the `companies` table, so banks/insurers/REITs get their sector
ratios (e.g. `net_interest_margin`) with inapplicable generic ratios suppressed. Only
**fundamental** ratios are produced — valuation/EV ratios (`ev_to_ebitda`, `fcf_yield`, …)
are intentionally excluded, since they require the share price as of that date and no
historical per-date price series is stored.
````

- [ ] **Step 2: Run the full suite + linters**

Run: `python -m pytest -q && ruff check src tests && mypy`
Expected: all pass; ruff clean; bare `mypy` Success.

- [ ] **Step 3: Commit the docs**

```bash
git add README.md
git commit -m "docs: document PointInTimeMetrics (point-in-time ratios, PIT SP3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Run the live merge gate against the existing 50-stock DB**

This proves the ratio itself is point-in-time on live data. `data/output/stock.db` already exists from
earlier gates (PRU FY2010 has differing-`net_income` vintages; JPM is a bank). Run:

```bash
python - <<'PY'
import sqlite3
import datetime as dt
from src.query.pit_metrics import PointInTimeMetrics

db = "data/output/stock.db"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
# A PRU fiscal year reported by multiple filings with differing net_income.
rows = conn.execute(
    "SELECT fiscal_year, accn, filed_date, net_income FROM financials_annual_vintages "
    "WHERE ticker='PRU' AND net_income IS NOT NULL "
    "AND fiscal_year IN (SELECT fiscal_year FROM financials_annual_vintages "
    "  WHERE ticker='PRU' AND net_income IS NOT NULL "
    "  GROUP BY fiscal_year HAVING COUNT(DISTINCT net_income) > 1) "
    "ORDER BY fiscal_year, filed_date").fetchall()
conn.close()
assert rows, "expected a PRU multi-vintage fiscal year (from earlier gates)"
fy = rows[0][0]
vints = [r for r in rows if r[0] == fy]
orig_filed, last_filed = vints[0][2], vints[-1][2]
print("PRU FY", fy, "filings:", [(v[1], v[2], v[3]) for v in vints])

pm = PointInTimeMetrics.from_path(db)
# Find a fundamental ratio that is non-None at both dates and differs across the restatement.
candidates = ["roe", "roa", "net_margin", "operating_margin", "interest_coverage"]
m_orig = pm.metrics_as_of("PRU", fy, orig_filed)
m_last = pm.metrics_as_of("PRU", fy, last_filed)
switched = [c for c in candidates
            if m_orig.get(c) is not None and m_last.get(c) is not None
            and m_orig[c] != m_last[c]]
print("ratios that moved across the restatement:", switched,
      {c: (round(m_orig[c], 5), round(m_last[c], 5)) for c in switched})
assert switched, "expected at least one fundamental ratio to differ across the restatement"
# Not-yet-filed -> None.
before = (dt.date.fromisoformat(orig_filed) - dt.timedelta(days=1)).isoformat()
assert pm.metrics_as_of("PRU", fy, before) is None
# Valuation ratios excluded.
assert "ev_to_ebitda" not in m_orig and "fcf_yield" not in m_orig
# Bank sector ratios for JPM.
jpm_hist = pm.metrics_history_as_of("JPM", last_filed, years_back=3)
assert jpm_hist, "expected JPM metrics"
some_year = next(iter(jpm_hist))
print("JPM", some_year, "net_interest_margin:", jpm_hist[some_year].get("net_interest_margin"),
      "roic(suppressed):", jpm_hist[some_year].get("roic"))
assert jpm_hist[some_year].get("net_interest_margin") is not None, "JPM should have bank NIM"
pm.close()
print("LIVE GATE PASS: point-in-time ratios + sector-correct metrics confirmed")
PY
```
Expected: prints the PRU multi-vintage year and the fundamental ratio(s) that move across the
restatement (e.g. `roe`), confirms the pre-first-filing date returns `None`, confirms valuation keys
are absent, shows JPM with a non-None `net_interest_margin` (bank sector ratio), and prints
`LIVE GATE PASS`.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/pit-metrics
gh pr create --base main --title "Point-in-time: metrics (SP3)" \
  --body "Adds PointInTimeMetrics — a pure-read composition of AsOfReader (SP2) + CalculatedMetrics that computes the fundamental ratio suite on as-of-date financials, so ratios (ROE, ROIC, margins, sector ratios) are read as they were knowable on a given date (no look-ahead). Primary metrics_as_of; metric_as_of and metrics_history_as_of conveniences. Sector auto-read from the companies table; valuation/EV ratios excluded (no as-of price series). Read-only; no write-path or schema change. Completes the 3-part point-in-time arc (SP1 ingestion, SP2 as-of queries, SP3 metrics). Spec/plan in docs/superpowers/.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review

**Spec coverage:**
- Pure-read `PointInTimeMetrics` composing `AsOfReader` + `CalculatedMetrics`, no own ratio/SQL → Tasks 1-3. ✓
- Compose rule (as_of_annual → sector → calculate_all) → Task 2. ✓
- Sector auto-read from `companies` + override → Task 1 (`_sector`), Task 2 (`test_sector_auto_applies_bank_ratios`, `test_explicit_sector_overrides_table`). ✓
- `metrics_as_of` / `metric_as_of` / `metrics_history_as_of` surface → Tasks 2-3. ✓
- Valuation boundary (no market_data → EV keys absent) → Task 2 (`test_valuation_ratios_excluded`). ✓
- Edge cases: not-yet-filed→None, unknown→None/{}, per-year error capture, single surfaces → Tasks 2-3
  (`test_not_yet_filed_returns_none`, `test_unknown_ticker_or_year_returns_none`,
  `test_history_metrics_captures_per_year_errors`). ✓
- Unknown sector → generic suite: `_sector` returns None → `calculate_all(sector=None)` no-op
  (`test_sector_none_when_absent_or_unknown` + the default-sector fixtures). ✓
- README usage + boundary → Task 4. ✓
- mypy `files` updated → Task 1 Step 5. ✓
- Live PRU (ratio switch) + JPM (bank ratios) merge gate → Task 4 Step 4. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step is complete; every test asserts
real behavior. The one broad-except is deliberate (mirrors `calculate_historical`) and carries a
`# noqa: BLE001` with a reason.

**Type consistency:** `AsOfReader.conn -> sqlite3.Connection` (Task 1) is used by `_sector` (Task 1).
`PointInTimeMetrics(reader, calculator=None, logger=None)`, `from_path(db_path)`, `_sector(ticker) ->
Optional[str]` (Task 1); `metrics_as_of(ticker, fiscal_year, as_of_date, sector=None) ->
Optional[Dict[str, Any]]`, `metric_as_of(ticker, fiscal_year, name, as_of_date, sector=None) -> Any`
(Task 2); `metrics_history_as_of(ticker, as_of_date, years_back=None, sector=None) -> Dict[int,
Dict[str, Any]]` (Task 3) — all used with these exact signatures in later tasks, the README, and the
live gate. `calculate_all(period, sector=sec)` matches the existing engine signature
(`calculate_all(financials, market_data=None, valuation=None, sector=None)`), called without
`market_data` so valuation keys never appear. The `_RaisingCalculator` stub mirrors that signature.
