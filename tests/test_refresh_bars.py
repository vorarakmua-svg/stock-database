"""``stock-data refresh``'s bars step (``src/refresh.py``; docs/superpowers/specs/
2026-10-25-universe-automation-design.md, "stock-data refresh"): ``YahooHandler.fetch_price_bars_since`` (one
``history(start=...)`` request with a collection's
parameters and bar shape, and the split factors Yahoo lists in that range), ``SQLiteStore.replace_price_bars_from``
(a ticker's bars from a day on replaced in one transaction) and the step itself: each company's bars from its last
stored one on (that bar may have been partial), asked from the latest bar before it with a close above 0, whose final
close must be Yahoo's on the stored bars' basis; a company whose basis can't be matched goes on the collection list
instead, and one already on it is left to the collection. An answer without bars, and a company without such a bar,
are that company's failure, never queued (code review 19, I-2)."""
import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

import src.refresh
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers import yahoo_handler as yh
from src.fetchers.yahoo_handler import YahooHandler
from src.refresh import BASIS_TOLERANCE
from tests import refresh_fakes as rf

#: Yahoo's answer to a throttled or failed request, or for a ticker it no longer serves: yfinance's empty history.
EMPTY = {"bars": [], "splits": []}

# ---------------------------------------------------------------------------
# YahooHandler.fetch_price_bars_since
# ---------------------------------------------------------------------------


def _frame(days, closes, splits=None):
    """yfinance's daily history with actions (auto_adjust=False): its exchange-time index, OHLC, Adj Close, Volume,
    Dividends and Stock Splits (0 on a day without one)."""
    index = pd.DatetimeIndex(pd.to_datetime(list(days))).tz_localize("America/New_York")
    return pd.DataFrame({
        "Open": [c - 0.5 for c in closes], "High": [c + 1.0 for c in closes], "Low": [c - 1.0 for c in closes],
        "Close": closes, "Adj Close": [c * 0.98 for c in closes], "Volume": [1000.0] * len(closes),
        "Dividends": [0.0] * len(closes), "Stock Splits": splits or [0.0] * len(closes),
    }, index=index)


class _HistoryTicker:
    """A yf.Ticker whose history() returns *frame* whatever it is asked, recording each call's arguments."""

    frame = None
    calls = []

    def __init__(self, ticker):
        self.ticker = ticker

    def history(self, **kwargs):
        _HistoryTicker.calls.append((self.ticker, kwargs))
        if isinstance(_HistoryTicker.frame, Exception):
            raise _HistoryTicker.frame
        return _HistoryTicker.frame


@pytest.fixture
def history(monkeypatch):
    _HistoryTicker.calls = []
    monkeypatch.setattr(yh.yf, "Ticker", _HistoryTicker)
    return _HistoryTicker


def test_the_bars_since_a_day_are_one_history_request_with_a_collections_parameters(history):
    history.frame = _frame(["2026-10-20", "2026-10-21"], [10.0, 11.0])
    YahooHandler(rate_limit_delay=0).fetch_price_bars_since("AAA", "2026-10-19")
    assert history.calls == [("AAA", {"start": "2026-10-19", "interval": "1d", "auto_adjust": False})]


def test_the_bars_have_a_collections_shape(history):
    # The market's closes (not the Adj Close), ascending, a missing value as None: as _price_bars_and_error has them.
    history.frame = _frame(["2026-10-22", "2026-10-20", "2026-10-21"], [12.0, 10.0, float("nan")])
    handler = YahooHandler(rate_limit_delay=0)
    got = handler.fetch_price_bars_since("AAA", "2026-10-20")
    assert got["bars"] == handler._price_bars_and_error(_HistoryTicker("AAA"))[0]
    assert [b["date"] for b in got["bars"]] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    assert got["bars"][0] == {"date": "2026-10-20", "open": 9.5, "high": 11.0, "low": 9.0, "close": 10.0,
                              "volume": 1000.0}
    assert got["bars"][1]["close"] is None
    assert got["splits"] == []


def test_the_splits_yahoo_lists_in_the_range_come_with_the_bars(history):
    history.frame = _frame(["2026-10-20", "2026-10-21", "2026-10-22"], [10.0, 5.0, 5.1], splits=[0.0, 2.0, 0.0])
    got = YahooHandler(rate_limit_delay=0).fetch_price_bars_since("AAA", "2026-10-20")
    assert got["splits"] == [{"date": "2026-10-21", "ratio": 2.0}]


def test_a_failed_request_is_an_error_not_a_raise(history):
    history.frame = RuntimeError("Too Many Requests")
    assert YahooHandler(rate_limit_delay=0).fetch_price_bars_since("AAA", "2026-10-20") == {
        "error": "Too Many Requests"}


def test_an_empty_history_is_no_bars(history):
    history.frame = pd.DataFrame()
    assert YahooHandler(rate_limit_delay=0).fetch_price_bars_since("AAA", "2026-10-20") == {"bars": [], "splits": []}


def test_the_request_waits_for_the_rate_limiter(history, monkeypatch):
    history.frame = _frame(["2026-10-20"], [10.0])
    handler = YahooHandler(rate_limit_delay=0)
    waits = []
    monkeypatch.setattr(handler.rate_limiter, "wait", lambda: waits.append(1))
    handler.fetch_price_bars_since("AAA", "2026-10-20")
    assert waits == [1]


def test_a_collections_bars_are_unchanged(history):
    # The collection's own request, its shape shared with the new one.
    history.frame = _frame(["2026-10-21", "2026-10-20"], [11.0, 10.0])
    bars, error = YahooHandler(rate_limit_delay=0)._price_bars_and_error(_HistoryTicker("AAA"))
    assert error is None and [b["close"] for b in bars] == [10.0, 11.0]
    assert history.calls == [("AAA", {"period": "max", "interval": "1d", "auto_adjust": False})]


# ---------------------------------------------------------------------------
# SQLiteStore.replace_price_bars_from
# ---------------------------------------------------------------------------


def _bars(db, ticker="AAA"):
    return rf.rows(db, "SELECT date, close, dividend_adjusted FROM price_bars WHERE ticker = ? ORDER BY date", ticker)


def test_the_bars_from_a_day_on_are_replaced(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    others = _bars(db, "BBB")
    new = [dict(rf.bar("AAA", "2026-10-20"), close=200.0), rf.bar("AAA", "2026-10-21"), rf.bar("AAA", "2026-10-22")]
    assert SQLiteStore(db).replace_price_bars_from("AAA", "2026-10-20", new) == (1, 3)
    stored = _bars(db)
    assert [d for d, _, _ in stored] == list(rf.STORED_DAYS) + ["2026-10-21", "2026-10-22"]
    assert stored[3] == ("2026-10-19", rf.close("AAA", "2026-10-19"), None)
    assert stored[4] == ("2026-10-20", 200.0, None)
    assert all(flag is None for _, _, flag in stored)
    assert _bars(db, "BBB") == others


def test_the_replaced_bars_are_market_prices(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", adjusted=["AAA"])
    SQLiteStore(db).replace_price_bars_from("AAA", "2026-10-20", [rf.bar("AAA", "2026-10-20")])
    assert [flag for _, _, flag in _bars(db)] == [1, 1, 1, 1, None]


def test_a_failed_replacement_changes_nothing(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    before = _bars(db)
    with pytest.raises(sqlite3.Error):
        SQLiteStore(db).replace_price_bars_from(
            "AAA", "2026-10-20", [rf.bar("AAA", "2026-10-20"), dict(rf.bar("AAA", "2026-10-21"), close=[1])])
    assert _bars(db) == before


def test_a_bar_before_the_day_is_refused(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    before = _bars(db)
    with pytest.raises(ValueError, match="2026-10-19"):
        SQLiteStore(db).replace_price_bars_from("AAA", "2026-10-20", [rf.bar("AAA", "2026-10-19")])
    assert _bars(db) == before


# ---------------------------------------------------------------------------
# The bars step
# ---------------------------------------------------------------------------


def _step(report, step="bars"):
    return report.result(step)


def test_each_companys_bars_are_asked_from_the_bar_before_its_last_and_replaced_from_its_last(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, revised={"2026-10-20": 0.25})})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert yahoo.bar_calls == [(t, "2026-10-19") for t in rf.TICKERS]
    stored = _bars(db)
    assert [d for d, _, _ in stored] == list(rf.STORED_DAYS + rf.NEW_DAYS)
    # The last stored bar may have been partial: Yahoo's final close replaces it; the one before is left as stored.
    assert stored[4][1] == rf.close("AAA", "2026-10-20") + 0.25
    assert stored[3][1] == rf.close("AAA", "2026-10-19")
    result = _step(report)
    assert (result.ran, result.ok, result.stored) == (True, True, True)
    p = rf.close("AAA", "2026-10-19")
    assert result.summary["updated"]["AAA"] == {"from": "2026-10-20", "to": "2026-10-23", "written": 4, "removed": 1,
                                                "checked": {"day": "2026-10-19", "stored": p, "yahoo": p}}
    assert (result.summary["written"], result.summary["removed"]) == (12, 3)
    assert report.queue == {}


def test_a_close_moved_by_less_than_the_tolerance_is_the_same_basis(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    within = 1 + BASIS_TOLERANCE * 0.8
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, factor=1 / within)})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert "AAA" in _step(report).summary["updated"] and "AAA" not in report.queue


def test_a_close_on_another_basis_puts_the_company_on_the_collection_list(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    beyond = 1 + BASIS_TOLERANCE * 1.2
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, factor=beyond)})
    before = _bars(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert _bars(db) == before
    assert list(report.queue) == ["AAA"]
    assert "2026-10-19" in report.queue["AAA"][0] and "basis" in report.queue["AAA"][0]
    assert "AAA" in _step(report).summary["to_collect"] and "AAA" not in _step(report).summary["updated"]


@pytest.mark.parametrize("factor, moved", [
    (1.01, True),     # SCCO's quarterly stock dividend
    (1 / 1.008, True),  # the same the other way
    (1.002, False),   # a revised close, a fifth of that
])
def test_a_one_percent_move_is_another_basis_and_a_revision_isnt(tmp_path, factor, moved):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, factor=factor)})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert ("AAA" in report.queue) is moved and ("AAA" in _step(report).summary["updated"]) is not moved


def test_a_split_yahoo_lists_after_the_last_stored_bar_puts_the_company_on_the_list(tmp_path):
    # SCCO's 1.01 stock dividend: Yahoo's bars before it fall by 1%, the stored ones don't, whatever the tolerance.
    db = rf.make_db(tmp_path / "stock.db")
    factor = {"date": "2026-10-22", "ratio": 1.001}
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, splits=[factor])})
    before = _bars(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert _bars(db) == before
    assert list(report.queue) == ["AAA"] and "2026-10-22" in report.queue["AAA"][0]


def test_a_split_on_or_before_the_last_stored_bar_with_its_closes_matching_is_already_in_the_stored_bars(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, splits=[
        {"date": "2026-10-20", "ratio": 2.0}])})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert "AAA" in _step(report).summary["updated"] and report.queue == {}


def test_a_one_for_one_entry_is_no_split(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, splits=[
        {"date": "2026-10-22", "ratio": 1.0}])})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert "AAA" in _step(report).summary["updated"] and report.queue == {}


def test_yahoos_answer_without_the_bar_before_the_last_cant_be_checked(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", "2026-10-20")})
    before = _bars(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert _bars(db) == before and list(report.queue) == ["AAA"] and "2026-10-19" in report.queue["AAA"][0]


def test_stored_dividend_adjusted_bars_put_the_company_on_the_list_without_asking_yahoo(tmp_path):
    # Bars stored before BUG-075's fix are Yahoo's dividend-adjusted closes: a collection replaces them all.
    db = rf.make_db(tmp_path / "stock.db", adjusted=["BBB"])
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert [t for t, _ in yahoo.bar_calls] == ["AAA", "CCC"]
    assert list(report.queue) == ["BBB"] and "dividend-adjusted" in report.queue["BBB"][0]


def test_a_company_on_the_collection_list_is_left_to_the_collection(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(quotes={"BBB": rf.quote("BBB", splits=[{"date": "2026-10-22", "ratio": 4.0}])})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["quotes", "bars"])
    assert [t for t, _ in yahoo.bar_calls] == ["AAA", "CCC"]
    assert _step(report).summary["skipped"] == {"BBB": "on the collection list"}
    assert list(report.queue) == ["BBB"]


def test_a_failed_request_keeps_the_stored_bars(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": {"error": "Too Many Requests"}})
    before = _bars(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert _bars(db) == before
    result = _step(report)
    assert result.summary["errors"] == {"AAA": "Too Many Requests"} and result.ok is True
    assert report.queue == {}


def test_an_answer_without_bars_from_the_last_stored_day_keeps_the_stored_bars(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, days=["2026-10-19"])})
    before = _bars(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert _bars(db) == before and "AAA" in _step(report).summary["errors"] and report.queue == {}


def test_a_company_without_stored_bars_is_skipped(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", tickers=["AAA"], days=[])
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert yahoo.bar_calls == [] and _step(report).summary["skipped"] == {"AAA": "no stored bars"}
    assert report.queue == {}


def test_a_company_with_one_stored_bar_is_asked_from_it(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", tickers=["AAA"], days=["2026-10-20"])
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert yahoo.bar_calls == [("AAA", "2026-10-20")]
    assert [d for d, _, _ in _bars(db)] == ["2026-10-20"] + list(rf.NEW_DAYS)
    assert _step(report).summary["updated"]["AAA"]["checked"] is None  # no bar before it to check


def test_the_step_fails_when_every_company_fails(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={t: {"error": "down"} for t in rf.TICKERS})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert (_step(report).ok, _step(report).stored, report.exit_code) == (False, False, 1)


# ---------------------------------------------------------------------------
# An answer without bars, and the bar Yahoo's basis is checked on (code review 19, I-2)
# ---------------------------------------------------------------------------


def test_an_answer_without_any_bars_is_the_companys_failure_and_queues_nothing(tmp_path):
    # yfinance answers a timeout, throttling or "possibly delisted; no timezone found" with an empty history, not an
    # error: that is no basis to collect the company in full on.
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(bars={"AAA": EMPTY})
    before = _bars(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    result = _step(report)
    assert _bars(db) == before and report.queue == {} and result.summary["to_collect"] == {}
    assert result.summary["errors"]["AAA"].startswith("Yahoo gave no bars from 2026-10-19")
    assert sorted(result.summary["updated"]) == ["BBB", "CCC"] and result.ok is True


def test_a_bad_yahoo_day_queues_no_company_and_fails_the_step(tmp_path):
    # Code review 19's empty_bars_queue probe: every quote failed and every bars answer was empty, and the run put
    # every company on the collection list and collected them all in full.
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo(quotes={t: {"error": "Yahoo returned no price"} for t in rf.TICKERS},
                         bars={t: EMPTY for t in rf.TICKERS + ("^GSPC",)})
    collect = rf.FakeCollect(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo, collect=collect), steps=["quotes", "bars", "collect"])
    bars = _step(report)
    assert report.queue == {} and collect.calls == [] and report.result("collect").reason == "nothing to collect"
    assert sorted(bars.summary["errors"]) == list(rf.TICKERS) and bars.summary["to_collect"] == {}
    assert bars.summary["benchmark"]["outcome"] == "failed"
    assert (bars.ok, bars.reason, report.exit_code) == (False, "every company's bars failed, and the benchmark's", 1)


def test_an_answer_with_bars_but_no_close_for_that_day_still_cant_be_checked(tmp_path):
    # Bars, but none of them that day's close: a basis Yahoo can't show, which only a collection matches.
    db = rf.make_db(tmp_path / "stock.db")

    def answer(start):
        got = rf.served("AAA", start)
        got["bars"][0]["close"] = None
        return got

    report = rf.run(db, rf.services(db, yahoo=rf.FakeYahoo(bars={"AAA": answer})), steps=["bars"])
    assert list(report.queue) == ["AAA"] and "give no close for that day" in report.queue["AAA"][0]


@pytest.mark.parametrize("stored", [None, 0.0, -1.0])
def test_a_bar_before_the_last_without_a_close_above_0_is_passed_over_for_the_one_before_it(tmp_path, stored):
    # Such a close queued the company every day: a full collection stores the same bars.
    db = rf.make_db(tmp_path / "stock.db")
    rf.set_closes(db, "AAA", {"2026-10-19": stored})
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert yahoo.bar_calls[0] == ("AAA", "2026-10-16") and report.queue == {}
    p = rf.close("AAA", "2026-10-16")
    assert _step(report).summary["updated"]["AAA"]["checked"] == {"day": "2026-10-16", "stored": p, "yahoo": p}
    stored_bars = _bars(db)
    assert [d for d, _, _ in stored_bars] == list(rf.STORED_DAYS + rf.NEW_DAYS)
    assert stored_bars[3] == ("2026-10-19", stored, None)  # only the bars from the last stored one on are replaced


def test_a_company_without_a_close_above_0_before_its_last_bar_fails_and_isnt_queued(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.set_closes(db, "AAA", {day: None for day in rf.STORED_DAYS[:-1]})
    yahoo = rf.FakeYahoo()
    before = _bars(db)
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert [t for t, _ in yahoo.bar_calls] == ["BBB", "CCC"] and report.queue == {} and _bars(db) == before
    assert _step(report).summary["errors"]["AAA"].startswith(
        "none of its 4 latest stored bars before the last (2026-10-20) has a close above 0")


@pytest.mark.parametrize("beyond, found", [(0, True), (1, False)])
def test_that_bar_is_looked_for_among_the_lookback_bars_before_the_last(tmp_path, beyond, found):
    back = src.refresh.LOOKBACK + beyond  # how many bars before the last the only close above 0 is
    days = [(date(2026, 10, 20) - timedelta(days=n)).isoformat() for n in reversed(range(back + 1))]
    db = rf.make_db(tmp_path / "stock.db", tickers=["AAA"], days=days)
    rf.set_closes(db, "AAA", {day: None for day in days[1:-1]})
    yahoo = rf.FakeYahoo(bars={"AAA": lambda start: rf.served("AAA", start, days=days + list(rf.NEW_DAYS))})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert report.queue == {}
    if found:
        assert yahoo.bar_calls == [("AAA", days[0])] and "AAA" in _step(report).summary["updated"]
    else:
        assert yahoo.bar_calls == [] and "AAA" in _step(report).summary["errors"]


# ---------------------------------------------------------------------------
# The benchmark (^GSPC), which the GP tab compares a stock against
# ---------------------------------------------------------------------------


def test_the_benchmarks_bars_are_refreshed_too_whatever_tickers_names(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"], tickers=["AAA"])
    assert yahoo.bar_calls == [("AAA", "2026-10-19"), ("^GSPC", "2026-10-19")]
    stored = _bars(db, "^GSPC")
    assert [d for d, _, _ in stored] == list(rf.STORED_DAYS + rf.NEW_DAYS)
    assert all(flag is None for _, _, flag in stored)  # stored as a collection stores its bars
    p = rf.close("^GSPC", "2026-10-19")
    result = _step(report)
    assert result.summary["benchmark"] == {"ticker": "^GSPC", "outcome": "updated", "from": "2026-10-20",
                                           "to": "2026-10-23", "written": 4, "removed": 1,
                                           "checked": {"day": "2026-10-19", "stored": p, "yahoo": p}}
    assert "^GSPC" not in result.summary["updated"] and "^GSPC: 4 written in place of 1" in result.headline
    assert rf.rows(db, "SELECT count(*) FROM companies WHERE ticker = '^GSPC'") == [(0,)]
    assert (result.ok, result.stored, report.queue) == (True, True, {})


@pytest.mark.parametrize("factor", [1.01, 2.0])
def test_the_benchmark_on_another_basis_is_left_as_stored_and_never_queued(tmp_path, factor):
    # The collection list is the companies'; a collection's benchmark fetch stores the index's whole history again.
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo(bars={"^GSPC": lambda start: rf.served("^GSPC", start, factor=factor)})
    before = _bars(db, "^GSPC")
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert _bars(db, "^GSPC") == before and report.queue == {}
    bench = _step(report).summary["benchmark"]
    assert bench["outcome"] == "failed" and "2026-10-19" in bench["reason"]
    assert bench["reason"].endswith("; the next collection's benchmark fetch stores its whole history again")
    assert _step(report).ok is True


def test_the_benchmark_without_stored_bars_is_skipped_with_the_reason(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert "^GSPC" not in [t for t, _ in yahoo.bar_calls]
    assert _step(report).summary["benchmark"] == {
        "ticker": "^GSPC", "outcome": "skipped",
        "reason": "no stored bars (a collection's benchmark fetch stores them)"}
    assert "^GSPC skipped" in _step(report).headline


def test_the_benchmarks_answer_without_bars_is_its_failure_not_another_basis(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo(bars={"^GSPC": EMPTY})
    before = _bars(db, "^GSPC")
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    bench = _step(report).summary["benchmark"]
    assert _bars(db, "^GSPC") == before and bench["outcome"] == "failed"
    assert bench["reason"].startswith("Yahoo gave no bars from 2026-10-19") and "whole history" not in bench["reason"]
    assert (_step(report).ok, report.queue) == (True, {})


@pytest.mark.parametrize("answer, reason", [({"error": "Too Many Requests"}, "Too Many Requests"),
                                            (RuntimeError("socket closed"), "RuntimeError: socket closed")])
def test_a_failed_benchmark_request_keeps_its_bars(tmp_path, answer, reason):
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo(bars={"^GSPC": answer})
    before = _bars(db, "^GSPC")
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert _bars(db, "^GSPC") == before
    assert _step(report).summary["benchmark"] == {"ticker": "^GSPC", "outcome": "failed", "reason": reason}
    assert _step(report).ok is True  # the companies' bars were refreshed


def test_the_benchmarks_bars_alone_are_something_stored(tmp_path):
    # Every company is on the collection list (a split each), so only the benchmark's bars are written.
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo(quotes={t: rf.quote(t, splits=[{"date": "2026-10-22", "ratio": 2.0}]) for t in rf.TICKERS})
    result = _step(rf.run(db, rf.services(db, yahoo=yahoo), steps=["quotes", "bars"]))
    assert result.summary["updated"] == {} and result.summary["benchmark"]["outcome"] == "updated"
    assert (result.ok, result.stored) == (True, True)


def test_the_benchmark_updated_is_one_item_that_didnt_fail(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo(bars={t: {"error": "down"} for t in rf.TICKERS})
    result = _step(rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"]))
    assert result.summary["benchmark"]["outcome"] == "updated" and result.ok is True


def test_the_step_fails_when_the_companies_and_the_benchmark_all_fail(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", benchmark=True)
    yahoo = rf.FakeYahoo(bars={t: {"error": "down"} for t in rf.TICKERS + ("^GSPC",)})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["bars"])
    assert (_step(report).ok, _step(report).stored, report.exit_code) == (False, False, 1)


def test_the_bars_step_is_daily(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "bars", timedelta(hours=19, minutes=59))
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo))
    assert yahoo.bar_calls == [] and _step(report).ran is False
