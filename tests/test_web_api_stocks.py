"""Tests for the ``/api/stocks/*`` JSON router and its two CSV exports.

The shared ``web_db``/``client`` fixtures (see ``tests/conftest.py``) give
AAA >=260 daily price bars starting 2023-01-01 (deterministic synthetic
walk, ending 2023-09-17), plus one analyst snapshot, earnings/dividend/
split history, holders, insider transactions, officers and a description.
``^GSPC`` has 30 daily bars (2024-01-01..2024-01-30) but NO ``companies``
row.

Range resolution is measured from "today" (see ``resolve_range_start`` in
``src/webapp/routes/stocks_api.py``). Because the fixture's price history
is fixed in the past, tests that need a *meaningful* (non-empty) 1M/3M/...
window monkeypatch ``stocks_api._today`` to a fixed reference date near the
fixture's own dates, rather than depending on the real wall clock.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta

from src.webapp.routes import stocks_api

AAA_BAR_COUNT = 260
AAA_START = date(2023, 1, 1)
AAA_LAST = AAA_START + timedelta(days=AAA_BAR_COUNT - 1)  # 2023-09-17


def _pin_today(monkeypatch, fixed: date) -> None:
    monkeypatch.setattr(stocks_api, "_today", lambda: fixed)


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------


def test_quote_known_ticker_returns_price_and_change(client):
    resp = client.get("/api/stocks/AAA/quote")
    assert resp.status_code == 200
    body = resp.json()
    # aaa2 snapshot: current_price=105.0, previous_close=100.0
    assert body["current_price"] == 105.0
    assert body["previous_close"] == 100.0
    assert body["change"] == 5.0
    assert body["change_pct"] == 0.05


def test_quote_unknown_ticker_404(client):
    resp = client.get("/api/stocks/ZZZ/quote")
    assert resp.status_code == 404


def test_quote_known_ticker_without_snapshot_404(client, web_db):
    conn = sqlite3.connect(str(web_db))
    conn.execute("INSERT INTO companies (ticker) VALUES ('NOSNAP')")
    conn.commit()
    conn.close()

    resp = client.get("/api/stocks/NOSNAP/quote")
    assert resp.status_code == 404
    assert "no quote" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# bars: shape/values, MAX range, bad range/interval
# ---------------------------------------------------------------------------


def test_bars_max_range_returns_full_daily_history(client):
    resp = client.get("/api/stocks/AAA/bars", params={"range": "MAX", "interval": "1d"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == AAA_BAR_COUNT
    assert body[0]["date"] == AAA_START.isoformat()
    assert body[-1]["date"] == AAA_LAST.isoformat()
    # First synthetic bar: price = 100.0 + 0*0.1 + 0*0.05 = 100.0
    assert body[0]["open"] == 99.8
    assert body[0]["high"] == 100.5
    assert body[0]["low"] == 99.5
    assert body[0]["close"] == 100.0
    assert body[0]["volume"] == 1_000_000.0


def test_bars_bad_range_400(client):
    resp = client.get("/api/stocks/AAA/bars", params={"range": "BOGUS"})
    assert resp.status_code == 400


def test_bars_bad_interval_400(client):
    resp = client.get("/api/stocks/AAA/bars", params={"interval": "BOGUS"})
    assert resp.status_code == 400


def test_bars_unknown_ticker_returns_empty_list_not_error(client):
    # Bars/indicators/compare don't existence-check the ticker (unlike
    # quote/profile) — Reader.price_bars just returns [] for an unknown one.
    resp = client.get("/api/stocks/ZZZ/bars", params={"range": "MAX"})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# bars: range slicing (mirrors production date math with a pinned "today")
# ---------------------------------------------------------------------------


def test_bars_range_slicing_1m_is_a_dated_subset_of_max(client, monkeypatch):
    _pin_today(monkeypatch, AAA_LAST)

    full = client.get("/api/stocks/AAA/bars", params={"range": "MAX", "interval": "1d"}).json()
    sliced = client.get("/api/stocks/AAA/bars", params={"range": "1M", "interval": "1d"}).json()

    expected_start = (AAA_LAST - timedelta(days=30)).isoformat()
    expected = [b for b in full if b["date"] >= expected_start]

    assert 0 < len(sliced) < len(full)
    assert sliced == expected


def test_bars_range_slicing_ytd(client, monkeypatch):
    _pin_today(monkeypatch, AAA_LAST)

    full = client.get("/api/stocks/AAA/bars", params={"range": "MAX", "interval": "1d"}).json()
    sliced = client.get("/api/stocks/AAA/bars", params={"range": "YTD", "interval": "1d"}).json()

    expected_start = date(AAA_LAST.year, 1, 1).isoformat()
    expected = [b for b in full if b["date"] >= expected_start]
    assert sliced == expected


# ---------------------------------------------------------------------------
# bars: interval resample math (deterministic — values verified against a
# pandas resample of the same synthetic-bar formula used in conftest.py)
# ---------------------------------------------------------------------------


def test_bars_weekly_resample_aggregates_ohlcv(client):
    resp = client.get("/api/stocks/AAA/bars", params={"range": "MAX", "interval": "1wk"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 38

    # First bucket: just day 0 (2023-01-01 is a Sunday -> single-day bucket).
    first = body[0]
    assert first["date"] == "2023-01-01"
    assert first["open"] == 99.8
    assert first["high"] == 100.5
    assert first["low"] == 99.5
    assert first["close"] == 100.0
    assert first["volume"] == 1_000_000.0

    # Last bucket: days up to and including the final bar (2023-09-17).
    last = body[-1]
    assert last["date"] == "2023-09-17"
    assert last["close"] == 126.1
    assert last["high"] == 126.6
    assert last["low"] == 124.95
    assert last["volume"] == 7_896_000.0


def test_bars_monthly_resample_aggregates_ohlcv(client):
    resp = client.get("/api/stocks/AAA/bars", params={"range": "MAX", "interval": "1mo"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 9

    first = body[0]
    assert first["date"] == "2023-01-31"
    assert first["open"] == 99.8
    assert first["high"] == 103.6
    assert first["low"] == 99.5
    assert first["close"] == 103.0

    last = body[-1]
    assert last["date"] == "2023-09-17"
    assert last["close"] == 126.1


def test_bars_auto_interval_5y_is_weekly_and_max_is_monthly(client):
    weekly_auto = client.get("/api/stocks/AAA/bars", params={"range": "5Y", "interval": "auto"}).json()
    weekly_explicit = client.get("/api/stocks/AAA/bars", params={"range": "5Y", "interval": "1wk"}).json()
    assert weekly_auto == weekly_explicit

    monthly_auto = client.get("/api/stocks/AAA/bars", params={"range": "MAX", "interval": "auto"}).json()
    monthly_explicit = client.get("/api/stocks/AAA/bars", params={"range": "MAX", "interval": "1mo"}).json()
    assert monthly_auto == monthly_explicit

    daily_auto = client.get("/api/stocks/AAA/bars", params={"range": "1Y", "interval": "auto"}).json()
    daily_explicit = client.get("/api/stocks/AAA/bars", params={"range": "1Y", "interval": "1d"}).json()
    assert daily_auto == daily_explicit


# ---------------------------------------------------------------------------
# indicators: warm-at-slice-start + 400 on bad range
# ---------------------------------------------------------------------------


def test_indicators_max_range_shape(client):
    resp = client.get("/api/stocks/AAA/indicators", params={"range": "MAX"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["dates"]) == AAA_BAR_COUNT
    assert len(body["close"]) == AAA_BAR_COUNT
    assert len(body["ma_50"]) == AAA_BAR_COUNT
    assert len(body["ma_200"]) == AAA_BAR_COUNT
    assert len(body["rsi"]) == AAA_BAR_COUNT
    assert len(body["macd"]["macd"]) == AAA_BAR_COUNT
    # MA200 needs 200 points of warm-up -> None for the first 199 entries.
    assert body["ma_200"][0] is None
    assert body["ma_200"][199] is not None


def test_indicators_ma200_warm_at_slice_start(client, monkeypatch):
    _pin_today(monkeypatch, AAA_LAST)

    resp = client.get("/api/stocks/AAA/indicators", params={"range": "1M"})
    assert resp.status_code == 200
    body = resp.json()

    expected_start = (AAA_LAST - timedelta(days=30)).isoformat()
    assert body["dates"][0] >= expected_start
    assert 0 < len(body["dates"]) < AAA_BAR_COUNT
    # Sliced AFTER computing on full history -> already warm, not None.
    assert body["ma_200"][0] is not None
    assert body["ma_50"][0] is not None


def test_indicators_bad_range_400(client):
    resp = client.get("/api/stocks/AAA/indicators", params={"range": "BOGUS"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# compare-bars
# ---------------------------------------------------------------------------


def test_compare_bars_includes_benchmark_and_others(client):
    resp = client.get(
        "/api/stocks/AAA/compare-bars",
        params={"others": "^GSPC,BBB", "range": "MAX"},
    )
    assert resp.status_code == 200
    body = resp.json()
    series = body["series"]
    assert set(series.keys()) == {"AAA", "^GSPC", "BBB"}

    # ^GSPC has bars (30) but no companies row -> not excluded.
    assert len(series["^GSPC"]["dates"]) == 30
    assert series["^GSPC"]["pct"][0] == 0.0

    assert len(series["AAA"]["dates"]) == AAA_BAR_COUNT
    assert series["AAA"]["pct"][0] == 0.0


def test_compare_bars_unknown_other_contributes_empty_series(client):
    resp = client.get(
        "/api/stocks/AAA/compare-bars",
        params={"others": "NOPE", "range": "MAX"},
    )
    assert resp.status_code == 200
    series = resp.json()["series"]
    assert series["NOPE"] == {"dates": [], "pct": []}


def test_compare_bars_unknown_primary_ticker_404(client):
    resp = client.get("/api/stocks/ZZZ/compare-bars", params={"others": "AAA"})
    assert resp.status_code == 404


def test_compare_bars_bad_range_400(client):
    resp = client.get("/api/stocks/AAA/compare-bars", params={"range": "BOGUS"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# analyst / earnings / dividends / splits / profile
# ---------------------------------------------------------------------------


def test_analyst_returns_latest_snapshot(client):
    resp = client.get("/api/stocks/AAA/analyst")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendation"] == "buy"
    assert body["number_of_analysts"] == 12


def test_analyst_no_snapshot_404(client):
    resp = client.get("/api/stocks/BBB/analyst")
    assert resp.status_code == 404


def test_earnings_history_ascending(client):
    resp = client.get("/api/stocks/AAA/earnings")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 4
    quarters = [row["quarter"] for row in body]
    assert quarters == sorted(quarters)


def test_dividends_ascending(client):
    resp = client.get("/api/stocks/AAA/dividends")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 6
    dates = [row["date"] for row in body]
    assert dates == sorted(dates)


def test_dividends_unknown_ticker_returns_empty_list(client):
    resp = client.get("/api/stocks/ZZZ/dividends")
    assert resp.status_code == 200
    assert resp.json() == []


def test_splits(client):
    resp = client.get("/api/stocks/AAA/splits")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"date": "2023-06-01", "ratio": 2.0}]


def test_profile_known_ticker(client):
    resp = client.get("/api/stocks/AAA/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["ticker"] == "AAA"
    names = {officer["name"] for officer in body["officers"]}
    assert names == {"Jane Doe", "John Smith"}


def test_profile_unknown_ticker_404(client):
    resp = client.get("/api/stocks/ZZZ/profile")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# holders / insiders
# ---------------------------------------------------------------------------


def test_holders_institutional_default(client):
    resp = client.get("/api/stocks/AAA/holders")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["holder"] == "Vanguard Group"  # highest pct_held (0.08)


def test_holders_mutualfund_type(client):
    resp = client.get("/api/stocks/AAA/holders", params={"type": "mutualfund"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {row["holder"] for row in body} == {"Fidelity Contrafund", "American Funds Growth"}


def test_holders_bad_type_400(client):
    resp = client.get("/api/stocks/AAA/holders", params={"type": "hedgefund"})
    assert resp.status_code == 400


def test_insiders_default_limit(client):
    resp = client.get("/api/stocks/AAA/insiders")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["start_date"] == "2024-04-01"  # newest first


def test_insiders_limit_param(client):
    resp = client.get("/api/stocks/AAA/insiders", params={"limit": 1})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# CSV exports
# ---------------------------------------------------------------------------


def test_export_bars_csv_parses_with_header(client):
    resp = client.get(
        "/api/export/stock/AAA/bars.csv",
        params={"range": "MAX", "interval": "1d"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert reader.fieldnames == ["date", "open", "high", "low", "close", "volume"]
    assert len(rows) == AAA_BAR_COUNT
    assert rows[0]["date"] == AAA_START.isoformat()


def test_export_bars_csv_unknown_ticker_404(client):
    resp = client.get("/api/export/stock/ZZZ/bars.csv")
    assert resp.status_code == 404


def test_export_bars_csv_bad_range_400(client):
    resp = client.get("/api/export/stock/AAA/bars.csv", params={"range": "BOGUS"})
    assert resp.status_code == 400


def test_export_dividends_csv_parses_with_header(client):
    resp = client.get("/api/export/stock/AAA/dividends.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert "date" in (reader.fieldnames or [])
    assert "amount" in (reader.fieldnames or [])
    assert len(rows) == 6


def test_export_dividends_csv_unknown_ticker_404(client):
    resp = client.get("/api/export/stock/ZZZ/dividends.csv")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# summary (watchlist batch)
# ---------------------------------------------------------------------------


def test_summary_returns_known_tickers_and_skips_unknown(client):
    resp = client.get("/api/stocks/summary?tickers=AAA,ZZZ")
    assert resp.status_code == 200
    body = resp.json()
    assert [row["ticker"] for row in body] == ["AAA"]
    row = body[0]
    # aaa2 snapshot: current_price=105.0, previous_close=100.0
    assert row["price"] == 105.0
    assert row["change"] == 5.0
    assert row["change_pct"] == 0.05
    assert 0 < len(row["sparkline"]) <= 63
    assert all(isinstance(v, float) for v in row["sparkline"])


def test_summary_normalizes_case_and_whitespace(client):
    resp = client.get("/api/stocks/summary?tickers=%20aaa%20")
    assert resp.status_code == 200
    assert [row["ticker"] for row in resp.json()] == ["AAA"]


def test_summary_empty_param_returns_empty_list(client):
    resp = client.get("/api/stocks/summary?tickers=,,")
    assert resp.status_code == 200
    assert resp.json() == []


def test_summary_includes_latest_quality_score(client, web_db):
    conn = sqlite3.connect(str(web_db))
    conn.execute(
        "INSERT INTO collection_runs (ticker, collected_at, quality_score) "
        "VALUES ('AAA', '2030-01-01T00:00:00', 88)"
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/stocks/summary?tickers=AAA")
    assert resp.json()[0]["quality_score"] == 88
