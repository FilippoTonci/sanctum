"""Argon2id passphrase KDF.

Derives a 32-byte AEAD key from a passphrase + per-store salt. Parameters
follow RFC 9106 recommendations for interactive use on a modern desktop:
``t=3, m=128 MiB, p=1``. A 200-500 ms unlock cost is the right tradeoff
for a local legal/consulting tool — the attacker model is an offline
brute-force against a stolen mapping file, not a login form.

The salt is stored in cleartext inside the mapping file header (it's not
secret — it just has to be unique per store). The derived key never
touches disk.
"""

from __future__ import annotations

import os
from typing import cast

from argon2.low_level import Type, hash_secret_raw

SALT_SIZE = 16
KEY_SIZE = 32

# RFC 9106 "second recommended" parameters, tuned for interactive unlock.
DEFAULT_TIME_COST = 3
DEFAULT_MEMORY_COST_KIB = 128 * 1024  # 128 MiB
DEFAULT_PARALLELISM = 1


def new_salt() -> bytes:
    """Generate a fresh 16-byte random salt for a new mapping store."""
    return os.urandom(SALT_SIZE)


def derive_key(
    passphrase: str,
    salt: bytes,
    *,
    time_cost: int = DEFAULT_TIME_COST,
    memory_cost: int = DEFAULT_MEMORY_COST_KIB,
    parallelism: int = DEFAULT_PARALLELISM,
) -> bytes:
    """Derive a 32-byte key from ``passphrase`` + ``salt`` via Argon2id.

    ``memory_cost`` is in KiB (argon2-cffi convention). Determinism is
    guaranteed for fixed (passphrase, salt, params); callers persist the
    params alongside the salt in the store header.
    """
    if len(salt) != SALT_SIZE:
        raise ValueError(f"salt must be {SALT_SIZE} bytes, got {len(salt)}")
    if not passphrase:
        raise ValueError("passphrase must be non-empty")
    return cast(
        bytes,
        hash_secret_raw(
            secret=passphrase.encode("utf-8"),
            salt=salt,
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=KEY_SIZE,
            type=Type.ID,
        ),
    )
