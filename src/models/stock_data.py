"""Data models for stock data."""

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, List, Optional


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

    # SEC submission data
    sec_submissions: Dict[str, Any] = field(default_factory=dict)

    # Insider transactions (from SEC Form 4)
    insider_transactions: List[Dict[str, Any]] = field(default_factory=list)

    # Historical price data and statistics (from Yahoo)
    price_history: Dict[str, Any] = field(default_factory=dict)

    # Analyst estimates and recommendations (from Yahoo)
    analyst_estimates: Dict[str, Any] = field(default_factory=dict)

    # Dividend payment history (from Yahoo)
    dividend_history: Dict[str, Any] = field(default_factory=dict)

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

        # Company info
        if self.company_info:
            summary["sector"] = self.company_info.get("sector")
            summary["industry"] = self.company_info.get("industry")
            summary["country"] = self.company_info.get("country")
            summary["employees"] = self.company_info.get("full_time_employees")
            summary["website"] = self.company_info.get("website")

        # Market data
        if self.market_data:
            summary["current_price"] = self.market_data.get("current_price")
            summary["market_cap"] = self.market_data.get("market_cap")
            summary["volume"] = self.market_data.get("volume")
            summary["beta"] = self.market_data.get("beta")
            summary["52_week_high"] = self.market_data.get("fifty_two_week_high")
            summary["52_week_low"] = self.market_data.get("fifty_two_week_low")
            summary["ma_50"] = self.market_data.get("fifty_day_average")
            summary["ma_200"] = self.market_data.get("two_hundred_day_average")

        # Valuation metrics
        if self.valuation:
            summary["pe_trailing"] = self.valuation.get("pe_trailing")
            summary["pe_forward"] = self.valuation.get("pe_forward")
            summary["peg_ratio"] = self.valuation.get("peg_ratio")
            summary["eps_trailing"] = self.valuation.get("eps_trailing")
            summary["eps_forward"] = self.valuation.get("eps_forward")
            summary["price_to_book"] = self.valuation.get("price_to_book")
            summary["dividend_yield"] = self.valuation.get("dividend_yield")
            summary["profit_margin"] = self.valuation.get("profit_margin")
            summary["operating_margin"] = self.valuation.get("operating_margin")
            summary["return_on_equity"] = self.valuation.get("return_on_equity")
            summary["return_on_assets"] = self.valuation.get("return_on_assets")
            summary["revenue_growth"] = self.valuation.get("revenue_growth")
            summary["total_revenue"] = self.valuation.get("total_revenue")
            summary["ebitda"] = self.valuation.get("ebitda")
            summary["net_income"] = self.valuation.get("net_income")
            summary["total_cash"] = self.valuation.get("total_cash")
            summary["total_debt"] = self.valuation.get("total_debt")
            summary["debt_to_equity"] = self.valuation.get("debt_to_equity")
            summary["free_cash_flow"] = self.valuation.get("free_cash_flow")

        # Shareholders
        if self.shareholders:
            summary["shares_outstanding"] = self.shareholders.get("shares_outstanding")
            summary["float_shares"] = self.shareholders.get("float_shares")
            summary["insider_percent"] = self.shareholders.get("insider_percent")
            summary["institutional_percent"] = self.shareholders.get("institutional_percent")
            summary["short_ratio"] = self.shareholders.get("short_ratio")

        # Latest SEC financials (if available)
        if self.financials_annual:
            # Get the most recent year
            years = sorted(self.financials_annual.keys(), reverse=True)
            if years:
                latest = self.financials_annual[years[0]]
                summary["sec_revenue"] = latest.get("revenue")
                summary["sec_net_income"] = latest.get("net_income")
                summary["sec_total_assets"] = latest.get("total_assets")
                summary["sec_total_liabilities"] = latest.get("total_liabilities")
                summary["sec_stockholders_equity"] = latest.get("total_equity")
                summary["sec_operating_cash_flow"] = latest.get("operating_cash_flow")
                summary["sec_fiscal_year"] = latest.get("fiscal_year")

        # Price history statistics
        if self.price_history:
            summary["cagr_5y"] = self.price_history.get("cagr")
            summary["annual_volatility"] = self.price_history.get("annual_volatility")
            summary["max_drawdown"] = self.price_history.get("max_drawdown")
            summary["sharpe_ratio"] = self.price_history.get("sharpe_ratio_estimate")
            summary["total_return_5y"] = self.price_history.get("total_return")

        # Calculated metrics for DCF
        if self.calculated_metrics:
            summary["calc_ebitda"] = self.calculated_metrics.get("ebitda")
            summary["calc_fcf"] = self.calculated_metrics.get("free_cash_flow")
            summary["calc_roic"] = self.calculated_metrics.get("roic")
            summary["calc_net_debt"] = self.calculated_metrics.get("net_debt")
            summary["calc_interest_coverage"] = self.calculated_metrics.get("interest_coverage")
            summary["calc_ev"] = self.calculated_metrics.get("enterprise_value")
            summary["calc_ev_to_ebitda"] = self.calculated_metrics.get("ev_to_ebitda")

        # Risk-free rate for WACC
        if self.risk_free_rate:
            summary["risk_free_rate_10y"] = self.risk_free_rate.get("risk_free_rate")
            summary["treasury_yield_source"] = self.risk_free_rate.get("source")

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
