"""BUG-275: a year whose 10-K's D&A and 10-Qs' sit on two bases puts the whole difference in its fourth quarter (the
year less nine months). Where the 10-K holds an amortization the 10-Qs' D&A leaves out, the year's quarters go onto
the 10-K's basis, the amortization spread over them by days, and a year with no 10-K yet after one carries the
difference a day; where the 10-Qs' D&A holds more than the 10-K's year, the year less nine months stands."""
from datetime import date

import pytest

from src.parsers.known_facts import facts_known_by
from src.parsers.ttm import compute_ttm
from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd

DEPRECIATION, INTANGIBLES, AMORTIZATION = "Depreciation", "AmortizationOfIntangibleAssets", "AdjustmentForAmortization"
DDA, DA = "DepreciationDepletionAndAmortization", "DepreciationAndAmortization"
SPREAD = "derived:amortization_spread"


def _facts(*filings):
    """A filer's facts from filings, each (start, end, form, filed, {tag: value}), with a balance sheet on each end
    and the same revenue for a period in every filing."""
    us_gaap = {"Assets": {"units": {"USD": []}}}
    for start, end, form, filed, tagged in filings:
        filing = dict(fy=int(filed[:4]), fp="FY" if form == "10-K" else "Q", form=form, filed=filed)
        us_gaap["Assets"]["units"]["USD"].append(usd(1e12, None, end, **filing))
        for tag, val in {"Revenues": 1e11, **tagged}.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, start, end, **filing))
    return {"facts": {"us-gaap": us_gaap}}


def _quarters(facts):
    """The quarters' D&A and its tag by end, and the trailing years' D&A by end."""
    quarters = XBRLParser().extract_quarterly_financials(facts)
    ttm = compute_ttm(quarters)
    return ({end: (q.get("depreciation_amortization"), q.get("_source_tags", {}).get("depreciation_amortization"))
             for end, q in sorted(quarters.items())},
            {end: t.get("depreciation_amortization") for end, t in sorted(ttm.items())})


def _days(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _spread(start, ends, lines, year):
    """Each quarter of a year whose rungs to date are *lines* (each the 10-Qs' line to its end) and whose 10-K's D&A
    *year* exceeds its line, the last of *lines*' ladder ending at ends[-1], by the amortization: each rung takes its
    share by days, the fourth quarter the rest."""
    gap, total = year - lines[-1], _days(start, ends[-1])
    rungs = [line + gap * _days(start, end) / total for end, line in zip(ends, lines[:-1])] + [year]
    return [b - a for a, b in zip([0.0] + rungs, rungs)]


#: AVGO's FY2024 (CIK 1730168): its 10-Qs tag Depreciation alone; its 10-K of 2025-12-18 the year's $593M and
#: $9.267B of amortization of intangibles, read together as the year's $9.860B (BUG-170).
_AVGO_START = "2023-10-30"
_AVGO_ENDS = ("2024-02-04", "2024-05-05", "2024-08-04", "2024-11-03")
_AVGO = [(_AVGO_START, "2024-02-04", "10-Q", "2024-03-14", {DEPRECIATION: 139e6}),
         (_AVGO_START, "2024-05-05", "10-Q", "2024-06-13", {DEPRECIATION: 288e6}),
         (_AVGO_START, "2024-08-04", "10-Q", "2024-09-11", {DEPRECIATION: 437e6}),
         (_AVGO_START, "2024-11-03", "10-K", "2025-12-18", {DEPRECIATION: 593e6, INTANGIBLES: 9267e6})]


def test_a_10k_amortization_the_10qs_never_tag_is_spread_over_the_years_quarters():
    # The fourth quarter was the year less nine months of depreciation: $9.423B beside quarters of $0.14B, and the
    # trailing years over it counted FY2024's amortization in FY2025 again ($11.9-15.9B for $8.6-9.9B).
    quarters, ttm = _quarters(_facts(*_AVGO))
    expected = _spread(_AVGO_START, _AVGO_ENDS, [139e6, 288e6, 437e6, 593e6], 9860e6)
    assert [quarters[end][0] for end in _AVGO_ENDS] == pytest.approx(expected)
    assert [round(q / 1e6) for q in expected] == [2568, 2428, 2428, 2435]
    assert {quarters[end][1] for end in _AVGO_ENDS} == {SPREAD}
    assert ttm["2024-11-03"] == pytest.approx(9860e6)  # the trailing year at the year end is the year


def test_before_its_10k_a_years_quarters_stay_as_tagged():
    # The spread reads the 10-K: known before it, the year's quarters are the 10-Qs' figures.
    quarters, _ = _quarters(facts_known_by(_facts(*_AVGO), "2025-12-17"))
    assert [quarters[end] for end in _AVGO_ENDS[:3]] == [(139e6, "derived:ytd_diff"), (149e6, "derived:ytd_diff"),
                                                         (149e6, "derived:ytd_diff")]
    assert quarters.get("2024-11-03", (None, None))[0] is None


def test_a_year_no_10k_gives_moves_nothing():
    # The spread reads a 10-K's year: a twelve-month figure in a 10-Q is no 10-K's.
    avgo = _AVGO[:3] + [(_AVGO_START, "2024-11-03", "10-Q", "2025-12-18", _AVGO[3][4])]
    quarters, _ = _quarters(_facts(*avgo))
    assert quarters["2024-08-04"] == (149e6, "derived:ytd_diff")


def test_only_d_and_a_reads_its_bases():
    # Another flow whose figures happen to match SYK's D&A keeps the year less nine months.
    start = "2023-01-01"
    facts = _facts(*[(start, end, "10-Q", filed, {DEPRECIATION: d, INTANGIBLES: a, "NetIncomeLoss": d + a})
                     for end, filed, d, a in (("2023-03-31", "2023-04-28", 97e6, 160e6),
                                              ("2023-06-30", "2023-07-28", 195e6, 322e6),
                                              ("2023-09-30", "2023-10-27", 292e6, 486e6))],
                   (start, "2023-12-31", "10-K", "2024-02-07", {DDA: 1341e6, DEPRECIATION: 393e6, AMORTIZATION: 635e6,
                                                                INTANGIBLES: 635e6, "NetIncomeLoss": 1341e6}))
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert quarters["2023-12-31"]["net_income"] == pytest.approx(563e6)


def test_a_10k_total_beside_its_depreciation_is_spread_over_10qs_of_depreciation_alone():
    # The 10-K tags its total ($1,000M) and its depreciation ($700M), its 10-Qs the depreciation alone.
    start, ends = "2025-01-01", ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    facts = _facts((start, ends[0], "10-Q", "2025-04-30", {DEPRECIATION: 170e6}),
                   (start, ends[1], "10-Q", "2025-07-30", {DEPRECIATION: 345e6}),
                   (start, ends[2], "10-Q", "2025-10-30", {DEPRECIATION: 520e6}),
                   (start, ends[3], "10-K", "2026-02-20", {DDA: 1000e6, DEPRECIATION: 700e6}))
    quarters, ttm = _quarters(facts)
    assert [quarters[end][0] for end in ends] == pytest.approx(
        _spread(start, ends, [170e6, 345e6, 520e6, 700e6], 1000e6))
    assert ttm[ends[3]] == pytest.approx(1000e6)


#: SWK's FY2023 (CIK 93556): its 10-K's DepreciationDepletionAndAmortization is its depreciation line ($432.4M),
#: with the $192.7M of amortization another total ($625.1M, BUG-274); its 10-Qs tag the line and the amortization apart.
_SWK_START = "2023-01-01"
_SWK_ENDS = ("2023-04-01", "2023-07-01", "2023-09-30", "2023-12-30")
_SWK = [(_SWK_START, "2023-04-01", "10-Q", "2023-05-04", {DDA: 112.9e6, INTANGIBLES: 48.3e6}),
        (_SWK_START, "2023-07-01", "10-Q", "2023-08-01", {DDA: 229.0e6, INTANGIBLES: 96.6e6}),
        (_SWK_START, "2023-09-30", "10-Q", "2023-10-30", {DDA: 332.0e6, INTANGIBLES: 144.7e6}),
        (_SWK_START, "2023-12-30", "10-K", "2024-02-27",
         {DDA: 432.4e6, DA: 625.1e6, DEPRECIATION: 383.3e6, AMORTIZATION: 49.1e6, INTANGIBLES: 192.7e6})]


def test_a_10k_read_as_its_line_and_amortization_is_spread_over_10qs_of_the_line():
    # The fourth quarter was $293.1M beside quarters of $89-116M.
    quarters, ttm = _quarters(_facts(*_SWK))
    assert [quarters[end][0] for end in _SWK_ENDS] == pytest.approx(
        _spread(_SWK_START, _SWK_ENDS, [112.9e6, 229.0e6, 332.0e6, 432.4e6], 625.1e6))
    assert ttm["2023-12-30"] == pytest.approx(625.1e6)


def test_a_10q_rung_combined_with_its_amortization_goes_onto_its_line():
    # HD's FY2025: its first quarter's 10-Q is read with its amortization, the six and nine months as the line: each
    # goes onto its own filing's line, then takes its share.
    start, ends = "2025-01-01", ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    facts = _facts((start, ends[0], "10-Q", "2025-04-30", {DEPRECIATION: 80e6, INTANGIBLES: 20e6}),
                   (start, ends[1], "10-Q", "2025-07-30", {DEPRECIATION: 165e6}),
                   (start, ends[2], "10-Q", "2025-10-30", {DEPRECIATION: 250e6}),
                   (start, ends[3], "10-K", "2026-02-20", {DEPRECIATION: 340e6, INTANGIBLES: 85e6}))
    quarters, _ = _quarters(facts)
    assert [quarters[end][0] for end in ends] == pytest.approx(
        _spread(start, ends, [80e6, 165e6, 250e6, 340e6], 425e6))


def test_a_10q_figure_that_is_its_depreciation_is_on_the_depreciations_basis():
    # F's FY2025: its 10-Qs tag one figure as their total and their Depreciation ($5.722B for nine months), its 10-K a
    # $15.974B total beside $7.834B of depreciation: the fourth quarter read $10.252B beside some $1.9B.
    start, ends = "2025-01-01", ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    facts = _facts(*[(start, end, "10-Q", f"2025-{int(end[5:7]) + 1:02d}-28", {DDA: val, DEPRECIATION: val})
                     for end, val in zip(ends, (1848e6, 3747e6, 5722e6))],
                   (start, ends[3], "10-K", "2026-02-11", {DDA: 15974e6, DEPRECIATION: 7834e6}))
    quarters, ttm = _quarters(facts)
    assert [quarters[end][0] for end in ends] == pytest.approx(
        _spread(start, ends, [1848e6, 3747e6, 5722e6, 7834e6], 15974e6))
    assert ttm[ends[3]] == pytest.approx(15974e6)


def test_a_10k_larger_total_beside_the_10qs_total_is_spread_over_them():
    # The 10-Qs tag DepreciationAndAmortization, the 10-K that figure for the year ($400M) beside a larger total
    # read first ($520M): the quarters go onto the 10-K's.
    start, ends = "2025-01-01", ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    facts = _facts((start, ends[0], "10-Q", "2025-04-30", {DA: 100e6}),
                   (start, ends[1], "10-Q", "2025-07-30", {DA: 200e6}),
                   (start, ends[2], "10-Q", "2025-10-30", {DA: 300e6}),
                   (start, ends[3], "10-K", "2026-02-20", {DDA: 520e6, DA: 400e6}))
    quarters, _ = _quarters(facts)
    assert [quarters[end][0] for end in ends] == pytest.approx(
        _spread(start, ends, [100e6, 200e6, 300e6, 400e6], 520e6))


def test_a_10k_that_tags_nothing_under_the_10qs_tag_moves_nothing():
    # Its year under another total only: nothing shows the 10-Qs' figure a part of it.
    start, ends = "2025-01-01", ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    facts = _facts((start, ends[0], "10-Q", "2025-04-30", {DA: 100e6, INTANGIBLES: 10e6}),
                   (start, ends[1], "10-Q", "2025-07-30", {DA: 200e6, INTANGIBLES: 20e6}),
                   (start, ends[2], "10-Q", "2025-10-30", {DA: 300e6, INTANGIBLES: 30e6}),
                   (start, ends[3], "10-K", "2026-02-20", {DDA: 520e6, INTANGIBLES: 40e6}))
    quarters, _ = _quarters(facts)
    assert quarters[ends[3]] == (pytest.approx(220e6), "derived:ytd_diff")


def test_10qs_of_their_depreciation_and_amortization_go_onto_a_larger_10k_total():
    # SYK's FY2023: its 10-Qs tag the depreciation and the amortization (read together, $778M for nine months), its
    # 10-K a $1.341B total beside its $393M and $635M: the fourth quarter read $563M beside some $260M.
    start, ends = "2023-01-01", ("2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31")
    facts = _facts((start, ends[0], "10-Q", "2023-04-28", {DEPRECIATION: 97e6, INTANGIBLES: 160e6}),
                   (start, ends[1], "10-Q", "2023-07-28", {DEPRECIATION: 195e6, INTANGIBLES: 322e6}),
                   (start, ends[2], "10-Q", "2023-10-27", {DEPRECIATION: 292e6, INTANGIBLES: 486e6}),
                   (start, ends[3], "10-K", "2024-02-07",
                    {DDA: 1341e6, DEPRECIATION: 393e6, AMORTIZATION: 635e6, INTANGIBLES: 635e6}))
    quarters, _ = _quarters(facts)
    assert [quarters[end][0] for end in ends] == pytest.approx(
        _spread(start, ends, [257e6, 517e6, 778e6, 1028e6], 1341e6))


@pytest.mark.parametrize("to_date, year_end", [
    # nine months of more than the 10-K's year of the line aren't that line's
    ((240e6, 480e6, 710e6), {DDA: 2000e6, DEPRECIATION: 700e6}),
    # a 10-K total below its depreciation holds no amortization to spread
    ((150e6, 300e6, 450e6), {DDA: 600e6, DEPRECIATION: 700e6}),
])
def test_a_10k_line_that_cant_be_the_10qs_moves_nothing(to_date, year_end):
    start, ends = "2025-01-01", ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    facts = _facts(*[(start, end, "10-Q", f"2025-{int(end[5:7]) + 1:02d}-28", {DEPRECIATION: val})
                     for end, val in zip(ends, to_date)],
                   (start, ends[3], "10-K", "2026-02-20", year_end))
    quarters, _ = _quarters(facts)
    assert quarters[ends[3]] == (pytest.approx(year_end[DDA] - to_date[2]), "derived:ytd_diff")


def test_10qs_read_with_an_amortization_the_10k_doesnt_tag_move_nothing():
    # The 10-Qs' figure is their depreciation with the cash flow statement's amortization, which the 10-K doesn't tag:
    # its depreciation and intangibles' amortization are no figure on the 10-Qs' basis.
    start, ends = "2023-01-01", ("2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31")
    facts = _facts(*[(start, end, "10-Q", f"2023-{int(end[5:7]) + 1:02d}-28",
                      {DEPRECIATION: d, AMORTIZATION: a, INTANGIBLES: i})
                     for end, (d, a, i) in zip(ends, ((97e6, 200e6, 160e6), (195e6, 400e6, 322e6),
                                                      (292e6, 600e6, 486e6)))],
                   (start, ends[3], "10-K", "2024-02-07", {DDA: 1500e6, DEPRECIATION: 393e6, INTANGIBLES: 635e6}))
    quarters, _ = _quarters(facts)
    assert quarters[ends[3]] == (pytest.approx(1500e6 - 892e6), "derived:ytd_diff")


def test_10qs_read_as_their_line_and_amortization_beside_a_10k_read_so_stay():
    # The 10-Qs' line and amortization make their total (BUG-274), read for them as for the 10-K: one basis, though
    # the fourth quarter is the year's largest.
    start, ends = "2023-01-01", ("2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31")
    facts = _facts(*[(start, end, "10-Q", f"2023-{int(end[5:7]) + 1:02d}-28", {DDA: d, DA: d + a, INTANGIBLES: a})
                     for end, (d, a) in zip(ends, ((80e6, 20e6), (160e6, 40e6), (240e6, 60e6)))],
                   (start, ends[3], "10-K", "2024-02-07", {DDA: 600e6, DA: 700e6, INTANGIBLES: 100e6}))
    quarters, _ = _quarters(facts)
    assert [quarters[end] for end in ends[2:]] == [(pytest.approx(100e6), "derived:ytd_diff"),
                                                   (pytest.approx(400e6), "derived:ytd_diff")]


def test_a_rung_whose_filing_tags_no_line_moves_nothing():
    # The first 10-Q tags a total alone: its quarter has no figure on the line's basis to go onto.
    avgo = [(_AVGO_START, "2024-02-04", "10-Q", "2024-03-14", {DA: 2400e6})] + _AVGO[1:]
    quarters, _ = _quarters(_facts(*avgo))
    assert quarters["2024-11-03"] == (pytest.approx(9860e6 - 437e6), "derived:ytd_diff")


def test_a_spread_that_would_dig_a_hole_in_the_fourth_quarter_moves_nothing():
    # KEY's FY2024: nine months of $68M beside a 10-K's $73M under their tag and $91M read first: on the $73M the
    # fourth quarter would be $5M beside quarters of $23M; the 10-Qs were on the $91M's basis.
    start, ends = "2024-01-01", ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31")
    daan = "DepreciationAmortizationAndAccretionNet"
    facts = _facts(*[(start, end, "10-Q", f"2024-{int(end[5:7]) + 1:02d}-28", {daan: val})
                     for end, val in zip(ends, (22e6, 45e6, 68e6))],
                   (start, ends[3], "10-K", "2025-02-20", {DDA: 91e6, daan: 73e6}))
    quarters, _ = _quarters(facts)
    assert quarters[ends[3]] == (pytest.approx(23e6), "derived:ytd_diff")


@pytest.mark.parametrize("year_end", [
    {DEPRECIATION: 800e6},  # seasonality: a fourth quarter of $500M beside $100M, one tag throughout
    {DDA: 900e6},  # an acquisition: the same total in the 10-Qs and the 10-K
])
def test_a_large_fourth_quarter_on_one_basis_stays(year_end):
    start, ends = "2025-01-01", ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    tag = next(iter(year_end))
    facts = _facts(*[(start, end, "10-Q", f"2025-{int(end[5:7]) + 1:02d}-28", {tag: val})
                     for end, val in zip(ends, (100e6, 200e6, 300e6))],
                   (start, ends[3], "10-K", "2026-02-20", year_end))
    quarters, _ = _quarters(facts)
    assert quarters[ends[3]] == (pytest.approx(year_end[tag] - 300e6), "derived:ytd_diff")
    assert quarters[ends[0]] == (pytest.approx(100e6), "derived:ytd_diff")


#: GOOGL's 2025 (CIK 1652044): its 10-Qs tag the depreciation and the amortization (read together, BUG-170), its
#: 10-K the depreciation alone.
_GOOGL_ENDS = ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
_GOOGL = [("2025-01-01", "2025-03-31", "10-Q", "2025-04-25", {DEPRECIATION: 4487e6, INTANGIBLES: 122e6}),
          ("2025-01-01", "2025-06-30", "10-Q", "2025-07-24", {DEPRECIATION: 9485e6, INTANGIBLES: 246e6}),
          ("2025-01-01", "2025-09-30", "10-Q", "2025-10-30", {DEPRECIATION: 15096e6, INTANGIBLES: 375e6}),
          ("2025-01-01", "2025-12-31", "10-K", "2026-02-05", {DEPRECIATION: 21136e6})]


def test_10qs_holding_more_than_the_10ks_year_keep_the_year_less_nine_months():
    # The reverse kind stays as it was (the user's choice): the fourth quarter the 10-K's depreciation less nine months
    # of the depreciation and amortization, $5.665B, and the trailing year at the year end the annual figure.
    quarters, ttm = _quarters(_facts(*_GOOGL))
    assert [quarters[end][0] for end in _GOOGL_ENDS[:3]] == pytest.approx([4609e6, 5122e6, 5740e6])
    assert quarters["2025-12-31"] == (pytest.approx(21136e6 - 15471e6), "derived:ytd_diff")
    assert ttm["2025-12-31"] == pytest.approx(21136e6)


def test_10qs_on_the_10ks_basis_keep_their_fourth_quarter():
    # The same 10-Qs beside a 10-K that tags the amortization too: one basis, the year less nine months.
    googl = _GOOGL[:3] + [("2025-01-01", "2025-12-31", "10-K", "2026-02-05",
                           {DEPRECIATION: 21136e6, INTANGIBLES: 504e6})]
    quarters, _ = _quarters(_facts(*googl))
    assert quarters["2025-12-31"] == (pytest.approx(21640e6 - 15471e6), "derived:ytd_diff")


#: AVGO's FY2024 with its 10-K filed before FY2025's 10-Qs (the real one came in December 2025, after them).
_AVGO_EARLY = _AVGO[:3] + [(_AVGO_START, "2024-11-03", "10-K", "2024-12-20", _AVGO[3][4])]
_NEXT = "2024-11-04"
_NEXT_Q1 = (_NEXT, "2025-02-02", "10-Q", "2025-03-12", {DEPRECIATION: 142e6})
_NEXT_Q2 = (_NEXT, "2025-05-04", "10-Q", "2025-06-11", {DEPRECIATION: 284e6})
#: FY2024's difference a day: $9.267B over its 370 days.
_PER_DAY = 9267e6 / 370


def test_a_year_with_no_10k_yet_carries_the_last_spread_years_difference():
    # The open year's 10-Qs leave the amortization out as FY2024's did: on their line alone, the trailing year to
    # 2025-05-04 held half of FY2024's amortization ($5.7B for some $10B). Each rung carries FY2024's difference a
    # day, as a year with no 10-K yet is read as the last 10-K was (BUG-332).
    quarters, ttm = _quarters(_facts(*_AVGO_EARLY, _NEXT_Q1, _NEXT_Q2))
    q1, q2 = 142e6 + _PER_DAY * _days(_NEXT, "2025-02-02"), 284e6 + _PER_DAY * _days(_NEXT, "2025-05-04")
    assert quarters["2025-02-02"] == (pytest.approx(q1), "derived:amortization_carried")
    assert quarters["2025-05-04"] == (pytest.approx(q2 - q1), "derived:amortization_carried")
    six = 288e6 + 9267e6 * _days(_AVGO_START, "2024-05-05") / 370
    assert ttm["2025-05-04"] == pytest.approx(9860e6 - six + q2)  # the year less six months, plus six on one basis


def test_a_first_quarter_alone_carries_the_difference_too():
    # Until its second 10-Q a year's ladder is its first quarter's own figure (BUG-124): it carries it as well.
    quarters, ttm = _quarters(_facts(*_AVGO_EARLY, _NEXT_Q1))
    q1 = 142e6 + _PER_DAY * _days(_NEXT, "2025-02-02")
    assert quarters["2025-02-02"] == (pytest.approx(q1), "derived:amortization_carried")
    first = 139e6 + 9267e6 * _days(_AVGO_START, "2024-02-04") / 370
    assert ttm["2025-02-02"] == pytest.approx(9860e6 - first + q1)


def test_a_quarter_filed_before_the_10k_carries_nothing():
    # FY2025's 10-Qs came before the 10-K that gave FY2024's amortization (AVGO's real filings): a quarter never reads
    # a 10-K filed after it, so they stay as tagged.
    quarters, _ = _quarters(_facts(*_AVGO, _NEXT_Q1, _NEXT_Q2))
    assert quarters["2025-02-02"] == (pytest.approx(142e6), "derived:ytd_diff")
    assert quarters["2025-05-04"] == (pytest.approx(142e6), "derived:ytd_diff")


def test_after_a_year_on_one_basis_nothing_is_carried():
    year = (_AVGO_START, "2024-11-03", "10-K", "2024-12-20", {DEPRECIATION: 593e6})
    quarters, _ = _quarters(_facts(*_AVGO[:3], year, _NEXT_Q1, _NEXT_Q2))
    assert quarters["2025-02-02"] == (pytest.approx(142e6), "derived:ytd_diff")
    assert quarters["2025-05-04"] == (pytest.approx(142e6), "derived:ytd_diff")


def test_an_open_year_whose_10q_tags_no_figure_on_the_line_carries_nothing():
    # Its six months tag a total alone: no figure on the depreciation's basis to carry the difference onto.
    q2 = (_NEXT, "2025-05-04", "10-Q", "2025-06-11", {DA: 4200e6})
    quarters, _ = _quarters(_facts(*_AVGO_EARLY, _NEXT_Q1, q2))
    assert quarters["2025-05-04"] == (pytest.approx(4200e6 - 142e6), "derived:ytd_diff")


def test_10qs_read_with_their_amortization_carry_a_larger_10k_totals_difference():
    # SYK's: FY2023 spread onto its 10-K's total ($1.341B beside its $393M and $635M), FY2024's 10-Qs read as their
    # depreciation and amortization together carry the $313M a year.
    start, ends = "2023-01-01", ("2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31")
    facts = _facts((start, ends[0], "10-Q", "2023-04-28", {DEPRECIATION: 97e6, INTANGIBLES: 160e6}),
                   (start, ends[1], "10-Q", "2023-07-28", {DEPRECIATION: 195e6, INTANGIBLES: 322e6}),
                   (start, ends[2], "10-Q", "2023-10-27", {DEPRECIATION: 292e6, INTANGIBLES: 486e6}),
                   (start, ends[3], "10-K", "2024-02-07",
                    {DDA: 1341e6, DEPRECIATION: 393e6, AMORTIZATION: 635e6, INTANGIBLES: 635e6}),
                   ("2024-01-01", "2024-03-31", "10-Q", "2024-04-26", {DEPRECIATION: 100e6, INTANGIBLES: 165e6}),
                   ("2024-01-01", "2024-06-30", "10-Q", "2024-07-26", {DEPRECIATION: 201e6, INTANGIBLES: 331e6}))
    quarters, _ = _quarters(facts)
    per_day = 313e6 / _days(start, ends[3])
    q1 = 265e6 + per_day * _days("2024-01-01", "2024-03-31")
    q2 = 532e6 + per_day * _days("2024-01-01", "2024-06-30")
    assert [quarters[e] for e in ("2024-03-31", "2024-06-30")] == [
        (pytest.approx(q1), "derived:amortization_carried"), (pytest.approx(q2 - q1), "derived:amortization_carried")]


def test_a_year_with_its_own_10k_follows_it_not_the_last():
    # FY2025's 10-K is filed and on the 10-Qs' basis: nothing is carried into it.
    year = (_NEXT, "2025-11-02", "10-K", "2025-12-18", {DEPRECIATION: 574e6})
    q3 = (_NEXT, "2025-08-03", "10-Q", "2025-09-10", {DEPRECIATION: 426e6})
    quarters, _ = _quarters(_facts(*_AVGO_EARLY, _NEXT_Q1, _NEXT_Q2, q3, year))
    assert [quarters[e] for e in ("2025-02-02", "2025-11-02")] == [(pytest.approx(142e6), "derived:ytd_diff"),
                                                                   (pytest.approx(148e6), "derived:ytd_diff")]


def test_an_open_year_10q_read_as_a_total_of_its_own_carries_nothing():
    # After SYK's spread year, a 10-Q whose figure is a total of its own, not its depreciation and amortization.
    start = "2023-01-01"
    facts = _facts((start, "2023-03-31", "10-Q", "2023-04-28", {DEPRECIATION: 97e6, INTANGIBLES: 160e6}),
                   (start, "2023-06-30", "10-Q", "2023-07-28", {DEPRECIATION: 195e6, INTANGIBLES: 322e6}),
                   (start, "2023-09-30", "10-Q", "2023-10-27", {DEPRECIATION: 292e6, INTANGIBLES: 486e6}),
                   (start, "2023-12-31", "10-K", "2024-02-07",
                    {DDA: 1341e6, DEPRECIATION: 393e6, AMORTIZATION: 635e6, INTANGIBLES: 635e6}),
                   ("2024-01-01", "2024-03-31", "10-Q", "2024-04-26",
                    {DDA: 300e6, DEPRECIATION: 100e6, INTANGIBLES: 165e6}),
                   ("2024-01-01", "2024-06-30", "10-Q", "2024-07-26",
                    {DDA: 610e6, DEPRECIATION: 201e6, INTANGIBLES: 331e6}))
    quarters, _ = _quarters(facts)
    assert quarters["2024-06-30"] == (pytest.approx(310e6), "derived:ytd_diff")
