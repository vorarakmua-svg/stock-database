# More valuation tools and the market pages: design (Milestone 4)

Plan: `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` ("Milestones 2-5", M4). Build loop:
`qa/BUILD_LOOP_PROMPT.md`.

## Goal

GuruFocus shows, beside its own GF Value, a row of other intrinsic values (a reverse DCF, the earnings power value,
NCAV and the Graham net-net, the median P/S value, the Graham number) and market-wide pages (the Buffett
indicator, the Shiller P/E, the yield curve). This milestone adds both, with every formula published:
- **Indicators** for each company: display-only, like owner earnings. They never enter the six models' median,
  the verdicts or the upside; `MEDIAN_MODELS` and the `valuations` table are untouched (a test pins both).
- **A market page** (`/market`): the Buffett indicator, Shiller's CAPE and the Treasury yield curve.

## Facts that shape the design (verified 2026-10-24)

- The DCF row's assumptions store `fcf_basis`, `shares_outstanding`, `discount_base`, `growth_base` and
  `terminal_growth`, so a reverse DCF can solve for growth without re-reading the inputs.
- M1's `valuation_ranges` row for `ps` already holds `implied_value`: the 10-year median P/S times the trailing
  revenue per share (`history.implied_price`). The median P/S value is that number.
- `financials_annual` has `goodwill` and `intangible_assets` (mapped from `IntangibleAssetsNetExcludingGoodwill`,
  else `FiniteLivedIntangibleAssetsNet`: neither includes goodwill), and the cash, short-term investment,
  receivable and inventory figures. M2's `quality_scores` has `ncav_per_share` and `wacc`, and M2's annual rows
  (`scores.fundamentals.load_fundamentals`) carry each year's figures with the debt reading (`read_debt`).
- `compute_and_store` runs, per ticker and each in its own try/except, the valuations, the valuation history and
  the quality scores (`scores.engine.store_ticker`), then re-ranks the universe.
- The 10-year rate the models use is stored in `market_snapshots.risk_free_rate`, with its day and source.
- FRED serves `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>` without a key (6 KB each):
  `NCBEILQ027S` (nonfinancial corporate business, corporate equities, a liability level, quarterly, $ millions,
  from 1945-10) and `GDP` (quarterly, $ billions SAAR, from 1947-01). On Q2 2026 they read $83.05T and $32.56T.
- Shiller's data: `https://shillerdata.com/` links `ie_data.xls` on a CDN under a versioned URL
  (`img1.wsimg.com/.../ie_data.xls?ver=...`), so the link is read from that page each time. The file is a
  legacy Excel workbook (BIFF8 in an OLE2 container, 1.6 MB, saved 2026-10-05).
- No `.xls` reader is installed (no `xlrd`); `openpyxl` reads only `.xlsx`. Installing `xlrd` would change the
  user's environment, which the loop doesn't do, so the milestone carries its own reader (below); `xlrd` is
  NEEDS-USER (Q14).
- Treasury.gov's yearly CSV (`daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve`), already read
  for the 10-year rate (`FredHandler._fetch_treasury_gov_year`), carries every maturity (1 month to 30 years) per
  trading day, newest first.

## Indicators (`src/valuation/indicators.py`)

Computed per company after its scores (they read M2's figures), into `valuation_indicators`. Each one has a base
value, a bear and a bull where the method gives them, an N/A reason otherwise, and its inputs in `assumptions`.

- **Reverse DCF**: the growth the price implies. Solve `dcf_per_share(fcf_basis, shares, g, discount_base,
  terminal_growth) = price` for `g` (the same 10-year two-stage DCF: years 1-5 at `g`, a fade to the terminal
  growth by year 10) by bisection over -50% to +100%. Shown beside the DCF's own `growth_base`. Stored at the
  latest snapshot's price (for the screener) and recomputed against the live price on the VAL tab. N/A without
  an applicable DCF, without a price, or when no `g` in the range reaches the price.
- **Earnings power value (EPV)**, Greenwald's no-growth value:
  - normalized EBIT = the mean operating margin of the last 5 fiscal years (at least 3) x the latest revenue; a
    year without operating income takes pretax income plus interest expense, GuruFocus's EBIT (an untagged
    interest counts as 0, as `metrics_annual`'s EBITDA takes it), and those years are listed. Thirteen large
    caps file no operating income in any of their last five years (BMY, COP, CVX, ETN, GE, IBM, JNJ, KLAC, LLY,
    MRK, PFE, TJX, XOM; BUG-203) but do file pretax income;
  - adjusted earnings = normalized EBIT x (1 - t), t the effective tax rate as M2's WACC takes it (income tax /
    pretax income, clamped to 0-35%, else 21%); maintenance capex is taken as equal to D&A, so neither is added;
  - EPV of the firm = adjusted earnings / WACC (M2's `wacc`); of the equity = that - total debt + cash and
    short-term investments (M2's debt reading: a debt-free filer's debt is 0, an unknown debt makes EPV N/A);
  - per share over today's count (the valuation inputs' `shares_outstanding`, the count the DCF divides by; N/A
    without one); bear and bull at WACC + 1 and - 1 point (no bull at a rate of 0 or less). A value below 0
    (debt above the earnings power: D, INTC) is stored as it is.
  - N/A for banks, insurers and REITs (no operating EBIT), without a WACC, or when adjusted earnings are not
    positive.
- **NCAV and the Graham net-net**: NCAV per share (M2's: current assets less total liabilities over the count) as
  the base; as the bear, Graham's stricter net-net working capital per share (cash and short-term investments +
  0.75 x receivables + 0.5 x inventory - total liabilities, over the same count) when it is positive; Graham's
  buy line, two thirds of NCAV, in the assumptions and on the VAL tab. N/A when NCAV isn't positive (nearly every
  large cap: say so), and for banks and insurers (no current assets).
- **Median P/S value**: `valuation_ranges(ps).implied_value` as the base; bear and bull scaled by the P/S range's
  p25 and p75 (`implied_value x p25 / median`, `x p75 / median`). N/A where the P/S range is N/A (under 36 months
  of history, no trailing revenue, ...), with its reason.
- **Graham number on tangible book**: `sqrt(22.5 x EPS x tangible book per share)`, tangible book = total equity -
  goodwill - intangible assets, on the Graham model's EPS and share basis; bear and bull on the lowest and highest
  positive EPS of the last 3 fiscal years, as the Graham model does. N/A when EPS or tangible book isn't positive
  (common for acquisitive companies: say so). A missing goodwill or intangibles figure counts as 0 and is listed:
  a filer that tags no intangibles (KO's trademarks) or only its finite-lived ones keeps them in tangible book.

Storage: `valuation_indicators` (ticker, indicator, applicable, na_reason, value_bear, value_base, value_bull,
unit ('per_share' or 'rate'), assumptions (JSON), basis_fiscal_year, computed_at; PK (ticker, indicator)),
replaced per ticker in one transaction. Engine: `compute_and_store` runs it after the scores, in its own
try/except (a failure never blocks the valuations, the history or the scores); the backfill fills it.

## The market data (`src/market/`)

- **The reader** (`xls.py`): the OLE2 compound file's `Workbook` stream (the header, the FAT and its DIFAT chain,
  the directory, the stream's sector chain), then BIFF8 records: `BOUNDSHEET` for the sheets, `SST` with its
  `CONTINUE` records for the shared strings, and in the sheet `NUMBER`, `RK`, `MULRK`, `LABELSST` and `FORMULA`
  (its cached number). Enough for Shiller's `Data` sheet; anything else is skipped. About 200 lines, tested on a
  trimmed real workbook built from the real file's records, and against the real file in the real-data check.
- **Sources** (`collect.py`), through one `requests` session with the app's default User-Agent, each answer
  cached under `data/cache/market/`:
  - FRED: `NCBEILQ027S` and `GDP`, refreshed when the cached copy is over a day old;
  - Shiller: `ie_data.xls` from the link on shillerdata.com: the `Data` sheet's monthly date, price, dividend,
    earnings, CPI and CAPE columns, found by their header labels (a missing header fails loudly);
  - Treasury.gov: the yearly CSVs from 1990 (the current year refreshed, past years cached once), one request a
    second.
- **Storage**: `market_series` (series, date, value; PK (series, date)): `fred:NCBEILQ027S`, `fred:GDP`,
  `shiller:price`, `shiller:earnings`, `shiller:cpi`, `shiller:cape`, `ust:1m` ... `ust:30y`; and `market_fetches`
  (source, url, rows, fetched_at). A series is replaced whole when its source is fetched.
- **CLI**: `python -m src.market.collect [--fred] [--shiller] [--treasury] [--db PATH] [--cache-dir DIR]`
  (all three by default), progress and a summary printed; a failed source is reported and the others go on.

## The market views (`src/webapp/market.py`), computed at read time

- **Buffett indicator**: corporate equities / GDP per quarter (`NCBEILQ027S / 1000 / GDP`), as a percentage.
  - The zone, by the ratio's distance from its own long-run trend: fit `ln(ratio)` against time over the whole
    history (least squares), take the residuals' standard deviation, and read the latest quarter's residual:
    below -2 sd "significantly undervalued", -2 to -1 "modestly undervalued", -1 to +1 "fairly valued", +1 to +2
    "modestly overvalued", above +2 "significantly overvalued". The cut-offs are published with the chart.
  - Also the latest ratio, its percentile over the history, and the trend's value today.
  - Its date is the latest quarter both series have (FRED's equities level lags a quarter or two).
- **Shiller CAPE**: the monthly series from 1881; the latest value, its mean, median, min, max and percentile over
  the history; the latest month's date.
- **Yield curve**: the latest day's curve against the curve a year earlier; the 10-year less 2-year and less
  3-month spreads since 1990, with the inverted days marked.
- **Page** `/market` (a nav link): three sections with Plotly charts, each with its source, its latest date and its
  method note. **API** `GET /api/market` returns the same. Without the tables, the page says how to collect.

## Web for the indicators

- **VAL tab**: an "Other indicators" section after the models and owner earnings: per indicator its value (bear,
  base, bull), the price's distance from the base, its method in one line and its N/A reason; the reverse DCF
  recomputed at the live price beside the DCF's growth.
- **API**: `GET /api/stocks/{t}/indicators`.
- **Screener** (NULL without the table): `reverse_dcf_growth`, `epv_upside_pct`, `ps_value_upside_pct`,
  `graham_tangible_upside_pct` (base over the snapshot price - 1) and `price_to_nnwc` (beside M2's
  `price_to_ncav`).

## Validation

- **Indicators** (`qa/oracle/indicators_oracle.py`, no `src` imports): EPV, the median P/S value, the Graham number
  on tangible book, NCAV and the reverse DCF recomputed from the raw tables, each within 0.5% where both are
  applicable, and N/A on the same companies.
- **The valuations untouched**: every `valuations` row, median and verdict identical before and after the backfill.
- **The reader**: every number of the `Data` sheet's CAPE column against CAPE recomputed from the sheet's own
  price, earnings and CPI columns (real price / the 10-year mean of real earnings; the CPI's base cancels),
  within 0.1%, from 1881, misses explained; and Shiller's monthly price (a mean of daily closes) against the
  mean of the app's own `^GSPC` closes for each month both have, within 1%.
- **The market views**: the Buffett ratio recomputed by hand for the latest quarter; the yield curve's 10-year
  rate on the day `market_snapshots.risk_free_rate_date` names against that stored rate.
- **Web**: browser-checked (the VAL tab of AAPL, JPM, O and GEV; `/market`), no console errors.

## Edge cases

| Case | Handling |
|---|---|
| A bank, an insurer, a REIT | EPV N/A for all three and NCAV for banks and insurers, with the reason; the P/S value where its range applies |
| A loss-making company | EPV N/A (adjusted earnings not positive); the Graham numbers N/A; the reverse DCF N/A if the DCF is |
| Negative tangible book (acquisitive companies) | The tangible Graham number N/A, saying so |
| A price no growth in -50% to +100% reaches | Reverse DCF N/A, saying whether the price is above or below that range |
| Shiller's link or sheet layout changes | The collector fails loudly for that source; the stored series stays |
| Shiller's dates are numbers: 2026.1 is October, 2026.01 January | Month = round(100 x the fraction); a test pins October |
| The sheet's last months have a price but no earnings or CAPE yet | Each series stops at its own last value |
| FRED's equities series lags GDP | The ratio's date is the latest quarter both have |

## NEEDS-USER

- **Q14, `xlrd`**: installing the `xlrd` package would read the `.xls` with a standard library instead of the
  milestone's own reader. Default: the own reader, nothing installed.

## Tasks

M4-1 the indicators (reverse DCF, EPV, NCAV/net-net, median P/S value, Graham on tangible book), storage, the engine
hook and the backfill; M4-2 the VAL tab's section, the API and the screener fields, browser-checked; M4-3 the
market data: the reader, the three sources, storage and the CLI; M4-4 the market page and its API,
browser-checked; M4-5 the indicators' independent oracle; M4-6 docs; M4-7 milestone close (code review 18, the
backup, the offline backfill and the market collection into the user's DB).
