"""Read-only tour briefings with Komoot supplies and local timing estimates."""

from datetime import datetime, timezone

from komoot_mcp.briefing_analysis import (
    briefing_schedule, briefing_sections, validate_briefing_timing,
)
from komoot_mcp.context import get_client
from komoot_mcp.route_sections import coordinate_distances
from komoot_mcp.tour_supplies import find_tour_supplies


def register(mcp):
    @mcp.tool()
    async def komoot_get_tour_briefing(
        tour_id: int, corridor_m: int = 300,
        departure_time: str | None = None, pauses: list[dict] | None = None,
    ) -> dict:
        """Read supplies, documented route sections and an estimated pause schedule.

        Searches Komoot map POIs around the route without account credentials.
        corridor_m is a straight-line corridor of 1 to 1000 metres around the route.
        Supply offsets are not walking detours. Access, drinkability and current
        opening hours are unverified. Komoot POI inventories can be incomplete.
        departure_time must be ISO 8601 with a timezone, such as 2026-11-19T09:00:00+01:00.
        pauses contains objects with km, duration_s and optional name. Pauses stay local.
        Times assume uniform distance-based pace and exclude travel off the route.
        This tool does not change the tour, create places or write files.
        """
        if isinstance(corridor_m, bool) or not isinstance(corridor_m, int) or not 1 <= corridor_m <= 1000:
            raise ValueError("corridor_m must be an integer between 1 and 1000")
        departure, stops = validate_briefing_timing(departure_time, pauses)
        tour = await get_client().get_tour_full(tour_id, include_coordinates=True)
        embedded = tour.get("_embedded")
        coordinates = embedded.get("coordinates") if isinstance(embedded, dict) else None
        coordinates = coordinates.get("items") if isinstance(coordinates, dict) else coordinates
        points = coordinates if isinstance(coordinates, list) else []
        distances = coordinate_distances(points) if points else None
        total_km = distances[-1] if distances else None
        # Check pause positions before searching for supply points.
        briefing_schedule(tour, total_km, [], departure, stops)
        supplies = (
            await find_tour_supplies(points, distances, corridor_m)
            if distances is not None and len(points) >= 2 and total_km is not None and total_km > 0
            else {
                "status": "unavailable", "items": [], "source": "Komoot map POIs",
                "fetched_at": None, "issues": ["Route geometry is missing, invalid or has zero length."],
            }
        )
        return {
            "tour_id": tour_id,
            "name": tour.get("name"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tour_source": f"https://www.komoot.com/tour/{tour_id}",
            "total_km": total_km,
            "supplies": supplies,
            "route_analysis": briefing_sections(tour, distances),
            "schedule": briefing_schedule(tour, total_km, supplies["items"], departure, stops),
        }
