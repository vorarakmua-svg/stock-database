"""BUG-160, BUG-258: a saved quote refresh stores Yahoo's payments with the price (the chart request the split history
comes from serves them too), dated as a collection dates its own (``sqlite_store._served_day``), so whatever values the
ticker after it reads the payments a full collection that day would: the web job after a stock dividend's or a
spin-off's factor (BUG-128, BUG-160), and ``stock-data refresh``'s recompute step, which values every company after
the quotes step stored their snapshots (BUG-258). DDM's trailing year ends on the latest snapshot's day
(``inputs.load_inputs``)."""
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

import src.fetchers.quote_refresh as quote_refresh
import src.fetchers.yahoo_handler as yh
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers.quote_refresh import refresh_quote
from src.models.stock_data import StockData
from src.valuation.engine import compute_and_store
from src.webapp.jobs import CollectionJobManager
from tests import refresh_fakes as rf

#: ZZZ pays $1.00 a quarter. Its 2021 2:1 split is stored; a 5% stock dividend on 2024-08-01 (Yahoo's factor 1.05)
#: leaves it paying $1.00 a share, $0.95 on the old basis.
SPLIT = {"date": "2021-06-01", "ratio": 2.0}
FACTOR = {"date": "2024-08-01", "ratio": 1.05}
DAYS = [f"{y}-{md}" for y in range(2020, 2025) for md in ("03-13", "06-14", "09-13", "12-13")]
COLLECTED = "2024-06-20"  # the last collection, local 19:51 (New York's 08:51, its last bar the day before)
REFRESHED = "2024-09-20"  # after the 09-13 payment


def _yahoo(day: str, factor: bool) -> Dict[str, Any]:
    """Yahoo's split history and payments on *day*, with or without the factor (without it, the 09-13 payment is
    raised to $1.10)."""
    listed = factor and FACTOR["date"] < day
    splits = [SPLIT] + ([FACTOR] if listed else [])
    payments = [{"date": d, "amount": 1.0 / FACTOR["ratio"] if listed and d < FACTOR["date"]
                 else 1.10 if not factor and d == "2024-09-13" else 1.0} for d in DAYS if d < day]
    return {"splits": splits, "payments": payments, "price": 100.0 / FACTOR["ratio"] if listed else 100.0,
            "shares": 1e8 * (FACTOR["ratio"] if listed else 1.0)}


def _collect(path: Path, day: str, factor: bool = True) -> Path:
    """ZZZ as a full collection on *day* stores it (``SQLiteStore.export``), then valued."""
    y = _yahoo(day, factor)
    stock = StockData(ticker="ZZZ", cik="0000000099", company_name="ZZZ Corp", sector_class="general",
                      collected_at=datetime.fromisoformat(f"{day}T19:51:00"))
    stock.market_data = {"current_price": y["price"], "market_cap": 1e10, "beta": 1.0}
    stock.shareholders = {"shares_outstanding": y["shares"]}
    stock.risk_free_rate = {"risk_free_rate": 0.04, "date": "2024-06-18", "source": "treasury_gov"}
    stock.analyst_estimates = {"earnings_growth": 0.05}
    stock.dividend_history = {"dividend_payments": y["payments"]}
    stock.price_bars = [{"date": d.strftime("%Y-%m-%d"), "open": y["price"], "high": y["price"], "low": y["price"],
                         "close": y["price"], "volume": 1e6}
                        for d in pd.bdate_range("2024-01-02", pd.Timestamp(day) - pd.Timedelta(days=1))]
    stock.splits = y["splits"]
    stock.add_source("yahoo_finance")
    stock.add_source("sec_edgar")
    assert SQLiteStore(path).export([stock]) == path
    assert compute_and_store(path, tickers=["ZZZ"]) == 1
    return path


def _quote(day: str, factor: bool = True, dividends: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Yahoo's quote-only answer on *day* (``YahooHandler.fetch_quote``): the price, the split history, the payments."""
    y = _yahoo(day, factor)
    return {"market_data": {"current_price": y["price"], "market_cap": 1e10, "beta": 1.0}, "valuation": {},
            "shareholders": {"shares_outstanding": y["shares"]}, "analyst_estimates": {"earnings_growth": 0.05},
            "splits": y["splits"],
            "dividend_history": {"dividend_payments": y["payments"]} if dividends is None else dividends}


class _Handler:
    def __init__(self, answer: Dict[str, Any]) -> None:
        self.answer = answer

    def fetch_quote(self, ticker: str) -> Dict[str, Any]:
        return dict(self.answer)


def _ddm(path: Path) -> Dict[str, Any]:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value_bear, value_base, value_bull, assumptions FROM valuations "
                           "WHERE ticker = 'ZZZ' AND model = 'ddm'").fetchone()
        summary = conn.execute("SELECT median_bear, median_base, median_bull FROM valuation_summary "
                               "WHERE ticker = 'ZZZ'").fetchone()
    finally:
        conn.close()
    a = json.loads(row[3])
    return {"values": row[:3], "ttm_dps": a["ttm_dps"], "ttm_anchor": a["ttm_anchor"], "summary": summary}


def _payments(path: Path, since: str = "2024-03-01") -> List[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT date, amount, served_on FROM dividend_events WHERE ticker = 'ZZZ' AND date >= ? "
                            "ORDER BY date", (since,)).fetchall()
    finally:
        conn.close()


def test_a_refresh_after_a_price_factor_values_ddm_as_a_full_collection_that_day_would(tmp_path, monkeypatch):
    # BUG-160 (code review 14, I1): the refresh stored the factor but no payments, and the web job valued the ticker
    # as of the refresh's day, so the 09-13 payment was missing from DDM's trailing year (three payments of four):
    # SCCO's DDM read $53.63 for $68.16 on a copy of the user's data, HON's median $59.85 for $74.75.
    db = _collect(tmp_path / "stock.db", COLLECTED)
    oracle = _ddm(_collect(tmp_path / "full.db", REFRESHED))
    monkeypatch.setattr(quote_refresh, "collected_at_now", lambda: f"{REFRESHED}T19:51:00")
    manager = CollectionJobManager(db, quote_fetcher_factory=lambda config: _Handler(_quote(REFRESHED)))
    job_id = manager.submit(["ZZZ"], mode="quote")
    deadline = time.monotonic() + 30
    while manager.get(job_id).state not in ("done", "error") and time.monotonic() < deadline:
        time.sleep(0.02)
    job = manager.get(job_id)
    manager.shutdown()
    assert job.summary["successful"] == 1
    got = _ddm(db)
    assert got["ttm_anchor"] == oracle["ttm_anchor"] == REFRESHED
    assert got["ttm_dps"] == pytest.approx(oracle["ttm_dps"]) == pytest.approx(3 / 1.05 + 1.0)
    assert got["values"] == pytest.approx(oracle["values"])
    assert got["summary"] == pytest.approx(oracle["summary"])


def test_the_refreshs_recompute_values_ddm_as_a_full_collection_that_day_would(tmp_path, monkeypatch):
    # stock-data refresh's recompute step values every company after its quotes step stored their snapshots, a refresh
    # without a factor too: DDM read three payments of four after a payment since the last collection (CVS and LOW on
    # the user's data after its 2026-10-25 refresh).
    db = _collect(tmp_path / "stock.db", COLLECTED, factor=False)
    oracle = _ddm(_collect(tmp_path / "full.db", REFRESHED, factor=False))
    monkeypatch.setattr(quote_refresh, "collected_at_now", lambda: f"{REFRESHED}T19:51:00")

    def compute(tickers: Optional[List[str]]) -> int:
        return compute_and_store(db, tickers=tickers)

    svc = rf.services(db, yahoo=rf.FakeYahoo(quotes={"ZZZ": _quote(REFRESHED, factor=False)}), compute=compute,
                      clock=lambda: datetime.fromisoformat(f"{REFRESHED}T12:51:00+00:00"))
    report = rf.run(db, svc, steps=["quotes", "recompute"])
    assert report.result("quotes").ok and report.result("recompute").ok
    got = _ddm(db)
    assert got["ttm_anchor"] == oracle["ttm_anchor"] == REFRESHED
    assert got["ttm_dps"] == pytest.approx(oracle["ttm_dps"]) == pytest.approx(3.0 + 1.10)
    assert got["values"] == pytest.approx(oracle["values"])


@pytest.mark.parametrize("factor, served", [(True, "2024-08-01"), (False, "2021-06-01")])
def test_a_refresh_dates_yahoos_payments_as_a_collection_would(tmp_path, factor, served):
    # The collection's rule (_served_day) on a run without bars: the latest split Yahoo lists, every one of which its
    # payments are adjusted for; a split it doesn't list yet isn't (BUG-117).
    db = _collect(tmp_path / "stock.db", COLLECTED, factor=factor)
    result = refresh_quote(_Handler(_quote(REFRESHED, factor=factor)), SQLiteStore(db), "ZZZ",
                           f"{REFRESHED}T19:51:00")
    assert result.outcome == ("rebased" if factor else "stored")
    amount = 1.0 / FACTOR["ratio"] if factor else 1.0
    assert _payments(db) == [("2024-03-13", pytest.approx(amount), served),
                             ("2024-06-14", pytest.approx(amount), served),
                             ("2024-09-13", 1.0 if factor else 1.10, served)]


def test_a_refresh_whose_dividend_request_failed_keeps_the_stored_payments(tmp_path):
    db = _collect(tmp_path / "stock.db", COLLECTED)
    before = _payments(db, since="0000")
    result = refresh_quote(_Handler(_quote(REFRESHED, dividends={"error": "429 Too Many Requests"})),
                           SQLiteStore(db), "ZZZ", f"{REFRESHED}T19:51:00")
    assert result.outcome == "rebased"
    assert _payments(db, since="0000") == before


def test_a_quote_carries_yahoos_payments_from_the_chart_request_the_splits_use(monkeypatch):
    class ChartTicker:
        def __init__(self, ticker: str) -> None:
            self.info = {"currentPrice": 12.0, "regularMarketPrice": 12.0}
            self.splits = pd.Series([1.05], index=pd.to_datetime(["2024-08-01"]))
            self.dividends = pd.Series([0.95, 1.0], index=pd.to_datetime(["2024-06-14", "2024-09-13"]))

    monkeypatch.setattr(yh.yf, "Ticker", ChartTicker)
    quote = yh.YahooHandler(rate_limit_delay=0).fetch_quote("ZZZ")
    assert quote["splits"] == [{"date": "2024-08-01", "ratio": 1.05}]
    assert quote["dividend_history"]["dividend_payments"] == [{"date": "2024-06-14", "amount": 0.95},
                                                              {"date": "2024-09-13", "amount": 1.0}]
