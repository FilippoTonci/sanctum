"""Mapping-store implementations for reversible pseudonymization.

Two concrete impls behind the ``MappingStore`` Protocol:

* ``InMemoryMappingStore`` — session-only. A dict that dies with the
  process. No passphrase, no disk, no crypto. Default for one-off runs.

* ``EncryptedFileMappingStore`` — persistent. A single file, framed as:

      magic        b"SANCTUM1"          8 bytes
      salt         Argon2id salt       16 bytes
      params_len   u32 little-endian    4 bytes
      params_json  KDF params          `params_len` bytes
      aead_blob    nonce || ct || tag   remainder

  On ``unlock()`` we decrypt the whole payload into an in-memory dict;
  on ``lock()`` we re-encrypt and atomically rename the file into place.
  Scale is capped by RAM — comfortable up to ~10^5 entities, which covers
  every legal/consulting use we're targeting. A SQLite-backed impl is a
  deferred follow-up behind the same Protocol (no caller change needed).
"""

from __future__ import annotations

import contextlib
import json
import os
import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from sanctum.core.exceptions import (
    MappingStoreError,
    MappingStoreLockedError,
)
from sanctum.security import cipher, keyring

_MAGIC: Final[bytes] = b"SANCTUM1"
_PAYLOAD_VERSION: Final[int] = 1
_AD: Final[bytes] = b"sanctum-mapping-v1"  # AEAD associated-data tag
_MAX_PSEUDONYM_RETRIES: Final[int] = 5


def _key(entity_type: str, original: str) -> str:
    return f"{entity_type}::{original}"


class _BaseStore:
    """Shared dict plumbing + get_or_create/reverse logic for the two impls."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] | None = None

    @property
    def is_unlocked(self) -> bool:
        return self._entries is not None

    def _require_unlocked(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            raise MappingStoreLockedError("mapping store is locked")
        return self._entries

    def get_or_create(self, original: str, entity_type: str, factory: Callable[[], str]) -> str:
        with self._lock:
            entries = self._require_unlocked()
            key = _key(entity_type, original)
            existing = entries.get(key)
            if existing is not None:
                return str(existing["pseudonym"])

            taken = {e["pseudonym"] for k, e in entries.items() if k.startswith(f"{entity_type}::")}
            for _ in range(_MAX_PSEUDONYM_RETRIES):
                candidate = factory()
                if candidate not in taken:
                    entries[key] = {"pseudonym": candidate}
                    return candidate
            raise MappingStoreError(
                f"could not generate a unique pseudonym for {entity_type} "
                f"after {_MAX_PSEUDONYM_RETRIES} attempts"
            )

    def reverse(self, pseudonym: str, entity_type: str) -> str | None:
        with self._lock:
            entries = self._require_unlocked()
            prefix = f"{entity_type}::"
            for key, entry in entries.items():
                if key.startswith(prefix) and entry["pseudonym"] == pseudonym:
                    return key[len(prefix) :]
            return None


class InMemoryMappingStore(_BaseStore):
    """Session-only mapping store. Dies with the process."""

    def unlock(self, passphrase: str | None = None) -> None:
        with self._lock:
            if self._entries is None:
                self._entries = {}

    def lock(self) -> None:
        with self._lock:
            self._entries = None


class EncryptedFileMappingStore(_BaseStore):
    """Passphrase-encrypted mapping store backed by a single file."""

    def __init__(
        self,
        path: Path,
        *,
        kdf_time_cost: int = keyring.DEFAULT_TIME_COST,
        kdf_memory_cost: int = keyring.DEFAULT_MEMORY_COST_KIB,
        kdf_parallelism: int = keyring.DEFAULT_PARALLELISM,
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._kdf_time_cost = kdf_time_cost
        self._kdf_memory_cost = kdf_memory_cost
        self._kdf_parallelism = kdf_parallelism
        self._salt: bytes | None = None
        self._key: bytes | None = None

    @property
    def path(self) -> Path:
        return self._path

    def unlock(self, passphrase: str | None = None) -> None:
        if not passphrase:
            raise ValueError("EncryptedFileMappingStore requires a passphrase")
        with self._lock:
            if self._path.exists():
                salt, params, aead = self._read_file()
                self._salt = salt
                self._key = keyring.derive_key(
                    passphrase,
                    salt,
                    time_cost=params["time_cost"],
                    memory_cost=params["memory_cost"],
                    parallelism=params["parallelism"],
                )
                plaintext = cipher.decrypt(self._key, aead, _AD)
                self._entries = self._deserialize(plaintext)
            else:
                self._salt = keyring.new_salt()
                self._key = keyring.derive_key(
                    passphrase,
                    self._salt,
                    time_cost=self._kdf_time_cost,
                    memory_cost=self._kdf_memory_cost,
                    parallelism=self._kdf_parallelism,
                )
                self._entries = {}

    def lock(self) -> None:
        with self._lock:
            if self._entries is not None and self._key is not None and self._salt is not None:
                self._write_file(self._salt, self._entries, self._key)
            self._entries = None
            self._key = None
            self._salt = None

    def rotate_passphrase(self, old: str, new: str) -> None:
        with self._lock:
            self.unlock(old)
            entries = self._require_unlocked()
            new_salt = keyring.new_salt()
            new_key = keyring.derive_key(
                new,
                new_salt,
                time_cost=self._kdf_time_cost,
                memory_cost=self._kdf_memory_cost,
                parallelism=self._kdf_parallelism,
            )
            self._write_file(new_salt, entries, new_key)
            self._salt, self._key = new_salt, new_key

    def _kdf_params(self) -> dict[str, int]:
        return {
            "time_cost": self._kdf_time_cost,
            "memory_cost": self._kdf_memory_cost,
            "parallelism": self._kdf_parallelism,
        }

    def _serialize(self, entries: dict[str, dict[str, Any]]) -> bytes:
        payload = {"version": _PAYLOAD_VERSION, "entries": entries}
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    def _deserialize(self, plaintext: bytes) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MappingStoreError("decrypted payload is not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("version") != _PAYLOAD_VERSION:
            raise MappingStoreError("unrecognized mapping payload version")
        entries = payload.get("entries", {})
        if not isinstance(entries, dict):
            raise MappingStoreError("payload 'entries' must be an object")
        return entries

    def _read_file(self) -> tuple[bytes, dict[str, int], bytes]:
        blob = self._path.read_bytes()
        if len(blob) < len(_MAGIC) + keyring.SALT_SIZE + 4:
            raise MappingStoreError(f"mapping file {self._path} is truncated")
        if blob[: len(_MAGIC)] != _MAGIC:
            raise MappingStoreError(f"mapping file {self._path} missing {_MAGIC!r} magic header")
        offset = len(_MAGIC)
        salt = blob[offset : offset + keyring.SALT_SIZE]
        offset += keyring.SALT_SIZE
        (params_len,) = struct.unpack_from("<I", blob, offset)
        offset += 4
        params_raw = blob[offset : offset + params_len]
        offset += params_len
        try:
            params = json.loads(params_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MappingStoreError("invalid KDF params header") from exc
        for required in ("time_cost", "memory_cost", "parallelism"):
            if required not in params:
                raise MappingStoreError(f"KDF params missing {required!r}")
        return salt, params, blob[offset:]

    def _write_file(
        self,
        salt: bytes,
        entries: dict[str, dict[str, Any]],
        key: bytes,
    ) -> None:
        plaintext = self._serialize(entries)
        aead = cipher.encrypt(key, plaintext, _AD)
        params_raw = json.dumps(self._kdf_params(), sort_keys=True).encode("utf-8")
        body = _MAGIC + salt + struct.pack("<I", len(params_raw)) + params_raw + aead

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()
