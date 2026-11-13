"""SEC EDGAR API handler for financial reports and filings."""

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from ..parsers.xbrl_instance import companyfacts_from_instance
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

#: The most reports a collection reads from their own XBRL when the companyfacts lack them (Q17): a year's, the
#: latest and the three before it; the submissions keep as many (``reports``).
XBRL_REPORTS_READ = 4

#: The shape of a filing's facts as the handler caches them (``get_filing_facts``): a file of another is read again.
_FILING_CACHE_VERSION = 1

#: A filing's linkbases and the EDGAR documents beside its instance, none of them an instance.
_NOT_INSTANCES = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "filingsummary.xml")


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


def _newest_reports(filings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The newest XBRL_REPORTS_READ of *filings* that ``_latest_report``'s rule looks for, newest first."""
    reports = [f for f in filings if f.get("form") in _REPORT_FORMS and f.get("isXBRL") != 0]
    reports.sort(key=lambda f: (str(f.get("filingDate") or ""), str(f.get("acceptanceDateTime") or "")), reverse=True)
    return reports[:XBRL_REPORTS_READ]


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


def _named(filing: Dict[str, Any]) -> Dict[str, str]:
    """A report as a run names it: its form, accession and filing date."""
    return {"form": str(filing.get("form")), "accession": str(filing.get("accessionNumber")),
            "filed": str(filing.get("filingDate") or "")[:10]}


def reports_without_facts(submissions: Optional[Dict[str, Any]],
                          facts: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The reports *submissions* list (``_latest_report``'s rule: as filed, with XBRL) that *facts* carry no fact of,
    newest first, from the latest back to the first the facts carry, at most XBRL_REPORTS_READ: the gap whose figures a
    run reads from the filings' own XBRL (Q17). Its first is ``report_without_facts``'; empty when that is None. The
    reports are the submissions' ``reports`` (the newest of the feed's "recent" block), else, in a file cached before
    those were kept, the newest 100 filings and the latest report."""
    listed = submissions or {}
    candidates = [f for f in [*(listed.get("reports") or []), *(listed.get("filings") or []),
                              listed.get("latest_report") or {}]
                  if f.get("form") in _REPORT_FORMS and f.get("isXBRL") != 0]
    newest: Dict[str, Dict[str, Any]] = {}
    for filing in sorted(candidates, key=lambda f: (str(f.get("filingDate") or ""),
                                                     str(f.get("acceptanceDateTime") or "")), reverse=True):
        newest.setdefault(str(filing.get("accessionNumber") or ""), filing)
    carried = {e.get("accn") for e in _entries(facts or {})}
    gap: List[Dict[str, Any]] = []
    for accession, filing in newest.items():
        if not accession or accession in carried or len(gap) == XBRL_REPORTS_READ:
            break
        gap.append(filing)
    return gap


def merge_report_facts(facts: Dict[str, Any], read: List[Dict[str, Any]]) -> Dict[str, Any]:
    """*facts* (companyfacts) with the facts of reports read from their own XBRL (*read*, each in companyfacts' shape,
    ``get_filing_facts``) beside them, under the same taxonomy, concept and unit. The companyfacts win: a fact of an
    accession they carry any fact of is left out (a report they lack has none; one they carry is never read)."""
    carried = {e.get("accn") for e in _entries(facts)}
    merged = {taxonomy: dict(tags) for taxonomy, tags in (facts.get("facts") or {}).items()}
    for piece in read:
        for taxonomy, tags in (piece.get("facts") or {}).items():
            into_taxonomy = merged.setdefault(taxonomy, {})
            for tag, body in tags.items():
                held = into_taxonomy.get(tag) or {"units": {}}
                into = {**held, "units": {unit: list(entries) for unit, entries in (held.get("units") or {}).items()}}
                for unit, entries in (body.get("units") or {}).items():
                    into["units"].setdefault(unit, []).extend(e for e in entries if e.get("accn") not in carried)
                into_taxonomy[tag] = into
    return {**facts, "facts": merged}


def _instance_name(names: List[str], primary: str) -> Optional[str]:
    """A filing's XBRL instance among its documents (*names*, as its index lists them): EDGAR's extracted instance of
    an inline filing (the primary document's ``_htm.xml``, else the one ``_htm.xml``), else the ``.xml`` that is no
    linkbase and no FilingSummary, the one named after the filing's schema first. None when there is none."""
    stem = primary.rsplit(".", 1)[0]
    if f"{stem}_htm.xml" in names:
        return f"{stem}_htm.xml"
    extracted = sorted(n for n in names if n.lower().endswith("_htm.xml"))
    if extracted:
        return extracted[0]
    schemas = {n[:-4] for n in names if n.lower().endswith(".xsd")}
    instances = sorted((n[:-4] not in schemas, n) for n in names
                       if n.lower().endswith(".xml") and not n.lower().endswith(_NOT_INSTANCES)
                       and not re.fullmatch(r"R\d+\.xml", n))  # an early filing's rendered pages
    return instances[0][1] if instances else None


def _why(exc: Exception) -> str:
    """An exception as a run's warning gives the reason."""
    return str(exc) or type(exc).__name__


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
            the latest report the submissions list (``report_without_facts``); then the facts of the reports they lack
            are read from the filings' own XBRL into ``company_facts`` (``_read_gap``, Q17)
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

        # The latest report the submissions list whose facts the companyfacts lack (BUG-251), and those reports' facts
        # read from their own XBRL (Q17).
        missing = report_without_facts(data.get("submissions"), data.get("company_facts"))
        if missing:
            data["missing_report"] = missing
            self.logger.warning(f"The SEC's companyfacts of {ticker} carry no fact of its {missing['form']} filed "
                                f"{missing['filed']} ({missing['accession']})")
            if data.get("company_facts"):
                self._read_gap(ticker, cik, data)

        if "company_facts_error" in data or "submissions_error" in data:
            self.logger.warning(f"SEC data for {ticker} is incomplete (see the error above)")
        else:
            self.logger.info(f"Successfully fetched SEC data for {ticker}")
        return data

    def _read_gap(self, ticker: str, cik: str, data: Dict[str, Any]) -> None:
        """Read the reports *data*'s companyfacts lack (``reports_without_facts``: the latest and up to three before
        it) from their own XBRL (``get_filing_facts``: one request each, two for a filing that isn't inline) into
        ``company_facts`` (``merge_report_facts``). ``reports_read`` names those read and their instance documents,
        ``reports_unread`` those that couldn't be, and why; a failure never fails the collection. When every one was
        read the run's figures are whole: ``missing_report`` gains ``recovered``, and the run isn't stored as
        incomplete (it is still named in the run's warning)."""
        read: List[Dict[str, Any]] = []
        unread: List[Dict[str, str]] = []
        pieces: List[Dict[str, Any]] = []
        for filing in reports_without_facts(data.get("submissions"), data.get("company_facts")):
            try:
                instance, facts = self.get_filing_facts(cik, filing)
            except Exception as e:
                unread.append({**_named(filing), "why": _why(e)})
                self.logger.warning(f"The XBRL of {ticker}'s {filing.get('form')} filed {filing.get('filingDate')} "
                                    f"couldn't be read: {_why(e)}")
                continue
            read.append({**_named(filing), "instance": instance})
            pieces.append(facts)
        if pieces:
            data["company_facts"] = merge_report_facts(data["company_facts"], pieces)
        data["reports_read"], data["reports_unread"] = read, unread
        if read and not unread:
            data["missing_report"] = {**data["missing_report"], "recovered": True}

    # ---- a filing's own XBRL (Q17) ----------------------------------------

    def _filing_cache_path(self, cik: str, accession: str) -> Path:
        return self.cache_dir / f"xbrl_CIK{str(cik).zfill(10)}_{accession}.json"

    def _read_filing_cache(self, path: Path) -> Optional[Dict[str, Any]]:
        """A filing's cached facts (``get_filing_facts``), at any age, as a filing's documents never change; None when
        the cache is off (a TTL of 0 or less), or the file is missing, unreadable or of another shape."""
        if self.cache_ttl_seconds <= 0 or not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
        except Exception as e:
            self.logger.debug(f"Cache read failed for {path}: {e}")
            return None
        if not isinstance(cached, dict) or cached.get("version") != _FILING_CACHE_VERSION:
            return None
        return cached

    def get_filing_facts(self, cik: str, filing: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """The facts of *filing* (an entry of the submissions feed) from its own XBRL instance, in companyfacts' shape
        (``companyfacts_from_instance``: its form and filing date as the feed lists them), and the instance's name.
        Read from the payload cache when a run cached it before; else fetched through the rate limiter (``_instance``)
        and cached. Raises when the instance can't be fetched or read."""
        accession = str(filing.get("accessionNumber") or "")
        path = self._filing_cache_path(cik, accession)
        cached = self._read_filing_cache(path)
        if cached is not None:
            return str(cached.get("instance")), cached.get("facts") or {}
        instance, content = self._instance(cik, filing)
        facts = companyfacts_from_instance(content, cik, accession, str(filing.get("form")),
                                           str(filing.get("filingDate") or "")[:10])
        self._write_payload_cache(path, {"version": _FILING_CACHE_VERSION, "accession": accession,
                                         "instance": instance, "facts": facts})
        return instance, facts

    def _instance(self, cik: str, filing: Dict[str, Any]) -> Tuple[str, bytes]:
        """*filing*'s XBRL instance document: its name and content. An inline filing's is EDGAR's extracted instance,
        named after its primary document (``c-20260630.htm`` -> ``c-20260630_htm.xml``): one request. Else, or when
        that isn't there, the filing's index names it (``_instance_name``): two."""
        folder = (f"{self.SEC_WWW_URL}/Archives/edgar/data/{int(cik)}/"
                  f"{str(filing.get('accessionNumber') or '').replace('-', '')}/")
        primary = str(filing.get("primaryDocument") or "")
        tried: List[str] = []
        if filing.get("isInlineXBRL") and primary.lower().endswith((".htm", ".html")):
            name = primary.rsplit(".", 1)[0] + "_htm.xml"
            response = self._request(folder + name, timeout=60)
            if response.status_code == 200:
                return name, response.content
            tried.append(f"HTTP {response.status_code} for {name}")
        response = self._request(folder + "index.json", timeout=30)
        if response.status_code != 200:
            raise LookupError("; ".join(tried + [f"HTTP {response.status_code} for the filing's index"]))
        items = ((response.json() or {}).get("directory") or {}).get("item") or []
        found = _instance_name([str(item.get("name") or "") for item in items], primary)
        if not found:
            raise LookupError("; ".join(tried + ["no XBRL instance among the filing's documents"]))
        response = self._request(folder + found, timeout=60)
        if response.status_code != 200:
            raise LookupError("; ".join(tried + [f"HTTP {response.status_code} for {found}"]))
        return found, response.content

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
            when it lists none), which the newest 100 of a bank's feed don't reach (BUG-285); ``reports``, the newest
            XBRL_REPORTS_READ reports by that rule, newest first (Q17)
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
            # The newest reports by the same rule, which a run whose companyfacts lack them reads from their own XBRL.
            "reports": _newest_reports(recent),
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
