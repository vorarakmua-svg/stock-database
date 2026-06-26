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
| `tickers` | Stock ticker symbols (required) | - |
| `--output-dir`, `-o` | Base output directory | `./data` |
| `--formats`, `-f` | Output formats (`json`, `csv`, `sqlite`) | `json csv sqlite` |
| `--db` | SQLite database path | `<output-dir>/output/stock.db` |
| `--workers`, `-w` | Tickers fetched concurrently (1 = sequential) | `4` |
| `--years` | Years of historical data | `10` |
| `--no-yahoo` | Skip Yahoo Finance data | `false` |
| `--no-sec` | Skip SEC EDGAR data | `false` |
| `--sec-user-agent` | SEC API User-Agent header | `StockDataCollector admin@example.com` |
| `--verbose`, `-v` | Enable debug logging | `false` |

### Examples

```bash
# Fetch 10 years of data for tech stocks
python -m src.main AAPL MSFT GOOGL AMZN META

# Yahoo Finance data only (faster)
python -m src.main TSLA --no-sec

# SEC data only (more comprehensive financials)
python -m src.main AAPL --no-yahoo

# Custom SEC User-Agent (recommended for production)
python -m src.main AAPL --sec-user-agent "MyCompany contact@mycompany.com"
```

## Output

### Directory Structure

```
data/
└── output/
    ├── json/
    │   ├── AAPL.json      # Full data per ticker
    │   ├── MSFT.json
    │   └── GOOGL.json
    └── csv/
        └── summary.csv    # Summary metrics for all tickers
```

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
    "years_of_dividends": 12,
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
- Insider transactions (Form 4)

**Rate Limits:**
- SEC EDGAR: 10 requests/second (we use 0.12s delay)
- Yahoo Finance: Conservative 2.5s delay between tickers

### FRED (Federal Reserve Economic Data)

- 10-Year Treasury Constant Maturity Rate (DGS10)
- Used for WACC discount rate calculations

## Valuation Model Support

This data pipeline provides all inputs needed for:

| Model | Required Data | Coverage |
|-------|--------------|----------|
| **DCF** | FCF, growth rates, WACC, risk-free rate | 100% |
| **Graham Number** | EPS, Book Value per Share | 100% |
| **Peter Lynch** | PEG ratio, earnings growth, dividends | 100% |
| **Buffett Analysis** | ROE, margins, debt levels, moat indicators | 100% |

## Project Structure

```
stock-database/
├── src/
│   ├── __init__.py
│   ├── main.py                    # CLI entry point
│   ├── config.py                  # Configuration
│   ├── fetchers/
│   │   ├── stock_data_fetcher.py  # Main orchestrator
│   │   ├── yahoo_handler.py       # Yahoo Finance API
│   │   ├── sec_handler.py         # SEC EDGAR API
│   │   ├── fred_handler.py        # FRED API
│   │   └── rate_limiter.py        # Rate limiting
│   ├── parsers/
│   │   ├── xbrl_parser.py         # SEC XBRL parsing
│   │   └── calculated_metrics.py  # Financial calculations
│   ├── models/
│   │   └── stock_data.py          # Data models
│   ├── exporters/
│   │   ├── json_exporter.py       # JSON output (with merge)
│   │   └── csv_exporter.py        # CSV summary
│   └── mappings/
│       └── xbrl_tags.py           # GAAP tag mappings
├── data/
│   └── output/
│       ├── json/
│       └── csv/
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
noninterest income.

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
| **Bank** | net interest margin\*, efficiency ratio, loan-to-deposit | EBITDA family, ROIC/NOPAT/invested capital, interest coverage, gross/operating margin, inventory & receivables turnover, asset turnover, working capital, net/total debt, FCF family |
| **Insurer** | loss ratio, combined ratio\* | EBITDA family, ROIC/NOPAT/invested capital, inventory turnover, gross margin, asset turnover, working capital |
| **REIT** | FFO\*, AFFO\*, FFO/share, FFO payout | ROIC/NOPAT/invested capital, inventory & receivables turnover, gross margin, asset turnover, FCF family |

General operating companies (and utilities/energy) get the full generic ratio
suite unchanged. A suppressed ratio is stored as `NULL`, so a screen such as
`WHERE roic > 0.15` automatically excludes sectors where ROIC is undefined
instead of returning a misleading value.

\* **Proxy** (the registry doesn't split out the exact inputs):
net interest margin = `net_interest_income / total_assets`;
combined ratio = `benefits_and_expenses / premiums_earned`;
FFO = `net_income + total D&A` (no real-estate-specific D&A or gains-on-sale
adjustment); AFFO = `FFO − total capex`. Each proxy is flagged in the metrics
JSON under `_basis`.

### Integrity checks (data-quality score)

Beyond required-field and accounting-identity checks, the quality layer runs four
**flag-only** integrity checks (they surface issues via findings + the 0–100 score; they
never alter data):

| Check | Catches | Threshold | Penalty |
|---|---|---|---|
| Magnitude outlier | a USD field that spikes then reverts to its prior level — a one-off filing/tag error (persistent step-changes like M&A goodwill are not flagged) | spike ≥ 100× both adjacent years | −25 |
| Cash-flow consistency | the cash-flow statement's reported net change != its own sections + FX effect | residual > 1% | −10 |
| Quarterly-sum | discrete quarters that don't sum to the annual figure | per-field > 1% | −10 |
| Ratio bounds | a computed metric outside its plausible range (e.g. >100% gross margin) | impossibility bounds | −3 |

Thresholds are deliberately wide (a $1M materiality floor; the most recent 5 fiscal years are
scored), so clean filings keep a score of 100. Findings appear in `data_quality.findings` and,
for medium+ severity, in `warnings`.

### Fiscal vs calendar year

Companies have different fiscal year-ends (Microsoft June, Apple September, Walmart
January), so comparing by fiscal-year number mixes different macroeconomic windows. Each
period therefore carries two labels:

- `fiscal_year` — the company's own fiscal year (deterministic, from the period-end date).
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
  filed as a 10-Q (`Q4 = annual − 9-month YTD`). Derived values are tagged in
  `_source_tags`.
- **Balance-sheet checkpoints** at every quarter-end (instant facts), full history.
- **`fiscal_quarter`** (1–4, company calendar) and **`calendar_quarter`** (1–4, calendar)
  are both attached; `calendar_quarter` comes from the SEC frame and is the reliable
  cross-company key.
- **TTM (`financials_ttm`)** — trailing-twelve-month series (rolling 4 discrete quarters
  for flows, balance sheet as-of) for seasonality-free, up-to-date comparison.

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

Tables: `companies`, `financials_annual`, `financials_quarterly`, `metrics_annual`,
`market_snapshots`, `collection_runs`. All writes are idempotent upserts, so re-runs
update in place.

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

## Limitations

- **US Stocks Only**: SEC EDGAR only covers US-listed companies
- **Free Data Sources**: No premium data feeds (Bloomberg, Refinitiv, etc.)
- **Rate Limits**: SEC allows 10 req/sec; we're conservative at ~8 req/sec
- **Historical Depth**: SEC XBRL data typically available from 2009+
- **Real-Time Data**: Yahoo Finance data may have 15-20 minute delay

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This tool is for educational and research purposes only. The data collected should not be used as the sole basis for investment decisions. Always verify data accuracy and consult with qualified financial professionals before making investment decisions.

---

**Built with Python** | **Free Data Sources Only** | **Valuation Model Ready**
