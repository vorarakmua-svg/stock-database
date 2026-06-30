"""Tests for point-in-time metrics (ratios computed on as-of-date financials)."""

import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.query.pit_metrics import PointInTimeMetrics


def _build_db(tmp_path, vintages, ticker="PRU", sector_class=None):
    """Write a StockData (vintages + optional sector_class) and return the db path.

    vintages: {fy_str: {accn: period_dict}} as StockData.financials_annual_vintages.
    """
    s = StockData(ticker=ticker, cik="1", company_name="Co")
    if sector_class is not None:
        s.sector_class = sector_class
    s.financials_annual_vintages = vintages
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([s])
    return str(db)


_ONE_VINTAGE = {
    "2019": {"a": {"fiscal_year": 2019, "accn": "a", "filed_date": "2020-02-15",
                   "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                   "net_income": 100.0, "total_equity": 1000.0, "revenue": 1000.0}},
}


def test_sector_auto_read_from_companies(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE, sector_class="bank")
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm._sector("PRU") == "bank"


def test_sector_none_when_absent_or_unknown(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE)  # no sector_class set
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm._sector("PRU") is None       # column is NULL
        assert pm._sector("ZZZZ") is None       # no such company row


def test_from_path_builds_reader_and_closes(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE)
    pm = PointInTimeMetrics.from_path(db)
    assert pm.reader is not None
    pm.close()
    # After close, the reader's connection is unusable.
    with pytest.raises(sqlite3.ProgrammingError):
        pm.reader.conn.execute("SELECT 1")


# ---------------------------------------------------------------------------
# Task 2: metrics_as_of + metric_as_of
# ---------------------------------------------------------------------------

_RESTATED = {
    "2019": {
        "orig": {"fiscal_year": 2019, "accn": "orig", "filed_date": "2020-02-15",
                 "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                 "net_income": 100.0, "total_equity": 1000.0, "revenue": 1000.0},
        "restate": {"fiscal_year": 2019, "accn": "restate", "filed_date": "2021-02-15",
                    "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                    "net_income": 90.0, "total_equity": 1000.0, "revenue": 1000.0},
    },
}


def test_metric_is_point_in_time(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        # Between the two filings: ROE from the original (100/1000).
        assert pm.metric_as_of("PRU", 2019, "roe", "2020-06-30") == 0.10
        # On/after the restatement: ROE from the restated value (90/1000).
        assert pm.metric_as_of("PRU", 2019, "roe", "2021-02-15") == 0.09


def test_not_yet_filed_returns_none(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm.metrics_as_of("PRU", 2019, "2020-02-14") is None
        assert pm.metric_as_of("PRU", 2019, "roe", "2020-02-14") is None


def test_unknown_ticker_or_year_returns_none(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm.metrics_as_of("ZZZZ", 2019, "2025-01-01") is None
        assert pm.metrics_as_of("PRU", 1990, "2025-01-01") is None


def test_valuation_ratios_excluded(tmp_path):
    db = _build_db(tmp_path, _RESTATED)
    with PointInTimeMetrics.from_path(db) as pm:
        m = pm.metrics_as_of("PRU", 2019, "2020-06-30")
        for k in ("enterprise_value", "ev_to_ebitda", "ev_to_revenue",
                  "ev_to_fcf", "fcf_yield"):
            assert k not in m
        # Fundamental ratios ARE present.
        assert m["roe"] == 0.10
        assert m["net_margin"] == 0.10


def test_sector_auto_applies_bank_ratios(tmp_path):
    # A bank vintage: net_interest_income + total_assets -> net_interest_margin computed,
    # and generic roic suppressed (set to None) by the sector overlay.
    bank_vintage = {
        "2019": {"a": {"fiscal_year": 2019, "accn": "a", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "total_equity": 1000.0,
                       "net_interest_income": 50.0, "total_assets": 2000.0}},
    }
    db = _build_db(tmp_path, bank_vintage, ticker="JPM", sector_class="bank")
    with PointInTimeMetrics.from_path(db) as pm:
        m = pm.metrics_as_of("JPM", 2019, "2020-06-30")
        assert m["net_interest_margin"] == 50.0 / 2000.0   # bank ratio present
        assert m["roic"] is None                            # generic ratio suppressed


def test_explicit_sector_overrides_table(tmp_path):
    # Table says bank, but the caller overrides with "general": no bank ratios added.
    bank_vintage = {
        "2019": {"a": {"fiscal_year": 2019, "accn": "a", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "total_equity": 1000.0,
                       "net_interest_income": 50.0, "total_assets": 2000.0}},
    }
    db = _build_db(tmp_path, bank_vintage, ticker="JPM", sector_class="bank")
    with PointInTimeMetrics.from_path(db) as pm:
        m = pm.metrics_as_of("JPM", 2019, "2020-06-30", sector="general")
        assert "net_interest_margin" not in m   # override won; generic suite only


# ---------------------------------------------------------------------------
# Task 3: metrics_history_as_of
# ---------------------------------------------------------------------------

def _three_years(tmp_path):
    def period(fy, accn, filed, ni):
        return {"fiscal_year": fy, "accn": accn, "filed_date": filed,
                "period_end": f"{fy}-12-31", "form": "10-K", "calendar_year": fy,
                "net_income": ni, "total_equity": 1000.0, "revenue": 1000.0}
    return _build_db(tmp_path, {
        "2018": {"k18": period(2018, "k18", "2019-02-15", 80.0)},
        "2019": {"k19": period(2019, "k19", "2020-02-15", 100.0),
                 "k19r": period(2019, "k19r", "2021-02-15", 90.0)},
        "2020": {"k20": period(2020, "k20", "2021-02-15", 110.0)},
    })


def test_history_metrics_only_filed_years_and_pit(tmp_path):
    db = _three_years(tmp_path)
    with PointInTimeMetrics.from_path(db) as pm:
        hist = pm.metrics_history_as_of("PRU", "2020-06-30")
        assert set(hist.keys()) == {2018, 2019}          # 2020 not yet filed
        assert hist[2019]["roe"] == 0.10                  # pre-restatement
        assert hist[2018]["roe"] == 0.08


def test_history_metrics_newest_first_and_years_back(tmp_path):
    db = _three_years(tmp_path)
    with PointInTimeMetrics.from_path(db) as pm:
        hist = pm.metrics_history_as_of("PRU", "2021-02-15", years_back=2)
        assert list(hist.keys()) == [2020, 2019]          # newest first, trimmed
        assert hist[2019]["roe"] == 0.09                  # restated by this date


def test_history_metrics_unknown_ticker_empty(tmp_path):
    db = _three_years(tmp_path)
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm.metrics_history_as_of("ZZZZ", "2025-01-01") == {}


class _RaisingCalculator:
    """Stub calculator that always raises — to exercise per-year error capture."""

    def calculate_all(self, financials, market_data=None, valuation=None, sector=None):
        raise ValueError("boom")


def test_history_metrics_captures_per_year_errors(tmp_path):
    from src.query.asof import AsOfReader
    db = _three_years(tmp_path)
    pm = PointInTimeMetrics(AsOfReader(db), calculator=_RaisingCalculator())
    hist = pm.metrics_history_as_of("PRU", "2021-02-15")
    pm.close()
    assert set(hist.keys()) == {2018, 2019, 2020}
    for fy in (2018, 2019, 2020):
        assert hist[fy] == {"error": "boom"}              # captured, not raised
