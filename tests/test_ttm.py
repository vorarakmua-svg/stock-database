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

