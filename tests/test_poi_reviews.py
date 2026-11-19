"""External reviews retain provider data and distinguish empty results from errors."""

from unittest.mock import AsyncMock, Mock

import pytest
import requests
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp.client import KomootAPIError
from komoot_mcp.server import create_server
from komoot_mcp.tools import poi_review_tools


@pytest.mark.asyncio
async def test_reviews_are_available_in_read_only_mode_with_a_string_id_schema():
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        tools = await session.list_tools()
    tool = next(tool for tool in tools.tools if tool.name == "komoot_get_poi_external_reviews")
    assert tool.inputSchema["properties"]["poi_id"]["type"] == "string"
    assert tool.inputSchema["required"] == ["poi_id"]
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_empty_reviews_are_successful_and_preserve_response_metadata(monkeypatch):
    payload = {"reviews": [], "_links": {"self": {"href": "/pois/7mn4K/external_reviews"}}}
    client = Mock(_http_request=AsyncMock(return_value=payload))
    monkeypatch.setattr(poi_review_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_poi_external_reviews", {"poi_id": "7mn4K"})
    assert result.isError is False
    assert result.structuredContent == {"ok": True, "data": {
        "poi_id": "7mn4K", "status": "empty", "external_reviews": payload,
    }}
    client._http_request.assert_awaited_once_with(
        "GET", "https://api.komoot.de/v007/pois/7mn4K/external_reviews",
    )


@pytest.mark.asyncio
async def test_nonempty_reviews_preserve_provider_fields_and_values(monkeypatch):
    # Synthetic contract data based on the shipped renderer, not a live rating.
    payload = {"reviews": [
        {"provider_id": "yelp", "url": "https://example.test/yelp?ref=komoot",
         "rating_value": "4.5", "rating_count": 17, "source": {"name": "Yelp"}},
        {"provider_id": "tripadvisor", "url": "https://example.test/tripadvisor",
         "rating_image_url": "https://example.test/rating.svg", "rating_count": "008"},
        {"provider_id": "unknown-provider", "score": 0, "count": None,
         "_links": {"source": {"href": "https://example.test/source"}}, "text": "Original review"},
    ], "attribution": "Original attribution"}
    client = Mock(_http_request=AsyncMock(return_value=payload))
    monkeypatch.setattr(poi_review_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_poi_external_reviews", {"poi_id": "7mn4K"})
    assert result.isError is False
    assert result.structuredContent == {"ok": True, "data": {
        "poi_id": "7mn4K", "status": "available", "external_reviews": payload,
    }}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    None, [], {}, {"reviews": None}, {"reviews": {}}, {"reviews": ""},
    {"reviews": [None]}, {"reviews": ["review"]},
])
async def test_malformed_reviews_are_upstream_errors_not_empty_results(monkeypatch, payload):
    client = Mock(_http_request=AsyncMock(return_value=payload))
    monkeypatch.setattr(poi_review_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_poi_external_reviews", {"poi_id": "7mn4K"})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["error"]["code"] == "upstream_error"
    assert result.structuredContent["error"]["operation_state"] == "unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize("poi_id", ["", "7mn4K/", "../42", "poi:7mn4K", "node/123", "é123", "７mn4K", "7mn4K\n", " 7mn4K", 42, None])
async def test_invalid_poi_ids_fail_before_credentials_or_network(monkeypatch, poi_id):
    forbidden = Mock(side_effect=AssertionError("Invalid ID reached credentials"))
    monkeypatch.setattr(poi_review_tools, "get_client", forbidden)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_poi_external_reviews", {"poi_id": poi_id})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "invalid_input"
    forbidden.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,code,retry_safe", [
    (KomootAPIError("POI not found", status_code=404), "not_found", False),
    (KomootAPIError("Authentication failed", status_code=401), "authentication_failed", False),
    (KomootAPIError("Access denied", status_code=403), "permission_denied", False),
    (KomootAPIError("Rate limited", status_code=429), "rate_limited", True),
    (KomootAPIError("Service unavailable", status_code=503), "upstream_error", True),
    (requests.Timeout("Request timed out"), "timeout", True),
])
async def test_request_errors_remain_mcp_errors(monkeypatch, error, code, retry_safe):
    client = Mock(_http_request=AsyncMock(side_effect=error))
    monkeypatch.setattr(poi_review_tools, "get_client", lambda: client)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_get_poi_external_reviews", {"poi_id": "7mn4K"})
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is False
    details = result.structuredContent["error"]
    assert details["code"] == code
    assert details["retry_safe"] is retry_safe
    assert details["operation_state"] == "unchanged"
    assert "data" not in result.structuredContent
