"""Client-visible results, error signals and side-effect hints over MCP."""

import json
from unittest.mock import AsyncMock, Mock

from mcp.types import TextContent
from mcp.shared.memory import create_connected_server_and_client_session
import pytest
import requests

from komoot_mcp.client import KomootAPIError
from komoot_mcp.server import create_server
from komoot_mcp.tools import browse_tools, write_tools


@pytest.mark.asyncio
async def test_discovery_distinguishes_reads_account_changes_and_local_files():
    async with create_connected_server_and_client_session(create_server()) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
    def hints(name):
        value = tools[name].annotations
        assert value is not None
        return value

    assert hints("komoot_get_tour").readOnlyHint is True
    assert hints("komoot_plan_native_route").readOnlyHint is True
    assert hints("komoot_create_planned_tour").readOnlyHint is False
    assert hints("komoot_create_planned_tour").idempotentHint is False
    assert hints("komoot_delete_tour").destructiveHint is True
    assert hints("komoot_download_tour_gpx").readOnlyHint is False
    assert hints("komoot_download_tour_gpx").destructiveHint is True
    preview = hints("komoot_preview_prepared")
    assert preview.readOnlyHint is True and preview.openWorldHint is False
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.outputSchema is not None
        assert tool.outputSchema["required"] == ["ok"]


@pytest.mark.asyncio
async def test_full_tour_data_arrives_as_structured_content_not_encoded_json(monkeypatch):
    tour = {"id": 42, "status": "private", "distance": 1234.5,
            "path": [{"location": {"lat": 49.0, "lng": 8.0}}]}
    client = Mock(get_tour=AsyncMock(return_value=tour))
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_tour", {"tour_id": 42, "compact": False})
    assert result.isError is False
    assert result.structuredContent == {"ok": True, "data": tour}
    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == result.structuredContent
    client.get_tour.assert_awaited_once_with(42, share_token=None)


@pytest.mark.asyncio
async def test_bad_arguments_signal_error_without_reaching_komoot(monkeypatch):
    client = Mock(get_tour=AsyncMock())
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_tour", {"tour_id": "not-an-id"})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert result.structuredContent["error"]["operation_state"] == "not_attempted"
    client.get_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_error_preserves_status_and_read_retry_safety(monkeypatch):
    client = Mock(get_tour=AsyncMock(side_effect=KomootAPIError("Too many requests", status_code=429)))
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_tour", {"tour_id": 42})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"] == {
        "code": "rate_limited", "message": "Too many requests", "retry_safe": True,
        "operation_state": "unchanged", "status_code": 429,
    }


@pytest.mark.asyncio
async def test_account_timeout_does_not_recommend_retry_or_claim_no_change(monkeypatch):
    client = Mock(upload_tour=AsyncMock(side_effect=requests.Timeout("Response lost")))
    monkeypatch.setattr(write_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_upload_tour", {"gpx_content": "<gpx/>"})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"] == {
        "code": "timeout", "message": "Response lost", "retry_safe": False,
        "operation_state": "unknown",
    }
    client.upload_tour.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_tool_is_a_structured_error():
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_missing_tool", {})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "unknown_tool"
    assert result.structuredContent["error"]["operation_state"] == "not_attempted"


@pytest.mark.asyncio
async def test_unknown_parameters_are_rejected_before_the_backend(monkeypatch):
    client = Mock(get_tour=AsyncMock())
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        tool = next(tool for tool in (await session.list_tools()).tools if tool.name == "komoot_get_tour")
        result = await session.call_tool("komoot_get_tour", {"tour_id": 42, "avoid_roads": True})
    assert tool.inputSchema["additionalProperties"] is False
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "unknown_parameters"
    assert result.structuredContent["error"]["operation_state"] == "not_attempted"
    client.get_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_argument_errors_do_not_echo_private_input(monkeypatch):
    client = Mock(get_tour=AsyncMock())
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_get_tour", {"tour_id": "private-input-sentinel"})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert "private-input-sentinel" not in json.dumps(result.model_dump())
    client.get_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_validation_after_a_write_keeps_the_outcome_unknown():
    from pydantic import BaseModel

    from komoot_mcp.protocol import KomootMCP

    server = KomootMCP("synthetic-validation-test")
    changes = []

    class InternalResult(BaseModel):
        number: int

    @server.tool(name="komoot_delete_tour")
    async def simulated_write(tour_id: int) -> dict:
        changes.append(tour_id)
        InternalResult.model_validate({"number": "private-internal-sentinel"})
        return {}

    async with create_connected_server_and_client_session(server) as session:
        invalid = await session.call_tool("komoot_delete_tour", {"tour_id": "bad-id"})
        assert changes == []
        result = await session.call_tool("komoot_delete_tour", {"tour_id": 42})
    assert invalid.structuredContent is not None
    assert invalid.structuredContent["error"]["operation_state"] == "not_attempted"
    assert changes == [42]
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"] == {
        "code": "internal_error", "message": "Tool execution failed",
        "retry_safe": False, "operation_state": "unknown",
    }
    assert "private-internal-sentinel" not in json.dumps(result.model_dump())


@pytest.mark.asyncio
async def test_read_only_discovery_keeps_reads_and_local_preview():
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
    assert {"komoot_get_tour", "komoot_plan_native_route", "komoot_preview_prepared"} <= tools.keys()
    assert {"komoot_delete_tour", "komoot_upload_tour", "komoot_download_tour_fit", "komoot_adopt_receipt"}.isdisjoint(tools)
    for tool in tools.values():
        assert tool.annotations is not None and tool.annotations.readOnlyHint is True


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("komoot_delete_tour", {"tour_id": 42}),
    ("komoot_upload_tour", {"gpx_content": "<gpx/>"}),
    ("komoot_create_planned_tour", {"route_data": {}, "name": "Example"}),
    ("komoot_publish_prepared", {"prepared_path": "/not/read/prepared.json", "publication_directory": "/not/written", "confirmed": True}),
    ("komoot_download_tour_fit", {"tour_id": 42, "save_path": "/not/written/tour.fit"}),
    ("komoot_adopt_receipt", {"receipt_path": "/not/read/receipt.json", "publication_directory": "/not/written"}),
])
async def test_read_only_rejects_hidden_calls_before_auth_or_files(monkeypatch, name, arguments):
    from komoot_mcp.tools import export_tools, native_routing_tools, publication_tools

    forbidden = Mock(side_effect=AssertionError("Disabled tool reached account or filesystem code"))
    for module in (write_tools, export_tools, native_routing_tools, publication_tools):
        monkeypatch.setattr(module, "get_client", forbidden)
    monkeypatch.setattr(publication_tools, "Publication", forbidden)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool(name, arguments)
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "permission_denied"
    assert result.structuredContent["error"]["operation_state"] == "not_attempted"
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_still_executes_allowed_reads(monkeypatch):
    client = Mock(get_tour=AsyncMock(return_value={"id": 42, "status": "private"}))
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_tour", {"tour_id": 42})
    assert result.isError is False
    assert result.structuredContent == {"ok": True, "data": {"id": 42, "status": "private"}}
    client.get_tour.assert_awaited_once()
