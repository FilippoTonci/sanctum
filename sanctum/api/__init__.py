"""Flask localhost API — thin REST wrapper over the SanctumEngine.

The API binds to `127.0.0.1` only; see `sanctum.api.auth` for the bearer-token
+ Host/Origin-allowlist stack that prevents DNS-rebinding and cross-origin
abuse from a malicious local browser context.
"""
