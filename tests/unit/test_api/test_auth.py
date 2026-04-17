"""Unit tests for `sanctum.api.auth` — token IO + decorator enforcement."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from flask import Flask
from sanctum.api.auth import (
    InsecureTokenFileError,
    ensure_token,
    generate_token,
    read_token,
    require_bearer_token,
    require_local_host,
    write_token,
)

# --- token IO -------------------------------------------------------------


def test_generate_token_is_high_entropy_and_urlsafe():
    t = generate_token()
    # 32 random bytes base64url-encoded → 43 chars without padding.
    assert len(t) >= 40
    assert all(c.isalnum() or c in "-_" for c in t)
    # Two successive calls must not collide (probabilistically impossible).
    assert generate_token() != t


def test_write_token_creates_file_with_0600_perms(tmp_path: Path):
    path = tmp_path / "nested" / "api-token"
    write_token("s3cret", path)
    assert path.read_text().strip() == "s3cret"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0600 perms, got {oct(mode)}"


def test_write_token_is_atomic_on_overwrite(tmp_path: Path):
    path = tmp_path / "api-token"
    write_token("first", path)
    write_token("second", path)
    assert path.read_text().strip() == "second"
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_read_token_returns_none_when_absent(tmp_path: Path):
    assert read_token(tmp_path / "nope") is None


def test_read_token_strips_trailing_newline(tmp_path: Path):
    path = tmp_path / "api-token"
    # write_token handles the trailing newline and applies 0600; going
    # through it keeps this test decoupled from the on-disk format while
    # satisfying the loose-perms refusal in read_token.
    write_token("raw-token", path)
    assert read_token(path) == "raw-token"


def test_ensure_token_generates_once_and_reuses(tmp_path: Path):
    path = tmp_path / "api-token"
    first = ensure_token(path)
    second = ensure_token(path)
    assert first == second
    assert path.exists()


def test_ensure_token_reads_existing_file(tmp_path: Path):
    path = tmp_path / "api-token"
    write_token("pre-existing", path)
    assert ensure_token(path) == "pre-existing"


# --- decorators -----------------------------------------------------------


@pytest.fixture()
def app() -> Flask:
    app = Flask(__name__)
    app.config["SANCTUM_API_TOKEN"] = "the-server-token"
    app.config["SANCTUM_ALLOWED_HOSTS"] = {"127.0.0.1:8765", "localhost:8765"}

    @app.route("/ping-host")
    @require_local_host
    def ping_host() -> tuple[dict, int]:
        return {"ok": True}, 200

    @app.route("/ping-auth")
    @require_bearer_token
    def ping_auth() -> tuple[dict, int]:
        return {"ok": True}, 200

    @app.route("/ping-both")
    @require_local_host
    @require_bearer_token
    def ping_both() -> tuple[dict, int]:
        return {"ok": True}, 200

    return app


def test_host_allowlist_accepts_loopback(app: Flask):
    client = app.test_client()
    r = client.get("/ping-host", headers={"Host": "127.0.0.1:8765"})
    assert r.status_code == 200
    r = client.get("/ping-host", headers={"Host": "localhost:8765"})
    assert r.status_code == 200


def test_host_allowlist_blocks_dns_rebinding_attempt(app: Flask):
    """A browser tricked into resolving attacker.example → 127.0.0.1 still
    sends the attacker hostname in the Host header."""
    client = app.test_client()
    r = client.get("/ping-host", headers={"Host": "attacker.example:8765"})
    assert r.status_code == 403


def test_host_allowlist_blocks_wrong_port(app: Flask):
    client = app.test_client()
    r = client.get("/ping-host", headers={"Host": "127.0.0.1:9999"})
    assert r.status_code == 403


def test_origin_header_when_local_is_accepted(app: Flask):
    client = app.test_client()
    r = client.get(
        "/ping-host",
        headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"},
    )
    assert r.status_code == 200


def test_origin_header_when_non_local_is_rejected(app: Flask):
    client = app.test_client()
    r = client.get(
        "/ping-host",
        headers={"Host": "127.0.0.1:8765", "Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403


def test_bearer_token_happy_path(app: Flask):
    client = app.test_client()
    r = client.get(
        "/ping-auth",
        headers={"Authorization": "Bearer the-server-token"},
    )
    assert r.status_code == 200


def test_bearer_token_missing_header(app: Flask):
    client = app.test_client()
    r = client.get("/ping-auth")
    assert r.status_code == 401


def test_bearer_token_wrong_value(app: Flask):
    client = app.test_client()
    r = client.get(
        "/ping-auth",
        headers={"Authorization": "Bearer something-else"},
    )
    assert r.status_code == 401


def test_bearer_token_wrong_scheme(app: Flask):
    client = app.test_client()
    r = client.get(
        "/ping-auth",
        headers={"Authorization": "Basic the-server-token"},
    )
    assert r.status_code == 401


def test_bearer_token_server_misconfig_returns_500(app: Flask):
    app.config["SANCTUM_API_TOKEN"] = None
    client = app.test_client()
    r = client.get(
        "/ping-auth",
        headers={"Authorization": "Bearer anything"},
    )
    assert r.status_code == 500


def test_both_decorators_enforced_together(app: Flask):
    client = app.test_client()
    r = client.get(
        "/ping-both",
        headers={
            "Host": "127.0.0.1:8765",
            "Authorization": "Bearer the-server-token",
        },
    )
    assert r.status_code == 200

    # Bad host, good token → 403 (host check runs first).
    r = client.get(
        "/ping-both",
        headers={
            "Host": "evil.example:8765",
            "Authorization": "Bearer the-server-token",
        },
    )
    assert r.status_code == 403

    # Good host, bad token → 401.
    r = client.get(
        "/ping-both",
        headers={"Host": "127.0.0.1:8765"},
    )
    assert r.status_code == 401


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_token_file_not_world_readable(tmp_path: Path):
    """The 0600 perms must not leak to group/other even through umask."""
    # Force a permissive umask; O_CREAT mode should still clamp to 0600.
    old_umask = os.umask(0o000)
    try:
        path = tmp_path / "api-token"
        write_token("leaky", path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600
    finally:
        os.umask(old_umask)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_write_token_tightens_preexisting_parent_dir_to_0700(tmp_path: Path):
    """If ``~/.sanctum/`` pre-exists with looser perms (umask, old install),
    the next write must re-tighten it — the dir's perms are part of the
    token's authz boundary."""
    parent = tmp_path / "sanctum_dir"
    parent.mkdir(mode=0o755)
    # Ensure the looser perms actually stuck (some filesystems ignore mode).
    os.chmod(parent, 0o755)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755

    write_token("tok", parent / "api-token")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_read_token_refuses_loose_perms(tmp_path: Path):
    """A token file that has drifted to 0644 must not be trusted — an
    attacker with the same UID as the user might have planted or altered
    it. Raise so the operator fixes it instead of silently continuing."""
    path = tmp_path / "api-token"
    write_token("t", path)
    os.chmod(path, 0o644)

    with pytest.raises(InsecureTokenFileError):
        read_token(path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_ensure_token_surfaces_loose_perms(tmp_path: Path):
    """`ensure_token` must propagate the error rather than silently
    rotating — a quiet rotation would leave the old loose-perm file
    sitting on disk."""
    path = tmp_path / "api-token"
    write_token("t", path)
    os.chmod(path, 0o644)

    with pytest.raises(InsecureTokenFileError):
        ensure_token(path)
