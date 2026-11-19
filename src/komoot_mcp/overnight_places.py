"""Conservative OSM overnight candidates, never booking or access guarantees."""

import math
from numbers import Real

from komoot_mcp.overnight_geodata import query_osm
from komoot_mcp.route_geometry import RouteGeometry, coordinate

_MODES = ("tent", "bivouac", "accommodation")
_ACCOMMODATION = {
    "hotel", "hostel", "guest_house", "motel", "chalet", "apartment",
    "alpine_hut", "wilderness_hut",
}
_SELECTORS = [
    '["tourism"~"^(camp_site|hotel|hostel|guest_house|motel|chalet|apartment|alpine_hut|wilderness_hut)$"]',
    '["amenity"="shelter"]["shelter_type"~"^(basic_hut|lean_to)$"]',
    '["bivouac"~"^(yes|designated|permissive)$"]',
]
_POSITIVE = {"yes", "designated", "permissive"}
_DETAILS = (
    "website", "contact:website", "phone", "contact:phone", "email", "contact:email",
    "opening_hours", "seasonal", "reservation", "drinking_water", "toilets", "shower",
    "electricity", "fee", "operator", "access", "tents", "cabins", "chalets", "rental",
    "camp_site", "camping", "bivouac", "shelter_type",
)


def _has_units(value):
    return value == "yes" or (isinstance(value, str) and value.isdecimal() and int(value) > 0)


def _modes(tags):
    tourism = tags.get("tourism")
    modes = []
    if tourism == "camp_site" and tags.get("tents") != "no" and tags.get("camping") != "no":
        modes.append("tent")
    if tags.get("bivouac") in _POSITIVE or (
        tourism == "camp_site" and tags.get("camp_site") == "bivouac" and tags.get("bivouac") != "no"
    ):
        modes.append("bivouac")
    # Basic huts describe overnight shelters. Lean-tos need an explicit sleeping tag.
    sleeping_shelter = tags.get("amenity") == "shelter" and (
        tags.get("shelter_type") == "basic_hut"
        or (tags.get("shelter_type") == "lean_to" and (
            tags.get("sleeping") == "yes" or tags.get("overnight") == "yes"
        ))
    )
    rentals = tourism == "camp_site" and (
        _has_units(tags.get("cabins")) or _has_units(tags.get("chalets"))
        or tags.get("rental") in {"cabin", "cabins", "chalet", "chalets", "accommodation"}
    )
    if tourism in _ACCOMMODATION or sleeping_shelter or rentals:
        modes.append("accommodation")
    return modes


def _restriction(tags):
    if tags.get("access") in {"no", "private"}:
        return "prohibited" if tags["access"] == "no" else "restricted"
    if tags.get("bivouac") == "no" and tags.get("tourism") is None:
        return "prohibited"
    if any(tags.get(key) in {"yes", "true", "1"} for key in ("disused", "abandoned", "closed")):
        return "restricted"
    if tags.get("tourism") in {"disused", "abandoned", "closed"} or tags.get("opening_hours") in {"closed", "off"}:
        return "restricted"
    if any(tags.get(f"{state}:{feature}") not in {None, "no", "false", "0"}
           for state in ("disused", "abandoned", "closed") for feature in ("tourism", "amenity")):
        return "restricted"
    return None


def _position(element):
    if element.get("type") == "node":
        return coordinate([element.get("lat"), element.get("lon", element.get("lng"))])
    native_center = element.get("center", {})
    center = coordinate([native_center.get("lat"), native_center.get("lon", native_center.get("lng"))])
    if center is not None:
        return center
    # Full Overpass geometry has no guaranteed entrance. Use an actual geometry vertex.
    geometry = element.get("geometry", [])
    positions = [position for point in geometry if (
        position := coordinate([point.get("lat"), point.get("lon", point.get("lng"))])
    ) is not None]
    if not positions:
        for member in element.get("members", []):
            position = _position(member)
            if position is not None:
                return position
    if positions:
        return positions[len(positions) // 2]
    bounds = element.get("bounds", {})
    lower = coordinate([bounds.get("minlat"), bounds.get("minlon")])
    upper = coordinate([bounds.get("maxlat"), bounds.get("maxlon")])
    if lower is not None and upper is not None:
        return (lower[0] + upper[0]) / 2, (lower[1] + upper[1]) / 2
    return None


async def find_overnight_places(points, distances, overnight_modes, corridor_m: float = 2000, overnight_date=None):
    """Return OR-matched places near the route, retaining source and availability uncertainty.

    Distances are local cumulative kilometres. Date is context only; OSM tags cannot
    establish current/seasonal opening, room availability, or a right to stay.
    """
    if (not isinstance(overnight_modes, (list, tuple)) or not overnight_modes
            or any(mode not in _MODES for mode in overnight_modes)):
        raise ValueError("overnight_modes must explicitly select tent, bivouac or accommodation")
    if not isinstance(corridor_m, Real) or isinstance(corridor_m, bool) or not math.isfinite(corridor_m) or corridor_m <= 0:
        raise ValueError("corridor_m must be positive and finite")
    geometry = RouteGeometry(points, distances)
    response = await query_osm(points, corridor_m, _SELECTORS)
    issues = list(response.get("issues", []))
    status = response["status"]
    items, seen = [], set()
    for element in response.get("elements", []):
        kind, osm_id = element.get("type"), element.get("id")
        if kind not in {"node", "way", "relation"} or not isinstance(osm_id, int) or isinstance(osm_id, bool) or osm_id <= 0:
            issues.append("Ignored an OSM element without a valid identity")
            if status == "ok":
                status = "partial"
            continue
        identity = f"osm:{kind}:{osm_id}"
        if identity in seen:
            continue
        seen.add(identity)
        tags = element.get("tags", {})
        modes = _modes(tags)
        # Keep every supported requested mode, but never silently switch the request.
        modes = [mode for mode in modes if mode in overnight_modes]
        if not modes:
            continue
        restriction = _restriction(tags)
        if restriction:
            issues.append(f"Excluded {identity}: {restriction} according to mapped access/lifecycle tags")
            continue
        position = _position(element)
        if position is None:
            issues.append(f"Cannot locate {identity}: mapped geometry unavailable")
            if status == "ok":
                status = "partial"
            continue
        encounters = geometry.encounters(position, corridor_m)
        if not geometry.segments:
            nearest = geometry.nearest(position)
            if nearest["distance_m"] <= corridor_m:
                encounters = [{"route_km": 0.0, "offset_m": nearest["distance_m"]}]
        if not encounters:
            continue
        candidate_issues = ["Mapped information is not verified; availability and permission to stay require confirmation"]
        if "tent" in modes and tags.get("tents") is None:
            candidate_issues.append("Tent acceptance is unknown: camp_site is mapped but tents is not tagged")
        if kind != "node":
            candidate_issues.append("Area location is representative, not a verified entrance or sleeping pitch")
        permission_tags = {key: tags[key] for key in ("access", "camping", "bivouac", "camp_site") if key in tags}
        designated = ("bivouac" in modes and (
            tags.get("bivouac") in _POSITIVE or tags.get("camp_site") == "bivouac"
        )) or ("tent" in modes and tags.get("camping") in _POSITIVE)
        items.append({
            "id": identity, "name": tags.get("name") or identity, "overnight_modes": modes,
            "lat": position[0], "lng": position[1],
            "source_links": [f"https://www.openstreetmap.org/{kind}/{osm_id}"],
            "source_tags": dict(tags),
            "attribution": "© OpenStreetMap contributors (ODbL)",
            "permission": {"status": "designated" if designated else "unknown", "basis": permission_tags},
            "availability": "unknown", "overnight_date": overnight_date,
            "details": {key: tags.get(key, "unknown") for key in _DETAILS},
            "location_note": "Mapped representative location; entrance and actual detour not verified",
            "issues": candidate_issues, "encounters": encounters,
        })
    items.sort(key=lambda item: (item["encounters"][0]["route_km"], item["id"]))
    return {"items": items, "status": status, "issues": issues,
            "coverage": response.get("coverage", {}), "sources": [response.get("source", {})]}
