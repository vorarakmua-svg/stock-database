"""Debt on a filing's own balance sheet (debt1): a note's total assets don't date a balance sheet that the filing's
total dates (BUG-273), the current-liabilities bound tells nothing where the borrowings alone overflow the other lines
(BUG-286), and a 10-Q's current notes under a concept short-term debt doesn't read are its short-term debt where its
year-end column is the annual record's (BUG-292)."""

import pytest

from src.parsers.annual_snapshots import build_snapshots
from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


def _filings(*filings):
    """Company facts of filings, each ``(form, filed, end, {tag: value})``: what that filing tags on *end*."""
    us_gaap = {}
    for form, filed, end, tagged in filings:
        filing = dict(fy=int(filed[:4]), fp="FY" if form.startswith("10-K") else "Q1", form=form, filed=filed)
        for tag, val in tagged.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, None, end, **filing))
    return {"facts": {"us-gaap": us_gaap}}


def _read(period, field):
    return period.get(field), period.get("_source_tags", {}).get(field)


# ---------------- BUG-273: a note's total assets at an earlier year end ----------------

_BS = {"LiabilitiesAndStockholdersEquity": 8.0e9, "Assets": 8.0e9}


def _chd():
    # CHD: its FY2021 10-K's balance sheet shows $1.611B of noncurrent debt; its FY2023 10-K tags total assets at
    # 2021-12-31 in a note (no balance-sheet total there) and LongTermDebtNoncurrent $2.202B, its 2023 figure, there.
    return _filings(
        ("10-K", "2022-02-17", "2021-12-31", {**_BS, "LongTermDebtNoncurrent": 1.611e9, "DebtCurrent": 0.952e9}),
        ("10-K", "2024-02-15", "2023-12-31", {**_BS, "LongTermDebtNoncurrent": 2.202e9}),
        ("10-K", "2024-02-15", "2022-12-31", {**_BS, "LongTermDebtNoncurrent": 2.599e9}),
        ("10-K", "2024-02-15", "2021-12-31", {"Assets": 8.0e9, "LongTermDebtNoncurrent": 2.202e9}))


def test_a_notes_total_assets_dont_date_a_filing_that_tags_its_balance_sheets_total():
    years = XBRLParser().extract_annual_financials(_chd())
    assert _read(years["2021"], "long_term_debt") == (1.611e9, "LongTermDebtNoncurrent")
    assert _read(years["2023"], "long_term_debt") == (2.202e9, "LongTermDebtNoncurrent")


def test_every_snapshot_and_vintage_keeps_the_years_own_balance_sheet():
    snaps = build_snapshots(XBRLParser(), _chd(), ticker="X", years_back=None)
    rows = [row for row in snaps.rows if row["fiscal_year"] == 2021 and row["present"]]
    assert rows and all(row["long_term_debt"] == 1.611e9 for row in rows)
    assert all(view.get("long_term_debt") in (None, 1.611e9) for view in snaps.vintages["2021"].values())


def test_a_filing_that_tags_only_total_assets_is_still_dated_by_them():
    # BUG-319's date rule stands where a filing tags no balance-sheet total: its total assets date it.
    facts = _filings(("10-K", "2022-02-17", "2021-12-31", {"Assets": 8.0e9, "LongTermDebtNoncurrent": 1.611e9}),
                     ("10-K", "2022-02-17", "2021-03-02", {"LongTermDebt": 0.5e9}))
    assert _read(XBRLParser().extract_annual_financials(facts)["2021"], "long_term_debt") == (
        1.611e9, "LongTermDebtNoncurrent")


def test_an_adoption_days_opening_balance_sheet_days_from_the_total_is_still_dated():
    # CVS's 10-Qs tag their 2019-01-01 opening balance sheet (ASC 842) with total assets alone, a day after the year
    # end their balance sheets' total dates: the adoption day keeps its figures.
    facts = _filings(("10-Q", "2019-05-01", "2018-12-31", {**_BS, "LongTermDebtNoncurrent": 71.444e9}),
                     ("10-Q", "2019-05-01", "2019-01-01", {"Assets": 9.0e9, "LongTermDebtNoncurrent": 71.348e9}),
                     ("10-Q", "2019-05-01", "2019-03-31", {**_BS, "LongTermDebtNoncurrent": 71.0e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2019-01-01"]
    assert _read(quarter, "long_term_debt") == (71.348e9, "LongTermDebtNoncurrent")


# ---------------- BUG-286: the current-liabilities bound where the other lines overlap ----------------

# Balance sheet, $M: payables and accrued expenses 1,000 | short-term borrowings 200 | current maturities 150 |
# other 20 | current liabilities 1,370; short-term debt is 200 + 150 (review 22, M-1).
_LINES = dict(LiabilitiesAndStockholdersEquity=9.0e9, ShortTermBorrowings=0.200e9, LongTermDebtCurrent=0.150e9,
              LongTermDebtNoncurrent=3.0e9, LiabilitiesCurrent=1.370e9)


def _short_term(**tagged):
    facts = _filings(("10-K", "2026-02-20", "2025-12-31", {**_LINES, **tagged}))
    return XBRLParser().extract_annual_financials(facts)["2025"]["short_term_debt"]


def test_other_lines_the_borrowings_alone_overflow_tell_nothing():
    # The combined line tagged AccountsPayableCurrent with its note's compensation and taxes inside it: 1,000 + 160
    # + 60 beside the 200 of borrowings is already above 1,370, so the lines overlap and the bound doesn't fire.
    assert _short_term(AccountsPayableCurrent=1.000e9, EmployeeRelatedLiabilitiesCurrent=0.160e9,
                       AccruedIncomeTaxesCurrent=0.060e9) == pytest.approx(0.350e9)


def test_the_bound_still_fires_where_the_borrowings_fit_and_both_lines_dont():
    # Payables 1,100 beside 200 of borrowings fit in 1,370; with the 150 of maturities they don't: the borrowings
    # hold them (BUG-267).
    assert _short_term(AccountsPayableCurrent=1.100e9) == pytest.approx(0.200e9)


def test_the_bound_fires_with_the_borrowings_at_the_current_liabilities_rounding():
    # 1,170 + 200 = 1,370 exactly: the borrowings fit, so the maturities are inside them.
    assert _short_term(AccountsPayableCurrent=1.170e9) == pytest.approx(0.200e9)


# ---------------- BUG-292: a 10-Q's current notes under a concept short-term debt doesn't read ----------------

def _orcl(**quarter):
    # ORCL's 10-K tags its current notes as DebtCurrent and NotesPayableCurrent ($7.271B at 2025-05-31); its 10-Q
    # tags them as NotesPayableCurrent alone, at the year end's column and at the quarter's ($9.079B at 2025-08-31).
    return _filings(
        ("10-K", "2025-06-18", "2025-05-31", {**_BS, "DebtCurrent": 7.271e9, "NotesPayableCurrent": 7.271e9,
                                              "LongTermNotesPayable": 85.297e9}),
        ("10-Q", "2025-09-10", "2025-05-31", {**_BS, "NotesPayableCurrent": 7.271e9}),
        ("10-Q", "2025-09-10", "2025-08-31", {**_BS, "NotesPayableCurrent": 9.079e9, "LongTermNotesPayable": 82.236e9,
                                              **quarter}))


def test_a_10qs_current_notes_the_10k_shows_as_its_current_debt_are_the_quarters():
    quarter = XBRLParser().extract_quarterly_financials(_orcl())["2025-08-31"]
    assert _read(quarter, "short_term_debt") == (9.079e9, "NotesPayableCurrent")


def test_a_quarter_that_reads_its_own_short_term_debt_keeps_it():
    quarter = XBRLParser().extract_quarterly_financials(_orcl(DebtCurrent=9.5e9))["2025-08-31"]
    assert _read(quarter, "short_term_debt") == (9.5e9, "DebtCurrent")


def test_a_quarter_with_commercial_paper_reads_no_line():
    quarter = XBRLParser().extract_quarterly_financials(_orcl(CommercialPaper=1.0e9))["2025-08-31"]
    assert _read(quarter, "short_term_debt") == (None, None)


@pytest.mark.parametrize("total", ["DebtLongtermAndShorttermCombinedAmount",
                                   "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"])
def test_a_quarter_reading_a_total_with_current_debt_reads_no_line_beside_it(total):
    # ILMN's 10-Q for 2015-03-29: its own total, $1.301B, holds its $0.307B of ConvertibleDebtCurrent.
    facts = _filings(
        ("10-K", "2015-02-18", "2014-12-28", {**_BS, "DebtCurrent": 0.304e9, "ConvertibleDebtCurrent": 0.304e9,
                                              "LongTermDebtNoncurrent": 0.987e9}),
        ("10-Q", "2015-05-01", "2014-12-28", {**_BS, "ConvertibleDebtCurrent": 0.304e9}),
        ("10-Q", "2015-05-01", "2015-03-29", {**_BS, "ConvertibleDebtCurrent": 0.307e9, total: 1.301e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2015-03-29"]
    assert _read(quarter, "long_term_debt") == (1.301e9, total)
    assert _read(quarter, "short_term_debt") == (None, None)


def test_a_current_line_the_annual_reports_dont_show_as_the_current_debt_isnt_read():
    # A 10-Q's notes the year end shows apart from the 10-K's current debt are another line (a part).
    facts = _orcl()
    facts["facts"]["us-gaap"]["NotesPayableCurrent"]["units"]["USD"][1]["val"] = 1.0e9
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-08-31"]
    assert _read(quarter, "short_term_debt") == (None, None)


def test_lines_that_disagree_on_the_quarter_are_no_line():
    facts = _orcl(LoansPayableCurrent=1.0e9)
    facts["facts"]["us-gaap"]["LoansPayableCurrent"]["units"]["USD"].append(
        usd(7.271e9, None, "2025-05-31", fy=2025, fp="Q1", form="10-Q", filed="2025-09-10"))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-08-31"]
    assert _read(quarter, "short_term_debt") == (None, None)


def test_a_10ks_note_figure_on_a_quarter_end_is_no_quarters_line():
    # Only a 10-Q's balance sheet shows a quarter: a 10-K's current notes tagged at a quarter end are a note's.
    facts = _orcl()
    facts["facts"]["us-gaap"]["NotesPayableCurrent"]["units"]["USD"].append(
        usd(8.0e9, None, "2025-02-28", fy=2025, fp="FY", form="10-K", filed="2025-06-18"))
    for tag in ("LiabilitiesAndStockholdersEquity", "Assets"):
        facts["facts"]["us-gaap"][tag]["units"]["USD"].append(
            usd(8.0e9, None, "2025-02-28", fy=2025, fp="Q3", form="10-Q", filed="2025-03-10"))
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert _read(quarters["2025-02-28"], "short_term_debt") == (None, None)


@pytest.mark.parametrize("tag, line", [("NotesPayableCurrent", True), ("SeniorNotesCurrent", True),
                                       ("ConvertibleDebtCurrent", True), ("DebtCurrent", False),
                                       ("CommercialPaper", False), ("LongTermDebtNoncurrent", False),
                                       ("DebtInstrumentFairValueCurrent", False), ("AccountsPayableCurrent", False),
                                       ("OperatingLeaseLiabilityCurrent", False)])
def test_the_current_debt_concepts_a_line_can_carry(tag, line):
    from src.parsers.xbrl_parser import _current_line_concept
    assert _current_line_concept(tag) is line
