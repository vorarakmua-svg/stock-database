"""The JSON and CSV exports after a failed or partial run agree with what SQLite keeps.

A failed run never stamps old data with its own time (BUG-030), never blanks a value the database keeps
(BUG-038), and a run that can't speak for the stored years (a successor's whose predecessor's filings
weren't read, BUG-179) only fills the exports, as it only fills the database (BUG-278).
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.exporters.csv_exporter import CSVExporter
from src.exporters.json_exporter import JSONExporter
from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.parsers.calculated_metrics import CalculatedMetrics

T1, T2, T3, T4 = (datetime(2026, 11, day, 9, 0) for day in (2, 3, 4, 5))


def _stores(tmp_path: Path) -> tuple:
    return (JSONExporter(tmp_path / "json"), CSVExporter(tmp_path / "csv"),
            SQLiteStore(db_path=tmp_path / "stock.db"))


def _export(stores: tuple, stock: StockData) -> None:
    json_store, csv_store, sqlite_store = stores
    json_store.export([stock])
    csv_store.export([stock])
    csv_store.export_financial_history([stock])
    sqlite_store.export([stock])


def _json(tmp_path: Path) -> Dict[str, Any]:
    return json.loads((tmp_path / "json" / "AAA.json").read_text(encoding="utf-8"))


def _rows(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _db(tmp_path: Path, sql: str) -> List[tuple]:
    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _num(text: str) -> Optional[float]:
    return float(text) if text != "" else None


# ---- BUG-030: kept data keeps its own time ----


def test_price_statistics_a_failed_run_kept_are_filed_under_the_time_they_were_collected(tmp_path: Path) -> None:
    # A failed run keeps the earlier price_history (BUG-018), and the next good run's merge moved it into
    # price_history_snapshots labelled with the failed run's collected_at.
    exporter = JSONExporter(output_dir=tmp_path / "json")
    good = StockData(ticker="AAA", company_name="AAA Inc.", collected_at=T1,
                     market_data={"current_price": 50.0}, price_history={"cagr": 0.12, "annual_volatility": 0.2},
                     financials_annual={"2025": {"revenue": 1.0}}, data_quality={"score": 100})
    exporter.export(good)
    for at in (T2, T3):
        exporter.export(StockData(ticker="AAA", collected_at=at, errors=["Yahoo Finance: HTTP 429"],
                                  data_quality={"score": 0, "findings": [{"code": "no_financials"}]}))
        data = _json(tmp_path)
        assert data["collected_at"] == at.isoformat()
        assert data["price_history"] == {"cagr": 0.12, "annual_volatility": 0.2}
        assert data["data_quality"] == {"score": 100}
        assert data["section_collected_at"] == {key: T1.isoformat() for key in (
            "company_name", "market_data", "price_history", "financials_annual", "data_quality")}
    exporter.export(StockData(ticker="AAA", company_name="AAA Inc.", collected_at=T4,
                              market_data={"current_price": 55.0}, price_history={"cagr": 0.15},
                              financials_annual={"2025": {"revenue": 1.0}}, data_quality={"score": 100}))
    data = _json(tmp_path)
    assert data["price_history_snapshots"][-1] == {"collected_at": T1.isoformat(), "cagr": 0.12,
                                                   "annual_volatility": 0.2}
    assert "section_collected_at" not in data


# ---- BUG-038: a run without a price keeps the last quote and EV, as SQLite's latest snapshot does ----


def _priced_run(at: datetime) -> StockData:
    stock = StockData(ticker="AAA", company_name="AAA Inc.", collected_at=at)
    stock.market_data = {"current_price": 50.0, "market_cap": 5.0e9, "beta": 1.1}
    stock.valuation = {"pe_trailing": 20.0, "price_to_book": 3.0}
    stock.calculated_metrics = {"ebitda": 4.0e8, "net_debt": 1.0e9, "enterprise_value": 6.0e9, "ev_to_ebitda": 15.0,
                                "ev_to_revenue": 3.0, "ev_to_fcf": 30.0, "fcf_yield": 0.04}
    stock.risk_free_rate = {"risk_free_rate": 0.042, "source": "fred", "date": "2026-11-01"}
    return stock


@pytest.mark.parametrize("market_data, valuation", [({}, {}), ({"current_price": None, "ma_50": 48.0},
                                                                 {"pe_trailing": None, "price_to_book": None})],
                         ids=["yahoo failed", "quote unavailable"])
def test_a_run_without_a_price_keeps_the_last_quote_and_ev_in_every_store(
        tmp_path: Path, market_data: Dict[str, Any], valuation: Dict[str, Any]) -> None:
    # A run with Yahoo failing (or --no-yahoo) computes its metrics without a market cap, so it has no EV. SQLite
    # writes no snapshot without a price (BUG-018, BUG-033) and keeps the last EV; the exports blanked it.
    stores = _stores(tmp_path)
    _export(stores, _priced_run(T1))
    failed = StockData(ticker="AAA", company_name="AAA Inc.", collected_at=T2,
                       market_data=dict(market_data), valuation=dict(valuation),
                       calculated_metrics={"ebitda": 4.2e8, "net_debt": 0.9e9},
                       risk_free_rate={"risk_free_rate": 0.043, "source": "fred", "date": "2026-11-02"})
    _export(stores, failed)

    assert _db(tmp_path, "SELECT current_price, pe_trailing, enterprise_value, ev_to_ebitda, ev_to_fcf, fcf_yield "
                         "FROM market_snapshots ORDER BY collected_at DESC LIMIT 1") == [(50.0, 20.0, 6.0e9, 15.0,
                                                                                           30.0, 0.04)]
    data = _json(tmp_path)
    metrics = data["calculated_metrics"]
    assert {k: metrics[k] for k in ("enterprise_value", "ev_to_ebitda", "ev_to_revenue", "ev_to_fcf",
                                    "fcf_yield")} == {"enterprise_value": 6.0e9, "ev_to_ebitda": 15.0,
                                                      "ev_to_revenue": 3.0, "ev_to_fcf": 30.0, "fcf_yield": 0.04}
    assert (metrics["ebitda"], metrics["net_debt"]) == (4.2e8, 0.9e9)  # the run's own figures
    assert data["market_data"] == {"current_price": 50.0, "market_cap": 5.0e9, "beta": 1.1}
    assert data["valuation"] == {"pe_trailing": 20.0, "price_to_book": 3.0}
    assert data["section_collected_at"]["calculated_metrics.enterprise_value"] == T1.isoformat()
    assert data["section_collected_at"]["market_data"] == T1.isoformat()
    (row,) = _rows(tmp_path / "csv" / "summary.csv")
    assert (_num(row["calc_ev"]), _num(row["calc_ev_to_ebitda"])) == (6.0e9, 15.0)
    assert (_num(row["current_price"]), _num(row["market_cap"]), _num(row["pe_trailing"])) == (50.0, 5.0e9, 20.0)
    assert _num(row["calc_ebitda"]) == 4.2e8
    assert row["collected_at"] == T2.isoformat()


def test_a_priced_run_replaces_the_ev_even_when_it_has_none(tmp_path: Path) -> None:
    # Guard: a run with a price speaks for its quote; an EV it couldn't compute (no net debt; a bank's suppressed
    # ratios) is empty in every store, as its snapshot is.
    stores = _stores(tmp_path)
    _export(stores, _priced_run(T1))
    second = _priced_run(T2)
    second.calculated_metrics = {"ebitda": None, "enterprise_value": None, "ev_to_ebitda": None}
    _export(stores, second)
    assert _db(tmp_path, "SELECT enterprise_value, ev_to_ebitda FROM market_snapshots "
                         "ORDER BY collected_at DESC LIMIT 1") == [(None, None)]
    metrics = _json(tmp_path)["calculated_metrics"]
    assert (metrics["enterprise_value"], metrics["ev_to_ebitda"]) == (None, None)
    assert "ev_to_fcf" not in metrics
    (row,) = _rows(tmp_path / "csv" / "summary.csv")
    assert (row["calc_ev"], row["calc_ev_to_ebitda"]) == ("", "")


# ---- BUG-278: a run that couldn't read the predecessor only fills the exports, as it only fills the database ----

_GOOD_ANNUAL = {
    "2020": {"fiscal_year": 2020, "revenue": 90.0, "net_income": 9.0, "total_equity": 45.0, "total_assets": 180.0},
    "2021": {"fiscal_year": 2021, "revenue": 100.0, "net_income": 10.0, "total_equity": 50.0},
    "2022": {"fiscal_year": 2022, "revenue": 110.0, "net_income": 11.0, "total_equity": 55.0},
}
# The successor's own reports give the earlier years as their comparatives do: some figures from another tag
# (equity with the noncontrolling interests), others missing, a figure the earlier parse lacked.
_FILL_ANNUAL = {
    "2021": {"fiscal_year": 2021, "revenue": 100.0, "net_income": None, "total_equity": 52.0, "total_assets": 200.0},
    "2022": {"fiscal_year": 2022, "revenue": 110.0, "net_income": 11.0, "total_equity": 57.0},
    "2023": {"fiscal_year": 2023, "revenue": 120.0, "net_income": 12.0, "total_equity": 60.0},
}


def _sec_run(at: datetime, annual: Dict[str, Dict[str, Any]], predecessor_error: Optional[str] = None,
             quarterly: Optional[Dict[str, Dict[str, Any]]] = None,
             unmapped: Optional[List[Dict[str, Any]]] = None) -> StockData:
    stock = StockData(ticker="AAA", company_name="AAA Inc.", collected_at=at)
    stock.financials_annual = {year: dict(period) for year, period in annual.items()}
    stock.financials_quarterly = {end: dict(period) for end, period in (quarterly or {}).items()}
    stock.unmapped_facts = [dict(fact) for fact in unmapped or []]
    metrics: Dict[str, Any] = {"ebitda": 1.0}
    metrics["historical"] = CalculatedMetrics().calculate_historical(stock.financials_annual)
    stock.calculated_metrics = metrics
    stock.predecessor_error = predecessor_error
    return stock


def test_a_run_that_couldnt_read_the_predecessor_only_fills_the_exports_as_it_fills_the_database(
        tmp_path: Path) -> None:
    # BUG-179 made the store only fill from such a run; the JSON and financial_history.csv still took its partial
    # years (BLK.json would have lost 319 cells, the CSV 56, until the next good run).
    stores = _stores(tmp_path)
    _export(stores, _sec_run(T1, _GOOD_ANNUAL,
                             quarterly={"2022-12-31": {"fiscal_year": 2022, "revenue": 30.0, "net_income": 3.0}},
                             unmapped=[{"tag": "OldTag", "label": "Old", "value": 5.0, "period_end": "2022-12-31"}]))
    _export(stores, _sec_run(T2, _FILL_ANNUAL, predecessor_error="503 Server Error",
                             quarterly={"2022-12-31": {"fiscal_year": 2022, "revenue": 31.0, "net_income": None,
                                                       "total_assets": 210.0},
                                        "2023-03-31": {"fiscal_year": 2023, "revenue": 32.0}},
                             unmapped=[{"tag": "OldTag", "label": None, "value": 6.0, "period_end": "2023-03-31",
                                        "unit": "USD"},
                                       {"tag": "NewTag", "label": "New", "value": 7.0}]))

    expected = {
        2020: (90.0, 9.0, 45.0, 180.0),
        2021: (100.0, 10.0, 50.0, 200.0),   # stored figures kept; the gap filled
        2022: (110.0, 11.0, 55.0, None),
        2023: (120.0, 12.0, 60.0, None),    # a year not stored yet is added
    }
    assert {r[0]: r[1:] for r in _db(tmp_path, "SELECT fiscal_year, revenue, net_income, total_equity, total_assets "
                                               "FROM financials_annual WHERE ticker = 'AAA'")} == expected
    annual = _json(tmp_path)["financials_annual"]
    assert {int(y): (p.get("revenue"), p.get("net_income"), p.get("total_equity"), p.get("total_assets"))
            for y, p in annual.items()} == expected
    history = {int(r["fiscal_year"]): (_num(r["revenue"]), _num(r["net_income"]), _num(r["total_equity"]),
                                       _num(r["total_assets"]))
               for r in _rows(tmp_path / "csv" / "financial_history.csv")}
    assert history == expected
    (summary,) = _rows(tmp_path / "csv" / "summary.csv")
    assert (_num(summary["sec_revenue"]), summary["sec_fiscal_year"]) == (120.0, "2023")  # a later year is new

    quarter = _json(tmp_path)["financials_quarterly"]
    assert _db(tmp_path, "SELECT revenue, net_income, total_assets FROM financials_quarterly "
                         "WHERE period_end = '2022-12-31'") == [(30.0, 3.0, 210.0)]
    assert (quarter["2022-12-31"]["revenue"], quarter["2022-12-31"]["net_income"],
            quarter["2022-12-31"]["total_assets"]) == (30.0, 3.0, 210.0)
    assert quarter["2023-03-31"]["revenue"] == 32.0

    facts = {f["tag"]: f for f in _json(tmp_path)["unmapped_facts"]}
    assert _db(tmp_path, "SELECT tag, label, value, period_end, unit FROM unmapped_facts ORDER BY tag") == [
        ("NewTag", "New", 7.0, None, None), ("OldTag", "Old", 5.0, "2022-12-31", "USD")]
    assert {t: (f.get("label"), f.get("value"), f.get("period_end"), f.get("unit")) for t, f in facts.items()} == {
        "NewTag": ("New", 7.0, None, None), "OldTag": ("Old", 5.0, "2022-12-31", "USD")}

    # Each year's metrics are computed on the year as stored, as metrics_annual's are; a year the run didn't give
    # keeps its own.
    historical = _json(tmp_path)["calculated_metrics"]["historical"]
    stored_margins = dict(_db(tmp_path, "SELECT fiscal_year, net_margin FROM metrics_annual WHERE ticker = 'AAA'"))
    assert {int(y): m["net_margin"] for y, m in historical.items()} == stored_margins == {
        2020: 0.1, 2021: 0.1, 2022: 0.1, 2023: 0.1}


def test_a_run_that_couldnt_read_the_predecessor_keeps_the_summarys_stored_latest_year(tmp_path: Path) -> None:
    # summary.csv's sec_* columns are the latest year: the run's partial copy of a stored year only fills them, and a
    # record that ends before the stored latest year leaves them as they are.
    stores = _stores(tmp_path)
    _export(stores, _sec_run(T1, {"2022": _GOOD_ANNUAL["2022"]}))
    _export(stores, _sec_run(T2, {"2022": {"fiscal_year": 2022, "revenue": 111.0, "total_equity": None,
                                           "total_assets": 300.0}}, predecessor_error="404"))
    (row,) = _rows(tmp_path / "csv" / "summary.csv")
    assert _db(tmp_path, "SELECT revenue, net_income, total_equity, total_assets FROM financials_annual "
                         "WHERE fiscal_year = 2022") == [(110.0, 11.0, 55.0, 300.0)]
    assert (_num(row["sec_revenue"]), _num(row["sec_net_income"]), _num(row["sec_stockholders_equity"]),
            _num(row["sec_total_assets"]), row["sec_fiscal_year"]) == (110.0, 11.0, 55.0, 300.0, "2022")

    _export(stores, _sec_run(T3, {"2021": {"fiscal_year": 2021, "revenue": 99.0, "total_liabilities": 40.0}},
                             predecessor_error="404"))
    (row,) = _rows(tmp_path / "csv" / "summary.csv")
    assert (_num(row["sec_revenue"]), _num(row["sec_total_assets"]), row["sec_total_liabilities"],
            row["sec_fiscal_year"]) == (110.0, 300.0, "", "2022")


def test_a_run_that_reads_the_predecessor_again_restates_the_exports(tmp_path: Path) -> None:
    # Guard: a good run speaks for every year it gives, so it replaces them (BUG-029), in the exports as in the store.
    stores = _stores(tmp_path)
    _export(stores, _sec_run(T1, _GOOD_ANNUAL))
    _export(stores, _sec_run(T2, _FILL_ANNUAL))
    expected = (100.0, None, 52.0, 200.0)
    assert _db(tmp_path, "SELECT revenue, net_income, total_equity, total_assets FROM financials_annual "
                         "WHERE fiscal_year = 2021") == [expected]
    year = _json(tmp_path)["financials_annual"]["2021"]
    assert (year["revenue"], year["net_income"], year["total_equity"], year["total_assets"]) == expected
    (row,) = [r for r in _rows(tmp_path / "csv" / "financial_history.csv") if r["fiscal_year"] == "2021"]
    assert (_num(row["revenue"]), _num(row["net_income"]), _num(row["total_equity"]),
            _num(row["total_assets"])) == expected
    (summary,) = _rows(tmp_path / "csv" / "summary.csv")
    assert (_num(summary["sec_stockholders_equity"]), summary["sec_fiscal_year"]) == (60.0, "2023")
