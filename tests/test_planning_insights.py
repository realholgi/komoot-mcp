from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from komoot_mcp.planning_insights import planning_insights
from komoot_mcp.routing import compare_route_variants, plan_native_route, saved_plan_result


def client():
    return SimpleNamespace(_basic_auth=Mock(return_value=("user", "token")),
                           rl=SimpleNamespace(acquire=AsyncMock()))


def test_snap_uses_geometry_index_not_path_location():
    route = {"path": [{"index": 0, "location": {"lat": 0, "lng": 0}}, {"index": 1}],
             "_embedded": {"coordinates": {"items": [[0, 0.001], [0, 0.01]]}}}
    report = planning_insights(route, [[0, 0], [0, 0.01]])["access_diagnostics"]
    assert report["waypoints"][0]["distance_m"] == pytest.approx(111.195, abs=0.01)
    assert report["waypoints"][0]["suspicious"] is True
    assert report["thresholds"]["waypoint_distance_m"] == 50


@pytest.mark.parametrize("path", [[], [{}, {}], [{"index": True}, {"index": -1}],
                                   [{"index": 10}, {"index": 0}]])
def test_missing_or_unmatched_indices_remain_unknown(path):
    route = {"path": path, "_embedded": {"coordinates": {"items": [[0, 0]]}}}
    report = planning_insights(route, [[0, 0], [0, 0]])["access_diagnostics"]
    assert report["waypoints"][0]["status"] == "unknown"
    assert report["waypoints"][0]["distance_m"] is None
    assert report["legs"][0]["routed_distance_m"] is None


def test_loop_has_distance_but_no_ratio_and_detour_has_both_thresholds():
    route = {"path": [{"index": 0}, {"index": 2}, {"index": 4}],
             "_embedded": {"coordinates": {"items": [
                 [0, 0], [0.01, 0], [0, 0], [0.01, 0], [0, 0.001],
             ]}}}
    legs = planning_insights(route, [[0, 0], [0, 0], [0, 0.001]])["access_diagnostics"]["legs"]
    assert legs[0]["routed_distance_m"] > 2000
    assert legs[0]["detour_ratio"] is None
    assert legs[0]["ratio_status"] == "zero_straight_line"
    assert legs[0]["suspicious"] is None
    assert legs[1]["detour_ratio"] > 3
    assert legs[1]["excess_distance_m"] > 500
    assert legs[1]["suspicious"] is True


@pytest.mark.asyncio
async def test_roundtrip_closure_does_not_repeat_stay_or_change_native_duration(monkeypatch):
    upstream = {"duration": 100, "distance": 1000, "path": [
        {"location": {"lat": 0, "lng": 0}, "index": 0},
        {"location": {"lat": 0, "lng": 0.01}, "index": 1},
        {"location": {"lat": 0, "lng": 0}, "index": 2, "reference": "special:back"},
    ]}
    response = Mock(status_code=200)
    response.json.side_effect = lambda: deepcopy(upstream)
    post = Mock(return_value=response)
    monkeypatch.setattr("komoot_mcp.routing.secure_http.post", post)
    route = await plan_native_route(client(), [[0, 0], [0, 0.01]], roundtrip=True,
                                    waypoint_stays_s=[60, 120])
    assert route["duration"] == 100
    assert route["planning_summary"]["timing"] == {
        "moving_time_s": 100, "stay_time_s": 180, "total_time_s": 280,
        "waypoint_stays_s": [60, 120, 0], "moving_time_status": "known",
    }
    assert "waypoint_stays_s" not in post.call_args.kwargs["json"]
    saved = saved_plan_result({"id": 1}, route, "private")
    assert saved["planning_summary"]["timing"] == route["planning_summary"]["timing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stays", [[0], [True, 0], [-1, 0], [float("nan"), 0], [0, float("inf")]])
async def test_invalid_stays_fail_before_authentication(stays):
    instance = client()
    with pytest.raises(ValueError, match="waypoint_stays_s"):
        await plan_native_route(instance, [[0, 0], [0, 0.01]], waypoint_stays_s=stays)
    instance._basic_auth.assert_not_called()


@pytest.mark.asyncio
async def test_comparison_keeps_full_routes_inputs_and_deltas(monkeypatch):
    async def plan(*args):
        route_type = args[6]
        return {"distance": {"shortest": 100, "scenic": 150, "quiet": 125}[route_type],
                "duration": 10, "extra_native_field": route_type}

    planner = AsyncMock(side_effect=plan)
    monkeypatch.setattr("komoot_mcp.routing.plan_native_route", planner)
    instance = client()
    coordinates, names, avoid, details, stays = [[0, 0], [0, 1]], ["A", "B"], ["steps"], [None, None], [0, 60]
    report = await compare_route_variants(instance, coordinates, "hike", 4, True, names, avoid, details, stays)
    for call, kind in zip(planner.call_args_list, ("shortest", "scenic", "quiet")):
        assert call.args == (instance, coordinates, "hike", 4, True, names, kind, avoid, details, stays)
    assert report["variants"]["scenic"]["route"]["extra_native_field"] == "scenic"
    assert report["variants"]["scenic"]["deltas_vs_shortest"]["distance_m"] == 50
    assert report["variants"]["quiet"]["deltas_vs_shortest"]["elevation_up_m"] is None


def test_default_stays_are_zero_and_missing_moving_time_stays_unknown():
    timing = planning_insights({}, [[0, 0], [0, 1]])["timing"]
    assert timing["stay_time_s"] == 0
    assert timing["waypoint_stays_s"] == [0, 0]
    assert timing["moving_time_s"] is None
    assert timing["total_time_s"] is None
