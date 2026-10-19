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
