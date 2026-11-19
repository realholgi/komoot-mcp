"""Consumer-visible overnight mode, geometry and constraint behavior."""

import copy
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import gpxpy
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp import stage_planning
from komoot_mcp.overnight import cut_route, point_at, route_snapshot
from komoot_mcp.server import create_server
from komoot_mcp.tools import overnight_tools

_DEGREE = 180 / (math.pi * 6371.0088)


def _tour(length=30, step=1, elevation=True):
    return {"id": 42, "duration": length * 900, "_embedded": {"coordinates": {"items": [
        [0, km * _DEGREE, km * 5 if elevation else None] for km in range(0, length + 1, step)
    ]}}}


def _stay(identity, km, modes, offset=0):
    return {"id": identity, "name": identity, "lat": offset * _DEGREE, "lng": km * _DEGREE,
            "overnight_modes": modes, "permission": {"status": "unknown"},
            "source_links": [], "issues": [], "encounters": [{"route_km": km, "offset_m": offset * 1000}]}


def _discovery(items=None, status="ok"):
    return {"items": items or [], "status": status, "issues": [], "coverage": {}, "sources": []}


@pytest.fixture
def account(monkeypatch):
    account = SimpleNamespace(get_tour_full=AsyncMock(return_value=_tour()))
    monkeypatch.setattr(overnight_tools, "get_client", lambda: account)
    return account


@pytest.mark.parametrize("arguments", [
    {"tour_id": 42}, {"tour_id": 42, "overnight_modes": []},
    {"tour_id": 42, "overnight_modes": ["wild_camping"]},
    {"tour_id": 42, "overnight_modes": ["tent", "tent"]},
])
async def test_modes_required_before_account_access(account, arguments):
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        response = await session.call_tool("komoot_find_overnight_stays", arguments)
    assert response.isError
    assert response.structuredContent["error"]["code"] == "invalid_input"
    account.get_tour_full.assert_not_awaited()


async def test_union_search_keeps_both_modes_and_source_window_positions(account, monkeypatch):
    places = AsyncMock(return_value=_discovery([_stay("camp", 1, ["tent"]), _stay("hotel", 2, ["accommodation"])]))
    terrain = AsyncMock()
    monkeypatch.setattr(overnight_tools, "find_overnight_places", places)
    monkeypatch.setattr(overnight_tools, "find_bivouac_candidates", terrain)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        response = await session.call_tool("komoot_find_overnight_stays", {
            "tour_id": 42, "overnight_modes": ["tent", "accommodation"], "start_km": 10, "end_km": 15,
        })
    assert not response.isError
    result = response.structuredContent["data"]
    assert [(item["id"], item["encounters"][0]["route_km"]) for item in result["items"]] == [("camp", 11), ("hotel", 12)]
    assert places.call_args.args[2] == ["tent", "accommodation"]
    terrain.assert_not_awaited()


async def test_bivouac_and_accommodation_union_retains_partial_source_failure(account, monkeypatch):
    monkeypatch.setattr(overnight_tools, "find_overnight_places", AsyncMock(return_value=_discovery([_stay("hotel", 1, ["accommodation"])])))
    terrain = AsyncMock(return_value=_discovery(status="unavailable"))
    monkeypatch.setattr(overnight_tools, "find_bivouac_candidates", terrain)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        response = await session.call_tool("komoot_find_overnight_stays", {
            "tour_id": 42, "overnight_modes": ["bivouac", "accommodation"], "start_km": 10, "end_km": 10.2,
        })
    result = response.structuredContent["data"]
    assert result["status"] == "partial"
    assert [item["id"] for item in result["items"]] == ["hotel"]
    terrain.assert_awaited_once()


async def test_fixed_mixed_stays_cut_original_geometry_without_discovery(account, monkeypatch):
    discover = AsyncMock()
    monkeypatch.setattr(overnight_tools, "_discover", discover)
    fixed = [{"night": night, "name": mode, "lat": 0, "lng": km * _DEGREE,
              "route_km": km, "overnight_modes": [mode]}
             for night, km, mode in [(1, 10.5, "tent"), (2, 20.5, "accommodation")]]
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        response = await session.call_tool("komoot_plan_stages", {
            "tour_id": 42, "overnight_modes": ["tent", "accommodation"], "days": 3,
            "fixed_stays": fixed, "night_modes": [["tent"], ["accommodation"]], "start_date": "2026-11-19",
        })
    assert not response.isError
    result = response.structuredContent["data"]
    stages = result["alternatives"][0]["stages"]
    parsed = [gpxpy.parse(stage["gpx_content"]).tracks[0].segments[0].points for stage in stages]
    assert [stage["date"] for stage in stages] == ["2026-11-19", "2026-11-20", "2026-11-21"]
    assert [stage["overnight"]["name"] if stage["overnight"] else None for stage in stages] == ["tent", "accommodation", None]
    assert parsed[0][-1].longitude == pytest.approx(parsed[1][0].longitude)
    assert parsed[1][-1].longitude == pytest.approx(parsed[2][0].longitude)
    observed = [point.longitude for section in parsed for point in section]
    for point in account.get_tour_full.return_value["_embedded"]["coordinates"]["items"]:
        assert any(abs(lng - point[1]) < 1e-10 for lng in observed)
    assert sum(stage["distance_m"] for stage in stages) == pytest.approx(30000)
    discover.assert_not_awaited()


async def test_complete_sequence_avoids_nearest_greedy_dead_end():
    tour = _tour(33)
    points, distances = route_snapshot(tour)
    candidates = [_stay("near", 10.9, ["tent"]), _stay("works", 11.5, ["tent"]),
                  _stay("hotel", 23, ["accommodation"])]
    result = await stage_planning.plan_stages(None, tour, points, distances, candidates,
                                             ["tent", "accommodation"], 3, max_distance_km=11.6,
                                             night_modes=[["tent"], ["accommodation"]])
    assert result["status"] == "ok"
    assert result["alternatives"][0]["stages"][0]["overnight"]["id"] == "works"


async def test_alternatives_do_not_mutate_shared_stage_geometry():
    tour = _tour()
    original = copy.deepcopy(tour)
    points, distances = route_snapshot(tour)
    candidates = [_stay(str(km), km, ["tent"]) for km in [9, 10, 11, 19, 20, 21]]
    result = await stage_planning.plan_stages(None, tour, points, distances, candidates, ["tent"], 3)
    assert len(result["alternatives"]) == 3
    for alternative in result["alternatives"]:
        assert sum(stage["distance_m"] for stage in alternative["stages"]) == pytest.approx(30000)
        assert alternative["stages"][-1]["source_end_km"] == pytest.approx(30)
    assert tour == original


async def test_routed_detours_use_distinct_directions_and_preserve_anchor(monkeypatch):
    calls = []
    async def route(client, coordinates, sport, constitution):
        calls.append(coordinates)
        start, end = coordinates
        # Outbound route takes a longer bend than the independently routed return.
        bend = [start[0] + (0.002 if len(calls) == 1 else 0.001), start[1] + 0.001, 100]
        return {"duration": len(calls) * 100, "_embedded": {"coordinates": {"items": [start, bend, end]}}}
    monkeypatch.setattr(stage_planning, "plan_native_route", route)
    tour = _tour(20)
    points, distances = route_snapshot(tour)
    stay = _stay("hotel", 10, ["accommodation"], offset=0.1)
    result = await stage_planning.plan_stages(None, tour, points, distances, [stay], ["accommodation"], 2)
    stages = result["alternatives"][0]["stages"]
    assert calls[1] == list(reversed(calls[0]))
    outbound = stages[0]["access_legs"]["to_next_stay"]
    returning = stages[1]["access_legs"]["from_previous_stay"]
    assert outbound["distance_m"] != returning["distance_m"]
    assert outbound["points"][0][:2] == pytest.approx(point_at(points, distances, 10)[:2])
    assert returning["points"][-1][:2] == pytest.approx(outbound["points"][0][:2])
    assert sum(stage["distance_m"] for stage in stages) == pytest.approx(20000 + outbound["distance_m"] + returning["distance_m"])


async def test_unknown_bivouac_approach_time_cannot_satisfy_hard_time_limit():
    tour = _tour(20)
    points, distances = route_snapshot(tour)
    stay = _stay("bivouac", 10, ["bivouac"], offset=0.1)
    preview = await stage_planning.plan_stages(None, tour, points, distances, [stay], ["bivouac"], 2)
    stages = preview["alternatives"][0]["stages"]
    assert all(stage["access_status"] == "unverified_off_path" for stage in stages)
    assert all(stage["duration_s"] is None for stage in stages)
    limited = await stage_planning.plan_stages(None, tour, points, distances, [stay], ["bivouac"], 2, max_duration_s=20000)
    assert limited["status"] == "no_feasible_plan"
    assert limited["alternatives"] == []


async def test_mode_preferences_rank_without_excluding_alternatives():
    tour = _tour(20)
    points, distances = route_snapshot(tour)
    candidates = [_stay("hotel", 10, ["accommodation"]), _stay("camp", 10, ["tent"])]
    result = await stage_planning.plan_stages(None, tour, points, distances, candidates,
                                             ["tent", "accommodation"], 2, preferred_modes=["tent"])
    assert [alternative["stages"][0]["overnight"]["id"] for alternative in result["alternatives"]] == ["camp", "hotel"]


async def test_unknown_elevation_does_not_pass_ascent_limit():
    tour = _tour(20, elevation=False)
    points, distances = route_snapshot(tour)
    result = await stage_planning.plan_stages(None, tour, points, distances, [_stay("camp", 10, ["tent"])],
                                             ["tent"], 2, max_ascent_m=500)
    assert result["status"] == "no_feasible_plan"


def test_fractional_cuts_interpolate_across_antimeridian_without_shortcuts():
    tour = {"_embedded": {"coordinates": {"items": [[20, 179, 100], [20, -179, 200]]}}}
    points, distances = route_snapshot(tour)
    left, _ = cut_route(points, distances, 0, distances[-1] / 2)
    right, _ = cut_route(points, distances, distances[-1] / 2, distances[-1])
    assert abs(left[-1][1]) == pytest.approx(180)
    assert left[-1] == right[0]
    assert left[-1][2] == pytest.approx(150)


@pytest.mark.parametrize("options", [
    {"night_modes": [["bivouac"]]},
    {"night_modes": []},
    {"preferred_modes": ["bivouac"]},
    {"max_detour_m": float("nan")},
    {"fixed_stays": [{"night": 1, "name": "Place", "lat": 0, "lng": 0,
                      "route_km": 10, "overnight_modes": ["bivouac"]}]},
])
async def test_invalid_stage_constraints_fail_before_account_access(account, options):
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        response = await session.call_tool("komoot_plan_stages", {
            "tour_id": 42, "overnight_modes": ["tent", "accommodation"], "days": 2, **options,
        })
    assert response.isError
    assert response.structuredContent["error"]["code"] == "invalid_input"
    account.get_tour_full.assert_not_awaited()
