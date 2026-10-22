"""A guru's quarters (Milestone 3): the positions of each period of report from the 13F filings that stand (the latest
original or restatement passing the checksum, and the NEW HOLDINGS amendments filed after it), the union of the
guru's filers, keyed by the security (the CUSIP's ticker, else the CUSIP), options apart from shares; and the changes
from the quarter before, the shares before adjusted for the ticker's share splits between
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).
"""
import sqlite3
from decimal import Decimal

import pytest

from src.exporters.sqlite_store import SQLiteStore, read_only_uri
from src.ownership.collect import FilingRef
from src.ownership.gurus import Guru
from src.ownership.portfolio import (
    Entry,
    Filing,
    Quarter,
    build_quarter,
    build_quarters,
    load_guru,
    previous_period,
    quarter_changes,
    share_classes,
    split_factor,
)
from src.ownership.thirteenf import Holding, ThirteenF

Q1, Q2 = "2026-03-31", "2026-06-30"
BERKSHIRE = "1067983"


def _filing(accession, period=Q2, filed="2026-08-14", form="13F-HR", amendment=None, ok=True, filer=BERKSHIRE):
    return Filing(accession=accession, filer_cik=filer, form=form, period=period, filed=filed,
                  amendment_type=amendment, checksum_ok=ok)


def _restatement(accession, filed, **kw):
    return _filing(accession, filed=filed, form="13F-HR/A", amendment="RESTATEMENT", **kw)


def _new_holdings(accession, filed, **kw):
    return _filing(accession, filed=filed, form="13F-HR/A", amendment="NEW HOLDINGS", **kw)


def _entries(accession, *positions):
    """One filing's entries, in its order: each (cusip, shares, value) or (cusip, shares, value, put_call)."""
    return [Entry(accession=accession, row=row, cusip=p[0], issuer=f"ISSUER {p[0]}", title_of_class="COM",
                  value_usd=float(p[2]), shares=float(p[1]), share_type="SH", put_call=p[3] if len(p) > 3 else None)
            for row, p in enumerate(positions)]


def _held(quarter):
    """A quarter's positions: key -> (shares, value)."""
    return {key: (p.shares, p.value_usd) for key, p in quarter.positions.items()}


def _quarter(period, *positions, tickers=None):
    """A quarter from one original filing holding *positions*, its CUSIPs' tickers *tickers*."""
    return build_quarter(period, [_filing("A-" + period, period=period)], _entries("A-" + period, *positions),
                         tickers)


def test_the_period_before():
    assert previous_period("2026-06-30") == "2026-03-31"
    assert previous_period("2026-03-31") == "2025-12-31"
    assert previous_period("2025-12-31") == "2025-09-30"
    assert previous_period("2025-09-30") == "2025-06-30"


# ---- the filings a quarter stands on ------------------------------------------------------------------------------

def test_a_quarter_from_its_original():
    q = build_quarter(Q2, [_filing("O")], _entries("O", ("A", 100, 1000), ("B", 50, 3000)))
    assert (q.period, q.accessions, q.complete) == (Q2, ("O",), True)
    assert _held(q) == {("A", None): (100.0, 1000.0), ("B", None): (50.0, 3000.0)}


def test_the_latest_restatement_is_the_base():
    # Oaktree's Q1 2026: the 13F-HR of May 15, restated on May 19 and again on May 20.
    filings = [_restatement("R2", "2026-05-20"), _filing("O", filed="2026-05-15"), _restatement("R1", "2026-05-19")]
    entries = _entries("O", ("A", 100, 1000)) + _entries("R1", ("A", 110, 1100)) + _entries("R2", ("A", 120, 1200),
                                                                                         ("B", 5, 50))
    q = build_quarter(Q2, filings, entries)
    assert q.accessions == ("R2",) and q.complete
    assert _held(q) == {("A", None): (120.0, 1200.0), ("B", None): (5.0, 50.0)}


def test_an_amendment_without_a_type_is_a_restatement():
    filings = [_filing("O", filed="2026-08-14"), _filing("A1", filed="2026-08-20", form="13F-HR/A")]
    q = build_quarter(Q2, filings, _entries("O", ("A", 100, 1000)) + _entries("A1", ("A", 90, 900)))
    assert q.accessions == ("A1",) and _held(q) == {("A", None): (90.0, 900.0)}


def test_new_holdings_filed_after_the_base_add_their_rows():
    # A confidential holding disclosed later: a new CUSIP, and more of one already reported.
    filings = [_new_holdings("N", "2026-11-14"), _filing("O")]
    entries = _entries("O", ("A", 100, 1000), ("B", 10, 100)) + _entries("N", ("C", 7, 70), ("A", 20, 200))
    q = build_quarter(Q2, filings, entries)
    assert q.accessions == ("O", "N") and q.complete
    assert _held(q) == {("A", None): (120.0, 1200.0), ("B", None): (10.0, 100.0), ("C", None): (7.0, 70.0)}


def test_new_holdings_rows_that_repeat_the_bases_are_left_out_one_for_one():
    # First Eagle's Q2 2026 NEW HOLDINGS amendment re-files its base's rows with two more; Berkshire's Q1 2025 one adds
    # 202 Lennar B shares to a CUSIP its base holds. A row the same as one of the base's in CUSIP, put/call, shares and
    # value is a duplicate, each base row matching one; every other row adds.
    base = _entries("O", ("A", 100, 1000), ("B", 10, 100), ("B", 10, 100), ("C", 5, 50, "Put"), ("E", 10, 500),
                    ("F", 10, 100))
    amendment = _entries("N", ("A", 100, 1000), ("B", 10, 100), ("B", 10, 100), ("B", 10, 100), ("C", 5, 50, "Call"),
                         ("A", 20, 200), ("D", 7, 70), ("E", 12, 500), ("F", 10, 110))
    q = build_quarter(Q2, [_filing("O"), _new_holdings("N", "2026-08-24")], base + amendment)
    assert q.accessions == ("O", "N")
    assert _held(q) == {
        ("A", None): (120.0, 1200.0),  # the repeated row out, the 20 more shares in
        ("B", None): (30.0, 300.0),  # two of the three repeat the base's two
        ("C", "Put"): (5.0, 50.0), ("C", "Call"): (5.0, 50.0),  # a call isn't the base's put
        ("E", None): (22.0, 1000.0), ("F", None): (20.0, 210.0),  # other shares, another value: rows of their own
        ("D", None): (7.0, 70.0)}
    assert q.total_value == 2880.0


def test_each_new_holdings_amendment_is_matched_against_the_base():
    # Two complete re-filings labelled NEW HOLDINGS, the second with a holding more: the quarter is the base and it.
    filings = [_filing("O"), _new_holdings("N1", "2026-08-20"), _new_holdings("N2", "2026-08-24")]
    entries = (_entries("O", ("A", 100, 1000), ("B", 10, 100)) + _entries("N1", ("A", 100, 1000), ("B", 10, 100))
               + _entries("N2", ("A", 100, 1000), ("B", 10, 100), ("C", 1, 10)))
    q = build_quarter(Q2, filings, entries)
    assert _held(q) == {("A", None): (100.0, 1000.0), ("B", None): (10.0, 100.0), ("C", None): (1.0, 10.0)}
    # A restatement's rows are never matched: it is the base.
    restated = build_quarter(Q2, [_filing("O"), _restatement("R", "2026-08-20")],
                             _entries("O", ("A", 100, 1000)) + _entries("R", ("A", 100, 1000)))
    assert _held(restated) == {("A", None): (100.0, 1000.0)}


def test_an_amendment_filed_before_the_base_is_superseded_by_it():
    # Original, then NEW HOLDINGS, then a restatement that carries everything: the restatement alone.
    filings = [_filing("O", filed="2026-08-14"), _new_holdings("N", "2026-08-16"), _restatement("R", "2026-08-20")]
    entries = (_entries("O", ("A", 100, 1000)) + _entries("N", ("C", 7, 70))
               + _entries("R", ("A", 100, 1000), ("C", 7, 70), ("D", 1, 10)))
    q = build_quarter(Q2, filings, entries)
    assert q.accessions == ("R",)
    assert _held(q) == {("A", None): (100.0, 1000.0), ("C", None): (7.0, 70.0), ("D", None): (1.0, 10.0)}


def test_filings_of_one_day_go_by_accession():
    # The day's restatement (...-000004) came before its NEW HOLDINGS amendment (...-000005): the amendment adds.
    restated, added = "0000949509-26-000004", "0000949509-26-000005"
    filings = [_new_holdings(added, "2026-05-20"), _restatement(restated, "2026-05-20"),
               _filing("0000949509-26-000002", filed="2026-05-15")]
    entries = (_entries("0000949509-26-000002", ("A", 1, 10)) + _entries(restated, ("A", 2, 20))
               + _entries(added, ("B", 3, 30)))
    q = build_quarter(Q2, filings, entries)
    assert q.accessions == (restated, added)
    assert _held(q) == {("A", None): (2.0, 20.0), ("B", None): (3.0, 30.0)}
    # Two bases of one day: the later accession stands.
    late = [_restatement("0000949509-26-000007", "2026-05-21"), _restatement("0000949509-26-000006", "2026-05-21")]
    q = build_quarter(Q2, late, _entries("0000949509-26-000006", ("A", 6, 60))
                      + _entries("0000949509-26-000007", ("A", 7, 70)))
    assert q.accessions == ("0000949509-26-000007",)


def test_a_quarters_filing_date_is_the_latest_of_the_filings_it_stands_on():
    filings = [_filing("A-1", filed="2026-08-14"), _new_holdings("A-2", "2026-09-02"),
               _filing("A-0", filed="2026-08-01", ok=False),  # fails the checksum: not stood on
               _restatement("A-9", "2026-10-30", ok=False)]
    quarter = build_quarter(Q2, filings, _entries("A-1", ("X", 1, 1)) + _entries("A-2", ("Y", 1, 1)))
    assert (quarter.accessions, quarter.filed) == (("A-1", "A-2"), "2026-09-02")
    assert build_quarter(Q2, [_filing("A-0", ok=False)], []).filed is None  # stands on none


def test_a_filing_that_fails_the_checksum_is_left_out():
    entries = _entries("O", ("A", 100, 1000)) + _entries("R", ("A", 90, 900)) + _entries("N", ("B", 5, 50))
    later_restatement_fails = build_quarter(Q2, [_filing("O"), _restatement("R", "2026-08-20", ok=False)], entries)
    assert later_restatement_fails.accessions == ("O",) and later_restatement_fails.complete
    original_fails = build_quarter(Q2, [_filing("O", ok=False), _restatement("R", "2026-08-20")], entries)
    assert original_fails.accessions == ("R",) and original_fails.complete
    new_holdings_fail = build_quarter(Q2, [_filing("O"), _new_holdings("N", "2026-08-20", ok=False)], entries)
    assert new_holdings_fail.accessions == ("O",) and _held(new_holdings_fail) == {("A", None): (100.0, 1000.0)}


def test_a_filer_without_a_passing_original_or_restatement_leaves_the_quarter_incomplete():
    # Two filers of one guru: the second's original fails the checksum, so its rows are left out, its NEW HOLDINGS
    # amendment's too.
    filings = [_filing("P1", filer="1336528"), _filing("P2", filer="2026053", ok=False),
               _new_holdings("P2N", "2026-09-01", filer="2026053")]
    entries = _entries("P1", ("A", 100, 1000)) + _entries("P2", ("B", 5, 50)) + _entries("P2N", ("C", 3, 30))
    q = build_quarter(Q2, filings, entries)
    assert (q.complete, q.accessions, _held(q)) == (False, ("P1",), {("A", None): (100.0, 1000.0)})
    assert q.total_value == 1000.0
    # A filer with nothing but a NEW HOLDINGS amendment has no base either.
    q = build_quarter(Q2, [_filing("P1", filer="1336528"), _new_holdings("P2N", "2026-09-01", filer="2026053")],
                      entries)
    assert (q.complete, q.accessions) == (False, ("P1",))


def test_a_gurus_quarter_is_the_union_of_its_filers():
    # Pershing Square Capital Management and Pershing Square Inc. both reporting Q1 2026: one guru.
    filings = [_filing("PSI", period=Q1, filed="2026-05-15", filer="2026053"),
               _filing("PSCM", period=Q1, filed="2026-05-14", filer="1336528"),
               _filing("Q2", period=Q2, filer="2026053")]  # another period
    entries = (_entries("PSI", ("A", 50, 500), ("C", 1, 10)) + _entries("PSCM", ("A", 100, 1000), ("B", 10, 100))
               + _entries("Q2", ("Z", 1, 1)))
    q = build_quarter(Q1, filings, entries)
    assert q.accessions == ("PSCM", "PSI") and q.complete
    assert _held(q) == {("A", None): (150.0, 1500.0), ("B", None): (10.0, 100.0), ("C", None): (1.0, 10.0)}


# ---- positions ----------------------------------------------------------------------------------------------------

def test_options_are_kept_apart_from_the_shares():
    q = build_quarter(Q2, [_filing("O")], _entries("O", ("A", 100, 1000), ("A", 30, 300, "Call"),
                                                   ("A", 20, 200, "Put"), ("A", 5, 50, "Call")))
    assert _held(q) == {("A", None): (100.0, 1000.0), ("A", "Call"): (35.0, 350.0), ("A", "Put"): (20.0, 200.0)}
    assert [(p.security, p.put_call) for p in q.positions.values()] == [("A", None), ("A", "Call"), ("A", "Put")]


def test_rows_of_one_key_are_summed_keeping_the_first_seen_issuer_class_and_type():
    # One CUSIP in several rows (other managers, discretion), given out of their order.
    rows = [Entry("O", 2, "A", "ALPHA INC NEW", "CL A", 500.0, 50.0, "PRN", None),
            Entry("O", 1, "B", "BETA CORP", "COM", 200.0, 20.0, "SH", None),
            Entry("O", 0, "A", "ALPHA INC", "COM", 1000.0, 100.0, "SH", None)]
    q = build_quarter(Q2, [_filing("O")], rows)
    alpha = q.positions[("A", None)]
    assert (alpha.issuer, alpha.title_of_class, alpha.share_type, alpha.shares, alpha.value_usd, alpha.cusips) == (
        "ALPHA INC", "COM", "SH", 150.0, 1500.0, ("A",))
    assert list(q.positions) == [("A", None), ("B", None)]  # in the filing's order


def test_a_position_is_keyed_by_its_cusips_ticker_else_the_cusip():
    # Exxon Mobil Corp's 30231G102 and ExxonMobil Holdings' 30233Q108 in one quarter, as filers moved to the new one;
    # a filer's 200021014 for Allstate's 020002101; a CUSIP without a ticker keeps its own.
    tickers = {"30231G102": "XOM", "30233Q108": "XOM", "020002101": "ALL", "200021014": "ALL"}
    q = build_quarter(Q2, [_filing("O")], [
        Entry("O", 0, "30231G102", "EXXON MOBIL CORP", "COM", 1000.0, 10.0, "SH", None),
        Entry("O", 1, "200021014", "The Allstate Corporation", "COM", 300.0, 3.0, "SH", None),
        Entry("O", 2, "30233Q108", "EXXONMOBIL HOLDINGS", "COM", 500.0, 5.0, "SH", None),
        Entry("O", 3, "000000000", "GONE CORP", "COM", 1.0, 1.0, "SH", None),
        Entry("O", 4, "30233Q108", "EXXONMOBIL HOLDINGS", "COM", 100.0, 1.0, "SH", "Call")], tickers)
    assert [(p.security, p.ticker, p.put_call, p.cusips, p.issuer, p.shares, p.value_usd)
            for p in q.positions.values()] == [
        ("XOM", "XOM", None, ("30231G102", "30233Q108"), "EXXON MOBIL CORP", 15.0, 1500.0),
        ("ALL", "ALL", None, ("200021014",), "The Allstate Corporation", 3.0, 300.0),
        ("000000000", None, None, ("000000000",), "GONE CORP", 1.0, 1.0),
        ("XOM", "XOM", "Call", ("30233Q108",), "EXXONMOBIL HOLDINGS", 1.0, 100.0)]


def test_the_quarters_total_and_each_positions_weight():
    q = build_quarter(Q2, [_filing("O")], _entries("O", ("A", 10, 1000), ("B", 30, 3000), ("A", 5, 1000, "Call")))
    assert q.total_value == 5000.0
    assert {key: p.weight for key, p in q.positions.items()} == {("A", None): 0.2, ("B", None): 0.6,
                                                                 ("A", "Call"): 0.2}
    empty = build_quarter(Q2, [_filing("O")], _entries("O", ("A", 10, 0)))
    assert empty.total_value == 0.0 and empty.positions[("A", None)].weight is None


def test_a_row_without_a_figure_adds_nothing_to_it():
    q = build_quarter(Q2, [_filing("O")], [Entry("O", 0, "A", "ALPHA", "COM", 1000.0, 100.0, "SH", None),
                                          Entry("O", 1, "A", "ALPHA", "COM", None, None, "SH", None)])
    assert _held(q) == {("A", None): (100.0, 1000.0)} and q.total_value == 1000.0


def test_every_period_of_the_gurus_filings():
    filings = [_filing("B", period=Q2), _filing("A", period=Q1, filed="2026-05-15"), _filing("X", period=None)]
    entries = _entries("A", ("A", 1, 10)) + _entries("B", ("B", 2, 20))
    quarters = build_quarters(filings, entries)
    assert list(quarters) == [Q1, Q2]  # oldest first; a filing without a period is in none
    assert _held(quarters[Q1]) == {("A", None): (1.0, 10.0)} and quarters[Q2].accessions == ("B",)


# ---- changes ------------------------------------------------------------------------------------------------------

def test_the_changes_from_the_quarter_before():
    before = _quarter(Q1, ("A", 100, 1000), ("B", 50, 500), ("C", 10, 100), ("D", 7, 70), ("A", 4, 40, "Put"))
    now = _quarter(Q2, ("A", 150, 1800), ("B", 25, 300), ("C", 10, 120), ("E", 5, 50), ("A", 4, 30, "Call"))
    changes = quarter_changes(now, before)
    assert [(c.security, c.put_call, c.action, c.shares_before, c.shares_after, c.value_before, c.value_after,
             c.change_pct) for c in changes] == [
        ("A", None, "added", 100.0, 150.0, 1000.0, 1800.0, 0.5),
        ("A", "Call", "new", None, 4.0, None, 30.0, None),
        ("A", "Put", "sold_out", 4.0, None, 40.0, None, None),
        ("B", None, "reduced", 50.0, 25.0, 500.0, 300.0, -0.5),
        ("C", None, "unchanged", 10.0, 10.0, 100.0, 120.0, 0.0),  # the shares decide, not the value
        ("D", None, "sold_out", 7.0, None, 70.0, None, None),
        ("E", None, "new", None, 5.0, None, 50.0, None),
    ]
    assert (changes[5].issuer, changes[6].issuer) == ("ISSUER D", "ISSUER E")


def test_no_changes_without_the_quarter_before():
    now = _quarter(Q2, ("A", 1, 10))
    assert quarter_changes(now, None) is None
    assert quarter_changes(now, _quarter("2025-12-31", ("A", 1, 10))) is None  # not the quarter before


def test_a_position_from_nothing_has_no_percent():
    changes = quarter_changes(_quarter(Q2, ("A", 5, 50)), _quarter(Q1, ("A", 0, 0)))
    assert [(c.action, c.change_pct) for c in changes] == [("added", None)]


TICKERS = {"09857L108": "BKNG", "482480100": "KLAC", "438516106": "HON"}


def test_the_share_splits_between_the_quarters_adjust_the_shares_before():
    # BKNG's 25-for-1 of 2026-04-06 and KLAC's 10-for-1 of 2026-06-12 fall in Q2 2026; HON's 0.9535 of 2026-06-29
    # isn't a share split's ratio; 000000000 has no ticker, so no split data (not our universe).
    before = _quarter(Q1, ("09857L108", 100, 500000), ("482480100", 100, 80000), ("438516106", 100, 20000),
                      ("000000000", 100, 1000), ("482480100", 10, 8000, "Call"), tickers=TICKERS)
    now = _quarter(Q2, ("09857L108", 2500, 525000), ("482480100", 1200, 120000), ("438516106", 100, 21000),
                   ("000000000", 100, 1100), ("482480100", 100, 10000, "Call"), tickers=TICKERS)
    splits = {"BKNG": [("2026-04-06", 25.0)], "KLAC": [("2026-06-12", 10.0), ("2024-06-01", 2.0)],
              "HON": [("2026-06-29", 0.9535), ("2025-10-30", 1.061)], "000000000": [("2026-05-01", 2.0)]}
    changes = quarter_changes(now, before, splits)
    assert [(c.security, c.ticker, c.put_call, c.action, c.shares_before, c.split_factor, c.shares_before_adjusted,
             c.change_pct) for c in changes] == [
        ("000000000", None, None, "unchanged", 100.0, 1.0, 100.0, 0.0),  # by its ticker only: it has none
        ("BKNG", "BKNG", None, "unchanged", 100.0, 25.0, 2500.0, 0.0),
        ("HON", "HON", None, "unchanged", 100.0, 1.0, 100.0, 0.0),
        ("KLAC", "KLAC", None, "added", 100.0, 10.0, 1000.0, pytest.approx(0.2)),
        ("KLAC", "KLAC", "Call", "unchanged", 10.0, 10.0, 100.0, 0.0),  # an option on the split shares
    ]
    # Without the split data, BKNG's 25-for-1 reads as 25 times the shares.
    unadjusted = {(c.security, c.put_call): c for c in quarter_changes(now, before)}
    assert (unadjusted[("BKNG", None)].action, unadjusted[("BKNG", None)].change_pct) == ("added", 24.0)


def test_a_security_that_changed_its_cusip_across_a_split_is_one_position():
    # A 1-for-2 reverse split that gave the stock a new CUSIP: the quarter before on the old one, this one on the new.
    tickers = {"438516106": "HON", "438516205": "HON"}
    splits = {"HON": [("2026-06-29", 0.5)]}
    before = _quarter(Q1, ("438516106", 8000, 1800000), tickers=tickers)
    for shares, action, pct in ((4000, "unchanged", 0.0), (4400, "added", 0.1), (3000, "reduced", -0.25)):
        now = _quarter(Q2, ("438516205", shares, shares * 225), tickers=tickers)
        (change,) = quarter_changes(now, before, splits)
        assert (change.security, change.action, change.shares_before, change.shares_after, change.split_factor,
                change.change_pct) == ("HON", action, 8000.0, float(shares), 0.5, pytest.approx(pct))
        assert change.cusips == ("438516205", "438516106")  # the codes it covers, this quarter's first
    # Keyed by CUSIP, as without the map, the guru would read as selling out and buying anew.
    apart = quarter_changes(_quarter(Q2, ("438516205", 4000, 900000)), _quarter(Q1, ("438516106", 8000, 1800000)),
                            splits)
    assert [(c.security, c.action) for c in apart] == [("438516106", "sold_out"), ("438516205", "new")]


def test_the_splits_counted_are_those_after_the_quarter_before_through_this_one():
    assert split_factor([("2026-03-31", 2.0)], Q1, Q2) == 1.0  # on the day before's count: in it already
    assert split_factor([("2026-04-01", 2.0)], Q1, Q2) == 2.0
    assert split_factor([("2026-06-30", 2.0)], Q1, Q2) == 2.0  # in this quarter's count
    assert split_factor([("2026-07-01", 2.0)], Q1, Q2) == 1.0
    assert split_factor([("2026-04-06", 25.0), ("2026-05-01", 2.0)], Q1, Q2) == 50.0
    assert split_factor([("2026-06-30 00:00:00", 3.0)], Q1, Q2) == 3.0  # a time with the date: the day counts
    # Only share splits, a reverse one too: not SCCO's stock dividends (1.01), HON's 0.9535, a missing ratio.
    assert split_factor([("2026-05-13", 1.01), ("2026-06-29", 0.9535), ("2026-04-02", 1.5), ("2026-04-03", 0.1),
                         ("2026-04-04", None), ("2026-04-05", 0.0)], Q1, Q2) == pytest.approx(0.15)


def test_a_tickers_other_share_classes_are_those_of_its_issuer():
    # Our universe's tickers whose CUSIPs share an issuer (the first six characters) with one of the ticker's.
    assert share_classes("GOOGL") == ("GOOGL", "GOOG") and share_classes("GOOG") == ("GOOG", "GOOGL")
    assert share_classes("BRK-B") == ("BRK-B", "BRK-A")
    assert share_classes("XOM") == ("XOM",)  # two issuers' codes (Exxon Mobil Corp's, ExxonMobil Holdings'), one class
    assert share_classes("AAPL") == ("AAPL",) and share_classes("ZZZZ") == ("ZZZZ",)
    universe = {"123456101": "B", "123456200": "C", "123456309": "A", "654321100": "D", "654321209": "D"}
    assert share_classes("B", universe) == ("B", "A", "C")  # its own first, the others in order
    assert share_classes("D", universe) == ("D",)


# ---- the loader ---------------------------------------------------------------------------------------------------

ACKMAN = Guru("2026053", "PERSHING SQUARE INC.", "Bill Ackman",
              former=(("1336528", "Pershing Square Capital Management, L.P."),))


def _thirteenf(accession, period, holdings, form="13F-HR", amendment=None, entry_total=None):
    """A parsed 13F: *holdings* (cusip, shares, value[, put_call]) in dollars, its cover's totals agreeing with them
    unless *entry_total* says otherwise."""
    rows = [Holding(row=i, cusip=h[0], issuer=f"ISSUER {h[0]}", title_of_class="COM", value=Decimal(h[2]),
                    value_usd=float(h[2]), shares=float(h[1]), share_type="SH", put_call=h[3] if len(h) > 3 else None)
            for i, h in enumerate(holdings)]
    return ThirteenF(accession=accession, form=form, period=period, amendment_type=amendment,
                     entry_total=len(rows) if entry_total is None else entry_total,
                     value_total=sum((h.value for h in rows), Decimal(0)), holdings=rows)


def _store_db(tmp_path):
    db = tmp_path / "gurus.db"
    store = SQLiteStore(db_path=db)
    store.export_guru_filings(ACKMAN, [
        (FilingRef("PSCM-Q1", "13F-HR", "2026-05-15", Q1, "PSCM-Q1.txt", "1336528"),
         _thirteenf("PSCM-Q1", Q1, [("09857L108", 100, 500000), ("HHH", 10, 900)])),
        (FilingRef("PSI-Q1", "13F-HR", "2026-05-15", Q1, "PSI-Q1.txt", "2026053"),
         _thirteenf("PSI-Q1", Q1, [("HHH", 5, 450)])),
        (FilingRef("PSI-Q2", "13F-HR", "2026-08-14", Q2, "PSI-Q2.txt", "2026053"),
         _thirteenf("PSI-Q2", Q2, [("09857L108", 2500, 525000), ("NEW", 1, 10)])),
        (FilingRef("PSI-Q2-BAD", "13F-HR/A", "2026-08-20", Q2, "PSI-Q2-BAD.txt", "2026053"),
         _thirteenf("PSI-Q2-BAD", Q2, [("09857L108", 1, 1)], form="13F-HR/A", amendment="RESTATEMENT", entry_total=9)),
    ], "2026-10-22T00:00:00+00:00")
    store.export_guru_filings(Guru("1709323", "Himalaya Capital Management LLC", "Li Lu"), [
        (FilingRef("H-Q2", "13F-HR", "2026-08-14", Q2, "H-Q2.txt", "1709323"), _thirteenf("H-Q2", Q2, [("X", 1, 1)])),
    ], "2026-10-22T00:00:00+00:00")
    store.export_cusip_map([
        {"cusip": "09857L108", "ticker": "BKNG", "name": "BOOKING HOLDINGS INC", "source": "universe",
         "security_type": None, "mapped_at": "2026-10-22T00:00:00+00:00"},
        {"cusip": "NEW", "ticker": None, "name": "ISSUER NEW", "source": "none", "security_type": None,
         "mapped_at": "2026-10-22T00:00:00+00:00"}])
    conn = sqlite3.connect(db)
    conn.executemany("INSERT INTO split_events (ticker, date, ratio) VALUES (?, ?, ?)",
                     [("BKNG", "2026-04-06", 25.0), ("HHH", "2026-04-07", 2.0)])  # HHH: no cusip_map row
    conn.commit()
    conn.close()
    return db


def test_load_guru_reads_its_quarters_and_their_changes(tmp_path):
    conn = sqlite3.connect(read_only_uri(_store_db(tmp_path)), uri=True)
    try:
        guru = load_guru(conn, "2026053")
    finally:
        conn.close()
    assert guru.guru_cik == "2026053" and list(guru.quarters) == [Q1, Q2]
    q1, q2 = guru.quarters[Q1], guru.quarters[Q2]
    assert isinstance(q1, Quarter) and q1.accessions == ("PSCM-Q1", "PSI-Q1") and q1.complete
    assert _held(q1) == {("BKNG", None): (100.0, 500000.0), ("HHH", None): (15.0, 1350.0)}
    assert q2.accessions == ("PSI-Q2",) and q2.total_value == 525010.0  # the failed restatement left out
    assert guru.changes[Q1] is None  # no Q4 2025
    # HHH has no ticker (no cusip_map row), so the split_events of a ticker spelled like it don't apply; NEW's row
    # has no ticker either (OpenFIGI found none).
    assert [(c.security, c.ticker, c.cusips, c.action, c.split_factor, c.change_pct) for c in guru.changes[Q2]] == [
        ("BKNG", "BKNG", ("09857L108",), "unchanged", 25.0, 0.0), ("HHH", None, ("HHH",), "sold_out", 1.0, None),
        ("NEW", None, ("NEW",), "new", 1.0, None)]


def test_load_guru_on_a_database_from_before_the_cusip_map(tmp_path):
    db = _store_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE cusip_map")
    conn.commit()
    try:
        changes = load_guru(conn, "2026053").changes[Q2]
    finally:
        conn.close()
    assert [(c.security, c.ticker, c.action, c.split_factor) for c in changes][0] == (
        "09857L108", None, "added", 1.0)


def test_load_guru_without_filings_or_their_tables(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "fresh.db")
    conn = store._connect()
    store._create_schema(conn)
    fresh = load_guru(conn, BERKSHIRE)
    conn.close()
    conn = sqlite3.connect(tmp_path / "empty.db")  # a database from before the gurus' tables
    empty = load_guru(conn, BERKSHIRE)
    conn.close()
    assert (fresh.quarters, fresh.changes) == (empty.quarters, empty.changes) == ({}, {})
