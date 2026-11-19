from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from komoot_mcp.planning_insights import planning_insights
from komoot_mcp.route_summary import route_summary
from komoot_mcp.routing import KomootNativePlanner, saved_plan_result
from komoot_mcp.tools import browse_tools


@pytest.fixture
def route():
    return {
        "distance": 12000, "duration": 5400, "constitution": 4,
        "sport": "hike",
        "tour_information": [
            {"type": "MISPLACED_WAYPOINT_CHECK", "segments": [{"from": 126, "to": 136}]},
            {"type": "CONSERVATION_AREA", "segments": [{
                "from": 0, "to": 209,
                "extra": {"protect_class": "5", "name": "Naturpark Schwarzwald Mitte/Nord"},
            }]},
        ],
    }


def test_warning_explains_coordinate_spans_and_preserves_upstream(route):
    original = deepcopy(route)
    summary = route_summary(route)
    warning, = summary["warnings"]
    assert warning["known"] is True
    assert warning["code"] == "MISPLACED_WAYPOINT_CHECK"
    assert "waypoint" in warning["message"] and "U-turn" in warning["message"]
    assert warning["coordinate_index_spans"] == [{"from": 126, "to": 136}]
    assert warning["raw"] == original["tour_information"][0]
    assert summary["raw_tour_information"] == original["tour_information"]
    assert route == original


def test_protection_area_is_information_not_danger(route):
    summary = route_summary(route)
    info, = summary["information"]
    assert info["code"] == "CONSERVATION_AREA"
    assert info["known"] is True
    assert "danger" not in info["message"].lower()
    assert info["raw"]["segments"][0]["extra"]["protect_class"] == "5"
    assert all(w["code"] != "CONSERVATION_AREA" for w in summary["warnings"])


def test_unknown_warning_retains_code_and_raw_segments():
    entry = {"type": "FUTURE_CHECK", "segments": [{"from": 2, "to": 3, "extra": {"future": 1}}]}
    warning, = route_summary({"tour_information": [entry]})["warnings"]
    assert warning["known"] is False
    assert "Unknown" in warning["message"] and "FUTURE_CHECK" in warning["message"]
    assert warning["code"] == "FUTURE_CHECK"
    assert warning["raw"] == entry


def test_malformed_unknown_entries_are_not_silently_lost():
    entries = [None, {"type": {"unexpected": True}}, {"segments": None}]
    summary = route_summary({"tour_information": entries})
    assert [w["raw"] for w in summary["warnings"]] == entries
    assert all(w["known"] is False for w in summary["warnings"])


def test_pace_uses_metres_seconds_and_verified_english_fitness_label(route):
    pace = route_summary(route)["pace"]
    assert pace["distance_m"] == 12000
    assert pace["duration_s"] == 5400
    assert pace["duration_hm"] == "1 h 30 min"
    assert pace["average_speed_kmh"] == pytest.approx(8)
    assert pace["constitution"] == 4
    assert pace["constitution_label"] == "Fast"
    assert pace["constitution_label_verified"] is True
    assert pace["label_language"] == "en"
    assert pace["constitution_label_source"]["url"] == "https://www.komoot.com/plan"
    assert pace["constitution_label_source"]["observed_on"] == "2026-11-18"


@pytest.mark.parametrize("fitness", [None, 1, 2, 3, 5, True, "4"])
def test_other_fitness_labels_remain_explicitly_unverified(fitness):
    pace = route_summary({"constitution": fitness})["pace"]
    assert pace["constitution_label"] is None
    assert pace["constitution_label_verified"] is False
    assert pace["label_language"] is None
    assert pace["constitution_label_source"] is None


@pytest.mark.parametrize("sport", [None, "e_mtb", "nordicwalking", "mountainbike"])
def test_unobserved_sports_remain_unverified(sport):
    pace = route_summary({"sport": sport, "constitution": 4})["pace"]
    assert pace["constitution_label"] is None
    assert pace["constitution_label_verified"] is False


@pytest.mark.parametrize("sport", ["hike", "jogging", "touringbicycle", "mtb", "racebike", "mtb_easy"])
@pytest.mark.parametrize("fitness,label", [(1, "Relaxed"), (2, "Steady"), (3, "Moderate"), (4, "Fast"), (5, "Very fast")])
def test_live_observed_preset_labels(sport, fitness, label):
    pace = route_summary({"sport": sport, "constitution": fitness})["pace"]
    assert pace["constitution_label"] == label
    assert pace["constitution_label_verified"] is True
    assert pace["constitution_label_source"]["sport"] == sport


@pytest.mark.parametrize("duration", [0, -1, True, "3600", float("nan"), float("inf")])
def test_invalid_duration_is_explicit_null(duration):
    pace = route_summary({"distance": 1000, "duration": duration})["pace"]
    assert pace["duration_status"] == "invalid"
    assert pace["duration_s"] is None
    assert pace["duration_hm"] is None
    assert pace["average_speed_kmh"] is None


def test_missing_duration_and_zero_distance():
    pace = route_summary({"distance": 0})["pace"]
    assert pace["duration_status"] == "missing"
    assert pace["distance_m"] == 0
    assert pace["duration_s"] is None
    assert route_summary({"distance": 0, "duration": 3600})["pace"]["average_speed_kmh"] == 0


def test_planner_preview_adds_only_namespaced_explanation(monkeypatch, route):
    response = Mock(status_code=200)
    response.json.return_value = deepcopy(route)
    monkeypatch.setattr("komoot_mcp.routing.secure_http.post", Mock(return_value=response))
    result = KomootNativePlanner(("user", "token")).plan([[48, 8], [48.1, 8.1]], "hike", 4)
    summary = result.pop("planning_summary")
    assert result == route
    assert summary == {
        **route_summary(route),
        **planning_insights(route, [[48, 8], [48.1, 8.1]]),
    }


def test_planner_summary_retains_requested_constitution_when_response_omits_it(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"distance": 1000, "duration": 1000}
    monkeypatch.setattr("komoot_mcp.routing.secure_http.post", Mock(return_value=response))
    route = KomootNativePlanner(("user", "token")).plan([[48, 8], [48.1, 8.1]], "hike", 4)
    assert route["planning_summary"]["pace"]["constitution"] == 4
    assert saved_plan_result({"id": 1}, route, "private")["planning_summary"]["pace"]["constitution"] == 4


def test_saved_result_propagates_only_explicit_visibility_verification(route):
    verification = {"verified": True, "requested": {"status": "private"}, "observed": {"status": "private"}, "mismatches": []}
    saved = {"id": 42, "verified_visibility": "private", "visibility_verified": True, "verification": verification}
    result = saved_plan_result(saved, route, "private")
    assert result["visibility_verified"] is True
    assert result["verified_visibility"] == "private"
    assert result["verification"] == verification
    assert result["planning_summary"] == route_summary(route)
    for unverified in ({"id": 42}, {"id": 42, "verified_visibility": "private"}):
        result = saved_plan_result(unverified, route, "private")
        assert result["visibility_verified"] is False
        assert result["verified_visibility"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["komoot_get_tour", "komoot_get_tour_full"])
@pytest.mark.parametrize("compact", [True, False])
async def test_browse_modes_include_explanations_without_mutating_raw(monkeypatch, route, tool_name, compact):
    original = deepcopy(route)
    client = SimpleNamespace(get_tour=AsyncMock(return_value=route), get_tour_full=AsyncMock(return_value=route))
    monkeypatch.setattr(browse_tools, "get_client", lambda: client)
    registered = {}

    class Registry:
        def tool(self):
            def register(fn):
                registered[fn.__name__] = fn
                return fn
            return register

    browse_tools.register(Registry())
    result = await registered[tool_name](42, compact=compact)
    assert result["planning_summary"] == route_summary(route)
    assert route == original
    if not compact:
        assert result["tour_information"] == route["tour_information"]

@pytest.mark.parametrize("status", ["saved", "updated"])
def test_saved_result_propagates_operation_status(status):
    result = saved_plan_result({"id": 42, "status": status}, {}, "private")
    assert result["status"] == status
