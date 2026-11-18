"""Native Komoot planning and two-stage GPX imports."""

from __future__ import annotations

from typing import Any, Literal
from mcp.server.fastmcp import Context

from komoot_mcp.context import get_client
from komoot_mcp.descriptions import prepare_gpx_descriptions
from komoot_mcp.gpx_import import import_gpx
from komoot_mcp.routing import (
    native_sport, plan_native_route, saved_plan_result, validate_coordinates,
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
    ) -> dict[str, Any]:
        """Plan with Komoot from ordered [lat, lng] pairs without saving.

        constitution is integer fitness from 1 to 5. roundtrip appends the
        start if needed. sport accepts hike, trail_run, mountain_bike,
        road_cycle, gravel_ride (touringbicycle). Native aliases are mtb,
        mountainbike, touringbicycle, racebike, e_racebike, e_mtb, jogging,
        nordicwalking, and e_touringbicycle. Unknown names are rejected.
        """
        return await plan_native_route(get_client(), coordinates, sport, constitution, roundtrip)

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
    async def komoot_plan_and_save_tour(
        coordinates: list[list[float]], name: str, sport: str = "hike",
        constitution: int = 3, roundtrip: bool = False,
        status: Literal["private", "friends", "public"] = "private",
    ) -> dict[str, Any]:
        """Plan from [lat, lng] pairs and create a planned tour in one call.

        Side effect: saves the result on Komoot, private by default.
        sport accepts hike, trail_run, mountain_bike, road_cycle, gravel_ride
        (touringbicycle), plus native mtb, mountainbike, touringbicycle,
        racebike, e_racebike, e_mtb, jogging, nordicwalking, e_touringbicycle.
        constitution is integer fitness 1–5. roundtrip appends the start.
        Visibility is requested, not readback-verified.
        """
        if status not in {"private", "friends", "public"}:
            raise ValueError("status must be private, friends, or public")
        route = await plan_native_route(get_client(), coordinates, sport, constitution, roundtrip)
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
