"""Verified import at the standalone prepared-artifact interface."""
import json
import zipfile

import pytest

from komoot_mcp.gpx_details import GPX
from komoot_mcp.komoot_import import complete_pois, import_prepared, update_prepared


def test_omitted_offtrack_poi_retains_position_without_navigation_detour():
    import copy
    coordinates = [{"lat": 48.0, "lng": 7.0, "alt": 100}, {"lat": 48.1, "lng": 7.1, "alt": 101}]
    route = {"path": [{"index": 0, "location": {"lat": 48, "lng": 7}},
                      {"index": 1, "location": {"lat": 48.1, "lng": 7.1}}],
             "_embedded": {"coordinates": {"items": copy.deepcopy(coordinates)}}}
    gpx = f'<gpx xmlns="{GPX}"><wpt lat="48.101" lon="7.102"><name>Source</name><desc>Water uncertain</desc><type>Source</type></wpt></gpx>'
    assert complete_pois(route, gpx) == 1
    assert route["_embedded"]["coordinates"]["items"] == coordinates
    named = next(p for p in route["path"] if p.get("name") == "Source")
    assert named["location"] == {"lat": 48.101, "lng": 7.102}
    assert named["index"] == 1 and named["description"] == "Water uncertain"
    assert named["category"] == 3
    assert complete_pois(route, gpx) == 0


@pytest.mark.asyncio
async def test_import_prepared_saves_private_tour_without_mcp_dispatch(tmp_path):
    import copy

    coordinates = [{"lat": 48.0, "lng": 7.0, "alt": 100}, {"lat": 48.1, "lng": 7.1, "alt": 101}]
    gpx = f'<gpx xmlns="{GPX}"><wpt lat="48.101" lon="7.102"><name>Source</name><desc>Water uncertain</desc><type>Source</type></wpt><trk><trkseg><trkpt lat="48" lon="7"><ele>100</ele></trkpt><trkpt lat="48.1" lon="7.1"><ele>101</ele></trkpt></trkseg></trk></gpx>'
    (tmp_path / "route.gpx").write_text(gpx)
    with zipfile.ZipFile(tmp_path / "details.zip", "w"):
        pass
    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps({
        "schema_version": 1, "comments_coverage": "cache-only",
        "routes": [{"id": "1", "name": "Stage 1", "gpx": "route.gpx",
                    "details": "details.zip", "aliases": {"1:1:42": "Source"},
                    "description": "Source notes"}],
    }))
    persisted = {}

    class Client:
        async def _http_request(self, method, url, **kwargs):
            assert kwargs["data"] == gpx.encode()
            return {"_embedded": {"items": [{"name": "track"}]}}

        async def _komoot_post(self, url, **kwargs):
            assert kwargs["params"]["sport"] == "hike"
            return {"_embedded": {"original": {
                "path": [{"index": 0}, {"index": 1}],
                "segments": [{"type": "Manual", "from": 0, "to": 1}],
                "_embedded": {"coordinates": {"items": copy.deepcopy(coordinates)}},
            }}}

        async def save_planned_tour(self, route, name, status):
            if route.get("segments") and len(route["segments"]) != len(route["path"]) - 1:
                raise ValueError("Planned tour needs either no segments or (path-1) number of segments")
            persisted.update(copy.deepcopy(route))
            persisted.update(id=42, name=name, status=status, type="tour_planned")
            return {"id": 42}

        async def _http_get_json(self, url, params):
            if "custom_named_waypoints" in params.get("_embedded", "").split(","):
                raise ValueError("custom_named_waypoints is not a valid persisted-tour embed")
            return copy.deepcopy(persisted)

    receipt = tmp_path / "receipt.json"
    await import_prepared(str(prepared), str(receipt), Client())
    saved = json.loads(receipt.read_text())["tours"][0]
    assert saved["tour_id"] == 42
    assert saved["saved_pois"] == 1 and saved["dropped_pois"] == 0
    assert saved["verification"] == "private-planned-waypoints-verified"
    assert persisted["status"] == "private" and persisted["name"] == "Stage 1"
    assert persisted["_embedded"]["coordinates"]["items"] == coordinates
    assert persisted["segments"] == [
        {"type": "Manual", "from": 0, "to": 1},
        {"type": "Manual", "from": 1, "to": 1},
    ]
    poi = next(p for p in persisted["path"] if p.get("name") == "Source")
    assert poi["location"] == {"lat": 48.101, "lng": 7.102}
    assert poi["description"] == "Water uncertain" and poi["category"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0, 2, "1", True])
async def test_incompatible_prepared_schema_rejected_before_import(tmp_path, version):
    from unittest.mock import AsyncMock

    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps({"schema_version": version, "routes": []}))
    receipt = tmp_path / "receipt.json"
    client = AsyncMock()
    with pytest.raises(ValueError, match="schema_version 1"):
        await import_prepared(prepared, receipt, client)
    assert not receipt.exists()
    assert client.mock_calls == []


@pytest.mark.asyncio
async def test_incompatible_prepared_schema_rejected_before_update(tmp_path):
    from unittest.mock import AsyncMock

    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema_version": 1, "routes": []}))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schema_version": 1, "prepared_path": str(old), "tours": []}))
    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps({"schema_version": 2, "routes": []}))
    client = AsyncMock()
    with pytest.raises(ValueError, match="schema_version 1"):
        await update_prepared(receipt, prepared, client)
    assert client.mock_calls == []
