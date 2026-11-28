"""The valuation oracle's unit-seam rule (BUG-308, Q19): ``qa/oracle/valuation_oracle.py`` drops the years before a
share-count seam beyond 50x that neither a stored split nor the count EPS implies explains, as
``inputs._normalize_splits`` documents it (the spec is silent), so its DCF growth window starts where the code's does.
And its DDM one-payment gate counts the regular payments beside a year only (BUG-366), as ``models.dividend_history``
does.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("qa_valuation_oracle", ROOT / "qa" / "oracle" / "valuation_oracle.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


oracle = _load()


def _row(fy, diluted=None, net_income=None, eps=None, period_end_count=None):
    """A financials_annual row as the oracle reads it: fy, period_end, net income, equity, period-end count,
    diluted weighted count, diluted EPS."""
    return (fy, f"{fy}-12-31", net_income, None, period_end_count, diluted, eps)


def test_a_seam_beyond_50x_is_a_unit_break_and_one_within_it_is_not():
    assert oracle.unit_seam([_row(2016, 900e6), _row(2017, 906e6), _row(2018, 4.5e6)]) == 2017  # 1:201
    assert oracle.unit_seam([_row(2018, 40_586.0), _row(2019, 40_420.0), _row(2020, 40e6)]) == 2019  # thousands
    assert oracle.unit_seam([_row(2017, 100e6), _row(2018, 100e6 / 49)]) is None  # a 1:49 reverse split folds
    assert oracle.unit_seam([_row(2017, 100e6), _row(2018, 100e6 * 49)]) is None
    assert oracle.unit_seam([_row(2017, 100e6), _row(2018, 100e6 * 51)]) == 2017
    assert oracle.unit_seam([]) is None


def test_the_newest_seam_cuts_and_a_year_without_a_count_is_not_bridged():
    rows = [_row(2014, 1e9), _row(2015, 1e6), _row(2016, 1e6), _row(2017, 1e9)]
    assert oracle.unit_seam(rows) == 2016
    assert oracle.unit_seam([_row(2016, 1e9), _row(2017), _row(2018, 1e6)]) is None


def test_the_count_is_the_one_eps_divides_by():
    # No diluted count: net income / EPS, else the period-end count.
    assert oracle.unit_seam([_row(2017, net_income=500e6, eps=0.5), _row(2018, 5e6)]) == 2017  # 1e9 -> 5e6
    assert oracle.unit_seam([_row(2017, period_end_count=1e9), _row(2018, 5e6)]) == 2017


def _db(fcf, counts, eps_moves=False, to_common=None, splits=()):
    """EXE's FCF and diluted counts. Its EPS is unknown (no count implied), or with *eps_moves* net income (1e9, or
    *to_common*'s figure) over the count, the EPS a split restates with it; *splits* are stored."""
    db = sqlite3.connect(":memory:")
    db.executescript(
        "create table market_snapshots (ticker, beta, risk_free_rate, shares_outstanding, current_price, market_cap,"
        " collected_at);"
        "create table analyst_snapshots (ticker, earnings_growth, collected_at);"
        "create table split_events (ticker, date, ratio);"
        "create table financials_annual (ticker, fiscal_year, period_end, net_income, total_equity,"
        " shares_outstanding, weighted_avg_shares_diluted, eps_diluted, filed_date, form, net_income_to_common);"
        "create table metrics_annual (ticker, fiscal_year, free_cash_flow);")
    db.execute("insert into market_snapshots values ('EXE', 1.0, 0.04, 240e6, 100.0, 24e9, '2026-10-20T00:00:00')")
    for fy, count in counts.items():
        common = (to_common or {}).get(fy)
        eps = ((1e9 if common is None else common) / count) if eps_moves else None
        db.execute("insert into financials_annual values ('EXE', ?, ?, 1e9, 1e10, null, ?, ?, ?, '10-K', ?)",
                   (fy, f"{fy}-12-31", count, eps, f"{fy + 1}-02-20", common))
    for day, ratio in splits:
        db.execute("insert into split_events values ('EXE', ?, ?)", (day, ratio))
    for fy, value in fcf.items():
        db.execute("insert into metrics_annual values ('EXE', ?, ?)", (fy, value))
    return db


def test_the_dcf_growth_window_starts_after_the_seam():
    # EXE: FY2016's loss, FY2017's 475M, then a 1:200 seam into FY2018; the code's FCF history starts at FY2018.
    fcf = {2016: -1650e6, 2017: 475e6, 2018: 1730e6, 2019: 1623e6, 2020: 22e6, 2022: 2302e6, 2023: 551e6,
           2024: 8e6, 2025: 1839e6}
    counts = {2016: 764e6, 2017: 906e6, 2018: 4.546e6, 2019: 8.3e6, 2020: 9.8e6, 2022: 146e6, 2023: 143e6,
              2024: 157e6, 2025: 240e6}
    db = _db(fcf, counts)
    assert [row[0] for row in oracle.Inputs(db, "EXE").annual][0] == 2018  # every model's years, as the code's
    o = oracle.level2(db, "EXE", "dcf", {})
    assert o["inputs"]["hist_cagr"] == pytest.approx((1839 / 1730) ** (1 / 7) - 1)
    assert o["inputs"]["cagr_years"] == 7
    assert o["inputs"]["fcf_basis"] == pytest.approx(551e6)  # the median of FY2023-25
    # Without the seam the window reaches back to FY2017's 475M (from its first positive year).
    counts_smooth = {**counts, 2016: 4.5e6, 2017: 4.5e6}
    o = oracle.level2(_db(fcf, counts_smooth), "EXE", "dcf", {})
    assert o["inputs"]["hist_cagr"] == pytest.approx((1839 / 475) ** (1 / 8) - 1)


def test_a_seam_a_split_explains_folds_and_the_window_reaches_back():
    # Q19: EXE's 1:200 is a reverse split; with the EPS restated with the count, the seam folds, the years stay and
    # the DCF's window starts at FY2017's 475M (its first positive year), as the code's records do.
    fcf = {2016: -1650e6, 2017: 475e6, 2018: 1730e6, 2019: 1623e6, 2020: 22e6, 2022: 2302e6, 2023: 551e6,
           2024: 8e6, 2025: 1839e6}
    counts = {2016: 764e6, 2017: 906e6, 2018: 4.546e6, 2019: 8.3e6, 2020: 9.8e6, 2022: 146e6, 2023: 143e6,
              2024: 157e6, 2025: 240e6}
    for db in (_db(fcf, counts, eps_moves=True), _db(fcf, counts, splits=[("2020-04-14", 0.005)])):
        x = oracle.Inputs(db, "EXE")
        assert x.truncated_at is None
        assert [row[0] for row in x.annual][0] == 2016
        o = oracle.level2(db, "EXE", "dcf", {})
        assert o["inputs"]["hist_cagr"] == pytest.approx((1839 / 475) ** (1 / 8) - 1)
    # A stored split dated before the older year's end, or far from the seam, explains nothing.
    for splits in ([("2017-06-01", 0.005)], [("2020-04-14", 0.5)]):
        assert oracle.Inputs(_db(fcf, counts, splits=splits), "EXE").truncated_at == 2017


def test_the_implied_count_reads_net_income_to_common_where_a_year_has_it():
    rows = [(2017, "2017-12-31", -505e6, None, None, 906e6, -0.70), (2018, "2018-12-31", 226e6, None, None, 4.546e6,
                                                                    29.26)]
    assert oracle.unit_seam(rows) == 2017  # net income before preferred dividends implies 1:93
    assert oracle.unit_seam(rows, to_common={2017: -631e6, 2018: 133e6}) is None  # to common: 1:200
    assert oracle.unit_seam(rows, to_common={2017: -631e6}) == 2017  # FY2018's net income stands in
    seam = 4.546e6 / 906e6
    assert oracle.seam_explained(seam, rows[0], rows[1], to_common={2017: -631e6, 2018: 133e6 * 1.24})
    assert not oracle.seam_explained(seam, rows[0], rows[1], to_common={2017: -631e6, 2018: 133e6 * 1.3})
    assert oracle.seam_explained(seam, rows[0], rows[1], splits=[("2019-06-01", 0.1), ("2020-04-14", 0.05)])  # a run
    assert not oracle.seam_explained(seam, (2017, "2017-12-31", None, None, None, 906e6, None), rows[1])


def test_a_seam_between_two_counts_eps_implies_is_not_explained_by_them():
    # BUG-377: neither year has a diluted weighted count, so each year's count is net income / EPS, and the count EPS
    # implies is the seam itself: nothing independent explains it (an EPS filed 1,000x small one year).
    rows = [(2017, "2017-12-31", 1.0e9, None, None, None, 1.00), (2018, "2018-12-31", 1.1e9, None, None, None, 0.0011)]
    assert oracle.unit_seam(rows) == 2017
    # With a diluted count in either year the implied count checks it (EXE's fold).
    rows[1] = (2018, "2018-12-31", 1.1e9, None, None, 1.0e12, 0.0011)
    assert oracle.unit_seam(rows) is None


def test_only_the_years_a_valuation_reads_are_searched_for_a_seam():
    # The valuation reads the latest 10 fiscal years: a unit seam older than them (FY2013-14) cuts nothing.
    counts = {fy: (1e6 if fy <= 2013 else 1e9) for fy in range(2012, 2026)}
    assert [row[0] for row in oracle.Inputs(_db({}, counts), "EXE").annual][0] == 2012
    counts[2016], counts[2017] = 1e6, 1e6  # one inside them (FY2017-18) cuts there
    assert [row[0] for row in oracle.Inputs(_db({}, counts), "EXE").annual][0] == 2018


# ---- BUG-366: the oracle's one-payment gate counts regular payments only, as dividend_history does ----

def _annual(extra=()):
    """An annual payer's ex-dates (each 15 January, 2.95 in 2024, 3.10 in 2025), its 2026 payment slipped to
    2025-12-30, and *extra* payments."""
    return sorted([("2023-01-15", 2.81), ("2024-01-15", 2.95), ("2025-01-15", 3.10), ("2025-12-30", 3.26),
                   *extra])


@pytest.mark.parametrize("extra, counted", [
    ((), 1),
    ((("2024-07-15", 0.5),), 1),    # under half the median of 2024's payments (1.725)
    ((("2024-07-15", 1.2),), 2),    # half of it or more: a payment of a payer of two a year
    ((("2024-07-15", 10.0),), 1),   # a one-off whole (over 3x the payments either side)
    ((("2024-07-15", 10.0), ("2024-09-15", 0.5)), 1),   # the one-off isn't measured against either
    # BUG-372: under half the median, a payment counts when a like one (within 3x) recurs within a quarter of its
    # anniversary: a sixth of 2.95 within its season, a stray a fifth of it a year on, the same size 460 or 270 days
    # away don't; the same size a year on does
    ((("2024-11-20", 0.5),), 1),
    ((("2024-07-15", 0.5), ("2025-07-10", 0.1)), 1),
    ((("2023-04-12", 0.5), ("2024-07-15", 0.5)), 1),
    ((("2024-07-15", 0.5), ("2025-04-11", 0.5)), 1),
    ((("2024-07-15", 0.5), ("2025-07-10", 0.5)), 2),
    # BUG-376: ... into the year the gate judges (2025 here): the same size a year before, or two years running
    # before it, is an annual payer's recurring extra, not its second payment a year
    ((("2023-07-20", 0.5), ("2024-07-15", 0.5)), 1),
    ((("2022-07-15", 0.5), ("2023-07-20", 0.5), ("2024-07-15", 0.5)), 1),
    ((("2023-07-20", 0.5), ("2024-07-15", 0.5), ("2025-07-10", 0.5)), 2),
])
def test_the_oracles_gate_counts_the_regular_payments_beside_a_year(extra, counted):
    events = _annual(extra)
    assert oracle.regular_beside(events, oracle.one_offs(events), "2024-12-31", "2025-12-31") == counted


def test_the_oracles_gate_counts_a_semiannual_interim_and_quarters_beside_specials():
    semiannual = [(f"{y}-{m}", a) for y in (2023, 2024, 2025) for m, a in (("03-15", 0.4), ("09-15", 1.0))]
    assert oracle.regular_beside(semiannual, oracle.one_offs(semiannual), "2024-12-31", "2025-12-31") == 2
    for interim in (0.3, 0.1):  # BUG-372: an interim under a third of the final recurs, so it counts
        small = [(f"{y}-{m}", a) for y in (2023, 2024, 2025) for m, a in (("03-15", interim), ("09-15", 1.0))]
        assert oracle.regular_beside(small, oracle.one_offs(small), "2024-12-31", "2025-12-31") == 2
    # BUG-376: a payer turned semiannual in 2025: the year after shows its interim, recurring into 2025 a year earlier
    turned = [("2023-09-15", 1.2), ("2024-09-15", 1.2), ("2025-03-15", 0.3), ("2025-09-15", 1.0), ("2026-03-15", 0.3),
              ("2026-09-15", 1.0)]
    assert oracle.regular_beside(turned, oracle.one_offs(turned), "2024-12-31", "2025-12-31") == 2
    tpl =[("2019-03-07", 0.666667), ("2020-03-06", 1.777778), ("2020-12-10", 1.111111), ("2021-03-05", 0.305556),
           ("2021-06-07", 0.305556), ("2021-09-07", 0.305556), ("2021-12-07", 0.305556)]
    assert oracle.regular_beside(tpl, oracle.one_offs(tpl), "2019-12-31", "2020-12-31") == 4


def _dividend_db(extra, more=()):
    """AAA, an annual payer growing 5% (SEC's dividends per share each FY), its 2026 payment ex-dated 2025-12-30, an
    *extra* payment on 2024-07-15, and *more* (day, amount) payments."""
    db = sqlite3.connect(":memory:")
    db.executescript(
        "create table market_snapshots (ticker, beta, risk_free_rate, shares_outstanding, current_price, market_cap,"
        " collected_at);"
        "create table analyst_snapshots (ticker, earnings_growth, collected_at);"
        "create table split_events (ticker, date, ratio);"
        "create table financials_annual (ticker, fiscal_year, period_end, net_income, total_equity,"
        " shares_outstanding, weighted_avg_shares_diluted, eps_diluted, filed_date, form, dividends_per_share);"
        "create table dividend_events (ticker, date, amount);"
        "create table price_bars (ticker, date, close);")
    db.execute("insert into market_snapshots values ('AAA', 1.0, 0.04, 100, 100.0, 1e4, '2026-08-04T00:00:00')")
    total = {y: 2.0 * 1.05 ** (y - 2016) for y in range(2015, 2027)}
    for fy in range(2016, 2026):
        db.execute("insert into financials_annual values ('AAA', ?, ?, 1e3, 1e4, 100, 100, 10, ?, '10-K', ?)",
                   (fy, f"{fy}-12-31", f"{fy + 1}-02-20", total[fy]))
    for day, amount in [(f"{y}-01-15", total[y]) for y in range(2015, 2026)] + [("2025-12-30", total[2026]),
                                                                                ("2024-07-15", extra)] + list(more):
        db.execute("insert into dividend_events values ('AAA', ?, ?)", (day, amount))
    db.execute("insert into price_bars values ('AAA', '2026-08-03', 100.0)")
    return db


def test_the_oracles_ddm_keeps_an_annual_payers_slipped_year_beside_an_extra_payment():
    # BUG-366 through level 2: the extra no longer turns FY2025's SEC figure into Yahoo's two payments (13.7%).
    o = oracle.level2(_dividend_db(0.5), "AAA", "ddm", {"ttm_anchor": "2026-08-04"})
    assert o["inputs"]["hist_cagr"] == pytest.approx(0.05)


@pytest.mark.parametrize("extra, dps, slipped", [
    ((("2024-07-15", 0.5), ("2025-07-10", 0.5)), 3.10, True),   # BUG-378: 3 regular in 2025 beside 2 (3.45) before
    ((("2023-07-20", 0.5), ("2024-07-15", 0.5), ("2025-07-10", 0.5)), 3.10, True),
    ((("2024-07-15", 0.5), ("2025-07-10", 0.5)), 1.70, False),  # under half the side before's 3.45
    ((("2024-07-15", 0.5), ("2025-07-10", 0.5)), 1.73, True),
    ((("2024-07-15", 1.2),), 3.10, False),   # 2 regular in 2025 (3.10, 3.26) beside 2 before (2.95, 1.2): no slip
    # each side of two or more must be outnumbered: a quarterly 2026 (4) after it isn't; a side of one needn't be
    ((("2024-07-15", 0.5), ("2025-07-10", 0.5), ("2026-03-15", 0.85), ("2026-06-15", 0.85), ("2026-09-15", 0.85),
      ("2026-12-15", 0.85)), 3.10, False),
    ((("2024-07-15", 0.5), ("2025-07-10", 0.5), ("2026-06-15", 7.0)), 3.10, True),
    ((), 3.10, False),                       # no side of two
])
def test_the_oracles_gate_shuts_for_a_slipped_year_beside_a_recurring_extra(extra, dps, slipped):
    # BUG-378 (decided 2026-11-26, option C): the year holds more regular payments than the side that opened the
    # gate, and SEC's figure is at least half that side's regular payments: the figure is the year's.
    events = _annual(extra)
    assert oracle.slipped_year(events, oracle.one_offs(events), "2024-12-31", "2025-12-31", dps) is slipped


def test_the_oracles_gate_stays_open_for_a_quarters_rate_or_a_semiannual_final():
    # GPN's / J's shape: a quarterly payer's year holding a fifth payment, SEC tagging a quarter's rate (0.25 against
    # the side's 1.0): Yahoo's payments still stand in. BUG-372's semiannual payer (0.3 + 1.0, SEC the final alone):
    # its year holds no more payments than its sides.
    quarterly = [(f"{y}-{m:02d}-15", 0.25) for y in (2023, 2024, 2025) for m in (1, 4, 7, 10)] + [("2025-12-30", 0.25)]
    assert not oracle.slipped_year(quarterly, oracle.one_offs(quarterly), "2024-12-31", "2025-12-31", 0.25)
    small = [(f"{y}-{m}", a) for y in (2023, 2024, 2025, 2026) for m, a in (("03-15", 0.3), ("09-15", 1.0))]
    assert not oracle.slipped_year(small, oracle.one_offs(small), "2024-12-31", "2025-12-31", 1.0)


def test_the_oracles_ddm_keeps_an_annual_payers_slipped_year_beside_a_recurring_extra():
    # BUG-378 through level 2: the extra paid in 2024 and again in 2025 no longer opens the gate on FY2025 (3.10, 0.50
    # and 3.26 for SEC's 3.10).
    o = oracle.level2(_dividend_db(0.5, [("2025-07-10", 0.5)]), "AAA", "ddm", {"ttm_anchor": "2026-08-04"})
    assert o["inputs"]["hist_cagr"] == pytest.approx(0.05)


# --- BUG-262: the oracles' own reading of the splits the filings record ------------------------------------------

#: DD's 10-K views of FY2017-18 (fiscal_year, filed, diluted weighted count, diluted EPS, net income) and Yahoo's DD
#: history, whose 0.4725 is the 1:3 of 2019 times Corteva's spin-off.
DD_VINTAGES = [(2017, "2019-02-11", 1598.1e6, 0.91, 1460e6), (2017, "2020-02-14", 532.7e6, 2.15, 1159e6),
               (2018, "2019-02-11", 2315.5e6, 1.65, 3844e6), (2018, "2020-02-14", 771.8e6, 4.96, 3845e6)]
DD_YAHOO = [("2019-04-02", 1.487), ("2019-06-03", 0.4725)]


def _oracle(name):
    spec = importlib.util.spec_from_file_location(f"qa_{name}_262", ROOT / "qa" / "oracle" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["valuation_oracle", "scores_oracle", "history_vs_yahoo"])
def test_each_oracle_reads_the_split_the_filings_record_out_of_yahoos_mixed_factor(name):
    history = _oracle(name).filed_splits(DD_VINTAGES, DD_YAHOO)
    assert [day for day, _ in history] == ["2019-04-02", "2019-06-03", "2019-06-03"]
    assert sorted(ratio for _, ratio in history) == pytest.approx(sorted([1.487, 1 / 3, 1.4175]))
    # Chesapeake's 1:200 (EXE), which Yahoo's history lacks, on the first filing restated for it.
    exe = [(2018, "2020-02-27", 909e6, 0.15, 226e6), (2018, "2021-03-01", 4.546e6, 29.26, 226e6)]
    assert _oracle(name).filed_splits(exe, []) == [("2021-03-01", pytest.approx(1 / 200))]
    # A reverse merger restates net income too (KDP's FY2017), and a declared split is Yahoo's already.
    kdp = [(2017, "2018-02-14", 182.8e6, 5.89, 1076e6), (2017, "2020-02-27", 790.5e6, 0.47, 378e6)]
    assert _oracle(name).filed_splits(kdp, []) == []
    aapl = [(2019, "2019-10-31", 4648.9e6, 11.89, 55.256e9), (2019, "2020-10-30", 18595.7e6, 2.97, 55.256e9)]
    assert _oracle(name).filed_splits(aapl, [("2020-08-31", 4.0)]) == [("2020-08-31", 4.0)]


def test_the_valuation_oracle_reads_the_vintages_for_a_tickers_splits():
    db = sqlite3.connect(":memory:")
    db.execute("create table financials_annual_vintages (ticker, fiscal_year, filed_date, weighted_avg_shares_diluted,"
               " eps_diluted, net_income)")
    db.executemany("insert into financials_annual_vintages values ('DD', ?, ?, ?, ?, ?)", DD_VINTAGES)
    history = oracle.with_filed_splits(db, "DD", DD_YAHOO)
    assert sorted(ratio for _, ratio in history) == pytest.approx(sorted([1.487, 1 / 3, 1.4175]))
    assert oracle.with_filed_splits(sqlite3.connect(":memory:"), "DD", DD_YAHOO) == DD_YAHOO  # no vintages kept


# --- BUG-154: a payment Yahoo served twice ---------------------------------------------------------------------------

def test_the_oracle_reads_a_payment_served_twice_once():
    ko = [("2001-06-13", 0.09), ("2001-09-10", 0.18), ("2001-09-12", 0.18), ("2001-09-17", 0.09),
          ("2001-09-18", 0.18)]
    assert oracle.once_each(ko) == [("2001-06-13", 0.09), ("2001-09-10", 0.18), ("2001-09-17", 0.09),
                                    ("2001-09-18", 0.18)]


def test_the_oracles_completeness_check_reads_a_payment_served_twice_once():
    # CB's FY2025 without its Sep and Dec payments, and Yahoo's copies of its March and June ones (a week on).
    db = sqlite3.connect(":memory:")
    db.executescript("create table dividend_events (ticker, date, amount);"
                     "create table financials_annual (ticker, fiscal_year, period_end, dividends_per_share);")
    for year in range(2021, 2027):
        for month in ("03", "06", "09", "12"):
            if f"{year}-{month}" not in ("2025-09", "2025-12", "2026-09", "2026-12"):
                db.execute("insert into dividend_events values ('CB', ?, 0.97)", (f"{year}-{month}-13",))
    db.executemany("insert into dividend_events values ('CB', ?, 0.97)", [("2025-03-20",), ("2025-06-20",)])
    db.execute("insert into financials_annual values ('CB', 2025, '2025-12-31', 3.88)")
    assert oracle.payment_gaps(db, "CB", []) == [(2025, "2025-06-13", "2026-03-13")]


def test_the_oracles_ddm_reads_a_payment_served_twice_once():
    # The TTM's one payment (2025-12-30) served again on 2026-01-02.
    total_2026 = 2.0 * 1.05 ** 10
    db = _dividend_db(0.5)
    db.execute("insert into dividend_events values ('AAA', '2026-01-02', ?)", (total_2026,))
    o = oracle.level2(db, "AAA", "ddm", {"ttm_anchor": "2026-08-04"})
    assert o["inputs"]["ttm_dps"] == pytest.approx(total_2026)


# --- BUG-025 (1) and (2): the oracle's completeness check -----------------------------------------------------------

def _gaps_db(payments, years):
    db = sqlite3.connect(":memory:")
    db.executescript("create table dividend_events (ticker, date, amount);"
                     "create table financials_annual (ticker, fiscal_year, period_end, dividends_per_share);")
    db.executemany("insert into dividend_events values ('AAA', ?, ?)", payments)
    db.executemany("insert into financials_annual values ('AAA', ?, ?, ?)", years)
    return db


def test_the_oracles_completeness_check_reads_the_recent_schedule():
    # A payer once a year for decades, quarterly since 2020, missing 2025-08: a gap against the recent 92 days.
    payments = [(f"{y}-12-01", 2.8) for y in range(1975, 2020)]
    payments += [(f"{y}-{m}-15", 0.70) for y in range(2020, 2027) for m in ("02", "05", "08", "11") if
                 f"{y}-{m}" != "2025-08"]
    assert oracle.payment_gaps(_gaps_db(payments, [(2025, "2025-12-31", 2.80)]), "AAA", []) == [
        (2025, "2025-05-15", "2025-11-15")]
    # Quarterly for years, twice a year since 2020: a shortfall alone is no gap.
    semiannual = [(f"{y}-{m}-15", 0.45) for y in range(2005, 2020) for m in ("02", "05", "08", "11")]
    semiannual += [(f"{y}-{m}-10", 0.45) for y in range(2020, 2027) for m in ("01", "07")]
    assert oracle.payment_gaps(_gaps_db(semiannual, [(2025, "2025-12-31", 1.20)]), "AAA", []) == []


def test_the_oracles_completeness_check_ends_on_the_collection_day():
    payments = [(f"{y}-{m}-15", 0.70) for y in range(2021, 2027) for m in ("02", "05", "08", "11")
                if f"{y}-{m}" < "2026-05"]
    db = _gaps_db(payments + [("2026-05-26", 0.70)], [(2026, "2026-10-02", 2.80)])
    assert oracle.payment_gaps(db, "AAA", [], "2026-11-20") == [(2026, "2026-05-26", "2026-11-20")]
    assert oracle.payment_gaps(db, "AAA", [], "2026-09-20") == []
    assert oracle.payment_gaps(db, "AAA", []) == []


def test_the_oracles_ddm_ends_the_schedule_on_its_anchor():
    # A quarterly payer whose stored payments stop after 2025-06-15, valued on 2026-08-04: FY2025's two payments
    # against SEC's year, and no payment since: DDM is N/A (Q4) as the code's is.
    db = _dividend_db(0.5)
    db.execute("delete from dividend_events")
    total = {y: 2.0 * 1.05 ** (y - 2016) for y in range(2015, 2026)}
    db.executemany("insert into dividend_events values ('AAA', ?, ?)",
                   [(f"{y}-{m}-15", total[y] / 4) for y in range(2015, 2026) for m in ("03", "06", "09", "12")
                    if f"{y}-{m}" <= "2025-06"])
    o = oracle.level2(db, "AAA", "ddm", {"ttm_anchor": "2026-08-04"})
    assert o["na"] == "dividend history incomplete" and o["inputs"]["gap_years"] == [2025]
