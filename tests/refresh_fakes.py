"""Fakes and a small database for ``stock-data refresh``'s tests (``src/refresh.py``): every service a step uses
(``refresh.Services``) without the network or a sleep, and three companies last collected with Yahoo and the SEC on
2026-10-21, each with five daily bars to 2026-10-20 and that collection's snapshot."""
import copy
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.refresh import Output, Services, run_refresh

TICKERS = ("AAA", "BBB", "CCC")
#: The benchmark the collection stores beside the companies' bars.
BENCHMARK = "^GSPC"
#: The stored daily bars' days, and the days since that Yahoo serves.
STORED_DAYS = ("2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20")
NEW_DAYS = ("2026-10-21", "2026-10-22", "2026-10-23")
#: Each company's last collection (Yahoo and the SEC), naive local as the collector writes it.
COLLECTED = datetime(2026, 10, 21, 9, 30)
#: The run's clock.
NOW = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)


def cik_of(ticker: str) -> str:
    return f"{ord(ticker[0]) - ord('A') + 1:010d}"


def close(ticker: str, day: str) -> float:
    """The market close of *ticker* on *day*: the same for the stored bars and Yahoo's, unless a test moves it."""
    return 100.0 + 10 * (ord(ticker[0]) - ord("A")) + int(day[8:]) / 2


def bar(ticker: str, day: str, factor: float = 1.0) -> Dict[str, Any]:
    """A daily bar as a collection stores it, its prices divided by *factor* (a split's or a price factor's)."""
    c = close(ticker, day) / factor
    return {"date": day, "open": c - 0.5, "high": c + 1.0, "low": c - 1.0, "close": c,
            "volume": 1000.0 + int(day[8:])}


def make_db(path: Path, tickers: Sequence[str] = TICKERS, no_cik: Sequence[str] = (),
            adjusted: Sequence[str] = (), days: Sequence[str] = STORED_DAYS, benchmark: bool = False) -> Path:
    """A database of *tickers*, each collected on COLLECTED with Yahoo and the SEC: its CIK (none for *no_cik*), the
    snapshot priced at its last close, and its bars on *days* (flagged Yahoo's dividend-adjusted closes for
    *adjusted*, as bars stored before BUG-075's fix are); with *benchmark*, ^GSPC's bars on *days* too, stored as a
    collection stores them (``export_benchmark_bars``)."""
    stocks = []
    for ticker in tickers:
        stock = StockData(ticker=ticker, cik=None if ticker in no_cik else cik_of(ticker),
                          company_name=f"{ticker} Corp", sector_class="general", collected_at=COLLECTED)
        stock.market_data = {"current_price": close(ticker, days[-1]) if days else 100.0, "market_cap": 1e9}
        stock.price_bars = [bar(ticker, day) for day in days]
        stock.add_source("yahoo_finance")
        stock.add_source("sec_edgar")
        stocks.append(stock)
    assert SQLiteStore(path).export(stocks) == path
    if benchmark:
        SQLiteStore(path).export_benchmark_bars(BENCHMARK, [bar(BENCHMARK, day) for day in days])
    if adjusted:
        conn = sqlite3.connect(path)
        conn.executemany("UPDATE price_bars SET dividend_adjusted = 1 WHERE ticker = ?", [(t,) for t in adjusted])
        conn.commit()
        conn.close()
    return path


def rows(db: Path, sql: str, *params: Any) -> List[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def set_closes(db: Path, ticker: str, closes: Dict[str, Optional[float]]) -> None:
    """Store *ticker*'s close of each day in *closes* (None for a bar stored without one)."""
    conn = sqlite3.connect(db)
    conn.executemany("UPDATE price_bars SET close = ? WHERE ticker = ? AND substr(date, 1, 10) = ?",
                     [(c, ticker, day) for day, c in closes.items()])
    conn.commit()
    conn.close()


def quote(ticker: str, price: Optional[float] = None, splits: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Yahoo's quote-only answer (``YahooHandler.fetch_quote``) with its split history."""
    return {"market_data": {"current_price": price if price is not None else close(ticker, NEW_DAYS[-1]),
                            "market_cap": 1e9},
            "valuation": {"pe_trailing": 20.0}, "shareholders": {"shares_outstanding": 1e7},
            "analyst_estimates": {"target_price_mean": 150.0}, "splits": list(splits)}


def served(ticker: str, start: str, factor: float = 1.0, splits: Sequence[Dict[str, Any]] = (),
           revised: Optional[Dict[str, float]] = None,
           days: Sequence[str] = STORED_DAYS + NEW_DAYS) -> Dict[str, Any]:
    """Yahoo's daily bars from *start* (``YahooHandler.fetch_price_bars_since``): the stored days' closes and the new
    days', all divided by *factor*, a day's close moved by *revised* (a partial bar's final close), and *splits*."""
    bars = []
    for day in days:
        if day >= start:
            b = bar(ticker, day, factor)
            if revised and day in revised:
                b["close"] += revised[day]
            bars.append(b)
    return {"bars": bars, "splits": list(splits)}


class FakeYahoo:
    """``YahooHandler``'s two refresh calls: *quotes* and *bars* per ticker (an answer, an Exception to raise, or for
    bars a callable of the start day); a ticker not named gets a plain quote, and the stored bars and the new days."""

    def __init__(self, quotes: Optional[Dict[str, Any]] = None, bars: Optional[Dict[str, Any]] = None) -> None:
        self.quotes = quotes or {}
        self.bars = bars or {}
        self.quote_calls: List[str] = []
        self.bar_calls: List[tuple] = []

    def fetch_quote(self, ticker: str) -> Dict[str, Any]:
        self.quote_calls.append(ticker)
        got = self.quotes.get(ticker)
        if isinstance(got, Exception):
            raise got
        return copy.deepcopy(got) if got is not None else quote(ticker)

    def fetch_price_bars_since(self, ticker: str, start: str) -> Dict[str, Any]:
        self.bar_calls.append((ticker, start))
        got = self.bars.get(ticker)
        if callable(got):
            got = got(start)
        if isinstance(got, Exception):
            raise got
        return copy.deepcopy(got) if got is not None else served(ticker, start)


def filing(form: str, filed: str, accession: Optional[str] = None) -> Dict[str, Any]:
    """A filing as ``SECHandler.get_submissions`` lists it."""
    return {"accessionNumber": accession or f"0000000000-26-{form.replace('/', '')}-{filed}", "form": form,
            "filingDate": filed, "reportDate": "2026-09-30", "primaryDocument": "doc.htm"}


class FakeSEC:
    """``FreshSECHandler.recent_filings``: *filings* per CIK (none for a CIK not named), *errors* per CIK raised, and
    None (no feed) for the CIKs in *missing*."""

    def __init__(self, filings: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 errors: Optional[Dict[str, Exception]] = None, missing: Sequence[str] = ()) -> None:
        self.filings = filings or {}
        self.errors = errors or {}
        self.missing = set(missing)
        self.calls: List[str] = []

    def recent_filings(self, cik: str) -> Optional[List[Dict[str, Any]]]:
        self.calls.append(cik)
        if cik in self.errors:
            raise self.errors[cik]
        if cik in self.missing:
            return None
        return copy.deepcopy(self.filings.get(cik, []))


class FakeCollect:
    """A full collection: records each call's tickers and stores a collection run for each but *missing* (at
    *collected_at*, with *errors* per ticker), logging the collector's "Completed" line for each; it raises *raises*
    before storing anything, *raise_after* after."""

    def __init__(self, db: Path, missing: Sequence[str] = (), errors: Optional[Dict[str, int]] = None,
                 collected_at: str = "2026-10-25T12:00:00", raises: Optional[BaseException] = None,
                 raise_after: Optional[Exception] = None) -> None:
        self.db = db
        self.missing = set(missing)
        self.errors = errors or {}
        self.collected_at = collected_at
        self.raises = raises
        self.raise_after = raise_after
        self.calls: List[List[str]] = []

    def __call__(self, tickers: List[str], log: Callable[[str], None]) -> Dict[str, Any]:
        self.calls.append(list(tickers))
        if self.raises is not None:
            raise self.raises
        conn = sqlite3.connect(self.db)
        for ticker in tickers:
            if ticker in self.missing:
                continue
            conn.execute("INSERT INTO collection_runs (ticker, collected_at, data_sources, warning_count, error_count) "
                         "VALUES (?, ?, ?, ?, ?)", (ticker, self.collected_at, "yahoo_finance, sec_edgar", 2,
                                                    self.errors.get(ticker, 0)))
            log(f"Completed {ticker}: sources=['yahoo_finance', 'sec_edgar'], warnings=2, "
                f"errors={self.errors.get(ticker, 0)}")
        conn.commit()
        conn.close()
        if self.raise_after is not None:
            raise self.raise_after
        done = [t for t in tickers if t not in self.missing]
        return {"tickers_requested": len(tickers), "tickers_fetched": len(tickers),
                "successful": sum(1 for t in done if not self.errors.get(t)),
                "with_warnings": len(done), "with_errors": sum(1 for t in done if self.errors.get(t)),
                "export_paths": {"sqlite": [self.db]}, "duration_seconds": 1.5}


class Recorder:
    """A service that records each call's arguments and returns *result* (called with them when callable), or
    raises *raises*."""

    def __init__(self, result: Any = None, raises: Optional[Exception] = None) -> None:
        self.result = result
        self.raises = raises
        self.calls: List[tuple] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises
        return self.result(*args) if callable(self.result) else copy.deepcopy(self.result)


def insiders_result(tickers: List[str], log: Callable[[str], None]) -> Dict[str, Dict[str, Any]]:
    for ticker in tickers:
        log(f"{ticker}: 2 Form 4 filings since 2023-10-25, 2 stored, 0 of other issuers left out, 0 errors")
    return {t: {"listed": 2, "stored": 2, "other_issuer": 0, "from_predecessor": 0, "errors": []} for t in tickers}


def gurus_result(log: Callable[[str], None]) -> Dict[str, Any]:
    log("Warren Buffett (1067983): 1 13F filings for periods from 2024-07-01, 1 stored, 0 checksum failures, 0 errors")
    return {"filings": {"1067983": {"listed": 1, "stored": 1, "checksum_failures": [], "errors": []}},
            "cusips": {"universe": 3, "openfigi": 1, "none": 0, "errors": []},
            "positions": {"1067983": {"quarters": 8, "positions": 40}}}


def market_result(log: Callable[[str], None], failed: Sequence[str] = ()) -> Dict[str, Dict[str, Any]]:
    summary = {}
    for source in ("fred:NCBEILQ027S", "fred:GDP", "shiller", "treasury"):
        if source in failed:
            summary[source] = {"ok": False, "rows": 0, "series": {}, "latest": None, "error": "OSError: down"}
        else:
            summary[source] = {"ok": True, "rows": 10, "series": {}, "latest": "2026-10-23", "error": None}
        log(f"{source}: {'FAILED' if source in failed else 'stored'}")
    return summary


def services(db: Path, **overrides: Any) -> Services:
    """Every service a fake: Yahoo, the SEC, the collection, the insiders, the gurus, the market data and the
    valuations (each ticker list it is given valued, every company for None), on the clock NOW."""
    parts: Dict[str, Any] = {
        "yahoo": FakeYahoo(), "sec": FakeSEC(), "collect": FakeCollect(db), "insiders": Recorder(insiders_result),
        "gurus": Recorder(gurus_result), "market": Recorder(market_result),
        "compute": Recorder(lambda tickers: len(tickers) if tickers is not None else len(TICKERS)),
        "clock": lambda: NOW,
    }
    parts.update(overrides)
    return Services(**parts)


def run(db: Path, svc: Services, steps: Optional[Sequence[str]] = None, force: bool = False,
        tickers: Optional[Sequence[str]] = None, stream: Optional[io.StringIO] = None):
    """``run_refresh`` with the output in *stream* (a new one by default) and no log file."""
    return run_refresh(db, svc, Output(stream=stream if stream is not None else io.StringIO()), steps=steps,
                       force=force, tickers=tickers)


def succeeded(db: Path, step: str, ago: timedelta, ok: Optional[bool] = True, started: Optional[timedelta] = None,
              run: Optional[timedelta] = None, **summary: Any) -> None:
    """A refresh_log row: *step* finished *ago* before NOW (ok, failed or, None, skipped), having started *started*
    before NOW (*ago* when not given), in a run that started *run* before NOW (none given: the row has no ``run``, as
    one this code didn't write), with *summary*'s counts."""
    logged: Dict[str, Any] = {"seeded": True, **summary}
    if run is not None:
        logged = {"run": (NOW - run).isoformat(), **logged}
    SQLiteStore(db).record_refresh_step(step, (NOW - (ago if started is None else started)).isoformat(),
                                        (NOW - ago).isoformat(), ok, logged)
