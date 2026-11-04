"""DDM's special and variable dividends (Q3) and its N/A where Yahoo's dividend history is incomplete (Q4)."""
from datetime import date

import pytest

from src.valuation import models
from src.valuation.inputs import FYRecord, ValuationInputs
from src.valuation.models import value_ddm


def _inputs(**kwargs):
    defaults = dict(ticker="AAA", sector_class="insurance", fy_records=[], shares_outstanding=100.0, beta=1.0,
                    risk_free_rate=0.045, dividends=[], as_of=date(2026, 1, 1))
    defaults.update(kwargs)
    return ValuationInputs(**defaults)


def _quarterly(first, last, amount, growth=0.0, months=("03", "06", "09", "12")):
    """Quarterly payments on the 15th, *amount* each in *first*, growing by *growth* a year."""
    return [(f"{year}-{month}-15", amount * (1 + growth) ** (year - first))
            for year in range(first, last + 1) for month in months]


def _with(divs, *extra):
    """*divs* with each (day, amount) in *extra* added, or replacing the payment that day."""
    days = dict(divs)
    days.update(extra)
    return sorted(days.items())


def _sec_years(dps_by_year, year_end="12-31"):
    records = []
    for fy, dps in sorted(dps_by_year.items()):
        rec = FYRecord(fiscal_year=fy, period_end=f"{fy}-{year_end}", net_income=None, total_equity=None,
                       eps_diluted=None, shares=100.0, fcf=None, ffo_per_share=None)
        rec.dividends_per_share = dps
        records.append(rec)
    return records


# ---- Q3: one-off payments leave the TTM ----

def test_a_special_between_regular_payments_leaves_the_ttm():
    # COST's $15 of 2023-12-27 between $1.02 quarterly payments; PSA's $13.15 of 2022-07-29.
    divs = _with(_quarterly(2019, 2025, 0.25), ("2025-08-01", 3.0))
    res = value_ddm(_inputs(dividends=divs))
    assert res.assumptions["ttm_dps"] == pytest.approx(1.0)
    assert res.assumptions["ttm_one_offs"] == [["2025-08-01", 3.0]]


def test_a_special_served_with_a_quarters_payment_leaves_only_its_excess():
    # HST's $0.92 of 2026-06-30 holds its $0.20 quarterly payment: Yahoo served them as one amount.
    divs = _with(_quarterly(2019, 2025, 0.25), ("2025-09-15", 3.25))
    res = value_ddm(_inputs(dividends=divs))
    assert res.assumptions["ttm_dps"] == pytest.approx(1.0)
    assert res.assumptions["ttm_one_offs"] == [["2025-09-15", pytest.approx(3.0)]]


def test_a_variable_dividend_paid_every_year_stays():
    # PGR: $0.10 a quarter, and each January an annual variable dividend ($13.60 in 2026).
    divs = _quarterly(2018, 2026, 0.10, months=("04", "07", "10"))
    divs += [(f"{year}-01-05", 2.0 + year - 2018) for year in range(2018, 2026)] + [("2026-01-05", 13.6)]
    res = value_ddm(_inputs(dividends=sorted(divs), as_of=date(2026, 8, 1)))
    assert res.assumptions["ttm_dps"] == pytest.approx(13.6 + 0.3)
    assert "ttm_one_offs" not in res.assumptions


def test_a_variable_dividend_a_quarter_late_still_recurs():
    # CME's annual variable dividend each late December, until 2025's came on 2026-03-10 (73 days on) in place of
    # that quarter's regular payment.
    divs = _quarterly(2018, 2026, 1.0)
    divs = [(day, amount) for day, amount in divs if day != "2026-03-15"]
    divs += [(f"{year}-12-27", 4.0) for year in range(2018, 2025)] + [("2026-03-10", 7.0)]
    res = value_ddm(_inputs(dividends=sorted(divs), as_of=date(2026, 10, 1)))
    assert res.assumptions["ttm_dps"] == pytest.approx(7.0 + 3 * 1.0)
    assert "ttm_one_offs" not in res.assumptions


def test_a_raise_isnt_a_one_off():
    # NVDA: $0.25 a quarter from June 2026 after years of $0.01. The payments after it are at its level.
    divs = _quarterly(2019, 2025, 0.01) + [("2026-03-15", 0.01), ("2026-06-15", 0.25), ("2026-09-15", 0.25)]
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 10, 25)))
    assert res.assumptions["ttm_dps"] == pytest.approx(0.01 + 0.01 + 0.25 + 0.25)
    assert "ttm_one_offs" not in res.assumptions


def test_the_latest_payment_is_never_a_one_off():
    # Nothing after it shows whether it is a special or a raise.
    divs = _with(_quarterly(2019, 2025, 0.25), ("2025-12-15", 3.0))
    assert models.one_off_dividends(divs) == []


def test_a_special_two_or_three_years_apart_is_a_one_off():
    # COST's specials of 2012, 2015, 2017, 2020 and 2023: one in the 3 years before each at most.
    divs = _with(_quarterly(2016, 2025, 1.0), ("2020-12-01", 10.0), ("2023-12-27", 15.0))
    assert models.one_off_dividends(divs) == [("2020-12-01", 10.0), ("2023-12-27", 15.0)]


def test_a_payment_up_to_three_times_the_regular_ones_is_regular():
    divs = _with(_quarterly(2019, 2025, 1.0), ("2025-08-01", 3.0))
    assert models.one_off_dividends(divs) == []


# ---- Q3: one-off payments leave the CAGR's endpoints ----

def test_a_special_in_secs_first_year_leaves_the_cagr():
    # COST's FY2017 declared $8.90, its $7 special with its regular $1.90.
    divs = _with(_quarterly(2015, 2026, 0.50, growth=0.05)[:-2], ("2016-08-01", 2.0))
    dps = {year: 2.0 * 1.05 ** (year - 2015) for year in range(2016, 2026)}
    dps[2016] += 2.0
    res = value_ddm(_inputs(fy_records=_sec_years(dps), dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)
    assert res.assumptions["growth_one_offs"] == [[2016, pytest.approx(2.0)]]
    assert res.assumptions["dividend_growth_years"][0] == [2016, pytest.approx(2.0 * 1.05), "sec"]


def test_a_special_in_secs_last_year_leaves_the_cagr():
    divs = _with(_quarterly(2015, 2026, 0.50, growth=0.05)[:-2], ("2025-08-01", 4.0))
    dps = {year: 2.0 * 1.05 ** (year - 2015) for year in range(2016, 2026)}
    dps[2025] += 4.0
    res = value_ddm(_inputs(fy_records=_sec_years(dps), dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)


def test_secs_figure_without_the_special_stays():
    # LDOS's FY2016 declared $1.28, leaving its $13.64 special out.
    divs = _with(_quarterly(2015, 2026, 0.50, growth=0.05)[:-2], ("2016-08-01", 2.0))
    dps = {year: 2.0 * 1.05 ** (year - 2015) for year in range(2016, 2026)}
    res = value_ddm(_inputs(fy_records=_sec_years(dps), dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)
    assert "growth_one_offs" not in res.assumptions


def test_a_special_in_yahoos_first_year_leaves_the_cagr():
    # A company without SEC fiscal years measures over Yahoo's calendar years.
    divs = _with(_quarterly(2015, 2026, 0.50, growth=0.05)[:-2], ("2016-08-01", 2.0))
    res = value_ddm(_inputs(dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["hist_cagr"] == pytest.approx(0.05)


def test_a_special_inside_the_window_changes_nothing():
    # PSA's 2022 special sits mid-window: the CAGR reads only the endpoints.
    divs = _with(_quarterly(2015, 2026, 0.50, growth=0.05)[:-2], ("2020-08-01", 2.0))
    dps = {year: 2.0 * 1.05 ** (year - 2015) for year in range(2016, 2026)}
    dps[2020] += 2.0
    res = value_ddm(_inputs(fy_records=_sec_years(dps), dividends=divs, as_of=date(2026, 8, 4)))
    assert res.assumptions["dividend_growth_years"][4] == [2020, pytest.approx(dps[2020]), "sec"]
    assert "growth_one_offs" not in res.assumptions


# ---- Q4: DDM is N/A where the completeness check finds Yahoo missing payments it reads ----

def test_ddm_is_na_where_yahoos_missing_payments_fall_in_the_ttm():
    # CB: no payment between 2025-06-13 and 2026-03-13; the TTM held 2 of 4.
    divs = [(day, amount) for day, amount in _quarterly(2016, 2026, 0.9)[:-2]
            if day not in ("2025-09-15", "2025-12-15")]
    dps = {year: 3.6 for year in range(2016, 2026)}
    res = value_ddm(_inputs(fy_records=_sec_years(dps), dividends=divs, as_of=date(2026, 8, 4),
                            dividend_gaps=[(2025, "2025-06-15", "2026-03-15")]))
    assert res.applicable is False
    assert res.na_reason == "dividend history incomplete (Yahoo is missing payments in FY2025)"


def test_a_gap_from_a_year_before_running_into_the_ttm_makes_ddm_na():
    # JCI: FY2025 ended in September, before the TTM's start, and its gap runs to 2026-03-16, inside the TTM.
    divs = [(day, amount) for day, amount in _quarterly(2016, 2026, 0.9)
            if day not in ("2025-09-15", "2025-12-15")]
    dps = {year: 3.6 for year in range(2016, 2026)}
    res = value_ddm(_inputs(fy_records=_sec_years(dps, year_end="09-30"), dividends=divs, as_of=date(2026, 10, 25),
                            dividend_gaps=[(2025, "2025-06-15", "2026-03-15")]))
    assert res.applicable is False


def test_an_older_flagged_year_with_secs_figure_leaves_ddm_alone():
    # Its growth year is SEC's figure, and the TTM has every payment.
    divs = [(day, amount) for day, amount in _quarterly(2016, 2026, 0.9)[:-2]
            if day not in ("2022-09-15", "2022-12-15")]
    dps = {year: 3.6 for year in range(2016, 2026)}
    res = value_ddm(_inputs(fy_records=_sec_years(dps), dividends=divs, as_of=date(2026, 8, 4),
                            dividend_gaps=[(2022, "2022-06-15", "2023-03-15")]))
    assert res.applicable is True
    assert res.assumptions["ttm_dps"] == pytest.approx(3.6)


def test_a_flagged_growth_year_from_yahoo_makes_ddm_na():
    # ACN's FY2026 has no SEC figure, so its growth year is Yahoo's payments, two of them missing.
    divs = [(day, amount) for day, amount in _quarterly(2016, 2026, 0.9)[:-2]
            if day not in ("2022-09-15", "2022-12-15")]
    dps = {year: 3.6 for year in range(2016, 2026) if year != 2022}
    res = value_ddm(_inputs(fy_records=_sec_years({**dps, 2022: None}), dividends=divs, as_of=date(2026, 8, 4),
                            dividend_gaps=[(2022, "2022-06-15", "2023-03-15")]))
    assert res.applicable is False
    assert res.na_reason == "dividend history incomplete (Yahoo is missing payments in FY2022)"
