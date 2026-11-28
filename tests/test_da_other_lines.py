"""BUG-276: some filers carry their D&A on tags whose meaning varies by filer, and tag no D&A total, so D&A read the
depreciation alone (or the depreciation and the amortization of intangibles, BUG-170). OtherDepreciationAndAmortization
is the D&A where the filing shows it holds the depreciation and that amortization (WDAY's, MET's, VEEV's), the
amortization line beside the depreciation where it is below the depreciation and holds the intangibles' (FLEX's); and
AmortizationOfDeferredCharges is the amortization line where a filing shows it to be the intangibles' and the
capitalized software's together (MTD's)."""
import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd

DEPRECIATION, INTANGIBLES, AMORTIZATION = "Depreciation", "AmortizationOfIntangibleAssets", "AdjustmentForAmortization"
DDA, DA = "DepreciationDepletionAndAmortization", "DepreciationAndAmortization"
OTHER, CHARGES, SOFTWARE = ("OtherDepreciationAndAmortization", "AmortizationOfDeferredCharges",
                            "CapitalizedComputerSoftwareAmortization1")
YEAR = ("2025-01-01", "2025-12-31", "10-K", "2026-02-12")


def _facts(*filings):
    """A filer's facts from filings, each (start, end, form, filed, {tag: value}), with a balance sheet on each end
    and the same revenue for a period in every filing."""
    us_gaap = {"Assets": {"units": {"USD": []}}}
    for start, end, form, filed, tagged in filings:
        filing = dict(fy=int(filed[:4]), fp="FY" if form == "10-K" else "Q", form=form, filed=filed)
        us_gaap["Assets"]["units"]["USD"].append(usd(1e12, None, end, **filing))
        for tag, val in {"Revenues": 1e11, **tagged}.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, start, end, **filing))
    return {"facts": {"us-gaap": us_gaap}}


def _da(period):
    """A period's D&A as (value, tag)."""
    return period.get("depreciation_amortization"), period.get("_source_tags", {}).get("depreciation_amortization")


def _records(facts, year="2025", filed="2026-02-12"):
    """A year's D&A in the annual record and in the vintage filed on *filed*."""
    parser = XBRLParser()
    return [_da(parser.extract_annual_financials(facts).get(year, {})),
            _da(parser.extract_annual_vintages(facts).get(year, {}).get(filed, {}))]


@pytest.mark.parametrize("tagged, da", [
    # WDAY's FY2026 10-K: "Depreciation and amortization", $347M, tagged OtherDepreciationAndAmortization, beside
    # $237M of depreciation and no amortization of intangibles (its 10-Ks to FY2019 tagged the line
    # DepreciationAndAmortization; Yahoo's D&A is $347M).
    ({OTHER: 347e6, DEPRECIATION: 237e6}, 347e6),
    # MET's FY2025 10-K: $753M beside $117M of depreciation and $83M of amortization of intangibles (Yahoo: $753M).
    ({OTHER: 753e6, DEPRECIATION: 117e6, INTANGIBLES: 83e6}, 753e6),
    # the line as large as the two, rounded
    ({OTHER: 1000e6, DEPRECIATION: 700e6, INTANGIBLES: 302e6}, 1000e6),
])
def test_a_line_that_holds_the_depreciation_and_the_amortization_is_the_d_and_a(tagged, da):
    assert _records(_facts((*YEAR, tagged))) == [(da, DA)] * 2


def test_a_line_below_the_depreciation_that_holds_the_intangibles_amortization_is_the_amortization_line():
    # FLEX's FY2026 10-K: $457M of depreciation and $106M of "amortization and other impairment charges" tagged
    # OtherDepreciationAndAmortization, beside $68M of amortization of intangibles it holds; D&A read $525M (the
    # depreciation and the intangibles' amortization) for the cash flow statement's $563M.
    facts = _facts((*YEAR, {DEPRECIATION: 457e6, OTHER: 106e6, INTANGIBLES: 68e6}))
    assert _records(facts) == [(563e6, DA)] * 2


@pytest.mark.parametrize("tagged, da", [
    # between the depreciation and the two together it shows nothing: AMD's 10-K of 2026 tags $750M beside $521M of
    # depreciation and $2.3B of amortization of intangibles, FISV's $1.857B beside $669M and $2.3B
    ({OTHER: 750e6, DEPRECIATION: 521e6, INTANGIBLES: 2300e6}, (2821e6, DA)),
    # and above the amortization: ELV's 10-K of 2012 tags FY2011's $300.3M beside $95.7M and $239.4M
    ({OTHER: 300.3e6, DEPRECIATION: 95.7e6, INTANGIBLES: 239.4e6}, (335.1e6, DA)),
    # below the depreciation with no amortization tagged: ES's FY2013 $42.6M beside $610.8M
    ({OTHER: 42.6e6, DEPRECIATION: 610.8e6}, (610.8e6, DEPRECIATION)),
    # below the depreciation and the amortization: ACN's
    ({OTHER: 20e6, DEPRECIATION: 500e6, INTANGIBLES: 60e6}, (560e6, DA)),
    # a filing's own total stands (DAL's)
    ({DDA: 2443e6, OTHER: 314e6, DEPRECIATION: 2000e6}, (2443e6, DDA)),
    # beside a larger line too (ABNB's FY2021 10-K: a $85.6M total beside $138.3M)
    ({DA: 85.6e6, OTHER: 138.3e6, DEPRECIATION: 60e6}, (85.6e6, DA)),
    # no depreciation tagged and nothing shown: MTB's FY2023 $435M beside $62M of amortization (Yahoo: the two)
    ({OTHER: 435e6, INTANGIBLES: 62e6}, (None, None)),
])
def test_a_line_the_filing_shows_nothing_of_stays_unread(tagged, da):
    assert _records(_facts((*YEAR, tagged))) == [da] * 2


def test_a_filing_without_depreciation_reads_the_line_a_filing_before_showed_to_be_the_d_and_a():
    # VEEV's 10-K for FY2024 tags $32.6M beside $6M of depreciation and $19M of amortization of intangibles; its 10-K
    # for FY2025 tags no depreciation: $39.4M beside $19M, so D&A was empty (Yahoo: $39.4M).
    earlier = ("2024-01-01", "2024-12-31", "10-K", "2025-02-12", {OTHER: 32.6e6, DEPRECIATION: 6e6, INTANGIBLES: 19e6})
    facts = _facts(earlier, (*YEAR, {OTHER: 39.4e6, INTANGIBLES: 19e6}))
    assert _records(facts) == [(39.4e6, DA)] * 2
    assert _records(facts, "2024", "2025-02-12") == [(32.6e6, DA)] * 2


def test_a_filing_without_depreciation_reads_no_later_filing():
    # the filing that shows the line to be the D&A comes after it: the earlier filing's vintage reads what was known
    later = ("2025-01-01", "2025-12-31", "10-K", "2027-02-12", {OTHER: 39.4e6, DEPRECIATION: 9e6, INTANGIBLES: 19e6})
    facts = _facts((*YEAR, {OTHER: 39.4e6, INTANGIBLES: 19e6}), later)
    assert _records(facts)[1] == (None, None)
    assert _records(facts, filed="2027-02-12")[1] == (39.4e6, DA)


def test_deferred_charges_shown_to_be_the_intangibles_and_the_softwares_amortization_are_the_amortization_line():
    # MTD's FY2025 10-K: $51.1M of depreciation and "amortization", $74.5M, tagged AmortizationOfDeferredCharges: its
    # $28.5M of amortization of intangibles and $45.8M of capitalized software's (Yahoo: $125.6M); D&A read $79.6M.
    facts = _facts((*YEAR, {DEPRECIATION: 51.1e6, CHARGES: 74.5e6, INTANGIBLES: 28.5e6, SOFTWARE: 45.8e6}))
    assert _records(facts) == [(125.6e6, DA)] * 2


def test_deferred_charges_once_shown_are_the_amortization_line_beside_rounded_parts():
    # MTD's 10-Qs tag the two parts rounded to $0.1M: to March 2019 $12.222M, $3.7M and $8.5M (they agree), to June
    # 2019 $24.548M, $7.4M and $17.0M (0.6% apart); its 10-Q to September 2020 tags no software
    q1 = ("2025-01-01", "2025-03-31", "10-Q", "2025-04-28",
          {DEPRECIATION: 9.767e6, CHARGES: 12.222e6, INTANGIBLES: 3.7e6, SOFTWARE: 8.5e6})
    six = ("2025-01-01", "2025-06-30", "10-Q", "2025-07-28",
           {DEPRECIATION: 19.39e6, CHARGES: 24.548e6, INTANGIBLES: 7.4e6, SOFTWARE: 17.0e6})
    nine = ("2025-01-01", "2025-09-30", "10-Q", "2025-10-28",
            {DEPRECIATION: 29.348e6, CHARGES: 36.877e6, INTANGIBLES: 11.3e6})
    year = (*YEAR, {DEPRECIATION: 38.991e6, CHARGES: 49.69e6, INTANGIBLES: 15.1e6, SOFTWARE: 34.4e6})
    quarters = XBRLParser().extract_quarterly_financials(_facts(q1, six, nine, year))
    assert [round(quarters[end]["depreciation_amortization"] / 1e3) for end in
            ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")] == [21989, 21949, 22287, 22456]


def test_deferred_charges_are_read_from_no_later_filings_proof():
    # a 10-K filed before the filing that shows the deferred charges to be the amortization line: its vintage reads
    # its depreciation and its intangibles' amortization, as was known then
    earlier = ("2024-01-01", "2024-12-31", "10-K", "2025-02-12", {DEPRECIATION: 50e6, CHARGES: 70e6, INTANGIBLES: 27e6})
    year = (*YEAR, {DEPRECIATION: 51.1e6, CHARGES: 74.5e6, INTANGIBLES: 28.5e6, SOFTWARE: 45.8e6})
    assert _records(_facts(earlier, year), "2024", "2025-02-12")[1] == (77e6, DA)


@pytest.mark.parametrize("tagged", [
    # no software beside them: PH's deferred charges equal its intangibles' amortization
    {DEPRECIATION: 354e6, CHARGES: 553e6, INTANGIBLES: 553e6},
    # deferred charges that aren't the two: WAT's
    {DEPRECIATION: 100e6, CHARGES: 40e6, INTANGIBLES: 20e6, SOFTWARE: 10e6},
])
def test_deferred_charges_not_shown_to_be_the_amortization_line_stay_unread(tagged):
    assert _records(_facts((*YEAR, tagged))) == [(tagged[DEPRECIATION] + tagged[INTANGIBLES], DA)] * 2


def test_a_years_quarters_stay_on_the_lines_basis():
    # WDAY's 10-Qs and 10-K tag the line and the depreciation alike: the fourth quarter is the year less nine months of
    # the line ($347M less $256M), not of the depreciation ($60M); its real quarters, on two bases, read FY2026's
    # fourth quarter -$10M.
    nine = ("2025-01-01", "2025-09-30", "10-Q", "2025-10-28", {OTHER: 256e6, DEPRECIATION: 177e6})
    year = (*YEAR, {OTHER: 347e6, DEPRECIATION: 237e6})
    quarters = XBRLParser().extract_quarterly_financials(_facts(nine, year))
    assert quarters["2025-12-31"]["depreciation_amortization"] == 91e6
