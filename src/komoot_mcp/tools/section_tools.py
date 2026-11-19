"""Read-only kilometre section analysis tools."""

from komoot_mcp.context import get_client
from komoot_mcp.route_sections import analyze_tour_sections, validate_range


def register(mcp):
    @mcp.tool()
    async def komoot_analyze_tour_sections(
        tour_id: int, start_km: float = 0, end_km: float | None = None,
    ) -> dict:
        """Read way types, surfaces, and directions for concrete kilometre spans.

        Uses native coordinate-index spans, not whole-tour percentages.
        Distances are geographic distances calculated from route coordinates.
        The requested range is clipped to the route. Native types are kept;
        labels without verified evidence stay null. Missing or conflicting
        spans have unknown coverage. A type alone does not prove that a
        section is stony, technical, safe, or suitable for a specific person.
        Direction fields are unchanged in raw. This tool does not save changes.
        """
        validate_range(start_km, end_km)
        tour = await get_client().get_tour_full(tour_id, include_coordinates=True)
        return {"tour_id": tour_id, **analyze_tour_sections(tour, start_km, end_km)}
