"""Main orchestrator for stock data collection."""

import json
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import AppConfig, default_config
from ..exporters.csv_exporter import CSVExporter
from ..exporters.json_exporter import JSONExporter
from ..exporters.sqlite_store import SQLiteStore, read_only_uri
from ..models.canonical import validate_period
from ..models.stock_data import StockData
from ..parsers.calculated_metrics import CalculatedMetrics
from ..parsers.derived_fields import apply_derivations
from ..parsers.share_scale import normalize_share_scale
from ..parsers.ttm import compute_ttm
from ..parsers.unmapped import detect_unmapped
from ..parsers.xbrl_parser import XBRLParser, explain_no_annual_financials, explain_stale_annual_financials
from ..validation.integrity import (
    check_cashflow_reconciliation,
    check_dividend_completeness,
    check_field_outliers,
    check_quarterly_sums,
    check_ratio_bounds,
)
from ..validation.quality import assess_annual, score_for
from .fred_handler import FREDHandler
from .sec_handler import SECHandler
from .yahoo_handler import YahooHandler

#: How the run's warning for a filer whose SEC data gave no annual financials begins.
NO_ANNUAL_FINANCIALS = "SEC EDGAR: no annual financials"
#: How it begins for a record that stops where the filer's IFRS reports begin.
STALE_ANNUAL_FINANCIALS = "SEC EDGAR: annual financials stop"


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
        years_back: Optional[int] = None
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
                annual: Dict[str, Any] = {}
                if "company_facts" in sec_data and sec_data["company_facts"]:
                    facts = sec_data["company_facts"]

                    annual = self.xbrl_parser.extract_annual_financials(
                        facts, years_back=years_back
                    )
                    # A later filing's tagging error the parser put right (BUG-061).
                    for fy, period in annual.items():
                        for r in period.pop("_repairs", []):
                            stock.add_warning(
                                f"SEC EDGAR: {ticker} FY{fy} {r['field']} is {r['kept']:.4g}, as earlier "
                                f"filings report; the filing of {r['filed']} reports {r['dropped']:.4g}, "
                                f"which looks like a tagging error ({r['kind']})")
                    quarterly = self.xbrl_parser.extract_quarterly_financials(
                        facts, quarters_back=(years_back * 4 if years_back else None)
                    )
                    vintages = self.xbrl_parser.extract_annual_vintages(
                        facts, years_back=years_back
                    )
                    stock.financials_annual_vintages = vintages

                    stock.merge_parsed_financials(annual, quarterly)

                    # Capture material facts under tags we don't yet map, so
                    # taxonomy evolution / firm tag variability never silently
                    # drops data.
                    stock.unmapped_facts = detect_unmapped(facts)

                # An empty record otherwise reads like a short history: say why.
                if sec_data.get("cik") and not annual:
                    stock.add_warning(self._no_annual_financials(ticker, sec_data))
                elif annual:
                    # And one that stops where IFRS reports begin reads as current (BUG-066).
                    latest = max(annual, key=int)
                    stale = explain_stale_annual_financials(sec_data.get("company_facts"),
                                                            annual[latest].get("period_end"))
                    if stale:
                        stock.add_warning(f"{STALE_ANNUAL_FINANCIALS} at FY{latest} for {ticker}: {stale}")

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
            rate = self.fred_handler.get_risk_free_rate_details(maturity="10y") or {}
            risk_free = rate.get("rate")
            market_premium = self.fred_handler.get_market_risk_premium()

            # Where the rate came from (FRED, Treasury.gov or the static estimate) and the
            # day it is for travel with it, so the valuations can show a stale or estimated rate.
            rate_data = {
                "risk_free_rate": risk_free,
                "risk_free_rate_10y": risk_free,
                "market_risk_premium": market_premium.get("current_estimate"),
                "source": rate.get("source") or ("fred" if self.fred_handler.api_key else "fallback"),
                "date": rate.get("date"),
            }
            stock.merge_risk_free_rate(rate_data)
        except Exception as e:
            self.logger.warning(f"FRED data error for {ticker}: {e}")
            stock.add_warning(f"FRED: {str(e)}")

        # The sector comes from SEC's submissions data. When that request failed, the
        # stored record still knows it; metrics computed as a general company's would
        # overwrite every stored year's sector ratios (FFO, net interest margin).
        if stock.sector_class is None and stock.financials_annual:
            stored = self._stored_sector(ticker)
            if stored:
                stock.sector_class = stored
                stock.add_warning(f"SEC EDGAR: sector '{stored}' taken from the stored record "
                                  "(the submissions data is unavailable)")

        # Clean, derive identities, and build the TTM series.
        if stock.financials_annual or stock.financials_quarterly:
            try:
                self._clean_and_derive(stock)
            except Exception as e:
                self.logger.warning(f"Data cleaning error for {ticker}: {e}")
                stock.add_warning(f"Data cleaning: {str(e)}")

        # Calculate derived metrics (generic + sector-aware).
        if stock.financials_annual:
            try:
                self._compute_metrics(stock)
            except Exception as e:
                self.logger.warning(f"Metrics calculation error for {ticker}: {e}")
                stock.add_warning(f"Calculated metrics: {str(e)}")

        # Assess data quality (sector + integrity checks), now that metrics exist.
        if stock.financials_annual or stock.financials_quarterly:
            try:
                self._assess(stock)
            except Exception as e:
                self.logger.warning(f"Data-quality assessment error for {ticker}: {e}")
                stock.add_warning(f"Data quality: {str(e)}")

        self.logger.info(
            f"Completed {ticker}: sources={stock.data_sources}, "
            f"warnings={len(stock.warnings)}, errors={len(stock.errors)}"
        )

        return stock

    def _stored_sector(self, ticker: str) -> Optional[str]:
        """The sector already stored for *ticker*: the database's, else the JSON export's (read-only)."""
        path = Path(self.sqlite_store.db_path)
        if path.exists():
            try:
                conn = sqlite3.connect(read_only_uri(path), uri=True)
                try:
                    row = conn.execute("SELECT sector_class FROM companies WHERE ticker = ?", (ticker,)).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error:
                row = None
            if row and row[0]:
                return row[0]
        try:
            stored = json.loads((Path(self.json_exporter.output_dir) / f"{ticker.upper()}.json")
                                .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return stored.get("sector_class") or None if isinstance(stored, dict) else None

    def _compute_metrics(self, stock: StockData) -> None:
        """Populate generic + sector-aware derived metrics on the stock.

        Uses the company's ``sector_class`` so banks/insurers/REITs get their
        own ratios and the generic ratios that don't apply are stored as None.
        """
        years = sorted((stock.financials_annual or {}).keys(), reverse=True)
        if not years:
            return
        latest_financials = stock.financials_annual[years[0]]
        metrics = self.metrics_calculator.calculate_all(
            financials=latest_financials,
            market_data=stock.market_data,
            valuation=stock.valuation,
            sector=stock.sector_class,
        )
        metrics["historical"] = self.metrics_calculator.calculate_historical(
            stock.financials_annual,
            sector=stock.sector_class,
        )
        stock.merge_calculated_metrics(metrics)

    def _clean_and_derive(self, stock: StockData) -> None:
        """Derive identities, validate/coerce each period, and build the TTM series."""
        for attr in ("financials_annual", "financials_quarterly"):
            periods = getattr(stock, attr)
            if not periods:
                continue
            # Fix share counts filed in thousands/millions BEFORE anything reads
            # them — derivations, validation and every per-share metric downstream
            # all assume units. Cross-period, so a year with no EPS to anchor on
            # still inherits the scale its siblings agree on.
            for period_key, fields in normalize_share_scale(periods).items():
                stock.add_warning(
                    f"share scale {attr} {period_key}: rescaled {', '.join(fields)} "
                    "(filed in thousands/millions)"
                )
            cleaned = {}
            for period_key, period in periods.items():
                apply_derivations(period)
                clean, errors = validate_period(period)
                cleaned[period_key] = clean
                for err in errors:
                    stock.add_warning(f"validation {attr} {period_key}: {err}")
            setattr(stock, attr, cleaned)

        # Derive identities within each point-in-time vintage (self-contained snapshots).
        for by_accn in (stock.financials_annual_vintages or {}).values():
            normalize_share_scale(by_accn)
            for period in by_accn.values():
                apply_derivations(period)

        if stock.financials_quarterly:
            stock.financials_ttm = compute_ttm(stock.financials_quarterly)

    @staticmethod
    def _no_annual_financials(ticker: str, sec_data: Dict[str, Any]) -> str:
        """The run's warning for a filer whose SEC data gave no annual financials."""
        submissions = sec_data.get("submissions") or {}
        filings = submissions.get("filings") or []
        dates = sorted(f["filingDate"] for f in filings if f.get("filingDate"))
        # SECHandler keeps the latest 100 filings: their first date is the filer's
        # first only when there are fewer than that.
        first = dates[0] if dates and len(filings) < 100 else None
        if sec_data.get("company_facts_error"):
            reason = f"its companyfacts request failed ({sec_data['company_facts_error']})"
        else:
            reason = explain_no_annual_financials(sec_data.get("company_facts"), first)
        name = submissions.get("name") or "name unknown"
        return f"{NO_ANNUAL_FINANCIALS} for {ticker} from CIK {sec_data['cik']} ({name}): {reason}"

    def _assess(self, stock: StockData) -> None:
        """Attach a data-quality report: sector checks + integrity checks + score.

        Runs after metrics are computed so the ratio-bounds check can see them.
        """
        annual = stock.financials_annual or {}
        quarterly = stock.financials_quarterly or {}
        historical = (stock.calculated_metrics or {}).get("historical", {})
        scored_years = set(sorted(annual.keys(), reverse=True)[:5])

        report = assess_annual(annual, sector=stock.sector_class)
        if annual:
            # Integrity checks all key off annual data; with no annual financials
            # keep assess_annual's score (0) rather than letting a lone
            # no_financials finding net out to 75.
            report.findings.extend(check_field_outliers(annual, scored_years))
            report.findings.extend(check_cashflow_reconciliation(annual, scored_years))
            report.findings.extend(check_quarterly_sums(annual, quarterly, scored_years))
            report.findings.extend(check_ratio_bounds(historical, annual, scored_years))
            report.findings.extend(check_dividend_completeness(
                annual, (stock.dividend_history or {}).get("dividend_payments") or [],
                stock.splits or [], scored_years))
            report.score = score_for(report.findings)

        stock.data_quality = report.as_dict()
        stock.data_quality["unmapped_tag_count"] = len(stock.unmapped_facts)
        for message in report.warning_messages():
            stock.add_warning(message)

    def fetch_multiple(
        self,
        tickers: List[str],
        include_yahoo: bool = True,
        include_sec: bool = True,
        years_back: Optional[int] = None
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
        years_back: Optional[int],
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

            # Also export financial history. The summary can fail on its own (Windows
            # won't replace a file Excel has open), and that must not stop the rest.
            history_path = self.csv_exporter.export_financial_history(
                data, filename="financial_history.csv"
            )
            if history_path:
                results.setdefault("csv", []).append(history_path)

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
        years_back: Optional[int] = None,
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

        # Benchmark (^GSPC) bars: collected once per run, not once per ticker, so
        # market-relative metrics (beta, relative strength) have a common index.
        # A benchmark failure must never fail the run.
        resolved_formats = formats or self.config.output_formats
        if include_yahoo and "sqlite" in resolved_formats:
            try:
                benchmark_bars = self.yahoo_handler.fetch_benchmark_bars()
                self.sqlite_store.export_benchmark_bars("^GSPC", benchmark_bars)
            except Exception as e:
                self.logger.warning(f"Benchmark bars fetch/export failed: {e}")

        # Valuations: recompute for the just-collected tickers so the stored
        # fair-value ranges always reflect the newest fundamentals. Import is
        # local + lazy, and a valuation failure must never fail the run.
        if "sqlite" in resolved_formats and data:
            try:
                from ..valuation import engine as valuation_engine
                valuation_engine.compute_and_store(
                    self.sqlite_store.db_path,
                    tickers=[s.ticker for s in data],
                    logger=self.logger,
                )
            except Exception as e:
                self.logger.warning(f"Valuation computation failed: {e}")

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
