"""Tests for XBRLParser — in particular the fiscal-year grouping regression (A1)."""

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.parsers.calculated_metrics import CalculatedMetrics
from src.parsers.derived_fields import apply_derivations
from src.parsers.share_scale import normalize_share_scale
from src.parsers.ttm import compute_ttm
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


def _balance_sheet_filings(*filings, form="10-K"):
    """Facts of filings, each (filed, its balance-sheet dates, {tag: {end: value}}): the filing tags its balance
    sheet's total (LiabilitiesAndStockholdersEquity) on each of those dates."""
    us_gaap = {}
    for filed, dates, tagged in filings:
        tagged = {"LiabilitiesAndStockholdersEquity": {end: 100.0e9 for end in dates}, **tagged}
        for tag, by_end in tagged.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].extend(
                usd(v, None, end, fy=int(filed[:4]), fp="FY" if form == "10-K" else "Q1", form=form, filed=filed)
                for end, v in by_end.items())
    return {"facts": {"us-gaap": us_gaap}}


def _ltd(period):
    return period.get("long_term_debt"), period.get("_source_tags", {}).get("long_term_debt")


def test_a_notes_long_term_debt_on_another_day_isnt_the_years():
    # BUG-319: PSX's FY2015 10-K tags LongTermDebt $1.1B at 2015-03-02, a note's figure (its balance sheets are at
    # 2015-12-31 and 2014-12-31), which the year read ahead of its balance sheet's $8.843B under a later tag.
    facts = _balance_sheet_filings(("2016-02-19", ("2015-12-31", "2014-12-31"), {
        "LongTermDebt": {"2015-03-02": 1.1e9},
        "LongTermDebtAndCapitalLeaseObligations": {"2015-12-31": 8.843e9, "2014-12-31": 7.793e9},
        "DebtCurrent": {"2015-12-31": 0.044e9}}))
    year = XBRLParser().extract_annual_financials(facts)["2015"]
    assert _ltd(year) == (8.843e9, "LongTermDebtAndCapitalLeaseObligations")
    assert _total_debt(year) == pytest.approx(8.887e9)  # $1.144B before
    vintage = XBRLParser().extract_annual_vintages(facts)["2015"]["2016-02-19"]
    assert _ltd(vintage) == (8.843e9, "LongTermDebtAndCapitalLeaseObligations")


def test_a_figure_dated_after_its_filing_takes_no_year():
    # ECHO's FY2023 10-K (filed 2024-02-29) tags LongTermDebt $1.98B at 2024-11-30, a maturity; FY2024 read it ahead
    # of its own 10-K's $25.66B, and the FY2023 10-K had a vintage of FY2024 made of it.
    facts = _balance_sheet_filings(
        ("2024-02-29", ("2023-12-31", "2022-12-31"), {
            "LongTermDebt": {"2024-11-30": 1.98e9},
            "LongTermDebtAndCapitalLeaseObligations": {"2023-12-31": 19.717e9}}),
        ("2025-02-27", ("2024-12-31", "2023-12-31"), {
            "LongTermDebtAndCapitalLeaseObligations": {"2024-12-31": 25.660e9, "2023-12-31": 19.717e9}}))
    assert _ltd(XBRLParser().extract_annual_financials(facts)["2024"]) == (
        25.660e9, "LongTermDebtAndCapitalLeaseObligations")
    assert list(XBRLParser().extract_annual_vintages(facts)["2024"]) == ["2025-02-27"]


def test_a_later_10qs_note_figure_isnt_the_quarters_debt():
    # CHD's Q1 2024 10-Q tags LongTermDebtNoncurrent $2.203B at 2023-03-31, a date its balance sheets don't show;
    # the quarter read it for its own 10-Q's $2.400B (EQIX's, CCI's and DAL's quarters likewise).
    facts = _balance_sheet_filings(
        ("2023-04-27", ("2023-03-31", "2022-12-31"), {"LongTermDebtNoncurrent": {"2023-03-31": 2.4001e9}}),
        ("2024-05-02", ("2024-03-31", "2023-12-31"), {"LongTermDebtNoncurrent": {"2023-03-31": 2.2028e9,
                                                                               "2024-03-31": 2.2034e9}}),
        form="10-Q")
    assert XBRLParser().extract_quarterly_financials(facts)["2023-03-31"]["long_term_debt"] == 2.4001e9


def _gis_10k(tagged):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": [
        usd(v, None, end, fy=2014, form="10-K", filed="2014-07-03") for end, v in by_end.items()]}}
        for tag, by_end in tagged.items()}}}


@pytest.mark.parametrize("total", ["LiabilitiesAndStockholdersEquity", "Assets"])
def test_a_filing_dates_its_balance_sheet_by_its_total_or_its_assets(total):
    # GIS's older 10-Ks tag Assets and no LiabilitiesAndStockholdersEquity: their total assets date the balance sheet.
    facts = _gis_10k({total: {"2014-05-25": 23.1457e9}, "LongTermDebt": {"2014-03-01": 1.0e9},
                      "LongTermDebtAndCapitalLeaseObligations": {"2014-05-25": 6.4235e9}})
    assert _ltd(XBRLParser().extract_annual_financials(facts)["2014"]) == (
        6.4235e9, "LongTermDebtAndCapitalLeaseObligations")


def test_a_filing_that_tags_no_balance_sheet_keeps_its_debt():
    # REG's and MAA's 10-Ks for FY2010-17 tag neither total (the company facts don't carry their balance sheets).
    facts = _gis_10k({"LongTermDebtAndCapitalLeaseObligations": {"2014-05-25": 6.4235e9}})
    assert _ltd(XBRLParser().extract_annual_financials(facts)["2014"]) == (
        6.4235e9, "LongTermDebtAndCapitalLeaseObligations")


def test_a_total_far_below_the_notes_payable_beside_it_is_a_part():
    # BUG-319: PHM's 10-Ks for FY2021-25 tag LongTermDebt of $31-77M beside the notes payable of their balance
    # sheets ($2.029B for FY2021), and the year read the part: total debt $0.064B, net debt -$1.7B.
    year = _year_end_balance_sheet(LongTermDebt=63.9e6, NotesPayable=2029.043e6, OtherNotesPayable=40.185e6,
                                   SeniorNotes=1988.858e6)
    assert _ltd(year) == (2029.043e6, "NotesPayable")
    assert _total_debt(year) == pytest.approx(2029.043e6)


def test_a_noncurrent_figure_far_below_another_is_a_part():
    # GNRC's FY2016 10-K tags LongTermDebtNoncurrent $1,048,400 (its $1,048.4M term loan in thousands) beside its
    # balance sheet's $1.007B line of long-term borrowings and capital leases.
    year = _year_end_balance_sheet(LongTermDebtNoncurrent=1048400.0, LongTermDebtAndCapitalLeaseObligations=1006.758e6,
                                   LongTermDebt=903.673e6, LongTermDebtCurrent=14.399e6,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=14.965e6,
                                   ShortTermBorrowings=31.198e6)
    assert _ltd(year) == (1006.758e6, "LongTermDebtAndCapitalLeaseObligations")


def test_a_part_a_later_10k_repeats_alone_is_still_a_part():
    # BIIB's FY2015 10-K tags LongTermDebtAndCapitalLeaseObligations $18.1M beside $6.56B of notes; its FY2016 10-K
    # repeats FY2015's $18.1M without the notes, and as the later filing it took the year.
    facts = _balance_sheet_filings(
        ("2016-02-03", ("2015-12-31", "2014-12-31"), {
            "LongTermDebtAndCapitalLeaseObligations": {"2015-12-31": 18.1e6},
            "LongTermNotesPayable": {"2015-12-31": 6559.6e6},
            "LongTermDebt": {"2015-12-31": 6521.5e6}, "DebtCurrent": {"2015-12-31": 4.8e6}}),
        ("2017-02-02", ("2016-12-31", "2015-12-31"), {
            "LongTermDebtAndCapitalLeaseObligations": {"2016-12-31": 16.4e6, "2015-12-31": 18.1e6},
            "LongTermNotesPayable": {"2016-12-31": 6556.2e6},
            "LongTermDebt": {"2016-12-31": 6512.7e6, "2015-12-31": 6521.5e6},
            "DebtCurrent": {"2016-12-31": 4.7e6, "2015-12-31": 4.8e6}}))
    years = XBRLParser().extract_annual_financials(facts)
    assert [years[fy]["long_term_debt"] for fy in ("2015", "2016")] == [6521.5e6, 6556.2e6]  # $18.1M, $16.4M before
    vintages = XBRLParser().extract_annual_vintages(facts)
    assert vintages["2015"]["2017-02-02"]["long_term_debt"] == 6521.5e6


def test_a_vintage_doesnt_read_a_later_filings_evidence():
    # A filing's vintage holds what was known when it was filed: the figure a later 10-K shows to be a part stands in
    # the earlier filing's own vintage.
    facts = _balance_sheet_filings(
        ("2016-02-03", ("2015-12-31", "2014-12-31"), {
            "LongTermDebtAndCapitalLeaseObligations": {"2015-12-31": 18.1e6}}),
        ("2017-02-02", ("2016-12-31", "2015-12-31"), {
            "LongTermDebtAndCapitalLeaseObligations": {"2016-12-31": 16.4e6, "2015-12-31": 18.1e6},
            "LongTermNotesPayable": {"2016-12-31": 6556.2e6, "2015-12-31": 6559.6e6}}))
    vintages = XBRLParser().extract_annual_vintages(facts)["2015"]
    assert (vintages["2016-02-03"]["long_term_debt"], vintages["2017-02-02"]["long_term_debt"]) == (18.1e6, 6559.6e6)


def test_a_figure_a_later_filing_restates_isnt_a_part_of_the_earlier_ones():
    # AXON's FY2024 10-K tags its $680M of convertible notes as noncurrent; its 10-K/A moves them to current debt and
    # tags the noncurrent figure $0, as its later filings do. The $0 is the restated figure, not a part of the notes
    # the first 10-K showed: total debt stays $680M, not the notes twice.
    facts = _balance_sheet_filings(
        ("2025-02-28", ("2024-12-31", "2023-12-31"), {"ConvertibleLongTermNotesPayable": {"2024-12-31": 680.289e6}}),
        ("2025-05-07", ("2024-12-31", "2023-12-31"), {
            "ConvertibleLongTermNotesPayable": {"2024-12-31": 0.0}, "LongTermDebtNoncurrent": {"2024-12-31": 0.0},
            "LongTermDebtCurrent": {"2024-12-31": 680.289e6}}),
        ("2026-02-25", ("2025-12-31", "2024-12-31"), {
            "LongTermDebt": {"2024-12-31": 680.289e6}, "LongTermDebtCurrent": {"2024-12-31": 680.289e6},
            "LongTermDebtNoncurrent": {"2024-12-31": 0.0}, "LongTermNotesPayable": {"2024-12-31": 0.0}}))
    year = XBRLParser().extract_annual_financials(facts)["2024"]
    assert _ltd(year) == (0.0, "LongTermDebtNoncurrent")
    assert _total_debt(year) == pytest.approx(680.289e6)


def test_a_figure_under_half_of_another_is_a_part():
    # Half is the bound, not a factor of ten: 40% of the line is no line of its own.
    year = _year_end_balance_sheet(LongTermDebtNoncurrent=0.4e9, LongTermDebtAndCapitalLeaseObligations=1.0e9)
    assert _ltd(year) == (1.0e9, "LongTermDebtAndCapitalLeaseObligations")


@pytest.mark.parametrize("tagged, read", [
    # RMD's FY2013: its debt all due within the year, the noncurrent figure is the total less the maturities
    ({"LongTermDebtNoncurrent": 0.769e6, "LongTermDebt": 300.786e6, "LongTermDebtCurrent": 300.017e6},
     (0.769e6, "LongTermDebtNoncurrent")),
    # two noncurrent figures no further apart than half: the tag order decides
    ({"LongTermDebtNoncurrent": 0.6e9, "LongTermDebtAndCapitalLeaseObligations": 1.0e9},
     (0.6e9, "LongTermDebtNoncurrent")),
    ({"LongTermDebtNoncurrent": 0.5e9, "LongTermDebtAndCapitalLeaseObligations": 1.0e9},
     (0.5e9, "LongTermDebtNoncurrent")),
    ({"LongTermDebt": 1.0e9, "NotesPayable": 1.9e9}, (1.0e9, "LongTermDebt")),
    # a figure with no other of its kind beside it isn't weighed: ETR's 2009 10-Q/A tags -$10.185B, as filed
    ({"LongTermDebtNoncurrent": -10.185e9, "LongTermDebtCurrent": -0.806e9}, (-10.185e9, "LongTermDebtNoncurrent")),
])
def test_figures_that_arent_parts_stand(tagged, read):
    assert _ltd(_year_end_balance_sheet(**tagged)) == read


@pytest.mark.parametrize("tag", ["LongTermNotesAndLoans", "SeniorLongTermNotes", "OtherLongTermDebtNoncurrent",
                                 "LongTermLoansPayable"])
def test_a_total_gives_way_to_a_noncurrent_line_under_a_concept_long_term_debt_doesnt_read(tag):
    # BUG-290: Corpay tags its noncurrent notes as LongTermNotesAndLoans ($4.723B at FY2022), beside its LongTermDebt
    # total ($7.037B) and the $2.314B of current maturities short-term debt reads; BUG-141 weighs only the concepts
    # long-term debt reads, so the total stood and total debt was $9.351B. KMB's FY2009-11 likewise, FICO's
    # SeniorLongTermNotes, ADP's OtherLongTermDebtNoncurrent, JKHY's LongTermLoansPayable. The line is read as the
    # date's noncurrent figure.
    year = _year_end_balance_sheet(LongTermDebt=7.037e9, LongTermDebtCurrent=2.314e9, **{tag: 4.723e9})
    assert (year.get("long_term_debt"), year.get("_source_tags", {}).get("long_term_debt")) == (
        4.723e9, "LongTermDebtNoncurrent")
    assert _total_debt(year) == pytest.approx(7.037e9)


@pytest.mark.parametrize("current, short", [
    ({"DebtCurrent": 0.049e9}, 0.049e9),  # FICO's FY2012 figures, DebtCurrent on the total's own filing
    ({"DebtCurrent": 0.610e9, "LongTermDebtCurrent": 0.049e9}, 0.610e9),  # KMB's FY2009: the maturities inside it
    ({"LongTermDebtAndCapitalLeaseObligationsCurrent": 0.049e9}, 0.049e9),
    # FICO's FY2014: the notes due within the year only in the maturity schedule, inside its current debt
    ({"DebtCurrent": 0.170e9, "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths": 0.049e9}, 0.170e9),
])
def test_the_line_and_the_current_maturities_make_up_the_total(current, short):
    year = _year_end_balance_sheet(LongTermDebt=0.504e9, SeniorLongTermNotes=0.455e9, **current)
    assert (year.get("long_term_debt"), year.get("short_term_debt")) == (0.455e9, short)


def test_a_line_that_doesnt_make_up_the_total_is_a_part_of_it():
    # Equinix's FY2024 LongTermLoansPayable ($0.644B) is one of its loans beside a $15.334B total.
    assert _year_end_debt(LongTermDebt=15.334e9, LongTermDebtCurrent=0.555e9, LongTermLoansPayable=0.644e9) == (
        15.334e9, "LongTermDebt")


def test_a_total_whose_maturities_no_short_term_figure_holds_keeps_them_beside_the_line():
    # FICO's FY2011: $512M of notes, $8M of them due within the year, which no short-term figure holds.
    assert _year_end_debt(LongTermDebt=0.512e9, SeniorLongTermNotes=0.504e9,
                          LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths=0.008e9) == (
        0.512e9, "LongTermDebt")
    # and in a filing whose other column's total gives way, FICO's FY2011 beside its FY2012 figures (the balance
    # sheet's total on both columns)
    facts = _debt_filing(LongTermDebt=0.504e9, SeniorLongTermNotes=0.455e9, DebtCurrent=0.049e9)
    for tag, val in (("LongTermDebt", 0.512e9), ("SeniorLongTermNotes", 0.504e9),
                     ("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", 0.008e9),
                     ("LiabilitiesAndStockholdersEquity", 100.0e9)):
        facts["facts"]["us-gaap"].setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(
            usd(val, None, "2024-12-31", **_K10))
    years = XBRLParser().extract_annual_financials(facts)
    assert [years[fy].get("long_term_debt") for fy in ("2024", "2025")] == [0.512e9, 0.455e9]


def test_a_line_that_is_the_total_itself_leaves_it():
    # One line tagged twice, beside maturities of its own (KLAC's shape): the total is the noncurrent line.
    assert _year_end_debt(LongTermDebt=5.88e9, LongTermNotesAndLoans=5.88e9, LongTermDebtCurrent=0.75e9) == (
        5.88e9, "LongTermDebt")


def test_the_line_that_makes_up_the_total_stands_not_the_first_concept_tagged():
    # JKHY's FY2011 10-K/A: $1.015M of other long-term debt, and the $127.939M of loans that with the $26.092M of
    # current maturities make up its $154.031M total.
    assert _year_end_debt(LongTermDebt=154.031e6, LongTermDebtCurrent=26.092e6, OtherLongTermDebtNoncurrent=1.015e6,
                          LongTermLoansPayable=127.939e6) == (127.939e6, "LongTermDebtNoncurrent")


def test_a_noncurrent_figure_long_term_debt_reads_stands_before_such_a_line():
    assert _year_end_debt(LongTermDebt=10.0e9, LongTermDebtCurrent=2.0e9, LongTermNotesPayable=8.0e9,
                          LongTermNotesAndLoans=8.0e9) == (8.0e9, "LongTermNotesPayable")
    assert _year_end_debt(LongTermDebt=10.0e9, LongTermDebtCurrent=2.0e9, LongTermDebtNoncurrent=7.96e9,
                          LongTermNotesAndLoans=8.0e9) == (7.96e9, "LongTermDebtNoncurrent")


def test_corpays_quarters_and_vintages_read_the_line_too():
    # Corpay's 10-Qs tag the same three figures (Q1 2025: $8.171B, $2.255B, $5.916B); each filing's vintage reads
    # its own.
    q10 = dict(fy=2025, fp="Q1", form="10-Q", filed="2025-05-12")
    facts = {"facts": {"us-gaap": {tag: {"units": {"USD": [usd(val, None, "2025-03-31", **q10)]}} for tag, val in (
        ("LongTermDebt", 8.171e9), ("LongTermDebtCurrent", 2.255e9), ("LongTermNotesAndLoans", 5.916e9))}}}
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-03-31"]
    assert (quarter.get("long_term_debt"), quarter.get("short_term_debt")) == (5.916e9, 2.255e9)
    facts = _debt_filing(LongTermDebt=7.037e9, LongTermDebtCurrent=2.314e9, LongTermNotesAndLoans=4.723e9)
    vintage = next(iter(XBRLParser().extract_annual_vintages(facts)["2025"].values()))
    assert vintage.get("long_term_debt") == 4.723e9


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
    # $29.564B (BUG-159; $27.965B left the maturities out); the later 10-K's maturities alone ($1.599B, a lesser
    # tag under half the $4.437B held) are a narrower figure, so the debt stays the first 10-K's view (BUG-271;
    # it read $28.836B, the paper beside the maturities without the $0.728B of other borrowings)
    (_aep_fy2019, (29.564e9, 29.564e9)),
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


def _filings(*filings):
    """Company facts of the given filings, each ``(form, filed, end, {tag: value})``: that filing's balance sheet on
    *end*, with its total (a filing's two columns are two entries)."""
    us_gaap = {}
    for form, filed, end, tagged in filings:
        filing = dict(fy=int(filed[:4]), fp="FY" if form.startswith("10-K") else "Q2", form=form, filed=filed)
        for tag, val in {"LiabilitiesAndStockholdersEquity": 100.0e9, **tagged}.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, None, end, **filing))
    return {"facts": {"us-gaap": us_gaap}}


def test_borrowings_that_are_their_own_parts_are_a_line_of_their_own():
    # DOW's FY2022: notes payable $362M (tagged ShortTermBankLoansAndNotesPayable too) and long-term debt due within
    # one year $362M, separate lines that happen to agree, as the same 10-K's other column shows: $161M of notes
    # payable beside $231M due within one year at 2021-12-31 (BUG-267: one figure tagged as the borrowings, their part
    # and the maturities is one line, ICE's and AMD's, unless the filing shows them apart).
    year = _debt_year(_filings(
        ("10-K", "2026-02-12", "2025-12-31", dict(
            ShortTermBorrowings=0.362e9, ShortTermBankLoansAndNotesPayable=0.362e9,
            LongTermDebtAndCapitalLeaseObligationsCurrent=0.362e9, LongTermDebtAndCapitalLeaseObligations=14.698e9)),
        ("10-K", "2026-02-12", "2024-12-31", dict(
            ShortTermBorrowings=0.161e9, ShortTermBankLoansAndNotesPayable=0.161e9,
            LongTermDebtAndCapitalLeaseObligationsCurrent=0.231e9, LongTermDebtAndCapitalLeaseObligations=14.280e9))))
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


# BUG-267 (code review 21): BUG-159's combination, against the filings it regressed.

def test_a_year_end_checkpoint_counts_the_paper_once_beside_a_later_10ks_borrowings_and_maturities():
    # I-1, NEE's 2011-12-31: its FY2011 10-K shows $1.349B of paper and $0.808B of maturities, its 10-Qs a comparative
    # DebtCurrent of $2.157B that holds both, its FY2012 10-K the paper, the maturities and $0 of borrowings, which
    # BUG-159 combines. The paper was judged by the 10-Qs' DebtCurrent as filed while the checkpoint read the later
    # 10-K's combination: $21.618B for its balance sheet's $22.967B (BUG-257's fix undone there).
    facts = _debt_filing(CommercialPaper=1.349e9, LongTermDebtCurrent=0.808e9, LongTermDebtNoncurrent=20.81e9)
    for filed in ("2026-04-28", "2026-07-28", "2026-10-27"):
        facts = _with_10q(facts, filed, CommercialPaper=1.349e9, DebtCurrent=2.157e9, LongTermDebtCurrent=0.808e9,
                          LongTermDebtNoncurrent=20.81e9, ShortTermBorrowings=0.0)
    facts = _with_later_10k(facts, "2027-02-26", CommercialPaper=1.349e9, LongTermDebtCurrent=0.808e9,
                            LongTermDebtNoncurrent=20.81e9, ShortTermBorrowings=0.0)
    assert _total_debt(XBRLParser().extract_quarterly_financials(facts)["2025-12-31"]) == pytest.approx(22.967e9)


def test_one_line_tagged_as_borrowings_paper_and_maturities_is_counted_once():
    # I-2, ICE's FY2015 10-K: its $2.591B short-term debt line, its commercial paper, is tagged ShortTermBorrowings,
    # CommercialPaper and LongTermDebtCurrent, and LongTermDebt is $4.717B + $2.591B. BUG-159's "own parts are a line
    # of their own" (DOW's) added the line to itself: $9.899B for $7.308B.
    year = _debt_year(_debt_filing(ShortTermBorrowings=2.591e9, CommercialPaper=2.591e9, LongTermDebtCurrent=2.591e9,
                                   LongTermDebtNoncurrent=4.717e9, LongTermDebt=7.308e9))
    assert _total_debt(year) == pytest.approx(7.308e9)


def test_a_line_of_credit_tagged_three_ways_is_counted_once_in_a_quarter():
    # I-2, AMD's Q1 2016 10-Q: its $230M line of credit is tagged ShortTermBorrowings, LinesOfCreditCurrent and
    # LongTermDebtAndCapitalLeaseObligationsCurrent in both its columns ($0.460B read). Its FY2015 10-K tags the first
    # and the last, and its FY2016 10-K's LongTermDebt is $2.007B + $0.230B. (Its FY2014 10-K showed a $55M line of
    # credit apart from $60M of maturities at 2013-12-28: another filing's columns say nothing of this one's lines.)
    line = dict(ShortTermBorrowings=0.230e9, LinesOfCreditCurrent=0.230e9,
                LongTermDebtAndCapitalLeaseObligationsCurrent=0.230e9)
    facts = _filings(("10-K", "2015-02-19", "2013-12-28", dict(
        ShortTermBorrowings=0.055e9, LinesOfCreditCurrent=0.055e9,
        LongTermDebtAndCapitalLeaseObligationsCurrent=0.060e9, LongTermDebtNoncurrent=1.986e9)),
        ("10-Q", "2016-04-28", "2016-03-26", dict(line, LongTermDebtNoncurrent=2.002e9)),
        ("10-Q", "2016-04-28", "2015-12-26", dict(line, LongTermDebtNoncurrent=2.007e9)))
    assert XBRLParser().extract_quarterly_financials(facts)["2016-03-26"]["short_term_debt"] == pytest.approx(0.230e9)


def _hwm_fy2019():
    """HWM's FY2019: its FY2019 10-K shows $1.034B of short-term debt beside $1.003B of maturities (LongTermDebt
    $4.906B + $1.003B), which nothing in it decides; its FY2020 10-K restates the date, the borrowings its maturities
    ($1.034B each, LongTermDebt $4.906B + $1.034B)."""
    first = dict(ShortTermBorrowings=1.034e9, LongTermDebtCurrent=1.003e9, LongTermDebtNoncurrent=4.906e9,
                 LongTermDebt=5.909e9, LongTermDebtAndCapitalLeaseObligations=4.906e9)
    return _with_later_10k(_debt_filing(**first), "2027-02-16", **{**first, "LongTermDebtCurrent": 1.034e9,
                                                                   "LongTermDebt": 5.940e9})


def test_a_later_10ks_borrowings_that_are_its_maturities_outrank_an_earlier_10ks_combination():
    # I-3: the first 10-K's combination, a DebtCurrent, outranked the later 10-K's borrowings, so the year and its
    # year end read $6.943B for $5.940B. The latest filing that shows the borrowings and the maturities decides a date.
    facts = _hwm_fy2019()
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-12-31"]
    assert [_total_debt(p) for p in (_debt_year(facts), quarter)] == [pytest.approx(5.940e9)] * 2


def test_a_later_10ks_verdict_doesnt_reach_an_earlier_10ks_vintage():
    # A vintage is its own filing's view as known then: the FY2019 10-K's leaves its borrowings and maturities apart.
    assert _total_debt(_vintage(_hwm_fy2019(), "2026-02-12")) == pytest.approx(6.943e9)


def test_a_later_10k_that_shows_the_borrowings_alone_leaves_the_combination():
    # ABT's FY2021: its FY2021 and FY2022 10-Ks show $0 of borrowings and $0.754B of maturities, its FY2023 10-K the
    # borrowings alone, which says nothing of the maturities: total debt $18.050B, not the noncurrent $17.296B.
    facts = _debt_filing(ShortTermBorrowings=0.0, LongTermDebtCurrent=0.754e9, LongTermDebtNoncurrent=17.296e9)
    facts = _with_later_10k(facts, "2027-02-17", ShortTermBorrowings=0.0, LongTermDebtCurrent=0.754e9,
                            LongTermDebtNoncurrent=17.296e9)
    facts = _with_later_10k(facts, "2028-02-16", ShortTermBorrowings=0.0)
    assert _total_debt(_debt_year(facts)) == pytest.approx(18.050e9)


def _coo_fy2016(**changed):
    """COO's FY2016 as its FY2017 10-K shows it: current liabilities of $543.2M are its payables $107.4M, accrued
    compensation $77.7M, other $131.8M and $226.3M of short-term debt, exactly; $210.7M of maturities."""
    return _debt_filing(**{**dict(ShortTermBorrowings=0.2263e9, LinesOfCreditCurrent=0.0171e9,
                                  LongTermDebtCurrent=0.2107e9, LongTermDebtNoncurrent=1.1074e9,
                                  LiabilitiesCurrent=0.5432e9, AccountsPayableCurrent=0.1074e9,
                                  EmployeeRelatedLiabilitiesCurrent=0.0777e9, OtherLiabilitiesCurrent=0.1318e9),
                           **changed})


def test_borrowings_the_current_liabilities_cant_hold_beside_the_maturities_hold_them():
    # I-3: nothing in BUG-159's rules decided COO's lines, so they were combined: $1.544B for $1.334B. With the
    # maturities as a line of their own, the borrowings, its payables and its accrued compensation would already exceed
    # its current liabilities (the current-liabilities bound).
    assert _total_debt(_debt_year(_coo_fy2016())) == pytest.approx(1.3337e9)


def test_payables_tagged_above_the_current_liabilities_are_no_part_of_them():
    # A tagging error a thousand times too large (BUG-061's kind) is no evidence the borrowings hold the maturities.
    year = _debt_year(_coo_fy2016(AccountsPayableCurrent=107.4e9))
    assert year["short_term_debt"] == pytest.approx(0.4370e9)


def test_lines_within_rounding_of_the_current_liabilities_dont_decide():
    # The bound needs the lines to exceed the current liabilities beyond rounding (0.5%).
    year = _debt_year(_coo_fy2016(LiabilitiesCurrent=0.6200e9))  # the lines make $0.6221B: 0.34% over
    assert year["short_term_debt"] == pytest.approx(0.4370e9)


def test_payables_with_accruals_beside_the_borrowings_and_maturities_tell_the_borrowings_hold_them():
    # YUM's FY2018 as its FY2019 10-K shows it: current liabilities of $1.301B are its payables and other liabilities
    # $911M, income taxes $69M and $321M of short-term borrowings, exactly; its note's maturities are $331M
    # (borrowings net of issuance costs). The note's payables ($202M) and compensation ($206M) are parts of the $911M.
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.321e9, LongTermDebtCurrent=0.331e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=0.331e9,
                                   LongTermDebtAndCapitalLeaseObligations=9.751e9, LiabilitiesCurrent=1.301e9,
                                   AccountsPayableAndAccruedLiabilitiesCurrent=0.911e9, AccountsPayableCurrent=0.202e9,
                                   EmployeeRelatedLiabilitiesCurrent=0.206e9, AccruedIncomeTaxesCurrent=0.069e9))
    assert _total_debt(year) == pytest.approx(10.072e9)  # $10.403B with the maturities again


def test_a_notes_parts_of_the_payables_with_accruals_dont_add_to_them():
    # BAX's FY2009 as its FY2010 10-K shows it: current liabilities of $4.464B are its payables and accrued liabilities
    # $3.753B, $682M of maturities and $29M of borrowings, exactly; the note's trade payables ($807M), compensation
    # ($494M) and income taxes ($375M) are parts of the $3.753B, so they can't be added to it: two lines.
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.029e9, LongTermDebtAndCapitalLeaseObligationsCurrent=0.682e9,
                                   LongTermDebtAndCapitalLeaseObligations=3.440e9, LiabilitiesCurrent=4.464e9,
                                   AccountsPayableAndAccruedLiabilitiesCurrent=3.753e9,
                                   AccountsPayableTradeCurrent=0.807e9, EmployeeRelatedLiabilitiesCurrent=0.494e9,
                                   AccruedIncomeTaxesCurrent=0.375e9))
    assert year["short_term_debt"] == pytest.approx(0.711e9)


def test_income_taxes_beside_the_payables_with_accruals_count_in_the_bound():
    # YUM's Q3 2024 10-Q: current liabilities of $1.186B are $1.138B of payables and other liabilities, $24M of income
    # taxes and $24M of short-term borrowings; the note's maturities are $26M.
    facts = _with_10q({"facts": {"us-gaap": {}}}, "2024-11-07", "2024-09-30", ShortTermBorrowings=0.024e9,
                      LongTermDebtCurrent=0.026e9, LongTermDebtAndCapitalLeaseObligations=11.169e9,
                      LiabilitiesCurrent=1.186e9, AccountsPayableAndAccruedLiabilitiesCurrent=1.138e9,
                      AccruedIncomeTaxesCurrent=0.024e9)
    assert XBRLParser().extract_quarterly_financials(facts)["2024-09-30"]["short_term_debt"] == pytest.approx(0.024e9)


_PEP_FY2010 = ("10-K", "2011-02-18", "2010-12-25", dict(ShortTermBorrowings=4.898e9, LongTermDebtCurrent=0.113e9,
                                                        LongTermDebtNoncurrent=19.999e9))


def test_a_quarters_current_liabilities_tell_its_borrowings_hold_the_maturities():
    # I-4, PEP's Q2 2026 10-Q: $10.602B of short-term borrowings beside a note's $1.6B of maturities, and current
    # liabilities of $35.106B that are its payables and accruals ($24.504B) and the borrowings, exactly. Nothing in its
    # 10-Ks decided it (they tag the line as DebtCurrent since FY2011), so the quarter took its FY2010 10-K's undecided
    # entry as "separate", 5,649 days old: $12.202B (18 quarters from 2020, +$0.75B to +$3.2B each).
    facts = _filings(_PEP_FY2010, ("10-Q", "2026-07-09", "2026-06-13", dict(
        ShortTermBorrowings=10.602e9, LongTermDebtCurrent=1.6e9, CommercialPaper=6.1e9, LiabilitiesCurrent=35.106e9,
        AccountsPayableAndAccruedLiabilitiesCurrent=24.504e9)))
    assert XBRLParser().extract_quarterly_financials(facts)["2026-06-13"]["short_term_debt"] == 10.602e9


def test_a_quarter_whose_comparative_borrowings_are_its_annual_reports_debt_current_holds_the_maturities():
    # I-4: PEP's FY2025 10-K tags its short-term obligations, $6.861B, as DebtCurrent; its 10-Qs tag the same
    # year-end line as ShortTermBorrowings, so their borrowings are that total, maturities and all.
    facts = _filings(_PEP_FY2010, ("10-K", "2026-02-03", "2025-12-27", dict(DebtCurrent=6.861e9)),
                     ("10-Q", "2026-07-09", "2025-12-27", dict(ShortTermBorrowings=6.861e9)),
                     ("10-Q", "2026-07-09", "2026-06-13", dict(ShortTermBorrowings=10.602e9,
                                                               LongTermDebtCurrent=1.6e9)))
    assert XBRLParser().extract_quarterly_financials(facts)["2026-06-13"]["short_term_debt"] == 10.602e9


_ATO_FY2018 = ("10-K", "2018-11-13", "2018-09-30", dict(ShortTermBorrowings=0.57578e9, LongTermDebtCurrent=0.575e9,
                                                        LongTermDebtNoncurrent=2.493665e9))


def test_borrowings_below_the_maturities_dont_take_an_annual_reports_word_that_they_hold_them():
    # I-4, ATO's Q1 FY2019 10-Q: no short-term borrowings beside $575M of maturities; its FY2018 10-K's borrowings are
    # within 1% of its maturities, which the quarter took as holding them: its maturities were lost.
    facts = _filings(_ATO_FY2018, ("10-Q", "2019-02-05", "2018-12-31", dict(
        ShortTermBorrowings=0.0, LongTermDebtCurrent=0.575e9, LongTermDebtNoncurrent=3.084779e9)))
    assert XBRLParser().extract_quarterly_financials(facts)["2018-12-31"]["short_term_debt"] == 0.575e9


def test_a_quarter_takes_only_its_latest_annual_reports_word():
    # I-4, ATO's Q2 FY2020 10-Q: $199.9M of borrowings beside $0.131M of maturities. Its FY2019 10-K shows no
    # maturities and so says nothing; the quarter took its FY2018 10-K's word instead.
    facts = _filings(_ATO_FY2018, ("10-K", "2019-11-12", "2019-09-30", dict(
        ShortTermBorrowings=0.464915e9, LongTermDebtCurrent=0.0, LongTermDebtNoncurrent=3.529452e9)),
        ("10-Q", "2020-05-06", "2020-03-31", dict(ShortTermBorrowings=0.199923e9,
                                                  LongTermDebtAndCapitalLeaseObligationsCurrent=0.000131e9)))
    assert XBRLParser().extract_quarterly_financials(facts)["2020-03-31"]["short_term_debt"] == 0.200054e9


def test_borrowings_a_rounding_below_the_maturities_take_the_annual_reports_word():
    # BE's Q3 2025 10-Q: $1.4M of short-term borrowings, a tenth of a million, beside $1.424M of maturities; its FY2024
    # 10-K's $114.4M of borrowings are its maturities ($114.385M).
    facts = _filings(("10-K", "2025-02-27", "2024-12-31", dict(
        ShortTermBorrowings=0.1144e9, LongTermDebtCurrent=0.114385e9, LongTermDebtNoncurrent=1.014407e9)),
        ("10-Q", "2025-10-28", "2025-09-30", dict(
            ShortTermBorrowings=0.0014e9, LongTermDebtCurrent=0.001424e9, LongTermDebtNoncurrent=1.130892e9)))
    assert XBRLParser().extract_quarterly_financials(facts)["2025-09-30"]["short_term_debt"] == 0.0014e9


def test_a_10qs_year_end_column_takes_the_annual_report_on_that_date():
    # GEHC's 2025-12-31: its FY2025 10-K shows $508M of borrowings beside $502M of maturities, 1.2% apart, which
    # nothing decides; its Q1 2026 10-Q's comparative column the same. The checkpoint reads the date as the annual
    # record does, not by the FY2024 10-K's borrowings, which were within 1% of its maturities.
    facts = _filings(("10-K", "2025-02-13", "2024-12-31", dict(ShortTermBorrowings=1.502e9, LongTermDebtCurrent=1.500e9,
                                                               LongTermDebtNoncurrent=7.449e9)),
                     ("10-K", "2026-02-04", "2025-12-31", dict(ShortTermBorrowings=0.508e9, LongTermDebtCurrent=0.502e9,
                                                               LongTermDebtNoncurrent=9.495e9)),
                     ("10-Q", "2026-04-29", "2025-12-31", dict(ShortTermBorrowings=0.508e9, LongTermDebtCurrent=0.502e9,
                                                               LongTermDebtNoncurrent=9.495e9)))
    parser = XBRLParser()
    year = parser.extract_annual_financials(facts)["2025"]
    quarter = parser.extract_quarterly_financials(facts)["2025-12-31"]
    assert [p["short_term_debt"] for p in (year, quarter)] == [pytest.approx(1.010e9)] * 2


def test_a_later_10k_doesnt_change_an_earlier_10ks_vintage():
    # I-5, XEL's FY2024 10-K: $0.695B of borrowings and $1.103B of maturities beside $27.316B of noncurrent debt; its
    # FY2025 10-K tags the same noncurrent line as LongTermDebt, a total BUG-159 keeps, which withheld the first 10-K's
    # maturities from its own vintage: $29.114B as known on 2025-02-27, $28.011B once the later 10-K was filed.
    first = ("10-K", "2025-02-27", "2024-12-31", dict(LongTermDebtAndCapitalLeaseObligations=27.316e9,
                                                      LongTermDebtAndCapitalLeaseObligationsCurrent=1.103e9,
                                                      ShortTermBorrowings=0.695e9))
    later = ("10-K", "2026-02-25", "2024-12-31", dict(LongTermDebt=27.316e9,
                                                      LongTermDebtAndCapitalLeaseObligationsCurrent=1.103e9,
                                                      ShortTermBorrowings=0.695e9))
    then, now = (XBRLParser().extract_annual_vintages(_filings(*filings))["2024"]["2025-02-27"]
                 for filings in ((first,), (first, later)))
    assert [_total_debt(then), _total_debt(now)] == [pytest.approx(29.114e9)] * 2


# BUG-267 (code review 21, M-1): BUG-159's measured boundaries.

def test_a_notes_total_a_little_above_the_balance_sheets_lines_gives_way():
    # ABBV's FY2019: LongTermDebt $67.233B, 0.75% above the balance sheet's $62.975B noncurrent and $3.753B current
    # (its own total $66.728B): within _MADE_UP's 5% the total gives way (NRG's notes run 1-3% above its lines).
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.0, LongTermDebt=67.233e9,
                                   LongTermDebtAndCapitalLeaseObligations=62.975e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=3.753e9))
    assert _total_debt(year) == pytest.approx(66.728e9)


def test_a_narrow_noncurrent_figure_doesnt_replace_the_total():
    # OMC's FY2011: LongTermDebt $3.161B beside its notes without the convertibles, $2.524B (LongTermNotesPayable),
    # $9.5M of borrowings and $0.7M of maturities: 80% of the total is a note's figure, so the total stands.
    year = _debt_year(_debt_filing(LongTermDebt=3.1607e9, LongTermNotesPayable=2.5235e9, LongTermDebtCurrent=0.0007e9,
                                   ShortTermBorrowings=0.0095e9))
    assert _total_debt(year) == pytest.approx(3.1702e9)


def test_borrowings_just_below_the_maturities_are_a_line_of_their_own():
    # AEE's FY2019: $440M of short-term debt beside $442M of current maturities, two lines; the 1% margin is one-sided.
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.440e9, LongTermDebtCurrent=0.442e9,
                                   LongTermDebtNoncurrent=8.915e9))
    assert year["short_term_debt"] == pytest.approx(0.882e9)


def test_debt_and_lease_obligations_that_are_the_noncurrent_figure_say_nothing_of_the_maturities():
    # CEG's FY2024: DebtAndCapitalLeaseObligations is its noncurrent $7.384B alone, which can't tell that its $0 of
    # borrowings holds $1.028B of maturities: total debt $8.412B.
    year = _debt_year(_debt_filing(ShortTermBorrowings=0.0, LongTermDebtCurrent=1.028e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=1.028e9,
                                   LongTermDebtNoncurrent=7.384e9, DebtAndCapitalLeaseObligations=7.384e9))
    assert _total_debt(year) == pytest.approx(8.412e9)


def test_a_total_that_is_the_noncurrent_figure_keeps_no_maturities():
    # ODFL's FY2016: LongTermDebt and LongTermDebtAndCapitalLeaseObligations are both its $105.0M noncurrent line, so
    # its maturities ($0) are read beside them.
    year = _debt_year(_debt_filing(LongTermDebt=0.104975e9, LongTermDebtAndCapitalLeaseObligations=0.104975e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=0.0))
    assert _short_term_debt(year) == (0.0, "LongTermDebtAndCapitalLeaseObligationsCurrent")


def test_a_10qs_total_keeps_no_year_ends_maturities():
    # ADBE's FY2014: its 10-Ks show $911.1M of noncurrent debt and $603.2M of maturities; a later 10-Q tags the year
    # end's noncurrent line as LongTermDebt, which no annual record reads: total debt $1.514B, not $0.911B.
    facts = _with_10q(_debt_filing(LongTermDebtAndCapitalLeaseObligations=0.911086e9,
                                   LongTermDebtAndCapitalLeaseObligationsCurrent=0.603229e9),
                      "2026-09-23", LongTermDebt=0.911086e9, LongTermDebtAndCapitalLeaseObligationsCurrent=0.603229e9)
    assert _total_debt(_debt_year(facts)) == pytest.approx(1.514315e9)


def test_a_total_beside_a_later_10ks_noncurrent_figure_keeps_no_year_end():
    # PNR's FY2010: its FY2010 10-K's LongTermDebt total, $702.5M, beside $4.9M of borrowings; its FY2011 10-K tags
    # the noncurrent figure, the borrowings and $18K of maturities, and its own total $707.472M.
    facts = _with_later_10k(_debt_filing(LongTermDebt=0.702521e9, LongTermDebtCurrent=0.000018e9,
                                         ShortTermBorrowings=0.004933e9),
                            "2027-02-21", LongTermDebtNoncurrent=0.702521e9, LongTermDebtCurrent=0.000018e9,
                            ShortTermBorrowings=0.004933e9, DebtLongtermAndShorttermCombinedAmount=0.707472e9)
    assert _total_debt(_debt_year(facts)) == pytest.approx(0.707472e9)


# BUG-171 (build M1-4): a 10-Q's long-term debt under a concept the field doesn't map.

_INCLUDING = "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"


def _balance_sheets(*filings):
    """``_filings`` with total assets on each balance sheet, as every filing tags them, so each date is a checkpoint."""
    return _filings(*((form, filed, end, {"Assets": 200.0e9, **tagged}) for form, filed, end, tagged in filings))


def _quarter_debt(facts, end):
    """The quarterly checkpoint's long-term debt on *end* as (value, tag)."""
    quarter = XBRLParser().extract_quarterly_financials(facts)[end]
    return quarter.get("long_term_debt"), quarter["_source_tags"].get("long_term_debt")


def test_a_10qs_long_term_debt_under_another_concept_is_its_quarters():
    # ORCL's 10-Qs tag their noncurrent notes as LongTermNotesAndLoans, its 10-Ks as LongTermNotesPayable, so every
    # quarter had no long-term debt: $117.712B at 2026-08-31. The 10-Q's year-end column shows the 10-K's figure under
    # that concept, which makes it the 10-Q's long-term debt line.
    facts = _balance_sheets(
        ("10-K", "2026-06-22", "2026-05-31", dict(LongTermNotesPayable=122.342e9, DebtCurrent=7.199e9)),
        ("10-Q", "2026-09-11", "2026-05-31", dict(LongTermNotesAndLoans=122.342e9, NotesPayableCurrent=7.199e9)),
        ("10-Q", "2026-09-11", "2026-08-31", dict(LongTermNotesAndLoans=117.712e9, NotesPayableCurrent=7.625e9)))
    assert _quarter_debt(facts, "2026-08-31") == (117.712e9, "LongTermNotesAndLoans")


def test_a_10q_concept_whose_year_end_figure_isnt_the_10ks_is_no_line():
    # TMUS's 10-Qs tag their long-term debt only under their own concept, which the SEC's company facts don't carry:
    # their finance leases are no stand-in for the $81.147B the year end shows.
    facts = _balance_sheets(
        ("10-K", "2026-02-11", "2025-12-31", dict(LongTermDebtNoncurrent=81.147e9, ShortTermBorrowings=5.135e9)),
        ("10-Q", "2026-04-28", "2025-12-31", dict(FinanceLeaseLiabilityNoncurrent=1.107e9,
                                                  ShortTermBorrowings=5.135e9)),
        ("10-Q", "2026-04-28", "2026-03-31", dict(FinanceLeaseLiabilityNoncurrent=1.024e9,
                                                  ShortTermBorrowings=2.238e9)))
    assert _quarter_debt(facts, "2026-03-31") == (None, None)


@pytest.mark.parametrize("noncurrent", ["LongTermDebtAndCapitalLeaseObligations", "LongTermDebtNoncurrent"])
def test_a_10q_line_the_10k_shows_without_its_current_maturities_is_read_beside_short_term_debt(noncurrent):
    # CVX's 10-Qs tag the balance sheet's long-term debt as LongTermDebtAndCapitalLeaseObligationsIncludingCurrent-
    # Maturities; its FY2024 10-K tags the same $20.135B as LongTermDebtAndCapitalLeaseObligations (any noncurrent
    # concept alike) beside $4.012B of current maturities its short-term debt holds, so the line holds none. Q1 2025's
    # total debt read $4.076B, its short-term debt alone, where its balance sheet has $29.681B.
    facts = _balance_sheets(
        ("10-K", "2025-02-21", "2024-12-31", {noncurrent: 20.135e9, _INCLUDING: 20.135e9,
                                              "DebtCurrent": 4.406e9, "LongTermDebtCurrent": 4.012e9}),
        ("10-Q", "2025-05-08", "2024-12-31", {_INCLUDING: 20.135e9, "ShortTermBorrowings": 4.406e9}),
        ("10-Q", "2025-05-08", "2025-03-31", {_INCLUDING: 25.605e9, "ShortTermBorrowings": 4.076e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-03-31"]
    assert (quarter.get("long_term_debt"), _total_debt(quarter)) == (25.605e9, pytest.approx(29.681e9))


def test_a_10q_line_that_can_hold_the_current_maturities_isnt_read_beside_short_term_debt():
    # Kenvue's 10-Qs tag the same concept as a total: its 2024-09-29 figure, $7.803B, holds $750M of maturities its
    # $1.648B of DebtCurrent holds too (its own total $8.701B). Its year end showed no maturities, and its 10-K's figure
    # there is a total (LongTermDebt), so nothing tells the line apart from a total: unknown rather than $0.75B high.
    facts = _balance_sheets(
        ("10-K", "2024-03-01", "2023-12-31", dict(LongTermDebt=7.687e9, DebtCurrent=0.599e9, LongTermDebtCurrent=0.0,
                                                  CommercialPaper=0.600e9)),
        ("10-Q", "2024-11-07", "2023-12-31", {_INCLUDING: 7.687e9, "DebtCurrent": 0.599e9, "LongTermDebtCurrent": 0.0}),
        ("10-Q", "2024-11-07", "2024-09-29", {_INCLUDING: 7.803e9, "DebtCurrent": 1.648e9,
                                              "LongTermDebtCurrent": 0.750e9}))
    assert _quarter_debt(facts, "2024-09-29") == (None, None)


def test_a_10q_line_beside_a_10k_total_isnt_read_beside_short_term_debt():
    # FICO's FY2014 10-K tags its senior notes, $447M with $71M due within the year, as LongTermDebt beside the
    # maturities; its 10-Qs tag the same $447M as SeniorNotes. Beside the Q1 FY2015 10-Q's $231M of DebtCurrent, which
    # holds the $71M, the line would count them twice.
    facts = _balance_sheets(
        ("10-K", "2014-11-10", "2014-09-30", dict(LongTermDebt=0.447e9, DebtCurrent=0.170e9,
                                                  LongTermDebtCurrent=0.071e9)),
        ("10-Q", "2015-01-29", "2014-09-30", dict(SeniorNotes=0.447e9, DebtCurrent=0.170e9)),
        ("10-Q", "2015-01-29", "2014-12-31", dict(SeniorNotes=0.447e9, DebtCurrent=0.231e9)))
    assert _quarter_debt(facts, "2014-12-31") == (None, None)


def test_notes_that_came_due_within_the_year_arent_read_beside_short_term_debt():
    # SWKS's 10-Q of 2011-07-01: its convertible notes ($25M, noncurrent at the year end, where no maturities show: 0
    # here) are now its DebtCurrent; the 10-Q tags them as ConvertibleNotesPayable in both columns.
    facts = _balance_sheets(
        ("10-K", "2010-11-24", "2010-10-01", dict(ConvertibleLongTermNotesPayable=0.025e9, DebtCurrent=0.050e9,
                                                  LongTermDebtCurrent=0.0)),
        ("10-Q", "2011-08-09", "2010-10-01", dict(ConvertibleNotesPayable=0.025e9, DebtCurrent=0.050e9)),
        ("10-Q", "2011-08-09", "2011-07-01", dict(ConvertibleNotesPayable=0.026e9, DebtCurrent=0.026e9)))
    assert _quarter_debt(facts, "2011-07-01") == (None, None)


def test_a_10q_line_a_noncurrent_concept_is_read_beside_short_term_debt():
    # ORCL's Q2 FY2017 10-Q: LongTermNotesAndLoans, a noncurrent concept, beside $3.838B of DebtCurrent; its FY2016 10-K
    # shows no maturities apart (its notes due within the year are its DebtCurrent).
    facts = _balance_sheets(
        ("10-K", "2016-06-22", "2016-05-31", dict(LongTermNotesPayable=40.105e9, DebtCurrent=3.750e9)),
        ("10-Q", "2016-12-19", "2016-05-31", dict(LongTermNotesAndLoans=40.105e9, DebtCurrent=3.750e9)),
        ("10-Q", "2016-12-19", "2016-11-30", dict(LongTermNotesAndLoans=50.489e9, DebtCurrent=3.838e9)))
    assert _quarter_debt(facts, "2016-11-30") == (50.489e9, "LongTermNotesAndLoans")


def test_a_10q_line_named_noncurrent_is_read_beside_short_term_debt():
    # Block's Q2 2022 10-Q: OtherLongTermDebtNoncurrent beside $460M of current maturities (its FY2021 10-K's $455K of
    # maturities, left out here, also show the line holds none).
    facts = _balance_sheets(
        ("10-K", "2022-02-24", "2021-12-31", dict(LongTermDebtNoncurrent=4.559208e9)),
        ("10-Q", "2022-08-04", "2021-12-31", dict(OtherLongTermDebtNoncurrent=4.559208e9)),
        ("10-Q", "2022-08-04", "2022-06-30", dict(OtherLongTermDebtNoncurrent=4.104664e9,
                                                  LongTermDebtCurrent=0.459984e9)))
    assert _quarter_debt(facts, "2022-06-30") == (4.104664e9, "OtherLongTermDebtNoncurrent")


def test_a_10q_line_beside_no_short_term_debt_is_read():
    # TYL's FY2025 10-K: its $599.7M of convertible notes, due in 2026, as LongTermDebt; its Q2 2026 10-Q tags them as
    # ConvertibleDebt at the year end, and its new $1.409B notes so on 2026-06-30, beside short-term debt of 0 (none
    # tagged, in the filing).
    facts = _balance_sheets(
        ("10-K", "2026-02-18", "2025-12-31", dict(LongTermDebt=0.599663e9, LoansPayableCurrent=0.599663e9)),
        ("10-Q", "2026-07-29", "2025-12-31", dict(ConvertibleDebt=0.599663e9, DebtCurrent=0.599663e9)),
        ("10-Q", "2026-07-29", "2026-06-30", dict(ConvertibleDebt=1.408691e9, DebtCurrent=0.0)))
    assert _quarter_debt(facts, "2026-06-30") == (1.408691e9, "ConvertibleDebt")


def test_a_10q_line_matches_the_figure_an_annual_report_filed():
    # IDXX's 10-Qs of 2016 show the year end's $597.085M as SecuredLongTermDebt, as its FY2015 10-K did (LongTermDebt);
    # its FY2016 10-K restated the year end as $597.1M, the figure the annual record reads.
    facts = _balance_sheets(
        ("10-K", "2016-02-17", "2015-12-31", dict(LongTermDebt=0.597085e9)),
        ("10-K", "2017-02-17", "2015-12-31", dict(LongTermDebt=0.5971e9)),
        ("10-Q", "2016-04-29", "2015-12-31", dict(SecuredLongTermDebt=0.597085e9)),
        ("10-Q", "2016-04-29", "2016-03-31", dict(SecuredLongTermDebt=0.600021e9)))
    assert _quarter_debt(facts, "2016-03-31") == (0.600021e9, "SecuredLongTermDebt")


def test_a_year_end_without_long_term_debt_carries_no_line():
    # NVDA's FY2016 10-K tagged its $1.413B of convertible notes, all due within the year, as LongTermDebt; its FY2017
    # 10-K restated the year end's long-term debt as 0, which the annual record reads. A 10-Q line that is the first
    # figure continues no long-term debt of the record's.
    facts = _balance_sheets(
        ("10-K", "2016-03-17", "2016-01-31", dict(LongTermDebt=1.413e9)),
        ("10-K", "2017-03-01", "2016-01-31", dict(LongTermDebt=0.0)),
        ("10-Q", "2016-05-25", "2016-01-31", dict(ConvertibleDebt=1.413e9)),
        ("10-Q", "2016-05-25", "2016-05-01", dict(ConvertibleDebt=1.421e9)))
    assert _quarter_debt(facts, "2016-05-01") == (None, None)


def test_a_10qs_comparative_isnt_the_annual_reports_figure():
    # The line carries the figure a 10-K filed: a 10-Q that restates the year end (its LongTermDebtNoncurrent there,
    # $650M for the 10-K's $600M) and tags the same $650M as SeniorNotes leaves the quarter to its own concepts.
    facts = _balance_sheets(
        ("10-K", "2026-02-12", "2025-12-31", dict(LongTermDebtNoncurrent=0.600e9)),
        ("10-Q", "2026-04-28", "2025-12-31", dict(LongTermDebtNoncurrent=0.650e9, SeniorNotes=0.650e9)),
        ("10-Q", "2026-04-28", "2026-03-31", dict(SeniorNotes=0.700e9)))
    assert _quarter_debt(facts, "2026-03-31") == (None, None)


def test_a_zero_an_annual_report_filed_is_no_figure_to_match():
    # A 10-K's zero for the year end (restated by the next 10-K to $1.0B) isn't a long-term debt figure: a concept the
    # 10-Q shows at 0 there carries no line.
    facts = _balance_sheets(
        ("10-K", "2026-02-12", "2025-12-31", dict(LongTermDebt=0.0)),
        ("10-K", "2027-02-11", "2025-12-31", dict(LongTermDebt=1.0e9)),
        ("10-Q", "2026-04-28", "2025-12-31", dict(ConvertibleDebtNoncurrent=0.0)),
        ("10-Q", "2026-04-28", "2026-03-31", dict(ConvertibleDebtNoncurrent=1.2e9)))
    assert _quarter_debt(facts, "2026-03-31") == (None, None)


def test_a_figure_near_the_year_ends_isnt_its_line():
    # The 10-Q's year-end figure must be the annual report's: $601.0M beside $600.0M (0.17% apart) is another figure.
    facts = _balance_sheets(
        ("10-K", "2026-02-12", "2025-12-31", dict(LongTermDebtNoncurrent=0.600e9)),
        ("10-Q", "2026-04-28", "2025-12-31", dict(SeniorNotes=0.601e9)),
        ("10-Q", "2026-04-28", "2026-03-31", dict(SeniorNotes=0.601e9)))
    assert _quarter_debt(facts, "2026-03-31") == (None, None)


@pytest.mark.parametrize("tag", [
    "SeniorNotesCurrent",  # GPC's 10-Qs: the notes due within the year, as large as the noncurrent ones
    "ShortTermBankLoansAndNotesPayable",  # short-term debt's own
    "DebtLongtermAndShorttermCombinedAmount",  # VTR's 10-Qs: its own total, beside its commercial paper
    "DebtAndCapitalLeaseObligations",  # the filer's debt and leases
    "DebtInstrumentCarryingAmount",  # often one instrument's (BUG-125)
    "LongTermDebtFairValue",  # a note's fair value, at par at the year end
    "Goodwill",  # no debt at all
])
def test_a_figure_that_isnt_a_long_term_debt_line_isnt_read(tag):
    facts = _balance_sheets(
        ("10-K", "2026-02-12", "2025-12-31", dict(LongTermDebtNoncurrent=0.250e9)),
        ("10-Q", "2026-04-28", "2025-12-31", {tag: 0.250e9}),
        ("10-Q", "2026-04-28", "2026-03-31", {tag: 0.300e9}))
    assert _quarter_debt(facts, "2026-03-31") == (None, None)


def test_lines_that_disagree_on_the_quarter_are_no_line():
    # Two concepts carry the year end's figure but part on the quarter's: which is the line, the filing doesn't say.
    facts = _balance_sheets(
        ("10-K", "2026-02-12", "2025-12-31", dict(LongTermDebtNoncurrent=0.600e9)),
        ("10-Q", "2026-04-28", "2025-12-31", dict(SeniorNotes=0.600e9, OtherLongTermDebt=0.600e9)),
        ("10-Q", "2026-04-28", "2026-03-31", dict(SeniorNotes=0.600e9, OtherLongTermDebt=0.900e9)))
    assert _quarter_debt(facts, "2026-03-31") == (None, None)


def test_the_latest_10q_showing_the_quarter_decides_it():
    # TGT's 10-Qs show the year-earlier quarter too: its Q1 FY2016 10-Q restates 2015-05-02's line, which reads as the
    # checkpoints read every figure, the latest filing's.
    facts = _balance_sheets(
        ("10-K", "2015-03-13", "2015-01-31", dict(LongTermDebtNoncurrent=12.705e9)),
        ("10-K", "2016-03-11", "2016-01-30", dict(LongTermDebtNoncurrent=11.945e9)),
        ("10-Q", "2015-06-05", "2015-01-31", dict(OtherLongTermDebtNoncurrent=12.705e9)),
        ("10-Q", "2015-06-05", "2015-05-02", dict(OtherLongTermDebtNoncurrent=12.585e9)),
        ("10-Q", "2016-06-08", "2016-01-30", dict(OtherLongTermDebtNoncurrent=11.945e9)),
        ("10-Q", "2016-06-08", "2015-05-02", dict(OtherLongTermDebtNoncurrent=12.600e9)))
    assert _quarter_debt(facts, "2015-05-02") == (12.600e9, "OtherLongTermDebtNoncurrent")


def test_a_quarters_own_long_term_debt_concept_outranks_a_line():
    # The checkpoints' own reading stands: the 10-Q's LongTermDebt, though its year-end figure isn't the 10-K's and
    # another concept's is.
    facts = _balance_sheets(
        ("10-K", "2026-02-12", "2025-12-31", dict(LongTermDebtNoncurrent=0.600e9)),
        ("10-Q", "2026-04-28", "2025-12-31", dict(LongTermDebt=0.650e9, SeniorNotes=0.600e9)),
        ("10-Q", "2026-04-28", "2026-03-31", dict(LongTermDebt=0.700e9, SeniorNotes=0.640e9)))
    assert _quarter_debt(facts, "2026-03-31") == (0.700e9, "LongTermDebt")


def test_a_10ks_year_end_reads_no_line():
    # A 10-K that tags its debt only under another concept (AIG's FY2010 10-K, OtherLongTermDebt at both year ends)
    # leaves its year without long-term debt, and the year end's checkpoint stays the annual record's.
    facts = _balance_sheets(
        ("10-K", "2010-02-26", "2009-12-31", dict(LongTermDebt=113.298e9)),
        ("10-K", "2011-02-24", "2009-12-31", dict(OtherLongTermDebt=113.298e9)),
        ("10-K", "2011-02-24", "2010-12-31", dict(OtherLongTermDebt=85.476e9)))
    assert XBRLParser().extract_annual_financials(facts)["2010"].get("long_term_debt") is None
    assert _quarter_debt(facts, "2010-12-31") == (None, None)


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


EQUIVALENTS = "CashEquivalentsAtCarryingValue"


def _cash(period):
    """A period's cash as (value, tag)."""
    return period.get("cash_and_equivalents"), period.get("_source_tags", {}).get("cash_and_equivalents")


def _cash_records(facts, filed="2026-02-12"):
    """FY2025's cash in the annual record, the year end's quarterly checkpoint and the vintage filed on *filed*."""
    parser = XBRLParser()
    return [_cash(record) for record in (parser.extract_annual_financials(facts).get("2025", {}),
                                         parser.extract_quarterly_financials(facts).get("2025-12-31", {}),
                                         parser.extract_annual_vintages(facts).get("2025", {}).get(filed, {}))]


def test_a_notes_cash_equivalents_give_way_to_the_balance_sheets_total():
    # BUG-161 (code review 14): PG tags its balance sheet's cash line with the total that includes restricted cash
    # (FY2025: $9.556B; it has none) and its fair-value note's cash equivalents, $8.3B of it, as
    # CashEquivalentsAtCarryingValue, which ranked first: FY2020-26 read the part (net debt $1.3B high in FY2025).
    facts = _debt_filing(**{RESTRICTED: 9.556e9, EQUIVALENTS: 8.3e9})
    assert _cash_records(facts) == [(9.556e9, RESTRICTED)] * 3


def test_a_later_10ks_note_doesnt_replace_the_years_total():
    # BRK-B's balance sheet shows cash by segment only; its one consolidated figure is the total, $36.399B for FY2022
    # with $0.588B of restricted cash. Its FY2023 10-K adds a note's $2.6B of cash equivalents for FY2022, which the
    # year read (FY2022-25), beside the FY2018-21 totals BUG-139 fills. Restricted cash the total holds isn't the rest
    # of it: $33.2B is cash.
    facts = _debt_filing(**{RESTRICTED: 36.399e9, "RestrictedCashAndCashEquivalents": 0.588e9})
    facts = _with_later_10k(facts, "2027-02-26", **{RESTRICTED: 36.399e9, EQUIVALENTS: 2.6e9,
                                                    "RestrictedCashAndCashEquivalents": 0.588e9})
    parser = XBRLParser()
    assert [_cash(parser.extract_annual_financials(facts)["2025"]),
            _cash(parser.extract_annual_vintages(facts)["2025"]["2027-02-26"])] == [(36.399e9, RESTRICTED)] * 2


def test_a_cash_lines_smallest_part_gives_way_to_a_later_10ks_total():
    # AXP's FY2017 10-K tags its balance sheet's cash line, $32.927B, as CashCashEquivalentsAndFederalFundsSold, which
    # cash doesn't read, over three parts: cash and due from banks, interest-bearing deposits in other banks, and
    # $0.070B of short-term investment securities it tags CashEquivalentsAtCarryingValue, which the year read; its
    # FY2018 10-K tags FY2017 with the total, the line and $0.336B of restricted cash.
    lines = {"CashCashEquivalentsAndFederalFundsSold": 32.927e9, "CashAndDueFromBanks": 5.148e9,
             "InterestBearingDepositsInBanks": 27.709e9, "RestrictedCashAndCashEquivalents": 0.336e9}
    facts = _with_later_10k(_debt_filing(**{EQUIVALENTS: 0.070e9, **lines}), "2027-02-12",
                            **{RESTRICTED: 33.263e9, **lines})
    assert _cash(_debt_year(facts)) == (33.263e9, RESTRICTED)


@pytest.mark.parametrize("tagged, line", [
    # ICE's FY2020 10-K tags its balance sheet's cash line as CashEquivalentsAtCarryingValue (its 10-Qs, the same
    # figures as CashAndCashEquivalentsAtCarryingValue), beside short- and long-term restricted cash and their total;
    # the cash flow statement's total adds them.
    ({EQUIVALENTS: 0.583e9, "RestrictedCashAndCashEquivalentsAtCarryingValue": 1.000e9,
      "RestrictedCashAndCashEquivalentsNoncurrent": 0.408e9, "RestrictedCashAndCashEquivalents": 1.408e9,
      RESTRICTED: 1.991e9}, 0.583e9),
    # from its FY2021 10-K the total adds the clearing houses' margin deposits too: $147.976B for $0.607B of cash
    ({EQUIVALENTS: 0.607e9, "RestrictedCashAndCashEquivalentsAtCarryingValue": 1.035e9,
      "RestrictedCashAndCashEquivalentsNoncurrent": 0.398e9, "RestrictedCashAndCashEquivalents": 1.433e9,
      "DepositsAssetsCurrent": 145.936e9, RESTRICTED: 147.976e9}, 0.607e9),
    # figures rounded to the million can miss their total by one
    ({EQUIVALENTS: 0.841e9, "RestrictedCashAndCashEquivalentsAtCarryingValue": 0.943e9,
      "RestrictedCashAndCashEquivalentsNoncurrent": 0.404e9, RESTRICTED: 2.189e9}, 0.841e9),
])
def test_equivalents_the_total_adds_restricted_cash_to_are_the_cash_line(tagged, line):
    # Read before the equivalents everywhere, the total made ICE's FY2020-21 cash $83.6B and $148.0B, where its
    # balance sheets show $0.583B and $0.607B.
    assert _cash_records(_debt_filing(**tagged)) == [(line, EQUIVALENTS)] * 3


def test_a_total_with_no_equivalents_beside_it_stays_the_cash():
    # CPRT's FY2021 10-K tags its $1.048B cash line as the total and as restricted cash too: with no equivalents
    # beside them, the total is the only cash figure there is.
    facts = _debt_filing(**{RESTRICTED: 1.04826e9, "RestrictedCashAndCashEquivalentsAtCarryingValue": 1.04826e9})
    assert _cash_records(facts) == [(1.04826e9, RESTRICTED)] * 3


@pytest.mark.parametrize("tagged, cash", [
    # BBY's FY2021 10-K: its $2.936B line (its current assets add up with it), a note's $1.584B of cash equivalents,
    # and a $3.205B total whose $0.269B of restricted cash it tags nowhere.
    ({"CashAndCashEquivalentsAtCarryingValue": 2.936e9, EQUIVALENTS: 1.584e9, RESTRICTED: 3.205e9},
     (2.936e9, "CashAndCashEquivalentsAtCarryingValue")),
    # HIG's FY2024 10-K: its $0.183B of cash, and a total that is it and $0.051B of restricted cash.
    ({"Cash": 0.183e9, "RestrictedCash": 0.051e9, RESTRICTED: 0.234e9}, (0.183e9, "Cash")),
])
def test_the_balance_sheets_own_cash_line_still_outranks_the_total(tagged, cash):
    # Read before CashAndCashEquivalentsAtCarryingValue, the total would replace 1,986 years' cash line at 287 filers;
    # before Cash, ES's, HIG's, PGR's and VRT's.
    assert _year_end_cash(**tagged) == cash


@pytest.mark.parametrize("tagged", [
    # BUG-260: TGT's 10-Ks tag their balance sheet's cash line ($4.762B for FY2025) as the total and as cash with its
    # short-term investments, and a note's $0.276B of Cash, a part of it, which ranked first: net debt $4.5B high.
    {"Cash": 0.276e9, "ShortTermInvestments": 3.893e9, "CashCashEquivalentsAndShortTermInvestments": 4.762e9,
     RESTRICTED: 4.762e9},
    # CF's 10-Ks tag the line ($1.614B for FY2024) as the total and in the fair-value note, beside $0.168B of Cash.
    {"Cash": 0.168e9, "CashAndCashEquivalentsFairValueDisclosure": 1.614e9, RESTRICTED: 1.614e9},
    # GEN's FY2025 10-Qs: $0.462B of Cash and $0.544B of cash equivalents, the $1.006B the total is.
    {"Cash": 0.462e9, EQUIVALENTS: 0.544e9, RESTRICTED: 1.006e9},
])
def test_cash_a_total_without_restricted_cash_holds_gives_way_to_it(tagged):
    assert _cash_records(_debt_filing(**tagged)) == [(tagged[RESTRICTED], RESTRICTED)] * 3


def _cf_fy2024():
    """CF's FY2023 10-K (the line tagged as the total and in the fair-value note) and a 10-Q whose two columns tag only
    Cash and the total, as CF's have since 2021."""
    return _balance_sheets(
        ("10-K", "2024-02-22", "2023-12-31", {"Cash": 0.208e9, "CashAndCashEquivalentsFairValueDisclosure": 2.032e9,
                                              RESTRICTED: 2.032e9}),
        ("10-Q", "2024-11-01", "2023-12-31", {"Cash": 0.208e9, RESTRICTED: 2.032e9}),
        ("10-Q", "2024-11-01", "2024-09-30", {"Cash": 0.331e9, RESTRICTED: 1.877e9}))


def test_a_10qs_cash_an_earlier_filing_shows_a_part_gives_way_to_the_total():
    # CF's 10-Qs tag no figure that tells the total from restricted cash; the year-end column's figures are the 10-K's,
    # whose total held none: Cash is a part in both columns of the 10-Q's balance sheet. Q3 2024 read $0.331B of
    # $1.877B.
    assert _cash(XBRLParser().extract_quarterly_financials(_cf_fy2024())["2024-09-30"]) == (1.877e9, RESTRICTED)


def test_an_earlier_filings_line_shows_a_10ks_cash_to_be_a_part():
    # KLAC's FY2020 10-K tags Cash ($0.480B and $0.461B) and the total ($1.016B and $1.234B) only; its FY2019 10-K
    # tagged FY2019's $1.016B as CashAndCashEquivalentsAtCarryingValue beside the same Cash.
    facts = _balance_sheets(
        ("10-K", "2019-08-02", "2019-06-30", {"CashAndCashEquivalentsAtCarryingValue": 1.016e9, "Cash": 0.480e9}),
        ("10-K", "2020-08-07", "2019-06-30", {"Cash": 0.480e9, RESTRICTED: 1.016e9}),
        ("10-K", "2020-08-07", "2020-06-30", {"Cash": 0.461e9, RESTRICTED: 1.234e9}))
    assert _cash(XBRLParser().extract_annual_vintages(facts)["2020"]["2020-08-07"]) == (1.234e9, RESTRICTED)


def test_a_later_filings_line_doesnt_reach_an_earlier_vintage():
    # As of its filing day the FY2020 10-K's Cash is all there is to tell (review 21's look-ahead rule); the year itself
    # reads the later 10-K's line.
    facts = _balance_sheets(
        ("10-K", "2020-08-07", "2019-06-30", {"Cash": 0.480e9, RESTRICTED: 1.016e9}),
        ("10-K", "2020-08-07", "2020-06-30", {"Cash": 0.461e9, RESTRICTED: 1.234e9}),
        ("10-K", "2021-08-06", "2020-06-30", {"CashAndCashEquivalentsAtCarryingValue": 1.234e9, "Cash": 0.461e9}))
    parser = XBRLParser()
    assert _cash(parser.extract_annual_vintages(facts)["2020"]["2020-08-07"]) == (0.461e9, "Cash")
    assert _cash(parser.extract_annual_financials(facts)["2020"]) == (1.234e9, "CashAndCashEquivalentsAtCarryingValue")


def test_cash_beside_a_total_with_restricted_cash_stays_the_line():
    # ES's balance sheets show Cash ($0.107B for FY2020) and hold restricted cash in other lines it tags nowhere; the
    # total ($0.265B) adds it. From its 2022 10-Qs ES tags CashAndCashEquivalentsAtCarryingValue too, Cash and the
    # equivalents ($0.375B for FY2022), which the total ($0.522B) is not.
    facts = _balance_sheets(
        ("10-K", "2023-02-15", "2022-12-31", {"CashAndCashEquivalentsAtCarryingValue": 0.3746e9, "Cash": 0.0476e9,
                                              EQUIVALENTS: 0.3270e9, RESTRICTED: 0.5218e9}),
        ("10-Q", "2023-05-05", "2022-12-31", {"Cash": 0.0476e9, RESTRICTED: 0.5218e9}),
        ("10-Q", "2023-05-05", "2023-03-31", {"Cash": 0.0360e9, RESTRICTED: 0.1628e9}))
    assert _cash(XBRLParser().extract_quarterly_financials(facts)["2023-03-31"]) == (0.0360e9, "Cash")
    assert _year_end_cash(Cash=0.1066e9, **{RESTRICTED: 0.2650e9}) == (0.1066e9, "Cash")


def test_cash_that_is_its_line_stays_the_line():
    # EG's FY2018 10-K tags its cash line ($0.656B) as Cash and as CashAndCashEquivalentsAtCarryingValue, its 10-Qs as
    # Cash and the total: Cash isn't a part of a line it agrees with.
    facts = _balance_sheets(
        ("10-K", "2019-03-01", "2018-12-31", {"CashAndCashEquivalentsAtCarryingValue": 0.6561e9, "Cash": 0.6561e9}),
        ("10-Q", "2019-05-10", "2018-12-31", {"Cash": 0.6561e9, RESTRICTED: 0.6561e9}),
        ("10-Q", "2019-05-10", "2019-03-31", {"Cash": 0.5840e9, RESTRICTED: 0.5840e9}))
    assert _cash(XBRLParser().extract_quarterly_financials(facts)["2019-03-31"]) == (0.5840e9, "Cash")


def test_an_instruments_carrying_amount_is_not_the_years_debt():
    # DebtInstrumentCarryingAmount is often one instrument's, on days with no balance sheet: SLB's $0.5B note at
    # 2020-12-31 beside some $16B of debt, BSX's and HWM's debt-only quarters that cost them seven trailing years.
    assert _year_end_debt(DebtInstrumentCarryingAmount=0.5e9) == (None, None)


DEPRECIATION, INTANGIBLES, AMORTIZATION = "Depreciation", "AmortizationOfIntangibleAssets", "AdjustmentForAmortization"
DDA, DA = "DepreciationDepletionAndAmortization", "DepreciationAndAmortization"


def _da_year(filing=_K10, **tagged):
    """A 10-K's FY2025 cash flow statement holding the given tags (tag=value)."""
    return {"facts": {"us-gaap": {tag: {"units": {"USD": [usd(val, "2025-01-01", "2025-12-31", **filing)]}}
                                  for tag, val in tagged.items()}}}


def _da(period):
    """A period's D&A as (value, tag)."""
    return period.get("depreciation_amortization"), period.get("_source_tags", {}).get("depreciation_amortization")


def _da_records(facts, filed="2026-02-12"):
    """FY2025's D&A in the annual record and in the vintage filed on *filed*."""
    parser = XBRLParser()
    return [_da(parser.extract_annual_financials(facts).get("2025", {})),
            _da(parser.extract_annual_vintages(facts).get("2025", {}).get(filed, {}))]


def test_the_depreciation_and_the_amortization_of_intangibles_apart_are_the_years_d_and_a():
    # BUG-170 (build iteration 5, M1-4): D&A read the first of four totals, else Depreciation, and AbbVie's cash flow
    # statement shows its depreciation ($762M for FY2025) and its amortization of intangible assets ($7.377B) on lines
    # of their own, with no total: D&A was the depreciation alone (88 filers at 715 years), and EBITDA $15.84B, not
    # $23.21B.
    facts = _da_year(OperatingIncomeLoss=15075e6, **{DEPRECIATION: 762e6, INTANGIBLES: 7377e6})
    assert _da_records(facts) == [(8139e6, DA)] * 2
    assert CalculatedMetrics().calculate_all(XBRLParser().extract_annual_financials(facts)["2025"])["ebitda"] == 23214e6


@pytest.mark.parametrize("tagged, da", [
    # INTU's FY2025: the cash flow statement's amortization, $637M, is that of the acquired technology in cost of
    # revenue too; the other acquired intangibles' alone ($481M) are AmortizationOfIntangibleAssets.
    ({DEPRECIATION: 172e6, INTANGIBLES: 481e6, AMORTIZATION: 637e6}, 809e6),
    # BRO's FY2025: the amortization tagged only as the cash flow statement's.
    ({DEPRECIATION: 55e6, AMORTIZATION: 312e6}, 367e6),
])
def test_the_cash_flow_statements_amortization_comes_first(tagged, da):
    # Where a filing tags both, its own total of other years is the depreciation and the cash flow statement's
    # figure in 308 filing-years, and the intangibles' in 116.
    assert _da_records(_da_year(**tagged)) == [(da, DA)] * 2


def _da_quarters(*rungs):
    """Year-to-date rungs, each (start, end, form, filed, {tag: value}), as a filer's 10-Qs and 10-Ks tag them, with
    a balance sheet on each end; the quarters' and the trailing years' D&A by end."""
    us_gaap = {"Assets": {"units": {"USD": []}}}
    for start, end, form, filed, tagged in rungs:
        filing = dict(fy=int(filed[:4]), fp="FY" if form == "10-K" else "Q", form=form, filed=filed)
        us_gaap["Assets"]["units"]["USD"].append(usd(1e12, None, end, **filing))
        for tag, val in tagged.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, start, end, **filing))
    quarters = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": us_gaap}})
    ttm = compute_ttm(quarters)
    return ({end: q.get("depreciation_amortization") for end, q in sorted(quarters.items())},
            {end: t.get("depreciation_amortization") for end, t in sorted(ttm.items())})


def test_a_years_quarters_and_trailing_years_read_the_10qs_parts_together():
    # AbbVie's 10-Qs tag the depreciation and the amortization year to date too: its quarters were the depreciation
    # alone, and the trailing year to Q1 2025 $762M of $8.351B.
    s24, s25 = "2024-01-01", "2025-01-01"
    quarters, ttm = _da_quarters(
        (s24, "2024-03-31", "10-Q", "2024-05-03", {DEPRECIATION: 183e6, INTANGIBLES: 1891e6}),
        (s24, "2024-06-30", "10-Q", "2024-08-02", {DEPRECIATION: 367e6, INTANGIBLES: 3838e6}),
        (s24, "2024-09-30", "10-Q", "2024-11-01", {DEPRECIATION: 558e6, INTANGIBLES: 5726e6}),
        (s24, "2024-12-31", "10-K", "2025-02-14", {DEPRECIATION: 764e6, INTANGIBLES: 7622e6}),
        (s25, "2025-03-31", "10-Q", "2025-05-02", {DEPRECIATION: 181e6, INTANGIBLES: 1858e6}))
    assert quarters == {"2024-03-31": 2074e6, "2024-06-30": 2131e6, "2024-09-30": 2079e6, "2024-12-31": 2102e6,
                        "2025-03-31": 2039e6}
    assert ttm == {"2024-12-31": 8386e6, "2025-03-31": 8351e6}


@pytest.mark.parametrize("ends, to_date, year_end, quarters", [
    # DHR's FY2022: its 10-Qs tag the depreciation and the amortization, its 10-K the total ($2.132B, the two
    # together): the fourth quarter was the total less nine months of depreciation alone, $1.595B.
    (("2022-04-01", "2022-07-01", "2022-09-30", "2022-12-31"),
     ((179e6, 386e6), (358e6, 759e6), (537e6, 1120e6)), {DDA: 2132e6, DEPRECIATION: 698e6, INTANGIBLES: 1434e6},
     (565e6, 552e6, 540e6, 475e6)),
    # CME's FY2025: its 10-K tags its depreciation line as the total ($107.5M) beside $223.4M of amortization, so the
    # fourth quarter would go below zero beside 10-Qs that read both.
    (("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"),
     ((27e6, 55e6), (55e6, 111e6), (81e6, 168e6)), {DA: 107.5e6, INTANGIBLES: 223.4e6}, (82e6, 84e6, 83e6, 81.9e6)),
])
def test_a_fourth_quarter_reads_the_10k_on_the_10qs_basis(ends, to_date, year_end, quarters):
    start = ends[0][:4] + "-01-01"
    rungs = [(start, end, "10-Q", f"{end[:4]}-{int(end[5:7]) + 1:02d}-25", {DEPRECIATION: d, INTANGIBLES: a})
             for end, (d, a) in zip(ends, to_date)]
    found, _ = _da_quarters(*rungs, (start, ends[3], "10-K", f"{int(ends[3][:4]) + 1}-02-20", year_end))
    assert found == dict(zip(ends, quarters))


@pytest.mark.parametrize("tagged, da", [
    # CME's FY2025 10-K tags its depreciation line DepreciationAndAmortization ($107.5M) beside $223.4M of
    # amortization of purchased intangibles, and no Depreciation.
    ({DA: 107.5e6, INTANGIBLES: 223.4e6}, 330.9e6),
    # EME's FY2023: its depreciation line tagged as the total and as the depreciation ($51.8M), the amortization of
    # identifiable intangible assets ($67.1M) apart.
    ({DDA: 51.8e6, DEPRECIATION: 51.8e6, INTANGIBLES: 67.1e6}, 118.9e6),
    # GPN's FY2023: a $1,141.9M "total" beside $458.2M of depreciation and $1,318.5M of amortization.
    ({DDA: 1141.9e6, DEPRECIATION: 458.2e6, INTANGIBLES: 1318.5e6}, 1776.7e6),
    # NDSN's FY2025: the total tagged with the amortization's figure ($79.3M), beside $71.3M of depreciation.
    ({DDA: 79.3e6, DEPRECIATION: 71.3e6, INTANGIBLES: 79.3e6}, 150.6e6),
])
def test_a_total_no_larger_than_the_amortization_beside_it_is_the_depreciation_line(tagged, da):
    # A total can't be smaller than a part it holds: such a "total" is the filing's depreciation line under a total's
    # tag, its amortization on a line of its own (Yahoo's D&A is the two in all 35 such years it covers).
    assert _da_records(_da_year(**tagged)) == [(da, DA)] * 2


def test_of_two_totals_the_one_that_can_hold_the_amortization_is_read():
    # MCK's FY2025 10-K: DepreciationDepletionAndAmortization is its depreciation line ($272M),
    # DepreciationAndAmortization the cash flow statement's total ($636M), with $394M of amortization: the
    # first-ranked tag read $272M.
    tagged = {DDA: 272e6, DA: 636e6, AMORTIZATION: 394e6, INTANGIBLES: 226e6}
    assert _da_records(_da_year(**tagged)) == [(636e6, DA)] * 2


@pytest.mark.parametrize("tagged, da", [
    # DHR's FY2023: the total ($2.166B) is its depreciation ($675M) and amortization ($1.491B), each tagged too.
    ({DDA: 2166e6, DEPRECIATION: 675e6, INTANGIBLES: 1491e6}, (2166e6, DDA)),
    # BBY's FY2025: its total tagged as the depreciation too ($866M), the $21M of amortization inside it.
    ({DDA: 866e6, DEPRECIATION: 866e6, INTANGIBLES: 21e6}, (866e6, DDA)),
    # FDX's FY2025: the total beside the amortization, no depreciation figure.
    ({DDA: 4264e6, INTANGIBLES: 48e6}, (4264e6, DDA)),
    # A total tagged DepreciationAndAmortization above the depreciation and amortization beside it ($738M, $508M and
    # $120M: NOW's FY2025, whose total holds other amortization too).
    ({DA: 738e6, DEPRECIATION: 508e6, INTANGIBLES: 120e6}, (738e6, DA)),
])
def test_a_total_that_can_hold_the_amortization_stays_the_years_d_and_a(tagged, da):
    # The amortization a filer tags beside its total is mostly a note's figure inside it (4,644 years at 372 filers):
    # added to the total, it counted twice.
    assert _da_records(_da_year(**tagged)) == [da] * 2


@pytest.mark.parametrize("tagged, da", [
    # GOOGL's FY2025: its depreciation alone; the finance leases' amortization is a note's.
    ({DEPRECIATION: 21136e6, "FinanceLeaseRightOfUseAssetAmortization": 553e6}, (21136e6, DEPRECIATION)),
    # EXPE's FY2017: its depreciation holds the capitalized software's amortization ($398M), the intangibles' apart.
    ({DEPRECIATION: 614e6, INTANGIBLES: 275e6, "CapitalizedComputerSoftwareAmortization1": 398e6}, (889e6, DA)),
    # A negative figure under the amortization's tag is no amortization (F's FY2017, -$669M).
    ({DEPRECIATION: 9241e6, INTANGIBLES: -669e6}, (9241e6, DEPRECIATION)),
    # The amortization alone is no D&A (27 filers' 173 years: banks', insurers').
    ({"OperatingIncomeLoss": 900e6, INTANGIBLES: 34.4e6}, (None, None)),
])
def test_only_the_amortization_of_intangibles_joins_the_depreciation(tagged, da):
    # Other amortization is inside the depreciation at one filer and apart at another, so none is added.
    assert _da_records(_da_year(**tagged)) == [da] * 2


@pytest.mark.parametrize("earlier, later, da", [
    # HUM's FY2018 10-K tags its depreciation also as the total ($444M), beside $90M of amortization; its FY2019 10-K
    # tags the two apart.
    ({DA: 444e6, DEPRECIATION: 444e6, INTANGIBLES: 90e6}, {DEPRECIATION: 444e6, INTANGIBLES: 90e6}, 534e6),
    # and the other way about: a later filing's total restates an earlier filing's two figures
    ({DEPRECIATION: 410e6, INTANGIBLES: 75e6}, {DA: 480e6, DEPRECIATION: 410e6, INTANGIBLES: 75e6}, 480e6),
])
def test_a_later_filings_d_and_a_restates_an_earlier_ones(earlier, later, da):
    # The sum is the filing's DepreciationAndAmortization, so the latest filing's figure for the year stands, as for any
    # restated figure: INTC's FY2018-19 read a $200M "total" its later 10-Ks replace with $9.085B and $10.826B.
    facts = _da_year(**earlier)
    for tag, val in later.items():
        facts["facts"]["us-gaap"].setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(
            usd(val, "2025-01-01", "2025-12-31", fy=2026, fp="FY", form="10-K", filed="2027-02-18"))
    assert _da(XBRLParser().extract_annual_financials(facts)["2025"]) == (da, DA)


DAAN = "DepreciationAmortizationAndAccretionNet"


def test_a_total_that_holds_the_intangibles_amortization_stays_beside_a_broader_cash_flow_figure():
    # BUG-299 (code review 23): ALL's FY2021 10-K tags a $1.086B total, $411M of depreciation, $376M of amortization
    # of intangibles and a cash flow statement's $1.281B of amortization (more than the intangibles'): the total was
    # set aside against the broader figure, and the vintage read $1.692B.
    tagged = {DAAN: 1086e6, DEPRECIATION: 411e6, AMORTIZATION: 1281e6, INTANGIBLES: 376e6}
    assert _da_records(_da_year(**tagged)) == [(1086e6, DAAN)] * 2


@pytest.mark.parametrize("tagged", [
    # SWK's FY2023 10-K: its depreciation line ($432.4M) under the first-ranked tag, the total of it and the $192.7M of
    # amortization ($625.1M) under another; the cash flow statement's amortization ($49.1M) is another's, inside it.
    {DDA: 432.4e6, DA: 625.1e6, DEPRECIATION: 383.3e6, AMORTIZATION: 49.1e6, INTANGIBLES: 192.7e6},
    {DDA: 432.4e6, DA: 625.1e6, INTANGIBLES: 192.7e6},
])
def test_a_total_the_filing_shows_to_be_its_depreciation_line_is_set_aside(tagged):
    # BUG-274: a total larger than the amortization beside it was read as holding it. One that, with the amortization,
    # makes another total of the filing is that total's part.
    assert _da_records(_da_year(**tagged)) == [(625.1e6, DDA)] * 2


def test_a_net_add_back_below_the_depreciation_stays():
    # BMY's FY2016 cash flow statement adds back "Depreciation and amortization, net", $382M, below its $448M of
    # depreciation and beside $178M of amortization, and below its 10-Qs' nine months of the two: it is no gross total,
    # but nothing shows the tag to carry a line, so it is the statement's own add-back. A total of the filing that
    # holds the two shows it a line (BA's FY2009: $1.066B beside $1.459B, $207M and a $1.678B total).
    q3 = ("2025-01-01", "2025-09-30", "10-Q", "2025-10-25", {DEPRECIATION: 330e6, INTANGIBLES: 133e6})
    year = ("2025-01-01", "2025-12-31", "10-K", "2026-02-12")
    facts = _da_filings(q3, (*year, {DDA: 382e6, DEPRECIATION: 448e6, INTANGIBLES: 178e6}))
    assert _da_records(facts) == [(382e6, DDA)] * 2
    facts = _da_filings((*year, {DDA: 1066e6, DA: 1678e6, DEPRECIATION: 1459e6, INTANGIBLES: 207e6}))
    assert _da_records(facts) == [(1678e6, DDA)] * 2


def _da_filings(*filings):
    """A filer's facts from filings, each (start, end, form, filed, {tag: value}); a period's revenue, the same in
    every filing unless given, puts the filings on one basis."""
    us_gaap = {"Assets": {"units": {"USD": []}}}
    for start, end, form, filed, tagged in filings:
        filing = dict(fy=int(filed[:4]), fp="FY" if form == "10-K" else "Q", form=form, filed=filed)
        us_gaap["Assets"]["units"]["USD"].append(usd(1e12, None, end, **filing))
        for tag, val in {"Revenues": 1e11, **tagged}.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, start, end, **filing))
    return {"facts": {"us-gaap": us_gaap}}


@pytest.mark.parametrize("earlier, later, da", [
    # MRSH's FY2023: its first 10-K tags a $713M total, $370M of depreciation and $343M of amortization; its later
    # 10-Ks tag the depreciation line as the total ($370M) beside the same amortization.
    ({DDA: 713e6, DEPRECIATION: 370e6, INTANGIBLES: 343e6}, {DDA: 370e6, DEPRECIATION: 370e6, INTANGIBLES: 343e6},
     713e6),
    # HD's FY2023: its first 10-K's total ($2.975B) is a later 10-K's first-ranked line ($2.796B) and its $179M of
    # amortization; that 10-K's other total ($2.455B, the income statement's) is smaller than the two.
    ({DDA: 2975e6}, {DDA: 2796e6, DA: 2455e6, INTANGIBLES: 179e6}, 2975e6),
])
def test_an_earlier_filings_total_shows_a_later_ones_line(earlier, later, da):
    year = ("2025-01-01", "2025-12-31", "10-K")
    facts = _da_filings((*year, "2026-02-12", earlier), (*year, "2027-02-18", later))
    parser = XBRLParser()
    assert parser.extract_annual_financials(facts)["2025"]["depreciation_amortization"] == da
    assert parser.extract_annual_vintages(facts)["2025"]["2027-02-18"]["depreciation_amortization"] == da


def test_an_earlier_filings_total_on_another_basis_shows_nothing():
    # HLT's 2016: its first report's $509M (Park Hotels and Hilton Grand Vacations inside) is its later restated
    # $273M and $234M of amortization by chance; the revenue shows the two on other bases.
    year = ("2016-01-01", "2016-12-31", "10-K")
    facts = _da_filings((*year, "2017-02-15", {DA: 509e6, "Revenues": 11.7e9}),
                        (*year, "2018-02-14", {DA: 273e6, INTANGIBLES: 234e6, "Revenues": 7.0e9}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2016"]) == (273e6, DA)


def test_the_combination_stands_over_an_earlier_filings_figure_as_any_restatement():
    # BEN's FY2019: its 10-K of 2020 tags its depreciation line ($78.7M) under the first-ranked tag beside $14.7M of
    # amortization, together its first 10-K's $93.4M total; entered under that tag, the later figure stands, not the
    # first 10-K's $83.2M under it.
    year = ("2018-10-01", "2019-09-30", "10-K")
    facts = _da_filings((*year, "2019-11-12", {DDA: 83.2e6, DA: 93.4e6, INTANGIBLES: 14.7e6}),
                        (*year, "2020-11-23", {DDA: 78.7e6, DA: 83.2e6, INTANGIBLES: 14.7e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2019"]) == (93.4e6, DDA)


_HD_SHOWN = [("2022-01-31", "2023-01-29", "10-K", "2023-03-15", {DDA: 2975e6}),
             ("2022-01-31", "2023-01-29", "10-K", "2025-03-21", {DDA: 2796e6, DA: 2455e6, INTANGIBLES: 179e6})]
_HD_FY2025 = ("2024-01-29", "2025-02-02", "10-K")


def test_a_filings_line_shown_in_one_year_is_its_line_in_the_others():
    # HD's 10-K of March 2025 tags its depreciation line as the total for FY2023 ($2.796B, its first 10-K's $2.975B
    # less $179M of amortization) and for FY2025 ($3.336B beside $425M), where nothing else shows it.
    year = (*_HD_FY2025, "2025-03-21", {DDA: 3336e6, DA: 3034e6, INTANGIBLES: 425e6})
    parser = XBRLParser()
    facts = _da_filings(*_HD_SHOWN, year)
    assert _da(parser.extract_annual_financials(facts)["2025"]) == (3761e6, DDA)
    assert _da(parser.extract_annual_vintages(facts)["2025"]["2025-03-21"]) == (3761e6, DDA)
    alone = _da_filings(year)  # the year alone: nothing shows its line
    assert _da(parser.extract_annual_financials(alone)["2025"]) == (3336e6, DDA)


@pytest.mark.parametrize("year, tag", [
    ((*_HD_FY2025, "2025-03-21", {DAAN: 3336e6, INTANGIBLES: 425e6}), DAAN),  # the filing's other tag
    ((*_HD_FY2025, "2026-03-18", {DDA: 3336e6, INTANGIBLES: 425e6}), DDA),  # the next 10-K, which shows nothing
    ((*_HD_FY2025, "2025-03-21", {DDA: 3336e6, DEPRECIATION: 3000e6, INTANGIBLES: 425e6}), DDA),  # not its depreciation
])
def test_only_the_filings_own_line_is_shown_so(year, tag):
    # Nothing shows these totals to be lines: they stay.
    assert _da(XBRLParser().extract_annual_financials(_da_filings(*_HD_SHOWN, year))["2025"]) == (3336e6, tag)


_HUM_SHOWN = [("2022-01-01", "2022-12-31", "10-K", "2023-02-16", {DA: 845e6}),
              ("2022-01-01", "2022-12-31", "10-K", "2024-02-16", {DA: 709e6, INTANGIBLES: 136e6})]


def test_a_total_below_the_depreciation_is_a_line_where_the_filer_tags_one_there():
    # HUM's 10-K of 2024 tags FY2022's DepreciationAndAmortization as $709M, its first 10-K's $845M less $136M of
    # amortization: the tag carries a line, so FY2024's $839M beside $908M of depreciation is that line, not a net
    # add-back.
    facts = _da_filings(*_HUM_SHOWN, ("2024-01-01", "2024-12-31", "10-K", "2025-02-19",
                                      {DA: 839e6, DEPRECIATION: 908e6, AMORTIZATION: 60e6, INTANGIBLES: 60e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2024"]) == (968e6, DA)
    assert _da(XBRLParser().extract_annual_vintages(facts)["2024"]["2025-02-19"]) == (968e6, DA)
    # a 10-K filed before the filings showed the line is read as filed
    early = _da_filings(*_HUM_SHOWN, ("2023-01-01", "2023-12-31", "10-K", "2024-02-15",
                                      {DA: 779e6, DEPRECIATION: 850e6, INTANGIBLES: 67e6}))
    assert _da(XBRLParser().extract_annual_vintages(early)["2023"]["2024-02-15"]) == (779e6, DA)


def test_a_total_below_the_depreciation_shows_no_line_in_the_filings_other_years():
    # A total below its depreciation, in a filing whose tag the filings show to carry a line elsewhere, is that line
    # ($19.3M + $4.5M); it shows nothing of the filing's other years: FY2024's $48.4M beside $17.4M stays.
    facts = _da_filings(("2022-01-01", "2022-12-31", "10-K", "2023-02-28", {DDA: 24.4e6}),
                        ("2022-01-01", "2022-12-31", "10-K", "2024-02-27", {DDA: 20.4e6, INTANGIBLES: 4.0e6}),
                        ("2023-01-01", "2023-12-31", "10-K", "2025-02-28",
                         {DDA: 19.3e6, DEPRECIATION: 28.1e6, INTANGIBLES: 4.5e6}),
                        ("2024-01-01", "2024-12-31", "10-K", "2025-02-28", {DDA: 48.4e6, INTANGIBLES: 17.4e6}))
    annual = XBRLParser().extract_annual_financials(facts)
    assert _da(annual["2023"]) == (32.6e6, DDA)
    assert _da(annual["2024"]) == (48.4e6, DDA)


def test_a_restatement_the_size_of_a_small_amortization_shows_no_line():
    # DAL's 10-K of February 2019 restates FY2016's total from $1.902B to $1.886B; its $17M of amortization makes up
    # the difference only by chance. In one filing the two totals do show the line (DVA's FY2022 $721.1M and $732.6M,
    # with $11.5M).
    year = ("2016-01-01", "2016-12-31", "10-K")
    facts = _da_filings((*year, "2017-02-13", {DDA: 1902e6}), (*year, "2019-02-15", {DDA: 1886e6, INTANGIBLES: 17e6}))
    assert _da(XBRLParser().extract_annual_vintages(facts)["2016"]["2019-02-15"]) == (1886e6, DDA)
    assert _da_records(_da_year(**{DDA: 721.1e6, DA: 732.6e6, INTANGIBLES: 11.5e6})) == [(732.6e6, DDA)] * 2


def test_a_larger_total_holds_a_line_known_from_another_year():
    # SBUX's 10-K of November 2018: FY2017's income-statement D&A ($1.011B) and $57.5M of amortization make its cash
    # flow statement's $1.067B, so that tag is a line; FY2018's $1.247B under it stands beside the cash flow
    # statement's $1.306B, which holds it, not beside $1.433B.
    filed = ("10-K", "2018-11-16")
    facts = _da_filings(("2016-10-03", "2017-10-01", *filed, {DDA: 1067.1e6, DA: 1011.4e6, INTANGIBLES: 57.5e6}),
                        ("2017-10-02", "2018-09-30", *filed, {DDA: 1305.9e6, DA: 1247e6, INTANGIBLES: 186.5e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2018"]) == (1305.9e6, DDA)


def test_against_the_intangibles_figure_a_total_holds_it_only_beside_the_depreciation():
    # CSGP's 2012 six months: $4.7M under the first-ranked tag beside $4.5M of depreciation, the cash flow statement's
    # $7.0M of amortization and $4.2M of the intangibles': no total exceeds $7.0M, and $4.7M is the depreciation line,
    # not a total of it and the $4.2M (ALL's $1.086B beside $411M and $376M is).
    assert _da_records(_da_year(**{DDA: 4.7e6, DEPRECIATION: 4.5e6, AMORTIZATION: 7.0e6, INTANGIBLES: 4.2e6})) == [
        (11.5e6, DA)] * 2


def test_a_years_10qs_are_read_so_only_where_its_10k_is():
    # PSTG's FY2022: its nine months' $47.2M and $12.4M make the 10-Q's $59.6M, but the 10-K's $65.9M beside $16.8M
    # isn't its $83.2M: read alone, the nine months would leave the fourth quarter $6.3M of the 10-K's line.
    facts = _da_filings(("2025-01-01", "2025-09-30", "10-Q", "2025-10-30",
                         {DDA: 47.2e6, DA: 59.6e6, INTANGIBLES: 12.4e6}),
                        ("2025-01-01", "2025-12-31", "10-K", "2026-02-12",
                         {DDA: 65.9e6, DA: 83.2e6, INTANGIBLES: 16.8e6}))
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert quarters["2025-12-31"]["depreciation_amortization"] == pytest.approx(18.7e6)
    # the year's latest 10-K decides, as the quarters read it
    facts = _da_filings(("2025-01-01", "2025-09-30", "10-Q", "2025-10-30",
                         {DDA: 47.2e6, DA: 59.6e6, INTANGIBLES: 12.4e6}),
                        ("2025-01-01", "2025-12-31", "10-K", "2026-02-12",
                         {DDA: 65.9e6, DA: 82.7e6, INTANGIBLES: 16.8e6}),
                        ("2025-01-01", "2025-12-31", "10-K", "2027-02-12",
                         {DDA: 65.0e6, DA: 83.2e6, INTANGIBLES: 16.8e6}))
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert quarters["2025-12-31"]["depreciation_amortization"] == pytest.approx(17.8e6)


_SWK_2025 = [("2025-01-01", "2025-03-31", "10-Q", "2025-04-30", {DDA: 91.1e6, DA: 128.4e6, INTANGIBLES: 37.3e6}),
             ("2025-01-01", "2025-12-31", "10-K", "2026-02-24", {DDA: 365.6e6, DA: 512.4e6, INTANGIBLES: 146.8e6})]


def test_a_year_with_no_10k_yet_is_read_as_the_last_10k_was():
    # BUG-332 (code review 28, I1): SWK's 10-Q of April 2026 tags $113.0M = its $84.4M line + $28.6M of amortization;
    # FY2026 has no 10-K yet, and the gate left the quarter at the line beside FY2025's quarters on the 10-K's total:
    # the trailing year mixed the two ($440.1M for $496.3M).
    q1 = ("2026-01-01", "2026-03-31", "10-Q", "2026-04-29", {DDA: 84.4e6, DA: 113.0e6, INTANGIBLES: 28.6e6})
    quarters = XBRLParser().extract_quarterly_financials(_da_filings(*_SWK_2025, q1))
    assert quarters["2025-03-31"]["depreciation_amortization"] == pytest.approx(128.4e6)
    assert quarters["2026-03-31"]["depreciation_amortization"] == pytest.approx(113.0e6)


def test_a_year_with_no_10k_yet_stays_on_the_last_10ks_line_basis():
    # The last 10-K reads its line ($65.0M beside $16.8M makes no $83.2M), so the next year's 10-Q stays on it too,
    # though its own figures make a total.
    before = ("2024-01-01", "2024-12-31", "10-K", "2025-02-12", {DDA: 60.0e6, DA: 75.0e6, INTANGIBLES: 15.0e6})
    year = ("2025-01-01", "2025-12-31", "10-K", "2026-02-12", {DDA: 65.0e6, DA: 83.2e6, INTANGIBLES: 16.8e6})
    q1 = ("2026-01-01", "2026-03-31", "10-Q", "2026-04-29", {DDA: 16.0e6, DA: 20.2e6, INTANGIBLES: 4.2e6})
    quarters = XBRLParser().extract_quarterly_financials(_da_filings(before, year, q1))
    assert quarters["2026-03-31"]["depreciation_amortization"] == pytest.approx(16.0e6)


def test_a_10k_line_the_guard_keeps_doesnt_move_its_10qs():
    # BUG-333 (code review 28, M1): the year's latest 10-K keeps its $906M line ($794M + $111M come to no more), but
    # the gate read the line before the guard, so the 10-Q's nine months moved to $893M and the fourth quarter read
    # $13M, not $226M.
    facts = _da_filings(
        ("2025-01-01", "2025-09-30", "10-Q", "2025-10-25", {DDA: 680e6, DA: 893e6, INTANGIBLES: 213e6}),
        ("2025-01-01", "2025-12-31", "10-K", "2026-02-07", {DDA: 1189e6}),
        ("2025-01-01", "2025-12-31", "10-K", "2027-01-25",
         {DDA: 906e6, DEPRECIATION: 794e6, INTANGIBLES: 284e6, AMORTIZATION: 111e6}))
    parser = XBRLParser()
    assert parser.extract_annual_financials(facts)["2025"]["depreciation_amortization"] == pytest.approx(906e6)
    assert parser.extract_quarterly_financials(facts)["2025-12-31"]["depreciation_amortization"] == pytest.approx(
        226e6)


def test_a_later_filings_total_beside_a_line_stands_over_an_earlier_ones_line():
    # DDOG's FY2021: its first 10-K tags $18.5M under the first-ranked tag and $22.9M under another, with no usable
    # amortization; its later 10-K's $4.4M shows $18.5M the line and $22.9M the total, which stands for the year.
    year = ("2021-01-01", "2021-12-31", "10-K")
    facts = _da_filings((*year, "2022-02-25", {DDA: 18.5e6, DA: 22.9e6}),
                        (*year, "2024-02-23", {DDA: 18.5e6, DA: 22.9e6, INTANGIBLES: 4.4e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2021"]) == (22.9e6, DDA)


def test_a_line_the_depreciation_and_amortization_dont_exceed_held_them():
    # LMT's FY2019: its 10-K of 2022 tags $906M, its first 10-K's $1.189B less the $284M of the intangibles'
    # amortization, but $794M of depreciation and the cash flow statement's $111M come to $905M: no more than the
    # line, which stands.
    year = ("2019-01-01", "2019-12-31", "10-K")
    parts = {DEPRECIATION: 794e6, INTANGIBLES: 284e6, AMORTIZATION: 111e6}
    facts = _da_filings((*year, "2020-02-07", {DA: 1189e6, **parts}), (*year, "2022-01-25", {DA: 906e6, **parts}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2019"]) == (906e6, DA)


_UPS_FILED = [("2023-01-01", "2023-12-31", "10-K", "2024-02-20", {DA: 3366e6}),
              ("2024-01-01", "2024-12-31", "10-K", "2025-02-18", {DA: 3609e6})]
_UPS_2026 = [("2023-01-01", "2023-12-31", "10-K", "2026-02-17", {DDA: 2800e6, DA: 3366e6}),
             ("2024-01-01", "2024-12-31", "10-K", "2026-02-17", {DDA: 3000e6, DA: 3609e6}),
             ("2025-01-01", "2025-12-31", "10-K", "2026-02-17", {DDA: 3000e6, DA: 3746e6})]


def test_a_total_the_filing_shows_to_be_another_line_is_set_aside():
    # BUG-327: UPS's 10-K of 2026 repeats FY2023's and FY2024's D&A as its earlier 10-Ks tagged it alone
    # (DepreciationAndAmortization, $3.366B and $3.609B) and tags a note's rounded $2.8B and $3.0B under the
    # first-ranked DepreciationDepletionAndAmortization: D&A read $2.8B/$3.0B/$3.0B for FY2023-25.
    parser = XBRLParser()
    facts = _da_filings(*_UPS_FILED, *_UPS_2026)
    annual = parser.extract_annual_financials(facts)
    assert [_da(annual[fy]) for fy in ("2023", "2024", "2025")] == [(3366e6, DDA), (3609e6, DDA), (3746e6, DDA)]
    assert _da(parser.extract_annual_vintages(facts)["2025"]["2026-02-17"]) == (3746e6, DDA)


def test_a_10q_shows_its_other_line_so_too():
    # UPS's 10-Q of May 2026: Q1 2025's $912M as its 10-Q of 2025 tagged it alone, beside $743M under the first-ranked
    # tag; so Q1 2026's $791M there is that line too, beside $985M.
    facts = _da_filings(("2025-01-01", "2025-03-31", "10-Q", "2025-05-07", {DA: 912e6}),
                        ("2025-01-01", "2025-12-31", "10-K", "2026-02-17", {DA: 3746e6}),
                        ("2025-01-01", "2025-03-31", "10-Q", "2026-05-06", {DDA: 743e6, DA: 912e6}),
                        ("2026-01-01", "2026-03-31", "10-Q", "2026-05-06", {DDA: 791e6, DA: 985e6}))
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert quarters["2026-03-31"]["depreciation_amortization"] == pytest.approx(985e6)


@pytest.mark.parametrize("earlier, later, expected", [
    # the earlier filing tagged both: nothing shows which is the D&A, so the first-ranked one stands
    ({DDA: 2000e6, DA: 2455e6}, {DDA: 2001e6, DA: 2455e6}, (2001e6, DDA)),
    # the same figure under another tag is the same D&A
    ({DA: 3366e6}, {DDA: 3366e6, DA: 3366e6}, (3366e6, DDA)),
    # the later filing changes the earlier tag's figure: the period was restated, not repeated
    ({DA: 3366e6}, {DDA: 2800e6, DA: 3400e6}, (2800e6, DDA)),
])
def test_only_a_repeated_total_beside_another_shows_it(earlier, later, expected):
    year = ("2023-01-01", "2023-12-31", "10-K")
    facts = _da_filings((*year, "2024-02-20", earlier), (*year, "2026-02-17", later))
    assert _da(XBRLParser().extract_annual_financials(facts)["2023"]) == expected


def test_the_tag_is_judged_only_where_the_two_differ():
    # A filing whose two tags agree on a repeated year shows nothing of a year where they don't; and where a filing's
    # tag is shown to be another line, a year it agrees with the D&A on keeps it.
    filed = ("2023-01-01", "2023-12-31", "10-K", "2024-02-20", {DA: 3366e6})
    facts = _da_filings(filed, ("2023-01-01", "2023-12-31", "10-K", "2026-02-17", {DDA: 3366e6, DA: 3366e6}),
                        ("2024-01-01", "2024-12-31", "10-K", "2026-02-17", {DDA: 3700e6, DA: 3609e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2024"]) == (3700e6, DDA)
    facts = _da_filings(filed, ("2023-01-01", "2023-12-31", "10-K", "2026-02-17", {DDA: 2800e6, DA: 3366e6}),
                        ("2022-01-01", "2022-12-31", "10-K", "2026-02-17", {DDA: 3188e6, DA: 3188e6}),
                        ("2021-01-01", "2021-12-31", "10-K", "2026-02-17", {DDA: 4000e6, DA: 2953e6}))
    annual = XBRLParser().extract_annual_financials(facts)
    assert _da(annual["2022"]) == (3188e6, DDA)
    assert _da(annual["2021"]) == (4000e6, DDA)  # larger: it can hold more
    # a larger figure beside the repeated D&A shows nothing of a smaller one in another year
    facts = _da_filings(filed, ("2023-01-01", "2023-12-31", "10-K", "2026-02-17", {DDA: 3500e6, DA: 3366e6}),
                        ("2024-01-01", "2024-12-31", "10-K", "2026-02-17", {DDA: 3000e6, DA: 3609e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2024"]) == (3000e6, DDA)


def _shown_part(tag=DDA, other=DA):
    """Two 10-Ks of FY2010 that show *tag* a part of *other*: the first tags $10.6M under *other* alone, the next
    repeats it beside $10.0M under *tag* (TSLA's of February and March 2012)."""
    year = ("2010-01-01", "2010-12-31", "10-K")
    return [(*year, "2012-02-27", {other: 10.6e6}), (*year, "2012-03-28", {tag: 10.0e6, other: 10.6e6})]


def test_the_part_stays_a_part_in_the_filers_later_filings():
    # PACCAR's DDA is its property's depreciation, apart from the equipment it leases out: once its filings show it,
    # a later 10-K's FY2016 $302M beside $993M is that part too.
    facts = _da_filings(*_shown_part(), ("2016-01-01", "2016-12-31", "10-K", "2017-02-21", {DDA: 302e6, DA: 993e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2016"]) == (993e6, DDA)


def test_a_part_alone_gives_way_to_the_d_and_a_given_before():
    # TSLA's 10-K of 2019 repeats FY2017 as its $769.3M part alone; its 10-K of 2018 gave the year's D&A, $1.636B.
    year = ("2017-01-01", "2017-12-31", "10-K")
    facts = _da_filings(*_shown_part(), (*year, "2018-02-23", {DDA: 769.3e6, DA: 1636e6}),
                        (*year, "2019-02-19", {DDA: 769.3e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2017"]) == (1636e6, DDA)


def test_a_years_10qs_set_the_part_aside_only_together():
    # TSLA's 2018: the first quarter's $245.2M part beside $416.2M, but six months under the part's tag alone, with no
    # D&A beside it: the quarters stay on the part, or the second would read $80.8M.
    facts = _da_filings(*_shown_part(), ("2017-01-01", "2017-12-31", "10-K", "2018-02-23", {DA: 1636e6}),
                        ("2018-01-01", "2018-03-31", "10-Q", "2018-05-07", {DDA: 245.2e6, DA: 416.2e6}),
                        ("2018-01-01", "2018-06-30", "10-Q", "2018-08-06", {DDA: 497.0e6}))
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert quarters["2018-06-30"]["depreciation_amortization"] == pytest.approx(251.8e6)


def test_the_d_and_a_enters_under_the_parts_tag():
    # NTAP's FY2017: its own 10-K's $178M part (filed before its filings showed the tag a part) would outrank the next
    # 10-K's $226M under the lower-ranked tag; entered under the part's tag, the later figure stands.
    facts = _da_filings(("2015-05-01", "2016-04-29", "10-K", "2016-06-22", {DA: 279e6}),
                        ("2016-04-30", "2017-04-28", "10-K", "2017-06-20", {DDA: 178e6, DA: 226e6}),
                        ("2015-05-01", "2016-04-29", "10-K", "2018-06-19", {DDA: 212e6, DA: 279e6}),
                        ("2016-04-30", "2017-04-28", "10-K", "2018-06-19", {DDA: 178e6, DA: 226e6}))
    parser = XBRLParser()
    assert _da(parser.extract_annual_financials(facts)["2017"]) == (226e6, DDA)
    assert _da(parser.extract_annual_vintages(facts)["2017"]["2017-06-20"]) == (178e6, DDA)


def test_a_total_ranked_between_keeps_the_part():
    # KEY's FY2021: its part's tag ($107M) beside a $32M accretion figure ranked between it and the D&A's tag ($298M):
    # set aside, the part would give way to the $32M, so it stays.
    facts = _da_filings(*_shown_part(), ("2021-01-01", "2021-12-31", "10-K", "2022-02-22",
                                         {DDA: 107e6, DAAN: 32e6, DA: 298e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2021"]) == (107e6, DDA)


def test_a_years_10qs_are_judged_on_their_latest_figures():
    # PACCAR's 2015: its 10-Q of August 2015 gave six months before its filings showed the part; the 10-Qs of 2016
    # repeat the first quarter and six months, both on the D&A, which the quarters read.
    facts = _da_filings(("2015-01-01", "2015-06-30", "10-Q", "2016-08-03", {DDA: 146.2e6, DA: 448.4e6}),
                        ("2015-01-01", "2015-03-31", "10-Q", "2016-05-04", {DDA: 70.3e6, DA: 221.1e6}),
                        ("2015-01-01", "2015-12-31", "10-K", "2016-02-16", {DA: 907.1e6}),
                        ("2015-01-01", "2015-06-30", "10-Q", "2015-08-06", {DDA: 146.2e6, DA: 448.4e6}),
                        ("2015-01-01", "2015-03-31", "10-Q", "2015-05-06", {DA: 221.1e6}))
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert quarters["2015-06-30"]["depreciation_amortization"] == pytest.approx(227.3e6)


def test_two_lines_below_the_amortization_are_left_to_it():
    # BEN's FY2024: its part's tag ($116.5M) and the D&A's ($129.9M) are both below its $338.2M of amortization, both
    # lines (BUG-170): the first-ranked one and the amortization make the D&A, $454.7M.
    facts = _da_filings(*_shown_part(), ("2023-10-01", "2024-09-30", "10-K", "2024-11-12",
                                         {DDA: 116.5e6, DA: 129.9e6, INTANGIBLES: 338.2e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2024"]) == (pytest.approx(454.7e6), DA)


def test_a_later_filing_doesnt_show_an_earlier_ones_line():
    # A vintage reads what was known: the first 10-K's two figures stand as filed, whatever a later one repeats.
    year = ("2023-01-01", "2023-12-31", "10-K")
    facts = _da_filings((*year, "2024-02-20", {DDA: 2800e6, DA: 3366e6}), (*year, "2025-02-18", {DA: 3366e6}))
    assert _da(XBRLParser().extract_annual_vintages(facts)["2023"]["2024-02-20"]) == (2800e6, DDA)


def test_a_later_filing_doesnt_judge_an_earlier_ones_figures():
    # A vintage reads what was known when it was filed: the later 10-K's $713M total says nothing to the first.
    year = ("2025-01-01", "2025-12-31", "10-K")
    facts = _da_filings((*year, "2026-02-12", {DDA: 370e6, DEPRECIATION: 370e6, INTANGIBLES: 343e6}),
                        (*year, "2027-02-18", {DDA: 713e6, DEPRECIATION: 370e6, INTANGIBLES: 343e6}))
    assert _da(XBRLParser().extract_annual_vintages(facts)["2025"]["2026-02-12"]) == (370e6, DDA)


_MRSH_10QS = [("2025-01-01", end, "10-Q", filed, {DEPRECIATION: d, INTANGIBLES: a}) for end, filed, d, a in (
    ("2025-03-31", "2025-04-24", 86e6, 85e6), ("2025-06-30", "2025-07-24", 172e6, 171e6),
    ("2025-09-30", "2025-10-23", 259e6, 258e6))]


_MRSH_SHOWN = [("2023-01-01", "2023-12-31", "10-K", "2024-02-12", {DDA: 713e6}),
               ("2023-01-01", "2023-12-31", "10-K", "2025-02-12", {DDA: 370e6, INTANGIBLES: 343e6})]


def test_nine_months_of_the_10qs_lines_above_the_years_total_show_its_line():
    # A 10-K that tags its depreciation line as the total ($381M) beside $338M of amortization, its 10-Qs $259M and
    # $258M for nine months: the year's "total" was below nine months of its parts, its fourth quarter -$136M. The
    # filer's earlier 10-K shows the tag to carry its line (MRSH's 10-K of 2025: FY2023's $370M, its first $713M).
    facts = _da_filings(*_MRSH_SHOWN, *_MRSH_10QS, ("2025-01-01", "2025-12-31", "10-K", "2026-02-12",
                                                    {DDA: 381e6, DEPRECIATION: 381e6, INTANGIBLES: 338e6}))
    assert _da_records(facts) == [(719e6, DDA)] * 2
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert [quarters[end]["depreciation_amortization"] for end in ("2025-03-31", "2025-06-30", "2025-09-30",
                                                                    "2025-12-31")] == [171e6, 172e6, 174e6, 202e6]


def _shown(tag):
    """Two 10-Ks of FY2024 that show *tag* to carry the filer's depreciation line ($800M + $100M = $900M)."""
    year = ("2024-01-01", "2024-12-31", "10-K")
    return [(*year, "2025-02-01", {tag: 900e6}), (*year, "2025-06-02", {tag: 800e6, INTANGIBLES: 100e6})]


def test_the_10qs_judge_a_total_only_as_first_reported():
    # GE's FY2013: its 10-Qs and first 10-K held GE Capital ($9.762B of depreciation by the year's end); its FY2015
    # 10-K restates the year without it (here a $5.5B total beside $5.202B of depreciation and $1.286B of
    # amortization), below the first basis's nine months.
    q3 = ("2025-01-01", "2025-09-30", "10-Q", "2025-10-30", {DEPRECIATION: 7300e6, INTANGIBLES: 1290e6})
    year = ("2025-01-01", "2025-12-31", "10-K")
    facts = _da_filings(*_shown(DDA), q3,
                        (*year, "2026-02-27", {DDA: 11473e6, DEPRECIATION: 9762e6, INTANGIBLES: 1711e6}),
                        (*year, "2028-02-26", {DDA: 5500e6, DEPRECIATION: 5202e6, INTANGIBLES: 1286e6}))
    assert _da(XBRLParser().extract_annual_financials(facts)["2025"]) == (5500e6, DDA)


def test_a_depreciation_tagged_as_the_total_too_doesnt_judge_the_year():
    # GE's FY2019 10-Qs tag one figure as the depreciation and the total ($2.969B for nine months) beside $1.220B of
    # amortization: it may hold the amortization, so it says nothing of the 10-K's $4.026B.
    facts = _da_filings(*_shown(DAAN), ("2025-01-01", "2025-09-30", "10-Q", "2025-10-30",
                                        {DAAN: 2969e6, DEPRECIATION: 2969e6, INTANGIBLES: 1220e6}),
                        ("2025-01-01", "2025-12-31", "10-K", "2026-02-24",
                         {DAAN: 4026e6, DEPRECIATION: 4026e6, INTANGIBLES: 1569e6}))
    assert _da_records(facts, filed="2026-02-24") == [(4026e6, DAAN)] * 2


def test_the_10qs_filed_after_a_10k_dont_judge_it():
    # The next year's 10-Q shows the nine months again, filed after the 10-K: the 10-K is read as filed.
    facts = _da_filings(*_shown(DDA),
                        ("2025-01-01", "2025-09-30", "10-Q", "2026-10-23", {DEPRECIATION: 259e6, INTANGIBLES: 258e6}),
                        ("2025-01-01", "2025-12-31", "10-K", "2026-02-12",
                         {DDA: 381e6, DEPRECIATION: 381e6, INTANGIBLES: 338e6}))
    assert _da(XBRLParser().extract_annual_vintages(facts)["2025"]["2026-02-12"]) == (381e6, DDA)


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


def _derived(periods):
    """*periods* as the fetcher stores them: each derived (``apply_derivations``)."""
    for period in periods.values():
        apply_derivations(period)
    return periods


def _revenue_of(period):
    return period.get("revenue"), period.get("_source_tags", {}).get("revenue")


def test_a_banks_contract_revenue_gives_way_to_its_net_interest_and_noninterest_income():
    # BUG-328: FITB's FY2016-23 10-Ks tag the contract revenue of a part of its noninterest income, under the tag
    # including assessed taxes, and no total: FY2022 read $0.589B beside NII of $5.609B and noninterest income of
    # $2.766B, where its other years read the two's sum.
    annual = _derived(_annual(*_one_year(2022, "2023-02-24",
                                         RevenueFromContractWithCustomerIncludingAssessedTax=0.589e9,
                                         InterestIncomeExpenseNet=5.609e9, NoninterestIncome=2.766e9)))
    assert _revenue_of(annual["2022"]) == (8.375e9, "derived")


def test_a_banks_contract_revenue_net_of_taxes_gives_way_too():
    # BUG-328: HBAN's FY2018-25 under the tag excluding them: FY2022 $1.318B for $7.254B.
    annual = _derived(_annual(*_one_year(2022, "2023-02-17",
                                         RevenueFromContractWithCustomerExcludingAssessedTax=1.318e9,
                                         InterestIncomeExpenseNet=5.273e9, NoninterestIncome=1.981e9)))
    assert _revenue_of(annual["2022"]) == (7.254e9, "derived")


def test_a_banks_revenue_less_than_its_net_interest_income_is_its_revenue():
    # MS's FY2012: revenues of $26.178B beside noninterest income of $26.383B and net interest income of -$0.205B.
    annual = _derived(_annual(*_one_year(2012, "2013-02-26", Revenues=26.178e9, InterestIncomeExpenseNet=-0.205e9,
                                         NoninterestIncome=26.383e9)))
    assert _revenue_of(annual["2012"]) == (26.178e9, "Revenues")


@pytest.mark.parametrize("share, read", [(0.78, "derived"), (0.8, "Revenues"), (0.82, "Revenues")])
def test_a_banks_revenue_gives_way_under_its_bound_of_the_two(share, read):
    # The bound: every revenue fact under 0.8 of its filing's NII + noninterest income is a contract-revenue part (the
    # highest 0.776); the lowest total is 0.90 (STT's Q3 2023).
    annual = _derived(_annual(*_one_year(2022, "2023-02-24", Revenues=share * 8.0e9, InterestIncomeExpenseNet=5.0e9,
                                         NoninterestIncome=3.0e9)))
    assert _revenue_of(annual["2022"])[1] == read


def test_a_companys_net_interest_figure_alone_leaves_its_revenue():
    # Operating companies tag their net interest expense under the bank's tag; without noninterest income the
    # revenue stays.
    annual = _derived(_annual(*_one_year(2022, "2023-02-24", Revenues=2.0e9, InterestIncomeExpenseNet=5.0e9)))
    assert _revenue_of(annual["2022"]) == (2.0e9, "Revenues")


def test_noninterest_income_without_net_interest_income_leaves_the_revenue():
    # With one of the two missing there is no sum to weigh the figure against, nor one to take its place.
    annual = _derived(_annual(*_one_year(2022, "2023-02-24", Revenues=2.0e9, NoninterestIncome=5.0e9)))
    assert _revenue_of(annual["2022"]) == (2.0e9, "Revenues")


def test_a_banks_quarters_and_trailing_year_take_the_two_not_the_part():
    # BUG-328: FITB's 10-Qs tag the part year to date too, and its quarters read it ($154M for Q2 2022), so the
    # trailing year to 2022-12-31 read $0.589B.
    start, filed = "2022-01-01", "2023-02-24"
    ladders = {"RevenueFromContractWithCustomerIncludingAssessedTax": (152e6, 306e6, 449e6, 589e6),
               "InterestIncomeExpenseNet": (1195e6, 2534e6, 4032e6, 5609e6),
               "NoninterestIncome": (684e6, 1359e6, 2031e6, 2766e6)}
    us_gaap = {tag: {"units": {"USD": [usd(v, start, end, fy=2022, fp=fp, form=form, filed=f) for v, (end, fp, form, f)
                                       in zip(values, (("2022-03-31", "Q1", "10-Q", "2022-05-06"),
                                                       ("2022-06-30", "Q2", "10-Q", "2022-08-05"),
                                                       ("2022-09-30", "Q3", "10-Q", "2022-11-04"),
                                                       ("2022-12-31", "FY", "10-K", filed)))]}}
               for tag, values in ladders.items()}
    quarters = _derived(XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": us_gaap}}))
    assert [quarters[end]["revenue"] for end in ("2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31")] == [
        1879e6, 2014e6, 2170e6, 2312e6]
    assert compute_ttm(quarters)["2022-12-31"]["revenue"] == 8375e6


def test_a_banks_10q_part_above_half_of_the_two_gives_way_too():
    # BUG-328: NTRS's 10-Qs tag only the contract revenue, 0.63 of NII + noninterest income ($928.9M for Q1 2019 beside
    # $422.0M + $1,058.9M), and its 10-K the total too ($6,073.1M): Q1-Q3 read the part, and Q4 the total less nine
    # months of it ($3,213.2M). The noninterest income alone is under 1.25 times the part: the bound is on the two.
    start = "2019-01-01"
    ladders = {"RevenueFromContractWithCustomerExcludingAssessedTax": (928.9e6, 1884.4e6, 2859.9e6, 3852.1e6),
               "InterestIncomeExpenseNet": (422.0e6, 839.4e6, 1257.1e6, 1677.9e6),
               "NoninterestIncome": (1058.9e6, 2148.1e6, 3268.3e6, 4395.2e6)}
    rungs = (("2019-03-31", "Q1", "10-Q", "2019-04-29"), ("2019-06-30", "Q2", "10-Q", "2019-07-30"),
             ("2019-09-30", "Q3", "10-Q", "2019-10-28"), ("2019-12-31", "FY", "10-K", "2020-02-25"))
    us_gaap = {tag: {"units": {"USD": [usd(v, start, end, fy=2019, fp=fp, form=form, filed=f)
                                       for v, (end, fp, form, f) in zip(values, rungs)]}}
               for tag, values in ladders.items()}
    us_gaap["Revenues"] = {"units": {"USD": [usd(6073.1e6, start, "2019-12-31", fy=2019, filed="2020-02-25")]}}
    facts = {"facts": {"us-gaap": us_gaap}}
    quarters = _derived(XBRLParser().extract_quarterly_financials(facts))
    assert [quarters[end]["revenue"] for end in ("2019-03-31", "2019-06-30", "2019-09-30", "2019-12-31")] == (
        pytest.approx([1480.9e6, 1506.6e6, 1537.9e6, 1547.7e6]))
    assert _revenue_of(_derived(_annual(us_gaap))["2019"]) == (6073.1e6, "Revenues")


def test_a_banks_revenue_is_weighed_against_its_own_filings_two_not_its_quarters():
    # STT's 2020 10-Qs tag 2019's noninterest income to date as -$1M, so its Q4 2019 noninterest income reads $9.148B,
    # the year less -$1M, beside revenue of $3.048B, the year's $11.756B less nine months' $8.708B. Each filing's
    # revenue is its NII + noninterest income, and the quarter keeps it.
    start = "2019-01-01"
    ladders = {"Revenues": (2932e6, 5805e6, 8708e6, 11756e6),
               "InterestIncomeExpenseNet": (673e6, 1286e6, 1930e6, 2566e6),
               "NoninterestIncome": (2260e6, 4520e6, 6779e6, 9147e6)}
    rungs = (("2019-03-31", "Q1", "10-Q", "2019-05-01"), ("2019-06-30", "Q2", "10-Q", "2019-07-26"),
             ("2019-09-30", "Q3", "10-Q", "2019-10-25"), ("2019-12-31", "FY", "10-K", "2020-02-20"))
    us_gaap = {tag: {"units": {"USD": [usd(v, start, end, fy=2019, fp=fp, form=form, filed=f)
                                       for v, (end, fp, form, f) in zip(values, rungs)]}}
               for tag, values in ladders.items()}
    us_gaap["NoninterestIncome"]["units"]["USD"] += [usd(-1e6, start, end, fy=2020, fp=fp, form="10-Q", filed=f)
                                                     for end, fp, f in (("2019-03-31", "Q1", "2020-04-28"),
                                                                        ("2019-06-30", "Q2", "2020-07-27"),
                                                                        ("2019-09-30", "Q3", "2020-10-23"))]
    q4 = _derived(XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": us_gaap}}))["2019-12-31"]
    assert (q4["noninterest_income"], q4["revenue"]) == (9148e6, 3048e6)


def test_a_banks_filing_vintage_takes_the_two_not_the_part():
    # BUG-328: the as-of view reads each filing's own figures, and RF's FY2021 10-K tags FY2021's part, $104M.
    filed = "2022-02-24"
    facts = {"facts": {"us-gaap": {k: v for tag in _one_year(
        2021, filed, RevenueFromContractWithCustomerIncludingAssessedTax=104e6, InterestIncomeExpenseNet=3.914e9,
        NoninterestIncome=2.524e9) for k, v in tag.items()}}}
    vintage = _derived(XBRLParser().extract_annual_vintages(facts)["2021"])[filed]
    assert _revenue_of(vintage) == (6.438e9, "derived")


_TO_COMMON = "NetIncomeLossAvailableToCommonStockholdersBasic"
_UNIT_OF = {"EarningsPerShareBasic": "USD/shares", "EarningsPerShareDiluted": "USD/shares",
            "WeightedAverageNumberOfSharesOutstandingBasic": "shares",
            "WeightedAverageNumberOfDilutedSharesOutstanding": "shares"}


def _income_facts(*filings):
    """Facts of filings, each (filed, start, end, form, {tag: value}), one accession per filing date."""
    us_gaap = {}
    for filed, start, end, form, tagged in filings:
        for tag, value in tagged.items():
            unit = _UNIT_OF.get(tag, "USD")
            us_gaap.setdefault(tag, {"units": {unit: []}})["units"][unit].append(
                usd(value, start, end, fy=int(filed[:4]), fp="FY" if form == "10-K" else "Q", form=form, filed=filed))
    return {"facts": {"us-gaap": us_gaap}}


def _eg_fy2016(**changed):
    """EG's FY2016 in its 10-K of 2017-03-01: no NetIncomeLoss; its to-common figure, $792.4M, beside ProfitLoss,
    $996.3M, which its EPS x its counts (in thousands) make."""
    tagged = {_TO_COMMON: 792.415e6, "ProfitLoss": 996.344e6, "EarningsPerShareBasic": 23.85,
              "WeightedAverageNumberOfSharesOutstandingBasic": 41329.0, "EarningsPerShareDiluted": 23.68,
              "WeightedAverageNumberOfDilutedSharesOutstanding": 41628.0}
    tagged.update(changed)
    return _income_facts(("2017-03-01", "2016-01-01", "2016-12-31", "10-K", tagged))


def test_a_to_common_figure_its_own_eps_contradicts_gives_way_to_the_one_it_makes():
    # BUG-252: EG tags no NetIncomeLoss for FY2016-20, and its to-common figure is about 20% below the net income its
    # EPS x count makes (23.85 x 41.329M = $985.7M), which is its ProfitLoss: net income read $792.4M.
    year = XBRLParser().extract_annual_financials(_eg_fy2016())["2016"]
    assert (year["net_income"], year["_source_tags"]["net_income"]) == (996.344e6, "ProfitLoss")


def test_the_counts_in_thousands_are_rescaled_once_net_income_is_the_eps_figure():
    # BUG-252: fed $792.4M, share_scale's net income / EPS check (33.5M) left the diluted 41,628 in thousands, a 989.6x
    # seam against FY2020's units that truncated FY2015-19.
    years = XBRLParser().extract_annual_financials(_eg_fy2016())
    normalize_share_scale(years)
    assert years["2016"]["weighted_avg_shares_diluted"] == 41.628e6
    assert years["2016"]["weighted_avg_shares_basic"] == 41.329e6


def test_a_loss_to_common_beside_a_profit_its_eps_makes_gives_way():
    # EG's FY2018: -$125.8M to common, its EPS +2.17 beside a ProfitLoss of $89.0M (2.17 x 40.586M = $88.1M).
    year = XBRLParser().extract_annual_financials(_eg_fy2016(**{
        _TO_COMMON: -125.848e6, "ProfitLoss": 89.041e6, "EarningsPerShareBasic": 2.18,
        "WeightedAverageNumberOfSharesOutstandingBasic": 40388.0, "EarningsPerShareDiluted": 2.17,
        "WeightedAverageNumberOfDilutedSharesOutstanding": 40586.0}))["2016"]
    assert year["net_income"] == 89.041e6


def test_a_to_common_figure_of_the_other_sign_from_its_eps_gives_way():
    # The size its EPS x count makes, with the sign flipped, is no figure that EPS divides.
    year = XBRLParser().extract_annual_financials(_eg_fy2016(**{_TO_COMMON: -996.344e6}))["2016"]
    assert year["net_income"] == 996.344e6


def test_a_to_common_figure_is_checked_with_basic_eps_before_diluted():
    # It is basic EPS's figure: a diluted EPS adds back what converts (a convertible's interest), so its diluted
    # EPS x count, $1.15B here, can be another filing figure's while the basic pair makes the figure for common.
    year = XBRLParser().extract_annual_financials(_eg_fy2016(**{
        _TO_COMMON: 1000.0e6, "ProfitLoss": 1150.0e6, "EarningsPerShareBasic": 2.00,
        "WeightedAverageNumberOfSharesOutstandingBasic": 500.0e6, "EarningsPerShareDiluted": 2.00,
        "WeightedAverageNumberOfDilutedSharesOutstanding": 575.0e6}))["2016"]
    assert year["net_income"] == 1000.0e6


def test_a_to_common_figure_after_preferred_dividends_stays():
    # A filer with preferred dividends and a large noncontrolling interest: its EPS x count is its to-common figure, and
    # ProfitLoss, 15% above it, is not the EPS's figure (FCX, SPG and PNC's other years have this shape).
    year = XBRLParser().extract_annual_financials(_eg_fy2016(**{
        _TO_COMMON: 1000.0e6, "ProfitLoss": 1150.0e6, "EarningsPerShareBasic": 2.00,
        "WeightedAverageNumberOfSharesOutstandingBasic": 500.0e6, "EarningsPerShareDiluted": 1.99,
        "WeightedAverageNumberOfDilutedSharesOutstanding": 502.5e6}))["2016"]
    assert (year["net_income"], year["_source_tags"]["net_income"]) == (1000.0e6, _TO_COMMON)


def test_a_to_common_figure_its_eps_makes_stays_beside_a_profit_it_makes_too():
    # Both within share_scale's 10%: the tag order settles it (AEP, CAT: a small noncontrolling interest).
    year = XBRLParser().extract_annual_financials(_eg_fy2016(**{
        _TO_COMMON: 1000.0e6, "ProfitLoss": 1050.0e6, "EarningsPerShareBasic": 2.00,
        "WeightedAverageNumberOfSharesOutstandingBasic": 500.0e6, "EarningsPerShareDiluted": 1.99,
        "WeightedAverageNumberOfDilutedSharesOutstanding": 502.5e6}))["2016"]
    assert year["net_income"] == 1000.0e6


def test_a_to_common_figure_stays_where_its_eps_makes_no_other_either():
    # Ford's FY2019: $47M to common, ProfitLoss $84M, EPS 0.01 x 4,004M = $40M: an EPS rounded to the cent checks
    # neither, so the tag order stands.
    year = XBRLParser().extract_annual_financials(_eg_fy2016(**{
        _TO_COMMON: 47.0e6, "ProfitLoss": 84.0e6, "EarningsPerShareBasic": 0.01,
        "WeightedAverageNumberOfSharesOutstandingBasic": 3972.0e6, "EarningsPerShareDiluted": 0.01,
        "WeightedAverageNumberOfDilutedSharesOutstanding": 4004.0e6}))["2016"]
    assert year["net_income"] == 47.0e6


def test_the_diluted_eps_checks_a_to_common_figure_without_a_basic_one():
    facts = _eg_fy2016()
    for tag in ("EarningsPerShareBasic", "WeightedAverageNumberOfSharesOutstandingBasic"):
        del facts["facts"]["us-gaap"][tag]
    assert XBRLParser().extract_annual_financials(facts)["2016"]["net_income"] == 996.344e6


def test_a_vintage_judges_its_to_common_figure_by_its_own_filing():
    # PNC's FY2019 in its 10-K of 2021: $4.284B to common, ProfitLoss $5.418B, EPS 11.39 x 448M = $5.103B. An earlier
    # filing whose to-common figure its EPS makes keeps it.
    first, later = "2020-02-28", "2021-03-01"
    facts = _income_facts(
        (first, "2019-01-01", "2019-12-31", "10-K", {_TO_COMMON: 5.103e9, "ProfitLoss": 5.418e9,
                                                       "EarningsPerShareDiluted": 11.39,
                                                       "WeightedAverageNumberOfDilutedSharesOutstanding": 448.0e6}),
        (later, "2019-01-01", "2019-12-31", "10-K", {_TO_COMMON: 4.284e9, "ProfitLoss": 5.418e9,
                                                       "EarningsPerShareDiluted": 11.39,
                                                       "WeightedAverageNumberOfDilutedSharesOutstanding": 448.0e6}))
    vintages = XBRLParser().extract_annual_vintages(facts)["2019"]
    assert (vintages[first]["net_income"], vintages[later]["net_income"]) == (5.103e9, 5.418e9)


def test_a_quarterly_ladder_reads_the_figure_each_filings_eps_makes():
    # EG's 2017: its 10-Qs' to-common figures are as far from their EPS x count as its 10-K's, and the fourth quarter,
    # the year less nine months, is the 10-K's own fourth-quarter ProfitLoss, $571.0M.
    start = "2017-01-01"
    facts = _income_facts(*[
        (filed, start, end, form, {_TO_COMMON: tc, "ProfitLoss": pl, "EarningsPerShareDiluted": eps,
                                   "WeightedAverageNumberOfDilutedSharesOutstanding": count})
        for filed, end, form, tc, pl, eps, count in (
            ("2017-05-05", "2017-03-31", "10-Q", 237.754e6, 291.643e6, 7.07, 40.796e6),
            ("2017-08-04", "2017-06-30", "10-Q", 430.035e6, 537.317e6, 13.02, 40.836e6),
            ("2017-11-03", "2017-09-30", "10-Q", -255.944e6, -102.057e6, -2.51, 40.852e6),
            ("2018-03-01", "2017-12-31", "10-K", 258.952e6, 468.968e6, 11.36, 40843.0))])
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert [quarters[end]["net_income"] for end in ("2017-03-31", "2017-06-30", "2017-09-30", "2017-12-31")] == [
        pytest.approx(v) for v in (291.643e6, 245.674e6, -639.374e6, 571.025e6)]


# Q16: net income to common, a field of its own, for the valuation history's P/E (as its P/B is on common equity).
def _alb_like(filed="2026-02-12", start="2025-01-01", end="2025-12-31", form="10-K", **changed):
    """A year with preferred dividends: net income $224M, $57M to common, basic EPS 0.48 x 118M ($56.6M)."""
    tagged = {"NetIncomeLoss": 224.0e6, _TO_COMMON: 57.0e6, "EarningsPerShareBasic": 0.48,
              "WeightedAverageNumberOfSharesOutstandingBasic": 118.0e6, "EarningsPerShareDiluted": 0.48,
              "WeightedAverageNumberOfDilutedSharesOutstanding": 118.5e6}
    tagged.update(changed)
    return filed, start, end, form, tagged


def test_net_income_to_common_is_read_beside_net_income():
    year = XBRLParser().extract_annual_financials(_income_facts(_alb_like()))["2025"]
    assert (year["net_income"], year["_source_tags"]["net_income"]) == (224.0e6, "NetIncomeLoss")
    assert (year["net_income_to_common"], year["_source_tags"]["net_income_to_common"]) == (57.0e6, _TO_COMMON)


def test_a_to_common_figure_its_own_eps_contradicts_is_not_read_as_net_income_to_common():
    # EG's FY2016: $792.4M to common where its EPS x count make $985.7M. Net income ranks it last (BUG-252); the field
    # of its own doesn't read it at all, and the history's P/E then takes net income.
    year = XBRLParser().extract_annual_financials(_eg_fy2016())["2016"]
    assert "net_income_to_common" not in year
    assert year["net_income"] == 996.344e6


def test_a_to_common_figure_no_other_figure_makes_is_still_not_read_where_its_eps_contradicts_it():
    # Unlike net income's rule, no other tag need be the EPS's figure: one that EPS x count contradicts isn't to common.
    year = XBRLParser().extract_annual_financials(_income_facts(_alb_like(**{_TO_COMMON: 80.0e6})))["2025"]
    assert "net_income_to_common" not in year
    assert year["net_income"] == 224.0e6


def test_a_to_common_figure_without_eps_in_its_filing_is_read():
    filed, start, end, form, tagged = _alb_like()
    tagged = {k: v for k, v in tagged.items() if k in ("NetIncomeLoss", _TO_COMMON)}
    year = XBRLParser().extract_annual_financials(_income_facts((filed, start, end, form, tagged)))["2025"]
    assert year["net_income_to_common"] == 57.0e6


def test_a_vintage_reads_the_to_common_figure_its_own_eps_makes():
    # PNC's FY2019: the 10-K of 2020 tags $5.103B, which its EPS x count make; that of 2021 $4.284B, which they don't.
    first, later = "2020-02-28", "2021-03-01"
    facts = _income_facts(
        (first, "2019-01-01", "2019-12-31", "10-K", {_TO_COMMON: 5.103e9, "ProfitLoss": 5.418e9,
                                                       "EarningsPerShareDiluted": 11.39,
                                                       "WeightedAverageNumberOfDilutedSharesOutstanding": 448.0e6}),
        (later, "2019-01-01", "2019-12-31", "10-K", {_TO_COMMON: 4.284e9, "ProfitLoss": 5.418e9,
                                                       "EarningsPerShareDiluted": 11.39,
                                                       "WeightedAverageNumberOfDilutedSharesOutstanding": 448.0e6}))
    vintages = XBRLParser().extract_annual_vintages(facts)["2019"]
    assert vintages[first]["net_income_to_common"] == 5.103e9
    assert "net_income_to_common" not in vintages[later]
    # The annual record reads the latest filing the EPS doesn't contradict.
    assert XBRLParser().extract_annual_financials(facts)["2019"]["net_income_to_common"] == 5.103e9


def _to_common_year(contradicted_nine_months=False):
    """A year's ladder: to common 100, 250, 330 and 500 year to date; net income 120, 290, 390 and 580."""
    start = "2025-01-01"
    rungs = (("2025-05-01", "2025-03-31", "10-Q", 100.0e6, 120.0e6),
             ("2025-08-01", "2025-06-30", "10-Q", 250.0e6, 290.0e6),
             ("2025-10-31", "2025-09-30", "10-Q", 330.0e6, 390.0e6),
             ("2026-02-20", "2025-12-31", "10-K", 500.0e6, 580.0e6))
    filings = []
    for filed, end, form, to_common, net_income in rungs:
        eps = to_common / 100.0e6
        if contradicted_nine_months and end == "2025-09-30":
            to_common = 250.0e6  # 2.50 for EPS 3.30 x 100M
        filings.append((filed, start, end, form, {
            "NetIncomeLoss": net_income, _TO_COMMON: to_common, "EarningsPerShareBasic": eps,
            "WeightedAverageNumberOfSharesOutstandingBasic": 100.0e6}))
    return _income_facts(*filings)


def test_net_income_to_common_quarters_are_the_year_to_date_differences():
    quarters = XBRLParser().extract_quarterly_financials(_to_common_year())
    ends = ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    assert [quarters[e]["net_income_to_common"] for e in ends] == [
        pytest.approx(v) for v in (100e6, 150e6, 80e6, 170e6)]
    assert [quarters[e]["net_income"] for e in ends] == [pytest.approx(v) for v in (120e6, 170e6, 100e6, 190e6)]
    ttm = compute_ttm(quarters)["2025-12-31"]
    assert ttm["net_income_to_common"] == pytest.approx(500e6)
    assert ttm["net_income"] == pytest.approx(580e6)


def test_a_contradicted_rung_leaves_its_quarters_without_net_income_to_common():
    # The nine months' figure its EPS contradicts isn't read: the third and fourth quarters have no difference to take,
    # and the trailing year falls to net income (the history's fallback).
    quarters = XBRLParser().extract_quarterly_financials(_to_common_year(contradicted_nine_months=True))
    assert quarters["2025-06-30"]["net_income_to_common"] == pytest.approx(150e6)
    assert "net_income_to_common" not in quarters["2025-09-30"]
    assert "net_income_to_common" not in quarters["2025-12-31"]
    assert quarters["2025-09-30"]["net_income"] == pytest.approx(100e6)
    assert "net_income_to_common" not in compute_ttm(quarters)["2025-12-31"]


def test_an_eps_rounded_near_zero_contradicts_no_to_common_figure():
    # MCHP's FY2026 (April to March): nine months of $2.4M to common at EPS $0.00, which checks nothing. Read, the third
    # and fourth quarters have their differences (otherwise the trailing year fell back to net income).
    start = "2025-04-01"
    facts = _income_facts(*[
        (filed, start, end, form, {_TO_COMMON: tc, "EarningsPerShareBasic": eps,
                                   "WeightedAverageNumberOfSharesOutstandingBasic": 540.0e6})
        for filed, end, form, tc, eps in (
            ("2025-08-07", "2025-06-30", "10-Q", -46.4e6, -0.09),
            ("2025-11-06", "2025-09-30", "10-Q", -32.5e6, -0.06),
            ("2026-02-05", "2025-12-31", "10-Q", 2.4e6, 0.0),
            ("2026-05-21", "2026-03-31", "10-K", 118.8e6, 0.22))])
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert [quarters[e]["net_income_to_common"] for e in ("2025-09-30", "2025-12-31", "2026-03-31")] == [
        pytest.approx(v) for v in (13.9e6, 34.9e6, 116.4e6)]


def _to_common_filing(filed, fy, to_common, eps):
    return (filed, f"{fy}-01-01", f"{fy}-12-31", "10-K", {
        "NetIncomeLoss": eps * 100.0e6, _TO_COMMON: to_common, "EarningsPerShareBasic": eps,
        "WeightedAverageNumberOfSharesOutstandingBasic": 100.0e6})


def test_a_contradicted_to_common_figure_is_not_put_back_by_the_restore_step():
    # BUG-316 (code review 26): FY2020's 10-Ks of 2021 and 2022 tag +$50M to common beside EPS -0.50 x 100M (their
    # own EPS contradicts them: not read, Q16); the 10-K of 2023 tags -$50M, which its EPS makes. The restore step
    # read the unread facts and put +$50M back as a "sign flip" between profitable neighbours.
    facts = _income_facts(
        _to_common_filing("2019-02-20", 2018, 80.0e6, 0.80),
        _to_common_filing("2020-02-20", 2019, 90.0e6, 0.90),
        _to_common_filing("2021-02-20", 2020, 50.0e6, -0.50),
        _to_common_filing("2022-02-20", 2020, 50.0e6, -0.50),
        _to_common_filing("2022-02-20", 2021, 110.0e6, 1.10),
        _to_common_filing("2023-02-20", 2020, -50.0e6, -0.50),
        _to_common_filing("2023-02-20", 2022, 120.0e6, 1.20),
        _to_common_filing("2024-02-20", 2023, 130.0e6, 1.30),
    )
    year = XBRLParser().extract_annual_financials(facts)["2020"]
    assert year["net_income_to_common"] == -50.0e6, (year["net_income_to_common"], year.get("_repairs"))

