"""`/mapping/*` routes — unlock, lock, reverse, rotate-key.

The encrypted mapping store lives in ``current_app.config["SANCTUM_MAPPING_STORE"]``
once unlocked, and is cleared back to ``None`` on lock. ``/health`` reads
that same key to flip its ``mapping_store_unlocked`` flag, so the GUI's
lock indicator stays in sync without any extra plumbing.

A ``threading.Lock`` in ``app.config["SANCTUM_MAPPING_LOCK"]`` serializes
unlock/lock state transitions — without it, two concurrent waitress
worker threads could both ``unlock`` and one would silently overwrite
the other's flock handle. Reads through ``/mapping/reverse`` don't need
the app-level lock because the store has its own internal RLock.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from flask import Blueprint, current_app

from sanctum.api.auth import require_bearer_token
from sanctum.api.routes.pipeline import _parse_body, _validate_local_path
from sanctum.api.schemas import (
    MappingStatusResponse,
    ReverseMappingRequest,
    ReverseMappingResponse,
    RotateMappingKeyRequest,
    RotateMappingKeyResponse,
    UnlockMappingRequest,
)
from sanctum.core.exceptions import (
    IncorrectPassphraseError,
    MappingStoreError,
    MappingStoreLockedError,
)
from sanctum.security import EncryptedFileMappingStore

mapping_bp = Blueprint("mapping", __name__, url_prefix="/mapping")


def _current_store() -> EncryptedFileMappingStore | None:
    store = current_app.config.get("SANCTUM_MAPPING_STORE")
    return store if isinstance(store, EncryptedFileMappingStore) else None


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _new_store(path: Path) -> EncryptedFileMappingStore:
    """Build a fresh store via the configured factory.

    Production wiring uses ``EncryptedFileMappingStore(path)`` with default
    KDF params; tests inject a factory with cheap params so the suite
    doesn't pay the 200-500 ms unlock cost on every test.
    """
    factory: Callable[[Path], EncryptedFileMappingStore] = current_app.config[
        "SANCTUM_MAPPING_STORE_FACTORY"
    ]
    return factory(path)


@mapping_bp.post("/unlock")
@require_bearer_token
def unlock() -> tuple[dict, int]:
    req, err = _parse_body(UnlockMappingRequest)
    if err is not None:
        return err

    path, perr = _validate_local_path(req.store_path, must_exist=False)
    if perr is not None:
        return {"error": f"store_path: {perr}"}, 400
    assert path is not None

    state_lock = current_app.config["SANCTUM_MAPPING_LOCK"]
    with state_lock:
        existing = _current_store()
        if existing is not None and existing.is_unlocked:
            # Refuse to clobber an unlocked store — the caller probably
            # forgot a /mapping/lock and would lose any pending mappings.
            return {
                "error": "a mapping store is already unlocked; lock it first",
                "store_path": str(existing.path),
            }, 409

        store = _new_store(path)
        try:
            store.unlock(req.passphrase)
        except IncorrectPassphraseError:
            return {"error": "incorrect passphrase or tampered store"}, 401
        except (MappingStoreError, ValueError) as exc:
            return {"error": f"unlock failed: {exc}"}, 400

        current_app.config["SANCTUM_MAPPING_STORE"] = store

    payload = MappingStatusResponse(unlocked=True, store_path=str(path))
    return payload.model_dump(), 200


@mapping_bp.post("/lock")
@require_bearer_token
def lock() -> tuple[dict, int]:
    state_lock = current_app.config["SANCTUM_MAPPING_LOCK"]
    with state_lock:
        store = _current_store()
        if store is None:
            payload = MappingStatusResponse(unlocked=False, store_path=None)
            return payload.model_dump(), 200

        path = str(store.path)
        try:
            store.lock()
        except MappingStoreError as exc:
            return {"error": f"lock failed: {exc}"}, 500
        finally:
            # Clear the config slot regardless of whether the write succeeded —
            # an exception out of `.lock()` means the store is in an
            # indeterminate state, and the caller needs a clean slate to retry.
            current_app.config["SANCTUM_MAPPING_STORE"] = None

    payload = MappingStatusResponse(unlocked=False, store_path=path)
    return payload.model_dump(), 200


@mapping_bp.post("/reverse")
@require_bearer_token
def reverse() -> tuple[dict, int]:
    store = _current_store()
    if store is None or not store.is_unlocked:
        return {"error": "mapping store is locked; unlock it first"}, 409

    req, err = _parse_body(ReverseMappingRequest)
    if err is not None:
        return err

    try:
        original = store.reverse(req.pseudonym, req.entity_type)
    except MappingStoreLockedError:
        # Race: a concurrent /lock landed between the check and the call.
        return {"error": "mapping store is locked; unlock it first"}, 409

    if original is None:
        return {
            "error": "no mapping found",
            "pseudonym": req.pseudonym,
            "entity_type": req.entity_type,
        }, 404

    payload = ReverseMappingResponse(
        pseudonym=req.pseudonym,
        entity_type=req.entity_type,
        original=original,
    )
    return payload.model_dump(), 200


@mapping_bp.post("/rotate-key")
@require_bearer_token
def rotate_key() -> tuple[dict, int]:
    req, err = _parse_body(RotateMappingKeyRequest)
    if err is not None:
        return err

    if req.old_passphrase == req.new_passphrase:
        return {"error": "new passphrase must differ from old passphrase"}, 400

    path, perr = _validate_local_path(req.store_path, must_exist=True)
    if perr is not None:
        return {"error": f"store_path: {perr}"}, 400
    assert path is not None

    state_lock = current_app.config["SANCTUM_MAPPING_LOCK"]
    with state_lock:
        existing = _current_store()
        if existing is not None and existing.is_unlocked:
            # Rotating while another store is unlocked would leave the
            # config slot pointing at the *old* instance — refuse loudly.
            return {
                "error": "lock the currently unlocked mapping store before rotating",
                "store_path": str(existing.path),
            }, 409

        store = _new_store(path)
        try:
            store.rotate_passphrase(req.old_passphrase, req.new_passphrase)
        except IncorrectPassphraseError:
            return {"error": "incorrect old passphrase or tampered store"}, 401
        except (MappingStoreError, ValueError) as exc:
            return {"error": f"rotate failed: {exc}"}, 400
        finally:
            # rotate_passphrase leaves the store unlocked; lock it back so
            # the caller's invariant ("rotation does not leak state") holds.
            with contextlib.suppress(MappingStoreError):
                store.lock()

    payload = RotateMappingKeyResponse(rotated=True, store_path=str(path))
    return payload.model_dump(), 200


__all__: list[str] = ["mapping_bp"]
