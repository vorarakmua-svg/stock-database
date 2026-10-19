"""The CSV exports (summary.csv, financial_history.csv) accumulate across collection runs.

Each run used to overwrite both files with only that run's tickers, so after a few batches the
"summary for all tickers" held just the last batch, and its columns depended on which tickers ran
(BUG-004 in qa/BUGS.md). The merge rules follow the JSON export's: the newest data for a ticker
(or ticker-year) replaces the old, and everything else is kept.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.exporters.csv_exporter import CSVExporter
from src.models.stock_data import StockData

CALC_COLUMNS = {"calc_ebitda", "calc_fcf", "calc_roic", "calc_net_debt", "calc_interest_coverage", "calc_ev",
                "calc_ev_to_ebitda"}


def _stock(ticker: str, price: float = 10.0, annual: Optional[Dict[str, Dict[str, Any]]] = None,
           metrics: bool = True) -> StockData:
    return StockData(
        ticker=ticker,
        company_name=f"{ticker} Inc.",
        market_data={"current_price": price},
        calculated_metrics={"ebitda": 2.0e9, "ev_to_ebitda": 12.5} if metrics else {},
        financials_annual=annual or {},
    )


def _rows(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_summary_keeps_tickers_from_earlier_runs(tmp_path: Path) -> None:
    exporter = CSVExporter(tmp_path)
    exporter.export([_stock("AAA"), _stock("BBB")])
    exporter.export([_stock("CCC")])
    assert [r["ticker"] for r in _rows(tmp_path / "summary.csv")] == ["AAA", "BBB", "CCC"]


def test_rerunning_a_ticker_replaces_its_summary_row(tmp_path: Path) -> None:
    exporter = CSVExporter(tmp_path)
    exporter.export([_stock("AAA", price=10.0), _stock("BBB")])
    exporter.export([_stock("AAA", price=12.0)])
    rows = _rows(tmp_path / "summary.csv")
    assert [r["ticker"] for r in rows] == ["AAA", "BBB"]
    assert rows[0]["current_price"] == "12.0000"


def test_summary_columns_do_not_depend_on_which_tickers_ran(tmp_path: Path) -> None:
    # A run of tickers without calculated metrics (an ETF, a failed ticker) must not drop the calc_* columns.
    CSVExporter(tmp_path).export([_stock("SPY", metrics=False)])
    with open(tmp_path / "summary.csv", newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert CALC_COLUMNS <= set(header)


def test_financial_history_keeps_tickers_from_earlier_runs(tmp_path: Path) -> None:
    exporter = CSVExporter(tmp_path)
    exporter.export_financial_history([_stock("AAA", annual={"2024": {"revenue": 1.0}})])
    exporter.export_financial_history([_stock("BBB", annual={"2024": {"revenue": 2.0}})])
    rows = _rows(tmp_path / "financial_history.csv")
    assert [(r["ticker"], r["fiscal_year"]) for r in rows] == [("AAA", "2024"), ("BBB", "2024")]


def test_financial_history_merges_years_like_the_json_export(tmp_path: Path) -> None:
    # Same rule as the JSON merge: a new year overwrites the same year, older years are kept.
    exporter = CSVExporter(tmp_path)
    exporter.export_financial_history([_stock("AAA", annual={"2023": {"revenue": 1.0}, "2024": {"revenue": 2.0}})])
    exporter.export_financial_history([_stock("AAA", annual={"2024": {"revenue": 5.0}, "2025": {"revenue": 6.0}})])
    rows = _rows(tmp_path / "financial_history.csv")
    assert [(r["fiscal_year"], r["revenue"]) for r in rows] == [("2023", "1.0000"), ("2024", "5.0000"),
                                                                ("2025", "6.0000")]


# ---- the merged files hold every earlier run, so no failure may take them down (BUG-015) ----


def test_summary_keeps_earlier_rows_after_excel_adds_a_byte_order_mark(tmp_path: Path) -> None:
    # Excel's "CSV UTF-8" save prepends a BOM. Read as plain UTF-8, the header became "\ufeffticker"
    # and every earlier row was dropped.
    exporter = CSVExporter(tmp_path)
    exporter.export([_stock("AAA"), _stock("BBB")])
    path = tmp_path / "summary.csv"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    exporter.export([_stock("CCC")])
    assert [r["ticker"] for r in _rows(path)] == ["AAA", "BBB", "CCC"]


def test_financial_history_keeps_earlier_rows_after_excel_adds_a_byte_order_mark(tmp_path: Path) -> None:
    exporter = CSVExporter(tmp_path)
    exporter.export_financial_history([_stock("AAA", annual={"2024": {"revenue": 1.0}})])
    path = tmp_path / "financial_history.csv"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    exporter.export_financial_history([_stock("BBB", annual={"2024": {"revenue": 2.0}})])
    assert [(r["ticker"], r["fiscal_year"]) for r in _rows(path)] == [("AAA", "2024"), ("BBB", "2024")]


def test_an_unreadable_summary_is_kept_aside_not_overwritten(tmp_path: Path) -> None:
    # An Excel ANSI save writes cp1252 ("Société" has byte 0xE9), which isn't UTF-8. The file holds
    # every earlier run, so it's moved aside for the user, not replaced by this run's rows alone.
    path = tmp_path / "summary.csv"
    original = "ticker,company_name\r\nAAA,Société Inc.\r\n".encode("cp1252")
    path.write_bytes(original)
    CSVExporter(tmp_path).export([_stock("CCC")])
    assert [b.read_bytes() for b in tmp_path.glob("summary.unreadable-*.csv")] == [original]
    assert [r["ticker"] for r in _rows(path)] == ["CCC"]


def test_a_failure_mid_write_leaves_the_old_summary_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = CSVExporter(tmp_path)
    exporter.export([_stock("AAA"), _stock("BBB")])
    path = tmp_path / "summary.csv"
    before = path.read_bytes()
    real_writerow = csv.DictWriter.writerow
    calls: List[Any] = []

    def failing_writerow(self: Any, row: Any) -> Any:  # the header goes through; the first data row fails
        calls.append(row)
        if len(calls) > 1:
            raise OSError("disk full")
        return real_writerow(self, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", failing_writerow)
    assert exporter.export([_stock("CCC")]) is None
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_recollection_keeps_the_earlier_values(tmp_path: Path) -> None:
    # Re-collecting AAA while Yahoo and SEC fail yields an almost empty summary, which used to replace
    # the good row. Values the run didn't get keep the earlier ones; what it did get (the error) wins.
    exporter = CSVExporter(tmp_path)
    exporter.export([_stock("AAA", price=12.5)])
    exporter.export([StockData(ticker="AAA", errors=["Yahoo Finance: HTTP 429 Too Many Requests"])])
    (row,) = _rows(tmp_path / "summary.csv")
    assert row["company_name"] == "AAA Inc."
    assert row["current_price"] == "12.5000"
    assert row["error_count"] == "1"


@pytest.mark.parametrize("rewrite", ["semicolons", "renamed header"])
def test_a_summary_without_a_ticker_column_is_kept_aside(tmp_path: Path, rewrite: str) -> None:
    # Excel saves CSV with ';' where that's the list separator (BOM included), and a header can be
    # renamed. Either way the file parses but has no `ticker` column, so every earlier row was dropped.
    exporter = CSVExporter(tmp_path)
    exporter.export([_stock("AAA"), _stock("BBB")])
    path = tmp_path / "summary.csv"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if rewrite == "semicolons":
        path.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(";".join(r) for r in rows).encode("utf-8") + b"\r\n")
    else:
        rows[0][0] = "Ticker"
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
    saved = path.read_bytes()
    exporter.export([_stock("CCC")])
    assert [kept.read_bytes() for kept in tmp_path.glob("summary.unreadable-*.csv")] == [saved]
    assert [r["ticker"] for r in _rows(path)] == ["CCC"]


def test_a_successful_recollection_clears_values_that_went_empty(tmp_path: Path) -> None:
    # Only a section the run didn't get keeps its earlier values. After a successful run in which the
    # company swung to a loss and suspended its dividend, the old P/E and yield used to stay beside the
    # new EPS, and FY2024's liabilities beside FY2025.
    exporter = CSVExporter(tmp_path)
    first = _stock("AAA", annual={"2024": {"fiscal_year": 2024, "revenue": 1e9, "total_liabilities": 5e8}})
    first.valuation = {"pe_trailing": 25.0, "dividend_yield": 0.021, "eps_trailing": 4.0}
    exporter.export([first])
    second = _stock("AAA", annual={"2025": {"fiscal_year": 2025, "revenue": 8e8}})
    second.valuation = {"pe_trailing": None, "dividend_yield": None, "eps_trailing": -1.5}
    exporter.export([second])
    (row,) = _rows(tmp_path / "summary.csv")
    assert (row["eps_trailing"], row["pe_trailing"], row["dividend_yield"]) == ("-1.5000", "", "")
    assert (row["sec_fiscal_year"], row["sec_total_liabilities"]) == ("2025", "")


def test_every_summary_column_belongs_to_a_section_the_identity_or_the_run() -> None:
    # The CSV merge decides per section; a new summary column without one would never be kept or cleared.
    from src.models.stock_data import SUMMARY_IDENTITY_COLUMNS, SUMMARY_RUN_COLUMNS, SUMMARY_SECTIONS
    mapped = [c for columns in SUMMARY_SECTIONS.values() for c in columns]
    mapped += list(SUMMARY_IDENTITY_COLUMNS) + list(SUMMARY_RUN_COLUMNS)
    assert sorted(mapped) == sorted(_stock("AAA").to_summary())


def test_company_fields_a_run_lacked_keep_their_earlier_values_in_every_store(tmp_path: Path) -> None:
    # Yahoo's info sometimes omits a few company fields. The JSON and SQLite keep each earlier value, field by
    # field; summary.csv replaced the whole company section and blanked them (a regression from 23fb062).
    import json
    import sqlite3

    from src.exporters.json_exporter import JSONExporter
    from src.exporters.sqlite_store import SQLiteStore

    stores = (JSONExporter(tmp_path / "json"), CSVExporter(tmp_path / "csv"),
              SQLiteStore(db_path=tmp_path / "stock.db"))
    full = {"sector": "Technology", "industry": "Software", "country": "United States",
            "full_time_employees": 1200, "website": "https://aaa.example"}
    for info in (full, {**full, "industry": None, "country": None, "full_time_employees": None, "website": None}):
        stock = _stock("AAA")
        stock.company_info = dict(info)
        for store in stores:
            store.export([stock])
    company = json.loads((tmp_path / "json" / "AAA.json").read_text(encoding="utf-8"))["company_info"]
    (row,) = _rows(tmp_path / "csv" / "summary.csv")
    conn = sqlite3.connect(tmp_path / "stock.db")
    db_row = conn.execute("SELECT industry, country, employees, website FROM companies WHERE ticker = 'AAA'").fetchone()
    conn.close()
    assert tuple(company[k] for k in ("industry", "country", "full_time_employees", "website")) == (
        "Software", "United States", 1200, "https://aaa.example")
    assert db_row == ("Software", "United States", 1200, "https://aaa.example")
    assert (row["industry"], row["country"], row["employees"], row["website"]) == (
        "Software", "United States", "1200", "https://aaa.example")

