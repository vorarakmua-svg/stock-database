"""/api/stocks/{ticker}/valuation-history: the 10-year ranges, the historical value against the live price, and the
monthly series (docs/superpowers/specs/2026-10-21-valuation-history-design.md)."""
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.history import HistoryResult, HistoryRow, MetricRange, historical_value_label
from src.webapp.repository import Reader


def _seed(db_path, ticker="AAA"):
    rows = [HistoryRow(date=d, close=c, market_cap=c * 1e8, ttm_period_end="2024-12-31",
                       multiples={"pe": c / 1.5, "ps": 2.0, "pb": None, "pfcf": None, "ev_ebitda": 9.0, "pffo": None,
                                  "div_yield": 0.02},
                       fair_value=fv, fair_value_n=2 if fv else 0)
            for d, c, fv in (("2025-01-31", 15.0, None), ("2025-02-28", 18.0, 16.0), ("2025-03-31", 21.0, 17.0))]
    ranges = [
        MetricRange(metric="pffo", na_reason="REITs only", as_of="2025-03-31"),
        MetricRange(metric="pe", applicable=True, as_of="2025-03-31", window_start="2015-04-30", n_months=120,
                    current=14.0, pct_rank=0.9, min=8.0, p25=10.0, median=11.5, p75=13.0, max=16.0,
                    implied_value=17.25, in_fair_value=True),
        MetricRange(metric="ps", applicable=True, as_of="2025-03-31", window_start="2015-04-30", n_months=120,
                    current=2.0, pct_rank=0.5, min=1.0, p25=1.5, median=2.0, p75=2.5, max=3.0,
                    implied_value=16.8, in_fair_value=True),
        MetricRange(metric="fair_value", applicable=True, as_of="2025-03-31", window_start="2018-03-31",
                    n_months=85, current=17.0, p25=14.0, p75=20.0, n_metrics=2),
    ]
    SQLiteStore(db_path=db_path).export_valuation_history(
        ticker, HistoryResult(ticker=ticker, rows=rows, ranges=ranges), "2025-04-01T00:00:00+00:00")


def test_the_endpoint_gives_the_value_the_ranges_and_the_series(client, web_db):
    _seed(web_db)
    resp = client.get("/api/stocks/AAA/valuation-history")
    assert resp.status_code == 200
    body = resp.json()
    price = body["price"]
    assert price is not None  # web_db's AAA snapshot has a current price
    hv = body["historical_value"]
    assert (hv["applicable"], hv["value"], hv["low"], hv["high"], hv["n_metrics"]) == (True, 17.0, 14.0, 20.0, 2)
    assert hv["metrics"] == ["pe", "ps"]
    assert hv["cutoffs"] == [0.7, 0.9, 1.1, 1.3]
    assert hv["label"] == historical_value_label(price, 17.0)  # against the live price
    assert hv["price_to_value"] == pytest.approx(price / 17.0)
    assert body["as_of"] == "2025-03-31" and body["computed_at"] == "2025-04-01T00:00:00+00:00"
    # The metrics in their fixed order, whatever the table's.
    assert [g["metric"] for g in body["ranges"]] == ["pe", "ps", "pffo"]
    pe = body["ranges"][0]
    assert (pe["current"], pe["pct_rank"], pe["median"], pe["in_fair_value"]) == (14.0, 0.9, 11.5, True)
    assert body["ranges"][2]["na_reason"] == "REITs only" and body["ranges"][2]["applicable"] is False
    series = body["series"]
    assert series["dates"] == ["2025-01-31", "2025-02-28", "2025-03-31"]
    assert series["close"] == [15.0, 18.0, 21.0]
    assert series["fair_value"] == [None, 16.0, 17.0]
    assert series["pe"] == [10.0, 12.0, 14.0]
    assert series["pb"] == [None, None, None]


def test_an_unknown_ticker_is_404(client):
    assert client.get("/api/stocks/ZZZZ/valuation-history").status_code == 404


def test_the_benchmark_has_no_history(client):
    assert client.get("/api/stocks/%5EGSPC/valuation-history").status_code == 404


def test_a_ticker_not_computed_yet(client):
    body = client.get("/api/stocks/BBB/valuation-history").json()
    assert body["historical_value"]["applicable"] is False
    assert body["historical_value"]["na_reason"] == "not computed yet"
    assert body["ranges"] == [] and body["series"]["dates"] == []


def test_a_database_from_before_the_history_tables(client, web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE IF EXISTS valuation_history")
    conn.execute("DROP TABLE IF EXISTS valuation_ranges")
    conn.commit()
    conn.close()
    resp = client.get("/api/stocks/AAA/valuation-history")
    assert resp.status_code == 200
    assert resp.json()["historical_value"]["na_reason"] == "not computed yet"


def test_the_readers_order_by_date_and_metric(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        assert [row["date"] for row in r.valuation_history("AAA")] == ["2025-01-31", "2025-02-28", "2025-03-31"]
        assert {row["metric"] for row in r.valuation_ranges("AAA")} == {"pe", "ps", "pffo", "fair_value"}
        assert r.valuation_history("BBB") == [] and r.valuation_ranges("BBB") == []
