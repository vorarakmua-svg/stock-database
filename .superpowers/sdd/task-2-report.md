# Task 2 Report — Reader DAL + Shared Fixtures

## TDD RED/GREEN

### RED (before repository.py)
```
pytest tests/test_web_repository.py -q
ERROR collecting tests/test_web_repository.py
ModuleNotFoundError: No module named 'src.webapp.repository'
```

### GREEN (after implementation)
```
pytest tests/test_web_repository.py -v
66 passed in 3.58s
```

## Files Changed

| File | Action |
|------|--------|
| `src/webapp/repository.py` | Created — Reader class, all 14 methods |
| `src/webapp/dependencies.py` | Updated — added get_reader (HTTP 503 on missing DB) |
| `tests/conftest.py` | Updated — appended web_db and client fixtures |
| `tests/test_web_repository.py` | Created — 66 tests |

## Full Gate Results

- `ruff check src/ tests/`: **clean** (1 auto-fixed import sort, 2 unused imports removed)
- `mypy`: **clean** (22 source files, no issues)
- `pytest -q`: **275 passed** (209 baseline + 66 new)

## Self-Review

### Completeness vs brief deliverables
- All 14 Reader methods implemented: list_companies, count_companies, get_company, search_companies, distinct_sectors, company_overview, annual_financials, quarterly_financials, ttm_financials, annual_metrics, metric_series, financial_series, latest_snapshot, snapshot_history. ✓
- get_reader dependency with 503 + yield/finally pattern. ✓
- web_db: 3 companies across sectors, 3 fiscal years for AAA, multi-vintage FY2022 (original + restatement), 2 quarterly periods, 1 TTM, 2 snapshots (different timestamps), 1 unmapped fact, 1 collection_runs row per company. ✓
- client fixture: TestClient(create_app(db_path=web_db)). ✓
- Tests assert real fixture values (e.g., revenue==1000.0, roic==0.15, current_price==105.0 for latest snapshot). ✓
- ValueError on injection attempt and unlisted column names tested for both metric_series and financial_series. ✓
- No screener/vintage/quality methods added (YAGNI). ✓
- No FastAPI routes or pydantic schemas. ✓

### 3.9-safe typing
- `from __future__ import annotations` in repository.py and dependencies.py. ✓
- All annotations use `typing.Optional/Union/List/Dict/Any` (no runtime `X | None`). ✓
- `frozenset[str]` annotation is safe under `from __future__ import annotations`. ✓

### Design notes
- Imported `_SNAPSHOT_COLUMNS` per brief requirement; used it to populate `_SNAPSHOT_COL_SET` (module-level frozenset) so ruff does not flag it as an unused import. The set is available for future snapshot-field filtering tasks.
- Used `_METRIC_COL_SET` and `_CANONICAL_COL_SET` (frozenset) for O(1) whitelist lookups in series methods.
- Connection pattern copied verbatim from `asof.py`: `sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)` + `row_factory = sqlite3.Row`, plus `close()`, `__enter__`, `__exit__`, `conn` property.
- `Reader.__init__` explicitly checks `Path.exists()` before opening, raising `FileNotFoundError` (the dependency converts this to a 503, but the check is also in `get_reader` so the Reader never even opens on a missing file via the dependency path).

### Concerns
None. All brief deliverables are met, the gate is green, and no overbuilding was done.

---

## Fix — Review finding: get_reader dependency untested (2026-06-30)

### Tests added to `tests/test_web_repository.py`

1. **`test_get_reader_raises_503_when_db_missing`** — creates a `MagicMock` settings
   object with `db_path` pointing to a nonexistent file, drives the generator with
   `next(gen)`, and asserts `HTTPException` with `status_code == 503`.

2. **`test_get_reader_yields_reader_and_closes_on_teardown`** — creates a `MagicMock`
   settings object with `db_path = web_db`, calls `next(gen)` to receive a `Reader`,
   exhausts the generator with `next(gen)` inside `pytest.raises(StopIteration)` to
   trigger the `finally` block, then asserts the connection is closed by expecting
   `sqlite3.ProgrammingError` on `r.conn.execute("SELECT 1")`.

### Commands run

```
pytest tests/test_web_repository.py -q   # 68 passed
ruff check src/ tests/                   # All checks passed!
mypy tests/test_web_repository.py src/webapp/  # Success: no issues found in 7 source files
pytest -q                                # 277 passed in 6.88s
```

### Commit

`991a424` test(webapp): cover get_reader 503 and teardown (Task 2 review fix)
