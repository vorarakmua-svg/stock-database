"""SEC EDGAR API handler for financial reports and filings."""

import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path

import requests

from .rate_limiter import RateLimiter, RetryHandler


class SECHandler:
    """
    Handler for SEC EDGAR API data retrieval.

    IMPORTANT: SEC requires a specific User-Agent header format:
    "CompanyName email@domain.com"

    Provides methods to fetch:
    - Company facts (XBRL data)
    - Filing submissions
    - Ticker to CIK mapping
    - Form 4 insider transactions
    """

    SEC_BASE_URL = "https://data.sec.gov"
    SEC_WWW_URL = "https://www.sec.gov"

    def __init__(
        self,
        user_agent: str = "StockDataCollector user@example.com",
        rate_limit_delay: float = 0.12,
        cache_dir: Optional[Path] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize SEC EDGAR handler.

        Args:
            user_agent: Required User-Agent header (format: "Company email@domain.com")
            rate_limit_delay: Minimum seconds between requests (SEC allows 10/sec)
            cache_dir: Optional directory for caching CIK mapping
            logger: Optional logger instance
        """
        self.user_agent = user_agent
        self.cache_dir = cache_dir or Path("data/cache")
        self.logger = logger or logging.getLogger(__name__)

        self.rate_limiter = RateLimiter(
            min_interval=rate_limit_delay,
            name="sec"
        )

        self.retry_handler = RetryHandler(
            max_retries=3,
            base_delay=1.0,
            logger=self.logger
        )

        # Create session for connection pooling
        self.session = requests.Session()
        self.session.headers.update(self._get_headers())

        # CIK mapping cache
        self._cik_mapping: Optional[Dict[str, str]] = None

    def _get_headers(self) -> Dict[str, str]:
        """Get required headers for SEC API requests."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

    def fetch_all(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch all available SEC data for a ticker.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')

        Returns:
            Dictionary containing all fetched SEC data
        """
        self.logger.info(f"Fetching SEC EDGAR data for {ticker}")

        # Get CIK for ticker
        cik = self.ticker_to_cik(ticker)
        if not cik:
            self.logger.warning(f"Could not find CIK for ticker {ticker}")
            return {
                "error": f"CIK not found for ticker {ticker}",
                "fetched_at": datetime.now().isoformat(),
                "source": "sec_edgar",
            }

        data = {
            "cik": cik,
            "ticker": ticker.upper(),
            "fetched_at": datetime.now().isoformat(),
            "source": "sec_edgar",
        }

        # Fetch company facts (XBRL data)
        try:
            facts = self.get_company_facts(cik)
            if facts:
                data["company_facts"] = facts
        except Exception as e:
            self.logger.error(f"Error fetching company facts: {e}")
            data["company_facts_error"] = str(e)

        # Fetch submissions (filing history)
        try:
            submissions = self.get_submissions(cik)
            if submissions:
                data["submissions"] = submissions
        except Exception as e:
            self.logger.error(f"Error fetching submissions: {e}")
            data["submissions_error"] = str(e)

        self.logger.info(f"Successfully fetched SEC data for {ticker}")
        return data

    def ticker_to_cik(self, ticker: str) -> Optional[str]:
        """
        Convert ticker symbol to 10-digit CIK number.

        Args:
            ticker: Stock ticker symbol

        Returns:
            10-digit zero-padded CIK string, or None if not found
        """
        ticker = ticker.upper()

        # Load mapping if not cached
        if self._cik_mapping is None:
            self._load_cik_mapping()

        cik = self._cik_mapping.get(ticker)
        if cik:
            return str(cik).zfill(10)
        return None

    def _load_cik_mapping(self) -> None:
        """Load ticker to CIK mapping from SEC."""
        # Try to load from cache first
        cache_file = self.cache_dir / "company_tickers.json"

        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                self._cik_mapping = self._parse_tickers_json(data)
                self.logger.info("Loaded CIK mapping from cache")
                return
            except Exception as e:
                self.logger.warning(f"Error loading cached CIK mapping: {e}")

        # Fetch from SEC
        url = f"{self.SEC_WWW_URL}/files/company_tickers.json"
        self.logger.info("Fetching CIK mapping from SEC...")

        try:
            self.rate_limiter.wait()
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            data = response.json()
            self._cik_mapping = self._parse_tickers_json(data)

            # Cache the mapping
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w') as f:
                json.dump(data, f)

            self.logger.info(f"Loaded {len(self._cik_mapping)} ticker mappings")

        except Exception as e:
            self.logger.error(f"Error fetching CIK mapping: {e}")
            self._cik_mapping = {}

    def _parse_tickers_json(self, data: Dict) -> Dict[str, str]:
        """Parse SEC company_tickers.json format."""
        mapping = {}
        for entry in data.values():
            ticker = entry.get("ticker")
            cik = entry.get("cik_str")
            if ticker and cik:
                mapping[ticker.upper()] = str(cik)
        return mapping

    def get_company_facts(self, cik: str) -> Optional[Dict[str, Any]]:
        """
        Get all XBRL facts for a company.

        This endpoint returns all historical financial data filed in XBRL format.

        Args:
            cik: 10-digit CIK number

        Returns:
            Dictionary containing company facts, or None on error
        """
        cik = str(cik).zfill(10)
        url = f"{self.SEC_BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"

        self.logger.debug(f"Fetching company facts from {url}")

        try:
            self.rate_limiter.wait()
            response = self.session.get(url, timeout=60)

            if response.status_code == 404:
                self.logger.warning(f"No XBRL data found for CIK {cik}")
                return None

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching company facts: {e}")
            return None

    def get_submissions(self, cik: str) -> Optional[Dict[str, Any]]:
        """
        Get filing submission history for a company.

        Args:
            cik: 10-digit CIK number

        Returns:
            Dictionary containing submissions data, or None on error
        """
        cik = str(cik).zfill(10)
        url = f"{self.SEC_BASE_URL}/submissions/CIK{cik}.json"

        self.logger.debug(f"Fetching submissions from {url}")

        try:
            self.rate_limiter.wait()
            response = self.session.get(url, timeout=30)

            if response.status_code == 404:
                self.logger.warning(f"No submissions found for CIK {cik}")
                return None

            response.raise_for_status()
            data = response.json()

            # Extract relevant fields
            return {
                "entity_type": data.get("entityType"),
                "sic": data.get("sic"),
                "sic_description": data.get("sicDescription"),
                "name": data.get("name"),
                "tickers": data.get("tickers", []),
                "exchanges": data.get("exchanges", []),
                "ein": data.get("ein"),
                "description": data.get("description"),
                "category": data.get("category"),
                "fiscal_year_end": data.get("fiscalYearEnd"),
                "state_of_incorporation": data.get("stateOfIncorporation"),
                "state_of_incorporation_description": data.get("stateOfIncorporationDescription"),
                "addresses": data.get("addresses", {}),
                "phone": data.get("phone"),
                "filings": self._extract_recent_filings(data.get("filings", {})),
            }

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching submissions: {e}")
            return None

    def _extract_recent_filings(
        self,
        filings: Dict[str, Any],
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Extract recent filings from submissions data."""
        recent = filings.get("recent", {})
        if not recent:
            return []

        # Get all field names
        keys = list(recent.keys())
        if not keys:
            return []

        # Get number of filings
        n_filings = len(recent.get("accessionNumber", []))
        n_filings = min(n_filings, limit)

        # Build list of filings
        result = []
        for i in range(n_filings):
            filing = {}
            for key in keys:
                values = recent.get(key, [])
                if i < len(values):
                    filing[key] = values[i]
            result.append(filing)

        return result

    def get_insider_transactions(
        self,
        cik: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get insider transactions (Form 4) for a company.

        Note: This extracts Form 4 filings from submissions.
        For detailed transaction parsing, additional XML parsing would be needed.

        Args:
            cik: 10-digit CIK number
            limit: Maximum number of transactions to return

        Returns:
            List of Form 4 filing information
        """
        submissions = self.get_submissions(cik)
        if not submissions:
            return []

        filings = submissions.get("filings", [])

        # Filter for Form 4 filings
        form4_filings = [
            f for f in filings
            if f.get("form") in ["4", "4/A"]
        ][:limit]

        return form4_filings

    def extract_financial_metrics(
        self,
        facts: Dict[str, Any],
        tags: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Extract specific financial metrics from company facts.

        Args:
            facts: Company facts dictionary from get_company_facts()
            tags: List of XBRL tags to extract (without us-gaap: prefix)

        Returns:
            Dictionary mapping tags to their historical values
        """
        result = {}

        us_gaap = facts.get("facts", {}).get("us-gaap", {})

        for tag in tags:
            tag_data = us_gaap.get(tag, {})
            if not tag_data:
                continue

            units = tag_data.get("units", {})
            label = tag_data.get("label", tag)
            description = tag_data.get("description", "")

            # Get USD values (most common for financial data)
            usd_values = units.get("USD", [])
            # Also check for shares (for share count data)
            shares_values = units.get("shares", [])
            # Pure numbers (for ratios like EPS)
            pure_values = units.get("USD/shares", [])

            values = usd_values or shares_values or pure_values

            if values:
                result[tag] = {
                    "label": label,
                    "description": description,
                    "values": values
                }

        return result

    def get_annual_financials(
        self,
        facts: Dict[str, Any],
        years_back: int = 5
    ) -> Dict[str, Any]:
        """
        Extract annual financial data (10-K) from company facts.

        Args:
            facts: Company facts dictionary
            years_back: Number of years of data to extract

        Returns:
            Dictionary with annual financial data organized by fiscal year
        """
        from ..mappings.xbrl_tags import PRIORITY_TAGS

        metrics = self.extract_financial_metrics(facts, PRIORITY_TAGS)

        # Organize by fiscal year
        by_year = {}

        for tag, data in metrics.items():
            for entry in data.get("values", []):
                # Only get annual data (10-K filings)
                form = entry.get("form")
                if form not in ["10-K", "10-K/A"]:
                    continue

                fy = entry.get("fy")
                if not fy:
                    continue

                if fy not in by_year:
                    by_year[fy] = {
                        "fiscal_year": fy,
                        "filed": entry.get("filed"),
                        "form": form,
                    }

                by_year[fy][tag] = entry.get("val")

        # Sort by year and limit
        years = sorted(by_year.keys(), reverse=True)[:years_back]
        return {str(y): by_year[y] for y in years}

    def get_quarterly_financials(
        self,
        facts: Dict[str, Any],
        quarters_back: int = 12
    ) -> Dict[str, Any]:
        """
        Extract quarterly financial data (10-Q) from company facts.

        Args:
            facts: Company facts dictionary
            quarters_back: Number of quarters of data to extract

        Returns:
            Dictionary with quarterly financial data
        """
        from ..mappings.xbrl_tags import PRIORITY_TAGS

        metrics = self.extract_financial_metrics(facts, PRIORITY_TAGS)

        # Organize by period
        by_period = {}

        for tag, data in metrics.items():
            for entry in data.get("values", []):
                # Only get quarterly data (10-Q filings)
                form = entry.get("form")
                if form not in ["10-Q", "10-Q/A"]:
                    continue

                end_date = entry.get("end")
                if not end_date:
                    continue

                if end_date not in by_period:
                    by_period[end_date] = {
                        "period_end": end_date,
                        "filed": entry.get("filed"),
                        "form": form,
                        "fy": entry.get("fy"),
                        "fp": entry.get("fp"),
                    }

                by_period[end_date][tag] = entry.get("val")

        # Sort by date and limit
        periods = sorted(by_period.keys(), reverse=True)[:quarters_back]
        return {p: by_period[p] for p in periods}

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
