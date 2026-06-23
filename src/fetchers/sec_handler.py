"""SEC EDGAR API handler for financial reports and filings."""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .rate_limiter import RateLimiter, RetryHandler, is_transient_error

# Default time-to-live for cached SEC payloads (companyfacts/submissions).
# These update at most quarterly, so a week-long cache is safe and slashes
# repeated network load when re-running over many tickers.
_DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 3600


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
        user_agent: str = "StockDataCollector admin@example.com",
        rate_limit_delay: float = 0.12,
        cache_dir: Optional[Path] = None,
        logger: Optional[logging.Logger] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        cache_ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS,
    ):
        """
        Initialize SEC EDGAR handler.

        Args:
            user_agent: Required User-Agent header (format: "Company email@domain.com")
            rate_limit_delay: Minimum seconds between requests (SEC allows 10/sec)
            cache_dir: Optional directory for caching CIK mapping
            logger: Optional logger instance
            max_retries: Max retry attempts for transient network failures
            base_delay: Initial backoff delay in seconds
            max_delay: Maximum backoff delay cap in seconds
            cache_ttl_seconds: Lifetime of cached companyfacts/submissions payloads.
                Set <= 0 to disable on-disk payload caching.
        """
        self.user_agent = user_agent
        self.cache_dir = cache_dir or Path("data/cache")
        self.cache_ttl_seconds = cache_ttl_seconds
        self.logger = logger or logging.getLogger(__name__)
        # Guards lazy CIK-map loading when fetching tickers concurrently.
        self._cik_lock = threading.Lock()

        self.rate_limiter = RateLimiter(
            min_interval=rate_limit_delay,
            name="sec"
        )

        self.retry_handler = RetryHandler(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            logger=self.logger
        )

        # Create session for connection pooling
        self.session = requests.Session()
        self.session.headers.update(self._get_headers())

        # CIK mapping cache
        self._cik_mapping: Optional[Dict[str, str]] = None

        # Fallback mappings for tickers missing from SEC's company_tickers.json
        # These are common stocks where only preferred tickers are listed
        self._fallback_cik_mapping = {
            "BAC": "70858",      # Bank of America Corp
            "PLD": "1045609",    # Prologis, Inc.
        }

    def _get_headers(self) -> Dict[str, str]:
        """Get required headers for SEC API requests."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

    def _request(self, url: str, timeout: int = 30) -> requests.Response:
        """Perform a rate-limited GET with retries on transient failures.

        Retries timeouts, connection errors, and retryable HTTP statuses
        (429/502/503/504); non-retryable responses (incl. 404/403) are returned
        as-is for the caller to interpret. Raises ``RequestException`` only when
        transient retries are exhausted.
        """
        def do_get() -> requests.Response:
            self.rate_limiter.wait()
            response = self.session.get(url, timeout=timeout)
            # Trigger a retry for transient HTTP statuses; leave other statuses
            # (200, 404, 403, ...) for the caller to handle.
            if response.status_code in (429, 502, 503, 504):
                response.raise_for_status()
            return response

        return self.retry_handler.run(
            do_get,
            retryable_exceptions=(requests.exceptions.RequestException,),
            should_retry=is_transient_error,
        )

    # ---- on-disk payload cache (companyfacts/submissions) ----------------

    def _payload_cache_path(self, name: str, cik: str) -> Path:
        return self.cache_dir / f"{name}_CIK{cik}.json"

    def _read_payload_cache(self, path: Path) -> Optional[Dict[str, Any]]:
        """Return cached JSON if present and fresher than the TTL, else None."""
        if self.cache_ttl_seconds <= 0 or not path.exists():
            return None
        if (time.time() - path.stat().st_mtime) > self.cache_ttl_seconds:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.debug(f"Cache read failed for {path}: {e}")
            return None

    def _write_payload_cache(self, path: Path, data: Dict[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            self.logger.debug(f"Cache write failed for {path}: {e}")

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

        # Load mapping if not cached (lock guards concurrent first-use).
        if self._cik_mapping is None:
            with self._cik_lock:
                if self._cik_mapping is None:
                    self._load_cik_mapping()

        # _load_cik_mapping leaves the mapping None if the fetch failed; treat as
        # empty for this lookup (the next call will retry the load).
        cik = (self._cik_mapping or {}).get(ticker)
        if cik:
            return str(cik).zfill(10)

        # Check fallback mappings for known missing tickers
        fallback_cik = self._fallback_cik_mapping.get(ticker)
        if fallback_cik:
            return str(fallback_cik).zfill(10)

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
            response = self._request(url, timeout=30)
            response.raise_for_status()

            data = response.json()
            self._cik_mapping = self._parse_tickers_json(data)

            # Cache the mapping
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w') as f:
                json.dump(data, f)

            self.logger.info(f"Loaded {len(self._cik_mapping)} ticker mappings")

        except Exception as e:
            # Leave the mapping as None (not {}) so a later ticker can retry the
            # fetch within the same run instead of being permanently disabled.
            self.logger.error(f"Error fetching CIK mapping: {e}")
            self._cik_mapping = None

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

        cache_path = self._payload_cache_path("companyfacts", cik)
        cached = self._read_payload_cache(cache_path)
        if cached is not None:
            self.logger.debug(f"companyfacts cache hit for CIK {cik}")
            return cached

        url = f"{self.SEC_BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
        self.logger.debug(f"Fetching company facts from {url}")

        response = self._request(url, timeout=60)

        # 404 is a genuine "this company has no XBRL data" — distinct from an error.
        if response.status_code == 404:
            self.logger.warning(f"No XBRL data found for CIK {cik}")
            return None

        # Any other non-2xx (e.g. 403 blocked User-Agent) is surfaced, not silently
        # swallowed as "no data". raise_for_status() propagates to fetch_all, which
        # records it as company_facts_error.
        response.raise_for_status()
        data = response.json()
        self._write_payload_cache(cache_path, data)
        return data

    def get_submissions(self, cik: str) -> Optional[Dict[str, Any]]:
        """
        Get filing submission history for a company.

        Args:
            cik: 10-digit CIK number

        Returns:
            Dictionary containing submissions data, or None on error
        """
        cik = str(cik).zfill(10)

        cache_path = self._payload_cache_path("submissions", cik)
        cached = self._read_payload_cache(cache_path)
        if cached is not None:
            self.logger.debug(f"submissions cache hit for CIK {cik}")
            return cached

        url = f"{self.SEC_BASE_URL}/submissions/CIK{cik}.json"
        self.logger.debug(f"Fetching submissions from {url}")

        response = self._request(url, timeout=30)

        if response.status_code == 404:
            self.logger.warning(f"No submissions found for CIK {cik}")
            return None

        response.raise_for_status()
        data = response.json()

        # Extract relevant fields
        result = {
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

        self._write_payload_cache(cache_path, result)
        return result

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

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
