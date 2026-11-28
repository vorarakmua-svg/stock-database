"""A mis-scaled EPS is no oracle for a count's scale, and a count its own EPS confirms is never rescaled (BUG-391)."""

from src.parsers.share_scale import normalize_share_scale


def _dxcm():
    # DXCM's 10-Qs tag Q1 and Q2 2012's diluted EPS as -210,000 and 209,999.58 beside 67.8 and 68.6 million shares:
    # net income over EPS implies 67 shares. Q3's EPS (-0.25) is right; Q4 has none.
    return {"2012-03-31": {"net_income": -14_100_000.0, "eps_diluted": -210_000.0,
                           "weighted_avg_shares_diluted": 67_800_000.0, "weighted_avg_shares_basic": 67_800_000.0},
            "2012-06-30": {"net_income": -14_700_000.0, "eps_diluted": 209_999.58,
                           "weighted_avg_shares_diluted": 68_600_000.0, "weighted_avg_shares_basic": 68_600_000.0},
            "2012-09-30": {"net_income": -17_200_000.0, "eps_diluted": -0.25,
                           "weighted_avg_shares_diluted": 69_100_000.0},
            "2012-12-31": {"weighted_avg_shares_diluted": 69_293_478.0}}


def test_an_eps_implying_fewer_than_100000_shares_rescales_no_count():
    # BUG-391: it rescaled the diluted counts to 67.8 and 68.6, and the basic ones followed them (BUG-369's pass 1b)
    periods = _dxcm()
    normalize_share_scale(periods)
    assert [periods["2012-03-31"][k] for k in ("weighted_avg_shares_diluted", "weighted_avg_shares_basic")] == [
        67_800_000.0, 67_800_000.0]
    assert periods["2012-06-30"]["weighted_avg_shares_diluted"] == 68_600_000.0


def test_a_mis_scaled_eps_sets_no_other_periods_scale():
    # ... and pass 2 took the median of those "corrections" as every other quarter's reference: Q3 and Q4 read 69.1
    # and 69.3
    periods = _dxcm()
    normalize_share_scale(periods)
    assert [periods[q]["weighted_avg_shares_diluted"] for q in ("2012-09-30", "2012-12-31")] == [
        69_100_000.0, 69_293_478.0]


def test_a_count_its_own_eps_confirms_is_never_rescaled_to_its_siblings():
    # HAL's shape: one quarter's EPS a thousand times its size (420 for 0.42) puts its count a thousand times too low,
    # which pass 2 then gave the quarters beside it, though their own EPS confirms them as filed (873 million read 873)
    periods = {"2017-06-30": {"net_income": 365_000_000.0, "eps_diluted": 420.0, "weighted_avg_shares_diluted": 873e6},
               "2017-09-30": {"net_income": 367_000_000.0, "eps_diluted": 0.42, "weighted_avg_shares_diluted": 875e6},
               "2017-12-31": {"weighted_avg_shares_diluted": 876e6}}
    normalize_share_scale(periods)
    assert periods["2017-09-30"]["weighted_avg_shares_diluted"] == 875e6


def test_a_count_in_thousands_its_eps_puts_right_still_is():
    # COP's FY2016 diluted count, 1,245,440 (thousands), beside EPS -2.91 on $3.615B: implied 1.24 billion
    periods = {"2016": {"net_income": -3_615_000_000.0, "eps_diluted": -2.91,
                        "weighted_avg_shares_diluted": 1_245_440.0}}
    normalize_share_scale(periods)
    assert periods["2016"]["weighted_avg_shares_diluted"] == 1_245_440_000.0
