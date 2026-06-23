"""Main orchestrator for stock data collection."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import AppConfig, default_config
from ..exporters.csv_exporter import CSVExporter
from ..exporters.json_exporter import JSONExporter
from ..exporters.sqlite_store import SQLiteStore
from ..models.canonical import validate_period
from ..models.stock_data import StockData
from ..parsers.calculated_metrics import CalculatedMetrics
from ..parsers.derived_fields import apply_derivations
from ..parsers.xbrl_parser import XBRLParser
from ..validation.quality import assess_annual
from .fred_handler import FREDHandler
from .sec_handler import SECHandler
from .yahoo_handler import YahooHandler


class StockDataFetcher:
    """
    Main orchestrator for fetching stock data from multiple sources.

    Coordinates data collection from Yahoo Finance and SEC EDGAR,
    merges the data, and exports to various formats.
    """

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the stock data fetcher.

        Args:
            config: Application configuration (uses default if not provided)
            logger: Optional logger instance
        """
        self.config = config or default_config
        self.logger = logger or logging.getLogger(__name__)

        # Initialize handlers
        self.yahoo_handler = YahooHandler(
            rate_limit_delay=self.config.yahoo.rate_limit_delay,
            logger=self.logger
        )

        self.sec_handler = SECHandler(
            user_agent=self.config.sec.user_agent,
            rate_limit_delay=self.config.sec.rate_limit_delay,
            cache_dir=self.config.storage.cache_dir,
            logger=self.logger,
            max_retries=self.config.retry.max_retries,
            base_delay=self.config.retry.base_delay,
            max_delay=self.config.retry.max_delay,
        )

        # Initialize parsers
        self.xbrl_parser = XBRLParser(logger=self.logger)
        self.metrics_calculator = CalculatedMetrics(logger=self.logger)

        # Initialize FRED handler for risk-free rate
        self.fred_handler = FREDHandler(
            api_key=os.getenv("FRED_API_KEY"),
            logger=self.logger,
            max_retries=self.config.retry.max_retries,
            base_delay=self.config.retry.base_delay,
            max_delay=self.config.retry.max_delay,
        )

        # Initialize exporters
        self.json_exporter = JSONExporter(
            output_dir=self.config.storage.json_dir,
            logger=self.logger
        )

        self.csv_exporter = CSVExporter(
            output_dir=self.config.storage.csv_dir,
            logger=self.logger
        )

        self.sqlite_store = SQLiteStore(
            db_path=self.config.storage.database_path,
            logger=self.logger
        )

    def fetch_ticker(
        self,
        ticker: str,
        include_yahoo: bool = True,
        include_sec: bool = True,
        years_back: int = 10
    ) -> StockData:
        """
        Fetch all available data for a single ticker.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')
            include_yahoo: Whether to fetch Yahoo Finance data
            include_sec: Whether to fetch SEC EDGAR data
            years_back: Years of historical financial data

        Returns:
            StockData object with merged data from all sources
        """
        ticker = ticker.upper().strip()
        self.logger.info(f"Fetching data for {ticker}")

        stock = StockData(ticker=ticker)

        # Fetch Yahoo Finance data
        if include_yahoo:
            try:
                yahoo_data = self.yahoo_handler.fetch_all(ticker)
                stock.merge_yahoo_data(yahoo_data)
            except Exception as e:
                self.logger.error(f"Yahoo Finance error for {ticker}: {e}")
                stock.add_error(f"Yahoo Finance: {str(e)}")

        # Fetch SEC EDGAR data
        if include_sec:
            try:
                sec_data = self.sec_handler.fetch_all(ticker)
                stock.merge_sec_data(sec_data)

                # Parse XBRL financials if company facts available
                if "company_facts" in sec_data and sec_data["company_facts"]:
                    facts = sec_data["company_facts"]

                    annual = self.xbrl_parser.extract_annual_financials(
                        facts, years_back=years_back
                    )
                    quarterly = self.xbrl_parser.extract_quarterly_financials(
                        facts, quarters_back=years_back * 4
                    )

                    stock.merge_parsed_financials(annual, quarterly)

                # Get insider transactions
                if stock.cik:
                    transactions = self.sec_handler.get_insider_transactions(
                        stock.cik, limit=50
                    )
                    stock.merge_insider_transactions(transactions)

            except Exception as e:
                self.logger.error(f"SEC EDGAR error for {ticker}: {e}")
                stock.add_error(f"SEC EDGAR: {str(e)}")

        # Fetch risk-free rate from FRED (for WACC calculation)
        try:
            risk_free = self.fred_handler.get_risk_free_rate(maturity="10y")
            market_premium = self.fred_handler.get_market_risk_premium()

            rate_data = {
                "risk_free_rate": risk_free,
                "risk_free_rate_10y": risk_free,
                "market_risk_premium": market_premium.get("current_estimate"),
                "source": "fred" if self.fred_handler.api_key else "fallback",
            }
            stock.merge_risk_free_rate(rate_data)
        except Exception as e:
            self.logger.warning(f"FRED data error for {ticker}: {e}")
            stock.add_warning(f"FRED: {str(e)}")

        # Validate/coerce standardized financials and assess data quality.
        if stock.financials_annual or stock.financials_quarterly:
            try:
                self._validate_and_score(stock)
            except Exception as e:
                self.logger.warning(f"Data-quality validation error for {ticker}: {e}")
                stock.add_warning(f"Data quality: {str(e)}")

        # Calculate derived metrics (FCF, EBITDA, ROIC, etc.)
        if stock.financials_annual:
            try:
                # Get the most recent year's financials
                years = sorted(stock.financials_annual.keys(), reverse=True)
                if years:
                    latest_financials = stock.financials_annual[years[0]]

                    # Calculate metrics
                    metrics = self.metrics_calculator.calculate_all(
                        financials=latest_financials,
                        market_data=stock.market_data,
                        valuation=stock.valuation
                    )

                    # Add historical metrics for all years
                    metrics["historical"] = self.metrics_calculator.calculate_historical(
                        stock.financials_annual
                    )

                    stock.merge_calculated_metrics(metrics)

            except Exception as e:
                self.logger.warning(f"Metrics calculation error for {ticker}: {e}")
                stock.add_warning(f"Calculated metrics: {str(e)}")

        self.logger.info(
            f"Completed {ticker}: sources={stock.data_sources}, "
            f"warnings={len(stock.warnings)}, errors={len(stock.errors)}"
        )

        return stock

    def _validate_and_score(self, stock: StockData) -> None:
        """Derive identities, validate/coerce periods, attach a data-quality report."""
        # Derive missing fields (e.g. total_liabilities = assets - equity), then
        # validate + coerce each period; surface validation errors as warnings.
        for attr in ("financials_annual", "financials_quarterly"):
            periods = getattr(stock, attr)
            if not periods:
                continue
            cleaned = {}
            for period_key, period in periods.items():
                apply_derivations(period)
                clean, errors = validate_period(period)
                cleaned[period_key] = clean
                for err in errors:
                    stock.add_warning(f"validation {attr} {period_key}: {err}")
            setattr(stock, attr, cleaned)

        # Score annual financials (sector-aware) and record findings.
        report = assess_annual(stock.financials_annual, sector=stock.sector_class)
        stock.data_quality = report.as_dict()
        for message in report.warning_messages():
            stock.add_warning(message)

    def fetch_multiple(
        self,
        tickers: List[str],
        include_yahoo: bool = True,
        include_sec: bool = True,
        years_back: int = 10
    ) -> List[StockData]:
        """
        Fetch data for multiple tickers.

        Args:
            tickers: List of ticker symbols
            include_yahoo: Whether to fetch Yahoo Finance data
            include_sec: Whether to fetch SEC EDGAR data
            years_back: Years of historical financial data

        Returns:
            List of StockData objects
        """
        workers = max(1, getattr(self.config, "max_workers", 1))
        self.logger.info(
            f"Fetching data for {len(tickers)} tickers "
            f"({'sequential' if workers == 1 or len(tickers) <= 1 else f'{workers} workers'})"
        )

        if workers == 1 or len(tickers) <= 1:
            results = [
                self._safe_fetch_ticker(t, include_yahoo, include_sec, years_back)
                for t in tickers
            ]
            self.logger.info(f"Completed fetching {len(results)} tickers")
            return results

        # Parallel fetch. Per-source RateLimiters are thread-safe and serialize each
        # external API, so we overlap work across tickers without exceeding limits.
        # Results are placed back in input order.
        ordered: List[Optional[StockData]] = [None] * len(tickers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_idx = {
                executor.submit(
                    self._safe_fetch_ticker, ticker, include_yahoo, include_sec, years_back
                ): idx
                for idx, ticker in enumerate(tickers)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                ordered[idx] = future.result()

        self.logger.info(f"Completed fetching {len(ordered)} tickers")
        return [r for r in ordered if r is not None]

    def _safe_fetch_ticker(
        self,
        ticker: str,
        include_yahoo: bool,
        include_sec: bool,
        years_back: int,
    ) -> StockData:
        """Fetch one ticker, converting any failure into an error StockData."""
        try:
            return self.fetch_ticker(
                ticker,
                include_yahoo=include_yahoo,
                include_sec=include_sec,
                years_back=years_back,
            )
        except Exception as e:
            self.logger.error(f"Failed to fetch {ticker}: {e}")
            error_stock = StockData(ticker=ticker)
            error_stock.add_error(f"Fetch failed: {str(e)}")
            return error_stock

    def export(
        self,
        data: List[StockData],
        formats: Optional[List[str]] = None
    ) -> Dict[str, List[Path]]:
        """
        Export stock data to files.

        Args:
            data: List of StockData objects
            formats: List of formats to export ('json', 'csv')
                    Defaults to config setting

        Returns:
            Dictionary mapping format to list of created file paths
        """
        formats = formats or self.config.output_formats
        results = {}

        if "json" in formats:
            json_paths = self.json_exporter.export(data)
            results["json"] = json_paths

        if "csv" in formats:
            csv_path = self.csv_exporter.export(data, filename="summary.csv")
            if csv_path:
                results["csv"] = [csv_path]

            # Also export financial history
            history_path = self.csv_exporter.export_financial_history(
                data, filename="financial_history.csv"
            )
            if history_path:
                results["csv"].append(history_path)

        if "sqlite" in formats:
            db_path = self.sqlite_store.export(data)
            if db_path:
                results["sqlite"] = [db_path]

        return results

    def fetch_and_export(
        self,
        tickers: List[str],
        include_yahoo: bool = True,
        include_sec: bool = True,
        years_back: int = 10,
        formats: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Convenience method to fetch and export in one call.

        Args:
            tickers: List of ticker symbols
            include_yahoo: Whether to fetch Yahoo Finance data
            include_sec: Whether to fetch SEC EDGAR data
            years_back: Years of historical financial data
            formats: Export formats

        Returns:
            Dictionary with results summary
        """
        start_time = datetime.now()

        # Fetch data
        data = self.fetch_multiple(
            tickers,
            include_yahoo=include_yahoo,
            include_sec=include_sec,
            years_back=years_back
        )

        # Export
        export_paths = self.export(data, formats=formats)

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        # Build summary
        summary = {
            "tickers_requested": len(tickers),
            "tickers_fetched": len(data),
            "successful": sum(1 for s in data if not s.errors),
            "with_warnings": sum(1 for s in data if s.warnings),
            "with_errors": sum(1 for s in data if s.errors),
            "export_paths": export_paths,
            "duration_seconds": duration,
            "started_at": start_time.isoformat(),
            "completed_at": end_time.isoformat(),
        }

        self.logger.info(
            f"Fetch complete: {summary['successful']}/{summary['tickers_requested']} successful "
            f"in {duration:.1f}s"
        )

        return summary

    def close(self) -> None:
        """Close handlers and release resources."""
        self.sec_handler.close()
        self.fred_handler.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
