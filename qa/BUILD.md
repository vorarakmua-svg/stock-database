# Build board: the GuruFocus roadmap, milestones 0-5

- Plan: `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` (approved 2026-10-21)
- Protocol: `qa/BUILD_LOOP_PROMPT.md`
- Status: TODO | DOING | DONE | NEEDS-USER | BLOCKED (why)
- A milestone without a design spec starts with its design task. That task writes the spec and
  replaces its own row with the milestone's task rows.

**Counts:** TODO 12 · DOING 0 · DONE 2 · NEEDS-USER 0 · BLOCKED 0

## M0: the levered-FCF double count

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M0-1 | `_calculate_levered_fcf` stops subtracting interest again; `load_inputs` reads `free_cash_flow`; RED tests, mutants, spec/README/oracle updated; BUG logged and verified; a DCF and verdict diff on a DB copy | DONE | 59e0678 (oracle), this commit (fix) | `qa/evidence/levfcf-*.txt` |

## M1: valuation history and a historical fair value (spec: `docs/superpowers/specs/2026-10-21-valuation-history-design.md`)

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M1-1 | `src/valuation/history_inputs.py`: share series normalization, price factors, known dates, field choice, `load_history_inputs`; with tests | DONE | this commit | `qa/evidence/valhist-m1-1-*.txt` |
| M1-2 | `src/valuation/history.py`: multiples, window statistics, implied prices, the fair-value line, labels, `compute_history`; with tests | TODO | | |
| M1-3 | Storage (`valuation_history`, `valuation_ranges`, `export_valuation_history`) and the engine hook in `compute_and_store`, covering backfill; with tests | TODO | | |
| M1-4 | Real-data validation: `qa/oracle/history_vs_yahoo.py` on a backfilled DB copy; the spec's gates; split continuity; jumps traced; misses explained | TODO | | |
| M1-5 | Repository readers and `GET /api/stocks/{t}/valuation-history`; with tests | TODO | | |
| M1-6 | The VAL tab section (fragment, `renderValHistory`), checked in Chrome on a scratch server; with tests | TODO | | |
| M1-7 | Screener columns (`*_pct_10y`, `price_to_hist_value`), filters, CSV; with tests | TODO | | |
| M1-8 | Docs: README "Valuation history", USAGE_GUIDE, the stale "no price series" text; the multiples-band basis bug logged (NEEDS-USER) | TODO | | |
| M1-9 | Milestone close: code review 15 (opus, 16f3005..HEAD) with Critical and Important findings fixed; then backup and offline backfill of the user's DB; verdict changes reported | TODO | | |

## M2: quality scores and ranks

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M2-0 | Design M2 (plan section "Milestones 2-5"): spec in `docs/superpowers/specs/`, then the task rows here | TODO | | |

## M3: SEC ownership (Form 4 insiders, 13F gurus)

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M3-0 | Design M3: spec, then the task rows | TODO | | |

## M4: more valuation tools and market pages

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M4-0 | Design M4: spec, then the task rows | TODO | | |

## M5: a wider universe and automation

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M5-0 | Design M5: spec, then the task rows | TODO | | |

## NEEDS-USER (each also recorded in `qa/BUGS.md`)

- Q10 [OPS]: a draft PR so CI runs on the branch. Default: no PR; local gates and ruff's py39 target.

## Log

### Build iteration 0: 2026-10-21: setup
Did: the approved plan copied into the repo; the M1 spec written from the design pass; this board and
`qa/BUILD_LOOP_PROMPT.md` created; the Ralph loop started.
Next: M0-1.

### Build iteration 1: 2026-10-21 14:20: M0-1
Did:
- RED tests: `levered_fcf` 180 where 200 is expected, 230 where 250 is; the basis read 70 where 80 is.
- The oracle was put right first (59e0678), reading `free_cash_flow` as the spec says; it then flagged 54 DCF rows.
- The fix: `_calculate_levered_fcf` returns FCF, and `load_inputs` reads `free_cash_flow`; the spec is updated.
- Real data on two backfilled copies: 66 DCF rows change (28 by 10% or more), two verdicts move to fair
  (MCD, PFE), and the oracle agrees (level 1 0/522, level 2 8/487, all Graham bear cases, BUG-007).
- BUG-169 opened as P1 and verified. Q10 opened: no PR is open, so CI doesn't run on the branch.
- The BUGS header's NEEDS-USER count had stayed 11 after Q6's decision; with Q10 it is 11 again, now right.
Evidence: `qa/evidence/levfcf-{red,green,oracle-before,oracle-flags,oracle-after,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1195 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: 59e0678 test(qa): the valuation oracle reads the DCF basis as OCF - capex, as the spec says; (this commit) fix(parsers): levered FCF stops taking off interest that operating cash flow already bears
Next: M1-1.

### Build iteration 2: 2026-10-21 15:05: M1-1
Did:
- `src/valuation/history_inputs.py`: `load_history_inputs` reads the month-end closes, each TTM row's
  fundamentals through `CalculatedMetrics` (sector suppression applied; FCF only with capex), the day each
  became known, and one share field per quarter on the bars' split basis.
- The bars-to-price factor moved out of `load_inputs` into `inputs.bars_to_price_factor` (behaviour unchanged).
- TDD: 17 tests (RED: the module missing), and 15 mutants, each killed.
- Real data (a backup-API copy, read-only): 122 of 127 load; N/A for 3 without quarterly share counts
  (BRK-B, V, one more), BX by the Yahoo guard (1.49x, Q7) and SPCX (no TTM). The latest count is within
  5% of Yahoo's for 117 of 123; 4 splits applied (APH and CRWD after their latest quarter, BKNG, GOOGL);
  C's latest trailing year is stale (2025-12-31).
- Leads for M1-4: TSLA's quarterly weighted basic count (3.24B) lags its period-end 3.95B, about 18% low at
  the current point; the oldest quarters (2009-2013) of PG, LLY, GE and HON lack the chosen share field, so
  their history starts in 2011-2013.
Evidence: `qa/evidence/valhist-m1-1-{red,mutants,realdata}.txt`
Gates: ruff ✓ mypy ✓ pytest 1212 passed, 1 skipped (1 known warning) | CI: no PR (Q10)
Commit: (this commit) feat(valuation): history inputs put every quarter's shares on the price bars' basis
Next: M1-2.
