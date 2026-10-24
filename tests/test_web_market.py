"""The market page and its API (Milestone 4, M4-4): ``webapp.market``'s views over the market data M4-3 stores
(``market_series``, ``market_fetches``): the Buffett indicator against its long-run trend, Shiller's CAPE against its
history and the Treasury yield curve with its 10-year spreads, served by ``GET /api/market`` and the page ``/market``.

docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md ("The market views")
"""
import html as htmllib
import json
import math
import re
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.exporters.sqlite_store import SQLiteStore
from src.market.sources import TREASURY_COLUMNS, fred_url, treasury_url
from src.webapp import create_app
from src.webapp.market import (
    BUFFETT_CUTOFFS,
    BUFFETT_ZONES,
    COLLECT_COMMAND,
    EPISODE_GAP,
    EPISODE_MIN_DAYS,
    MATURITIES,
    ZONE_PILLS,
    buffett_zone,
    latest_episode,
    market_view,
)
from src.webapp.repository import Reader

#: Each source's market_fetches row as the collector writes it: its URL and when its answer was fetched.
FETCHES = {
    "fred:NCBEILQ027S": (fred_url("NCBEILQ027S"), "2026-10-24T14:46:50+00:00"),
    "fred:GDP": (fred_url("GDP"), "2026-10-24T14:34:32+00:00"),
    "shiller": ("https://img1.wsimg.com/blobby/go/e5e7/downloads/1449/ie_data.xls?ver=1791297298731",
                "2026-10-23T09:00:00+00:00"),
    "treasury": (treasury_url(2026), "2026-10-22T18:00:00+00:00"),
}
#: The designed Buffett ratio: 80% in 1990-Q1 on a trend of 5% a year, 40 quarters to 1999-Q4.
FIRST_YEAR, N, START, GROWTH = 1990, 40, 80.0, 0.05
#: Shiller's CAPE, 40 months from 1881-01 to 1884-04: 10 to 48, then the latest, 30.5, at or above 22 of the 40.
CAPE_VALUES = [10.0 + i for i in range(39)] + [30.5]
#: A Treasury curve a year apart: 2025-10-23 (the latest day less 365) is no trading day, so the curve a year earlier is
#: 2025-10-22's, not 2025-10-24's. On the latest day the 30-year has no yield (its last is the day before), the 20-year
#: none since 2025 and the 1.5-month none before 2026; the 2-, 4- and 6-month, 3-, 5- and 7-year are never stored.
CURVE = {
    "30y": [("2025-10-21", 4.62), ("2025-10-22", 4.60), ("2025-10-24", 4.58), ("2026-10-22", 5.30)],
    "3m": [("2025-10-21", 3.91), ("2025-10-22", 3.90), ("2025-10-24", 3.89), ("2026-10-22", 4.21),
           ("2026-10-23", 5.10)],
    "10y": [("2025-10-21", 4.01), ("2025-10-22", 4.00), ("2025-10-24", 3.99), ("2026-10-22", 4.98),
            ("2026-10-23", 5.00)],
    "1m": [("2025-10-21", 4.06), ("2025-10-22", 4.05), ("2025-10-24", 4.04), ("2026-10-22", 4.09),
           ("2026-10-23", 4.10)],
    "2y": [("2025-10-21", 3.61), ("2025-10-22", 3.60), ("2025-10-24", 3.59), ("2026-10-22", 4.68),
           ("2026-10-23", 4.69)],
    "6w": [("2026-10-22", 4.11), ("2026-10-23", 4.12)],
    "20y": [("2025-10-21", 4.52), ("2025-10-22", 4.50), ("2025-10-24", 4.49)],
    "1y": [("2025-10-21", 3.81), ("2025-10-22", 3.80), ("2025-10-24", 3.79), ("2026-10-22", 4.29),
           ("2026-10-23", 4.30)],
}
LATEST_CURVE = [("1m", "1 mo", 4.10), ("6w", "1.5 mo", 4.12), ("3m", "3 mo", 5.10), ("1y", "1 yr", 4.30),
                ("2y", "2 yr", 4.69), ("10y", "10 yr", 5.00)]
YEAR_AGO_CURVE = [("1m", "1 mo", 4.05), ("3m", "3 mo", 3.90), ("1y", "1 yr", 3.80), ("2y", "2 yr", 3.60),
                  ("10y", "10 yr", 4.00), ("20y", "20 yr", 4.50), ("30y", "30 yr", 4.60)]
#: Ten trading days of the 10-year, the 2-year (none on the 4th) and the 3-month (none on the 7th) for the spreads.
SPREAD_DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13",
               "2026-01-14", "2026-01-15", "2026-01-16"]
SPREAD_YIELDS = {
    "10y": [4.00] * 10,
    "2y": [4.10, 4.10, 4.20, None, 4.00, 4.30, 4.40, 3.90, 4.05, 3.95],
    "3m": [4.20, 4.30, 3.90, 4.10, 4.20, 4.40, None, 4.50, 4.60, 4.70],
}
#: What the page shows for a figure that isn't there: an em dash.
DASH = chr(0x2014)


# ---- seeding ------------------------------------------------------------------------------------------------------


def _fresh(path):
    """A database as the store creates it: every table, the market ones empty."""
    store = SQLiteStore(db_path=path)
    conn = store._connect()
    store._create_schema(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(tmp_path):
    return _fresh(tmp_path / "market.db")


def _client(db):
    return TestClient(create_app(db_path=db))


def _store(db, source, series):
    url, fetched_at = FETCHES[source]
    SQLiteStore(db_path=db).export_market_series(source, series, url, fetched_at)


def _drop_market_tables(db):
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE IF EXISTS market_series")
    conn.execute("DROP TABLE IF EXISTS market_fetches")
    conn.commit()
    conn.close()


def _view(db):
    with Reader(db) as r:
        return market_view(r)


def _quarters(first_year, n):
    """n quarters from first_year's first, dated as FRED dates them (the quarter's first day)."""
    return [f"{first_year + i // 4}-{3 * (i % 4) + 1:02d}-01" for i in range(n)]


def _months(first_year, n):
    return [f"{first_year + i // 12}-{i % 12 + 1:02d}-01" for i in range(n)]


def _residuals(z, n=N, s=0.05):
    """Residuals a least-squares line leaves as they are: each equals its mirror's (so they are orthogonal to evenly
    spaced time) and they sum to 0. The first and last are x, the middle two -x, two mirrored pairs +s and two -s, x
    chosen so that the last is z standard deviations: z = x / sqrt((4x^2 + 8s^2) / (n - 1))."""
    x = z * s * math.sqrt(8 / (n - 1 - 4 * z * z))
    e = [0.0] * n
    for k, v in ((0, x), (n // 2 - 1, -x), (5, s), (10, s), (7, -s), (14, -s)):
        e[k] = e[n - 1 - k] = v
    return e


def _ratios(e, start=START, growth=GROWTH):
    """The ratio each quarter, in percent: start x (1 + growth) ^ the years since the first quarter x e ^ residual."""
    return [start * (1 + growth) ** (i / 4) * math.exp(r) for i, r in enumerate(e)]


def _trend(n=N, start=START, growth=GROWTH):
    return [start * (1 + growth) ** (i / 4) for i in range(n)]


def _fred(ratios, dates, gdp0=1000.0):
    """FRED's two series for *ratios* (percent) on *dates*: GDP ($ billions) rising 1% a quarter from gdp0, and the
    equities ($ millions) at ratio / 100 x GDP x 1000."""
    gdp = [(d, gdp0 * 1.01 ** i) for i, d in enumerate(dates)]
    equities = [(d, r / 100 * g * 1000) for (d, g), r in zip(gdp, ratios)]
    return equities, gdp


def _seed_fred(db, equities, gdp):
    _store(db, "fred:NCBEILQ027S", {"fred:NCBEILQ027S": equities})
    _store(db, "fred:GDP", {"fred:GDP": gdp})


def _seed_buffett(db, z):
    """The designed ratio whose latest quarter is z standard deviations from its trend; its dates, residuals and
    ratios."""
    dates = _quarters(FIRST_YEAR, N)
    e = _residuals(z)
    ratios = _ratios(e)
    _seed_fred(db, *_fred(ratios, dates))
    return dates, e, ratios


def _seed_cape(db, values=CAPE_VALUES, first_year=1881):
    """Shiller's series, CAPE from *first_year*; the other four beside it, which the CAPE section doesn't read."""
    months = _months(first_year, len(values))
    _store(db, "shiller", {"shiller:price": [(m, 4.0 + i) for i, m in enumerate(months)],
                           "shiller:dividend": [(m, 0.2) for m in months],
                           "shiller:earnings": [(m, 0.4) for m in months],
                           "shiller:cpi": [(m, 12.0) for m in months],
                           "shiller:cape": list(zip(months, values))})
    return months


def _seed_treasury(db, curve):
    _store(db, "treasury", {f"ust:{m}": rows for m, rows in curve.items()})


def _seed_spreads(db):
    _seed_treasury(db, {m: [(d, v) for d, v in zip(SPREAD_DAYS, values) if v is not None]
                        for m, values in SPREAD_YIELDS.items()})


def _seed_all(db):
    dates, e, ratios = _seed_buffett(db, 2.5)
    months = _seed_cape(db)
    _seed_treasury(db, CURVE)
    return dates, ratios, months


def _sd(e):
    """The residuals' sample standard deviation, by hand (they sum to 0)."""
    return math.sqrt(sum(r * r for r in e) / (len(e) - 1))


def _business_days(first, n):
    """n weekdays from *first*, ISO."""
    days, day = [], date.fromisoformat(first)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _seed_pattern(db, days, pattern, missing=()):
    """The 10-year at 4.0 each of *days* and the 2-year 0.1 above it on a "-" of *pattern* (10y - 2y below 0), 0.1 under
    it on a "+"; no 2-year yield on the days in *missing*."""
    _seed_treasury(db, {"10y": [(d, 4.0) for d in days],
                        "2y": [(d, 4.1 if c == "-" else 3.9) for d, c in zip(days, pattern) if d not in missing]})


# ---- the reader ---------------------------------------------------------------------------------------------------


def test_the_reader_gives_each_named_series_oldest_first(db):
    _store(db, "treasury", {"ust:10y": [("2026-10-23", 5.0), ("2026-10-21", 4.9), ("2026-10-22", 4.95)],
                            "ust:2y": [("2026-10-23", 4.69)], "ust:1m": [("2026-10-23", 4.11)]})
    with Reader(db) as r:
        got = r.market_series(["ust:10y", "ust:2y", "ust:30y"])
        assert r.market_series([]) == {}
    # Only the series named; one without rows is absent.
    assert got == {"ust:10y": [("2026-10-21", 4.9), ("2026-10-22", 4.95), ("2026-10-23", 5.0)],
                   "ust:2y": [("2026-10-23", 4.69)]}


def test_the_reader_gives_each_sources_latest_fetch(db):
    _seed_all(db)
    with Reader(db) as r:
        fetches = r.market_fetches()
    assert set(fetches) == set(FETCHES)
    assert {s: (f["url"], f["fetched_at"]) for s, f in fetches.items()} == FETCHES
    assert fetches["shiller"]["rows"] == 5 * len(CAPE_VALUES)


def test_the_reader_before_the_market_tables(db):
    _drop_market_tables(db)
    with Reader(db) as r:
        assert r.market_series(["fred:GDP", "ust:10y"]) == {}
        assert r.market_fetches() == {}


# ---- the Buffett indicator ----------------------------------------------------------------------------------------


def test_the_ratio_is_equities_in_millions_over_gdp_in_billions_as_a_percentage(db):
    # Q2 2026 as FRED reads it: $83.05T of corporate equities over $32.56T of GDP.
    dates = ["2025-10-01", "2026-01-01", "2026-04-01"]
    _seed_fred(db, list(zip(dates, [70_000_000.0, 69_000_000.0, 83_050_847.0])),
               list(zip(dates, [31_000.0, 31_500.0, 32_563.0])))
    b = _view(db)["buffett"]
    assert (b["available"], b["date"]) == (True, "2026-04-01")
    assert b["ratio"] == pytest.approx(255.0467, abs=1e-4)
    assert b["series"]["ratio"] == pytest.approx([225.8065, 219.0476, 255.0467], abs=1e-4)


def test_the_trend_is_the_least_squares_line_through_the_log_ratio(db):
    dates, e, ratios = _seed_buffett(db, 2.5)
    b = _view(db)["buffett"]
    trend, sd = _trend(), _sd(e)
    assert (b["date"], b["first_date"], b["quarters"]) == ("1999-10-01", "1990-01-01", N)
    assert b["ratio"] == pytest.approx(ratios[-1], rel=1e-12)
    assert b["trend"] == pytest.approx(START * (1 + GROWTH) ** 9.75, rel=1e-9)
    assert b["growth"] == pytest.approx(GROWTH, rel=1e-9)
    assert b["sd"] == pytest.approx(sd, rel=1e-9)
    s = b["series"]
    assert s["dates"] == dates
    assert s["ratio"] == pytest.approx(ratios, rel=1e-12)
    assert s["trend"] == pytest.approx(trend, rel=1e-9)
    # The bands: the trend x e^(k sd), the residuals being in ln terms.
    assert s["lower_2sd"] == pytest.approx([t * math.exp(-2 * sd) for t in trend], rel=1e-9)
    assert s["lower_1sd"] == pytest.approx([t * math.exp(-sd) for t in trend], rel=1e-9)
    assert s["upper_1sd"] == pytest.approx([t * math.exp(sd) for t in trend], rel=1e-9)
    assert s["upper_2sd"] == pytest.approx([t * math.exp(2 * sd) for t in trend], rel=1e-9)


@pytest.mark.parametrize("z, zone, pill", [
    (-2.5, "significantly undervalued", "cheap"),
    (-1.5, "modestly undervalued", "cheap"),
    (-0.5, "fairly valued", "fair"),
    (0.0, "fairly valued", "fair"),
    (1.5, "modestly overvalued", "expensive"),
    (2.5, "significantly overvalued", "expensive"),
])
def test_the_zone_reads_the_latest_quarters_residual_in_standard_deviations(db, z, zone, pill):
    dates, e, ratios = _seed_buffett(db, z)
    b = _view(db)["buffett"]
    assert (b["available"], b["reason"]) == (True, None)
    assert b["z"] == pytest.approx(z, abs=1e-9)
    assert b["z"] == pytest.approx(e[-1] / _sd(e), abs=1e-9)
    assert (b["zone"], b["pill"]) == (zone, pill)
    assert b["ratio"] == pytest.approx(_trend()[-1] * math.exp(e[-1]), rel=1e-12)


def test_the_zones_cut_offs_and_the_side_each_belongs_to():
    assert BUFFETT_CUTOFFS == (-2.0, -1.0, 1.0, 2.0)
    zs = (-3.0, -2.000001, -2.0, -1.5, -1.000001, -1.0, 0.0, 1.0, 1.000001, 1.5, 2.0, 2.000001, 3.0)
    assert [buffett_zone(z) for z in zs] == [
        "significantly undervalued", "significantly undervalued", "modestly undervalued", "modestly undervalued",
        "modestly undervalued", "fairly valued", "fairly valued", "fairly valued", "modestly overvalued",
        "modestly overvalued", "modestly overvalued", "significantly overvalued", "significantly overvalued"]


def test_each_zones_pill_is_the_verdicts():
    assert BUFFETT_ZONES == ("significantly undervalued", "modestly undervalued", "fairly valued",
                             "modestly overvalued", "significantly overvalued")
    assert [ZONE_PILLS[zone] for zone in BUFFETT_ZONES] == ["cheap", "cheap", "fair", "expensive", "expensive"]


def test_only_the_quarters_both_series_have_are_used(db):
    dates = _quarters(FIRST_YEAR, N)
    e = _residuals(1.5)
    ratios = _ratios(e)
    equities, gdp = _fred(ratios, dates)
    # FRED's equities begin before GDP (1945-Q4 against 1947-Q1) and lag it at the end: quarters off the design,
    # which would move the trend if they were used.
    early = [(d, 9.0e9) for d in _quarters(FIRST_YEAR - 1, 4)]
    late = [(d, 1.0) for d in _quarters(FIRST_YEAR + 10, 2)]
    _seed_fred(db, early + equities, gdp + late)
    b = _view(db)["buffett"]
    assert (b["date"], b["quarters"]) == ("1999-10-01", N)
    assert b["series"]["dates"] == dates
    assert b["z"] == pytest.approx(1.5, abs=1e-9) and b["zone"] == "modestly overvalued"


def test_a_quarter_one_series_lacks_is_left_out(db):
    dates = _quarters(FIRST_YEAR, N)
    ratios = _ratios(_residuals(1.5))
    equities, gdp = _fred(ratios, dates)
    del equities[20]  # NCBEILQ027S is annual before 1951-Q4: GDP's quarters between have no equities
    _seed_fred(db, equities, gdp)
    s = _view(db)["buffett"]["series"]
    assert s["dates"] == dates[:20] + dates[21:]
    assert s["ratio"] == pytest.approx(ratios[:20] + ratios[21:], rel=1e-12)


def test_the_latest_ratios_percentile_over_the_history(db):
    dates, e, ratios = _seed_buffett(db, -2.5)
    b = _view(db)["buffett"]
    at_or_below = sum(1 for r in ratios if r <= ratios[-1])
    assert 20 < at_or_below < N  # the design puts the latest under a few of the last quarters
    assert b["percentile"] == pytest.approx(at_or_below / N)


def test_the_buffett_indicator_needs_both_fred_series(db):
    _store(db, "fred:GDP", {"fred:GDP": [(d, 1000.0 + i) for i, d in enumerate(_quarters(2000, 8))]})
    b = _view(db)["buffett"]
    assert b["available"] is False and "ratio" not in b and "series" not in b
    assert "NCBEILQ027S" in b["reason"] and f"{COLLECT_COMMAND} --fred" in b["reason"]
    assert b["collect"] == f"{COLLECT_COMMAND} --fred"


def test_without_either_fred_series(db):
    b = _view(db)["buffett"]
    assert b["available"] is False
    assert "NCBEILQ027S and GDP" in b["reason"] and b["reason"].endswith(f"{COLLECT_COMMAND} --fred")


def test_the_trend_needs_three_quarters_and_some_spread(db):
    two = _quarters(2000, 2)
    _seed_fred(db, [(d, 1.0e6 * (i + 1)) for i, d in enumerate(two)], [(d, 1000.0) for d in two])
    b = _view(db)["buffett"]
    assert b["available"] is False and "2 quarters" in b["reason"] and "the trend needs 3" in b["reason"]
    assert b["collect"] is None
    # A ratio that never leaves its trend leaves no spread to read a zone from.
    flat = _quarters(2000, 6)
    _seed_fred(db, [(d, 500_000.0) for d in flat], [(d, 1000.0) for d in flat])
    b = _view(db)["buffett"]
    assert b["available"] is False and "spread" in b["reason"]


def _fit_by_hand(ratios):
    """The textbook least squares of ln(ratio) on the years since the first quarter (a quarter apart), on uncentred
    sums: the slope, the intercept, the residuals and their sample standard deviation."""
    n = len(ratios)
    ts = [i / 4 for i in range(n)]
    ys = [math.log(r) for r in ratios]
    st, sy, stt, sty = sum(ts), sum(ys), sum(t * t for t in ts), sum(t * y for t, y in zip(ts, ys))
    slope = (n * sty - st * sy) / (n * stt - st * st)
    intercept = (sy - slope * st) / n
    res = [y - (intercept + slope * t) for t, y in zip(ts, ys)]
    mean = sum(res) / n
    return slope, intercept, res, math.sqrt(sum((r - mean) ** 2 for r in res) / (n - 1))


def test_an_uneven_history_against_a_fit_by_hand(db):
    dates = _quarters(2010, 12)
    ratios = [80.0, 84.0, 79.0, 90.0, 95.0, 88.0, 99.0, 104.0, 98.0, 110.0, 108.0, 120.0]
    _seed_fred(db, *_fred(ratios, dates))
    b = _view(db)["buffett"]
    slope, intercept, res, sd = _fit_by_hand(ratios)
    assert res[0] != pytest.approx(res[-1])  # the latest residual, not the first, is the one read
    assert (b["available"], b["date"], b["ratio"]) == (True, "2012-10-01", pytest.approx(120.0, rel=1e-12))
    assert b["growth"] == pytest.approx(math.exp(slope) - 1, rel=1e-9)
    assert b["trend"] == pytest.approx(math.exp(intercept + slope * 11 / 4), rel=1e-9)
    assert b["sd"] == pytest.approx(sd, rel=1e-9)
    assert b["z"] == pytest.approx(res[-1] / sd, rel=1e-9)
    assert b["zone"] == buffett_zone(res[-1] / sd)
    assert b["percentile"] is None  # fewer than the 36 values window_stats needs


# ---- Shiller's CAPE -----------------------------------------------------------------------------------------------


def test_cape_against_its_history(db):
    months = _seed_cape(db)
    c = _view(db)["cape"]
    assert (c["available"], c["reason"], c["date"], c["value"]) == (True, None, "1884-04-01", 30.5)
    assert (c["first_date"], c["months"]) == ("1881-01-01", 40)
    assert c["mean"] == pytest.approx(1161.5 / 40)  # 29.0375, under the median: the history is skewed
    assert (c["median"], c["min"], c["max"]) == (29.5, 10.0, 48.0)
    assert (c["min_date"], c["max_date"]) == ("1881-01-01", "1884-03-01")
    assert c["percentile"] == pytest.approx(22 / 40)
    assert c["series"] == {"dates": months, "values": CAPE_VALUES}


def test_cape_method_names_shillers_definition(db):
    _seed_cape(db)
    method = _view(db)["cape"]["method"]
    assert "120 months ending the month before" in method
    assert "a single day's close" in method and "estimated CPI" in method


def test_a_short_cape_history_has_no_statistics(db):
    _seed_cape(db, values=[20.0, 22.0, 21.0, 25.0])
    c = _view(db)["cape"]
    assert (c["available"], c["date"], c["value"]) == (True, "1881-04-01", 25.0)
    assert (c["mean"], c["median"], c["min"], c["max"], c["percentile"]) == (None, None, None, None, None)


def test_cape_without_shillers_series(db):
    c = _view(db)["cape"]
    assert c["available"] is False and "series" not in c
    assert "CAPE" in c["reason"] and c["reason"].endswith(f"{COLLECT_COMMAND} --shiller")
    assert c["collect"] == f"{COLLECT_COMMAND} --shiller"


# ---- the yield curve ----------------------------------------------------------------------------------------------


def test_the_maturities_in_order_with_their_labels():
    assert MATURITIES == (("1m", "1 mo"), ("6w", "1.5 mo"), ("2m", "2 mo"), ("3m", "3 mo"), ("4m", "4 mo"),
                          ("6m", "6 mo"), ("1y", "1 yr"), ("2y", "2 yr"), ("3y", "3 yr"), ("5y", "5 yr"),
                          ("7y", "7 yr"), ("10y", "10 yr"), ("20y", "20 yr"), ("30y", "30 yr"))
    # The collector's maturities, in its order.
    assert [m for m, _ in MATURITIES] == list(TREASURY_COLUMNS.values())


def _points(curve):
    return [(p["maturity"], p["label"], p["value"]) for p in curve]


def test_the_curve_on_the_latest_day_against_a_year_earlier(db):
    _seed_treasury(db, CURVE)
    y = _view(db)["yield_curve"]
    assert (y["available"], y["reason"], y["date"], y["first_date"]) == (True, None, "2026-10-23", "2025-10-21")
    # Each maturity with a yield that day, shortest first: the 30-year's latest is the day before, so it is left out.
    assert _points(y["curve"]) == LATEST_CURVE
    # 365 days before 2026-10-23 is 2025-10-23, no trading day: the latest day on or before it.
    assert y["year_ago"]["date"] == "2025-10-22"
    assert _points(y["year_ago"]["curve"]) == YEAR_AGO_CURVE


def test_the_year_ago_curve_is_the_day_365_days_before_when_it_has_one(db):
    curve = {**CURVE, "10y": sorted(CURVE["10y"] + [("2025-10-23", 3.995)])}
    _seed_treasury(db, curve)
    year_ago = _view(db)["yield_curve"]["year_ago"]
    assert year_ago["date"] == "2025-10-23"
    assert _points(year_ago["curve"]) == [("10y", "10 yr", 3.995)]


def test_a_curve_without_a_year_of_history_has_no_year_ago(db):
    _seed_treasury(db, {m: [(d, v) for d, v in rows if d >= "2026-01-01"] for m, rows in CURVE.items()
                        if any(d >= "2026-01-01" for d, _ in rows)})
    y = _view(db)["yield_curve"]
    assert (y["available"], y["date"], y["year_ago"]) == (True, "2026-10-23", None)


def _spread(y, key):
    return next(s for s in y["spreads"] if s["key"] == key)


def test_the_spreads_on_the_days_both_yields_have(db):
    _seed_spreads(db)
    y = _view(db)["yield_curve"]
    assert [(s["key"], s["label"], s["long"], s["short"]) for s in y["spreads"]] == [
        ("10y_2y", "10 yr - 2 yr", "10y", "2y"), ("10y_3m", "10 yr - 3 mo", "10y", "3m")]
    two, three = _spread(y, "10y_2y"), _spread(y, "10y_3m")
    # The 10-year less the other, in percentage points, oldest first; a day either lacks is no day of the spread.
    assert two["dates"] == SPREAD_DAYS[:3] + SPREAD_DAYS[4:]
    assert two["values"] == [-0.1, -0.1, -0.2, 0.0, -0.3, -0.4, 0.1, -0.05, 0.05]
    assert three["dates"] == SPREAD_DAYS[:6] + SPREAD_DAYS[7:]
    assert three["values"] == [-0.2, -0.3, 0.1, -0.1, -0.2, -0.4, -0.5, -0.6, -0.7]
    assert (two["days"], two["first_date"], two["latest_date"], two["latest_value"]) == (
        9, "2026-01-05", "2026-01-16", 0.05)
    assert (three["days"], three["latest_date"], three["latest_value"]) == (9, "2026-01-16", -0.7)


def test_the_spreads_inversions_and_last_inverted_days(db):
    _seed_spreads(db)
    y = _view(db)["yield_curve"]
    two, three = _spread(y, "10y_2y"), _spread(y, "10y_3m")
    assert all("latest_run" not in s for s in y["spreads"])
    # 10y - 2y: below 0 on 6 of its 9 days (a spread of 0 is not inverted): the 5th-7th, the 12th-13th and the 15th,
    # and the 16th is above 0 again: not inverted now, though its first day was. 6 days are no sustained inversion.
    assert (two["inverted"], two["inverted_share"]) == (False, pytest.approx(6 / 9))
    assert (two["last_inverted_day"], two["latest_episode"]) == ("2026-01-15", None)
    # 10y - 3m: inverted now, on 8 of its 9 days.
    assert (three["inverted"], three["inverted_share"]) == (True, pytest.approx(8 / 9))
    assert (three["last_inverted_day"], three["latest_episode"]) == ("2026-01-16", None)


def test_a_spread_never_inverted_and_one_without_days(db):
    _seed_treasury(db, {"10y": [("2026-01-05", 4.0), ("2026-01-06", 4.1)], "2y": [("2026-01-05", 3.5),
                                                                                   ("2026-01-06", 3.6)]})
    y = _view(db)["yield_curve"]
    two, three = _spread(y, "10y_2y"), _spread(y, "10y_3m")
    assert (two["inverted"], two["inverted_share"], two["last_inverted_day"], two["latest_episode"]) == (
        False, 0.0, None, None)
    assert (three["dates"], three["values"], three["days"]) == ([], [], 0)
    assert (three["latest_value"], three["inverted"], three["inverted_share"], three["last_inverted_day"],
            three["latest_episode"]) == (None, None, None, None, None)


def _episode(pattern):
    """latest_episode on a spread drawn as one character a day: "-" below 0, "+" not; day i is "d<i>"."""
    return latest_episode([f"d{i:03d}" for i in range(len(pattern))], [c == "-" for c in pattern])


def test_a_run_of_20_inverted_days_is_a_sustained_inversion_and_one_of_19_is_not():
    assert (EPISODE_GAP, EPISODE_MIN_DAYS) == (20, 20)
    assert _episode("+++" + "-" * 20 + "+++") == {"first": "d003", "last": "d022", "days": 20}
    assert _episode("+++" + "-" * 19 + "+++") is None


def test_runs_merge_across_a_gap_of_20_days_and_split_across_21():
    # Two runs of 10: one episode of 20 inverted days across 20 days above 0; across 21, two of 10, neither sustained.
    assert _episode("-" * 10 + "+" * 20 + "-" * 10) == {"first": "d000", "last": "d039", "days": 20}
    assert _episode("-" * 10 + "+" * 21 + "-" * 10) is None
    # Two sustained episodes 21 days apart: the latest.
    assert _episode("-" * 20 + "+" * 21 + "-" * 20) == {"first": "d041", "last": "d060", "days": 20}


def test_an_episode_counts_its_inverted_days_not_its_span():
    assert _episode("-" * 15 + "+" * 15 + "-" * 4) is None  # 19 inverted days over 34
    assert _episode("-" * 16 + "+" * 15 + "-" * 4) == {"first": "d000", "last": "d034", "days": 20}


def test_blips_after_an_episode_do_not_displace_it():
    # More than 20 days on, blips make an episode of their own, too short to be sustained.
    assert _episode("-" * 30 + "+" * 25 + "-" + "+" * 5 + "-" + "+" * 2) == {
        "first": "d000", "last": "d029", "days": 30}
    # Within 20 days they extend it, as 10y - 2y's blips of 2024-09-03 and 09-05 after its run to 2024-08-26.
    assert _episode("-" * 30 + "+" * 4 + "-" + "+" + "-" + "+" * 30) == {"first": "d000", "last": "d036", "days": 32}


def test_no_inversion_no_episode():
    assert _episode("+" * 30) is None and _episode("") is None


def test_an_episodes_gaps_are_counted_in_the_spreads_own_days(db):
    # 10 days below 0, 22 trading days above it of which the 2-year lacks 2 (20 of the spread's own days), then 10
    # below 0 again: one episode of 20 inverted days. Counted in the 10-year's days, the gap would be 22.
    days = _business_days("2026-01-05", 42)
    pattern = "-" * 10 + "+" * 22 + "-" * 10
    _seed_pattern(db, days, pattern, missing={days[15], days[20]})
    two = _spread(_view(db)["yield_curve"], "10y_2y")
    assert two["days"] == 40
    assert two["latest_episode"] == {"first": days[0], "last": days[41], "days": 20}
    assert two["last_inverted_day"] == days[41]


def test_the_curve_without_the_treasurys_series(db):
    y = _view(db)["yield_curve"]
    assert y["available"] is False and "curve" not in y
    assert "Treasury" in y["reason"] and y["reason"].endswith(f"{COLLECT_COMMAND} --treasury")


# ---- the sources, the empty and the partial databases ------------------------------------------------------------


def test_each_sections_sources_and_latest_date(db):
    _seed_all(db)
    sources = _view(db)["sources"]
    assert set(sources) == {"buffett", "cape", "yield_curve"}
    assert [sources[s]["latest"] for s in ("buffett", "cape", "yield_curve")] == [
        "1999-10-01", "1884-04-01", "2026-10-23"]
    got = {s: [(f["source"], f["url"], f["fetched_at"]) for f in sources[s]["fetches"]] for s in sources}
    assert got == {
        "buffett": [("fred:NCBEILQ027S", *FETCHES["fred:NCBEILQ027S"]), ("fred:GDP", *FETCHES["fred:GDP"])],
        "cape": [("shiller", *FETCHES["shiller"])],
        "yield_curve": [("treasury", *FETCHES["treasury"])],
    }
    assert all(f["name"] for s in sources.values() for f in s["fetches"])


def test_an_empty_database_says_how_to_collect_each_section(db):
    view = _view(db)
    assert view["available"] is False
    for section, flag in (("buffett", "--fred"), ("cape", "--shiller"), ("yield_curve", "--treasury")):
        assert view[section]["available"] is False
        assert f"{COLLECT_COMMAND} {flag}" in view[section]["reason"]
        assert view[section]["date"] is None and view[section]["method"]
        assert view["sources"][section]["latest"] is None
        assert all(f["url"] is None and f["fetched_at"] is None for f in view["sources"][section]["fetches"])


def test_a_database_from_before_the_market_tables(db):
    _drop_market_tables(db)
    view = _view(db)
    assert view["available"] is False
    assert [view[s]["available"] for s in ("buffett", "cape", "yield_curve")] == [False, False, False]
    assert COLLECT_COMMAND in view["yield_curve"]["reason"]


def test_fred_only(db):
    _seed_buffett(db, 1.5)
    view = _view(db)
    assert view["available"] is True
    assert [view[s]["available"] for s in ("buffett", "cape", "yield_curve")] == [True, False, False]
    assert view["cape"]["reason"].endswith("--shiller") and view["yield_curve"]["reason"].endswith("--treasury")
    assert view["sources"]["cape"]["latest"] is None


def test_no_treasury(db):
    _seed_buffett(db, 1.5)
    _seed_cape(db)
    view = _view(db)
    assert [view[s]["available"] for s in ("buffett", "cape", "yield_curve")] == [True, True, False]
    assert view["yield_curve"]["reason"].endswith(f"{COLLECT_COMMAND} --treasury")
    assert view["sources"]["yield_curve"]["latest"] is None


# ---- the API ------------------------------------------------------------------------------------------------------


def test_the_api_gives_the_market_view(db):
    _seed_all(db)
    response = _client(db).get("/api/market")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"available", "buffett", "cape", "yield_curve", "sources"}
    with Reader(db) as r:
        assert body == json.loads(json.dumps(market_view(r)))
    assert (body["buffett"]["zone"], body["cape"]["value"], body["yield_curve"]["date"]) == (
        "significantly overvalued", 30.5, "2026-10-23")
    assert set(body["buffett"]["series"]) == {"dates", "ratio", "trend", "lower_2sd", "lower_1sd", "upper_1sd",
                                              "upper_2sd"}


def test_the_api_answers_200_without_the_data(db):
    client = _client(db)
    for _ in ("empty tables", "no tables"):
        response = client.get("/api/market")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is False
        assert all(COLLECT_COMMAND in body[s]["reason"] for s in ("buffett", "cape", "yield_curve"))
        _drop_market_tables(db)


# ---- the page -----------------------------------------------------------------------------------------------------


def _config(page):
    match = re.search(r'<script type="application/json" id="market-cfg">(.*?)</script>', page, re.S)
    assert match, "no inline chart config"
    return json.loads(match.group(1))


def _page(db):
    response = _client(db).get("/market")
    assert response.status_code == 200
    return response.text


#: The page's sections, in order.
HEADINGS = ["The Buffett indicator", "Shiller's CAPE", "The yield curve"]


def _headings(page):
    return re.findall(r'<h2 class="section-heading">(.*?)</h2>', page)


def test_the_page_shows_the_three_sections_with_their_figures(db):
    dates, ratios, months = _seed_all(db)
    raw = _page(db)
    page = htmllib.unescape(raw)
    assert _headings(page) == HEADINGS
    assert "Error" not in page and "Internal server error" not in page
    # The Buffett indicator: the zone's pill, the ratio, its quarter, the trend, z, the percentile and the growth.
    assert 'class="pill verdict-expensive">Significantly overvalued</span>' in raw
    assert f">{ratios[-1]:.1f}%<" in raw and "Q4 1999" in page
    assert f">{START * (1 + GROWTH) ** 9.75:.1f}%<" in raw
    assert ">+2.50 sd<" in raw and ">100.0%<" in raw and ">5.0% a year<" in raw
    # CAPE: the latest and its month, the mean, median, low, high and percentile.
    assert "CAPE, 1884-04" in page and ">30.5<" in raw
    assert ">29.0<" in raw and ">29.5<" in raw and ">10.0<" in raw and ">48.0<" in raw and ">55.0%<" in raw
    assert "1881-01" in page and "1884-03" in page
    # The yield curve: the two days, the yields, and the spreads, the inverted one with its pill.
    assert "2026-10-23" in page and "2025-10-22" in page
    assert ">5.00%<" in raw and ">4.60%<" in raw
    assert ">+0.31 pp<" in raw and ">-0.10 pp<" in raw
    assert raw.count('class="pill down">Inverted</span>') == 1
    # The spreads table: 10y - 2y never inverted; 10y - 3m inverted on the latest day alone, no sustained inversion.
    assert "<th>Latest sustained inversion</th><th>Last inverted day</th>" in raw
    assert "<td>none</td><td>never</td>" in raw and "<td>none</td><td>2026-10-23</td>" in raw
    # Each section's chart, drawn by app.js.
    for chart in ("market-buffett-chart", "market-cape-chart", "market-curve-chart", "market-spreads-chart"):
        assert f'id="{chart}"' in raw
    assert "renderMarket(" in raw


def test_app_js_draws_the_market_charts(db):
    script = _client(db).get("/static/app.js").text
    assert "function renderMarket(cfg)" in script
    for chart in ("market-buffett-chart", "market-cape-chart", "market-curve-chart", "market-spreads-chart"):
        assert f"'{chart}'" in script


def test_the_page_links_each_sections_source_and_publishes_its_method(db):
    _seed_all(db)
    raw = _page(db)
    page = htmllib.unescape(raw)
    for source in ("fred:NCBEILQ027S", "fred:GDP", "shiller", "treasury"):
        assert f'href="{htmllib.escape(FETCHES[source][0])}"' in raw
    assert "fetched 2026-10-24" in page and "fetched 2026-10-23" in page and "fetched 2026-10-22" in page
    assert "latest quarter 1999-10-01" in page and "latest month 1884-04" in page and "latest day 2026-10-23" in page
    # The method notes, with the zones' published cut-offs.
    assert "least-squares line through ln(ratio)" in page
    assert ("below -2: significantly undervalued; -2 to under -1: modestly undervalued; -1 to +1: fairly valued; "
            "over +1 to +2: modestly overvalued; above +2: significantly overvalued") in page
    assert "120 months ending the month before" in page
    assert "on or before 365 days earlier" in page
    # The inversions' two rules.
    assert "its last inverted day is the latest day it was below 0" in page
    assert ("runs of days below 0 joined across gaps of at most 20 of the spread's days, and the latest sustained "
            "inversion is the latest episode with at least 20 days below 0") in page


def test_the_page_names_the_latest_sustained_inversion(db):
    # 25 days below 0, 25 above, a blip, 14 above: the blip is the last inverted day but not the latest sustained
    # inversion, which is the 25 days'.
    days = _business_days("2026-01-05", 70)
    _seed_pattern(db, days, "+" * 5 + "-" * 25 + "+" * 25 + "-" + "+" * 14)
    raw = _page(db)
    assert f"<td>{days[5]} to {days[29]}, 25 inverted days</td><td>{days[55]}</td>" in raw
    # 10y - 3m has no days: no share and neither figure.
    assert f'<td class="num">{DASH}</td><td>{DASH}</td><td>{DASH}</td></tr>' in raw


def test_the_pages_chart_config(db):
    dates, ratios, months = _seed_all(db)
    cfg = _config(_page(db))
    assert set(cfg) == {"buffett", "cape", "curve", "spreads"}
    assert cfg["buffett"]["dates"] == dates
    assert cfg["buffett"]["ratio"] == pytest.approx(ratios, rel=1e-12)
    assert set(cfg["buffett"]) == {"dates", "ratio", "trend", "lower_2sd", "lower_1sd", "upper_1sd", "upper_2sd"}
    assert cfg["cape"]["dates"] == months and cfg["cape"]["values"] == CAPE_VALUES
    assert (cfg["cape"]["mean"], cfg["cape"]["median"]) == (pytest.approx(29.0375), 29.5)
    # The curve's maturities: those either day has, shortest first.
    assert cfg["curve"]["labels"] == ["1 mo", "1.5 mo", "3 mo", "1 yr", "2 yr", "10 yr", "20 yr", "30 yr"]
    assert cfg["curve"]["today"] == {"date": "2026-10-23", "labels": [p[1] for p in LATEST_CURVE],
                                     "values": [p[2] for p in LATEST_CURVE]}
    assert cfg["curve"]["year_ago"] == {"date": "2025-10-22", "labels": [p[1] for p in YEAR_AGO_CURVE],
                                        "values": [p[2] for p in YEAR_AGO_CURVE]}
    assert [s["label"] for s in cfg["spreads"]] == ["10 yr - 2 yr", "10 yr - 3 mo"]
    assert cfg["spreads"][1]["dates"][-1] == "2026-10-23" and cfg["spreads"][1]["values"][-1] == -0.1


def test_the_inline_config_cannot_close_its_script(db):
    _seed_cape(db, values=[20.0, 21.0])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE market_series SET date = '1881-02-01</script><script>alert(1)</script>' "
                 "WHERE series = 'shiller:cape' AND date = '1881-02-01'")
    conn.commit()
    conn.close()
    raw = _page(db)
    assert "</script><script>alert(1)" not in raw
    assert "<" + chr(92) + "/script>" in raw
    assert _config(raw)["cape"]["dates"][-1] == "1881-02-01</script><script>alert(1)</script>"


def test_the_page_without_data_says_how_to_collect(db):
    for _ in ("empty tables", "no tables"):
        raw = _page(db)
        page = htmllib.unescape(raw)
        assert "No market data collected yet" in page
        for flag in ("--fred", "--shiller", "--treasury"):
            assert f"<code>{COLLECT_COMMAND} {flag}</code>" in raw
        assert 'id="market-cfg"' not in raw and "renderMarket(" not in raw
        assert _headings(page) == HEADINGS
        _drop_market_tables(db)


def test_the_page_with_one_section(db):
    _seed_buffett(db, -1.5)
    raw = _page(db)
    assert 'class="pill verdict-cheap">Modestly undervalued</span>' in raw
    assert 'id="market-buffett-chart"' in raw and 'id="market-cape-chart"' not in raw
    assert 'id="market-curve-chart"' not in raw and "No market data collected yet" not in raw
    assert f"<code>{COLLECT_COMMAND} --shiller</code>" in raw and f"<code>{COLLECT_COMMAND} --treasury</code>" in raw
    assert f"<code>{COLLECT_COMMAND} --fred</code>" not in raw
    cfg = _config(raw)
    assert cfg["buffett"]["dates"][-1] == "1999-10-01"
    assert (cfg["cape"], cfg["curve"], cfg["spreads"]) == (None, None, None)


def test_the_nav_links_the_market_page(client):
    page = client.get("/screener").text
    assert '<a href="/market" class="nav-link">Market</a>' in page
    assert page.index('href="/gurus" class="nav-link"') < page.index('href="/market" class="nav-link"') < page.index(
        'href="/asof" class="nav-link"')
