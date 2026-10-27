"""SEC EDGAR API handler for financial reports and filings."""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

from .rate_limiter import RateLimiter, RetryHandler, is_transient_error

#: A registrant SEC's ticker map now sends to a successor formed to take its place (a holding
#: company reorganization), mapped from the successor's CIK: the successor's company facts are
#: read with the predecessor's, so the ticker keeps its history (Q6, the user's decisions of
#: 2026-10-21). XOM since 2026-07: ExxonMobil Holdings Corp, whose 10-Qs follow Exxon Mobil
#: Corp's 20 years. BLK since 2024-10: BlackRock, Inc., formed with the GIP acquisition, after
#: the old BlackRock, now BlackRock Finance, Inc.
PREDECESSOR_CIKS: Dict[str, str] = {
    "0002115436": "0000034088",
    "0002012383": "0001364742",
}

_REPORT_FORMS = ("10-K", "10-Q", "20-F", "40-F")


def _entries(facts: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    for tags in (facts.get("facts") or {}).values():
        for body in tags.values():
            for entries in (body.get("units") or {}).values():
                yield from entries


def merge_company_facts(current: Dict[str, Any], earlier: Dict[str, Any]) -> Dict[str, Any]:
    """*current*'s company facts with a predecessor's (*earlier*) beside them: each tag's entries
    from both filers, whose accession numbers never coincide, and the successor's own name. The
    predecessor's count only as filed before the successor's first annual or quarterly report:
    from then on the successor reports, and a predecessor that keeps filing as its subsidiary
    (Exxon Mobil Corp's 10-Q of 2026-08-03) reports another entity."""
    reports = [e.get("filed") or "" for e in _entries(current) if (e.get("form") or "").startswith(_REPORT_FORMS)]
    first = min(reports) if reports else None
    facts: Dict[str, Dict[str, Any]] = {}
    for source in (earlier, current):
        for taxonomy, tags in (source.get("facts") or {}).items():
            merged = facts.setdefault(taxonomy, {})
            for tag, body in tags.items():
                into = merged.setdefault(tag, {**{k: v for k, v in body.items() if k != "units"}, "units": {}})
                for unit, entries in (body.get("units") or {}).items():
                    into["units"].setdefault(unit, []).extend(
                        entries if source is current or first is None
                        else [e for e in entries if (e.get("filed") or "") < first])
    return {**current, "facts": facts}


def _latest_report(filings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The latest of *filings* (a submissions feed's entries) that ``report_without_facts`` looks for, None when there
    is none: a report the parser reads, as filed (a 10-K, 10-Q, 20-F or 40-F, not an amendment, which often has no
    financial facts: a 10-K/A of Part III; nor a 10-KT), and filed with XBRL, as only such a report ever gets facts
    that change the record."""
    reports = [f for f in filings if f.get("form") in _REPORT_FORMS and f.get("isXBRL") != 0]
    if not reports:
        return None
    return max(reports, key=lambda f: (str(f.get("filingDate") or ""), str(f.get("acceptanceDateTime") or "")))


def report_without_facts(submissions: Optional[Dict[str, Any]],
                         facts: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The latest report *submissions* (``get_submissions``') list when *facts*, the companyfacts read with them (None
    when that request failed or the SEC has none), carry no fact of it: the SEC's lag, after which a run's figures
    stop at the report before (BUG-251: on 2026-10-25 DLR's companyfacts had no fact of its 10-Q of 2026-07-31, though
    they had its 424B7 of 2026-08-19). Its form, accession and filing date; None when the facts carry it, or when there
    is none to look for.

    The report is the latest of the feed's whole "recent" block (``_latest_report``'s rule: as filed, with XBRL), kept
    beside the newest 100 filings as ``latest_report``, which a bank's 424B2s fill in days (BUG-285: JPM's held 94
    424B2s of two days and no report); submissions cached before that was kept give their newest 100 alone. Its facts
    count under any taxonomy (an IFRS filer's 20-F under ifrs-full)."""
    listed = submissions or {}
    # The newest 100 and the latest report behind them: an empty entry where none was kept is no report.
    latest = _latest_report([*(listed.get("filings") or []), listed.get("latest_report") or {}])
    if latest is None:
        return None
    accession = latest.get("accessionNumber")
    if not accession or any(e.get("accn") == accession for e in _entries(facts or {})):
        return None
    return {"form": str(latest["form"]), "accession": str(accession), "filed": str(latest.get("filingDate") or "")[:10]}


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
            Dictionary containing all fetched SEC data, with ``missing_report`` when the companyfacts carry no fact of
            the latest report the submissions list (``report_without_facts``)
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

        # A successor's history is its predecessor's filings (PREDECESSOR_CIKS).
        predecessor = PREDECESSOR_CIKS.get(cik)
        if predecessor and data.get("company_facts"):
            data["predecessor_cik"] = predecessor
            try:
                earlier = self.get_company_facts(predecessor)
            except Exception as e:
                earlier = None
                data["predecessor_error"] = str(e) or type(e).__name__
            if earlier:
                data["company_facts"] = merge_company_facts(data["company_facts"], earlier)
                data["predecessor_name"] = earlier.get("entityName")
            else:
                data.setdefault("predecessor_error", "no XBRL data")
                self.logger.warning(f"Predecessor CIK {predecessor} of {ticker} not read: {data['predecessor_error']}")

        # Fetch submissions (filing history)
        try:
            submissions = self.get_submissions(cik)
            if submissions:
                data["submissions"] = submissions
        except Exception as e:
            self.logger.error(f"Error fetching submissions: {e}")
            data["submissions_error"] = str(e)

        # The latest report the submissions list whose facts the companyfacts lack (BUG-251).
        missing = report_without_facts(data.get("submissions"), data.get("company_facts"))
        if missing:
            data["missing_report"] = missing
            self.logger.warning(f"The SEC's companyfacts of {ticker} carry no fact of its {missing['form']} filed "
                                f"{missing['filed']} ({missing['accession']})")

        if "company_facts_error" in data or "submissions_error" in data:
            self.logger.warning(f"SEC data for {ticker} is incomplete (see the error above)")
        else:
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
            Dictionary containing submissions data, or None on error: ``filings``, the newest 100 of the feed's
            "recent" block, and ``latest_report``, the latest report of that whole block (``_latest_report``; None
            when it lists none), which the newest 100 of a bank's feed don't reach (BUG-285)
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
        # Every entry of the "recent" block (a year's, or the latest 1,000 when that's more), newest first.
        feed = data.get("filings", {})
        recent = self._extract_recent_filings(feed, limit=len((feed.get("recent") or {}).get("accessionNumber") or []))

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
            "filings": recent[:100],
            "latest_report": _latest_report(recent),
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

    def get_raw(self, url: str) -> requests.Response:
        """GET any SEC URL (a filing's document, a submissions page) through this handler's rate limiter,
        retries and User-Agent; the caller reads the status and the body."""
        return self._request(url, timeout=30)

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
