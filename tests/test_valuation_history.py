"""Valuation history (Milestone 1): multiples on totals, trailing 10-year windows, the historical fair value.

docs/superpowers/specs/2026-10-21-valuation-history-design.md
"""
from datetime import date, timedelta

import pytest

from src.valuation.history import (
    HISTORICAL_VALUE_CUTOFFS,
    compute_history,
    fair_value_metrics,
    historical_value_label,
    implied_price,
    multiples_at,
    window_stats,
)
from src.valuation.history_inputs import HistoryInputs, TTMPoint


def _point(**kw):
    base = dict(period_end="2025-12-31", known_on=date(2026, 2, 15), shares=100.0, revenue=1000.0,
                net_income=100.0, common_equity=500.0, fcf=80.0, ebitda=200.0, net_debt=300.0, ffo=None)
    base.update(kw)
    return TTMPoint(**base)


def test_multiples_are_taken_on_totals():
    m = multiples_at(2000.0, 20.0, _point(), 0.5)
    assert (m["pe"], m["ps"], m["pb"], m["pfcf"]) == pytest.approx((20.0, 2.0, 4.0, 25.0))
    assert m["ev_ebitda"] == pytest.approx(11.5)  # (2000 market cap + 300 net debt) / 200
    assert m["div_yield"] == pytest.approx(0.025)
    assert m["pffo"] is None  # no FFO outside REITs


def test_a_denominator_of_zero_or_less_gives_none():
    m = multiples_at(2000.0, 20.0, _point(net_income=-5.0, revenue=0.0, common_equity=-1.0, fcf=None,
                                          ebitda=0.0), 0.0)
    assert all(m[k] is None for k in ("pe", "ps", "pb", "pfcf", "ev_ebitda", "div_yield"))
    # An enterprise value of zero or less (net cash above the market cap) isn't a multiple either.
    assert multiples_at(100.0, 1.0, _point(net_debt=-500.0), 0.0)["ev_ebitda"] is None


def test_a_window_needs_36_months_of_values():
    assert window_stats([1.0] * 35 + [None] * 10, 1.0) is None
    values = [float(v) for v in range(1, 37)]
    s = window_stats(values, 36.0)
    assert s is not None
    assert (s["n"], s["min"], s["median"], s["max"]) == (36, 1.0, 18.5, 36.0)
    assert (s["p25"], s["p75"]) == pytest.approx((9.75, 27.25))  # inclusive quartiles
    assert s["pct_rank"] == 1.0  # at the window's max
    low = window_stats(values, 1.0)
    assert low is not None and low["pct_rank"] == pytest.approx(1 / 36)  # at its min
    assert window_stats(values, None)["pct_rank"] is None


def test_the_combined_metrics_follow_the_sector_rules():
    assert fair_value_metrics("general") == ("pe", "ps", "pb", "pfcf", "ev_ebitda")
    assert fair_value_metrics("utility") == fair_value_metrics("energy") == fair_value_metrics("general")
    assert fair_value_metrics("bank") == ("pe", "ps", "pb")
    assert fair_value_metrics("insurance") == ("pe", "ps", "pb", "pfcf")
    assert fair_value_metrics("reit") == ("pffo", "ps", "pb", "ev_ebitda")


def test_implied_prices_apply_a_multiple_to_the_fundamental():
    p = _point()
    assert implied_price("pe", 20.0, p, 100.0) == pytest.approx(20.0)  # 20 x 100 earnings / 100 shares
    assert implied_price("ev_ebitda", 11.5, p, 100.0) == pytest.approx(20.0)  # (11.5 x 200 - 300 debt) / 100
    assert implied_price("pe", 20.0, _point(net_income=-1.0), 100.0) is None
    assert implied_price("ev_ebitda", 1.0, p, 100.0) is None  # the debt exceeds the implied enterprise value
    assert implied_price("ps", 2.0, p, None) is None


@pytest.mark.parametrize("price,label", [(69.99, "far below"), (70.0, "below"), (89.99, "below"), (90.0, "near"),
                                         (110.0, "near"), (110.01, "above"), (130.0, "above"),
                                         (130.01, "far above")])
def test_labels_at_their_cutoffs(price, label):
    assert HISTORICAL_VALUE_CUTOFFS == (0.7, 0.9, 1.1, 1.3)
    assert historical_value_label(price, 100.0) == label


def test_no_label_without_a_fair_value():
    assert historical_value_label(50.0, None) is None
    assert historical_value_label(None, 100.0) is None


# ---------- compute_history on hand-built inputs ----------

def _month_ends(first, count):
    months, (y, m) = [], (first.year, first.month)
    for _ in range(count):
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        months.append((nxt - timedelta(days=1)).isoformat())
        y, m = nxt.year, nxt.month
    return months


def _inputs(n_months=60, close=20.0, sector="general", **point_kw):
    """Month ends from 2021-01-31, a quarter's figures known 45 days after it ends (from 2020-06-30)."""
    months = _month_ends(date(2021, 1, 1), n_months)
    ends = [date(y, m, d) for y in range(2020, 2040) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    points = [_point(period_end=e.isoformat(), known_on=e + timedelta(days=45), **point_kw)
              for e in ends if "2020-06-30" <= e.isoformat() <= months[-1]]
    return HistoryInputs(ticker="AAA", sector_class=sector, months=[(d, close) for d in months],
                         last_bar=months[-1], points=points, current_price=close)


def test_steady_multiples_give_a_fair_value_at_the_price():
    result = compute_history(_inputs())
    assert result.na_reason is None
    latest = result.rows[-1]
    assert latest.market_cap == pytest.approx(2000.0)  # 20 x 100 shares
    assert latest.multiples["pe"] == pytest.approx(20.0)
    ranges = {r.metric: r for r in result.ranges}
    assert ranges["pe"].applicable and ranges["pe"].median == pytest.approx(20.0)
    assert ranges["pe"].n_months == 60
    fv = ranges["fair_value"]
    assert fv.current == pytest.approx(20.0) and fv.n_metrics == 5
    assert historical_value_label(20.0, fv.current) == "near"


def test_a_metric_needs_36_months_and_the_fair_value_two_metrics():
    short = {r.metric: r for r in compute_history(_inputs(n_months=35)).ranges}
    assert not short["pe"].applicable and "35 months" in short["pe"].na_reason
    assert short["pe"].current == pytest.approx(20.0)  # the multiple itself is still shown
    assert short["fair_value"].current is None
    enough = {r.metric: r for r in compute_history(_inputs(n_months=36)).ranges}
    assert enough["pe"].applicable
    # A bank with only revenue to go on: one combined metric is not enough.
    bank_result = compute_history(_inputs(sector="bank", net_income=None, common_equity=None))
    bank = {r.metric: r for r in bank_result.ranges}
    assert bank["ps"].applicable and bank["fair_value"].current is None
    assert "1 metric" in bank["fair_value"].na_reason
    assert "banks" in bank["pfcf"].na_reason
    # The sector rules' suppression reaches the stored months too.
    assert all(r.multiples["pfcf"] is None and r.multiples["ev_ebitda"] is None for r in bank_result.rows)


def test_the_fair_value_line_never_looks_ahead():
    base = compute_history(_inputs(n_months=96))
    moved_inputs = _inputs(n_months=96)
    # From the 41st month on the price doubles: more than half of all the months, so a median over the
    # whole series would move the earlier values too.
    moved_inputs.months = [(d, 40.0 if i >= 40 else c) for i, (d, c) in enumerate(moved_inputs.months)]
    moved = compute_history(moved_inputs)
    before = [r.fair_value for r in base.rows[:40]]
    assert [r.fair_value for r in moved.rows[:40]] == before
    assert sum(v is not None for v in before) == 5  # the line starts at its 36th month
    assert moved.rows[-1].multiples["pe"] == pytest.approx(40.0)


def test_a_rising_price_puts_it_above_the_fair_value():
    x = _inputs()
    x.months = [(d, 20.0 * (1.0 + 0.01 * i)) for i, (d, _) in enumerate(x.months)]
    result = compute_history(x)
    fv = next(r for r in result.ranges if r.metric == "fair_value")
    assert fv.current is not None and x.months[-1][1] > fv.current  # the multiple's median lags the price
    pe = next(r for r in result.ranges if r.metric == "pe")
    assert pe.pct_rank == 1.0  # the dearest month in the window


def test_a_spin_off_factor_restores_the_traded_price():
    x = _inputs()
    x.price_factors = [("2023-06-15", 1.281)]
    result = compute_history(x)
    by_day = {r.date: r for r in result.rows}
    assert by_day["2023-05-31"].market_cap == pytest.approx(2000.0 * 1.281)  # Yahoo divided that close by 1.281
    assert by_day["2023-06-30"].market_cap == pytest.approx(2000.0)


def test_stale_fundamentals_give_no_current_multiples():
    x = _inputs()
    x.points = [p for p in x.points if p.period_end <= "2024-12-31"]  # nothing known after 2025-02-14
    ranges = {r.metric: r for r in compute_history(x).ranges}
    assert ranges["pe"].current is None and "days old" in ranges["pe"].na_reason


def test_dividend_yield_counts_the_trailing_365_days():
    x = _inputs()
    x.dividends = [("2025-03-01", 0.2), ("2025-09-01", 0.2), ("2024-12-01", 0.2)]
    row = next(r for r in compute_history(x).rows if r.date == "2025-09-30")
    assert row.multiples["div_yield"] == pytest.approx(0.6 / 20.0)
    row = next(r for r in compute_history(x).rows if r.date == "2025-12-31")
    assert row.multiples["div_yield"] == pytest.approx(0.4 / 20.0)  # 2024-12-01 is 395 days back


def test_a_fair_value_row_is_on_the_quoted_price_basis():
    x = _inputs()
    x.bars_to_price = 2.0  # a split between the latest bar and the quote
    fv = next(r for r in compute_history(x).ranges if r.metric == "fair_value")
    assert fv.current == pytest.approx(40.0)


def test_inputs_without_a_history_give_reasons_everywhere():
    result = compute_history(HistoryInputs(ticker="AAA", na_reason="no company record"))
    assert result.na_reason == "no company record" and result.rows == []
    assert {r.metric for r in result.ranges} == {"pe", "ps", "pb", "pfcf", "ev_ebitda", "pffo", "div_yield",
                                                 "fair_value"}
    assert all(not r.applicable and r.na_reason == "no company record" for r in result.ranges)


def test_a_window_that_ends_on_feb_29():
    x = _inputs(n_months=12 * 11 + 2)  # to 2032-02-29
    assert x.months[-1][0] == "2032-02-29"
    pe = next(r for r in compute_history(x).ranges if r.metric == "pe")
    assert pe.window_start == "2022-03-31"  # the first month after 2022-02-28, ten years back
    assert pe.n_months == 120
