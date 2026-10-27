"""Tests for SEC payload caching (companyfacts/submissions)."""

import json
import os
import time

import pytest
import requests

from src.fetchers.sec_handler import SECHandler
from src.parsers.xbrl_parser import XBRLParser


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

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
    handler, _ = _successor_handler(tmp_path, monkeypatch, {
        "companyfacts/CIK0001297996.json": {"entityName": "DIGITAL REALTY TRUST, INC.", "facts": {
            "ffd": {"TtlFeeAmt": {"units": {"USD": [_DLR_FEE]}}},
            "us-gaap": {"Revenues": {"units": {"USD": list(revenue)}}}}},
        "submissions/CIK0001297996.json": {"name": "DIGITAL REALTY TRUST, INC.", "filings": {"recent": {
            k: [f[k] for f in filings] for k in keys}}}},
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
