"""Integration test — `sanctum serve` shuts down cleanly on SIGTERM.

The Phase 3 Electron sidecar lifecycle sends SIGTERM on app quit; the
process must exit within a few seconds without leaving a locked
mapping-store flock or a half-written session directory. This test
spawns the real CLI as a subprocess, waits for the SANCTUM_READY line,
sends SIGTERM, and verifies the exit path.

Windows has no real SIGTERM, so this test is gated to POSIX. Windows
behaviour is covered by a different test path (to land when WS3's
signal-shim lands).

Marked `integration` so `pytest -m "not integration"` skips it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM"),
]

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_ready_line(line: str) -> dict[str, str]:
    """Split `SANCTUM_READY k=v k=v k=v` into a dict."""
    assert line.startswith("SANCTUM_READY"), line
    parts = line.removeprefix("SANCTUM_READY").strip().split()
    out: dict[str, str] = {}
    for part in parts:
        k, _, v = part.partition("=")
        out[k] = v
    return out


def _wait_for_ready(proc: subprocess.Popen[str], timeout: float = 30.0) -> dict[str, str]:
    """Block until the subprocess emits the ready line on stdout."""
    assert proc.stdout is not None
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            # EOF — process died before emitting ready.
            rc = proc.poll()
            raise RuntimeError(f"sanctum serve exited before ready (rc={rc})")
        if line.startswith("SANCTUM_READY"):
            return _parse_ready_line(line.strip())
    raise RuntimeError("sanctum serve never emitted SANCTUM_READY")


def _wait_for_exit(proc: subprocess.Popen[str], timeout: float = 10.0) -> int:
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise


@pytest.fixture()
def serve_env(tmp_path: Path) -> dict[str, str]:
    """Isolated env: SANCTUM_COMMIT sentinel, HOME under tmp so the CLI
    doesn't try to write to the developer's real ~/.sanctum."""
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["SANCTUM_COMMIT"] = "sigterm-test"
    return env


def test_sanctum_serve_exits_cleanly_on_sigterm(serve_env: dict[str, str], tmp_path: Path) -> None:
    """Happy path: spawn serve, wait for ready, SIGTERM, assert the
    process exits within 10s without needing SIGKILL."""
    token = "sigterm-test-token-x"
    proc = subprocess.Popen(
        ["sanctum", "serve", "--port", "0", "--token-stdin"],
        cwd=str(_REPO_ROOT),
        env=serve_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(token + "\n")
    proc.stdin.flush()
    proc.stdin.close()

    try:
        ready = _wait_for_ready(proc)
        # Confirm the server actually accepts a real request.
        port = int(ready["port"])
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health",
            headers={"Host": f"127.0.0.1:{port}"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as r:
            assert r.status == 200

        # SIGTERM should trigger the graceful shutdown path.
        proc.send_signal(signal.SIGTERM)
        rc = _wait_for_exit(proc, timeout=10.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)

    # Some Python / waitress combinations surface SIGTERM as exit 0 after
    # the KeyboardInterrupt is caught; others as -SIGTERM. Either is a
    # "clean, signal-triggered" exit — we only fail on an uncaught
    # exception path (positive rc other than 0).
    assert rc in (0, -signal.SIGTERM, 130), f"unexpected serve exit code {rc}"


def test_sanctum_serve_stops_accepting_after_sigterm(
    serve_env: dict[str, str], tmp_path: Path
) -> None:
    """After SIGTERM + exit, the port must be free. If waitress had
    leaked a bound socket, a follow-up connect would still succeed."""
    token = "sigterm-test-token-y"
    proc = subprocess.Popen(
        ["sanctum", "serve", "--port", "0", "--token-stdin"],
        cwd=str(_REPO_ROOT),
        env=serve_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(token + "\n")
    proc.stdin.flush()
    proc.stdin.close()

    try:
        ready = _wait_for_ready(proc)
        port = int(ready["port"])
        proc.send_signal(signal.SIGTERM)
        _wait_for_exit(proc, timeout=10.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)

    # After exit, connection attempts should fail (connection refused).
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0)
