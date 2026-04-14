from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from sanctum.core.exceptions import (
    IncorrectPassphraseError,
    MappingStoreError,
    MappingStoreLockedError,
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


def test_inmemory_requires_unlock_before_use():
    s = InMemoryMappingStore()
    with pytest.raises(MappingStoreLockedError):
        s.get_or_create("Alice", "PERSON", lambda: "X")


def test_inmemory_get_or_create_is_idempotent():
    s = InMemoryMappingStore()
    s.unlock()
    f = _counting_factory()
    first = s.get_or_create("Alice", "PERSON", f)
    second = s.get_or_create("Alice", "PERSON", f)
    assert first == second
    # factory called once: counter only advanced once
    assert s.get_or_create("Bob", "PERSON", f) != first


def test_inmemory_reverse_roundtrips():
    s = InMemoryMappingStore()
    s.unlock()
    alias = s.get_or_create("Alice Smith", "PERSON", lambda: "Dana Doe")
    assert s.reverse(alias, "PERSON") == "Alice Smith"
    assert s.reverse("never-issued", "PERSON") is None


def test_inmemory_entity_type_scopes_lookup():
    s = InMemoryMappingStore()
    s.unlock()
    person = s.get_or_create("1234", "PERSON", lambda: "alias-A")
    phone = s.get_or_create("1234", "PHONE_NUMBER", lambda: "alias-B")
    assert person != phone
    assert s.reverse("alias-A", "PERSON") == "1234"
    assert s.reverse("alias-A", "PHONE_NUMBER") is None


def test_inmemory_lock_wipes_entries():
    s = InMemoryMappingStore()
    s.unlock()
    s.get_or_create("Alice", "PERSON", lambda: "X")
    s.lock()
    with pytest.raises(MappingStoreLockedError):
        s.reverse("X", "PERSON")


def test_inmemory_pseudonym_collision_retries():
    s = InMemoryMappingStore()
    s.unlock()
    outputs = iter(["same", "same", "unique"])
    s.get_or_create("Alice", "PERSON", lambda: "same")
    # Second caller hits "same" once, then "unique" is accepted.
    result = s.get_or_create("Bob", "PERSON", lambda: next(outputs))
    assert result == "unique"


def test_inmemory_pseudonym_collision_exhaustion_raises():
    s = InMemoryMappingStore()
    s.unlock()
    s.get_or_create("Alice", "PERSON", lambda: "same")
    with pytest.raises(MappingStoreError):
        s.get_or_create("Bob", "PERSON", lambda: "same")


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


def test_encrypted_bad_magic_raises(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    path.write_bytes(b"NOPE" + b"\x00" * 100)
    s = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(MappingStoreError):
        s.unlock("pw")


def test_encrypted_truncated_file_raises(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    path.write_bytes(b"SANCTUM1" + b"\x00" * 4)
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


def test_encrypted_atomic_write_leaves_no_tmp(tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s = EncryptedFileMappingStore(path, **CHEAP)
    s.unlock("pw")
    s.get_or_create("Alice", "PERSON", lambda: "X")
    s.lock()
    assert not (tmp_path / "store.sanctum.tmp").exists()
