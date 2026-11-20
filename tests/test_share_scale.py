"""Share-count scale normalization.

SEC XBRL facts are stored as filed, and some filers tag share counts in
thousands or millions rather than units — ConocoPhillips filed diluted shares
as ``1,245,440`` (thousands) through FY2019 and ``1,078,030,000`` (units) after;
McDonald's filed ``752`` (millions) from FY2021. The values are wrong at the
source, so a faithful parser stores wrong numbers.

EPS and net income come from the SAME filing and are correctly scaled, so
``net_income / eps_diluted`` is a reliable oracle for the true share count.
"""

from src.parsers.share_scale import normalize_share_scale


def test_thousands_scaled_diluted_shares_are_corrected():
    """COP's real shape: shares filed in thousands, EPS/net income in units."""
    periods = {
        "2016": {
            "net_income": 3_615_000_000.0 * -1,  # sign irrelevant to the ratio
            "eps_diluted": -2.91,
            "weighted_avg_shares_diluted": 1_245_440.0,
        },
    }
    normalize_share_scale(periods)
    # implied = 3.615e9 / 2.91 = 1.242e9 -> the stored value was 1000x too small
    assert periods["2016"]["weighted_avg_shares_diluted"] == 1_245_440_000.0
    assert periods["2016"]["_source_tags"]["weighted_avg_shares_diluted"] == "rescaled x1000"


def test_millions_scaled_shares_are_corrected():
    """MCD's real shape: shares filed in millions."""
    periods = {
        "2021": {
            "net_income": 7_545_000_000.0,
            "eps_diluted": 10.04,
            "weighted_avg_shares_diluted": 752.0,
        },
    }
    normalize_share_scale(periods)
    assert periods["2021"]["weighted_avg_shares_diluted"] == 752_000_000.0


def test_basic_shares_use_their_own_eps_oracle():
    periods = {
        "2016": {
            "net_income": 1_000_000_000.0,
            "eps_basic": 2.0,
            "weighted_avg_shares_basic": 500_000.0,
        },
    }
    normalize_share_scale(periods)
    assert periods["2016"]["weighted_avg_shares_basic"] == 500_000_000.0


def test_correctly_scaled_shares_are_left_alone():
    periods = {
        "2020": {
            "net_income": 2_700_000_000.0,
            "eps_diluted": 2.5,
            "weighted_avg_shares_diluted": 1_080_000_000.0,
        },
    }
    normalize_share_scale(periods)
    assert periods["2020"]["weighted_avg_shares_diluted"] == 1_080_000_000.0
    assert "_source_tags" not in periods["2020"]


def test_noisy_oracle_never_rescales():
    """A near-zero EPS makes net_income/eps meaningless (GE FY2022: EPS 0.05).

    No power-of-1000 correction lands near the implied value, so the honest
    answer is to leave the reported number alone rather than invent a scale.
    """
    periods = {
        "2022": {
            "net_income": 336_000_000.0,
            "eps_diluted": 0.05,  # implied 6.72e9 — nowhere near a 1000x of 1.101e9
            "weighted_avg_shares_diluted": 1_101_000_000.0,
        },
    }
    normalize_share_scale(periods)
    assert periods["2022"]["weighted_avg_shares_diluted"] == 1_101_000_000.0


def test_period_without_eps_falls_back_to_corrected_siblings():
    """COP FY2018 has no EPS, so it has no oracle of its own.

    Leaving it un-rescaled while its neighbours are corrected would manufacture
    a brand-new 1000x seam in the middle of the series — the exact artifact this
    normalization exists to remove. Fall back to the corrected sibling scale.
    """
    periods = {
        "2017": {
            "net_income": 855_000_000.0 * -1,
            "eps_diluted": -0.70,
            "weighted_avg_shares_diluted": 1_221_038.0,
        },
        "2018": {  # no eps_diluted at all
            "weighted_avg_shares_diluted": 1_175_538.0,
        },
        "2019": {
            "net_income": 7_189_000_000.0,
            "eps_diluted": 6.40,
            "weighted_avg_shares_diluted": 1_123_536.0,
        },
    }
    normalize_share_scale(periods)
    assert periods["2017"]["weighted_avg_shares_diluted"] == 1_221_038_000.0
    assert periods["2019"]["weighted_avg_shares_diluted"] == 1_123_536_000.0
    assert periods["2018"]["weighted_avg_shares_diluted"] == 1_175_538_000.0
    assert periods["2018"]["_source_tags"]["weighted_avg_shares_diluted"] == "rescaled x1000"


def test_shares_outstanding_rescaled_against_corrected_weighted_average():
    """shares_outstanding has no EPS oracle; the weighted-average count is its
    reference (the two track within a few percent for a normal year)."""
    periods = {
        "2016": {
            "net_income": 1_000_000_000.0,
            "eps_diluted": 2.0,
            "weighted_avg_shares_diluted": 500_000.0,
            "shares_outstanding": 505_000.0,
        },
    }
    normalize_share_scale(periods)
    assert periods["2016"]["weighted_avg_shares_diluted"] == 500_000_000.0
    assert periods["2016"]["shares_outstanding"] == 505_000_000.0


def _hsy_years():
    """HSY's FY2008-13 record as the parser reads it after its restored FY2009-10 basic counts: no EPS in the company
    facts, and the FY2011 10-K's FY2009 diluted and FY2011 basic counts in thousands."""
    counts = {"2008": (227_035_071.0, 227_486_000.0, None), "2009": (227_998_276.0, 227_845_000.0, 228_995.0),
              "2010": (227_030_232.0, 227_740_000.0, 230_313_000.0), "2011": (225_205_918.0, 226_574.0, 229_919_000.0),
              "2012": (223_786_030.0, 225_036_000.0, 228_337_000.0),
              "2013": (223_894_721.0, 224_176_000.0, 227_203_000.0)}
    return {fy: {k: v for k, v in zip(("shares_outstanding", "weighted_avg_shares_basic",
                                       "weighted_avg_shares_diluted"), values) if v is not None}
            for fy, values in counts.items()}


def test_shares_outstanding_follow_a_count_its_eps_confirms():
    # A weighted count its own EPS confirms as filed is checked: the shares outstanding follow it.
    periods = {"2016": {"net_income": 1_000_000_000.0, "eps_diluted": 2.0,
                        "weighted_avg_shares_diluted": 500_000_000.0, "shares_outstanding": 505_000.0}}
    normalize_share_scale(periods)
    assert periods["2016"]["shares_outstanding"] == 505_000_000.0


def test_shares_outstanding_follow_a_count_its_siblings_put_right():
    # COP's FY2018 has no EPS; its diluted count takes its corrected siblings' scale, and its shares outstanding
    # follow it.
    periods = {
        "2017": {"net_income": -855_000_000.0, "eps_diluted": -0.70, "weighted_avg_shares_diluted": 1_221_038.0},
        "2018": {"weighted_avg_shares_diluted": 1_175_538.0, "shares_outstanding": 1_140_000.0},
        "2019": {"net_income": 7_189_000_000.0, "eps_diluted": 6.40, "weighted_avg_shares_diluted": 1_123_536.0},
    }
    normalize_share_scale(periods)
    assert periods["2018"]["shares_outstanding"] == 1_140_000_000.0


def test_an_unchecked_count_sets_no_scale_for_the_shares_outstanding():
    # BUG-329: HSY's FY2009 shares outstanding, 227,998,276, read 227,998 beside a diluted count tagged in thousands
    # that no EPS checks.
    periods = {"2009": {"shares_outstanding": 227_998_276.0, "weighted_avg_shares_basic": 227_845.0,
                        "weighted_avg_shares_diluted": 228_995.0, "net_income": 435_994_000.0}}
    normalize_share_scale(periods)
    assert periods["2009"]["shares_outstanding"] == 227_998_276.0


def test_an_unchecked_count_takes_the_scale_of_its_neighbouring_years():
    # BUG-329: HSY's FY2011 basic count (226,574) and FY2009 diluted count (228,995), in thousands, beside years in
    # units; the shares outstanding stay.
    periods = _hsy_years()
    normalize_share_scale(periods)
    assert (periods["2011"]["weighted_avg_shares_basic"], periods["2009"]["weighted_avg_shares_diluted"]) == (
        226_574_000.0, 228_995_000.0)
    assert periods["2011"]["_source_tags"]["weighted_avg_shares_basic"] == "rescaled x1000"
    assert [periods[fy]["shares_outstanding"] for fy in sorted(periods)] == [
        227_035_071.0, 227_998_276.0, 227_030_232.0, 225_205_918.0, 223_786_030.0, 223_894_721.0]


def test_a_count_whose_neighbours_disagree_keeps_its_scale():
    # Three years in thousands, then three in units, with no EPS: at the seam the neighbours split, and no count moves.
    values = (1_200_000.0, 1_190_000.0, 1_180_000.0, 1_170_000_000.0, 1_160_000_000.0, 1_150_000_000.0)
    periods = {str(2010 + i): {"weighted_avg_shares_basic": v} for i, v in enumerate(values)}
    normalize_share_scale(periods)
    assert tuple(periods[k]["weighted_avg_shares_basic"] for k in sorted(periods)) == values


def test_a_count_with_one_neighbour_keeps_its_scale():
    periods = {"2009": {"weighted_avg_shares_basic": 227_845.0}, "2010": {"weighted_avg_shares_basic": 227_740_000.0}}
    normalize_share_scale(periods)
    assert periods["2009"]["weighted_avg_shares_basic"] == 227_845.0


def test_a_count_its_eps_checks_stays_beside_neighbours_on_another_scale():
    # A count its own EPS confirms isn't moved by neighbours left on another scale; nor are they, whose neighbours
    # are then on two scales.
    values = (1_200.0, 1_190.0, 1_180_000_000.0, 1_170.0, 1_160.0)
    periods = {str(2010 + i): {"weighted_avg_shares_diluted": v} for i, v in enumerate(values)}
    periods["2012"].update(net_income=1_180_000_000.0, eps_diluted=1.0)
    normalize_share_scale(periods)
    assert tuple(periods[k]["weighted_avg_shares_diluted"] for k in sorted(periods)) == values


def test_a_derived_count_takes_no_neighbours_scale():
    # A count differenced from rungs on two scales is no filed figure to put on a scale.
    periods = {str(2010 + i): {"weighted_avg_shares_basic": v} for i, v in enumerate(
        (227_000_000.0, 226_000_000.0, 418_739.8, 225_000_000.0, 224_000_000.0))}
    periods["2012"]["_source_tags"] = {"weighted_avg_shares_basic": "derived:ytd_diff"}
    normalize_share_scale(periods)
    assert periods["2012"]["weighted_avg_shares_basic"] == 418_739.8


def test_a_count_beside_its_periods_other_count_keeps_its_scale():
    # HIG's Q1 2010 diluted count, 393,700,000, beside the same basic count, amid quarters tagged in millions: the
    # neighbours are the ones off.
    values = (416.2, 400.0, 393_700_000.0, 480.2, 495.3)
    periods = {str(2010 + i): {"weighted_avg_shares_diluted": v} for i, v in enumerate(values)}
    periods["2012"]["weighted_avg_shares_basic"] = 393_700_000.0
    normalize_share_scale(periods)
    assert periods["2012"]["weighted_avg_shares_diluted"] == 393_700_000.0


def test_a_count_no_power_of_1000_brings_near_its_neighbours_keeps_its_scale():
    # FITB's Q3 2010 diluted count of 6,000,000 beside about 800 million (6 billion is 7.5 times off), and a spin-off's
    # 100 shares beside 345 million.
    periods = {str(2010 + i): {"weighted_avg_shares_diluted": v, "shares_outstanding": s} for i, (v, s) in
               enumerate(((790e6, 345e6), (802e6, 346e6), (6e6, 100.0), (800e6, 347e6), (805e6, 348e6)))}
    normalize_share_scale(periods)
    assert (periods["2012"]["weighted_avg_shares_diluted"], periods["2012"]["shares_outstanding"]) == (6e6, 100.0)


def test_a_count_takes_the_scale_most_of_its_neighbours_share():
    # MSI's Q2 2009 shares outstanding filed as 327.914 (millions) beside its weighted counts in units; of its
    # neighbours, the year end before is on the basis before its 1:7 reverse split (2,276.5 million).
    periods = {"2008-12-31": {"shares_outstanding": 2_276_500_000.0},
               "2009-07-04": {"shares_outstanding": 327.914, "weighted_avg_shares_basic": 327_700_000.0},
               "2009-10-03": {"shares_outstanding": 330_128_571.0}, "2009-12-31": {"shares_outstanding": 330_300_000.0}}
    normalize_share_scale(periods)
    assert periods["2009-07-04"]["shares_outstanding"] == 327_914_000.0


def test_a_count_whose_neighbours_share_no_scale_keeps_its_own():
    # Two of four neighbours agree, which is no majority (each neighbour's EPS checks it, so only 2012 can move).
    values = (100_000.0, 100_000_000.0, 100_000.0, 125_000_000.0, 100_000_000_000.0)
    periods = {str(2010 + i): {"weighted_avg_shares_basic": v, "net_income": v, "eps_basic": 1.0}
               for i, v in enumerate(values)}
    del periods["2012"]["eps_basic"]
    normalize_share_scale(periods)
    assert periods["2012"]["weighted_avg_shares_basic"] == 100_000.0


def test_without_neighbours_an_unchecked_count_keeps_its_scale():
    # A filing's vintages of one year are other filings' views, later ones among them (BUG-329: no look-ahead).
    periods = _hsy_years()
    normalize_share_scale(periods, neighbours=False)
    assert (periods["2011"]["weighted_avg_shares_basic"], periods["2009"]["weighted_avg_shares_diluted"]) == (
        226_574.0, 228_995.0)


def test_empty_and_missing_fields_are_safe():
    periods = {"2020": {}, "2021": {"net_income": 5.0}}
    normalize_share_scale(periods)  # must not raise
    assert periods["2020"] == {}
