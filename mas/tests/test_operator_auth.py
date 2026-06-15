"""Regression tests for narrow operator-auth control-plane enforcement."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from config import OPERATOR_AUTH_HEADER  # noqa: E402


PROTECTED_PATH = "/projects/operator-auth-probe/run"


def _clear_operator_auth_env(monkeypatch):
    monkeypatch.delenv("MAS_REQUIRE_OPERATOR_AUTH", raising=False)
    monkeypatch.delenv("MAS_OPERATOR_API_KEY", raising=False)


def _post_protected(headers: dict[str, str] | None = None):
    client = TestClient(api.app)
    try:
        return client.post(PROTECTED_PATH, headers=headers or {})
    finally:
        client.close()


def test_local_default_allows_protected_endpoint_without_operator_key(monkeypatch):
    _clear_operator_auth_env(monkeypatch)
    load = AsyncMock(return_value=None)

    with patch("api.store.load", new=load):
        response = _post_protected()

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"
    load.assert_awaited_once_with("operator-auth-probe")


def test_required_operator_auth_rejects_missing_and_invalid_keys(monkeypatch):
    monkeypatch.setenv("MAS_REQUIRE_OPERATOR_AUTH", "true")
    monkeypatch.setenv("MAS_OPERATOR_API_KEY", "valid-operator-key")
    load = AsyncMock(return_value=None)

    with patch("api.store.load", new=load):
        missing = _post_protected()
        invalid = _post_protected(headers={OPERATOR_AUTH_HEADER: "wrong-operator-key"})

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json()["detail"] == "Operator authentication required"
    assert invalid.json()["detail"] == "Operator authentication required"
    assert "valid-operator-key" not in missing.text
    assert "valid-operator-key" not in invalid.text
    assert "wrong-operator-key" not in invalid.text
    load.assert_not_awaited()


def test_required_operator_auth_accepts_valid_key(monkeypatch):
    monkeypatch.setenv("MAS_REQUIRE_OPERATOR_AUTH", "true")
    monkeypatch.setenv("MAS_OPERATOR_API_KEY", "valid-operator-key")
    load = AsyncMock(return_value=None)

    with patch("api.store.load", new=load):
        response = _post_protected(headers={OPERATOR_AUTH_HEADER: "valid-operator-key"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"
    assert "valid-operator-key" not in response.text
    load.assert_awaited_once_with("operator-auth-probe")


def test_required_operator_auth_without_configured_key_rejects_protected_endpoint(monkeypatch):
    monkeypatch.setenv("MAS_REQUIRE_OPERATOR_AUTH", "true")
    monkeypatch.delenv("MAS_OPERATOR_API_KEY", raising=False)
    load = AsyncMock(return_value=None)

    with patch("api.store.load", new=load):
        response = _post_protected(headers={OPERATOR_AUTH_HEADER: "anything"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Operator auth is required but not configured"
    assert "anything" not in response.text
    load.assert_not_awaited()


def test_health_remains_open_when_operator_auth_is_required(monkeypatch):
    monkeypatch.setenv("MAS_REQUIRE_OPERATOR_AUTH", "true")
    monkeypatch.setenv("MAS_OPERATOR_API_KEY", "valid-operator-key")
    client = TestClient(api.app)

    try:
        with patch("api.store._get_pool", new=AsyncMock(return_value=None)):
            response = client.get("/health")
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["persistence"] == "memory"
    assert "valid-operator-key" not in response.text
