# 52/53-Week Fiscal-Year Labeling Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive each period's `fiscal_year` from the filer's own original-filing SEC `fy` instead of the period-end calendar year, fixing off-by-one labels, missing year buckets, and quarter-grouping collisions for 52/53-week filers whose year-end crosses the Dec/Jan boundary (e.g. JNJ).

**Architecture:** Add two helpers to `XBRLParser`: `_build_fiscal_year_map` (period-end → fiscal year, from the earliest-filed `fp=="FY"` instance) and `_fiscal_year_fallback` (a Dec/Jan-boundary date rule for periods with no original FY filing). Use them as the annual bucketing key, and fix the quarterly ladder's fiscal-year label from the ladder's own earliest-filed `fy`. `_period_year` is kept unchanged as the ultimate fallback. `calendar_year`/`calendar_quarter` (SEC frames) are untouched.

**Tech Stack:** Python 3.9, pytest. All changes in `src/parsers/xbrl_parser.py` + tests + README.

## Global Constraints

- Python 3.9 floor — no `X | Y` union syntax.
- ruff clean: line-length 120; rules E, F, W, I; imports at top of file (avoid E402).
- mypy clean.
- All external data synthetic in tests (no network).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Do NOT modify `_period_year` or `test_period_year_uses_end_then_frame` — `_period_year` stays as-is (still used as the final fallback and pinned by that test).
- Flag-only philosophy unchanged; this fix corrects labels, never mutates reported values.

---

### Task 1: Fiscal-year derivation helpers

Two pure helpers on `XBRLParser`. No call sites change yet.

**Files:**
- Modify: `src/parsers/xbrl_parser.py` (add two methods; suggested location: just after `_period_year`, ~line 76)
- Test: `tests/test_fiscal_year_labeling.py` (new)

**Interfaces:**
- Consumes: existing `self._parse_iso_date`, `self._is_full_year`; module constant `CANONICAL_FIELDS`; `Dict`, `Tuple`, `Optional` (already imported).
- Produces:
  - `_build_fiscal_year_map(self, us_gaap: Dict[str, Any], form_set: set) -> Dict[str, int]` — maps period-end ISO string → fiscal year.
  - `_fiscal_year_fallback(self, end_iso: Optional[str]) -> Optional[int]` — date-boundary fallback.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fiscal_year_labeling.py`:

```python
"""Tests for authoritative fiscal-year derivation (52/53-week Dec/Jan-boundary fix)."""

import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


@pytest.fixture
def parser():
    return XBRLParser()


# ---------------- _fiscal_year_fallback ----------------

def test_fiscal_year_fallback_early_january_is_prior_year(parser):
    # A 52/53-week December filer whose year-end drifted into early January
    # belongs to the prior fiscal year.
    assert parser._fiscal_year_fallback("2023-01-01") == 2022
    assert parser._fiscal_year_fallback("2021-01-03") == 2020
    assert parser._fiscal_year_fallback("2016-01-07") == 2015


def test_fiscal_year_fallback_keeps_end_year_otherwise(parser):
    assert parser._fiscal_year_fallback("2023-12-31") == 2023   # December filer
    assert parser._fiscal_year_fallback("2026-01-31") == 2026   # Jan-31 retailer (day > 7)
    assert parser._fiscal_year_fallback("2024-09-28") == 2024   # September filer
    assert parser._fiscal_year_fallback(None) is None


# ---------------- _build_fiscal_year_map ----------------

def test_build_fiscal_year_map_uses_original_filing_fy(parser):
    # End 2023-01-01 is reported by the original FY2022 10-K (fy=2022) and carried as
    # a comparative in later 10-Ks with inflated fy. Earliest-filed fp==FY wins.
    us_gaap = {
        "Revenues": {"units": {"USD": [
            usd(78000, "2022-01-03", "2023-01-01", fy=2022, filed="2023-02-16"),
            usd(78000, "2022-01-03", "2023-01-01", fy=2023, filed="2024-02-16"),
            usd(78000, "2022-01-03", "2023-01-01", fy=2024, filed="2025-02-13"),
            usd(85000, "2023-01-02", "2023-12-31", fy=2023, filed="2024-02-16"),
            # A quarter-length fp=Q1 fact must be ignored by the FY map.
            usd(20000, "2023-01-02", "2023-04-02", fy=2023, fp="Q1", form="10-Q",
                filed="2023-04-20"),
        ]}},
    }
    fy_map = parser._build_fiscal_year_map(us_gaap, {"10-K", "10-K/A"})
    assert fy_map == {"2023-01-01": 2022, "2023-12-31": 2023}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fiscal_year_labeling.py -v`
Expected: FAIL — `AttributeError: 'XBRLParser' object has no attribute '_fiscal_year_fallback'` (and `_build_fiscal_year_map`).

- [ ] **Step 3: Implement the two helpers**

In `src/parsers/xbrl_parser.py`, immediately after `_period_year` (after its `return None`, ~line 76), add:

```python
    def _build_fiscal_year_map(self, us_gaap: Dict[str, Any], form_set: set) -> Dict[str, int]:
        """Map each annual period-end to the filer's *own* fiscal year.

        The authoritative source is the SEC ``fy`` of the *original* filing — the
        earliest-filed ``fp == "FY"`` instance for that period-end. Later comparatives
        carry an inflated ``fy`` (the filing's year, not the period's), so the
        earliest-filed instance recovers the declared fiscal year. This is correct for
        52/53-week filers whose year-end crosses the Dec/Jan boundary, where the
        period-end *calendar* year is off by one.
        """
        best: Dict[str, Tuple[str, int]] = {}  # end -> (earliest_filed, fy)
        for field in CANONICAL_FIELDS:
            for tag in field.tags:
                for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in form_set:
                        continue
                    if entry.get("fp") != "FY":
                        continue
                    if not self._is_full_year(entry):
                        continue
                    end = entry.get("end")
                    fy = entry.get("fy")
                    if not end or not isinstance(fy, int):
                        continue
                    filed = entry.get("filed") or ""
                    cur = best.get(end)
                    if cur is None or filed < cur[0] or (filed == cur[0] and fy < cur[1]):
                        best[end] = (filed, fy)
        return {end: fy for end, (_filed, fy) in best.items()}

    def _fiscal_year_fallback(self, end_iso: Optional[str]) -> Optional[int]:
        """Fiscal year when no original ``fp == "FY"`` filing exists for a period-end
        (e.g. a pre-XBRL year seen only as a comparative).

        Uses the period-end calendar year, minus one only for an early-January
        52/53-week year-end (day <= 7), which belongs to the prior (December) fiscal
        year. January-end retailers (day 28-31) keep the end year.
        """
        end = self._parse_iso_date(end_iso)
        if end is None:
            return None
        if end.month == 1 and end.day <= 7:
            return end.year - 1
        return end.year
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fiscal_year_labeling.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/parsers/xbrl_parser.py tests/test_fiscal_year_labeling.py
git commit -m "feat(parser): add authoritative fiscal-year derivation helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Use the map for annual bucketing

Swap the annual `period_key_fn` from `self._period_year` to a lookup that prefers the fiscal-year map, then the date fallback, then `_period_year`.

**Files:**
- Modify: `src/parsers/xbrl_parser.py` — `extract_annual_financials` (~lines 125-135)
- Test: `tests/test_fiscal_year_labeling.py` (append)

**Interfaces:**
- Consumes: `_build_fiscal_year_map`, `_fiscal_year_fallback` (Task 1); existing `_resolve_canonical`, `_period_year`.
- Produces: no new signatures; `extract_annual_financials` now buckets by declared fiscal year.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fiscal_year_labeling.py`:

```python
# ---------------- annual integration ----------------

def test_annual_dec_jan_boundary_no_collision(parser):
    # JNJ-like: FY2022 ends 2023-01-01, FY2023 ends 2023-12-31 -> both calendar 2023.
    # end.year bucketing would collide both into "2023" and drop FY2022; the fy map
    # keeps them distinct.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(78000, "2022-01-03", "2023-01-01", fy=2022, filed="2023-02-16"),
        usd(85000, "2023-01-02", "2023-12-31", fy=2023, filed="2024-02-16", frame="CY2023"),
        # FY2022 carried as a comparative in the FY2023 10-K (inflated fy).
        usd(78000, "2022-01-03", "2023-01-01", fy=2023, filed="2024-02-16"),
    ]}}}}}
    annual = parser.extract_annual_financials(facts, years_back=10)

    assert {"2022", "2023"} <= set(annual.keys())
    assert annual["2022"]["revenue"] == 78000
    assert annual["2022"]["fiscal_year"] == 2022
    assert annual["2022"]["period_end"] == "2023-01-01"
    assert annual["2023"]["revenue"] == 85000
    assert annual["2023"]["fiscal_year"] == 2023
    assert annual["2023"]["period_end"] == "2023-12-31"


def test_annual_january_retailer_unchanged(parser):
    # WMT-like Jan-31 filer: declared FY2026 (fy=2026), macro/calendar 2025.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(648000, "2025-02-01", "2026-01-31", fy=2026, filed="2026-03-15", frame="CY2025"),
    ]}}}}}
    period = parser.extract_annual_financials(facts, years_back=1)["2026"]
    assert period["fiscal_year"] == 2026
    assert period["calendar_year"] == 2025
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fiscal_year_labeling.py::test_annual_dec_jan_boundary_no_collision -v`
Expected: FAIL — `KeyError: '2022'` (both periods currently collapse into `"2023"`).

- [ ] **Step 3: Implement the annual wiring**

In `extract_annual_financials`, replace the body from `us_gaap = ...` through the `_resolve_canonical(...)` call (current lines ~125-135) with:

```python
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        fy_map = self._build_fiscal_year_map(us_gaap, {"10-K", "10-K/A"})

        def annual_fy(entry: Dict[str, Any]) -> Optional[int]:
            end = entry.get("end")
            fy = fy_map.get(end)
            if fy is not None:
                return fy
            fy = self._fiscal_year_fallback(end)
            if fy is not None:
                return fy
            return self._period_year(entry)

        data = self._resolve_canonical(
            us_gaap,
            form_set={"10-K", "10-K/A"},
            valid_fn=self._is_full_year,
            period_key_fn=annual_fy,
            quarterly=False,
        )
```

(The `sorted_years` / `years_back` / return block below is unchanged.)

- [ ] **Step 4: Run the new + existing parser tests**

Run: `python -m pytest tests/test_fiscal_year_labeling.py tests/test_xbrl_parser.py tests/test_fiscal_calendar.py -v`
Expected: PASS — the two new annual tests pass; all existing parser/calendar tests still pass (Sept/Dec filers and the restatement test resolve identically under earliest-filed `fp==FY`).

- [ ] **Step 5: Commit**

```bash
git add src/parsers/xbrl_parser.py tests/test_fiscal_year_labeling.py
git commit -m "fix(parser): bucket annual financials by declared fiscal year

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Fix quarterly ladder fiscal-year label

The discrete-flow ladder currently labels its fiscal year from `_period_year(points[-1])` (the last end's calendar year), which is off by one for a fiscal year ending in early January. Derive it from the ladder's own earliest-filed `fy` instead.

**Files:**
- Modify: `src/parsers/xbrl_parser.py` — `_collect_discrete_flows` (~lines 226-282)
- Test: `tests/test_fiscal_year_labeling.py` (append)

**Interfaces:**
- Consumes: `_fiscal_year_fallback` (Task 1).
- Produces: no new signatures; each quarter's `fiscal_year` now matches the fiscal year it rolls into.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fiscal_year_labeling.py`:

```python
# ---------------- quarterly integration ----------------

def _ni_ladder():
    # JNJ-like: FY2022 (start 2022-01-03, ends 2023-01-01) and FY2023 (start
    # 2023-01-02, ends 2023-12-31). Each is a YTD ladder Q1/H1/9M + 10-K full year.
    return [
        usd(100, "2022-01-03", "2022-04-03", fy=2022, fp="Q1", form="10-Q", filed="2022-04-20"),
        usd(210, "2022-01-03", "2022-07-03", fy=2022, fp="Q2", form="10-Q", filed="2022-07-20"),
        usd(320, "2022-01-03", "2022-10-02", fy=2022, fp="Q3", form="10-Q", filed="2022-10-20"),
        usd(440, "2022-01-03", "2023-01-01", fy=2022, fp="FY", form="10-K", filed="2023-02-16"),
        usd(120, "2023-01-02", "2023-04-02", fy=2023, fp="Q1", form="10-Q", filed="2023-04-20"),
        usd(240, "2023-01-02", "2023-07-02", fy=2023, fp="Q2", form="10-Q", filed="2023-07-20"),
        usd(370, "2023-01-02", "2023-10-01", fy=2023, fp="Q3", form="10-Q", filed="2023-10-20"),
        usd(500, "2023-01-02", "2023-12-31", fy=2023, fp="FY", form="10-K", filed="2024-02-16"),
    ]


def test_quarterly_dec_jan_boundary_fiscal_year(parser):
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": _ni_ladder()}}}}}
    q = parser.extract_quarterly_financials(facts)

    # The early-January year-end quarter rolls into FY2022, not FY2023.
    assert q["2023-01-01"]["fiscal_year"] == 2022
    assert q["2023-01-01"]["fiscal_quarter"] == 4
    assert q["2023-01-01"]["net_income"] == 120          # 440 - 320

    # FY2023's own quarters are labeled 2023, distinct from FY2022's.
    assert q["2023-04-02"]["fiscal_year"] == 2023
    assert q["2022-04-03"]["fiscal_year"] == 2022

    # No fiscal-year bucket holds two of the same fiscal quarter.
    from collections import Counter
    buckets = Counter((p["fiscal_year"], p["fiscal_quarter"]) for p in q.values()
                      if p.get("fiscal_year") and p.get("fiscal_quarter"))
    assert all(n == 1 for n in buckets.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fiscal_year_labeling.py::test_quarterly_dec_jan_boundary_fiscal_year -v`
Expected: FAIL — `assert q["2023-01-01"]["fiscal_year"] == 2022` fails (currently 2023, from `_period_year` of the Jan-1 end).

- [ ] **Step 3: Implement the quarterly fix**

In `_collect_discrete_flows`, inside the `for field in CANONICAL_FIELDS:` loop, add a `start_fy` tracker alongside `chosen`. Change the `chosen` declaration block (current ~lines 230-250) to also record the earliest-filed `fy` per fiscal-year start:

```python
            # Dedup per (start, end): higher-priority tag, then most-recently-filed.
            chosen: Dict[Tuple[str, str], Tuple[int, str, Any]] = {}
            # Per fiscal-period start: the earliest-filed fy (the original filing's
            # declared fiscal year), authoritative even when the year-end is in early
            # January (where the end's calendar year is off by one).
            start_fy: Dict[str, Tuple[str, int]] = {}
            for priority, tag in enumerate(field.tags):
                for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in _QUARTERLY_FORMS:
                        continue
                    start, end = entry.get("start"), entry.get("end")
                    if not start or not end:
                        continue
                    span = self._span(start, end)
                    if span is None or span > _FULL_YEAR_MAX_DAYS:
                        continue  # ignore multi-year facts
                    if entry.get("frame"):
                        frames.setdefault(end, set()).add(entry["frame"])
                    filed = entry.get("filed") or ""
                    fy = entry.get("fy")
                    if isinstance(fy, int):
                        sf = start_fy.get(start)
                        if sf is None or filed < sf[0] or (filed == sf[0] and fy < sf[1]):
                            start_fy[start] = (filed, fy)
                    key = (start, end)
                    cur = chosen.get(key)
                    if cur is not None and not (
                        priority < cur[0] or (priority == cur[0] and filed >= cur[1])
                    ):
                        continue
                    chosen[key] = (priority, filed, entry.get("val"))
```

Then change the ladder-fiscal-year line (current line ~262) from:

```python
                # The fiscal year this ladder belongs to (year of its latest end).
                ladder_fy = self._period_year({"end": points[-1][0]})
```

to:

```python
                # The fiscal year this ladder belongs to: the original filing's
                # declared fy (falls back to a Dec/Jan-boundary date rule).
                sf = start_fy.get(start)
                ladder_fy = sf[1] if sf is not None else self._fiscal_year_fallback(points[-1][0])
```

- [ ] **Step 4: Run the quarterly tests**

Run: `python -m pytest tests/test_fiscal_year_labeling.py tests/test_xbrl_parser.py -v`
Expected: PASS — the new quarterly test passes; the existing ladder/differencing tests (`test_quarterly_ladder_differencing_and_q4`, `test_quarterly_cashflow_from_ytd_only_ladder`, `test_quarterly_no_cap_returns_all_and_limit_works`) still pass (values are unchanged; only the fiscal-year label source changed).

- [ ] **Step 5: Commit**

```bash
git add src/parsers/xbrl_parser.py tests/test_fiscal_year_labeling.py
git commit -m "fix(parser): label quarterly ladders by declared fiscal year

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Docs, full verification, and live merge gate

Update the README note, run the whole suite + linters, then prove the fix on live data per the spec's merge gate.

**Files:**
- Modify: `README.md` (the "Fiscal vs calendar year" subsection, ~line 426-436)

**Interfaces:**
- Consumes: the completed parser fix.
- Produces: nothing new (verification + docs only).

- [ ] **Step 1: Update the README**

In `README.md`, in the "Fiscal vs calendar year" subsection, change the `fiscal_year` bullet to record the authoritative derivation. Replace:

```markdown
- `fiscal_year` — the company's own fiscal year (deterministic, from the period-end date).
```

with:

```markdown
- `fiscal_year` — the company's own fiscal year, taken from the filer's original-filing
  SEC `fy` (the earliest-filed `fp="FY"` instance for the period). This is correct even
  for 52/53-week filers whose year-end crosses the Dec/Jan boundary (e.g. JNJ), where the
  period-end's calendar year would be off by one.
```

- [ ] **Step 2: Run the full test suite + linters**

Run:
```bash
python -m pytest -q
ruff check src tests
mypy src
```
Expected: all tests pass; ruff reports no issues; mypy reports no issues.

- [ ] **Step 3: Commit the docs + verified state**

```bash
git add README.md
git commit -m "docs: fiscal_year now derived from the filer's declared fy

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Rebuild the DB and run the live 50-stock merge gate**

Relabeling changes `(ticker, fiscal_year)` upsert keys, so delete the existing DB first to avoid stale mislabeled rows:

```bash
rm -f data/output/stock.db
python -m src.main JPM BAC WFC C GS USB PGR TRV ALL MET PRU CB PLD AMT EQIX SPG O PSA NEE DUK SO D XOM CVX COP SLB AAPL MSFT GOOGL NVDA AVGO ORCL WMT COST HD PG KO MCD JNJ UNH PFE ABBV MRK CAT HON GE BA VZ T TMUS --no-yahoo
```

- [ ] **Step 5: Verify JNJ labels + score and run regression checks**

Run:
```bash
python - <<'PY'
import sqlite3, json, glob
db = sqlite3.connect("data/output/stock.db"); db.row_factory = sqlite3.Row; c = db.cursor()
# JNJ: contiguous fiscal years, no missing/duplicate buckets, correct early-Jan labels.
rows = list(c.execute("SELECT fiscal_year, calendar_year, period_end FROM "
                      "financials_annual WHERE ticker='JNJ' ORDER BY period_end"))
for r in rows:
    print("JNJ", r["fiscal_year"], r["calendar_year"], r["period_end"])
fys = [r["fiscal_year"] for r in rows]
assert len(fys) == len(set(fys)), "duplicate fiscal_year bucket"
# The year ending 2023-01-01 must be FY2022; 2021-01-03 must be FY2020.
m = {r["period_end"]: r["fiscal_year"] for r in rows}
assert m.get("2023-01-01") == 2022, m.get("2023-01-01")
assert m.get("2021-01-03") == 2020, m.get("2021-01-03")
# WMT (Jan-31) and MSFT (June) unchanged.
for t, end, exp in (("WMT", "2025-01-31", 2025), ("MSFT", "2025-06-30", 2025)):
    got = c.execute("SELECT fiscal_year FROM financials_annual WHERE ticker=? AND period_end=?",
                    (t, end)).fetchone()
    print(t, end, got["fiscal_year"] if got else None)
db.close()
# JNJ data-quality score back to 100 (no quarterly_sum_mismatch).
d = json.load(open("data/output/json/JNJ.json", encoding="utf-8"))
print("JNJ score:", (d.get("data_quality") or {}).get("score"))
print("JNJ findings:", [f.get("code") for f in (d.get("data_quality") or {}).get("findings", [])])
# No company regressed below its prior score (spot-check all scored 50).
scores = []
for fp in glob.glob("data/output/json/*.json"):
    s = (json.load(open(fp, encoding="utf-8")).get("data_quality") or {}).get("score")
    if s is not None:
        scores.append(s)
print("scored:", len(scores), "min:", min(scores), "mean:", round(sum(scores)/len(scores), 1))
PY
```
Expected:
- JNJ fiscal years are contiguous with no duplicates; `2023-01-01 → 2022` and `2021-01-03 → 2020`.
- WMT `2025-01-31 → 2025`, MSFT `2025-06-30 → 2025` (unchanged).
- JNJ score `100`, no `quarterly_sum_mismatch` finding.
- The scored set is the 50 basket tickers; min score not worse than the pre-fix run (PRU 80 may remain — out of scope; no *new* low scores introduced).

If JNJ (or any other Dec/Jan-boundary filer) still shows a `quarterly_sum_mismatch` after relabeling, capture the failing year and its four quarters' values before merging — it would indicate a residual differencing artifact to investigate (not silently merge over).

- [ ] **Step 6: Open the PR (only after the gate is green)**

```bash
git push -u origin fix/fiscal-year-labeling
gh pr create --base main --title "Fix 52/53-week fiscal-year labeling" \
  --body "Derive fiscal_year from the filer's original-filing SEC fy instead of end.year, fixing off-by-one labels, missing year buckets, and quarter-grouping collisions for Dec/Jan-boundary 52/53-week filers (e.g. JNJ). Surfaced by the 50-stock live smoke. Spec/plan in docs/superpowers/. 

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review

**Spec coverage:**
- Authoritative `fy` map → Task 1 (`_build_fiscal_year_map`). ✓
- Early-January fallback for comparative-only periods → Task 1 (`_fiscal_year_fallback`). ✓
- Annual extraction uses the map → Task 2. ✓
- Quarterly grouping/anchoring/dedupe → Task 3 (correct `ladder_fy` makes each `(fy, fq)` bucket unique; the dedupe assertion proves it — no separate dedup code needed since ladders are already grouped by fiscal-year `start`). ✓
- `calendar_year` untouched → verified by `test_annual_january_retailer_unchanged` + the live MSFT/WMT checks. ✓
- Docstring/keep-or-remove `_period_year` → kept (still the final fallback, pinned by `test_period_year_uses_end_then_frame`); new helpers carry the authoritative-derivation docstrings. Spec's "remove if unused" branch does not apply. ✓
- Migration (stale rows) → Task 4 Step 4 deletes the DB before the gate. ✓
- README update → Task 4 Step 1. ✓
- TDD fixtures for the four calendars → JNJ-like (Tasks 2 & 3), WMT-like Jan-31 (Task 2), December/September (existing `test_xbrl_parser` + `test_fiscal_calendar`, re-run in Task 2 Step 4), comparative-only fallback (Task 1). ✓
- Merge gate → Task 4 Steps 4-5. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows full code. ✓

**Type consistency:** `_build_fiscal_year_map(us_gaap, form_set) -> Dict[str, int]` and `_fiscal_year_fallback(end_iso) -> Optional[int]` are used with matching signatures in Task 2 (`annual_fy`) and Task 3 (`start_fy`/`ladder_fy`). `start_fy: Dict[str, Tuple[str, int]]` matches its usage `sf[0]`/`sf[1]`. `_period_year` signature unchanged. ✓
