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
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient

from src.models.stock_data import StockData
from src.webapp import create_app
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
