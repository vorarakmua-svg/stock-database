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


def test_empty_and_missing_fields_are_safe():
    periods = {"2020": {}, "2021": {"net_income": 5.0}}
    normalize_share_scale(periods)  # must not raise
    assert periods["2020"] == {}
