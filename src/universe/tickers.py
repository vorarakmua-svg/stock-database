"""Tickers in Yahoo's form, and the tickers files ``stock-data --tickers-file`` reads (Milestone 5; docs/superpowers/
specs/2026-10-25-universe-automation-design.md, "The universe").

- A ticker is letters and digits (ASCII), with at most one dash or dot between them, upper-cased, a class share's dot
  as a dash: Yahoo's form, ``BRK.B`` as ``BRK-B``, ``BF.B`` as ``BF-B``. Anything else (a space inside, a caret, two
  separators, an empty line's nothing) isn't one, so a typo fails loudly instead of being collected.
- A ``.txt`` file holds a ticker a line, ``#`` starting a comment (a whole line's, or the rest of a line), blank lines
  ignored. A ``.csv`` file's tickers are its ``ticker`` column, else its ``Symbol`` column (its header read without
  case or spaces); its blank rows are skipped. Either's tickers are normalized and each kept once, in the file's
  order (``unique_tickers``). The files are read as UTF-8, a byte-order mark aside.
"""
import csv
import re
from pathlib import Path
from typing import Iterable, List, Tuple, Union

#: A ticker as written: letters and digits (ASCII), with at most one dash or dot between them.
TICKER_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)?")
#: A CSV file's ticker column, by its header (without case or spaces), in the order they are looked for.
CSV_COLUMNS = ("ticker", "symbol")
#: A comment in a .txt file starts at this character and runs to the line's end.
COMMENT = "#"
#: The tickers files' extensions (without case).
TXT, CSV = ".txt", ".csv"


def normalize_ticker(text: str) -> str:
    """*text* as a ticker in Yahoo's form: stripped, upper-cased, a class share's dot as a dash. Raises ValueError
    for a text that isn't a ticker (TICKER_PATTERN)."""
    ticker = text.strip()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError(f"not a ticker: {ascii(text.strip())} (letters and digits, with at most one dash or dot "
                         "between them)")
    return ticker.upper().replace(".", "-")


def unique_tickers(texts: Iterable[str]) -> List[str]:
    """*texts* normalized (``normalize_ticker``), each ticker once, in order of its first appearance."""
    seen = set()
    tickers = []
    for text in texts:
        ticker = normalize_ticker(text)
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def _txt_lines(path: Path) -> List[Tuple[int, str]]:
    """A .txt file's (line number, ticker text) pairs: each line without its comment, its blank lines left out."""
    with open(path, encoding="utf-8-sig") as f:
        lines = [(number, line.split(COMMENT, 1)[0].strip()) for number, line in enumerate(f, start=1)]
    return [(number, text) for number, text in lines if text]


def _csv_lines(path: Path) -> List[Tuple[int, str]]:
    """A .csv file's (line number, ticker text) pairs: its ticker column's cells (CSV_COLUMNS), its blank rows left
    out. Raises ValueError for a file without a ticker or Symbol column."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next((row for row in reader if any(cell.strip() for cell in row)), [])
        names = [cell.strip().casefold() for cell in header]
        column = next((names.index(name) for name in CSV_COLUMNS if name in names), None)
        if column is None:
            raise ValueError(f"{path}: a CSV of tickers needs a ticker or Symbol column; its header is "
                             f"{ascii(','.join(header)[:120])}")
        lines = []
        for row in reader:
            if any(cell.strip() for cell in row):
                lines.append((reader.line_num, row[column] if column < len(row) else ""))
    return lines


def read_tickers_file(path: Union[str, Path]) -> List[str]:
    """The tickers a ``.txt`` or a ``.csv`` file lists (the module's docstring), normalized and each kept once, in the
    file's order. Raises ValueError for a file of another extension, a CSV without a ticker or Symbol column, or a
    line whose ticker isn't one (naming the file and the line), OSError for a file that can't be read."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == TXT:
        lines = _txt_lines(path)
    elif suffix == CSV:
        lines = _csv_lines(path)
    else:
        raise ValueError(f"{path}: a tickers file is a .txt or a .csv, not {ascii(path.suffix or 'no extension')}")
    tickers = []
    for number, text in lines:
        try:
            tickers.append(normalize_ticker(text))
        except ValueError as exc:
            raise ValueError(f"{path}, line {number}: {exc}") from None
    return unique_tickers(tickers)
