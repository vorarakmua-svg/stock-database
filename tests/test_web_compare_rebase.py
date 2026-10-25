"""BUG-149: GP's compare chart rebases every series on the first date they share.

``/api/stocks/{T}/compare-bars?others=^GSPC&range=R`` rebased each series on its own first bar in the range, so at MAX
AAPL (from 1980-12-12) read +349,443% beside an S&P 500 counted from 1927: +43,509%, where its change from AAPL's first
day was +5,859%. Now the series start together: the shared start is the latest of their first dates with a close, and
each series runs from it, rebased on its first close on or after it (0% for every one there). A series with no bars
stays empty and doesn't move the start.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.exporters.sqlite_store import SQLiteStore
from src.webapp import create_app
from src.webapp.routes import stocks_api

#: The index from 2024-03-20 (no bar on the weekend of the 23rd), six days before NEW's first.
INDEX = [("2024-03-20", 5000.0), ("2024-03-21", 5100.0), ("2024-03-22", 5050.0), ("2024-03-25", 5200.0),
         ("2024-03-26", 5250.0), ("2024-03-27", 5460.0), ("2024-03-28", 5040.0)]
#: A stock listed on 2024-03-26.
NEW = [("2024-03-26", 40.0), ("2024-03-27", 50.0), ("2024-03-28", 30.0)]
#: A stock with the index's dates.
OLD = [("2024-03-20", 10.0), ("2024-03-21", 11.0), ("2024-03-22", 12.0), ("2024-03-25", 15.0),
       ("2024-03-26", 16.0), ("2024-03-27", 20.0), ("2024-03-28", 12.0)]
#: From 2024-03-26 on.
FROM_26TH = ["2024-03-26", "2024-03-27", "2024-03-28"]


def _client(tmp_path, bars, companies):
    """A client on a fresh database holding *bars* ({ticker: [(date, close), ...]}) and a companies row for each of
    *companies* (the index has bars but no companies row, as the collection stores it)."""
    path = tmp_path / "stock.db"
    store = SQLiteStore(db_path=path)
    conn = store._connect()
    store._create_schema(conn)
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO companies (ticker) VALUES (?)", [(t,) for t in companies])
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES (?, ?, ?)",
                     [(t, d, close) for t, rows in bars.items() for d, close in rows])
    conn.commit()
    conn.close()
    return TestClient(create_app(db_path=path))


def _compare(client, ticker, others, range_key):
    resp = client.get(f"/api/stocks/{ticker}/compare-bars", params={"others": others, "range": range_key})
    assert resp.status_code == 200
    return resp.json()["series"]


def test_a_later_listing_and_the_index_start_together_on_its_first_day(tmp_path):
    series = _compare(_client(tmp_path, {"NEW": NEW, "^GSPC": INDEX}, ["NEW"]), "NEW", "^GSPC", "MAX")

    assert series["NEW"]["dates"] == FROM_26TH
    assert series["NEW"]["pct"] == pytest.approx([0.0, 0.25, -0.25])
    # The index's change from NEW's first day (5,250): +4% and -4%; from its own first bar (5,000) it read +9.2%, +0.8%.
    assert series["^GSPC"]["dates"] == FROM_26TH
    assert series["^GSPC"]["pct"] == pytest.approx([0.0, 0.04, -0.04])


def test_a_range_shorter_than_both_histories_is_as_it_was(tmp_path, monkeypatch):
    # 1M back from 2024-04-25 starts on 2024-03-26, a day both series have a bar: each is rebased on that bar. Both
    # have a bar on 2024-04-25, today: a range counts back from the last bar when that is earlier (BUG-151).
    monkeypatch.setattr(stocks_api, "_today", lambda: date(2024, 4, 25))
    old, index = OLD + [("2024-04-25", 18.0)], INDEX + [("2024-04-25", 5355.0)]
    series = _compare(_client(tmp_path, {"OLD": old, "^GSPC": index}, ["OLD"]), "OLD", "^GSPC", "1M")

    assert series == {
        "OLD": {"dates": FROM_26TH + ["2024-04-25"], "pct": pytest.approx([0.0, 0.25, -0.25, 0.125])},
        "^GSPC": {"dates": FROM_26TH + ["2024-04-25"], "pct": pytest.approx([0.0, 0.04, -0.04, 0.02])},
    }


def test_an_other_without_bars_stays_empty_and_moves_nothing(tmp_path):
    client = _client(tmp_path, {"NEW": NEW, "^GSPC": INDEX}, ["NEW", "BARE"])
    alone = _compare(client, "NEW", "^GSPC", "MAX")
    # NOPE is no company, BARE a company without bars; both before the index in the list.
    series = _compare(client, "NEW", "NOPE,BARE,^GSPC", "MAX")

    assert series["NOPE"] == {"dates": [], "pct": []}
    assert series["BARE"] == {"dates": [], "pct": []}
    assert series["NEW"] == alone["NEW"]
    assert series["^GSPC"] == alone["^GSPC"]
    assert series["^GSPC"]["dates"] == FROM_26TH
    assert series["^GSPC"]["pct"] == pytest.approx([0.0, 0.04, -0.04])


def test_an_index_that_starts_after_the_stock_rebases_both_on_its_first_day(tmp_path):
    series = _compare(_client(tmp_path, {"OLD": OLD, "^GSPC": INDEX[4:]}, ["OLD"]), "OLD", "^GSPC", "MAX")

    # OLD's change from the index's first day (16): +25% and -25%; from its own first bar (10) it read +100%, +20%.
    assert series["OLD"]["dates"] == FROM_26TH
    assert series["OLD"]["pct"] == pytest.approx([0.0, 0.25, -0.25])
    assert series["^GSPC"]["dates"] == FROM_26TH
    assert series["^GSPC"]["pct"] == pytest.approx([0.0, 0.04, -0.04])


@pytest.mark.parametrize("on_the_26th, dates, pct", [
    ([], ["2024-03-27", "2024-03-28"], [0.0, 0.02]),  # no bar that day
    ([("2024-03-26", None)], FROM_26TH, [None, 0.0, 0.02]),  # a bar without a close
], ids=["no-bar", "no-close"])
def test_an_index_without_a_close_on_the_shared_start_is_rebased_on_its_next_close(tmp_path, on_the_26th, dates, pct):
    before, after = [("2024-03-22", 5050.0), ("2024-03-25", 5200.0)], [("2024-03-27", 5000.0), ("2024-03-28", 5100.0)]
    series = _compare(_client(tmp_path, {"NEW": NEW, "^GSPC": before + on_the_26th + after}, ["NEW"]),
                      "NEW", "^GSPC", "MAX")

    assert series["NEW"]["dates"] == FROM_26TH
    assert series["NEW"]["pct"] == pytest.approx([0.0, 0.25, -0.25])
    # Rebased on the 27th's 5,000 (+2% on the 28th), not on the 25th's 5,200 before the start.
    assert series["^GSPC"]["dates"] == dates
    assert series["^GSPC"]["pct"] == pytest.approx(pct)


def test_a_first_bar_without_a_close_doesnt_set_the_start(tmp_path):
    new = [("2024-03-26", None), ("2024-03-27", 40.0), ("2024-03-28", 50.0)]
    index = [("2024-03-25", 5200.0), ("2024-03-26", 5250.0), ("2024-03-27", 5000.0), ("2024-03-28", 5100.0)]
    series = _compare(_client(tmp_path, {"NEW": new, "^GSPC": index}, ["NEW"]), "NEW", "^GSPC", "MAX")

    # NEW's first close is the 27th's: both start there.
    assert series["NEW"]["dates"] == ["2024-03-27", "2024-03-28"]
    assert series["NEW"]["pct"] == pytest.approx([0.0, 0.25])
    assert series["^GSPC"]["dates"] == ["2024-03-27", "2024-03-28"]
    assert series["^GSPC"]["pct"] == pytest.approx([0.0, 0.02])


def test_a_series_that_ends_before_the_others_start_shares_no_date_and_is_empty(tmp_path):
    series = _compare(_client(tmp_path, {"OLD": OLD[:4], "^GSPC": INDEX[4:]}, ["OLD"]), "OLD", "^GSPC", "MAX")

    assert series["OLD"] == {"dates": [], "pct": []}
    assert series["^GSPC"]["dates"] == FROM_26TH
    assert series["^GSPC"]["pct"] == pytest.approx([0.0, 0.04, -0.04])
