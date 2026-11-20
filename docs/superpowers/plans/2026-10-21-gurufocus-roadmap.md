# GuruFocus comparison, and what to build next

## Context

The user asked four things about GuruFocus: what it offers, what this app (stock-database) offers, the
limitations of each, and what to build next to offer something like it. Three read-only research passes
ran on 2026-10-21: our feature inventory, GuruFocus's offering, and the free data that could feed each
missing feature. The user then decided two things:
- **Purpose**: this is their own research tool, run locally, so free SEC and Yahoo data is fine.
- **First milestone**: valuation history with a transparent historical fair-value line.

This plan records the comparison and the roadmap, and details Step 0 and Milestone 1 so they can be
built right after approval. The user then approved the direction and asked for two things:
- **Auto mode.**
- **"Loop engineering until everything finish by the plan".** The build therefore runs as a Ralph
  loop, one task per iteration, through milestones 0-5. M6 stays "Later", as the roadmap says.

## What we have (verified inventory)

- **Data**:
  - 127 US large caps from a hand-kept list. Collection is manual: the CLI, `/collect`, or a
    per-ticker refresh.
  - SEC companyfacts XBRL supplies 78 canonical fields, with banks, insurers and REITs covered. Four
    period types: annual, discrete quarters (Q4 derived), TTM, and point-in-time vintages per filing.
    History is a median 20 years (FY2003+).
  - Yahoo (unofficial yfinance) supplies the quote, about 40 multiples, the profile, analyst targets,
    **daily closes since about 1984 (1.25M bars)**, dividends, splits, earnings surprises, the top-10
    holders and about 2 years of insider trades.
  - The 10y Treasury yield comes from Treasury.gov, or from FRED when a key is set.
- **Analytics**:
  - 31 stored ratios with a sector overlay (bank NIM/efficiency, insurer combined ratio, REIT
    FFO/AFFO proxies).
  - Six valuation models with bear/base/bull cases (`src/valuation/models.py`): DCF, DDM, Graham,
    Lynch, own-history multiples band, owner earnings.
  - The median of five of them gives a cheap/fair/expensive verdict and an upside.
  - A data-quality score.
- **Web** (FastAPI + Jinja + HTMX + Plotly, 79 routes):
  - Workstation tabs DES GP FA ERN STAT HP DVD HDS INS VAL.
  - A price chart with MA/RSI/MACD and comparison against ^GSPC.
  - A screener (latest FY, 45 columns, saved screens), compare, Ctrl+K search, as-of (vintage) view,
    CSV exports, and a localStorage watchlist.
- **Exports**: SQLite (19 tables), JSON, CSV.
- **Engineering**: about 1,190 tests, with ruff, mypy and pytest in CI.

## What GuruFocus offers (from their articles, search-result text of their pages, and reviews; their product pages refuse automated fetches)

- **Coverage**:
  - 60-90k stocks in 100+ countries.
  - Fundamentals come from FactSet (page footer, Oct 2026; older replies name Morningstar), quotes from
    QuoteMedia, estimates from Refinitiv/FactSet.
  - SEC filers' financials appear in 2-4 business days.
- **Stock pages**:
  - A customizable summary dashboard: warning and good signs, predictability stars, the GF Score
    pentagon, the GF Value chart, and a "Valuation Box" of intrinsic values against price. Also
    Piotroski and Lynch charts, guru and insider trade charts, and segment charts.
  - 30-year financials: 30 years and 120 quarters, per-share and common-size rows, charts against the
    industry median.
  - Interactive charts of any series, with 34 technical indicators.
  - A page per ratio with its 10-year min/median/max and the stock's industry rank.
  - Peer tables, ownership, dividends, and earnings-call transcripts.
- **Proprietary metrics** (only partly published):
  - **GF Value**: the historical multiples the stock traded at, of sales, earnings, FCF and book,
    times an unpublished adjustment for past returns and growth. Analyst estimates extend it about 2
    years. Price/GF Value maps to six labels.
  - **GF Score** (0-100): five 1-10 ranks (Financial Strength, Profitability, Growth, GF Value,
    Momentum). Profitability and Growth weigh the most; the weights are unpublished, fitted on a
    2006-2021 backtest.
  - **Rank inputs**:
    - Financial Strength: interest coverage, Altman Z, equity/assets, cash/debt, debt/revenue.
    - Profitability: the 10-year operating margin and its trend, Piotroski F, predictability, 5-year
      revenue growth, profitable years out of 10, and 10-year median ROIC.
    - Growth: 3- and 5-year revenue growth and 5-year EBITDA growth.
    - Momentum: the mean of the 12-1 and 6-1 month returns, divided by beta.
  - **Predictability**: 1-5 stars from the 10-year consistency of revenue/share and EBITDA/share.
    Any operating loss means 1 star.
  - **Standard scores**: Piotroski F, Altman Z, Beneish M, Sloan, ROIC vs WACC.
- **Valuation tools**:
  - An EPS-based DCF: 10 years at the 10-year growth, clamped 5-20%, then 10 years at 4%, discounted
    at the 10y Treasury rounded up plus 6 points (about 12%).
  - An FCF DCF, a DDM, a reverse DCF, Lynch, Graham on tangible book, NCAV/net-net, median P/S value,
    projected FCF, EPV.
- **Gurus and insiders**:
  - 140+ gurus and 18k+ institutions/funds from 13F. "Real-time picks" from 13D/13G and Form 4.
  - An aggregated guru portfolio, a scoreboard and model portfolios.
  - Form 4 insider trades since 2003, with CEO/CFO, cluster and double/triple buys.
- **Tools**:
  - A 500+ filter screener with prebuilt screens (Buffett-Munger, Lynch, Graham net-net, Magic Formula,
    undervalued-predictable).
  - Backtesting to 2006, portfolios and email alerts.
  - An Excel add-in and a Sheets add-on, a data API (about $200/month), mobile apps.
  - Market pages: Buffett Indicator, Shiller P/E, the yield curve.
- **Price**: annual plans. Premium (US) is about $549, Premium Plus about $1,398 and Professional about
  $2,448, plus regional add-ons.

## Gap analysis

| Area | GuruFocus | Ours | Gap and effort |
|---|---|---|---|
| Financial history | 30y/120q, per-share, common-size, charts | ~20y/75q + TTM + point-in-time vintages; tables only | Per-share and growth series, trend charts (S) |
| Valuation history | 10y range per ratio, industry rank, GF Value Line | 5-FY multiples band only | **Missing, but the data is stored (S-M)**: Milestone 1 |
| Intrinsic values | 10 methods | 6 methods, bear/base/bull, sensitivity grid | Reverse DCF, EPV, NCAV, median P/S (S) |
| Scores and ranks | GF Score, 5 ranks, predictability, F/Z/M/Sloan, warning signs | Data-quality score only | Missing (S-M); percentiles want a bigger universe |
| Screener | 500+ filters, 90k stocks, alerts, backtests | 45 columns, latest FY, 127 stocks | More fields, prebuilt screens (S-M) |
| Gurus (13F) | 140+ gurus, trades, aggregated portfolio | Yahoo top-10 holders | Missing (M) |
| Insiders | Form 4 since 2003, CEO and cluster buys | Yahoo, ~2 years, no aggregates | Partial (M) |
| Universe | 60-90k worldwide | 127 US | M-L: Yahoo's 2.5 s pacing caps it at a few hundred |
| Market pages | Buffett Indicator, CAPE | None | S |
| Transcripts, segments, international, ETFs | Yes | None | L, and licensing |

**Where we're ahead**:
- Every formula is published in our code.
- Point-in-time vintages keep the as-first-reported figures, the basis for history free of look-ahead.
- Tag-level XBRL provenance, with data-quality checks.
- Sector-aware handling of banks, insurers and REITs.
- Bear/base/bull ranges.
- Free and local, and tested.

## Limitations

**GuruFocus**
- **Opaque formulas and weak evidence**: GF Value's formula and cutoffs and the GF Score's weights are
  unpublished. Its studies are self-published, price-only and survivorship-biased.
- **Backward-looking valuation**: multiples-based value is backward-looking, poor for changing
  businesses, short histories and financials (its own caveat).
- **Data quality**: the data is vendor-normalized, with reported errors (units, missing quarters).
- **Cost**: $549-2,448 a year.

**Ours**
1. **The verdicts are skewed**: 0 names are cheap, 16 fair, 108 expensive, and the median upside is
   -58%. Graham, DDM and a capped DCF value asset-light large caps far below price.
2. **The DCF basis counts interest twice.**
   - `levered_fcf` subtracts interest from OCF − capex (`src/parsers/calculated_metrics.py:248-262`).
   - US GAAP OCF already bears interest paid, as the spec says
     (`docs/superpowers/specs/2026-07-13-valuation-layer-design.md:86-88`).
   - Of the 91 DCF bases, 30 are understated by 10% or more (DE 1.67x, D 1.61x, T 1.57x, AMGN 1.52x).
3. **The multiples band divides spin-adjusted closes by as-reported EPS**: GE's band reads 12.2x to
   1,044x (`src/valuation/models.py:371-408`). Fixing it moves the median, so it goes to the user.
4. **No history analytics**: no valuation ranges, growth fields, per-share series or fundamental
   charts, though 40 years of prices and 20 of fundamentals are stored.
5. **No cross-company analytics**: no ranks or percentiles, and no stored SIC code. The screener sees
   only the latest FY.
6. **Thin ownership and scoring**: no F/Z/M scores and no 13F, and insiders are Yahoo's 2 years.
7. **Fragile platform**: a hand-kept universe, no scheduler, no IFRS filers (BUG-044), and Yahoo is
   unofficial with no retry.

## Free data for each gap

| Gap | Source | Effort | Main risk |
|---|---|---|---|
| Valuation history, growth, trend charts | Already stored: `price_bars` + TTM/quarterly + vintages | S-M | Split basis; no quarterly filing dates |
| F/Z/M scores, predictability | Stored canonical fields | S | Sector applicability |
| Insiders | EDGAR Form 4 XML + SEC Insider Transactions Data Sets (2006+) | M | Transaction codes, amendments |
| 13F gurus | SEC 13F Data Sets (2013+) + a CUSIP map (curated for our tickers; OpenFIGI otherwise) | M | 45-day lag; values in $000 before 2023 |
| Industry percentiles, bigger universe | companyfacts for more filers (~12 min for 6k) + SIC; `frames` API | M | Yahoo pacing for prices |
| Estimates | Yahoo snapshot; Alpha Vantage (25 calls/day) | S | No history |
| Buffett indicator, CAPE | FRED Z.1 + GDP (Wilshire left FRED in 2024); Shiller's `ie_data.xls` | S | Series choice |

SEC data is free to reuse. The free price data, Yahoo included, is for personal use only, which fits
the user's purpose.

## Roadmap

| # | Milestone | Size |
|---|---|---|
| 0 | Fix the levered-FCF double count | S |
| 1 | **Valuation history + historical fair-value line** (below) | M |
| 2 | Quality scores and ranks: growth CAGRs, per-share series, Piotroski F, Altman Z, Beneish M, Sloan, predictability stars, ROIC vs WACC, warning signs, a published five-rank composite, screener columns and prebuilt screens | M |
| 3 | SEC ownership: Form 4 insider history (CEO and cluster buys, net buying), then 13F for a curated guru list (holdings, quarter-over-quarter trades, aggregated portfolio) | M |
| 4 | More valuation tools (reverse DCF, EPV, NCAV, median P/S value) and market pages (Buffett indicator, CAPE) | S |
| 5 | Wider universe (S&P 500 first), stored SIC and industry, industry percentiles, a local scheduler | M-L |
| 6 | Later: backtesting (needs a survivorship-free universe), IFRS mapping, segments, transcripts, local alerts and portfolios | L |

The open QA work continues alongside: 7 P2s, starting with BUG-142, and code review 15, which is due.

## Step 0: levered FCF (own commit)

- **The fix**:
  - `_calculate_levered_fcf` returns `_calculate_fcf`. Its docstring cites ASC 230: interest paid is
    already in OCF.
  - `load_inputs` (`src/valuation/inputs.py:504,534`) reads `ma.free_cash_flow` only, so an offline
    backfill is right before any re-collection. The stored `metrics_annual.levered_fcf`, shown in the
    FA tab and the screener, refreshes at the next collection.
- **RED tests first**:
  - `tests/test_calculated_metrics.py`: `test_levered_fcf_does_not_subtract_interest_again` expects 200
    (today 180), and `test_levered_fcf_without_capex_is_ocf` expects 250 (today 230).
  - `tests/test_valuation_inputs.py:73-77` becomes `test_fcf_basis_is_free_cash_flow`, expecting 80.
  - The fixture in `tests/test_valuation_store.py:29-33` also seeds `free_cash_flow`.
- **Docs**: update the spec's lines 86-88, the README's DCF row, and
  `qa/oracle/valuation_oracle.py:445-446`.
- **Record**: log it at the next free BUG number (VAL, P2) and mark it verified. Run the mutants, then
  diff the DCF results and verdicts on a DB copy, saved as `qa/evidence/levfcf-*.txt`.

## Milestone 1: valuation history and a historical fair value

**Data facts that shape the design** (verified with read-only queries):
- **Price basis**: `price_bars` has no adj_close. Closes are split-adjusted to today's basis (AAPL
  124.81 to 129.04 across 2020-08-31's 4:1 split) and also divided by Yahoo's spin-off factors (GE
  1.281 on 2023-01-04, 1.253 on 2024-04-02).
- **Share counts**: filed counts sit on mixed split bases, so never divide price by filed EPS. Take
  ratios on totals, with market cap = close × PF(d) × S(q). PF(d) is the product of the non-split
  factors after d.
- **Filing dates**: no quarter has one (`financials_quarterly.filed_date` is NULL in all 8,906 rows,
  and TTM has no such column), so a filing-lag rule decides when each figure was known.
- **Yahoo's EV/EBITDA** is only in the JSON exports. `market_snapshots.ev_to_ebitda` is ours.

**`src/valuation/history_inputs.py`** (new): `load_history_inputs(conn, ticker) -> HistoryInputs`.
- **Helpers**: `is_share_split`, `price_factor_after`, `known_on`, `pick_share_field`,
  `normalize_share_series`.
- **Reuse** from `src/valuation/inputs.py`: `_eps_moved_inversely` (:149), `_normalize_splits`
  (:188), `_simple_ratio` (:265), `_yahoo_dividend_factors` (:371), `_dividend_onto` (:414) and
  `_drop_spin_off_dividends` (:441). Also extract the bars-to-price factor (:628-639) into a shared
  helper.
- **Shares**:
  - One field per ticker: the first with at least 60% coverage, in the order weighted basic, shares
    outstanding, weighted diluted, NI/EPS.
  - Drop one-quarter spikes.
  - Fold seams that match declared splits. An undeclared seam of 1.5x or more folds only if EPS moved
    inversely, so mergers stay organic.
  - N/A when our count is more than 1.25x off Yahoo's (BX, BRK-B; Q7).
- **Known date**:
  - Q1-Q3: period end + 45 days.
  - Q4: the first 10-K filed date from the vintages if within 120 days, else + 75 days.
  - Capped at the collection day. A figure more than 200 days old is stale.
- **Fundamentals**: each TTM row goes through `CalculatedMetrics().calculate_all(row,
  sector=sector_class)`, for EBITDA, net debt, FCF and FFO with the sector suppression. Common equity
  = total − preferred.
- **Samples**: each month's last bar, plus the latest bar as the current point.

**`src/valuation/history.py`** (new): `compute_history(inputs) -> HistoryResult`, with
`multiples_at`, `window_stats`, `implied_price`, `fair_value_metrics(sector)` and
`historical_value_label`.
- **Multiples**:
  - P/E, P/S, P/B, P/FCF, P/FFO, EV/EBITDA (EV = market cap + net debt), and dividend yield
    (trailing 365-day dividends / close).
  - A denominator of zero or less gives NULL.
- **Window**:
  - The trailing 10 years. Below 36 monthly values, N/A.
  - Statistics: min, p25, median, p75, max, and the current value's percentile rank.
- **Metrics by sector**:
  - general/utility/energy: P/E, P/S, P/B, P/FCF, EV/EBITDA.
  - bank: P/E, P/S, P/B. Insurance adds P/FCF.
  - REIT: P/FFO, P/S, P/B, EV/EBITDA.
  - Dividend yield is shown but never combined.
- **The fair-value line**:
  - FV(d) = the median, across the sector's metrics (each with at least 36 months, and at least 2
    metrics in all), of the trailing-median multiple × that date's fundamental per share. For
    EV/EBITDA it is (m × EBITDA − net debt) / shares.
  - Low and high come from the p25 and p75 multiples.
  - Trailing windows only, and no fitted weights: both would bring in look-ahead.
- **Labels (decided by approving this plan)**: price / FV below 0.7 is "far below", 0.7-0.9 "below",
  0.9-1.1 "near", 1.1-1.3 "above", over 1.3 "far above". They are computed at read time against the
  live price.

**Storage** (`src/exporters/sqlite_store.py`, `_create_schema` :146-338):
- `valuation_history`: PK (ticker, date); columns close, market_cap, enterprise_value,
  ttm_period_end, pe, ps, pb, pfcf, ev_ebitda, pffo, div_yield, fair_value, fair_value_n.
- `valuation_ranges`: PK (ticker, metric), where metric includes 'fair_value'. Columns applicable,
  na_reason, as_of, window_start, n_months, current, pct_rank, min, p25, median, p75, max,
  implied_value, in_fair_value, n_metrics, computed_at.
- `export_valuation_history` replaces a ticker's rows in one transaction.

**Engine**: a separate indicator, not a seventh model.
- It hooks into `compute_and_store` after `export_valuations` (`src/valuation/engine.py:169`), in its
  own try/except.
- That covers CLI collection, web jobs and `python -m src.valuation.backfill`.
- `MEDIAN_MODELS` (:53), the six-model contract and the verdicts stay unchanged.

**Web**:
- **Repository readers** `valuation_history()` and `valuation_ranges()` return [] when the table is
  missing.
- **API**: `GET /api/stocks/{t}/valuation-history` returns the price, the historical value with
  low/high, label, metrics and cutoffs, the ranges, and the monthly series. 404 for an unknown ticker
  or ^GSPC.
- **VAL tab**: a section loaded lazily (`/ui/stocks/{t}/val-history`,
  `templates/fragments/val_history.html`). It holds:
  - a label pill;
  - price vs the fair-value line with shaded bands;
  - the metrics table: current, 10y min/median/max, percentile;
  - a single-multiple chart with min/median/max lines;
  - method notes and N/A reasons.
  `renderValHistory` in `static/app.js` builds on the SLATE layout helpers (:7-33).
- **Screener** (`src/webapp/screener.py`, `routes/pages.py`): `pe_pct_10y` through
  `div_yield_pct_10y`, plus `price_to_hist_value`, through LEFT JOINs on `valuation_ranges`. They
  appear as filter options, result columns and in the CSV.

**Docs**:
- A README "Valuation history" section: definitions, the lag rule, the restatement look-ahead
  (accepted in v1), the window rules, the FV method and cutoffs, the tables, the API, the UI and the
  limitations.
- Fix the stale "no price series" text (README :701-702, `src/valuation/pit_metrics.py:10-14`).
- USAGE_GUIDE in prose and SQL only.

**Edge cases**:

| Case | Handling |
|---|---|
| GEV, SNDK (under 36 months) | Multiples only |
| CEG | Shorter window, labeled as such |
| SPCX | No TTM, so N/A |
| V, BRK-B, BX | N/A (share classes, Q7) |
| Negative earnings | P/E is NULL; FV comes from the other metrics |
| ^GSPC | Never computed |
| XOM, BLK | Check continuity at the splice quarter |
| Spin-offs | Pre-spin P/S reads high (noted) |

**Records**: log the multiples-band basis defect (limitation 3) as an open bug, plus a LOG/MATRIX entry
for the milestone.

## Milestones 2-5 (each is designed in its first loop iteration, then built task by task)

- **M2: Quality scores and ranks**
  - Per-share series, and 3/5/10-year CAGRs of revenue, EPS, EBITDA, FCF and book per share.
  - Piotroski F (9 tests).
  - Altman Z: the original, Z'' for non-manufacturers, N/A for banks and insurers.
  - Beneish M (N/A for financials), the Sloan ratio, ROIC vs WACC (CAPM equity cost + after-tax debt
    cost).
  - Predictability stars, from our own published formula: 10-year consistency of revenue/share and
    EBITDA/share.
  - About 20 warning-sign rules with severities.
  - Five ranks: financial strength, profitability, growth, valuation (from M1's price/FV), and
    momentum (12-1 and 6-1 month returns / beta). They're percentiles within the universe, or within
    the sector where it has enough peers.
  - A 0-100 composite whose weights are published. **Default: equal weights**, recorded as a
    NEEDS-USER question.
  - A scores section in the UI, and screener columns.
  - Preset screens: Buffett-Munger, Lynch, Graham net-net, Magic Formula, undervalued-predictable.
  - Checked by an independent oracle script computed from raw companyfacts.
- **M3: SEC ownership**
  - **Form 4 parser** (ownershipDocument XML): transaction code, shares, price, acquired/disposed,
    holdings after, role, 10b5-1 flag, amendments. It fetches each filing for our issuers through the
    app's rate-limited, cached SEC handler.
  - **Insider aggregates**: net open-market buying (P vs S), CEO/CFO buys, cluster buys (3 or more
    insiders within 30 days). The INS tab shows the SEC data, with Yahoo as the fallback.
  - **13F**: a curated list of about 30 value-investor CIKs (Berkshire, Baupost, Pershing Square,
    Greenlight, Oaktree, Markel...).
    - Their 13F-HR info tables, fetched per filing.
    - A CUSIP-to-ticker map for our universe, taken from the info tables' issuer names and checked;
      OpenFIGI without a key for the rest.
    - Holdings, quarter-over-quarter trades (new, added, reduced, sold out), "gurus holding" per stock,
      a page per guru, and an aggregated guru portfolio.
  - SEC's bulk data set ZIPs are not downloaded unless you approve their sizes (NEEDS-USER).
- **M4: Valuation tools and market pages**
  - **Display-only indicators**, excluded from the median like owner earnings, so no verdict changes:
    - reverse DCF (the growth the price implies);
    - EPV;
    - NCAV and Graham net-net;
    - median P/S value (from M1's 10-year median P/S);
    - the Graham number on tangible book.
  - **Market page**:
    - The Buffett indicator, from the FRED series NCBEILQ027S and GDP via FRED's keyless
      `fredgraph.csv` (a few KB each).
    - Shiller CAPE, from `ie_data.xls` on shillerdata.com (about 1-2 MB).
    - The yield curve, from the Treasury.gov feed the app already uses.
- **M5: A wider universe and automation**
  - A universe file in the repo and a `--tickers-file` CLI option.
  - S&P 500 members, from a public list saved as a dated file.
  - The SIC code stored per company, industry groups (2-3 digit SIC), industry percentiles for M2's
    ranks where n ≥ 10, and a screener industry filter.
  - A `stock-data refresh` command (quotes daily, SEC weekly, incremental), with instructions for
    Windows Task Scheduler. **Installing the scheduled task stays yours (NEEDS-USER)**.
  - The added tickers collected live into your DB after a backup. This takes hours at Yahoo's pacing,
    so it runs in the background with progress logged.
  - Checks on DB size, indexes and page speed.
- **M6** stays "Later", as the roadmap says, and is outside this loop.

## How the work runs: a build loop, in auto mode

Once this plan is approved, the build runs as a loop with one task per iteration, as you asked:

1. **Setup** (the first actions after approval):
   - Copy this plan into the repo as `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md`.
   - Create `qa/BUILD.md`: the task board for milestones 0-5, with statuses TODO / DOING / DONE /
     NEEDS-USER and a short log per iteration.
   - Create `qa/BUILD_LOOP_PROMPT.md`: the per-iteration protocol below.
   - Start the Ralph loop with the prompt: "Read qa/BUILD_LOOP_PROMPT.md and run exactly ONE iteration
     of it. Output <promise>BUILD LOOP COMPLETE</promise> only when its Definition of Done is fully met
     and verified in this iteration." Use `--completion-promise "BUILD LOOP COMPLETE"` and
     `--max-iterations 150`.
2. **Each iteration**:
   - **Read** the board, the tail of the log and git state.
   - **Pick** the next TODO in milestone order. A milestone without a spec first gets a design
     iteration: a spec in `docs/superpowers/specs/` and its tasks on the board.
   - **Build** it TDD (RED evidence, then GREEN) through workers, per CLAUDE.md: opus for multi-file
     or tricky work, sonnet for routine. The orchestrator reviews every diff.
   - **Verify**: gates, mutants for each new rule, real-data checks on a DB copy, and a web smoke test
     when the UI changed.
   - **Record**: the board, the log, evidence in `qa/evidence/`, and any bug found in `qa/BUGS.md` with
     the census updated.
   - **Commit and push**: a conventional commit on `qa/full-loop`, staging files by name, with the
     Co-Authored-By line. Never stage data/, .env, CLAUDE.md or `.claude/*.local.*`.
3. **Milestone close**: an opus code review, with its Critical and Important findings fixed. Then the
   milestone's live DB step, backup first.
4. **Rules carried over from the QA loop**:
   - SEC at most 10 req/s with the app's default User-Agent. No network in tests.
   - Python 3.9-compatible, CRLF files, cp1252-safe console output.
   - stock-web only in the background with collection and quote refresh disabled; the port freed
     afterwards.
   - No changes to the global environment. No accounts or API keys created. No paid sources.
5. **Decisions that are yours** go into `qa/BUGS.md` as NEEDS-USER questions. The loop continues
   with the default that leaves today's verdicts and data untouched:
   - verdict or median membership;
   - composite weights;
   - bulk SEC data set downloads;
   - installing the scheduled task;
   - the multiples-band fix.
6. **Definition of Done**:
   - Milestones 0-5 are DONE on the board, with evidence.
   - Each milestone's code review is closed with no open Critical or Important finding.
   - Gates are green, and README/USAGE_GUIDE are current.
   - Your DB is updated per the plan.
   - The tree is clean except your own files, and everything is pushed.
   - Only the NEEDS-USER questions remain, each with its default applied.

**What approving this plan authorizes** (beyond code changes):
- **Writes to your DB**, each after a backup to `%LOCALAPPDATA%\stockdb-backups\`:
  - the offline backfill (Step 0, M1, M2 and M4);
  - SEC insider and 13F rows (M3);
  - live collection of the added tickers (M5).
- **The app's network fetches**:
  - SEC EDGAR filings: Form 4 and 13F-HR, rate-limited and cached.
  - OpenFIGI without a key.
  - Two small FRED CSVs, Shiller's xls (about 1-2 MB) and a public S&P 500 list page.
- **Commits pushed** to `qa/full-loop`, never to main.

## Verification

- **Gates**: `ruff check src/ diagnose.py tests/`, `mypy`, and
  `python -m pytest -p no:cacheprovider -o addopts="" -q`.
- **Mutants**: each of these must fail a test:
  - no split fold;
  - no filing lag;
  - full-window instead of trailing medians;
  - a label cutoff moved.
- **Real data, on a copy** made with the backup API from a mode=ro connection: run
  `python -m src.valuation.backfill --db <copy>`, then `qa/oracle/history_vs_yahoo.py`. Gates against
  Yahoo:
  - P/E: at least 75% within 5% and 88% within 10%.
  - P/S and P/B: at least 80% within 5% and 90% within 10%.
  - EV/EBITDA: at least 55% within 10% and 75% within 20%.
  - Share counts: at least 90% within 5%.
  - TTM vs `metrics_annual`: at least 95% within 1%.
  - Market cap stays continuous across the AAPL, NVDA, AVGO, WMT, GE, KLAC, CRWD and BKNG splits.
  - `valuation_summary` is unchanged by Milestone 1.
  - Misses are explained in `qa/evidence/valhist-*.txt`.
  - The design prototype already met these: P/S 106/125 within 5%, P/B 97/117, P/E 97/123.
- **Web smoke test on a scratch copy**:
  - Start it in the background:
    `STOCK_WEB_DB_PATH=<copy> STOCK_WEB_PORT=8765 STOCK_WEB_ALLOW_COLLECTION=0 STOCK_WEB_ALLOW_QUOTE_REFRESH=0 stock-web`,
    with no arguments.
  - curl the API.
  - Look at the VAL section in Chrome for AAPL, NVDA, GE, JPM, O and GEV.
  - Screen for price / historical value below 0.9.
  - Stop the server and confirm the port is free.
- **The user's DB**: back up to `%LOCALAPPDATA%\stockdb-backups\<date>`, then run the offline backfill
  (no network) on `data/output/stock.db`. That fills the new tables and applies Step 0 to the
  valuations. Report the verdict changes.
