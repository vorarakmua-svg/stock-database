"""Behaviours no test pinned (BUG-087, BUG-147, BUG-162, BUG-288), found as surviving mutants by code reviews 8, 13, 14
and 22. Each test says the mutant it kills. Self-contained (no helper imported from another test module)."""
import sqlite3
from datetime import datetime

import pytest

import src.refresh
from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.parsers.calculated_metrics import CalculatedMetrics
from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd

# --- BUG-147: a lone first quarter's 80-day floor --------------------------------------------------------------------


def _transition(*lone):
    """A November year end, then lone (end, revenue) figures from the day after it, each on a balance sheet."""
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [usd(400.0, "2007-12-01", "2008-11-30", fy=2008, fp="FY", form="10-K",
                                           filed="2009-01-28")] + [
            usd(val, "2008-12-01", end, fy=2009, fp="Q1", form="10-K", filed="2009-04-08") for end, val in lone]}},
        "Assets": {"units": {"USD": [usd(900.0, None, end, fy=2009, fp="Q1", form="10-K", filed="2009-04-08")
                                     for end, _ in lone]}},
    }}}
    quarters = XBRLParser().extract_quarterly_financials(facts)
    return {end: quarters[end].get("revenue") for end, _ in lone}


def test_a_lone_one_month_transition_period_makes_no_quarter():
    # BUG-147 (m124_lone_no_span_floor): MS's and GS's 2008 transition periods (one month, from the day after their
    # November year end to the December balance sheet) are lone figures that start as a first quarter does, on a
    # day after a year's end, and end on a balance sheet's date. Without the 80-day floor they became first quarters
    # with flows (MS OCF -$14.17B, debt issued $13.59B). A lone three-month figure there still fills its quarter
    # (BUG-124: the control).
    assert _transition(("2008-12-31", 30.0)) == {"2008-12-31": None}
    assert _transition(("2009-02-28", 110.0)) == {"2009-02-28": 110.0}


# --- BUG-162: the quarterly extraction reads the filtered facts ------------------------------------------------------

RESTRICTED = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"


def test_a_cash_total_only_a_cash_flow_statement_has_makes_no_quarter():
    # BUG-162 (m139_quarterly_unfiltered): a cash flow statement reconciles cash with restricted cash, so its opening
    # balance stands at the year end before the 10-Q, a date no balance sheet of the filing shows (DIS's FY2016 was a
    # cash-only year, 65 quarterly cells filled from cash flow statements). The quarterly checkpoints read the total
    # on balance-sheet dates alone: the opening date is no quarter, the balance sheet's own date has its cash.
    q1 = dict(fy=2026, fp="Q1", form="10-Q", filed="2026-04-28")
    facts = {"facts": {"us-gaap": {
        RESTRICTED: {"units": {"USD": [usd(7.0e9, None, "2025-12-31", **q1), usd(8.0e9, None, "2026-03-31", **q1)]}},
        "LiabilitiesAndStockholdersEquity": {"units": {"USD": [usd(90.0e9, None, "2026-03-31", **q1)]}},
    }}}
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert sorted(quarters) == ["2026-03-31"]
    assert quarters["2026-03-31"]["cash_and_equivalents"] == 8.0e9


# --- BUG-288: boundaries of BUG-267's rules (code review 22's surviving mutants) -------------------------------------


def _filings(*filings):
    """Company facts of the given filings, each ``(form, filed, end, {tag: value})``: that filing's balance sheet on
    *end*, with its total."""
    us_gaap = {}
    for form, filed, end, tagged in filings:
        filing = dict(fy=int(filed[:4]), fp="FY" if form.startswith("10-K") else "Q2", form=form, filed=filed)
        for tag, val in {"LiabilitiesAndStockholdersEquity": 100.0e9, **tagged}.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, None, end, **filing))
    return {"facts": {"us-gaap": us_gaap}}


def _year_2025(facts):
    return XBRLParser().extract_annual_financials(facts).get("2025", {})


def test_one_line_tagged_three_ways_needs_its_figures_equal_not_just_in_agreement():
    # BUG-288 (r22_three_ways_by_agree_not_equality): BUG-267's "one line tagged as the borrowings, their part and
    # the maturities" is one figure three times, exactly; NI's 2018-06-30 had $600.0M of borrowings (and of one part of
    # them) beside $597.7M of maturities, 0.38% apart: two lines, $1,197.7M, where agreement alone would count the
    # borrowings as holding the maturities ($600M).
    facts = _filings(("10-K", "2026-02-12", "2025-12-31", dict(
        ShortTermBorrowings=0.6000e9, OtherShortTermBorrowings=0.6000e9, LongTermDebtCurrent=0.5977e9,
        LongTermDebtNoncurrent=5.0e9)))
    assert _year_2025(facts)["short_term_debt"] == pytest.approx(1.1977e9)
    # And the same figure three times is one line, as BUG-267 has it.
    same = _filings(("10-K", "2026-02-12", "2025-12-31", dict(
        ShortTermBorrowings=0.6e9, OtherShortTermBorrowings=0.6e9, LongTermDebtCurrent=0.6e9,
        LongTermDebtNoncurrent=5.0e9)))
    assert _year_2025(same)["short_term_debt"] == pytest.approx(0.6e9)


_BE_FY2024 = ("10-K", "2025-02-27", "2024-12-31", dict(
    ShortTermBorrowings=0.1144e9, LongTermDebtCurrent=0.114385e9, LongTermDebtNoncurrent=1.014407e9))


@pytest.mark.parametrize("borrowings, short_term", [
    (0.001424e9, 0.001424e9),                 # equal to the maturities: held, as the annual report says
    (0.001396e9, 0.001396e9),                 # 1.97% below: within the 2% of rounding
    (0.001380e9, 0.001380e9 + 0.001424e9),    # 3.1% below: no longer rounding, two lines
])
def test_borrowings_below_the_maturities_take_the_annual_reports_word_only_within_two_percent(borrowings, short_term):
    # BUG-288 (r22_quarter_guard_5pct): the quarter rule's guard is _MATURITIES_ROUNDING (2%) under the maturities; BE's
    # 1.7% is pinned (a rounding), the upper side wasn't: a guard of 5% read 3% below as held, losing them.
    facts = _filings(_BE_FY2024, ("10-Q", "2025-10-28", "2025-09-30", dict(
        ShortTermBorrowings=borrowings, LongTermDebtCurrent=0.001424e9, LongTermDebtNoncurrent=1.130892e9)))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-09-30"]
    assert quarter["short_term_debt"] == pytest.approx(short_term)


def _coo_fy2016(**changed):
    """COO's FY2016 lines (BUG-267's current-liabilities bound): $226.3M of borrowings, $210.7M of maturities, payables
    $107.4M, compensation $77.7M and other liabilities $131.8M, within current liabilities of *LiabilitiesCurrent*."""
    tagged = dict(ShortTermBorrowings=0.2263e9, LinesOfCreditCurrent=0.0171e9, LongTermDebtCurrent=0.2107e9,
                  LongTermDebtNoncurrent=1.1074e9, LiabilitiesCurrent=0.5432e9, AccountsPayableCurrent=0.1074e9,
                  EmployeeRelatedLiabilitiesCurrent=0.0777e9, OtherLiabilitiesCurrent=0.1318e9)
    return _filings(("10-K", "2026-02-12", "2025-12-31", {**tagged, **changed}))


@pytest.mark.parametrize("current_liabilities, short_term", [
    (0.6200e9, 0.4370e9),   # the lines make $0.6221B: 0.34% over, within rounding: they decide nothing
    (0.6159e9, 0.2263e9),   # 1.0% over: beyond rounding (0.5%), the borrowings hold the maturities
])
def test_the_current_liabilities_bound_decides_beyond_half_a_percent(current_liabilities, short_term):
    # BUG-288 (r22_bound_tolerance_2pct): the bound's tolerance is _AGREE (0.5%). A tolerance of 2% survived: it is
    # equivalent on the user's data (HWM's and DOV's fires between 0.5% and 2% have borrowings equal to maturities,
    # which the margin decides anyway), so the boundary needs a figure of its own, 1% over.
    year = _year_2025(_coo_fy2016(LiabilitiesCurrent=current_liabilities))
    assert year["short_term_debt"] == pytest.approx(short_term)


@pytest.mark.parametrize("lacked, gap", [
    ({}, (None, False, False)),                                            # a complete collection
    ({"report": {"accession": "0001-26-000001"}}, ("0001-26-000001", False, False)),
    ({"predecessor": "503 Server Error"}, (None, True, False)),
    ({"report": {"accession": "0001-26-000001"}, "predecessor": "no XBRL data"}, ("0001-26-000001", True, False)),
    ({"submissions": "503 Server Error"}, (None, False, True)),          # BUG-282: no report could be looked for
    ({"predecessor": "no XBRL data", "submissions": "HTTPError"}, (None, True, True)),
])
def test_a_gap_is_its_report_and_whether_its_predecessor_went_unread(lacked, gap):
    # BUG-288 (r22_gap_ignores_predecessor, "report and predecessor" against "report alone"): two collections are the
    # same gap when every part agrees. A report's gap alone has no predecessor flag (reducing it to "anything lacked"
    # marks it as one whose predecessor failed too), and a predecessor's alone has no report. BUG-282 adds whether the
    # submissions went unread.
    assert src.refresh._gap(lacked) == gap


# --- BUG-147 and BUG-162: the quote refresh's EV family --------------------------------------------------------------


def _collected(tmp_path, operating_income, sector_class=None):
    """A full collection of AAA at a market cap of 1,000 (net debt 250, FCF 60, EBITDA 120 with *operating_income* 100),
    and its company's sector class if given."""
    store = SQLiteStore(db_path=tmp_path / "stock.db")
    s = StockData(ticker="AAA", cik="1", company_name="AAA Inc.")
    year = {"fiscal_year": 2025, "period_end": "2025-12-31", "operating_income": operating_income,
            "depreciation_amortization": 20.0, "operating_cash_flow": 90.0, "capex": 30.0,
            "long_term_debt": 300.0, "short_term_debt": 50.0, "cash_and_equivalents": 100.0}
    s.financials_annual = {"2025": year}
    s.market_data = {"current_price": 10.0, "market_cap": 1000.0}
    s.valuation = {"enterprise_value": 5000.0, "ev_to_ebitda": 50.0}
    s.calculated_metrics = CalculatedMetrics().calculate_all(year, market_data=s.market_data, valuation=s.valuation)
    s.collected_at = datetime(2026, 8, 1, 12)
    store.export([s])
    if sector_class:
        conn = sqlite3.connect(tmp_path / "stock.db")
        conn.execute("UPDATE companies SET sector_class = ? WHERE ticker = 'AAA'", (sector_class,))
        conn.commit()
        conn.close()
    return store


def _ev_family(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT enterprise_value, ev_to_ebitda, ev_to_fcf, fcf_yield FROM market_snapshots "
                            "ORDER BY collected_at").fetchall()
    finally:
        conn.close()


def _refresh(store, valuation):
    store.upsert_quote("AAA", {"market_data": {"current_price": 10.1, "market_cap": 1010.0}, "valuation": valuation,
                               "shareholders": {}, "analyst_estimates": {}}, "2026-08-02T12:00:00")


def test_a_refresh_reads_yahoos_ebitda_where_the_stored_year_has_none(tmp_path):
    # BUG-147 (m126_no_yahoo_ebitda): a year without operating income has no EBITDA of ours, and the calculator falls
    # back to the quote's (CB's and CRWD's refreshed EV/EBITDA read 10.13 and 3,541; without it None). The EV is ours,
    # the market cap's 1,010 with the net debt of 250.
    store = _collected(tmp_path, operating_income=None)
    _refresh(store, {"ebitda": 200.0})
    assert _ev_family(tmp_path / "stock.db")[1] == pytest.approx((1260.0, 1260.0 / 200.0, 1260.0 / 60.0, 60.0 / 1010.0))


def test_a_refresh_without_yahoos_ebitda_has_no_ev_to_ebitda_where_the_stored_year_has_none(tmp_path):
    store = _collected(tmp_path, operating_income=None)
    _refresh(store, {})
    assert _ev_family(tmp_path / "stock.db")[1] == pytest.approx((1260.0, None, 1260.0 / 60.0, 60.0 / 1010.0))


def test_a_banks_refresh_has_an_empty_ev_family(tmp_path):
    # BUG-162 (m134_refresh_unsectored): a bank's net debt, EBITDA and FCF are suppressed (BUG-134), so its EV family is
    # empty, in a refresh as in a collection; the refresh computed it without the company's sector and JPM's EV read
    # $1,027.5B (review 14: 65 cells).
    store = _collected(tmp_path, operating_income=100.0, sector_class="bank")
    _refresh(store, {"ebitda": 200.0})
    assert _ev_family(tmp_path / "stock.db")[1] == (None, None, None, None)


def test_a_general_companys_refresh_has_its_ev_family(tmp_path):
    store = _collected(tmp_path, operating_income=100.0)
    _refresh(store, {"ebitda": 200.0})
    assert _ev_family(tmp_path / "stock.db")[1] == pytest.approx((1260.0, 1260.0 / 120.0, 1260.0 / 60.0, 60.0 / 1010.0))
