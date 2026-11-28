"""A derived quarter's EPS: its own 3-month figure where a filing tags one that the year-to-date difference rounds to
(BUG-335), else the difference."""

import pytest

from src.parsers.ttm import compute_ttm
from src.parsers.xbrl_parser import XBRLParser


def _eps(val, start, end, fy, fp, form="10-Q", filed=None):
    return {"val": val, "start": start, "end": end, "fy": fy, "fp": fp, "form": form, "filed": filed or end,
            "accn": "a-" + (filed or end)}


def _quarterly(entries, tag="EarningsPerShareDiluted", income=None):
    gaap = {tag: {"units": {"USD/shares": entries}}}
    if income is not None:
        gaap["NetIncomeLoss"] = {"units": {"USD": income}}
    return XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": gaap}})


def _c_2026(tag="EarningsPerShareDiluted"):
    # Citigroup's 10-Qs of 2026: year to date 3.06 / 6.21 / 9.07, and each quarter's own 3.15 and 2.85.
    s = "2026-01-01"
    return _quarterly([
        _eps(3.06, s, "2026-03-31", 2026, "Q1", filed="2026-05-07"),
        _eps(6.21, s, "2026-06-30", 2026, "Q2", filed="2026-08-06"),
        _eps(3.15, "2026-04-01", "2026-06-30", 2026, "Q2", filed="2026-08-06"),
        _eps(9.07, s, "2026-09-30", 2026, "Q3", filed="2026-11-05"),
        _eps(2.85, "2026-07-01", "2026-09-30", 2026, "Q3", filed="2026-11-05")], tag=tag)


@pytest.mark.parametrize("tag, key", [("EarningsPerShareDiluted", "eps_diluted"),
                                      ("EarningsPerShareBasic", "eps_basic")])
def test_a_quarters_own_3_month_eps_is_used_where_filed(tag, key):
    # C Q3 2026: nine months less six of rounded figures read 2.86; the 10-Q tags 2.85.
    q = _c_2026(tag)
    assert [q[end][key] for end in ("2026-03-31", "2026-06-30", "2026-09-30")] == [3.06, 3.15, 2.85]


def test_an_own_figure_carries_no_float_noise():
    # C Q3 2025: 5.78 - 3.92 read 1.8600000000000003.
    s = "2025-01-01"
    q = _quarterly([_eps(1.96, s, "2025-03-31", 2025, "Q1"), _eps(3.92, s, "2025-06-30", 2025, "Q2"),
                    _eps(5.78, s, "2025-09-30", 2025, "Q3"), _eps(1.86, "2025-07-01", "2025-09-30", 2025, "Q3")])
    assert q["2025-09-30"]["eps_diluted"] == 1.86


def test_a_fourth_quarter_no_filing_tags_is_still_the_year_less_nine_months():
    s = "2025-01-01"
    q = _quarterly([_eps(1.96, s, "2025-03-31", 2025, "Q1"), _eps(3.92, s, "2025-06-30", 2025, "Q2"),
                    _eps(5.78, s, "2025-09-30", 2025, "Q3"), _eps(1.86, "2025-07-01", "2025-09-30", 2025, "Q3"),
                    _eps(6.99, s, "2025-12-31", 2025, "FY", form="10-K", filed="2026-02-20")])
    assert q["2025-12-31"]["eps_diluted"] == pytest.approx(1.21)


def test_a_loss_quarters_own_eps_is_used_too():
    s = "2025-01-01"
    q = _quarterly([_eps(0.50, s, "2025-03-31", 2025, "Q1"), _eps(0.39, s, "2025-06-30", 2025, "Q2"),
                    _eps(-0.10, "2025-04-01", "2025-06-30", 2025, "Q2")])
    assert q["2025-06-30"]["eps_diluted"] == -0.10


def test_a_zero_own_eps_is_used():
    s = "2025-01-01"
    q = _quarterly([_eps(0.50, s, "2025-03-31", 2025, "Q1"), _eps(0.51, s, "2025-06-30", 2025, "Q2"),
                    _eps(0.0, "2025-04-01", "2025-06-30", 2025, "Q2")])
    assert q["2025-06-30"]["eps_diluted"] == 0.0


@pytest.mark.parametrize("own, expected", [(1.875, 1.875), (1.845, 1.845), (1.876, 1.86), (1.844, 1.86),
                                           (2110000.0, 1.86)])
def test_an_own_figure_is_read_only_within_the_rounding_of_the_figures_differenced(own, expected):
    # Two year-to-date figures rounded to the cent are a cent and a half from the quarter's own at most; an own figure
    # farther off is another figure (ICE's 2013 quarters tag 2,110,000, mis-scaled), and the difference stays.
    s = "2025-01-01"
    q = _quarterly([_eps(1.96, s, "2025-03-31", 2025, "Q1"), _eps(3.92, s, "2025-06-30", 2025, "Q2"),
                    _eps(5.78, s, "2025-09-30", 2025, "Q3"), _eps(own, "2025-07-01", "2025-09-30", 2025, "Q3")])
    assert q["2025-09-30"]["eps_diluted"] == pytest.approx(expected, abs=1e-12)


def test_the_trailing_year_sums_the_quarters_own_eps():
    # Q4 2025 1.21 + 3.06 + 3.15 + 2.85 (HEAD: 1.21 + 3.06 + 3.15 + 2.86).
    s, n = "2025-01-01", "2026-01-01"
    q = _quarterly([
        _eps(1.96, s, "2025-03-31", 2025, "Q1"), _eps(3.92, s, "2025-06-30", 2025, "Q2"),
        _eps(5.78, s, "2025-09-30", 2025, "Q3"), _eps(6.99, s, "2025-12-31", 2025, "FY", form="10-K"),
        _eps(3.06, n, "2026-03-31", 2026, "Q1"), _eps(6.21, n, "2026-06-30", 2026, "Q2"),
        _eps(3.15, "2026-04-01", "2026-06-30", 2026, "Q2"), _eps(9.07, n, "2026-09-30", 2026, "Q3"),
        _eps(2.85, "2026-07-01", "2026-09-30", 2026, "Q3")])
    assert compute_ttm(q)["2026-09-30"]["eps_diluted"] == pytest.approx(10.27, abs=1e-9)


def test_an_own_figure_fills_no_quarter_the_ladder_does_not_reach():
    # Q2's three months with no half year: no difference to round, so no EPS for Q2 (and no quarter of its own).
    s = "2025-01-01"
    income = [_eps(100.0, s, "2025-03-31", 2025, "Q1"), _eps(210.0, s, "2025-06-30", 2025, "Q2")]
    q = _quarterly([_eps(0.50, s, "2025-03-31", 2025, "Q1"), _eps(0.55, "2025-04-01", "2025-06-30", 2025, "Q2")],
                   income=income)
    assert "eps_diluted" not in q["2025-06-30"]
    q = _quarterly([_eps(0.50, s, "2025-03-31", 2025, "Q1"), _eps(0.55, "2025-07-01", "2025-09-30", 2025, "Q3")],
                   income=income)
    assert "2025-09-30" not in q


def test_dividends_per_share_keep_the_year_to_date_difference():
    # Only EPS reads a quarter's own figure here (dividends per share are another item's).
    s = "2025-01-01"
    q = _quarterly([_eps(0.50, s, "2025-03-31", 2025, "Q1"), _eps(1.10, s, "2025-06-30", 2025, "Q2"),
                    _eps(0.59, "2025-04-01", "2025-06-30", 2025, "Q2")], tag="CommonStockDividendsPerShareDeclared")
    assert q["2025-06-30"]["dividends_per_share"] == pytest.approx(0.60)


def test_the_own_figure_is_the_quarters_three_months_whatever_the_facts_order():
    # The year to date ends on the quarter's end too: only the 3-month span is the quarter's own figure.
    s = "2025-01-01"
    q = _quarterly([_eps(1.86, "2025-07-01", "2025-09-30", 2025, "Q3"), _eps(5.78, s, "2025-09-30", 2025, "Q3"),
                    _eps(1.96, s, "2025-03-31", 2025, "Q1"), _eps(3.92, s, "2025-06-30", 2025, "Q2")])
    assert q["2025-09-30"]["eps_diluted"] == 1.86
