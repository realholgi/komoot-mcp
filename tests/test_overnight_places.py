"""Offline OSM overnight classification retains uncertainty and route encounters."""

from unittest.mock import AsyncMock

import pytest

from komoot_mcp import overnight_places
from komoot_mcp.route_sections import coordinate_distances


def _place(osm_id=1, kind="node", lat=0, lon=0.01, **tags):
    return {"type": kind, "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


async def _find(monkeypatch, elements, modes, points=None, status="ok", issues=None, **kwargs):
    response = {"elements": elements, "status": status, "issues": issues or [],
                "coverage": {"completed": 1, "planned": 2},
                "source": {"attribution": "© OpenStreetMap contributors", "warnings": ["Mapping may be incomplete"]}}
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(overnight_places, "query_osm", query)
    points = points or [[0, 0], [0, 0.02]]
    result = await overnight_places.find_overnight_places(points, coordinate_distances(points), modes, **kwargs)
    return result, query


@pytest.mark.asyncio
async def test_multi_mode_union_requires_explicit_cabin_rentals(monkeypatch):
    result, query = await _find(monkeypatch, [
        _place(1, tourism="camp_site"),
        _place(2, tourism="hotel"),
        _place(3, tourism="camp_site", cabins="yes"),
        _place(4, tourism="camp_site", caravans="yes"),
    ], ["tent", "accommodation"])
    by_id = {item["id"]: item for item in result["items"]}
    assert by_id["osm:node:1"]["overnight_modes"] == ["tent"]
    assert by_id["osm:node:2"]["overnight_modes"] == ["accommodation"]
    assert by_id["osm:node:3"]["overnight_modes"] == ["tent", "accommodation"]
    assert by_id["osm:node:4"]["overnight_modes"] == ["tent"]
    assert by_id["osm:node:1"]["permission"]["status"] == "unknown"
    assert any("Tent acceptance is unknown" in issue for issue in by_id["osm:node:1"]["issues"])
    assert query.await_count == 1


@pytest.mark.asyncio
async def test_tents_no_excludes_tent_without_switching_to_rooms(monkeypatch):
    site = _place(tourism="camp_site", tents="no", cabins="yes")
    result, _ = await _find(monkeypatch, [site], ["tent"])
    assert result["items"] == []
    result, _ = await _find(monkeypatch, [site], ["tent", "accommodation"])
    assert result["items"][0]["overnight_modes"] == ["accommodation"]
    result, _ = await _find(monkeypatch, [_place(tourism="camp_site", camping="no")], ["tent"])
    assert result["items"] == []


@pytest.mark.asyncio
async def test_shelters_need_supported_sleeping_meaning(monkeypatch):
    result, _ = await _find(monkeypatch, [
        _place(1, amenity="shelter"),
        _place(2, amenity="shelter", shelter_type="weather_shelter"),
        _place(3, amenity="shelter", shelter_type="lean_to"),
        _place(4, amenity="shelter", shelter_type="lean_to", sleeping="yes"),
        _place(5, amenity="shelter", shelter_type="basic_hut"),
    ], ["accommodation"])
    assert [item["id"] for item in result["items"]] == ["osm:node:4", "osm:node:5"]


@pytest.mark.asyncio
async def test_bivouac_needs_explicit_permission_not_campsite_assumption(monkeypatch):
    result, _ = await _find(monkeypatch, [
        _place(1, tourism="camp_site"),
        _place(2, tourism="camp_site", camping="designated"),
        _place(3, tourism="camp_site", bivouac="yes"),
        _place(4, tourism="camp_site", camp_site="bivouac"),
        _place(5, tourism="camp_site", camp_site="bivouac", bivouac="no"),
    ], ["bivouac"])
    assert [item["id"] for item in result["items"]] == ["osm:node:3", "osm:node:4"]
    assert all(item["permission"]["status"] == "designated" for item in result["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [
    {"access": "private"}, {"access": "no"}, {"disused": "yes"},
    {"abandoned": "yes"}, {"closed": "yes"}, {"opening_hours": "closed"},
    {"disused:tourism": "camp_site"},
])
async def test_private_and_inactive_places_are_not_usable(monkeypatch, blocked):
    result, _ = await _find(monkeypatch, [_place(tourism="camp_site", **blocked)], ["tent"])
    assert result["items"] == []
    assert any("restricted" in issue or "prohibited" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_identity_dedup_preserves_loop_encounters(monkeypatch):
    points = [[0, 0], [0, 0.02], [0.02, 0.02], [0.02, 0], [0, 0], [0, 0.02]]
    place = _place(tourism="hotel")
    result, _ = await _find(monkeypatch, [place, place.copy()], ["accommodation"], points=points, corridor_m=100)
    assert len(result["items"]) == 1
    encounters = result["items"][0]["encounters"]
    assert len(encounters) == 2
    assert encounters[1]["route_km"] - encounters[0]["route_km"] > 8


@pytest.mark.asyncio
async def test_area_representatives_corridor_and_distinct_osm_types(monkeypatch):
    way = {"type": "way", "id": 1, "center": {"lat": 0, "lon": 0.01}, "tags": {"tourism": "hotel"}}
    relation = {"type": "relation", "id": 1, "members": [
        {"type": "way", "geometry": [{"lat": 0, "lon": 0.01}]},
    ], "tags": {"tourism": "hotel"}}
    result, _ = await _find(monkeypatch, [
        _place(1, tourism="hotel"), way, relation,
        _place(2, lat=1, tourism="hotel"),
    ], ["accommodation"])
    assert {item["id"] for item in result["items"]} == {"osm:node:1", "osm:way:1", "osm:relation:1"}
    assert all("entrance" in item["location_note"] for item in result["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["partial", "unavailable"])
async def test_upstream_coverage_and_warnings_are_retained(monkeypatch, status):
    result, _ = await _find(monkeypatch, [], ["tent"], status=status, issues=["Request budget exhausted"])
    assert result["status"] == status
    assert result["coverage"] == {"completed": 1, "planned": 2}
    assert result["issues"] == ["Request budget exhausted"]
    assert result["sources"][0]["warnings"] == ["Mapping may be incomplete"]


@pytest.mark.asyncio
async def test_date_opening_and_amenities_never_imply_availability(monkeypatch):
    tags = {"tourism": "hotel", "website": "https://example.invalid", "contact:phone": "+1 555",
            "opening_hours": "24/7", "seasonal": "summer", "reservation": "required",
            "drinking_water": "yes", "toilets": "yes"}
    place = _place()
    place["tags"] = tags
    result, _ = await _find(monkeypatch, [place], ["accommodation"], overnight_date="2027-01-01")
    item = result["items"][0]
    assert item["availability"] == "unknown"
    assert item["permission"]["status"] == "unknown"
    assert item["source_tags"] == tags
    assert item["details"]["opening_hours"] == "24/7"
    assert item["details"]["drinking_water"] == "yes"
    assert item["details"]["shower"] == "unknown"
    assert item["source_links"] == ["https://www.openstreetmap.org/node/1"]
    assert "OpenStreetMap" in item["attribution"]


@pytest.mark.asyncio
async def test_missing_geometry_is_partial_not_silent_absence(monkeypatch):
    result, _ = await _find(monkeypatch, [{"type": "way", "id": 1, "tags": {"tourism": "hotel"}}], ["accommodation"])
    assert result["status"] == "partial"
    assert result["items"] == []
    assert any("geometry unavailable" in issue for issue in result["issues"])


@pytest.mark.asyncio
@pytest.mark.parametrize("modes", [[], None, "tent", ["camping"]])
async def test_modes_are_required_without_default(monkeypatch, modes):
    with pytest.raises(ValueError, match="overnight_modes"):
        await _find(monkeypatch, [], modes)
