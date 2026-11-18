"""Smoke tests for the tool handlers with kompy mocked out.

These exercise the multi-tenant plumbing end-to-end at the tool layer:
- AuthManager is resolved from the ContextVar.
- Two installed AuthManagers don't bleed across requests.
- The login + list_tours + user_profile happy paths return semantic dictionaries.

The installed kompy connector is patched per test; no live API is contacted.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import kompy
import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootClient
from komoot_mcp.context import (
    clear_request_state,
    get_auth_manager,
    set_auth_manager,
    reset_auth_manager,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(KomootClient, "_basic_auth", lambda self: (self.auth.email, "test-token"))

    async def get_tours(self, url, **kwargs):
        email = url.split("/users/")[1].split("/")[0]
        return {"_embedded": {"tours": [{"id": 1, "name": f"tour-for-{email}"}]}}

    monkeypatch.setattr(KomootClient, "_komoot_get", get_tours)
    clear_request_state()
    yield
    clear_request_state()


class TestPerRequestAuthManager:
    @pytest.mark.asyncio
    async def test_client_uses_contextvar_auth(self):
        """The client built from ContextVar AM hits the right kompy creds."""
        am = AuthManager(email="alice@x.com", password="pw")
        token = set_auth_manager(am)
        try:
            from komoot_mcp.context import get_client
            client = get_client()
            assert client.auth.email == "alice@x.com"
            result = await client.list_tours()
            assert result["tours"][0]["name"] == "tour-for-alice@x.com"
        finally:
            reset_auth_manager(token)

    @pytest.mark.asyncio
    async def test_two_scopes_get_different_clients(self):
        """Two contexts must build clients pinned to their own AM."""
        import contextvars

        async def run_as(email):
            ctx = contextvars.copy_context()
            async def inner():
                am = AuthManager(email=email, password="pw")
                set_auth_manager(am)
                from komoot_mcp.context import get_client
                client = get_client()
                result = await client.list_tours()
                return result["tours"][0]["name"]
            # Run in a copied context so changes don't leak.
            import asyncio
            return await asyncio.create_task(inner(), context=ctx)

        # Run sequentially in distinct contexts. Each should see its email.
        a = await run_as("alice@x.com")
        b = await run_as("bob@x.com")
        assert a == "tour-for-alice@x.com"
        assert b == "tour-for-bob@x.com"

    @pytest.mark.asyncio
    async def test_list_tours_tool_returns_data(self):
        """The actual tool handler returns semantic tour data."""
        # Build a minimal "FastMCP-like" recorder.
        registered: dict[str, callable] = {}

        class _Mcp:
            def tool(self):
                def decorator(fn):
                    registered[fn.__name__] = fn
                    return fn
                return decorator

        from komoot_mcp.tools import browse_tools
        browse_tools.register(_Mcp())

        am = AuthManager(email="carol@x.com", password="pw")
        token = set_auth_manager(am)
        try:
            out = await registered["komoot_list_tours"]()
            assert out["tours"] == [{"id": 1, "name": "tour-for-carol@x.com"}]
        finally:
            reset_auth_manager(token)

    @pytest.mark.asyncio
    async def test_login_tool_uses_contextvar_auth(self):
        """komoot_login should drive the ContextVar AM, not a global one."""
        registered: dict[str, callable] = {}

        class _Mcp:
            def tool(self):
                def decorator(fn):
                    registered[fn.__name__] = fn
                    return fn
                return decorator

        from komoot_mcp.tools import auth_tools
        auth_tools.register(_Mcp())

        am = AuthManager(email="dave@x.com", password="pw")
        # Force "already authenticated" path so we don't hit network.
        am.user_id = "dave"
        am.token = "tok"
        token = set_auth_manager(am)
        try:
            out = await registered["komoot_login"]()
            assert out == {"authenticated": True, "user_id": "dave", "already_authenticated": True}
        finally:
            reset_auth_manager(token)

    @pytest.mark.asyncio
    async def test_user_profile_display_falls_back_when_username_unknown(self):
        """When kompy returns the literal 'unknown' placeholder for the
        display name, both the profile and tool data fall back to the email."""
        registered: dict[str, callable] = {}

        class _Mcp:
            def tool(self):
                def decorator(fn):
                    registered[fn.__name__] = fn
                    return fn
                return decorator

        from komoot_mcp.tools import browse_tools
        browse_tools.register(_Mcp())

        # Drop in a connector whose authentication returns the literal
        # "unknown" username — the placeholder kompy hands back when
        # Komoot's login response omits a display name.
        class _UnknownAuth:
            def get_username(self):
                return "unknown"

            def get_email_address(self):
                return "frank@x.com"

        class _UnknownConnector:
            def __init__(self, email, password):
                self.authentication = _UnknownAuth()

        am = AuthManager(email="frank@x.com", password="pw")
        token = set_auth_manager(am)
        try:
            with patch.object(kompy, "KomootConnector", _UnknownConnector):
                from komoot_mcp.context import get_client

                # Direct client check: dict carries the fallback explicitly.
                profile = await get_client().get_user_profile()
                assert profile["display_name"] == "frank@x.com"
                assert profile["username"] == "unknown"
                assert profile["email"] == "frank@x.com"

                # Tool-handler data carries the same display-name fallback.
                out = await registered["komoot_get_user_profile"]()
            assert out["display_name"] == "frank@x.com"
            assert out["email"] == "frank@x.com"
        finally:
            reset_auth_manager(token)

    @pytest.mark.asyncio
    async def test_user_profile_tool_no_silent_swallow(self):
        """get_user_profile should propagate kompy errors via the handler."""
        registered: dict[str, callable] = {}

        class _Mcp:
            def tool(self):
                def decorator(fn):
                    registered[fn.__name__] = fn
                    return fn
                return decorator

        from komoot_mcp.tools import browse_tools
        browse_tools.register(_Mcp())

        am = AuthManager(email="eve@x.com", password="pw")
        token = set_auth_manager(am)
        try:
            # Patch the KomootConnector stub to raise on construction —
            # handler must propagate the error instead of a fallback.
            # Patch on the shared kompy module
            # object so the reference in client.py also sees the change.
            with patch.object(
                kompy, "KomootConnector", side_effect=RuntimeError("boom"),
            ):
                with pytest.raises(RuntimeError, match="boom"):
                    await registered["komoot_get_user_profile"]()
        finally:
            reset_auth_manager(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,method,result", [
    ("komoot_modify_tour", "modify_tour", {"tour_id": 42, "updated": True}),
    ("komoot_delete_tour", "delete_tour", {"tour_id": 42, "deleted": True}),
])
async def test_sdk_boolean_mutations_do_not_report_rejections_as_success(monkeypatch, tool_name, method, result):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from komoot_mcp.client import KomootAPIError
    from komoot_mcp.tools import write_tools

    registered = {}

    class MCP:
        def tool(self):
            def register(fn):
                registered[fn.__name__] = fn
                return fn
            return register

    mutation = AsyncMock(return_value=True)
    monkeypatch.setattr(write_tools, "get_client", lambda: SimpleNamespace(**{method: mutation}))
    write_tools.register(MCP())
    assert await registered[tool_name](42) == result
    mutation.return_value = False
    with pytest.raises(KomootAPIError, match="42"):
        await registered[tool_name](42)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429])
async def test_sdk_error_mapping_preserves_http_status(status):
    from komoot_mcp.client import KomootAPIError

    class NoLimit:
        async def acquire(self):
            return None

    original = RuntimeError(f"HTTP {status}")

    def rejected():
        raise original

    client = KomootClient(AuthManager(email="t@x.com", password="pw"), NoLimit())
    with pytest.raises(KomootAPIError) as caught:
        await client._call(rejected)
    assert caught.value.status_code == status
    assert caught.value.__cause__ is original
