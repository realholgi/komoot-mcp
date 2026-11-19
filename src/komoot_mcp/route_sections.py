"""Locate native route attributes on cumulative geographic kilometres."""

import math
from bisect import bisect_right
from collections import Counter, defaultdict
from itertools import pairwise
from numbers import Real
from typing import TypeGuard


# Literal English descriptions from Komoot's public v007 documentation.
# Numeric difficulties are native labels, not conversions to SAC/technical grades.
_LABEL_SOURCES = {
    "language": "en",
    "way_types": "https://static.komoot.de/doc/external-api/v007/waytypes.html",
    "surfaces": "https://static.komoot.de/doc/external-api/v007/surfaces.html",
}
_WAY_LABELS = {
    "wt#ferry": "Ferry",
    "wt#alpine_bike_d9": "Trail with difficulty 9",
    "wt#alpine_bike_d8": "Trail with difficulty 8",
    **{f"wt#trail_d{n}": f"Trail with difficulty {n}" for n in range(1, 8)},
    **{f"wt#hike_d{n}": f"Hiking path with difficulty {n}" for n in range(2, 10)},
    "wt#hiking_path": "Hiking path",
    "wt#long_hiking_path": "Long distance hiking path",
    "wt#alpine_hiking_path": "Alpine Hiking Path",
    "wt#track": "Track",
    "wt#way": "Path",
    "wt#minor_road": "Street",
    "wt#street": "Road",
    "wt#primary": "State Road",
    "wt#service": "Access Road",
    "wt#cycleway": "Cycleway",
    "wt#cycle_route": "Long-distance Cycleway",
    "wt#footway": "Footpath",
    "wt#movable_bridge": "Movable Bridge",
    "wt#unknown": "Unknown",
    "wt#off_grid": "Off-grid (unknown)",
}
_SURFACE_LABELS = {
    f"{prefix}#{suffix}": label
    for prefix in ("sb", "sf", "sm")
    for suffix, label in {
        "asphalt": "Asphalt", "concrete": "Concrete", "paved": "Paved",
        "paving_stones": "Paving Stones", "cobblestone": "Cobblestones",
        "compacted": "Compacted Gravel", "gravel": "Gravel",
        "grass_paver": "Grass Paver", "wood": "Wood", "sand": "Sand",
        "ground": "Ground", "stone": "Stone", "unpaved": "Unpaved",
        "alpin": "Alpine", "unknown": "Unknown",
    }.items()
}
_SURFACE_LABELS.update({
    "sb#cobbles": "Cobblestones", "sf#nature": "Natural", "sm#nature": "Natural",
})


def _finite(value) -> TypeGuard[float]:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def validate_range(start_km, end_km):
    """Reject invalid requested ranges before a network request."""
    if not _finite(start_km) or start_km < 0:
        raise ValueError("start_km must be a finite nonnegative number")
    if end_km is not None and (not _finite(end_km) or end_km < start_km):
        raise ValueError("end_km must be finite and at least start_km")


def _items(embedded, name):
    value = embedded.get(name)
    if isinstance(value, dict):
        value = value.get("items")
    return value if isinstance(value, list) else []


def _position(point):
    if isinstance(point, dict):
        lat, lng = point.get("lat"), point.get("lng")
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        lat, lng = point[:2]
    else:
        return None
    if not (_finite(lat) and _finite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return math.radians(lat), math.radians(lng)


def coordinate_distances(points):
    distances = [0.0]
    previous = None
    for point in points:
        position = _position(point)
        if position is None:
            return None
        if previous is not None:
            lat, lng = position
            old_lat, old_lng = previous
            a = math.sin((lat - old_lat) / 2) ** 2 + math.cos(lat) * math.cos(old_lat) * math.sin((lng - old_lng) / 2) ** 2
            distances.append(distances[-1] + 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, a))))
        previous = position
    return distances


def _index(value, size) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < size


def _attribute(active):
    types = sorted(code for code, count in active.items() if count and code is not None)
    known = len(types) == 1 and not active.get(None)
    return {
        "status": "known" if known else "unknown",
        "native_type": types[0] if known else None,
        "label": (_WAY_LABELS.get(types[0]) or _SURFACE_LABELS.get(types[0])) if known else None,
        "candidate_native_types": types,
    }


def analyze_tour_sections(tour, start_km: float = 0, end_km: float | None = None):
    """Join native from/to coordinate spans; never infer terrain from aggregates.

    A span from i to j describes edges i..j-1, not the next edge at j.
    Coordinates are [latitude, longitude, ...] or native lat/lng objects.
    Only documented English descriptions are labels; unknown codes stay null.
    """
    validate_range(start_km, end_km)
    embedded = tour.get("_embedded", {}) if isinstance(tour, dict) else {}
    if not isinstance(embedded, dict):
        embedded = {}
    points = _items(embedded, "coordinates")
    distances = coordinate_distances(points) if points else None
    result = {
        "distance_source": "coordinate_haversine",
        "span_convention": "from inclusive, to exclusive for edges; both are coordinate indices",
        "label_source": dict(_LABEL_SOURCES),
        "total_km": None,
        "requested_range": {"start_km": start_km, "end_km": end_km},
        "range": None,
        "sections": [],
        "unlocated_directions": [],
        "issues": [],
        "coverage": "unknown",
    }
    if distances is None:
        result["issues"].append("Coordinates are missing or invalid; kilometre positions are unknown.")
        result["unlocated_directions"] = _items(embedded, "directions")
        return result
    total = distances[-1]
    start, end = min(start_km, total), min(total if end_km is None else end_km, total)
    result.update(total_km=total, range={"start_km": start, "end_km": end})
    events = defaultdict(list)
    events[0]
    events[len(points) - 1]
    for name in ("way_types", "surfaces"):
        entries = _items(embedded, name)
        if not entries:
            result["issues"].append(f"{name}: native section spans are missing.")
        for number, entry in enumerate(entries):
            if not isinstance(entry, dict):
                result["issues"].append(f"{name}[{number}]: invalid span.")
                continue
            first, last = entry.get("from"), entry.get("to")
            if not (_index(first, len(points)) and _index(last, len(points)) and first < last):
                result["issues"].append(f"{name}[{number}]: invalid coordinate span; not applied.")
                continue
            code = entry.get("element")
            if not isinstance(code, str) or not code:
                code = None
                result["issues"].append(f"{name}[{number}]: native element is missing or invalid.")
            events[first].append((name, code, 1))
            events[last].append((name, code, -1))
    active = {name: Counter() for name in ("way_types", "surfaces")}
    boundaries = sorted(events)
    sections = result["sections"]
    for first, last in pairwise(boundaries):
        for name, code, delta in events[first]:
            active[name][code] += delta
        low, high = max(start, distances[first]), min(end, distances[last])
        if high <= low:
            continue
        way_type, surface = _attribute(active["way_types"]), _attribute(active["surfaces"])
        if sections and sections[-1]["way_type"] == way_type and sections[-1]["surface"] == surface:
            sections[-1]["end_km"] = high
            sections[-1]["coordinate_index_span"]["to"] = last
        else:
            sections.append({
                "start_km": low, "end_km": high,
                "coordinate_index_span": {"from": first, "to": last},
                "way_type": way_type, "surface": surface, "directions": [],
            })
    starts = [section["start_km"] for section in sections]
    for direction in _items(embedded, "directions"):
        index = direction.get("index") if isinstance(direction, dict) else None
        if not _index(index, len(points)):
            result["unlocated_directions"].append(direction)
            continue
        km = distances[index]
        if start <= km <= end and sections:
            section = sections[max(0, bisect_right(starts, km) - 1)]
            section["directions"].append({"km": km, "raw": direction})
        elif start <= km <= end:
            # An empty/zero-length range has no edge section to own the event.
            result["unlocated_directions"].append(direction)
    known = all(s["way_type"]["status"] == s["surface"]["status"] == "known" for s in sections)
    result["coverage"] = "complete" if sections and known and not result["issues"] else "unknown" if not sections else "partial"
    return result
