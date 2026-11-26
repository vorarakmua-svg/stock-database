"""A figure filed before its period ends is no figure of that period (BUG-370).

BAC's FY2010 10-K (filed 2011-02-25) tags a $0.01 dividend declared for 2011-01-01..2011-12-31, and FAST's (filed
2011-02-09) $0.50 the same way. Under the higher-ranked tag, each outranked the year's own 10-K figure ($0.04, $0.65
under ``CommonStockDividendsPerShareCashPaid``) once the year was over, in the record, in every as-of snapshot after
it, and in the quarters (BAC's fourth quarter of 2011 read -0.02).
"""

from typing import Any, Dict, List, Optional

import pytest

from src.parsers.annual_record import build_annual_record
from src.parsers.annual_snapshots import build_snapshots
from src.parsers.xbrl_parser import XBRLParser

DECLARED, PAID = "CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"


def _fact(val: float, start: str, end: str, filed: str, accn: str, form: str = "10-K",
          fp: str = "FY") -> Dict[str, Any]:
    return {"val": val, "start": start, "end": end, "filed": filed, "accn": accn, "form": form,
            "fy": int(filed[:4]) - (1 if form == "10-K" else 0), "fp": fp}


def _facts(prior_10k: str, ahead: float, own: float, quarters: Optional[List[Dict[str, Any]]] = None
           ) -> Dict[str, Any]:
    """The prior 10-K (filed *prior_10k*) tags *ahead* as 2011's dividend under the declared tag; FY2011's 10-K
    (2012-02-23) files the year's *own* under the cash-paid tag."""
    k10, k11 = (prior_10k, "k10"), ("2012-02-23", "k11")
    revenue = [_fact(1.0e11, "2010-01-01", "2010-12-31", *k10), _fact(1.1e11, "2011-01-01", "2011-12-31", *k11)]
    declared = [_fact(own, "2010-01-01", "2010-12-31", *k10), _fact(ahead, "2011-01-01", "2011-12-31", *k10)]
    paid = [_fact(own, "2010-01-01", "2010-12-31", *k11), _fact(own, "2011-01-01", "2011-12-31", *k11)]
    return {"cik": 1, "entityName": "X", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": revenue}},
        DECLARED: {"units": {"USD/shares": declared}},
        PAID: {"units": {"USD/shares": paid + list(quarters or [])}}}}}


#: (prior 10-K's filing day, the forward-dated figure, the year's own)
CASES = [pytest.param("2011-02-25", 0.01, 0.04, id="BAC"), pytest.param("2011-02-09", 0.50, 0.65, id="FAST")]


@pytest.mark.parametrize("filed, ahead, own", CASES)
def test_the_record_reads_the_years_own_10k_not_the_figure_filed_ahead_of_it(filed, ahead, own):
    record = build_annual_record(XBRLParser(), _facts(filed, ahead, own), ticker="X", years_back=None)
    period = record.periods["2011"]
    assert period["dividends_per_share"] == own
    assert period["_source_tags"]["dividends_per_share"] == PAID
    assert period["_source_filings"]["dividends_per_share"]["accn"] == "k11"


@pytest.mark.parametrize("filed, ahead, own", CASES)
def test_no_snapshot_reads_the_figure_filed_ahead_of_its_year(filed, ahead, own):
    snapshots = build_snapshots(XBRLParser(), _facts(filed, ahead, own), ticker="X", years_back=None)
    rows = [row for row in snapshots.rows if row["fiscal_year"] == 2011 and row["present"]]
    assert rows and all(row["dividends_per_share"] == own for row in rows)
    assert [row["known_from"] for row in rows] == ["2012-02-23"]
    assert sorted(snapshots.vintages["2011"]) == ["k11"]


def test_the_vintages_hold_no_view_of_a_figure_filed_ahead():
    vintages = XBRLParser().extract_annual_vintages(_facts("2011-02-25", 0.01, 0.04))
    assert sorted(vintages["2011"]) == ["k11"]
    assert vintages["2011"]["k11"]["dividends_per_share"] == 0.04


def test_the_fourth_quarter_differences_the_years_own_figure():
    # BAC's 10-Qs: 0.01 a quarter, year to date; the year less nine months is the fourth quarter's 0.01, not
    # the forward-dated 0.01 less 0.03.
    quarters = [_fact(0.01, "2011-01-01", "2011-03-31", "2011-05-05", "q1", "10-Q", "Q1"),
                _fact(0.02, "2011-01-01", "2011-06-30", "2011-08-04", "q2", "10-Q", "Q2"),
                _fact(0.01, "2011-04-01", "2011-06-30", "2011-08-04", "q2", "10-Q", "Q2"),
                _fact(0.03, "2011-01-01", "2011-09-30", "2011-11-03", "q3", "10-Q", "Q3"),
                _fact(0.01, "2011-07-01", "2011-09-30", "2011-11-03", "q3", "10-Q", "Q3")]
    quarterly = XBRLParser().extract_quarterly_financials(_facts("2011-02-25", 0.01, 0.04, quarters))
    assert quarterly["2011-12-31"]["dividends_per_share"] == pytest.approx(0.01)


def test_a_figure_filed_on_its_periods_last_day_is_read():
    # The edge: filed the day the period ends is not ahead of it.
    facts = _facts("2011-02-25", 0.01, 0.04)
    facts["facts"]["us-gaap"][DECLARED]["units"]["USD/shares"].append(
        _fact(0.04, "2011-01-01", "2011-12-31", "2011-12-31", "k11a"))
    record = build_annual_record(XBRLParser(), facts, ticker="X", years_back=None)
    assert record.periods["2011"]["_source_tags"]["dividends_per_share"] == DECLARED
    assert record.periods["2011"]["_source_filings"]["dividends_per_share"]["accn"] == "k11a"
