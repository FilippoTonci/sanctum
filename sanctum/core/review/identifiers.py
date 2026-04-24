"""Stable, content-addressed ids for detections inside a review session.

Both code paths that need a proposal id — the session surface
(``sanctum.core.review.proposals``) and the native-comment export
(``sanctum.documents.review``) — derive it the same way, so a document
reviewed via the UI and later exported to Word carries matching ids on
both sides.

Hashing ``entity_type + original + position`` rather than using a
sequence number means the id survives Word's comment-id renumbering on
copy-paste — the same detection in the same segment produces the same
id on every run. 12 hex chars gives ~48 bits of collision resistance,
which is plenty for the ~hundreds-of-detections-per-doc regime.
"""

from __future__ import annotations

import hashlib


def make_detection_id(entity_type: str, original: str, position: str | int) -> str:
    """Content-addressed proposal id (12 hex chars)."""
    payload = f"{entity_type}\x00{original}\x00{position}".encode()
    return hashlib.sha1(payload).hexdigest()[:12]
