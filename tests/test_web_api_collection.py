"""Tests for data-collection API (Task 8).

All tests use a fake fetcher — zero network I/O.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient

from src.models.stock_data import StockData
from src.webapp import create_app
from src.webapp.settings import WebSettings

# ---------------------------------------------------------------------------
# Fake fetcher
# ---------------------------------------------------------------------------

class FakeFetcher:
    """Minimal fetcher context-manager that never touches the network."""

    def __enter__(self) -> "FakeFetcher":
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


def fake_factory(config: Any) -> FakeFetcher:  # type: ignore[return]
    return FakeFetcher()


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


def _make_client(web_db: Path, allow_collection: bool = True) -> TestClient:
    settings = WebSettings(db_path=web_db, allow_collection=allow_collection)
    app = create_app(settings=settings)
    # Inject fake fetcher so no network I/O occurs
    app.state.job_manager.fetcher_factory = fake_factory
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests: gating
# ---------------------------------------------------------------------------

def test_disabled_returns_409(web_db: Path) -> None:
    """POST /api/collection/jobs → 409 when allow_collection is False."""
    client = _make_client(web_db, allow_collection=False)
    r = client.post("/api/collection/jobs", json={"tickers": ["AAPL"]})
    assert r.status_code == 409
    assert "disabled" in r.json()["detail"].lower()


def test_empty_tickers_returns_400(web_db: Path) -> None:
    """POST /api/collection/jobs with empty tickers list → 400."""
    client = _make_client(web_db)
    r = client.post("/api/collection/jobs", json={"tickers": []})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Tests: submit + poll
# ---------------------------------------------------------------------------

def test_submit_returns_202_and_job_id(web_db: Path) -> None:
    """POST /api/collection/jobs → 202 with job_id."""
    client = _make_client(web_db)
    r = client.post("/api/collection/jobs", json={"tickers": ["AAPL", "MSFT"]})
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["state"] in ("queued", "running")


def test_poll_to_done(web_db: Path) -> None:
    """Submit a job; poll until done; assert completed == total == n tickers."""
    client = _make_client(web_db)
    tickers = ["AAPL", "MSFT", "GOOG"]
    r = client.post("/api/collection/jobs", json={"tickers": tickers})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    final = _wait_until_done(client, job_id)
    assert final["state"] == "done"
    assert final["completed"] == len(tickers)
    assert final["total"] == len(tickers)


def test_list_jobs(web_db: Path) -> None:
    """GET /api/collection/jobs lists submitted jobs."""
    client = _make_client(web_db)
    r = client.post("/api/collection/jobs", json={"tickers": ["AAPL"]})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    _wait_until_done(client, job_id)

    jobs = client.get("/api/collection/jobs").json()
    assert isinstance(jobs, list)
    assert any(j["job_id"] == job_id for j in jobs)


def test_unknown_job_returns_404(web_db: Path) -> None:
    """GET /api/collection/jobs/{unknown} → 404."""
    client = _make_client(web_db)
    r = client.get("/api/collection/jobs/doesnotexist12345")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: serialization (max concurrency ≤ 1)
# ---------------------------------------------------------------------------

def test_serialization_max_one_concurrent_writer(web_db: Path) -> None:
    """Two queued jobs must never run concurrently (max_workers=1).

    Uses a tracking fake that records peak concurrency. If serialization is
    broken the peak would be 2; correctly it must be ≤ 1.
    """
    active: List[int] = [0]
    peak: List[int] = [0]
    conc_lock = threading.Lock()

    class TrackingFetcher:
        def __enter__(self) -> "TrackingFetcher":
            with conc_lock:
                active[0] += 1
                if active[0] > peak[0]:
                    peak[0] = active[0]
            # Pause long enough for a second job to start if concurrency existed
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

    def tracking_factory(config: Any) -> TrackingFetcher:
        return TrackingFetcher()

    settings = WebSettings(db_path=web_db, allow_collection=True)
    app = create_app(settings=settings)
    app.state.job_manager.fetcher_factory = tracking_factory

    with TestClient(app) as client:
        r1 = client.post("/api/collection/jobs", json={"tickers": ["AAA"]})
        r2 = client.post("/api/collection/jobs", json={"tickers": ["BBB"]})
        assert r1.status_code == 202
        assert r2.status_code == 202
        id1 = r1.json()["job_id"]
        id2 = r2.json()["job_id"]

        # Wait for both to finish
        _wait_until_done(client, id1, timeout=10.0)
        _wait_until_done(client, id2, timeout=10.0)

    assert peak[0] <= 1, f"Max concurrency was {peak[0]}; expected ≤ 1 (serialization broken)"


# ---------------------------------------------------------------------------
# Tests: UI pages (smoke)
# ---------------------------------------------------------------------------

def test_collect_page_disabled(web_db: Path) -> None:
    """/collect renders even when collection is disabled (shows disabled message)."""
    client = _make_client(web_db, allow_collection=False)
    r = client.get("/collect")
    assert r.status_code == 200
    assert b"disabled" in r.content.lower()


def test_collect_page_enabled(web_db: Path) -> None:
    """/collect renders the form when collection is enabled."""
    client = _make_client(web_db)
    r = client.get("/collect")
    assert r.status_code == 200
    # Should contain a form or input for tickers
    assert b"ticker" in r.content.lower() or b"collect" in r.content.lower()
