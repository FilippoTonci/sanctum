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
    touches disk. Otherwise `store_path` must be set and the caller must
    ``unlock(passphrase)`` before using the store.
    """
    if settings.session_only:
        return InMemoryMappingStore()
    if settings.store_path is None:
        raise ValueError("security.store_path must be set when security.session_only is False")
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
