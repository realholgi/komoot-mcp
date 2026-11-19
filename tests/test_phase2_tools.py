"""Tests for the four phase-2 tools added in feat/phase2-...

The new client methods (``get_tour_full``, ``get_highlight``,
``get_tour_weather``, ``discover_near``) all share the same plumbing:
they call ``requests.get`` directly with Basic auth pulled off the
kompy-built ``api.authentication``. We mock ``requests.get`` at the
module level inside ``komoot_mcp.client`` so the network is never
touched and we can assert URL + param + auth wiring.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootAPIError, KomootClient


class _NoLimit:
    async def acquire(self):
        return None


@pytest.fixture
def client():
    am = AuthManager(email="t@x.com", password="pw")
    c = KomootClient(am, _NoLimit())
    # Pre-seed ``_api`` so ``_basic_auth`` doesn't try to construct a
    # real kompy connector. Mirrors the pattern used by
    # ``test_client_tour_methods._install_api_stub``.
    api = MagicMock()
    auth = MagicMock()
    auth.get_username.return_value = "12345"
    auth.get_token.return_value = "long-lived-token"
    api.authentication = auth
    c._api = api
    return c


def _resp(status=200, json_body=None, text=""):
    """Build a fake ``requests.Response``-shaped object."""
    r = SimpleNamespace()
    r.status_code = status
    r.text = text

    def _json():
        if json_body is None:
            raise ValueError("no json")
        return json_body

    r.json = _json
    return r


# ---------------------------------------------------------------- get_tour_full

class TestGetTourFull:
    @pytest.mark.asyncio
    async def test_hits_embed_url_with_all_embeds(self, client):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _resp(200, {
                "id": 42,
                "name": "Feldberg loop",
                "sport": "hike",
                "_embedded": {
                    "coordinates": {"items": [{}] * 2081},
                    "way_types": {"items": [{"type": "trail", "amount": 0.7}]},
                    "surfaces": {"items": [{"type": "gravel", "amount": 0.6}]},
                    "directions": {"items": [{}] * 12},
                    "timeline": {"items": []},
                },
            })

        with patch("komoot_mcp.client.requests.get", side_effect=fake_get):
            out = await client.get_tour_full(42)

        assert captured["url"] == "https://api.komoot.de/v007/tours/42"
        params = captured["kwargs"]["params"]
        # All the embeds the planner spec called for
        assert "coordinates" in params["_embedded"]
        assert "way_types" in params["_embedded"]
        assert "surfaces" in params["_embedded"]
        assert "directions" in params["_embedded"]
        assert "timeline" in params["_embedded"]
        assert "cover_images" in params["_embedded"]
        assert params["directions"] == "v2"
        # Basic auth threaded through from kompy.authentication
        assert captured["kwargs"]["auth"] == ("12345", "long-lived-token")
        assert out["name"] == "Feldberg loop"

    @pytest.mark.asyncio
    async def test_404_raises_friendly_error(self, client):
        with patch(
            "komoot_mcp.client.requests.get",
            return_value=_resp(404, text="not found"),
        ):
            with pytest.raises(KomootAPIError, match="not found"):
                await client.get_tour_full(999)


# ---------------------------------------------------------------- get_highlight

class TestGetHighlight:
    @pytest.mark.asyncio
    async def test_metadata_only_hits_one_url(self, client):
        urls = []

        def fake_get(url, **kwargs):
            urls.append(url)
            return _resp(200, {"id": 98160, "name": "Feldberg Summit"})

        with patch("komoot_mcp.client.requests.get", side_effect=fake_get):
            out = await client.get_highlight(98160)

        assert urls == ["https://api.komoot.de/v007/highlights/98160"]
        assert out["metadata"]["name"] == "Feldberg Summit"
        assert "tips" not in out
        assert "recommenders" not in out

    @pytest.mark.asyncio
    async def test_with_tips_and_recommenders_hits_three_urls(self, client):
        urls = []

        def fake_get(url, **kwargs):
            urls.append(url)
            if url.endswith("/tips/"):
                return _resp(200, {"items": [{"text": "Great view!"}]})
            if url.endswith("/recommenders/"):
                return _resp(200, {"items": [{"id": "u1"}, {"id": "u2"}]})
            return _resp(200, {"id": 98160, "name": "Feldberg Summit"})

        with patch("komoot_mcp.client.requests.get", side_effect=fake_get):
            out = await client.get_highlight(
                98160, with_tips=True, with_recommenders=True,
            )

        assert len(urls) == 3
        assert any(u.endswith("/tips/") for u in urls)
        assert any(u.endswith("/recommenders/") for u in urls)
        assert out["tips"]["items"][0]["text"] == "Great view!"
        assert len(out["recommenders"]["items"]) == 2


# ---------------------------------------------------------------- get_tour_weather

class TestGetTourWeather:
    @pytest.mark.asyncio
    async def test_hits_weather_service_host(self, client):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return _resp(200, {"forecast": [
                {"time": "2026-05-18T08:00", "temperature": 12, "condition": "clear"},
            ]})

        with patch("komoot_mcp.client.requests.get", side_effect=fake_get):
            out = await client.get_tour_weather(42)

        assert (
            captured["url"]
            == "https://weather-along-tour-api.komoot.de/v1/weather"
        )
        assert captured["params"] == {"tour_id": 42}
        assert out["forecast"][0]["temperature"] == 12

    @pytest.mark.asyncio
    async def test_tool_surfaces_error_on_bad_request(self, client):
        """Client errors propagate to the common MCP result boundary."""
        registered = {}

        class _Mcp:
            def tool(self):
                def decorator(fn):
                    registered[fn.__name__] = fn
                    return fn
                return decorator

        from komoot_mcp.context import (
            clear_request_state,
            reset_auth_manager,
            set_auth_manager,
        )
        from komoot_mcp.tools import browse_tools

        browse_tools.register(_Mcp())

        am = AuthManager(email="t@x.com", password="pw")
        # Pre-seed kompy auth so the tool's client doesn't try to log in.
        clear_request_state()
        token = set_auth_manager(am)
        try:
            from komoot_mcp.context import get_client
            c = get_client()
            api = MagicMock()
            auth = MagicMock()
            auth.get_username.return_value = "12345"
            auth.get_token.return_value = "tok"
            api.authentication = auth
            c._api = api

            with patch(
                "komoot_mcp.client.requests.get",
                return_value=_resp(400, text="bad request"),
            ):
                with pytest.raises(KomootAPIError, match="bad request"):
                    await registered["komoot_tour_weather"](42)
        finally:
            reset_auth_manager(token)
            clear_request_state()


# ---------------------------------------------------------------- discover_near

class TestDiscoverNear:
    @pytest.mark.asyncio
    async def test_builds_lat_lng_path_segment(self, client):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return _resp(200, {"_embedded": {"items": [
                {"type": "tour", "name": "Sample", "sport": "hike", "id": 1},
            ]}})

        with patch("komoot_mcp.client.requests.get", side_effect=fake_get):
            out = await client.discover_near(
                lat=47.87, lng=8.0, sport="hike", limit=5,
            )

        assert (
            captured["url"]
            == "https://api.komoot.de/v007/discover/47.87,8.0/elements/"
        )
        assert captured["params"]["_embedded"] == "main_tour,summary"
        assert captured["params"]["sport"] == "hike"
        assert captured["params"]["limit"] == 5
        assert out["_embedded"]["items"][0]["name"] == "Sample"

    @pytest.mark.asyncio
    async def test_omits_sport_when_none(self, client):
        captured = {}

        def fake_get(url, **kwargs):
            captured["params"] = kwargs.get("params")
            return _resp(200, {"_embedded": {"items": []}})

        with patch("komoot_mcp.client.requests.get", side_effect=fake_get):
            await client.discover_near(lat=50, lng=10)

        assert "sport" not in captured["params"]


# ---------------------------------------------------------------- tool renderers

@pytest.fixture(autouse=True)
def _reset_request_state():
    from komoot_mcp.context import clear_request_state
    clear_request_state()
    yield
    clear_request_state()


def _register_tools(modname):
    """Helper: register a tools module against a recorder ``_Mcp``."""
    registered = {}

    class _Mcp:
        def tool(self):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    mod = __import__(f"komoot_mcp.tools.{modname}", fromlist=[modname])
    mod.register(_Mcp())
    return registered


def _install_client_with_fake_kompy_auth():
    """Shared setup: pin a client whose ``_basic_auth`` works without
    talking to kompy. Returns the AuthManager token so the test can
    reset it."""
    from komoot_mcp.context import get_client, set_auth_manager

    am = AuthManager(email="t@x.com", password="pw")
    token = set_auth_manager(am)
    c = get_client()
    api = MagicMock()
    auth = MagicMock()
    auth.get_username.return_value = "12345"
    auth.get_token.return_value = "tok"
    api.authentication = auth
    c._api = api
    return token


class TestToolData:
    @pytest.mark.asyncio
    async def test_tour_full_returns_compact_data(self):
        from komoot_mcp.context import reset_auth_manager
        registered = _register_tools("browse_tools")
        token = _install_client_with_fake_kompy_auth()
        try:
            body = {
                "id": 42,
                "name": "Feldberg loop",
                "sport": "hike",
                "status": "private",
                "distance": 12345,
                "duration": 3600,
                "elevation_up": 800,
                "elevation_down": 800,
                "difficulty": {"grade": "moderate"},
                "start_point": {"lat": 47.87, "lng": 8.0, "alt": 1493},
                "_embedded": {
                    "coordinates": {"items": [{}] * 2081},
                    "way_types": {
                        "items": [
                            {"type": "trail", "amount": 0.6},
                            {"type": "road", "amount": 0.4},
                        ]
                    },
                    "surfaces": {
                        "items": [{"type": "gravel", "amount": 1.0}]
                    },
                    "directions": {"items": [{}] * 12},
                    "timeline": {"items": [
                        {"_embedded": {"reference": {"id": 98160, "name": "Feldberg Summit"}}}
                    ]},
                    "cover_images": {"items": [{}]},
                },
            }
            with patch(
                "komoot_mcp.client.requests.get", return_value=_resp(200, body),
            ):
                out = await registered["komoot_get_tour_full"](42)
            assert out["name"] == "Feldberg loop"
            assert out["coordinates_count"] == 2081
            assert out["way_types"] == body["_embedded"]["way_types"]["items"]
            assert out["surfaces"] == [{"type": "gravel", "amount": 1.0}]
            assert out["directions_count"] == 12
            assert out["highlights"] == [{"id": 98160, "name": "Feldberg Summit"}]
            assert "_embedded" not in out
        finally:
            reset_auth_manager(token)

    @pytest.mark.asyncio
    async def test_highlight_returns_metadata_only(self):
        from komoot_mcp.context import reset_auth_manager
        registered = _register_tools("browse_tools")
        token = _install_client_with_fake_kompy_auth()
        try:
            body = {
                "id": 98160,
                "name": "Feldberg Summit",
                "category": "summit",
                "sports": "hike",
                "score": 95,
                "location": {"lat": 47.87, "lng": 8.0},
            }
            with patch(
                "komoot_mcp.client.requests.get", return_value=_resp(200, body),
            ):
                out = await registered["komoot_get_highlight"](98160)
            assert out == {"metadata": body}
        finally:
            reset_auth_manager(token)

    @pytest.mark.asyncio
    async def test_discover_returns_items(self):
        from komoot_mcp.context import reset_auth_manager
        registered = _register_tools("discover_tools")
        token = _install_client_with_fake_kompy_auth()
        try:
            body = {"_embedded": {"items": [
                {
                    "type": "tour",
                    "name": "Black Forest loop",
                    "sport": "hike",
                    "distance": 12000,
                    "id": 777,
                },
                {
                    "type": "collection",
                    "name": "Best of Feldberg",
                    "_embedded": {
                        "main_tour": {
                            "id": 888,
                            "name": "Feldberg summit",
                            "sport": "hike",
                            "distance": 9000,
                        }
                    },
                },
            ]}}
            with patch(
                "komoot_mcp.client.requests.get", return_value=_resp(200, body),
            ):
                out = await registered["komoot_recommend_tours_near"](
                    lat=47.87, lng=8.0, sport="hike", limit=5,
                )
            assert out == body
        finally:
            reset_auth_manager(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["tips", "recommenders"])
async def test_highlight_optional_resource_errors_propagate_unchanged(monkeypatch, resource):
    from unittest.mock import AsyncMock
    from komoot_mcp.tools import browse_tools

    error = KomootAPIError("resource denied", status_code=403)
    client = KomootClient(AuthManager(email="t@x.com", password="pw"), _NoLimit())
    client._http_get_json = AsyncMock(side_effect=[{"id": 42}, error])
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    registered = _register_tools("browse_tools")
    with pytest.raises(KomootAPIError) as caught:
        await registered["komoot_get_highlight"](42, **{f"include_{resource}": True})
    assert caught.value is error
    assert client._http_get_json.await_count == 2


@pytest.mark.asyncio
async def test_full_and_compact_tour_modes_preserve_native_values(monkeypatch):
    from unittest.mock import AsyncMock
    from komoot_mcp.tools import browse_tools

    payload = {
        "id": 42, "name": "Zero", "distance": 0, "duration": 0,
        "description": "Full description", "path": [{"lat": 1, "lng": 2}],
        "_embedded": {"coordinates": {"items": [{"lat": 1, "lng": 2}]}},
    }
    client = SimpleNamespace(
        get_tour=AsyncMock(return_value=payload),
        get_tour_full=AsyncMock(return_value=payload),
        list_tours=AsyncMock(return_value={"tours": [payload], "page": {"number": 0}}),
    )
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    tools = _register_tools("browse_tools")
    from komoot_mcp.route_summary import route_summary
    explained = {**payload, "planning_summary": route_summary(payload)}
    assert await tools["komoot_get_tour"](42, compact=False) == explained
    client.get_tour.assert_awaited_once_with(42, share_token=None)
    compact = await tools["komoot_get_tour"](42)
    assert compact == {
        "id": 42, "name": "Zero", "distance": 0, "duration": 0,
        "planning_summary": route_summary(payload),
    }
    assert await tools["komoot_get_tour_full"](42, include_coordinates=False, compact=False) == explained
    client.get_tour_full.assert_awaited_once_with(42, include_coordinates=False)
    listed = await tools["komoot_list_tours"]()
    assert listed == {"tours": [{"id": 42, "name": "Zero", "distance": 0}], "page": {"number": 0}}
    assert await tools["komoot_list_tours"](compact=False) == {"tours": [payload], "page": {"number": 0}}
    client.list_tours.return_value = {"tours": [], "page": {"number": 0}}
    assert await tools["komoot_list_tours"]() == {"tours": [], "page": {"number": 0}}
