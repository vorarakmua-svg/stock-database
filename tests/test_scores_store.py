"""Quality scores (Milestone 2) in SQLite: the three tables, the per-ticker replace, the engine hooks and the
universe-wide rank pass. docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
import json
import sqlite3

import pytest

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


def test_the_scores_store_their_debt_to_ebitda(tmp_path):
    db = _seed(tmp_path / "s.db")
    conn = sqlite3.connect(db)
    if "debt_to_ebitda" in {r[1] for r in conn.execute("PRAGMA table_info(quality_scores)")}:
        conn.execute("ALTER TABLE quality_scores DROP COLUMN debt_to_ebitda")  # a table from before BUG-190's fix
    conn.commit()
    conn.close()
    compute_and_store(db)
    assert _scores(db)["AAA"].get("debt_to_ebitda") == pytest.approx(5.0)  # debt = revenue, EBITDA 20% of it


# ---------------------------------------------------------------------------------------------------------------
# The industry ranks (Milestone 5): docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and
# industries"

INDUSTRY = ("industry_group", "industry_label", "industry_size", "ind_rank_strength", "ind_rank_profitability",
            "ind_rank_growth", "ind_rank_valuation", "ind_rank_momentum", "ind_composite")
M2_RANKS = ("rank_strength", "rank_profitability", "rank_growth", "rank_valuation", "rank_momentum",
            "composite_score", "magic_formula_rank")
#: Ten computer makers (357), Deere and Caterpillar (35's, through it), two banks without a group (602 and 60 have
#: two) and a company without a code: (ticker, SIC code, SEC description, revenue growth).
COMPANIES = (
    [(f"C{i}", "3571" if i < 4 else "3572" if i < 7 else "3576",
      "Electronic Computers" if i < 4 else "Computer Storage Devices" if i < 7 else "Computer Communications Equipment",
      (i + 1) / 100) for i in range(10)]
    + [("DE", "3523", "Farm Machinery & Equipment", 0.055), ("CAT", "3531", "Construction Machinery & Equip", 0.5),
       ("JPM", "6021", "National Commercial Banks", 0.2), ("BAC", "6021", "National Commercial Banks", 0.3),
       ("NOC", None, None, 0.1)])
LABEL_357 = "Electronic Computers; Computer Communications Equipment; Computer Storage Devices"


def _components(growth):
    return {"strength": {"interest_coverage": 10.0, "altman_z": 4.0},
            "profitability": {"operating_margin": 0.2, "piotroski_share": 0.7},
            "growth": {"rev_ps_cagr_3y": growth, "rev_ps_cagr_5y": growth},
            "valuation": {"price_to_value": 1.0}, "momentum": {"momentum": 0.05}}


def _ranked_db(db_path):
    """What the rank pass reads: quality_scores rows (their components) and companies rows (their SIC codes)."""
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    for ticker, sic, description, growth in COMPANIES:
        conn.execute("INSERT INTO companies (ticker, sector_class, sic, sic_description) VALUES (?, 'general', ?, ?)",
                     (ticker, sic, description))
        conn.execute("INSERT INTO quality_scores (ticker, components, earnings_yield, return_on_capital, computed_at) "
                     "VALUES (?, ?, 0.05, 0.2, '2026-10-25T00:00:00')", (ticker, json.dumps(_components(growth))))
    conn.commit()
    conn.close()
    return db_path


def _drop(db_path, table, columns):
    conn = sqlite3.connect(db_path)
    present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for column in columns:
        if column in present:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    conn.close()


def test_the_rank_pass_stores_the_industry_ranks(tmp_path):
    db = _ranked_db(tmp_path / "s.db")
    assert scores_engine.rank_all(db, "2026-10-25T01:00:00") == 15
    rows = _scores(db)
    for i in range(10):
        c = rows[f"C{i}"]
        assert (c["industry_group"], c["industry_size"], c["industry_label"]) == ("357", 10, f"SIC 357: {LABEL_357}")
        assert c["ind_rank_growth"] == i + 1  # the slowest of the ten is 1, the fastest 10
        assert (c["ind_rank_strength"], c["ind_rank_valuation"], c["ind_rank_momentum"]) == (10, 10, 10)
        assert c["ind_composite"] == pytest.approx(10 * (40 + i + 1) / 5)
    # 35's twelve: Deere at 0.055 is sixth of twelve, at the middle; Caterpillar the fastest.
    de, cat = rows["DE"], rows["CAT"]
    assert (de["industry_group"], de["industry_size"], de["industry_label"]) == ("35", 12, f"SIC 35: {LABEL_357}")
    assert (de["ind_rank_growth"], cat["ind_rank_growth"]) == (5, 10)
    assert de["ind_composite"] == pytest.approx(90.0)
    for ticker in ("JPM", "BAC", "NOC"):
        assert {c: rows[ticker][c] for c in INDUSTRY} == {c: None for c in INDUSTRY}
    # The universe's ranks beside them: twelve of the fifteen grow no faster than the fastest of the ten.
    assert rows["C9"]["rank_growth"] == 8 and rows["C9"]["ranked_at"] == "2026-10-25T01:00:00"


def test_a_group_s_label_and_size_are_its_members():
    # Ten drug makers form 283; the computer makers and the banks, five each and no group, describe more companies.
    rows = ([{"ticker": f"P{i}", "sic": "2834" if i < 6 else "2836", "components": _components(0.05),
              "sic_description": "Pharmaceutical Preparations" if i < 6 else "Biological Products"} for i in range(10)]
            + [{"ticker": f"E{i}", "sic": "3571", "sic_description": "Electronic Computers",
                "components": _components(0.05)} for i in range(5)]
            + [{"ticker": f"B{i}", "sic": "6021", "sic_description": "National Commercial Banks",
                "components": _components(0.05)} for i in range(5)])
    columns = scores_engine.industry_columns(rows)
    assert columns["P0"]["industry_label"] == "SIC 283: Pharmaceutical Preparations; Biological Products"
    assert columns["P0"]["industry_size"] == 10 and columns["P9"]["industry_group"] == "283"
    assert all(columns[f"{p}{i}"]["industry_label"] is None for p in "EB" for i in range(5))


def test_the_industry_ranks_leave_the_universe_s_alone(tmp_path):
    coded = _ranked_db(tmp_path / "coded.db")
    uncoded = _ranked_db(tmp_path / "uncoded.db")
    conn = sqlite3.connect(uncoded)
    conn.execute("UPDATE companies SET sic = NULL, sic_description = NULL")
    conn.commit()
    conn.close()
    scores_engine.rank_all(coded, "2026-10-25T01:00:00")
    scores_engine.rank_all(uncoded, "2026-10-25T01:00:00")
    with_codes, without = _scores(coded), _scores(uncoded)
    assert {t: [r[c] for c in M2_RANKS] for t, r in with_codes.items()} == {
        t: [r[c] for c in M2_RANKS] for t, r in without.items()}
    assert with_codes["C0"]["industry_group"] == "357" and without["C0"]["industry_group"] is None


def test_a_scores_table_from_before_the_industries_gains_them(tmp_path):
    db = _ranked_db(tmp_path / "s.db")
    _drop(db, "quality_scores", INDUSTRY)
    scores_engine.rank_all(db, "2026-10-25T01:00:00")
    rows = _scores(db)
    assert rows["C3"]["industry_group"] == "357" and rows["C3"]["ind_rank_growth"] == 4
    assert rows["C3"]["rank_growth"] is not None


def test_a_companies_table_from_before_the_codes_ranks_without_industries(tmp_path):
    db = _ranked_db(tmp_path / "s.db")
    _drop(db, "companies", ("sic", "sic_description"))
    assert scores_engine.rank_all(db, "2026-10-25T01:00:00") == 15
    rows = _scores(db)
    assert all(rows[t]["rank_growth"] is not None for t in rows)  # the universe's ranks, as before
    assert all({c: r[c] for c in INDUSTRY} == {c: None for c in INDUSTRY} for r in rows.values())


def test_a_full_run_stores_the_industry_columns(tmp_path):
    db = _seed(tmp_path / "s.db")  # two companies, no codes
    compute_and_store(db)
    rows = _scores(db)
    assert rows["AAA"]["rank_growth"] is not None
    assert all(rows[t][c] is None for t in ("AAA", "BBB") for c in INDUSTRY)
