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
    ) -> dict[str, Any]:
        """Plan with Komoot from ordered [lat, lng] pairs without saving.

        constitution is integer fitness from 1 to 5. roundtrip appends the
        start if needed. sport accepts hike, trail_run, mountain_bike,
        road_cycle, gravel_ride (touringbicycle). Native aliases are mtb,
        mountainbike, touringbicycle, racebike, e_racebike, e_mtb, jogging,
        nordicwalking, and e_touringbicycle. Unknown names are rejected.
        waypoint_names contains one name or null per input coordinate.
        Names stay attached to the ordered start, via points, and end.
        An appended roundtrip endpoint uses the start name. Coordinates alone
        do not trigger reverse geocoding or generate POI names.
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
        Coordinates remain required. Roundtrip closure inherits the start metadata.
        """
        return await plan_native_route(
            get_client(), coordinates, sport, constitution, roundtrip, waypoint_names,
            route_type=route_type, avoid=avoid,
            waypoint_details=waypoint_details,
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
        """
        route = dict(route_data)
        if sport is not None:
            route["sport"] = native_sport(sport)
        saved = await get_client().save_planned_tour(route, name, status=status)
        return saved_plan_result(saved, route, status)

    @mcp.tool()
    async def komoot_update_planned_tour(
        tour_id: int, route_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace the geometry of one existing planned tour with a previewed route.

        Side effect: updates this exact tour ID in the account.
        Pass the full result of komoot_plan_native_route or komoot_import_gpx_route.
        This tool does not plan again. It preserves name, visibility, description,
        and gear. Komoot can refresh the planning date. The result reports its
        before and after values in metadata_changes.date.
        This tool rejects recorded activities and incomplete geometry.
        Readback checks geometry, ordered waypoint references, sport, fitness,
        visibility, and the preserved metadata. If readback fails, inspect the
        reported tour ID before retrying. This tool does not create another tour.
        """
        saved = await get_client().update_planned_tour(tour_id, route_data)
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
    ) -> dict[str, Any]:
        """Plan from [lat, lng] pairs and create a planned tour in one call.

        Side effect: saves the result on Komoot, private by default.
        sport accepts hike, trail_run, mountain_bike, road_cycle, gravel_ride
        (touringbicycle), plus native mtb, mountainbike, touringbicycle,
        racebike, e_racebike, e_mtb, jogging, nordicwalking, e_touringbicycle.
        constitution is integer fitness 1–5. roundtrip appends the start.
        Readback verifies the saved visibility, sport, fitness, and waypoint references.
        waypoint_names contains one name or null per input coordinate.
        An appended roundtrip endpoint uses the start name.
        route_type selects best, quiet, scenic, shortest, less_elevation, or technical.
        avoid combines ferries, steps, steep_hills, and rough_terrain.
        The saved route keeps these preferences. Avoidance is not a guarantee.
        waypoint_details has one object or null per input point.
        Use name for custom points, highlight_id with highlight_type point/segment,
        or poi_id for an ordinary map POI. poi_id accepts ASCII letters and digits.
        Use a Komoot POI ID, not an OSM ID. These identities are mutually exclusive.
        Highlight and POI references cannot also have a waypoint_names entry.
        Coordinates remain required.
        Roundtrip closure inherits the start metadata.
        """
        if status not in {"private", "friends", "public"}:
            raise ValueError("status must be private, friends, or public")
        route = await plan_native_route(
            get_client(), coordinates, sport, constitution, roundtrip, waypoint_names,
            route_type=route_type, avoid=avoid,
            waypoint_details=waypoint_details,
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
