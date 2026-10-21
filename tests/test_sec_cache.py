"""Tests for SEC payload caching (companyfacts/submissions)."""

import os
import time

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
