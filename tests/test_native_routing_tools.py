"""Native import and planned-tour contracts, without any live account access."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import gpxpy

from komoot_mcp.client import KomootAPIError
from komoot_mcp import routing
from komoot_mcp.tools import native_routing_tools as tools


@pytest.fixture
def handlers(monkeypatch):
    functions = {}

    class Recorder:
        def tool(self):
            def register(fn):
                functions[fn.__name__] = fn
                return fn
            return register

    tools.register(Recorder())
    client = SimpleNamespace(
        _http_request=AsyncMock(return_value={"_embedded": {"items": [
            {"name": "track", "_embedded": {"coordinates": {"items": [[49, 8], [49.1, 8.1]]}}},
        ]}}),
        _komoot_post=AsyncMock(return_value={"_embedded": {"matched": {
            "sport": "hike", "path": [{"name": "Fontaine"}],
        }}}),
        save_planned_tour=AsyncMock(return_value={"id": 42, "status": "saved"}),
        update_planned_tour=AsyncMock(return_value={
            "id": 42, "status": "updated", "verified_visibility": "friends",
            "visibility_verified": True, "verification": {
                "verified": True, "requested": {"status": "friends"},
                "observed": {"status": "friends"}, "mismatches": [],
            },
            "metadata_changes": {"date": {"before": "2026-01-01", "after": "2026-11-18"}},
        }),
        _basic_auth=Mock(return_value=("user", "token")),
        rl=SimpleNamespace(acquire=AsyncMock()),
    )
    monkeypatch.setattr(tools, "get_client", lambda: client)
    return functions, client


@pytest.mark.asyncio
async def test_import_transmits_names_comments_and_matches_without_saving(handlers):
    functions, client = handlers
    gpx = '<gpx version="1.1"><wpt lat="49" lon="8"><name>Fontaine</name><desc>Still working</desc></wpt></gpx>'
    result = await functions["komoot_import_gpx_route"](gpx)
    sent = client._http_request.call_args
    assert sent.args == ("POST", "https://www.komoot.com/api/routing/import/files/")
    assert sent.kwargs["data"] == gpx.encode("utf-8")
    match = client._komoot_post.call_args
    assert match.args == ("https://www.komoot.com/api/routing/import/tour",)
    assert match.kwargs["json_body"] == {
        "name": "track", "_embedded": {"coordinates": {"items": [[49, 8], [49.1, 8.1]]}},
    }
    assert result["path"][0]["name"] == "Fontaine"
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_import_response_does_not_save_or_match(handlers):
    functions, client = handlers
    client._http_request.return_value = "not a route import"
    with pytest.raises(KomootAPIError):
        await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>')
    client._komoot_post.assert_not_awaited()
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_keeps_route_metadata_and_selects_private_without_mutating_input(handlers):
    functions, client = handlers
    route = {"sport": "touringbicycle", "path": [{"name": "Fontaine", "description": "Still working"}]}
    await functions["komoot_create_planned_tour"](route, name="Example route", sport="hike")
    sent = client.save_planned_tour.call_args
    assert sent.args == ({**route, "sport": "hike"}, "Example route")
    assert sent.kwargs["status"] == "private"
    assert route["sport"] == "touringbicycle"


@pytest.mark.asyncio
async def test_roundtrip_uses_native_planner_and_then_saves_same_geometry(handlers, monkeypatch):
    functions, client = handlers
    route = {"distance": 1234, "path": [{"location": {"lat": 49, "lng": 8}}]}
    planner = Mock()
    planner.plan.return_value = route
    monkeypatch.setattr(routing, "KomootNativePlanner", Mock(return_value=planner))
    coordinates = [[49.0, 8.0], [49.1, 8.1]]
    result = await functions["komoot_plan_and_save_tour"](coordinates, name="Loop", roundtrip=True)
    assert result["tour_id"] == 42
    assert result["distance_m"] == 1234
    assert result["requested_visibility"] == "private"
    assert result["verified_visibility"] is None
    assert result["visibility_verified"] is False
    assert planner.plan.call_args.args == ([*coordinates, coordinates[0]], "hike", 3)
    assert coordinates == [[49.0, 8.0], [49.1, 8.1]]
    client.save_planned_tour.assert_awaited_once_with(route, "Loop", status="private")


@pytest.mark.asyncio
async def test_update_uses_existing_id_and_preview_without_planning(handlers, monkeypatch):
    functions, client = handlers
    planner = Mock()
    monkeypatch.setattr(routing, "KomootNativePlanner", planner)
    route = {"sport": "hike", "constitution": 3, "distance": 1200,
             "path": [{"reference": "poi:Ab12"}, {"reference": "hlp:25"}]}
    result = await functions["komoot_update_planned_tour"](42, route)
    client.update_planned_tour.assert_awaited_once_with(42, route)
    client.save_planned_tour.assert_not_awaited()
    planner.assert_not_called()
    assert result["tour_id"] == 42
    assert result["visibility_verified"] is True
    assert result["verified_visibility"] == "friends"
    assert result["verification"]["verified"] is True
    assert result["metadata_changes"]["date"] == {"before": "2026-01-01", "after": "2026-11-18"}


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinates,fitness", [
    ([[49, 8]], 3), ([[49, 8], [49.1, 8.1]], 0),
    ([[100, 8], [49, 8]], 3), ([[49, 8], [49, float("nan")]], 3),
    ([[49, float("inf")], [49, 8]], 3), ([[49, 8, 3], [49, 8]], 3),
    ([None, [49, 8]], 3), ([["49", 8], [49, 8]], 3),
    ([[True, 8], [49, 8]], 3), (None, 3),
    ([[49, 8], [49.1, 8.1]], 2.5), ([[49, 8], [49.1, 8.1]], True),
])
async def test_invalid_native_plan_never_authenticates_or_saves(handlers, coordinates, fitness):
    functions, client = handlers
    with pytest.raises(ValueError):
        await functions["komoot_plan_native_route"](coordinates, constitution=fitness)
    client._basic_auth.assert_not_called()
    client.save_planned_tour.assert_not_awaited()
    client.rl.acquire.assert_not_awaited()


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
async def test_coordinate_planning_resolves_sports_and_preserves_full_route(handlers, monkeypatch, sport, profile):
    functions, client = handlers
    route = {"path": [{"reference": "cnw:1", "name": "POI"}],
             "_embedded": {"coordinates": {"items": [[49, 8], [49.1, 8.1]]}}}
    planner = Mock()
    planner.plan.return_value = route
    monkeypatch.setattr(routing, "KomootNativePlanner", Mock(return_value=planner))
    result = await functions["komoot_plan_native_route"]([[49, 8], [49.1, 8.1]], sport=sport)
    assert result is route
    assert planner.plan.call_args.args[1] == profile
    client.rl.acquire.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_native_sport_fails_before_auth_or_rate_limit(handlers):
    functions, client = handlers
    with pytest.raises(ValueError, match="Unsupported sport"):
        await functions["komoot_plan_native_route"]([[49, 8], [49.1, 8.1]], sport="bogus")
    client._basic_auth.assert_not_called()
    client.rl.acquire.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["komoot_plan_native_route", "komoot_plan_and_save_tour", "komoot_plan_and_upload"])
async def test_planning_consumers_share_limiter_and_do_not_block_event_loop(handlers, monkeypatch, tool_name):
    functions, client = handlers
    from komoot_mcp.tools import routing_tools
    class Recorder:
        def tool(self):
            def register(fn):
                functions[fn.__name__] = fn
                return fn
            return register
    routing_tools.register(Recorder())
    monkeypatch.setattr(routing_tools, "get_client", lambda: client)
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    stages = []
    release = threading.Event()

    def authenticate():
        assert threading.get_ident() != loop_thread
        stages.append("auth")
        return ("user", "token")

    async def acquire():
        stages.append("limit")

    def plan(*args):
        assert threading.get_ident() != loop_thread
        stages.append("plan")
        loop.call_soon_threadsafe(release.set)
        assert release.wait(2), "event loop blocked by synchronous planner"
        return {"distance": 100}

    client._basic_auth.side_effect = authenticate
    client.rl.acquire.side_effect = acquire
    planner = Mock()
    planner.plan.side_effect = plan
    monkeypatch.setattr(routing, "KomootNativePlanner", Mock(return_value=planner))
    if tool_name == "komoot_plan_and_upload":
        await functions[tool_name]("49,8", "49.1,8.1")
    elif tool_name == "komoot_plan_and_save_tour":
        await functions[tool_name]([[49, 8], [49.1, 8.1]], name="Example")
    else:
        await functions[tool_name]([[49, 8], [49.1, 8.1]])
    assert stages == ["auth", "limit", "plan"]


@pytest.mark.asyncio
async def test_reverse_geocoding_never_sends_account_credentials(handlers):
    functions, client = handlers
    await functions["komoot_reverse_geocode"](49, 8, lang="de")
    sent = client._http_request.call_args
    assert sent.args == ("GET", "https://photon.komoot.io/reverse")
    assert sent.kwargs["no_auth"] is True
    assert sent.kwargs["params"] == {"lat": 49, "lon": 8, "lang": "de"}


@pytest.mark.asyncio
async def test_import_selects_the_requested_track_not_the_file_envelope(handlers):
    functions, client = handlers
    tracks = [{"name": "first"}, {"name": "second", "_embedded": {"coordinates": {"items": [[49, 8]]}}}]
    client._http_request.return_value = {"_embedded": {"items": tracks}}
    await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>', track_index=1)
    assert client._komoot_post.call_args.kwargs["json_body"] == tracks[1]


@pytest.mark.asyncio
async def test_missing_track_does_not_match_an_empty_file_envelope(handlers):
    functions, client = handlers
    client._http_request.return_value = {"_embedded": {"items": []}}
    with pytest.raises(KomootAPIError, match="selected track"):
        await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>')
    client._komoot_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_poi_description_requires_model_preparation(handlers):
    functions, client = handlers
    client._http_request.return_value = {"_embedded": {"items": [{
        "_embedded": {"custom_named_waypoints": [{"name": "Fontaine", "description": "x" * 251}]},
    }]}}
    with pytest.raises(KomootAPIError, match="prepare_descriptions=True"):
        await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>')
    client._komoot_post.assert_not_awaited()




@pytest.mark.asyncio
async def test_matching_requires_a_real_matched_route_not_an_envelope(handlers):
    functions, client = handlers
    client._komoot_post.return_value = {"_embedded": {"original": {"path": [{"name": "not matched"}]}}}
    with pytest.raises(KomootAPIError, match="no matched route"):
        await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>')
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_hydrates_custom_references_for_persistence_without_renaming_shaping_points(handlers):
    functions, client = handlers
    start_location = {"lat": 49.014062, "lng": 8.404368}
    end_location = {"lat": 49.014819, "lng": 8.405404}
    client._komoot_post.return_value = {"_embedded": {"matched": {
        "path": [
            {"location": start_location, "index": 0},
            {"location": start_location, "index": 0, "reference": "cnw:9"},
            {"location": end_location, "index": 11, "reference": "cnw:3"},
        ],
        "segments": [{"type": "Routed"}, {"type": "Routed"}],
        "_embedded": {"custom_named_waypoints": [
            {"lat": 49.0148186, "lng": 8.4054039, "index": 11,
             "name": "GPX-Test B", "description": "Kommentar-Test B 2026-11-15", "category": 0},
            {"lat": 49.0140616, "lng": 8.4043681, "index": 0,
             "name": "Test-Start", "description": "Test-Start kommentar", "category": 0},
        ]},
    }}}
    route = await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>')
    assert route["path"] == [
        {"location": start_location, "index": 0},
        {"location": start_location, "index": 0, "reference": "cnw:9",
         "name": "Test-Start", "description": "Test-Start kommentar", "category": 0},
        {"location": end_location, "index": 11, "reference": "cnw:3",
         "name": "GPX-Test B", "description": "Kommentar-Test B 2026-11-15", "category": 0},
    ]
    assert route["segments"] == [{"type": "Routed"}, {"type": "Routed"}]


@pytest.mark.asyncio
async def test_unresolved_custom_reference_is_rejected_instead_of_saving_generic_waypoint(handlers):
    functions, client = handlers
    client._komoot_post.return_value = {"_embedded": {"matched": {
        "path": [{"location": {"lat": 49, "lng": 8}, "index": 0, "reference": "cnw:1"}],
    }}}
    with pytest.raises(KomootAPIError, match="no matching name"):
        await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>')




@pytest.mark.asyncio
async def test_import_can_keep_original_track_instead_of_network_matching(handlers):
    functions, client = handlers
    client._komoot_post.return_value = {"_embedded": {
        "matched": {"distance": 300, "path": [{"index": 0, "location": {"lat": 49, "lng": 8}}]},
        "original": {"distance": 200, "path": [{"index": 0, "location": {"lat": 49.1, "lng": 8.1}}]},
    }}
    route = await functions["komoot_import_gpx_route"]('<gpx version="1.1"/>', match_route=False)
    assert route["distance"] == 200
    assert route["path"] == [{"index": 0, "location": {"lat": 49.1, "lng": 8.1}}]


@pytest.mark.asyncio
async def test_import_preserves_poi_types_existing_symbols_and_full_track(handlers):
    functions, client = handlers
    gpx = """<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
      <wpt lat="49" lon="8"><name>Fontaine</name><desc>Not drinking water</desc>
        <sym>Flag, Blue</sym><type>Fontaine</type></wpt>
      <wpt lat="49.001" lon="8.001"><name>Auberge</name><type>Restaurant</type></wpt>
      <wpt lat="49.002" lon="8.002"><name>Local shelter</name>
        <sym>Shelter</sym><type>Unrecognized local type</type></wpt>
      <wpt lat="49.003" lon="8.003"><name>Abri</name>
        <sym>Shelter</sym><type>Refuge non Gardé</type></wpt>
      <trk><trkseg>
        <trkpt lat="49" lon="8"><ele>123</ele></trkpt>
        <trkpt lat="49.001" lon="8.001"><ele>150</ele></trkpt>
        <trkpt lat="49.002" lon="8.002"><ele>234.5</ele></trkpt>
        <trkpt lat="49.003" lon="8.003"><ele>300</ele></trkpt>
      </trkseg></trk>
    </gpx>"""

    async def import_file(*args, **kwargs):
        parsed = gpxpy.parse(kwargs["data"].decode("utf-8"))
        # Category IDs observed from Komoot's real GPX symbol importer.
        categories = {"Drinking Water": 3, "Restaurant": 4, "Shelter": 36}
        points = [
            {"lat": point.latitude + 0.0002, "lng": point.longitude + 0.0002, "index": index,
             "name": point.name, "description": point.description or "",
             "category": categories.get(point.symbol, 0)}
            for index, point in enumerate(parsed.waypoints)
        ]
        coordinates = [
            [point.latitude, point.longitude, point.elevation]
            for point in parsed.tracks[0].segments[0].points
        ]
        return {"_embedded": {"items": [{"_embedded": {
            "custom_named_waypoints": points, "coordinates": {"items": coordinates},
        }}]}}

    async def match_file(*args, json_body, **kwargs):
        points = json_body["_embedded"]["custom_named_waypoints"]
        return {"_embedded": {"original": {
            "_embedded": json_body["_embedded"],
            "path": [
                {"location": {"lat": point["lat"], "lng": point["lng"]},
                 "index": point["index"], "reference": f"cnw:{index + 1}"}
                for index, point in enumerate(points)
            ],
        }}}

    client._http_request.side_effect = import_file
    client._komoot_post.side_effect = match_file
    route = await functions["komoot_import_gpx_route"](gpx, match_route=False)
    assert [(p["name"], p["category"]) for p in route["path"]] == [
        ("Fontaine", 3), ("Auberge", 1001), ("Local shelter", 36), ("Abri", 1010),
    ]
    assert [p["category"] for p in route["_embedded"]["custom_named_waypoints"]] == [
        3, 1001, 36, 1010,
    ]
    assert route["path"][0]["description"] == "Not drinking water"
    assert route["_embedded"]["coordinates"]["items"] == [
        [49, 8, 123], [49.001, 8.001, 150], [49.002, 8.002, 234.5],
        [49.003, 8.003, 300],
    ]


@pytest.mark.asyncio
async def test_conflicting_same_name_poi_types_are_rejected_before_import(handlers):
    functions, client = handlers
    gpx = """<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
      <wpt lat="49" lon="8"><name>Shared POI</name><type>Fontaine</type></wpt>
      <wpt lat="49.001" lon="8.001"><name>Shared POI</name><type>Restaurant</type></wpt>
    </gpx>"""
    with pytest.raises(ValueError, match="conflicting categories"):
        await functions["komoot_import_gpx_route"](gpx)
    client._http_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["komoot_plan_native_route", "komoot_plan_and_save_tour"])
async def test_named_coordinate_tools_keep_names_after_snapping_and_save(handlers, monkeypatch, tool_name):
    functions, client = handlers
    response_route = {
        "path": [
            {"location": {"lat": 49.0001, "lng": 8.0001}, "index": 0},
            {"location": {"lat": 49.1001, "lng": 8.1001}, "index": 10, "description": "Keep this"},
            {"location": {"lat": 49.0001, "lng": 8.0001}, "index": 20},
        ],
        "segments": [{"type": "Routed", "from": 0, "to": 10}, {"type": "Routed", "from": 10, "to": 20}],
        "_embedded": {"coordinates": {"items": [[49, 8, 100], [49.1, 8.1, 200], [49, 8, 100]]}},
        "query": "routing-token",
    }
    response = Mock(status_code=200)
    response.json.return_value = response_route
    post = Mock(return_value=response)
    monkeypatch.setattr(routing.secure_http, "post", post)
    options = {"name": "Named loop"} if tool_name == "komoot_plan_and_save_tour" else {}
    result = await functions[tool_name](
        [[49, 8], [49.1, 8.1]], roundtrip=True,
        waypoint_names=["Station", "Lookout"], **options,
    )
    sent_path = post.call_args.kwargs["json"]["path"]
    assert [point["name"] for point in sent_path] == ["Station", "Lookout", "Station"]
    assert [point["reference"] for point in sent_path] == ["cnw:1", "cnw:2", "cnw:3"]
    assert [point["name"] for point in response_route["path"]] == ["Station", "Lookout", "Station"]
    assert [point["reference"] for point in response_route["path"]] == ["cnw:1", "cnw:2", "cnw:3"]
    assert response_route["path"][1]["description"] == "Keep this"
    assert response_route["query"] == "routing-token"
    assert len(response_route["_embedded"]["coordinates"]["items"]) == 3
    if tool_name == "komoot_plan_native_route":
        assert result is response_route
        client.save_planned_tour.assert_not_awaited()
    else:
        client.save_planned_tour.assert_awaited_once_with(response_route, "Named loop", status="private")


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [[], ["Only start"], ["Start", ""], ["Start", 4], "Start|End"])
async def test_invalid_waypoint_names_fail_before_authentication(handlers, names):
    functions, client = handlers
    with pytest.raises(ValueError, match="waypoint_names"):
        await functions["komoot_plan_native_route"]([[49, 8], [49.1, 8.1]], waypoint_names=names)
    client._basic_auth.assert_not_called()
    client.rl.acquire.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_native_path_count_rejects_ambiguous_names(handlers, monkeypatch):
    functions, client = handlers
    response = Mock(status_code=200)
    response.json.return_value = {"path": [{"location": {"lat": 49, "lng": 8}}]}
    monkeypatch.setattr(routing.secure_http, "post", Mock(return_value=response))
    with pytest.raises(routing.RoutingError, match="waypoint count"):
        await functions["komoot_plan_and_save_tour"](
            [[49, 8], [49.1, 8.1]], name="Must not save", waypoint_names=["Start", "End"],
        )
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("match_route", [True, False])
async def test_gpx_track_endpoint_names_survive_import_and_save(handlers, match_route):
    functions, client = handlers
    route = {
        "path": [
            {"location": {"lat": 49.0002, "lng": 8.0002}, "index": 0},
            {"location": {"lat": 49.1, "lng": 8.1}, "index": 12},
        ],
        "segments": [{"type": "Routed", "from": 0, "to": 12}],
        "_embedded": {"coordinates": {"items": [[49.0002, 8.0002, 100], [49.1, 8.1, 200]]}},
    }
    client._komoot_post.return_value = {"_embedded": {
        "matched" if match_route else "original": route,
    }}
    gpx = """<gpx version="1.1"><trk><trkseg>
      <trkpt lat="49" lon="8"><name>Railway station</name><desc>North entrance</desc></trkpt>
      <trkpt lat="49.1" lon="8.1"><name>Trailhead</name></trkpt>
    </trkseg></trk></gpx>"""
    imported = await functions["komoot_import_gpx_route"](gpx, match_route=match_route)
    await functions["komoot_create_planned_tour"](imported, name="Endpoint names")
    saved = client.save_planned_tour.call_args.args[0]
    assert [point["name"] for point in saved["path"]] == ["Railway station", "Trailhead"]
    assert [point["reference"] for point in saved["path"]] == ["cnw:1", "cnw:2"]
    assert saved["path"][0]["description"] == "North entrance"
    assert saved["_embedded"] is route["_embedded"]
    assert saved["segments"] == route["segments"]


@pytest.mark.asyncio
async def test_geocoded_names_survive_http_response_to_save(handlers, monkeypatch):
    from komoot_mcp.tools import routing_tools

    functions, client = handlers

    class Recorder:
        def tool(self):
            def register(fn):
                functions[fn.__name__] = fn
                return fn
            return register

    routing_tools.register(Recorder())
    monkeypatch.setattr(routing_tools, "get_client", lambda: client)
    geocoder = Mock()
    geocoder.forward.return_value = [{"lat": 49, "lon": 8, "display_name": "Central station"}]
    monkeypatch.setattr(routing_tools, "get_geocoder", lambda: geocoder)
    response = Mock(status_code=200)
    route = {"path": [
        {"location": {"lat": 49.0001, "lng": 8.0001}, "index": 0},
        {"location": {"lat": 50.0001, "lng": 9.0001}, "index": 100},
    ], "_embedded": {"coordinates": {"items": [[49, 8, 123], [50, 9, 456]]}}}
    response.json.return_value = route
    post = Mock(return_value=response)
    monkeypatch.setattr(routing.secure_http, "post", post)
    await functions["komoot_plan_and_upload"](
        "Station search", "50,9", waypoint_names=[None, "Walking trail"],
    )
    assert [point["name"] for point in post.call_args.kwargs["json"]["path"]] == [
        "Central station", "Walking trail",
    ]
    saved = client.save_planned_tour.call_args.kwargs["route_response"]
    assert saved is route
    assert [(point["name"], point["reference"]) for point in saved["path"]] == [
        ("Central station", "cnw:1"), ("Walking trail", "cnw:2"),
    ]
    assert saved["_embedded"]["coordinates"]["items"] == [[49, 8, 123], [50, 9, 456]]
    geocoder.reverse.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_gpx_endpoint_sources_fail_instead_of_guessing(handlers):
    functions, client = handlers
    gpx = """<gpx version="1.1">
      <trk><trkseg><trkpt lat="49" lon="8"><name>First start</name></trkpt>
        <trkpt lat="49.1" lon="8.1"/></trkseg></trk>
      <trk><trkseg><trkpt lat="49" lon="8"><name>Second start</name></trkpt>
        <trkpt lat="49.1" lon="8.1"/></trkseg></trk>
    </gpx>"""
    with pytest.raises(KomootAPIError, match="identify the source GPX track"):
        await functions["komoot_import_gpx_route"](gpx)
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", [
    "komoot_plan_native_route", "komoot_plan_and_save_tour", "komoot_plan_and_upload",
])
@pytest.mark.parametrize("details,expected", [
    (
        [{"highlight_id": 123}, {"name": "Station"},
         {"highlight_id": 456, "highlight_type": "segment"}],
        ["hlp:123", "cnw:2", "hls:456", "hlp:123"],
    ),
    (
        [{"poi_id": "Ab123"}, {"name": "Station"}, {"poi_id": "007zY"}],
        ["poi:Ab123", "cnw:2", "poi:007zY", "poi:Ab123"],
    ),
])
async def test_reference_identity_survives_snapping_roundtrip_and_save(
    handlers, monkeypatch, tool_name, details, expected,
):
    from komoot_mcp.tools import routing_tools
    functions, client = handlers
    if tool_name == "komoot_plan_and_upload":
        class Recorder:
            def tool(self):
                def register(fn):
                    functions[fn.__name__] = fn
                    return fn
                return register
        routing_tools.register(Recorder())
        monkeypatch.setattr(routing_tools, "get_client", lambda: client)
    route = {"path": [
        {"location": {"lat": 49.0001, "lng": 8}, "index": 0},
        {"location": {"lat": 49.1001, "lng": 8.1}, "index": 12},
        {"location": {"lat": 49.2001, "lng": 8.2}, "index": 24, "end_index": 30},
        {"location": {"lat": 49.0001, "lng": 8}, "index": 40},
    ], "query": "keep-query", "_embedded": {"coordinates": {"items": [[49, 8], [49.2, 8.2]]}}}
    response = Mock(status_code=200)
    response.json.return_value = route
    post = Mock(return_value=response)
    monkeypatch.setattr(routing.secure_http, "post", post)
    args: dict = {"start": "49,8", "end": "49.2,8.2", "waypoints": "49.1,8.1"} if tool_name == "komoot_plan_and_upload" else {
        "coordinates": [[49, 8], [49.1, 8.1], [49.2, 8.2]],
    }
    if tool_name == "komoot_plan_and_save_tour":
        args["name"] = "Highlights"
    await functions[tool_name](**args, roundtrip=True, waypoint_details=details)
    assert [p["reference"] for p in post.call_args.kwargs["json"]["path"]] == expected
    assert [p["reference"] for p in route["path"]] == expected
    assert route["path"][1]["name"] == "Station"
    assert route["path"][2]["end_index"] == 30
    assert route["query"] == "keep-query"
    if tool_name != "komoot_plan_native_route":
        call = client.save_planned_tour.call_args
        saved = call.kwargs["route_response"] if tool_name == "komoot_plan_and_upload" else call.args[0]
        assert saved is route


@pytest.mark.asyncio
@pytest.mark.parametrize("details", [
    [], [{"highlight_id": -1}, None], [{"highlight_id": True}, None],
    [{"name": "Custom", "highlight_id": 123}, None],
    [{"highlight_type": "segment"}, None], [{"name": "  "}, None],
    [{"highlight_id": 123, "unknown": True}, None],
])
async def test_invalid_highlight_metadata_never_authenticates(handlers, details):
    functions, client = handlers
    with pytest.raises(ValueError):
        await functions["komoot_plan_native_route"](
            [[49, 8], [49.1, 8.1]], waypoint_details=details,
        )
    client._basic_auth.assert_not_called()
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("detail,names", [
    ({"poi_id": "Ab123", "name": "Station"}, None),
    ({"poi_id": "Ab123", "highlight_id": 123}, None),
    ({"poi_id": "Ab123", "highlight_type": "segment"}, None),
    ({"poi_id": "Ab123"}, ["Station", None]),
])
async def test_conflicting_poi_metadata_never_authenticates(handlers, detail, names):
    functions, client = handlers
    with pytest.raises(ValueError):
        await functions["komoot_plan_native_route"](
            [[49, 8], [49.1, 8.1]], waypoint_details=[detail, None],
            waypoint_names=names,
        )
    client._basic_auth.assert_not_called()
    client.rl.acquire.assert_not_awaited()
    client.save_planned_tour.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("poi_id", [
    "", 123, True, "poi:Ab123", "osm:node:123", "ab_cd", "ab-cd", "é123",
    " Ab123", "Ab123 ", "Ab123\n",
])
async def test_invalid_poi_id_never_authenticates(handlers, poi_id):
    functions, client = handlers
    with pytest.raises(ValueError):
        await functions["komoot_plan_native_route"](
            [[49, 8], [49.1, 8.1]], waypoint_details=[{"poi_id": poi_id}, None],
        )
    client._basic_auth.assert_not_called()
    client.rl.acquire.assert_not_awaited()
    client.save_planned_tour.assert_not_awaited()
