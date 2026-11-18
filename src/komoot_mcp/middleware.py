"""Fail-closed gateway authentication and per-request Komoot credentials.

HTTP requires INTERNAL_SECRET or GATEWAY_SECRET plus x-user-credentials.
Only /health is public. Stdio alone may use environment credentials.
"""
from __future__ import annotations

import json
import os
import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from komoot_mcp.auth import AuthManager
from komoot_mcp.context import (
    clear_request_state,
    reset_auth_manager,
    set_auth_manager,
    set_http_context,
    reset_http_context,
)




INTERNAL_SECRET_HEADER = "authorization"
BEARER_PREFIX = "Bearer "
USER_CREDENTIALS_HEADER = "x-user-credentials"
HEALTH_PATH = "/health"


class InternalSecretMiddleware:
    """Reject HTTP traffic unless a configured gateway secret matches.

    Accepts two Authorization header formats emitted by the platform gateway:

    * ``Bearer <INTERNAL_SECRET>`` — legacy/direct form.
    * ``Bearer Internal-gateway:<GATEWAY_SECRET>`` — gateway-prefixed form
      (used when ``GATEWAY_SECRET`` is set on the gateway side).

    On rejection, returns a valid JSON-RPC 2.0 error body with code -32001
    (server-defined unauthorized, mirrors Bitrix's -32000 shape).

    Env vars are read at request time so credential rotation works without
    restart.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Read at request time so rotation works without restart.
        secret = os.environ.get("INTERNAL_SECRET")
        gateway_secret = os.environ.get("GATEWAY_SECRET")

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Allow health checks without auth so the orchestrator can probe.
        if scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return

        # Header names are case-insensitive (HTTP/1.1 RFC 7230 §3.2); lowercase
        # them for lookup. The value must match the Bearer prefix case-sensitively
        # to mirror the Bitrix reference (`auth !== \`Bearer ${secret}\``).
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        provided = headers.get(INTERNAL_SECRET_HEADER)

        expected_direct = f"{BEARER_PREFIX}{secret}" if secret else None
        expected_gateway = (
            f"{BEARER_PREFIX}Internal-gateway:{gateway_secret}"
            if gateway_secret
            else None
        )

        valid = isinstance(provided, str) and any(
            expected is not None and hmac.compare_digest(provided.encode(), expected.encode())
            for expected in (expected_direct, expected_gateway)
        )
        if not valid:
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32001,
                        "message": "unauthorized: missing or invalid Authorization header",
                    },
                },
                status_code=401,
                media_type="application/json",
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class UserCredentialsMiddleware:
    """Extract ``x-user-credentials`` and install it in the ContextVar."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Health endpoint never needs creds — skip the parse work.
        if scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        try:
            creds = json.loads(request.headers.get(USER_CREDENTIALS_HEADER, "null"))
        except json.JSONDecodeError:
            creds = None
        if not isinstance(creds, dict) or not all(
            isinstance(creds.get(key), str) and creds[key]
            for key in ("email", "password")
        ):
            response = JSONResponse(
                {"error": "Valid per-request x-user-credentials are required"},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        clear_request_state()
        http_token = set_http_context()
        auth_token = set_auth_manager(AuthManager(email=creds["email"], password=creds["password"]))

        try:
            await self.app(scope, receive, send)
        finally:
            # Always reset — never let one tenant's creds outlive the request.
            if auth_token is not None:
                try:
                    reset_auth_manager(auth_token)
                except (ValueError, LookupError):
                    # ContextVar reset can fail across task boundaries; fall
                    # back to a hard clear so we don't leak state.
                    clear_request_state()
            # Belt-and-braces: also drop the lazily-built KomootClient so the
            # next request doesn't see a stale instance bound to old creds.
            clear_request_state()
            reset_http_context(http_token)
