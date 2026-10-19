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

