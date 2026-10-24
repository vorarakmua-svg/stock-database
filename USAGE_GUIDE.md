# Stock Data Usage Guide

A comprehensive guide on how to use the output data from the Stock Data Collection System for financial analysis, valuation models, and investment research.

---

## Table of Contents

1. [Output Files Overview](#output-files-overview)
2. [Loading Data in Python](#loading-data-in-python)
3. [Working with JSON Data](#working-with-json-data)
4. [Working with CSV Data](#working-with-csv-data)
5. [Valuation Layer](#valuation-layer) — built-in, stored fair-value models
   - [Valuation history](#valuation-history) — monthly multiples, 10-year ranges, the historical value
   - [Quality scores](#quality-scores) — Piotroski F, Altman Z, Beneish M, ranks, warning signs
   - [Other valuation indicators](#other-valuation-indicators) — reverse DCF, earnings power value, NCAV and net-net, median P/S value, tangible Graham number
   - [Owner Earnings ("Buffett mode")](#owner-earnings-buffett-mode) — separate lens, excluded from the median
6. [SEC Ownership](#sec-ownership) — insiders' Form 4 trades and the gurus' 13F holdings
   - [Collecting ownership data](#collecting-ownership-data) — the commands and what each touches
   - [Insiders (Form 4)](#insiders-form-4) — `insider_filings`, `insider_trades` and queries
   - [Gurus (13F)](#gurus-13f) — the six 13F tables, what a position's change means, and queries
   - [Viewing ownership](#viewing-ownership) — the web pages, the API and the screener fields
7. [Market Data](#market-data) — FRED's corporate equities and GDP, Shiller's CAPE and the Treasury yield curve
   - [Collecting market data](#collecting-market-data) — the command, its cache and its exit codes
   - [The market series](#the-market-series) — `market_series`, `market_fetches` and queries
   - [Viewing market data](#viewing-market-data) — the Market page, the API and how to read each section
8. [Updating Data from the Web](#updating-data-from-the-web) — UPDATE DATA button, fetching new tickers
9. [Valuation Models](#valuation-models) — DIY formulas over the raw JSON/CSV
   - [DCF (Discounted Cash Flow)](#dcf-discounted-cash-flow)
   - [Graham Number](#graham-number)
   - [Peter Lynch Fair Value](#peter-lynch-fair-value)
   - [Buffett-Style Analysis](#buffett-style-analysis)
10. [Financial Analysis Examples](#financial-analysis-examples)
11. [Building Dashboards](#building-dashboards)
12. [Data Quality Checks](#data-quality-checks)
13. [Common Use Cases](#common-use-cases)

---

## Output Files Overview

After running the data collection:

```
data/output/
├── json/
│   ├── AAPL.json     # Complete data for Apple
│   ├── MSFT.json     # Complete data for Microsoft
│   └── GOOGL.json    # Complete data for Alphabet
└── csv/
    └── summary.csv   # Summary metrics for all tickers
```

| File Type | Use Case | Best For |
|-----------|----------|----------|
| JSON | Deep analysis, historical data, all fields | Single stock analysis, ML models |
| CSV | Quick comparison, screening, dashboards | Multi-stock comparison, Excel |

---

## Loading Data in Python

### Loading JSON Data

```python
import json
from pathlib import Path

def load_stock_data(ticker: str, data_dir: str = "data/output/json") -> dict:
    """Load complete stock data from JSON file."""
    file_path = Path(data_dir) / f"{ticker.upper()}.json"
    with open(file_path, 'r') as f:
        return json.load(f)

# Load single stock
aapl = load_stock_data("AAPL")

# Load multiple stocks
tickers = ["AAPL", "MSFT", "GOOGL"]
stocks = {ticker: load_stock_data(ticker) for ticker in tickers}
```

### Loading CSV Summary

```python
import pandas as pd

def load_summary(data_dir: str = "data/output/csv") -> pd.DataFrame:
    """Load summary CSV with proper data types."""
    df = pd.read_csv(
        f"{data_dir}/summary.csv",
        parse_dates=['collected_at']
    )
    return df

# Load and display
summary = load_summary()
print(summary[['ticker', 'company_name', 'market_cap', 'pe_trailing']].head())
```

---

## Working with JSON Data

### Data Structure Overview

```python
# Top-level keys in JSON
stock = load_stock_data("AAPL")

print("Available sections:")
for key in stock.keys():
    print(f"  - {key}")

# Output:
# - ticker
# - cik
# - company_name
# - sector_class
# - collected_at
# - company_info
# - market_data
# - valuation
# - shareholders
# - yahoo_financials
# - financials_annual
# - financials_quarterly
# - financials_ttm
# - sec_submissions
# - insider_transactions
# - unmapped_facts
# - price_history
# - analyst_estimates
# - dividend_history
# - price_bars
# - earnings_history
# - splits
# - calculated_metrics
# - risk_free_rate
# - data_quality
# - data_sources
# - warnings
# - errors
```

### Accessing Company Information

```python
stock = load_stock_data("AAPL")

# Basic info
print(f"Company: {stock['company_name']}")
print(f"Sector: {stock['company_info']['sector']}")
print(f"Industry: {stock['company_info']['industry']}")
print(f"Employees: {stock['company_info']['full_time_employees']:,}")
print(f"Website: {stock['company_info']['website']}")
print(f"Description: {stock['company_info'].get('description', 'N/A')[:200]}...")
```

### Accessing Market Data

```python
market = stock['market_data']

print(f"Current Price: ${market['current_price']:.2f}")
print(f"Market Cap: ${market['market_cap']:,.0f}")
print(f"52-Week High: ${market['fifty_two_week_high']:.2f}")
print(f"52-Week Low: ${market['fifty_two_week_low']:.2f}")
print(f"50-Day MA: ${market['fifty_day_average']:.2f}")
print(f"200-Day MA: ${market['two_hundred_day_average']:.2f}")
print(f"Beta: {market['beta']:.2f}")
```

### Accessing Historical Financials (SEC Data)

```python
# Get all available years
annual = stock['financials_annual']
years = sorted(annual.keys(), reverse=True)

print(f"Available years: {', '.join(years)}")

# Get latest year data
latest_year = years[0]
financials = annual[latest_year]

print(f"\n{stock['ticker']} - FY{latest_year}")
print(f"Revenue: ${financials.get('revenue') or 0:,.0f}")
print(f"Net Income: ${financials.get('net_income') or 0:,.0f}")
print(f"Total Assets: ${financials.get('total_assets') or 0:,.0f}")
print(f"Long-Term Debt: ${financials.get('long_term_debt') or 0:,.0f}")
print(f"Operating Cash Flow: ${financials.get('operating_cash_flow') or 0:,.0f}")
```

### Multi-Year Financial Trends

```python
import pandas as pd

def get_financial_trends(stock: dict, metrics: list) -> pd.DataFrame:
    """Extract multi-year financial trends."""
    annual = stock['financials_annual']
    years = sorted(annual.keys())

    data = []
    for year in years:
        row = {'Year': year}
        for metric in metrics:
            row[metric] = annual[year].get(metric)
        data.append(row)

    return pd.DataFrame(data)

# Example: Revenue and Net Income trends
metrics = ['revenue', 'net_income', 'operating_cash_flow']
trends = get_financial_trends(stock, metrics)

# Calculate growth rates
trends['revenue_growth'] = trends['revenue'].pct_change()

print(trends[['Year', 'revenue', 'net_income', 'revenue_growth']])
```

### Accessing Analyst Estimates

```python
estimates = stock['analyst_estimates']

print(f"Number of Analysts: {estimates['number_of_analysts']}")
print(f"Recommendation: {estimates['recommendation']}")
print(f"Target Price (Mean): ${estimates['target_price_mean']:.2f}")
print(f"Target Price (Low): ${estimates['target_price_low']:.2f}")
print(f"Target Price (High): ${estimates['target_price_high']:.2f}")
print(f"Upside Potential: {estimates['upside_potential']*100:.1f}%")
print(f"Expected EPS Growth: {estimates.get('earnings_growth', 0)*100:.1f}%")
```

### Accessing Dividend History

```python
dividends = stock['dividend_history']

print(f"Current Dividend Rate: ${dividends['dividend_rate']:.2f}")
print(f"Dividend Yield: {dividends['dividend_yield']*100:.2f}%")
print(f"Payout Ratio: {dividends['payout_ratio']*100:.1f}%")
print(f"Years of Dividend History: {dividends['years_of_data']:.1f}")
print(f"Dividend CAGR: {dividends.get('dividend_cagr', 0)*100:.1f}%")

# Get annual dividend totals
annual_divs = dividends.get('annual_dividends', {})
for year, amount in sorted(annual_divs.items(), reverse=True)[:5]:
    print(f"  {year}: ${amount:.2f}")
```

---

## Working with CSV Data

### Quick Stock Screening

```python
import pandas as pd

df = pd.read_csv("data/output/csv/summary.csv")

# Screen for undervalued stocks
undervalued = df[
    (df['pe_trailing'] < 20) &
    (df['pe_trailing'] > 0) &
    (df['profit_margin'] > 0.15) &
    (df['debt_to_equity'] < 100)  # a percent: under 1x
]

print("Potentially Undervalued Stocks:")
print(undervalued[['ticker', 'company_name', 'pe_trailing', 'profit_margin']])
```

### Sector Analysis

```python
# Group by sector
sector_stats = df.groupby('sector').agg({
    'market_cap': 'sum',
    'pe_trailing': 'mean',
    'profit_margin': 'mean',
    'return_on_equity': 'mean',
    'ticker': 'count'
}).rename(columns={'ticker': 'count'})

print(sector_stats.sort_values('market_cap', ascending=False))
```

### Export to Excel with Multiple Sheets

```python
with pd.ExcelWriter('stock_analysis.xlsx') as writer:
    # Summary sheet
    df.to_excel(writer, sheet_name='Summary', index=False)

    # Valuation metrics
    valuation_cols = ['ticker', 'pe_trailing', 'pe_forward', 'price_to_book',
                      'peg_ratio', 'calc_ev_to_ebitda']
    df[valuation_cols].to_excel(writer, sheet_name='Valuation', index=False)

    # Growth metrics
    growth_cols = ['ticker', 'revenue_growth', 'cagr_5y', 'return_on_equity']
    df[growth_cols].to_excel(writer, sheet_name='Growth', index=False)
```

---

## Valuation Layer

Unlike the DIY formulas in the next section (which you write yourself against the raw
JSON/CSV), `src/valuation/` is a built-in module that **computes and stores** actual
fair-value estimates in the SQLite database, using only data already collected — no
extra network calls.

### The five models

| Model | Applies to | What it needs |
|---|---|---|
| **DCF** (two-stage FCF) | general, utility, energy | ≥4 years of FCF history, shares outstanding |
| **DDM** (multi-stage Gordon) | any steady dividend payer | ≥3 calendar years of dividend history |
| **Graham Number** | any company with positive EPS + book value | Latest EPS, book value per share |
| **Peter Lynch fair value** | general, utility, energy | ≥4 years of EPS history |
| **Historical multiples band** | all sectors | ≥3 fiscal years of price + sector-appropriate multiple (P/E, P/B for banks/insurers, P/FFO for REITs) |

Each model returns a **bear / base / bull** per-share range, not a single number — growth
and discount-rate assumptions are deliberately conservative (growth = min(historical CAGR,
analyst estimate), CAPM discount rate with a floor/cap). When a model doesn't fit a
company's sector (DCF/Lynch for banks, insurers, and REITs) or the data it needs isn't
available (e.g. no dividend history for DDM), it reports `applicable = false` with a
plain-English `na_reason` — never a fabricated number.

Results are stored in two tables:

- **`valuations`** — one row per (ticker, model): `applicable`, `na_reason`,
  `value_bear`/`value_base`/`value_bull`, the exact `assumptions` used (JSON), and
  `basis_fiscal_year`.
- **`valuation_summary`** — one row per ticker: `n_applicable` and the cross-model
  `median_bear`/`median_base`/`median_bull` (price-independent; verdict and upside % are
  computed at read time against the live price).

### Running the backfill

Valuations recompute **automatically** at the end of every collection run — whether a
`python -m src.main` terminal run or a web-submitted job (see
[Updating Data from the Web](#updating-data-from-the-web)) — for the tickers just
collected, so you normally don't need to run anything extra. To
(re)compute valuations for tickers already in the database without re-collecting data
(e.g. after upgrading the valuation models, or to backfill a ticker collected before the
valuation layer existed):

```bash
# Every company already in the database
python -m src.valuation.backfill

# Explicit database path
python -m src.valuation.backfill --db data/output/stock.db

# Only specific tickers
python -m src.valuation.backfill AAPL MSFT GOOGL
```

This reads only what collection already stored (financials, dividends, price bars,
market snapshots) — it never hits the network. Output looks like:

```
Valuations stored for 50 tickers in data/output/stock.db
```

### Viewing valuations

- **Web UI** — the workstation's **VAL** tab (`/stocks/{ticker}?tab=val`) shows every
  model's bear/base/bull range against the current price, the assumptions behind each
  number, and (when DCF applies) a growth/discount-rate sensitivity grid. The **DES**
  (overview) tab shows an at-a-glance verdict pill (*Looks cheap* / *Fairly valued* /
  *Looks expensive*) and the median upside %.
- **Screener** (`/screener`) — adds a sortable **Upside %** column and a verdict chip per
  row, plus a verdict filter (`cheap` / `fair` / `expensive`).
- **API** — `GET /api/stocks/{ticker}/valuation` returns the full payload (`verdict`,
  `verdict_label`, `upside_pct`, `summary`, and the per-model `models` list with
  `applicable`/`na_reason`/`assumptions`); `GET /api/screen?verdict=cheap` filters the
  screener by verdict; `GET /api/export/screen.csv` includes `median_base` and
  `val_upside_pct` columns.

### Valuation history

Alongside the models, every company gets a **valuation history**. It is computed with the valuations,
so the backfill above fills it too, and holds:
- each month's multiples since its filings begin;
- each multiple's 10-year range;
- a *historical value* drawn against the price.

See the README's [Valuation history](README.md#valuation-history) for the method. Two tables hold it:

- **`valuation_history`**: one row per (ticker, month-end):
  - `close`, `market_cap`, `enterprise_value`, and the trailing year used (`ttm_period_end`);
  - `pe`, `ps`, `pb`, `pfcf`, `ev_ebitda`, `pffo`, `div_yield`;
  - `fair_value` (per share, on the price bars' basis), with `fair_value_n`, the number of multiples it
    combined.
- **`valuation_ranges`**: one row per (ticker, metric) at the latest month:
  - `applicable` / `na_reason`, `window_start`, `n_months`;
  - `current_value`, `pct_rank`, `min`, `p25`, `median`, `p75`, `max`;
  - `implied_value` (the price that metric's 10-year median implies) and `in_fair_value`.

  The historical value itself is the row with `metric = 'fair_value'`: its `current_value`, its range in
  `p25` / `p75`, and `n_metrics`.

```sql
-- AAPL's P/E and historical value over the last ten years, month by month
SELECT date, close, pe, fair_value
FROM valuation_history
WHERE ticker = 'AAPL' AND date >= date('now', '-10 years')
ORDER BY date;

-- Every company's historical value against its latest price, cheapest first
SELECT v.ticker, v.current_value AS historical_value, v.p25 AS low, v.p75 AS high,
       m.current_price, m.current_price / v.current_value AS price_to_value
FROM valuation_ranges v
JOIN market_snapshots m ON m.ticker = v.ticker
 AND m.collected_at = (SELECT MAX(collected_at) FROM market_snapshots WHERE ticker = v.ticker)
WHERE v.metric = 'fair_value' AND v.applicable = 1
ORDER BY price_to_value;
```

It is also shown in three places:
- **Web**: the **VAL** tab's *Historical Value* section, with the label against the live price, the two
  charts and the ranges table.
- **Screener**: the *Historical value* fields, `pe_pct_10y` … `div_yield_pct_10y` (a multiple's
  percentile in its 10 years, as a fraction) and `price_to_hist_value`.
- **API**: `GET /api/stocks/{ticker}/valuation-history`.

It changes none of the models, their median or the verdict.

### Quality scores

Every company also gets **quality scores**: Piotroski F, Altman Z, Beneish M, the Sloan ratio, ROIC vs WACC,
predictability stars, 20 warning signs, five percentile ranks and a composite out of 100. They are computed
with the valuations, so the backfill above fills them too, and any run re-ranks every company. See the
README's [Quality scores](README.md#quality-scores) for the formulas, cutoffs and weights. Three tables hold
them:

- **`per_share_annual`**: one row per (ticker, fiscal_year), the latest 11 years, on today's split basis:
  - `revenue_ps`, `eps`, `ebitda_ps`, `fcf_ps`, `book_ps`.
- **`quality_scores`**: one row per ticker, for its latest `fiscal_year`:
  - the CAGRs, as fractions (NULL where undefined): `rev_ps_cagr_3y`, `rev_ps_cagr_5y`, `rev_ps_cagr_10y`,
    `eps_cagr_3y`, `eps_cagr_5y`, `eps_cagr_10y`, `ebitda_ps_cagr_3y`, `ebitda_ps_cagr_5y`,
    `ebitda_ps_cagr_10y`, `fcf_ps_cagr_5y` and `book_ps_cagr_5y`;
  - `piotroski_f`, `piotroski_tested` (the tests that could run) and `piotroski_tests` (JSON: each test true,
    false or null);
  - `altman_z` and `altman_zone` (`distress`, `grey` or `safe`), `beneish_m` and `beneish_variables` (JSON),
    `sloan_ratio`, `roic`, `wacc` and `roic_minus_wacc`, `debt_to_ebitda` (on the scores' reading of the
    debt: 0 for a filer tagging no debt and paying no interest), `predictability_stars` and
    `predictability_r2` (JSON);
  - `ncav_per_share`, `earnings_yield`, `return_on_capital` and `momentum`;
  - `rank_strength`, `rank_profitability`, `rank_growth`, `rank_valuation` and `rank_momentum` (1-10),
    `composite_score` (out of 100) and `magic_formula_rank`;
  - `components` (JSON: the values each rank is built from), `warning_count`, `na_reasons` (JSON: why a score
    is NULL), `computed_at` and `ranked_at`.
- **`warning_signs`**: one row per (ticker, code) for each sign that fires: `severity` (`severe` or `medium`),
  `message`, `value` and `threshold`.

The JSON columns read with SQLite's JSON functions, e.g. `json_extract(piotroski_tests, '$.cfo_above_ni')` (1
passed, 0 failed, NULL not run).

```sql
-- The ten highest composite scores, with the five ranks behind them
SELECT q.ticker, c.company_name, q.composite_score,
       q.rank_strength, q.rank_profitability, q.rank_growth, q.rank_valuation, q.rank_momentum
FROM quality_scores q
JOIN companies c ON c.ticker = q.ticker
WHERE q.composite_score IS NOT NULL
ORDER BY q.composite_score DESC, q.ticker
LIMIT 10;

-- Piotroski F of 8 or more, out of how many tests could run
SELECT ticker, piotroski_f, piotroski_tested, altman_zone, predictability_stars
FROM quality_scores
WHERE piotroski_f >= 8
ORDER BY piotroski_f DESC, piotroski_tested DESC, ticker;

-- NVDA's warning signs, severe first
SELECT code, severity, message
FROM warning_signs
WHERE ticker = 'NVDA'
ORDER BY CASE severity WHEN 'severe' THEN 0 ELSE 1 END, code;

-- AAPL's per-share series on today's split basis, newest year first
SELECT fiscal_year, revenue_ps, eps, ebitda_ps, fcf_ps, book_ps
FROM per_share_annual
WHERE ticker = 'AAPL'
ORDER BY fiscal_year DESC;
```

It is also shown in three places:
- **Web**: the **STAT** tab's *Quality* section: the composite with a radar of the five ranks, the ranks, each
  score with its reading, the nine Piotroski tests, the growth table and the warning signs, severe first.
- **Screener**: the quality fields (`piotroski_f`, `altman_z`, `composite_score`, `rank_strength` …
  `rank_momentum`, `warning_count` and the rest), `price_to_ncav` and `lynch_upside_pct`, and a *Preset
  screens* dropdown: Buffett-Munger, Peter Lynch, Graham net-net, Magic Formula and Undervalued-predictable
  (the README lists their filters).
- **API**: `GET /api/stocks/{ticker}/scores`.

It changes none of the models, their median or the verdict.

### Other valuation indicators

Every company also gets five **other valuation indicators**: a reverse DCF, the earnings power value (EPV), NCAV with
Graham's net-net, the median P/S value and the Graham number on tangible book. They are computed with the valuations,
after the quality scores whose figures they read, so the backfill above fills them too, and every collection, in the
terminal or the web interface, recomputes them. They are display-only, like owner earnings: none enters the five-model
median, the verdicts or the upside. See the README's
[Other valuation indicators](README.md#other-valuation-indicators) for the formulas and the N/A rules.

How to read them: each answers a different question about the price.

- **Reverse DCF** (`reverse_dcf`): what growth does the price need? It turns the DCF around. On the DCF's own cash flow
  basis, share count, discount rate and terminal growth, it finds the growth of years 1-5 at which the DCF gives the
  price. Read it beside the growth the DCF itself assumes (`dcf_growth_base` in the assumptions). On 2026-10-24 the price
  implied more growth than the DCF assumes for 72 of the 91 companies it applies to (AAPL: 26.5% a year against the
  DCF's 7.1%). It is a rate, so it has no bear, bull or upside, and the search covers -50% to +100% a year: a price
  outside that range is N/A, with the side it is on.
- **EPV** (`epv`): what is the business worth if it never grows? Its normalized after-tax operating earnings over the
  WACC, less debt plus cash and short-term investments. A price above the EPV pays for growth; a price below it is less
  than the business's current earnings are worth with none. On 2026-10-24 only VZ ($44.54) traded under its EPV
  ($55.69); the median company's EPV was a quarter of its price, and D and INTC read negative, their debt exceeding
  their earnings power.
- **NCAV and the net-net** (`ncav`): what are the current assets worth after every liability? NCAV per share is current
  assets less total liabilities, and Graham bought below two thirds of it (`netnet_buy_price` in the assumptions). The
  bear, Graham's net-net working capital, is stricter: it counts receivables at 75% and inventory at half their book
  value. Most large companies are N/A here, their liabilities exceeding their current assets (83 of the 98 with the
  figures). Of the 15 with a positive NCAV, none was near a net-net on 2026-10-24: the lowest price over NCAV was ISRG's
  21.9.
- **Median P/S value** (`ps_value`): what would the price be at the company's own typical sales multiple? The 10-year
  median P/S times the trailing revenue per share, with the 25th and 75th percentile P/S as the bear and bull. It
  compares the price with the company's own history, and it needs 36 months of it. On 2026-10-24 the price was under
  the median P/S value for 45 of the 120 companies it applies to.
- **Graham number on tangible book** (`graham_tangible`): the Graham model's `sqrt(22.5 × EPS × book value per share)`
  with goodwill and intangibles taken out of book value, so a company that grew by acquisition reads lower than on the
  Graham model (the tangible number was the lower for 74 of the 81 companies it applies to). N/A where tangible book is
  negative (33 of the 127).

One table holds them:

- **`valuation_indicators`**: one row per (ticker, indicator), five per company, replaced together whenever the
  company's valuations are computed:
  - `indicator`: `reverse_dcf`, `epv`, `ncav`, `ps_value` or `graham_tangible`;
  - `applicable` (1 or 0) and `na_reason` (NULL when applicable);
  - `value_bear`, `value_base` and `value_bull`: per share, on the price's basis, NULL where the method gives no such
    case or the indicator is N/A. For `reverse_dcf` the base is the growth as a fraction (0.265 is 26.5% a year), and
    the bear and bull are NULL;
  - `unit`: `per_share`, or `rate` for `reverse_dcf`;
  - `assumptions` (JSON): the inputs. An N/A row may carry the ones computed before the method stopped, a negative NCAV
    per share for instance;
  - `basis_fiscal_year` (NULL for `ps_value`) and `computed_at`.

The JSON column reads with SQLite's JSON functions, e.g. `json_extract(assumptions, '$.dcf_growth_base')`. What each
indicator stores in it:
- `reverse_dcf`: `price` and `price_date` (the snapshot it was solved at), the DCF's inputs (`fcf_basis`,
  `shares_outstanding`, `discount_base`, `terminal_growth`), `dcf_growth_base` (the DCF's own growth), `dcf_value_base`
  and the search range (`search_low`, `search_high`);
- `epv`: `margin_years` and `mean_operating_margin`, `pretax_years` (the years whose EBIT is pretax income plus
  interest) and `no_interest_years` (those without an interest figure), `latest_revenue`, `normalized_ebit`, `tax_rate`
  and `tax_source` (`effective` or `default`), `adjusted_earnings`, `wacc`, `total_debt` (and `debt_missing`),
  `cash_and_sti`, and `shares_outstanding` with `shares_source` (`market_snapshot`, or `financials_annual` after a
  failed quote);
- `ncav`: `ncav_per_share`, `nnwc_per_share` (the net-net working capital), `current_assets`, `total_liabilities`,
  `shares`, the four net-net parts (`cash_and_equivalents`, `short_term_investments`, `accounts_receivable`,
  `inventory`) with `receivables_weight` and `inventory_weight`, `nnwc_missing` (the parts counted as 0) and, on an
  applicable row, `netnet_buy_price`;
- `ps_value`: `median_ps`, `p25_ps`, `p75_ps`, `current_ps`, `as_of` and `n_months`;
- `graham_tangible`: `eps_base`, `eps_bear`, `eps_bull`, `bvps`, `tangible_bvps`, `total_equity`, `goodwill`,
  `intangible_assets`, `multiplier` and `missing` (the goodwill or intangibles figures counted as 0).

```sql
-- AAPL's five indicators: the bear, base and bull per share (a growth rate for the reverse DCF), or why not
SELECT indicator, applicable, value_bear, value_base, value_bull, unit, na_reason
FROM valuation_indicators
WHERE ticker = 'AAPL'
ORDER BY indicator;

-- Every company's earnings power value against its latest price, the ones furthest under it first
SELECT i.ticker, i.value_bear, i.value_base, i.value_bull, m.current_price,
       i.value_base / m.current_price - 1 AS epv_upside
FROM valuation_indicators i
JOIN market_snapshots m ON m.ticker = i.ticker
 AND m.collected_at = (SELECT MAX(collected_at) FROM market_snapshots WHERE ticker = i.ticker)
WHERE i.indicator = 'epv' AND i.applicable = 1
ORDER BY epv_upside DESC
LIMIT 10;

-- The growth each stored price implies, beside the growth the DCF itself assumes, the widest gap first
SELECT ticker,
       value_base AS implied_growth,
       json_extract(assumptions, '$.dcf_growth_base') AS dcf_growth,
       value_base - json_extract(assumptions, '$.dcf_growth_base') AS gap,
       json_extract(assumptions, '$.price') AS price,
       json_extract(assumptions, '$.price_date') AS price_date
FROM valuation_indicators
WHERE indicator = 'reverse_dcf' AND applicable = 1
ORDER BY gap DESC
LIMIT 10;

-- The companies with a positive NCAV: Graham's buy line, the net-net working capital per share and the price over NCAV
-- (a net-net trades at or under the buy line: a price over NCAV of two thirds or less)
SELECT i.ticker, i.value_base AS ncav_per_share, i.value_bear AS netnet_working_capital,
       json_extract(i.assumptions, '$.netnet_buy_price') AS buy_line,
       m.current_price, m.current_price / i.value_base AS price_to_ncav
FROM valuation_indicators i
JOIN market_snapshots m ON m.ticker = i.ticker
 AND m.collected_at = (SELECT MAX(collected_at) FROM market_snapshots WHERE ticker = i.ticker)
WHERE i.indicator = 'ncav' AND i.applicable = 1
ORDER BY price_to_ncav;

-- The Graham number on tangible book against the Graham model's own, the biggest cuts first
SELECT t.ticker, g.value_base AS graham, t.value_base AS graham_tangible,
       t.value_base / g.value_base - 1 AS change
FROM valuation_indicators t
JOIN valuations g ON g.ticker = t.ticker AND g.model = 'graham' AND g.applicable = 1
WHERE t.indicator = 'graham_tangible' AND t.applicable = 1
ORDER BY change
LIMIT 10;

-- Why an indicator is N/A, and for how many companies
SELECT indicator, na_reason, COUNT(*) AS companies
FROM valuation_indicators
WHERE applicable = 0
GROUP BY indicator, na_reason
ORDER BY indicator, companies DESC;
```

It is also shown in three places:
- **Web**: the **VAL** tab's *Other indicators* section, after the models and owner earnings: per indicator its bear,
  base and bull, *Price vs base* (the base's upside from the live price), its method in one line, notes read from its
  assumptions and the assumptions themselves, or its N/A reason. The reverse DCF is solved again at the live price,
  beside the growth the DCF assumes; the stored growth is at the price of the last computation. Before the indicators
  are computed the section says to run the backfill or click UPDATE DATA.
- **Screener**: five fields, under *Other indicators* in the field list: `reverse_dcf_growth` (the growth the price
  implies, a fraction, as stored), `epv_upside_pct`, `ps_value_upside_pct` and `graham_tangible_upside_pct` (the base
  over the snapshot's price, less 1) and `price_to_nnwc` (the price over Graham's net-net working capital per share,
  where that is positive; `price_to_ncav` is the same over NCAV). The results show the first as an *Implied growth*
  column, and the CSV export carries all five. For example `GET /api/screen?epv_upside_pct_gt=0&sort=epv_upside_pct&sort_dir=desc`
  or `GET /api/screen?reverse_dcf_growth_gt=0.3&sort=reverse_dcf_growth&sort_dir=desc`. A database from before the table
  screens with all five empty.
- **API**: `GET /api/stocks/{ticker}/valuation-indicators` (not `/indicators`, the price chart's MA/RSI/MACD bundle)
  returns `{ticker, price, computed, computed_at, indicators, reverse_dcf_live}`. `computed` is false before the
  indicators are stored. Each of the five `indicators` has its `indicator`, `label`, `method`, `applicable`,
  `na_reason`, `unit`, `value_bear`, `value_base`, `value_bull`, `basis_fiscal_year`, `assumptions`, `upside_pct` (the
  base over the price, less 1; null for the reverse DCF) and `notes`. `reverse_dcf_live` is the reverse DCF solved again
  at `price`, the latest quote: `{price, growth, na_reason, dcf_growth_base}`. An unknown ticker answers 404.

It changes none of the models, their median or the verdict.

### Owner Earnings ("Buffett mode")

A sixth model, alongside the five above, computed and stored the same way — but
intentionally excluded from the five-model median described above.

**Model key:** `owner_earnings`. Applies to the same sectors as the DCF (general,
utility, energy); banks, insurers, and REITs report `not applicable`, same as DCF.

```
owner_earnings    = net_income + depreciation_amortization - maintenance_capex
maintenance_capex = min(depreciation_amortization, abs(capex))
```

Growth capex (the excess of capex over D&A) is **not** subtracted — the model's
whole point is to stop penalizing a company for investing to grow. Stock-based
compensation is **not** added back; it's real compensation, and Buffett has been
explicit that pretending otherwise is dishonest. Basis is the median of the last
3 fiscal years, the same shape as the DCF's FCF basis.

**Discount rate:** `max(10-year Treasury yield, 7%)`. No beta — Buffett rejects
it as a risk measure — and a 7% floor so a collapse in yields can't manufacture
an implausibly cheap valuation.

**Margin of safety lives in the price**, not the discount rate:
`buy_below = intrinsic_value x 0.70`. The verdict is its own:
- price below `buy_below` → **cheap**
- price between `buy_below` and intrinsic value → **fair**
- price above intrinsic value → **expensive**

**Predictability gate.** The model refuses to value a history that isn't
consistently positive: owner earnings must be positive in at least 8 of the last
10 fiscal years, and at least 10 years of history must exist to run the test at
all. Below that threshold it reports `applicable=false` with an honest reason
(`owner earnings too erratic to forecast`, or the insufficient-history variant)
instead of a number. It deliberately does **not** claim to filter cyclicals —
against the real database the 8-of-10 gate still values SLB, COP, CVX, and CAT,
because the D&A add-back smooths their owner earnings relative to net income even
though the underlying business is volatile. Rather than fake a mechanical
judgment no formula can make honestly, the model stores the volatility evidence
(`assumptions.volatility`: `positive_years`, `total_years`, `collapse_years`,
`worst_drop`) and leaves the circle-of-competence call to you. On the real data:
MSFT is positive 10 of 10 years with 0 collapse years and a 35% worst single-year
fall; SLB is positive 8 of 10 with 2 collapse years and a 100% worst fall — the
model values both and shows you why that's a judgment call, not a fact it makes
for you.

**Why it's excluded from the five-model median.** A Treasury-discounted value and
a CAPM-discounted value answer different questions; mixing them into one median
would silently average two incompatible philosophies and drag every verdict
toward "cheap" without you knowing why. On the real 50-ticker database, the
median CAPM DCF prices the market at 0.68x price while the Treasury-discounted
owner-earnings model prices it at 1.98x — the same cash flows, 170% apart,
purely from the discount-rate choice. Concretely: MSFT's academic DCF is $245.66
vs owner earnings $559.92 (2.28x); NVDA's is $41.86 vs $142.00 (3.39x) — in both
cases the gap is the growth capex being credited back rather than subtracted.
Verified against the live 50-ticker run: adding `owner_earnings` changed **zero**
of the existing five-model summary rows.

**So the two verdicts can legitimately disagree** — a stock can be "expensive"
under the five-model median (CAPM discount) and "cheap" under owner earnings
(Treasury discount) with no error anywhere. The gap tells you how much of any
verdict is method rather than business; it is not a discrepancy to reconcile.

**Known limitation:** GOOGL is `not applicable` under this model. Alphabet's
older SEC filings don't tag a cash-flow D&A concept, so its owner-earnings
history is under 10 years — a gap in SEC's own filed data, not a bug, and a
little ironic since Alphabet is exactly the kind of heavy-growth-capex company
this model exists to value properly.

**Where it shows up:**

- **Web UI** — the VAL tab's "Owner Earnings (Buffett)" section, below the
  five-model chart: intrinsic value (bear/base/bull), the `buy_below` threshold
  and its verdict chip, a method-gap line comparing academic DCF vs owner
  earnings vs price, and the volatility statement above. When not applicable,
  the reason is shown directly.
- **API** — `GET /api/stocks/{ticker}/valuation` already returns every model's
  row (`model: "owner_earnings"` in the `models` list) plus two top-level
  fields, `owner_earnings_verdict` and `owner_earnings_verdict_label` (its
  verdict isn't derivable from the shared `summary` medians, so it's returned
  separately).
- **Screener** (`/screener`) — a sortable **Buffett upside %** column
  (`oe_upside_pct`, `(owner_earnings base − price) / price`), kept separate
  from the regular Upside % column, plus a filter on its own verdict:
  `oe_verdict=cheap|fair|expensive`. Both are included in
  `GET /api/export/screen.csv`.
- Computed by the same `python -m src.valuation.backfill` as the other five
  models, and recomputed automatically after each collection run.

---

## SEC Ownership

`src/ownership/` stores what the SEC's own filings say about a company's insiders and about a list of 34
gurus: each insider's Form 4 trades of the last three years, and each guru's 13F holdings of the last eight
quarters, mapped to tickers. See the README's
[SEC ownership: insiders and gurus](README.md#sec-ownership-insiders-and-gurus) for the rules and the limits.
It changes none of the models, scores or histories, and neither `python -m src.main` nor the web interface's
update collects it: it has its own command, and eight tables hold it.

### Collecting ownership data

```bash
# Insiders: three years of Form 4s for every company in the database
python -m src.ownership.collect --insiders

# Some tickers only, or another look-back in years
python -m src.ownership.collect --insiders AAPL PFE --years 1

# Gurus: the last eight quarters' 13F-HRs, their CUSIPs mapped to tickers, then the quarters and positions
python -m src.ownership.collect --gurus

# The same, asking OpenFIGI nothing (only our companies' CUSIPs are mapped)
python -m src.ownership.collect --gurus --no-openfigi

# Rebuild the gurus' quarters and positions from the stored filings (offline)
python -m src.ownership.collect --positions

# Another database or cache
python -m src.ownership.collect --insiders --gurus --db data/output/stock.db --cache-dir data/cache
```

The collector sends the SEC User-Agent of `SEC_USER_AGENT` (in `.env` or the environment, as the README's
[SEC User-Agent Requirement](README.md#sec-user-agent-requirement) describes); it has no `--sec-user-agent`
option. `--insiders` reads each company's CIK from `companies`, so run a collection first.

The first run takes what the SEC's rate limit allows: about two hours for every company's Form 4s (an
estimate: PFE's and AAPL's 452 filings took 107 seconds), under two minutes for the gurus' 248 13F filings,
and at least 7.5 minutes more for OpenFIGI's 1,817 codes. `--positions` takes seconds and touches no network.
Later runs fetch only what the caches (`data/cache/form4/`, `13f/` and `openfigi/`) lack. Each run prints a
line per company or guru and a total; the end of `--positions` looks like:

```
Warren Buffett (1067983): 7 quarters, 283 position rows stored
...
Guru quarters stored: 235 quarters, 16813 position rows for 34 gurus
```

### Insiders (Form 4)

Two tables hold each company's Form 4 and 4/A filings of the last three years:

- **`insider_filings`**: one row per (`accession`, `owner_cik`), a filing and one of its reporting owners:
  - `ticker` (our company, the issuer), `form` (`4` or `4/A`), `filed_date`, `period_of_report` (the date the
    report covers) and `original_filed` (for a 4/A, the day its original was filed);
  - the owner: `owner_name`, `is_director`, `is_officer`, `officer_title`, `is_ten_percent` and `is_other` (1
    or 0), and `is_ceo_cfo` (1 when the title names the chief executive or financial officer);
  - `aff10b5one`: 1 when the filing says its trades were made under a Rule 10b5-1 plan, 0 when it says they
    weren't, NULL when it doesn't say;
  - `superseded_by`: the accession of the 4/A that amended this filing, NULL otherwise; the trades the 4/A
    restated are marked in `insider_trades`, and the filing's other trades stand;
  - `collected_at`.
- **`insider_trades`**: one row per transaction, (`accession`, `seq`), `seq` being its place in the filing:
  - `ticker`, `kind` (`nonderivative` or `derivative`), `security_title`, `date`, and `code`, the SEC's
    transaction code: `P` is an open-market purchase and `S` an open-market sale; the others (`A` grant or
    award, `M` exercise or conversion, `F` shares withheld for tax, `G` gift, ...) are shown but never summed;
  - `shares`, `price` (NULL where the filing states none: a footnote alone, whose ids `price_footnotes`
    keeps), `acquired_disposed` (`A` or `D`), `owned_after` (NULL where the filing doesn't say),
    `direct_indirect` (`D` or `I`) and `nature_of_ownership`;
  - a derivative row (an option, an RSU) also has `exercise_price`, `underlying_title` and
    `underlying_shares`;
  - `superseded_by`: the accession of the 4/A that restated this trade, NULL for a trade that stands. A
    restated trade stays in the table, and every aggregate, the INS tab and the screener leave it out, so test
    `t.superseded_by IS NULL`;
  - `price_suspect`: 1 when the open-market price is a filer's dropped decimal point (1,000 times, or a
    thousandth of, a price within 10% of the stock's close that day; the Lilly Endowment filed $1,031,414 for
    $1,031.41), 0 when checked, NULL when unchecked (no price or no close, or a derivative row). The price stays
    as filed and every dollar figure treats it as missing, so add `AND t.price_suspect IS NOT 1` to a dollar sum.

A filing with several reporting owners has a row in `insider_filings` for each, so a join on `accession`
repeats its trades once per owner: add dollars from `insider_trades` alone, as the first query does. The INS
tab and the API name the first owner (the lowest `owner_cik`) and count one buyer; the screener's
`ins_buyers_12m` and `ins_cluster_buyers_12m` count every owner.

```sql
-- Open-market dollars bought and sold over the last 12 months, per company, the biggest net buyers first
SELECT t.ticker,
       SUM(CASE WHEN t.code = 'P' THEN t.shares * t.price ELSE 0 END) AS bought,
       SUM(CASE WHEN t.code = 'S' THEN t.shares * t.price ELSE 0 END) AS sold,
       SUM(CASE t.code WHEN 'P' THEN t.shares * t.price ELSE -t.shares * t.price END) AS net
FROM insider_trades t
WHERE t.kind = 'nonderivative' AND t.code IN ('P', 'S') AND t.price IS NOT NULL AND t.price_suspect IS NOT 1
  AND t.date >= date('now', '-365 days') AND t.superseded_by IS NULL
GROUP BY t.ticker
ORDER BY net DESC;

-- PFE's open-market purchases over the last 12 months: who bought, and what they held after
SELECT t.date, f.owner_name, f.officer_title, f.is_director,
       t.shares, t.price, t.shares * t.price AS value, t.owned_after
FROM insider_trades t
JOIN insider_filings f ON f.accession = t.accession
WHERE t.ticker = 'PFE' AND t.kind = 'nonderivative' AND t.code = 'P'
  AND t.superseded_by IS NULL AND t.date >= date('now', '-365 days')
ORDER BY t.date;

-- Purchases by a company's CEO or CFO, newest first
SELECT t.ticker, t.date, f.owner_name, f.officer_title, t.shares, t.price, t.shares * t.price AS value
FROM insider_trades t
JOIN insider_filings f ON f.accession = t.accession
WHERE t.kind = 'nonderivative' AND t.code = 'P' AND f.is_ceo_cfo = 1 AND t.superseded_by IS NULL
ORDER BY t.date DESC;

-- The trades a 4/A restated, with the 4/A that restated each
SELECT t.ticker, t.accession AS amended, t.date, t.code, t.shares, t.price, t.superseded_by AS restated_by
FROM insider_trades t
WHERE t.superseded_by IS NOT NULL
ORDER BY t.ticker, t.date;
```

### Gurus (13F)

Six tables hold the gurus' 13F-HR filings and what they add up to. A guru's id, `guru_cik`, is its current 13F
filer's CIK, as text without leading zeros.

- **`guru_filers`**: one row per 13F filer, `filer_cik`: its `guru_cik`, the filer's `name` as the SEC lists
  it and the `manager` the list follows (Warren Buffett). A guru with an earlier filer has two rows with one
  `guru_cik`; the current filer's own row has `filer_cik = guru_cik`.
- **`guru_filings`**: one row per 13F-HR or 13F-HR/A, `accession`:
  - `guru_cik`, `filer_cik`, `form`, `period` (the quarter's last day), `filed`, `report_type`
    (`13F HOLDINGS REPORT` or `13F COMBINATION REPORT`) and `amendment_type` (`RESTATEMENT` or `NEW HOLDINGS`
    for an amendment, else NULL);
  - the cover's totals, `entry_total` and `value_total`, beside the parsed ones, `rows` and `rows_value`, all
    values in dollars;
  - `checksum_ok`: 1 when `entry_total` equals the entries parsed and `value_total` is within rounding of
    their sum (half a unit per entry plus half a unit, in the units the filing states). A filing with 0 stays
    in the table and out of every quarter;
  - `multiplier`: the units the filing's values were read in, 1000 for thousands of dollars and 1 for dollars,
    so `value_usd` is the value as filed times it. By the filing date it is 1000 before 2023-01-03 and 1 from
    then, unless the holdings' prices overrule it: Baupost's and Duquesne's filings are 1000 after the switch;
  - `confidential_omitted` (the cover's flag, NULL where it isn't stated) and `collected_at`.
- **`guru_holdings`**: one row per entry of a filing's information table, (`accession`, `row`), as filed:
  - `guru_cik`, `cusip` (nine characters; a letter first for a foreign issuer's CINS), `issuer` and
    `title_of_class`;
  - `value_usd` (dollars), `shares` (the shares or the principal amount) and `share_type` (`SH` or `PRN`),
    `put_call` (`Put`, `Call`, or NULL for the security itself) and `discretion`;
  - one CUSIP may take several rows of a filing (other managers, discretion).
- **`cusip_map`**: one row per CUSIP: `ticker` (NULL where there is none), `name`, `security_type`
  (OpenFIGI's), `mapped_at`, and `source`: `universe` for our companies' codes, curated in
  `src/ownership/cusips.py`; `openfigi` for a listing OpenFIGI gave; `none` where OpenFIGI knows no listing,
  so there is no ticker and `name` is the issuer's name from the holdings.
- **`guru_quarters`**: one row per (`guru_cik`, `period`), a guru's quarter as built from the filings that
  stand: `total_value`, `positions` (held, options too), `complete` (0 when a filer's report for the quarter
  is missing or fails its checksum: its rows are left out), `has_prior` (1 when the quarter before is stored,
  so the changes exist), `accessions` (a JSON list of the filings it stands on), `filed` (the latest of their
  filing days) and `computed_at`.
- **`guru_positions`**: one row per position of a quarter, (`guru_cik`, `period`, `security`, `put_call`):
  - `security` is the ticker where `cusip_map` has one, else the CUSIP; `ticker` is NULL where there is none;
    `cusips` lists the CUSIPs the position covers, comma-separated; `issuer`, `title_of_class` and
    `share_type` are its first row's;
  - `put_call = ''` (an empty string, not NULL) is a position in shares or principal amounts; `'Put'` and
    `'Call'` are options, apart from the shares, so filter `put_call = ''` for the shares;
  - `shares` and `value_usd`, summed over the position's rows, and `weight`, its value over the quarter's
    total (options included);
  - the change from the quarter before: `action` (`new`, `added`, `reduced` or `unchanged`), `shares_before`
    and `value_before`, `split_factor` (the share splits between the two quarters, applied to the shares
    before) and `change_pct` (the change in shares against the split-adjusted shares before, as a fraction:
    0.25 is a quarter more; NULL for a new position). `action` is NULL on every row of a quarter whose quarter
    before isn't stored (`has_prior = 0`);
  - a `sold_out` row for each position of the quarter before that is gone: `shares`, `value_usd` and `weight`
    are NULL, and `shares_before` and `value_before` say what the guru held. To leave these out use
    `action IS NOT 'sold_out'`: `<> 'sold_out'` also drops the rows whose `action` is NULL.

Each guru has its own latest quarter (`MAX(period)` in `guru_quarters`): Scion's is Q3 2025, the others'
Q2 2026. `guru_positions` is derived from `guru_filings`, `guru_holdings`, `cusip_map` and `split_events`, and
`--positions` rebuilds it.

```sql
-- Each guru's latest quarter: when it was filed, what it holds, and whether it stands complete
SELECT g.manager, q.period, q.filed, q.total_value, q.positions, q.complete, q.has_prior
FROM guru_quarters q
JOIN guru_filers g ON g.filer_cik = q.guru_cik
WHERE q.period = (SELECT MAX(period) FROM guru_quarters WHERE guru_cik = q.guru_cik)
ORDER BY q.total_value DESC;

-- Berkshire Hathaway's ten largest positions in its latest quarter
SELECT p.ticker, p.issuer, p.shares, p.value_usd, ROUND(p.weight * 100, 1) AS weight_pct, p.action
FROM guru_positions p
WHERE p.guru_cik IN (SELECT guru_cik FROM guru_filers WHERE manager = 'Warren Buffett')
  AND p.put_call = '' AND p.action IS NOT 'sold_out'
  AND p.period = (SELECT MAX(period) FROM guru_quarters WHERE guru_cik = p.guru_cik)
ORDER BY p.weight DESC
LIMIT 10;

-- The gurus holding GOOGL or its other class GOOG in their latest quarter, the largest first
SELECT g.manager, p.ticker, p.period, p.shares, p.value_usd, ROUND(p.weight * 100, 1) AS weight_pct,
       p.action, ROUND(p.change_pct * 100, 1) AS change_pct
FROM guru_positions p
JOIN guru_filers g ON g.filer_cik = p.guru_cik
WHERE p.ticker IN ('GOOGL', 'GOOG') AND p.put_call = '' AND p.action IS NOT 'sold_out'
  AND p.period = (SELECT MAX(period) FROM guru_quarters WHERE guru_cik = p.guru_cik)
ORDER BY p.value_usd DESC;

-- How many gurus hold it (each counted once, as the screener's gurus_holding) and how many are buying it
SELECT COUNT(DISTINCT CASE WHEN p.action IS NOT 'sold_out' THEN p.guru_cik END) AS holding,
       COUNT(DISTINCT CASE WHEN p.action IN ('new', 'added') THEN p.guru_cik END) AS buying
FROM guru_positions p
WHERE p.ticker IN ('GOOGL', 'GOOG') AND p.put_call = ''
  AND p.period = (SELECT MAX(period) FROM guru_quarters WHERE guru_cik = p.guru_cik);

-- The gurus that sold GOOGL or GOOG out in their latest quarter, and what they held before
SELECT g.manager, p.ticker, p.period, p.shares_before, p.value_before
FROM guru_positions p
JOIN guru_filers g ON g.filer_cik = p.guru_cik
WHERE p.ticker IN ('GOOGL', 'GOOG') AND p.put_call = '' AND p.action = 'sold_out'
  AND p.period = (SELECT MAX(period) FROM guru_quarters WHERE guru_cik = p.guru_cik);

-- What the gurus hold together: per security, the gurus holding it, their shares and value summed,
-- and how many bought (new or added) and sold (reduced or sold out) it in their latest quarter
SELECT p.security, MIN(p.issuer) AS issuer,
       COUNT(DISTINCT CASE WHEN p.action IS NOT 'sold_out' THEN p.guru_cik END) AS gurus,
       SUM(p.shares) AS shares, SUM(p.value_usd) AS value,
       COUNT(DISTINCT CASE WHEN p.action IN ('new', 'added') THEN p.guru_cik END) AS buyers,
       COUNT(DISTINCT CASE WHEN p.action IN ('reduced', 'sold_out') THEN p.guru_cik END) AS sellers
FROM guru_positions p
JOIN (SELECT guru_cik, MAX(period) AS period FROM guru_quarters GROUP BY guru_cik) l
  ON l.guru_cik = p.guru_cik AND l.period = p.period
WHERE p.put_call = ''
GROUP BY p.security
HAVING gurus > 0
ORDER BY gurus DESC, value DESC
LIMIT 15;

-- Berkshire Hathaway's buys and sells in its latest quarter, by the value involved
SELECT p.action, p.security, p.issuer, p.shares_before, p.shares,
       ROUND(p.change_pct * 100, 1) AS change_pct, p.value_before, p.value_usd
FROM guru_positions p
WHERE p.guru_cik IN (SELECT guru_cik FROM guru_filers WHERE manager = 'Warren Buffett')
  AND p.put_call = '' AND p.action IN ('new', 'added', 'reduced', 'sold_out')
  AND p.period = (SELECT MAX(period) FROM guru_quarters WHERE guru_cik = p.guru_cik)
ORDER BY p.action, MAX(COALESCE(p.value_usd, 0), COALESCE(p.value_before, 0)) DESC;

-- The filings whose values the holdings' prices put in thousands after the SEC's switch to dollars
SELECT g.manager, f.period, f.filed, f.multiplier
FROM guru_filings f
JOIN guru_filers g ON g.filer_cik = f.filer_cik
WHERE f.multiplier = 1000 AND f.filed >= '2023-01-03'
ORDER BY g.manager, f.period;

-- The filings whose cover totals differ from their entries, in the units as filed: a sum within half a unit
-- per entry plus half a unit is rounding; a sum beyond it, or another count, sets checksum_ok to 0
SELECT g.manager, f.accession, f.period, f.checksum_ok, f.entry_total, f.rows,
       (f.rows_value - f.value_total) / f.multiplier AS off_by_units
FROM guru_filings f
JOIN guru_filers g ON g.filer_cik = f.filer_cik
WHERE f.rows <> f.entry_total OR f.rows_value <> f.value_total
ORDER BY ABS(off_by_units) DESC, f.accession;

-- The codes OpenFIGI doesn't know: no ticker, the issuer's name, and what the gurus hold in them
SELECT m.cusip, m.name, COUNT(*) AS entries, SUM(h.value_usd) AS value_usd
FROM cusip_map m
JOIN guru_holdings h ON h.cusip = m.cusip
WHERE m.source = 'none'
GROUP BY m.cusip
ORDER BY value_usd DESC;
```

### Viewing ownership

- **Web**:
  - the **INS** tab (`/stocks/{ticker}?tab=ins`, or `AAPL INS` in the command bar): the SEC's Form 4 trades
    with their aggregates, and Yahoo's rows for a company with no Form 4 stored;
  - the **HDS** tab (`?tab=hds`): a Gurus section above Yahoo's holders, once any quarter is stored;
  - `/gurus` (each guru at its latest quarter), `/gurus/{cik}` (a guru's holdings, the changes from the
    quarter before and its filings; `?period=2026-03-31` for an earlier quarter) and `/gurus/portfolio` (what
    they hold together). The nav has a Gurus link. A guru or quarter that isn't stored answers 404.
- **API** (values in dollars; a position's `value_usd` is `value`):
  - `GET /api/stocks/{ticker}/form4` returns `{ticker, source, summary, trades}`. `source` is `sec`, `yahoo`
    (no Form 4 stored: `summary` is null and `trades` are Yahoo's rows) or null. `summary` holds `windows`
    (`3m`, `6m` and `12m`, each with `bought_shares`, `bought_value`, `sold_shares`, `sold_value`,
    `net_value`, `buyers` and `sellers`), `ceo_cfo_buys_12m`, `clusters_12m` and `planned_sale_share_12m`.
    Each trade has its `date`, `code` and `meaning`, `kind`, `shares`, `price`, `value`, `owned_after`, the
    owner's `owner_name`, `role` and `is_ceo_cfo`, and `plan` (true, false or null), among others;
  - `GET /api/stocks/{ticker}/gurus` returns `{ticker, classes, gurus}`: the ticker and its other share
    classes (`GOOGL` with `GOOG`), and each guru's positions in them in its latest quarter (the guru's `cik`,
    `manager`, `period`, `filed` and `complete`, and the position's `put_call` (null for shares), `shares`,
    `value`, `weight`, `action`, `shares_before`, `value_before`, `split_factor` and `change_pct`): the shares
    first, then the options, then the positions sold out;
  - `GET /api/gurus` returns each guru at its latest quarter: `cik`, `manager`, `filers`, `period`, `filed`,
    `total_value`, `positions` (held in shares or principal amounts), `new` and `sold_out` (null without the
    quarter before), `complete` and `has_prior`;
  - `GET /api/gurus/portfolio?limit=200` (1 to 10,000) returns the securities held by most gurus: `security`,
    `ticker`, `issuer`, `gurus`, `shares`, `value`, `buyers` and `sellers`;
  - `GET /api/gurus/{cik}?period=2026-03-31` returns one guru's quarter (its latest without `period`):
    `periods` (those stored, newest first), `filed`, `total_value`, `complete`, `has_prior`, `accessions` and
    `filings`, `positions` (the shares before the options, the largest first) and `changes`, the positions
    grouped as `new`, `added`, `reduced` and `sold_out` (null without the quarter before).
- **Screener**: the *Insiders (SEC)* fields `ins_net_buy_12m` (open-market dollars bought less sold),
  `ins_buyers_12m`, `ins_ceo_cfo_buys_12m` (filings with a CEO/CFO purchase) and `ins_cluster_buyers_12m` (the
  most insiders buying within 30 days of one purchase: 3 or more is a cluster), and the *Gurus (13F)* fields
  `gurus_holding` and `gurus_buying` (the gurus holding the stock in their latest quarter and, of them, the
  ones whose position is new or added; each guru counted once over the stock's share classes), shown as one
  "Gurus (buying)" column. For example `GET /api/screen?gurus_buying_gte=5&sort=gurus_buying&sort_dir=desc` or
  `GET /api/screen?ins_cluster_buyers_12m_gte=3`.

A company with no Form 4 collected reads 0 in `ins_buyers_12m` and `ins_ceo_cfo_buys_12m`, as one whose
insiders bought nothing does, and empty in `ins_net_buy_12m` and `ins_cluster_buyers_12m`; a company no guru
holds reads 0 in `gurus_holding` and `gurus_buying`. A database without the ownership tables screens with all
six fields empty.

---

## Market Data

`src/market/` stores three market-wide sources that no company's filings give: FRED's corporate equities and GDP, Robert
Shiller's monthly S&P data with his cyclically adjusted P/E (CAPE), and Treasury.gov's daily yield curve. The **Market**
page (`/market`) draws the Buffett indicator, the CAPE and the yield curve from them. See the README's
[Market data](README.md#market-data) for the sources, the cache and the methods. They change nothing collected for the
companies, and neither `python -m src.main` nor the web interface's update collects them: they have their own command,
and two tables hold them.

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

The database must already exist (run a collection first: nothing is created, and a missing one exits with 2). No key
and no SEC User-Agent are needed: the three sites are asked with requests' own User-Agent, a request a second.
`--treasury-from YEAR` starts the Treasury's files at another year than 1990; its series are replaced whole, so a later
year removes the earlier rows from `market_series` (the cache keeps them, and a run without the option stores them
again).

The first run takes about 12 minutes (725 seconds on 2026-10-24), nearly all of it Treasury.gov, which answers slowly
(about 19 seconds for each of its 37 yearly files). Every answer is cached under `data/cache/market/`, and a later run
fetches only what is a day old: FRED's two series, Shiller's page and workbook, and the Treasury's current year. A past
Treasury year is not fetched again once it was cached after that year ended. A run with everything fresh takes about 3
seconds and stores the same rows, so a run within a day of the last one adds nothing new. A fetch that fails falls back
to the cached copy and says so. Each run prints a line per source and a summary; the end of a run looks like:

```
Market data: 4 of 4 sources stored
  fred:NCBEILQ027S: 305 rows, 1945-10-01 to 2026-04-01
  ...
  ust:30y: 8215 rows, 1990-01-02 to 2026-10-23
Latest: fred:NCBEILQ027S 2026-04-01; fred:GDP 2026-04-01; shiller 2026-10-01; treasury 2026-10-23
Failed: none
```

A source that fails, or returns no rows, is listed on the `Failed:` line with its reason and stores nothing; what it
stored before stays, and the others still go on. The exit code is 0 when every source asked for stored rows and 1
when any did not.

### The market series

Two tables hold them:

- **`market_series`**: one row per (`series`, `date`) with its `value`. `date` is ISO: the first day of the quarter for
  FRED, of the month for Shiller, and the trading day for the Treasury. A series is replaced whole when its source is
  fetched. The series:
  - `fred:NCBEILQ027S`: the corporate equities of US nonfinancial corporate business at market value, quarterly, in
    $ millions, from 1945-10-01. It is annual before 1951-Q4, and its empty quarters are not stored;
  - `fred:GDP`: gross domestic product, quarterly, in $ billions at a seasonally adjusted annual rate, from 1947-01-01;
  - `shiller:price`, `shiller:dividend`, `shiller:earnings`, `shiller:cpi` and `shiller:cape`: Shiller's monthly S&P
    Composite price (the month's mean of daily closes), dividend and earnings, the CPI and the CAPE, from 1871-01-01 (the
    CAPE from 1881-01-01). Each stops at its own last month: on 2026-10-24 the price and CPI reach 2026-10 and the
    dividend and earnings 2026-06;
  - `ust:1m`, `ust:6w`, `ust:2m`, `ust:3m`, `ust:4m`, `ust:6m`, `ust:1y`, `ust:2y`, `ust:3y`, `ust:5y`, `ust:7y`,
    `ust:10y`, `ust:20y` and `ust:30y`: the Treasury's daily par yield curve, in percent as published (`4.69` is 4.69%),
    from 1990. A maturity has rows only from the day Treasury began publishing it (the 20-year 1993-10-01, the 1-month
    2001-07-31, the 2-month 2018-10-16, the 4-month 2022-10-19, the 1.5-month `6w` 2025-02-18), and the 30-year has none
    from 2002 to 2006.
- **`market_fetches`**: one row per source (`fred:NCBEILQ027S`, `fred:GDP`, `shiller`, `treasury`): the `url` it came
  from (Shiller's workbook link, the Treasury's current-year file), the `rows` stored for it and `fetched_at`, when the
  answer came from the source. A cached copy keeps its own time, so a fall back to an old copy shows its age.

```sql
-- Every series: its rows and its first and last date
SELECT series, COUNT(*) AS n_rows, MIN(date) AS first_date, MAX(date) AS last_date
FROM market_series
GROUP BY series
ORDER BY series;

-- What was collected from each source, and when its answer was fetched
SELECT source, rows, fetched_at, url
FROM market_fetches
ORDER BY source;

-- The Buffett indicator for the latest quarters both FRED series have:
-- equities ($ millions) over GDP ($ billions), in percent
SELECT e.date AS quarter, e.value AS equities_musd, g.value AS gdp_busd,
       e.value / 1000 / g.value * 100 AS equities_to_gdp_pct
FROM market_series e
JOIN market_series g ON g.series = 'fred:GDP' AND g.date = e.date
WHERE e.series = 'fred:NCBEILQ027S'
ORDER BY e.date DESC
LIMIT 8;

-- Shiller's CAPE: the latest month against its mean since 1881, its low and high,
-- and the share of months at or below the latest
WITH cape AS (SELECT date, value FROM market_series WHERE series = 'shiller:cape'),
     latest AS (SELECT date, value FROM cape ORDER BY date DESC LIMIT 1)
SELECT latest.date AS month, latest.value AS latest, AVG(cape.value) AS mean,
       MIN(cape.value) AS low, MAX(cape.value) AS high, AVG(cape.value <= latest.value) AS percentile
FROM cape, latest;

-- The 10-year less the 2-year yield on the latest days; below 0 is an inverted curve
SELECT t.date, t.value AS ten_year, w.value AS two_year, ROUND(t.value - w.value, 2) AS spread
FROM market_series t
JOIN market_series w ON w.series = 'ust:2y' AND w.date = t.date
WHERE t.series = 'ust:10y'
ORDER BY t.date DESC
LIMIT 10;

-- Days the 10-year yield was under the 2-year yield, by year
SELECT substr(t.date, 1, 4) AS year, COUNT(*) AS days, SUM(t.value < w.value) AS inverted_days
FROM market_series t
JOIN market_series w ON w.series = 'ust:2y' AND w.date = t.date
WHERE t.series = 'ust:10y'
GROUP BY year
HAVING inverted_days > 0
ORDER BY year DESC;
```

### Viewing market data

- **Web**: the **Market** page (`/market`, a link in the nav bar) has three sections, each with its figures, its
  chart, a source line (the link, the day it was fetched and the latest date) and its method note. A section whose
  series aren't stored says which command collects them (`--fred`, `--shiller` or `--treasury`).
- **API**: `GET /api/market` returns `{available, buffett, cape, yield_curve, sources}`, computed at each request from
  the stored series (about 430 KB, since it carries the charts' series). `available` says whether any section is. Each
  section has `available`, `date` (its latest), `method` (the published note), `reason` and `collect`. A section whose
  series aren't stored has none of its figures: its `reason` says why and `collect` is the command that collects them
  (both null for an available section), so the answer is 200 before anything is collected. `sources` lists, per
  section, its `latest` date and its `fetches`: `source`, `name`, `url` and `fetched_at` (null if never fetched).

How to read each section:

- **The Buffett indicator** (`buffett`): the corporate equities over GDP, as a percentage. The ratio has a long-run
  trend, so the section reads it against that trend: `z` is how many standard deviations the latest quarter is from it
  (`sd` is the standard deviation of the log residuals), and `zone` names where that falls, with the `pill` the page
  colors it by:

  | z | `zone` | `pill` |
  |---|---|---|
  | below -2 | significantly undervalued | cheap |
  | -2 up to -1 | modestly undervalued | cheap |
  | -1 to +1 | fairly valued | fair |
  | above +1 up to +2 | modestly overvalued | expensive |
  | above +2 | significantly overvalued | expensive |

  The trend is the least-squares line through ln(ratio) against time over every quarter both series have. The section
  also gives `ratio`, `trend` (its value in the same quarter), `growth` (the trend's growth a year), `percentile` (the
  share of the quarters at or below the latest, as a fraction; null under 36 quarters), `date` (the latest quarter both
  series have, the first day of the quarter), `first_date`, `quarters`, `cutoffs`, `zones` and `series` (the chart's
  `dates`, `ratio`, `trend` and the bands at the trend times e to the ±1 and ±2 standard deviations: `lower_2sd`,
  `lower_1sd`, `upper_1sd`, `upper_2sd`). On 2026-10-24 the ratio read 255.0% for Q2 2026, the highest of 303 quarters
  (percentile 1.0), 1.68 standard deviations above its trend of 147.6%: *modestly overvalued*. A record ratio can still
  read that way: the zone depends on the span of the fit, and over 1947-2026 the spread around the trend is wide (0.325
  in logs). The same quarter would read +1.21 on a fit from 1970; the page fits the whole history.
- **Shiller's CAPE** (`cape`): the real price over the mean real earnings of the 120 months ending the month before.
  The section gives `value` and `date` (the latest month), and over its `months` months from `first_date` the `mean`,
  `median`, `min` (with `min_date`), `max` (with `max_date`) and `percentile` (a fraction), and `series` (`dates`,
  `values`). On 2026-10-24: 40.7 for 2026-10 against a mean of 17.8 and a median of 16.6 since 1881, at the 98.9th
  percentile. It says where the market's price stands against ten years of its earnings and its own history, not what
  comes next. Shiller's latest month is a single day's close with an estimated CPI.
- **The yield curve** (`yield_curve`): `curve` is the latest day's yield at each maturity that has one that day
  (`{maturity, label, value}`, shortest first), and `year_ago` (`{date, curve}`) the curve of the latest day on or before
  365 days earlier, so the section shows how the whole curve moved over the year. `spreads` has the 10-year less the
  2-year (`key` `10y_2y`) and the 10-year less the 3-month (`10y_3m`), in percentage points, on each day both have:
  `dates` and `values`, `days`, `latest_value`, `inverted` (below 0 now), `inverted_share` (a fraction of its days,
  since `first_date`), `last_inverted_day` and `latest_episode`. An inverted curve has short yields above long ones.
  `latest_episode` is the latest sustained inversion, `{first, last, days}` or null: the spread's runs of days below 0
  are joined into episodes across gaps of at most 20 of its days, and the latest episode with at least 20 days below 0
  counts (`days` is the days below 0, not the span), so a one-day dip after an inversion doesn't replace it. On
  2026-10-23 the 10-year less 2-year spread was +0.31 (last inverted 2024-09-05; sustained inversion 2022-07-06 to
  2024-09-05, 539 days) and the 10-year less 3-month +0.78 (last inverted 2025-10-16; sustained 2025-02-26 to 2025-09-15,
  85 days).

---

## Updating Data from the Web

You don't need the terminal to refresh the database — the web interface can run
collection itself. These surfaces are enabled by default (see
[disabling collection](#disabling-collection-read-only-deployments) below).

### Updating a company already in the database

Every stock page (`/stocks/{ticker}`) has an **UPDATE DATA** button next to the
company name. Clicking it submits a single-ticker collection job — the same
Yahoo + SEC collection a terminal run performs, and valuations are recomputed
automatically afterwards, so one click updates everything. Job status is shown
inline under the header, and when the job finishes the active tab reloads with
the fresh data.

### Fetching a ticker not yet in the database

Two ways to add a brand-new company:

- **Command palette** — press `Ctrl+K` (or `Cmd+K`), type the ticker; if it
  isn't in the database, an action row **Fetch {TICKER} from SEC/Yahoo**
  appears. Picking it opens the fetch page.
- **Direct URL** — visit `/stocks/{ticker}`. An unknown-but-plausible ticker
  gets a fetch page (still a 404 — the data genuinely isn't there yet) instead
  of a dead end.

On the fetch page, click **Fetch {TICKER} from SEC/Yahoo**. When the job
completes you land on the new company's workstation page, valuations included.

### Disabling collection (read-only deployments)

Web collection is on by default. To turn the write surfaces off, start the
server with `STOCK_WEB_ALLOW_COLLECTION` set to a falsy value (`0`, `false`,
`no`, or `off`, case-insensitive):

```bash
STOCK_WEB_ALLOW_COLLECTION=0 stock-web
```

When disabled, the UPDATE DATA button and the unknown-ticker fetch page are
hidden, and collection API requests are rejected (HTTP 409). The `stock-data`
CLI and `python -m src.valuation.backfill` work either way.

---

## Valuation Models

### DCF (Discounted Cash Flow)

```python
def dcf_valuation(stock: dict,
                  growth_rate: float = 0.10,
                  terminal_growth: float = 0.025,
                  discount_rate: float = None,
                  projection_years: int = 5) -> dict:
    """
    Calculate intrinsic value using DCF model.

    Args:
        stock: Stock data dictionary
        growth_rate: Expected FCF growth rate
        terminal_growth: Terminal growth rate (perpetuity)
        discount_rate: WACC (uses risk-free rate + premium if None)
        projection_years: Years to project

    Returns:
        Dictionary with DCF valuation results
    """
    # Get inputs
    calc = stock['calculated_metrics']
    market = stock['market_data']
    shareholders = stock['shareholders']
    risk_free = stock['risk_free_rate']

    fcf = calc.get('free_cash_flow')
    if not fcf or fcf <= 0:
        return {"error": "No positive FCF available"}

    # Discount rate (WACC approximation)
    if discount_rate is None:
        rf_rate = risk_free.get('risk_free_rate', 0.04)
        beta = market.get('beta', 1.0)
        equity_risk_premium = 0.05  # Historical average
        discount_rate = rf_rate + (beta * equity_risk_premium)

    # Project future cash flows
    projected_fcf = []
    current_fcf = fcf
    for year in range(1, projection_years + 1):
        current_fcf *= (1 + growth_rate)
        projected_fcf.append(current_fcf)

    # Calculate present value of projected FCF
    pv_fcf = sum(
        cf / ((1 + discount_rate) ** year)
        for year, cf in enumerate(projected_fcf, 1)
    )

    # Terminal value (Gordon Growth Model)
    terminal_fcf = projected_fcf[-1] * (1 + terminal_growth)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** projection_years)

    # Enterprise value
    enterprise_value = pv_fcf + pv_terminal

    # Equity value
    net_debt = calc.get('net_debt', 0)
    equity_value = enterprise_value - net_debt

    # Per share value
    shares = shareholders.get('shares_outstanding', 1)
    intrinsic_value = equity_value / shares

    # Current price comparison
    current_price = market.get('current_price', 0)
    upside = (intrinsic_value - current_price) / current_price if current_price else 0

    return {
        "current_fcf": fcf,
        "discount_rate": discount_rate,
        "growth_rate": growth_rate,
        "terminal_growth": terminal_growth,
        "pv_projected_fcf": pv_fcf,
        "pv_terminal_value": pv_terminal,
        "enterprise_value": enterprise_value,
        "net_debt": net_debt,
        "equity_value": equity_value,
        "shares_outstanding": shares,
        "intrinsic_value_per_share": intrinsic_value,
        "current_price": current_price,
        "upside_potential": upside,
        "margin_of_safety": upside
    }

# Example usage
stock = load_stock_data("AAPL")
dcf_result = dcf_valuation(stock, growth_rate=0.08, terminal_growth=0.025)

print(f"\nDCF Valuation for {stock['ticker']}")
print(f"{'='*40}")
print(f"Current FCF: ${dcf_result['current_fcf']:,.0f}")
print(f"Discount Rate (WACC): {dcf_result['discount_rate']*100:.1f}%")
print(f"Growth Rate: {dcf_result['growth_rate']*100:.1f}%")
print(f"Terminal Growth: {dcf_result['terminal_growth']*100:.1f}%")
print(f"Enterprise Value: ${dcf_result['enterprise_value']:,.0f}")
print(f"Intrinsic Value/Share: ${dcf_result['intrinsic_value_per_share']:.2f}")
print(f"Current Price: ${dcf_result['current_price']:.2f}")
print(f"Upside Potential: {dcf_result['upside_potential']*100:.1f}%")
```

### Graham Number

```python
def graham_number(stock: dict) -> dict:
    """
    Calculate Graham Number (Benjamin Graham's intrinsic value formula).

    Formula: sqrt(22.5 * EPS * Book Value per Share)

    The 22.5 comes from Graham's criteria:
    - P/E ratio should not exceed 15
    - P/B ratio should not exceed 1.5
    - 15 * 1.5 = 22.5
    """
    valuation = stock['valuation']
    market = stock['market_data']
    shareholders = stock['shareholders']

    # Get EPS (trailing)
    eps = valuation.get('eps_trailing')
    if not eps or eps <= 0:
        return {"error": "No positive EPS available"}

    # Calculate Book Value per Share
    # From SEC data
    annual = stock.get('financials_annual', {})
    if annual:
        latest_year = sorted(annual.keys(), reverse=True)[0]
        equity = annual[latest_year].get('total_equity') or 0
        shares = shareholders.get('shares_outstanding', 1)
        bvps = equity / shares if shares else 0
    else:
        # Fallback: calculate from P/B ratio
        price = market.get('current_price', 0)
        pb = valuation.get('price_to_book', 0)
        bvps = price / pb if pb else 0

    if bvps <= 0:
        return {"error": "No positive book value available"}

    # Graham Number calculation
    graham_value = (22.5 * eps * bvps) ** 0.5

    current_price = market.get('current_price', 0)
    upside = (graham_value - current_price) / current_price if current_price else 0

    return {
        "eps": eps,
        "book_value_per_share": bvps,
        "graham_number": graham_value,
        "current_price": current_price,
        "upside_potential": upside,
        "pe_ratio": current_price / eps if eps else None,
        "pb_ratio": current_price / bvps if bvps else None,
        "pe_x_pb": (current_price / eps) * (current_price / bvps) if eps and bvps else None
    }

# Example usage
stock = load_stock_data("AAPL")
graham = graham_number(stock)

print(f"\nGraham Number for {stock['ticker']}")
print(f"{'='*40}")
print(f"EPS (TTM): ${graham['eps']:.2f}")
print(f"Book Value/Share: ${graham['book_value_per_share']:.2f}")
print(f"Graham Number: ${graham['graham_number']:.2f}")
print(f"Current Price: ${graham['current_price']:.2f}")
print(f"P/E Ratio: {graham['pe_ratio']:.1f}")
print(f"P/B Ratio: {graham['pb_ratio']:.1f}")
print(f"P/E x P/B: {graham['pe_x_pb']:.1f} (Graham max: 22.5)")
print(f"Upside Potential: {graham['upside_potential']*100:.1f}%")
```

### Peter Lynch Fair Value

```python
def peter_lynch_value(stock: dict) -> dict:
    """
    Calculate Peter Lynch Fair Value.

    Lynch's Rule of Thumb:
    - Fair P/E = Earnings Growth Rate (%)
    - If dividend yield is significant, add it to growth rate

    PEG Ratio interpretation:
    - PEG < 1.0: Potentially undervalued
    - PEG = 1.0: Fairly valued
    - PEG > 1.0: Potentially overvalued
    """
    valuation = stock['valuation']
    market = stock['market_data']
    estimates = stock.get('analyst_estimates', {})
    dividends = stock.get('dividend_history', {})

    # Get current metrics
    current_price = market.get('current_price', 0)
    eps = valuation.get('eps_trailing')
    pe_ratio = valuation.get('pe_trailing')

    if not eps or eps <= 0:
        return {"error": "No positive EPS available"}

    # Get growth rate
    earnings_growth = estimates.get('earnings_growth')
    if not earnings_growth:
        # Fallback to revenue growth
        earnings_growth = valuation.get('revenue_growth', 0.10)

    growth_rate_pct = abs(earnings_growth) * 100  # Convert to percentage

    # Add dividend yield (Lynch's adjustment)
    div_yield = dividends.get('dividend_yield', 0) or 0
    div_yield_pct = div_yield * 100

    # Lynch Fair P/E = Growth Rate + Dividend Yield
    lynch_fair_pe = growth_rate_pct + div_yield_pct

    # Lynch Fair Value
    lynch_fair_value = eps * lynch_fair_pe

    # PEG Ratio
    peg_ratio = pe_ratio / growth_rate_pct if growth_rate_pct > 0 else None

    # Upside calculation
    upside = (lynch_fair_value - current_price) / current_price if current_price else 0

    return {
        "eps": eps,
        "pe_ratio": pe_ratio,
        "earnings_growth": earnings_growth,
        "earnings_growth_pct": growth_rate_pct,
        "dividend_yield": div_yield,
        "dividend_yield_pct": div_yield_pct,
        "lynch_fair_pe": lynch_fair_pe,
        "lynch_fair_value": lynch_fair_value,
        "current_price": current_price,
        "peg_ratio": peg_ratio,
        "upside_potential": upside,
        "lynch_verdict": "Undervalued" if peg_ratio and peg_ratio < 1 else "Overvalued" if peg_ratio and peg_ratio > 1.5 else "Fairly Valued"
    }

# Example usage
stock = load_stock_data("AAPL")
lynch = peter_lynch_value(stock)

print(f"\nPeter Lynch Valuation for {stock['ticker']}")
print(f"{'='*40}")
print(f"EPS (TTM): ${lynch['eps']:.2f}")
print(f"Current P/E: {lynch['pe_ratio']:.1f}")
print(f"Earnings Growth: {lynch['earnings_growth_pct']:.1f}%")
print(f"Dividend Yield: {lynch['dividend_yield_pct']:.1f}%")
print(f"Lynch Fair P/E: {lynch['lynch_fair_pe']:.1f}")
print(f"Lynch Fair Value: ${lynch['lynch_fair_value']:.2f}")
print(f"Current Price: ${lynch['current_price']:.2f}")
print(f"PEG Ratio: {lynch['peg_ratio']:.2f}")
print(f"Verdict: {lynch['lynch_verdict']}")
print(f"Upside Potential: {lynch['upside_potential']*100:.1f}%")
```

### Buffett-Style Analysis

```python
def buffett_analysis(stock: dict) -> dict:
    """
    Analyze stock using Warren Buffett's key principles.

    Buffett's criteria:
    1. Consistent earnings (predictable business)
    2. Good ROE (>15%)
    3. Low debt (Debt/Equity < 0.5 preferred)
    4. High profit margins
    5. Economic moat (sustainable competitive advantage)
    6. Shareholder-friendly management
    """
    valuation = stock['valuation']
    market = stock['market_data']
    calc = stock['calculated_metrics']
    dividends = stock.get('dividend_history', {})
    annual = stock.get('financials_annual', {})

    results = {
        "ticker": stock['ticker'],
        "company_name": stock['company_name'],
        "criteria": {},
        "score": 0,
        "max_score": 10
    }

    # 1. Return on Equity (target: >15%)
    roe = valuation.get('return_on_equity', 0) or 0
    roe_pass = roe > 0.15
    results['criteria']['ROE'] = {
        "value": f"{roe*100:.1f}%",
        "target": ">15%",
        "pass": roe_pass,
        "score": 2 if roe_pass else (1 if roe > 0.10 else 0)
    }
    results['score'] += results['criteria']['ROE']['score']

    # 2. Return on Invested Capital (target: >10%)
    roic = calc.get('roic', 0) or 0
    roic_pass = roic > 0.10
    results['criteria']['ROIC'] = {
        "value": f"{roic*100:.1f}%",
        "target": ">10%",
        "pass": roic_pass,
        "score": 2 if roic_pass else (1 if roic > 0.05 else 0)
    }
    results['score'] += results['criteria']['ROIC']['score']

    # 3. Debt to Equity (target: <0.5); Yahoo's debt_to_equity is a percent (78.4 is 0.784x)
    de_ratio = (valuation.get('debt_to_equity') or 0) / 100
    de_pass = de_ratio < 0.5
    results['criteria']['Debt/Equity'] = {
        "value": f"{de_ratio:.2f}",
        "target": "<0.5",
        "pass": de_pass,
        "score": 2 if de_pass else (1 if de_ratio < 1.0 else 0)
    }
    results['score'] += results['criteria']['Debt/Equity']['score']

    # 4. Profit Margin (target: >10%)
    margin = valuation.get('profit_margin', 0) or 0
    margin_pass = margin > 0.10
    results['criteria']['Profit Margin'] = {
        "value": f"{margin*100:.1f}%",
        "target": ">10%",
        "pass": margin_pass,
        "score": 2 if margin_pass else (1 if margin > 0.05 else 0)
    }
    results['score'] += results['criteria']['Profit Margin']['score']

    # 5. Earnings Consistency (check 5+ years of positive earnings)
    positive_years = 0
    if annual:
        for year_data in annual.values():
            net_income = year_data.get('net_income', 0)
            if net_income and net_income > 0:
                positive_years += 1
    consistency = positive_years >= 5
    results['criteria']['Earnings Consistency'] = {
        "value": f"{positive_years} years positive",
        "target": "5+ years",
        "pass": consistency,
        "score": 2 if consistency else (1 if positive_years >= 3 else 0)
    }
    results['score'] += results['criteria']['Earnings Consistency']['score']

    # Calculate overall grade
    score_pct = results['score'] / results['max_score']
    if score_pct >= 0.8:
        results['grade'] = 'A'
        results['verdict'] = 'Excellent - Meets Buffett criteria'
    elif score_pct >= 0.6:
        results['grade'] = 'B'
        results['verdict'] = 'Good - Mostly meets criteria'
    elif score_pct >= 0.4:
        results['grade'] = 'C'
        results['verdict'] = 'Fair - Partially meets criteria'
    else:
        results['grade'] = 'D'
        results['verdict'] = 'Poor - Does not meet criteria'

    return results

# Example usage
stock = load_stock_data("AAPL")
buffett = buffett_analysis(stock)

print(f"\nBuffett Analysis for {buffett['ticker']}")
print(f"{'='*50}")
print(f"Company: {buffett['company_name']}")
print(f"\nCriteria Analysis:")
for name, data in buffett['criteria'].items():
    status = "✓" if data['pass'] else "✗"
    print(f"  {status} {name}: {data['value']} (target: {data['target']}) [{data['score']}/2]")

print(f"\nOverall Score: {buffett['score']}/{buffett['max_score']}")
print(f"Grade: {buffett['grade']}")
print(f"Verdict: {buffett['verdict']}")
```

---

## Financial Analysis Examples

### Profitability Analysis

```python
def profitability_analysis(stock: dict) -> pd.DataFrame:
    """Analyze profitability trends over time."""
    annual = stock['financials_annual']

    data = []
    for year in sorted(annual.keys()):
        fin = annual[year]
        revenue = fin.get('revenue') or 0
        gross_profit = fin.get('gross_profit', 0)
        operating_income = fin.get('operating_income', 0)
        net_income = fin.get('net_income', 0)

        if revenue > 0:
            data.append({
                'Year': year,
                'Revenue': revenue,
                'Gross Margin': gross_profit / revenue if gross_profit else None,
                'Operating Margin': operating_income / revenue if operating_income else None,
                'Net Margin': net_income / revenue if net_income else None
            })

    return pd.DataFrame(data)

stock = load_stock_data("AAPL")
prof = profitability_analysis(stock)
print(prof.to_string(index=False))
```

### Cash Flow Analysis

```python
def cash_flow_analysis(stock: dict) -> pd.DataFrame:
    """Analyze cash flow trends and quality."""
    annual = stock['financials_annual']

    data = []
    for year in sorted(annual.keys()):
        fin = annual[year]
        net_income = fin.get('net_income', 0)
        ocf = fin.get('operating_cash_flow', 0)
        capex = fin.get('capex', 0)

        # FCF = Operating Cash Flow - CapEx
        fcf = ocf - abs(capex) if ocf and capex else None

        # Cash Flow Quality = OCF / Net Income (>1 is good)
        cf_quality = ocf / net_income if net_income and net_income > 0 else None

        data.append({
            'Year': year,
            'Net Income': net_income,
            'Operating CF': ocf,
            'CapEx': capex,
            'Free Cash Flow': fcf,
            'CF Quality': cf_quality
        })

    return pd.DataFrame(data)

stock = load_stock_data("AAPL")
cf = cash_flow_analysis(stock)
print(cf.to_string(index=False))
```

### Peer Comparison

```python
def peer_comparison(tickers: list) -> pd.DataFrame:
    """Compare key metrics across multiple stocks."""
    data = []

    for ticker in tickers:
        try:
            stock = load_stock_data(ticker)
            val = stock['valuation']
            calc = stock['calculated_metrics']
            market = stock['market_data']

            data.append({
                'Ticker': ticker,
                'Market Cap': market.get('market_cap'),
                'P/E': val.get('pe_trailing'),
                'P/B': val.get('price_to_book'),
                'ROE': val.get('return_on_equity'),
                'Profit Margin': val.get('profit_margin'),
                'Debt/Equity': val.get('debt_to_equity'),
                'EV/EBITDA': calc.get('ev_to_ebitda'),
                'ROIC': calc.get('roic')
            })
        except FileNotFoundError:
            print(f"Warning: Data not found for {ticker}")

    df = pd.DataFrame(data)

    # Add ranks
    for col in ['P/E', 'P/B', 'Debt/Equity', 'EV/EBITDA']:
        if col in df.columns:
            df[f'{col} Rank'] = df[col].rank()  # Lower is better

    for col in ['ROE', 'Profit Margin', 'ROIC']:
        if col in df.columns:
            df[f'{col} Rank'] = df[col].rank(ascending=False)  # Higher is better

    return df

# Compare tech giants
peers = peer_comparison(['AAPL', 'MSFT', 'GOOGL'])
print(peers.to_string(index=False))
```

---

## Building Dashboards

### Streamlit Dashboard Example

```python
# Save as dashboard.py
# Run with: streamlit run dashboard.py

import streamlit as st
import pandas as pd
import json
from pathlib import Path

st.title("Stock Data Dashboard")

# Load available tickers
json_dir = Path("data/output/json")
available_tickers = [f.stem for f in json_dir.glob("*.json")]

# Ticker selection
selected_ticker = st.selectbox("Select Ticker", available_tickers)

if selected_ticker:
    # Load data
    with open(json_dir / f"{selected_ticker}.json") as f:
        stock = json.load(f)

    # Company header
    st.header(f"{stock['company_name']} ({stock['ticker']})")
    st.write(f"Sector: {stock['company_info'].get('sector', 'N/A')}")

    # Key metrics
    col1, col2, col3, col4 = st.columns(4)

    market = stock['market_data']
    valuation = stock['valuation']

    col1.metric("Price", f"${market.get('current_price', 0):.2f}")
    col2.metric("Market Cap", f"${market.get('market_cap', 0)/1e9:.1f}B")
    col3.metric("P/E Ratio", f"{valuation.get('pe_trailing', 0):.1f}")
    col4.metric("Dividend Yield", f"{valuation.get('dividend_yield', 0)*100:.2f}%")

    # Analyst estimates
    st.subheader("Analyst Estimates")
    estimates = stock.get('analyst_estimates', {})
    st.write(f"Target Price: ${estimates.get('target_price_mean', 0):.2f}")
    st.write(f"Recommendation: {estimates.get('recommendation', 'N/A')}")
    st.write(f"Number of Analysts: {estimates.get('number_of_analysts', 0)}")

    # Financial trends
    st.subheader("Financial Trends")
    annual = stock.get('financials_annual', {})
    if annual:
        years = sorted(annual.keys())
        revenue = [annual[y].get('revenue') for y in years]
        net_income = [annual[y].get('net_income') for y in years]

        chart_data = pd.DataFrame({
            'Year': years,
            'Revenue': revenue,
            'Net Income': net_income
        })
        st.line_chart(chart_data.set_index('Year'))
```

### Plotly Visualization Example

```python
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def create_financial_dashboard(stock: dict):
    """Create interactive Plotly dashboard."""
    annual = stock['financials_annual']
    years = sorted(annual.keys())

    # Extract data
    revenue = [annual[y].get('revenue') for y in years]
    net_income = [annual[y].get('net_income') for y in years]
    ocf = [annual[y].get('operating_cash_flow') for y in years]

    # Create subplots
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=('Revenue Trend', 'Net Income Trend',
                       'Operating Cash Flow', 'Profitability Margins')
    )

    # Revenue
    fig.add_trace(
        go.Bar(x=years, y=revenue, name='Revenue'),
        row=1, col=1
    )

    # Net Income
    fig.add_trace(
        go.Bar(x=years, y=net_income, name='Net Income'),
        row=1, col=2
    )

    # Operating Cash Flow
    fig.add_trace(
        go.Bar(x=years, y=ocf, name='OCF'),
        row=2, col=1
    )

    # Margins
    margins = []
    for y in years:
        rev = annual[y].get('revenue') or 1
        ni = annual[y].get('net_income') or 0
        margins.append(ni / rev * 100)

    fig.add_trace(
        go.Scatter(x=years, y=margins, name='Net Margin %', mode='lines+markers'),
        row=2, col=2
    )

    fig.update_layout(
        title=f"{stock['company_name']} Financial Overview",
        height=600,
        showlegend=True
    )

    return fig

# Usage
stock = load_stock_data("AAPL")
fig = create_financial_dashboard(stock)
fig.show()
```

---

## Data Quality Checks

```python
def check_data_quality(stock: dict) -> dict:
    """
    Perform data quality checks on stock data.

    Returns dictionary with quality metrics and issues found.
    """
    issues = []
    warnings = []

    # Check essential fields
    essential_fields = [
        ('market_data', 'current_price'),
        ('market_data', 'market_cap'),
        ('valuation', 'pe_trailing'),
        ('valuation', 'eps_trailing'),
        ('calculated_metrics', 'free_cash_flow'),
    ]

    for section, field in essential_fields:
        value = stock.get(section, {}).get(field)
        if value is None:
            issues.append(f"Missing: {section}.{field}")

    # Check SEC data availability
    annual = stock.get('financials_annual', {})
    if not annual:
        issues.append("No SEC annual financial data")
    elif len(annual) < 3:
        warnings.append(f"Only {len(annual)} years of SEC data (expected 5+)")

    # Check for data freshness
    from datetime import datetime, timedelta
    collected = stock.get('collected_at', '')
    if collected:
        collected_date = datetime.fromisoformat(collected.replace('Z', '+00:00'))
        if datetime.now(collected_date.tzinfo) - collected_date > timedelta(days=7):
            warnings.append("Data is more than 7 days old")

    # Check for errors in collection
    if stock.get('errors'):
        for error in stock['errors']:
            issues.append(f"Collection error: {error}")

    # Calculate completeness score
    total_sections = ['company_info', 'market_data', 'valuation', 'shareholders',
                      'financials_annual', 'calculated_metrics', 'analyst_estimates',
                      'dividend_history', 'price_history', 'risk_free_rate']

    filled_sections = sum(1 for s in total_sections if stock.get(s))
    completeness = filled_sections / len(total_sections)

    return {
        "ticker": stock['ticker'],
        "completeness": completeness,
        "completeness_pct": f"{completeness*100:.0f}%",
        "issues": issues,
        "warnings": warnings + stock.get('warnings', []),
        "data_sources": stock.get('data_sources', []),
        "collected_at": stock.get('collected_at'),
        "years_of_data": len(annual),
        "is_healthy": len(issues) == 0
    }

# Check all stocks
import os
json_dir = "data/output/json"
for filename in os.listdir(json_dir):
    if filename.endswith('.json'):
        with open(os.path.join(json_dir, filename)) as f:
            stock = json.load(f)
        quality = check_data_quality(stock)
        status = "✓" if quality['is_healthy'] else "✗"
        print(f"{status} {quality['ticker']}: {quality['completeness_pct']} complete, {len(quality['issues'])} issues")
```

---

## Common Use Cases

### 1. Stock Screener

```python
def screen_stocks(criteria: dict) -> pd.DataFrame:
    """
    Screen stocks based on custom criteria.

    Example criteria:
    {
        'pe_trailing': {'max': 20},
        'return_on_equity': {'min': 0.15},
        'debt_to_equity': {'max': 50},
        'market_cap': {'min': 10_000_000_000}
    }
    """
    df = pd.read_csv("data/output/csv/summary.csv")

    for field, rules in criteria.items():
        if 'min' in rules:
            df = df[df[field] >= rules['min']]
        if 'max' in rules:
            df = df[df[field] <= rules['max']]

    return df

# Example: Find undervalued, profitable, low-debt stocks
results = screen_stocks({
    'pe_trailing': {'max': 25, 'min': 0},
    'return_on_equity': {'min': 0.12},
    'debt_to_equity': {'max': 75},
    'profit_margin': {'min': 0.08}
})
print(results[['ticker', 'company_name', 'pe_trailing', 'return_on_equity']])
```

### 2. Portfolio Tracker

```python
def analyze_portfolio(holdings: dict) -> pd.DataFrame:
    """
    Analyze a portfolio of stocks.

    holdings: {'AAPL': 100, 'MSFT': 50, 'GOOGL': 25}  # shares owned
    """
    portfolio_data = []

    for ticker, shares in holdings.items():
        stock = load_stock_data(ticker)
        market = stock['market_data']
        valuation = stock['valuation']

        price = market.get('current_price', 0)
        value = price * shares

        portfolio_data.append({
            'Ticker': ticker,
            'Shares': shares,
            'Price': price,
            'Value': value,
            'P/E': valuation.get('pe_trailing'),
            'Div Yield': valuation.get('dividend_yield', 0),
            'Annual Dividend': value * valuation.get('dividend_yield', 0)
        })

    df = pd.DataFrame(portfolio_data)
    df['Weight'] = df['Value'] / df['Value'].sum()

    # Portfolio summary
    print(f"Total Value: ${df['Value'].sum():,.2f}")
    print(f"Annual Dividends: ${df['Annual Dividend'].sum():,.2f}")
    print(f"Weighted P/E: {(df['P/E'] * df['Weight']).sum():.1f}")

    return df

portfolio = analyze_portfolio({'AAPL': 100, 'MSFT': 50, 'GOOGL': 25})
print(portfolio.to_string(index=False))
```

### 3. Dividend Growth Analysis

```python
def dividend_growth_analysis(ticker: str) -> dict:
    """Analyze dividend growth history and sustainability."""
    stock = load_stock_data(ticker)
    dividends = stock.get('dividend_history', {})
    valuation = stock['valuation']
    calc = stock['calculated_metrics']

    annual_divs = dividends.get('annual_dividends', {})

    # Calculate growth rates
    years = sorted(annual_divs.keys())
    if len(years) >= 2:
        div_values = [annual_divs[y] for y in years]

        # Year-over-year growth
        yoy_growth = []
        for i in range(1, len(div_values)):
            if div_values[i-1] > 0:
                growth = (div_values[i] - div_values[i-1]) / div_values[i-1]
                yoy_growth.append(growth)

        avg_growth = sum(yoy_growth) / len(yoy_growth) if yoy_growth else 0
    else:
        avg_growth = 0

    # Dividend sustainability
    payout_ratio = dividends.get('payout_ratio', 0)
    fcf = calc.get('free_cash_flow', 0)
    total_dividends = valuation.get('dividend_yield', 0) * stock['market_data'].get('market_cap', 0)
    fcf_coverage = fcf / total_dividends if total_dividends > 0 else float('inf')

    return {
        "ticker": ticker,
        "current_yield": dividends.get('dividend_yield'),
        "dividend_rate": dividends.get('dividend_rate'),
        "years_of_dividends": dividends.get('years_of_data'),
        "dividend_cagr": dividends.get('dividend_cagr'),
        "avg_yoy_growth": avg_growth,
        "payout_ratio": payout_ratio,
        "fcf_coverage": fcf_coverage,
        "sustainable": payout_ratio < 0.75 and fcf_coverage > 1.2
    }

result = dividend_growth_analysis("AAPL")
print(f"Dividend Analysis for {result['ticker']}")
print(f"Current Yield: {result['current_yield']*100:.2f}%")
print(f"Dividend CAGR: {result['dividend_cagr']*100:.1f}%")
print(f"Payout Ratio: {result['payout_ratio']*100:.1f}%")
print(f"FCF Coverage: {result['fcf_coverage']:.1f}x")
print(f"Sustainable: {'Yes' if result['sustainable'] else 'No'}")
```

---

## Tips and Best Practices

### 1. Handle Missing Data

```python
def safe_get(data: dict, *keys, default=None):
    """Safely navigate nested dictionaries."""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return default
        if data is None:
            return default
    return data

# Usage
eps = safe_get(stock, 'valuation', 'eps_trailing', default=0)
```

### 2. Cache Loaded Data

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def load_stock_cached(ticker: str) -> dict:
    """Load stock data with caching."""
    return load_stock_data(ticker)
```

### 3. Batch Processing

```python
from concurrent.futures import ThreadPoolExecutor

def process_all_stocks(processor_func):
    """Process all stocks in parallel."""
    json_dir = Path("data/output/json")
    tickers = [f.stem for f in json_dir.glob("*.json")]

    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(processor_func, t): t for t in tickers}
        for future in futures:
            try:
                results.append(future.result())
            except Exception as e:
                print(f"Error processing {futures[future]}: {e}")

    return results
```

---

## Next Steps

1. **Build Custom Valuation Models**: Use the DCF, Graham, and Lynch templates as starting points
2. **Create Automated Reports**: Combine analysis functions into PDF/HTML reports
3. **Set Up Alerts**: Monitor for significant changes in key metrics
4. **Machine Learning**: Use historical data for price prediction models
5. **Backtesting**: Test investment strategies using historical data

---

**Questions?** Check the main [README.md](README.md) for system setup and data collection instructions.
