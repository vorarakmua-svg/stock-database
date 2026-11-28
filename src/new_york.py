"""New York's clock: the U.S. market's (a session closes at 16:00 there) and the SEC's (a filing is dated by its day
there). Without a tz database (Windows has none without ``tzdata``): the U.S. daylight-saving rule since 2007."""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

#: The U.S. market closes at 16:00 New York time.
MARKET_CLOSE_HOUR = 16


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def new_york_clock(moment: datetime) -> datetime:
    """*moment* as New York wall-clock time (naive): EDT (UTC-4) from the second Sunday of March 2:00 to the first
    Sunday of November 2:00, EST (UTC-5) otherwise. A naive *moment* is this machine's local time, as the collector
    stamps its runs (``collection_runs.collected_at``, ``sec_read_at``)."""
    now = moment.astimezone(timezone.utc)
    year = now.year
    dst_start = datetime(year, 3, _nth_sunday(year, 3, 2).day, 7, tzinfo=timezone.utc)  # 2:00 EST
    dst_end = datetime(year, 11, _nth_sunday(year, 11, 1).day, 6, tzinfo=timezone.utc)  # 2:00 EDT
    offset = -4 if dst_start <= now < dst_end else -5
    return (now + timedelta(hours=offset)).replace(tzinfo=None)


def new_york_day(moment: datetime) -> date:
    """*moment*'s day in New York (``new_york_clock``): the SEC's day, by which a filing is dated."""
    return new_york_clock(moment).date()


def new_york_day_of(stamp: Optional[str]) -> Optional[str]:
    """The ISO day in New York of a stored time stamp (``market_snapshots.collected_at``; a naive one is this
    machine's, as ``new_york_clock`` reads it): the exchange's day, on which splits and payments are dated (BUG-079).
    A day alone stays that day, and a stamp that can't be read is taken as its first ten characters. None for none."""
    text = str(stamp or "").strip()
    if not text:
        return None
    if len(text) <= 10:
        return text
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return text[:10]
    return new_york_day(moment).isoformat()


def taken_before_close(day: str, moment: datetime) -> bool:
    """Whether *moment* (``new_york_clock``) came before the close of the session of *day* (ISO; a time of day after
    it is ignored): before 16:00 in New York on that day, or on an earlier day."""
    ny = new_york_clock(moment)
    return (ny.date().isoformat(), ny.hour) < (str(day)[:10], MARKET_CLOSE_HOUR)
