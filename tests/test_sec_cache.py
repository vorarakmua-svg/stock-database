"""Tests for SEC payload caching (companyfacts/submissions)."""

import os
import time

import requests

from src.fetchers.sec_handler import SECHandler


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

