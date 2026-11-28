"""BUG-108: a quote refresh reads Yahoo's splits and payments since the last collection, not the whole daily history
(``YahooHandler.fetch_quote``'s *since*, ``events_period``; ``SQLiteStore.quote_events_since``), with BUG-093's and
BUG-107's checks as before. No network: yfinance's requests are answered here."""
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.fetchers import yahoo_handler as yh
from src.fetchers.quote_refresh import refresh_quote
from src.fetchers.yahoo_handler import NO_HISTORY, YahooHandler
from tests import refresh_fakes as rf
from tests.test_yahoo_new_sections import _ChartResponse, _TzCache

TODAY = date(2026, 11, 20)


@pytest.mark.parametrize("days_back, period", [
    (-3, "1mo"), (0, "1mo"), (21, "1mo"), (22, "3mo"), (82, "3mo"), (83, "6mo"), (174, "6mo"), (175, "1y"),
    (358, "1y"), (359, "2y"), (723, "2y"), (724, "5y"), (1819, "5y"), (1820, "10y"), (3645, "10y"), (3646, "max"),
])
def test_the_events_period_reaches_a_week_before_its_start(days_back, period):
    # Yahoo's ranges count back from now; each is taken at its shortest (a month of 28 days, a year of 365) and must
    # reach EVENT_WINDOW_MARGIN_DAYS before the start.
    assert yh.events_period((TODAY - timedelta(days=days_back)).isoformat(), TODAY) == period


def test_no_start_is_the_whole_history():
    assert yh.events_period(None, TODAY) == "max"


def test_a_start_with_a_time_reads_its_day():
    assert yh.events_period("2026-10-30T09:30:00", TODAY) == "1mo"


# ---- fetch_quote through the installed yfinance: the chart request it makes ----

def _charts_asked(monkeypatch) -> List[Dict[str, Any]]:
    """The installed yfinance answering its chart requests with three served days (the rest failing, no network); the
    chart requests' parameters, as asked."""
    import yfinance.cache
    import yfinance.data

    asked: List[Dict[str, Any]] = []

    def get(self, url, *args, **kwargs):
        if "/v8/finance/chart/" in url:
            asked.append(dict(kwargs.get("params") or {}))
            return _ChartResponse(True)
        raise OSError("no network")

    for method in ("get", "cache_get", "post"):
        monkeypatch.setattr(yfinance.data.YfData, method, get)
    monkeypatch.setattr(yfinance.cache, "get_tz_cache", lambda: _TzCache("America/New_York"))
    return asked


def test_a_quote_since_a_day_asks_yahoo_for_a_short_chart(monkeypatch):
    # The refresh asked for the chart from 1927-11-15 (period1=-1329577857) to read the splits; since the last
    # collection a month's chart answers it, splits and payments alike, in one request.
    asked = _charts_asked(monkeypatch)
    YahooHandler(rate_limit_delay=0.0).fetch_quote("AAA", since=(date.today() - timedelta(days=10)).isoformat())
    assert asked and all(p.get("range") == "1mo" and "period1" not in p for p in asked)


def test_a_quote_without_a_start_asks_the_whole_history(monkeypatch):
    asked = _charts_asked(monkeypatch)
    YahooHandler(rate_limit_delay=0.0).fetch_quote("AAA")
    assert asked and all("period1" in p and "range" not in p for p in asked)


# ---- fetch_quote with a ticker double: the window's events read as before ----

class _WindowTicker:
    """yfinance's Ticker for a quote: a price, and the events of the chart of each period asked (``get_splits``,
    ``get_dividends``), served or, with *served* False, yfinance's answer to a failed request."""

    def __init__(self, served: bool = True) -> None:
        self.info = {"currentPrice": 12.0, "regularMarketPrice": 12.0}
        self.served = served
        self.periods: List[str] = []

    def _events(self, name: str, period: str, values: List[float], days: List[str]) -> pd.Series:
        self.periods.append(period)
        if not self.served:
            return pd.Series(dtype=float)
        return pd.Series(values, index=pd.to_datetime(days), name=name)

    def get_splits(self, period: str = "max") -> pd.Series:
        return self._events("Stock Splits", period, [1.05], ["2026-11-02"])

    def get_dividends(self, period: str = "max") -> pd.Series:
        return self._events("Dividends", period, [0.25], ["2026-11-10"])

    @property
    def splits(self) -> pd.Series:
        raise AssertionError("the whole history was asked for")

    @property
    def dividends(self) -> pd.Series:
        raise AssertionError("the whole history was asked for")


def _quote(monkeypatch, ticker: _WindowTicker, since: Optional[str]) -> Dict[str, Any]:
    monkeypatch.setattr(yh.yf, "Ticker", lambda symbol: ticker)
    return YahooHandler(rate_limit_delay=0.0).fetch_quote("ZZZ", since=since)


def test_a_quote_since_a_day_reads_the_windows_splits_and_payments(monkeypatch):
    ticker = _WindowTicker()
    quote = _quote(monkeypatch, ticker, (date.today() - timedelta(days=30)).isoformat())
    assert quote["splits"] == [{"date": "2026-11-02", "ratio": 1.05}]
    assert quote["dividend_history"]["dividend_payments"] == [{"date": "2026-11-10", "amount": 0.25}]
    assert set(ticker.periods) == {"3mo"}


def test_a_windows_split_history_yahoo_didnt_serve_is_still_the_quotes_error(monkeypatch):
    # BUG-107's rule within the window: an unserved split history can't tell a split since the last collection.
    quote = _quote(monkeypatch, _WindowTicker(served=False), (date.today() - timedelta(days=30)).isoformat())
    assert quote == {"error": f"Yahoo's split history unavailable ({NO_HISTORY}), so the quote can't be checked "
                              "for a split since the last collection"}


# ---- the refresh asks from the last collection (SQLiteStore.quote_events_since) ----

class _Recording:
    def __init__(self, answer: Dict[str, Any]) -> None:
        self.answer = answer
        self.since: List[Optional[str]] = []

    def fetch_quote(self, ticker: str, since: Optional[str] = None) -> Dict[str, Any]:
        self.since.append(since)
        return dict(self.answer)


def _execute(db, sql: str, *params: Any) -> None:
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def test_a_refresh_asks_for_yahoos_events_since_the_last_collection(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    handler = _Recording(rf.quote("AAA", 111.0))
    assert refresh_quote(handler, SQLiteStore(db), "AAA", "2026-10-25T05:00:00").outcome == "stored"
    assert handler.since == [rf.COLLECTED.date().isoformat()]


def test_the_events_start_at_the_stored_prices_day_when_it_is_earlier(tmp_path):
    # A later collection whose quote failed kept the earlier price (BUG-102): a split after that price's day counts.
    db = rf.make_db(tmp_path / "stock.db")
    _execute(db, "UPDATE market_snapshots SET collected_at = '2026-09-30T10:00:00-04:00' WHERE ticker = 'AAA'")
    assert SQLiteStore(db).quote_events_since("AAA") == "2026-09-30"


def test_the_events_start_at_the_last_bar_without_a_collection_row(tmp_path):
    # As factors_since_collection reads the last collection with Yahoo: its run row, else its latest bar.
    db = rf.make_db(tmp_path / "stock.db")
    _execute(db, "DELETE FROM collection_runs WHERE ticker = 'AAA'")
    assert SQLiteStore(db).quote_events_since("AAA") == rf.STORED_DAYS[-1]


def test_the_events_start_no_later_than_the_latest_payment_stored(tmp_path):
    # Payments after the latest stored one are all asked for, as the whole history did (a collection whose dividend
    # request failed stored none of its own).
    db = rf.make_db(tmp_path / "stock.db")
    _execute(db, "INSERT INTO dividend_events (ticker, date, amount) VALUES ('AAA', '2026-06-12', 0.5)")
    _execute(db, "INSERT INTO dividend_events (ticker, date, amount) VALUES ('AAA', '2026-03-13', 0.5)")
    assert SQLiteStore(db).quote_events_since("AAA") == "2026-06-12"


def test_a_ticker_without_a_collection_asks_the_whole_history(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    handler = _Recording(rf.quote("ZZZ", 111.0))
    refresh_quote(handler, SQLiteStore(db), "ZZZ", "2026-10-25T05:00:00")
    assert handler.since == [None]
    assert SQLiteStore(tmp_path / "none.db").quote_events_since("AAA") is None
