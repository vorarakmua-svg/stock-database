"""The web UI's number formats (BUG-137, BUG-138)."""
from src.webapp.formatting import fmt_count, fmt_money, fmt_price, fmt_signed_price, fmt_sources


def test_a_negative_price_puts_its_sign_before_the_dollar():
    # BUG-137: DES read ALL's change "$-10.87" beside statements' "-$1.63B".
    assert fmt_price(-10.87) == "-$10.87"
    assert fmt_price(1234.5) == "$1,234.50"
    assert fmt_signed_price(-9.17) == "-$9.17"
    assert fmt_price(None) == "—"


def test_a_trillion_or_more_reads_in_trillions():
    # BUG-138: MSFT's market cap read "$3676.66B".
    assert fmt_money(3_676_657_156_096) == "$3.68T"
    assert fmt_money(-1.2e12) == "-$1.20T"
    assert fmt_money(999.99e9) == "$999.99B"
    assert fmt_count(14_590_000_000) == "14.59B"


def test_the_quality_page_names_where_the_rate_came_from():
    # BUG-070: "fred_treasury_gov" read as if FRED had been consulted; the stored value stays, the display
    # says Treasury.gov.
    assert fmt_sources("yahoo_finance, sec_edgar, fred_treasury_gov, calculated_metrics") == (
        "yahoo_finance, sec_edgar, treasury_gov (rate), calculated_metrics")
    assert fmt_sources("fred_estimate") == "rate_estimate (static)"
    assert fmt_sources("fred_fred") == "fred"
    assert fmt_sources("") == "—" and fmt_sources(None) == "—"
