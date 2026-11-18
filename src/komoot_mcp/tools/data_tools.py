"""Tour data tools for komoot-mcp."""

from typing import Annotated

from pydantic import Field

from komoot_mcp.client import KomootAPIError

from komoot_mcp.context import get_client


COORDINATE_PAGE_SIZE = 500
MAX_COORDINATE_PAGE_SIZE = 1000


def register(mcp):
    @mcp.tool()
    async def komoot_get_tour_coordinates(
        tour_id: int,
        offset: Annotated[int, Field(ge=0, strict=True)] = 0,
        limit: Annotated[int, Field(ge=1, le=MAX_COORDINATE_PAGE_SIZE, strict=True)] = COORDINATE_PAGE_SIZE,
    ) -> dict:
        """Read an ordered page of [latitude, longitude, altitude] coordinates.

        offset starts at zero. limit defaults to 500 and cannot exceed 1000.
        Follow next_offset until it is null. total counts the full coordinate
        array; truncated means more coordinates follow this page.
        Each call fetches the full upstream array before slicing the response.
        Changes to the tour between calls can change page boundaries.
        """
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_COORDINATE_PAGE_SIZE:
            raise ValueError("limit must be an integer between 1 and 1000")
        coordinates = await get_client().get_tour_coordinates(tour_id)
        if not isinstance(coordinates, list):
            raise KomootAPIError("Komoot returned an invalid coordinate array")
        page = coordinates[offset:offset + limit]
        next_offset = offset + len(page) if offset + len(page) < len(coordinates) else None
        return {
            "tour_id": tour_id, "coordinates": page, "offset": offset, "limit": limit,
            "total": len(coordinates), "next_offset": next_offset,
            "truncated": next_offset is not None,
        }

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
