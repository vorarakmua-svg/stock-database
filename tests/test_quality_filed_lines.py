"""The accounting identities with the lines a filing tags between the canonical figures: temporary equity between
liabilities and equity (BUG-114, with BUG-063's non-controlling interests), and a cost line deducted beside cost of
revenue (MO's excise taxes, BUG-115). The check reads them from the run's companyfacts; nothing is stored."""

import pytest

from src.validation.quality import assess_annual
from tests.conftest import usd
from tests.test_fetcher_concurrency import _sec_only_fetcher

_K10 = dict(fy=2025, fp="FY", form="10-K", filed="2026-02-10")


def _facts(entries):
    """companyfacts holding {tag: [fact, ...]}."""
    return {"facts": {"us-gaap": {tag: {"units": {"USD": list(facts)}} for tag, facts in entries.items()}}}


def _year(**overrides):
    year = {"period_end": "2025-12-31", "revenue": 15.0e9, "net_income": 4.5e9, "operating_cash_flow": 5.0e9,
            "total_assets": 61.200e9, "total_liabilities": 22.889e9, "total_equity": 33.142e9,
            "minority_interest": 0.252e9}
    year.update(overrides)
    return year


def _codes(report):
    return [f.code for f in report.findings]


def test_spgis_redeemable_interests_close_its_balance_sheet():
    # SPGI FY2025: assets 61.200B; liabilities, equity and NCI 56.283B; redeemable NCI 4.917B between them.
    facts = _facts({"RedeemableNoncontrollingInterestEquityCarryingAmount": [usd(4.917e9, None, "2025-12-31", **_K10)]})
    assert "balance_sheet_imbalance" not in _codes(assess_annual({"2025": _year()}, facts=facts))
    assert "balance_sheet_imbalance" in _codes(assess_annual({"2025": _year()}))


def test_wdcs_convertible_preferred_closes_its_balance_sheet():
    # WDC FY2023: its convertible preferred stock, temporary equity attributable to the parent ($0.876B).
    year = _year(period_end="2023-06-30", total_assets=24.546e9, total_liabilities=11.448e9, total_equity=12.222e9,
                 minority_interest=None)
    k10 = dict(fy=2023, fp="FY", form="10-K", filed="2023-08-25")
    facts = _facts({"TemporaryEquityCarryingAmountAttributableToParent": [usd(0.876e9, None, "2023-06-30", **k10)]})
    assert "balance_sheet_imbalance" not in _codes(assess_annual({"2023": year}, facts=facts))


@pytest.mark.parametrize("tagged, closes", [
    # The total with the redeemable non-controlling interests stands for its parts (a filing's parts can be tagged
    # for other figures: added up, 7.917B).
    ({"TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests": 4.917e9,
      "TemporaryEquityCarryingAmountAttributableToParent": 4.917e9,
      "RedeemableNoncontrollingInterestEquityCarryingAmount": 3.0e9}, True),
    # Without it, the parent's part and the interests' add up.
    ({"TemporaryEquityCarryingAmountAttributableToParent": 2.917e9,
      "RedeemableNoncontrollingInterestEquityCarryingAmount": 2.0e9}, True),
    ({"TemporaryEquityCarryingAmount": 2.917e9,
      "TemporaryEquityCarryingAmountAttributableToNoncontrollingInterest": 2.0e9}, True),
    # The interests' parts add up where their total isn't tagged.
    ({"RedeemableNoncontrollingInterestEquityCommonCarryingAmount": 3.917e9,
      "RedeemableNoncontrollingInterestEquityPreferredCarryingAmount": 1.0e9}, True),
    # One figure tagged as both parts is one figure (KKR's 2023 10-K: $615M under each).
    ({"TemporaryEquityCarryingAmountAttributableToParent": 4.917e9,
      "RedeemableNoncontrollingInterestEquityCarryingAmount": 4.917e9}, True),
    # A fair value isn't the carrying amount the balance sheet holds.
    ({"RedeemableNoncontrollingInterestEquityFairValue": 4.917e9}, False),
])
def test_the_temporary_equity_tags_read(tagged, closes):
    facts = _facts({tag: [usd(val, None, "2025-12-31", **_K10)] for tag, val in tagged.items()})
    flagged = "balance_sheet_imbalance" in _codes(assess_annual({"2025": _year()}, facts=facts))
    assert flagged is not closes


def test_temporary_equity_on_another_day_or_in_a_10q_is_not_the_years():
    q10 = dict(fy=2026, fp="Q1", form="10-Q", filed="2026-04-28")
    facts = _facts({"RedeemableNoncontrollingInterestEquityCarryingAmount": [
        usd(4.917e9, None, "2024-12-31", **_K10), usd(4.917e9, None, "2025-12-31", **q10)]})
    assert "balance_sheet_imbalance" in _codes(assess_annual({"2025": _year()}, facts=facts))


def test_the_latest_filed_figure_for_the_day_reads():
    # A later 10-K restates the year end's redeemable interests; its figure is the one read.
    k26 = dict(fy=2026, fp="FY", form="10-K", filed="2027-02-10")
    facts = _facts({"RedeemableNoncontrollingInterestEquityCarryingAmount": [
        usd(1.0e9, None, "2025-12-31", **_K10), usd(4.917e9, None, "2025-12-31", **k26)]})
    assert "balance_sheet_imbalance" not in _codes(assess_annual({"2025": _year()}, facts=facts))


def test_a_gap_temporary_equity_doesnt_explain_is_still_flagged():
    facts = _facts({"RedeemableNoncontrollingInterestEquityCarryingAmount": [usd(1.0e9, None, "2025-12-31", **_K10)]})
    report = assess_annual({"2025": _year()}, facts=facts)
    assert "balance_sheet_imbalance" in _codes(report)
    message = next(f.message for f in report.findings if f.code == "balance_sheet_imbalance")
    assert message == "Assets (61,200,000,000) != Liabilities + Equity + NCI + temporary equity (57,283,000,000)."


def test_a_balance_sheet_that_closes_without_temporary_equity_stays_closed():
    # The line explains a gap; it adds none to a balance sheet whose figures close (a liabilities total that holds it).
    year = _year(total_liabilities=27.806e9)
    facts = _facts({"RedeemableNoncontrollingInterestEquityCarryingAmount": [usd(4.917e9, None, "2025-12-31", **_K10)]})
    assert "balance_sheet_imbalance" not in _codes(assess_annual({"2025": year}, facts=facts))


def _mo(**overrides):
    # MO FY2024: net revenues 24.018B, cost of sales 6.077B, excise taxes on products 3.574B, gross profit 14.367B.
    year = {"period_end": "2024-12-31", "revenue": 24.018e9, "cost_of_revenue": 6.077e9, "gross_profit": 14.367e9,
            "net_income": 11.264e9, "operating_cash_flow": 8.753e9, "total_assets": 35.177e9,
            "total_liabilities": 38.705e9, "total_equity": -3.528e9}
    year.update(overrides)
    return year


_MO_K10 = dict(fy=2024, fp="FY", form="10-K", filed="2025-02-12")


def test_mos_excise_taxes_explain_its_gross_profit():
    facts = _facts({"OtherCostOfOperatingRevenue": [usd(3.574e9, "2024-01-01", "2024-12-31", **_MO_K10)]})
    assert "gross_profit_mismatch" not in _codes(assess_annual({"2024": _mo()}, facts=facts))
    assert "gross_profit_mismatch" in _codes(assess_annual({"2024": _mo()}))


def test_a_cost_line_of_a_quarter_or_another_year_doesnt_explain_the_years_gross_profit():
    facts = _facts({"OtherCostOfOperatingRevenue": [usd(3.574e9, "2024-10-01", "2024-12-31", **_MO_K10),
                                                    usd(3.574e9, "2023-01-01", "2023-12-31", **_MO_K10)]})
    assert "gross_profit_mismatch" in _codes(assess_annual({"2024": _mo()}, facts=facts))


def test_a_gap_the_cost_line_doesnt_explain_is_still_flagged():
    facts = _facts({"OtherCostOfOperatingRevenue": [usd(1.0e9, "2024-01-01", "2024-12-31", **_MO_K10)]})
    assert "gross_profit_mismatch" in _codes(assess_annual({"2024": _mo()}, facts=facts))


def test_a_gross_profit_that_holds_without_the_line_still_holds():
    # A filer whose cost of revenue holds the line already (gross profit = revenue - cost of revenue).
    facts = _facts({"OtherCostOfOperatingRevenue": [usd(3.574e9, "2024-01-01", "2024-12-31", **_MO_K10)]})
    year = _mo(cost_of_revenue=9.651e9)
    assert "gross_profit_mismatch" not in _codes(assess_annual({"2024": year}, facts=facts))


def _bx_facts():
    """BX's 10-K for 2025 as companyfacts: equity with and without its non-controlling interests, and its redeemable
    ones between liabilities and equity."""
    k10 = dict(fy=2025, fp="FY", form="10-K", filed="2026-02-27")
    tagged = {"Assets": 47.708975e9, "Liabilities": 25.827803e9, "StockholdersEquity": 8.665526e9,
              "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": 20.500669e9,
              "RedeemableNoncontrollingInterestEquityCarryingAmount": 1.380503e9}
    entries = {tag: [usd(val, None, "2025-12-31", **k10)] for tag, val in tagged.items()}
    entries["RevenueFromContractWithCustomerExcludingAssessedTax"] = [usd(13.0e9, "2025-01-01", "2025-12-31", **k10)]
    entries["NetIncomeLoss"] = [usd(3.0e9, "2025-01-01", "2025-12-31", **k10)]
    entries["NetCashProvidedByUsedInOperatingActivities"] = [usd(3.5e9, "2025-01-01", "2025-12-31", **k10)]
    return _facts(entries)


def test_the_run_reads_the_lines_from_its_companyfacts(tmp_path, monkeypatch):
    # BX FY2025 end to end: its non-controlling interests (BUG-063) and its redeemable ones (BUG-114) close the
    # balance sheet the quality pass checks (assets 47.709B; liabilities, equity and NCI 34.493B before).
    fetcher = _sec_only_fetcher(tmp_path, monkeypatch, {
        "cik": "0001393818", "ticker": "BX", "source": "sec_edgar", "company_facts": _bx_facts(),
        "submissions": {"name": "Blackstone Inc.", "sic": "6282", "filings": []}})
    stock = fetcher.fetch_ticker("BX", include_yahoo=False)
    assert stock.financials_annual["2025"]["minority_interest"] == pytest.approx(11.835143e9)
    assert "balance_sheet_imbalance" not in [f["code"] for f in stock.data_quality["findings"]]
