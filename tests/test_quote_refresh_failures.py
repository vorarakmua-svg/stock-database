"""A write that failed is never reported or counted as success (BUG-053, BUG-237, BUG-163). A quote refresh whose
database write fails stores nothing and is that company's error: in ``refresh_quote``, the web's quote job and
``stock-data refresh``'s quotes step. A refresh whose valuation on a new price factor fails keeps the price, says so in
the job's errors, and the ticker's next refresh values it again (its valuations predate a stored factor). The failures
are SQLite's own: another writer holds the lock past the wait (``refresh_fakes.locked``)."""
import sqlite3
import time
from typing import Any, Dict, List

import pytest

import src.valuation.engine as engine
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers.quote_refresh import refresh_quote
from src.webapp.jobs import CollectionJobManager
from tests import refresh_fakes as rf

#: A stock dividend Yahoo lists the day after the stored collection (2026-10-21), and the old valuations' day.
FACTOR = {"date": "2026-10-22", "ratio": 1.01}
VALUED = "2026-10-21T13:00:00+00:00"


class _Handler:
    def __init__(self, answer: Dict[str, Any]) -> None:
        self.answer = answer

    def fetch_quote(self, ticker: str) -> Dict[str, Any]:
        return dict(self.answer)


def _snapshots(db, ticker="AAA"):
    return rf.rows(db, "SELECT collected_at, current_price FROM market_snapshots WHERE ticker = ? "
                       "ORDER BY collected_at", ticker)


def _valued_at(db, ticker="AAA"):
    return rf.rows(db, "SELECT computed_at FROM valuation_summary WHERE ticker = ?", ticker)[0][0]


def _wait(manager, job_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job.state in ("done", "error"):
            return job
        time.sleep(0.02)
    raise TimeoutError(job_id)


def _job(db, quote, tickers=("AAA",)):
    manager = CollectionJobManager(db, quote_fetcher_factory=lambda config: _Handler(quote))
    try:
        return _wait(manager, manager.submit(list(tickers), mode="quote"))
    finally:
        manager.shutdown()


# ---- the quote's write (BUG-237, BUG-053) -------------------------------------------------------------------------

def test_a_failed_quote_write_raises(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")
    before = _snapshots(db)
    with rf.locked(db, monkeypatch):
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            SQLiteStore(db).upsert_quote("AAA", rf.quote("AAA", 111.0), "2026-10-25T05:00:00")
    assert _snapshots(db) == before


def test_a_quote_whose_write_fails_is_an_error_and_not_stored(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")
    before = _snapshots(db)
    with rf.locked(db, monkeypatch):
        result = refresh_quote(_Handler(rf.quote("AAA", 111.0)), SQLiteStore(db), "AAA", "2026-10-25T05:00:00")
    assert (result.outcome, result.saved, result.message) == ("error", False, "database is locked")
    assert _snapshots(db) == before


def test_a_failed_write_without_a_message_is_named_by_its_type(tmp_path, monkeypatch):
    def upsert_quote(*args, **kwargs):
        raise RuntimeError()

    db = rf.make_db(tmp_path / "stock.db")
    store = SQLiteStore(db)
    monkeypatch.setattr(store, "upsert_quote", upsert_quote)
    result = refresh_quote(_Handler(rf.quote("AAA", 111.0)), store, "AAA")
    assert (result.outcome, result.message) == ("error", "RuntimeError")


def test_the_web_job_counts_a_failed_quote_write_as_that_companys_error(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")
    with rf.locked(db, monkeypatch):
        job = _job(db, rf.quote("AAA", 111.0))
    assert job.state == "done"
    assert (job.summary["successful"], job.summary["with_errors"]) == (0, 1)
    assert job.summary["errors"] == {"AAA": "database is locked"}


def test_the_refresh_quotes_step_counts_a_failed_write_as_an_error(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")
    with rf.locked(db, monkeypatch):
        report = rf.run(db, rf.services(db), steps=["quotes"])
    quotes = report.result("quotes")
    assert quotes.summary["stored"] == 0 and not quotes.stored
    assert quotes.summary["errors"] == dict.fromkeys(rf.TICKERS, "database is locked")
    assert (quotes.ok, quotes.reason) == (False, "every quote failed")
    assert quotes.headline.startswith("0 of 3 quotes stored")


# ---- the valuation on a new factor (BUG-163) ----------------------------------------------------------------------

def _valued_before_the_factor(tmp_path) -> Any:
    """AAA collected 2026-10-21 and valued that day (its valuations predate FACTOR)."""
    db = rf.make_db(tmp_path / "stock.db")
    assert engine.compute_and_store(db, tickers=["AAA"]) == 1
    conn = sqlite3.connect(db)
    conn.execute("UPDATE valuation_summary SET computed_at = ?", (VALUED,))
    conn.commit()
    conn.close()
    return db


def _engine(monkeypatch, db, lock: List[bool]) -> List[List[str]]:
    """The real engine, its writes failing ("database is locked") while ``lock[0]``; returns the tickers each run
    was asked to value."""
    real = engine.compute_and_store
    runs: List[List[str]] = []

    def compute(db_path, tickers=None, logger=None, **kwargs):
        runs.append(list(tickers or []))
        if not lock[0]:
            return real(db_path, tickers=tickers, logger=logger, **kwargs)
        with rf.locked(db, monkeypatch):
            return real(db_path, tickers=tickers, logger=logger, **kwargs)

    monkeypatch.setattr("src.valuation.engine.compute_and_store", compute)
    return runs


def test_a_failed_valuation_on_a_new_factor_is_the_jobs_error(tmp_path, monkeypatch):
    db = _valued_before_the_factor(tmp_path)
    runs = _engine(monkeypatch, db, [True])
    before = len(_snapshots(db))
    job = _job(db, rf.quote("AAA", 111.0, splits=[FACTOR]))
    assert runs == [["AAA"]]
    assert (job.summary["successful"], job.summary["with_errors"]) == (0, 1)
    assert job.summary["errors"] == {"AAA": (
        "The price was stored, but valuing AAA on a 1.01 price adjustment (a spin-off or merger) on 2026-10-22 "
        "failed: database is locked. Its next refresh values it again.")}
    # The price and the factor are stored; the valuations are the old ones.
    assert len(_snapshots(db)) == before + 1 and _snapshots(db)[-1][1] == 111.0
    assert rf.rows(db, "SELECT date, ratio FROM split_events WHERE ticker = 'AAA'") == [("2026-10-22", 1.01)]
    assert _valued_at(db) == VALUED


def test_the_next_refresh_values_a_ticker_whose_valuation_failed(tmp_path, monkeypatch):
    db = _valued_before_the_factor(tmp_path)
    lock = [True]
    runs = _engine(monkeypatch, db, lock)
    _job(db, rf.quote("AAA", 111.0, splits=[FACTOR]))
    lock[0] = False
    # The factor is now stored, before the stored price: not new, but the valuations predate it.
    job = _job(db, rf.quote("AAA", 112.0, splits=[FACTOR]))
    assert runs == [["AAA"], ["AAA"]]
    assert (job.summary["successful"], job.summary["with_errors"], job.summary["errors"]) == (1, 0, {})
    assert _valued_at(db) > VALUED
    # Valued on it, the next refresh is an ordinary one.
    job = _job(db, rf.quote("AAA", 113.0, splits=[FACTOR]))
    assert runs == [["AAA"], ["AAA"]] and job.summary["successful"] == 1


@pytest.mark.parametrize("compute, said", [
    (rf.Recorder(1), "1 of 3 quotes stored (1 with a price factor, valued on it: CCC)"),
    (rf.Recorder(0), "1 of 3 quotes stored (1 with a price factor: CCC; valuing 1 of them on it failed)"),
    (rf.Recorder(raises=sqlite3.OperationalError("database is locked")),
     "1 of 3 quotes stored (1 with a price factor: CCC; valuing 1 of them on it failed)"),
])
def test_the_refresh_quotes_step_says_when_valuing_a_factor_failed(tmp_path, compute, said):
    # The CLI's twin of BUG-163: its headline said "valued on it" whatever the valuation did. The recompute step (or
    # the next run's quotes step: the valuations predate the stored factor) values it again.
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(quotes={"AAA": {"error": "down"}, "BBB": {"error": "down"},
                                 "CCC": rf.quote("CCC", splits=[FACTOR])})
    quotes = rf.run(db, rf.services(db, yahoo=yahoo, compute=compute), steps=["quotes"]).result("quotes")
    assert quotes.headline == said + ", 0 to collect, 2 errors"


def test_a_failed_valuation_names_only_the_tickers_it_failed(tmp_path, monkeypatch):
    db = _valued_before_the_factor(tmp_path)
    real = SQLiteStore.export_valuations

    def export_valuations(store, ticker, *args, **kwargs):
        if ticker == "BBB":
            raise sqlite3.OperationalError("disk I/O error")
        return real(store, ticker, *args, **kwargs)

    monkeypatch.setattr(SQLiteStore, "export_valuations", export_valuations)
    failures: Dict[str, str] = {}
    assert engine.compute_and_store(db, tickers=["AAA", "BBB", "CCC"], failures=failures) == 2
    assert failures == {"BBB": "disk I/O error"}


@pytest.mark.parametrize("stored, valued, unvalued", [
    ([FACTOR], VALUED, [FACTOR]),                                     # dated after the valuations' day
    ([FACTOR], "2026-10-22T01:00:00+00:00", []),                      # valued on its day
    ([{"date": "2026-10-20", "ratio": 1.01}], VALUED, []),             # before the valuations
    ([{"date": "2026-10-22", "ratio": 1.0}], VALUED, []),              # no factor
    ([{"date": "2026-10-22", "ratio": 0.0}], VALUED, []),              # not a factor
    ([{"date": "2026-10-23", "ratio": 2.0}, FACTOR], VALUED,         # every one, in date order
     [FACTOR, {"date": "2026-10-23", "ratio": 2.0}]),
])
def test_the_factors_the_valuations_predate(tmp_path, stored, valued, unvalued):
    db = _valued_before_the_factor(tmp_path)
    conn = sqlite3.connect(db)
    conn.executemany("INSERT OR REPLACE INTO split_events (ticker, date, ratio) VALUES ('AAA', ?, ?)",
                     [(s["date"], s["ratio"]) for s in stored])
    conn.execute("UPDATE valuation_summary SET computed_at = ?", (valued,))
    conn.commit()
    conn.close()
    assert SQLiteStore(db).unvalued_factors("AAA") == unvalued


def test_a_ticker_never_valued_has_no_factors_to_value_again(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('AAA', '2026-10-22', 1.01)")
    conn.commit()
    conn.close()
    assert SQLiteStore(db).unvalued_factors("AAA") == []
