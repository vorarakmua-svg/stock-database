"""Sector-aware ratio functions and the suppression/orchestration layer."""

import pytest

from src.parsers.calculated_metrics import CalculatedMetrics
from src.parsers.sector_metrics import apply_sector, bank_metrics, insurer_metrics, reit_metrics

_EV_FAMILY = ("enterprise_value", "ev_to_ebitda", "ev_to_revenue", "ev_to_fcf", "fcf_yield")


# Net debt 250, EBITDA 120, FCF 60, revenue 500.
_YEAR = {"revenue": 500.0, "operating_income": 100.0, "depreciation_amortization": 20.0,
         "operating_cash_flow": 90.0, "capex": 30.0, "long_term_debt": 300.0, "short_term_debt": 50.0,
         "cash_and_equivalents": 100.0}


def _ev_family(sector):
    """The EV family of a company with _YEAR's figures at a market cap of 1,000."""
    metrics = CalculatedMetrics().calculate_all(_YEAR, market_data={"current_price": 10.0, "market_cap": 1000.0},
                                                sector=sector)
    return {key: metrics.get(key) for key in _EV_FAMILY}


def test_a_general_companys_ev_family_is_computed():
    assert _ev_family(None) == pytest.approx({"enterprise_value": 1250.0, "ev_to_ebitda": 1250.0 / 120.0,
                                              "ev_to_revenue": 2.5, "ev_to_fcf": 1250.0 / 60.0, "fcf_yield": 0.06})


def test_a_banks_ev_family_goes_with_its_net_debt_ebitda_and_fcf():
    # BUG-134: a bank's net debt, EBITDA and FCF are suppressed (deposits fund it), but its EV family was computed
    # from them before the suppression: JPM's EV read $1.03T (Yahoo's $474B) and its FCF yield -15%.
    assert _ev_family("bank") == dict.fromkeys(_EV_FAMILY)


def test_an_insurers_ev_to_ebitda_goes_with_its_ebitda():
    family = _ev_family("insurance")
    assert family["ev_to_ebitda"] is None
    assert (family["enterprise_value"], family["fcf_yield"]) == pytest.approx((1250.0, 0.06))


def test_yearly_metrics_without_a_price_gain_no_ev_keys():
    # The EV family needs a price, which a bank's yearly metrics (`calculate_historical`, the as-of metrics) don't
    # have: its suppression adds nothing there, as the README has those metrics leave the EV ratios out.
    metrics = CalculatedMetrics().calculate_historical({"2025": _YEAR}, sector="bank")["2025"]
    assert not set(_EV_FAMILY) & metrics.keys()
    assert metrics["net_debt"] is None


def test_a_reits_fcf_ratios_go_with_its_fcf():
    family = _ev_family("reit")
    assert (family["ev_to_fcf"], family["fcf_yield"]) == (None, None)
    assert (family["enterprise_value"], family["ev_to_ebitda"]) == pytest.approx((1250.0, 1250.0 / 120.0))


def test_bank_metrics_compute_from_canonical_fields():
    f = {
        "net_interest_income": 50.0, "noninterest_income": 30.0,
        "noninterest_expense": 48.0, "total_assets": 1000.0,
        "total_loans": 600.0, "total_deposits": 900.0,
    }
    m = bank_metrics(f)
    assert m["efficiency_ratio"] == 48.0 / 80.0           # exact
    assert m["loan_to_deposit"] == 600.0 / 900.0          # exact
    assert m["net_interest_margin"] == 50.0 / 1000.0      # proxy


def test_bank_metrics_missing_inputs_are_none():
    assert bank_metrics({})["efficiency_ratio"] is None
    assert bank_metrics({})["loan_to_deposit"] is None
    assert bank_metrics({})["net_interest_margin"] is None


def test_bank_efficiency_ratio_requires_both_revenue_components():
    # Only NII present (noninterest income missing) -> incomplete denominator,
    # so efficiency_ratio must be None rather than a misleading partial ratio.
    m = bank_metrics({"net_interest_income": 50.0, "noninterest_expense": 48.0})
    assert m["efficiency_ratio"] is None


def test_apply_sector_bank_adds_and_suppresses():
    metrics = {
        "roic": 0.20, "inventory_turnover": 5.0, "ebitda": 100.0,
        "interest_coverage": 8.0, "net_debt": 10.0,
        "roe": 0.15, "net_margin": 0.10,
    }
    f = {
        "net_interest_income": 50.0, "noninterest_income": 30.0,
        "noninterest_expense": 48.0, "total_assets": 1000.0,
        "total_loans": 600.0, "total_deposits": 900.0,
    }
    apply_sector(metrics, f, "bank")
    # generic ratios that don't apply to a bank are nulled
    assert metrics["roic"] is None
    assert metrics["inventory_turnover"] is None
    assert metrics["ebitda"] is None
    assert metrics["interest_coverage"] is None
    assert metrics["net_debt"] is None
    # universally meaningful ones are kept
    assert metrics["roe"] == 0.15
    assert metrics["net_margin"] == 0.10
    # bank ratios are added
    assert metrics["efficiency_ratio"] == 48.0 / 80.0
    # proxy basis recorded
    assert "net_interest_margin" in metrics["_basis"]


def test_apply_sector_none_is_noop():
    metrics = {"roic": 0.2, "inventory_turnover": 5.0}
    apply_sector(metrics, {}, None)
    assert metrics == {"roic": 0.2, "inventory_turnover": 5.0}


def test_insurer_metrics_compute():
    f = {"premiums_earned": 200.0, "claims_incurred": 150.0,
         "benefits_and_expenses": 190.0}
    m = insurer_metrics(f)
    assert m["loss_ratio"] == 150.0 / 200.0          # exact
    assert m["combined_ratio"] == 190.0 / 200.0      # proxy


def test_apply_sector_insurance_adds_and_suppresses():
    metrics = {"roic": 0.2, "inventory_turnover": 5.0, "ebitda": 100.0,
               "interest_coverage": 8.0, "roe": 0.12}
    f = {"premiums_earned": 200.0, "claims_incurred": 150.0,
         "benefits_and_expenses": 190.0}
    apply_sector(metrics, f, "insurance")
    assert metrics["roic"] is None
    assert metrics["inventory_turnover"] is None
    assert metrics["ebitda"] is None
    assert metrics["interest_coverage"] == 8.0   # kept for insurers
    assert metrics["loss_ratio"] == 150.0 / 200.0
    assert "combined_ratio" in metrics["_basis"]


def test_reit_ffo_and_derivatives():
    f = {"net_income": 100.0, "depreciation_amortization": 40.0,
         "capex": 10.0, "dividends_paid": 84.0,
         "weighted_avg_shares_diluted": 70.0}
    m = reit_metrics(f)
    assert m["ffo"] == 140.0                 # net_income + D&A
    assert m["affo"] == 130.0                # ffo - capex
    assert m["ffo_per_share"] == 2.0         # 140 / 70
    assert m["ffo_payout"] == 84.0 / 140.0   # dividends / ffo


def test_apply_sector_reit_adds_and_suppresses():
    metrics = {"roic": 0.2, "inventory_turnover": 5.0, "ebitda": 100.0,
               "interest_coverage": 8.0, "roe": 0.06}
    f = {"net_income": 100.0, "depreciation_amortization": 40.0,
         "capex": 10.0, "dividends_paid": 84.0,
         "weighted_avg_shares_diluted": 70.0}
    apply_sector(metrics, f, "reit")
    assert metrics["roic"] is None
    assert metrics["inventory_turnover"] is None
    assert metrics["ebitda"] == 100.0          # kept for REITs
    assert metrics["interest_coverage"] == 8.0  # kept for REITs
    assert metrics["ffo"] == 140.0
    assert "ffo" in metrics["_basis"]
    assert "affo" in metrics["_basis"]
