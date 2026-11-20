"""The valuation oracle's unit-seam rule (BUG-308): ``qa/oracle/valuation_oracle.py`` drops the years before a
share-count seam beyond 50x, as ``inputs._normalize_splits`` documents it (the spec is silent), so its DCF growth
window starts where the code's does. And its DDM one-payment gate counts the regular payments beside a year only
(BUG-366), as ``models.dividend_history`` does.
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


# ---- BUG-366: the oracle's one-payment gate counts regular payments only, as dividend_history does ----

def _annual(extra=()):
    """An annual payer's ex-dates (each 15 January, 2.95 in 2024, 3.10 in 2025), its 2026 payment slipped to
    2025-12-30, and *extra* payments."""
    return sorted([("2023-01-15", 2.81), ("2024-01-15", 2.95), ("2025-01-15", 3.10), ("2025-12-30", 3.26),
                   *extra])


@pytest.mark.parametrize("extra, counted", [
    ((), 1),
    ((("2024-07-15", 0.5),), 1),    # under half the median of 2024's payments (1.725)
    ((("2024-07-15", 1.2),), 2),    # half of it or more: a payment of a payer of two a year
    ((("2024-07-15", 10.0),), 1),   # a one-off whole (over 3x the payments either side)
    ((("2024-07-15", 10.0), ("2024-09-15", 0.5)), 1),   # the one-off isn't measured against either
])
def test_the_oracles_gate_counts_the_regular_payments_beside_a_year(extra, counted):
    events = _annual(extra)
    assert oracle.regular_beside(events, oracle.one_offs(events), "2024-12-31", "2025-12-31") == counted


def test_the_oracles_gate_counts_a_semiannual_interim_and_quarters_beside_specials():
    semiannual = [(f"{y}-{m}", a) for y in (2023, 2024, 2025) for m, a in (("03-15", 0.4), ("09-15", 1.0))]
    assert oracle.regular_beside(semiannual, oracle.one_offs(semiannual), "2024-12-31", "2025-12-31") == 2
    tpl = [("2019-03-07", 0.666667), ("2020-03-06", 1.777778), ("2020-12-10", 1.111111), ("2021-03-05", 0.305556),
           ("2021-06-07", 0.305556), ("2021-09-07", 0.305556), ("2021-12-07", 0.305556)]
    assert oracle.regular_beside(tpl, oracle.one_offs(tpl), "2019-12-31", "2020-12-31") == 4


def _dividend_db(extra):
    """AAA, an annual payer growing 5% (SEC's dividends per share each FY), its 2026 payment ex-dated 2025-12-30, and
    an *extra* payment on 2024-07-15."""
    db = sqlite3.connect(":memory:")
    db.executescript(
        "create table market_snapshots (ticker, beta, risk_free_rate, shares_outstanding, current_price, market_cap,"
        " collected_at);"
        "create table analyst_snapshots (ticker, earnings_growth, collected_at);"
        "create table split_events (ticker, date, ratio);"
        "create table financials_annual (ticker, fiscal_year, period_end, net_income, total_equity,"
        " shares_outstanding, weighted_avg_shares_diluted, eps_diluted, filed_date, form, dividends_per_share);"
        "create table dividend_events (ticker, date, amount);"
        "create table price_bars (ticker, date, close);")
    db.execute("insert into market_snapshots values ('AAA', 1.0, 0.04, 100, 100.0, 1e4, '2026-08-04T00:00:00')")
    total = {y: 2.0 * 1.05 ** (y - 2016) for y in range(2015, 2027)}
    for fy in range(2016, 2026):
        db.execute("insert into financials_annual values ('AAA', ?, ?, 1e3, 1e4, 100, 100, 10, ?, '10-K', ?)",
                   (fy, f"{fy}-12-31", f"{fy + 1}-02-20", total[fy]))
    for day, amount in [(f"{y}-01-15", total[y]) for y in range(2015, 2026)] + [("2025-12-30", total[2026]),
                                                                                ("2024-07-15", extra)]:
        db.execute("insert into dividend_events values ('AAA', ?, ?)", (day, amount))
    db.execute("insert into price_bars values ('AAA', '2026-08-03', 100.0)")
    return db


def test_the_oracles_ddm_keeps_an_annual_payers_slipped_year_beside_an_extra_payment():
    # BUG-366 through level 2: the extra no longer turns FY2025's SEC figure into Yahoo's two payments (13.7%).
    o = oracle.level2(_dividend_db(0.5), "AAA", "ddm", {"ttm_anchor": "2026-08-04"})
    assert o["inputs"]["hist_cagr"] == pytest.approx(0.05)
