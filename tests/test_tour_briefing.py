"""Exercise tour enrichment through the public read-only MCP interface."""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp import secure_http
from komoot_mcp.server import create_server
from komoot_mcp.tools import briefing_tools
from komoot_mcp import tour_supplies


DEGREE_PER_KM = 180 / (math.pi * 6371.0088)


@pytest.fixture
def briefing_client(monkeypatch):
    tour = {
        "id": 42, "name": "Test walk", "duration": 7200,
        "tour_information": [{"type": "STEPS", "segments": [{"from": 0, "to": 1}]}],
        "_embedded": {
            "coordinates": {"items": [[0, 0], [0, 10 * DEGREE_PER_KM]]},
            "way_types": {"items": [{"from": 0, "to": 1, "element": "wt#way"}]},
            "surfaces": {"items": [{"from": 0, "to": 1, "element": "sf#stone"}]},
        },
    }
    client = SimpleNamespace(get_tour_full=AsyncMock(return_value=tour))
    monkeypatch.setattr(briefing_tools, "get_client", lambda: client)
    monkeypatch.setattr(tour_supplies, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))
    return client


@pytest.mark.asyncio
async def test_read_only_briefing_combines_corridor_evidence_and_pause_timing(monkeypatch, briefing_client):
    response = Mock()
    response.json.return_value = {"_embedded": {"items": [{
        "name": "Lunch", "category": 40, "poi_id": "3dtl", "osm_id": "n123",
        "point": {"y": 0, "x": 5 * DEGREE_PER_KM},
    }]}}
    get = Mock(return_value=response)
    monkeypatch.setattr(secure_http, "get", get)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_tour_briefing", {
            "tour_id": 42, "departure_time": "2026-11-19T09:00:00+01:00",
            "pauses": [{"km": 5, "duration_s": 1200, "name": "Lunch"}],
        })
    assert not result.isError, result
    assert result.structuredContent is not None
    data = result.structuredContent["data"]
    supply = data["supplies"]["items"][0]
    assert supply["route_km"] == pytest.approx(5)
    assert supply["native_category"] == 40
    assert supply["poi_id"] == "3dtl"
    assert supply["opening_hours"] is None
    assert data["schedule"]["moving_s"] == 7200
    assert data["schedule"]["pause_s"] == 1200
    assert data["schedule"]["total_s"] == 8400
    assert data["route_analysis"]["critical_sections"][0]["code"] == "STEPS"
    briefing_client.get_tour_full.assert_awaited_once_with(42, include_coordinates=True)
    assert all(call.args == ("https://www.komoot.com/api/search/reverse-geocoding",) for call in get.call_args_list)
    assert all("auth" not in call.kwargs for call in get.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"corridor_m": 0}, {"departure_time": "2026-11-19T09:00:00"},
    {"pauses": [{"km": -1, "duration_s": 10}]},
])
async def test_invalid_briefing_arguments_fail_before_account_access(briefing_client, arguments):
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_tour_briefing", {"tour_id": 42, **arguments})
    assert result.isError
    briefing_client.get_tour_full.assert_not_awaited()


@pytest.mark.asyncio
async def test_pause_beyond_finish_never_discloses_geometry(monkeypatch, briefing_client):
    get = Mock()
    monkeypatch.setattr(secure_http, "get", get)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_tour_briefing", {
            "tour_id": 42, "pauses": [{"km": 20, "duration_s": 600}],
        })
    assert result.isError
    get.assert_not_called()


@pytest.mark.asyncio
async def test_supply_outage_keeps_native_analysis_and_schedule(monkeypatch, briefing_client):
    monkeypatch.setattr(secure_http, "get", Mock(side_effect=requests.Timeout("Unavailable")))
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_tour_briefing", {"tour_id": 42})
    assert not result.isError, result
    assert result.structuredContent is not None
    data = result.structuredContent["data"]
    assert data["supplies"]["status"] == "unavailable"
    assert data["supplies"]["issues"]
    assert data["schedule"]["total_s"] == 7200
    assert data["route_analysis"]["critical_sections"][0]["code"] == "STEPS"


@pytest.mark.asyncio
async def test_missing_geometry_is_not_an_empty_supply_inventory(monkeypatch, briefing_client):
    briefing_client.get_tour_full.return_value = {"id": 42, "duration": 7200}
    get = Mock()
    monkeypatch.setattr(secure_http, "get", get)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_tour_briefing", {"tour_id": 42})
    assert not result.isError, result
    assert result.structuredContent is not None
    data = result.structuredContent["data"]
    assert data["total_km"] is None
    assert data["supplies"]["status"] == "unavailable"
    assert data["schedule"]["total_s"] is None
    get.assert_not_called()


@pytest.mark.parametrize("authenticated", [False, True])
def test_removed_provider_is_no_longer_an_approved_destination(authenticated):
    with pytest.raises(ValueError, match="approved HTTPS"):
        secure_http.validate_destination("https://overpass-api.de/api/interpreter", authenticated=authenticated)
