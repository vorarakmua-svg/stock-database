"""The valuation oracle's unit-seam rule (BUG-308): ``qa/oracle/valuation_oracle.py`` drops the years before a
share-count seam beyond 50x, as ``inputs._normalize_splits`` documents it (the spec is silent), so its DCF growth
window starts where the code's does.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("qa_valuation_oracle", ROOT / "qa" / "oracle" / "valuation_oracle.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


oracle = _load()


def _row(fy, diluted=None, net_income=None, eps=None, period_end_count=None):
    """A financials_annual row as the oracle reads it: fy, period_end, net income, equity, period-end count,
    diluted weighted count, diluted EPS."""
    return (fy, f"{fy}-12-31", net_income, None, period_end_count, diluted, eps)


def test_a_seam_beyond_50x_is_a_unit_break_and_one_within_it_is_not():
    assert oracle.unit_seam([_row(2016, 900e6), _row(2017, 906e6), _row(2018, 4.5e6)]) == 2017  # 1:201
    assert oracle.unit_seam([_row(2018, 40_586.0), _row(2019, 40_420.0), _row(2020, 40e6)]) == 2019  # thousands
    assert oracle.unit_seam([_row(2017, 100e6), _row(2018, 100e6 / 49)]) is None  # a 1:49 reverse split folds
    assert oracle.unit_seam([_row(2017, 100e6), _row(2018, 100e6 * 49)]) is None
    assert oracle.unit_seam([_row(2017, 100e6), _row(2018, 100e6 * 51)]) == 2017
    assert oracle.unit_seam([]) is None


def test_the_newest_seam_cuts_and_a_year_without_a_count_is_not_bridged():
    rows = [_row(2014, 1e9), _row(2015, 1e6), _row(2016, 1e6), _row(2017, 1e9)]
    assert oracle.unit_seam(rows) == 2016
    assert oracle.unit_seam([_row(2016, 1e9), _row(2017), _row(2018, 1e6)]) is None


def test_the_count_is_the_one_eps_divides_by():
    # No diluted count: net income / EPS, else the period-end count.
    assert oracle.unit_seam([_row(2017, net_income=500e6, eps=0.5), _row(2018, 5e6)]) == 2017  # 1e9 -> 5e6
    assert oracle.unit_seam([_row(2017, period_end_count=1e9), _row(2018, 5e6)]) == 2017


def _db(fcf, counts):
    db = sqlite3.connect(":memory:")
    db.executescript(
        "create table market_snapshots (ticker, beta, risk_free_rate, shares_outstanding, current_price, market_cap,"
        " collected_at);"
        "create table analyst_snapshots (ticker, earnings_growth, collected_at);"
        "create table split_events (ticker, date, ratio);"
        "create table financials_annual (ticker, fiscal_year, period_end, net_income, total_equity,"
        " shares_outstanding, weighted_avg_shares_diluted, eps_diluted, filed_date, form);"
        "create table metrics_annual (ticker, fiscal_year, free_cash_flow);")
    db.execute("insert into market_snapshots values ('EXE', 1.0, 0.04, 240e6, 100.0, 24e9, '2026-10-20T00:00:00')")
    for fy, count in counts.items():
        db.execute("insert into financials_annual values ('EXE', ?, ?, 1e9, 1e10, null, ?, ?, ?, '10-K')",
                   (fy, f"{fy}-12-31", count, 1e9 / count, f"{fy + 1}-02-20"))
    for fy, value in fcf.items():
        db.execute("insert into metrics_annual values ('EXE', ?, ?)", (fy, value))
    return db


def test_the_dcf_growth_window_starts_after_the_seam():
    # EXE: FY2016's loss, FY2017's 475M, then a 1:200 seam into FY2018; the code's FCF history starts at FY2018.
    fcf = {2016: -1650e6, 2017: 475e6, 2018: 1730e6, 2019: 1623e6, 2020: 22e6, 2022: 2302e6, 2023: 551e6,
           2024: 8e6, 2025: 1839e6}
    counts = {2016: 764e6, 2017: 906e6, 2018: 4.546e6, 2019: 8.3e6, 2020: 9.8e6, 2022: 146e6, 2023: 143e6,
              2024: 157e6, 2025: 240e6}
    db = _db(fcf, counts)
    assert [row[0] for row in oracle.Inputs(db, "EXE").annual][0] == 2018  # every model's years, as the code's
    o = oracle.level2(db, "EXE", "dcf", {})
    assert o["inputs"]["hist_cagr"] == pytest.approx((1839 / 1730) ** (1 / 7) - 1)
    assert o["inputs"]["cagr_years"] == 7
    assert o["inputs"]["fcf_basis"] == pytest.approx(551e6)  # the median of FY2023-25
    # Without the seam the window reaches back to FY2017's 475M (from its first positive year).
    counts_smooth = {**counts, 2016: 4.5e6, 2017: 4.5e6}
    o = oracle.level2(_db(fcf, counts_smooth), "EXE", "dcf", {})
    assert o["inputs"]["hist_cagr"] == pytest.approx((1839 / 475) ** (1 / 8) - 1)


def test_only_the_years_a_valuation_reads_are_searched_for_a_seam():
    # The valuation reads the latest 10 fiscal years: a unit seam older than them (FY2013-14) cuts nothing.
    counts = {fy: (1e6 if fy <= 2013 else 1e9) for fy in range(2012, 2026)}
    assert [row[0] for row in oracle.Inputs(_db({}, counts), "EXE").annual][0] == 2012
    counts[2016], counts[2017] = 1e6, 1e6  # one inside them (FY2017-18) cuts there
    assert [row[0] for row in oracle.Inputs(_db({}, counts), "EXE").annual][0] == 2018
