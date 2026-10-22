"""Our companies' CUSIPs (Milestone 3): the curated map, checked against OpenFIGI and the 13F issuer names
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md; qa/evidence/ownership-m3-5-universe-review.txt).
"""
from src.ownership.cusips import UNIVERSE_CUSIPS


def test_each_code_is_a_nine_character_cusip_or_cins():
    assert len(UNIVERSE_CUSIPS) >= 120
    assert all(len(code) == 9 and code.isalnum() and code == code.upper() for code in UNIVERSE_CUSIPS)


def test_a_security_whose_cusip_changed_keeps_both_codes():
    assert UNIVERSE_CUSIPS["438516106"] == UNIVERSE_CUSIPS["438516205"] == "HON"  # the 2026 reverse split
    assert UNIVERSE_CUSIPS["30231G102"] == UNIVERSE_CUSIPS["30233Q108"] == "XOM"  # ExxonMobil Holdings
    assert UNIVERSE_CUSIPS["200021014"] == UNIVERSE_CUSIPS["020002101"] == "ALL"  # a filer's typo


def test_each_share_class_keeps_its_own_ticker():
    assert UNIVERSE_CUSIPS["02079K305"] == "GOOGL" and UNIVERSE_CUSIPS["02079K107"] == "GOOG"
    assert UNIVERSE_CUSIPS["084670702"] == "BRK-B" and UNIVERSE_CUSIPS["084670108"] == "BRK-A"
    assert "060505682" not in UNIVERSE_CUSIPS  # Bank of America's convertible preferred isn't its common stock


def test_a_foreign_issuers_cins_is_mapped():
    assert UNIVERSE_CUSIPS["H1467J104"] == "CB" and UNIVERSE_CUSIPS["G54950103"] == "LIN"
