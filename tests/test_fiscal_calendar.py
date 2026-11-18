"""Tests for SEC-frame-based calendar-year alignment across fiscal year-ends."""

import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


@pytest.fixture
def parser():
    return XBRLParser()


# ---------------- frame parsing & fallback ----------------

def test_parse_frame(parser):
    assert parser._parse_frame("CY2025") == (2025, None)
    assert parser._parse_frame("CY2024Q4I") == (2024, 4)
    assert parser._parse_frame("CY2024Q1") == (2024, 1)
    assert parser._parse_frame("") == (None, None)
    assert parser._parse_frame("garbage") == (None, None)


def test_calendar_year_from_end_fallback(parser):
    # Jan-May -> prior calendar year; Jun-Dec -> end year.
    assert parser._calendar_year_from_end("2026-01-31") == 2025  # Walmart-style
    assert parser._calendar_year_from_end("2025-06-30") == 2025  # Microsoft-style
    assert parser._calendar_year_from_end("2024-09-28") == 2024  # Apple-style
    assert parser._calendar_year_from_end("2025-12-31") == 2025  # Dec filer
    assert parser._calendar_year_from_end(None) is None


def _facts_annual(entries):
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


# ---------------- parser integration ----------------

def test_january_filer_calendar_year_from_frame(parser):
    # Walmart-style: fiscal year ends Jan 2026 but macro year is 2025; SEC frame=CY2025.
    facts = _facts_annual([
        usd(648000, "2025-02-01", "2026-01-31", fy=2026, filed="2026-03-15", frame="CY2025"),
    ])
    annual = parser.extract_annual_financials(facts, years_back=1)
    period = annual["2026"]
    assert period["fiscal_year"] == 2026          # company's own label (end-year bucket)
    assert period["calendar_year"] == 2025        # SEC-aligned macro year
    assert period["frame"] == "CY2025"


def test_calendar_year_falls_back_without_frame(parser):
    facts = _facts_annual([
        usd(100, "2025-02-01", "2026-01-31", fy=2026, filed="2026-03-15"),  # no frame
    ])
    period = parser.extract_annual_financials(facts, years_back=1)["2026"]
    assert period["calendar_year"] == 2025  # via end-month fallback


def test_june_and_sep_filers_align_to_same_calendar_year(parser):
    msft = parser.extract_annual_financials(_facts_annual([
        usd(1, "2024-07-01", "2025-06-30", fy=2025, filed="2025-07-30", frame="CY2025"),
    ]), years_back=1)["2025"]
    aapl = parser.extract_annual_financials(_facts_annual([
        usd(1, "2024-09-29", "2025-09-27", fy=2025, filed="2025-11-01", frame="CY2025"),
    ]), years_back=1)["2025"]
    assert msft["calendar_year"] == aapl["calendar_year"] == 2025


def test_quarterly_calendar_quarter(parser):
    # A cumulative ladder (Q1 then H1) sharing the fiscal-year start.
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        usd(25, "2025-02-01", "2025-05-01", fy=2026, fp="Q1", form="10-Q",
            filed="2025-05-20", frame="CY2025Q1"),
        usd(55, "2025-02-01", "2025-07-31", fy=2026, fp="Q2", form="10-Q",
            filed="2025-08-20"),
    ]}}}}}
    q = parser.extract_quarterly_financials(facts)
    period = q["2025-05-01"]                 # discrete Q1
    assert period["net_income"] == 25
    assert period["calendar_year"] == 2025
    assert period["calendar_quarter"] == 1   # from frame CY2025Q1
    assert q["2025-07-31"]["net_income"] == 30  # 55 (H1) - 25 (Q1)


# ---------------- which frame a period takes (BUG-057, BUG-123, BUG-277) ----------------
# A period's frames used to be a set, and the first one parsed won, so with no deciding frame the calendar year
# followed the process's string hash. Each shape below is a real filer's, cut down.

def _facts(by_tag):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": entries}} for tag, entries in by_tag.items()}}}


def _vintage(facts, fy):
    (period,) = XBRLParser().extract_annual_vintages(facts)[fy].values()
    return period


def _wmt_fy2012_vintage():
    # WMT's FY2012 10-K (filed 2012-03-27): the year's figures carry no full-year frame (SEC put CY2011 on a later
    # 10-K's copy), its balance sheet carries CY2011Q4I, and a stray cash instant at 2012-12-31 CY2012Q4I.
    filed = "2012-03-27"
    return _vintage(_facts({
        "Revenues": [usd(443854, "2011-02-01", "2012-01-31", fy=2012, filed=filed)],
        "NetIncomeLoss": [usd(15699, "2011-02-01", "2012-01-31", fy=2012, filed=filed)],
        "Assets": [usd(193406, None, "2012-01-31", fy=2012, filed=filed, frame="CY2011Q4I")],
        "CashAndCashEquivalentsAtCarryingValue": [
            usd(6550, None, "2012-01-31", fy=2012, filed=filed, frame="CY2011Q4I"),
            usd(7781, None, "2012-12-31", fy=2012, filed=filed, frame="CY2012Q4I"),
        ],
    }), "2012")


def _tjx_fy2012_vintage():
    # TJX's FY2012 (to 2012-01-28) as its 2015 10-K holds it: instants CY2011Q4I, a stray CY2012Q4I.
    filed = "2015-03-31"
    return _vintage(_facts({
        "Revenues": [usd(23191, "2011-01-30", "2012-01-28", fy=2015, filed=filed)],
        "Assets": [usd(8282, None, "2012-01-28", fy=2015, filed=filed, frame="CY2011Q4I")],
        "CashAndCashEquivalentsAtCarryingValue": [
            usd(1507, None, "2012-01-28", fy=2015, filed=filed, frame="CY2011Q4I"),
            usd(1812, None, "2012-12-29", fy=2015, filed=filed, frame="CY2012Q4I"),
        ],
    }), "2012")


def _gpn_fy2016():
    # GPN's year key 2016 after its move to December years: the cash flows of its year to 2016-05-31 (CY2015), the
    # income statement of calendar 2016 (CY2016) and the balance sheet at 2016-12-31. More of its flow figures are
    # the May year's, so that is the year it is; a balance sheet doesn't count.
    filed = "2019-02-21"
    may, dec = ("2015-06-01", "2016-05-31"), ("2016-01-01", "2016-12-31")
    return XBRLParser().extract_annual_financials(_facts({
        "NetCashProvidedByUsedInOperatingActivities": [usd(592, *may, fy=2018, filed=filed, frame="CY2015")],
        "NetCashProvidedByUsedInInvestingActivities": [usd(-2127, *may, fy=2018, filed=filed, frame="CY2015")],
        "PaymentsToAcquirePropertyPlantAndEquipment": [usd(91, *may, fy=2018, filed=filed, frame="CY2015")],
        "Revenues": [usd(3370, *dec, fy=2018, filed=filed, frame="CY2016")],
        "NetIncomeLoss": [usd(201, *dec, fy=2018, filed=filed, frame="CY2016")],
        "Assets": [usd(10664, None, "2016-12-31", fy=2018, filed=filed, frame="CY2016Q4I")],
        "Liabilities": [usd(7885, None, "2016-12-31", fy=2018, filed=filed, frame="CY2016Q4I")],
        "StockholdersEquity": [usd(2630, None, "2016-12-31", fy=2018, filed=filed, frame="CY2016Q4I")],
    }))["2016"]


def _lrcx_fy2012():
    # LRCX's year to 2012-06-24 (CY2011) whose record's period_end a later-filed figure at 2012-06-30 sets.
    filed, year = "2012-08-21", ("2011-06-27", "2012-06-24")
    return XBRLParser().extract_annual_financials(_facts({
        "Revenues": [usd(2665, *year, fy=2012, filed=filed, frame="CY2011")],
        "NetIncomeLoss": [usd(168, *year, fy=2012, filed=filed, frame="CY2011")],
        "Assets": [usd(7252, None, "2012-06-24", fy=2012, filed=filed)],
        "Liabilities": [usd(3412, None, "2012-06-30", fy=2013, filed="2013-08-20", frame="CY2012Q2I")],
    }))["2012"]


def _lhx_fy2019():
    # LHX's year key 2019: its June year to 2019-06-28 (CY2018) as the 10-K of 2019 filed it, and the year to
    # 2020-01-03 (CY2019), filed later; one flow figure each, so the later end's.
    return XBRLParser().extract_annual_financials(_facts({
        "Revenues": [usd(6801, "2018-06-30", "2019-06-28", fy=2019, filed="2019-08-22", frame="CY2018"),
                     usd(12856, "2018-12-29", "2020-01-03", fy=2020, filed="2021-03-01", frame="CY2019")],
        "NetCashProvidedByUsedInOperatingActivities": [
            usd(1185, "2018-06-30", "2019-06-28", fy=2019, filed="2019-08-22", frame="CY2018")],
    }))["2019"]


def _lhx_fy2019_vintage():
    # LHX's FY2020 10-K: the year to 2020-01-03 (CY2019) is its FY2019 figures; a June-year figure comes first,
    # so the vintage's period_end is 2019-06-28, and its equity statement opens at 2019-06-28.
    filed = "2021-03-01"
    return _vintage(_facts({
        "Revenues": [usd(6801, "2018-06-30", "2019-06-28", fy=2020, filed=filed, frame="CY2018"),
                     usd(12856, "2018-12-29", "2020-01-03", fy=2020, filed=filed, frame="CY2019")],
        "NetIncomeLoss": [usd(1333, "2018-12-29", "2020-01-03", fy=2020, filed=filed, frame="CY2019")],
        "StockholdersEquity": [usd(10400, None, "2019-06-28", fy=2020, filed=filed)],
        "RetainedEarningsAccumulatedDeficit": [usd(5100, None, "2019-06-28", fy=2020, filed=filed)],
        "AdditionalPaidInCapital": [usd(900, None, "2019-06-28", fy=2020, filed=filed)],
    }), "2019")


def _exe_fy2021():
    # EXE's year key 2021 after its fresh start: balance sheets alone, at 2021-02-09 (CY2020Q4I) and, filed later,
    # 2021-12-31 (CY2021Q4I), the record's period_end.
    return XBRLParser().extract_annual_financials(_facts({
        "Assets": [usd(6814, None, "2021-02-09", fy=2023, filed="2024-02-21", frame="CY2020Q4I")],
        "Liabilities": [usd(3228, None, "2021-02-09", fy=2023, filed="2024-02-21", frame="CY2020Q4I")],
        "StockholdersEquity": [usd(5671, None, "2021-12-31", fy=2024, filed="2025-02-26", frame="CY2021Q4I")],
    }))["2021"]


def _tel_fy2022():
    # TEL's FY2022 (to 2022-09-30, CY2022) beside one 52-week figure to 2022-03-25 (CY2021).
    filed = "2022-11-15"
    return XBRLParser().extract_annual_financials(_facts({
        "Revenues": [usd(16281, "2021-09-25", "2022-09-30", fy=2022, filed=filed, frame="CY2022")],
        "OperatingIncomeLoss": [usd(12, "2021-03-27", "2022-03-25", fy=2022, filed=filed, frame="CY2021")],
        "NetIncomeLoss": [usd(2428, "2021-09-25", "2022-09-30", fy=2022, filed=filed, frame="CY2022")],
    }))["2022"]


def _tel_fy2022_vintage():
    # The same year as the 10-K of 2024 holds it: SEC put the year's frame on another filing's copy, the stray
    # figure's is here.
    filed = "2024-11-12"
    return _vintage(_facts({
        "Revenues": [usd(16281, "2021-09-25", "2022-09-30", fy=2024, filed=filed)],
        "NetIncomeLoss": [usd(2428, "2021-09-25", "2022-09-30", fy=2024, filed=filed)],
        "OperatingIncomeLoss": [usd(12, "2021-03-27", "2022-03-25", fy=2024, filed=filed, frame="CY2021")],
    }), "2022")


def _clash_fy2016():
    # Two full-year frames at one end (none of the cached files has such a pair): the first in sort order.
    filed = "2016-07-28"
    return XBRLParser().extract_annual_financials(_facts({
        "Revenues": [usd(10, "2015-06-01", "2016-05-31", fy=2016, filed=filed, frame="CY2016")],
        "NetIncomeLoss": [usd(1, "2015-06-01", "2016-05-31", fy=2016, filed=filed, frame="CY2015")],
    }))["2016"]


def _june_fy2019():
    # Seagate-like 52-week year to 2019-06-28 without a full-year frame: SEC's rule gives CY2018 (end less 180 days).
    filed = "2019-08-02"
    return XBRLParser().extract_annual_financials(_facts({
        "Revenues": [usd(10390, "2018-06-30", "2019-06-28", fy=2019, filed=filed)],
    }))["2019"]


def _march_fy2012():
    # A March year without a full-year frame: its year-end instant is CY2012Q1I, but SEC's frame for the year
    # (April 2011 to March 2012) is CY2011.
    filed = "2012-05-20"
    return XBRLParser().extract_annual_financials(_facts({
        "Revenues": [usd(500, "2011-04-01", "2012-03-31", fy=2012, filed=filed)],
        "Assets": [usd(900, None, "2012-03-31", fy=2012, filed=filed, frame="CY2012Q1I")],
    }))["2012"]


def _quarters():
    # A December quarter framed both as a quarter (CY2024Q4) and at its end (CY2024Q4I); a March year's fourth
    # quarter whose only frame is the year's (CY2024); a 52-week filer's quarter to 2024-10-05 with no frame.
    return XBRLParser().extract_quarterly_financials(_facts({
        "Revenues": [
            usd(100, "2024-10-01", "2024-12-31", fy=2024, fp="Q4", form="10-K", filed="2025-02-10", frame="CY2024Q4"),
            usd(300, "2024-04-01", "2024-12-31", fy=2025, fp="Q3", form="10-Q", filed="2025-02-01"),
            usd(420, "2024-04-01", "2025-03-31", fy=2025, fp="FY", form="10-K", filed="2025-05-20", frame="CY2024"),
            usd(50, "2024-07-07", "2024-10-05", fy=2024, fp="Q3", form="10-Q", filed="2024-11-01"),
        ],
        "Assets": [usd(900, None, "2024-12-31", fy=2024, fp="FY", form="10-K", filed="2025-02-10", frame="CY2024Q4I"),
                   usd(880, None, "2024-10-05", fy=2024, fp="Q3", form="10-Q", filed="2024-11-01")],
    }))


def _calendars():
    """Every shape's calendar cells, as one JSON-able dict (run in-process and under other hash seeds)."""
    q = _quarters()
    shapes = {"wmt": _wmt_fy2012_vintage(), "tjx": _tjx_fy2012_vintage(), "gpn": _gpn_fy2016(), "tel": _tel_fy2022(),
              "tel_v": _tel_fy2022_vintage(), "lhx": _lhx_fy2019(), "lhx_v": _lhx_fy2019_vintage(),
              "lrcx": _lrcx_fy2012(), "exe": _exe_fy2021(), "clash": _clash_fy2016(),
              "march": _march_fy2012(), "june": _june_fy2019(),
              "q_dec": q["2024-12-31"], "q_march_q4": q["2025-03-31"], "q_unframed": q["2024-10-05"]}
    return {name: [p.get("calendar_year"), p.get("calendar_quarter"), p.get("frame")] for name, p in shapes.items()}


def test_a_january_years_vintage_takes_the_years_frame_not_a_stray_instants():
    assert _calendars()["wmt"][::2] == [2011, "CY2011"]
    assert _calendars()["tjx"][::2] == [2011, "CY2011"]


def test_a_year_takes_the_frame_of_the_end_its_flow_figures_share():
    cal = _calendars()
    assert cal["gpn"][::2] == [2015, "CY2015"]
    assert cal["lrcx"][::2] == [2011, "CY2011"]
    assert cal["lhx"][::2] == [2019, "CY2019"]
    assert cal["lhx_v"][::2] == [2019, "CY2019"]
    assert cal["exe"][::2] == [2021, "CY2021"]
    assert cal["tel"][::2] == [2022, "CY2022"]
    assert cal["tel_v"][::2] == [2022, "CY2022"]
    assert cal["clash"][::2] == [2015, "CY2015"]


def test_a_year_without_a_full_year_frame_takes_secs_rule_not_its_year_end_instants():
    cal = _calendars()
    assert cal["march"][::2] == [2011, "CY2011"]
    assert cal["june"][::2] == [2018, "CY2018"]


def test_a_quarter_takes_its_quarter_frame_and_secs_rule_without_one():
    cal = _calendars()
    assert cal["q_dec"] == [2024, 4, "CY2024Q4"]
    assert cal["q_march_q4"] == [2025, 1, "CY2025Q1"]
    assert cal["q_unframed"] == [2024, 3, "CY2024Q3"]


def test_the_calendar_is_the_same_under_every_hash_seed():
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    code = "import json; from tests.test_fiscal_calendar import _calendars; print(json.dumps(_calendars()))"
    seen = {}
    for seed in ("0", "1", "2", "3", "4", "5", "6", "7"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", code], cwd=str(root), env=env, check=True,
                             capture_output=True, text=True, timeout=120).stdout
        seen[seed] = json.loads(out.strip().splitlines()[-1])
    assert all(cells == seen["0"] for cells in seen.values()), seen
