"""History inputs (Milestone 1): share counts on the price bars' split basis, price factors, known dates.

docs/superpowers/specs/2026-10-21-valuation-history-design.md
"""
from datetime import date

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.history_inputs import (
    is_share_split,
    known_on,
    load_history_inputs,
    normalize_share_series,
    pick_share_field,
    price_factor_after,
)

# GE's split history: the 1:8 reverse split, then the GE HealthCare and GE Vernova spin-off factors.
GE_EVENTS = [("2021-08-02", 0.125), ("2023-01-04", 1.281), ("2024-04-02", 1.253)]
ENDS = ["2019-12-28", "2020-03-28", "2020-06-27", "2020-09-26", "2020-12-26"]


def test_share_splits_are_simple_ratios():
    assert is_share_split(4.0) and is_share_split(0.125) and is_share_split(1.5)
    assert not is_share_split(1.281)  # a spin-off's price factor
    assert not is_share_split(1.04)
    assert not is_share_split(1.0)
    assert not is_share_split(None)


def test_price_factor_counts_the_spin_offs_after_the_day():
    last = "2026-10-20"
    assert price_factor_after("2022-06-30", GE_EVENTS, last) == pytest.approx(1.281 * 1.253)
    assert price_factor_after("2023-06-30", GE_EVENTS, last) == pytest.approx(1.253)
    assert price_factor_after("2024-06-28", GE_EVENTS, last) == 1.0
    # The 1:8 is a share split: it moves the share counts, never the price factor.
    assert price_factor_after("2020-12-31", GE_EVENTS, last) == pytest.approx(1.281 * 1.253)
    # The ex-date's own close is already after the factor.
    assert price_factor_after("2023-01-04", GE_EVENTS, last) == pytest.approx(1.253)
    # A factor after the last bar isn't in the bars yet.
    assert price_factor_after("2026-06-30", [("2026-12-01", 1.3)], last) == 1.0


def test_known_dates_follow_the_filing_lag():
    collected = date(2026, 10, 21)
    assert known_on("2026-06-30", 2, None, collected) == date(2026, 8, 14)  # a 10-Q: + 45 days
    assert known_on("2025-12-31", 4, "2026-02-20", collected) == date(2026, 2, 20)  # the 10-K's own day
    assert known_on("2025-12-31", 4, None, collected) == date(2026, 3, 16)  # no 10-K date: + 75 days
    assert known_on("2025-12-31", 4, "2026-06-30", collected) == date(2026, 3, 16)  # 181 days on: not its 10-K
    assert known_on("2026-09-30", 3, None, collected) == collected  # stored, so known by the collection day


def test_share_field_is_the_first_covering_most_quarters():
    rows = [{"weighted_avg_shares_basic": 1.0 if i < 5 else None,
             "shares_outstanding": 1.0 if i < 9 else None} for i in range(10)]
    assert pick_share_field(rows) == "shares_outstanding"  # basic covers 50% of the quarters
    rows[5]["weighted_avg_shares_basic"] = 1.0
    assert pick_share_field(rows) == "weighted_avg_shares_basic"  # 60%
    assert pick_share_field([{"net_income": 10.0, "eps_diluted": 2.0}] * 4) == "net_income/eps_diluted"
    assert pick_share_field([{}] * 4) is None


def test_a_declared_split_folds_its_seam():
    # AAPL-like: the 4:1 of 2020-08-31 shows as 3.97x between the June and September quarters.
    counts = [4.40e9, 4.36e9, 4.33e9, 17.19e9, 17.11e9]
    eps = [5.0, 2.6, 2.6, 0.73, 1.7]
    out, applied = normalize_share_series(ENDS, counts, eps, [("2020-08-31", 4.0)])
    assert out == pytest.approx([17.60e9, 17.44e9, 17.32e9, 17.19e9, 17.11e9])
    assert applied == [("2020-08-31", 4.0)]


def test_a_merger_seam_stays_organic():
    # COF-like: 1.56x the shares from a stock-paid merger, EPS not inverted, no split declared.
    counts = [380e6, 382e6, 596e6, 600e6, 601e6]
    out, applied = normalize_share_series(ENDS, counts, [3.0, 3.1, 3.3, 3.4, 3.5], [])
    assert out == pytest.approx(counts)
    assert applied == []


def test_an_undeclared_seam_folds_only_when_eps_moved_inversely():
    counts = [100e6, 101e6, 202e6, 203e6, 204e6]
    out, _ = normalize_share_series(ENDS, counts, [2.0, 2.0, 1.0, 1.0, 1.0], [])
    assert out[:2] == pytest.approx([200e6, 202e6])  # folded by the seam's own ratio, 2.0


def test_a_one_quarter_spike_is_dropped():
    counts = [100e6, 100e6, 130e6, 101e6, 100e6]
    out, _ = normalize_share_series(ENDS, counts, [None] * 5, [])
    assert out == pytest.approx([100e6, 100e6, 100e6, 101e6, 100e6])  # the gap carries the count before it


def test_a_split_after_the_latest_quarter_needs_yahoos_count():
    # APH-like: a 2:1 on 2026-09-03, after the latest quarter, which no stored quarter reflects.
    ends = ["2025-12-31", "2026-03-31", "2026-06-30"]
    out, applied = normalize_share_series(ends, [1.21e9, 1.215e9, 1.22e9], [None] * 3, [("2026-09-03", 2.0)],
                                          yahoo_count=2.44e9)
    assert out == pytest.approx([2.42e9, 2.43e9, 2.44e9])
    assert applied == [("2026-09-03", 2.0)]
    # Quarters a filing already restated: the split would move them away from Yahoo's count.
    out, applied = normalize_share_series(ends, [2.42e9, 2.43e9, 2.44e9], [None] * 3, [("2026-09-03", 2.0)],
                                          yahoo_count=2.44e9)
    assert out == pytest.approx([2.42e9, 2.43e9, 2.44e9])
    assert applied == []


def test_a_unit_seam_cuts_the_older_quarters():
    counts = [100e3, 101e3, 102e6, 103e6, 104e6]  # filed in thousands, then in units
    out, _ = normalize_share_series(ENDS, counts, [None] * 5, [])
    assert out[:2] == [None, None]
    assert out[2:] == pytest.approx([102e6, 103e6, 104e6])


# ---------- load_history_inputs on a synthetic database ----------

QUARTERS = [  # (period_end, fiscal_year, fiscal_quarter, basic shares as filed, diluted EPS as filed)
    ("2024-03-31", 2024, 1, 100e6, 1.0),
    ("2024-06-30", 2024, 2, 100e6, 1.0),
    ("2024-09-30", 2024, 3, 100e6, 1.0),
    ("2024-12-31", 2024, 4, 100e6, 1.0),
    ("2025-03-31", 2025, 1, 100e6, 1.1),
    ("2025-06-30", 2025, 2, 200e6, 0.55),  # the 2:1 of 2025-05-15
    ("2025-09-30", 2025, 3, 200e6, 0.55),
    ("2025-12-31", 2025, 4, 200e6, 0.55),
]


def _db(tmp_path, sector="general", yahoo_cap=4.0e9, ttm=True):
    store = SQLiteStore(db_path=tmp_path / "hist.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('AAA', ?)", (sector,))
    for end, fy, fq, shares, eps in QUARTERS:
        conn.execute(
            "INSERT INTO financials_quarterly (ticker, period_end, fiscal_year, fiscal_quarter, "
            "weighted_avg_shares_basic, eps_diluted, net_income) VALUES ('AAA', ?, ?, ?, ?, ?, ?)",
            (end, fy, fq, shares, eps, 110e6))
        if ttm and end >= "2024-12-31":
            conn.execute(
                "INSERT INTO financials_ttm (ticker, period_end, fiscal_year, revenue, net_income, total_equity, "
                "preferred_equity, operating_cash_flow, capex, operating_income, depreciation_amortization, "
                "long_term_debt, cash_and_equivalents) VALUES ('AAA', ?, ?, 1.0e9, 4.4e8, 2.0e9, 1.0e8, 5.0e8, "
                "-1.0e8, 6.0e8, 1.0e8, 9.0e8, 3.0e8)", (end, fy))
    for fy, end, filed in ((2024, "2024-12-31", "2025-02-20"), (2025, "2025-12-31", "2026-02-19")):
        conn.execute("INSERT INTO financials_annual (ticker, fiscal_year, period_end) VALUES ('AAA', ?, ?)",
                     (fy, end))
        conn.execute("INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn, filed_date, period_end, "
                     "form) VALUES ('AAA', ?, ?, ?, ?, '10-K')", (fy, f"acc-{fy}", filed, end))
    # A later 10-K restating FY2024 is not its first report.
    conn.execute("INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn, filed_date, period_end, form) "
                 "VALUES ('AAA', 2024, 'acc-2025', '2026-02-19', '2024-12-31', '10-K')")
    conn.executemany("INSERT INTO split_events (ticker, date, ratio) VALUES ('AAA', ?, ?)",
                     [("2025-05-15", 2.0), ("2025-08-01", 1.1)])
    bars = [("2025-01-15", 10.0), ("2025-01-31", 11.0), ("2025-02-27", 12.0), ("2025-02-28", 12.5),
            ("2025-12-31", 20.0), ("2026-02-24", 21.0)]
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', ?, ?)", bars)
    conn.executemany("INSERT INTO dividend_events (ticker, date, amount) VALUES ('AAA', ?, ?)",
                     [("2025-03-01", 0.10), ("2025-09-01", 0.10)])
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price, market_cap, "
                 "shares_outstanding) VALUES ('AAA', '2026-02-25T10:00:00', 21.0, ?, 190e6)", (yahoo_cap,))
    conn.execute("INSERT INTO collection_runs (ticker, collected_at) VALUES ('AAA', '2026-02-25T10:00:00')")
    conn.commit()
    return conn


def test_load_history_inputs_puts_every_quarter_on_the_bars_basis(tmp_path):
    conn = _db(tmp_path)
    x = load_history_inputs(conn, "AAA")
    assert x.na_reason is None
    assert x.share_field == "weighted_avg_shares_basic"
    assert x.splits_applied == [("2025-05-15", 2.0)]
    by_end = {p.period_end: p for p in x.points}
    assert by_end["2024-12-31"].shares == pytest.approx(200e6)  # 100M as filed, on the post-split basis
    assert by_end["2025-12-31"].shares == pytest.approx(200e6)
    # Known dates: a Q4 on its first 10-K (not the later one restating it), a Q1 + 45 days.
    assert by_end["2024-12-31"].known_on == date(2025, 2, 20)
    assert by_end["2025-03-31"].known_on == date(2025, 5, 15)
    assert by_end["2025-12-31"].known_on == date(2026, 2, 19)
    # Fundamentals through CalculatedMetrics: FCF = OCF - |capex|, EBITDA = EBIT + D&A, net debt = debt - cash.
    p = by_end["2025-12-31"]
    assert (p.revenue, p.net_income, p.common_equity) == (1.0e9, 4.4e8, 1.9e9)
    assert (p.fcf, p.ebitda, p.net_debt) == (4.0e8, 7.0e8, 6.0e8)
    # One sample per month, its last bar; the latest bar last.
    assert x.months == [("2025-01-31", 11.0), ("2025-02-28", 12.5), ("2025-12-31", 20.0), ("2026-02-24", 21.0)]
    assert x.last_bar == "2026-02-24"
    assert x.price_factors == [("2025-08-01", 1.1)]  # the spin-off factor only
    assert x.yahoo_shares == pytest.approx(4.0e9 / 21.0)
    assert x.collected_on == date(2026, 2, 25)
    assert [d for d, _ in x.dividends] == ["2025-03-01", "2025-09-01"]


def test_a_bank_has_no_fcf_ebitda_or_net_debt(tmp_path):
    conn = _db(tmp_path, sector="bank")
    p = load_history_inputs(conn, "AAA").points[-1]
    assert (p.fcf, p.ebitda, p.net_debt) == (None, None, None)
    assert p.revenue == 1.0e9


def test_no_trailing_years_means_no_history(tmp_path):
    conn = _db(tmp_path, ttm=False)
    assert load_history_inputs(conn, "AAA").na_reason == "no trailing-twelve-month financials"


def test_a_share_count_far_from_yahoos_means_no_history(tmp_path):
    conn = _db(tmp_path, yahoo_cap=21.0 * 400e6)  # Yahoo counts 400M, we count 200M: another class
    reason = load_history_inputs(conn, "AAA").na_reason
    assert reason is not None and "share count" in reason


def test_an_unknown_ticker_means_no_history(tmp_path):
    conn = _db(tmp_path)
    assert load_history_inputs(conn, "ZZZ").na_reason == "no company record"


def test_no_capex_means_no_fcf(tmp_path):
    # Operating cash flow alone isn't free cash flow (CalculatedMetrics falls back to OCF).
    conn = _db(tmp_path)
    conn.execute("UPDATE financials_ttm SET capex = NULL")
    p = load_history_inputs(conn, "AAA").points[-1]
    assert p.fcf is None
    assert p.ebitda == 7.0e8


def test_a_spin_off_factor_never_folds_a_share_seam(tmp_path):
    # A 1.3x jump in the count (an acquisition) beside a 1.3 spin-off factor: only share splits fold.
    conn = _db(tmp_path)
    conn.execute("UPDATE financials_quarterly SET weighted_avg_shares_basic = weighted_avg_shares_basic * 1.3 "
                 "WHERE period_end >= '2024-09-30'")
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('AAA', '2024-08-01', 1.3)")
    conn.execute("INSERT INTO financials_ttm (ticker, period_end, fiscal_year, revenue) "
                 "VALUES ('AAA', '2024-06-30', 2024, 1.0e9)")
    by_end = {p.period_end: p for p in load_history_inputs(conn, "AAA").points}
    assert by_end["2024-06-30"].shares == pytest.approx(200e6)  # 100M as filed, folded by the 2:1 only
    assert by_end["2024-12-31"].shares == pytest.approx(260e6)  # the acquisition's 130M, likewise
