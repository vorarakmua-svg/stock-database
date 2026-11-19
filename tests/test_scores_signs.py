"""Quality scores (Milestone 2): the warning signs. A healthy company fires none; each sign fires on its own rule.
docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
import pytest

from src.scores.fundamentals import AnnualRow, Fundamentals
from src.scores.scores import Scores
from src.scores.signs import SIGN_CODES, HistoryContext, warning_signs


def _company(years=6, **overrides):
    """A healthy company over *years* fiscal years: revenue +5% a year and the rest in step with it. An
    override is a function of (year index, revenue) giving that figure, or a constant."""
    rows = []
    for i in range(years):
        base = 1000.0 * 1.05 ** i
        rule = overrides.get("revenue")
        revenue = rule(i, base) if callable(rule) else base
        figures = dict(revenue=revenue, operating_margin=0.20, gross_profit=0.40 * revenue,
                       free_cash_flow=0.10 * revenue, total_debt=0.30 * revenue, accounts_receivable=0.10 * revenue,
                       inventory=0.08 * revenue, total_assets=2.0 * revenue, goodwill=50.0, total_equity=800.0,
                       dividends_paid=0.03 * revenue, interest_coverage=10.0)
        for key, value in overrides.items():
            if key != "revenue":
                figures[key] = value(i, revenue) if callable(value) else value
        rows.append(AnnualRow(fiscal_year=2020 + i, period_end=f"{2020 + i}-12-31", shares=100.0, eps=1.0,
                              figures=figures))
    return Fundamentals(ticker="AAA", rows=rows)


def _scores(**kw):
    base = dict(piotroski_f=7, piotroski_tested=9, altman_z=4.0, altman_zone="safe", beneish_m=-2.5,
                sloan_ratio=0.02, roic=0.15, wacc=0.09)
    base.update(kw)
    return Scores(**base)


CALM = HistoryContext(price_to_value=1.0, pe_pct_rank=0.5)


def _codes(f=None, s=None, h=CALM):
    return {sign.code for sign in warning_signs(f or _company(), s or _scores(), h)}


def test_a_healthy_company_fires_nothing():
    assert _codes() == set()
    assert len(SIGN_CODES) == 20


@pytest.mark.parametrize("scores,code,severity", [
    (dict(altman_z=1.5, altman_zone="distress"), "altman_distress", "severe"),
    (dict(beneish_m=-1.5), "beneish_flag", "severe"),
    (dict(sloan_ratio=0.30), "sloan_high", "severe"),
    (dict(sloan_ratio=-0.15), "sloan_high", "medium"),
    (dict(piotroski_f=3, piotroski_tested=8), "piotroski_low", "medium"),
    (dict(wacc=0.20), "roic_below_wacc", "medium"),
])
def test_the_score_signs(scores, code, severity):
    signs = warning_signs(_company(), _scores(**scores), CALM)
    assert [(s.code, s.severity) for s in signs] == [(code, severity)]
    assert signs[0].message and signs[0].value is not None


def test_a_low_f_needs_seven_tests():
    assert _codes(s=_scores(piotroski_f=3, piotroski_tested=6)) == set()


@pytest.mark.parametrize("overrides,code", [
    (dict(revenue=lambda i, r: 1000.0 * 0.95 ** i), "revenue_decline"),
    (dict(operating_margin=lambda i, r: 0.20 - 0.02 * i), "margin_decline"),
    (dict(free_cash_flow=lambda i, r: -10.0 if i in (3, 4) else 0.1 * r), "fcf_negative"),
    (dict(total_debt=lambda i, r: 300.0 * 1.25 ** i), "debt_outgrows_revenue"),
    (dict(accounts_receivable=lambda i, r: 0.10 * r * (1.3 if i == 5 else 1.0)), "receivables_outgrow_revenue"),
    (dict(inventory=lambda i, r: 0.08 * r * (1.3 if i == 5 else 1.0)), "inventory_outgrows_revenue"),
    (dict(total_assets=lambda i, r: 2000.0 * 1.2 ** i), "assets_outgrow_revenue"),
    (dict(goodwill=500.0), "goodwill_heavy"),
    (dict(dividends_paid=lambda i, r: 0.2 * r), "payout_over_fcf"),
    (dict(gross_profit=lambda i, r: (0.40 - 0.015 * i) * r), "gross_margin_decline"),
])
def test_the_trend_signs(overrides, code):
    assert _codes(f=_company(**overrides)) == {code}


def test_negative_equity_is_a_sign_and_not_goodwill_heavy():
    assert _codes(f=_company(total_equity=-100.0)) == {"negative_equity"}


def test_interest_coverage_by_severity():
    signs = warning_signs(_company(interest_coverage=1.2), _scores(), CALM)
    assert [(s.code, s.severity) for s in signs] == [("interest_coverage_low", "severe")]
    signs = warning_signs(_company(interest_coverage=2.5), _scores(), CALM)
    assert [(s.code, s.severity) for s in signs] == [("interest_coverage_low", "medium")]


def test_dilution_reads_the_split_normalized_count():
    f = _company()
    for i, row in enumerate(f.rows):
        row.shares = 100.0 * 1.05 ** i
    assert _codes(f=f) == {"dilution"}


def test_the_history_signs():
    assert _codes(h=HistoryContext(price_to_value=1.4, pe_pct_rank=0.5)) == {"price_far_above_value"}
    assert _codes(h=HistoryContext(price_to_value=1.0, pe_pct_rank=0.96)) == {"pe_at_high"}
    assert _codes(h=HistoryContext(price_to_value=None, pe_pct_rank=None)) == set()


def test_severe_signs_come_first():
    signs = warning_signs(_company(goodwill=500.0), _scores(beneish_m=-1.0), CALM)
    assert [s.code for s in signs] == ["beneish_flag", "goodwill_heavy"]


def test_too_short_a_record_fires_no_trend_sign():
    f = _company(years=3, revenue=lambda i, r: 1000.0 * 0.9 ** i)
    assert "revenue_decline" not in _codes(f=f)  # three years of declines need four years of figures


def test_the_gross_margin_sign_falls_back_on_revenue_less_cost_of_revenue():
    # BUG-199: a filer that tags cost of revenue but no gross profit; the margin falls 1.5 points a year.
    f = _company(gross_profit=None, cost_of_revenue=lambda i, r: (0.60 + 0.015 * i) * r)
    assert "gross_margin_decline" in _codes(f)


# ---------- the years back are fiscal years (BUG-198) ----------

def _gap(f, missing):
    """*f* without fiscal year *missing*: the rows after it keep their own years."""
    f.rows = [r for r in f.rows if r.fiscal_year != missing]
    return f


def test_three_years_of_declines_need_the_fiscal_years_t_minus_3_to_t():
    # Revenue falls every row, but FY2022 isn't stored: FY2020 -> FY2021 -> FY2023 -> FY2024 is three falls over
    # four years, one of them unknown.
    f = _gap(_company(years=5, revenue=lambda i, r: 1000.0 * 0.95 ** i), 2022)
    assert [r.fiscal_year for r in f.rows] == [2020, 2021, 2023, 2024]
    assert "revenue_decline" not in _codes(f=f)
    assert "revenue_decline" in _codes(f=_company(years=4, revenue=lambda i, r: 1000.0 * 0.95 ** i))


def test_five_years_back_is_fiscal_year_t_minus_5():
    # The operating and gross margins were 10 points higher in FY2020 only. FY2023 isn't stored, so the sixth row
    # from the end is FY2020, six years before FY2026; FY2021, five back, is level with it.
    f = _gap(_company(years=7, operating_margin=lambda i, r: 0.30 if i == 0 else 0.20,
                      gross_profit=lambda i, r: (0.50 if i == 0 else 0.40) * r), 2023)
    assert [r.fiscal_year for r in f.rows] == [2020, 2021, 2022, 2024, 2025, 2026]
    codes = _codes(f=f)
    assert "margin_decline" not in codes and "gross_margin_decline" not in codes
    # Five years back stored: FY2020 against FY2025 fires both.
    codes = _codes(f=_company(years=6, operating_margin=lambda i, r: 0.30 if i == 0 else 0.20,
                              gross_profit=lambda i, r: (0.50 if i == 0 else 0.40) * r))
    assert {"margin_decline", "gross_margin_decline"} <= codes


def test_the_last_three_years_of_cash_flow_are_fiscal_years_t_minus_2_to_t():
    # Negative free cash flow in FY2021 and FY2024; FY2022 isn't stored, so the last three rows reach back four years.
    f = _gap(_company(years=5, free_cash_flow=lambda i, r: -10.0 if i in (1, 4) else 0.1 * r), 2022)
    assert [r.fiscal_year for r in f.rows][-3:] == [2021, 2023, 2024]
    assert "fcf_negative" not in _codes(f=f)


def test_receivables_three_years_back_are_fiscal_year_t_minus_3():
    # Receivables per dollar of revenue 30% higher from FY2021 on than in FY2020. FY2022 isn't stored, so three rows
    # back is FY2020, four years back; FY2021, three back, is level with FY2024.
    f = _gap(_company(years=5, accounts_receivable=lambda i, r: 0.10 * r * (1.0 if i == 0 else 1.3)), 2022)
    assert "receivables_outgrow_revenue" not in _codes(f=f)
    # FY2020 stored three years back: the same rise fires.
    f = _company(years=4, accounts_receivable=lambda i, r: 0.10 * r * (1.0 if i == 0 else 1.3))
    assert "receivables_outgrow_revenue" in _codes(f=f)
