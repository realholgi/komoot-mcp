"""Tests for Geocoder (Photon API wrapper)."""
import json
import pytest
from unittest.mock import patch, MagicMock
from komoot_mcp.geocoder import Geocoder

class TestGeocoder:
    def test_forward_parses_response(self):
        geo = Geocoder()
        mock_response = {
            "features": [{
                "geometry": {"coordinates": [13.404954, 52.520008]},
                "properties": {"name": "Berlin", "city": "Berlin", "country": "Germany", "type": "city", "osm_id": 12345}
            }]
        }
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            results = geo.forward("Berlin")
            assert len(results) == 1
            assert results[0]["display_name"] == "Berlin"
            assert results[0]["lat"] == 52.520008
            assert results[0]["lon"] == 13.404954

    def test_forward_empty_results(self):
        geo = Geocoder()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"features":[]}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            assert geo.forward("xyznonexistent") == []

    def test_reverse_returns_fallback_on_empty(self):
        geo = Geocoder()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"features":[]}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            result = geo.reverse(0.0, 0.0)
            assert result["display_name"] == "Unknown location"


@pytest.mark.parametrize("reverse", [False, True])
def test_geocoding_preserves_poi_category_and_osm_identity(reverse):
    response = {"features": [{
        "geometry": {"coordinates": [8.1960117, 48.6739408]},
        "properties": {
            "name": "Bühlertal Schindelpeter", "osm_id": 369831410,
            "osm_type": "N", "osm_key": "highway", "osm_value": "bus_stop",
        },
    }]}
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
        geo = Geocoder()
        result = geo.reverse(48.673941, 8.196012) if reverse else geo.forward(
            "Schindelpeter", osm_tag="highway:bus_stop", near_lat=48.673941, near_lng=8.196012,
        )[0]
        if not reverse:
            from urllib.parse import parse_qs, urlsplit
            query = parse_qs(urlsplit(urlopen.call_args.args[0]).query)
            assert query["osm_tag"] == ["highway:bus_stop"]
            assert query["lat"] == ["48.673941"]
            assert query["lon"] == ["8.196012"]
    assert result["display_name"] == "Bühlertal Schindelpeter"
    assert (result["osm_type"], result["osm_id"]) == ("N", 369831410)
    assert (result["osm_key"], result["osm_value"]) == ("highway", "bus_stop")

def _routing_handlers():
    from komoot_mcp.tools import routing_tools
    functions = {}
    class Recorder:
        def tool(self):
            def register(fn):
                functions[fn.__name__] = fn
                return fn
            return register
    routing_tools.register(Recorder())
    return functions, routing_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_async_geocoding_runs_off_event_loop_and_returns_records(monkeypatch, reverse):
    import asyncio
    import threading
    functions, tools = _routing_handlers()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    record = {"lat": 49, "lon": 8, "display_name": "Place"}
    release = threading.Event()

    def geocode(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(release.set)
        assert release.wait(2), "event loop blocked by synchronous geocoder"
        return record if reverse else [record]

    geo = MagicMock()
    geo.reverse.side_effect = geocode
    geo.forward.side_effect = geocode
    monkeypatch.setattr(tools, "get_geocoder", lambda: geo)
    query = "49,8" if reverse else "Place"
    assert await functions["komoot_geocode"](query) == {"query": query, "results": [record]}


@pytest.mark.asyncio
async def test_geocoding_failures_raise_and_empty_results_stay_data(monkeypatch):
    functions, tools = _routing_handlers()
    geo = MagicMock()
    monkeypatch.setattr(tools, "get_geocoder", lambda: geo)
    geo.forward.return_value = []
    assert await functions["komoot_geocode"]("Missing") == {"query": "Missing", "results": []}
    geo.forward.side_effect = OSError("offline")
    with pytest.raises(OSError, match="offline"):
        await functions["komoot_geocode"]("Place")


@pytest.mark.asyncio
async def test_place_planning_geocodes_off_event_loop(monkeypatch):
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from komoot_mcp import routing
    functions, tools = _routing_handlers()
    loop_thread = threading.get_ident()
    def forward(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        return [{"lat": 49, "lon": 8}]
    geo = MagicMock()
    geo.forward.side_effect = forward
    client = SimpleNamespace(
        _basic_auth=Mock(return_value=("user", "token")),
        rl=SimpleNamespace(acquire=AsyncMock()),
        save_planned_tour=AsyncMock(return_value={"id": 42}),
    )
    planner = Mock()
    planner.plan.return_value = {}
    monkeypatch.setattr(tools, "get_geocoder", lambda: geo)
    monkeypatch.setattr(tools, "get_client", lambda: client)
    monkeypatch.setattr(routing, "KomootNativePlanner", Mock(return_value=planner))
    await functions["komoot_plan_and_upload"]("Start place", "End place")
    assert geo.forward.call_count == 2


@pytest.mark.asyncio
async def test_resolve_place_runs_both_searches_off_event_loop(monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    functions, tools = _routing_handlers()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    record = {
        "display_name": "Stop", "lat": 48.6739408, "lon": 8.1960117,
        "osm_type": "N", "osm_id": 369831410, "osm_value": "bus_stop",
    }

    def allow_loop_progress():
        assert threading.get_ident() != loop_thread
        release = threading.Event()
        loop.call_soon_threadsafe(release.set)
        assert release.wait(2), "event loop blocked by synchronous place search"

    def forward(*args, **kwargs):
        allow_loop_progress()
        return [record]

    def nearby(*args, **kwargs):
        allow_loop_progress()
        return {"results": [{"osm_id": "n369831410", "poi_id": "7mn4K"}]}

    geo = MagicMock()
    geo.forward.side_effect = forward
    limiter = SimpleNamespace(acquire=AsyncMock())
    monkeypatch.setattr(tools, "get_geocoder", lambda: geo)
    monkeypatch.setattr(tools, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(tools, "find_pois_nearby", nearby)
    result = await functions["komoot_resolve_place"](
        "Stop", limit=3, lang="en", near_lat=48, near_lng=8, osm_tag="highway:bus_stop",
    )
    assert result["results"][0]["poi_id"] == "7mn4K"
    assert limiter.acquire.await_count == 2
    geo.forward.assert_called_once_with(
        "Stop", limit=3, lang="en", near_lat=48, near_lng=8, osm_tag="highway:bus_stop",
    )


@pytest.mark.asyncio
async def test_resolve_place_preserves_all_candidates_and_bounds_nearby_calls(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    functions, tools = _routing_handlers()
    records = [
        {"display_name": "Missing identity", "lat": 48, "lon": 8},
        {"display_name": "Bad location", "lat": None, "lon": 8, "osm_type": "N", "osm_id": 1},
        {"display_name": "Not linked", "lat": 48, "lon": 8, "osm_type": "W", "osm_id": 2},
        {"display_name": "Over limit", "lat": 48, "lon": 8, "osm_type": "N", "osm_id": 3},
    ]
    geo = MagicMock()
    geo.forward.return_value = records
    nearby = MagicMock(return_value={"results": []})
    monkeypatch.setattr(tools, "get_geocoder", lambda: geo)
    monkeypatch.setattr(tools, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))
    monkeypatch.setattr(tools, "find_pois_nearby", nearby)
    result = await functions["komoot_resolve_place"]("Place", limit=3)
    assert [item["display_name"] for item in result["results"]] == [
        "Missing identity", "Bad location", "Not linked",
    ]
    assert [item["resolution_status"] for item in result["results"]] == [
        "missing_osm_identity", "invalid_location", "not_found",
    ]
    assert all(item["waypoint_details"] is None for item in result["results"])
    nearby.assert_called_once_with(48, 8, 100, 100, "de")


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 11}, {"limit": True}, {"near_lat": 48},
    {"near_lng": 8}, {"near_lat": 91, "near_lng": 8}, {"query": "  "},
])
async def test_resolve_place_rejects_invalid_options_before_network(monkeypatch, options):
    functions, tools = _routing_handlers()
    geo = MagicMock()
    monkeypatch.setattr(tools, "get_geocoder", lambda: geo)
    with pytest.raises(ValueError):
        await functions["komoot_resolve_place"](**({"query": "Place"} | options))
    geo.forward.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_place_does_not_turn_network_failure_into_no_match(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from komoot_mcp.geocoder import GeocoderError

    functions, tools = _routing_handlers()
    geo = MagicMock()
    geo.forward.return_value = []
    nearby = MagicMock(side_effect=GeocoderError("offline"))
    monkeypatch.setattr(tools, "get_geocoder", lambda: geo)
    monkeypatch.setattr(tools, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))
    monkeypatch.setattr(tools, "find_pois_nearby", nearby)
    assert await functions["komoot_resolve_place"]("Empty") == {"query": "Empty", "results": []}
    nearby.assert_not_called()
    geo.forward.return_value = [{"osm_type": "N", "osm_id": 1, "lat": 48, "lon": 8}]
    with pytest.raises(GeocoderError, match="offline"):
        await functions["komoot_resolve_place"]("Place")
    geo.forward.side_effect = GeocoderError("Photon offline")
    with pytest.raises(GeocoderError, match="Photon offline"):
        await functions["komoot_resolve_place"]("Place")
