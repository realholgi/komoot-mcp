"""Highlight (POI) tools for komoot-mcp.

Phase 3: lightweight standalone tools for highlight sub-resources.
Phase 2's ``komoot_get_highlight`` (in ``browse_tools``) hits the
umbrella endpoint and optionally embeds tips/recommenders. The tools
below are intentionally narrower — useful when the caller already has
a highlight id and wants just the images, or just the tips, without
the metadata round-trip.
"""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_get_highlight_images(
        highlight_id: int, page: int = 0,
    ) -> dict:
        """Get photos attached to a Komoot highlight (POI).

        Example: ``komoot_get_highlight_images(highlight_id=15829,
        page=0)``.

        Args:
            highlight_id: The numeric highlight ID
            page: Page number (0-indexed)
        """
        return await get_client().get_highlight_images(highlight_id, page=page)

    @mcp.tool()
    async def komoot_get_highlight_tips(
        highlight_id: int, page: int = 0,
    ) -> dict:
        """Get community tips for a Komoot highlight.

        Lighter alternative to ``komoot_get_highlight(include_tips=True)``
        — only the tips, no metadata round-trip.

        Example: ``komoot_get_highlight_tips(highlight_id=15829,
        page=0)``.

        Args:
            highlight_id: The numeric highlight ID
            page: Page number (0-indexed)
        """
        return await get_client().get_highlight_tips(highlight_id, page=page)
