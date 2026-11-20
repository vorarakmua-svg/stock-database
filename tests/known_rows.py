"""The annual record as known at each filing date (``financials_annual_known``) for the as-of tests: rows as
``build_snapshots`` gives them, and a database the store writes them to."""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData


def snapshot(fiscal_year: int, known_from: str, *, accn: Optional[str] = None, form: str = "10-K",
             earlier: Optional[Dict[str, Tuple[str, str, str]]] = None, period_end: Optional[str] = None,
             **figures: float) -> Dict[str, Any]:
    """*fiscal_year* as known from *known_from*: its *figures*, each read from the filing *accn* (``acc-<day>`` by
    default) made that day, but those *earlier* names (``{field: (accn, filed, form)}``), read from an earlier one.
    The row's ``accn``/``filed_date``/``form`` are its latest filing's, as ``build_snapshots`` gives them."""
    own = {"accn": accn or f"acc-{known_from}", "filed": known_from, "form": form}
    filings = {key: dict(own) for key in figures}
    for key, (other, filed, other_form) in (earlier or {}).items():
        filings[key] = {"accn": other, "filed": filed, "form": other_form}
    latest = max(filings.values(), key=lambda f: (f["filed"], f["accn"])) if filings else own
    return {"fiscal_year": fiscal_year, "known_from": known_from, "known_accn": own["accn"], "present": True,
            **figures, "source_tags": {key: "Tag" for key in figures}, "source_filings": filings,
            "period_end": period_end or f"{fiscal_year}-12-31", "calendar_year": fiscal_year,
            "frame": f"CY{fiscal_year}", "accn": latest["accn"], "filed_date": latest["filed"], "form": latest["form"]}


def tombstone(fiscal_year: int, known_from: str) -> Dict[str, Any]:
    """The row that says *fiscal_year* left the record on *known_from*."""
    return {"fiscal_year": fiscal_year, "known_from": known_from, "known_accn": f"acc-{known_from}",
            "present": False}


def build_db(folder: Path, rows: Optional[List[Dict[str, Any]]], ticker: str = "PRU",
             vintages: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None, **attrs: Any) -> str:
    """A database the store writes *ticker*'s snapshot *rows* (None: a run that built none) and *vintages* to, with
    the StockData *attrs* (``sector_class``); its path."""
    stock = StockData(ticker=ticker, cik="1", company_name=f"{ticker} Inc.")
    for key, value in attrs.items():
        setattr(stock, key, value)
    stock.financials_annual_known = rows
    stock.financials_annual_vintages = vintages or {}
    db = folder / "stock.db"
    SQLiteStore(db_path=db).export([stock])
    return str(db)
