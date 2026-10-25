"""``stock-data refresh`` (Milestone 5): keep the database current with one command a scheduler can run
(docs/superpowers/specs/2026-10-25-universe-automation-design.md, "stock-data refresh").

    stock-data refresh [--steps LIST] [--force] [--tickers LIST] [--db PATH] [--output-dir DIR] [--cache-dir DIR]

The steps, in this order, each in its own try/except (a failure is reported and the others go on). Each step with a
cadence is skipped while it succeeded in a run that started within its period (the ``refresh_log`` table: daily 20
hours, weekly 6 days 20 hours; a step's age is its run's, so one that finished late in a long run is due at the next
day's run), unless ``--force`` (every step) or named in ``--steps`` (only those run). A weekly step whose latest
success left something undone runs again (code review 19, I-3): ``sec`` after a feed failed, or when a company it put
on the list wasn't collected in that run; ``insiders`` and ``gurus`` after an item failed.

- ``quotes`` (daily): every company's quote through the web's quote refresh (``fetchers.quote_refresh``). A company
  with a split since its last collection goes on the collection list instead; one priced after a stock dividend's or
  a spin-off's factor is stored with it and valued on it, as the web job does (BUG-128).
- ``bars`` (daily): every company's daily bars from its last stored bar (which may have been partial) to today, one
  ``history(start=...)`` request with a collection's parameters, its bars from that day on replaced. Yahoo is asked
  from the latest bar before it with a close above 0 (of the LOOKBACK before it), which is final, so its close shows
  whether Yahoo's prices are on the stored bars' basis. A company whose basis can't be matched (another close there,
  a split Yahoo lists after its last bar, bars stored dividend-adjusted) goes on the collection list instead, and one
  already on it is left to the collection. An answer without bars (yfinance's answer to a timeout or throttling) and
  a company without such a bar are that company's failures: its bars stay, and it isn't queued. Then the benchmark's
  (^GSPC, which the GP tab compares against) the same way, whatever --tickers names; never queued, as the next
  collection's benchmark fetch stores it again.
- ``sec`` (weekly): every company's submissions feed, read fresh through the SEC handler, every one of its recent
  filings (not the newest 100 the handler keeps for its other callers, which a bank's 424B2s fill in days). A 10-K,
  10-Q, 20-F, 40-F, a transition report (10-KT, 10-QT) or an amendment of one filed on or after the day of the
  company's last collection with the SEC puts it on the list.
- ``collect`` (whenever the list isn't empty): a full collection of the list as ``stock-data`` runs one (json, csv
  and sqlite, stored and valued a batch at a time), its SEC payloads read fresh; a company it didn't store fails it.
- ``insiders`` and ``gurus`` (weekly): M3's collectors, the insiders' last three years for every company with a CIK
  (incremental: each Form 4 is fetched once and cached), then the gurus' 13F filings, their CUSIPs' tickers (OpenFIGI,
  with its cache) and the gurus' positions.
- ``market`` (daily): M4's market data, its three sources.
- ``recompute`` (when a step stored something): every company's valuations, history, scores and indicators, then the
  ranks (``valuation.engine.compute_and_store``).

``--tickers`` limits the per-company steps to some of the database's companies (for testing). Such a run's rows carry
its ``subset`` and count for no step's cadence: the next full run is due as if it hadn't happened. One run at a time:
``<db>.refresh.lock`` is held with an OS file lock for the whole run, released by the OS should the process die, and a
second run exits 3, naming the first's PID and start time. A line per step and per batch of companies, then a summary
(each step: ran or skipped and why, ok or failed and why, its counts; the collection list), printed and appended to
``<output-dir>/logs/refresh-<YYYY-MM-DD>.log``. Exit 0 when every step ran or was skipped, 1 when one failed, 2 for a
missing database (none is created) or a usage error, 3 for a run already going.
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import threading
import traceback
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, TextIO, Tuple

from .config import AppConfig, SECConfig, StorageConfig, YahooConfig
from .exporters.sqlite_store import SQLiteStore, read_only_uri
from .fetchers.quote_refresh import QuoteRefresh, describe_splits, refresh_quote
from .fetchers.sec_handler import SECHandler
from .fetchers.stock_data_fetcher import StockDataFetcher
from .fetchers.yahoo_handler import YahooHandler
from .market.collect import SOURCES as MARKET_SOURCES
from .market.collect import collect_market
from .ownership.collect import (
    Form4Source,
    ThirteenFSource,
    collect_gurus,
    collect_insiders,
    map_cusips,
    store_guru_positions,
)
from .ownership.gurus import GURUS
from .ownership.openfigi import OpenFIGI
from .universe.tickers import normalize_ticker
from .valuation.engine import compute_and_store

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

#: The steps, in the order a run takes them.
STEPS: Tuple[str, ...] = ("quotes", "bars", "sec", "collect", "insiders", "gurus", "market", "recompute")
#: A daily step's period: under a day, so a run scheduled daily never skips one because yesterday's ran a few minutes
#: later in the day.
DAILY = timedelta(hours=20)
#: A weekly step's period: under a week, for the same reason.
WEEKLY = timedelta(days=6, hours=20)
#: Each step with a cadence and its period; collect and recompute run as needed.
PERIODS: Dict[str, timedelta] = {"quotes": DAILY, "bars": DAILY, "sec": WEEKLY, "insiders": WEEKLY,
                                 "gurus": WEEKLY, "market": DAILY}
#: The reports whose filing puts a company on the collection list, each with its amendment ("10-K/A"): the transition
#: reports a change of fiscal year brings (10-KT, 10-QT) too.
REPORT_FORMS: Tuple[str, ...] = ("10-K", "10-Q", "20-F", "40-F", "10-KT", "10-QT")
#: A long step says how far it is every BATCH companies.
BATCH = 25
#: The benchmark index whose bars a collection stores beside the companies' (``fetch_and_export``'s ^GSPC), which the
#: GP tab compares a stock against; the bars step refreshes them too.
BENCHMARK = "^GSPC"
#: How far Yahoo's close for the bar before a company's last stored one may be from the stored close, as a fraction,
#: for the two to be on the same basis: that bar is final, and a stock dividend or a spin-off moves it by about 1% or
#: more (SCCO's 1.01, SCCO's 1.008, HON's 0.9535), a split by far more.
BASIS_TOLERANCE = 0.005
#: How many stored bars before a company's last one the bars step looks through for the latest with a close above 0,
#: the bar Yahoo's basis is checked on: a bar stored without a close, or with 0, can't show it (code review 19, I-2).
LOOKBACK = 10
#: The insiders' window in years and the gurus' quarters: M3's CLI defaults (src/ownership/collect.py).
INSIDER_YEARS = 3.0
GURU_QUARTERS = 8
#: The collection's formats and workers: ``stock-data``'s defaults.
COLLECT_FORMATS: Tuple[str, ...] = ("json", "csv", "sqlite")
COLLECT_WORKERS = 4
#: The exit codes: every step ran or was skipped; a step failed; no database (or a usage error, as argparse's own);
#: another run holds the lock.
EXIT_OK, EXIT_FAILED, EXIT_NO_DATABASE, EXIT_RUNNING = 0, 1, 2, 3
#: Where in the lock file the lock is held: past the PID and start time it holds, so a second run can read them while
#: the first holds the lock (Windows locks the bytes themselves).
_LOCK_OFFSET = 4096
_PERIOD_NAMES = {DAILY: "daily", WEEKLY: "weekly"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _nothing() -> None:
    return None


@dataclass
class Services:
    """What the steps fetch, collect and compute with: the real handlers and collectors from ``real_services``, fakes
    in the tests, so these never touch the network or sleep.

    *yahoo*: ``fetch_quote(ticker)`` and ``fetch_price_bars_since(ticker, start)`` (``YahooHandler``); *sec*:
    ``recent_filings(cik)`` (``FreshSECHandler``); *collect(tickers, log)*: a full collection, returning
    ``fetch_and_export``'s summary; *insiders(tickers, log)*, *gurus(log)*, *market(log)*: M3's and M4's collectors,
    returning their summaries; *compute(tickers)*: the valuations, every company for None, returning the companies
    valued; *clock*: now, UTC; *close*: releases what they hold. *log* takes each of the collector's lines."""

    yahoo: Any
    sec: Any
    collect: Callable[[List[str], Callable[[str], None]], Dict[str, Any]]
    insiders: Callable[[List[str], Callable[[str], None]], Dict[str, Dict[str, Any]]]
    gurus: Callable[[Callable[[str], None]], Dict[str, Any]]
    market: Callable[[Callable[[str], None]], Dict[str, Dict[str, Any]]]
    compute: Callable[[Optional[List[str]]], int]
    clock: Callable[[], datetime] = _now
    close: Callable[[], None] = _nothing


class FreshSECHandler(SECHandler):
    """The app's SEC handler (its User-Agent, rate limit and retries) that reads no cached companyfacts or
    submissions, which it still writes for the app's other readers. The handler's 7-day cache would show a weekly step
    a feed up to a week old, hiding the filings since, and give a collection the facts from before the filing that put
    its company on the list, which would then be stored without it and never be collected for it again."""

    def _read_payload_cache(self, path: Path) -> Optional[Dict[str, Any]]:
        return None

    def recent_filings(self, cik: str) -> Optional[List[Dict[str, Any]]]:
        """Every filing of *cik*'s submissions feed's "recent" block, newest first (the SEC keeps a year's there, or
        the latest 1,000 when that's more), not the newest 100 ``get_submissions`` keeps for its other callers: GS,
        MS, JPM, C, BAC and WFC file dozens of 424B2s a day, which push a 10-Q filed days before out of those 100. One
        request, through the rate limiter, retries and User-Agent; the payload cache is left alone. None when the SEC
        has no feed for the CIK."""
        response = self._request(f"{self.SEC_BASE_URL}/submissions/CIK{str(cik).zfill(10)}.json", timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        filings = response.json().get("filings") or {}
        every = len((filings.get("recent") or {}).get("accessionNumber") or [])
        return self._extract_recent_filings(filings, limit=every)


class _CollectorLines(logging.Handler):
    """Hands a collection's progress to *log*: each ticker's "Completed" line and every error, not the collector's
    every message."""

    def __init__(self, log: Callable[[str], None]) -> None:
        super().__init__(logging.INFO)
        self.log = log

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.levelno >= logging.ERROR:
                self.log(f"ERROR: {message}")
            elif record.levelno == logging.INFO and message.startswith("Completed "):
                self.log(message)
        except Exception:  # a log line must never stop the collection
            self.handleError(record)


def collect_tickers(db_path: Path, output_dir: Path, cache_dir: Path, tickers: List[str],
                    log: Callable[[str], None]) -> Dict[str, Any]:
    """A full collection of *tickers*, as ``stock-data`` runs one (``StockDataFetcher.fetch_and_export``: Yahoo and
    the SEC, every year, json, csv and sqlite, which values them), into the database at *db_path*, the exports under
    *output_dir*. Its SEC handler is a ``FreshSECHandler`` on *cache_dir*: the facts a company is collected for are
    the SEC's of now. Each ticker's "Completed" line and every error go to *log*. Returns the collection's summary."""
    config = AppConfig(sec=SECConfig(), yahoo=YahooConfig(),
                       storage=StorageConfig(base_dir=Path(output_dir), db_path=Path(db_path)),
                       output_formats=list(COLLECT_FORMATS), max_workers=COLLECT_WORKERS)
    logger = logging.getLogger(f"{__name__}.collect")
    lines = _CollectorLines(log)
    logger.addHandler(lines)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        with StockDataFetcher(config=config, logger=logger) as fetcher:
            fetcher.sec_handler.close()
            fetcher.sec_handler = FreshSECHandler(
                user_agent=config.sec.user_agent, rate_limit_delay=config.sec.rate_limit_delay,
                cache_dir=Path(cache_dir), logger=logger, max_retries=config.retry.max_retries,
                base_delay=config.retry.base_delay, max_delay=config.retry.max_delay)
            return fetcher.fetch_and_export(tickers=list(tickers), include_yahoo=True, include_sec=True,
                                            years_back=None, formats=list(COLLECT_FORMATS))
    finally:
        logger.removeHandler(lines)


def real_services(db_path: Path, output_dir: Path, cache_dir: Path) -> Services:
    """The services of a real run: Yahoo through the app's handler (its 2.5 s pacing); the SEC through a
    ``FreshSECHandler`` on *cache_dir* with the app's User-Agent (``SECConfig``, as M3's CLI builds it), for the feeds,
    the insiders and the gurus; the collection (``collect_tickers``); M3's and M4's collectors and the valuation
    engine on *db_path*, with their caches under *cache_dir*."""
    logger = logging.getLogger(__name__)
    yahoo = YahooHandler(rate_limit_delay=YahooConfig().rate_limit_delay)
    sec = FreshSECHandler(user_agent=SECConfig().user_agent, cache_dir=Path(cache_dir))

    def collect(tickers: List[str], log: Callable[[str], None]) -> Dict[str, Any]:
        return collect_tickers(db_path, output_dir, cache_dir, tickers, log)

    def insiders(tickers: List[str], log: Callable[[str], None]) -> Dict[str, Dict[str, Any]]:
        return collect_insiders(db_path, tickers, INSIDER_YEARS, Form4Source(sec, Path(cache_dir)), log=log)

    def gurus(log: Callable[[str], None]) -> Dict[str, Any]:
        return {"filings": collect_gurus(db_path, GURUS, GURU_QUARTERS, ThirteenFSource(sec, Path(cache_dir)), log=log),
                "cusips": map_cusips(db_path, OpenFIGI(Path(cache_dir)), log=log),
                "positions": store_guru_positions(db_path, GURUS, log=log)}

    def market(log: Callable[[str], None]) -> Dict[str, Dict[str, Any]]:
        return collect_market(db_path, MARKET_SOURCES, Path(cache_dir), log=log)

    def compute(tickers: Optional[List[str]]) -> int:
        return compute_and_store(db_path, tickers=tickers, logger=logger)

    return Services(yahoo=yahoo, sec=sec, collect=collect, insiders=insiders, gurus=gurus, market=market,
                    compute=compute, close=sec.close)


class Output:
    """The run's lines: each printed to *stream* (stdout by default) with its time, in what the stream can show (a
    cp1252 console can't print every character), and appended to the log file at *log_path* (UTF-8, its folder made),
    which also takes ``note``'s detail lines. Safe from the collection's worker threads."""

    def __init__(self, stream: Optional[TextIO] = None, log_path: Optional[Path] = None,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.stream: TextIO = stream if stream is not None else sys.stdout
        self.clock = clock
        self._lock = threading.Lock()
        self._file: Optional[TextIO] = None
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self._file = open(log_path, "a", encoding="utf-8")

    def say(self, line: str) -> None:
        """Print *line* and log it."""
        stamped = f"{self.clock():%H:%M:%S} {line}"
        encoding = getattr(self.stream, "encoding", None) or "utf-8"
        with self._lock:
            self.stream.write(stamped.encode(encoding, "replace").decode(encoding) + "\n")
            self.stream.flush()
            self._write(stamped)

    def note(self, line: str) -> None:
        """Log *line* only: a detail the console can do without."""
        with self._lock:
            self._write(f"{self.clock():%H:%M:%S} {line}")

    def _write(self, stamped: str) -> None:
        if self._file is not None:
            self._file.write(stamped + "\n")
            self._file.flush()

    def close(self) -> None:
        """Close the log file."""
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


class LockHeld(Exception):
    """Another run holds the lock at *path*; *holder* is what its lock file says: its PID and start time."""

    def __init__(self, path: Path, holder: Dict[str, Any]) -> None:
        self.path = path
        self.holder = holder
        super().__init__(f"another refresh holds {path}: PID {holder.get('pid', 'unknown')}, started "
                         f"{holder.get('started', 'at a time it did not write')}")


def _lock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _holder(fd: int) -> Dict[str, Any]:
    """What a lock file says of its holder, {} when it says nothing readable (the holder hasn't written it yet)."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        holder = json.loads(os.read(fd, _LOCK_OFFSET).decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return holder if isinstance(holder, dict) else {}


class RefreshLock:
    """``stock-data refresh``'s lock on the file at *path* (``lock_path``): an OS file lock held from ``acquire`` to
    ``release`` (``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere), which the OS releases should the process
    die, so a lock a dead run left is simply taken: no PID needs checking. The file holds the holder's PID and start
    time as JSON for the message a second run gives, and stays after the run: only the lock says a run is going (a
    file removed at the end could be locked by one run while a third created another)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: Optional[int] = None

    def acquire(self, started: str) -> None:
        """Take the lock and write this process's PID and *started* in the file. Raises LockHeld, naming the holder,
        when another run holds it."""
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _lock(fd)
        except OSError:
            holder = _holder(fd)
            os.close(fd)
            raise LockHeld(self.path, holder) from None
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"pid": os.getpid(), "started": started}).encode("utf-8"))
        self._fd = fd

    def release(self) -> None:
        """Release the lock, if held."""
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None


def lock_path(db_path: Path) -> Path:
    """The lock file of the refreshes of the database at *db_path*: beside it, ``<db>.refresh.lock``."""
    return Path(f"{db_path}.refresh.lock")


def is_report(form: Optional[str]) -> bool:
    """Whether a filing's *form* is a report whose filing puts its company on the collection list: a 10-K, 10-Q, 20-F
    or 40-F (``REPORT_FORMS``), or an amendment of one ("10-Q/A")."""
    if not form:
        return False
    return (form[:-2] if form.endswith("/A") else form) in REPORT_FORMS


def read_companies(db_path: Path, tickers: Optional[Sequence[str]] = None) -> List[Tuple[str, Optional[str]]]:
    """The database's companies and their CIKs, by ticker, through a read-only connection: those of *tickers* only,
    when given, each of which must be there (ValueError naming those that aren't)."""
    with closing(sqlite3.connect(read_only_uri(db_path), uri=True)) as conn:
        stored = [(t, c) for t, c in conn.execute("SELECT ticker, cik FROM companies ORDER BY ticker")]
    if tickers is None:
        return stored
    unknown = sorted(set(tickers) - {t for t, _ in stored})
    if unknown:
        raise ValueError(f"--tickers names companies that aren't in the database: {', '.join(unknown)}")
    return [(t, c) for t, c in stored if t in set(tickers)]


def _span(span: timedelta, seconds: bool = False) -> str:
    """*span* as "6d 19h 59m" (and its seconds, "1m 5s", with *seconds*)."""
    total = max(0, int(span.total_seconds()))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{n}{unit}" for n, unit in ((days, "d"), (hours, "h"), (minutes, "m")) if n]
    if seconds and (secs or not parts):
        parts.append(f"{secs}s")
    return " ".join(parts) or "0m"


def _utc(text: str) -> datetime:
    when = datetime.fromisoformat(text)
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _parsed(text: Any) -> Dict[str, Any]:
    """A ``refresh_log`` summary's JSON, {} for one that isn't an object (a row this code didn't write)."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _named(items: Any, limit: int = 10) -> str:
    """A summary's tickers (or gurus' CIKs) for a line: the first *limit*, and how many more."""
    names = [str(item) for item in items]
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


@dataclass
class StepResult:
    """A step of a run: whether it *ran* (else *reason* says why it was skipped) and why it ran *again* though it
    succeeded within its period (``_Run._undone``), whether it went *ok* (None when skipped) and why not (*reason*),
    its *summary* (its counts, stored in ``refresh_log``), whether it *stored* anything (the recompute's trigger) and
    its *headline*, the summary's line."""

    step: str
    ran: bool = False
    ok: Optional[bool] = None
    reason: Optional[str] = None
    summary: Dict[str, Any] = field(default_factory=dict)
    stored: bool = False
    headline: str = ""
    again: Optional[str] = None


@dataclass
class RunReport:
    """A run: when it *started* (UTC), each step's result in order, and the collection *queue* (ticker -> why, in the
    order the steps put it there)."""

    started: datetime
    results: List[StepResult] = field(default_factory=list)
    queue: Dict[str, List[str]] = field(default_factory=dict)

    def result(self, step: str) -> StepResult:
        return next(r for r in self.results if r.step == step)

    @property
    def exit_code(self) -> int:
        """EXIT_FAILED when a step failed, else EXIT_OK."""
        return EXIT_FAILED if any(r.ok is False for r in self.results) else EXIT_OK


def summary_lines(report: RunReport) -> List[str]:
    """The run's summary: each step, ran (again, and why) or skipped and why, ok or failed and why, its counts; the
    collection list; the exit code."""
    lines = [f"Summary of the refresh started {report.started.astimezone().isoformat(timespec='seconds')}:"]
    for r in report.results:
        ran = f"ran again ({r.again})" if r.again else "ran"
        if not r.ran:
            lines.append(f"  {r.step}: skipped ({r.reason})")
        elif r.ok:
            lines.append(f"  {r.step}: {ran}, ok: {r.headline}")
        else:
            lines.append(f"  {r.step}: {ran}, FAILED: {r.reason}" + (f" ({r.headline})" if r.headline else ""))
    if report.queue:
        lines.append(f"Collection list ({len(report.queue)}): " + ", ".join(
            f"{ticker} ({'; '.join(why)})" for ticker, why in report.queue.items()))
    else:
        lines.append("Collection list: empty")
    failed = [r.step for r in report.results if r.ok is False]
    lines.append(f"Exit {report.exit_code}: " + (f"failed: {', '.join(failed)}" if failed
                                                 else "every step ran or was skipped"))
    return lines


class _Run:
    """One run's state: the database, the services, the output, the steps asked for, the companies and the report."""

    def __init__(self, db_path: Path, services: Services, output: Output, steps: Optional[Sequence[str]],
                 force: bool, tickers: Optional[Sequence[str]]) -> None:
        self.db_path = db_path
        self.store = SQLiteStore(db_path)
        self.services = services
        self.out = output
        self.named = None if steps is None else set(steps)
        self.force = force
        self.subset = tickers is not None
        self.companies = read_companies(db_path, tickers)
        self.report = RunReport(started=services.clock())

    # ---- the run ----------------------------------------------------------

    def go(self) -> RunReport:
        asked = ("steps " + ", ".join(s for s in STEPS if s in self.named) if self.named is not None
                 else "every step due")
        self.out.say(f"Refresh of {self.db_path}: {len(self.companies)} companies, {asked}"
                     + (", forced" if self.force else ""))
        for step in STEPS:
            started = self.services.clock()
            result = StepResult(step)
            skip, again = self._skip(step)
            if skip is not None:
                result.reason = skip
                result.summary["skipped"] = skip
                self.out.say(f"{step}: skipped ({skip})")
            else:
                result.ran = True
                if again is not None:
                    result.again = result.summary["again"] = again
                self.out.say(f"{step}: running" + (f" again: {again}" if again is not None else ""))
                try:
                    getattr(self, f"_{step}")(result)
                except Exception as exc:  # anything one step raises is its own: the others go on
                    result.ok = False
                    result.reason = f"{type(exc).__name__}: {exc}"
                    result.summary["error"] = result.reason
                    for line in traceback.format_exc().splitlines():
                        self.out.note(f"{step}: {line}")
                took = _span(self.services.clock() - started, seconds=True)
                self.out.say(f"{step}: done in {took}: {result.headline}" if result.ok
                             else f"{step}: FAILED after {took}: {result.reason}")
            self._record(result, started, self.services.clock())
            self.report.results.append(result)
        for line in summary_lines(self.report):
            self.out.say(line)
        return self.report

    def _skip(self, step: str) -> Tuple[Optional[str], Optional[str]]:
        """Why *step* is skipped (None when it runs), and why it runs again though it succeeded within its period
        (None unless it does: ``_undone``). A step's age is its run's: one that finished late in a long run (the
        market data after hours of collection) is due at the next day's run (code review 19, m-5)."""
        if self.named is not None and step not in self.named:
            return "not named in --steps", None
        if step == "collect":
            return (None if self.report.queue else "nothing to collect"), None
        if step == "recompute":
            stored = any(r.stored for r in self.report.results)
            return (None if self.named is not None or self.force or stored else "no step stored anything"), None
        if self.named is not None or self.force:
            return None, None
        last = self._last_success(step)
        if last is None:
            return None, None
        row_id, run, summary = last
        try:
            age = self.services.clock() - _utc(run)
        except ValueError:  # a log row this code didn't write: the step runs
            return None, None
        period = PERIODS[step]
        if age >= period:
            return None, None
        when = f"succeeded in the run started {_span(age)} ago, within its {_PERIOD_NAMES[period]} period"
        undone = self._undone(step, row_id, run, summary)
        if undone is not None:
            return None, f"{when}, but that run {undone}"
        return f"{when} ({_span(period)})", None

    def _log_rows(self, sql: str, *params: Any) -> Iterator[Tuple[Any, ...]]:
        """``refresh_log``'s rows for *sql*, read one at a time, so a caller that stops at the first it wants reads
        no more; none from a database without the table (one from before the refresh) or one it can't be read from,
        so that a step whose cadence can't be read runs."""
        try:
            with closing(sqlite3.connect(read_only_uri(self.db_path), uri=True)) as conn:
                yield from conn.execute(sql, params)
        except sqlite3.Error:
            return

    def _last_success(self, step: str) -> Optional[Tuple[int, str, Dict[str, Any]]]:
        """*step*'s latest ``refresh_log`` row that finished ok in a run of every company: its id, when its run
        started (its summary's ``run``; the row's own start for a row without one) and its summary. None without one.
        A ``--tickers`` run's rows carry its ``subset`` and are passed over: a test on a few companies refreshed no
        other, so the next full run is due as if it hadn't happened."""
        for row_id, started_at, text in self._log_rows(
                "SELECT id, started_at, summary FROM refresh_log WHERE step = ? AND ok = 1 "
                "ORDER BY id DESC", step):
            summary = _parsed(text)
            if "subset" in summary:  # a --tickers run's
                continue
            run = summary.get("run")
            return row_id, run if isinstance(run, str) else str(started_at), summary
        return None

    def _undone(self, step: str, row_id: int, run: str, summary: Dict[str, Any]) -> Optional[str]:
        """What a weekly *step*'s latest success (the ``refresh_log`` row *row_id*, of the run started *run*, with
        its *summary*) left undone, which makes it run again within its period (code review 19, I-3); None when
        nothing did, and for a daily step, which runs again the next day anyway.

        An item that failed: a company's feed (``sec``), a company's Form 4s (``insiders``), a guru's 13F filings
        (``gurus``). And for ``sec``, the companies it put on the collection list when that run's ``collect`` step
        didn't finish ok (it failed or was skipped, or the run stopped before it ended), which lost the list. Running
        the SEC step again is safe: a report filed on or after the day of a company's latest collection with the SEC
        is found again until that company is collected."""
        if PERIODS.get(step) != WEEKLY:
            return None
        undone = []
        errors = summary.get("errors")
        if isinstance(errors, (dict, list)) and errors:
            undone.append(f"had {'feed ' if step == 'sec' else ''}errors for {_named(errors)}")
        listed = summary.get("new_filings") if step == "sec" else None
        if isinstance(listed, dict) and listed:
            after = next(self._log_rows("SELECT ok, summary FROM refresh_log WHERE step = 'collect' AND id > ? "
                                        "ORDER BY id LIMIT 1", row_id), None)
            if after is None or _parsed(after[1]).get("run") != run:
                undone.append(f"put {_named(listed)} on the collection list, and stopped before its collect step "
                              f"finished")
            elif after[0] != 1:
                skipped = _parsed(after[1]).get("skipped")
                state = "failed" if after[0] == 0 else "was skipped" + (f" ({skipped})" if skipped else "")
                undone.append(f"put {_named(listed)} on the collection list, and its collect step {state}")
        return "; ".join(undone) or None

    def _record(self, result: StepResult, started: datetime, finished: datetime) -> None:
        summary: Dict[str, Any] = {"run": self.report.started.isoformat()}
        if self.subset:  # a --tickers run's rows count for no step's cadence (_last_success)
            summary["subset"] = [ticker for ticker, _ in self.companies]
        summary.update(result.summary)
        try:
            self.store.record_refresh_step(result.step, started.isoformat(), finished.isoformat(), result.ok, summary)
        except sqlite3.Error as exc:
            self.out.say(f"{result.step}: not logged in refresh_log ({exc}): it runs again next time")

    def _queue(self, ticker: str, why: str) -> None:
        self.report.queue.setdefault(ticker, []).append(why)

    def _progress(self, step: str, done: int, total: int, counts: str = "") -> None:
        if done % BATCH == 0 or done == total:
            self.out.say(f"{step}: {done} of {total} companies" + (f": {counts}" if counts else ""))

    def _rows(self, sql: str, *params: Any) -> List[Tuple[Any, ...]]:
        with closing(sqlite3.connect(read_only_uri(self.db_path), uri=True)) as conn:
            return conn.execute(sql, params).fetchall()

    # ---- quotes -----------------------------------------------------------

    def _quotes(self, result: StepResult) -> None:
        """Every company's quote (``refresh_quote``); a split since its last collection puts it on the list, and one
        priced after a factor since is valued on it right away, as the web job does (BUG-128)."""
        s = result.summary
        s.update({"tickers": len(self.companies), "stored": 0, "rebased": [], "to_collect": {}, "errors": {}})
        total = len(self.companies)
        for done, (ticker, _cik) in enumerate(self.companies, 1):
            try:
                outcome = refresh_quote(self.services.yahoo, self.store, ticker)
            except Exception as exc:  # one company's refresh is its own (a locked database): the others go on
                outcome = QuoteRefresh(ticker, "error", f"{type(exc).__name__}: {exc}")
            if outcome.saved:
                s["stored"] += 1
                result.stored = True
                if outcome.outcome == "rebased":
                    s["rebased"].append(ticker)
                self.out.note(f"quotes: {ticker} stored at {outcome.price}"
                              + (f", with {describe_splits(outcome.splits)}" if outcome.splits else ""))
            elif outcome.outcome == "split":
                why = describe_splits(outcome.splits)
                s["to_collect"][ticker] = why
                self._queue(ticker, f"quotes: {why} since its last collection")
                self.out.note(f"quotes: {ticker} to collect: {why} since its last collection")
            else:
                s["errors"][ticker] = outcome.message
                self.out.note(f"quotes: {ticker} failed: {outcome.message}")
            self._progress("quotes", done, total, f"{s['stored']} stored, {len(s['to_collect'])} to collect, "
                                                  f"{len(s['errors'])} errors")
        if s["rebased"]:
            try:
                s["rebased_valued"] = self.services.compute(list(s["rebased"]))
            except Exception as exc:  # the quotes have landed; the recompute values them again
                s["rebased_error"] = f"{type(exc).__name__}: {exc}"
        result.ok = total == 0 or len(s["errors"]) < total
        if not result.ok:
            result.reason = "every quote failed"
        result.headline = (f"{s['stored']} of {total} quotes stored"
                           + (f" ({len(s['rebased'])} with a price factor, valued on it: {', '.join(s['rebased'])})"
                              if s["rebased"] else "")
                           + f", {len(s['to_collect'])} to collect, {len(s['errors'])} errors")

    # ---- bars -------------------------------------------------------------

    def _bars(self, result: StepResult) -> None:
        """Every company's daily bars since its last stored one (``_bars_since``), but those on the collection list,
        a company whose basis can't be matched going on it; then the benchmark's (``_benchmark_bars``), whatever
        --tickers names: it isn't a company."""
        s = result.summary
        s.update({"tickers": len(self.companies), "updated": {}, "written": 0, "removed": 0, "to_collect": {},
                  "errors": {}, "skipped": {}})
        total = len(self.companies)
        for done, (ticker, _cik) in enumerate(self.companies, 1):
            if ticker in self.report.queue:
                s["skipped"][ticker] = "on the collection list"
            else:
                try:
                    outcome, detail = self._bars_since(ticker)
                except Exception as exc:  # one company's bars are its own (a locked database): the others go on
                    outcome, detail = "failed", f"{type(exc).__name__}: {exc}"
                if outcome == "updated":
                    s["updated"][ticker] = detail
                    s["written"] += detail["written"]
                    s["removed"] += detail["removed"]
                    result.stored = True
                elif outcome == "basis":
                    s["to_collect"][ticker] = detail
                    self._queue(ticker, f"bars: {detail}")
                elif outcome == "failed":
                    s["errors"][ticker] = detail
                else:
                    s["skipped"][ticker] = "no stored bars"
                self.out.note(f"bars: {ticker} {outcome}: {detail}")
            self._progress("bars", done, total, f"{len(s['updated'])} updated, {len(s['to_collect'])} to collect, "
                                                f"{len(s['errors'])} errors")
        updated = s["updated"].values()
        s["from"] = min((u["from"] for u in updated), default=None)
        s["to"] = max((u["to"] for u in updated), default=None)
        bench = s["benchmark"] = self._benchmark_bars()
        result.stored = result.stored or bench["outcome"] == "updated"
        asked = total - len(s["skipped"]) + (bench["outcome"] != "skipped")
        failed = len(s["errors"]) + (bench["outcome"] == "failed")
        result.ok = asked == 0 or failed < asked
        if not result.ok:
            result.reason = "every company's bars failed, and the benchmark's"
        benchmark = (f"{BENCHMARK}: {bench['written']} written in place of {bench['removed']}"
                     if bench["outcome"] == "updated" else f"{BENCHMARK} {bench['outcome']} ({bench['reason']})")
        result.headline = (f"{len(s['updated'])} companies' bars updated, {s['written']} written in place of "
                           f"{s['removed']}" + (f", {s['from']} to {s['to']}" if s["from"] else "")
                           + f"; {len(s['to_collect'])} to collect, {len(s['errors'])} errors, "
                             f"{len(s['skipped'])} skipped; {benchmark}")

    def _benchmark_bars(self) -> Dict[str, Any]:
        """The benchmark's bars (BENCHMARK) since its last stored one, as a company's (``_bars_since``: the same
        request, the same check of a bar before its last and the same failures), stored as a collection stores them.
        It is never queued: the collection list is the companies', and the next collection's benchmark fetch
        (``fetch_and_export``) stores the index's whole history again."""
        try:
            outcome, detail = self._bars_since(BENCHMARK)
        except Exception as exc:  # the benchmark's bars are their own: the companies' stand
            outcome, detail = "failed", f"{type(exc).__name__}: {exc}"
        if outcome == "updated":
            bench = {"ticker": BENCHMARK, "outcome": "updated", **detail}
        elif outcome == "none":
            bench = {"ticker": BENCHMARK, "outcome": "skipped",
                     "reason": "no stored bars (a collection's benchmark fetch stores them)"}
        else:
            bench = {"ticker": BENCHMARK, "outcome": "failed", "reason": detail + (
                "; the next collection's benchmark fetch stores its whole history again" if outcome == "basis" else "")}
        self.out.note(f"bars: {BENCHMARK} (the benchmark) {outcome}: {detail}")
        return bench

    def _dividend_adjusted(self, ticker: str) -> bool:
        """Whether *ticker*'s stored bars include Yahoo's dividend-adjusted closes (BUG-075): one flagged so, or any
        bar of a database from before the flag."""
        columns = {row[1] for row in self._rows("PRAGMA table_info(price_bars)")}
        if "dividend_adjusted" not in columns:
            return True
        return bool(self._rows("SELECT 1 FROM price_bars WHERE ticker = ? AND dividend_adjusted LIMIT 1", ticker))

    def _bars_since(self, ticker: str) -> Tuple[str, Any]:
        """*ticker*'s bars from its last stored one on, replaced with Yahoo's (``SQLiteStore.replace_price_bars_from``)
        when they are on the stored bars' basis. Returns ("updated", the days, the rows written and removed and the
        close checked), ("basis", why Yahoo's bars can't be matched to the stored ones), ("failed", why) or ("none",
        None) without stored bars.

        Yahoo is asked from the bar the basis is checked on: the latest stored bar before the last with a close above
        0, of the LOOKBACK before it (from the last, when it is the only one). That bar is final, so Yahoo's close for
        it is the stored one, within BASIS_TOLERANCE, on the same basis. A split or a price factor Yahoo lists after
        the last stored bar puts Yahoo's bars before it on another basis, whatever its size; bars stored
        dividend-adjusted (BUG-075) can't be matched at all.

        Two things fail the company instead, its bars kept and nothing queued (code review 19, I-2): no such bar, as a
        full collection would store the same bars and the company would be queued again every day; and an answer
        without a bar, which is how yfinance answers a timeout, throttling or a ticker it no longer serves ("possibly
        delisted; no timezone found"), not a basis of its own."""
        tail = self._rows("SELECT substr(date, 1, 10), close FROM price_bars WHERE ticker = ? "
                          "ORDER BY date DESC LIMIT ?", ticker, LOOKBACK + 1)
        if not tail:
            return "none", None
        if self._dividend_adjusted(ticker):
            return "basis", ("its stored bars are Yahoo's dividend-adjusted closes (BUG-075), which only a full "
                             "collection replaces")
        last_day, earlier = tail[0][0], tail[1:]
        before = next(((day, c) for day, c in earlier if c is not None and c > 0), None)
        if earlier and before is None:
            return "failed", (f"none of its {len(earlier)} latest stored bars before the last ({last_day}) has a "
                              f"close above 0 to check Yahoo's basis on; the stored bars stay")
        start = before[0] if before is not None else last_day
        answer = self.services.yahoo.fetch_price_bars_since(ticker, start)
        if "error" in answer:
            return "failed", str(answer["error"])
        bars = [b for b in answer.get("bars") or [] if b.get("date")]
        if not bars:
            return "failed", (f"Yahoo gave no bars from {start} (yfinance's answer to a timeout, throttling or a "
                              f"ticker Yahoo no longer serves); the stored bars stay")
        moved = [x for x in answer.get("splits") or []
                 if str(x.get("date"))[:10] > last_day and x.get("ratio") and float(x["ratio"]) != 1.0]
        if moved:
            return "basis", (f"Yahoo lists {describe_splits(moved)} after its last stored bar ({last_day}), so "
                             f"Yahoo's bars before it are on another basis")
        checked = None
        if before is not None:
            theirs = next((b.get("close") for b in bars if str(b["date"])[:10] == before[0]), None)
            if theirs is None:
                return "basis", (f"Yahoo's bars from {before[0]} give no close for that day, so their basis can't be "
                                 f"checked against the stored bars'")
            if abs(theirs / before[1] - 1) > BASIS_TOLERANCE:
                return "basis", (f"Yahoo's close for {before[0]} is {theirs:.6g} and the stored one {before[1]:.6g}: "
                                 f"Yahoo's prices are on another basis now")
            checked = {"day": before[0], "stored": before[1], "yahoo": theirs}
        new = [b for b in bars if str(b["date"])[:10] >= last_day]
        if not new:
            return "failed", f"Yahoo gave no bars from {last_day}, the last stored one's day; the stored bars stay"
        removed, written = self.store.replace_price_bars_from(ticker, last_day, new)
        return "updated", {"from": str(new[0]["date"])[:10], "to": str(new[-1]["date"])[:10], "written": written,
                           "removed": removed, "checked": checked}

    # ---- sec --------------------------------------------------------------

    def _sec(self, result: StepResult) -> None:
        """Every company's submissions feed: each report (``is_report``) filed on or after the day of its last
        collection with the SEC puts it on the list. The day is the collector's own (``collection_runs``), which east
        of New York runs ahead of the SEC's: a report filed on it may have come after the collection, so it counts."""
        s = result.summary
        s.update({"feeds": 0, "new_filings": {}, "no_cik": [], "errors": {}})
        total = len(self.companies)
        for done, (ticker, cik) in enumerate(self.companies, 1):
            if not cik:
                s["no_cik"].append(ticker)
            else:
                try:
                    self._filings_of(ticker, cik, s)
                except Exception as exc:  # one company's feed is its own: the others go on
                    s["errors"][ticker] = f"{type(exc).__name__}: {exc}"
                    self.out.note(f"sec: {ticker} failed: {s['errors'][ticker]}")
            self._progress("sec", done, total, f"{len(s['new_filings'])} with new reports, {len(s['errors'])} errors")
        asked = total - len(s["no_cik"])
        result.ok = asked == 0 or len(s["errors"]) < asked
        if not result.ok:
            result.reason = "every feed failed"
        result.headline = (f"{s['feeds']} feeds read, {sum(len(v) for v in s['new_filings'].values())} new reports "
                           f"at {len(s['new_filings'])} companies, {len(s['no_cik'])} without a CIK, "
                           f"{len(s['errors'])} errors")

    def _filings_of(self, ticker: str, cik: str, s: Dict[str, Any]) -> None:
        filings = self.services.sec.recent_filings(cik)
        if filings is None:
            raise LookupError(f"the SEC has no submissions feed for CIK {cik}")
        s["feeds"] += 1
        last = self._rows("SELECT max(collected_at) FROM collection_runs WHERE ticker = ? "
                          "AND data_sources LIKE '%sec_edgar%'", ticker)
        since = str(last[0][0])[:10] if last and last[0][0] else None
        new = [f for f in filings
               if is_report(f.get("form")) and (since is None or str(f.get("filingDate") or "")[:10] >= since)]
        if not new:
            return
        s["new_filings"][ticker] = [{"form": f.get("form"), "filed": str(f.get("filingDate") or "")[:10],
                                     "accession": f.get("accessionNumber")} for f in new]
        for f in s["new_filings"][ticker]:
            self._queue(ticker, f"sec: {f['form']} filed {f['filed']} ({f['accession']})")
        self.out.note(f"sec: {ticker} to collect: " + ", ".join(
            f"{f['form']} filed {f['filed']}" for f in s["new_filings"][ticker]))

    # ---- collect ----------------------------------------------------------

    def _last_run(self, ticker: str) -> Optional[Tuple[Any, ...]]:
        found = self._rows("SELECT collected_at, warning_count, error_count FROM collection_runs WHERE ticker = ? "
                           "ORDER BY collected_at DESC LIMIT 1", ticker)
        return found[0] if found else None

    def _collect(self, result: StepResult) -> None:
        """A full collection of the list; each company's outcome is the collection run it stored, if any. A company
        it didn't store fails the step (code review 19, I-1: the store keeps each company to its own savepoint, so a
        collection can store part of the list)."""
        tickers = list(self.report.queue)
        s = result.summary
        s.update({"tickers": tickers, "why": {t: list(self.report.queue[t]) for t in tickers}, "collected": {},
                  "not_stored": []})
        before = {t: self._last_run(t) for t in tickers}
        self.out.say(f"collect: {len(tickers)} companies: {', '.join(tickers)}")
        try:
            s["fetch"] = self.services.collect(tickers, lambda line: self.out.say(f"collect: {line}"))
        finally:  # what it stored counts, should it then have raised
            for ticker in tickers:
                after, earlier = self._last_run(ticker), before[ticker]
                if after is not None and (earlier is None or after[0] != earlier[0]):
                    s["collected"][ticker] = {"collected_at": after[0], "warnings": after[1], "errors": after[2]}
                    result.stored = True
                else:
                    s["not_stored"].append(ticker)
        result.ok = not s["not_stored"]
        if not result.ok:
            result.reason = ("the collection stored none of them" if not s["collected"] else
                             f"the collection didn't store {len(s['not_stored'])} of the {len(tickers)}: "
                             f"{', '.join(s['not_stored'])}")
        clean = sum(1 for c in s["collected"].values() if not c["errors"])
        result.headline = (f"{len(s['collected'])} of {len(tickers)} collected ({clean} without errors, "
                           f"{len(s['collected']) - clean} with errors)"
                           + (f"; not stored: {', '.join(s['not_stored'])}" if s["not_stored"] else ""))

    # ---- insiders, gurus, market, recompute -------------------------------

    def _insiders(self, result: StepResult) -> None:
        """M3's insiders for every company with a CIK; the collector's line for each company goes to the log."""
        tickers = [t for t, cik in self.companies if cik]
        done = [0]

        def log(line: str) -> None:
            self.out.note(f"insiders: {line}")
            done[0] += 1
            self._progress("insiders", done[0], len(tickers))

        got = self.services.insiders(tickers, log)
        s = result.summary
        s.update({"companies": len(got), "listed": sum(v.get("listed", 0) for v in got.values()),
                  "stored": sum(v.get("stored", 0) for v in got.values()),
                  "other_issuer": sum(v.get("other_issuer", 0) for v in got.values()),
                  "errors": {t: v["errors"] for t, v in got.items() if v.get("errors")}})
        result.stored = s["stored"] > 0
        result.ok = not got or len(s["errors"]) < len(got)
        if not result.ok:
            result.reason = "every company's filings failed"
        result.headline = (f"{s['stored']} Form 4 filings stored for {len(got)} companies, {len(s['errors'])} with "
                           f"errors")

    def _gurus(self, result: StepResult) -> None:
        """M3's gurus: their 13F filings, their CUSIPs' tickers and their positions."""
        got = self.services.gurus(lambda line: self.out.say(f"gurus: {line}"))
        filings = got.get("filings") or {}
        cusips = got.get("cusips") or {}
        positions = got.get("positions") or {}
        s = result.summary
        s.update({"gurus": len(filings), "filings_stored": sum(v.get("stored", 0) for v in filings.values()),
                  "checksum_failures": sum(len(v.get("checksum_failures") or []) for v in filings.values()),
                  "cusips": {k: cusips.get(k, 0) for k in ("universe", "openfigi", "none")},
                  "cusip_errors": len(cusips.get("errors") or []),
                  "positions": sum(v.get("positions", 0) for v in positions.values()),
                  "errors": {g: v["errors"] for g, v in filings.items() if v.get("errors")}})
        result.stored = s["filings_stored"] > 0 or s["positions"] > 0
        result.ok = not filings or len(s["errors"]) < len(filings)
        if not result.ok:
            result.reason = "every guru's filings failed"
        result.headline = (f"{s['filings_stored']} 13F filings stored for {len(filings)} gurus, "
                           f"{s['positions']} position rows, {len(s['errors'])} gurus with errors, "
                           f"{s['cusip_errors']} CUSIP errors")

    def _market(self, result: StepResult) -> None:
        """M4's market data, its three sources."""
        got = self.services.market(lambda line: self.out.say(f"market: {line}"))
        stored = [source for source, v in got.items() if v.get("ok")]
        s = result.summary
        s.update({"sources": {source: {k: v.get(k) for k in ("ok", "rows", "latest", "error")}
                              for source, v in got.items()},
                  "stored": len(stored), "failed": {source: v.get("error") for source, v in got.items()
                                                    if not v.get("ok")}})
        result.stored = bool(stored)
        result.ok = not got or bool(stored)
        if not result.ok:
            result.reason = "every source failed"
        result.headline = (f"{len(stored)} of {len(got)} sources stored"
                           + (f"; failed: {', '.join(s['failed'])}" if s["failed"] else ""))

    def _recompute(self, result: StepResult) -> None:
        """Every company's valuations (those of --tickers only, when given), then the ranks."""
        tickers = [t for t, _ in self.companies] if self.subset else None
        valued = self.services.compute(tickers)
        result.summary.update({"tickers": tickers if tickers is not None else "all", "valued": valued})
        result.stored = valued > 0
        result.ok = valued > 0 or not self.companies
        if not result.ok:
            result.reason = "no company was valued"
        result.headline = f"{valued} of {len(self.companies)} companies valued, then ranked"


def run_refresh(db_path: Path, services: Services, output: Output, steps: Optional[Sequence[str]] = None,
                force: bool = False, tickers: Optional[Sequence[str]] = None) -> RunReport:
    """Run the refresh's steps on the database at *db_path* with *services*, saying what it does to *output*: only
    *steps* when given, whatever their cadence; every step with *force*; the per-company steps for *tickers* only when
    given (each in the database). Logs each step in ``refresh_log`` and ends with the summary."""
    return _Run(Path(db_path), services, output, steps, force, tickers).go()


def _steps_list(value: str) -> List[str]:
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in STEPS]
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"unknown steps: {', '.join(unknown) or 'none named'}; the steps are "
                                         f"{', '.join(STEPS)}")
    return names


def _tickers_list(value: str) -> List[str]:
    try:
        return [normalize_ticker(name) for name in value.split(",") if name.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stock-data refresh",
        description="Keep the database current: the quotes and the new daily bars every day, the SEC's new filings "
                    "every week (each company with one collected in full), the insiders, the gurus and the market "
                    "data, then the valuations (docs/superpowers/specs/2026-10-25-universe-automation-design.md).",
        epilog="Steps, in order: quotes (daily), bars (daily), sec (weekly), collect (as needed), insiders (weekly), "
               "gurus (weekly), market (daily), recompute (after a step stored something). Exit 0 when every step "
               "ran or was skipped, 1 when one failed, 2 without the database, 3 when another refresh is running.")
    parser.add_argument("--steps", type=_steps_list, metavar="LIST",
                        help=f"Run only these steps, whatever their cadence (comma-separated: {', '.join(STEPS)})")
    parser.add_argument("--force", action="store_true", help="Run every step, whatever its cadence")
    parser.add_argument("--tickers", type=_tickers_list, metavar="LIST",
                        help="Only these of the database's companies (comma-separated; for testing)")
    parser.add_argument("--db", type=Path, default=None, metavar="PATH",
                        help="The SQLite DB, which must exist (default: <output-dir>/output/stock.db)")
    parser.add_argument("--output-dir", type=Path, default=Path("data"), metavar="DIR",
                        help="The base output directory: the collection's exports under output/, the run's log under "
                             "logs/ (default: data)")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"), metavar="DIR",
                        help="The SEC's, the ownership filings' and the market data's cache (default: data/cache)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None,
         make_services: Callable[[Path, Path, Path], Services] = real_services) -> int:
    """``stock-data refresh``: the run on the database --db names (else the output dir's), with the services
    *make_services* builds from the database, the output dir and the cache dir."""
    args = parse_args(argv)
    db_path = Path(args.db or StorageConfig(base_dir=args.output_dir).database_path)
    if not db_path.is_file():
        print(f"No database at {db_path}: --db must name an existing database (none is created).", file=sys.stderr)
        return EXIT_NO_DATABASE
    try:
        read_companies(db_path, args.tickers)
    except ValueError as exc:
        print(f"stock-data refresh: {exc}", file=sys.stderr)
        return EXIT_NO_DATABASE
    except sqlite3.Error as exc:
        print(f"Cannot read the companies in {db_path}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    started = datetime.now()
    output = Output(log_path=Path(args.output_dir) / "logs" / f"refresh-{started:%Y-%m-%d}.log")
    lock = RefreshLock(lock_path(db_path))
    try:
        try:
            lock.acquire(started.isoformat(timespec="seconds"))
        except LockHeld as held:
            output.say(f"Not started: {held}. One refresh runs at a time.")
            return EXIT_RUNNING
        services = make_services(db_path, Path(args.output_dir), Path(args.cache_dir))
        try:
            report = run_refresh(db_path, services, output, steps=args.steps, force=args.force,
                                 tickers=args.tickers)
        except KeyboardInterrupt:
            output.say("Interrupted: the steps that finished are logged.")
            return 130
        except Exception:
            output.say("The refresh stopped on an error (the traceback is in the log):")
            for line in traceback.format_exc().splitlines():
                output.note(line)
            raise
        finally:
            services.close()
        return report.exit_code
    finally:
        lock.release()
        output.close()


if __name__ == "__main__":
    sys.exit(main())
