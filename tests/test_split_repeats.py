"""Splits the filings restate, read from counts and per-share figures, and figures a later filing repeats on their
first filing's basis (BUG-105)."""

import pytest

from src.parsers.xbrl_parser import XBRLParser, _split_restatements

EPS, DILUTED, OUTSTANDING, INCOME = ("EarningsPerShareDiluted", "WeightedAverageNumberOfDilutedSharesOutstanding",
                                     "CommonStockSharesOutstanding", "NetIncomeLoss")


def _entry(val, start, end, form, filed):
    entry = {"val": val, "end": end, "form": form, "filed": filed, "accn": filed, "fy": int(filed[:4]),
             "fp": "FY" if form == "10-K" else "Q"}
    if start:
        entry["start"] = start
    return entry


def _facts(rows):
    """{tag: [(value, start, end, form, filed), ...]} as companyfacts."""
    unit = {EPS: "USD/shares", INCOME: "USD", "CommonStockDividendsPerShareDeclared": "USD/shares",
            "CommonStockDividendsPerShareCashPaid": "USD/shares"}
    return {"facts": {"us-gaap": {tag: {"units": {unit.get(tag, "shares"): [_entry(*row) for row in entries]}}
                                  for tag, entries in rows.items()}}}


# ISRG around its 3:1 split of October 2021: 10-Qs of 2020 (2020-04-17, 07-23, 10-19), the FY2020 10-K (2021-02-10),
# the 10-Q for Q3 2021 (2021-10-20, after the split) and the FY2021 10-K (2022-02-03), which tags the 2020 quarters'
# EPS as first filed (2.62, 0.57, 2.60, 3.02) beside the year restated (2.94).
Y = "2020-01-01"
_ISRG = {
    EPS: [(2.62, Y, "2020-03-31", "10-Q", "2020-04-17"), (2.62, Y, "2020-03-31", "10-K", "2021-02-10"),
          (2.62, Y, "2020-03-31", "10-K", "2022-02-03"),
          (3.19, Y, "2020-06-30", "10-Q", "2020-07-23"),
          (0.57, "2020-04-01", "2020-06-30", "10-Q", "2020-07-23"),
          (0.57, "2020-04-01", "2020-06-30", "10-K", "2022-02-03"),
          (5.80, Y, "2020-09-30", "10-Q", "2020-10-19"), (1.93, Y, "2020-09-30", "10-Q", "2021-10-20"),
          (2.60, "2020-07-01", "2020-09-30", "10-Q", "2020-10-19"),
          (0.87, "2020-07-01", "2020-09-30", "10-Q", "2021-10-20"),
          (2.60, "2020-07-01", "2020-09-30", "10-K", "2022-02-03"),
          (8.82, Y, "2020-12-31", "10-K", "2021-02-10"), (2.94, Y, "2020-12-31", "10-K", "2022-02-03")],
    DILUTED: [(119.8e6, Y, "2020-03-31", "10-Q", "2020-04-17"), (119.7e6, Y, "2020-06-30", "10-Q", "2020-07-23"),
              (120.0e6, Y, "2020-09-30", "10-Q", "2020-10-19"), (360.0e6, Y, "2020-09-30", "10-Q", "2021-10-20"),
              (120.4e6, Y, "2020-12-31", "10-K", "2021-02-10"), (361.2e6, Y, "2020-12-31", "10-K", "2022-02-03")],
    INCOME: [(313.5e6, Y, "2020-03-31", "10-Q", "2020-04-17"), (381.5e6, Y, "2020-06-30", "10-Q", "2020-07-23"),
             (695.4e6, Y, "2020-09-30", "10-Q", "2020-10-19"), (1060.6e6, Y, "2020-12-31", "10-K", "2021-02-10")],
}


def test_a_figure_a_later_filing_repeats_is_on_its_first_filings_basis():
    # BUG-105: the FY2021 10-K's 2.62 for Q1 2020 was read as restated for the split, as filed after it: Q1 2020's EPS
    # read 2.62 beside 359M shares, and Q2 -1.557 on $68M of net income.
    quarterly = XBRLParser().extract_quarterly_financials(_facts(_ISRG))
    eps = [quarterly[end]["eps_diluted"] for end in ("2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31")]
    assert eps == pytest.approx([2.62 / 3, 3.19 / 3 - 2.62 / 3, 1.93 - 3.19 / 3, 2.94 - 1.93])
    assert quarterly["2020-03-31"]["_split_levels"]["eps_diluted"] == 3.0


def test_a_repeat_after_a_restatement_reads_no_reverse_split():
    # The FY2021 10-K's 2.60 for Q3 2020 after the 10-Q's restated 0.87 is a repeat, not a 1-for-3 after the split;
    # nor is its Q3 2020 count as first filed after the 10-Q's restated one.
    rows = dict(_ISRG)
    rows[DILUTED] = rows[DILUTED] + [(120.1e6, "2020-07-01", "2020-09-30", "10-Q", "2020-10-19"),
                                     (360.3e6, "2020-07-01", "2020-09-30", "10-Q", "2021-10-20"),
                                     (120.1e6, "2020-07-01", "2020-09-30", "10-K", "2022-02-03")]
    assert _split_restatements(_facts(rows)["facts"]["us-gaap"]) == [("2021-02-10", "2021-10-20", 3.0)]


def test_a_count_a_later_filing_repeats_is_on_its_first_filings_basis():
    # A quarter-end count a filing after the split gives as first filed is on the old basis too.
    rows = dict(_ISRG)
    rows[OUTSTANDING] = [(116.0e6, None, "2020-06-30", "10-Q", "2020-07-23"),
                         (116.0e6, None, "2020-06-30", "10-Q", "2021-10-20"),
                         (117.0e6, None, "2020-09-30", "10-Q", "2020-10-19"),
                         (351.6e6, None, "2020-12-31", "10-K", "2022-02-03")]
    quarterly = XBRLParser().extract_quarterly_financials(_facts(rows))
    counts = [quarterly[end]["shares_outstanding"] for end in ("2020-06-30", "2020-09-30", "2020-12-31")]
    assert counts == pytest.approx([348.0e6, 351.0e6, 351.6e6])


# GOOGL around its 20:1 split of July 2022: one count pair restated (the shares outstanding at 2021-12-31), and its
# diluted EPS for 2021's half year and nine months and the year.
G = "2021-01-01"
_GOOGL = {
    OUTSTANDING: [(664.7e6, None, "2021-12-31", "10-K", "2022-02-02"),
                  (664.7e6, None, "2021-12-31", "10-Q", "2022-04-27"),
                  (13_242e6, None, "2021-12-31", "10-Q", "2022-07-27"),
                  (658.8e6, None, "2022-03-31", "10-Q", "2022-04-27"),
                  (13_078e6, None, "2022-06-30", "10-Q", "2022-07-27")],
    EPS: [(26.29, G, "2021-03-31", "10-Q", "2021-04-28"),
          (53.55, G, "2021-06-30", "10-Q", "2021-07-28"), (2.68, G, "2021-06-30", "10-Q", "2022-07-27"),
          (81.25, G, "2021-09-30", "10-Q", "2021-10-27"), (4.06, G, "2021-09-30", "10-Q", "2022-10-26"),
          (112.20, G, "2021-12-31", "10-K", "2022-02-02"), (5.61, G, "2021-12-31", "10-K", "2023-02-03"),
          (24.62, "2022-01-01", "2022-03-31", "10-Q", "2022-04-27"),
          (2.44, "2022-01-01", "2022-06-30", "10-Q", "2022-07-27")],
}


def test_restated_eps_beside_one_restated_count_shows_a_split():
    # BUG-105: GOOGL's 20:1 split restated one count pair and seven of its diluted EPS; counts alone needed two, so it
    # was missed, and its quarter-end counts read 664.7M, 13,242M, 658.8M, 13,078M.
    assert _split_restatements(_facts(_GOOGL)["facts"]["us-gaap"]) == [("2022-04-27", "2022-07-27", 20.0)]
    quarterly = XBRLParser().extract_quarterly_financials(_facts(_GOOGL))
    counts = [quarterly[end]["shares_outstanding"] for end in ("2021-12-31", "2022-03-31", "2022-06-30")]
    assert counts == pytest.approx([13_242e6, 13_176e6, 13_078e6])


def test_restated_eps_alone_shows_no_split():
    # A restated EPS of a few cents reads as a simple fraction (0.04 for 0.02 is 2:1): per-share figures count only
    # beside a count.
    rows = {EPS: [(0.04, G, "2021-03-31", "10-Q", "2021-04-28"), (0.02, G, "2021-03-31", "10-Q", "2022-04-27"),
                  (0.06, G, "2021-06-30", "10-Q", "2021-07-28"), (0.03, G, "2021-06-30", "10-Q", "2022-07-27")]}
    assert _split_restatements(_facts(rows)["facts"]["us-gaap"]) == []


def test_a_year_whose_latest_full_year_figure_repeats_its_first_filing_keeps_that_basis():
    # Where the filing after the split repeats the year's EPS as first filed (8.82), the year, as the annual record
    # holds it, is on the old basis: so are its quarters.
    rows = dict(_ISRG)
    rows[EPS] = [row for row in rows[EPS] if row[0] != 2.94] + [(8.82, Y, "2020-12-31", "10-K", "2022-02-03")]
    quarterly = XBRLParser().extract_quarterly_financials(_facts(rows))
    eps = [quarterly[end]["eps_diluted"] for end in ("2020-03-31", "2020-09-30", "2020-12-31")]
    assert eps == pytest.approx([2.62, 1.93 * 3 - 3.19, 8.82 - 1.93 * 3])
    assert quarterly["2020-03-31"]["_split_levels"]["eps_diluted"] == 1.0


# A 2:1 split the counts date between 2011-03-11 and 2011-09-09 (two restated counts), around an October year.
N = "2010-11-01"
P = "2009-11-01"
_COUNTS = [(34.0e6, P, "2010-10-31", "10-K", "2010-12-17"), (68.0e6, P, "2010-10-31", "10-Q", "2011-09-09"),
           (34.5e6, N, "2011-01-31", "10-Q", "2011-03-11"), (69.0e6, N, "2011-01-31", "10-Q", "2011-09-09")]
DECLARED, PAID = "CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"


def test_a_repeat_first_filed_inside_a_splits_window_keeps_its_own_filings_basis():
    # NDSN's 10-Q of 2011-06-03 is on the basis after its 2:1 split, which its counts date only by 2011-09-09: its
    # half year of 1.61, repeated a year on, is no figure of the old basis.
    rows = {DILUTED: _COUNTS,
            EPS: [(1.33, N, "2011-01-31", "10-Q", "2011-03-11"), (0.67, N, "2011-01-31", "10-K", "2011-12-16"),
                  (1.61, N, "2011-04-30", "10-Q", "2011-06-03"), (1.61, N, "2011-04-30", "10-Q", "2012-06-05"),
                  (2.44, N, "2011-07-31", "10-Q", "2011-09-09"), (3.25, N, "2011-10-31", "10-K", "2011-12-16")]}
    assert _split_restatements(_facts(rows)["facts"]["us-gaap"]) == [("2011-03-11", "2011-09-09", 2.0)]
    quarterly = XBRLParser().extract_quarterly_financials(_facts(rows))
    assert quarterly["2011-04-30"]["eps_diluted"] == pytest.approx(1.61 - 0.67)


def test_a_count_first_filed_inside_a_splits_window_keeps_its_own_filings_basis():
    # A quarter-end count first filed by that 10-Q of 2011-06-03, already after the split, and repeated a year on.
    rows = {DILUTED: _COUNTS,
            EPS: [(0.67, N, "2011-01-31", "10-K", "2011-12-16"), (1.61, N, "2011-04-30", "10-Q", "2012-06-05"),
                  (2.44, N, "2011-07-31", "10-Q", "2011-09-09"), (3.25, N, "2011-10-31", "10-K", "2011-12-16")],
            OUTSTANDING: [(70.0e6, None, "2011-04-30", "10-Q", "2011-06-03"),
                          (70.0e6, None, "2011-04-30", "10-Q", "2012-06-05"),
                          (70.4e6, None, "2011-10-31", "10-K", "2011-12-16")]}
    quarterly = XBRLParser().extract_quarterly_financials(_facts(rows))
    assert quarterly["2011-04-30"]["shares_outstanding"] == pytest.approx(70.0e6)

def test_a_per_share_figure_a_split_moves_by_half_a_cent_is_no_repeat():
    # SBUX's FY2013 EPS of 0.01, filed again after its 2:1 split as 0.01 (0.005 rounded): read as restated, so the
    # year's quarters go onto the new basis.
    rows = {DILUTED: _COUNTS,
            EPS: [(0.57, P, "2010-01-31", "10-Q", "2010-03-11"), (0.01, P, "2010-10-31", "10-K", "2010-12-17"),
                  (0.01, P, "2010-10-31", "10-K", "2011-12-16")]}
    quarterly = XBRLParser().extract_quarterly_financials(_facts(rows))
    assert quarterly["2010-01-31"]["eps_diluted"] == pytest.approx(0.57 / 2)


def test_another_tags_equal_figure_is_no_repeat():
    # BF-B's dividends declared in the first quarter, 0.315 after its 2:1 split, equal the dividends paid as first
    # filed before it: no repeat, so the declared figure stays on the new basis.
    rows = {DILUTED: _COUNTS,
            DECLARED: [(0.63, N, "2011-01-31", "10-Q", "2011-03-11"), (0.315, N, "2011-01-31", "10-K", "2011-12-16"),
                       (1.26, N, "2011-10-31", "10-K", "2011-12-16")],
            PAID: [(0.315, N, "2011-01-31", "10-Q", "2011-03-11")]}
    quarterly = XBRLParser().extract_quarterly_financials(_facts(rows))
    assert quarterly["2011-01-31"]["dividends_per_share"] == pytest.approx(0.315)
