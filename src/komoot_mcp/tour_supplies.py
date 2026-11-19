"""Read Komoot supply candidates along a complete spherical route."""

import asyncio
import math
import re
from datetime import datetime, timezone
from numbers import Real
from urllib.parse import urlencode

import requests

from komoot_mcp.context import get_rate_limiter
from komoot_mcp.geocoder import GeocoderError
from komoot_mcp.poi_search import find_pois_nearby
from komoot_mcp.route_geometry import RouteGeometry, coordinate
from komoot_mcp.supply_categories import CATEGORY_LABELS, CATEGORY_SOURCE, KNOWN_CATEGORIES, SUPPLY_CATEGORIES

_ENDPOINT = "https://www.komoot.com/api/search/reverse-geocoding"
_EARTH_M = 6371008.8
_MAX_SEARCHES = 200


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _search_centers(geometry, spacing_m):
    """Sample physical arc length, not vertex count or supplied distance labels."""
    lengths = [segment[3] * _EARTH_M for segment in geometry.segments]
    total = sum(lengths)
    required = math.ceil(total / spacing_m) + 1 if total else 1
    targets = (min(index * spacing_m, total) for index in range(required))
    centers = {}
    segment_index, start = 0, 0.0
    for target in targets:
        while segment_index < len(lengths) - 1 and target > start + lengths[segment_index]:
            start += lengths[segment_index]
            segment_index += 1
        a, _, normal, angle = geometry.segments[segment_index]
        if target == 0:
            point = geometry.coordinates[0]
        elif target == total:
            point = geometry.coordinates[-1]
        elif normal is None:
            point = geometry.coordinates[segment_index]
        else:
            fraction = (target - start) / lengths[segment_index]
            # Rotate the start vector within its great-circle plane. Unlike
            # linear latitude/longitude interpolation this crosses the dateline.
            tangent = (
                normal[1] * a[2] - normal[2] * a[1],
                normal[2] * a[0] - normal[0] * a[2],
                normal[0] * a[1] - normal[1] * a[0],
            )
            turn = angle * fraction
            vector = tuple(math.cos(turn) * x + math.sin(turn) * y for x, y in zip(a, tangent))
            point = (
                math.degrees(math.atan2(vector[2], math.hypot(vector[0], vector[1]))),
                math.degrees(math.atan2(vector[1], vector[0])),
            )
        key = (round(point[0], 12), round((point[1] + 180) % 360 - 180, 12))
        centers.setdefault(key, point)
        if len(centers) > _MAX_SEARCHES:
            return None, len(centers)
    return list(centers.values()), required


def _identity(candidate):
    poi_id = candidate.get("poi_id")
    if isinstance(poi_id, str) and poi_id.strip():
        return "poi", poi_id
    osm_id = candidate.get("osm_id")
    if isinstance(osm_id, str) and re.fullmatch(r"[nwr][1-9][0-9]*", osm_id):
        return "osm", osm_id
    return None


async def find_tour_supplies(points, distances, corridor_m=300) -> dict:
    """Find candidates, not verified entrances, opening times or drinking safety.

    Overlapping circles cover the full spherical route corridor by the triangle
    bound. Komoot's result limits and unknown categories remain explicit; even an
    OK response does not promise a complete inventory.
    """
    if not _finite(corridor_m) or not 1 <= corridor_m <= 1000:
        raise ValueError("corridor_m must be finite and between 1 and 1000")
    radius_m = max(1000, math.ceil(2 * corridor_m))
    coverage = {
        "planned": 0, "completed": 0, "capped": 0, "failed": 0,
        "unknown_categories": 0, "radius_m": radius_m,
        "center_spacing_m": radius_m - corridor_m,
    }
    result = {
        "status": "unavailable", "items": [],
        "source": {
            "name": "Komoot map POIs", "url": "https://www.komoot.com/",
            "endpoint": _ENDPOINT, "category_source": CATEGORY_SOURCE,
            "completeness": "not_guaranteed",
            "coverage": "Overlapping searches along the full route; capped results and unmapped places can hide candidates.",
        },
        "fetched_at": None, "issues": [], "coverage": coverage,
        "unknown_categories": [],
    }
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        result["issues"].append("A route needs at least two valid coordinates.")
        return result
    if len(points) > 20000:
        result["issues"].append("Route exceeds 20000 coordinates; no truncated query was sent.")
        return result
    if distances is None:
        result["issues"].append("Cumulative route distances are missing.")
        return result
    try:
        geometry = RouteGeometry(points, distances)
    except ValueError as exc:
        result["issues"].append(str(exc))
        return result
    centers, required = _search_centers(geometry, radius_m - corridor_m)
    if centers is None:
        coverage["planned"] = required
        result["issues"].append(
            f"Full-route sampling requires at least {required} distinct circles, exceeding the {_MAX_SEARCHES}-request budget; no searches sent."
        )
        return result
    coverage["planned"] = len(centers)
    seen = set()
    seen_unknown = set()
    limiter = get_rate_limiter()
    malformed = unresolved = 0
    for lat, lng in centers:
        search_url = f"{_ENDPOINT}?{urlencode({'lat': lat, 'lng': lng, 'distance': radius_m, 'limit': 100, 'language': 'de'})}"
        try:
            await limiter.acquire()
            payload = await asyncio.to_thread(find_pois_nearby, lat, lng, radius_m=radius_m, limit=100, lang="de")
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("Komoot POI response has no valid results list")
            candidates = payload["results"]
        except (GeocoderError, requests.RequestException, ValueError) as exc:
            coverage["failed"] += 1
            result["issues"].append(f"Komoot supply lookup at {lat},{lng} failed: {exc}")
            continue
        result["fetched_at"] = datetime.now(timezone.utc).isoformat()
        if payload.get("limit_reached") is True or len(candidates) >= 100:
            coverage["capped"] += 1
        valid = 0
        for candidate in candidates:
            point = coordinate(candidate) if isinstance(candidate, dict) else None
            if point is None:
                malformed += 1
                continue
            category = candidate.get("category")
            valid += 1
            identity = _identity(candidate)
            if not isinstance(category, int) or isinstance(category, bool) or category not in KNOWN_CATEGORIES:
                if identity is not None:
                    if identity in seen_unknown:
                        continue
                    seen_unknown.add(identity)
                coverage["unknown_categories"] += 1
                if category not in result["unknown_categories"]:
                    result["unknown_categories"].append(category)
                continue
            if category not in SUPPLY_CATEGORIES:
                continue
            encounters = geometry.encounters(point, corridor_m)
            if not encounters:
                continue
            if identity is not None:
                if identity in seen:
                    continue
                seen.add(identity)
            if identity is None:
                unresolved += 1
            nearest = min(encounters, key=lambda encounter: encounter["offset_m"])
            osm_id = candidate.get("osm_id")
            osm_type = osm_number = osm_url = None
            if isinstance(osm_id, str) and re.fullmatch(r"[nwr][1-9][0-9]*", osm_id):
                osm_type = {"n": "node", "w": "way", "r": "relation"}[osm_id[0]]
                osm_number = int(osm_id[1:])
                osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_number}"
            item = {
                "poi_id": candidate.get("poi_id"), "osm_id": osm_id,
                "osm_type": osm_type, "osm_number": osm_number, "osm_url": osm_url,
                "identity_status": "resolved" if identity else "unresolved",
                "name": candidate.get("name"), "category": category, "native_category": category,
                "category_label": CATEGORY_LABELS.get(category),
                "categories": [SUPPLY_CATEGORIES[category]],
                "lat": point[0], "lng": point[1], "point": {"lat": point[0], "lng": point[1]},
                "waypoint_details": candidate.get("waypoint_details"),
                "raw": candidate.get("raw"),
                "source_links": [search_url] + ([osm_url] if osm_url else []),
                "location_source": "komoot_poi_point",
                "location_note": "Komoot representative point; entrance not verified.",
                "route_km": nearest["route_km"], "offset_m": nearest["offset_m"],
                "route_km_candidates": [encounter["route_km"] for encounter in encounters],
                "encounters": encounters,
                "route_position_status": "multiple_encounters" if len(encounters) > 1 else "single_encounter",
                "offset_kind": "straight_line_to_representative_point", "actual_detour": "unknown",
                "availability": "unverified", "open_now": "unknown", "access": "unknown",
                "drinking_safety": "unverified", "opening_hours": None,
            }
            result["items"].append(item)
        if candidates and not valid:
            coverage["failed"] += 1
        else:
            coverage["completed"] += 1
    if coverage["capped"]:
        result["issues"].append(f"{coverage['capped']} Komoot searches reached the 100-result cap; candidates may be hidden.")
    if coverage["unknown_categories"]:
        result["issues"].append(f"{coverage['unknown_categories']} unique identified POIs or unidentified observations have unknown native categories.")
    if malformed:
        result["issues"].append(f"Skipped {malformed} malformed Komoot POI records.")
    if unresolved:
        result["issues"].append(f"{unresolved} supply observations lack a complete source identity and cannot be deduplicated.")
    result["items"].sort(key=lambda item: (item["route_km"], str(item["poi_id"]), str(item["osm_id"])))
    result["status"] = "unavailable" if not coverage["completed"] else "partial" if result["issues"] else "ok"
    return result
