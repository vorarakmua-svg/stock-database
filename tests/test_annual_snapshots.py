"""The annual record as it was known on each filing date: one replay of the record per date, a row per change."""

import math
from typing import Any, Dict, List, Optional

from src.mappings.canonical import CANONICAL_BY_KEY
from src.parsers import annual_snapshots
from src.parsers.annual_record import PARSED_TAXONOMY, build_annual_record
from src.parsers.annual_snapshots import SnapshotSet, build_snapshots
from src.parsers.xbrl_parser import XBRLParser


def _fact(val: Any, end: str, filed: Optional[str], accn: str, start: Optional[str] = None,
          form: str = "10-K") -> Dict[str, Any]:
    entry: Dict[str, Any] = {"val": val, "end": end, "accn": accn, "fy": int(end[:4]), "fp": "FY", "form": form}
    if start:
        entry["start"] = start
    if filed is not None:
        entry["filed"] = filed
    return entry


def _year(val: Any, year: int, filed: Optional[str], accn: str, form: str = "10-K") -> Dict[str, Any]:
    return _fact(val, f"{year}-12-31", filed, accn, start=f"{year}-01-01", form=form)


def _facts(**tags: List[Dict[str, Any]]) -> Dict[str, Any]:
    units = {"EarningsPerShareDiluted": "USD/shares", "CommonStockDividendsPerShareDeclared": "USD/shares",
             "WeightedAverageNumberOfDilutedSharesOutstanding": "shares"}
    return {"cik": 1, "entityName": "X", "facts": {"us-gaap": {
        tag: {"label": tag, "units": {units.get(tag, "USD"): entries}} for tag, entries in tags.items()}}}


#: Three 10-Ks: FY2019's alone, FY2020's alone, then FY2021's with FY2020 as filed and FY2019 restated.
D1, D2, D3 = "2020-02-14", "2021-02-12", "2022-02-11"


def _three_10ks() -> Dict[str, Any]:
    return _facts(
        Revenues=[_year(100.0, 2019, D1, "a1"), _year(110.0, 2020, D2, "a2"),
                  _year(120.0, 2021, D3, "a3"), _year(110.0, 2020, D3, "a3"), _year(95.0, 2019, D3, "a3")],
        NetIncomeLoss=[_year(10.0e6, 2019, D1, "a1"), _year(11.0e6, 2020, D2, "a2"),
                       _year(12.0e6, 2021, D3, "a3"), _year(11.0e6, 2020, D3, "a3")],
        EarningsPerShareDiluted=[_year(1.0, 2019, D1, "a1"), _year(1.1, 2020, D2, "a2"),
                                 _year(1.2, 2021, D3, "a3"), _year(1.1, 2020, D3, "a3")],
        WeightedAverageNumberOfDilutedSharesOutstanding=[
            _year(10.0e6, 2019, D1, "a1"), _year(10.0e3, 2020, D2, "a2"),  # FY2020's count filed in thousands
            _year(10.0e6, 2021, D3, "a3"), _year(10.0e6, 2020, D3, "a3")])


def _rows(snapshots: SnapshotSet, year: int) -> List[Dict[str, Any]]:
    return [row for row in snapshots.rows if row["fiscal_year"] == year]


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return bool(a == b)


def test_one_row_per_change():
    snapshots = build_snapshots(XBRLParser(), _three_10ks(), ticker="X", years_back=None)
    assert [row["known_from"] for row in _rows(snapshots, 2019)] == [D1, D3]
    assert [row["revenue"] for row in _rows(snapshots, 2019)] == [100.0, 95.0]
    first = _rows(snapshots, 2019)[0]
    assert first["present"] is True and first["known_accn"] == "a1"
    assert (first["accn"], first["filed_date"], first["form"]) == ("a1", D1, "10-K")
    assert first["source_tags"]["revenue"] == "Revenues"
    assert first["source_filings"]["revenue"] == {"accn": "a1", "filed": D1, "form": "10-K"}
    assert (first["period_end"], first["calendar_year"]) == ("2019-12-31", 2019)
    # FY2020 changes when the third 10-K files it again (its filings), and FY2021 opens then.
    assert [row["known_from"] for row in _rows(snapshots, 2020)] == [D2, D3]
    assert [row["known_from"] for row in _rows(snapshots, 2021)] == [D3]


def test_the_latest_snapshot_equals_the_record():
    facts = _three_10ks()
    snapshots = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    record = build_annual_record(XBRLParser(), facts, ticker="X", years_back=None)
    assert {str(row["fiscal_year"]) for row in snapshots.rows} == set(record.periods)
    for fy, period in record.periods.items():
        last = [row for row in snapshots.rows if row["fiscal_year"] == int(fy)][-1]
        assert last["present"] is True
        values = {k: v for k, v in last.items() if k in CANONICAL_BY_KEY}
        assert values.keys() == {k for k in period if k in CANONICAL_BY_KEY}
        assert all(_same(v, period[k]) for k, v in values.items())
        assert last["source_tags"] == period["_source_tags"]
        assert last["source_filings"] == period["_source_filings"]
        for key in ("period_end", "calendar_year", "frame"):
            assert last.get(key) == period.get(key)
    # the record's cleaning runs in every snapshot: FY2020's count is in units from the day it was filed
    assert [row["weighted_avg_shares_diluted"] for row in _rows(snapshots, 2020)] == [10.0e6, 10.0e6]


def test_a_year_that_leaves_the_record_gets_a_tombstone():
    # FY2019's only figure is its balance sheet at 2019-06-30. A 10-K/A of 2019-09-03 tags assets at 2019-12-31
    # (a figure of the year ahead, LLY's shape in BUG-113): the year now holds that period, which hasn't ended, so the
    # 2019-06-30 figure is set aside and the year leaves the record until a filing after its end.
    facts = _facts(
        Revenues=[_year(50.0, 2018, "2019-08-15", "b1")],
        Assets=[_fact(500.0, "2019-06-30", "2019-08-15", "b1"), _fact(520.0, "2019-12-31", "2019-09-03", "b2",
                                                                       form="10-K/A")])
    snapshots = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    rows = _rows(snapshots, 2019)
    assert [(row["known_from"], row["present"]) for row in rows] == [("2019-08-15", True), ("2019-09-03", False)]
    assert rows[0]["total_assets"] == 500.0
    assert rows[1] == {"fiscal_year": 2019, "known_from": "2019-09-03", "known_accn": "b2", "present": False}


def test_a_year_that_leaves_the_record_and_comes_back_gets_a_row_each_time():
    # The tombstone's year again: it leaves the record with the 10-K/A of 2019-09-03 and comes back with the FY2019
    # 10-K filed after its end, which reads the year's own figures.
    facts = _facts(
        Revenues=[_year(50.0, 2018, "2019-08-15", "b1"), _year(60.0, 2019, "2020-02-14", "b3")],
        Assets=[_fact(500.0, "2019-06-30", "2019-08-15", "b1"),
                _fact(520.0, "2019-12-31", "2019-09-03", "b2", form="10-K/A"),
                _fact(530.0, "2019-12-31", "2020-02-14", "b3")])
    snapshots = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    rows = _rows(snapshots, 2019)
    assert [(row["known_from"], row["present"]) for row in rows] == [
        ("2019-08-15", True), ("2019-09-03", False), ("2020-02-14", True)]
    assert rows[1] == {"fiscal_year": 2019, "known_from": "2019-09-03", "known_accn": "b2", "present": False}
    assert (rows[2]["known_accn"], rows[2]["revenue"], rows[2]["total_assets"]) == ("b3", 60.0, 530.0)
    # the year back in the record is the record's year
    record = build_annual_record(XBRLParser(), facts, ticker="X", years_back=None)
    assert (record.periods["2019"]["revenue"], record.periods["2019"]["total_assets"]) == (60.0, 530.0)


def test_same_day_filings_make_one_snapshot():
    # A 10-K and its 10-K/A on one day: both are known that day, in one snapshot.
    facts = _facts(Revenues=[_year(100.0, 2019, D1, "0001-20-000010")],
                   NetIncomeLoss=[_year(10.0, 2019, D1, "0001-20-000011", form="10-K/A")])
    snapshots = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    assert len(snapshots.rows) == 1
    row = snapshots.rows[0]
    assert (row["known_from"], row["known_accn"]) == (D1, "0001-20-000011")
    assert (row["revenue"], row["net_income"]) == (100.0, 10.0)
    assert (row["accn"], row["form"]) == ("0001-20-000011", "10-K/A")
    assert sorted(snapshots.vintages["2019"]) == ["0001-20-000010", "0001-20-000011"]


def test_an_undated_fact_warns():
    facts = _facts(Revenues=[_year(100.0, 2019, D1, "a1"), _year(999.0, 2020, None, "a9")])
    snapshots = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    assert [row["fiscal_year"] for row in snapshots.rows] == [2019]
    assert len(snapshots.warnings) == 1
    assert "X" in snapshots.warnings[0] and "1 fact" in snapshots.warnings[0]
    assert "2020" not in snapshots.vintages


def test_no_undated_fact_no_warning():
    assert build_snapshots(XBRLParser(), _three_10ks(), ticker="X", years_back=None).warnings == []


def _bug120() -> Dict[str, Any]:
    # BUG-120 (code review 11's repro): the FY2016 10-K reports Revenues of $10.0B and contract revenue of $8.0B, its
    # operating lines adding up to $10.0B; the FY2017 10-K restates FY2016's contract revenue to $7.0B, its operating
    # lines adding up to that. Judged on the latest figures, FY2016's Revenues isn't the year's revenue.
    k16, k17 = ("2017-02-15", "0000-17-000001"), ("2018-02-15", "0000-18-000001")
    return _facts(
        Revenues=[_year(10.0e9, 2016, *k16), _year(11.0e9, 2017, *k17)],
        RevenueFromContractWithCustomerExcludingAssessedTax=[
            _year(8.0e9, 2016, *k16), _year(7.0e9, 2016, *k17), _year(11.0e9, 2017, *k17)],
        OperatingIncomeLoss=[_year(2.0e9, 2016, *k16), _year(1.0e9, 2016, *k17), _year(2.0e9, 2017, *k17)],
        OperatingExpenses=[_year(8.0e9, 2016, *k16), _year(6.0e9, 2016, *k17), _year(9.0e9, 2017, *k17)])


def test_a_vintage_reads_no_later_filing():
    snapshots = build_snapshots(XBRLParser(), _bug120(), ticker="X", years_back=None)
    filed_2016 = snapshots.vintages["2016"]["0000-17-000001"]
    assert filed_2016["revenue"] == 10.0e9
    assert filed_2016["_source_tags"]["revenue"] == "Revenues"
    # the later 10-K's own view of FY2016 is decided on its own figures
    assert snapshots.vintages["2016"]["0000-18-000001"]["revenue"] == 7.0e9
    # the as-of record agrees: $10.0B as known on the first 10-K's day
    assert [row["revenue"] for row in _rows(snapshots, 2016)] == [10.0e9, 7.0e9]


def test_a_vintage_of_a_year_not_yet_ended_is_dropped():
    # BUG-113's vintage half: BAC's FY2010 10-K (filed 2011-02-25) tags a dividend for 2011, a year not over when it
    # was filed; that 10-K has no FY2011 view. The FY2011 10-K's view stands.
    facts = _facts(
        Revenues=[_year(100.0, 2010, "2011-02-25", "c10"), _year(110.0, 2011, "2012-02-23", "c11")],
        CommonStockDividendsPerShareDeclared=[_year(0.04, 2011, "2011-02-25", "c10"),
                                              _year(0.04, 2011, "2012-02-23", "c11")])
    snapshots = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    assert sorted(snapshots.vintages["2011"]) == ["c11"]
    assert sorted(snapshots.vintages["2010"]) == ["c10"]
    assert [row["known_from"] for row in _rows(snapshots, 2011)] == ["2012-02-23"]


def test_a_vintage_is_normalised_as_the_fetcher_does():
    # FY2020's count is filed in thousands; its EPS puts it in units in the vintage too, and derivations run.
    snapshots = build_snapshots(XBRLParser(), _three_10ks(), ticker="X", years_back=None)
    vintage = snapshots.vintages["2020"]["a2"]
    assert vintage["weighted_avg_shares_diluted"] == 10.0e6
    assert vintage["accn"] == "a2" and vintage["filed_date"] == D2


def _counts(earlier_checked: bool) -> Dict[str, Any]:
    # FY2020's diluted count, filed in thousands by both its 10-K (e1) and the next year's (e2); one filing's EPS
    # checks it (and share scale puts it in units), the other's doesn't.
    k1, k2 = ("2021-02-12", "e1"), ("2022-02-11", "e2")
    checked, unchecked = (k1, k2) if earlier_checked else (k2, k1)
    return _facts(
        NetIncomeLoss=[_year(10.0e6, 2020, *k1), _year(10.0e6, 2020, *k2)],
        EarningsPerShareDiluted=[_year(1.0, 2020, *checked)],
        WeightedAverageNumberOfDilutedSharesOutstanding=[_year(10.0e3, 2020, *k1), _year(10.0e3, 2020, *k2)])


def test_a_vintages_unchecked_count_takes_the_scale_of_the_views_filed_by_its_day():
    # The later 10-K's count, which no EPS of its own checks, takes the scale the earlier 10-K's checked count was
    # put on, as the run's vintages always have (normalize_share_scale over the year's views).
    vintages = build_snapshots(XBRLParser(), _counts(earlier_checked=True), ticker="X", years_back=None).vintages
    assert vintages["2020"]["e1"]["weighted_avg_shares_diluted"] == 10.0e6
    assert vintages["2020"]["e2"]["weighted_avg_shares_diluted"] == 10.0e6


def test_a_vintages_count_takes_no_scale_from_a_later_filing():
    # Reversed: the later 10-K's EPS checks its count; the earlier 10-K's view can't have known it.
    vintages = build_snapshots(XBRLParser(), _counts(earlier_checked=False), ticker="X", years_back=None).vintages
    assert vintages["2020"]["e1"]["weighted_avg_shares_diluted"] == 10.0e3
    assert vintages["2020"]["e2"]["weighted_avg_shares_diluted"] == 10.0e6


def test_years_back_trims_rows_not_history():
    full = build_snapshots(XBRLParser(), _three_10ks(), ticker="X", years_back=None)
    trimmed = build_snapshots(XBRLParser(), _three_10ks(), ticker="X", years_back=2)
    assert {row["fiscal_year"] for row in trimmed.rows} == {2020, 2021}
    assert trimmed.rows == [row for row in full.rows if row["fiscal_year"] in (2020, 2021)]
    assert sorted(trimmed.vintages) == ["2020", "2021"]


#: The days of ``_unread_days``'s companyfacts: the parse's days (a 10-K, a 10-Q, the next 10-K), and days whose only
#: facts are in taxonomies the record's parse never reads (dei share counts, srt, ifrs-full).
K19, DEI1, Q1, K20, DEI2, SRT, ENDED, DEI3 = ("2020-02-14", "2020-03-01", "2020-05-01", "2021-02-12", "2021-06-01",
                                              "2021-08-01", "2022-01-20", "2022-03-01")


def _unread_days() -> Dict[str, Any]:
    # The FY2020 10-K also tags a dividend for 2021, a year not over on its day (BAC's shape, BUG-113): FY2021 opens
    # the first filing day after 2021-12-31, here a day with only a dei share count (ENDED).
    facts = _facts(
        Revenues=[_year(100.0, 2019, K19, "a1"), _fact(30.0, "2020-03-31", Q1, "q1", start="2020-01-01", form="10-Q"),
                  _year(110.0, 2020, K20, "a2"), _year(100.0, 2019, K20, "a2")],
        NetIncomeLoss=[_year(10.0, 2019, K19, "a1"), _year(11.0, 2020, K20, "a2")],
        CommonStockDividendsPerShareDeclared=[_year(0.5, 2021, K20, "a2")])

    def instant(val: float, day: str, accn: str, form: str) -> Dict[str, Any]:
        return {"val": val, "end": day, "accn": accn, "fy": int(day[:4]), "fp": "Q1", "form": form, "filed": day}

    facts["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"label": "shares", "units": {"shares": [
        instant(9.0e6, K19, "a1", "10-K"), instant(9.1e6, DEI1, "d1", "10-K"),  # a 10-K form, no us-gaap fact
        instant(9.2e6, DEI2, "d2", "10-Q"), instant(9.3e6, ENDED, "d3", "10-Q"), instant(9.4e6, DEI3, "d4", "10-Q")]}}}
    # the same concepts as the us-gaap ones, other figures: a parse that read them would differ
    facts["facts"]["srt"] = {"Revenues": {"label": "r", "units": {"USD": [_year(999.0, 2020, SRT, "s1")]}}}
    facts["facts"]["ifrs-full"] = {"Revenues": {"label": "r", "units": {"USD": [_year(888.0, 2019, SRT, "s1")]}}}
    return facts


def _built_days(monkeypatch, facts: Dict[str, Any]) -> List[str]:
    """The days ``build_snapshots`` builds the record on (each build's facts' latest filed day)."""
    built: List[str] = []

    def spy(parser, known, **kwargs):
        built.append(max(e["filed"] for taxonomy in known["facts"].values() for node in taxonomy.values()
                         for entries in node["units"].values() for e in entries))
        return build_annual_record(parser, known, **kwargs)

    monkeypatch.setattr(annual_snapshots, "build_annual_record", spy)
    build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    return built


def test_the_cached_builder_gives_the_uncached_rows(monkeypatch):
    facts = _unread_days()
    cached = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    monkeypatch.setattr(annual_snapshots, "_SKIP_UNCHANGED_DAYS", False)
    uncached = build_snapshots(XBRLParser(), facts, ticker="X", years_back=None)
    # the fixture holds the cases: FY2021 opens on a day with a dei fact alone, its year now over
    assert [(row["known_from"], row["present"]) for row in _rows(uncached, 2021)] == [(ENDED, True)]
    assert _rows(uncached, 2021)[0]["dividends_per_share"] == 0.5
    assert sorted(uncached.vintages) == ["2019", "2020"]
    assert cached == uncached
    # with years_back, and with an undated fact (its warning)
    facts["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"].append(
        {"val": 1.0, "end": "2020-12-31", "accn": "d9", "form": "10-Q"})
    for years_back in (None, 1, 2):
        uncached = build_snapshots(XBRLParser(), facts, ticker="X", years_back=years_back)
        monkeypatch.setattr(annual_snapshots, "_SKIP_UNCHANGED_DAYS", True)
        assert build_snapshots(XBRLParser(), facts, ticker="X", years_back=years_back) == uncached
        monkeypatch.setattr(annual_snapshots, "_SKIP_UNCHANGED_DAYS", False)
    assert len(uncached.warnings) == 1


def test_a_day_whose_facts_the_record_cant_read_isnt_built(monkeypatch):
    # Built: the first day, each day the parse's taxonomy filed on (the 10-Q's too) and the day FY2021's end has
    # passed; not built: the dei-only days (the 10-K-form one too) and the srt/ifrs-full day.
    assert _built_days(monkeypatch, _unread_days()) == [K19, Q1, K20, ENDED]


def test_the_uncached_builder_builds_every_day(monkeypatch):
    monkeypatch.setattr(annual_snapshots, "_SKIP_UNCHANGED_DAYS", False)
    assert _built_days(monkeypatch, _unread_days()) == [K19, DEI1, Q1, K20, DEI2, SRT, ENDED, DEI3]


def test_the_record_reads_no_taxonomy_but_its_own():
    # The skip's premise (PARSED_TAXONOMY): the record of facts with dei, srt and ifrs-full beside us-gaap is that of
    # us-gaap alone, given the same latest filed day.
    facts = _unread_days()
    alone = {**facts, "facts": {"us-gaap": facts["facts"]["us-gaap"],
                                "dei": {"X": {"units": {"shares": [{"val": 1.0, "end": DEI3, "filed": DEI3}]}}}}}
    assert PARSED_TAXONOMY == "us-gaap"
    assert (build_annual_record(XBRLParser(), facts, ticker="X", years_back=None)
            == build_annual_record(XBRLParser(), alone, ticker="X", years_back=None))
