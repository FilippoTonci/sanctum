from __future__ import annotations

from pathlib import Path

import pytest
from sanctum.config.settings import SecuritySettings
from sanctum.security import (
    EncryptedFileMappingStore,
    InMemoryMappingStore,
    create_mapping_store,
)


def test_session_only_builds_inmemory():
    store = create_mapping_store(SecuritySettings(session_only=True))
    assert isinstance(store, InMemoryMappingStore)


def test_persistent_requires_store_path():
    with pytest.raises(ValueError):
        create_mapping_store(SecuritySettings(session_only=False, store_path=None))


def test_persistent_builds_encrypted_file(tmp_path: Path):
    s = SecuritySettings(
        session_only=False,
        store_path=tmp_path / "store.sanctum",
        kdf_memory_cost=8,
        kdf_time_cost=1,
        kdf_parallelism=1,
    )
    store = create_mapping_store(s)
    assert isinstance(store, EncryptedFileMappingStore)
    assert store.path == tmp_path / "store.sanctum"
