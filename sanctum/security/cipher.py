"""ChaCha20-Poly1305 AEAD wrapper for the encrypted mapping store.

One authenticated-encryption primitive, one nonce size (12 bytes per RFC 8439),
no key schedule or mode knobs exposed to callers. Callers pass the 32-byte key
and the plaintext bytes; we handle nonce generation and ciphertext framing.

Framed output layout:
    [12-byte nonce] || [ciphertext || 16-byte poly1305 tag]

Tamper detection is inherent to the AEAD tag — any bit-flip fails decryption.
"""

from __future__ import annotations

import os
from typing import cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from sanctum.core.exceptions import IncorrectPassphraseError

NONCE_SIZE = 12
KEY_SIZE = 32


def encrypt(key: bytes, plaintext: bytes, associated_data: bytes | None = None) -> bytes:
    """Encrypt ``plaintext`` under ``key`` and return framed nonce||ciphertext."""
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    nonce = os.urandom(NONCE_SIZE)
    ct = cast(bytes, ChaCha20Poly1305(key).encrypt(nonce, plaintext, associated_data))
    return nonce + ct


def decrypt(key: bytes, framed: bytes, associated_data: bytes | None = None) -> bytes:
    """Decrypt a nonce||ciphertext blob. Raises ``IncorrectPassphraseError`` on tag failure."""
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    if len(framed) < NONCE_SIZE:
        raise ValueError("framed ciphertext shorter than nonce length")
    nonce, ct = framed[:NONCE_SIZE], framed[NONCE_SIZE:]
    try:
        return cast(bytes, ChaCha20Poly1305(key).decrypt(nonce, ct, associated_data))
    except InvalidTag as exc:
        raise IncorrectPassphraseError(
            "AEAD authentication failed — wrong passphrase or tampered ciphertext"
        ) from exc
