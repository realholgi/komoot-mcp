"""Routing tools for komoot-mcp."""

import asyncio

from komoot_mcp.context import get_client, get_geocoder
from komoot_mcp.routing import (
    native_sport, plan_native_route, saved_plan_result, validate_coordinates,
)


def register(mcp):
    @mcp.tool()
    async def komoot_geocode(
        query: str, limit: int = 5, lang: str | None = None,
        near_lat: float | None = None, near_lng: float | None = None,
    ) -> dict:
        """Find places using Photon, or reverse-geocode a 'lat,lng' query.

        limit sets the maximum number of forward results. lang sets the
        result language; near_lat and near_lng bias forward results.
        Returns the full location records, without display formatting.
        """
        geocoder = get_geocoder()
        coords = _parse_coords(query)
        if coords is not None:
            result = await asyncio.to_thread(geocoder.reverse, *coords)
            return {"query": query, "results": [result]}
        options = {"lang": lang, "near_lat": near_lat, "near_lng": near_lng}
        results = await asyncio.to_thread(
            geocoder.forward, query, limit,
            **{key: value for key, value in options.items() if value is not None},
        )
        return {"query": query, "results": results}

    @mcp.tool()
    async def komoot_plan_and_upload(
        start: str, end: str | None = None, roundtrip: bool = False,
        sport: str = "hike", waypoints: str | None = None,
        tour_name: str | None = None, tour_status: str = "private",
        constitution: int = 3,
    ) -> dict:
        """Plan from place names or 'lat,lng' and save a planned tour.

        Creates a Komoot tour, private by default. tour_status accepts private,
        friends, or public. The result reports requested visibility, not a
        verified readback. constitution is integer fitness from 1 to 5.

        sport accepts hike, trail_run, mountain_bike, road_cycle, gravel_ride.
        Gravel uses touringbicycle. Native aliases are mtb, mountainbike,
        touringbicycle, racebike, e_racebike, e_mtb, jogging, nordicwalking,
        and e_touringbicycle. Unknown names are rejected before network access.

        waypoints contains ordered 'lat,lng' pairs separated by '|'.
        roundtrip appends the start after the end (if supplied) and waypoints.
        Without an end, roundtrip needs at least one waypoint. This is an
        out-and-back, not a target-distance loop generator.
        """
        profile = native_sport(sport)
        if isinstance(constitution, bool) or not isinstance(constitution, int) or not 1 <= constitution <= 5:
            raise ValueError("constitution must be an integer between 1 and 5")
        if tour_status not in {"private", "friends", "public"}:
            raise ValueError("tour_status must be private, friends, or public")
        if not end and not roundtrip:
            raise ValueError("Provide an end location or roundtrip=true")
        vias = []
        if waypoints:
            for text in waypoints.split("|"):
                coords = _parse_coords(text.strip())
                if coords is None:
                    raise ValueError(f"Invalid waypoint coordinates: {text}")
                vias.append(coords)
        if roundtrip and not end and not vias:
            raise ValueError("Roundtrip needs waypoints to avoid a zero-distance route")
        # Validate all explicit coordinates before any geocoding request.
        start_coords = _parse_coords(start)
        end_coords = _parse_coords(end) if end else None
        geocoder = get_geocoder()
        if start_coords is None:
            start_coords = await _geocode_location(start, geocoder)
        if end and end_coords is None:
            end_coords = await _geocode_location(end, geocoder)
        points = [start_coords, *vias]
        if end_coords is not None:
            points.append(end_coords)
        elif roundtrip:
            points.append(start_coords)
        client = get_client()
        route = await plan_native_route(client, points, sport, constitution, roundtrip)
        saved = await client.save_planned_tour(
            route_response=route, name=tour_name or f"Planned {sport} route",
            status=tour_status,
        )
        return {
            **saved_plan_result(saved, route, tour_status),
            "sport": sport, "native_sport": profile,
        }


async def _geocode_location(text, geocoder):
    results = await asyncio.to_thread(geocoder.forward, text, limit=1)
    if not results:
        raise ValueError(f"Could not geocode location: {text}")
    return validate_coordinates([[results[0]["lat"], results[0]["lon"]]])[0]


def _parse_coords(text):
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        coords = [float(part.strip()) for part in parts]
    except ValueError:
        return None
    return validate_coordinates([coords])[0]
