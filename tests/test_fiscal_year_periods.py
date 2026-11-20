"""A fiscal year's record holds one period's figures (BUG-341).

A fiscal-year key is the period end's year, so after a change of fiscal year (or beside a stray figure) one key can
hold two periods: GPN's 2016 held its year to 2016-05-31, the calendar 2016 a later 10-K recast and the balance sheet
at the end of its June-December transition period. The record is the period most of the key's income and cash-flow
figures cover; figures of a period ending more than a week from it are not that year's. Each shape is a real
filer's, cut down.
"""

import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


def _facts(by_tag):
    def unit(tag):
        return "USD/shares" if "PerShare" in tag else "USD"
    return {"facts": {"us-gaap": {tag: {"units": {unit(tag): entries}} for tag, entries in by_tag.items()}}}


def _annual(by_tag, fy):
    return XBRLParser().extract_annual_financials(_facts(by_tag))[fy]


def _vintage(by_tag, fy, accn):
    return XBRLParser().extract_annual_vintages(_facts(by_tag))[fy][accn]


MAY_2016, CAL_2016, CAL_2017 = ("2015-06-01", "2016-05-31"), ("2016-01-01", "2016-12-31"), ("2017-01-01", "2017-12-31")
GPN_10K_2016, GPN_10K_2017 = "2016-07-28", "2018-02-23"


def _gpn():
    # GPN moved from May years to calendar years in 2016 (transition period June-December 2016, on a 10-KT). Its 10-K
    # for the year to 2016-05-31 filed that year; its 10-K for 2017 adds the calendar 2016 income statement (recast)
    # and the balance sheet at 2016-12-31, beside the May year's cash flows.
    a, b = GPN_10K_2016, GPN_10K_2017
    return {
        "Revenues": [usd(2898, *MAY_2016, fy=2016, filed=a), usd(3370, *CAL_2016, fy=2017, filed=b),
                     usd(3975, *CAL_2017, fy=2017, filed=b)],
        "NetIncomeLoss": [usd(272, *MAY_2016, fy=2016, filed=a), usd(201, *CAL_2016, fy=2017, filed=b),
                          usd(469, *CAL_2017, fy=2017, filed=b)],
        "NetCashProvidedByUsedInOperatingActivities": [usd(592, *MAY_2016, fy=2016, filed=a),
                                                       usd(592, *MAY_2016, fy=2017, filed=b),
                                                       usd(950, *CAL_2017, fy=2017, filed=b)],
        "PaymentsToAcquirePropertyPlantAndEquipment": [usd(91, *MAY_2016, fy=2016, filed=a),
                                                       usd(91, *MAY_2016, fy=2017, filed=b)],
        "Assets": [usd(10664, None, "2016-05-31", fy=2016, filed=a), usd(10865, None, "2016-12-31", fy=2017, filed=b),
                   usd(23432, None, "2017-12-31", fy=2017, filed=b)],
        "StockholdersEquity": [usd(2630, None, "2016-05-31", fy=2016, filed=a),
                               usd(5143, None, "2016-12-31", fy=2017, filed=b),
                               usd(5878, None, "2017-12-31", fy=2017, filed=b)],
    }


def test_a_year_after_a_change_of_fiscal_year_is_the_period_most_of_its_figures_cover():
    year = _annual(_gpn(), "2016")
    assert (year["revenue"], year["net_income"]) == (2898, 272)          # not calendar 2016's 3370 and 201
    assert (year["operating_cash_flow"], year["capex"]) == (592, 91)
    assert (year["total_assets"], year["total_equity"]) == (10664, 2630)  # not the transition's 2016-12-31 sheet
    assert year["period_end"] == "2016-05-31"
    assert year["calendar_year"] == 2015
    assert _annual(_gpn(), "2017")["revenue"] == 3975                     # the new calendar years are untouched


def test_a_later_10ks_view_of_the_year_holds_only_that_years_figures():
    # The 10-K for 2017's view of FY2016: the May year's cash flows; its calendar 2016 figures are another period.
    view = _vintage(_gpn(), "2016", GPN_10K_2017)
    assert (view["operating_cash_flow"], view["capex"]) == (592, 91)
    assert view.get("revenue") is None and view.get("total_assets") is None
    assert view["period_end"] == "2016-05-31"


def test_a_transition_periods_balance_sheet_is_not_the_old_years():
    # MOS: its year to 2013-05-31, then a June-December 2013 transition period whose balance sheet the 10-K of 2014
    # (filed later) carries at 2013-12-31.
    year = _annual({
        "Revenues": [usd(9974, "2012-06-01", "2013-05-31", fy=2013, filed="2013-07-17")],
        "Assets": [usd(18085, None, "2013-05-31", fy=2013, filed="2013-07-17"),
                   usd(17865, None, "2013-12-31", fy=2014, filed="2015-02-17")],
        "CashAndCashEquivalentsAtCarryingValue": [usd(3697, None, "2013-05-31", fy=2013, filed="2013-07-17"),
                                                  usd(2765, None, "2013-12-31", fy=2014, filed="2015-02-17")],
    }, "2013")
    assert (year["total_assets"], year["cash_and_equivalents"], year["period_end"]) == (18085, 3697, "2013-05-31")


def test_a_recast_year_to_another_end_is_not_the_years():
    # LHX: its June year to 2019-06-28 (its 10-K of 2019, with its cash flows); its 10-K for the year to 2021-01-01
    # recasts the year to 2020-01-03 (fiscal-year key 2019 too; an income statement alone) and opens at the
    # transition period's end.
    a, b = "2019-08-22", "2021-03-01"
    year = _annual({
        "Revenues": [usd(6801, "2018-06-30", "2019-06-28", fy=2019, filed=a),
                     usd(17465, "2019-12-28", "2021-01-01", fy=2020, filed=b),
                     usd(12856, "2018-12-29", "2020-01-03", fy=2020, filed=b)],
        "NetIncomeLoss": [usd(949, "2018-06-30", "2019-06-28", fy=2019, filed=a),
                          usd(1333, "2018-12-29", "2020-01-03", fy=2020, filed=b)],
        "NetCashProvidedByUsedInOperatingActivities": [usd(1185, "2018-06-30", "2019-06-28", fy=2019, filed=a)],
        "StockholdersEquity": [usd(5200, None, "2019-06-28", fy=2019, filed=a),
                               usd(19825, None, "2020-01-03", fy=2020, filed=b)],
    }, "2019")
    assert (year["revenue"], year["net_income"], year["total_equity"]) == (6801, 949, 5200)
    assert year["period_end"] == "2019-06-28"


def test_a_stray_figure_at_another_date_is_not_the_years():
    # BBY's FY2018 (to 2018-02-03): a later 10-K tags goodwill at its 2018-10-01 impairment test; TEL's September
    # year beside a 52-week figure to March.
    year = _annual({
        "Revenues": [usd(42151, "2017-01-29", "2018-02-03", fy=2018, filed="2018-04-02")],
        "Goodwill": [usd(425, None, "2018-02-03", fy=2018, filed="2018-04-02"),
                     usd(915, None, "2018-10-01", fy=2020, filed="2020-03-19")],
    }, "2018")
    assert year["goodwill"] == 425
    tel = _annual({
        "Revenues": [usd(16281, "2021-09-25", "2022-09-30", fy=2022, filed="2022-11-15")],
        "CommonStockDividendsPerShareDeclared": [usd(2.12, "2021-09-25", "2022-09-30", fy=2022, filed="2022-11-15"),
                                                 usd(2.24, "2021-03-27", "2022-03-25", fy=2024, filed="2024-11-12")],
    }, "2022")
    assert tel["dividends_per_share"] == 2.12


def test_a_year_tagged_to_a_nearby_day_is_the_same_year():
    # CIEN's 10-K of 2019 tags its 52-week year as ending 2019-10-31, later 10-Ks at its true end 2019-11-02: one
    # year, so the later filing's restatement stands.
    year = _annual({
        "Revenues": [usd(3572, "2018-11-01", "2019-10-31", fy=2019, filed="2019-12-20"),
                     usd(3532, "2018-11-04", "2019-11-02", fy=2020, filed="2020-12-18"),
                     usd(3532, "2019-11-03", "2020-10-31", fy=2020, filed="2020-12-18")],
        "NetIncomeLoss": [usd(253, "2018-11-01", "2019-10-31", fy=2019, filed="2019-12-20")],
        "Assets": [usd(4229, None, "2019-10-31", fy=2019, filed="2019-12-20"),
                   usd(4239, None, "2019-11-02", fy=2020, filed="2020-12-18")],
    }, "2019")
    assert (year["revenue"], year["net_income"], year["total_assets"]) == (3532, 253, 4239)


def test_two_periods_with_as_many_figures_each_take_the_later():
    # One flow figure of the May year, one of the recast calendar year: the later period's, alone.
    year = _annual({
        "NetCashProvidedByUsedInOperatingActivities": [usd(592, *MAY_2016, fy=2016, filed=GPN_10K_2016)],
        "Revenues": [usd(3370, *CAL_2016, fy=2017, filed=GPN_10K_2017)],
    }, "2016")
    assert (year["revenue"], year.get("operating_cash_flow"), year["period_end"]) == (3370, None, "2016-12-31")


def test_after_a_reverse_merger_the_year_is_the_period_most_of_its_figures_cover():
    # KDP (Dr Pepper Snapple's CIK): its own 10-K for calendar 2017; the merged company's later 10-Ks carry its
    # accounting predecessor's (Keurig's) year to 2017-09-30 with more figures, cash flows among them: the year is
    # Keurig's, as the company's later reports present its history, and DPS's 2017-12-31 balance sheet isn't its.
    a, b = "2018-02-14", "2019-02-28"
    year = _annual({
        "Revenues": [usd(6690, "2017-01-01", "2017-12-31", fy=2017, filed=a),
                     usd(4269, "2016-09-25", "2017-09-30", fy=2018, filed=b),
                     usd(7442, "2018-01-01", "2018-12-31", fy=2018, filed=b)],
        "NetIncomeLoss": [usd(1076, "2017-01-01", "2017-12-31", fy=2017, filed=a),
                          usd(1209, "2016-09-25", "2017-09-30", fy=2018, filed=b)],
        "NetCashProvidedByUsedInOperatingActivities": [usd(841, "2016-09-25", "2017-09-30", fy=2018, filed=b)],
        "PaymentsToAcquirePropertyPlantAndEquipment": [usd(131, "2016-09-25", "2017-09-30", fy=2018, filed=b)],
        "Assets": [usd(10022, None, "2017-12-31", fy=2017, filed=a)],
    }, "2017")
    assert (year["revenue"], year["net_income"], year["period_end"]) == (4269, 1209, "2017-09-30")
    assert (year["operating_cash_flow"], year["capex"]) == (841, 131)
    assert year.get("total_assets") is None   # DPS's year-end sheet is another period's


def test_a_year_of_balance_sheets_alone_takes_its_latest_end():
    # EXE's 2021 after its fresh start (no full year of flows): the fresh-start balance sheet of 2021-02-09 and the
    # year-end one; a later 10-K's view of 2021 holds more fresh-start figures than year-end ones.
    by_tag = {
        "Assets": [usd(6814, None, "2021-02-09", fy=2023, filed="2024-02-21")],
        "Liabilities": [usd(3228, None, "2021-02-09", fy=2023, filed="2024-02-21")],
        "StockholdersEquity": [usd(5671, None, "2021-12-31", fy=2023, filed="2024-02-21")],
    }
    year = _annual(by_tag, "2021")
    assert year.get("total_assets") is None and year["total_equity"] == 5671
    assert year["period_end"] == "2021-12-31"
    view = _vintage(by_tag, "2021", "2024-02-21")
    assert view.get("total_assets") is None and view["total_equity"] == 5671


def test_a_view_of_balance_sheets_alone_takes_the_end_the_years_flows_share():
    # ADM's 10-K of 2015 opens FY2011's equity at 2011-06-30 (its June year) and a recast calendar 2012's cash at
    # 2011-12-31; its 10-K of 2011, filed before, presented the year to 2011-06-30's income statement.
    by_tag = {
        "Revenues": [usd(80676, "2010-07-01", "2011-06-30", fy=2011, filed="2011-08-25")],
        "StockholdersEquity": [usd(18838, None, "2011-06-30", fy=2014, filed="2015-02-20")],
        "CashAndCashEquivalentsAtCarryingValue": [usd(864, None, "2011-12-31", fy=2014, filed="2015-02-20")],
    }
    view = _vintage(by_tag, "2011", "2015-02-20")
    assert view["total_equity"] == 18838 and view.get("cash_and_equivalents") is None
    assert view["period_end"] == "2011-06-30"


@pytest.mark.parametrize("filed_first", [True, False])
def test_a_vintage_knows_only_the_annual_reports_filed_by_its_own(filed_first):
    # A vintage reads the facts of its day: which period is the key's follows the annual reports filed up to its
    # filing, never a later one's (the as-of view replays the pipeline on the facts known at each filing date). The
    # same figures, with the May year's 10-K filed before the vintage's filing or (an artificial order) after it.
    early, late = ("2016-07-28", "2018-02-23") if filed_first else ("2018-02-23", "2016-07-28")
    by_tag = {
        "Revenues": [usd(3370, *CAL_2016, fy=2017, filed=early), usd(3975, *CAL_2017, fy=2017, filed=early),
                     usd(2898, *MAY_2016, fy=2016, filed=late)],
        "NetIncomeLoss": [usd(272, *MAY_2016, fy=2016, filed=late)],
        "Assets": [usd(10865, None, "2016-12-31", fy=2017, filed=early),
                   usd(10664, None, "2016-05-31", fy=2017, filed=early)],
        "StockholdersEquity": [usd(5143, None, "2016-12-31", fy=2017, filed=early),
                               usd(2630, None, "2016-05-31", fy=2017, filed=early)],
    }
    view = _vintage(by_tag, "2016", early)
    if filed_first:   # the May 10-K came later: this filing's own flow end is the year's
        assert (view["revenue"], view["total_assets"], view["period_end"]) == (3370, 10865, "2016-12-31")
    else:             # the May 10-K came first: its two flow figures make May the key's, so this view is May's sheet
        assert view.get("revenue") is None
        assert (view["total_assets"], view["total_equity"], view["period_end"]) == (10664, 2630, "2016-05-31")
