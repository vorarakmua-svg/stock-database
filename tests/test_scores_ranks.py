"""Quality scores (Milestone 2): momentum, the five percentile ranks, the composite and the Magic Formula rank.
docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
from datetime import date, timedelta

import pytest

from src.scores.ranks import (
    MIN_INDUSTRY_PEERS,
    MIN_SECTOR_PEERS,
    RANKS,
    momentum,
    percentiles,
    rank_industries,
    rank_universe,
)


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


# ---------------------------------------------------------------------------------------------------------------
# Within the industry (Milestone 5): docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and
# industries"


def _member(ticker, sic, **components):
    row = _row(ticker, **components)
    row["sic"] = sic
    return row


def _ten(sic="3571", prefix="C", **per_index):
    """Ten companies with *sic*: each keyword a rank whose components are a function of the company's index."""
    return [_member(f"{prefix}{i}", sic, **{rank: make(i) for rank, make in per_index.items()}) for i in range(10)]


def _groups(rows, group="357", others=None):
    return {row["ticker"]: (group if row["sic"].startswith(group) else others) for row in rows}


def test_the_industry_peers_are_ten():
    assert MIN_INDUSTRY_PEERS == 10


def test_industry_ranks_are_deciles_within_the_group():
    rows = _ten(growth=lambda i: {"rev_ps_cagr_3y": (i + 1) / 100, "rev_ps_cagr_5y": (i + 1) / 100},
                valuation=lambda i: {"price_to_value": 0.5 + i / 10})
    # Five banks with no group, faster and cheaper than every one of them: they move the universe's ranks only.
    rows += [_member(f"B{i}", "6021", growth={"rev_ps_cagr_3y": 1.0, "rev_ps_cagr_5y": 1.0},
                     valuation={"price_to_value": 0.1}) for i in range(5)]
    out = rank_industries(rows, _groups(rows))
    assert [out[f"C{i}"]["ind_rank_growth"] for i in range(10)] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert [out[f"C{i}"]["ind_rank_valuation"] for i in range(10)] == [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]  # lower: better
    # The tied components put every member at the top.
    assert {out[f"C{i}"][f"ind_rank_{r}"] for i in range(10) for r in ("strength", "profitability", "momentum")} == {
        10}
    # 10 x the mean of (10, 10, i + 1, 10 - i, 10).
    assert all(out[f"C{i}"]["ind_composite"] == pytest.approx(82.0) for i in range(10))
    # Against the universe (fifteen), the fastest of the ten is the 10th of 15.
    assert rank_universe(rows)["C9"]["rank_growth"] == 7


def test_a_company_without_a_group_has_no_industry_ranks():
    rows = _ten() + [_member("B0", "6021"), _member("N0", None)]
    out = rank_industries(rows, {**_groups(rows[:10]), "B0": None, "N0": None})
    for ticker in ("B0", "N0"):
        assert out[ticker] == {**{f"ind_rank_{r}": None for r in RANKS}, "ind_composite": None}
    assert out["C0"]["ind_composite"] == pytest.approx(100.0)


@pytest.mark.parametrize("valued, expected", [
    (10, [7] * 10),  # altman_z read: each mean (1 + (i + 1) / 10 + (10 - i) / 10) / 3 = 0.7
    (9, [6, 6, 7, 7, 8, 8, 9, 9, 10, 10]),  # left out: each mean (1 + (i + 1) / 10) / 2
])
def test_a_component_fewer_than_ten_members_have_is_left_out(valued, expected):
    def strength(i):
        z = (10 - i if i < 9 else 0.5) if i < valued else None
        return {"interest_coverage": 10.0, "equity_to_assets": (i + 1) / 10, "altman_z": z}

    rows = _ten(strength=strength)
    out = rank_industries(rows, _groups(rows))
    assert [out[f"C{i}"]["ind_rank_strength"] for i in range(10)] == expected


def test_a_rank_needs_its_components_ranked_within_the_industry():
    # Nine of the ten have a Piotroski share: it's left out, and one component is too few for profitability.
    def profitability(i):
        return {"operating_margin": (i + 1) / 100, "piotroski_share": 0.7 if i < 9 else None}

    rows = _ten(profitability=profitability)
    out = rank_industries(rows, _groups(rows))
    assert {out[f"C{i}"]["ind_rank_profitability"] for i in range(10)} == {None}
    assert rank_universe(rows)["C0"]["rank_profitability"] is not None  # the universe reads both
    assert {out[f"C{i}"]["ind_rank_momentum"] for i in range(10)} == {10}  # momentum's one component is enough


def test_the_industry_composite_is_ten_times_the_mean_and_needs_four_ranks():
    rows = [_member(f"C{i}", "3571", growth={"rev_ps_cagr_3y": (i + 1) / 100, "rev_ps_cagr_5y": (i + 1) / 100})
            for i in range(12)]
    rows[10]["components"]["momentum"] = {"momentum": None}  # four ranks
    rows[11]["components"]["momentum"] = {"momentum": None}  # three ranks
    rows[11]["components"]["valuation"] = {"price_to_value": None}
    out = rank_industries(rows, _groups(rows))
    c10, c11 = out["C10"], out["C11"]
    assert c10["ind_rank_momentum"] is None and c10["ind_rank_valuation"] == 10
    assert c10["ind_composite"] == pytest.approx(10 * (10 + 10 + c10["ind_rank_growth"] + 10) / 4)
    assert c11["ind_rank_valuation"] is None and c11["ind_composite"] is None
    assert out["C0"]["ind_composite"] == pytest.approx(10 * (10 + 10 + out["C0"]["ind_rank_growth"] + 10 + 10) / 5)


def test_a_2_digit_group_ranks_against_all_its_companies():
    # 357 has ten of its own; Deere (3523) falls back to 35, whose members are all eleven.
    rows = _ten(growth=lambda i: {"rev_ps_cagr_3y": (i + 1) / 100, "rev_ps_cagr_5y": (i + 1) / 100})
    rows.append(_member("DE", "3523", growth={"rev_ps_cagr_3y": 0.055, "rev_ps_cagr_5y": 0.055}))
    out = rank_industries(rows, {**_groups(rows[:10]), "DE": "35"})
    assert out["DE"]["ind_rank_growth"] == 6  # six of eleven at or below it: ceil(10 x 6 / 11)
    assert out["DE"]["ind_rank_strength"] == 10
    assert [out[f"C{i}"]["ind_rank_growth"] for i in range(10)] == list(range(1, 11))  # 357's own ten, unchanged


def test_the_industry_ranks_leave_the_universe_ranks_alone():
    rows = _ten(growth=lambda i: {"rev_ps_cagr_3y": (i + 1) / 100, "rev_ps_cagr_5y": (i + 1) / 100})
    before = rank_universe(rows)
    rank_industries(rows, _groups(rows))
    assert rank_universe(rows) == before
