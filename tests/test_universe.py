"""The universe (``src/universe/tickers.py`` and ``src/universe/sp500.py``; docs/superpowers/specs/
2026-10-25-universe-automation-design.md, "The universe"): tickers in Yahoo's form (a class share's dot as a dash,
anything else refused); the tickers files (a .txt with its comments and blank lines, a .csv's ticker or Symbol
column), deduplicated in order; Wikipedia's S&P 500 table read from a synthetic page shaped like it (a wikitable with
the page's header cells, links in the cells, footnote markers, entities, a template's CSS, hidden text; no copy of the
page), the first table with the header cells, its columns in any order, its bounds and its failures; one company a
CIK; the saved CSV; the fetch (one request, the app's User-Agent without an email) and the CLI, whose fetch is a fake.
No test touches the network.
"""
import csv
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.universe import sp500
from src.universe.sp500 import (
    CSV_FIELDS,
    SP500_MAX_ROWS,
    SP500_MIN_ROWS,
    SP500_URL,
    TIMEOUT,
    USER_AGENT,
    Member,
    fetch_page,
    one_per_cik,
    parse_sp500,
    write_csv,
)
from src.universe.tickers import normalize_ticker, read_tickers_file, unique_tickers

REPO = Path(__file__).resolve().parents[1]

# ---- tickers ------------------------------------------------------------------------------------------------------


def test_a_class_shares_dot_is_a_dash():
    assert normalize_ticker("BRK.B") == "BRK-B"
    assert normalize_ticker("BF.B") == "BF-B"
    assert normalize_ticker("BRK-B") == "BRK-B"


def test_a_ticker_is_upper_cased_and_stripped():
    assert normalize_ticker("aapl") == "AAPL"
    assert normalize_ticker("  brk.b \t") == "BRK-B"
    assert normalize_ticker("\tX001\r\n") == "X001"
    assert normalize_ticker("7203") == "7203"


@pytest.mark.parametrize("text", ["", "   ", "BRK B", "BRK..B", "BRK.B.C", "BRK-B-C", "BRK.-B", ".B", "BRK.", "-B",
                                  "BRK-", "^GSPC", "AAPL!", "AAPL,MSFT", "\u00c4BC", "stra\u00dfe", "\ufb01", "A_B"])
def test_anything_else_isnt_a_ticker(text):
    with pytest.raises(ValueError, match="not a ticker"):
        normalize_ticker(text)


def test_unique_tickers_are_normalized_each_once_in_order():
    assert unique_tickers(["msft", "AAPL", "MSFT", "brk.b", "BRK-B", "aapl"]) == ["MSFT", "AAPL", "BRK-B"]
    assert unique_tickers([]) == []


def _write(path, text, encoding="utf-8"):
    path.write_bytes(text.encode(encoding))
    return path


def test_a_txt_file_holds_a_ticker_a_line_with_its_comments_and_blank_lines_ignored(tmp_path):
    path = _write(tmp_path / "list.txt", "# The core list\n\nAAPL\n  msft  \nBRK.B  # Berkshire's class B\n"
                                         "   # GOOG is commented out\n#GOOG\n\t\nNVDA#no space before it\n")
    assert read_tickers_file(path) == ["AAPL", "MSFT", "BRK-B", "NVDA"]


def test_a_txt_files_crlf_and_byte_order_mark_are_read(tmp_path):
    path = _write(tmp_path / "list.txt", "\ufeffAAPL\r\nmsft\r\n\r\n# end\r\n")
    assert read_tickers_file(path) == ["AAPL", "MSFT"]


def test_a_files_tickers_are_deduplicated_in_file_order(tmp_path):
    path = _write(tmp_path / "list.txt", "MSFT\nAAPL\nmsft\nBRK-B\nbrk.b\nAAPL\nGOOGL\n")
    assert read_tickers_file(path) == ["MSFT", "AAPL", "BRK-B", "GOOGL"]


def test_a_csv_files_ticker_column(tmp_path):
    path = _write(tmp_path / "top.csv", "rank,ticker,company,sector\n1,NVDA,NVIDIA Corporation,Technology\n"
                                        "2,brk.b,\"Berkshire Hathaway, Inc.\",Financials\n3,NVDA,again,Technology\n"
                                        "4, aapl ,Apple Inc.,Technology\n")
    assert read_tickers_file(path) == ["NVDA", "BRK-B", "AAPL"]


def test_a_csv_files_symbol_column_without_a_ticker_column(tmp_path):
    path = _write(tmp_path / "members.csv", "Symbol,Security,CIK\nMMM,3M,0000066740\nBF.B,Brown-Forman,0000014693\n")
    assert read_tickers_file(path) == ["MMM", "BF-B"]


def test_a_csv_files_ticker_column_comes_before_its_symbol_column(tmp_path):
    path = _write(tmp_path / "both.csv", "Symbol,Ticker\nAAA,BBB\nCCC,DDD\n")
    assert read_tickers_file(path) == ["BBB", "DDD"]


def test_a_csv_files_header_is_read_without_its_case_and_spaces(tmp_path):
    assert read_tickers_file(_write(tmp_path / "a.csv", "TICKER,Name\naapl,Apple\n")) == ["AAPL"]
    assert read_tickers_file(_write(tmp_path / "b.csv", "name, symbol \nMicrosoft,msft\n")) == ["MSFT"]
    assert read_tickers_file(_write(tmp_path / "c.csv", "\ufeffTicker,Name\r\nNVDA,NVIDIA\r\n")) == ["NVDA"]


def test_a_csv_files_blank_rows_are_skipped(tmp_path):
    path = _write(tmp_path / "gaps.csv", "ticker,name\nAAPL,Apple\n\n,,\n , \nMSFT,Microsoft\n,,\n")
    assert read_tickers_file(path) == ["AAPL", "MSFT"]


def test_a_csv_without_a_ticker_or_symbol_column_raises(tmp_path):
    with pytest.raises(ValueError, match="ticker.*Symbol"):
        read_tickers_file(_write(tmp_path / "none.csv", "name,cik\nApple,320193\n"))
    with pytest.raises(ValueError, match="ticker.*Symbol"):
        read_tickers_file(_write(tmp_path / "empty.csv", ""))


def test_a_ticker_that_isnt_one_raises_naming_the_file_and_its_line(tmp_path):
    with pytest.raises(ValueError, match=r"bad\.txt, line 3: not a ticker: 'BRK B'"):
        read_tickers_file(_write(tmp_path / "bad.txt", "AAPL\n# fine\nBRK B\n"))
    with pytest.raises(ValueError, match=r"bad\.csv, line 3: not a ticker: '\^GSPC'"):
        read_tickers_file(_write(tmp_path / "bad.csv", "ticker\nAAPL\n^GSPC\n"))
    with pytest.raises(ValueError, match=r"blank\.csv, line 2: not a ticker: ''"):
        read_tickers_file(_write(tmp_path / "blank.csv", "ticker,name\n,Apple\n"))


def test_the_extension_is_read_without_its_case(tmp_path):
    assert read_tickers_file(_write(tmp_path / "LIST.TXT", "aapl\n")) == ["AAPL"]
    assert read_tickers_file(_write(tmp_path / "LIST.CSV", "ticker\nmsft\n")) == ["MSFT"]


@pytest.mark.parametrize("name", ["tickers.json", "tickers.xlsx", "tickers", "tickers.txt.bak"])
def test_a_file_that_isnt_a_txt_or_a_csv_raises(tmp_path, name):
    with pytest.raises(ValueError, match="a .txt or a .csv"):
        read_tickers_file(_write(tmp_path / name, "AAPL\n"))


def test_a_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        read_tickers_file(tmp_path / "missing.txt")


def test_the_repos_core_list_reads_as_tickers_sorted_each_once():
    path = REPO / "universe" / "core.txt"
    tickers = read_tickers_file(path)
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert tickers == lines == sorted(lines)
    assert {"AAPL", "BRK-B", "GOOGL", "XOM"} <= set(tickers)


def test_the_repos_sp500_lists_read_as_tickers_files_one_company_a_cik():
    files = sorted((REPO / "universe").glob("sp500-*.csv"))
    assert files
    for path in files:
        assert re.fullmatch(r"sp500-\d{4}-\d{2}-\d{2}\.csv", path.name)
        with open(path, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        assert tuple(rows[0]) == CSV_FIELDS
        body = rows[1:]
        assert 400 < len(body) <= SP500_MAX_ROWS
        ciks = [row[2] for row in body]
        assert all(re.fullmatch(r"[0-9]{10}", cik) for cik in ciks) and len(set(ciks)) == len(ciks)
        assert read_tickers_file(path) == [row[0] for row in body]
        assert {"BRK-B", "BF-B", "GOOGL"} <= {row[0] for row in body}
        assert not {"BRK.B", "BF.B", "GOOG"} & {row[0] for row in body}


# ---- the S&P 500's table ------------------------------------------------------------------------------------------

#: The members' table's header cells on Wikipedia's page, with the two columns the parser ignores.
HEADER = ("Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Headquarters Location", "Date added", "CIK",
          "Founded")


def _footnote(n):
    """A citation's marker as the page writes it."""
    return f'<sup id="cite_ref-{n}" class="reference"><a href="#cite_note-{n}">&#91;{n}&#93;</a></sup>'


#: A header cell's HTML, where the page's isn't its name alone: a footnote, a link.
HEADER_HTML = {"Date added": "Date added" + _footnote(1),
               "CIK": '<a href="/wiki/Central_Index_Key" title="Central Index Key">CIK</a>'}


def _member(symbol, security, cik, sector="Industrials", sub="Industrial Conglomerates", added="2000-01-01",
            hq="Springfield, Ohio", founded="1900"):
    """A member's cells (HTML) by header: the symbol linked to its quote, the security and the city to articles."""
    return {"Symbol": f'<a rel="nofollow" class="external text" href="https://www.nyse.com/quote/XNYS:{symbol}">'
                      f'{symbol}</a>',
            "Security": f'<a href="/wiki/Article" title="Article">{security}</a>',
            "GICS Sector": sector, "GICS Sub-Industry": sub,
            "Headquarters Location": f'<a href="/wiki/City" title="City">{hq}</a>',
            "Date added": added, "CIK": cik, "Founded": founded}


def _filler(i):
    return _member(f"X{i:03d}", f"Filler Company {i}", f"{900000 + i:010d}", added="2020-01-01")


#: The interesting members: what each tests is beside it.
SPECIAL = [
    _member("MMM", "3M", "0000066740", added="1957-03-04", hq="Saint Paul, Minnesota", founded="1902"),
    _member("T", "AT&amp;T", "732717", sector="Communication Services",  # an entity; a CIK without its zeros
            sub="Integrated Telecommunication Services", added="1983-11-30"),
    _member("BRK.B", "Berkshire Hathaway" + _footnote(3), "0001067983", sector="Financials",  # a dot; a footnote
            sub="Multi-Sector Holdings", added="2010-02-16"),
    _member("BF.B", "Brown&#8211;Forman", "0000014693", sector="Consumer Staples",  # a numeric entity
            sub="Distillers &amp; Vintners", added="1982-10-31[a]"),  # a footnote marker as text
    _member("GOOGL", "Alphabet Inc. (Class A)", "0001652044", sector="Communication Services",
            sub="Interactive Media &amp; Services", added="2014-04-03"),
    _member("GOOG", "Alphabet Inc. (Class C)", "0001652044", sector="Communication Services",
            sub="Interactive Media &amp; Services", added="2006-04-03"),
    _member("ABT", '<style data-mw-deduplicate="TemplateStyles:r1">.mw-parser-output .x{color:red}</style>'
                   "Abbott Laboratories", "0000001800", sector="Health Care",  # a template's CSS in the cell
            sub="Health Care<br>Equipment", added="1957-03-04"),  # a line break
    _member("XYZ", 'Block,&nbsp;Inc.<sup class="noprint Inline-Template">&#91;<i><a href="/wiki/Citation_needed">'
                   "citation needed</a></i>&#93;</sup>", "0001512673", sector="Financials",  # a [citation needed]
            sub="Transaction &amp; Payment Processing Services", added=""),  # an empty date
    _member("FOXA", "Fox Corporation (Class A)", "0001754301", sector="Communication Services", sub="Broadcasting",
            added="2013-07-01"),
    _member("FOX", "Fox Corporation (Class B)", "0001754301", sector="Communication Services", sub="Broadcasting",
            added="2015-09-18"),
    _member("NWSA", "News Corp (Class A)", "0001564708", sector="Communication Services", sub="Publishing",
            added="2013-08-01"),
    _member("NWS", "News Corp (Class B)", "0001564708", sector="Communication Services", sub="Publishing",
            added="2015-09-18"),
    _member("ZTS", '<span style="display: none">00000Sort key</span>Zoetis', "0001555280",  # hidden text
            sector="Health Care", sub="Pharmaceuticals", added="2013-06-21"),
]

#: SPECIAL as parsed, in the page's order.
EXPECTED = [
    Member("MMM", "3M", "0000066740", "Industrials", "Industrial Conglomerates", "1957-03-04"),
    Member("T", "AT&T", "0000732717", "Communication Services", "Integrated Telecommunication Services", "1983-11-30"),
    Member("BRK-B", "Berkshire Hathaway", "0001067983", "Financials", "Multi-Sector Holdings", "2010-02-16"),
    Member("BF-B", "Brown\u2013Forman", "0000014693", "Consumer Staples", "Distillers & Vintners", "1982-10-31"),
    Member("GOOGL", "Alphabet Inc. (Class A)", "0001652044", "Communication Services", "Interactive Media & Services",
           "2014-04-03"),
    Member("GOOG", "Alphabet Inc. (Class C)", "0001652044", "Communication Services", "Interactive Media & Services",
           "2006-04-03"),
    Member("ABT", "Abbott Laboratories", "0000001800", "Health Care", "Health Care Equipment", "1957-03-04"),
    Member("XYZ", "Block, Inc.", "0001512673", "Financials", "Transaction & Payment Processing Services", ""),
    Member("FOXA", "Fox Corporation (Class A)", "0001754301", "Communication Services", "Broadcasting", "2013-07-01"),
    Member("FOX", "Fox Corporation (Class B)", "0001754301", "Communication Services", "Broadcasting", "2015-09-18"),
    Member("NWSA", "News Corp (Class A)", "0001564708", "Communication Services", "Publishing", "2013-08-01"),
    Member("NWS", "News Corp (Class B)", "0001564708", "Communication Services", "Publishing", "2015-09-18"),
    Member("ZTS", "Zoetis", "0001555280", "Health Care", "Pharmaceuticals", "2013-06-21"),
]


def _row(cells, header=HEADER, closed=True):
    """A table row: each header's cell from *cells* (HTML), empty for one it lacks; *closed* False leaves out the
    cells' and the row's end tags, as HTML allows."""
    end_cell, end_row = ("</td>", "</tr>") if closed else ("", "")
    return "<tr>\n" + "".join(f"<td>{cells.get(name, '')}{end_cell}\n" for name in header) + end_row + "\n"


def _table(members=(), rows=500, header=HEADER, raw_rows=(), closed=True, labels=None,
           attrs='class="wikitable sortable"'):
    """A members' table: *header*'s cells (each shown as *labels* has it, else as HEADER_HTML or its name), then
    *members* and fillers up to *rows* rows, then *raw_rows* (HTML)."""
    fillers = [_filler(i) for i in range(rows - len(members))]
    shown = dict(HEADER_HTML, **(labels or {}))
    head = "".join(f"<th>{shown.get(name, name)}</th>\n" for name in header)
    body = "".join(_row(cells, header, closed) for cells in [*members, *fillers])
    return f"<table {attrs}>\n<tbody><tr>\n{head}</tr>\n{body}{''.join(raw_rows)}</tbody></table>\n"


def _page(members=(), rows=500, header=HEADER, raw_rows=(), closed=True, labels=None, before="", after=""):
    """A page shaped like Wikipedia's: an infobox table, then the members' table (``_table``), then the table of
    changes, whose two header rows have no Symbol; *before* and *after* go around the members' table."""
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="UTF-8"><title>List of S&amp;P 500 companies - '
            "Wikipedia</title><style>.mw-parser-output table{width:100%}</style><script>var x = '<td>';</script>"
            '</head>\n<body><div class="mw-parser-output">\n'
            '<table class="infobox"><tbody><tr><th>Type</th><td>Stock market index</td></tr>'
            "<tr><th>Founded</th><td>1957</td></tr></tbody></table>\n<p>The S&amp;P 500 ...</p>\n"
            + before
            + _table(members, rows, header, raw_rows, closed, labels,
                     attrs='class="wikitable sortable sticky-header" id="constituents"')
            + after
            + '<h2>Selected changes</h2>\n<table class="wikitable" id="changes"><tbody>'
              '<tr><th rowspan="2">Effective Date</th><th colspan="2">Added</th><th colspan="2">Removed</th>'
              '<th rowspan="2">Reason</th></tr>\n<tr><th>Ticker</th><th>Security</th><th>Ticker</th>'
              "<th>Security</th></tr>\n<tr><td>October 1, 2026</td><td>NEW</td><td>New Co</td><td>OLD</td>"
              "<td>Old Co</td><td>Market capitalization change.</td></tr>\n</tbody></table>\n"
              "</div></body></html>\n")


def test_the_members_table_is_read_with_its_links_footnotes_entities_css_and_hidden_text_handled():
    members = parse_sp500(_page(SPECIAL))
    assert len(members) == 500
    assert members[:len(SPECIAL)] == EXPECTED
    assert members[len(SPECIAL)] == Member("X000", "Filler Company 0", "0000900000", "Industrials",
                                           "Industrial Conglomerates", "2020-01-01")


def test_the_columns_are_found_by_their_header_cells_in_any_order_the_others_ignored():
    shuffled = ("CIK", "Founded", "Date added", "GICS Sub-Industry", "Security", "Headquarters Location", "Symbol",
                "GICS Sector")
    assert parse_sp500(_page(SPECIAL, header=shuffled)) == parse_sp500(_page(SPECIAL))
    lean = ("Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Date added", "CIK")
    assert parse_sp500(_page(SPECIAL, header=lean))[:len(SPECIAL)] == EXPECTED


def test_the_header_cells_are_read_without_their_case():
    labels = {"Symbol": "SYMBOL", "GICS Sector": "GICS sector", "GICS Sub-Industry": "GICS sub-industry",
              "Date added": "Date Added", "CIK": "cik"}
    members = parse_sp500(_page(SPECIAL, labels=labels))
    assert members[:len(SPECIAL)] == EXPECTED and members == parse_sp500(_page(SPECIAL))


def test_cells_without_their_end_tags_are_read_as_html_allows():
    members = parse_sp500(_page(SPECIAL, closed=False))
    assert members[:len(SPECIAL)] == EXPECTED and members == parse_sp500(_page(SPECIAL))


def test_a_footnote_left_open_ends_with_its_cell():
    unclosed = dict(_member("MMM", "3M", "0000066740"), Security='3M<sup class="reference"><a href="#n">[1]</a>')
    expected = [Member("MMM", "3M", "0000066740", "Industrials", "Industrial Conglomerates", "2000-01-01")]
    assert parse_sp500(_page([unclosed]))[:1] == expected
    assert parse_sp500(_page([unclosed], closed=False))[:1] == expected  # the next cell's start ends both
    stray = ('<tr><td>MMM</td><td>3M<sup class="reference">[1]</td>stray text<td>Industrials</td>'
             "<td>Industrial Conglomerates</td><td>Saint Paul</td><td>2000-01-01</td><td>66740</td><td>1902</td></tr>")
    assert parse_sp500(_page(rows=499, raw_rows=[stray]))[-1:] == expected  # text between cells is no cell's


def test_void_elements_neither_open_nor_close_a_skipped_element():
    cells = dict(_member("MMM", "3M", "0000066740"),
                 Security='3M<sup class="reference">[1]<br><img src="a.png"><br/>note</sup> Company'
                          '<img style="display:none" src="b.png"> Ltd')
    assert parse_sp500(_page([cells]))[0].name == "3M Company Ltd"


def test_the_first_table_with_the_header_cells_is_read():
    other = _table([_member("AAA", "Another", "0000000001")], rows=500)
    assert parse_sp500(_page(SPECIAL, after=other))[:len(SPECIAL)] == EXPECTED
    assert parse_sp500(_page(SPECIAL, before=other))[0].ticker == "AAA"


def test_a_page_without_the_members_table_raises():
    with pytest.raises(ValueError, match="no table"):
        parse_sp500("<html><body><p>Moved.</p></body></html>")
    with pytest.raises(ValueError, match="no table"):
        parse_sp500('<table><tr><th>Type</th><td>Stock market index</td></tr></table>')


@pytest.mark.parametrize("missing", ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Date added", "CIK"])
def test_a_missing_column_raises_naming_it(missing):
    with pytest.raises(ValueError, match=f"no '{missing}' column"):
        parse_sp500(_page(SPECIAL, header=tuple(name for name in HEADER if name != missing)))


@pytest.mark.parametrize("cik", ["66740a", "", "0000066740 1", "12345678901", "\u0666\u0666\u0667\u0664\u0660",
                                 "-66740"])
def test_a_cik_that_isnt_ten_digits_at_most_raises(cik):
    with pytest.raises(ValueError, match="CIK"):
        parse_sp500(_page([_member("MMM", "3M", cik)]))


def test_a_ticker_that_isnt_one_raises():
    with pytest.raises(ValueError, match="not a ticker: 'BRK B'"):
        parse_sp500(_page([_member("BRK B", "Berkshire Hathaway", "0001067983")]))


def test_a_row_without_the_headers_cells_raises():
    with pytest.raises(ValueError, match="2 cells"):
        parse_sp500(_page(rows=499, raw_rows=["<tr><td>ZZZ</td><td>Short Row</td></tr>"]))
    with pytest.raises(ValueError, match="9 cells"):
        parse_sp500(_page(rows=499, raw_rows=[_row(dict(_filler(999), Extra="x"), HEADER + ("Extra",))]))


def test_a_row_without_cells_isnt_a_member():
    assert len(parse_sp500(_page(rows=500, raw_rows=["<tr></tr>\n"]))) == 500


@pytest.mark.parametrize("rows", [SP500_MIN_ROWS, SP500_MAX_ROWS])
def test_a_table_within_the_bounds_is_read(rows):
    assert (SP500_MIN_ROWS, SP500_MAX_ROWS) == (480, 520)
    assert len(parse_sp500(_page(rows=rows))) == rows


@pytest.mark.parametrize("rows", [0, SP500_MIN_ROWS - 1, SP500_MAX_ROWS + 1])
def test_a_table_outside_the_bounds_raises(rows):
    with pytest.raises(ValueError, match=f"{rows} rows"):
        parse_sp500(_page(rows=rows))


# ---- one company a CIK, the CSV -----------------------------------------------------------------------------------


def test_one_company_a_cik_keeps_the_first_of_each():
    kept, dropped = one_per_cik(EXPECTED)
    assert [m.ticker for m in kept] == ["MMM", "T", "BRK-B", "BF-B", "GOOGL", "ABT", "XYZ", "FOXA", "NWSA", "ZTS"]
    assert [m.ticker for m in dropped] == ["GOOG", "FOX", "NWS"]
    flipped, gone = one_per_cik([EXPECTED[5], EXPECTED[4]])
    assert ([m.ticker for m in flipped], [m.ticker for m in gone]) == (["GOOG"], ["GOOGL"])
    assert one_per_cik([]) == ([], [])


def test_the_csv_has_its_header_and_a_row_a_member(tmp_path):
    path = tmp_path / "sp500.csv"
    write_csv(EXPECTED[:3] + EXPECTED[3:4] + EXPECTED[7:8], path)
    assert CSV_FIELDS == ("ticker", "name", "cik", "gics_sector", "gics_sub_industry", "date_added")
    assert path.read_bytes().decode("utf-8") == (
        "ticker,name,cik,gics_sector,gics_sub_industry,date_added\r\n"
        "MMM,3M,0000066740,Industrials,Industrial Conglomerates,1957-03-04\r\n"
        "T,AT&T,0000732717,Communication Services,Integrated Telecommunication Services,1983-11-30\r\n"
        "BRK-B,Berkshire Hathaway,0001067983,Financials,Multi-Sector Holdings,2010-02-16\r\n"
        "BF-B,Brown\u2013Forman,0000014693,Consumer Staples,Distillers & Vintners,1982-10-31\r\n"
        'XYZ,"Block, Inc.",0001512673,Financials,Transaction & Payment Processing Services,\r\n')
    assert read_tickers_file(path) == ["MMM", "T", "BRK-B", "BF-B", "XYZ"]


def test_the_csv_is_never_written_over_an_existing_file(tmp_path):
    path = tmp_path / "sp500.csv"
    path.write_text("kept\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_csv(EXPECTED, path)
    assert path.read_text(encoding="utf-8") == "kept\n"


# ---- the fetch and the CLI ----------------------------------------------------------------------------------------


class _Get:
    """A fake ``requests.get``: records each call and answers *status* with *content*."""

    def __init__(self, status=200, content=b"<html></html>"):
        self.status, self.content, self.calls = status, content, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return SimpleNamespace(status_code=self.status, content=self.content)


def test_the_page_is_fetched_with_one_request_and_the_apps_user_agent_without_an_email():
    get = _Get(content="<p>Brown\u2013Forman</p>".encode("utf-8"))
    assert fetch_page(get) == "<p>Brown\u2013Forman</p>"
    assert get.calls == [("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                          {"headers": {"User-Agent": USER_AGENT}, "timeout": 30})]
    assert SP500_URL == get.calls[0][0] and TIMEOUT == 30
    assert "StockDataCollector" in USER_AGENT and "@" not in USER_AGENT


@pytest.mark.parametrize("status", [403, 429, 500, 301])
def test_an_answer_other_than_200_raises_after_its_one_request(status):
    get = _Get(status=status)
    with pytest.raises(OSError, match=f"HTTP {status}"):
        fetch_page(get)
    assert len(get.calls) == 1


def _fake_fetch(monkeypatch, page):
    """The CLI's fetch answering *page* (or raising it, an exception); returns the calls made."""
    calls = []

    def fetch():
        calls.append(1)
        if isinstance(page, BaseException):
            raise page
        return page

    monkeypatch.setattr(sp500, "fetch_page", fetch)
    return calls


def test_the_cli_writes_the_dated_file_in_the_universe_folder_and_prints_a_summary(tmp_path, monkeypatch, capsys):
    calls = _fake_fetch(monkeypatch, _page(SPECIAL, rows=503))
    monkeypatch.chdir(tmp_path)
    days = {date.today().isoformat()}
    assert sp500.main([]) == 0
    days.add(date.today().isoformat())
    written = [tmp_path / "universe" / f"sp500-{day}.csv" for day in days if (tmp_path / "universe" /
                                                                               f"sp500-{day}.csv").exists()]
    assert len(written) == 1 and calls == [1]
    rows = list(csv.reader(written[0].read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 1 + 500 and [row[0] for row in rows[1:11]] == ["MMM", "T", "BRK-B", "BF-B", "GOOGL", "ABT",
                                                                       "XYZ", "FOXA", "NWSA", "ZTS"]
    out = capsys.readouterr().out
    assert "503 rows read" in out and "500 kept" in out
    assert "3 dropped" in out and "GOOG (" in out and "FOX (" in out and "NWS (" in out
    assert str(Path("universe") / written[0].name) in out


def test_the_cli_writes_the_file_out_names_making_its_folder(tmp_path, monkeypatch, capsys):
    _fake_fetch(monkeypatch, _page(SPECIAL))
    out = tmp_path / "new" / "folder" / "list.csv"
    assert sp500.main(["--out", str(out)]) == 0
    assert read_tickers_file(out)[:3] == ["MMM", "T", "BRK-B"] and len(read_tickers_file(out)) == 497


def test_the_cli_refuses_to_overwrite_a_file_and_fetches_nothing(tmp_path, monkeypatch, capsys):
    calls = _fake_fetch(monkeypatch, _page(SPECIAL))
    out = tmp_path / "sp500-2026-10-25.csv"
    out.write_text("the earlier list\n", encoding="utf-8")
    assert sp500.main(["--out", str(out)]) == 1
    assert calls == [] and out.read_text(encoding="utf-8") == "the earlier list\n"
    assert "refusing to overwrite" in capsys.readouterr().err


def test_the_cli_reports_a_refused_fetch_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _fake_fetch(monkeypatch, OSError(f"{SP500_URL} answered HTTP 403"))
    out = tmp_path / "sp500.csv"
    assert sp500.main(["--out", str(out)]) == 1
    assert not out.exists() and "HTTP 403" in capsys.readouterr().err


def test_the_cli_reports_a_page_that_changed_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _fake_fetch(monkeypatch, _page(SPECIAL, rows=300))
    out = tmp_path / "sp500.csv"
    assert sp500.main(["--out", str(out)]) == 1
    assert not out.exists() and "300 rows" in capsys.readouterr().err
