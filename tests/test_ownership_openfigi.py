"""OpenFIGI's mapping API (Milestone 3): the tickers of the 13F CUSIPs outside our universe, keyless: up to 10 codes a
request (a CINS asked as one), 25 requests a minute, each answer cached on disk ("no match" too) and read before any
request; a 429 or a failed connection is retried once, and a request that fails raises OSError, never taken for "no
match" (docs/superpowers/specs/2026-10-22-sec-ownership-design.md). The listings below are shaped like OpenFIGI's
answers; no test touches the network.
"""
import json

import pytest
import requests

from src.fetchers.rate_limiter import RateLimiter
from src.ownership import openfigi
from src.ownership.openfigi import Listing, OpenFIGI, pick_listing

URL = "https://api.openfigi.com/v3/mapping"
BRK_B_CUSIP = "084670702"
APPLE_CUSIP = "037833100"
NOT_FOUND = {"warning": "No identifier found."}


def _listing(ticker, exch, figi, name="BERKSHIRE HATHAWAY INC-CL B", kind="Common Stock"):
    return {"figi": figi, "name": name, "ticker": ticker, "exchCode": exch, "compositeFIGI": "BBG000DWG505",
            "securityType": kind, "marketSector": "Equity", "shareClassFIGI": "BBG001S5TZJ6"}


#: Berkshire's class B on three exchanges, the US composite in the middle, as OpenFIGI writes its ticker.
BRK_B = [_listing("BRK/B", "UN", "BBG000DWCFL4"), _listing("BRK/B", "US", "BBG000DWG505"),
         _listing("BRK/B", "UA", "BBG000DWCH91")]
APPLE = _listing("AAPL", "US", "BBG000B9XRY4", name="APPLE INC")


class _Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}

    def json(self):
        return json.loads(self.body) if isinstance(self.body, bytes) else self.body


def _answers(table):
    """A server answering each job from *table* (CUSIP -> its job's result), in the jobs' order."""
    return lambda jobs: _Response(200, [table[job["idValue"]] for job in jobs])


class _Session:
    """Answers each POST with the next of *answers*: a response, a function of the jobs giving one, or an exception
    to raise. Records each request (url, keyword arguments)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer(kwargs["json"]) if callable(answer) else answer

    def asked(self, i):
        """The CUSIPs of the *i*-th request."""
        return [job["idValue"] for job in self.requests[i][1]["json"]]


def _client(tmp_path, *answers):
    """A client on *answers*, its limiter without a wait; also its session and the seconds it slept."""
    slept = []
    session = _Session(*answers)
    client = OpenFIGI(tmp_path / "cache", session=session, limiter=RateLimiter(min_interval=0), sleep=slept.append)
    return client, session, slept


def _cache(tmp_path, cusip):
    return tmp_path / "cache" / "openfigi" / f"{cusip}.json"


def test_a_match_takes_the_us_composite_of_its_listings(tmp_path):
    client, session, _ = _client(tmp_path, _answers({BRK_B_CUSIP: {"data": BRK_B}}))
    assert client.lookup([BRK_B_CUSIP]) == {BRK_B_CUSIP: Listing(
        ticker="BRK-B", name="BERKSHIRE HATHAWAY INC-CL B", security_type="Common Stock", exch_code="US",
        figi="BBG000DWG505")}
    url, kwargs = session.requests[0]
    assert url == URL and kwargs["json"] == [{"idType": "ID_CUSIP", "idValue": BRK_B_CUSIP}]


def test_without_a_us_composite_the_first_listing_with_a_ticker_else_the_first():
    london = _listing(None, "LN", "F1", name="X PLC")
    frankfurt = _listing("X1", "GR", "F2", name="X PLC")
    paris = _listing("X2", "FP", "F3", name="X PLC")
    assert pick_listing([london, frankfurt, paris]) == Listing("X1", "X PLC", "Common Stock", "GR", "F2")
    assert pick_listing([london, dict(london, figi="F4")]) == Listing(None, "X PLC", "Common Stock", "LN", "F1")
    assert pick_listing([]) is None


def test_openfigis_slash_in_a_ticker_becomes_a_dash():
    assert pick_listing([_listing("BF/B", "US", "F1")]).ticker == "BF-B"
    assert pick_listing([_listing("BRK/A", "US", "F1")]).ticker == "BRK-A"
    assert pick_listing([_listing("KO", "US", "F1")]).ticker == "KO"


def test_no_identifier_found_is_no_match_and_is_cached(tmp_path):
    client, _, _ = _client(tmp_path, _answers({"000000000": NOT_FOUND}))
    assert client.lookup(["000000000"]) == {"000000000": None}
    assert json.loads(_cache(tmp_path, "000000000").read_text(encoding="utf-8")) == NOT_FOUND
    again, idle, _ = _client(tmp_path)  # no answers: a request would fail
    assert again.lookup(["000000000"]) == {"000000000": None} and idle.requests == []


def test_a_cached_answer_is_used_before_any_request(tmp_path):
    first, _, _ = _client(tmp_path, _answers({BRK_B_CUSIP: {"data": BRK_B}}))
    first.lookup([BRK_B_CUSIP])
    client, session, _ = _client(tmp_path, _answers({APPLE_CUSIP: {"data": [APPLE]}}))
    found = client.lookup([BRK_B_CUSIP, APPLE_CUSIP])
    assert [(cusip, listing.ticker) for cusip, listing in found.items()] == [(BRK_B_CUSIP, "BRK-B"),
                                                                           (APPLE_CUSIP, "AAPL")]
    assert len(session.requests) == 1 and session.asked(0) == [APPLE_CUSIP]


def test_ten_cusips_a_request_each_asked_once(tmp_path):
    cusips = [f"{i:09d}" for i in range(25)]
    client, session, _ = _client(tmp_path, *[_answers(dict.fromkeys(cusips, NOT_FOUND))] * 3)
    assert client.lookup(cusips + cusips[:3]) == dict.fromkeys(cusips)
    assert [session.asked(i) for i in range(len(session.requests))] == [cusips[:10], cusips[10:20], cusips[20:]]
    assert all(job["idType"] == "ID_CUSIP" for _, kwargs in session.requests for job in kwargs["json"])
    assert openfigi.BATCH_SIZE == 10


def test_a_cins_is_asked_as_one():
    # A code whose first character is a letter is a foreign issuer's CINS: OpenFIGI finds it only as an ID_CINS.
    assert [openfigi.id_type(code) for code in ("H1467J104", "G54950103", "037833100", "02079K305", "84670702A")] == [
        "ID_CINS", "ID_CINS", "ID_CUSIP", "ID_CUSIP", "ID_CUSIP"]


def test_each_job_has_its_codes_type(tmp_path):
    codes = [APPLE_CUSIP, "H1467J104", "G54950103"]
    client, session, _ = _client(tmp_path, _answers(dict.fromkeys(codes, NOT_FOUND)))
    client.lookup(codes)
    assert session.requests[0][1]["json"] == [{"idType": "ID_CUSIP", "idValue": APPLE_CUSIP},
                                              {"idType": "ID_CINS", "idValue": "H1467J104"},
                                              {"idType": "ID_CINS", "idValue": "G54950103"}]


def test_the_rate_limiter_paces_every_request_the_retries_too(tmp_path):
    limiter = RateLimiter(min_interval=0)
    cusips = [f"{i:09d}" for i in range(15)]
    answer = _answers(dict.fromkeys(cusips, NOT_FOUND))
    session = _Session(_Response(429, None, {"Retry-After": "1"}), answer,
                       requests.exceptions.ConnectTimeout("connect timed out"), answer)
    OpenFIGI(tmp_path / "cache", session=session, limiter=limiter, sleep=lambda seconds: None).lookup(cusips)
    assert limiter.request_count == len(session.requests) == 4
    # 2.5 seconds apart: 24 requests a minute, under OpenFIGI's 25 without a key.
    assert OpenFIGI(tmp_path / "cache").limiter.min_interval == 2.5


def test_a_429_waits_as_asked_then_retries_once(tmp_path):
    client, session, slept = _client(tmp_path, _Response(429, None, {"Retry-After": "7"}),
                                     _answers({APPLE_CUSIP: {"data": [APPLE]}}))
    assert client.lookup([APPLE_CUSIP])[APPLE_CUSIP].ticker == "AAPL"
    assert slept == [7.0] and len(session.requests) == 2


def test_a_429_that_doesnt_say_how_long_waits_a_minute(tmp_path):
    for i, headers in enumerate(({}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, {"Retry-After": "-5"},
                                 {"Retry-After": "inf"})):
        client, _, slept = _client(tmp_path / str(i), _Response(429, None, headers),
                                   _answers({APPLE_CUSIP: NOT_FOUND}))
        assert client.lookup([APPLE_CUSIP]) == {APPLE_CUSIP: None}
        assert slept == [60.0]


def test_a_second_429_raises_and_caches_nothing(tmp_path):
    client, session, slept = _client(tmp_path, _Response(429), _Response(429))
    with pytest.raises(OSError, match="429"):
        client.lookup([APPLE_CUSIP])
    assert len(session.requests) == 2 and slept == [60.0]
    assert not _cache(tmp_path, APPLE_CUSIP).exists()


@pytest.mark.parametrize("dropped", [
    requests.exceptions.ConnectTimeout("connect timed out"),  # as OpenFIGI's connection did once, mid-run
    requests.exceptions.ConnectionError("connection refused"),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ChunkedEncodingError("connection broken"),
])
def test_a_connection_that_fails_is_retried_once_after_a_minute(tmp_path, dropped):
    client, session, slept = _client(tmp_path, dropped, _answers({APPLE_CUSIP: {"data": [APPLE]}}))
    assert client.lookup([APPLE_CUSIP])[APPLE_CUSIP].ticker == "AAPL"
    assert slept == [60.0] and len(session.requests) == 2


def test_a_connection_that_fails_twice_raises_and_caches_nothing(tmp_path):
    client, session, slept = _client(tmp_path, requests.exceptions.ConnectTimeout("connect timed out"),
                                     requests.exceptions.ConnectionError("connection reset"))
    with pytest.raises(OSError, match="connection reset"):
        client.lookup([APPLE_CUSIP])
    assert slept == [60.0] and len(session.requests) == 2
    assert not _cache(tmp_path, APPLE_CUSIP).exists()


def test_a_lookup_that_stops_is_resumed_from_the_cache(tmp_path):
    cusips = [f"{i:09d}" for i in range(25)]
    answer = _answers(dict.fromkeys(cusips, NOT_FOUND))
    dropped = requests.exceptions.ConnectTimeout("connect timed out")
    client, _, _ = _client(tmp_path, answer, dropped, dropped)
    with pytest.raises(OSError):
        client.lookup(cusips)  # the first ten answered, the second request's connection failing twice
    again, session, _ = _client(tmp_path, answer, answer)
    assert again.lookup(cusips) == dict.fromkeys(cusips)
    assert [session.asked(i) for i in range(len(session.requests))] == [cusips[10:20], cusips[20:]]


@pytest.mark.parametrize("answer", [
    _Response(500, None),
    _Response(503, None),
    _Response(502, [NOT_FOUND]),  # an error status, whatever its body says
    _Response(403, {"error": "Forbidden"}),
    requests.exceptions.InvalidURL("invalid URL"),  # a failure of the request, not of its connection
    _Response(200, b"<html>maintenance</html>"),  # not JSON
    _Response(200, {"error": "Invalid request body"}),  # not a list of answers
    _Response(200, []),  # fewer answers than jobs
    _Response(200, ["No identifier found."]),  # an answer that isn't one
    _Response(200, [{}]),
])
def test_any_other_failure_raises_oserror_and_caches_nothing(tmp_path, answer):
    client, session, slept = _client(tmp_path, answer)
    with pytest.raises(OSError):
        client.lookup([APPLE_CUSIP])
    assert len(session.requests) == 1 and slept == []  # retried only after a 429 or a failed connection
    assert not _cache(tmp_path, APPLE_CUSIP).exists()


def test_a_malformed_code_is_a_definitive_no_match_and_is_cached(tmp_path):
    # OpenFIGI refuses a code whose check digit is wrong (78462F953 and B57FG04CA in the gurus' filings): no request
    # will ever map it, so the answer is kept like "No identifier found".
    malformed = {"error": "Invalid idValue format."}
    client, _, _ = _client(tmp_path, _answers({"78462F953": malformed, "B57FG04CA": malformed,
                                               APPLE_CUSIP: {"data": [APPLE]}}))
    assert client.lookup(["78462F953", "B57FG04CA", APPLE_CUSIP]) == {
        "78462F953": None, "B57FG04CA": None,
        APPLE_CUSIP: Listing("AAPL", "APPLE INC", "Common Stock", "US", "BBG000B9XRY4")}
    assert json.loads(_cache(tmp_path, "B57FG04CA").read_text(encoding="utf-8")) == malformed
    again, idle, _ = _client(tmp_path)  # no answers: a request would fail
    assert again.lookup(["78462F953", "B57FG04CA"]) == {"78462F953": None, "B57FG04CA": None}
    assert idle.requests == []


def test_a_job_answered_with_an_error_raises_and_is_never_taken_for_no_match(tmp_path):
    client, _, _ = _client(tmp_path, _answers({APPLE_CUSIP: {"data": [APPLE]},
                                               "99999999X": {"error": "Unexpected error."}}))
    with pytest.raises(OSError, match="99999999X"):
        client.lookup([APPLE_CUSIP, "99999999X"])
    assert _cache(tmp_path, APPLE_CUSIP).exists() and not _cache(tmp_path, "99999999X").exists()
    # The next lookup asks only the one that failed.
    again, session, _ = _client(tmp_path, _answers({"99999999X": NOT_FOUND}))
    assert again.lookup([APPLE_CUSIP, "99999999X"]) == {
        APPLE_CUSIP: Listing("AAPL", "APPLE INC", "Common Stock", "US", "BBG000B9XRY4"), "99999999X": None}
    assert session.asked(0) == ["99999999X"]


def test_a_job_answered_with_an_error_leaves_the_others_answered(tmp_path):
    # A job's error is that code's failure alone: the other jobs of the request, and the requests after it, are
    # answered and kept.
    codes = [f"{i:09d}" for i in range(12)]
    table = dict.fromkeys(codes, NOT_FOUND)
    table[codes[3]] = {"error": "Unexpected error."}
    table[codes[11]] = {"data": [APPLE]}
    client, session, _ = _client(tmp_path, _answers(table), _answers(table))
    with pytest.raises(openfigi.JobErrors) as raised:
        client.lookup(codes)
    assert len(session.requests) == 2
    assert raised.value.failed == {codes[3]: "Unexpected error."}
    assert raised.value.found == {**dict.fromkeys(codes[:3] + codes[4:11]),
                                  codes[11]: Listing("AAPL", "APPLE INC", "Common Stock", "US", "BBG000B9XRY4")}
    assert isinstance(raised.value, OSError) and str(raised.value) == f"OpenFIGI: {codes[3]}: Unexpected error."


def test_a_cached_answer_that_doesnt_read_is_asked_again(tmp_path):
    for broken in ('{"da', '{"something": "else"}', '["No identifier found."]'):
        cached = _cache(tmp_path, APPLE_CUSIP)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(broken, encoding="utf-8")
        client, session, _ = _client(tmp_path, _answers({APPLE_CUSIP: {"data": [APPLE]}}))
        assert client.lookup([APPLE_CUSIP])[APPLE_CUSIP].ticker == "AAPL" and len(session.requests) == 1
        assert json.loads(cached.read_text(encoding="utf-8")) == {"data": [APPLE]}


def test_an_empty_list_of_listings_is_no_match(tmp_path):
    client, _, _ = _client(tmp_path, _answers({APPLE_CUSIP: {"data": []}}))
    assert client.lookup([APPLE_CUSIP]) == {APPLE_CUSIP: None}


def test_only_cusips_are_asked_or_cached(tmp_path):
    # A CUSIP names its cache file: nine letters or digits, as the 13F parser upper-cases them.
    client, session, _ = _client(tmp_path)
    for bad in ("../../x.js", "03783310", "0378331000", "03783310*", "03783310a", ""):
        with pytest.raises(ValueError):
            client.lookup([bad])
    assert session.requests == [] and not (tmp_path / "cache").exists()


def test_keyless_with_the_librarys_own_user_agent(tmp_path):
    client, session, _ = _client(tmp_path, _answers({APPLE_CUSIP: NOT_FOUND}))
    client.lookup([APPLE_CUSIP])
    _, kwargs = session.requests[0]
    assert sorted(kwargs) == ["json", "timeout"]  # no API key, no headers of its own
    default = OpenFIGI(tmp_path / "cache")
    assert "X-OPENFIGI-APIKEY" not in default.session.headers
    assert default.session.headers["User-Agent"] == requests.utils.default_user_agent()
