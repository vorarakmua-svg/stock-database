"""Tags the canonical mapping reads last, for filers that tag nothing earlier in a field's order (BUG-364, BUG-203,
BUG-221): the totals with current debt as long-term debt, the lease-inclusive PP&E, and the current securities as
short-term investments. Each is read only where no tag the field read before is filed for the period, and a
bank's MarketableSecurities, its whole securities book, not at all (Q20)."""

import pytest

from src.analysis.coverage import REPORTED, analyze
from src.parsers.annual_record import build_annual_record
from src.parsers.annual_snapshots import build_snapshots
from src.parsers.calculated_metrics import CalculatedMetrics
from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd
from tests.test_fetcher_concurrency import _collect_from_sec

_K10 = dict(fy=2025, fp="FY", form="10-K", filed="2026-02-12")
_Q10 = dict(fy=2026, fp="Q1", form="10-Q", filed="2026-05-05")
_WITH_CURRENT = "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"
_COMBINED = "DebtLongtermAndShorttermCombinedAmount"
_LEASE_PPE = "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"


def _facts(end="2025-12-31", filing=_K10, **tagged):
    """A balance sheet on *end* (its total tagged beside) holding the given tags (tag=value)."""
    us_gaap = {tag: {"units": {"USD": [usd(val, None, end, **filing)]}} for tag, val in tagged.items()}
    us_gaap["LiabilitiesAndStockholdersEquity"] = {"units": {"USD": [usd(300.0e9, None, end, **filing)]}}
    return {"facts": {"us-gaap": us_gaap}}


def _year(**tagged):
    return XBRLParser().extract_annual_financials(_facts(**tagged)).get("2025", {})


def _read(period, field):
    return period.get(field), period.get("_source_tags", {}).get(field)


# ---------------- long-term debt: the totals with current debt, last ----------------

def test_gms_total_with_current_maturities_is_its_debt():
    # BUG-364: GM tags its debt only as LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities ($131.574B for
    # FY2025), which no field read, so no year had a debt figure and its EV/EBITDA counted net debt as 0 (4.88 against
    # Yahoo's 8.96).
    year = _year(**{_WITH_CURRENT: 131.574e9, "CashAndCashEquivalentsAtCarryingValue": 20.945e9,
                    "AvailableForSaleSecuritiesDebtSecuritiesCurrent": 6.724e9})
    assert _read(year, "long_term_debt") == (131.574e9, _WITH_CURRENT)
    metrics = CalculatedMetrics().calculate_all(year)
    assert metrics["total_debt"] == pytest.approx(131.574e9)
    assert metrics["net_debt"] == pytest.approx(131.574e9 - 20.945e9 - 6.724e9)


def test_pgrs_combined_debt_is_its_debt():
    # BUG-203: PGR files its debt as DebtLongtermAndShorttermCombinedAmount ($6.897B for FY2025), which no field read,
    # so its debt read unknown (BUG-189) and TRV's likewise.
    year = _year(**{_COMBINED: 6.897e9})
    assert _read(year, "long_term_debt") == (6.897e9, _COMBINED)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(6.897e9)


@pytest.mark.parametrize("tag", ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
                                 "LongTermNotesPayable", "UnsecuredLongTermDebt", "ConvertibleLongTermNotesPayable",
                                 "NotesPayable"])
@pytest.mark.parametrize("total", [_WITH_CURRENT, _COMBINED])
def test_a_tag_long_term_debt_read_before_outranks_the_totals(tag, total):
    # The totals hold the current debt the short-term figure reads, so a filer whose noncurrent line or total the
    # field already read keeps it: JPM's FY2013 LongTermDebt, TRV's FY2014 LongTermDebtNoncurrent.
    assert _read(_year(**{tag: 60.0e9, total: 63.0e9}), "long_term_debt") == (60.0e9, tag)


def test_the_total_with_current_maturities_outranks_the_combined_debt():
    # Kenvue tags both ($7.821B and its $8.524B with commercial paper, FY2025): the narrower total first, as its
    # short-term debt holds the paper.
    year = _year(**{_WITH_CURRENT: 7.821e9, _COMBINED: 8.524e9})
    assert _read(year, "long_term_debt") == (7.821e9, _WITH_CURRENT)


def test_a_quarter_and_a_vintage_read_the_total_too():
    facts = _facts(end="2026-03-31", filing=_Q10, **{_WITH_CURRENT: 133.0e9})
    quarter = XBRLParser().extract_quarterly_financials(facts)["2026-03-31"]
    assert _read(quarter, "long_term_debt") == (133.0e9, _WITH_CURRENT)
    vintage = XBRLParser().extract_annual_vintages(_facts(**{_COMBINED: 6.897e9}))["2025"]["2026-02-12"]
    assert _read(vintage, "long_term_debt") == (6.897e9, _COMBINED)


@pytest.mark.parametrize("current", ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"])
def test_a_total_with_maturities_beside_them_reads_the_total_less_them(current):
    # Kenvue's FY2025: $7.821B with the $750M of maturities its $1.453B of DebtCurrent holds; read whole beside it,
    # total debt was $9.274B for its own $8.524B. The total less the maturities the filing tags is the noncurrent
    # debt (Q23).
    year = _year(**{_WITH_CURRENT: 7.821e9, current: 0.750e9})
    assert _read(year, "long_term_debt") == (pytest.approx(7.071e9), _WITH_CURRENT)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(7.821e9)


def test_a_total_with_maturities_beside_current_debt_alone_isnt_read():
    # A DebtCurrent can hold borrowings besides the maturities, so it is no current part of the long-term debt to take
    # from the total: with no maturities tagged the total stays unread there.
    year = _year(**{_WITH_CURRENT: 7.821e9, "DebtCurrent": 0.750e9})
    assert _read(year, "long_term_debt") == (None, None)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(0.750e9)


@pytest.mark.parametrize("current", ["DebtCurrent", "ShortTermBorrowings", "CommercialPaper", "LongTermDebtCurrent",
                                     "LongTermDebtAndCapitalLeaseObligationsCurrent"])
def test_the_combined_debt_beside_current_debt_reads_the_total_less_it(current):
    # TRV's FY2025: $9.267B of debt, its $300M of DebtCurrent among it: $8.967B is the rest (Q23), and total debt is
    # the filer's own.
    year = _year(**{_COMBINED: 9.267e9, current: 0.300e9})
    assert _read(year, "long_term_debt") == (pytest.approx(8.967e9), _COMBINED)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(9.267e9)


@pytest.mark.parametrize("total", [_WITH_CURRENT, _COMBINED])
def test_a_total_beside_current_debt_of_zero_is_read(total):
    year = _year(**{total: 6.897e9, "DebtCurrent": 0.0, "LongTermDebtCurrent": 0.0})
    assert _read(year, "long_term_debt") == (6.897e9, total)


def test_a_total_with_maturities_beside_borrowings_it_doesnt_hold_is_read():
    # JPM tags its long-term debt with the maturities ($435.206B, FY2025) beside short-term borrowings, a line of their
    # own: the two make its debt.
    year = _year(**{_WITH_CURRENT: 435.206e9, "ShortTermBorrowings": 64.776e9})
    assert _read(year, "long_term_debt") == (435.206e9, _WITH_CURRENT)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(499.982e9)


def _two_10ks(total, later_current):
    """A total on 2024-12-31 in the FY2024 10-K alone, and the FY2025 10-K's comparative column there with a current
    figure beside the same total."""
    us_gaap = {"LiabilitiesAndStockholdersEquity": {"units": {"USD": [
        usd(300.0e9, None, "2024-12-31", fy=2024, fp="FY", form="10-K", filed="2025-02-12"),
        usd(300.0e9, None, "2024-12-31", **_K10), usd(300.0e9, None, "2025-12-31", **_K10)]}},
        total: {"units": {"USD": [usd(6.0e9, None, "2024-12-31", fy=2024, fp="FY", form="10-K", filed="2025-02-12"),
                                  usd(6.0e9, None, "2024-12-31", **_K10)]}},
        later_current: {"units": {"USD": [usd(0.5e9, None, "2024-12-31", **_K10)]}}}
    return {"facts": {"us-gaap": us_gaap}}


#: Each total's long-term debt beside the later 10-K's $0.5B of DebtCurrent on 2024-12-31 (``_two_10ks``): the own
#: total less it (Q23); the total with maturities stays unread, as a DebtCurrent is no current part of it.
_LESS_DEBT_CURRENT = {_WITH_CURRENT: None, _COMBINED: 5.5e9}


@pytest.mark.parametrize("total", [_WITH_CURRENT, _COMBINED])
def test_a_date_with_current_debt_in_any_report_leaves_the_records_total_unread_but_not_an_earlier_vintage(total):
    # The annual record reads the short-term debt from whichever report tags it, so a total one report tags alone
    # would sit beside another report's current debt on the date; a vintage is its own filing's view. The later
    # report, which tags its current debt beside the total, has its own total less it read.
    facts = _two_10ks(total, "DebtCurrent")
    less = _LESS_DEBT_CURRENT[total]
    assert _read(XBRLParser().extract_annual_financials(facts)["2024"], "long_term_debt") == (
        (less, total) if less else (None, None))
    vintages = XBRLParser().extract_annual_vintages(facts)["2024"]
    assert _read(vintages["2025-02-12"], "long_term_debt") == (6.0e9, total)
    assert _read(vintages["2026-02-12"], "long_term_debt") == ((less, total) if less else (None, None))


@pytest.mark.parametrize("total", [_WITH_CURRENT, _COMBINED])
def test_a_later_reports_current_debt_of_zero_leaves_the_total_read(total):
    # Current debt of zero beside a total holds nothing of it, so the earlier report's total is read whole, though
    # that report tags no current part to take.
    facts = _two_10ks(total, "DebtCurrent")
    us_gaap = facts["facts"]["us-gaap"]
    us_gaap[total]["units"]["USD"].pop()
    us_gaap["DebtCurrent"]["units"]["USD"][0]["val"] = 0.0
    assert _read(XBRLParser().extract_annual_financials(facts)["2024"], "long_term_debt") == (6.0e9, total)


@pytest.mark.parametrize("total", [_WITH_CURRENT, _COMBINED])
def test_a_total_whose_filing_tags_no_current_part_stays_unread_beside_anothers(total):
    # Q23 takes the current part the filing that tags the total tags on the date: the FY2024 10-K's total beside the
    # FY2025 10-K's current debt (a later report that doesn't repeat the total) is set aside, not read less a figure
    # another filing tags.
    facts = _two_10ks(total, "LongTermDebtCurrent")
    later = facts["facts"]["us-gaap"][total]["units"]["USD"]
    later[:] = [e for e in later if e["filed"] != _K10["filed"]]
    assert _read(XBRLParser().extract_annual_financials(facts)["2024"], "long_term_debt") == (None, None)
    vintages = XBRLParser().extract_annual_vintages(facts)["2024"]
    assert _read(vintages["2025-02-12"], "long_term_debt") == (6.0e9, total)


def test_a_quarter_with_current_debt_beside_the_total_reads_the_total_less_its_maturities():
    # Kenvue's 10-Q for 2024-09-29: $7.803B with $750M of maturities, beside $1.648B of DebtCurrent holding them: long-
    # term debt $7.053B, total debt its own $8.701B.
    facts = _facts(end="2026-03-31", filing=_Q10, **{_WITH_CURRENT: 7.803e9, "LongTermDebtCurrent": 0.750e9,
                                                      "DebtCurrent": 1.648e9, _COMBINED: 8.701e9})
    quarter = XBRLParser().extract_quarterly_financials(facts)["2026-03-31"]
    assert _read(quarter, "long_term_debt") == (pytest.approx(7.053e9), _WITH_CURRENT)
    assert CalculatedMetrics().calculate_all(quarter)["total_debt"] == pytest.approx(8.701e9)


# ---------------- Q23: the total less the current part the same filing tags ----------------

def test_kenvues_fy2024_debt_is_its_own_total():
    # BUG-291: Kenvue's FY2024 10-K tags its long-term debt only with the maturities ($7.805B, $750M of them), beside
    # $1.552B of DebtCurrent (its $800M of paper and the maturities) and its own total, $8.607B.
    year = _year(**{_WITH_CURRENT: 7.805e9, "LongTermDebtCurrent": 0.750e9, "DebtCurrent": 1.552e9,
                    "CommercialPaper": 0.800e9, _COMBINED: 8.607e9})
    assert _read(year, "long_term_debt") == (pytest.approx(7.055e9), _WITH_CURRENT)
    assert _read(year, "short_term_debt") == (1.552e9, "DebtCurrent")
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(8.607e9)


def test_maturities_tagged_as_zero_leave_the_total_whole():
    # Kenvue's 10-Q for 2026-03-29: $7.072B with maturities of $0 beside $1.589B of DebtCurrent (its paper): the
    # total is the noncurrent debt.
    facts = _facts(end="2026-03-31", filing=_Q10, **{_WITH_CURRENT: 7.072e9, "LongTermDebtCurrent": 0.0,
                                                      "DebtCurrent": 1.589e9, _COMBINED: 8.661e9})
    quarter = XBRLParser().extract_quarterly_financials(facts)["2026-03-31"]
    assert _read(quarter, "long_term_debt") == (7.072e9, _WITH_CURRENT)
    assert CalculatedMetrics().calculate_all(quarter)["total_debt"] == pytest.approx(8.661e9)


def test_the_lease_inclusive_maturities_are_the_current_part_of_the_lease_inclusive_total():
    # The total is LongTermDebtAndCapitalLeaseObligations...IncludingCurrentMaturities, so its current part is the
    # lease-inclusive maturities where the filing tags both (APD's FY2025: $17.664B, $0.716B of them).
    year = _year(**{_WITH_CURRENT: 17.664e9, "LongTermDebtAndCapitalLeaseObligationsCurrent": 0.716e9,
                    "LongTermDebtCurrent": 0.700e9, "ShortTermBorrowings": 0.035e9})
    assert _read(year, "long_term_debt") == (pytest.approx(16.948e9), _WITH_CURRENT)


def test_trvs_combined_debt_less_the_current_debt_holding_its_paper():
    # TRV's FY2025: $9.267B, $300M of DebtCurrent holding its $100M of paper: long-term debt $8.967B, total debt
    # $9.267B, the paper counted once.
    year = _year(**{_COMBINED: 9.267e9, "DebtCurrent": 0.300e9, "CommercialPaper": 0.100e9})
    assert _read(year, "long_term_debt") == (pytest.approx(8.967e9), _COMBINED)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(9.267e9)


def test_the_combined_debt_less_the_short_term_debt_read_not_the_maturities_inside_it():
    # The own total's current part is its short-term debt as the field reads it, DebtCurrent first, which holds the
    # maturities (AIG's FY2022 10-K: $27.179B, $1.500B current).
    year = _year(**{_COMBINED: 27.179e9, "DebtCurrent": 1.500e9, "LongTermDebtCurrent": 0.600e9})
    assert _read(year, "long_term_debt") == (pytest.approx(25.679e9), _COMBINED)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(27.179e9)


def test_the_combined_debt_less_its_current_debt_and_the_paper_none_holds():
    # A quarter tagging its own total beside its paper alone (VTR's 10-Qs: $12.053B, $305M of paper, 2019-09-30): the
    # paper is the current part, and total debt the filer's own.
    facts = _facts(end="2026-03-31", filing=_Q10, **{_COMBINED: 12.053e9, "CommercialPaper": 0.305e9})
    quarter = XBRLParser().extract_quarterly_financials(facts)["2026-03-31"]
    assert _read(quarter, "long_term_debt") == (pytest.approx(11.748e9), _COMBINED)
    assert CalculatedMetrics().calculate_all(quarter)["total_debt"] == pytest.approx(12.053e9)


def test_a_combined_debt_all_current_reads_no_long_term_debt():
    # RMD's 10-Q for 2017-12-31: its $1.019B of debt is all short-term borrowings, so long-term debt is 0.
    year = _year(**{_COMBINED: 1.019e9, "ShortTermBorrowings": 1.019e9})
    assert _read(year, "long_term_debt") == (0.0, _COMBINED)
    assert CalculatedMetrics().calculate_all(year)["total_debt"] == pytest.approx(1.019e9)


def test_paper_another_reports_debt_holds_is_no_current_part_of_the_total():
    # The FY2024 10-K's own total beside its paper; the FY2025 10-K's DebtCurrent on the date holds that paper (read
    # as 0.0). The earlier filing's paper is no part to take, so its total stays unread beside the later debt.
    facts = _two_10ks(_COMBINED, "DebtCurrent")
    us_gaap = facts["facts"]["us-gaap"]
    us_gaap[_COMBINED]["units"]["USD"].pop()  # the FY2025 10-K doesn't repeat the total
    us_gaap["CommercialPaper"] = {"units": {"USD": [
        usd(0.1e9, None, "2024-12-31", fy=2024, fp="FY", form="10-K", filed="2025-02-12")]}}
    year = XBRLParser().extract_annual_financials(facts)["2024"]
    assert _read(year, "long_term_debt") == (None, None)
    assert (year.get("short_term_debt"), year.get("commercial_paper")) == (0.5e9, 0.0)


def test_a_current_part_above_the_total_leaves_it_unread():
    # A current figure above the total it should be part of tells nothing of the long-term debt.
    year = _year(**{_COMBINED: 1.0e9, "DebtCurrent": 1.2e9})
    assert _read(year, "long_term_debt") == (None, None)


# ---------------- PP&E: the lease-inclusive line, last ----------------

def test_a_lease_inclusive_ppe_is_the_ppe_of_a_filer_that_tags_no_other():
    # BUG-203: UNP, HD and TSLA tag their balance sheet's PP&E with finance-lease assets only under the
    # lease-inclusive concept (UNP's $59.645B for FY2025), so the Magic Formula had no PP&E for them.
    assert _read(_year(**{_LEASE_PPE: 59.645e9}), "ppe_net") == (59.645e9, _LEASE_PPE)


def test_the_net_ppe_outranks_the_lease_inclusive_line():
    assert _read(_year(**{"PropertyPlantAndEquipmentNet": 54.161e9, _LEASE_PPE: 55.0e9}), "ppe_net") == (
        54.161e9, "PropertyPlantAndEquipmentNet")


# ---------------- short-term investments: the current securities, then the marketable securities in total ----------

_CURRENT_SECURITIES = ["AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                       "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent", "DebtSecuritiesCurrent",
                       "HeldToMaturitySecuritiesCurrent", "OtherShortTermInvestments"]


@pytest.mark.parametrize("tag", _CURRENT_SECURITIES + ["MarketableSecurities"])
def test_current_securities_are_the_short_term_investments_of_a_filer_that_tags_no_other(tag):
    # BUG-221: ORCL, PFE, MU and nine more tag their line as available-for-sale debt securities, NVDA's FY2026 as
    # debt securities ($39.065B after $34.621B under MarketableSecuritiesCurrent), KO's as other short-term
    # investments; none was read, so net debt, EPV's cash and Graham's net-net left them out.
    assert _read(_year(**{tag: 39.065e9}), "short_term_investments") == (39.065e9, tag)


@pytest.mark.parametrize("tag", ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                                 "AvailableForSaleSecuritiesCurrent"])
@pytest.mark.parametrize("later", _CURRENT_SECURITIES + ["MarketableSecurities"])
def test_a_tag_short_term_investments_read_before_outranks_the_new_ones(tag, later):
    assert _read(_year(**{tag: 5.0e9, later: 7.0e9}), "short_term_investments") == (5.0e9, tag)


@pytest.mark.parametrize("current", _CURRENT_SECURITIES)
def test_the_marketable_securities_in_total_give_way_to_a_current_figure(current):
    # MarketableSecurities can hold noncurrent securities: read only where no current figure is tagged.
    assert _read(_year(**{"MarketableSecurities": 9.0e9, current: 4.0e9}), "short_term_investments") == (
        4.0e9, current)


def test_the_available_for_sale_debt_securities_outrank_the_other_short_term_investments():
    year = _year(AvailableForSaleSecuritiesDebtSecuritiesCurrent=3.0e9, OtherShortTermInvestments=1.0e9)
    assert _read(year, "short_term_investments") == (3.0e9, "AvailableForSaleSecuritiesDebtSecuritiesCurrent")


def test_short_term_investments_read_lower_the_net_debt():
    year = _year(LongTermDebtNoncurrent=8.0e9, CashAndCashEquivalentsAtCarryingValue=1.0e9,
                 HeldToMaturitySecuritiesCurrent=0.25e9)
    assert CalculatedMetrics().calculate_all(year)["net_debt"] == pytest.approx(6.75e9)


# ---------------- BUG-171's 10-Q line rule, for the concepts the mapping still doesn't read ----------------

def _balance_sheets(*filings):
    """Company facts of filings, each ``(form, filed, end, {tag: value})``: that filing's balance sheet on *end*."""
    us_gaap = {}
    for form, filed, end, tagged in filings:
        filing = dict(fy=int(filed[:4]), fp="FY" if form.startswith("10-K") else "Q1", form=form, filed=filed)
        for tag, val in {"LiabilitiesAndStockholdersEquity": 100.0e9, "Assets": 100.0e9, **tagged}.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, None, end, **filing))
    return {"facts": {"us-gaap": us_gaap}}


@pytest.mark.parametrize("noncurrent", ["LongTermDebtAndCapitalLeaseObligations", "LongTermDebtNoncurrent"])
def test_a_10q_line_the_10k_shows_without_maturities_is_still_read_beside_short_term_debt(noncurrent):
    # The mapping now reads CVX's LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities line itself, so
    # BUG-171's proof (the 10-K shows the line beside current maturities its short-term debt holds) is pinned with a
    # concept it doesn't read: a 10-Q's senior notes.
    facts = _balance_sheets(
        ("10-K", "2025-02-21", "2024-12-31", {noncurrent: 20.135e9, "SeniorNotes": 20.135e9,
                                              "DebtCurrent": 4.406e9, "LongTermDebtCurrent": 4.012e9}),
        ("10-Q", "2025-05-08", "2024-12-31", {"SeniorNotes": 20.135e9, "ShortTermBorrowings": 4.406e9}),
        ("10-Q", "2025-05-08", "2025-03-31", {"SeniorNotes": 25.605e9, "ShortTermBorrowings": 4.076e9}))
    quarter = XBRLParser().extract_quarterly_financials(facts)["2025-03-31"]
    assert _read(quarter, "long_term_debt") == (25.605e9, "SeniorNotes")


def test_a_10qs_current_debt_on_the_year_end_leaves_the_annual_records_total():
    # The annual record reads its short-term debt from annual reports only, so a 10-Q's comparative column with
    # current debt on the year end sits beside no total the record reads.
    us_gaap = _facts(**{_COMBINED: 6.897e9})["facts"]["us-gaap"]
    us_gaap["DebtCurrent"] = {"units": {"USD": [usd(0.5e9, None, "2025-12-31", **_Q10)]}}
    year = XBRLParser().extract_annual_financials({"facts": {"us-gaap": us_gaap}})["2025"]
    assert _read(year, "long_term_debt") == (6.897e9, _COMBINED)


# ---------------- the as-of snapshots: each day's record judges the totals by the facts known that day ----------------

@pytest.mark.parametrize("total", [_WITH_CURRENT, _COMBINED])
def test_an_as_of_snapshot_reads_the_total_until_a_later_report_tags_current_debt_beside_it(total):
    # The as-of view is the record built on the facts known on each filing day (Q18): on the FY2024 10-K's day the
    # total stands alone and is the year's debt; the FY2025 10-K's column beside it with current debt it holds has it
    # read less that debt (Q23; unread where that debt is no current part of it) from that day, and no earlier day
    # reads the later report's current debt (no look-ahead).
    rows = [row for row in build_snapshots(XBRLParser(), _two_10ks(total, "DebtCurrent"), ticker="X",
                                           years_back=None).rows if row["fiscal_year"] == 2024]
    less = _LESS_DEBT_CURRENT[total]
    assert [(row["known_from"], row.get("long_term_debt"), row["source_tags"].get("long_term_debt"))
            for row in rows] == [("2025-02-12", 6.0e9, total), ("2026-02-12", less, total if less else None)]


def test_an_as_of_snapshot_reads_the_total_less_the_maturities_from_the_day_they_are_filed():
    rows = [row for row in build_snapshots(XBRLParser(), _two_10ks(_WITH_CURRENT, "LongTermDebtCurrent"), ticker="X",
                                           years_back=None).rows if row["fiscal_year"] == 2024]
    assert [(row["known_from"], row.get("long_term_debt"), row.get("short_term_debt")) for row in rows] == [
        ("2025-02-12", 6.0e9, None), ("2026-02-12", 5.5e9, 0.5e9)]


@pytest.mark.parametrize("total", [_WITH_CURRENT, _COMBINED])
def test_the_snapshots_vintages_judge_the_total_by_their_own_filing(total):
    vintages = build_snapshots(XBRLParser(), _two_10ks(total, "DebtCurrent"), ticker="X", years_back=None).vintages
    less = _LESS_DEBT_CURRENT[total]
    assert _read(vintages["2024"]["2025-02-12"], "long_term_debt") == (6.0e9, total)
    assert _read(vintages["2024"]["2026-02-12"], "long_term_debt") == ((less, total) if less else (None, None))


# ---------------- a bank's MarketableSecurities: its securities book, not short-term investments (Q20) ----------------

def _bank_book(end="2025-12-31", filing=_K10):
    # BAC tags its whole securities book as MarketableSecurities ($925.6B for FY2025) and its deposits beside it.
    return _facts(end=end, filing=filing, MarketableSecurities=925.6e9, Deposits=2.0e12)


def test_a_banks_marketable_securities_arent_its_short_term_investments():
    parser = XBRLParser()
    assert _read(parser.extract_annual_financials(_bank_book(), sector="bank")["2025"],
                 "short_term_investments") == (None, None)
    assert _read(parser.extract_annual_vintages(_bank_book(), sector="bank")["2025"]["2026-02-12"],
                 "short_term_investments") == (None, None)
    quarter = parser.extract_quarterly_financials(_bank_book("2026-03-31", _Q10), sector="bank")["2026-03-31"]
    assert _read(quarter, "short_term_investments") == (None, None)


@pytest.mark.parametrize("sector", [None, "general", "insurance", "reit", "utility", "energy"])
def test_marketable_securities_are_read_outside_banks(sector):
    parser = XBRLParser()
    assert _read(parser.extract_annual_financials(_bank_book(), sector=sector)["2025"],
                 "short_term_investments") == (925.6e9, "MarketableSecurities")
    quarter = parser.extract_quarterly_financials(_bank_book("2026-03-31", _Q10), sector=sector)["2026-03-31"]
    assert _read(quarter, "short_term_investments") == (925.6e9, "MarketableSecurities")


@pytest.mark.parametrize("tag", _CURRENT_SECURITIES)
def test_a_banks_current_securities_are_still_read(tag):
    # Only the total book is left: a current tag is a current line, a bank's too.
    year = XBRLParser().extract_annual_financials(_facts(**{tag: 4.0e9, "MarketableSecurities": 925.6e9}),
                                                  sector="bank")["2025"]
    assert _read(year, "short_term_investments") == (4.0e9, tag)


def test_a_banks_record_and_snapshots_leave_the_book_unread():
    record = build_annual_record(XBRLParser(), _bank_book(), ticker="BAC", years_back=None, sector="bank")
    assert record.periods["2025"].get("short_term_investments") is None
    snapshots = build_snapshots(XBRLParser(), _bank_book(), ticker="BAC", years_back=None, sector="bank")
    assert [row.get("short_term_investments") for row in snapshots.rows] == [None]
    assert snapshots.vintages["2025"]["2026-02-12"].get("short_term_investments") is None
    general = build_snapshots(XBRLParser(), _bank_book(), ticker="X", years_back=None)
    assert [row.get("short_term_investments") for row in general.rows] == [925.6e9]
    assert general.vintages["2025"]["2026-02-12"]["short_term_investments"] == 925.6e9


def _collected(tmp_path, monkeypatch, sic, facts):
    return _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0000070858", "ticker": "BAC", "source": "sec_edgar", "company_facts": facts,
        "submissions": {"name": "BANK OF AMERICA CORP", "sic": sic, "filings": []}})


def test_the_run_leaves_a_banks_book_unread_in_every_record(tmp_path, monkeypatch):
    # The run's sector class decides (the one the store and the valuation read): a finance SIC with deposits.
    facts = _balance_sheets(
        ("10-K", "2026-02-12", "2025-12-31", {"MarketableSecurities": 925.6e9, "Deposits": 2.0e12}),
        ("10-Q", "2026-05-05", "2026-03-31", {"MarketableSecurities": 930.0e9, "Deposits": 2.1e12}))
    stock = _collected(tmp_path, monkeypatch, "6021", facts)
    assert stock.sector_class == "bank"
    assert stock.financials_annual["2025"].get("short_term_investments") is None
    assert all(period.get("short_term_investments") is None for period in stock.financials_quarterly.values())
    assert all(row.get("short_term_investments") is None for row in stock.financials_annual_known)
    assert stock.financials_annual_vintages["2025"]["2026-02-12"].get("short_term_investments") is None


def test_a_finance_sic_without_deposits_has_its_marketable_securities_read(tmp_path, monkeypatch):
    # BLK's shape (Q9): a finance SIC whose companyfacts tag no deposits is a general company.
    facts = _facts(MarketableSecurities=6.1e9)
    stock = _collected(tmp_path, monkeypatch, "6211", facts)
    assert stock.sector_class == "general"
    assert stock.financials_annual["2025"]["short_term_investments"] == 6.1e9
    assert stock.financials_annual_known[-1]["short_term_investments"] == 6.1e9


def test_the_coverage_report_reads_a_banks_book_as_the_run_does():
    assert analyze("BAC", _bank_book(), {"sic": "6021"}).status["short_term_investments"] != REPORTED
    assert analyze("X", _bank_book(), {"sic": "3571"}).status["short_term_investments"] == REPORTED
