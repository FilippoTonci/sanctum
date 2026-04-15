from __future__ import annotations

import os

import pytest
from sanctum.core.exceptions import IncorrectPassphraseError
from sanctum.security import cipher


def test_roundtrip_preserves_plaintext():
    key = os.urandom(cipher.KEY_SIZE)
    pt = b"Alice Smith lives at 742 Evergreen Terrace."
    assert cipher.decrypt(key, cipher.encrypt(key, pt)) == pt


def test_roundtrip_with_associated_data():
    key = os.urandom(cipher.KEY_SIZE)
    pt, ad = b"PERSON::Alice", b"sanctum-mapping-v1"
    assert cipher.decrypt(key, cipher.encrypt(key, pt, ad), ad) == pt


def test_each_encrypt_uses_fresh_nonce():
    key = os.urandom(cipher.KEY_SIZE)
    a = cipher.encrypt(key, b"same plaintext")
    b = cipher.encrypt(key, b"same plaintext")
    assert a != b, "random nonces should make ciphertexts differ"
    assert a[: cipher.NONCE_SIZE] != b[: cipher.NONCE_SIZE]


def test_wrong_key_raises_incorrect_passphrase():
    framed = cipher.encrypt(os.urandom(cipher.KEY_SIZE), b"secret")
    with pytest.raises(IncorrectPassphraseError):
        cipher.decrypt(os.urandom(cipher.KEY_SIZE), framed)


def test_tampered_ciphertext_raises_incorrect_passphrase():
    key = os.urandom(cipher.KEY_SIZE)
    framed = bytearray(cipher.encrypt(key, b"secret payload"))
    framed[-1] ^= 0x01  # flip one bit in the tag
    with pytest.raises(IncorrectPassphraseError):
        cipher.decrypt(key, bytes(framed))


def test_tampered_associated_data_raises():
    key = os.urandom(cipher.KEY_SIZE)
    framed = cipher.encrypt(key, b"payload", b"ad-original")
    with pytest.raises(IncorrectPassphraseError):
        cipher.decrypt(key, framed, b"ad-tampered")


def test_invalid_key_size_raises_value_error():
    with pytest.raises(ValueError):
        cipher.encrypt(os.urandom(16), b"x")
    with pytest.raises(ValueError):
        cipher.decrypt(os.urandom(16), b"x" * 32)


def test_truncated_framed_blob_raises_value_error():
    with pytest.raises(ValueError):
        cipher.decrypt(os.urandom(cipher.KEY_SIZE), b"too-short")
