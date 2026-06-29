# Point-in-Time As-Of-Date Query API (Sub-Project 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `AsOfReader` that resolves each annual period to the latest filing made on or before a given date, so backtests can read fundamentals with no look-ahead bias.

**Architecture:** A new read-only package `src/query/` consumes the SP1 `financials_annual_vintages` table (and its `idx_fav_asof` index) via a `mode=ro` SQLite connection. One primary method (`as_of_annual`, returns the whole resolved period dict) plus two thin conveniences (`as_of_value`, `history_as_of`). No write-path or schema change.

**Tech Stack:** Python 3.9, SQLite (stdlib `sqlite3`), pytest.

## Global Constraints

- Python 3.9 floor — no `X | Y` union syntax (use `Optional[...]`, `Dict[...]`, `Any`).
- ruff clean: line-length 120; rules E, F, W, I; imports at top of file (avoid E402).
- mypy gate is **bare `mypy`** (project scopes via pyproject `[tool.mypy] files=[...]`). `src/query/asof.py` MUST be added to that `files` list so it is type-gated. Do NOT run `mypy src`.
- Read-only DB access (`sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`); never mutate any table.
- Resolution rule: latest `filed_date <= as_of_date`; inclusive boundary; deterministic `filed_date DESC, accn DESC` tie-break; `None`/`{}` when nothing was filed ≤ D.
- Commit trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `src/query/__init__.py` — new package marker, exports `AsOfReader`.
- `src/query/asof.py` — the `AsOfReader` class (connection lifecycle + the three query methods).
- `tests/test_asof_reader.py` — unit tests against a hand-built temp DB.
- `pyproject.toml` — add `src/query/asof.py` to `[tool.mypy] files`.
- `README.md` — "Point-in-time as-of queries" subsection (raw-SQL parity).

---

### Task 1: Package skeleton + read-only connection lifecycle

Create the package and the `AsOfReader` shell: a read-only connection, `close()`, and context-manager support. No query methods yet — this task proves the read-only guarantee in isolation.

**Files:**
- Create: `src/query/__init__.py`
- Create: `src/query/asof.py`
- Create: `tests/test_asof_reader.py`
- Modify: `pyproject.toml` (`[tool.mypy] files` list)

**Interfaces:**
- Consumes: the `financials_annual_vintages` table created by `src/exporters/sqlite_store.py` (SP1).
- Produces: `AsOfReader(db_path, logger=None)` with `.close()`, `__enter__`/`__exit__`, and a live read-only `sqlite3.Connection` at `self._conn` with `row_factory = sqlite3.Row`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_asof_reader.py`:

```python
"""Tests for the point-in-time as-of-date query API (no look-ahead)."""

import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.query.asof import AsOfReader


def _build_db(tmp_path, vintages):
    """Write a StockData carrying the given annual vintages and return the db path.

    vintages: {fy_str: {accn: period_dict}} exactly as StockData.financials_annual_vintages.
    """
    s = StockData(ticker="PRU", cik="1", company_name="Prudential")
    s.financials_annual_vintages = vintages
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([s])
    return str(db)


def test_reader_connection_is_read_only(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    reader = AsOfReader(db)
    with pytest.raises(sqlite3.OperationalError):
        reader._conn.execute(
            "INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn) "
            "VALUES ('X', 1, 'y')"
        )
    reader.close()


def test_reader_context_manager_closes(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    with AsOfReader(db) as reader:
        assert reader._conn is not None
    # After exit, using the closed connection raises.
    with pytest.raises(sqlite3.ProgrammingError):
        reader._conn.execute("SELECT 1")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_asof_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.query'`.

- [ ] **Step 3: Create the package**

Create `src/query/__init__.py`:

```python
"""Read-only query APIs over the standardized SQLite store."""

from .asof import AsOfReader

__all__ = ["AsOfReader"]
```

- [ ] **Step 4: Create the `AsOfReader` shell**

Create `src/query/asof.py`:

```python
"""Point-in-time as-of-date queries over the vintaged annual store.

A read-only consumer of the ``financials_annual_vintages`` table (see
``exporters/sqlite_store.py``). For a given date ``D``, each annual period resolves to
the latest filing made on or before ``D`` — so backtests read fundamentals with no
look-ahead bias. The connection is opened ``mode=ro``; the reader never mutates data.
"""

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Accepts an ISO ``YYYY-MM-DD`` string or a date/datetime (normalized before querying).
AsOfDate = Union[str, "date"]


class AsOfReader:
    """Resolve vintaged annual data as it was known on a given date."""

    def __init__(self, db_path: Union[str, Path],
                 logger: Optional[logging.Logger] = None) -> None:
        self.db_path = Path(db_path)
        self.logger = logger or logging.getLogger(__name__)
        self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AsOfReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def _norm_date(as_of_date: AsOfDate) -> str:
        """Normalize a date/datetime or ISO string to ``YYYY-MM-DD`` for comparison."""
        if isinstance(as_of_date, datetime):
            return as_of_date.date().isoformat()
        if isinstance(as_of_date, date):
            return as_of_date.isoformat()
        return str(as_of_date)
```

- [ ] **Step 5: Add the module to the mypy files list**

In `pyproject.toml`, under `[tool.mypy]`, add `"src/query/asof.py",` to the `files = [...]` list (e.g. after the `"src/exporters/sqlite_store.py",` entry):

```toml
    "src/exporters/sqlite_store.py",
    "src/query/asof.py",
]
```

- [ ] **Step 6: Run the tests + linters to verify green**

Run: `python -m pytest tests/test_asof_reader.py -v && ruff check src tests && mypy`
Expected: both tests PASS; ruff clean; bare `mypy` Success (now covering `src/query/asof.py`).

- [ ] **Step 7: Commit**

```bash
git add src/query/__init__.py src/query/asof.py tests/test_asof_reader.py pyproject.toml
git commit -m "feat(query): AsOfReader skeleton with read-only connection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `as_of_annual` + `as_of_value` (the core resolver)

Implement the primary row resolver and its scalar convenience.

**Files:**
- Modify: `src/query/asof.py` (add `as_of_annual`, `as_of_value`)
- Test: `tests/test_asof_reader.py` (append)

**Interfaces:**
- Consumes: `self._conn` (read-only), `self._norm_date` (Task 1).
- Produces:
  - `as_of_annual(ticker: str, fiscal_year: int, as_of_date: AsOfDate) -> Optional[Dict[str, Any]]`
  - `as_of_value(ticker: str, fiscal_year: int, field: str, as_of_date: AsOfDate) -> Any`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_asof_reader.py`. These use a fiscal year with two vintages — an original filed 2020-02-15 (net_income 100) and a restatement filed 2021-02-15 (net_income 90):

```python
def _pru_two_vintages(tmp_path):
    return _build_db(tmp_path, {
        "2019": {
            "a-orig": {"fiscal_year": 2019, "accn": "a-orig", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "revenue": 1000.0},
            "b-restate": {"fiscal_year": 2019, "accn": "b-restate", "filed_date": "2021-02-15",
                          "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                          "net_income": 90.0, "revenue": 1000.0},
        },
    })


def test_not_yet_filed_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Before the original filing, FY2019 was not yet known.
        assert r.as_of_annual("PRU", 2019, "2020-02-14") is None


def test_boundary_is_inclusive(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # On the exact filing date, the original is visible.
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row is not None
        assert row["net_income"] == 100.0
        assert row["accn"] == "a-orig"


def test_restatement_switch(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Between the two filings: still the original.
        assert r.as_of_annual("PRU", 2019, "2020-06-30")["net_income"] == 100.0
        # On/after the restatement: the restated value.
        assert r.as_of_annual("PRU", 2019, "2021-02-15")["net_income"] == 90.0
        assert r.as_of_annual("PRU", 2019, "2025-01-01")["net_income"] == 90.0


def test_row_carries_provenance_metadata(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-06-30")
        assert row["fiscal_year"] == 2019
        assert row["accn"] == "a-orig"
        assert row["filed_date"] == "2020-02-15"
        assert row["period_end"] == "2019-12-31"
        assert row["form"] == "10-K"


def test_accepts_date_object(tmp_path):
    from datetime import date
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("PRU", 2019, date(2020, 6, 30))["net_income"] == 100.0


def test_same_day_tie_break_prefers_higher_accn(tmp_path):
    # Two filings on the same date: deterministic accn DESC tie-break.
    db = _build_db(tmp_path, {
        "2019": {
            "0001-10K": {"fiscal_year": 2019, "accn": "0001-10K", "filed_date": "2020-02-15",
                         "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                         "net_income": 100.0},
            "0002-10KA": {"fiscal_year": 2019, "accn": "0002-10KA", "filed_date": "2020-02-15",
                          "period_end": "2019-12-31", "form": "10-K/A", "calendar_year": 2019,
                          "net_income": 105.0},
        },
    })
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row["accn"] == "0002-10KA"
        assert row["net_income"] == 105.0


def test_unknown_ticker_or_year_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("ZZZZ", 2019, "2025-01-01") is None
        assert r.as_of_annual("PRU", 1990, "2025-01-01") is None


def test_as_of_value_delegates(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2019, "net_income", "2020-06-30") == 100.0
        assert r.as_of_value("PRU", 2019, "net_income", "2021-02-15") == 90.0
        # Not yet filed → None passthrough.
        assert r.as_of_value("PRU", 2019, "net_income", "2019-01-01") is None
        # Missing field → None.
        assert r.as_of_value("PRU", 2019, "no_such_field", "2025-01-01") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_asof_reader.py -k "as_of or restatement or boundary or tie_break or provenance or not_yet or date_object or unknown" -v`
Expected: FAIL — `AttributeError: 'AsOfReader' object has no attribute 'as_of_annual'`.

- [ ] **Step 3: Implement `as_of_annual` and `as_of_value`**

In `src/query/asof.py`, add these methods to `AsOfReader` (after `_norm_date`):

```python
    def as_of_annual(
        self, ticker: str, fiscal_year: int, as_of_date: AsOfDate
    ) -> Optional[Dict[str, Any]]:
        """The annual period for ``(ticker, fiscal_year)`` as known on ``as_of_date``.

        Returns the latest vintage filed on or before ``as_of_date`` as a plain dict
        (all canonical line items + provenance metadata), or ``None`` if the year had
        not been filed yet as of that date.
        """
        cutoff = self._norm_date(as_of_date)
        cur = self._conn.execute(
            "SELECT * FROM financials_annual_vintages "
            "WHERE ticker = ? AND fiscal_year = ? AND filed_date <= ? "
            "ORDER BY filed_date DESC, accn DESC LIMIT 1",
            (ticker, int(fiscal_year), cutoff),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def as_of_value(
        self, ticker: str, fiscal_year: int, field: str, as_of_date: AsOfDate
    ) -> Any:
        """A single canonical field's value as known on ``as_of_date`` (or ``None``)."""
        row = self.as_of_annual(ticker, fiscal_year, as_of_date)
        return row.get(field) if row is not None else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_asof_reader.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Run linters**

Run: `ruff check src tests && mypy`
Expected: ruff clean; bare `mypy` Success.

- [ ] **Step 6: Commit**

```bash
git add src/query/asof.py tests/test_asof_reader.py
git commit -m "feat(query): as_of_annual + as_of_value resolver (no look-ahead)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `history_as_of` (multi-year snapshot)

Resolve every fiscal year known as of `D` in one call, reusing the single resolver.

**Files:**
- Modify: `src/query/asof.py` (add `history_as_of`)
- Test: `tests/test_asof_reader.py` (append)

**Interfaces:**
- Consumes: `self._conn`, `self._norm_date`, `as_of_annual` (Task 2).
- Produces: `history_as_of(ticker: str, as_of_date: AsOfDate, years_back: Optional[int] = None) -> Dict[int, Dict[str, Any]]` — `{fiscal_year: period}`, newest first, only years with a vintage filed ≤ `D`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_asof_reader.py`. This DB has three fiscal years, each filed the February after its close, plus a 2019 restatement:

```python
def _three_years(tmp_path):
    def period(fy, accn, filed, ni):
        return {"fiscal_year": fy, "accn": accn, "filed_date": filed,
                "period_end": f"{fy}-12-31", "form": "10-K", "calendar_year": fy,
                "net_income": ni}
    return _build_db(tmp_path, {
        "2018": {"k18": period(2018, "k18", "2019-02-15", 80.0)},
        "2019": {"k19": period(2019, "k19", "2020-02-15", 100.0),
                 "k19r": period(2019, "k19r", "2021-02-15", 90.0)},
        "2020": {"k20": period(2020, "k20", "2021-02-15", 110.0)},
    })


def test_history_as_of_only_includes_filed_years(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        # As of mid-2020, FY2020 has not been filed yet (filed 2021-02-15).
        hist = r.history_as_of("PRU", "2020-06-30")
        assert set(hist.keys()) == {2018, 2019}
        assert hist[2019]["net_income"] == 100.0  # pre-restatement
        assert hist[2018]["net_income"] == 80.0


def test_history_as_of_reflects_restatement_and_full_set(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        hist = r.history_as_of("PRU", "2021-02-15")
        assert set(hist.keys()) == {2018, 2019, 2020}
        assert hist[2019]["net_income"] == 90.0  # restated by this date
        assert hist[2020]["net_income"] == 110.0


def test_history_as_of_newest_first_and_years_back(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        hist = r.history_as_of("PRU", "2021-02-15", years_back=2)
        assert list(hist.keys()) == [2020, 2019]  # newest first, trimmed to 2


def test_history_as_of_unknown_ticker_empty(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        assert r.history_as_of("ZZZZ", "2025-01-01") == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_asof_reader.py -k history -v`
Expected: FAIL — `AttributeError: 'AsOfReader' object has no attribute 'history_as_of'`.

- [ ] **Step 3: Implement `history_as_of`**

In `src/query/asof.py`, add to `AsOfReader` (after `as_of_value`):

```python
    def history_as_of(
        self, ticker: str, as_of_date: AsOfDate, years_back: Optional[int] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Every fiscal year known as of ``as_of_date``, each resolved to its latest
        vintage filed ≤ that date. Keyed by fiscal_year, newest first; ``years_back``
        trims to the most recent N years.
        """
        cutoff = self._norm_date(as_of_date)
        cur = self._conn.execute(
            "SELECT DISTINCT fiscal_year FROM financials_annual_vintages "
            "WHERE ticker = ? AND filed_date <= ? "
            "ORDER BY fiscal_year DESC",
            (ticker, cutoff),
        )
        years = [row["fiscal_year"] for row in cur.fetchall()]
        if years_back:
            years = years[:years_back]
        result: Dict[int, Dict[str, Any]] = {}
        for fy in years:
            period = self.as_of_annual(ticker, fy, cutoff)
            if period is not None:
                result[int(fy)] = period
        return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_asof_reader.py -v`
Expected: PASS (full file).

- [ ] **Step 5: Run linters**

Run: `ruff check src tests && mypy`
Expected: ruff clean; bare `mypy` Success.

- [ ] **Step 6: Commit**

```bash
git add src/query/asof.py tests/test_asof_reader.py
git commit -m "feat(query): history_as_of multi-year point-in-time snapshot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: README docs, full verification, and live merge gate

**Files:**
- Modify: `README.md` (add a "Point-in-time as-of queries" subsection)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: nothing new (docs + verification).

- [ ] **Step 1: Update the README**

In `README.md`, immediately after the `financials_annual_vintages` paragraph (added in SP1, near the end of the SQLite section), add:

````markdown
### Point-in-time as-of queries

To read fundamentals **as they were known on a given date** (no look-ahead), resolve each
year to the latest filing made on or before that date. In SQL:

```sql
-- Net income for FY2019 as it was known on 2020-06-30:
SELECT * FROM financials_annual_vintages
WHERE ticker = 'PRU' AND fiscal_year = 2019 AND filed_date <= '2020-06-30'
ORDER BY filed_date DESC, accn DESC LIMIT 1;
```

Or in Python via `AsOfReader`:

```python
from src.query.asof import AsOfReader

with AsOfReader("data/output/stock.db") as r:
    period = r.as_of_annual("PRU", 2019, "2020-06-30")   # full period dict, or None
    ni = r.as_of_value("PRU", 2019, "net_income", "2020-06-30")
    history = r.history_as_of("PRU", "2020-06-30")        # {fiscal_year: period}, newest first
```

`as_of_date` accepts an ISO `YYYY-MM-DD` string or a `datetime.date`. A year not yet filed
as of that date resolves to `None` (and is absent from `history_as_of`).
````

- [ ] **Step 2: Run the full suite + linters**

Run: `python -m pytest -q && ruff check src tests && mypy`
Expected: all pass; ruff clean; bare `mypy` Success.

- [ ] **Step 3: Commit the docs**

```bash
git add README.md
git commit -m "docs: document AsOfReader point-in-time queries (PIT SP2)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Run the live merge gate against the existing 50-stock DB**

This proves real no-look-ahead on live data. `data/output/stock.db` already exists from the SP1 gate
(PRU has fiscal years with multiple differing-`net_income` vintages). Run:

```bash
python - <<'PY'
import sqlite3
from src.query.asof import AsOfReader

db = "data/output/stock.db"
# Find a PRU fiscal year with two differing-net_income vintages and their filing dates.
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = conn.execute(
    "SELECT fiscal_year, accn, filed_date, net_income FROM financials_annual_vintages "
    "WHERE ticker='PRU' AND net_income IS NOT NULL "
    "AND fiscal_year IN (SELECT fiscal_year FROM financials_annual_vintages "
    "  WHERE ticker='PRU' AND net_income IS NOT NULL "
    "  GROUP BY fiscal_year HAVING COUNT(DISTINCT net_income) > 1) "
    "ORDER BY fiscal_year, filed_date").fetchall()
conn.close()
assert rows, "expected PRU to have a multi-vintage fiscal year (from SP1 gate)"
fy = rows[0][0]
vints = [r for r in rows if r[0] == fy]
print("PRU FY", fy, "vintages (accn, filed, ni):", [(v[1], v[2], v[3]) for v in vints])
orig_filed, orig_ni = vints[0][2], vints[0][3]
last_filed, last_ni = vints[-1][2], vints[-1][3]
assert orig_ni != last_ni, "need differing net_income to prove the switch"

r = AsOfReader(db)
# A day before the original filing: not yet known.
import datetime as dt
before = (dt.date.fromisoformat(orig_filed) - dt.timedelta(days=1)).isoformat()
assert r.as_of_annual("PRU", fy, before) is None, "should be unknown before first filing"
# On the original filing date: original value.
on_orig = r.as_of_value("PRU", fy, "net_income", orig_filed)
print("as_of", orig_filed, "net_income =", on_orig, "(expect", orig_ni, ")")
assert on_orig == orig_ni
# On/after the last filing: restated value.
on_last = r.as_of_value("PRU", fy, "net_income", last_filed)
print("as_of", last_filed, "net_income =", on_last, "(expect", last_ni, ")")
assert on_last == last_ni
# history_as_of includes only years filed by the cutoff.
hist = r.history_as_of("PRU", last_filed)
print("history_as_of years:", sorted(hist.keys(), reverse=True)[:5], "... total", len(hist))
assert fy in hist and hist[fy]["net_income"] == last_ni
r.close()
print("LIVE GATE PASS: real no-look-ahead confirmed on PRU FY", fy)
PY
```
Expected: prints the PRU multi-vintage year, shows `net_income` switching from the original to the
restated value as the as-of date crosses the restatement filing, confirms the pre-first-filing date
returns `None`, and prints `LIVE GATE PASS`.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/pit-asof-query
gh pr create --base main --title "Point-in-time: as-of-date query API (SP2)" \
  --body "Adds a read-only AsOfReader that resolves each annual period to the latest filing made on or before a given date (latest filed_date <= D, inclusive, deterministic accn tie-break) — no look-ahead. Primary as_of_annual returns the full resolved period dict; as_of_value and history_as_of are thin conveniences. Read-only (mode=ro) consumer of the SP1 financials_annual_vintages table + idx_fav_asof index; no write-path or schema change. Sub-project 2 of 3 (point-in-time metrics follow). Spec/plan in docs/superpowers/.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review

**Spec coverage:**
- Read-only module `src/query/asof.py` + `AsOfReader` with read-only connection → Task 1. ✓
- Resolution rule (latest filed ≤ D, inclusive, `accn` tie-break, `None` when unfiled) → Task 2
  (`test_boundary_is_inclusive`, `test_not_yet_filed_returns_none`, `test_same_day_tie_break_prefers_higher_accn`). ✓
- `as_of_annual` returns full dict + provenance metadata, registry-decoupled (`SELECT *`) → Task 2
  (`test_row_carries_provenance_metadata`). ✓
- `as_of_value` convenience + None/missing-field passthrough → Task 2 (`test_as_of_value_delegates`). ✓
- `history_as_of` multi-year, newest-first, `years_back`, only filed years → Task 3. ✓
- `as_of_date` accepts str or date/datetime → Task 1 `_norm_date` + Task 2 `test_accepts_date_object`. ✓
- Read-only guard (write raises) → Task 1 `test_reader_connection_is_read_only`. ✓
- README SQL + Python parity → Task 4. ✓
- mypy `files` list updated → Task 1 Step 5. ✓
- Live PRU merge gate (real no-look-ahead) → Task 4 Step 4. ✓
- Edge cases: unknown ticker/year → Task 2/3; missing DB → surfaces at connect (inherent, `mode=ro`
  raises `OperationalError` on a missing file — not silently swallowed). ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step contains complete code; every
test step contains real assertions.

**Type consistency:** `AsOfReader.__init__(db_path, logger=None)`, `_conn`, `_norm_date(as_of_date) -> str`,
`as_of_annual(ticker, fiscal_year, as_of_date) -> Optional[Dict[str, Any]]`,
`as_of_value(ticker, fiscal_year, field, as_of_date) -> Any`,
`history_as_of(ticker, as_of_date, years_back=None) -> Dict[int, Dict[str, Any]]` are defined in Tasks
1-3 and used with those exact signatures in later tasks, the README (Task 4), and the live gate. The
`AsOfDate = Union[str, date]` alias is defined once (Task 1) and referenced throughout. Table/column
names (`financials_annual_vintages`, `ticker`, `fiscal_year`, `accn`, `filed_date`, `net_income`) match
the SP1 schema in `sqlite_store.py`.
