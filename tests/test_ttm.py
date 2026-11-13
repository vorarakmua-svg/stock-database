"""Tests for the trailing-twelve-month (TTM) series."""

import pytest

from src.parsers.ttm import compute_ttm


def _quarters():
    # Five consecutive ~quarterly periods with a flow (net_income) and an instant
    # balance-sheet value (total_assets).
    ni = {"2024-03-31": 10, "2024-06-30": 20, "2024-09-30": 30,
          "2024-12-31": 40, "2025-03-31": 50}
    ta = {"2024-03-31": 100, "2024-06-30": 110, "2024-09-30": 120,
          "2024-12-31": 130, "2025-03-31": 140}
    return {e: {"period_end": e, "net_income": ni[e], "total_assets": ta[e],
                "calendar_year": int(e[:4]), "calendar_quarter": (int(e[5:7]) - 1) // 3 + 1}
            for e in ni}


def test_ttm_sums_trailing_four_quarters():
    ttm = compute_ttm(_quarters())
    # Only quarters with 4 consecutive predecessors get a TTM row.
    assert "2024-12-31" in ttm and "2025-03-31" in ttm
    assert "2024-09-30" not in ttm  # only 3 quarters available
    assert ttm["2024-12-31"]["net_income"] == 10 + 20 + 30 + 40
    assert ttm["2025-03-31"]["net_income"] == 20 + 30 + 40 + 50


def test_ttm_balance_sheet_is_as_of_latest():
    ttm = compute_ttm(_quarters())
    # Instant concept = the latest quarter-end value, not a sum.
    assert ttm["2025-03-31"]["total_assets"] == 140


def test_ttm_skips_when_gap_breaks_continuity():
    q = _quarters()
    # Remove a middle quarter -> no 4-consecutive window spanning the gap.
    del q["2024-09-30"]
    ttm = compute_ttm(q)
    assert "2024-12-31" not in ttm  # window would skip a quarter
    assert "2025-03-31" not in ttm  # the one window of four left spans the six-month gap


def test_ttm_empty():
    assert compute_ttm({}) == {}


def test_ttm_share_counts_are_the_four_quarters_average():
    # BUG-074: a weighted-average share count isn't additive; summed, AAPL's TTM count was a telescoped near-miss.
    q = _quarters()
    for end, count in zip(sorted(q), (4.0e9, 4.1e9, 4.2e9, 4.3e9, 4.4e9)):
        q[end]["weighted_avg_shares_diluted"] = count
    ttm = compute_ttm(q)
    assert ttm["2024-12-31"]["weighted_avg_shares_diluted"] == 4.15e9
    assert ttm["2024-12-31"]["net_income"] == 10 + 20 + 30 + 40


def test_a_trailing_year_across_a_split_is_on_its_latest_quarters_basis():
    # BUG-082: each quarter's per-share figures and counts sit on the basis its own year is filed on, so a trailing year
    # that reaches back past a 20:1 split's restatement put three old-basis quarters beside one new-basis quarter.
    q = _quarters()
    for end, eps, count in zip(sorted(q), (5.0, 6.0, 6.0, 6.0, 0.3), (500e6, 500e6, 500e6, 500e6, 10.1e9)):
        q[end].update(eps_diluted=eps, weighted_avg_shares_diluted=count)
    levels = {end: {"eps_diluted": 1.0, "weighted_avg_shares_diluted": 1.0} for end in q}
    levels["2025-03-31"] = {"eps_diluted": 20.0, "weighted_avg_shares_diluted": 20.0}
    ttm = compute_ttm(q, levels)
    assert ttm["2025-03-31"]["eps_diluted"] == pytest.approx(3 * 6.0 / 20 + 0.3)
    assert ttm["2025-03-31"]["weighted_avg_shares_diluted"] == pytest.approx((3 * 500e6 * 20 + 10.1e9) / 4)
    assert ttm["2025-03-31"]["net_income"] == 20 + 30 + 40 + 50  # dollars as they are
    assert ttm["2024-12-31"]["eps_diluted"] == 5.0 + 6.0 + 6.0 + 6.0  # one basis already


def test_a_trailing_year_runs_over_a_sixteen_week_quarter():
    # BUG-095: the trailing years took consecutive quarter-ends to be at most 100 days apart, and a 12-12-12-16-week
    # year steps 112 days into its fourth quarter, so COST and PEP had none.
    ends = ["2023-11-26", "2024-02-18", "2024-05-12", "2024-09-01", "2024-11-24"]
    q = {e: {"period_end": e, "net_income": ni} for e, ni in zip(ends, (100, 120, 110, 170, 105))}
    ttm = compute_ttm(q)
    assert (ttm["2024-09-01"]["net_income"], ttm["2024-11-24"]["net_income"]) == (500, 505)


def _year_2017_to_2018():
    """Six calendar quarters, 2017 Q1 to 2018 Q2, each with net income and total assets."""
    ends = ["2017-03-31", "2017-06-30", "2017-09-30", "2017-12-31", "2018-03-31", "2018-06-30"]
    return {e: {"period_end": e, "net_income": 10.0 * n, "total_assets": 100.0 + n} for n, e in enumerate(ends, 1)}


def test_an_adoption_day_balance_sheet_breaks_no_trailing_year():
    # BUG-249 (build M5-6): a 10-Q's opening balance sheet on the day a standard is adopted (AES's 2018-01-01 retained
    # earnings, ABT's 2018-01-05 long-term debt) is a period end of its own with no flow figure, and the windows of
    # four ends crossing it were never one quarter apart: 1,802 trailing years lost at 306 companies.
    q = _year_2017_to_2018()
    q["2018-01-01"] = {"period_end": "2018-01-01", "retained_earnings": 7.0}
    ttm = compute_ttm(q)
    assert ttm["2018-03-31"]["net_income"] == 20 + 30 + 40 + 50
    assert ttm["2018-06-30"]["net_income"] == 30 + 40 + 50 + 60
    assert ttm["2018-03-31"]["total_assets"] == 105.0  # the quarter's own balance sheet
    assert "2018-01-01" not in ttm


def test_a_share_count_beside_a_quarter_breaks_no_trailing_year():
    # BUG-096: ADI's FY2012 10-K re-dates fiscal Q1 2011 to end 2011-02-04, beside the 10-Q's 2011-01-29, and gives it
    # share counts only; GRMN's 2022-09-25 holds a dividend per share alone. Neither is a quarter of the year's flows,
    # and their counts stay out of the trailing year's average.
    for extra in ({"weighted_avg_shares_diluted": 9e9}, {"dividends_per_share": 0.73}):
        q = _year_2017_to_2018()
        for end in q:
            q[end]["weighted_avg_shares_diluted"] = 1e9
        q["2018-01-05"] = dict(extra, period_end="2018-01-05")
        ttm = compute_ttm(q)
        assert ttm["2018-03-31"]["net_income"] == 20 + 30 + 40 + 50
        assert ttm["2018-03-31"]["weighted_avg_shares_diluted"] == 1e9
        assert "dividends_per_share" not in ttm["2018-03-31"]
        assert "2018-01-05" not in ttm


def test_a_quarter_whose_flows_are_missing_still_ends_its_windows():
    # CB's 2010-12-31 and BNY's 2010 quarters hold a balance sheet and no flow figure, a quarter apart from their
    # neighbours: they are quarters, so the trailing years ending on them keep their balance sheets, and the years
    # across them stay without the flows they lack, as before.
    q = _year_2017_to_2018()
    q["2017-12-31"] = {"period_end": "2017-12-31", "total_assets": 104.0}
    ttm = compute_ttm(q)
    assert ttm["2017-12-31"] == {"period_end": "2017-12-31", "calendar_year": None, "calendar_quarter": None,
                                 "fiscal_year": None, "total_assets": 104.0}
    assert "net_income" not in ttm["2018-03-31"] and ttm["2018-03-31"]["total_assets"] == 105.0


def test_an_end_beside_a_gap_in_the_quarters_is_left_as_it_was():
    # Only an end between two quarters one quarter apart lies inside a quarter: in place of a missing quarter
    # (2017-09-30 here) it is that quarter's balance sheet, and the trailing years across it stay without its flows.
    q = _year_2017_to_2018()
    del q["2017-09-30"]
    q["2017-10-02"] = {"period_end": "2017-10-02", "total_assets": 103.5}
    ttm = compute_ttm(q)
    assert ttm["2017-12-31"]["total_assets"] == 104.0 and "net_income" not in ttm["2017-12-31"]
    assert "net_income" not in ttm["2018-03-31"] and "net_income" not in ttm["2018-06-30"]

