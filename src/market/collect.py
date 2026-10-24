"""Collect the market data (Milestone 4) into the database: FRED's corporate equities and GDP, Shiller's monthly S&P
series with its CAPE, and Treasury.gov's daily yield curve (docs/superpowers/specs/
2026-10-24-valuation-tools-market-design.md, "The market data").

Each source is fetched through ``sources`` (one session, a request a second, each answer cached under
``<cache-dir>/market/``) and stored by ``SQLiteStore.export_market_series``: its series replaced whole and its
``market_fetches`` row (url, rows, when its answer was fetched) in one transaction. FRED's two series are a source
each. Each source runs in its own try/except: a failure is reported with its reason, what was stored for it before
stays, and the others go on. A progress line per source, then a summary: each series' rows and dates, each source's
latest date.

    python -m src.market.collect [--fred] [--shiller] [--treasury] [--treasury-from YEAR] [--db PATH]
                                 [--cache-dir DIR]

All three sources when none is named. --db must name an existing database (a typo must not create a new one): exit
2 otherwise. Exit 0 when every source asked for stored rows, 1 otherwise.
"""
import argparse
import sys
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..exporters.sqlite_store import SQLiteStore
from .sources import FRED_SERIES, TREASURY_FROM, MarketFetcher, SourceData, fetch_fred, fetch_shiller, fetch_treasury

#: The sources, in the order they are collected.
SOURCES: Tuple[str, ...] = ("fred", "shiller", "treasury")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _stored(series: Dict[str, List[Tuple[str, float]]], data: SourceData, fetched_at: str) -> Dict[str, Any]:
    """A stored source's summary (``collect_market``)."""
    spans: Dict[str, Dict[str, Any]] = {name: {"rows": len(rows), "first": rows[0][0], "last": rows[-1][0]}
                                        for name, rows in series.items()}
    return {"ok": True, "rows": sum(span["rows"] for span in spans.values()), "series": spans,
            "latest": max(span["last"] for span in spans.values()), "url": data.url, "fetched_at": fetched_at,
            "fetched": data.fetched, "cached": data.cached, "notes": list(data.notes), "error": None}


def _failed(error: str) -> Dict[str, Any]:
    return {"ok": False, "rows": 0, "series": {}, "latest": None, "url": None, "fetched_at": None, "fetched": 0,
            "cached": 0, "notes": [], "error": error}


def _progress(source: str, s: Dict[str, Any]) -> str:
    if not s["ok"]:
        return f"{source}: FAILED: {s['error']}; its stored series stay as they were"
    return (f"{source}: {s['rows']} rows in {len(s['series'])} series, latest {s['latest']} ({s['fetched']} fetched, "
            f"{s['cached']} from the cache)")


def collect_market(db_path: Path, sources: Sequence[str], cache_dir: Path, session: Any = None,
                   log: Callable[[str], None] = print, treasury_from: int = TREASURY_FROM,
                   today: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Fetch and store each of *sources* ("fred", "shiller", "treasury"; the Treasury's years from *treasury_from* to
    *today*'s, ISO, the current day by default), through *session* (a new ``requests.Session`` by default) and the
    cache under *cache_dir*/market/. Each source in its own try/except: one that fails, or has no rows, stores
    nothing and the others go on.

    Returns, per stored source in order (FRED's two series a source each, "fred:<ID>"; "shiller"; "treasury"): "ok"
    (it stored rows), "rows", "series" (each stored series' rows, first and last date), "latest" (its series' latest
    date), "url", "fetched_at" (when its answer was fetched, UTC ISO), "fetched" and "cached" (its answers fetched and
    read from the cache), "notes" (its fallbacks to a cached copy) and "error" (why it failed, else None)."""
    unknown = [source for source in sources if source not in SOURCES]
    if unknown:
        raise ValueError(f"unknown market sources {', '.join(unknown)}: the sources are {', '.join(SOURCES)}")
    year = (date.fromisoformat(today) if today else date.today()).year
    fetcher = MarketFetcher(cache_dir, session=session, log=log)
    jobs: List[Tuple[str, str, Callable[[], SourceData]]] = []
    if "fred" in sources:
        jobs += [(f"fred:{series_id}", "fred:", partial(fetch_fred, fetcher, series_id)) for series_id in FRED_SERIES]
    if "shiller" in sources:
        jobs.append(("shiller", "shiller:", partial(fetch_shiller, fetcher)))
    if "treasury" in sources:
        jobs.append(("treasury", "ust:", partial(fetch_treasury, fetcher, treasury_from, year, log)))
    store = SQLiteStore(db_path=db_path)
    summary: Dict[str, Dict[str, Any]] = {}
    for source, prefix, fetch in jobs:
        try:
            data = fetch()
            series = {prefix + name: rows for name, rows in data.series.items() if rows}
            if not series:
                raise ValueError("no rows")
            fetched_at = _iso(data.fetched_at)
            store.export_market_series(source, series, data.url, fetched_at)
            summary[source] = _stored(series, data, fetched_at)
        except Exception as exc:  # anything one source raises is its own: the others go on
            summary[source] = _failed(f"{type(exc).__name__}: {exc}")
        log(_progress(source, summary[source]))
    return summary


def summary_lines(summary: Dict[str, Dict[str, Any]]) -> List[str]:
    """The run's summary: the sources stored, each stored series' rows and dates, each source's latest date, the
    sources that failed and why."""
    stored = [source for source, s in summary.items() if s["ok"]]
    lines = [f"Market data: {len(stored)} of {len(summary)} sources stored"]
    for source in stored:
        lines += [f"  {name}: {span['rows']} rows, {span['first']} to {span['last']}"
                  for name, span in summary[source]["series"].items()]
    lines.append("Latest: " + ("; ".join(f"{source} {summary[source]['latest']}" for source in stored) or "none"))
    failed = [f"{source} ({s['error']})" for source, s in summary.items() if not s["ok"]]
    lines.append("Failed: " + ("; ".join(failed) or "none"))
    return lines


def _say(line: str) -> None:
    """Print *line* (flushed) in what the console can show: a cp1252 console can't print every character."""
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    print(line.encode(encoding, "replace").decode(encoding), flush=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from ..config import StorageConfig

    parser = argparse.ArgumentParser(
        description="Collect the market data into the database (Milestone 4): FRED's corporate equities and GDP, "
                    "Shiller's monthly S&P series and CAPE, Treasury.gov's daily yield curve.")
    parser.add_argument("--fred", action="store_true", help="FRED's NCBEILQ027S (corporate equities) and GDP")
    parser.add_argument("--shiller", action="store_true",
                        help="Shiller's ie_data.xls: the monthly price, dividend, earnings, CPI and CAPE")
    parser.add_argument("--treasury", action="store_true", help="Treasury.gov's daily yield curve, a CSV a year")
    parser.add_argument("--treasury-from", type=int, default=TREASURY_FROM, metavar="YEAR",
                        help=f"The Treasury's first year (default: {TREASURY_FROM})")
    parser.add_argument("--db", type=Path, default=None,
                        help="The SQLite DB, which must exist (default: the standard store location)")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"),
                        help="The cache directory; the answers go under market/ (default: data/cache)")
    args = parser.parse_args(argv)
    wanted = [source for source in SOURCES if getattr(args, source)] or list(SOURCES)
    db_path = args.db or StorageConfig().database_path
    if not Path(db_path).is_file():
        print(f"No database at {db_path}: --db must name an existing database (none is created).", file=sys.stderr)
        return 2
    summary = collect_market(db_path, wanted, args.cache_dir, log=_say, treasury_from=args.treasury_from)
    for line in summary_lines(summary):
        _say(line)
    return 0 if all(s["ok"] for s in summary.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
