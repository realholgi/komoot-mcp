"""Per-request execution context for komoot-mcp.

Multi-tenancy is achieved with :class:`contextvars.ContextVar` — each
incoming HTTP request runs inside an isolated context so the
``AuthManager`` / ``KomootClient`` it builds never leaks across
tenants. The Starlette middleware in :mod:`komoot_mcp.middleware`
populates the context before the MCP handler runs and resets it
afterwards.

For stdio/local-dev mode (no middleware), the helpers below fall back
to env vars and a process-wide ``RateLimiter`` so behaviour is
unchanged for single-user setups.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from komoot_mcp.auth import AuthManager
    from komoot_mcp.client import KomootClient
from komoot_mcp.rate_limiter import RateLimiter
from komoot_mcp.geocoder import Geocoder


# Per-request state. Tools must NEVER look at module globals — always
# resolve through ``get_*`` helpers so multi-tenant isolation holds.
_auth_var: ContextVar[Optional[AuthManager]] = ContextVar(
    "komoot_auth_manager", default=None
)
_client_var: ContextVar[Optional[KomootClient]] = ContextVar(
    "komoot_client", default=None
)
_http_context: ContextVar[bool] = ContextVar("komoot_http_context", default=False)


def set_http_context():
    return _http_context.set(True)


def reset_http_context(token):
    _http_context.reset(token)


def is_http_context() -> bool:
    """Report the request transport without creating account state."""
    return _http_context.get()


def require_local_filesystem():
    """Filesystem tools are available only to the trusted local stdio client."""
    if _http_context.get():
        raise PermissionError("Server filesystem access is disabled over HTTP; use inline content")


# Shared singletons that don't carry tenant identity. Rate limiting is
# per-process (we throttle the whole server, not per-user). Geocoder
# talks to public APIs with no user state.
_rate_limiter: RateLimiter | None = None
_geocoder: Geocoder | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def get_geocoder() -> Geocoder:
    global _geocoder
    if _geocoder is None:
        _geocoder = Geocoder()
    return _geocoder


def set_auth_manager(auth: AuthManager) -> object:
    """Install an :class:`AuthManager` for the current context.

    Returns a token that can be passed to :func:`reset_auth_manager`
    to undo the change. Middleware MUST reset to avoid leaking creds
    across requests served by the same worker.
    """
    return _auth_var.set(auth)


def reset_auth_manager(token: object) -> None:
    _auth_var.reset(token)  # type: ignore[arg-type]


def get_auth_manager() -> AuthManager:
    """Return the current request's AuthManager.

    Lazily builds one from env vars if none has been installed —
    preserves stdio/local-dev behaviour for single-user setups.
    """
    am = _auth_var.get()
    if am is None:
        if _http_context.get():
            raise RuntimeError("HTTP requests require explicit Komoot credentials")
        from komoot_mcp.auth import AuthManager
        am = AuthManager()
        _auth_var.set(am)
        # The lazy fallback is not reset — stdio mode is single-tenant
        # by design, so we keep the auth for the life of the process.
    return am


def get_client() -> KomootClient:
    """Return the current request's KomootClient, building lazily."""
    c = _client_var.get()
    if c is None:
        from komoot_mcp.client import KomootClient
        c = KomootClient(get_auth_manager(), get_rate_limiter())
        _client_var.set(c)
    return c


def clear_request_state() -> None:
    """Reset auth/client ContextVars to their defaults.

    Middleware calls this in a ``finally`` block to make absolutely
    sure no tenant state is held by the worker after a response.
    """
    _auth_var.set(None)
    _client_var.set(None)
