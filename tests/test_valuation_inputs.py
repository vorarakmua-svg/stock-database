"""ValuationInputs loader: builds the per-ticker bundle from SQLite."""
import sqlite3
from datetime import date, datetime

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
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


def test_fcf_basis_is_free_cash_flow(val_db):
    # The DCF's basis is OCF - capex, interest already through OCF. A stored levered_fcf
    # from before that fix took interest off again, so it isn't read.
    conn = _connect(val_db)
    inputs = load_inputs(conn, "AAA")
    conn.close()
    assert inputs.fy_records[0].fcf == 80.0  # free_cash_flow, not the stale levered_fcf


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


def _buyback_split_db(tmp_path, declared_splits):
    """4:1 split in 2022 while buybacks shrank the old share count (110 -> 105): the observed seam
    is 400 / 105 = 3.81, not 4 (AAPL: 20.0B / 5.252B = 3.808 around its 2020 4:1 split)."""
    db_path = tmp_path / "buyback_split.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('BBS', 'general')")
    for fy, eps, shares in [(2019, 4.00, 110.0), (2020, 4.40, 108.0), (2021, 4.84, 105.0),
                            (2022, 1.331, 400.0), (2023, 1.464, 400.0)]:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, net_income, total_equity, "
            "eps_diluted, weighted_avg_shares_diluted) VALUES ('BBS', ?, ?, ?, 800.0, ?, ?)",
            (fy, f"{fy}-12-31", eps * shares, eps, shares),
        )
    for date_str, ratio in declared_splits:
        conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('BBS', ?, ?)", (date_str, ratio))
    conn.commit()
    conn.close()
    return db_path


def test_split_factor_uses_the_declared_ratio_not_the_share_jump(tmp_path):
    # The share jump also carries a year of buybacks; split_events declares the exact 4:1.
    conn = _connect(_buyback_split_db(tmp_path, [("2022-06-01", 4.0)]))
    by_fy = {r.fiscal_year: r for r in load_inputs(conn, "BBS").fy_records}
    conn.close()
    assert by_fy[2021].split_factor == pytest.approx(4.0)
    assert by_fy[2021].eps_diluted == pytest.approx(4.84 / 4)


def test_split_factor_falls_back_to_the_share_jump_without_declared_splits(tmp_path):
    # Tickers without split_events rows keep the detected jump (the only evidence there is).
    conn = _connect(_buyback_split_db(tmp_path, []))
    by_fy = {r.fiscal_year: r for r in load_inputs(conn, "BBS").fy_records}
    conn.close()
    assert by_fy[2021].split_factor == pytest.approx(400.0 / 105.0)


def test_declared_run_matches_a_run_of_splits_and_ignores_unrelated_ones():
    from src.valuation.inputs import _declared_run
    nvda = [("2021-07-20", 4.0), ("2024-06-10", 10.0)]
    assert _declared_run(38.1, "2020-01-26", nvda) == nvda  # seam restated for both splits
    assert _declared_run(3.81, "2020-01-26", [("2022-06-01", 2.0)]) == []  # 2:1 is nowhere near
    assert _declared_run(3.81, "2023-01-01", [("2022-06-01", 4.0)]) == []  # split predates the seam


def _post_filing_split_db(tmp_path, split_date, ratio, snapshot_shares=None, market_cap_shares=None):
    """KLAC-like: FY2023-2025 as filed on 2025-08-08 (old basis), then one declared split, and
    optionally today's share count in a market snapshot (and a market cap counting other shares)."""
    db_path = tmp_path / "post_filing_split.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('KLA', 'general')")
    for fy, eps in [(2023, 25.00), (2024, 20.28), (2025, 30.37)]:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, filed_date, net_income, total_equity, "
            "eps_diluted, weighted_avg_shares_diluted) VALUES ('KLA', ?, ?, '2025-08-08', ?, 4.0e9, ?, 1.3375e8)",
            (fy, f"{fy}-06-30", eps * 1.3375e8, eps),
        )
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('KLA', ?, ?)", (split_date, ratio))
    if snapshot_shares is not None:
        conn.execute("INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, market_cap, "
                     "current_price) VALUES ('KLA', '2026-08-04T12:00:00', ?, ?, 100.0)",
                     (snapshot_shares, (market_cap_shares or snapshot_shares) * 100.0))
    conn.commit()
    conn.close()
    return db_path


def test_a_post_filing_ratio_the_share_count_does_not_show_is_not_a_split(tmp_path):
    # Yahoo's split history also holds spin-off factors of 3:2 or more (ABT 2.084 for AbbVie). ABT's
    # share count didn't move, so applying the factor as a split after the filing would halve every
    # per-share figure; a real split shows in today's share count.
    conn = _connect(_post_filing_split_db(tmp_path, "2026-01-02", 2.084, snapshot_shares=1.34e8))
    latest = load_inputs(conn, "KLA").fy_records[-1]
    conn.close()
    assert latest.split_factor == 1.0


def test_a_post_filing_split_the_share_count_confirms_still_applies(tmp_path):
    # KLAC: 133.75M shares filed, 1.31B today after its 10:1 split (buybacks make up the rest).
    conn = _connect(_post_filing_split_db(tmp_path, "2026-06-12", 10.0, snapshot_shares=1.306e9))
    latest = load_inputs(conn, "KLA").fy_records[-1]
    conn.close()
    assert latest.split_factor == 10.0


def test_a_simple_split_after_the_filing_applies_while_yahoos_count_lags(tmp_path):
    # Yahoo can show the pre-split count (and so market cap) for a while at the post-split price. A simple
    # ratio is a share split (all 430 simple factors in the real data are), so it needs no confirmation:
    # refusing it valued every per-share model ten times too high.
    conn = _connect(_post_filing_split_db(tmp_path, "2026-06-12", 10.0, snapshot_shares=1.33e8))
    latest = load_inputs(conn, "KLA").fy_records[-1]
    conn.close()
    assert latest.split_factor == 10.0


def test_a_split_factor_either_yahoo_count_confirms_applies(tmp_path):
    # A factor that isn't a simple ratio (GOOGL's 1.998 class-C dividend of 2014) needs a count that moved by
    # it. Yahoo's own count does here; market cap / price also counts partnership units (BX), and checking
    # only the larger figure refused the split.
    conn = _connect(_post_filing_split_db(tmp_path, "2026-06-12", 1.998, snapshot_shares=2.67e8,
                                          market_cap_shares=4.4e8))
    latest = load_inputs(conn, "KLA").fy_records[-1]
    conn.close()
    assert latest.split_factor == 1.998


def test_split_after_the_latest_filing_moves_every_year_to_todays_basis(tmp_path):
    # KLAC's FY2025 10-K (filed 2025-08-08) predates its 10:1 split of 2026-06-12: every stored
    # per-share figure is pre-split, while today's price is post-split.
    conn = _connect(_post_filing_split_db(tmp_path, "2026-06-12", 10.0))
    by_fy = {r.fiscal_year: r for r in load_inputs(conn, "KLA").fy_records}
    conn.close()
    assert [by_fy[fy].split_factor for fy in (2023, 2024, 2025)] == [10.0, 10.0, 10.0]
    assert by_fy[2025].eps_diluted == pytest.approx(3.037)
    assert by_fy[2025].shares == pytest.approx(1.3375e9)


@pytest.mark.parametrize("split_date, ratio", [
    ("2025-07-15", 10.0),    # between the year end and the filing: the 10-K already restated it
    ("2026-06-29", 0.9535),  # HON's spin-off price factor, not a share split
])
def test_later_split_events_that_are_not_pending_splits_change_nothing(tmp_path, split_date, ratio):
    conn = _connect(_post_filing_split_db(tmp_path, split_date, ratio))
    latest = load_inputs(conn, "KLA").fy_records[-1]
    conn.close()
    assert latest.split_factor == 1.0
    assert latest.eps_diluted == pytest.approx(30.37)


def _dividend_db(tmp_path, extra_dividends, splits):
    """Quarterly 0.11 dividends 2015-2017, plus *extra_dividends* and *splits* as (date, value)."""
    db_path = tmp_path / "dividends.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('DH', 'general')")
    regular = [(f"{y}-{m}-28", 0.11) for y in (2015, 2016, 2017) for m in ("03", "06", "09", "12")]
    for date_str, amount in regular + extra_dividends:
        conn.execute("INSERT INTO dividend_events (ticker, date, amount) VALUES ('DH', ?, ?)", (date_str, amount))
    for date_str, ratio in splits:
        conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('DH', ?, ?)", (date_str, ratio))
    conn.commit()
    conn.close()
    return db_path


def test_a_spin_off_recorded_as_a_dividend_is_not_a_dividend(tmp_path):
    # Yahoo records DHR's 2016 Fortive spin-off twice: a 1.319 price factor in its split history and a
    # $24.56 "dividend" the same day, 228x the regular payment. Only cash dividends belong in the DDM.
    conn = _connect(_dividend_db(tmp_path, [("2016-07-05", 24.56)], [("2016-07-05", 1.319)]))
    amounts = [amount for _, amount in load_inputs(conn, "DH").dividends]
    conn.close()
    assert 24.56 not in amounts
    assert len(amounts) == 12


@pytest.mark.parametrize("extra, splits", [
    ([("2016-07-05", 0.11)], [("2016-07-05", 1.008)]),  # SCCO: a regular dividend beside a stock dividend
    ([("2016-07-05", 7.00)], []),                       # COST: a special dividend, no spin-off that day
])
def test_cash_dividends_stay_even_on_unusual_days(tmp_path, extra, splits):
    conn = _connect(_dividend_db(tmp_path, extra, splits))
    amounts = [amount for _, amount in load_inputs(conn, "DH").dividends]
    conn.close()
    assert len(amounts) == 13


def _shares_db(tmp_path, snapshot, market_cap_shares, fy_diluted, price=100.0):
    """One FY of diluted shares and a market snapshot with its own share count and market cap."""
    db_path = tmp_path / "shares.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('SHR', 'general')")
    conn.execute("INSERT INTO financials_annual (ticker, fiscal_year, period_end, filed_date, net_income, "
                 "eps_diluted, weighted_avg_shares_diluted) "
                 "VALUES ('SHR', 2025, '2025-12-31', '2026-02-10', ?, 1.0, ?)", (fy_diluted, fy_diluted))
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, market_cap, current_price) "
                 "VALUES ('SHR', '2026-08-04T12:00:00', ?, ?, ?)", (snapshot, market_cap_shares * price, price))
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.parametrize("snapshot, market_cap_shares, fy_diluted, shares, source", [
    (5.867e9, 12.229e9, 12.230e9, 12.229e9, "market_cap / price"),  # GOOGL: Yahoo counts class A only
    (0.317e9, 0.317e9, 0.643e9, 0.643e9, "financials_annual"),      # HON: count and market cap both halved
    (14.594e9, 14.594e9, 15.005e9, 14.594e9, "market_snapshot"),    # AAPL: one class, buybacks since the FY
    (0.613e9, 0.613e9, 0.541e9, 0.613e9, "market_snapshot"),        # COF: shares issued since the FY
    (0.8758e9, 0.8975e9, 0.8697e9, 0.8758e9, "market_snapshot"),    # MRVL: one class, Yahoo's figures 2.5% apart
    (2.2961e9, 2.3973e9, 2.5652e9, 2.3973e9, "market_cap / price"),  # PLTR: 4.4% apart, the filings side with it
    (0.3243e9, 0.3800e9, 0.3264e9, 0.3800e9, "market_cap / price"),  # SPG: 17% apart, units (still undecided)
])
def test_per_share_values_count_every_share_class(tmp_path, snapshot, market_cap_shares, fy_diluted, shares, source):
    # DCF and owner earnings divide by the share count so the result compares with the price; Yahoo's
    # count can miss whole classes (GOOGL's DCF read about twice its value).
    conn = _connect(_shares_db(tmp_path, snapshot, market_cap_shares, fy_diluted))
    inputs = load_inputs(conn, "SHR")
    conn.close()
    assert inputs.shares_outstanding == pytest.approx(shares)
    assert inputs.shares_source == source


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


def _seam_db(tmp_path, name, rows, splits=(), revenue=None, first_bar=None):
    """Build a 5-FY ticker 'SPL' from (fy, net_income, eps, shares) rows and declared (date, ratio) splits, with
    each year's revenue from *revenue* (fy -> revenue) and a price bar on *first_bar* (the listing)."""
    db_path = tmp_path / name
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('SPL', 'general')")
    for fy, ni, eps, shares in rows:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, "
            "net_income, total_equity, eps_diluted, weighted_avg_shares_diluted, revenue) "
            "VALUES ('SPL', ?, ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", ni, 800.0, eps, shares, (revenue or {}).get(fy)),
        )
    for date_str, ratio in splits:
        conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('SPL', ?, ?)", (date_str, ratio))
    if first_bar:
        conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES ('SPL', ?, 20.0)", (first_bar,))
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


def test_a_declared_split_applies_even_when_eps_rose_across_its_seam(tmp_path):
    # NOW: the FY2025 10-K restated FY2023-24 for the 5:1 split of 2025-12-18, and a $723M tax benefit
    # lifted 2023 EPS past the ratio (1.60 -> 1.68), so the EPS-direction test vetoed the declared split
    # and FY2016-22 stayed on the pre-split basis beside split-adjusted prices.
    db = _seam_db(tmp_path, "eps_rose.db", [
        (2019, 300.0, 1.50, 200.0),
        (2020, 320.0, 1.60, 200.0),
        (2021, 1700.0, 1.70, 1000.0),
        (2022, 1800.0, 1.80, 1000.0),
        (2023, 1900.0, 1.90, 1000.0),
    ], splits=[("2023-06-01", 5.0)])
    conn = _connect(db)
    by_fy = {r.fiscal_year: r for r in load_inputs(conn, "SPL").fy_records}
    conn.close()
    assert [by_fy[fy].split_factor for fy in range(2019, 2024)] == [5.0, 5.0, 1.0, 1.0, 1.0]
    assert by_fy[2020].eps_diluted == pytest.approx(0.32)


def test_an_undeclared_share_jump_without_an_eps_drop_is_not_a_split(tmp_path):
    # An acquisition paid in shares brings its own earnings: shares x1.74 while EPS rose (BX's FY2018
    # seam). No declared split explains it, so the per-share history stays as reported.
    db = _seam_db(tmp_path, "acquisition.db", [
        (2019, 100.0, 1.00, 100.0),
        (2020, 110.0, 1.10, 100.0),
        (2021, 200.0, 1.15, 174.0),
        (2022, 215.0, 1.24, 174.0),
        (2023, 230.0, 1.32, 174.0),
    ])
    conn = _connect(db)
    recs = load_inputs(conn, "SPL").fy_records
    conn.close()
    assert all(r.split_factor == 1.0 for r in recs)


def _factors(db):
    conn = _connect(db)
    recs = load_inputs(conn, "SPL").fy_records
    conn.close()
    return {r.fiscal_year: r.split_factor for r in recs}


def test_a_spin_off_price_factor_never_explains_a_share_seam(tmp_path):
    # RTX (BUG-185): Raytheon's merger raised the count 1.57x into FY2020, and Yahoo's 1.589 for the Carrier and
    # Otis spin-offs (2020-04-03) sits within the tolerance of it. A spin-off changes no share count.
    db = _seam_db(tmp_path, "spin.db", [
        (2018, 5000.0, 6.20, 806.0),
        (2019, 5500.0, 6.40, 860.0),
        (2020, -3500.0, -2.60, 1350.0),
        (2021, 3900.0, 2.60, 1500.0),
        (2022, 5200.0, 3.50, 1486.0),
    ], splits=[("2020-04-03", 1.589)], first_bar="1990-01-02")  # listed long before: no listing jump
    assert set(_factors(db).values()) == {1.0}


def test_a_merger_whose_revenue_rose_with_the_count_is_not_a_split(tmp_path):
    # LIN (BUG-185): Praxair's merger with Linde AG, 1.88x the shares in FY2019 while EPS fell (merger costs)
    # and revenue rose 1.89x with them. A split leaves revenue where it was.
    db = _seam_db(tmp_path, "merger.db", [
        (2016, 3800.0, 13.10, 290.0),
        (2017, 4000.0, 13.79, 290.0),
        (2018, 4200.0, 14.48, 290.0),
        (2019, 2300.0, 4.20, 547.6),
        (2020, 2500.0, 4.60, 543.5),
    ], revenue={2016: 11.4e9, 2017: 12.0e9, 2018: 14.9e9, 2019: 28.2e9, 2020: 27.2e9})
    assert set(_factors(db).values()) == {1.0}


def test_a_diluted_count_that_swings_alone_is_not_a_split(tmp_path):
    # BX (BUG-185): the diluted count takes the Holdings units in and out (1,195M, 666M, 1,207M, 676M) while
    # the count its EPS divides by (net income / EPS) stays near 670M. A split moves both.
    db = _seam_db(tmp_path, "swing.db", [
        (2015, 710.0, 1.04, 1188.0),
        (2016, 1040.0, 1.56, 1195.0),
        (2017, 1471.0, 2.21, 666.0),
        (2018, 1541.0, 2.26, 1207.0),
        (2019, 2049.0, 3.03, 676.0),
    ])
    assert set(_factors(db).values()) == {1.0}


def test_years_before_the_listing_take_the_public_count(tmp_path):
    # PLTR (BUG-185): listed on 2020-09-30. Its weighted counts before then leave out the preferred stock that
    # converted (544M, 577M, 979M, then 1,924M in the listing year), so every jump into the public count folds,
    # whatever EPS did; before, only the one EPS happened to agree with did.
    db = _seam_db(tmp_path, "listing.db", [
        (2017, -580.0, -1.07, 544.0),
        (2018, -580.0, -1.01, 577.0),
        (2019, -1170.0, -1.19, 979.0),
        (2020, -520.0, -0.27, 1923.6),
        (2021, -373.0, -0.19, 1960.0),
    ], first_bar="2020-09-30")
    factors = _factors(db)
    assert factors[2017] == pytest.approx(1923.6 / 577.0) and factors[2018] == pytest.approx(1923.6 / 577.0)
    assert factors[2019] == pytest.approx(1923.6 / 979.0) and factors[2020] == 1.0


def test_a_listing_jump_folds_though_eps_changed_sign(tmp_path):
    # APP (BUG-185), a year earlier here: listed in April 2020, so FY2019's count (214.9M) leaves out the
    # converted preferred, and the jump folds though EPS changed sign.
    db = _seam_db(tmp_path, "app.db", [
        (2018, -100.0, -0.47, 212.4),
        (2019, -120.0, -0.56, 214.9),
        (2020, 35.0, 0.10, 342.8),
        (2021, 360.0, 0.98, 367.0),
        (2022, -190.0, -0.51, 373.0),
    ], first_bar="2020-04-15")
    factors = _factors(db)
    assert factors[2018] == pytest.approx(342.8 / 214.9) and factors[2020] == 1.0


def test_a_declared_split_explains_one_seam_only(tmp_path):
    # A 2:1 split in 2021 shows at the FY2020 -> FY2021 seam. An older acquisition that doubled the share
    # count (EPS up, FY2018) dates before the same split, but the split can't explain both seams.
    db = _seam_db(tmp_path, "one_seam.db", [
        (2017, 100.0, 1.00, 100.0),
        (2018, 210.0, 1.05, 200.0),
        (2019, 230.0, 1.15, 200.0),
        (2020, 250.0, 1.25, 200.0),
        (2021, 270.0, 0.675, 400.0),
    ], splits=[("2021-06-01", 2.0)])
    conn = _connect(db)
    by_fy = {r.fiscal_year: r for r in load_inputs(conn, "SPL").fy_records}
    conn.close()
    assert [by_fy[fy].split_factor for fy in range(2017, 2022)] == [2.0, 2.0, 2.0, 2.0, 1.0]


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


def test_the_rates_day_and_source_reach_the_inputs(tmp_path):
    db_path = _shares_db(tmp_path, 1.0e9, 1.0e9, 1.0e9)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE market_snapshots SET risk_free_rate = 0.0515, risk_free_rate_date = '2026-10-19', "
                 "risk_free_rate_source = 'treasury_gov' WHERE ticker = 'SHR'")
    conn.commit()
    conn.close()
    conn = _connect(db_path)
    inputs = load_inputs(conn, "SHR")
    conn.close()
    assert (inputs.risk_free_rate, inputs.risk_free_rate_date, inputs.risk_free_rate_source) == (
        0.0515, "2026-10-19", "treasury_gov")


def test_inputs_load_from_a_database_without_the_rate_columns(tmp_path):
    # The backfill reads the database without migrating it, so a database written before the rate's day and
    # source were stored must still load (the valuations of every ticker used to fail on a missing column).
    db_path = _shares_db(tmp_path, 1.0e9, 1.0e9, 1.0e9)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE market_snapshots DROP COLUMN risk_free_rate_date")
    conn.execute("ALTER TABLE market_snapshots DROP COLUMN risk_free_rate_source")
    conn.commit()
    conn.close()
    conn = _connect(db_path)
    inputs = load_inputs(conn, "SHR")
    conn.close()
    assert (inputs.shares_outstanding, inputs.risk_free_rate_date, inputs.risk_free_rate_source) == (1.0e9, None, None)


def _merger_then_split_db(tmp_path):
    """FY2016-2025, each filed the February after: a stock-paid merger doubles the share count in FY2020 while
    EPS keeps rising, and a 2:1 split follows the FY2025 10-K."""
    db_path = tmp_path / "merger_then_split.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('MRG', 'general')")
    for fy in range(2016, 2026):
        shares = 1.0e8 if fy < 2020 else 2.0e8
        eps = 2.0 + 0.1 * (fy - 2016) if fy < 2020 else 2.6 + 0.1 * (fy - 2020)
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, filed_date, net_income, total_equity, "
            "eps_diluted, weighted_avg_shares_diluted) VALUES ('MRG', ?, ?, ?, ?, 1.0e9, ?, ?)",
            (fy, f"{fy}-12-31", f"{fy + 1}-02-20", eps * shares, eps, shares),
        )
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('MRG', '2026-06-01', 2.0)")
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, market_cap, current_price) "
                 "VALUES ('MRG', '2026-10-20T09:00:00', 4.0e8, 1.2e10, 30.0)")
    conn.commit()
    conn.close()
    return db_path


def test_a_split_after_the_latest_filing_never_explains_an_older_seam(tmp_path):
    # Code review 5 (I1): the 2:1 split of 2026-06-01, made after the FY2025 10-K, also explained the FY2019 ->
    # FY2020 merger seam, so FY2016-19 were split twice (factor 4.0, EPS 0.50-0.575) and Lynch read 13.4% growth.
    conn = _connect(_merger_then_split_db(tmp_path))
    records = load_inputs(conn, "MRG").fy_records
    conn.close()
    assert [(r.fiscal_year, r.split_factor, round(r.eps_diluted, 3)) for r in records[:5]] == [
        (2016, 2.0, 1.0), (2017, 2.0, 1.05), (2018, 2.0, 1.1), (2019, 2.0, 1.15), (2020, 2.0, 1.3)]


def _with_yahoo_series(db_path):
    """Yahoo's dividends and FY-end price bars, adjusted for every split it knew when a run on 2026-10-01 fetched them,
    the day the bars run to."""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO dividend_events (ticker, date, amount) VALUES ('KLA', '2025-05-15', 0.19)")
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES ('KLA', ?, ?)",
                     [("2025-06-30", 60.0), ("2026-10-01", 80.0)])
    conn.commit()
    conn.close()
    return db_path


def _with_bars_from(db_path, served, closes):
    """Bars a run served on *served*, the day they run to: *closes* by date."""
    conn = sqlite3.connect(db_path)
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES ('KLA', ?, ?)", closes + [(served, 900.0)])
    conn.commit()
    conn.close()
    return db_path


def test_bars_from_before_a_split_stay_on_the_older_prices_basis(tmp_path):
    # BUG-094 (code review 9): a later run stored a split while its quote and price-history requests failed. Its bars,
    # served with the price on 2026-08-04, were taken back by the split as if refetched since: the multiples band read
    # 9,833.66 against 983.37.
    db_path = _post_filing_split_db(tmp_path, "2026-09-01", 10.0, snapshot_shares=1.3375e8)
    conn = _connect(_with_bars_from(db_path, "2026-08-04", [("2025-06-30", 600.0)]))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert inputs.fy_end_prices == {2025: 600.0}


def test_bars_served_on_a_splits_own_day_are_after_it(tmp_path):
    # Yahoo splits the history on the split's day, so bars that reach it carry the split.
    db_path = _post_filing_split_db(tmp_path, "2026-09-01", 10.0, snapshot_shares=1.3375e8)
    conn = _connect(_with_bars_from(db_path, "2026-09-01", [("2025-06-30", 60.0)]))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert inputs.fy_end_prices == {2025: 600.0}


def test_bars_from_before_a_split_go_onto_a_newer_prices_basis(tmp_path):
    # BUG-094 (iteration 65): a later run's quote worked after the split while its bar request failed, so the new price
    # sat beside bars a split behind it: the multiples band read 983.37 against 98.34.
    db_path = _post_filing_split_db(tmp_path, "2026-09-01", 10.0, snapshot_shares=1.3375e8)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, market_cap, current_price) "
                 "VALUES ('KLA', '2026-10-01T12:00:00', 1.3375e9, 1.3375e11, 100.0)")
    conn.commit()
    conn.close()
    conn = _connect(_with_bars_from(db_path, "2026-08-04", [("2025-06-30", 600.0)]))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert (inputs.fy_records[-1].split_factor, inputs.fy_end_prices) == (10.0, {2025: 60.0})


def test_a_split_after_the_latest_price_leaves_every_figure_on_that_prices_basis(tmp_path):
    # Code review 5 (I2): a run whose quote failed keeps the older snapshot (BUG-033's fix) but stores the split and
    # Yahoo's split-adjusted dividends and bars. The per-share figures moved past the split while the price didn't,
    # so every model came out about a tenth of the price beside it.
    db_path = _post_filing_split_db(tmp_path, "2026-09-01", 10.0, snapshot_shares=1.3375e8)
    conn = _connect(_with_yahoo_series(db_path))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    latest = inputs.fy_records[-1]
    assert (latest.split_factor, latest.eps_diluted) == (1.0, 30.37)
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 1.9)]
    assert {fy: round(price, 6) for fy, price in inputs.fy_end_prices.items()} == {2025: 600.0}


def test_a_split_before_the_latest_price_leaves_yahoos_figures_as_they_are(tmp_path):
    db_path = _post_filing_split_db(tmp_path, "2026-06-12", 10.0, snapshot_shares=1.306e9)
    conn = _connect(_with_yahoo_series(db_path))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert inputs.fy_records[-1].split_factor == 10.0
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 0.19)]
    assert inputs.fy_end_prices == {2025: 60.0}


@pytest.mark.parametrize("annual_rows", ["none", "without a filing date"])
def test_a_split_after_the_latest_price_takes_yahoos_series_back_whatever_the_sec_record_holds(tmp_path, annual_rows):
    # BUG-078 (code review 7): Yahoo's dividends and bars went back to the price's basis only when the latest annual
    # row had a filing date, so a company without SEC annual financials (an IFRS filer like TSM, whose only model is
    # DDM) kept Yahoo's split-adjusted dividends beside the older price, and its DDM came out a tenth of its value.
    db_path = _post_filing_split_db(tmp_path, "2026-09-01", 10.0, snapshot_shares=1.3375e8)
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM financials_annual" if annual_rows == "none"
                 else "UPDATE financials_annual SET filed_date = NULL")
    conn.commit()
    conn.close()
    conn = _connect(_with_yahoo_series(db_path))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 1.9)]
    assert {fy: round(price, 6) for fy, price in inputs.fy_end_prices.items()} == (
        {} if annual_rows == "none" else {2025: 600.0})


def _with_dividend_served_on(db_path, amount, served_on):
    """One payment, of 2025-05-15, as Yahoo served it on *served_on*."""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO dividend_events (ticker, date, amount, served_on) VALUES ('KLA', '2025-05-15', ?, ?)",
                 (amount, served_on))
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.parametrize("split_date, served_on, stored, expected", [
    ("2026-06-12", "2026-06-01", 1.9, 0.19),  # served before a split the price came after: down by it (BUG-103)
    ("2026-09-01", "2026-08-04", 1.9, 1.9),   # served with the price, before a later split: as it is
    ("2026-09-01", "2026-10-01", 0.19, 1.9),  # served after a split the price came before: up by it (BUG-078, BUG-094)
    ("2026-06-12", "2026-10-01", 0.19, 0.19),  # served, like the price, after the split: as it is
    ("2026-06-12", "2026-06-12", 0.19, 0.19),  # served on the split's day, which Yahoo has split by then
])
def test_a_dividend_goes_to_the_prices_basis_from_the_day_yahoo_served_it(tmp_path, split_date, served_on, stored,
                                                                          expected):
    # BUG-103 (code review 10): Yahoo's dividends went to the price's basis by every split after the price, as if Yahoo
    # had served them after all of them. A run whose dividend request failed keeps payments from before a split beside
    # a price after it, and DDM came out ten times its value. The price here is of 2026-08-04.
    db_path = _post_filing_split_db(tmp_path, split_date, 10.0, snapshot_shares=1.3375e8)
    conn = _connect(_with_dividend_served_on(db_path, stored, served_on))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", expected)]


def test_a_run_before_the_open_on_a_splits_day_keeps_its_payments_pre_split(tmp_path):
    # BUG-117 (code review 11), end to end: a run at 08:00 local (+0700) on the split's day saw New York's evening
    # before, no split yet, so its payment is pre-split; the price, a later run's, is after the split.
    db_path = _post_filing_split_db(tmp_path, "2026-06-12", 10.0, snapshot_shares=1.306e9)
    run = StockData(ticker="KLA", cik="000", company_name="KLA Corp")
    run.collected_at = datetime(2026, 6, 12, 8)
    run.dividend_history = {"dividend_payments": [{"date": "2025-05-15", "amount": 1.9}]}
    run.price_bars = [{"date": "2026-06-11", "close": 1000.0}]
    SQLiteStore(db_path=db_path).export([run])
    conn = _connect(db_path)
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 0.19)]


def test_dividends_of_a_database_without_served_days_are_on_every_splits_basis(tmp_path):
    # A database from before `dividend_events.served_on`, read by the backfill without migrating, keeps the rule its
    # payments were read by: Yahoo's basis after every stored split.
    db_path = _post_filing_split_db(tmp_path, "2026-09-01", 10.0, snapshot_shares=1.3375e8)
    conn = sqlite3.connect(_with_yahoo_series(db_path))
    conn.execute("ALTER TABLE dividend_events DROP COLUMN served_on")
    conn.commit()
    conn.close()
    conn = _connect(db_path)
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 1.9)]


def test_without_a_price_a_dividend_stays_as_yahoo_served_it(tmp_path):
    # No snapshot has a price, so no basis to bring it to (as before the served day).
    db_path = _post_filing_split_db(tmp_path, "2026-06-12", 10.0)
    conn = _connect(_with_dividend_served_on(db_path, 0.19, "2026-10-01"))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 0.19)]


def test_a_dividend_without_a_served_day_beside_bars_a_split_behind_is_on_every_splits_basis(tmp_path):
    # BUG-094's case: a run stored the split and its dividends while its bar request failed. Such a payment, stored
    # before the column (or not dated by the migration), is on the new basis, though the bars are a split behind.
    db_path = _post_filing_split_db(tmp_path, "2026-09-01", 10.0, snapshot_shares=1.3375e8)
    conn = _connect(_with_bars_from(_with_dividend_served_on(db_path, 0.19, None), "2026-08-04",
                                    [("2025-06-30", 600.0)]))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 1.9)]


def _restating_filings_db(tmp_path, split_date, shares_by_fy, eps_by_fy):
    """FY2016-2025 with every 10-K's view of each year in the vintages: the 10-K for FY f, filed the February after,
    reports FY f-2 to f, restated for a split made before it. *shares_by_fy* and *eps_by_fy* are on the post-split
    basis; a filing made before the split reports half the shares and twice the EPS (a 2:1 split)."""
    db_path = tmp_path / "restating_filings.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('RST', 'general')")
    for fy in range(2016, 2026):
        reports = [f"{f + 1}-02-20" for f in range(fy, min(fy + 2, 2025) + 1)]
        for filed in reports:
            shares, eps = shares_by_fy[fy], eps_by_fy[fy]
            if filed < split_date:
                shares, eps = shares / 2, eps * 2
            conn.execute("INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn, filed_date, period_end, "
                         "net_income, eps_diluted, weighted_avg_shares_diluted) VALUES ('RST', ?, ?, ?, ?, ?, ?, ?)",
                         (fy, f"acc-{filed[:4]}", filed, f"{fy}-12-31", eps * shares, eps, shares))
        conn.execute("INSERT INTO financials_annual (ticker, fiscal_year, period_end, filed_date, net_income, "
                     "total_equity, eps_diluted, weighted_avg_shares_diluted) VALUES ('RST', ?, ?, ?, ?, 1.0e9, ?, ?)",
                     (fy, f"{fy}-12-31", reports[-1], eps * shares, eps, shares))
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('RST', ?, 2.0)", (split_date,))
    conn.commit()
    conn.close()
    return db_path


def test_a_split_every_stored_year_already_reflects_explains_no_seam(tmp_path):
    # Code review 5's variant (BUG-059): a 2:1 split in March 2017 restated every year stored (FY2016 was last filed
    # in 2019), and a stock-paid merger doubles the share count in FY2017 while EPS rises. The split took the
    # merger's seam, halving FY2016's EPS: Lynch read 12.6% growth instead of 4.2%.
    shares = {fy: 1.0e8 if fy < 2017 else 2.0e8 for fy in range(2016, 2026)}
    eps = {fy: 2.0 if fy < 2017 else 2.1 + 0.1 * (fy - 2017) for fy in range(2016, 2026)}
    conn = _connect(_restating_filings_db(tmp_path, "2017-03-01", shares, eps))
    first = load_inputs(conn, "RST").fy_records[0]
    conn.close()
    assert (first.fiscal_year, first.split_factor, first.eps_diluted) == (2016, 1.0, 2.0)


def test_a_split_between_two_years_filings_still_explains_their_seam(tmp_path):
    # NOW-like: the FY2023 10-K (filed 2024-02-20) restates FY2021-23 for a 2:1 split of 2023-06-01, while FY2020 was
    # last filed before it. EPS grew enough to rise across the seam, so only the declared split explains it.
    shares = {fy: 2.0e8 for fy in range(2016, 2026)}
    eps = {fy: 0.5 + 0.1 * (fy - 2016) if fy < 2021 else 2.0 + 0.1 * (fy - 2021) for fy in range(2016, 2026)}
    conn = _connect(_restating_filings_db(tmp_path, "2023-06-01", shares, eps))
    records = load_inputs(conn, "RST").fy_records
    conn.close()
    assert [(r.fiscal_year, r.split_factor) for r in records[3:7]] == [
        (2019, 2.0), (2020, 2.0), (2021, 1.0), (2022, 1.0)]


def _restated_after_the_price_db(tmp_path, snapshot_day, price, snapshot_shares):
    """FY2023-25 as the FY2025 10-K, filed 2026-02-20, restates them for a 10:1 split of 2026-02-01, with Yahoo's
    split-adjusted dividends and FY-end bars as a run on 2026-03-01 served them (the day the bars run to), and the
    latest market snapshot taken on *snapshot_day*."""
    db_path = tmp_path / "restated_after_the_price.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('KLA', 'general')")
    for fy, eps in [(2023, 2.500), (2024, 2.028), (2025, 3.037)]:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, filed_date, net_income, total_equity, "
            "eps_diluted, weighted_avg_shares_diluted) VALUES ('KLA', ?, ?, '2026-02-20', ?, 4.0e9, ?, 1.3375e9)",
            (fy, f"{fy}-12-31", eps * 1.3375e9, eps),
        )
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('KLA', '2026-02-01', 10.0)")
    conn.execute("INSERT INTO dividend_events (ticker, date, amount) VALUES ('KLA', '2025-05-15', 0.19)")
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES ('KLA', ?, ?)",
                     [("2025-12-31", 60.0), ("2026-03-01", 80.0)])
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, market_cap, current_price) "
                 "VALUES ('KLA', ?, ?, ?, ?)", (f"{snapshot_day}T12:00:00", snapshot_shares,
                                                snapshot_shares * price, price))
    conn.commit()
    conn.close()
    return db_path


def test_a_split_the_filing_restated_after_the_price_is_taken_back_out(tmp_path):
    # BUG-060: the latest price is from before a split that the latest 10-K already restated (a later run's quote
    # failed). The filed figures were past the split and the price wasn't: every model a tenth of the price.
    conn = _connect(_restated_after_the_price_db(tmp_path, "2026-01-10", 1000.0, 1.3375e8))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    latest = inputs.fy_records[-1]
    assert (latest.split_factor, round(latest.eps_diluted, 6), latest.shares) == (0.1, 30.37, 1.3375e8)
    assert [(day, round(amount, 6)) for day, amount in inputs.dividends] == [("2025-05-15", 1.9)]
    assert {fy: round(close, 6) for fy, close in inputs.fy_end_prices.items()} == {2025: 600.0}


def test_a_split_the_filing_restated_before_the_price_stays(tmp_path):
    conn = _connect(_restated_after_the_price_db(tmp_path, "2026-03-01", 100.0, 1.3375e9))
    inputs = load_inputs(conn, "KLA")
    conn.close()
    latest = inputs.fy_records[-1]
    assert (latest.split_factor, latest.eps_diluted, latest.shares) == (1.0, 3.037, 1.3375e9)
    assert inputs.dividends == [("2025-05-15", 0.19)]
    assert inputs.fy_end_prices == {2025: 60.0}


def _foreign_issuer_db(tmp_path, form, filed_shares, snapshot_shares, price, period_end_shares=None,
                       market_count=None):
    """FY2023-25 of a filer whose annual report is *form* (EPS per filed share), and today's market snapshot: Yahoo's
    count *snapshot_shares*, and a market cap of *market_count* (default: that count) times the price."""
    db_path = tmp_path / "foreign_issuer.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('ADR', 'general')")
    for fy, eps in [(2023, 1.07), (2024, 1.36), (2025, 1.70)]:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, filed_date, form, net_income, "
            "total_equity, eps_diluted, weighted_avg_shares_diluted, shares_outstanding) "
            "VALUES ('ADR', ?, ?, ?, ?, ?, 1.0e11, ?, ?, ?)",
            (fy, f"{fy}-12-31", f"{fy + 1}-04-15", form, eps * filed_shares, eps, filed_shares, period_end_shares),
        )
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, market_cap, current_price) "
                 "VALUES ('ADR', '2026-10-20T09:00:00', ?, ?, ?)",
                 (snapshot_shares, (market_count or snapshot_shares) * price, price))
    conn.commit()
    conn.close()
    return db_path


def test_a_foreign_issuers_per_share_figures_move_to_the_ads_basis(tmp_path):
    # Code review 6 (I1): a 20-F counts ordinary shares while Yahoo quotes and counts the ADS (five shares each, like
    # TSM's 25.93B and 5.186B), so every per-share model came out a fifth of the price's basis.
    from src.valuation.engine import run_valuations

    conn = _connect(_foreign_issuer_db(tmp_path, "20-F", 25.93e9, 5.186e9, 421.70))
    inputs = load_inputs(conn, "ADR")
    conn.close()
    latest = inputs.fy_records[-1]
    assert inputs.ads_ratio == 5.0
    assert (round(latest.eps_diluted, 6), round(latest.shares), inputs.shares_outstanding) == (8.5, 5.186e9, 5.186e9)
    assert {r.assumptions.get("ads_ratio") for r in run_valuations(inputs) if r.applicable} == {5.0}


@pytest.mark.parametrize("form, filed_shares", [("10-K", 25.93e9), ("20-F", 5.17e9)])
def test_the_ads_basis_is_only_for_a_foreign_issuer_whose_count_is_a_multiple(tmp_path, form, filed_shares):
    # A 10-K filer's gap is a share class or a stale count (HON), and ARM's ADS is one share.
    conn = _connect(_foreign_issuer_db(tmp_path, form, filed_shares, 5.186e9, 421.70))
    inputs = load_inputs(conn, "ADR")
    conn.close()
    assert (inputs.ads_ratio, inputs.fy_records[-1].eps_diluted) == (1.0, 1.70)


def _dividend_payer_db(tmp_path, bars):
    """FY2021-22 of a payer whose market close is 100 throughout, and two $2 dividends in 2022: Yahoo's adjusted
    prices are 96.04 (2021-12-31, 2022-03-11), 98.0 (2022-06-13) and 100.0 (2022-12-30); the ex-dates trade at
    97 and 99 (95.06 and 99.0 adjusted), which the factors mustn't read. *bars* says how they're stored."""
    db_path = tmp_path / "payer.db"
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('PAY', 'general')")
    for fy in (2021, 2022):
        conn.execute("INSERT INTO financials_annual (ticker, fiscal_year, period_end, net_income, total_equity, "
                     "eps_diluted, weighted_avg_shares_diluted) VALUES ('PAY', ?, ?, 5.0e8, 4.0e9, 5.0, 1.0e8)",
                     (fy, f"{fy}-12-31"))
    conn.execute("INSERT INTO dividend_events (ticker, date, amount) VALUES ('PAY', '2022-03-14', 2.0), "
                 "('PAY', '2022-06-14', 2.0)")
    adjusted = [("2021-12-31", 96.04), ("2022-03-11", 96.04), ("2022-03-14", 95.06), ("2022-06-13", 98.0),
                ("2022-06-14", 99.0), ("2022-12-30", 100.0)]
    for day, close in adjusted:
        conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES ('PAY', ?, ?)", (day, close))
    if bars == "flagged":
        conn.execute("UPDATE price_bars SET dividend_adjusted = 1")
    elif bars == "before the flag":
        conn.execute("ALTER TABLE price_bars DROP COLUMN dividend_adjusted")
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.parametrize("bars, expected", [
    ("flagged", {2021: 100.0, 2022: 100.0}),
    ("before the flag", {2021: 100.0, 2022: 100.0}),
    ("market closes", {2021: 96.04, 2022: 100.0}),
])
def test_fy_end_prices_are_market_closes(tmp_path, bars, expected):
    # BUG-075: a bar stored from yfinance's default history is Yahoo's dividend-adjusted price, lower by every dividend
    # paid since; its market close is rebuilt. A bar the fetch stored as a market close is read as it is.
    conn = _connect(_dividend_payer_db(tmp_path, bars))
    inputs = load_inputs(conn, "PAY")
    conn.close()
    assert {fy: round(price, 6) for fy, price in inputs.fy_end_prices.items()} == expected


@pytest.mark.parametrize("bars", ["flagged", "before the flag"])
def test_a_dividend_after_the_last_adjusted_bar_is_not_undone(tmp_path, bars):
    # BUG-084 (code review 8): Yahoo adjusted the stored bars for the dividends it knew when it served them. A later
    # run whose bar request failed still stored its newer dividends, and the rebuild undid them too: FY-end prices
    # about 0.5% high for each stale quarter.
    db_path = _dividend_payer_db(tmp_path, bars)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO dividend_events (ticker, date, amount) VALUES ('PAY', '2023-03-14', 2.0)")
    conn.commit()
    conn.close()
    conn = _connect(db_path)
    inputs = load_inputs(conn, "PAY")
    conn.close()
    assert {fy: round(price, 6) for fy, price in inputs.fy_end_prices.items()} == {2021: 100.0, 2022: 100.0}


def test_a_dividend_on_the_last_adjusted_bars_day_is_undone(tmp_path):
    # The bars Yahoo served on an ex-date already carry that day's dividend: with the payer's bars ending on its second
    # ex-date, 2022-06-14, FY2021's close still comes back to 100.
    db_path = _dividend_payer_db(tmp_path, "flagged")
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM price_bars WHERE date > '2022-06-14'")
    conn.commit()
    conn.close()
    conn = _connect(db_path)
    inputs = load_inputs(conn, "PAY")
    conn.close()
    assert {fy: round(price, 6) for fy, price in inputs.fy_end_prices.items()} == {2021: 100.0, 2022: 99.0}


def _payer_after_a_split(tmp_path, split_day, served_on, halve=False, bars_too=False):
    """The payer with a 2:1 split on *split_day*, its payments as served on *served_on* (halved: the new basis), and
    with *bars_too* its adjusted bars restated for the split, as Yahoo serves them after it."""
    db_path = _dividend_payer_db(tmp_path, "flagged")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('PAY', ?, 2.0)", (split_day,))
    conn.execute("UPDATE dividend_events SET amount = amount / ?, served_on = ?", (2.0 if halve else 1.0, served_on))
    if bars_too:
        conn.execute("UPDATE price_bars SET close = close / 2.0")
    conn.commit()
    conn.close()
    conn = _connect(db_path)
    inputs = load_inputs(conn, "PAY")
    conn.close()
    return {fy: round(price, 6) for fy, price in inputs.fy_end_prices.items()}


@pytest.mark.parametrize("served_on", ["2023-03-01", None])
def test_dividends_restated_for_a_split_after_the_bars_go_onto_their_basis(tmp_path, served_on):
    # BUG-104 (code review 10): a later run stored a split and its dividends on the new basis while its bar request
    # failed, and the rebuild took the post-split amounts against the pre-split closes: each dividend counted at half
    # its size, and KO's FY-end prices came out 12.8% to 0.7% low. A payment without a served day is on every stored
    # split's basis.
    assert _payer_after_a_split(tmp_path, "2023-02-01", served_on, halve=True) == {2021: 100.0, 2022: 100.0}


def test_dividends_served_before_a_split_go_onto_the_basis_of_bars_served_after_it(tmp_path):
    # The other way: the bars Yahoo served after a split are restated for it, the payments from before it aren't.
    assert _payer_after_a_split(tmp_path, "2022-12-01", "2022-11-01", bars_too=True) == {2021: 50.0, 2022: 50.0}


def test_dividends_on_the_bars_basis_stay_as_they_are(tmp_path):
    # Served with the bars, before a later split: nothing to move.
    assert _payer_after_a_split(tmp_path, "2023-02-01", "2022-12-30") == {2021: 100.0, 2022: 100.0}


def test_an_ads_ratio_is_read_against_every_class(tmp_path):
    # Code review 7 (I2): Yahoo counts one class of several (GOOGL's class A, 5.867B of 12.23B), so a two-class 20-F
    # filer read as an ADS of 2 and its Graham and Lynch values doubled. Market cap / price counts every class.
    conn = _connect(_foreign_issuer_db(tmp_path, "20-F", 12.23e9, 5.867e9, 345.40, market_count=12.228e9))
    inputs = load_inputs(conn, "ADR")
    conn.close()
    assert (inputs.ads_ratio, inputs.ads_unreadable, inputs.fy_records[-1].eps_diluted) == (1.0, None, 1.70)


def test_the_period_end_count_reads_an_ads_ratio_a_drifted_average_misses(tmp_path):
    # The year's weighted average lags today's count by half a year of buybacks; the year-end count by less.
    conn = _connect(_foreign_issuer_db(tmp_path, "20-F", 27.5e9, 5.186e9, 421.70, period_end_shares=25.93e9))
    inputs = load_inputs(conn, "ADR")
    conn.close()
    assert inputs.ads_ratio == 5.0


def test_an_unreadable_ads_ratio_leaves_the_per_share_models_unvalued(tmp_path):
    # Code review 7 (I1): with today's count 8% off the filed one, a 5-share ADS read as none, and Graham, Lynch and DCF
    # came out per ordinary share beside a per-ADS price. 25.93B / 5.60B is 4.63: no whole number, so they say why.
    from src.valuation.engine import run_valuations

    conn = _connect(_foreign_issuer_db(tmp_path, "20-F", 25.93e9, 5.186e9 * 1.08, 421.70))
    inputs = load_inputs(conn, "ADR")
    conn.close()
    assert inputs.ads_unreadable == ("the ADS ratio can't be read: the latest 20-F counts 4.63 shares for each one "
                                     "the market counts")
    reasons = {r.model: r.na_reason for r in run_valuations(inputs)}
    assert {m: reasons[m] for m in ("dcf", "graham", "lynch", "owner_earnings")} == dict.fromkeys(
        ("dcf", "graham", "lynch", "owner_earnings"), inputs.ads_unreadable)
    # Yahoo's dividends are per ADS, and a multiple's per-share basis cancels: those two say their own reasons
    assert inputs.ads_unreadable not in (reasons["ddm"], reasons["multiples"])


@pytest.mark.parametrize("filed_to_market, ratio", [
    (5.0, 5.0), (5.2, 5.0), (2.08, 2.0),   # an ADS of n shares, a little drift
    (1.07, 1.0), (0.82, 1.0),               # a 1:1 listing, its count drifted by buybacks or issuance
    (0.5, 0.5),                             # two ADSs to the share
    (4.63, None), (8 * 1.07, None),         # no whole number: a drifted 8-share ADS isn't read as 9
])
def test_an_ads_ratio_is_a_whole_number_or_unreadable(filed_to_market, ratio):
    from src.valuation.inputs import _ads_ratio

    assert _ads_ratio(filed_to_market) == ratio

