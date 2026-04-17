"""Unit tests for the Flask app factory — config wiring + global host guard."""

from __future__ import annotations

from sanctum.api.app import _build_allowed_hosts, create_app


def test_allowed_hosts_covers_bind_and_localhost_alias():
    allowed = _build_allowed_hosts("127.0.0.1", 8765)
    assert "127.0.0.1:8765" in allowed
    assert "localhost:8765" in allowed


def test_create_app_populates_config():
    app = create_app(token="t", host="127.0.0.1", port=8765)
    assert app.config["SANCTUM_API_TOKEN"] == "t"
    assert app.config["SANCTUM_ALLOWED_HOSTS"] == {"127.0.0.1:8765", "localhost:8765"}
    assert app.config["SANCTUM_ENGINE"] is None


def test_before_request_guard_rejects_spoofed_host():
    """Even without any registered route, the host guard runs and returns 403."""
    app = create_app(token="t", host="127.0.0.1", port=8765)
    client = app.test_client()
    r = client.get("/any-path", headers={"Host": "attacker.example:8765"})
    assert r.status_code == 403


def test_before_request_guard_rejects_non_local_origin():
    app = create_app(token="t", host="127.0.0.1", port=8765)

    @app.route("/ping")
    def ping() -> tuple[dict, int]:
        return {"ok": True}, 200

    client = app.test_client()
    r = client.get(
        "/ping",
        headers={"Host": "127.0.0.1:8765", "Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403


def test_before_request_guard_allows_loopback():
    app = create_app(token="t", host="127.0.0.1", port=8765)

    @app.route("/ping")
    def ping() -> tuple[dict, int]:
        return {"ok": True}, 200

    client = app.test_client()
    r = client.get("/ping", headers={"Host": "127.0.0.1:8765"})
    assert r.status_code == 200
