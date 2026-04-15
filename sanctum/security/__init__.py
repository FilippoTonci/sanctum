"""Mapping-store + passphrase crypto for reversible pseudonymization (WS3)."""

from __future__ import annotations

from sanctum.config.settings import SecuritySettings
from sanctum.core.protocols import MappingStore
from sanctum.security.mapping_store import (
    EncryptedFileMappingStore,
    InMemoryMappingStore,
)


def create_mapping_store(settings: SecuritySettings) -> MappingStore:
    """Factory — pick the mapping-store impl that matches ``settings``.

    ``session_only=True`` (default) builds an `InMemoryMappingStore`; nothing
    touches disk. Otherwise `store_path` is guaranteed non-None by
    `SecuritySettings`'s model validator, and the caller must
    ``unlock(passphrase)`` on the returned store before using it.
    """
    if settings.session_only:
        return InMemoryMappingStore()
    assert settings.store_path is not None  # model validator enforces this
    return EncryptedFileMappingStore(
        settings.store_path,
        kdf_time_cost=settings.kdf_time_cost,
        kdf_memory_cost=settings.kdf_memory_cost,
        kdf_parallelism=settings.kdf_parallelism,
    )


__all__ = [
    "EncryptedFileMappingStore",
    "InMemoryMappingStore",
    "MappingStore",
    "create_mapping_store",
]
