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
    owner_earnings_volatility,
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


def test_value_ddm_cagr_uses_only_the_last_10_years():
    # Spec: dividend growth uses a <= 10-year CAGR. 12%/yr growth until 2013, 3%/yr since: as of
    # mid-2026 the window is 2016-2025, so growth is 3%, not a blend dominated by decades-old growth.
    divs = []
    total = 1.0
    for year in range(1994, 2026):
        if year > 1994:
            total *= 1.12 if year <= 2013 else 1.03
        divs += [(f"{year}-{month:02d}-15", total / 4) for month in (3, 6, 9, 12)]
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 7, 1)))
    assert res.assumptions["cagr_years"] == 9
    assert res.assumptions["hist_cagr"] == pytest.approx(0.03)


def test_value_ddm_cagr_starts_at_the_first_full_year_of_payments():
    # MU restarted its dividend late in 2021 (2 payments), then paid 4 a year; measuring from that
    # short year reported 23%/yr on a flat dividend. Here 2019 has one payment, then 10%/yr.
    divs = [("2019-11-15", 0.10)] + _quarterly_dividends(2020, 2025, 2.0, 0.10)
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["cagr_years"] == 5
    assert res.assumptions["hist_cagr"] == pytest.approx(0.10)


def test_value_ddm_cagr_skips_every_short_year_at_the_start():
    # A partial first year, a year without dividends, then a partial restart: growth starts at the first
    # full year, not at the restart (code review 1: 20.6% on a synthetic 10%/yr series).
    divs = [("2016-11-15", 0.10), ("2018-11-15", 0.10)] + _quarterly_dividends(2019, 2025, 2.0, 0.10)
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["cagr_years"] == 6
    assert res.assumptions["hist_cagr"] == pytest.approx(0.10)


def test_value_ddm_scales_a_last_year_with_an_extra_payment_to_the_usual_count():
    # Realty Income pays monthly. January 2026's payment went ex-dividend on 2025-12-31, so 2025 holds
    # 13 payments, and growth read 4.58% instead of 3.66%. That year counts as its usual 12.
    rate = {y: 0.25 * 1.03 ** (y - 2016) for y in range(2016, 2027)}
    divs = [(f"{y}-{m:02d}-01", rate[y]) for y in range(2016, 2026) for m in range(1, 13)]
    divs += [("2025-12-31", rate[2025])] + [(f"2026-{m:02d}-01", rate[2026]) for m in range(2, 8)]
    res = value_ddm(_inputs(dividends=sorted(divs), as_of=date(2026, 8, 4)))
    assert res.assumptions["hist_cagr"] == pytest.approx(0.03)


def _cadence_dividends(counts_by_year, growth=0.05):
    """Yearly totals growing by *growth*, paid in counts_by_year[y] equal installments, plus 2026's
    payments through July (the TTM window as of 2026-08-04)."""
    divs = []
    for year, n in sorted(counts_by_year.items()):
        total = 2.0 * (1 + growth) ** (year - 2016)
        paid = max(1, n * 7 // 12) if year == 2026 else n
        divs += [(f"{year}-{1 + 12 * k // n:02d}-15", total / n) for k in range(paid)]
    return divs


@pytest.mark.parametrize("counts", [
    {y: 4 if y <= 2020 else 12 for y in range(2016, 2027)},  # quarterly -> monthly
    {y: 2 if y <= 2020 else 4 for y in range(2016, 2027)},   # semiannual -> quarterly
    {y: 1 if y <= 2022 else 4 for y in range(2016, 2027)},   # annual -> quarterly
    {y: 1 if y <= 2018 else 4 for y in range(2016, 2027)},   # PGR: annual years, then quarterly
    {y: 4 if y <= 2016 else 12 for y in range(2015, 2027)},  # the window opens on the last quarterly year
    {y: 1 if y <= 2024 else 2 for y in range(2016, 2027)},   # annual -> semiannual in the last full year
    {y: 1 if y <= 2023 else 2 for y in range(2016, 2027)},   # ... in the last two full years (review 4)
    {y: 2 if y <= 2017 else 1 for y in range(2016, 2027)},   # semiannual -> annual after two years
    {y: 4 if y <= 2017 else 3 for y in range(2016, 2027)},   # quarterly -> three a year
])
def test_value_ddm_growth_survives_a_payment_frequency_change(counts):
    # Paying the same yearly amount more often makes neither a partial first year nor a long last
    # one; the last full year used to be scaled down (or the early years dropped), and 5% read as 0%.
    res = value_ddm(_inputs(dividends=_cadence_dividends(counts), as_of=date(2026, 8, 4)))
    assert res.assumptions["cagr_years"] == 9
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)


def test_value_ddm_skips_a_restart_year_with_as_many_payments_as_the_suspension_year():
    # A flat quarterly dividend suspended after June 2015 and restarted in September 2016: both years hold
    # 2 payments, so 2016 passed as a lower-frequency year and growth read 8%/yr on a flat dividend. A lower
    # frequency pays more each time; a restart pays the same.
    def quarterly(year, months):
        return [(f"{year}-{m:02d}-15", 0.25) for m in months]

    divs = quarterly(2014, (3, 6, 9, 12)) + quarterly(2015, (3, 6)) + quarterly(2016, (9, 12))
    for year in range(2017, 2026):
        divs += quarterly(year, (3, 6, 9, 12))
    divs += quarterly(2026, (3, 6))
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["cagr_years"] == 8
    assert res.assumptions["hist_cagr"] == pytest.approx(0.0)


def test_value_ddm_skips_a_first_year_that_opens_the_record_with_one_larger_payment():
    # HWM's record starts in November 2016 with one payment 1.5x its later quarterly ones (the dividend was
    # cut after a separation): nothing paid the year before, so it is a partial year, not an annual schedule.
    divs = [("2016-11-15", 1.0)] + _quarterly_dividends(2017, 2025, 2.0, 0.10)
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["cagr_years"] == 8
    assert res.assumptions["hist_cagr"] == pytest.approx(0.10)


def test_value_ddm_counts_a_first_year_with_a_slipped_payment_at_its_usual_count():
    # BMY's 2020 holds 5 ex-dates: one payment slipped across the year boundary. As the first year
    # of the window it counted in full and understated growth; it counts as its usual 4.
    divs = sorted(_quarterly_dividends(2016, 2025, 2.0, 0.10) + [("2016-01-04", 0.5)])
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["cagr_years"] == 9
    assert res.assumptions["hist_cagr"] == pytest.approx(0.10)


def test_value_ddm_leaves_a_special_in_the_last_year_as_it_is():
    # A long last year is scaled only for one extra regular-size payment (O's slipped ex-date). A
    # special dividend stays as it is: whether specials count is the user's question (Q3).
    divs = sorted(_quarterly_dividends(2016, 2025, 2.0, 0.10) + [("2025-07-01", 7.0)])
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    total_2025 = 2.0 * 1.1 ** 9 + 7.0
    assert res.assumptions["hist_cagr"] == pytest.approx((total_2025 / 2.0) ** (1 / 9) - 1)


def test_value_ddm_keeps_a_short_last_year_as_it_is():
    # Two payments in the last full year can be a real cut, so that year isn't scaled up.
    divs = [d for d in _quarterly_dividends(2016, 2025, 2.0, 0.10) if d[0] < "2025-07-01"]
    divs += [("2026-03-15", 0.5), ("2026-06-15", 0.5)]
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    half_of_2025 = 2.0 * 1.1 ** 9 / 2
    assert res.assumptions["hist_cagr"] == pytest.approx((half_of_2025 / 2.0) ** (1 / 9) - 1)


def test_value_ddm_keeps_a_full_first_year_when_a_later_year_has_a_special():
    # COST: 2017 adds a $7 special to 4 regular payments. A first year is partial against the
    # window's usual cadence, not against a neighbour that a special pushed to 5 payments.
    divs = sorted(_quarterly_dividends(2016, 2025, 2.0, 0.10) + [("2017-07-01", 7.0)])
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["cagr_years"] == 9
    assert res.assumptions["hist_cagr"] == pytest.approx(0.10)


@pytest.mark.parametrize("first_year, hist_cagr", [(2021, None), (2020, 0.05)])
def test_value_ddm_growth_needs_4_full_years_of_dividends(first_year, hist_cagr):
    # Spec: the historical CAGR "requires >= 4 FYs". DDM stays applicable from 3 calendar years,
    # but with fewer full years no CAGR is measured (TMUS's 2 full years read 29%/yr from a
    # single year's change).
    res = value_ddm(_inputs(dividends=_quarterly_dividends(first_year, 2023, 1.00, 0.05)))
    assert res.applicable is True
    assert res.assumptions["hist_cagr"] == (None if hist_cagr is None else pytest.approx(hist_cagr))
    assert res.assumptions["growth_source"] == ("none" if hist_cagr is None else "hist_only")


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


# ---- Owner earnings volatility evidence ----
def test_owner_earnings_volatility_steady_history_has_no_collapses():
    history = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
    vol = owner_earnings_volatility(history)
    assert vol["collapse_years"] == 0
    assert vol["worst_drop"] < 0.1
    assert vol["positive_years"] == 10
    assert vol["total_years"] == 10


def test_owner_earnings_volatility_cyclical_history_has_collapses():
    history = [10.0, 2.0, 12.0, 3.0, 14.0, 4.0, 16.0, 5.0, 18.0, 6.0]
    vol = owner_earnings_volatility(history)
    assert vol["collapse_years"] >= 2
    assert vol["worst_drop"] > 0.5


def test_value_owner_earnings_assumptions_contain_volatility():
    recs = [_oe_fy(fy, ni=200.0 * 1.05 ** i) for i, fy in enumerate(range(2016, 2026))]
    res = value_owner_earnings(_inputs(fy_records=recs, risk_free_rate=0.045))
    assert res.applicable is True
    vol = res.assumptions["volatility"]
    assert set(vol.keys()) == {
        "collapse_years", "worst_drop", "positive_years", "total_years",
    }


def test_owner_earnings_volatility_caps_a_swing_to_a_loss_at_100pct():
    """A year that swings to a loss has wiped out its earnings, not fallen 334%.
    SLB's raw figure was 334% — a number that reads as nonsense rather than as
    evidence, which defeats the point of showing it."""
    vol = owner_earnings_volatility([10.0, -24.0, 10.0, 10.0])
    assert vol["worst_drop"] == 1.0


@pytest.mark.parametrize("source, flagged", [("treasury_gov", False), ("estimate", True)])
def test_rate_using_models_record_the_rates_day_and_source(source, flagged):
    # Spec: "the assumption JSON stores the rate and its as-of date so staleness is visible in the UI" (the
    # VAL tab lists every assumption); the static estimate, which has no day, is flagged.
    rate = dict(risk_free_rate=0.0515, risk_free_rate_date=None if flagged else "2026-10-19",
                risk_free_rate_source=source)
    results = [
        value_dcf(_inputs(fy_records=[_fy(fy, fcf=100.0 * 1.05 ** i) for i, fy in enumerate(range(2019, 2024))],
                          **rate)),
        value_ddm(_inputs(dividends=_quarterly_dividends(2019, 2023, 1.00, 0.05), **rate)),
        value_owner_earnings(_inputs(fy_records=[_oe_fy(fy) for fy in range(2016, 2026)], **rate)),
    ]
    for res in results:
        assert res.applicable, res.model
        assert (res.assumptions["risk_free_rate_as_of"], res.assumptions["risk_free_rate_source"]) == (
            rate["risk_free_rate_date"], source), res.model
        assert res.assumptions.get("rf_estimate", False) is flagged, res.model

