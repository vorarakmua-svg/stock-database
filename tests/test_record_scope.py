"""One rule for what a run's SEC record replaces, in every output (BUG-380).

Since BUG-344 the database removes a ticker's periods and unmapped tags that a complete run no longer gives; the JSON
file and financial_history.csv merged by period and kept them, so the three outputs disagreed (MATCH-05). Each case
here collects the same two runs into all three and asks each for the same periods.
"""
import csv
import json

import pytest

from src.exporters.csv_exporter import CSVExporter
from src.exporters.json_exporter import JSONExporter
from src.exporters.sqlite_store import SQLiteStore
from tests.test_sqlite_store import (
    _held,
    _no_annual_record,
    _predecessor_unread,
    _record,
    _report_unread,
    _sec_failed,
    _submissions_failed,
)


def _outputs(tmp_path, *runs):
    """Collect *runs* in turn into a database, the JSON files and financial_history.csv, as a collection does; give
    each output's stored keys of AAA's record: the database's (``_held``), the JSON file's and the CSV's years."""
    db = tmp_path / "stock.db"
    exports = tmp_path / "json"
    for run in runs:
        SQLiteStore(db).export([run])
        JSONExporter(exports).export(run)
        CSVExporter(tmp_path).export_financial_history([run])
    with open(exports / "AAA.json", encoding="utf-8") as f:
        record = json.load(f)
    as_json = {"annual": sorted(int(y) for y in record.get("financials_annual") or {}),
               "quarterly": sorted(record.get("financials_quarterly") or {}),
               "ttm": sorted(record.get("financials_ttm") or {}),
               "unmapped": sorted(fact["tag"] for fact in record.get("unmapped_facts") or [])}
    with open(tmp_path / "financial_history.csv", newline="", encoding="utf-8") as f:
        as_csv = sorted(int(row["fiscal_year"]) for row in csv.DictReader(f) if row["ticker"] == "AAA")
    return _held(db), as_json, as_csv


def _agree(held, as_json, as_csv, expected):
    assert held == expected
    assert as_json == {key: expected[key] for key in ("annual", "quarterly", "ttm", "unmapped")}
    assert as_csv == expected["annual"]


def _first():
    return _record([2009, 2010, 2011], ["2011-01-29", "2011-04-30", "2011-07-30"],
                   ["2011-04-30", "2011-07-30", "2011-10-29"], ["Aaa", "Bbb"])


def test_a_complete_recollection_removes_the_same_periods_from_every_output(tmp_path):
    # BUG-380: the JSON file and the CSV kept FY2009, the quarter and the trailing years and the tag the run no longer
    # gives, which the database removed (BUG-344).
    second = _record([2010, 2011], ["2011-01-29", "2011-10-29"], ["2011-10-29"], ["Bbb", "Ccc"])
    _agree(*_outputs(tmp_path, _first(), second),
           {"annual": [2010, 2011], "metrics": [2010, 2011], "quarterly": ["2011-01-29", "2011-10-29"],
            "ttm": ["2011-10-29"], "unmapped": ["Bbb", "Ccc"]})


def test_a_complete_run_without_quarters_or_tags_removes_them_from_every_output(tmp_path):
    # The JSON merge kept a section the run left empty (BUG-030): a complete run that gives none speaks for none.
    _agree(*_outputs(tmp_path, _first(), _record([2011])),
           {"annual": [2011], "metrics": [2011], "quarterly": [], "ttm": [], "unmapped": []})


def test_a_years_back_run_removes_only_within_its_years_in_every_output(tmp_path):
    first = _record([2019, 2020, 2021], ["2019-12-31", "2020-06-30", "2020-09-30"],
                    ["2019-12-31", "2020-09-30", "2021-03-31"], ["Aaa"])
    second = _record([2020, 2022], ["2020-03-31", "2020-12-31"], ["2020-12-31"], ["Bbb"], years_back=2)
    held, as_json, as_csv = _outputs(tmp_path, first, second)
    _agree(held, as_json, as_csv,
           {"annual": [2019, 2020, 2022], "metrics": [2019, 2020, 2022],
            "quarterly": ["2019-12-31", "2020-03-31", "2020-12-31"],
            "ttm": ["2019-12-31", "2020-09-30", "2020-12-31"], "unmapped": ["Bbb"]})


def test_a_years_back_run_without_quarters_keeps_them_in_every_output(tmp_path):
    first = _record([2019, 2020, 2021], ["2020-06-30"], ["2020-06-30"])
    _agree(*_outputs(tmp_path, first, _record([2020], years_back=1)),
           {"annual": [2019, 2020], "metrics": [2019, 2020], "quarterly": ["2020-06-30"], "ttm": ["2020-06-30"],
            "unmapped": []})


@pytest.mark.parametrize("partial", [_predecessor_unread, _report_unread, _submissions_failed, _sec_failed,
                                     _no_annual_record])
def test_a_run_that_cant_speak_for_the_whole_record_removes_nothing_from_any_output(tmp_path, partial):
    stored = _record([2010, 2011], ["2011-04-30", "2011-07-30"], ["2011-04-30", "2011-07-30"], ["Aaa"])
    run = _record([2011], ["2011-07-30"])
    partial(run)
    _agree(*_outputs(tmp_path, stored, run),
           {"annual": [2010, 2011], "metrics": [2010, 2011], "quarterly": ["2011-04-30", "2011-07-30"],
            "ttm": ["2011-04-30", "2011-07-30"], "unmapped": ["Aaa"]})


def test_a_run_whose_quarters_werent_built_keeps_them_in_every_output(tmp_path):
    stored = _record([2010, 2011], ["2011-04-30", "2011-07-30"], ["2011-04-30", "2011-07-30"], ["Aaa"])
    run = _record([2011], ["2011-07-30"])
    run.unbuilt = ["quarterly"]
    _agree(*_outputs(tmp_path, stored, run),
           {"annual": [2011], "metrics": [2011], "quarterly": ["2011-04-30", "2011-07-30"],
            "ttm": ["2011-04-30", "2011-07-30"], "unmapped": []})


def test_absent_compares_a_year_however_an_output_keeps_it():
    # The database keeps fiscal years as integers, the JSON file and the CSV as text: each gets its own keys back.
    from src.exporters.record_scope import absent
    run = _record([2010, 2011])
    assert absent(run, "financials_annual", [2009, 2010]) == [2009]
    assert absent(run, "financials_annual", ["2009", "2010", "2011"]) == ["2009"]
    assert absent(run, "unmapped_facts", ["Aaa"]) == ["Aaa"]
