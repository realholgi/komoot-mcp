"""Search account tour geometries without filtering their start locations."""

import requests

from komoot_mcp.client import KomootAPIError
from komoot_mcp.route_geometry import RouteGeometry

_PAGE_SIZE = 50


def _tour_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return int(text) if text.isascii() and text.isdigit() and int(text) > 0 else None


async def search_tours(client, center, radius_m, tour_type, sport_type, limit, max_tours, offset):
    """Scan a bounded account slice. Report gaps instead of excluding unknown routes."""
    matches = []
    failures = []
    issues = []
    seen = set()
    scanned = 0
    checked = 0
    page, skip = divmod(offset, _PAGE_SIZE)
    next_offset = offset
    exhausted = False
    while scanned < max_tours:
        try:
            result = await client.list_tours(
                page=page, limit=_PAGE_SIZE, tour_type=tour_type,
                sport_type=sport_type, sort_field="date", sort_direction="desc",
            )
        except (KomootAPIError, requests.RequestException) as exc:
            if not scanned:
                raise
            issues.append(f"Tour listing stopped: {exc}")
            break
        records = result.get("tours") if isinstance(result, dict) else None
        if not isinstance(records, list):
            issues.append("Tour listing has no valid tour list.")
            break
        pagination = result.get("page")
        total_pages = pagination.get("totalPages") if isinstance(pagination, dict) else None
        last_page = isinstance(total_pages, int) and not isinstance(total_pages, bool) and 0 <= total_pages <= page + 1
        if not records:
            if isinstance(total_pages, int) and page + 1 < total_pages:
                issues.append("An empty page appeared before the declared end of the account listing.")
            else:
                exhausted = True
            break
        new_ids = 0
        consumed_page = True
        for index, tour in enumerate(records[skip:], start=skip):
            if scanned >= max_tours:
                consumed_page = False
                break
            scanned += 1
            next_offset = page * _PAGE_SIZE + index + 1
            tour_id = _tour_id(tour.get("id")) if isinstance(tour, dict) else None
            if tour_id is None:
                failures.append({"tour_id": None, "reason": "Tour record has no valid ID."})
                continue
            if tour_id in seen:
                continue
            seen.add(tour_id)
            new_ids += 1
            try:
                points = await client.get_tour_coordinates(tour_id)
            except (KomootAPIError, requests.RequestException) as exc:
                failures.append({"tour_id": tour_id, "reason": str(exc)})
                continue
            try:
                nearest = RouteGeometry(points).nearest(center)
            except ValueError as exc:
                failures.append({"tour_id": tour_id, "reason": str(exc)})
                continue
            checked += 1
            if nearest["distance_m"] > radius_m:
                continue
            near = ["route"]
            if nearest["start_distance_m"] <= radius_m:
                near.append("start")
            if nearest["end_distance_m"] <= radius_m:
                near.append("end")
            matches.append({
                **{key: tour[key] for key in (
                    "name", "type", "sport", "status", "distance", "duration",
                    "elevation_up", "elevation_down", "start_point",
                ) if key in tour},
                "id": tour_id, "tour_url": f"https://www.komoot.com/tour/{tour_id}",
                "proximity": {**nearest, "near": near},
            })
        if consumed_page and last_page:
            exhausted = True
            break
        if not new_ids and skip == 0:
            issues.append("Tour listing repeated records; remaining coverage is unknown.")
            break
        if scanned >= max_tours:
            break
        page += 1
        skip = 0
        next_offset = page * _PAGE_SIZE
    matches.sort(key=lambda tour: (tour["proximity"]["distance_m"], tour["id"]))
    return {
        "status": "ok" if offset == 0 and exhausted and not failures and not issues else "partial",
        "center": list(center), "radius_m": radius_m,
        "tour_type": tour_type, "sport_type": sport_type,
        "tours": matches[:limit], "matched_tours": len(matches),
        "results_truncated": len(matches) > limit,
        "scan": {
            "offset": offset, "scanned_records": scanned, "checked_tours": checked,
            "exhausted": exhausted, "next_offset": None if exhausted else next_offset,
            "coverage": "complete" if offset == 0 and exhausted and not failures and not issues else "partial",
            "failures": failures, "issues": issues,
        },
        "distance_method": "nearest_spherical_route_segment_including_endpoints",
        "limits": [
            "Distances are straight-line offsets, not reachable walking detours.",
            "Results rank only the scanned tours; failed geometries remain unknown.",
            "Offsets refer to the account listing sorted by date descending; account edits can shift pages.",
        ],
    }
