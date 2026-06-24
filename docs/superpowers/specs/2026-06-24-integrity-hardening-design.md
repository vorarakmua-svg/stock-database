# Integrity / Trust Hardening — Design Spec

**Date:** 2026-06-24
**Status:** Approved (brainstorming) → ready for implementation plan

## Context

The pipeline now produces broad (all sectors), comparable (canonical + sector-aware
metrics), and calendar-aligned fundamentals. The remaining gap is **trust**: the quality
layer (`src/validation/quality.py`) checks required fields per sector, the balance identity
(`A = L + E + NCI`), gross-profit consistency, outflow signs, EPS plausibility, and a
revenue-only YoY continuity flag — but it cannot catch several classes of *confidently-wrong*
values that a now-comprehensive, screened-upon database must not silently carry:

- a single canonical field whose magnitude is wildly inconsistent with its own history
  (a mis-resolved tag or a filing error);
- a cash-flow statement that doesn't reconcile to the change in balance-sheet cash
  (a mis-mapped cash-flow section);
- discrete quarters that don't sum to the annual figure (a ladder-differencing or
  cross-filing error — verified once for AAPL by hand, never automatically);
- a computed ratio that is mathematically impossible or wildly implausible
  (e.g. a >100% gross margin, a negative efficiency ratio).

## Goal

Extend the quality layer with four integrity checks so the now-comprehensive data is
**provably trustworthy**, surfacing these errors through the existing 0–100 quality score
and findings/warnings — without rearchitecting storage or mutating data.

### Non-goals (out of scope this round)

- No schema change. Findings ride the existing `data_quality` field and `collection_runs`
  table; no new tables/columns.
- **Flag-only.** Checks never mutate, null, or "correct" a detected-bad value. Data is
  preserved (consistent with the `unmapped_facts` "never silently lose data" principle);
  the finding + score + warning surface the issue for a human to judge.
- No point-in-time/restatement handling, no incremental updates (separate future work).
- Outlier and cash-reconciliation checks run on **annual** data only this round (quarterly
  outlier/recon is noisier; deferred).

## Decisions locked during brainstorming

1. **Calibrated penalties** feed the existing 0–100 score: `magnitude_outlier` HIGH (−25),
   `cashflow_reconcile` MEDIUM (−10), `quarterly_sum_check` MEDIUM (−10), `ratio_bounds`
   LOW (−3). Thresholds are deliberately wide so normal filings stay at 100.
2. **Flag-only**, never mutate data (see non-goals).

## The four checks

All reuse `quality.py`'s `Finding` dataclass, the `HIGH/MEDIUM/LOW` severities, the
`_PENALTY` map, and the `_num` accessor. Each is a pure function over already-fetched dicts.
A `$1M` materiality floor and the `recent_years` scoring window (default 5) bound the long
tail — findings are emitted only for periods inside the scored window, though full history
may be used for context (e.g. computing a field's median).

### 1. `magnitude_outlier` → HIGH (−25), code `magnitude_outlier`

Operates on annual periods. For each canonical field with `unit == UNIT_USD` (income,
balance, cash-flow level amounts — excludes per-share, share counts):

- Collect `|value|` across all retained years; keep nonzero values; require **≥ 3** of them.
- Compute their median; skip the field if `median < $1M` (materiality floor).
- For each year in the **scored window**, flag if `|value| / median ≥ 100`.

Only the **high direction** is flagged (a value ≥100× its own median is almost certainly a
mis-resolved tag or error). The low direction is not flagged this round (a near-zero year is
usually legitimate); noted as future work. 100× is far above real YoY growth, so genuine
trends never fire.

### 2. `cashflow_reconcile` → MEDIUM (−10), code `cashflow_imbalance`

Operates on consecutive annual year pairs `(t-1, t)` where `cash_and_equivalents` exists in
both and `operating_cash_flow`, `investing_cash_flow`, `financing_cash_flow` all exist in `t`:

- `delta_cash = cash[t] - cash[t-1]`; `flow_sum = OCF[t] + ICF[t] + FCF[t]`;
  `residual = delta_cash - flow_sum`.
- `denom = max(|delta_cash|, |flow_sum|)`; skip if `denom < $1M`.
- Flag year `t` if `|residual| / denom > 0.05`.

The 5% tolerance absorbs the foreign-exchange-effect-on-cash line (not a canonical field)
and minor restricted-cash reclassifications, which are typically <2–3%.

### 3. `quarterly_sum_check` → MEDIUM (−10), code `quarterly_sum_mismatch`

For each `fiscal_year` present in the annual data, gather quarterly periods with that
`fiscal_year` whose `fiscal_quarter ∈ {1,2,3,4}`; proceed only when **all four** are present.
Summable fields = canonical fields with `kind == DURATION` and `statement ∈ {INCOME,
CASHFLOW}` and `unit == UNIT_USD` (flows; excludes per-share durations and weighted-average
share counts, which don't sum across quarters). For each such field present in the annual
period **and** all four quarters:

- `sum_q = Σ quarter values`; `ann = annual[fy][field]`; skip if `|ann| < $1M`.
- Flag `(field, fy)` if `|sum_q - ann| / |ann| > 0.01`.

This validates the cumulative-ladder differencing across every company, not just spot-checks.

### 4. `ratio_bounds` → LOW (−3), code `ratio_out_of_bounds`

Operates on the computed historical metrics `{year: {metric: value}}`, per year in the scored
window. Hard impossibility / strong-implausibility bounds (flag when outside):

| Metric(s) | Flag when |
|---|---|
| `gross_margin`, `operating_margin`, `ebitda_margin` | `> 1.01` (output can't exceed revenue) |
| `net_margin`, `fcf_margin` | `\| · \| > 2.0` |
| `roe`, `roa`, `roic` | `\| · \| > 5.0` |
| `efficiency_ratio` | `≤ 0` or `> 2.0` |
| `loss_ratio`, `combined_ratio` | `< 0` or `> 3.0` |
| `loan_to_deposit` | `< 0` or `> 5.0` |
| `ffo_payout` | `< 0` or `> 5.0` |
| `net_interest_margin` | `< 0` or `> 0.25` |

`None` metrics are skipped. Bounds are intentionally loose (impossibility, not "unusual") so
legitimately strong companies (e.g. Apple's ~82% ROIC) never fire.

## Architecture

New `src/validation/integrity.py` — pure functions, fully type-annotated, no network:

- `check_field_outliers(annual, scored_years) -> List[Finding]`
- `check_cashflow_reconciliation(annual, scored_years) -> List[Finding]`
- `check_quarterly_sums(annual, quarterly, scored_years) -> List[Finding]`
- `check_ratio_bounds(historical_metrics, scored_years) -> List[Finding]`

`integrity` imports `Finding`, `HIGH`/`MEDIUM`/`LOW`, and `_num` from `quality` (one-way
dependency; no cycle). The summable-flow set and the USD-level-field set for checks 1 and 3
are derived from `CANONICAL_FIELDS` so they auto-track registry additions.

`quality.py` gains one small extraction: `score_for(findings: List[Finding]) -> int`
(the existing `100 - Σ_PENALTY`, clamped at 0), used by `assess_annual` and by the
orchestration after integrity findings are merged, so the score is computed once over all
findings.

## Wiring & data flow

The three field/quarterly checks need annual + quarterly (available before metrics);
`ratio_bounds` needs the computed metrics (currently produced after validation). To give a
single assessment all inputs, split the fetcher's current `_validate_and_score` into:

1. `_clean_and_derive(stock)` — `apply_derivations` + `validate_period` (+ warnings) + TTM
   (the current first half).
2. `_compute_metrics(stock)` — unchanged (already exists; needs derived fields, which step 1
   produced).
3. `_assess(stock)` — `report = assess_annual(annual, sector)`; append the four integrity
   checks' findings (scored window); `report.score = score_for(report.findings)`; set
   `stock.data_quality` (+ `unmapped_tag_count`) and push MEDIUM+ warnings.

`fetch_ticker` calls them in that order. Net: one coherent quality pass that sees cleaned
data **and** metrics, one report, one score — same `stock.data_quality` / `warnings` surface
as today.

## Scoring

Penalties reuse `_PENALTY` (HIGH 25, MEDIUM 10, LOW 3); multiple findings compound;
`score = max(0, 100 − Σ)`, over the `recent_years=5` window. Conservative thresholds keep
clean filings at 100. New finding codes: `magnitude_outlier`, `cashflow_imbalance`,
`quarterly_sum_mismatch`, `ratio_out_of_bounds`.

## Error handling

Pure functions never raise on missing/odd inputs — a missing field, a zero denominator, or
fewer than the required periods means the check simply emits nothing for that case (mirrors
`quality.py`'s `_num`-guarded style). The `_assess` step stays inside the fetcher's existing
try/except so any failure becomes a warning, never a crash.

## Testing (TDD)

New `tests/test_integrity.py`, each check from synthetic dicts:

- **`magnitude_outlier`:** a 3+-year series with one 1000× spike fires (HIGH); a clean
  multi-year series and a real ~2–3× trend do **not**.
- **`cashflow_reconcile`:** a set missing Δcash by 20% fires (MEDIUM); a 3% gap does **not**
  (tolerance); insufficient fields → silent.
- **`quarterly_sum_check`:** four quarters summing to ≠ annual (>1%) fires (MEDIUM); an exact
  ladder does **not**; only-3-quarters → silent.
- **`ratio_bounds`:** a 250% gross margin and a negative efficiency ratio fire (LOW); Apple-like
  82% ROIC does **not**.
- **Negative control + scoring:** clean multi-year data → zero integrity findings → score 100.
- Extend `test_quality` / fetcher tests for the consolidated `_clean_and_derive` →
  `_compute_metrics` → `_assess` pass. All current tests stay green.

## Scope / files

- **New:** `src/validation/integrity.py`, `tests/test_integrity.py`.
- **Modified:** `src/validation/quality.py` (extract `score_for`), `src/fetchers/
  stock_data_fetcher.py` (split `_validate_and_score` into `_clean_and_derive` + `_assess`,
  reorder with `_compute_metrics`), `pyproject.toml` (add `integrity.py` to mypy `files`),
  `README.md` (document the integrity checks). No schema/registry/parser changes.

## Global constraints (carried into the plan)

- Python 3.9 floor (no `X | Y` unions); ruff line-length 120, select E,F,W,I, imports at top
  (E402); mypy clean on listed files (`integrity.py` added). All existing tests stay green;
  clean filings keep score 100 (no regressions from false positives).

## Future work (not this spec)

- Low-direction magnitude outliers; quarterly outlier/cash-recon; point-in-time/restatement;
  configurable thresholds.
