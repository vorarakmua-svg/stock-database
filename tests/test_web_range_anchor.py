"""BUG-151: a range counts back from the ticker's last stored bar when that is before today.

Every range counted back from today, so a database not collected for a month read its short ranges empty: the user's
copy, last collected 2026-08-04, gave the HP tab's 1M "No price history collected." for all 127 companies on
2026-10-20, beside AAPL's 11,502 bars, the bars CSV a 0-byte 200 and the bars API ``[]``. Now the HP tab, the bars API,
the indicators bundle, compare-bars and the bars CSV count back from the ticker's last bar (compare-bars from the
primary's): 1M is the 30 days up to and including it, YTD from 1 January of its year, MAX all of them. A ticker
without bars, and a last bar dated today or later, keep today's anchor.
"""
from __future__ import annotations

import html
import re
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.exporters.sqlite_store import SQLiteStore
from src.webapp import create_app
from src.webapp.routes import stocks_api

#: STALE's bars: every day from FIRST to LAST (247), the database's last collection.
FIRST, LAST = date(2025, 12, 1), date(2026, 8, 4)
#: EARLY's bars end here; ^GSPC's run on to INDEX_LAST, collected ten days after STALE's.
EARLY_LAST, INDEX_LAST = date(2026, 6, 30), date(2026, 8, 14)

#: Each range's bars counted back from LAST: (count, first date), each ending on LAST.
RANGES = {
    "1M": (31, "2026-07-05"),
    "3M": (92, "2026-05-05"),
    "6M": (183, "2026-02-03"),
    "YTD": (216, "2026-01-01"),
    "1Y": (247, "2025-12-01"),
    "MAX": (247, "2025-12-01"),
}
#: LAST itself (collected today: as before), the bug's day, and the next year (YTD in the last bar's year).
TODAYS = [LAST, date(2026, 10, 20), date(2027, 1, 15)]
ENDPOINTS = ["hp", "bars", "indicators", "compare", "csv"]


def _days(first, last):
    """Every ISO date from *first* to *last*, both included."""
    start, end = date.fromisoformat(str(first)), date.fromisoformat(str(last))
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _bars(ticker, days, base, step):
    """(ticker, date, open, high, low, close, volume) rows on *days*, the close rising by *step* a day from *base*."""
    return [(ticker, d, base + i * step - 0.5, base + i * step + 1, base + i * step - 1, base + i * step, 1e6 + i)
            for i, d in enumerate(days)]


@pytest.fixture(scope="module")
def stale_client(tmp_path_factory):
    """A client on a database last collected on LAST: the companies STALE, EARLY, BARE (no bars) and STAMP (its last
    bar stored with a time of day), and ^GSPC's bars (no companies row, as a collection stores it)."""
    path = tmp_path_factory.mktemp("bug151") / "stock.db"
    store = SQLiteStore(db_path=path)
    conn = store._connect()
    store._create_schema(conn)
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO companies (ticker) VALUES (?)", [("STALE",), ("EARLY",), ("BARE",), ("STAMP",)])
    rows = (_bars("STALE", _days(FIRST, LAST), 100.0, 1.0) + _bars("EARLY", _days(FIRST, EARLY_LAST), 50.0, 0.5)
            + _bars("^GSPC", _days(FIRST, INDEX_LAST), 5000.0, 10.0)
            + _bars("STAMP", _days("2026-06-01", "2026-08-03") + ["2026-08-04 16:00:00"], 20.0, 0.1))
    conn.executemany("INSERT INTO price_bars (ticker, date, open, high, low, close, volume) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return TestClient(create_app(db_path=path))


def _pin_today(monkeypatch, day):
    monkeypatch.setattr(stocks_api, "_today", lambda: day)


def _hp(client, ticker, range_key):
    """The HP tab's table dates, oldest first (it lists the newest first), and its page."""
    resp = client.get(f"/ui/stocks/{ticker}/hp", params={"range": range_key})
    assert resp.status_code == 200
    return re.findall(r'class="row-label hp-bar-date">([^<]+)<', resp.text)[::-1], resp.text


def _dates(client, endpoint, ticker, range_key):
    """The dates *endpoint* gives *ticker* over *range_key*, oldest first."""
    if endpoint == "hp":
        return _hp(client, ticker, range_key)[0]
    if endpoint == "bars":
        resp = client.get(f"/api/stocks/{ticker}/bars", params={"range": range_key, "interval": "1d"})
    elif endpoint == "indicators":
        resp = client.get(f"/api/stocks/{ticker}/indicators", params={"range": range_key})
    elif endpoint == "compare":
        resp = client.get(f"/api/stocks/{ticker}/compare-bars", params={"range": range_key})
    else:
        resp = client.get(f"/api/export/stock/{ticker}/bars.csv", params={"range": range_key, "interval": "1d"})
    assert resp.status_code == 200
    if endpoint == "bars":
        return [bar["date"] for bar in resp.json()]
    if endpoint == "indicators":
        return resp.json()["dates"]
    if endpoint == "compare":
        return resp.json()["series"][ticker]["dates"]
    return [line.split(",")[0] for line in resp.text.splitlines()[1:]]


@pytest.mark.parametrize("today", TODAYS, ids=["collected-today", "2026-10-20", "next-year"])
@pytest.mark.parametrize("range_key", list(RANGES))
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_a_range_ends_on_the_last_bar(stale_client, monkeypatch, endpoint, range_key, today):
    _pin_today(monkeypatch, today)
    count, first = RANGES[range_key]
    dates = _dates(stale_client, endpoint, "STALE", range_key)

    assert (len(dates), dates[:1], dates[-1:]) == (count, [first], [LAST.isoformat()])
    assert dates == _days(first, LAST)


def test_the_hp_tab_lists_the_bars_and_its_csv_is_that_table(stale_client, monkeypatch):
    _pin_today(monkeypatch, date(2026, 10, 20))
    dates, body = _hp(stale_client, "STALE", "1M")
    href = html.unescape(re.search(r'href="(/api/export/stock/STALE/bars\.csv[^"]*)"', body).group(1))
    csv_dates = [line.split(",")[0] for line in stale_client.get(href).text.splitlines()[1:]]

    assert "No price history collected." not in body
    assert csv_dates == dates == _days("2026-07-05", LAST)


def test_compare_bars_count_back_from_the_primarys_last_bar(stale_client, monkeypatch):
    _pin_today(monkeypatch, date(2026, 10, 20))
    resp = stale_client.get("/api/stocks/STALE/compare-bars", params={"others": "EARLY,^GSPC", "range": "1M"})
    assert resp.status_code == 200
    series = resp.json()["series"]

    # 30 days back from STALE's last bar: ^GSPC, collected ten days later, runs on to its own last bar; EARLY's bars
    # end before the range, so it shares no date (BUG-149).
    assert series["STALE"]["dates"] == _days("2026-07-05", LAST)
    assert series["^GSPC"]["dates"] == _days("2026-07-05", INDEX_LAST)
    assert series["EARLY"] == {"dates": [], "pct": []}
    # Both rebased on 2026-07-05 (day 216): STALE 316 to 346 on its last bar, ^GSPC 7,160 to 7,560 on its own.
    assert series["STALE"]["pct"][0] == series["^GSPC"]["pct"][0] == 0.0
    assert series["STALE"]["pct"][-1] == pytest.approx(346 / 316 - 1)
    assert series["^GSPC"]["pct"][-1] == pytest.approx(7560 / 7160 - 1)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_bars_dated_after_today_leave_the_range_on_today(stale_client, monkeypatch, endpoint):
    # A clock behind the data's: the range counts back from today, as before, and runs to the last bar.
    _pin_today(monkeypatch, date(2026, 7, 20))
    assert _dates(stale_client, endpoint, "STALE", "1M") == _days("2026-06-20", LAST)


def test_a_last_bar_stored_with_a_time_of_day_anchors_the_range_on_its_day(stale_client, monkeypatch):
    _pin_today(monkeypatch, date(2026, 10, 20))
    assert _dates(stale_client, "bars", "STAMP", "1M") == _days("2026-07-05", "2026-08-03") + ["2026-08-04 16:00:00"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_a_company_without_bars_stays_empty(stale_client, monkeypatch, endpoint):
    _pin_today(monkeypatch, date(2026, 10, 20))
    assert _dates(stale_client, endpoint, "BARE", "1M") == []


def test_the_hp_tab_of_a_company_without_bars_says_none_were_collected(stale_client, monkeypatch):
    _pin_today(monkeypatch, date(2026, 10, 20))
    dates, body = _hp(stale_client, "BARE", "1M")
    assert dates == [] and "No price history collected." in body


def test_an_unknown_ticker_gets_no_bars_and_no_indicators(stale_client, monkeypatch):
    _pin_today(monkeypatch, date(2026, 10, 20))
    assert _dates(stale_client, "bars", "NOPE", "1M") == []
    assert _dates(stale_client, "indicators", "NOPE", "1M") == []


def test_compare_bars_for_a_primary_without_bars_count_back_from_today(stale_client, monkeypatch):
    _pin_today(monkeypatch, date(2026, 8, 20))
    resp = stale_client.get("/api/stocks/BARE/compare-bars", params={"others": "^GSPC", "range": "1M"})
    assert resp.status_code == 200
    series = resp.json()["series"]

    assert series["BARE"] == {"dates": [], "pct": []}
    # 30 days back from today (2026-07-21), not from ^GSPC's last bar (2026-07-15).
    assert series["^GSPC"]["dates"] == _days("2026-07-21", INDEX_LAST)
