"""The market data's three sources (Milestone 4): their parsers, pure, and their fetchers, cached on disk
(docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md, "The market data").

- FRED's ``fredgraph.csv``, without a key: NCBEILQ027S, the nonfinancial corporate business's corporate equities (a
  liability level, quarterly, $ millions, from 1945-10), and GDP (quarterly, $ billions SAAR, from 1947-01). A header
  row ("observation_date,<ID>"; older files "DATE,<ID>"), then a row per period, an empty cell where it has no value
  (older files: ".").
- Shiller's ``ie_data.xls``, which shillerdata.com links on a CDN under a versioned URL, so the link (an https one) is
  read from the page each time: the Data sheet's monthly price (P, a mean of daily closes), dividend (D), earnings (E),
  CPI and CAPE, found by their header labels (a missing one is an answer that doesn't parse, never a wrong column).
  Its dates are numbers: 1871.01 is January, 2026.1 October (the month is round(100 x the fraction)). The latest
  months have a price but no dividend or earnings yet, so each series stops at its own last value.
- Treasury.gov's yearly CSV of the daily par yield curve (the one ``FREDHandler._fetch_treasury_gov_year`` reads the
  10-year rate from): a row per trading day, newest first, the date MM/DD/YYYY, each maturity's yield in percent, a
  blank where it wasn't issued; older years have fewer columns, but every year since 1990 has the 3-month, 2-year and
  10-year. A file without rows is a failed answer, except the current year's in its first days.

The fetchers share one ``requests`` session (requests' own User-Agent, never the SEC's, which may carry an email), a
rate limiter (a request a second) and retries of the transient failures. Each answer is cached under
``<cache-dir>/market/`` once it parses, and a cached copy is reused while it is fresh: FRED's series, Shiller's page
and workbook and the current Treasury year for a day; a past Treasury year once it was cached after that year ended in
New York (a copy fetched during the year is incomplete) and has rows. A failed fetch, or an answer that doesn't parse,
falls back to the cached copy and says so; without one, the source fails.
"""
import csv
import html
import io
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar
from urllib.parse import urljoin, urlsplit

import requests

from ..fetchers.rate_limiter import RETRYABLE_STATUS_CODES, RateLimiter, RetryHandler, is_transient_error
from . import xls

#: FRED's series: the nonfinancial corporate business's corporate equities ($ millions) and GDP ($ billions).
FRED_SERIES: Tuple[str, ...] = ("NCBEILQ027S", "GDP")
#: The first column's header in FRED's CSV, without case: today's, and older files'.
FRED_DATE_HEADERS: Tuple[str, ...] = ("observation_date", "date")
#: A FRED cell without a value: "." in older files, an empty cell in today's (NCBEILQ027S's, 2026-10-24).
FRED_NO_VALUE: Tuple[str, ...] = (".", "")
#: Shiller's page, which links his workbook.
SHILLER_PAGE_URL = "https://shillerdata.com/"
#: The workbook's sheet of monthly data.
SHILLER_SHEET = "Data"
#: Each column read from the Data sheet and its header label, as the sheet writes it (spaces around aside).
SHILLER_LABELS: Dict[str, str] = {"date": "Date", "price": "P", "dividend": "D", "earnings": "E", "cpi": "CPI",
                                  "cape": "CAPE"}
#: The series read, in order (all the columns but the date).
SHILLER_SERIES: Tuple[str, ...] = ("price", "dividend", "earnings", "cpi", "cape")
#: The header row is looked for in the sheet's first rows.
SHILLER_HEADER_ROWS = 30
#: Treasury.gov's column headers and the maturities they hold, in order; other columns are ignored.
TREASURY_COLUMNS: Dict[str, str] = {
    "1 Mo": "1m", "1.5 Month": "6w", "2 Mo": "2m", "3 Mo": "3m", "4 Mo": "4m", "6 Mo": "6m", "1 Yr": "1y",
    "2 Yr": "2y", "3 Yr": "3y", "5 Yr": "5y", "7 Yr": "7y", "10 Yr": "10y", "20 Yr": "20y", "30 Yr": "30y"}
#: The columns every year's file has had since 1990: rows without one of them are a renamed header, not a year
#: without those yields (review 18's I-1).
TREASURY_REQUIRED: Tuple[str, ...] = ("3 Mo", "2 Yr", "10 Yr")
#: A Treasury.gov cell that holds no yield, beside a blank.
TREASURY_NO_VALUE = "N/A"
#: The first year of Treasury.gov's daily par yield curve.
TREASURY_FROM = 1990
#: Seconds between requests; the retries of a transient failure and the first backoff, in seconds (it doubles).
MIN_INTERVAL = 1.0
MAX_RETRIES = 3
RETRY_DELAY = 2.0
TIMEOUT = 60.0
#: A cached copy fetched a day ago or less is fresh.
DAY = 86400.0
#: New York's year ends at midnight EST, 05:00 UTC: a Treasury year cached from then on is whole.
NEW_YORK_YEAR_END_HOUR_UTC = 5
#: The cache's folder in the cache directory.
CACHE_FOLDER = "market"

T = TypeVar("T")
_HREF = re.compile(r"""href\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_WORKBOOK_LINK = re.compile(r"(?:^|/)ie_data\.xls(?:[?#]|$)", re.IGNORECASE)


def fred_url(series_id: str) -> str:
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"


def treasury_url(year: int) -> str:
    return ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
            f"{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")


def year_end(year: int) -> float:
    """When *year* ended in New York (epoch seconds)."""
    return datetime(year + 1, 1, 1, NEW_YORK_YEAR_END_HOUR_UTC, tzinfo=timezone.utc).timestamp()


def _number(value: Any) -> Optional[float]:
    """A cell's finite number, or None (a string, a boolean, nothing)."""
    return value if isinstance(value, float) and math.isfinite(value) else None


# ---- parsers ------------------------------------------------------------------------------------------------------


def parse_fred_csv(text: str) -> List[Tuple[str, float]]:
    """A fredgraph.csv's (ISO date, value) rows, oldest first, a cell without a value (FRED_NO_VALUE) skipped. Raises
    ValueError for a text that isn't one: no FRED header, a date that isn't ISO, a value that isn't a number, a date
    twice."""
    lines = [line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    if not lines or lines[0].split(",")[0].strip().lower() not in FRED_DATE_HEADERS:
        raise ValueError(f"not a FRED CSV: it starts {ascii(text[:40])}")
    rows: Dict[str, float] = {}
    for line in lines[1:]:
        day, _, cell = line.partition(",")
        iso = date.fromisoformat(day.strip()).isoformat()
        if cell.strip() in FRED_NO_VALUE:
            continue
        value = float(cell)
        if not math.isfinite(value):
            raise ValueError(f"FRED's value for {iso} is not a number: {ascii(line)}")
        if iso in rows:
            raise ValueError(f"FRED's CSV has {iso} twice")
        rows[iso] = value
    return sorted(rows.items())


def parse_treasury_csv(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """A Treasury.gov yearly CSV's yields by maturity (TREASURY_COLUMNS' names): (ISO date, percent) rows, oldest
    first, a blank (a maturity not issued that day) skipped, other columns ignored. A file without rows (an empty
    answer, or the header alone) has none: the current year's in its first days, else a failed answer
    (``fetch_treasury``). Raises ValueError for a text that isn't one: no Date column first, rows without one of
    TREASURY_REQUIRED's columns, a date that isn't MM/DD/YYYY, a yield that isn't a number."""
    rows = [row for row in csv.reader(io.StringIO(text.lstrip("\ufeff"))) if any(cell.strip() for cell in row)]
    if not rows:
        return {}
    header = [cell.strip() for cell in rows[0]]
    if header[0] != "Date":
        raise ValueError(f"not a Treasury.gov yield curve CSV: it starts {ascii(text[:40])}")
    missing = [name for name in TREASURY_REQUIRED if name not in header]
    if missing and len(rows) > 1:
        raise ValueError(f"Treasury.gov's CSV has rows but no {', '.join(map(repr, missing))} column (its header: "
                         f"{ascii(','.join(header)[:120])})")
    columns = {i: TREASURY_COLUMNS[name] for i, name in enumerate(header) if name in TREASURY_COLUMNS}
    series: Dict[str, Dict[str, float]] = {}
    for row in rows[1:]:
        iso = datetime.strptime(row[0].strip(), "%m/%d/%Y").date().isoformat()
        for i, name in columns.items():
            cell = row[i].strip() if i < len(row) else ""
            if cell and cell.upper() != TREASURY_NO_VALUE:
                value = float(cell)
                if not math.isfinite(value):
                    raise ValueError(f"Treasury.gov's {name} yield on {iso} is not a number: {ascii(cell)}")
                series.setdefault(name, {})[iso] = value
    return {name: sorted(series[name].items()) for name in TREASURY_COLUMNS.values() if name in series}


def shiller_month(value: Any) -> Optional[str]:
    """The month a Shiller date names, as its first day: 1871.01 is "1871-01-01", 2026.1 "2026-10-01" (the month is
    round(100 x the fraction)). None for a cell that isn't such a number."""
    number = _number(value)
    if number is None or not 1000 <= number < 10000:
        return None
    year = int(number)
    month = round((number - year) * 100)
    return f"{year:04d}-{month:02d}-01" if 1 <= month <= 12 else None


def _shiller_header(rows: Dict[int, Dict[int, xls.Cell]]) -> Tuple[int, Dict[str, int]]:
    """The header row and each label's column (its leftmost cell reading the label): of the sheet's first
    SHILLER_HEADER_ROWS rows, the one with the most of SHILLER_LABELS. Raises ValueError naming any it lacks."""
    wanted = {label: name for name, label in SHILLER_LABELS.items()}
    header = -1
    columns: Dict[str, int] = {}
    for row in sorted(rows)[:SHILLER_HEADER_ROWS]:
        found: Dict[str, int] = {}
        for col in sorted(rows[row]):
            value = rows[row][col]
            if isinstance(value, str) and value.strip() in wanted:
                found.setdefault(wanted[value.strip()], col)
        if len(found) > len(columns):
            header, columns = row, found
    missing = [label for name, label in SHILLER_LABELS.items() if name not in columns]
    if missing:
        where = (f" (its row {header + 1} has {', '.join(repr(SHILLER_LABELS[n]) for n in columns)})"
                 if columns else "")
        raise ValueError(f"Shiller's {SHILLER_SHEET} sheet has no header {', '.join(map(repr, missing))}{where}")
    return header, columns


def parse_shiller(data: bytes) -> Dict[str, List[Tuple[str, float]]]:
    """Shiller's monthly series (SHILLER_SERIES) from his workbook's Data sheet: (the month's first day, value) rows,
    oldest first. The columns are found by their header labels (``_shiller_header``); the months are the rows below
    it whose date reads as a month (``shiller_month``); a cell that isn't a number ("NA", blank) has no value, so each
    series begins and stops at its own months. Raises ValueError (``xls.XLSError`` for the workbook itself) for a
    missing label, a month twice, or a series without any value."""
    rows: Dict[int, Dict[int, xls.Cell]] = {}
    for (row, col), value in xls.read_sheet(data, SHILLER_SHEET).items():
        rows.setdefault(row, {})[col] = value
    header, columns = _shiller_header(rows)
    series: Dict[str, List[Tuple[str, float]]] = {name: [] for name in SHILLER_SERIES}
    months: Dict[str, int] = {}
    for row in sorted(r for r in rows if r > header):
        month = shiller_month(rows[row].get(columns["date"]))
        if month is None:
            continue
        if month in months:
            raise ValueError(f"Shiller's {SHILLER_SHEET} sheet has {month} twice (rows {months[month] + 1} and "
                             f"{row + 1})")
        months[month] = row
        for name in SHILLER_SERIES:
            number = _number(rows[row].get(columns[name]))
            if number is not None:
                series[name].append((month, number))
    for name in SHILLER_SERIES:
        if not series[name]:
            raise ValueError(f"Shiller's {SHILLER_SHEET} sheet has no {SHILLER_LABELS[name]!r} values")
    return {name: sorted(rows) for name, rows in series.items()}


def find_xls_link(page: str) -> str:
    """The URL of the first ``https`` link to ``ie_data.xls`` on Shiller's page, its entities unescaped and made
    absolute (a protocol-relative or relative link against the page's URL). The URL is fetched, stored and shown as
    the CAPE's source link, so a link of another scheme (``http:``, ``javascript:``, ``data:``) is skipped (review
    18's I-2). Raises ValueError without one."""
    for _, href in _HREF.findall(page):
        link = html.unescape(href.strip())
        if _WORKBOOK_LINK.search(link):
            url = urljoin(SHILLER_PAGE_URL, link)
            if urlsplit(url).scheme == "https":
                return url
    raise ValueError("Shiller's page has no https link to ie_data.xls")


# ---- fetchers -----------------------------------------------------------------------------------------------------


class MarketFetchError(OSError):
    """An answer that couldn't be fetched, or didn't parse, without a cached copy to fall back on."""


@dataclass
class Answer:
    """Where an answer came from: its URL, when it was fetched (epoch seconds; a cached copy's mtime), whether it was
    fetched now, and the note said when a failed fetch fell back to the cached copy."""

    url: str
    fetched_at: float
    fetched: bool
    note: Optional[str] = None


@dataclass
class SourceData:
    """A source's series as parsed (name -> (date, value) rows, oldest first), the URL it was read from and when that
    answer was fetched (Treasury's: the latest year's), its answers fetched and read from the cache, and the notes of
    its fallbacks to a cached copy."""

    series: Dict[str, List[Tuple[str, float]]]
    url: str
    fetched_at: float
    fetched: int = 0
    cached: int = 0
    notes: List[str] = field(default_factory=list)


def _reason(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class MarketFetcher:
    """The sources' answers through one *session* (a ``requests.Session`` of its own by default: requests' own
    User-Agent), *limiter* (MIN_INTERVAL apart) and *retry* (transient failures), each cached under
    *cache_dir*/market/ once it parses. *clock* gives the time, and the cached copies' mtimes; *log* says each
    fallback to a cached copy."""

    def __init__(self, cache_dir: Path, session: Any = None, clock: Callable[[], float] = time.time,
                 log: Callable[[str], None] = lambda line: None, limiter: Optional[RateLimiter] = None,
                 retry: Optional[RetryHandler] = None) -> None:
        self.cache = Path(cache_dir) / CACHE_FOLDER
        self.session = session if session is not None else requests.Session()
        self.clock = clock
        self.log = log
        self.limiter = limiter if limiter is not None else RateLimiter(min_interval=MIN_INTERVAL, name="market")
        self.retry = retry if retry is not None else RetryHandler(max_retries=MAX_RETRIES, base_delay=RETRY_DELAY)

    def _download(self, url: str) -> bytes:
        """The answer's bytes: a timeout, a dropped connection or a 429/50x retried; another status raises OSError."""
        def attempt() -> Any:
            self.limiter.wait()
            response = self.session.get(url, timeout=TIMEOUT)
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
            return response

        response = self.retry.run(attempt, retryable_exceptions=(requests.RequestException,),
                                  should_retry=is_transient_error)
        if response.status_code != 200:
            raise OSError(f"HTTP {response.status_code}")
        return bytes(response.content)

    def cached(self, name: str, parse: Callable[[bytes], T]) -> Optional[T]:
        """*parse* of the cached copy of *name*: None without one, or for one that doesn't parse."""
        path = self.cache / name
        if not path.is_file():
            return None
        try:
            return parse(path.read_bytes())
        except ValueError:
            return None

    def get(self, url: str, name: str, parse: Callable[[bytes], T], not_before: float) -> Tuple[T, Answer]:
        """*parse* of the answer for *url*, cached as *name*: the cached copy when it was fetched at or after
        *not_before* (epoch seconds) and parses, else fetched, and cached once it parses. A failed fetch, or an answer
        that doesn't parse, falls back to the cached copy, saying so; without one, MarketFetchError."""
        path = self.cache / name
        mtime = path.stat().st_mtime if path.is_file() else None
        if mtime is not None and mtime >= not_before:
            try:
                return parse(path.read_bytes()), Answer(url, mtime, False)
            except ValueError:
                pass  # a copy that no longer parses is fetched again
        try:
            content = self._download(url)
            parsed = parse(content)
        except (OSError, ValueError) as exc:
            if mtime is not None:
                try:
                    kept = parse(path.read_bytes())
                except ValueError:
                    pass
                else:
                    note = f"{name}: the fetch failed ({_reason(exc)}); using the cached copy of {_utc(mtime)}"
                    self.log(note)
                    return kept, Answer(url, mtime, False, note)
            raise MarketFetchError(f"{url}: {_reason(exc)}") from exc
        now = self.clock()
        self.cache.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(content)
        os.replace(part, path)
        os.utime(path, (now, now))
        return parsed, Answer(url, now, True)


def _text(parse: Callable[[str], T]) -> Callable[[bytes], T]:
    """*parse* of an answer's text (UTF-8, a byte-order mark dropped)."""
    return lambda content: parse(content.decode("utf-8-sig"))


def _source(series: Dict[str, List[Tuple[str, float]]], answers: Sequence[Answer]) -> SourceData:
    """A source's series with its answers' tally; its URL and time are the last answer's."""
    return SourceData(series, answers[-1].url, answers[-1].fetched_at, fetched=sum(a.fetched for a in answers),
                      cached=sum(not a.fetched for a in answers), notes=[a.note for a in answers if a.note])


def fetch_fred(fetcher: MarketFetcher, series_id: str) -> SourceData:
    """FRED's *series_id* (``parse_fred_csv``), its cached copy refreshed when over a day old."""
    rows, answer = fetcher.get(fred_url(series_id), f"fred_{series_id}.csv", _text(parse_fred_csv),
                               fetcher.clock() - DAY)
    return _source({series_id: rows}, [answer])


def fetch_shiller(fetcher: MarketFetcher) -> SourceData:
    """Shiller's series (``parse_shiller``) from the workbook his page links (``find_xls_link``); the page and the
    workbook each refreshed when their cached copy is over a day old. Its URL is the workbook's link."""
    not_before = fetcher.clock() - DAY
    link, page = fetcher.get(SHILLER_PAGE_URL, "shiller.html",
                             lambda content: find_xls_link(content.decode("utf-8", "replace")), not_before)
    series, workbook = fetcher.get(link, "ie_data.xls", parse_shiller, not_before)
    return _source(series, [page, workbook])


def _treasury_year(required: bool) -> Callable[[bytes], Dict[str, List[Tuple[str, float]]]]:
    """``parse_treasury_csv`` of a yearly file's bytes. A file without rows raises ValueError when *required* (a past
    year's, or the current year's once its cached copy has rows): a failed answer, not a year without rows yet."""

    def parse(content: bytes) -> Dict[str, List[Tuple[str, float]]]:
        parsed = parse_treasury_csv(content.decode("utf-8-sig"))
        if required and not parsed:
            raise ValueError("Treasury.gov's file has no rows")
        return parsed

    return parse


def fetch_treasury(fetcher: MarketFetcher, first_year: int, last_year: int,
                   log: Callable[[str], None] = lambda line: None) -> SourceData:
    """Treasury.gov's yields from *first_year* to *last_year*, the current year, merged by maturity, oldest first:
    the current year's copy refreshed when over a day old; a past year's kept once it was cached after that year
    ended (``year_end``), else fetched again. A file without rows is a failed answer for a past year (a cached copy
    without rows is fetched again) and for the current year once its cached copy has rows; the current year's first
    days, before its first row, have none (``_treasury_year``). A progress line (*log*) for each year fetched; the URL
    and time are the current year's. Raises ValueError for no years."""
    if first_year > last_year:
        raise ValueError(f"no Treasury years from {first_year} to {last_year}")
    merged: Dict[str, Dict[str, float]] = {}
    answers = []
    for year in range(first_year, last_year + 1):
        cached_as = f"treasury_{year}.csv"
        if year < last_year:  # a past year: whole once cached after it ended, and never without rows
            not_before, required = year_end(year), True
        else:  # the current year: without rows in its first days, unless its cached copy already has some
            not_before, required = fetcher.clock() - DAY, bool(fetcher.cached(cached_as, _treasury_year(False)))
        parsed, answer = fetcher.get(treasury_url(year), cached_as, _treasury_year(required), not_before)
        for name, rows in parsed.items():
            merged.setdefault(name, {}).update(rows)
        if answer.fetched:
            log(f"treasury {year}: fetched, {max(map(len, parsed.values()), default=0)} days")
        answers.append(answer)
    series = {name: sorted(merged[name].items()) for name in TREASURY_COLUMNS.values() if name in merged}
    return _source(series, answers)
