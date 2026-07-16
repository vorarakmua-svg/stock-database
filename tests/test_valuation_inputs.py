"""ValuationInputs loader: builds the per-ticker bundle from SQLite."""
import sqlite3
from datetime import date

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.inputs import FYRecord, load_inputs


@pytest.fixture
def val_db(tmp_path):
    """Minimal DB with one general company, 5 FYs, snapshot, analyst, dividends, bars."""
    db_path = tmp_path / "val.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute(
        "INSERT INTO companies (ticker, sector_class) VALUES ('AAA', 'general')"
    )
    for i, fy in enumerate(range(2019, 2024)):
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted, "
            "depreciation_amortization, capex) "
            "VALUES ('AAA', ?, ?, ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", 100.0 + 10 * i, 500.0, 1.0 + 0.1 * i, 100.0,
             40.0, -60.0),
        )
        conn.execute(
            "INSERT INTO metrics_annual (ticker, fiscal_year, free_cash_flow, "
            "levered_fcf) VALUES ('AAA', ?, ?, ?)",
            (fy, 80.0 + 5 * i, 70.0 + 5 * i),
        )
        conn.execute(
            "INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', ?, ?)",
            (f"{fy}-12-30", 20.0 + i),
        )
    conn.execute(
        "INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, "
        "beta, risk_free_rate) VALUES ('AAA', '2024-01-05T00:00:00', 100.0, 1.2, 0.043)"
    )
    conn.execute(
        "INSERT INTO analyst_snapshots (ticker, collected_at, earnings_growth) "
        "VALUES ('AAA', '2024-01-05T00:00:00', 0.08)"
    )
    conn.execute(
        "INSERT INTO dividend_events (ticker, date, amount) VALUES "
        "('AAA', '2023-03-01', 0.25), ('AAA', '2023-09-01', 0.25)"
    )
    conn.commit()
    conn.close()
    return db_path


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_load_inputs_builds_fy_records_ascending(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.ticker == "AAA"
    assert inputs.sector_class == "general"
    assert [r.fiscal_year for r in inputs.fy_records] == [2019, 2020, 2021, 2022, 2023]
    assert inputs.fy_records[-1].net_income == 140.0


def test_fcf_prefers_levered_fcf(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.fy_records[0].fcf == 70.0  # levered_fcf, not free_cash_flow


def test_market_analyst_and_dividends_loaded(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.shares_outstanding == 100.0
    assert inputs.beta == 1.2
    assert inputs.risk_free_rate == 0.043
    assert inputs.analyst_growth == 0.08
    assert inputs.dividends == [("2023-03-01", 0.25), ("2023-09-01", 0.25)]


def test_fy_end_prices_use_close_on_or_before_period_end(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.fy_end_prices[2019] == 20.0
    assert inputs.fy_end_prices[2023] == 24.0


def test_missing_company_defaults_sector_general(val_db):
    conn = _connect(val_db)
    conn.execute("UPDATE companies SET sector_class = NULL WHERE ticker = 'AAA'")
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.sector_class == "general"


def test_as_of_from_latest_market_snapshot(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.as_of == date(2024, 1, 5)


def test_as_of_falls_back_to_today_without_snapshot(val_db):
    conn = _connect(val_db)
    conn.execute("DELETE FROM market_snapshots WHERE ticker = 'AAA'")
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.as_of == date.today()


def test_no_split_leaves_series_untouched(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert all(r.split_factor == 1.0 for r in inputs.fy_records)
    assert inputs.fy_records[0].eps_diluted == pytest.approx(1.0)
    assert inputs.fy_records[0].shares == 100.0


# ---- split normalization (per-share series onto the current share basis) ----


@pytest.fixture
def split_db(tmp_path):
    """5 FYs with a 4:1 split effective FY2022 (as-reported, unrestated)."""
    db_path = tmp_path / "split.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('SPL', 'general')")
    # eps as reported: pre-split years on the old (100-share) basis.
    rows = [
        (2019, 400.0, 4.00, 100.0),
        (2020, 440.0, 4.40, 100.0),
        (2021, 484.0, 4.84, 100.0),
        (2022, 532.4, 1.331, 400.0),
        (2023, 585.6, 1.464, 400.0),
    ]
    for fy, ni, eps, shares in rows:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted) "
            "VALUES ('SPL', ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", ni, 800.0, eps, shares),
        )
        conn.execute(
            "INSERT INTO metrics_annual (ticker, fiscal_year, ffo_per_share) "
            "VALUES ('SPL', ?, ?)",
            (fy, eps),
        )
    conn.commit()
    conn.close()
    return db_path


def test_split_normalizes_per_share_history_to_current_basis(split_db):
    conn = _connect(split_db)
    recs = load_inputs(conn, "SPL").fy_records
    conn.close()
    by_fy = {r.fiscal_year: r for r in recs}
    assert [by_fy[fy].split_factor for fy in range(2019, 2024)] == [4.0, 4.0, 4.0, 1.0, 1.0]
    # pre-split EPS divided by the factor -> a smooth 10%/yr series
    assert by_fy[2019].eps_diluted == pytest.approx(1.0)
    assert by_fy[2021].eps_diluted == pytest.approx(1.21)
    assert by_fy[2022].eps_diluted == pytest.approx(1.331)
    # share counts multiplied -> everything on the current 400-share basis
    assert by_fy[2019].shares == pytest.approx(400.0)
    assert by_fy[2019].ffo_per_share == pytest.approx(1.0)
    # split-invariant absolutes untouched
    assert by_fy[2019].net_income == pytest.approx(400.0)
    assert by_fy[2019].total_equity == pytest.approx(800.0)
    # bvps stays consistent: equity / current-basis shares
    assert by_fy[2019].bvps() == pytest.approx(2.0)
    assert by_fy[2023].bvps() == pytest.approx(2.0)


def test_split_detected_from_net_income_over_eps_when_shares_null(split_db):
    """GOOGL case: weighted_avg_shares_diluted is NULL; derive shares = NI / EPS."""
    conn = _connect(split_db)
    conn.execute("UPDATE financials_annual SET weighted_avg_shares_diluted = NULL "
                 "WHERE ticker = 'SPL'")
    recs = load_inputs(conn, "SPL").fy_records
    conn.close()
    by_fy = {r.fiscal_year: r for r in recs}
    assert by_fy[2019].split_factor == pytest.approx(4.0)
    assert by_fy[2019].eps_diluted == pytest.approx(1.0)


def test_ordinary_issuance_below_threshold_is_not_a_split(split_db):
    conn = _connect(split_db)
    conn.execute("UPDATE financials_annual SET weighted_avg_shares_diluted = 120.0, "
                 "eps_diluted = 4.0, net_income = 480.0 "
                 "WHERE ticker = 'SPL' AND fiscal_year >= 2022")
    recs = load_inputs(conn, "SPL").fy_records
    conn.close()
    assert all(r.split_factor == 1.0 for r in recs)
    assert recs[0].eps_diluted == pytest.approx(4.00)


def _seam_db(tmp_path, name, rows):
    """Build a 5-FY ticker 'SPL' from (fy, net_income, eps, shares) rows."""
    db_path = tmp_path / name
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('SPL', 'general')")
    for fy, ni, eps, shares in rows:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted) "
            "VALUES ('SPL', ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", ni, 800.0, eps, shares),
        )
    conn.commit()
    conn.close()
    return db_path


def test_huge_unit_seam_truncates_history_instead_of_splitting(tmp_path):
    """COP shape: shares stored in thousands then in units (959x) is NOT a split."""
    db = _seam_db(tmp_path, "units_up.db", [
        (2017, 1.05e9, 0.95, 1_100_000.0),
        (2018, 1.10e9, 1.00, 1_110_000.0),
        (2019, 1.15e9, 1.05, 1_123_536.0),
        (2020, 1.20e9, 1.11, 1_078_030_000.0),
        (2021, 1.25e9, 1.16, 1_070_000_000.0),
    ])
    conn = _connect(db)
    inputs = load_inputs(conn, "SPL")
    conn.close()
    assert [r.fiscal_year for r in inputs.fy_records] == [2020, 2021]
    assert all(r.split_factor == 1.0 for r in inputs.fy_records)
    assert inputs.history_truncated is True
    assert inputs.fy_records[0].eps_diluted == pytest.approx(1.11)


def test_tiny_unit_seam_truncates_history(tmp_path):
    """MCD/COP shape: shares drop by ~1000x at a seam -> unit error, not a split."""
    db = _seam_db(tmp_path, "units_down.db", [
        (2017, 1.05e9, 0.95, 1_500_000_000.0),
        (2018, 1.10e9, 1.00, 1_497_608_000.0),
        (2019, 1.15e9, 1.05, 1_491_067.0),
        (2020, 1.20e9, 1.11, 1_480_000.0),
        (2021, 1.25e9, 1.16, 1_470_000.0),
    ])
    conn = _connect(db)
    inputs = load_inputs(conn, "SPL")
    conn.close()
    assert [r.fiscal_year for r in inputs.fy_records] == [2019, 2020, 2021]
    assert all(r.split_factor == 1.0 for r in inputs.fy_records)
    assert inputs.history_truncated is True


def test_genuine_forward_split_still_normalizes(split_db):
    """Regression guard: AAPL/GOOGL/NVDA-shaped 4:1 split behavior is unchanged."""
    conn = _connect(split_db)
    inputs = load_inputs(conn, "SPL")
    conn.close()
    assert inputs.history_truncated is False
    by_fy = {r.fiscal_year: r for r in inputs.fy_records}
    assert [by_fy[fy].split_factor for fy in range(2019, 2024)] == [4.0, 4.0, 4.0, 1.0, 1.0]
    assert by_fy[2019].eps_diluted == pytest.approx(1.0)
    assert by_fy[2019].shares == pytest.approx(400.0)


def test_reverse_split_scales_old_eps_up_and_shares_down(tmp_path):
    """GE shape: 1:8 reverse split -> pre-split EPS x8, pre-split shares / 8."""
    db = _seam_db(tmp_path, "reverse.db", [
        (2019, 800.0, 1.00, 800.0),
        (2020, 880.0, 1.10, 800.0),
        (2021, 968.0, 9.68, 100.0),
        (2022, 1064.8, 10.648, 100.0),
        (2023, 1171.3, 11.713, 100.0),
    ])
    conn = _connect(db)
    inputs = load_inputs(conn, "SPL")
    conn.close()
    assert inputs.history_truncated is False
    by_fy = {r.fiscal_year: r for r in inputs.fy_records}
    assert by_fy[2019].split_factor == pytest.approx(0.125)
    assert by_fy[2020].split_factor == pytest.approx(0.125)
    assert by_fy[2021].split_factor == pytest.approx(1.0)
    # per-share values divided by f < 1 -> scaled UP by 8
    assert by_fy[2019].eps_diluted == pytest.approx(8.0)
    assert by_fy[2020].eps_diluted == pytest.approx(8.8)
    # share counts multiplied by f < 1 -> scaled DOWN by 8, onto the 100 basis
    assert by_fy[2019].shares == pytest.approx(100.0)
    assert by_fy[2019].bvps() == pytest.approx(8.0)


def test_ordinary_issuance_and_buyback_are_not_splits(tmp_path):
    """1.2x issuance (and a 0.9x buyback) leave the series untouched."""
    db = _seam_db(tmp_path, "issuance.db", [
        (2019, 100.0, 1.00, 100.0),
        (2020, 110.0, 1.10, 100.0),
        (2021, 120.0, 1.00, 120.0),   # 1.2x issuance
        (2022, 130.0, 1.20, 108.0),   # 0.9x buyback
        (2023, 140.0, 1.30, 108.0),
    ])
    conn = _connect(db)
    inputs = load_inputs(conn, "SPL")
    conn.close()
    assert inputs.history_truncated is False
    assert all(r.split_factor == 1.0 for r in inputs.fy_records)
    assert inputs.fy_records[0].eps_diluted == pytest.approx(1.0)
    assert len(inputs.fy_records) == 5


def test_fyrecord_eps_and_bvps_fallbacks():
    rec = FYRecord(fiscal_year=2023, period_end=None, net_income=100.0,
                   total_equity=500.0, eps_diluted=None, shares=50.0,
                   fcf=None, ffo_per_share=None)
    assert rec.eps() == 2.0
    assert rec.bvps() == 10.0
    rec2 = FYRecord(fiscal_year=2023, period_end=None, net_income=None,
                    total_equity=None, eps_diluted=1.5, shares=None,
                    fcf=None, ffo_per_share=None)
    assert rec2.eps() == 1.5
    assert rec2.bvps() is None


def test_load_inputs_carries_da_and_capex(val_db):
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    rec = inputs.fy_records[-1]
    assert rec.depreciation_amortization == 40.0
    assert rec.capex == -60.0  # as filed: capex is negative in the cash-flow statement


def test_split_normalization_leaves_da_and_capex_alone():
    """D&A and capex are absolute dollars, not per-share — a split does not
    restate them, so the normalizer must not scale them."""
    from src.valuation.inputs import FYRecord, _normalize_splits
    recs = [
        FYRecord(fiscal_year=2020, period_end=None, net_income=100.0,
                 total_equity=500.0, eps_diluted=8.0, shares=100.0, fcf=None,
                 ffo_per_share=None, depreciation_amortization=40.0, capex=-60.0),
        FYRecord(fiscal_year=2021, period_end=None, net_income=100.0,
                 total_equity=500.0, eps_diluted=2.0, shares=400.0, fcf=None,
                 ffo_per_share=None, depreciation_amortization=44.0, capex=-66.0),
    ]
    survivors, truncated = _normalize_splits(recs, [100.0, 400.0])
    assert truncated is False
    assert survivors[0].split_factor == 4.0          # 4:1 split detected
    assert survivors[0].eps_diluted == 2.0           # per-share restated
    assert survivors[0].depreciation_amortization == 40.0   # absolute, untouched
    assert survivors[0].capex == -60.0                      # absolute, untouched
