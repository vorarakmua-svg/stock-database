"""A DebtCurrent that is the filing's total debt (BUG-254): HIG's FY2013 10-K tags its whole debt, the noncurrent
long-term debt and the short-term borrowings together, as DebtCurrent, so short-term debt read it beside the
long-term debt it holds and total debt was about twice the filer's own."""

import pytest

from src.parsers.annual_snapshots import build_snapshots
from src.parsers.calculated_metrics import CalculatedMetrics
from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd

_K10 = dict(fy=2013, fp="FY", form="10-K", filed="2014-02-28")
_Q10 = dict(fy=2011, fp="Q2", form="10-Q", filed="2011-03-01")


def _facts(end="2012-12-31", filing=_K10, **tagged):
    """A balance sheet on *end* (its total tagged beside) holding the given tags (tag=value)."""
    us_gaap = {tag: {"units": {"USD": [usd(val, None, end, **filing)]}} for tag, val in tagged.items()}
    us_gaap["LiabilitiesAndStockholdersEquity"] = {"units": {"USD": [usd(300.0e9, None, end, **filing)]}}
    return {"facts": {"us-gaap": us_gaap}}


def _year(facts, fy="2012"):
    return XBRLParser().extract_annual_financials(facts)[fy]


def _debt(period):
    tags = period.get("_source_tags", {})
    return (period.get("long_term_debt"), tags.get("long_term_debt"), period.get("short_term_debt"),
            tags.get("short_term_debt"))


def test_higs_debt_current_holding_the_long_term_debt_and_borrowings_isnt_short_term_debt():
    # HIG's FY2013 10-K at 2012-12-31: DebtCurrent $7.126B = $6.806B of long-term debt + $0.320B of borrowings.
    year = _year(_facts(DebtCurrent=7.126e9, LongTermDebtNoncurrent=6.806e9, ShortTermBorrowings=0.320e9))
    assert _debt(year) == (6.806e9, "LongTermDebtNoncurrent", 0.320e9, "ShortTermBorrowings")
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(7.126e9)


@pytest.mark.parametrize("maturities", ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"])
def test_a_debt_current_holding_the_long_term_debt_and_its_maturities_isnt_short_term_debt(maturities):
    # CSCO's 10-Q for 2011-01-29: DebtCurrent $15.241B = $12.152B noncurrent + $3.089B of maturities.
    facts = _facts(end="2011-01-29", filing=_Q10, DebtCurrent=15.241e9, LongTermDebtNoncurrent=12.152e9,
                   **{maturities: 3.089e9})
    quarter = XBRLParser().extract_quarterly_financials(facts)["2011-01-29"]
    assert _debt(quarter) == (12.152e9, "LongTermDebtNoncurrent", 3.089e9, maturities)
    assert CalculatedMetrics().calculate_all(quarter)["total_debt"] == pytest.approx(15.241e9)


@pytest.mark.parametrize("noncurrent", ["LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable",
                                        "UnsecuredLongTermDebt", "ConvertibleLongTermNotesPayable"])
def test_the_noncurrent_figure_under_any_of_its_tags(noncurrent):
    year = _year(_facts(DebtCurrent=0.200e9, LongTermDebtCurrent=0.050e9, **{noncurrent: 0.150e9}))
    assert _debt(year)[2:] == (0.050e9, "LongTermDebtCurrent")


def test_a_short_term_debt_that_equals_the_sum_only_within_half_a_percent_is_read():
    # GOOGL's FY2015: $3.225B of short-term debt (its paper and the maturities), $1.995B of long-term debt and
    # $1.225B of maturities: 0.16% from their sum, by chance.
    year = _year(_facts(DebtCurrent=3.225e9, LongTermDebtNoncurrent=1.995e9,
                        LongTermDebtAndCapitalLeaseObligationsCurrent=1.225e9))
    assert _debt(year)[2:] == (3.225e9, "DebtCurrent")


def test_a_sum_off_by_the_rounding_of_its_figures_is_the_total():
    # Figures rounded to the $0.5M apart: $6.8055B + $0.320B for a total of $7.126B.
    year = _year(_facts(DebtCurrent=7.126e9, LongTermDebtNoncurrent=6.8055e9, ShortTermBorrowings=0.320e9))
    assert _debt(year)[2:] == (0.320e9, "ShortTermBorrowings")


def test_a_debt_current_equal_to_the_noncurrent_debt_alone_is_read():
    # Notes moved to current debt can be tagged at both on the date (TER's FY2013): nothing says it is a total.
    year = _year(_facts(DebtCurrent=0.187e9, LongTermDebtNoncurrent=0.187e9))
    assert _debt(year)[2:] == (0.187e9, "DebtCurrent")


def test_a_current_figure_of_zero_makes_no_total():
    year = _year(_facts(DebtCurrent=0.187e9, LongTermDebtNoncurrent=0.187e9, ShortTermBorrowings=0.0))
    assert _debt(year)[2:] == (0.187e9, "DebtCurrent")


def test_another_filings_figures_make_no_total():
    # The sum is the filing's own: an earlier 10-K's long-term debt and borrowings on the date tell nothing of a later
    # filing's DebtCurrent.
    us_gaap = _facts(LongTermDebtNoncurrent=6.806e9, ShortTermBorrowings=0.320e9,
                     filing=dict(fy=2012, fp="FY", form="10-K", filed="2013-03-01"))["facts"]["us-gaap"]
    us_gaap["DebtCurrent"] = {"units": {"USD": [usd(7.126e9, None, "2012-12-31", **_K10)]}}
    us_gaap["LiabilitiesAndStockholdersEquity"]["units"]["USD"].append(usd(300.0e9, None, "2012-12-31", **_K10))
    year = _year({"facts": {"us-gaap": us_gaap}})
    assert _debt(year)[2:] == (7.126e9, "DebtCurrent")


def test_every_record_and_snapshot_reads_the_current_figure():
    facts = _facts(DebtCurrent=7.126e9, LongTermDebtNoncurrent=6.806e9, ShortTermBorrowings=0.320e9)
    vintage = XBRLParser().extract_annual_vintages(facts)["2012"]["2014-02-28"]
    assert _debt(vintage)[2:] == (0.320e9, "ShortTermBorrowings")
    quarter = XBRLParser().extract_quarterly_financials(facts)["2012-12-31"]
    assert _debt(quarter)[2:] == (0.320e9, "ShortTermBorrowings")
    rows = [row for row in build_snapshots(XBRLParser(), facts, ticker="X", years_back=None).rows
            if row["fiscal_year"] == 2012]
    assert [(row["known_from"], row.get("short_term_debt")) for row in rows] == [("2014-02-28", 0.320e9)]
