"""The annual record as it was known on each filing date: one replay of the record per date, a row per change."""

import math
from typing import Any, Dict, List, Optional

from src.mappings.canonical import CANONICAL_BY_KEY
from src.parsers.annual_record import build_annual_record
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


def test_years_back_trims_rows_not_history():
    full = build_snapshots(XBRLParser(), _three_10ks(), ticker="X", years_back=None)
    trimmed = build_snapshots(XBRLParser(), _three_10ks(), ticker="X", years_back=2)
    assert {row["fiscal_year"] for row in trimmed.rows} == {2020, 2021}
    assert trimmed.rows == [row for row in full.rows if row["fiscal_year"] in (2020, 2021)]
    assert sorted(trimmed.vintages) == ["2020", "2021"]
