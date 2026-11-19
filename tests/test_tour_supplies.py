"""Komoot-only supply enrichment covers full arcs and retains uncertainty."""

import math
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from komoot_mcp import tour_supplies
from komoot_mcp.geocoder import GeocoderError
from komoot_mcp.route_sections import coordinate_distances
from komoot_mcp.supply_categories import KNOWN_CATEGORIES, SUPPLY_CATEGORIES

_SUPPLY_CATEGORY = next(iter(SUPPLY_CATEGORIES))


def _poi(poi_id: str | None = "Poi1", lat: object = 0.001, lng=0.01, category: object = _SUPPLY_CATEGORY, **extra):
    return {
        "poi_id": poi_id, "osm_id": "n1", "name": "Mapped candidate",
        "lat": lat, "lng": lng, "category": category,
        "waypoint_details": {"poi_id": poi_id} if poi_id else None, **extra,
    }


async def _lookup(monkeypatch, payload, points=None, corridor_m=300):
    limiter = Mock(acquire=AsyncMock())
    search = Mock(return_value=payload)
    monkeypatch.setattr(tour_supplies, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(tour_supplies, "find_pois_nearby", search)
    points = points if points is not None else [[0, 0], [0, 0.02]]
    result = await tour_supplies.find_tour_supplies(points, coordinate_distances(points), corridor_m)
    return result, search, limiter


@pytest.mark.asyncio
async def test_sparse_midpoint_endpoints_and_overlapping_requests(monkeypatch):
    points = [[0, 0], [0, 0.04]]
    result, search, limiter = await _lookup(monkeypatch, {"results": [
        _poi("Start", lat=0, lng=0), _poi("Middle", lat=0.001, lng=0.02),
        _poi("End", lat=0, lng=0.04),
    ]}, points)
    assert result["status"] == "ok"
    assert [item["poi_id"] for item in result["items"]] == ["Start", "Middle", "End"]
    centers = [list(call.args) for call in search.call_args_list]
    assert centers[0] == points[0]
    assert centers[-1] == points[-1]
    assert len(centers) > 2
    center_distances = coordinate_distances(centers)
    assert center_distances is not None
    assert max(b - a for a, b in zip(center_distances, center_distances[1:])) <= 0.700001
    for call in search.call_args_list:
        assert call.kwargs == {"radius_m": 1000, "limit": 100, "lang": "de"}
    assert limiter.acquire.await_count == search.call_count == result["coverage"]["completed"]
    assert result["coverage"]["planned"] == search.call_count
    assert result["source"]["endpoint"] == "https://www.komoot.com/api/search/reverse-geocoding"
    assert result["source"]["completeness"] == "not_guaranteed"
    assert datetime.fromisoformat(result["fetched_at"]).utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_requests_are_rate_limited_before_blocking_search(monkeypatch):
    events = []
    limiter = Mock(acquire=AsyncMock(side_effect=lambda: events.append("acquire")))
    monkeypatch.setattr(tour_supplies, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(tour_supplies, "find_pois_nearby", lambda *args, **kwargs: events.append("search") or {"results": []})
    points = [[0, 0], [0, 0.001]]
    await tour_supplies.find_tour_supplies(points, coordinate_distances(points))
    assert events == ["acquire", "search", "acquire", "search"]


@pytest.mark.asyncio
async def test_duplicates_metadata_and_unknown_availability(monkeypatch):
    poi = _poi(name="Always open safe drinking water", raw={"category": _SUPPLY_CATEGORY})
    result, _, _ = await _lookup(monkeypatch, {"results": [poi, poi.copy()]})
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["category"] == _SUPPLY_CATEGORY
    assert item["native_category"] == _SUPPLY_CATEGORY
    assert item["categories"] == [SUPPLY_CATEGORIES[_SUPPLY_CATEGORY]]
    assert item["raw"] == poi["raw"]
    assert item["waypoint_details"] == poi["waypoint_details"]
    for key in ("open_now", "access"):
        assert item[key] == "unknown"
    assert item["drinking_safety"] == "unverified"
    assert item["opening_hours"] is None
    assert item["osm_url"] == "https://www.openstreetmap.org/node/1"
    assert "tags" not in item and "timestamp" not in item and "version" not in item
    assert "entrance not verified" in item["location_note"]
    assert item["actual_detour"] == "unknown"


@pytest.mark.asyncio
async def test_osm_fallback_requires_complete_identity(monkeypatch):
    points = [[0, 0], [0, 0]]
    result, search, _ = await _lookup(monkeypatch, {"results": [
        _poi(None, lat=0, lng=0), _poi(None, lat=0, lng=0),
        _poi(None, lat=0, lng=0, osm_id="w1"),
        _poi(None, lat=0, lng=0, osm_id=1),
    ]}, points)
    assert search.call_count == 1
    assert len(result["items"]) == 3
    assert result["status"] == "partial"
    unresolved = next(item for item in result["items"] if item["identity_status"] == "unresolved")
    assert unresolved["poi_id"] is None
    assert unresolved["osm_url"] is None


@pytest.mark.asyncio
async def test_poi_identity_takes_precedence_over_shared_osm_identity(monkeypatch):
    result, _, _ = await _lookup(monkeypatch, {"results": [
        _poi("Source-A"), _poi("Source-B"), _poi("Source-A"),
    ]})
    assert [item["poi_id"] for item in result["items"]] == ["Source-A", "Source-B"]


@pytest.mark.asyncio
async def test_dense_vertices_do_not_consume_search_budget(monkeypatch):
    points = [[0, index / 100000] for index in range(1001)]
    result, search, _ = await _lookup(monkeypatch, {"results": []}, points)
    assert result["status"] == "ok"
    assert search.call_count == 3


@pytest.mark.asyncio
async def test_loop_retains_distinct_encounters(monkeypatch):
    points = [[0, 0], [0, 0.005], [0, 0.01], [0, 0.015], [0, 0.02], [0, 0.015], [0, 0.01], [0, 0.005], [0, 0]]
    result, _, _ = await _lookup(monkeypatch, {"results": [_poi(lat=0)]}, points)
    item = result["items"][0]
    assert item["route_position_status"] == "multiple_encounters"
    assert item["route_km_candidates"] == pytest.approx([1.11195, 3.33585], abs=0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize(("points", "poi"), [
    ([[0, 179.99], [0, -179.99]], _poi(lat=0.001, lng=180)),
    ([[85, -0.1], [85, 0.1]], _poi(
        lat=math.degrees(math.atan(math.tan(math.radians(85)) / math.cos(math.radians(0.1)))), lng=0)),
])
async def test_spherical_sampling_and_projection(monkeypatch, points, poi):
    result, search, _ = await _lookup(monkeypatch, {"results": [poi]}, points)
    assert result["status"] == "ok"
    route_distances = coordinate_distances(points)
    assert route_distances is not None
    assert result["items"][0]["route_km"] == pytest.approx(route_distances[-1] / 2, abs=0.001)
    centers = [call.args for call in search.call_args_list]
    center_distances = coordinate_distances(centers)
    assert center_distances is not None
    assert max(b - a for a, b in zip(center_distances, center_distances[1:])) <= 0.700001
    if points[0][1] > 179:
        assert all(abs(lng) > 179 for lat, lng in centers)


@pytest.mark.asyncio
async def test_outside_corridor_and_known_non_supply_ignored(monkeypatch):
    category = next(category for category in KNOWN_CATEGORIES if category not in SUPPLY_CATEGORIES)
    result, _, _ = await _lookup(monkeypatch, {"results": [
        _poi(lat=0.01), _poi("Other", category=category, name="Restaurant drinking water"),
    ]})
    assert result["status"] == "ok"
    assert result["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [max(KNOWN_CATEGORIES) + 1, True, [], None, "water"])
async def test_unknown_category_is_not_an_empty_inventory(monkeypatch, category):
    result, _, _ = await _lookup(monkeypatch, {"results": [_poi(category=category)]})
    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["coverage"]["unknown_categories"] == 1
    assert result["unknown_categories"] == [category]


@pytest.mark.asyncio
async def test_capped_results_partial_even_when_no_candidates_in_corridor(monkeypatch):
    result, search, _ = await _lookup(monkeypatch, {"results": [_poi(lat=1)] * 100})
    assert result["status"] == "partial"
    assert result["coverage"]["capped"] == search.call_count
    assert result["items"] == []


@pytest.mark.asyncio
async def test_one_failed_circle_keeps_success_without_retry(monkeypatch):
    limiter = Mock(acquire=AsyncMock())
    search = Mock(side_effect=[GeocoderError("late"), {"results": [_poi(lat=0, lng=0)]}])
    monkeypatch.setattr(tour_supplies, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(tour_supplies, "find_pois_nearby", search)
    points = [[0, 0], [0, 0.001]]
    result = await tour_supplies.find_tour_supplies(points, coordinate_distances(points))
    assert result["status"] == "partial"
    assert len(result["items"]) == 1
    assert result["coverage"]["failed"] == result["coverage"]["completed"] == 1
    assert search.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], {}, {"results": {}}, {"results": [None]}, {"results": [_poi(lat="bad")]}])
async def test_malformed_response_is_unavailable(monkeypatch, payload):
    result, _, _ = await _lookup(monkeypatch, payload)
    assert result["status"] == "unavailable"
    assert result["issues"]
    assert result["items"] == []
    assert result["coverage"]["completed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("points", "distances"), [
    ([], []), ([[0, 0]], [0]), ([[91, 0], [0, 0]], [0, 1]),
    ([[0, 0], [0, 0.01]], None), ([[0, 0], [0, 0.01]], [0, float("nan")]),
    ([[0, 0], [0, 0.01]], [1, 0]), ([[0, 0], [0, 180]], [0, 20015]),
    ([[0, 0]] * 20001, [0] * 20001),
])
async def test_invalid_geometry_sends_no_request(monkeypatch, points, distances):
    search = Mock()
    monkeypatch.setattr(tour_supplies, "find_pois_nearby", search)
    result = await tour_supplies.find_tour_supplies(points, distances)
    assert result["status"] == "unavailable"
    assert result["issues"]
    assert result["fetched_at"] is None
    search.assert_not_called()


@pytest.mark.asyncio
async def test_budget_failure_is_preflight_not_route_truncation(monkeypatch):
    search = Mock()
    monkeypatch.setattr(tour_supplies, "find_pois_nearby", search)
    points = [[0, 0], [0, 2]]
    result = await tour_supplies.find_tour_supplies(points, coordinate_distances(points))
    assert result["status"] == "unavailable"
    assert result["coverage"]["planned"] > 200
    assert result["coverage"]["completed"] == 0
    assert "no searches sent" in result["issues"][0]
    search.assert_not_called()


@pytest.mark.asyncio
async def test_maximum_corridor_keeps_unverified_metadata_raw(monkeypatch):
    result, search, _ = await _lookup(monkeypatch, {"results": [
        _poi(raw={"opening_hours": "24/7", "access": "private"}),
    ], "limit_reached": True}, corridor_m=1000)
    assert all(call.kwargs["radius_m"] == 2000 for call in search.call_args_list)
    assert result["coverage"]["center_spacing_m"] == 1000
    assert result["status"] == "partial"
    assert result["items"][0]["raw"] == {"opening_hours": "24/7", "access": "private"}
    assert result["items"][0]["opening_hours"] is None
    assert result["items"][0]["access"] == "unknown"
    assert result["items"][0]["drinking_safety"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize("corridor", [0, -1, 1001, True, float("inf"), "300"])
async def test_invalid_corridor_rejected_before_request(monkeypatch, corridor):
    search = Mock()
    monkeypatch.setattr(tour_supplies, "find_pois_nearby", search)
    with pytest.raises(ValueError, match="corridor_m"):
        await tour_supplies.find_tour_supplies([[0, 0], [0, 0.001]], [0, 0.1], corridor)
    search.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("category,label,kind", [
    (40, "Restaurant", "food"), (89, "Supermarket", "groceries"),
    (73, "Public Toilet", "toilets"), (120, "Spring", "water"),
    (164, "Public Water Fountain", "water"),
])
async def test_verified_map_categories_become_supply_candidates(monkeypatch, category, label, kind):
    result, _, _ = await _lookup(monkeypatch, {"results": [_poi(category=category)]})
    assert result["items"][0]["category_label"] == label
    assert result["items"][0]["categories"] == [kind]
    assert result["items"][0]["drinking_safety"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [148, 244, 60])
async def test_ornamental_water_animal_troughs_and_hotels_are_not_supplies(monkeypatch, category):
    result, _, _ = await _lookup(monkeypatch, {"results": [_poi(category=category)]})
    assert result["items"] == []
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_later_recognized_category_is_not_hidden_by_an_earlier_unknown(monkeypatch):
    result, _, _ = await _lookup(monkeypatch, {"results": [_poi(category=None), _poi(category=40)]})
    assert result["items"][0]["native_category"] == 40
    assert len(result["items"]) == 1
