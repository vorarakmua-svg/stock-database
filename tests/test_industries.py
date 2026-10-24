"""SIC codes and industry groups (Milestone 5): src/universe/industries.py.

docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and industries"
"""
import pytest

from src.universe.industries import (
    LABEL_DESCRIPTIONS,
    MIN_INDUSTRY_SIZE,
    group_label,
    group_members,
    industry_groups,
    industry_options,
    normalize_sic,
    normalize_sic_description,
    sic_label,
)


def _many(code, n, prefix):
    """*n* companies (prefix0, prefix1, ...) with the same SIC *code*."""
    return {f"{prefix}{i}": code for i in range(n)}


def test_the_spec_s_figures():
    assert MIN_INDUSTRY_SIZE == 10 and LABEL_DESCRIPTIONS == 3


@pytest.mark.parametrize("value, code", [
    ("3571", "3571"), (" 3571 ", "3571"), (3571, "3571"), ("6021", "6021"),
    ("100", "0100"),  # a code whose leading zero was dropped
    (None, None), ("", None), ("   ", None),
    ("35712", None), ("35a1", None), ("35.7", None), ("-357", None),
    ("\uff13\uff15\uff17\uff11", None),  # full-width digits are no SIC code
])
def test_a_code_is_up_to_four_ascii_digits_kept_as_four(value, code):
    assert normalize_sic(value) == code


@pytest.mark.parametrize("value, description", [
    ("Electronic Computers", "Electronic Computers"), ("  Electronic Computers ", "Electronic Computers"),
    ("Retail-Eating  Places", "Retail-Eating  Places"),  # as the SEC writes it, inner spaces kept
    ("", None), ("  ", None), (None, None),
])
def test_a_description_is_the_sec_s_text(value, description):
    assert normalize_sic_description(value) == description


def test_ten_sharing_a_3_digit_prefix_form_its_group():
    codes = {**_many("2834", 6, "P"), **_many("2836", 4, "B")}  # ten in 283
    groups = industry_groups(codes)
    assert groups == {t: "283" for t in codes}


def test_nine_are_not_enough_for_either_prefix():
    codes = _many("2834", 9, "P")
    assert industry_groups(codes) == {t: None for t in codes}


def test_a_small_industry_falls_back_to_its_2_digit_group():
    codes = {**_many("2834", 10, "P"), "LIN": "2810"}  # 283 has ten, 281 one, 28 eleven
    groups = industry_groups(codes)
    assert groups["LIN"] == "28"
    assert all(groups[f"P{i}"] == "283" for i in range(10))


def test_the_2_digit_group_needs_ten_too():
    nine = {**_many("2834", 8, "P"), "LIN": "2810"}  # 28 has nine
    assert set(industry_groups(nine).values()) == {None}
    ten = {**nine, "PG": "2840"}  # now ten
    assert set(industry_groups(ten).values()) == {"28"}


def test_the_3_digit_group_comes_before_the_2_digit_one():
    codes = {**_many("2834", 10, "P"), **_many("2810", 10, "C")}  # 28 has twenty, 283 and 281 ten each
    groups = industry_groups(codes)
    assert {groups[f"P{i}"] for i in range(10)} == {"283"}
    assert {groups[f"C{i}"] for i in range(10)} == {"281"}


def test_a_company_without_a_code_has_no_group_and_counts_for_none():
    codes = {**_many("2834", 9, "P"), "N": None, "E": "", "X": "abcd"}
    groups = industry_groups(codes)
    assert groups == {t: None for t in codes}
    groups = industry_groups({**codes, "P9": "2834"})
    assert (groups["N"], groups["E"], groups["X"], groups["P9"]) == (None, None, None, "283")


def test_the_size_can_be_given():
    codes = {"A": "3571", "B": "3572", "C": "3523"}
    assert industry_groups(codes, min_size=2) == {"A": "357", "B": "357", "C": "35"}
    assert industry_groups(codes, min_size=4) == {"A": None, "B": None, "C": None}


def test_the_codes_are_read_as_codes():
    codes = {**{f"P{i}": " 2834 " for i in range(5)}, **{f"Q{i}": 2834 for i in range(5)}}
    assert set(industry_groups(codes).values()) == {"283"}


def test_a_group_s_members_are_every_code_starting_with_it():
    codes = {"A": "2834", "B": "2810", "C": "3571", "D": None, "E": "2836"}
    assert group_members(codes, "28") == ["A", "B", "E"]  # a 2-digit group takes in its 3-digit groups' companies
    assert group_members(codes, "283") == ["A", "E"]
    assert group_members(codes, "357") == ["C"]
    assert group_members(codes, "36") == []


def test_the_label_names_the_most_common_descriptions_first():
    descriptions = (["Pharmaceutical Preparations"] * 3 + ["Biological Products"] * 2 + ["In Vitro Diagnostics"] * 2
                    + ["Animal Health"] + [None, ""])
    assert group_label("283", descriptions) == (
        "SIC 283: Pharmaceutical Preparations; Biological Products; In Vitro Diagnostics")


def test_the_label_breaks_ties_alphabetically():
    assert group_label("28", ["Soap", "Chemicals", "Plastics"]) == "SIC 28: Chemicals; Plastics; Soap"
    assert group_label("28", ["Soap", "Chemicals", "Soap"]) == "SIC 28: Soap; Chemicals"  # the count comes first


def test_the_label_names_three_at_most():
    assert group_label("73", ["D", "C", "B", "A"]) == "SIC 73: A; B; C"


def test_a_label_without_descriptions_is_the_code():
    assert group_label("357", []) == "SIC 357"
    assert group_label("357", [None, ""]) == "SIC 357"


def test_the_des_label():
    assert sic_label("3571", "Electronic Computers") == "SIC 3571 Electronic Computers"
    assert sic_label("3571", None) == "SIC 3571"
    assert sic_label(None, "Electronic Computers") is None
    assert sic_label("", "Electronic Computers") is None


def test_the_options_are_every_2_and_3_digit_prefix_with_a_company():
    companies = [("2834", "Pharm"), ("2836", "Bio"), ("3571", "Computers"), ("2834", "Pharm"), (None, None),
                 ("", "Nothing")]
    assert industry_options(companies) == [
        {"prefix": "28", "size": 3, "label": "SIC 28: Pharm; Bio"},
        {"prefix": "283", "size": 3, "label": "SIC 283: Pharm; Bio"},
        {"prefix": "35", "size": 1, "label": "SIC 35: Computers"},
        {"prefix": "357", "size": 1, "label": "SIC 357: Computers"},
    ]
    assert industry_options([]) == []
