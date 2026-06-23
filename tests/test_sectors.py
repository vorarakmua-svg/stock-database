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
