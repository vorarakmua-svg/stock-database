"""Filling the companies' SIC codes offline (Milestone 5; docs/superpowers/specs/2026-10-25-universe-automation-
design.md, "SIC codes and industries").

    python -m src.universe.sic [--db PATH] [--json-dir DIR] [--cache-dir DIR]

Every collection stores a company's SEC SIC code and its description (``companies.sic``, ``sic_description``); this
fills them for the companies collected before, without a request:
- for each company without a code (``companies.sic`` NULL, or not a code), from its JSON export
  (``<json-dir>/<TICKER>.json``: ``sec_submissions.sic`` and ``sec_submissions.sic_description``), else from the SEC
  handler's cached submissions for its CIK (``<cache-dir>/submissions_CIK<10-digit CIK>.json``, the handler's own
  file, read whatever its age: no request and no handler);
- it writes those two columns only (adding them to a companies table from before them), never another;
- it prints the companies, those without a code, how many it filled from each source and each one still missing.

Exit 0, or 1 when the database or its companies table isn't there (a missing database is never created).
"""
import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ..config import StorageConfig
from ..exporters.sqlite_store import COMPANY_SIC_COLUMNS, SQLiteStore
from .industries import normalize_sic, normalize_sic_description

#: The SEC handler's cached submissions file for a CIK (``SECHandler._payload_cache_path("submissions", cik)``).
CACHE_FILE = "submissions_CIK{cik}.json"
#: A CIK as the companies table may hold it: 1 to 10 ASCII digits, zero-padded to 10 for the cache's file name.
CIK_PATTERN = re.compile(r"[0-9]{1,10}")

Code = Tuple[str, Optional[str]]


@dataclass
class Filled:
    """What a run did: the companies, those that had a code, and the tickers filled from each source or missing."""

    companies: int = 0
    coded: int = 0
    from_json: List[str] = field(default_factory=list)
    from_cache: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)


def _code(submissions: Any) -> Optional[Code]:
    """A submissions section's (code, description), or None without a code in it."""
    if not isinstance(submissions, dict):
        return None
    code = normalize_sic(submissions.get("sic"))
    return (code, normalize_sic_description(submissions.get("sic_description"))) if code is not None else None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def from_export(json_dir: Path, ticker: str) -> Optional[Code]:
    """The code in the ticker's JSON export (its ``sec_submissions``), or None: no export, one that doesn't read, or
    one without a code."""
    data = _read_json(json_dir / f"{ticker.upper()}.json")
    return _code(data.get("sec_submissions")) if isinstance(data, dict) else None


def from_cache(cache_dir: Path, cik: Optional[str]) -> Optional[Code]:
    """The code in the SEC handler's cached submissions for *cik*, or None: no CIK, no file, or no code in it."""
    text = str(cik).strip() if cik is not None else ""
    if not CIK_PATTERN.fullmatch(text):
        return None
    return _code(_read_json(cache_dir / CACHE_FILE.format(cik=text.zfill(10))))


def fill(db_path: Path, json_dir: Path, cache_dir: Path) -> Filled:
    """Fill the codes of the companies without one, in one transaction (``companies`` must exist)."""
    done = Filled()
    conn = sqlite3.connect(str(db_path))
    try:
        SQLiteStore._ensure_columns(conn, "companies", COMPANY_SIC_COLUMNS)
        rows = conn.execute("SELECT ticker, cik, sic FROM companies ORDER BY ticker").fetchall()
        done.companies = len(rows)
        for ticker, cik, sic in rows:
            if normalize_sic(sic) is not None:
                done.coded += 1
                continue
            code = from_export(json_dir, ticker)
            source = done.from_json
            if code is None:
                code, source = from_cache(cache_dir, cik), done.from_cache
            if code is None:
                done.missing.append(ticker)
                continue
            conn.execute("UPDATE companies SET sic = ?, sic_description = ? WHERE ticker = ?", (*code, ticker))
            source.append(ticker)
        conn.commit()
    finally:
        conn.close()
    return done


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    storage = StorageConfig()
    parser = argparse.ArgumentParser(
        prog="python -m src.universe.sic",
        description="Fill the companies' SEC SIC codes from the JSON exports, else the SEC cache (no network).")
    parser.add_argument("--db", default=str(storage.database_path), help="the SQLite database (default: %(default)s)")
    parser.add_argument("--json-dir", default=str(storage.json_dir),
                        help="the JSON exports, <TICKER>.json (default: %(default)s)")
    parser.add_argument("--cache-dir", default=str(storage.cache_dir),
                        help="the SEC handler's cache, submissions_CIK<CIK>.json (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"No database at {db_path}")
        return 1
    conn = sqlite3.connect(str(db_path))
    try:
        has_companies = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'companies'").fetchone() is not None
    finally:
        conn.close()
    if not has_companies:
        print(f"No companies table in {db_path}")
        return 1
    done = fill(db_path, Path(args.json_dir), Path(args.cache_dir))
    print(f"SIC codes in {db_path}: {done.companies} companies, {done.companies - done.coded} without a code")
    print(f"  filled from the JSON exports: {len(done.from_json)} ({args.json_dir})")
    print(f"  filled from the SEC cache: {len(done.from_cache)} ({args.cache_dir})"
          + (f": {', '.join(done.from_cache)}" if done.from_cache else ""))
    print(f"  still missing: {len(done.missing)}")
    for ticker in done.missing:
        print(f"    {ticker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
