"""The quote-only refresh both the web's quote job and ``stock-data refresh`` use (``src/fetchers/quote_refresh.py``;
docs/superpowers/specs/2026-10-25-universe-automation-design.md, "stock-data refresh"): Yahoo's quote saved; refused
after a split the stored valuations don't reflect (BUG-093, BUG-102), which only a full collection brings over; saved
with a stock dividend's or a spin-off's factor since (BUG-128), for the caller to value the ticker on; or Yahoo's
error. The web job's own behavior is tests/test_web_quote_refresh.py's."""
import time
from typing import Any, Dict, List

import pytest

import src.refresh
import src.webapp.jobs
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers import quote_refresh
from src.fetchers.quote_refresh import QuoteRefresh, describe_splits, refresh_quote, split_refusal
from src.webapp.jobs import CollectionJobManager
from tests import refresh_fakes as rf


class _Handler:
    def __init__(self, answer: Dict[str, Any]) -> None:
        self.answer = answer
        self.calls: List[str] = []

    def fetch_quote(self, ticker: str) -> Dict[str, Any]:
        self.calls.append(ticker)
        return dict(self.answer)


def _snapshots(db, ticker="AAA"):
    return rf.rows(db, "SELECT collected_at, current_price FROM market_snapshots WHERE ticker = ? "
                       "ORDER BY collected_at", ticker)


def _splits(db, ticker="AAA"):
    return dict(rf.rows(db, "SELECT date, ratio FROM split_events WHERE ticker = ?", ticker))


def test_a_quote_without_a_split_since_is_stored(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    result = refresh_quote(_Handler(rf.quote("AAA", 111.0)), SQLiteStore(db), "AAA", "2026-10-25T05:00:00")
    assert (result.ticker, result.outcome, result.message, result.price, result.saved) == (
        "AAA", "stored", None, 111.0, True)
    assert _snapshots(db)[-1] == ("2026-10-25T05:00:00", 111.0)
    assert _splits(db) == {}


def test_a_split_since_the_last_collection_is_refused_with_the_web_jobs_reason(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    split = {"date": "2026-10-22", "ratio": 10.0}
    result = refresh_quote(_Handler(rf.quote("AAA", splits=[split])), SQLiteStore(db), "AAA")
    assert (result.outcome, result.saved, result.splits) == ("split", False, [split])
    assert result.message == ("Yahoo shows a 10-for-1 split on 2026-10-22 that the stored valuations don't reflect, "
                              "so the quote wasn't saved. Collect AAA in full to update them.")
    assert len(_snapshots(db)) == 1 and _splits(db) == {}


def test_a_split_the_last_collection_stored_doesnt_stop_the_quote(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    result = refresh_quote(_Handler(rf.quote("AAA", splits=[{"date": "2026-10-01", "ratio": 2.0}])),
                           SQLiteStore(db), "AAA")
    assert result.outcome == "stored" and len(_snapshots(db)) == 2


@pytest.mark.parametrize("ratio", [1.01, 0.9535])  # SCCO's stock dividend, HON's spin-off factor
def test_a_price_factor_since_is_stored_with_the_quote(tmp_path, ratio):
    db = rf.make_db(tmp_path / "stock.db")
    factor = {"date": "2026-10-22", "ratio": ratio}
    result = refresh_quote(_Handler(rf.quote("AAA", splits=[factor])), SQLiteStore(db), "AAA")
    assert (result.outcome, result.saved, result.splits) == ("rebased", True, [factor])
    assert _splits(db) == {"2026-10-22": ratio} and len(_snapshots(db)) == 2


def test_a_failed_fetch_is_yahoos_error_and_stores_nothing(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    result = refresh_quote(_Handler({"error": "Yahoo returned no price"}), SQLiteStore(db), "AAA")
    assert (result.outcome, result.message, result.saved, result.price) == (
        "error", "Yahoo returned no price", False, None)
    assert len(_snapshots(db)) == 1


def test_the_collected_at_is_naive_local_by_default(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    refresh_quote(_Handler(rf.quote("AAA")), SQLiteStore(db), "AAA")
    latest = _snapshots(db)[-1][0]
    assert "+" not in latest and latest[:10] == quote_refresh.collected_at_now()[:10]


@pytest.mark.parametrize("ratio, named", [
    (10.0, "a 10-for-1 split on 2024-07-01"),
    (1.25, "a 5-for-4 split on 2024-07-01"),
    (0.1, "a 1-for-10 split on 2024-07-01"),
    (2.0842, "a 2.0842 price adjustment (a spin-off or merger) on 2024-07-01"),
])
def test_the_splits_are_named_as_the_web_job_names_them(ratio, named):
    splits = [{"date": "2024-07-01T00:00:00", "ratio": ratio}]
    assert describe_splits(splits) == named
    assert named in split_refusal("AAA", splits)


def test_two_splits_are_both_named():
    assert describe_splits([{"date": "2024-07-01", "ratio": 2.0}, {"date": "2025-01-02", "ratio": 3.0}]) == (
        "a 2-for-1 split on 2024-07-01, a 3-for-1 split on 2025-01-02")


def _wait(manager, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job.state in ("done", "error"):
            return job
        time.sleep(0.02)
    raise TimeoutError(job_id)


def test_the_web_quote_job_goes_through_the_shared_refresh(web_db, monkeypatch):
    calls = []
    real = src.webapp.jobs.refresh_quote

    def recording(handler, store, ticker, collected_at=None):
        calls.append(ticker)
        return real(handler, store, ticker, collected_at)

    monkeypatch.setattr(src.webapp.jobs, "refresh_quote", recording)
    manager = CollectionJobManager(web_db, quote_fetcher_factory=lambda config: _Handler(rf.quote("AAA")))
    try:
        job = _wait(manager, manager.submit(["AAA", "BBB"], mode="quote"))
    finally:
        manager.shutdown()
    assert calls == ["AAA", "BBB"]
    assert (job.state, job.summary["successful"], job.summary["with_errors"]) == ("done", 2, 0)


def test_the_web_quote_job_reports_the_shared_refreshs_outcomes(web_db, monkeypatch):
    outcomes = {"AAA": QuoteRefresh("AAA", "split", "a split reason"), "BBB": QuoteRefresh("BBB", "error", "down"),
                "CCC": QuoteRefresh("CCC", "rebased", price=1.0)}
    monkeypatch.setattr(src.webapp.jobs, "refresh_quote", lambda handler, store, ticker, collected_at=None:
                        outcomes[ticker])
    valued = []
    monkeypatch.setattr("src.valuation.engine.compute_and_store",
                        lambda db_path, tickers=None, logger=None: valued.append(list(tickers or [])) or 1)
    manager = CollectionJobManager(web_db, quote_fetcher_factory=lambda config: _Handler({}))
    try:
        job = _wait(manager, manager.submit(["AAA", "BBB", "CCC"], mode="quote"))
    finally:
        manager.shutdown()
    assert (job.summary["successful"], job.summary["with_errors"]) == (1, 2)
    assert job.summary["errors"] == {"AAA": "a split reason", "BBB": "down"}
    assert valued == [["CCC"]]


def test_the_refresh_quotes_step_goes_through_the_shared_refresh(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")
    calls = []
    real = src.refresh.refresh_quote

    def recording(handler, store, ticker, collected_at=None):
        calls.append(ticker)
        return real(handler, store, ticker, collected_at)

    monkeypatch.setattr(src.refresh, "refresh_quote", recording)
    rf.run(db, rf.services(db), steps=["quotes"])
    assert calls == list(rf.TICKERS)
    assert len(rf.rows(db, "SELECT * FROM market_snapshots")) == 6


def test_a_split_refusal_is_the_web_jobs_message():
    splits = [{"date": "2024-07-01", "ratio": 10.0}]
    assert split_refusal("AAA", splits) == (
        "Yahoo shows a 10-for-1 split on 2024-07-01 that the stored valuations don't reflect, so the quote wasn't "
        "saved. Collect AAA in full to update them.")


def test_the_web_job_module_no_longer_carries_its_own_copy():
    # One rule for both callers: the web job imports the shared function rather than keeping the logic itself.
    assert src.webapp.jobs.refresh_quote is quote_refresh.refresh_quote
    assert not hasattr(src.webapp.jobs, "_split_since_collection")
