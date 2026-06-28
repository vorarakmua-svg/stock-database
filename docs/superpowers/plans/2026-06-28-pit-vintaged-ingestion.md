# Point-in-Time Vintaged Annual Ingestion (Sub-Project 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture and persist every filing's (accession's) view of each annual period — vintages — into an additive `financials_annual_vintages` SQLite table, so later sub-projects can answer "what was knowable as of date D" without look-ahead bias.

**Architecture:** `companyfacts` already retains every historical fact instance with its `accn` + `filed` date, so a new parser method buckets those by `(fiscal_year, accn)` (reusing a shared resolution helper extracted from `_resolve_canonical`). Vintages flow onto `StockData` (SQLite-only, kept out of JSON), get per-vintage derivations, and are written to a new additive table keyed `(ticker, fiscal_year, accn)`. No existing table, query, or behavior changes.

**Tech Stack:** Python 3.9, SQLite (stdlib `sqlite3`), pytest.

## Global Constraints

- Python 3.9 floor — no `X | Y` union syntax (use `Optional[...]`, `Tuple[...]`).
- ruff clean: line-length 120; rules E, F, W, I; imports at top of file (avoid E402).
- mypy gate is **bare `mypy`** (project scopes via pyproject `files=[...]`; `src/exporters/sqlite_store.py` is in scope). Do NOT run `mypy src` (66 pre-existing unrelated errors).
- Additive-only schema: do NOT change existing tables, keys, or queries. Idempotent upserts.
- Vintages are SQLite-only — never added to the per-ticker JSON (`to_dict`).
- No additional network — read the already-fetched `companyfacts`.
- Commit trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Extract a shared resolution helper (refactor `_resolve_canonical`)

A behavior-preserving refactor that pulls the per-fact "is this the best value so far?" logic into a reusable helper, so Task 2's vintage extractor doesn't copy-paste it (DRY).

**Files:**
- Modify: `src/parsers/xbrl_parser.py` (`_resolve_canonical` ~lines 340-419; add `_assign_if_better`)

**Interfaces:**
- Consumes: existing `CANONICAL_FIELDS`, `SIGN_ABS`, `_init_period_meta`, `_apply_calendar` (unchanged); `Tuple` (already imported).
- Produces: `_assign_if_better(self, data, best, period, seed, field, priority, tag, entry) -> bool` — stores the value if it beats the current best for `(period, field.key)`, seeding the period dict from `seed`; returns True when it won.

- [ ] **Step 1: Confirm the regression baseline is green**

Run: `python -m pytest tests/test_xbrl_parser.py tests/test_fiscal_year_labeling.py tests/test_fiscal_calendar.py -q`
Expected: PASS (this is the behavior these tests pin; the refactor must keep them green).

- [ ] **Step 2: Add the `_assign_if_better` helper**

In `src/parsers/xbrl_parser.py`, add this method immediately above `_resolve_canonical` (~line 340):

```python
    def _assign_if_better(
        self,
        data: Dict[Any, Dict[str, Any]],
        best: Dict[Any, Tuple[int, str]],
        period: Any,
        seed: Dict[str, Any],
        field: Any,
        priority: int,
        tag: str,
        entry: Dict[str, Any],
    ) -> bool:
        """Store ``entry``'s value for ``field`` under ``period`` iff it beats the
        current best (higher-priority tag, or the same tag filed at least as late).
        Seeds a new period dict from ``seed``. Returns True when it won, so the caller
        can run any period-level metadata update."""
        filed = entry.get("filed") or ""
        bkey = (period, field.key)
        cur = best.get(bkey)
        if cur is not None and not (
            priority < cur[0] or (priority == cur[0] and filed >= cur[1])
        ):
            return False
        best[bkey] = (priority, filed)
        value = entry.get("val")
        if field.sign == SIGN_ABS and isinstance(value, (int, float)):
            value = abs(value)
        period_dict = data.setdefault(period, dict(seed))
        period_dict[field.key] = value
        period_dict.setdefault("_source_tags", {})[field.key] = tag
        return True
```

- [ ] **Step 3: Rewrite `_resolve_canonical`'s inner block to use the helper**

In `_resolve_canonical`, replace the block from `# Capture the SEC frame ...` through the end of the metadata `if quarterly: period_dict["fiscal_year"] = entry.get("fy")` (current lines ~381-416) with:

```python
                    # Capture the SEC frame from every contributing fact (it may be
                    # present only on a comparative instance, not the one that wins).
                    frame = entry.get("frame")
                    if frame:
                        period_frames.setdefault(period, set()).add(frame)

                    seed = self._init_period_meta(period, quarterly)
                    if not self._assign_if_better(
                        data, best, period, seed, field, priority, tag, entry
                    ):
                        continue

                    # Period-level metadata follows the latest-filed contributing entry.
                    period_dict = data[period]
                    filed = entry.get("filed") or ""
                    if filed >= meta_filed.get(period, ""):
                        meta_filed[period] = filed
                        period_dict["filed_date"] = entry.get("filed")
                        period_dict["period_end"] = entry.get("end")
                        period_dict["form"] = entry.get("form")
                        period_dict["fiscal_period"] = entry.get("fp")
                        if quarterly:
                            period_dict["fiscal_year"] = entry.get("fy")
```

(The lines above this block — the `for field`/`for priority, tag`/`for entry` loops, the `form`/`valid_fn`/`period = period_key_fn(entry)`/`if period is None` guards — are unchanged, as are `self._apply_calendar(...)` and `return data`.)

- [ ] **Step 4: Verify the full suite is still green (refactor guard)**

Run: `python -m pytest -q && ruff check src tests && mypy`
Expected: all tests pass (169), ruff clean, bare `mypy` Success. The refactor changed no behavior, so the existing parser tests — including `test_most_recently_filed_value_wins`, the ladder/differencing tests, and the fiscal-year tests — must all still pass.

- [ ] **Step 5: Commit**

```bash
git add src/parsers/xbrl_parser.py
git commit -m "refactor(parser): extract _assign_if_better from _resolve_canonical

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `extract_annual_vintages`

Add the vintage extractor that buckets annual facts by `(fiscal_year, accn)`.

**Files:**
- Modify: `src/parsers/xbrl_parser.py` (new method, after `extract_annual_financials` ~line 159)
- Test: `tests/test_xbrl_vintages.py` (new)

**Interfaces:**
- Consumes: `_assign_if_better` (Task 1); existing `_is_full_year`, `_fiscal_year_from_end`, `_period_year`, `_apply_calendar`, `CANONICAL_FIELDS`.
- Produces: `extract_annual_vintages(self, facts: Dict[str, Any], years_back: Optional[int] = None) -> Dict[str, Dict[str, Dict[str, Any]]]` returning `{fiscal_year_str: {accn: period_dict}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_xbrl_vintages.py`:

```python
"""Tests for point-in-time annual vintage extraction (one view per filing)."""

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


def _facts(entries):
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


def test_two_filings_yield_two_vintages_with_own_values():
    # FY2022 reported as 100 in the original 10-K, restated to 110 in the next year's
    # 10-K (different accn + filed). Both vintages are kept with their own value.
    facts = _facts([
        usd(100, "2022-01-01", "2022-12-31", fy=2022, filed="2023-02-15"),
        usd(110, "2022-01-01", "2022-12-31", fy=2023, filed="2024-02-15"),
    ])
    # give the two entries distinct accns (conftest usd defaults accn=filed)
    v = XBRLParser().extract_annual_vintages(facts)
    assert set(v.keys()) == {"2022"}
    by_accn = v["2022"]
    assert len(by_accn) == 2
    vals = sorted(p["revenue"] for p in by_accn.values())
    assert vals == [100, 110]
    # each vintage carries its own filed_date and accn
    for accn, p in by_accn.items():
        assert p["accn"] == accn
        assert p["filed_date"] in ("2023-02-15", "2024-02-15")
        assert p["fiscal_year"] == 2022


def test_within_filing_higher_priority_tag_wins():
    # One filing (one accn) tags revenue under both Revenues (priority 0) and the
    # contract-revenue tag (priority 1); the priority-0 tag wins for that vintage.
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            usd(200, "2023-01-01", "2023-12-31", fy=2023, filed="2024-02-15")]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            usd(999, "2023-01-01", "2023-12-31", fy=2023, filed="2024-02-15")]}},
    }}}
    v = XBRLParser().extract_annual_vintages(facts)
    (period,) = v["2023"].values()
    assert period["revenue"] == 200
    assert period["_source_tags"]["revenue"] == "Revenues"


def test_vintage_uses_date_rule_and_calendar():
    # Early-January 52/53-week year-end -> fiscal_year is end-year - 1; calendar from frame.
    facts = _facts([
        usd(50, "2022-01-03", "2023-01-01", fy=2022, filed="2023-02-15", frame="CY2022"),
    ])
    (period,) = XBRLParser().extract_annual_vintages(facts)["2022"].values()
    assert period["fiscal_year"] == 2022
    assert period["calendar_year"] == 2022


def test_years_back_trims_to_recent():
    facts = _facts([
        usd(1, "2020-01-01", "2020-12-31", fy=2020, filed="2021-02-15"),
        usd(2, "2021-01-01", "2021-12-31", fy=2021, filed="2022-02-15"),
        usd(3, "2022-01-01", "2022-12-31", fy=2022, filed="2023-02-15"),
    ])
    v = XBRLParser().extract_annual_vintages(facts, years_back=2)
    assert set(v.keys()) == {"2022", "2021"}


def test_empty_facts_returns_empty():
    assert XBRLParser().extract_annual_vintages({}) == {}
```

To give the two restatement entries distinct accns, set them explicitly in the first test by editing the entries after construction. Replace the first test's `facts = _facts([...])` with explicit accns:

```python
    e1 = usd(100, "2022-01-01", "2022-12-31", fy=2022, filed="2023-02-15")
    e1["accn"] = "0000-22-A"
    e2 = usd(110, "2022-01-01", "2022-12-31", fy=2023, filed="2024-02-15")
    e2["accn"] = "0000-23-B"
    facts = _facts([e1, e2])
```

(Use this explicit-accn form in `test_two_filings_yield_two_vintages_with_own_values`; the other tests have a single accn so the conftest default `accn=filed` is fine.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_xbrl_vintages.py -v`
Expected: FAIL — `AttributeError: 'XBRLParser' object has no attribute 'extract_annual_vintages'`.

- [ ] **Step 3: Implement `extract_annual_vintages`**

In `src/parsers/xbrl_parser.py`, add after `extract_annual_financials` (~line 159):

```python
    def extract_annual_vintages(
        self,
        facts: Dict[str, Any],
        years_back: Optional[int] = None,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Every filing's view of each annual period: ``{fiscal_year: {accn: period}}``.

        Unlike :meth:`extract_annual_financials` (which collapses to the latest-filed
        value per period), this keeps one period dict per *filing* (accession), so a
        restated year retains both its original and restated vintages. Reads the
        already-fetched ``companyfacts`` (every historical instance carries its own
        ``accn``/``filed``); no extra network. Annual (10-K) only.
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        data: Dict[Any, Dict[str, Any]] = {}
        best: Dict[Any, Tuple[int, str]] = {}
        period_frames: Dict[Any, set] = {}

        for field in CANONICAL_FIELDS:
            for priority, tag in enumerate(field.tags):
                tag_data = us_gaap.get(tag)
                if not tag_data:
                    continue
                for entry in tag_data.get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in {"10-K", "10-K/A"}:
                        continue
                    if not self._is_full_year(entry):
                        continue
                    fy = self._fiscal_year_from_end(entry.get("end")) or self._period_year(entry)
                    accn = entry.get("accn")
                    if fy is None or not accn:
                        continue
                    period = (fy, accn)
                    frame = entry.get("frame")
                    if frame:
                        period_frames.setdefault(period, set()).add(frame)
                    seed = {
                        "fiscal_year": fy, "accn": accn,
                        "filed_date": entry.get("filed"), "period_end": entry.get("end"),
                        "form": entry.get("form"),
                    }
                    self._assign_if_better(data, best, period, seed, field,
                                           priority, tag, entry)

        self._apply_calendar(data, period_frames, quarterly=False)

        nested: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for (fy, accn), period_dict in data.items():
            nested.setdefault(str(fy), {})[accn] = period_dict
        if years_back:
            keep = sorted(nested.keys(), reverse=True)[:years_back]
            nested = {y: nested[y] for y in keep}
        return nested
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_xbrl_vintages.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/parsers/xbrl_parser.py tests/test_xbrl_vintages.py
git commit -m "feat(parser): extract_annual_vintages (one view per filing)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Model field + pipeline wiring

Add the `StockData` field (SQLite-only — kept out of JSON), call the extractor in the fetcher, and derive each vintage.

**Files:**
- Modify: `src/models/stock_data.py` (new field ~line 45; `to_dict` ~line 82)
- Modify: `src/fetchers/stock_data_fetcher.py` (call ~line 149; `_clean_and_derive` ~line 251)
- Test: `tests/test_stock_data.py` (append)

**Interfaces:**
- Consumes: `extract_annual_vintages` (Task 2); existing `apply_derivations`.
- Produces: `StockData.financials_annual_vintages: Dict[str, Any]` (shape `{fy: {accn: period}}`), populated by the fetcher and excluded from `to_dict`.

- [ ] **Step 1: Write the failing model tests**

Append to `tests/test_stock_data.py`:

```python
def test_financials_annual_vintages_defaults_empty():
    from src.models.stock_data import StockData
    s = StockData(ticker="T", cik="1", company_name="Test")
    assert s.financials_annual_vintages == {}


def test_vintages_excluded_from_to_dict():
    from src.models.stock_data import StockData
    s = StockData(ticker="T", cik="1", company_name="Test")
    s.financials_annual_vintages = {"2022": {"acc-1": {"revenue": 100}}}
    assert "financials_annual_vintages" not in s.to_dict()


def test_from_dict_keeps_vintages_field_when_present():
    # financials_annual_vintages is a declared dataclass field, so from_dict keeps it
    # when present. (The JSON exporter never WRITES the key — see the to_dict test —
    # so in practice JSON has no vintages; this just pins round-trip behavior.)
    from src.models.stock_data import StockData
    s = StockData.from_dict({"ticker": "T", "cik": "1", "company_name": "Test",
                             "financials_annual_vintages": {"2022": {"a": {"revenue": 1}}}})
    assert s.financials_annual_vintages == {"2022": {"a": {"revenue": 1}}}
```

- [ ] **Step 2: Run the model tests to verify they fail**

Run: `python -m pytest tests/test_stock_data.py -k vintage -v`
Expected: FAIL — `AttributeError: 'StockData' object has no attribute 'financials_annual_vintages'`.

- [ ] **Step 3: Add the field and exclude it from `to_dict`**

In `src/models/stock_data.py`, add the field after `financials_ttm` (~line 47):

```python
    # Point-in-time vintages: {fiscal_year: {accn: period}}. SQLite-only (excluded
    # from to_dict/JSON); see the vintaged-ingestion spec.
    financials_annual_vintages: Dict[str, Any] = field(default_factory=dict)
```

Then in `to_dict`, drop it from the serialized output:

```python
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with JSON-serializable values."""
        data = asdict(self)
        # Convert datetime to ISO string
        if isinstance(data["collected_at"], datetime):
            data["collected_at"] = data["collected_at"].isoformat()
        # Vintages are SQLite-only — never written to the per-ticker JSON.
        data.pop("financials_annual_vintages", None)
        return data
```

`from_dict` already filters to declared fields, so the now-declared `financials_annual_vintages` is
kept when present (pinned by `test_from_dict_keeps_vintages_field_when_present`), while `to_dict` drops
it so JSON never carries it.

- [ ] **Step 4: Run the model tests to verify they pass**

Run: `python -m pytest tests/test_stock_data.py -k vintage -v`
Expected: PASS (3 tests: defaults-empty, excluded-from-to_dict, from_dict-keeps-when-present).

- [ ] **Step 5: Wire the fetcher**

In `src/fetchers/stock_data_fetcher.py`, after the `quarterly = self.xbrl_parser.extract_quarterly_financials(...)` call and before `stock.merge_parsed_financials(annual, quarterly)` (~line 149-151), add:

```python
                    vintages = self.xbrl_parser.extract_annual_vintages(
                        facts, years_back=years_back
                    )
                    stock.financials_annual_vintages = vintages
```

Then in `_clean_and_derive`, after the existing `for attr in ("financials_annual", "financials_quarterly"):` loop and before the `if stock.financials_quarterly:` TTM line (~line 252), add:

```python
        # Derive identities within each point-in-time vintage (self-contained snapshots).
        for by_accn in (stock.financials_annual_vintages or {}).values():
            for period in by_accn.values():
                apply_derivations(period)
```

- [ ] **Step 6: Verify the suite + linters**

Run: `python -m pytest -q && ruff check src tests && mypy`
Expected: all pass; ruff clean; bare `mypy` Success. (The fetcher wiring is integration glue verified end-to-end by the Task 5 merge gate; the model behavior is unit-tested above.)

- [ ] **Step 7: Commit**

```bash
git add src/models/stock_data.py src/fetchers/stock_data_fetcher.py tests/test_stock_data.py
git commit -m "feat: carry annual vintages on StockData and derive them (SQLite-only)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Additive `financials_annual_vintages` SQLite table

Persist vintages to a new table; leave every existing table untouched.

**Files:**
- Modify: `src/exporters/sqlite_store.py` (`_create_schema` ~line 121; `_migrate` ~line 179; `_write_stock` ~line 280)
- Test: `tests/test_sqlite_store.py` (append)

**Interfaces:**
- Consumes: `StockData.financials_annual_vintages` (Task 3); existing `_cols_ddl`, `_CANONICAL_COLUMNS`, `_canonical_values`, `_upsert`.
- Produces: table `financials_annual_vintages` PK `(ticker, fiscal_year, accn)` + index `idx_fav_asof (ticker, fiscal_year, filed_date)`.

- [ ] **Step 1: Write the failing store tests**

Append to `tests/test_sqlite_store.py` (follow the file's existing pattern for building a `StockData` and exporting to a temp DB; reuse its helpers/fixtures):

```python
def test_vintages_table_written_and_idempotent(tmp_path):
    from src.models.stock_data import StockData
    from src.exporters.sqlite_store import SQLiteStore
    import sqlite3

    s = StockData(ticker="ZZ", cik="9", company_name="Z Co")
    s.financials_annual_vintages = {
        "2022": {
            "acc-A": {"fiscal_year": 2022, "accn": "acc-A", "filed_date": "2023-02-15",
                      "period_end": "2022-12-31", "form": "10-K", "calendar_year": 2022,
                      "revenue": 100.0, "net_income": 10.0},
            "acc-B": {"fiscal_year": 2022, "accn": "acc-B", "filed_date": "2024-02-15",
                      "period_end": "2022-12-31", "form": "10-K", "calendar_year": 2022,
                      "revenue": 110.0, "net_income": 11.0},
        }
    }
    db = tmp_path / "v.db"
    store = SQLiteStore(db_path=str(db))
    store.export([s])
    store.export([s])  # second export must not duplicate rows

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT accn, filed_date, revenue FROM financials_annual_vintages "
        "WHERE ticker='ZZ' AND fiscal_year=2022 ORDER BY filed_date"
    ).fetchall()
    conn.close()
    assert rows == [("acc-A", "2023-02-15", 100.0), ("acc-B", "2024-02-15", 110.0)]


def test_vintages_do_not_affect_financials_annual(tmp_path):
    from src.models.stock_data import StockData
    from src.exporters.sqlite_store import SQLiteStore
    import sqlite3

    s = StockData(ticker="ZZ", cik="9", company_name="Z Co")
    s.financials_annual = {"2022": {"fiscal_year": 2022, "period_end": "2022-12-31",
                                    "revenue": 100.0}}
    s.financials_annual_vintages = {"2022": {"acc-A": {"fiscal_year": 2022, "accn": "acc-A",
                                    "filed_date": "2023-02-15", "revenue": 100.0}}}
    db = tmp_path / "v2.db"
    SQLiteStore(db_path=str(db)).export([s])
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM financials_annual WHERE ticker='ZZ'").fetchone()[0]
    conn.close()
    assert n == 1  # the latest-view table is unaffected by vintages
```

(If `SQLiteStore`'s constructor/method names differ in the file, match the existing tests' usage exactly.)

- [ ] **Step 2: Run the store tests to verify they fail**

Run: `python -m pytest tests/test_sqlite_store.py -k vintage -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: financials_annual_vintages`.

- [ ] **Step 3: Add the table to `_create_schema`**

In `src/exporters/sqlite_store.py`, inside `_create_schema`'s SQL script, after the `financials_ttm` table and before the `CREATE INDEX idx_fa_calendar_year` block (~line 121), add:

```sql
            CREATE TABLE IF NOT EXISTS financials_annual_vintages (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                accn TEXT NOT NULL,
                filed_date TEXT,
                period_end TEXT,
                form TEXT,
                calendar_year INTEGER,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, fiscal_year, accn)
            );
```

And add to the index block (alongside the other `CREATE INDEX` lines):

```sql
            CREATE INDEX IF NOT EXISTS idx_fav_asof
                ON financials_annual_vintages (ticker, fiscal_year, filed_date);
```

- [ ] **Step 4: Register it in `_migrate`**

In `_migrate`'s `expected` dict, add an entry (so a pre-existing DB gains canonical columns as the registry grows), mirroring `financials_annual`:

```python
            "financials_annual_vintages": [("calendar_year", "INTEGER")]
            + [(c, "REAL") for c in _CANONICAL_COLUMNS],
```

- [ ] **Step 5: Add the write loop in `_write_stock`**

In `_write_stock`, after the `financials_ttm` loop (~after line 308's block) add:

```python
        # financials_annual_vintages (point-in-time: one row per filing/accession)
        for fy, by_accn in (stock.financials_annual_vintages or {}).items():
            for accn, period in by_accn.items():
                vrow = {
                    "ticker": stock.ticker,
                    "fiscal_year": int(fy) if str(fy).isdigit() else None,
                    "accn": accn,
                    "filed_date": period.get("filed_date"),
                    "period_end": period.get("period_end"),
                    "form": period.get("form"),
                    "calendar_year": period.get("calendar_year"),
                }
                vrow.update(self._canonical_values(period))
                self._upsert(conn, "financials_annual_vintages",
                             ["ticker", "fiscal_year", "accn"], vrow)
```

- [ ] **Step 6: Run the store tests + suite + linters**

Run: `python -m pytest tests/test_sqlite_store.py -q && python -m pytest -q && ruff check src tests && mypy`
Expected: the new store tests pass; full suite green; ruff clean; bare `mypy` Success.

- [ ] **Step 7: Commit**

```bash
git add src/exporters/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): additive financials_annual_vintages table + as-of index

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Docs, full verification, and live merge gate

**Files:**
- Modify: `README.md` (the SQLite "Tables:" list ~line 507 and the point-in-time note)

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: nothing new (docs + verification).

- [ ] **Step 1: Update the README**

In `README.md`, add `financials_annual_vintages` to the SQLite tables list and a one-line description. Find the line listing tables (e.g. `Tables: \`companies\`, \`financials_annual\`, ...`) and append `financials_annual_vintages` to it, then add a sentence after that paragraph:

```markdown
The `financials_annual_vintages` table stores **point-in-time** data: one row per
(ticker, fiscal_year, filing accession), so you can see every filing's view of a year —
the original and each restatement — keyed by `filed_date`. This is sub-project 1 of the
no-look-ahead point-in-time work (the as-of-date query API and point-in-time metrics
follow). Vintages are SQLite-only (not in the per-ticker JSON).
```

- [ ] **Step 2: Run the full suite + linters**

Run: `python -m pytest -q && ruff check src tests && mypy`
Expected: all pass; ruff clean; bare `mypy` Success.

- [ ] **Step 3: Commit the docs**

```bash
git add README.md
git commit -m "docs: document financials_annual_vintages (point-in-time SP1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Rebuild the DB and run the live 50-stock gate**

```bash
rm -f data/output/stock.db
python -m src.main JPM BAC WFC C GS USB PGR TRV ALL MET PRU CB PLD AMT EQIX SPG O PSA NEE DUK SO D XOM CVX COP SLB AAPL MSFT GOOGL NVDA AVGO ORCL WMT COST HD PG KO MCD JNJ UNH PFE ABBV MRK CAT HON GE BA VZ T TMUS --no-yahoo
```

- [ ] **Step 5: Verify vintages populated, a restatement is visible, and nothing regressed**

Run:
```bash
python - <<'PY'
import sqlite3, glob, json
db = sqlite3.connect("data/output/stock.db"); c = db.cursor()
total = c.execute("SELECT COUNT(*) FROM financials_annual_vintages").fetchone()[0]
tickers = c.execute("SELECT COUNT(DISTINCT ticker) FROM financials_annual_vintages").fetchone()[0]
print("vintage rows:", total, "tickers with vintages:", tickers)
assert total > 1000 and tickers >= 45, (total, tickers)
# Multiple vintages per (ticker, fiscal_year) somewhere (a year reported by >1 filing).
multi = c.execute(
    "SELECT ticker, fiscal_year, COUNT(*) n FROM financials_annual_vintages "
    "GROUP BY ticker, fiscal_year HAVING n > 1 LIMIT 5").fetchall()
print("sample multi-vintage (ticker, fy, #filings):", multi)
assert multi, "expected at least one year reported by multiple filings"
# A genuine restatement: a (ticker, fy) whose net_income differs across filings.
restated = c.execute(
    "SELECT ticker, fiscal_year, COUNT(DISTINCT net_income) d FROM financials_annual_vintages "
    "WHERE net_income IS NOT NULL GROUP BY ticker, fiscal_year HAVING d > 1 LIMIT 5").fetchall()
print("sample restatements (distinct net_income across filings):", restated)
# Additivity: existing latest-view table and scores unchanged in shape.
fa = c.execute("SELECT COUNT(*) FROM financials_annual").fetchone()[0]
print("financials_annual rows:", fa)
db.close()
scores = sorted((json.load(open(fp,encoding="utf-8")).get("data_quality") or {}).get("score")
                for fp in glob.glob("data/output/json/*.json")
                if (json.load(open(fp,encoding="utf-8")).get("data_quality") or {}).get("score") is not None)
print("score min/mean:", scores[0], round(sum(scores)/len(scores),1), "N=", len(scores))
PY
```
Expected:
- `financials_annual_vintages` has many rows (>1000) across ~all 50 tickers.
- At least one `(ticker, fiscal_year)` has multiple filings, and at least one shows a **differing `net_income`** across filings (a real restatement captured).
- `financials_annual` row count and the data-quality score distribution match the pre-change baseline (min 97 / mean ~99.9, only PSA sub-100) — confirming the change is purely additive.
- Full suite + ruff + bare `mypy` green.

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin feat/pit-vintaged-ingestion
gh pr create --base main --title "Point-in-time: vintaged annual ingestion (SP1)" \
  --body "Captures every filing's view of each annual period into an additive financials_annual_vintages table (PK ticker, fiscal_year, accn; indexed on filed_date for as-of queries). Foundation for no-look-ahead backtesting; sub-project 1 of 3 (as-of query API and point-in-time metrics follow). Purely additive — existing tables, queries, JSON, and all 50 quality scores unchanged. Spec/plan in docs/superpowers/.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review

**Spec coverage:**
- Parallel vintage extractor bucketing by (fiscal_year, accn), DRY via shared helper → Tasks 1-2. ✓
- Date-rule fiscal year + calendar + `_source_tags` per vintage → Task 2 (`test_vintage_uses_date_rule_and_calendar`, within-filing test). ✓
- `years_back` trim → Task 2 (`test_years_back_trims_to_recent`). ✓
- No extra network (reads companyfacts) → Task 2 docstring; inherent. ✓
- StockData field, SQLite-only (excluded from to_dict) → Task 3. ✓
- Per-vintage `apply_derivations` → Task 3 Step 5. ✓
- Additive table PK (ticker, fiscal_year, accn) + as-of index + `_migrate` → Task 4. ✓
- Idempotent upsert; existing tables untouched → Task 4 tests + Task 5 gate. ✓
- README (table + decomposition note) → Task 5. ✓
- Merge gate (vintages populated, restatement visible, additive/no-regression) → Task 5. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". Step 3 of Task 3 flags a test-design subtlety and resolves it by replacing the test with the consistent-behavior version — the final test set is explicit, not a placeholder. Every code step shows full code.

**Type consistency:** `_assign_if_better(self, data, best, period, seed, field, priority, tag, entry) -> bool` is defined in Task 1 and called identically in Task 1's rewrite and Task 2. `extract_annual_vintages(...) -> Dict[str, Dict[str, Dict[str, Any]]]` returns `{fy_str: {accn: period}}`, consumed with that shape in Task 3 (fetcher loop `by_accn.values()`) and Task 4 (`for fy, by_accn ... for accn, period`). Field name `financials_annual_vintages` and table name `financials_annual_vintages` are consistent across Tasks 3-5. PK/keys `["ticker", "fiscal_year", "accn"]` match the table definition.
