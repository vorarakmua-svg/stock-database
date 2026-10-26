"""Data models for stock data."""

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

#: The StockData section each ``to_summary`` column comes from. A CSV merge keeps a column's
#: earlier value only when a run got nothing for its section (a failed or partial fetch), the
#: way the JSON merge keeps a whole section; a section the run did get replaces it, empty
#: values included. Company details are the exception: like the JSON's company_info and
#: SQLite's companies row, they keep each earlier value the run lacked.
SUMMARY_SECTIONS: Dict[str, Tuple[str, ...]] = {
    "company_info": ("sector", "industry", "country", "employees", "website"),
    "market_data": ("current_price", "market_cap", "volume", "beta", "52_week_high", "52_week_low",
                    "ma_50", "ma_200"),
    "valuation": ("pe_trailing", "pe_forward", "peg_ratio", "eps_trailing", "eps_forward", "price_to_book",
                  "dividend_yield", "profit_margin", "operating_margin", "return_on_equity", "return_on_assets",
                  "revenue_growth", "total_revenue", "ebitda", "net_income", "total_cash", "total_debt",
                  "debt_to_equity", "free_cash_flow"),
    "shareholders": ("shares_outstanding", "float_shares", "insider_percent", "institutional_percent",
                     "short_ratio"),
    "financials_annual": ("sec_revenue", "sec_net_income", "sec_total_assets", "sec_total_liabilities",
                          "sec_stockholders_equity", "sec_operating_cash_flow", "sec_fiscal_year"),
    "price_history": ("cagr_5y", "annual_volatility", "max_drawdown", "sharpe_ratio", "total_return_5y"),
    "calculated_metrics": ("calc_ebitda", "calc_fcf", "calc_roic", "calc_net_debt", "calc_interest_coverage",
                           "calc_ev", "calc_ev_to_ebitda"),
    "risk_free_rate": ("risk_free_rate_10y", "treasury_yield_source"),
}
#: Identifiers a merge keeps from an earlier run when a run lacks them.
SUMMARY_IDENTITY_COLUMNS: Tuple[str, ...] = ("ticker", "cik", "company_name")
#: The run's own columns: always the latest run's.
SUMMARY_RUN_COLUMNS: Tuple[str, ...] = ("collected_at", "data_sources", "warning_count", "error_count")


@dataclass
class StockData:
    """
    Unified stock data model combining data from all sources.

    This model standardizes data from Yahoo Finance and SEC EDGAR
    into a single, consistent structure.
    """

    # Core identifiers
    ticker: str
    cik: Optional[str] = None
    company_name: Optional[str] = None
    # Sector class derived from SIC (bank/insurance/reit/utility/energy/general)
    sector_class: Optional[str] = None

    # Collection metadata
    collected_at: datetime = field(default_factory=datetime.now)

    # Company information (primarily from Yahoo)
    company_info: Dict[str, Any] = field(default_factory=dict)

    # Market data (from Yahoo)
    market_data: Dict[str, Any] = field(default_factory=dict)

    # Valuation metrics (from Yahoo)
    valuation: Dict[str, Any] = field(default_factory=dict)

    # Shareholder information (from Yahoo)
    shareholders: Dict[str, Any] = field(default_factory=dict)

    # Financial statements from Yahoo (as backup)
    yahoo_financials: Dict[str, Any] = field(default_factory=dict)

    # Financial statements from SEC EDGAR (preferred source)
    financials_annual: Dict[str, Any] = field(default_factory=dict)
    financials_quarterly: Dict[str, Any] = field(default_factory=dict)
    # Trailing-twelve-month series derived from discrete quarters
    financials_ttm: Dict[str, Any] = field(default_factory=dict)

    # Point-in-time vintages: {fiscal_year: {accn: period}}. SQLite-only (excluded
    # from to_dict/JSON); see the vintaged-ingestion spec.
    financials_annual_vintages: Dict[str, Any] = field(default_factory=dict)

    # SEC submission data
    sec_submissions: Dict[str, Any] = field(default_factory=dict)

    # Why a successor's predecessor's company facts couldn't be read (sec_handler.PREDECESSOR_CIKS),
    # None when they were or there is none: the run's record then lacks every figure only the
    # predecessor filed, so the SQLite store only fills from it (BUG-179). SQLite-only, like the
    # vintages (excluded from to_dict/JSON); the run's warning says so.
    predecessor_error: Optional[str] = None

    # Insider transactions (from SEC Form 4)
    insider_transactions: List[Dict[str, Any]] = field(default_factory=list)

    # Material us-gaap facts whose tag isn't yet in the canonical registry
    # (taxonomy evolution / firm tag variability) — captured so nothing is lost.
    unmapped_facts: List[Dict[str, Any]] = field(default_factory=list)

    # Historical price data and statistics (from Yahoo)
    price_history: Dict[str, Any] = field(default_factory=dict)

    # Analyst estimates and recommendations (from Yahoo)
    analyst_estimates: Dict[str, Any] = field(default_factory=dict)

    # Dividend payment history (from Yahoo)
    dividend_history: Dict[str, Any] = field(default_factory=dict)

    # Daily OHLCV price bars: {date, open, high, low, close, volume} (from Yahoo)
    price_bars: List[Dict[str, Any]] = field(default_factory=list)

    # Earnings-surprise history: {quarter, eps_estimate, eps_actual, surprise_pct} (from Yahoo)
    earnings_history: List[Dict[str, Any]] = field(default_factory=list)

    # Stock split events: {date, ratio} (from Yahoo)
    splits: List[Dict[str, Any]] = field(default_factory=list)

    # Calculated metrics for DCF modeling (FCF, EBITDA, ROIC, etc.)
    calculated_metrics: Dict[str, Any] = field(default_factory=dict)

    # Risk-free rate and market data for WACC calculation (from FRED)
    risk_free_rate: Dict[str, Any] = field(default_factory=dict)

    # Data-quality assessment of standardized financials (score + findings)
    data_quality: Dict[str, Any] = field(default_factory=dict)

    # Data quality metadata
    data_sources: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with JSON-serializable values."""
        data = asdict(self)
        # Convert datetime to ISO string
        if isinstance(data["collected_at"], datetime):
            data["collected_at"] = data["collected_at"].isoformat()
        # Vintages and the predecessor's error are SQLite-only — never written to the per-ticker JSON.
        data.pop("financials_annual_vintages", None)
        data.pop("predecessor_error", None)
        return data

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StockData":
        """Create StockData from dictionary.

        Tolerates extra keys not declared on the model. The JSON exporter's
        merge mode adds bookkeeping fields (``collection_history``,
        ``first_collected_at``, ``price_history_snapshots``) that are not
        dataclass fields, so we filter down to known fields before constructing.
        """
        # Only keep keys that correspond to declared dataclass fields
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}

        # Convert ISO string back to datetime
        if isinstance(data.get("collected_at"), str):
            data["collected_at"] = datetime.fromisoformat(data["collected_at"])
        return cls(**data)

    def to_summary(self) -> Dict[str, Any]:
        """
        Generate a summary with key metrics for CSV export.

        Returns:
            Dictionary with flattened key metrics
        """
        summary = {
            "ticker": self.ticker,
            "cik": self.cik,
            "company_name": self.company_name,
            "collected_at": (
                self.collected_at.isoformat()
                if isinstance(self.collected_at, datetime)
                else self.collected_at
            ),
        }

        # Every key is always present (None when its section is empty), so the CSV
        # columns don't depend on which tickers a run happened to collect.

        # Company info
        info = self.company_info or {}
        summary["sector"] = info.get("sector")
        summary["industry"] = info.get("industry")
        summary["country"] = info.get("country")
        summary["employees"] = info.get("full_time_employees")
        summary["website"] = info.get("website")

        # Market data
        market = self.market_data or {}
        summary["current_price"] = market.get("current_price")
        summary["market_cap"] = market.get("market_cap")
        summary["volume"] = market.get("volume")
        summary["beta"] = market.get("beta")
        summary["52_week_high"] = market.get("fifty_two_week_high")
        summary["52_week_low"] = market.get("fifty_two_week_low")
        summary["ma_50"] = market.get("fifty_day_average")
        summary["ma_200"] = market.get("two_hundred_day_average")

        # Valuation metrics
        valuation = self.valuation or {}
        summary["pe_trailing"] = valuation.get("pe_trailing")
        summary["pe_forward"] = valuation.get("pe_forward")
        summary["peg_ratio"] = valuation.get("peg_ratio")
        summary["eps_trailing"] = valuation.get("eps_trailing")
        summary["eps_forward"] = valuation.get("eps_forward")
        summary["price_to_book"] = valuation.get("price_to_book")
        summary["dividend_yield"] = valuation.get("dividend_yield")
        summary["profit_margin"] = valuation.get("profit_margin")
        summary["operating_margin"] = valuation.get("operating_margin")
        summary["return_on_equity"] = valuation.get("return_on_equity")
        summary["return_on_assets"] = valuation.get("return_on_assets")
        summary["revenue_growth"] = valuation.get("revenue_growth")
        summary["total_revenue"] = valuation.get("total_revenue")
        summary["ebitda"] = valuation.get("ebitda")
        summary["net_income"] = valuation.get("net_income")
        summary["total_cash"] = valuation.get("total_cash")
        summary["total_debt"] = valuation.get("total_debt")
        summary["debt_to_equity"] = valuation.get("debt_to_equity")
        summary["free_cash_flow"] = valuation.get("free_cash_flow")

        # Shareholders
        holders = self.shareholders or {}
        summary["shares_outstanding"] = holders.get("shares_outstanding")
        summary["float_shares"] = holders.get("float_shares")
        summary["insider_percent"] = holders.get("insider_percent")
        summary["institutional_percent"] = holders.get("institutional_percent")
        summary["short_ratio"] = holders.get("short_ratio")

        # Latest SEC financials (the most recent year, if any)
        annual = self.financials_annual or {}
        latest = annual[max(annual)] if annual else {}
        summary["sec_revenue"] = latest.get("revenue")
        summary["sec_net_income"] = latest.get("net_income")
        summary["sec_total_assets"] = latest.get("total_assets")
        summary["sec_total_liabilities"] = latest.get("total_liabilities")
        summary["sec_stockholders_equity"] = latest.get("total_equity")
        summary["sec_operating_cash_flow"] = latest.get("operating_cash_flow")
        summary["sec_fiscal_year"] = latest.get("fiscal_year")

        # Price history statistics
        prices = self.price_history or {}
        summary["cagr_5y"] = prices.get("cagr")
        summary["annual_volatility"] = prices.get("annual_volatility")
        summary["max_drawdown"] = prices.get("max_drawdown")
        summary["sharpe_ratio"] = prices.get("sharpe_ratio_estimate")
        summary["total_return_5y"] = prices.get("total_return")

        # Calculated metrics for DCF
        metrics = self.calculated_metrics or {}
        summary["calc_ebitda"] = metrics.get("ebitda")
        summary["calc_fcf"] = metrics.get("free_cash_flow")
        summary["calc_roic"] = metrics.get("roic")
        summary["calc_net_debt"] = metrics.get("net_debt")
        summary["calc_interest_coverage"] = metrics.get("interest_coverage")
        summary["calc_ev"] = metrics.get("enterprise_value")
        summary["calc_ev_to_ebitda"] = metrics.get("ev_to_ebitda")

        # Risk-free rate for WACC
        rates = self.risk_free_rate or {}
        summary["risk_free_rate_10y"] = rates.get("risk_free_rate")
        summary["treasury_yield_source"] = rates.get("source")

        # Data quality
        summary["data_sources"] = ", ".join(self.data_sources)
        summary["warning_count"] = len(self.warnings)
        summary["error_count"] = len(self.errors)

        return summary

    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)

    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)

    def add_source(self, source: str) -> None:
        """Record a data source."""
        if source not in self.data_sources:
            self.data_sources.append(source)

    def merge_yahoo_data(self, yahoo_data: Dict[str, Any]) -> None:
        """
        Merge data from Yahoo Finance.

        Args:
            yahoo_data: Data dictionary from YahooHandler.fetch_all()
        """
        if "error" in yahoo_data:
            self.add_error(f"Yahoo Finance: {yahoo_data['error']}")
            return

        self.add_source("yahoo_finance")
        for warning in yahoo_data.get("warnings", []):
            self.add_warning(f"Yahoo Finance: {warning}")

        # Company info
        if "company_info" in yahoo_data:
            self.company_info = yahoo_data["company_info"]
            if not self.company_name:
                self.company_name = self.company_info.get("name")

        # Market data
        if "market_data" in yahoo_data:
            self.market_data = yahoo_data["market_data"]

        # Valuation
        if "valuation" in yahoo_data:
            self.valuation = yahoo_data["valuation"]

        # Shareholders
        if "shareholders" in yahoo_data:
            self.shareholders = yahoo_data["shareholders"]

        # Financials (as backup)
        if "financials" in yahoo_data:
            self.yahoo_financials = yahoo_data["financials"]

        # Price history with statistics
        if "price_history" in yahoo_data:
            self.price_history = yahoo_data["price_history"]

        # Analyst estimates and recommendations
        if "analyst_estimates" in yahoo_data:
            self.analyst_estimates = yahoo_data["analyst_estimates"]

        # Dividend payment history
        if "dividend_history" in yahoo_data:
            self.dividend_history = yahoo_data["dividend_history"]

        # Daily OHLCV price bars
        if "price_bars" in yahoo_data:
            self.price_bars = yahoo_data["price_bars"]

        # Earnings-surprise history
        if "earnings_history" in yahoo_data:
            self.earnings_history = yahoo_data["earnings_history"]

        # Stock split events
        if "splits" in yahoo_data:
            self.splits = yahoo_data["splits"]

    def merge_sec_data(self, sec_data: Dict[str, Any]) -> None:
        """
        Merge data from SEC EDGAR.

        Args:
            sec_data: Data dictionary from SECHandler.fetch_all()
        """
        if "error" in sec_data:
            self.add_error(f"SEC EDGAR: {sec_data['error']}")
            return

        self.add_source("sec_edgar")

        # A failed request (403, 5xx, timeout) leaves its part empty: an error, not a quiet gap.
        for key, endpoint in (("company_facts_error", "companyfacts"), ("submissions_error", "submissions")):
            if sec_data.get(key):
                self.add_error(f"SEC EDGAR {endpoint}: {sec_data[key]}")
        # A successor's facts read without its predecessor's (a 503, a 404: "no XBRL data") lack the
        # predecessor's figures: a warning, as the fetcher words it, and the store only fills (BUG-179).
        self.predecessor_error = sec_data.get("predecessor_error")

        # CIK
        if "cik" in sec_data:
            self.cik = sec_data["cik"]

        # Submissions
        if "submissions" in sec_data:
            self.sec_submissions = sec_data["submissions"]
            # Update company name from SEC if not set
            if not self.company_name and sec_data["submissions"]:
                self.company_name = sec_data["submissions"].get("name")
            # Classify sector from SIC for sector-aware standardization
            from ..mappings.sectors import classify_submissions
            self.sector_class = classify_submissions(sec_data["submissions"])

    def merge_parsed_financials(
        self,
        annual: Dict[str, Any],
        quarterly: Dict[str, Any]
    ) -> None:
        """
        Merge parsed SEC financial data.

        Args:
            annual: Annual financials from XBRLParser
            quarterly: Quarterly financials from XBRLParser
        """
        if annual:
            self.financials_annual = annual
        if quarterly:
            self.financials_quarterly = quarterly

    def merge_insider_transactions(self, transactions: List[Dict[str, Any]]) -> None:
        """
        Merge insider transaction data.

        Args:
            transactions: List of insider transactions
        """
        self.insider_transactions = transactions

    def merge_calculated_metrics(self, metrics: Dict[str, Any]) -> None:
        """
        Merge calculated financial metrics.

        Args:
            metrics: Dictionary of calculated metrics (FCF, EBITDA, ROIC, etc.)
        """
        if metrics:
            self.calculated_metrics = metrics
            self.add_source("calculated_metrics")

    def merge_risk_free_rate(self, rate_data: Dict[str, Any]) -> None:
        """
        Merge risk-free rate data from FRED or fallback source.

        Args:
            rate_data: Dictionary with risk-free rate and source info
        """
        if rate_data:
            self.risk_free_rate = rate_data
            source = rate_data.get("source", "unknown")
            if source != "unknown":
                self.add_source(f"fred_{source}")
