"""``stock-data refresh`` (``src/refresh.py``; docs/superpowers/specs/2026-10-25-universe-automation-design.md,
"stock-data refresh"): the steps in their order, each in its own try/except and skipped while it succeeded in a run
that started within its period (the ``refresh_log`` table) unless forced or named, a weekly step whose latest success
left something undone running again (code review 19, I-3); the collection list the quotes, the bars and the SEC's new
filings make, collected in full; the insiders, the gurus and the market data; the recomputation after anything stored;
one run at a time (``<db>.refresh.lock``); the output, its log file and the exit codes; the dispatch from
``stock-data``. Every service is a fake (``tests/refresh_fakes.py``): no network, no sleeping. The bars step is
tests/test_refresh_bars.py's, the shared quote refresh tests/test_quote_refresh.py's."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.main
import src.refresh
from src.config import AppConfig, StorageConfig
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers.sec_handler import SECHandler
from src.fetchers.stock_data_fetcher import StockDataFetcher
from src.fetchers.yahoo_handler import YahooHandler
from src.models.stock_data import StockData
from src.refresh import (
    DAILY,
    PERIODS,
    STEPS,
    WEEKLY,
    FreshSECHandler,
    LockHeld,
    Output,
    RefreshLock,
    collect_tickers,
    is_report,
    lock_path,
    main,
    real_services,
)
from tests import refresh_fakes as rf
from tests.test_fetcher_concurrency import (
    BLK_FY2022,
    DLR_10Q,
    DLR_UNREAD,
    serve_blk,
    serve_dlr,
    stored_blk,
    stored_run,
)

ROOT = Path(__file__).resolve().parents[1]


def _ran(report):
    return [r.step for r in report.results if r.ran]


def _main(db, tmp_path, *args, svc=None):
    """``main`` on *db* with the fakes, the output dir and the cache dir under *tmp_path*."""
    argv = ["--db", str(db), "--output-dir", str(tmp_path / "out"), "--cache-dir", str(tmp_path / "cache"), *args]
    made = svc if svc is not None else rf.services(db)
    return main(argv, make_services=lambda db_path, output_dir, cache_dir: made)


# ---------------------------------------------------------------------------
# The steps, their order and their cadence
# ---------------------------------------------------------------------------


def test_the_steps_and_their_periods():
    assert STEPS == ("quotes", "bars", "sec", "collect", "insiders", "gurus", "market", "recompute")
    assert DAILY == timedelta(hours=20) and WEEKLY == timedelta(days=6, hours=20)
    assert PERIODS == {"quotes": DAILY, "bars": DAILY, "sec": WEEKLY, "insiders": WEEKLY, "gurus": WEEKLY,
                       "market": DAILY}


def test_a_first_run_runs_every_step_in_order(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    report = rf.run(db, rf.services(db))
    assert [r.step for r in report.results] == list(STEPS)
    # Nothing to collect: no split, no basis moved, no new filing.
    assert _ran(report) == ["quotes", "bars", "sec", "insiders", "gurus", "market", "recompute"]
    assert report.result("collect").reason == "nothing to collect"
    assert report.exit_code == 0


@pytest.mark.parametrize("step", ["quotes", "bars", "market"])
def test_a_daily_step_that_succeeded_within_20_hours_is_skipped(tmp_path, step):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, step, timedelta(hours=19, minutes=59))
    result = rf.run(db, rf.services(db)).result(step)
    assert (result.ran, result.ok) == (False, None)
    assert result.reason == "succeeded in the run started 19h 59m ago, within its daily period (20h)"


@pytest.mark.parametrize("step", ["quotes", "bars", "market"])
def test_a_daily_step_runs_again_after_20_hours(tmp_path, step):
    # A daily scheduled run never skips for a few minutes' drift: yesterday's run is 24 hours old, give or take.
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, step, timedelta(hours=20, minutes=1))
    assert rf.run(db, rf.services(db)).result(step).ran is True


@pytest.mark.parametrize("step", ["sec", "insiders", "gurus"])
def test_a_weekly_step_that_succeeded_within_6_days_20_hours_is_skipped(tmp_path, step):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, step, timedelta(days=6, hours=19, minutes=59))
    result = rf.run(db, rf.services(db)).result(step)
    assert result.ran is False and "weekly" in result.reason and "6d 19h 59m ago" in result.reason


@pytest.mark.parametrize("step", ["sec", "insiders", "gurus"])
def test_a_weekly_step_runs_again_after_6_days_20_hours(tmp_path, step):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, step, timedelta(days=6, hours=20, minutes=1))
    assert rf.run(db, rf.services(db)).result(step).ran is True


@pytest.mark.parametrize("step, period", [("market", timedelta(hours=20)), ("sec", timedelta(days=6, hours=20))])
def test_a_step_that_succeeded_exactly_its_period_ago_runs(tmp_path, step, period):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, step, period)
    assert rf.run(db, rf.services(db)).result(step).ran is True


def test_a_daily_step_that_ran_a_day_ago_but_not_within_its_period_runs(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "quotes", timedelta(hours=23, minutes=50))
    assert rf.run(db, rf.services(db)).result("quotes").ran is True


@pytest.mark.parametrize("ok", [False, None])
def test_only_a_success_counts_for_the_cadence(tmp_path, ok):
    # A run that failed, or one that skipped the step, didn't refresh it.
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "market", timedelta(hours=1), ok=ok)
    assert rf.run(db, rf.services(db)).result("market").ran is True


def test_the_latest_success_counts(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "market", timedelta(days=3))
    rf.succeeded(db, "market", timedelta(hours=2))
    rf.succeeded(db, "market", timedelta(hours=1), ok=False)
    assert rf.run(db, rf.services(db)).result("market").ran is False


def test_a_database_without_the_refresh_log_runs_every_step_due(tmp_path):
    # A database from before the refresh: its first run creates the table as it logs its first step.
    db = rf.make_db(tmp_path / "stock.db")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE refresh_log")
    conn.commit()
    conn.close()
    report = rf.run(db, rf.services(db))
    assert _ran(report) == ["quotes", "bars", "sec", "insiders", "gurus", "market", "recompute"]
    assert report.exit_code == 0


# ---------------------------------------------------------------------------
# A step's age is its run's (code review 19, m-5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", ["quotes", "bars", "market"])
def test_a_daily_step_that_finished_5_hours_into_its_run_is_due_at_the_next_days_run(tmp_path, step):
    # A daily step late in a long run (market after a collection of hours) finished 19 hours before the next day's
    # run, which started 24 hours after it.
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, step, timedelta(hours=19), run=timedelta(hours=24))
    assert rf.run(db, rf.services(db)).result(step).ran is True


def test_a_weekly_step_that_finished_late_in_its_run_is_due_a_week_after_the_run_started(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "insiders", timedelta(days=6, hours=19), run=timedelta(days=7))
    assert rf.run(db, rf.services(db)).result("insiders").ran is True


def test_the_age_a_skipped_step_gives_is_its_runs(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "market", timedelta(hours=15), run=timedelta(hours=19, minutes=59))
    result = rf.run(db, rf.services(db)).result("market")
    assert (result.ran, result.reason) == (False, "succeeded in the run started 19h 59m ago, within its daily period "
                                                  "(20h)")


def test_a_row_without_its_run_is_aged_from_its_own_start(tmp_path):
    # A row this code didn't write.
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "market", timedelta(hours=19), started=timedelta(hours=21))
    assert rf.run(db, rf.services(db)).result("market").ran is True


# ---------------------------------------------------------------------------
# A weekly step whose latest success left something undone runs again (code review 19, I-3)
# ---------------------------------------------------------------------------

#: AAA's 10-Q, filed after its last collection (2026-10-21).
AAA_10Q = {rf.cik_of("AAA"): [rf.filing("10-Q", "2026-10-23", "a-1")]}


def _sec(failing=()):
    """The SEC's feeds, AAA's with its 10-Q, those of *failing* answering 403."""
    return rf.FakeSEC(AAA_10Q, errors={rf.cik_of(t): OSError("403 Client Error: Forbidden") for t in failing})


def _day(db, n, stream=None, **services):
    """A scheduled run (every step due) *n* days after NOW, with *services* in place of the fakes'."""
    return rf.run(db, rf.services(db, clock=lambda: rf.NOW + timedelta(days=n), **services), stream=stream)


def _insiders(failing=()):
    """The insiders' collector, every company's Form 4s read but *failing*'s."""
    def collect(tickers, log):
        got = rf.insiders_result(tickers, log)
        for ticker in failing:
            got[ticker]["errors"] = ["submissions: HTTP 403"]
        return got
    return rf.Recorder(collect)


def _gurus(failing=()):
    """The gurus' collector with two gurus, the 13F filings of every guru read but *failing*'s (gurus' CIKs)."""
    def collect(log):
        got = rf.gurus_result(log)
        got["filings"]["1336528"] = {"listed": 1, "stored": 1, "checksum_failures": [], "errors": []}
        for cik in failing:
            got["filings"][cik]["errors"] = [f"{cik} submissions: HTTP 403"]
        return got
    return rf.Recorder(collect)


def test_the_sec_step_runs_again_the_day_after_its_collection_failed(tmp_path):
    # Code review 19's sec_queue_lost probe: the list the SEC step made was lost with the failed collection, and the
    # company with the new 10-Q waited for the next week's SEC step.
    db = rf.make_db(tmp_path / "stock.db")
    day0 = _day(db, 0, sec=_sec(), collect=rf.FakeCollect(db, raises=OSError("Yahoo timed out")))
    assert (day0.result("sec").ok, day0.result("collect").ok) == (True, False)
    collect = rf.FakeCollect(db)
    day1 = _day(db, 1, sec=_sec(), collect=collect)
    sec = day1.result("sec")
    assert sec.ran is True and sec.again == ("succeeded in the run started 1d ago, within its weekly period, but that "
                                             "run put AAA on the collection list, and its collect step failed")
    assert collect.calls == [["AAA"]] and day1.result("collect").ok is True
    # Collected: the SEC step is skipped by its cadence again.
    later = rf.FakeCollect(db)
    day2 = _day(db, 2, sec=_sec(), collect=later)
    assert day2.result("sec").reason == "succeeded in the run started 1d ago, within its weekly period (6d 20h)"
    assert later.calls == []


def test_a_feed_that_fails_makes_the_sec_step_run_again_each_day_naming_it_until_it_is_read(tmp_path):
    # The probe's second case: the feed of the company with the report failed while the others were read.
    db = rf.make_db(tmp_path / "stock.db")
    day0 = _day(db, 0, sec=_sec(failing=["AAA"]))
    assert day0.result("sec").summary["errors"] == {"AAA": "OSError: 403 Client Error: Forbidden"}
    assert (day0.result("sec").ok, day0.queue) == (True, {})
    sec = _day(db, 1, sec=_sec(failing=["AAA"])).result("sec")
    assert sec.ran is True and sec.again == ("succeeded in the run started 1d ago, within its weekly period, but that "
                                             "run had feed errors for AAA")
    collect = rf.FakeCollect(db)
    sec = _day(db, 2, sec=_sec(), collect=collect).result("sec")
    assert sec.ran is True and sec.again.endswith("had feed errors for AAA") and collect.calls == [["AAA"]]
    assert _day(db, 3, sec=_sec()).result("sec").ran is False


def test_the_sec_step_runs_again_after_a_run_that_stopped_before_collecting_its_list(tmp_path):
    # Ctrl+C or a killed process: the SEC step was logged, the collect step never was.
    db = rf.make_db(tmp_path / "stock.db")
    with pytest.raises(KeyboardInterrupt):
        _day(db, 0, sec=_sec(), collect=rf.FakeCollect(db, raises=KeyboardInterrupt()))
    assert [r[1] for r in _log(db)] == ["quotes", "bars", "sec"]
    collect = rf.FakeCollect(db)
    sec = _day(db, 1, sec=_sec(), collect=collect).result("sec")
    assert sec.ran is True and sec.again.endswith(
        "but that run put AAA on the collection list, and stopped before its collect step finished")
    assert collect.calls == [["AAA"]]


def test_the_sec_step_runs_again_after_a_run_that_left_collect_out(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    assert list(rf.run(db, rf.services(db, sec=_sec()), steps=["sec"]).queue) == ["AAA"]
    collect = rf.FakeCollect(db)
    sec = _day(db, 1, sec=_sec(), collect=collect).result("sec")
    assert sec.ran is True and sec.again.endswith("its collect step was skipped (not named in --steps)")
    assert collect.calls == [["AAA"]]


_LISTED = {"AAA": [{"form": "10-Q", "filed": "2026-10-23", "accession": "a-1"}]}


def test_a_sec_run_whose_list_its_own_collect_step_stored_is_skipped_by_its_cadence(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "sec", timedelta(days=2), run=timedelta(days=2), new_filings=_LISTED, errors={})
    rf.succeeded(db, "collect", timedelta(days=2), run=timedelta(days=2))
    sec = rf.run(db, rf.services(db)).result("sec")
    assert (sec.ran, sec.reason) == (False, "succeeded in the run started 2d ago, within its weekly period (6d 20h)")


def test_a_collection_in_another_run_doesnt_count_for_the_sec_steps_list(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "sec", timedelta(days=2), run=timedelta(days=2), new_filings=_LISTED, errors={})
    rf.succeeded(db, "collect", timedelta(days=1), run=timedelta(days=1))
    sec = rf.run(db, rf.services(db, sec=_sec())).result("sec")
    assert sec.ran is True and sec.again.endswith("stopped before its collect step finished")


def test_later_runs_collections_leave_a_clean_sec_run_clean(tmp_path):
    # Its own run's collect step is the one that counts, not the latest.
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, "sec", timedelta(days=3), run=timedelta(days=3), new_filings=_LISTED, errors={})
    rf.succeeded(db, "collect", timedelta(days=3), run=timedelta(days=3))
    rf.succeeded(db, "collect", timedelta(days=1), ok=False, run=timedelta(days=1))
    assert rf.run(db, rf.services(db)).result("sec").ran is False


@pytest.mark.parametrize("step", ["quotes", "bars"])
def test_a_daily_steps_item_errors_dont_make_it_run_again_within_its_period(tmp_path, step):
    # It runs again the next day anyway; a second run the same day doesn't ask Yahoo for every company again.
    db = rf.make_db(tmp_path / "stock.db")
    rf.succeeded(db, step, timedelta(hours=2), run=timedelta(hours=2), errors={"AAA": "Too Many Requests"})
    result = rf.run(db, rf.services(db)).result(step)
    assert (result.ran, result.again) == (False, None)


def test_a_long_list_of_failed_feeds_names_the_first_ten(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    failed = {f"T{n:02d}": "OSError: 403 Client Error: Forbidden" for n in range(1, 13)}
    rf.succeeded(db, "sec", timedelta(days=1), run=timedelta(days=1), new_filings={}, errors=failed)
    sec = rf.run(db, rf.services(db)).result("sec")
    assert sec.again.endswith("had feed errors for T01, T02, T03, T04, T05, T06, T07, T08, T09, T10 and 2 more")


@pytest.mark.parametrize("step, collector, failing", [("insiders", _insiders, "BBB"), ("gurus", _gurus, "1336528")])
def test_a_weekly_step_runs_again_the_day_after_an_item_failed(tmp_path, step, collector, failing):
    db = rf.make_db(tmp_path / "stock.db")
    day0 = _day(db, 0, **{step: collector(failing=[failing])})
    assert day0.result(step).ok is True and list(day0.result(step).summary["errors"]) == [failing]
    again = collector()
    result = _day(db, 1, **{step: again}).result(step)
    assert result.ran is True and result.again == (f"succeeded in the run started 1d ago, within its weekly period, "
                                                   f"but that run had errors for {failing}")
    assert len(again.calls) == 1
    assert _day(db, 2).result(step).ran is False


#: Every step a first run takes on the fakes (collect has nothing to collect).
_EVERY_STEP = ["quotes", "bars", "sec", "insiders", "gurus", "market", "recompute"]


def _at(hours):
    """A clock *hours* after NOW."""
    return lambda: rf.NOW + timedelta(hours=hours)


@pytest.mark.parametrize("tickers", [["AAA"], []])
def test_a_tickers_run_of_every_step_leaves_every_step_due_for_the_next_full_run(tmp_path, tickers):
    # A test run on a few companies (none, with --tickers "") refreshed no other company: the scheduled run that
    # follows in the same hour runs every step, as if it hadn't happened.
    db = rf.make_db(tmp_path / "stock.db")
    test = rf.run(db, rf.services(db), tickers=tickers)
    assert [r.step for r in test.results if r.ok] == _EVERY_STEP
    assert _ran(rf.run(db, rf.services(db, clock=_at(0.5)))) == _EVERY_STEP


def test_a_tickers_run_between_two_full_runs_leaves_the_cadence_to_the_first(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.run(db, rf.services(db))
    test = rf.run(db, rf.services(db, clock=_at(1)), force=True, tickers=["AAA"])
    assert [r.step for r in test.results if r.ok] == _EVERY_STEP  # its rows are successes, of a subset
    report = rf.run(db, rf.services(db, clock=_at(2)))
    assert _ran(report) == []
    assert {r.step: r.reason.split(", within")[0] for r in report.results if r.step in PERIODS} == {
        step: "succeeded in the run started 2h ago" for step in PERIODS}


def test_a_tickers_run_before_the_scheduled_run_doesnt_make_it_skip_the_daily_steps(tmp_path):
    # Day 0's run at 05:00; a --tickers test at 02:00 the next day finds the daily steps due and runs them for its
    # companies; the scheduled run at 05:00 still runs them for every company.
    db = rf.make_db(tmp_path / "stock.db")
    rf.run(db, rf.services(db))
    test = rf.run(db, rf.services(db, clock=_at(21)), tickers=["AAA"])
    assert [r.step for r in test.results if r.ran and r.step in PERIODS] == ["quotes", "bars", "market"]
    yahoo = rf.FakeYahoo()
    report = rf.run(db, rf.services(db, yahoo=yahoo, clock=_at(24)))
    assert [s for s in _ran(report) if s in PERIODS] == ["quotes", "bars", "market"]
    assert yahoo.quote_calls == list(rf.TICKERS)


def test_a_tickers_runs_rows_carry_its_subset_and_a_full_runs_dont(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.run(db, rf.services(db), tickers=["CCC", "AAA"])
    rf.run(db, rf.services(db, clock=_at(0.5)))
    subsets = [json.loads(r[5]).get("subset", "none") for r in _log(db)]
    assert subsets == [["AAA", "CCC"]] * len(STEPS) + ["none"] * len(STEPS)


def test_why_a_step_runs_again_is_printed_logged_and_in_the_summary(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _day(db, 0, sec=_sec(failing=["AAA"]))
    stream = io.StringIO()
    _day(db, 1, sec=_sec(), stream=stream)
    again = "succeeded in the run started 1d ago, within its weekly period, but that run had feed errors for AAA"
    text = stream.getvalue()
    assert f"sec: running again: {again}" in text
    assert f"  sec: ran again ({again}), ok: 3 feeds read" in text
    assert json.loads([r for r in _log(db) if r[1] == "sec"][-1][5])["again"] == again


def _all_recent(db, but=()):
    """Every step with a cadence succeeded an hour before NOW, but *but*, two days before."""
    for step in PERIODS:
        rf.succeeded(db, step, timedelta(days=2) if step in but else timedelta(hours=1))


def test_a_run_right_after_another_skips_every_step_and_exits_0(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _all_recent(db)
    svc = rf.services(db)
    report = rf.run(db, svc)
    assert _ran(report) == [] and report.exit_code == 0
    assert report.result("collect").reason == "nothing to collect"
    assert report.result("recompute").reason == "no step stored anything"
    assert svc.yahoo.quote_calls == [] and svc.market.calls == [] and svc.compute.calls == []


def test_force_runs_every_step_whatever_its_cadence(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _all_recent(db)
    report = rf.run(db, rf.services(db), force=True)
    assert _ran(report) == ["quotes", "bars", "sec", "insiders", "gurus", "market", "recompute"]
    assert report.result("collect").reason == "nothing to collect"


def test_steps_runs_only_those_named_whatever_their_cadence(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _all_recent(db)
    svc = rf.services(db)
    report = rf.run(db, svc, steps=["market", "quotes"])
    assert _ran(report) == ["quotes", "market"]
    assert {r.step: r.reason for r in report.results if not r.ran} == {
        s: "not named in --steps" for s in STEPS if s not in ("quotes", "market")}
    assert svc.compute.calls == []


def test_a_named_recompute_runs_though_nothing_was_stored(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = rf.services(db)
    report = rf.run(db, svc, steps=["recompute"])
    assert _ran(report) == ["recompute"] and svc.compute.calls == [(None,)]


def test_a_named_collect_with_nothing_to_collect_is_skipped(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = rf.services(db)
    report = rf.run(db, svc, steps=["collect"])
    assert report.result("collect").reason == "nothing to collect" and svc.collect.calls == []


def test_an_unknown_step_is_a_usage_error(tmp_path, capsys):
    db = rf.make_db(tmp_path / "stock.db")
    with pytest.raises(SystemExit) as exit_:
        _main(db, tmp_path, "--steps", "quotes,prices")
    assert exit_.value.code == 2 and "prices" in capsys.readouterr().err


def test_a_step_that_raises_fails_alone(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = rf.services(db, market=rf.Recorder(raises=RuntimeError("Shiller is down")))
    report = rf.run(db, svc)
    market = report.result("market")
    assert (market.ran, market.ok, market.reason) == (True, False, "RuntimeError: Shiller is down")
    assert report.result("recompute").ran is True and report.exit_code == 1


def test_a_company_whose_quote_raises_is_its_own_error(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(quotes={"BBB": RuntimeError("socket closed")})
    report = rf.run(db, rf.services(db, yahoo=yahoo))
    quotes = report.result("quotes")
    assert (quotes.ok, quotes.summary["stored"]) == (True, 2)
    assert quotes.summary["errors"] == {"BBB": "RuntimeError: socket closed"}
    assert len(rf.rows(db, "SELECT * FROM market_snapshots WHERE ticker = 'CCC'")) == 2
    assert report.exit_code == 0


def test_a_company_whose_bars_dont_store_is_its_own_error(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")
    replace = SQLiteStore.replace_price_bars_from

    def locked(self, ticker, start, bars):
        if ticker == "AAA":
            raise sqlite3.OperationalError("database is locked")
        return replace(self, ticker, start, bars)

    monkeypatch.setattr(SQLiteStore, "replace_price_bars_from", locked)
    bars = rf.run(db, rf.services(db), steps=["bars"]).result("bars")
    assert bars.summary["errors"] == {"AAA": "OperationalError: database is locked"}
    assert sorted(bars.summary["updated"]) == ["BBB", "CCC"] and bars.ok is True


def test_what_a_step_stored_before_it_raised_counts_for_the_recompute(tmp_path):
    # The quotes store nothing (two fail, a split sends CCC to the collection); the collection stores CCC and raises.
    db = rf.make_db(tmp_path / "stock.db")
    _all_recent(db, but=["quotes"])
    yahoo = rf.FakeYahoo(quotes={"AAA": {"error": "down"}, "BBB": {"error": "down"},
                                 "CCC": rf.quote("CCC", splits=[{"date": "2026-10-22", "ratio": 2.0}])})
    svc = rf.services(db, yahoo=yahoo, collect=rf.FakeCollect(db, raise_after=RuntimeError("the exports failed")))
    report = rf.run(db, svc)
    assert report.result("quotes").stored is False
    collect = report.result("collect")
    assert (collect.ok, collect.stored, collect.reason) == (False, True, "RuntimeError: the exports failed")
    assert sorted(collect.summary["collected"]) == ["CCC"] and collect.summary["not_stored"] == []
    assert report.result("recompute").ran is True and svc.compute.calls == [(None,)]


def test_a_long_step_says_how_far_it_is_a_batch_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(src.refresh, "BATCH", 2)
    db = rf.make_db(tmp_path / "stock.db")
    stream = io.StringIO()
    rf.run(db, rf.services(db), steps=["quotes", "bars", "sec"], stream=stream)
    text = stream.getvalue()
    for step in ("quotes", "bars", "sec"):
        assert f"{step}: 2 of 3 companies" in text and f"{step}: 3 of 3 companies" in text
        assert f"{step}: 1 of 3 companies" not in text


# ---------------------------------------------------------------------------
# quotes
# ---------------------------------------------------------------------------


def test_every_companys_quote_is_stored(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = rf.services(db)
    result = rf.run(db, svc, steps=["quotes"]).result("quotes")
    assert svc.yahoo.quote_calls == list(rf.TICKERS)
    assert len(rf.rows(db, "SELECT * FROM market_snapshots")) == 6
    assert (result.ok, result.stored, result.summary["stored"], result.summary["errors"]) == (True, True, 3, {})


def test_a_split_since_the_last_collection_puts_the_company_on_the_collection_list(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(quotes={"BBB": rf.quote("BBB", splits=[{"date": "2026-10-22", "ratio": 4.0}])})
    svc = rf.services(db, yahoo=yahoo)
    report = rf.run(db, svc, steps=["quotes", "bars", "collect"])
    assert len(rf.rows(db, "SELECT * FROM market_snapshots WHERE ticker = 'BBB'")) == 1
    assert report.queue == {"BBB": ["quotes: a 4-for-1 split on 2026-10-22 since its last collection"]}
    assert report.result("quotes").summary["to_collect"] == {"BBB": "a 4-for-1 split on 2026-10-22"}
    assert svc.collect.calls == [["BBB"]]


def test_a_price_factor_is_stored_and_the_company_valued_on_it_as_the_web_job_does(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(quotes={"CCC": rf.quote("CCC", splits=[{"date": "2026-10-22", "ratio": 1.01}])})
    svc = rf.services(db, yahoo=yahoo)
    report = rf.run(db, svc, steps=["quotes"])
    assert rf.rows(db, "SELECT date, ratio FROM split_events WHERE ticker = 'CCC'") == [("2026-10-22", 1.01)]
    assert svc.compute.calls == [(["CCC"],)]
    assert report.result("quotes").summary["rebased"] == ["CCC"] and report.queue == {}


def test_a_failed_quote_is_counted_and_the_others_stored(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(quotes={"AAA": {"error": "Yahoo returned no price"}})
    result = rf.run(db, rf.services(db, yahoo=yahoo), steps=["quotes"]).result("quotes")
    assert result.summary["errors"] == {"AAA": "Yahoo returned no price"}
    assert (result.ok, result.summary["stored"]) == (True, 2)


def test_the_quotes_step_fails_when_every_quote_fails(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    yahoo = rf.FakeYahoo(quotes={t: {"error": "Too Many Requests"} for t in rf.TICKERS})
    report = rf.run(db, rf.services(db, yahoo=yahoo), steps=["quotes"])
    assert (report.result("quotes").ok, report.result("quotes").stored, report.exit_code) == (False, False, 1)


def test_tickers_limits_the_companies(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = rf.services(db)
    report = rf.run(db, svc, steps=["quotes", "sec", "insiders", "recompute"], tickers=["CCC", "AAA"])
    assert svc.yahoo.quote_calls == ["AAA", "CCC"]
    assert svc.sec.calls == [rf.cik_of("AAA"), rf.cik_of("CCC")]
    assert svc.insiders.calls[0][0] == ["AAA", "CCC"]
    assert svc.compute.calls == [(["AAA", "CCC"],)]
    assert report.exit_code == 0


def test_tickers_not_in_the_database_are_a_usage_error(tmp_path, capsys):
    db = rf.make_db(tmp_path / "stock.db")
    assert _main(db, tmp_path, "--tickers", "AAA,ZZZ") == 2
    assert "ZZZ" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# sec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", ["10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A", "20-F/A", "40-F/A",
                                  "10-KT", "10-QT", "10-KT/A", "10-QT/A"])
def test_a_report_filed_since_the_last_collection_puts_the_company_on_the_list(tmp_path, form):
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC({rf.cik_of("AAA"): [rf.filing(form, "2026-10-23", "0000000001-26-000001")]})
    report = rf.run(db, rf.services(db, sec=sec), steps=["sec"])
    assert report.queue == {"AAA": [f"sec: {form} filed 2026-10-23 (0000000001-26-000001)"]}
    assert report.result("sec").summary["new_filings"] == {
        "AAA": [{"form": form, "filed": "2026-10-23", "accession": "0000000001-26-000001"}]}


@pytest.mark.parametrize("form", ["8-K", "4", "DEF 14A", "S-8", "10-K405", "NT 10-Q", "6-K", "10-Q/A2"])
def test_another_form_doesnt(tmp_path, form):
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC({rf.cik_of("AAA"): [rf.filing(form, "2026-10-23")]})
    assert rf.run(db, rf.services(db, sec=sec), steps=["sec"]).queue == {}


def test_is_report():
    # The transition reports a change of fiscal year brings (10-KT, 10-QT) are reports too.
    forms = ("10-K", "10-Q/A", "20-F", "40-F/A", "10-KT", "10-QT/A", "10-KT405", "8-K", "424B2", "", None)
    assert [f for f in forms if is_report(f)] == ["10-K", "10-Q/A", "20-F", "40-F/A", "10-KT", "10-QT/A"]


def test_a_report_filed_before_the_last_collection_doesnt(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC({rf.cik_of("AAA"): [rf.filing("10-Q", "2026-10-20"), rf.filing("10-K", "2026-02-01")]})
    assert rf.run(db, rf.services(db, sec=sec), steps=["sec"]).queue == {}


def test_a_report_filed_on_the_last_collections_day_counts(tmp_path):
    # The collection's day is the SEC's (BUG-350): a report filed later that day came after it.
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC({rf.cik_of("AAA"): [rf.filing("10-Q", "2026-10-21")]})
    assert list(rf.run(db, rf.services(db, sec=sec), steps=["sec"]).queue) == ["AAA"]


def test_the_last_collection_is_the_last_one_with_the_sec(tmp_path):
    # A later Yahoo-only run brought no filing in.
    db = rf.make_db(tmp_path / "stock.db")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO collection_runs (ticker, collected_at, data_sources) VALUES "
                 "('AAA', '2026-10-24T09:00:00', 'yahoo_finance')")
    conn.commit()
    conn.close()
    sec = rf.FakeSEC({rf.cik_of("AAA"): [rf.filing("10-Q", "2026-10-22")]})
    assert list(rf.run(db, rf.services(db, sec=sec), steps=["sec"]).queue) == ["AAA"]


def test_every_new_report_is_listed(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC({rf.cik_of("BBB"): [rf.filing("10-Q", "2026-10-23", "a-1"), rf.filing("8-K", "2026-10-23"),
                                          rf.filing("10-K/A", "2026-10-22", "a-2")]})
    report = rf.run(db, rf.services(db, sec=sec), steps=["sec"])
    assert report.queue == {"BBB": ["sec: 10-Q filed 2026-10-23 (a-1)", "sec: 10-K/A filed 2026-10-22 (a-2)"]}


def test_a_company_without_a_cik_is_skipped(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", no_cik=["BBB"])
    sec = rf.FakeSEC()
    result = rf.run(db, rf.services(db, sec=sec), steps=["sec"]).result("sec")
    assert sec.calls == [rf.cik_of("AAA"), rf.cik_of("CCC")]
    assert result.summary["no_cik"] == ["BBB"] and result.ok is True


def test_a_feed_that_fails_is_reported_and_the_others_read(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC({rf.cik_of("CCC"): [rf.filing("10-Q", "2026-10-23", "c-1")]},
                     errors={rf.cik_of("AAA"): OSError("HTTP 503")})
    report = rf.run(db, rf.services(db, sec=sec), steps=["sec"])
    assert report.result("sec").summary["errors"] == {"AAA": "OSError: HTTP 503"}
    assert list(report.queue) == ["CCC"] and report.result("sec").ok is True


def test_the_sec_step_fails_when_every_feed_fails(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC(errors={rf.cik_of(t): OSError("blocked") for t in rf.TICKERS})
    assert rf.run(db, rf.services(db, sec=sec), steps=["sec"]).result("sec").ok is False


def test_a_company_without_a_feed_is_its_error(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    sec = rf.FakeSEC(missing=[rf.cik_of("BBB")])
    result = rf.run(db, rf.services(db, sec=sec), steps=["sec"]).result("sec")
    assert result.summary["errors"] == {
        "BBB": f"LookupError: the SEC has no submissions feed for CIK {rf.cik_of('BBB')}"}
    assert (result.summary["feeds"], result.ok) == (2, True)


class _Response:
    """The handler's HTTP answer: *status* and *body*, a submissions feed."""

    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")


def _feed(rows):
    """A submissions feed as the SEC serves it: its "recent" block a column per field, the newest filing first."""
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    return {"cik": "886982", "name": "GOLDMAN SACHS GROUP INC", "sic": "6211",
            "filings": {"recent": {k: [r[i] for r in rows] for i, k in enumerate(keys)}, "files": []}}


def _bank_feed():
    """A bank's feed: its 10-Q behind 150 newer 424B2s (GS, MS, JPM, C, BAC and WFC file dozens a day), 60 older."""
    rows = [(f"0000886982-26-{i:06d}", "424B2", "2026-10-23", "", "p.htm") for i in range(150)]
    rows.append(("0000886982-26-900001", "10-Q", "2026-10-21", "2026-09-30", "gs-20260930.htm"))
    rows += [(f"0000886982-26-{i:06d}", "424B2", "2026-10-20", "", "p.htm") for i in range(200, 260)]
    return _feed(rows)


def _served(handler, monkeypatch, feeds):
    """*handler*'s requests answered from *feeds* (a CIK's 10 digits -> its feed, else 404), each URL recorded."""
    urls = []

    def request(url, timeout=30):
        urls.append(url)
        cik = url.rsplit("CIK", 1)[-1][:10]
        return _Response(200, feeds[cik]) if cik in feeds else _Response(404, {})

    monkeypatch.setattr(handler, "_request", request)
    return urls


def test_the_fresh_handler_reads_every_recent_filing_and_leaves_the_cache_alone(tmp_path, monkeypatch):
    handler = FreshSECHandler(cache_dir=tmp_path / "cache")
    urls = _served(handler, monkeypatch, {"0000886982": _bank_feed()})
    filings = handler.recent_filings("886982")
    handler.close()
    assert urls == ["https://data.sec.gov/submissions/CIK0000886982.json"]
    assert len(filings) == 211 and filings[150]["form"] == "10-Q" and filings[150]["filingDate"] == "2026-10-21"
    assert not (tmp_path / "cache").exists()


def test_a_cik_without_a_feed_has_no_recent_filings(tmp_path, monkeypatch):
    handler = FreshSECHandler(cache_dir=tmp_path)
    _served(handler, monkeypatch, {})
    assert handler.recent_filings("0000000009") is None
    handler.close()


@pytest.mark.parametrize("handler_class", [SECHandler, FreshSECHandler])
def test_get_submissions_still_keeps_the_newest_100_for_its_other_callers(tmp_path, monkeypatch, handler_class):
    handler = handler_class(cache_dir=tmp_path)
    _served(handler, monkeypatch, {"0000886982": _bank_feed()})
    got = handler.get_submissions("886982")
    handler.close()
    assert len(got["filings"]) == 100 and {f["form"] for f in got["filings"]} == {"424B2"}


def test_a_report_behind_150_newer_filings_still_queues_the_company(tmp_path, monkeypatch):
    # Through the real handler, its transport faked: the newest-100 cut would have hidden this 10-Q.
    db = rf.make_db(tmp_path / "stock.db")
    handler = FreshSECHandler(cache_dir=tmp_path / "cache")
    feed = _bank_feed()
    _served(handler, monkeypatch, {rf.cik_of("AAA"): feed, rf.cik_of("BBB"): _feed([]), rf.cik_of("CCC"): _feed([])})
    report = rf.run(db, rf.services(db, sec=handler), steps=["sec"])
    handler.close()
    assert report.queue == {"AAA": ["sec: 10-Q filed 2026-10-21 (0000886982-26-900001)"]}
    assert report.result("sec").summary["feeds"] == 3


# ---------------------------------------------------------------------------
# sec: a company whose latest collection with the SEC was incomplete is collected again (BUG-251, BUG-279)
# ---------------------------------------------------------------------------

#: AAA's 10-Q of 2026-10-23, which its feed lists, as a run whose companyfacts had no fact of it names it.
AAA_Q3 = {"form": "10-Q", "accession": "0000000001-26-000003", "filed": "2026-10-23"}
_AAA_FEED = {rf.cik_of("AAA"): [rf.filing("10-Q", "2026-10-23", "0000000001-26-000003")]}
_LACKED = "read no facts of its 10-Q filed 2026-10-23 (0000000001-26-000003)"


def _local(*utc):
    """The collector's naive local time (as collection_runs keeps it) of a UTC time: the same moment, so the same SEC
    day (BUG-350), on any machine."""
    return datetime(*utc, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _collected(db, ticker, at, report=None, predecessor=None, read=None, submissions=None):
    """A collection of *ticker* with Yahoo and the SEC at *at* (naive local, as the collector writes it), stored by the
    store: its companyfacts lacking *report*, the latest report its submissions listed (BUG-251), its predecessor's
    filings unread for *predecessor* (BUG-279), its submissions unread for *submissions* (BUG-282); a complete one
    without any. Its SEC data read from the SEC at *read* (a cached payload's time, BUG-232), else as it ran."""
    stock = StockData(ticker=ticker, cik=rf.cik_of(ticker), collected_at=at)
    stock.add_source("yahoo_finance")
    stock.add_source("sec_edgar")
    stock.missing_report = report
    stock.predecessor_error = predecessor
    stock.submissions_error = submissions
    stock.sec_read_at = (read or at).isoformat()
    SQLiteStore(db).export([stock])


def _redo(day, why, n):
    """The queue's reason for a company collected again: its latest collection's *day*, what it did (*why*) and the
    *n*th time it is collected again."""
    return f"sec: its collection of {day} {why}; collected again ({n} of 3)"


def test_a_company_whose_companyfacts_lacked_its_latest_report_is_collected_again(tmp_path):
    # BUG-251: AAA's collection of 2026-10-24 read a feed listing its 10-Q of 2026-10-23 and companyfacts without a fact
    # of it, so its record stops a quarter early. The report was filed before the collection's day, so the SEC step
    # took the run for one that had it, and only AAA's next report would have put it on the list.
    db = rf.make_db(tmp_path / "stock.db")
    at = _local(2026, 10, 24, 13, 0)  # 09:00 in New York
    _collected(db, "AAA", at, report=AAA_Q3)
    report = rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"])
    assert report.queue == {"AAA": [_redo(str(at)[:10], _LACKED, 1)]}
    sec = report.result("sec")
    assert sec.summary["incomplete"] == {"AAA": {"collected_at": at.isoformat(), "why": _LACKED, "again": 1}}
    assert (sec.summary["new_filings"], sec.summary["incomplete_left"]) == ({}, {})
    assert sec.headline == ("3 feeds read, 0 new reports at 0 companies, 1 incomplete collections to redo, 0 without a "
                            "CIK, 0 errors")


@pytest.mark.parametrize("report, predecessor, why", [
    # BUG-279: the good run that would restate BBB waited for its next report.
    (None, "503 Server Error", "couldn't read its predecessor's filings (503 Server Error)"),
    (AAA_Q3, "no XBRL data", f"{_LACKED} and couldn't read its predecessor's filings (no XBRL data)"),
])
def test_a_company_whose_predecessor_couldnt_be_read_is_collected_again(tmp_path, report, predecessor, why):
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "BBB", datetime(2026, 10, 24, 9, 0), report=report, predecessor=predecessor)
    assert rf.run(db, rf.services(db), steps=["sec"]).queue == {"BBB": [_redo("2026-10-24", why, 1)]}


def test_a_complete_collection_since_ends_it(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "AAA", datetime(2026, 10, 24, 9, 0), report=AAA_Q3)
    collect = rf.FakeCollect(db, collected_at="2026-10-25T06:00:00")  # its companyfacts now carry the 10-Q
    day0 = _day(db, 0, sec=rf.FakeSEC(_AAA_FEED), collect=collect)
    assert collect.calls == [["AAA"]] and day0.result("collect").ok is True
    sec = _day(db, 7, sec=rf.FakeSEC(_AAA_FEED)).result("sec")
    assert sec.ran is True and (sec.summary["incomplete"], sec.summary["incomplete_left"]) == ({}, {})


def _aaa_never_collected(tmp_path):
    """The test database without AAA's collection of rf.COLLECTED (2026-10-21), so that a test's own are AAA's every
    one."""
    db = rf.make_db(tmp_path / "stock.db")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM collection_runs WHERE ticker = 'AAA'")
    conn.commit()
    conn.close()
    return db


#: AAA's 10-Q of 2026-07-31, an earlier report its collections' companyfacts lacked, and what such a run did.
AAA_Q2 = {"form": "10-Q", "accession": "0000000001-26-000002", "filed": "2026-07-31"}
_LACKED_Q2 = "read no facts of its 10-Q filed 2026-07-31 (0000000001-26-000002)"


def test_a_report_still_without_facts_is_collected_again_three_times_then_left(tmp_path):
    # The SEC fills a lag within days. A report three more collections a week apart still find without facts is the
    # source's gap (DLR's 10-Q of 2026-07-31 still had none 86 days on): AAA then waits for its next report, each run's
    # warning naming the report.
    db = _aaa_never_collected(tmp_path)
    _collected(db, "AAA", datetime(2026, 10, 18, 9, 0), report=AAA_Q2)
    queued, collected = [], []
    for week in range(5):
        at = (rf.NOW + timedelta(days=7 * week, hours=1)).replace(tzinfo=None).isoformat()
        collect = rf.FakeCollect(db, collected_at=at, incomplete={"AAA": {"report": AAA_Q2}})
        report = _day(db, 7 * week, collect=collect)
        queued.append(report.queue.get("AAA"))
        collected += collect.calls
    assert queued == [[_redo("2026-10-18", _LACKED_Q2, 1)], [_redo("2026-10-25", _LACKED_Q2, 2)],
                      [_redo("2026-11-01", _LACKED_Q2, 3)], None, None]
    assert collected == [["AAA"]] * 3
    sec = report.result("sec")
    assert sec.summary["incomplete"] == {}
    assert sec.summary["incomplete_left"] == {"AAA": {"collected_at": "2026-11-08T06:00:00", "why": _LACKED_Q2,
                                                      "collections": 4, "tries": 4, "since": "2026-10-18"}}
    assert sec.headline == ("3 feeds read, 0 new reports at 0 companies, 0 incomplete collections to redo (and 1 left "
                            "after 3 tries), 0 without a CIK, 0 errors")


def test_another_report_without_facts_starts_its_own_count(tmp_path):
    # AAA's 10-Q of 2026-07-31 was left after four collections a week apart without its facts; the next one's lag is
    # its own.
    db = _aaa_never_collected(tmp_path)
    for at in (datetime(2026, 10, 2, 9, 0), datetime(2026, 10, 9, 9, 0), datetime(2026, 10, 16, 9, 0),
               datetime(2026, 10, 23, 9, 0)):
        _collected(db, "AAA", at, report=AAA_Q2)
    sec = rf.run(db, rf.services(db), steps=["sec"]).result("sec")
    assert list(sec.summary["incomplete_left"]) == ["AAA"] and sec.summary["incomplete_left"]["AAA"]["collections"] == 4
    at = _local(2026, 10, 24, 13, 0)  # 09:00 in New York, after the 10-Q's day
    _collected(db, "AAA", at, report=AAA_Q3)
    report = rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"])
    assert report.queue == {"AAA": [_redo(str(at)[:10], _LACKED, 1)]}


# ---- which collections are tries, and what ends a gap (BUG-232, BUG-281, BUG-289) ----

def _runs(*runs):
    """``incomplete_tries``'s rows: each (collected_at, its companyfacts' missing report, its predecessor's error,
    when its SEC data was read; None for as it ran), as ``collection_runs`` keeps them, newest first."""
    rows = []
    for at, report, predecessor, read in runs:
        lacked = {**({"report": report} if report else {}), **({"predecessor": predecessor} if predecessor else {})}
        rows.append((at.isoformat(), json.dumps(lacked) if lacked else None, read.isoformat() if read else None))
    return sorted(rows, reverse=True)


def _weekly(first, n, report=AAA_Q2, predecessor=None):
    """*n* collections a week apart from *first*, each read fresh, lacking *report* and *predecessor* alike."""
    return [(first + timedelta(days=7 * i), report, predecessor, None) for i in range(n)]


def test_a_report_filed_after_the_sec_data_a_collection_read_puts_the_company_on_the_list(tmp_path):
    # BUG-232: AAA's collection of 2026-10-24 (`stock-data`, by hand) read the companyfacts and submissions the SEC
    # handler had cached on 2026-10-21, before its 10-Q of 2026-10-23, which neither listed: AAA was stored without it.
    # The run postdated the filing, so the SEC step never put AAA on the list for it.
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "AAA", datetime(2026, 10, 24, 9, 0), read=datetime(2026, 10, 21, 8, 0))
    report = rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"])
    assert report.queue == {"AAA": ["sec: 10-Q filed 2026-10-23 (0000000001-26-000003)"]}


def test_a_report_filed_before_the_sec_data_a_collection_read_doesnt(tmp_path):
    # Read at 09:00 New York on 2026-10-24, the 10-Q filed 2026-10-23.
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "AAA", _local(2026, 10, 24, 14, 0), read=_local(2026, 10, 24, 13, 0))
    assert rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"]).queue == {}


@pytest.mark.parametrize("read", [
    (2026, 10, 23, 20, 0),   # 16:00 New York (EDT): 2026-10-24 03:00 east of it, at UTC+7
    (2026, 10, 24, 2, 0),    # 22:00 New York: already 2026-10-24 in UTC
])
def test_a_report_filed_on_the_secs_day_of_the_read_puts_the_company_on_the_list(tmp_path, read):
    # BUG-350: the day compared was the collector's own: east of New York (or in UTC) a read late on the SEC's
    # 2026-10-23 falls on 2026-10-24, so a 10-Q accepted after it that day (16:00-17:30 New York is common), filed
    # 2026-10-23, was never queued.
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "AAA", _local(*read) + timedelta(minutes=5), read=_local(*read))
    report = rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"])
    assert report.queue == {"AAA": ["sec: 10-Q filed 2026-10-23 (0000000001-26-000003)"]}


def test_a_read_after_midnight_in_new_york_is_the_next_sec_day(tmp_path):
    # 00:30 New York (EDT) on 2026-10-24: a report filed 2026-10-23 came before it.
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "AAA", _local(2026, 10, 24, 4, 35), read=_local(2026, 10, 24, 4, 30))
    assert rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"]).queue == {}


@pytest.mark.parametrize("stamp, day", [
    ("2026-10-24T03:00:00+07:00", "2026-10-23"),   # an offset is the stamp's own
    ("2026-10-23T23:30:00-04:00", "2026-10-23"),
    ("2026-01-15T04:59:00+00:00", "2026-01-14"),   # EST: midnight New York is 05:00 UTC
    ("2026-01-15T05:00:00+00:00", "2026-01-15"),
])
def test_the_sec_day_of_a_read_is_new_yorks(stamp, day):
    assert src.refresh._read_day((stamp, None, None)) == day
    assert src.refresh._read_day(("2020-01-01T00:00:00+00:00", None, stamp)) == day   # sec_read_at first
    naive = datetime.fromisoformat(stamp).astimezone().replace(tzinfo=None).isoformat()   # this machine's local time
    assert src.refresh._read_day((naive, None, None)) == day


def test_a_collection_that_read_the_sec_on_the_day_before_a_report_isnt_in_its_gap():
    # BUG-350 in incomplete_tries: a collection whose read came on the SEC's day before the gap's report was filed
    # (22:00 New York on 2026-10-22, 2026-10-23 in UTC and east of New York) can't have seen it: no try at it.
    runs = _runs((_local(2026, 10, 24, 14, 0), AAA_Q3, None, None),
                 (_local(2026, 10, 23, 2, 0), AAA_Q3, None, None))
    tried = src.refresh.incomplete_tries(runs)
    assert (tried["tries"], tried["collections"]) == (1, 1)


def test_a_collection_on_the_sec_cache_isnt_a_try(tmp_path):
    # BUG-281: of AAA's four collections a week apart without its 10-Q's facts, the one of 2026-10-17 (by hand) read
    # the SEC data the refresh's collection of 2026-10-11 had cached: three reads of the SEC, so AAA is collected a
    # third time.
    db = _aaa_never_collected(tmp_path)
    for at, read in ((datetime(2026, 10, 4, 9, 0), None), (datetime(2026, 10, 11, 9, 0), None),
                     (datetime(2026, 10, 17, 10, 0), datetime(2026, 10, 11, 9, 1)),
                     (datetime(2026, 10, 23, 9, 0), None)):
        _collected(db, "AAA", at, report=AAA_Q2, read=read)
    report = rf.run(db, rf.services(db), steps=["sec"])
    assert report.queue == {"AAA": [_redo("2026-10-23", _LACKED_Q2, 3)]}
    assert report.result("sec").summary["incomplete"]["AAA"]["again"] == 3


def test_collections_that_read_the_sec_days_apart_are_one_try(tmp_path):
    # BUG-289: four collections a day apart (by hand, each after the cache's week) spent AAA's three tries in four days,
    # though the SEC fills a lag in days: they are one, and AAA is collected again.
    db = _aaa_never_collected(tmp_path)
    for day in (20, 21, 22, 23):
        _collected(db, "AAA", datetime(2026, 10, day, 9, 0), report=AAA_Q2)
    assert rf.run(db, rf.services(db), steps=["sec"]).queue == {"AAA": [_redo("2026-10-23", _LACKED_Q2, 1)]}


def test_a_predecessor_read_that_fails_by_turns_doesnt_start_a_new_count(tmp_path):
    # BUG-289: each collection compared its gap with the one before, report and predecessor alike, so a predecessor
    # read that failed and succeeded by turns started a new count each week: AAA was collected again for ever.
    db = _aaa_never_collected(tmp_path)
    for i, at in enumerate((datetime(2026, 9, 27, 9, 0), datetime(2026, 10, 4, 9, 0), datetime(2026, 10, 11, 9, 0),
                            datetime(2026, 10, 18, 9, 0))):
        _collected(db, "AAA", at, report=AAA_Q2, predecessor=None if i % 2 else "503 Server Error")
    sec = rf.run(db, rf.services(db), steps=["sec"]).result("sec")
    assert sec.summary["incomplete"] == {}
    assert sec.summary["incomplete_left"] == {"AAA": {"collected_at": "2026-10-18T09:00:00", "why": _LACKED_Q2,
                                                      "collections": 4, "tries": 4, "since": "2026-09-27"}}


def test_a_report_read_from_its_own_xbrl_by_turns_doesnt_start_a_new_count(tmp_path):
    # BUG-289's kind: beside a predecessor that can't be read, the 10-Q's own XBRL read (Q17) that succeeds and fails
    # by turns left the report off the gap every other week.
    db = _aaa_never_collected(tmp_path)
    for i, at in enumerate((datetime(2026, 9, 27, 9, 0), datetime(2026, 10, 4, 9, 0), datetime(2026, 10, 11, 9, 0),
                            datetime(2026, 10, 18, 9, 0))):
        _collected(db, "AAA", at, report=None if i % 2 else AAA_Q2, predecessor="no XBRL data")
    sec = rf.run(db, rf.services(db), steps=["sec"]).result("sec")
    assert (sec.summary["incomplete"], list(sec.summary["incomplete_left"])) == ({}, ["AAA"])
    assert sec.summary["incomplete_left"]["AAA"]["tries"] == 4


def test_a_new_reports_lag_after_a_predecessor_left_unread_gets_its_own_tries(tmp_path):
    # XOM-like: four weekly collections couldn't read the predecessor, so it waits for its next report; the
    # collection for that report then finds the SEC's lag on it as well, which collections from before the report
    # was filed can't have seen: it gets its own tries.
    db = _aaa_never_collected(tmp_path)
    for at in (datetime(2026, 9, 25, 9, 0), datetime(2026, 10, 2, 9, 0), datetime(2026, 10, 9, 9, 0),
               datetime(2026, 10, 16, 9, 0)):
        _collected(db, "AAA", at, predecessor="no XBRL data")
    assert list(rf.run(db, rf.services(db), steps=["sec"]).result("sec").summary["incomplete_left"]) == ["AAA"]
    at = _local(2026, 10, 24, 13, 0)  # 09:00 in New York, after the 10-Q's day
    _collected(db, "AAA", at, report=AAA_Q3, predecessor="no XBRL data")
    report = rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"])
    assert report.queue == {"AAA": [_redo(
        str(at)[:10], f"{_LACKED} and couldn't read its predecessor's filings (no XBRL data)", 1)]}


def test_the_tries_of_a_complete_latest_collection_are_none():
    assert src.refresh.incomplete_tries([]) is None
    assert src.refresh.incomplete_tries(_runs((datetime(2026, 10, 18), None, None, None),
                                              *_weekly(datetime(2026, 9, 1), 5))) is None


def test_the_tries_are_reads_of_the_sec_a_week_apart_back_to_a_complete_collection():
    runs = _runs((datetime(2026, 9, 1), None, None, None), *_weekly(datetime(2026, 9, 8), 3))
    assert src.refresh.incomplete_tries(runs) == {"tries": 3, "collections": 3, "since": "2026-09-08"}
    # Six days apart is a week's tries; less is the same one.
    assert src.refresh.incomplete_tries(_runs((datetime(2026, 9, 8), AAA_Q2, None, None),
                                              (datetime(2026, 9, 14), AAA_Q2, None, None)))["tries"] == 2
    assert src.refresh.incomplete_tries(_runs((datetime(2026, 9, 8), AAA_Q2, None, None),
                                              (datetime(2026, 9, 13, 23, 59), AAA_Q2, None, None)))["tries"] == 1


def test_a_collection_that_read_older_sec_data_than_a_try_isnt_one():
    # A collection on another cache, older than the last try's read: not a try, and not the reference for the next.
    runs = _runs((datetime(2026, 9, 8), AAA_Q2, None, None),
                 (datetime(2026, 9, 22), AAA_Q2, None, datetime(2026, 9, 1)),
                 (datetime(2026, 9, 15), AAA_Q2, None, None))
    assert src.refresh.incomplete_tries(runs) == {"tries": 2, "collections": 3, "since": "2026-09-08"}


def test_a_runs_stored_before_their_sec_read_was_kept_are_read_as_of_their_collection():
    runs = [(row[0], row[1], None) for row in _runs(*_weekly(datetime(2026, 9, 1), 4))]
    assert src.refresh.incomplete_tries(runs) == {"tries": 4, "collections": 4, "since": "2026-09-01"}
    # And one this code didn't write.
    runs = [(row[0], row[1], "unknown") for row in runs]
    assert src.refresh.incomplete_tries(runs) == {"tries": 4, "collections": 4, "since": "2026-09-01"}


def test_a_collection_that_lacked_another_report_ends_the_gap():
    runs = _runs(*_weekly(datetime(2026, 8, 1), 4, report={**AAA_Q2, "accession": "0000000001-26-000001"}),
                 *_weekly(datetime(2026, 9, 1), 2))
    assert src.refresh.incomplete_tries(runs) == {"tries": 2, "collections": 2, "since": "2026-09-01"}


def test_a_collection_that_read_the_sec_before_the_report_was_filed_ends_the_gap():
    runs = _runs(*_weekly(datetime(2026, 7, 3), 4, report=None, predecessor="503"),
                 (datetime(2026, 7, 31, 9, 0), None, "503", datetime(2026, 7, 30, 9, 0)),
                 (datetime(2026, 8, 7), AAA_Q2, "503", None))
    assert src.refresh.incomplete_tries(runs) == {"tries": 1, "collections": 1, "since": "2026-08-07"}
    # Read on the report's day (the SEC's: 09:00 in New York), it may have seen it.
    runs = _runs((_local(2026, 7, 31, 13, 0), None, "503", None), (datetime(2026, 8, 7), AAA_Q2, "503", None))
    assert src.refresh.incomplete_tries(runs)["collections"] == 2


def test_a_company_with_a_new_report_and_an_incomplete_collection_has_both_reasons(tmp_path):
    # Its collection of 2026-10-22 lacked its 10-K of 2026-08-14; its 10-Q of 2026-10-23 came since.
    db = rf.make_db(tmp_path / "stock.db")
    ten_k = {"form": "10-K", "accession": "0000000001-26-000001", "filed": "2026-08-14"}
    _collected(db, "AAA", datetime(2026, 10, 22, 9, 0), report=ten_k)
    report = rf.run(db, rf.services(db, sec=rf.FakeSEC(_AAA_FEED)), steps=["sec"])
    assert report.queue == {"AAA": ["sec: 10-Q filed 2026-10-23 (0000000001-26-000003)", _redo(
        "2026-10-22", "read no facts of its 10-K filed 2026-08-14 (0000000001-26-000001)", 1)]}


def _without_the_incomplete_column(db):
    """*db* as a database last written before BUG-251's ``collection_runs.incomplete``: the user's, to 2026-10-25."""
    conn = sqlite3.connect(db)
    if "incomplete" in {row[1] for row in conn.execute("PRAGMA table_info(collection_runs)")}:
        conn.execute("ALTER TABLE collection_runs DROP COLUMN incomplete")
    conn.commit()
    conn.close()


def test_a_database_whose_runs_dont_say_what_they_lacked_is_listed_by_the_reports_alone(tmp_path):
    # Its runs read as complete, as they did: a report filed since a company's last collection puts it on the list.
    db = rf.make_db(tmp_path / "stock.db")
    _without_the_incomplete_column(db)
    sec = rf.FakeSEC({**_AAA_FEED, rf.cik_of("BBB"): [rf.filing("10-Q", "2026-10-20", "b-1")]})
    report = rf.run(db, rf.services(db, sec=sec), steps=["sec"])
    assert report.queue == {"AAA": ["sec: 10-Q filed 2026-10-23 (0000000001-26-000003)"]}
    assert report.result("sec").ok is True


def test_the_sec_step_runs_again_after_a_run_that_lost_an_incomplete_collection_it_listed(tmp_path):
    # As for a new report (code review 19, I-3): the list lives in the run, and its collection failed.
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "AAA", datetime(2026, 10, 24, 9, 0), report=AAA_Q3)
    day0 = _day(db, 0, sec=rf.FakeSEC(_AAA_FEED), collect=rf.FakeCollect(db, raises=OSError("Yahoo timed out")))
    assert (day0.result("sec").ok, day0.result("collect").ok) == (True, False)
    collect = rf.FakeCollect(db, collected_at="2026-10-26T06:00:00")
    sec = _day(db, 1, sec=rf.FakeSEC(_AAA_FEED), collect=collect).result("sec")
    assert sec.again == ("succeeded in the run started 1d ago, within its weekly period, but that run put AAA on the "
                         "collection list, and its collect step failed")
    assert collect.calls == [["AAA"]]


def test_the_real_collection_stores_what_the_companyfacts_lack_and_the_sec_step_collects_it_again(tmp_path,
                                                                                                 monkeypatch):
    # The collect step's collection (its SEC read a FreshSECHandler's) stores DLR's run as `stock-data` does; the next
    # SEC step, whose feed lists no report since, puts DLR on the list again.
    db = tmp_path / "stock.db"
    serve_dlr(monkeypatch, lagging=True)
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["DLR"], lambda line: None)["stored"] == 1
    warnings, incomplete = stored_run(db, "DLR")
    assert incomplete == {"report": DLR_10Q} and DLR_UNREAD in warnings
    feed = rf.FakeSEC({"0001297996": [rf.filing("10-Q", "2026-07-31", "0001104659-26-089296")]})
    queue = rf.run(db, rf.services(db, sec=feed), steps=["sec"]).queue
    assert list(queue) == ["DLR"] and len(queue["DLR"]) == 1
    assert queue["DLR"][0].startswith("sec: its collection of ") and queue["DLR"][0].endswith(
        " read no facts of its 10-Q filed 2026-07-31 (0001104659-26-089296); collected again (1 of 3)")


def test_the_real_collection_stores_a_failed_predecessor_read_and_the_sec_step_collects_it_again(tmp_path,
                                                                                                 monkeypatch):
    # BUG-279 through the collect step's collection: BLK's run whose read of the old BlackRock failed goes on the list
    # again at the next SEC step, and once a run reads both filers, BLK is off it.
    db = tmp_path / "stock.db"
    quiet = rf.FakeSEC()  # BLK's feed lists no report since
    serve_blk(monkeypatch, 503)
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["BLK"], lambda line: None)["stored"] == 1
    queue = rf.run(db, rf.services(db, sec=quiet), steps=["sec"]).queue
    assert list(queue) == ["BLK"] and queue["BLK"][0].startswith("sec: its collection of ")
    assert queue["BLK"][0].endswith(" couldn't read its predecessor's filings (HTTPError); collected again (1 of 3)")
    serve_blk(monkeypatch, 200)
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["BLK"], lambda line: None)["stored"] == 1
    assert rf.run(db, rf.services(db, sec=quiet), steps=["sec"]).queue == {}
    assert quiet.calls == ["0002012383", "0002012383"]


def _age_dlr_cache(cache, days):
    """Date DLR's cached companyfacts and submissions in *cache* *days* back; returns that time."""
    then = int(time.time()) - days * 86400
    for name in ("companyfacts_CIK0001297996.json", "submissions_CIK0001297996.json"):
        os.utime(cache / name, (then, then))
    return then


def test_a_collection_by_hand_on_the_sec_cache_is_queued_for_a_report_filed_since_the_cache(tmp_path, monkeypatch):
    # BUG-232 end to end: `stock-data` collects DLR from the companyfacts and submissions the SEC handler cached three
    # days ago, and DLR filed a 10-Q yesterday, which neither lists: the next SEC step puts DLR on the list for it,
    # though the collection ran after the filing.
    db = tmp_path / "stock.db"
    cached = SECHandler._read_payload_cache
    serve_dlr(monkeypatch, lagging=False)
    monkeypatch.setattr(SECHandler, "_read_payload_cache", cached)  # `stock-data`'s handler reads its cache
    config = AppConfig(storage=StorageConfig(base_dir=tmp_path, db_path=db))
    with StockDataFetcher(config=config) as fetcher:
        assert fetcher.fetch_and_export(["DLR"], formats=["sqlite"])["stored"] == 1
    then = _age_dlr_cache(tmp_path / "cache", 3)
    with StockDataFetcher(config=config) as fetcher:
        assert fetcher.fetch_and_export(["DLR"], formats=["sqlite"])["stored"] == 1
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    feed = rf.FakeSEC({"0001297996": [rf.filing("10-Q", yesterday, "0001104659-26-099999")]})
    assert rf.run(db, rf.services(db, sec=feed), steps=["sec"]).queue == {
        "DLR": [f"sec: 10-Q filed {yesterday} (0001104659-26-099999)"]}
    assert rf.rows(db, "SELECT sec_read_at FROM collection_runs ORDER BY collected_at DESC LIMIT 1") == [
        (datetime.fromtimestamp(then).isoformat(),)]


def test_the_refresh_collection_reads_the_sec_as_it_runs_whatever_the_cache_holds(tmp_path, monkeypatch):
    # The collect step's FreshSECHandler asks the SEC: its run's SEC data is of now, a try at a gap (BUG-281).
    db = tmp_path / "stock.db"
    cached = SECHandler._read_payload_cache
    serve_dlr(monkeypatch, lagging=False)
    monkeypatch.setattr(SECHandler, "_read_payload_cache", cached)
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["DLR"], lambda line: None)["stored"] == 1
    _age_dlr_cache(tmp_path / "cache", 3)
    before = datetime.now()
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["DLR"], lambda line: None)["stored"] == 1
    (read,), = rf.rows(db, "SELECT sec_read_at FROM collection_runs ORDER BY collected_at DESC LIMIT 1")
    assert before <= datetime.fromisoformat(read) <= datetime.now()


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


def _queued_by_every_step(db):
    yahoo = rf.FakeYahoo(quotes={"CCC": rf.quote("CCC", splits=[{"date": "2026-10-22", "ratio": 2.0}])},
                         bars={"AAA": lambda start: rf.served("AAA", start, factor=1.5)})
    sec = rf.FakeSEC({rf.cik_of("BBB"): [rf.filing("10-Q", "2026-10-23", "b-1")],
                      rf.cik_of("AAA"): [rf.filing("10-Q", "2026-10-22", "a-1")]})
    return rf.services(db, yahoo=yahoo, sec=sec)


def test_the_collection_list_is_collected_in_full_each_company_once(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = _queued_by_every_step(db)
    report = rf.run(db, svc)
    assert svc.collect.calls == [["CCC", "AAA", "BBB"]]
    assert report.queue["AAA"][0].startswith("bars: ") and report.queue["AAA"][1] == "sec: 10-Q filed 2026-10-22 (a-1)"
    collect = report.result("collect")
    assert (collect.ran, collect.ok, collect.stored) == (True, True, True)
    assert collect.summary["collected"] == {t: {"collected_at": "2026-10-25T12:00:00", "warnings": 2, "errors": 0}
                                            for t in ("CCC", "AAA", "BBB")}
    assert collect.summary["not_stored"] == []


def test_a_company_the_collection_didnt_store_is_reported(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = _queued_by_every_step(db)
    svc.collect = rf.FakeCollect(db, missing=["AAA"], errors={"BBB": 1})
    report = rf.run(db, svc)
    collect = report.result("collect")
    assert collect.summary["not_stored"] == ["AAA"] and collect.summary["collected"]["BBB"]["errors"] == 1
    # A company not stored fails the step, named (code review 19, I-1); the others count as collected.
    assert (collect.ok, collect.stored, report.exit_code) == (False, True, 1)
    assert collect.reason == "the collection didn't store 1 of the 3: AAA"


def test_a_company_the_real_collection_couldnt_store_fails_the_step_and_the_others_are_stored(tmp_path, monkeypatch):
    # Code review 19 (I-1): the store keeps each stock to its own savepoint, so a collection can store part of the
    # list. The one it couldn't store (a value SQLite can't bind) fails the step, named; the others are collected
    # and valued.
    db = rf.make_db(tmp_path / "stock.db")
    svc = _queued_by_every_step(db)

    def fetch(self, ticker, include_yahoo=True, include_sec=True, years_back=None):
        stock = StockData(ticker=ticker, cik=rf.cik_of(ticker), company_name=f"{ticker} Corp", sector_class="general",
                          collected_at=datetime(2026, 10, 25, 12, 0))
        stock.company_info = {"full_time_employees": {"oops": 1} if ticker == "AAA" else 100}
        stock.add_source("yahoo_finance")
        stock.add_source("sec_edgar")
        return stock

    valued = []
    monkeypatch.setattr(StockDataFetcher, "fetch_ticker", fetch)
    monkeypatch.setattr(YahooHandler, "fetch_benchmark_bars", lambda self, symbol="^GSPC": [])
    monkeypatch.setattr("src.valuation.engine.compute_and_store",
                        lambda db_path, tickers=None, logger=None: valued.append(sorted(tickers)) or len(tickers))
    svc.collect = lambda tickers, log: collect_tickers(db, tmp_path / "out", tmp_path / "cache", tickers, log)
    out = io.StringIO()
    report = rf.run(db, svc, stream=out)
    collect = report.result("collect")
    assert sorted(collect.summary["collected"]) == ["BBB", "CCC"] and collect.summary["not_stored"] == ["AAA"]
    assert (collect.ok, report.exit_code) == (False, 1)
    assert collect.reason == "the collection didn't store 1 of the 3: AAA"
    assert list(collect.summary["fetch"]["not_stored"]) == ["AAA"] and collect.summary["fetch"]["stored"] == 2
    assert valued == [["BBB", "CCC"]]
    assert "collect: ERROR: SQLite write failed for AAA: Error binding parameter" in out.getvalue()


def test_the_collect_step_fails_when_nothing_was_stored(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = _queued_by_every_step(db)
    svc.collect = rf.FakeCollect(db, missing=["AAA", "BBB", "CCC"])
    report = rf.run(db, svc)
    assert (report.result("collect").ok, report.exit_code) == (False, 1)


def test_the_list_isnt_collected_when_steps_leaves_collect_out(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = _queued_by_every_step(db)
    report = rf.run(db, svc, steps=["quotes", "bars", "sec"])
    assert svc.collect.calls == [] and report.result("collect").reason == "not named in --steps"
    assert list(report.queue) == ["CCC", "AAA", "BBB"]


def test_a_collection_that_raises_fails_the_step(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = _queued_by_every_step(db)
    svc.collect = rf.FakeCollect(db, raises=RuntimeError("disk full"))
    report = rf.run(db, svc)
    assert report.result("collect").reason == "RuntimeError: disk full" and report.exit_code == 1


# ---------------------------------------------------------------------------
# insiders, gurus, market
# ---------------------------------------------------------------------------


def test_the_insiders_are_collected_for_every_company_with_a_cik(tmp_path):
    db = rf.make_db(tmp_path / "stock.db", no_cik=["BBB"])
    svc = rf.services(db)
    result = rf.run(db, svc, steps=["insiders"]).result("insiders")
    assert [call[0] for call in svc.insiders.calls] == [["AAA", "CCC"]]
    assert (result.ok, result.stored, result.summary["stored"], result.summary["errors"]) == (True, True, 4, {})


def test_the_insiders_step_fails_when_every_company_fails(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    failing = rf.Recorder(lambda tickers, log: {t: {"listed": 0, "stored": 0, "other_issuer": 0,
                                                    "from_predecessor": 0, "errors": ["submissions: 403"]}
                                                for t in tickers})
    result = rf.run(db, rf.services(db, insiders=failing), steps=["insiders"]).result("insiders")
    assert result.ok is False and result.summary["errors"]["AAA"] == ["submissions: 403"]


def test_the_gurus_are_collected_mapped_and_built(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = rf.services(db)
    result = rf.run(db, svc, steps=["gurus"]).result("gurus")
    assert len(svc.gurus.calls) == 1
    assert (result.ok, result.stored) == (True, True)
    assert (result.summary["filings_stored"], result.summary["positions"]) == (1, 40)


def test_the_gurus_step_fails_when_every_guru_fails(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    failing = rf.Recorder({"filings": {"1067983": {"listed": 0, "stored": 0, "checksum_failures": [],
                                                   "errors": ["1067983 submissions: HTTP 403"]}},
                           "cusips": {"universe": 0, "openfigi": 0, "none": 0, "errors": []},
                           "positions": {"1067983": {"quarters": 0, "positions": 0}}})
    assert rf.run(db, rf.services(db, gurus=failing), steps=["gurus"]).result("gurus").ok is False


def test_the_market_data_is_collected(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    svc = rf.services(db)
    result = rf.run(db, svc, steps=["market"]).result("market")
    assert len(svc.market.calls) == 1 and (result.ok, result.stored) == (True, True)
    assert result.summary["stored"] == 4 and result.summary["failed"] == {}


def test_a_market_source_that_fails_is_reported(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    market = rf.Recorder(lambda log: rf.market_result(log, failed=["shiller"]))
    result = rf.run(db, rf.services(db, market=market), steps=["market"]).result("market")
    assert result.ok is True and result.summary["failed"] == {"shiller": "OSError: down"}


def test_the_market_step_fails_when_every_source_fails(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    market = rf.Recorder(lambda log: rf.market_result(log, failed=["fred:NCBEILQ027S", "fred:GDP", "shiller",
                                                                   "treasury"]))
    result = rf.run(db, rf.services(db, market=market), steps=["market"]).result("market")
    assert (result.ok, result.stored) == (False, False)


# ---------------------------------------------------------------------------
# recompute
# ---------------------------------------------------------------------------


def test_every_company_is_recomputed_after_a_step_stored_something(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _all_recent(db, but=["market"])
    svc = rf.services(db)
    report = rf.run(db, svc)
    assert _ran(report) == ["market", "recompute"] and svc.compute.calls == [(None,)]
    assert report.result("recompute").summary["valued"] == 3


def test_nothing_is_recomputed_when_nothing_was_stored(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _all_recent(db, but=["quotes"])
    yahoo = rf.FakeYahoo(quotes={t: {"error": "down"} for t in rf.TICKERS})
    svc = rf.services(db, yahoo=yahoo)
    report = rf.run(db, svc)
    assert _ran(report) == ["quotes"] and svc.compute.calls == []
    assert report.result("recompute").reason == "no step stored anything"


def test_the_recompute_fails_when_no_company_was_valued(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    report = rf.run(db, rf.services(db, compute=rf.Recorder(0)), steps=["recompute"])
    assert (report.result("recompute").ok, report.exit_code) == (False, 1)


# ---------------------------------------------------------------------------
# refresh_log
# ---------------------------------------------------------------------------


def _log(db):
    return rf.rows(db, "SELECT id, step, started_at, finished_at, ok, summary FROM refresh_log ORDER BY id")


def test_the_log_table_is_created_with_the_other_tables(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    columns = [r[1] for r in rf.rows(db, "PRAGMA table_info(refresh_log)")]
    assert columns == ["id", "step", "started_at", "finished_at", "ok", "summary"]


def test_each_run_logs_a_row_per_step(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.run(db, rf.services(db), steps=["quotes", "market"])
    rows = _log(db)
    assert [r[1] for r in rows] == list(STEPS)
    assert [r[4] for r in rows] == [1, None, None, None, None, None, 1, None]
    assert all(r[2] == r[3] == rf.NOW.isoformat() for r in rows)
    summaries = [json.loads(r[5]) for r in rows]
    assert {s["run"] for s in summaries} == {rf.NOW.isoformat()}
    assert summaries[0]["stored"] == 3 and summaries[1]["skipped"] == "not named in --steps"


def test_a_failed_step_is_logged_with_its_reason(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    rf.run(db, rf.services(db, market=rf.Recorder(raises=OSError("down"))), steps=["market"])
    market = [r for r in _log(db) if r[1] == "market"][0]
    assert market[4] == 0 and json.loads(market[5])["error"] == "OSError: down"


def test_the_last_success_is_read_from_the_log(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    store = SQLiteStore(db)
    assert store.last_refresh_success("quotes") is None
    store.record_refresh_step("quotes", "2026-10-24T01:00:00+00:00", "2026-10-24T01:05:00+00:00", True, {})
    store.record_refresh_step("quotes", "2026-10-24T02:00:00+00:00", "2026-10-24T02:05:00+00:00", False, {})
    store.record_refresh_step("market", "2026-10-24T03:00:00+00:00", "2026-10-24T03:05:00+00:00", True, {})
    assert store.last_refresh_success("quotes") == "2026-10-24T01:05:00+00:00"


def test_the_last_success_of_a_database_without_the_log_is_none(tmp_path):
    db = tmp_path / "bare.db"
    sqlite3.connect(db).close()
    assert SQLiteStore(db).last_refresh_success("quotes") is None


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------


def test_the_lock_is_beside_the_database(tmp_path):
    assert lock_path(tmp_path / "stock.db") == tmp_path / "stock.db.refresh.lock"


def test_a_second_holder_is_refused_with_the_first_ones_pid_and_start(tmp_path):
    path = tmp_path / "stock.db.refresh.lock"
    first = RefreshLock(path)
    first.acquire("2026-10-25T12:00:00")
    try:
        holder = json.loads(path.read_text(encoding="utf-8"))
        assert holder == {"pid": os.getpid(), "started": "2026-10-25T12:00:00"}
        with pytest.raises(LockHeld) as held:
            RefreshLock(path).acquire("2026-10-25T12:01:00")
        assert held.value.holder == holder
        assert f"PID {os.getpid()}" in str(held.value) and "2026-10-25T12:00:00" in str(held.value)
    finally:
        first.release()
    second = RefreshLock(path)
    second.acquire("12:02")  # shorter than the first's: the file holds only the new holder
    second.release()
    assert json.loads(path.read_text(encoding="utf-8")) == {"pid": os.getpid(), "started": "12:02"}


def test_a_lock_left_by_a_process_that_died_is_taken(tmp_path):
    # The OS releases the lock of a process that dies (here killed while holding it): no PID check is needed.
    path = tmp_path / "stock.db.refresh.lock"
    script = ("import sys, time; sys.path.insert(0, sys.argv[1]); from src.refresh import RefreshLock; "
              "RefreshLock(sys.argv[2]).acquire('2026-10-25T12:00:00'); print('locked', flush=True); time.sleep(120)")
    child = subprocess.Popen([sys.executable, "-c", script, str(ROOT), str(path)], stdout=subprocess.PIPE,
                             text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(LockHeld) as held:
            RefreshLock(path).acquire("2026-10-25T12:01:00")
        assert held.value.holder["pid"] == child.pid
    finally:
        child.kill()
        child.wait()
        child.stdout.close()
    deadline = time.monotonic() + 5
    while True:
        lock = RefreshLock(path)
        try:
            lock.acquire("2026-10-25T12:02:00")
            break
        except LockHeld:
            assert time.monotonic() < deadline, "the dead process's lock was never released"
            time.sleep(0.05)
    lock.release()


def test_a_second_run_exits_3_naming_the_first(tmp_path, capsys):
    db = rf.make_db(tmp_path / "stock.db")
    first = RefreshLock(lock_path(db))
    first.acquire("2026-10-25T11:58:00")
    svc = rf.services(db)
    try:
        assert _main(db, tmp_path, svc=svc) == 3
    finally:
        first.release()
    out = capsys.readouterr().out
    assert f"PID {os.getpid()}" in out and "2026-10-25T11:58:00" in out
    assert svc.yahoo.quote_calls == [] and _log(db) == []
    logged = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "out" / "logs").glob("refresh-*.log"))
    assert f"PID {os.getpid()}" in logged


def test_the_lock_is_released_after_a_run_and_after_a_step_raised(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    assert _main(db, tmp_path, "--steps", "market",
                 svc=rf.services(db, market=rf.Recorder(raises=RuntimeError("boom")))) == 1
    assert _main(db, tmp_path, "--steps", "market") == 0
    lock = RefreshLock(lock_path(db))
    lock.acquire("2026-10-25T12:00:00")
    lock.release()


def test_the_lock_is_released_when_the_run_itself_raises(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")

    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(src.refresh, "run_refresh", broken)
    with pytest.raises(RuntimeError):
        _main(db, tmp_path)
    lock = RefreshLock(lock_path(db))
    lock.acquire("2026-10-25T12:00:00")
    lock.release()


# ---------------------------------------------------------------------------
# Output, the log file, the exit codes
# ---------------------------------------------------------------------------


def test_a_missing_database_exits_2_and_none_is_created(tmp_path, capsys):
    db = tmp_path / "typo" / "stock.db"
    assert _main(db, tmp_path) == 2
    assert not db.exists() and not lock_path(db).exists() and not (tmp_path / "typo").exists()
    assert "No database at" in capsys.readouterr().err


def test_the_default_database_is_the_output_dirs(tmp_path):
    db = rf.make_db(tmp_path / "out" / "output" / "stock.db")
    made = []
    code = main(["--output-dir", str(tmp_path / "out"), "--steps", "market"],
                make_services=lambda db_path, output_dir, cache_dir: made.append((db_path, output_dir, cache_dir))
                or rf.services(db))
    assert code == 0
    assert made == [(db, tmp_path / "out", Path("data/cache"))]


def test_the_output_is_printed_and_appended_to_the_days_log_file(tmp_path, capsys):
    db = rf.make_db(tmp_path / "stock.db")
    assert _main(db, tmp_path, "--steps", "market") == 0
    assert _main(db, tmp_path, "--steps", "market") == 0
    printed = capsys.readouterr().out
    logs = list((tmp_path / "out" / "logs").glob("refresh-*.log"))
    assert len(logs) == 1 and logs[0].name == f"refresh-{time.strftime('%Y-%m-%d')}.log"
    text = logs[0].read_text(encoding="utf-8")
    assert text.count("market: ran") == 2 and printed.count("market: ran") == 2
    # Each step's line in the summary, and the collector's own lines in the log.
    assert "quotes: skipped (not named in --steps)" in text and "shiller: stored" in text


def test_the_summary_says_what_each_step_did_and_lists_the_collection(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    stream = io.StringIO()
    report = rf.run(db, _queued_by_every_step(db), stream=stream)
    text = stream.getvalue()
    for step in STEPS:
        assert f"{step}: ran, ok" in text
    assert "Collection list (3): CCC (quotes: a 2-for-1 split on 2026-10-22 since its last collection)" in text
    assert "BBB (sec: 10-Q filed 2026-10-23 (b-1))" in text
    assert report.exit_code == 0


def test_a_failed_step_says_why_in_the_summary(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    stream = io.StringIO()
    rf.run(db, rf.services(db, market=rf.Recorder(raises=OSError("down"))), steps=["market"], stream=stream)
    assert "market: ran, FAILED: OSError: down" in stream.getvalue()


def test_the_console_gets_only_what_it_can_show(tmp_path):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    out = Output(stream=stream, log_path=tmp_path / "logs" / "refresh.log")
    out.say("BRK.B \u2192 BRK-B: \u20ac1")
    out.close()
    stream.flush()
    assert "BRK.B ? BRK-B: \u20ac1" in stream.buffer.getvalue().decode("cp1252")
    assert "BRK.B \u2192 BRK-B" in (tmp_path / "logs" / "refresh.log").read_text(encoding="utf-8")


def test_exit_0_when_every_step_ran_or_was_skipped(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    assert _main(db, tmp_path) == 0


def test_exit_1_when_a_step_failed(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    assert _main(db, tmp_path, svc=rf.services(db, gurus=rf.Recorder(raises=RuntimeError("x")))) == 1


# ---------------------------------------------------------------------------
# The dispatch from stock-data, and the real services
# ---------------------------------------------------------------------------


def test_stock_data_refresh_is_dispatched_to_the_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(src.refresh, "main", lambda argv: calls.append(list(argv)) or 7)
    assert src.main.main(["refresh", "--steps", "quotes", "--force"]) == 7
    assert calls == [["--steps", "quotes", "--force"]]


def test_the_console_scripts_arguments_are_dispatched_too(monkeypatch):
    calls = []
    monkeypatch.setattr(src.refresh, "main", lambda argv: calls.append(list(argv)) or 0)
    monkeypatch.setattr(sys, "argv", ["stock-data", "refresh", "--steps", "market"])
    assert src.main.main() == 0 and calls == [["--steps", "market"]]


def test_a_ticker_list_isnt_dispatched(monkeypatch, tmp_path):
    monkeypatch.setattr(src.refresh, "main", lambda argv: pytest.fail("dispatched"))
    runs = []

    class FakeFetcher:
        def __init__(self, config, logger):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch_and_export(self, tickers, **kwargs):
            runs.append(list(tickers))
            return {"tickers_requested": 1, "successful": 1, "with_warnings": 0, "with_errors": 0,
                    "duration_seconds": 0.0, "export_paths": {}}

    monkeypatch.setattr(src.main, "StockDataFetcher", FakeFetcher)
    assert src.main.main(["AAPL", "refresh", "--output-dir", str(tmp_path)]) == 0
    assert runs == [["AAPL", "REFRESH"]]


def test_the_fresh_sec_handler_reads_no_cached_payload_but_writes_one(tmp_path):
    cached = tmp_path / "submissions_CIK0000000001.json"
    cached.write_text(json.dumps({"filings": []}), encoding="utf-8")
    assert SECHandler(cache_dir=tmp_path)._read_payload_cache(cached) == {"filings": []}
    fresh = FreshSECHandler(cache_dir=tmp_path)
    assert fresh._read_payload_cache(cached) is None
    fresh._write_payload_cache(tmp_path / "companyfacts_CIK0000000002.json", {"facts": {}})
    assert json.loads((tmp_path / "companyfacts_CIK0000000002.json").read_text(encoding="utf-8")) == {"facts": {}}
    fresh.close()


def test_the_real_collection_is_stock_datas_with_the_fresh_sec_handler(tmp_path, monkeypatch):
    seen = {}

    class FakeFetcher:
        def __init__(self, config, logger):
            seen["config"] = config
            self.logger = logger
            self.sec_handler = SECHandler(cache_dir=tmp_path / "unused")
            seen["original"] = self.sec_handler

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch_and_export(self, tickers, include_yahoo, include_sec, years_back, formats):
            seen["call"] = (list(tickers), include_yahoo, include_sec, years_back, list(formats))
            seen["handler"] = self.sec_handler
            self.logger.info("Fetching data for AAA")
            self.logger.info("Completed AAA: sources=[], warnings=0, errors=0")
            self.logger.warning("Data cleaning error for AAA: x")
            self.logger.error("Failed to fetch BBB: timeout")
            return {"tickers_requested": 1}

    monkeypatch.setattr(src.refresh, "StockDataFetcher", FakeFetcher)
    lines = []
    db = tmp_path / "stock.db"
    summary = src.refresh.collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["AAA", "BBB"], lines.append)
    assert summary == {"tickers_requested": 1}
    config = seen["config"]
    assert (config.storage.database_path, config.storage.base_dir) == (db, tmp_path / "out")
    assert (config.output_formats, config.max_workers) == (["json", "csv", "sqlite"], 4)
    assert seen["call"] == (["AAA", "BBB"], True, True, None, ["json", "csv", "sqlite"])
    assert isinstance(seen["handler"], FreshSECHandler) and seen["handler"].cache_dir == tmp_path / "cache"
    assert seen["original"].session is not seen["handler"].session
    # The collector's progress: each ticker's "Completed" line and its errors, not its every message.
    assert lines == ["Completed AAA: sources=[], warnings=0, errors=0", "ERROR: Failed to fetch BBB: timeout"]


def test_the_real_collection_keeps_what_a_failed_predecessor_read_cant_give(tmp_path, monkeypatch):
    # The collect step's collection (its SEC read a FreshSECHandler's) stores a run as `stock-data` does: after one
    # that read both of BLK's filers, one whose read of the old BlackRock failed keeps the FY2022 they gave (BUG-179).
    db = tmp_path / "stock.db"
    for predecessor in (200, 503):
        serve_blk(monkeypatch, predecessor)
        summary = collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["BLK"], lambda line: None)
        assert summary["stored"] == 1
        assert stored_blk(db)[0] == BLK_FY2022


def test_the_real_services_are_the_apps_collectors(tmp_path, monkeypatch):
    db = rf.make_db(tmp_path / "stock.db")
    calls = {}
    monkeypatch.setattr(src.refresh, "collect_insiders",
                        lambda db_path, tickers, years, source, log: calls.setdefault("insiders", (
                            db_path, list(tickers), years, type(source).__name__, source.cache)) and {})
    monkeypatch.setattr(src.refresh, "collect_gurus",
                        lambda db_path, gurus, quarters, source, log: calls.setdefault("gurus", (
                            db_path, len(gurus), quarters, type(source).__name__)) and {})
    monkeypatch.setattr(src.refresh, "map_cusips",
                        lambda db_path, client, log: calls.setdefault("cusips", (db_path, type(client).__name__))
                        and {})
    monkeypatch.setattr(src.refresh, "store_guru_positions",
                        lambda db_path, gurus, log: calls.setdefault("positions", (db_path, len(gurus))) and {})
    monkeypatch.setattr(src.refresh, "collect_market",
                        lambda db_path, sources, cache_dir, log: calls.setdefault("market", (
                            db_path, tuple(sources), cache_dir)) and {})
    monkeypatch.setattr(src.refresh, "compute_and_store",
                        lambda db_path, tickers=None, logger=None: calls.setdefault("compute", (db_path, tickers))
                        and 0)
    svc = real_services(db, tmp_path / "out", tmp_path / "cache")
    try:
        assert type(svc.yahoo).__name__ == "YahooHandler" and svc.yahoo.rate_limiter.min_interval == 2.5
        assert isinstance(svc.sec, FreshSECHandler) and svc.sec.cache_dir == tmp_path / "cache"
        svc.insiders(["AAA"], print)
        svc.gurus(print)
        svc.market(print)
        svc.compute(None)
    finally:
        svc.close()
    assert calls["insiders"] == (db, ["AAA"], 3.0, "Form4Source", tmp_path / "cache" / "form4")
    assert calls["gurus"][0] == db and calls["gurus"][2:] == (8, "ThirteenFSource")
    assert calls["cusips"] == (db, "OpenFIGI") and calls["positions"][0] == db
    assert calls["market"] == (db, ("fred", "shiller", "treasury"), tmp_path / "cache")
    assert calls["compute"] == (db, None)


def test_a_collection_that_read_the_report_from_its_own_xbrl_isnt_collected_again(tmp_path, monkeypatch):
    # Q17: the collect step's collection read DLR's 10-Q of 2026-07-31 from the filing, which its companyfacts lacked:
    # its figures are whole, so the next SEC step, whose feed lists no report since, leaves DLR alone.
    db = tmp_path / "stock.db"
    serve_dlr(monkeypatch, lagging=True, instance=True)
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["DLR"], lambda line: None)["stored"] == 1
    assert stored_run(db, "DLR")[1] is None
    feed = rf.FakeSEC({"0001297996": [rf.filing("10-Q", "2026-07-31", "0001104659-26-089296")]})
    assert rf.run(db, rf.services(db, sec=feed), steps=["sec"]).queue == {}


# ---- BUG-282: a collection whose submissions request failed couldn't look for the SEC's lag ----

_UNCHECKED = "couldn't check for the SEC's lag: its submissions request failed (503 Server Error)"


def test_a_company_whose_submissions_couldnt_be_read_is_collected_again(tmp_path):
    # Its companyfacts may lack the latest report (BUG-251), and nothing would say so: the run read as complete.
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "BBB", datetime(2026, 10, 24, 9, 0), submissions="503 Server Error")
    report = rf.run(db, rf.services(db), steps=["sec"])
    assert report.queue == {"BBB": [_redo("2026-10-24", _UNCHECKED, 1)]}
    assert report.result("sec").summary["incomplete"]["BBB"]["why"] == _UNCHECKED


def test_a_company_whose_submissions_and_predecessor_couldnt_be_read_says_both(tmp_path):
    db = rf.make_db(tmp_path / "stock.db")
    _collected(db, "BBB", datetime(2026, 10, 24, 9, 0), predecessor="no XBRL data", submissions="503 Server Error")
    assert rf.run(db, rf.services(db), steps=["sec"]).queue == {"BBB": [_redo(
        "2026-10-24", f"couldn't read its predecessor's filings (no XBRL data) and {_UNCHECKED}", 1)]}


def test_unchecked_collections_get_three_tries_then_wait_for_the_next_report(tmp_path):
    # A submissions request that keeps failing (the feed gone, the CIK's filer changed) is the source's: three more
    # reads of the SEC a week apart, as for a lag.
    db = _aaa_never_collected(tmp_path)
    for at in (datetime(2026, 9, 27, 9, 0), datetime(2026, 10, 4, 9, 0), datetime(2026, 10, 11, 9, 0),
               datetime(2026, 10, 18, 9, 0)):
        _collected(db, "AAA", at, submissions="503 Server Error")
    sec = rf.run(db, rf.services(db), steps=["sec"]).result("sec")
    assert sec.summary["incomplete"] == {}
    assert sec.summary["incomplete_left"] == {"AAA": {"collected_at": "2026-10-18T09:00:00", "why": _UNCHECKED,
                                                      "collections": 4, "tries": 4, "since": "2026-09-27"}}


def test_an_unchecked_collection_inside_a_reports_gap_starts_no_new_count(tmp_path):
    # BUG-289's kind: a collection whose submissions failed between two that lacked the 10-Q's facts couldn't tell
    # whether they still lacked them; it neither ends the gap nor starts a new one.
    db = _aaa_never_collected(tmp_path)
    for i, at in enumerate((datetime(2026, 9, 27, 9, 0), datetime(2026, 10, 4, 9, 0), datetime(2026, 10, 11, 9, 0),
                            datetime(2026, 10, 18, 9, 0))):
        _collected(db, "AAA", at, report=None if i == 2 else AAA_Q2, submissions="503 Server Error" if i == 2 else None)
    sec = rf.run(db, rf.services(db), steps=["sec"]).result("sec")
    assert (sec.summary["incomplete"], list(sec.summary["incomplete_left"])) == ({}, ["AAA"])
    assert sec.summary["incomplete_left"]["AAA"]["tries"] == 4


def test_the_tries_of_an_unchecked_latest_collection():
    runs = [("2026-10-18T09:00:00", json.dumps({"submissions": "HTTPError"}), None),
            ("2026-10-11T09:00:00", None, None)]
    assert src.refresh.incomplete_tries(runs) == {"tries": 1, "collections": 1, "since": "2026-10-18"}


# ---- BUG-365 (code review 34): a collection that names no report, after a report newer than the gap's ----

_Q2_LEFT = [(_local(2026, 8, day, 13, 0), AAA_Q2, None, None) for day in (1, 8, 15, 22)]
#: AAA's feed: its 10-Q of 2026-10-23 and its 10-Q of 2026-07-31 (the gap's).
_AAA_BOTH = [rf.filing("10-Q", "2026-10-23", AAA_Q3["accession"]), rf.filing("10-Q", "2026-07-31", AAA_Q2["accession"])]


def _named_none(at, lacked):
    """A ``collection_runs`` row (collected_at, incomplete, sec_read_at) of a collection that names no report: its
    submissions unread, or its predecessor's filings alone."""
    return (at.isoformat(), json.dumps(lacked), None)


@pytest.mark.parametrize("lacked, why", [
    ({"submissions": "503 Server Error"}, _UNCHECKED),
    ({"predecessor": "no XBRL data"}, "couldn't read its predecessor's filings (no XBRL data)"),
])
def test_a_collection_naming_no_report_after_an_abandoned_gap_gets_its_own_tries(tmp_path, lacked, why):
    # BUG-365: AAA's 10-Q of 2026-07-31 went without facts at four reads a week apart (left). Its 10-Q of 2026-10-23
    # put it on the list, and that collection's submissions request failed (BUG-282), or it read the report and not
    # its predecessor's filings: it was joined to July's gap (five tries) and left, waiting a quarter.
    db = _aaa_never_collected(tmp_path)
    for at, report, _, _ in _Q2_LEFT:
        _collected(db, "AAA", at, report=report)
    at = _local(2026, 10, 24, 13, 0)  # 09:00 in New York, the day after the 10-Q
    _collected(db, "AAA", at, predecessor=lacked.get("predecessor"), submissions=lacked.get("submissions"))
    report = rf.run(db, rf.services(db, sec=rf.FakeSEC({rf.cik_of("AAA"): _AAA_BOTH})), steps=["sec"])
    assert report.queue == {"AAA": [_redo(str(at)[:10], why, 1)]}
    assert report.result("sec").summary["incomplete_left"] == {}


def test_the_report_a_collection_naming_none_looked_for_is_the_feeds_latest_by_its_read():
    # Read before the 10-Q of 2026-10-23 was filed, the collection looked for July's 10-Q: a try at its gap. Read on
    # the 10-Q's day (the SEC's) or after it, the 10-Q's: its own. Without a feed, nothing ends the gap.
    gap = _runs(*_Q2_LEFT)
    before = [_named_none(_local(2026, 10, 23, 2, 0), {"submissions": "503"})] + gap   # 22:00 NY on 10-22
    on_the_day = [_named_none(_local(2026, 10, 23, 20, 0), {"submissions": "503"})] + gap   # 16:00 NY on 10-23
    assert src.refresh.incomplete_tries(before, _AAA_BOTH) == {"tries": 5, "collections": 5,
                                                               "since": str(gap[-1][0])[:10]}
    assert src.refresh.incomplete_tries(on_the_day, _AAA_BOTH) == {"tries": 1, "collections": 1,
                                                                   "since": str(on_the_day[0][0])[:10]}
    assert src.refresh.incomplete_tries(on_the_day)["collections"] == 5
    assert src.refresh.incomplete_tries(on_the_day, [])["collections"] == 5


def test_collections_naming_no_report_before_and_after_a_new_report_split_at_it():
    runs = [_named_none(_local(2026, 10, 31, 13, 0), {"submissions": "503"}),
            _named_none(_local(2026, 10, 17, 13, 0), {"submissions": "503"})] + _runs(*_Q2_LEFT)
    assert src.refresh.incomplete_tries(runs, _AAA_BOTH) == {"tries": 1, "collections": 1,
                                                             "since": str(runs[0][0])[:10]}


@pytest.mark.parametrize("later", [
    rf.filing("10-Q/A", "2026-10-01", "0000000001-26-000004"),                # an amendment
    {**rf.filing("10-Q", "2026-10-01", "0000000001-26-000004"), "isXBRL": 0},  # a report without XBRL
    rf.filing("8-K", "2026-10-01", "0000000001-26-000004"),
])
def test_a_filing_the_collection_doesnt_look_for_doesnt_end_the_gap(later):
    # A collection names the latest report as filed, with XBRL (``sec_handler._latest_report``): a filing it wouldn't
    # name leaves the run a try at July's report.
    runs = [_named_none(_local(2026, 10, 24, 13, 0), {"submissions": "503"})] + _runs(*_Q2_LEFT)
    assert src.refresh.incomplete_tries(runs, [later, _AAA_BOTH[1]])["collections"] == 5


def test_a_collection_naming_the_older_report_on_the_new_reports_day_doesnt_reopen_the_older_gap():
    # Read at 16:00 New York on 2026-10-23, before the 10-Q was accepted that day, a collection named July's report:
    # the day is the 10-Q's (it may have seen it), so it stays in the latest run's gap, and the reads before the 10-Q
    # (July's gap, given up on) don't join it.
    runs = [_named_none(_local(2026, 10, 24, 13, 0), {"submissions": "503"})] + _runs(
        (_local(2026, 10, 23, 20, 0), AAA_Q2, None, None), *_Q2_LEFT)
    assert src.refresh.incomplete_tries(runs, _AAA_BOTH) == {"tries": 1, "collections": 2,
                                                             "since": str(runs[1][0])[:10]}


def test_a_collection_naming_its_report_isnt_read_from_the_feed():
    # It said which report it lacked: July's, on a read of 2026-10-24 (the SEC hadn't listed the 10-Q of 10-23 yet).
    runs = _runs((_local(2026, 10, 24, 13, 0), AAA_Q2, None, None), *_Q2_LEFT)
    assert src.refresh.incomplete_tries(runs, _AAA_BOTH)["collections"] == 5


def test_the_real_collection_stores_a_failed_submissions_read_and_the_sec_step_collects_it_again(tmp_path,
                                                                                                 monkeypatch):
    # BUG-282 through the collect step's collection: DLR's run whose submissions request failed goes on the list
    # again at the next SEC step; once a run reads them, DLR is off it.
    db = tmp_path / "stock.db"
    quiet = rf.FakeSEC()  # DLR's feed lists no report since
    serve_dlr(monkeypatch, lagging=False, submissions=503)
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["DLR"], lambda line: None)["stored"] == 1
    queue = rf.run(db, rf.services(db, sec=quiet), steps=["sec"]).queue
    assert list(queue) == ["DLR"] and queue["DLR"][0].startswith("sec: its collection of ")
    assert queue["DLR"][0].endswith(" couldn't check for the SEC's lag: its submissions request failed (HTTPError); "
                                    "collected again (1 of 3)")
    serve_dlr(monkeypatch, lagging=False)
    assert collect_tickers(db, tmp_path / "out", tmp_path / "cache", ["DLR"], lambda line: None)["stored"] == 1
    assert rf.run(db, rf.services(db, sec=quiet), steps=["sec"]).queue == {}
