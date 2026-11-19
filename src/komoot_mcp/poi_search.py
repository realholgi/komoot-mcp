"""Read ordinary Komoot map POIs without account credentials."""

import re

import requests

from komoot_mcp import secure_http
from komoot_mcp.geocoder import GeocoderError
from komoot_mcp.routing import validate_coordinates


def find_pois_nearby(lat, lng, radius_m=100, limit=20, lang="de"):
    lat, lng = validate_coordinates([[lat, lng]])[0]
    if isinstance(radius_m, bool) or not isinstance(radius_m, int) or not 1 <= radius_m <= 5000:
        raise ValueError("radius_m must be an integer between 1 and 5000")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    try:
        response = secure_http.get(
            "https://www.komoot.com/api/search/reverse-geocoding",
            params={"lat": lat, "lng": lng, "distance": radius_m, "limit": limit, "language": lang},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocoderError(f"Komoot map POI search failed: {exc}") from exc
    embedded = payload.get("_embedded") if isinstance(payload, dict) else None
    items = embedded.get("items") if isinstance(embedded, dict) else None
    if not isinstance(items, list):
        raise GeocoderError("Komoot map POI search returned no item list")
    results = []
    for item in items:
        if not isinstance(item, dict):
            raise GeocoderError("Komoot map POI search returned an invalid item")
        point = item.get("point")
        point = point if isinstance(point, dict) else {}
        poi_id = item.get("poi_id")
        results.append({
            "name": item.get("name"),
            "lat": point.get("y"), "lng": point.get("x"),
            "poi_id": poi_id, "osm_id": item.get("osm_id"),
            "category": item.get("category"),
            "waypoint_details": {"poi_id": poi_id} if poi_id else None,
            "raw": item,
        })
    return {
        "results": results, "returned_count": len(items), "limit": limit,
        "limit_reached": len(items) >= limit, "completeness": "not_guaranteed",
    }


def resolve_poi_candidate(candidate, nearby):
    """Join a Photon candidate to map POIs by its full OSM identity."""
    osm_type = candidate.get("osm_type")
    osm_id = candidate.get("osm_id")
    identity = None
    if (
        isinstance(osm_type, str) and osm_type.lower() in {"n", "w", "r"}
        and not isinstance(osm_id, bool)
        and isinstance(osm_id, (str, int))
        and re.fullmatch(r"[1-9][0-9]*", str(osm_id))
    ):
        identity = f"{osm_type.lower()}{osm_id}"
    matches = [
        item for item in nearby
        if identity is not None and item.get("osm_id") == identity
    ]
    ids = {item["poi_id"] for item in matches if (
        isinstance(item.get("poi_id"), str)
        and re.fullmatch(r"[A-Za-z0-9]+", item["poi_id"])
    )}
    if identity is None:
        status = "missing_osm_identity"
    elif not matches:
        status = "not_found"
    elif len(ids) > 1:
        status = "ambiguous"
    elif not ids:
        status = "missing_poi_id"
    else:
        status = "resolved"
    poi_id = next(iter(ids)) if status == "resolved" else None
    return {
        **candidate,
        "name": candidate.get("display_name"),
        "lng": candidate.get("lon"),
        "category": candidate.get("osm_value"),
        "komoot_osm_id": identity,
        "resolution_status": status,
        "poi_id": poi_id,
        "waypoint_details": {"poi_id": poi_id} if poi_id else None,
        "komoot_matches": matches,
    }
