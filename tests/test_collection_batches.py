"""A collection stores its companies batch by batch (code review 19, I-1; ``StockDataFetcher.fetch_and_export``).

The workers fetch without a pause between batches. Each time ``batch_size`` companies have arrived, and with the rest
at the end, the batch is exported (JSON, the CSVs merged, SQLite with each stock in its own savepoint) and the
companies it stored are valued; nothing of a batch is kept once it is exported. Ctrl+C cancels the tickers not started
and exports nothing half-done. ``stock-data`` prints how many companies were stored and exits 1 when any wasn't, 130
when interrupted. The fetches, the benchmark's and the valuations are fakes: nothing is collected.
"""
import gc
import logging
import sqlite3
import threading
import time
import weakref
from concurrent.futures import ALL_COMPLETED
from datetime import datetime

import pytest

import src.main
from src import config as app_config
from src.config import AppConfig, StorageConfig
from src.exporters.csv_exporter import CSVExporter
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers import stock_data_fetcher as sdf
from src.fetchers.stock_data_fetcher import StockDataFetcher
from src.models.stock_data import StockData

COLLECTED = datetime(2026, 10, 25, 12, 0)
TICKERS = [f"T{n:02d}" for n in range(1, 31)]


def _made(ticker, employees=100):
    """A company as a collection fetches it: a profile, a quote, two years of financials (an odd company's latest with
    a field of its own, so the history's columns grow batch by batch) and a bar; *employees* a dict, SQLite can't bind
    it."""
    n = sum(map(ord, ticker))
    stock = StockData(ticker=ticker, cik=f"{n:010d}", company_name=f"{ticker} Corp", sector_class="general",
                      collected_at=COLLECTED)
    stock.company_info = {"sector": "Industrials", "full_time_employees": employees}
    stock.market_data = {"current_price": 10.0 + n % 7, "market_cap": 1.0e9 + n}
    stock.financials_annual = {str(year): {"fiscal_year": year, "revenue": 1.0e9 + n * year, "net_income": 1.0e8 + n}
                               for year in (2024, 2025)}
    if n % 2:
        stock.financials_annual["2025"][f"extra_{ticker.lower()}"] = 1.0
    stock.price_bars = [{"date": "2026-10-23", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}]
    stock.data_sources = ["yahoo_finance", "sec_edgar"]
    return stock


@pytest.fixture
def events(monkeypatch):
    """The run's events in order: ("fetch", ticker) as each fetch starts, ("store", tickers) for each SQLite batch,
    ("benchmark",) for each benchmark fetch and ("value", tickers) for each valuation pass (a fake engine)."""
    seen = []

    def compute(db_path, tickers=None, logger=None):
        seen.append(("value", list(tickers)))
        return len(tickers)

    monkeypatch.setattr("src.valuation.engine.compute_and_store", compute)
    return seen


def _fetcher(base, monkeypatch, events, workers=1, batch_size=None, made=_made):
    """A real fetcher on *base* whose fetches are *made*'s, recording the events; the benchmark's bars a fake."""
    config = AppConfig(storage=StorageConfig(base_dir=base), max_workers=workers)
    if batch_size is not None:
        config.batch_size = batch_size
    fetcher = StockDataFetcher(config=config)

    def fetch(ticker, include_yahoo=True, include_sec=True, years_back=None):
        events.append(("fetch", ticker))
        return made(ticker)

    def benchmark(symbol="^GSPC"):
        events.append(("benchmark",))
        return [{"date": "2026-10-23", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}]

    monkeypatch.setattr(fetcher, "fetch_ticker", fetch)
    monkeypatch.setattr(fetcher.yahoo_handler, "fetch_benchmark_bars", benchmark)
    write = fetcher.sqlite_store.write_batch

    def store(data):
        events.append(("store", [s.ticker for s in data]))
        return write(data)

    monkeypatch.setattr(fetcher.sqlite_store, "write_batch", store)
    return fetcher


def _stored(db):
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    try:
        return [row[0] for row in conn.execute("SELECT ticker FROM companies ORDER BY ticker")]
    finally:
        conn.close()


def _json(base):
    return sorted(p.stem for p in (base / "output" / "json").glob("*.json"))


# ---------------------------------------------------------------------------
# the batches
# ---------------------------------------------------------------------------


def test_each_batch_is_exported_stored_and_valued_as_it_fills_and_the_rest_at_the_end(tmp_path, monkeypatch, events):
    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=1, batch_size=3)
    summary = fetcher.fetch_and_export(TICKERS[:7], formats=["json", "csv", "sqlite"])
    first, second, rest = TICKERS[:3], TICKERS[3:6], TICKERS[6:7]
    assert events == ([("fetch", t) for t in first] + [("store", first), ("benchmark",), ("value", first)]
                      + [("fetch", t) for t in second] + [("store", second), ("value", second)]
                      + [("fetch", t) for t in rest] + [("store", rest), ("value", rest)])
    assert (summary["batch_size"], summary["batches"], summary["exported"], summary["stored"]) == (3, 3, 7, 7)
    assert (summary["not_stored"], summary["sqlite_errors"], summary["interrupted"]) == ({}, [], False)
    assert (summary["tickers_requested"], summary["tickers_fetched"], summary["successful"]) == (7, 7, 7)
    assert _stored(fetcher.config.storage.database_path) == TICKERS[:7] == _json(tmp_path)
    assert sorted(p.stem for p in summary["export_paths"]["json"]) == TICKERS[:7]
    assert [p.name for p in summary["export_paths"]["csv"]] == ["summary.csv", "financial_history.csv"]
    assert summary["export_paths"]["sqlite"] == [fetcher.config.storage.database_path]


def test_the_batch_size_is_the_configs_by_default_and_25_unless_set(tmp_path, monkeypatch, events):
    assert app_config.DEFAULT_BATCH_SIZE == 25 and AppConfig(storage=StorageConfig(base_dir=tmp_path)).batch_size == 25
    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=1, batch_size=4)
    assert fetcher.fetch_and_export(TICKERS[:5], formats=["sqlite"])["batches"] == 2
    assert [e[1] for e in events if e[0] == "store"] == [TICKERS[:4], TICKERS[4:5]]
    events.clear()
    assert fetcher.fetch_and_export(TICKERS[:5], formats=["sqlite"], batch_size=2)["batches"] == 3
    with pytest.raises(ValueError):
        fetcher.fetch_and_export(TICKERS[:5], formats=["sqlite"], batch_size=0)


def test_the_workers_go_on_while_a_batch_is_exported(tmp_path, monkeypatch, events):
    # No pause between batches: while the first batch is exported, a ticker of a later one is being fetched.
    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=2, batch_size=2)
    write = fetcher.sqlite_store.write_batch
    overlapped = []

    def first_waits(data):
        if not overlapped:
            mine = {s.ticker for s in data}
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not [e for e in events if e[0] == "fetch" and e[1] not in mine]:
                time.sleep(0.01)
            overlapped.append(bool([e for e in events if e[0] == "fetch" and e[1] not in mine]))
        return write(data)

    monkeypatch.setattr(fetcher.sqlite_store, "write_batch", first_waits)
    assert fetcher.fetch_and_export(TICKERS[:6], formats=["sqlite"])["stored"] == 6
    assert overlapped == [True]


def test_no_more_than_a_batch_and_the_workers_companies_are_held(tmp_path, monkeypatch, events):
    # 5.6 MB a company: the run held every one of 375 until its one export at the end (about 2.1 GB).
    refs = []

    def made(ticker):
        stock = _made(ticker)
        refs.append(weakref.ref(stock))
        return stock

    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=2, batch_size=3, made=made)
    write = fetcher.sqlite_store.write_batch
    held = []

    def slow(data):
        time.sleep(0.05)  # a slow export: the workers race ahead of it
        gc.collect()
        held.append(sum(1 for ref in refs if ref() is not None))
        return write(data)

    monkeypatch.setattr(fetcher.sqlite_store, "write_batch", slow)
    summary = fetcher.fetch_and_export(TICKERS[:20], formats=["sqlite"])
    assert summary["stored"] == 20 and len(held) == 7
    assert max(held) <= 3 + sdf.IN_FLIGHT_PER_WORKER * 2
    gc.collect()
    assert sum(1 for ref in refs if ref() is not None) == 0


def test_batch_after_batch_merges_the_csvs_into_what_one_export_writes(tmp_path, monkeypatch, events):
    def made(ticker):
        stock = _made(ticker)
        if ticker == "T01":
            stock.market_data = {}  # Yahoo failed this time: the summary keeps its earlier quote
        return stock

    dirs = {}
    for name, workers, size in (("one", 1, 50), ("batches", 3, 2)):
        base = tmp_path / name
        earlier = CSVExporter(base / "output" / "csv")
        earlier.export([_made("T01"), _made("Z99")])
        earlier.export_financial_history([_made("T01"), _made("Z99")])
        fetcher = _fetcher(base, monkeypatch, events, workers=workers, batch_size=size, made=made)
        assert fetcher.fetch_and_export(TICKERS[:9], formats=["csv"])["batches"] == (1 if size == 50 else 5)
        dirs[name] = base / "output" / "csv"
    for filename in ("summary.csv", "financial_history.csv"):
        assert (dirs["batches"] / filename).read_bytes() == (dirs["one"] / filename).read_bytes()
    summary = (dirs["batches"] / "summary.csv").read_text(encoding="utf-8").splitlines()
    assert [line.split(",")[0] for line in summary[1:]] == TICKERS[:9] + ["Z99"]
    assert "extra_t01" in (dirs["batches"] / "financial_history.csv").read_text(encoding="utf-8").splitlines()[0]


def test_the_benchmark_is_fetched_once_a_run_after_the_first_batch(tmp_path, monkeypatch, events):
    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=2, batch_size=2)
    fetcher.fetch_and_export(TICKERS[:5], formats=["sqlite"])
    assert [e for e in events if e[0] == "benchmark"] == [("benchmark",)]
    kinds = [e[0] for e in events if e[0] != "fetch"]
    assert kinds[:3] == ["store", "benchmark", "value"]
    conn = sqlite3.connect(fetcher.config.storage.database_path)
    try:
        assert conn.execute("SELECT count(*) FROM price_bars WHERE ticker = '^GSPC'").fetchone() == (1,)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# what the database didn't take, and the valuations
# ---------------------------------------------------------------------------


def test_a_company_the_database_didnt_take_is_reported_and_not_valued(tmp_path, monkeypatch, events):
    made = lambda t: _made(t, employees={"x": 1}) if t == "T02" else _made(t)  # noqa: E731
    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=1, batch_size=2, made=made)
    summary = fetcher.fetch_and_export(TICKERS[:4], formats=["json", "sqlite"])
    assert (summary["stored"], list(summary["not_stored"]), summary["sqlite_errors"]) == (3, ["T02"], [])
    assert summary["not_stored"]["T02"].startswith("Error binding parameter")
    assert [e for e in events if e[0] == "value"] == [("value", ["T01"]), ("value", ["T03", "T04"])]
    assert _stored(fetcher.config.storage.database_path) == ["T01", "T03", "T04"]
    assert _json(tmp_path) == TICKERS[:4]  # the JSON export still has it
    assert summary["export_paths"]["sqlite"] == [fetcher.config.storage.database_path]


def test_a_batch_whose_export_failed_is_reported_and_the_next_one_is_stored(tmp_path, monkeypatch, events):
    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=1, batch_size=2)
    migrate = SQLiteStore._migrate
    calls = []

    def locked_once(self, conn):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        migrate(self, conn)

    monkeypatch.setattr(SQLiteStore, "_migrate", locked_once)
    summary = fetcher.fetch_and_export(TICKERS[:4], formats=["sqlite"])
    assert summary["sqlite_errors"] == ["batch 1: database is locked"]
    assert summary["not_stored"] == {"T01": "database is locked", "T02": "database is locked"}
    assert summary["stored"] == 2 and _stored(fetcher.config.storage.database_path) == ["T03", "T04"]
    assert [e for e in events if e[0] == "value"] == [("value", ["T03", "T04"])]


def test_a_failed_valuation_names_its_batch_and_the_run_goes_on(tmp_path, monkeypatch, events, caplog):
    def compute(db_path, tickers=None, logger=None):
        if "T03" in tickers:
            raise RuntimeError("engine down")
        return len(tickers)

    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=1, batch_size=2)
    monkeypatch.setattr("src.valuation.engine.compute_and_store", compute)
    summary = fetcher.fetch_and_export(TICKERS[:5], formats=["sqlite"])
    assert summary["stored"] == 5 and summary["batches"] == 3
    assert [r.getMessage() for r in caplog.records if "engine down" in r.getMessage()] == [
        "Valuation computation failed for batch 2 (T03, T04): engine down"]


# ---------------------------------------------------------------------------
# Ctrl+C
# ---------------------------------------------------------------------------


def test_ctrl_c_keeps_the_batches_stored_and_exports_nothing_half_done(tmp_path, monkeypatch, events):
    def made(ticker):
        if ticker == "T06":
            raise KeyboardInterrupt  # Ctrl+C during its fetch (one worker: on this thread)
        return _made(ticker)

    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=1, batch_size=3, made=made)
    with pytest.raises(sdf.CollectionInterrupted) as caught:
        fetcher.fetch_and_export(TICKERS[:9], formats=["json", "csv", "sqlite"])
    assert isinstance(caught.value, KeyboardInterrupt)
    summary = caught.value.summary
    assert (summary["interrupted"], summary["stored"], summary["exported"], summary["tickers_fetched"]) == (
        True, 3, 3, 5)
    assert summary["unvalued"] == []
    # T04 and T05 were fetched after the last batch: neither exported anywhere; T07 on never fetched.
    assert _stored(fetcher.config.storage.database_path) == TICKERS[:3] == _json(tmp_path)
    rows = (tmp_path / "output" / "csv" / "summary.csv").read_text(encoding="utf-8").splitlines()[1:]
    assert [line.split(",")[0] for line in rows] == TICKERS[:3]
    assert [e[1] for e in events if e[0] == "fetch"] == TICKERS[:6]
    assert [e for e in events if e[0] == "value"] == [("value", TICKERS[:3])]


def test_ctrl_c_while_waiting_cancels_the_tickers_not_started_and_stops_at_once(tmp_path, monkeypatch, events):
    release = threading.Event()
    finished = []

    def made(ticker):
        if ticker in ("T05", "T06"):
            release.wait(10)  # still fetching when Ctrl+C comes
            finished.append(ticker)
        return _made(ticker)

    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=2, batch_size=2, made=made)
    real_wait = sdf.wait

    def ctrl_c_while_waiting(fs, timeout=None, return_when=ALL_COMPLETED):
        got = real_wait(fs, timeout=0.01, return_when=return_when)
        if not got.done and ("fetch", "T05") in events and ("fetch", "T06") in events:
            raise KeyboardInterrupt
        return got

    monkeypatch.setattr(sdf, "wait", ctrl_c_while_waiting)
    started = time.monotonic()
    try:
        with pytest.raises(sdf.CollectionInterrupted) as caught:
            fetcher.fetch_and_export(TICKERS[:8], formats=["json", "sqlite"])
        took = time.monotonic() - started
        assert took < 5 and finished == []  # it didn't wait for the two still fetching
        assert caught.value.summary["stored"] == 4
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while len(finished) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.3)  # time for a ticker wrongly left queued to start
    assert sorted(finished) == ["T05", "T06"]
    assert sorted(e[1] for e in events if e[0] == "fetch") == TICKERS[:6]  # T07 and T08 cancelled
    # The two that finished after Ctrl+C were never exported.
    assert _stored(fetcher.config.storage.database_path) == TICKERS[:4] == _json(tmp_path)


def test_ctrl_c_during_a_batchs_valuations_names_the_companies_left_unvalued(tmp_path, monkeypatch, events):
    def compute(db_path, tickers=None, logger=None):
        if "T03" in tickers:
            raise KeyboardInterrupt
        return len(tickers)

    fetcher = _fetcher(tmp_path, monkeypatch, events, workers=1, batch_size=2)
    monkeypatch.setattr("src.valuation.engine.compute_and_store", compute)
    with pytest.raises(sdf.CollectionInterrupted) as caught:
        fetcher.fetch_and_export(TICKERS[:6], formats=["sqlite"])
    assert (caught.value.summary["stored"], caught.value.summary["unvalued"]) == (4, ["T03", "T04"])


# ---------------------------------------------------------------------------
# stock-data: the summary and the exit code
# ---------------------------------------------------------------------------


@pytest.fixture
def made_by_class(monkeypatch, events):
    """``StockDataFetcher.fetch_ticker`` for every fetcher ``main`` builds: *made[0]*'s companies (``_made`` until a
    test sets another)."""
    made = [_made]

    def fetch(self, ticker, include_yahoo=True, include_sec=True, years_back=None):
        events.append(("fetch", ticker))
        return made[0](ticker)

    monkeypatch.setattr(StockDataFetcher, "fetch_ticker", fetch)
    return made


def test_stock_data_exits_1_naming_a_company_the_database_didnt_store(tmp_path, made_by_class, caplog, capsys):
    # The review's probe (one_bad_ticker): one value SQLite couldn't bind rolled back all four, and the run printed
    # FETCH COMPLETE, "Successfully fetched: 4", and exited 0 with nothing stored.
    made_by_class[0] = lambda t: _made(t, employees={"oops": 1}) if t == "BADC" else _made(t)
    code = src.main.main(["GOODA", "GOODB", "BADC", "GOODD", "--no-yahoo", "--output-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "Stored in the database: 3 of 4" in out
    assert _stored(tmp_path / "output" / "stock.db") == ["GOODA", "GOODB", "GOODD"]
    assert _json(tmp_path) == ["BADC", "GOODA", "GOODB", "GOODD"]
    named = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "Not stored" in r.getMessage()]
    assert len(named) == 1 and "BADC (Error binding parameter" in named[0] and "GOOD" not in named[0]


def test_stock_data_exits_0_when_every_company_was_stored(tmp_path, made_by_class, capsys):
    code = src.main.main(["AAA", "BBB", "CCC", "--no-yahoo", "--output-dir", str(tmp_path), "--batch-size", "2"])
    assert code == 0
    assert "Stored in the database: 3 of 3" in capsys.readouterr().out
    assert _stored(tmp_path / "output" / "stock.db") == ["AAA", "BBB", "CCC"]


def test_stock_data_exits_1_when_the_sqlite_export_failed(tmp_path, made_by_class, monkeypatch, caplog, capsys):
    def locked(self, conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(SQLiteStore, "_migrate", locked)
    code = src.main.main(["AAA", "BBB", "--no-yahoo", "--output-dir", str(tmp_path)])
    assert code == 1
    assert "Stored in the database: 0 of 2" in capsys.readouterr().out
    assert "The SQLite export failed: batch 1: database is locked" in caplog.text
    assert "AAA (database is locked); BBB (database is locked)" in caplog.text


def test_stock_data_without_sqlite_says_nothing_of_the_database(tmp_path, made_by_class, capsys):
    code = src.main.main(["AAA", "BBB", "--no-yahoo", "--output-dir", str(tmp_path), "--formats", "json"])
    assert code == 0
    assert "Stored in the database" not in capsys.readouterr().out
    assert not (tmp_path / "output" / "stock.db").exists()


def test_stock_data_says_how_many_were_stored_when_interrupted_and_exits_130(tmp_path, made_by_class, caplog):
    def made(ticker):
        if ticker == "T05":
            raise KeyboardInterrupt
        return _made(ticker)

    made_by_class[0] = made
    code = src.main.main(TICKERS[:6] + ["--workers", "1", "--batch-size", "2", "--no-yahoo",
                                        "--output-dir", str(tmp_path)])
    assert code == 130
    warned = [r.getMessage() for r in caplog.records
              if r.levelno == logging.WARNING and "Interrupted" in r.getMessage()]
    assert len(warned) == 1 and "4 of 6 companies stored" in warned[0] and "--skip-existing" in warned[0]
    assert _stored(tmp_path / "output" / "stock.db") == TICKERS[:4]


@pytest.fixture
def configs(monkeypatch):
    """The config of each fetcher ``main`` builds, a fake that collects nothing."""
    seen = []

    class FakeFetcher:
        def __init__(self, config, logger):
            seen.append(config)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch_and_export(self, tickers, include_yahoo, include_sec, years_back, formats):
            return {"tickers_requested": len(tickers), "tickers_fetched": len(tickers), "successful": len(tickers),
                    "with_warnings": 0, "with_errors": 0, "duration_seconds": 0.0, "export_paths": {}, "stored": 0,
                    "not_stored": {}, "sqlite_errors": []}

    monkeypatch.setattr(src.main, "StockDataFetcher", FakeFetcher)
    return seen


def test_batch_size_is_passed_through_the_config(tmp_path, configs):
    assert src.main.main(["AAA", "--output-dir", str(tmp_path), "--formats", "json"]) == 0
    assert src.main.main(["AAA", "--output-dir", str(tmp_path), "--formats", "json", "--batch-size", "7"]) == 0
    assert [c.batch_size for c in configs] == [app_config.DEFAULT_BATCH_SIZE, 7]


@pytest.mark.parametrize("value", ["0", "-3", "two", "2.5"])
def test_a_batch_size_that_isnt_a_whole_number_of_1_or_more_is_a_usage_error(tmp_path, configs, value, capsys):
    with pytest.raises(SystemExit) as caught:
        src.main.main(["AAA", "--output-dir", str(tmp_path), "--batch-size", value])
    err = capsys.readouterr().err
    assert caught.value.code == 2 and "argument --batch-size: " in err and "unrecognized" not in err
    assert configs == []
