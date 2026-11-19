"""Native planner persistence payloads and readback comparisons."""

import math

from komoot_mcp.route_geometry import coordinate


# TourHal._syncPostData's planned attribute filter in the shipped web planner.
# main.4aa1c01d52651411.js, offsets 1068690–1069025 and 1092250–1093070.
_PLANNED_FIELDS = frozenset({
    "id", "status", "type", "name", "description", "distance", "duration",
    "sport", "query", "constitution", "summary", "difficulty", "source",
    "routing_version", "date", "kcal_active", "kcal_resting", "start_point",
    "time_in_motion", "elevation_up", "elevation_down", "tour_information",
    "custom_tags", "path", "segments", "avoid_areas",
})
_EMBEDDED_FIELDS = ("coordinates", "way_types", "surfaces", "directions")


def roundtrip_path(path):
    """Mark an exact return to the start as Komoot's native roundtrip endpoint."""
    if not isinstance(path, list) or len(path) < 2:
        return path
    first, last = path[0], path[-1]
    if not isinstance(first, dict) or not isinstance(last, dict):
        return path
    start = coordinate(first.get("location"))
    end = coordinate(last.get("location"))
    if start is None or start != end or last.get("reference") == "special:back":
        return path
    # The web planner hides this closing marker instead of showing a duplicate POI.
    # main.1087dae42262732d.js, offsets 89412 and 1084498, observed 2026-11-19.
    endpoint = {key: value for key, value in last.items()
                if key not in {"reference", "referenceKey", "name", "description", "category"}}
    endpoint["reference"] = "special:back"
    return [*path[:-1], endpoint]


def planned_payload(route):
    if not isinstance(route, dict):
        raise ValueError("route_data must be a full native planner result")
    body = {key: value for key, value in route.items() if key in _PLANNED_FIELDS}
    if "path" in body:
        body["path"] = roundtrip_path(body["path"])
    embedded = route.get("_embedded", {})
    if isinstance(embedded, dict):
        body["_embedded"] = {
            key: {k: v for k, v in value.items() if k not in {"_meta", "_links", "page"}}
            for key in _EMBEDDED_FIELDS
            if isinstance((value := embedded.get(key)), dict)
        }
    return body


def geometry_points(items):
    if not isinstance(items, list) or len(items) < 2:
        raise ValueError("route_data needs at least two embedded coordinates")
    points = []
    for point in items:
        if isinstance(point, dict):
            pair = [point.get("lat"), point.get("lng")]
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            pair = list(point[:2])
        else:
            raise ValueError("Invalid route coordinate")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in pair):
            raise ValueError("Route coordinates must be finite numbers")
        if not (-90 <= pair[0] <= 90 and -180 <= pair[1] <= 180):
            raise ValueError("Route coordinate is outside latitude/longitude bounds")
        points.append(pair)
    return points


def validate_replacement(body):
    from komoot_mcp.routing import native_sport

    native_sport(body.get("sport"))
    fitness = body.get("constitution")
    if isinstance(fitness, bool) or not isinstance(fitness, int) or not 1 <= fitness <= 5:
        raise ValueError("route_data constitution must be an integer between 1 and 5")
    coordinates = body.get("_embedded", {}).get("coordinates", {}).get("items")
    geometry = geometry_points(coordinates)
    path, segments = body.get("path"), body.get("segments")
    if not isinstance(path, list) or len(path) < 2:
        raise ValueError("route_data needs at least two path waypoints")
    if not isinstance(segments, list) or len(segments) != len(path) - 1:
        raise ValueError("route_data needs one segment per path leg")
    last_index = -1
    for point in path:
        if not isinstance(point, dict) or not isinstance(point.get("location"), dict):
            raise ValueError("Each path waypoint needs a location")
        location = point["location"]
        geometry_points([location, location])
        index = point.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not last_index <= index < len(geometry):
            raise ValueError("Path indexes must follow embedded geometry order")
        last_index = index
        end = point.get("end_index", index)
        if isinstance(end, bool) or not isinstance(end, int) or not index <= end < len(geometry):
            raise ValueError("Path end_index must be within embedded geometry")
        reference = point.get("reference")
        if reference is not None and (not isinstance(reference, str) or not reference):
            raise ValueError("Path references must be nonempty strings")
    if path[0]["index"] != 0 or path[-1].get("end_index", path[-1]["index"]) != len(geometry) - 1:
        raise ValueError("Path endpoints must cover the embedded geometry")
    for position, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("type") not in {"Manual", "Routed"}:
            raise ValueError("Invalid route segment")
        start, end = segment.get("from"), segment.get("to")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)) or not 0 <= start <= end < len(geometry):
            raise ValueError("Segment ranges must be within embedded geometry")
        expected_start = path[position].get("end_index", path[position]["index"])
        if start != expected_start or end != path[position + 1]["index"]:
            raise ValueError("Segment ranges must connect consecutive path waypoints")
    return geometry


def expected_readback(body):
    requested = {key: body[key] for key in ("id", "type", "status", "sport", "constitution") if key in body}
    path = body.get("path", [])
    if any(isinstance(point, dict) and point.get("reference") is not None for point in path):
        requested["path_references"] = [point.get("reference") for point in path]
    return requested


def compare_readback(requested, tour):
    observed = {key: tour.get(key) for key in requested if key != "path_references"}
    if "path_references" in requested:
        path = tour.get("path")
        observed["path_references"] = (
            [point.get("reference") if isinstance(point, dict) else None for point in path]
            if isinstance(path, list) else None
        )
    mismatches = [key for key, value in requested.items() if observed.get(key) != value]
    return {"verified": not mismatches, "requested": requested, "observed": observed, "mismatches": mismatches}
