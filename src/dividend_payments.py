"""One payment per payment: Yahoo serves some dividends twice, the same amount ex-dated a few days apart (BUG-154: KO's
and PGR's 2001-09-10 and 2001-09-12, the second on a day the market was shut; HD 2001-11-27/28, AXP and MDT
1987-12-31/1988-01-04, NSC 2015-04-29/05-06, ...), and ``dividend_events`` keeps both, since its key is the ex-date.
Every reader of the stored payments takes them through ``distinct_payments``."""
import math
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, TypeVar

#: Decided 2026-11-26 (BUG-154): a payment of the same amount as one ex-dated up to this many days before it is that
#: payment served twice. A week is under any real schedule's spacing (the shortest, a monthly payer's, is about 28
#: days), and every same-amount pair within it in the user's database is one payment (12 pairs, up to 7 days apart).
SAME_PAYMENT_DAYS = 7

T = TypeVar("T")


def _day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _same_amount(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def distinct_payments(payments: Sequence[T], day: Callable[[T], Any], amount: Callable[[T], Any]) -> List[T]:
    """*payments* in their order less each one whose *amount* equals that of a payment kept and ex-dated (*day*, an
    ISO date or timestamp) up to SAME_PAYMENT_DAYS before it: the earlier ex-date stands. A payment whose day doesn't
    parse or whose amount isn't a number is kept as it is (the readers have their own rules for those)."""
    kept: List[T] = []
    last: Dict[float, date] = {}  # each amount's latest kept ex-date
    for payment in payments:
        on, paid = _day(day(payment)), amount(payment)
        if on is None or not isinstance(paid, (int, float)) or not math.isfinite(paid):
            kept.append(payment)
            continue
        if any(_same_amount(paid, other) and 0 <= (on - when).days <= SAME_PAYMENT_DAYS
               for other, when in last.items()):
            continue
        kept.append(payment)
        last[paid] = on
    return kept
