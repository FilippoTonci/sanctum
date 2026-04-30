"""Generate ``schema/openapi.json`` from the Flask route surface + Pydantic schemas.

The desktop app (``sanctum-desktop``) builds its typed TypeScript HTTP
client from this artefact. CI re-runs this script and diffs the output
against the committed file; a non-empty diff fails the build.

Design:

- The authoritative route list lives in the ``ROUTES`` constant below.
  New / removed routes update this list *and* the PR updates the
  committed ``schema/openapi.json``.
- Pydantic v2's ``model_json_schema`` produces per-model JSON Schema; we
  hoist nested ``$defs`` into a single ``components.schemas`` pool so
  every model is referenced via ``$ref`` (standard OpenAPI shape).
- Output is deterministic: sorted keys, 2-space indent, trailing newline.

The script exits 0 on success and writes the file. Invoke from the repo
root: ``python scripts/generate_openapi.py``.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

# Make the script runnable from the repo root without `pip install -e .`
# having first put `sanctum` on the path in the calling shell.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pydantic import BaseModel  # noqa: E402
from sanctum import __version__  # noqa: E402
from sanctum.api.schemas import (  # noqa: E402
    AddUserAddedDecisionRequest,
    AnalyzeRequest,
    AnalyzeResponse,
    AnonymizeRequest,
    AnonymizeResponse,
    CommitReviewSessionRequest,
    CommitReviewSessionResponse,
    CreateReviewSessionRequest,
    DecisionWithPreviewResponse,
    HealthResponse,
    LockMappingResponse,
    PatchProposalDecisionRequest,
    ProcessFileRequest,
    ProcessFileResponse,
    ProcessFileReviewResponse,
    ReverseMappingRequest,
    ReverseMappingResponse,
    ReviewSessionListResponse,
    ReviewSessionResponse,
    RotateMappingKeyRequest,
    RotateMappingKeyResponse,
    UnlockMappingRequest,
    UnlockMappingResponse,
)

REF_TEMPLATE = "#/components/schemas/{model}"
OUTPUT = _REPO_ROOT / "schema" / "openapi.json"


@dataclasses.dataclass(frozen=True)
class Route:
    """One HTTP route's contract.

    `responses` maps status code → response model (or ``None`` for empty
    body, e.g. 204). Error responses are documented as a generic
    ``ErrorResponse`` shape — the Flask handlers return ``{"error": str,
    "details": list}`` consistently; see ``sanctum/api/_internal.py``.
    """

    method: str
    path: str
    summary: str
    requires_auth: bool
    path_params: tuple[str, ...]
    request_body: type[BaseModel] | None
    responses: dict[int, type[BaseModel] | _BinaryResponse | None]


# Error shape used across the API for 4xx / 5xx responses. Kept here
# rather than under ``sanctum/api/schemas.py`` because it's a
# documentation-only envelope — the runtime returns plain dicts.
class _ErrorResponse(BaseModel):
    error: str
    details: list[dict[str, Any]] | None = None


@dataclasses.dataclass(frozen=True)
class _BinaryResponse:
    """Sentinel for routes that emit a binary (non-JSON) success body.

    Lets ``GET /review-sessions/{id}/input`` etc. document a content
    type without inventing a Pydantic model. The schema rendered for
    this content is OpenAPI's standard binary form (``string`` /
    ``binary``), and ``content_type`` is what the route actually sets
    on the ``Content-Type`` header.
    """

    content_type: str
    description: str = "Binary response"


# Path parameter names (as they appear in Flask ``<name>`` placeholders)
# are declared once per route so we can emit `parameters:` entries even
# for routes with no request body.
ROUTES: list[Route] = [
    Route(
        method="get",
        path="/health",
        summary="Liveness probe + mapping-store state + build pin.",
        requires_auth=False,
        path_params=(),
        request_body=None,
        responses={200: HealthResponse},
    ),
    Route(
        method="post",
        path="/analyze",
        summary="Detect PII entities in free text.",
        requires_auth=True,
        path_params=(),
        request_body=AnalyzeRequest,
        responses={
            200: AnalyzeResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            500: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/anonymize",
        summary="Detect + anonymize PII in free text.",
        requires_auth=True,
        path_params=(),
        request_body=AnonymizeRequest,
        responses={
            200: AnonymizeResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            409: _ErrorResponse,
            500: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/process-file",
        summary="Process a document — fire-and-forget or staged review.",
        requires_auth=True,
        path_params=(),
        request_body=ProcessFileRequest,
        responses={
            # `review=false` → 200 + ProcessFileResponse.
            # `review=true`  → 201 + ProcessFileReviewResponse.
            200: ProcessFileResponse,
            201: ProcessFileReviewResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            413: _ErrorResponse,
            415: _ErrorResponse,
            500: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/mapping/unlock",
        summary="Unlock the encrypted pseudonym mapping store.",
        requires_auth=True,
        path_params=(),
        request_body=UnlockMappingRequest,
        responses={
            200: UnlockMappingResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            409: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/mapping/lock",
        summary="Re-encrypt the mapping store and drop it from memory.",
        requires_auth=True,
        path_params=(),
        request_body=None,
        responses={
            200: LockMappingResponse,
            500: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/mapping/reverse",
        summary="Look up the original value behind a pseudonym.",
        requires_auth=True,
        path_params=(),
        request_body=ReverseMappingRequest,
        responses={
            200: ReverseMappingResponse,
            401: _ErrorResponse,
            404: _ErrorResponse,
            409: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/mapping/rotate-key",
        summary="Re-encrypt the mapping store under a new passphrase.",
        requires_auth=True,
        path_params=(),
        request_body=RotateMappingKeyRequest,
        responses={
            200: RotateMappingKeyResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            409: _ErrorResponse,
            500: _ErrorResponse,
        },
    ),
    Route(
        method="get",
        path="/review-sessions",
        summary="List persisted review sessions (newest first).",
        requires_auth=True,
        path_params=(),
        request_body=None,
        responses={
            200: ReviewSessionListResponse,
            401: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/review-sessions",
        summary="Create a review session from a document.",
        requires_auth=True,
        path_params=(),
        request_body=CreateReviewSessionRequest,
        responses={
            201: ReviewSessionResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            415: _ErrorResponse,
            500: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="get",
        path="/review-sessions/{session_id}",
        summary="Fetch a review session — segments, proposals, previews.",
        requires_auth=True,
        path_params=("session_id",),
        request_body=None,
        responses={
            200: ReviewSessionResponse,
            401: _ErrorResponse,
            404: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="get",
        path="/review-sessions/{session_id}/input",
        summary="Fetch the original input bytes for an open session.",
        requires_auth=True,
        path_params=("session_id",),
        request_body=None,
        responses={
            200: _BinaryResponse(
                # The four supported document formats share this slot —
                # the runtime sets Content-Type per session.format. The
                # spec lists docx as the canonical advertised type since
                # it's the only format wired end-to-end today; the
                # runtime is forward-compatible with the rest.
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                description="Document bytes — Content-Type matches session.format.",
            ),
            401: _ErrorResponse,
            404: _ErrorResponse,
            410: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="patch",
        path="/review-sessions/{session_id}/decisions/{proposal_id}",
        summary="Accept or reject a proposal; override operator / params.",
        requires_auth=True,
        path_params=("session_id", "proposal_id"),
        request_body=PatchProposalDecisionRequest,
        responses={
            200: DecisionWithPreviewResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            404: _ErrorResponse,
            409: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/review-sessions/{session_id}/decisions/user-added",
        summary="Flag a span Sanctum missed; same operator-fallback chain.",
        requires_auth=True,
        path_params=("session_id",),
        request_body=AddUserAddedDecisionRequest,
        responses={
            201: DecisionWithPreviewResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            404: _ErrorResponse,
            409: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="delete",
        path="/review-sessions/{session_id}/decisions/user-added/{ua_id}",
        summary="Remove a previously-added user decision.",
        requires_auth=True,
        path_params=("session_id", "ua_id"),
        request_body=None,
        responses={
            204: None,
            401: _ErrorResponse,
            404: _ErrorResponse,
            409: _ErrorResponse,
        },
    ),
    Route(
        method="post",
        path="/review-sessions/{session_id}/commit",
        summary="Finalize the session — write the anonymized document.",
        requires_auth=True,
        path_params=("session_id",),
        request_body=CommitReviewSessionRequest,
        responses={
            200: CommitReviewSessionResponse,
            400: _ErrorResponse,
            401: _ErrorResponse,
            404: _ErrorResponse,
            409: _ErrorResponse,
            500: _ErrorResponse,
            503: _ErrorResponse,
        },
    ),
    Route(
        method="delete",
        path="/review-sessions/{session_id}",
        summary="Abandon a session — deletes input bytes + plaintext proposals.",
        requires_auth=True,
        path_params=("session_id",),
        request_body=None,
        responses={
            204: None,
            401: _ErrorResponse,
            404: _ErrorResponse,
            409: _ErrorResponse,
        },
    ),
]


def _collect_models() -> list[type[BaseModel]]:
    """Unique models referenced by any route (request or response)."""
    seen: dict[str, type[BaseModel]] = {}
    for r in ROUTES:
        if r.request_body is not None:
            seen[r.request_body.__name__] = r.request_body
        for m in r.responses.values():
            # Skip both ``None`` (empty body, e.g. 204) and binary
            # response sentinels — neither contributes a JSON model
            # to the components pool.
            if isinstance(m, type) and issubclass(m, BaseModel):
                seen[m.__name__] = m
    # Deterministic ordering for stable diffs.
    return [seen[k] for k in sorted(seen)]


def _build_components(models: list[type[BaseModel]]) -> dict[str, Any]:
    """Merge each model's ``model_json_schema`` into a flat component pool.

    Pydantic nests sub-models under ``$defs``; we lift those into the
    same ``components.schemas`` table so every reference resolves.
    """
    schemas: dict[str, Any] = {}
    for model in models:
        s = model.model_json_schema(ref_template=REF_TEMPLATE)
        defs = s.pop("$defs", {})
        schemas[model.__name__] = s
        for name, sub in defs.items():
            # Later-seen duplicates should be identical; assert to catch drift.
            if name in schemas and schemas[name] != sub:
                raise ValueError(
                    f"conflicting schema for {name!r} across models — "
                    "Pydantic produced two different $defs entries"
                )
            schemas[name] = sub
    return {
        "schemas": dict(sorted(schemas.items())),
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "Loopback-only bearer token. The CLI's `sanctum serve` "
                    "writes the token to `~/.sanctum/api-token` (0600 perms); "
                    "the Phase 3 desktop app passes it via `--token-stdin`."
                ),
            },
        },
    }


def _route_operation(r: Route) -> dict[str, Any]:
    op: dict[str, Any] = {
        "summary": r.summary,
        "responses": {},
    }
    if r.requires_auth:
        op["security"] = [{"bearerAuth": []}]
    if r.path_params:
        op["parameters"] = [
            {
                "name": p,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
            for p in r.path_params
        ]
    if r.request_body is not None:
        op["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": REF_TEMPLATE.format(model=r.request_body.__name__)},
                },
            },
        }
    for status, model in r.responses.items():
        response: dict[str, Any] = {"description": _status_description(status)}
        if isinstance(model, _BinaryResponse):
            response["description"] = model.description
            response["content"] = {
                model.content_type: {
                    "schema": {"type": "string", "format": "binary"},
                },
            }
        elif model is not None:
            response["content"] = {
                "application/json": {
                    "schema": {"$ref": REF_TEMPLATE.format(model=model.__name__)},
                },
            }
        op["responses"][str(status)] = response
    # Sort response keys for stable output.
    op["responses"] = dict(sorted(op["responses"].items()))
    return op


_STATUS_DESCRIPTIONS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    409: "Conflict",
    410: "Gone",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def _status_description(code: int) -> str:
    return _STATUS_DESCRIPTIONS.get(code, f"HTTP {code}")


def _build_paths() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for r in ROUTES:
        paths.setdefault(r.path, {})
        paths[r.path][r.method] = _route_operation(r)
    return dict(sorted(paths.items()))


def build() -> dict[str, Any]:
    models = _collect_models()
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Sanctum API",
            "version": __version__,
            "description": (
                "Loopback-only HTTP API for the Sanctum Python backend. "
                "Consumed by the `sanctum-desktop` Electron app and the "
                "`sanctum` CLI. See `plans/phase-3-desktop-ui.md` WS1."
            ),
        },
        "paths": _build_paths(),
        "components": _build_components(models),
    }


def main() -> None:
    spec = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(spec, indent=2, sort_keys=False) + "\n")
    print(f"wrote {OUTPUT.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    main()
