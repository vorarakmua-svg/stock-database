"""Collect SEC ownership filings (Milestone 3): insiders' Form 4s and the gurus' 13F holdings
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

For each company: its submissions feed back *years* (the ``recent`` block, and the older pages whose filings reach
the cutoff), then each Form 4 and 4/A's raw XML, parsed (``form4.parse_form4``) and stored. For each guru: each of
its filers' feeds, then each 13F-HR and 13F-HR/A whose period falls in the last *quarters* quarters before the
current one, read from its full submission text (``thirteenf.parse_13f``) and stored under the guru. A document is
cached on disk once it parses, so a later run fetches only new filings. One that fails to fetch or parse is reported
and left out, and what an earlier run stored stays. Then the holdings' CUSIPs are mapped to tickers (``map_cusips``):
our universe's from ``cusips.UNIVERSE_CUSIPS``, the others through OpenFIGI (``openfigi``), unless --no-openfigi.
Last, each guru's quarters and positions with their changes are built from its stored filings and stored
(``store_guru_positions``: ``guru_quarters``, ``guru_positions``), for the web; --positions rebuilds them alone,
without the network.

    python -m src.ownership.collect --insiders [--years 3] [--db PATH] [TICKERS...]
    python -m src.ownership.collect --gurus [--quarters 8] [--no-openfigi] [--db PATH] [--cache-dir DIR]
    python -m src.ownership.collect --positions [--db PATH]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, TypeVar

from ..exporters.sqlite_store import SQLiteStore, read_only_uri
from .cusips import UNIVERSE_CUSIPS
from .form4 import Form4, Form4Error, parse_form4
from .gurus import Guru
from .openfigi import BATCH_SIZE, CUSIP, JobErrors, OpenFIGI
from .portfolio import GuruQuarters, load_guru
from .thirteenf import ThirteenF, ThirteenFError, parse_13f

FORM4_FORMS = ("4", "4/A")
#: The 13F forms that carry holdings; a 13F-NT is a notice that another manager reports them.
THIRTEENF_FORMS = ("13F-HR", "13F-HR/A")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{document}"
#: A Form 4's primary document is its rendered page (xslF345X05/, xslF345X06/); the raw XML sits beside it.
_RENDERED = re.compile(r"^xslF345X\d+/")
_FEED_COLUMNS = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
#: OpenFIGI is taken to be down after this many failed requests in a row (each retried once by the client): the
#: CUSIPs not asked yet are left for the next run, which reads the answers so far from the cache.
OPENFIGI_FAILURES_IN_A_ROW = 3
_Parsed = TypeVar("_Parsed")


@dataclass
class FilingRef:
    """A filing as a submissions feed lists it, and the *document* to fetch from its filing folder: a Form 4's raw
    XML, a 13F's full submission text. *cik* is the filer whose feed lists it, where the caller names one."""

    accession: str
    form: str
    filed: str
    report_date: Optional[str]
    document: str
    cik: Optional[str] = None


def raw_document(primary: str) -> str:
    """The raw XML's name: the primary document without its rendering folder."""
    return _RENDERED.sub("", primary)


def _feed_rows(pages: Iterable[Dict[str, List[Any]]]) -> Iterator[Tuple[Any, ...]]:
    """Each filing of the feed pages, in their order: (accession, form, filed, report date, primary document). Each
    page is in the SEC's column layout: one list per field, a filing per index."""
    for page in pages:
        n = len(page.get("accessionNumber") or [])
        columns = [list(page.get(key) or []) + [None] * n for key in _FEED_COLUMNS]
        yield from zip(*(column[:n] for column in columns))


def form4_refs(pages: Iterable[Dict[str, List[Any]]], since: str) -> List[FilingRef]:
    """The Form 4 and 4/A filings filed on or after *since*, in the feed pages' order."""
    refs = []
    for accession, form, filed, report, primary in _feed_rows(pages):
        if form in FORM4_FORMS and filed and str(filed)[:10] >= since and primary:
            refs.append(FilingRef(accession, form, str(filed)[:10], str(report)[:10] if report else None,
                                  raw_document(primary)))
    return refs


def thirteenf_refs(pages: Iterable[Dict[str, List[Any]]], since_period: str,
                   cik: Optional[str] = None) -> List[FilingRef]:
    """The 13F-HR and 13F-HR/A filings whose period of report is on or after *since_period*, in the feed pages'
    order, each read from its full submission text. A 13F-NT notice holds no holdings and is left out."""
    refs = []
    for accession, form, filed, report, _primary in _feed_rows(pages):
        if form in THIRTEENF_FORMS and filed and report and str(report)[:10] >= since_period:
            refs.append(FilingRef(accession, form, str(filed)[:10], str(report)[:10], f"{accession}.txt", cik))
    return refs


def quarter_start(day: date, quarters: int) -> str:
    """The first day of the quarter *quarters* quarters before *day*'s: the earliest period of report of the last
    *quarters* quarters before the current one."""
    index = day.year * 4 + (day.month - 1) // 3 - quarters
    return date(index // 4, index % 4 * 3 + 1, 1).isoformat()


class _FilerSource:
    """A filer's submissions pages and filing documents, through the app's SEC handler (its User-Agent, rate
    limiter and retries); each document is cached under *cache_dir*/*folder*/ once it parses."""

    def __init__(self, sec: Any, cache_dir: Path, folder: str) -> None:
        self.sec = sec
        self.cache = Path(cache_dir) / folder

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

    def _document(self, name: str, url: str, parse: Callable[[bytes], _Parsed]) -> _Parsed:
        """The document *name* parsed: from the cache, else fetched, parsed and then cached. A cached document that
        no longer parses is fetched again."""
        path = self.cache / name
        if path.exists():
            try:
                return parse(path.read_bytes())
            except ValueError:  # Form4Error, ThirteenFError
                pass
        content = self._get(url).content
        parsed = parse(content)
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return parsed


class Form4Source(_FilerSource):
    """A company's submissions pages and Form 4 filings; each document is cached under *cache_dir*/form4/ once it
    parses."""

    def __init__(self, sec: Any, cache_dir: Path) -> None:
        super().__init__(sec, cache_dir, "form4")

    def filing(self, cik: str, ref: FilingRef) -> Form4:
        """The parsed filing, from the cache or fetched (``_document``)."""
        url = ARCHIVE_URL.format(cik=int(cik), folder=ref.accession.replace("-", ""), document=ref.document)
        return self._document(f"{ref.accession}.xml", url, lambda content: parse_form4(content, ref.accession))


class ThirteenFSource(_FilerSource):
    """A 13F filer's submissions pages and filings; each filing's full submission text is cached under
    *cache_dir*/13f/ once it parses."""

    def __init__(self, sec: Any, cache_dir: Path) -> None:
        super().__init__(sec, cache_dir, "13f")

    def filing(self, cik: str, ref: FilingRef) -> ThirteenF:
        """The parsed filing, from the cache or fetched (``_document``)."""
        url = ARCHIVE_URL.format(cik=int(cik), folder=ref.accession.replace("-", ""), document=ref.document)
        return self._document(f"{ref.accession}.txt", url, lambda content: parse_13f(content, ref.accession))


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


def collect_gurus(db_path: Path, gurus: Sequence[Guru], quarters: int, source: ThirteenFSource,
                  today: Optional[str] = None, log: Callable[[str], None] = lambda line: None,
                  ) -> Dict[str, Dict[str, Any]]:
    """Fetch, parse and store each guru's 13F-HR and 13F-HR/A filings whose period falls in the last *quarters*
    quarters before the current one, from every filer of its portfolio (a guru's quarter is the union of its
    filers' reports). Returns, per guru (its CIK), the filings listed, stored, those whose cover totals disagree
    with their entries (the checksum failures, stored and flagged), and the errors (a failed feed, fetch or
    parse)."""
    day = date.fromisoformat(today) if today else date.today()
    since = quarter_start(day, quarters)
    store = SQLiteStore(db_path=db_path)
    summary: Dict[str, Dict[str, Any]] = {}
    for guru in gurus:
        refs: List[FilingRef] = []
        parsed: List[Tuple[FilingRef, ThirteenF]] = []
        errors: List[str] = []
        for cik in guru.ciks:
            try:
                found = thirteenf_refs(source.feed_pages(cik, since), since, cik)
            except (OSError, ValueError) as exc:
                errors.append(f"{cik} submissions: {exc}")
                continue
            refs += found
            for ref in found:
                try:
                    parsed.append((ref, source.filing(cik, ref)))
                except (OSError, ThirteenFError) as exc:
                    errors.append(f"{ref.accession}: {exc}")
        store.export_guru_filings(guru, parsed, datetime.now(timezone.utc).isoformat())
        failures = [ref.accession for ref, filing in parsed if not filing.checksum_ok]
        summary[guru.cik] = {"listed": len(refs), "stored": len(parsed), "checksum_failures": failures,
                             "errors": errors}
        log(f"{guru.manager} ({guru.cik}): {len(refs)} 13F filings for periods from {since}, {len(parsed)} stored, "
            f"{len(failures)} checksum failures, {len(errors)} errors")
    return summary


def _issuers(conn: sqlite3.Connection) -> Dict[str, Optional[str]]:
    """Each CUSIP the gurus hold, in order, and the issuer's name most of its entries give (a tie: the first
    alphabetically). Empty for a database without the holdings' table."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'guru_holdings'").fetchone():
        return {}
    names: Dict[str, Dict[str, int]] = {}
    for cusip, issuer, count in conn.execute(
            "SELECT cusip, issuer, COUNT(*) FROM guru_holdings WHERE cusip IS NOT NULL GROUP BY cusip, issuer"):
        counts = names.setdefault(cusip, {})
        if issuer is not None:
            counts[issuer] = count
    return {cusip: min(counts, key=lambda name: (-counts[name], name)) if counts else None
            for cusip, counts in sorted(names.items())}


def _ask_openfigi(client: OpenFIGI, cusips: Sequence[str], issuers: Mapping[str, Optional[str]], mapped_at: str,
                  log: Callable[[str], None]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The ``cusip_map`` rows OpenFIGI gives *cusips*, asked 10 at a time so a failed request loses only its own,
    and the errors: a failed request, a code OpenFIGI refuses (``JobErrors``: the request's other codes are kept),
    a CUSIP that can't be asked. After OPENFIGI_FAILURES_IN_A_ROW failed requests in a row OpenFIGI is taken to be
    down, and the CUSIPs not asked yet are left for the next run."""
    errors = [f"{cusip}: not a CUSIP (9 letters or digits)" for cusip in cusips if not CUSIP.fullmatch(cusip)]
    ask = [cusip for cusip in cusips if CUSIP.fullmatch(cusip)]
    rows: List[Dict[str, Any]] = []
    failures = 0
    for start in range(0, len(ask), BATCH_SIZE):
        chunk = ask[start:start + BATCH_SIZE]
        try:
            found = client.lookup(chunk)
        except JobErrors as exc:
            found = exc.found
            for cusip, error in exc.failed.items():
                errors.append(f"{cusip}: OpenFIGI: {error}")
                log(errors[-1])
        except OSError as exc:
            errors.append(f"{', '.join(chunk)}: {exc}")
            log(errors[-1])
            failures += 1
            rest = len(ask) - start - len(chunk)
            if failures >= OPENFIGI_FAILURES_IN_A_ROW and rest:
                errors.append(f"OpenFIGI: {failures} requests failed in a row; {rest} CUSIPs not asked, left for "
                              f"the next run")
                log(errors[-1])
                break
            continue
        failures = 0
        for cusip in chunk:
            if cusip not in found:  # refused: reported, left for the next run
                continue
            listing = found[cusip]
            if listing is None:
                rows.append({"cusip": cusip, "ticker": None, "name": issuers.get(cusip), "source": "none",
                             "mapped_at": mapped_at})
            else:
                rows.append({"cusip": cusip, "ticker": listing.ticker, "name": listing.name, "source": "openfigi",
                             "security_type": listing.security_type, "mapped_at": mapped_at})
        if (start // BATCH_SIZE + 1) % 20 == 0:
            log(f"OpenFIGI: {start + len(chunk)} of {len(ask)} CUSIPs asked")
    return rows, errors


def map_cusips(db_path: Path, client: Optional[OpenFIGI], universe: Mapping[str, str] = UNIVERSE_CUSIPS,
               log: Callable[[str], None] = lambda line: None) -> Dict[str, Any]:
    """Map the CUSIPs the gurus hold to tickers (``cusip_map``), in one transaction. Those of our universe
    (*universe*, CUSIP -> ticker) are written every time as 'universe', overriding an earlier row; every other CUSIP
    without a row is asked of OpenFIGI (*client*; None asks nothing): a match is written as 'openfigi', no match as
    'none' (no ticker). A 'universe' or 'none' row is named by the holdings' issuer (``_issuers``), an 'openfigi' one
    by OpenFIGI. A request that fails is reported and its CUSIPs are left for the next run, never stored as no match.
    Returns the rows written by source, and the errors."""
    with closing(sqlite3.connect(str(db_path))) as conn:
        issuers = _issuers(conn)
        mapped = set()
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cusip_map'").fetchone():
            mapped = {cusip for (cusip,) in conn.execute("SELECT cusip FROM cusip_map")}
    mapped_at = datetime.now(timezone.utc).isoformat()
    rows = [{"cusip": cusip, "ticker": universe[cusip], "name": name, "source": "universe", "mapped_at": mapped_at}
            for cusip, name in issuers.items() if cusip in universe]
    errors: List[str] = []
    if client is not None:
        found, errors = _ask_openfigi(client, [c for c in issuers if c not in universe and c not in mapped], issuers,
                                      mapped_at, log)
        rows += found
    SQLiteStore(db_path=db_path).export_cusip_map(rows)
    summary: Dict[str, Any] = {source: sum(row["source"] == source for row in rows)
                               for source in ("universe", "openfigi", "none")}
    summary["errors"] = errors
    log(f"CUSIP map: {summary['universe']} from our universe, {summary['openfigi']} by OpenFIGI, {summary['none']} "
        f"that OpenFIGI doesn't know; {len(errors)} errors")
    return summary


def _load_guru(db_path: Path, guru_cik: str) -> GuruQuarters:
    """The guru's quarters and changes from its stored filings, read through a read-only connection; none from a
    database that isn't there yet."""
    if not Path(db_path).exists():
        return GuruQuarters(guru_cik)
    with closing(sqlite3.connect(read_only_uri(db_path), uri=True)) as conn:
        return load_guru(conn, guru_cik)


def store_guru_positions(db_path: Path, gurus: Sequence[Guru], log: Callable[[str], None] = lambda line: None,
                         ) -> Dict[str, Dict[str, int]]:
    """Store each guru's quarters and positions with their changes (``guru_quarters``, ``guru_positions``), built
    from its stored 13F filings (``portfolio.load_guru``), without any network: each guru's rows are replaced in one
    transaction (``SQLiteStore.export_guru_positions``), a guru without filings left with none. Returns, per guru
    (its CIK), the quarters and the position rows stored."""
    store = SQLiteStore(db_path=db_path)
    computed_at = datetime.now(timezone.utc).isoformat()
    summary: Dict[str, Dict[str, int]] = {}
    for guru in gurus:
        loaded = _load_guru(db_path, guru.cik)
        rows = store.export_guru_positions(loaded, computed_at)
        summary[guru.cik] = {"quarters": len(loaded.quarters), "positions": rows}
        log(f"{guru.manager} ({guru.cik}): {len(loaded.quarters)} quarters, {rows} position rows stored")
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    from ..config import AppConfig, StorageConfig
    from ..fetchers.sec_handler import SECHandler
    from .gurus import GURUS

    parser = argparse.ArgumentParser(description="Collect SEC ownership filings into the database (Milestone 3).")
    parser.add_argument("tickers", nargs="*",
                        help="Tickers whose insiders to collect (default: every company in the DB)")
    parser.add_argument("--insiders", action="store_true", help="Insiders' Form 4 filings")
    parser.add_argument("--gurus", action="store_true", help="The gurus' 13F holdings (src/ownership/gurus.py)")
    parser.add_argument("--years", type=float, default=3.0, help="How far back for insiders (default: 3 years)")
    parser.add_argument("--quarters", type=int, default=8,
                        help="How many quarters before the current one for gurus (default: 8)")
    parser.add_argument("--db", type=Path, default=None, help="SQLite DB path (default: the standard store location)")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"),
                        help="The SEC cache, and OpenFIGI's under openfigi/ (default: data/cache)")
    parser.add_argument("--no-openfigi", action="store_true",
                        help="With --gurus: map only our universe's CUSIPs, asking OpenFIGI nothing")
    parser.add_argument("--positions", action="store_true",
                        help="Rebuild the gurus' quarters and positions from the stored 13F filings, offline "
                             "(--gurus stores them after mapping the CUSIPs)")
    args = parser.parse_args(argv)
    if not (args.insiders or args.gurus or args.positions):
        parser.error("choose what to collect: --insiders, --gurus or both (or --positions, to rebuild the gurus' "
                     "quarters offline)")
    db_path = args.db or StorageConfig().database_path
    tickers: List[str] = []
    if args.insiders:
        with sqlite3.connect(str(db_path)) as conn:
            tickers = [t.upper() for t in args.tickers] or [t for (t,) in conn.execute(
                "SELECT ticker FROM companies WHERE cik IS NOT NULL ORDER BY ticker")]
    if args.insiders or args.gurus:
        sec = SECHandler(user_agent=AppConfig().sec.user_agent, cache_dir=args.cache_dir)
        try:
            if args.insiders:
                summary = collect_insiders(db_path, tickers, args.years, Form4Source(sec, args.cache_dir),
                                           log=lambda line: print(line, flush=True))
                stored = sum(s["stored"] for s in summary.values())
                errors = sum(len(s["errors"]) for s in summary.values())
                print(f"Form 4 filings stored: {stored} for {len(summary)} tickers; errors: {errors}")
            if args.gurus:
                summary = collect_gurus(db_path, GURUS, args.quarters, ThirteenFSource(sec, args.cache_dir),
                                        log=lambda line: print(line, flush=True))
                stored = sum(s["stored"] for s in summary.values())
                failures = sum(len(s["checksum_failures"]) for s in summary.values())
                errors = sum(len(s["errors"]) for s in summary.values())
                print(f"13F filings stored: {stored} for {len(summary)} gurus; checksum failures: {failures}; "
                      f"errors: {errors}")
                mapped = map_cusips(db_path, None if args.no_openfigi else OpenFIGI(args.cache_dir),
                                    log=lambda line: print(line, flush=True))
                print(f"CUSIPs mapped: {mapped['universe']} from our universe, {mapped['openfigi']} by OpenFIGI, "
                      f"{mapped['none']} not found; errors: {len(mapped['errors'])}")
        finally:
            sec.close()
    if args.gurus or args.positions:  # after the mapping, once; offline
        built = store_guru_positions(db_path, GURUS, log=lambda line: print(line, flush=True))
        print(f"Guru quarters stored: {sum(s['quarters'] for s in built.values())} quarters, "
              f"{sum(s['positions'] for s in built.values())} position rows for {len(built)} gurus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
