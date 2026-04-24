"""Read-only ``MappingStore`` façade used by review-session previews.

Previews must show the reviewer the pseudonym that *commit* will mint
without actually persisting it — otherwise every keystroke in the UI
would leak a mapping into the encrypted file. ``PreviewMappingStore``
wraps a real store and splits the lookup into two halves:

* ``peek`` first — if a mapping already exists, return it (this is the
  cross-document reuse case and must agree with commit).
* otherwise call ``factory`` and return the value without writing. The
  deterministic-seed path in ``PseudonymizeOperator`` guarantees that
  commit, running ``get_or_create`` against the real store, mints the
  same value on first encounter.

The ``reverse`` and ``peek`` methods pass through so introspection
during preview rendering still behaves. Mutating calls go nowhere.
"""

from __future__ import annotations

from collections.abc import Callable

from sanctum.core.protocols import MappingStore


class PreviewMappingStore:
    """Non-persisting wrapper used by ``compute_preview`` for pseudonymize."""

    def __init__(self, real: MappingStore) -> None:
        self._real = real

    def get_or_create(self, original: str, entity_type: str, factory: Callable[[], str]) -> str:
        existing = self._real.peek(original, entity_type)
        if existing is not None:
            return existing
        return factory()

    def reverse(self, pseudonym: str, entity_type: str) -> str | None:
        return self._real.reverse(pseudonym, entity_type)

    def peek(self, original: str, entity_type: str) -> str | None:
        return self._real.peek(original, entity_type)
