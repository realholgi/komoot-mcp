"""Private place behavior through the MCP interface and HTTP adapter."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp.client import KomootClient
from komoot_mcp.server import create_server
from komoot_mcp.tools import private_place_tools


@pytest.fixture
def account(monkeypatch):
    client = KomootClient(SimpleNamespace(), SimpleNamespace(acquire=AsyncMock()))
    client._basic_auth = Mock(return_value=("user-7", "token"))
    monkeypatch.setattr(private_place_tools, "get_client", lambda: client)
    places = {}
    calls = []

    def request(*, method, url, params, json, **kwargs):
        assert url.startswith("https://api.komoot.de/v007/users/user-7/addresses")
        calls.append((method, url, deepcopy(json)))
        if method == "GET":
            assert params == {"type": "other"}
            value = list(places.values())
        elif method == "POST":
            assert json["is_home_address"] is False
            value = {"id": 73, **json}
            places["73"] = deepcopy(value)
        elif method == "PATCH":
            assert url.endswith("/73")
            places["73"].update(json)
            value = places["73"]
        elif method == "DELETE":
            assert url.endswith("/73")
            places.pop("73")
            value = None
        else:
            raise AssertionError(method)
        captured = deepcopy(value)
        return SimpleNamespace(ok=True, status_code=204 if value is None else 200,
                               text="" if value is None else "json", json=lambda: captured)

    monkeypatch.setattr("komoot_mcp.client.requests.request", request)
    return client, places, calls


@pytest.mark.asyncio
async def test_create_list_rename_delete_without_tour_or_home_change(account):
    _, places, calls = account
    places["home"] = {"id": "home", "name": "Home", "is_home_address": True}
    async with create_connected_server_and_client_session(create_server()) as session:
        created = await session.call_tool("komoot_create_private_place", {"name": "Shelter", "lat": 49.1, "lng": 8.2})
        assert created.isError is False
        result = created.structuredContent["data"]
        assert result["place_id"] == "73"
        assert result["place"]["point"] == {"x": 8.2, "y": 49.1}
        listing = await session.call_tool("komoot_list_private_places", {})
        assert [p["name"] for p in listing.structuredContent["data"]["places"]] == ["Shelter"]
        places["73"]["address_entry"] = {"city": "Karlsruhe"}
        renamed = await session.call_tool("komoot_rename_private_place", {"place_id": "73", "name": "Lunch stop"})
        assert renamed.isError is False
        assert renamed.structuredContent["data"]["place"]["name"] == "Lunch stop"
        assert places["73"]["point"] == {"x": 8.2, "y": 49.1}
        assert places["73"]["address_entry"] == {"city": "Karlsruhe"}
        deleted = await session.call_tool("komoot_delete_private_place", {"place_id": "73"})
        assert deleted.structuredContent["data"] == {"place_id": "73", "deleted": True, "verified": True}
    assert places == {"home": {"id": "home", "name": "Home", "is_home_address": True}}
    assert [method for method, _, _ in calls].count("POST") == 1
    assert all("/addresses" in url for _, url, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("komoot_create_private_place", {"name": " ", "lat": 49, "lng": 8}),
    ("komoot_create_private_place", {"name": "Test", "lat": 91, "lng": 8}),
    ("komoot_rename_private_place", {"place_id": "../home", "name": "Test"}),
    ("komoot_delete_private_place", {"place_id": "73?x=1"}),
])
async def test_invalid_place_input_never_contacts_account(account, tool, args):
    client, _, calls = account
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool(tool, args)
    assert result.isError is True
    assert calls == []
    client._basic_auth.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("komoot_rename_private_place", {"place_id": "home", "name": "Changed"}),
    ("komoot_delete_private_place", {"place_id": "home"}),
])
async def test_home_address_cannot_be_changed(account, tool, args):
    _, places, calls = account
    places["home"] = {"id": "home", "name": "Home", "is_home_address": True}
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool(tool, args)
    assert result.isError is True
    assert [method for method, _, _ in calls] == ["GET"]
    assert places["home"]["name"] == "Home"


@pytest.mark.asyncio
async def test_accepted_create_with_failed_readback_is_not_retried(account):
    client, _, _ = account
    client._http_request = AsyncMock(side_effect=[{"id": 73}, RuntimeError("read failed")])
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_create_private_place", {"name": "Shelter", "lat": 49, "lng": 8})
    error = result.structuredContent["error"]
    assert error["place_id"] == "73"
    assert error["operation_state"] == "applied"
    assert error["confirmed_changes"] == {"write_accepted": True}
    assert error["retry_safe"] is False
    assert client._http_request.await_count == 2


@pytest.mark.asyncio
async def test_uncertain_rename_keeps_place_id_and_never_retries(account):
    client, _, _ = account
    client._http_request = AsyncMock(side_effect=[
        [{"id": 73, "point": {"x": 8, "y": 49}, "name": "Old"}], RuntimeError("timeout"),
    ])
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_rename_private_place", {"place_id": "73", "name": "New"})
    error = result.structuredContent["error"]
    assert error["place_id"] == "73"
    assert error["operation_state"] == "unknown"
    assert error["confirmed_changes"] == {}
    assert client._http_request.await_count == 2


@pytest.mark.asyncio
async def test_read_only_policy_exposes_listing_but_blocks_place_writes(account):
    _, _, calls = account
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert "komoot_list_private_places" in names
        assert not names.intersection({"komoot_create_private_place", "komoot_rename_private_place", "komoot_delete_private_place"})
        result = await session.call_tool("komoot_create_private_place", {"name": "Stop", "lat": 49, "lng": 8})
    assert result.isError is True
    assert calls == []
