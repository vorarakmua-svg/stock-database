# As-of snapshots: one rule for "what we knew on date D"

Date: 2026-11-18. Status: draft for the user's approval. Structural item #2 of the P3 triage (2026-11-18).

## Intent

The as-of (point-in-time) view exists for **backtesting**: "what would our own annual numbers have said on date D,
using only what had been filed by D?" (the user, 2026-11-18: option (a), not "what the filings literally said").

Today three pieces of code each decide which filing's figure wins, and they disagree:

1. **The annual record** (`XBRLParser.extract_annual_financials` + the fetcher's `_clean_and_derive`): tag rank
   first, latest filing after, set-asides, `_restore_agreed_figures` (a later filing's tagging error put right),
   share-scale normalisation, derivations, validation.
2. **Per-filing vintages** (`extract_annual_vintages`): their own walk, but set-asides (`self._aside(us_gaap)`) are
   computed from *every* filing, later ones included: look-ahead (BUG-120).
3. **The as-of reader** (`src/query/asof.py`): rebuilds a year from stored vintage rows with a third rule set
   (carry forward, BUG-110; "narrower" later figures, BUG-271; debt from one filing's view, BUG-334).

Each fix patched one path; the others drifted (1,250 of 95,407 figures differ, BUG-116). This design replaces
path 3, and the look-ahead in path 2, with **replays of path 1**.

## Goals

- **One rule.** The as-of view of fiscal year Y on date D is exactly the annual record our code would produce from
  the facts filed on or before D. No as-of-specific figure-selection rule exists anywhere.
- **No look-ahead by construction.** The only gate is one filter, `filed <= D`, applied to the facts *before* the
  pipeline runs. Nothing the pipeline reads may come from after D.
- **Agreement guarantee.** The latest snapshot of every year equals the annual record the same run builds for that
  year, cell for cell. The reference is the run's freshly built record, not the stored `financials_annual` table:
  the store only upserts, so the table can also hold years an older parse made, years kept from a `years_back`
  run, and fill-only cells (BUG-179/287). Those leftovers are out of scope here (filed as their own bug).
- **Deterministic** (depends on BUG-277's fix, f786e89).
- Close, by construction, BUG-116, 120, 085, 334, 268, and as far as the annual record handles them, 113, 132, 287,
  320, 339. Each is re-checked and marked VERIFIED or left open with the reason.

## Non-goals

- Quarterly and TTM as-of views (none exist today; not added).
- Changing any annual-record rule. The refactor that makes the pipeline replayable must be behaviour-preserving.
- The valuation engine's use of vintages (`MIN(filed_date)` per year in `inputs.py`, `history_inputs.py`): unchanged.

## Anti-goals (a failure even if it "works")

- Any as-of figure that depends on a fact filed after D.
- Any change to `financials_annual` (or anything downstream: metrics, scores, valuations) caused by the refactor.
- A second copy of selection logic creeping back into the reader "for speed" or "for a special case".

## Constraints

- Python 3.9-compatible; SQLite store; web app opens the DB read-only.
- Collection runs offline from the SEC cache for all 502 routinely; RAM is often under 5 GB.
- Repo conventions: CRLF, README documents every rule, ledger/LOG under `qa/`.
- **BUG-341 (P2) is a hard prerequisite, fixed separately first** (a worker is on it; if it needs a product decision
  on transition periods, that goes to the user). A fiscal-year key holding two fiscal years would otherwise be
  replayed into every snapshot. The snapshot key follows whatever key the annual record has after BUG-341. All
  before/after baselines are taken after BUG-341 lands.

## Approaches considered

- **A. Snapshots per filing date (chosen by the user).** Replay the annual pipeline on the facts known at each
  filing date; store a year's row when it changes; the as-of query reads the latest snapshot on or before D. One
  rule and no look-ahead by construction; cost is collection-time CPU (one pipeline run per annual filing date).
- B. Re-run the pipeline at query time from raw companyfacts. Same answers, no new table, but a 500-company
  backtest re-parses every company per query and the web app would need the ~29 GB cache. Rejected: too slow.
- C. Shared rule functions used by both the vintage build and `asof.py`. Smaller change, but the reader works from
  stored rows that no longer hold the alternative tags and later-filing evidence the repairs need, so it can only
  approximate the record. Rejected: disagreement shrinks but never ends.

## Design

### 1. A replayable annual pipeline

Extract one pure function (name the builder's call), e.g. `build_annual_record(facts, *, sector_class, years_back)
-> (periods, warnings)`, holding everything that today turns companyfacts into `financials_annual`:
`extract_annual_financials` (resolve, set-asides, restore), `normalize_share_scale(..., neighbours=True)`,
`apply_derivations`, `validate_period`. The fetcher calls it for today's record; the snapshot builder calls it per
date. Each period also carries per-field provenance (`{field: {accn, filed, form}}`); the parser already knows
each winning entry's `accn`/`filed` in `_assign_if_better`.

Behaviour-preserving: `financials_annual` must be byte-identical before and after over all 502 (census + DB-copy
diff of every table).

### 2. Snapshot dates and the look-ahead gate

- Snapshot dates for a company: every distinct `filed` date among **all** the facts the pipeline reads, of any form
  (its preprocessing reads 10-Qs too: `_filer_facts`, `_kept_totals`, `_foreign_filings`), Q17's filing-XBRL facts
  included, ascending. So the as-of view at any D, not only on a 10-K's date, equals a fresh build on the facts
  filed by D. This means roughly 4-5x more pipeline runs than 10-K dates alone; dedupe keeps storage small, and the
  cost is measured (verification 5). An optimisation that skips dates is allowed only if the any-D oracle still
  passes.
- For each date d: `known = filter(facts, filed <= d)` (one helper, the only gate), then
  `build_annual_record(known, ...)`.
- **A year not yet ended is not known**: the shared pipeline drops any fiscal year whose end is after the date of
  the latest fact it was given (BUG-113: BAC's forward-dated 2011 dividend in a 10-K filed 2011-02-25 opened
  FY2011). A year's end is the end most of its flow figures share (BUG-277's rule), else its `period_end`. Because
  the rule lives in the shared pipeline it applies to today's annual record too (a year holding only a forward-dated
  fact, such as LLY's FY2014 short-term borrowings at 2014-03-31 in its FY2013 10-K, is no longer a "latest year").
  This is the one intended change to the annual record; its moves are measured and any valuation move goes to the
  user first.
- Same-day: a fact filed on D is known on D (as today: `filed_date <= D`).
- Inputs other than facts: `sector_class` is the run's current one (treated as a fixed property of the company);
  documented as the one known exception. Nothing else external enters the pipeline (no Yahoo data).

### 3. Storage

New table `financials_annual_known` (name the builder's call): one row per `(ticker, fiscal_year, known_from)`,
holding the canonical columns, `source_tags`, per-field provenance JSON, `period_end`, `calendar_year`, `frame`,
and `known_accn` (the filing whose date opened the snapshot). A row is written only when the year's row differs
from its previous snapshot (dedupe), so size stays near today's vintage table (~30k rows over 499 tickers).
Index `(ticker, fiscal_year, known_from)`.

A full run replaces all of a ticker's snapshot rows. A `years_back` run replaces only the rows of the years it
builds, with snapshots replayed on the full facts and trimmed to those years.
`calendar_year` and `frame` come from SEC's rule (`_frame_of_end`, BUG-277). SEC attaches `frame` to the latest
filed fact of a period, so reading it would be metadata look-ahead. BUG-277 found the rule gives every one of the
5.2 million cached frames, so the values equal the annual record's.

Writes follow the store's existing per-run transaction. A fill-only run (BUG-179: predecessor read failed) leaves a
ticker's snapshot rows untouched; it doesn't rebuild them from partial facts.

### 4. The as-of reader

`AsOfReader.as_of_annual(ticker, fy, D)`: the row with the greatest `known_from <= D`, or None.
`history_as_of`: that, for every fiscal year with a row `known_from <= D`. Return shape kept for the API, the web
fragment and `PointInTimeMetrics`: canonical fields, `accn`/`filed_date`/`form`/`period_end` (the snapshot's
latest contributing filing), and `carried_from` built from the per-field provenance (fields whose filing isn't the
latest). `_held`, `_narrower`, `_debt_view` and the other rule helpers are deleted.

A ticker with no snapshot rows (a database not re-collected since the change) returns None, and the ASOF page says
the as-of data isn't built for that ticker yet (my call: no fallback to the old rules, which would keep two rules
alive). The 502 are re-collected offline once after the change.

### 5. Vintages ("as filed")

`financials_annual_vintages` stays as the per-filing "as filed" record (explorer side-by-side tables; valuation's
first-filed dates). Its set-asides are computed from the facts known at that filing's date (the same gate), which
removes BUG-120's look-ahead from the explorer too. Builder's call whether the vintage of filing k comes out of
snapshot k's run or a separate gated call.

## Verification (what "done" means)

1. **Agreement census:** for all 502, the latest snapshot of each year equals the run's freshly built annual
   record cell for cell.
2. **Look-ahead oracle:** for a sample of (ticker, D) pairs, D drawn at arbitrary dates (not only filing dates), the
   stored as-of view equals a fresh
   `build_annual_record` on facts filtered to `filed <= D`, and no per-field provenance is filed after D;
   review21's `lookahead.py` gives 0.
3. **No downstream moves:** DB copies before/after (engine over 502): every valuation, metrics, scores and
   quality table identical; only the as-of view, the new table and vintages' set-asides change. Every as-of move
   is listed by bug it closes.
4. **Determinism:** two runs under different `PYTHONHASHSEED` give identical snapshot tables.
5. **Cost measured:** collection time and RAM for the 502 before/after; DB size growth.
6. The ten bugs above each re-checked, VERIFIED or left open with the reason; README's as-of section rewritten.

## Left to the builder

Function and table names; how per-field provenance is threaded through; batching of snapshot runs (e.g. reuse of
parsed state between dates) if the cost measurement calls for it; the vintage build's placement (section 5); the
migration that creates the table.

## Calls I made (for the user to check)

- `sector_class` is treated as fixed over time (not replayed).
- No fallback to the old as-of rules for un-rebuilt tickers.
- Years not yet ended are dropped, in the shared pipeline, so from today's annual record too (the one intended
  record change; measured).
- Snapshots open on every filing date of any form, so "as of any D" holds exactly, at the cost of more collection CPU
  (measured; optimise only under the any-D oracle).
- The agreement reference is the run's own fresh record; stale stored rows are a separate bug.
- Fill-only runs don't touch snapshots.
- BUG-341 goes first, as its own fix.
