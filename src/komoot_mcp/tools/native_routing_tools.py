"""Native Komoot planning and two-stage GPX imports."""

from __future__ import annotations

from typing import Any, Literal
from mcp.server.fastmcp import Context

from komoot_mcp.context import get_client
from komoot_mcp.descriptions import prepare_gpx_descriptions
from komoot_mcp.gpx_import import import_gpx
from komoot_mcp.routing import (
    native_sport, plan_native_route, saved_plan_result, validate_coordinates,
    RouteType, Avoidance, WaypointDetails,
)


def register(mcp):
    @mcp.tool()
    async def komoot_import_gpx_route(
        gpx_content: str, sport: str = "hike", track_index: int = 0,
        prepare_descriptions: bool = False,
        match_route: bool = True,
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Import a GPX, optionally matching its geometry to Komoot's network.

        Sends the full GPX, including any waypoint names and descriptions, to
        Komoot's two-stage file/import service. Komoot may discard standalone
        POIs or comments; this tool does not promise their preservation. Save
        the returned route_data using komoot_create_planned_tour.
        track_index selects one of the file service's imported tracks (zero-based).
        Descriptions must fit Komoot's 250 UTF-16-unit storage limit.
        prepare_descriptions=True uses MCP client sampling to summarize long
        texts and translate other languages to German. German/English short
        texts stay verbatim. No source GPX is overwritten or text simply cut off.
        match_route=False keeps the original imported track and its POIs rather
        than the network-matched variant. Custom names/descriptions are attached
        to their cnw path entries so that saving the route preserves them.
        Recognized GPX POI types select the current Komoot web-editor category
        by unambiguous source name/type. Unknown types retain the imported category.
        The category "water" does not imply drinking-water safety.
        Named GPX track/route endpoints become custom named waypoints when
        the selected source geometry is unambiguous. Existing names take priority.
        """
        if prepare_descriptions:
            prepared = await prepare_gpx_descriptions(gpx_content, ctx.session if ctx else None)
            gpx_content = prepared["gpx_content"]

        return await import_gpx(
            gpx_content, get_client(), sport=sport, track_index=track_index, match_route=match_route,
        )


    @mcp.tool()
    async def komoot_plan_native_route(
        coordinates: list[list[float]], sport: str = "hike",
        constitution: int = 3, roundtrip: bool = False,
        waypoint_names: list[str | None] | None = None,
        route_type: RouteType = "best", avoid: list[Avoidance] | None = None,
        waypoint_details: list[WaypointDetails | None] | None = None,
        waypoint_stays_s: list[float] | None = None,
    ) -> dict[str, Any]:
        """Plan with Komoot from ordered [lat, lng] pairs without saving.

        constitution is integer fitness from 1 to 5. roundtrip appends the
        start only if needed. Equal start/end coordinates always enable Komoot's
        native roundtrip setting, even with roundtrip=False. sport accepts hike, mountaineering, trail_run, mountain_bike,
        road_cycle, gravel_ride, enduro, e_mountain_bike, e_road_cycle, e_gravel_ride, and e_enduro.
        Gravel uses mtb_easy. Enduro uses mtb_advanced. E-bike profiles stay separate.
        Native aliases include mtb, mountainbike, touringbicycle, racebike, jogging,
        nordicwalking, mtb_easy, mtb_advanced, and their supported e_ variants.
        Unknown names fail before network access.
        waypoint_names contains one name or null per input coordinate.
        Names stay attached to the ordered start, via points, and end.
        The closing point uses special:back instead of a duplicate name or POI.
        Coordinates alone do not trigger reverse geocoding or generate POI names.
        route_type selects best, quiet, scenic, shortest, less_elevation, or technical.
        avoid combines ferries, steps, steep_hills, and rough_terrain.
        Komoot uses these preferences to calculate geometry. Avoidance is not a guarantee.
        waypoint_details has one object or null per input point, including start and end.
        Use {"name": "Station"} for a custom name, or {"highlight_id": 123,
        "highlight_type": "point"} for an existing highlight. For a segment use
        highlight_type="segment". Use {"poi_id": "Ab123"} for an ordinary map POI.
        poi_id is a Komoot ID with ASCII letters and digits, not an OSM ID.
        Use only one of name, highlight_id, or poi_id per point.
        Highlight and POI references cannot also have a waypoint_names entry.
        Coordinates remain required. Roundtrip closure keeps the start location.
        waypoint_stays_s sets a pause in seconds per input point. Default: zero.
        planning_summary separates moving time, pauses, and total time.
        It also reports waypoint offsets and unusual detours. These do not prove access.
        """
        return await plan_native_route(
            get_client(), coordinates, sport, constitution, roundtrip, waypoint_names,
            route_type=route_type, avoid=avoid,
            waypoint_details=waypoint_details,
            waypoint_stays_s=waypoint_stays_s,
        )

    @mcp.tool()
    async def komoot_create_planned_tour(
        route_data: dict[str, Any], name: str = "Planned tour",
        sport: str | None = None,
        status: Literal["private", "friends", "public"] = "private",
    ) -> dict[str, Any]:
        """Save imported/planned route_data as a planned tour in the account.

        Side effect: creates a Komoot tour. Defaults to private. route_data is
        the full result of komoot_import_gpx_route or komoot_plan_native_route,
        including its path/waypoint metadata and embedded geometry.
        Equal start/end coordinates enable Komoot's native roundtrip setting
        without changing the geometry or adding another return leg.
        """
        route = dict(route_data)
        if sport is not None:
            route["sport"] = native_sport(sport)
        saved = await get_client().save_planned_tour(route, name, status=status)
        return saved_plan_result(saved, route, status)

    @mcp.tool()
    async def komoot_preview_tour_update(
        tour_id: int, route_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare a replacement route with the current tour. Do not save.

        Return distance, ascent, descent, duration, and ordered POI changes.
        Pass preview_token and the same route_data to komoot_update_planned_tour.
        A changed tour needs a new preview.
        """
        return await get_client().preview_tour_update(tour_id, route_data)

    @mcp.tool()
    async def komoot_compare_route_variants(
        coordinates: list[list[float]], sport: str = "hike",
        constitution: int = 3, roundtrip: bool = False,
        waypoint_names: list[str | None] | None = None,
        avoid: list[Avoidance] | None = None,
        waypoint_details: list[WaypointDetails | None] | None = None,
        waypoint_stays_s: list[float] | None = None,
    ) -> dict[str, Any]:
        """Compare shortest, scenic, and quiet routes without saving.

        Use the same points, sport, fitness, avoidance, and pauses for each route.
        Return each full route and its differences from shortest.
        Preferences do not guarantee scenery or low traffic. Pauses default to zero.
        """
        from komoot_mcp.routing import compare_route_variants

        return await compare_route_variants(
            get_client(), coordinates, sport, constitution, roundtrip, waypoint_names,
            avoid=avoid, waypoint_details=waypoint_details, waypoint_stays_s=waypoint_stays_s,
        )

    @mcp.tool()
    async def komoot_update_planned_tour(
        tour_id: int, route_data: dict[str, Any], preview_token: str,
    ) -> dict[str, Any]:
        """Replace one planned tour with the exact route from its preview.

        This changes the tour in the account. First call komoot_preview_tour_update.
        Pass its preview_token and the same route_data. If either changed, this tool
        stops without writing. Review a new preview before retrying.
        It preserves name, visibility, description, and gear.
        Komoot can refresh the date. metadata_changes.date reports this change.
        Readback checks geometry, waypoints, sport, fitness, and preserved metadata.
        After a write error, inspect tour_id and confirmed_changes before retrying.
        """
        saved = await get_client().update_planned_tour(tour_id, route_data, preview_token)
        result = saved_plan_result(saved, route_data, saved["verified_visibility"])
        result["metadata_changes"] = saved["metadata_changes"]
        return result

    @mcp.tool()
    async def komoot_plan_and_save_tour(
        coordinates: list[list[float]], name: str, sport: str = "hike",
        constitution: int = 3, roundtrip: bool = False,
        status: Literal["private", "friends", "public"] = "private",
        waypoint_names: list[str | None] | None = None,
        route_type: RouteType = "best", avoid: list[Avoidance] | None = None,
        waypoint_details: list[WaypointDetails | None] | None = None,
        waypoint_stays_s: list[float] | None = None,
    ) -> dict[str, Any]:
        """Plan from [lat, lng] pairs and create a planned tour in one call.

        Side effect: saves the result on Komoot, private by default.
        sport accepts hike, mountaineering, trail_run, mountain_bike, road_cycle,
        gravel_ride (mtb_easy), enduro (mtb_advanced), e_mountain_bike,
        e_road_cycle, e_gravel_ride, e_enduro, and supported native aliases.
        E-bike profiles stay separate. Unknown names fail before network access.
        constitution is integer fitness 1–5. roundtrip appends the start if needed.
        Equal start/end coordinates always enable Komoot's native roundtrip setting.
        Readback verifies the saved visibility, sport, fitness, and waypoint references.
        waypoint_names contains one name or null per input coordinate.
        The closing point uses special:back instead of a duplicate name or POI.
        route_type selects best, quiet, scenic, shortest, less_elevation, or technical.
        avoid combines ferries, steps, steep_hills, and rough_terrain.
        The saved route keeps these preferences. Avoidance is not a guarantee.
        waypoint_details has one object or null per input point.
        Use name for custom points, highlight_id with highlight_type point/segment,
        or poi_id for an ordinary map POI. poi_id accepts ASCII letters and digits.
        Use a Komoot POI ID, not an OSM ID. These identities are mutually exclusive.
        Highlight and POI references cannot also have a waypoint_names entry.
        Coordinates remain required.
        Roundtrip closure keeps the start location.
        waypoint_stays_s sets seconds per input point. Pauses default to zero.
        Pauses affect the local total time, not Komoot's stored moving duration.
        """
        if status not in {"private", "friends", "public"}:
            raise ValueError("status must be private, friends, or public")
        route = await plan_native_route(
            get_client(), coordinates, sport, constitution, roundtrip, waypoint_names,
            route_type=route_type, avoid=avoid,
            waypoint_details=waypoint_details,
            waypoint_stays_s=waypoint_stays_s,
        )
        saved = await get_client().save_planned_tour(route, name, status=status)
        return saved_plan_result(saved, route, status)

    @mcp.tool()
    async def komoot_reverse_geocode(lat: float, lng: float, lang: str = "en") -> dict[str, Any]:
        """Look up places near coordinates using public Photon, without credentials."""
        validate_coordinates([[lat, lng]])
        return await get_client()._http_request(
            "GET", "https://photon.komoot.io/reverse", no_auth=True,
            params={"lat": lat, "lon": lng, "lang": lang},
        )
