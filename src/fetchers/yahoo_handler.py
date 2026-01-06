"""Yahoo Finance data handler using yfinance library."""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

import yfinance as yf
import pandas as pd

from .rate_limiter import RateLimiter


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

    def _get_market_data(self, stock: yf.Ticker) -> Dict[str, Any]:
        """Extract market data and technical indicators."""
        try:
            info = stock.info

            # Get historical data for technical indicators
            hist = stock.history(period="1y")

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
            }

            # Calculate moving averages from historical data
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

    def _get_shareholders(self, stock: yf.Ticker) -> Dict[str, Any]:
        """Extract shareholder and ownership information."""
        try:
            info = stock.info

            data = {
                "shares_outstanding": info.get("sharesOutstanding"),
                "float_shares": info.get("floatShares"),
                "shares_short": info.get("sharesShort"),
                "shares_short_prior_month": info.get("sharesShortPriorMonth"),
                "short_ratio": info.get("shortRatio"),
                "short_percent_of_float": info.get("shortPercentOfFloat"),
                "insider_percent": info.get("heldPercentInsiders"),
                "institutional_percent": info.get("heldPercentInstitutions"),
            }

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
                self.logger.debug(f"Error getting income statement: {e}")

            # Balance Sheet
            try:
                balance_sheet = stock.balance_sheet
                if balance_sheet is not None and not balance_sheet.empty:
                    financials["balance_sheet_annual"] = self._df_to_dict(balance_sheet)

                balance_sheet_q = stock.quarterly_balance_sheet
                if balance_sheet_q is not None and not balance_sheet_q.empty:
                    financials["balance_sheet_quarterly"] = self._df_to_dict(balance_sheet_q)
            except Exception as e:
                self.logger.debug(f"Error getting balance sheet: {e}")

            # Cash Flow
            try:
                cashflow = stock.cashflow
                if cashflow is not None and not cashflow.empty:
                    financials["cash_flow_annual"] = self._df_to_dict(cashflow)

                cashflow_q = stock.quarterly_cashflow
                if cashflow_q is not None and not cashflow_q.empty:
                    financials["cash_flow_quarterly"] = self._df_to_dict(cashflow_q)
            except Exception as e:
                self.logger.debug(f"Error getting cash flow: {e}")

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
