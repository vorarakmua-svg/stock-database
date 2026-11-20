# As-of Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The as-of view of a fiscal year on date D is exactly the annual record our pipeline builds from the facts
filed on or before D, stored as change-only snapshots and read with no as-of-specific rules.

**Architecture:** Extract today's annual steps (parser + share scale + derivations + validation) into one pure
function of the facts. At collection, replay it on the facts known at every distinct filing date, keep a year's row
when it changes (with tombstones when a year leaves the record), store them in a new table, and make `AsOfReader`
a plain lookup over that table. Vintages get the same filing-date gate.

**Tech Stack:** Python 3.9-compatible, SQLite, pytest, FastAPI (web app, read-only DB).

**Spec:** `docs/superpowers/specs/2026-11-18-asof-snapshots-design.md`

## Global Constraints

- Python 3.9-compatible syntax; repo files are CRLF (preserve them); console cp1252 (no non-ASCII in prints).
- Gates for every task: `ruff check src/ diagnose.py tests/`, `mypy`, `python -m pytest -p no:cacheprovider -o addopts="" -q`.
- Tests never touch the network (the suite's `no_network` guard) or `data/output/`.
- The refactor must leave `financials_annual` cell-for-cell identical over all 502, except the one intended change
  (Task 4: years not yet ended). Any moved median, verdict, owner-earnings verdict or historical-value label goes to
  the user before it is committed.
- The only look-ahead gate is `facts_known_by`; no other code filters by filing date for snapshots.
- No selection rule (tag ranking, carry-forward, narrower-figure, debt-view) may exist outside the shared pipeline.
- **Prerequisite:** BUG-341 is merged before Task 1 starts; every baseline is taken after it.
- QA process: each task's fix is committed with a ledger/LOG entry like the QA loop's (qa/BUGS.md, qa/LOG.md).

## Review Focus

1. A fact with no `filed` date: the gate can't place it. Expected: the builder counts such facts and adds a run
   warning; they enter no snapshot and no annual record (none exist in the sampled cache; the test pins the rule).
2. Two filings on the same day (a 10-K and its 10-K/A): one snapshot date, both known. Expected: one snapshot that
   reflects both.
3. A year that leaves the record at a later date (re-keyed by BUG-341's rule, or set aside by a later filing).
   Expected: a tombstone row, so as-of after that date returns None, not the stale row.
4. A `years_back` run on a ticker with full-history snapshots. Expected: only the built years' rows are replaced;
   the others stay.
5. A database built before this change (no table, or a ticker with no rows). Expected: the reader returns None and
   the ASOF page says the as-of data isn't built for that ticker, with no 500.

---

### Task 0: Baselines (after BUG-341)

**Files:** none in the repo; scratch `%LOCALAPPDATA%\stockdb-qa\asof\base\`.

- [ ] **Step 1:** Export HEAD (`git archive`) to `base\tree`. Make a DB copy (`copy_db.py`, backup API, `mode=ro`),
      re-collect all 502 offline with the base tree, and run the engine over the 502. Check free RAM >= 4 GB first.
- [ ] **Step 2:** Save the per-ticker `financials_annual` dicts the fetcher builds (pickle per ticker) as the
      agreement baseline, plus collection wall time and peak RAM for the 502.

### Task 1: The look-ahead gate and snapshot dates

**Files:**
- Create: `src/parsers/known_facts.py`
- Test: `tests/test_known_facts.py`

**Interfaces:**
- Produces: `facts_known_by(facts: Dict[str, Any], day: str) -> Dict[str, Any]` (companyfacts shape, every
  taxonomy kept, each entry with `filed <= day`, empty units/tags dropped, other top-level keys kept);
  `filing_dates(facts: Dict[str, Any]) -> List[str]` (distinct `filed` of every entry of every taxonomy, ascending);
  `undated_facts(facts) -> int`.

- [ ] **Step 1: Failing tests**

```python
def test_a_fact_filed_on_the_day_is_known_and_one_filed_after_is_not():
    facts = _facts(("us-gaap", "Revenues", "2020-02-01", 10.0), ("us-gaap", "Revenues", "2021-02-01", 11.0))
    known = facts_known_by(facts, "2020-02-01")
    assert [e["val"] for e in known["facts"]["us-gaap"]["Revenues"]["units"]["USD"]] == [10.0]

def test_every_taxonomy_is_gated_and_kept():
    facts = _facts(("dei", "EntityCommonStockSharesOutstanding", "2020-02-01", 5.0),
                   ("ifrs-full", "Revenue", "2021-02-01", 1.0))
    known = facts_known_by(facts, "2020-06-30")
    assert "dei" in known["facts"] and "ifrs-full" not in known["facts"]

def test_filing_dates_are_distinct_and_ascending_across_taxonomies():
    facts = _facts(("us-gaap", "A", "2021-02-01", 1.0), ("dei", "B", "2020-02-01", 1.0), ("us-gaap", "C", "2021-02-01", 2.0))
    assert filing_dates(facts) == ["2020-02-01", "2021-02-01"]

def test_an_undated_fact_is_never_known_and_is_counted():
    facts = _facts(("us-gaap", "Revenues", None, 10.0))
    assert facts_known_by(facts, "2099-12-31")["facts"] == {} and undated_facts(facts) == 1
```

- [ ] **Step 2:** Run `python -m pytest tests/test_known_facts.py -q`; expect import errors.
- [ ] **Step 3:** Implement the three functions in `src/parsers/known_facts.py` (pure, no copying of untouched
      entries beyond what filtering needs).
- [ ] **Step 4:** Tests pass; gates pass.
- [ ] **Step 5:** Commit `feat(asof): one gate for the facts known on a date`.

### Task 2: Per-field provenance in the annual record

**Files:**
- Modify: `src/parsers/xbrl_parser.py` (`_assign_if_better` ~2161, `_restore_agreed_figures`, wherever
  `_source_tags` is popped or carried)
- Modify: `src/fetchers/stock_data_fetcher.py` (pop `_source_filings` where `_source_tags` is handled, if it is)
- Test: `tests/test_xbrl_parser.py`

**Interfaces:**
- Produces: each annual period dict carries `_source_filings: Dict[str, Dict[str, str]]`, `{field: {"accn",
  "filed", "form"}}`, for every field set from a fact; a figure `_restore_agreed_figures` puts back carries the
  restoring entry's filing; derived fields carry none. `_source_tags` unchanged.

- [ ] **Step 1: Failing tests**: `test_each_annual_figure_names_the_filing_it_came_from` (two 10-Ks, FY2020 revenue
      restated in the FY2021 10-K: provenance accn is the FY2021 10-K's; FY2020 net income only in the FY2020 10-K:
      its accn) and `test_a_restored_figure_names_the_filing_whose_figure_was_kept` (reuse an existing
      `_restore_agreed_figures` fixture; assert the kept filing's accn).
- [ ] **Step 2:** Run them; expect KeyError `_source_filings`.
- [ ] **Step 3:** Record provenance in `_assign_if_better` beside `_source_tags`; update it in the restore step.
- [ ] **Step 4:** Tests pass; full suite passes (no stored column yet, so no store change).
- [ ] **Step 5:** Commit `feat(parsers): each annual figure names its filing`.

### Task 3: `build_annual_record`, a behaviour-preserving extraction

**Files:**
- Create: `src/parsers/annual_record.py`
- Modify: `src/fetchers/stock_data_fetcher.py` (SEC block ~236-260 and `_clean_and_derive` ~411-436: the annual
  part moves out; quarterly and vintages stay)
- Test: `tests/test_annual_record.py`

**Interfaces:**
- Consumes: `XBRLParser.extract_annual_financials`, `normalize_share_scale`, `apply_derivations`, `validate_period`.
- Produces: `AnnualRecord` (dataclass: `periods: Dict[str, Dict[str, Any]]`, `warnings: List[str]`) and
  `build_annual_record(parser: XBRLParser, facts: Dict[str, Any], *, ticker: str, years_back: Optional[int]) ->
  AnnualRecord`. `periods` is exactly what `stock.financials_annual` holds after `_clean_and_derive` today, with
  `_source_tags` and `_source_filings` kept; `warnings` are today's repair, share-scale and validation warnings for
  the annual record, same text, same order.

- [ ] **Step 1: Failing test** `test_the_record_equals_the_fetchers_annual_record` over 3 fixture companyfacts
      already in `tests/fixtures` (one with a repair, one with a share-scale rescale, one with a validation warning):
      run the fetcher's current path and `build_annual_record`; assert equal periods and warnings.
- [ ] **Step 2:** Run; expect ImportError.
- [ ] **Step 3:** Implement by moving code, not rewriting it; the fetcher calls `build_annual_record` and adds its
      warnings. `_clean_and_derive` keeps the quarterly loop, the vintages loop and TTM.
- [ ] **Step 4:** Tests pass; full suite passes.
- [ ] **Step 5: Census.** Re-collect the 502 offline with this tree; compare every ticker's `financials_annual` dict
      and annual warnings to Task 0's baseline. Expected: 0 differences.
- [ ] **Step 6:** Commit `refactor(annual): one function builds the annual record from the facts`.

### Task 4: A year not yet ended isn't in the record

**Files:**
- Modify: `src/parsers/xbrl_parser.py` (`_apply_calendar`: keep the chosen end as `period_dict["_year_end"]`)
- Modify: `src/parsers/annual_record.py`
- Test: `tests/test_annual_record.py`

**Interfaces:**
- Produces: `build_annual_record` drops any year whose `_year_end` (else `period_end`) is after the latest `filed`
  date in *facts*; `_year_end` never reaches `periods`.

- [ ] **Step 1: Failing tests** `test_a_forward_dated_dividend_opens_no_year` (BAC shape: 10-K filed 2011-02-25
      holds FY2010 and a 2011-01-01..2011-12-31 dividend; record has FY2010 only) and
      `test_a_year_ended_on_its_filing_day_stays` (end = latest filed date: kept).
- [ ] **Step 2:** Run; expect FY2011 present.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass; gates pass.
- [ ] **Step 5: Measure.** Re-collect the 502 offline on a DB copy, run the engine, diff every table against Task
      0's; list every dropped year and every moved median, verdict, owner-earnings verdict and label. **Stop and
      bring any valuation move to the user before committing.**
- [ ] **Step 6:** Commit with the ledger entry (BUG-113 part) `fix(annual): a year not yet ended isn't in the record`.

### Task 5: The snapshot builder (with gated vintages)

**Files:**
- Create: `src/parsers/annual_snapshots.py`
- Test: `tests/test_annual_snapshots.py`

**Interfaces:**
- Consumes: `facts_known_by`, `filing_dates`, `undated_facts` (Task 1); `build_annual_record` (Task 3);
  `XBRLParser.extract_annual_vintages`.
- Produces: `SnapshotSet` (dataclass: `rows: List[Dict[str, Any]]`, `vintages: Dict[str, Dict[str, Dict[str,
  Any]]]`, `warnings: List[str]`) and `build_snapshots(parser, facts, *, ticker: str, years_back: Optional[int]) ->
  SnapshotSet`. Each row: `fiscal_year: int`, `known_from: str`, `known_accn: Optional[str]` (an accession filed
  that day, the latest by accession), `present: bool`, and when present the period's canonical values,
  `source_tags`, `source_filings`, `period_end`, `calendar_year`, `frame`, `accn`/`filed_date`/`form` (the latest
  filing among its `_source_filings`). Vintages: filing k's vintage is taken from `extract_annual_vintages(
  facts_known_by(facts, filed_k))`, the accessions filed on that day only, then normalised as the fetcher does now.

- [ ] **Step 1: Failing tests**
  - `test_one_row_per_change`: three 10-Ks; FY2019 restated only in the third: FY2019 rows at dates 1 and 3 only.
  - `test_the_latest_snapshot_equals_the_record`: for each year, the last present row's values == `build_annual_record(facts).periods[year]`.
  - `test_a_year_that_leaves_the_record_gets_a_tombstone` (Review Focus 3): a later filing sets the year's only figures aside; a `present=False` row at that date.
  - `test_same_day_filings_make_one_snapshot` (Review Focus 2).
  - `test_an_undated_fact_warns` (Review Focus 1).
  - `test_a_vintage_reads_no_later_filing` (BUG-120 shape from `qa/evidence` review 11's `bug099_vintage_lookahead.py`: the FY2016 10-K's vintage revenue stays $10.0B).
  - `test_years_back_trims_rows_not_history`: `years_back=2` keeps rows of the 2 latest years, built on full history.
- [ ] **Step 2:** Run; expect ImportError.
- [ ] **Step 3:** Implement: loop over `filing_dates`, build, compare each year to its previous row (values,
      `source_tags`, `source_filings`, `period_end`, `calendar_year`, `frame`), emit changes and tombstones.
- [ ] **Step 4:** Tests pass; gates pass.
- [ ] **Step 5: Cost.** Time `build_snapshots` over the 502 cached facts (Pool <= 4, RAM >= 4 GB). If the median
      company exceeds 10 s or the total exceeds 3x Task 0's collection time, add caching of the parse between dates
      under the any-D oracle (Task 8) and re-measure; report the numbers either way.
- [ ] **Step 6:** Commit `feat(asof): snapshots of the annual record at every filing date`.

### Task 6: Store and fetcher wiring

**Files:**
- Modify: `src/exporters/sqlite_store.py` (schema ~294-315, `expected` columns ~734, `_write_stock` ~938 and the
  vintages block ~1041-1056)
- Modify: `src/models/stock_data.py` (new field; pop from the JSON export like vintages at ~145)
- Modify: `src/fetchers/stock_data_fetcher.py` (call `build_snapshots`; vintages from `SnapshotSet.vintages`; drop
  the old vintage extraction and its normalisation loop)
- Test: `tests/test_sqlite_store.py`, `tests/test_fetcher_concurrency.py`

**Interfaces:**
- Produces: table `financials_annual_known` (`ticker TEXT NOT NULL, fiscal_year INTEGER NOT NULL, known_from TEXT
  NOT NULL, known_accn TEXT, present INTEGER NOT NULL, accn TEXT, filed_date TEXT, form TEXT, period_end TEXT,
  calendar_year INTEGER, frame TEXT, source_tags TEXT, source_filings TEXT, <canonical columns>`, PRIMARY KEY
  `(ticker, fiscal_year, known_from)`), index `idx_fak_asof (ticker, fiscal_year, known_from)`;
  `StockData.financials_annual_known: List[Dict[str, Any]]`.

- [ ] **Step 1: Failing tests**
  - `test_a_full_run_replaces_a_tickers_snapshots`: store rows, store a run with fewer: old rows gone.
  - `test_a_years_back_run_replaces_only_its_years` (Review Focus 4).
  - `test_a_fill_only_run_leaves_snapshots_alone` (predecessor_error set).
  - `test_the_json_export_has_no_snapshots`.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement: create table/index in the schema; delete-then-insert inside `_write_stock`'s
      transaction; fill-only skip.
- [ ] **Step 4:** Tests pass; full suite passes.
- [ ] **Step 5:** Commit `feat(store): the annual record as known at each filing date`.

### Task 7: The as-of reader as a lookup

**Files:**
- Modify: `src/query/asof.py` (rewrite; delete `_TAG_RANKS`, `_NARROWER`, `_DEBT*`, `_tags`, `_narrower`, `_held`,
  `_latest`, `_narrower_than_view`, `_debt_view`)
- Modify: `src/webapp/routes/pages.py` (`asof_result_fragment` ~469) and its template: the "not built" message
- Test: `tests/test_asof_reader.py` (rewrite fixtures to insert `financials_annual_known` rows; rule tests move to
  Task 5's file or are deleted with the rules), `tests/test_pit_metrics.py`, `tests/test_web_api_asof.py`

**Interfaces:**
- Consumes: the table from Task 6.
- Produces: unchanged public API: `AsOfReader.as_of_annual(ticker, fiscal_year, as_of_date) -> Optional[Dict]`
  (canonical fields, `accn`, `filed_date`, `form`, `period_end`, `calendar_year`, `carried_from: {field: {accn,
  filed_date, form}}` for fields whose `source_filings` accn isn't `accn`), `as_of_value`, `history_as_of`; new
  `AsOfReader.has_snapshots(ticker) -> bool`.

- [ ] **Step 1: Failing tests**
  - `test_the_latest_row_on_or_before_the_date_wins` (rows at 2020-02-01 and 2021-02-01; D=2020-12-31 -> first).
  - `test_a_tombstone_hides_the_year` (Review Focus 3).
  - `test_a_ticker_without_snapshots_reads_none_and_the_page_says_not_built` (Review Focus 5; web fragment 200 with the message).
  - `test_carried_from_names_the_earlier_filings_figures`.
  - `test_a_database_without_the_table_reads_none` (Review Focus 5).
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement: one SQL per year (`MAX(known_from) <= ?`), `present=0` -> None.
- [ ] **Step 4:** Tests pass; full suite passes.
- [ ] **Step 5:** Commit `feat(asof): the as-of view reads the record as it was known`.

### Task 8: Verification, docs, ledger

**Files:**
- Create: `qa/oracle/asof_oracle.py`
- Modify: `README.md` (rewrite "Point-in-time as-of queries" ~2373-2430 and the vintages paragraph), `qa/BUGS.md`,
  `qa/LOG.md`

- [ ] **Step 1: The any-D oracle** `qa/oracle/asof_oracle.py DB CACHE_DIR --sample N --seed S`: for N random
      (ticker, D) with D uniform over each ticker's filing span (not only filing dates), compare
      `AsOfReader.as_of_annual` for every year to `build_annual_record(facts_known_by(facts, D))`, and assert no
      `source_filings` date > D. Exit 1 on any difference.
- [ ] **Step 2:** On a DB copy re-collected with the branch (all 502): run the agreement census (latest present row
      == `financials_annual` per year), the oracle (N=2000), review21's `lookahead.py` (Pool(4) copy), and the same
      collection under `PYTHONHASHSEED=0` and `4` (snapshot tables identical). All must pass.
- [ ] **Step 3:** Diff every table against Task 0's copy: only `financials_annual_known` (new), vintages (gated
      set-asides) and Task 4's dropped years change; list the vintage moves by bug. Report collection time, peak RAM
      and DB size growth against Task 0.
- [ ] **Step 4:** Re-check BUG-116, 120, 085, 334, 268, 113, 132, 287, 320, 339 each with its own repro on the new
      view; mark VERIFIED or leave open with the reason. File the stale-`financials_annual`-rows bug (store only
      upserts).
- [ ] **Step 5:** README rewritten (the snapshot rule, the gate, tombstones, "not built" state, the vintages'
      "as filed" meaning). Commit `docs(asof): ...` with the ledger and LOG entries.
