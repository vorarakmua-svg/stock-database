# Magnitude-Outlier → Spike-and-Revert — Design Spec

**Date:** 2026-06-26
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `fix/magnitude-outlier-spike-revert` (off `main`)

## Context

A 5-sector live test (WMT/BAC/PGR/O/XOM) exposed a false positive in the `magnitude_outlier`
integrity check. **Realty Income (O)** scored **0** with five `magnitude_outlier` findings,
all on `goodwill`:

```
goodwill: 2009-2020 ≈ $15-17M   (Realty Income had minimal goodwill)
          2021 = $3.68B          (VEREIT merger)
          2024 = $4.93B          (Spirit Realty merger)
```

The check computes a field's median over **all** history (dominated by the pre-merger ~$15M
years), so the post-merger years read as 217-291× outliers and are mislabeled *"likely a
mis-resolved tag or filing error,"* zeroing a clean company's score. These are **legitimate
acquisitions**, not errors.

Root cause: the check assumes a field should never be ~100× its own historical median, but
real financial data has legitimate **step-changes** (M&A goodwill/intangibles, divestitures,
new line items). The check cannot distinguish a one-off *error* from a persistent *event*.

## Goal

Make `magnitude_outlier` flag only the actual error signature — a **transient spike that
reverts** — so legitimate step-changes (which jump and *persist*) are never flagged, while
one-off anomalies still are.

### Non-goals

- No change to the other three integrity checks, to `_USD_FIELDS`/`_OUTLIER_EXCLUDE`, or to
  the materiality floor / scored-window concepts.
- Not attempting to flag latest-year anomalies (see "Endpoints" — inherently undecidable).
- The separate energy-coverage issue (XOM) is out of scope (tracked separately).

## Decision (from brainstorming)

**Spike-and-revert.** Flag year *t* only when its value is ≥100× **both** the previous and
next present value of that field. A merger jumps and *stays* (≈1× the next year), so it never
satisfies the "both neighbors" test, at any position in the series. A transient one-off spike
(value jumps then returns) does. Chosen over a recent-window-median approach, which still
false-positives when a merger lands late in the window.

## The rewritten check

`check_field_outliers(annual, scored_years) -> List[Finding]` in `src/validation/integrity.py`:

- For each field in `_USD_FIELDS` (unchanged — still excludes `_OUTLIER_EXCLUDE`):
  - Build `points`: `(year, value, magnitude)` for every year where the field is present and
    non-zero, then **sort by year** (year keys are 4-digit strings → chronological).
  - If fewer than 3 points, skip the field.
  - For each interior index `i` in `1 .. len(points)-2` (so both neighbors exist):
    - `year, value, mag = points[i]`; skip unless `year in scored_years` and `mag >= _MATERIALITY`.
    - `prev_mag = points[i-1].magnitude`, `next_mag = points[i+1].magnitude` (both > 0 by the
      non-zero filter).
    - Flag `Finding(HIGH, "magnitude_outlier", …, year)` when
      `mag >= _OUTLIER_FACTOR * prev_mag` **and** `mag >= _OUTLIER_FACTOR * next_mag`.

Comparisons use multiplication (`mag >= 100 * prev_mag`), not division — no divide-by-zero risk.
`_OUTLIER_FACTOR` stays `100.0`, `_MATERIALITY` stays `$1M`. Flag-only (no mutation).

**Message** changes to reflect the new semantics, e.g.:
`'goodwill' = 1,200,000,000 spikes to 1200x the prior year and 1091x the next; likely a
one-off filing or tag error.`

### Endpoints (deliberate)

The first and last points of a field's series are never flagged — they have only one neighbor.
The last point is typically the **latest year**; a just-happened jump is indistinguishable from
a real recent event without a later value, so it is intentionally not flagged. Other checks
(cash-flow consistency, quarterly-sum, ratio-bounds, balance identity) still cover the latest
period. This is the accepted tradeoff of distinguishing errors from events.

## Testing (TDD)

In `tests/test_integrity.py`:

- **Rewrite** `test_outlier_fires_on_1000x_spike` so the spike is in a **middle** year (e.g.
  revenue `1e9, 1.2e12, 1.1e9, 1.2e9` for 2021-2024 → 2022 spikes and reverts → flagged).
- **Add** `test_outlier_silent_on_persistent_step_change`: `goodwill 1.5e7, 3.7e9, 3.7e9, 4.9e9`
  (the O scenario) → **no** finding (each large year is ≈1× a neighbor).
- Keep (verify still green): `test_outlier_silent_on_real_growth_and_small_series` (no 100×
  jumps; <3 points), `test_outlier_only_flags_scored_years` (spike is the last point and
  unscored → not flagged), `test_magnitude_outlier_excludes_volatile_cashflow_residuals`
  (excluded field, untouched).

## Scope / files

- **Modified:** `src/validation/integrity.py` (`check_field_outliers` only),
  `tests/test_integrity.py`, `README.md` (the magnitude-outlier row: threshold becomes "a
  one-off spike ≥ 100× both adjacent years"; note that persistent M&A step-changes aren't flagged).
- **Untouched:** the other checks, the registry, the schema, pyproject (integrity.py already
  mypy-covered via the `src/validation` directory entry).

## Global constraints

Python 3.9 (no `X|Y`); ruff 120 / E,F,W,I / imports at top; mypy clean; flag-only; all existing
tests green. Commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Merge gate (empirical — the lesson of the cash fix)

Re-run the 5-stock smoke `python -m src.main WMT BAC PGR O XOM --no-yahoo` and confirm:
- **O recovers to score 100 with zero `magnitude_outlier` findings**,
- WMT/BAC/PGR unchanged (still 100), XOM unchanged (still 50 — its energy-coverage issue is
  separate and not addressed here),
- full suite + ruff + mypy green.
Only then merge.
