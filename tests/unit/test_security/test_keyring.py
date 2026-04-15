from __future__ import annotations

import pytest
from sanctum.security import keyring

# Test-only params: keep the KDF cheap enough that the full suite stays fast.
# Real defaults (128 MiB, t=3) are exercised implicitly by the integration test.
CHEAP = {"time_cost": 1, "memory_cost": 8, "parallelism": 1}


def test_new_salt_is_random_and_right_size():
    a, b = keyring.new_salt(), keyring.new_salt()
    assert len(a) == keyring.SALT_SIZE
    assert a != b


def test_derive_key_is_deterministic_for_same_inputs():
    salt = keyring.new_salt()
    k1 = keyring.derive_key("correct horse battery staple", salt, **CHEAP)
    k2 = keyring.derive_key("correct horse battery staple", salt, **CHEAP)
    assert k1 == k2
    assert len(k1) == keyring.KEY_SIZE


def test_different_salt_yields_different_key():
    k1 = keyring.derive_key("pw", keyring.new_salt(), **CHEAP)
    k2 = keyring.derive_key("pw", keyring.new_salt(), **CHEAP)
    assert k1 != k2


def test_different_passphrase_yields_different_key():
    salt = keyring.new_salt()
    k1 = keyring.derive_key("password-one", salt, **CHEAP)
    k2 = keyring.derive_key("password-two", salt, **CHEAP)
    assert k1 != k2


def test_empty_passphrase_rejected():
    with pytest.raises(ValueError):
        keyring.derive_key("", keyring.new_salt(), **CHEAP)


def test_bad_salt_size_rejected():
    with pytest.raises(ValueError):
        keyring.derive_key("pw", b"too-short", **CHEAP)
