"""Read-only place information and tour search through MCP."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp.client import KomootClient
from komoot_mcp.server import create_server
from komoot_mcp.tools import browse_tools, highlight_tools, thematic_tools


@pytest.fixture
def client(monkeypatch):
    value = KomootClient(SimpleNamespace(), SimpleNamespace(acquire=AsyncMock()))
    value._basic_auth = Mock(return_value=("user7", "token"))
    value._http_request = AsyncMock()
    for module in (browse_tools, highlight_tools, thematic_tools):
        monkeypatch.setattr(module, "get_client", lambda: value)
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("content,fields,status", [
    ({"text": "<p>Seasonal waterfall. Check flow after rain.</p>", "details": [{"key": "height", "value": 12}],
      "features": ["waterfall"], "locale": "en"}, ["text", "details", "features"], "available"),
    ({"text": "", "details": [], "features": []}, [], "empty"),
    ({"features": ["shelter"]}, ["features"], "available"),
])
async def test_editorial_content_preserves_facts_and_missing_fields(client, content, fields, status):
    client._http_request.return_value = content
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_highlight_content", {"highlight_id": 73})
    assert result.isError is False
    data = result.structuredContent["data"]
    assert data["content"] == content
    assert data["available_fields"] == fields
    assert data["status"] == status
    assert data["text"] == content.get("text")
    client._http_request.assert_awaited_once_with("GET", "https://api.komoot.de/v007/highlights/73/content")


@pytest.mark.asyncio
async def test_invalid_editorial_response_is_not_reported_as_empty(client):
    client._http_request.return_value = []
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_highlight_content", {"highlight_id": 73})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_family_waterfall_search_uses_live_filters_and_returns_tours(client):
    tours = {"_embedded": {"items": [{"id": "e123", "name": "Waterfall loop"}]}, "page": {"number": 0}}
    client._http_request.side_effect = [{"route_attributes": ["waterfalls", "family_friendly", "caves"]}, tours]
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_search_thematic_tours", {
            "lat": 48.75, "lng": 8.55, "themes": ["waterfalls"], "family_friendly": True,
        })
    assert result.isError is False
    data = result.structuredContent["data"]
    assert data["results"]["_embedded"]["items"][0]["id"] == "e123"
    assert data["requested_filters"] == ["waterfalls", "family_friendly"]
    request = client._http_request.call_args
    assert request.args == ("GET", "https://www.komoot.com/api/v007/discover_tours/from_location/")
    assert request.kwargs["params"]["route_attributes"] == "waterfalls,family_friendly"
    assert request.kwargs["params"]["max_distance"] == 20000
    assert "attributes" not in request.kwargs["params"]


@pytest.mark.asyncio
async def test_unavailable_theme_is_not_silently_dropped(client):
    client._http_request.return_value = {"route_attributes": ["family_friendly"]}
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_search_thematic_tours", {
            "lat": 48.75, "lng": 8.55, "themes": ["waterfalls"], "family_friendly": True,
        })
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert client._http_request.await_count == 1


@pytest.mark.asyncio
async def test_personal_nearby_search_preserves_start_location_and_pagination(client):
    client._http_request.return_value = {"_embedded": {"tours": [{
        "id": 42, "name": "My walk", "start_point": {"lat": 49, "lng": 8},
    }]}, "page": {"number": 0, "totalPages": 3}}
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_list_tours", {
            "center": [49, 8], "max_distance": 5000, "sort_field": "proximity", "tour_type": "tour_planned",
        })
    assert result.isError is False
    data = result.structuredContent["data"]
    assert data["tours"][0]["start_point"] == {"lat": 49, "lng": 8}
    assert data["page"]["totalPages"] == 3
    request = client._http_request.call_args
    assert request.args == ("GET", "https://api.komoot.de/v007/users/user7/tours/")
    assert request.kwargs["params"]["center"] == "49.0,8.0"
    assert request.kwargs["params"]["max_distance"] == 5000
    assert request.kwargs["params"]["sort_field"] == "proximity"
    assert request.kwargs["params"]["sort_direction"] == "asc"


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{"max_distance": 1000}, {"sort_field": "proximity"},
                                  {"center": [91, 8]}, {"center": [49, 8], "max_distance": -1}])
async def test_invalid_geographic_search_never_sends_request(client, args):
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_list_tours", args)
    assert result.isError is True
    client._http_request.assert_not_awaited()
    client._basic_auth.assert_not_called()


@pytest.mark.asyncio
async def test_native_empty_thematic_page_is_not_an_error(client):
    empty = {"page": {"size": 12, "totalElements": 0, "totalPages": 0, "number": 0}}
    client._http_request.side_effect = [{"route_attributes": ["waterfalls", "family_friendly"]}, empty]
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_search_thematic_tours", {
            "lat": 48.75, "lng": 8.55, "themes": ["waterfalls"], "family_friendly": True,
        })
    assert result.isError is False
    assert result.structuredContent["data"]["status"] == "empty"
    assert result.structuredContent["data"]["results"] == empty
