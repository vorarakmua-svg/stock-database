# Point-in-Time: As-Of-Date Query API (Sub-Project 2) — Design Spec

**Date:** 2026-06-29
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/pit-asof-query` (off `main`)

## Context

Sub-project 1 (merged, PR #17) landed the `financials_annual_vintages` table — every filing's view of
each annual period, keyed `(ticker, fiscal_year, accn)` with its `filed_date`, plus the
`idx_fav_asof (ticker, fiscal_year, filed_date)` index. It stored the data but exposed no way to read
it point-in-time.

This sub-project adds the **first read API** in the codebase. Today the pipeline only *writes* the DB;
users query it with raw SQL (README examples). SP2 introduces an as-of-date resolver: given a date `D`,
return each annual period **as it was known on D** — the latest filing made on or before `D` — with no
look-ahead. This is the building block SP3 (point-in-time metrics) will compute ratios on.

This is the second of three point-in-time sub-projects:

1. **Vintaged annual ingestion + storage** — shipped (PR #17).
2. **As-of-date query API** (this spec) — resolve `(ticker, fiscal_year, as_of=D)` to the latest vintage
   filed ≤ D.
3. **Point-in-time metrics** — ratios computed on as-of data (later).

### Decisions (from brainstorming)
- **Primary shape: full period row.** The main method returns the whole canonical period dict (all line
  items + provenance metadata), with a scalar accessor and a multi-year series as thin conveniences on
  top. SP3 metrics need whole rows, not single fields.
- **Delivery: Python read API + README SQL parity.** A new read-only module/class, plus the equivalent
  raw-SQL pattern documented for SQL users. No CLI subcommand yet (YAGNI until there is a consumer).
- **Read-only.** Vintages are never mutated; the reader opens the DB read-only so no-look-ahead is a
  structural guarantee, not a convention.

## 1. Module, class, and the core resolution rule

New **read-only** package `src/query/` (`__init__.py`, `asof.py`), separate from `SQLiteStore` (which
stays write-only — clean separation of concerns). One class:

```python
class AsOfReader:
    def __init__(self, db_path, logger=None): ...   # opens a read-only connection
    def close(self) -> None: ...
    def __enter__(self) -> "AsOfReader": ...
    def __exit__(self, *exc) -> None: ...
```

It opens the DB with `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` and
`conn.row_factory = sqlite3.Row`, so a reader can never mutate data.

**Resolution rule (the whole point of SP2):** for a `(ticker, fiscal_year)`, the value "as of date `D`"
is the vintage with the **latest `filed_date ≤ D`**. If no vintage was filed on or before `D` (the year
had not been reported yet as of `D`), the answer is `None` — this is what eliminates look-ahead. The
query is a single index-friendly statement riding `idx_fav_asof`:

```sql
SELECT * FROM financials_annual_vintages
WHERE ticker = ? AND fiscal_year = ? AND filed_date <= ?
ORDER BY filed_date DESC, accn DESC LIMIT 1
```

- `filed_date` is stored as ISO `YYYY-MM-DD`, so string comparison is correct.
- `as_of_date` accepts a `str` (ISO `YYYY-MM-DD`) or a `date`/`datetime`, normalized to that string form
  before the query.
- The boundary is **inclusive**: a filing made exactly on `D` is visible at `as_of=D`.
- `filed_date DESC, accn DESC` makes the result **deterministic** when two filings share a `filed_date`
  (e.g. a 10-K and a same-day amendment) — the higher `accn` wins.

## 2. Method surface

Three methods — the row resolver is primary; the other two are thin conveniences over it (DRY: one
resolution path).

```python
def as_of_annual(self, ticker, fiscal_year, as_of_date) -> Optional[Dict[str, Any]]
```
The primary. Runs the Section-1 query and returns the whole resolved vintage as a plain `dict` — every
canonical line item **plus** provenance metadata (`fiscal_year`, `accn`, `filed_date`, `period_end`,
`form`, `calendar_year`). Uses `SELECT *` + `dict(row)`, so it stays decoupled from the canonical
registry — new canonical columns appear automatically with no change here. Returns `None` when nothing
was filed ≤ `D`.

```python
def as_of_value(self, ticker, fiscal_year, field, as_of_date) -> Any
```
Convenience: `as_of_annual(...)` then `.get(field)`; `None` if the row is `None` or the field is absent.

```python
def history_as_of(self, ticker, as_of_date, years_back=None) -> Dict[int, Dict[str, Any]]
```
The multi-year snapshot: every fiscal year that had **any** vintage filed ≤ `D`, each resolved with the
same rule, keyed by `fiscal_year` (int), newest first; `years_back` trims to the most recent N.
Implemented by discovering the distinct fiscal years with `filed_date ≤ D` (one indexed query) and
calling `as_of_annual` per year — reuses the single resolution path, no duplicated SQL.

### Edge cases (all explicit)
- **Not yet filed as of `D`** → `None` (resolver) / absent from the dict (`history_as_of`). *The*
  no-look-ahead case.
- **`D` after the latest restatement** → returns the most recent vintage (all that is knowable).
- **Unknown ticker / fiscal_year** → `None` / `{}`.
- **Same-day filings** → deterministic `accn DESC` tie-break.
- **Missing DB file or table** → surfaces as a normal error at connect/query time; not silently
  swallowed.

## 3. README — raw-SQL parity

Add a short "Point-in-time as-of queries" subsection under the SQLite docs giving the equivalent
correlated pattern, so SQL users get the resolver the index was built for:

```sql
-- Revenue / net income for FY2019 *as it was known on 2020-06-30* (no look-ahead):
SELECT * FROM financials_annual_vintages
WHERE ticker = 'PRU' AND fiscal_year = 2019 AND filed_date <= '2020-06-30'
ORDER BY filed_date DESC, accn DESC LIMIT 1;
```

## Architecture / scope

- **New:** `src/query/__init__.py`, `src/query/asof.py` (`AsOfReader`), `tests/test_asof_reader.py`,
  README subsection.
- **Untouched:** the writer (`sqlite_store.py`), the schema (SP2 is a read-only consumer of the SP1
  table/index), the parser, models, fetcher, validation. SP2 adds **zero** write-path or schema change.

## Out of scope (YAGNI)

- Point-in-time *metrics* (SP3 — this returns line items, not ratios).
- Quarterly vintages.
- CLI subcommand.
- Caching / connection pooling beyond one held read-only connection.

## Testing (TDD)

Pure unit tests with a hand-built temp DB of synthetic vintages (no network):

- **Boundary inclusive:** a filing dated exactly `D` is visible at `as_of=D`, invisible at `D − 1 day`.
- **Restatement switch:** before the restatement's `filed_date`, `as_of_annual` returns the original
  value; on/after it, the restated value.
- **Not-yet-filed → `None`**, and absent from `history_as_of`.
- **Deterministic tie-break:** two same-`filed_date` vintages → higher `accn` wins.
- **`history_as_of` + `years_back`:** correct per-year resolution and trim.
- **Read-only guard:** an attempted write through the reader's connection raises.
- **`as_of_value`:** delegates correctly, including `None` passthrough.
- Full suite + ruff + bare `mypy` green.

## Global constraints

- Python 3.9 (no `X | Y` unions); ruff (line-length 120; E,F,W,I; imports at top — avoid E402).
- mypy gate is **bare `mypy`** (project scopes via `pyproject` `[tool.mypy] files=[...]`). **Add
  `src/query/asof.py` to that `files` list** so the new module is type-gated and stays clean. Do NOT run
  `mypy src` (it surfaces pre-existing errors in unrelated legacy files).
- Read-only DB access (`mode=ro`); no mutation of any table.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Against the existing 50-stock `data/output/stock.db`, instantiate `AsOfReader` and confirm on a known
restater (e.g. **PRU**, which SP1 verified has fiscal years with multiple differing-`net_income`
vintages):

- For such a fiscal year, an `as_of_date` **between** the original and restated filings returns the
  **original** value; an `as_of_date` **after** the restatement returns the **restated** value (real
  no-look-ahead on live data).
- `history_as_of(ticker, as_of_date)` returns the expected set of fiscal years (only those filed ≤ `D`).
- Full suite + ruff + bare `mypy` green.

Only then merge.
