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
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import src.main
import src.refresh
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
    # The collection's day is the collector's own: east of New York it runs ahead, so the same day may be after it.
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
