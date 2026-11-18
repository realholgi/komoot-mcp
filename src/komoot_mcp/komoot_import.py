"""Import prepared local artifacts and verify private Komoot tours."""

import asyncio
import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from komoot_mcp.artifacts import local_file, read_manifest
from komoot_mcp.gpx_import import import_gpx
from komoot_mcp.gpx_details import GPX
from komoot_mcp.poi_types import get_poi_type
from komoot_mcp import secure_http


def complete_pois(route, gpx_content):
    """Restore omitted map POIs without adding navigation detours."""
    import math

    points = ET.fromstring(gpx_content).findall(f"{{{GPX}}}wpt")
    segments = route.get("segments", [])
    segment_by_start = {id(point): segment for point, segment in zip(route["path"], segments)}
    existing = {p["name"] for p in route["path"] if p.get("reference", "").startswith("cnw:")}
    coordinates = route["_embedded"]["coordinates"]["items"]
    reference = max((int(p["reference"].split(":")[1]) for p in route["path"]
                     if p.get("reference", "").startswith("cnw:")), default=0)
    added = 0
    for point in points:
        name = point.findtext(f"{{{GPX}}}name")
        if name in existing:
            continue
        lat, lng = float(point.get("lat")), float(point.get("lon"))
        scale = math.cos(math.radians(lat))
        index = min(range(len(coordinates)), key=lambda i:
                    (coordinates[i]["lat"] - lat) ** 2 + ((coordinates[i]["lng"] - lng) * scale) ** 2)
        kind = get_poi_type(point.findtext(f"{{{GPX}}}type", default=""))
        if kind is None:
            raise ValueError(f"Cannot assign an unknown POI category for {name!r}")
        reference += 1
        route["path"].append({
            "location": {"lat": lat, "lng": lng}, "index": index, "reference": f"cnw:{reference}",
            "name": name, "description": point.findtext(f"{{{GPX}}}desc", default=""),
            "category": kind.category,
        })
        existing.add(name)
        added += 1
    route["path"].sort(key=lambda point: point["index"])
    if added and segments:
        segment = segments[0]
        rebuilt = []
        for start, end in zip(route["path"], route["path"][1:]):
            segment = segment_by_start.get(id(start), segment)
            rebuilt.append({**segment, "from": start["index"], "to": end["index"]})
        route["segments"] = rebuilt
    return added


async def import_prepared(prepared_path, receipt_path, client):
    """Save tour IDs to an exclusive receipt before verifying account readback."""
    prepared_path = Path(prepared_path).expanduser().resolve()
    prepared = read_manifest(prepared_path)
    receipt = Path(receipt_path).expanduser()
    root = prepared_path.parent
    for route in prepared["routes"]:
        local_file(root, route["gpx"])
        local_file(root, route["details"])
    with receipt.open("x") as journal:
        record = {"schema_version": 1, "prepared_path": str(prepared_path), "tours": []}
        json.dump(record, journal)
        journal.flush()
        for route in prepared["routes"]:
            content = local_file(root, route["gpx"]).read_text()
            imported = await import_gpx(content, client, sport="hike", match_route=False)
            complete_pois(imported, content)
            imported["description"] = route["description"]
            if prepared["comments_coverage"] != "cloud-complete":
                imported["description"] = ("Hinweis: Community-Kommentare und Fotos sind nicht vollständig "
                                           "abgerufen; diese Tour enthält nur lokal verfügbare Angaben.\n\n"
                                           + imported["description"])
            saved = await client.save_planned_tour(imported, route["name"], status="private")
            row = {"route_id": route["id"], "tour_id": saved["id"], "url": f'https://www.komoot.com/tour/{saved["id"]}',
                   "source_pois": len(route["aliases"]), "verification": "pending"}
            record["tours"].append(row)
            journal.seek(0)
            journal.truncate()
            json.dump(record, journal, ensure_ascii=False, indent=2)
            journal.flush()
            actual = await client._http_get_json(
                f'https://api.komoot.de/v007/tours/{int(saved["id"])}',
                params={"_embedded": "coordinates"},
            )
            if actual.get("status") != "private" or actual.get("type") != "tour_planned":
                raise ValueError(f'Tour {saved["id"]} saved but privacy/type verification failed; inspect receipt')
            expected = {p["name"]: p.get("description", "") for p in imported["path"] if p.get("reference", "").startswith("cnw:")}
            actual_points = {p["name"]: p.get("description", "") for p in actual.get("path", []) if p.get("reference", "").startswith("cnw:")}
            if expected != actual_points:
                raise ValueError(f'Tour {saved["id"]} saved but waypoint readback differs; inspect receipt')
            def geometry(data):
                return [(p["lat"], p["lng"], p.get("alt")) for p in data["_embedded"]["coordinates"]["items"]]
            if geometry(actual) != geometry(imported):
                raise ValueError(f'Tour {saved["id"]} saved but geometry readback differs; inspect receipt')
            row.update({"verification": "private-planned-waypoints-verified", "saved_pois": len(actual_points),
                        "dropped_pois": len(route["aliases"]) - len(actual_points)})
            journal.seek(0)
            journal.truncate()
            json.dump(record, journal, ensure_ascii=False, indent=2)
            journal.flush()
    return {"receipt_path": str(receipt.absolute()), **record}


async def update_prepared(receipt_path, prepared_path, client):
    """Update matching source POIs with ETag and geometry readback checks."""
    prepared_path = Path(prepared_path).expanduser().resolve()
    receipt = read_manifest(receipt_path)
    previous = read_manifest(receipt["prepared_path"])
    current = read_manifest(prepared_path)
    old_routes = {str(r["id"]): r for r in previous["routes"]}
    new_routes = {str(r["id"]): r for r in current["routes"]}
    auth = await asyncio.to_thread(client._basic_auth)
    embeds = "coordinates,way_types,surfaces,directions"
    fields = {"id", "status", "type", "name", "description", "distance", "duration", "sport",
              "query", "constitution", "summary", "difficulty", "source", "routing_version",
              "date", "kcal_active", "kcal_resting", "start_point", "time_in_motion",
              "elevation_up", "elevation_down", "tour_information", "custom_tags",
              "path", "segments", "avoid_areas"}
    updated = []
    for saved in receipt["tours"]:
        key = str(saved["route_id"])
        old, new = old_routes[key], new_routes[key]
        if set(old["aliases"]) != set(new["aliases"]):
            raise ValueError("Refresh changes source POI identities; export/import separately")
        tree = ET.parse(local_file(prepared_path.parent, new["gpx"]))
        texts = {p.findtext(f"{{{GPX}}}name"): p.findtext(f"{{{GPX}}}desc", default="")
                 for p in tree.findall(f"{{{GPX}}}wpt")}
        identities = {name: identity for identity, name in old["aliases"].items()}
        url = f'https://api.komoot.de/v007/tours/{int(saved["tour_id"])}'

        async def http(method, **kwargs):
            await client.rl.acquire()
            response = await asyncio.to_thread(secure_http.request, method, url, auth=auth, timeout=60, **kwargs)
            if not response.ok:
                raise ValueError(f"Komoot {method} returned HTTP {response.status_code}; inspect tour before retrying")
            return response

        before = (await http("GET", params={"_embedded": embeds})).json()
        if before.get("status") != "private" or before.get("type") != "tour_planned":
            raise ValueError("Only explicitly imported private planned tours can be updated")
        path = copy.deepcopy(before["path"])
        count = 0
        for point in path:
            if not point.get("reference", "").startswith("cnw:"):
                continue
            identity = identities.get(point.get("name"))
            if identity is None:
                raise ValueError("A stored POI was renamed or is not owned by this export")
            alias = new["aliases"][identity]
            point.update(name=alias, description=texts[alias])
            count += 1
        canonical = await http("GET")
        etag = canonical.headers.get("ETag")
        if not etag or canonical.json().get("changed_at") != before.get("changed_at"):
            raise ValueError("Tour changed during refresh; nothing saved")
        body = {k: v for k, v in before.items() if k in fields}
        body["path"] = [{k: v for k, v in p.items() if k != "referenceKey"} for p in path]
        body["_embedded"] = {
            k: {field: value for field, value in before["_embedded"][k].items()
                if field not in ("_meta", "_links", "page")} for k in embeds.split(",")
        }
        await http("PUT", headers={"Content-Type": "application/hal+json", "If-Match": etag}, json=body)
        after = (await http("GET", params={"_embedded": embeds})).json()
        if after["path"] != path or after["_embedded"]["coordinates"]["items"] != before["_embedded"]["coordinates"]["items"]:
            raise ValueError("Tour was saved but readback differs; inspect before retrying")
        for field in fields - {"path"}:
            if before.get(field) != after.get(field):
                raise ValueError(f"Unexpected saved metadata change: {field}")
        updated.append({"tour_id": saved["tour_id"], "updated_pois": count, "geometry_unchanged": True})
    return {"tours": updated, "photos": "Preserved only in local detail archives; not uploaded"}
