"""Tests for the FastAPI web app health endpoint — Task 1 scaffold."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.webapp import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db_path" in data
    assert "db_exists" in data


def test_health_db_exists_is_bool(client: TestClient) -> None:
    data = client.get("/api/health").json()
    assert isinstance(data["db_exists"], bool)


def test_openapi_json(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "openapi" in schema
