"""Quality scores (Milestone 2) in SQLite: the three tables, the per-ticker replace, the engine hooks and the
universe-wide rank pass. docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
import json
import sqlite3

from src.exporters.sqlite_store import SQLiteStore
from src.scores import engine as scores_engine
from src.valuation import engine
from src.valuation.backfill import main as backfill_main
from src.valuation.engine import compute_and_store


def _seed(db_path, tickers=("AAA", "BBB")):
    """Each ticker: 12 fiscal years, revenue +8% (AAA) or +2% (BBB) a year, a snapshot, monthly bars."""
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    for k, ticker in enumerate(tickers):
        g = 0.08 if k == 0 else 0.02
        conn.execute("INSERT INTO companies (ticker, sector_class) VALUES (?, 'general')", (ticker,))
        for i, fy in enumerate(range(2014, 2026)):
            revenue = 1000e6 * (1 + g) ** i
            ni = 0.1 * revenue
            conn.execute(
                "INSERT INTO financials_annual (ticker, fiscal_year, period_end, revenue, net_income, total_equity, "
                "eps_diluted, weighted_avg_shares_diluted, shares_outstanding, total_assets, current_assets, "
                "current_liabilities, total_liabilities, retained_earnings, operating_income, operating_cash_flow, "
                "investing_cash_flow, long_term_debt, cash_and_equivalents, gross_profit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, fy, f"{fy}-12-31", revenue, ni, 4 * revenue, ni / 100e6, 100e6, 100e6, 6 * revenue,
                 2 * revenue, revenue, 2 * revenue, 3 * revenue, 0.15 * revenue, 0.13 * revenue, -0.05 * revenue,
                 revenue, 0.2 * revenue, 0.4 * revenue))
            conn.execute(
                "INSERT INTO metrics_annual (ticker, fiscal_year, ebitda, free_cash_flow, roic, operating_margin, "
                "interest_coverage, total_debt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, fy, 0.2 * revenue, 0.08 * revenue, 0.12, 0.15, 8.0, revenue))
        conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price, market_cap, beta, "
                     "risk_free_rate) VALUES (?, '2026-02-25T10:00:00', 50.0, 5.0e9, 1.1, 0.04)", (ticker,))
        for m in range(1, 13):
            conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES (?, ?, ?)",
                         (ticker, f"2025-{m:02d}-28", 40.0 + m * (1 if k == 0 else -1)))
        conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES (?, '2026-01-30', ?)",
                     (ticker, 50.0 if k == 0 else 30.0))
    conn.commit()
    conn.close()
    return db_path


def _scores(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return {r["ticker"]: dict(r) for r in conn.execute("SELECT * FROM quality_scores")}


def test_compute_and_store_writes_the_scores_and_ranks(tmp_path):
    db = _seed(tmp_path / "s.db")
    assert compute_and_store(db) == 2
    rows = _scores(db)
    a, b = rows["AAA"], rows["BBB"]
    assert a["fiscal_year"] == 2025 and a["piotroski_tested"] == 9
    assert a["rev_ps_cagr_5y"] > b["rev_ps_cagr_5y"]
    assert a["altman_z"] is not None and a["wacc"] is not None
    assert json.loads(a["components"])["growth"]["rev_ps_cagr_5y"] == a["rev_ps_cagr_5y"]
    # AAA outgrows BBB, and its price rose where BBB's fell.
    assert a["rank_growth"] > b["rank_growth"] and a["rank_momentum"] > b["rank_momentum"]
    assert a["composite_score"] is not None and a["ranked_at"] == a["computed_at"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM per_share_annual WHERE ticker = 'AAA'").fetchone()[0] == 11


def test_one_tickers_run_reranks_every_ticker(tmp_path):
    db = _seed(tmp_path / "s.db")
    compute_and_store(db)
    first = _scores(db)["BBB"]["ranked_at"]
    compute_and_store(db, tickers=["AAA"])
    rows = _scores(db)
    assert rows["BBB"]["ranked_at"] > first  # BBB re-ranked against the new AAA
    assert rows["BBB"]["computed_at"] < rows["AAA"]["computed_at"]  # but its own scores weren't recomputed


def test_a_rerun_replaces_a_tickers_signs(tmp_path):
    db = _seed(tmp_path / "s.db")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE financials_annual SET total_equity = -100 WHERE ticker = 'AAA' AND fiscal_year = 2025")
    conn.commit()
    compute_and_store(db)
    codes = {r[0] for r in conn.execute("SELECT code FROM warning_signs WHERE ticker = 'AAA'")}
    assert "negative_equity" in codes
    conn.execute("UPDATE financials_annual SET total_equity = 4000e6 WHERE ticker = 'AAA' AND fiscal_year = 2025")
    conn.commit()
    compute_and_store(db)
    codes = {r[0] for r in conn.execute("SELECT code FROM warning_signs WHERE ticker = 'AAA'")}
    assert "negative_equity" not in codes
    assert _scores(db)["AAA"]["warning_count"] == len(codes)


def test_a_scores_failure_never_blocks_the_valuations_or_the_history(tmp_path, monkeypatch, caplog):
    db = _seed(tmp_path / "s.db")

    def broken(conn, ticker):
        raise RuntimeError("scores broke")

    monkeypatch.setattr(scores_engine, "compute_ticker", broken)
    assert compute_and_store(db) == 2
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0] == 12
    assert conn.execute("SELECT COUNT(*) FROM valuation_ranges").fetchone()[0] == 16
    assert conn.execute("SELECT COUNT(*) FROM quality_scores").fetchone()[0] == 0
    assert "scores broke" in caplog.text


def test_a_ranking_failure_leaves_the_scores(tmp_path, monkeypatch, caplog):
    db = _seed(tmp_path / "s.db")

    def broken(db_path, ranked_at):
        raise RuntimeError("ranking broke")

    monkeypatch.setattr(engine, "rank_all", broken)
    assert compute_and_store(db) == 2
    assert _scores(db)["AAA"]["rank_growth"] is None and _scores(db)["AAA"]["piotroski_f"] is not None
    assert "ranking broke" in caplog.text


def test_the_scores_read_the_latest_snapshot_with_a_price(tmp_path):
    """BUG-187: a later run whose quote failed leaves a snapshot without price, market cap or beta."""
    clean = _seed(tmp_path / "clean.db")
    failed = _seed(tmp_path / "failed.db")
    conn = sqlite3.connect(failed)
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, risk_free_rate) "
                 "VALUES ('AAA', '2026-02-26T10:00:00', 0.04)")
    conn.commit()
    conn.close()
    compute_and_store(clean)
    compute_and_store(failed)

    def scores(db):
        return {k: v for k, v in _scores(db)["AAA"].items() if k not in ("computed_at", "ranked_at")}

    assert scores(failed)["altman_z"] is not None and scores(failed)["wacc"] is not None
    assert scores(failed) == scores(clean)


def test_the_backfill_writes_the_scores(tmp_path):
    db = _seed(tmp_path / "s.db")
    assert backfill_main(["--db", str(db)]) == 0
    assert set(_scores(db)) == {"AAA", "BBB"}


def test_an_old_database_gains_the_tables(tmp_path):
    db = _seed(tmp_path / "s.db")
    conn = sqlite3.connect(db)
    for table in ("quality_scores", "per_share_annual", "warning_signs"):
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()
    compute_and_store(db)
    assert set(_scores(db)) == {"AAA", "BBB"}
