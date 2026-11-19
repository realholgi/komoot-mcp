"""Read-only overnight discovery and stage previews with explicit sleeping modes."""

import asyncio

from komoot_mcp.bivouac import find_bivouac_candidates
from komoot_mcp.context import get_client
from komoot_mcp.overnight import (
    OvernightMode, cut_route, route_snapshot, shifted_candidates, validate_date,
    validate_distance, validate_modes,
)
from komoot_mcp.overnight_places import find_overnight_places
from komoot_mcp.stage_planning import plan_stages, validate_stage_options


def _validate_search(overnight_modes, corridor_m, overnight_date, start_km, end_km,
                     min_path_distance_m, max_path_distance_m, max_slope_deg, min_area_m2, persons):
    validate_modes(overnight_modes)
    validate_date(overnight_date)
    validate_distance(corridor_m, "corridor_m", minimum=1, maximum=5000)
    validate_distance(start_km, "start_km")
    if end_km is not None:
        validate_distance(end_km, "end_km", minimum=start_km)
        if end_km == start_km:
            raise ValueError("end_km must be greater than start_km")
    validate_distance(min_path_distance_m, "min_path_distance_m", minimum=1, maximum=500)
    validate_distance(max_path_distance_m, "max_path_distance_m", minimum=min_path_distance_m, maximum=500)
    if max_path_distance_m <= min_path_distance_m:
        raise ValueError("max_path_distance_m must be greater than min_path_distance_m")
    validate_distance(max_slope_deg, "max_slope_deg", minimum=0.1, maximum=15)
    validate_distance(min_area_m2, "min_area_m2", minimum=1, maximum=1000)
    if isinstance(persons, bool) or not isinstance(persons, int) or not 1 <= persons <= 20:
        raise ValueError("persons must be an integer between 1 and 20")


async def _discover(points, distances, overnight_modes, *, corridor_m: float = 2000, overnight_date=None,
                    min_path_distance_m: float = 30, max_path_distance_m: float = 150,
                    max_slope_deg: float = 5, min_area_m2: float = 6, persons=1,
                    terrain_windows=None):
    searches = []
    # Both queries also accept bivouac: mapped designated sites and terrain inspection areas differ.
    searches.append(find_overnight_places(points, distances, overnight_modes, corridor_m, overnight_date))
    windows = ([(0, distances[-1])] if terrain_windows is None else terrain_windows) if "bivouac" in overnight_modes else []
    for start, end in windows:
        selected, selected_distances = cut_route(points, distances, start, end)
        async def terrain_search(selected=selected, selected_distances=selected_distances, start=start, end=end):
            result = await find_bivouac_candidates(
                selected, selected_distances, min_path_distance_m=min_path_distance_m,
                max_path_distance_m=max_path_distance_m, max_slope_deg=max_slope_deg,
                min_area_m2=min_area_m2, persons=persons,
            )
            shifted_candidates(result, start)
            result["coverage"]["source_start_km"] = start
            result["coverage"]["source_end_km"] = end
            return result
        searches.append(terrain_search())
    results = await asyncio.gather(*searches)
    statuses = [result["status"] for result in results]
    status = "ok" if all(value == "ok" for value in statuses) else "unavailable" if all(value == "unavailable" for value in statuses) else "partial"
    return {
        "status": status, "overnight_modes": overnight_modes,
        "items": [item for result in results for item in result["items"]],
        "issues": [issue for result in results for issue in result["issues"]],
        "coverage": [result["coverage"] for result in results],
        "sources": [source for result in results for source in result["sources"]],
        "terrain_search_windows": windows,
    }


def register(mcp):
    @mcp.tool()
    async def komoot_find_overnight_stays(
        tour_id: int, overnight_modes: list[OvernightMode],
        start_km: float = 0, end_km: float | None = None,
        corridor_m: float = 2000, overnight_date: str | None = None,
        min_path_distance_m: float = 30, max_path_distance_m: float = 150,
        max_slope_deg: float = 5, min_area_m2: float = 6, persons: int = 1,
    ) -> dict:
        """Find overnight places and bivouac inspection areas along a source tour.

        overnight_modes is REQUIRED and nonempty: tent, bivouac, accommodation.
        Multiple modes mean OR; never infer or substitute a different sleeping mode.
        Ask the user for the modes before calling if they have not specified them.
        Anonymous Overpass requests disclose the selected route window to OpenStreetMap
        infrastructure. Bivouac mode also requests public AWS Terrarium terrain tiles.
        corridor_m filters mapped places. Bivouac terrain uses max_path_distance_m
        around the source track and min_path_distance_m from ALL mapped paths.
        Raster flatness does not establish a level sleeping footprint, ground safety,
        permission or a walkable approach. min_area_m2 is per person. Dates do not
        establish availability. Coverage, source resolution and unknowns stay explicit.
        No tour changes, account writes, bookings or local files.
        """
        _validate_search(overnight_modes, corridor_m, overnight_date, start_km, end_km,
                         min_path_distance_m, max_path_distance_m, max_slope_deg, min_area_m2, persons)
        tour = await get_client().get_tour_full(tour_id, include_coordinates=True)
        points, distances = route_snapshot(tour)
        selected, selected_distances = cut_route(points, distances, start_km, end_km)
        result = await _discover(
            selected, selected_distances, overnight_modes, corridor_m=corridor_m,
            overnight_date=overnight_date, min_path_distance_m=min_path_distance_m,
            max_path_distance_m=max_path_distance_m, max_slope_deg=max_slope_deg,
            min_area_m2=min_area_m2, persons=persons,
        )
        shifted_candidates(result, start_km)
        return {**result, "tour_id": tour_id, "source_start_km": start_km,
                "source_end_km": distances[-1] if end_km is None else end_km,
                "overnight_date": overnight_date}

    @mcp.tool()
    async def komoot_plan_stages(
        tour_id: int, overnight_modes: list[OvernightMode], days: int,
        target_distance_km: float | None = None, max_distance_km: float | None = None,
        max_duration_s: float | None = None, max_ascent_m: float | None = None,
        max_detour_m: float = 5000, corridor_m: float = 2000,
        fixed_stays: list[dict] | None = None,
        night_modes: list[list[OvernightMode]] | None = None,
        preferred_modes: list[OvernightMode] | None = None,
        start_date: str | None = None, sport: str = "hike", constitution: int = 3,
        min_path_distance_m: float = 30, max_path_distance_m: float = 150,
        max_slope_deg: float = 5, min_area_m2: float = 6, persons: int = 1,
    ) -> dict:
        """Preview complete overnight stages while retaining the source track.

        REQUIRED overnight_modes has OR semantics: tent, bivouac, accommodation.
        Ask before calling if sleeping modes are unspecified. night_modes optionally
        narrows the selection per night (days-1 nonempty lists). fixed_stays contains
        night (1-based), name, lat, lng, route_km and overnight_modes for user-selected
        places. Explicit route_km chooses the encounter on loops. start_date is day 1.
        preferred_modes optionally ranks allowed modes from most to least preferred.
        Finds mapped places via anonymous Overpass route queries; bivouac also reads
        public AWS terrain tiles. Fixed stays can avoid discovery when every night is set.
        Automatic terrain searches cover 500 m around each ideal overnight position,
        not the whole tour. Search specific windows separately to inspect alternatives.
        Preserves every original section. Routes each accommodation access direction
        separately, returning next day to the same source position. Terrain bivouac
        approaches are straight-line inspection legs, explicitly NOT verified paths.
        max_detour_m limits both access legs together. Unknown time/ascent cannot
        satisfy a corresponding hard limit. Source times use uniform average pace.
        Returns up to three alternatives with inline GPX, never saves. After explicit
        approval, import each GPX with match_route=false and create private tours using
        existing tools. Import can simplify geometry. No booking or local file writes.
        """
        per_night, fixed = validate_stage_options(
            overnight_modes, days, target_distance_km=target_distance_km,
            max_distance_km=max_distance_km, max_duration_s=max_duration_s,
            max_ascent_m=max_ascent_m, max_detour_m=max_detour_m, fixed_stays=fixed_stays,
            night_modes=night_modes, start_date=start_date, sport=sport, constitution=constitution,
            preferred_modes=preferred_modes,
        )
        _validate_search(overnight_modes, corridor_m, start_date, 0, None,
                         min_path_distance_m, max_path_distance_m, max_slope_deg, min_area_m2, persons)
        client = get_client()
        tour = await client.get_tour_full(tour_id, include_coordinates=True)
        points, distances = route_snapshot(tour)
        for stay in fixed.values():
            if not 0 < stay["route_km"] < distances[-1]:
                raise ValueError("A fixed stay must attach inside the source route")
        terrain_windows = [
            (max(0, distances[-1] * night / days - 0.25),
             min(distances[-1], distances[-1] * night / days + 0.25))
            for night in range(1, days)
            if "bivouac" in per_night[night - 1] and night not in fixed
        ]
        if fixed_stays is not None and len(fixed_stays) == days - 1:
            discovery = {"status": "not_requested", "items": [], "issues": [], "coverage": [], "sources": []}
        else:
            discovery = await _discover(
                points, distances, overnight_modes, corridor_m=corridor_m,
                min_path_distance_m=min_path_distance_m, max_path_distance_m=max_path_distance_m,
                max_slope_deg=max_slope_deg, min_area_m2=min_area_m2, persons=persons,
                terrain_windows=terrain_windows,
            )
        result = await plan_stages(
            client, tour, points, distances, discovery["items"], overnight_modes, days,
            target_distance_km=target_distance_km, max_distance_km=max_distance_km,
            max_duration_s=max_duration_s, max_ascent_m=max_ascent_m, max_detour_m=max_detour_m,
            fixed_stays=fixed_stays, night_modes=night_modes, start_date=start_date,
            sport=sport, constitution=constitution,
            preferred_modes=preferred_modes,
        )
        result["discovery"] = {key: value for key, value in discovery.items() if key != "items"}
        if terrain_windows:
            result["search"]["complete"] = False
            result["search"]["terrain_scope"] = "500 m around each ideal overnight position only"
        if discovery["status"] in {"partial", "unavailable"}:
            result["search"]["complete"] = False
        return result
