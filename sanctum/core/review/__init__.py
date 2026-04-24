"""Review-session domain — state machine, proposal builder, local persistence.

Phase 1.5 WS2 split (issue #16): the server-owned review flow lives
here; native-comment interop for Word stays in ``sanctum.documents`` for
WS5. The core-layer purity rule still applies — stdlib + Pydantic only,
no Presidio, no Office libs.
"""

from sanctum.core.review.identifiers import make_detection_id
from sanctum.core.review.proposals import build_proposals
from sanctum.core.review.session import abandon, add_decision, commit
from sanctum.core.review.store import SessionStore

__all__ = [
    "SessionStore",
    "abandon",
    "add_decision",
    "build_proposals",
    "commit",
    "make_detection_id",
]
