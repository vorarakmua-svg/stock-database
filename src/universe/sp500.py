"""The S&P 500's members from Wikipedia's "List of S&P 500 companies" page, saved as a dated CSV in the repo's
``universe/`` folder (Milestone 5; docs/superpowers/specs/2026-10-25-universe-automation-design.md, "The universe").

    python -m src.universe.sp500 [--out PATH]

- The page (SP500_URL) is read with one request, without retries, its User-Agent naming the app without an email
  (never the SEC's, which carries one); an answer other than 200 (a 403 or a 429 refusing it) fails the run.
- Its members' table is the page's first table whose header row has the cells COLUMNS names (in any order, without
  case; the other columns ignored), read with the standard library's ``html.parser``: each cell's text without its
  tags, its superscripts (the footnote markers, "[3]", and the "[citation needed]"s), a marker left as text, the CSS
  and scripts a template puts in a cell, or its hidden text (``display: none``); its entities decoded, a line break
  as a space, its spaces collapsed. A cell or a row left open ends where the next begins, as HTML allows.
- A member's ticker is in Yahoo's form (``tickers.normalize_ticker``: ``BRK.B`` as ``BRK-B``), its CIK ten digits,
  zero-padded, its date added as the page has it (an ISO date), or empty.
- A page that changed fails loudly, with ValueError: no such table (or one without a column, named), a ticker that
  isn't one, a CIK that isn't 1 to 10 digits, a row without the header's cells, or a count of rows outside
  SP500_MIN_ROWS to SP500_MAX_ROWS.
- One company a CIK (``one_per_cik``): a second share class after the first of its CIK in the page's order (GOOG
  after GOOGL, FOX after FOXA, NWS after NWSA, the page listing them by name) is dropped, as the database keeps one
  (GOOGL).
- The CLI writes ``universe/sp500-<today>.csv`` (CSV_FIELDS; --out names another file), never over an existing file
  (it refuses before fetching), and prints the rows read, kept and dropped (their tickers) and the file. Exit 0 when
  it wrote the file, 1 otherwise.
"""
import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import requests

from .tickers import normalize_ticker

#: Wikipedia's list of the S&P 500's members.
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
#: The request's User-Agent: the app's name, without an email (never the SEC's).
USER_AGENT = "StockDataCollector/1.0 (personal research tool)"
#: The request's timeout, in seconds.
TIMEOUT = 30
#: The members' table's rows (before ``one_per_cik``), inclusive: fewer or more and the page changed.
SP500_MIN_ROWS = 480
SP500_MAX_ROWS = 520
#: The columns read from the members' table and their header cells (read without case).
COLUMNS: Dict[str, str] = {"ticker": "Symbol", "name": "Security", "gics_sector": "GICS Sector",
                           "gics_sub_industry": "GICS Sub-Industry", "date_added": "Date added", "cik": "CIK"}
#: The saved file's header, its columns in order.
CSV_FIELDS: Tuple[str, ...] = ("ticker", "name", "cik", "gics_sector", "gics_sub_industry", "date_added")
#: The saved files' folder, relative to the working directory (the repo's root).
UNIVERSE_DIR = Path("universe")
#: A CIK as the page may write it: 1 to 10 digits (ASCII), zero-padded to 10 when read.
CIK_PATTERN = re.compile(r"[0-9]{1,10}")
#: A footnote marker left as text in a cell: "[3]", "[a]", "[note 1]".
MARKER_PATTERN = re.compile(r"\[(?:[0-9]{1,3}|[a-z]{1,2}|note [0-9]{1,3})\]")
#: Elements whose text isn't a cell's: superscripts (on this page, footnote markers), a template's CSS, scripts.
SKIPPED_TAGS = ("sup", "style", "script")
#: Elements without an end tag: they never open a skipped element.
VOID_TAGS = ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
             "wbr")
#: The tags that end a cell, so a skipped element left open ends with it, as a browser closes it.
CELL_BOUNDARIES = ("td", "th", "tr", "table")


@dataclass(frozen=True)
class Member:
    """An S&P 500 member as the saved file has it (CSV_FIELDS): its ticker in Yahoo's form, its name (the page's
    Security), its CIK (10 digits), its GICS sector and sub-industry, the day it was added (ISO, or empty)."""

    ticker: str
    name: str
    cik: str
    gics_sector: str
    gics_sub_industry: str
    date_added: str


def _clean(text: str) -> str:
    """A cell's text without its footnote markers (MARKER_PATTERN), its spaces collapsed."""
    return " ".join(MARKER_PATTERN.sub("", text).split())


@dataclass
class _Table:
    """A table being read: its rows (each its cells' texts), the open row and the open cell's pieces of text."""

    rows: List[List[str]] = field(default_factory=list)
    row: Optional[List[str]] = None
    cell: Optional[List[str]] = None

    def end_cell(self) -> None:
        if self.cell is not None and self.row is not None:
            self.row.append(_clean("".join(self.cell)))
        self.cell = None

    def end_row(self) -> None:
        self.end_cell()
        if self.row:
            self.rows.append(self.row)
        self.row = None


class _TableReader(HTMLParser):
    """The page's tables in document order, each a list of its rows, each row its cells' texts (a th's or a td's):
    a table inside a cell is a table of its own. Text outside a cell is ignored; inside one, SKIPPED_TAGS' and a
    hidden element's text is."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self._open: List[_Table] = []
        self._skip = 0  # the depth inside a skipped element of the open cell (0: not in one)

    def _cell(self) -> Optional[List[str]]:
        return self._open[-1].cell if self._open else None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if self._skip:
            if tag not in CELL_BOUNDARIES:
                self._skip += tag not in VOID_TAGS
                return
            self._skip = 0  # the cell ends, and the skipped element left open with it
        if tag == "table":
            self._open.append(_Table())
            self.tables.append(self._open[-1].rows)
        elif not self._open:
            return
        elif tag == "tr":
            self._open[-1].end_row()
            self._open[-1].row = []
        elif tag in ("td", "th"):
            table = self._open[-1]
            table.end_cell()
            if table.row is None:
                table.row = []
            table.cell = []
        else:
            cell = self._cell()
            style = (dict(attrs).get("style") or "").replace(" ", "").lower()
            if cell is None:
                return
            if tag == "br":
                cell.append(" ")
            elif tag not in VOID_TAGS and (tag in SKIPPED_TAGS or "display:none" in style):
                self._skip = 1

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if self._skip:
            if tag not in CELL_BOUNDARIES:
                self._skip -= 1
                return
            self._skip = 0
        if not self._open:
            return
        table = self._open[-1]
        if tag == "table":
            table.end_row()
            self._open.pop()
        elif tag == "tr":
            table.end_row()
        elif tag in ("td", "th"):
            table.end_cell()

    def handle_data(self, data: str) -> None:
        cell = self._cell()
        if cell is not None and not self._skip:
            cell.append(data)


def _members_table(tables: Sequence[List[List[str]]]) -> Tuple[List[List[str]], Dict[str, int]]:
    """The first table whose header row (its first) has COLUMNS' cells, and each column's place in it (a name's first
    cell). Raises ValueError naming the columns the table nearest to it lacks, or saying none is near."""
    wanted = {name.casefold(): column for column, name in COLUMNS.items()}
    nearest: Tuple[int, List[str]] = (0, [])
    for rows in tables:
        if not rows:
            continue
        found: Dict[str, int] = {}
        for i, text in enumerate(rows[0]):
            if text.casefold() in wanted:
                found.setdefault(wanted[text.casefold()], i)
        if len(found) == len(COLUMNS):
            return rows, found
        if len(found) > nearest[0]:
            nearest = (len(found), rows[0])
    names = ", ".join(repr(name) for name in COLUMNS.values())
    if not nearest[0]:
        raise ValueError(f"the page has no table whose header cells include {names}")
    have = {text.casefold() for text in nearest[1]}
    missing = ", ".join(repr(name) for name in COLUMNS.values() if name.casefold() not in have)
    raise ValueError(f"the page's table nearest to the members' (its header cells {ascii(nearest[1])}) has no "
                     f"{missing} column")


def parse_sp500(html: str) -> List[Member]:
    """The S&P 500's members from Wikipedia's page (*html*), in the page's order (the module's docstring). Raises
    ValueError for a page that changed: no members' table, a column missing, a ticker that isn't one, a CIK that
    isn't 1 to 10 digits, a row without the header's cells, or rows outside SP500_MIN_ROWS to SP500_MAX_ROWS."""
    reader = _TableReader()
    reader.feed(html)
    reader.close()
    rows, columns = _members_table(reader.tables)
    width = len(rows[0])
    members = []
    for number, row in enumerate(rows[1:], start=1):
        if len(row) != width:
            raise ValueError(f"the members' table's row {number} has {len(row)} cells, its header {width}: "
                             f"{ascii(row[:3])}")
        cells = {column: row[i] for column, i in columns.items()}
        try:
            ticker = normalize_ticker(cells["ticker"])
        except ValueError as exc:
            raise ValueError(f"the members' table's row {number} ({ascii(cells['name'])}): {exc}") from None
        if not CIK_PATTERN.fullmatch(cells["cik"]):
            raise ValueError(f"the members' table's row {number} ({ticker}): its CIK {ascii(cells['cik'])} isn't 1 to "
                             "10 digits")
        members.append(Member(ticker, cells["name"], cells["cik"].zfill(10), cells["gics_sector"],
                              cells["gics_sub_industry"], cells["date_added"]))
    if not SP500_MIN_ROWS <= len(members) <= SP500_MAX_ROWS:
        raise ValueError(f"the members' table has {len(members)} rows, not {SP500_MIN_ROWS} to {SP500_MAX_ROWS}: the "
                         "page changed")
    return members


def one_per_cik(members: Sequence[Member]) -> Tuple[List[Member], List[Member]]:
    """*members* with one company a CIK: the first of each CIK, in order, and the others dropped (a company's second
    share class: GOOG after GOOGL), in order."""
    kept: List[Member] = []
    dropped: List[Member] = []
    ciks = set()
    for member in members:
        (dropped if member.cik in ciks else kept).append(member)
        ciks.add(member.cik)
    return kept, dropped


def write_csv(members: Iterable[Member], path: Union[str, Path]) -> None:
    """*members* as a CSV at *path*: CSV_FIELDS' header, then a row a member, UTF-8. Never over an existing file:
    raises FileExistsError for one."""
    with open(path, "x", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for member in members:
            writer.writerow([getattr(member, name) for name in CSV_FIELDS])


def fetch_page(get: Callable[..., Any] = requests.get) -> str:
    """Wikipedia's page (SP500_URL) with one request through *get* (``requests.get``), its User-Agent USER_AGENT and
    TIMEOUT, as text (UTF-8). Raises OSError for an answer other than 200 (``requests``' own errors are OSErrors
    too)."""
    response = get(SP500_URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise OSError(f"{SP500_URL} answered HTTP {response.status_code}")
    return bytes(response.content).decode("utf-8")


def default_out(today: Optional[date] = None) -> Path:
    """The saved file for *today* (the current day by default): ``universe/sp500-<YYYY-MM-DD>.csv``."""
    return UNIVERSE_DIR / f"sp500-{(today or date.today()).isoformat()}.csv"


def summary_lines(members: Sequence[Member], kept: Sequence[Member], dropped: Sequence[Member],
                  out: Path) -> List[str]:
    """The run's summary: the rows read, kept and dropped (each dropped ticker with its CIK and the ticker kept for
    it), and the file written."""
    first = {member.cik: member.ticker for member in kept}
    gone = "; ".join(f"{m.ticker} (CIK {m.cik}, beside {first.get(m.cik, '?')})" for m in dropped) or "none"
    return [f"S&P 500 list: {len(members)} rows read from {SP500_URL}",
            f"One company a CIK: {len(kept)} kept, {len(dropped)} dropped: {gone}",
            f"Wrote {out}"]


def _say(line: str, stream: Any = None) -> None:
    """Print *line* (flushed) to *stream* (stdout) in what it can show: a cp1252 console can't print every
    character."""
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    print(line.encode(encoding, "replace").decode(encoding), file=stream, flush=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Save the S&P 500's members from Wikipedia's list (one request) as a dated CSV in universe/, one "
                    "company a CIK.")
    parser.add_argument("--out", type=Path, default=None, metavar="PATH",
                        help="The CSV to write (default: universe/sp500-<today>.csv); an existing file is never "
                             "overwritten")
    args = parser.parse_args(argv)
    out = args.out or default_out()
    if out.exists():
        _say(f"{out} exists: refusing to overwrite it (delete it, or name another --out); nothing fetched", sys.stderr)
        return 1
    try:
        page = fetch_page()
    except OSError as exc:
        _say(f"Fetching the S&P 500 list failed: {exc}", sys.stderr)
        return 1
    try:
        members = parse_sp500(page)
    except ValueError as exc:
        _say(f"The S&P 500 list didn't read: {exc}; nothing written", sys.stderr)
        return 1
    kept, dropped = one_per_cik(members)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(kept, out)
    for line in summary_lines(members, kept, dropped, out):
        _say(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
