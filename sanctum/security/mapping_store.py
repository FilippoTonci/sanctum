"""Mapping-store implementations for reversible pseudonymization.

Two concrete impls behind the ``MappingStore`` Protocol:

* ``InMemoryMappingStore`` — session-only. A dict that dies with the
  process. No passphrase, no disk, no crypto, no lifecycle. Construct
  and use.

* ``EncryptedFileMappingStore`` — persistent. A single file, framed as:

      magic        b"SANCTUM2"          8 bytes
      salt         Argon2id salt       16 bytes
      params_len   u32 little-endian    4 bytes
      params_json  KDF params          `params_len` bytes
      aead_blob    nonce || ct || tag   remainder

  The header prefix (magic..params_json) is bound into the AEAD
  associated data, so tampering with salt or KDF params fails the tag.
  ``unlock()`` acquires an exclusive OS-level lock on the file until
  ``lock()`` releases it, so two concurrent CLI invocations can't
  clobber each other's writes.
"""

from __future__ import annotations

import contextlib
import json
import os
import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

from sanctum.core.exceptions import (
    MappingStoreError,
    MappingStoreLockedError,
)
from sanctum.security import cipher, keyring

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:  # Windows
    _HAS_FCNTL = False

_MAGIC: Final[bytes] = b"SANCTUM2"
_PAYLOAD_VERSION: Final[int] = 2
_MAX_PSEUDONYM_RETRIES: Final[int] = 5

# Clamp KDF params read from the file header. Upper bounds prevent a
# hostile writer from forcing a DoS-sized Argon2 derivation on unlock;
# lower bounds keep the security floor intact.
_KDF_BOUNDS: Final[dict[str, tuple[int, int]]] = {
    "time_cost": (1, 10),
    "memory_cost": (8, 1024 * 1024),  # 8 KiB .. 1 GiB
    "parallelism": (1, 16),
}


class _BaseStore:
    """Shared nested-dict plumbing + get_or_create/reverse logic."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # {entity_type: {original: pseudonym}} — nested, no separator collisions.
        self._entries: dict[str, dict[str, str]] | None = None

    @property
    def is_unlocked(self) -> bool:
        return self._entries is not None

    def _require_unlocked(self) -> dict[str, dict[str, str]]:
        if self._entries is None:
            raise MappingStoreLockedError("mapping store is locked")
        return self._entries

    def get_or_create(self, original: str, entity_type: str, factory: Callable[[], str]) -> str:
        with self._lock:
            entries = self._require_unlocked()
            bucket = entries.setdefault(entity_type, {})
            existing = bucket.get(original)
            if existing is not None:
                return existing

            taken = set(bucket.values())
            for _ in range(_MAX_PSEUDONYM_RETRIES):
                candidate = factory()
                if candidate not in taken:
                    bucket[original] = candidate
                    return candidate

            # Small-domain fallback: deterministically suffix a fresh factory
            # output until unique. Guarantees progress for entity types whose
            # Faker provider has a bounded output space (e.g. DATE_TIME).
            base = factory()
            suffix = 1
            while True:
                candidate = f"{base}-{suffix}"
                if candidate not in taken:
                    bucket[original] = candidate
                    return candidate
                suffix += 1

    def reverse(self, pseudonym: str, entity_type: str) -> str | None:
        with self._lock:
            entries = self._require_unlocked()
            bucket = entries.get(entity_type, {})
            for original, alias in bucket.items():
                if alias == pseudonym:
                    return original
            return None


class InMemoryMappingStore(_BaseStore):
    """Session-only mapping store. Always ready, dies with the process."""

    def __init__(self) -> None:
        super().__init__()
        self._entries = {}


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
        self._flock_fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def unlock(self, passphrase: str | None = None) -> None:
        if not passphrase:
            raise ValueError("EncryptedFileMappingStore requires a passphrase")
        with self._lock:
            self._acquire_flock()
            try:
                if self._path.exists():
                    salt, params, header_prefix, aead = self._read_file()
                    self._salt = salt
                    self._key = keyring.derive_key(
                        passphrase,
                        salt,
                        time_cost=params["time_cost"],
                        memory_cost=params["memory_cost"],
                        parallelism=params["parallelism"],
                    )
                    plaintext = cipher.decrypt(self._key, aead, header_prefix)
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
            except BaseException:
                self._release_flock()
                raise

    def lock(self) -> None:
        with self._lock:
            try:
                if self._entries is not None and self._key is not None and self._salt is not None:
                    self._write_file(self._salt, self._entries, self._key)
            finally:
                self._entries = None
                self._key = None
                self._salt = None
                self._release_flock()

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

    def _acquire_flock(self) -> None:
        if not _HAS_FCNTL:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise MappingStoreError(
                f"another process holds the mapping-store lock at {lock_path}"
            ) from exc
        self._flock_fd = fd

    def _release_flock(self) -> None:
        if self._flock_fd is None:
            return
        try:
            fcntl.flock(self._flock_fd, fcntl.LOCK_UN)
        finally:
            with contextlib.suppress(OSError):
                os.close(self._flock_fd)
            self._flock_fd = None

    def _kdf_params(self) -> dict[str, int]:
        return {
            "time_cost": self._kdf_time_cost,
            "memory_cost": self._kdf_memory_cost,
            "parallelism": self._kdf_parallelism,
        }

    def _serialize(self, entries: dict[str, dict[str, str]]) -> bytes:
        payload = {"version": _PAYLOAD_VERSION, "entries": entries}
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    def _deserialize(self, plaintext: bytes) -> dict[str, dict[str, str]]:
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MappingStoreError("decrypted payload is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise MappingStoreError("decrypted payload must be an object")
        version = payload.get("version")
        if version != _PAYLOAD_VERSION:
            raise MappingStoreError(
                f"unsupported mapping payload version {version!r} "
                f"(this build reads version {_PAYLOAD_VERSION})"
            )
        entries = payload.get("entries", {})
        if not isinstance(entries, dict):
            raise MappingStoreError("payload 'entries' must be an object")
        for entity_type, bucket in entries.items():
            if not isinstance(bucket, dict):
                raise MappingStoreError(f"entries[{entity_type!r}] must be an object")
        return entries

    def _read_file(self) -> tuple[bytes, dict[str, int], bytes, bytes]:
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
        if params_len > 4096 or offset + params_len > len(blob):
            raise MappingStoreError("KDF params header length is implausible")
        params_raw = blob[offset : offset + params_len]
        offset += params_len
        try:
            params = json.loads(params_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MappingStoreError("invalid KDF params header") from exc
        for field, (lo, hi) in _KDF_BOUNDS.items():
            if field not in params:
                raise MappingStoreError(f"KDF params missing {field!r}")
            value = params[field]
            if not isinstance(value, int) or not lo <= value <= hi:
                raise MappingStoreError(f"KDF param {field}={value!r} out of bounds [{lo}, {hi}]")
        header_prefix = blob[:offset]
        return salt, params, header_prefix, blob[offset:]

    def _write_file(
        self,
        salt: bytes,
        entries: dict[str, dict[str, str]],
        key: bytes,
    ) -> None:
        plaintext = self._serialize(entries)
        params_raw = json.dumps(self._kdf_params(), sort_keys=True).encode("utf-8")
        header_prefix = _MAGIC + salt + struct.pack("<I", len(params_raw)) + params_raw
        aead = cipher.encrypt(key, plaintext, header_prefix)
        body = header_prefix + aead

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
            self._fsync_parent()
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()

    def _fsync_parent(self) -> None:
        # Best-effort: on Windows fsync on a directory fd isn't supported.
        try:
            fd = os.open(str(self._path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
