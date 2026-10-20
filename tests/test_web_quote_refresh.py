"""Tests for on-demand quote refresh (Task 10): quote-only Yahoo fetch,
``mode="quote"`` job path, the ``POST /api/stocks/{ticker}/refresh-quote``
endpoint, and the DES panel's REFRESH button + poll fragment.

All tests use fake fetchers injected via ``job_manager.quote_fetcher_factory``
(mirroring ``fetcher_factory`` in ``tests/test_web_api_collection.py``) —
zero network I/O.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.webapp import create_app
from src.webapp.repository import Reader
from src.webapp.settings import WebSettings

# ---------------------------------------------------------------------------
# Fake quote fetcher
# ---------------------------------------------------------------------------

_CANNED_QUOTE: Dict[str, Any] = {
    "market_data": {
        "current_price": 123.45,
        "previous_close": 120.0,
        "open": 121.0,
        "day_high": 124.0,
        "day_low": 120.5,
        "volume": 1_000_000.0,
        "beta": 1.2,
    },
    "valuation": {"pe_trailing": 22.0, "price_to_book": 3.5},
    "shareholders": {"shares_outstanding": 10_000_000.0},
    "analyst_estimates": {
        "target_price_mean": 150.0,
        "recommendation": "buy",
        "number_of_analysts": 8,
    },
}


class FakeQuoteHandler:
    """Minimal stand-in for ``YahooHandler`` — only ``fetch_quote`` is called."""

    def __init__(self, canned: Optional[Dict[str, Any]] = None) -> None:
        self._canned = canned if canned is not None else _CANNED_QUOTE

    def fetch_quote(self, ticker: str) -> Dict[str, Any]:
        return dict(self._canned)


def fake_quote_factory(config: Any) -> FakeQuoteHandler:  # type: ignore[return]
    return FakeQuoteHandler()


class FakeFullFetcher:
    """Same fake full-collection fetcher as test_web_api_collection.py."""

    def __enter__(self) -> "FakeFullFetcher":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def fetch_ticker(
        self,
        ticker: str,
        include_yahoo: bool = True,
        include_sec: bool = True,
        years_back: Optional[int] = None,
    ) -> StockData:
        return StockData(ticker=ticker)

    def export(
        self,
        data: List[StockData],
        formats: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {}


def fake_full_factory(config: Any) -> FakeFullFetcher:  # type: ignore[return]
    return FakeFullFetcher()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_until_done(
    client: TestClient,
    job_id: str,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Poll GET /api/collection/jobs/{job_id} until terminal state or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/collection/jobs/{job_id}")
        assert r.status_code == 200, r.text
        data = r.json()
        if data["state"] in ("done", "error"):
            return data  # type: ignore[return-value]
        time.sleep(0.05)
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


def _make_client(web_db: Path, allow_quote_refresh: bool = True) -> TestClient:
    settings = WebSettings(db_path=web_db, allow_quote_refresh=allow_quote_refresh)
    app = create_app(settings=settings)
    app.state.job_manager.quote_fetcher_factory = fake_quote_factory
    return TestClient(app)


def _snapshot_count(db_path: Path, ticker: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE ticker = ?", (ticker,)
        )
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def _analyst_count(db_path: Path, ticker: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM analyst_snapshots WHERE ticker = ?", (ticker,)
        )
        return int(cur.fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests: collected_at timestamp convention (live-smoke find #2)
#
# ``market_snapshots.collected_at`` is TEXT-ordered (``ORDER BY collected_at
# DESC``/``MAX(collected_at)``), and the full pipeline writes NAIVE LOCAL
# timestamps (``StockData.collected_at`` -> ``datetime.now().isoformat()``,
# no ``+HH:MM`` suffix). If the quote-refresh path ever writes a
# timezone-AWARE string instead, a same-day naive-local row can sort higher
# than a chronologically later aware row (e.g. naive local "14:10" > aware
# "07:23+00:00" on a UTC+7 machine), so ``Reader.quote``/``latest_snapshot``
# would keep serving the stale row after a refresh.
# ---------------------------------------------------------------------------

def test_quote_refresh_collected_at_has_no_utc_offset_suffix(web_db: Path) -> None:
    """Format parity: the quote path's generated ``collected_at`` must match
    the full pipeline's naive-local ``datetime.now().isoformat()`` convention
    (no ``+HH:MM`` offset suffix) — mixed formats break TEXT-ordered
    latest-row resolution on ``market_snapshots``."""
    client = _make_client(web_db)
    r = client.post("/api/stocks/AAA/refresh-quote")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    final = _wait_until_done(client, job_id)
    assert final["state"] == "done"

    conn = sqlite3.connect(web_db)
    try:
        cur = conn.execute(
            "SELECT collected_at FROM market_snapshots WHERE ticker = ? "
            "ORDER BY collected_at DESC LIMIT 1",
            ("AAA",),
        )
        collected_at = cur.fetchone()[0]
    finally:
        conn.close()

    assert "+" not in collected_at, (
        f"collected_at={collected_at!r} looks timezone-aware "
        "(contains a '+HH:MM' offset) — must be naive-local like the full "
        "pipeline's StockData.collected_at"
    )


def test_quote_refresh_wins_latest_row_resolution_over_naive_local_seed(
    web_db: Path,
) -> None:
    """Regression guard: a same-day full-collection row (naive-local
    ``collected_at``, written via the normal export path) must not outrank a
    chronologically later quote-refresh row in ``Reader.quote``'s latest-row
    resolution. This is the exact live-smoke bug: mixing naive-local and
    UTC-aware ``collected_at`` strings in the same TEXT-ordered column broke
    ``MAX(collected_at)``, so a refresh appeared to do nothing.
    """
    ticker = "ZZZ"
    seed = StockData(
        ticker=ticker, cik="0000000099", company_name="ZZZ Corp",
        sector_class="general", collected_at=datetime.now(),
    )
    seed.market_data = {"current_price": 50.0, "previous_close": 49.0}
    seed.valuation = {"pe_trailing": 15.0}
    assert SQLiteStore(web_db).export([seed]) is not None

    client = _make_client(web_db)
    r = client.post(f"/api/stocks/{ticker}/refresh-quote")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    final = _wait_until_done(client, job_id)
    assert final["state"] == "done"

    with Reader(web_db) as reader:
        quote = reader.quote(ticker)

    assert quote is not None
    # From _CANNED_QUOTE (the refresh), NOT the seed's 50.0/49.0 — proves the
    # refresh row won latest-row resolution rather than the older seed row.
    assert quote["current_price"] == 123.45
    assert quote["previous_close"] == 120.0


# ---------------------------------------------------------------------------
# Tests: gating
# ---------------------------------------------------------------------------

def test_refresh_quote_disabled_returns_409(web_db: Path) -> None:
    client = _make_client(web_db, allow_quote_refresh=False)
    r = client.post("/api/stocks/AAA/refresh-quote")
    assert r.status_code == 409
    assert "disabled" in r.json()["detail"].lower()


def test_refresh_quote_unknown_ticker_404(web_db: Path) -> None:
    client = _make_client(web_db)
    r = client.post("/api/stocks/ZZZZ/refresh-quote")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: submit + poll (manager-level via the API)
# ---------------------------------------------------------------------------

def test_refresh_quote_submit_returns_202_and_job_id(web_db: Path) -> None:
    client = _make_client(web_db)
    r = client.post("/api/stocks/AAA/refresh-quote")
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body


def test_refresh_quote_poll_to_done_writes_new_snapshot_rows(web_db: Path) -> None:
    """A quote refresh must INSERT a new market_snapshots + analyst_snapshots
    row (never clobber the rows written by a prior full collection)."""
    before_snapshots = _snapshot_count(web_db, "AAA")
    before_analyst = _analyst_count(web_db, "AAA")

    client = _make_client(web_db)
    r = client.post("/api/stocks/AAA/refresh-quote")
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    final = _wait_until_done(client, job_id)
    assert final["state"] == "done"

    assert _snapshot_count(web_db, "AAA") == before_snapshots + 1
    assert _analyst_count(web_db, "AAA") == before_analyst + 1

    # DES should now show the fresh quote's price.
    des = client.get("/ui/stocks/AAA/des")
    assert des.status_code == 200
    assert "$123.45" in des.text


def _rate_limited_client(web_db: Path, monkeypatch) -> TestClient:
    """A client whose quote refreshes go through the real YahooHandler while Yahoo answers 429."""
    import src.fetchers.yahoo_handler as yh

    class RateLimitedTicker:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker

        def __getattr__(self, name: str) -> Any:
            raise Exception("Too Many Requests. Rate limited. Try after a while.")

    monkeypatch.setattr(yh.yf, "Ticker", RateLimitedTicker)
    client = _make_client(web_db)
    client.app.state.job_manager.quote_fetcher_factory = lambda config: yh.YahooHandler(rate_limit_delay=0)
    return client


def test_a_rate_limited_refresh_reports_an_error_and_keeps_the_latest_snapshot(web_db: Path, monkeypatch) -> None:
    # A Yahoo 429 leaves every quote extractor empty. The refresh used to count as a success and write a
    # snapshot holding only carried-forward fields, so the page lost its price, verdict and upside.
    before = _snapshot_count(web_db, "AAA")
    client = _rate_limited_client(web_db, monkeypatch)
    final = _wait_until_done(client, client.post("/api/stocks/AAA/refresh-quote").json()["job_id"])
    assert (final["summary"]["successful"], final["summary"]["with_errors"]) == (0, 1)
    assert _snapshot_count(web_db, "AAA") == before


def test_a_failed_refresh_says_so_on_the_stock_page(web_db: Path, monkeypatch) -> None:
    # The status fragment said "Quote refreshed." for every finished job, including one whose only ticker failed.
    client = _rate_limited_client(web_db, monkeypatch)
    job_id = client.post("/api/stocks/AAA/refresh-quote").json()["job_id"]
    _wait_until_done(client, job_id)
    body = client.get(f"/ui/stocks/AAA/refresh-status/{job_id}").text
    assert "Refresh failed" in body and "Yahoo returned no price" in body
    assert "Quote refreshed" not in body and "/ui/stocks/AAA/des" not in body


def _quote_with_splits_client(web_db: Path, splits: List[Dict[str, Any]],
                              stored: List[Dict[str, Any]] = ()) -> TestClient:  # type: ignore[assignment]
    """AAA last collected with Yahoo on 2024-06-15 (its bars end 2023-09-17, its stored split is 2:1 on
    2023-06-01), then SEC-only on 2024-08-01; a quote whose split history is *splits*."""
    conn = sqlite3.connect(web_db)
    conn.executemany("INSERT INTO collection_runs (ticker, collected_at, data_sources) VALUES ('AAA', ?, ?)",
                     [("2024-06-15T09:00:00", "yahoo_finance, sec_edgar"), ("2024-08-01T09:00:00", "sec_edgar")])
    conn.executemany("INSERT INTO split_events (ticker, date, ratio) VALUES ('AAA', ?, ?)",
                     [(s["date"], s["ratio"]) for s in stored])
    conn.commit()
    conn.close()
    client = _make_client(web_db)
    canned = {**_CANNED_QUOTE, "splits": splits}
    client.app.state.job_manager.quote_fetcher_factory = lambda config: FakeQuoteHandler(canned)
    return client


@pytest.mark.parametrize("split_day", ["2024-06-15", "2024-07-01"])
def test_a_refresh_after_a_split_since_the_last_collection_keeps_the_stored_quote(web_db: Path, split_day: str) -> None:
    # BUG-093 (code review 9): the quote job writes only a market snapshot, so after a split since the last full
    # collection the post-split price sat beside valuations, dividends and bars on the old basis (+420% and "cheap"
    # where a full collection gives -48% and "expensive"), and no backfill could tell. A split on the day of the last
    # collection with Yahoo may have come after it; an SEC-only run since doesn't bring it in.
    before = _snapshot_count(web_db, "AAA")
    client = _quote_with_splits_client(
        web_db, [{"date": "2023-06-01", "ratio": 2.0}, {"date": split_day, "ratio": 10.0}])
    job_id = client.post("/api/stocks/AAA/refresh-quote").json()["job_id"]
    final = _wait_until_done(client, job_id)
    assert (final["summary"]["successful"], final["summary"]["with_errors"]) == (0, 1)
    assert _snapshot_count(web_db, "AAA") == before
    body = client.get(f"/ui/stocks/AAA/refresh-status/{job_id}").text
    assert "Refresh failed" in body and f"10-for-1 split on {split_day}" in body and "Collect AAA in full" in body


@pytest.mark.parametrize("splits, stored", [
    ([{"date": "2023-06-01", "ratio": 2.0}], []),  # the split the last collection stored
    ([{"date": "2021-03-01", "ratio": 3.0}, {"date": "2023-06-01", "ratio": 2.0}], []),  # one from before it
    ([{"date": "2023-06-01", "ratio": 2.0}, {"date": "2024-01-10", "ratio": 3.0}], []),  # after the last bar, before it
    ([{"date": "2024-06-15", "ratio": 10.0}], [{"date": "2024-06-15", "ratio": 10.0}]),  # its day's, stored
    ([{"date": "2024-07-01", "ratio": 1.0}], []),  # a 1-for-1 entry, which restates nothing
])
def test_a_refresh_without_a_split_since_the_last_collection_saves_the_quote(
        web_db: Path, splits: List[Dict[str, Any]], stored: List[Dict[str, Any]]) -> None:
    before = _snapshot_count(web_db, "AAA")
    client = _quote_with_splits_client(web_db, splits, stored)
    final = _wait_until_done(client, client.post("/api/stocks/AAA/refresh-quote").json()["job_id"])
    assert final["summary"]["successful"] == 1
    assert _snapshot_count(web_db, "AAA") == before + 1


def _later_run(web_db: Path, day: str = "2024-10-01", price: Optional[float] = None) -> None:
    """A later collection of AAA with Yahoo on *day*: its quote failed (no snapshot) unless it priced AAA at *price*."""
    conn = sqlite3.connect(web_db)
    conn.execute("INSERT INTO collection_runs (ticker, collected_at, data_sources) VALUES ('AAA', ?, ?)",
                 (f"{day}T09:00:00", "yahoo_finance, sec_edgar"))
    if price is not None:
        conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price) VALUES ('AAA', ?, ?)",
                     (f"{day}T09:00:00", price))
    conn.commit()
    conn.close()


def _refresh_summary(client: TestClient, ticker: str = "AAA") -> Dict[str, Any]:
    return _wait_until_done(client, client.post(f"/api/stocks/{ticker}/refresh-quote").json()["job_id"])["summary"]


@pytest.mark.parametrize("split_day, stored", [
    ("2024-07-01", True),   # the later run stored the split (and its bars and dividends), but no price
    ("2024-07-01", False),  # every Yahoo request of the later run failed, yet its run row names Yahoo
    ("2024-06-15", False),  # the same, for a split on the stored price's day
])
def test_a_refresh_after_a_split_the_stored_price_predates_keeps_the_stored_quote(
        web_db: Path, split_day: str, stored: bool) -> None:
    # BUG-102 (code review 10): the refusal looked only for splits missing from `split_events` since the last run that
    # names Yahoo. So a collection after the split whose quote failed (storing the split) or all of whose Yahoo requests
    # failed (storing nothing) let the refresh save the post-split price beside valuations on the stored price's basis:
    # +411.2% and "cheap", where a backfill gives -48.6%.
    _later_run(web_db)
    split = {"date": split_day, "ratio": 10.0}
    before = _snapshot_count(web_db, "AAA")
    client = _quote_with_splits_client(web_db, [{"date": "2023-06-01", "ratio": 2.0}, split], [split] if stored else [])
    summary = _refresh_summary(client)
    assert (summary["successful"], summary["with_errors"]) == (0, 1)
    assert _snapshot_count(web_db, "AAA") == before


def test_a_refresh_after_a_collection_that_priced_the_split_saves_the_quote(web_db: Path) -> None:
    # The collection after the split got Yahoo's quote too: the stored price and valuations are on the new basis.
    _later_run(web_db, price=11.0)
    split = {"date": "2024-07-01", "ratio": 10.0}
    before = _snapshot_count(web_db, "AAA")
    client = _quote_with_splits_client(web_db, [{"date": "2023-06-01", "ratio": 2.0}, split], [split])
    assert _refresh_summary(client)["successful"] == 1
    assert _snapshot_count(web_db, "AAA") == before + 1


def test_a_split_yahoo_lists_late_is_refused_after_a_refresh_saved_before_it(web_db: Path) -> None:
    # Yahoo can list a split days after it: a refresh saved the post-split price on 2024-07-10, before the 2024-07-01
    # split was listed, so the stored price postdates the split while the valuations, from the last collection with
    # Yahoo (2024-06-15), don't.
    conn = sqlite3.connect(web_db)
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price) VALUES ('AAA', ?, ?)",
                 ("2024-07-10T09:00:00", 11.0))
    conn.commit()
    conn.close()
    before = _snapshot_count(web_db, "AAA")
    client = _quote_with_splits_client(
        web_db, [{"date": "2023-06-01", "ratio": 2.0}, {"date": "2024-07-01", "ratio": 10.0}])
    summary = _refresh_summary(client)
    assert (summary["successful"], summary["with_errors"]) == (0, 1)
    assert _snapshot_count(web_db, "AAA") == before


def test_a_latest_snapshot_without_a_price_leaves_a_stored_split_alone(web_db: Path) -> None:
    # The engine reads no price when the latest snapshot has none (one written before BUG-033's fix), and then applies
    # every stored split, so a stored one is no reason to refuse.
    conn = sqlite3.connect(web_db)
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price) VALUES ('AAA', ?, NULL)",
                 ("2024-10-01T09:00:00",))
    conn.commit()
    conn.close()
    split = {"date": "2024-10-05", "ratio": 10.0}
    client = _quote_with_splits_client(web_db, [{"date": "2023-06-01", "ratio": 2.0}, split], [split])
    assert _refresh_summary(client)["successful"] == 1


def test_a_ticker_without_a_stored_price_is_checked_against_its_last_collection(web_db: Path) -> None:
    # EEE has no snapshot: a split since its last collection with Yahoo is still refused.
    conn = sqlite3.connect(web_db)
    conn.execute("INSERT INTO collection_runs (ticker, collected_at, data_sources) VALUES ('EEE', ?, ?)",
                 ("2024-06-15T09:00:00", "yahoo_finance, sec_edgar"))
    conn.commit()
    conn.close()
    client = _make_client(web_db)
    canned = {**_CANNED_QUOTE, "splits": [{"date": "2024-07-01", "ratio": 10.0}]}
    client.app.state.job_manager.quote_fetcher_factory = lambda config: FakeQuoteHandler(canned)
    summary = _refresh_summary(client, "EEE")
    assert (summary["successful"], summary["with_errors"]) == (0, 1)
    assert _snapshot_count(web_db, "EEE") == 0


@pytest.mark.parametrize("ratio", [1.01, 0.9535, 1.057])  # SCCO's stock dividend, HON's and SPGI's spin-off factors
def test_a_stock_dividend_or_a_spin_off_factor_doesnt_stop_a_refresh(web_db: Path, ratio: float) -> None:
    # BUG-106 (code review 10): every factor in Yahoo's split history but 1.0 was refused as a split. SCCO pays a stock
    # dividend almost every quarter ("a 1.01-for-1 split"), so its refreshes were refused most of the time, though only
    # a share split, or a factor of 3:2 or more, moves the stored per-share figures; the next full collection brings
    # the smaller factors' price history and dividends over.
    before = _snapshot_count(web_db, "AAA")
    client = _quote_with_splits_client(
        web_db, [{"date": "2023-06-01", "ratio": 2.0}, {"date": "2024-07-01", "ratio": ratio}])
    assert _refresh_summary(client)["successful"] == 1
    assert _snapshot_count(web_db, "AAA") == before + 1


@pytest.mark.parametrize("ratio, named", [
    (1.25, "a 5-for-4 split on 2024-07-01"),  # a share split under 3:2
    (0.1, "a 1-for-10 split on 2024-07-01"),  # a reverse split
    (2.0842, "a 2.0842 price adjustment"),  # ABT's AbbVie spin-off factor: no split, but big enough to move values
])
def test_a_split_or_a_large_price_factor_is_refused_by_its_name(web_db: Path, ratio: float, named: str) -> None:
    before = _snapshot_count(web_db, "AAA")
    client = _quote_with_splits_client(
        web_db, [{"date": "2023-06-01", "ratio": 2.0}, {"date": "2024-07-01", "ratio": ratio}])
    summary = _refresh_summary(client)
    assert (summary["successful"], summary["with_errors"]) == (0, 1)
    assert named in summary["errors"]["AAA"]
    assert _snapshot_count(web_db, "AAA") == before


def test_a_quote_carries_yahoos_split_history(monkeypatch) -> None:
    # What the quote job checks a refresh against (BUG-093): the split history from Yahoo's chart request.
    import pandas as pd

    import src.fetchers.yahoo_handler as yh

    class SplitTicker:
        def __init__(self, ticker: str) -> None:
            self.info = {"currentPrice": 12.0, "regularMarketPrice": 12.0}
            self.splits = pd.Series([2.0, 10.0], index=pd.to_datetime(["2023-06-01", "2024-07-01"]))

    monkeypatch.setattr(yh.yf, "Ticker", SplitTicker)
    quote = yh.YahooHandler(rate_limit_delay=0).fetch_quote("AAA")
    assert quote["splits"] == [{"date": "2023-06-01", "ratio": 2.0}, {"date": "2024-07-01", "ratio": 10.0}]


def test_refresh_quote_job_summary_notes_mode(web_db: Path) -> None:
    client = _make_client(web_db)
    r = client.post("/api/stocks/AAA/refresh-quote")
    job_id = r.json()["job_id"]
    final = _wait_until_done(client, job_id)
    assert final["summary"] is not None
    assert final["summary"].get("mode") == "quote"


def test_full_mode_job_unaffected_by_quote_mode(web_db: Path) -> None:
    """Submitting via the existing full-collection endpoint must still work
    (mode="full" is untouched by the new quote-mode branch)."""
    settings = WebSettings(db_path=web_db, allow_collection=True)
    app = create_app(settings=settings)
    app.state.job_manager.fetcher_factory = fake_full_factory
    client = TestClient(app)

    r = client.post("/api/collection/jobs", json={"tickers": ["AAA"]})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    final = _wait_until_done(client, job_id)
    assert final["state"] == "done"
    assert final["completed"] == 1


# ---------------------------------------------------------------------------
# Tests: serialization — a quote job and a full job never overlap
# ---------------------------------------------------------------------------

def test_quote_and_full_jobs_serialize(web_db: Path) -> None:
    active: List[int] = [0]
    peak: List[int] = [0]
    conc_lock = threading.Lock()

    class TrackingQuoteHandler:
        def fetch_quote(self, ticker: str) -> Dict[str, Any]:
            with conc_lock:
                active[0] += 1
                if active[0] > peak[0]:
                    peak[0] = active[0]
            time.sleep(0.15)
            with conc_lock:
                active[0] -= 1
            return dict(_CANNED_QUOTE)

    class TrackingFullFetcher:
        def __enter__(self) -> "TrackingFullFetcher":
            with conc_lock:
                active[0] += 1
                if active[0] > peak[0]:
                    peak[0] = active[0]
            time.sleep(0.15)
            return self

        def __exit__(self, *args: Any) -> None:
            with conc_lock:
                active[0] -= 1

        def fetch_ticker(
            self,
            ticker: str,
            include_yahoo: bool = True,
            include_sec: bool = True,
            years_back: Optional[int] = None,
        ) -> StockData:
            return StockData(ticker=ticker)

        def export(
            self,
            data: List[StockData],
            formats: Optional[List[str]] = None,
        ) -> Dict[str, Any]:
            return {}

    def tracking_quote_factory(config: Any) -> TrackingQuoteHandler:
        return TrackingQuoteHandler()

    def tracking_full_factory(config: Any) -> TrackingFullFetcher:
        return TrackingFullFetcher()

    settings = WebSettings(db_path=web_db, allow_collection=True, allow_quote_refresh=True)
    app = create_app(settings=settings)
    app.state.job_manager.quote_fetcher_factory = tracking_quote_factory
    app.state.job_manager.fetcher_factory = tracking_full_factory

    with TestClient(app) as client:
        r1 = client.post("/api/stocks/AAA/refresh-quote")
        r2 = client.post("/api/collection/jobs", json={"tickers": ["BBB"]})
        assert r1.status_code == 202
        assert r2.status_code == 202
        id1 = r1.json()["job_id"]
        id2 = r2.json()["job_id"]

        _wait_until_done(client, id1, timeout=10.0)
        _wait_until_done(client, id2, timeout=10.0)

    assert peak[0] <= 1, f"Max concurrency was {peak[0]}; expected <= 1 (serialization broken)"


# ---------------------------------------------------------------------------
# Tests: WebSettings.from_env
# ---------------------------------------------------------------------------

def test_from_env_allow_quote_refresh_defaults_true_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("STOCK_WEB_ALLOW_QUOTE_REFRESH", raising=False)
    settings = WebSettings.from_env()
    assert settings.allow_quote_refresh is True


def test_from_env_allow_quote_refresh_parses_falsy(monkeypatch) -> None:
    monkeypatch.setenv("STOCK_WEB_ALLOW_QUOTE_REFRESH", "0")
    settings = WebSettings.from_env()
    assert settings.allow_quote_refresh is False


def test_from_env_allow_quote_refresh_parses_truthy(monkeypatch) -> None:
    monkeypatch.setenv("STOCK_WEB_ALLOW_QUOTE_REFRESH", "true")
    settings = WebSettings.from_env()
    assert settings.allow_quote_refresh is True


# ---------------------------------------------------------------------------
# Tests: DES button rendering
# ---------------------------------------------------------------------------

def test_des_renders_live_refresh_button_when_enabled(web_db: Path) -> None:
    client = _make_client(web_db, allow_quote_refresh=True)
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    body = resp.text
    assert 'hx-post="/api/stocks/AAA/refresh-quote"' in body
    # Must not be the disabled placeholder button.
    refresh_start = body.index("REFRESH")
    snippet = body[max(0, refresh_start - 200) : refresh_start]
    assert "disabled" not in snippet


def test_des_renders_disabled_refresh_button_when_disabled(web_db: Path) -> None:
    client = _make_client(web_db, allow_quote_refresh=False)
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    body = resp.text
    assert "REFRESH" in body
    assert "disabled" in body
    assert 'hx-post="/api/stocks/AAA/refresh-quote"' not in body


# ---------------------------------------------------------------------------
# Tests: poll fragment endpoint
# ---------------------------------------------------------------------------

def test_refresh_status_fragment_serves_running_then_done(web_db: Path) -> None:
    client = _make_client(web_db)
    r = client.post("/api/stocks/AAA/refresh-quote")
    job_id = r.json()["job_id"]

    # Poll the HTML fragment endpoint directly.
    deadline = time.monotonic() + 5.0
    last_body = ""
    while time.monotonic() < deadline:
        poll = client.get(f"/ui/stocks/AAA/refresh-status/{job_id}")
        assert poll.status_code == 200
        last_body = poll.text
        if "every 2s" not in last_body:
            break
        time.sleep(0.05)
    else:
        raise TimeoutError("refresh-status fragment never reached terminal state")

    # Terminal (done) fragment triggers a DES reload, not further polling.
    assert "every 2s" not in last_body
    assert "/ui/stocks/AAA/des" in last_body
