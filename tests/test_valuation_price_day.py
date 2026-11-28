"""The price's day on New York's calendar (BUG-079), Yahoo's count across a spin-off after the last bar (BUG-178), and a
quarter stored by a weekend collection known at the latest bar (BUG-250)."""
import importlib.util
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from src import new_york
from src.exporters.sqlite_store import SQLiteStore
from src.valuation.history import compute_history
from src.valuation.history_inputs import known_on, load_history_inputs
from src.valuation.inputs import load_inputs

from .test_valuation_history_inputs import _db

# 02:30 on a split's day east of New York (UTC+7) is 15:30 the day before there: before the split.
EAST_BEFORE_THE_SPLIT = "2026-06-12T02:30:00+07:00"


@pytest.mark.parametrize("stamp, day", [
    (EAST_BEFORE_THE_SPLIT, "2026-06-11"),
    ("2026-06-12T10:30:00-04:00", "2026-06-12"),
    ("2026-06-12T23:30:00Z", "2026-06-12"),  # 19:30 in New York
    ("2026-01-15T03:00:00+00:00", "2026-01-14"),  # EST: 22:00 the day before
    ("2026-01-15T04:30:00+00:00", "2026-01-14"),  # 23:30 in EST, 00:30 the next day in EDT
    ("2026-06-12", "2026-06-12"),  # a day alone stays that day
    (None, None),
    ("", None),
])
def test_a_stamps_day_is_new_yorks(stamp, day):
    assert new_york.new_york_day_of(stamp) == day


def _oracle():
    path = Path(__file__).resolve().parent.parent / "qa" / "oracle" / "valuation_oracle.py"
    spec = importlib.util.spec_from_file_location("qa_valuation_oracle_day", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.mark.parametrize("stamp", [EAST_BEFORE_THE_SPLIT, "2026-06-12T10:30:00-04:00", "2026-06-12T23:30:00Z",
                                   "2026-01-15T03:00:00+00:00", "2026-01-15T04:30:00+00:00",
                                   "2026-03-08T06:59:00+00:00",
                                   "2026-03-08T07:00:00+00:00", "2026-11-01T05:30:00+00:00",
                                   "2026-11-01T06:30:00+00:00", "2026-06-12T02:30:00", "2026-06-12", None])
def test_the_valuation_oracle_reads_the_price_day_as_the_code_does(stamp):
    assert _oracle().exchange_day(stamp) == new_york.new_york_day_of(stamp)


def test_the_valuation_oracle_prices_on_new_yorks_day(tmp_path):
    conn = _split_on_the_price_day_db(tmp_path, EAST_BEFORE_THE_SPLIT)
    conn.row_factory = None
    assert _oracle().Inputs(conn, "KLA").priced_on == "2026-06-11"


def test_a_naive_stamp_is_this_machines_time():
    assert new_york.new_york_day_of("2026-06-12T02:30:00") == new_york.new_york_day(
        datetime(2026, 6, 12, 2, 30)).isoformat()


def _split_on_the_price_day_db(tmp_path, collected_at):
    """KLAC-like: FY2023-2025 filed on 2025-08-08, a 10:1 split on 2026-06-12, and a quote stamped *collected_at* at a
    pre-split price and count (BUG-048's scenario)."""
    store = SQLiteStore(db_path=tmp_path / "price_day.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, sector_class) VALUES ('KLA', 'general')")
    for fy, eps in [(2023, 25.00), (2024, 20.28), (2025, 30.37)]:
        conn.execute(
            "INSERT INTO financials_annual (ticker, fiscal_year, period_end, filed_date, net_income, total_equity, "
            "eps_diluted, weighted_avg_shares_diluted) VALUES ('KLA', ?, ?, '2025-08-08', ?, 4.0e9, ?, 1.3375e8)",
            (fy, f"{fy}-06-30", eps * 1.3375e8, eps))
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('KLA', '2026-06-12', 10.0)")
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, shares_outstanding, market_cap, current_price) "
                 "VALUES ('KLA', ?, 1.3375e8, 1.3375e10, 100.0)", (collected_at,))
    conn.commit()
    conn.row_factory = sqlite3.Row  # as load_inputs is read
    return conn


def test_a_quote_before_new_yorks_split_day_keeps_the_years_on_its_basis(tmp_path):
    # BUG-079: stamped 02:30 on the split's day in UTC+7, the quote is New York's 15:30 the day before, pre-split. The
    # split dated after the price's day moves no year (BUG-048): Graham 452.06, not 45.21.
    conn = _split_on_the_price_day_db(tmp_path, EAST_BEFORE_THE_SPLIT)
    latest = load_inputs(conn, "KLA").fy_records[-1]
    assert (latest.split_factor, latest.eps_diluted) == (1.0, 30.37)


def test_a_quote_on_new_yorks_split_day_takes_the_split(tmp_path):
    conn = _split_on_the_price_day_db(tmp_path, "2026-06-12T10:30:00-04:00")
    assert load_inputs(conn, "KLA").fy_records[-1].split_factor == 10.0


def test_the_historys_price_day_is_new_yorks(tmp_path):
    conn = _db(tmp_path)
    conn.execute("UPDATE market_snapshots SET collected_at = '2026-02-25T09:00:00+09:00'")  # 19:00 on the 24th
    conn.commit()
    assert load_history_inputs(conn, "AAA").priced_on == "2026-02-24"


def test_the_stores_price_day_is_new_yorks(tmp_path):
    # The refresh's check for splits since the stored price reads its day as the engine does: a split stored on New
    # York's next day still comes after the price.
    conn = _split_on_the_price_day_db(tmp_path, EAST_BEFORE_THE_SPLIT)
    conn.close()
    store = SQLiteStore(db_path=tmp_path / "price_day.db")
    split = {"date": "2026-06-12", "ratio": 10.0}
    assert store.factors_since_collection("KLA", [split]) == [split]


def test_the_stores_last_collection_day_is_new_yorks(tmp_path):
    # A run at 02:30 on the 13th in UTC+7 is New York's 12th: a split Yahoo dates the 12th and the run didn't store
    # may have come after it, though a quote refresh priced the ticker since.
    conn = _split_on_the_price_day_db(tmp_path, "2026-06-13T10:00:00-04:00")
    conn.execute("DELETE FROM split_events")
    conn.execute("INSERT INTO collection_runs (ticker, collected_at, data_sources) "
                 "VALUES ('KLA', '2026-06-13T02:30:00+07:00', 'yahoo_finance, sec_edgar')")
    conn.commit()
    conn.close()
    split = {"date": "2026-06-12", "ratio": 10.0}
    assert SQLiteStore(db_path=tmp_path / "price_day.db").factors_since_collection("KLA", [split]) == [split]


# ---- BUG-178 ------------------------------------------------------------------------------------------------------

def test_a_spin_off_after_the_last_bar_leaves_yahoos_count_alone(tmp_path):
    # The bars end 2026-02-24; a 1.30 spin-off factor on the quote's day moves the price, not the count (4.0B / 21).
    conn = _db(tmp_path)
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('AAA', '2026-02-25', 1.3)")
    conn.commit()
    x = load_history_inputs(conn, "AAA")
    assert x.yahoo_shares == pytest.approx(4.0e9 / 21.0)
    assert x.na_reason is None
    assert x.bars_to_price == pytest.approx(1 / 1.3)  # the closes still go onto the quote's basis


def test_a_split_after_the_last_bar_still_moves_yahoos_count_onto_the_bars(tmp_path):
    conn = _db(tmp_path, yahoo_cap=21.0 * 400e6)  # 400M after a 2:1 on the quote's day: 200M on the bars' basis
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('AAA', '2026-02-25', 2.0)")
    conn.commit()
    x = load_history_inputs(conn, "AAA")
    assert x.yahoo_shares == pytest.approx(200e6)
    assert x.na_reason is None


# ---- BUG-250 ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("collected, known", [
    (date(2026, 10, 25), date(2026, 10, 23)),  # Sunday: SEC accepts no filing at weekends, so Friday's
    (date(2026, 10, 24), date(2026, 10, 23)),  # Saturday
    (date(2026, 10, 26), date(2026, 10, 26)),  # Monday: one could be filed that morning
    (date(2026, 10, 21), date(2026, 10, 21)),  # a weekday
])
def test_a_quarter_stored_by_a_weekend_collection_is_known_by_the_last_weekday(collected, known):
    assert known_on("2026-09-30", 3, None, collected) == known


def test_a_quarter_known_before_the_collection_keeps_its_lag():
    assert known_on("2026-06-30", 2, None, date(2026, 10, 25)) == date(2026, 8, 14)


def test_the_latest_bar_before_a_weekend_collection_reads_the_quarter_it_stored(tmp_path):
    # BUG-250: bars to Friday 2026-10-23, collected Sunday 10-25: the September quarter (known 11-14 by the lag) was
    # filed by Friday, so the latest point is on it, not on June's.
    conn = _db(tmp_path)
    conn.execute("INSERT INTO financials_quarterly (ticker, period_end, fiscal_year, fiscal_quarter, "
                 "weighted_avg_shares_basic, eps_diluted, net_income) VALUES "
                 "('AAA', '2026-03-31', 2026, 1, 200e6, 0.55, 110e6), "
                 "('AAA', '2026-06-30', 2026, 2, 200e6, 0.55, 110e6), "
                 "('AAA', '2026-09-30', 2026, 3, 200e6, 0.55, 110e6)")
    for end in ("2026-03-31", "2026-06-30", "2026-09-30"):
        conn.execute("INSERT INTO financials_ttm (ticker, period_end, fiscal_year, revenue, net_income, total_equity) "
                     "VALUES ('AAA', ?, 2026, 1.0e9, 4.4e8, 2.0e9)", (end,))
    conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', '2026-10-23', 22.0)")
    conn.execute("UPDATE market_snapshots SET collected_at = '2026-10-25T20:00:00-04:00'")
    conn.execute("UPDATE collection_runs SET collected_at = '2026-10-25T20:00:00-04:00'")
    conn.commit()
    x = load_history_inputs(conn, "AAA")
    assert x.collected_on == date(2026, 10, 25)
    history = compute_history(x)
    assert (history.rows[-1].date, history.rows[-1].ttm_period_end) == ("2026-10-23", "2026-09-30")


def test_a_collection_east_of_new_york_is_dated_by_new_yorks_day(tmp_path):
    conn = _db(tmp_path)
    conn.execute("UPDATE collection_runs SET collected_at = '2026-02-25T09:00:00+09:00'")  # 19:00 on the 24th
    conn.commit()
    assert load_history_inputs(conn, "AAA").collected_on == date(2026, 2, 24)


def test_a_trailing_year_ending_after_the_collection_is_never_known(tmp_path):
    # CCI: a stray 2026-12-31 row, empty but for its treasury stock, collected 2026-02-25 here: no point reads it.
    conn = _db(tmp_path)
    conn.execute("INSERT INTO financials_quarterly (ticker, period_end, treasury_stock) "
                 "VALUES ('AAA', '2026-12-31', 0)")
    conn.execute("INSERT INTO financials_ttm (ticker, period_end, treasury_stock) VALUES ('AAA', '2026-12-31', 0)")
    conn.commit()
    x = load_history_inputs(conn, "AAA")
    assert x.points[-1].period_end == "2025-12-31"
    assert compute_history(x).rows[-1].ttm_period_end == "2025-12-31"


def test_a_trailing_year_ending_on_the_collection_day_is_known(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO financials_ttm (ticker, period_end, fiscal_year, revenue) "
                 "VALUES ('AAA', '2026-02-25', 2026, 1.0e9)")
    conn.commit()
    assert load_history_inputs(conn, "AAA").points[-1].period_end == "2026-02-25"


# ---- BUG-388 ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("end, quarter, collected", [
    ("2026-10-24", 3, date(2026, 10, 25)),  # a 52/53-week quarter ending Saturday, collected Sunday
    ("2026-10-25", 4, date(2026, 10, 25)),  # a year ending Sunday, collected that Sunday
    ("2026-10-24", 3, date(2026, 10, 24)),  # ending Saturday, collected that Saturday
])
def test_a_trailing_year_is_never_known_before_it_ends(end, quarter, collected):
    # BUG-250's weekday cap put these on Friday 2026-10-23, the day before they ended
    assert known_on(end, quarter, None, collected) >= date.fromisoformat(end)


def test_the_weekday_cap_still_holds_a_quarter_that_ended_before_the_weekend():
    assert known_on("2026-10-23", 3, None, date(2026, 10, 25)) == date(2026, 10, 23)  # ended Friday: known Friday
    assert known_on("2026-09-30", 3, None, date(2026, 10, 25)) == date(2026, 10, 23)


def test_a_trailing_year_ending_on_the_collections_weekend_isnt_read_by_the_friday_bar(tmp_path):
    # a Saturday 2026-10-24 row a Sunday collection stored: no 10-Q for it can have been filed by then, so Friday's
    # bar (the latest point) stays on September's trailing year and no point reads the stray row
    conn = _db(tmp_path)
    for end, fq in (("2026-03-31", 1), ("2026-06-30", 2), ("2026-09-30", 3), ("2026-10-24", 4)):
        conn.execute("INSERT INTO financials_quarterly (ticker, period_end, fiscal_year, fiscal_quarter, "
                     "weighted_avg_shares_basic, eps_diluted, net_income) VALUES ('AAA', ?, 2026, ?, 200e6, 0.55, "
                     "110e6)", (end, fq))
        conn.execute("INSERT INTO financials_ttm (ticker, period_end, fiscal_year, revenue, net_income, total_equity) "
                     "VALUES ('AAA', ?, 2026, 1.0e9, 4.4e8, 2.0e9)", (end,))
    conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', '2026-10-23', 22.0)")
    conn.execute("UPDATE market_snapshots SET collected_at = '2026-10-25T20:00:00-04:00'")
    conn.execute("UPDATE collection_runs SET collected_at = '2026-10-25T20:00:00-04:00'")
    conn.commit()
    x = load_history_inputs(conn, "AAA")
    assert "2026-10-24" not in [point.period_end for point in x.points]
    history = compute_history(x)
    assert (history.rows[-1].date, history.rows[-1].ttm_period_end) == ("2026-10-23", "2026-09-30")
