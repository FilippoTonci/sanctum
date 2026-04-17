"""Unit tests for `/health` — shape, version, mapping-store flag, no-auth, host guard."""

from __future__ import annotations

from sanctum import __version__
from sanctum.api.app import create_app
from sanctum.api.schemas import HealthResponse


def _client():
    app = create_app(token="t", host="127.0.0.1", port=8765)
    return app, app.test_client()


def test_health_returns_200_and_expected_payload():
    _, client = _client()
    r = client.get("/health", headers={"Host": "127.0.0.1:8765"})
    assert r.status_code == 200
    body = r.get_json()
    assert body == {
        "status": "ok",
        "version": __version__,
        "mapping_store_unlocked": False,
    }


def test_health_payload_validates_against_schema():
    _, client = _client()
    r = client.get("/health", headers={"Host": "127.0.0.1:8765"})
    # Round-trip through the Pydantic model — any drift in the wire shape blows up here.
    HealthResponse.model_validate(r.get_json())


def test_health_does_not_require_bearer_token():
    """No `Authorization` header — must still succeed (liveness is unauthenticated)."""
    _, client = _client()
    r = client.get("/health", headers={"Host": "127.0.0.1:8765"})
    assert r.status_code == 200


def test_health_still_subject_to_host_guard():
    _, client = _client()
    r = client.get("/health", headers={"Host": "attacker.example:8765"})
    assert r.status_code == 403


def test_health_reports_mapping_store_unlocked_when_configured():
    """Until WS4.7 wires real lock state, presence of the engine config key flips the flag."""
    app = create_app(token="t", host="127.0.0.1", port=8765)
    app.config["SANCTUM_MAPPING_STORE"] = object()  # stand-in for an unlocked store
    client = app.test_client()
    r = client.get("/health", headers={"Host": "127.0.0.1:8765"})
    assert r.status_code == 200
    assert r.get_json()["mapping_store_unlocked"] is True
