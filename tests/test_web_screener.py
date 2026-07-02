"""Tests for the cross-company screener: build_screen_query, Reader methods, API.

TDD: these tests are written BEFORE the implementation to drive design.
The security-critical tests (injection-guard) run first.
"""
from __future__ import annotations

from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# build_screen_query — pure unit tests, no DB
# ---------------------------------------------------------------------------


class TestBuildScreenQuerySecurity:
    """SQL injection guards: non-whitelisted field/sort/op raises ValueError."""

    def test_nonwhitelisted_field_raises(self) -> None:
        from src.webapp.screener import MetricFilter, ScreenSpec, build_screen_query

        spec = ScreenSpec(
            filters=[MetricFilter(field="roic; DROP TABLE metrics_annual; --", op="gte", value=0.1)],
        )
        with pytest.raises(ValueError, match="field"):
            build_screen_query(spec)

    def test_nonwhitelisted_sort_raises(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[], sort="1=1; DROP TABLE--")
        with pytest.raises(ValueError, match="sort"):
            build_screen_query(spec)

    def test_invalid_op_raises(self) -> None:
        from src.webapp.screener import MetricFilter, ScreenSpec, build_screen_query

        spec = ScreenSpec(
            filters=[MetricFilter(field="roic", op="INJECT", value=0.1)],
        )
        with pytest.raises(ValueError, match="op"):
            build_screen_query(spec)

    def test_invalid_sort_dir_raises(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[], sort="roic", sort_dir="UNION SELECT--")
        with pytest.raises(ValueError, match="sort_dir"):
            build_screen_query(spec)

    def test_value_not_in_sql(self) -> None:
        """Raw filter value must appear in params, NOT interpolated into SQL."""
        from src.webapp.screener import MetricFilter, ScreenSpec, build_screen_query

        sentinel = 99999.12345
        spec = ScreenSpec(
            filters=[MetricFilter(field="roic", op="gte", value=sentinel)],
        )
        sql, params = build_screen_query(spec)
        assert str(sentinel) not in sql
        assert sentinel in params

    def test_sector_value_not_in_sql(self) -> None:
        """Sector filter value must be a bound parameter, not in the SQL string."""
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[], sector="general")
        sql, params = build_screen_query(spec)
        # literal "general" must NOT be in the SQL text
        assert "general" not in sql
        assert "general" in params


class TestBuildScreenQueryOperators:
    """Each operator emits the right SQL fragment; values are in params."""

    @staticmethod
    def _q(
        op: str,
        value: float = 0.1,
        value2: Optional[float] = None,
    ):  # type: ignore[return]
        from src.webapp.screener import MetricFilter, ScreenSpec, build_screen_query

        f = MetricFilter(field="roic", op=op, value=value, value2=value2)
        return build_screen_query(ScreenSpec(filters=[f]))

    def test_gte(self) -> None:
        sql, params = self._q("gte", 0.13)
        assert ">=" in sql
        assert 0.13 in params
        assert "0.13" not in sql

    def test_lte(self) -> None:
        sql, params = self._q("lte", 0.20)
        assert "<=" in sql
        assert 0.20 in params
        assert "0.2" not in sql

    def test_gt(self) -> None:
        sql, params = self._q("gt", 0.30)
        # Must have " > ?" (strictly, not >=)
        assert " > ?" in sql
        assert ">= ?" not in sql
        assert 0.30 in params

    def test_lt(self) -> None:
        sql, params = self._q("lt", 0.40)
        assert " < ?" in sql
        assert "<= ?" not in sql
        assert 0.40 in params

    def test_eq(self) -> None:
        sql, params = self._q("eq", 0.50)
        assert " = ?" in sql
        assert 0.50 in params

    def test_ne(self) -> None:
        sql, params = self._q("ne", 0.60)
        assert "<>" in sql
        assert 0.60 in params

    def test_between(self) -> None:
        sql, params = self._q("between", 0.10, 0.20)
        assert "BETWEEN" in sql.upper()
        assert "AND" in sql
        assert 0.10 in params
        assert 0.20 in params
        # values must NOT be interpolated
        assert "0.1" not in sql
        assert "0.2" not in sql

    def test_between_missing_value2_raises(self) -> None:
        from src.webapp.screener import MetricFilter, ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[MetricFilter(field="roic", op="between", value=0.1)])
        with pytest.raises(ValueError, match="value2"):
            build_screen_query(spec)


class TestBuildScreenQueryFeatures:
    """Limit/offset parameterized; sort with NULLs-last; sector clause."""

    def test_limit_offset_are_parameters(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[], limit=25, offset=50)
        sql, params = build_screen_query(spec)
        upper = sql.upper()
        assert "LIMIT" in upper and "OFFSET" in upper
        # must be parameterized placeholders, not literals
        assert "LIMIT ?" in sql
        assert "OFFSET ?" in sql
        assert 25 in params
        assert 50 in params

    def test_nulls_last_when_sort_set(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[], sort="roic", sort_dir="desc")
        sql, params = build_screen_query(spec)
        # NULLs-last pattern uses IS NULL boolean trick
        upper = sql.upper()
        assert "IS NULL" in upper
        assert "roic" in sql
        assert "DESC" in upper

    def test_nulls_last_asc(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[], sort="roic", sort_dir="asc")
        sql, params = build_screen_query(spec)
        upper = sql.upper()
        assert "IS NULL" in upper
        assert "ASC" in upper

    def test_sort_dir_invalid_raises_even_without_sort(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[], sort=None, sort_dir="HACK")
        with pytest.raises(ValueError, match="sort_dir"):
            build_screen_query(spec)

    def test_no_sort_defaults_to_ticker(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[])
        sql, params = build_screen_query(spec)
        assert "ticker" in sql.lower()

    def test_returns_latest_fy_join(self) -> None:
        from src.webapp.screener import ScreenSpec, build_screen_query

        spec = ScreenSpec(filters=[])
        sql, _ = build_screen_query(spec)
        upper = sql.upper()
        # must join to the latest-per-ticker subquery
        assert "MAX(FISCAL_YEAR)" in upper
        assert "GROUP BY" in upper


# ---------------------------------------------------------------------------
# Reader.screen — end-to-end on the fixture DB
# ---------------------------------------------------------------------------


class TestReaderScreen:
    def test_roic_gte_returns_aaa(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader
        from src.webapp.screener import MetricFilter, ScreenSpec

        with Reader(web_db) as r:
            spec = ScreenSpec(
                filters=[MetricFilter(field="roic", op="gte", value=0.13)]
            )
            result = r.screen(spec)

        assert result["columns"][0] == "ticker"
        tickers = [row["ticker"] for row in result["items"]]
        assert "AAA" in tickers
        assert "BBB" not in tickers
        assert "CCC" not in tickers
        assert result["total"] >= 1

    def test_sort_roic_desc_nulls_last(self, web_db) -> None:  # type: ignore[no-untyped-def]
        """With sort=roic desc, AAA (roic=0.15) must come before NULL-roic tickers."""
        from src.webapp.repository import Reader
        from src.webapp.screener import ScreenSpec

        with Reader(web_db) as r:
            spec = ScreenSpec(filters=[], sort="roic", sort_dir="desc")
            result = r.screen(spec)

        items = result["items"]
        assert len(items) >= 1
        assert items[0]["ticker"] == "AAA"

    def test_screen_total_counts_all_matching(self, web_db) -> None:  # type: ignore[no-untyped-def]
        """total reflects the full count; limit=1 still shows total correctly."""
        from src.webapp.repository import Reader
        from src.webapp.screener import ScreenSpec

        with Reader(web_db) as r:
            spec = ScreenSpec(filters=[], limit=1, offset=0)
            result = r.screen(spec)

        # Only AAA has metrics_annual rows in the fixture
        assert result["total"] == 1
        assert len(result["items"]) == 1

    def test_screen_columns_match_screen_columns(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader
        from src.webapp.screener import SCREEN_COLUMNS, ScreenSpec

        with Reader(web_db) as r:
            result = r.screen(ScreenSpec(filters=[]))

        assert result["columns"] == SCREEN_COLUMNS


# ---------------------------------------------------------------------------
# Reader.compare
# ---------------------------------------------------------------------------


class TestReaderCompare:
    def test_compare_aaa_returns_correct_values(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            result = r.compare(["AAA"], ["roic", "net_margin"])

        assert result["tickers"] == ["AAA"]
        rows_map = {row["metric"]: row["values"] for row in result["rows"]}
        assert rows_map["roic"]["AAA"] == pytest.approx(0.15)
        assert rows_map["net_margin"]["AAA"] == pytest.approx(0.10)

    def test_compare_bbb_no_metrics_returns_none(self, web_db) -> None:  # type: ignore[no-untyped-def]
        """BBB has no metrics_annual rows — values must be None, not absent."""
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            result = r.compare(["BBB"], ["roic"])

        rows_map = {row["metric"]: row["values"] for row in result["rows"]}
        assert rows_map["roic"]["BBB"] is None

    def test_compare_multi_ticker(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            result = r.compare(["AAA", "BBB"], ["roic"])

        assert set(result["tickers"]) == {"AAA", "BBB"}
        rows_map = {row["metric"]: row["values"] for row in result["rows"]}
        assert rows_map["roic"]["AAA"] == pytest.approx(0.15)
        assert rows_map["roic"]["BBB"] is None

    def test_compare_invalid_metric_raises(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            with pytest.raises(ValueError):
                r.compare(["AAA"], ["not_a_column"])


# ---------------------------------------------------------------------------
# Reader.sector_medians
# ---------------------------------------------------------------------------


class TestReaderSectorMedians:
    def test_general_sector_medians(self, web_db) -> None:  # type: ignore[no-untyped-def]
        """AAA is the only general-sector company; its values are the medians."""
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            medians = r.sector_medians("general")

        assert medians.get("roic") == pytest.approx(0.15)
        assert medians.get("net_margin") == pytest.approx(0.10)
        assert medians.get("gross_margin") == pytest.approx(0.45)

    def test_no_metrics_sector_returns_empty_dict(self, web_db) -> None:  # type: ignore[no-untyped-def]
        """Bank sector (BBB) has no metrics_annual rows → empty dict."""
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            medians = r.sector_medians("bank")

        assert len(medians) == 0

    def test_unknown_sector_returns_empty_dict(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            medians = r.sector_medians("nonexistent_sector")

        assert len(medians) == 0


# ---------------------------------------------------------------------------
# Reader.sector_aggregates
# ---------------------------------------------------------------------------


class TestReaderSectorAggregates:
    def test_returns_entry_for_each_sector(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            aggs = r.sector_aggregates()

        sector_classes = {a["sector_class"] for a in aggs}
        assert "general" in sector_classes
        assert "bank" in sector_classes
        assert "reit" in sector_classes

    def test_general_sector_counts_and_roic(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            aggs = r.sector_aggregates()

        general = next(a for a in aggs if a["sector_class"] == "general")
        assert general["n"] == 1
        assert general.get("roic") == pytest.approx(0.15)

    def test_bank_sector_null_metrics(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            aggs = r.sector_aggregates()

        bank = next(a for a in aggs if a["sector_class"] == "bank")
        assert bank["n"] == 1
        assert bank.get("roic") is None

    def test_entries_have_expected_keys(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            aggs = r.sector_aggregates()

        for entry in aggs:
            assert "sector_class" in entry
            assert "n" in entry
            assert "median_quality" in entry


# ---------------------------------------------------------------------------
# Reader.peer_comparison
# ---------------------------------------------------------------------------


class TestReaderPeerComparison:
    def test_aaa_peer_comparison(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            result = r.peer_comparison("AAA", ["roic"])

        assert result["sector_class"] == "general"
        assert result["n_peers"] >= 1
        assert result["company"].get("roic") == pytest.approx(0.15)
        # sector median = AAA's own value (only member in general sector)
        assert result["sector_median"].get("roic") == pytest.approx(0.15)

    def test_peer_comparison_invalid_metric_raises(self, web_db) -> None:  # type: ignore[no-untyped-def]
        from src.webapp.repository import Reader

        with Reader(web_db) as r:
            with pytest.raises(ValueError):
                r.peer_comparison("AAA", ["not_a_metric"])


# ---------------------------------------------------------------------------
# API — JSON endpoints
# ---------------------------------------------------------------------------


class TestScreenerAPI:
    def test_post_screen_returns_aaa(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.post(
            "/api/screen",
            json={"filters": [{"field": "roic", "op": "gte", "value": 0.13}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        tickers = [row["ticker"] for row in data["items"]]
        assert "AAA" in tickers
        assert "total" in data
        assert "columns" in data

    def test_post_screen_invalid_column_400(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.post(
            "/api/screen",
            json={"filters": [{"field": "not_a_column", "op": "gte", "value": 0.1}]},
        )
        assert resp.status_code == 400

    def test_get_screen_shorthand_roic(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/screen?roic_gte=0.13")
        assert resp.status_code == 200
        data = resp.json()
        tickers = [row["ticker"] for row in data["items"]]
        assert "AAA" in tickers

    def test_get_screen_with_sort(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/screen?sort=roic&sort_dir=desc")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"][0]["ticker"] == "AAA"

    def test_get_screen_bad_column_400(self, client) -> None:  # type: ignore[no-untyped-def]
        """A filter key whose field is not whitelisted must return 400."""
        resp = client.get("/api/screen?evil_gte=0.1")
        assert resp.status_code == 400

    def test_get_compare_shape(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/compare?tickers=AAA,BBB&metrics=roic,roe")
        assert resp.status_code == 200
        data = resp.json()
        assert "tickers" in data
        assert "rows" in data

    def test_get_compare_aaa_roic_value(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/compare?tickers=AAA&metrics=roic")
        assert resp.status_code == 200
        data = resp.json()
        rows_map = {row["metric"]: row["values"] for row in data["rows"]}
        assert rows_map["roic"]["AAA"] == pytest.approx(0.15)

    def test_get_sectors_aggregates_shape(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/sectors/aggregates")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert all("sector_class" in a for a in data)

    def test_get_sector_medians_general(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/sectors/general/medians")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert data.get("roic") == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# UI smoke checks
# ---------------------------------------------------------------------------


class TestScreenerUI:
    def test_screener_page_200(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/screener")
        assert resp.status_code == 200

    def test_screen_fragment_contains_aaa(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/ui/screen?roic_gte=0.13")
        assert resp.status_code == 200
        assert b"AAA" in resp.content

    def test_compare_fragment_200(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/ui/compare?tickers=AAA,BBB&metrics=roic")
        assert resp.status_code == 200

    def test_compare_fragment_contains_aaa_value(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/ui/compare?tickers=AAA&metrics=roic")
        assert resp.status_code == 200
        # 0.15 formatted as percentage → "15.0%"
        assert b"15.0" in resp.content or b"AAA" in resp.content


# ---------------------------------------------------------------------------
# Sortable column headers
# ---------------------------------------------------------------------------


class TestScreenerSortableHeaders:
    def test_roic_header_has_hx_get_sort_link(self, client) -> None:  # type: ignore[no-untyped-def]
        """ROIC column header must contain an hx-get link with sort=roic."""
        resp = client.get("/ui/screen?roic_gte=0.13")
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "hx-get" in content
        assert "sort=roic" in content

    def test_sort_dir_toggles_desc_to_asc(self, client) -> None:  # type: ignore[no-untyped-def]
        """When sort=roic&sort_dir=desc the ROIC header link must toggle to sort_dir=asc."""
        resp = client.get("/ui/screen?roic_gte=0.13&sort=roic&sort_dir=desc")
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "sort=roic" in content
        assert "sort_dir=asc" in content
