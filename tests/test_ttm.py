"""Tests for the trailing-twelve-month (TTM) series."""

from src.parsers.ttm import compute_ttm


def _quarters():
    # Five consecutive ~quarterly periods with a flow (net_income) and an instant
    # balance-sheet value (total_assets).
    ni = {"2024-03-31": 10, "2024-06-30": 20, "2024-09-30": 30,
          "2024-12-31": 40, "2025-03-31": 50}
    ta = {"2024-03-31": 100, "2024-06-30": 110, "2024-09-30": 120,
          "2024-12-31": 130, "2025-03-31": 140}
    return {e: {"period_end": e, "net_income": ni[e], "total_assets": ta[e],
                "calendar_year": int(e[:4]), "calendar_quarter": (int(e[5:7]) - 1) // 3 + 1}
            for e in ni}


def test_ttm_sums_trailing_four_quarters():
    ttm = compute_ttm(_quarters())
    # Only quarters with 4 consecutive predecessors get a TTM row.
    assert "2024-12-31" in ttm and "2025-03-31" in ttm
    assert "2024-09-30" not in ttm  # only 3 quarters available
    assert ttm["2024-12-31"]["net_income"] == 10 + 20 + 30 + 40
    assert ttm["2025-03-31"]["net_income"] == 20 + 30 + 40 + 50


def test_ttm_balance_sheet_is_as_of_latest():
    ttm = compute_ttm(_quarters())
    # Instant concept = the latest quarter-end value, not a sum.
    assert ttm["2025-03-31"]["total_assets"] == 140


def test_ttm_skips_when_gap_breaks_continuity():
    q = _quarters()
    # Remove a middle quarter -> no 4-consecutive window spanning the gap.
    del q["2024-09-30"]
    ttm = compute_ttm(q)
    assert "2024-12-31" not in ttm  # window would skip a quarter


def test_ttm_empty():
    assert compute_ttm({}) == {}
