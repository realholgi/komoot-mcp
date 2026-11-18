"""Coordinate pages preserve order and reject invalid budgets before network access."""

from unittest.mock import AsyncMock, Mock

from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from komoot_mcp.server import create_server
from komoot_mcp.tools import data_tools


@pytest.mark.asyncio
async def test_coordinate_pages_preserve_values_and_terminate(monkeypatch):
    coordinates = [[49.0, 8.0, 120], [49.1, 8.1, 125], [49.2, 8.2, None]]
    client = Mock(get_tour_coordinates=AsyncMock(return_value=coordinates))
    monkeypatch.setattr(data_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        first = await session.call_tool("komoot_get_tour_coordinates", {"tour_id": 42, "limit": 2})
        last = await session.call_tool("komoot_get_tour_coordinates", {"tour_id": 42, "offset": 2, "limit": 2})
        past_end = await session.call_tool("komoot_get_tour_coordinates", {"tour_id": 42, "offset": 10, "limit": 2})
    assert first.structuredContent == {"ok": True, "data": {
        "tour_id": 42, "coordinates": [[49.0, 8.0, 120], [49.1, 8.1, 125]],
        "offset": 0, "limit": 2, "total": 3, "next_offset": 2, "truncated": True,
    }}
    assert last.structuredContent == {"ok": True, "data": {
        "tour_id": 42, "coordinates": [[49.2, 8.2, None]],
        "offset": 2, "limit": 2, "total": 3, "next_offset": None, "truncated": False,
    }}
    assert past_end.structuredContent == {"ok": True, "data": {
        "tour_id": 42, "coordinates": [], "offset": 10, "limit": 2,
        "total": 3, "next_offset": None, "truncated": False,
    }}


@pytest.mark.asyncio
async def test_default_page_does_not_inline_the_entire_track(monkeypatch):
    client = Mock(get_tour_coordinates=AsyncMock(return_value=[[49, 8, altitude] for altitude in range(501)]))
    monkeypatch.setattr(data_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_tour_coordinates", {"tour_id": 42})
    assert result.structuredContent is not None
    data = result.structuredContent["data"]
    assert data["coordinates"][0] == [49, 8, 0]
    assert data["coordinates"][-1] == [49, 8, 499]
    assert data["limit"] == 500 and data["total"] == 501
    assert data["next_offset"] == 500 and data["truncated"] is True
    assert [49, 8, 500] not in data["coordinates"]


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 1001}, {"limit": True}, {"limit": 1.5},
    {"offset": -1}, {"offset": True}, {"offset": 1.5},
])
async def test_invalid_page_limits_fail_before_resolving_credentials(monkeypatch, options):
    forbidden = Mock(side_effect=AssertionError("Invalid budget reached credentials"))
    monkeypatch.setattr(data_tools, "get_client", forbidden)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_tour_coordinates", {"tour_id": 42, **options})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert result.structuredContent["error"]["operation_state"] == "not_attempted"
    forbidden.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, "not a coordinate array"])
async def test_malformed_upstream_coordinates_are_not_reported_as_a_page(monkeypatch, payload):
    client = Mock(get_tour_coordinates=AsyncMock(return_value=payload))
    monkeypatch.setattr(data_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_tour_coordinates", {"tour_id": 42})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "upstream_error"
