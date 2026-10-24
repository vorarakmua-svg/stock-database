# A wider universe and automation: design (Milestone 5)

Plan: `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` ("Milestones 2-5", M5). Build loop:
`qa/BUILD_LOOP_PROMPT.md`.

## Goal

The app covers 127 hand-picked large caps, collected by hand. GuruFocus covers every listed company, keeps them
current on its own, and ranks each against its industry. This milestone takes the app to the S&P 500 and keeps it
current with one command:
- **A universe in the repo**: the S&P 500's members from a public list, saved as a dated file, and a `--tickers-file`
  option to collect a list.
- **Industries**: each company's SEC SIC code stored, industry groups from it, and M2's five ranks also computed
  within the industry where it has at least 10 companies; an industry filter in the screener.
- **`stock-data refresh`**: quotes and new daily bars every day, the SEC's new filings every week, the insiders, the
  gurus and the market data incrementally, then the recomputation, in one command a scheduler can run. Installing
  the Windows scheduled task stays the user's decision (Q15).
- **Scale**: the pages stay fast with about 500 companies, checked before and after the collection.

## Facts that shape the design (verified 2026-10-25)

- `stock-data` (`src/main.py`) takes tickers as positional arguments only, collects them with `StockDataFetcher`
  (4 workers), and its collection ends in `valuation_engine.compute_and_store` for the tickers it stored, so the
  valuations, the history, the scores and the indicators follow any collection. The 127 companies were collected
  from a list kept under `data/` (the user's file, untracked).
- Recent full collections took about 6 s a ticker with a warm SEC cache (127 in 13 minutes) and about 20 s cold
  (2026-08-04). The SEC handler caches companyfacts and submissions for 7 days.
- The SIC code is already fetched (`SECHandler.get_submissions`: `sic`, `sic_description`) and decides
  `sector_class` (`mappings.sectors.classify_sic`), but isn't stored. The JSON exports keep it
  (`data/output/json/AAPL.json`: `sec_submissions.sic` "3571", "Electronic Computers"), so the existing companies'
  codes can be filled offline.
- M2's ranks (`scores.ranks.rank_universe`): each component's percentile within the company's `sector_class` when
  it has at least 10 companies with that component, else the universe's; a rank is ceil(10 x the mean percentile),
  needing 2 components (1 for valuation and momentum); the composite needs 4 ranks.
- The web's quote refresh (`webapp.jobs._run_quote`) fetches Yahoo's quote, refuses one after a split the stored
  valuations don't reflect (BUG-093, BUG-102) and stores a snapshot; it fetches no price bars. A full collection
  fetches the whole daily history (`period="max"`).
- On 2026-10-25 the DB is 183 MB with 1.25M price bars, and the JSON exports 330 MB, for 127 companies: about four
  times that for 500.

## The universe (`universe/`, `src/universe/`)

- **Files in the repo** (`universe/`, tracked; never under `data/`):
  - `core.txt`: the 127 tickers in the DB today, one a line, `#` comments allowed;
  - `sp500-<YYYY-MM-DD>.csv`: the S&P 500's members on that day: `ticker,name,cik,gics_sector,gics_sub_industry,
    date_added`, the source page and the day in `universe/README.md`.
- **The S&P 500 list** (`src/universe/sp500.py`, `python -m src.universe.sp500 [--out PATH]`): Wikipedia's "List
  of S&P 500 companies" page, its first table (Symbol, Security, GICS Sector, GICS Sub-Industry, Date added, CIK),
  parsed with the standard library's `html.parser`; a neutral User-Agent naming the app without an email; one
  request. It refuses a table under 480 or over 520 rows, a missing column or a malformed CIK, so a page change
  fails loudly. Tested on a synthetic page shaped like Wikipedia's (no copy of the page is in the repo).
- **Tickers**: Yahoo's form, a class share's dot as a dash (`BRK.B` as `BRK-B`, `BF.B` as `BF-B`). A second share
  class of a company already in the list (GOOG beside GOOGL, FOX beside FOXA, NWS beside NWSA) is skipped: one
  company per CIK, as the DB already does (GOOGL only).
- **`--tickers-file PATH`** (`stock-data`, repeatable, beside positional tickers): a `.txt` file (a ticker a line,
  `#` comments, blanks ignored) or a `.csv` (its `ticker` or `Symbol` column); the union, upper-cased, normalized,
  deduplicated, in file order. **`--skip-existing`**: leaves out the tickers already in the DB (to add the new
  ones only).

## SIC codes and industries (`src/universe/industries.py`)

- **Stored**: `companies.sic` and `companies.sic_description` (added with `_ensure_columns`), written by every
  collection from the submissions feed, and filled for the existing companies offline by
  `python -m src.universe.sic --db PATH` from the JSON exports (`sec_submissions.sic`), else the SEC handler's
  cached submissions; no network.
- **Industry groups**: a company's group is its 3-digit SIC prefix when at least 10 companies in the DB share it,
  else its 2-digit major group when at least 10 do, else none. The groups and their sizes are computed at rank time
  from the stored codes. A group's label is data-driven: "SIC 283" with its members' most common SEC descriptions
  ("Pharmaceutical Preparations, Biological Products"), so no external SIC table is needed.
- **Industry ranks** (`rank_all`): for each of M2's five ranks, each component's percentile within the company's
  industry group among the members with that component, when at least 10 of them have it (else that component is
  left out of the industry rank); the rank is ceil(10 x the mean percentile) with M2's component minimums, and an
  industry composite of 10 x their mean with M2's 4-rank minimum. Stored in `quality_scores`: `industry_group`,
  `industry_label`, `industry_size`, `ind_rank_strength`, `ind_rank_profitability`, `ind_rank_growth`,
  `ind_rank_valuation`, `ind_rank_momentum`, `ind_composite`. M2's own ranks and composite are unchanged.
- **Web**: the DES tab shows the SIC code and description; the STAT tab's Quality section shows the industry ranks
  beside the universe's ("within SIC 283, 24 companies"), or why there are none; `GET /api/stocks/{t}/scores`
  includes them; the screener gains an industry filter (a 2- or 3-digit SIC prefix, validated as digits and bound
  as a parameter, chosen from a list of the groups in the DB with their sizes) and the `ind_*` fields.

## `stock-data refresh` (`src/refresh.py`)

`stock-data refresh [--steps LIST] [--force] [--db PATH] [--output-dir DIR] [--cache-dir DIR]`, dispatched from
`src/main.py` when the first argument is `refresh` (no reinstall needed; no ticker is called REFRESH).

- **Steps**, each in its own try/except, in this order, each skipped while it succeeded within its period (the
  `refresh_log` table) unless `--force` or named in `--steps`:
  - `quotes` (daily): every company's quote, through the web job's logic, moved into a shared function so both use
    it; a ticker with a split or spin-off since its last collection goes to the collection list instead;
  - `bars` (daily): every company's daily bars after its last stored bar (`history(start=...)`, unadjusted closes
    as the collection stores them), unless it is on the collection list;
  - `sec` (weekly): every company's submissions feed (the handler's 7-day cache); a 10-K, 10-Q, 20-F, 40-F or an
    amendment of one filed after the company's last collection puts it on the collection list;
  - `collect` (as needed): a full collection of the list, as `stock-data` does;
  - `insiders` and `gurus` (weekly): M3's collectors, incremental by accession, then the gurus' positions;
  - `market` (daily): M4's `collect_market`;
  - `recompute` (after any step that stored something): `compute_and_store` for every company, then the ranks.
- **State**: `refresh_log` (step, started_at, finished_at, ok, summary JSON), one row a run and step.
- **A lock**: `<db>.refresh.lock` holding the PID and start time; a second run exits 3 while one is alive; a lock
  whose process is gone is taken over.
- **Output**: a progress line a step and ticker batch, and a summary (each step's result, rows stored, the
  collection list, failures), printed and appended to `<output-dir>/logs/refresh-<YYYY-MM-DD>.log`. Exit 0 when
  every step ran or was skipped, 1 when any failed, 2 for a missing database, 3 for a run already going.
- **Scheduling**: README instructions for Windows Task Scheduler (`schtasks /Create /SC DAILY ...` running
  `stock-data refresh` from the repo folder, after the US close). Installing it is the user's (Q15).

## Scale (about 500 companies)

- **Before the collection**: a synthetic copy, each of the 127 companies cloned three times under made-up tickers
  (about 500), to measure the pages and APIs (the screener, a stock's tabs, /gurus, /market, the exports), read the
  query plans of the heaviest queries, and add the indexes they need. Gate: every page and API under 1 s, the
  screener's default and sorted queries under 0.5 s, on this machine.
- **After the collection**: the same measures on the real data, plus the DB's and the JSON exports' sizes.

## Validation

- **The list**: the saved file's rows within 480-520, every CIK 10 digits, every ticker in Yahoo's form, and each
  CIK equal to the SEC's own ticker map's where both have the ticker (the SEC handler's cached
  `company_tickers.json`), mismatches listed.
- **SIC**: every company's stored code equal to its JSON export's.
- **Industry ranks**: `qa/oracle/scores_oracle.py` gains a gate recomputing them from the stored components and
  codes, exactly, as it does the universe ranks.
- **Refresh**: tests with fakes for every step and rule; a real run on a scratch copy (quotes and bars for 127,
  their submissions, the market data), its counts checked and every other table unchanged.
- **The collection**: on the live DB after it, the existing oracles (valuation, scores with industries,
  indicators, market) pass for about 500 companies, with misses explained.

## Edge cases

| Case | Handling |
|---|---|
| A class share (BRK.B, BF.B) | Yahoo's form, BRK-B and BF-B |
| A second class of a company in the list (GOOG, FOX, NWS) | Skipped: one company per CIK |
| A company in the DB that left the index | Kept: the core list isn't the index |
| An S&P member Yahoo or the SEC can't serve | The collection reports it; the rest go on |
| A split since the last collection | The refresh collects that company in full instead of appending bars |
| An industry with under 10 companies | The 2-digit group; under 10 there too, no industry ranks (said why) |
| Two refreshes at once | The second exits 3 |

## NEEDS-USER

- **Q15, the scheduled task**: installing a Windows scheduled task to run `stock-data refresh` daily. Default:
  not installed; the README gives the command.

## The live step (M5-6)

After code review 19, a dry run on a backup-API copy with ten of the new tickers (the same commands, so Yahoo
and the SEC aren't asked for everything twice), then the backup, then on the user's DB, in the background with
progress logged:
1. `stock-data --tickers-file universe/sp500-<date>.csv --skip-existing` (about 380 companies, 2 to 3 hours);
2. `python -m src.universe.sic` for the existing companies (offline), then `python -m src.valuation.backfill`;
3. `python -m src.ownership.collect --insiders` for the new companies (three years of Form 4s: hours at the SEC's
   polite rate), then `--positions`;
4. the checks: the oracles, the page speeds, the sizes.

## Tasks

M5-1 the universe files, the S&P 500 list (its parser, fetcher and the saved file) and `--tickers-file`; M5-2 SIC
codes, industry groups and ranks, their web and screener parts; M5-3 `stock-data refresh`; M5-4 the scale
pre-flight on a synthetic 500-company copy, with the indexes it needs; M5-5 docs, including the Task Scheduler
instructions; M5-6 milestone close (code review 19, the dry run, the backup and the live collection).
