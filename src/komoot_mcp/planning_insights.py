"""Local geometry diagnostics and stop timing, not access verification."""

import math
from itertools import pairwise
from numbers import Real


def _number(value):
    return (not isinstance(value, bool) and isinstance(value, Real)
            and math.isfinite(value) and float(value) >= 0)


def validate_waypoint_stays(waypoint_stays_s, count):
    if waypoint_stays_s is None:
        return [0] * count
    if (not isinstance(waypoint_stays_s, (list, tuple))
            or len(waypoint_stays_s) != count
            or any(not _number(value) for value in waypoint_stays_s)):
        raise ValueError("waypoint_stays_s must contain one nonnegative finite number per input point")
    return list(waypoint_stays_s)


def _point(value):
    if isinstance(value, dict):
        value = [value.get("lat"), value.get("lng")]
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    lat, lng = value[:2]
    if (isinstance(lat, bool) or not isinstance(lat, Real) or not math.isfinite(lat)
            or isinstance(lng, bool) or not isinstance(lng, Real) or not math.isfinite(lng)):
        return None
    lat, lng = float(lat), float(lng)
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def _distance(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2)
    return 12742000 * math.asin(math.sqrt(min(1, max(0, h))))


def planning_insights(route, coordinates, waypoint_stays_s=None):
    """Report ordered waypoint snaps, geometry leg lengths, and separate stop time.

    Path indices must identify embedded geometry; location-only or nearest-point
    guesses are deliberately not used as evidence of reaching a waypoint.
    """
    stays = validate_waypoint_stays(waypoint_stays_s, len(coordinates))
    moving = route.get("duration")
    moving = moving if _number(moving) else None
    embedded = route.get("_embedded")
    raw_geometry = embedded.get("coordinates") if isinstance(embedded, dict) else None
    raw_geometry = raw_geometry.get("items") if isinstance(raw_geometry, dict) else raw_geometry
    geometry = [_point(p) for p in raw_geometry] if isinstance(raw_geometry, list) else []
    path = route.get("path")
    matched = isinstance(path, list) and len(path) == len(coordinates)
    waypoints = []
    indices = []
    for i, requested in enumerate(coordinates):
        entry = path[i] if matched else None
        index = entry.get("index") if isinstance(entry, dict) else None
        snapped = None
        if (isinstance(index, int) and not isinstance(index, bool)
                and 0 <= index < len(geometry)):
            snapped = geometry[index]
        valid = snapped is not None
        indices.append(index if valid else None)
        distance = _distance(requested, snapped) if snapped is not None else None
        waypoints.append({
            "waypoint_index": i, "requested_coordinate": list(requested),
            "geometry_index": index if valid else None,
            "routed_coordinate": list(snapped) if snapped is not None else None,
            "distance_m": distance,
            "status": "known" if valid else "unknown",
            "unknown_reason": None if valid else ("unmatched_path" if not matched else "missing_or_invalid_geometry_index"),
            "suspicious": distance > 50 if distance is not None else None,
        })
    legs = []
    for i, (a, b) in enumerate(pairwise(coordinates)):
        start, end = indices[i:i + 2]
        straight = _distance(a, b)
        points = geometry[start:end + 1] if start is not None and end is not None and end >= start else []
        valid = bool(points) and all(p is not None for p in points)
        routed = sum(_distance(x, y) for x, y in pairwise(points)) if valid else None
        ratio = routed / straight if routed is not None and straight > 0 else None
        excess = max(0, routed - straight) if routed is not None else None
        legs.append({
            "from_waypoint_index": i, "to_waypoint_index": i + 1,
            "status": "known" if valid else "unknown",
            "unknown_reason": None if valid else "missing_invalid_or_unordered_geometry_indices",
            "straight_line_distance_m": straight, "routed_distance_m": routed,
            "excess_distance_m": excess, "detour_ratio": ratio,
            "ratio_status": "zero_straight_line" if straight == 0 else ("known" if valid else "unknown"),
            "suspicious": (ratio > 3 and excess > 500) if ratio is not None and excess is not None else None,
        })
    return {
        "timing": {"moving_time_s": moving, "stay_time_s": sum(stays),
                   "total_time_s": moving + sum(stays) if moving is not None else None,
                   "waypoint_stays_s": stays,
                   "moving_time_status": "known" if moving is not None else "unknown"},
        "access_diagnostics": {
            "thresholds": {"waypoint_distance_m": 50, "leg_detour_ratio": 3, "leg_excess_distance_m": 500},
            "waypoints": waypoints, "legs": legs,
            "limitation": "These checks do not confirm legal or physical POI access. A leg is suspicious only when it exceeds both detour thresholds. Zero-distance loops have no detour ratio.",
        },
    }
