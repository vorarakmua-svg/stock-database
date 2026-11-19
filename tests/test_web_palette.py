"""Behavior tests for the Ctrl+K command palette (``src/webapp/static/palette.js``).

palette.js is plain browser JavaScript, so these tests run the real file under
Node through ``tests/js/palette_driver.js`` (a minimal DOM stand-in with a fake
company-search API): play keystrokes, and check where the palette goes.
Skipped when Node isn't installed.
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
Steps = List[List[str]]


def outcomes(
    *scenarios: Steps, search_hits: Optional[Dict[str, Hits]] = None, allow_collect: bool = False,
    manual_search: bool = False
) -> List[Optional[str]]:
    """Play each scenario on a fresh page; return where each one ends up.

    Steps are ``["open"]``, ``["type", text]``, ``["settle"]`` (let the input
    debounce and the company search finish), ``["enter"]`` and ``["escape"]``;
    pending work settles once more at the end. An outcome is the URL the palette
    navigated to, ``"#help"`` when the HELP overlay opened, or ``None``.
    ``search_hits`` stands in for the company-search API (query -> hits);
    ``allow_collect`` is the page's ``data-allow-collect`` flag (collection is on
    by default in the real app). ``manual_search`` makes the company search answer only when told, with the steps
    ``["fire"]`` (run the pending input debounce, no answer), ``["resolve", text]`` (answer the oldest open request for
    that text) and ``["resolveAll"]`` (answer every open request, newest first), to order the debounce, the requests
    and their answers.
    """
    spec = {"scenarios": list(scenarios), "searchHits": search_hits or {}, "allowCollect": allow_collect,
            "manualSearch": manual_search}
    result = subprocess.run(
        [NODE, str(DRIVER), str(PALETTE_JS)],
        input=json.dumps(spec), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def navigate(
    *queries: str, search_hits: Optional[Dict[str, Hits]] = None, allow_collect: bool = False
) -> List[Optional[str]]:
    """Type each query, let its results arrive, press Enter; return where each one goes."""
    scenarios = [[["open"], ["type", query], ["settle"], ["enter"]] for query in queries]
    return outcomes(*scenarios, search_hits=search_hits, allow_collect=allow_collect)


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


@pytest.mark.parametrize(
    "query, hits, destination",
    [
        ("QM", [], "/quality"),
        ("AAPL GP", [], "/stocks/AAPL?tab=gp"),
        ("AAPL", [{"ticker": "AAPL", "company_name": "Apple Inc."}], "/stocks/AAPL"),
    ],
    ids=["global-code", "ticker-code", "ticker"],
)
def test_enter_right_after_typing_acts_on_what_was_typed(query: str, hits: Hits, destination: str) -> None:
    # Enter lands before the input debounce fires and before the company search
    # answers; the palette must act on the typed text, not the list it was showing.
    typed_fast = [["open"], ["type", query], ["enter"]]
    assert outcomes(typed_fast, search_hits={query: hits}, allow_collect=True) == [destination]


def test_closing_the_palette_cancels_a_waiting_enter() -> None:
    # Escape closes the palette while that Enter still waits for search results;
    # reopening it and typing the same text must not navigate on its own.
    steps = [["open"], ["type", "AAPL"], ["enter"], ["escape"], ["open"], ["type", "AAPL"]]
    hits = {"AAPL": [{"ticker": "AAPL", "company_name": "Apple Inc."}]}
    assert outcomes(steps, search_hits=hits) == [None]


def test_early_enter_only_acts_on_the_text_it_was_pressed_for() -> None:
    # Enter waits on "AAPL", then the user keeps typing: that Enter must not
    # fire on "AAPL GP", which was never confirmed.
    steps = [["open"], ["type", "AAPL"], ["enter"], ["type", "AAPL GP"]]
    assert outcomes(steps) == [None]


THREE = [{"ticker": "AAPL", "company_name": "Apple Inc."}, {"ticker": "AAPB", "company_name": "Second"},
         {"ticker": "AAPC", "company_name": "Third"}]


def test_arrow_keys_on_settled_results_move_the_selection() -> None:
    steps = [["open"], ["type", "AAP"], ["settle"], ["down"], ["down"], ["up"], ["enter"]]
    assert outcomes(steps, search_hits={"AAP": THREE}) == ["/stocks/AAPB"]


def test_an_arrow_pressed_before_the_results_land_applies_to_them_not_to_the_old_list() -> None:
    # BUG-003: ArrowDown beat the debounce and the search, so it moved within the list of the text before
    # (here the empty box's pages); the new results then landed with the first row selected and lost it.
    steps = [["open"], ["type", "AAP"], ["down"], ["enter"]]
    assert outcomes(steps, search_hits={"AAP": THREE}) == ["/stocks/AAPB"]


def test_several_early_arrows_all_count_and_never_past_the_ends() -> None:
    down2 = [["open"], ["type", "AAP"], ["down"], ["down"], ["enter"]]
    down9 = [["open"], ["type", "AAP"]] + [["down"]] * 9 + [["enter"]]
    up_first = [["open"], ["type", "AAP"], ["up"], ["enter"]]
    assert outcomes(down2, down9, up_first, search_hits={"AAP": THREE}) == [
        "/stocks/AAPC", "/stocks/AAPC", "/stocks/AAPL"]


def test_an_early_arrow_belongs_to_the_text_it_was_pressed_on() -> None:
    # The user pressed Down on "AAP", then kept typing: the arrow isn't carried over to "AAPL"'s results.
    hits = {"AAP": THREE, "AAPL": THREE[:2]}
    steps = [["open"], ["type", "AAP"], ["down"], ["type", "AAPL"], ["enter"]]
    assert outcomes(steps, search_hits=hits) == ["/stocks/AAPL"]


def test_an_early_enter_opens_the_row_the_arrows_chose_before_it() -> None:
    # Down, then Enter, both before the results land: Enter opens the second row; a Down after that Enter
    # (it has already chosen) doesn't change it.
    chosen = [["open"], ["type", "AAP"], ["down"], ["enter"]]
    late = [["open"], ["type", "AAP"], ["down"], ["enter"], ["down"]]
    assert outcomes(chosen, late, search_hits={"AAP": THREE}) == ["/stocks/AAPB", "/stocks/AAPB"]


def test_an_early_arrow_doesnt_outlive_the_palette_it_was_pressed_in() -> None:
    # Down on "AAP"'s stale list, then Escape: the palette opened again starts on its first row.
    steps = [["open"], ["type", "AAP"], ["down"], ["escape"], ["open"], ["enter"]]
    assert outcomes(steps, search_hits={"AAP": THREE}) == ["/"]


def test_an_early_up_then_down_nets_out_from_the_first_row() -> None:
    # Up at the top row stays on it, so the Down after it reaches the second.
    steps = [["open"], ["type", "AAP"], ["up"], ["down"], ["enter"]]
    assert outcomes(steps, search_hits={"AAP": THREE}) == ["/stocks/AAPB"]


# BUG-360 / BUG-361 (code review 33): the early Enter and the early arrows, ordered against the answers.

TWO_ROWS = {"ZZ": [{"ticker": "ZZA"}, {"ticker": "ZZB"}], "AAP": [{"ticker": "AAP"}, {"ticker": "AAPL"}],
            "AAPL": [{"ticker": "AAPL"}, {"ticker": "AAPLX"}]}


def test_an_enter_whose_results_never_landed_doesnt_swallow_later_arrows() -> None:
    # BUG-360: Enter on "AAP", then "L" typed before "AAP"'s answer: that answer is dropped as stale and the Enter
    # (for the text before) must not stay armed, or the Down before "AAPL"'s results land is lost.
    steps = [["open"], ["type", "AAP"], ["enter"], ["type", "AAPL"], ["down"], ["fire"], ["resolveAll"], ["enter"]]
    assert outcomes(steps, search_hits=TWO_ROWS, manual_search=True) == ["/stocks/AAPLX"]


def test_an_enter_whose_results_never_landed_doesnt_fire_when_the_text_comes_back() -> None:
    # BUG-360: editing the text back to "AAP" later must not navigate by itself: no Enter was pressed for it.
    steps = [["open"], ["type", "AAP"], ["enter"], ["type", "AAPL"], ["fire"], ["resolveAll"], ["type", "AAP"],
             ["settle"]]
    assert outcomes(steps, search_hits=TWO_ROWS, manual_search=True) == [None]


def test_an_enter_still_waits_for_its_own_text_while_the_answer_is_slow() -> None:
    # The cancel is for typing: with the text unchanged the waiting Enter still runs the row an early arrow chose.
    steps = [["open"], ["type", "AAP"], ["down"], ["enter"], ["fire"], ["resolveAll"]]
    assert outcomes(steps, search_hits=TWO_ROWS, manual_search=True) == ["/stocks/AAPL"]


EARLY_KEYS = [
    ["down", "down", "down", "up"],            # past the end of two rows, then back
    ["up", "up", "down"],                      # above the top, then down
    ["down", "up", "up", "down", "down"],
    ["down"] * 5 + ["up"] * 3,
    ["up", "down", "up"],
]


@pytest.mark.parametrize("keys", EARLY_KEYS, ids=lambda keys: "".join(key[0] for key in keys))
def test_early_arrows_land_where_the_same_keys_would_on_the_landed_list(keys: List[str]) -> None:
    # BUG-361: the presses are replayed in order against the landed list, each stopping at an end, so the row is the
    # one the same keys give once the results are in (they were summed with a floor but no ceiling).
    presses = [[key] for key in keys]
    landed = [["open"], ["type", "ZZ"], ["settle"]] + presses + [["enter"]]
    early = [["open"], ["type", "ZZ"]] + presses + [["settle"], ["enter"]]
    got_landed, got_early = outcomes(landed, early, search_hits=TWO_ROWS)
    assert got_early == got_landed


def test_the_overshoot_case_picks_the_first_row_both_ways() -> None:
    # Down x3, Up on two rows: each Down stops at row 2, the Up goes back to row 1.
    keys = [["down"], ["down"], ["down"], ["up"]]
    landed = [["open"], ["type", "ZZ"], ["settle"]] + keys + [["enter"]]
    early = [["open"], ["type", "ZZ"]] + keys + [["settle"], ["enter"]]
    assert outcomes(landed, early, search_hits=TWO_ROWS) == ["/stocks/ZZA", "/stocks/ZZA"]


def test_a_second_build_for_the_same_text_keeps_a_selection_already_moved() -> None:
    # BUG-361: AAPL's answer is in and the user moves down; typing AAPLX and back to AAPL builds AAPL again, and its
    # answer must not put the selection back on the first row.
    steps = [["open"], ["type", "AAPL"], ["fire"], ["resolveAll"], ["down"], ["type", "AAPLX"], ["type", "AAPL"],
             ["fire"], ["resolveAll"], ["enter"]]
    assert outcomes(steps, search_hits=TWO_ROWS, manual_search=True) == ["/stocks/AAPLX"]


def test_a_reopened_palette_starts_on_its_first_row_though_it_was_moved_before() -> None:
    # The kept selection is for one palette session: after Escape and open() the empty box starts on its first row.
    steps = [["open"], ["down"], ["down"], ["escape"], ["open"], ["enter"]]
    assert outcomes(steps) == ["/"]
