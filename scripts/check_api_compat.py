"""Fail CI when the API's OpenAPI spec breaks a committed contract.

The Phase 3 desktop app (`sanctum-desktop`) generates its typed HTTP
client from `schema/openapi.json`. Because the atomic-installer model
pins each desktop release to a specific `sanctum` commit, a contract
break in `sanctum` doesn't silently ship to installed users — but it
does mean the desktop repo's pin has to be bumped in lockstep. This
script flags those moments so the PR author bumps deliberately rather
than by surprise.

What counts as a break (exits 1):

- A route that existed in the baseline is gone in the current spec.
- A schema that existed in the baseline is gone in the current spec.
- A property that existed on a baseline schema is gone in the current
  schema.
- A request schema gained a *required* property that was not in the
  baseline (old clients will 400 on it).

What does not count (exits 0, silently allowed):

- New routes, new schemas, new optional properties, new response
  fields, new status codes. These extend the contract without breaking
  existing consumers.

Usage::

    python scripts/check_api_compat.py \\
        --base /tmp/baseline.json \\
        --current schema/openapi.json

The script does not run git itself; CI is responsible for materializing
the baseline spec from `main` (or from the merge base) into a temp
file. Keeping git out of the script keeps it testable with plain JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _route_keys(spec: dict[str, Any]) -> set[tuple[str, str]]:
    """Return a set of ``(path, method)`` tuples for every documented route."""
    out: set[tuple[str, str]] = set()
    for path, methods in spec.get("paths", {}).items():
        for method in methods:
            out.add((path, method.lower()))
    return out


def _schemas(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return spec.get("components", {}).get("schemas", {}) or {}


def _properties(schema: dict[str, Any]) -> dict[str, Any]:
    return schema.get("properties", {}) or {}


def _required(schema: dict[str, Any]) -> set[str]:
    return set(schema.get("required", []) or [])


def find_breaks(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Return a list of human-readable break descriptions. Empty = compatible."""
    breaks: list[str] = []

    # 1. Removed routes.
    base_routes = _route_keys(baseline)
    cur_routes = _route_keys(current)
    for path, method in sorted(base_routes - cur_routes):
        breaks.append(f"route removed: {method.upper()} {path}")

    # 2. Schema-level checks.
    base_schemas = _schemas(baseline)
    cur_schemas = _schemas(current)
    for name in sorted(set(base_schemas) - set(cur_schemas)):
        breaks.append(f"schema removed: {name}")

    for name in sorted(set(base_schemas) & set(cur_schemas)):
        base_props = _properties(base_schemas[name])
        cur_props = _properties(cur_schemas[name])

        # 3. Properties that existed are gone.
        for prop in sorted(set(base_props) - set(cur_props)):
            breaks.append(f"property removed: {name}.{prop}")

        # 4. New required properties. We only flag this on request
        # schemas — "Request" suffix is the project convention (see
        # sanctum/api/schemas.py). For response schemas, narrowing
        # required would be caught by "property removed"; widening
        # required is additive. Being conservative: flag newly-required
        # props only on Request types to avoid false positives.
        if name.endswith("Request"):
            base_required = _required(base_schemas[name])
            cur_required = _required(cur_schemas[name])
            # Only "new required" — a prop that was optional before and
            # is required now, OR a prop that didn't exist before and
            # is required now. "Existed optional → required" is strictly
            # worse for old clients.
            newly_required = cur_required - base_required
            for prop in sorted(newly_required):
                breaks.append(f"new required request field: {name}.{prop}")

    return breaks


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument(
        "--base",
        type=Path,
        required=True,
        help="Path to the baseline openapi.json (usually main's version).",
    )
    p.add_argument(
        "--current",
        type=Path,
        default=Path("schema/openapi.json"),
        help="Path to the current branch's openapi.json. Default: schema/openapi.json.",
    )
    args = p.parse_args()

    if not args.base.is_file():
        print(f"baseline spec not found: {args.base}", file=sys.stderr)
        return 2
    if not args.current.is_file():
        print(f"current spec not found: {args.current}", file=sys.stderr)
        return 2

    breaks = find_breaks(_load(args.base), _load(args.current))
    if not breaks:
        print("API contract unchanged or strictly extended — no breaking changes.")
        return 0

    print("BREAKING API changes detected vs baseline:", file=sys.stderr)
    for b in breaks:
        print(f"  - {b}", file=sys.stderr)
    print(
        "\nEither revert the change, or coordinate a lockstep bump of the "
        "sanctum commit pinned by sanctum-desktop. See plans/phase-3-desktop-ui.md WS1.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
