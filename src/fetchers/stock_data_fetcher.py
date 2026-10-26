"""Main orchestrator for stock data collection."""

import json
import logging
import os
import sqlite3
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..config import DEFAULT_BATCH_SIZE, AppConfig, default_config
from ..exporters.csv_exporter import CSVExporter
from ..exporters.json_exporter import JSONExporter
from ..exporters.sqlite_store import SQLiteStore, StoreResult, read_only_uri
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
#: How it begins for a record that stops before the latest report the SEC's submissions list, which its companyfacts
#: lack (BUG-251).
REPORT_WITHOUT_FACTS = "SEC EDGAR: the companyfacts lack"
#: The tickers a collection's worker has submitted at most: one fetching and one waiting its turn, so the workers go
#: on while the calling thread exports a batch, and the finished companies it hasn't taken yet stay few.
IN_FLIGHT_PER_WORKER = 2
#: How long a collection waits for a fetch to finish before it waits again: a timed wait, so Ctrl+C is seen within
#: this even where a thread's untimed wait doesn't wake for it (Windows).
POLL_SECONDS = 0.5


class CollectionInterrupted(KeyboardInterrupt):
    """Ctrl+C stopped a collection (``StockDataFetcher.fetch_and_export``). *summary* is its summary so far: the
    batches it exported and stored. The tickers not started were cancelled, and the companies fetched since the last
    batch, like the few still fetching, weren't exported."""

    def __init__(self, summary: Dict[str, Any]) -> None:
        super().__init__(f"collection interrupted after storing {summary['stored']} companies")
        self.summary = summary


class _Tally:
    """A collection's counts as it goes: the companies fetched and how, the batches exported and what the database
    stored, so nothing of a batch need be kept once it is exported."""

    def __init__(self, requested: int, batch_size: int) -> None:
        self.requested = requested
        self.batch_size = batch_size
        self.fetched = self.successful = self.with_warnings = self.with_errors = 0
        self.batches = self.exported = self.stored = 0
        self.not_stored: Dict[str, str] = {}
        self.sqlite_errors: List[str] = []
        self.export_paths: Dict[str, List[Path]] = {}
        self.benchmark_done = False
        #: The stored companies whose batch's valuations haven't finished.
        self.unvalued: List[str] = []

    def fetched_one(self, stock: StockData) -> None:
        self.fetched += 1
        self.successful += 0 if stock.errors else 1
        self.with_warnings += 1 if stock.warnings else 0
        self.with_errors += 1 if stock.errors else 0

    def exported_batch(self, count: int, paths: Dict[str, List[Path]], stored: Optional[StoreResult]) -> None:
        self.batches += 1
        self.exported += count
        for kind, written in paths.items():
            known = self.export_paths.setdefault(kind, [])
            known.extend(path for path in written if path not in known)
        if stored is not None:
            self.stored += len(stored.stored)
            self.not_stored.update(stored.failed)
            if stored.error is not None:
                self.sqlite_errors.append(f"batch {self.batches}: {stored.error}")

    def summary(self, start: datetime, interrupted: bool) -> Dict[str, Any]:
        end = datetime.now()
        return {
            "tickers_requested": self.requested,
            "tickers_fetched": self.fetched,
            "successful": self.successful,
            "with_warnings": self.with_warnings,
            "with_errors": self.with_errors,
            "batch_size": self.batch_size,
            "batches": self.batches,
            "exported": self.exported,
            "stored": self.stored,
            "not_stored": dict(self.not_stored),
            "sqlite_errors": list(self.sqlite_errors),
            "interrupted": interrupted,
            "unvalued": list(self.unvalued),
            "export_paths": {kind: list(paths) for kind, paths in self.export_paths.items()},
            "duration_seconds": (end - start).total_seconds(),
            "started_at": start.isoformat(),
            "completed_at": end.isoformat(),
        }


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
        # Each quarter's split basis, for the trailing years; not a figure to store (BUG-082).
        split_levels: Dict[str, Dict[str, float]] = {}

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
                    split_levels = {end: period.pop("_split_levels") for end, period in quarterly.items()
                                    if "_split_levels" in period}
                    vintages = self.xbrl_parser.extract_annual_vintages(
                        facts, years_back=years_back
                    )
                    stock.financials_annual_vintages = vintages

                    stock.merge_parsed_financials(annual, quarterly)

                    # Capture material facts under tags we don't yet map, so
                    # taxonomy evolution / firm tag variability never silently
                    # drops data.
                    stock.unmapped_facts = detect_unmapped(facts)

                # A successor read with its predecessor's filings says so (sec_handler.PREDECESSOR_CIKS).
                if sec_data.get("predecessor_error"):
                    stock.add_warning(f"SEC EDGAR: {ticker}'s predecessor, CIK {sec_data['predecessor_cik']}, "
                                      f"couldn't be read ({sec_data['predecessor_error']}), so its history is "
                                      f"missing from this run")
                elif sec_data.get("predecessor_cik"):
                    name = (sec_data.get("submissions") or {}).get("name") or "name unknown"
                    stock.add_warning(f"SEC EDGAR: {ticker} reads CIK {sec_data['cik']} ({name}) with its "
                                      f"predecessor's filings, CIK {sec_data['predecessor_cik']} "
                                      f"({sec_data.get('predecessor_name') or 'name unknown'})")

                # The latest report the submissions list, which the companyfacts lack: the SEC's lag (BUG-251).
                # A companyfacts request that failed has its error.
                missing = sec_data.get("missing_report")
                if missing and "company_facts_error" not in sec_data:
                    stock.add_warning(f"{REPORT_WITHOUT_FACTS} {ticker}'s {missing['form']} filed {missing['filed']} "
                                      f"({missing['accession']}), so this run's figures stop before it; the refresh "
                                      f"collects {ticker} again")

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
                self._clean_and_derive(stock, split_levels)
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

    def _clean_and_derive(self, stock: StockData,
                          split_levels: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        """Derive identities, validate/coerce each period, and build the TTM series
        (*split_levels*: each quarter's split basis, the parser's ``_split_levels``)."""
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
            stock.financials_ttm = compute_ttm(stock.financials_quarterly, split_levels)

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
            List of StockData objects, in the order of *tickers*
        """
        workers = max(1, getattr(self.config, "max_workers", 1))
        self.logger.info(
            f"Fetching data for {len(tickers)} tickers "
            f"({'sequential' if workers == 1 or len(tickers) <= 1 else f'{workers} workers'})"
        )
        # Results are placed back in input order.
        ordered: List[Optional[StockData]] = [None] * len(tickers)
        for idx, stock in self._fetch_each(tickers, include_yahoo, include_sec, years_back):
            ordered[idx] = stock
        self.logger.info(f"Completed fetching {len(ordered)} tickers")
        return [r for r in ordered if r is not None]

    def _fetch_each(
        self,
        tickers: List[str],
        include_yahoo: bool,
        include_sec: bool,
        years_back: Optional[int],
    ) -> Iterator[Tuple[int, StockData]]:
        """Each ticker's StockData as its fetch finishes, with the ticker's place in *tickers*.

        With one worker (or one ticker), one after another on this thread. Else on ``max_workers`` threads, each
        with IN_FLIGHT_PER_WORKER tickers submitted at most, the rest waiting here: the workers go on while the caller
        exports what it took, and the finished ones it hasn't taken yet stay few. Per-source RateLimiters are
        thread-safe and serialize each external API, so we overlap work across tickers without exceeding limits.
        Closing the iterator, or Ctrl+C while it waits, cancels the tickers not started; the few running finish in
        their threads, and nothing reads them.
        """
        workers = max(1, getattr(self.config, "max_workers", 1))
        if workers == 1 or len(tickers) <= 1:
            for idx, ticker in enumerate(tickers):
                yield idx, self._safe_fetch_ticker(ticker, include_yahoo, include_sec, years_back)
            return
        queue = iter(enumerate(tickers))
        executor = ThreadPoolExecutor(max_workers=workers)
        pending: Dict[Future, int] = {}
        finished = False

        def submit(count: int) -> None:
            for idx, ticker in islice(queue, count):
                pending[executor.submit(self._safe_fetch_ticker, ticker, include_yahoo, include_sec, years_back)] = idx

        try:
            submit(IN_FLIGHT_PER_WORKER * workers)
            while pending:
                done = wait(pending, timeout=POLL_SECONDS, return_when=FIRST_COMPLETED).done
                while done:
                    future = done.pop()
                    idx = pending.pop(future)
                    stock = future.result()
                    del future  # a finished fetch is the caller's alone
                    submit(1)
                    yield idx, stock
                    del stock
            finished = True
        finally:
            # Done, nothing is left to wait for. Else (Ctrl+C, the caller stopped), the tickers not started are
            # cancelled, and the running ones aren't waited for.
            executor.shutdown(wait=finished, cancel_futures=not finished)

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
            formats: List of formats to export ('json', 'csv', 'sqlite')
                    Defaults to config setting

        Returns:
            Dictionary mapping format to list of created file paths: the database's when it stored any of *data*
            (each stock in its own savepoint; one it didn't take carries the error, "SQLite write failed: ...")
        """
        return self._export(data, formats)[0]

    def _export(
        self,
        data: List[StockData],
        formats: Optional[List[str]] = None
    ) -> Tuple[Dict[str, List[Path]], Optional[StoreResult]]:
        """``export``'s paths, and what the SQLite store did with *data* (None without the sqlite format)."""
        formats = formats or self.config.output_formats
        results: Dict[str, List[Path]] = {}

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

        stored: Optional[StoreResult] = None
        if "sqlite" in formats:
            stored = self.sqlite_store.write_batch(data)
            if stored.stored:
                results["sqlite"] = [stored.path]

        return results, stored

    def fetch_and_export(
        self,
        tickers: List[str],
        include_yahoo: bool = True,
        include_sec: bool = True,
        years_back: Optional[int] = None,
        formats: Optional[List[str]] = None,
        batch_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Fetch *tickers* and export them as they arrive, a batch at a time (code review 19, I-1).

        The workers fetch without a pause between batches (``_fetch_each``). Each time *batch_size* companies have
        arrived, and with the rest at the end, the batch is exported on this thread (JSON, the CSVs merged with the
        files', SQLite with each stock in its own savepoint), then the companies it stored are valued and every
        stored one ranked (``valuation_engine.compute_and_store``): a run stopped part way leaves what it stored
        valued and ranked, and the last batch's pass gives the run's ranks. The benchmark's bars (^GSPC) are fetched
        once a run, after the first batch is stored. Nothing of a batch is kept once it is exported: at most
        *batch_size* companies plus IN_FLIGHT_PER_WORKER times the workers' are in memory at once.

        Ctrl+C stops it at once: the tickers not started are cancelled, the companies fetched since the last batch
        (like the few still fetching) aren't exported, and CollectionInterrupted, a KeyboardInterrupt, carries the
        summary so far.

        Args:
            tickers: List of ticker symbols
            include_yahoo: Whether to fetch Yahoo Finance data
            include_sec: Whether to fetch SEC EDGAR data
            years_back: Years of historical financial data
            formats: Export formats
            batch_size: Companies exported at a time, 1 or more (default: the config's, DEFAULT_BATCH_SIZE)

        Returns:
            Dictionary with results summary: the tickers requested and fetched, those without errors, with warnings
            and with errors; the batch size, the batches exported and the companies in them; ``stored``, the companies
            the database took, and ``not_stored``, each one exported that it didn't with why (none without the sqlite
            format); ``sqlite_errors``, each batch whose whole export failed and why; ``interrupted``; ``unvalued``,
            the stored companies whose batch's valuations Ctrl+C cut short; the export paths; the duration and times
        """
        start_time = datetime.now()
        resolved_formats = formats or self.config.output_formats
        size = batch_size if batch_size is not None else getattr(self.config, "batch_size", DEFAULT_BATCH_SIZE)
        if size < 1:
            raise ValueError(f"the batch size must be 1 or more, not {size}")
        tally = _Tally(len(tickers), size)
        self.logger.info(f"Collecting {len(tickers)} tickers: exported, stored and valued {size} at a time")
        fetched = self._fetch_each(tickers, include_yahoo, include_sec, years_back)
        batch: List[Tuple[int, StockData]] = []
        try:
            for item in fetched:
                tally.fetched_one(item[1])
                batch.append(item)
                del item
                if len(batch) >= size:
                    self._export_batch(batch, tally, resolved_formats, include_yahoo)
                    batch = []
            if batch:
                self._export_batch(batch, tally, resolved_formats, include_yahoo)
                batch = []
        except KeyboardInterrupt:
            summary = tally.summary(start_time, interrupted=True)
            self.logger.info(f"Collection stopped by Ctrl+C: {summary['stored']} of {len(tickers)} companies stored; "
                             f"{summary['tickers_fetched'] - summary['exported']} fetched since the last batch, left "
                             "out")
            raise CollectionInterrupted(summary) from None
        finally:
            fetched.close()  # the tickers not started are cancelled (nothing to do once every one was taken)

        summary = tally.summary(start_time, interrupted=False)
        self.logger.info(
            f"Fetch complete: {summary['successful']}/{summary['tickers_requested']} successful "
            f"in {summary['duration_seconds']:.1f}s"
            + (f"; {summary['stored']} stored in the database" if "sqlite" in resolved_formats else "")
        )

        return summary

    def _export_batch(
        self,
        batch: List[Tuple[int, StockData]],
        tally: _Tally,
        formats: List[str],
        include_yahoo: bool,
    ) -> None:
        """Export a batch in its tickers' order (``_export``), fetch the benchmark's bars after the run's first, and
        value the companies it stored. A benchmark or valuation failure never fails the run."""
        number = tally.batches + 1
        stocks = [stock for _, stock in sorted(batch, key=lambda item: item[0])]
        paths, result = self._export(stocks, formats)
        tally.exported_batch(len(stocks), paths, result)
        stored = list(result.stored) if result is not None else []
        tally.unvalued = stored
        self.logger.info(f"Batch {number}: {len(stocks)} exported"
                         + (f", {len(stored)} stored" if result is not None else "")
                         + f" ({tally.exported} of {tally.requested} so far)")

        # Benchmark (^GSPC) bars: collected once per run, not once per ticker, so
        # market-relative metrics (beta, relative strength) have a common index.
        # A benchmark failure must never fail the run.
        if include_yahoo and "sqlite" in formats and not tally.benchmark_done:
            tally.benchmark_done = True
            try:
                benchmark_bars = self.yahoo_handler.fetch_benchmark_bars()
                self.sqlite_store.export_benchmark_bars("^GSPC", benchmark_bars)
            except Exception as e:
                self.logger.warning(f"Benchmark bars fetch/export failed: {e}")

        # Valuations: recompute for the companies just stored so the stored
        # fair-value ranges always reflect the newest fundamentals; each pass
        # ranks every stored company. Import is local + lazy, and a valuation
        # failure must never fail the run.
        if stored:
            try:
                from ..valuation import engine as valuation_engine
                valuation_engine.compute_and_store(
                    self.sqlite_store.db_path,
                    tickers=list(stored),
                    logger=self.logger,
                )
            except Exception as e:
                self.logger.warning(f"Valuation computation failed for batch {number} ({', '.join(stored)}): {e}")
        tally.unvalued = []

    def close(self) -> None:
        """Close handlers and release resources."""
        self.sec_handler.close()
        self.fred_handler.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
