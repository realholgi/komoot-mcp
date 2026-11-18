"""Share-link tools for komoot-mcp.

Komoot's tour share tokens let a tour owner expose a "secret link" to a
non-authenticated viewer. These tools create / revoke share tokens on a
tour you own, and resolve a share URL back to the underlying tour
metadata.
"""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_create_share_link(tour_id: int) -> dict:
        """Create a public share link for a tour.

        Returns the share token + a directly-clickable URL of the shape
        ``https://www.komoot.com/tour/{tour_id}?share_token={token}``.

        Example: ``komoot_create_share_link(tour_id=2614957086)``.

        Args:
            tour_id: The numeric tour ID (must be a tour you own)
        """
        data = await get_client().create_tour_share_link(tour_id)
        token = data.get("token") or data.get("share_token") or data.get("value")
        return {**data, "tour_id": tour_id, "url": f"https://www.komoot.com/tour/{tour_id}?share_token={token}"} if token else {**data, "tour_id": tour_id}

    @mcp.tool()
    async def komoot_revoke_share_link(tour_id: int) -> dict:
        """Revoke the share token for a tour.

        After this, any previously-issued share URL stops working.

        Example: ``komoot_revoke_share_link(tour_id=2614957086)``.

        Args:
            tour_id: The numeric tour ID
        """
        await get_client().revoke_tour_share_link(tour_id)
        return {"tour_id": tour_id, "revoked": True}

    @mcp.tool()
    async def komoot_resolve_share_url(share_url: str) -> dict:
        """Resolve a Komoot share URL to tour metadata.

        Accepts URLs of the shape
        ``https://www.komoot.com/tour/{tour_id}?share_token={t}``.
        When ``share_token`` is present in the URL, the share token is
        used as the auth cap — no login needed.

        Example: ``komoot_resolve_share_url(share_url="https://www.komoot.com/tour/2614957086?share_token=a1b2c3")``.

        Args:
            share_url: The full share URL
        """
        return await get_client().resolve_share_url(share_url)
