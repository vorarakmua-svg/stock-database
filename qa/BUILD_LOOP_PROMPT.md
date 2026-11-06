# Stock Database: the build loop for the GuruFocus roadmap

This is the loop prompt for the Ralph Loop plugin. Started from the repo root:

```
/ralph-loop "Read qa/BUILD_LOOP_PROMPT.md and run exactly ONE iteration of it. Output <promise>BUILD LOOP COMPLETE</promise> only when its Definition of Done (section 8) is fully met and verified in this iteration." --completion-promise "BUILD LOOP COMPLETE" --max-iterations 150
```

Stop it any time with `/cancel-ralph`. All progress lives in `qa/BUILD.md`, so a stopped loop resumes
where it left off.

---

## 1. Mission

Build milestones 0-5 of the approved plan `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md`:
- **M0**: the levered-FCF fix.
- **M1**: valuation history and a historical fair value.
- **M2**: quality scores and ranks.
- **M3**: SEC ownership.
- **M4**: more valuation tools and market pages.
- **M5**: a wider universe and automation.

M6 is out of scope. The app is the user's own research tool, run locally.

Work in small, verified units: **one task from `qa/BUILD.md` per iteration**.

## 2. Hard rules (never break these)

1. **The user's data**:
   - Write to `data/output/stock.db*`, `data/output/json/` or `data/output/csv/` only in a
     milestone-close task that the plan authorizes:
     - the offline backfill after M0+M1, M2 and M4;
     - SEC insider and 13F rows in M3;
     - live collection of the added tickers in M5.
   - Each such write follows a dry run of the same command on a backup-API copy, and a backup to
     `%LOCALAPPDATA%\stockdb-backups\<YYYY-MM-DD-x>` (copy the DB with the SQLite backup API from a
     `mode=ro` connection, plus the json/ and csv/ folders).
   - Everything else runs on copies under `SCRATCH` = `%LOCALAPPDATA%\stockdb-qa`.
2. **No hard deletes of user files.** Delete only what you created under `SCRATCH` or `qa/evidence/`.
3. **Git**:
   - Branch `qa/full-loop` only. One commit per task, with a conventional subject
     (`feat(valuation): ...`, `fix(...)`, `test(...)`, `docs: ...`, `chore(qa): ...`).
   - End the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   - Never commit to `main`, merge, force-push or rewrite pushed history.
   - Stage files by name. Never stage `.claude/settings.local.json`, `.claude/ralph-loop.local.md`,
     `CLAUDE.md`, anything under `data/`, `.env`, or secrets. Push after each commit.
4. **Gates before every commit**, identical to CI: `ruff check src/ diagnose.py tests/`, `mypy`, and
   `python -m pytest -p no:cacheprovider -o addopts="" -q`. All must be green or there's no commit.
5. **Test-first** (superpowers:test-driven-development):
   - Write a failing test you watched fail for the right reason, then the minimal code. Save the RED
     output as evidence.
   - Each new rule gets a mutant that a test kills.
   - Bugs found along the way get their root cause first (superpowers:systematic-debugging).
6. **Valuation policy is the user's**:
   - Never change `MEDIAN_MODELS`, the six models' formulas, their caps or floors, or the verdict
     thresholds, beyond what the plan states (M0's fix).
   - New indicators are display-only, like owner earnings.
   - Any change that would move the median or a verdict is NEEDS-USER.
   - Never tune outputs to look plausible: check them against an independent oracle (`qa/oracle/`,
     which never imports `src`).
7. **Scope** is the plan's milestones. No refactors for taste. New runtime dependencies only when a
   spec justifies them, and none in M0-M2.
8. **Network**:
   - SEC at most 10 req/s through the app's handler, with the default User-Agent; never write
     personal emails into commits or logs.
   - Go easy on Yahoo. No network in tests (the autouse guard stays).
   - Allowed new sources: SEC EDGAR filings (Form 4, 13F-HR) and keyless OpenFIGI, FRED's keyless
     `fredgraph.csv`, Shiller's `ie_data.xls` from shillerdata.com, and a public S&P 500 list page.
   - SEC bulk data set ZIPs, API keys, accounts and paid sources are NEEDS-USER.
9. **No persistent system configuration**: no scheduled tasks, services or registry changes.
   Installing the M5 scheduled task is NEEDS-USER. Leave the global environment alone.
10. **stock-web**:
    - Never run `stock-web` with an argument (BUG-046).
    - Start it only in the background, on a scratch copy:
      `STOCK_WEB_DB_PATH=<copy> STOCK_WEB_PORT=8765 STOCK_WEB_ALLOW_COLLECTION=0 STOCK_WEB_ALLOW_QUOTE_REFRESH=0 stock-web`.
    - Stop it afterwards and check the port is free.
    - In Chrome (claude-in-chrome), open your own tabs and close them. Never trigger dialogs.
11. **Files and console**:
    - Repo files are CRLF (`core.autocrlf=true`). Patch them with Write-tool scripts that assert each
      old string occurs once and write `\r\n`.
    - Python must stay 3.9-compatible.
    - Console output must be cp1252-safe: no arrows in prints.
    - In inline Bash, backslashes lose a level, so use `cygpath -m` or forward-slash paths.
12. **Evidence before claims** (superpowers:verification-before-completion). Nothing becomes DONE, and
    the promise is never printed, without the command output that proves it, run in this iteration.

## 3. State

- **`qa/BUILD.md`**:
  - The task board: one row per task, with status, commit and evidence. The `**Counts:**` header is
    kept in sync.
  - A short log entry per iteration: `### Build iteration N: date: task`, then Did / Evidence / Gates
    / Commit / Next.
- **`qa/BUGS.md`**: bugs found along the way, and NEEDS-USER questions, in its template. The census
  header line is kept in sync. New entries go before `\n### Q2 [VAL] NEEDS-USER:`.
- **Specs**: `docs/superpowers/specs/2026-10-21-<milestone>-design.md`, one per milestone.
- **Evidence**: `qa/evidence/<task>-*.txt`, gitignored.
- **The QA ledgers**: `qa/LOOP_PROMPT.md`, `qa/MATRIX.md` and `qa/LOG.md` belong to the QA loop. Touch
  them only to record a bug's status or a matrix row the build changed.

## 4. Iteration protocol

1. **Orient** (briefly):
   - Read `qa/BUILD.md`: the board, the counts, and the last 2 log entries.
   - Run `git status`, `git log --oneline -3`, and `gh run list --branch qa/full-loop --limit 1` if
     `gh` works.
2. **Pick ONE unit**, in this order:
   1. A red gate, locally or on CI.
   2. A DOING task left from an interrupted iteration.
   3. The first TODO task in board order. A milestone's design task (Mn-0) comes before its other
      tasks.
3. **Execute**:
   - **Design task (Mn-0)**:
     - Explore the code read-only, then write the milestone's spec from the plan's outline: exact
       formulas, data sources, schema, UI, tests, validation gates and edge cases.
     - Replace the Mn-0 row with the task rows. Each must fit one iteration and end with a
       milestone-close task.
     - User decisions go in as NEEDS-USER, with the default that leaves today's data and verdicts
       untouched.
   - **Build task**: TDD per rule 5. Implement through workers (rule 4 below), review every diff
     yourself, and run the real-data checks the spec asks for on a DB copy.
   - **Milestone close**:
     - An opus code review (superpowers:requesting-code-review) of the milestone's commits. Fix its
       Critical and Important findings, each test-first; record the rest as bugs.
     - Then the authorized user-data step (rule 1): dry run, backup, run, and report what changed.
4. **Delegate** (the CLAUDE.md orchestrator/worker split): the main session picks, reviews and decides.
   - Workers execute: `model: "opus"` for multi-file features, tricky logic and reviews;
     `model: "sonnet"` for routine edits, docs, searches and test runs.
   - Never let two workers edit at once.
   - Re-run a worker's key command yourself before recording its claim.
   - Background subagents can't be awaited across the stop hook. Dispatch foreground work, or only as
     much background work as you can fill the time with.
5. **Record**: update the task row, the counts and a log entry in `qa/BUILD.md`, plus `qa/BUGS.md` if
   needed.
6. **Gates, commit, push** (rules 3 and 4).
7. **End the iteration**: print 3 lines (what was done, gate status, what's next) and stop. Don't start
   a second unit.

## 5. Templates

**Log entry** (in `qa/BUILD.md`):
```
### Build iteration N: YYYY-MM-DD HH:MM: <task ID>
Did: <2-4 lines>
Evidence: <commands + key output; qa/evidence/... paths>
Gates: ruff ✓ mypy ✓ pytest <N> passed | CI: <state>
Commit: <sha or "this commit"> <subject>
Next: <task ID>
```

**BUG entry** (in `qa/BUGS.md`): follow the template in `qa/LOOP_PROMPT.md` section 8. Severity
follows the same scale.

## 6. Milestone notes

**M0/M1**
- The spec is `docs/superpowers/specs/2026-10-21-valuation-history-design.md`.
- M0-1's live effect is applied by M1-9's backfill.
- M1-9's review is "code review 15". It covers `16f3005..HEAD`, so the QA loop's due review is done
  too; record it in `qa/LOG.md` as the QA loop's review 15.

**M2**
- Scores: Piotroski F (9 tests); Altman Z (original, Z'' for non-manufacturers, N/A for banks and
  insurers); Beneish M (N/A for financials); Sloan; ROIC vs WACC.
- Predictability stars from a published formula.
- About 20 warning-sign rules with severities.
- Five ranks as percentiles within the universe, or the sector where it has at least 10 peers.
- A 0-100 composite with published weights. Equal weights by default, as NEEDS-USER.
- Per-share series and CAGRs.
- Screener columns and preset screens.
- An independent oracle.

**M3**
- A Form 4 XML parser through the SEC handler (cached, rate-limited), for our issuers.
- Aggregates: open-market P/S, CEO/CFO buys, cluster buys.
- 13F for a curated list of about 30 value investors, fetched per filing.
- A CUSIP map curated for our universe; OpenFIGI without a key for the rest.
- Quarter-over-quarter trades and an aggregated guru portfolio.
- No bulk ZIPs without the user (NEEDS-USER).

**M4**
- Display-only indicators: reverse DCF, EPV, NCAV/net-net, median P/S value, Graham on tangible book.
- A market page: the Buffett indicator (FRED NCBEILQ027S + GDP via `fredgraph.csv`), Shiller CAPE
  (`ie_data.xls`) and the yield curve (Treasury.gov).

**M5**
- A universe file and `--tickers-file`.
- S&P 500 members from a public list, saved with its date.
- The stored SIC code, industry groups and industry percentiles.
- A `stock-data refresh` command, plus Task Scheduler instructions. The install is NEEDS-USER.
- The live collection runs in the background with progress logs.
- Checks on DB size, indexes and page speed.

## 7. Stuck and anti-loop rules

- **A stuck task**: one with no progress for 3 iterations becomes BLOCKED, with evidence. Move on to
  the next task that doesn't depend on it.
- **A failing approach**: after 3 failed attempts, stop and question the design
  (systematic-debugging Phase 4.5). Record it in the spec and as NEEDS-USER if it is a design
  decision.
- **Context**: keep it lean. Read the board and the specs, not history, and put long output in
  `qa/evidence/`.

## 8. Definition of Done

Print `<promise>BUILD LOOP COMPLETE</promise>` **only when all of these are verified in the current
iteration**:
1. Every M0-M5 task row on `qa/BUILD.md` is DONE, or NEEDS-USER with its default applied and the
   question recorded in `qa/BUGS.md`. Nothing is TODO, DOING or BLOCKED without evidence.
2. Each milestone's close task is DONE: its code review has no open Critical or Important finding,
   and its authorized user-data step ran and was checked.
3. The gates are green locally, and on CI for the pushed head when a PR is open. With no PR, ruff's py39
   target stands in for CI's Python 3.9 job (Q10, the user's call).
4. README and USAGE_GUIDE describe every new feature, and the docs tests pass.
5. The working tree is clean apart from the user's own files, and everything is pushed.
6. A final summary is appended to the `qa/BUILD.md` log: what was built, the commits, the NEEDS-USER
   questions and the remaining risks.

Never print the promise to escape the loop.

## 9. Command reference

```bash
# gates (from repo root), same as CI
ruff check src/ diagnose.py tests/ && mypy && python -m pytest -p no:cacheprovider -o addopts="" -q

# consistent read-only copy of the real DB (backup API)
python -c "import sqlite3,sys; s=sqlite3.connect('file:data/output/stock.db?mode=ro',uri=True); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close()" "$SCRATCH/realcopy/stock.db"

# valuations and history (no network)
python -m src.valuation.backfill --db "$SCRATCH/realcopy/stock.db" [TICKERS...]

# web app on a scratch DB (background only; no arguments)
STOCK_WEB_DB_PATH="$SCRATCH/realcopy/stock.db" STOCK_WEB_PORT=8765 STOCK_WEB_ALLOW_COLLECTION=0 STOCK_WEB_ALLOW_QUOTE_REFRESH=0 stock-web
```
