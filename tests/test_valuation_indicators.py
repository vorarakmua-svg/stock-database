"""The other valuation indicators (Milestone 4) on hand-built inputs, against hand-computed values: the reverse DCF,
the earnings power value, NCAV and Graham's net-net, the median P/S value and the Graham number on tangible book.

docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md ("Indicators")
"""
import dataclasses
import math

import pytest

from src.scores.fundamentals import AnnualRow, Fundamentals
from src.scores.scores import DEBT_UNKNOWN, Market
from src.valuation import indicators
from src.valuation.indicators import (
    INDICATORS,
    IndicatorInputs,
    PsRange,
    compute_indicators,
    epv,
    graham_tangible,
    implied_growth,
    ncav,
    ps_value,
    reverse_dcf,
)
from src.valuation.inputs import FYRecord, ValuationInputs
from src.valuation.models import dcf_per_share, value_dcf

ADS = "the ADS ratio can't be read: the latest 20-F counts 3.30 shares for each one the market counts"


def _valuation(**kw):
    defaults = dict(ticker="AAA", sector_class="general", fy_records=[], shares_outstanding=10.0, beta=1.0,
                    risk_free_rate=0.045)
    defaults.update(kw)
    return ValuationInputs(**defaults)


def _inputs(valuation=None, rows=(), market=None, ps=None, book=None, sector="general"):
    return IndicatorInputs(ticker="AAA", valuation=valuation or _valuation(sector_class=sector),
                           fundamentals=Fundamentals(ticker="AAA", sector_class=sector, rows=list(rows)),
                           market=market or Market(), price_date="2026-02-25T10:00:00", ps_range=ps,
                           book=book or {})


# ---- the reverse DCF ---------------------------------------------------------------------------------------------

FCF0, SHARES, DISCOUNT, TG = 100.0, 10.0, 0.09, 0.025


@pytest.mark.parametrize("growth", [0.07, -0.2, -0.45, 0.0, 0.95])
def test_implied_growth_gives_back_the_growth_behind_a_price(growth):
    price = dcf_per_share(FCF0, SHARES, growth, DISCOUNT, TG)
    found, reason = implied_growth(FCF0, SHARES, price, DISCOUNT, TG)
    assert reason is None
    assert found == pytest.approx(growth, abs=1e-6)


def test_a_price_no_growth_in_the_range_reaches_says_which_side():
    at_low = dcf_per_share(FCF0, SHARES, -0.5, DISCOUNT, TG)
    at_high = dcf_per_share(FCF0, SHARES, 1.0, DISCOUNT, TG)
    assert implied_growth(FCF0, SHARES, 0.9 * at_low, DISCOUNT, TG) == (
        None, "the price is below the DCF's value at -50% growth a year")
    assert implied_growth(FCF0, SHARES, 1.1 * at_high, DISCOUNT, TG) == (
        None, "the price is above the DCF's value at 100% growth a year")
    # The range's ends are reached.
    assert implied_growth(FCF0, SHARES, at_low, DISCOUNT, TG)[0] == pytest.approx(-0.5, abs=1e-6)
    assert implied_growth(FCF0, SHARES, at_high, DISCOUNT, TG)[0] == pytest.approx(1.0, abs=1e-6)


def test_the_search_range_is_a_parameter():
    price = dcf_per_share(FCF0, SHARES, 0.3, DISCOUNT, TG)
    assert implied_growth(FCF0, SHARES, price, DISCOUNT, TG, low=-0.1, high=0.2) == (
        None, "the price is above the DCF's value at 20% growth a year")
    assert implied_growth(FCF0, SHARES, price, DISCOUNT, TG, low=0.4, high=0.6) == (
        None, "the price is below the DCF's value at 40% growth a year")


def _fy(year, fcf=None, eps=None, equity=None, shares=10.0):
    return FYRecord(fiscal_year=year, period_end=f"{year}-12-31", net_income=None, total_equity=equity,
                    eps_diluted=eps, shares=shares, fcf=fcf, ffo_per_share=None)


def _dcf_records():
    # FCF 80, 90, 100, 110: the DCF's basis is the 3-year median, 100, and its growth the 3-year CAGR, 11.2%.
    return [_fy(y, fcf=f) for y, f in zip(range(2022, 2026), (80.0, 90.0, 100.0, 110.0))]


def test_reverse_dcf_solves_the_dcf_at_its_own_discount():
    valuation = _valuation(fy_records=_dcf_records())  # rf 4.5% + beta 1 x 4.5%: 9%
    dcf = value_dcf(valuation)
    assert dcf.assumptions["discount_base"] == pytest.approx(0.09)
    price = dcf_per_share(100.0, 10.0, 0.05, 0.09, 0.025)
    r = reverse_dcf(_inputs(valuation, market=Market(price=price)))
    assert (r.indicator, r.applicable, r.unit, r.basis_fiscal_year) == ("reverse_dcf", True, "rate", 2025)
    # 5% at the DCF's base discount (9%), not its bear (10%) or bull (8%) discount.
    assert r.value_base == pytest.approx(0.05, abs=1e-6)
    assert r.value_bear is None and r.value_bull is None
    a = r.assumptions
    assert a["price"] == price and a["price_date"] == "2026-02-25T10:00:00"
    assert a["fcf_basis"] == 100.0 and a["shares_outstanding"] == 10.0
    assert a["discount_base"] == pytest.approx(0.09) and a["terminal_growth"] == 0.025
    assert a["dcf_growth_base"] == pytest.approx((110.0 / 80.0) ** (1 / 3) - 1)
    assert a["dcf_value_base"] == pytest.approx(dcf.value_base)
    assert (a["search_low"], a["search_high"]) == (-0.5, 1.0)


def test_reverse_dcf_divides_by_the_dcfs_own_share_count():
    # Without today's count the DCF falls back to the latest fiscal year's (here 20), and so does its reverse.
    records = [dataclasses.replace(r, shares=20.0) for r in _dcf_records()]
    price = dcf_per_share(100.0, 20.0, 0.04, 0.09, 0.025)
    r = reverse_dcf(_inputs(_valuation(fy_records=records, shares_outstanding=None), market=Market(price=price)))
    assert r.assumptions["shares_outstanding"] == 20.0
    assert r.value_base == pytest.approx(0.04, abs=1e-6)


def test_reverse_dcf_is_na_without_a_dcf_a_price_or_a_readable_ads():
    market = Market(price=50.0)
    short = _valuation(fy_records=_dcf_records()[:3])
    assert reverse_dcf(_inputs(short, market=market)).na_reason == (
        "no DCF: insufficient FCF history (need >= 4 fiscal years)")
    bank = _valuation(sector_class="bank", fy_records=_dcf_records())
    assert reverse_dcf(_inputs(bank, market=market, sector="bank")).na_reason == (
        "no DCF: not applicable to sector 'bank'")
    for price in (None, 0.0):
        r = reverse_dcf(_inputs(_valuation(fy_records=_dcf_records()), market=Market(price=price)))
        assert (r.applicable, r.na_reason, r.unit) == (False, "no price", "rate")
    ads = _valuation(fy_records=_dcf_records(), ads_unreadable=ADS)
    assert reverse_dcf(_inputs(ads, market=market)).na_reason == ADS


def test_reverse_dcf_out_of_range_keeps_its_inputs():
    price = 2 * dcf_per_share(100.0, 10.0, 1.0, 0.09, 0.025)
    r = reverse_dcf(_inputs(_valuation(fy_records=_dcf_records()), market=Market(price=price)))
    assert not r.applicable and r.unit == "rate"
    assert r.na_reason == "the price is above the DCF's value at 100% growth a year"
    assert r.assumptions["price"] == price and r.assumptions["fcf_basis"] == 100.0
    assert r.value_base is None


# ---- the earnings power value ------------------------------------------------------------------------------------

def _row(year, revenue, operating_income, shares=12.0, **figures):
    return AnnualRow(fiscal_year=year, period_end=f"{year}-12-31", shares=shares, eps=None,
                     figures=dict(revenue=revenue, operating_income=operating_income, **figures))


#: The latest year: 25% effective tax, debt 200 at 8% interest, cash 50 and short-term investments 30.
LATEST = dict(income_tax_expense=25.0, pretax_income=100.0, total_debt=200.0, interest_expense=16.0,
              cash_and_equivalents=50.0, short_term_investments=30.0)
#: A WACC of 10%: equity 800 at 6.5% + 1.0 x 4.5% = 11%, debt 200 at 8% x (1 - 25%), 0.8 x 11% + 0.2 x 6%.
MARKET = Market(market_cap=800.0, price=90.0, risk_free_rate=0.065, beta=1.0)


def _epv_rows(**latest):
    """Operating margins of 10%, 12%, 14%, 16% and 18% on revenue rising to 1000: a mean of 14%."""
    rows = [_row(y, rev, oi) for y, rev, oi in
            ((2021, 500.0, 50.0), (2022, 600.0, 72.0), (2023, 700.0, 98.0), (2024, 800.0, 128.0))]
    rows.append(_row(2025, 1000.0, 180.0, **{**LATEST, **latest}))
    return rows


def test_epv_worked_example():
    r = epv(_inputs(rows=_epv_rows(), market=MARKET))
    # Normalized EBIT 14% x 1000 = 140; after 25% tax 105; at a 10% WACC the firm is worth 1050; less the debt
    # (200), plus the cash and short-term investments (80): 930 over today's 10 shares (the fiscal year's are 12).
    assert (r.indicator, r.applicable, r.unit, r.basis_fiscal_year) == ("epv", True, "per_share", 2025)
    assert r.value_base == pytest.approx(93.0)
    assert r.value_bear == pytest.approx((105.0 / 0.11 - 120.0) / 10.0)  # 83.45 at 11%
    assert r.value_bull == pytest.approx((105.0 / 0.09 - 120.0) / 10.0)  # 104.67 at 9%
    a = r.assumptions
    assert a["margin_years"] == [2021, 2022, 2023, 2024, 2025]
    assert a["mean_operating_margin"] == pytest.approx(0.14)
    assert a["latest_revenue"] == 1000.0 and a["normalized_ebit"] == pytest.approx(140.0)
    assert a["tax_rate"] == pytest.approx(0.25) and a["tax_source"] == "effective"
    assert a["adjusted_earnings"] == pytest.approx(105.0) and a["wacc"] == pytest.approx(0.10)
    assert a["total_debt"] == 200.0 and a["cash_and_sti"] == 80.0 and a["debt_missing"] is False
    assert a["shares_outstanding"] == 10.0
    assert a["maintenance_capex"] == "taken as equal to D&A"


def test_epv_reads_only_the_last_five_years():
    rows = [_row(2020, 100.0, 90.0)] + _epv_rows()  # a 90% margin six years back
    r = epv(_inputs(rows=rows, market=MARKET))
    assert r.assumptions["margin_years"] == [2021, 2022, 2023, 2024, 2025]
    assert r.value_base == pytest.approx(93.0)


def test_epv_needs_three_of_the_last_five_years():
    rows = _epv_rows()
    for row in rows[1:3]:  # 2022 and 2023 without operating income: three years left
        row.figures["operating_income"] = None
    r = epv(_inputs(rows=rows, market=MARKET))
    assert r.applicable and r.assumptions["margin_years"] == [2021, 2024, 2025]
    assert r.assumptions["mean_operating_margin"] == pytest.approx((0.10 + 0.16 + 0.18) / 3)
    rows[0].figures["revenue"] = 0.0  # 2021's revenue isn't positive: two
    older = [_row(2019, 400.0, 40.0), _row(2020, 450.0, 45.0)]  # usable, but outside the window
    r = epv(_inputs(rows=older + rows, market=MARKET))
    assert (r.applicable, r.na_reason) == (
        False, "operating income (or pretax income) and revenue in fewer than 3 of the last 5 fiscal years")


def test_epv_without_operating_income_takes_pretax_income_plus_interest():
    """XOM, JNJ, LLY and ten more state no operating income: EBIT is then pretax income plus interest expense
    (GuruFocus's EBIT), here the worked example's margins again."""
    rows = [_row(y, rev, None, pretax_income=pretax, interest_expense=10.0) for y, rev, pretax in
            ((2021, 500.0, 40.0), (2022, 600.0, 62.0), (2023, 700.0, 88.0), (2024, 800.0, 118.0))]
    rows.append(_row(2025, 1000.0, None, **{**LATEST, "pretax_income": 164.0, "income_tax_expense": 41.0}))
    r = epv(_inputs(rows=rows, market=MARKET))
    assert r.applicable and r.assumptions["mean_operating_margin"] == pytest.approx(0.14)
    assert r.assumptions["pretax_years"] == [2021, 2022, 2023, 2024, 2025]
    assert r.assumptions["no_interest_years"] == []
    assert r.value_base == pytest.approx(93.0)


def test_epv_counts_an_untagged_interest_as_zero():
    rows = _epv_rows()
    rows[1].figures.update(operating_income=None, pretax_income=60.0)  # 2022: 10% instead of 12%, no interest
    r = epv(_inputs(rows=rows, market=MARKET))
    assert r.assumptions["pretax_years"] == [2022] and r.assumptions["no_interest_years"] == [2022]
    # 2025 states both: its operating income (180) counts, not its pretax income (100).
    assert r.assumptions["mean_operating_margin"] == pytest.approx((0.10 + 0.10 + 0.14 + 0.16 + 0.18) / 5)


def test_epv_reads_the_last_five_fiscal_years_by_their_labels():
    """A missing year narrows the window rather than reaching further back: without 2024, 2021-2023 and 2025 count,
    not 2020's 90% margin."""
    rows = [_row(2020, 100.0, 90.0)] + [row for row in _epv_rows() if row.fiscal_year != 2024]
    r = epv(_inputs(rows=rows, market=MARKET))
    assert r.assumptions["margin_years"] == [2021, 2022, 2023, 2025]
    assert r.assumptions["mean_operating_margin"] == pytest.approx((0.10 + 0.12 + 0.14 + 0.18) / 4)


def test_epv_without_todays_count_divides_by_the_latest_fiscal_years_as_the_dcf_does():
    """After a failed quote the valuation inputs can lack today's count: the DCF then divides by the latest fiscal
    year's count (``models.value_dcf``), and EPV does too, saying so."""
    records = [_fy(2024, shares=11.0), _fy(2025, shares=12.0)]
    r = epv(_inputs(_valuation(shares_outstanding=None, fy_records=records), rows=_epv_rows(), market=MARKET))
    assert r.applicable and r.assumptions["shares_outstanding"] == 12.0
    assert r.assumptions["shares_source"] == "financials_annual"
    assert r.value_base == pytest.approx(930.0 / 12.0)
    assert epv(_inputs(rows=_epv_rows(), market=MARKET)).assumptions["shares_source"] == "market_snapshot"


@pytest.mark.parametrize("tax, pretax, rate, source", [
    (50.0, 100.0, 0.35, "effective"),   # 50% is clamped to 35%
    (-10.0, 100.0, 0.0, "effective"),   # a tax benefit counts as no tax
    (25.0, None, 0.21, "default"),
    (25.0, 0.0, 0.21, "default"),
    (25.0, -40.0, 0.21, "default"),     # a pretax loss
    (None, 100.0, 0.21, "default"),
])
def test_epv_takes_the_tax_rate_as_the_wacc_does(tax, pretax, rate, source):
    r = epv(_inputs(rows=_epv_rows(income_tax_expense=tax, pretax_income=pretax), market=MARKET))
    a = r.assumptions
    assert a["tax_rate"] == pytest.approx(rate) and a["tax_source"] == source
    assert a["adjusted_earnings"] == pytest.approx(140.0 * (1 - rate))
    assert r.value_base == pytest.approx((140.0 * (1 - rate) / a["wacc"] - 120.0) / 10.0)


def test_epv_is_na_for_banks_insurers_and_reits():
    for sector, name in (("bank", "banks"), ("insurance", "insurers"), ("reit", "REITs")):
        r = epv(_inputs(rows=_epv_rows(), market=MARKET, sector=sector))
        assert (r.applicable, r.na_reason, r.unit) == (False, f"not meaningful for {name}", "per_share")
    assert epv(_inputs(rows=_epv_rows(), market=MARKET, sector="utility")).applicable
    assert epv(_inputs(rows=_epv_rows(), market=MARKET, sector="energy")).applicable


def test_epv_is_na_without_its_inputs():
    unknown = _epv_rows()
    unknown[-1].debt_unknown = True
    assert epv(_inputs(rows=unknown, market=MARKET)).na_reason == DEBT_UNKNOWN
    losses = [_row(y, 1000.0, -50.0) for y in range(2021, 2025)] + [_row(2025, 1000.0, -50.0, **LATEST)]
    assert epv(_inputs(rows=losses, market=MARKET)).na_reason == (
        "normalized after-tax operating earnings are not positive")
    no_cap = Market(price=90.0, risk_free_rate=0.065, beta=1.0)
    assert epv(_inputs(rows=_epv_rows(), market=no_cap)).na_reason == "no WACC: no market cap"
    no_count = _valuation(shares_outstanding=None)
    assert epv(_inputs(no_count, rows=_epv_rows(), market=MARKET)).na_reason == "no share count"
    assert epv(_inputs(_valuation(ads_unreadable=ADS), rows=_epv_rows(), market=MARKET)).na_reason == ADS
    assert epv(_inputs(market=MARKET)).na_reason == "no annual financials"
    no_revenue = _epv_rows()
    no_revenue[-1].figures["revenue"] = None
    assert epv(_inputs(rows=no_revenue, market=MARKET)).na_reason == "no revenue in the latest fiscal year"


def test_epv_below_the_debt_is_stored_negative():
    # Debt of 2000 at 8%: WACC 800/2800 x 11% + 2000/2800 x 6% = 7.43%, 105 / 7.43% = 1413, less 2000, plus 80.
    r = epv(_inputs(rows=_epv_rows(total_debt=2000.0, interest_expense=160.0), market=MARKET))
    w = 800.0 / 2800.0 * 0.11 + 2000.0 / 2800.0 * 0.08 * 0.75
    assert r.applicable and r.assumptions["wacc"] == pytest.approx(w)
    assert r.value_base == pytest.approx((105.0 / w - 2000.0 + 80.0) / 10.0)
    assert r.value_base < 0


def test_epv_without_a_debt_figure_takes_none_and_says_so():
    r = epv(_inputs(rows=_epv_rows(total_debt=None, interest_expense=None), market=MARKET))
    assert r.assumptions["debt_missing"] is True and r.assumptions["total_debt"] == 0.0
    assert r.assumptions["wacc"] == pytest.approx(0.11)  # all equity
    assert r.value_base == pytest.approx((105.0 / 0.11 + 80.0) / 10.0)


def test_epv_has_no_bull_case_below_a_one_point_wacc(monkeypatch):
    monkeypatch.setattr(indicators, "wacc", lambda cur, market: 0.008)
    r = epv(_inputs(rows=_epv_rows(), market=MARKET))
    assert r.value_base == pytest.approx((105.0 / 0.008 - 120.0) / 10.0)
    assert r.value_bear == pytest.approx((105.0 / 0.018 - 120.0) / 10.0)
    assert r.value_bull is None


# ---- NCAV and Graham's net-net -----------------------------------------------------------------------------------

def _ncav_rows(shares=10.0, **figures):
    base = dict(current_assets=1000.0, total_liabilities=300.0, cash_and_equivalents=100.0,
                short_term_investments=50.0, accounts_receivable=200.0, inventory=300.0)
    base.update(figures)
    return [AnnualRow(fiscal_year=2025, period_end="2025-12-31", shares=shares, eps=None, figures=base)]


def test_ncav_and_the_net_net_worked_example():
    # NCAV (1000 - 300) / 10 = 70; net-net working capital (100 + 50 + 0.75 x 200 + 0.5 x 300 - 300) / 10 = 15.
    # Over the fiscal year's count (10), as M2's ncav_per_share, not today's (20).
    r = ncav(_inputs(_valuation(shares_outstanding=20.0), rows=_ncav_rows()))
    assert (r.indicator, r.applicable, r.unit, r.basis_fiscal_year) == ("ncav", True, "per_share", 2025)
    assert r.value_base == pytest.approx(70.0) and r.value_bear == pytest.approx(15.0) and r.value_bull is None
    a = r.assumptions
    assert a["ncav_per_share"] == pytest.approx(70.0) and a["nnwc_per_share"] == pytest.approx(15.0)
    assert a["netnet_buy_price"] == pytest.approx(70.0 * 2 / 3)
    assert (a["current_assets"], a["total_liabilities"], a["shares"]) == (1000.0, 300.0, 10.0)
    assert (a["cash_and_equivalents"], a["short_term_investments"], a["accounts_receivable"], a["inventory"]) == (
        100.0, 50.0, 200.0, 300.0)
    assert a["nnwc_missing"] == []


def test_a_net_net_of_zero_or_less_leaves_no_bear_case():
    r = ncav(_inputs(rows=_ncav_rows(total_liabilities=600.0)))  # NCAV 40; net-net (450 - 600) / 10 = -15
    assert r.applicable and r.value_base == pytest.approx(40.0) and r.value_bear is None
    assert r.assumptions["nnwc_per_share"] == pytest.approx(-15.0)
    r = ncav(_inputs(rows=_ncav_rows(total_liabilities=450.0)))  # net-net exactly 0
    assert r.value_base == pytest.approx(55.0) and r.value_bear is None


def test_ncav_of_zero_or_less_is_na_and_says_how_far():
    r = ncav(_inputs(rows=_ncav_rows(current_assets=500.0, total_liabilities=600.0)))
    assert (r.applicable, r.na_reason) == (
        False, "net current assets are negative: total liabilities exceed current assets")
    assert r.assumptions["ncav_per_share"] == pytest.approx(-10.0)
    assert r.assumptions["nnwc_per_share"] == pytest.approx((100.0 + 50.0 + 150.0 + 150.0 - 600.0) / 10.0)
    assert r.value_base is None and r.basis_fiscal_year == 2025
    assert not ncav(_inputs(rows=_ncav_rows(current_assets=600.0, total_liabilities=600.0))).applicable


def test_missing_net_net_parts_count_as_zero_and_are_listed():
    r = ncav(_inputs(rows=_ncav_rows(total_liabilities=100.0, short_term_investments=None, inventory=None)))
    assert r.value_base == pytest.approx(90.0)
    assert r.value_bear == pytest.approx((100.0 + 0.75 * 200.0 - 100.0) / 10.0)
    assert r.assumptions["nnwc_missing"] == ["short_term_investments", "inventory"]


def test_ncav_is_na_for_banks_and_insurers_and_without_its_inputs():
    for sector, name in (("bank", "banks"), ("insurance", "insurers")):
        assert ncav(_inputs(rows=_ncav_rows(), sector=sector)).na_reason == (
            f"not meaningful for {name} (no current assets)")
    assert ncav(_inputs(rows=_ncav_rows(current_assets=None))).na_reason == "no current assets or total liabilities"
    assert ncav(_inputs(rows=_ncav_rows(total_liabilities=None))).na_reason == (
        "no current assets or total liabilities")
    assert ncav(_inputs(rows=_ncav_rows(shares=None))).na_reason == "no share count"
    assert ncav(_inputs()).na_reason == "no annual financials"
    assert ncav(_inputs(_valuation(ads_unreadable=ADS), rows=_ncav_rows())).na_reason == ADS


# ---- the median P/S value ----------------------------------------------------------------------------------------

PS = PsRange(applicable=True, as_of="2026-01-30", n_months=57, current=4.0, p25=2.0, median=3.0, p75=5.0,
             implied_value=60.0)


def test_ps_value_is_the_histories_median_ps_value_and_its_quartiles():
    r = ps_value(_inputs(ps=PS))
    assert (r.indicator, r.applicable, r.unit, r.basis_fiscal_year) == ("ps_value", True, "per_share", None)
    assert r.value_base == 60.0
    assert r.value_bear == pytest.approx(40.0)   # 60 x 2 / 3
    assert r.value_bull == pytest.approx(100.0)  # 60 x 5 / 3
    assert r.assumptions == {"median_ps": 3.0, "p25_ps": 2.0, "p75_ps": 5.0, "current_ps": 4.0,
                             "as_of": "2026-01-30", "n_months": 57}


def test_ps_value_is_na_where_the_ps_range_is():
    assert ps_value(_inputs()).na_reason == "no valuation history (run the backfill)"
    short = PsRange(applicable=False, na_reason="20 months of values, fewer than 36", n_months=20)
    assert ps_value(_inputs(ps=short)).na_reason == "no P/S range: 20 months of values, fewer than 36"
    stale = dataclasses.replace(PS, current=None, implied_value=None,
                                na_reason="the latest trailing year known (2025-03-31) is 250 days old")
    assert ps_value(_inputs(ps=stale)).na_reason == (
        "no current P/S value: the latest trailing year known (2025-03-31) is 250 days old")
    assert ps_value(_inputs(ps=dataclasses.replace(PS, implied_value=None))).na_reason == (
        "no current P/S value: no trailing revenue")


def test_ps_value_without_a_quartile_has_no_case_for_it():
    r = ps_value(_inputs(ps=dataclasses.replace(PS, p25=None)))
    assert r.value_base == 60.0 and r.value_bear is None and r.value_bull == pytest.approx(100.0)
    r = ps_value(_inputs(ps=dataclasses.replace(PS, median=0.0)))
    assert r.value_base == 60.0 and r.value_bear is None and r.value_bull is None


# ---- the Graham number on tangible book --------------------------------------------------------------------------

def _graham(eps=(3.0, 5.0, 4.0), **kw):
    # Book 2000 over 100 shares: 20 a share. EPS 3, 5 and 4 in 2023-2025.
    records = [_fy(y, eps=e, equity=2000.0, shares=100.0) for y, e in zip((2023, 2024, 2025), eps)]
    return _valuation(fy_records=records, **kw)


BOOK = {2025: {"total_equity": 2000.0, "goodwill": 500.0, "intangible_assets": 300.0}}


def test_graham_on_tangible_book_worked_example():
    # Tangible book 2000 - 500 - 300 = 1200: 12 a share. sqrt(22.5 x 4 x 12) = 32.86.
    r = graham_tangible(_inputs(_graham(), book=BOOK))
    assert (r.indicator, r.applicable, r.unit, r.basis_fiscal_year) == ("graham_tangible", True, "per_share", 2025)
    assert r.value_base == pytest.approx(math.sqrt(22.5 * 4.0 * 12.0))
    # Bear and bull on the lowest and highest positive EPS of the last 3 fiscal years, as the Graham model does.
    assert r.value_bear == pytest.approx(math.sqrt(22.5 * 3.0 * 12.0))
    assert r.value_bull == pytest.approx(math.sqrt(22.5 * 5.0 * 12.0))
    a = r.assumptions
    assert a["bvps"] == pytest.approx(20.0) and a["tangible_bvps"] == pytest.approx(12.0)
    assert (a["eps_bear"], a["eps_base"], a["eps_bull"]) == (3.0, 4.0, 5.0)
    assert (a["total_equity"], a["goodwill"], a["intangible_assets"]) == (2000.0, 500.0, 300.0)
    assert a["multiplier"] == 22.5 and a["missing"] == []


def test_negative_tangible_book_is_na_and_says_so():
    book = {2025: {"total_equity": 2000.0, "goodwill": 1500.0, "intangible_assets": 600.0}}
    r = graham_tangible(_inputs(_graham(), book=book))
    assert (r.applicable, r.na_reason) == (False, "tangible book is negative: goodwill and intangibles exceed equity")
    assert r.assumptions["tangible_bvps"] == pytest.approx(-1.0)  # 20 x -100 / 2000
    assert (r.assumptions["goodwill"], r.assumptions["intangible_assets"]) == (1500.0, 600.0)
    zero = {2025: {"total_equity": 2000.0, "goodwill": 1500.0, "intangible_assets": 500.0}}
    assert not graham_tangible(_inputs(_graham(), book=zero)).applicable


def test_a_missing_goodwill_or_intangible_figure_counts_as_zero():
    book = {2025: {"total_equity": 2000.0, "goodwill": None, "intangible_assets": 300.0}}
    r = graham_tangible(_inputs(_graham(), book=book))
    assert r.value_base == pytest.approx(math.sqrt(22.5 * 4.0 * 17.0))
    assert r.assumptions["missing"] == ["goodwill"] and r.assumptions["goodwill"] == 0.0
    book = {2025: {"total_equity": 2000.0, "goodwill": 500.0}}
    r = graham_tangible(_inputs(_graham(), book=book))
    assert r.value_base == pytest.approx(math.sqrt(22.5 * 4.0 * 15.0))
    assert r.assumptions["missing"] == ["intangible_assets"]


def test_graham_tangible_is_na_where_the_graham_number_is():
    r = graham_tangible(_inputs(_graham(eps=(3.0, 5.0, -1.0)), book=BOOK))
    assert (r.applicable, r.na_reason, r.basis_fiscal_year) == (False, "EPS is not positive", 2025)
    assert graham_tangible(_inputs(_graham(ads_unreadable=ADS), book=BOOK)).na_reason == ADS
    assert graham_tangible(_inputs(_graham(), book={})).na_reason == "no total equity"
    assert graham_tangible(_inputs(book=BOOK)).na_reason == "EPS or book value unavailable"


# ---- all five ----------------------------------------------------------------------------------------------------

def test_compute_indicators_gives_the_five_in_order():
    assert INDICATORS == ("reverse_dcf", "epv", "ncav", "ps_value", "graham_tangible")
    results = compute_indicators(_inputs())
    assert [r.indicator for r in results] == list(INDICATORS)
    assert [r.unit for r in results] == ["rate", "per_share", "per_share", "per_share", "per_share"]
    assert all(not r.applicable and r.na_reason for r in results)


def test_one_indicator_failing_leaves_the_others(monkeypatch, caplog):
    def boom(inputs):
        raise RuntimeError("boom")

    monkeypatch.setattr(indicators, "epv", boom)
    monkeypatch.setattr(indicators, "reverse_dcf", boom)
    results = {r.indicator: r for r in compute_indicators(_inputs(rows=_ncav_rows(), ps=PS))}
    assert list(results) == list(INDICATORS)
    assert (results["epv"].applicable, results["epv"].na_reason, results["epv"].unit) == (
        False, "internal error: boom", "per_share")
    assert (results["reverse_dcf"].na_reason, results["reverse_dcf"].unit) == ("internal error: boom", "rate")
    assert results["ncav"].applicable and results["ps_value"].applicable
    assert "boom" in caplog.text
