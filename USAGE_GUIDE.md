# Stock Data Usage Guide

A comprehensive guide on how to use the output data from the Stock Data Collection System for financial analysis, valuation models, and investment research.

---

## Table of Contents

1. [Output Files Overview](#output-files-overview)
2. [Loading Data in Python](#loading-data-in-python)
3. [Working with JSON Data](#working-with-json-data)
4. [Working with CSV Data](#working-with-csv-data)
5. [Valuation Layer](#valuation-layer) — built-in, stored fair-value models
   - [Owner Earnings ("Buffett mode")](#owner-earnings-buffett-mode) — separate lens, excluded from the median
6. [Valuation Models](#valuation-models) — DIY formulas over the raw JSON/CSV
   - [DCF (Discounted Cash Flow)](#dcf-discounted-cash-flow)
   - [Graham Number](#graham-number)
   - [Peter Lynch Fair Value](#peter-lynch-fair-value)
   - [Buffett-Style Analysis](#buffett-style-analysis)
7. [Financial Analysis Examples](#financial-analysis-examples)
8. [Building Dashboards](#building-dashboards)
9. [Data Quality Checks](#data-quality-checks)
10. [Common Use Cases](#common-use-cases)

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
# - collected_at
# - company_info
# - market_data
# - valuation
# - shareholders
# - yahoo_financials
# - financials_annual
# - financials_quarterly
# - sec_submissions
# - insider_transactions
# - price_history
# - analyst_estimates
# - dividend_history
# - calculated_metrics
# - risk_free_rate
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
print(f"Revenue: ${financials.get('Revenue', financials.get('Net Revenue', 0)):,.0f}")
print(f"Net Income: ${financials.get('Net Income', 0):,.0f}")
print(f"Total Assets: ${financials.get('Total Assets', 0):,.0f}")
print(f"Total Debt: ${financials.get('Long-Term Debt', 0):,.0f}")
print(f"Operating Cash Flow: ${financials.get('Operating Cash Flow', 0):,.0f}")
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
metrics = ['Revenue', 'Net Revenue', 'Net Income', 'Operating Cash Flow']
trends = get_financial_trends(stock, metrics)

# Calculate growth rates
trends['Revenue_Final'] = trends['Revenue'].fillna(trends['Net Revenue'])
trends['Revenue_Growth'] = trends['Revenue_Final'].pct_change()

print(trends[['Year', 'Revenue_Final', 'Net Income', 'Revenue_Growth']])
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
print(f"Years of Dividends: {dividends['years_of_dividends']}")
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
    (df['debt_to_equity'] < 100)
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

Valuations recompute **automatically** at the end of every `python -m src.main` collection
run (for the tickers just collected) — you normally don't need to run anything extra. To
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
        equity = annual[latest_year].get('Total Stockholders Equity', 0)
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

    # 3. Debt to Equity (target: <0.5)
    de_ratio = valuation.get('debt_to_equity', 0) or 0
    de_ratio = de_ratio / 100 if de_ratio > 10 else de_ratio  # Normalize if in percentage
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
            net_income = year_data.get('Net Income', 0)
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
        revenue = fin.get('Revenue') or fin.get('Net Revenue') or 0
        gross_profit = fin.get('Gross Profit', 0)
        operating_income = fin.get('Operating Income', 0)
        net_income = fin.get('Net Income', 0)

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
        net_income = fin.get('Net Income', 0)
        ocf = fin.get('Operating Cash Flow', 0)
        capex = fin.get('CapEx', 0) or fin.get('Capital Expenditures', 0)

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
        revenue = [annual[y].get('Revenue') or annual[y].get('Net Revenue') for y in years]
        net_income = [annual[y].get('Net Income') for y in years]

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
    revenue = [annual[y].get('Revenue') or annual[y].get('Net Revenue') for y in years]
    net_income = [annual[y].get('Net Income') for y in years]
    ocf = [annual[y].get('Operating Cash Flow') for y in years]

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
        rev = annual[y].get('Revenue') or annual[y].get('Net Revenue') or 1
        ni = annual[y].get('Net Income') or 0
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
        "years_of_dividends": dividends.get('years_of_dividends'),
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
