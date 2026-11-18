"""Read versioned prepared artifacts and publish local export files."""

import json
import os
from pathlib import Path
import tempfile
import math
import xml.etree.ElementTree as ET

from .gpx_details import GPX
from .poi_types import get_poi_type
from .text_limits import utf16_units

from komoot_mcp.context import require_local_filesystem


def read_manifest(path):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"Duplicate manifest key: {key}")
            value[key] = item
        return value

    manifest = json.loads(Path(path).expanduser().read_text(encoding="utf-8"),
                          object_pairs_hook=unique_object)
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Expected a prepared artifact or receipt with schema_version 1")
    return manifest


def local_file(directory, name):
    base = Path(directory).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError("Artifact file must exist inside its manifest directory")
    return path


def validate_prepared(path):
    """Read prepared artifacts and return local findings without changing files."""
    prepared = Path(path).expanduser().resolve()
    manifest = read_manifest(prepared)
    routes = manifest.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("Prepared routes must be a nonempty list")
    if not isinstance(manifest.get("comments_coverage"), str) or not manifest["comments_coverage"]:
        raise ValueError("Prepared comments_coverage must be a nonempty string")
    issues, summaries, seen = [], [], set()

    def issue(code, message, route_id):
        issues.append({"code": code, "message": message, "route_id": route_id})

    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("Prepared routes must be objects")
        identity = route.get("id")
        if type(identity) not in (str, int) or not str(identity):
            raise ValueError("Prepared route IDs must be nonempty strings or integers")
        key = str(identity)
        if key in seen:
            raise ValueError("Prepared route IDs must be unique")
        seen.add(key)
        source = route.get("source_route_id", identity)
        if type(source) not in (str, int) or not str(source):
            raise ValueError("Prepared source route IDs must be nonempty strings or integers")
        for field in ("name", "description", "gpx", "details"):
            if not isinstance(route.get(field), str) or (field != "description" and not route[field]):
                raise ValueError(f"Prepared route {key} requires a string {field}")
        aliases = route.get("aliases")
        if not isinstance(aliases, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in aliases.items()
        ):
            raise ValueError("Prepared aliases must map nonempty source identities to names")
        summary = {"id": identity, "name": route["name"], "source_route_id": source,
                   "source_poi_ids": list(aliases), "poi_count": 0, "track_point_count": 0,
                   "gpx_path": None, "details_path": None, "text_limit_findings": []}
        summaries.append(summary)
        files = {}
        for field in ("gpx", "details"):
            try:
                files[field] = local_file(prepared.parent, route[field])
                summary[f"{field}_path"] = str(files[field])
            except (ValueError, OSError) as exc:
                issue("artifact_path", f"{field}: {exc}", identity)
        if len(aliases) > 512:
            issue("poi_limit", "Komoot routes must be split into at most 512 named POIs", identity)
        if len(set(aliases.values())) != len(aliases):
            issue("alias_identity", "Source POI identities must have unique alias names", identity)
        if "gpx" not in files:
            continue
        try:
            root = ET.parse(files["gpx"]).getroot()
        except (ET.ParseError, OSError, ValueError) as exc:
            issue("invalid_gpx", str(exc), identity)
            continue
        if root.tag != f"{{{GPX}}}gpx":
            issue("invalid_gpx", "Expected a GPX 1.1 document", identity)
            continue
        waypoints = root.findall(f"{{{GPX}}}wpt")
        tracks = root.findall(f".//{{{GPX}}}trkpt")
        summary["track_point_count"] = len(tracks)
        if not tracks:
            issue("invalid_gpx", "Prepared GPX requires track points", identity)
        names = []
        for point in root.iter():
            if point.tag not in {f"{{{GPX}}}wpt", f"{{{GPX}}}trkpt", f"{{{GPX}}}rtept"}:
                continue
            try:
                lat, lon = float(point.get("lat", "")), float(point.get("lon", ""))
                if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError("Coordinates must be finite and within latitude/longitude bounds")
                elevation = point.findtext(f"{{{GPX}}}ele")
                if elevation is not None and not math.isfinite(float(elevation)):
                    raise ValueError("Elevation must be finite")
            except (TypeError, ValueError) as exc:
                issue("invalid_coordinates", str(exc), identity)
        for point in waypoints:
            name = point.findtext(f"{{{GPX}}}name", default="")
            description = point.findtext(f"{{{GPX}}}desc", default="")
            if not name:
                issue("alias_identity", "Every prepared GPX waypoint must have a name", identity)
                continue
            names.append(name)
            if get_poi_type(point.findtext(f"{{{GPX}}}type", default="")) is None:
                issue("poi_category", f"Unknown POI category for {name!r}", identity)
            for field, text, limit in (("name", name, 30), ("description", description, 250)):
                units = utf16_units(text)
                if units > limit:
                    finding = {"name": name, "field": field, "utf16_units": units, "limit": limit}
                    summary["text_limit_findings"].append(finding)
                    issue("text_limit", f"POI {field} exceeds {limit} UTF-16 units: {name!r}", identity)
        summary["poi_count"] = len(names)
        if len(names) > 512 and len(aliases) <= 512:
            issue("poi_limit", "Komoot routes must be split into at most 512 named POIs", identity)
        if len(set(names)) != len(names) or set(names) != set(aliases.values()):
            issue("alias_identity", "Aliases must correspond exactly to unique named GPX waypoints", identity)
    return manifest, {"prepared_path": str(prepared), "comments_coverage": manifest["comments_coverage"],
                      "routes": summaries, "issues": issues}


def atomic_write(path: Path, data: bytes, overwrite: bool) -> None:
    """Publish a complete same-filesystem file without a check/replace race."""
    require_local_filesystem()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_export(save_path: str, default_name: str, data: bytes, overwrite: bool) -> dict:
    path = Path(save_path).expanduser()
    if path.is_dir() or save_path.endswith(("/", "\\")):
        path = path / default_name
    atomic_write(path, data, overwrite)
    return {"path": str(path), "size_bytes": len(data)}
