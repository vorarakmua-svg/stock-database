# Stock Data Collection System

A professional-grade Python data pipeline for collecting US stock fundamentals from **free sources only**. Designed to support automated valuation models (DCF, Graham Number, Peter Lynch Fair Value) and AI-powered investment analysis.

## Features

- **Multi-Source Data Collection**
  - Yahoo Finance: Market data, valuation metrics, analyst estimates, dividend history
  - SEC EDGAR: 10+ years of financial statements (185-248 XBRL fields per year)
  - FRED: Risk-free rate (10-Year Treasury yield) for WACC calculations

- **Comprehensive Financial Metrics**
  - Balance Sheet, Income Statement, Cash Flow Statement
  - Calculated metrics: EBITDA, FCF, ROIC, Net Debt, Interest Coverage, EV/EBITDA
  - Historical price statistics: CAGR, volatility, Sharpe ratio, max drawdown

- **Valuation Model Support**
  - Analyst estimates with target prices and recommendations
  - Complete dividend payment history with CAGR calculations
  - All inputs needed for DCF, Graham Number, and Peter Lynch models

- **Valuation Models** — 5 sector-aware fair-value models computed and stored per
  ticker (`src/valuation/`): two-stage FCF DCF, multi-stage DDM, Graham Number,
  Peter Lynch fair-value, and a historical price-multiple band. Each produces a
  bear/base/bull per-share range plus the exact assumptions used (growth,
  discount rate, basis figures), so every number is reproducible from stored
  data rather than recomputed live. Models that don't fit a company's sector
  (e.g. DCF/Lynch for banks and REITs) are honestly reported as not-applicable
  with a human-readable reason instead of a misleading number — no fake output
  is ever better than an admitted gap. A sixth model, **owner earnings
  ("Buffett mode")**, values companies the way Buffett describes rather than by
  textbook DCF, and is deliberately kept out of this five-model median instead
  of folded into it. See [Valuation layer](#valuation-layer) below.

- **Valuation history** — each company's monthly P/E, P/S, P/B, P/FCF, EV/EBITDA
  and dividend yield since its filings begin, each multiple's 10-year range and
  percentile, and a published *historical value* (our GF Value counterpart) drawn
  against the price, with no look-ahead. On the VAL tab, in the screener and the
  API. See [Valuation history](#valuation-history).

- **Quality scores** — Piotroski F, Altman Z, Beneish M, the Sloan ratio, ROIC vs
  WACC, predictability stars, 20 warning signs, five percentile ranks and a 0-100
  composite (our GF Score counterpart), with every formula and weight published.
  On the STAT tab, in the screener (with five preset screens) and the API. See
  [Quality scores](#quality-scores).

- **Other valuation indicators** — a reverse DCF (the growth the price implies), the earnings power value, NCAV
  with Graham's net-net, the median P/S value and the Graham number on tangible book, each with its formula
  published and none of them in the models' median or the verdicts. On the VAL tab, in the screener and the API.
  See [Other valuation indicators](#other-valuation-indicators).

- **SEC ownership** — insiders' Form 4 trades (every filing of the last 3 years, with open-market buying,
  CEO/CFO buys, cluster buys and 10b5-1 sales) and the 13F holdings of 34 gurus (the last 8 quarters, with
  each quarter's new, added, reduced and sold-out positions), read from the SEC's own filings
  (`src/ownership/`). On the INS and HDS tabs, the Gurus pages, the screener and the API. See
  [SEC ownership](#sec-ownership-insiders-and-gurus).

- **Market data** — FRED's corporate equities and GDP, Robert Shiller's monthly S&P data with his CAPE and
  Treasury.gov's daily yield curve (`src/market/`), behind a Market page: the Buffett indicator against its
  long-run trend, the CAPE against its history and the yield curve against a year earlier, with its inversions.
  See [Market data](#market-data).

- **The universe and industries** — the S&P 500's members as a dated file in the repository (`universe/`),
  `stock-data --tickers-file` and `--skip-existing` to collect a list, each company's SEC SIC code, industry groups
  from it, and the quality scores' five ranks and composite also within the industry (`src/universe/`). On the DES and
  STAT tabs, in the screener's industry filter and the API. See
  [The universe and industries](#the-universe-and-industries).

- **Keeping the data current** — `stock-data refresh` (`src/refresh.py`): quotes and new daily bars every day, the SEC's
  new filings, the insiders and the gurus every week, the market data, then the recomputation, with a lock, a log and
  exit codes a scheduler can read. Scheduling it on Windows is the user's decision. See
  [Keeping the data current](#keeping-the-data-current-stock-data-refresh).

- **Database-Like Behavior**
  - Merge mode preserves historical data across runs
  - Tracks collection history and price snapshots
  - Deduplicates insider transactions

- **Self-Diagnostic Tools**
  - Built-in diagnostic script for troubleshooting data issues
  - Comprehensive warnings and error tracking

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/stock-database.git
cd stock-database

# Install dependencies
pip install -r requirements.txt

# Or install as a package (provides the `stock-data` command)
pip install -e .
```

### Requirements

- Python 3.8+
- Dependencies:
  - `yfinance>=0.2.36`
  - `requests>=2.31.0`
  - `pandas>=2.1.0`
  - `python-dotenv>=1.0.0`

## Usage

### Basic Usage

```bash
# Fetch data for one or more tickers
python -m src.main AAPL MSFT GOOGL

# Fetch with custom output directory
python -m src.main AAPL --output-dir ./my_data

# JSON output only
python -m src.main AAPL --formats json

# Verbose logging
python -m src.main AAPL -v
```

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `tickers` | Stock ticker symbols (required unless `--tickers-file` gives some) | - |
| `--tickers-file` | A file of tickers to collect, repeatable: a `.txt` (a ticker a line, `#` comments) or a `.csv` (its `ticker` or `Symbol` column); added after the positional tickers, each ticker once (see [Collecting a list](#collecting-a-list)) | - |
| `--skip-existing` | Leave out the tickers already in the database (`--db`, else `<output-dir>/output/stock.db`) | `false` |
| `--output-dir`, `-o` | Base output directory | `./data` |
| `--formats`, `-f` | Output formats (`json`, `csv`, `sqlite`) | `json csv sqlite` |
| `--db` | SQLite database path | `<output-dir>/output/stock.db` |
| `--workers`, `-w` | Tickers fetched concurrently (1 = sequential) | `4` |
| `--batch-size` | Companies exported, stored and valued at a time as they arrive (1 or more); a run stopped part way keeps the batches it stored (see [Output](#output)) | `25` |
| `--years` | Limit history to the most recent N years | all available |
| `--no-yahoo` | Skip Yahoo Finance data | `false` |
| `--no-sec` | Skip SEC EDGAR data | `false` |
| `--sec-user-agent` | SEC API User-Agent header | `StockDataCollector admin@example.com` |
| `--verbose`, `-v` | Enable debug logging | `false` |

`refresh` as the first argument is a command of its own, `stock-data refresh`, with its own options: it keeps the
database current (see [Keeping the data current](#keeping-the-data-current-stock-data-refresh)).

### Examples

```bash
# Fetch 10 years of data for tech stocks
python -m src.main AAPL MSFT GOOGL AMZN META --years 10

# Yahoo Finance data only (faster)
python -m src.main TSLA --no-sec

# SEC data only (more comprehensive financials)
python -m src.main AAPL --no-yahoo

# Custom SEC User-Agent (recommended for production)
python -m src.main AAPL --sec-user-agent "MyCompany contact@mycompany.com"

# A list of tickers from a file (repeatable, beside positional tickers)
python -m src.main --tickers-file universe/core.txt

# The S&P 500's members the database doesn't hold yet
python -m src.main --tickers-file universe/sp500-2026-10-25.csv --skip-existing

# Keep the database current: quotes, bars, new filings, ownership and market data, recomputation
python -m src.main refresh
```

## Output

### Directory Structure

```
data/
├── output/
│   ├── json/
│   │   ├── AAPL.json      # Full data per ticker
│   │   ├── MSFT.json
│   │   └── GOOGL.json
│   └── csv/
│       └── summary.csv    # Summary metrics for all tickers
└── logs/
    └── refresh-2026-10-25.log   # stock-data refresh's log, a file for each day it ran
```

A collection writes its outputs as the companies arrive, 25 at a time (`--batch-size`): each batch's JSON files, its
rows of the two CSVs (merged with the rows already there) and its companies in the database, each company in its own
savepoint, then the batch is valued and every stored company ranked. A company whose database write fails is rolled
back alone, and a run stopped part way keeps the batches it stored. The summary says `Stored in the database: N of M`.
The exit code is 0 when the database took every company fetched, 1 when it didn't (each one is named in the log with
why, such as a value SQLite can't write or a locked database), and 130 after Ctrl+C, which cancels the tickers not
started and leaves out the companies fetched since the last batch. Running the command again collects the rest; with
`--skip-existing`, only the companies the database doesn't hold yet.

### JSON Output Structure

Each ticker JSON file contains:

```json
{
  "ticker": "AAPL",
  "cik": "0000320193",
  "company_name": "Apple Inc.",
  "collected_at": "2026-01-07T12:00:00",

  "company_info": {
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "country": "United States",
    "full_time_employees": 164000,
    "website": "https://www.apple.com"
  },

  "market_data": {
    "current_price": 243.85,
    "market_cap": 3670000000000,
    "volume": 45000000,
    "beta": 1.24,
    "fifty_two_week_high": 260.10,
    "fifty_two_week_low": 164.08
  },

  "valuation": {
    "pe_trailing": 38.5,
    "pe_forward": 32.1,
    "peg_ratio": 2.8,
    "price_to_book": 62.3,
    "dividend_yield": 0.0044
  },

  "financials_annual": {
    "2024": { /* 185-248 SEC XBRL fields */ },
    "2023": { /* ... */ },
    "2022": { /* ... */ }
  },

  "financials_quarterly": {
    "2024-Q4": { /* ... */ },
    "2024-Q3": { /* ... */ }
  },

  "analyst_estimates": {
    "target_price_mean": 287.71,
    "recommendation": "buy",
    "number_of_analysts": 41,
    "upside_potential": 0.18
  },

  "dividend_history": {
    "dividend_rate": 1.0,
    "dividend_yield": 0.0041,
    "payout_ratio": 0.157,
    "dividend_cagr": 0.078,
    "years_of_data": 12.4,
    "dividend_payments": [ /* full history */ ]
  },

  "calculated_metrics": {
    "ebitda": 137000000000,
    "free_cash_flow": 110000000000,
    "roic": 0.58,
    "net_debt": -51000000000,
    "interest_coverage": 29.5,
    "enterprise_value": 3620000000000,
    "ev_to_ebitda": 26.4
  },

  "risk_free_rate": {
    "risk_free_rate": 0.0417,
    "source": "fred",
    "series": "DGS10"
  },

  "price_history": {
    "period": "10y",
    "cagr": 0.267,
    "annual_volatility": 0.285,
    "max_drawdown": -0.315,
    "sharpe_ratio_estimate": 0.89
  },

  "insider_transactions": [ /* SEC Form 4 filings */ ],

  "data_sources": ["yahoo_finance", "sec_edgar", "fred_fred", "calculated_metrics"],
  "warnings": [],
  "errors": []
}
```

### CSV Summary Fields

The summary CSV contains key metrics for quick comparison:

| Category | Fields |
|----------|--------|
| Identifiers | ticker, cik, company_name |
| Company Info | sector, industry, country, employees, website |
| Market Data | current_price, market_cap, volume, beta, 52_week_high/low, ma_50, ma_200 |
| Valuation | pe_trailing, pe_forward, peg_ratio, eps_trailing, eps_forward, price_to_book |
| Financials | total_revenue, net_income, total_cash, total_debt, ebitda, free_cash_flow |
| Margins | profit_margin, operating_margin, return_on_equity, return_on_assets |
| SEC Data | sec_revenue, sec_net_income, sec_total_assets, sec_stockholders_equity |
| Calculated | calc_ebitda, calc_fcf, calc_roic, calc_net_debt, calc_ev, calc_ev_to_ebitda |
| Risk Metrics | annual_volatility, max_drawdown, sharpe_ratio, cagr_5y |
| Data Quality | data_sources, warning_count, error_count |

## Diagnostic Tool

Troubleshoot data issues without AI assistance:

```bash
# Basic diagnosis
python diagnose.py AAPL

# Verbose mode (shows all available fields)
python diagnose.py MSFT -v

# Check multiple tickers
python diagnose.py AAPL MSFT GOOGL
```

## Data Sources

### Yahoo Finance (via yfinance)

- Real-time and historical price data
- Market metrics (P/E, P/B, Market Cap, Beta)
- Analyst estimates and recommendations
- Dividend payment history
- Shareholder information

### SEC EDGAR

- Official financial statements (10-K, 10-Q)
- Complete XBRL data (all US-GAAP tags)
- Company submissions and filings
- Insider transactions (Form 4): each filing's XML for the last 3 years, parsed (see
  [SEC ownership](#sec-ownership-insiders-and-gurus))
- Institutional holdings reports (13F-HR) of 34 gurus, from each filing's full submission text

**Rate Limits:**
- SEC EDGAR: 10 requests/second (we use 0.12s delay)
- Yahoo Finance: Conservative 2.5s delay between tickers
- OpenFIGI: 25 requests/minute without a key (we send 10 CUSIPs every 2.5s)
- Market data (FRED, shillerdata.com, Treasury.gov): one request a second, with requests' own User-Agent (the SEC's is
  never sent), answers cached on disk

### FRED (Federal Reserve Economic Data)

- 10-Year Treasury Constant Maturity Rate (DGS10)
- Used for WACC discount rate calculations
- Corporate equities (`NCBEILQ027S`) and GDP, from the keyless `fredgraph.csv` (no API key), for the Buffett indicator
  (`src/market/sources.py`), asked only by `python -m src.market.collect` (see [Market data](#market-data))

### Robert Shiller's data

- `ie_data.xls` from [shillerdata.com](https://shillerdata.com/): the S&P Composite's monthly price, dividends and
  earnings, the CPI and the CAPE, from 1871 (the CAPE from 1881)
- A legacy Excel workbook, read by `src/market/xls.py`; asked only by `python -m src.market.collect`

### Treasury.gov

- The daily par yield curve, a CSV a year from 1990: every maturity from 1 month to 30 years, in percent as published
- Asked by `python -m src.market.collect` for every year; a collection without a FRED API key also asks the current
  year's file for the 10-year rate

### OpenFIGI

- Maps the CUSIPs of the gurus' 13F holdings to tickers (`src/ownership/openfigi.py`), asked only by
  `python -m src.ownership.collect --gurus`
- No API key: 10 codes a request, answers cached on disk

### Wikipedia

- "List of S&P 500 companies" (its first table), for the S&P 500's members (`src/universe/sp500.py`), asked only by
  `python -m src.universe.sp500`
- One request with a User-Agent that names the app and carries no email address; no key (see
  [The universe and industries](#the-universe-and-industries))

## Valuation Model Support

This data pipeline provides all inputs needed for:

| Model | Required Data | Coverage |
|-------|--------------|----------|
| **DCF** | FCF, growth rates, WACC, risk-free rate | 100% |
| **Graham Number** | EPS, Book Value per Share | 100% |
| **Peter Lynch** | PEG ratio, earnings growth, dividends | 100% |
| **Buffett Analysis** | ROE, margins, debt levels, moat indicators | 100% |

## Valuation layer

Beyond raw inputs, `src/valuation/` computes and stores actual fair-value estimates —
five sector-aware models per ticker, each producing a bear/base/bull per-share range:

| Model | Applies to | Basis |
|---|---|---|
| **DCF** (two-stage FCF) | general, utility, energy | Median 3-year FCF, growth = min(historical CAGR, analyst estimate), CAPM discount rate |
| **DDM** (multi-stage Gordon) | any steady dividend payer | Trailing-12-month dividend, dividend CAGR |
| **Graham Number** | any company with positive EPS + book value | `sqrt(22.5 × EPS × BVPS)` |
| **Peter Lynch fair value** | general, utility, energy | Growth-rate-as-fair-P/E × EPS |
| **Historical multiples band** | all sectors (sector-appropriate multiple: P/E, P/B for banks/insurers, P/FFO for REITs) | Company's own 5-year multiple range |

A model that doesn't fit a company's sector or lacks the data it needs (e.g. DCF/Lynch
for banks and REITs, DDM for non-payers) reports `applicable=false` with a plain-English
`na_reason` instead of a fabricated number. Every computed value is stored alongside the
exact assumptions used (growth, discount rate, basis figures) in the `valuations` table,
with cross-model bear/base/bull medians in `valuation_summary` — so results are
reproducible and auditable, not just a live calculation.

A sixth model, **owner earnings ("Buffett mode")** (`owner_earnings`), follows
Buffett's own stated method rather than textbook DCF, for the same sectors as the
DCF (general, utility, energy):

| Model | Applies to | Basis |
|---|---|---|
| **Owner earnings (Buffett mode)** | general, utility, energy | Median 3-year owner earnings (`net income + D&A − maintenance capex`, maintenance capex = `min(D&A, \|capex\|)`); discounted at `max(10-yr Treasury, 7%)` — no beta |

Two deliberate divergences from the DCF above it:
- **Growth capex is credited back.** Only maintenance capex (capped at D&A) is
  subtracted, so investing to grow isn't penalized as if it were merely standing
  still. Stock-based comp is still subtracted — Buffett treats it as a real cost.
- **No beta.** The discount rate is the 10-year Treasury yield floored at 7%, so
  collapsing yields can't manufacture an absurdly cheap valuation. The margin of
  safety instead lives in the price (`buy_below = intrinsic value x 0.70`);
  only prices below that threshold are called "cheap".

Because a Treasury-discounted value isn't comparable to a CAPM-discounted one,
**`owner_earnings` is excluded from the cross-model median** that drives the
headline verdict, upside %, and screener ranking — it carries its own verdict
instead. This matters on the real data: the median company's CAPM DCF prices it
at 0.68x price vs 1.98x under the Treasury discount — the same cash flows, a
170% swing driven purely by method. Concretely: MSFT's academic DCF is $245.66
vs owner earnings $559.92 (2.28x); NVDA's is $41.86 vs $142.00 (3.39x) — in both
cases the gap is the growth capex being credited back rather than subtracted.
Verified live: adding the model changed zero of the 50 existing summary rows.

It also refuses histories that aren't consistently positive (owner earnings
positive in at least 8 of the last 10 fiscal years) or shorter than 10 years, and
it does not claim to filter cyclicals — it records the volatility evidence (years
positive, worst single-year fall) and leaves the circle-of-competence judgment to
the reader. One honest limitation: GOOGL is not applicable under this model
because Alphabet's older SEC filings don't tag a cash-flow D&A concept, leaving
under 10 years of history — a source-data gap, not a bug, and a little ironic
since Alphabet is exactly the kind of heavy-growth-capex company this model
exists to value properly.

Valuations are recomputed automatically after each collection run and can be backfilled
on demand with `python -m src.valuation.backfill`; see the
[USAGE_GUIDE](USAGE_GUIDE.md#valuation-layer) for the full CLI reference. In the web
interface, the workstation's **VAL** tab shows the full per-model breakdown and a DCF
sensitivity grid, the **DES** tab shows an at-a-glance verdict pill, and the
[screener](#web-interface) adds sortable Upside % and verdict columns/filter.

### Valuation history

Beside the models, `src/valuation/history.py` keeps each company's **valuation history**:
- every month's P/E, P/S, P/B, P/FCF, EV/EBITDA, P/FFO (REITs) and dividend yield since its filings
  begin (2009 for most);
- each multiple's range over the trailing 10 years;
- a **historical value** drawn against the price. It is the transparent counterpart of GuruFocus's GF
  Value line ([design](docs/superpowers/specs/2026-10-21-valuation-history-design.md)).

How it's built:
- **No look-ahead.** Each month-end close is paired with the trailing twelve months *known* that month:
  a quarter 45 days after it ends, a fiscal year on its 10-K's first filing day (from the vintages).
  Quarters from before the listing are left out, and windows are trailing only. The one exception is
  restatement: the quarters are the latest *restated* figures, as SEC's company facts give them, and
  revenue was restated by more than 1% in 8% of the years.
- **Multiples on totals.** Market cap = the close × the spin-off factors after that day × the quarter's
  share count, put on the price bars' split basis. Dividing a price by a filed EPS would cross split
  bases. Zero or negative earnings, book, FCF or EBITDA give no multiple, and the sector rules apply: no
  P/FCF or EV/EBITDA for banks, and P/FFO in place of P/E for REITs.
- **Share counts.** A filed count jumps where the filings stopped restating it for a split, so a jump
  within 1.25× of a declared split folds by that split. A jump of 1.5× or more that no split explains
  folds by its own ratio only on a split's marks: EPS of one sign moved against the count at least
  halfway (in log terms), and revenue didn't move with it as far. A merger's or an exchange offer's
  jump stays (DUK 2012, Citigroup 2009). The quarter of a listing, whose weighted count blends the
  private one, takes the public count after it. The fiscal years behind the six models and the quality scores
  follow the same rules, the EPS-implied count (net income / EPS) having to move with the jump too, and a year
  before the listing takes the public count (BUG-185), though the quality scores leave out the per-share
  figures of a year that ended before the first bar (BUG-188). A spin-off's price factor never explains a
  jump.
- **Ranges.** Each multiple's min, quartiles, median and max, and the current value's percentile, over
  the trailing 10 years. They need 36 months of values, so GEV and SNDK show their multiples without a
  range.
- **The historical value.** Each month, the median, across the sector's multiples, of each multiple's
  trailing 10-year median applied to that month's trailing year; at least two multiples are needed.
  - The multiples: P/E, P/S, P/B, P/FCF and EV/EBITDA; banks P/E, P/S and P/B; insurers add P/FCF; REITs
    P/FFO, P/S, P/B and EV/EBITDA.
  - Its low and high use the quartile multiples.
  - No fitted weights and no adjustment factor: every step is published.
- **Labels**, against the live price: below 0.7× the value is *far below*, up to 0.9× *below*, 0.9-1.1×
  *near*, up to 1.3× *above*, and beyond that *far above*.

It is a separate indicator: it changes none of the six models, their median or the verdicts. It is
computed with the valuations, after a collection, a web job or `python -m src.valuation.backfill`, into
two tables:
- `valuation_history`: one row per month;
- `valuation_ranges`: each metric's range at the latest month, with the historical value as
  `metric = 'fair_value'`.

Where it shows:
- **VAL tab**: a chart of the price against the value and its bands, a chart of any multiple against
  its 10-year low/median/high, and the ranges table.
- **API**: `GET /api/stocks/{ticker}/valuation-history` returns the same.
- **Screener**: filters and sorts by `pe_pct_10y` … `div_yield_pct_10y` (a multiple's percentile in its
  10 years) and by `price_to_hist_value`.

Checked against Yahoo's own figures by `qa/oracle/history_vs_yahoo.py`, which imports nothing from
`src`:
- current P/E within 10% for 110 of 120 companies, P/S for 115 of 122, P/B for 108 of 114;
- share counts within 5% for 118 of 122;
- market cap continuous at all 40 split seams the history crosses, for 38 of the 44 share splits since
  the filed quarters begin. A seam is the jump of about a split's ratio in the filed counts, up to three
  years before the split, and no monthly move matches a nearby split's ratio. C's and TJX's seams sit
  before the history begins, DHR's, FTNT's and SCCO's quarters were all restated, and APH's 2026 split
  came after the latest quarter.

Limitations:
- It's a history of how the market priced a company, not a forecast. A business that changed (a
  spin-off, a new segment) or a short history reads poorly, as GuruFocus warns of its own GF Value.
- It isn't available for share classes that the filings and the quote count differently (BRK-B, V, BX;
  Q7), without trailing years (SPCX), or without a quote with a price to check our share count against.
- Yahoo normalizes EBITDA, so our EV/EBITDA can read apart from Yahoo's (MRK, PFE). A quarter whose 10-Q
  lacks the long-term debt its 10-Ks report has no EV (BUG-171).

### Quality scores

`src/scores/` scores each company's quality beside the models and the valuation history: our counterparts of
GuruFocus's GF Score, its predictability rank and its warning signs, with every formula, cutoff and weight
published ([design](docs/superpowers/specs/2026-10-21-quality-scores-design.md)). It changes none of the six
models, their median or the verdicts, and it grades the company, not the collected data (that is the
[data-quality score](#integrity-checks-data-quality-score)).

Inputs:
- **Two fiscal years.** Each score is on the latest fiscal year (t) against the one before (t-1), from the
  latest restated figures. The latest 11 years are read, so a 10-year CAGR has its start year.
- **Per-share series** (revenue, EPS, EBITDA, FCF and book value per share) on today's split basis. A
  share-count jump of 1.5× or more folds into it only for a declared share split, on a split's marks (EPS
  moved against the count, revenue didn't move with it, the count EPS implies moved with it), or when the
  older year ends before the listing or in its year (a pre-listing count leaves out converted preferred).
  Otherwise a merger, an exchange offer, a count that swings on its own and a spin-off's price factor fold
  nothing (BUG-185). A merger around the listing jumps like the converted preferred, though (DELL's EMC in
  2016), so a year that ended before the first price bar and that only the listing's jump folds has no
  per-share figures: APP, CRWD, DELL, PLTR and UBER lose their pre-listing years, and their CAGRs start
  after them. The listing's own year keeps the public count, as the history's listing quarter does
  (BUG-188).
- **Debt.** A year with no debt tagged and no interest expense is debt-free, outside banks and insurers (ISRG,
  ANET, PANW, PLTR and VRTX tag none): total debt 0, net debt less its cash and short-term investments, and
  ROIC on equity less cash, by `metrics_annual`'s formulas (it leaves all three empty for them). A year that
  paid interest with no debt tagged has debt the mapping doesn't read (PGR files its $8.4B under a combined
  tag), so its debt, net debt and ROIC are unknown, and so are its WACC, earnings yield, cash / debt and debt
  / revenue (BUG-189, BUG-190). A missing interest figure alone proves nothing: many filers with debt tag
  their interest under a name not mapped. `metrics_annual` keeps its figures as filed.
- **CAGRs** over 3, 5 and 10 years (revenue, EPS and EBITDA per share) or 5 (FCF and book value per share):
  `(last / first) ** (1 / years) - 1`, when both ends are positive and the start year is exactly that many
  fiscal years back.

The scores. Each is N/A, with its reason stored, where its inputs are missing:
- **Piotroski F**, 0-9, a point for each test passed: ROA (net income / total assets) above zero; operating
  cash flow (CFO) above zero; ROA up; CFO above net income; long-term debt / total assets down (untested when
  either year's debt is missing); current ratio up; no new shares (the split-normalized count up by no more
  than 0.5%); gross margin up; asset turnover up. A test with missing inputs doesn't run: it scores no point
  and shows as "Not tested" (BUG-186), and the tests that ran are stored ("F out of N tested"). N/A for
  banks and insurers (no current ratio or gross margin).
- **Altman Z**, the original model: `1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA`, with
  WC = current assets - current liabilities, EBIT = operating income and MVE the latest snapshot's market cap.
  Below 1.81 is the distress zone, 1.81-2.99 grey, above 2.99 safe. N/A for banks, insurers, REITs and
  utilities (a regulated utility's heavy assets read as distress: D, DUK, NEE and SO all score below 1.2).
  Z'', Altman's variant for non-manufacturers, needs the SIC code. It is stored since Milestone 5 (see
  [The universe and industries](#the-universe-and-industries)) but this score doesn't read it, so the original
  applies to the rest.
- **Beneish M**, eight variables, each a figure in t over the same figure in t-1 (GMI and DEPI the other way
  round); above -1.78 flags a likely manipulator:
  `M = -4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI`
  - DSRI is receivables / sales; GMI the gross margin; AQI 1 - (current assets + PP&E) / total assets; SGI
    sales; DEPI D&A / (D&A + PP&E); SGAI SG&A / sales; LVGI (current liabilities + long-term debt) / total
    assets. TATA, the exception, is (net income - CFO) / total assets in t.
  - SGAI and DEPI are 1 (neutral) when their inputs are missing, and a missing long-term debt reads as zero in
    LVGI (7 companies: ANET, DE, GEV, ISRG, PANW, PLTR, VRTX). Any other missing variable makes M N/A, and the
    reason names it. Banks, insurers and REITs are N/A too.
- **Sloan ratio**: `(net income - CFO - investing cash flow) / total assets`. Within ±10% is safe, 10-25% a
  warning, and beyond ±25% flagged.
- **ROIC vs WACC**: the stored ROIC less `WACC = E/(D+E)·Re + D/(D+E)·Rd·(1-t)`, N/A for banks and insurers. E
  is the market cap and D the total debt. Re is the DCF's CAPM cost of equity, the risk-free rate + beta ×
  4.5%, held between 8% and 14%. Rd = interest expense / D, clamped between the risk-free rate and 10 points
  above it (the risk-free rate with no debt or no interest figure). t = income tax / pretax income, clamped to
  0-35%, and 21% where pretax income is missing or not positive. Where ROIC is missing, the row says why: no
  operating income, invested capital (equity + debt - cash) of 0 or less, or unknown debt; banks, insurers
  and REITs have none.
- **Predictability**, 1-5 stars in half steps, from the last 10 fiscal years (fewer is N/A): our own formula,
  as GuruFocus's is unpublished. A year with revenue or EBITDA per share at or below zero, or a falling trend
  (a log-linear slope of zero or less in either series), is 1 star. Otherwise
  `stars = 1 + 4 × min(R²_revenue, R²_EBITDA)`, the R² of ln(revenue per share) and ln(EBITDA per share)
  against the year, rounded down to the half star. Banks and insurers use EPS where EBITDA is suppressed.

**Warning signs**: twenty published rules. Each that fires is stored with its severity and a message stating
the figure, the value and the threshold; severe signs come first.

| Code | Severity | Fires when |
|---|---|---|
| `altman_distress` | severe | Altman Z below 1.81 |
| `beneish_flag` | severe | Beneish M above -1.78 |
| `sloan_high` | severe / medium | Sloan ratio beyond ±25% / beyond ±10% |
| `piotroski_low` | medium | F of 3 or less, with at least 7 tests run |
| `interest_coverage_low` | severe / medium | operating income covers interest less than 1.5 times / less than 3 times |
| `revenue_decline` | medium | revenue fell in each of the last 3 years |
| `margin_decline` | medium | operating margin fell by more than 5 points over 5 years |
| `fcf_negative` | medium | free cash flow below zero in 2 of the last 3 years |
| `dilution` | medium | the split-normalized share count grew by more than 3% a year over 3 years |
| `debt_outgrows_revenue` | medium | total-debt CAGR above the revenue CAGR by more than 10 points over 3 years |
| `receivables_outgrow_revenue` | medium | receivables per dollar of revenue up more than 20% over 3 years |
| `inventory_outgrows_revenue` | medium | inventory per dollar of revenue up more than 20% over 3 years |
| `assets_outgrow_revenue` | medium | total-asset CAGR above the revenue CAGR by more than 10 points over 3 years |
| `goodwill_heavy` | medium | goodwill above 50% of total equity (equity above zero) |
| `negative_equity` | medium | total equity below zero |
| `payout_over_fcf` | medium | dividends paid above zero and above free cash flow (which may be negative) |
| `roic_below_wacc` | medium | ROIC below WACC |
| `price_far_above_value` | medium | price / historical value above 1.3 (the history's *far above*) |
| `pe_at_high` | medium | P/E at or above its 95th percentile of the last 10 years |
| `gross_margin_decline` | medium | gross margin fell by more than 5 points over 5 years |

**Ranks**: five, each 1-10 (10 best), computed across the universe after every company's scores are stored, so
any run re-ranks everyone.

| Rank | Components |
|---|---|
| Financial strength | interest coverage (capped at 100), Altman Z, equity / assets, cash / debt (capped at 10), debt / revenue (lower is better) |
| Profitability | operating margin, its 10-year mean and 5-year slope, the share of Piotroski tests passed, predictability stars, 10-year median ROIC, profitable years out of 10 |
| Growth | revenue per share CAGR over 3 and 5 years, EBITDA per share CAGR over 5 years (EPS for banks and insurers) |
| Valuation | price / historical value (lower is better), else the P/E's percentile in its 10 years (lower is better) |
| Momentum | the mean of the 12-1 and 6-1 month returns (the close a month back against 12 and 6 months back), over max(beta, 0.5) |

- Each component becomes a percentile in (0, 1]: the share of its group at or below it (at or above, where
  lower is better). The group is the company's sector class when at least 10 companies have that component,
  else the whole universe. A rank is `ceil(10 × mean)` of its components' percentiles, given at least two
  components (financial strength, profitability, growth) or its one (valuation, momentum).
- The **composite**, out of 100, is `10 × the mean of the five ranks`, with at least four available. The
  weights are equal by default: GuruFocus weighs profitability and growth most, with unpublished weights, and
  choosing ours is left to the user (Q11 in `qa/BUGS.md`).
- Milestone 5 ranks each company again within its SIC industry group, beside these and leaving them as they are (see
  [Industry ranks](#industry-ranks)).
- The **Magic Formula rank** (Greenblatt) sums the ranks of the earnings yield (EBIT / EV, EV = market cap +
  net debt) and the return on capital (EBIT / (net working capital + net PP&E), net working capital = current
  assets - cash - (current liabilities - short-term debt), counted as 0 when negative: a business its
  customers fund, as MA's, V's or BKNG's, ties up only its PP&E). It ranks the companies with both, 61 of the
  94 outside banks, insurers, utilities and REITs; 1 is best, and a tie goes to the higher earnings yield. The
  STAT tab says why the others have no rank (BUG-191): 17 file no `PropertyPlantAndEquipmentNet` (UNP, HD and
  TSLA tag a lease-inclusive PP&E instead; META, GOOGL, LOW and T are among them) and 15 no operating income
  (JNJ, LLY, MRK, PFE, XOM, CVX, IBM, GE, DE, KLAC and others), gaps in the tag mapping open as BUG-203.

It is computed with the valuations, after a collection, a web job or `python -m src.valuation.backfill`, into
three tables (columns and queries are in the [USAGE_GUIDE](USAGE_GUIDE.md#quality-scores)):
- `per_share_annual`: one row per company and fiscal year, the five per-share series;
- `quality_scores`: one row per company, with every score and its inputs, the CAGRs, the ranks, the composite,
  the Magic Formula rank, NCAV per share (current assets less total liabilities, over the share count),
  `na_reasons` and the components behind each rank (JSON), and the industry group and ranks of Milestone 5 (see
  [Industry ranks](#industry-ranks));
- `warning_signs`: one row per sign that fires.

Where it shows:
- **STAT tab**: a Quality section with the composite and a radar of the five ranks, the ranks, each score with
  its reading, the nine Piotroski tests, the growth table (the CAGRs) and the warning signs, severe first.
- **API**: `GET /api/stocks/{ticker}/scores` returns the same as JSON, with the Beneish variables, the
  predictability fit's R² and the per-share series. It answers 404 for an unknown ticker and
  `"computed": false` before its scores exist.
- **Screener**: filters and sorts by `piotroski_f`, `altman_z`, `beneish_m`, `sloan_ratio`,
  `predictability_stars`, `rev_ps_cagr_5y`, `eps_cagr_5y`, `roic_minus_wacc`, the five `rank_*`,
  `composite_score`, `momentum`, `magic_formula_rank` and `warning_count`, by `price_to_ncav` (price / NCAV
  per share, where NCAV is positive), by `scores_debt_to_ebitda` (debt / EBITDA on the scores' reading of the
  debt: 0 for a debt-free filer, which `metrics_annual` leaves empty) and by `lynch_upside_pct` (the Lynch
  model's base value over the price).
  A *Preset screens* dropdown fills the filters:
  - Buffett-Munger: predictability ≥ 4, ROIC - WACC > 0, the scores' debt / EBITDA ≤ 3, price / historical
    value ≤ 1.1;
  - Peter Lynch: the Lynch model's base value above the price, most upside first;
  - Graham net-net: price / NCAV per share ≤ 0.67, lowest first;
  - Magic Formula: ranked, best first (banks, insurers, utilities and REITs have no rank, nor have the
    companies missing an input, each with its reason in the STAT tab);
  - Undervalued-predictable: predictability ≥ 4 and price / historical value < 0.9, lowest first.

Checked by `qa/oracle/scores_oracle.py`, which imports nothing from `src` and recomputes from the raw tables,
with its own split basis (each year's count from its latest filing, times the declared splits after it). All 9
gates pass on the 127 companies (`qa/evidence/scores-m2-5-oracle.txt`): the fiscal year scored (127/127), the
per-share series within 2% (6,630/6,630 values), the CAGRs within 0.5 points (1,367/1,367), each Piotroski
test and F (1,397/1,397), Altman Z and its zone within 0.5% (127/127), Beneish M and its variables within 0.5%
(1,143/1,143), the Sloan ratio within 0.1 points (127/127), predictability stars exactly (321/321), and the
ranks, composite and Magic Formula rank recomputed from the stored components (889/889). Years ending before a
company's first price bar plus a year (160 per-share values, 30 CAGRs) aren't compared: their basis is an
approximation the filings don't state.

The oracle's first run found BUG-185: the fiscal years folded mergers, spin-off price factors and a swinging
count as if they were splits (RTX, DELL, LIN, BX; BX's 10-year EPS CAGR read 1.51% against 14.04%). The 14
severe warning signs (5 Altman, 5 Beneish, 4 Sloan) were each traced to their raw figures: none is a data error.

Limitations:
- The original Altman model, fitted on manufacturers, reads capital-heavy telecoms as distress: T (0.85), VZ
  (1.26) and TMUS (1.45). It is N/A for 49 of the 127 companies and Beneish M for 75: the sectors above, and
  any company missing an input.
- Beneish flags hypergrowth (its sales-growth variable) and one-off gains: LRCX, MRVL, NVDA and SNDK grew
  sales 26%, 42%, 65% and 175%, and WDC's net income of $9.4B came with $3.9B of operating cash flow.
- The cash-flow Sloan ratio counts purchases of marketable securities as accruals (PLTR, MU, NVDA). GuruFocus
  publishes the same definition.
- Years before a listing are on an approximate share basis: nothing in the filings says what converted then.
- Ranks are percentiles within a 127-company universe, so they move when it changes. A sector class is ranked
  within itself only with at least 10 companies, which only general (90) and banks (13) have.
- The composite's equal weights are a default, not a finding.

### Other valuation indicators

`src/valuation/indicators.py` adds five more values beside the models, the valuation history and the quality scores: our
counterparts of the row of intrinsic values GuruFocus shows next to its GF Value, with every formula published
([design](docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md)). They are display-only, like owner
earnings: none enters `MEDIAN_MODELS`, the cross-model median, the verdicts or the upside, and the `valuations` table is
untouched (a test pins both). Each has a base value, a bear and a bull where its method gives them, an N/A reason where
it doesn't apply, and its inputs in `assumptions`.

They read what the models and the scores read: the DCF's own inputs, the quality scores' annual rows with their debt
reading, the latest snapshot with a price (a run whose quote failed stores a snapshot without one), the valuation
history's P/S range and each fiscal year's goodwill and intangible assets. A foreign issuer whose ADS ratio can't be read
has every indicator but the P/S value N/A, as the models' per-share values are (none of the 127 companies).

The indicators:
- **Reverse DCF** (`reverse_dcf`): the growth the price implies. It solves the DCF for the growth at which it gives the
  price, on the DCF's own inputs: its FCF basis (the median free cash flow of the last 3 fiscal years), share count, base
  discount rate and terminal growth (2.5%). The DCF is the same 10-year two-stage one: years 1-5 grow at the solved
  growth, years 6-10 fade linearly to the terminal growth, and a Gordon terminal value follows. The search is a bisection
  over -50% to +100% growth. The base is that growth, a fraction (0.265 is 26.5% a year); a rate has no bear, bull or
  upside. It is stored at the price of the latest snapshot with a price, which is the screener's figure, and the VAL tab
  and the API solve it again at the live price from the DCF inputs the row stores, beside the DCF's own growth. N/A
  without an applicable DCF (the reason follows "no DCF:"), without a price, or when no growth in the range reaches the
  price (the reason says whether the price is below the DCF's value at -50% growth or above its value at +100%).
- **Earnings power value** (`epv`), Greenwald's no-growth value, what the business is worth if it never grows:
  - normalized EBIT = the mean operating margin of the last 5 fiscal years by their labels (at least 3 must have one; a
    missing year narrows the window rather than reaching further back) × the latest revenue. A year without operating
    income takes pretax income plus interest expense, GuruFocus's EBIT (an untagged interest counts as 0, as
    `metrics_annual`'s EBITDA takes it), and those years are listed. Thirteen large caps file no operating income in any
    of their last five years (BMY, COP, CVX, ETN, GE, IBM, JNJ, KLAC, LLY, MRK, PFE, TJX, XOM; BUG-203) but do file
    pretax income;
  - adjusted earnings = normalized EBIT × (1 - t), t the latest year's effective tax rate as the quality scores' WACC
    takes it (income tax / pretax income, clamped to 0-35%, and 21% where pretax income is missing or not positive).
    Maintenance capex is taken as equal to D&A, so neither is added back;
  - the firm's EPV = adjusted earnings / the quality scores' WACC; the equity's = that less total debt plus cash and
    short-term investments. Debt is the scores' reading: a debt-free filer's is 0, and a filer that paid interest with no
    debt tagged has unknown debt, which makes EPV N/A;
  - per share over today's count (the valuation inputs' share count, the one the DCF divides by; after a failed quote,
    like the DCF, the latest fiscal year's count). The bear and bull are at the WACC plus and minus 1 point (no bull
    at a rate of 0 or less). A value below 0, debt above the earnings power (D, INTC), is stored as it is;
  - N/A for banks, insurers and REITs (no operating EBIT), without annual financials, a latest revenue, a WACC (no
    market cap) or a share count, with unknown debt, with fewer than 3 years of margins, or when the adjusted earnings
    are not positive (BA, CRWD, GEV, MRVL, UBER).
- **NCAV and the Graham net-net** (`ncav`): NCAV per share, current assets less total liabilities over the latest fiscal
  year's share count (the quality scores' `ncav_per_share`), is the base. The bear is Graham's stricter net-net working
  capital per share when it is positive: cash and short-term investments + 0.75 × receivables + 0.5 × inventory - total
  liabilities, over the same count (a missing part counts as 0 and is listed). Graham's buy line, two thirds of NCAV, is
  in the assumptions and on the VAL tab. N/A when NCAV isn't positive, as it isn't for most large caps (83 of the 98
  companies with the figures; the row still carries NCAV per share, to say how far below zero it is), for banks and
  insurers (no current assets), and where current assets, total liabilities or the share count is missing.
- **Median P/S value** (`ps_value`): the valuation history's P/S row, its implied value (the 10-year median P/S × the
  trailing revenue per share), is the base; the bear and bull scale it by the range's 25th and 75th percentiles
  (`implied value × p25 / median` and `× p75 / median`). N/A where the P/S range is (under 36 months of history, no
  trailing revenue, ...), with the range's reason.
- **Graham number on tangible book** (`graham_tangible`): `sqrt(22.5 × EPS × tangible book per share)`, tangible book
  being total equity less goodwill and intangible assets, on the Graham model's EPS and share basis; the bear and bull
  use the lowest and highest positive EPS of the last 3 fiscal years, as the Graham model does. N/A where the Graham
  model is, where equity isn't positive, or where tangible book isn't (33 of the 127: their goodwill and intangibles
  exceed their equity). A missing goodwill or intangibles figure counts as 0 and is listed.

An indicator that raises an error becomes an N/A row (`internal error: ...`) and the others still run. On the 127
companies the reverse DCF applies to 91, EPV to 93, NCAV to 15, the median P/S value to 120 and the tangible Graham
number to 81.

It is computed after the scores it reads, by every collection (a terminal run or a web job) and by
`python -m src.valuation.backfill` (offline), each ticker in its own try/except so a failure never blocks the
valuations, the history or the scores. One table holds it (columns and queries are in the
[USAGE_GUIDE](USAGE_GUIDE.md#other-valuation-indicators)):
- `valuation_indicators`: one row per company and indicator (`reverse_dcf`, `epv`, `ncav`, `ps_value`,
  `graham_tangible`), replaced per ticker in one transaction: `applicable`, `na_reason`, `value_bear`, `value_base`,
  `value_bull`, `unit` (`per_share`, or `rate` for the reverse DCF's growth), `assumptions` (JSON),
  `basis_fiscal_year` and `computed_at`.

Where it shows:
- **VAL tab**: an *Other indicators* section after the models and owner earnings: per indicator its bear, base and bull,
  the base's upside from the live price, its method in one line, notes read from its assumptions (the years whose EBIT
  is pretax income plus interest, Graham's buy line, the median P/S with its quartiles, ...) and its assumptions, or its
  N/A reason. The reverse DCF is solved again at the live price, beside the growth the DCF itself assumes.
- **API**: `GET /api/stocks/{ticker}/valuation-indicators` returns the same as JSON, with the reverse DCF solved again at
  the live price. It answers 404 for an unknown ticker and `"computed": false` before the indicators exist. (Not
  `/indicators`: that is the price chart's MA/RSI/MACD bundle.)
- **Screener**: filters and sorts by five fields, empty for a database from before the table:
  - `reverse_dcf_growth`: the growth the price implies, a fraction, as stored (at the price of the snapshot it was
    computed at); the results show it as an *Implied growth* column;
  - `epv_upside_pct`, `ps_value_upside_pct` and `graham_tangible_upside_pct`: the indicator's base over the snapshot's
    price, less 1;
  - `price_to_nnwc`: the price over Graham's net-net working capital per share (the NCAV row's bear), where that is
    positive; `price_to_ncav` is the price over NCAV itself.

Checked by `qa/oracle/indicators_oracle.py`, which imports nothing from `src` and recomputes the five indicators from the
raw tables, taking the inputs the earlier oracles validated (the DCF and Graham rows' inputs, the P/S range, the
per-share series) as stored. All 12 gates pass on the 127 companies (`qa/evidence/oracle-m4-5-indicators.txt`), each
indicator being N/A on the same companies on both sides (127 of 127):
- the reverse DCF, against the oracle's own two-stage DCF and root finder, within 0.05 percentage points (91/91; the
  largest gap 4.5e-6 points);
- EPV's EBIT, tax rate, adjusted earnings, WACC, debt, cash and its bear, base and bull within 0.5% (1,048/1,048
  figures), with the years whose EBIT is pretax income plus interest equal;
- NCAV and the net-net within 0.5% (241/241 figures);
- the median P/S value exact to 1e-9 (360/360 figures) and the Graham number on tangible book within 0.5% (357/357).

A copy of the database with altered rows makes the oracle fail, each failure naming the altered row. The valuations are
untouched: every table the models, the history and the scores write is identical before and after the backfill
(timestamps aside).

Limitations:
- V files its EPS and share counts only by class, so none is stored (BUG-223): its NCAV, tangible Graham number and
  median P/S value are N/A. Its EPV and reverse DCF use the snapshot's share count.
- An intangible the filing doesn't tag stays in tangible book: a filer that tags no intangibles (KO's trademarks) has
  all of them left in, and one that tags only its finite-lived ones has its indefinite-lived ones left in. 23 of the 127
  have a goodwill or intangibles figure missing, counted as 0 and listed in the row's assumptions.
- Short-term investments some filers tag under names the mapping doesn't read are missing from EPV's cash and the
  net-net's: NVDA's FY2026 stores none after $34.6B in FY2025, so its EPV and net-net read low (BUG-221). NCAV itself
  reads current assets and is unaffected.
- EPV is a no-growth value on a 5-year mean margin, so a company priced for growth reads far under its price: on
  2026-10-24 only VZ traded under its EPV, and AMD, PANW, PLTR and SNDK read under 5% of theirs. A cyclical whose margin
  is far from its 5-year mean reads high or low accordingly.
- The reverse DCF is only as good as the DCF's inputs: it solves for growth on the same median 3-year free cash flow and
  CAPM discount rate, so a depressed or inflated recent cash flow shows up as a high or low implied growth. The
  screener's `reverse_dcf_growth` is the stored one, at the snapshot price of the last computation; the VAL tab and the
  API re-solve it at the live price, so the two differ until the next collection or backfill.

## SEC ownership: insiders and gurus

`src/ownership/` reads two kinds of SEC filings into the database: what a company's **insiders** buy and sell
(Form 4) and what a list of **gurus**, value investors and other managers, hold (13F-HR). They are our
counterparts of GuruFocus's insider trades and guru holdings
([design](docs/superpowers/specs/2026-10-22-sec-ownership-design.md)). Both come from the filings themselves
(the gurus' CUSIPs are mapped to tickers with OpenFIGI), and nothing collected before changes: not a model, a
score or a history. It has its own command, `python -m src.ownership.collect` (see
[Collecting ownership data](#collecting-ownership-data)); a `stock-data` collection and the web interface's update
don't run it, but `stock-data refresh` does every week (see
[Keeping the data current](#keeping-the-data-current-stock-data-refresh)).

### Insiders (Form 4)

Collected:
- Every Form 4 and 4/A of our companies filed in the last 3 years (`--years`). Each company's submissions feed
  at the SEC lists them (its `recent` block and the older pages that reach back that far), then each filing's
  raw XML is fetched (the primary document without its `xslF345X0n/` rendering folder).
- Each XML is cached once it parses (`data/cache/form4/{accession}.xml`), so a later run fetches only the new
  filings. A document that isn't a readable ownership document is skipped and counted as an error, never
  guessed or stored as empty, and what an earlier run stored stays.
- Only the company's own filings: a feed also lists the Form 4s the company filed as an owner of another
  issuer's shares (PFE's of Cerevel, Berkshire's of OXY or Bank of America), which aren't its insiders' trades
  and are left out, counted as "other issuer". A company that succeeded another (`PREDECESSOR_CIKS`: XOM from
  Exxon Mobil Corp in 2026, BLK from the old BlackRock in 2024) also reads its predecessor's own Form 4s filed
  before its first.

Parsed (the SEC's `ownershipDocument`):
- the reporting owners (CIK, name) and their roles (director, officer and title, 10% owner, other), the period
  of report, the 10b5-1 flag (`aff10b5One`, written 1/0 or true/false) and, for a 4/A, the day of the
  original;
- each **non-derivative** transaction: security, date, transaction code, shares, price, acquired or disposed,
  shares owned after, and direct or indirect ownership;
- each **derivative** transaction too (grants, exercises), with its exercise price and underlying security;
- only what the filing states: a price given as a footnote alone (an RSU's) stays empty with the footnote's id
  kept, shares owned after are never derived, and holding rows that record no transaction aren't kept.

Amendments: a 4/A amends the same owner's filing made on the day the 4/A names as its original's (a Form 4 or
an earlier 4/A; the period of report only chooses between several) and replaces only the trades it restates:
one of the same table, code and security on the same date or with the same shares and price, or one with the
same date, shares and price under another code (a code correction), matched one for one. The amended filing's
other trades stand, as a 4/A often adds a trade its original omitted (both of PFE's do). A restated trade stays,
marked `superseded_by` the 4/A's accession in `insider_trades`, and the aggregates, the INS tab and the
screener leave it out; `insider_filings.superseded_by` notes the amending 4/A. A filing with several reporting
owners has a row for each in `insider_filings` and its trades once in `insider_trades`.

The aggregates, computed at read time from the trades no amendment restated:
- **Open-market buying and selling** over 3, 6 and 12 months (91, 182 and 365 days back from today): the
  purchases (code P) and sales (code S) of non-derivative securities, in shares and dollars (`shares × price`;
  a trade without a price counts in shares only, as does one whose price is a filer's dropped decimal point:
  1,000 times, or a thousandth of, a price within 10% of the stock's close that day, kept as filed and flagged
  `price_suspect`, like the Lilly Endowment's $1,031,414 for $1,031.41; BUG-220), the net (`bought - sold`, in
  dollars) and the distinct
  buyers and sellers. Grants (A), exercises (M), tax withholding (F), gifts (G) and the other codes are stored
  and shown with their meaning, never summed.
- **CEO and CFO buys**: a purchase in the last 12 months by an officer whose title names the chief executive
  or financial officer ("CEO", "CFO", "Chief Executive Officer" or "Chief Financial Officer", in any case). A
  comma, "of" or a dash right after the name makes it a unit's head ("EVP, Pres. & CEO, BGS", "CEO of
  Payments", "CEO - Americas"), which doesn't count. The flag is stored per filing
  (`insider_filings.is_ceo_cfo`).
- **Cluster buys**: 3 or more distinct insiders buying (code P) within 30 days of the first of them, over the
  last 12 months. A purchase belongs to one cluster at most.
- **Sales under a 10b5-1 plan**: of the shares sold in the last 12 months in filings that state the flag, the
  share sold under a plan; empty where no filing states it.

Where it shows:
- **INS tab**: the last 12 months' open-market buying and selling, the net buying over 3, 6 and 12 months, the
  CEO/CFO buys and the share of sales under a plan, a line for each cluster buy and CEO/CFO buy, then the
  trades newest first (the latest 200): date, insider, role, the code with its meaning, shares, price, value,
  holding after and plan. A company with no Form 4 stored keeps Yahoo's rows, as before.
- **API**: `GET /api/stocks/{ticker}/form4` returns `source` (`sec`, `yahoo` with Yahoo's rows where no Form 4
  is stored, or null for neither), the aggregates in `summary` and the `trades`.
  `GET /api/stocks/{ticker}/insiders` still serves Yahoo's rows.
- **Screener**: filters and sorts by `ins_net_buy_12m` (open-market dollars bought less sold),
  `ins_buyers_12m` (insiders buying), `ins_ceo_cfo_buys_12m` (filings with a CEO/CFO purchase) and
  `ins_cluster_buyers_12m` (the most insiders buying within 30 days of one purchase: 3 or more is a cluster),
  each over the last 12 months.

Checked on PFE and AAPL, collected into a scratch copy (452 filings in 107 seconds, 0 errors). On 2026-10-23,
PFE's 3 buying insiders had bought $2.96M in the last 12 months, all in August 2026 (a cluster that includes
the CEO's $1.00M), its two amendments each added a trade their original omitted, and its Form 4 as Cerevel's
owner is left out; AAPL's 8 selling insiders had sold
$207M, 56% of the shares under 10b5-1 plans. Of Yahoo's rows for the same months, 26 of PFE's 34 and 80 of
AAPL's 100 match an SEC trade on the same day with shares within 1%; the rest are kinds Yahoo labels or sums
differently (option conversions, multi-day sale ranges, rows without text).

### Gurus (13F)

The gurus are the 34 in `src/ownership/gurus.py`, value investors and the other managers GuruFocus follows:
Berkshire Hathaway (Warren Buffett), Baupost (Seth Klarman), Pershing Square (Bill Ackman), DME Capital (David
Einhorn), Oaktree (Howard Marks), Markel (Tom Gayner), Duquesne (Stanley Druckenmiller), Scion (Michael
Burry), Third Point (Dan Loeb), Appaloosa (David Tepper), Himalaya (Li Lu), Dalal Street (Mohnish Pabrai),
Fundsmith (Terry Smith), TCI, Dodge & Cox, First Eagle, the Gates Foundation Trust and others.
- A guru is its current 13F filer's CIK, and may have earlier filers of the same portfolio, whose reports are
  summed into one quarter. Pershing Square Inc. reports Ackman's portfolio from Q2 2026, when Pershing Square
  Capital Management's own filing became a 13F-NT notice pointing to it (before, the two reported different
  holdings). DME Capital Management has filed Einhorn's since Greenlight Capital's last 13F, for Q4 2023.
- The list is the user's to change: a tuple of `Guru(cik, name, manager, former)` in `src/ownership/gurus.py`.
  Each CIK was checked against its submissions feed on 2026-10-22
  (`qa/evidence/ownership-m3-4-gurus-check.txt`). After adding or dropping a filer, run `--gurus` again; a
  guru dropped from the list keeps the rows already stored.

Fetched:
- Each filer's 13F-HR and 13F-HR/A whose period of report falls in the last 8 quarters before the current one
  (`--quarters`): on 2026-10-22, the periods from 2024-10-01. That is 7 quarters, Q4 2024 to Q2 2026, until
  the Q3 2026 reports are filed (up to 45 days after the quarter ends). A 13F-NT notice holds no holdings and
  is skipped.
- Each filing's full submission text (`{accession}.txt`), which carries the cover and the INFORMATION TABLE
  each under its `<TYPE>`: one request per filing, cached once it parses (`data/cache/13f/`). On 2026-10-22:
  248 filings and 20,538 entries for the 34 gurus.

Parsed:
- each entry's issuer, class, CUSIP, value, shares or principal amount (`SH` or `PRN`), put/call and
  investment discretion, and the cover's period, form, report type, amendment type and totals. Tags are
  matched on their local names, as filers' namespaces vary.
- **Units**: values are in thousands of dollars in filings made before 2023-01-03 and in dollars from that day
  (the SEC's rule goes by the filing date, not the period), and are stored in dollars. The holdings' prices
  overrule the date where it gives impossible ones: when the median price of a filing's share rows (value /
  shares) falls outside $1 to $100,000 under the date's units and inside them at 1,000 times more or less, the
  filing states the other units. Baupost and Duquesne still state thousands after the switch (14 filings in
  the window). `guru_filings.multiplier` keeps the units used (1000 or 1). A pre-2023 filing stated in dollars
  is caught only when its median price is above $100; none falls in the window.
- **Checksum**: the cover's `tableEntryTotal` must equal the entries parsed, and its `tableValueTotal` their
  sum within rounding. Each entry's value and the total are rounded to the unit, so they may differ by half a
  unit per entry plus half a unit: 13 of the 248 filings differ, by 1 to 4 units. A filing that fails is
  stored with `checksum_ok = 0` and kept out of the guru's quarters (none did).

The CUSIP map (`cusip_map`) turns each holding's CUSIP into a ticker:
- Our companies' codes are curated in `src/ownership/cusips.py`: 124 codes for 121 tickers (source
  `universe`). Each is the OpenFIGI listing (the US composite) of a code in the gurus' filings, kept where its
  ticker is ours and read against the filing's issuer name (`qa/evidence/ownership-m3-5-universe-review.txt`).
  Added by hand: the other common class (GOOG beside GOOGL, BRK-A beside BRK-B), the codes that changed in the
  window (Honeywell's before its 2026 reverse split, Exxon Mobil Corp's before ExxonMobil Holdings took XOM)
  and a filer's typo of Allstate's. A company's preferred shares and convertible notes keep their own OpenFIGI
  tickers.
- Every other code goes to OpenFIGI's mapping API (source `openfigi`) without a key: 10 codes a request, 25
  requests a minute, each answer cached on disk (`data/cache/openfigi/{cusip}.json`). A code that starts with
  a letter is a CINS, a foreign issuer's (Chubb, Linde, Seagate), asked as `ID_CINS`: as `ID_CUSIP` OpenFIGI
  finds nothing. "No identifier found" and "Invalid idValue format" (a malformed code: a filer's bad check
  digit) are answers, stored as source `none`. Another error fails only its own code, which the next run asks
  again, and three requests failing in a row stop the mapping until the next run. A `none` code has no ticker
  and keeps its issuer's name from the filing.
- On 2026-10-22, 1,941 codes: 124 `universe`, 1,796 `openfigi` and 21 `none`.

A guru's quarter (`src/ownership/portfolio.py`) is built from the filings that pass the checksum:
- per filer and period, the latest original 13F-HR or RESTATEMENT amendment (a 13F-HR/A with no amendment type
  counts as one), plus the NEW HOLDINGS amendments filed after it. A filer with no usable filing leaves the
  quarter incomplete (`guru_quarters.complete = 0`) and its rows out;
- a NEW HOLDINGS amendment's rows that repeat its base's (the same CUSIP, put/call, shares and value), matched
  one for one, are left out: First Eagle's Q2 2026 amendment repeats all 614 rows of its 13F-HR and adds 2, so
  counting it whole would read the quarter as $120.7B, not $60.75B, while Berkshire's Q1 2025 one adds 202
  Lennar class B shares to a code the base holds;
- the guru's filers summed (Pershing Square Inc. with Pershing Square Capital Management);
- positions keyed by security and put/call: the security is the mapped ticker, else the CUSIP, so a code that
  changed doesn't read as a sale and a purchase, and options (puts and calls) stay apart from shares. The rows
  of one key are summed (one CUSIP may take several rows: other managers, discretion), and a position's weight
  is its value over the quarter's total.

The changes compare a quarter with the one before it only (a guru's first quarter in the window, or one after
a gap, has none): each position is **new**, **added**, **reduced**, **sold out** or **unchanged**, by shares.
The earlier quarter's shares are first adjusted for the share splits of our tickers dated between the two
period ends (simple ratios only: Yahoo's spin-off price factors change no holder's count, and stock dividends
such as SCCO's 1% read as small additions); other securities have no split data. The change is
`(shares - adjusted shares before) / adjusted shares before`. Dodge & Cox's BKNG, 997,498 shares before the
25:1 split of 2026-04-06 and 25,536,950 after, reads +2.4%, not +2,460%.

The quarters are materialized when collecting, as the holders tab, the aggregated portfolio and the screener
all need every guru's latest quarter, too slow to rebuild per request. After mapping, `--gurus` stores each
guru's quarters (`guru_quarters`: the total value, the positions held, whether it is complete, whether the
quarter before is there, the filings it stands on) and positions with their changes (`guru_positions`: one row
per security and put/call, put/call `''` for shares and principal amounts, and a `sold_out` row for each
position of the quarter before that's gone); `--positions` rebuilds them offline. Each guru's rows are
replaced in one transaction.

Where it shows:
- **Pages**: `/gurus` lists the gurus, each at its latest quarter: the value, the positions held in shares or
  principal amounts, and of them the new and the sold-out ones. `/gurus/{cik}` (`?period=` for an earlier
  quarter) shows a guru's holdings with weights and changes, the changes grouped, and the filings the quarter
  stands on, linked to EDGAR. `/gurus/portfolio` aggregates the gurus: per security, the gurus holding it,
  their summed shares and value, and the buyers (new or added) and sellers (reduced or sold out) of the latest
  quarter. The nav has a Gurus link.
- **HDS tab**: a Gurus section above Yahoo's holders: each guru's latest quarter, shown on each row (Scion's
  stops at Q3 2025), with its position in the stock or its other share class (GOOGL with GOOG, BRK-B with
  BRK-A) and the change from the quarter before, then the gurus that sold it out that quarter. Hidden until a
  quarter is stored.
- **API**: `GET /api/stocks/{ticker}/gurus`, `GET /api/gurus`, `GET /api/gurus/portfolio` and
  `GET /api/gurus/{cik}` return the same.
- **Screener**: filters and sorts by `gurus_holding` (the gurus holding the stock in their latest quarter) and
  `gurus_buying` (of those, the ones whose position is new or added), each guru counted once over the stock's
  share classes. The results show them as one "Gurus (buying)" column.

Checked by `qa/oracle/thirteenf_oracle.py`, which imports nothing from `src` and re-reads every cached filing
with plain patterns. On the first collection (2026-10-22, a scratch copy) all its gates pass on the 248
filings (each one's form, date and period, the units, the cover's totals, the entries' count, CUSIPs and
values, and `checksum_ok`). Each of the 235 quarters equals the totals of the filings it stands on, less the
rows a NEW HOLDINGS amendment repeats (First Eagle's Q2 2026: $60.75B, where its two covers add to
$120.67B), none is incomplete, and the stored quarters and positions equal what `load_guru` builds from the
filings for all 34 gurus.

### Collecting ownership data

```bash
# Insiders: three years of Form 4s for every company in the database
python -m src.ownership.collect --insiders

# Some tickers only, with another look-back (in years)
python -m src.ownership.collect --insiders AAPL PFE --years 1

# Gurus: the last 8 quarters' 13F-HRs, the CUSIP map, then the quarters and positions
python -m src.ownership.collect --gurus

# The same, asking OpenFIGI nothing (only our companies' CUSIPs are mapped)
python -m src.ownership.collect --gurus --no-openfigi

# Rebuild the gurus' quarters and positions from what is stored (offline)
python -m src.ownership.collect --positions
```

| Option | Description | Default |
|--------|-------------|---------|
| `tickers` | With `--insiders`: the tickers whose insiders to collect | every company with a CIK |
| `--insiders` | Collect insiders' Form 4 filings | `false` |
| `--gurus` | Collect the gurus' 13F holdings, map their CUSIPs, then store their quarters and positions | `false` |
| `--positions` | Rebuild the gurus' quarters and positions from the stored filings, offline | `false` |
| `--years` | How far back for insiders, in years | `3` |
| `--quarters` | How many quarters before the current one for gurus | `8` |
| `--no-openfigi` | With `--gurus`: map only our companies' CUSIPs, asking OpenFIGI nothing | `false` |
| `--db` | SQLite database path | `data/output/stock.db` |
| `--cache-dir` | The SEC cache (OpenFIGI's answers go under `openfigi/`) | `data/cache` |

At least one of `--insiders`, `--gurus` and `--positions` is required. A run is incremental: the caches hold
every filing that parsed, a company's or guru's rows are replaced in one transaction, a failed fetch or parse
is counted and reported, and what an earlier run stored stays. What each one touches and takes (timed on
2026-10-22, from empty caches):
- `--insiders`: the SEC (each company's submissions feed and each Form 4's XML), through the app's SEC
  handler: its User-Agent (`SEC_USER_AGENT`, see [SEC User-Agent Requirement](#sec-user-agent-requirement))
  and its rate limit of about 8 requests a second. It reads each company's CIK from `companies`, so the
  database needs a collection first, and writes `insider_filings` and `insider_trades`. PFE's and AAPL's three
  years, 452 filings, took 107 seconds, about 4 filings a second: the first run for every company, an
  estimated 30,000 filings, should take about two hours. A later run reads each feed again and fetches only
  the filings not cached.
- `--gurus`: the SEC (each filer's feed and each filing's text) and OpenFIGI (`api.openfigi.com`; none with
  `--no-openfigi`). It writes `guru_filers`, `guru_filings`, `guru_holdings` and `cusip_map`, then
  `guru_quarters` and `guru_positions`. The 248 filings took 1 minute 41 seconds. OpenFIGI answers 10 codes a
  request, 2.5 seconds apart, so the first run's 1,817 codes need 7.5 minutes at least; a later run asks only
  the codes with no `cusip_map` row.
- `--positions`: nothing on the network. It reads the stored filings and entries, `cusip_map` and
  `split_events`, and writes `guru_quarters` and `guru_positions`: 3.3 seconds for 235 quarters. The changes
  read `split_events` when they are built, so run it after a collection brings new splits.

The columns and queries are in the [USAGE_GUIDE](USAGE_GUIDE.md#sec-ownership).

### Ownership limitations

- A 13F is part of a portfolio, as it stood up to four and a half months before: the long positions in
  US-listed stocks, ETFs, options and convertible notes at a quarter's end, filed up to 45 days after it. It
  reports no short positions, cash, bonds or non-US listings, so a guru's weights are shares of what it
  reports.
- A guru's latest quarter can be stale. Scion's last 13F-HR covers Q3 2025 (Michael Burry deregistered it), so
  it stays at that quarter in the aggregated portfolio, which mixes quarters; the list and the holders tab
  show each guru's quarter.
- OpenFIGI leaves some codes unmapped: 21 of the 1,817 it was asked, 7 of them CINS codes. A position in one
  has no ticker, is keyed by its CUSIP and shows its issuer's name from the filing.
- Stock dividends aren't adjusted: SCCO's 1% stock dividends read as small additions.
- HON's 2026 reverse split is inside a combined price factor in Yahoo's split history, which isn't a share
  split, so Dodge & Cox's HON reads as a 50% sale (8,019 to 4,010 shares; BUG-184).
- Form 4 history is the last 3 years and the 13F history the last 8 quarters (`--years`, `--quarters`); the
  SEC's bulk data sets would reach further and aren't used. A quarter whose previous one isn't stored has no
  changes.
- A title that puts the CEO or CFO before a comma ("CEO, Chairman") reads as a unit's head and isn't counted
  as a CEO/CFO buy.
- A joint Form 4 (several reporting owners) is read two ways: the INS tab, the API and their aggregates name
  its first owner (the lowest CIK) and count one buyer, while the screener's `ins_buyers_12m` and
  `ins_cluster_buyers_12m` count every owner.
- Four edge cases of the CUSIP map and the quarters are open as BUG-205; none shows in the data collected so
  far.

## Market data

`src/market/` stores three market-wide sources that no company's filings give, behind the web interface's **Market**
page: FRED's corporate equities and GDP (for the Buffett indicator), Robert Shiller's monthly S&P data with his
cyclically adjusted P/E (CAPE), and Treasury.gov's daily yield curve
([design](docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md)). Nothing collected for the companies
changes: not a model, a score or a history. It has its own command, `python -m src.market.collect` (see
[Collecting market data](#collecting-market-data)); a `stock-data` collection and the web interface's update don't run
it, but `stock-data refresh` does every day (see
[Keeping the data current](#keeping-the-data-current-stock-data-refresh)).

### Sources and storage

Three sources, each stored whole:
- **FRED**, the keyless `fredgraph.csv` (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>`; no API key, and
  not the optional `FRED_API_KEY`): `NCBEILQ027S`, the corporate equities of US nonfinancial corporate business at market
  value (a liability level, quarterly, $ millions, from 1945-Q4), and `GDP` (quarterly, $ billions at a seasonally
  adjusted annual rate, from 1947-Q1). A missing value is an empty cell in today's files ("." in older ones):
  `NCBEILQ027S` is annual before 1951-Q4, so its 18 quarters from 1946 to 1951 are empty.
- **Shiller**: `ie_data.xls` from [shillerdata.com](https://shillerdata.com/), which links the workbook on a CDN under a
  versioned URL, so the link is read from that page each time: an `https` link only, since the link is fetched, stored
  and shown as the page's source link (one of another scheme, `http:` or `javascript:`, is skipped). The `Data` sheet's
  monthly date, price (the month's mean of daily closes), dividend, earnings, CPI and CAPE columns are found by their
  header labels, on the row with the most of them; a missing label makes the answer one that doesn't parse, so a wrong
  column is never read (see [The cache and the network](#the-cache-and-the-network)). A date is a number: 2026.1 is
  October and 2026.01 January (the month is round(100 × the fraction)). Each series stops at its own last value: on
  2026-10-24 the price and CPI reach 2026-10, the dividend and earnings 2026-06 and the CAPE (from 1881) 2026-10.
- **Treasury.gov**: the yearly CSVs of the daily par yield curve (the feed the 10-year rate falls back to without a FRED
  API key), from 1990 to the current year: a row per trading day, each maturity's yield in percent as published, a
  blank (a maturity not issued that day) skipped. The newer maturities start when Treasury began publishing them: the
  20-year in 1993, the 1-month in 2001, the 2-month in 2018, the 4-month in 2022 and the 1.5-month in 2025; the 30-year
  has no rows from 2002 to 2006. Every year has the 3-month, 2-year and 10-year columns, so a file with rows but
  without one of them (a renamed header) doesn't parse, and a file without rows is a failed answer, except the
  current year's in its first days.

Two tables hold them (columns and queries are in the [USAGE_GUIDE](USAGE_GUIDE.md#market-data)):
- `market_series`: one row per series and date: `fred:NCBEILQ027S`, `fred:GDP`, `shiller:price`, `shiller:dividend`,
  `shiller:earnings`, `shiller:cpi`, `shiller:cape`, and `ust:1m`, `ust:6w`, `ust:2m`, `ust:3m`, `ust:4m`, `ust:6m`,
  `ust:1y`, `ust:2y`, `ust:3y`, `ust:5y`, `ust:7y`, `ust:10y`, `ust:20y` and `ust:30y` (yields in percent, as
  published). A date is the first day of the quarter (FRED) or month (Shiller), or the trading day (Treasury). When a
  source is fetched its series are replaced, FRED's and Shiller's whole and the Treasury's from its first year's
  January 1 on (so a run with a later `--treasury-from` keeps the earlier yields), in one transaction with its
  `market_fetches` row, so a store that fails changes nothing.
- `market_fetches`: one row per source (`fred:NCBEILQ027S`, `fred:GDP`, `shiller`, `treasury`): the `url` it came from
  (Shiller's workbook link, the Treasury's current-year file), the `rows` stored and `fetched_at`, when the answer came
  from the source: a cached copy's time, so a fall back to an old copy shows its age.

### Reading Shiller's workbook

`ie_data.xls` is a legacy Excel workbook (BIFF8 records in an OLE2 compound file, 1.6 MB). `openpyxl` reads only
`.xlsx` and no `.xls` reader is installed, so `src/market/xls.py` is the milestone's own: the compound file's header,
FAT and DIFAT chain, directory and the `Workbook` stream's sector chain (through the mini stream when it is small), then
the BIFF8 records: `BOUNDSHEET` for the sheets, `SST` with its `CONTINUE` records for the shared strings, and in a sheet
`NUMBER`, `RK`, `MULRK`, `LABELSST`, `LABEL`, `FORMULA` (its cached result) and `BOOLERR`. That is enough for the `Data`
sheet; every other record is skipped. A file it can't read (not a compound file, not BIFF8, a sector size other than
MS-CFB's 512 and 4096 bytes (and 64 for mini sectors), a FAT counted beyond what the file's sectors need or listing a
sector twice, a sector chain that loops, leaves the file or ends short) raises an error, before allocating more than
the file's own size, which counts as an answer that doesn't parse (see
[The cache and the network](#the-cache-and-the-network)). Installing the `xlrd` package to read the workbook with a standard library instead is an open question for the user
(Q14 in `qa/BUGS.md`); nothing was installed. The reader is tested on workbooks the tests write (every record type
above, a string split across `CONTINUE` records, a workbook in the mini stream), with no copy of Shiller's data in the
repository, and against the real file (see the checks below).

### The cache and the network

Each answer is cached once it parses, under `data/cache/market/` (the `--cache-dir`, then `market/`):
`fred_NCBEILQ027S.csv`, `fred_GDP.csv`, `shiller.html`, `ie_data.xls` and `treasury_{year}.csv`. A cached copy is used
while it is fresh, else the answer is fetched again:
- FRED's two files, Shiller's page and workbook, and the Treasury's current year: fresh for a day (fetched a day ago or
  less);
- a past Treasury year: fresh once it was cached after that year ended in New York (midnight EST, 05:00 UTC on January
  1), since a copy fetched during the year lacks its last days. Such a year is not fetched again, unless its copy has
  no rows.

A fetch that fails, or an answer that doesn't parse, falls back to the cached copy and says so ("the fetch failed
(...); using the cached copy of ..."), as it happens and on the summary's `Stale:` line; without a copy, the source
fails. An answer that doesn't parse is never cached. A Treasury file without rows counts as one that doesn't parse: a
past year's always, and the current year's once its cached copy has rows, so only the current year's first days,
before its first trading day, may have none.

The three sites are asked through one `requests` session with requests' own User-Agent (never the SEC's, which may carry
an email address), a request a second, and up to 3 retries (after 2 seconds, then doubling) of a timeout, a dropped
connection or an HTTP 429, 502, 503 or 504. Any other status fails at once.

### Collecting market data

```bash
# All three sources
python -m src.market.collect

# One or two of them
python -m src.market.collect --shiller
python -m src.market.collect --fred --treasury

# Another database or cache
python -m src.market.collect --db data/output/stock.db --cache-dir data/cache
```

| Option | Description | Default |
|--------|-------------|---------|
| `--fred` | FRED's `NCBEILQ027S` and `GDP` | all three sources when none is named |
| `--shiller` | Shiller's `ie_data.xls`: the monthly price, dividend, earnings, CPI and CAPE | all three sources when none is named |
| `--treasury` | Treasury.gov's daily yield curve, a CSV a year | all three sources when none is named |
| `--treasury-from` | The Treasury's first year: its stored yields from that year on are refreshed, and the earlier ones stay | `1990` |
| `--db` | SQLite database path; it must exist, so run a collection first (a typo must not create a new database) | `data/output/stock.db` |
| `--cache-dir` | The cache directory; the answers go under `market/` | `data/cache` |

A run prints a line per source as it goes (and one per Treasury year fetched), then a summary: how many sources stored,
each stored series' rows and dates, each source's latest date, each fallback to a cached copy (`Stale:`, with the
copy's age) and the sources that failed with the reason:

```
fred:NCBEILQ027S: 305 rows in 1 series, latest 2026-04-01 (1 fetched, 0 from the cache)
...
Market data: 4 of 4 sources stored
  fred:NCBEILQ027S: 305 rows, 1945-10-01 to 2026-04-01
  ...
  ust:30y: 8215 rows, 1990-01-02 to 2026-10-23
Latest: fred:NCBEILQ027S 2026-04-01; fred:GDP 2026-04-01; shiller 2026-10-01; treasury 2026-10-23
Stale: none
Failed: none
```

Each source runs in its own try/except: one that fails, or has no rows, is reported with its reason and stores nothing,
what it stored before stays, and the others go on. The exit code is **0** when every source asked for stored rows (one
that fell back to its cached copy too: it is on the `Stale:` line), **1** when any did not, and **2** when the database
doesn't exist (the message goes to stderr and nothing is created).

What a run touches and takes (timed on 2026-10-24): FRED, shillerdata.com with its CDN and Treasury.gov, through the
cache above, and nothing else; no key and no SEC User-Agent. From empty caches the first full run took 725 seconds,
nearly all of it the Treasury, which answers slowly (about 19 seconds for each of its 37 yearly files); rerun with
everything fresh it took 3 seconds. Later runs fetch only what is a day old: FRED's two files, Shiller's page and
workbook, and the Treasury's current year. It writes `market_series` (109,739 rows, 99,894 of them the Treasury's) and
`market_fetches`; no other table's rows are read or changed.

### The market page

`/market` (a link in the nav) shows three sections, each with its figures, its Plotly charts, a source line (the link,
the day it was fetched and the latest date) and its method note, and `GET /api/market` returns the same as JSON. Both are
computed at read time from the stored series (`src/webapp/market.py`), so a new collection needs no recomputation. A
section whose series aren't stored says which command collects it (`--fred`, `--shiller` or `--treasury`); the API then
answers 200 with that section `available: false`, a `reason` and the command.

- **The Buffett indicator**: the corporate equities of US nonfinancial corporate business over GDP,
  `NCBEILQ027S / 1000 / GDP` as a percentage, for each quarter both series have, so its date is the latest quarter both
  have.
  - The zone is the ratio's distance from its own long-run trend. `ln(ratio)` is fitted against time (in years) by least
    squares over the whole history, the residuals' standard deviation is taken, and the latest quarter's residual over
    that standard deviation is z. Below -2 is *significantly undervalued*, -2 up to -1 *modestly undervalued*, -1 to +1
    *fairly valued*, above +1 up to +2 *modestly overvalued* and above +2 *significantly overvalued*. The chart draws
    the trend and the bands at the trend × e^(±1 sd) and e^(±2 sd), and the method note publishes the cut-offs.
  - Also the latest ratio, its percentile over the history (the share of the quarters at or below it; it needs 36
    quarters), the trend's value then and its growth a year. At least 3 quarters are needed, and a ratio that never
    leaves its trend has no zone.
  - On 2026-10-24: 255.0% for Q2 2026 (83.05 trillion dollars of equities over 32.56 trillion of GDP), the highest of
    the 303 quarters since 1947-Q4 (percentile 100%) and 1.68 standard deviations above its trend (147.6%, growing 1.77%
    a year, a standard deviation of 0.325 in logs): *modestly overvalued*.
- **Shiller's CAPE**: the monthly series from 1881: the latest value and its month, the mean, median, low and high over
  the history (with the months of the low and the high), and the latest value's percentile (the share of the months at
  or below it; the statistics need 36 months).
  - Shiller's CAPE is the real price over the mean real earnings of the 120 months ending the month before, real meaning
    deflated by the CPI. His price is the month's mean of daily closes, except in his latest month, a single day's close
    with an estimated CPI (his footnotes); in his latest months, whose earnings aren't out yet, the mean is of the
    window's months that have them (119, 118 and 117 months for 2026-08 to 2026-10).
  - On 2026-10-24: 40.7 for 2026-10, against a mean of 17.8 and a median of 16.6 since 1881, a low of 4.8 (1920-12), a
    high of 44.2 (1999-12) and a percentile of 98.9%.
- **The yield curve**: the latest day's curve against the curve of the latest day on or before 365 days earlier, each
  with the maturities that have a yield that day, and the 10-year less 2-year and 10-year less 3-month spreads, in
  percentage points, on each day both have, with the inverted days (a spread below 0) shaded. For each spread:
  - its latest value and whether it is inverted now, the share of its days inverted, and its last inverted day;
  - its latest sustained inversion: its runs of days below 0 are joined into episodes across gaps of at most 20 of the
    spread's own days, and the latest episode with at least 20 days below 0 (the days below 0, not the span) is the
    sustained one, so a one-day blip doesn't displace it. The page names its first and last inverted day and the days
    below 0;
  - on 2026-10-23, against 2025-10-23, the 10-year read 5.00% against 4.01%, the 2-year 4.69% against 3.48% and the
    3-month 4.22% against 3.95%. The 10-year less 2-year spread was +0.31 points, inverted on 11.4% of its 9,209 days
    since 1990 and last on 2024-09-05; its latest sustained inversion ran from 2022-07-06 to 2024-09-05, 539 days below
    0. The 10-year less 3-month spread was +0.78, inverted on 12.2% of 9,206 days and last on 2025-10-16; its latest
    sustained inversion ran from 2025-02-26 to 2025-09-15, 85 days below 0.

Checked by `qa/oracle/market_oracle.py`, which imports nothing from `src` and reads the stored series (and, with
`--api`, a saved answer of `GET /api/market`). All 6 gates pass on the 2026-10-24 collection
(`qa/evidence/oracle-m4-5-market.txt`):
- every month of Shiller's CAPE column (1,750 months from 1881 to 2026-10) against CAPE recomputed from the sheet's own
  price, earnings and CPI columns, within 0.1%: the largest gap is 1.2e-15, so the reader and the column agree. The
  windows of 2026-08 to 2026-10 skip the blank earnings, as Excel's `AVERAGE` does;
- Shiller's monthly price against the mean of the app's own `^GSPC` closes for each month both have: 1,187 months, a
  median difference of 0.004% and 1,185 within 1%. The two misses are 1927-12 (the database has a single bar that month)
  and 1974-07 (Shiller's 79.31 is the close of 1974-07-31, not the month's mean);
- the 10-year rate of `market_snapshots` on its latest `risk_free_rate_date` (2026-10-20) against `ust:10y` that day:
  5.12%, equal on 257 snapshots;
- with `--api`: the Buffett indicator (the ratio by hand for Q2 2026, 83,050,847 / 1000 / 32,563.03 × 100 = 255.05%; the
  trend, percentile, z and zone within the tolerances), the yield curve (both curves, and each spread's days, latest
  value, inverted share, last inverted day and latest sustained inversion, all equal) and the CAPE section.

A copy of the database with altered rows, and an API answer with an altered episode, make the oracle fail, each failure
naming what was altered.

### Market limitations

- FRED publishes the equities level later than GDP, so the latest quarter both series have can be a quarter or two
  behind GDP's (on 2026-10-24 both reached Q2 2026), and the equities are annual before 1951-Q4, which thins the early
  points the trend is fitted through.
- The numerator is the nonfinancial corporate business's equities, not the whole market's value, so the figure isn't
  comparable to a Buffett indicator built on another measure.
- The span of the trend fit decides the zone. The fit runs over the whole history (1947-2026): a trend rising 1.77% a
  year with a wide spread (a standard deviation of 0.325 in logs, so +2 standard deviations is 283%). A record ratio
  therefore reads *modestly overvalued*: Q2 2026's 255.0% is the highest quarter on record at +1.68 standard deviations.
  Fitted from 1970 or from 1990 instead, the same quarter reads +1.21 or +1.33; the page does not use those fits.
- Shiller's latest month is a single day's close with an estimated CPI (on 2026-10-24, 2026-10 is October 2nd's close),
  and his dividends and earnings end months before his price, so the newest CAPE windows average the earnings they have.
- A spread's inversion share and episodes count the days stored: since 1990 by default, or since the earliest
  `--treasury-from` collected (a run with a later one keeps the earlier days).
- A change to Shiller's page or sheet layout (no `https` link to `ie_data.xls`, a missing header label) is an answer
  that doesn't parse. Once a cached copy exists, the run falls back to it, stores the same rows again and exits 0, so
  the CAPE stays at the copy's latest month; the summary's `Stale:` line lists the fallback, with the copy's age, on
  every run until the change is handled. Without a cached copy the Shiller source fails, and the stored series stays.

## The universe and industries

The app began with 127 hand-picked large caps. Milestone 5
([design](docs/superpowers/specs/2026-10-25-universe-automation-design.md)) keeps the tickers it collects in files in
the repository, adds the S&P 500's list, stores each company's SEC industry code (its SIC code) and ranks the quality
scores' five ranks within an industry as well as within the universe. Nothing collected before changes: not a model, a
valuation or a history, and not the quality scores' own ranks and composite. A list is collected with `stock-data` (see
[Collecting a list](#collecting-a-list)); the SIC code comes with every collection, and one command fills it for the
companies collected before (see [SIC codes](#sic-codes)).

### The universe files

`universe/` is tracked in the repository (never under `data/`). Its lists are tickers files for
`stock-data --tickers-file`, with tickers in Yahoo's form: a class share's dot is a dash (`BRK-B`, `BF-B`).

| File | What it is |
|---|---|
| `universe/core.txt` | The 127 companies in the database on 2026-10-25, collected by hand before Milestone 5: a ticker a line, sorted, `#` comments. A company that leaves the S&P 500 stays here: the list isn't the index (SCCO and SPCX aren't members). |
| `universe/sp500-2026-10-25.csv` | The S&P 500's members on 2026-10-25, one company a CIK: `ticker,name,cik,gics_sector,gics_sub_industry,date_added` (the CIK 10 digits, the date added an ISO date). 500 rows: the page's 503 less GOOG, FOX and NWS, the second share classes of Alphabet, Fox and News Corp (GOOGL, FOXA and NWSA are kept). |
| `universe/README.md` | The list's source and license, and how to refresh it. |

The S&P 500 list comes from Wikipedia's "List of S&P 500 companies" (its first table), read on 2026-10-25 (revision
1381317423, last edited on 2026-10-21):
- **Checked when it was saved**: 503 rows read; each CIK equal to the SEC's own ticker map's (`company_tickers.json`) for
  all 503; every ticker in Yahoo's form; every date added an ISO date. 125 of the database's 127 companies are members.
- **License**: the list's facts are taken from that page, whose text is available under the
  [Creative Commons Attribution-ShareAlike 4.0 License](https://creativecommons.org/licenses/by-sa/4.0/). The file is
  attributed to the page and its contributors and shared under the same license. It isn't S&P Dow Jones Indices'
  official list of the index's members. [`universe/README.md`](universe/README.md) has the page's address and its
  history.

To save a newer list (run from the repository folder):

```bash
# universe/sp500-<today>.csv
python -m src.universe.sp500

# Another file
python -m src.universe.sp500 --out universe/sp500-test.csv
```

It makes one request to Wikipedia, without retries, its User-Agent naming the app and carrying no email address, reads
the table with the standard library's `html.parser`, and prints the rows read, the ones dropped and the file written.
One company a CIK: a second share class after the first of its CIK in the page's order (GOOG after GOOGL, FOX after
FOXA, NWS after NWSA) is dropped, as the database keeps one (GOOGL). It never overwrites a file (an existing one is
refused before the page is fetched), and a page that changed fails loudly and writes nothing: no table with the
expected header cells (Symbol, Security, GICS Sector, GICS Sub-Industry, Date added, CIK), a ticker or a CIK that
doesn't read, a row of another width than the header, or a count of rows outside 480 to 520. Exit 0 when it wrote the
file, 1 otherwise. Keep the earlier files (each records the index on its day), update `universe/README.md`'s date and
counts, and collect the new members with `--skip-existing`.

### Collecting a list

```bash
# The 127 companies the database started with
stock-data --tickers-file universe/core.txt

# The S&P 500's members the database doesn't hold yet (375 of the 500 on 2026-10-25)
stock-data --tickers-file universe/sp500-2026-10-25.csv --skip-existing

# The option repeats, and tickers can be named beside the files
stock-data NVDA --tickers-file my-watchlist.txt --tickers-file universe/core.txt
```

- **`--tickers-file PATH`** (repeatable): a `.txt` file (a ticker a line; `#` starts a comment, a whole line's or the
  rest of a line; blank lines are ignored) or a `.csv` file (its `ticker` column, else its `Symbol` column, the header
  read without case or spaces). The tickers collected are the positional ones, then each file's in order, upper-cased,
  a dot as a dash (`BRK.B` as `BRK-B`), each once. A file is read as UTF-8. A ticker is letters and digits with at most
  one dash or dot between them: anything else (a space inside, a caret, two separators) fails the run before anything is
  collected.
- **`--skip-existing`**: leaves out the tickers already in the `companies` table of the database the run writes to
  (`--db`, else `<output-dir>/output/stock.db`; it is read without writing, and a database that doesn't exist yet counts
  as holding none), to collect the new ones only. It says how many it left out, for example
  `--skip-existing: 125 of 500 tickers already in data\output\stock.db left out, 375 to collect`.
- The errors, each logged as an error with exit 1 and nothing collected:
  - `Cannot read the tickers: ...` for a file that can't be read, one that isn't a `.txt` or a `.csv`, a CSV with no
    `ticker` or `Symbol` column, or a ticker that isn't one (a file's line is named, as in
    `x.txt, line 3: not a ticker: 'AAPL,MSFT' (letters and digits, with at most one dash or dot between them)`);
  - `No tickers given: name some, or a --tickers-file that lists some`;
  - `All 127 tickers given are already in the database (PATH): nothing to collect (--skip-existing)`;
  - `Cannot read the companies in PATH: ...`, when SQLite can't read the database.
- An S&P member that Yahoo or the SEC can't serve is reported in the collection's summary (`With errors`) and the rest go
  on.
- A list is stored 25 companies at a time as they arrive (`--batch-size`, see [Output](#output)), so stopping a long run
  (Ctrl+C, exit 130) or one company's failed write keeps what was stored, and the same command with `--skip-existing`
  collects the rest. The run exits 1 when the database didn't store every company it fetched, naming each in the log;
  check `Stored in the database: N of M` in its summary.

Adding the S&P 500's members is a long run: earlier collections took about 6 seconds a ticker with a warm SEC cache (127
in 13 minutes) and about 20 seconds cold, so the 375 new members are an estimated 2 to 3 hours, before step 3's Form 4s.
Start it in the background; if it stops, run step 2 again as it is. In order:

```bash
# 1. SIC codes for the companies collected before Milestone 5 (offline, seconds)
python -m src.universe.sic

# 2. The members the database doesn't hold yet, collected in full
stock-data --tickers-file universe/sp500-2026-10-25.csv --skip-existing --sec-user-agent "YourName you@example.com"

# 3. The insiders (three years of Form 4s: hours at the SEC's rate for the new members, the others from the cache),
#    then the gurus' positions
python -m src.ownership.collect --insiders
python -m src.ownership.collect --positions
```

Every collection values each batch of companies it stores and then ranks all the stored ones, in the universe and within
industries, so doing step 1 first gives the collection's rank passes every code; if the codes are filled after a
collection, `python -m src.valuation.backfill` ranks again. Give the SEC a real contact in `--sec-user-agent` (see
[SEC User-Agent Requirement](#sec-user-agent-requirement)). The insiders and the gurus aren't part of a collection (see
[SEC ownership](#sec-ownership-insiders-and-gurus)): step 3 fetches the new companies' Form 4s, and `--positions`
rebuilds the gurus' changes, which read `split_events` when they are built, after the new companies' splits arrive.
About 0.73 GB of database and 1.4 GB of JSON exports are expected for about 507 companies, about four times what the 127
take.

### SIC codes

The SEC assigns each filer a four-digit Standard Industrial Classification code and describes it ("3571", "Electronic
Computers"). It is stored in `companies.sic` and `companies.sic_description`, written by every collection from the
company's submissions feed, which the collection fetched already: the same code decides the company's sector class. A
collection whose feed failed keeps the stored code.

For the companies collected before Milestone 5, one command fills the codes without a request:

```bash
python -m src.universe.sic
python -m src.universe.sic --db data/output/stock.db --json-dir data/output/json --cache-dir data/cache
```

| Option | Description | Default |
|--------|-------------|---------|
| `--db` | SQLite database path; it must exist and have a `companies` table (nothing is created) | `data/output/stock.db` |
| `--json-dir` | The JSON exports, `<TICKER>.json` | `data/output/json` |
| `--cache-dir` | The SEC handler's cache, `submissions_CIK<10-digit CIK>.json` | `data/cache` |

For each company without a code (`sic` empty or not a code) it reads `sec_submissions.sic` and
`sec_submissions.sic_description` from the company's JSON export, else from the SEC handler's cached submissions for its
CIK (read whatever its age; the handler isn't used and no request is made). It writes those two columns only, in one
transaction (adding them to a `companies` table from before them), and prints the companies, how many lacked a code,
how many it filled from the exports and from the cache (naming the ones from the cache) and each company still
missing. Exit 0, or 1 when the database or its `companies` table isn't there. On 2026-10-25 it filled all 127 codes
from the JSON exports in about 4 seconds, each equal to its export's; filling from the cache alone gave the same 127.

### Industry groups

A company's **industry group** is formed from the stored codes of the companies being ranked (those with quality
scores), each time the ranks are computed:
- its 3-digit SIC prefix (the SEC's industry group) when at least 10 of them share it;
- else its 2-digit prefix (the SIC major group) when at least 10 share that;
- else none, and the company has no industry ranks.

A group's members are every company whose code starts with it, so a 2-digit group takes in its 3-digit groups'
companies and always has 10 members or more; a company is ranked in one group only, the 3-digit one when it has one.
A group's label is drawn from the data, so no SIC table is needed: "SIC 283: " and its members' three most common SEC
descriptions, by count and then alphabetically, joined with "; " (a description can hold a comma). Each company's
`quality_scores` row stores `industry_group`, `industry_label` and `industry_size` (the members), all empty without a
group.

At 127 companies (2026-10-25) eight groups form, 55 companies have a group and the other 72 (all with a code) are in
industries under 10 companies:

| Group | Members | Ranked in it | Industry |
|---|---|---|---|
| 283 | 10 | 10 | drugs |
| 28 | 12 | 2 (LIN, PG) | its major group, with the 10 of 283 |
| 357 | 10 | 10 | computers |
| 35 | 14 | 4 (CAT, DE, ETN, LRCX) | its major group, with the 10 of 357 |
| 367 | 10 | 10 | semiconductors |
| 36 | 13 | 3 (GE, GEV, QCOM) | its major group, with the 10 of 367 |
| 737 | 12 | 12 | software |
| 73 | 16 | 4 (MA, SPGI, UBER, V) | its major group, with the 12 of 737 |

The label of 367 reads "SIC 367: Semiconductors & Related Devices; Electronic Connectors".

### Industry ranks

`rank_industries` (`src/scores/ranks.py`) computes M2's five ranks and the composite again within each company's industry
group, from the same components `quality_scores` stores (`components`), oriented the same way. It runs in the rank pass,
so every collection, web update job, `python -m src.valuation.backfill` and `stock-data refresh`'s recompute step
produces it, and any run ranks everyone again:
- Each **component** becomes a percentile within the group: the share of the members that have the component whose
  value is at or below the company's (at or above, where lower is better), when at least 10 members have it. A
  component fewer than 10 members have is left out of the industry rank, for the whole group.
- A **rank** is `ceil(10 x mean)` of its components' percentiles, with M2's minimums, counting only the components
  ranked within the industry: two for financial strength, profitability and growth, one for valuation and momentum.
- The **industry composite** is `10 x` the mean of the industry ranks given, with at least four of them.
- A company in a 2-digit group is ranked against all that group's members, its 3-digit groups' companies included.
- M2's own ranks, composite and Magic Formula rank are unchanged, and the ranks are stored beside them in
  `quality_scores`: `ind_rank_strength`, `ind_rank_profitability`, `ind_rank_growth`, `ind_rank_valuation`,
  `ind_rank_momentum` and `ind_composite`, empty without a group or the components (columns and queries are in the
  [USAGE_GUIDE](USAGE_GUIDE.md#the-universe-and-industries)).

At 127 companies 54 of the 55 have at least one industry rank (SPCX, in 737, has none), 41 have all five and 42 have an
industry composite. NVDA's ranks within 367 read 9, 8, 10, 10 and 2 with a composite of 78, against 10, 8, 10, 9 and 5
and 84 in the universe.

Where it shows:
- **DES tab**: the SEC's industry under the description, "Industry (SEC): SIC 3571 Electronic Computers", once a code is
  stored.
- **STAT tab**: the Quality section's rank table gains an *Industry* column beside the 1-10 ranks, an *Industry
  composite* pill beside the composite, and a line naming the group ("Industry ranks among the 10 companies of SIC 367:
  ..."); a company with no industry ranks says why ("No industry ranks: SIC 6021's industry has fewer than 10 companies
  here").
- **API**: `GET /api/stocks/{ticker}/scores` gains an `industry` block: `group`, `label`, `size`, `sic`, `ranks` (the
  five), `composite` and `na_reason`, which is null with a group and otherwise "no SIC code stored", "industry ranks not
  computed yet" (a database ranked before this milestone) or "SIC <code>'s industry has fewer than 10 companies here".
- **Screener**: an *Industry* drop-down of every 2-digit major group and 3-digit industry group that has a company
  (the 3-digit ones indented under theirs), each as its label and its size; the `industry` parameter,
  `GET /api/screen?industry=367` (also `POST /api/screen`'s `industry`, the page's `/screener?industry=367` and the CSV
  export): a 2- or 3-digit SIC prefix, the companies whose code starts with it, validated as digits (anything else is
  a 400) and bound as a parameter, for any prefix whether or not it forms a group; and the fields `ind_rank_strength`,
  `ind_rank_profitability`, `ind_rank_growth`, `ind_rank_valuation`, `ind_rank_momentum` and `ind_composite` to filter
  and sort by, labelled "(industry)" under the quality scores, for example
  `GET /api/screen?industry=367&sort=ind_composite&sort_dir=desc`. The results and the CSV carry a `sic` column. An
  option's size counts every company with that code; a screen lists those that have annual metrics (737 reads 12, and
  SPCX, which has no annual metrics, is not among its 11 rows).

Checked by `qa/oracle/scores_oracle.py`, which imports nothing from `src`, with three gates added to its nine: all 12
pass on the 127 companies (`qa/evidence/industries-m5-2-oracle-joiner.txt`).
- Each company's industry group, label and size recomputed from `companies.sic` (381/381 values).
- The five industry ranks and the industry composite recomputed from the stored components, with exact fractions
  (762/762).
- The same 73 companies without any industry rank (the 72 without a group and SPCX).
- A copy of the database with an altered rank, label or group makes the gates fail, naming the row
  (`qa/evidence/industries-m5-2-oracle-broken.txt`). No value of M2's ranks, composites or Magic Formula ranks differs
  from a copy made before the industry pass, and every other table is identical (timestamps aside).

### Universe limitations

- Groups of 10 are fragile at this size: the group size and a component's minimum are both 10, so one member missing a
  component drops it for the whole group. At 127 companies IBM lacks the operating margin (and its mean and slope), the
  median ROIC and Altman Z, and SNDK, a 2025 spin-off, lacks the 5-year growth, most of profitability and price / value,
  so 357 keeps only financial strength and momentum and none of its 10 companies has an industry composite. In 283 five
  companies (BMY, JNJ, LLY, MRK, PFE) lack the operating-margin components, the median ROIC and Altman Z, so those
  components are left out there, and the rest still give the group all five ranks.
- A 2-digit group's label is dominated by its largest 3-digit group: DE and CAT, in 35, read "SIC 35: Computer Storage
  Devices; Computer Communications Equipment; Computer Peripheral Equipment, NEC".
- A company in a 2-digit group is ranked against its whole major group, not against its own trade: MA and V (7389,
  business services) are ranked in 73 against 16 companies, 12 of them software (737).
- The SIC code is the SEC's, one code a filer: AMZN's is 5961 (Retail-Catalog & Mail-Order Houses), which no other
  company in the database shares, so it is in no group.
- Ranks are percentiles among the companies the database holds, so they move when the universe changes: as companies
  are added, more prefixes reach 10.
- BUG-231: "one company a CIK" keeps the first row of each CIK in the page's order. Wikipedia sorts by company name, so
  "(Class A)" comes first and GOOGL, FOXA and NWSA are kept. A page sorted by symbol would keep GOOG, FOX and NWS, and
  `--skip-existing`, which compares tickers, not CIKs, would then collect a second Alphabet beside the database's
  GOOGL. The saved file is right, and none of its 375 new tickers is a stored company under another ticker.
- A list is a snapshot: a company that leaves the index stays in the database, and `stock-data refresh` works on the
  companies already there, so new members are added by collecting the list again with `--skip-existing`.

## Keeping the data current: stock-data refresh

`stock-data refresh` keeps the database current with one command a scheduler can run
([design](docs/superpowers/specs/2026-10-25-universe-automation-design.md)): the quotes and the new daily bars every
day, the SEC's new filings every week (a company with a new report is collected in full), the insiders, the gurus and
the market data, then the recomputation of the valuations, history, scores, indicators and ranks. `refresh` is the first
argument of the same command (`stock-data refresh`, or `python -m src.main refresh`; no ticker is called REFRESH). It
works on the companies the database already holds, run by hand or scheduled (see
[Scheduling it on Windows](#scheduling-it-on-windows)), and a day it missed is caught up by the next run: the bars step
fetches from each company's last stored bar, and the SEC step looks for the reports filed since each company's last
collection.

### The steps

Eight steps, in this order, each in its own try/except: a failure is reported and the others go on. Three of them put
companies on the **collection list**, each with its reason: `quotes`, `bars` and `sec`.
- **`quotes`** (daily): every company's Yahoo quote, through the code of the web interface's on-demand quote refresh
  (`src/fetchers/quote_refresh.py`): one request a company, stored as a new market snapshot and a new analyst snapshot
  (a full collection's rows stay). A company with a split since its last collection (a share split, or any price factor
  of 3:2 or more) isn't saved: only a full collection restates its per-share figures, so it goes on the collection
  list. A stock dividend's or a smaller spin-off's factor is stored with the price and the company is valued on it
  right away.
- **`bars`** (daily): every company's daily bars since its last stored bar, one `history(start=...)` request a company
  with a collection's parameters (unadjusted closes, as a collection stores them).
  - Yahoo is asked from the latest stored bar before the last one with a close above 0 (the bar before the last, unless
    it was stored without a close, or with 0 or less; the 10 before the last are looked through). That bar is final,
    so its close shows whether Yahoo's prices are still on the stored bars' basis; the bars from the last stored one on
    (it may have been a part-day bar) are replaced in one transaction.
  - A company goes on the collection list instead, its bars left as stored, when Yahoo's close for that earlier bar
    differs from the stored one by more than 0.5% (a stock dividend or a spin-off moves it by about 1% or more, a split
    by far more), when Yahoo's bars give no close for that day, when Yahoo lists a split or price factor dated after
    the last stored bar, or when its stored bars are Yahoo's dividend-adjusted closes (BUG-075), which only a full
    collection replaces. A company already on the list is left to the collection, and one with no stored bars is
    skipped, not queued.
  - An answer with no bars at all is that company's error, its bars left as stored and nothing queued: yfinance gives
    one for a timeout, throttling or a ticker Yahoo no longer serves ("possibly delisted; no timezone found") rather
    than an error, so on a bad Yahoo day the step fails (every company's bars failed) instead of queuing every company
    for a full collection. A company with no close above 0 among the (up to) 10 bars before its last is an error too:
    a full collection would store the same bars.
  - Then the benchmark's bars (`^GSPC`, which the GP tab compares a stock against) the same way, whatever `--tickers`
    names. It is never queued: on another basis its bars stay as stored, and the next collection's benchmark fetch
    stores its whole history again.
- **`sec`** (weekly): every company's submissions feed at the SEC, read fresh (never through the SEC handler's 7-day
  cache, which would show a weekly step a feed up to a week old) and every entry of its `recent` block (the SEC keeps a
  year's there, or the latest 1,000 when that is more), not the newest 100 the handler keeps for its other callers: a
  bank files hundreds of prospectus supplements a week, so the newest 100 hide its 10-Q. Read on 2026-10-25, GS's recent
  block held 16,518 filings (2025-10-24 to 2026-10-23), the newest 100 reached back only to 2026-10-21, and its latest
  10-Q, filed 2026-08-03, sat behind 3,159 newer ones. A 10-K, 10-Q, 20-F, 40-F, a transition report (10-KT, 10-QT) or
  an amendment of one ("10-Q/A") filed on or after the day of the company's latest collection with SEC data puts it on
  the collection list. That day is the collector's own (`collection_runs`), which east of New York runs ahead of the
  SEC's, so a report filed that day counts; a company never collected with the SEC counts every report in its feed. A
  company without a CIK is counted and skipped, and a feed that fails is that company's error.
- **`collect`** (whenever the list isn't empty): a full collection of the list as `stock-data` runs it (Yahoo and the
  SEC, every year, the JSON, CSV and SQLite outputs, stored and valued 25 companies at a time), four fetched at a time,
  its SEC data read fresh like the `sec` step's. The list is built within the run by the three steps above, so naming
  `collect` alone leaves nothing to collect.
- **`insiders`** and **`gurus`** (weekly): the collectors of [SEC ownership](#sec-ownership-insiders-and-gurus), the
  insiders' last three years of Form 4s for every company with a CIK (each filing fetched once and cached), then the
  gurus' last eight quarters of 13F filings, their CUSIPs' tickers (OpenFIGI, cached) and the gurus' positions.
- **`market`** (daily): the collector of [Market data](#market-data), its three sources.
- **`recompute`**: every company's valuations, history, scores and indicators, then the ranks (what
  `python -m src.valuation.backfill` does), when a step of the run stored something or when it is named or forced.

**Cadence.** A step with a cadence is skipped while it succeeded in a run that started within its period: 20 hours for
a daily step, 6 days 20 hours for a weekly one, a little under a day and a week so that a run scheduled daily never
skips a step because yesterday's started a few minutes later. A step's age is measured from the start of its run, not
from when the step finished, so a step that finished hours into a long run (`market` after a collection of 2 or 3
hours) is still due at the next day's run. On a daily schedule `quotes`, `bars` and `market` run every day and `sec`,
`insiders` and `gurus` every seventh. What counts is the step's latest `refresh_log` row that finished ok in a run
without `--tickers`; a failed or skipped row doesn't, and neither does a `--tickers` run's: a test on a few companies
refreshed no other, so the next full run is due as if it hadn't happened (its rows stay logged, with its `subset`).

A weekly step also runs again, at the next run, while its latest success left something undone:
- `sec`, when a company's feed failed in it, or when it put a company on the collection list and that run's `collect`
  step didn't finish ok (it failed, `--steps` left it out, or the run was stopped or died before it ended). The list
  lives only in the run, so the check is made again: a report filed on or after the day of a company's latest
  collection with SEC data still counts until that company is collected.
- `insiders` and `gurus`, when a company's Form 4s or a guru's 13F filings failed in it.

The step then says why it runs again, on its `running` line, in the summary and in `refresh_log`: `sec: running
again: succeeded in the run started 1d ago, within its weekly period, but that run had feed errors for AAPL`. A
company whose feed keeps failing makes the step run every day, each day naming it. `--steps` names the steps to run,
whatever their cadence (and `recompute` runs when named), and `--force` ignores the cadence of every step.

### Running it

```bash
# Every step that is due: what a scheduler runs
stock-data refresh

# Only some steps, whatever their cadence
stock-data refresh --steps quotes,bars

# Every step, whatever its cadence
stock-data refresh --force

# Two companies only (for testing; it counts for no step's cadence)
stock-data refresh --tickers AAPL,MSFT

# On a copy of the database: give it an output directory too, or a collection's
# JSON and CSV go into data/output and the run's log into data/logs
stock-data refresh --db scratch/stock.db --output-dir scratch

# The defaults spelled out
stock-data refresh --db data/output/stock.db --output-dir data --cache-dir data/cache
```

| Option | Description | Default |
|--------|-------------|---------|
| `--steps` | Run only these steps, whatever their cadence (comma-separated: `quotes`, `bars`, `sec`, `collect`, `insiders`, `gurus`, `market`, `recompute`) | every step that is due |
| `--force` | Run every step, whatever its cadence | `false` |
| `--tickers` | Limit the per-company steps to these of the database's companies (comma-separated; for testing). Each must be in the database; the benchmark's bars, the gurus and the market data aren't per company. Such a run counts for no step's cadence: its `refresh_log` rows carry its `subset`, and the next full run is due as if it hadn't happened | all companies |
| `--db` | SQLite database path; it must exist (none is created). It doesn't move the outputs: see `--output-dir` | `<output-dir>/output/stock.db` |
| `--output-dir` | The base output directory: a collection's exports go under `output/`, the run's log under `logs/`. `--db` doesn't change it, so a run on a copy with `--db` alone writes a collection's JSON and CSV into `data/output` and its log into `data/logs`: give both options when testing on a copy | `data` |
| `--cache-dir` | The cache of the SEC's answers, the ownership filings and the market data (a cache, so a copy's run can share it) | `data/cache` |

The SEC's User-Agent is `SEC_USER_AGENT` (in `.env` or the environment, see
[Environment Variables](#environment-variables)); `refresh` has no `--sec-user-agent`.

### Output and the log

A run prints a time-stamped line as each step starts and finishes, one for every 25 companies a long step has done and
a collection's line for each company, then a summary: each step ran (`ran again`, and why, for a weekly step run again
within its period) or was skipped (and why), ok or FAILED (and why), with its counts; the collection list, each company
with its reasons; and the exit code. Everything printed is appended
to `<output-dir>/logs/refresh-<YYYY-MM-DD>.log` (`data/logs/refresh-2026-10-25.log`: the day the run started, local
time, UTF-8), which also holds the detail the console leaves out: each quote stored, each company's bars, a failed
step's traceback. An excerpt of the first real run's output, on a copy of the 127-company database (the path
shortened):

```
06:01:39 Refresh of data\output\stock.db: 127 companies, steps quotes, bars, sec, collect, market, recompute
06:01:39 quotes: running
06:06:55 quotes: done in 5m 15s: 127 of 127 quotes stored, 0 to collect, 0 errors
...
06:15:08 Summary of the refresh started 2026-10-25T06:01:39+07:00:
06:15:08   quotes: ran, ok: 127 of 127 quotes stored, 0 to collect, 0 errors
...
06:15:08   sec: ran, ok: 127 feeds read, 19 new reports at 19 companies, 0 without a CIK, 0 errors
06:15:08   collect: ran, ok: 19 of 19 collected (19 without errors, 0 with errors)
06:15:08   insiders: skipped (not named in --steps)
06:15:08   gurus: skipped (not named in --steps)
06:15:08   market: ran, ok: 4 of 4 sources stored
06:15:08   recompute: ran, ok: 127 of 127 companies valued, then ranked
06:15:08 Collection list (19): AXP (sec: 10-Q filed 2026-10-23 (0000004962-26-000409)), DHR (sec: 10-Q filed 2026-10-21 (0000313616-26-000199)), ...
06:15:08 Exit 0: every step ran or was skipped
```

`refresh_log`, a table of the database, has a row for each step of each run, ran or skipped: `id`, `step`,
`started_at` and `finished_at` (UTC, ISO 8601), `ok` (1 for a step that ran and succeeded, 0 for one that failed, empty
for one that was skipped) and `summary` (JSON: `run`, the run's start, which groups a run's rows and from which the
cadence measures a step's age; `subset`, a `--tickers` run's companies, whose rows the cadence passes over; then the
step's counts, or `skipped` with the reason, or `error`; and `again`, why a weekly step ran again within its period).
The cadence reads it, and so can you (queries are in the [USAGE_GUIDE](USAGE_GUIDE.md#the-refresh-log)). A step whose
row can't be written says so and runs again next time.

### One run at a time, and the exit codes

`<db>.refresh.lock` (`data\output\stock.db.refresh.lock`) is held with an OS file lock for the whole run
(`msvcrt.locking` on Windows, `fcntl.flock` elsewhere), which the OS releases should the process die, so a lock a dead
run left is simply taken. The file holds the running process's PID and start time and stays after the run. A second
refresh prints `Not started: another refresh holds <path>: PID <n>, started <time>. One refresh runs at a time.`, logs
it and exits 3. Only refreshes take the lock: it doesn't stop a `stock-data` collection, a web job or
`python -m src.ownership.collect` from writing to the same database.

| Exit | Meaning |
|---|---|
| 0 | every step ran or was skipped |
| 1 | a step failed (the summary names it; the others went on), the companies couldn't be read from the database, or the run itself stopped on an error (its traceback is in the log) |
| 2 | no database at `--db` (none is created), `--tickers` naming a company that isn't in it, or a usage error such as an unknown step |
| 3 | another refresh holds the lock |
| 130 | interrupted with Ctrl+C: the steps that finished are logged |

A step fails when it raises or when every one of its items fails: every quote, every company's bars and the benchmark's
(an answer with no bars is a failure too, so a day Yahoo answers nothing fails `quotes` and `bars` rather than queuing
every company), every feed, every company's Form 4s, every guru's filings, every market source, a `recompute` that
valued no company. Failures of single items are counted and listed in the summary, and a weekly step that had one runs
again at the next run (see Cadence above). `collect` is stricter: it fails when the database didn't store any one
company of the list (the others are kept), naming it.

### Timings

Timed on 2026-10-25 on a copy of the 127-company database: the first real run, with
`--steps quotes,bars,sec,collect,market,recompute` (the insiders and the gurus left out), took 13 minutes 31 seconds.

| Step | Time | Result |
|---|---|---|
| `quotes` | 5 min 15 s | 127 of 127 quotes stored |
| `bars` | 5 min 17 s | 127 companies' bars updated: 508 bars written in place of 127, 2026-10-20 to 2026-10-23 |
| `sec` | 35 s | 127 feeds read, 19 new reports (Q3 10-Qs filed 2026-10-21 to 2026-10-23) |
| `collect` | 2 min 12 s | the 19 companies collected in full, 0 errors |
| `market` | 0 s | 4 of 4 sources stored, every answer from the cache |
| `recompute` | 7 s | 127 of 127 companies valued, then ranked |

- The quotes and bars steps ask Yahoo once a company each, 2.5 seconds apart (the app's Yahoo pacing), so each takes
  about 5 minutes for 127 companies and an estimated 20 minutes for about 500. This run was timed before the bars step
  also refreshed `^GSPC` (one more request) and before the `sec` step read every entry of the recent block (the same
  one request a company).
- No close of a replaced bar changed, no bar before a company's last stored one changed, and all 127 new closes were
  within 0.01% of the quotes stored the same day. Every table the steps don't write was identical to the copy before
  the run.
- The scheduled form (no `--steps`) run next on the same copy ran the weekly insiders and gurus steps, which had not
  succeeded there, and then `recompute`, and skipped the rest: 1 min 35 s for the insiders (31,526 Form 4 filings
  re-stored from the cache for 127 companies, nothing new), 14 s for the gurus (248 13F filings) and 8 s to recompute,
  2 minutes in all. Run once more at once, it skipped every step in 3 seconds. A refresh started while another ran
  exited 3, naming the first one's PID.

### Scheduling it on Windows

Installing a scheduled task is a persistent change to the machine, so it is the user's decision (Q15 in
`qa/BUGS.md`): the build did not install one, and nothing in the repository does. The command below creates a daily task
that runs `stock-data refresh` from the repository folder, because the paths it uses (`data/output/stock.db`,
`data/cache`, `data/logs`) are relative to it. Change `C:\dev\stock-database` to where the repository is, and run it in
a Command Prompt or PowerShell:

```bat
schtasks /Create /SC DAILY /ST 05:00 /TN "stock-database refresh" /TR "cmd /c cd /d C:\dev\stock-database && stock-data refresh"
```

- **The time**: after the US close, which is 16:00 New York time. A run before it stores that day's bar as it stands (a
  part-day bar), which the next run replaces. Pick a time at least an hour after the close in your local time. New York
  is on UTC-4 until the first Sunday of November and UTC-5 after, so on a machine at UTC+7 the close is 03:00 and then
  04:00 the next morning, and 05:00 or later is an hour past it all year. `/ST` takes `HH:mm`, 24-hour.
- **The first run** finds every step due. Its insiders step lists each company's Form 4s again and fetches only those
  not in the cache yet (`data/cache/form4/`, where each filing is kept once it parses): for companies whose Form 4s were
  collected before, a minute or two (1 minute 35 seconds for 127 companies), but for a company never collected for
  insiders, three years of filings, which take hours at the SEC's rate for the S&P 500's new members (see
  [Collecting ownership data](#collecting-ownership-data)). Run `python -m src.ownership.collect --insiders` by hand
  first if you would rather the scheduled run didn't.
- **Checking it**: `schtasks /Query /TN "stock-database refresh" /V /FO LIST` shows its status, its last and next run
  times and its Last Result, which is the exit code of `stock-data refresh` (the table above).
  `schtasks /Run /TN "stock-database refresh"` starts it now. The day's log is `data/logs/refresh-<date>.log`, and
  `refresh_log` records every step.
- **Removing it**: `schtasks /Delete /TN "stock-database refresh" /F`.
- If Task Scheduler can't find `stock-data`, give the command's full path (`where stock-data` prints it). The task's
  conditions (running on battery, waking the machine) and the account it runs as are Task Scheduler's: open the task
  there, or see `schtasks /Create /?` for `/RU` and `/RP`.

### Refresh limitations

- BUG-232: collections outside the refresh (`stock-data`, the web's collect job) read the SEC handler's 7-day cache of
  companyfacts and submissions. A company that files a 10-Q on day 2 after its cache was written on day 0, and is
  collected by hand on day 3, is stored without the 10-Q, and its `collection_runs` row (day 3) then postdates the
  filing, so the SEC step never queues it for that report. It stays without the report until a later report or a
  collection after the cache's expiry. The refresh's own collections read the SEC fresh and aren't affected.
- The market step stores the market collector's cached answers again while they are a day old or less (that collector's
  own freshness rule), so a run a few minutes earlier than the day before can add nothing new, and a series can trail by
  a day until the next run.
- The refresh works on the companies already in the database: it never adds index members (collect a list with
  `--skip-existing` for that) and never removes a company.
- A report filed on the day of a company's last collection counts, so that company is collected once more.
- The insiders step lists every Form 4 of the three-year window again each week; the documents come from the cache, but
  it took 1 minute 35 seconds for 127 companies.
- A weekly step with an item that keeps failing (a feed the SEC keeps refusing, a Form 4 that never parses) runs at
  every daily run, naming it, until it is read; and a company of the SEC step's list that a collection can't store is
  looked for and collected again at every daily run, each run's `collect` step failing and naming it, until it is
  stored.
- New reports are noticed weekly: a company that files a 10-Q the day after the SEC step ran is collected up to a week
  later.

## Project Structure

```
stock-database/
├── src/
│   ├── __init__.py
│   ├── main.py                    # CLI entry point
│   ├── refresh.py                 # CLI: stock-data refresh (keeps the database current)
│   ├── config.py                  # Configuration
│   ├── fetchers/
│   │   ├── stock_data_fetcher.py  # Main orchestrator
│   │   ├── yahoo_handler.py       # Yahoo Finance API
│   │   ├── sec_handler.py         # SEC EDGAR API
│   │   ├── fred_handler.py        # FRED API
│   │   ├── quote_refresh.py       # A quote-only refresh, shared by the web job and the refresh
│   │   └── rate_limiter.py        # Rate limiting
│   ├── parsers/
│   │   ├── xbrl_parser.py         # SEC XBRL parsing
│   │   └── calculated_metrics.py  # Financial calculations
│   ├── models/
│   │   └── stock_data.py          # Data models
│   ├── exporters/
│   │   ├── json_exporter.py       # JSON output (with merge)
│   │   └── csv_exporter.py        # CSV summary
│   ├── ownership/
│   │   ├── form4.py               # Form 4 parser (insiders' filings)
│   │   ├── insiders.py            # Insider aggregates
│   │   ├── gurus.py               # The gurus (13F filers)
│   │   ├── thirteenf.py           # 13F parser: units and checksum
│   │   ├── cusips.py              # Our companies' CUSIPs
│   │   ├── openfigi.py            # OpenFIGI CUSIP-to-ticker client
│   │   ├── portfolio.py           # A guru's quarters and changes
│   │   └── collect.py             # CLI: python -m src.ownership.collect
│   ├── market/
│   │   ├── xls.py                 # Reader of Shiller's legacy .xls (OLE2 + BIFF8)
│   │   ├── sources.py             # FRED, Shiller and Treasury.gov: parsers, fetchers, cache
│   │   └── collect.py             # CLI: python -m src.market.collect
│   ├── universe/
│   │   ├── tickers.py             # Tickers in Yahoo's form; the tickers files (--tickers-file)
│   │   ├── sp500.py               # CLI: python -m src.universe.sp500 (the S&P 500 list from Wikipedia)
│   │   ├── industries.py          # SIC codes and industry groups
│   │   └── sic.py                 # CLI: python -m src.universe.sic (fills the SIC codes offline)
│   └── mappings/
│       └── xbrl_tags.py           # GAAP tag mappings
├── universe/
│   ├── README.md                  # The lists' source, license and how to refresh them
│   ├── core.txt                   # The 127 companies the database started with
│   └── sp500-2026-10-25.csv       # The S&P 500's members on that day
├── data/
│   └── output/
│       ├── json/
│       └── csv/
├── qa/
│   ├── oracle/                    # Independent checks of the stored data (import nothing from src)
│   │   ├── valuation_oracle.py    # The valuation models
│   │   ├── history_vs_yahoo.py    # The valuation history against Yahoo's figures
│   │   ├── scores_oracle.py       # The quality scores and the industry ranks
│   │   ├── thirteenf_oracle.py    # The gurus' 13F holdings against the filings
│   │   ├── indicators_oracle.py   # The other valuation indicators
│   │   └── market_oracle.py       # The market series and views
│   └── scale/                     # The scale pre-flight (see Development)
│       ├── synthetic_universe.py  # A copy of a database with each company cloned under made-up tickers
│       └── page_timings.py        # Times every page and API against its gate
├── diagnose.py                    # Diagnostic tool
├── requirements.txt
└── README.md
```

## Configuration

### Environment Variables

Create a `.env` file (optional):

```env
# SEC EDGAR User-Agent (required format: "Company email@domain.com")
SEC_USER_AGENT=MyCompany contact@mycompany.com

# FRED API Key (optional, uses public endpoint by default)
FRED_API_KEY=your_api_key_here
```

### SEC User-Agent Requirement

SEC EDGAR requires a valid User-Agent header. Update the default in production:

```bash
python -m src.main AAPL --sec-user-agent "YourCompany your@email.com"
```

## Data Standardization & Quality

Different companies report the same concept under different US-GAAP XBRL tags
(e.g. `Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax` vs
`SalesRevenueNet`). This pipeline resolves every concept to a stable **canonical
field** (`revenue`, `net_income`, `operating_cash_flow`, …) defined in
`src/mappings/canonical.py`, so financials are directly comparable across companies.
Each period also carries a `_source_tags` map recording which raw tag each value
came from.

Every company is scored by a **sector-aware** data-quality pass
(`src/validation/quality.py`) that checks required fields, accounting identities
(Assets = Liabilities + Equity + non-controlling interest, Gross Profit = Revenue −
COGS), sign conventions, and year-over-year continuity. The result is stored under
`data_quality` (score 0–100 + findings) and surfaced by `python diagnose.py TICKER`.

### Cross-sector coverage

Banks, insurers, and REITs report fundamentally different statements, so the pipeline
classifies each company by SIC (`src/mappings/sectors.py`) and adds dedicated line
items per sector — banks (`net_interest_income`, `provision_for_credit_losses`,
`total_deposits`), insurers (`premiums_earned`, `claims_reserve`), REITs
(`real_estate_net`) — on top of the universal core. Fields that aren't directly tagged
are filled by accounting identities (`src/parsers/derived_fields.py`): e.g.
`total_liabilities = total_assets − total_equity`, and bank revenue from net interest +
noninterest income (captured when reported; broker-dealers classified as banks may report
fee/trading components without the aggregate, so `noninterest_income` is not required).

The **universal core** (`revenue`, `net_income`, `total_assets`, `total_liabilities`,
`total_equity`, `operating_cash_flow`) resolves for **100%** of a 41-company
cross-sector basket. Prove it yourself:

```bash
python coverage_report.py            # curated cross-sector basket
python coverage_report.py JPM PGR PLD # specific tickers
```

This reports per-field, per-sector fill rates and surfaces any company missing a core
field (with the raw tags it filed) so candidate-tag lists can be extended.

### Sector-aware metrics

Ratios are computed per the company's sector (classified from its SIC code), so
cross-company screening compares like with like:

| Sector | Added ratios | Suppressed generic ratios (stored `NULL` = not applicable) |
|---|---|---|
| **Bank** | net interest margin\*, efficiency ratio, loan-to-deposit | EBITDA family, ROIC/NOPAT/invested capital, interest coverage, gross/operating margin, inventory & receivables turnover, asset turnover, working capital, net/total debt, FCF family, EV family (EV, EV/EBITDA, EV/revenue, EV/FCF, FCF yield) |
| **Insurer** | loss ratio, combined ratio\* | EBITDA family (with EV/EBITDA), ROIC/NOPAT/invested capital, inventory turnover, gross margin, asset turnover, working capital |
| **REIT** | FFO\*, AFFO\*, FFO/share, FFO payout | ROIC/NOPAT/invested capital, inventory & receivables turnover, gross margin, asset turnover, FCF family (with EV/FCF and FCF yield) |

General operating companies (and utilities/energy) get the full generic ratio
suite unchanged. A suppressed ratio is stored as `NULL`, so a screen such as
`WHERE roic > 0.15` automatically excludes sectors where ROIC is undefined
instead of returning a misleading value. A ratio built on a suppressed figure goes
with it, as the EV family goes with a bank's net debt, EBITDA and FCF.

\* **Proxy** (the registry doesn't split out the exact inputs):
net interest margin = `net_interest_income / total_assets`;
combined ratio = `benefits_and_expenses / premiums_earned`;
FFO = `net_income + total D&A` (no real-estate-specific D&A or gains-on-sale
adjustment); AFFO = `FFO − total capex`. Each proxy is flagged in the metrics
JSON under `_basis`.

**Energy & utilities.** Both use the general operating-company schema. `operating_income` is
**not a required field** for any sector — integrated oil & gas majors and other diversified
multinationals (e.g. JNJ) report pretax income by geography rather than a tagged
`OperatingIncomeLoss` line, so its absence is not a data-quality penalty (it is still captured
when reported). Oil-major `roic`/`inventory_turnover` are left empty rather than approximated;
the `inventory` line resolves from energy-specific tags
(`EnergyRelatedInventory`, `InventoryCrudeOilProductsAndMerchandise`).

### Integrity checks (data-quality score)

Beyond required-field and accounting-identity checks, the quality layer runs five
**flag-only** integrity checks (they surface issues via findings + the 0–100 score; they
never alter data):

| Check | Catches | Threshold | Penalty |
|---|---|---|---|
| Magnitude outlier | a recurring USD field that spikes then reverts — a one-off filing/tag error (persistent M&A step-changes, and event-driven flows like debt issuance, buybacks, M&A, and impairments, are not flagged) | spike ≥ 100× both adjacent years | −25 |
| Cash-flow consistency | the cash-flow statement's reported net change != its own sections + FX effect (a continuing-only statement reconciles once the separately-reported `cash_from_discontinued_operations` line is added) | residual > 1% both bases | −10 |
| Quarterly-sum | discrete quarters that don't sum to the annual figure; event-driven flows (debt issuance/repayment, buybacks, M&A, impairments/restructuring) are excluded, as for the magnitude-outlier check | per-field > 1% | −10 |
| Ratio bounds | a computed metric outside its plausible range (e.g. >100% gross margin); roe/roic are not flagged when the equity base is negligible or negative (a buyback-depleted denominator makes the ratio meaningless, not wrong) | impossibility bounds | −3 |
| Dividend completeness | Yahoo's dividend history missing payments that SEC filings declare: a gap in the payment schedule that begins near the fiscal year, and Yahoo's payments for that year well short of the SEC-declared dividends per share (a December declaration paid in January, a suspension SEC confirms, a first dividend paid after the year ends, and years before a split are not flagged) | interval > 1.7× the usual one and payments < 80% of declared | −10 |

Thresholds are deliberately wide (a $1M materiality floor; the most recent 5 fiscal years are
scored), so clean filings keep a score of 100. Findings appear in `data_quality.findings` and,
for medium+ severity, in `warnings`.

### Fiscal vs calendar year

Companies have different fiscal year-ends (Microsoft June, Apple September, Walmart
January), so comparing by fiscal-year number mixes different macroeconomic windows. Each
period therefore carries two labels:

- `fiscal_year` — the company's own fiscal year, derived from the period-end date: its
  calendar year, minus one for a 52/53-week year-end that lands in early January (e.g. JNJ's
  year ending 2023-01-01 is FY2022). January-end retailers (e.g. WMT, Jan 31) keep the end
  year. SEC's `fy` field is deliberately not used — filers mis-stamp it.
- `calendar_year` (and `calendar_quarter`) — the macro-aligned year taken from SEC's
  XBRL `frame` context flag (e.g. MSFT FY2025, AAPL FY2025, WMT FY2026 and every December
  filer's FY2025 all map to `calendar_year = 2025`; NVIDIA's January "FY2022" → 2021).

**Always compare across companies with `calendar_year`, not `fiscal_year`.**

### Granular quarterly history

The pipeline extracts the **full** available quarterly history (no year cap by default;
limit with `--years N`) and stores true 3-month figures, not the cumulative year-to-date
numbers SEC filings report:

- **Discrete quarters via ladder differencing.** Income and cash-flow statements are
  filed cumulative-to-date in 10-Qs (and full-year in the 10-K). We difference the
  cumulative ladder anchored at the fiscal-year start (`discrete[k] = YTD[k] − YTD[k-1]`),
  which recovers every quarter's standalone performance — including **Q4**, which is never
  filed as a 10-Q (`Q4 = annual − 9-month YTD`). Until a year's second 10-Q, its first
  quarter is its own 3-month figure. Derived values are tagged in `_source_tags`.
- **One split basis per year.** A stock split restates only the filings made after it,
  so a year's figures can come from both sides of one. Each quarter's per-share figures
  and share counts are put on the basis of its year's annual figures, as
  `financials_annual` has them, before Q4 is derived. The parser reads the splits from
  the filings themselves: counts that two filings give for the same period at a split's
  ratio. Years that no filing after a split reports keep their older basis, as in
  `financials_annual`.
- **One share-count scale per year.** A later filing can tag a year's share count in
  thousands or millions where its other figures are in units. Before Q4 is derived,
  each weighted count goes onto the scale its own net income and EPS imply, provided
  another of the year's counts agrees; otherwise it follows the year's checked counts.
  A count at or below zero, filed or derived, is left empty.
- **Outflows by size.** Capex, buybacks, debt repaid and dividends paid are stored as
  positive amounts, and filers tag them with either sign, sometimes both within one year.
  Their quarters difference the year-to-date totals' sizes, and a quarter whose total
  shrinks (a later filing reporting the flow on another basis) is left empty.
- **Acquisitions as paid.** Acquisitions are net of the cash acquired, so they keep their
  sign: positive is cash paid, negative cash brought in, where a deal acquired more cash
  than it cost (Wells Fargo's 2008 with Wachovia, -$11.2B). A filing that tags its other
  outflows negative has its acquisitions turned back. Their quarters difference the
  year-to-date totals as they are, so a year's quarters add up to its figure; but a total
  of payments that falls by half or more is two bases (a restated year beside nine months
  as first filed), and that quarter is left empty.
- **Long-term debt beside current debt.** Long-term debt is the balance sheet's noncurrent
  figure where the filing's short-term debt holds the current maturities (Boeing's FY2025:
  $45.6B beside $8.5B of current debt, not its $53.8B total, which counted them twice), and
  the filer's total with the maturities where no short-term figure holds them.
- **Short-term debt and commercial paper.** Short-term debt is the balance sheet's: where a
  filing's debt due within a year is the figure before a refinancing moved part of it to
  long-term debt, the short-term borrowings its balance sheet shows stand instead (Chevron's
  FY2024: $4.4B, not $12.7B, whose other $8.25B its long-term debt holds). Commercial paper is
  stored only where the short-term figure doesn't hold it (a line of its own, like Apple's), so
  total debt counts it once (P&G's FY2026: $34.1B, not $39.0B), and only on balance-sheet dates.
- **Balance-sheet checkpoints** at every quarter-end (instant facts), full history.
- **`fiscal_quarter`** (1–4, company calendar) and **`calendar_quarter`** (1–4, calendar)
  are both attached; `calendar_quarter` comes from the SEC frame and is the reliable
  cross-company key.
- **TTM (`financials_ttm`)** — trailing-twelve-month series (rolling 4 discrete quarters
  for flows, their average for weighted share counts, balance sheet as-of, and per-share
  figures and counts on the latest quarter's split basis) for seasonality-free, up-to-date
  comparison.

```sql
-- Discrete quarterly revenue trend for one company (incl. derived Q4):
SELECT period_end, fiscal_year, fiscal_quarter, calendar_quarter, revenue, operating_cash_flow
FROM financials_quarterly WHERE ticker = 'AAPL' ORDER BY period_end DESC LIMIT 8;

-- Latest trailing-twelve-month revenue per company:
SELECT ticker, MAX(period_end) AS asof, revenue FROM financials_ttm GROUP BY ticker;
```

### Taxonomy evolution & tag variability

The SEC US-GAAP taxonomy changes over time and filers tag identical items differently
(`us-gaap:Revenues` vs `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`). The
pipeline is designed so this never breaks ingestion or silently loses data:

- **Tags are data, not schema.** Tables use canonical columns and inserts are keyed by
  concept, never by raw tag — a taxonomy change can't break the schema or queries.
- **Firm variability is resolved** by ordered candidate-tag lists in
  `src/mappings/canonical.py` (both revenue tags → `revenue`).
- **One revenue figure per year.** Revenue takes the first of its tags a year has, the
  totals first (`Revenues`, then revenue net of interest expense, then the contract-revenue
  and sales tags). Of two figures for a year 25% or more apart, one the year's income
  statement doesn't add up to (operating income plus operating expenses, or cost of revenue
  plus gross profit) gives way to one it does; where it adds up to neither, a zero gives way
  to a positive figure, and one below the year's cost of revenue to one that isn't. Revenue
  including excise taxes only fills a year with no other figure.
- **Nothing is silently dropped.** Any *material* fact under a tag not yet in the registry
  (a new taxonomy element or an unanticipated tag) is captured in the **`unmapped_facts`**
  table (the tag stored as a row value) — so the data is preserved and visible even before
  it's mapped.
- **Evidence-based maintenance loop.** `python coverage_report.py` prints the top unmapped
  tags ranked by how many companies use them; promote frequent ones into
  `CANONICAL_FIELDS` and re-run. `python diagnose.py TICKER` shows a company's unmapped count.

```sql
-- Tags worth mapping next (most widely used, still unmapped):
SELECT tag, COUNT(*) AS companies, MAX(ABS(value)) AS max_value
FROM unmapped_facts GROUP BY tag ORDER BY companies DESC, max_value DESC LIMIT 20;
```

## Cross-Company Screening (SQLite)

With `sqlite` output enabled (on by default), all tickers are written to a single
queryable database at `data/output/stock.db` with consistent, canonical columns —
so you can screen across your whole universe (use `calendar_year` to align fiscal
calendars):

```sql
-- High-quality compounders for the SAME macro year (across fiscal calendars):
-- strong returns, modest leverage.
SELECT f.ticker, f.fiscal_year, f.calendar_year, m.roic, m.net_margin, m.debt_to_ebitda
FROM financials_annual f
JOIN metrics_annual m ON f.ticker = m.ticker AND f.fiscal_year = m.fiscal_year
WHERE f.calendar_year = 2025 AND m.roic > 0.15 AND m.debt_to_ebitda < 3
ORDER BY m.roic DESC;
```

Tables: `companies`, `financials_annual`, `financials_quarterly`, `financials_ttm`,
`financials_annual_vintages`, `metrics_annual`, `market_snapshots`, `collection_runs`,
plus (added for the web terminal — see [Web interface](#web-interface)) `price_bars`
(daily OHLCV, also used for the `^GSPC` S&P 500 benchmark), `analyst_snapshots`
(point-in-time price targets/recommendations), `earnings_history` (EPS
estimate/actual/surprise per quarter), `dividend_events`, `split_events`,
`holders` (institutional + mutual-fund), `insider_transactions`, and `officers`; and the valuation
layer's `valuations`, `valuation_summary`, `valuation_history` and `valuation_ranges` (see
[Valuation history](#valuation-history)); and the quality scores' `per_share_annual`,
`quality_scores` and `warning_signs` (see [Quality scores](#quality-scores)); and the other valuation indicators'
`valuation_indicators` (see [Other valuation indicators](#other-valuation-indicators)); and the SEC ownership tables
`insider_filings`, `insider_trades`, `guru_filers`, `guru_filings`, `guru_holdings`, `cusip_map`,
`guru_quarters` and `guru_positions` (see [SEC ownership](#sec-ownership-insiders-and-gurus)); and the market data's
`market_series` and `market_fetches` (see [Market data](#market-data)); and `stock-data refresh`'s `refresh_log` (see
[Keeping the data current](#keeping-the-data-current-stock-data-refresh)). `companies` also holds each company's SEC
`sic` and `sic_description` (see [SIC codes](#sic-codes)):

```sql
-- Cheap against their own history: P/E in the bottom quarter of its last 10 years,
-- and the price under 0.9x the historical value.
SELECT r.ticker, r.current_value AS pe, r.pct_rank, r.median AS pe_median_10y,
       m.current_price / v.current_value AS price_to_value
FROM valuation_ranges r
JOIN valuation_ranges v ON v.ticker = r.ticker AND v.metric = 'fair_value' AND v.applicable = 1
JOIN market_snapshots m ON m.ticker = r.ticker
 AND m.collected_at = (SELECT MAX(collected_at) FROM market_snapshots WHERE ticker = r.ticker)
WHERE r.metric = 'pe' AND r.applicable = 1 AND r.pct_rank <= 0.25
  AND m.current_price / v.current_value < 0.9
ORDER BY r.pct_rank;
```

`market_snapshots` itself was widened with ~40 point-in-time market/valuation
columns (P/E, PEG, price/book, debt/equity (Yahoo's, a percent), short interest, 52-week range,
moving averages, …) — see `SNAPSHOT_SCREEN_COLUMNS` in `src/webapp/screener.py`
for the subset exposed to the screener. All writes are idempotent upserts, so
re-runs update in place: a ticker's run that parses its annual record rewrites every period it
gives, so a figure the current rules leave empty is cleared, while periods it doesn't give and a
filer with no 10-K yet (a new registrant) keep what's stored. A ticker SEC now maps to a
successor formed to take a registrant's place reads the predecessor's filings too
(`sec_handler.PREDECESSOR_CIKS`), up to the successor's first report: XOM is ExxonMobil Holdings
Corp's 10-Qs with Exxon Mobil Corp's history, BLK BlackRock, Inc.'s reports since 2024 with the old
BlackRock's (now BlackRock Finance, Inc.), and the run notes both. `dividend_events` keeps each payment as Yahoo served it,
split-adjusted as of `served_on`: the collection's day on Yahoo's calendar, its last
daily bar or the latest split Yahoo listed, whichever is later (the collector's own date
can run a day ahead of New York's). The valuation takes each payment to its price's
split basis by the splits in between, so a run whose dividend request failed can still
store a split.

The `financials_annual_vintages` table stores **point-in-time** data: one row per
(ticker, fiscal_year, filing accession), so you can see every filing's view of a year —
the original and each later restatement — keyed by `filed_date`. This is sub-project 1 of
the no-look-ahead point-in-time work (point-in-time metrics follow). Vintages are
SQLite-only (not in the per-ticker JSON).

### Point-in-time as-of queries

To read fundamentals **as they were known on a given date** (no look-ahead), take each
figure from the latest filing made on or before that date that reports it. A 10-K repeats
only part of each earlier year (two years of income and cash-flow statements, one balance
sheet, the equity statement's opening balances), so a later filing's row for a year is
mostly empty, and a figure it leaves out keeps the value last filed. In SQL, for one figure:

```sql
-- Net income for FY2019 as it was known on 2020-06-30:
SELECT net_income, filed_date, accn FROM financials_annual_vintages
WHERE ticker = 'PRU' AND fiscal_year = 2019 AND filed_date <= '2020-06-30'
  AND net_income IS NOT NULL
ORDER BY filed_date DESC, accn DESC LIMIT 1;
```

Or in Python via `AsOfReader`:

```python
from src.query.asof import AsOfReader

with AsOfReader("data/output/stock.db") as r:
    period = r.as_of_annual("PRU", 2019, "2020-06-30")   # full period dict, or None
    ni = r.as_of_value("PRU", 2019, "net_income", "2020-06-30")
    history = r.history_as_of("PRU", "2020-06-30")        # {fiscal_year: period}, newest first
```

`as_of_date` accepts an ISO `YYYY-MM-DD` string or a `datetime.date`. A year not yet filed
as of that date resolves to `None` (and is absent from `history_as_of`). A period carries the
latest filing's `accn`, `filed_date` and `form`, and `carried_from` names the filing of each
figure last filed earlier (`{"revenue": {"accn": ..., "filed_date": ..., "form": ...}}`).

For point-in-time **ratios** (computed on the as-of financials), use `PointInTimeMetrics`:

```python
from src.query.pit_metrics import PointInTimeMetrics

with PointInTimeMetrics.from_path("data/output/stock.db") as pm:
    roe = pm.metric_as_of("PRU", 2010, "roe", "2012-06-30")   # ROE as known on that date
    m = pm.metrics_as_of("PRU", 2010, "2012-06-30")           # full ratio dict, or None
    hist = pm.metrics_history_as_of("PRU", "2012-06-30")      # {fiscal_year: metrics}
```

Sector is auto-read from the `companies` table, so banks/insurers/REITs get their sector
ratios (e.g. `net_interest_margin`) with inapplicable generic ratios suppressed. Only
**fundamental** ratios are produced — valuation/EV ratios (`ev_to_ebitda`, `fcf_yield`, …)
are left to the [valuation history](#valuation-history), which pairs each month's close
(`price_bars`) with the trailing year known that month.

## Development

Install with the dev extras and run the test suite (no network access required —
all external APIs are mocked):

```bash
pip install -e ".[dev]"
pytest
```

The tests cover the financial-metric calculations, XBRL fiscal-year extraction,
the data model's serialization round-trips, JSON merge semantics, and the
rate-limiter/retry logic.

### Scale tools

`qa/scale/` checks the pages at about 500 companies before the S&P 500 is collected, on a synthetic copy of the
database (it needs the package and the web and dev extras: `pip install -e ".[dev,web]"`):

```bash
# 127 companies cloned three times under made-up tickers (AAPLQ1, AAPLQ2, AAPLQ3, ...): about 508 companies
python qa/scale/synthetic_universe.py --source data/output/stock.db --out ../synthetic.db

# Every page and API a user hits, in process, against its gate
python qa/scale/page_timings.py --db ../synthetic.db
```

- `synthetic_universe.py [--copies 3]` copies the source with SQLite's backup API from a read-only connection (the source
  is never written, and `--out` must not exist), then clones every row of every table with a ticker column in one
  transaction. Each clone's ticker must be one the app accepts as it stands and none of the universe files' or the
  database's, else the run stops and removes the copy (exit 1). The Form 4 accessions get a `-Q<k>` suffix so keys stay
  unique, a clone's company name gets `[Q<k>]` appended, and the gurus' positions in our companies are cloned onto the
  clones; the tables without a ticker column and the `^GSPC` bars are left as they are. It prints each table's rows
  before and after, an integrity check and the copy's size.
- `page_timings.py [--runs 5] [--tickers LIST]` runs the app in process through FastAPI's `TestClient` (no server; the
  database opened read-only; collection and the quote refresh off), requests each path once cold and then `--runs`
  times, and prints each path's status, size and median, max and cold times (ms), flagging a median over its gate: 500
  ms for the screener's results (`/ui/screen`, `/api/screen` and the CSV export), 1,000 ms for every other page and API.
  The paths are the home page, the companies list, the screener (the default, five heavy sorts, the largest 2-digit
  industry, a preset and the CSV export), every tab of the heaviest stocks, the gurus pages, the Market and quality
  pages and the as-of pages. Exit 0 when every median is within its gate and no response is a 5xx, 1 otherwise, and 2
  when the database file changed (its SHA-256 and its WAL's are compared before and after).

On 2026-10-25, at 508 synthetic companies (710 MB), every page and API answered under 1 s and the screener's results
under 0.5 s. Two indexes cut the heaviest queries: `idx_insider_trades_code` on `insider_trades (ticker, code, date)`
for the screener's insider fields and `idx_unmapped_tag_cover` on `unmapped_facts (tag, ticker, label)` for the quality
page's top unmapped tags. The store's schema pass creates them at a database's next write (a collection, a refresh, a
backfill: 0.25 s and 5.9 MB at 127 companies, 1.16 s and 24.6 MB at 508). With them the screener's results run under 180
ms and the quality page in about 150 ms (649 ms before), and every row the queries read came out the same
(`qa/evidence/scale-m5-4-ab.txt`). Run `page_timings.py` on the real database after the S&P 500 collection to check the
same gates there; `tests/test_scale_preflight.py` and `tests/test_scale_indexes.py` cover the tools and the indexes.

## Limitations

- **US Stocks Only**: SEC EDGAR only covers US-listed companies
- **Free Data Sources**: No premium data feeds (Bloomberg, Refinitiv, etc.)
- **Rate Limits**: SEC allows 10 req/sec; we're conservative at ~8 req/sec
- **Historical Depth**: SEC XBRL data typically available from 2009+
- **Real-Time Data**: Yahoo Finance data may have 15-20 minute delay
- **Ownership Data**: Form 4 history is 3 years and 13F history 8 quarters; a 13F reports only long
  positions, up to 45 days after its quarter ends (see [Ownership limitations](#ownership-limitations))

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Web interface

The stock-database ships with a FastAPI web interface for browsing data interactively.

### Install

```bash
# Web interface only
pip install -e ".[web]"

# For development (tests + web)
pip install -e ".[dev,web]"
```

### Run

```bash
stock-web
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

The server binds to `127.0.0.1` by default (localhost only).

### Features

- **Terminal command bar** — a Bloomberg-style `TICKER CODE` command line (focus with `` ` `` or `/`) that jumps straight to any workstation tab or global tool. See [Terminal command bar](#terminal-command-bar) below.
- **Company workstation** — a single per-ticker page (`/stocks/{ticker}`) with 10 tabs: description/overview (with the SEC's industry), price chart with indicators, financial statements, earnings, key stats (with the quality scores, and their ranks within the industry), historical prices, dividends, holders (the gurus' 13F positions above Yahoo's holders), insider activity (the SEC's Form 4 trades with their aggregates), and valuation (with the other valuation indicators).
- **Gurus** — `/gurus` lists the 34 gurus at their latest 13F quarter, `/gurus/{cik}` shows a guru's holdings with weights, the changes from the quarter before and its filings, and `/gurus/portfolio` what they hold together. See [SEC ownership](#sec-ownership-insiders-and-gurus).
- **Market** — `/market` charts the Buffett indicator against its long-run trend, Shiller's CAPE against its history and the Treasury yield curve against a year earlier with its 10-year spreads and their inversions, from the series `python -m src.market.collect` stores. Without them it says how to collect. See [Market data](#market-data).
- **Dashboard** — data quality score, field coverage bar chart, freshness summary, unmapped-fact tracker.
- **Companies browser** — filterable list of all tracked companies with sector drill-down.
- **Cross-company screener** — filter by any fundamental metric (ROIC, net margin, debt/EBITDA, …) *or* market/valuation column (P/E, dividend yield, PEG, short interest, …) *or* the valuation layer's Upside % / verdict *or* the quality scores (Piotroski F, Altman Z, the ranks and composite, …, with five preset screens) *or* the other valuation indicators (the reverse DCF's implied growth, the upsides to the EPV, the median P/S value and the tangible Graham number, price / net-net working capital) *or* the insiders' open-market trades and the gurus' holdings (`ins_*`, `gurus_holding`, `gurus_buying`) *or* the SEC's industry (a SIC prefix filter) and the ranks within it (`ind_*`), sort, paginate, compare selected companies side-by-side, and download results as CSV.
- **Point-in-time (as-of) explorer** — look up what financials and ratios looked like as of any historical date (before later restatements), and browse vintage side-by-side tables.
- **On-demand quote refresh** — re-fetch a single ticker's live price/market snapshot from the workstation without running a full collection (see `STOCK_WEB_ALLOW_QUOTE_REFRESH` below). After a stock split the stored valuations don't reflect (one dated after the stored price, as after a collection whose Yahoo quote failed, or one not yet stored since the ticker's last collection with Yahoo), the refresh isn't saved, because those valuations are on the pre-split basis; its status asks for a full collection of the ticker instead. A split here is a share split (a simple ratio such as 2:1, 5:4 or 1:10) or any price factor of 3:2 or more. The stock dividends and smaller spin-off factors Yahoo also lists don't stop a refresh: it stores them with the price and values the ticker again, so its DDM and multiples band move onto the new price's basis, as a full collection would put them. A saved refresh recomputes the snapshot's enterprise value, EV/EBITDA, EV/FCF and FCF yield as a full collection does (its new market cap with the latest year's net debt, EBITDA and FCF), so the columns keep one definition; Yahoo's own enterprise value stays in the JSON's `valuation` section.
- **CSV export** — download annual financials, annual metrics, or screener results as CSV from any company page or screener view.

### Terminal command bar

Type a ticker and a function code into the command bar (e.g. `AAPL GP` ⏎) to jump directly to a workstation tab. A ticker alone defaults to `DES`. Autocomplete suggests tickers as you type.

Per-ticker function codes (`TICKER CODE` → `/stocks/{TICKER}?tab=...`):

| Code | Tab | Shows |
|---|---|---|
| `DES` | Description | Company overview, profile, the SEC's industry (SIC code), officers (default) |
| `GP` | Graph | Price chart with MA50/MA200/RSI/MACD indicators and `^GSPC` overlay |
| `FA` | Financial analysis | Annual/quarterly/TTM statements |
| `ERN` | Earnings | Earnings-surprise history (EPS estimate vs. actual) |
| `STAT` | Key statistics | Valuation & market snapshot (P/E, market cap, beta, …) and the Quality section (composite, ranks with the industry's beside them, scores, warning signs) |
| `HP` | Historical prices | Daily OHLCV price table |
| `DVD` | Dividends | Dividend payments and stock splits |
| `HDS` | Holders | The gurus' 13F positions in the stock (a Gurus section), then Yahoo's institutional and mutual-fund holders |
| `INS` | Insiders | The SEC's Form 4 trades with open-market, CEO/CFO and cluster-buy aggregates (Yahoo's rows where none is stored) |
| `VAL` | Valuation | Fair-value ranges per model (DCF/DDM/Graham/Lynch/multiples), assumptions, DCF sensitivity, and the other valuation indicators (reverse DCF, EPV, NCAV and net-net, median P/S value, tangible Graham number) |

Global codes (no ticker needed):

| Code | Destination |
|---|---|
| `SCR` | Cross-company screener (`/screener`) |
| `ASOF` | Point-in-time as-of explorer (`/asof`) |
| `QM` | Data-quality dashboard (`/quality`) |
| `COL` | Collection page (`/collect`) |
| `HELP` | Toggles the in-app command-code cheat sheet |

The legacy `/companies/{ticker}` route still works — it redirects (307) to `/stocks/{ticker}?tab=fa`.

### JSON API

An interactive OpenAPI interface is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
All programmatic endpoints are under the `/api/` prefix.

Key endpoints:

| Endpoint | Description |
|---|---|
| `GET /api/companies` | Paginated company list (filter by sector/search) |
| `GET /api/companies/{ticker}` | Company overview |
| `GET /api/companies/{ticker}/peers` | Peer benchmarking vs sector median |
| `GET /api/companies/{ticker}/financials/annual` | Annual financials |
| `GET /api/companies/{ticker}/metrics` | Annual metrics |
| `GET /api/stocks/{ticker}/valuation` | Per-model fair-value ranges, assumptions, verdict, upside % |
| `GET /api/stocks/{ticker}/valuation-history` | Valuation history: the historical value against the price, each multiple's 10-year range, the monthly series |
| `GET /api/stocks/{ticker}/valuation-indicators` | The other valuation indicators (reverse DCF, EPV, NCAV and net-net, median P/S value, tangible Graham number): bear/base/bull, the base's upside from the live price, notes, N/A reasons, and the reverse DCF solved at the live price (not `/indicators`, the price chart's MA/RSI/MACD bundle) |
| `GET /api/stocks/{ticker}/scores` | Quality scores: Piotroski with its nine tests, Altman, Beneish, Sloan, ROIC vs WACC, predictability, the five ranks and the composite, the same within the industry group (`industry`), the growth table, the warning signs |
| `GET /api/stocks/{ticker}/form4` | Insiders' trades from the SEC's Form 4 filings with their aggregates (open-market buying and selling over 3, 6 and 12 months, CEO/CFO buys, cluster buys, sales under a 10b5-1 plan); Yahoo's rows where none is stored |
| `GET /api/stocks/{ticker}/gurus` | The gurus holding the stock or its other share class in their latest 13F quarter, with the quarter's change, then the ones that sold it out |
| `GET /api/gurus` | Each guru at its latest quarter: the total value, the positions, the new and sold-out ones |
| `GET /api/gurus/portfolio` | The gurus' aggregated portfolio: per security the gurus holding it, their summed shares and value, the buyers and sellers (`limit`, default 200) |
| `GET /api/gurus/{cik}` | One guru's quarter (`period` for an earlier one): holdings with weights and changes, the changes grouped, the filings; 404 if not stored |
| `GET /api/market` | The market page's data: the Buffett indicator against its long-run trend (ratio, trend, z, zone), Shiller's CAPE against its history, the yield curve against a year earlier with the 10-year spreads and their inversions, and each section's sources; 200 with a section unavailable (and the command that collects it) before the data is collected |
| `GET /api/screen` | Cross-company screener (supports `verdict=cheap\|fair\|expensive` and `industry=<2- or 3-digit SIC prefix>`, e.g. `industry=367`) |
| `GET /api/export/company/{ticker}/annual.csv` | Annual financials as CSV |
| `GET /api/export/company/{ticker}/metrics.csv` | Annual metrics as CSV |
| `GET /api/export/screen.csv` | Screener results as CSV |
| `GET /api/health` | Health check |

### Data collection

Data collection from the web UI is enabled by default. The workstation provides two ways to update data:

1. **UPDATE DATA button** — click on any stock page to fetch and recompute valuations for a single company.
2. **Unknown-ticker fetch** — type an unknown ticker into the command bar (`` ` `` or `/`) to open a fetch page and collect that company.

Valuations are recomputed automatically after each fetch. To disable data collection (e.g. in read-only deployments), set the `STOCK_WEB_ALLOW_COLLECTION` environment variable to a falsy value:

```bash
STOCK_WEB_ALLOW_COLLECTION=0 stock-web
```

Supported settings:

| Variable | Default | Description |
|---|---|---|
| `STOCK_WEB_ALLOW_COLLECTION` | `True` | Set to `0`, `false`, `no`, or `off` to disable full collection (hides UPDATE DATA button and unknown-ticker fetch). |
| `STOCK_WEB_ALLOW_QUOTE_REFRESH` | `True` (**enabled**) | Set to `0`/`false`/`no`/`off` to disable the on-demand single-ticker quote refresh (unlike collection, this is opt-out — refresh is low-risk/low-cost). Gates `POST /api/stocks/{ticker}/refresh-quote`. |
| `STOCK_WEB_HOST` | `127.0.0.1` | Bind host |
| `STOCK_WEB_PORT` | `8000` | Bind port |
| `STOCK_WEB_DB_PATH` | default DB path | Path to the SQLite database |

To refresh data without the web UI or to backfill historical valuations, use the `stock-data` CLI as described above;
`stock-data refresh` keeps every company current (see
[Keeping the data current](#keeping-the-data-current-stock-data-refresh)).

Because the workstation's ERN/HP/DVD/HDS/INS tabs and the `^GSPC` chart overlay
need price history, analyst estimates, earnings history, dividends/splits, and
shareholder data, a full collection now makes roughly 6 extra Yahoo Finance
calls per ticker (`history()`, `dividends`, `splits`, `institutional_holders`,
`mutualfund_holders`, `insider_transactions`) plus one benchmark call
(`^GSPC` price history) made once per collection run, not per ticker.

The INS tab's SEC trades and the HDS tab's Gurus section aren't part of a collection: they read what
`python -m src.ownership.collect` (or `stock-data refresh`'s weekly steps) stores (see
[SEC ownership](#sec-ownership-insiders-and-gurus)); without it, INS shows Yahoo's rows and HDS no Gurus section. The
Market page isn't either: it reads what `python -m src.market.collect` (or `stock-data refresh`'s daily step) stores
(see [Market data](#market-data)); without it, each section says which command collects its series.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This tool is for educational and research purposes only. The data collected should not be used as the sole basis for investment decisions. Always verify data accuracy and consult with qualified financial professionals before making investment decisions.

---

**Built with Python** | **Free Data Sources Only** | **Valuation Model Ready**
