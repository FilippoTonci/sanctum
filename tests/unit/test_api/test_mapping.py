"""Unit tests for `/mapping/*` — unlock, lock, reverse, rotate-key + /health flag."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sanctum.api.app import create_app
from sanctum.security.mapping_store import EncryptedFileMappingStore

LOOPBACK = {"Host": "127.0.0.1:8765"}
AUTH = {"Authorization": "Bearer t"}


def _cheap_factory(path: Path) -> EncryptedFileMappingStore:
    """KDF tuned for test speed — same params used by the security suite."""
    return EncryptedFileMappingStore(
        path,
        kdf_time_cost=1,
        kdf_memory_cost=8,
        kdf_parallelism=1,
    )


def _client(engine: Any = None):
    app = create_app(
        token="t",
        host="127.0.0.1",
        port=8765,
        engine=engine,
        mapping_store_factory=_cheap_factory,
    )
    return app, app.test_client()


# ---------- auth ----------


@pytest.mark.parametrize(
    "path",
    ["/mapping/unlock", "/mapping/lock", "/mapping/reverse", "/mapping/rotate-key"],
)
def test_mapping_routes_require_bearer(path: str):
    _, client = _client()
    r = client.post(path, headers=LOOPBACK, json={})
    assert r.status_code == 401


# ---------- /mapping/unlock ----------


def test_unlock_rejects_relative_path(tmp_path: Path):
    _, client = _client()
    r = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": "relative/store.bin", "passphrase": "p"},
    )
    assert r.status_code == 400
    assert "store_path" in r.get_json()["error"]


def test_unlock_rejects_unc_path():
    _, client = _client()
    r = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": "//share/store.bin", "passphrase": "p"},
    )
    assert r.status_code == 400


def test_unlock_rejects_empty_passphrase(tmp_path: Path):
    _, client = _client()
    r = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(tmp_path / "s.bin"), "passphrase": ""},
    )
    assert r.status_code == 400


def test_unlock_creates_fresh_store_on_missing_path(tmp_path: Path):
    """Unlocking a non-existent path is treated as 'create on first lock'."""
    _, client = _client()
    target = tmp_path / "fresh.bin"
    r = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "passw0rd!"},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body == {"unlocked": True, "store_path": str(target)}
    # Health should now flip the flag.
    h = client.get("/health", headers=LOOPBACK)
    assert h.get_json()["mapping_store_unlocked"] is True


def test_unlock_existing_store_with_correct_passphrase(tmp_path: Path):
    target = tmp_path / "existing.bin"
    seed = _cheap_factory(target)
    seed.unlock("right-pass")
    seed.get_or_create("Alice", "PERSON", lambda: "Bob")
    seed.lock()

    _, client = _client()
    r = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "right-pass"},
    )
    assert r.status_code == 200


def test_unlock_existing_store_with_wrong_passphrase_returns_401(tmp_path: Path):
    target = tmp_path / "existing.bin"
    seed = _cheap_factory(target)
    seed.unlock("right-pass")
    seed.lock()

    _, client = _client()
    r = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "wrong-pass"},
    )
    assert r.status_code == 401
    assert "passphrase" in r.get_json()["error"]


def test_unlock_409_when_already_unlocked(tmp_path: Path):
    _, client = _client()
    p = str(tmp_path / "first.bin")
    client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": p, "passphrase": "x"},
    )
    r = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(tmp_path / "second.bin"), "passphrase": "y"},
    )
    assert r.status_code == 409


# ---------- /mapping/lock ----------


def test_lock_when_nothing_unlocked_is_idempotent():
    _, client = _client()
    r = client.post("/mapping/lock", headers={**LOOPBACK, **AUTH})
    assert r.status_code == 200
    assert r.get_json() == {"unlocked": False, "store_path": None}


def test_lock_persists_mappings_and_clears_state(tmp_path: Path):
    _, client = _client()
    target = tmp_path / "store.bin"
    client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "p"},
    )
    # Lock writes to disk + clears the slot.
    r = client.post("/mapping/lock", headers={**LOOPBACK, **AUTH})
    assert r.status_code == 200
    assert r.get_json()["unlocked"] is False
    assert target.exists()  # file persisted

    h = client.get("/health", headers=LOOPBACK)
    assert h.get_json()["mapping_store_unlocked"] is False


# ---------- /mapping/reverse ----------


def test_reverse_409_when_locked():
    _, client = _client()
    r = client.post(
        "/mapping/reverse",
        headers={**LOOPBACK, **AUTH},
        json={"pseudonym": "Bob", "entity_type": "PERSON"},
    )
    assert r.status_code == 409


def test_reverse_404_when_mapping_missing(tmp_path: Path):
    _, client = _client()
    client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(tmp_path / "s.bin"), "passphrase": "p"},
    )
    r = client.post(
        "/mapping/reverse",
        headers={**LOOPBACK, **AUTH},
        json={"pseudonym": "Nobody", "entity_type": "PERSON"},
    )
    assert r.status_code == 404


def test_reverse_returns_original(tmp_path: Path):
    target = tmp_path / "store.bin"
    seed = _cheap_factory(target)
    seed.unlock("p")
    seed.get_or_create("Alice", "PERSON", lambda: "Bob")
    seed.lock()

    _, client = _client()
    client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "p"},
    )
    r = client.post(
        "/mapping/reverse",
        headers={**LOOPBACK, **AUTH},
        json={"pseudonym": "Bob", "entity_type": "PERSON"},
    )
    assert r.status_code == 200
    assert r.get_json() == {
        "pseudonym": "Bob",
        "entity_type": "PERSON",
        "original": "Alice",
    }


def test_reverse_400_on_bad_body(tmp_path: Path):
    _, client = _client()
    client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(tmp_path / "s.bin"), "passphrase": "p"},
    )
    r = client.post(
        "/mapping/reverse",
        headers={**LOOPBACK, **AUTH},
        json={"pseudonym": ""},  # missing entity_type, empty pseudonym
    )
    assert r.status_code == 400


# ---------- /mapping/rotate-key ----------


def test_rotate_key_happy_path(tmp_path: Path):
    target = tmp_path / "store.bin"
    seed = _cheap_factory(target)
    seed.unlock("old-pass")
    seed.get_or_create("Alice", "PERSON", lambda: "Bob")
    seed.lock()

    _, client = _client()
    r = client.post(
        "/mapping/rotate-key",
        headers={**LOOPBACK, **AUTH},
        json={
            "store_path": str(target),
            "old_passphrase": "old-pass",
            "new_passphrase": "new-pass",
        },
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"rotated": True, "store_path": str(target)}

    # Old passphrase must now fail; new must succeed.
    bad = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "old-pass"},
    )
    assert bad.status_code == 401
    good = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "new-pass"},
    )
    assert good.status_code == 200


def test_rotate_key_rejects_same_passphrase(tmp_path: Path):
    target = tmp_path / "store.bin"
    seed = _cheap_factory(target)
    seed.unlock("p")
    seed.lock()
    _, client = _client()
    r = client.post(
        "/mapping/rotate-key",
        headers={**LOOPBACK, **AUTH},
        json={
            "store_path": str(target),
            "old_passphrase": "p",
            "new_passphrase": "p",
        },
    )
    assert r.status_code == 400
    assert "differ" in r.get_json()["error"]


def test_rotate_key_401_on_wrong_old_passphrase(tmp_path: Path):
    target = tmp_path / "store.bin"
    seed = _cheap_factory(target)
    seed.unlock("right")
    seed.lock()
    _, client = _client()
    r = client.post(
        "/mapping/rotate-key",
        headers={**LOOPBACK, **AUTH},
        json={
            "store_path": str(target),
            "old_passphrase": "wrong",
            "new_passphrase": "new",
        },
    )
    assert r.status_code == 401


def test_rotate_key_400_when_store_missing(tmp_path: Path):
    _, client = _client()
    r = client.post(
        "/mapping/rotate-key",
        headers={**LOOPBACK, **AUTH},
        json={
            "store_path": str(tmp_path / "nope.bin"),
            "old_passphrase": "a",
            "new_passphrase": "b",
        },
    )
    assert r.status_code == 400


def test_rotate_key_409_when_different_store_unlocked(tmp_path: Path):
    """Rotating store B while store A is unlocked must fail — otherwise the
    config slot would end up pointing at the wrong instance."""
    target = tmp_path / "target.bin"
    other = tmp_path / "other.bin"
    for p in (target, other):
        seed = _cheap_factory(p)
        seed.unlock("p")
        seed.lock()

    _, client = _client()
    # Unlock *other* — different path from the one we rotate.
    client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(other), "passphrase": "p"},
    )
    r = client.post(
        "/mapping/rotate-key",
        headers={**LOOPBACK, **AUTH},
        json={
            "store_path": str(target),
            "old_passphrase": "p",
            "new_passphrase": "q",
        },
    )
    assert r.status_code == 409
    assert "different" in r.get_json()["error"]


def test_rotate_key_auto_locks_same_unlocked_store(tmp_path: Path):
    """Rotating the very same store that's currently unlocked should
    auto-lock it and succeed — asking the user to lock-then-rotate an
    already-open store is churn, not safety."""
    target = tmp_path / "same.bin"
    seed = _cheap_factory(target)
    seed.unlock("old-pass")
    seed.lock()

    _, client = _client()
    client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "old-pass"},
    )
    r = client.post(
        "/mapping/rotate-key",
        headers={**LOOPBACK, **AUTH},
        json={
            "store_path": str(target),
            "old_passphrase": "old-pass",
            "new_passphrase": "new-pass",
        },
    )
    assert r.status_code == 200, r.get_json()

    # Health should reflect that the store ended up locked after rotation.
    h = client.get("/health", headers=LOOPBACK)
    assert h.get_json()["mapping_store_unlocked"] is False

    # New passphrase works, old does not.
    good = client.post(
        "/mapping/unlock",
        headers={**LOOPBACK, **AUTH},
        json={"store_path": str(target), "passphrase": "new-pass"},
    )
    assert good.status_code == 200
