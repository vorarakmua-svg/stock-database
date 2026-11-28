"""BUG-390: a quarter BUG-275 spread onto its year's 10-K's D&A basis (derived:amortization_spread) holds a share of an
amortization only that 10-K tags, so a trailing year holding one is known in the valuation history from that 10-K's
first filing day; before it the history reads the trailing years known before. The quarters of a year with no 10-K
yet that carry the last 10-K's difference (derived:amortization_carried) read only a 10-K filed before them, and keep
their lag.

BUG-395: the 10-K that supplies the spread is the first of the year's 10-Ks to give the year every D&A figure the
spread read, not merely the year's first 10-K: AVGO's FY2024 amortization was first tagged by its 10-K of 2025-12-18,
SWK's FY2020 line by its 10-K of 2023-02-23. The parser names it in each spread quarter's _source_filings, the store
keeps it (financials_quarterly.source_filings), the history dates the trailing years by it. A quarter stored before
that column is dated by its year's first 10-K, as BUG-390 dated it."""
import json
import sqlite3
from datetime import date

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.parsers.known_facts import facts_known_by
from src.parsers.ttm import compute_ttm
from src.parsers.xbrl_parser import XBRLParser
from src.valuation.history import compute_history
from src.valuation.history_inputs import known_on, load_history_inputs
from tests.conftest import usd

from .test_valuation_history_inputs import _db

SPREAD, CARRIED = "derived:amortization_spread", "derived:amortization_carried"


def _tag(conn, ends, tag=SPREAD):
    for end in ends:
        conn.execute("UPDATE financials_quarterly SET source_tags = ? WHERE ticker = 'AAA' AND period_end = ?",
                     (json.dumps({"depreciation_amortization": tag, "revenue": "Revenues"}), end))
    conn.commit()


def _known(conn):
    return {p.period_end: p.known_on for p in load_history_inputs(conn, "AAA").points}


# ---- known_on -----------------------------------------------------------------------------------------------------

def test_a_trailing_year_holding_a_spread_quarter_is_known_on_its_10ks_first_filing_day():
    assert known_on("2024-08-04", 3, None, date(2026, 11, 20), "2025-12-18") == date(2025, 12, 18)
    # a fourth quarter whose 10-K came after the window is known on it too, not after the lag
    assert known_on("2024-11-03", 4, "2025-12-18", date(2026, 11, 20), "2025-12-18") == date(2025, 12, 18)


def test_a_spread_10k_filed_before_the_lag_moves_nothing():
    assert known_on("2025-03-31", 1, None, date(2026, 2, 25), "2025-02-20") == date(2025, 5, 15)


def test_the_collection_still_caps_a_spread_trailing_year():
    assert known_on("2024-08-04", 3, None, date(2025, 12, 14), "2025-12-18") == date(2025, 12, 12)  # Sunday: Friday


# ---- load_history_inputs ------------------------------------------------------------------------------------------

def test_a_spread_years_trailing_years_are_known_on_its_10ks_first_filing_day(tmp_path):
    # FY2025's 10-K (first filed 2026-02-19) holds an amortization its 10-Qs left out: Q1-Q3 hold their share of it
    conn = _db(tmp_path)
    before = _known(conn)
    assert (before["2025-03-31"], before["2025-06-30"], before["2025-09-30"]) == (
        date(2025, 5, 15), date(2025, 8, 14), date(2025, 11, 14))
    _tag(conn, ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"])
    known = _known(conn)
    assert (known["2025-03-31"], known["2025-06-30"], known["2025-09-30"]) == (date(2026, 2, 19),) * 3
    assert known["2025-12-31"] == date(2026, 2, 19)
    assert known["2024-12-31"] == date(2025, 2, 20)


def test_before_the_10k_the_history_reads_the_trailing_year_known_before(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', '2025-06-30', 15.0)")
    conn.commit()
    rows = {r.date: r for r in compute_history(load_history_inputs(conn, "AAA")).rows}
    assert rows["2025-06-30"].ttm_period_end == "2025-03-31"
    _tag(conn, ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"])
    rows = {r.date: r for r in compute_history(load_history_inputs(conn, "AAA")).rows}
    assert rows["2025-06-30"].ttm_period_end == "2024-12-31"
    assert rows["2026-02-24"].ttm_period_end == "2025-12-31"


def test_a_trailing_year_after_the_spread_year_keeps_its_lag(tmp_path):
    # FY2024's quarters spread by its 10-K of 2025-02-20: the trailing year to 2025-03-31 holds two of them, known
    # by its own 10-Q's lag anyway
    conn = _db(tmp_path)
    _tag(conn, ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"])
    known = _known(conn)
    assert known["2024-12-31"] == date(2025, 2, 20)
    assert known["2025-03-31"] == date(2025, 5, 15)


def test_a_trailing_year_over_two_spread_years_waits_for_the_later_10k(tmp_path):
    # the trailing year to 2025-03-31 holds FY2024's last three quarters (its 10-K of 2025-02-20) and FY2025's first
    # (its 10-K of 2026-02-19)
    conn = _db(tmp_path)
    _tag(conn, ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"])
    assert _known(conn)["2025-03-31"] == date(2026, 2, 19)


def test_only_the_trailing_years_holding_the_quarter_wait_for_its_10k(tmp_path):
    # a late 10-K (first filed 2025-12-18) for FY2024, whose first quarter alone is spread: the trailing year to
    # 2025-03-31 holds 2024-06-30 on, not 2024-03-31, so it keeps its lag
    conn = _db(tmp_path)
    conn.execute("UPDATE financials_annual_vintages SET filed_date = '2025-12-18' WHERE ticker = 'AAA' "
                 "AND accn = 'acc-2024'")
    _tag(conn, ["2024-03-31"])
    known = _known(conn)
    assert known["2024-12-31"] == date(2025, 12, 18)
    assert known["2025-03-31"] == date(2025, 5, 15)


def test_a_spread_quarter_no_10k_dates_is_known_on_no_day(tmp_path):
    conn = _db(tmp_path)
    conn.execute("DELETE FROM financials_annual_vintages WHERE ticker = 'AAA' AND fiscal_year = 2025")
    # a later year's 10-K isn't the quarter's
    conn.execute("INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn, filed_date, period_end, form) "
                 "VALUES ('AAA', 2026, 'acc-2026', '2027-02-18', '2026-12-31', '10-K')")
    _tag(conn, ["2025-03-31"])
    known = _known(conn)
    assert "2025-03-31" not in known and "2025-12-31" not in known
    assert known["2024-12-31"] == date(2025, 2, 20)


def _supplied_by(conn, ends, filed, accn="acc-x"):
    for end in ends:
        conn.execute("UPDATE financials_quarterly SET source_filings = ? WHERE ticker = 'AAA' AND period_end = ?",
                     (json.dumps({"depreciation_amortization": {"accn": accn, "filed": filed, "form": "10-K"}}), end))
    conn.commit()


def test_a_spread_quarter_is_known_from_the_10k_that_supplies_it(tmp_path):
    # FY2024's spread read the 10-K of 2026-02-19, which first tagged its amortization, not its first of 2025-02-20
    conn = _db(tmp_path)
    year = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
    _tag(conn, year)
    _supplied_by(conn, year, "2026-02-19")
    known = _known(conn)
    assert known["2024-12-31"] == date(2026, 2, 19)
    assert known["2025-03-31"] == date(2026, 2, 19)  # holds FY2024's last three quarters
    # a supplying 10-K that is the year's first moves nothing
    _supplied_by(conn, year, "2025-02-20")
    known = _known(conn)
    assert (known["2024-12-31"], known["2025-03-31"]) == (date(2025, 2, 20), date(2025, 5, 15))


def test_a_spread_quarter_naming_no_filing_is_dated_by_its_years_first_10k(tmp_path):
    conn = _db(tmp_path)
    year = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
    _tag(conn, year)
    _supplied_by(conn, year, "2026-02-19")
    for filings in (None, "not json", json.dumps({"revenue": {"filed": "2026-02-19"}}),
                    json.dumps({"depreciation_amortization": {"filed": "someday"}})):
        conn.execute("UPDATE financials_quarterly SET source_filings = ? WHERE ticker = 'AAA'", (filings,))
        conn.commit()
        assert _known(conn)["2024-12-31"] == date(2025, 2, 20), filings
    conn.execute("ALTER TABLE financials_quarterly DROP COLUMN source_filings")
    conn.commit()
    assert _known(conn)["2024-12-31"] == date(2025, 2, 20)


def test_a_carried_quarter_keeps_its_lag(tmp_path):
    conn = _db(tmp_path)
    _tag(conn, ["2025-03-31", "2025-06-30"], CARRIED)
    known = _known(conn)
    assert (known["2025-03-31"], known["2025-06-30"]) == (date(2025, 5, 15), date(2025, 8, 14))


def test_another_figures_derived_tag_moves_nothing(tmp_path):
    conn = _db(tmp_path)
    conn.execute("UPDATE financials_quarterly SET source_tags = ? WHERE period_end = '2025-03-31'",
                 (json.dumps({"depreciation_amortization": "Depreciation", "revenue": SPREAD}),))
    conn.commit()
    assert _known(conn)["2025-03-31"] == date(2025, 5, 15)


def test_a_database_from_before_the_column_reads_as_before(tmp_path):
    conn = _db(tmp_path)
    expected = _known(conn)
    conn.execute("ALTER TABLE financials_quarterly DROP COLUMN source_tags")
    conn.commit()
    assert _known(conn) == expected


# ---- the store ----------------------------------------------------------------------------------------------------

def _quarter_tags(path, end):
    conn = sqlite3.connect(path)
    try:
        return json.loads(conn.execute("SELECT source_tags FROM financials_quarterly WHERE period_end = ?",
                                       (end,)).fetchone()[0])
    finally:
        conn.close()


def test_the_store_keeps_the_filing_that_supplies_a_spread_quarter(tmp_path):
    supplier = {"accn": "0001730168-25-000121", "filed": "2025-12-18", "form": "10-K"}
    s = StockData(ticker="AAA", cik="000", company_name="A Inc.")
    s.financials_quarterly = {
        "2024-08-04": {"fiscal_year": 2024, "fiscal_quarter": 3, "revenue": 1.0e10, "depreciation_amortization": 2.4e9,
                       "_source_tags": {"revenue": "Revenues", "depreciation_amortization": SPREAD},
                       "_source_filings": {"depreciation_amortization": supplier}},
        "2025-02-02": {"fiscal_year": 2025, "fiscal_quarter": 1, "revenue": 1.0e10,
                       "_source_tags": {"revenue": "Revenues"}}}
    s.add_source("sec_edgar")
    SQLiteStore(tmp_path / "stock.db").export([s])
    conn = sqlite3.connect(tmp_path / "stock.db")
    rows = dict(conn.execute("SELECT period_end, source_filings FROM financials_quarterly").fetchall())
    conn.close()
    assert json.loads(rows["2024-08-04"]) == {"depreciation_amortization": supplier}
    assert rows["2025-02-02"] is None


def test_a_quarterly_table_without_source_filings_gains_it(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "old.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("ALTER TABLE financials_quarterly DROP COLUMN source_filings")
    conn.execute("INSERT INTO financials_quarterly (ticker, period_end, fiscal_year) VALUES ('AAA', '2024-12-31', "
                 "2024)")
    conn.commit()
    store._migrate(conn)
    assert conn.execute("SELECT source_filings FROM financials_quarterly").fetchone() == (None,)
    conn.close()


def test_the_store_keeps_each_quarters_figure_tags(tmp_path):
    s = StockData(ticker="AAA", cik="000", company_name="A Inc.")
    s.financials_quarterly = {"2025-03-31": {"fiscal_year": 2025, "fiscal_quarter": 1, "revenue": 1.0e9,
                                             "depreciation_amortization": 2.0e8,
                                             "_source_tags": {"revenue": "Revenues",
                                                              "depreciation_amortization": SPREAD}}}
    s.add_source("sec_edgar")
    SQLiteStore(tmp_path / "stock.db").export([s])
    assert _quarter_tags(tmp_path / "stock.db", "2025-03-31") == {"depreciation_amortization": SPREAD,
                                                                  "revenue": "Revenues"}


def test_a_quarterly_table_from_before_the_column_gains_it(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "old.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("ALTER TABLE financials_quarterly DROP COLUMN source_tags")
    conn.execute("INSERT INTO financials_quarterly (ticker, period_end, fiscal_year) VALUES ('AAA', '2024-12-31', "
                 "2024)")
    conn.commit()
    store._migrate(conn)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(financials_quarterly)")}
    assert "source_tags" in columns
    assert conn.execute("SELECT source_tags FROM financials_quarterly").fetchone() == (None,)
    conn.close()


# ---- end to end: the parser's spread, stored, read by the history ---------------------------------------------------

def _filing(start, end, form, filed, tagged, shares):
    entry = dict(fy=int(filed[:4]), fp="FY" if form == "10-K" else "Q", form=form, filed=filed)
    facts = [(tag, usd(val, start, end, **entry)) for tag, val in {"Revenues": 1e10, **tagged}.items()]
    facts.append(("Assets", usd(1e12, None, end, **entry)))
    return facts, ("WeightedAverageNumberOfSharesOutstandingBasic", usd(shares, start, end, **entry))


def test_the_parsers_spread_quarters_wait_for_their_10k_in_the_history(tmp_path):
    # AVGO's FY2024 shape (tests/test_da_bases.py): its 10-Qs tag Depreciation alone, its 10-K the amortization of
    # intangibles too, which the stored quarters now hold a share of. FY2023 is on one basis.
    filings = [("2022-10-31", "2023-01-29", "10-Q", "2023-03-09", {"Depreciation": 130e6}),
               ("2022-10-31", "2023-04-30", "10-Q", "2023-06-08", {"Depreciation": 260e6}),
               ("2022-10-31", "2023-07-30", "10-Q", "2023-09-07", {"Depreciation": 390e6}),
               ("2022-10-31", "2023-10-29", "10-K", "2023-12-14", {"Depreciation": 520e6}),
               ("2023-10-30", "2024-02-04", "10-Q", "2024-03-14", {"Depreciation": 139e6}),
               ("2023-10-30", "2024-05-05", "10-Q", "2024-06-13", {"Depreciation": 288e6}),
               ("2023-10-30", "2024-08-04", "10-Q", "2024-09-11", {"Depreciation": 437e6}),
               ("2023-10-30", "2024-11-03", "10-K", "2024-12-20", {"Depreciation": 593e6,
                                                                    "AmortizationOfIntangibleAssets": 9267e6})]
    us_gaap = {}
    for filing in filings:
        facts, (share_tag, share_entry) = _filing(*filing, shares=4.6e9)
        for tag, entry in facts:
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(entry)
        us_gaap.setdefault(share_tag, {"units": {"shares": []}})["units"]["shares"].append(share_entry)
    quarters = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": us_gaap}})
    assert quarters["2024-08-04"]["_source_tags"]["depreciation_amortization"] == SPREAD
    s = StockData(ticker="AAA", cik="000", company_name="A Inc.")
    s.financials_quarterly = quarters
    s.financials_ttm = compute_ttm(quarters)
    s.financials_annual = {"2023": {"fiscal_year": 2023, "period_end": "2023-10-29"},
                           "2024": {"fiscal_year": 2024, "period_end": "2024-11-03"}}
    s.financials_annual_vintages = {
        "2023": {"acc-2023": {"filed_date": "2023-12-14", "period_end": "2023-10-29", "form": "10-K"}},
        "2024": {"acc-2024": {"filed_date": "2024-12-20", "period_end": "2024-11-03", "form": "10-K"}}}
    s.add_source("sec_edgar")
    path = tmp_path / "stock.db"
    SQLiteStore(path).export([s])
    assert _quarter_tags(path, "2024-08-04")["depreciation_amortization"] == SPREAD
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', ?, 100.0)",
                     [("2023-01-31",), ("2024-01-31",), ("2024-10-31",), ("2025-01-31",)])
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price, market_cap) "
                 "VALUES ('AAA', '2025-02-03T10:00:00-05:00', 100.0, 4.6e11)")
    conn.execute("INSERT INTO collection_runs (ticker, collected_at) VALUES ('AAA', '2025-02-03T10:00:00-05:00')")
    conn.commit()
    x = load_history_inputs(conn, "AAA")
    known = {p.period_end: p.known_on for p in x.points}
    assert known["2023-10-29"] == date(2023, 12, 14)
    assert (known["2024-02-04"], known["2024-05-05"], known["2024-08-04"]) == (date(2024, 12, 20),) * 3
    assert known["2024-11-03"] == date(2024, 12, 20)
    rows = {r.date: r for r in compute_history(x).rows}
    assert rows["2024-01-31"].ttm_period_end == "2023-10-29"  # 48 days old: the FY2023 trailing year
    conn.close()


# ---- BUG-395: the 10-K that supplies a spread ------------------------------------------------------------------------

def _facts(filings, shares=4.6e9):
    us_gaap = {}
    for filing in filings:
        facts, (share_tag, share_entry) = _filing(*filing, shares=shares)
        for tag, entry in facts:
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(entry)
        us_gaap.setdefault(share_tag, {"units": {"shares": []}})["units"]["shares"].append(share_entry)
    return {"facts": {"us-gaap": us_gaap}}


def _supplier(quarters, end):
    return (quarters[end].get("_source_filings") or {}).get("depreciation_amortization", {}).get("filed")


# AVGO's real FY2024 (companyfacts CIK0001730168): its 10-Qs and its first 10-K (2024-12-20) tag Depreciation alone
# (139/288/437/593M); the amortization of intangibles for FY2024, $9,267M, is first tagged by the FY2025 10-K of
# 2025-12-18, from which BUG-275 spreads FY2024's quarters (code review 42's probe).
AVGO = [("2022-10-31", "2023-01-29", "10-Q", "2023-03-09", {"Depreciation": 130e6}),
        ("2022-10-31", "2023-04-30", "10-Q", "2023-06-08", {"Depreciation": 260e6}),
        ("2022-10-31", "2023-07-30", "10-Q", "2023-09-07", {"Depreciation": 390e6}),
        ("2022-10-31", "2023-10-29", "10-K", "2023-12-14", {"Depreciation": 520e6}),
        ("2023-10-30", "2024-02-04", "10-Q", "2024-03-14", {"Depreciation": 139e6}),
        ("2023-10-30", "2024-05-05", "10-Q", "2024-06-13", {"Depreciation": 288e6}),
        ("2023-10-30", "2024-08-04", "10-Q", "2024-09-11", {"Depreciation": 437e6}),
        ("2023-10-30", "2024-11-03", "10-K", "2024-12-20", {"Depreciation": 593e6}),
        ("2024-11-04", "2025-02-02", "10-Q", "2025-03-12", {"Depreciation": 150e6}),
        ("2024-11-04", "2025-05-04", "10-Q", "2025-06-11", {"Depreciation": 300e6}),
        ("2024-11-04", "2025-08-03", "10-Q", "2025-09-10", {"Depreciation": 450e6}),
        ("2023-10-30", "2024-11-03", "10-K", "2025-12-18", {"Depreciation": 593e6,
                                                             "AmortizationOfIntangibleAssets": 9267e6}),
        ("2024-11-04", "2025-11-02", "10-K", "2025-12-18", {"Depreciation": 600e6,
                                                             "AmortizationOfIntangibleAssets": 6000e6})]


def test_a_spread_names_the_later_10k_that_first_tags_its_amortization():
    facts = _facts(AVGO)
    quarters = XBRLParser().extract_quarterly_financials(facts)
    for end in ("2024-02-04", "2024-05-05", "2024-08-04", "2024-11-03"):
        assert quarters[end]["_source_tags"]["depreciation_amortization"] == SPREAD
        assert _supplier(quarters, end) == "2025-12-18", end
    assert quarters["2024-08-04"]["_source_filings"]["depreciation_amortization"] == {
        "accn": "2025-12-18", "filed": "2025-12-18", "form": "10-K"}
    # what was known on the year's first 10-K day: the 10-Q's line, no spread
    then = XBRLParser().extract_quarterly_financials(facts_known_by(facts, "2024-12-20"))["2024-08-04"]
    assert then["_source_tags"]["depreciation_amortization"] != SPREAD
    assert then["depreciation_amortization"] == pytest.approx(149e6)
    # an unspread quarter names no filing
    assert "_source_filings" not in quarters["2023-07-30"]


# SWK's real FY2020 (companyfacts CIK0000093556): its 10-Qs tag DepreciationDepletionAndAmortization; its first 10-K
# (2021-02-18) gives the year $376.5M, on the 10-Qs' basis; its 10-K of 2022-02-22 restates it to $578.1M, with the
# amortization, but tags no line under the 10-Qs' tag, so nothing spreads; its 10-K of 2023-02-23 tags that line too.
DDA, DA = "DepreciationDepletionAndAmortization", "DepreciationAndAmortization"
SWK = [("2019-12-29", "2020-03-28", "10-Q", "2020-04-28", {DDA: 92.8e6}),
       ("2019-12-29", "2020-06-27", "10-Q", "2020-07-28", {DDA: 187.0e6}),
       ("2019-12-29", "2020-09-26", "10-Q", "2020-10-27", {DDA: 281.1e6}),
       ("2019-12-29", "2021-01-02", "10-K", "2021-02-18", {DA: 376.5e6, "Depreciation": 332.6e6,
                                                            "AmortizationOfIntangibleAssets": 201.6e6}),
       ("2019-12-29", "2021-01-02", "10-K", "2022-02-22", {DA: 578.1e6, "Depreciation": 332.6e6,
                                                            "AmortizationOfIntangibleAssets": 201.6e6}),
       ("2019-12-29", "2021-01-02", "10-K", "2023-02-23", {DDA: 376.5e6, DA: 578.1e6, "Depreciation": 332.6e6,
                                                            "AmortizationOfIntangibleAssets": 201.6e6})]


def test_a_spread_names_the_later_10k_that_first_tags_its_line():
    facts = _facts(SWK, shares=1.5e8)
    quarters = XBRLParser().extract_quarterly_financials(facts)
    for end in ("2020-03-28", "2020-06-27", "2020-09-26", "2021-01-02"):
        assert quarters[end]["_source_tags"]["depreciation_amortization"] == SPREAD
        assert _supplier(quarters, end) == "2023-02-23", end
    # the 10-K of 2022-02-22 gave the year its $578.1M, but no spread
    then = XBRLParser().extract_quarterly_financials(facts_known_by(facts, "2022-02-22"))
    assert then["2020-09-26"]["_source_tags"]["depreciation_amortization"] != SPREAD
    assert then["2020-09-26"]["depreciation_amortization"] == pytest.approx(94.1e6)


def test_a_spread_names_the_first_10k_that_gives_every_figure_it_read():
    # the first 10-K (2024-12-20) already tags the amortization; a later one repeats it, beside a figure it no longer
    # tags (SWK's FY2022 Depreciation, gone from its 10-K of 2025-02-18), and another restates an unread tag
    filings = AVGO[:7] + [
        ("2023-10-30", "2024-11-03", "10-K", "2024-12-20", {"Depreciation": 593e6,
                                                             "AmortizationOfIntangibleAssets": 9267e6}),
        ("2023-10-30", "2024-11-03", "10-K", "2025-12-18", {"AmortizationOfIntangibleAssets": 9267e6})]
    quarters = XBRLParser().extract_quarterly_financials(_facts(filings))
    assert quarters["2024-08-04"]["_source_tags"]["depreciation_amortization"] == SPREAD
    assert _supplier(quarters, "2024-08-04") == "2024-12-20"
    # within the filings' rounding (_AGREE, 0.5%) a figure is the same; beyond it, another (a restatement)
    for later, supplier in ((9300e6, "2024-12-20"), (9500e6, "2025-12-18")):
        filings[-1] = ("2023-10-30", "2024-11-03", "10-K", "2025-12-18", {"Depreciation": 593e6,
                                                                          "AmortizationOfIntangibleAssets": later})
        quarters = XBRLParser().extract_quarterly_financials(_facts(filings))
        assert _supplier(quarters, "2024-08-04") == supplier, later


def test_a_10q_giving_the_year_its_figures_supplies_no_spread():
    # a 10-Q (2025-03-12) tagging FY2024's year with the amortization before the 10-K of 2025-12-18 does: the spread is
    # a 10-K's (BUG-275), and so is its supplier
    filings = AVGO + [("2023-10-30", "2024-11-03", "10-Q", "2025-03-12", {"Depreciation": 593e6,
                                                                          "AmortizationOfIntangibleAssets": 9267e6})]
    quarters = XBRLParser().extract_quarterly_financials(_facts(filings))
    assert quarters["2024-08-04"]["_source_tags"]["depreciation_amortization"] == SPREAD
    assert _supplier(quarters, "2024-08-04") == "2025-12-18"


@pytest.mark.parametrize("vintage_da", [True, False])
def test_a_spread_from_a_later_10k_is_not_read_before_that_10k(tmp_path, vintage_da):
    # Code review 42's probe, end to end: the parser's AVGO FY2024, stored by SQLiteStore, read by load_history_inputs.
    # The vintages' D&A (the first 10-K's $593M, the later one's $9,860M) or none: the parser's supplier dates it.
    quarters = XBRLParser().extract_quarterly_financials(_facts(AVGO))

    def vintage(filed, end, da):
        return {"filed_date": filed, "period_end": end, "form": "10-K",
                **({"depreciation_amortization": da} if vintage_da else {})}

    s = StockData(ticker="AAA", cik="000", company_name="A Inc.")
    s.financials_quarterly = quarters
    s.financials_ttm = compute_ttm(quarters)
    s.financials_annual = {"2023": {"fiscal_year": 2023, "period_end": "2023-10-29"},
                           "2024": {"fiscal_year": 2024, "period_end": "2024-11-03"},
                           "2025": {"fiscal_year": 2025, "period_end": "2025-11-02"}}
    s.financials_annual_vintages = {
        "2023": {"acc-2023": vintage("2023-12-14", "2023-10-29", 520e6)},
        "2024": {"acc-2024": vintage("2024-12-20", "2024-11-03", 593e6),
                 "acc-2025": vintage("2025-12-18", "2024-11-03", 9860e6)},
        "2025": {"acc-2025": vintage("2025-12-18", "2025-11-02", 6600e6)}}
    s.add_source("sec_edgar")
    path = tmp_path / "stock.db"
    SQLiteStore(path).export([s])
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES ('AAA', ?, 100.0)",
                     [("2023-01-31",), ("2024-01-31",), ("2025-01-31",), ("2026-01-30",)])
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price, market_cap) "
                 "VALUES ('AAA', '2026-02-02T10:00:00-05:00', 100.0, 4.6e11)")
    conn.execute("INSERT INTO collection_runs (ticker, collected_at) VALUES ('AAA', '2026-02-02T10:00:00-05:00')")
    conn.commit()
    x = load_history_inputs(conn, "AAA")
    conn.close()
    known = {p.period_end: p.known_on for p in x.points}
    assert known["2023-10-29"] == date(2023, 12, 14)
    # the trailing years over FY2024's spread quarters hold a share of an amortization filed 2025-12-18
    for end in ("2024-02-04", "2024-05-05", "2024-08-04", "2024-11-03"):
        assert known[end] == date(2025, 12, 18), (end, known[end])
    rows = {r.date: r for r in compute_history(x).rows}
    # FY2024 waits for its supplying 10-K (HEAD read it from its first, 2024-12-20); FY2023's is stale by then
    assert rows["2025-01-31"].ttm_period_end is None
