"""Offline MCP contracts for route previews and references."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp import client as client_module
from komoot_mcp import route_references, routing
from komoot_mcp.middleware import InternalSecretMiddleware, UserCredentialsMiddleware
from komoot_mcp.server import create_server
from komoot_mcp.tools import native_routing_tools as tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments", [
    ("komoot_create_planned_tour", {}),
    ("komoot_preview_tour_update", {"tour_id": 42}),
    ("komoot_update_planned_tour", {"tour_id": 42, "preview_token": "token"}),
])
async def test_route_input_errors_precede_client_access(monkeypatch, tool, arguments):
    client = Mock(side_effect=AssertionError("Invalid input reached account code"))
    monkeypatch.setattr(tools, "get_client", client)
    async with create_connected_server_and_client_session(create_server()) as session:
        for supplied, code in [
            ({}, "invalid_input"),
            ({"route_data": {}, "route_ref": "unknown"}, "invalid_input"),
            ({"route_ref": " "}, "route_ref_unavailable"),
            ({"route_ref": "private-reference-sentinel"}, "route_ref_unavailable"),
        ]:
            result = await session.call_tool(tool, {**arguments, **supplied})
            assert result.isError
            assert result.structuredContent is not None
            error = result.structuredContent["error"]
            assert error["code"] == code
            assert error["operation_state"] == "not_attempted"
            assert "private-reference-sentinel" not in result.model_dump_json()
    client.assert_not_called()


_POINTS = [[49, 8], [49.1, 8.1]]
_GEOMETRY = [[49, 8, 201], [49.015, 8.023, 243.7], [49.1, 8.1, 210]]


@pytest.fixture
def route():
    return {
        "sport": "hike", "constitution": 3, "distance": 1234, "duration": 600,
        "elevation_up": 42, "elevation_down": 33, "custom_tags": ["scenic"],
        "path": [
            {"reference": "cnw:1", "name": "Station", "description": "North entrance",
             "location": {"lat": 49, "lng": 8, "alt": 201}, "index": 0},
            {"reference": "poi:Ab12", "name": "Trailhead",
             "location": {"lat": 49.1, "lng": 8.1}, "index": 2},
        ],
        "segments": [{"type": "Routed", "from": 0, "to": 2}],
        "_embedded": {"coordinates": {"items": deepcopy(_GEOMETRY)}},
        "query": "planner-query-sentinel",
        "tour_information": [
            {"type": "STEPS", "segments": [{"from": 0, "to": 2}], "raw_detail": "warning-sentinel"},
            {"type": "CONSERVATION_AREA", "segments": [{"from": 1, "to": 2}]},
        ],
    }


@pytest.fixture
def upstream(monkeypatch, route):
    planner = Mock(return_value=deepcopy(route))
    monkeypatch.setattr(routing, "KomootNativePlanner", lambda auth: SimpleNamespace(plan=planner))
    client = Mock(
        _basic_auth=Mock(return_value=("user", "token")),
        rl=SimpleNamespace(acquire=AsyncMock()),
        _http_request=AsyncMock(),
        _komoot_post=AsyncMock(),
        save_planned_tour=AsyncMock(return_value={
            "id": 42, "visibility_verified": True, "verified_visibility": "private",
        }),
    )
    monkeypatch.setattr(tools, "get_client", Mock(return_value=client))
    return planner, client


def _data(result):
    assert not result.isError, result.structuredContent
    assert result.structuredContent is not None
    return result.structuredContent["data"]


async def _plan(session, **options):
    return _data(await session.call_tool("komoot_plan_native_route", {
        "coordinates": _POINTS, **options,
    }))


@pytest.mark.asyncio
async def test_plan_and_save_exact_snapshot_over_mcp(upstream, route):
    planner, client = upstream
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_plan_native_route", {"coordinates": _POINTS})
        preview = _data(result)
        assert preview["distance_m"] == 1234
        assert preview["duration_s"] == 600
        assert preview["route_ref_expires_in_s"] == 900
        assert preview["waypoints"] == [
            {"reference": "cnw:1", "name": "Station", "location": {"lat": 49, "lng": 8}},
            {"reference": "poi:Ab12", "name": "Trailhead", "location": {"lat": 49.1, "lng": 8.1}},
        ]
        assert preview["route_ref"]
        assert all(secret not in result.model_dump_json() for secret in (
            "_embedded", "243.7", "planner-query-sentinel", "warning-sentinel", "North entrance",
        ))
        summary = preview["planning_summary"]
        assert summary["pace"]["duration_s"] == 600
        assert summary["warnings"] == [{
            "code": "STEPS", "known": True, "message": "The route includes steps.",
            "coordinate_index_spans": [{"from": 0, "to": 2}],
        }]
        assert summary["information"][0]["code"] == "CONSERVATION_AREA"
        assert "raw" not in summary["information"][0]
        assert "raw_tour_information" not in summary
        assert "timing" in summary and "access_diagnostics" in summary
        saved = _data(await session.call_tool("komoot_create_planned_tour", {
            "route_ref": preview["route_ref"], "name": "Example",
        }))
        assert saved["tour_id"] == 42
        assert saved["visibility_verified"] is True
        assert saved["verified_visibility"] == "private"
        client.save_planned_tour.assert_awaited_once_with(planner.return_value, "Example", status="private")
        assert client.save_planned_tour.call_args.args[0]["tour_information"] == route["tour_information"]
        planner.assert_called_once()
        client._http_request.assert_not_awaited()
        client._komoot_post.assert_not_awaited()

        raw = await _plan(session, include_route_data=True)
        assert raw == planner.return_value
        assert "route_ref" not in raw
        _data(await session.call_tool("komoot_create_planned_tour", {"route_data": raw}))
        client.save_planned_tour.assert_awaited_with(raw, "Planned tour", status="private")


@pytest.mark.asyncio
async def test_original_gpx_reference_preserves_geometry_and_waypoint_text(upstream, route):
    planner, client = upstream
    original = deepcopy(route)
    original["_embedded"]["custom_named_waypoints"] = [{
        "lat": 49, "lng": 8, "index": 0, "name": "Station", "description": "North entrance",
    }]
    del original["path"][0]["name"]
    del original["path"][0]["description"]
    client._http_request.return_value = {"_embedded": {"items": [{"name": "track"}]}}
    client._komoot_post.return_value = {"_embedded": {
        "original": original, "matched": {**route, "distance": 9999},
    }}
    gpx = """<gpx version="1.1"><wpt lat="49" lon="8">
      <name>Station</name><desc>North entrance</desc></wpt></gpx>"""
    async with create_connected_server_and_client_session(create_server()) as session:
        preview = _data(await session.call_tool("komoot_import_gpx_route", {
            "gpx_content": gpx, "match_route": False,
        }))
        assert preview["distance_m"] == 1234
        assert preview["waypoints"][0]["name"] == "Station"
        _data(await session.call_tool("komoot_create_planned_tour", {"route_ref": preview["route_ref"]}))
    written = client.save_planned_tour.call_args.args[0]
    assert written["_embedded"]["coordinates"]["items"] == _GEOMETRY
    assert written["path"][0] == route["path"][0]
    client._http_request.assert_awaited_once()
    client._komoot_post.assert_awaited_once()
    planner.assert_not_called()


@pytest.mark.asyncio
async def test_variant_reference_saves_only_the_selected_route(upstream, route):
    planner, client = upstream
    routes = [deepcopy(route) for _ in range(3)]
    for variant, (distance, latitude) in zip(routes, [(1200, 49.01), (1500, 49.02), (1300, 49.03)]):
        variant["distance"] = distance
        variant["_embedded"]["coordinates"]["items"][1][0] = latitude
    planner.side_effect = [*routes, *routes]
    async with create_connected_server_and_client_session(create_server()) as session:
        comparison = _data(await session.call_tool("komoot_compare_route_variants", {"coordinates": _POINTS}))
        assert comparison["baseline"] == "shortest"
        assert comparison["limitation"]
        variants = comparison["variants"]
        assert [variants[name]["deltas_vs_shortest"]["distance_m"] for name in ("shortest", "scenic", "quiet")] == [
            0, 300, 100,
        ]
        assert len({variant["route"]["route_ref"] for variant in variants.values()}) == 3
        _data(await session.call_tool("komoot_create_planned_tour", {
            "route_ref": variants["scenic"]["route"]["route_ref"],
        }))
        client.save_planned_tour.assert_awaited_once_with(routes[1], "Planned tour", status="private")
        assert planner.call_count == 3

        full = _data(await session.call_tool("komoot_compare_route_variants", {
            "coordinates": _POINTS, "include_route_data": True,
        }))
        assert [full["variants"][name]["route"] for name in ("shortest", "scenic", "quiet")] == routes


@pytest.mark.asyncio
async def test_replacement_reference_still_requires_a_current_preview(monkeypatch, upstream):
    planner, _ = upstream
    client = client_module.KomootClient(None, SimpleNamespace(acquire=AsyncMock()))
    monkeypatch.setattr(tools, "get_client", lambda: client)
    monkeypatch.setattr(client, "_basic_auth", lambda: ("user", "token"))
    current = {
        "id": 42, "type": "tour_planned", "status": "private", "name": "Kept",
        "description": "Original", "date": "2026-01-01T00:00:00Z", "gear": "Boots",
    }
    current_geometry = [[49, 8], [50, 9]]
    url = "https://api.komoot.de/v007/tours/42"
    written = None

    def request(*, method, url, json, **_kwargs):
        nonlocal written
        if method == "PUT":
            written = json
            body = {}
        elif url.endswith("/coordinates"):
            body = {"items": current_geometry if written is None else written["_embedded"]["coordinates"]["items"]}
        else:
            body = current.copy() if written is None else written
        return Mock(ok=True, status_code=200, text="{}", json=Mock(return_value=body))

    send = Mock(side_effect=request)
    monkeypatch.setattr(client_module.requests, "request", send)
    async with create_connected_server_and_client_session(create_server()) as session:
        target = {"tour_id": 42, "route_ref": (await _plan(session))["route_ref"]}
        preview = _data(await session.call_tool("komoot_preview_tour_update", target))
        current["name"] = "Renamed"
        conflict = await session.call_tool("komoot_update_planned_tour", {
            **target, "preview_token": preview["preview_token"],
        })
        assert conflict.isError
        assert conflict.structuredContent is not None
        assert conflict.structuredContent["error"]["code"] == "preview_conflict"
        assert [call.kwargs["method"] for call in send.call_args_list] == ["GET"] * 4
        assert written is None

        preview = _data(await session.call_tool("komoot_preview_tour_update", target))
        saved = _data(await session.call_tool("komoot_update_planned_tour", {
            **target, "preview_token": preview["preview_token"],
        }))
    assert [call.kwargs["method"] for call in send.call_args_list[4:]] == ["GET", "GET", "GET", "GET", "PUT", "GET", "GET"]
    assert send.call_args_list[8].kwargs["url"] == url
    assert written is not None
    assert {key: written[key] for key in current} == current
    assert written["_embedded"]["coordinates"]["items"] == _GEOMETRY
    assert written["path"] == planner.return_value["path"]
    assert saved["tour_id"] == 42 and saved["verification"]["verified"] is True
    planner.assert_called_once()


@pytest.mark.asyncio
async def test_references_are_scoped_to_exact_credentials_over_http(monkeypatch, upstream):
    _, client = upstream
    monkeypatch.setenv("INTERNAL_SECRET", "gateway")
    server = create_server()
    server.settings.json_response = True
    app = server.streamable_http_app()
    app.add_middleware(UserCredentialsMiddleware)
    app.add_middleware(InternalSecretMiddleware)
    alice = "Alice@example.test"
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
    ) as http:
        async def call(email, password, tool, arguments):
            response = await http.post("/mcp", headers={
                "Host": "127.0.0.1:8000",
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer gateway",
                "x-user-credentials": json.dumps({"email": email, "password": password}),
            }, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            })
            response.raise_for_status()
            return response.json()["result"]

        planned = await call(alice, "original", "komoot_plan_native_route", {"coordinates": _POINTS})
        ref = planned["structuredContent"]["data"]["route_ref"]
        save = "komoot_create_planned_tour"
        missing = await call(alice, "original", save, {"route_ref": "unknown"})
        for email, password in [(alice, "changed"), ("bob@example.test", "original"), (alice.lower(), "original")]:
            result = await call(email, password, save, {"route_ref": ref})
            assert result == missing
        assert missing["isError"]
        assert missing["structuredContent"]["error"]["code"] == "route_ref_unavailable"
        assert missing["structuredContent"]["error"]["operation_state"] == "not_attempted"
        client_factory = tools.get_client
        assert isinstance(client_factory, Mock)
        client_factory.assert_called_once_with()
        client.save_planned_tour.assert_not_awaited()
        saved = await call(alice, "original", save, {"route_ref": ref})
        assert saved["structuredContent"]["data"]["tour_id"] == 42
        client.save_planned_tour.assert_awaited_once()


@pytest.mark.asyncio
async def test_reference_reuse_does_not_extend_expiry(monkeypatch, upstream):
    _, client = upstream
    clock = Mock(return_value=1000.0)
    monkeypatch.setattr(route_references, "monotonic", clock)
    async with create_connected_server_and_client_session(create_server()) as session:
        arguments = {"route_ref": (await _plan(session))["route_ref"]}
        _data(await session.call_tool("komoot_create_planned_tour", {**arguments, "sport": "road_cycle"}))
        clock.return_value = 1899.0
        _data(await session.call_tool("komoot_create_planned_tour", arguments))
        assert [call.args[0]["sport"] for call in client.save_planned_tour.call_args_list] == ["racebike", "hike"]
        clock.return_value = 1900.0
        result = await session.call_tool("komoot_create_planned_tour", arguments)
        assert result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["error"]["code"] == "route_ref_unavailable"
        assert result.structuredContent["error"]["operation_state"] == "not_attempted"
        client_factory = tools.get_client
        assert isinstance(client_factory, Mock)
        assert client_factory.call_count == 3
        assert client.save_planned_tour.await_count == 2


@pytest.mark.asyncio
async def test_variant_capacity_failure_keeps_active_refs_and_adds_none(monkeypatch, upstream):
    _, client = upstream
    monkeypatch.setattr(route_references, "_MAX_ENTRIES", 3)
    async with create_connected_server_and_client_session(create_server()) as session:
        original = (await _plan(session))["route_ref"]
        failed = await session.call_tool("komoot_compare_route_variants", {"coordinates": _POINTS})
        assert failed.isError
        assert failed.structuredContent is not None
        error = failed.structuredContent["error"]
        assert error["code"] == "route_ref_capacity"
        assert error["operation_state"] == "not_attempted"
        assert "include_route_data=true" in error["message"]
        client.save_planned_tour.assert_not_awaited()
        assert (await _plan(session))["route_ref"] != original
        assert (await _plan(session))["route_ref"] != original
        assert "_embedded" in await _plan(session, include_route_data=True)
        saved = _data(await session.call_tool("komoot_create_planned_tour", {"route_ref": original}))
        assert saved["tour_id"] == 42


@pytest.mark.asyncio
async def test_preview_rejects_non_json_route_without_exposing_payload(upstream):
    planner, client = upstream
    planner.return_value["query"] = {"private-query": float("nan")}
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool("komoot_plan_native_route", {"coordinates": _POINTS})
    assert result.isError
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "route_ref_invalid"
    assert result.structuredContent["error"]["operation_state"] == "not_attempted"
    assert "private-query" not in result.model_dump_json()
    client.save_planned_tour.assert_not_awaited()
