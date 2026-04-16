"""Pydantic request/response models for the Sanctum API.

One module for every route's wire types so the contract is reviewable in
one place and the GUI team can generate TypeScript clients off a single
import. Models accumulate here as routes land in subsequent substeps.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sanctum.core.models import DetectionResult


class _Frozen(BaseModel):
    """Base for response models — immutable, no extra fields tolerated."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _StrictRequest(BaseModel):
    """Base for request models — extra fields rejected (client contract)."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(_Frozen):
    """Liveness payload for `/health`.

    `mapping_store_unlocked` is always present so the GUI can render the
    lock indicator from a single field; WS4.7 wires it to the real store
    state when the mapping routes land.
    """

    status: Literal["ok"]
    version: str
    mapping_store_unlocked: bool


class AnalyzeRequest(_StrictRequest):
    """Body for `POST /analyze`.

    Mirrors the knobs the CLI `analyze` command exposes. `entities=None`
    means "detect every entity type the analyzer recognizes"; passing an
    empty list narrows the scan to nothing (Presidio semantics — we do
    not second-guess it).
    """

    text: str = Field(min_length=1)
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class AnalyzeResponse(_Frozen):
    """Body for `POST /analyze` — detections plus a convenience count."""

    detections: list[DetectionResult]
    count: int


class AnonymizeRequest(_StrictRequest):
    """Body for `POST /anonymize`.

    `operator` maps onto the ``DEFAULT`` entry in `OperatorPolicy`-land:
    ``None`` means the engine's configured default. ``pseudonymize`` is
    *not* accepted over HTTP until WS4.7 wires the mapping store — the
    store is per-session state that has to be unlocked explicitly first,
    and letting `/anonymize` silently skip it would produce an
    unrecoverable leak of originals.
    """

    text: str = Field(min_length=1)
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    operator: str | None = None


class AnonymizeResponse(_Frozen):
    """Body for `POST /anonymize` — full engine result, wire-friendly."""

    original_text: str
    anonymized_text: str
    detections: list[DetectionResult]
    operators_applied: dict[str, str]


class ProcessFileRequest(_StrictRequest):
    """Body for `POST /process-file`.

    Both paths are *server-side* paths (the API is local; the GUI runs on
    the same machine). The route refuses non-absolute paths and UNC paths
    so a malicious client can't coerce the server into reading from or
    writing to a network share — that would defeat the airgap by smuggling
    data across the network mount.
    """

    input_path: str
    output_path: str
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    operator: str | None = None


class ProcessFileResponse(_Frozen):
    """Body for `POST /process-file` — summary of what the engine did."""

    output_path: str
    segments_changed: int
    entities_replaced: int


class UnlockMappingRequest(_StrictRequest):
    """Body for `POST /mapping/unlock`.

    `store_path` is server-side; same UNC/absolute rules as /process-file.
    A non-existent file is treated as "create on first lock" — matches the
    CLI behavior where `EncryptedFileMappingStore.unlock` on a missing
    path generates a fresh salt and starts with empty entries.
    """

    store_path: str
    passphrase: str = Field(min_length=1)


class MappingStatusResponse(_Frozen):
    """Body for `POST /mapping/{unlock,lock}` — current store state."""

    unlocked: bool
    store_path: str | None


class ReverseMappingRequest(_StrictRequest):
    """Body for `POST /mapping/reverse` — look up the original behind a pseudonym."""

    pseudonym: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)


class ReverseMappingResponse(_Frozen):
    """Body for `POST /mapping/reverse` — the original that maps to the pseudonym."""

    pseudonym: str
    entity_type: str
    original: str


class RotateMappingKeyRequest(_StrictRequest):
    """Body for `POST /mapping/rotate-key` — re-encrypt under a new passphrase."""

    store_path: str
    old_passphrase: str = Field(min_length=1)
    new_passphrase: str = Field(min_length=1)


class RotateMappingKeyResponse(_Frozen):
    """Body for `POST /mapping/rotate-key` — confirmation of rotation."""

    rotated: bool
    store_path: str
