"""Persist previewed routes without live account mutations."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from komoot_mcp.client import KomootAPIError, KomootClient


@pytest.fixture
def route():
    return {
        "sport": "hike", "constitution": 3, "distance": 1200, "duration": 900,
        "path": [
            {"location": {"lat": 49, "lng": 8}, "index": 0, "reference": "poi:Ab12"},
            {"location": {"lat": 49.1, "lng": 8.1}, "index": 1, "reference": "hlp:25"},
        ],
        "segments": [{"type": "Routed", "from": 0, "to": 1}],
        "_embedded": {"coordinates": {"items": [[49, 8, 300], [49.1, 8.1, 400]]}},
        "result_status": "ok", "planning_summary": {"warnings": ["preview only"]},
    }


@pytest.fixture
def client(route):
    value = KomootClient(SimpleNamespace(), SimpleNamespace(acquire=AsyncMock()))
    current = {
        "id": 42, "type": "tour_planned", "status": "friends", "name": "Original",
        "description": "Keep this", "date": "2026-01-01T12:00:00Z", "gear": "Boots",
    }
    value.get_tour = AsyncMock(side_effect=[current, {**route, **current}])
    value.get_tour_coordinates = AsyncMock(return_value=route["_embedded"]["coordinates"]["items"])
    value._http_request = AsyncMock(return_value=None)
    return value


@pytest.mark.asyncio
async def test_update_puts_existing_id_preserves_metadata_and_verifies(client, route):
    route.update({"id": 999, "name": "Do not rename", "status": "public", "gear": "Bike"})
    original = deepcopy(route)
    result = await client.update_planned_tour(42, route)
    call = client._http_request.call_args
    assert call.args == ("PUT", "https://api.komoot.de/v007/tours/42")
    body = call.kwargs["json_body"]
    assert body["id"] == 42
    assert body["name"] == "Original"
    assert body["status"] == "friends"
    assert body["description"] == "Keep this"
    assert body["date"] == "2026-01-01T12:00:00Z"
    assert body["gear"] == "Boots"
    assert body["path"] == route["path"]
    assert body["_embedded"] == route["_embedded"]
    assert "planning_summary" not in body
    assert "result_status" not in body
    assert route == original
    assert result["id"] == 42
    assert result["status"] == "updated"
    assert result["verified_visibility"] == "friends"
    assert result["visibility_verified"] is True
    assert result["verification"]["verified"] is True
    client._http_request.assert_awaited_once()
    client.get_tour_coordinates.assert_awaited_once_with(42)
    assert result["metadata_changes"]["date"] == {
        "before": "2026-01-01T12:00:00Z", "after": "2026-01-01T12:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("type", "tour_recorded"), ("status", "unknown"),
])
async def test_update_rejects_wrong_existing_type_or_visibility(client, route, field, value):
    client.get_tour.side_effect = None
    client.get_tour.return_value = {"type": "tour_planned", "status": "private", field: value}
    with pytest.raises(KomootAPIError, match="no update sent"):
        await client.update_planned_tour(42, route)
    client._http_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda route: route.pop("_embedded"),
    lambda route: route.update(path=[]),
    lambda route: route.update(segments=[]),
    lambda route: route["path"][1].update(index=99),
    lambda route: route["path"][0]["location"].update(lat=float("nan")),
    lambda route: route["segments"][0].update(to=99),
    lambda route: route.update(constitution=True),
    lambda route: route.update(sport="bogus"),
])
async def test_invalid_preview_never_reads_or_writes(client, route, change):
    change(route)
    with pytest.raises(ValueError):
        await client.update_planned_tour(42, route)
    client.get_tour.assert_not_awaited()
    client._http_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("status", "public"), ("sport", "racebike"), ("constitution", 5),
    ("type", "tour_recorded"), ("name", "Changed"), ("gear", "Changed"),
    ("path", [{"reference": "hlp:25"}, {"reference": "poi:Ab12"}]),
])
async def test_update_readback_mismatch_retains_id_and_never_retries(client, route, field, value):
    current, observed = client.get_tour.side_effect
    client.get_tour.side_effect = [current, {**observed, field: value}]
    with pytest.raises(KomootAPIError, match="Tour 42.*inspect this tour before retrying"):
        await client.update_planned_tour(42, route)
    client._http_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_geometry_mismatch_is_not_a_verified_update(client, route):
    client.get_tour_coordinates.return_value = [[49, 8], [50, 9]]
    with pytest.raises(KomootAPIError, match="Tour 42.*geometry.*inspect"):
        await client.update_planned_tour(42, route)
    client._http_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_uncertain_update_transport_retains_exact_target_and_never_retries(client, route):
    client._http_request.side_effect = KomootAPIError("timeout")
    with pytest.raises(KomootAPIError, match="Tour 42.*inspect this tour before retrying"):
        await client.update_planned_tour(42, route)
    client._http_request.assert_awaited_once()
    client.get_tour.assert_awaited_once_with(42)


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [
    KomootAPIError("GET failed"),
    {"type": "tour_planned", "status": "public", "sport": "hike", "constitution": 3},
    {"type": "tour_planned", "status": "private", "sport": "racebike", "constitution": 3},
    {"type": "tour_planned", "status": "private", "sport": "hike", "constitution": 1},
    {"type": "tour_planned", "status": "private", "sport": "hike", "constitution": 3,
     "path": [{"reference": "hlp:25"}, {"reference": "poi:Ab12"}]},
])
async def test_save_requires_successful_readback_and_never_retries(route, monkeypatch, readback):
    value = KomootClient(SimpleNamespace(), SimpleNamespace(acquire=AsyncMock()))
    value._basic_auth = Mock(return_value=("user", "token"))
    value.get_tour = AsyncMock(side_effect=readback) if isinstance(readback, Exception) else AsyncMock(return_value=readback)
    post = Mock(return_value=SimpleNamespace(status_code=201, json=lambda: {"id": 42}))
    monkeypatch.setattr("komoot_mcp.client.requests.post", post)
    with pytest.raises(KomootAPIError, match="Tour 42.*inspect this tour before retrying"):
        await value.save_planned_tour(route, "New route")
    post.assert_called_once()
    value.get_tour.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_refreshed_planning_date_is_reported_without_unsupported_patch(client, route):
    current, observed = client.get_tour.side_effect
    refreshed = "2026-11-18T21:21:33.091Z"
    client.get_tour.side_effect = [current, {**observed, "date": refreshed}]
    client._komoot_patch = AsyncMock()
    result = await client.update_planned_tour(42, route)
    expected = {"date": {"before": current["date"], "after": refreshed}}
    assert result["metadata_changes"] == expected
    assert result["verification"]["metadata_changes"] == expected
    assert result["verification"]["verified"] is True
    assert "date" not in result["verification"]["requested"]
    assert result["verification"]["observed"]["date"] == refreshed
    client._http_request.assert_awaited_once()
    client._komoot_patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_original_date_is_reported_as_null(client, route):
    current, observed = client.get_tour.side_effect
    current.pop("date")
    client.get_tour.side_effect = [current, observed]
    result = await client.update_planned_tour(42, route)
    assert result["metadata_changes"]["date"] == {
        "before": None, "after": observed["date"],
    }


@pytest.mark.asyncio
async def test_update_final_readback_failure_retains_id_without_replaying_write(client, route):
    current, _ = client.get_tour.side_effect
    client.get_tour.side_effect = [current, KomootAPIError("GET failed")]
    with pytest.raises(KomootAPIError, match="Tour 42.*readback failed.*inspect this tour before retrying"):
        await client.update_planned_tour(42, route)
    client._http_request.assert_awaited_once()
