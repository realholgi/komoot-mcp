"""Issue #19 / native-planner — ``komoot_plan_and_upload``.

The user-facing workflow "plan a route and add it to my Komoot tours"
previously required two MCP tool calls with a ~100k-token GPX flowing
through the LLM in between. The current tool runs both server-side and
returns only the tour ID + URL.

The tool uses Komoot's native planner (``POST /api/routing/tour``) and
``save_planned_tour``, which honors ``type=tour_planned`` in the JSON
body so routes appear as planned tours rather than recorded activities.

Also covers the lower-level ``upload_gpx_capture_id`` client helper —
kept around because ``komoot_upload_tour`` still uses it for the
"upload a GPS recording" path where the user really did ride the
route.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootAPIError, KomootClient
from komoot_mcp.context import clear_request_state


GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>inline-track</name>
    <trkseg>
      <trkpt lat="49.0" lon="8.4"><ele>120</ele></trkpt>
      <trkpt lat="49.01" lon="8.41"><ele>125</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


class _NoLimit:
    async def acquire(self):
        return None


@pytest.fixture(autouse=True)
def _reset():
    clear_request_state()
    yield
    clear_request_state()


@pytest.fixture
def client():
    am = AuthManager(email="t@x.com", password="pw")
    return KomootClient(am, _NoLimit())


def _register_routing_tools():
    from komoot_mcp.tools import routing_tools

    registered: dict[str, callable] = {}

    class _Mcp:
        def tool(self):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    routing_tools.register(_Mcp())
    return registered, routing_tools


class _FakeGeocoder:
    def forward(self, q, limit=1):
        return [{
            "lat": 49.0094, "lon": 8.4001,
            "display_name": q, "city": "X",
            "country": "DE", "type": "place",
        }]

    def reverse(self, lat, lon):  # pragma: no cover - unused
        return {}


# ---------------------------------------------------------------------
# The new komoot_plan_and_upload tool
# ---------------------------------------------------------------------


@pytest.fixture
def planning(monkeypatch):
    from komoot_mcp import routing
    registered, tools = _register_routing_tools()
    route = {
        "distance": 12345.6, "duration": 5400, "elevation_up": 250,
        "_embedded": {"coordinates": {"items": [[49, 8]] * 5000}},
    }
    planner = Mock()
    planner.plan.return_value = route
    client = SimpleNamespace(
        _basic_auth=Mock(return_value=("uid", "tok")),
        rl=SimpleNamespace(acquire=AsyncMock()),
        save_planned_tour=AsyncMock(return_value={"id": 7654321, "status": "saved"}),
    )
    geocoder = Mock(wraps=_FakeGeocoder())
    monkeypatch.setattr(routing, "KomootNativePlanner", Mock(return_value=planner))
    monkeypatch.setattr(tools, "get_client", lambda: client)
    monkeypatch.setattr(tools, "get_geocoder", lambda: geocoder)
    return registered["komoot_plan_and_upload"], planner, client, geocoder, route


@pytest.mark.asyncio
async def test_saved_plan_reports_units_and_requested_not_verified_visibility(planning):
    tool, _, client, _, route = planning
    result = await tool("Freiburg", "Karlsruhe", tour_name="My route")
    assert result["tour_id"] == 7654321
    assert result["tour_url"] == "https://www.komoot.com/tour/7654321"
    assert result["distance_m"] == 12345.6
    assert result["duration_s"] == 5400
    assert result["elevation_up_m"] == 250
    assert result["requested_visibility"] == "private"
    assert result["verified_visibility"] is None
    assert result["visibility_verified"] is False
    assert "_embedded" not in result
    assert client.save_planned_tour.call_args.kwargs["route_response"] is route
    assert client.save_planned_tour.call_args.kwargs["name"] == "My route"
    client.rl.acquire.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("sport,profile", [
    ("hike", "hike"), ("trail_run", "jogging"), ("mountain_bike", "mtb"),
    ("road_cycle", "racebike"), ("gravel_ride", "touringbicycle"),
    ("mtb", "mtb"), ("mountainbike", "mountainbike"),
    ("touringbicycle", "touringbicycle"), ("racebike", "racebike"),
    ("e_racebike", "e_racebike"), ("e_mtb", "e_mtb"),
    ("jogging", "jogging"), ("nordicwalking", "nordicwalking"),
    ("e_touringbicycle", "e_touringbicycle"),
])
async def test_place_planning_resolves_sports(planning, sport, profile):
    tool, planner, *_ = planning
    await tool("49,8", "49.1,8.1", sport=sport)
    assert planner.plan.call_args.args[1] == profile


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"sport": "unknown"}, {"constitution": 0}, {"constitution": 2.5},
    {"start": "nan,8"}, {"end": "91,8"}, {"waypoints": "bad"},
    {"waypoints": "49,8|inf,8"}, {"tour_status": "secret"},
    {"end": None, "roundtrip": True},
])
async def test_bad_inputs_never_geocode_authenticate_or_plan(planning, options):
    tool, planner, client, geocoder, _ = planning
    with pytest.raises(ValueError):
        await tool(**{"start": "Freiburg", "end": "Karlsruhe", **options})
    geocoder.forward.assert_not_called()
    client._basic_auth.assert_not_called()
    client.rl.acquire.assert_not_awaited()
    planner.plan.assert_not_called()
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_roundtrip_preserves_order_and_closes_after_explicit_end(planning):
    tool, planner, *_ = planning
    await tool("49,8", "50,9", roundtrip=True, waypoints="49.2,8.2|49.3,8.3")
    assert planner.plan.call_args.args[0] == [
        [49, 8], [49.2, 8.2], [49.3, 8.3], [50, 9], [49, 8],
    ]


@pytest.mark.asyncio
async def test_roundtrip_without_end_uses_vias(planning):
    tool, planner, *_ = planning
    await tool("49,8", roundtrip=True, waypoints="49.2,8.2|49.3,8.3")
    assert planner.plan.call_args.args[0] == [[49, 8], [49.2, 8.2], [49.3, 8.3], [49, 8]]


@pytest.mark.asyncio
async def test_planner_failure_propagates_without_saving(planning):
    from komoot_mcp.routing import RoutingError
    tool, planner, client, *_ = planning
    planner.plan.side_effect = RoutingError("HTTP 500")
    with pytest.raises(RoutingError, match="HTTP 500"):
        await tool("49,8", "49.1,8.1")
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_failure_propagates(planning):
    tool, _, client, *_ = planning
    client.save_planned_tour.side_effect = KomootAPIError("HTTP 400")
    with pytest.raises(KomootAPIError, match="HTTP 400"):
        await tool("49,8", "49.1,8.1")


@pytest.mark.asyncio
async def test_missing_geocode_result_is_an_exception(planning):
    tool, _, client, geocoder, _ = planning
    geocoder.forward.return_value = []
    with pytest.raises(ValueError, match="Could not geocode"):
        await tool("Unknown place", "49,8")
    client._basic_auth.assert_not_called()



@pytest.mark.asyncio
async def test_place_names_and_explicit_via_names_reach_native_planner(planning):
    tool, planner, _, geocoder, _ = planning
    geocoder.forward.side_effect = [
        [{"lat": 49, "lon": 8, "display_name": "Resolved station"}],
        [{"lat": 50, "lon": 9, "display_name": "Resolved trailhead"}],
    ]
    await tool(
        "Station query", "Trailhead query", waypoints="49.2,8.2", roundtrip=True,
        waypoint_names=[None, "Picnic spot", "My destination"],
    )
    assert planner.plan.call_args.args[0] == [[49, 8], [49.2, 8.2], [50, 9], [49, 8]]
    assert planner.plan.call_args.kwargs["waypoint_names"] == [
        "Resolved station", "Picnic spot", "My destination", "Resolved station",
    ]
    geocoder.reverse.assert_not_called()


@pytest.mark.asyncio
async def test_coordinates_without_names_do_not_trigger_geocoding(planning):
    tool, planner, _, geocoder, _ = planning
    await tool("49,8", "50,9", waypoints="49.2,8.2")
    assert planner.plan.call_args.kwargs == {}
    geocoder.forward.assert_not_called()
    geocoder.reverse.assert_not_called()


@pytest.mark.asyncio
async def test_missing_resolved_name_does_not_invent_one(planning):
    tool, planner, _, geocoder, _ = planning
    geocoder.forward.return_value = [{"lat": 49, "lon": 8, "display_name": ""}]
    await tool("Unnamed place query", "50,9")
    assert planner.plan.call_args.kwargs == {}
    geocoder.reverse.assert_not_called()


@pytest.mark.asyncio
async def test_place_name_count_is_validated_before_geocoding(planning):
    tool, planner, client, geocoder, _ = planning
    with pytest.raises(ValueError, match="waypoint_names"):
        await tool("Station", "Trailhead", waypoint_names=["Station"])
    geocoder.forward.assert_not_called()
    planner.plan.assert_not_called()
    client.save_planned_tour.assert_not_awaited()

# ---------------------------------------------------------------------
# upload_gpx_capture_id — the client-side helper used by #19
# ---------------------------------------------------------------------


class _FakeKomootResponse:
    def __init__(self, status_code=201, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class TestUploadGpxCaptureId:
    """Direct test of the client-side upload helper that pulls the ID
    out of the Komoot response (kompy throws it away)."""

    @pytest.mark.asyncio
    async def test_201_returns_id_and_uploaded_status(self, client, monkeypatch):
        # Stub the kompy connector so _get_api() works.
        api = MagicMock()
        api.authentication = MagicMock()
        api.authentication.get_email_address = MagicMock(return_value="t@x.com")
        api.authentication.get_password = MagicMock(return_value="pw")
        client._api = api

        from komoot_mcp import client as client_mod

        def fake_post(url, auth=None, headers=None, params=None, data=None, **kwargs):
            assert "tours" in url
            assert params["data_type"] == "gpx"
            # Default for this helper is now tour_planned — uploading
            # a planned route should NOT show up as a completed
            # activity in the user's Komoot tour list.
            assert params["type"] == "tour_planned"
            return _FakeKomootResponse(status_code=201, body={"id": 555})

        monkeypatch.setattr(client_mod.requests, "post", fake_post)

        out = await client.upload_gpx_capture_id(
            gpx_content=GPX_SAMPLE, sport="hike", tour_name="x",
        )
        assert out == {"id": 555, "status": "uploaded"}

    @pytest.mark.asyncio
    async def test_tour_type_override_passes_through(self, client, monkeypatch):
        """A caller can still request ``tour_recorded`` (e.g. for a
        future "upload my real GPS recording" path) — the helper must
        forward whatever ``tour_type`` it's given, not hard-code one."""
        api = MagicMock()
        api.authentication = MagicMock()
        api.authentication.get_email_address = MagicMock(return_value="t@x.com")
        api.authentication.get_password = MagicMock(return_value="pw")
        client._api = api

        from komoot_mcp import client as client_mod

        seen = {}

        def fake_post(url, auth=None, headers=None, params=None, data=None, **kwargs):
            seen.update(params)
            return _FakeKomootResponse(status_code=201, body={"id": 1})

        monkeypatch.setattr(client_mod.requests, "post", fake_post)

        await client.upload_gpx_capture_id(
            gpx_content=GPX_SAMPLE, sport="hike", tour_type="tour_recorded",
        )
        assert seen["type"] == "tour_recorded"

    @pytest.mark.asyncio
    async def test_202_marks_duplicate(self, client, monkeypatch):
        api = MagicMock()
        api.authentication = MagicMock()
        api.authentication.get_email_address = MagicMock(return_value="t@x.com")
        api.authentication.get_password = MagicMock(return_value="pw")
        client._api = api

        from komoot_mcp import client as client_mod

        def fake_post(*a, **kw):
            return _FakeKomootResponse(status_code=202, body={"id": 999})

        monkeypatch.setattr(client_mod.requests, "post", fake_post)

        out = await client.upload_gpx_capture_id(
            gpx_content=GPX_SAMPLE, sport="hike",
        )
        assert out == {"id": 999, "status": "duplicate"}

    @pytest.mark.asyncio
    async def test_400_raises_with_status_in_message(self, client, monkeypatch):
        api = MagicMock()
        api.authentication = MagicMock()
        api.authentication.get_email_address = MagicMock(return_value="t@x.com")
        api.authentication.get_password = MagicMock(return_value="pw")
        client._api = api

        from komoot_mcp import client as client_mod

        def fake_post(*a, **kw):
            return _FakeKomootResponse(
                status_code=400, body={}, text="bad gpx",
            )

        monkeypatch.setattr(client_mod.requests, "post", fake_post)

        with pytest.raises(KomootAPIError) as ei:
            await client.upload_gpx_capture_id(
                gpx_content=GPX_SAMPLE, sport="hike",
            )
        msg = str(ei.value)
        assert "400" in msg
        # Helpful diagnosis hint for the most common 400 cause.
        assert "track format" in msg or "trk" in msg
