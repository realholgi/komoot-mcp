"""REST behavior for native tour metadata, uploads, and binary exports."""

import json
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootAPIError, KomootClient


class NoLimit:
    async def acquire(self):
        pass


@pytest.fixture
def client():
    value = KomootClient(AuthManager(email="test@example.invalid", password="test"), NoLimit())
    value._basic_auth = Mock(return_value=("user123", "test-token"))
    return value


def response(status=200, body=None, raw=None):
    value = requests.Response()
    value.status_code = status
    value._content = raw if raw is not None else json.dumps(body).encode()
    value.encoding = "utf-8"
    return value


@pytest.mark.asyncio
async def test_list_retains_visibility_and_filters_dates_type_and_direction(client, monkeypatch):
    request = Mock(return_value=response(body={"_embedded": {"tours": [
        {"id": 1, "status": "private", "description": "Full description"},
        {"id": 2, "status": "public"},
    ]}, "page": {"totalPages": 4}}))
    monkeypatch.setattr("komoot_mcp.client.requests.request", request)
    result = await client.list_tours(
        tour_type="tour_planned", status="private", start_date="2026-01-01",
        end_date="2026-01-02", sort_direction="asc",
    )
    assert result["tours"] == [{"id": 1, "status": "private", "description": "Full description"}]
    assert result["page"] == {"totalPages": 4}
    sent = request.call_args.kwargs
    assert sent["params"]["type"] == "tour_planned"
    assert sent["params"]["sort_direction"] == "asc"
    assert sent["params"]["start_date"] == "2026-01-01T00:00:00.000Z"
    assert sent["params"]["end_date"] == "2026-01-02T23:59:59.999Z"
    assert sent["auth"] == ("user123", "test-token")


@pytest.mark.asyncio
async def test_empty_hal_page_is_a_successful_empty_list(client, monkeypatch):
    monkeypatch.setattr("komoot_mcp.client.requests.request", Mock(return_value=response(body={"page": {"totalPages": 0}})))
    result = await client.list_tours(name="nothing-matches")
    assert result["tours"] == []
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_shared_tour_uses_share_token_without_account_auth(client, monkeypatch):
    request = Mock(return_value=response(body={"id": 42, "path": [{"name": "Fontaine"}]}))
    monkeypatch.setattr("komoot_mcp.client.requests.request", request)
    result = await client.get_tour(42, share_token="share-only")
    assert result["path"] == [{"name": "Fontaine"}]
    assert request.call_args.kwargs["auth"] is None
    assert request.call_args.kwargs["params"] == {"share_token": "share-only"}
    client._basic_auth.assert_not_called()


@pytest.mark.asyncio
async def test_raw_fit_bytes_are_not_text_decoded(client, monkeypatch):
    binary = b"\x00\xff\x80.FIT\x00"
    monkeypatch.setattr("komoot_mcp.client.requests.request", Mock(return_value=response(raw=binary)))
    assert await client._http_request("GET", "https://api.komoot.de/v007/tours/42.fit", raw=True) == binary


@pytest.mark.asyncio
async def test_upload_http_status_id_and_payload_survive_transport(client, monkeypatch):
    request = Mock(return_value=response(status=202, body={"id": 42}))
    monkeypatch.setattr("komoot_mcp.client.requests.request", request)
    gpx = '<gpx version="1.1"><trk><name>Example route</name></trk></gpx>'
    result = await client.upload_tour(gpx_content=gpx, sport="hike")
    sent = request.call_args.kwargs
    assert sent["auth"] == ("user123", "test-token")
    assert sent["data"] == gpx.encode()
    assert sent["params"]["status"] == "private"
    assert result == {"id": 42, "duplicate": True}


@pytest.mark.asyncio
async def test_rest_http_failure_preserves_status_and_body(client, monkeypatch):
    monkeypatch.setattr("komoot_mcp.client.requests.request", Mock(return_value=response(status=409, raw=b"already bookmarked")))
    with pytest.raises(KomootAPIError) as error:
        await client._http_request("POST", "https://api.komoot.de/v006/test")
    assert error.value.status_code == 409
    assert error.value.body == "already bookmarked"


@pytest.mark.asyncio
async def test_save_sends_private_planned_tour_with_poi_metadata(client, monkeypatch):
    request = Mock(return_value=response(status=201, body={"id": 42}))
    monkeypatch.setattr("komoot_mcp.client.requests.post", request)
    route = {"result_status": "ok", "sport": "hike", "constitution": 3,
             "planning_summary": {"warnings": []},
             "path": [{"reference": "poi:Ab123", "name": "Fontaine", "description": "Still working"}]}
    client.get_tour = AsyncMock(return_value={
        **route, "id": 42, "type": "tour_planned", "status": "private",
    })
    result = await client.save_planned_tour(route, "Example route")
    sent = request.call_args.kwargs["json"]
    assert sent["type"] == "tour_planned"
    assert sent["status"] == "private"
    assert sent["path"] == route["path"]
    assert "result_status" not in sent
    assert route["result_status"] == "ok"
    assert result["url"] == "https://www.komoot.com/tour/42"
    assert result["verification"]["verified"] is True
    assert result["verified_visibility"] == "private"
    assert result["visibility_verified"] is True
    assert "planning_summary" not in sent
    assert sent["_embedded"]["save_options"] == {"origin": "route_planner"}
    client.get_tour.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_gpx_reader_returns_complete_xml_from_constructed_endpoint(client, monkeypatch):
    gpx = b'<gpx><wpt lat="49" lon="8"><name>Fontaine</name></wpt></gpx>'
    request = Mock(return_value=response(raw=gpx))
    monkeypatch.setattr("komoot_mcp.client.requests.request", request)
    assert await client.get_tour_gpx(42) == gpx.decode()
    assert request.call_args.kwargs["url"] == "https://api.komoot.de/v007/tours/42.gpx"
