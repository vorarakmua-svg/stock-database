"""Valuation models: hand-checked values, N/A paths, scenario ordering."""
import math

import pytest

from src.valuation.inputs import FYRecord, ValuationInputs
from src.valuation.models import (
    dcf_per_share,
    ddm_per_share,
    value_dcf,
    value_ddm,
    value_graham,
    value_lynch,
    value_multiples,
)


def _fy(fy, fcf=None, net_income=None, equity=None, eps=None, shares=100.0,
        ffo_ps=None, period_end=None):
    return FYRecord(fiscal_year=fy, period_end=period_end, net_income=net_income,
                    total_equity=equity, eps_diluted=eps, shares=shares,
                    fcf=fcf, ffo_per_share=ffo_ps)


def _inputs(**kwargs):
    defaults = dict(ticker="AAA", sector_class="general", fy_records=[],
                    shares_outstanding=100.0, beta=1.0, risk_free_rate=0.045,
                    analyst_growth=None, dividends=[], fy_end_prices={})
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
    recs = [_fy(fy, fcf=100.0) for fy in range(2019, 2024)]
    res = value_dcf(_inputs(fy_records=recs, shares_outstanding=None))
    assert res.applicable is False
    assert res.na_reason == "shares outstanding unavailable"


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
