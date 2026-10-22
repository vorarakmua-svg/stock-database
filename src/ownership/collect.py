"""Collect insiders' Form 4 filings from the SEC (Milestone 3)
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

For each company: its submissions feed back *years* (the ``recent`` block, and the older pages whose filings reach
the cutoff), then each Form 4 and 4/A's raw XML, parsed (``form4.parse_form4``) and stored. A document is cached
on disk once it parses, so a later run fetches only new filings. One that fails to fetch or parse is reported and
left out, and what an earlier run stored stays.

    python -m src.ownership.collect --insiders [--years 3] [--db PATH] [TICKERS...]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence

from ..exporters.sqlite_store import SQLiteStore
from .form4 import Form4, Form4Error, parse_form4

FORM4_FORMS = ("4", "4/A")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{document}"
#: A Form 4's primary document is its rendered page (xslF345X05/, xslF345X06/); the raw XML sits beside it.
_RENDERED = re.compile(r"^xslF345X\d+/")
_FEED_COLUMNS = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")


@dataclass
class FilingRef:
    """A Form 4 or 4/A as a submissions feed lists it: the raw XML's *document* name in its filing folder."""

    accession: str
    form: str
    filed: str
    report_date: Optional[str]
    document: str


def raw_document(primary: str) -> str:
    """The raw XML's name: the primary document without its rendering folder."""
    return _RENDERED.sub("", primary)


def form4_refs(pages: Iterable[Dict[str, List[Any]]], since: str) -> List[FilingRef]:
    """The Form 4 and 4/A filings filed on or after *since*, in the feed pages' order. Each page is in the SEC's
    column layout: one list per field, a filing per index."""
    refs = []
    for page in pages:
        n = len(page.get("accessionNumber") or [])
        columns = [list(page.get(key) or []) + [None] * n for key in _FEED_COLUMNS]
        for accession, form, filed, report, primary in zip(*(column[:n] for column in columns)):
            if form in FORM4_FORMS and filed and str(filed)[:10] >= since and primary:
                refs.append(FilingRef(accession, form, str(filed)[:10], str(report)[:10] if report else None,
                                      raw_document(primary)))
    return refs


class Form4Source:
    """A company's submissions pages and Form 4 filings, through the app's SEC handler (its User-Agent, rate
    limiter and retries); each document is cached under *cache_dir*/form4/ once it parses."""

    def __init__(self, sec: Any, cache_dir: Path) -> None:
        self.sec = sec
        self.cache = Path(cache_dir) / "form4"

    def _get(self, url: str) -> Any:
        response = self.sec.get_raw(url)
        if response.status_code != 200:
            raise OSError(f"{url}: HTTP {response.status_code}")
        return response

    def feed_pages(self, cik: str, since: str) -> Iterator[Dict[str, List[Any]]]:
        """The feed's ``recent`` block, then each older page whose filings reach *since*."""
        feed = self._get(SUBMISSIONS_URL.format(name=f"CIK{int(cik):010d}.json")).json()
        filings = feed.get("filings") or {}
        yield filings.get("recent") or {}
        for page in filings.get("files") or []:
            if str(page.get("filingTo") or "")[:10] >= since:
                yield self._get(SUBMISSIONS_URL.format(name=page["name"])).json()

    def filing(self, cik: str, ref: FilingRef) -> Form4:
        """The parsed filing: from the cache, else fetched, parsed and then cached. A cached document that no
        longer parses is fetched again."""
        path = self.cache / f"{ref.accession}.xml"
        if path.exists():
            try:
                return parse_form4(path.read_bytes(), ref.accession)
            except Form4Error:
                pass
        url = ARCHIVE_URL.format(cik=int(cik), folder=ref.accession.replace("-", ""), document=ref.document)
        content = self._get(url).content
        parsed = parse_form4(content, ref.accession)
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return parsed


def collect_insiders(db_path: Path, tickers: Sequence[str], years: float, source: Form4Source,
                     today: Optional[str] = None, log: Callable[[str], None] = lambda line: None,
                     ) -> Dict[str, Dict[str, Any]]:
    """Fetch, parse and store each company's Form 4 filings of the last *years*. Returns, per ticker, the
    filings listed, stored, and the errors (a failed fetch or parse; a company without a CIK)."""
    day = date.fromisoformat(today) if today else date.today()
    since = (day - timedelta(days=round(365.25 * years))).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        ciks = {t: c for t, c in conn.execute("SELECT ticker, cik FROM companies")}
    store = SQLiteStore(db_path=db_path)
    summary: Dict[str, Dict[str, Any]] = {}
    for ticker in tickers:
        cik = ciks.get(ticker)
        if not cik:
            summary[ticker] = {"listed": 0, "stored": 0, "errors": ["no CIK"]}
            continue
        try:
            refs = form4_refs(source.feed_pages(cik, since), since)
        except (OSError, ValueError) as exc:
            summary[ticker] = {"listed": 0, "stored": 0, "errors": [f"submissions: {exc}"]}
            continue
        parsed, errors = [], []
        for ref in refs:
            try:
                parsed.append((ref, source.filing(cik, ref)))
            except (OSError, Form4Error) as exc:
                errors.append(f"{ref.accession}: {exc}")
        store.export_insider_filings(ticker, parsed, datetime.now(timezone.utc).isoformat())
        summary[ticker] = {"listed": len(refs), "stored": len(parsed), "errors": errors}
        log(f"{ticker}: {len(refs)} Form 4 filings since {since}, {len(parsed)} stored, {len(errors)} errors")
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    from ..config import AppConfig, StorageConfig
    from ..fetchers.sec_handler import SECHandler

    parser = argparse.ArgumentParser(description="Collect SEC ownership filings into the database (Milestone 3).")
    parser.add_argument("tickers", nargs="*", help="Tickers to collect (default: every company in the DB)")
    parser.add_argument("--insiders", action="store_true", help="Insiders' Form 4 filings")
    parser.add_argument("--years", type=float, default=3.0, help="How far back (default: 3 years)")
    parser.add_argument("--db", type=Path, default=None, help="SQLite DB path (default: the standard store location)")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"),
                        help="The SEC cache (default: data/cache)")
    args = parser.parse_args(argv)
    if not args.insiders:
        parser.error("choose what to collect: --insiders")
    db_path = args.db or StorageConfig().database_path
    with sqlite3.connect(str(db_path)) as conn:
        tickers = [t.upper() for t in args.tickers] or [t for (t,) in conn.execute(
            "SELECT ticker FROM companies WHERE cik IS NOT NULL ORDER BY ticker")]
    sec = SECHandler(user_agent=AppConfig().sec.user_agent, cache_dir=args.cache_dir)
    try:
        summary = collect_insiders(db_path, tickers, args.years, Form4Source(sec, args.cache_dir),
                                   log=lambda line: print(line, flush=True))
    finally:
        sec.close()
    stored = sum(s["stored"] for s in summary.values())
    errors = sum(len(s["errors"]) for s in summary.values())
    print(f"Form 4 filings stored: {stored} for {len(summary)} tickers; errors: {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
