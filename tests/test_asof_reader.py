"""Tests for the point-in-time as-of-date query API: a lookup over the annual record as known at each filing date
(``financials_annual_known``). What the record holds on a day (a figure a later 10-K leaves out, a narrower concept,
a debt view) is the annual record's, tested with its builder (``test_annual_record``, ``test_annual_snapshots``)."""

import sqlite3
from datetime import date

import pytest

from src.exporters.sqlite_store import _CANONICAL_COLUMNS
from src.query.asof import AsOfReader
from tests.known_rows import build_db, snapshot, tombstone

#: PRU's FY2019: filed 2020-02-15, restated 2021-02-15.
_ORIGINAL = snapshot(2019, "2020-02-15", accn="a-orig", net_income=100.0, revenue=1000.0)
_RESTATED = snapshot(2019, "2021-02-15", accn="b-restate", net_income=90.0, revenue=1000.0)


def _pru(tmp_path):
    return build_db(tmp_path, [_ORIGINAL, _RESTATED])


def test_reader_connection_is_read_only(tmp_path):
    reader = AsOfReader(_pru(tmp_path))
    with pytest.raises(sqlite3.OperationalError):
        reader._conn.execute(
            "INSERT INTO financials_annual_known (ticker, fiscal_year, known_from, present) VALUES ('X', 1, 'y', 1)")
    reader.close()


def test_reader_context_manager_closes(tmp_path):
    with AsOfReader(_pru(tmp_path)) as reader:
        assert reader._conn is not None
    # After exit, using the closed connection raises.
    with pytest.raises(sqlite3.ProgrammingError):
        reader._conn.execute("SELECT 1")


def test_the_latest_row_on_or_before_the_date_wins(tmp_path):
    db = build_db(tmp_path, [snapshot(2019, "2020-02-01", net_income=100.0),
                             snapshot(2019, "2021-02-01", net_income=90.0)])
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-12-31")
        assert (row["net_income"], row["accn"], row["filed_date"]) == (100.0, "acc-2020-02-01", "2020-02-01")
        assert r.as_of_annual("PRU", 2019, "2021-02-01")["net_income"] == 90.0


def test_not_yet_filed_returns_none(tmp_path):
    with AsOfReader(_pru(tmp_path)) as r:
        # Before the original filing, FY2019 was not yet known.
        assert r.as_of_annual("PRU", 2019, "2020-02-14") is None


def test_boundary_is_inclusive(tmp_path):
    with AsOfReader(_pru(tmp_path)) as r:
        # On the exact filing date, the original is visible.
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row is not None
        assert row["net_income"] == 100.0
        assert row["accn"] == "a-orig"


def test_restatement_switch(tmp_path):
    with AsOfReader(_pru(tmp_path)) as r:
        # Between the two filings: still the original.
        assert r.as_of_annual("PRU", 2019, "2020-06-30")["net_income"] == 100.0
        # On/after the restatement: the restated value.
        assert r.as_of_annual("PRU", 2019, "2021-02-15")["net_income"] == 90.0
        assert r.as_of_annual("PRU", 2019, "2025-01-01")["net_income"] == 90.0


def test_row_carries_provenance_metadata(tmp_path):
    with AsOfReader(_pru(tmp_path)) as r:
        row = r.as_of_annual("PRU", 2019, "2020-06-30")
    assert (row["ticker"], row["fiscal_year"], row["accn"], row["filed_date"], row["period_end"], row["form"],
            row["calendar_year"], row["carried_from"]) == (
        "PRU", 2019, "a-orig", "2020-02-15", "2019-12-31", "10-K", 2019, {})


def test_the_period_keeps_its_shape(tmp_path):
    # The API, the web fragment and the ratios read the shape the vintages gave: the canonical figures, the latest
    # filing's provenance and carried_from, nothing of the snapshot's own bookkeeping.
    with AsOfReader(_pru(tmp_path)) as r:
        row = r.as_of_annual("PRU", 2019, "2025-01-01")
    assert set(row) == {"ticker", "fiscal_year", "accn", "filed_date", "period_end", "form", "calendar_year",
                        "carried_from", *_CANONICAL_COLUMNS}
    assert row["total_assets"] is None  # a figure the year doesn't hold


def test_accepts_date_object(tmp_path):
    from datetime import datetime
    with AsOfReader(_pru(tmp_path)) as r:
        assert r.as_of_annual("PRU", 2019, date(2020, 6, 30))["net_income"] == 100.0
        assert r.as_of_annual("PRU", 2019, datetime(2021, 2, 15, 23, 59))["net_income"] == 90.0


def test_unknown_ticker_or_year_returns_none(tmp_path):
    with AsOfReader(_pru(tmp_path)) as r:
        assert r.as_of_annual("ZZZZ", 2019, "2025-01-01") is None
        assert r.as_of_annual("PRU", 1990, "2025-01-01") is None


def test_as_of_value_delegates(tmp_path):
    with AsOfReader(_pru(tmp_path)) as r:
        assert r.as_of_value("PRU", 2019, "net_income", "2020-06-30") == 100.0
        assert r.as_of_value("PRU", 2019, "net_income", "2021-02-15") == 90.0
        # Not yet filed → None passthrough.
        assert r.as_of_value("PRU", 2019, "net_income", "2019-01-01") is None
        # Missing field → None.
        assert r.as_of_value("PRU", 2019, "no_such_field", "2025-01-01") is None


def test_a_tombstone_hides_the_year(tmp_path):
    # A year that left the record on a day (BUG-113: a view of a year not over yet) reads as not known from that day,
    # and as known again from a later row.
    db = build_db(tmp_path, [snapshot(2011, "2011-02-25", net_income=5.0), snapshot(2010, "2011-02-25", net_income=4.0),
                             tombstone(2011, "2011-05-05"), snapshot(2011, "2012-02-23", net_income=6.0)])
    with AsOfReader(db) as r:
        assert r.as_of_annual("PRU", 2011, "2011-03-01")["net_income"] == 5.0
        assert r.as_of_annual("PRU", 2011, "2011-05-05") is None
        assert r.as_of_value("PRU", 2011, "net_income", "2012-01-01") is None
        assert list(r.history_as_of("PRU", "2012-01-01")) == [2010]
        # a tombstoned year doesn't take a slot of years_back
        assert list(r.history_as_of("PRU", "2012-01-01", years_back=1)) == [2010]
        assert r.as_of_annual("PRU", 2011, "2012-02-23")["net_income"] == 6.0
        assert list(r.history_as_of("PRU", "2012-02-23")) == [2011, 2010]


def test_carried_from_names_the_earlier_filings_figures(tmp_path):
    # MSFT's FY2016 as of its FY2019 10-K, which holds only the opening equity: the income statement is the FY2018
    # 10-K's, the balance sheet the FY2017 10-K's (BUG-110), and the record names each figure's filing.
    k18, k17 = ("k18", "2018-08-03", "10-K"), ("k17", "2017-08-02", "10-K/A")
    db = build_db(tmp_path, [snapshot(2016, "2019-08-01", accn="k19", period_end="2016-06-30", revenue=91.2e9,
                                      net_income=20.5e9, total_assets=193.5e9, total_equity=83.1e9,
                                      earlier={"revenue": k18, "net_income": k18, "total_assets": k17})],
                  ticker="MSFT")
    with AsOfReader(db) as r:
        row = r.as_of_annual("MSFT", 2016, "2026-01-01")
    assert (row["revenue"], row["total_assets"], row["total_equity"]) == (91.2e9, 193.5e9, 83.1e9)
    assert (row["accn"], row["filed_date"], row["form"]) == ("k19", "2019-08-01", "10-K")  # the latest filing's
    restated = {"accn": "k18", "filed_date": "2018-08-03", "form": "10-K"}
    assert row["carried_from"] == {"revenue": restated, "net_income": restated,
                                   "total_assets": {"accn": "k17", "filed_date": "2017-08-02", "form": "10-K/A"}}


def test_a_database_without_the_table_reads_none(tmp_path):
    # A database collected before the snapshots: no table, so nothing is known as of any day (no fallback).
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE companies (ticker TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    with AsOfReader(db) as r:
        assert r.as_of_annual("PRU", 2019, "2025-01-01") is None
        assert r.as_of_value("PRU", 2019, "net_income", "2025-01-01") is None
        assert r.history_as_of("PRU", "2025-01-01") == {}
        assert r.has_snapshots("PRU") is False


def test_has_snapshots_is_whether_the_ticker_has_rows(tmp_path):
    db = build_db(tmp_path, [_ORIGINAL])
    with AsOfReader(db) as r:
        assert r.has_snapshots("PRU") is True
        assert r.has_snapshots("ZZZZ") is False


# ---------------------------------------------------------------------------
# history_as_of
# ---------------------------------------------------------------------------

def _three_years(tmp_path):
    return build_db(tmp_path, [snapshot(2018, "2019-02-15", accn="k18", net_income=80.0),
                               snapshot(2019, "2020-02-15", accn="k19", net_income=100.0),
                               snapshot(2019, "2021-02-15", accn="k19r", net_income=90.0),
                               snapshot(2020, "2021-02-15", accn="k20", net_income=110.0)])


def test_history_as_of_only_includes_filed_years(tmp_path):
    with AsOfReader(_three_years(tmp_path)) as r:
        # As of mid-2020, FY2020 has not been filed yet (filed 2021-02-15).
        hist = r.history_as_of("PRU", "2020-06-30")
        assert set(hist.keys()) == {2018, 2019}
        assert hist[2019]["net_income"] == 100.0  # pre-restatement
        assert hist[2018]["net_income"] == 80.0


def test_history_as_of_reflects_restatement_and_full_set(tmp_path):
    with AsOfReader(_three_years(tmp_path)) as r:
        hist = r.history_as_of("PRU", "2021-02-15")
        assert set(hist.keys()) == {2018, 2019, 2020}
        assert hist[2019]["net_income"] == 90.0  # restated by this date
        assert hist[2020]["net_income"] == 110.0


def test_history_as_of_newest_first_and_years_back(tmp_path):
    with AsOfReader(_three_years(tmp_path)) as r:
        hist = r.history_as_of("PRU", "2021-02-15", years_back=2)
        assert list(hist.keys()) == [2020, 2019]  # newest first, trimmed to 2


def test_history_as_of_years_back_zero_or_negative_trims_nothing(tmp_path):
    with AsOfReader(_three_years(tmp_path)) as r:
        for years_back in (0, -1):
            assert list(r.history_as_of("PRU", "2021-02-15", years_back=years_back)) == [2020, 2019, 2018]


def test_history_as_of_unknown_ticker_empty(tmp_path):
    with AsOfReader(_three_years(tmp_path)) as r:
        assert r.history_as_of("ZZZZ", "2025-01-01") == {}


def test_a_database_under_a_hash_folder_opens(tmp_path):
    # BUG-080: a '#' in the path cut `?mode=ro` off, and the reader opened, and created, an empty database at "proj".
    (tmp_path / "proj#1").mkdir()
    db = build_db(tmp_path / "proj#1", [_ORIGINAL])
    with AsOfReader(db) as reader:
        assert reader.as_of_value("PRU", 2019, "net_income", "2020-03-01") == 100.0
    assert [p.name for p in tmp_path.iterdir()] == ["proj#1"]
