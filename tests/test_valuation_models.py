"""Valuation models: hand-checked values, N/A paths, scenario ordering."""
import math
from datetime import date

import pytest

from src.valuation.assumptions import MARGIN_OF_SAFETY, OE_DISCOUNT_FLOOR
from src.valuation.inputs import FYRecord, ValuationInputs
from src.valuation.models import (
    dcf_per_share,
    ddm_per_share,
    owner_earnings,
    value_dcf,
    value_ddm,
    value_graham,
    value_lynch,
    value_multiples,
    value_owner_earnings,
)


def _fy(fy, fcf=None, net_income=None, equity=None, eps=None, shares=100.0,
        ffo_ps=None, period_end=None, split_factor=1.0):
    return FYRecord(fiscal_year=fy, period_end=period_end, net_income=net_income,
                    total_equity=equity, eps_diluted=eps, shares=shares,
                    fcf=fcf, ffo_per_share=ffo_ps, split_factor=split_factor)


def _inputs(**kwargs):
    defaults = dict(ticker="AAA", sector_class="general", fy_records=[],
                    shares_outstanding=100.0, beta=1.0, risk_free_rate=0.045,
                    analyst_growth=None, dividends=[], fy_end_prices={},
                    as_of=date(2024, 1, 1))
    defaults.update(kwargs)
    return ValuationInputs(**defaults)


# ---- dcf_per_share: closed-form perpetuity check ----
def test_dcf_per_share_zero_growth_is_perpetuity():
    # growth = terminal = 0, discount 10%: PV of flat 100/yr forever = 1000
    v = dcf_per_share(100.0, 100.0, growth=0.0, discount=0.10, terminal_growth=0.0)
    assert v == pytest.approx(10.0, rel=1e-9)


def test_dcf_per_share_monotonic_in_growth_and_discount():
    lo = dcf_per_share(100.0, 100.0, 0.02, 0.10)
    hi = dcf_per_share(100.0, 100.0, 0.08, 0.10)
    assert hi > lo
    cheap_money = dcf_per_share(100.0, 100.0, 0.05, 0.08)
    dear_money = dcf_per_share(100.0, 100.0, 0.05, 0.12)
    assert cheap_money > dear_money


# ---- value_dcf ----
def test_value_dcf_happy_path_scenario_ordering():
    recs = [_fy(fy, fcf=100.0 * 1.05 ** i) for i, fy in enumerate(range(2019, 2024))]
    res = value_dcf(_inputs(fy_records=recs))
    assert res.applicable is True
    assert res.model == "dcf"
    assert res.basis_fiscal_year == 2023
    assert res.value_bear < res.value_base < res.value_bull
    assert res.assumptions["growth_source"] == "hist_only"
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)


def test_value_dcf_na_wrong_sector():
    res = value_dcf(_inputs(sector_class="bank"))
    assert res.applicable is False
    assert res.na_reason == "not applicable to sector 'bank'"


def test_value_dcf_na_insufficient_history():
    recs = [_fy(fy, fcf=100.0) for fy in (2021, 2022, 2023)]
    res = value_dcf(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "insufficient FCF history (need >= 4 fiscal years)"


def test_value_dcf_na_negative_fcf():
    recs = [_fy(fy, fcf=-50.0) for fy in range(2019, 2024)]
    res = value_dcf(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "median 3-year FCF is not positive"


def test_value_dcf_na_missing_shares():
    # Shares missing from BOTH the market snapshot and every FY record.
    recs = [_fy(fy, fcf=100.0, shares=None) for fy in range(2019, 2024)]
    res = value_dcf(_inputs(fy_records=recs, shares_outstanding=None))
    assert res.applicable is False
    assert res.na_reason == "shares outstanding unavailable"


def test_value_dcf_shares_fallback_to_financials_annual():
    # No market-snapshot share count, but financials_annual has it for every FY.
    recs = [_fy(fy, fcf=100.0 * 1.05 ** i, shares=50.0)
            for i, fy in enumerate(range(2019, 2024))]
    res_fallback = value_dcf(_inputs(fy_records=recs, shares_outstanding=None))
    res_snapshot = value_dcf(_inputs(fy_records=recs, shares_outstanding=50.0))
    assert res_fallback.applicable is True
    assert res_fallback.assumptions["shares_source"] == "financials_annual"
    assert res_fallback.assumptions["shares_outstanding"] == 50.0
    assert res_fallback.value_base == pytest.approx(res_snapshot.value_base)
    assert res_fallback.value_bear == pytest.approx(res_snapshot.value_bear)
    assert res_fallback.value_bull == pytest.approx(res_snapshot.value_bull)


def test_value_dcf_shares_fallback_walks_back_to_latest_positive():
    # Latest FY record's shares is missing; walk back to the most recent
    # fiscal year (among the FCF-filtered records) with a positive count.
    recs = [_fy(2019, fcf=100.0, shares=80.0),
            _fy(2020, fcf=105.0, shares=80.0),
            _fy(2021, fcf=110.0, shares=80.0),
            _fy(2022, fcf=115.0, shares=None),
            _fy(2023, fcf=120.0, shares=None)]
    res = value_dcf(_inputs(fy_records=recs, shares_outstanding=None))
    assert res.applicable is True
    assert res.assumptions["shares_source"] == "financials_annual"
    assert res.assumptions["shares_outstanding"] == 80.0


def test_value_dcf_shares_prefers_market_snapshot_when_present():
    # Both sources present with DIFFERENT values -> the market snapshot wins.
    recs = [_fy(fy, fcf=100.0 * 1.05 ** i, shares=50.0)
            for i, fy in enumerate(range(2019, 2024))]
    res = value_dcf(_inputs(fy_records=recs, shares_outstanding=200.0))
    res_other = value_dcf(_inputs(fy_records=recs, shares_outstanding=50.0))
    assert res.applicable is True
    assert res.assumptions["shares_source"] == "market_snapshot"
    assert res.assumptions["shares_outstanding"] == 200.0
    # Per-share value scales as 1/shares -> confirms 200 (snapshot), not 50 (FY), was used.
    assert res.value_base == pytest.approx(res_other.value_base * 50.0 / 200.0)


# ---- ddm_per_share: closed-form perpetuity check ----
def _quarterly_dividends(start_year, end_year, start_amount, growth_per_year):
    """Four equal payments per calendar year, growing annually."""
    events = []
    amount = start_amount
    for year in range(start_year, end_year + 1):
        for month in ("03", "06", "09", "12"):
            events.append((f"{year}-{month}-15", amount / 4.0))
        amount *= 1.0 + growth_per_year
    return events


def test_ddm_per_share_zero_growth_is_perpetuity():
    # growth = terminal = 0, discount 10%: 1/yr forever = 10.0
    assert ddm_per_share(1.0, 0.0, 0.10, terminal_growth=0.0) == pytest.approx(10.0)


# ---- value_ddm ----
def test_value_ddm_happy_path_for_bank():
    divs = _quarterly_dividends(2019, 2023, 1.00, 0.05)
    res = value_ddm(_inputs(sector_class="bank", dividends=divs))
    assert res.applicable is True
    assert res.model == "ddm"
    assert res.value_bear < res.value_base < res.value_bull
    # Growth clamps to DDM cap 10%, hist CAGR ~5% -> hist wins the min()
    assert res.assumptions["growth_cap"] == 0.10
    assert res.assumptions["ttm_dps"] == pytest.approx(1.00 * 1.05 ** 4)


def test_value_ddm_na_no_dividends():
    res = value_ddm(_inputs(sector_class="bank", dividends=[]))
    assert res.applicable is False
    assert res.na_reason == "no dividend history"


def test_value_ddm_na_dividends_discontinued():
    # Paid 2015-2023, suspended since: as of 2026 that is no longer a payer.
    divs = _quarterly_dividends(2015, 2023, 1.00, 0.05)
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 7, 1)))
    assert res.applicable is False
    assert res.na_reason == "dividends discontinued (no payment in the last 15 months)"


def test_value_ddm_na_no_dividends_in_trailing_12_months():
    # Last payment 2023-12-15; as of 2025-01-10 that is 13 months back:
    # inside the 15-month suspension window, but outside the TTM window.
    divs = _quarterly_dividends(2021, 2023, 1.00, 0.05)
    res = value_ddm(_inputs(dividends=divs, as_of=date(2025, 1, 10)))
    assert res.applicable is False
    assert res.na_reason == "no dividends in trailing 12 months"


def test_value_ddm_ttm_window_anchored_to_as_of():
    divs = _quarterly_dividends(2019, 2023, 1.00, 0.05)
    res = value_ddm(_inputs(dividends=divs, as_of=date(2024, 6, 1)))
    assert res.applicable is True
    assert res.assumptions["ttm_anchor"] == "2024-06-01"
    # Only the payments in the 365 days before as_of: 2023-06/09/12 (3 of 4).
    assert res.assumptions["ttm_dps"] == pytest.approx(3.0 * (1.00 * 1.05 ** 4) / 4.0)


def test_value_ddm_cagr_uses_calendar_years():
    divs = _quarterly_dividends(2019, 2023, 1.00, 0.05)
    res = value_ddm(_inputs(dividends=divs, as_of=date(2024, 1, 1)))
    assert res.assumptions["cagr_years"] == 4  # 2019 -> 2023, not len-1 of positions
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)


def test_value_ddm_na_too_short():
    divs = _quarterly_dividends(2022, 2023, 1.0, 0.0)
    res = value_ddm(_inputs(sector_class="bank", dividends=divs))
    assert res.applicable is False
    assert res.na_reason == "insufficient dividend history (need >= 3 calendar years)"


# ---- Graham Number ----
def test_value_graham_hand_computed():
    recs = [_fy(fy, net_income=None, equity=2000.0, eps=e, shares=100.0)
            for fy, e in ((2021, 2.0), (2022, 2.5), (2023, 3.0))]
    res = value_graham(_inputs(fy_records=recs))
    assert res.applicable is True
    # base: sqrt(22.5 * 3.0 * 20.0); bear uses min EPS 2.0; bull max EPS 3.0
    assert res.value_base == pytest.approx(math.sqrt(22.5 * 3.0 * 20.0))
    assert res.value_bear == pytest.approx(math.sqrt(22.5 * 2.0 * 20.0))
    assert res.value_bull == res.value_base
    assert res.basis_fiscal_year == 2023


def test_value_graham_na_negative_eps():
    recs = [_fy(2023, equity=2000.0, eps=-1.0, shares=100.0)]
    res = value_graham(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "EPS is not positive"


def test_value_graham_na_no_data():
    res = value_graham(_inputs(fy_records=[]))
    assert res.applicable is False
    assert res.na_reason == "EPS or book value unavailable"


# ---- Peter Lynch ----
def test_value_lynch_hand_computed():
    # EPS CAGR 10%/yr; analyst 12% -> min is hist 10%.
    # fair P/E = growth*100 clamped [5,25]: bear 7 -> base 10 -> bull 13.
    eps_hist = [2.00, 2.20, 2.42, 2.662, 2.9282]
    recs = [_fy(2019 + i, eps=e, shares=100.0) for i, e in enumerate(eps_hist)]
    res = value_lynch(_inputs(fy_records=recs, analyst_growth=0.12))
    assert res.applicable is True
    g = res.assumptions["growth_base"]
    expected_pe = min(max(g * 100.0, 5.0), 25.0)
    assert res.value_base == pytest.approx(expected_pe * 2.9282)
    assert res.value_bear < res.value_base < res.value_bull


def test_value_lynch_fair_pe_floor_applies():
    recs = [_fy(2019 + i, eps=2.0, shares=100.0) for i in range(5)]  # 0% growth
    res = value_lynch(_inputs(fy_records=recs))
    assert res.applicable is True
    assert res.value_base == pytest.approx(5.0 * 2.0)  # P/E floor 5


def test_value_lynch_na_when_no_growth_is_derivable():
    # 5 FYs of EPS but the first is negative -> no CAGR; no analyst estimate
    # -> growth_source "none". Publishing floor-P/E x EPS here would be a
    # fabricated number.
    eps_hist = [-1.0, 1.0, 2.0, 3.0, 4.0]
    recs = [_fy(2019 + i, eps=e, shares=100.0) for i, e in enumerate(eps_hist)]
    res = value_lynch(_inputs(fy_records=recs, analyst_growth=None))
    assert res.applicable is False
    assert res.na_reason == "no usable earnings-growth history"
    assert res.basis_fiscal_year == 2023
    assert res.value_base is None


def test_value_lynch_and_multiples_flag_split_adjustment():
    eps_hist = [2.00, 2.20, 2.42, 2.662, 2.9282]
    recs = [_fy(2019 + i, eps=e, shares=100.0, split_factor=4.0 if i < 2 else 1.0)
            for i, e in enumerate(eps_hist)]
    prices = {fy: 30.0 for fy in range(2019, 2024)}
    lynch = value_lynch(_inputs(fy_records=recs, fy_end_prices=prices))
    mult = value_multiples(_inputs(fy_records=recs, fy_end_prices=prices))
    assert lynch.assumptions["split_adjusted"] is True
    assert mult.assumptions["split_adjusted"] is True

    plain = [_fy(2019 + i, eps=e, shares=100.0) for i, e in enumerate(eps_hist)]
    assert value_lynch(
        _inputs(fy_records=plain)).assumptions["split_adjusted"] is False
    assert value_multiples(
        _inputs(fy_records=plain, fy_end_prices=prices)
    ).assumptions["split_adjusted"] is False


def test_value_lynch_cagr_uses_fiscal_year_span():
    eps_hist = [2.00, 2.20, 2.42, 2.662, 2.9282]
    recs = [_fy(2019 + i, eps=e, shares=100.0) for i, e in enumerate(eps_hist)]
    res = value_lynch(_inputs(fy_records=recs))
    assert res.assumptions["cagr_years"] == 4
    assert res.assumptions["hist_cagr"] == pytest.approx(0.10)


def test_value_lynch_na_sector_and_history():
    res = value_lynch(_inputs(sector_class="reit"))
    assert res.na_reason == "not applicable to sector 'reit'"
    recs = [_fy(2023, eps=2.0, shares=100.0)]
    res = value_lynch(_inputs(fy_records=recs))
    assert res.na_reason == "insufficient EPS history (need >= 4 fiscal years)"


# ---- Historical multiples band ----
def test_value_multiples_pe_band_hand_computed():
    # FY-end P/E multiples: 10, 12, 14 -> band (10, 12, 14) * latest EPS 2.0
    recs = [_fy(2021, eps=2.0), _fy(2022, eps=2.0), _fy(2023, eps=2.0)]
    prices = {2021: 20.0, 2022: 24.0, 2023: 28.0}
    res = value_multiples(_inputs(fy_records=recs, fy_end_prices=prices))
    assert res.applicable is True
    assert res.value_bear == pytest.approx(20.0)
    assert res.value_base == pytest.approx(24.0)
    assert res.value_bull == pytest.approx(28.0)
    assert res.assumptions["multiple_kind"] == "pe"


def test_value_multiples_reit_uses_pffo():
    recs = [_fy(fy, ffo_ps=3.0) for fy in (2021, 2022, 2023)]
    prices = {2021: 30.0, 2022: 36.0, 2023: 42.0}
    res = value_multiples(_inputs(sector_class="reit", fy_records=recs,
                                  fy_end_prices=prices))
    assert res.applicable is True
    assert res.assumptions["multiple_kind"] == "pffo"
    assert res.value_base == pytest.approx(12.0 * 3.0)


def test_value_multiples_na_insufficient_history():
    recs = [_fy(2022, eps=2.0), _fy(2023, eps=2.0)]
    prices = {2022: 24.0, 2023: 28.0}
    res = value_multiples(_inputs(fy_records=recs, fy_end_prices=prices))
    assert res.applicable is False
    assert res.na_reason == "insufficient multiple history (need >= 3 fiscal years)"


def test_value_multiples_na_negative_latest_basis():
    recs = [_fy(2020, eps=2.0), _fy(2021, eps=2.0), _fy(2022, eps=2.0),
            _fy(2023, eps=-1.0)]
    prices = {2020: 20.0, 2021: 20.0, 2022: 20.0, 2023: 20.0}
    res = value_multiples(_inputs(fy_records=recs, fy_end_prices=prices))
    assert res.applicable is False
    assert res.na_reason == "latest per-share basis is not positive"


def test_all_models_report_history_truncated_in_assumptions():
    """The truncation flag reaches every model's assumptions, not just the two
    that read the per-share series — DCF/Graham/DDM silently run on the
    shortened window too, so the trust anchor must say so."""
    recs = [_fy(fy, fcf=100.0 * 1.05 ** i, net_income=200.0, equity=1000.0,
                eps=2.0 + 0.1 * i, shares=100.0)
            for i, fy in enumerate(range(2019, 2024))]
    divs = _quarterly_dividends(2019, 2023, 1.00, 0.05)
    inputs = _inputs(fy_records=recs, dividends=divs, history_truncated=True)
    for res in (value_dcf(inputs), value_graham(inputs), value_ddm(inputs),
                value_lynch(inputs)):
        assert res.applicable is True, res.na_reason
        assert res.assumptions["history_truncated"] is True, res.model


# ---- Owner earnings (Buffett mode) ----
def _oe_fy(fy, ni=200.0, da=50.0, capex=-60.0, shares=100.0):
    """A fiscal year with the cash-flow figures owner earnings needs."""
    rec = _fy(fy, net_income=ni, equity=1000.0, eps=2.0, shares=shares)
    rec.depreciation_amortization = da
    rec.capex = capex
    return rec


def test_owner_earnings_adds_back_growth_capex():
    # capex 60 exceeds D&A 50 -> maintenance is 50, the other 10 is growth spend
    # owner earnings = 200 + 50 - 50 = 200   (plain FCF would be 200 + 50 - 60 = 190)
    assert owner_earnings(_oe_fy(2023, ni=200.0, da=50.0, capex=-60.0)) == 200.0


def test_owner_earnings_caps_maintenance_at_actual_capex():
    # capex 30 is BELOW D&A 50 -> you cannot spend more on maintenance than you spent
    # owner earnings = 200 + 50 - 30 = 220
    assert owner_earnings(_oe_fy(2023, ni=200.0, da=50.0, capex=-30.0)) == 220.0


def test_owner_earnings_none_without_inputs():
    rec = _fy(2023, net_income=200.0, shares=100.0)  # no D&A, no capex
    assert owner_earnings(rec) is None


def test_value_owner_earnings_happy_path():
    recs = [_oe_fy(fy, ni=200.0 * 1.05 ** i) for i, fy in enumerate(range(2016, 2026))]
    res = value_owner_earnings(_inputs(fy_records=recs, risk_free_rate=0.045))
    assert res.applicable is True
    assert res.model == "owner_earnings"
    assert res.value_bear < res.value_base < res.value_bull
    a = res.assumptions
    assert a["discount_base"] == OE_DISCOUNT_FLOOR   # 4.5% rf floors at 7%
    assert a["beta_used"] is False
    assert a["sbc_added_back"] is False
    assert a["margin_of_safety"] == MARGIN_OF_SAFETY
    assert a["buy_below"] == pytest.approx(res.value_base * 0.70)
    assert a["positive_years"] == 10
    assert a["maintenance_capex"] == 50.0
    assert a["growth_capex_added_back"] == pytest.approx(10.0)


def test_value_owner_earnings_uses_treasury_above_the_floor():
    recs = [_oe_fy(fy) for fy in range(2016, 2026)]
    res = value_owner_earnings(_inputs(fy_records=recs, risk_free_rate=0.09))
    assert res.assumptions["discount_base"] == 0.09  # above the 7% floor -> used as-is


def test_value_owner_earnings_na_erratic_earnings():
    """The predictability gate: Buffett declines to forecast what he cannot predict."""
    recs = []
    for i, fy in enumerate(range(2016, 2026)):
        ni = 200.0 if i % 2 == 0 else -150.0  # only 5 of 10 years positive
        recs.append(_oe_fy(fy, ni=ni))
    res = value_owner_earnings(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "owner earnings too erratic to forecast"


def test_value_owner_earnings_na_wrong_sector():
    res = value_owner_earnings(_inputs(sector_class="bank"))
    assert res.applicable is False
    assert res.na_reason == "not applicable to sector 'bank'"


def test_value_owner_earnings_na_insufficient_history():
    recs = [_oe_fy(fy) for fy in (2023, 2024, 2025)]
    res = value_owner_earnings(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "insufficient history (need >= 4 fiscal years)"


def test_value_owner_earnings_na_missing_shares():
    recs = [_oe_fy(fy, shares=None) for fy in range(2016, 2026)]
    res = value_owner_earnings(_inputs(fy_records=recs, shares_outstanding=None))
    assert res.applicable is False
    assert res.na_reason == "shares outstanding unavailable"


def test_value_owner_earnings_short_but_clean_history_is_not_called_erratic():
    """6 years, every one positive — that is not erratic, it is just short."""
    recs = [_oe_fy(fy, ni=200.0) for fy in range(2020, 2026)]
    res = value_owner_earnings(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == (
        "insufficient history for the predictability test (need >= 10 fiscal years)")


def test_value_owner_earnings_erratic_reason_needs_a_full_window():
    """With a full 10-year window and too few positive years, the erratic reason
    is the true one."""
    recs = []
    for i, fy in enumerate(range(2016, 2026)):
        recs.append(_oe_fy(fy, ni=200.0 if i % 2 == 0 else -150.0))
    res = value_owner_earnings(_inputs(fy_records=recs))
    assert res.applicable is False
    assert res.na_reason == "owner earnings too erratic to forecast"
