"""A synthetic copy of a stock database with each company cloned under made-up tickers, for the scale pre-flight
(build task M5-4; docs/superpowers/specs/2026-10-25-universe-automation-design.md, "Scale"): 127 companies cloned
three times make about 500, the size of the S&P 500 collection, before it is run.

The copy is made with SQLite's backup API from a read-only connection to the source, which is never written; --out
must not exist. Then every company is cloned --copies times, AAPL as AAPLQ1, AAPLQ2, AAPLQ3: each clone ticker is
checked to be one the app accepts as it is (``src.universe.tickers.normalize_ticker`` gives it back unchanged) and to
be none of the universe files' tickers (universe/sp500-*.csv, universe/core.txt) or any ticker the database holds (in
any table), so no clone stands for a real company; a clone that fails either check stops the run (exit 1) and the
copy is removed.

Cloned, in one transaction: every row whose ticker is a company's, of every table with a ticker column (companies,
the financials and their vintages, the metrics, the snapshots, the price bars, the valuations and their summary,
history, ranges and indicators, the quality scores, the warning signs, the per-share series, the insiders' Form 4
filings and trades and Yahoo's insider rows, the holders, the officers, the dividends, splits and earnings, the
collection runs, the unmapped facts, ...), with the clone's ticker. In the Form 4 tables the accession, and the
amendment a row names (``superseded_by``), are suffixed with "-Q<k>", so the primary keys stay unique and each
clone's amendments name its own filings. A clone's company name ends in " [Q<k>]". The ^GSPC benchmark's bars (no
companies row) aren't cloned.

Left as they are: the tables without a ticker column (market_series and market_fetches, the gurus' filers, filings,
holdings and quarters, refresh_log) and cusip_map (its tickers name CUSIPs, not our companies). The gurus' positions
in our companies (``guru_positions`` rows whose ticker is a company's) are cloned onto the clone tickers, the security
suffixed as the ticker is ("AAPL" as "AAPLQ1"): the screener's and the HDS tab's guru lookups then cost what they will
once the S&P 500's companies, which the gurus already hold, are ours. A guru's quarter then lists each such position
1 + copies times, its stored total and position count left as they are: the guru pages are larger than they will be,
the conservative side for a timing.

No ANALYZE is run: the source has no statistics (sqlite_stat1), so the planner reads the copy as it reads the source.
It prints the companies and each table's rows before and after, and the copy's file size.

Usage: python qa/scale/synthetic_universe.py --source PATH --out PATH [--copies 3]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.exporters.sqlite_store import read_only_uri
from src.universe.tickers import normalize_ticker, read_tickers_file

#: The repository's universe files, whose tickers no clone may take.
UNIVERSE_DIR = Path(__file__).resolve().parents[2] / "universe"
#: Tables with a ticker column that are left as they are: the CUSIP map's tickers name CUSIPs, not our companies.
LEFT_AS_IS = frozenset({"cusip_map"})
#: Per table, the key columns besides the ticker that a clone suffixes: the Form 4 tables' accession (and the
#: amendment a row names) with "-Q<k>", the gurus' positions' security as the ticker ("Q<k>").
SUFFIXED: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "insider_filings": (("accession", "-"), ("superseded_by", "-")),
    "insider_trades": (("accession", "-"), ("superseded_by", "-")),
    "guru_positions": (("security", ""),),
}
#: The clone suffix: the k-th clone of AAPL is AAPLQ<k>.
MARK = "Q"


def _tables(conn: sqlite3.Connection) -> List[str]:
    return [name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Each table's row count."""
    return {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in _tables(conn)}


def taken_tickers(conn: sqlite3.Connection) -> Set[str]:
    """Every ticker the universe files list and the database holds, in any table: none may be a clone's."""
    taken: Set[str] = set()
    for path in sorted(UNIVERSE_DIR.glob("sp500-*.csv")) + [UNIVERSE_DIR / "core.txt"]:
        if path.is_file():
            taken.update(read_tickers_file(path))
    for table in _tables(conn):
        if "ticker" in _columns(conn, table):
            taken.update(t for (t,) in conn.execute(f'SELECT DISTINCT ticker FROM "{table}"') if t is not None)
    return taken


def clone_tickers(tickers: List[str], copies: int, taken: Set[str]) -> Dict[str, List[str]]:
    """Each ticker's clones (AAPL: AAPLQ1 ... AAPLQ<copies>). Raises ValueError for a clone the app wouldn't accept as
    it is, or one that is a real ticker or another clone."""
    clones: Dict[str, List[str]] = {}
    seen: Set[str] = set()
    for ticker in tickers:
        clones[ticker] = [f"{ticker}{MARK}{k}" for k in range(1, copies + 1)]
        for clone in clones[ticker]:
            if normalize_ticker(clone) != clone:
                raise ValueError(f"clone {clone} of {ticker} isn't a ticker in Yahoo's form")
            if clone in taken or clone in seen:
                raise ValueError(f"clone {clone} of {ticker} is a ticker already: pick another suffix")
            seen.add(clone)
    return clones


def _clone_sql(table: str, columns: List[str]) -> str:
    """The INSERT that copies *table*'s rows of the original companies with a clone's suffix, bound by name: :mark
    ("Q<k>") for the ticker, the name and the security, :dash_mark ("-Q<k>") for an accession."""
    suffixed = dict(SUFFIXED.get(table, ()))
    exprs = []
    for column in columns:
        if column == "ticker":
            exprs.append("ticker || :mark")
        elif column == "company_name" and table == "companies":
            exprs.append("company_name || ' [' || :mark || ']'")
        elif column in suffixed:
            exprs.append(f'"{column}" || ' + (":dash_mark" if suffixed[column] else ":mark"))
        else:
            exprs.append(f'"{column}"')
    names = ", ".join(f'"{c}"' for c in columns)
    return (f'INSERT INTO "{table}" ({names}) SELECT {", ".join(exprs)} FROM "{table}" '
            "WHERE ticker IN (SELECT ticker FROM temp.originals)")


def clone(conn: sqlite3.Connection, copies: int) -> Dict[str, int]:
    """Clone every company *copies* times in *conn* (one transaction); returns the rows added per table."""
    tickers = [t for (t,) in conn.execute("SELECT ticker FROM companies ORDER BY ticker")]
    clone_tickers(tickers, copies, taken_tickers(conn))
    tables = [t for t in _tables(conn) if t not in LEFT_AS_IS and "ticker" in _columns(conn, t)]
    added: Dict[str, int] = {}
    conn.execute("CREATE TEMP TABLE originals (ticker TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO temp.originals SELECT ticker FROM companies")
    try:
        for k in range(1, copies + 1):
            marks = {"mark": f"{MARK}{k}", "dash_mark": f"-{MARK}{k}"}
            for table in tables:
                cur = conn.execute(_clone_sql(table, _columns(conn, table)), marks)
                added[table] = added.get(table, 0) + cur.rowcount
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return added


def _size(path: Path) -> str:
    return f"{path.stat().st_size / 1e6:,.1f} MB ({path.stat().st_size:,} bytes)"


def _remove(db: Path) -> None:
    """Delete a copy this run made, with its WAL files."""
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="A synthetic copy of a stock database with each company cloned "
                                                 "under made-up tickers (the scale pre-flight).")
    parser.add_argument("--source", required=True, help="the database to copy (opened read-only)")
    parser.add_argument("--out", required=True, help="the synthetic copy (must not exist)")
    parser.add_argument("--copies", type=int, default=3, help="clones of each company (default: %(default)s)")
    args = parser.parse_args(argv)
    source, out = Path(args.source), Path(args.out)
    if args.copies < 1:
        parser.error("--copies must be at least 1")
    if not source.is_file():
        print(f"No database at {source}")
        return 1
    if out.exists():
        print(f"{out} exists: refusing to overwrite it")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(out))
    try:
        reader = sqlite3.connect(read_only_uri(source), uri=True)
        try:
            reader.backup(target)
        finally:
            reader.close()
        before = counts(target)
        print(f"source {source}: {before.get('companies', 0)} companies, {_size(source)}")
        try:
            added = clone(target, args.copies)
        except ValueError as exc:
            target.close()
            _remove(out)
            print(f"refused, and the copy removed: {exc}")
            return 1
        after = counts(target)
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        check = target.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        target.close()
    print(f"copy {out}: {after.get('companies', 0)} companies ({args.copies} clones of each), integrity {check}")
    width = max(len(t) for t in after)
    print(f"{'table':<{width}}  {'before':>10}  {'after':>10}  {'cloned':>10}")
    for table in sorted(after):
        note = "" if table in added else "  (left as it is)"
        print(f"{table:<{width}}  {before.get(table, 0):>10,}  {after[table]:>10,}  {added.get(table, 0):>10,}{note}")
    print(f"rows: {sum(before.values()):,} before, {sum(after.values()):,} after")
    print(f"file size: {_size(out)}")
    return 0 if check == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
