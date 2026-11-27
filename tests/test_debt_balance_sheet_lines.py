"""Debt on a filing's own balance sheet (debt1): a note's total assets don't date a balance sheet that the filing's
total dates (BUG-273), the current-liabilities bound tells nothing where the borrowings alone overflow the other lines
(BUG-286), and a 10-Q's current notes under a concept short-term debt doesn't read are its short-term debt where its
year-end column is the annual record's (BUG-292), except beside a long-term debt total that holds
them (BUG-383)."""

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


# ---------------- BUG-383: a quarter reading a long-term debt total that holds the current line ----------------

def _debt(quarter):
    return (quarter.get("long_term_debt") or 0) + (quarter.get("short_term_debt") or 0)


@pytest.mark.parametrize("total", [91.315e9, 91.0e9])
def test_a_quarter_reading_a_long_term_debt_total_reads_no_current_line_beside_it(total):
    # Code review 38's probe: ORCL's shape, with the 10-Q's LongTermDebt total, $91.315B, holding its $9.079B of
    # NotesPayableCurrent beside its $82.236B of LongTermNotesPayable. Long-term debt reads the total, which holds the
    # line: read beside it too, the current notes count twice ($100.394B). A total rounded below the two (0.35%) too.
    quarter = XBRLParser().extract_quarterly_financials(_orcl(LongTermDebt=total))["2025-08-31"]
    assert _read(quarter, "long_term_debt") == (total, "LongTermDebt")
    assert _read(quarter, "short_term_debt") == (None, None)
    assert _debt(quarter) == pytest.approx(total)


def test_a_long_term_debt_total_the_10q_tags_no_other_figure_beside_keeps_the_line():
    # Nothing shows the total holds the current notes: it may be the noncurrent figure, as BIIB's is.
    facts = _orcl(LongTermDebt=82.236e9)
    del facts["facts"]["us-gaap"]["LongTermNotesPayable"]
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-08-31"]
    assert _read(quarter, "long_term_debt") == (82.236e9, "LongTermDebt")
    assert _read(quarter, "short_term_debt") == (9.079e9, "NotesPayableCurrent")


def test_amgens_quarter_reads_no_current_notes_beside_its_long_term_debt_total():
    # AMGN's 10-Q for 2014-03-31: LongTermDebt $32.024B = LongTermNotesPayable $29.519B + NotesPayableCurrent $2.505B;
    # its 10-K's year end shows the notes as LongTermDebtCurrent, which the 10-Q's year-end column tags them as.
    facts = _filings(
        ("10-K", "2014-02-24", "2013-12-31", {**_BS, "LongTermDebtCurrent": 2.505e9,
                                              "LongTermDebtNoncurrent": 29.623e9}),
        ("10-Q", "2014-04-30", "2013-12-31", {**_BS, "NotesPayableCurrent": 2.505e9}),
        ("10-Q", "2014-04-30", "2014-03-31", {**_BS, "NotesPayableCurrent": 2.505e9, "LongTermNotesPayable": 29.519e9,
                                              "LongTermDebt": 32.024e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2014-03-31"]
    assert _read(quarter, "long_term_debt") == (32.024e9, "LongTermDebt")
    assert _read(quarter, "short_term_debt") == (None, None)


def _equinix(**quarter):
    # EQIX's 10-Ks show its current senior notes as SeniorNotesCurrent, the annual record's LongTermDebtCurrent
    # ($1.199B at 2024-12-31); its 10-Q for 2025-09-30 tags its note's LongTermDebt total, $17.328B, which holds the
    # $0.699B of SeniorNotesCurrent beside $15.789B of SeniorLongTermNotes, $0.704B of loans and the rest.
    return _filings(
        ("10-K", "2025-02-12", "2024-12-31", {**_BS, "LongTermDebtCurrent": 1.199e9, "SeniorNotesCurrent": 1.199e9,
                                              "LongTermDebtNoncurrent": 14.135e9}),
        ("10-Q", "2025-10-29", "2024-12-31", {**_BS, "SeniorNotesCurrent": 1.199e9}),
        ("10-Q", "2025-10-29", "2025-09-30", {**_BS, "SeniorNotesCurrent": 0.699e9, "SeniorLongTermNotes": 15.789e9,
                                              "LongTermNotesAndLoans": 0.687e9, **quarter}))


@pytest.mark.parametrize("total", ["LongTermDebt", "NotesPayable"])
def test_equinixs_quarter_reads_no_current_notes_beside_its_debt_total(total):
    quarter = XBRLParser().extract_quarterly_financials(_equinix(**{total: 17.328e9}))["2025-09-30"]
    assert _read(quarter, "long_term_debt") == (17.328e9, total)
    assert _read(quarter, "short_term_debt") == (None, None)


def test_a_long_term_debt_total_the_line_and_its_noncurrent_figure_overflow_keeps_the_line():
    # The 10-Q's noncurrent notes ($16.8B) and the current ones ($0.699B) are more than its $17.328B total: the total
    # is the noncurrent figure (another of its lines'), which doesn't hold the current notes.
    quarter = XBRLParser().extract_quarterly_financials(_equinix(
        LongTermDebt=17.328e9, SeniorLongTermNotes=16.8e9))["2025-09-30"]
    assert _read(quarter, "short_term_debt") == (0.699e9, "SeniorNotesCurrent")


@pytest.mark.parametrize("part", [{}, {"LongTermNotesAndLoans": 1.0e9}])
def test_biogens_noncurrent_long_term_debt_keeps_its_current_notes(part):
    # BIIB's 10-Q for 2024-03-31: LongTermDebt $6.290B is the noncurrent notes, NotesPayable $6.540B their total with
    # the $0.250B of NotesPayableCurrent: the line sits beside the long-term debt, not in it. The largest figure beside
    # the total decides, not a part of it that the line would fit beside (a $1.0B loan).
    facts = _filings(
        ("10-K", "2024-02-14", "2023-12-31", {**_BS, "LongTermDebtCurrent": 1.749e9, "NotesPayableCurrent": 1.749e9,
                                              "LongTermDebtNoncurrent": 4.543e9}),
        ("10-Q", "2024-04-24", "2023-12-31", {**_BS, "NotesPayableCurrent": 1.749e9}),
        ("10-Q", "2024-04-24", "2024-03-31", {**_BS, "NotesPayableCurrent": 0.250e9, "LongTermDebt": 6.2901e9,
                                              "NotesPayable": 6.5401e9, **part}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2024-03-31"]
    assert _read(quarter, "long_term_debt") == (6.2901e9, "LongTermDebt")
    assert _read(quarter, "short_term_debt") == (0.250e9, "NotesPayableCurrent")


def test_a_noncurrent_long_term_debt_figure_keeps_the_line_whatever_fits_inside_it():
    # ORCL's quarter reads its LongTermNotesPayable, noncurrent: the current notes fit beside a $1.0B loan inside it,
    # but a noncurrent figure holds no current debt.
    quarter = XBRLParser().extract_quarterly_financials(_orcl(LongTermNotesAndLoans=1.0e9))["2025-08-31"]
    assert _read(quarter, "long_term_debt") == (82.236e9, "LongTermNotesPayable")
    assert _read(quarter, "short_term_debt") == (9.079e9, "NotesPayableCurrent")


def test_a_zero_current_line_beside_a_total_is_still_read():
    # BIIB's 10-Q for 2020-06-30: no current notes (NotesPayableCurrent $0) beside its LongTermDebt, equal to its
    # NotesPayable total, with another debt figure the 10-Q tags: a zero counts nothing
    # twice, so the quarter keeps its short-term debt of 0.
    facts = _filings(
        ("10-K", "2020-02-06", "2019-12-31", {**_BS, "LongTermDebtCurrent": 1.5e9, "NotesPayableCurrent": 1.5e9,
                                              "LongTermDebtNoncurrent": 4.459e9}),
        ("10-Q", "2020-07-22", "2019-12-31", {**_BS, "NotesPayableCurrent": 1.5e9}),
        ("10-Q", "2020-07-22", "2020-06-30", {**_BS, "NotesPayableCurrent": 0.0, "LongTermDebt": 7.43e9,
                                              "NotesPayable": 7.43e9, "SeniorLongTermNotes": 5.0e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2020-06-30"]
    assert _read(quarter, "short_term_debt") == (0.0, "NotesPayableCurrent")


def test_a_notes_payable_figure_equal_to_the_debt_total_is_no_other_figure_beside_it():
    # BUG-384: review 38's ORCL shape (LongTermDebt $91.315B = LongTermNotesPayable $82.236B +
    # NotesPayableCurrent $9.079B), the 10-Q also tagging the same total as NotesPayable. holds() took the largest
    # figure beside the total, which was the NotesPayable total itself ($91.315B), so line + it overflows and the
    # current notes are read again beside the total that holds them.
    facts = _filings(
        ("10-K", "2025-06-18", "2025-05-31", {**_BS, "DebtCurrent": 7.271e9, "NotesPayableCurrent": 7.271e9,
                                              "LongTermNotesPayable": 85.297e9}),
        ("10-Q", "2025-09-10", "2025-05-31", {**_BS, "NotesPayableCurrent": 7.271e9}),
        ("10-Q", "2025-09-10", "2025-08-31", {**_BS, "NotesPayableCurrent": 9.079e9, "LongTermNotesPayable": 82.236e9,
                                              "LongTermDebt": 91.315e9, "NotesPayable": 91.315e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-08-31"]
    debt = (quarter.get("long_term_debt") or 0) + (quarter.get("short_term_debt") or 0)
    assert abs(debt - 91.315e9) < 1e6, (quarter.get("long_term_debt"), quarter.get("short_term_debt"),
                                        quarter.get("_source_tags", {}).get("long_term_debt"),
                                        quarter.get("_source_tags", {}).get("short_term_debt"))


def test_a_notes_payable_figure_within_rounding_of_the_debt_total_is_no_other_figure_either():
    # the total rounded differently in its two tags ($91.315B and $91.4B, within _AGREE): still the total tagged twice
    facts = _filings(
        ("10-K", "2025-06-18", "2025-05-31", {**_BS, "DebtCurrent": 7.271e9, "NotesPayableCurrent": 7.271e9,
                                              "LongTermNotesPayable": 85.297e9}),
        ("10-Q", "2025-09-10", "2025-05-31", {**_BS, "NotesPayableCurrent": 7.271e9}),
        ("10-Q", "2025-09-10", "2025-08-31", {**_BS, "NotesPayableCurrent": 9.079e9, "LongTermNotesPayable": 82.236e9,
                                              "LongTermDebt": 91.315e9, "NotesPayable": 91.4e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-08-31"]
    assert abs(((quarter.get("long_term_debt") or 0) + (quarter.get("short_term_debt") or 0)) - 91.315e9) < 1e6
