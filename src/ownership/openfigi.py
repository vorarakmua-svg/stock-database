"""OpenFIGI's mapping API (Milestone 3): the listing behind each 13F CUSIP outside our universe, asked without an API
key (docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

A request is a POST of up to 10 jobs, ``{"idType": ..., "idValue": code}`` each, and OpenFIGI answers them in their
order: the code's listings (``{"data": [...]}``), a warning when it knows none (``"No identifier found."``), or an
error. A code whose first character is a letter is a CINS, a foreign issuer's (Chubb's H1467J104, Linde's G54950103),
which OpenFIGI finds only as an ``ID_CINS``; the others are asked as an ``ID_CUSIP``. Without a key it serves 25
requests a minute, so requests go through the app's ``RateLimiter`` 2.5 seconds apart. Each answer but an error is
cached on disk (``<cache_dir>/openfigi/<cusip>.json``), "no match" too, and a cached one is read before any request, so
a run that stops is resumed by the next. On HTTP 429 the client waits as told (Retry-After's seconds, else a minute),
and after a connection that failed (refused, dropped, timed out) a minute, then retries once; that retry failing, or
any other failure, raises OSError for the caller to report. OpenFIGI's "Invalid idValue format." for a job is a
definitive answer: the code is malformed (its check digit wrong), so it is cached and read as "no match", like "No
identifier found". Any other error for a job is that code's failure alone: once the other jobs are answered,
``JobErrors`` (an OSError) carries their listings and the codes refused. A failure is never taken for "no match".
The requests carry no key and the ``requests`` library's own User-Agent.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

from ..fetchers.rate_limiter import RateLimiter

MAPPING_URL = "https://api.openfigi.com/v3/mapping"
#: The jobs a request may carry without an API key.
BATCH_SIZE = 10
#: Seconds between requests: 24 a minute, under the 25 OpenFIGI serves without a key.
MIN_INTERVAL = 2.5
#: Seconds to wait before the one retry: after a connection that failed, or a 429 whose Retry-After doesn't say.
RETRY_AFTER = 60.0
TIMEOUT = 30.0
#: A CUSIP (or CINS) as the 13F parser keeps it (upper-cased); it names its cache file.
CUSIP = re.compile(r"[0-9A-Z]{9}")
#: A connection that failed: refused, timed out, or dropped while the answer came.
_DROPPED = (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)
#: OpenFIGI's error for a malformed code (a check digit that is wrong: 78462F953, B57FG04CA in the gurus' filings): a
#: definitive answer, as asking again will never map it, so it is kept and read as "no match".
MALFORMED = "Invalid idValue format."


class JobErrors(OSError):
    """OpenFIGI answered some codes' jobs with an error (other than MALFORMED, which is a definitive no match):
    *failed* holds each one's error, and *found* the other codes' listings, as ``lookup`` gives them."""

    def __init__(self, found: Dict[str, Optional["Listing"]], failed: Dict[str, str]) -> None:
        super().__init__("OpenFIGI: " + "; ".join(f"{code}: {error}" for code, error in failed.items()))
        self.found = found
        self.failed = failed


def id_type(code: str) -> str:
    """OpenFIGI's type for a 13F code: a CINS (a foreign issuer's code, its first character a letter) or a CUSIP."""
    return "ID_CINS" if code[:1].isalpha() else "ID_CUSIP"


@dataclass(frozen=True)
class Listing:
    """The listing chosen for a CUSIP: its ticker ("/" made "-": OpenFIGI writes "BRK/B"), name, security type,
    exchange code and FIGI."""

    ticker: Optional[str]
    name: Optional[str]
    security_type: Optional[str]
    exch_code: Optional[str]
    figi: Optional[str]


def pick_listing(data: Sequence[Dict[str, Any]]) -> Optional[Listing]:
    """One of a CUSIP's listings: the US composite (exchange code "US") if there is one, else the first with a
    ticker, else the first. None without any."""
    if not data:
        return None
    chosen = (next((d for d in data if d.get("exchCode") == "US"), None)
              or next((d for d in data if d.get("ticker")), None) or data[0])
    ticker = chosen.get("ticker")
    return Listing(ticker=ticker.replace("/", "-") if ticker else None, name=chosen.get("name"),
                   security_type=chosen.get("securityType"), exch_code=chosen.get("exchCode"),
                   figi=chosen.get("figi"))


def _answer(result: Any) -> Optional[Dict[str, Any]]:
    """A job's result as an answer to keep: its listings, a warning that there are none, or OpenFIGI's error for a
    malformed code (MALFORMED: no request will ever map it). None for another error, or anything else."""
    if not isinstance(result, dict):
        return None
    if "error" in result:
        return result if str(result["error"]).strip() == MALFORMED else None
    if isinstance(result.get("data"), list) or "warning" in result:
        return result
    return None


def _retry_after(response: Any) -> float:
    """The seconds a 429 asks to wait: Retry-After's, else a minute (a date isn't read)."""
    try:
        seconds = float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return RETRY_AFTER
    return seconds if math.isfinite(seconds) and seconds >= 0 else RETRY_AFTER


class OpenFIGI:
    """The mapping API through *session* (a ``requests.Session`` of its own by default: no key, the library's
    User-Agent), *limiter* (2.5 seconds apart) and the cache under *cache_dir*/openfigi/."""

    def __init__(self, cache_dir: Path, session: Any = None, limiter: Optional[RateLimiter] = None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = TIMEOUT) -> None:
        self.cache = Path(cache_dir) / "openfigi"
        self.session = session if session is not None else requests.Session()
        self.limiter = limiter if limiter is not None else RateLimiter(min_interval=MIN_INTERVAL, name="openfigi")
        self.sleep = sleep
        self.timeout = timeout

    def lookup(self, cusips: Sequence[str]) -> Dict[str, Optional[Listing]]:
        """Each CUSIP's listing (``pick_listing``), None where OpenFIGI knows none: from the cache, the others asked
        in requests of up to 10, a CINS as one (``id_type``). Raises OSError when a request fails (the answers before
        it are cached), JobErrors (an OSError) after the last request when OpenFIGI answered jobs with an error, and
        ValueError for a code that isn't 9 letters or digits."""
        wanted = list(dict.fromkeys(cusips))
        for cusip in wanted:
            if not CUSIP.fullmatch(cusip):
                raise ValueError(f"not a CUSIP: {cusip!r}")
        answers: Dict[str, Dict[str, Any]] = {}
        for cusip in wanted:
            cached = self._cached(cusip)
            if cached is not None:
                answers[cusip] = cached
        ask = [cusip for cusip in wanted if cusip not in answers]
        failed: Dict[str, str] = {}
        for start in range(0, len(ask), BATCH_SIZE):
            batch = ask[start:start + BATCH_SIZE]
            for cusip, result in zip(batch, self._request(batch)):
                answer = _answer(result)
                if answer is None:
                    error = result.get("error") if isinstance(result, dict) else None
                    failed[cusip] = str(error or "not an answer")
                    continue
                self._keep(cusip, answer)
                answers[cusip] = answer
        found = {cusip: pick_listing(answers[cusip].get("data") or []) for cusip in wanted if cusip in answers}
        if failed:
            raise JobErrors(found, failed)
        return found

    def _path(self, cusip: str) -> Path:
        return self.cache / f"{cusip}.json"

    def _cached(self, cusip: str) -> Optional[Dict[str, Any]]:
        """The cached answer, or None when there's none that reads."""
        path = self._path(cusip)
        if not path.exists():
            return None
        try:
            return _answer(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def _keep(self, cusip: str, answer: Dict[str, Any]) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        self._path(cusip).write_text(json.dumps(answer), encoding="utf-8")

    def _post(self, jobs: List[Dict[str, str]]) -> Any:
        self.limiter.wait()
        return self.session.post(MAPPING_URL, json=jobs, timeout=self.timeout)

    def _send(self, jobs: List[Dict[str, str]]) -> Any:
        """The response to *jobs*, asked once more after a 429 (waiting Retry-After's seconds, else a minute) or a
        connection that failed (a minute). Raises OSError when the connection fails again, or for another failure of
        the request."""
        try:
            response = self._post(jobs)
        except _DROPPED:
            self.sleep(RETRY_AFTER)
        except requests.RequestException as exc:
            raise OSError(f"OpenFIGI: {exc}") from exc
        else:
            if response.status_code != 429:
                return response
            self.sleep(_retry_after(response))
        try:
            return self._post(jobs)
        except requests.RequestException as exc:
            raise OSError(f"OpenFIGI: {exc}") from exc

    def _request(self, batch: List[str]) -> List[Any]:
        """OpenFIGI's results for *batch*, one per code in its order (``_send``)."""
        response = self._send([{"idType": id_type(cusip), "idValue": cusip} for cusip in batch])
        if response.status_code != 200:
            raise OSError(f"OpenFIGI: HTTP {response.status_code}")
        try:
            results = response.json()
        except ValueError as exc:
            raise OSError(f"OpenFIGI: the answer isn't JSON ({exc})") from exc
        if not isinstance(results, list) or len(results) != len(batch):
            raise OSError(f"OpenFIGI: {len(batch)} jobs and an answer that isn't {len(batch)} results")
        return results
