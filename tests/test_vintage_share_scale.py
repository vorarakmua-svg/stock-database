"""A weighted count no EPS checks takes the scale of its period's other weighted count where that one's EPS checks
it (BUG-369), in the record and in each filing's view (vintage)."""

from src.parsers.annual_snapshots import build_snapshots
from src.parsers.share_scale import normalize_share_scale
from src.parsers.xbrl_parser import XBRLParser


def _cop_fy2018_view():
    # COP's FY2018 10-K: both counts in thousands; basic EPS 5.36 on $6,257M puts the basic count right; no diluted
    # EPS.
    return {"net_income": 6_257_000_000.0, "eps_basic": 5.36, "weighted_avg_shares_basic": 1_166_499.0,
            "weighted_avg_shares_diluted": 1_175_538.0}


def test_a_weighted_count_no_eps_checks_follows_the_other_one_its_eps_checks():
    views = {"0001193125-19-043841": _cop_fy2018_view()}
    normalize_share_scale(views, neighbours=False)
    view = views["0001193125-19-043841"]
    assert view["weighted_avg_shares_diluted"] == 1_175_538_000.0
    assert view["_source_tags"]["weighted_avg_shares_diluted"] == "rescaled x1000"
    assert view["weighted_avg_shares_basic"] == 1_166_499_000.0


def test_the_basic_count_follows_a_checked_diluted_count_too():
    view = {"net_income": 1_000_000_000.0, "eps_diluted": 2.0, "weighted_avg_shares_diluted": 500_000_000.0,
            "weighted_avg_shares_basic": 495.0}
    periods = {"2020": view}
    normalize_share_scale(periods, neighbours=False)
    assert view["weighted_avg_shares_basic"] == 495_000_000.0


def test_a_count_an_eps_puts_below_100000_shares_sets_no_other_counts_scale():
    # DXCM's 10-Q tags Q1 2012's diluted EPS as -210,000, which rescales its diluted count of 67.8 million to 67.8:
    # no listed company's count, so the basic count beside it, no EPS of its own, stays.
    view = {"net_income": -14_100_000.0, "eps_diluted": -210_000.0, "weighted_avg_shares_diluted": 67_800_000.0,
            "weighted_avg_shares_basic": 67_800_000.0}
    normalize_share_scale({"2012-03-31": view})
    assert view["weighted_avg_shares_basic"] == 67_800_000.0

def test_a_count_put_right_by_the_other_is_no_reference_for_other_periods():
    # Pass 2 gives a period without EPS the scale of the counts the EPS corrected; one put right by its period's
    # other count isn't one of them.
    periods = {"2019": {"net_income": 1_000_000_000.0, "eps_basic": 2.0, "weighted_avg_shares_basic": 500_000_000.0,
                        "weighted_avg_shares_diluted": 505_000.0},
               "2020": {"weighted_avg_shares_diluted": 510_000.0}}
    normalize_share_scale(periods, neighbours=False)
    assert (periods["2019"]["weighted_avg_shares_diluted"], periods["2020"]["weighted_avg_shares_diluted"]) == (
        505_000_000.0, 510_000.0)

def test_counts_further_apart_than_share_scales_tolerance_stay():
    # A dilution of more than 10% is no scale error to put right.
    view = {"net_income": 1_000_000_000.0, "eps_basic": 2.0, "weighted_avg_shares_basic": 500_000_000.0,
            "weighted_avg_shares_diluted": 600_000.0}
    normalize_share_scale({"2020": view}, neighbours=False)
    assert view["weighted_avg_shares_diluted"] == 600_000.0


def test_a_count_its_own_eps_checks_isnt_moved_by_the_other():
    # its own EPS, in step with it, checks it as filed
    view = {"net_income": 1_000_000_000.0, "eps_basic": 2.0, "eps_diluted": 2000.0,
            "weighted_avg_shares_basic": 500_000_000.0, "weighted_avg_shares_diluted": 500_000.0}
    normalize_share_scale({"2020": view}, neighbours=False)
    assert view["weighted_avg_shares_diluted"] == 500_000.0


def test_the_shares_outstanding_keep_following_an_eps_checked_count():
    # The shares outstanding follow a count its own EPS checks (as before), not one put right beside it: 455 million
    # is within 10% of the basic count, not of the diluted one.
    view = {"net_income": 1_000_000_000.0, "eps_basic": 2.0, "weighted_avg_shares_basic": 500_000_000.0,
            "weighted_avg_shares_diluted": 540.0, "shares_outstanding": 455_000.0}
    normalize_share_scale({"2020": view}, neighbours=False)
    assert (view["weighted_avg_shares_diluted"], view["shares_outstanding"]) == (540_000_000.0, 455_000_000.0)


def _fact(val, filed, accn):
    return {"val": val, "start": "2018-01-01", "end": "2018-12-31", "form": "10-K", "filed": filed, "accn": accn,
            "fy": int(filed[:4]), "fp": "FY"}


def test_each_filings_view_holds_the_diluted_count_in_units():
    # BUG-369: COP's FY2018-20 vintages held the diluted count as filed in thousands (1.18e6 for 1.18e9), as no earlier
    # sibling's EPS checked it; its basic EPS checks the basic count in the same view.
    filings = (("2019-02-19", "0001193125-19-043841"), ("2020-02-18", "0001193125-20-039954"))
    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [_fact(6_257_000_000.0, *f) for f in filings]}},
        "EarningsPerShareBasic": {"units": {"USD/shares": [_fact(5.36, *f) for f in filings]}},
        "WeightedAverageNumberOfSharesOutstandingBasic": {"units": {"shares": [
            _fact(1_166_499.0, *f) for f in filings]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            _fact(1_175_538.0, *f) for f in filings]}}}}}
    snapshots = build_snapshots(XBRLParser(), facts, ticker="COP", years_back=None)
    views = snapshots.vintages["2018"]
    assert [views[accn]["weighted_avg_shares_diluted"] for _, accn in filings] == [1_175_538_000.0] * 2
    assert {row["weighted_avg_shares_diluted"] for row in snapshots.rows} == {1_175_538_000.0}


def test_a_count_net_income_over_eps_misses_takes_the_scale_that_brings_it_near():
    # BUG-339 (option A): NTRS's FY2011 10-K tags FY2009's counts at 235.5 trillion; preferred dividends put
    # $864.2M / 3.16 = 273M 16% from its counts, beyond the 10% that checks one, but within a factor of two.
    view = {"net_income": 864_200_000.0, "eps_basic": 3.18, "eps_diluted": 3.16,
            "weighted_avg_shares_basic": 235_511_879_000_000.0, "weighted_avg_shares_diluted": 236_416_029_000_000.0,
            "shares_outstanding": 241_679_942.0}
    normalize_share_scale({"2009": view}, neighbours=False, loose=True)
    assert (view["weighted_avg_shares_basic"], view["weighted_avg_shares_diluted"]) == (235_511_879.0, 236_416_029.0)


def test_a_count_within_a_factor_of_ten_of_net_income_over_eps_keeps_its_scale():
    view = {"net_income": 864_200_000.0, "eps_diluted": 3.16, "weighted_avg_shares_diluted": 150_000_000.0}
    normalize_share_scale({"2009": view}, neighbours=False, loose=True)
    assert view["weighted_avg_shares_diluted"] == 150_000_000.0


def test_the_shares_outstanding_follow_whichever_checked_count_they_land_near():
    # BUG-339 (option A): TER's FY2022 10-K tags FY2021's shares outstanding in thousands, 162,251: 1.6% from the
    # basic count its EPS checks, 11.6% from the diluted one.
    view = {"net_income": 1_014_589_000.0, "eps_basic": 6.15, "eps_diluted": 5.53,
            "weighted_avg_shares_basic": 164_960_000.0, "weighted_avg_shares_diluted": 183_625_000.0,
            "shares_outstanding": 162_251.0}
    normalize_share_scale({"2021": view}, neighbours=False)
    assert view["shares_outstanding"] == 162_251_000.0


def test_the_quarters_take_no_loose_scale():
    # BUG-339 (option A): the quarters' derived counts and rounded EPS mislead it (BE's 2023 diluted counts read
    # 206,724 for 206.7 million), so the fetcher's call, without *loose*, leaves them.
    view = {"net_income": 864_200_000.0, "eps_diluted": 3.16, "weighted_avg_shares_diluted": 236_416_029_000_000.0}
    normalize_share_scale({"2009-12-31": view})
    assert view["weighted_avg_shares_diluted"] == 236_416_029_000_000.0


def test_the_record_and_each_view_take_the_loose_scale():
    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [_fact(864_200_000.0, "2019-02-25", "a1")]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [_fact(3.16, "2019-02-25", "a1")]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            _fact(236_416_029_000_000.0, "2019-02-25", "a1")]}}}}}
    snapshots = build_snapshots(XBRLParser(), facts, ticker="NTRS", years_back=None)
    assert snapshots.vintages["2018"]["a1"]["weighted_avg_shares_diluted"] == 236_416_029.0
    assert {row["weighted_avg_shares_diluted"] for row in snapshots.rows} == {236_416_029.0}


def test_every_view_of_a_year_takes_the_loose_scale():
    # AMT FY2015: its 2016 view is in units, 13% from $685.1M / 1.42; its 2017 view tags the counts in thousands
    views = {"2016": {"net_income": 685_074_000.0, "eps_basic": 1.42, "weighted_avg_shares_basic": 418_907_000.0},
             "2017": {"net_income": 685_074_000.0, "eps_basic": 1.42, "weighted_avg_shares_basic": 419_000.0}}
    normalize_share_scale(views, neighbours=False, loose=True)
    assert [views[a]["weighted_avg_shares_basic"] for a in ("2016", "2017")] == [418_907_000.0, 419_000_000.0]
