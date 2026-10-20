# Stock Database: Full-Spectrum QA and Fix Loop

Loop prompt for the Ralph Loop plugin. Start it from the repo root in Claude Code:

```
/ralph-loop "Read qa/LOOP_PROMPT.md and run exactly ONE iteration of it. Output <promise>QA LOOP COMPLETE</promise> only when its Definition of Done (section 9) is fully met and verified in this iteration." --completion-promise "QA LOOP COMPLETE" --max-iterations 100
```

Stop it any time with `/cancel-ralph`. All progress lives in `qa/`, so a stopped loop resumes where it left off.

---

## 1. Mission

Test every aspect of this program: code health, data gathering, data validation, data matching, financial
valuation, storage and point-in-time queries, web UI/UX, API, CLI, security, performance, reliability, docs and
packaging. Find every defect, fix each one test-first, and improve the program wherever evidence shows a real
problem. Work in small, verified units: **one unit per iteration**. The loop ends only when the Definition of
Done (section 9) holds.

## 2. Hard rules (never break these)

1. **The real data is read-only.** Never write to `data/output/stock.db*`, `data/output/json/`, `data/output/csv/`.
   Every collection, backfill, web job or migration test runs against copies under `SCRATCH` (section 3).
   Make DB copies with the SQLite backup API from a `mode=ro` connection (consistent with the WAL), never by
   copying `stock.db` alone.
2. **No hard deletes of user files.** You may delete only what you created under `SCRATCH` or `qa/evidence/`.
   Anything else that should go: record it as NEEDS-USER in `qa/BUGS.md`.
3. **Git discipline.** Work only on branch `qa/full-loop`. One commit per fix, conventional-commit subjects
   (`fix(valuation): …`, `test(parsers): …`, `docs: …`, `chore(qa): …`), ending with a
   `Co-Authored-By: Claude <noreply@anthropic.com>` trailer. Never commit to `main`, never merge, never force-push,
   never rewrite pushed history. Stage files by name. Never stage `.claude/settings.local.json`, `CLAUDE.md`,
   anything under `data/`, `.env`, or secrets.
4. **Gates before every commit** (identical to CI): `ruff check src/ diagnose.py tests/`, `mypy`, `pytest`. All green
   or no commit. If you can't get them green this iteration, undo only your own uncommitted changes and log why.
5. **Every fix is test-first.** Root cause first (superpowers:systematic-debugging), then a failing test you watched
   fail for the right reason, then the minimal fix (superpowers:test-driven-development). Prove any test written
   after its fix by deliberately breaking the code, watching it fail, and restoring the code byte-identically.
6. **Never tune financial outputs to look plausible.** Fix formulas or plumbing only when an independent oracle
   (section 7) proves them wrong. Changes to valuation *policy* (discount-rate floors, growth caps, verdict
   thresholds, the model mix) are design decisions: record them as NEEDS-USER and don't change them.
7. **No scope creep.** No refactors for taste and no new runtime dependencies. Dev-only tools need a written
   justification in the commit message. Change UX only to fix a recorded bug; redesigns are NEEDS-USER proposals.
8. **Network etiquette.** SEC EDGAR allows at most 10 req/s, and the program's rate limiter must stay on. Use the
   default User-Agent unless `SEC_USER_AGENT` is set, and never write personal emails into commits or logs. Go easy
   on Yahoo. Live collection is limited to the golden basket (section 7); reuse the scratch golden DB instead of
   re-collecting. `coverage_report.py` calls SEC live (about 41 companies by default), so pass explicit tickers.
9. **Browser safety** (Claude in Chrome). Open your own tabs and close them before the iteration ends. Drive only
   servers you started on a scratch DB (port 8765). Never click UPDATE DATA, REFRESH or Fetch on a server pointed
   at the real DB. Never trigger alert/confirm dialogs.
10. **Leave the global environment alone.** Packaging and install tests use a throwaway venv under `SCRATCH`.
11. **Evidence before claims** (superpowers:verification-before-completion). Nothing becomes PASS, FIXED or VERIFIED,
    and the promise is never printed, without the command output that proves it, in this iteration.

## 3. Workspace and state

- `SCRATCH` = `%LOCALAPPDATA%\stockdb-qa` (Git Bash: `"$LOCALAPPDATA/stockdb-qa"`), outside the repo. (Moved out of `Temp` in iteration 11: around 02:00 on 2026-10-20 every file under `Temp\stockdb-qa` was deleted, most likely by Windows' scheduled maintenance.)
  Subfolders: `golden/` (live-collected golden basket DB + JSON), `realcopy/` (backup-API copy of the real DB),
  `venv/`, and per-test throwaway folders.
- Loop state lives in `qa/` and is committed on the branch:
  - `qa/MATRIX.md`: the full test matrix from section 6, one row per ID: status
    (`TODO | PASS | FAIL→BUG-n | N-A (reason) | BLOCKED (why) | NEEDS-USER`), last-run commit, evidence pointer.
    Header lines: counts per status and `Consecutive clean sweeps: n`.
  - `qa/BUGS.md`: the bug ledger (template in section 8).
  - `qa/LOG.md`: one short entry per iteration (template in section 8).
  - `qa/GOLDEN.md`: golden basket, oracle reference values with sources and retrieval dates, tolerances.
  - `qa/oracle/`: independent oracle scripts (section 7). They must never import `src.valuation` or `src.parsers`.
  - `qa/REPORT.md`: the final report.
  - `qa/evidence/`: screenshots, logs, outputs. Gitignored; add `qa/evidence/` to `.gitignore` in the bootstrap.

## 4. Iteration protocol (exactly this, every time)

1. **Orient** (briefly). Read the last 3 entries of `qa/LOG.md`, open items in `qa/BUGS.md`, and the header of
   `qa/MATRIX.md`. Then run `git status`, `git branch --show-current`, `git log --oneline -5`, and
   `gh pr checks` if a PR exists. If the `qa/` state files are missing, this is the bootstrap: do section 5 and stop.
2. **Pick ONE unit**, in this priority order:
   1. A red gate locally or on CI: fix it.
   2. The highest-severity OPEN P0–P2 bug (P0 > P1 > P2), oldest first. A fix unit is one bug.
   3. A discovery unit: up to 5 related TODO matrix items, riskiest area first: VAL > VALID > MATCH > GATHER >
      STORE > SEC > API > UI > CLI > CODE > OPS > DOCS > PKG > UX.
   4. Open P3 bugs, up to 3 closely related ones per unit. They wait until discovery is done so cosmetic fixes never
      delay finding serious bugs; they must still be fixed or WONTFIX before section 9. (Changed in iteration 4.)
   5. If nothing is TODO or OPEN: a convergence sweep (section 9).
3. **Execute.**
   - **Discovery:** test each item with its method and oracle. Record PASS or FAIL with evidence. Every FAIL gets a
     BUG entry with a minimal repro. Don't fix during discovery; the next iteration will.
   - **Fix:** follow rule 5. Verify in the real app (browser or CLI) when the bug is user-visible. Re-run the
     matrix items that found it. Mark the bug FIXED, then VERIFIED once its evidence is recorded.
4. **Delegate** (CLAUDE.md orchestrator/worker split). The main session picks, reviews and decides. Dispatch
   read-only discovery sweeps in parallel (superpowers:dispatching-parallel-agents): `model: "sonnet"` for routine
   checks, `model: "opus"` for valuation math audits, tricky debugging and code review. Apply fixes one at a time,
   never two workers editing at once. Re-run a worker's key command yourself before recording its claim.
   **Background subagents can't be awaited inside an iteration:** ending your turn fires the Ralph stop hook and
   starts the next iteration while the worker is still running. Dispatch only when you have enough parallel work of
   your own to fill the time until they report back; otherwise work inline. (Learned in iteration 2.)
5. **Gates, commit, push.** Run the three gates, commit the fix together with the `qa/` updates (or `chore(qa):`
   alone for discovery), then `git push`. On the first push, open a **draft** PR against `main` titled
   "QA loop: full-spectrum testing fixes". If PR #27 (`fix/palette-commands`) is still open, say this branch builds
   on it.
6. **Review cadence.** After every 5 fixes, have an opus subagent review the commits since the last review
   (superpowers:requesting-code-review). Valid findings become bugs.
7. **End the iteration.** Print 3 lines (what was done, gate status, what's next) and stop. Don't start a second unit.

## 5. First iteration (bootstrap)

1. Base: if `fix/palette-commands` isn't merged into `origin/main`, run `git switch -c qa/full-loop fix/palette-commands`;
   otherwise `git fetch && git switch -c qa/full-loop origin/main`.
2. Create `qa/MATRIX.md` (every section 6 item as an ID'd row, status TODO), `qa/BUGS.md` (seeded with section 8's
   known bugs), `qa/LOG.md`, `qa/GOLDEN.md` (section 7) and the `.gitignore` entry for `qa/evidence/`.
3. Baseline: run the three gates and record the test count and duration (`pytest --durations=15`), the Python and
   key package versions (`pip list`), and line coverage per module if `coverage` is installed
   (`python -m coverage run -m pytest && python -m coverage report`). If it isn't, mark CODE-03 as needing
   a dev-only tool and justify adding it.
4. Build `SCRATCH`. Make `realcopy/stock.db` with the backup API, then run `PRAGMA integrity_check` on the copy.
   Live-collect the golden basket into `golden/`, a few tickers per run:
   `stock-data <TICKERS> --output-dir "$SCRATCH/golden" --workers 2`.
5. Commit `chore(qa): bootstrap QA loop state`, push, open the draft PR. Stop.

## 6. Test matrix: every area must be tested

Each line is a matrix row: **ID: what to test, then how (method/oracle) and the pass criterion.**

### VAL: Financial valuation correctness (highest priority)
Oracle: `qa/oracle/valuation_oracle.py`, an independent re-implementation from
`docs/superpowers/specs/2026-07-13-valuation-layer-design.md` and `2026-07-14-owner-earnings-design.md` that
reads raw inputs from the DB and never imports `src.valuation`. Adjudicate every disagreement by hand calculation.
- VAL-01 DCF (two-stage FCF): basis = median of 3-year FCF; growth = min(historical CAGR, analyst estimate) with its caps; CAPM rate = rf + β×ERP with floor/cap; terminal growth < discount rate; discount-period convention; net debt; diluted shares; bear/base/bull spread. Matches the oracle within 0.5%.
- VAL-02 DDM (multi-stage): trailing-12-month dividend, dividend CAGR, stage lengths, the g ≥ r guard, special dividends, and "not applicable" with a reason for non-payers.
- VAL-03 Graham Number: √(22.5 × EPS × BVPS); BVPS units (equity ÷ shares, same share basis); "not applicable" when EPS ≤ 0 or BVPS ≤ 0.
- VAL-04 Peter Lynch: the spec's fair P/E rule, growth bounds, ≥4 years of EPS history; "not applicable" for banks, insurers and REITs.
- VAL-05 Historical multiples band: the company's own 5-year range of P/E (P/B for banks and insurers, P/FFO for REITs); each year's price taken at its fiscal-year end with no look-ahead; at least 3 fiscal years.
- VAL-06 Owner earnings: NI + D&A − min(D&A, |capex|); median of 3 years; discount rate = max(10-year Treasury, 7%); buy_below = 0.70 × intrinsic value; predictability gate (positive in ≥8 of the last 10 years, ≥10 years of history); volatility evidence fields; excluded from the 5-model median; own verdict.
- VAL-07 Summary: n_applicable; median bear/base/bull over applicable models only (even counts handled); upside% = (median_base − price) / price using the latest price; verdict thresholds; the screener ranks with the same numbers.
- VAL-08 Invariant scan over the whole real-DB copy: bear ≤ base ≤ bull; no NaN or ±inf; applicability matches the sector rules; na_reason present exactly when not applicable; values recomputed from the stored `assumptions` JSON match the stored values within 0.5%.
- VAL-09 Edge cases: negative FCF, zero or negative equity (buyback-depleted, e.g. MCD), hyper-growth (NVDA), cyclical collapses (energy), banks, REITs, short histories (recent IPO), missing beta, missing analyst growth, missing shares, a stock split inside the basis window (per-share consistency), share-unit scale.
- VAL-10 Rates: risk-free units (percent vs decimal), fallback sources flagged in assumptions, floors and caps applied, ERP documented.
- VAL-11 Reproducibility: `python -m src.valuation.backfill --db <copy>` run twice gives identical rows (ignoring timestamps); backfill and post-collection valuations agree.
- VAL-12 Presentation: the VAL tab equals the `/api/stocks/{T}/valuation` numbers; the centre of the DCF sensitivity grid equals the base case; verdict pills and screener Upside % / Buffett upside % match the API.
- VAL-13 Hand-worked examples (a general company, a bank, a dividend payer): compute every model step by step from DB inputs, write the worked numbers into `qa/GOLDEN.md`, and they must match.

### VALID: Data validation (parsing, standardization, quality)
- VALID-01 Canonical mapping: run `python coverage_report.py <basket tickers>`. Core fields (revenue, net_income, total_assets, total_liabilities, total_equity, operating_cash_flow) resolve 100%; unmapped material facts land in `unmapped_facts`.
- VALID-02 Accounting identities: assets = liabilities + equity (+NCI); gross profit = revenue − COGS; cash-flow sections + FX (+ discontinued ops) = net change; EBITDA = operating income + D&A; FCF = OCF − |capex|. All within tolerance.
- VALID-03 Sign conventions: capex, buybacks, dividends paid, D&A and debt repayment are signed consistently across parsers, metrics and valuation inputs.
- VALID-04 Units and scale: shares filed in thousands or millions (`parsers/share_scale.py`), per-share vs totals, percent vs fraction in ratios, non-USD reporters.
- VALID-05 Periods: `fiscal_year` (52/53-week years ending in early January belong to the prior FY; January-end retailers keep their year); `calendar_year` from SEC frames; fiscal and calendar quarters; duplicate or missing periods; 10-K/A restatements.
- VALID-06 Quarterly ladder: discrete quarters from YTD differencing; Q4 = annual − 9-month YTD; flow quarters sum to the annual figure within 1%; balance-sheet checkpoints; TTM = rolling 4 quarters.
- VALID-07 Integrity checks (`validation/integrity.py`, `quality.py`): spike-revert outliers (≥100×), cash-flow consistency, quarterly sums, ratio bounds. True positives fire, lumpy flows don't, and score penalties match the README table.
- VALID-08 Sector classification by SIC: bank, insurer, REIT, utility, energy, general, including boundary SIC codes; sector-specific fields present; inapplicable ratios stored as NULL.
- VALID-09 Derived fields (`parsers/derived_fields.py`) only fill gaps, never overwrite reported values, and are tagged in `_source_tags`.
- VALID-10 Point-in-time: one vintage row per filing; as-of resolution never returns a filing dated after the as-of date; restatements handled; PIT metrics match a manual computation.
- VALID-11 The `/quality` dashboard numbers equal direct SQL on the same DB.
- VALID-12 Sanity scan over the real-DB copy (read-only): negative revenue, margins over 100%, shares ≤ 0, price ≤ 0, future dates, absurd P/E. Triage each hit as a data bug or a real-world fact.

### MATCH: Data matching and reconciliation
- MATCH-01 Identity: ticker ↔ CIK ↔ company name agree across Yahoo, SEC, the `companies` table and JSON. Shared CIKs (GOOG/GOOGL, BRK-A/BRK-B) are intentional; nothing else collides.
- MATCH-02 Cross-source fundamentals: latest-FY revenue, net income and shares agree between SEC and Yahoo within the GOLDEN tolerances (definitional differences documented); EPS ≈ NI ÷ diluted shares.
- MATCH-03 Market math: market cap ≈ price × shares (±5%); EV = market cap + debt − cash; P/E ≈ price ÷ TTM EPS; dividend yield ≈ annual dividends ÷ price; price within the 52-week range at the data date.
- MATCH-04 Price history: split-adjusted continuity (no fake jumps at split dates); dividends on their ex-dates; ^GSPC overlay aligned by date; the HP tab and `/api/export/stock/{T}/bars.csv` equal the DB.
- MATCH-05 Store consistency: JSON, SQLite and `summary.csv` agree for the same ticker and field, including after merge re-runs.
- MATCH-06 UI vs DB: 20 fields × each golden ticker on DES, STAT, FA, VAL and the screener equal the DB after formatting. Check B/M/K units, percentages multiplied by 100 exactly once, signs, and rounding.
- MATCH-07 Peers and sector medians are computed within the right sector and the medians are correct.
- MATCH-08 Dedup: insider transactions, holders, dividends, splits, earnings and price bars have no duplicates after 2–3 repeated collections.

### GATHER: Data gathering (`src/fetchers/*`, `main.py`, `webapp/jobs.py`)
- GATHER-01 Yahoo: every section populated for the golden basket (quote, ratios, analyst data, dividends, splits, institutional and fund holders, insiders, earnings history, officers, description, 10+ years of prices, the ^GSPC benchmark). Missing sections produce warnings, not crashes.
- GATHER-02 SEC CIK resolution: dual-class and punctuated tickers (BRK-B / BRK.B, GOOG / GOOGL), renamed tickers, delisted tickers, foreign filers (20-F / 40-F), ADRs, ETFs and indices. Each gives correct data or a graceful, explained gap.
- GATHER-03 companyfacts parsing: multiple units per concept (USD, shares, USD/share, pure), duplicate facts across amendments, frames, absent concepts.
- GATHER-04 Rates: with and without `FRED_API_KEY`; Treasury.gov fallback, including the January prior-year fallback; the static estimate flagged as stale; values within 0–15% and in the right units.
- GATHER-05 Rate limiting and retries: SEC ≤ 10 req/s under `--workers 4` (measure it); backoff on 429/5xx; timeouts; bounded retries; User-Agent header present.
- GATHER-06 Failure modes (mocked): DNS failure, offline, 403 (bad User-Agent), 404, 429, 500, malformed or truncated JSON, empty yfinance frames. Errors are reported per ticker, the run continues, and the exit code and summary are honest.
- GATHER-07 SEC cache: freshness, invalidation, a corrupted cache file, concurrent access.
- GATHER-08 Idempotency: re-collecting a ticker creates no duplicates; JSON merge keeps history; `collection_runs` and `market_snapshots` are appended correctly.
- GATHER-09 Ticker input: lowercase, whitespace, duplicates, `;drop`, `../`, very long strings, unknown (ZZZZ), ETF (SPY), index (^GSPC), crypto (BTC-USD). All graceful.
- GATHER-10 `--workers 1` and `--workers 4` produce identical DB content apart from timestamps.
- GATHER-11 Flag combinations: `--years N`, `--no-yahoo`, `--no-sec`, each `--formats` subset, `--db`, and an `--output-dir` containing spaces or non-ASCII (e.g. Thai) characters. Outputs are exactly what was asked for.
- GATHER-12 Web jobs on a scratch server: queueing (one at a time), state transitions, failure reporting, valuations recomputed, `STOCK_WEB_ALLOW_COLLECTION=0` returns 409 and hides the buttons, overlapping requests, server stopped mid-job.
- GATHER-13 Quote refresh (`POST /api/stocks/{T}/refresh-quote`) writes only the snapshot; `STOCK_WEB_ALLOW_QUOTE_REFRESH=0` disables it.

### STORE: Persistence and queries
- STORE-01 Schema migrations applied to the real-DB copy are idempotent and non-destructive (row counts unchanged).
- STORE-02 A collection killed mid-run on scratch leaves a DB that opens, with `PRAGMA integrity_check` = ok and no half-written ticker.
- STORE-03 WAL handling: read-only opens with a large WAL, with a missing `-shm`, and during a concurrent writer. Users never see "database is locked".
- STORE-04 The web Reader (`mode=ro`) never writes; per-request connections survive threadpool hopping.
- STORE-05 The as-of API and UI equal `AsOfReader`; edge dates (before the first filing, today, the future, malformed) give clean 4xx responses.
- STORE-06 CSV exports: content type, header row, UTF-8 encoding, commas and quotes inside company names, large exports.
- STORE-07 The real-DB copy passes `PRAGMA integrity_check` and `foreign_key_check`.

### API: HTTP API (`/docs`, every `/api/*` route)
- API-01 The OpenAPI schema is valid, every route responds, and responses match their declared models.
- API-02 Status codes on every error path (400/404/409/422) with consistent JSON error bodies; no 500 for bad input.
- API-03 Limit, offset, sort and filter params at their bounds: 0, negative, huge, unknown columns, injected strings.
- API-04 Every CSV export equals its JSON endpoint.
- API-05 `/api/health` reflects the DB state, including a missing DB.
- API-06 Collection API validation: empty or huge ticker lists, invalid tickers, unknown job ID (404).

### SEC: Security
- SEC-01 SQL injection through screener filters and sort, search `q`, and ticker path params: parameterized everywhere.
- SEC-02 XSS: a scratch DB whose company name, description and officer names contain `<script>`, `</script>`, quotes and `{{ }}` must render escaped everywhere, including JSON embedded in `<script type="application/json">`.
- SEC-03 Path traversal via `/static/..` and export paths; the ticker regex enforced on every route.
- SEC-04 Cross-site requests: can a malicious web page POST collection jobs or quote refreshes to `127.0.0.1:8000` (CSRF / DNS rebinding)? Assess and propose Origin/Host checks (NEEDS-USER if it changes behavior).
- SEC-05 Binding: the default is 127.0.0.1, and a warning appears when binding to 0.0.0.0.
- SEC-06 Secrets: no API keys in logs, JSON outputs or commits; `.env` stays ignored.
- SEC-07 Known-vulnerable dependencies: run `pip-audit` in the scratch venv if available, else record as N-A.

### UI: Web UI/UX (Claude in Chrome on a scratch-DB server, port 8765)
Pages: `/`, `/companies`, `/stocks/{T}` with all 10 tabs (DES GP FA ERN STAT HP DVD HDS INS VAL), `/screener`,
`/asof`, `/quality`, `/collect`, `/docs`, the error page and the unknown-ticker fetch page.
- UI-01 Every page and tab for AAPL, JPM, a REIT, an insurer, a sparse ticker, a non-payer and a ticker without analyst data: no console errors and no failed requests.
- UI-02 Command palette: every `TICKER CODE`, every global code, aliases (CHART, STATS, …), recents, the fetch row, arrow keys, Escape, click, `/` `` ` `` and Ctrl+K, fast typing, non-ASCII input, very long input. The existing Node driver (`tests/js/palette_driver.js`) is the pattern for automated palette tests.
- UI-03 The HELP overlay lists exactly the codes the palette and the TABS registry support.
- UI-04 Screener: every filter chip, select, column sort (both directions) and page; Save screen / Copy link; Compare; the CSV download equals the table; the empty-results message.
- UI-05 Charts: every GP range (1M…MAX), Line/Candle, MA50/MA200/RSI/MACD, the S&P overlay and the compare list, metric charts and the VAL chart, all with sparse and gappy data (no misdrawn gaps or NaN).
- UI-06 Watchlist: add, remove, persistence, and the star on the stock page.
- UI-07 UPDATE DATA and fetch-page flows on scratch: job card states (queued, running, done, failed), auto-reload, error copy; buttons hidden when collection is disabled.
- UI-08 Empty, error and loading states: unknown ticker, invalid ticker, missing DB (`STOCK_WEB_DB_PATH` set to a missing file), API failure, slow responses. Friendly messages, never a raw 500.
- UI-09 Responsive layout at widths 375, 768, 1280 and 1920 (`resize_window`): no page-level horizontal scroll, tables scroll inside their containers, the palette is usable on a phone.
- UI-10 Accessibility: keyboard-only navigation, visible focus, tab roles and ARIA, labelled controls, WCAG AA contrast in the dark theme, page titles and `lang`.
- UI-11 Formatting: currency, %, B/M/K, negatives, missing values shown as "—", dates and timezones, consistent rounding across pages.
- UI-12 Performance: golden pages load in under 1 s locally; payload sizes; N+1 queries; static assets revalidate (the `Cache-Control: no-cache` fix).
- UI-13 CDN dependencies (Plotly, htmx, Google Fonts): behavior when they're blocked; Subresource Integrity attributes.
- UI-14 Copy and methodology: verdict wording, tooltips and disclaimers present estimates as estimates, never as facts.
- UI-15 Heuristic review (Nielsen's 10): data freshness visible (data that's N days old should be obvious), system status, error prevention and recovery. Write findings as P2 bugs only when they're clear defects; everything else becomes a NEEDS-USER proposal.

### CLI: Command line and scripts
- CLI-01 `stock-data --help` matches the README; every flag works; exit codes for success, partial failure and total failure; the summary counts are right.
- CLI-02 `python -m src.valuation.backfill [--db PATH] [TICKERS…]`, including an unknown ticker and an empty DB.
- CLI-03 `python diagnose.py TICKER -v` (reads `data/output/json/`, so run it read-only or from a scratch cwd) and `python coverage_report.py TICKERS`: correct output and exit codes.
- CLI-04 Ctrl+C mid-collection: a clean stop with no corrupt DB.

### CODE: Engineering health
- CODE-01 Gates match CI (ruff, mypy, pytest); no Python 3.10+ syntax anywhere (CI's minimum is 3.9).
- CODE-02 Test-suite health: run it twice for flakiness; list the slowest tests; confirm no test needs the network (run once with `HTTP(S)_PROXY=http://127.0.0.1:9`).
- CODE-03 Coverage gaps: untested branches in `valuation/`, `parsers/`, `webapp/routes/` and fetcher error paths. Add tests for the risky ones.
- CODE-04 Error handling: broad `except` blocks that swallow errors and cause silent data loss; warnings surfaced in the `warnings` field.
- CODE-05 Type bugs in modules mypy doesn't check: run `mypy --follow-imports=silent src/fetchers src/parsers/xbrl_parser.py src/main.py` and triage real None/type bugs.
- CODE-06 Numerical robustness: division by zero, NaN and inf propagation, None arithmetic, float equality, huge values.
- CODE-07 Concurrency: shared state between `--workers` threads (rate limiter, SEC cache, SQLite writes) and the job manager.
- CODE-08 Resource leaks: run `pytest -W error::ResourceWarning`; unclosed connections, files and sessions.
- CODE-09 Config and env: every env var with valid and invalid values (e.g. `STOCK_WEB_PORT=abc`); `.env` loading.
- CODE-10 Cross-platform: Windows paths, CRLF, non-ASCII paths with spaces, case-insensitive filesystems.
- CODE-11 Deprecations: pandas FutureWarnings, yfinance API drift, FastAPI/Starlette deprecations, Python 3.13 locally vs 3.9 on CI.
- CODE-12 Logging: accurate messages, sensible levels, no secrets or PII, `-v` works.

### PKG: Packaging and install
- PKG-01 In a scratch venv, a non-editable `pip install .` then `stock-web` from another directory serves templates and static files. Checks that package data is included.
- PKG-02 Entry points `stock-data` and `stock-web` resolve outside the repo; `find_packages` includes every subpackage.
- PKG-03 `pip install -e ".[dev,web]"` in a clean venv; the dev extras are enough to run the gates.

### OPS: Reliability and operations
- OPS-01 After 1000 mixed requests: no memory growth, and connections and threads are released.
- OPS-02 Startup with a missing, locked or corrupt DB gives a clear error message.
- OPS-03 Time handling: timezones, DST, ISO storage, "as of" display, a fiscal year ending on Jan 1, leap years.
- OPS-04 Scale: a synthetic 500-ticker scratch DB; the screener, companies list and exports stay responsive.

### DOCS: Documentation accuracy
- DOCS-01 README: every command, flag, endpoint, table and env var exists and behaves as described.
- DOCS-02 USAGE_GUIDE: every Python example runs against the current JSON, CSV and DB schema (see BUG-001).
- DOCS-03 Quantitative claims (e.g. "100% core coverage for a 41-company basket") are reproducible.
- DOCS-04 In-app help, tooltips and the HELP overlay match actual behavior.

### UX: Evidence-backed improvements
- UX-01 Gather the improvement ideas found during testing into `qa/REPORT.md` as proposals, each with evidence, effort and risk. Implement only the ones that are clear defects (as bugs); design changes stay NEEDS-USER.

## 7. Golden basket and oracles

**Golden basket** (live-collected once into `SCRATCH/golden`; re-collect a ticker only when testing gathering changes):
AAPL (September FY, buyback-depleted equity), MSFT (June FY), NVDA (January FY, hyper-growth), WMT (January-end
retailer), JNJ (52/53-week year), JPM (bank), PGR (insurer), PLD (REIT), XOM (energy, cyclical), NEE (utility),
KO (steady dividends), GOOGL (missing cash-flow D&A tags), BRK-B (class share, conglomerate), MCD (negative
equity), TSLA (no dividend), ARM (recent IPO), TSM (foreign filer, 20-F), SPY (ETF), ZZZZ (invalid). If a pick
misbehaves for reasons outside this program, swap in an equivalent and say why in `qa/GOLDEN.md`.

**Oracles** (independent of the code under test):
- `qa/oracle/valuation_oracle.py`: re-implements every model from the specs, reading raw inputs from the DB. Output: per ticker and model, oracle vs stored value and the diff.
- `qa/oracle/sec_oracle.py`: parses SEC companyfacts JSON on its own (requests + json, not `src.parsers`) for revenue, net income, assets, liabilities, equity, OCF, capex and diluted shares, then compares with the DB.
- Hand checks: for 3 companies, check the oracle itself against the filed 10-K numbers (record source URLs and dates).
- Oracle rule changes go in their own commit, citing the spec, before the code fix they verify, and that commit shows the oracle flagging the unfixed code. An oracle changed in the same commit as the code proves nothing. (Added in iteration 10 after code review 1.)
- When an oracle rule encodes the fix's own heuristic (a detection threshold or tolerance, not a formula from the spec), the commit says so, and the rule is first probed for false positives and misses on the real data's distributions: otherwise the oracle agreeing only shows the code implements the same heuristic. (Added in iteration 52 after code review 7.)
- **Tolerances:** SEC-reported values exact after unit normalization; derived sums ±1%; cross-source market data ±5%; valuations ±0.5% vs the oracle.

## 8. Templates and seeded bugs

**Severity:** P0 = wrong numbers shown as fact (beyond tolerance), data loss or corruption, a crash on a main
flow, or an exploitable security hole. P1 = a feature broken or misleading for some tickers or sectors, or wrong
data matching. P2 = degraded UX, missing validation, inconsistent formatting, misleading docs, slowness.
P3 = cosmetic or wording.

**LOG entry:**
```
## Iteration N: YYYY-MM-DD HH:MM: discovery | fix | sweep
Picked: <IDs>, because <priority rule>
Did: <2–4 lines>
Evidence: <commands + key output lines; qa/evidence/... paths>
Result: <PASS/FAIL per ID; BUG-n opened / fixed / verified>
Gates: ruff ✓ mypy ✓ pytest <N> passed | CI: <state>
Commit: <sha> <subject>
Next: <what the next iteration should pick>
```

**BUG entry:**
```
### BUG-n [P?][AREA] <one-line title>
Status: OPEN | FIXING | FIXED | VERIFIED | WONTFIX (reason) | NEEDS-USER | BLOCKED
Found: iteration N via <matrix ID> | Fixed: <sha> | Regression test: <path::test>
Repro: <minimal steps>
Expected: <value + oracle/source>   Actual: <value>
Root cause: <file:line + why>
Notes / decision needed: <...>
```

**Seed `qa/BUGS.md` with these known issues:**
- BUG-001 [P2][DOCS] OPEN: USAGE_GUIDE's Python examples look up display-style keys (`'Revenue'`, `'Net Income'`, `'Total Assets'`, `'Operating Cash Flow'`), but the data uses canonical snake_case (`revenue`, `net_income`, …). The `.get(..., 0)` calls silently print zeros. Verified 2026-10-20 against `data/output/json/AAPL.json`.
- BUG-002 [P3][CODE] OPEN: every test run warns `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`.
- BUG-003 [P3][UI] OPEN (suspected): in the palette, ArrowUp/ArrowDown pressed before the debounced results land act on the stale list (a sibling of the fixed fast-Enter bug). Confirm with the Node driver.
- NEEDS-USER Q1 [UX]: `/screener` shows no rows until a filter is chosen (intended in `screener.js`); propose showing all companies by default.

## 9. Definition of Done and convergence

**Convergence sweep:** on the latest commit, re-run a cross-section of every area:
1. all gates and CI
2. the golden collection on a fresh scratch folder
3. both oracles
4. a UI smoke test of every page and tab for 5 golden tickers
5. API error paths
6. security payloads
7. docs examples

Zero new P0–P2 bugs means the sweep is clean, so increment `Consecutive clean sweeps`. Any new P0–P2 resets it to 0.

Print `<promise>QA LOOP COMPLETE</promise>` **only when all of these are verified in the current iteration**:
1. Every MATRIX row is PASS, N-A (with a reason), BLOCKED (with evidence) or NEEDS-USER. Nothing is TODO or FAIL.
2. No P0–P2 bug is OPEN or FIXING. Every FIXED bug has a regression test and is VERIFIED. Each P3 is fixed or WONTFIX with a reason.
3. `Consecutive clean sweeps` ≥ 2.
4. The gates are green locally and on CI for the pushed head (`gh pr checks`).
5. The golden collection ran with 0 errors, the oracles agree within tolerance, and the UI sweep shows 0 console errors.
6. `qa/REPORT.md` is written (summary, bugs fixed with commits, NEEDS-USER decisions, remaining risks, UX proposals) and the PR description is updated to match.

Never print the promise to escape the loop. If you're stuck, say so in `qa/LOG.md` and keep working the queue.

## 10. Stuck and anti-loop rules

- An item with no status change for 3 iterations becomes BLOCKED with evidence; move on.
- After 3 failed fix attempts on one bug, stop and question the design (systematic-debugging Phase 4.5). Mark it NEEDS-USER with your analysis.
- Never re-try an approach recorded as failed. If a fix would undo an earlier fix, stop and record the conflict.
- Keep context lean: read state files rather than replaying history, and put long outputs in `qa/evidence/`.

## 11. Command reference (verified 2026-10-20)

```bash
# gates (from repo root): same as CI
ruff check src/ diagnose.py tests/ && mypy && pytest

# collection into scratch (never into data/output)
stock-data AAPL JPM --output-dir "$SCRATCH/golden" [--db PATH] [--workers N] [--years N] \
  [--formats json csv sqlite] [--no-yahoo] [--no-sec] [--sec-user-agent "Name contact@example.com"] [-v]

# valuations (no network)
python -m src.valuation.backfill --db "$SCRATCH/golden/output/stock.db" [TICKERS...]

# web app on a scratch DB (start it from a scratch cwd)
STOCK_WEB_DB_PATH="$SCRATCH/golden/output/stock.db" STOCK_WEB_PORT=8765 stock-web
#   also: STOCK_WEB_ALLOW_COLLECTION=0, STOCK_WEB_ALLOW_QUOTE_REFRESH=0, STOCK_WEB_HOST

# diagnostics
python diagnose.py AAPL -v              # reads data/output/json/ (read-only)
python coverage_report.py JPM PGR PLD   # live SEC calls, so pass explicit tickers

# consistent read-only copy of the real DB
python -c "import sqlite3,sys; s=sqlite3.connect('file:data/output/stock.db?mode=ro',uri=True); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close()" "$SCRATCH/realcopy/stock.db"
```
