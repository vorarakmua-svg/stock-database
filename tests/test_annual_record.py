"""The annual record is one function of a filer's companyfacts: the fetcher keeps what it builds."""

import pytest

from src.parsers.annual_record import AnnualRecord, build_annual_record
from src.parsers.xbrl_parser import XBRLParser
from tests.test_fetcher_concurrency import _collect_from_sec


def _fy(val, year, filed):
    return {"val": val, "start": f"{year}-01-01", "end": f"{year}-12-31", "fy": year, "fp": "FY", "form": "10-K",
            "filed": filed, "accn": filed}


def _revenues():
    return {"units": {"USD": [_fy(1.0e10, y, f"{y + 1}-08-01") for y in (2021, 2022, 2023)]}}


def _repaired():
    # BUG-061's shape: the 2024 filing tags FY2022's dividend a million times too large; earlier filings agree on 2.77.
    dps = [_fy(2.68, 2021, "2021-08-06"), _fy(2.77, 2022, "2022-08-05"), _fy(2.77, 2022, "2023-08-04"),
           _fy(2770000.0, 2022, "2024-08-02"), _fy(2.80, 2023, "2024-08-02")]
    return {"facts": {"us-gaap": {"CommonStockDividendsPerShareDeclared": {"units": {"USD/shares": dps}},
                                  "Revenues": _revenues()}}}


def _rescaled():
    # FY2022's diluted count is filed in thousands: its EPS (2.00 on 1e9 of net income) says 500 million shares.
    def per_year(values, unit):
        return {"units": {unit: [_fy(v, y, f"{y + 1}-08-01") for y, v in values.items()]}}

    return {"facts": {"us-gaap": {
        "Revenues": _revenues(),
        "NetIncomeLoss": per_year({2021: 1.0e9, 2022: 1.0e9, 2023: 1.0e9}, "USD"),
        "EarningsPerShareDiluted": per_year({2021: 2.0, 2022: 2.0, 2023: 2.0}, "USD/shares"),
        "WeightedAverageNumberOfDilutedSharesOutstanding": per_year({2021: 5.0e8, 2022: 5.0e5, 2023: 5.0e8},
                                                                    "shares")}}}


def _invalid():
    # A figure that isn't a number: the record keeps the period as filed and says why it couldn't be validated.
    rd = [_fy("n/a", 2022, "2023-08-01")]
    return {"facts": {"us-gaap": {"Revenues": _revenues(), "ResearchAndDevelopmentExpense": {"units": {"USD": rd}}}}}


#: Each record's own path, and how its warning begins.
CASES = [("STX", _repaired, "SEC EDGAR: STX FY2022 dividends_per_share is 2.77"),
         ("RSC", _rescaled, "share scale financials_annual 2022: rescaled weighted_avg_shares_diluted"),
         ("BAD", _invalid, "validation financials_annual 2022: rd_expense")]


def _annual(warning, ticker):
    return warning.startswith((f"SEC EDGAR: {ticker} FY", "share scale financials_annual ",
                               "validation financials_annual "))


@pytest.mark.parametrize("ticker, facts, expected", CASES)
def test_the_record_equals_the_fetchers_annual_record(tmp_path, monkeypatch, ticker, facts, expected):
    stock = _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0001137789", "ticker": ticker, "source": "sec_edgar", "company_facts": facts(),
        "submissions": {"name": f"{ticker} Inc.", "filings": []}})
    record = build_annual_record(XBRLParser(), facts(), ticker=ticker, years_back=None)
    assert isinstance(record, AnnualRecord)
    assert record.periods == stock.financials_annual
    assert all("_source_tags" in p and "_source_filings" in p for p in record.periods.values())
    assert record.warnings == [w for w in stock.warnings if _annual(w, ticker)]
    assert any(w.startswith(expected) for w in record.warnings)


def test_years_back_keeps_the_latest_years():
    record = build_annual_record(XBRLParser(), _repaired(), ticker="STX", years_back=2)
    assert sorted(record.periods) == ["2022", "2023"]


def test_the_fetcher_keeps_the_order_of_its_warnings(tmp_path, monkeypatch):
    # The repairs are the parse's; the other warnings of the SEC block (here the predecessor's) come before the
    # cleaning's, as they did before the record was one function.
    facts = _rescaled()
    facts["facts"]["us-gaap"].update(_repaired()["facts"]["us-gaap"])
    stock = _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0001137789", "ticker": "STX", "source": "sec_edgar", "company_facts": facts,
        "predecessor_cik": "0000000001", "predecessor_name": "STX Old Inc.",
        "submissions": {"name": "STX Inc.", "filings": []}})
    expected = ["SEC EDGAR: STX FY2022 dividends_per_share is 2.77", "SEC EDGAR: STX reads CIK 0001137789",
                "share scale financials_annual 2022: rescaled"]
    sec = [w for w in stock.warnings if w.startswith(("SEC EDGAR", "share scale"))]
    assert len(sec) == len(expected) and all(w.startswith(e) for w, e in zip(sec, expected))
