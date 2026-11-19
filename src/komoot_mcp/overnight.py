"""Shared overnight modes and exact cuts of complete source geometry."""

import bisect
import math
from datetime import date
from typing import Literal, TypeGuard

from komoot_mcp.route_geometry import RouteGeometry, coordinate
from komoot_mcp.route_sections import coordinate_distances

OvernightMode = Literal["tent", "bivouac", "accommodation"]
MODES = frozenset({"tent", "bivouac", "accommodation"})

def finite(value) -> TypeGuard[float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_modes(modes):
    if (not isinstance(modes, list) or not modes
            or any(not isinstance(mode, str) or mode not in MODES for mode in modes)):
        raise ValueError("overnight_modes must be a nonempty list of tent, bivouac, accommodation")
    if len(set(modes)) != len(modes):
        raise ValueError("overnight_modes must not contain duplicates")
    return list(modes)


def validate_date(value):
    if value is not None:
        if not isinstance(value, str):
            raise ValueError("overnight_date must use YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("overnight_date must use YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise ValueError("overnight_date must use YYYY-MM-DD")
    return value

def validate_distance(value, name, minimum: float = 0, maximum: float | None = None):
    if not finite(value) or value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} must be finite and between {minimum} and {maximum or 'the supported limit'}")
    return value


def altitude(point):
    if isinstance(point, dict):
        value = point.get("alt", point.get("altitude", point.get("elevation")))
    else:
        value = point[2] if len(point) > 2 else None
    return float(value) if finite(value) else None


def route_snapshot(tour):
    embedded = tour.get("_embedded", {})
    raw = embedded.get("coordinates") if isinstance(embedded, dict) else None
    raw = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(raw, list) or not 2 <= len(raw) <= 20000:
        raise ValueError("Overnight planning needs 2 to 20000 complete source coordinates")
    points = []
    for point in raw:
        pair = coordinate(point)
        if pair is None:
            raise ValueError("Source route has invalid coordinates")
        points.append([*pair, altitude(point)])
    distances = coordinate_distances(points)
    RouteGeometry(points, distances)
    if not distances or distances[-1] <= 0:
        raise ValueError("Source route must have positive geographic length")
    return points, distances


def point_at(points, distances, km):
    """Interpolate on the sphere and retain a fractional cut's elevation."""
    if km <= 0:
        return list(points[0])
    if km >= distances[-1]:
        return list(points[-1])
    index = bisect.bisect_right(distances, km) - 1
    fraction = (km - distances[index]) / (distances[index + 1] - distances[index])
    start, end = points[index:index + 2]
    a, b = [math.radians(v) for v in start[:2]], [math.radians(v) for v in end[:2]]
    va = (math.cos(a[0]) * math.cos(a[1]), math.cos(a[0]) * math.sin(a[1]), math.sin(a[0]))
    vb = (math.cos(b[0]) * math.cos(b[1]), math.cos(b[0]) * math.sin(b[1]), math.sin(b[0]))
    angle = math.acos(max(-1, min(1, sum(x * y for x, y in zip(va, vb)))))
    if angle < 1e-10:
        vector = va
    else:
        vector = [
            (math.sin((1 - fraction) * angle) * x + math.sin(fraction * angle) * y) / math.sin(angle)
            for x, y in zip(va, vb)
        ]
    elevation = None
    if start[2] is not None and end[2] is not None:
        elevation = start[2] + fraction * (end[2] - start[2])
    return [math.degrees(math.atan2(vector[2], math.hypot(vector[0], vector[1]))),
            math.degrees(math.atan2(vector[1], vector[0])), elevation]


def cut_route(points, distances, start_km: float = 0, end_km: float | None = None):
    end_km = distances[-1] if end_km is None else end_km
    validate_distance(start_km, "start_km", maximum=distances[-1])
    validate_distance(end_km, "end_km", maximum=distances[-1])
    if end_km <= start_km:
        raise ValueError("end_km must be greater than start_km")
    first = bisect.bisect_right(distances, start_km)
    last = bisect.bisect_left(distances, end_km)
    result = [point_at(points, distances, start_km), *points[first:last], point_at(points, distances, end_km)]
    return result, coordinate_distances(result)


def shifted_candidates(result, offset_km):
    for item in result["items"]:
        for encounter in item["encounters"]:
            encounter["route_km"] += offset_km
    if "terrain_search_windows" in result:
        result["terrain_search_windows"] = [
            [start + offset_km, end + offset_km] for start, end in result["terrain_search_windows"]
        ]
    if isinstance(result.get("coverage"), list):
        for coverage in result["coverage"]:
            for key in ("source_start_km", "source_end_km"):
                if key in coverage:
                    coverage[key] += offset_km
    return result
