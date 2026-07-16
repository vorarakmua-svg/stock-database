"""Tests for WebSettings.from_env() and end-to-end settings wiring."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.webapp import create_app
from src.webapp.settings import WebSettings

# ---------------------------------------------------------------------------
# from_env() unit tests
# ---------------------------------------------------------------------------

class TestWebSettingsFromEnv:
    def test_default_allow_collection_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset STOCK_WEB_ALLOW_COLLECTION → allow_collection is True."""
        monkeypatch.delenv("STOCK_WEB_ALLOW_COLLECTION", raising=False)
        settings = WebSettings.from_env()
        assert settings.allow_collection is True

    def test_allow_collection_truthy_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_ALLOW_COLLECTION=1 → allow_collection is True."""
        monkeypatch.setenv("STOCK_WEB_ALLOW_COLLECTION", "1")
        settings = WebSettings.from_env()
        assert settings.allow_collection is True

    def test_allow_collection_truthy_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_ALLOW_COLLECTION=true (case-insensitive) → True."""
        monkeypatch.setenv("STOCK_WEB_ALLOW_COLLECTION", "True")
        settings = WebSettings.from_env()
        assert settings.allow_collection is True

    def test_allow_collection_truthy_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_ALLOW_COLLECTION=yes → True."""
        monkeypatch.setenv("STOCK_WEB_ALLOW_COLLECTION", "yes")
        settings = WebSettings.from_env()
        assert settings.allow_collection is True

    def test_allow_collection_truthy_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_ALLOW_COLLECTION=on → True."""
        monkeypatch.setenv("STOCK_WEB_ALLOW_COLLECTION", "ON")
        settings = WebSettings.from_env()
        assert settings.allow_collection is True

    def test_allow_collection_falsy_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_ALLOW_COLLECTION=0 → False."""
        monkeypatch.setenv("STOCK_WEB_ALLOW_COLLECTION", "0")
        settings = WebSettings.from_env()
        assert settings.allow_collection is False

    def test_allow_collection_defaults_on(self) -> None:
        """WebSettings() with no args → allow_collection is True."""
        assert WebSettings().allow_collection is True

    def test_allow_collection_env_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_ALLOW_COLLECTION=0 via from_env → False."""
        monkeypatch.setenv("STOCK_WEB_ALLOW_COLLECTION", "0")
        assert WebSettings.from_env().allow_collection is False

    def test_allow_collection_env_unset_means_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset STOCK_WEB_ALLOW_COLLECTION → allow_collection is True."""
        monkeypatch.delenv("STOCK_WEB_ALLOW_COLLECTION", raising=False)
        assert WebSettings.from_env().allow_collection is True

    def test_port_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_PORT=9001 → port == 9001."""
        monkeypatch.setenv("STOCK_WEB_PORT", "9001")
        monkeypatch.delenv("STOCK_WEB_ALLOW_COLLECTION", raising=False)
        settings = WebSettings.from_env()
        assert settings.port == 9001

    def test_combined_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_ALLOW_COLLECTION=1 and STOCK_WEB_PORT=9001 both honoured."""
        monkeypatch.setenv("STOCK_WEB_ALLOW_COLLECTION", "1")
        monkeypatch.setenv("STOCK_WEB_PORT", "9001")
        settings = WebSettings.from_env()
        assert settings.allow_collection is True
        assert settings.port == 9001

    def test_host_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCK_WEB_HOST=0.0.0.0 → host == '0.0.0.0'."""
        monkeypatch.setenv("STOCK_WEB_HOST", "0.0.0.0")
        settings = WebSettings.from_env()
        assert settings.host == "0.0.0.0"

    def test_db_path_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """STOCK_WEB_DB_PATH sets db_path."""
        db = tmp_path / "custom.db"
        monkeypatch.setenv("STOCK_WEB_DB_PATH", str(db))
        settings = WebSettings.from_env()
        assert settings.db_path == db


# ---------------------------------------------------------------------------
# End-to-end: settings are actually honoured inside the app
# ---------------------------------------------------------------------------

class TestSettingsHonouredEndToEnd:
    def test_collection_enabled_flips_409_gate(self, web_db: Path) -> None:
        """create_app(settings=WebSettings(..., allow_collection=True)) must NOT 409.

        The 409 gate only fires when allow_collection is False.  With it True,
        the response should be 202 (accepted) or 400 (bad request — empty
        tickers), but NOT 409.
        """
        settings = WebSettings(db_path=web_db, allow_collection=True)
        app = create_app(settings=settings)
        client = TestClient(app)
        # Empty tickers → 400 (validation), not 409 (disabled)
        r = client.post("/api/collection/jobs", json={"tickers": []})
        assert r.status_code != 409, "409 returned despite allow_collection=True"
        assert r.status_code == 400

    def test_collection_disabled_returns_409(self, web_db: Path) -> None:
        """create_app with allow_collection=False → POST jobs returns 409."""
        settings = WebSettings(db_path=web_db, allow_collection=False)
        app = create_app(settings=settings)
        client = TestClient(app)
        r = client.post("/api/collection/jobs", json={"tickers": ["AAPL"]})
        assert r.status_code == 409
