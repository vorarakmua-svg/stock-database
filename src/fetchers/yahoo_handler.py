"""Yahoo Finance data handler using yfinance library."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
import yfinance as yf

from .rate_limiter import RateLimiter

# Known yfinance ``earnings_history`` column names — these vary across yfinance
# releases, so candidates are tried defensively rather than assuming one spelling.
_EARNINGS_ESTIMATE_KEYS = ("epsEstimate", "EPS Estimate")
_EARNINGS_ACTUAL_KEYS = ("epsActual", "Reported EPS", "EPS Actual")
_EARNINGS_SURPRISE_KEYS = ("surprisePercent", "Surprise(%)", "Surprise (%)")


class YahooHandler:
    """
    Handler for Yahoo Finance data retrieval using yfinance.

    Provides methods to fetch:
    - Company information (name, sector, industry, description)
    - Market data (price, volume, moving averages)
    - Valuation metrics (PE, PEG, EPS, market cap)
    - Shareholder information (float, insider %, institutional %)
    - Financial statements (income, balance sheet, cash flow)
    """

    def __init__(
        self,
        rate_limit_delay: float = 2.5,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize Yahoo Finance handler.

        Args:
            rate_limit_delay: Minimum seconds between requests
            logger: Optional logger instance
        """
        self.rate_limiter = RateLimiter(
            min_interval=rate_limit_delay,
            name="yahoo"
        )
        self.logger = logger or logging.getLogger(__name__)

    def fetch_all(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch all available data for a ticker.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')

        Returns:
            Dictionary containing all fetched data
        """
        self.logger.info(f"Fetching Yahoo Finance data for {ticker}")

        try:
            self.rate_limiter.wait()
            stock = yf.Ticker(ticker)

            data = {
                "company_info": self._get_company_info(stock),
                "market_data": self._get_market_data(stock),
                "valuation": self._get_valuation_metrics(stock),
                "shareholders": self._get_shareholders(stock),
                "financials": self._get_financials(stock),
                "price_history": self._get_price_history(stock),
                "analyst_estimates": self._get_analyst_estimates(stock),
                "dividend_history": self._get_dividend_history(stock),
                "price_bars": self._get_price_bars(stock),
                "earnings_history": self._get_earnings_history(stock),
                "splits": self._get_splits(stock),
                "fetched_at": datetime.now().isoformat(),
                "source": "yahoo_finance",
            }

            self.logger.info(f"Successfully fetched Yahoo data for {ticker}")
            return data

        except Exception as e:
            self.logger.error(f"Error fetching Yahoo data for {ticker}: {e}")
            return {
                "error": str(e),
                "fetched_at": datetime.now().isoformat(),
                "source": "yahoo_finance",
            }

    def fetch_quote(self, ticker: str) -> Dict[str, Any]:
        """Fetch a lightweight, quote-only snapshot for on-demand refresh (Task 10).

        Makes exactly ONE rate-limited ``yf.Ticker(ticker)`` call and reuses
        the same private extractors ``fetch_all`` uses for market data,
        valuation, and analyst estimates, plus the SCALAR shareholder stats
        only (no holder-list DataFrame calls, no history/bars call).

        Deliberately makes no ``stock.history()`` call, so the returned
        ``market_data`` never includes ``ma_50``/``ma_200`` here (those
        require the "1y" history call ``_get_market_data`` makes for a full
        collection) — ``SQLiteStore.upsert_quote`` carries the previous
        snapshot's values for those forward instead of leaving them ``None``.

        Returns ``{"market_data": ..., "valuation": ..., "shareholders": ...,
        "analyst_estimates": ...}`` on success, or ``{"error": ...}`` (never
        raises) on failure — mirroring ``fetch_all``'s contract so callers
        (e.g. ``CollectionJobManager``'s quote-job worker) can check for the
        ``"error"`` key rather than needing a try/except per ticker.
        """
        self.logger.info(f"Fetching Yahoo quote-only data for {ticker}")
        try:
            self.rate_limiter.wait()
            stock = yf.Ticker(ticker)
            return {
                "market_data": self._get_market_data(stock, include_moving_averages=False),
                "valuation": self._get_valuation_metrics(stock),
                "shareholders": self._get_shareholder_stats(stock),
                "analyst_estimates": self._get_analyst_estimates(stock),
            }
        except Exception as e:
            self.logger.error(f"Error fetching Yahoo quote for {ticker}: {e}")
            return {"error": str(e)}

    def _get_company_info(self, stock: yf.Ticker) -> Dict[str, Any]:
        """Extract company information from yfinance info dict."""
        try:
            info = stock.info
            return {
                "name": info.get("longName") or info.get("shortName"),
                "ticker": info.get("symbol"),
                "exchange": info.get("exchange"),
                "currency": info.get("currency"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "description": info.get("longBusinessSummary"),
                "website": info.get("website"),
                "logo_url": info.get("logo_url"),
                "country": info.get("country"),
                "city": info.get("city"),
                "state": info.get("state"),
                "address": info.get("address1"),
                "zip": info.get("zip"),
                "phone": info.get("phone"),
                "full_time_employees": info.get("fullTimeEmployees"),
                "officers": self._extract_officers(info),
            }
        except Exception as e:
            self.logger.warning(f"Error getting company info: {e}")
            return {}

    def _extract_officers(self, info: Dict) -> List[Dict[str, Any]]:
        """Extract company officers from info dict."""
        officers = info.get("companyOfficers", [])
        return [
            {
                "name": officer.get("name"),
                "title": officer.get("title"),
                "age": officer.get("age"),
                "total_pay": officer.get("totalPay"),
            }
            for officer in officers
        ]

    def _get_market_data(
        self, stock: yf.Ticker, include_moving_averages: bool = True
    ) -> Dict[str, Any]:
        """Extract market data and technical indicators.

        ``include_moving_averages=False`` skips the ``stock.history(period="1y")``
        call entirely — used by ``fetch_quote`` so an on-demand quote refresh
        makes exactly one Yahoo request. In that case ``ma_50``/``ma_200`` are
        simply absent from the returned dict (see ``fetch_quote`` and
        ``SQLiteStore.upsert_quote`` for how the previous snapshot's values are
        carried forward instead of being lost).
        """
        try:
            info = stock.info

            data = {
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "previous_close": info.get("previousClose") or info.get("regularMarketPreviousClose"),
                "open": info.get("open") or info.get("regularMarketOpen"),
                "day_high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
                "day_low": info.get("dayLow") or info.get("regularMarketDayLow"),
                "volume": info.get("volume") or info.get("regularMarketVolume"),
                "avg_volume": info.get("averageVolume"),
                "avg_volume_10d": info.get("averageVolume10days"),
                "market_cap": info.get("marketCap"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "fifty_day_average": info.get("fiftyDayAverage"),
                "two_hundred_day_average": info.get("twoHundredDayAverage"),
                "beta": info.get("beta"),
                "post_market_price": info.get("postMarketPrice"),
                "pre_market_price": info.get("preMarketPrice"),
            }

            # Calculate moving averages from historical data (skipped for a
            # quote-only refresh — see include_moving_averages above).
            if include_moving_averages:
                hist = stock.history(period="1y")
                if not hist.empty and len(hist) > 0:
                    close_prices = hist['Close']
                    if len(close_prices) >= 50:
                        data["ma_50"] = float(close_prices.tail(50).mean())
                    if len(close_prices) >= 200:
                        data["ma_200"] = float(close_prices.tail(200).mean())

            return data
        except Exception as e:
            self.logger.warning(f"Error getting market data: {e}")
            return {}

    def _get_valuation_metrics(self, stock: yf.Ticker) -> Dict[str, Any]:
        """Extract valuation and financial ratios."""
        try:
            info = stock.info
            return {
                # P/E Ratios
                "pe_trailing": info.get("trailingPE"),
                "pe_forward": info.get("forwardPE"),
                "peg_ratio": info.get("pegRatio"),

                # EPS
                "eps_trailing": info.get("trailingEps"),
                "eps_forward": info.get("forwardEps"),

                # Price ratios
                "price_to_book": info.get("priceToBook"),
                "price_to_sales": info.get("priceToSalesTrailing12Months"),

                # Enterprise value
                "enterprise_value": info.get("enterpriseValue"),
                "ev_to_revenue": info.get("enterpriseToRevenue"),
                "ev_to_ebitda": info.get("enterpriseToEbitda"),

                # Profitability
                "profit_margin": info.get("profitMargins"),
                "operating_margin": info.get("operatingMargins"),
                "gross_margin": info.get("grossMargins"),
                "ebitda_margin": info.get("ebitdaMargins"),

                # Returns
                "return_on_assets": info.get("returnOnAssets"),
                "return_on_equity": info.get("returnOnEquity"),

                # Growth
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "earnings_quarterly_growth": info.get("earningsQuarterlyGrowth"),

                # Dividends
                "dividend_rate": info.get("dividendRate"),
                "dividend_yield": info.get("dividendYield"),
                "payout_ratio": info.get("payoutRatio"),
                "ex_dividend_date": self._safe_timestamp(info.get("exDividendDate")),

                # Book value
                "book_value": info.get("bookValue"),

                # Revenue/Income
                "total_revenue": info.get("totalRevenue"),
                "revenue_per_share": info.get("revenuePerShare"),
                "ebitda": info.get("ebitda"),
                "net_income": info.get("netIncomeToCommon"),
                "free_cash_flow": info.get("freeCashflow"),
                "operating_cash_flow": info.get("operatingCashflow"),
                "total_cash": info.get("totalCash"),
                "total_cash_per_share": info.get("totalCashPerShare"),
                "total_debt": info.get("totalDebt"),
                "debt_to_equity": info.get("debtToEquity"),
                "current_ratio": info.get("currentRatio"),
                "quick_ratio": info.get("quickRatio"),
            }
        except Exception as e:
            self.logger.warning(f"Error getting valuation metrics: {e}")
            return {}

    def _get_shareholder_stats(self, stock: yf.Ticker) -> Dict[str, Any]:
        """Scalar shareholder/ownership stats only — no holder-list DataFrames.

        Extracted out of ``_get_shareholders`` so ``fetch_quote`` can reuse
        exactly this piece (the cheap ``stock.info`` fields) without also
        triggering the extra yfinance calls the major/institutional/mutual-fund
        holder lists and insider-transactions require.
        """
        try:
            info = stock.info
            return {
                "shares_outstanding": info.get("sharesOutstanding"),
                "float_shares": info.get("floatShares"),
                "shares_short": info.get("sharesShort"),
                "shares_short_prior_month": info.get("sharesShortPriorMonth"),
                "short_ratio": info.get("shortRatio"),
                "short_percent_of_float": info.get("shortPercentOfFloat"),
                "insider_percent": info.get("heldPercentInsiders"),
                "institutional_percent": info.get("heldPercentInstitutions"),
            }
        except Exception as e:
            self.logger.warning(f"Error getting shareholder stats: {e}")
            return {}

    def _get_shareholders(self, stock: yf.Ticker) -> Dict[str, Any]:
        """Extract shareholder and ownership information."""
        try:
            data = self._get_shareholder_stats(stock)

            # Get major holders if available
            try:
                major_holders = stock.major_holders
                if major_holders is not None and not major_holders.empty:
                    data["major_holders"] = major_holders.to_dict()
            except Exception:
                pass

            # Get institutional holders if available
            try:
                inst_holders = stock.institutional_holders
                if inst_holders is not None and not inst_holders.empty:
                    data["institutional_holders"] = self._df_to_list(inst_holders)
            except Exception:
                pass

            # Get mutual fund holders if available
            try:
                mf_holders = stock.mutualfund_holders
                if mf_holders is not None and not mf_holders.empty:
                    data["mutualfund_holders"] = self._df_to_list(mf_holders)
            except Exception:
                pass

            # Get insider transactions if available
            try:
                insider_transactions = stock.insider_transactions
                if insider_transactions is not None and not insider_transactions.empty:
                    data["insider_transactions"] = self._df_to_list(insider_transactions)
            except Exception:
                pass

            return data
        except Exception as e:
            self.logger.warning(f"Error getting shareholders: {e}")
            return {}

    def _get_financials(self, stock: yf.Ticker) -> Dict[str, Any]:
        """Extract financial statements."""
        try:
            financials = {}

            # Income Statement
            try:
                income_stmt = stock.income_stmt
                if income_stmt is not None and not income_stmt.empty:
                    financials["income_statement_annual"] = self._df_to_dict(income_stmt)

                income_stmt_q = stock.quarterly_income_stmt
                if income_stmt_q is not None and not income_stmt_q.empty:
                    financials["income_statement_quarterly"] = self._df_to_dict(income_stmt_q)
            except Exception as e:
                self.logger.warning(f"Error getting income statement: {e}")

            # Balance Sheet
            try:
                balance_sheet = stock.balance_sheet
                if balance_sheet is not None and not balance_sheet.empty:
                    financials["balance_sheet_annual"] = self._df_to_dict(balance_sheet)

                balance_sheet_q = stock.quarterly_balance_sheet
                if balance_sheet_q is not None and not balance_sheet_q.empty:
                    financials["balance_sheet_quarterly"] = self._df_to_dict(balance_sheet_q)
            except Exception as e:
                self.logger.warning(f"Error getting balance sheet: {e}")

            # Cash Flow
            try:
                cashflow = stock.cashflow
                if cashflow is not None and not cashflow.empty:
                    financials["cash_flow_annual"] = self._df_to_dict(cashflow)

                cashflow_q = stock.quarterly_cashflow
                if cashflow_q is not None and not cashflow_q.empty:
                    financials["cash_flow_quarterly"] = self._df_to_dict(cashflow_q)
            except Exception as e:
                self.logger.warning(f"Error getting cash flow: {e}")

            return financials
        except Exception as e:
            self.logger.warning(f"Error getting financials: {e}")
            return {}

    def _df_to_dict(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Convert DataFrame to dictionary with string keys."""
        result = {}
        for col in df.columns:
            col_key = col.isoformat() if hasattr(col, 'isoformat') else str(col)
            col_data = {}
            for idx, val in df[col].items():
                idx_key = str(idx)
                if pd.isna(val):
                    col_data[idx_key] = None
                elif isinstance(val, (int, float)):
                    col_data[idx_key] = float(val) if isinstance(val, float) else int(val)
                else:
                    col_data[idx_key] = str(val)
            result[col_key] = col_data
        return result

    def _df_to_list(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Convert DataFrame to list of dictionaries."""
        records = df.to_dict(orient='records')
        # Convert any timestamps to strings
        for record in records:
            for key, val in record.items():
                if pd.isna(val):
                    record[key] = None
                elif hasattr(val, 'isoformat'):
                    record[key] = val.isoformat()
                elif isinstance(val, (pd.Timestamp, datetime)):
                    record[key] = str(val)
        return records

    def _safe_timestamp(self, ts) -> Optional[str]:
        """Safely convert timestamp to ISO string."""
        if ts is None:
            return None
        try:
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts).isoformat()
            elif hasattr(ts, 'isoformat'):
                return ts.isoformat()
            return str(ts)
        except Exception:
            return None

    def _get_price_history(
        self,
        stock: yf.Ticker,
        period: str = "10y"
    ) -> Dict[str, Any]:
        """
        Get historical price data for returns and volatility calculations.

        Args:
            stock: yfinance Ticker object
            period: Historical period (1y, 2y, 5y, 10y, max)

        Returns:
            Dictionary with price history summary and statistics
        """
        try:
            hist = stock.history(period=period)

            if hist.empty:
                return {}

            close_prices = hist['Close']
            volume = hist['Volume']

            # Calculate returns
            daily_returns = close_prices.pct_change().dropna()
            annual_returns = close_prices.resample('YE').last().pct_change().dropna()

            # Calculate statistics
            result = {
                "period": period,
                "start_date": hist.index[0].isoformat() if len(hist) > 0 else None,
                "end_date": hist.index[-1].isoformat() if len(hist) > 0 else None,
                "trading_days": len(hist),

                # Price summary
                "latest_close": float(close_prices.iloc[-1]) if len(close_prices) > 0 else None,
                "period_high": float(close_prices.max()),
                "period_low": float(close_prices.min()),
                "period_avg": float(close_prices.mean()),

                # Returns
                "total_return": (
                    float((close_prices.iloc[-1] / close_prices.iloc[0] - 1))
                    if len(close_prices) > 1 else None
                ),
                "cagr": self._calculate_cagr(close_prices),

                # Volatility
                "daily_volatility": float(daily_returns.std()) if len(daily_returns) > 0 else None,
                "annual_volatility": float(daily_returns.std() * (252 ** 0.5)) if len(daily_returns) > 0 else None,

                # Risk metrics
                "max_drawdown": self._calculate_max_drawdown(close_prices),
                "sharpe_ratio_estimate": self._calculate_sharpe_estimate(daily_returns),

                # Volume
                "avg_volume": float(volume.mean()) if len(volume) > 0 else None,
                "volume_volatility": float(volume.std()) if len(volume) > 0 else None,

                # Annual returns (for historical analysis)
                "annual_returns": {
                    str(idx.year): float(ret)
                    for idx, ret in annual_returns.items()
                } if len(annual_returns) > 0 else {},
            }

            return result

        except Exception as e:
            self.logger.warning(f"Error getting price history: {e}")
            return {}

    def _calculate_cagr(self, prices: pd.Series) -> Optional[float]:
        """Calculate Compound Annual Growth Rate."""
        try:
            if len(prices) < 2:
                return None

            start_price = prices.iloc[0]
            end_price = prices.iloc[-1]
            years = len(prices) / 252  # Approximate trading days per year

            if years <= 0 or start_price <= 0:
                return None

            cagr = (end_price / start_price) ** (1 / years) - 1
            return float(cagr)
        except Exception:
            return None

    def _calculate_max_drawdown(self, prices: pd.Series) -> Optional[float]:
        """Calculate maximum drawdown from peak."""
        try:
            if len(prices) < 2:
                return None

            rolling_max = prices.expanding().max()
            drawdowns = prices / rolling_max - 1
            max_drawdown = drawdowns.min()
            return float(max_drawdown)
        except Exception:
            return None

    def _calculate_sharpe_estimate(
        self,
        returns: pd.Series,
        risk_free_rate: float = 0.04
    ) -> Optional[float]:
        """
        Estimate Sharpe ratio (using assumed risk-free rate).

        Note: For accurate Sharpe ratio, use actual risk-free rate from FRED.
        """
        try:
            if len(returns) < 30:  # Need enough data
                return None

            # Annualize
            annual_return = returns.mean() * 252
            annual_vol = returns.std() * (252 ** 0.5)

            if annual_vol == 0:
                return None

            sharpe = (annual_return - risk_free_rate) / annual_vol
            return float(sharpe)
        except Exception:
            return None

    def get_detailed_history(
        self,
        ticker: str,
        period: str = "5y",
        interval: str = "1d"
    ) -> pd.DataFrame:
        """
        Get detailed price history as DataFrame.

        Args:
            ticker: Stock ticker symbol
            period: Historical period
            interval: Data interval (1d, 1wk, 1mo)

        Returns:
            DataFrame with OHLCV data
        """
        try:
            self.rate_limiter.wait()
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period, interval=interval)
            return hist
        except Exception as e:
            self.logger.error(f"Error getting detailed history: {e}")
            return pd.DataFrame()

    def _get_analyst_estimates(self, stock: yf.Ticker) -> Dict[str, Any]:
        """
        Get analyst estimates and recommendations.

        Includes target prices, buy/sell ratings, and earnings estimates.
        Useful for DCF terminal value and sentiment analysis.
        """
        try:
            info = stock.info

            estimates = {
                # Price targets
                "target_price_low": info.get("targetLowPrice"),
                "target_price_mean": info.get("targetMeanPrice"),
                "target_price_median": info.get("targetMedianPrice"),
                "target_price_high": info.get("targetHighPrice"),
                "current_price": info.get("currentPrice"),

                # Analyst ratings
                "recommendation": info.get("recommendationKey"),  # buy, hold, sell
                "recommendation_mean": info.get("recommendationMean"),  # 1=Strong Buy, 5=Sell
                "number_of_analysts": info.get("numberOfAnalystOpinions"),

                # Growth estimates
                "earnings_growth": info.get("earningsGrowth"),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_quarterly_growth": info.get("earningsQuarterlyGrowth"),

                # Forward estimates
                "forward_eps": info.get("forwardEps"),
                "forward_pe": info.get("forwardPE"),

                # Trailing data for comparison
                "trailing_eps": info.get("trailingEps"),
                "trailing_pe": info.get("trailingPE"),
            }

            # Calculate upside/downside potential
            current = estimates.get("current_price")
            target = estimates.get("target_price_mean")
            if current and target:
                estimates["upside_potential"] = (target - current) / current

            # Try to get earnings calendar
            try:
                calendar = stock.calendar
                if calendar is not None and not calendar.empty:
                    if isinstance(calendar, pd.DataFrame):
                        estimates["earnings_date"] = str(calendar.iloc[0, 0]) if len(calendar) > 0 else None
                    elif isinstance(calendar, dict):
                        estimates["earnings_date"] = str(calendar.get("Earnings Date", [None])[0])
            except Exception:
                pass

            # Try to get analyst recommendations history
            try:
                recs = stock.recommendations
                if recs is not None and not recs.empty:
                    recent_recs = recs.tail(10)
                    estimates["recent_recommendations"] = recent_recs.to_dict('records')
            except Exception:
                pass

            return estimates

        except Exception as e:
            self.logger.debug(f"Error getting analyst estimates: {e}")
            return {}

    def _get_dividend_history(self, stock: yf.Ticker) -> Dict[str, Any]:
        """
        Get complete dividend payment history.

        Useful for:
        - Dividend growth rate calculation (Peter Lynch)
        - Dividend consistency (Buffett quality indicator)
        - Dividend yield analysis
        """
        try:
            info = stock.info
            dividends = stock.dividends

            result = {
                # Current dividend info
                "dividend_rate": info.get("dividendRate"),  # Annual dividend per share
                "dividend_yield": info.get("dividendYield"),
                "payout_ratio": info.get("payoutRatio"),
                "ex_dividend_date": self._safe_timestamp(info.get("exDividendDate")),
                "last_dividend_date": info.get("lastDividendDate"),
                "last_dividend_value": info.get("lastDividendValue"),

                # 5-year dividend yield
                "five_year_avg_dividend_yield": info.get("fiveYearAvgDividendYield"),
            }

            # Process dividend history
            if dividends is not None and not dividends.empty:
                # Convert to list of records
                div_records = []
                for date, amount in dividends.items():
                    div_records.append({
                        "date": date.strftime("%Y-%m-%d"),
                        "amount": float(amount)
                    })

                result["dividend_payments"] = div_records
                result["total_payments"] = len(div_records)
                result["years_of_data"] = (
                    (dividends.index[-1] - dividends.index[0]).days / 365.25
                    if len(dividends) > 1 else 0
                )

                # Calculate annual dividends
                annual_dividends = dividends.resample('YE').sum()
                result["annual_dividends"] = {
                    str(date.year): float(amount)
                    for date, amount in annual_dividends.items()
                    if amount > 0
                }

                # Calculate dividend growth rate (CAGR)
                if len(annual_dividends) >= 2:
                    first_year = annual_dividends[annual_dividends > 0].iloc[0]
                    last_year = annual_dividends[annual_dividends > 0].iloc[-1]
                    years = len(annual_dividends) - 1
                    if first_year > 0 and years > 0:
                        cagr = (last_year / first_year) ** (1 / years) - 1
                        result["dividend_cagr"] = float(cagr)

                # Check dividend consistency (years without cut)
                if len(annual_dividends) >= 2:
                    annual_list = annual_dividends[annual_dividends > 0].tolist()
                    increases = sum(
                        1 for i in range(1, len(annual_list))
                        if annual_list[i] >= annual_list[i - 1]
                    )
                    result["dividend_increases"] = increases
                    result["dividend_consistency"] = (
                        increases / (len(annual_list) - 1) if len(annual_list) > 1 else None
                    )

            else:
                result["dividend_payments"] = []
                result["total_payments"] = 0
                result["years_of_data"] = 0
                result["note"] = "No dividend history (non-dividend paying stock)"

            return result

        except Exception as e:
            self.logger.debug(f"Error getting dividend history: {e}")
            return {"error": str(e)}

    def fetch_benchmark_bars(self, symbol: str = "^GSPC") -> List[Dict[str, Any]]:
        """
        Fetch daily OHLCV bars for a benchmark index (default: S&P 500).

        Called once per collection run (not once per ticker) so market-relative
        metrics (beta, relative strength) can be computed against a common index.
        Uses its own rate-limited ``yf.Ticker`` call, independent of ``fetch_all``.

        Returns an empty list on any failure — a benchmark fetch failure must
        never fail the caller's run.
        """
        self.logger.info(f"Fetching benchmark bars for {symbol}")
        try:
            self.rate_limiter.wait()
            stock = yf.Ticker(symbol)
            return self._get_price_bars(stock)
        except Exception as e:
            self.logger.warning(f"Error fetching benchmark bars for {symbol}: {e}")
            return []

    def _get_price_bars(self, stock: yf.Ticker) -> List[Dict[str, Any]]:
        """
        Get the full daily OHLCV price history, ascending by ISO date.

        Returns a list of ``{date, open, high, low, close, volume}`` records,
        or ``[]`` if no history is available.
        """
        try:
            hist = stock.history(period="max", interval="1d")
            if hist is None or hist.empty:
                return []

            hist = hist.sort_index()
            bars = []
            for idx, row in hist.iterrows():
                date_key = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                bars.append({
                    "date": date_key,
                    "open": self._safe_float(row.get("Open")),
                    "high": self._safe_float(row.get("High")),
                    "low": self._safe_float(row.get("Low")),
                    "close": self._safe_float(row.get("Close")),
                    "volume": self._safe_float(row.get("Volume")),
                })
            return bars
        except Exception as e:
            self.logger.warning(f"Error getting price bars: {e}")
            return []

    def _get_earnings_history(self, stock: yf.Ticker) -> List[Dict[str, Any]]:
        """
        Get historical earnings-surprise records (estimate vs. actual EPS).

        yfinance's ``earnings_history`` column names vary across versions, so
        known fields are extracted defensively via candidate column names. If
        the frame's columns match none of the known candidates (an unexpected
        shape), this tolerates-and-logs by returning ``[]`` rather than guessing.
        """
        try:
            df = stock.earnings_history
            if df is None or df.empty:
                return []

            known_columns = set(
                _EARNINGS_ESTIMATE_KEYS + _EARNINGS_ACTUAL_KEYS
                + _EARNINGS_SURPRISE_KEYS + ("quarter",)
            )
            if not (set(df.columns) & known_columns):
                self.logger.warning(
                    "Unrecognized earnings_history column shape; skipping"
                )
                return []

            records = []
            for idx, row in df.iterrows():
                rec = row.to_dict()
                quarter = self._extract_earnings_quarter(idx, rec)
                if quarter is None:
                    continue
                records.append({
                    "quarter": quarter,
                    "eps_estimate": self._first_numeric(rec, _EARNINGS_ESTIMATE_KEYS),
                    "eps_actual": self._first_numeric(rec, _EARNINGS_ACTUAL_KEYS),
                    "surprise_pct": self._first_numeric(rec, _EARNINGS_SURPRISE_KEYS),
                })
            return records
        except Exception as e:
            self.logger.warning(f"Error getting earnings history: {e}")
            return []

    def _get_splits(self, stock: yf.Ticker) -> List[Dict[str, Any]]:
        """Get historical stock split events as ``{date, ratio}`` records, ascending."""
        try:
            splits = stock.splits
            if splits is None or splits.empty:
                return []

            splits = splits.sort_index()
            records = []
            for date, ratio in splits.items():
                date_key = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)
                records.append({"date": date_key, "ratio": float(ratio)})
            return records
        except Exception as e:
            self.logger.warning(f"Error getting splits: {e}")
            return []

    def _extract_earnings_quarter(self, idx: Any, rec: Dict[str, Any]) -> Optional[str]:
        """Get the quarter-end date (ISO string) from a ``quarter`` column or the row index."""
        src = rec.get("quarter")
        if src is None:
            src = idx
        if src is None:
            return None
        if hasattr(src, "strftime"):
            return src.strftime("%Y-%m-%d")
        if hasattr(src, "isoformat"):
            return src.isoformat()
        return str(src)

    @staticmethod
    def _first_numeric(rec: Dict[str, Any], keys: Sequence[str]) -> Optional[float]:
        """Return the first present, non-NaN value among ``keys``, coerced to float."""
        for key in keys:
            if key not in rec:
                continue
            val = rec[key]
            if val is None:
                continue
            try:
                if pd.isna(val):
                    continue
            except (TypeError, ValueError):
                pass
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        """Coerce a pandas cell value to ``float``, tolerating ``None``/``NaN``."""
        if val is None:
            return None
        try:
            if pd.isna(val):
                return None
        except (TypeError, ValueError):
            pass
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
