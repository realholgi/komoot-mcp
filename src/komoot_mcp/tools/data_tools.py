"""Tour data tools for komoot-mcp."""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_get_tour_coordinates(tour_id: int) -> dict:
        """Get the coordinate array (lat, lng, altitude) for a tour.

        Args:
            tour_id: The numeric tour ID
        """
        return {"tour_id": tour_id, "coordinates": await get_client().get_tour_coordinates(tour_id)}

    @mcp.tool()
    async def komoot_get_tour_gpx(tour_id: int) -> dict:
        """Return a tour's GPX content inline in the response.

        gpx_content contains the complete XML so callers can save or forward
        it without access to the server filesystem. size_bytes reports the
        UTF-8 byte count; truncated is always false.

        Args:
            tour_id: The numeric tour ID
        """
        gpx = await get_client().get_tour_gpx(tour_id)
        return {"tour_id": tour_id, "gpx_content": gpx, "size_bytes": len(gpx.encode("utf-8")), "truncated": False}

    @mcp.tool()
    async def komoot_get_tour_directions(tour_id: int) -> dict:
        """Get turn-by-turn directions for a tour."""
        return {"tour_id": tour_id, "directions": await get_client().get_tour_directions(tour_id)}

    @mcp.tool()
    async def komoot_get_tour_way_types(tour_id: int) -> dict:
        """Get the way type breakdown for a tour (road, trail, path percentages)."""
        return {"tour_id": tour_id, "way_types": await get_client().get_tour_way_types(tour_id)}

    @mcp.tool()
    async def komoot_get_tour_surfaces(tour_id: int) -> dict:
        """Get the surface breakdown for a tour (paved, gravel, trail percentages)."""
        return {"tour_id": tour_id, "surfaces": await get_client().get_tour_surfaces(tour_id)}

    @mcp.tool()
    async def komoot_get_tour_timeline(tour_id: int) -> dict:
        """Get the event timeline for a tour."""
        return {"tour_id": tour_id, "timeline": await get_client().get_tour_timeline(tour_id)}
