"""End-to-end integration tests for the Sanctum API.

Boots a real waitress server in a daemon thread on a random loopback
port, hits it over a real socket, and shuts it down cleanly between
runs. The engine is real (Presidio + spaCy) so this test catches wiring
mistakes the unit suite would miss — auth headers reaching real
middleware, JSON serialization round-trips, blueprint registration order,
the Pydantic-via-Werkzeug path.

Marked `integration` so it stays out of `pytest -m "not integration"`.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sanctum.analyzer.adapter import PresidioAnalyzer
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.api.app import create_app
from sanctum.api.server import assert_loopback
from sanctum.core.engine import SanctumEngine
from sanctum.security.mapping_store import EncryptedFileMappingStore
from waitress.server import create_server  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

_TOKEN = "integration-token-do-not-reuse"


def _free_port() -> int:
    """Ask the OS for a free loopback port. Tiny race-window vs. waitress bind."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _cheap_factory(path: Path) -> EncryptedFileMappingStore:
    """KDF tuned for test speed; same params as the unit suite."""
    return EncryptedFileMappingStore(
        path,
        kdf_time_cost=1,
        kdf_memory_cost=8,
        kdf_parallelism=1,
    )


def _wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    """Poll /health until it answers 200 — proves waitress accepted connections."""
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{base_url}/health",
                headers={"Host": _host_header(base_url)},
            )
            with urllib.request.urlopen(req, timeout=1.0) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError) as exc:
            last_exc = exc
        time.sleep(0.05)
    raise RuntimeError(f"server never came up: {last_exc!r}")


def _host_header(base_url: str) -> str:
    """Strip ``http://`` so the value is suitable for a Host header."""
    return base_url.removeprefix("http://").removeprefix("https://")


@pytest.fixture(scope="module")
def server() -> Iterator[tuple[str, str]]:
    """Boot a real waitress server in a daemon thread; yield (base_url, token)."""
    port = _free_port()
    engine = SanctumEngine(
        analyzer=PresidioAnalyzer(),
        anonymizer=PresidioAnonymizer(),
    )
    app = create_app(
        token=_TOKEN,
        host="127.0.0.1",
        port=port,
        engine=engine,
        mapping_store_factory=_cheap_factory,
    )
    wsgi_server = create_server(app, host="127.0.0.1", port=port, threads=2)
    thread = threading.Thread(target=wsgi_server.run, name="sanctum-test-waitress", daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url)
        yield base_url, _TOKEN
    finally:
        wsgi_server.close()
        thread.join(timeout=5.0)


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Tiny urllib-based client. Returns ``(status, parsed_json_or_{})``."""
    # urlparse-style: ``http://127.0.0.1:PORT/rest`` → netloc is element 2 of split("/", 3).
    netloc = url.split("/", 3)[2]
    headers: dict[str, str] = {"Host": netloc}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data: bytes | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as r:
            raw = r.read().decode()
            payload = json.loads(raw) if raw else {}
            return r.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        payload = json.loads(raw) if raw else {}
        return exc.code, payload


# ---------- liveness + auth boundaries ----------


def test_health_round_trip(server: tuple[str, str]) -> None:
    base, token = server
    status, body = _request("GET", f"{base}/health", token=token)
    assert status == 200
    assert body["status"] == "ok"
    assert "version" in body
    assert body["mapping_store_unlocked"] is False


def test_missing_bearer_returns_401(server: tuple[str, str]) -> None:
    base, _ = server
    status, _ = _request("POST", f"{base}/analyze", body={"text": "hi"})
    assert status == 401


def test_spoofed_host_header_returns_403(server: tuple[str, str]) -> None:
    base, token = server
    status, _ = _request(
        "POST",
        f"{base}/analyze",
        token=token,
        body={"text": "hi"},
        extra_headers={"Host": "attacker.example:80"},
    )
    assert status == 403


# ---------- analyze + anonymize over the wire ----------


def test_analyze_finds_real_pii(server: tuple[str, str]) -> None:
    base, token = server
    status, body = _request(
        "POST",
        f"{base}/analyze",
        token=token,
        body={"text": "My name is John Smith and I work at Acme Corp."},
    )
    assert status == 200, body
    types = {d["entity_type"] for d in body["detections"]}
    assert "PERSON" in types
    assert body["count"] == len(body["detections"])


def test_anonymize_replaces_pii(server: tuple[str, str]) -> None:
    base, token = server
    status, body = _request(
        "POST",
        f"{base}/anonymize",
        token=token,
        body={"text": "My name is John Smith.", "operator": "replace"},
    )
    assert status == 200, body
    assert "John Smith" not in body["anonymized_text"]
    assert body["original_text"] == "My name is John Smith."


# ---------- mapping flow + pseudonymize ----------


def test_mapping_unlock_pseudonymize_reverse_lock_cycle(
    server: tuple[str, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    base, token = server
    store_path = tmp_path_factory.mktemp("api_store") / "store.bin"

    # 0. Health flag starts at False.
    _, h0 = _request("GET", f"{base}/health", token=token)
    assert h0["mapping_store_unlocked"] is False

    # 1. Unlock.
    status, body = _request(
        "POST",
        f"{base}/mapping/unlock",
        token=token,
        body={"store_path": str(store_path), "passphrase": "passw0rd"},
    )
    assert status == 200, body
    assert body == {"unlocked": True, "store_path": str(store_path)}

    # 2. Health flag now True.
    _, h1 = _request("GET", f"{base}/health", token=token)
    assert h1["mapping_store_unlocked"] is True

    try:
        # 3. Anonymize with pseudonymize → store learns a mapping. Text is
        # just the PERSON span with no surrounding context so the whole
        # anonymized_text IS the pseudonym — no extraction needed.
        #
        # Earlier versions used "John Smith ate lunch." and peeled common
        # prefix/suffix bytes against the original to recover the middle
        # slice. That was deterministically broken: whenever Faker drew a
        # pseudonym starting with the same letter as the original (e.g.
        # "Justin Hamilton" for "John Smith"), the prefix walk consumed
        # the shared 'J' and the extracted key was "ustin Hamilton" —
        # /mapping/reverse 404'd, and CI flaked ~10-20% of the time.
        # Trimming the surrounding text removes the entire failure mode.
        status, anon = _request(
            "POST",
            f"{base}/anonymize",
            token=token,
            body={"text": "John Smith", "operator": "pseudonymize"},
        )
        assert status == 200, anon
        assert anon["anonymized_text"] != anon["original_text"]
        person_dets = [d for d in anon["detections"] if d["entity_type"] == "PERSON"]
        assert person_dets, anon

        # 4. Reverse: feed the pseudonym back, get the original PERSON span.
        pseudonym = anon["anonymized_text"]
        assert pseudonym, f"no pseudonym in {anon!r}"

        status, rev = _request(
            "POST",
            f"{base}/mapping/reverse",
            token=token,
            body={"pseudonym": pseudonym, "entity_type": "PERSON"},
        )
        assert status == 200, rev
        assert rev["original"] == "John Smith"
    finally:
        # 5. Lock — even if the assertions blew up, leave the store locked
        # so the persisted file is well-formed for the next test.
        status, body = _request("POST", f"{base}/mapping/lock", token=token)
        assert status == 200
        assert body["unlocked"] is False

    # 6. Health flag back to False.
    _, h2 = _request("GET", f"{base}/health", token=token)
    assert h2["mapping_store_unlocked"] is False


def test_mapping_rotate_key_changes_passphrase(
    server: tuple[str, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    base, token = server
    store_path = tmp_path_factory.mktemp("rot_store") / "store.bin"

    # Seed the store via unlock + lock so a real file exists.
    _request(
        "POST",
        f"{base}/mapping/unlock",
        token=token,
        body={"store_path": str(store_path), "passphrase": "old-pass"},
    )
    _request("POST", f"{base}/mapping/lock", token=token)

    # Rotate.
    status, body = _request(
        "POST",
        f"{base}/mapping/rotate-key",
        token=token,
        body={
            "store_path": str(store_path),
            "old_passphrase": "old-pass",
            "new_passphrase": "new-pass",
        },
    )
    assert status == 200, body

    # Old passphrase fails; new succeeds.
    bad, _ = _request(
        "POST",
        f"{base}/mapping/unlock",
        token=token,
        body={"store_path": str(store_path), "passphrase": "old-pass"},
    )
    assert bad == 401
    good, _ = _request(
        "POST",
        f"{base}/mapping/unlock",
        token=token,
        body={"store_path": str(store_path), "passphrase": "new-pass"},
    )
    assert good == 200
    _request("POST", f"{base}/mapping/lock", token=token)


# ---------- /process-file over the wire ----------


def test_process_file_round_trip(
    server: tuple[str, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    base, token = server
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "office" / "nda_contract.docx"
    assert fixture.is_file(), "expected docx fixture to exist for the integration test"

    out_dir = tmp_path_factory.mktemp("api_proc")
    out_path = out_dir / "out.docx"

    status, body = _request(
        "POST",
        f"{base}/process-file",
        token=token,
        body={
            "input_path": str(fixture),
            "output_path": str(out_path),
            "operator": "replace",
        },
    )
    assert status == 200, body
    assert body["output_path"] == str(out_path)
    assert out_path.is_file(), "writer should have created the output document"


# ---------- bind-time loopback enforcement (no server boot needed) ----------


def test_assert_loopback_refuses_external_bind() -> None:
    with pytest.raises(ValueError, match="non-loopback"):
        assert_loopback("0.0.0.0")
    with pytest.raises(ValueError, match="non-loopback"):
        assert_loopback("10.0.0.1")


# ---------- airgap-import invariant ----------


def test_request_handling_opens_no_outbound_sockets(
    server: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The airgap promise is only as strong as the code path that serves
    a request. Wrap ``socket.create_connection`` so that any call from a
    non-main (i.e., waitress worker) thread records a violation; the test
    client runs on the main thread and is allowed through untouched.

    Tracking violations in a list rather than raising avoids masking the
    real response with a middleware exception — we want to see the full
    request complete and then assert no server-side outbound attempt.
    """
    real_create_connection = socket.create_connection
    violations: list[str] = []

    def _audit(*args: Any, **kwargs: Any) -> Any:
        if threading.current_thread() is not threading.main_thread():
            violations.append(threading.current_thread().name)
        return real_create_connection(*args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", _audit)

    base, token = server
    status, _ = _request(
        "POST",
        f"{base}/analyze",
        token=token,
        body={"text": "My name is John Smith."},
    )
    assert status == 200

    status, _ = _request(
        "POST",
        f"{base}/anonymize",
        token=token,
        body={"text": "My name is John Smith.", "operator": "replace"},
    )
    assert status == 200

    assert not violations, (
        f"airgap violation: server-side code attempted outbound TCP " f"from {violations!r}"
    )
