"""Exercise native section joins through the public read-only tool seam."""

import math
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from komoot_mcp.tools import section_tools


# Equatorial points exactly one geographic kilometre apart on our sphere.
DEGREE_PER_KM = 180 / (math.pi * 6371.0088)


def native_tour():
    return {"_embedded": {
        "coordinates": {"items": [[0, i * DEGREE_PER_KM, 100] for i in range(6)]},
        "way_types": {"items": [
            {"from": 0, "to": 3, "element": "wt#way"},
            {"from": 3, "to": 5, "element": "wt#hike_d2"},
        ]},
        "surfaces": {"items": [
            {"from": 0, "to": 2, "element": "sf#compacted"},
            {"from": 2, "to": 4, "element": "sf#ground"},
            {"from": 4, "to": 5, "element": "sf#compacted"},
        ]},
        "directions": {"items": [
            {"index": 3, "type": "TL", "distance": 103, "street_name": "Track", "future": {"x": 1}},
            {"index": 5, "type": "F"},
        ]},
    }}


def tool(monkeypatch, tour):
    client = SimpleNamespace(get_tour_full=AsyncMock(return_value=tour))
    monkeypatch.setattr(section_tools, "get_client", lambda: client)
    registered = {}

    class MCP:
        def tool(self):
            def decorate(fn):
                registered[fn.__name__] = fn
                return fn
            return decorate

    section_tools.register(MCP())
    return registered["komoot_analyze_tour_sections"], client


@pytest.mark.asyncio
async def test_joins_all_attribute_boundaries_and_preserves_native_evidence(monkeypatch):
    tour = native_tour()
    original = deepcopy(tour)
    analyze, client = tool(monkeypatch, tour)
    result = await analyze(42)
    client.get_tour_full.assert_awaited_once_with(42, include_coordinates=True)
    assert result["tour_id"] == 42
    assert result["total_km"] == pytest.approx(5)
    assert result["coverage"] == "complete"
    sections = result["sections"]
    assert [s["start_km"] for s in sections] == pytest.approx([0, 2, 3, 4])
    assert [s["end_km"] for s in sections] == pytest.approx([2, 3, 4, 5])
    assert sections[2]["way_type"]["native_type"] == "wt#hike_d2"
    assert sections[2]["way_type"]["label"] == "Hiking path with difficulty 2"
    assert sections[2]["directions"] == [{"km": pytest.approx(3), "raw": original["_embedded"]["directions"]["items"][0]}]
    assert sections[-1]["directions"][0]["raw"]["index"] == 5
    assert tour == original


@pytest.mark.asyncio
async def test_clips_range_and_assigns_boundary_direction_once(monkeypatch):
    analyze, _ = tool(monkeypatch, native_tour())
    result = await analyze(42, 2.5, 99)
    assert result["range"] == {"start_km": 2.5, "end_km": pytest.approx(5)}
    assert result["sections"][0]["start_km"] == 2.5
    assert sum(len(s["directions"]) for s in result["sections"]) == 2
    assert result["sections"][1]["coordinate_index_span"] == {"from": 3, "to": 4}


@pytest.mark.asyncio
async def test_conflicting_overlap_is_unknown_only_where_overlapping(monkeypatch):
    tour = native_tour()
    tour["_embedded"]["surfaces"]["items"].append({"from": 1, "to": 3, "element": "sf#wood"})
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42)
    sections = result["sections"]
    assert sections[0]["surface"]["status"] == "known"
    assert sections[1]["surface"] == {
        "status": "unknown", "native_type": None, "label": None,
        "candidate_native_types": ["sf#compacted", "sf#wood"],
    }
    assert sections[-1]["surface"]["status"] == "known"
    assert result["coverage"] == "partial"


@pytest.mark.asyncio
async def test_gap_and_aggregate_items_never_become_whole_route_claims(monkeypatch):
    tour = native_tour()
    tour["_embedded"]["surfaces"]["items"] = [
        {"type": "sf#gravel", "amount": 1.0},
        {"from": 2, "to": 3, "element": "sf#ground"},
    ]
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42)
    assert result["coverage"] == "partial"
    assert result["issues"]
    assert result["sections"][0]["surface"]["status"] == "unknown"
    known = [s for s in result["sections"] if s["surface"]["status"] == "known"]
    assert len(known) == 1
    assert known[0]["start_km"] == pytest.approx(2)
    assert known[0]["end_km"] == pytest.approx(3)


@pytest.mark.asyncio
@pytest.mark.parametrize("span", [
    {"from": -1, "to": 5, "element": "sf#wood"},
    {"from": 0, "to": 6, "element": "sf#wood"},
    {"from": True, "to": 5, "element": "sf#wood"},
    {"from": 4, "to": 2, "element": "sf#wood"},
    {"from": 0, "to": 5}, None,
])
async def test_malformed_spans_are_reported_not_stretched(monkeypatch, span):
    tour = native_tour()
    tour["_embedded"]["surfaces"]["items"] = [span]
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42)
    assert result["coverage"] == "partial"
    assert result["issues"]
    assert all(s["surface"]["status"] == "unknown" for s in result["sections"])


@pytest.mark.asyncio
async def test_merges_identical_adjacent_spans_without_copying_points(monkeypatch):
    tour = native_tour()
    for name, code in (("way_types", "wt#way"), ("surfaces", "sf#ground")):
        tour["_embedded"][name]["items"] = [
            {"from": 0, "to": 2, "element": code},
            {"from": 2, "to": 5, "element": code},
        ]
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42)
    assert len(result["sections"]) == 1
    assert result["sections"][0]["coordinate_index_span"] == {"from": 0, "to": 5}
    assert "coordinates" not in result


@pytest.mark.asyncio
async def test_zero_length_edges_and_native_coordinate_objects(monkeypatch):
    tour = native_tour()
    tour["_embedded"]["coordinates"]["items"] = [
        {"lat": 0, "lng": x * DEGREE_PER_KM} for x in [0, 0, 1, 2, 3, 4]
    ]
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42)
    assert result["total_km"] == pytest.approx(4)
    assert all(s["end_km"] > s["start_km"] for s in result["sections"])
    assert result["coverage"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinates", [None, [], [[0, 0], [float("nan"), 0]], [[0, 0], [91, 0]]])
async def test_missing_or_invalid_geometry_does_not_bridge_unknown_distance(monkeypatch, coordinates):
    tour = native_tour()
    tour["_embedded"]["coordinates"] = {"items": coordinates}
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42)
    assert result["total_km"] is None
    assert result["coverage"] == "unknown"
    assert result["sections"] == []
    assert result["unlocated_directions"] == tour["_embedded"]["directions"]["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(0, 0), (10, None), (5, 5)])
async def test_empty_clipped_range_has_no_positive_length_sections(monkeypatch, start, end):
    analyze, _ = tool(monkeypatch, native_tour())
    result = await analyze(42, start, end)
    assert result["sections"] == []
    assert result["coverage"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(-1, None), (2, 1), (float("nan"), None), (0, float("inf")), (True, None)])
async def test_invalid_range_fails_before_fetch(monkeypatch, start, end):
    analyze, client = tool(monkeypatch, native_tour())
    with pytest.raises(ValueError):
        await analyze(42, start, end)
    client.get_tour_full.assert_not_awaited()


@pytest.mark.asyncio
async def test_unlocated_directions_are_retained_and_outside_range_omitted(monkeypatch):
    tour = native_tour()
    tour["_embedded"]["directions"]["items"] += [{"index": 99, "type": "future"}, {"type": "missing"}]
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42, 0, 1)
    assert all(not s["directions"] for s in result["sections"])
    assert result["unlocated_directions"] == [{"index": 99, "type": "future"}, {"type": "missing"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("way,surface,way_label,surface_label", [
    ("wt#hike_d2", "sf#stone", "Hiking path with difficulty 2", "Stone"),
    ("wt#alpine_hiking_path", "sm#nature", "Alpine Hiking Path", "Natural"),
    ("wt#way", "sb#compacted", "Path", "Compacted Gravel"),
    ("wt#mountain_hiking_path", "sf#rock", None, None),
])
async def test_only_documented_literal_descriptions_become_labels(
    monkeypatch, way, surface, way_label, surface_label,
):
    tour = native_tour()
    for name, code in (("way_types", way), ("surfaces", surface)):
        tour["_embedded"][name]["items"] = [{"from": 0, "to": 5, "element": code}]
    analyze, _ = tool(monkeypatch, tour)
    result = await analyze(42, 3, 4)
    section = result["sections"][0]
    assert section["start_km"] == 3
    assert section["end_km"] == 4
    assert section["way_type"]["native_type"] == way
    assert section["way_type"]["label"] == way_label
    assert section["surface"]["native_type"] == surface
    assert section["surface"]["label"] == surface_label
    assert result["label_source"] == {
        "language": "en",
        "way_types": "https://static.komoot.de/doc/external-api/v007/waytypes.html",
        "surfaces": "https://static.komoot.de/doc/external-api/v007/surfaces.html",
    }
