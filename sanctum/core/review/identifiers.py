"""Stable, content-addressed ids for detections inside a review session.

Hashing ``entity_type + original + position`` rather than using a
sequence number makes the id reproducible: the same detection in the
same segment produces the same id on every run, so a reviewer action
(PATCH by id) keeps resolving against the same proposal across session
reloads. 12 hex chars gives ~48 bits of collision resistance, which
is plenty for the ~hundreds-of-detections-per-doc regime.
"""

from __future__ import annotations

import hashlib


def make_detection_id(entity_type: str, original: str, position: str | int) -> str:
    """Content-addressed proposal id (12 hex chars)."""
    payload = f"{entity_type}\x00{original}\x00{position}".encode()
    return hashlib.sha1(payload).hexdigest()[:12]
