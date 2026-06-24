"""The shared canonical-field resolver used by both metric modules."""

from src.parsers.metric_utils import field_value


def test_returns_first_present_key():
    data = {"b": 2.0, "c": 3.0}
    assert field_value(data, ["a", "b", "c"]) == 2.0


def test_skips_none_and_coerces_numeric_strings():
    data = {"a": None, "b": "4"}
    assert field_value(data, ["a", "b"]) == 4.0


def test_missing_keys_return_none():
    assert field_value({"a": 1.0}, ["x", "y"]) is None


def test_non_numeric_value_is_skipped():
    assert field_value({"a": "not-a-number", "b": 5.0}, ["a", "b"]) == 5.0
