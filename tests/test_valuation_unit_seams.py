"""A share-count seam beyond MAX_SPLIT_RATIO folds when a stored split or the count EPS implies explains it, and
truncates the older years otherwise (Q19, decided): EXE's FY2017-18 seam is Chesapeake's 1:200 reverse split of
2020, restated into FY2018, which Yahoo's history (begun at the 2021 relisting) lacks."""
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation import inputs as inputs_module
from src.valuation.inputs import load_inputs

# EXE's FY2016-20 as stored: (fy, net income, net income to common, diluted EPS, diluted weighted count). Net income
# is before the preferred dividends, so only the figure to common divides into the count EPS reads.
EXE = [
    (2016, -4390e6, -4915e6, -6.43, 764e6),
    (2017, -505e6, -631e6, -0.70, 906e6),
    (2018, 226e6, 133e6, 29.26, 4.546e6),
    (2019, -308e6, -416e6, -49.97, 8.325e6),
    (2020, -9734e6, -9756e6, -998.26, 9.773e6),
]


def _db(tmp_path, rows, splits=(), name="seam.db", filed=False):
    """Ticker 'SPL' from *rows* and stored (date, ratio) *splits*; with *filed*, each year's figures were last filed
    on 20 February of the next (the vintages), so a split is placed between two years' filings."""
    db_path = tmp_path / name
    store = SQLiteStore(db_path=db_path)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('SPL', 'general')")
    for fy, ni, to_common, eps, shares in rows:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, net_income, net_income_to_common, "
            "total_equity, eps_diluted, weighted_avg_shares_diluted) VALUES ('SPL', ?, ?, ?, ?, 800.0, ?, ?)",
            (fy, f"{fy}-12-31", ni, to_common, eps, shares))
        if filed:
            conn.execute(
                "INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn, filed_date, period_end, form, "
                "net_income, eps_diluted, weighted_avg_shares_diluted) VALUES ('SPL', ?, ?, ?, ?, '10-K', ?, ?, ?)",
                (fy, f"a{fy}", f"{fy + 1}-02-20", f"{fy}-12-31", ni, eps, shares))
    for day, ratio in splits:
        conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('SPL', ?, ?)", (day, ratio))
    conn.commit()
    conn.close()
    return db_path


def _load(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return load_inputs(conn, "SPL")
    finally:
        conn.close()


def test_exes_reverse_split_folds_by_the_count_eps_to_common_implies(tmp_path):
    inputs = _load(_db(tmp_path, EXE))
    assert inputs.history_truncated is False
    assert [r.fiscal_year for r in inputs.fy_records] == [2016, 2017, 2018, 2019, 2020]
    by_fy = {r.fiscal_year: r for r in inputs.fy_records}
    seam = 4.546e6 / 906e6
    assert by_fy[2017].split_factor == pytest.approx(seam)
    assert by_fy[2016].split_factor == pytest.approx(seam)
    assert by_fy[2018].split_factor == 1.0
    assert by_fy[2017].eps_diluted == pytest.approx(-0.70 / seam)  # about -139.5 on today's basis
    assert by_fy[2017].shares == pytest.approx(4.546e6)
    assert by_fy[2017].net_income == -505e6  # split-invariant


def test_net_income_before_preferred_dividends_stands_in_without_the_figure_to_common(tmp_path):
    # Without the figure to common, EPS implies 721M then 7.72M shares (1:93), not the seam's 1:199: unexplained.
    rows = [(fy, ni, None, eps, shares) for fy, ni, _, eps, shares in EXE]
    inputs = _load(_db(tmp_path, rows))
    assert inputs.history_truncated is True
    assert [r.fiscal_year for r in inputs.fy_records] == [2018, 2019, 2020]
    # Where net income is the figure EPS divides, it explains the seam on its own.
    rows = [(fy, to_common, None, eps, shares) for fy, _, to_common, eps, shares in EXE]
    inputs = _load(_db(tmp_path, rows, name="to_common_as_ni.db"))
    assert inputs.history_truncated is False
    assert len(inputs.fy_records) == 5


def test_a_stored_split_explains_a_seam_its_eps_cannot(tmp_path):
    # FY2017 lacks its EPS (nothing implies a count), but the stored 1:200 of April 2020 lies after it.
    rows = [(fy, ni, tc, None if fy == 2017 else eps, shares) for fy, ni, tc, eps, shares in EXE]
    inputs = _load(_db(tmp_path, rows, splits=[("2020-04-14", 0.005)]))
    assert inputs.history_truncated is False
    by_fy = {r.fiscal_year: r for r in inputs.fy_records}
    assert by_fy[2017].split_factor == pytest.approx(0.005)  # the declared ratio, not the seam's 0.00502
    assert by_fy[2017].shares == pytest.approx(906e6 * 0.005)
    # Without the split, the same rows truncate.
    assert _load(_db(tmp_path, rows, name="no_split.db")).history_truncated is True


def test_a_stored_split_far_from_a_unit_seam_does_not_explain_it(tmp_path):
    # A thousands-to-units seam (1000x, EPS unmoved) with a 2:1 split between: the split is nearer than 1.0, as a
    # seam within 50x would fold by, but it explains a factor of 2, not 1000. The older years still go.
    rows = [
        (2017, 1.05e9, None, 0.95, 1_100_000.0),
        (2018, 1.10e9, None, 1.00, 1_110_000.0),
        (2019, 1.15e9, None, 1.05, 1_120_000.0),
        (2020, 1.20e9, None, 0.55, 2_180_000_000.0),
        (2021, 1.25e9, None, 0.58, 2_160_000_000.0),
    ]
    inputs = _load(_db(tmp_path, rows, splits=[("2020-06-01", 2.0)], filed=True))
    assert inputs.history_truncated is True
    assert [r.fiscal_year for r in inputs.fy_records] == [2020, 2021]


def test_each_stored_split_explains_one_seam(tmp_path):
    # Two 1:200 seams, one stored 1:200: the newer seam takes it; the older one, without the EPS to imply a count,
    # truncates.
    rows = [
        (2016, 100e6, None, None, 2e9),
        (2017, 110e6, None, 0.011, 10e6),
        (2018, 120e6, None, 0.0121, 9.9e6),
        (2019, 130e6, None, None, 9.8e6),
        (2020, 140e6, None, 2857.0, 49e3),
    ]
    inputs = _load(_db(tmp_path, rows, splits=[("2020-03-01", 0.005)]))
    assert inputs.history_truncated is True
    assert [r.fiscal_year for r in inputs.fy_records] == [2017, 2018, 2019, 2020]
    assert {r.fiscal_year: r.split_factor for r in inputs.fy_records}[2017] == pytest.approx(0.005)


def test_a_seam_between_two_counts_eps_implies_truncates(tmp_path):
    # BUG-377: neither year has a diluted weighted count, so each year's detection count is net income / EPS, and the
    # count EPS implies is the seam itself: it can't tell a reverse split from an EPS filed 1,000x small. Unexplained.
    rows = [(2016, 0.95e9, None, 0.95, None), (2017, 1.0e9, None, 1.00, None), (2018, 1.1e9, None, 0.0011, None),
            (2019, 1.2e9, None, 0.0012, None)]
    inputs = _load(_db(tmp_path, rows))
    assert inputs.history_truncated is True
    assert [r.fiscal_year for r in inputs.fy_records] == [2018, 2019]
    # A diluted count in one of the two years is checked by the count its EPS implies: the seam folds.
    rows[2] = (2018, 1.1e9, None, 0.0011, 1.0e12)
    inputs = _load(_db(tmp_path, rows, name="one_count.db"))
    assert inputs.history_truncated is False
    assert len(inputs.fy_records) == 4


@pytest.mark.parametrize("income_older, income_newer, eps_older, eps_newer, explains", [
    (-631e6, 133e6, -0.70, 29.26, True),        # EXE's: EPS changed sign with income, the counts 901M then 4.5M
    (631e6, 133e6, 0.70, 29.26, True),
    (631e6, 133e6, 0.70, 29.26 * 1.24, True),   # within DECLARED_SPLIT_TOLERANCE
    (631e6, 133e6, 0.70, 29.26 * 1.30, False),
    (None, 133e6, 0.70, 29.26, False),          # unknown doesn't explain a seam beyond 50x
    (631e6, 133e6, None, 29.26, False),
    (631e6, 133e6, 0.70, 0.0, False),
    (631e6, -133e6, 0.70, 29.26, False),        # an implied count below zero
])
def test_the_implied_count_explains_a_seam_only_when_every_figure_is_known(income_older, income_newer, eps_older,
                                                                            eps_newer, explains):
    seam = (133e6 / 29.26) / (631e6 / 0.70)
    assert inputs_module._implied_count_explains(seam, income_older, income_newer, eps_older, eps_newer) is explains
