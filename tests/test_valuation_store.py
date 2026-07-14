"""valuations/valuation_summary persistence + compute_and_store end to end."""
import json
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.backfill import main as backfill_main
from src.valuation.engine import compute_and_store


@pytest.fixture
def seeded_db(tmp_path):
    """One 'general' ticker with enough history for DCF/Graham/Lynch/multiples."""
    db_path = tmp_path / "store.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute(
        "INSERT INTO companies (ticker, sector_class) VALUES ('AAA', 'general')"
    )
    for i, fy in enumerate(range(2019, 2024)):
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted) "
            "VALUES ('AAA', ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", 200.0, 1000.0, 2.0 + 0.1 * i, 100.0),
        )
        conn.execute(
            "INSERT INTO metrics_annual (ticker, fiscal_year, levered_fcf) "
            "VALUES ('AAA', ?, ?)",
            (fy, 100.0 * 1.05 ** i),
        )
        conn.execute(
            "INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', ?, ?)",
            (f"{fy}-12-30", 30.0 + i),
        )
    conn.execute(
        "INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, "
        "beta, risk_free_rate, current_price) "
        "VALUES ('AAA', '2024-01-05T00:00:00', 100.0, 1.0, 0.045, 35.0)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_compute_and_store_writes_five_model_rows_and_summary(seeded_db):
    n = compute_and_store(seeded_db)
    assert n == 1
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM valuations WHERE ticker='AAA' ORDER BY model"
    ).fetchall()
    assert [r["model"] for r in rows] == ["dcf", "ddm", "graham", "lynch", "multiples"]
    by_model = {r["model"]: r for r in rows}
    assert by_model["dcf"]["applicable"] == 1
    assert by_model["dcf"]["value_bear"] < by_model["dcf"]["value_bull"]
    assumptions = json.loads(by_model["dcf"]["assumptions"])
    assert assumptions["growth_source"] == "hist_only"
    assert by_model["ddm"]["applicable"] == 0
    assert by_model["ddm"]["na_reason"] == "no dividend history"
    summary = conn.execute(
        "SELECT * FROM valuation_summary WHERE ticker='AAA'"
    ).fetchone()
    assert summary["n_applicable"] == 4  # dcf, graham, lynch, multiples
    assert summary["median_base"] is not None
    assert summary["computed_at"]
    conn.close()


def test_compute_and_store_is_idempotent_upsert(seeded_db):
    compute_and_store(seeded_db)
    compute_and_store(seeded_db)
    conn = sqlite3.connect(seeded_db)
    count = conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0]
    assert count == 5
    conn.close()


def test_compute_and_store_skips_broken_ticker(seeded_db):
    conn = sqlite3.connect(seeded_db)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('BBB', 'general')")
    conn.commit()
    conn.close()
    n = compute_and_store(seeded_db)  # BBB has no data at all -> still stored (all N/A)
    assert n == 2
    conn = sqlite3.connect(seeded_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM valuations WHERE ticker='BBB' AND applicable=0"
    ).fetchone()[0]
    assert count == 5
    conn.close()


def test_compute_and_store_explicit_ticker_list(seeded_db):
    n = compute_and_store(seeded_db, tickers=["AAA"])
    assert n == 1


def test_compute_and_store_missing_companies_table_returns_zero(tmp_path):
    """A DB without a companies table logs a warning and returns 0, not a crash."""
    empty = tmp_path / "no_schema.db"
    n = compute_and_store(empty)
    assert n == 0


def test_backfill_cli_runs_on_explicit_db(seeded_db, capsys):
    rc = backfill_main(["--db", str(seeded_db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 ticker" in out
    conn = sqlite3.connect(seeded_db)
    assert conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0] == 5
    conn.close()


def test_backfill_cli_ticker_filter(seeded_db):
    rc = backfill_main(["--db", str(seeded_db), "AAA"])
    assert rc == 0


def test_backfill_cli_empty_db_returns_1(tmp_path):
    from src.exporters.sqlite_store import SQLiteStore
    empty = tmp_path / "empty.db"
    store = SQLiteStore(db_path=empty)
    conn = store._connect()
    store._create_schema(conn)
    conn.commit()
    conn.close()
    rc = backfill_main(["--db", str(empty)])
    assert rc == 1


def test_fetch_and_export_triggers_valuations(monkeypatch):
    """fetch_and_export calls compute_and_store for the collected tickers."""
    from src.fetchers import stock_data_fetcher as sdf

    calls = {}

    def fake_compute_and_store(db_path, tickers=None, logger=None):
        calls["db_path"] = db_path
        calls["tickers"] = tickers
        return len(tickers or [])

    monkeypatch.setattr(
        "src.valuation.engine.compute_and_store", fake_compute_and_store
    )

    fetcher = sdf.StockDataFetcher.__new__(sdf.StockDataFetcher)
    # Only the attributes fetch_and_export touches:
    import logging as _logging

    class _FakeStore:
        db_path = "fake.db"

        def export_benchmark_bars(self, *a, **k):
            pass

    class _FakeStock:
        ticker = "AAA"
        errors = []
        warnings = []

    fetcher.logger = _logging.getLogger("test")
    fetcher.sqlite_store = _FakeStore()
    fetcher.config = type("C", (), {"output_formats": ["sqlite"]})()
    monkeypatch.setattr(fetcher, "fetch_multiple", lambda *a, **k: [_FakeStock()])
    monkeypatch.setattr(fetcher, "export", lambda *a, **k: {"sqlite": ["fake.db"]})
    monkeypatch.setattr(
        fetcher, "yahoo_handler",
        type("Y", (), {"fetch_benchmark_bars": lambda self: []})(),
        raising=False,
    )

    summary = fetcher.fetch_and_export(["AAA"])
    assert calls["tickers"] == ["AAA"]
    assert calls["db_path"] == "fake.db"
    assert summary["tickers_fetched"] == 1
