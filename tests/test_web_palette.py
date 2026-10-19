"""Behavior tests for the Ctrl+K command palette (``src/webapp/static/palette.js``).

palette.js is plain browser JavaScript, so these tests run the real file under
Node through ``tests/js/palette_driver.js`` (a minimal DOM stand-in with a fake
company-search API): type a query, press Enter, and check where the palette
goes. Skipped when Node isn't installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from src.webapp.routes.workstation import TABS

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run palette.js")

PALETTE_JS = Path(__file__).resolve().parents[1] / "src" / "webapp" / "static" / "palette.js"
DRIVER = Path(__file__).resolve().parent / "js" / "palette_driver.js"

Hits = List[Dict[str, str]]


def navigate(
    *queries: str, search_hits: Optional[Dict[str, Hits]] = None, allow_collect: bool = False
) -> List[Optional[str]]:
    """Type each query into the palette and press Enter; return each outcome.

    An outcome is the URL the palette navigates to, ``"#help"`` when Enter opened
    the HELP overlay, or ``None`` when Enter did nothing. ``search_hits`` stands in
    for the company-search API (query -> hits); ``allow_collect`` is the page's
    ``data-allow-collect`` flag (collection is on by default in the real app).
    """
    spec = {"queries": list(queries), "searchHits": search_hits or {}, "allowCollect": allow_collect}
    result = subprocess.run(
        [NODE, str(DRIVER), str(PALETTE_JS)],
        input=json.dumps(spec), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("code, key", [(code, key) for _, code, key in TABS], ids=[code for _, code, _ in TABS])
def test_ticker_code_opens_every_workstation_tab(code: str, key: str) -> None:
    # Every tab the workstation renders must be reachable as `TICKER CODE`.
    # Overview (DES) is the default tab, so its URL carries no ?tab=.
    expected = "/stocks/AAPL" if key == "des" else f"/stocks/AAPL?tab={key}"
    assert navigate(f"AAPL {code}") == [expected]


# The global codes (README / HELP sheet) and where each one must take you.
GLOBAL_CODES = [("SCR", "/screener"), ("ASOF", "/asof"), ("QM", "/quality"), ("COL", "/collect"), ("HELP", "#help")]
GLOBAL_IDS = [code for code, _ in GLOBAL_CODES]


@pytest.mark.parametrize("code, destination", GLOBAL_CODES, ids=GLOBAL_IDS)
def test_global_code_beats_a_matching_company(code: str, destination: str) -> None:
    # The company search also matches the word (as "COL" finds Colgate); the
    # exact global code must still win.
    hits = {code: [{"ticker": "CL", "company_name": "Colgate-Palmolive Company"}]}
    assert navigate(code, search_hits=hits) == [destination]


@pytest.mark.parametrize("code, destination", GLOBAL_CODES, ids=GLOBAL_IDS)
def test_global_code_beats_the_fetch_offer(code: str, destination: str) -> None:
    # No company matches, so the palette offers "Fetch <code> from SEC/Yahoo";
    # the exact global code must still win.
    assert navigate(code, allow_collect=True) == [destination]


def test_global_code_ignores_case() -> None:
    assert navigate("scr", allow_collect=True) == ["/screener"]


@pytest.mark.parametrize(
    "query, hits, destination",
    [
        ("SC", [{"ticker": "SCHW", "company_name": "Charles Schwab Corporation"}], "/stocks/SCHW"),
        ("ZZZZ", [], "/stocks/ZZZZ"),
    ],
    ids=["code-prefix-opens-ticker", "unknown-ticker-offers-fetch"],
)
def test_other_words_still_go_to_tickers_first(query: str, hits: Hits, destination: str) -> None:
    # Only an exact global code jumps the queue: a prefix of one ("SC") still
    # opens the matching company, and an unknown ticker still offers the fetch.
    assert navigate(query, search_hits={query: hits}, allow_collect=True) == [destination]
