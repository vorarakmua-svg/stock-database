"""BUG-154 (decided 2026-11-26): a payment Yahoo served twice, the same amount ex-dated within a week, is one."""
import math

import pytest

from src.dividend_payments import SAME_PAYMENT_DAYS, distinct_payments


def _distinct(pairs):
    return distinct_payments(pairs, lambda p: p[0], lambda p: p[1])


def test_kos_2001_payments_read_once():
    # KO's 2001 as stored: the 0.18 of 2001-09-10 again on 09-12 (a day the market was shut), beside adjusted 0.09s.
    ko = [("2001-03-13", 0.09), ("2001-06-13", 0.09), ("2001-09-10", 0.18), ("2001-09-12", 0.18),
          ("2001-09-17", 0.09), ("2001-11-28", 0.09)]
    assert _distinct(ko) == [("2001-03-13", 0.09), ("2001-06-13", 0.09), ("2001-09-10", 0.18),
                             ("2001-09-17", 0.09), ("2001-11-28", 0.09)]


@pytest.mark.parametrize("later, kept", [
    ("2015-05-06", False),   # NSC's: 7 days on
    ("2015-04-29", False),   # the same day, as a timestamp of another time
    ("2015-05-07", True),    # 8 days on
])
def test_the_same_amount_is_the_same_payment_within_a_week(later, kept):
    first = ("2015-04-29T00:00:00", 0.59)
    assert SAME_PAYMENT_DAYS == 7
    assert _distinct([first, (later, 0.59)]) == ([first, (later, 0.59)] if kept else [first])


def test_other_amounts_and_unreadable_rows_are_kept():
    pairs = [("2020-01-02", 0.5), ("2020-01-03", 0.25), ("2020-01-04", 0.5000001), ("bad", 0.5), ("2020-01-05", None),
             ("2020-01-06", math.nan)]
    assert _distinct(pairs) == pairs   # another amount by a ten-millionth, an unreadable day, no amount, NaN


def test_each_payment_is_measured_against_the_one_kept():
    # A third served 10 days after the first is a payment of its own, even 5 days after the dropped second.
    rows = [{"date": "2020-01-01", "amount": 1.0}, {"date": "2020-01-06", "amount": 1.0},
            {"date": "2020-01-11", "amount": 1.0}]
    assert [r["date"] for r in distinct_payments(rows, lambda r: r["date"], lambda r: r["amount"])] == [
        "2020-01-01", "2020-01-11"]
