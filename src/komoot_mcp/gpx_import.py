"""Import GPX content through Komoot without MCP dispatch."""

from collections import deque

import gpxpy

from komoot_mcp.client import KomootAPIError
from komoot_mcp.text_limits import MAX_DESCRIPTION_UNITS, utf16_units
from komoot_mcp.poi_types import get_poi_type

_WWW = "https://www.komoot.com/api"
_EMBEDS = "coordinates,way_types,surfaces,directions,custom_named_waypoints"


async def import_gpx(gpx_content, client, sport="hike", track_index=0, match_route=True):
    gpx = gpxpy.parse(gpx_content)
    # File import projects POIs onto the track. Identify categories by source name.
    categories_by_name = {}
    for point in gpx.waypoints:
        kind = get_poi_type(point.type)
        category = kind.category if kind else None
        categories_by_name.setdefault(point.name or "", set()).add(category)
    if any(len(categories) > 1 for categories in categories_by_name.values()):
        raise ValueError("GPX POIs sharing a name have conflicting categories; use distinct names")
    categories_by_name = {
        name: next(iter(categories)) for name, categories in categories_by_name.items()
    }
    imported = await client._http_request(
        "POST", f"{_WWW}/routing/import/files/",
        data=gpx_content.encode("utf-8"),
        headers={"Content-Type": "application/gpx+xml"},
        params={"data_type": "gpx"},
    )
    items = (imported.get("_embedded") or {}).get("items") if isinstance(imported, dict) else None
    if not isinstance(items, list) or not 0 <= track_index < len(items):
        raise KomootAPIError("GPX import did not contain the selected track")
    track = items[track_index]
    named_points = (track.get("_embedded") or {}).get("custom_named_waypoints", [])
    oversized = [
        point for point in named_points
        if utf16_units(point.get("description", "")) > MAX_DESCRIPTION_UNITS
    ]
    if oversized:
        raise KomootAPIError(
            f"{len(oversized)} POI descriptions exceed Komoot's 250-character storage limit; "
            "use prepare_descriptions=True with a sampling-capable MCP client"
        )
    route = await client._komoot_post(
        f"{_WWW}/routing/import/tour", json_body=track,
        params={"sport": sport, "_embedded": _EMBEDS},
    )
    variant = "matched" if match_route else "original"
    selected_route = (route.get("_embedded") or {}).get(variant) if isinstance(route, dict) else None
    if not isinstance(selected_route, dict) or not selected_route.get("path"):
        raise KomootAPIError(f"Komoot returned no {variant} route with a path")
    named_by_position = {}
    for named in (selected_route.get("_embedded") or {}).get("custom_named_waypoints", []):
        key = (named["index"], round(named["lat"], 6), round(named["lng"], 6))
        named_by_position.setdefault(key, deque()).append(named)
    for waypoint in selected_route["path"]:
        if not waypoint.get("reference", "").startswith("cnw:"):
            continue
        location = waypoint["location"]
        key = (waypoint["index"], round(location["lat"], 6), round(location["lng"], 6))
        candidates = named_by_position.get(key)
        if not candidates:
            raise KomootAPIError("A custom waypoint reference has no matching name/description")
        named = candidates.popleft()
        waypoint.update({field: named[field] for field in ("name", "description", "category") if field in named})
        category = categories_by_name.get(named.get("name") or "")
        if category is not None:
            waypoint["category"] = category
            named["category"] = category
    if any(named_by_position.values()):
        raise KomootAPIError("Imported custom waypoint metadata has no matching path entry")
    return selected_route
