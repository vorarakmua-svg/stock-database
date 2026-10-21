"""Quality scores (Milestone 2): Piotroski F, Altman Z, Beneish M, Sloan, ROIC vs WACC, predictability, and the
inputs of the Graham net-net and Magic Formula screens. docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
import math

import pytest

from src.scores.fundamentals import AnnualRow, Fundamentals
from src.scores.scores import (
    Market,
    altman,
    beneish,
    compute_scores,
    piotroski,
    predictability,
    sloan,
    stars_for,
    wacc,
)


def _row(fy, shares=100.0, **figures):
    base = dict(net_income=100.0, total_assets=1050.0, operating_cash_flow=130.0, long_term_debt=280.0,
                short_term_debt=20.0, current_assets=450.0, current_liabilities=210.0, revenue=1000.0,
                gross_profit=420.0, cost_of_revenue=580.0, operating_income=150.0, retained_earnings=500.0,
                total_liabilities=600.0, accounts_receivable=100.0, ppe_net=300.0, depreciation_amortization=30.0,
                sga_expense=200.0, investing_cash_flow=-50.0, interest_expense=15.0, income_tax_expense=25.0,
                pretax_income=125.0, cash_and_equivalents=80.0, total_equity=450.0, roic=0.15, ebit=150.0,
                net_debt=220.0, ebitda=180.0)
    base.update(figures)
    return AnnualRow(fiscal_year=fy, period_end=f"{fy}-12-31", shares=shares, eps=base["net_income"] / shares,
                     figures=base)


PREV = dict(net_income=80.0, total_assets=1000.0, operating_cash_flow=100.0, long_term_debt=300.0,
            current_assets=400.0, current_liabilities=200.0, revenue=900.0, gross_profit=360.0, cost_of_revenue=540.0)


# ---------- Piotroski F ----------

def test_piotroski_counts_nine_passed_tests():
    f, tested, tests = piotroski(_row(2025, shares=99.0), _row(2024, **PREV))
    assert (f, tested) == (9, 9)
    assert all(tests.values()) and len(tests) == 9


def test_piotroski_fails_what_got_worse():
    worse = _row(2025, shares=105.0, net_income=50.0, operating_cash_flow=40.0, long_term_debt=400.0,
                 current_assets=300.0, gross_profit=330.0, revenue=880.0)
    f, tested, tests = piotroski(worse, _row(2024, **PREV))
    assert tests["roa_positive"] and tests["cfo_positive"]
    for failed in ("roa_up", "cfo_above_ni", "leverage_down", "current_ratio_up", "no_new_shares",
                   "gross_margin_up", "asset_turnover_up"):
        assert tests[failed] is False, failed
    assert (f, tested) == (2, 9)


def test_an_unchanged_year_improves_nothing():
    # "Rose" and "fell" are strict: an identical year passes only the level tests.
    f, tested, tests = piotroski(_row(2025), _row(2024))
    assert {k for k, v in tests.items() if v} == {"roa_positive", "cfo_positive", "cfo_above_ni", "no_new_shares"}
    assert (f, tested) == (4, 9)


def test_a_test_without_its_inputs_is_not_tested():
    f, tested, tests = piotroski(_row(2025, shares=99.0, current_assets=None), _row(2024, **PREV))
    assert tests["current_ratio_up"] is None
    assert (f, tested) == (8, 8)


# ---------- Altman Z ----------

def test_altman_z_and_its_zone():
    z, zone = altman(_row(2025), market_cap=2000.0)
    # 1.2*240/1050 + 1.4*500/1050 + 3.3*150/1050 + 0.6*2000/600 + 1.0*1000/1050
    assert z == pytest.approx(4.364762, rel=1e-6)
    assert zone == "safe"
    assert altman(_row(2025, retained_earnings=-2000.0), market_cap=100.0)[1] == "distress"
    assert altman(_row(2025), market_cap=None) == (None, None)


# ---------- Beneish M ----------

def test_beneish_m_is_its_intercept_and_weights_when_nothing_changed():
    same = _row(2024, net_income=130.0)  # net income = CFO, so TATA is 0
    m, variables = beneish(_row(2025, net_income=130.0), same)
    assert all(variables[k] == pytest.approx(1.0) for k in ("dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi"))
    assert variables["tata"] == pytest.approx(0.0)
    assert m == pytest.approx(-2.48)


def test_beneish_flags_receivables_outrunning_sales():
    m, variables = beneish(_row(2025, net_income=130.0, accounts_receivable=200.0), _row(2024, net_income=130.0))
    assert variables["dsri"] == pytest.approx(2.0)
    assert m == pytest.approx(-2.48 + 0.920)  # -1.56, above the -1.78 line


def test_beneish_takes_sga_and_dep_as_neutral_when_missing():
    m, variables = beneish(_row(2025, net_income=130.0, sga_expense=None), _row(2024, net_income=130.0))
    assert variables["sgai"] == 1.0 and m == pytest.approx(-2.48)
    assert beneish(_row(2025, accounts_receivable=None), _row(2024))[0] is None  # any other gap: no M


# ---------- Sloan, WACC ----------

def test_sloan_ratio():
    assert sloan(_row(2025)) == pytest.approx((100.0 - 130.0 + 50.0) / 1050.0)
    assert sloan(_row(2025, investing_cash_flow=None)) is None


def test_wacc_weighs_the_capm_equity_cost_and_the_after_tax_debt_cost():
    w = wacc(_row(2025), Market(market_cap=2000.0, risk_free_rate=0.04, beta=1.2))
    # Re = 0.04 + 1.2 * 4.5% = 9.4%; Rd = 15 / 300 = 5%; t = 25 / 125 = 20%
    assert w == pytest.approx(2000 / 2300 * 0.094 + 300 / 2300 * 0.05 * 0.8)
    # A debt cost far outside [rf, rf + 10%] is clamped: 150 / 300 = 50% reads 14%.
    w = wacc(_row(2025, interest_expense=150.0), Market(market_cap=2000.0, risk_free_rate=0.04, beta=1.2))
    assert w == pytest.approx(2000 / 2300 * 0.094 + 300 / 2300 * 0.14 * 0.8)
    assert wacc(_row(2025), Market(market_cap=None, risk_free_rate=0.04, beta=1.2)) is None


def test_wacc_without_a_pretax_figure_takes_the_default_tax():
    w = wacc(_row(2025, pretax_income=None), Market(market_cap=2000.0, risk_free_rate=0.04, beta=1.2))
    assert w == pytest.approx(2000 / 2300 * 0.094 + 300 / 2300 * 0.05 * (1 - 0.21))


# ---------- Predictability ----------

def _steady(years=10, growth=0.10, ebitda=None, sector="general", **extra):
    rows = []
    for i in range(years):
        revenue = 1000.0 * (1 + growth) ** i
        e = ebitda(i, revenue) if ebitda else 0.2 * revenue
        rows.append(_row(2016 + i, revenue=revenue, ebitda=e, **extra))
    return Fundamentals(ticker="AAA", sector_class=sector, rows=rows)


def test_steady_growth_is_five_stars():
    stars, r2 = predictability(_steady())
    assert stars == 5.0 and r2["revenue"] == pytest.approx(1.0) and r2["ebitda"] == pytest.approx(1.0)


def test_any_loss_or_a_falling_trend_is_one_star():
    assert predictability(_steady(ebitda=lambda i, r: -5.0 if i == 4 else 0.2 * r))[0] == 1.0
    assert predictability(_steady(growth=-0.05))[0] == 1.0


def test_a_noisier_record_earns_fewer_stars_in_half_steps():
    wobble = [1.0, 1.3, 0.8, 1.25, 0.9, 1.35, 0.95, 1.3, 1.0, 1.4]
    stars, r2 = predictability(_steady(ebitda=lambda i, r: 0.2 * r * wobble[i]))
    expected = math.floor((1 + 4 * min(r2["revenue"], r2["ebitda"])) * 2) / 2
    assert stars == expected and stars < 5.0 and stars * 2 == int(stars * 2)


def test_stars_round_down_to_the_half_star():
    assert stars_for(1.0) == 5.0
    assert stars_for(0.875) == 4.5
    assert stars_for(0.70) == 3.5  # 3.8 rounds down, never up to 4
    assert stars_for(0.0) == 1.0


def test_fewer_than_ten_years_is_not_rated():
    assert predictability(_steady(years=9)) == (None, {})


def test_a_bank_is_rated_on_eps():
    rows = _steady(sector="bank").rows
    for r in rows:
        r.figures["ebitda"] = None
        r.eps = r.figures["revenue"] * 0.1 / 100.0
    stars, r2 = predictability(Fundamentals(ticker="BBB", sector_class="bank", rows=rows))
    assert stars == 5.0 and "eps" in r2


# ---------- compute_scores ----------

def test_compute_scores_puts_it_together():
    f = Fundamentals(ticker="AAA", rows=_steady().rows)
    f.rows[-2] = _row(2024, revenue=f.rows[-2].figures["revenue"], ebitda=f.rows[-2].figures["ebitda"], **{
        k: v for k, v in PREV.items() if k != "revenue"})
    s = compute_scores(f, Market(market_cap=2000.0, risk_free_rate=0.04, beta=1.2, price=20.0))
    assert s.piotroski_tested == 9 and s.altman_zone == "safe" and s.beneish_m is not None
    assert s.roic == 0.15 and s.roic_minus_wacc == pytest.approx(0.15 - s.wacc)
    assert s.ncav_per_share == pytest.approx((450.0 - 600.0) / 100.0)
    assert s.earnings_yield == pytest.approx(150.0 / (2000.0 + 220.0))
    # Greenblatt's capital: (current assets - cash) - (current liabilities - short-term debt) + net PPE
    assert s.return_on_capital == pytest.approx(150.0 / ((450.0 - 80.0) - (210.0 - 20.0) + 300.0))


def test_a_bank_gets_reasons_not_scores():
    f = _steady(sector="bank")
    s = compute_scores(f, Market(market_cap=2000.0, risk_free_rate=0.04, beta=1.2, price=20.0))
    assert s.piotroski_f is None and "banks" in s.na_reasons["piotroski"]
    assert s.altman_z is None and s.beneish_m is None and s.wacc is None
    assert s.sloan_ratio is not None  # needs no sector exception


def test_one_year_is_not_enough_for_the_changes():
    s = compute_scores(Fundamentals(ticker="AAA", rows=[_row(2025)]), Market(market_cap=2000.0))
    assert s.piotroski_f is None and s.beneish_m is None
    assert s.altman_z is not None  # a single year's balance sheet suffices


def test_a_utility_has_no_altman_z():
    # The original Z was fitted on manufacturers: a regulated utility's heavy assets and slow turnover read as
    # distress (D, DUK, NEE, SO all below 1.2). Z'' for non-manufacturers waits for M5's SIC codes.
    s = compute_scores(_steady(sector="utility"), Market(market_cap=2000.0, risk_free_rate=0.04, beta=1.2))
    assert s.altman_z is None and s.na_reasons["altman"] == "not meaningful for utilities"
    assert s.beneish_m is not None and s.wacc is not None  # the rest apply

