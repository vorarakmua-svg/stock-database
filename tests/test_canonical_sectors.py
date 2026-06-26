"""Sector-specific canonical resolution and the coverage harness."""

from src.analysis.coverage import analyze, summarize
from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


def _facts(**concept_to_entries):
    return {"facts": {"us-gaap": {
        tag: {"units": {"USD": entries}} for tag, entries in concept_to_entries.items()
    }}}


def _annual_usd(val):
    """A single full-year 10-K USD fact for FY2024."""
    return [usd(val, "2023-10-01", "2024-09-28", 2024, filed="2024-11-01")]


def _instant_usd(val):
    return [usd(val, end="2024-09-28", fy=2024, filed="2024-11-01")]


def test_bank_fields_resolve():
    facts = _facts(
        InterestIncomeExpenseNet=_annual_usd(50),
        NoninterestIncome=_annual_usd(30),
        ProvisionForLoanLeaseAndOtherLosses=_annual_usd(5),
        Deposits=_instant_usd(900),
        NetIncomeLoss=_annual_usd(20),
        Assets=_instant_usd(1000),
    )
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["net_interest_income"] == 50
    assert annual["noninterest_income"] == 30
    assert annual["provision_for_credit_losses"] == 5
    assert annual["total_deposits"] == 900


def test_insurer_and_reit_fields_resolve():
    facts = _facts(
        PremiumsEarnedNet=_annual_usd(200),
        PolicyholderBenefitsAndClaimsIncurredNet=_annual_usd(150),
        RealEstateInvestmentPropertyNet=_instant_usd(5000),
        OperatingLeaseLeaseIncome=_annual_usd(400),
    )
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["premiums_earned"] == 200
    assert annual["claims_incurred"] == 150
    assert annual["real_estate_net"] == 5000
    assert annual["rental_revenue"] == 400


def test_coverage_analyze_and_summarize():
    # A bank that only files components (revenue must be derived) + an operating co.
    bank_facts = _facts(
        InterestIncomeExpenseNet=_annual_usd(50),
        NoninterestIncome=_annual_usd(30),
        Deposits=_instant_usd(900),
        NetIncomeLoss=_annual_usd(20),
        Assets=_instant_usd(1000),
        StockholdersEquity=_instant_usd(120),
        NetCashProvidedByUsedInOperatingActivities=_annual_usd(40),
    )
    tech_facts = _facts(
        Revenues=_annual_usd(1000),
        NetIncomeLoss=_annual_usd(150),
        Assets=_instant_usd(2000),
        Liabilities=_instant_usd(1200),
        StockholdersEquity=_instant_usd(800),
        NetCashProvidedByUsedInOperatingActivities=_annual_usd(250),
    )
    cov_bank = analyze("BNK", bank_facts, {"sic": "6021"})
    cov_tech = analyze("TCH", tech_facts, {"sic": "3571"})

    assert cov_bank.sector == "bank"
    # Bank revenue is derived from NII + noninterest income; liabilities derived.
    assert cov_bank.filled("revenue")
    assert cov_bank.status["revenue"] == "derived"
    assert cov_bank.filled("total_liabilities")
    assert cov_bank.filled("net_interest_income")

    summary = summarize([cov_bank, cov_tech])
    # Universal core fully covered across both companies...
    for key in ("revenue", "net_income", "total_assets",
                "total_liabilities", "total_equity", "operating_cash_flow"):
        assert summary.universal_core[key] == 1.0
    # ...so no gap line cites a missing *universal* field (sector-core may differ).
    assert all("universal:" not in g for g in summary.gaps)


def test_coverage_aggregates_unmapped_tags():
    # Two companies both report a material value under the same unmapped tag.
    def co(ticker):
        facts = _facts(
            Revenues=_annual_usd(1000),
            NetIncomeLoss=_annual_usd(100),
            Assets=_instant_usd(2000),
            StockholdersEquity=_instant_usd(800),
            NetCashProvidedByUsedInOperatingActivities=_annual_usd(200),
            FutureMysteryTagZZZ=_annual_usd(9_000_000_000),  # material, unmapped
        )
        return analyze(ticker, facts, {"sic": "3571"})

    summary = summarize([co("AAA"), co("BBB")])
    top = {tag: cnt for tag, cnt, _ex in summary.unmapped_top}
    assert top.get("FutureMysteryTagZZZ") == 2  # used by both companies


def test_cashflow_reconciliation_fields_resolve():
    net_tag = ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
               "PeriodIncreaseDecreaseIncludingExchangeRateEffect")
    facts = _facts(**{net_tag: _annual_usd(5000),
                      "EffectOfExchangeRateOnCashAndCashEquivalents": _annual_usd(-40)})
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["net_change_in_cash"] == 5000
    assert annual["fx_effect_on_cash"] == -40


def test_energy_inventory_tag_resolves():
    facts = _facts(EnergyRelatedInventory=_instant_usd(21800))
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["inventory"] == 21800


def test_inventory_prefers_inventorynet_over_energy_tag():
    facts = _facts(InventoryNet=_instant_usd(500),
                   EnergyRelatedInventory=_instant_usd(21800))
    annual = XBRLParser().extract_annual_financials(facts, years_back=1)["2024"]
    assert annual["inventory"] == 500
