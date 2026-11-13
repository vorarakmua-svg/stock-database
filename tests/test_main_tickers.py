"""``stock-data``'s tickers (``src/main.py``; docs/superpowers/specs/2026-10-25-universe-automation-design.md, "The
universe"): the positional tickers, then each ``--tickers-file``'s (repeatable), one list in Yahoo's form, each ticker
once, in order; ``--skip-existing`` leaving out the tickers already in the database the run writes to (``--db``, else
``<output-dir>/output/stock.db``; none without one) and saying how many; an error, exit 1, when no ticker is given,
another when every one is already in the database, and one for a file or a ticker that doesn't read. The fetcher is a
fake recording the tickers it was asked for: nothing is collected.
"""
import logging
import sqlite3
from datetime import datetime

import pytest

import src.main
from src.exporters.sqlite_store import SQLiteStore
from src.main import main, resolve_tickers, stored_tickers
from src.models.stock_data import StockData


@pytest.fixture
def runs(monkeypatch):
    """The fetcher ``main`` builds, a fake: each collection's tickers and config are recorded; nothing is fetched."""
    runs = []

    class FakeFetcher:
        def __init__(self, config, logger):
            self.config = config

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch_and_export(self, tickers, include_yahoo, include_sec, years_back, formats):
            runs.append({"tickers": list(tickers), "config": self.config})
            return {"tickers_requested": len(tickers), "successful": len(tickers), "with_warnings": 0,
                    "with_errors": 0, "duration_seconds": 0.0, "export_paths": {}}

    monkeypatch.setattr(src.main, "StockDataFetcher", FakeFetcher)
    return runs


def _write(path, text):
    path.write_bytes(text.encode("utf-8"))
    return path


def _db(path, *tickers):
    """A database (the store's own schema) holding a company for each of *tickers*."""
    stocks = [StockData(ticker=t, cik=f"{i + 1:010d}", company_name=f"{t} Corp", collected_at=datetime(2026, 10, 1))
              for i, t in enumerate(tickers)]
    assert SQLiteStore(path).export(stocks) == path
    return path


def test_positional_tickers_are_collected_as_before(runs, tmp_path):
    assert main(["aapl", "MSFT", "--output-dir", str(tmp_path)]) == 0
    assert [run["tickers"] for run in runs] == [["AAPL", "MSFT"]]


def test_a_positional_class_share_is_in_yahoos_form_and_a_blank_one_is_left_out(runs, tmp_path):
    assert main(["brk.b", " ", "BRK-B", "bf.b", "--output-dir", str(tmp_path)]) == 0
    assert runs[0]["tickers"] == ["BRK-B", "BF-B"]


def test_a_tickers_file_alone(runs, tmp_path):
    listed = _write(tmp_path / "core.txt", "# the core list\nAAPL\nbrk.b\n\nMSFT  # Microsoft\n")
    assert main(["--tickers-file", str(listed), "--output-dir", str(tmp_path)]) == 0
    assert runs[0]["tickers"] == ["AAPL", "BRK-B", "MSFT"]


def test_the_positional_tickers_then_each_files_are_one_list_each_ticker_once(runs, tmp_path):
    a = _write(tmp_path / "a.txt", "AAPL\nBRK.B\nMSFT\n")
    b = _write(tmp_path / "b.csv", "Symbol,Security\nMSFT,Microsoft\nGOOGL,Alphabet\nBF.B,Brown-Forman\nnvda,NVIDIA\n")
    argv = ["NVDA", "aapl", "--tickers-file", str(a), "--tickers-file", str(b), "--output-dir", str(tmp_path)]
    assert main(argv) == 0
    assert runs[0]["tickers"] == ["NVDA", "AAPL", "BRK-B", "MSFT", "GOOGL", "BF-B"]
    assert main(["--tickers-file", str(b), "XOM", "--output-dir", str(tmp_path)]) == 0
    assert runs[1]["tickers"] == ["XOM", "MSFT", "GOOGL", "BF-B", "NVDA"]


def test_skip_existing_leaves_out_the_tickers_in_the_runs_database_and_says_how_many(runs, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    _db(tmp_path / "output" / "stock.db", "AAPL", "MSFT", "JPM")
    listed = _write(tmp_path / "sp.csv", "ticker,name\nAAPL,Apple\nNVDA,NVIDIA\nMSFT,Microsoft\nBRK.B,Berkshire\n")
    assert main(["GOOGL", "--tickers-file", str(listed), "--skip-existing", "--output-dir", str(tmp_path)]) == 0
    assert runs[0]["tickers"] == ["GOOGL", "NVDA", "BRK-B"]
    assert "2 of 5 tickers" in caplog.text and "left out" in caplog.text and "3 to collect" in caplog.text


def test_skip_existing_reads_the_database_db_names(runs, tmp_path):
    _db(tmp_path / "output" / "stock.db", "AAPL")
    other = _db(tmp_path / "other.db", "NVDA")
    assert main(["AAPL", "NVDA", "--skip-existing", "--db", str(other), "--output-dir", str(tmp_path)]) == 0
    assert runs[0]["tickers"] == ["AAPL"]


def test_skip_existing_without_a_database_leaves_out_none(runs, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    assert main(["AAPL", "NVDA", "--skip-existing", "--output-dir", str(tmp_path)]) == 0
    assert runs[0]["tickers"] == ["AAPL", "NVDA"]
    assert "0 of 2 tickers" in caplog.text
    assert not (tmp_path / "output" / "stock.db").exists()


def test_without_skip_existing_the_tickers_in_the_database_are_collected_again(runs, tmp_path):
    _db(tmp_path / "output" / "stock.db", "AAPL")
    assert main(["AAPL", "NVDA", "--output-dir", str(tmp_path)]) == 0
    assert runs[0]["tickers"] == ["AAPL", "NVDA"]


def test_no_ticker_given_is_an_error(runs, tmp_path, caplog):
    empty = _write(tmp_path / "empty.txt", "# nothing yet\n\n")
    for argv in ([], ["--tickers-file", str(empty)], ["--skip-existing"], [" "]):
        caplog.clear()
        assert main(argv + ["--output-dir", str(tmp_path)]) == 1
        assert "No tickers given" in caplog.text and "already in the database" not in caplog.text
    assert runs == []


def test_every_ticker_already_in_the_database_is_an_error_of_its_own(runs, tmp_path, caplog):
    _db(tmp_path / "output" / "stock.db", "AAPL", "MSFT")
    assert main(["msft", "AAPL", "--skip-existing", "--output-dir", str(tmp_path)]) == 1
    assert "All 2 tickers given are already in the database" in caplog.text
    assert "No tickers given" not in caplog.text
    assert runs == []


def test_a_tickers_file_that_doesnt_read_is_an_error(runs, tmp_path, caplog):
    assert main(["--tickers-file", str(tmp_path / "missing.txt"), "--output-dir", str(tmp_path)]) == 1
    assert "missing.txt" in caplog.text
    bad = _write(tmp_path / "bad.txt", "AAPL\nBRK B\n")
    assert main(["--tickers-file", str(bad), "--output-dir", str(tmp_path)]) == 1
    assert "bad.txt, line 2: not a ticker: 'BRK B'" in caplog.text
    sheet = _write(tmp_path / "list.xlsx", "AAPL\n")
    assert main(["--tickers-file", str(sheet), "--output-dir", str(tmp_path)]) == 1
    assert "a .txt or a .csv" in caplog.text
    assert runs == []


def test_skip_existing_with_a_database_that_doesnt_read_is_an_error(runs, tmp_path, caplog):
    broken = tmp_path / "output" / "stock.db"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not a database, " * 64)
    assert main(["AAPL", "--skip-existing", "--output-dir", str(tmp_path)]) == 1
    assert "Cannot read the companies in" in caplog.text and runs == []


def test_a_positional_ticker_that_isnt_one_is_an_error(runs, tmp_path, caplog):
    assert main(["AAPL", "^GSPC", "--output-dir", str(tmp_path)]) == 1
    assert "not a ticker: '^GSPC'" in caplog.text and runs == []


def test_resolve_tickers_is_the_positional_ones_then_each_files(tmp_path):
    a = _write(tmp_path / "a.txt", "msft\nAAPL\n")
    b = _write(tmp_path / "b.txt", "nvda\n")
    assert resolve_tickers(["aapl", ""], [a, b]) == ["AAPL", "MSFT", "NVDA"]
    assert resolve_tickers([], []) == []


def test_stored_tickers_reads_the_companies_without_creating_a_database(tmp_path):
    assert stored_tickers(_db(tmp_path / "stock.db", "AAPL", "BRK-B")) == {"AAPL", "BRK-B"}
    missing = tmp_path / "none" / "stock.db"
    assert stored_tickers(missing) == set() and not missing.exists()
    bare = tmp_path / "bare.db"
    conn = sqlite3.connect(bare)
    conn.execute("CREATE TABLE other (x TEXT)")
    conn.commit()
    conn.close()
    assert stored_tickers(bare) == set()


def test_stored_tickers_opens_the_database_read_only(tmp_path, monkeypatch):
    db = _db(tmp_path / "stock.db", "AAPL")
    opened = []
    connect = sqlite3.connect

    def recording(target, *args, **kwargs):
        opened.append((str(target), kwargs.get("uri")))
        return connect(target, *args, **kwargs)

    monkeypatch.setattr(src.main.sqlite3, "connect", recording)
    assert stored_tickers(db) == {"AAPL"}
    assert len(opened) == 1 and opened[0][0].endswith("?mode=ro") and opened[0][1] is True
