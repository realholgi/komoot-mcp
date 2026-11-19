"""Phase 3 tests — 20 new tools that bypass kompy via direct REST.

Each tool wraps a ``client.py`` method that hits Komoot directly with
``requests``. We monkeypatch ``requests.request`` (the single
underlying call all ``_http_request`` helpers route through) so a
single tiny fixture covers GET/POST/PATCH/DELETE.

Tests exercise the *tool* layer (not just the client) because that's
where native values and raised failures become consumer-visible.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.context import (
    clear_request_state,
    set_auth_manager,
    reset_auth_manager,
)


@pytest.fixture(autouse=True)
def _reset_context():
    clear_request_state()
    yield
    clear_request_state()


class _NoLimit:
    async def acquire(self):
        return None


def _make_response(*, status=200, json_body=None, text="ok"):
    """Mimic the requests.Response surface our helpers care about."""
    resolved_text = text if json_body is None else "{}"
    is_ok = 200 <= status < 300
    body = json_body

    class _Resp:
        status_code = status
        ok = is_ok
        headers = {"content-type": "application/json"}
        text = resolved_text

        def json(self):
            if body is None:
                raise ValueError("no json")
            return body

    return _Resp()


@pytest.fixture
def registered_tools():
    """Register all Phase 3 tools onto a recorder MCP and return the map."""
    registry: dict[str, callable] = {}

    class _Mcp:
        def tool(self):
            def decorator(fn):
                registry[fn.__name__] = fn
                return fn
            return decorator

    from komoot_mcp.tools import (
        browse_tools, write_tools, highlight_tools, discover_tools,
        collection_tools, share_tools, native_routing_tools,
    )
    browse_tools.register(_Mcp())
    write_tools.register(_Mcp())
    highlight_tools.register(_Mcp())
    discover_tools.register(_Mcp())
    collection_tools.register(_Mcp())
    share_tools.register(_Mcp())
    native_routing_tools.register(_Mcp())
    return registry


@pytest.fixture
def auth_token():
    am = AuthManager(email="t@x.com", password="pw")
    am.user_id = "123"
    am.token = "tok"
    token = set_auth_manager(am)
    yield token
    reset_auth_manager(token)


def _patch_request(json_body=None, text="ok", status=200):
    """Patch ``requests.request`` to return a canned response.

    All Phase 3 helpers go through ``requests.request``, so this single
    patch covers GET / POST / PATCH / DELETE.
    """
    resp = _make_response(status=status, json_body=json_body, text=text)
    return patch("komoot_mcp.client.requests.request", return_value=resp)


def _stub_kompy_auth(client):
    """Skip kompy login by injecting a stub authenticated connector."""
    client._api = SimpleNamespace(
        authentication=SimpleNamespace(
            get_username=lambda: "user123",
            get_token=lambda: "tokenABC",
        )
    )


# ----------------------------------------------------------------
# Tour metadata tools (5)
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tour_photos(registered_tools, auth_token):
    body = {
        "_embedded": {"items": [
            {"id": 1, "src": "https://img.komoot.de/x/{width}/{height}/{crop}",
             "rating": 4.7},
            {"id": 2, "src": "https://img.komoot.de/y/{width}/{height}/{crop}"},
        ]}
    }
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_get_tour_photos"](tour_id=42)
    assert out == body


@pytest.mark.asyncio
async def test_get_tour_line(registered_tools, auth_token):
    # Live shape (live-probed 2026-05-18): the response uses
    # ``geometry`` (not ``coordinates``).
    body = {
        "tour_id": 42,
        "geometry": [
            {"lat": 47.1, "lng": 11.4, "alt": 600},
            {"lat": 47.2, "lng": 11.5, "alt": 650},
        ],
    }
    with _patch_request(json_body=body) as mock_req:
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_get_tour_line"](tour_id=42)
    # Confirm we hit the corrected URL on www.komoot.com (not
    # api.komoot.de/.../line, which 404s).
    called_url = mock_req.call_args.kwargs.get("url")
    assert "www.komoot.com/api/v007/tours/42/tour_line" in called_url
    assert out == body


@pytest.mark.asyncio
async def test_create_share_link(registered_tools, auth_token):
    body = {"token": "abc123"}
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_create_share_link"](tour_id=42)
    assert out == {"token": "abc123", "tour_id": 42, "url": "https://www.komoot.com/tour/42?share_token=abc123"}


@pytest.mark.asyncio
async def test_revoke_share_link(registered_tools, auth_token):
    # DELETE often returns 204 No Content — patch the response shape.
    resp = _make_response(status=204, text="")
    with patch("komoot_mcp.client.requests.request", return_value=resp):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_revoke_share_link"](tour_id=42)
    assert out == {"tour_id": 42, "revoked": True}


@pytest.mark.asyncio
async def test_modify_tour_extended(registered_tools, auth_token):
    body = {"id": 42, "name": "new", "description": "d"}
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_modify_tour_extended"](
            tour_id=42, name="new", description="d",
        )
    assert out == {"tour_id": 42, "updated_fields": {"name": "new", "description": "d"}, "result": body}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tour_type,field", [("tour_planned", "duration"), ("tour_recorded", "time_in_motion")],
)
async def test_modify_average_speed_preserves_geometry(
    registered_tools, auth_token, tour_type, field,
):
    from komoot_mcp.context import get_client
    _stub_kompy_auth(get_client())
    current = {
        "id": 42, "type": tour_type, "distance": 10000, "duration": 7200,
        "time_in_motion": 6000, "path": [{"location": {"lat": 48, "lng": 8}}],
    }
    saved = {**current, field: 3600}
    responses = [
        _make_response(json_body=current),
        _make_response(status=204, text=""),
        _make_response(json_body=saved),
    ]
    with patch("komoot_mcp.client.requests.request", side_effect=responses) as request:
        out = await registered_tools["komoot_modify_tour_extended"](42, speed_kmh=10)
    assert [call.kwargs["method"] for call in request.call_args_list] == ["GET", "PATCH", "GET"]
    assert request.call_args_list[1].kwargs["json"] == {field: 3600}
    assert out["result"] == saved


@pytest.mark.asyncio
async def test_modify_explicit_timing_and_fitness(registered_tools, auth_token):
    from komoot_mcp.context import get_client
    _stub_kompy_auth(get_client())
    current = {"id": 42, "type": "tour_planned", "duration": 7200, "constitution": 3}
    saved = {**current, "duration": 5400, "constitution": 4, "name": "Summit"}
    with patch("komoot_mcp.client.requests.request", side_effect=[
        _make_response(json_body=current), _make_response(json_body={}),
        _make_response(json_body=saved),
    ]) as request:
        out = await registered_tools["komoot_modify_tour_extended"](
            42, duration_s=5400, constitution=4, name="Summit",
        )
    assert request.call_args_list[1].kwargs["json"] == {
        "duration": 5400, "constitution": 4, "name": "Summit",
    }
    assert out["result"]["constitution"] == 4


@pytest.mark.asyncio
async def test_replace_planned_route_uses_put_and_two_readbacks(registered_tools, auth_token):
    from komoot_mcp.context import get_client
    _stub_kompy_auth(get_client())
    current = {"id": 42, "type": "tour_planned", "status": "private", "name": "Kept",
               "description": "Original", "date": "2026-01-01T00:00:00Z", "gear": "Boots"}
    route = {
        "sport": "hike", "constitution": 4,
        "path": [
            {"location": {"lat": 49, "lng": 8}, "index": 0, "reference": "poi:Ab12"},
            {"location": {"lat": 50, "lng": 9}, "index": 1, "reference": "cnw:1"},
        ],
        "segments": [{"type": "Routed", "from": 0, "to": 1}],
        "_embedded": {"coordinates": {"items": [[49, 8], [50, 9]]}},
        "planning_summary": {"warnings": []},
    }
    from komoot_mcp.change_preview import preview_token
    token = preview_token(42, current, [[49, 8], [50, 9]], route)
    with patch("komoot_mcp.client.requests.request", side_effect=[
        _make_response(json_body=current),
        _make_response(json_body={"items": [[49, 8], [50, 9]]}),
        _make_response(json_body={}),
        _make_response(json_body={**route, **current}),
        _make_response(json_body={"items": [[49, 8], [50, 9]]}),
    ]) as request:
        out = await registered_tools["komoot_update_planned_tour"](42, route, token)
    assert [call.kwargs["method"] for call in request.call_args_list] == ["GET", "GET", "PUT", "GET", "GET"]
    write = request.call_args_list[2].kwargs
    assert write["url"] == "https://api.komoot.de/v007/tours/42"
    assert write["json"]["name"] == "Kept"
    assert write["json"]["path"] == route["path"]
    assert "planning_summary" not in write["json"]
    assert out["tour_id"] == 42
    assert out["verification"]["verified"] is True
    assert out["visibility_verified"] is True


@pytest.mark.asyncio
async def test_modify_speed_detects_ignored_update(registered_tools, auth_token):
    from komoot_mcp.client import KomootAPIError
    from komoot_mcp.context import get_client
    _stub_kompy_auth(get_client())
    current = {"id": 42, "type": "tour_planned", "distance": 10000, "duration": 7200}
    with _patch_request(json_body=current) as request:
        with pytest.raises(KomootAPIError, match="readback differs for duration"):
            await registered_tools["komoot_modify_tour_extended"](42, speed_kmh=10)
    assert request.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"speed_kmh": 0}, {"speed_kmh": float("inf")}, {"speed_kmh": -1},
    {"speed_kmh": 10, "duration_s": 3600}, {"duration_s": 0},
    {"time_in_motion_s": -1}, {"constitution": 6},
])
async def test_modify_invalid_timing_never_requests(registered_tools, fields):
    with patch("komoot_mcp.client.requests.request") as request:
        with pytest.raises(ValueError):
            await registered_tools["komoot_modify_tour_extended"](42, **fields)
    request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tour,fields", [
    ({"type": "tour_recorded", "duration": 7200}, {"constitution": 4}),
    ({"type": "tour_planned"}, {"time_in_motion_s": 3600}),
    ({"type": "tour_recorded", "distance": 10000, "duration": 1800}, {"speed_kmh": 10}),
    ({"type": "tour_recorded", "time_in_motion": 3600, "duration": 7200}, {"duration_s": 1800}),
])
async def test_modify_incompatible_timing_never_patches(
    registered_tools, auth_token, tour, fields,
):
    from komoot_mcp.context import get_client
    _stub_kompy_auth(get_client())
    with _patch_request(json_body=tour) as request:
        with pytest.raises(ValueError):
            await registered_tools["komoot_modify_tour_extended"](42, **fields)
    assert [call.kwargs["method"] for call in request.call_args_list] == ["GET"]


# ----------------------------------------------------------------
# Highlight tools (3)
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_highlight_images(registered_tools, auth_token):
    body = {"_embedded": {"items": [
        {"id": 11, "src": "https://img.komoot.de/h/{width}/{height}/{crop}",
         "attribution": "Anna"},
    ]}}
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_get_highlight_images"](
            highlight_id=99,
        )
    assert out == body


@pytest.mark.asyncio
async def test_get_highlight_tips(registered_tools, auth_token):
    body = {"_embedded": {"items": [
        {"id": 5, "text": "Bring water!",
         "creator": {"display_name": "Bob"}},
    ]}}
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_get_highlight_tips"](
            highlight_id=99,
        )
    assert out == body


@pytest.mark.asyncio
async def test_list_user_highlights(registered_tools, auth_token):
    body = {"_embedded": {"items": [
        {"id": 7, "name": "Marienplatz", "sports": "hike",
         "category": "viewpoint"},
    ]}}
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_list_user_highlights"](
            user_id="123",
        )
    assert out == body


# ----------------------------------------------------------------
# Discover / smart tour tools (5)
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_smart_tours_near(registered_tools, auth_token):
    body = {"_embedded": {"items": [
        {"id": 100, "name": "Alps Loop", "sport": "mountainbike",
         "distance": 25000, "elevation_up": 1200},
    ]}}
    with _patch_request(json_body=body) as mock_req:
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_smart_tours_near"](
            lat=47.0, lng=11.0, sport="mountainbike",
        )
    # Confirm we hit the corrected from_location URL.
    called_url = mock_req.call_args.kwargs.get("url")
    assert "discover_tours/from_location" in called_url
    params = mock_req.call_args.kwargs.get("params") or {}
    assert params.get("sport") == "mountainbike"
    assert params.get("max_distance") == 20000
    assert out == body


@pytest.mark.asyncio
async def test_smart_tour_for_highlight(registered_tools, auth_token):
    body = {"_embedded": {"items": [
        {"id": 200, "name": "Round trip", "sport": "hike",
         "distance": 8000},
    ]}}
    with _patch_request(json_body=body) as mock_req:
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_smart_tour_for_highlight"](
            highlight_id=99, lat=47.0, lng=11.0, sport="hike",
        )
    # Confirm we hit the corrected from_location URL with
    # highlight_id as a query param (no for_highlight path exists).
    called_url = mock_req.call_args.kwargs.get("url")
    assert "discover_tours/from_location" in called_url
    params = mock_req.call_args.kwargs.get("params") or {}
    assert params.get("highlight_id") == 99
    assert out == body


@pytest.mark.asyncio
async def test_discover_with_attributes(registered_tools, auth_token):
    body = {"_embedded": {"items": [
        {"id": 400, "name": "Scenic ridge", "sport": "hike",
         "distance": 12000},
    ]}}
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_discover_with_attributes"](
            lat=47.0, lng=11.0, attributes="scenic",
        )
    assert out == body


@pytest.mark.asyncio
async def test_route_attribute_options(registered_tools, auth_token):
    # Live shape (live-probed 2026-05-18): plain {"route_attributes":
    # [...]} — and ALL four query params are required.
    body = {"route_attributes": ["waterfalls", "lakes_rivers", "cafe"]}
    with _patch_request(json_body=body) as mock_req:
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_route_attribute_options"](
            lat=47.9959, lng=7.8522, sport="mountainbike",
        )
    params = mock_req.call_args.kwargs.get("params") or {}
    # All four required by the Komoot endpoint — missing any returns 400.
    assert params.get("lat") == 47.9959
    assert params.get("lng") == 7.8522
    assert params.get("sport") == "mountainbike"
    assert params.get("max_distance") == 20000
    assert out == body


# ----------------------------------------------------------------
# Collection tools (3)
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_collection(registered_tools, auth_token):
    body = {
        "id": 500, "name": "Best gravel rides", "sport": "touringbicycle",
        "number_of_tours": 12,
        "creator": {"display_name": "Lena"},
        "description": "Curated mix of rolling backroads",
    }
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_get_collection"](
            collection_id=500,
        )
    assert out == body


@pytest.mark.asyncio
async def test_get_collection_tours(registered_tools, auth_token):
    body = {"_embedded": {"items": [
        {"_embedded": {"tour": {
            "id": 11, "name": "Saturday loop", "sport": "racebike",
            "distance": 60000,
        }}},
    ]}}
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_get_collection_tours"](
            collection_id=500,
        )
    assert out == body


# ----------------------------------------------------------------
# Resolvers (1)
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_share_url(registered_tools, auth_token):
    body = {
        "id": 12345, "name": "Shared loop", "sport": "hike",
        "distance": 9500, "elevation_up": 300,
        "status": "public", "date": "2026-05-01T10:00:00Z",
    }
    with _patch_request(json_body=body):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        out = await registered_tools["komoot_resolve_share_url"](
            share_url="https://www.komoot.com/tour/12345?share_token=abc",
        )
    assert out == {"tour_id": 12345, "share_token": "abc", "tour": body}


# ----------------------------------------------------------------
# Error path + client unit checks
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_raise_api_errors(
    registered_tools, auth_token,
):
    """If Komoot returns 500, the handlers propagate typed API errors."""
    resp = _make_response(status=500, text="boom")
    with patch("komoot_mcp.client.requests.request", return_value=resp):
        from komoot_mcp.context import get_client
        _stub_kompy_auth(get_client())
        from komoot_mcp.client import KomootAPIError
        with pytest.raises(KomootAPIError):
            await registered_tools["komoot_get_tour_line"](tour_id=42)
        with pytest.raises(KomootAPIError):
            await registered_tools["komoot_modify_tour_extended"](tour_id=42, name="x")


@pytest.mark.asyncio
async def test_client_modify_tour_extended_empty_body_raises(auth_token):
    """No fields → KomootAPIError so callers don't accidentally PATCH nothing."""
    from komoot_mcp.client import KomootAPIError
    from komoot_mcp.context import get_client
    client = get_client()
    _stub_kompy_auth(client)
    client.rl = _NoLimit()
    with pytest.raises(KomootAPIError):
        await client.modify_tour_extended(tour_id=42)
