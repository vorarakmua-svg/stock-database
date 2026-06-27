# 52/53-Week Fiscal-Year Labeling Fix — Design Spec

**Date:** 2026-06-28
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `fix/fiscal-year-labeling` (off `main`)

## Context

A 50-stock cross-sector live test (mean data-quality 99.3) surfaced one finding that traced to a
real correctness bug in fiscal-year labeling. **JNJ** drew a `quarterly_sum_mismatch` on 16
income-statement fields for "FY2023"; on inspection the four *genuine* FY2023 quarters
(period-ends 2023-04-02 … 2023-12-31) sum to the annual revenue **exactly** (85,159M). The
finding was a symptom, not the disease.

**Root cause.** `XBRLParser._period_year` (`src/parsers/xbrl_parser.py:54-76`) derives the fiscal
year a fact covers from `end.year` — the calendar year of the period-end date. JNJ is a 52/53-week
filer whose fiscal year ends on the Sunday nearest Dec 31, so it oscillates across the Dec/Jan
boundary. Every early-January year-end is mislabeled by +1:

| Period-end | True fiscal year | Current label (`end.year`) |
|---|---|---|
| 2021-01-03 | FY2020 | FY2021 |
| 2022-01-02 | FY2021 | FY2022 |
| 2023-01-01 | FY2022 | FY2023 |
| 2023-12-31 | FY2023 | FY2023 (correct) |

Consequences, confirmed against live data:
- **Annual:** off-by-one `fiscal_year` labels, with **missing year buckets** (JNJ has no FY2009 /
  FY2015 / FY2020 — they are labeled 2010 / 2016 / 2021). `fiscal_year` is a DB join key
  (`metrics_annual JOIN financials_annual ON ticker AND fiscal_year`), so this is a data-integrity
  bug, not merely a cosmetic label.
- **Quarterly:** two different fiscal-year sets collapse into one `fiscal_year` bucket — JNJ's
  `fiscal_year=2023` holds **eight** quarters (two of each `fiscal_quarter`). `check_quarterly_sums`
  groups `by_fy[int(fy)][int(fq)]` and the later insertion wins per `fq`, mixing FY2022 and FY2023
  quarters (and surfacing ladder-differencing artifacts like a 27,606M "Q2"). Hence the false
  `quarterly_sum_mismatch`.

`calendar_year` (derived from the SEC `frame`, e.g. `CY2020`) is **already correct** throughout —
which is why the mission's "always compare with `calendar_year`" guidance has masked the bug. This
spec fixes `fiscal_year`; it does not touch `calendar_year`.

## Decision (from brainstorming)

Derive each period's fiscal year from the filer's **own declaration** — the SEC `fy` of the
*original* filing — rather than from `end.year`.

The current code's docstring warns that `fy` is "the `fy` of the *filing*, not the period." That is
true only for **comparative** facts: a prior year shown in a later 10-K carries that later filing's
`fy`. The **original** filing reports a period as its *current* period with the correct `fy`, and it
is the earliest-filed instance. So: among all facts for a given period-end with `fp == "FY"`, take
the `fy` of the **earliest-filed** instance (equivalently, `min(fy)`).

Validated against raw `companyfacts`:
- JNJ end 2021-01-03 → original `fy=2020` (filed 2021-02-22); comparatives carry 2021, 2022.
  `min(fy where fp==FY) = 2020`. ✓ Also 2023-01-01 → 2022, 2016-01-03 → 2015, 2023-12-31 → 2023.
- WMT (Jan-31 retailer) end 2025-01-31 → original `fy=2025` (comparative carries 2026,
  `frame=CY2024`). `min(fy) = 2025`. ✓ Confirms `fiscal_year` (2025) ≠ `calendar_year` (2024) and
  that the rule leaves genuine January-end retailers correct.

**Rejected alternatives.** (B) A pure date heuristic (early-Jan end → prior year): minimal but a
rule-of-thumb that ignores the authoritative signal. (C) Derive `fiscal_year` from
`calendar_year` + a fiscal-year-end offset: redundant with frames and more complex. We adopt (A),
with (B) demoted to a bounded fallback (below).

## Fix

All changes are in `src/parsers/xbrl_parser.py`.

### 1. Authoritative fiscal-year map

New helper that scans the corpus once per company and maps each annual period-end to its true
fiscal year:

```python
def _build_fiscal_year_map(self, us_gaap: Dict[str, Any], form_set: set) -> Dict[str, int]:
    """Map each annual period-end -> the filer's own fiscal year.

    Uses the SEC ``fy`` of the *earliest-filed* ``fp == "FY"`` instance for that
    period-end (the original 10-K, before later comparatives inflate ``fy``). This is
    authoritative for 52/53-week filers whose year-end crosses the Dec/Jan boundary,
    where ``end.year`` is off by one.
    """
```

Implementation notes:
- Iterate every tag's `units` (USD and shares as needed — in practice any full-year `fp=="FY"`
  duration fact suffices; balance-sheet instant facts also carry `fp=="FY"` at year-end and may be
  used). Keep only entries with `form in form_set`, `fp == "FY"`, and a full-year span
  (`_is_full_year`, which also admits instants).
- For each `end`, track the `fy` of the entry with the **earliest `filed`** date (tie-break:
  smaller `fy`). Filter out non-`FY` `fp` values so a year-end instant that also appears as a
  quarter-comparative opening balance (e.g. 2021-01-03 with `fp=Q1`, `fy=2021`) cannot pollute the
  map.

### 2. Fallback for comparative-only periods

A very old year whose original 10-K predates mandatory XBRL may appear only as a comparative (no
`fp=="FY"` original in `companyfacts`). When the map has no entry for a period-end, fall back to a
Dec/Jan-boundary-aware rule:

```python
def _fiscal_year_fallback(self, end_iso: str) -> Optional[int]:
    """No original FY filing available: use end.year, minus one only for an
    early-January 52/53-week year-end (day <= 7)."""
```

Rationale: `end.year` is correct for every fiscal-year-end **except** a December-type 52/53-week
filer that drifted into early January (days 1–4). The `day <= 7` threshold cleanly separates that
case from January-end retailers (WMT ends day 28–31), which keep `end.year`.

### 3. Annual extraction uses the map

In `extract_annual_financials`, build the map and pass a lookup as `period_key_fn` instead of the
bare `self._period_year`:

```python
        fy_map = self._build_fiscal_year_map(us_gaap, {"10-K", "10-K/A"})
        data = self._resolve_canonical(
            us_gaap,
            form_set={"10-K", "10-K/A"},
            valid_fn=self._is_full_year,
            period_key_fn=lambda e: fy_map.get(e.get("end")) or self._fiscal_year_fallback(e.get("end")),
            quarterly=False,
        )
```

`calendar_year` continues to come from `_apply_calendar` (SEC frames) and is untouched.

### 4. Quarterly grouping and differencing

`extract_quarterly_financials` / `_collect_discrete_flows` currently assign a quarter's
`fiscal_year` from `ladder_fy = self._period_year(...)` and `fiscal_quarter` from the cumulative
span. Correct both so each `(fiscal_year, fiscal_quarter)` maps to exactly one quarter:

- **`fiscal_year` per quarter:** the fiscal year of the annual period the quarter rolls into —
  derived from the quarter's *original* filing `fy` (earliest-filed `fp in {Q1,Q2,Q3}` instance at
  that quarter-end), consistent with the annual map. Q4 (derived as annual − 9-month YTD) inherits
  the annual period's fiscal year and `fiscal_quarter = 4`.
- **Ladder anchoring:** difference the cumulative YTD ladder **within a single fiscal year**
  (anchored at that fiscal year's start), so a fiscal-year boundary can no longer produce a
  cross-year artifact quarter.
- **Dedupe:** guarantee one quarter per `(fiscal_year, fiscal_quarter)` so no `fiscal_year` bucket
  ever holds eight quarters.

`calendar_quarter` (from `_apply_quarter_calendar` / frames) is unchanged.

### 5. Docstring

Update the `_period_year` docstring (and any references) to record that the original-filing `fy` is
authoritative for the period it primarily reports, superseding the prior "always use `end.year`"
rationale. If `_period_year` is no longer called anywhere after the swap, remove it; otherwise leave
it only for its remaining callers.

## Architecture / scope

- **Modified:** `src/parsers/xbrl_parser.py` (fiscal-year map + fallback; annual `period_key_fn`;
  quarterly grouping/anchoring/dedupe; docstring), tests, `README.md` (note that `fiscal_year` is
  now the filer's declared year, correct for 52/53-week Dec/Jan-boundary filers).
- **Untouched:** `calendar_year`/`calendar_quarter` (frames), `_resolve_canonical` value selection,
  the validation layer, the canonical registry, sector logic, the DB schema.

## Out of scope (YAGNI)

- Discontinued-operations cash capture (PRU's separate finding) — stays on backlog.
- Point-in-time / restatement / look-ahead handling — stays on backlog.
- PSA REIT `ebitda_margin` proxy bound — by design, stays on backlog.

## Migration

Relabeling changes `(ticker, fiscal_year)` upsert keys, so an existing `stock.db` will keep stale
mislabeled rows (e.g. a phantom JNJ `FY2021` from a prior run) alongside the corrected rows. The
implementation plan must either (a) rebuild the DB from scratch for the merge-gate run, or (b) add
a delete-existing-`(ticker, fiscal_year)`-rows-before-write step for the affected tickers. Tests use
fresh fixtures and are unaffected.

## Testing (TDD)

Pure-parser tests with synthetic `companyfacts` fixtures (no network), one per calendar:
- **52/53-week early-Jan filer (JNJ-like):** original `fp==FY` facts at ends 2023-01-01 (`fy=2022`)
  and 2023-12-31 (`fy=2023`), plus later comparatives carrying inflated `fy`. Assert annual labels
  → FY2022 and FY2023 (no off-by-one, no collision, no missing bucket). Provide four FY2023 quarters
  and assert they group under FY2023, sum to the annual, and `check_quarterly_sums` reports **no**
  finding.
- **January-31 retailer (WMT-like):** end 2025-01-31 original `fy=2025`, comparative `fy=2026`,
  `frame=CY2024`. Assert `fiscal_year == 2025` and `calendar_year == 2024` (rule does not regress
  genuine Jan-end filers).
- **Plain December filer:** end 2024-12-31 `fy=2024`. Assert `fiscal_year == 2024` unchanged.
- **Comparative-only fallback:** a period-end with no `fp==FY` original — assert the fallback
  yields `end.year` for a December end and `end.year - 1` for an early-January (day ≤ 7) end.
- Update any existing parser tests that assumed `end.year` labeling.
- Full suite + ruff + mypy green.

## Global constraints

Python 3.9 (no `X | Y` unions); ruff (line-length 120; E,F,W,I) with imports at top of file (avoid
E402); mypy clean. Commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical)

Rebuild the DB and re-run the 50-stock basket
(`python -m src.main JPM BAC WFC C GS USB PGR TRV ALL MET PRU CB PLD AMT EQIX SPG O PSA NEE DUK SO
D XOM CVX COP SLB AAPL MSFT GOOGL NVDA AVGO ORCL WMT COST HD PG KO MCD JNJ UNH PFE ABBV MRK CAT HON
GE BA VZ T TMUS --no-yahoo`) and confirm:
- **JNJ** annual `fiscal_year` labels are contiguous and correct (FY ending 2023-01-01 → FY2022,
  2021-01-03 → FY2020; no missing FY2009/2015/2020), the four real quarters per fiscal year sum to
  the annual, `quarterly_sum_mismatch` is gone → **score 100**.
- **Regression:** WMT (Jan-31 end), COST (early-September 52/53-week end), MSFT (June end), and a
  plain December filer keep correct, unchanged `fiscal_year` labels and show no new findings;
  `calendar_year` unchanged across the basket.
- The 49 other companies' scores do not regress.
- Full suite + ruff + mypy green.

Only then merge.
