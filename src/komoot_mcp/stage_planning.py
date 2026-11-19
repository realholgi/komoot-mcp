"""Choose a complete overnight sequence and cut the unchanged source track."""

from datetime import date, timedelta

import gpxpy.gpx

from komoot_mcp.overnight import (
    altitude, cut_route, finite, point_at, validate_date, validate_distance, validate_modes,
)
from komoot_mcp.route_geometry import coordinate
from komoot_mcp.route_sections import coordinate_distances
from komoot_mcp.routing import RoutingError, native_sport, plan_native_route

_MAX_OPTIONS_PER_NIGHT = 8
_MAX_FIXED_OFFSET_M = 5000


def validate_stage_options(overnight_modes, days, *, target_distance_km=None,
                           max_distance_km=None, max_duration_s=None, max_ascent_m=None,
                           max_detour_m: float = 5000, fixed_stays=None, night_modes=None,
                           start_date=None, sport="hike", constitution=3, preferred_modes=None):
    modes = validate_modes(overnight_modes)
    if preferred_modes is not None:
        if set(validate_modes(preferred_modes)) - set(modes):
            raise ValueError("preferred_modes must be a nonempty subset of overnight_modes")
    if isinstance(days, bool) or not isinstance(days, int) or not 2 <= days <= 14:
        raise ValueError("days must be an integer between 2 and 14")
    native_sport(sport)
    if isinstance(constitution, bool) or not isinstance(constitution, int) or not 1 <= constitution <= 5:
        raise ValueError("constitution must be an integer between 1 and 5")
    for name, value in (("target_distance_km", target_distance_km), ("max_distance_km", max_distance_km),
                        ("max_duration_s", max_duration_s), ("max_ascent_m", max_ascent_m)):
        if value is not None:
            validate_distance(value, name, minimum=0.001)
    validate_distance(max_detour_m, "max_detour_m", maximum=20000)
    validate_date(start_date)
    if night_modes is None:
        per_night = [modes for _ in range(days - 1)]
    else:
        if not isinstance(night_modes, list) or len(night_modes) != days - 1:
            raise ValueError("night_modes must have one nonempty selection per overnight stop")
        per_night = [validate_modes(selection) for selection in night_modes]
        if any(set(selection) - set(modes) for selection in per_night):
            raise ValueError("night_modes must be subsets of overnight_modes")
    fixed = {}
    if fixed_stays is not None:
        if not isinstance(fixed_stays, list):
            raise ValueError("fixed_stays must be a list")
        for stay in fixed_stays:
            if not isinstance(stay, dict) or set(stay) - {"night", "name", "lat", "lng", "route_km", "overnight_modes"}:
                raise ValueError("Fixed stays need night, name, lat, lng, route_km and overnight_modes")
            night = stay.get("night")
            if isinstance(night, bool) or not isinstance(night, int) or not 1 <= night < days or night in fixed:
                raise ValueError("Each fixed stay must identify one distinct night between 1 and days-1")
            if not isinstance(stay.get("name"), str) or not stay["name"].strip():
                raise ValueError("A fixed stay needs a nonempty name")
            if coordinate(stay) is None:
                raise ValueError("A fixed stay needs valid lat/lng coordinates")
            validate_distance(stay.get("route_km"), "fixed_stays.route_km")
            if not set(validate_modes(stay.get("overnight_modes"))) & set(per_night[night - 1]):
                raise ValueError("Fixed stay modes do not match that night's selected modes")
            fixed[night] = stay
    return per_night, fixed


def _ascent(points):
    if any(point[2] is None for point in points):
        return None
    return sum(max(0, end[2] - start[2]) for start, end in zip(points, points[1:]))


def _length_m(points):
    distances = coordinate_distances(points)
    if not distances:
        raise ValueError("Invalid access geometry")
    return distances[-1] * 1000


def _gpx(points, name, start_name, end_name):
    gpx = gpxpy.gpx.GPX()
    gpx.creator = "komoot-mcp"
    track = gpxpy.gpx.GPXTrack(name=name)
    segment = gpxpy.gpx.GPXTrackSegment()
    segment.points = [gpxpy.gpx.GPXTrackPoint(p[0], p[1], elevation=p[2]) for p in points]
    track.segments.append(segment)
    gpx.tracks.append(track)
    for point, label in ((points[0], start_name), (points[-1], end_name)):
        gpx.waypoints.append(gpxpy.gpx.GPXWaypoint(point[0], point[1], elevation=point[2], name=label))
    return gpx.to_xml()


async def _access(client, anchor, stay, sport, constitution):
    """Keep each direction distinct; a reverse route need not be traversable."""
    target = [stay["lat"], stay["lng"], None]
    offset = _length_m([anchor, target])
    if offset < 0.1:
        empty = {"points": [anchor], "distance_m": 0, "duration_s": 0, "ascent_m": 0}
        return {"outbound": empty, "return": empty, "status": "on_route"}
    if (stay.get("kind") == "terrain_bivouac" or stay.get("access_kind") == "unverified_off_path"
            or stay["overnight_modes"] == ["bivouac"]):
        outbound = {"points": [anchor, target], "distance_m": offset, "duration_s": None, "ascent_m": None}
        inbound = {**outbound, "points": [target, anchor]}
        return {"outbound": outbound, "return": inbound, "status": "unverified_off_path"}
    directions = []
    for start, end in ((anchor, target), (target, anchor)):
        route = await plan_native_route(client, [start[:2], end[:2]], sport, constitution)
        raw = route.get("_embedded", {}).get("coordinates", [])
        raw = raw.get("items", []) if isinstance(raw, dict) else raw
        if not isinstance(raw, list) or len(raw) < 2:
            raise RoutingError("Access route has no complete valid geometry")
        points = []
        for point in raw:
            pair = coordinate(point)
            if pair is None:
                raise RoutingError("Access route contains invalid geometry")
            points.append([*pair, altitude(point)])
        for requested, observed in ((start, points[0]), (end, points[-1])):
            if _length_m([requested, observed]) > 5:
                raise RoutingError("Access route snaps over 5 m from its requested endpoint")
        # Exact joins retain the original anchor and the selected overnight point.
        if points[0][:2] != start[:2]:
            points.insert(0, start)
        if points[-1][:2] != end[:2]:
            points.append(end)
        warnings = route.get("tour_information") or []
        if any(isinstance(warning, dict) and warning.get("type") in {
                "NO_ACCESS", "PRIVATE_LAND", "RESTRICTED", "OFF_GRID", "HIGH_DANGER"} for warning in warnings):
            raise RoutingError("Access route has a restriction or off-grid warning")
        duration = route.get("duration")
        directions.append({"points": points,
                           "distance_m": _length_m(points),
                           "duration_s": duration if finite(duration) and duration >= 0 else None,
                           "ascent_m": _ascent(points), "routing_warnings": warnings})
    return {"outbound": directions[0], "return": directions[1], "status": "routed_unverified_access"}


async def plan_stages(client, tour, points, distances, candidates, overnight_modes, days, *,
                      target_distance_km=None, max_distance_km=None, max_duration_s=None,
                      max_ascent_m=None, max_detour_m: float = 5000, fixed_stays=None,
                      night_modes=None, start_date=None, sport="hike", constitution=3,
                      preferred_modes=None):
    per_night, fixed = validate_stage_options(
        overnight_modes, days, target_distance_km=target_distance_km,
        max_distance_km=max_distance_km, max_duration_s=max_duration_s,
        max_ascent_m=max_ascent_m, max_detour_m=max_detour_m, fixed_stays=fixed_stays,
        night_modes=night_modes, start_date=start_date, sport=sport, constitution=constitution,
        preferred_modes=preferred_modes,
    )
    total = distances[-1]
    target = total / days if target_distance_km is None else target_distance_km
    options, issues = [], []
    pruned = 0
    for night in range(1, days):
        eligible = []
        if night in fixed:
            stay = fixed[night]
            if not 0 < stay["route_km"] < total:
                raise ValueError("A fixed stay must attach inside the source route")
            anchor = point_at(points, distances, stay["route_km"])
            offset = _length_m([anchor, [stay["lat"], stay["lng"]]])
            if offset > _MAX_FIXED_OFFSET_M:
                raise ValueError("A fixed stay must be within 5000 m of its stated source position")
            eligible = [{"id": f"fixed:{night}", **stay, "permission": {"status": "unknown", "basis": "user_supplied"},
                         "source_links": [], "encounters": [{"route_km": stay["route_km"], "offset_m": offset}]}]
        else:
            eligible = [stay for stay in candidates
                        if set(stay["overnight_modes"]) & set(per_night[night - 1])
                        and stay.get("permission", {}).get("status") not in {"prohibited", "restricted"}]
        positions = []
        for stay in eligible:
            for encounter in stay["encounters"]:
                km = encounter["route_km"]
                if not 0 < km < total or encounter["offset_m"] * 2 > max_detour_m:
                    continue
                positions.append({"stay": stay, "km": km})
        positions.sort(key=lambda option: (abs(option["km"] - total * night / days), option["km"], option["stay"]["id"]))
        if night not in fixed:
            pruned += max(0, len(positions) - _MAX_OPTIONS_PER_NIGHT)
            positions = positions[:_MAX_OPTIONS_PER_NIGHT]
        options.append(positions)
    accesses = {}
    for positions in options:
        for option in positions:
            key = option["stay"]["id"], option["km"]
            if key not in accesses:
                try:
                    access = await _access(client, point_at(points, distances, option["km"]), option["stay"], sport, constitution)
                    detour = access["outbound"]["distance_m"] + access["return"]["distance_m"]
                    if detour > max_detour_m:
                        raise RoutingError("Routed overnight detour exceeds max_detour_m")
                    accesses[key] = access
                except RoutingError as exc:
                    accesses[key] = None
                    issues.append(f"{option['stay']['id']} at {option['km']:.3f} km: {exc}")
            option["access"] = accesses[key]
    for index, positions in enumerate(options):
        options[index] = [option for option in positions if option["access"] is not None]
    raw_duration = tour.get("time_in_motion", tour.get("duration"))
    pace = raw_duration / total if finite(raw_duration) and raw_duration > 0 else None
    empty = {"points": [], "distance_m": 0, "duration_s": 0, "ascent_m": 0}

    def leg(previous, current):
        start = previous["km"] if previous else 0
        end = current["km"] if current else total
        if end <= start:
            return None
        section, _ = cut_route(points, distances, start, end)
        before = previous["access"]["return"] if previous else empty
        after = current["access"]["outbound"] if current else empty
        distance_km = end - start + (before["distance_m"] + after["distance_m"]) / 1000
        duration = ((end - start) * pace + before["duration_s"] + after["duration_s"]
                    if pace is not None and before["duration_s"] is not None and after["duration_s"] is not None else None)
        ascent = _ascent(section)
        if ascent is not None and before["ascent_m"] is not None and after["ascent_m"] is not None:
            ascent += before["ascent_m"] + after["ascent_m"]
        else:
            ascent = None
        if (max_distance_km is not None and distance_km > max_distance_km
                or max_duration_s is not None and (duration is None or duration > max_duration_s)
                or max_ascent_m is not None and (ascent is None or ascent > max_ascent_m)):
            return None
        return {"source_start_km": start, "source_end_km": end, "distance_m": distance_km * 1000,
                "duration_s": duration, "ascent_m": ascent, "source_points": section,
                "before": before, "after": after}

    # Keep several complete alternatives, not one greedy choice per day.
    states = [(0.0, [], [])]
    for positions in [*options, [None]]:
        next_states = []
        for current in positions:
            arrivals = []
            for score, stops, legs in states:
                previous = stops[-1] if stops else None
                result = leg(previous, current)
                if result is None:
                    continue
                penalty = (result["distance_m"] / 1000 - target) ** 2
                penalty += (result["before"]["distance_m"] + result["after"]["distance_m"]) / 1000
                if current and preferred_modes is not None:
                    supported = current["stay"]["overnight_modes"]
                    rank = min((preferred_modes.index(mode) for mode in supported if mode in preferred_modes),
                               default=len(preferred_modes))
                    penalty += rank * target ** 2
                arrivals.append((score + penalty, [*stops, current] if current else stops, [*legs, result]))
            arrivals.sort(key=lambda state: state[0])
            next_states.extend(arrivals[:3])
        states = next_states
    states.sort(key=lambda state: state[0])
    issues.extend([
        "Source moving time uses uniform distance-based pace; it is not a terrain-aware timing model.",
        "Overnight availability, entrances and access permission remain unverified unless explicitly sourced.",
        "GPX import can simplify geometry. Use match_route=false and review the saved result.",
    ])
    if pruned:
        issues.append(f"Search shortlisted eight positions per night; {pruned} other night/position combinations were not evaluated.")
    result = {
        "status": "ok" if states else "no_feasible_plan", "tour_id": tour.get("id"),
        "overnight_modes": overnight_modes, "night_modes": per_night, "days": days,
        "preferred_modes": preferred_modes,
        "source_distance_m": total * 1000, "issues": issues,
        "search": {"positions_pruned": pruned, "complete": pruned == 0, "alternatives_limit": 3},
        "alternatives": [], "account_changes": False,
    }
    if not states:
        result["conflicts"] = {
            "nights_without_candidates": [index + 1 for index, positions in enumerate(options) if not positions],
            "message": "No complete sequence meets the constraints among evaluated candidates. Unknown time or ascent cannot satisfy a corresponding hard limit.",
        }
        return result
    for score, stops, legs in states[:3]:
        stages = []
        for index, part in enumerate(legs):
            previous = stops[index - 1] if index else None
            current = stops[index] if index < len(stops) else None
            source = part["source_points"]
            before, after = part["before"], part["after"]
            metrics = {key: value for key, value in part.items() if key not in {"source_points", "before", "after"}}
            assembled = [*before["points"][:-1], *source, *after["points"][1:]]
            start_name = previous["stay"]["name"] if previous else "Start"
            end_name = current["stay"]["name"] if current else "Finish"
            name = f"Day {index + 1}: {start_name} to {end_name}"
            unverified = any(option and option["access"]["status"] == "unverified_off_path" for option in (previous, current))
            stages.append({**metrics, "day": index + 1, "name": name,
                           "date": (date.fromisoformat(start_date) + timedelta(days=index)).isoformat() if start_date else None,
                           "overnight": current["stay"] if current else None,
                           "access_status": "unverified_off_path" if unverified else "routed_unverified_access",
                           "access_legs": {"from_previous_stay": before, "to_next_stay": after},
                           "gpx_content": _gpx(assembled, name, start_name, end_name)})
        result["alternatives"].append({"score": score, "stages": stages})
    return result
