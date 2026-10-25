"""Tests for XBRLParser — in particular the fiscal-year grouping regression (A1)."""

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.parsers.calculated_metrics import CalculatedMetrics
from src.parsers.xbrl_parser import XBRLParser, explain_no_annual_financials, explain_stale_annual_financials
from src.query.asof import AsOfReader
from tests.conftest import usd


def test_annual_buckets_by_period_not_filing_fy(sample_company_facts):
    """Comparative years inside one 10-K must land in their own year.

    Every ``Revenues``/``Assets`` fact in the FY2024 filing carries ``fy=2024``;
    correct extraction keys by the period (end/frame), so each year gets its own
    value rather than the filing-year value.
    """
    parser = XBRLParser()
    annual = parser.extract_annual_financials(sample_company_facts, years_back=10)

    assert set(annual.keys()) == {"2024", "2023", "2022"}
    assert annual["2024"]["revenue"] == 400
    assert annual["2023"]["revenue"] == 300
    assert annual["2022"]["revenue"] == 200
    # Instant (balance-sheet) concept buckets by end-date year too.
    assert annual["2024"]["total_assets"] == 70
    assert annual["2023"]["total_assets"] == 60
    assert annual["2022"]["total_assets"] == 50
    # Provenance records which XBRL tag each canonical value came from.
    assert annual["2024"]["_source_tags"]["revenue"] == "Revenues"


def test_annual_excludes_quarterly_spans(sample_company_facts):
    """A quarter-length span reported in a 10-K must not pollute annual revenue."""
    parser = XBRLParser()
    annual = parser.extract_annual_financials(sample_company_facts, years_back=10)
    # 120 was the Q4-only figure; the full-year value is 400.
    assert annual["2024"]["revenue"] == 400
    for year in annual.values():
        assert year["revenue"] != 120


def test_annual_years_back_limit(sample_company_facts):
    parser = XBRLParser()
    annual = parser.extract_annual_financials(sample_company_facts, years_back=2)
    assert set(annual.keys()) == {"2024", "2023"}


def test_most_recently_filed_value_wins():
    """A restated value filed later supersedes the original."""
    facts = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            usd(60, end="2023-09-30", fy=2023, filed="2023-11-03"),
                            # Restated a year later
                            usd(62, end="2023-09-30", fy=2024, filed="2024-11-01"),
                        ]
                    }
                }
            }
        }
    }
    parser = XBRLParser()
    annual = parser.extract_annual_financials(facts, years_back=5)
    assert annual["2023"]["total_assets"] == 62


def test_quarterly_ladder_differencing_and_q4():
    """Discrete quarters come from differencing the cumulative (YTD) ladder, and
    Q4 = annual - 9-month YTD."""
    # One fiscal year (start 2023-10-01): YTD ladder at 3/6/9/12 months + the 10-K
    # full-year point that yields Q4.
    s = "2023-10-01"
    ladder = [
        usd(100, s, "2023-12-30", fy=2024, fp="Q1", form="10-Q", filed="2024-02-01"),
        usd(250, s, "2024-03-30", fy=2024, fp="Q2", form="10-Q", filed="2024-05-01"),
        usd(430, s, "2024-06-29", fy=2024, fp="Q3", form="10-Q", filed="2024-08-01"),
        usd(600, s, "2024-09-28", fy=2024, fp="FY", form="10-K", filed="2024-11-01"),
    ]
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": ladder}}}}}
    q = XBRLParser().extract_quarterly_financials(facts)

    assert q["2023-12-30"]["net_income"] == 100          # Q1 (base)
    assert q["2024-03-30"]["net_income"] == 150          # 250 - 100
    assert q["2024-06-29"]["net_income"] == 180          # 430 - 250
    assert q["2024-09-28"]["net_income"] == 170          # Q4 = 600 - 430
    # Fiscal quarter inferred from cumulative span.
    assert q["2024-09-28"]["fiscal_quarter"] == 4
    # The full-year cumulative (600) is never emitted as a discrete quarter.
    assert all(p.get("net_income") != 600 for p in q.values())
    # Discrete quarters sum to the annual full-year value.
    assert sum(q[e]["net_income"] for e in q) == 600


def test_a_sixteen_week_fourth_quarter_is_derived():
    # BUG-095 (code review 9): COST's and PEP's years run 12-12-12-16 weeks, and the ladder took a quarter to span at
    # most 100 days, so their 112- and 119-day fourth quarters were never derived: no Q4, and no trailing year.
    s = "2023-09-04"
    ladder = [
        usd(100, s, "2023-11-26", fy=2024, fp="Q1", form="10-Q", filed="2023-12-15"),
        usd(220, s, "2024-02-18", fy=2024, fp="Q2", form="10-Q", filed="2024-03-08"),
        usd(330, s, "2024-05-12", fy=2024, fp="Q3", form="10-Q", filed="2024-05-31"),
        usd(500, s, "2024-09-01", fy=2024, fp="FY", form="10-K", filed="2024-10-04"),
    ]
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": ladder}}}}})
    assert (q["2024-09-01"]["net_income"], q["2024-09-01"]["fiscal_quarter"]) == (170, 4)


def test_a_missing_rung_derives_no_two_quarter_figure():
    # A quarter runs 12 to 17 weeks: with the half year missing, the nine months less the first quarter is two quarters,
    # no quarter's figure.
    s = "2023-10-01"
    ladder = [
        usd(100, s, "2023-12-30", fy=2024, fp="Q1", form="10-Q", filed="2024-02-01"),
        usd(430, s, "2024-06-29", fy=2024, fp="Q3", form="10-Q", filed="2024-08-01"),
        usd(600, s, "2024-09-28", fy=2024, fp="FY", form="10-K", filed="2024-11-01"),
    ]
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": ladder}}}}})
    assert "net_income" not in q.get("2024-06-29", {})
    assert q["2024-09-28"]["net_income"] == 170


def test_quarterly_cashflow_from_ytd_only_ladder():
    """Cash flow is YTD-only in 10-Qs (no standalone 3-month facts); differencing
    must still recover discrete quarters and Q4."""
    s = "2023-10-01"
    ladder = [
        usd(30, s, "2023-12-30", fy=2024, fp="Q1", form="10-Q", filed="2024-02-01"),
        usd(54, s, "2024-03-30", fy=2024, fp="Q2", form="10-Q", filed="2024-05-01"),
        usd(82, s, "2024-06-29", fy=2024, fp="Q3", form="10-Q", filed="2024-08-01"),
        usd(110, s, "2024-09-28", fy=2024, fp="FY", form="10-K", filed="2024-11-01"),
    ]
    facts = {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ladder}}}}}
    q = XBRLParser().extract_quarterly_financials(facts)
    assert q["2023-12-30"]["operating_cash_flow"] == 30
    assert q["2024-03-30"]["operating_cash_flow"] == 24   # 54 - 30
    assert q["2024-06-29"]["operating_cash_flow"] == 28   # 82 - 54
    assert q["2024-09-28"]["operating_cash_flow"] == 28   # Q4 = 110 - 82


def test_a_years_first_quarter_comes_from_its_own_10q_before_the_second():
    # BUG-124: a year's ladder has one rung until its second 10-Q, and a ladder of one was skipped, so the first quarter
    # had no flows for about three months: the latest quarter of 61 of the user's 126 companies (META's Q1 2026).
    year, q1 = ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-03-31")
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [usd(400.0, *year, fy=2025, fp="FY", form="10-K", filed="2026-01-29"),
                                       usd(110.0, *q1, fy=2026, fp="Q1", form="10-Q", filed="2026-04-30")]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            usd(-40.0, *year, fy=2025, fp="FY", form="10-K", filed="2026-01-29"),
            usd(-12.0, *q1, fy=2026, fp="Q1", form="10-Q", filed="2026-04-30")]}},
        "Assets": {"units": {"USD": [
            usd(900.0, None, "2026-03-31", fy=2026, fp="Q1", form="10-Q", filed="2026-04-30")]}},
    }}}
    quarter = XBRLParser().extract_quarterly_financials(facts)["2026-03-31"]
    assert (quarter.get("revenue"), quarter.get("capex"), quarter.get("fiscal_quarter")) == (110.0, 12.0, 1)
    assert quarter["_source_tags"]["revenue"] == "Revenues"


def _january_year(*rungs):
    """A January-year filer (NVDA): FY2026 ended 2026-01-25, then FY2027's 10-Q rungs as (end, revenue)."""
    ladder = [usd(130.0, "2025-01-27", "2026-01-25", fy=2026, fp="FY", form="10-K", filed="2026-02-25")]
    ladder += [usd(val, "2026-01-26", end, fy=2027, fp=f"Q{n}", form="10-Q", filed=end[:8] + "28")
               for n, (end, val) in enumerate(rungs, 1)]
    assets = [usd(900.0, None, end, fy=2027, fp="Q1", form="10-Q", filed=end[:8] + "28") for end, _ in rungs]
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": ladder}}, "Assets": {"units": {"USD": assets}}}}})
    return [(q[end].get("revenue"), q[end].get("fiscal_year"), q[end].get("fiscal_quarter")) for end, _ in rungs]


def test_a_january_years_first_quarter_is_its_new_years():
    # BUG-124: NVDA's quarter to 2026-04-26 opens FY2027; read from its end's calendar year it was FY2026's, beside
    # FY2026's own first quarter.
    assert _january_year(("2026-04-26", 44.0)) == [(44.0, 2027, 1)]


def test_a_year_in_progress_is_the_year_it_starts():
    # A January year's quarters before its 10-K: their latest end's calendar year was the year before theirs.
    assert _january_year(("2026-04-26", 44.0), ("2026-07-26", 91.0)) == [(44.0, 2027, 1), (47.0, 2027, 2)]


def test_a_transition_period_a_10k_closes_keeps_its_ends_year():
    # NOW moved from June years to calendar ones through a 10-K for July-December 2011: closed by a 10-K, that period
    # keeps its end's year, as before, rather than the year after FY2011, which is FY2012's.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(120.0, "2010-07-01", "2011-06-30", fy=2011, fp="FY", form="10-K", filed="2011-09-15"),
        usd(34.0, "2011-07-01", "2011-09-30", fy=2011, fp="Q1", form="10-Q", filed="2011-11-10"),
        usd(73.0, "2011-07-01", "2011-12-31", fy=2011, fp="FY", form="10-K", filed="2012-03-15"),
        usd(47.0, "2012-01-01", "2012-03-31", fy=2012, fp="Q1", form="10-Q", filed="2012-05-10"),
        usd(104.0, "2012-01-01", "2012-06-30", fy=2012, fp="Q2", form="10-Q", filed="2012-08-10")]}}}}}
    q = XBRLParser().extract_quarterly_financials(facts)
    assert [(end, q[end]["fiscal_year"], q[end]["revenue"]) for end in sorted(q)] == [
        ("2011-09-30", 2011, 34.0), ("2011-12-31", 2011, 39.0), ("2012-03-31", 2012, 47.0), ("2012-06-30", 2012, 57.0)]


def test_a_later_quarters_own_3_month_figure_leaves_its_ladder_value():
    # A 10-Q's three months beside its year to date: the quarter is still the year-to-date difference.
    s = "2026-01-01"
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            usd(155.0, "2026-04-01", "2026-06-30", fy=2026, fp="Q2", form="10-Q", filed="2026-07-30"),
            usd(400.0, "2025-01-01", "2025-12-31", fy=2025, fp="FY", form="10-K", filed="2026-01-29"),
            usd(100.0, s, "2026-03-31", fy=2026, fp="Q1", form="10-Q", filed="2026-04-30"),
            usd(250.0, s, "2026-06-30", fy=2026, fp="Q2", form="10-Q", filed="2026-07-30")]}},
        "Assets": {"units": {"USD": [
            usd(900.0, None, "2026-06-30", fy=2026, fp="Q2", form="10-Q", filed="2026-07-30")]}},
    }}}
    q = XBRLParser().extract_quarterly_financials(facts)
    assert (q["2026-03-31"]["revenue"], q["2026-06-30"]["revenue"]) == (100.0, 150.0)


def test_a_lone_later_quarters_figure_stays_unread():
    # Only a year's first quarter starts with its year: a lone later quarter's three months would read as a first
    # quarter by its span, so it stays out (as before BUG-124's fix).
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            usd(400.0, "2025-01-01", "2025-12-31", fy=2025, fp="FY", form="10-K", filed="2026-01-29"),
            usd(155.0, "2026-04-01", "2026-06-30", fy=2026, fp="Q2", form="10-Q", filed="2026-07-30")]}},
        "Assets": {"units": {"USD": [
            usd(900.0, None, "2026-06-30", fy=2026, fp="Q2", form="10-Q", filed="2026-07-30")]}},
    }}}
    assert "revenue" not in XBRLParser().extract_quarterly_financials(facts)["2026-06-30"]


def test_a_lone_first_quarter_figure_makes_no_quarter_of_its_own():
    # BUG-096's lesson: a 10-K can re-date a year's first quarter (ADI's 2011-02-04 beside its 10-Q's 2011-01-29), so a
    # lone 3-month figure fills only a quarter the filings hold, its balance sheet's.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(400.0, "2025-01-01", "2025-12-31", fy=2025, fp="FY", form="10-K", filed="2026-01-29"),
        usd(105.0, "2026-01-01", "2026-04-04", fy=2026, fp="Q1", form="10-K", filed="2027-01-28")]}}}}}
    assert "2026-04-04" not in XBRLParser().extract_quarterly_financials(facts)


ACQUISITIONS, CAPEX, BUYBACKS = ("PaymentsToAcquireBusinessesNetOfCashAcquired",
                                 "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForRepurchaseOfCommonStock")


def _acquisition_quarters(values, capex_signs=(1, 1, 1, 1)):
    """Acquisitions' 2013 year-to-date ladder (three 10-Qs, then the 10-K; None leaves a rung out), each filing with
    capex of the given sign beside it, its sign convention; and the derived quarters."""
    ends = ("2013-03-31", "2013-06-30", "2013-09-30", "2013-12-31")
    forms, filed = ("10-Q", "10-Q", "10-Q", "10-K"), ("2013-05-02", "2013-08-01", "2013-10-31", "2014-02-27")
    acquisitions = [usd(v, "2013-01-01", end, form=form, filed=day)
                    for v, end, form, day in zip(values, ends, forms, filed) if v is not None]
    capex = [usd(sign * 100.0 * n, "2013-01-01", end, form=form, filed=day)
             for n, (sign, end, form, day) in enumerate(zip(capex_signs, ends, forms, filed), 1)]
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {
        ACQUISITIONS: {"units": {"USD": acquisitions}}, CAPEX: {"units": {"USD": capex}}}}})
    return [q.get(end, {}).get("acquisitions") for end in ends]


def test_acquisitions_are_net_so_a_total_below_zero_brought_cash_in():
    # BUG-127 (code review 12): acquisitions are net of the cash acquired, and a total below zero is a net inflow as
    # filed. GE's 2013 10-Qs give -$6.383B and -$6.187B, then +$1.617B for nine months, beside capex paid: read by
    # size, Q1 was a $6.383B outflow and Q3's $7.804B (Lufkin and Avio) a shrinking total, dropped.
    assert _acquisition_quarters((-6383.0, -6187.0, 1617.0, None)) == [-6383.0, 196.0, 7804.0, None]


def test_a_years_acquisition_quarters_add_up_to_its_figure():
    # COP FY2021: -$382M for nine months (the cash acquired with Concho), then +$8.290B for the year: Q4 paid $8.672B,
    # where sizes gave $7.908B. CB 2023: Q3 brought in $468M and Q4 paid $250M.
    assert _acquisition_quarters((-100.0, -250.0, -382.0, 8290.0)) == [-100.0, -150.0, -132.0, 8672.0]
    assert _acquisition_quarters((100.0, 252.0, -216.0, 34.0)) == [100.0, 152.0, -468.0, 250.0]


def test_a_total_of_acquisitions_that_falls_by_half_is_two_bases_not_a_quarter():
    # BUG-140 (code review 13): BUG-127's signed differencing dropped BUG-111's guard too, so a shrinking total became a
    # quarter: JNJ's Q2 10-Q tags its first quarter at $17,500M beside six months of $263M (Q2 -$17,237M, its latest
    # quarter), and GE's FY2017, restated to $2,722M, less nine months as first filed ($6,053M) read Q4 -$3,331M.
    assert _acquisition_quarters((17500.0, 263.0, 400.0, None)) == [17500.0, None, 137.0, None]
    assert _acquisition_quarters((967.0, 2643.0, 6053.0, 2722.0)) == [967.0, 1676.0, 3410.0, None]


def test_a_small_fall_in_acquisitions_is_a_true_up():
    # ABBV's 2012: nine months $780M, the year $688M: Q4 -$92M, a purchase-price true-up.
    assert _acquisition_quarters((671.0, 780.0, 780.0, 688.0)) == [671.0, 109.0, 0.0, -92.0]


def test_acquisitions_in_a_filing_that_tags_its_payments_negative_are_turned_back():
    # LLY's 2008 10-Qs tag every outflow negative and its 10-K positive: a sign convention, not an inflow.
    assert _acquisition_quarters((-10.0, -20.0, -44.4, 6083.0), capex_signs=(-1, -1, -1, 1)) == pytest.approx(
        [10.0, 10.0, 24.4, 6038.6])


def test_a_lone_first_quarter_that_brought_cash_in_reads_below_zero():
    # BUG-124's lone first quarters read acquisitions as filed too.
    year, q1 = ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-03-31")
    facts = {"facts": {"us-gaap": {
        ACQUISITIONS: {"units": {"USD": [
            usd(400.0, *year, form="10-K", filed="2026-01-29"), usd(-50.0, *q1, form="10-Q", filed="2026-04-30")]}},
        CAPEX: {"units": {"USD": [
            usd(80.0, *year, form="10-K", filed="2026-01-29"), usd(20.0, *q1, form="10-Q", filed="2026-04-30")]}},
        "Assets": {"units": {"USD": [usd(900.0, None, "2026-03-31", form="10-Q", filed="2026-04-30")]}},
    }}}
    quarter = XBRLParser().extract_quarterly_financials(facts)["2026-03-31"]
    assert (quarter.get("capex"), quarter.get("acquisitions")) == (20.0, -50.0)


def _annual_record(*filings):
    """The annual record of 10-Ks, each (filed, {tag: {fiscal year: value}}), calendar years."""
    us_gaap = {}
    for filed, tagged in filings:
        for tag, years in tagged.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].extend(
                usd(v, f"{fy}-01-01", f"{fy}-12-31", fy=int(filed[:4]), form="10-K", filed=filed)
                for fy, v in years.items())
    return XBRLParser().extract_annual_financials({"facts": {"us-gaap": us_gaap}})


def test_a_year_whose_acquisitions_brought_cash_in_reads_below_zero():
    # BUG-127: WFC's FY2008 10-K gives -$11.203B, the cash acquired with Wachovia, beside capex paid; read by size it
    # was an $11.203B outflow (ADI's FY2021 -$2.451B with Maxim, USB's FY2009 -$3.074B likewise).
    years = _annual_record(("2009-02-27", {ACQUISITIONS: {2008: -11203.0}, CAPEX: {2008: 3000.0}}))
    assert years["2008"]["acquisitions"] == -11203.0


def test_a_10k_that_tags_its_payments_negative_keeps_its_acquisitions_paid():
    tagged = {ACQUISITIONS: {2008: -400.0}, CAPEX: {2008: -3000.0}, "PaymentsOfDividends": {2008: -500.0}}
    years = _annual_record(("2009-02-27", tagged))
    assert (years["2008"]["acquisitions"], years["2008"]["capex"]) == (400.0, 3000.0)
    facts = {"facts": {"us-gaap": {tag: {"units": {"USD": [usd(v, "2008-01-01", "2008-12-31", fy=2008, form="10-K",
                                                                filed="2009-02-27") for v in by_year.values()]}}
                                   for tag, by_year in tagged.items()}}}
    vintage = XBRLParser().extract_annual_vintages(facts)["2008"]["2009-02-27"]
    assert vintage["acquisitions"] == 400.0  # each filing's own view, as the as-of reader shows it


def test_a_later_10k_that_flips_a_years_acquisitions_is_put_right():
    # GE's 2015 10-K tags FY2012-14's acquisitions negative (its capex positive, its buybacks negative), where its 2013
    # and 2014 10-Ks agree on FY2012's +$1.456B: a sign flip the years around it side against, so FY2012 stays paid.
    # Its 2016 10-K has the same mix of signs with its acquisitions paid: a tie isn't a filing's convention.
    years = _annual_record(
        ("2013-02-26", {ACQUISITIONS: {2011: 11202.0, 2012: 1456.0}, CAPEX: {2011: 100.0, 2012: 100.0}}),
        ("2014-02-27", {ACQUISITIONS: {2012: 1456.0, 2013: 1642.0}, CAPEX: {2012: 100.0, 2013: 100.0}}),
        ("2015-02-27", {ACQUISITIONS: {2012: -1456.0, 2013: -1642.0, 2014: -2639.0},
                        CAPEX: {2012: 100.0, 2013: 100.0, 2014: 100.0},
                        BUYBACKS: {2012: -50.0, 2013: -50.0, 2014: -50.0}}),
        ("2016-02-26", {ACQUISITIONS: {2013: 8026.0, 2014: 2091.0, 2015: 12027.0},
                        CAPEX: {2013: 100.0, 2014: 100.0, 2015: 100.0},
                        BUYBACKS: {2013: -50.0, 2014: -50.0, 2015: -50.0}}))
    assert [years[fy]["acquisitions"] for fy in ("2011", "2012", "2013", "2014", "2015")] == [
        11202.0, 1456.0, 8026.0, 2091.0, 12027.0]


def _year_end_balance_sheet(**tagged):
    """A 2025 balance sheet holding the given debt tags (tag=value), as the parser reads its year."""
    facts = {"facts": {"us-gaap": {
        tag: {"units": {"USD": [usd(val, None, "2025-12-31", fy=2025, fp="FY", form="10-K", filed="2026-02-12")]}}
        for tag, val in tagged.items()}}}
    return XBRLParser().extract_annual_financials(facts).get("2025", {})


def _year_end_debt(**tagged):
    """The long-term debt of ``_year_end_balance_sheet``'s year as (value, tag)."""
    year = _year_end_balance_sheet(**tagged)
    return year.get("long_term_debt"), year.get("_source_tags", {}).get("long_term_debt")


@pytest.mark.parametrize("tag", ["LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable",
                                 "UnsecuredLongTermDebt", "ConvertibleLongTermNotesPayable", "NotesPayable"])
def test_long_term_debt_is_read_from_the_tag_the_filer_uses(tag):
    # BUG-125: long-term debt read LongTermDebtNoncurrent and LongTermDebt only, and 22 companies file it otherwise
    # (VZ's $139.5B under LongTermDebtAndCapitalLeaseObligations, ORCL's $122.3B under LongTermNotesPayable), so
    # their net debt, debt/EBITDA, ROIC and EV were wrong: ORCL's ROIC read 88%.
    assert _year_end_debt(**{tag: 139.5e9}) == (139.5e9, tag)


def test_the_noncurrent_tags_win_beside_the_fallbacks():
    # The current portion is short-term debt's: the noncurrent figure before a fallback, a total last.
    assert _year_end_debt(LongTermDebtNoncurrent=100.0e9, LongTermDebtAndCapitalLeaseObligations=103.0e9) == (
        100.0e9, "LongTermDebtNoncurrent")
    assert _year_end_debt(LongTermDebtAndCapitalLeaseObligations=60.5e9, NotesPayable=63.7e9) == (
        60.5e9, "LongTermDebtAndCapitalLeaseObligations")


@pytest.mark.parametrize("current", [{"DebtCurrent": 8.461e9}, {"LongTermDebtCurrent": 8.211e9},
                                     {"ShortTermBorrowings": 8.211e9, "LongTermDebtCurrent": 8.211e9}])
def test_a_total_beside_current_debt_that_holds_its_current_maturities_gives_way(current):
    # BUG-141 (code review 13): LongTermDebt, a total with the current maturities, outranked the noncurrent tags, so
    # where the balance sheet's current debt holds them they counted twice: BA's FY2025 $53.85B beside $8.46B of
    # current debt, where its balance sheet has $45.64B; T, TMO, GE and IBM likewise. The short-term figure holds them
    # as current debt, as the maturities themselves, or as borrowings tagged on the same line (AMT's).
    assert _year_end_debt(LongTermDebt=53.848e9, LongTermDebtAndCapitalLeaseObligations=45.637e9, **current) == (
        45.637e9, "LongTermDebtAndCapitalLeaseObligations")


def test_bas_total_debt_is_its_balance_sheets():
    year = _year_end_balance_sheet(LongTermDebt=53.848e9, LongTermDebtAndCapitalLeaseObligations=45.637e9,
                                   DebtCurrent=8.461e9)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(54.098e9)  # $62.31B before


@pytest.mark.parametrize("current", [{}, {"ShortTermBorrowings": 0.014e9}, {"LongTermDebtCurrent": 0.0}])
def test_a_total_whose_current_maturities_no_short_term_figure_holds_is_kept(current):
    # ABBV's FY2021: $75.96B, $12.48B of it due within a year, beside $14M of short-term borrowings that don't hold
    # it; UNP's years hold the maturities in no short-term figure, GOOGL's FY2021 in one of zero. The noncurrent
    # figure alone would lose them (review 13: 67 years at 14 filers). ABBV and UNP tag those maturities as
    # LongTermDebtAndCapitalLeaseObligationsCurrent, which short-term debt reads since BUG-159, so their own totals
    # now give way: test_a_long_term_total_gives_way_beside_the_maturities_short_term_debt_now_reads.
    assert _year_end_debt(LongTermDebt=75.962e9, LongTermDebtAndCapitalLeaseObligations=64.189e9, **current) == (
        75.962e9, "LongTermDebt")


def test_a_long_term_debt_below_the_noncurrent_figure_is_a_notes_figure():
    # A total can't be below its noncurrent part: MU's FY2025 LongTermDebt is its notes ($11.53B), its balance sheet's
    # long-term debt with the finance leases $14.02B; AMT's FY2019 a note's $1.9M against $21.13B (BUG-141).
    assert _year_end_debt(LongTermDebt=11.533e9, LongTermDebtAndCapitalLeaseObligations=14.017e9) == (
        14.017e9, "LongTermDebtAndCapitalLeaseObligations")


def test_a_total_with_no_noncurrent_figure_beside_it_is_kept():
    # KLAC's balance sheet tags its noncurrent line LongTermDebt beside its current maturities: with no noncurrent
    # figure, the total is the only long-term debt there is.
    assert _year_end_debt(LongTermDebt=5.88e9, LongTermDebtCurrent=0.75e9) == (5.88e9, "LongTermDebt")


def test_a_total_is_weighed_against_the_noncurrent_figure_that_would_replace_it():
    # The first noncurrent tag a filing uses is the one long-term debt reads in the total's place.
    assert _year_end_debt(LongTermDebt=10.0e9, LongTermDebtAndCapitalLeaseObligations=12.0e9,
                          LongTermNotesPayable=9.0e9) == (12.0e9, "LongTermDebtAndCapitalLeaseObligations")


_K10 = dict(fy=2025, fp="FY", form="10-K", filed="2026-02-12")


def _debt_filing(refinanced=None, filing=_K10, end="2025-12-31", **tagged):
    """A 10-K balance sheet on *end* holding the given tags (tag=value) beside its total, with the short-term debt
    the filer refinanced over the year (ShortTermDebtRefinancedAmount, a period ending on *end*)."""
    us_gaap = {tag: {"units": {"USD": [usd(val, None, end, **filing)]}} for tag, val in tagged.items()}
    us_gaap["LiabilitiesAndStockholdersEquity"] = {"units": {"USD": [usd(100.0e9, None, end, **filing)]}}
    if refinanced is not None:
        us_gaap["ShortTermDebtRefinancedAmount"] = {"units": {"USD": [usd(refinanced, "2025-01-01", end, **filing)]}}
    return {"facts": {"us-gaap": us_gaap}}


def _debt_year(facts):
    return XBRLParser().extract_annual_financials(facts).get("2025", {})


def _short_term_debt(year):
    """The year's short-term debt as (value, tag)."""
    return year.get("short_term_debt"), year.get("_source_tags", {}).get("short_term_debt")


def _total_debt(period):
    return CalculatedMetrics().calculate_all(period)["total_debt"]


def test_current_debt_before_a_refinancing_gives_way_to_the_balance_sheets_borrowings():
    # BUG-142 (code review 13): CVX's DebtCurrent is its note's short-term debt before the $8.25B refinanced to
    # long-term debt, which its $20.135B of long-term debt holds; its balance sheet's short-term debt is the $4.406B
    # it tags ShortTermBorrowings (FY2024). Total debt read $38.18B where its balance sheet has $24.54B.
    year = _debt_year(_debt_filing(8.25e9, DebtCurrent=12.656e9, ShortTermBorrowings=4.406e9,
                                   LongTermDebtCurrent=4.012e9, CommercialPaper=5.386e9,
                                   LongTermDebtAndCapitalLeaseObligations=20.135e9))
    assert _short_term_debt(year) == (4.406e9, "ShortTermBorrowings")
    assert _total_debt(year) == pytest.approx(24.541e9)


def test_a_refinancing_the_short_term_figures_dont_show_leaves_current_debt():
    # COP tags the amount refinanced beside a DebtCurrent that is its balance sheet's ($0.936B, FY2010), and no
    # short-term borrowings that DebtCurrent less it would be.
    year = _debt_year(_debt_filing(1.125e9, DebtCurrent=0.936e9, LongTermDebtAndCapitalLeaseObligations=22.656e9))
    assert _short_term_debt(year) == (0.936e9, "DebtCurrent")


@pytest.mark.parametrize("tagged, short", [
    # NEE's FY2018: its balance sheet's commercial paper, other short-term debt and current maturities.
    (dict(DebtCurrent=10.930e9, ShortTermBorrowings=5.465e9, LongTermDebtCurrent=2.716e9), 10.930e9),
    # VZ's FY2019: the maturities, tagged LongTermDebtAndCapitalLeaseObligationsCurrent.
    (dict(DebtCurrent=10.777e9, ShortTermBorrowings=0.0, LongTermDebtAndCapitalLeaseObligationsCurrent=10.777e9),
     10.777e9),
])
def test_current_debt_above_its_parts_is_the_years(tagged, short):
    # DebtCurrent above ShortTermBorrowings plus LongTermDebtCurrent is no sign of a refinancing: 59 years at 17
    # filers hold maturities, paper or leases there that the parts don't.
    assert _short_term_debt(_debt_year(_debt_filing(**tagged))) == (short, "DebtCurrent")


@pytest.mark.parametrize("maturities", [{"LongTermDebtCurrent": 5.0e9}, {}])
def test_a_refinanced_current_debt_is_set_aside_after_long_term_debts_total(maturities):
    # BUG-141's rule reads DebtCurrent to know the current debt holds the maturities; set aside first, the total
    # would stay beside the noncurrent figure and count the maturities twice. (Since BUG-159 the maturities tagged
    # beside the borrowings tell it too; without them, only DebtCurrent as filed does.)
    year = _debt_year(_debt_filing(8.0e9, DebtCurrent=12.0e9, ShortTermBorrowings=4.0e9, LongTermDebt=30.0e9,
                                   LongTermDebtAndCapitalLeaseObligations=25.0e9, **maturities))
    assert (year.get("long_term_debt"), year.get("short_term_debt")) == (25.0e9, 4.0e9)


@pytest.mark.parametrize("tagged, total", [
    (dict(DebtCurrent=11.296e9, LongTermDebtCurrent=6.457e9, CommercialPaper=4.837e9,
          LongTermDebtNoncurrent=22.842e9), 34.138e9),  # PG's FY2026: debt due within one year holds it
    (dict(ShortTermBorrowings=4.510e9, CommercialPaper=4.226e9, LongTermDebtNoncurrent=20.683e9),
     25.193e9),  # LIN's FY2025: short-term debt
    (dict(ShortTermBorrowings=0.447e9, CommercialPaper=0.447e9, LongTermDebtNoncurrent=8.145e9),
     8.592e9),  # ADI's FY2025: the same line twice
    (dict(ShortTermBorrowings=0.745e9, CommercialPaper=0.750e9, LongTermDebtNoncurrent=23.293e9),
     24.038e9),  # FDX's FY2026: the paper at its face value
    (dict(DebtCurrent=10.930e9, ShortTermBorrowings=5.465e9, LongTermDebtCurrent=2.716e9, CommercialPaper=2.749e9,
          LongTermDebtNoncurrent=26.782e9), 37.712e9),  # NEE's FY2018
])
def test_paper_a_short_term_total_holds_is_counted_once(tagged, total):
    # BUG-143 (code review 13, M1): DebtCurrent and ShortTermBorrowings hold the paper, and total debt added it
    # again: PG's FY2026 $38.98B for $34.14B.
    assert _total_debt(_debt_year(_debt_filing(**tagged))) == pytest.approx(total)


@pytest.mark.parametrize("tagged, total", [
    (dict(LongTermDebtCurrent=12.350e9, CommercialPaper=7.979e9, LongTermDebtNoncurrent=78.328e9),
     98.657e9),  # AAPL's FY2025: a line of its own beside the term debt
    (dict(ShortTermBorrowings=0.097e9, CommercialPaper=1.299e9, LongTermDebtNoncurrent=6.801e9),
     8.197e9),  # HON's FY2013: beside the other short-term borrowings
    (dict(CommercialPaper=0.700e9, LongTermDebtNoncurrent=26.0e9), 26.7e9),  # WMB's: the only short-term figure
    (dict(DebtCurrent=0.410e9, LongTermDebtCurrent=0.597e9, CommercialPaper=0.187e9, LongTermDebtNoncurrent=10.373e9),
     10.970e9),  # IFF's FY2022: a DebtCurrent below the current maturities is a part
    # Review 20 (M-3): IFF's FY2021, its maturities left out: a total 5% short of the paper doesn't hold it
    (dict(DebtCurrent=0.308e9, CommercialPaper=0.324e9, LongTermDebtNoncurrent=10.768e9), 11.400e9),
    (dict(DebtCurrent=1.974e9, LongTermDebtAndCapitalLeaseObligationsCurrent=3.763e9, CommercialPaper=1.800e9,
          LongTermDebtNoncurrent=8.796e9), 12.570e9),  # PH's FY2023: below the maturities tagged with the leases
])
def test_paper_no_short_term_total_holds_is_added(tagged, total):
    assert _total_debt(_debt_year(_debt_filing(**tagged))) == pytest.approx(total)


def test_a_quarter_and_a_vintage_count_the_paper_once():
    facts = _debt_filing(DebtCurrent=11.296e9, CommercialPaper=4.837e9, LongTermDebtNoncurrent=22.842e9)
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-12-31"]
    vintage = XBRLParser().extract_annual_vintages(facts)["2025"]["2026-02-12"]
    assert [_total_debt(p) for p in (quarter, vintage)] == [pytest.approx(34.138e9)] * 2


def test_a_held_paper_reads_zero_in_every_record():
    # BUG-256 (code review 20, I-1): a paper the short-term total holds reads 0.0, no paper outside it, rather than
    # nothing, which the as-of view's carry fills with an earlier filing's (BUG-110).
    facts = _debt_filing(DebtCurrent=11.296e9, CommercialPaper=4.837e9, LongTermDebtNoncurrent=22.842e9)
    parser = XBRLParser()
    records = (parser.extract_annual_financials(facts)["2025"],
               parser.extract_quarterly_financials(facts)["2025-12-31"],
               parser.extract_annual_vintages(facts)["2025"]["2026-02-12"])
    assert [record.get("commercial_paper") for record in records] == [0.0, 0.0, 0.0]


def test_a_notes_paper_on_another_day_is_not_the_years():
    # MCO's FY2016 10-K tags $566M of paper on 2017-02-22, after its year end: with FY2017's own $130M set aside,
    # inside its $429M of current debt, the note's figure took the year (and made a quarter of its own).
    k10_before = dict(fy=2024, fp="FY", form="10-K", filed="2025-02-24")
    facts = _debt_filing(DebtCurrent=0.4294e9, CommercialPaper=0.1299e9, LongTermDebtNoncurrent=5.1111e9)
    us_gaap = facts["facts"]["us-gaap"]
    us_gaap["CommercialPaper"]["units"]["USD"].append(usd(0.566e9, None, "2025-02-22", **k10_before))
    us_gaap["LiabilitiesAndStockholdersEquity"]["units"]["USD"].append(usd(90.0e9, None, "2024-12-31", **k10_before))
    assert _total_debt(_debt_year(facts)) == pytest.approx(5.5405e9)
    assert "2025-02-22" not in XBRLParser().extract_quarterly_financials(facts)


def _with_later_10k(facts, filed, end="2025-12-31", **tagged):
    """*facts* and a later 10-K, filed on *filed*, whose balance sheet on *end* holds the given tags (tag=value)."""
    filing = dict(fy=2026, fp="FY", form="10-K", filed=filed)
    us_gaap = facts["facts"]["us-gaap"]
    for tag, val in {"LiabilitiesAndStockholdersEquity": 100.0e9, **tagged}.items():
        us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, None, end, **filing))
    return facts


def _ko_fy2019():
    """KO's FY2019: its 10-K shows $10.007B of paper apart from the current maturities, and no borrowings; its FY2020
    10-K shows the same paper inside $10.994B of loans and notes payable."""
    return _with_later_10k(_debt_filing(CommercialPaper=10.007e9, LongTermDebtCurrent=4.253e9,
                                        LongTermDebtNoncurrent=27.516e9),
                           "2027-02-25", CommercialPaper=10.007e9, ShortTermBorrowings=10.994e9)


def _hon_fy2014():
    """HON's FY2014: its 10-K shows $1.647B of paper beside $51M of other short-term borrowings; its FY2015 10-K tags
    the combined line, $1.698B, as the borrowings, and no paper."""
    return _with_later_10k(_debt_filing(CommercialPaper=1.647e9, ShortTermBorrowings=0.051e9,
                                        LongTermDebtNoncurrent=6.046e9), "2027-02-12", ShortTermBorrowings=1.698e9)


def _aep_fy2019():
    """AEP's FY2019: its 10-K's $2.838B of short-term debt holds its $2.110B of paper; a later 10-K tags only the
    year's $1.599B of current maturities and its long-term debt."""
    return _with_later_10k(_debt_filing(CommercialPaper=2.110e9, ShortTermBorrowings=2.838e9,
                                        LongTermDebtCurrent=1.599e9, LongTermDebtNoncurrent=25.127e9),
                           "2027-02-24", LongTermDebtCurrent=1.599e9, LongTermDebtNoncurrent=25.127e9)


def _qcom_fy2015():
    """QCOM's FY2015: its 10-K tags its $1.0B short-term debt line as borrowings; its FY2016 10-K tags the same line as
    paper, and no borrowings."""
    return _with_later_10k(_debt_filing(ShortTermBorrowings=1.0e9, LongTermDebtNoncurrent=9.969e9),
                           "2027-02-12", CommercialPaper=1.0e9, LongTermDebtNoncurrent=9.969e9)


def test_paper_a_later_10k_shows_inside_its_borrowings_is_counted_once():
    # KO's FY2019 10-K tags the year's $10.007B of paper and no borrowings; its FY2020 10-K tags the same paper inside
    # $10.994B of loans and notes payable. Judged filing by filing, the first 10-K's paper outlived the second's
    # (total debt $48.52B). This second 10-K tags no maturities; KO's own FY2020 10-K tags its $4.253B beside the
    # borrowings: test_paper_and_maturities_beside_the_borrowings_a_later_10k_shows.
    assert _total_debt(_debt_year(_ko_fy2019())) == pytest.approx(38.510e9)


def test_paper_beside_borrowings_a_later_10k_combines_is_counted_once():
    # HON's FY2014 10-K tags $1.647B of paper beside $51M of other short-term borrowings; its FY2015 10-K tags the
    # combined line, $1.698B, as the borrowings, which the year now reads.
    assert _total_debt(_debt_year(_hon_fy2014())) == pytest.approx(7.744e9)


def test_a_10qs_current_debt_says_nothing_of_a_10ks_paper():
    # A 10-Q's comparative DebtCurrent the annual record doesn't read says nothing of the 10-K's paper.
    q1 = dict(fy=2026, fp="Q1", form="10-Q", filed="2026-04-28")
    facts = _debt_filing(ShortTermBorrowings=0.0, CommercialPaper=1.349e9, LongTermDebtNoncurrent=20.81e9)
    facts["facts"]["us-gaap"]["DebtCurrent"] = {"units": {"USD": [usd(2.157e9, None, "2025-12-31", **q1)]}}
    assert _total_debt(_debt_year(facts)) == pytest.approx(22.159e9)


def _vintage(facts, filed):
    return XBRLParser().extract_annual_vintages(facts)["2025"][filed]


def test_the_first_10ks_vintage_keeps_the_paper_its_own_balance_sheet_shows():
    # BUG-256 (code review 20, I-1): a vintage is one filing's view, which the as-of explorer and /api/asof read "as
    # known then", and BUG-143's rule judged KO's FY2019 10-K's paper by the FY2020 10-K's borrowings, filed a year
    # later: as of mid-2020 KO's total debt read $31.769B for the $41.776B its balance sheet showed.
    first = _vintage(_ko_fy2019(), "2026-02-12")
    assert (first.get("commercial_paper"), _total_debt(first)) == (10.007e9, pytest.approx(41.776e9))


def test_a_later_vintage_whose_borrowings_hold_the_paper_reads_none_outside_them():
    # Held, the paper reads 0.0; and HON's FY2015 10-K, which tags none, states the FY2014 10-K's, which its borrowings
    # hold: left empty, the as-of view would fill it with the first 10-K's paper (BUG-110's carry).
    later = [_vintage(_ko_fy2019(), "2027-02-25"), _vintage(_hon_fy2014(), "2027-02-12")]
    assert [(v.get("short_term_debt"), v.get("commercial_paper")) for v in later] == [(10.994e9, 0.0), (1.698e9, 0.0)]


def _debt_as_of(tmp_path, facts, *days):
    """FY2025's total debt as the as-of view reads it on each of *days*, from the vintages a collection stores."""
    stock = StockData(ticker="DEBT", cik="1", company_name="Debt Co")
    stock.financials_annual_vintages = XBRLParser().extract_annual_vintages(facts)
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([stock])
    with AsOfReader(db) as reader:
        return [_total_debt(reader.as_of_annual("DEBT", 2025, day)) for day in days]


@pytest.mark.parametrize("facts, totals", [
    # the first 10-K's paper apart from its maturities; then the second's borrowings, which hold it, beside the
    # long-term debt carried from the first (this second 10-K tags no maturities; KO's own FY2020 10-K does:
    # test_paper_and_maturities_beside_the_borrowings_a_later_10k_shows)
    (_ko_fy2019, (41.776e9, 38.510e9)),
    (_hon_fy2014, (7.744e9, 7.744e9)),  # the paper beside the other borrowings, then the combined line: not $9.391B
    # the paper inside the short-term debt, beside the maturities on a line of their own: its balance sheet's
    # $29.564B (BUG-159; $27.965B left the maturities out); then the maturities alone, beside which the paper stands
    # (the borrowings the later 10-K leaves out aren't carried: BUG-110's carry fills gaps, not narrower figures)
    (_aep_fy2019, (29.564e9, 28.836e9)),
    # the line as borrowings; then as paper alone, read beside the borrowings carried from the first: not $11.969B
    (_qcom_fy2015, (10.969e9, 10.969e9)),
], ids=["KO", "HON", "AEP", "QCOM"])
def test_the_as_of_view_reads_each_10ks_debt_in_its_window(tmp_path, facts, totals):
    # BUG-256: as of a day before the later 10-K, and a day after it.
    assert _debt_as_of(tmp_path, facts(), "2026-06-30", "2027-06-30") == [pytest.approx(t) for t in totals]


def test_a_vintage_reads_no_10qs_figure_beside_its_paper(tmp_path):
    # The as-of view reads annual reports alone: a 10-Q's comparative borrowings on the date are none of its figures.
    facts = _debt_filing(CommercialPaper=1.0e9, LongTermDebtNoncurrent=9.969e9)
    facts["facts"]["us-gaap"]["ShortTermBorrowings"] = {"units": {"USD": [
        usd(1.0e9, None, "2025-12-31", fy=2026, fp="Q1", form="10-Q", filed="2026-04-28")]}}
    later = _with_later_10k(facts, "2027-02-12", CommercialPaper=1.0e9)
    assert _debt_as_of(tmp_path, later, "2027-06-30") == [pytest.approx(10.969e9)]


def test_the_year_end_checkpoint_counts_a_10ks_paper_once_beside_a_10qs_debt_current():
    # BUG-257 (code review 20, M-2): the checkpoint on a year end reads a 10-Q's comparative DebtCurrent, which holds
    # the 10-K's paper, and BUG-143's rule judged a 10-K's paper by the annual reports alone, the annual record's view:
    # NEE's 2011-12-31 read $24.316B for $22.967B (and D's, CLX's, CTAS's and ROK's year ends).
    q1 = dict(fy=2026, fp="Q1", form="10-Q", filed="2026-04-28")
    facts = _debt_filing(ShortTermBorrowings=0.0, CommercialPaper=1.349e9, LongTermDebtNoncurrent=20.81e9)
    facts["facts"]["us-gaap"]["DebtCurrent"] = {"units": {"USD": [usd(2.157e9, None, "2025-12-31", **q1)]}}
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-12-31"]
    assert (quarter.get("commercial_paper"), _total_debt(quarter)) == (0.0, pytest.approx(22.967e9))


def test_a_short_term_total_no_checkpoint_reads_says_nothing_of_a_quarters_paper():
    # The checkpoints read 10-Qs and 10-Ks: an 8-K's DebtCurrent on the quarter's end is no total of theirs.
    q3 = dict(fy=2025, fp="Q3", form="10-Q", filed="2025-10-28")
    us_gaap = {tag: {"units": {"USD": [usd(val, None, "2025-09-30", **q3)]}} for tag, val in (
        ("CommercialPaper", 1.0e9), ("ShortTermBorrowings", 0.2e9), ("LongTermDebtNoncurrent", 20.0e9),
        ("LiabilitiesAndStockholdersEquity", 90.0e9))}
    us_gaap["DebtCurrent"] = {"units": {"USD": [usd(1.2e9, None, "2025-09-30", fy=2025, fp="Q3", form="8-K",
                                                    filed="2025-11-05")]}}
    quarter = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": us_gaap}})["2025-09-30"]
    assert _total_debt(quarter) == pytest.approx(21.2e9)


@pytest.mark.parametrize("tagged, short, total", [
    # VZ's FY2023: debt maturing within one year, $0 of borrowings and $12.973B of maturities (both tags);
    # its own total debt $150.674B ($137.701B stored).
    (dict(ShortTermBorrowings=0.0, LongTermDebtCurrent=12.973e9, LongTermDebtAndCapitalLeaseObligationsCurrent=12.973e9,
          LongTermDebtAndCapitalLeaseObligations=137.701e9, DebtLongtermAndShorttermCombinedAmount=150.674e9),
     12.973e9, 150.674e9),
    # LIN's FY2025: short-term debt (its paper inside) and the current portion on lines of their own; own $26.989B.
    (dict(ShortTermBorrowings=4.510e9, CommercialPaper=4.226e9, LongTermDebtCurrent=1.796e9,
          LongTermDebtNoncurrent=20.683e9, DebtLongtermAndShorttermCombinedAmount=26.989e9), 6.306e9, 26.989e9),
    # DUK's FY2023: notes payable and paper, current maturities; own (DebtAndCapitalLeaseObligations) $79.540B.
    (dict(ShortTermBorrowings=4.288e9, LongTermDebtCurrent=2.800e9, LongTermDebtNoncurrent=72.452e9),
     7.088e9, 79.540e9),
])
def test_borrowings_beside_the_current_maturities_are_short_term_debt_with_them(tagged, short, total):
    # BUG-159 (iteration 98): short-term debt read the borrowings alone beside a noncurrent long-term figure, so the
    # current maturities on a line of their own were in no debt field: VZ's FY2023 total debt $137.70B for $150.67B.
    year = _debt_year(_debt_filing(**tagged))
    assert (year["short_term_debt"], _total_debt(year)) == (pytest.approx(short), pytest.approx(total))


def test_maturities_tagged_with_leases_alone_are_short_term_debt():
    # APH's FY2024: the current portion of debt only as LongTermDebtAndCapitalLeaseObligationsCurrent, which no field
    # read (UNP's, HD's, CVS's, KO's since FY2023).
    year = _debt_year(_debt_filing(LongTermDebtAndCapitalLeaseObligationsCurrent=0.402e9,
                                   LongTermDebtAndCapitalLeaseObligations=6.484e9))
    assert _short_term_debt(year) == (0.402e9, "LongTermDebtAndCapitalLeaseObligationsCurrent")
    assert _total_debt(year) == pytest.approx(6.886e9)


def test_a_long_term_total_gives_way_beside_the_maturities_short_term_debt_now_reads():
    # ABBV's FY2024: LongTermDebt, a note's $66.841B with the maturities, beside the balance sheet's noncurrent
    # $60.340B and current $6.804B (own $67.144B). BUG-141 kept the total while no short-term figure held them.
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.0, LongTermDebt=66.841e9,
                                   LongTermDebtAndCapitalLeaseObligations=60.340e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=6.804e9))
    assert (year["long_term_debt"], year["short_term_debt"]) == (60.340e9, 6.804e9)


def test_maturities_that_hold_the_borrowings_are_counted_once():
    # VZ's FY2020: debt maturing within one year, $5.889B, tagged LongTermDebtCurrent; its parts $0.320B of
    # borrowings and $5.569B of maturities (LongTermDebtAndCapitalLeaseObligationsCurrent). Own total $129.062B.
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.320e9, LongTermDebtCurrent=5.889e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=5.569e9,
                                   LongTermDebtAndCapitalLeaseObligations=123.173e9))
    assert _total_debt(year) == pytest.approx(129.062e9)


def test_paper_and_maturities_beside_the_borrowings_a_later_10k_shows():
    # KO's FY2019 (BUG-143's test with the FY2020 10-K's maturities): loans and notes payable $10.994B (its paper
    # inside) and current maturities $4.253B, lines of their own: total debt $42.763B, not $38.510B.
    facts = _with_later_10k(_debt_filing(CommercialPaper=10.007e9, LongTermDebtCurrent=4.253e9,
                                         LongTermDebtNoncurrent=27.516e9),
                            "2027-02-25", CommercialPaper=10.007e9, ShortTermBorrowings=10.994e9,
                            LongTermDebtCurrent=4.253e9)
    assert _total_debt(_debt_year(facts)) == pytest.approx(42.763e9)


def test_borrowings_that_are_their_own_parts_are_a_line_of_their_own():
    # DOW's FY2022: notes payable $362M (tagged ShortTermBankLoansAndNotesPayable too) and long-term debt due within
    # one year $362M, separate lines that happen to agree.
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.362e9, ShortTermBankLoansAndNotesPayable=0.362e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=0.362e9,
                                   LongTermDebtAndCapitalLeaseObligations=14.698e9))
    assert year["short_term_debt"] == pytest.approx(0.724e9)


def test_the_filers_own_total_tells_the_lines_are_apart():
    # DUK's FY2018: notes payable $3.410B and current maturities $3.406B, within 1% of each other; its debt and
    # lease obligations, $57.939B, are both beside the noncurrent figure.
    year = _debt_year(_debt_filing(ShortTermBorrowings=3.410e9, LongTermDebtCurrent=3.406e9,
                                   LongTermDebtNoncurrent=51.123e9, DebtAndCapitalLeaseObligations=57.939e9))
    assert _total_debt(year) == pytest.approx(57.939e9)


@pytest.mark.parametrize("tagged, short", [
    # IBM's FY2019: short-term debt $8.797B = current maturities $7.522B + paper $0.304B + loans $0.971B.
    (dict(ShortTermBorrowings=8.797e9, LongTermDebtAndCapitalLeaseObligationsCurrent=7.522e9, CommercialPaper=0.304e9,
          ShortTermBankLoansAndNotesPayable=0.971e9, LongTermDebtNoncurrent=54.102e9), 8.797e9),
    # IBM's FY2024: $5.089B beside $5.059B of maturities, no parts tagged.
    (dict(ShortTermBorrowings=5.089e9, LongTermDebtAndCapitalLeaseObligationsCurrent=5.059e9,
          LongTermDebtNoncurrent=49.884e9), 5.089e9),
    # APTV's: the line tagged as both.
    (dict(ShortTermBorrowings=0.081e9, LongTermDebtAndCapitalLeaseObligationsCurrent=0.081e9,
          LongTermDebtNoncurrent=7.470e9), 0.081e9),
    # GL's FY2022: short-term debt $0.449B (paper and $0.166B of maturities): its own total is the borrowings
    # beside the noncurrent figure.
    (dict(ShortTermBorrowings=0.449e9, LongTermDebtCurrent=0.166e9, LongTermDebtNoncurrent=1.628e9,
          DebtLongtermAndShorttermCombinedAmount=2.077e9), 0.449e9),
])
def test_borrowings_that_hold_the_maturities_stay_the_short_term_debt(tagged, short):
    # IBM tags its balance sheet's short-term debt, which holds the current maturities, as ShortTermBorrowings:
    # the plain sum counted them twice (FY2018 $55.86B against its balance sheet's $45.81B).
    assert _short_term_debt(_debt_year(_debt_filing(**tagged))) == (short, "ShortTermBorrowings")


def _with_10q(facts, filed, end="2025-12-31", **tagged):
    """*facts* and a first-quarter 10-Q, filed on *filed*, whose balance sheet on *end* holds the given tags."""
    filing = dict(fy=2026, fp="Q1", form="10-Q", filed=filed)
    us_gaap = facts["facts"]["us-gaap"]
    for tag, val in {"LiabilitiesAndStockholdersEquity": 100.0e9, **tagged}.items():
        us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, None, end, **filing))
    return facts


def test_a_quarter_takes_its_latest_annual_reports_verdict():
    # IBM's Q1 2026 10-Q: $8.655B of short-term debt beside $7.554B of maturities and no parts; its FY2025 10-K's
    # short-term debt is its maturities.
    facts = _with_10q(_debt_filing(ShortTermBorrowings=6.424e9, LongTermDebtAndCapitalLeaseObligationsCurrent=6.424e9,
                                   LongTermDebtNoncurrent=54.836e9),
                      "2026-04-28", "2026-03-31", ShortTermBorrowings=8.655e9,
                      LongTermDebtAndCapitalLeaseObligationsCurrent=7.554e9, LongTermDebtNoncurrent=57.706e9)
    assert XBRLParser().extract_quarterly_financials(facts)["2026-03-31"]["short_term_debt"] == 8.655e9


def test_a_long_term_total_that_keeps_the_maturities_keeps_them_out_of_short_term_debt():
    # EXC's FY2023: its 10-K's LongTermDebt, $41.279B with the current maturities, beside $0.331B of notes
    # (LongTermNotesPayable, a note's) and the maturities; its FY2024 10-K tags the borrowings and maturities and
    # $0.198B of notes; its 10-Qs a comparative LongTermDebtNoncurrent the annual record never reads.
    facts = _with_later_10k(_debt_filing(LongTermDebt=41.279e9, LongTermNotesPayable=0.331e9,
                                         ShortTermBorrowings=2.523e9, LongTermDebtCurrent=1.403e9),
                            "2027-02-12", ShortTermBorrowings=2.523e9, LongTermDebtCurrent=1.403e9,
                            LongTermNotesPayable=0.198e9)
    facts = _with_10q(facts, "2026-05-01", LongTermDebtNoncurrent=39.692e9)
    assert _total_debt(_debt_year(facts)) == pytest.approx(43.802e9)


def test_the_maturities_a_long_term_total_holds_are_no_short_term_debt():
    # DG's FY2020: LongTermDebt, no noncurrent figure beside it, and the current maturities as
    # LongTermDebtAndCapitalLeaseObligationsCurrent: the total holds them.
    year = _debt_year(_debt_filing(LongTermDebt=2.912e9, LongTermDebtAndCapitalLeaseObligationsCurrent=0.001e9))
    assert (year.get("short_term_debt"), _total_debt(year)) == (None, pytest.approx(2.912e9))


def test_a_refinancings_borrowings_hold_the_maturities():
    # CVX's FY2024 (BUG-142's facts): the borrowings are DebtCurrent less the part refinanced, so they hold the
    # maturities its DebtCurrent lists; BUG-159's step judges DebtCurrent as filed, before BUG-142 sets it aside.
    year = _debt_year(_debt_filing(8.25e9, DebtCurrent=12.656e9, ShortTermBorrowings=4.406e9,
                                   LongTermDebtCurrent=4.012e9, CommercialPaper=5.386e9,
                                   LongTermDebtAndCapitalLeaseObligations=20.135e9))
    assert _total_debt(year) == pytest.approx(24.541e9)


RESTRICTED = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"


def _year_end_cash(**tagged):
    """A 2025 balance sheet holding the given cash tags (tag=value) beside its total, and its cash as (value, tag)."""
    facts = {"facts": {"us-gaap": {
        tag: {"units": {"USD": [usd(val, None, "2025-12-31", fy=2025, fp="FY", form="10-K", filed="2026-02-12")]}}
        for tag, val in {"LiabilitiesAndStockholdersEquity": 90.0e9, **tagged}.items()}}}
    year = XBRLParser().extract_annual_financials(facts).get("2025", {})
    return year.get("cash_and_equivalents"), year.get("_source_tags", {}).get("cash_and_equivalents")


def test_cash_tagged_only_with_restricted_cash_is_read():
    # BUG-139 (code review 13): GE's, CVX's, DHR's, GEV's, GILD's, SBUX's and CB's FY2025 10-Ks tag their cash only
    # with the total that includes restricted cash, so cash was empty and net debt their whole total debt: GE's
    # $22.16B where its $12.39B of cash leaves $9.76B.
    assert _year_end_cash(**{RESTRICTED: 12.392e9}) == (12.392e9, RESTRICTED)


def test_cash_without_restricted_cash_comes_first():
    # Where a balance sheet tags both, its own cash line wins (the total runs a median 0.3% higher).
    assert _year_end_cash(CashAndCashEquivalentsAtCarryingValue=12.0e9, **{RESTRICTED: 12.4e9}) == (
        12.0e9, "CashAndCashEquivalentsAtCarryingValue")
    assert _year_end_cash(Cash=11.0e9, **{RESTRICTED: 12.4e9}) == (11.0e9, "Cash")


def test_restricted_cash_a_balance_sheet_doesnt_show_isnt_read():
    # A cash flow statement reconciles cash with restricted cash for each earlier year it covers: PH's 2024 10-K puts
    # FY2022's at $6.648B (the Meggitt escrow), where its own balance sheet showed $0.536B of cash, and DIS's 2019
    # 10-K opens on FY2016, a year it has no other figure for. A note's total assets can stand on such a year too.
    # The year before's 10-K, whose balance sheet does cover 2023, tags no restricted total.
    k10 = dict(fy=2025, fp="FY", form="10-K", filed="2026-02-12")
    k10_before = dict(fy=2024, fp="FY", form="10-K", filed="2025-02-12")
    facts = {"facts": {"us-gaap": {
        "LiabilitiesAndStockholdersEquity": {"units": {"USD": [
            usd(90.0e9, None, "2025-12-31", **k10), usd(80.0e9, None, "2024-12-31", **k10),
            usd(80.0e9, None, "2024-12-31", **k10_before), usd(70.0e9, None, "2023-12-31", **k10_before)]}},
        "Assets": {"units": {"USD": [usd(90.0e9, None, "2025-12-31", **k10), usd(80.0e9, None, "2024-12-31", **k10),
                                     usd(70.0e9, None, "2023-12-31", **k10),  # a segment note's
                                     usd(80.0e9, None, "2024-12-31", **k10_before),
                                     usd(70.0e9, None, "2023-12-31", **k10_before)]}},
        RESTRICTED: {"units": {"USD": [usd(v, None, end, **k10) for v, end in (
            (12.0e9, "2025-12-31"), (11.0e9, "2024-12-31"), (6.6e9, "2023-12-31"))]}},
    }}}
    parser = XBRLParser()
    years = parser.extract_annual_financials(facts)
    assert {y: p.get("cash_and_equivalents") for y, p in years.items()} == {"2025": 12.0e9, "2024": 11.0e9,
                                                                            "2023": None}
    assert parser.extract_annual_vintages(facts)["2023"]["2026-02-12"].get("cash_and_equivalents") is None


def test_a_quarter_whose_cash_is_tagged_only_with_restricted_cash_has_it():
    q1 = dict(fy=2026, fp="Q1", form="10-Q", filed="2026-04-28")
    facts = {"facts": {"us-gaap": {
        RESTRICTED: {"units": {"USD": [usd(8.0e9, None, "2026-03-31", **q1)]}},
        "LiabilitiesAndStockholdersEquity": {"units": {"USD": [usd(90.0e9, None, "2026-03-31", **q1)]}},
    }}}
    assert XBRLParser().extract_quarterly_financials(facts)["2026-03-31"].get("cash_and_equivalents") == 8.0e9


def test_an_instruments_carrying_amount_is_not_the_years_debt():
    # DebtInstrumentCarryingAmount is often one instrument's, on days with no balance sheet: SLB's $0.5B note at
    # 2020-12-31 beside some $16B of debt, BSX's and HWM's debt-only quarters that cost them seven trailing years.
    assert _year_end_debt(DebtInstrumentCarryingAmount=0.5e9) == (None, None)


def _outflow_quarters(tag, field, values, start="2008-01-01", ends=("2008-03-31", "2008-06-30", "2008-09-30",
                                                                  "2008-12-31")):
    """An outflow's year-to-date ladder (three 10-Qs, then the 10-K's year), and its derived quarters."""
    ladder = [usd(v, start, end, fy=int(ends[-1][:4]), fp=fp, form=form, filed=f"{end[:8]}28")
              for v, end, fp, form in zip(values, ends, ("Q1", "Q2", "Q3", "FY"), ("10-Q", "10-Q", "10-Q", "10-K"))]
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {tag: {"units": {"USD": ladder}}}}})
    return [q.get(end, {}).get(field) for end in ends]


def test_a_falling_year_to_date_outflow_derives_no_quarter():
    # BUG-111: INTU's 10-Qs give capex of $99M for nine months and its 10-K $84M for the year (capitalized software
    # put apart): the fall was stored as a fourth quarter of $15M, and the quarters summed to $114M against $84M.
    quarters = _outflow_quarters("PaymentsToAcquirePropertyPlantAndEquipment", "capex", (33e6, 64e6, 99e6, 84e6),
                                 start="2024-08-01", ends=("2024-10-31", "2025-01-31", "2025-04-30", "2025-07-31"))
    assert quarters == [33e6, 31e6, 35e6, None]


def test_an_outflow_tagged_with_both_signs_in_one_year_keeps_its_quarters():
    # BUG-111: LLY's 10-Qs tag dividends paid negative and its 10-K positive, so the fourth quarter took the two
    # totals' sum: $3.60B of 2008 dividends where a quarter paid $0.52B.
    quarters = _outflow_quarters("PaymentsOfDividends", "dividends_paid", (-500.0, -1000.0, -1500.0, 2000.0))
    assert quarters == [500.0, 500.0, 500.0, 500.0]


def test_an_outflow_tagged_negative_derives_its_quarters():
    quarters = _outflow_quarters("PaymentsOfDividends", "dividends_paid", (-500.0, -1000.0, -1500.0, -2000.0))
    assert quarters == [500.0, 500.0, 500.0, 500.0]


def test_a_quarter_without_an_outflow_stays_zero():
    quarters = _outflow_quarters("PaymentsToAcquireBusinessesNetOfCashAcquired", "acquisitions",
                                 (100.0, 100.0, 250.0, 250.0))
    assert quarters == [100.0, 0.0, 150.0, 0.0]


def test_a_signed_flow_keeps_its_signed_quarters():
    # Only the outflows stored as sizes difference sizes: a loss year's quarters keep their signs.
    quarters = _outflow_quarters("NetIncomeLoss", "net_income", (-100.0, -50.0, 30.0, 120.0))
    assert quarters == [-100.0, 50.0, 80.0, 90.0]


def test_quarterly_no_cap_returns_all_and_limit_works():
    s1, s2 = "2022-10-01", "2023-10-01"
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        usd(10, s1, "2022-12-31", fy=2023, form="10-Q", filed="2023-02-01"),
        usd(25, s1, "2023-03-31", fy=2023, form="10-Q", filed="2023-05-01"),
        usd(10, s2, "2023-12-30", fy=2024, form="10-Q", filed="2024-02-01"),
        usd(25, s2, "2024-03-30", fy=2024, form="10-Q", filed="2024-05-01"),
    ]}}}}}
    parser = XBRLParser()
    assert len(parser.extract_quarterly_financials(facts)) == 4          # all
    assert len(parser.extract_quarterly_financials(facts, quarters_back=2)) == 2


def test_empty_facts_returns_empty():
    parser = XBRLParser()
    assert parser.extract_annual_financials({}) == {}
    assert parser.extract_quarterly_financials({}) == {}


def test_period_year_uses_end_then_frame():
    parser = XBRLParser()
    # End year is primary and consistent across statements...
    assert parser._period_year({"end": "2019-06-30"}) == 2019
    # ...even when SEC's calendar-year frame disagrees (non-Dec fiscal year).
    assert parser._period_year({"frame": "CY2025", "end": "2026-01-31"}) == 2026
    # Frame is only a fallback when end is absent.
    assert parser._period_year({"frame": "CY2020Q4I"}) == 2020
    assert parser._period_year({}) is None


def _dividend_facts(declared, cash_paid):
    """Dividends per share under its two tags (USD/shares facts)."""
    us_gaap = {}
    if declared:
        us_gaap["CommonStockDividendsPerShareDeclared"] = {"units": {"USD/shares": declared}}
    if cash_paid:
        us_gaap["CommonStockDividendsPerShareCashPaid"] = {"units": {"USD/shares": cash_paid}}
    return {"facts": {"us-gaap": us_gaap}}


def test_a_duration_field_ignores_point_in_time_facts():
    # BAC tags each quarterly declaration as a point-in-time fact under the preferred tag, while the
    # fallback tag covers the year: the last declaration (0.24) became FY2023's dividends per share,
    # against 0.92 paid in the year.
    declared = [usd(v, end=e, fy=2023, filed="2024-02-20")
                for v, e in [(0.22, "2023-02-01"), (0.22, "2023-04-26"), (0.24, "2023-07-19"), (0.24, "2023-10-18")]]
    cash_paid = [usd(0.92, start="2023-01-01", end="2023-12-31", fy=2023, filed="2024-02-20")]
    annual = XBRLParser().extract_annual_financials(_dividend_facts(declared, cash_paid))
    assert annual["2023"]["dividends_per_share"] == 0.92
    assert annual["2023"]["_source_tags"]["dividends_per_share"] == "CommonStockDividendsPerShareCashPaid"


def test_a_duration_field_with_only_point_in_time_facts_stays_empty():
    # DIS reports each semiannual payment as a point-in-time fact. No fact covers the year, so there is
    # no annual value (one payment, half the year's dividends, used to stand in for it).
    cash_paid = [usd(0.5, end="2025-01-16", fy=2025, filed="2025-11-20"),
                 usd(0.5, end="2025-07-23", fy=2025, filed="2025-11-20")]
    annual = XBRLParser().extract_annual_financials(_dividend_facts([], cash_paid))
    assert "dividends_per_share" not in annual.get("2025", {})


def _fy2024(val):
    return usd(val, start="2024-01-01", end="2024-12-31", fy=2024, filed="2025-02-27")


def test_the_combined_basic_and_diluted_concepts_fill_both_fields():
    # A filer without dilutive securities may tag one EPS and one share count for both (SCCO for
    # FY2011-18, SPG, O, NOW, PANW, MO, TSLA, CRWD): those years had no EPS or share counts at all.
    annual = XBRLParser().extract_annual_financials({"facts": {"us-gaap": {
        "EarningsPerShareBasicAndDiluted": {"units": {"USD/shares": [_fy2024(1.00)]}},
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted": {"units": {"shares": [_fy2024(773.1e6)]}},
    }}})
    year = annual["2024"]
    assert (year["eps_basic"], year["eps_diluted"]) == (1.00, 1.00)
    assert (year["weighted_avg_shares_basic"], year["weighted_avg_shares_diluted"]) == (773.1e6, 773.1e6)


def test_the_separate_basic_and_diluted_concepts_win_over_the_combined_one():
    annual = XBRLParser().extract_annual_financials({"facts": {"us-gaap": {
        "EarningsPerShareBasic": {"units": {"USD/shares": [_fy2024(1.02)]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [_fy2024(1.00)]}},
        "EarningsPerShareBasicAndDiluted": {"units": {"USD/shares": [_fy2024(1.01)]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [_fy2024(780e6)]}},
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted": {"units": {"shares": [_fy2024(773e6)]}},
    }}})
    year = annual["2024"]
    assert (year["eps_basic"], year["eps_diluted"]) == (1.02, 1.00)
    assert (year["weighted_avg_shares_basic"], year["weighted_avg_shares_diluted"]) == (773e6, 780e6)


def _facts_from(taxonomy, form, tag="Revenues"):
    return {"facts": {taxonomy: {tag: {"units": {"USD": [usd(1.0e9, start="2025-01-01", end="2025-12-31",
                                                             fy=2025, form=form, filed="2026-03-01")]}}}}}


@pytest.mark.parametrize("facts, reason", [
    (None, "SEC has no XBRL financial statements for it"),
    (_facts_from("ifrs-full", "20-F"), "it reports in IFRS (20-F), which isn't mapped"),
    (_facts_from("us-gaap", "6-K"), "it has filed no annual report (20-F) yet"),
    (_facts_from("us-gaap", "10-Q"), "it has filed no annual report (10-K) yet"),
])
def test_an_empty_sec_record_says_why(facts, reason):
    # TSM (IFRS) and XOM's new holding company (10-Qs only) came back with no annual financials and no word on why.
    assert explain_no_annual_financials(facts) == reason


def test_a_10k_filer_whose_facts_cover_no_full_year_gets_the_last_reason():
    # Only a quarter-length fact from a 10-K: the parser finds no fiscal year, and the reason says so.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(2.5e8, start="2025-10-01", end="2025-12-31", fy=2025, form="10-K", filed="2026-02-20")]}}}}}
    assert XBRLParser().extract_annual_financials(facts) == {}
    assert explain_no_annual_financials(facts) == "its annual-report facts give no full fiscal year of a mapped concept"


def _fy_to_march(val, form, unit="USD"):
    entry = usd(val, start="2024-04-01", end="2025-03-31", fy=2025, form=form, filed="2025-05-28")
    return {"units": {unit: [entry]}}


@pytest.mark.parametrize("form", ["20-F", "20-F/A", "40-F"])
def test_a_foreign_issuers_annual_report_is_read(form):
    # ARM files its US-GAAP accounts on 20-F (fiscal year to March), but only 10-K facts were read: no financials.
    year = XBRLParser().extract_annual_financials({"facts": {"us-gaap": {
        "Revenues": _fy_to_march(4.0e9, form),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _fy_to_march(1.06e9, form, unit="shares"),
    }}})["2025"]
    assert (year["revenue"], year["weighted_avg_shares_diluted"]) == (4.0e9, 1.06e9)


def test_a_filer_reporting_in_another_currency_gets_no_record_and_a_reason():
    # Alibaba-style: US GAAP on 20-F, in renminbi. Only the share counts would resolve, a record of nothing else;
    # an empty one lets the run say why.
    facts = {"facts": {"us-gaap": {
        "Revenues": _fy_to_march(9.96e11, "20-F", unit="CNY"),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _fy_to_march(2.4e10, "20-F", unit="shares"),
    }}}
    parser = XBRLParser()
    assert parser.extract_annual_financials(facts) == {}
    assert parser.extract_annual_vintages(facts) == {}
    assert explain_no_annual_financials(facts) == "it reports in CNY (20-F), which isn't converted to dollars"


def _fiscal_years(unit, values, form="20-F"):
    """Full-year facts in *unit* for FY2023-25 (calendar years), each filed the March after."""
    return {unit: [usd(v, start=f"{fy}-01-01", end=f"{fy}-12-31", fy=fy, form=form, filed=f"{fy + 1}-03-20")
                   for fy, v in values.items()]}


def test_a_filer_reporting_in_another_currency_with_dollar_convenience_figures_gets_no_record():
    # Code review 6 (I1): a non-USD 20-F filer also tags a US$ convenience translation of the year it reports, so any
    # USD fact kept the record, at each year's convenience rate (TSM: 4,624 TWD facts beside 1,010 USD ones).
    cny = {2023: 9.0e11, 2024: 9.4e11, 2025: 9.96e11}
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {**_fiscal_years("CNY", cny), **_fiscal_years("USD", {2025: 1.37e11})}},
        "NetIncomeLoss": {"units": {**_fiscal_years("CNY", {fy: v / 8 for fy, v in cny.items()}),
                                    **_fiscal_years("USD", {2025: 1.7e10})}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": _fiscal_years(
            "shares", {2023: 2.5e10, 2024: 2.45e10, 2025: 2.4e10})},
    }}}
    parser = XBRLParser()
    assert parser.extract_annual_financials(facts) == {}
    assert parser.extract_annual_vintages(facts) == {}
    assert explain_no_annual_financials(facts) == "it reports in CNY (20-F), which isn't converted to dollars"


def test_a_filer_reporting_in_another_currency_keeps_no_share_only_quarters():
    # BUG-071: a CAD 10-Q filer's quarters held nothing but share counts while its annual record was dropped.
    q = [usd(v, start=s, end=e, fy=2025, fp=fp, form="10-Q", filed=f) for v, s, e, fp, f in [
        (1.0e9, "2025-01-01", "2025-03-31", "Q1", "2025-05-01"),
        (2.1e9, "2025-01-01", "2025-06-30", "Q2", "2025-08-01")]]
    shares = [usd(5.0e8, end=e, fy=2025, fp=fp, form="10-Q", filed=f) for e, fp, f in [
        ("2025-03-31", "Q1", "2025-05-01"), ("2025-06-30", "Q2", "2025-08-01")]]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"CAD": q}},
                                   "CommonStockSharesOutstanding": {"units": {"shares": shares}}}}}
    assert XBRLParser().extract_quarterly_financials(facts) == {}


def _filed_years(tag, unit, by_fy, instant=False):
    """One tag's facts: {fy: [(filed, value), ...]}, each a calendar fiscal year (or its year-end instant)."""
    return {tag: {"units": {unit: [usd(v, start=None if instant else f"{fy}-01-01", end=f"{fy}-12-31", fy=fy,
                                       filed=filed) for fy, filings in by_fy.items() for filed, v in filings]}}}


def _annual(*tags):
    facts = {"facts": {"us-gaap": {k: v for tag in tags for k, v in tag.items()}}}
    return XBRLParser().extract_annual_financials(facts)


def test_a_later_filings_scale_typo_keeps_the_figure_earlier_filings_report():
    # BUG-061: STX's FY2024 10-K tagged FY2022's dividend per share as 2,770,000; the 10-Ks of 2022 and 2023 said 2.77.
    annual = _annual(_filed_years("CommonStockDividendsPerShareDeclared", "USD/shares", {
        2021: [("2021-08-06", 2.68)],
        2022: [("2022-08-05", 2.77), ("2023-08-04", 2.77), ("2024-08-02", 2770000.0)],
        2023: [("2023-08-04", 2.80), ("2024-08-02", 2.80)]}))
    assert annual["2022"]["dividends_per_share"] == 2.77
    assert [(r["field"], r["kind"], r["dropped"]) for r in annual["2022"]["_repairs"]] == [
        ("dividends_per_share", "scale x10^6", 2770000.0)]


def test_a_later_filing_that_corrects_a_scale_typo_wins():
    # BRK-B's 10-Ks of 2011 and 2012 agreed on 1.6 trillion weighted shares for FY2010; the 2013 one corrected it.
    annual = _annual(_filed_years("WeightedAverageNumberOfSharesOutstandingBasic", "shares", {
        2009: [("2010-02-26", 1.55e6)],
        2010: [("2011-02-28", 1.636e12), ("2012-02-24", 1.636e12), ("2013-03-01", 1.636e6)],
        2011: [("2012-02-24", 1.65e6)]}),
        _filed_years("Revenues", "USD", {2009: [("2010-02-26", 1.12e11)], 2010: [("2011-02-28", 1.36e11)],
                                         2011: [("2012-02-24", 1.43e11)]}))
    assert annual["2010"]["weighted_avg_shares_basic"] == 1.636e6
    assert "_repairs" not in annual["2010"]


def test_a_later_filings_zero_keeps_the_reported_total():
    # BAC's 10-K filed 2011-02-25 tagged FY2008's total assets as 0; three earlier filings say $1,817.9B.
    annual = _annual(_filed_years("Assets", "USD", {
        2007: [("2009-02-27", 1.716e12)],
        2008: [("2009-02-27", 1.818e12), ("2009-08-07", 1.818e12), ("2010-02-26", 1.818e12), ("2011-02-25", 0.0)],
        2009: [("2010-02-26", 2.224e12)]}, instant=True))
    assert annual["2008"]["total_assets"] == 1.818e12



def test_a_later_zero_for_a_line_item_is_kept():
    # T-Mobile's 2014 10-K reports T-Mobile USA's FY2012 after the reverse merger: no long-term debt repaid, where
    # MetroPCS's own 10-K had $25M. Only a total can't be zero.
    annual = _annual(_filed_years("RepaymentsOfLongTermDebt", "USD", {
        2010: [("2011-03-01", 1.6e7)], 2011: [("2012-02-29", 2.429e7)],
        2012: [("2013-03-01", 2.539e7), ("2015-02-19", 0.0)],
        2013: [("2015-02-19", 9.0e6)], 2014: [("2015-02-19", 1.0e9)]}))
    assert (annual["2012"]["debt_repaid"], "_repairs" in annual["2012"]) == (0.0, False)


@pytest.mark.parametrize("fy2022, kept", [(1.549e9, 1.546e9), (-2.0e8, -1.546e9)])
def test_a_sign_flip_goes_back_only_when_the_neighbouring_years_agree(fy2022, kept):
    # DIS's 2023 10-K tagged FY2021's interest expense negative beside two filings and the years around it.
    annual = _annual(_filed_years("InterestExpense", "USD", {
        2020: [("2021-11-23", 1.491e9)],
        2021: [("2021-11-23", 1.546e9), ("2022-11-29", 1.546e9), ("2023-11-21", -1.546e9)],
        2022: [("2023-11-21", fy2022)]}))
    assert annual["2021"]["interest_expense"] == kept


def test_flipped_cash_flow_sections_go_back_when_the_year_then_adds_up():
    # AMD's 2024 10-K tagged FY2021's investing and financing cash flows positive: the year no longer summed to its
    # $0.94B change in cash.
    def year(tag, by_filing):
        return _filed_years(tag, "USD", {2021: list(zip(("2022-02-03", "2023-02-27", "2024-01-31"), by_filing))})

    annual = _annual(year("NetCashProvidedByUsedInOperatingActivities", (3.521e9,) * 3),
                     year("NetCashProvidedByUsedInInvestingActivities", (-0.686e9, -0.686e9, 0.686e9)),
                     year("NetCashProvidedByUsedInFinancingActivities", (-1.895e9, -1.895e9, 1.895e9)),
                     year("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecrease"
                          "IncludingExchangeRateEffect", (0.940e9,) * 3))
    assert (annual["2021"]["investing_cash_flow"], annual["2021"]["financing_cash_flow"]) == (-0.686e9, -1.895e9)


def test_a_ten_for_one_restatement_is_left_alone():
    # NVDA's 2025 10-K restated FY2023's 2.487B shares for its 10:1 split: a split, not a typo, even where the years
    # before it, counting fewer shares, would side with the earlier filings.
    annual = _annual(_filed_years("WeightedAverageNumberOfSharesOutstandingBasic", "shares", {
        2021: [("2022-03-18", 2.40e9)], 2022: [("2023-02-24", 2.45e9)],
        2023: [("2023-02-24", 2.487e9), ("2024-02-21", 2.487e9), ("2025-02-26", 2.487e10)]}),
        _filed_years("Revenues", "USD", {2023: [("2024-02-21", 2.7e10)]}))
    assert (annual["2023"]["weighted_avg_shares_basic"], "_repairs" in annual["2023"]) == (2.487e10, False)


def test_earlier_filings_that_disagree_leave_the_latest_figure():
    # TJX's filings for FY2012 operating income went -16.6M, +16.6M, -16.6M: no figure is agreed on.
    annual = _annual(_filed_years("OperatingIncomeLoss", "USD", {
        2011: [("2012-03-27", 1.03e7)],
        2012: [("2012-03-27", -1.66e7), ("2013-04-02", 1.66e7), ("2014-04-01", -1.66e7)],
        2013: [("2014-04-01", 3.442e9)], 2014: [("2015-03-31", 3.68e9)]}))
    assert annual["2012"]["operating_income"] == -1.66e7


def test_a_later_filing_that_misscales_two_years_has_both_put_back():
    # Each year's error stands beside the other's: the two years on each side outvote it.
    annual = _annual(_filed_years("CommonStockDividendsPerShareDeclared", "USD/shares", {
        2020: [("2020-08-04", 2.58)], 2021: [("2021-08-06", 2.66)],
        2022: [("2022-08-05", 2.77), ("2024-08-02", 2.77e6)],
        2023: [("2023-08-04", 2.80), ("2024-08-02", 2.80e6)],
        2024: [("2024-08-02", 2.80)], 2025: [("2025-08-01", 2.88)]}))
    assert (annual["2022"]["dividends_per_share"], annual["2023"]["dividends_per_share"]) == (2.77, 2.80)


def test_a_fourth_quarter_comes_from_the_restored_year():
    # AMD's Q4 FY2021 is the year less nine months; with the 2024 10-K's flipped year its investing cash flow read
    # +$0.99B.
    start, filings = "2020-12-27", ("2022-02-03", "2023-02-27", "2024-01-31")

    def section(tag, ytd, year):
        rungs = [usd(v, start, end, fy=2021, fp=fp, form="10-Q", filed=f)
                 for v, end, fp, f in zip(ytd, ("2021-03-27", "2021-06-26", "2021-09-25"), ("Q1", "Q2", "Q3"),
                                          ("2021-05-05", "2021-08-04", "2021-11-03"))]
        return {tag: {"units": {"USD": rungs + [usd(v, start, "2021-12-25", fy=2021, filed=f)
                                                for v, f in zip(year, filings)]}}}

    facts = {"facts": {"us-gaap": {
        **section("NetCashProvidedByUsedInOperatingActivities", (0.995e9, 1.947e9, 2.618e9), (3.521e9,) * 3),
        **section("NetCashProvidedByUsedInInvestingActivities", (-0.1e9, -0.2e9, -0.3e9),
                  (-0.686e9, -0.686e9, 0.686e9)),
        **section("NetCashProvidedByUsedInFinancingActivities", (-0.3e9, -0.6e9, -1.2e9),
                  (-1.895e9, -1.895e9, 1.895e9)),
        **section("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncluding"
                  "ExchangeRateEffect", (0.595e9, 1.147e9, 1.118e9), (0.940e9,) * 3)}}}
    q4 = XBRLParser().extract_quarterly_financials(facts)["2021-12-25"]
    assert (round(q4["investing_cash_flow"]), round(q4["financing_cash_flow"])) == (-386000000, -695000000)


def test_a_year_end_balance_comes_from_the_restored_year():
    # BAC's 2011 10-K put total assets at 2008-12-31 at 0, which the quarterly checkpoints read too.
    annual = _filed_years("Assets", "USD", {
        2007: [("2009-02-27", 1.716e12)],
        2008: [("2009-02-27", 1.818e12), ("2010-02-26", 1.818e12), ("2011-02-25", 0.0)],
        2009: [("2010-02-26", 2.224e12)]}, instant=True)
    quarterly = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": annual}})
    assert quarterly["2008-12-31"]["total_assets"] == 1.818e12


def _switcher_facts(later_form="20-F"):
    """US-GAAP 20-Fs through FY2019, then IFRS reports from FY2020, each also restating the two years before."""
    def fy(val, year, filed, form):
        return {"val": val, "start": f"{year}-01-01", "end": f"{year}-12-31", "fy": year, "fp": "FY", "form": form,
                "filed": filed, "accn": filed}

    gaap = [fy(1.0e10 + 1.0e9 * (y - 2015), y, f"{y + 1}-04-15", "20-F") for y in range(2015, 2020)]
    ifrs = [fy(4.5e11 + 1.0e10 * (y - 2018), y, f"{r + 1}-04-15", later_form)
            for r in range(2020, 2026) for y in (r - 2, r - 1, r)]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": gaap}}},
                      "ifrs-full": {"Revenue": {"units": {"TWD": ifrs}}}}}


def test_a_record_that_stops_where_ifrs_reports_begin_is_explained():
    # Code review 6 (I3): with US-GAAP 20-Fs to FY2019 and IFRS ones after, TSM's FY2015-19 were kept and valued
    # beside today's price, with nothing saying the figures were years old.
    assert explain_stale_annual_financials(_switcher_facts(), "2019-12-31") == (
        "later annual reports are in IFRS (20-F), which isn't mapped")


@pytest.mark.parametrize("facts, latest_end", [
    (_switcher_facts(), "2025-12-31"),            # nothing in IFRS after the record's last year
    (_switcher_facts(later_form="6-K"), "2019-12-31"),  # later IFRS facts, but no annual report
])
def test_ifrs_facts_that_are_no_later_annual_report_say_nothing(facts, latest_end):
    assert explain_stale_annual_financials(facts, latest_end) is None


def test_a_quarters_weighted_share_count_is_its_own_average():
    # BUG-074: the ladder differenced year-to-date averages as if they were sums, so AAPL's Q2-Q4 FY2025 diluted counts
    # read about -0.05B. A quarter's average is the year-to-date share-days less the previous rung's, over its days.
    start = "2024-09-29"
    rungs = [("2024-12-28", 15.150e9, "Q1", "10-Q"), ("2025-03-29", 15.100e9, "Q2", "10-Q"),
             ("2025-06-28", 15.050e9, "Q3", "10-Q"), ("2025-09-27", 15.000e9, "FY", "10-K")]
    entries = [{"val": v, "start": start, "end": end, "fy": 2025, "fp": fp, "form": form, "filed": end, "accn": end}
               for end, v, fp, form in rungs]
    quarterly = XBRLParser().extract_quarterly_financials(
        {"facts": {"us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": entries}}}}})
    counts = [quarterly[end]["weighted_avg_shares_diluted"] for end, *_ in rungs]
    # (15.100 x 181 - 15.150 x 90) / 91, and so on for Q3 and Q4
    assert counts == [15.150e9, pytest.approx(15.0505e9, rel=1e-5), pytest.approx(14.9505e9, rel=1e-5),
                      pytest.approx(14.8505e9, rel=1e-5)]


def test_a_quarters_own_3_month_share_count_is_used_where_filed():
    # A 10-Q tags the quarter's own weighted-average count; the year-to-date rungs can disagree with it (GE's restated
    # nine months beside an unrestated half year read -14.0B for Q3 2020), so it wins; Q4, which 10-Ks don't tag, is
    # still the year's share-days less the nine months'.
    start = "2024-09-29"
    ytd = [("2024-12-28", 15.150e9, "Q1", "10-Q"), ("2025-03-29", 15.100e9, "Q2", "10-Q"),
           ("2025-06-28", 15.050e9, "Q3", "10-Q"), ("2025-09-27", 15.000e9, "FY", "10-K")]
    entries = [{"val": v, "start": start, "end": end, "fy": 2025, "fp": fp, "form": form, "filed": end, "accn": end}
               for end, v, fp, form in ytd]
    entries.append({"val": 15.060e9, "start": "2024-12-29", "end": "2025-03-29", "fy": 2025, "fp": "Q2",
                    "form": "10-Q", "filed": "2025-03-29", "accn": "2025-03-29"})
    quarterly = XBRLParser().extract_quarterly_financials(
        {"facts": {"us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": entries}}}}})
    assert [quarterly[end]["weighted_avg_shares_diluted"] for end, *_ in ytd] == [
        15.150e9, 15.060e9, pytest.approx(14.9505e9, rel=1e-5), pytest.approx(14.8505e9, rel=1e-5)]


def test_a_quarters_own_share_count_fills_a_gap_in_the_year_to_date_rungs():
    # A filer that tagged Q2's 3-month count but no half-year one: the year-to-date ladder skips Q2, its own figure
    # doesn't.
    start = "2024-09-29"

    def entry(val, begin, end, fp, form):
        return {"val": val, "start": begin, "end": end, "fy": 2025, "fp": fp, "form": form, "filed": end, "accn": end}

    shares = [entry(15.150e9, start, "2024-12-28", "Q1", "10-Q"),
              entry(15.060e9, "2024-12-29", "2025-03-29", "Q2", "10-Q"),
              entry(15.050e9, start, "2025-06-28", "Q3", "10-Q"), entry(15.000e9, start, "2025-09-27", "FY", "10-K")]
    income = [entry(v, start, end, fp, form) for v, end, fp, form in (
        (3.0e10, "2024-12-28", "Q1", "10-Q"), (5.5e10, "2025-03-29", "Q2", "10-Q"),
        (8.0e10, "2025-06-28", "Q3", "10-Q"), (1.1e11, "2025-09-27", "FY", "10-K"))]
    quarterly = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": shares}},
        "NetIncomeLoss": {"units": {"USD": income}}}}})
    assert quarterly["2025-03-29"]["weighted_avg_shares_diluted"] == 15.060e9


#: AMZN's filings around its 20:1 split of June 2022: (form, date filed).
_SPLIT_FILINGS = {
    "Q1-19": ("10-Q", "2019-04-26"), "Q2-19": ("10-Q", "2019-07-26"), "Q3-19": ("10-Q", "2019-10-25"),
    "K-19": ("10-K", "2020-01-31"), "Q1-20": ("10-Q", "2020-04-30"), "Q2-20": ("10-Q", "2020-07-31"),
    "Q3-20": ("10-Q", "2020-10-30"), "K-20": ("10-K", "2021-02-03"), "Q1-21": ("10-Q", "2021-04-30"),
    "Q2-21": ("10-Q", "2021-07-30"), "Q3-21": ("10-Q", "2021-10-29"), "K-21": ("10-K", "2022-02-04"),
    "Q1-22": ("10-Q", "2022-04-29"), "Q2-22": ("10-Q", "2022-07-29"), "Q3-22": ("10-Q", "2022-10-28"),
    "K-22": ("10-K", "2023-02-03"),
}


def _split_restated_facts():
    """AMZN-like companyfacts around a 20:1 split in June 2022: (start, end, figure as filed before the split, those
    filings, figure as filed after it, those filings). FY2019 is never restated; FY2020's year is, by the 10-K for
    FY2022, and its quarters never are; FY2021's half and nine months are, by the 10-Qs of 2022, but not its first
    quarter, whose latest filing (the 10-Q for Q1 2022) came before the split."""
    eps = [
        ("2019-01-01", "2019-03-31", 7.09, "Q1-19 Q1-20", None, ""),
        ("2019-01-01", "2019-06-30", 12.40, "Q2-19 Q2-20", None, ""),
        ("2019-01-01", "2019-09-30", 16.43, "Q3-19 Q3-20", None, ""),
        ("2019-01-01", "2019-12-31", 23.01, "K-19 K-20 K-21", None, ""),
        ("2020-01-01", "2020-03-31", 5.01, "Q1-20 Q1-21", None, ""),
        ("2020-01-01", "2020-06-30", 15.32, "Q2-20 Q2-21", None, ""),
        ("2020-01-01", "2020-09-30", 27.72, "Q3-20 Q3-21", None, ""),
        ("2020-01-01", "2020-12-31", 41.83, "K-20 K-21", 2.09, "K-22"),
        ("2021-01-01", "2021-03-31", 15.79, "Q1-21 Q1-22", None, ""),
        ("2021-01-01", "2021-06-30", 30.91, "Q2-21", 1.55, "Q2-22"),
        ("2021-01-01", "2021-09-30", 37.03, "Q3-21", 1.85, "Q3-22"),
        ("2021-01-01", "2021-12-31", 64.81, "K-21", 3.24, "K-22"),
    ]
    diluted = [
        ("2019-01-01", "2019-03-31", 500e6, "Q1-19 Q1-20", None, ""),
        ("2019-01-01", "2019-06-30", 501e6, "Q2-19 Q2-20", None, ""),
        ("2019-01-01", "2019-09-30", 502e6, "Q3-19 Q3-20", None, ""),
        ("2019-01-01", "2019-12-31", 503e6, "K-19 K-20 K-21", None, ""),
        ("2020-01-01", "2020-03-31", 506e6, "Q1-20 Q1-21", None, ""),
        ("2020-01-01", "2020-06-30", 507e6, "Q2-20 Q2-21", None, ""),
        ("2020-01-01", "2020-09-30", 508e6, "Q3-20 Q3-21", None, ""),
        ("2020-01-01", "2020-12-31", 510e6, "K-20 K-21", 10.2e9, "K-22"),
        ("2021-01-01", "2021-03-31", 512e6, "Q1-21 Q1-22", None, ""),
        ("2021-01-01", "2021-06-30", 513e6, "Q2-21", 10.26e9, "Q2-22"),
        ("2021-01-01", "2021-09-30", 514e6, "Q3-21", 10.28e9, "Q3-22"),
        ("2021-01-01", "2021-12-31", 515e6, "K-21", 10.3e9, "K-22"),
    ]
    outstanding = [
        (None, "2021-03-31", 511e6, "Q1-21", None, ""),
        (None, "2021-06-30", 512e6, "Q2-21", None, ""),
        (None, "2021-09-30", 513e6, "Q3-21", None, ""),
        (None, "2021-12-31", 514e6, "K-21 Q1-22", 10.28e9, "Q2-22 Q3-22 K-22"),
    ]
    income = [  # dollars, which no split restates
        ("2020-01-01", "2020-03-31", 2.535e9, "Q1-20 Q1-21", None, ""),
        ("2020-01-01", "2020-06-30", 7.778e9, "Q2-20 Q2-21", None, ""),
        ("2020-01-01", "2020-09-30", 14.109e9, "Q3-20 Q3-21", None, ""),
        ("2020-01-01", "2020-12-31", 21.331e9, "K-20 K-21", 21.331e9, "K-22"),
    ]

    def entries(rows):
        out = []
        for start, end, before, before_in, after, after_in in rows:
            for value, accns in ((before, before_in), (after, after_in)):
                for accn in accns.split():
                    form, filed = _SPLIT_FILINGS[accn]
                    entry = {"val": value, "end": end, "accn": accn, "form": form, "filed": filed,
                             "fy": int(filed[:4]), "fp": "FY" if form == "10-K" else accn[:2]}
                    if start:
                        entry["start"] = start
                    out.append(entry)
        return out

    return {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": entries(eps)}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": entries(diluted)}},
        "CommonStockSharesOutstanding": {"units": {"shares": entries(outstanding)}},
        "NetIncomeLoss": {"units": {"USD": entries(income)}},
    }}}


def test_a_years_quarters_are_on_the_split_basis_its_year_is_filed_on():
    # BUG-082: each rung of a year's ladder comes from its latest filing, and a split restates only the filings after
    # it. AMZN's FY2020 was restated by the 10-K for FY2022 (EPS 2.09) while its quarters never were (5.01, 10.31,
    # 12.40), so Q4 2020, the year less nine months, read -25.63; FY2021's first quarter, from the 10-Q filed before
    # the split, sat beside a restated half year, so Q2 2021 read -14.24.
    quarterly = XBRLParser().extract_quarterly_financials(_split_restated_facts())
    eps = {end: quarterly[end]["eps_diluted"] for end in sorted(quarterly) if end >= "2020"}
    assert eps == {"2020-03-31": pytest.approx(0.2505), "2020-06-30": pytest.approx(0.5155),
                   "2020-09-30": pytest.approx(0.62), "2020-12-31": pytest.approx(0.704),
                   "2021-03-31": pytest.approx(0.7895), "2021-06-30": pytest.approx(0.7605),
                   "2021-09-30": pytest.approx(0.30), "2021-12-31": pytest.approx(1.39)}
    # FY2019, which no filing after the split reports, keeps its basis; dollars are never restated.
    assert [quarterly[end]["eps_diluted"] for end in ("2019-03-31", "2019-12-31")] == [
        7.09, pytest.approx(6.58)]
    assert quarterly["2020-12-31"]["net_income"] == pytest.approx(21.331e9 - 14.109e9)


def test_a_quarters_share_counts_are_on_its_years_basis():
    quarterly = XBRLParser().extract_quarterly_financials(_split_restated_facts())
    counts = {end: quarterly[end]["weighted_avg_shares_diluted"] for end in sorted(quarterly)}
    assert all(500e6 <= count <= 510e6 for end, count in counts.items() if end < "2020")
    assert all(10.1e9 <= count <= 10.4e9 for end, count in counts.items() if end >= "2020"), counts
    # A balance at a quarter's end: Q1-Q3 2021 from their own 10-Qs, before the split; the year's end from the
    # filings after it.
    assert {end: quarterly[end]["shares_outstanding"] for end in sorted(quarterly)
            if "shares_outstanding" in quarterly[end]} == {
        "2021-03-31": 10.22e9, "2021-06-30": 10.24e9, "2021-09-30": 10.26e9, "2021-12-31": 10.28e9}


def test_a_ratio_one_count_alone_shows_is_no_split():
    # A split restates every count the later filings report; one count that two filings give at 2:1 (a correction, a
    # mis-tag) restates nothing else.
    facts = _split_restated_facts()
    us_gaap = facts["facts"]["us-gaap"]
    for tag in ("EarningsPerShareDiluted", "CommonStockSharesOutstanding"):
        us_gaap.pop(tag)
    diluted = us_gaap["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"]
    diluted[:] = [e for e in diluted if e["filed"] < "2022-07-29"
                  or (e["start"], e["end"]) == ("2020-01-01", "2020-12-31")]
    for e in diluted:
        if e["filed"] >= "2022-07-29":
            e["val"] = 2 * 510e6
    quarterly = XBRLParser().extract_quarterly_financials(facts)
    assert quarterly["2020-03-31"]["weighted_avg_shares_diluted"] == 506e6


def test_a_reverse_mergers_restated_history_is_no_split():
    # TMUS: the 2013 reverse merger replaced MetroPCS's history with T-Mobile USA's, whose deemed count (535,286,077 for
    # every period) runs 1.46 to 1.49 times MetroPCS's. A split restates each count by its ratio exactly, so this is no
    # split, and MetroPCS's quarters stay as it filed them.
    def count(val, end, form, filed):
        return {"val": val, "start": "2012-01-01", "end": end, "form": form, "filed": filed, "accn": filed,
                "fy": 2012, "fp": "FY" if form == "10-K" else "Q"}

    basic = [count(362_718_613, "2012-03-31", "10-Q", "2012-04-26"),
             count(362_991_209, "2012-06-30", "10-Q", "2012-07-26"),
             count(363_190_434, "2012-09-30", "10-Q", "2012-10-30"),
             count(363_449_061, "2012-12-31", "10-K", "2013-03-01"),
             count(535_286_077, "2012-06-30", "10-Q", "2013-08-08"),
             count(535_286_077, "2012-09-30", "10-Q", "2013-11-07"),
             count(535_286_077, "2012-12-31", "10-K", "2014-02-25")]
    quarterly = XBRLParser().extract_quarterly_financials(
        {"facts": {"us-gaap": {"WeightedAverageNumberOfSharesOutstandingBasic": {"units": {"shares": basic}}}}})
    assert quarterly["2012-03-31"]["weighted_avg_shares_basic"] == 362_718_613


def _scale_mixed_year(counts, eps=None, true=(751.0e6, 751.6e6, 751.9e6, 751.8e6), income=None):
    """A calendar 2021 whose diluted-count rungs (3, 6, 9 and 12 months) are *counts* as filed, and, when *eps* is
    given, its EPS rungs with net income to match (*income*, else the *true* counts, in units, times EPS)."""
    ends = ("2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31")
    filed = ("2021-05-05", "2021-08-04", "2021-11-02", "2022-02-24")

    def entry(val, end, day):
        form = "10-K" if end.endswith("12-31") else "10-Q"
        return {"val": val, "start": "2021-01-01", "end": end, "form": form, "filed": day, "accn": day,
                "fy": 2021, "fp": "FY" if form == "10-K" else "Q"}

    us_gaap = {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
        entry(c, end, day) for c, end, day in zip(counts, ends, filed)]}}}
    if eps:
        us_gaap["EarningsPerShareDiluted"] = {"units": {"USD/shares": [
            entry(e, end, day) for e, end, day in zip(eps, ends, filed)]}}
        income = income or tuple(t * e for t, e in zip(true, eps))
        us_gaap["NetIncomeLoss"] = {"units": {"USD": [
            entry(n, end, day) for n, end, day in zip(income, ends, filed)]}}
    return {"facts": {"us-gaap": us_gaap}}


@pytest.mark.parametrize("counts", [
    (751.0e6, 751.6e6, 751.9e6, 751.8),  # MCD: the year re-tagged in millions by a later 10-K
    (751_000, 751.6e6, 751.9e6, 751_800),  # COP: the first quarter and the year in thousands
])
def test_a_years_share_counts_are_on_one_scale_before_its_fourth_quarter(counts):
    # BUG-092: the ladder differenced counts filed at different scales, so MCD's Q4 2021 read -2,223.0M and COP's Q4
    # 2019 -3,339.5M. Each rung goes onto the scale its own net income and EPS imply (share_scale's rule).
    quarterly = XBRLParser().extract_quarterly_financials(_scale_mixed_year(counts, eps=(2.05, 4.42, 7.26, 10.04)))
    assert [round(quarterly[end]["weighted_avg_shares_diluted"] / 1e6, 2) for end in sorted(quarterly)] == [
        751.0, 752.19, 752.49, 751.5]


def test_a_rung_without_eps_takes_the_scale_its_years_other_rungs_have():
    facts = _scale_mixed_year((751.0e6, 751.6e6, 751.9e6, 751.8), eps=(2.05, 4.42, 7.26, 10.04))
    for tag in ("EarningsPerShareDiluted", "NetIncomeLoss"):
        facts["facts"]["us-gaap"][tag]["units"] = {u: [e for e in entries if e["end"] != "2021-12-31"]
                                                   for u, entries in facts["facts"]["us-gaap"][tag]["units"].items()}
    quarterly = XBRLParser().extract_quarterly_financials(facts)
    assert round(quarterly["2021-12-31"]["weighted_avg_shares_diluted"] / 1e6, 2) == 751.5


def test_a_rung_whose_eps_is_misscaled_keeps_its_count():
    # TMO's 10-Q for Q3 2009 put 2008's nine-month EPS at 1,580,000 (1.58), so its net income over EPS implied a count
    # of 439 beside the 437.1M filed, and the count was rescaled to it; a correction no other rung of the year is near
    # is none.
    counts = (436.0e6, 436.6e6, 437.1e6, 434.7e6)
    income = tuple(c * e for c, e in zip(counts, (0.53, 1.09, 1.58, 2.25)))
    quarterly = XBRLParser().extract_quarterly_financials(
        _scale_mixed_year(counts, eps=(0.53, 1.09, 1_580_000.0, 2.25), income=income))
    assert [round(quarterly[end]["weighted_avg_shares_diluted"] / 1e6, 2) for end in sorted(quarterly)] == [
        436.0, 437.19, 438.08, 427.6]


def test_a_rung_whose_eps_misses_its_count_takes_its_years_scale():
    # COP-like: the first quarter's count in thousands beside an EPS a quarter off, which implies no power of 1000;
    # it's no check, so the rung takes the scale its year's checked rungs have.
    true = (751.0e6, 751.6e6, 751.9e6, 751.8e6)
    income = tuple(t * e for t, e in zip(true, (2.05, 4.42, 7.26, 10.04)))
    quarterly = XBRLParser().extract_quarterly_financials(
        _scale_mixed_year((751_000, 751.6e6, 751.9e6, 751.8e6), eps=(2.05 * 1.25, 4.42, 7.26, 10.04), income=income))
    assert [round(quarterly[end]["weighted_avg_shares_diluted"] / 1e6, 2) for end in sorted(quarterly)] == [
        751.0, 752.19, 752.49, 751.5]


def test_a_count_at_or_below_zero_is_no_count():
    # With nothing to tell the year's scale by, the fourth quarter's share-days difference is negative: no count, not
    # a negative one. A filed zero is no count either.
    quarterly = XBRLParser().extract_quarterly_financials(_scale_mixed_year((751.0e6, 751.6e6, 751.9e6, 751.8)))
    assert "weighted_avg_shares_diluted" not in quarterly.get("2021-12-31", {})
    quarterly = XBRLParser().extract_quarterly_financials(_scale_mixed_year((0.0, 751.6e6, 751.9e6, 751.8e6)))
    assert "weighted_avg_shares_diluted" not in quarterly.get("2021-03-31", {})
    # ...and no baseline: the half year less a zero first quarter would read 1,486.7M for Q2.
    assert "weighted_avg_shares_diluted" not in quarterly.get("2021-06-30", {})
    assert round(quarterly["2021-12-31"]["weighted_avg_shares_diluted"] / 1e6, 2) == 751.5


def _currency_switcher(before, after, switch_fy):
    """A 20-F filer reporting in *before* through FY{switch_fy - 1} and in *after* from FY{switch_fy}: each annual
    report gives its year and the two before it (its balance sheet, two) in its own currency, and one in a currency
    other than dollars also tags a US$ convenience translation of the year it reports."""
    rate = {"USD": 1.0, "RUB": 75.0, "TWD": 31.0}
    tags = {"Revenues": 1.0e10, "NetIncomeLoss": 1.0e9, "Assets": 3.0e10}
    facts = {}
    for tag, size in tags.items():
        instant = tag == "Assets"
        units = {}
        for report_fy in range(2019, 2026):
            currency = before if report_fy < switch_fy else after
            filed = f"{report_fy + 1}-04-15"
            for fy in range(report_fy - (1 if instant else 2), report_fy + 1):
                value = size * 1.08 ** (fy - 2019) * rate[currency]
                units.setdefault(currency, []).append(
                    usd(value, start=None if instant else f"{fy}-01-01", end=f"{fy}-12-31", fy=report_fy, form="20-F",
                        filed=filed))
            if currency != "USD":  # the convenience translation, at that year's rate
                units.setdefault("USD", []).append(usd(
                    size * 1.08 ** (report_fy - 2019) * rate[currency] / (rate[currency] * 0.93),
                    start=None if instant else f"{report_fy}-01-01", end=f"{report_fy}-12-31", fy=report_fy,
                    form="20-F", filed=filed))
        facts[tag] = {"units": units}
    return {"facts": {"us-gaap": facts}}


def test_a_filer_that_switched_to_dollars_keeps_its_dollar_filings():
    # BUG-077 (code review 7, I3): the reporting currency was read over the whole history (the Yandex-to-Nebius path:
    # RUB through FY2022, dollars since), so the filer got no record at all and the run said it reports in RUB.
    facts = _currency_switcher("RUB", "USD", 2023)
    parser = XBRLParser()
    annual = parser.extract_annual_financials(facts)
    assert sorted(annual) == ["2021", "2022", "2023", "2024", "2025"]  # the dollar 20-Fs and their comparatives
    assert annual["2022"]["revenue"] == pytest.approx(1.0e10 * 1.08 ** 3)  # restated in dollars, not converted
    assert {accn for year in parser.extract_annual_vintages(facts).values() for accn in year} == {
        "2024-04-15", "2025-04-15", "2026-04-15"}


def test_a_filer_that_switched_from_dollars_keeps_its_dollar_years_and_says_why_they_stop():
    # The other way round, the later convenience translations made a record of their own.
    facts = _currency_switcher("USD", "TWD", 2023)
    annual = XBRLParser().extract_annual_financials(facts)
    assert sorted(annual) == ["2017", "2018", "2019", "2020", "2021", "2022"]  # the dollar 20-Fs to FY2022
    assert annual["2022"]["revenue"] == pytest.approx(1.0e10 * 1.08 ** 3)
    assert explain_stale_annual_financials(facts, annual["2022"]["period_end"]) == (
        "later annual reports are in TWD (20-F), which isn't converted to dollars")


def _one_year(fy, filed, **figures):
    """One fiscal year's figures, each under its own tag, filed together."""
    return [_filed_years(tag, "USD", {fy: [(filed, value)]}) for tag, value in figures.items()]


def test_the_total_net_of_interest_expense_is_the_revenue_not_its_contract_revenue():
    # BUG-099: AXP files its income statement's total, total revenues net of interest expense, beside its ASC 606
    # contract revenue, a part of it; the contract-revenue tag came first, so FY2025 read $41.30B of $72.23B.
    annual = _annual(*_one_year(2025, "2026-02-06", RevenueFromContractWithCustomerExcludingAssessedTax=41.304e9,
                                RevenuesNetOfInterestExpense=72.229e9))
    assert (annual["2025"]["revenue"], annual["2025"]["_source_tags"]["revenue"]) == (
        72.229e9, "RevenuesNetOfInterestExpense")


def test_a_revenues_figure_the_income_statement_doesnt_add_up_to_gives_way():
    # BUG-099: BLK's FY2024 10-K tags `Revenues` of $12.79B beside its total revenue, $20.41B under the contract-revenue
    # tag, which its operating income and expenses add up to.
    annual = _annual(*_one_year(2024, "2025-02-25", Revenues=12.794e9,
                                RevenueFromContractWithCustomerExcludingAssessedTax=20.407e9,
                                OperatingExpenses=12.833e9, OperatingIncomeLoss=7.574e9))
    assert annual["2024"]["revenue"] == 20.407e9


def test_net_revenue_the_income_statement_adds_up_to_stays_beside_a_gross_contract_figure():
    # MA tags its gross revenue, before rebates and incentives, as contract revenue ($21.83B for FY2018) beside its net
    # revenue ($14.95B), which its operating income and expenses add up to.
    annual = _annual(*_one_year(2018, "2019-02-13", Revenues=14.950e9,
                                RevenueFromContractWithCustomerExcludingAssessedTax=21.831e9,
                                OperatingExpenses=7.668e9, OperatingIncomeLoss=7.282e9))
    assert annual["2018"]["revenue"] == 14.950e9


def test_a_revenue_figure_below_its_own_cost_of_revenue_gives_way():
    # BUG-099: PG's FY2014 10-K tags `Revenues` of $28.4B for FY2012 beside net sales of $82.0B and a cost of products
    # sold of $41.4B: revenue read $28.4B, and the derived gross profit -$13.0B.
    annual = _annual(*_one_year(2012, "2014-08-08", Revenues=28.4e9, SalesRevenueNet=82.006e9,
                                CostOfGoodsAndServicesSold=41.411e9))
    assert annual["2012"]["revenue"] == 82.006e9


def test_a_figure_below_cost_gives_way_only_to_one_that_isnt():
    # Of $1.0B, $1.3B and $1.45B against a cost of revenue of $1.4B, the first gives way to the third; the second stays,
    # as the third is too close to it to tell, and the order puts it first.
    annual = _annual(*_one_year(2022, "2023-02-28", Revenues=1.0e9,
                                RevenueFromContractWithCustomerExcludingAssessedTax=1.3e9, SalesRevenueNet=1.45e9,
                                CostOfRevenue=1.4e9))
    assert annual["2022"]["revenue"] == 1.3e9


def test_figures_all_below_cost_of_revenue_keep_the_tag_order():
    # A filer selling below cost (a young carmaker's first deliveries) has every revenue figure under its cost of
    # revenue, and none of them gives way.
    annual = _annual(*_one_year(2022, "2023-02-28", Revenues=1.658e9,
                                RevenueFromContractWithCustomerExcludingAssessedTax=1.2e9, CostOfRevenue=4.633e9))
    assert annual["2022"]["revenue"] == 1.658e9


def test_a_part_far_from_the_totals_leaves_two_close_totals_to_the_tag_order():
    # DELL's FY2017 holds $48.7B of goods sales beside two totals 0.8% apart, $61.6B and $62.2B: the part gives way,
    # and the order settles the totals (one as first filed, one restated), even where the income statement adds up to
    # the second.
    annual = _annual(*_one_year(2017, "2018-03-29", Revenues=61.642e9,
                                RevenueFromContractWithCustomerExcludingAssessedTax=62.164e9,
                                SalesRevenueGoodsNet=48.706e9, CostOfRevenue=49.205e9, GrossProfit=12.959e9))
    assert annual["2017"]["revenue"] == 61.642e9


def test_a_filing_keeps_its_own_revenue_figure_when_it_has_no_other():
    # PM's FY2016 10-K reports only its revenue including excise taxes ($75.0B, as net sales); its next 10-K adds the
    # figure net of the taxes ($26.7B), which the year's record takes. The first filing's vintage keeps its own figure.
    first, second = "2017-02-15", "2018-02-13"
    us_gaap = {k: v for tag in _one_year(2016, second, RevenueFromContractWithCustomerExcludingAssessedTax=26.685e9,
                                         SalesRevenueNet=74.953e9, CostOfRevenue=9.391e9, GrossProfit=17.294e9)
               for k, v in tag.items()}
    us_gaap["SalesRevenueNet"]["units"]["USD"].append(usd(74.953e9, "2016-01-01", "2016-12-31", fy=2016, filed=first))
    parser, facts = XBRLParser(), {"facts": {"us-gaap": us_gaap}}
    vintages = parser.extract_annual_vintages(facts)["2016"]
    assert (parser.extract_annual_financials(facts)["2016"]["revenue"], vintages[first]["revenue"],
            vintages[second]["revenue"]) == (26.685e9, 74.953e9, 26.685e9)


def test_a_zero_revenue_gives_way_to_a_positive_one():
    # BUG-099: ETN's 10-Ks tag `Revenues` of 0 for FY2011-16 beside net sales of $16-22B.
    annual = _annual(*_one_year(2013, "2014-02-26", Revenues=0.0, SalesRevenueNet=22.046e9))
    assert annual["2013"]["revenue"] == 22.046e9


def test_revenue_stays_net_of_excise_taxes_the_filer_also_reports():
    # PM reports its revenue both net of excise taxes ($26.7B for FY2016) and including them ($75.0B, under the
    # contract-revenue tag that includes assessed taxes). The second only fills a year with no other figure, even
    # where the income statement adds up to it (its expenses then including the taxes).
    annual = _annual(*_one_year(2016, "2017-02-15", RevenueFromContractWithCustomerExcludingAssessedTax=26.685e9,
                                RevenueFromContractWithCustomerIncludingAssessedTax=74.953e9,
                                OperatingExpenses=64.050e9, OperatingIncomeLoss=10.903e9))
    assert annual["2016"]["revenue"] == 26.685e9


def test_revenue_figures_less_than_a_quarter_apart_keep_the_tag_order():
    # Figures under 25% apart are a restatement or a presentation (GE's FY2017 in the cached filings: `Revenues`
    # $99.3B, net sales $113.2B), which the tag order settles, even where the income statement adds up to the other.
    annual = _annual(*_one_year(2017, "2019-02-26", Revenues=99.279e9, SalesRevenueNet=113.192e9,
                                OperatingExpenses=105.0e9, OperatingIncomeLoss=8.192e9))
    assert annual["2017"]["revenue"] == 99.279e9


def test_a_fourth_quarter_is_the_years_revenue_less_nine_months():
    # BUG-099: BLK's Q4 2024 read -$1.94B, the `Revenues` part of its year ($12.79B) less nine months of its revenue.
    start, filed = "2024-01-01", "2025-02-25"
    ytd = [usd(v, start, end, fy=2024, fp=fp, form="10-Q", filed=f) for v, end, fp, f in (
        (4.73e9, "2024-03-31", "Q1", "2024-05-03"), (9.54e9, "2024-06-30", "Q2", "2024-08-02"),
        (14.73e9, "2024-09-30", "Q3", "2024-11-01"))]
    year = {"Revenues": 12.794e9, "RevenueFromContractWithCustomerExcludingAssessedTax": 20.407e9,
            "OperatingExpenses": 12.833e9, "OperatingIncomeLoss": 7.574e9}
    us_gaap = {tag: {"units": {"USD": ([*ytd] if tag == "Revenues" else []) + [usd(v, start, "2024-12-31", fy=2024,
                                                                                 filed=filed)]}}
               for tag, v in year.items()}
    q4 = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": us_gaap}})["2024-12-31"]
    assert q4["revenue"] == pytest.approx(20.407e9 - 14.73e9)


def test_a_filings_vintage_takes_the_years_revenue_not_its_part():
    # BUG-099: the as-of view reads each filing's own figures, and BLK's FY2024 10-K holds both.
    filed = "2025-02-25"
    facts = {"facts": {"us-gaap": {k: v for tag in _one_year(
        2024, filed, Revenues=12.794e9, RevenueFromContractWithCustomerExcludingAssessedTax=20.407e9,
        OperatingExpenses=12.833e9, OperatingIncomeLoss=7.574e9) for k, v in tag.items()}}}
    assert XBRLParser().extract_annual_vintages(facts)["2024"][filed]["revenue"] == 20.407e9


def test_net_revenue_is_no_noninterest_income():
    # BUG-099: `noninterest_income` listed the total net of interest expense, which holds the net interest income, so a
    # bank filing no `NoninterestIncome` would count that twice in its efficiency ratio. No cached filer does.
    annual = _annual(*_one_year(2025, "2026-02-06", InterestIncomeExpenseNet=17.364e9,
                                RevenuesNetOfInterestExpense=72.229e9))
    assert "noninterest_income" not in annual["2025"]
