"""Read-only place and full-route search for the logged-in user's tours."""

import asyncio
import math

from komoot_mcp.context import get_client, get_geocoder, get_rate_limiter
from komoot_mcp.nearby_tours import search_tours
from komoot_mcp.route_geometry import coordinate
from komoot_mcp.routing import validate_coordinates


def register(mcp):
    @mcp.tool()
    async def komoot_find_my_tours_near(
        query: str | None = None, center: list[float] | None = None,
        radius_m: float = 5000, tour_type: str | None = "tour_planned",
        sport_type: str | None = None, limit: int = 20,
        max_tours: int = 200, offset: int = 0,
    ) -> dict:
        """Find your tours near a place along the full route, including start and end.

        Supply either a place query or center [lat, lng]. Photon resolves names.
        Ambiguous names return place candidates without scanning the account.
        Call again with the chosen center. No tour-name filter is applied.
        radius_m is a positive straight-line distance, not a walking detour.
        Keep the default 5000 metres unless the user specifies a distance.
        A place can describe the wider area even when absent from the tour title.
        tour_type defaults to tour_planned; null includes planned and recorded tours.
        Each scanned tour needs a coordinate request. max_tours limits scanned
        records to 1–1000 (default 200). offset resumes at scan.next_offset.
        limit bounds returned matches to 1–100, not the number of scanned tours.
        Results rank by nearest route-segment distance within the scanned slice.
        Partial scans and unavailable geometry do not prove a tour is absent.
        The tool changes no account data or files.
        """
        if (query is None) == (center is None):
            raise ValueError("Supply exactly one of query or center")
        if query is not None and not query.strip():
            raise ValueError("query must not be empty")
        if isinstance(radius_m, bool) or not isinstance(radius_m, (int, float)) or not math.isfinite(radius_m) or radius_m <= 0:
            raise ValueError("radius_m must be finite and greater than zero")
        if tour_type not in (None, "tour_planned", "tour_recorded"):
            raise ValueError("tour_type must be tour_planned, tour_recorded or null")
        for name, value, maximum in (("limit", limit, 100), ("max_tours", max_tours, 1000)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        place = None
        if center is not None:
            search_center = validate_coordinates([center])[0]
        else:
            assert query is not None
            await get_rate_limiter().acquire()
            candidates = await asyncio.to_thread(get_geocoder().forward, query, limit=5)
            locations = {}
            for candidate in candidates:
                position = coordinate({"lat": candidate.get("lat"), "lng": candidate.get("lon")})
                if position is not None:
                    locations.setdefault(position, candidate)
            if len(locations) != 1:
                return {
                    "status": "ambiguous_place" if locations else "place_not_found",
                    "query": query, "places": candidates, "tours": [],
                    "scan": {"coverage": "not_started", "scanned_records": 0},
                }
            search_center, place = next(iter(locations.items()))
        result = await search_tours(
            get_client(), search_center, radius_m, tour_type, sport_type, limit, max_tours, offset,
        )
        return {**result, "query": query, "place": place}
