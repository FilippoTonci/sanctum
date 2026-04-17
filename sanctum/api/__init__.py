"""Flask localhost API — thin REST wrapper over the SanctumEngine.

The API binds to `127.0.0.1` only; see `sanctum.api.auth` for the bearer-token
+ Host/Origin-allowlist stack that prevents DNS-rebinding and cross-origin
abuse from a malicious local browser context.
"""

from typing import Final

MAX_INPUT_BYTES: Final[int] = 50 * 1024 * 1024
"""Hard cap on input-document size *and* WSGI request bodies.

Lives at the package root because both the app factory (as
``MAX_CONTENT_LENGTH``) and ``/process-file`` (as a per-file size check)
need it. Keeping it here avoids the upward sibling-import it used to do
from ``routes/pipeline`` into ``app``.
"""

__all__ = ["MAX_INPUT_BYTES"]
