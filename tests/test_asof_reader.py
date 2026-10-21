"""Tests for the point-in-time as-of-date query API (no look-ahead)."""

import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.query.asof import AsOfReader


def _build_db(tmp_path, vintages, ticker="PRU"):
    """Write a StockData carrying the given annual vintages and return the db path.

    vintages: {fy_str: {accn: period_dict}} exactly as StockData.financials_annual_vintages.
    """
    s = StockData(ticker=ticker, cik="1", company_name="Prudential")
    s.financials_annual_vintages = vintages
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([s])
    return str(db)


def test_reader_connection_is_read_only(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    reader = AsOfReader(db)
    with pytest.raises(sqlite3.OperationalError):
        reader._conn.execute(
            "INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn) "
            "VALUES ('X', 1, 'y')"
        )
    reader.close()


def test_reader_context_manager_closes(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    with AsOfReader(db) as reader:
        assert reader._conn is not None
    # After exit, using the closed connection raises.
    with pytest.raises(sqlite3.ProgrammingError):
        reader._conn.execute("SELECT 1")


def _pru_two_vintages(tmp_path):
    return _build_db(tmp_path, {
        "2019": {
            "a-orig": {"fiscal_year": 2019, "accn": "a-orig", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "revenue": 1000.0},
            "b-restate": {"fiscal_year": 2019, "accn": "b-restate", "filed_date": "2021-02-15",
                          "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                          "net_income": 90.0, "revenue": 1000.0},
        },
    })


def test_not_yet_filed_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Before the original filing, FY2019 was not yet known.
        assert r.as_of_annual("PRU", 2019, "2020-02-14") is None


def test_boundary_is_inclusive(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # On the exact filing date, the original is visible.
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row is not None
        assert row["net_income"] == 100.0
        assert row["accn"] == "a-orig"


def test_restatement_switch(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Between the two filings: still the original.
        assert r.as_of_annual("PRU", 2019, "2020-06-30")["net_income"] == 100.0
        # On/after the restatement: the restated value.
        assert r.as_of_annual("PRU", 2019, "2021-02-15")["net_income"] == 90.0
        assert r.as_of_annual("PRU", 2019, "2025-01-01")["net_income"] == 90.0


def test_row_carries_provenance_metadata(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-06-30")
        assert row["fiscal_year"] == 2019
        assert row["accn"] == "a-orig"
        assert row["filed_date"] == "2020-02-15"
        assert row["period_end"] == "2019-12-31"
        assert row["form"] == "10-K"


def test_accepts_date_object(tmp_path):
    from datetime import date
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("PRU", 2019, date(2020, 6, 30))["net_income"] == 100.0


def test_same_day_tie_break_prefers_higher_accn(tmp_path):
    # Two filings on the same date: deterministic accn DESC tie-break.
    db = _build_db(tmp_path, {
        "2019": {
            "0001-10K": {"fiscal_year": 2019, "accn": "0001-10K", "filed_date": "2020-02-15",
                         "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                         "net_income": 100.0},
            "0002-10KA": {"fiscal_year": 2019, "accn": "0002-10KA", "filed_date": "2020-02-15",
                          "period_end": "2019-12-31", "form": "10-K/A", "calendar_year": 2019,
                          "net_income": 105.0},
        },
    })
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row["accn"] == "0002-10KA"
        assert row["net_income"] == 105.0


def test_unknown_ticker_or_year_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("ZZZZ", 2019, "2025-01-01") is None
        assert r.as_of_annual("PRU", 1990, "2025-01-01") is None


def test_as_of_value_delegates(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2019, "net_income", "2020-06-30") == 100.0
        assert r.as_of_value("PRU", 2019, "net_income", "2021-02-15") == 90.0
        # Not yet filed → None passthrough.
        assert r.as_of_value("PRU", 2019, "net_income", "2019-01-01") is None
        # Missing field → None.
        assert r.as_of_value("PRU", 2019, "no_such_field", "2025-01-01") is None


# ---------------------------------------------------------------------------
# Task 3: history_as_of
# ---------------------------------------------------------------------------

def _three_years(tmp_path):
    def period(fy, accn, filed, ni):
        return {"fiscal_year": fy, "accn": accn, "filed_date": filed,
                "period_end": f"{fy}-12-31", "form": "10-K", "calendar_year": fy,
                "net_income": ni}
    return _build_db(tmp_path, {
        "2018": {"k18": period(2018, "k18", "2019-02-15", 80.0)},
        "2019": {"k19": period(2019, "k19", "2020-02-15", 100.0),
                 "k19r": period(2019, "k19r", "2021-02-15", 90.0)},
        "2020": {"k20": period(2020, "k20", "2021-02-15", 110.0)},
    })


def test_history_as_of_only_includes_filed_years(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        # As of mid-2020, FY2020 has not been filed yet (filed 2021-02-15).
        hist = r.history_as_of("PRU", "2020-06-30")
        assert set(hist.keys()) == {2018, 2019}
        assert hist[2019]["net_income"] == 100.0  # pre-restatement
        assert hist[2018]["net_income"] == 80.0


def test_history_as_of_reflects_restatement_and_full_set(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        hist = r.history_as_of("PRU", "2021-02-15")
        assert set(hist.keys()) == {2018, 2019, 2020}
        assert hist[2019]["net_income"] == 90.0  # restated by this date
        assert hist[2020]["net_income"] == 110.0


def test_history_as_of_newest_first_and_years_back(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        hist = r.history_as_of("PRU", "2021-02-15", years_back=2)
        assert list(hist.keys()) == [2020, 2019]  # newest first, trimmed to 2


def test_history_as_of_unknown_ticker_empty(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        assert r.history_as_of("ZZZZ", "2025-01-01") == {}


def test_a_database_under_a_hash_folder_opens(tmp_path):
    # BUG-080: a '#' in the path cut `?mode=ro` off, and the reader opened, and created, an empty database at "proj".
    (tmp_path / "proj#1").mkdir()
    db = _build_db(tmp_path / "proj#1", {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    with AsOfReader(db) as reader:
        assert reader.as_of_value("PRU", 2019, "net_income", "2020-03-01") == 100.0
    assert [p.name for p in tmp_path.iterdir()] == ["proj#1"]


# ---------------------------------------------------------------------------
# BUG-110: a later 10-K repeats only part of a year
# ---------------------------------------------------------------------------

def _partial_refilings(tmp_path):
    """FY2016 as MSFT's 10-Ks file it: whole in its own 10-K and the next one's comparatives, then the
    FY2018 10-K restating the income statement without the balance sheet, then the FY2019 10-K with only
    the equity statement's opening balance."""
    def period(accn, filed, **figures):
        return {"fiscal_year": 2016, "accn": accn, "filed_date": filed, "period_end": "2016-06-30",
                "form": "10-K", "calendar_year": 2016, **figures}
    return _build_db(tmp_path, {"2016": {
        "k16": period("k16", "2016-07-28", revenue=85.3e9, net_income=16.8e9, total_assets=193.7e9,
                      total_equity=72.0e9),
        "k17": period("k17", "2017-08-02", revenue=85.3e9, net_income=16.8e9, total_assets=193.5e9,
                      total_equity=72.0e9),
        "k18": period("k18", "2018-08-03", revenue=91.2e9, net_income=20.5e9, total_equity=83.1e9),
        "k19": period("k19", "2019-08-01", total_equity=83.1e9),
    }}, ticker="MSFT")


def test_a_figure_a_later_filing_leaves_out_keeps_its_last_filed_value(tmp_path):
    # BUG-110: the latest filing, holding only the opening equity, came back whole: no revenue as of today.
    db = _partial_refilings(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("MSFT", 2016, "2026-01-01")
    assert (row["revenue"], row["net_income"], row["total_assets"], row["total_equity"]) == (
        91.2e9, 20.5e9, 193.5e9, 83.1e9)
    assert (row["accn"], row["filed_date"]) == ("k19", "2019-08-01")  # the latest filing's provenance
    restated = {"accn": "k18", "filed_date": "2018-08-03", "form": "10-K"}
    assert row["carried_from"] == {"revenue": restated, "net_income": restated,
                                   "total_assets": {"accn": "k17", "filed_date": "2017-08-02", "form": "10-K"}}


def test_a_figure_filed_after_the_date_is_never_carried(tmp_path):
    db = _partial_refilings(tmp_path)
    with AsOfReader(db) as r:
        before = r.as_of_annual("MSFT", 2016, "2018-08-02")
        after = r.as_of_annual("MSFT", 2016, "2018-08-03")
    assert (before["revenue"], before["total_assets"], before["accn"], before["carried_from"]) == (
        85.3e9, 193.5e9, "k17", {})
    assert (after["revenue"], after["total_assets"], after["accn"]) == (91.2e9, 193.5e9, "k18")
    assert after["carried_from"] == {"total_assets": {"accn": "k17", "filed_date": "2017-08-02", "form": "10-K"}}


def test_as_of_value_and_history_read_each_figure_as_last_filed(tmp_path):
    db = _partial_refilings(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_value("MSFT", 2016, "revenue", "2026-01-01") == 91.2e9
        assert r.history_as_of("MSFT", "2026-01-01")[2016]["total_assets"] == 193.5e9

