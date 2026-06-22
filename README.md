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
| `--formats`, `-f` | Output formats (`json`, `csv`) | `json csv` |
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
