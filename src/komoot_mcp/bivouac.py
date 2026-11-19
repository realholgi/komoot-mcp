"""Conservative raster inspection areas, never confirmed or permitted sleeping spots.

Terrarium pixels describe a sampled elevation surface, not ground obstacles or a
sleeping footprint. All network and grid budgets fail explicitly; incomplete OSM
exclusions fail closed. No route to an inspection area is assessed.
"""

import asyncio
import math
from collections import deque
from io import BytesIO
from itertools import pairwise

import requests
from PIL import Image

from komoot_mcp import secure_http
from komoot_mcp.overnight_geodata import query_enclosing_areas, query_osm
from komoot_mcp.route_geometry import RouteGeometry

_ZOOM = 15
_WORLD = 256 * 2 ** _ZOOM
_EARTH_M = 6371008.8
_MAX_CELLS = 120_000
_MAX_TILES = 24
_MAX_GEOMETRY_WORK = 8_000_000
_MAX_OSM_VERTICES = 80_000
_SELECTORS = [
    '["highway"]', '["building"]', '["natural"~"^(water|wetland)$"]',
    '["waterway"]', '["landuse"~"^(reservoir|basin|military)$"]',
    '["access"~"^(private|no)$"]', '["boundary"~"^(protected_area|national_park)$"]',
    '["leisure"="nature_reserve"]', '["protect_class"]',
    '["camping"="no"]', '["bivouac"="no"]',
]
_SOURCE = {
    "name": "AWS Terrain Tiles / Terrarium", "url": "https://registry.opendata.aws/terrain-tiles/",
    "tile_url_template": "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/15/{x}/{y}.png",
    "zoom": _ZOOM, "dem_source_resolution_m": "unknown",
    "note": "Raster spacing is not the resolution or accuracy of the underlying DEM.",
}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pixel(lat, lng):
    return ((lng + 180) / 360 * _WORLD,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * _WORLD)


def _position(x, y):
    return (math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / _WORLD)))),
            x / _WORLD * 360 - 180)


def _spacing(lat):
    return 2 * math.pi * _EARTH_M * math.cos(math.radians(lat)) / _WORLD


def _decode_tile(content):
    """Reject malformed images and common void encodings rather than flattening them."""
    with Image.open(BytesIO(content)) as image:
        if image.format != "PNG" or image.size != (256, 256) or image.mode not in ("RGB", "RGBA"):
            raise ValueError("DEM tile must be a 256x256 RGB/RGBA PNG")
        data = image.tobytes()
        channels = len(image.getbands())
        pixels = [data[index:index + channels] for index in range(0, len(data), channels)]
    values = []
    for pixel in pixels:
        r, g, b = pixel[:3]
        height = r * 256 + g + b / 256 - 32768
        # Terrarium has no universal validity mask. Reject transparent pixels,
        # impossible terrestrial heights and the common zero/white void values.
        valid = (len(pixel) == 3 or pixel[3] == 255) and -500 <= height <= 9000
        values.append(height if valid else None)
    return values


def _fetch_tile(x, y):
    url = f"https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{_ZOOM}/{x}/{y}.png"
    response = secure_http.get(url, timeout=20, stream=True)
    try:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 1_048_576:
                raise ValueError("DEM tile exceeds byte budget")
            chunks.append(chunk)
        return _decode_tile(b"".join(chunks))
    finally:
        response.close()


def _xy(point, origin):
    lat, lng = point
    return (math.radians(lng - origin[1]) * _EARTH_M * math.cos(math.radians(origin[0])),
            math.radians(lat - origin[0]) * _EARTH_M)


def _segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator)) if denominator else 0
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


def _inside(point, ring):
    x, y = point
    inside = False
    for a, b in pairwise(ring):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def _exclusions(elements, origin):
    """Use relation envelopes conservatively, including holes and member gaps."""
    paths, obstacles = [], []
    vertices = 0
    for element in elements:
        tags = element.get("tags", {})
        highway = "highway" in tags
        geometry = element.get("geometry")
        if element.get("type") == "node":
            geometry = [{"lat": element.get("lat"), "lon": element.get("lon")}]
        if element.get("type") == "relation":
            geometry = [point for member in element.get("members", []) for point in member.get("geometry", [])]
            bounds = element.get("bounds")
            if bounds and all(_finite(bounds.get(key)) for key in ("minlat", "maxlat", "minlon", "maxlon")):
                geometry = [{"lat": bounds[lat], "lon": bounds[lon]} for lat, lon in (
                    ("minlat", "minlon"), ("minlat", "maxlon"), ("maxlat", "maxlon"),
                    ("maxlat", "minlon"), ("minlat", "minlon"))]
            elif geometry and not highway:
                lats, lngs = [p.get("lat") for p in geometry], [p.get("lon") for p in geometry]
                if not all(_finite(v) for v in lats + lngs):
                    raise ValueError("Malformed exclusion relation")
                geometry = [{"lat": lat, "lon": lng} for lat, lng in (
                    (min(lats), min(lngs)), (min(lats), max(lngs)), (max(lats), max(lngs)),
                    (max(lats), min(lngs)), (min(lats), min(lngs)))]
            if highway:
                # Joining unrelated relation members would invent path segments.
                raise ValueError("Highway relation geometry cannot be verified")
        if not geometry or any(not _finite(p.get("lat")) or not _finite(p.get("lon"))
                               or abs(p["lat"]) > 80 or abs(p["lon"]) >= 180 for p in geometry):
            raise ValueError("Missing or unsupported OSM exclusion geometry")
        vertices += len(geometry)
        if vertices > _MAX_OSM_VERTICES:
            raise ValueError("OSM exclusion vertex budget exceeded")
        line = [_xy((p["lat"], p["lon"]), origin) for p in geometry]
        if not highway and "waterway" not in tags and (len(line) < 4 or line[0] != line[-1]):
            raise ValueError("Mapped exclusion has no complete polygon footprint")
        if highway:
            if tags.get("area") == "yes":
                if len(line) < 4 or line[0] != line[-1]:
                    raise ValueError("Mapped highway area has no complete polygon footprint")
                obstacles.append((line, True))
            paths.append(line)
        else:
            # Non-highway selector results are all excluded. Closed ways are
            # filled; waterways and isolated nodes receive a conservative buffer.
            obstacles.append((line, len(line) >= 4 and line[0] == line[-1]))
    return paths, obstacles, vertices


def _blocked(position, paths, obstacles, clearance, margin):
    for line in paths:
        if len(line) == 1 and math.dist(position, line[0]) < clearance + margin:
            return True
        if any(_segment_distance(position, a, b) < clearance + margin for a, b in pairwise(line)):
            return True
    for line, polygon in obstacles:
        if polygon and _inside(position, line):
            return True
        if len(line) == 1 and math.dist(position, line[0]) <= margin:
            return True
        if any(_segment_distance(position, a, b) <= margin for a, b in pairwise(line)):
            return True
    return False


async def find_bivouac_candidates(points, distances, *, min_path_distance_m: float = 30,
                                  max_path_distance_m: float = 150, max_slope_deg: float = 5,
                                  min_area_m2: float = 6, persons=1):
    """Return contiguous, sampled inspection patches within the route corridor.

    ``min_area_m2 * persons`` filters raster patch area only. Usable sleeping
    area, permission, ground cover, access and current hazards remain unknown.
    Raster centers require a complete 3x3 elevation neighborhood. Work beyond
    fixed budgets is refused, not silently sampled or truncated.
    """
    if (not all(_finite(v) for v in (min_path_distance_m, max_path_distance_m, max_slope_deg, min_area_m2))
            or min_path_distance_m < 0 or max_path_distance_m <= min_path_distance_m
            or not 0 <= max_slope_deg < 90 or min_area_m2 <= 0
            or not isinstance(persons, int) or isinstance(persons, bool) or persons < 1):
        raise ValueError("Invalid bivouac terrain thresholds")
    route = RouteGeometry(points, distances)
    coverage = {"complete": False, "grid_budget_cells": _MAX_CELLS, "tile_budget": _MAX_TILES}
    result = {"items": [], "status": "unavailable", "issues": [], "coverage": coverage, "sources": [_SOURCE.copy()]}
    coords = route.coordinates
    if (any(abs(lat) > 80 or abs(lng) >= 179.9 for lat, lng in coords)
            or max(lng for _, lng in coords) - min(lng for _, lng in coords) > 1
            or max(lat for lat, _ in coords) - min(lat for lat, _ in coords) > 1):
        result["issues"].append("Terrain search unsupported near the antimeridian, above 80 degrees latitude, or across more than one degree.")
        return result
    pixels = [_pixel(*point) for point in coords]
    minimum_spacing = min(_spacing(lat) for lat, _ in coords)
    padding = math.ceil(max_path_distance_m / minimum_spacing * 1.02) + 3
    x0, x1 = math.floor(min(x for x, _ in pixels)) - padding, math.ceil(max(x for x, _ in pixels)) + padding
    y0, y1 = math.floor(min(y for _, y in pixels)) - padding, math.ceil(max(y for _, y in pixels)) + padding
    count = (x1 - x0 + 1) * (y1 - y0 + 1)
    tiles = [(x, y) for x in range((x0 - 1) // 256, (x1 + 1) // 256 + 1)
             for y in range((y0 - 1) // 256, (y1 + 1) // 256 + 1)]
    coverage.update({"requested_grid_cells": count, "requested_tiles": len(tiles)})
    if count > _MAX_CELLS or len(tiles) > _MAX_TILES or count * max(1, len(coords)) > _MAX_GEOMETRY_WORK:
        result["issues"].append("Terrain grid, tile or route geometry work budget exceeded; use a smaller route window.")
        return result
    # Around the complete route, not just its vertices or envelope corners:
    # include every possible cell and the required clearance to mapped paths.
    osm = await query_osm([list(p) for p in coords],
                          max_path_distance_m + min_path_distance_m + 30, _SELECTORS)
    result["sources"].append(osm.get("source", {}))
    coverage["osm"] = osm.get("coverage", {})
    if osm.get("status") != "ok":
        result["issues"] = list(osm.get("issues", [])) + ["OSM exclusions incomplete; no terrain candidates returned."]
        return result
    origin = coords[0]
    try:
        paths, obstacles, vertices = _exclusions(osm["elements"], origin)
    except (ValueError, KeyError, TypeError) as exc:
        result["issues"].append(f"OSM exclusions unavailable: {exc}")
        return result
    if count * max(1, vertices) > _MAX_GEOMETRY_WORK:
        result["issues"].append("OSM exclusion evaluation budget exceeded; use a smaller route window.")
        return result
    raster = {}
    failed = 0
    for tile in tiles:
        try:
            raster[tile] = await asyncio.to_thread(_fetch_tile, *tile)
        except (requests.RequestException, OSError, ValueError) as exc:
            failed += 1
            result["issues"].append(f"DEM tile {tile[0]}/{tile[1]} unavailable: {type(exc).__name__}")
    coverage.update({"loaded_tiles": len(raster), "failed_tiles": failed})
    if not raster:
        return result

    def elevation(x, y):
        values = raster.get((x // 256, y // 256))
        return values[(y % 256) * 256 + x % 256] if values is not None else None

    eligible = {}
    missing = 0
    spacings = []
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            lat, lng = _position(x + 0.5, y + 0.5)
            source_offset = route.nearest((lat, lng))["distance_m"]
            spacing = _spacing(lat)
            spacings.append(spacing)
            margin = spacing * math.sqrt(2) / 2 + 2  # include cell edges and projection uncertainty
            if not min_path_distance_m + margin <= source_offset <= max_path_distance_m - margin:
                continue
            if _blocked(_xy((lat, lng), origin), paths, obstacles, min_path_distance_m, margin):
                continue
            neighborhood = {(dx, dy): elevation(x + dx, y + dy)
                            for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            if any(v is None or not _finite(v) for v in neighborhood.values()):
                missing += 1
                continue
            # Test all neighboring pairs in the 3x3 support, including diagonals;
            # testing the center gradient alone misses peaks and shallow bowls.
            slope = 0.0
            for (dx, dy), height in neighborhood.items():
                for sx, sy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                    other = neighborhood.get((dx + sx, dy + sy))
                    if other is not None:
                        slope = max(slope, math.degrees(math.atan(abs(height - other) / (spacing * math.hypot(sx, sy)))))
            if slope <= max_slope_deg:
                eligible[(x, y)] = (spacing, slope)
    coverage.update({"missing_elevation_neighborhoods": missing, "eligible_raster_cells": len(eligible),
                     "raster_spacing_m": {"min": min(spacings), "max": max(spacings)} if spacings else None})
    if missing:
        result["issues"].append("Missing, void or invalid DEM neighborhoods omitted; terrain coverage is partial.")
    remaining = set(eligible)
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        queue, patch = deque([seed]), []
        while queue:
            cell = queue.popleft()
            patch.append(cell)
            x, y = cell
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        area = sum(eligible[cell][0] ** 2 for cell in patch)
        if area < min_area_m2 * persons:
            continue
        # Choose a real accepted cell, never the centroid (which can be excluded).
        midpoint = pixels[len(pixels) // 2]
        x, y = min(patch, key=lambda cell: (eligible[cell][1], math.dist(cell, midpoint), cell))
        lat, lng = _position(x + 0.5, y + 0.5)
        southwest = _position(min(c[0] for c in patch), max(c[1] for c in patch) + 1)
        northeast = _position(max(c[0] for c in patch) + 1, min(c[1] for c in patch))
        result["items"].append({
            "id": f"terrarium:{_ZOOM}:{seed[0]}:{seed[1]}", "name": "Bivouac terrain inspection area",
            "overnight_modes": ["bivouac"], "lat": lat, "lng": lng,
            "kind": "terrain_bivouac", "access_kind": "unverified_off_path",
            "source_track_distance_m": route.nearest((lat, lng))["distance_m"],
            "source_links": [_SOURCE["url"], "https://www.openstreetmap.org/copyright"],
            "permission": {"status": "unknown", "basis": "No mapped exclusion found; this does not establish permission or complete protected-area coverage."},
            "availability": "unknown", "encounters": (
                route.encounters((lat, lng), max_path_distance_m) if route.segments else
                [{"route_km": 0.0, "offset_m": route.nearest((lat, lng))["distance_m"]}]),
            "area_bounds": {"south": southwest[0], "west": southwest[1], "north": northeast[0], "east": northeast[1]},
            "terrain": {"raster_patch_area_m2": area, "raster_cells": len(patch),
                        "max_sampled_slope_deg": max(eligible[c][1] for c in patch),
                        "raster_spacing_m": {"min": min(eligible[c][0] for c in patch), "max": max(eligible[c][0] for c in patch)},
                        "dem_source_resolution_m": "unknown", "usable_sleeping_area_m2": "unknown"},
            "issues": ["Inspection area only, not a confirmed sleeping footprint; bounds may include excluded holes.",
                       "Straight-line approach unverified; no safe or lawful route access established.",
                       "Ground cover, rocks, drainage, flood risk, current hazards and bivouac permission are unknown.",
                       "Mapped exclusions and DEM sampling cannot establish exhaustive real-world coverage."],
        })
    if result["items"]:
        if len(result["items"]) > 64:
            result["items"] = []
            result["issues"].append("Containing-area lookup exceeds 64-patch budget; use a smaller route window.")
            return result
        enclosing = await query_enclosing_areas(
            [[item["lat"], item["lng"]] for item in result["items"]],
            [selector for selector in _SELECTORS if selector not in ('["highway"]', '["waterway"]')])
        result["sources"].append(enclosing.get("source", {}))
        coverage["enclosing_areas"] = enclosing.get("coverage", {})
        if enclosing.get("status") != "ok":
            result["items"] = []
            result["issues"].extend(enclosing.get("issues", []))
            result["issues"].append("Containing-area exclusions incomplete; no terrain candidates returned.")
            return result
        try:
            _, containing, _ = _exclusions(enclosing["elements"], origin)
        except (ValueError, KeyError, TypeError) as exc:
            result["items"] = []
            result["issues"].append(f"Containing-area geometry unavailable: {exc}")
            return result
        result["items"] = [
            item for item in result["items"]
            if not _blocked(_xy((item["lat"], item["lng"]), origin), [], containing, 0,
                            item["terrain"]["raster_spacing_m"]["max"] + 2)]
    coverage["restriction_scope"] = "Mapped intersecting features and Overpass-generated containing areas; not exhaustive legal or land-ownership coverage."
    coverage["complete"] = not failed and not missing
    result["status"] = "partial" if failed or missing else "ok"
    return result
