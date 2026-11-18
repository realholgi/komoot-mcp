"""Auth tools for komoot-mcp."""

from komoot_mcp.context import get_auth_manager


def register(mcp):
    @mcp.tool()
    async def komoot_login() -> dict:
        """Log in to Komoot.

        In platform mode the gateway injects per-user credentials via
        the ``x-user-credentials`` header — no environment variables
        needed. In stdio/local mode, ``KOMOOT_EMAIL`` and
        ``KOMOOT_PASSWORD`` env vars are used instead.
        """
        auth_manager = get_auth_manager()
        already_authenticated = auth_manager.is_authenticated()
        if not already_authenticated:
            auth_manager.login()
        return {"authenticated": True, "user_id": auth_manager.get_user_id(), "already_authenticated": already_authenticated}
