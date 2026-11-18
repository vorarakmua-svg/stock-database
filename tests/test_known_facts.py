from typing import Any, Dict

from src.parsers.known_facts import facts_known_by, filing_dates, undated_facts


def _facts(*rows: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"cik": 1, "entityName": "X", "facts": {}}
    for tax, tag, filed, val in rows:
        entry: Dict[str, Any] = {"val": val, "end": "2019-12-31", "accn": "a"}
        if filed is not None:
            entry["filed"] = filed
        node = out["facts"].setdefault(tax, {}).setdefault(
            tag, {"label": tag, "units": {"USD": []}}
        )
        node["units"]["USD"].append(entry)
    return out


def test_a_fact_filed_on_the_day_is_known_and_one_filed_after_is_not():
    facts = _facts(("us-gaap", "Revenues", "2020-02-01", 10.0), ("us-gaap", "Revenues", "2021-02-01", 11.0))
    known = facts_known_by(facts, "2020-02-01")
    assert [e["val"] for e in known["facts"]["us-gaap"]["Revenues"]["units"]["USD"]] == [10.0]
    assert known["cik"] == 1 and known["facts"]["us-gaap"]["Revenues"]["label"] == "Revenues"
    assert len(facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]) == 2


def test_every_taxonomy_is_gated_and_kept():
    facts = _facts(("dei", "EntityCommonStockSharesOutstanding", "2020-02-01", 5.0),
                   ("ifrs-full", "Revenue", "2021-02-01", 1.0))
    known = facts_known_by(facts, "2020-06-30")
    assert "dei" in known["facts"] and "ifrs-full" not in known["facts"]


def test_filing_dates_are_distinct_and_ascending_across_taxonomies():
    facts = _facts(("us-gaap", "A", "2021-02-01", 1.0), ("dei", "B", "2020-02-01", 1.0),
                   ("us-gaap", "C", "2021-02-01", 2.0))
    assert filing_dates(facts) == ["2020-02-01", "2021-02-01"]


def test_an_undated_fact_is_never_known_and_is_counted():
    facts = _facts(("us-gaap", "Revenues", None, 10.0))
    assert facts_known_by(facts, "2099-12-31")["facts"] == {} and undated_facts(facts) == 1
    assert filing_dates(facts) == []
