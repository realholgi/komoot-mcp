"""Read bounded OSM geometry without account credentials or local files."""

import asyncio
import math
from datetime import datetime, timezone

import requests

from komoot_mcp import secure_http
from komoot_mcp.context import get_rate_limiter
from komoot_mcp.routing import validate_coordinates

_ENDPOINT = "https://overpass-api.de/api/interpreter"
_MAX_POINTS = 20000
_CHUNK_POINTS = 1000
_MAX_ELEMENTS = 100000


async def query_osm(points, radius_m, selectors):
    """Search complete overlapping polyline chunks; expose every failed chunk."""
    points = validate_coordinates(points)
    if not points or len(points) > _MAX_POINTS:
        raise ValueError("OSM search needs 1 to 20000 route points")
    if (isinstance(radius_m, bool) or not isinstance(radius_m, (int, float))
            or not math.isfinite(radius_m) or not 1 <= radius_m <= 5000):
        raise ValueError("OSM search radius must be between 1 and 5000 metres")
    if not selectors or any(not isinstance(selector, str) for selector in selectors):
        raise ValueError("OSM search needs internal tag selectors")
    chunks = [points[start:start + _CHUNK_POINTS]
              for start in range(0, max(1, len(points) - 1), _CHUNK_POINTS - 1)]
    result = {
        "elements": [], "status": "unavailable", "issues": [],
        "coverage": {"planned": len(chunks), "completed": 0, "failed": 0},
        "source": {"name": "OpenStreetMap via Overpass", "url": _ENDPOINT,
                   "attribution": "© OpenStreetMap contributors", "license": "ODbL-1.0",
                   "fetched_at": None, "completeness": "mapped_features_only"},
    }
    seen = set()
    for chunk in chunks:
        line = ",".join(f"{lat:.7f},{lng:.7f}" for lat, lng in chunk)
        queries = "".join(f"nwr{selector}(around:{radius_m:.3f},{line});" for selector in selectors)
        query = f"[out:json][timeout:25][maxsize:33554432];({queries});out geom;"
        try:
            await get_rate_limiter().acquire()
            response = await asyncio.to_thread(
                secure_http.post, _ENDPOINT, data={"data": query}, timeout=35,
                headers={"User-Agent": "komoot-mcp", "Accept": "application/json"},
            )
            response.raise_for_status()
            if len(response.content) > 33554432:
                raise ValueError("OSM geometry response exceeds 32 MiB")
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
                raise ValueError("OSM geometry response has no element list")
            if payload.get("remark"):
                raise ValueError(f"OSM query incomplete: {payload['remark']}")
            elements = payload["elements"]
            if len(elements) + len(seen) > _MAX_ELEMENTS:
                raise ValueError("OSM geometry exceeds the 100000-element budget")
            if any(not isinstance(item, dict) or item.get("type") not in {"node", "way", "relation"}
                   or isinstance(item.get("id"), bool) or not isinstance(item.get("id"), int)
                   for item in elements):
                raise ValueError("OSM geometry response contains invalid identities")
        except (requests.RequestException, ValueError) as exc:
            result["coverage"]["failed"] += 1
            result["issues"].append(str(exc))
            continue
        result["coverage"]["completed"] += 1
        result["source"]["fetched_at"] = datetime.now(timezone.utc).isoformat()
        for element in elements:
            key = element["type"], element["id"]
            if key not in seen:
                seen.add(key)
                result["elements"].append(element)
    if result["coverage"]["completed"]:
        result["status"] = "partial" if result["issues"] else "ok"
    return result


async def query_enclosing_areas(points, selectors):
    """Read generated containing OSM areas; this is not a complete legal inventory."""
    points = validate_coordinates(points)
    if not 1 <= len(points) <= 64:
        raise ValueError("Containing-area lookup needs 1 to 64 candidate centers")
    lookups = "".join(f"is_in({lat:.7f},{lng:.7f});" for lat, lng in points)
    queries = "".join(
        f"way(pivot.areas){selector};rel(pivot.areas){selector};" for selector in selectors
    )
    query = f"[out:json][timeout:25][maxsize:33554432];({lookups})->.areas;({queries});out geom;"
    result = {
        "elements": [], "status": "unavailable", "issues": [],
        "coverage": {"planned": 1, "completed": 0, "failed": 0},
        "source": {"name": "OpenStreetMap containing areas via Overpass", "url": _ENDPOINT,
                   "attribution": "© OpenStreetMap contributors", "license": "ODbL-1.0",
                   "coverage": "Generated OSM areas only; unmapped or non-generated restrictions remain unknown."},
    }
    try:
        await get_rate_limiter().acquire()
        response = await asyncio.to_thread(
            secure_http.post, _ENDPOINT, data={"data": query}, timeout=35,
            headers={"User-Agent": "komoot-mcp", "Accept": "application/json"},
        )
        response.raise_for_status()
        if len(response.content) > 33554432:
            raise ValueError("Containing-area response exceeds 32 MiB")
        payload = response.json()
        if (not isinstance(payload, dict) or not isinstance(payload.get("elements"), list)
                or payload.get("remark") or len(payload["elements"]) > _MAX_ELEMENTS):
            raise ValueError("Containing-area lookup returned incomplete geometry")
        if any(not isinstance(item, dict) or item.get("type") not in {"way", "relation"}
               or not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool)
               for item in payload["elements"]):
            raise ValueError("Containing-area lookup returned invalid identities")
        result["elements"] = payload["elements"]
        result["status"] = "ok"
        result["coverage"]["completed"] = 1
        result["source"]["fetched_at"] = datetime.now(timezone.utc).isoformat()
    except (requests.RequestException, ValueError) as exc:
        result["coverage"]["failed"] = 1
        result["issues"].append(str(exc))
    return result
