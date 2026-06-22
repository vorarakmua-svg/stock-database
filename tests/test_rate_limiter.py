"""Tests for RateLimiter and RetryHandler."""

import types

import pytest
import requests

import src.fetchers.rate_limiter as rl
from src.fetchers.rate_limiter import RateLimiter, RetryHandler, is_transient_error


def _http_error(status_code):
    err = requests.exceptions.HTTPError()
    err.response = types.SimpleNamespace(status_code=status_code)
    return err


def test_rate_limiter_enforces_interval(monkeypatch):
    clock = {"t": 1000.0}
    sleeps = []
    monkeypatch.setattr(rl.time, "time", lambda: clock["t"])
    monkeypatch.setattr(rl.time, "sleep", lambda s: sleeps.append(s))

    limiter = RateLimiter(min_interval=0.5)
    assert limiter.wait() == 0.0  # first call never waits

    waited = limiter.wait()  # immediately again -> must wait the full interval
    assert waited == pytest.approx(0.5)
    assert sleeps == [pytest.approx(0.5)]
    assert limiter.request_count == 2


def test_is_transient_error_classification():
    assert is_transient_error(requests.exceptions.Timeout())
    assert is_transient_error(requests.exceptions.ConnectionError())
    assert is_transient_error(_http_error(503))
    assert is_transient_error(_http_error(429))
    assert not is_transient_error(_http_error(404))
    assert not is_transient_error(_http_error(403))
    assert not is_transient_error(ValueError("nope"))


def test_retry_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    handler = RetryHandler(max_retries=3, base_delay=0.01)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return "ok"

    result = handler.run(
        flaky,
        retryable_exceptions=(requests.exceptions.RequestException,),
        should_retry=is_transient_error,
    )
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_does_not_retry_non_transient(monkeypatch):
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    handler = RetryHandler(max_retries=3, base_delay=0.01)

    calls = {"n": 0}

    def fails_404():
        calls["n"] += 1
        raise _http_error(404)

    with pytest.raises(requests.exceptions.HTTPError):
        handler.run(
            fails_404,
            retryable_exceptions=(requests.exceptions.RequestException,),
            should_retry=is_transient_error,
        )
    assert calls["n"] == 1  # failed fast, no retries


def test_retry_exhausts_and_raises(monkeypatch):
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    handler = RetryHandler(max_retries=2, base_delay=0.01)

    calls = {"n": 0}

    def always_503():
        calls["n"] += 1
        raise _http_error(503)

    with pytest.raises(requests.exceptions.HTTPError):
        handler.run(
            always_503,
            retryable_exceptions=(requests.exceptions.RequestException,),
            should_retry=is_transient_error,
        )
    assert calls["n"] == 3  # initial + 2 retries
