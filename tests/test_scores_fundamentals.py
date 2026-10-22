"""Quality scores (Milestone 2): each fiscal year's figures, the per-share series on today's split basis, and their
compound growth rates. docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.scores.fundamentals import _count_on_eps_basis, cagr, growth, load_fundamentals, per_share_rows, read_debt
from src.valuation.inputs import load_inputs

YEARS = list(range(2013, 2026))  # 13 fiscal years


def _db(tmp_path, sector="general"):
    """Revenue grows 10% a year from 1,000; 100M diluted shares as filed until FY2019, then 200M after a 2:1 split
    of 2020-06-01, which the filings from FY2020 on reflect (and only from FY2020: older years stay as filed)."""
    store = SQLiteStore(db_path=tmp_path / "scores.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('AAA', ?)", (sector,))
    for i, fy in enumerate(YEARS):
        post = fy >= 2020
        shares = 200e6 if post else 100e6
        revenue = 1000e6 * 1.1 ** i
        net_income = revenue * 0.1
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, revenue, net_income, total_equity, "
            "eps_diluted, weighted_avg_shares_diluted, shares_outstanding, total_assets) "
            "VALUES ('AAA', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", revenue, net_income, 5 * net_income, net_income / shares, shares, shares,
             3 * revenue))
        conn.execute("INSERT INTO metrics_annual (ticker, fiscal_year, ebitda, free_cash_flow) VALUES ('AAA', ?, ?, ?)",
                     (fy, 0.2 * revenue, 0.08 * revenue))
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('AAA', '2020-06-01', 2.0)")
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_a_cagr_compounds_over_exactly_its_years():
    series = [(2015 + i, 100.0 * 1.1 ** i) for i in range(6)]
    assert cagr(series, 5) == pytest.approx(0.10)
    assert cagr(series, 3) == pytest.approx(0.10)
    assert cagr(series, 10) is None  # no year ten years back
    assert cagr([(2015, -1.0), (2020, 5.0)], 5) is None  # a negative start has no CAGR
    assert cagr([(2015, 100.0), (2020, 0.0)], 5) is None
    assert cagr([(2015, 100.0), (2020, None)], 5) is None
    # The start must be exactly five years before the latest: a gap year doesn't stand in for it.
    assert cagr([(2014, 100.0), (2016, 120.0), (2020, 160.0)], 5) is None


def test_the_valuations_keep_ten_years_and_the_scores_take_eleven(tmp_path):
    conn = _db(tmp_path)
    assert len(load_inputs(conn, "AAA").fy_records) == 10
    assert [r.fiscal_year for r in load_inputs(conn, "AAA", max_years=11).fy_records] == YEARS[-11:]


def test_per_share_figures_are_on_todays_split_basis(tmp_path):
    f = load_fundamentals(_db(tmp_path), "AAA")
    assert [r.fiscal_year for r in f.rows] == YEARS[-11:]
    by_year = {r.fiscal_year: r for r in f.rows}
    # FY2019's 100M shares as filed are 200M on today's basis: revenue / share doesn't jump at the split.
    assert by_year[2019].shares == pytest.approx(200e6)
    assert by_year[2019].per_share("revenue") == pytest.approx(1000e6 * 1.1 ** 6 / 200e6)
    assert by_year[2020].per_share("revenue") == pytest.approx(1000e6 * 1.1 ** 7 / 200e6)
    assert by_year[2019].eps == pytest.approx(1000e6 * 1.1 ** 6 * 0.1 / 200e6)


def test_years_with_the_private_count_have_no_per_share_figures(tmp_path):
    """BUG-188: the jump into the listing's count folds for the valuations, but a merger around the listing jumps
    the same way (DELL's EMC), so the years that ended before the first bar and that only that jump folds have no
    per-share figures in the scores; their totals stay. The listing's own year takes the public count."""
    conn = _db(tmp_path)
    # No split: the 2020 doubling is the listing's (the first bar in June 2019), across a loss, so no split's marks.
    conn.execute("DELETE FROM split_events")
    conn.execute("UPDATE financials_annual SET net_income = -net_income, eps_diluted = -eps_diluted "
                 "WHERE fiscal_year = 2019")
    conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', '2019-06-03', 20.0)")
    conn.commit()
    f = load_fundamentals(conn, "AAA")
    by_year = {r.fiscal_year: r for r in f.rows}
    assert by_year[2018].shares is None and by_year[2018].eps is None and by_year[2015].shares is None
    assert by_year[2018].per_share("revenue") is None and by_year[2018].figures["revenue"] is not None
    assert by_year[2019].shares == pytest.approx(200e6) and by_year[2019].eps is not None  # the listing's year
    g = growth(f)
    assert g["rev_ps_cagr_5y"] == pytest.approx(0.10)  # FY2020 -> FY2025, after the jump
    assert g["rev_ps_cagr_10y"] is None


def test_growth_gives_the_cagrs_of_each_series(tmp_path):
    g = growth(load_fundamentals(_db(tmp_path), "AAA"))
    for key in ("rev_ps_cagr_3y", "rev_ps_cagr_5y", "rev_ps_cagr_10y", "eps_cagr_5y", "ebitda_ps_cagr_10y",
                "fcf_ps_cagr_5y", "book_ps_cagr_5y"):
        assert g[key] == pytest.approx(0.10), key  # the same 10% a year, the split notwithstanding


def test_the_per_share_rows_for_storage(tmp_path):
    rows = per_share_rows(load_fundamentals(_db(tmp_path), "AAA"))
    assert len(rows) == 11
    latest = rows[-1]
    assert latest["fiscal_year"] == 2025
    assert latest["revenue_ps"] == pytest.approx(1000e6 * 1.1 ** 12 / 200e6)
    assert latest["ebitda_ps"] == pytest.approx(0.2 * latest["revenue_ps"])
    assert latest["fcf_ps"] == pytest.approx(0.08 * latest["revenue_ps"])
    assert latest["book_ps"] == pytest.approx(0.5 * latest["revenue_ps"])


def test_a_bank_has_no_fcf_or_ebitda_per_share(tmp_path):
    conn = _db(tmp_path, sector="bank")
    conn.execute("UPDATE metrics_annual SET ebitda = NULL, free_cash_flow = NULL")  # as the sector rules store them
    f = load_fundamentals(conn, "AAA")
    assert f.sector_class == "bank"
    assert per_share_rows(f)[-1]["ebitda_ps"] is None
    assert growth(f)["fcf_ps_cagr_5y"] is None


def test_an_unknown_ticker_has_no_rows(tmp_path):
    f = load_fundamentals(_db(tmp_path), "ZZZ")
    assert f.rows == [] and growth(f)["rev_ps_cagr_5y"] is None


def test_a_year_without_weighted_shares_counts_on_its_eps_basis(tmp_path):
    # GOOGL-like: no weighted diluted count before FY2022; the cover's shares outstanding stay as filed (pre-split)
    # while FY2021's EPS was restated for the 20:1 split of 2022-07-15. The year's count must be on its EPS's
    # basis (net income / EPS), or its per-share figures read 20x high.
    store = SQLiteStore(db_path=tmp_path / "g.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('GGG', 'general')")
    for i, fy in enumerate(range(2017, 2026)):
        revenue, net_income = 10000e6 * 1.1 ** i, 1000e6 * 1.1 ** i  # EPS of $0.50 and up, past cents' rounding
        restated = fy >= 2021  # EPS on the post-split basis from FY2021 (restated by the FY2022 10-K)
        eps = net_income / (2000e6 if restated else 100e6)
        weighted = 2000e6 if fy >= 2022 else None
        outstanding = 2000e6 if fy >= 2022 else 100e6  # the cover page: as filed
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, revenue, net_income, total_equity, "
            "eps_diluted, weighted_avg_shares_diluted, shares_outstanding) VALUES ('GGG', ?, ?, ?, ?, ?, ?, ?, ?)",
            (fy, f"{fy}-12-31", revenue, net_income, 5 * net_income, eps, weighted, outstanding))
        conn.execute("INSERT INTO metrics_annual (ticker, fiscal_year, ebitda) VALUES ('GGG', ?, ?)",
                     (fy, 0.2 * revenue))
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('GGG', '2022-07-15', 20.0)")
    conn.commit()
    conn.row_factory = sqlite3.Row
    f = load_fundamentals(conn, "GGG")
    by_year = {r.fiscal_year: r for r in f.rows}
    assert by_year[2021].shares == pytest.approx(2000e6)  # not the cover's 100M as filed
    assert by_year[2020].shares == pytest.approx(2000e6)
    assert growth(f)["rev_ps_cagr_5y"] == pytest.approx(0.10)


def test_a_count_from_eps_of_cents_falls_back_to_the_filed_count():
    # EPS to the cent: $5M over $0.01 could be anything from 333M to 1B shares.
    assert _count_on_eps_basis(100e6, 0.01, {"net_income": 5e6}) == 100e6
    assert _count_on_eps_basis(100e6, 0.5, {"net_income": 60e6}) == pytest.approx(120e6)
    assert _count_on_eps_basis(100e6, 0.5, {"net_income": 60e6, "weighted_avg_shares_diluted": 99e6}) == 100e6


def _no_debt(**overrides):
    """A year with no debt tagged and no interest expense (ISRG's shape)."""
    figures = dict(total_debt=None, net_debt=None, roic=None, long_term_debt=None, short_term_debt=None,
                   commercial_paper=None, interest_expense=None, cash_and_equivalents=300.0,
                   short_term_investments=200.0, total_equity=1300.0, operating_income=250.0, pretax_income=260.0,
                   income_tax_expense=52.0)
    figures.update(overrides)
    return figures


def test_no_debt_tagged_and_no_interest_reads_as_zero_debt():
    # BUG-190: ISRG, ANET, PANW, PLTR and VRTX tag no debt and report no interest, so metrics_annual has no total
    # debt, net debt or ROIC for them.
    figures = _no_debt()
    assert read_debt(figures, "general") is False
    assert figures["total_debt"] == 0.0 and figures["net_debt"] == -500.0
    # ROIC as metrics_annual computes it: NOPAT at the effective 20% tax, over equity + 0 debt - cash.
    assert figures["roic"] == pytest.approx(250.0 * 0.8 / (1300.0 - 300.0))


def test_zeros_filed_as_such_stay_zero():
    # SNDK files its long-term and short-term debt as 0 and reports no interest.
    figures = _no_debt(total_debt=0.0, net_debt=-500.0, roic=0.2, long_term_debt=0.0, short_term_debt=0.0)
    assert read_debt(figures, "general") is False
    assert figures["total_debt"] == 0.0 and figures["net_debt"] == -500.0 and figures["roic"] == 0.2


def test_interest_paid_with_no_debt_tagged_leaves_the_debt_unknown():
    # BUG-189: PGR's short-term debt is 0 and its long-term debt untagged (DebtLongtermAndShorttermCombinedAmount,
    # $8.39B, isn't mapped), yet it paid $278M of interest: the 0 is a phantom.
    figures = _no_debt(total_debt=0.0, net_debt=-125.0, roic=0.9, short_term_debt=0.0, interest_expense=278.0)
    assert read_debt(figures, "insurance") is True
    assert figures["total_debt"] is None and figures["net_debt"] is None and figures["roic"] is None


def test_tagged_debt_stays_as_filed():
    figures = _no_debt(total_debt=500.0, net_debt=0.0, roic=0.12, long_term_debt=500.0)
    before = dict(figures)
    assert read_debt(figures, "general") is False and figures == before


def test_a_bank_or_an_insurer_without_debt_tags_keeps_its_debt_unknown():
    # KKR (a bank here) tags neither its debt nor its interest; a financial's debt has no zero to read.
    for sector in ("bank", "insurance"):
        figures = _no_debt()
        before = dict(figures)
        assert read_debt(figures, sector) is False and figures == before


def test_each_year_reads_its_own_debt(tmp_path):
    conn = _db(tmp_path)  # AAA tags no debt and no interest
    conn.execute("UPDATE financials_annual SET interest_expense = 5e6 WHERE fiscal_year = 2024")
    conn.commit()
    by_year = {r.fiscal_year: r for r in load_fundamentals(conn, "AAA").rows}
    assert by_year[2025].figures["total_debt"] == 0.0 and not by_year[2025].debt_unknown
    assert by_year[2024].figures["total_debt"] is None and by_year[2024].debt_unknown
