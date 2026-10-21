# Build board: the GuruFocus roadmap, milestones 0-5

- Plan: `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` (approved 2026-10-21)
- Protocol: `qa/BUILD_LOOP_PROMPT.md`
- Status: TODO | DOING | DONE | NEEDS-USER | BLOCKED (why)
- A milestone without a design spec starts with its design task. That task writes the spec and
  replaces its own row with the milestone's task rows.

**Counts:** TODO 14 · DOING 0 · DONE 0 · NEEDS-USER 0 · BLOCKED 0

## M0: the levered-FCF double count

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M0-1 | `_calculate_levered_fcf` stops subtracting interest again; `load_inputs` reads `free_cash_flow`; RED tests, mutants, spec/README/oracle updated; BUG logged and verified; a DCF and verdict diff on a DB copy | TODO | | |

## M1: valuation history and a historical fair value (spec: `docs/superpowers/specs/2026-10-21-valuation-history-design.md`)

| ID | Task | Status | Commit | Evidence |
|---|---|---|---|---|
| M1-1 | `src/valuation/history_inputs.py`: share series normalization, price factors, known dates, field choice, `load_history_inputs`; with tests | TODO | | |
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

None yet.

## Log

### Build iteration 0: 2026-10-21: setup
Did: the approved plan copied into the repo; the M1 spec written from the design pass; this board and
`qa/BUILD_LOOP_PROMPT.md` created; the Ralph loop started.
Next: M0-1.
