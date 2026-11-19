"""Spherical distances from places to complete route segments."""

import math
from numbers import Real

from komoot_mcp.route_sections import coordinate_distances

_EARTH_M = 6371008.8


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def coordinate(point):
    """Read a valid latitude/longitude pair from a native coordinate."""
    if isinstance(point, dict):
        lat, lng = point.get("lat"), point.get("lng")
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        lat, lng = point[:2]
    else:
        return None
    if not (_finite(lat) and _finite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return float(lat), float(lng)


def _vector(point):
    lat, lng = map(math.radians, point)
    return math.cos(lat) * math.cos(lng), math.cos(lat) * math.sin(lng), math.sin(lat)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _angle(a, b):
    cross = _cross(a, b)
    return math.atan2(math.sqrt(_dot(cross, cross)), _dot(a, b))


def _project(point, segment):
    a, b, normal, length = segment
    candidates = [(_angle(point, a), 0.0), (_angle(point, b), 1.0)]
    if normal is not None:
        height = _dot(point, normal)
        projected = tuple(p - height * n for p, n in zip(point, normal))
        norm = math.sqrt(_dot(projected, projected))
        if norm > 1e-15:
            q = tuple(p / norm for p in projected)
            for candidate in (q, tuple(-p for p in q)):
                along = _angle(a, candidate)
                if abs(along + _angle(candidate, b) - length) <= 1e-10:
                    candidates.append((_angle(point, candidate), min(1.0, along / length)))
    return min(candidates)


class RouteGeometry:
    """Prepare a complete route once. Reject gaps and ambiguous antipodal edges."""

    def __init__(self, points, distances=None):
        if not isinstance(points, (list, tuple)) or not points:
            raise ValueError("Route coordinates are missing")
        self.coordinates = []
        for point in points:
            position = coordinate(point)
            if position is None:
                raise ValueError("Route coordinates are invalid")
            self.coordinates.append(position)
        self.vectors = [_vector(point) for point in self.coordinates]
        self.segments = []
        for a, b in zip(self.vectors, self.vectors[1:]):
            cross = _cross(a, b)
            norm = math.sqrt(_dot(cross, cross))
            length = math.atan2(norm, _dot(a, b))
            if math.pi - length < 1e-10:
                raise ValueError("An antipodal route segment has no unique nearest arc")
            self.segments.append((a, b, tuple(v / norm for v in cross) if norm > 1e-15 else None, length))
        if distances is None:
            distances = coordinate_distances(self.coordinates)
        if (not isinstance(distances, (list, tuple)) or len(distances) != len(points)
                or any(not _finite(km) or km < 0 for km in distances)
                or distances[0] != 0 or any(a > b for a, b in zip(distances, distances[1:]))):
            raise ValueError("Cumulative route distances are invalid")
        self.distances = distances

    def nearest(self, point):
        """Locate the nearest point on the route, including both endpoints."""
        position = coordinate(point)
        if position is None:
            raise ValueError("Search coordinates are invalid")
        vector = _vector(position)
        start_distance = _angle(vector, self.vectors[0]) * _EARTH_M
        end_distance = _angle(vector, self.vectors[-1]) * _EARTH_M
        best = (start_distance, 0, 0.0)
        for index, segment in enumerate(self.segments):
            angle, fraction = _project(vector, segment)
            best = min(best, (angle * _EARTH_M, index, fraction))
        distance, index, fraction = best
        km = self.distances[index]
        if self.segments:
            km += fraction * (self.distances[index + 1] - km)
        return {
            "distance_m": distance, "route_km": km,
            "coordinate_index": index, "segment_fraction": fraction,
            "start_distance_m": start_distance, "end_distance_m": end_distance,
        }

    def encounters(self, point, corridor_m):
        """Keep distinct local minima for loops, not duplicate adjacent endpoints."""
        position = coordinate(point)
        if position is None:
            raise ValueError("Search coordinates are invalid")
        vector = _vector(position)
        projections = []
        for index, segment in enumerate(self.segments):
            angle, fraction = _project(vector, segment)
            projections.append((angle * _EARTH_M, self.distances[index] + fraction * (self.distances[index + 1] - self.distances[index])))
        encounters = []
        for index, (offset, km) in enumerate(projections):
            if offset > corridor_m + 1e-6:
                continue
            if index and offset > projections[index - 1][0] + 1e-6:
                continue
            if index + 1 < len(projections) and offset > projections[index + 1][0] + 1e-6:
                continue
            if encounters and abs(km - encounters[-1]["route_km"]) < 0.001:
                if offset < encounters[-1]["offset_m"]:
                    encounters[-1] = {"route_km": km, "offset_m": offset}
            else:
                encounters.append({"route_km": km, "offset_m": offset})
        return encounters
