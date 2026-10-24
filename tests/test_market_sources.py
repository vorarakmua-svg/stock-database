"""The market data's sources, cache, storage and CLI (``src/market/sources.py``, ``src/market/collect.py`` and
``SQLiteStore.export_market_series``; docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md, "The market
data"): FRED's CSV, Shiller's workbook (its Data sheet read by the header labels, its link an https one),
Treasury.gov's yearly CSVs (a file without its yields a failed answer); each answer cached for a day (a past Treasury
year once it has ended), a failed fetch falling back to the cached copy, listed as stale; each source's series
replaced (the Treasury's from its first year on) in one transaction, a failing source reported while the others go
on; the session's own User-Agent and timeout. No test touches the network: the sessions are fakes, and the Shiller
workbooks are written by the tests' own writer.
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace

import pytest
import requests

import src.config
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers.rate_limiter import RateLimiter, RetryHandler
from src.market import collect, sources
from src.market.sources import (
    DAY,
    SHILLER_PAGE_URL,
    MarketFetcher,
    MarketFetchError,
    fetch_fred,
    fetch_shiller,
    fetch_treasury,
    find_xls_link,
    fred_url,
    parse_fred_csv,
    parse_shiller,
    parse_treasury_csv,
    treasury_url,
    year_end,
)
from src.market.xls import XLSError
from tests import market_xls_writer as w

#: The tests' clock: noon UTC on 2026-10-24.
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc).timestamp()


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    """The fetchers ``collect_market`` builds wait for nothing: no rate limit, no backoff."""
    monkeypatch.setattr(sources, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(sources, "RETRY_DELAY", 0.0)


class _Response:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self.content = content

    @property
    def text(self):
        return self.content.decode("utf-8")


class _Session:
    """Answers each GET from *answers* (url -> bytes, a response, or an exception to raise); an unknown URL is a 404.
    Records each URL asked, and the keyword arguments each GET was given."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.asked = []
        self.kwargs = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        self.kwargs.append(kwargs)
        answer = self.answers.get(url, _Response(404))
        if isinstance(answer, BaseException):
            raise answer
        return _Response(200, answer) if isinstance(answer, bytes) else answer


def _fetcher(tmp_path, answers=None, now=NOW):
    """A fetcher on a fake session and clock, without waits; also its session and the lines it logged."""
    session, lines = _Session(answers), []
    fetcher = MarketFetcher(tmp_path / "cache", session=session, clock=lambda: now, log=lines.append,
                            limiter=RateLimiter(min_interval=0), retry=RetryHandler(max_retries=1, base_delay=0))
    return fetcher, session, lines


def _cache(tmp_path, name, content, at):
    """A cached copy of *name* holding *content*, fetched at *at* (its mtime)."""
    path = tmp_path / "cache" / "market" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (at, at))
    return path


# ---- FRED ---------------------------------------------------------------------------------------------------------

FRED_EQUITIES = b"observation_date,NCBEILQ027S\n2026-01-01,80000000.0\n2026-04-01,83050000.0\n"
FRED_GDP = b"observation_date,GDP\n2026-01-01,32000.0\n2026-04-01,32560.0\n2026-07-01,.\n"


def test_fred_csv_in_either_header_with_its_missing_values_skipped():
    # A missing value is "." in older files and an empty cell in today's (NCBEILQ027S on 2026-10-24).
    today = "observation_date,GDP\n1947-01-01,243.164\n1947-04-01,\n1947-07-01,250.1\n1947-10-01,.\n"
    older = "DATE,GDP\r\n1947-07-01,250.1\r\n1947-04-01,.\r\n1947-01-01,243.164\r\n"
    assert parse_fred_csv(today) == parse_fred_csv(older) == [("1947-01-01", 243.164), ("1947-07-01", 250.1)]
    assert parse_fred_csv("\ufeffobservation_date,GDP\n") == []


def test_a_fred_answer_that_isnt_its_csv_raises():
    for text in ("<!DOCTYPE html><html><body>Error</body></html>", "",
                 "observation_date,GDP\n1947-01-01,n/a\n", "observation_date,GDP\n01/01/1947,243.1\n"):
        with pytest.raises(ValueError):
            parse_fred_csv(text)


# ---- Treasury.gov -------------------------------------------------------------------------------------------------

TREASURY_HEADER = ('Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr",'
                   '"20 Yr","30 Yr"')
#: Newest first, as Treasury.gov lists a year; January 2's 1.5-month bill is blank.
TREASURY_2026 = "\n".join([TREASURY_HEADER,
                           "10/23/2026,4.30,4.29,4.28,4.25,4.20,4.10,3.95,3.60,3.55,3.70,3.90,4.15,4.60,4.70",
                           "01/02/2026,4.40,,4.38,4.33,4.30,4.20,4.05,3.70,3.65,3.80,4.00,4.19,4.65,4.75"]) + "\n"
TREASURY_2025 = "\n".join([TREASURY_HEADER,
                           "12/31/2025,4.35,4.34,4.33,4.31,4.28,4.18,4.00,3.65,3.60,3.75,3.95,4.18,4.62,4.72"]) + "\n"


def test_treasury_csv_one_series_per_maturity_oldest_first():
    parsed = parse_treasury_csv(TREASURY_2026)
    assert parsed["1m"] == [("2026-01-02", 4.40), ("2026-10-23", 4.30)]
    assert parsed["6w"] == [("2026-10-23", 4.29)]  # the blank skipped
    assert {name: rows[-1][1] for name, rows in parsed.items()} == {
        "1m": 4.30, "6w": 4.29, "2m": 4.28, "3m": 4.25, "4m": 4.20, "6m": 4.10, "1y": 3.95, "2y": 3.60, "3y": 3.55,
        "5y": 3.70, "7y": 3.90, "10y": 4.15, "20y": 4.60, "30y": 4.70}
    assert parsed["10y"] == [("2026-01-02", 4.19), ("2026-10-23", 4.15)]


def test_an_older_years_columns_and_an_unknown_column():
    text = ('Date,"3 Mo","Extra","2 Yr","10 Yr","30 Yr"\n12/31/1990,6.64,9.99,7.47,8.08,8.26\n'
            '01/02/1990,7.83,1.0,7.87,7.94,8.00\n')
    assert parse_treasury_csv(text) == {"3m": [("1990-01-02", 7.83), ("1990-12-31", 6.64)],
                                        "2y": [("1990-01-02", 7.87), ("1990-12-31", 7.47)],
                                        "10y": [("1990-01-02", 7.94), ("1990-12-31", 8.08)],
                                        "30y": [("1990-01-02", 8.00), ("1990-12-31", 8.26)]}


def test_a_year_without_rows_yet_is_empty():
    assert parse_treasury_csv("") == {} == parse_treasury_csv(TREASURY_HEADER + "\n")


def test_a_treasury_file_with_rows_but_without_a_key_maturity_raises():
    # Review 18's I-1: every year's file since 1990 has the 3-month, 2-year and 10-year columns, so rows without one
    # of them are a renamed header, not a year without yields (which would replace the stored yields with none).
    renamed = 'Date,"1 Month","3 Month","2 Year","10 Year"\n10/23/2026,4.30,4.25,3.60,4.15\n'
    with pytest.raises(ValueError, match="'3 Mo', '2 Yr', '10 Yr'"):
        parse_treasury_csv(renamed)
    for name in ("3 Mo", "2 Yr", "10 Yr"):
        with pytest.raises(ValueError, match=f"'{name}'"):
            parse_treasury_csv(TREASURY_2026.replace(f'"{name}"', f'"{name}."'))
    # The header alone has no rows: a year without rows yet, whatever its columns.
    assert parse_treasury_csv('Date,"1 Month","10 Year"\n') == {}


def test_a_treasury_answer_that_isnt_its_csv_raises():
    for text in ("<html><body>Maintenance</body></html>", 'Date,"3 Mo","2 Yr","10 Yr"\n2026-10-23,4.25,3.60,4.15\n',
                 'Date,"3 Mo","2 Yr","10 Yr"\n23/10/2026,4.25,3.60,4.15\n'):
        with pytest.raises(ValueError):
            parse_treasury_csv(text)


# ---- Shiller ------------------------------------------------------------------------------------------------------

#: The Data sheet's header labels in the tests' own order (Shiller's runs Date, P, D, E, CPI, ..., CAPE).
LABELS = ("Date", "CPI", "P", "Fraction", "E", "D", "CAPE")
#: Each month's values under their labels: CAPE begins in 1881; October 2026 has a price and a CPI but no dividend,
#: earnings or CAPE yet.
MONTHS = [
    (1871.01, {"P": 4.44, "D": 0.26, "E": 0.4, "CPI": 12.46, "Fraction": 1871.04}),
    (1871.02, {"P": 4.5, "D": 0.26, "E": 0.4, "CPI": 12.84, "Fraction": 1871.13}),
    (1881.01, {"P": 6.19, "D": 0.3, "E": 0.5, "CPI": 9.42, "CAPE": 18.47, "Fraction": 1881.04}),
    (2026.09, {"P": 6600.0, "D": 75.0, "E": 230.0, "CPI": 325.0, "CAPE": 39.5, "Fraction": 2026.71}),
    (2026.1, {"P": 6700.0, "CPI": 326.0, "Fraction": 2026.79}),
]


def _shiller_workbook(labels=LABELS, months=MONTHS):
    """A workbook with Shiller's layout in miniature: a "Notes" sheet, then "Data": a title row, a decoy row of labels
    (row 6), the header labels (row 7: "Date" and "CAPE" shared strings, the others inline, "CPI " with a space
    after it), a row per month from row 8 (the date a number: January 1871 an RK of 187101 / 100, the others NUMBER
    records), CAPE as formulas (an empty string where it has no value), and a footnote."""
    shared = ["Date", "CAPE"]
    records = [w.label(0, 0, "Stock market data used in a book"), w.label(6, 2, "Real"), w.label(6, 3, "Date"),
               w.label(6, 6, "Cyclically Adjusted")]
    for col, text in enumerate(labels):
        records.append(w.labelsst(7, col, shared.index(text)) if text in shared
                       else w.label(7, col, "CPI " if text == "CPI" else text))
    date_col = labels.index("Date")
    for row, (day, values) in enumerate(months, 8):
        records.append(w.rk(row, date_col, w.rk_int(187101, div100=True)) if day == 1871.01
                       else w.number(row, date_col, day))
        for name, value in values.items():
            if name in labels:
                col = labels.index(name)
                records.append(w.formula(row, col, w.cached_number(value)) if name == "CAPE"
                               else w.number(row, col, value))
        if "CAPE" in labels and "CAPE" not in values:
            records.append(w.formula(row, labels.index("CAPE"), w.cached(w.EMPTY_RESULT)))
    records.append(w.label(8 + len(months) + 1, 0, "Note: the latest months are preliminary"))
    sst = [w.sst_head(len(shared)) + b"".join(w.sst_string(text) for text in shared)]
    return w.workbook([("Notes", [w.label(0, 0, "about")]), ("Data", records)], sst=sst)


def test_shillers_series_are_read_under_their_header_labels():
    series = parse_shiller(_shiller_workbook())
    assert sorted(series) == ["cape", "cpi", "dividend", "earnings", "price"]
    assert series["price"] == [("1871-01-01", 4.44), ("1871-02-01", 4.5), ("1881-01-01", 6.19),
                               ("2026-09-01", 6600.0), ("2026-10-01", 6700.0)]
    assert series["dividend"] == [("1871-01-01", 0.26), ("1871-02-01", 0.26), ("1881-01-01", 0.3),
                                  ("2026-09-01", 75.0)]
    assert series["earnings"] == [("1871-01-01", 0.4), ("1871-02-01", 0.4), ("1881-01-01", 0.5),
                                  ("2026-09-01", 230.0)]
    assert series["cpi"] == [("1871-01-01", 12.46), ("1871-02-01", 12.84), ("1881-01-01", 9.42),
                             ("2026-09-01", 325.0), ("2026-10-01", 326.0)]
    assert series["cape"] == [("1881-01-01", 18.47), ("2026-09-01", 39.5)]


def test_shillers_2026_1_is_october_and_1871_01_january():
    # Month = round(100 x the fraction): 2026.1 is October, not January; truncating would read 2026.1 as September
    # and 1871.01 as month 0.
    days = [day for day, _ in parse_shiller(_shiller_workbook())["price"]]
    assert (days[0], days[-1]) == ("1871-01-01", "2026-10-01")


def test_each_shiller_series_stops_at_its_own_last_value():
    series = parse_shiller(_shiller_workbook())
    assert {name: rows[-1][0] for name, rows in series.items()} == {
        "price": "2026-10-01", "cpi": "2026-10-01", "dividend": "2026-09-01", "earnings": "2026-09-01",
        "cape": "2026-09-01"}
    assert series["cape"][0][0] == "1881-01-01"


def test_a_missing_shiller_header_label_raises_naming_it():
    with pytest.raises(ValueError, match="CAPE"):
        parse_shiller(_shiller_workbook(labels=("Date", "CPI", "P", "Fraction", "E", "D")))
    with pytest.raises(ValueError, match="'E'"):
        parse_shiller(_shiller_workbook(labels=("Date", "CPI", "P", "Fraction", "Earnings", "D", "CAPE")))


def test_a_shiller_series_without_any_value_raises():
    months = [(day, {k: v for k, v in values.items() if k != "CAPE"}) for day, values in MONTHS]
    with pytest.raises(ValueError, match="CAPE"):
        parse_shiller(_shiller_workbook(months=months))


def test_a_month_twice_in_shillers_sheet_raises():
    with pytest.raises(ValueError, match="1871-02"):
        parse_shiller(_shiller_workbook(months=MONTHS + [(1871.02, {"P": 4.6, "CPI": 12.9})]))


def test_a_workbook_without_shillers_data_sheet_raises():
    with pytest.raises(XLSError, match="Data"):
        parse_shiller(w.workbook([("Notes", [w.label(0, 0, "about")])]))


LINK = "https://img1.wsimg.com/blobby/go/e5e77e0b/downloads/ie_data.xls?ver=1759694232149"
PAGE = (f'<html><body><a href="https://img1.wsimg.com/blobby/go/e5e77e0b/downloads/Fig2-1.xls?ver=1">Figure</a>'
        f'<a data-ux="Link" target="_blank" href="{LINK}&amp;x=1">U.S. Stock Markets 1871-Present and CAPE Ratio</a>'
        f'</body></html>')


def test_the_workbooks_link_is_read_from_shillers_page():
    assert find_xls_link(PAGE) == LINK + "&x=1"
    assert find_xls_link('<a class="x" href="/downloads/ie_data.xls">data</a>') == (
        "https://shillerdata.com/downloads/ie_data.xls")
    assert find_xls_link("<a href='//img1.wsimg.com/d/IE_DATA.XLS?ver=2'>data</a>") == (
        "https://img1.wsimg.com/d/IE_DATA.XLS?ver=2")


def test_a_page_without_the_workbooks_link_raises():
    for html in ("<html><body>Moved</body></html>", '<a href="/downloads/ie_data.xlsx">new</a>',
                 '<a href="/downloads/old_ie_data.xls">old</a>'):
        with pytest.raises(ValueError):
            find_xls_link(html)


def test_only_an_https_link_to_the_workbook_is_taken():
    # Review 18's I-2: the link is fetched, stored and shown as the CAPE's source link, so a link of another scheme
    # is skipped: the page raises without an https one, and an https link after it is the one taken.
    for href in ("javascript:alert(document.domain)//ie_data.xls", "JavaScript:alert(1)//ie_data.xls",
                 "javascript&#58;alert(1)//ie_data.xls", " javascript:alert(1)//ie_data.xls",
                 "data:text/html,hi//ie_data.xls", "http://evil.example/ie_data.xls",
                 "ftp://img1.wsimg.com/downloads/ie_data.xls"):
        with pytest.raises(ValueError, match="https"):
            find_xls_link(f'<a href="{href}">data</a>')
        assert find_xls_link(f'<a href="{href}">data</a><a href="{LINK}">data</a>') == LINK


# ---- the cache ----------------------------------------------------------------------------------------------------


def test_an_answer_is_fetched_cached_and_read_from_the_cache_for_a_day(tmp_path):
    assert DAY == 24 * 3600
    fetcher, session, _ = _fetcher(tmp_path, {fred_url("GDP"): FRED_GDP})
    data = fetch_fred(fetcher, "GDP")
    assert data.series == {"GDP": [("2026-01-01", 32000.0), ("2026-04-01", 32560.0)]}
    assert (data.url, data.fetched_at, data.fetched, data.cached) == (fred_url("GDP"), NOW, 1, 0)
    path = tmp_path / "cache" / "market" / "fred_GDP.csv"
    assert path.read_bytes() == FRED_GDP and path.stat().st_mtime == NOW
    assert session.asked == [fred_url("GDP")]
    later, session, _ = _fetcher(tmp_path, {}, now=NOW + DAY - 60)
    data = fetch_fred(later, "GDP")
    assert (data.fetched_at, data.fetched, data.cached, session.asked) == (NOW, 0, 1, [])
    assert data.series == {"GDP": [("2026-01-01", 32000.0), ("2026-04-01", 32560.0)]}


def test_a_copy_older_than_a_day_is_fetched_again(tmp_path):
    _cache(tmp_path, "fred_GDP.csv", b"observation_date,GDP\n2025-10-01,31000.0\n", NOW - DAY - 60)
    fetcher, session, _ = _fetcher(tmp_path, {fred_url("GDP"): FRED_GDP})
    data = fetch_fred(fetcher, "GDP")
    assert session.asked == [fred_url("GDP")] and data.series["GDP"][-1] == ("2026-04-01", 32560.0)
    assert (tmp_path / "cache" / "market" / "fred_GDP.csv").read_bytes() == FRED_GDP


def test_a_failed_fetch_uses_the_cached_copy_and_says_so(tmp_path):
    old = b"observation_date,GDP\n2025-10-01,31000.0\n"
    for failure in (requests.ConnectionError("connection refused"), _Response(503), _Response(404)):
        _cache(tmp_path, "fred_GDP.csv", old, NOW - 3 * DAY)
        fetcher, session, lines = _fetcher(tmp_path, {fred_url("GDP"): failure})
        data = fetch_fred(fetcher, "GDP")
        assert data.series == {"GDP": [("2025-10-01", 31000.0)]}
        assert (data.fetched_at, data.fetched, data.cached) == (NOW - 3 * DAY, 0, 1)
        assert len(data.notes) == 1 and "cached copy" in data.notes[0] and lines == data.notes
        assert (tmp_path / "cache" / "market" / "fred_GDP.csv").read_bytes() == old


def test_a_failed_fetch_without_a_cached_copy_fails_the_source(tmp_path):
    fetcher, session, _ = _fetcher(tmp_path, {fred_url("GDP"): _Response(503)})
    with pytest.raises(MarketFetchError, match="503"):
        fetch_fred(fetcher, "GDP")
    assert session.asked == [fred_url("GDP")] * 2  # a 503 is retried (once, here)
    assert not (tmp_path / "cache" / "market" / "fred_GDP.csv").exists()


def test_an_answer_that_doesnt_parse_isnt_cached(tmp_path):
    html = b"<html><body>Down for maintenance</body></html>"
    fetcher, _, _ = _fetcher(tmp_path, {fred_url("GDP"): html})
    with pytest.raises(MarketFetchError):
        fetch_fred(fetcher, "GDP")
    assert not (tmp_path / "cache" / "market" / "fred_GDP.csv").exists()
    path = _cache(tmp_path, "fred_GDP.csv", FRED_GDP, NOW - 2 * DAY)
    fetcher, _, lines = _fetcher(tmp_path, {fred_url("GDP"): html})
    assert fetch_fred(fetcher, "GDP").series["GDP"][-1] == ("2026-04-01", 32560.0)
    assert path.read_bytes() == FRED_GDP and path.stat().st_mtime == NOW - 2 * DAY and len(lines) == 1


def test_a_cached_copy_that_no_longer_parses_is_fetched_again(tmp_path):
    _cache(tmp_path, "fred_GDP.csv", b"<html>broken</html>", NOW - 60)
    fetcher, session, _ = _fetcher(tmp_path, {fred_url("GDP"): FRED_GDP})
    assert fetch_fred(fetcher, "GDP").series["GDP"][-1] == ("2026-04-01", 32560.0)
    assert session.asked == [fred_url("GDP")]


class _Adapter(requests.adapters.BaseAdapter):
    """A transport mounted on a real ``requests.Session``: records each request it is sent and answers 200 with
    *content*, so what the default session sends is seen without the network."""

    def __init__(self, content):
        super().__init__()
        self.content = content
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append((request, kwargs))
        response = requests.Response()
        response.status_code, response._content = 200, self.content
        response.request, response.url = request, request.url
        return response

    def close(self):
        pass


def test_the_default_session_sends_requests_own_user_agent_and_the_timeout(tmp_path):
    # Review 18's m-3: the fetcher's own new session, never the SEC handler's, whose User-Agent may carry the user's
    # email address; and every request has a timeout (Treasury.gov answers in about 19 s).
    fetcher = MarketFetcher(tmp_path / "cache", limiter=RateLimiter(min_interval=0),
                            retry=RetryHandler(max_retries=0, base_delay=0))
    assert isinstance(fetcher.session, requests.Session)
    assert fetcher.session is not MarketFetcher(tmp_path / "cache").session
    adapter = _Adapter(FRED_GDP)
    fetcher.session.mount("https://", adapter)
    assert fetch_fred(fetcher, "GDP").series["GDP"][-1] == ("2026-04-01", 32560.0)
    (request, kwargs), = adapter.sent
    assert request.url == fred_url("GDP")
    assert request.headers["User-Agent"] == requests.utils.default_user_agent()
    assert request.headers["User-Agent"] != src.config.SECConfig().user_agent
    assert not any("@" in value for value in request.headers.values())
    assert kwargs["timeout"] == sources.TIMEOUT == 60.0


def test_every_request_is_given_the_timeout(tmp_path):
    # Each source's requests, a retried one's every attempt among them, pass the timeout and nothing else.
    answers = _answers()
    answers[fred_url("GDP")] = _Response(503)
    session = _Session(answers)
    summary = collect.collect_market(tmp_path / "stock.db", ["fred", "shiller", "treasury"], tmp_path / "cache",
                                     session=session, log=lambda line: None, treasury_from=2025, today="2026-10-24")
    assert summary["fred:GDP"]["ok"] is False and session.asked.count(fred_url("GDP")) == sources.MAX_RETRIES + 1
    assert len(session.kwargs) == len(session.asked) == 6 + sources.MAX_RETRIES
    assert all(kwargs == {"timeout": sources.TIMEOUT} for kwargs in session.kwargs)


def test_new_yorks_year_ends_at_five_utc():
    assert year_end(2025) == _utc(2026, 1, 1, 5)


def test_a_past_treasury_year_is_kept_once_cached_after_it_ended_and_fetched_again_if_cached_during_it(tmp_path):
    _cache(tmp_path, "treasury_2024.csv", TREASURY_2025.replace("2025", "2024").encode(), _utc(2025, 1, 1, 5))
    _cache(tmp_path, "treasury_2025.csv", b'Date,"3 Mo","2 Yr","10 Yr"\n06/30/2025,4.30,3.80,4.20\n',
           _utc(2026, 1, 1, 4, 59))
    _cache(tmp_path, "treasury_2026.csv", TREASURY_2026.encode(), NOW - 3600)
    fetcher, session, _ = _fetcher(tmp_path, {treasury_url(2025): TREASURY_2025.encode()})
    data = fetch_treasury(fetcher, 2024, 2026)
    assert session.asked == [treasury_url(2025)]
    assert data.series["10y"] == [("2024-12-31", 4.18), ("2025-12-31", 4.18), ("2026-01-02", 4.19),
                                  ("2026-10-23", 4.15)]
    assert (data.fetched, data.cached) == (1, 2)


def test_the_current_treasury_year_is_fetched_again_after_a_day(tmp_path):
    _cache(tmp_path, "treasury_2026.csv", b'Date,"3 Mo","2 Yr","10 Yr"\n01/02/2026,4.33,3.70,4.19\n', NOW - DAY - 60)
    fetcher, session, _ = _fetcher(tmp_path, {treasury_url(2026): TREASURY_2026.encode()})
    data = fetch_treasury(fetcher, 2026, 2026)
    assert session.asked == [treasury_url(2026)] and data.series["10y"][-1] == ("2026-10-23", 4.15)


def test_the_treasury_years_merge_with_the_latest_years_url_and_time(tmp_path):
    _cache(tmp_path, "treasury_2025.csv", TREASURY_2025.encode(), _utc(2026, 1, 3))
    fetcher, session, lines = _fetcher(tmp_path, {treasury_url(2026): TREASURY_2026.encode()})
    data = fetch_treasury(fetcher, 2025, 2026, log=lines.append)
    assert (data.url, data.fetched_at) == (treasury_url(2026), NOW)
    assert data.series["6w"] == [("2025-12-31", 4.34), ("2026-10-23", 4.29)]
    assert len(data.series) == 14 and lines  # a progress line for the year fetched
    with pytest.raises(ValueError):
        fetch_treasury(fetcher, 2027, 2026)


#: Treasury.gov's answers without rows: an empty 200, and the header alone.
EMPTY_ANSWERS = (b"", (TREASURY_HEADER + "\n").encode())


def test_a_past_year_answered_without_rows_falls_back_to_its_cached_copy(tmp_path):
    # Review 18's I-1: 2025's copy was cached on 2025-12-31, before the year ended, so it is fetched again. An answer
    # without rows, for a year that has ended, is a failed answer: the cached copy is used and kept, and that is said.
    for empty in EMPTY_ANSWERS:
        path = _cache(tmp_path, "treasury_2025.csv", TREASURY_2025.encode(), _utc(2025, 12, 31, 12))
        _cache(tmp_path, "treasury_2026.csv", TREASURY_2026.encode(), NOW - 3600)
        fetcher, session, lines = _fetcher(tmp_path, {treasury_url(2025): empty})
        data = fetch_treasury(fetcher, 2025, 2026)
        assert session.asked == [treasury_url(2025)]
        assert data.series["10y"] == [("2025-12-31", 4.18), ("2026-01-02", 4.19), ("2026-10-23", 4.15)]
        assert (data.fetched, data.cached) == (0, 2)
        assert len(data.notes) == 1 and lines == data.notes
        assert data.notes[0].startswith("treasury_2025.csv: the fetch failed") and "no rows" in data.notes[0]
        assert path.read_bytes() == TREASURY_2025.encode() and path.stat().st_mtime == _utc(2025, 12, 31, 12)


def test_a_past_year_answered_without_rows_and_without_a_cached_copy_fails_the_source(tmp_path):
    # A first collection: no copy to fall back on, so the source fails and nothing of it is stored.
    for empty in EMPTY_ANSWERS:
        fetcher, _, _ = _fetcher(tmp_path, {treasury_url(2025): empty, treasury_url(2026): TREASURY_2026.encode()})
        with pytest.raises(MarketFetchError, match="no rows"):
            fetch_treasury(fetcher, 2025, 2026)
        assert not (tmp_path / "cache" / "market" / "treasury_2025.csv").exists()


def test_a_cached_past_year_without_rows_is_fetched_again(tmp_path):
    # Cached after 2025 ended, but without rows: never kept as the year's whole file.
    for empty in EMPTY_ANSWERS:
        _cache(tmp_path, "treasury_2025.csv", empty, _utc(2026, 1, 2))
        _cache(tmp_path, "treasury_2026.csv", TREASURY_2026.encode(), NOW - 3600)
        fetcher, session, _ = _fetcher(tmp_path, {treasury_url(2025): TREASURY_2025.encode()})
        data = fetch_treasury(fetcher, 2025, 2026)
        assert session.asked == [treasury_url(2025)] and data.series["10y"][0] == ("2025-12-31", 4.18)
        assert (tmp_path / "cache" / "market" / "treasury_2025.csv").read_bytes() == TREASURY_2025.encode()


def test_the_current_year_without_rows_yet_is_no_failure(tmp_path):
    # The first days of January: the new year's file has no rows yet. Without a cached copy that has rows, that is
    # the year as it stands, not a failed answer: it is cached, and fetched again a day later.
    jan1 = _utc(2027, 1, 1, 15)
    fetcher, session, lines = _fetcher(tmp_path, {treasury_url(2027): EMPTY_ANSWERS[0]}, now=jan1)
    data = fetch_treasury(fetcher, 2027, 2027)
    assert (data.series, data.fetched, data.notes, lines) == ({}, 1, [], [])
    path = tmp_path / "cache" / "market" / "treasury_2027.csv"
    assert path.read_bytes() == EMPTY_ANSWERS[0] and path.stat().st_mtime == jan1
    fetcher, session, lines = _fetcher(tmp_path, {treasury_url(2027): EMPTY_ANSWERS[1]}, now=jan1 + DAY + 60)
    data = fetch_treasury(fetcher, 2027, 2027)
    assert (data.series, data.fetched, data.notes, session.asked) == ({}, 1, [], [treasury_url(2027)])
    assert path.read_bytes() == EMPTY_ANSWERS[1]


def test_the_current_year_answered_without_rows_falls_back_to_its_cached_copy_with_rows(tmp_path):
    # Once the current year's cached copy has rows, an answer without any is a failed answer too.
    for empty in EMPTY_ANSWERS:
        path = _cache(tmp_path, "treasury_2026.csv", TREASURY_2026.encode(), NOW - 2 * DAY)
        fetcher, session, lines = _fetcher(tmp_path, {treasury_url(2026): empty})
        data = fetch_treasury(fetcher, 2026, 2026)
        assert session.asked == [treasury_url(2026)]
        assert data.series["10y"] == [("2026-01-02", 4.19), ("2026-10-23", 4.15)]
        assert (data.fetched, data.cached, data.fetched_at) == (0, 1, NOW - 2 * DAY)
        assert len(data.notes) == 1 and "no rows" in data.notes[0] and lines == data.notes
        assert path.read_bytes() == TREASURY_2026.encode()


def test_shillers_workbook_is_fetched_from_the_link_on_its_page_then_read_from_the_cache(tmp_path):
    answers = {SHILLER_PAGE_URL: PAGE.encode(), LINK + "&x=1": _shiller_workbook()}
    fetcher, session, _ = _fetcher(tmp_path, answers)
    data = fetch_shiller(fetcher)
    assert session.asked == [SHILLER_PAGE_URL, LINK + "&x=1"]
    assert (data.url, data.fetched_at, data.fetched) == (LINK + "&x=1", NOW, 2)
    assert data.series["price"][-1] == ("2026-10-01", 6700.0)
    assert sorted(p.name for p in (tmp_path / "cache" / "market").iterdir()) == ["ie_data.xls", "shiller.html"]
    later, session, _ = _fetcher(tmp_path, {}, now=NOW + 3600)
    assert fetch_shiller(later).series == data.series and session.asked == []


def test_a_shiller_workbook_that_changed_its_layout_fails_the_source(tmp_path):
    answers = {SHILLER_PAGE_URL: PAGE.encode(),
               LINK + "&x=1": _shiller_workbook(labels=("Date", "CPI", "Price", "Fraction", "E", "D", "CAPE"))}
    fetcher, _, _ = _fetcher(tmp_path, answers)
    with pytest.raises(MarketFetchError, match="'P'"):
        fetch_shiller(fetcher)


# ---- the store ----------------------------------------------------------------------------------------------------


def _rows(db, series):
    with closing(sqlite3.connect(str(db))) as conn:
        return conn.execute("SELECT date, value FROM market_series WHERE series = ? ORDER BY date",
                            (series,)).fetchall()


def _fetches(db):
    with closing(sqlite3.connect(str(db))) as conn:
        return {row[0]: row[1:] for row in conn.execute(
            "SELECT source, url, rows, fetched_at FROM market_fetches ORDER BY source")}


def test_the_market_tables(tmp_path):
    db = tmp_path / "m.db"
    SQLiteStore(db).export_market_series("fred:GDP", {"fred:GDP": [("2026-04-01", 32560.0)]}, "u", "t")
    with closing(sqlite3.connect(str(db))) as conn:
        series = [(r[1], r[2], r[3], r[5]) for r in conn.execute("PRAGMA table_info(market_series)")]
        fetches = [(r[1], r[2], r[3], r[5]) for r in conn.execute("PRAGMA table_info(market_fetches)")]
    assert series == [("series", "TEXT", 1, 1), ("date", "TEXT", 1, 2), ("value", "REAL", 1, 0)]
    assert fetches == [("source", "TEXT", 0, 1), ("url", "TEXT", 0, 0), ("rows", "INTEGER", 0, 0),
                       ("fetched_at", "TEXT", 1, 0)]


def test_each_named_series_is_replaced_whole_and_its_sources_fetch_row_too(tmp_path):
    db = tmp_path / "m.db"
    store = SQLiteStore(db)
    store.export_market_series("shiller", {"shiller:price": [("2026-09-01", 1.0), ("2026-10-01", 2.0)],
                                           "shiller:cape": [("2026-09-01", 30.0)]}, "u1", "2026-10-01T00:00:00+00:00")
    store.export_market_series("fred:GDP", {"fred:GDP": [("2026-04-01", 32560.0)]}, "u2", "2026-10-02T00:00:00+00:00")
    store.export_market_series("shiller", {"shiller:price": [("2026-10-01", 2.5)]}, "u3", "2026-10-03T00:00:00+00:00")
    assert _rows(db, "shiller:price") == [("2026-10-01", 2.5)]
    assert _rows(db, "shiller:cape") == [("2026-09-01", 30.0)]  # not named this time: left as it was
    assert _rows(db, "fred:GDP") == [("2026-04-01", 32560.0)]
    assert _fetches(db) == {"fred:GDP": ("u2", 1, "2026-10-02T00:00:00+00:00"),
                            "shiller": ("u3", 1, "2026-10-03T00:00:00+00:00")}


def test_a_series_replaced_from_a_day_keeps_its_rows_before_it(tmp_path):
    # BUG-224: with replace_from, each named series loses only its rows dated on or after that day.
    db = tmp_path / "m.db"
    store = SQLiteStore(db)
    store.export_market_series("treasury", {"ust:10y": [("2024-12-31", 4.5), ("2025-01-01", 9.9), ("2025-06-30", 4.4)],
                                            "ust:2y": [("2024-12-31", 4.0), ("2025-06-30", 3.9)]}, "u1", "t1")
    store.export_market_series("treasury", {"ust:10y": [("2025-01-02", 4.6), ("2025-06-30", 4.3)]}, "u2", "t2",
                               replace_from="2025-01-01")
    assert _rows(db, "ust:10y") == [("2024-12-31", 4.5), ("2025-01-02", 4.6), ("2025-06-30", 4.3)]
    assert _rows(db, "ust:2y") == [("2024-12-31", 4.0), ("2025-06-30", 3.9)]  # not named: left as it was
    assert _fetches(db)["treasury"] == ("u2", 2, "t2")


def test_a_store_that_fails_changes_nothing(tmp_path):
    db = tmp_path / "m.db"
    store = SQLiteStore(db)
    store.export_market_series("shiller", {"shiller:price": [("2026-09-01", 1.0)],
                                           "shiller:cpi": [("2026-09-01", 3.0)]}, "u1", "t1")
    with pytest.raises(sqlite3.IntegrityError):  # a date twice in the second series
        store.export_market_series("shiller", {"shiller:price": [("2026-10-01", 2.0)],
                                               "shiller:cpi": [("2026-10-01", 4.0), ("2026-10-01", 4.0)]}, "u2", "t2")
    assert _rows(db, "shiller:price") == [("2026-09-01", 1.0)] and _rows(db, "shiller:cpi") == [("2026-09-01", 3.0)]
    assert _fetches(db) == {"shiller": ("u1", 2, "t1")}


# ---- collect_market -----------------------------------------------------------------------------------------------


def _answers():
    return {fred_url("NCBEILQ027S"): FRED_EQUITIES, fred_url("GDP"): FRED_GDP, SHILLER_PAGE_URL: PAGE.encode(),
            LINK + "&x=1": _shiller_workbook(), treasury_url(2025): TREASURY_2025.encode(),
            treasury_url(2026): TREASURY_2026.encode()}


def _collect(tmp_path, answers, wanted=("fred", "shiller", "treasury"), treasury_from=2025):
    db = tmp_path / "stock.db"
    lines = []
    summary = collect.collect_market(db, list(wanted), tmp_path / "cache", session=_Session(answers),
                                     log=lines.append, treasury_from=treasury_from, today="2026-10-24")
    return db, summary, lines


def _age(tmp_path, name, seconds):
    """Make the cached copy of *name* *seconds* older; its path."""
    path = tmp_path / "cache" / "market" / name
    when = path.stat().st_mtime - seconds
    os.utime(path, (when, when))
    return path


def _yields(db):
    """Every stored Treasury series' rows, by name."""
    return {name: _rows(db, f"ust:{name}") for name in sources.TREASURY_COLUMNS.values()}


def test_collect_market_stores_the_three_sources(tmp_path):
    db, summary, lines = _collect(tmp_path, _answers())
    assert list(summary) == ["fred:NCBEILQ027S", "fred:GDP", "shiller", "treasury"]
    assert all(s["ok"] and s["error"] is None for s in summary.values())
    assert _rows(db, "fred:NCBEILQ027S") == [("2026-01-01", 80000000.0), ("2026-04-01", 83050000.0)]
    assert _rows(db, "fred:GDP") == [("2026-01-01", 32000.0), ("2026-04-01", 32560.0)]
    assert _rows(db, "shiller:cape") == [("1881-01-01", 18.47), ("2026-09-01", 39.5)]
    assert _rows(db, "shiller:price")[-1] == ("2026-10-01", 6700.0)
    assert _rows(db, "ust:10y") == [("2025-12-31", 4.18), ("2026-01-02", 4.19), ("2026-10-23", 4.15)]
    fetches = _fetches(db)
    assert {source: row[:2] for source, row in fetches.items()} == {
        "fred:GDP": (fred_url("GDP"), 2), "fred:NCBEILQ027S": (fred_url("NCBEILQ027S"), 2),
        "shiller": (LINK + "&x=1", 5 + 4 + 4 + 5 + 2), "treasury": (treasury_url(2026), 3 + 2 + 3 * 12)}
    assert all(row[2].endswith("+00:00") for row in fetches.values())
    assert summary["fred:GDP"]["rows"] == 2 and summary["fred:GDP"]["latest"] == "2026-04-01"
    assert summary["shiller"]["series"]["shiller:price"] == {"rows": 5, "first": "1871-01-01", "last": "2026-10-01"}
    assert summary["shiller"]["latest"] == "2026-10-01" and summary["treasury"]["latest"] == "2026-10-23"
    assert summary["treasury"]["series"]["ust:6w"] == {"rows": 2, "first": "2025-12-31", "last": "2026-10-23"}
    assert all(any(line.startswith(source) for line in lines) for source in summary)  # a progress line each


def test_a_failing_source_is_reported_and_the_others_are_stored(tmp_path):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export_market_series("shiller", {"shiller:cape": [("2026-08-01", 39.0)]}, "old", "t0")
    answers = _answers()
    answers[SHILLER_PAGE_URL] = _Response(404)
    db, summary, lines = _collect(tmp_path, answers)
    assert [source for source, s in summary.items() if not s["ok"]] == ["shiller"]
    assert "404" in summary["shiller"]["error"] and summary["shiller"]["rows"] == 0
    assert any("shiller" in line and "404" in line for line in lines)
    assert _rows(db, "shiller:cape") == [("2026-08-01", 39.0)]  # what was stored stays
    assert _fetches(db)["shiller"] == ("old", 1, "t0")
    assert _rows(db, "fred:GDP") and _rows(db, "ust:30y") and _rows(db, "fred:NCBEILQ027S")


def test_an_unexpected_error_in_one_source_is_reported_and_the_others_go_on(tmp_path, monkeypatch):
    def broken(fetcher, series_id):
        if series_id == "NCBEILQ027S":
            raise RuntimeError("a bug")
        return real(fetcher, series_id)

    real = sources.fetch_fred
    monkeypatch.setattr(collect, "fetch_fred", broken)
    db, summary, _ = _collect(tmp_path, _answers())
    assert summary["fred:NCBEILQ027S"]["ok"] is False and "a bug" in summary["fred:NCBEILQ027S"]["error"]
    assert [s["ok"] for s in summary.values()] == [False, True, True, True]


def test_a_source_without_rows_fails_and_leaves_its_stored_series(tmp_path):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export_market_series("fred:GDP", {"fred:GDP": [("2026-01-01", 32000.0)]}, "old", "t0")
    answers = _answers()
    answers[fred_url("GDP")] = b"observation_date,GDP\n2026-07-01,.\n"
    db, summary, _ = _collect(tmp_path, answers, wanted=["fred"])
    assert summary["fred:GDP"]["ok"] is False and "no rows" in summary["fred:GDP"]["error"]
    assert summary["fred:NCBEILQ027S"]["ok"] is True
    assert _rows(db, "fred:GDP") == [("2026-01-01", 32000.0)]


def test_only_the_named_sources_are_collected(tmp_path):
    session = _Session(_answers())
    summary = collect.collect_market(tmp_path / "stock.db", ["treasury"], tmp_path / "cache", session=session,
                                     log=lambda line: None, treasury_from=2025, today="2026-10-24")
    assert list(summary) == ["treasury"] and session.asked == [treasury_url(2025), treasury_url(2026)]
    with pytest.raises(ValueError):
        collect.collect_market(tmp_path / "stock.db", ["yahoo"], tmp_path / "cache", session=session)


def test_a_past_year_answered_empty_after_a_good_run_keeps_its_stored_yields(tmp_path):
    # Review 18's I-1, first scenario: 2025's copy was cached before 2025 ended, so a 2026 run fetches it again, and
    # Treasury.gov answers with an empty 200. The run falls back to the cached copy and says so: the stored yields
    # and the cached copy stay.
    answers = _answers()
    db, _, _ = _collect(tmp_path, answers, wanted=["treasury"])
    stored = _yields(db)
    assert stored["10y"] == [("2025-12-31", 4.18), ("2026-01-02", 4.19), ("2026-10-23", 4.15)]
    path = tmp_path / "cache" / "market" / "treasury_2025.csv"
    os.utime(path, (year_end(2025) - 3600, year_end(2025) - 3600))
    answers[treasury_url(2025)] = b""
    db, summary, lines = _collect(tmp_path, answers, wanted=["treasury"])
    s = summary["treasury"]
    assert (s["ok"], s["error"], s["latest"]) == (True, None, "2026-10-23")
    assert _yields(db) == stored
    assert path.read_bytes() == TREASURY_2025.encode()
    assert len(s["notes"]) == 1 and s["notes"][0].startswith("treasury_2025.csv: the fetch failed")
    assert "using the cached copy" in s["notes"][0] and s["notes"][0] in lines


def test_the_current_year_with_its_maturity_headers_renamed_keeps_its_stored_yields(tmp_path):
    # Review 18's I-1, second scenario: two days later the current year's file has its maturity headers renamed
    # ("10 Year" for "10 Yr"): rows without the yield columns, a failed answer. The run falls back to the cached copy.
    answers = _answers()
    db, _, _ = _collect(tmp_path, answers, wanted=["treasury"])
    stored = _yields(db)
    path = _age(tmp_path, "treasury_2026.csv", 2 * DAY)
    renamed = TREASURY_2026.replace(' Mo"', ' Month"').replace(' Yr"', ' Year"')
    assert '"10 Year"' in renamed and '"10 Yr"' not in renamed and '"3 Month"' in renamed
    answers[treasury_url(2026)] = renamed.encode()
    db, summary, lines = _collect(tmp_path, answers, wanted=["treasury"])
    s = summary["treasury"]
    assert (s["ok"], s["error"], s["latest"]) == (True, None, "2026-10-23")
    assert _yields(db) == stored and stored["10y"][-1] == ("2026-10-23", 4.15)
    assert path.read_bytes() == TREASURY_2026.encode()
    assert len(s["notes"]) == 1 and s["notes"][0].startswith("treasury_2026.csv: the fetch failed")
    assert "'3 Mo', '2 Yr', '10 Yr'" in s["notes"][0] and s["notes"][0] in lines


def test_a_page_linking_the_workbook_through_another_scheme_falls_back_to_its_cached_copy(tmp_path):
    # Review 18's I-2: two days later Shiller's page links the workbook through a javascript: URL. That answer has no
    # https link, so it doesn't parse: the cached page's link is used, and the URL stored and shown stays https.
    answers = _answers()
    _collect(tmp_path, answers, wanted=["shiller"])
    for name in ("shiller.html", "ie_data.xls"):
        _age(tmp_path, name, 2 * DAY)
    answers[SHILLER_PAGE_URL] = b'<a href="javascript:alert(document.domain)//ie_data.xls">U.S. Stock Markets</a>'
    db, summary, _ = _collect(tmp_path, answers, wanted=["shiller"])
    s = summary["shiller"]
    assert (s["ok"], s["url"], s["fetched"]) == (True, LINK + "&x=1", 1)
    assert _fetches(db)["shiller"][0] == LINK + "&x=1"
    assert len(s["notes"]) == 1 and s["notes"][0].startswith("shiller.html: the fetch failed")


def test_a_later_treasury_from_keeps_the_earlier_yields_and_refreshes_its_own(tmp_path):
    # BUG-224: after a full run, a run with --treasury-from 2026 refreshes 2026's yields (a new day, a revised one)
    # and keeps 2025's; FRED's and Shiller's series are still replaced whole.
    answers = _answers()
    db, _, _ = _collect(tmp_path, answers)
    with closing(sqlite3.connect(str(db))) as conn:
        conn.executemany("INSERT INTO market_series (series, date, value) VALUES (?, ?, ?)",
                         [("fred:GDP", "2020-01-01", 1.0), ("shiller:cape", "1870-01-01", 2.0)])
        conn.commit()
    _age(tmp_path, "treasury_2026.csv", 2 * DAY)
    answers[treasury_url(2026)] = ("\n".join([
        TREASURY_HEADER, "10/26/2026,4.31,4.30,4.29,4.26,4.21,4.11,3.96,3.61,3.56,3.71,3.91,4.10,4.61,4.71",
        "10/23/2026,4.30,4.29,4.28,4.25,4.20,4.10,3.95,3.60,3.55,3.70,3.90,4.16,4.60,4.70",
        "01/02/2026,4.40,,4.38,4.33,4.30,4.20,4.05,3.70,3.65,3.80,4.00,4.19,4.65,4.75"]) + "\n").encode()
    db, summary, _ = _collect(tmp_path, answers, treasury_from=2026)
    assert all(s["ok"] for s in summary.values())
    assert _rows(db, "ust:10y") == [("2025-12-31", 4.18), ("2026-01-02", 4.19), ("2026-10-23", 4.16),
                                    ("2026-10-26", 4.10)]
    assert _rows(db, "ust:6w") == [("2025-12-31", 4.34), ("2026-10-23", 4.29), ("2026-10-26", 4.30)]
    assert summary["treasury"]["series"]["ust:10y"] == {"rows": 3, "first": "2026-01-02", "last": "2026-10-26"}
    assert _rows(db, "fred:GDP") == [("2026-01-01", 32000.0), ("2026-04-01", 32560.0)]
    assert _rows(db, "shiller:cape") == [("1881-01-01", 18.47), ("2026-09-01", 39.5)]


def test_a_second_collection_reads_its_fresh_copies_from_the_cache(tmp_path):
    session = _Session(_answers())
    for _ in range(2):
        summary = collect.collect_market(tmp_path / "stock.db", ["fred", "shiller", "treasury"], tmp_path / "cache",
                                         session=session, log=lambda line: None, treasury_from=2025,
                                         today="2026-10-24")
    assert len(session.asked) == 6  # each answer fetched once: two FRED series, the page and its workbook, two years
    assert all(s["ok"] and s["fetched"] == 0 for s in summary.values())


# ---- the CLI ------------------------------------------------------------------------------------------------------


def _ok(rows=1, latest="2026-10-23"):
    return {"ok": True, "rows": rows, "series": {}, "latest": latest, "url": "u", "fetched_at": "t", "fetched": 1,
            "cached": 0, "notes": [], "error": None}


def _fake_collect(calls, failed=()):
    def fake(db_path, wanted, cache_dir, **kwargs):
        calls.append((db_path, list(wanted), cache_dir, kwargs.get("treasury_from")))
        names = [n for s in wanted for n in (["fred:NCBEILQ027S", "fred:GDP"] if s == "fred" else [s])]
        return {n: dict(_ok(), ok=n not in failed, error="HTTP 404" if n in failed else None) for n in names}
    return fake


def test_the_cli_refuses_a_database_that_isnt_there_with_exit_2(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(collect, "collect_market", _fake_collect(calls))
    missing = tmp_path / "typo.db"
    assert collect.main(["--db", str(missing)]) == 2
    assert not missing.exists() and calls == []
    assert "typo.db" in capsys.readouterr().err
    monkeypatch.setattr(src.config, "StorageConfig", lambda: SimpleNamespace(database_path=tmp_path / "default.db"))
    assert collect.main([]) == 2 and not (tmp_path / "default.db").exists()


def test_the_cli_collects_all_three_sources_by_default_into_the_stores_database(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(collect, "collect_market", _fake_collect(calls))
    db = tmp_path / "default.db"
    db.write_bytes(b"")
    monkeypatch.setattr(src.config, "StorageConfig", lambda: SimpleNamespace(database_path=db))
    assert collect.main([]) == 0
    assert calls == [(db, ["fred", "shiller", "treasury"], collect.Path("data/cache"), 1990)]
    out = capsys.readouterr().out
    assert out.isascii() and "4 of 4" in out


def test_the_cli_collects_the_named_sources_only(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(collect, "collect_market", _fake_collect(calls))
    db = tmp_path / "stock.db"
    db.write_bytes(b"")
    cache = tmp_path / "cache"
    assert collect.main(["--treasury", "--fred", "--treasury-from", "2020", "--db", str(db),
                         "--cache-dir", str(cache)]) == 0
    assert calls == [(db, ["fred", "treasury"], cache, 2020)]
    calls.clear()
    assert collect.main(["--shiller", "--db", str(db)]) == 0
    assert calls[0][1] == ["shiller"]


def test_the_cli_exits_1_when_a_source_stored_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(collect, "collect_market", _fake_collect([], failed=("fred:GDP",)))
    db = tmp_path / "stock.db"
    db.write_bytes(b"")
    assert collect.main(["--db", str(db)]) == 1
    out = capsys.readouterr().out
    assert "3 of 4" in out and "fred:GDP" in out and "HTTP 404" in out


def test_the_summary_lists_each_fallback_to_a_cached_copy_on_its_stale_line():
    # Review 18's m-1: a source that fell back to its cached copy stored rows (exit 0), so the summary says which and
    # how old the copy is: the fetcher's notes.
    notes = ["shiller.html: the fetch failed (HTTP 503); using the cached copy of 2026-10-22 18:13 UTC",
             "ie_data.xls: the fetch failed (HTTP 503); using the cached copy of 2026-10-22 18:14 UTC"]
    treasury = "treasury_2026.csv: the fetch failed (Treasury.gov's file has no rows); using the cached copy of x"
    summary = {"fred:GDP": _ok(), "shiller": dict(_ok(), notes=notes), "treasury": dict(_ok(), notes=[treasury])}
    assert collect.summary_lines(summary)[-3:] == [
        "Latest: fred:GDP 2026-10-23; shiller 2026-10-23; treasury 2026-10-23",
        f"Stale: shiller ({notes[0]}); shiller ({notes[1]}); treasury ({treasury})", "Failed: none"]
    failed = dict(_ok(), ok=False, error="HTTP 404")
    assert collect.summary_lines({"fred:GDP": _ok(), "shiller": failed})[-2:] == [
        "Stale: none", "Failed: shiller (HTTP 404)"]


def test_a_layout_change_after_a_good_run_falls_back_and_says_so_on_the_stale_line(tmp_path, monkeypatch, capsys):
    # Review 18's m-1: once a cached copy exists, a workbook that lost its CAPE label doesn't fail the source: the run
    # falls back to the cached copy, stores its rows again and exits 0, and the summary's Stale line says so.
    answers = _answers()
    db, _, _ = _collect(tmp_path, answers, wanted=["shiller"])
    for name in ("shiller.html", "ie_data.xls"):
        _age(tmp_path, name, 2 * DAY)
    answers[LINK + "&x=1"] = _shiller_workbook(labels=("Date", "CPI", "P", "Fraction", "E", "D", "Cyclically Adjusted"))
    monkeypatch.setattr(collect, "collect_market", partial(collect.collect_market, session=_Session(answers),
                                                           today="2026-10-24"))
    assert collect.main(["--shiller", "--db", str(db), "--cache-dir", str(tmp_path / "cache")]) == 0
    out = capsys.readouterr().out
    stale = [line for line in out.splitlines() if line.startswith("Stale: ")]
    assert len(stale) == 1 and stale[0].startswith(
        "Stale: shiller (ie_data.xls: the fetch failed (Shiller's Data sheet has no header 'CAPE'")
    assert "using the cached copy of" in stale[0] and "Failed: none" in out
    assert _rows(db, "shiller:cape") == [("1881-01-01", 18.47), ("2026-09-01", 39.5)]


def test_the_cli_end_to_end_prints_each_source_and_a_summary(tmp_path, monkeypatch, capsys):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export_market_series("fred:GDP", {"fred:GDP": [("2025-01-01", 1.0)]}, "u", "t")
    monkeypatch.setattr(collect, "collect_market", partial(collect.collect_market, session=_Session(_answers()),
                                                           today="2026-10-24"))
    assert collect.main(["--db", str(db), "--cache-dir", str(tmp_path / "cache"), "--treasury-from", "2025"]) == 0
    out = capsys.readouterr().out
    assert out.isascii()
    for text in ("fred:NCBEILQ027S", "fred:GDP", "shiller", "treasury", "ust:10y", "shiller:cape", "2026-10-23",
                 "4 of 4"):
        assert text in out
    assert _rows(db, "fred:GDP") == [("2026-01-01", 32000.0), ("2026-04-01", 32560.0)]
