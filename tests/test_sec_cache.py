"""Tests for SEC payload caching (companyfacts/submissions)."""

import json
import os
import time

import pytest
import requests

from src.fetchers import sec_handler
from src.fetchers.sec_handler import SECHandler
from src.parsers.xbrl_parser import XBRLParser
from tests.test_xbrl_instance import CONTEXTS, instance


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return json.loads(self._payload) if isinstance(self._payload, bytes) else self._payload

    @property
    def content(self):
        return self._payload if isinstance(self._payload, bytes) else json.dumps(self._payload).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError()
            err.response = self
            raise err


def _handler(tmp_path, **kw):
    return SECHandler(cache_dir=tmp_path, **kw)


def test_companyfacts_served_from_cache(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    calls = {"n": 0}

    def fake_request(url, timeout=30):
        calls["n"] += 1
        return _FakeResp(200, {"cik": 320193, "facts": {"us-gaap": {}}})

    monkeypatch.setattr(handler, "_request", fake_request)

    first = handler.get_company_facts("320193")
    second = handler.get_company_facts("320193")  # within TTL -> cache hit
    assert first == second
    assert calls["n"] == 1


def test_cache_ttl_expiry_refetches(tmp_path, monkeypatch):
    handler = _handler(tmp_path, cache_ttl_seconds=10_000)
    calls = {"n": 0}

    def fake_request(url, timeout=30):
        calls["n"] += 1
        return _FakeResp(200, {"cik": 1, "facts": {}})

    monkeypatch.setattr(handler, "_request", fake_request)

    handler.get_company_facts("0000000001")
    # Age the cache file beyond the TTL.
    cache_file = handler._payload_cache_path("companyfacts", "0000000001")
    old = time.time() - 20_000
    os.utime(cache_file, (old, old))

    handler.get_company_facts("0000000001")
    assert calls["n"] == 2  # stale cache -> refetched


def test_cache_disabled_with_zero_ttl(tmp_path, monkeypatch):
    handler = _handler(tmp_path, cache_ttl_seconds=0)
    calls = {"n": 0}

    def fake_request(url, timeout=30):
        calls["n"] += 1
        return _FakeResp(200, {"cik": 2, "facts": {}})

    monkeypatch.setattr(handler, "_request", fake_request)

    handler.get_company_facts("0000000002")
    handler.get_company_facts("0000000002")
    assert calls["n"] == 2  # no caching


def test_404_not_cached(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    monkeypatch.setattr(handler, "_request", lambda url, timeout=30: _FakeResp(404, {}))
    assert handler.get_company_facts("0000000003") is None
    assert not handler._payload_cache_path("companyfacts", "0000000003").exists()


def test_a_failed_request_is_not_logged_as_a_success(tmp_path, monkeypatch, caplog):
    # A 403 on companyfacts logged an error and then "Successfully fetched SEC data" for the same ticker.
    handler = _handler(tmp_path)
    monkeypatch.setattr(handler, "ticker_to_cik", lambda ticker: "0000320193")
    monkeypatch.setattr(handler, "get_submissions", lambda cik: {"name": "Apple Inc.", "filings": []})

    def blocked(cik):
        raise requests.HTTPError("403 Client Error: Forbidden")

    monkeypatch.setattr(handler, "get_company_facts", blocked)
    with caplog.at_level("INFO"):
        data = handler.fetch_all("AAPL")
    assert data["company_facts_error"] == "403 Client Error: Forbidden"
    assert "Successfully fetched SEC data" not in caplog.text
    assert "SEC data for AAPL is incomplete" in caplog.text



def _successor_handler(tmp_path, monkeypatch, payloads, fail=()):
    """A handler whose SEC answers are *payloads* (URL suffix -> JSON), a suffix in *fail* answering 503; and the
    URLs it requested."""
    handler = _handler(tmp_path)
    handler._cik_mapping = {"XOM": "0002115436", "AAPL": "0000320193"}
    requested = []

    def fake_request(url, timeout=30):
        requested.append(url)
        for suffix, payload in payloads.items():
            if url.endswith(suffix):
                return _FakeResp(503 if suffix in fail else 200, payload)
        return _FakeResp(404, {})

    monkeypatch.setattr(handler, "_request", fake_request)
    return handler, requested


_Q2_2026 = {"val": 81.0e9, "start": "2026-04-01", "end": "2026-06-30", "fy": 2026, "fp": "Q2", "form": "10-Q",
            "filed": "2026-08-01", "accn": "0002115436-26-000004"}
_FY2025 = {"val": 339.2e9, "start": "2025-01-01", "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "10-K",
           "filed": "2026-02-18", "accn": "0000034088-26-000009"}
_XOM = {
    "companyfacts/CIK0002115436.json": {"entityName": "ExxonMobil Holdings Corp",
                                        "facts": {"us-gaap": {"Revenues": {"units": {"USD": [_Q2_2026]}}}}},
    "companyfacts/CIK0000034088.json": {"entityName": "EXXON MOBIL CORP",
                                        "facts": {"us-gaap": {"Revenues": {"units": {"USD": [_FY2025]}}}}},
    "submissions/CIK0002115436.json": {"name": "ExxonMobil Holdings Corp", "filings": {"recent": {}}},
}


def test_a_successor_registrant_reads_its_predecessors_filings(tmp_path, monkeypatch):
    # Q6, the user's decision of 2026-10-21: SEC's ticker map sends XOM to ExxonMobil Holdings Corp (CIK 2115436),
    # which has filed 10-Qs only, while the 20 years of history are Exxon Mobil Corp's (CIK 34088); XOM keeps them.
    handler, _ = _successor_handler(tmp_path, monkeypatch, _XOM)
    data = handler.fetch_all("XOM")
    assert (data["cik"], data["predecessor_cik"], data["predecessor_name"]) == (
        "0002115436", "0000034088", "EXXON MOBIL CORP")
    assert "predecessor_error" not in data
    assert data["company_facts"]["entityName"] == "ExxonMobil Holdings Corp"
    assert data["company_facts"]["facts"]["us-gaap"]["Revenues"]["units"]["USD"] == [_FY2025, _Q2_2026]
    assert XBRLParser().extract_annual_financials(data["company_facts"])["2025"]["revenue"] == 339.2e9


def test_a_registrant_without_a_predecessor_reads_its_own_filings(tmp_path, monkeypatch):
    handler, requested = _successor_handler(tmp_path, monkeypatch, {
        "companyfacts/CIK0000320193.json": {"entityName": "Apple Inc.", "facts": {"us-gaap": {}}},
        "submissions/CIK0000320193.json": {"name": "Apple Inc.", "filings": {"recent": {}}}})
    data = handler.fetch_all("AAPL")
    assert "predecessor_cik" not in data
    assert [url for url in requested if "companyfacts" in url] == [
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"]


def test_a_failed_predecessor_request_keeps_the_successors_filings(tmp_path, monkeypatch):
    # The successor's own facts still come through, with the failure named; a run with no annual record only adds
    # to what's stored (BUG-029).
    handler, _ = _successor_handler(tmp_path, monkeypatch, _XOM, fail=("companyfacts/CIK0000034088.json",))
    data = handler.fetch_all("XOM")
    assert data["predecessor_cik"] == "0000034088" and data["predecessor_error"]
    assert data["company_facts"]["facts"]["us-gaap"]["Revenues"]["units"]["USD"] == [_Q2_2026]


def test_a_predecessors_filings_from_the_successors_first_report_on_are_left_out(tmp_path, monkeypatch):
    # Exxon Mobil Corp keeps filing as the holding company's subsidiary (its own 10-Q of 2026-08-03, the day of
    # the holding company's first): from the successor's first report on, the successor reports, and a
    # subsidiary's figures aren't the company's.
    subsidiary_q2 = {**_Q2_2026, "val": 80.9e9, "accn": "0000034088-26-000031"}
    payloads = {**_XOM, "companyfacts/CIK0000034088.json": {
        "entityName": "EXXON MOBIL CORP",
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": [_FY2025, subsidiary_q2]}}}}}}
    handler, _ = _successor_handler(tmp_path, monkeypatch, payloads)
    data = handler.fetch_all("XOM")
    assert data["company_facts"]["facts"]["us-gaap"]["Revenues"]["units"]["USD"] == [_FY2025, _Q2_2026]


def test_blk_reads_blackrock_finances_filings(tmp_path, monkeypatch):
    # BLK, the user's decision of 2026-10-21 as for XOM: BlackRock, Inc. (CIK 2012383), the holding company formed
    # with the GIP acquisition, reports from its 10-Q of 2024-11-06; the history is BlackRock Finance, Inc.'s
    # (CIK 1364742), the old BlackRock.
    fy2023 = {"val": 17.859e9, "start": "2023-01-01", "end": "2023-12-31", "fy": 2023, "fp": "FY", "form": "10-K",
              "filed": "2024-02-23", "accn": "0001364742-24-000010"}
    q3_2024 = {"val": 5.197e9, "start": "2024-07-01", "end": "2024-09-30", "fy": 2024, "fp": "Q3", "form": "10-Q",
               "filed": "2024-11-06", "accn": "0002012383-24-000005"}
    handler, _ = _successor_handler(tmp_path, monkeypatch, {
        "companyfacts/CIK0002012383.json": {"entityName": "BlackRock, Inc.",
                                            "facts": {"us-gaap": {"Revenues": {"units": {"USD": [q3_2024]}}}}},
        "companyfacts/CIK0001364742.json": {"entityName": "BlackRock Finance, Inc.",
                                            "facts": {"us-gaap": {"Revenues": {"units": {"USD": [fy2023]}}}}},
        "submissions/CIK0002012383.json": {"name": "BlackRock, Inc.", "filings": {"recent": {}}}})
    handler._cik_mapping["BLK"] = "0002012383"
    data = handler.fetch_all("BLK")
    assert (data["predecessor_cik"], data["predecessor_name"]) == ("0001364742", "BlackRock Finance, Inc.")
    assert XBRLParser().extract_annual_financials(data["company_facts"])["2023"]["revenue"] == 17.859e9


def test_a_successors_debt_filing_doesnt_end_its_predecessors_history(tmp_path, monkeypatch):
    # The holding company's first filings with facts can be debt registrations (XOM's POSASR of 2026-07-01): only its
    # first annual or quarterly report takes over, so the predecessor's 10-Q filed before that still counts.
    q1_2026 = {"val": 85.138e9, "start": "2026-01-01", "end": "2026-03-31", "fy": 2026, "fp": "Q1", "form": "10-Q",
               "filed": "2026-05-04", "accn": "0000034088-26-000020"}
    shelf = {"val": 4.3e9, "end": "2026-03-31", "fy": 2026, "fp": "Q1", "form": "POSASR", "filed": "2026-04-01",
             "accn": "0002115436-26-000001"}
    payloads = {**_XOM,
                "companyfacts/CIK0002115436.json": {
                    "entityName": "ExxonMobil Holdings Corp",
                    "facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [shelf]}}},
                              "us-gaap": {"Revenues": {"units": {"USD": [_Q2_2026]}}}}},
                "companyfacts/CIK0000034088.json": {
                    "entityName": "EXXON MOBIL CORP",
                    "facts": {"us-gaap": {"Revenues": {"units": {"USD": [_FY2025, q1_2026]}}}}}}
    handler, _ = _successor_handler(tmp_path, monkeypatch, payloads)
    data = handler.fetch_all("XOM")
    assert data["company_facts"]["facts"]["us-gaap"]["Revenues"]["units"]["USD"] == [_FY2025, q1_2026, _Q2_2026]


# ---- BUG-251: the latest report the submissions list, and the companyfacts lack ----

#: DLR's filings as its submissions listed them on 2026-10-25, newest first, each with the fields the SEC serves:
#: a 424B7 of 2026-08-19, the 10-Q of 2026-07-31 and the 10-Q of 2026-05-01.
_DLR_424B7 = {"accessionNumber": "0001193125-26-357076", "form": "424B7", "filingDate": "2026-08-19", "reportDate": "",
              "acceptanceDateTime": "2026-08-19T20:55:58.000Z", "isXBRL": 1, "primaryDocument": "d914399d424b7.htm"}
_DLR_10Q2 = {"accessionNumber": "0001104659-26-089296", "form": "10-Q", "filingDate": "2026-07-31",
             "reportDate": "2026-06-30", "acceptanceDateTime": "2026-07-31T21:23:41.000Z", "isXBRL": 1,
             "primaryDocument": "dlr-20260630x10q.htm"}
_DLR_10Q1 = {"accessionNumber": "0001104659-26-054255", "form": "10-Q", "filingDate": "2026-05-01",
             "reportDate": "2026-03-31", "acceptanceDateTime": "2026-05-01T21:22:35.000Z", "isXBRL": 1,
             "primaryDocument": "dlr-20260331x10q.htm"}
#: The facts of DLR's companyfacts on 2026-10-25, cut to one each: its first quarter's revenue (the 10-Q of
#: 2026-05-01) and the 424B7's fee, but nothing of the 10-Q of 2026-07-31.
_DLR_Q1_REVENUE = {"val": 1635173000, "start": "2026-01-01", "end": "2026-03-31", "fy": 2026, "fp": "Q1",
                   "form": "10-Q", "filed": "2026-05-01", "frame": "CY2026Q1", "accn": "0001104659-26-054255"}
_DLR_FEE = {"val": 18777.23, "end": "2026-08-19", "form": "424B7", "filed": "2026-08-19", "frame": "CY2026Q3I",
            "accn": "0001193125-26-357076"}
#: The second quarter's revenue as the 10-Q of 2026-07-31 would give it, once the SEC has its facts (a test's figure).
_DLR_Q2_REVENUE = {"val": 1.71e9, "start": "2026-04-01", "end": "2026-06-30", "fy": 2026, "fp": "Q2", "form": "10-Q",
                   "filed": "2026-07-31", "frame": "CY2026Q2", "accn": "0001104659-26-089296"}


def _dlr(tmp_path, monkeypatch, filings, revenue=(_DLR_Q1_REVENUE,), fail=()):
    """DLR's handler: its submissions list *filings* (newest first); its companyfacts hold the 424B7's fee and the
    *revenue* entries (a 503 when "companyfacts" is in *fail*)."""
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime", "isXBRL", "primaryDocument")
    keys += ("isInlineXBRL",) if any("isInlineXBRL" in f for f in filings) else ()  # as the SEC serves it (Q17)
    handler, _ = _successor_handler(tmp_path, monkeypatch, {
        "companyfacts/CIK0001297996.json": {"entityName": "DIGITAL REALTY TRUST, INC.", "facts": {
            "ffd": {"TtlFeeAmt": {"units": {"USD": [_DLR_FEE]}}},
            "us-gaap": {"Revenues": {"units": {"USD": list(revenue)}}}}},
        "submissions/CIK0001297996.json": {"name": "DIGITAL REALTY TRUST, INC.", "filings": {"recent": {
            k: [f.get(k) for f in filings] for k in keys}}}},
        fail=tuple(f"{name}/CIK0001297996.json" for name in fail))
    handler._cik_mapping["DLR"] = "0001297996"
    return handler


def test_the_latest_report_the_companyfacts_lack_is_named(tmp_path, monkeypatch):
    # DLR's companyfacts of 2026-10-25 had no fact of its 10-Q of 2026-07-31, though they had the 424B7 filed after it:
    # the run's figures stopped at the March quarter and nothing said so.
    data = _dlr(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2, _DLR_10Q1]).fetch_all("DLR")
    assert data.get("missing_report") == {"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31"}


def test_companyfacts_with_the_latest_reports_facts_name_none(tmp_path, monkeypatch):
    data = _dlr(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2, _DLR_10Q1],
                revenue=(_DLR_Q1_REVENUE, _DLR_Q2_REVENUE)).fetch_all("DLR")
    assert "missing_report" not in data


@pytest.mark.parametrize("later", [
    {"form": "10-K/A", "primaryDocument": "dlr-10ka.htm"},  # a Part III amendment: no financial facts
    {"form": "10-Q/A", "primaryDocument": "dlr-10qa.htm"},
    {"form": "10-KT", "primaryDocument": "dlr-10kt.htm"},  # a transition report, which the parser doesn't read
    {"form": "8-K", "primaryDocument": "d8k.htm"},
    {"form": "10-Q", "isXBRL": 0, "primaryDocument": "dlr-10q.htm"},  # no XBRL, so never any facts
])
def test_only_a_report_as_filed_with_xbrl_is_looked_for(tmp_path, monkeypatch, later):
    # Filed after the 10-Q of 2026-05-01 and without facts: none of these is a report whose facts the parser would
    # read once the SEC had them, so the latest one looked for is that 10-Q, which the facts carry.
    filing = {**_DLR_10Q2, "accessionNumber": "0001104659-26-099999", "filingDate": "2026-09-15", **later}
    data = _dlr(tmp_path, monkeypatch, [_DLR_424B7, filing, _DLR_10Q1]).fetch_all("DLR")
    assert "missing_report" not in data


def test_an_ifrs_filers_report_is_found_among_its_ifrs_facts(tmp_path, monkeypatch):
    # A foreign filer's 20-F has its facts under ifrs-full, not us-gaap: they are its report's facts all the same.
    twenty_f = {"accessionNumber": "0001046179-26-000012", "form": "20-F", "filingDate": "2026-04-16",
                "reportDate": "2025-12-31", "acceptanceDateTime": "2026-04-16T09:30:00.000Z", "isXBRL": 1,
                "primaryDocument": "tsm-20251231.htm"}
    handler, _ = _successor_handler(tmp_path, monkeypatch, {
        "companyfacts/CIK0001046179.json": {"entityName": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "facts": {
            "ifrs-full": {"Revenue": {"units": {"TWD": [{
                "val": 3.81e12, "start": "2025-01-01", "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "20-F",
                "filed": "2026-04-16", "accn": "0001046179-26-000012"}]}}}}},
        "submissions/CIK0001046179.json": {"name": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "filings": {
            "recent": {k: [v] for k, v in twenty_f.items()}}}})
    handler._cik_mapping["TSM"] = "0001046179"
    assert "missing_report" not in handler.fetch_all("TSM")


def test_a_failed_companyfacts_request_leaves_the_latest_report_unread(tmp_path, monkeypatch):
    # The run read no facts at all, the latest report's among them.
    data = _dlr(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2, _DLR_10Q1], fail=("companyfacts",)).fetch_all("DLR")
    assert "company_facts_error" in data
    assert data.get("missing_report") == {"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31"}


def test_a_report_listed_without_its_accession_isnt_looked_for(tmp_path, monkeypatch):
    # There is nothing to look for among the facts, and nothing to name.
    data = _dlr(tmp_path, monkeypatch, [_DLR_424B7, {**_DLR_10Q2, "accessionNumber": ""}, _DLR_10Q1]).fetch_all("DLR")
    assert "missing_report" not in data


# ---- BUG-285: the latest report behind the newest 100 filings ----

#: A bank's pricing supplements, newest first: 150 424B2s filed after its reports, as JPM's 94 of 2026-10-22 and 23
#: filled the newest 100 of its feed (GS's latest 10-Q sat behind 3,159 newer filings on 2026-10-25).
_424B2S = [{"accessionNumber": f"0001213900-26-{100150 - n:06d}", "form": "424B2", "filingDate": "2026-10-23",
            "reportDate": "", "acceptanceDateTime": f"2026-10-23T16:{(149 - n) // 60:02d}:{(149 - n) % 60:02d}.000Z",
            "isXBRL": 0, "primaryDocument": f"ea{100150 - n}-424b2.htm"} for n in range(150)]


def test_a_report_behind_the_newest_100_filings_is_looked_for(tmp_path, monkeypatch):
    # The newest 100 hold 424B2s alone, so the check found no report and said complete, whatever the facts held.
    data = _dlr(tmp_path, monkeypatch, [*_424B2S, _DLR_10Q2, _DLR_10Q1]).fetch_all("DLR")
    assert data.get("missing_report") == {"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31"}


def test_a_report_behind_the_newest_100_filings_whose_facts_the_companyfacts_carry_names_none(tmp_path, monkeypatch):
    data = _dlr(tmp_path, monkeypatch, [*_424B2S, _DLR_10Q2, _DLR_10Q1],
                revenue=(_DLR_Q1_REVENUE, _DLR_Q2_REVENUE)).fetch_all("DLR")
    assert "missing_report" not in data


@pytest.mark.parametrize("later", [
    {"form": "10-K/A", "primaryDocument": "dlr-10ka.htm"},
    {"form": "10-Q/A", "primaryDocument": "dlr-10qa.htm"},
    {"form": "10-KT", "primaryDocument": "dlr-10kt.htm"},
    {"form": "8-K", "primaryDocument": "d8k.htm"},
    {"form": "10-Q", "isXBRL": 0, "primaryDocument": "dlr-10q.htm"},
])
def test_behind_the_newest_100_filings_only_a_report_as_filed_with_xbrl_is_looked_for(tmp_path, monkeypatch, later):
    # BUG-251's rule over the whole feed: the latest report looked for is the 10-Q of 2026-07-31, not the later
    # filing, which never gets facts that change the record.
    filing = {**_DLR_10Q2, "accessionNumber": "0001104659-26-099999", "filingDate": "2026-09-15", **later}
    data = _dlr(tmp_path, monkeypatch, [*_424B2S, filing, _DLR_10Q2, _DLR_10Q1]).fetch_all("DLR")
    assert data.get("missing_report") == {"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31"}


def test_the_submissions_keep_the_newest_100_filings_and_the_latest_report_behind_them(tmp_path, monkeypatch):
    # The handler's other callers (the insiders' Form 4s, the first filing date of a filer without annual figures) read
    # the newest 100 as before; the latest report comes beside them.
    submissions = _dlr(tmp_path, monkeypatch, [*_424B2S, _DLR_10Q2, _DLR_10Q1]).get_submissions("0001297996")
    assert submissions["filings"] == _424B2S[:100]
    assert submissions["latest_report"] == _DLR_10Q2


def test_submissions_cached_before_the_latest_report_was_kept_are_read_as_before(tmp_path, monkeypatch):
    # A cached submissions file written without "latest_report" (the handler's until BUG-285): its newest 100 are
    # looked through, as then.
    handler = _dlr(tmp_path, monkeypatch, [])
    path = handler._payload_cache_path("submissions", "0001297996")
    path.write_text(json.dumps({"name": "DIGITAL REALTY TRUST, INC.", "filings": [_DLR_424B7, _DLR_10Q2, _DLR_10Q1]}),
                    encoding="utf-8")
    data = handler.fetch_all("DLR")
    assert data["submissions"]["filings"] == [_DLR_424B7, _DLR_10Q2, _DLR_10Q1]
    assert data.get("missing_report") == {"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31"}


# ---- Q17: the reports the companyfacts lack, read from their own XBRL ----

#: The filings of DLR's feed as the SEC serves them, with the inline flag a report's instance is found by.
_DLR_10Q2_INLINE = {**_DLR_10Q2, "isInlineXBRL": 1}
_DLR_10Q1_INLINE = {**_DLR_10Q1, "isInlineXBRL": 1}
#: Where EDGAR keeps the 10-Q of 2026-07-31's documents.
_DLR_10Q2_FOLDER = "Archives/edgar/data/1297996/000110465926089296/"
#: Its instance, cut to the quarter's revenue (a test's figure), the half year's and a segment's.
_DLR_10Q2_INSTANCE = instance([("us-gaap:Revenues", "q2", "usd", "1712345000"),
                               ("us-gaap:Revenues", "h1", "usd", "3347518000"),
                               ("us-gaap:Revenues", "seg", "usd", "1000000000")],
                              contexts={**CONTEXTS, "seg": CONTEXTS["q2"]}, segments=("seg",))
#: The quarter's revenue as the instance gives it, in companyfacts' shape.
_Q2_FROM_INSTANCE = {"start": "2026-04-01", "end": "2026-06-30", "val": 1712345000, "accn": "0001104659-26-089296",
                     "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-07-31", "frame": "CY2026Q2"}
_H1_FROM_INSTANCE = {"start": "2026-01-01", "end": "2026-06-30", "val": 3347518000, "accn": "0001104659-26-089296",
                     "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-07-31"}


def _dlr_reading(tmp_path, monkeypatch, filings, documents, revenue=(_DLR_Q1_REVENUE,), fail=()):
    """DLR's handler (``_dlr``) whose EDGAR archive serves *documents* (a path under Archives/ -> bytes or JSON; 404
    for any other), and the archive's URLs it requested."""
    handler = _dlr(tmp_path, monkeypatch, filings, revenue=revenue, fail=fail)
    served = handler._request
    requested = []

    def request(url, timeout=30):
        if "/Archives/" in url:
            requested.append(url)
        for path, payload in documents.items():
            if url.endswith(path):
                return _FakeResp(200, payload)
        return served(url, timeout)

    monkeypatch.setattr(handler, "_request", request)
    return handler, requested


def _revenue(data):
    return data["company_facts"]["facts"]["us-gaap"]["Revenues"]["units"]["USD"]


def test_a_report_the_companyfacts_lack_is_read_from_its_own_xbrl(tmp_path, monkeypatch):
    # DLR's 10-Q of 2026-07-31: the run's figures stopped at the March quarter for months (BUG-251). Its instance
    # gives the second quarter's facts as the companyfacts would, beside theirs, for one more request.
    handler, requested = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE],
                                      {_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE})
    data = handler.fetch_all("DLR")
    assert _revenue(data) == [_DLR_Q1_REVENUE, _H1_FROM_INSTANCE, _Q2_FROM_INSTANCE]
    assert data["missing_report"] == {"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31",
                                      "recovered": True}
    assert data["reports_read"] == [{"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31",
                                     "instance": "dlr-20260630x10q_htm.xml"}]
    assert data["reports_unread"] == []
    assert requested == ["https://www.sec.gov/" + _DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml"]


def test_the_quarter_read_from_the_filing_is_parsed_as_the_companyfacts_would_give_it(tmp_path, monkeypatch):
    # The parser reads the second quarter from the instance's facts exactly as from the SEC's own, once it has them.
    handler, _ = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE],
                              {_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE})
    read = XBRLParser().extract_quarterly_financials(handler.fetch_all("DLR")["company_facts"])
    caught_up = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _DLR_Q1_REVENUE, _H1_FROM_INSTANCE, _Q2_FROM_INSTANCE]}}}}})
    assert read["2026-06-30"]["revenue"] == 1712345000
    assert read["2026-06-30"]["calendar_year"] == 2026 and read["2026-06-30"]["calendar_quarter"] == 2
    assert read == caught_up


def test_a_filing_that_isnt_inline_is_found_through_its_index(tmp_path, monkeypatch):
    # An older filing's instance is a .xml beside its schema and linkbases: the index names it, one request more.
    index = {"directory": {"item": [{"name": n} for n in (
        "0001104659-26-089296-index.html", "dlr-20260630.xsd", "dlr-20260630_cal.xml", "dlr-20260630_def.xml",
        "dlr-20260630_lab.xml", "dlr-20260630_pre.xml", "dlr-20260630.xml", "FilingSummary.xml", "R1.xml",
        "dlr-20260630x10q.htm")]}}
    handler, requested = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, {**_DLR_10Q2, "isInlineXBRL": 0}, _DLR_10Q1],
                                      {_DLR_10Q2_FOLDER + "index.json": index,
                                       _DLR_10Q2_FOLDER + "dlr-20260630.xml": _DLR_10Q2_INSTANCE})
    data = handler.fetch_all("DLR")
    assert data["reports_read"][0]["instance"] == "dlr-20260630.xml"
    assert [u.rsplit("/", 1)[1] for u in requested] == ["index.json", "dlr-20260630.xml"]


def test_an_inline_filing_without_its_extracted_instance_is_found_through_its_index(tmp_path, monkeypatch):
    index = {"directory": {"item": [{"name": "dlr-10q.htm"}, {"name": "dlr-20260630x10q_htm.xml"},
                                    {"name": "dlr-20260630.xsd"}]}}
    handler, requested = _dlr_reading(tmp_path, monkeypatch,
                                      [_DLR_424B7, {**_DLR_10Q2_INLINE, "primaryDocument": "dlr-10q.htm"}, _DLR_10Q1],
                                      {_DLR_10Q2_FOLDER + "index.json": index,
                                       _DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE})
    data = handler.fetch_all("DLR")
    assert [r["instance"] for r in data["reports_read"]] == ["dlr-20260630x10q_htm.xml"]
    assert [u.rsplit("/", 1)[1] for u in requested] == ["dlr-10q_htm.xml", "index.json", "dlr-20260630x10q_htm.xml"]


@pytest.mark.parametrize("names, primary, found", [
    (["c-20260630.htm", "c-20260630_htm.xml", "c-20260630.xsd", "c-20260630_cal.xml", "FilingSummary.xml"],
     "c-20260630.htm", "c-20260630_htm.xml"),
    (["d10q.htm", "other_htm.xml", "c.xsd"], "d10q.htm", "other_htm.xml"),
    (["a_htm.xml", "z10q.htm", "z10q_htm.xml"], "z10q.htm", "z10q_htm.xml"),
    (["R1.xml", "aapl-20180929_lab.xml", "aapl-20180929.xml", "aapl-20180929.xsd", "a.xml"], "", "aapl-20180929.xml"),
    (["jpm-20150331.xml", "jpm-20150331_pre.xml", "jpm-20150331_def.xml"], "jpm10q.htm", "jpm-20150331.xml"),
    (["d10q.htm", "FilingSummary.xml", "c_cal.xml", "R2.xml", "c.xsd"], "d10q.htm", None),
])
def test_the_instance_among_a_filings_documents(names, primary, found):
    assert sec_handler._instance_name(names, primary) == found


def test_every_report_the_companyfacts_lack_is_read_back_to_the_first_they_carry(tmp_path, monkeypatch):
    # Citigroup's companyfacts carried none of its 2026 10-Qs (BUG-295): each is read, newest first; the reports they
    # carry are not.
    q3 = {**_DLR_10Q2_INLINE, "accessionNumber": "0001104659-26-110001", "filingDate": "2026-10-30",
          "reportDate": "2026-09-30", "acceptanceDateTime": "2026-10-30T21:00:00.000Z",
          "primaryDocument": "dlr-20260930x10q.htm"}
    q3_instance = instance([("us-gaap:Revenues", "q3", "usd", "1801000000")], focus=("2026", "Q3"), focus_context="q3",
                           contexts={"q3": "<period><startDate>2026-07-01</startDate><endDate>2026-09-30</endDate>"
                                           "</period>"})
    handler, requested = _dlr_reading(tmp_path, monkeypatch, [q3, _DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE], {
        "000110465926110001/dlr-20260930x10q_htm.xml": q3_instance,
        _DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE})
    data = handler.fetch_all("DLR")
    assert [r["accession"] for r in data["reports_read"]] == ["0001104659-26-110001", "0001104659-26-089296"]
    assert data["missing_report"]["accession"] == "0001104659-26-110001" and data["missing_report"]["recovered"]
    assert sorted((e["end"], e["val"]) for e in _revenue(data) if e.get("frame")) == [
        ("2026-03-31", 1635173000), ("2026-06-30", 1712345000), ("2026-09-30", 1801000000)]
    assert len(requested) == 2


def test_at_most_a_years_reports_are_read(tmp_path, monkeypatch):
    # A filer whose companyfacts carry no report at all: the latest four are looked for, no more.
    reports = [{**_DLR_10Q2_INLINE, "accessionNumber": f"0001104659-26-0{n:05d}", "filingDate": f"2026-0{n}-01",
                "primaryDocument": f"q{n}.htm"} for n in range(9, 3, -1)]
    handler, _ = _dlr_reading(tmp_path, monkeypatch, reports, {}, revenue=())
    data = handler.fetch_all("DLR")
    assert [r["accession"] for r in data["reports_unread"]] == [r["accessionNumber"] for r in reports[:4]]


def test_the_submissions_keep_the_newest_reports(tmp_path, monkeypatch):
    submissions = _dlr(tmp_path, monkeypatch, [*_424B2S, _DLR_10Q2, _DLR_10Q1]).get_submissions("0001297996")
    assert submissions.get("reports") == [_DLR_10Q2, _DLR_10Q1]


def test_the_submissions_keep_a_years_reports_at_most(tmp_path, monkeypatch):
    reports = [{**_DLR_10Q2, "accessionNumber": f"0001104659-26-0{n:05d}", "filingDate": f"2026-0{n}-01"}
               for n in range(9, 3, -1)]
    submissions = _dlr(tmp_path, monkeypatch, [_DLR_424B7, *reports]).get_submissions("0001297996")
    assert submissions["reports"] == reports[:4]


def test_submissions_cached_before_the_reports_were_kept_give_the_latest_report(tmp_path, monkeypatch):
    # A file cached by an earlier build: the newest 100 filings and the latest report are looked through.
    handler, _ = _dlr_reading(tmp_path, monkeypatch, [], {
        _DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE})
    handler._payload_cache_path("submissions", "0001297996").write_text(json.dumps(
        {"name": "DIGITAL REALTY TRUST, INC.", "filings": _424B2S[:100], "latest_report": _DLR_10Q2_INLINE}),
        encoding="utf-8")
    assert [r["accession"] for r in handler.fetch_all("DLR")["reports_read"]] == ["0001104659-26-089296"]


def test_a_report_read_once_is_read_from_the_cache_after(tmp_path, monkeypatch):
    # An offline re-collection reads it as the run that fetched it did; so does the refresh's handler, which reads no
    # cached companyfacts or submissions: a filing's documents never change.
    documents = {_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE}
    handler, _ = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE], documents)
    first = handler.fetch_all("DLR")
    for fresh in (False, True):
        again, requested = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE], {})
        if fresh:
            monkeypatch.setattr(again, "_read_payload_cache", lambda path: None)
        data = again.fetch_all("DLR")
        assert _revenue(data) == _revenue(first) and data["reports_read"] == first["reports_read"]
        assert requested == []


def test_a_cached_read_of_another_shape_is_read_again(tmp_path, monkeypatch):
    handler, requested = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE],
                                      {_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE})
    handler._filing_cache_path("0001297996", "0001104659-26-089296").write_text(json.dumps(
        {"version": 0, "instance": "old.xml", "facts": {}}), encoding="utf-8")
    assert handler.fetch_all("DLR")["reports_read"][0]["instance"] == "dlr-20260630x10q_htm.xml"
    assert len(requested) == 1


def test_with_the_cache_off_a_report_is_read_each_time(tmp_path, monkeypatch):
    handler, requested = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE],
                                      {_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE})
    handler.cache_ttl_seconds = 0
    handler.fetch_all("DLR")
    handler.fetch_all("DLR")
    assert len(requested) == 2
    assert not list(tmp_path.glob("xbrl_*.json"))
    # Nor is a file an earlier run cached read.
    handler._filing_cache_path("0001297996", "0001104659-26-089296").write_text(json.dumps(
        {"version": 1, "instance": "cached.xml", "facts": {}}), encoding="utf-8")
    assert handler.fetch_all("DLR")["reports_read"][0]["instance"] == "dlr-20260630x10q_htm.xml"


@pytest.mark.parametrize("documents, why", [
    ({}, "HTTP 404 for dlr-20260630x10q_htm.xml; HTTP 404 for the filing's index"),
    ({_DLR_10Q2_FOLDER + "index.json": {"directory": {"item": [{"name": "dlr-20260630x10q.htm"}]}}},
     "HTTP 404 for dlr-20260630x10q_htm.xml; no XBRL instance among the filing's documents"),
    ({_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": b"<html>maintenance</html"}, "not an XBRL instance"),
    ({_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": instance([("dlr:Colocation", "q2", "usd", "1")])},
     "no fact of CIK 1297996 the companyfacts would keep"),
])
def test_a_report_that_cant_be_read_leaves_the_gap_and_says_why(tmp_path, monkeypatch, documents, why):
    handler, _ = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE], documents)
    data = handler.fetch_all("DLR")
    assert data["missing_report"] == {"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31"}
    assert data["reports_read"] == [] and len(data["reports_unread"]) == 1
    assert data["reports_unread"][0]["why"].startswith(why)
    assert _revenue(data) == [_DLR_Q1_REVENUE]
    assert not list(tmp_path.glob("xbrl_*.json"))


def test_a_failed_request_for_a_report_never_fails_the_collection(tmp_path, monkeypatch):
    handler, _ = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE], {})
    served = handler._request

    def request(url, timeout=30):
        if "/Archives/" in url:
            raise requests.exceptions.ConnectionError("network blocked")
        return served(url, timeout)

    monkeypatch.setattr(handler, "_request", request)
    data = handler.fetch_all("DLR")
    assert data["reports_unread"] == [{"form": "10-Q", "accession": "0001104659-26-089296", "filed": "2026-07-31",
                                       "why": "network blocked"}]
    assert "submissions" in data and "error" not in data and "recovered" not in data["missing_report"]


def test_a_later_report_read_beside_an_earlier_one_that_cant_be_is_no_recovery(tmp_path, monkeypatch):
    q3 = {**_DLR_10Q2_INLINE, "accessionNumber": "0001104659-26-110001", "filingDate": "2026-10-30",
          "primaryDocument": "dlr-20260930x10q.htm"}
    handler, _ = _dlr_reading(tmp_path, monkeypatch, [q3, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE], {
        "000110465926110001/dlr-20260930x10q_htm.xml": _DLR_10Q2_INSTANCE})
    data = handler.fetch_all("DLR")
    assert [r["accession"] for r in data["reports_read"]] == ["0001104659-26-110001"]
    assert [r["accession"] for r in data["reports_unread"]] == ["0001104659-26-089296"]
    assert "recovered" not in data["missing_report"]


def test_no_report_is_read_without_the_companyfacts(tmp_path, monkeypatch):
    # A failed companyfacts request: the run has an error and no figures to complete.
    handler, requested = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE],
                                      {_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE},
                                      fail=("companyfacts",))
    data = handler.fetch_all("DLR")
    assert "reports_read" not in data and requested == []


def test_companyfacts_with_the_report_read_nothing_more(tmp_path, monkeypatch):
    # Once the SEC carries the report its facts are the companyfacts' own: the filing isn't read, nor its cache.
    handler, requested = _dlr_reading(tmp_path, monkeypatch, [_DLR_424B7, _DLR_10Q2_INLINE, _DLR_10Q1_INLINE],
                                      {_DLR_10Q2_FOLDER + "dlr-20260630x10q_htm.xml": _DLR_10Q2_INSTANCE},
                                      revenue=(_DLR_Q1_REVENUE, _DLR_Q2_REVENUE))
    data = handler.fetch_all("DLR")
    assert "missing_report" not in data and "reports_read" not in data
    assert _revenue(data) == [_DLR_Q1_REVENUE, _DLR_Q2_REVENUE]
    assert requested == []


def test_the_companyfacts_win_where_they_carry_the_accession():
    # merge_report_facts adds a report's facts beside the companyfacts' and never over them; the companyfacts are left
    # as they were.
    facts = {"entityName": "DLR", "facts": {"us-gaap": {"Revenues": {"label": "Revenues", "units": {
        "USD": [_DLR_Q1_REVENUE, _DLR_Q2_REVENUE]}}}}}
    piece = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_Q2_FROM_INSTANCE, {**_DLR_Q1_REVENUE, "val": 1}]}},
        "Assets": {"units": {"USD": [{"end": "2026-06-30", "val": 4.86e10, "accn": "0001104659-26-110001"}]}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2026-07-24", "val": 341234567, "accn": "0001104659-26-110001"}]}}}}}
    merged = sec_handler.merge_report_facts(facts, [piece])
    assert merged["facts"]["us-gaap"]["Revenues"] == {"label": "Revenues", "units": {
        "USD": [_DLR_Q1_REVENUE, _DLR_Q2_REVENUE]}}
    assert merged["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] == 4.86e10
    assert merged["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"][0]["val"] == 341234567
    assert merged["entityName"] == "DLR"
    assert facts["facts"]["us-gaap"].keys() == {"Revenues"} and "dei" not in facts["facts"]
    assert facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"] == [_DLR_Q1_REVENUE, _DLR_Q2_REVENUE]


def test_a_reports_facts_are_added_beside_the_companyfacts_of_the_same_concept():
    facts = {"facts": {"us-gaap": {"Revenues": {"label": "Revenues", "units": {"USD": [_DLR_Q1_REVENUE]}}}}}
    merged = sec_handler.merge_report_facts(facts, [{"facts": {"us-gaap": {"Revenues": {"units": {
        "USD": [_H1_FROM_INSTANCE, _Q2_FROM_INSTANCE]}}}}}])
    assert merged["facts"]["us-gaap"]["Revenues"] == {"label": "Revenues", "units": {
        "USD": [_DLR_Q1_REVENUE, _H1_FROM_INSTANCE, _Q2_FROM_INSTANCE]}}
    assert facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"] == [_DLR_Q1_REVENUE]


def test_reports_without_facts_go_back_to_the_first_the_facts_carry():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [_DLR_Q1_REVENUE]}}}}}
    q3 = {**_DLR_10Q2, "accessionNumber": "0001104659-26-110001", "filingDate": "2026-10-30"}
    old = {**_DLR_10Q1, "accessionNumber": "0001104659-26-000001", "filingDate": "2026-02-20", "form": "10-K"}
    listed = {"reports": [q3, _DLR_10Q2, _DLR_10Q1, old], "filings": [_DLR_424B7, q3], "latest_report": q3}
    assert sec_handler.reports_without_facts(listed, facts) == [q3, _DLR_10Q2]
    assert sec_handler.reports_without_facts(listed, {"facts": {}}) == [q3, _DLR_10Q2, _DLR_10Q1, old]
    amended = {"filings": [_DLR_424B7, {**_DLR_10Q2, "form": "10-Q/A"}, _DLR_10Q1]}
    assert sec_handler.reports_without_facts(amended, facts) == []
