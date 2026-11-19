"""Routing tools for komoot-mcp."""

import asyncio

from komoot_mcp.context import get_client, get_geocoder
from komoot_mcp.context import get_rate_limiter
from komoot_mcp.poi_search import find_pois_nearby, resolve_poi_candidate
from komoot_mcp.routing import (
    native_sport, plan_native_route, saved_plan_result, validate_coordinates, validate_waypoint_names,
    RouteType, Avoidance, routing_tags, WaypointDetails, waypoint_metadata,
)


def register(mcp):
    @mcp.tool()
    async def komoot_geocode(
        query: str, limit: int = 5, lang: str | None = None,
        near_lat: float | None = None, near_lng: float | None = None,
        osm_tag: str | None = None,
    ) -> dict:
        """Find places using Photon, or reverse-geocode a 'lat,lng' query.

        limit sets the maximum number of forward results. lang sets the
        result language; near_lat and near_lng bias forward results.
        osm_tag filters forward results by an OSM tag, such as highway:bus_stop
        or amenity:restaurant. It is not supported for coordinate queries.
        Returns the full location records, without display formatting.
        """
        geocoder = get_geocoder()
        coords = _parse_coords(query)
        if coords is not None:
            if osm_tag is not None:
                raise ValueError("osm_tag is only supported for forward place queries, not coordinates")
            result = await asyncio.to_thread(geocoder.reverse, *coords)
            return {"query": query, "results": [result]}
        options = {"lang": lang, "near_lat": near_lat, "near_lng": near_lng, "osm_tag": osm_tag}
        results = await asyncio.to_thread(
            geocoder.forward, query, limit,
            **{key: value for key, value in options.items() if value is not None},
        )
        return {"query": query, "results": results}

    @mcp.tool()
    async def komoot_find_pois_nearby(
        lat: float, lng: float, radius_m: int = 100, limit: int = 20, lang: str = "de",
    ) -> dict:
        """Find ordinary map POIs, such as bus stops, near coordinates.

        Use komoot_geocode to find coordinates by name first. Results include
        genuine Komoot poi_id values and waypoint_details for route planning.
        Match osm_id to Photon osm_type + osm_id (N/369831410 becomes n369831410).
        Some results have no poi_id and cannot be linked as a Komoot POI.
        These are map POIs, not community highlights. No account access.
        radius_m accepts 1–5000 metres; limit accepts 1–100.
        """
        await get_rate_limiter().acquire()
        return await asyncio.to_thread(find_pois_nearby, lat, lng, radius_m, limit, lang)

    @mcp.tool()
    async def komoot_resolve_place(
        query: str, limit: int = 5, lang: str = "de",
        near_lat: float | None = None, near_lng: float | None = None,
        osm_tag: str | None = None,
    ) -> dict:
        """Find places with Photon and resolve their exact Komoot POI identities.

        limit accepts 1 to 10 candidates. near_lat and near_lng bias the search.
        osm_tag filters Photon results, such as highway:bus_stop.
        Each candidate keeps its Photon name, coordinates and OSM category.
        resolution_status reports resolved, not_found, ambiguous, missing_poi_id,
        missing_osm_identity, or invalid_location. Only resolved candidates have
        poi_id and waypoint_details. komoot_matches retains exact OSM matches.
        The tool never selects one place from several Photon candidates.
        It uses public searches and does not access an account.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("limit must be an integer between 1 and 10")
        if not query.strip():
            raise ValueError("query must not be empty")
        if (near_lat is None) != (near_lng is None):
            raise ValueError("near_lat and near_lng must be supplied together")
        if near_lat is not None:
            near_lat, near_lng = validate_coordinates([[near_lat, near_lng]])[0]
        limiter = get_rate_limiter()
        await limiter.acquire()
        candidates = await asyncio.to_thread(
            get_geocoder().forward, query, limit=limit, lang=lang,
            near_lat=near_lat, near_lng=near_lng, osm_tag=osm_tag,
        )
        results = []
        for candidate in candidates[:limit]:
            result = resolve_poi_candidate(candidate, [])
            if result["komoot_osm_id"] is not None:
                try:
                    lat, lng = validate_coordinates([[candidate.get("lat"), candidate.get("lon")]])[0]
                except ValueError:
                    result["resolution_status"] = "invalid_location"
                else:
                    await limiter.acquire()
                    nearby = await asyncio.to_thread(find_pois_nearby, lat, lng, 100, 100, lang)
                    result = resolve_poi_candidate(candidate, nearby["results"])
            results.append(result)
        return {"query": query, "results": results}

    @mcp.tool()
    async def komoot_plan_and_upload(
        start: str, end: str | None = None, roundtrip: bool = False,
        sport: str = "hike", waypoints: str | None = None,
        tour_name: str | None = None, tour_status: str = "private",
        constitution: int = 3,
        waypoint_names: list[str | None] | None = None,
        route_type: RouteType = "best", avoid: list[Avoidance] | None = None,
        waypoint_details: list[WaypointDetails | None] | None = None,
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
        roundtrip appends the start if needed after the end and waypoints.
        Equal start/end coordinates always enable Komoot's native roundtrip setting.
        Without an end, roundtrip needs at least one waypoint. This is an
        out-and-back, not a target-distance loop generator.
        waypoint_names contains one name or null for start, each via point,
        then end if supplied. Explicit names override resolved geocoder names.
        Null keeps a resolved place name where available. Coordinate-only
        points stay unnamed. Roundtrip closure uses special:back, not a duplicate name.
        route_type selects best, quiet, scenic, shortest, less_elevation, or technical.
        avoid combines ferries, steps, steep_hills, and rough_terrain.
        The saved route keeps these preferences. Avoidance is not a guarantee.
        waypoint_details has one object or null for start, each via point, then end.
        Use name for custom points, highlight_id with highlight_type point/segment,
        or poi_id for an ordinary map POI. poi_id accepts ASCII letters and digits.
        Use a Komoot POI ID, not an OSM ID. These identities are mutually exclusive.
        Highlight and POI references cannot also have a waypoint_names entry.
        Locations remain required.
        Roundtrip closure keeps the start location.
        """
        profile = native_sport(sport)
        routing_tags(route_type, avoid)
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
        names = validate_waypoint_names(waypoint_names, 1 + len(vias) + bool(end))
        metadata = waypoint_metadata(names, waypoint_details, len(names))
        # Validate all explicit coordinates before any geocoding request.
        start_coords = _parse_coords(start)
        end_coords = _parse_coords(end) if end else None
        geocoder = get_geocoder()
        if start_coords is None:
            start_coords, resolved_name = await _geocode_location(start, geocoder)
            if not metadata[0]:
                names[0] = resolved_name
        if end and end_coords is None:
            end_coords, resolved_name = await _geocode_location(end, geocoder)
            if not metadata[-1]:
                names[-1] = resolved_name
        points = [start_coords, *vias]
        if end_coords is not None:
            points.append(end_coords)
        client = get_client()
        route = await plan_native_route(
            client, points, sport, constitution, roundtrip, names,
            route_type=route_type, avoid=avoid,
            waypoint_details=waypoint_details,
        )
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
    result = results[0]
    name = result.get("display_name")
    if not isinstance(name, str) or not name.strip():
        name = None
    return validate_coordinates([[result["lat"], result["lon"]]])[0], name


def _parse_coords(text):
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        coords = [float(part.strip()) for part in parts]
    except ValueError:
        return None
    return validate_coordinates([coords])[0]
