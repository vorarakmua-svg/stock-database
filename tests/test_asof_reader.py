"""Tests for the point-in-time as-of-date query API (no look-ahead)."""

import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.query.asof import AsOfReader


def _build_db(tmp_path, vintages, ticker="PRU"):
    """Write a StockData carrying the given annual vintages and return the db path.

    vintages: {fy_str: {accn: period_dict}} exactly as StockData.financials_annual_vintages.
    """
    s = StockData(ticker=ticker, cik="1", company_name="Prudential")
    s.financials_annual_vintages = vintages
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([s])
    return str(db)


def test_reader_connection_is_read_only(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    reader = AsOfReader(db)
    with pytest.raises(sqlite3.OperationalError):
        reader._conn.execute(
            "INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn) "
            "VALUES ('X', 1, 'y')"
        )
    reader.close()


def test_reader_context_manager_closes(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    with AsOfReader(db) as reader:
        assert reader._conn is not None
    # After exit, using the closed connection raises.
    with pytest.raises(sqlite3.ProgrammingError):
        reader._conn.execute("SELECT 1")


def _pru_two_vintages(tmp_path):
    return _build_db(tmp_path, {
        "2019": {
            "a-orig": {"fiscal_year": 2019, "accn": "a-orig", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "revenue": 1000.0},
            "b-restate": {"fiscal_year": 2019, "accn": "b-restate", "filed_date": "2021-02-15",
                          "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                          "net_income": 90.0, "revenue": 1000.0},
        },
    })


def test_not_yet_filed_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Before the original filing, FY2019 was not yet known.
        assert r.as_of_annual("PRU", 2019, "2020-02-14") is None


def test_boundary_is_inclusive(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # On the exact filing date, the original is visible.
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row is not None
        assert row["net_income"] == 100.0
        assert row["accn"] == "a-orig"


def test_restatement_switch(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Between the two filings: still the original.
        assert r.as_of_annual("PRU", 2019, "2020-06-30")["net_income"] == 100.0
        # On/after the restatement: the restated value.
        assert r.as_of_annual("PRU", 2019, "2021-02-15")["net_income"] == 90.0
        assert r.as_of_annual("PRU", 2019, "2025-01-01")["net_income"] == 90.0


def test_row_carries_provenance_metadata(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-06-30")
        assert row["fiscal_year"] == 2019
        assert row["accn"] == "a-orig"
        assert row["filed_date"] == "2020-02-15"
        assert row["period_end"] == "2019-12-31"
        assert row["form"] == "10-K"


def test_accepts_date_object(tmp_path):
    from datetime import date
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("PRU", 2019, date(2020, 6, 30))["net_income"] == 100.0


def test_same_day_tie_break_prefers_higher_accn(tmp_path):
    # Two filings on the same date: deterministic accn DESC tie-break.
    db = _build_db(tmp_path, {
        "2019": {
            "0001-10K": {"fiscal_year": 2019, "accn": "0001-10K", "filed_date": "2020-02-15",
                         "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                         "net_income": 100.0},
            "0002-10KA": {"fiscal_year": 2019, "accn": "0002-10KA", "filed_date": "2020-02-15",
                          "period_end": "2019-12-31", "form": "10-K/A", "calendar_year": 2019,
                          "net_income": 105.0},
        },
    })
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row["accn"] == "0002-10KA"
        assert row["net_income"] == 105.0


def test_unknown_ticker_or_year_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("ZZZZ", 2019, "2025-01-01") is None
        assert r.as_of_annual("PRU", 1990, "2025-01-01") is None


def test_as_of_value_delegates(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2019, "net_income", "2020-06-30") == 100.0
        assert r.as_of_value("PRU", 2019, "net_income", "2021-02-15") == 90.0
        # Not yet filed → None passthrough.
        assert r.as_of_value("PRU", 2019, "net_income", "2019-01-01") is None
        # Missing field → None.
        assert r.as_of_value("PRU", 2019, "no_such_field", "2025-01-01") is None


# ---------------------------------------------------------------------------
# Task 3: history_as_of
# ---------------------------------------------------------------------------

def _three_years(tmp_path):
    def period(fy, accn, filed, ni):
        return {"fiscal_year": fy, "accn": accn, "filed_date": filed,
                "period_end": f"{fy}-12-31", "form": "10-K", "calendar_year": fy,
                "net_income": ni}
    return _build_db(tmp_path, {
        "2018": {"k18": period(2018, "k18", "2019-02-15", 80.0)},
        "2019": {"k19": period(2019, "k19", "2020-02-15", 100.0),
                 "k19r": period(2019, "k19r", "2021-02-15", 90.0)},
        "2020": {"k20": period(2020, "k20", "2021-02-15", 110.0)},
    })


def test_history_as_of_only_includes_filed_years(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        # As of mid-2020, FY2020 has not been filed yet (filed 2021-02-15).
        hist = r.history_as_of("PRU", "2020-06-30")
        assert set(hist.keys()) == {2018, 2019}
        assert hist[2019]["net_income"] == 100.0  # pre-restatement
        assert hist[2018]["net_income"] == 80.0


def test_history_as_of_reflects_restatement_and_full_set(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        hist = r.history_as_of("PRU", "2021-02-15")
        assert set(hist.keys()) == {2018, 2019, 2020}
        assert hist[2019]["net_income"] == 90.0  # restated by this date
        assert hist[2020]["net_income"] == 110.0


def test_history_as_of_newest_first_and_years_back(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        hist = r.history_as_of("PRU", "2021-02-15", years_back=2)
        assert list(hist.keys()) == [2020, 2019]  # newest first, trimmed to 2


def test_history_as_of_unknown_ticker_empty(tmp_path):
    db = _three_years(tmp_path)
    with AsOfReader(db) as r:
        assert r.history_as_of("ZZZZ", "2025-01-01") == {}


def test_a_database_under_a_hash_folder_opens(tmp_path):
    # BUG-080: a '#' in the path cut `?mode=ro` off, and the reader opened, and created, an empty database at "proj".
    (tmp_path / "proj#1").mkdir()
    db = _build_db(tmp_path / "proj#1", {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    with AsOfReader(db) as reader:
        assert reader.as_of_value("PRU", 2019, "net_income", "2020-03-01") == 100.0
    assert [p.name for p in tmp_path.iterdir()] == ["proj#1"]


# ---------------------------------------------------------------------------
# BUG-110: a later 10-K repeats only part of a year
# ---------------------------------------------------------------------------

def _partial_refilings(tmp_path):
    """FY2016 as MSFT's 10-Ks file it: whole in its own 10-K and the next one's comparatives, then the
    FY2018 10-K restating the income statement without the balance sheet, then the FY2019 10-K with only
    the equity statement's opening balance."""
    def period(accn, filed, **figures):
        return {"fiscal_year": 2016, "accn": accn, "filed_date": filed, "period_end": "2016-06-30",
                "form": "10-K", "calendar_year": 2016, **figures}
    return _build_db(tmp_path, {"2016": {
        "k16": period("k16", "2016-07-28", revenue=85.3e9, net_income=16.8e9, total_assets=193.7e9,
                      total_equity=72.0e9),
        "k17": period("k17", "2017-08-02", revenue=85.3e9, net_income=16.8e9, total_assets=193.5e9,
                      total_equity=72.0e9),
        "k18": period("k18", "2018-08-03", revenue=91.2e9, net_income=20.5e9, total_equity=83.1e9),
        "k19": period("k19", "2019-08-01", total_equity=83.1e9),
    }}, ticker="MSFT")


def test_a_figure_a_later_filing_leaves_out_keeps_its_last_filed_value(tmp_path):
    # BUG-110: the latest filing, holding only the opening equity, came back whole: no revenue as of today.
    db = _partial_refilings(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("MSFT", 2016, "2026-01-01")
    assert (row["revenue"], row["net_income"], row["total_assets"], row["total_equity"]) == (
        91.2e9, 20.5e9, 193.5e9, 83.1e9)
    assert (row["accn"], row["filed_date"]) == ("k19", "2019-08-01")  # the latest filing's provenance
    restated = {"accn": "k18", "filed_date": "2018-08-03", "form": "10-K"}
    assert row["carried_from"] == {"revenue": restated, "net_income": restated,
                                   "total_assets": {"accn": "k17", "filed_date": "2017-08-02", "form": "10-K"}}


def test_a_figure_filed_after_the_date_is_never_carried(tmp_path):
    db = _partial_refilings(tmp_path)
    with AsOfReader(db) as r:
        before = r.as_of_annual("MSFT", 2016, "2018-08-02")
        after = r.as_of_annual("MSFT", 2016, "2018-08-03")
    assert (before["revenue"], before["total_assets"], before["accn"], before["carried_from"]) == (
        85.3e9, 193.5e9, "k17", {})
    assert (after["revenue"], after["total_assets"], after["accn"]) == (91.2e9, 193.5e9, "k18")
    assert after["carried_from"] == {"total_assets": {"accn": "k17", "filed_date": "2017-08-02", "form": "10-K"}}


def test_as_of_value_and_history_read_each_figure_as_last_filed(tmp_path):
    db = _partial_refilings(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_value("MSFT", 2016, "revenue", "2026-01-01") == 91.2e9
        assert r.history_as_of("MSFT", "2026-01-01")[2016]["total_assets"] == 193.5e9


# ---------------------------------------------------------------------------
# BUG-271: a later 10-K's comparative under a narrower concept
# ---------------------------------------------------------------------------

def _vintage(fy, accn, filed, tags=None, **figures):
    """A vintage as the parser gives it: its figures and, unless *tags* is None, the tag each was read from."""
    period = {"fiscal_year": fy, "accn": accn, "filed_date": filed, "period_end": f"{fy}-12-31", "form": "10-K",
              "calendar_year": fy, **figures}
    if tags is not None:
        period["_source_tags"] = tags
    return period


def _exc(tmp_path):
    """EXC's FY2023 as its 10-Ks file it: its own 10-K's long-term debt total (holding the maturities) beside its
    borrowings; the FY2024 10-K's comparative, a $0.198B note under LongTermNotesPayable beside the borrowings and
    maturities combined; the FY2025 10-K's equity statement alone."""
    return _build_db(tmp_path, {"2023": {
        "k23": _vintage(2023, "k23", "2024-02-21", {"long_term_debt": "LongTermDebt",
                                                     "short_term_debt": "ShortTermBorrowings",
                                                     "total_equity": "StockholdersEquity"},
                        long_term_debt=41.279e9, short_term_debt=2.523e9, total_equity=26.9e9),
        "k24": _vintage(2023, "k24", "2025-02-12", {"long_term_debt": "LongTermNotesPayable",
                                                     "short_term_debt": "DebtCurrent",
                                                     "total_equity": "StockholdersEquity"},
                        long_term_debt=0.198e9, short_term_debt=3.926e9, total_equity=26.9e9),
        "k25": _vintage(2023, "k25", "2026-02-12", {"total_equity": "StockholdersEquity"}, total_equity=26.9e9),
    }}, ticker="EXC")


def test_a_later_filings_narrower_debt_figure_leaves_the_balance_sheets(tmp_path):
    # BUG-271: the FY2024 10-K's $0.198B note became FY2023's long-term debt, $2.721B of debt for $43.802B.
    db = _exc(tmp_path)
    with AsOfReader(db) as r:
        for day in ("2025-02-12", "2026-11-03"):
            row = r.as_of_annual("EXC", 2023, day)
            assert (row["long_term_debt"], row["short_term_debt"]) == (41.279e9, 2.523e9), day
            k23 = {"accn": "k23", "filed_date": "2024-02-21", "form": "10-K"}
            assert row["carried_from"]["long_term_debt"] == k23
            assert row["carried_from"]["short_term_debt"] == k23


def test_a_date_reads_its_debt_from_one_filings_view(tmp_path):
    # The FY2023 10-K's total holds the maturities its FY2024 10-K combines into the short-term figure: read
    # together, not $41.279B beside $3.926B (the maturities twice).
    db = _exc(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("EXC", 2023, "2026-11-03")
    assert row["long_term_debt"] + row["short_term_debt"] == 43.802e9


def test_a_narrower_figure_filed_after_the_date_is_never_read(tmp_path):
    db = _exc(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("EXC", 2023, "2025-02-11")
    assert (row["long_term_debt"], row["short_term_debt"], row["accn"], row["carried_from"]) == (
        41.279e9, 2.523e9, "k23", {})


def test_a_broader_figure_filed_after_the_date_is_never_read(tmp_path):
    # The rule looks back only: as of a day before the broader figure, the narrower one filed first stands.
    db = _build_db(tmp_path, {"2023": {
        "a": _vintage(2023, "a", "2024-02-21", {"long_term_debt": "LongTermNotesPayable"}, long_term_debt=0.198e9),
        "b": _vintage(2023, "b", "2025-02-12", {"long_term_debt": "LongTermDebtNoncurrent"}, long_term_debt=39.7e9),
    }})
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2023, "long_term_debt", "2025-02-11") == 0.198e9
        assert r.as_of_value("PRU", 2023, "long_term_debt", "2025-02-12") == 39.7e9


def test_a_restatement_under_the_same_concept_still_carries(tmp_path):
    # However far it moves: a later 10-K's figure under the same tag is the year restated.
    db = _build_db(tmp_path, {"2019": {
        "a": _vintage(2019, "a", "2020-02-15", {"long_term_debt": "LongTermDebtNoncurrent", "revenue": "Revenues"},
                      long_term_debt=10.0e9, revenue=8.0e9),
        "b": _vintage(2019, "b", "2021-02-15", {"long_term_debt": "LongTermDebtNoncurrent", "revenue": "Revenues"},
                      long_term_debt=3.0e9, revenue=2.0e9),
    }})
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2026-11-03")
    assert (row["long_term_debt"], row["revenue"], row["carried_from"]) == (3.0e9, 2.0e9, {})


def test_a_restatement_under_a_broader_or_near_concept_still_carries(tmp_path):
    # A better-ranked tag replaces however small (TSLA's FY2013); a lesser one replaces from half of the figure up:
    # ADI's FY2017 revenue restated under ASC 606's tag ($5.246B for $5.108B), OMC's notes without its convertibles.
    db = _build_db(tmp_path, {"2017": {
        "a": _vintage(2017, "a", "2018-02-15", {"long_term_debt": "ConvertibleLongTermNotesPayable",
                                                 "revenue": "Revenues", "interest_expense": "InterestExpense"},
                      long_term_debt=0.586e9, revenue=5.108e9, interest_expense=4.0e8),
        "b": _vintage(2017, "b", "2019-02-15", {"long_term_debt": "LongTermDebtNoncurrent",
                                                 "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
                                                 "interest_expense": "InterestExpenseOther"},
                      long_term_debt=0.0, revenue=5.246e9, interest_expense=2.0e8),
    }})
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2017, "2026-11-03")
    assert (row["long_term_debt"], row["revenue"], row["interest_expense"]) == (0.0, 5.246e9, 2.0e8)


def test_a_later_filings_narrower_figure_of_any_field_leaves_the_earlier_one(tmp_path):
    # WFC's FY2023 interest expense: its FY2024 10-K's comparative tags InterestExpenseOther, $0.712B of $32.74B;
    # HAL's FY2020 impairments: the FY2022 10-K's goodwill line, $0 beside $2.3B of long-lived assets.
    db = _build_db(tmp_path, {"2023": {
        "a": _vintage(2023, "a", "2024-02-20", {"interest_expense": "InterestExpense",
                                                 "impairment": "ImpairmentOfLongLivedAssetsHeldForUse"},
                      interest_expense=32.74e9, impairment=2.3e9),
        "b": _vintage(2023, "b", "2025-02-25", {"interest_expense": "InterestExpenseOther",
                                                 "impairment": "GoodwillImpairmentLoss"},
                      interest_expense=0.712e9, impairment=0.0),
    }})
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2023, "2026-11-03")
    assert (row["interest_expense"], row["impairment"]) == (32.74e9, 2.3e9)
    assert row["carried_from"]["interest_expense"] == {"accn": "a", "filed_date": "2024-02-20", "form": "10-K"}


def test_a_narrower_figure_just_under_half_is_set_aside(tmp_path):
    # BRO's FY2014: its FY2018 10-K's comparative tags its unsecured notes, $0.55B of the $1.153B of long-term debt.
    db = _build_db(tmp_path, {"2014": {
        "a": _vintage(2014, "a", "2016-02-26", {"long_term_debt": "LongTermDebtNoncurrent"}, long_term_debt=1.153e9),
        "b": _vintage(2014, "b", "2019-02-26", {"long_term_debt": "UnsecuredLongTermDebt"}, long_term_debt=0.55e9),
    }})
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2014, "long_term_debt", "2026-11-03") == 1.153e9


def test_a_derived_figure_ranks_after_every_tag(tmp_path):
    # BAC's FY2008 liabilities: its FY2010 10-K's equity statement derives -$177.1B beside $1,641B (BUG-085).
    db = _build_db(tmp_path, {"2008": {
        "a": _vintage(2008, "a", "2010-02-26", {"total_liabilities": "Liabilities"}, total_liabilities=1641.0e9),
        "b": _vintage(2008, "b", "2011-02-25", {"total_liabilities": "derived"}, total_liabilities=-177.1e9),
    }})
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2008, "total_liabilities", "2026-11-03") == 1641.0e9


def test_sizes_are_compared_whatever_their_sign(tmp_path):
    # KMB's FY2018 equity: the parent's -$0.287B, then its FY2021 10-K's opening balance with the noncontrolling
    # interest, -$0.046B.
    db = _build_db(tmp_path, {"2018": {
        "a": _vintage(2018, "a", "2020-02-13", {"total_equity": "StockholdersEquity"}, total_equity=-0.287e9),
        "b": _vintage(2018, "b", "2022-02-10",
                      {"total_equity": "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"},
                      total_equity=-0.046e9),
    }})
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2018, "total_equity", "2026-11-03") == -0.287e9


def test_the_paper_is_read_from_the_filing_whose_short_term_debt_is_read(tmp_path):
    # KO's FY2022: its own 10-K's DebtCurrent holds the paper (0.0 apart); the FY2023 10-K shows the maturities
    # alone ($0.399B) and the paper apart ($2.146B). Its DebtCurrent with the FY2023 10-K's paper would count the
    # paper twice.
    db = _build_db(tmp_path, {"2022": {
        "a": _vintage(2022, "a", "2023-02-21",
                      {"short_term_debt": "DebtCurrent", "commercial_paper": "CommercialPaper"},
                      short_term_debt=2.772e9, commercial_paper=0.0),
        "b": _vintage(2022, "b", "2024-02-20", {"short_term_debt": "LongTermDebtCurrent",
                                                 "commercial_paper": "CommercialPaper"},
                      short_term_debt=0.399e9, commercial_paper=2.146e9),
    }})
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2022, "2026-11-03")
    assert (row["short_term_debt"], row["commercial_paper"]) == (2.772e9, 0.0)


def test_a_later_filings_paper_beside_no_short_term_figure_still_carries(tmp_path):
    # QCOM's FY2015: its FY2016 10-K tags as paper the line its FY2015 10-K tags as borrowings; the parser read that
    # paper beside the borrowings the as-of view carries (BUG-256).
    db = _build_db(tmp_path, {"2015": {
        "a": _vintage(2015, "a", "2015-11-04", {"short_term_debt": "ShortTermBorrowings",
                                                 "long_term_debt": "LongTermDebtNoncurrent"},
                      short_term_debt=1.0e9, long_term_debt=10.0e9),
        "b": _vintage(2015, "b", "2016-11-02", {"commercial_paper": "CommercialPaper"}, commercial_paper=0.0),
    }})
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2015, "2026-11-03")
    assert (row["short_term_debt"], row["long_term_debt"], row["commercial_paper"]) == (1.0e9, 10.0e9, 0.0)


def test_vintages_stored_without_their_tags_read_the_latest_filing(tmp_path):
    # A vintage collected before the tags were stored can't tell a narrower concept: the latest filing's, as before.
    db = _build_db(tmp_path, {"2023": {
        "a": _vintage(2023, "a", "2024-02-21", {"long_term_debt": "LongTermDebt"}, long_term_debt=41.279e9),
        "b": _vintage(2023, "b", "2025-02-12", None, long_term_debt=0.198e9),
    }})
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2023, "long_term_debt", "2026-11-03") == 0.198e9


def test_a_database_stored_before_the_tags_column_reads_as_before(tmp_path):
    db = _exc(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE financials_annual_vintages DROP COLUMN source_tags")
    conn.commit()
    conn.close()
    with AsOfReader(db) as r:
        row = r.as_of_annual("EXC", 2023, "2026-11-03")
    assert (row["long_term_debt"], row["short_term_debt"]) == (0.198e9, 3.926e9)


def test_the_period_keeps_its_shape(tmp_path):
    db = _exc(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("EXC", 2023, "2026-11-03")
    assert "source_tags" not in row

