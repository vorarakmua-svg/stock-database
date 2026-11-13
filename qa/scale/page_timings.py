"""Time every page and API a user hits, in process through FastAPI's TestClient, on one database (build task M5-4;
docs/superpowers/specs/2026-10-25-universe-automation-design.md, "Scale").

No server is started and nothing is written: the app opens the database read-only (its readers connect with
mode=ro), collection and the quote refresh are off as in the tests, and the database file's SHA-256 and its -wal
file's (an empty WAL counts as none) are compared before and after the run (exit 2 when either changed).

Each path is requested once untimed (cold: the templates compile, the OS caches the pages), then --runs times, a run
being one pass over all the paths, so a stall of the machine touches every path alike. The table gives each path's
status, response bytes, the median and the max of the timed runs and the cold request (ms), and flags a median over
its gate: 500 ms for the screener's results (/ui/screen, /api/screen and the CSV export /api/export/screen.csv: the
default, the sorts and the filters), 1,000 ms for every other page and API. Exit 0 when every median is within its
gate and no response is a 5xx, else 1.

The paths: home with its watchlist API, the companies list, the screener page, its results (the default; sorted by
val_upside_pct, composite_score, gurus_holding, ins_net_buy_12m and reverse_dcf_growth; the largest 2-digit SIC
industry; the Buffett-Munger preset's filters) and its CSV export, the sector aggregates; for each ticker the stock's
page and each tab's fragments and APIs (DES; GP with its chart data; FA with its statements; ERN; STAT with Quality;
HP; DVD; HDS with the gurus; INS with Form 4; VAL with the history and the other indicators) and its as-of
fragments; the Ctrl+K search; /gurus, the guru with the most positions in its latest quarter, /gurus/portfolio and
their APIs; /market and /api/market; /quality and its APIs; the as-of and collect pages.

--tickers: comma-separated; by default the companies with the most Form 4 trades, price bars and gurus' positions
(the heaviest stock pages).

Usage: python qa/scale/page_timings.py --db PATH [--runs 5] [--tickers LIST]
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.parse import quote, urlencode

from fastapi.testclient import TestClient

from src.exporters.sqlite_store import read_only_uri
from src.webapp import create_app
from src.webapp.screener import PRESETS
from src.webapp.settings import WebSettings

#: The gates, on a path's median (ms).
PAGE_GATE_MS = 1000.0
SCREEN_GATE_MS = 500.0
#: The screener's results, gated at SCREEN_GATE_MS: the fragment, the JSON and the CSV export (the same query).
SCREEN_PREFIXES = ("/ui/screen", "/api/screen", "/api/export/screen.csv")
#: The screener's heavy sorts (descending).
HEAVY_SORTS = ("val_upside_pct", "composite_score", "gurus_holding", "ins_net_buy_12m", "reverse_dcf_growth")
#: The industry filter when the database has no SIC codes (it then matches no company).
FALLBACK_INDUSTRY = "28"
#: The watchlist API's tickers: the chosen ones, then the first companies, up to this many.
WATCHLIST = 10


@dataclass
class Timing:
    """One path's requests: its gate, the cold request, the timed runs, its last status and size."""

    path: str
    gate: float
    cold: float = 0.0
    runs: List[float] = field(default_factory=list)
    status: int = 0
    size: int = 0

    @property
    def median(self) -> float:
        return statistics.median(self.runs) if self.runs else self.cold

    @property
    def over(self) -> bool:
        return self.median > self.gate


def gate_of(path: str) -> float:
    return SCREEN_GATE_MS if path.startswith(SCREEN_PREFIXES) else PAGE_GATE_MS


def _one(conn: sqlite3.Connection, sql: str, params: Sequence[str] = ()) -> Optional[str]:
    """The first column of *sql*'s first row, None without a row or the table."""
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None or row[0] is None else str(row[0])


def _most(conn: sqlite3.Connection, table: str) -> Optional[str]:
    """The company with the most rows in *table*."""
    return _one(conn, f"SELECT ticker FROM {table} WHERE ticker IN (SELECT ticker FROM companies) "
                      "GROUP BY ticker ORDER BY COUNT(*) DESC, ticker LIMIT 1")


def default_tickers(conn: sqlite3.Connection) -> List[str]:
    """The companies with the most Form 4 trades, price bars and gurus' positions, each once; the first company
    when none has any."""
    found = [_most(conn, table) for table in ("insider_trades", "price_bars", "guru_positions")]
    tickers = list(dict.fromkeys(t for t in found if t is not None))
    return tickers or [t for t in [_one(conn, "SELECT ticker FROM companies ORDER BY ticker LIMIT 1")] if t]


def largest_industry(conn: sqlite3.Connection) -> str:
    """The 2-digit SIC prefix with the most companies, or FALLBACK_INDUSTRY without codes."""
    return _one(conn, "SELECT substr(sic, 1, 2) AS p FROM companies WHERE sic GLOB '[0-9][0-9]*' "
                      "GROUP BY p ORDER BY COUNT(*) DESC, p LIMIT 1") or FALLBACK_INDUSTRY


def heaviest_guru(conn: sqlite3.Connection) -> Optional[str]:
    """The guru with the most positions in its latest quarter."""
    return _one(conn, "SELECT p.guru_cik FROM guru_positions p JOIN (SELECT guru_cik, MAX(period) AS period "
                      "FROM guru_quarters GROUP BY guru_cik) l ON l.guru_cik = p.guru_cik AND l.period = p.period "
                      "GROUP BY p.guru_cik ORDER BY COUNT(*) DESC, p.guru_cik LIMIT 1")


def stock_paths(ticker: str, fiscal_year: Optional[str], today: str) -> List[str]:
    """A stock's page, each tab's fragments and APIs, and its as-of fragments."""
    t = quote(ticker, safe="")
    paths = [
        f"/stocks/{t}",
        # DES
        f"/ui/stocks/{t}/des", f"/api/stocks/{t}/quote", f"/api/stocks/{t}/profile", f"/api/stocks/{t}/analyst",
        f"/api/companies/{t}",
        # GP and its chart data
        f"/ui/stocks/{t}/gp", f"/api/stocks/{t}/bars?range=1Y", f"/api/stocks/{t}/bars?range=5Y",
        f"/api/stocks/{t}/bars?range=MAX", f"/api/stocks/{t}/indicators?range=1Y",
        f"/api/stocks/{t}/indicators?range=MAX", f"/api/stocks/{t}/compare-bars?others=%5EGSPC&range=1Y",
        # FA
        f"/ui/stocks/{t}/fa", f"/ui/companies/{t}/statements?period=quarterly",
        f"/ui/companies/{t}/statements?period=ttm", f"/ui/companies/{t}/statements?period=metrics",
        f"/ui/companies/{t}/metric-chart?metric=roic", f"/api/companies/{t}/financials/annual",
        f"/api/companies/{t}/financials/quarterly", f"/api/companies/{t}/financials/ttm",
        f"/api/companies/{t}/metrics", f"/api/companies/{t}/peers", f"/api/export/company/{t}/annual.csv",
        # ERN
        f"/ui/stocks/{t}/ern", f"/api/stocks/{t}/earnings",
        # STAT with Quality
        f"/ui/stocks/{t}/stat", f"/ui/stocks/{t}/scores", f"/api/stocks/{t}/scores",
        # HP
        f"/ui/stocks/{t}/hp?range=1Y", f"/ui/stocks/{t}/hp?range=MAX", f"/api/export/stock/{t}/bars.csv?range=MAX",
        # DVD
        f"/ui/stocks/{t}/dvd", f"/api/stocks/{t}/dividends", f"/api/stocks/{t}/splits",
        f"/api/export/stock/{t}/dividends.csv",
        # HDS with the gurus
        f"/ui/stocks/{t}/hds", f"/api/stocks/{t}/holders?type=institutional",
        f"/api/stocks/{t}/holders?type=mutualfund", f"/api/stocks/{t}/gurus",
        # INS with Form 4
        f"/ui/stocks/{t}/ins", f"/api/stocks/{t}/form4", f"/api/stocks/{t}/insiders",
        # VAL with the history and the other indicators
        f"/ui/stocks/{t}/val", f"/ui/stocks/{t}/val-history", f"/ui/stocks/{t}/val-indicators",
        f"/api/stocks/{t}/valuation", f"/api/stocks/{t}/valuation-history", f"/api/stocks/{t}/valuation-indicators",
    ]
    if fiscal_year is not None:
        paths += [
            "/ui/asof/result?" + urlencode({"ticker": ticker, "fiscal_year": fiscal_year, "date": today}),
            "/ui/asof/vintages?" + urlencode({"ticker": ticker, "fiscal_year": fiscal_year}),
            f"/api/asof/{t}/annual?" + urlencode({"fiscal_year": fiscal_year, "date": today}),
            f"/api/asof/{t}/metrics?" + urlencode({"fiscal_year": fiscal_year, "date": today}),
        ]
    return paths


def all_paths(conn: sqlite3.Connection, tickers: Sequence[str], today: str) -> List[str]:
    """Every path timed, in the table's order."""
    companies = [t for (t,) in conn.execute("SELECT ticker FROM companies ORDER BY ticker LIMIT ?", (WATCHLIST,))]
    watchlist = list(dict.fromkeys(list(tickers) + companies))[:WATCHLIST]
    presets = {key: qs for key, _, qs in PRESETS}
    paths = [
        "/", "/api/stocks/summary?" + urlencode({"tickers": ",".join(watchlist)}),
        "/companies", "/api/companies?limit=100", "/api/companies/sectors",
        "/api/companies/search?q=a", "/api/companies/search?q=micro",
        "/screener", "/ui/screen",
        *[f"/ui/screen?sort={s}&sort_dir=desc" for s in HEAVY_SORTS],
        f"/ui/screen?industry={largest_industry(conn)}",
        "/ui/screen?" + presets["buffett_munger"],
        "/api/screen", "/api/screen?sort=gurus_holding&sort_dir=desc",
        "/api/export/screen.csv", "/api/sectors/aggregates",
    ]
    for ticker in tickers:
        fiscal_year = _one(conn, "SELECT MAX(fiscal_year) FROM financials_annual_vintages WHERE ticker = ?", (ticker,))
        paths += stock_paths(ticker, fiscal_year, today)
    guru = heaviest_guru(conn)
    paths += ["/gurus", "/api/gurus"]
    if guru is not None:
        paths += [f"/gurus/{guru}", f"/api/gurus/{guru}"]
    paths += [
        "/gurus/portfolio", "/api/gurus/portfolio", "/market", "/api/market",
        "/quality", "/api/quality/latest", "/api/quality/coverage", "/api/quality/unmapped",
        "/api/quality/unmapped/top", "/api/quality/freshness", "/api/quality/runs",
        "/asof", "/collect", "/api/health",
    ]
    return paths


def fingerprint(db: Path) -> Dict[str, Optional[str]]:
    """The SHA-256 of the database file and of its -wal file: None for a WAL that is absent or empty, which holds no
    change (a read-only connection to a WAL database may leave an empty one behind)."""
    wal = db.with_name(db.name + "-wal")
    return {db.name: hashlib.sha256(db.read_bytes()).hexdigest(),
            wal.name: hashlib.sha256(wal.read_bytes()).hexdigest() if wal.is_file() and wal.stat().st_size else None}


def measure(client: TestClient, paths: Sequence[str], runs: int) -> List[Timing]:
    """One cold pass, then *runs* timed passes over *paths*."""
    timings = [Timing(path, gate_of(path)) for path in paths]
    for run in range(runs + 1):
        for timing in timings:
            start = time.perf_counter()
            response = client.get(timing.path)
            elapsed = (time.perf_counter() - start) * 1000.0
            if run == 0:
                timing.cold = elapsed
            else:
                timing.runs.append(elapsed)
            timing.status, timing.size = response.status_code, len(response.content)
    return timings


def table(timings: Sequence[Timing]) -> List[str]:
    """The table's lines: path, status, bytes, median, max, cold (ms), gate and a flag for a median over it."""
    width = max(len("path"), *(len(t.path) for t in timings))
    lines = [f"{'path':<{width}}  status  {'bytes':>10}  {'median ms':>9}  {'max ms':>8}  {'cold ms':>8}  gate"]
    for t in timings:
        flag = "  OVER" if t.over else ""
        slowest = max(t.runs or [t.cold])
        lines.append(f"{t.path:<{width}}  {t.status:>6}  {t.size:>10,}  {t.median:>9.1f}  {slowest:>8.1f}  "
                     f"{t.cold:>8.1f}  {t.gate:>4.0f}{flag}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Time every page and API a user hits, through FastAPI's "
                                                 "TestClient, on a database opened read-only.")
    parser.add_argument("--db", required=True, help="the SQLite database")
    parser.add_argument("--runs", type=int, default=5, help="timed passes over the paths (default: %(default)s)")
    parser.add_argument("--tickers", help="comma-separated tickers (default: the heaviest stock pages)")
    args = parser.parse_args(argv)
    db = Path(args.db)
    if not db.is_file():
        print(f"No database at {db}")
        return 1
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    today = date.today().isoformat()
    conn = sqlite3.connect(read_only_uri(db), uri=True)
    try:
        companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        tickers = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers
                   else default_tickers(conn))
        paths = all_paths(conn, tickers, today)
    finally:
        conn.close()
    before = fingerprint(db)
    settings = WebSettings(db_path=db, allow_collection=False, allow_quote_refresh=False)
    with TestClient(create_app(settings=settings)) as client:
        timings = measure(client, paths, args.runs)
    after = fingerprint(db)
    print(f"database: {db} ({companies} companies, {db.stat().st_size / 1e6:,.1f} MB)")
    print(f"measured {datetime.now().isoformat(timespec='seconds')}: {len(paths)} paths, 1 cold + {args.runs} timed "
          f"runs each, in process (TestClient), collection and quote refresh off; Python "
          f"{platform.python_version()}, SQLite {sqlite3.sqlite_version}, {platform.platform()}")
    print(f"tickers: {', '.join(tickers)}")
    print(f"gates (median): {SCREEN_GATE_MS:.0f} ms for the screener's results ({', '.join(SCREEN_PREFIXES)}), "
          f"{PAGE_GATE_MS:.0f} ms for every other page and API")
    print()
    for line in table(timings):
        print(line)
    print()
    print("slowest 10 by median:")
    for line in table(sorted(timings, key=lambda t: -t.median)[:10])[1:]:
        print("  " + line)
    over = [t for t in timings if t.over]
    errors = [t for t in timings if t.status >= 500]
    other = [t for t in timings if t.status != 200 and t.status < 500]
    print()
    print(f"over the gate: {len(over)}" + "".join(f"\n  {t.path}: {t.median:.1f} ms (gate {t.gate:.0f})" for t in over))
    print(f"5xx responses: {len(errors)}" + "".join(f"\n  {t.path}: {t.status}" for t in errors))
    print(f"other non-200 responses: {len(other)}" + "".join(f"\n  {t.path}: {t.status}" for t in other))
    unchanged = before == after
    print(f"database unchanged (SHA-256 of {', '.join(before)}): {'yes' if unchanged else 'NO'}")
    if not unchanged:
        return 2
    return 1 if over or errors else 0


if __name__ == "__main__":
    sys.exit(main())
