from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from sanctum.core.exceptions import (
    IncorrectPassphraseError,
    MappingStoreError,
)
from sanctum.security.mapping_store import (
    EncryptedFileMappingStore,
    InMemoryMappingStore,
)

# Cheap KDF params so the test suite stays fast; real defaults
# (128 MiB, t=3) are exercised implicitly by the integration test.
CHEAP = {"kdf_time_cost": 1, "kdf_memory_cost": 8, "kdf_parallelism": 1}


def _counting_factory(prefix: str = "alias"):
    counter = itertools.count()
    return lambda: f"{prefix}-{next(counter)}"


# --- InMemoryMappingStore -------------------------------------------------


def test_inmemory_is_ready_on_construction():
    s = InMemoryMappingStore()
    assert s.is_unlocked
    alias = s.get_or_create("Alice", "PERSON", lambda: "X")
    assert alias == "X"


def test_inmemory_get_or_create_is_idempotent():
    s = InMemoryMappingStore()
    f = _counting_factory()
    first = s.get_or_create("Alice", "PERSON", f)
    second = s.get_or_create("Alice", "PERSON", f)
    assert first == second
    # factory called once: counter only advanced once
    assert s.get_or_create("Bob", "PERSON", f) != first


def test_inmemory_reverse_roundtrips():
    s = InMemoryMappingStore()
    alias = s.get_or_create("Alice Smith", "PERSON", lambda: "Dana Doe")
    assert s.reverse(alias, "PERSON") == "Alice Smith"
    assert s.reverse("never-issued", "PERSON") is None


def test_inmemory_entity_type_scopes_lookup():
    s = InMemoryMappingStore()
    person = s.get_or_create("1234", "PERSON", lambda: "alias-A")
    phone = s.get_or_create("1234", "PHONE_NUMBER", lambda: "alias-B")
    assert person != phone
    assert s.reverse("alias-A", "PERSON") == "1234"
    assert s.reverse("alias-A", "PHONE_NUMBER") is None


def test_inmemory_entity_types_with_separator_in_original():
    """Nested storage: ``::`` in the original string can't collide keys."""
    s = InMemoryMappingStore()
    a = s.get_or_create("foo::bar", "PERSON", lambda: "alias-A")
    b = s.get_or_create("bar", "foo::PERSON", lambda: "alias-B")
    assert a != b
    assert s.reverse("alias-A", "PERSON") == "foo::bar"
    assert s.reverse("alias-A", "foo::PERSON") is None


def test_inmemory_pseudonym_collision_retries():
    s = InMemoryMappingStore()
    outputs = iter(["same", "same", "unique"])
    s.get_or_create("Alice", "PERSON", lambda: "same")
    result = s.get_or_create("Bob", "PERSON", lambda: next(outputs))
    assert result == "unique"


def test_inmemory_pseudonym_exhaustion_uses_deterministic_suffix():
    """Small-domain fallback: retries exhausted → suffix for guaranteed progress."""
    s = InMemoryMappingStore()
    s.get_or_create("Alice", "PERSON", lambda: "same")
    # Factory always collides; fallback appends -1, -2, ...
    result = s.get_or_create("Bob", "PERSON", lambda: "same")
    assert result == "same-1"
    result2 = s.get_or_create("Carol", "PERSON", lambda: "same")
    assert result2 == "same-2"


# --- EncryptedFileMappingStore -------------------------------------------


def test_encrypted_requires_passphrase(tmp_path: Path):
    s = EncryptedFileMappingStore(tmp_path / "store.sanctum", **CHEAP)
    with pytest.raises(ValueError):
        s.unlock("")
    with pytest.raises(ValueError):
        s.unlock(None)


def test_encrypted_roundtrip_persists_across_instances(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s1 = EncryptedFileMappingStore(path, **CHEAP)
    s1.unlock("correct horse battery staple")
    alias = s1.get_or_create("Alice Smith", "PERSON", lambda: "Dana Doe")
    s1.lock()
    assert path.exists()

    s2 = EncryptedFileMappingStore(path, **CHEAP)
    s2.unlock("correct horse battery staple")
    assert s2.reverse(alias, "PERSON") == "Alice Smith"
    s2.lock()


def test_encrypted_wrong_passphrase_raises(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s1 = EncryptedFileMappingStore(path, **CHEAP)
    s1.unlock("correct")
    s1.get_or_create("Alice", "PERSON", lambda: "X")
    s1.lock()

    s2 = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(IncorrectPassphraseError):
        s2.unlock("wrong")


def test_encrypted_tampered_file_raises(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s1 = EncryptedFileMappingStore(path, **CHEAP)
    s1.unlock("pw")
    s1.get_or_create("Alice", "PERSON", lambda: "X")
    s1.lock()

    # Flip a byte in the AEAD ciphertext region (last byte = part of tag).
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0x01
    path.write_bytes(bytes(blob))

    s2 = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(IncorrectPassphraseError):
        s2.unlock("pw")


def test_encrypted_tampered_header_fails_auth(tmp_path: Path):
    """Salt + KDF params are bound into AD, so header tampering fails auth."""
    path = tmp_path / "store.sanctum"
    s1 = EncryptedFileMappingStore(path, **CHEAP)
    s1.unlock("pw")
    s1.get_or_create("Alice", "PERSON", lambda: "X")
    s1.lock()

    blob = bytearray(path.read_bytes())
    # Flip a byte inside the salt region (starts right after 8-byte magic).
    blob[8] ^= 0x01
    path.write_bytes(bytes(blob))

    s2 = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises((IncorrectPassphraseError, MappingStoreError)):
        s2.unlock("pw")


def test_encrypted_bad_magic_raises(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    path.write_bytes(b"NOPE" + b"\x00" * 100)
    s = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(MappingStoreError):
        s.unlock("pw")


def test_encrypted_truncated_file_raises(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    path.write_bytes(b"SANCTUM2" + b"\x00" * 4)
    s = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(MappingStoreError):
        s.unlock("pw")


def test_encrypted_kdf_params_out_of_bounds_rejected(tmp_path: Path):
    """Header with absurd KDF cost is refused — can't DoS on unlock."""
    import json
    import struct

    path = tmp_path / "store.sanctum"
    salt = b"\x00" * 16
    evil_params = json.dumps(
        {"time_cost": 3, "memory_cost": 10 * 1024 * 1024 * 1024, "parallelism": 1}
    ).encode()
    blob = b"SANCTUM2" + salt + struct.pack("<I", len(evil_params)) + evil_params + b"\x00" * 32
    path.write_bytes(blob)

    s = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(MappingStoreError):
        s.unlock("pw")


def test_encrypted_rotate_passphrase_preserves_entries(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s = EncryptedFileMappingStore(path, **CHEAP)
    s.unlock("old-pw")
    alias = s.get_or_create("Alice", "PERSON", lambda: "X")
    s.lock()

    s2 = EncryptedFileMappingStore(path, **CHEAP)
    s2.rotate_passphrase("old-pw", "new-pw")
    s2.lock()

    s3 = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(IncorrectPassphraseError):
        s3.unlock("old-pw")

    s3.unlock("new-pw")
    assert s3.reverse(alias, "PERSON") == "Alice"
    s3.lock()


def test_encrypted_atomic_write_leaves_no_tmp(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s = EncryptedFileMappingStore(path, **CHEAP)
    s.unlock("pw")
    s.get_or_create("Alice", "PERSON", lambda: "X")
    s.lock()
    assert not (tmp_path / "store.sanctum.tmp").exists()


def test_encrypted_flock_blocks_concurrent_unlock(tmp_path: Path):
    """Second unlock on the same file refuses while the first holds the lock."""
    pytest.importorskip("fcntl")
    path = tmp_path / "store.sanctum"
    s1 = EncryptedFileMappingStore(path, **CHEAP)
    s1.unlock("pw")
    try:
        s2 = EncryptedFileMappingStore(path, **CHEAP)
        with pytest.raises(MappingStoreError):
            s2.unlock("pw")
    finally:
        s1.lock()
