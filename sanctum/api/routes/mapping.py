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
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Blueprint, current_app

from sanctum.api._internal import parse_body, validate_local_path
from sanctum.api.auth import require_bearer_token
from sanctum.api.schemas import (
    LockMappingResponse,
    ReverseMappingRequest,
    ReverseMappingResponse,
    RotateMappingKeyRequest,
    RotateMappingKeyResponse,
    UnlockMappingRequest,
    UnlockMappingResponse,
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
    req, err = parse_body(UnlockMappingRequest)
    if err is not None:
        return err

    path, perr = validate_local_path(req.store_path, must_exist=False)
    if perr is not None:
        return {"error": f"store_path: {perr}"}, 400
    assert path is not None

    state_lock = current_app.config["SANCTUM_MAPPING_LOCK"]
    with state_lock:
        existing = _current_store()
        if existing is not None and existing.is_unlocked:
            # Refuse to clobber an unlocked store — the caller probably
            # forgot a /mapping/lock and would lose any pending mappings.
            current_app.logger.warning(
                "/mapping/unlock refused: %s is already unlocked", existing.path
            )
            return {
                "error": "a mapping store is already unlocked; lock it first",
                "store_path": str(existing.path),
            }, 409

        store = _new_store(path)
        try:
            store.unlock(req.passphrase)
        except IncorrectPassphraseError:
            current_app.logger.warning("/mapping/unlock: incorrect passphrase for %s", path)
            return {"error": "incorrect passphrase or tampered store"}, 401
        except (MappingStoreError, ValueError) as exc:
            current_app.logger.exception("/mapping/unlock failed for %s", path)
            return {"error": f"unlock failed: {exc}"}, 400

        current_app.config["SANCTUM_MAPPING_STORE"] = store

    payload = UnlockMappingResponse(unlocked=True, store_path=str(path))
    return payload.model_dump(), 200


@mapping_bp.post("/lock")
@require_bearer_token
def lock() -> tuple[dict, int]:
    state_lock = current_app.config["SANCTUM_MAPPING_LOCK"]
    with state_lock:
        store = _current_store()
        if store is None:
            payload = LockMappingResponse(unlocked=False, store_path=None)
            return payload.model_dump(), 200

        path = str(store.path)
        try:
            store.lock()
        except MappingStoreError as exc:
            current_app.logger.exception("/mapping/lock failed for %s", path)
            return {"error": f"lock failed: {exc}"}, 500
        finally:
            # Clear the config slot regardless of whether the write succeeded —
            # an exception out of `.lock()` means the store is in an
            # indeterminate state, and the caller needs a clean slate to retry.
            current_app.config["SANCTUM_MAPPING_STORE"] = None

    payload = LockMappingResponse(unlocked=False, store_path=path)
    return payload.model_dump(), 200


@mapping_bp.post("/reverse")
@require_bearer_token
def reverse() -> tuple[dict, int]:
    store = _current_store()
    if store is None or not store.is_unlocked:
        current_app.logger.info("/mapping/reverse rejected: store is locked")
        return {"error": "mapping store is locked; unlock it first"}, 409

    req, err = parse_body(ReverseMappingRequest)
    if err is not None:
        return err

    try:
        original = store.reverse(req.pseudonym, req.entity_type)
    except MappingStoreLockedError:
        # Race: a concurrent /lock landed between the check and the call.
        current_app.logger.info("/mapping/reverse raced with a /mapping/lock")
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
    req, err = parse_body(RotateMappingKeyRequest)
    if err is not None:
        return err

    if req.old_passphrase == req.new_passphrase:
        return {"error": "new passphrase must differ from old passphrase"}, 400

    path, perr = validate_local_path(req.store_path, must_exist=True)
    if perr is not None:
        return {"error": f"store_path: {perr}"}, 400
    assert path is not None

    state_lock = current_app.config["SANCTUM_MAPPING_LOCK"]
    with state_lock:
        existing = _current_store()
        if existing is not None and existing.is_unlocked:
            # If the unlocked store *is* the one we're rotating, lock it
            # first on the caller's behalf — rotation is an explicit
            # request to touch this store. If it's a *different* store,
            # refuse: rotation would leave the config slot pointing at
            # the wrong instance.
            if Path(existing.path) == path:
                current_app.logger.info(
                    "/mapping/rotate-key: auto-locking %s before rotation", path
                )
                try:
                    existing.lock()
                except MappingStoreError as exc:
                    current_app.logger.exception("auto-lock before rotate failed for %s", path)
                    return {"error": f"auto-lock before rotate failed: {exc}"}, 500
                current_app.config["SANCTUM_MAPPING_STORE"] = None
            else:
                current_app.logger.warning(
                    "/mapping/rotate-key refused: %s is unlocked (rotating %s)",
                    existing.path,
                    path,
                )
                return {
                    "error": (
                        "a different mapping store is currently unlocked; "
                        "lock it before rotating this one"
                    ),
                    "store_path": str(existing.path),
                }, 409

        store = _new_store(path)
        try:
            store.rotate_passphrase(req.old_passphrase, req.new_passphrase)
        except IncorrectPassphraseError:
            current_app.logger.warning("/mapping/rotate-key: incorrect old passphrase for %s", path)
            return {"error": "incorrect old passphrase or tampered store"}, 401
        except (MappingStoreError, ValueError) as exc:
            current_app.logger.exception("/mapping/rotate-key failed for %s", path)
            return {"error": f"rotate failed: {exc}"}, 400
        finally:
            # rotate_passphrase leaves the store unlocked; lock it back so
            # the caller's invariant ("rotation does not leak state") holds.
            with contextlib.suppress(MappingStoreError):
                store.lock()

    payload = RotateMappingKeyResponse(rotated=True, store_path=str(path))
    return payload.model_dump(), 200


__all__: list[str] = ["mapping_bp"]
