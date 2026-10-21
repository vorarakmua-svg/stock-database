"""Quality scores (Milestone 2): momentum, the five percentile ranks, the composite and the Magic Formula rank.
docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
from datetime import date, timedelta

import pytest

from src.scores.ranks import MIN_SECTOR_PEERS, momentum, percentiles, rank_universe


def _month_ends(n, start=date(2024, 1, 31)):
    days, (y, m) = [], (start.year, start.month)
    for _ in range(n):
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        days.append((nxt - timedelta(days=1)).isoformat())
        y, m = nxt.year, nxt.month
    return days


def test_momentum_averages_the_12_1_and_6_1_returns_over_beta():
    days = _month_ends(13)  # 2024-01-31 .. 2025-01-31
    closes = [100.0] * 6 + [110.0] * 6 + [150.0]  # flat, then +10% from six months back, a jump in the last month
    bars = list(zip(days, closes))
    # P[-1m] (2024-12-31) = 110; P[-12m] (2024-01-31) = 100; P[-6m] (2024-07-31) = 110 -> (0.10 + 0.0) / 2 / 1.25
    assert momentum(bars, beta=1.25) == pytest.approx(0.04)
    assert momentum(bars, beta=0.2) == pytest.approx(0.05 / 0.5)  # beta floored at 0.5
    assert momentum(bars, beta=None) == pytest.approx(0.05)  # no beta: 1
    assert momentum(bars[-6:], beta=1.0) is None  # less than a year of bars


def test_percentiles_run_in_the_metrics_direction():
    values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
    assert percentiles(values, lower_better=False) == {"A": 0.25, "B": 0.5, "C": 0.75, "D": 1.0}
    assert percentiles(values, lower_better=True) == {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.25}
    assert percentiles({"A": 1.0, "B": 1.0}, lower_better=False) == {"A": 1.0, "B": 1.0}  # ties share the top


def _row(ticker, sector="general", **components):
    base = {"strength": {"interest_coverage": 10.0, "altman_z": 4.0},
            "profitability": {"operating_margin": 0.2, "piotroski_share": 0.7},
            "growth": {"rev_ps_cagr_3y": 0.05, "rev_ps_cagr_5y": 0.05},
            "valuation": {"price_to_value": 1.0},
            "momentum": {"momentum": 0.05}}
    for rank, values in components.items():
        base[rank] = values
    return {"ticker": ticker, "sector_class": sector, "components": base, "earnings_yield": 0.05,
            "return_on_capital": 0.2}


def test_ranks_are_deciles_of_the_mean_percentile():
    rows = [_row(t, growth={"rev_ps_cagr_3y": g, "rev_ps_cagr_5y": g}) for t, g in
            (("A", 0.01), ("B", 0.02), ("C", 0.03), ("D", 0.04))]
    out = rank_universe(rows)
    assert [out[t]["rank_growth"] for t in "ABCD"] == [3, 5, 8, 10]  # ceil(10 x 0.25, 0.5, 0.75, 1.0)
    assert out["A"]["rank_strength"] == 10  # all tied: every one at the top


def test_a_lower_is_better_component_ranks_the_cheapest_highest():
    rows = [_row(t, valuation={"price_to_value": v}) for t, v in (("A", 0.6), ("B", 0.9), ("C", 1.2), ("D", 1.5))]
    out = rank_universe(rows)
    assert [out[t]["rank_valuation"] for t in "ABCD"] == [10, 8, 5, 3]


def test_a_rank_needs_its_components():
    rows = [_row("A", strength={"interest_coverage": 10.0, "altman_z": None}), _row("B")]
    out = rank_universe(rows)
    assert out["A"]["rank_strength"] is None  # one of two: not enough
    assert out["B"]["rank_strength"] is not None
    assert out["A"]["rank_momentum"] is not None  # a one-component rank needs its one


def test_the_composite_is_ten_times_the_mean_rank_and_needs_four():
    rows = [_row("A"), _row("B", momentum={"momentum": None}, valuation={"price_to_value": None})]
    out = rank_universe(rows)
    ranks = [out["A"][f"rank_{r}"] for r in ("strength", "profitability", "growth", "valuation", "momentum")]
    assert out["A"]["composite_score"] == pytest.approx(10 * sum(ranks) / 5)
    assert out["B"]["composite_score"] is None  # three ranks


def test_a_large_sector_ranks_within_itself():
    banks = [_row(f"B{i}", sector="bank", growth={"rev_ps_cagr_3y": 0.10 + i / 100, "rev_ps_cagr_5y": 0.10 + i / 100})
             for i in range(MIN_SECTOR_PEERS)]
    others = [_row(f"G{i}", growth={"rev_ps_cagr_3y": i / 100, "rev_ps_cagr_5y": i / 100}) for i in range(5)]
    out = rank_universe(banks + others)
    # The slowest bank is the slowest of ten banks (10th percentile), however fast against the rest.
    assert out["B0"]["rank_growth"] == 1
    # A sector under ten ranks against the whole universe: G4 is 5th fastest of 15.
    assert out["G4"]["rank_growth"] == 4  # ceil(10 x 5/15) = 4


def test_the_magic_formula_sums_two_ranks_and_skips_financials():
    rows = [_row("A"), _row("B"), _row("C"), _row("BK", sector="bank")]
    rows[0].update(earnings_yield=0.10, return_on_capital=0.10)
    rows[1].update(earnings_yield=0.05, return_on_capital=0.50)
    rows[2].update(earnings_yield=0.02, return_on_capital=0.05)
    out = rank_universe(rows)
    # A: EY 1st + ROC 2nd = 3; B: 2nd + 1st = 3; C: 3rd + 3rd = 6. Ties go to the higher earnings yield.
    assert (out["A"]["magic_formula_rank"], out["B"]["magic_formula_rank"], out["C"]["magic_formula_rank"]) == (1, 2, 3)
    assert out["BK"]["magic_formula_rank"] is None
