"""Tests for the SQLite store: schema, upserts, and cross-company screening."""

import json
import sqlite3
from datetime import datetime

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.parsers.calculated_metrics import CalculatedMetrics


def _company(ticker, revenue, roic):
    s = StockData(ticker=ticker, cik="000", company_name=f"{ticker} Inc.")
    s.company_info = {"sector": "Technology"}
    s.financials_annual = {"2024": {"fiscal_year": 2024, "revenue": revenue,
                                    "net_income": revenue * 0.1, "total_assets": revenue * 2,
                                    "total_liabilities": revenue, "total_equity": revenue,
                                    "operating_cash_flow": revenue * 0.2}}
    s.calculated_metrics = {"historical": {"2024": {"roic": roic, "net_margin": 0.1}}}
    s.add_source("sec_edgar")
    return s


def test_export_creates_schema_and_rows(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    store.export([_company("AAA", 1000.0, 0.20)])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1
        row = conn.execute(
            "SELECT revenue FROM financials_annual WHERE ticker='AAA' AND fiscal_year=2024"
        ).fetchone()
        assert row[0] == 1000.0
        roic = conn.execute(
            "SELECT roic FROM metrics_annual WHERE ticker='AAA' AND fiscal_year=2024"
        ).fetchone()[0]
        assert roic == 0.20
    finally:
        conn.close()


def test_upsert_is_idempotent(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    store.export([_company("AAA", 1000.0, 0.20)])
    # Re-export with an updated value; row count stays 1, value updates.
    store.export([_company("AAA", 1111.0, 0.25)])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM financials_annual WHERE ticker='AAA'"
        ).fetchone()[0] == 1
        rev = conn.execute(
            "SELECT revenue FROM financials_annual WHERE ticker='AAA'"
        ).fetchone()[0]
        assert rev == 1111.0
    finally:
        conn.close()


def test_cross_company_screen(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    store.export([
        _company("AAA", 1000.0, 0.20),
        _company("BBB", 2000.0, 0.05),
        _company("CCC", 3000.0, 0.18),
    ])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        # Screen: companies with ROIC > 15% — comparable only because data is canonical.
        rows = conn.execute(
            "SELECT ticker FROM metrics_annual WHERE roic > 0.15 ORDER BY ticker"
        ).fetchall()
        assert [r[0] for r in rows] == ["AAA", "CCC"]
    finally:
        conn.close()


def test_quarterly_and_ttm_persist(tmp_path):
    s = StockData(ticker="QQQ", cik="000", company_name="Q Inc.")
    s.financials_quarterly = {
        "2024-09-28": {"period_end": "2024-09-28", "fiscal_year": 2024,
                       "fiscal_quarter": 4, "calendar_year": 2024, "calendar_quarter": 3,
                       "revenue": 95.0, "net_income": 14.0, "total_assets": 365.0},
    }
    s.financials_ttm = {
        "2024-09-28": {"period_end": "2024-09-28", "fiscal_year": 2024,
                       "calendar_year": 2024, "calendar_quarter": 3,
                       "revenue": 391.0, "total_assets": 365.0},
    }
    s.add_source("sec_edgar")
    SQLiteStore(tmp_path / "stock.db").export([s])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        fq = conn.execute(
            "SELECT fiscal_quarter, revenue FROM financials_quarterly "
            "WHERE ticker='QQQ' AND period_end='2024-09-28'"
        ).fetchone()
        assert fq == (4, 95.0)
        ttm = conn.execute(
            "SELECT revenue FROM financials_ttm WHERE ticker='QQQ'"
        ).fetchone()
        assert ttm[0] == 391.0
    finally:
        conn.close()


def test_unmapped_facts_persist(tmp_path):
    s = StockData(ticker="UMC", cik="000", company_name="U Inc.")
    s.unmapped_facts = [
        {"tag": "NewRevenueConceptZZZ", "label": "New Revenue", "unit": "USD",
         "period_end": "2025-09-30", "value": 5e9, "form": "10-K"},
    ]
    s.add_source("sec_edgar")
    SQLiteStore(tmp_path / "stock.db").export([s])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        row = conn.execute(
            "SELECT tag, value FROM unmapped_facts WHERE ticker='UMC'"
        ).fetchone()
        assert row == ("NewRevenueConceptZZZ", 5e9)
    finally:
        conn.close()


def test_empty_export_returns_none(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    assert store.export([]) is None


def _company_with_calendar(ticker, fiscal_year, calendar_year):
    s = StockData(ticker=ticker, cik="000", company_name=f"{ticker} Inc.")
    s.financials_annual = {str(fiscal_year): {
        "fiscal_year": fiscal_year, "calendar_year": calendar_year,
        "period_end": f"{fiscal_year}-01-31", "revenue": 100.0, "net_income": 10.0,
        "total_assets": 200.0, "total_equity": 100.0,
    }}
    s.add_source("sec_edgar")
    return s


def test_calendar_year_screen_aligns_fiscal_calendars(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    # A January filer (fiscal 2026) and a December filer (fiscal 2025) both belong to
    # macro calendar year 2025 and must be returned together.
    store.export([
        _company_with_calendar("JANCO", 2026, 2025),
        _company_with_calendar("DECCO", 2025, 2025),
        _company_with_calendar("OLDCO", 2024, 2024),
    ])
    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        rows = conn.execute(
            "SELECT ticker FROM financials_annual WHERE calendar_year=2025 ORDER BY ticker"
        ).fetchall()
        assert [r[0] for r in rows] == ["DECCO", "JANCO"]
    finally:
        conn.close()


def test_migrate_adds_missing_columns(tmp_path):
    db = tmp_path / "stock.db"
    # Simulate an old DB: a companies table without the newer sector_class column.
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE companies (ticker TEXT PRIMARY KEY, cik TEXT)")
    conn.commit()
    conn.close()

    SQLiteStore(db).export([_company("AAA", 1000.0, 0.20)])

    conn = sqlite3.connect(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(companies)")}
        assert "sector_class" in cols  # migration added it
        # And a new canonical column exists on the financials table.
        fcols = {row[1] for row in conn.execute("PRAGMA table_info(financials_annual)")}
        assert "net_interest_income" in fcols
        assert "calendar_year" in fcols  # calendar alignment column migrated in
        qcols = {row[1] for row in conn.execute("PRAGMA table_info(financials_quarterly)")}
        assert "fiscal_quarter" in qcols
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "financials_ttm" in tables  # new table created on existing DB
        assert "unmapped_facts" in tables  # tag-as-data capture table created too
        mcols = {row[1] for row in conn.execute("PRAGMA table_info(metrics_annual)")}
        assert "efficiency_ratio" in mcols   # sector ratio column migrated in
        assert "ffo" in mcols
    finally:
        conn.close()


def test_sector_metrics_persist_with_suppressed_nulls(tmp_path):
    s = StockData(ticker="BNK", cik="000", company_name="Bank Inc.")
    s.sector_class = "bank"
    s.financials_annual = {"2024": {"fiscal_year": 2024, "net_income": 20.0,
                                    "total_assets": 1000.0, "total_equity": 120.0}}
    # Simulate what calculate_historical(sector="bank") produces: a bank ratio set
    # plus the generic ratios it suppresses (stored as None).
    s.calculated_metrics = {"historical": {"2024": {
        "efficiency_ratio": 0.6, "loan_to_deposit": 0.67,
        "net_interest_margin": 0.05, "roe": 0.16,
        "roic": None, "inventory_turnover": None,
    }}}
    s.add_source("sec_edgar")
    SQLiteStore(tmp_path / "stock.db").export([s])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        eff, roic = conn.execute(
            "SELECT efficiency_ratio, roic FROM metrics_annual "
            "WHERE ticker='BNK' AND fiscal_year=2024"
        ).fetchone()
        assert eff == 0.6
        assert roic is None          # suppressed -> NULL
    finally:
        conn.close()


def test_vintages_table_written_and_idempotent(tmp_path):
    import sqlite3

    from src.exporters.sqlite_store import SQLiteStore
    from src.models.stock_data import StockData

    s = StockData(ticker="ZZ", cik="9", company_name="Z Co")
    s.financials_annual_vintages = {
        "2022": {
            "acc-A": {"fiscal_year": 2022, "accn": "acc-A", "filed_date": "2023-02-15",
                      "period_end": "2022-12-31", "form": "10-K", "calendar_year": 2022,
                      "revenue": 100.0, "net_income": 10.0},
            "acc-B": {"fiscal_year": 2022, "accn": "acc-B", "filed_date": "2024-02-15",
                      "period_end": "2022-12-31", "form": "10-K", "calendar_year": 2022,
                      "revenue": 110.0, "net_income": 11.0},
        }
    }
    db = tmp_path / "v.db"
    store = SQLiteStore(db_path=str(db))
    store.export([s])
    store.export([s])  # second export must not duplicate rows

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT accn, filed_date, revenue FROM financials_annual_vintages "
        "WHERE ticker='ZZ' AND fiscal_year=2022 ORDER BY filed_date"
    ).fetchall()
    conn.close()
    assert rows == [("acc-A", "2023-02-15", 100.0), ("acc-B", "2024-02-15", 110.0)]


def test_vintages_do_not_affect_financials_annual(tmp_path):
    import sqlite3

    from src.exporters.sqlite_store import SQLiteStore
    from src.models.stock_data import StockData

    s = StockData(ticker="ZZ", cik="9", company_name="Z Co")
    s.financials_annual = {"2022": {"fiscal_year": 2022, "period_end": "2022-12-31",
                                    "revenue": 100.0}}
    s.financials_annual_vintages = {"2022": {"acc-A": {"fiscal_year": 2022, "accn": "acc-A",
                                    "filed_date": "2023-02-15", "revenue": 100.0}}}
    db = tmp_path / "v2.db"
    SQLiteStore(db_path=str(db)).export([s])
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM financials_annual WHERE ticker='ZZ'").fetchone()[0]
    conn.close()
    assert n == 1  # the latest-view table is unaffected by vintages


def test_upsert_quote_carries_forward_computed_snapshot_fields(tmp_path):
    """A quote-only refresh (``fetch_quote``) can't compute ma_50/ma_200/beta
    (no history call) or ev_to_fcf/fcf_yield/risk_free_rate (full-export-only
    calculated_metrics/risk_free_rate). Those must carry forward from the
    latest prior snapshot rather than going NULL on every quote refresh."""
    db = tmp_path / "quote.db"
    store = SQLiteStore(db_path=str(db))

    # Seed a prior "full collection" snapshot with the computed-only fields set.
    s = _company("AAA", 1000.0, 0.20)
    s.market_data = {"current_price": 95.0}
    store.export([s])
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE market_snapshots SET ma_50=10.0, ma_200=12.0, beta=1.1, "
        "ev_to_fcf=15.0, fcf_yield=0.07, risk_free_rate=0.045 WHERE ticker='AAA'"
    )
    conn.commit()
    conn.close()

    # A quote-only fetch has none of these fields.
    quote = {
        "market_data": {"current_price": 99.0},
        "valuation": {"pe_trailing": 18.0},
        "shareholders": {},
        "analyst_estimates": {},
    }
    store.upsert_quote("AAA", quote, "2024-06-01T00:00:00")

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT ma_50, ma_200, beta, ev_to_fcf, fcf_yield, risk_free_rate, current_price "
            "FROM market_snapshots WHERE ticker='AAA' AND collected_at='2024-06-01T00:00:00'"
        ).fetchone()
    finally:
        conn.close()
    assert row == (10.0, 12.0, 1.1, 15.0, 0.07, 0.045, 99.0)


def _collected_with_our_ev(tmp_path, financials=True, operating_income=100.0):
    """A full collection of AAA at a market cap of 1,000 (net debt 250, EBITDA 120, FCF 60), Yahoo's own EV beside;
    FY2024 before it with other debt."""
    store = SQLiteStore(db_path=tmp_path / "stock.db")
    s = StockData(ticker="AAA", cik="1", company_name="AAA Inc.")
    if financials:
        year = {"fiscal_year": 2025, "period_end": "2025-12-31", "operating_income": operating_income,
                "depreciation_amortization": 20.0, "operating_cash_flow": 90.0, "capex": 30.0,
                "long_term_debt": 300.0, "short_term_debt": 50.0, "cash_and_equivalents": 100.0}
        s.financials_annual = {"2025": year, "2024": {**year, "fiscal_year": 2024, "period_end": "2024-12-31",
                                                      "long_term_debt": 900.0}}
    s.market_data = {"current_price": 10.0, "market_cap": 1000.0}
    s.valuation = {"enterprise_value": 5000.0, "ev_to_ebitda": 50.0}
    if financials:
        s.calculated_metrics = CalculatedMetrics().calculate_all(
            s.financials_annual["2025"], market_data=s.market_data, valuation=s.valuation)
    s.collected_at = datetime(2026, 8, 1, 12)
    store.export([s])
    return store


def _ev_family(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT enterprise_value, ev_to_ebitda, ev_to_fcf, fcf_yield FROM market_snapshots "
                            "ORDER BY collected_at").fetchall()
    finally:
        conn.close()


def _refresh(store, market_cap):
    quote = {"market_data": {"current_price": 10.1, "market_cap": market_cap},
             "valuation": {"enterprise_value": 5010.0, "ev_to_ebitda": 50.1},
             "shareholders": {}, "analyst_estimates": {}}
    store.upsert_quote("AAA", quote, "2026-08-02T12:00:00")


def test_a_quote_refresh_keeps_our_enterprise_value(tmp_path):
    # BUG-126: a collection stores our EV family (Yahoo's market cap with the latest year's net debt, EBITDA and FCF),
    # and a quote refresh stored Yahoo's own enterpriseValue and enterpriseToEbitda in the same columns, beside the
    # last collection's EV/FCF and FCF yield: ABT's EV read $187.8B after a collection and $213.1B after a refresh.
    store = _collected_with_our_ev(tmp_path)
    _refresh(store, 1010.0)
    collected, refreshed = _ev_family(tmp_path / "stock.db")
    assert collected == pytest.approx((1250.0, 1250.0 / 120.0, 1250.0 / 60.0, 0.06))
    assert refreshed == pytest.approx((1260.0, 1260.0 / 120.0, 1260.0 / 60.0, 60.0 / 1010.0))


def test_a_refresh_at_the_collections_market_cap_reads_its_ev_family(tmp_path):
    store = _collected_with_our_ev(tmp_path)
    _refresh(store, 1000.0)
    collected, refreshed = _ev_family(tmp_path / "stock.db")
    assert refreshed == pytest.approx(collected)


def test_a_refresh_carries_no_ev_multiple_ours_doesnt_have(tmp_path):
    # With EBITDA below zero ours has no EV/EBITDA; an earlier refresh's (Yahoo's 50.1, before BUG-126's fix) stays
    # out of the new row.
    store = _collected_with_our_ev(tmp_path, operating_income=-50.0)
    _refresh(store, 1010.0)
    conn = sqlite3.connect(tmp_path / "stock.db")
    conn.execute("UPDATE market_snapshots SET ev_to_ebitda = 50.1 WHERE collected_at = '2026-08-02T12:00:00'")
    conn.commit()
    conn.close()
    store.upsert_quote("AAA", {"market_data": {"current_price": 10.2, "market_cap": 1020.0}, "valuation": {},
                               "shareholders": {}, "analyst_estimates": {}}, "2026-08-03T12:00:00")
    assert _ev_family(tmp_path / "stock.db")[-1][1] is None


def test_a_refresh_without_a_stored_year_keeps_the_collections_ev_family(tmp_path):
    # Without annual financials a collection computes no EV family; Yahoo's own isn't ours either.
    store = _collected_with_our_ev(tmp_path, financials=False)
    _refresh(store, 1010.0)
    assert _ev_family(tmp_path / "stock.db") == [(None, None, None, None), (None, None, None, None)]


def test_a_failed_recollection_keeps_the_company_details(tmp_path):
    # A re-collection that got no company info used to overwrite the companies row with NULLs,
    # so the web header lost the name and sector.
    store = SQLiteStore(db_path=tmp_path / "stock.db")
    store.export([_company("AAA", revenue=1.0e9, roic=0.12)])
    store.export([StockData(ticker="AAA", errors=["Yahoo Finance: HTTP 429 Too Many Requests"])])
    conn = sqlite3.connect(tmp_path / "stock.db")
    row = conn.execute("SELECT company_name, cik, sector FROM companies WHERE ticker = 'AAA'").fetchone()
    conn.close()
    assert row == ("AAA Inc.", "000", "Technology")


def test_a_run_without_market_data_writes_no_snapshot(tmp_path):
    # A Yahoo failure still gets a risk-free rate (Treasury.gov or the fixed estimate), and the rate alone
    # used to make a snapshot: the latest one, with no price, market cap, beta or shares, read by the web
    # header, the screener and the valuations.
    store = SQLiteStore(db_path=tmp_path / "stock.db")
    good = _company("AAA", revenue=1.0e9, roic=0.12)
    good.market_data = {"current_price": 150.0, "market_cap": 1.5e11, "beta": 1.1}
    good.collected_at = datetime(2026, 8, 4, 12, 0)
    store.export([good])
    failed = StockData(ticker="AAA", collected_at=datetime(2026, 10, 20, 9, 0))
    failed.add_error("Yahoo Finance: HTTP 429 Too Many Requests")
    failed.merge_risk_free_rate({"risk_free_rate": 0.0435, "source": "fallback"})
    store.export([failed])
    conn = sqlite3.connect(tmp_path / "stock.db")
    latest = conn.execute("SELECT current_price FROM market_snapshots WHERE ticker = 'AAA' "
                          "ORDER BY collected_at DESC LIMIT 1").fetchone()
    runs = conn.execute("SELECT count(*) FROM collection_runs WHERE ticker = 'AAA'").fetchone()[0]
    conn.close()
    assert latest == (150.0,)
    assert runs == 2  # the failed run is still recorded


def _latest_price_and_count(db_path, ticker):
    conn = sqlite3.connect(db_path)
    latest = conn.execute("SELECT current_price FROM market_snapshots WHERE ticker = ? "
                          "ORDER BY collected_at DESC LIMIT 1", (ticker,)).fetchone()
    count = conn.execute("SELECT count(*) FROM market_snapshots WHERE ticker = ?", (ticker,)).fetchone()[0]
    conn.close()
    return latest, count


def _priced_store(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "stock.db")
    good = _company("AAA", revenue=1.0e9, roic=0.12)
    good.market_data = {"current_price": 150.0, "market_cap": 1.5e11, "beta": 1.1, "ma_50": 148.0}
    good.collected_at = datetime(2026, 8, 4, 12, 0)
    store.export([good])
    return store


def test_a_quote_without_a_price_writes_no_snapshot(tmp_path):
    # upsert_quote carried beta, the moving averages and the rate forward, so any quote made a new latest
    # snapshot, even one a Yahoo rate limit left without a price.
    store = _priced_store(tmp_path)
    store.upsert_quote("AAA", {"market_data": {}, "valuation": {"pe_trailing": 25.0}, "shareholders": {},
                               "analyst_estimates": {}}, "2026-10-20T09:00:00")
    assert _latest_price_and_count(tmp_path / "stock.db", "AAA") == ((150.0,), 1)


def test_a_run_with_only_price_history_writes_no_snapshot(tmp_path):
    # Yahoo's quote data can come back empty while its price history works: the moving averages alone made a
    # latest snapshot with no price, market cap, shares or beta.
    store = _priced_store(tmp_path)
    partial = StockData(ticker="AAA", collected_at=datetime(2026, 10, 20, 9, 0))
    partial.market_data = {"ma_50": 343.0, "ma_200": 343.0}
    partial.merge_risk_free_rate({"risk_free_rate": 0.0419, "source": "fallback"})
    store.export([partial])
    assert _latest_price_and_count(tmp_path / "stock.db", "AAA") == ((150.0,), 1)


def test_a_snapshot_keeps_the_rates_day_and_source(tmp_path):
    # The rate's day and source reach the valuations through the snapshot. A quote refresh fetches no rate,
    # so it carries them forward with the rate.
    store = SQLiteStore(db_path=tmp_path / "stock.db")
    stock = _company("AAA", revenue=1.0e9, roic=0.12)
    stock.market_data = {"current_price": 150.0}
    stock.collected_at = datetime(2026, 10, 20, 9, 0)
    stock.merge_risk_free_rate({"risk_free_rate": 0.0515, "source": "treasury_gov", "date": "2026-10-19"})
    store.export([stock])
    store.upsert_quote("AAA", {"market_data": {"current_price": 151.0}, "valuation": {}, "shareholders": {},
                               "analyst_estimates": {}}, "2026-10-20T15:00:00")
    conn = sqlite3.connect(tmp_path / "stock.db")
    rows = conn.execute("SELECT risk_free_rate, risk_free_rate_date, risk_free_rate_source FROM market_snapshots "
                        "WHERE ticker = 'AAA' ORDER BY collected_at").fetchall()
    conn.close()
    assert rows == [(0.0515, "2026-10-19", "treasury_gov")] * 2


def test_a_collection_run_keeps_its_warnings(tmp_path):
    # The runs table kept only their count, so the reason for an empty record (BUG-044) never reached the web app.
    stock = StockData(ticker="TSM", cik="0001046179", collected_at=datetime(2026, 10, 20, 9, 30))
    stock.add_warning("[data-quality/high] No annual financial data extracted.")
    stock.add_warning("SEC EDGAR: no annual financials for TSM from CIK 0001046179 (TSMC): it reports in IFRS")
    SQLiteStore(tmp_path / "s.db").export([stock])
    conn = sqlite3.connect(tmp_path / "s.db")
    rows = conn.execute("SELECT warning_count, warnings FROM collection_runs").fetchall()
    conn.close()
    assert [(count, json.loads(text)) for count, text in rows] == [(2, stock.warnings)]



def _filer(annual=None, quarterly=None, ttm=None, vintages=None):
    """AAA as a collection's SEC parse gives it: the annual record, quarters, trailing years and vintages."""
    s = StockData(ticker="AAA", cik="000", company_name="AAA Inc.")
    s.financials_annual = annual or {}
    s.financials_quarterly = quarterly or {}
    s.financials_ttm = ttm or {}
    s.financials_annual_vintages = vintages or {}
    s.add_source("sec_edgar")
    return s


def _stored(db, table, column):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute(f"SELECT {column} FROM {table} WHERE ticker = 'AAA'")]
    finally:
        conn.close()


def test_a_recollection_clears_what_its_parse_leaves_empty(tmp_path):
    # BUG-029: a re-parse that leaves a field empty (a rule fixed since, BUG-013's dividends or BUG-092's counts)
    # upserted only the fields it had, so the old figure stayed: the user's update on 2026-10-21 kept about 500,
    # WFC's 2017-06-30 acquisitions quarter and MSFT's zero impairments among them.
    db = tmp_path / "stock.db"
    year = {"fiscal_year": 2024, "period_end": "2024-12-31", "revenue": 1000.0}
    quarter = {"fiscal_year": 2024, "period_end": "2024-12-31", "revenue": 250.0}
    SQLiteStore(db).export([_filer(annual={"2024": {**year, "impairment": 0.0}},
                                   quarterly={"2024-12-31": {**quarter, "capex": 43.0}},
                                   ttm={"2024-12-31": {**quarter, "capex": 406.0}},
                                   vintages={"2024": {"0001-25-000001": {**year, "impairment": 0.0}}})])
    SQLiteStore(db).export([_filer(annual={"2024": year}, quarterly={"2024-12-31": quarter},
                                   ttm={"2024-12-31": quarter}, vintages={"2024": {"0001-25-000001": year}})])
    assert _stored(db, "financials_annual", "impairment") == [None]
    assert _stored(db, "financials_quarterly", "capex") == [None]
    assert _stored(db, "financials_ttm", "capex") == [None]
    assert _stored(db, "financials_annual_vintages", "impairment") == [None]
    assert _stored(db, "financials_annual", "revenue") == [1000.0]


def test_a_recollection_with_no_annual_record_only_adds(tmp_path):
    # XOM's ticker now reads its new holding company, which has filed no 10-K: its 10-Qs give a few figures for
    # quarters whose full record the predecessor filed, so a run without an annual record clears nothing.
    db = tmp_path / "stock.db"
    quarter = {"fiscal_year": 2025, "period_end": "2025-03-31"}
    SQLiteStore(db).export([_filer(annual={"2024": {"fiscal_year": 2024, "revenue": 1000.0}},
                                   quarterly={"2025-03-31": {**quarter, "revenue": 83.1e9, "net_income": 7.7e9}})])
    SQLiteStore(db).export([_filer(quarterly={"2025-03-31": {**quarter, "net_income": 7.7e9}})])
    assert _stored(db, "financials_quarterly", "revenue") == [83.1e9]
