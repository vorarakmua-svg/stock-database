"""Tests for SIC -> sector classification."""

import pytest

from src.mappings.sectors import (
    BANK,
    ENERGY,
    GENERAL,
    INSURANCE,
    REIT,
    UTILITY,
    classify_sic,
    classify_submissions,
)


@pytest.mark.parametrize("sic,expected", [
    (6021, BANK), (6022, BANK), (6199, BANK), (6211, BANK), (6712, BANK),
    (6311, INSURANCE), (6331, INSURANCE), (6411, INSURANCE),
    (6798, REIT),
    (4911, UTILITY), (4931, UTILITY),
    (2911, ENERGY), (1311, ENERGY),
    (3571, GENERAL), (5331, GENERAL), (7372, GENERAL),
    ("6021", BANK), (None, GENERAL), ("bogus", GENERAL),
])
def test_classify_sic(sic, expected):
    assert classify_sic(sic) == expected


def test_classify_submissions():
    assert classify_submissions({"sic": "6798"}) == REIT
    assert classify_submissions({"sic": 6311}) == INSURANCE
    assert classify_submissions({}) == GENERAL
    assert classify_submissions(None) == GENERAL


def _facts(*tags):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": [{"val": 1.0e9, "end": "2025-12-31"}]}} for tag in tags}}}


def test_a_health_insurer_is_valued_as_a_general_company():
    # Q9 (decided): SIC 6324, hospital and medical service plans (UNH), is managed care, valued on its earnings.
    assert classify_sic(6324) == GENERAL
    assert classify_sic(6311) == INSURANCE and classify_sic(6331) == INSURANCE


def test_a_finance_sic_without_deposits_is_a_general_company():
    # Q9 (decided): SIC 6020-6299 holds asset managers (BLK 6211; BX, KKR 6282) beside banks. A bank also has
    # deposits on its balance sheet; without them it is valued as a general company.
    assert classify_submissions({"sic": 6282}, _facts("Revenues", "NetIncomeLoss")) == GENERAL
    assert classify_submissions({"sic": 6211}, _facts("Revenues")) == GENERAL
    assert classify_submissions({"sic": 6021}, _facts("Deposits", "NetIncomeLoss")) == BANK
    assert classify_submissions({"sic": 6211}, _facts("InterestBearingDepositLiabilities")) == BANK


def test_without_the_facts_the_sic_decides():
    # No companyfacts to look in (a failed request, the coverage report): the SIC's class, as before.
    assert classify_submissions({"sic": 6282}) == BANK
    assert classify_submissions({"sic": 6282}, None) == BANK
