"""The valuation indicators in SQLite (Milestone 4): the table, the per-ticker replace, the engine hook after the
scores, the backfill, the loader, and the valuations they never touch.

docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md ("Indicators", "Validation")
"""
import json
import math
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation import engine
from src.valuation.backfill import main as backfill_main
from src.valuation.engine import MEDIAN_MODELS, compute_and_store
from src.valuation.indicators import INDICATORS, IndicatorResult, PsRange, load_indicator_inputs, ps_value
from src.valuation.models import dcf_per_share

COLUMNS = ["ticker", "indicator", "applicable", "na_reason", "value_bear", "value_base", "value_bull", "unit",
           "assumptions", "basis_fiscal_year", "computed_at"]
UNITS = {"reverse_dcf": "rate", "epv": "per_share", "ncav": "per_share", "ps_value": "per_share",
         "graham_tangible": "per_share"}
#: FY2025's revenue: 1000M growing 8% a year from FY2014.
REVENUE_2025 = 1000e6 * 1.08 ** 11


def _seed(db_path):
    """AAA (general) and BBB (a bank): 12 fiscal years with revenue +8% a year, five years of quarters and trailing
    years (for the valuation history's P/S range), monthly bars from 2021, a snapshot with a price."""
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    for ticker, sector in (("AAA", "general"), ("BBB", "bank")):
        conn.execute("INSERT INTO companies (ticker, sector_class) VALUES (?, ?)", (ticker, sector))
        for i, fy in enumerate(range(2014, 2026)):
            rev = 1000e6 * 1.08 ** i
            conn.execute(
                "INSERT INTO financials_annual (ticker, fiscal_year, period_end, revenue, operating_income, "
                "pretax_income, income_tax_expense, interest_expense, net_income, total_equity, goodwill, "
                "intangible_assets, eps_diluted, weighted_avg_shares_diluted, shares_outstanding, total_assets, "
                "current_assets, current_liabilities, total_liabilities, cash_and_equivalents, "
                "short_term_investments, accounts_receivable, inventory, long_term_debt, operating_cash_flow, "
                "investing_cash_flow, retained_earnings, gross_profit) VALUES (" + ", ".join("?" * 28) + ")",
                (ticker, fy, f"{fy}-12-31", rev, 0.15 * rev, 0.13 * rev, 0.03 * rev, 0.05 * rev, 0.1 * rev,
                 4 * rev, 0.5 * rev, 0.3 * rev, 0.1 * rev / 100e6, 100e6, 100e6, 6 * rev, 3 * rev, rev, 2 * rev,
                 0.2 * rev, 0.1 * rev, 0.5 * rev, 0.4 * rev, rev, 0.13 * rev, -0.05 * rev, 3 * rev, 0.4 * rev))
            conn.execute("INSERT INTO metrics_annual (ticker, fiscal_year, ebitda, free_cash_flow, total_debt) "
                         "VALUES (?, ?, ?, ?, ?)", (ticker, fy, 0.2 * rev, 0.08 * rev, rev))
        for fy in range(2021, 2026):
            rev = 1000e6 * 1.08 ** (fy - 2014)
            for quarter, end in enumerate(("03-31", "06-30", "09-30", "12-31"), start=1):
                conn.execute(
                    "INSERT INTO financials_quarterly (ticker, period_end, fiscal_year, fiscal_quarter, "
                    "weighted_avg_shares_basic, revenue, net_income, eps_diluted) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, f"{fy}-{end}", fy, quarter, 100e6, rev / 4, 0.1 * rev / 4, 0.1 * rev / 4 / 100e6))
                conn.execute("INSERT INTO financials_ttm (ticker, period_end, fiscal_year, revenue, net_income, "
                             "total_equity) VALUES (?, ?, ?, ?, ?, ?)",
                             (ticker, f"{fy}-{end}", fy, rev, 0.1 * rev, 4 * rev))
        for year in range(2021, 2026):
            for month in range(1, 13):
                conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES (?, ?, ?)",
                             (ticker, f"{year}-{month:02d}-28", 30.0 + 2 * (year - 2021) + 0.1 * month))
        conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES (?, '2026-01-30', 50.0)", (ticker,))
        conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price, market_cap, beta, "
                     "risk_free_rate, shares_outstanding) VALUES (?, '2026-02-25T10:00:00', 50.0, 5.0e9, 1.1, "
                     "0.04, 100e6)", (ticker,))
    conn.commit()
    conn.close()
    return db_path


def _indicators(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return {(r["ticker"], r["indicator"]): dict(r) for r in conn.execute("SELECT * FROM valuation_indicators")}


def _one(db, sql):
    return sqlite3.connect(db).execute(sql).fetchone()[0]


def test_compute_and_store_writes_the_five_indicators_of_each_ticker(tmp_path):
    db = _seed(tmp_path / "s.db")
    assert compute_and_store(db) == 2
    rows = _indicators(db)
    assert sorted(rows) == sorted((t, i) for t in ("AAA", "BBB") for i in INDICATORS)
    assert all(r["unit"] == UNITS[r["indicator"]] for r in rows.values())
    computed_at = _one(db, "SELECT computed_at FROM valuations WHERE ticker = 'AAA' AND model = 'dcf'")
    assert {r["computed_at"] for r in rows.values()} == {computed_at}
    aaa = {i: rows[("AAA", i)] for i in INDICATORS}
    assert all(r["applicable"] == 1 for r in aaa.values()), {i: r["na_reason"] for i, r in aaa.items()}
    assumptions = {i: json.loads(r["assumptions"]) for i, r in aaa.items()}
    # The reverse DCF's growth gives back the snapshot's price through the DCF's own inputs.
    a = assumptions["reverse_dcf"]
    assert (a["price"], a["price_date"]) == (50.0, "2026-02-25T10:00:00")
    assert dcf_per_share(a["fcf_basis"], a["shares_outstanding"], aaa["reverse_dcf"]["value_base"],
                         a["discount_base"], a["terminal_growth"]) == pytest.approx(50.0, rel=1e-5)
    # EPV on M2's WACC, NCAV as M2's, the P/S value as the history's.
    assert assumptions["epv"]["wacc"] == pytest.approx(_one(db, "SELECT wacc FROM quality_scores WHERE ticker = 'AAA'"))
    assert aaa["ncav"]["value_base"] == pytest.approx(
        _one(db, "SELECT ncav_per_share FROM quality_scores WHERE ticker = 'AAA'"))
    assert aaa["ps_value"]["value_base"] == pytest.approx(
        _one(db, "SELECT implied_value FROM valuation_ranges WHERE ticker = 'AAA' AND metric = 'ps'"))
    # Tangible book: equity 4x revenue less goodwill 0.5x and intangibles 0.3x; EPS 0.1x revenue, 100M shares.
    eps, tangible_bvps = 0.1 * REVENUE_2025 / 100e6, 3.2 * REVENUE_2025 / 100e6
    assert aaa["graham_tangible"]["value_base"] == pytest.approx(math.sqrt(22.5 * eps * tangible_bvps))
    assert [aaa[i]["basis_fiscal_year"] for i in INDICATORS] == [2025, 2025, 2025, None, 2025]
    bbb = {i: rows[("BBB", i)] for i in INDICATORS}
    assert bbb["reverse_dcf"]["na_reason"] == "no DCF: not applicable to sector 'bank'"
    assert bbb["epv"]["na_reason"] == "not meaningful for banks"
    assert bbb["ncav"]["na_reason"] == "not meaningful for banks (no current assets)"
    assert bbb["ps_value"]["applicable"] == 1 and bbb["graham_tangible"]["applicable"] == 1


def test_a_rerun_replaces_a_tickers_indicators(tmp_path):
    db = _seed(tmp_path / "s.db")
    compute_and_store(db)
    first = _indicators(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO valuation_indicators (ticker, indicator, applicable, unit, computed_at) "
                 "VALUES ('AAA', 'retired', 0, 'per_share', '2026-01-01')")
    conn.execute("UPDATE financials_annual SET goodwill = 4 * revenue WHERE ticker = 'AAA' AND fiscal_year = 2025")
    conn.commit()
    conn.close()
    compute_and_store(db, tickers=["AAA"])
    rows = _indicators(db)
    assert ("AAA", "retired") not in rows and len(rows) == 10
    g = rows[("AAA", "graham_tangible")]
    assert (g["applicable"], g["na_reason"]) == (0, "tangible book is negative: goodwill and intangibles exceed equity")
    assert g["computed_at"] > first[("AAA", "graham_tangible")]["computed_at"]
    assert rows[("BBB", "graham_tangible")] == first[("BBB", "graham_tangible")]  # BBB wasn't recomputed


def test_an_indicators_failure_never_blocks_the_valuations_the_history_or_the_scores(tmp_path, monkeypatch,
                                                                                   caplog):
    db = _seed(tmp_path / "s.db")

    def broken(conn, ticker):
        raise RuntimeError("indicators broke")

    monkeypatch.setattr(engine, "load_indicator_inputs", broken)
    assert compute_and_store(db) == 2
    assert _one(db, "SELECT COUNT(*) FROM valuations") == 12
    assert _one(db, "SELECT COUNT(*) FROM valuation_ranges") == 16
    assert _one(db, "SELECT COUNT(*) FROM quality_scores WHERE ranked_at IS NOT NULL") == 2
    assert _one(db, "SELECT COUNT(*) FROM valuation_indicators") == 0
    assert "Valuation indicators failed for AAA: indicators broke" in caplog.text


def test_the_backfill_writes_the_indicators(tmp_path):
    db = _seed(tmp_path / "s.db")
    assert backfill_main(["--db", str(db)]) == 0
    assert len(_indicators(db)) == 10


def test_an_old_database_gains_the_table(tmp_path):
    db = _seed(tmp_path / "s.db")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE valuation_indicators")
    conn.commit()
    conn.close()
    compute_and_store(db)
    assert len(_indicators(db)) == 10


def test_an_export_replaces_one_tickers_rows(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "s.db")
    conn = store._connect()
    store._create_schema(conn)
    assert [r[1] for r in conn.execute("PRAGMA table_info(valuation_indicators)")] == COLUMNS
    conn.close()
    results = [IndicatorResult(indicator="epv", applicable=True, value_bear=8.0, value_base=10.0, value_bull=12.0,
                               assumptions={"wacc": 0.1}, basis_fiscal_year=2025),
               IndicatorResult(indicator="reverse_dcf", applicable=False, na_reason="no price", unit="rate")]
    store.export_valuation_indicators("AAA", results, "2026-10-24T00:00:00+00:00")
    store.export_valuation_indicators("BBB", results, "2026-10-24T00:00:00+00:00")
    store.export_valuation_indicators("AAA", results[:1], "2026-10-25T00:00:00+00:00")
    rows = sqlite3.connect(tmp_path / "s.db").execute(
        "SELECT * FROM valuation_indicators ORDER BY ticker, indicator").fetchall()
    assert rows == [
        ("AAA", "epv", 1, None, 8.0, 10.0, 12.0, "per_share", '{"wacc": 0.1}', 2025, "2026-10-25T00:00:00+00:00"),
        ("BBB", "epv", 1, None, 8.0, 10.0, 12.0, "per_share", '{"wacc": 0.1}', 2025, "2026-10-24T00:00:00+00:00"),
        ("BBB", "reverse_dcf", 0, "no price", None, None, None, "rate", "{}", None, "2026-10-24T00:00:00+00:00"),
    ]


def _loader_conn(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def test_the_loader_reads_the_latest_snapshot_with_a_price(tmp_path):
    """BUG-187: a later run whose quote failed leaves a snapshot without price, market cap or beta."""
    db = _seed(tmp_path / "s.db")
    conn = _loader_conn(db)
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, risk_free_rate) "
                 "VALUES ('AAA', '2026-02-26T10:00:00', 0.05)")
    conn.commit()
    x = load_indicator_inputs(conn, "AAA")
    assert (x.market.price, x.market.market_cap, x.market.beta, x.market.risk_free_rate) == (50.0, 5.0e9, 1.1, 0.04)
    assert x.price_date == "2026-02-25T10:00:00"


def test_the_loader_reads_the_models_inputs_the_ps_range_and_the_book(tmp_path):
    db = _seed(tmp_path / "s.db")
    conn = _loader_conn(db)
    conn.execute("INSERT INTO valuation_ranges (ticker, metric, applicable, na_reason, as_of, n_months, "
                 "current_value, p25, median, p75, implied_value, computed_at) "
                 "VALUES ('AAA', 'ps', 1, NULL, '2026-01-28', 57, 2.1, 1.5, 2.0, 2.5, 46.6, '2026-10-24')")
    conn.commit()
    x = load_indicator_inputs(conn, "AAA")
    assert x.ticker == "AAA"
    assert x.ps_range == PsRange(applicable=True, na_reason=None, as_of="2026-01-28", n_months=57, current=2.1,
                                 p25=1.5, median=2.0, p75=2.5, implied_value=46.6)
    assert x.book[2025] == {"total_equity": pytest.approx(4 * REVENUE_2025),
                            "goodwill": pytest.approx(0.5 * REVENUE_2025),
                            "intangible_assets": pytest.approx(0.3 * REVENUE_2025)}
    assert len(x.book) == 12
    # The six models' own inputs (10 fiscal years, today's count) and M2's annual rows (11 years).
    assert len(x.valuation.fy_records) == 10 and x.valuation.shares_outstanding == 100e6
    assert x.fundamentals.sector_class == "general" and len(x.fundamentals.rows) == 11
    assert load_indicator_inputs(conn, "BBB").ps_range is None  # no row yet


def test_without_the_history_table_the_ps_value_says_to_backfill(tmp_path):
    db = _seed(tmp_path / "s.db")
    conn = _loader_conn(db)
    conn.execute("DROP TABLE valuation_ranges")
    conn.commit()
    x = load_indicator_inputs(conn, "AAA")
    assert x.ps_range is None
    assert ps_value(x).na_reason == "no valuation history (run the backfill)"


def test_the_indicators_never_touch_the_valuations(tmp_path, monkeypatch):
    """The six models, their median and the verdicts are the indicators' business in no way: MEDIAN_MODELS and the
    valuations table are pinned, and a run without the indicators stores the same valuations."""
    assert MEDIAN_MODELS == ("dcf", "ddm", "graham", "lynch", "multiples")
    assert not set(INDICATORS) & set(MEDIAN_MODELS)
    normal = _seed(tmp_path / "normal.db")
    compute_and_store(normal)

    def broken(inputs):
        raise RuntimeError("indicators broke")

    without = _seed(tmp_path / "without.db")
    monkeypatch.setattr(engine, "compute_indicators", broken)
    compute_and_store(without)

    def table(db, name):
        conn = sqlite3.connect(db)
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")
                   if r[1] not in ("computed_at", "ranked_at")]
        return sorted(map(repr, conn.execute(f"SELECT {', '.join(columns)} FROM {name}")))

    for name in ("valuations", "valuation_summary", "valuation_ranges", "quality_scores"):
        assert table(normal, name) == table(without, name), name
    models = {r[0] for r in sqlite3.connect(normal).execute("SELECT model FROM valuations")}
    assert models == {"dcf", "ddm", "graham", "lynch", "multiples", "owner_earnings"}
    assert not models & set(INDICATORS)
    assert len(_indicators(normal)) == 10 and not _indicators(without)
