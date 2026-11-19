"""Check native evidence positions and distance-based timing estimates."""

import math
from copy import deepcopy
from datetime import timedelta

import pytest

from komoot_mcp.briefing_analysis import (
    briefing_schedule,
    briefing_sections,
    validate_briefing_timing,
)
from komoot_mcp.route_sections import coordinate_distances


DEGREES_PER_KM = 180 / (math.pi * 6371.0088)


def native_tour():
    return {
        "duration": 3600,
        # The aggregate distance must not control warning positions.
        "distance": 999999,
        "tour_information": [
            {"type": "STEPS", "segments": [{"from": 1, "to": 3}]},
            {"type": "NEW_WARNING", "segments": [{"from": 3, "to": 4}]},
        ],
        "_embedded": {
            "coordinates": {"items": [[0, i * DEGREES_PER_KM] for i in range(5)]},
            "way_types": {"items": [
                {"from": 0, "to": 1, "element": "wt#hike_d2"},
                {"from": 1, "to": 2, "element": "wt#trail_d7"},
                {"from": 2, "to": 3, "element": "wt#alpine_hiking_path"},
                {"from": 3, "to": 4, "element": "wt#way"},
            ]},
            "surfaces": {"items": [
                {"from": 0, "to": 1, "element": "sf#stone"},
                {"from": 1, "to": 2, "element": "sf#sand"},
                {"from": 2, "to": 3, "element": "sf#alpin"},
                {"from": 3, "to": 4, "element": "sf#ground"},
            ]},
        },
    }


def test_native_warning_kilometres_and_highlights_keep_evidence():
    tour = native_tour()
    original = deepcopy(tour)
    distances = coordinate_distances(tour["_embedded"]["coordinates"]["items"])
    result = briefing_sections(tour, distances)
    assert result["sections"]["total_km"] == pytest.approx(4)
    warning = result["critical_sections"][0]
    assert warning["spans"] == [{
        "from": 1, "to": 3,
        "start_km": pytest.approx(1), "end_km": pytest.approx(3),
        "location_status": "located",
    }]
    assert warning["coordinate_index_spans"] == [{"from": 1, "to": 3}]
    assert warning["raw"] == original["tour_information"][0]
    assert warning["source"] == "komoot.tour_information"
    assert result["critical_sections"][1]["known"] is False
    assert result["critical_sections"][1]["code"] == "NEW_WARNING"
    assert len(result["surface_sections"]) == 3
    assert result["surface_sections"][0]["surface"]["label"] == "Stone"
    assert result["surface_sections"][1]["way_type"]["label"] == "Trail with difficulty 7"
    assert result["sections"]["label_source"]["surfaces"].endswith("surfaces.html")
    assert result["coverage"] == "partial"
    assert tour == original


@pytest.mark.parametrize("span", [
    {"from": True, "to": 2}, {"from": 0, "to": False},
    {"from": -1, "to": 2}, {"from": 2, "to": 1},
    {"from": 1, "to": 1}, {"from": 0, "to": 5},
    {"from": 0.5, "to": 2}, {"from": None, "to": 2}, {},
])
def test_invalid_warning_spans_remain_unlocated(span):
    tour = native_tour()
    tour["tour_information"] = [{"type": "RESTRICTED", "segments": [span]}]
    distances = coordinate_distances(tour["_embedded"]["coordinates"]["items"])
    result = briefing_sections(tour, distances)
    warning = result["critical_sections"][0]
    assert warning["raw"]["segments"] == [span]
    assert warning["coverage"] == "unknown"
    assert warning["spans"][0]["start_km"] is None
    assert warning["spans"][0]["end_km"] is None
    assert result["missing_data"]


def test_warning_without_spans_and_missing_geometry_stay_unknown():
    tour = {"tour_information": [
        {"type": "STEPS"},
        {"type": "FERRY", "segments": [{"from": 0, "to": 1}, None]},
    ]}
    result = briefing_sections(tour, None)
    assert result["critical_sections"][0]["spans"] == []
    assert result["critical_sections"][1]["spans"][0]["start_km"] is None
    assert result["critical_sections"][1]["raw"]["segments"][1] is None
    assert result["surface_sections"] == []
    assert result["missing_data"]


def test_unknown_surface_and_way_codes_do_not_become_difficulty_claims():
    tour = native_tour()
    tour["_embedded"]["way_types"]["items"] = [{"from": 0, "to": 4, "element": "wt#hike_d99"}]
    tour["_embedded"]["surfaces"]["items"] = [{"from": 0, "to": 4, "element": "future#stone"}]
    result = briefing_sections(tour, [0, 1, 2, 3, 4])
    assert result["surface_sections"] == []
    assert result["sections"]["sections"][0]["way_type"]["label"] is None
    assert result["sections"]["coverage"] == "partial"


@pytest.mark.parametrize("departure", [True, 123, "2026-11-19", "2026-11-19T08:00:00", "not a date"])
def test_invalid_departure_is_rejected(departure):
    with pytest.raises(ValueError):
        validate_briefing_timing(departure, [])


@pytest.mark.parametrize("pauses", [
    {}, "pause", [None], [[]], [{}],
    [{"km": True, "duration_s": 1}], [{"km": 1, "duration_s": False}],
    [{"km": math.nan, "duration_s": 1}], [{"km": 1, "duration_s": math.nan}],
    [{"km": math.inf, "duration_s": 1}], [{"km": 1, "duration_s": math.inf}],
    [{"km": -1, "duration_s": 1}], [{"km": 1, "duration_s": -1}],
    [{"km": "1", "duration_s": 1}], [{"km": 1, "duration_s": 1, "name": None}],
])
def test_invalid_pause_input_is_rejected(pauses):
    with pytest.raises(ValueError):
        validate_briefing_timing(None, pauses)


def test_offset_timezone_and_stable_pause_order_are_preserved():
    original = [{"km": 2, "duration_s": 10, "name": "Later"},
                {"km": 1, "duration_s": 0, "name": "First"},
                {"km": 1, "duration_s": 20, "name": "Second"}]
    departure, pauses = validate_briefing_timing("2026-11-19T08:00:00+05:30", original)
    assert departure is not None
    assert departure.utcoffset() == timedelta(hours=5, minutes=30)
    assert [pause["name"] for pause in pauses] == ["First", "Second", "Later"]
    assert [pause["name"] for pause in original] == ["Later", "First", "Second"]
    assert pauses[0] is not original[1]
    utc_departure, _ = validate_briefing_timing("2026-11-19T08:00:00Z", None)
    assert utc_departure is not None
    assert utc_departure.utcoffset() == timedelta(0)


def test_schedule_accumulates_pauses_after_same_km_supply_arrivals():
    departure, pauses = validate_briefing_timing("2026-11-19T08:00:00+02:00", [
        {"km": 5, "duration_s": 600, "name": "Lunch"},
        {"km": 0, "duration_s": 60},
        {"km": 5, "duration_s": 120, "name": "Water"},
        {"km": 10, "duration_s": 30},
    ])
    supplies = [{"name": "Shop", "route_km": 5}]
    result = briefing_schedule({"duration": 3600}, 10, supplies, departure, pauses)
    events = result["events"]
    assert [event["kind"] for event in events] == ["start", "pause", "supply", "pause", "pause", "pause", "finish"]
    assert events[0]["arrival_elapsed_s"] == events[0]["departure_elapsed_s"] == 0
    assert events[1]["arrival_elapsed_s"] == 0
    assert events[1]["departure_elapsed_s"] == 60
    assert events[2]["arrival_elapsed_s"] == events[2]["departure_elapsed_s"] == 1860
    assert events[2]["arrival_time"] == "2026-11-19T08:31:00+02:00"
    assert events[3]["arrival_elapsed_s"] == 1860
    assert events[3]["departure_elapsed_s"] == 2460
    assert events[4]["arrival_elapsed_s"] == 2460
    assert events[4]["departure_elapsed_s"] == 2580
    assert events[-1]["arrival_elapsed_s"] == 4410
    assert events[-1]["arrival_time"] == "2026-11-19T09:13:30+02:00"
    assert result["moving_s"] == 3600
    assert result["pause_s"] == 810
    assert result["total_s"] == 4410
    assert supplies == [{"name": "Shop", "route_km": 5}]


def test_all_loop_supply_encounters_get_schedule_events():
    supplies = [{"name": "Tap", "route_km": 2,
                 "encounters": [{"route_km": 2, "offset_m": 10},
                                {"route_km": 8, "offset_m": 12}]}]
    result = briefing_schedule({"duration": 1000}, 10, supplies, None, [])
    events = [event for event in result["events"] if event["kind"] == "supply"]
    assert [event["km"] for event in events] == [2, 8]
    assert [event["arrival_elapsed_s"] for event in events] == [200, 800]
    assert all(event["arrival_time"] is None for event in events)


@pytest.mark.parametrize("duration", [None, 0, -1, True, math.nan, math.inf, "3600"])
def test_missing_or_invalid_duration_never_invents_pace(duration):
    result = briefing_schedule({"duration": duration}, 10, [{"km": 5}], None,
                               [{"km": 1, "duration_s": 60}])
    assert result["status"] == "unknown"
    assert result["moving_s"] is None
    assert result["total_s"] is None
    assert result["pause_s"] == 60
    assert all(event["arrival_elapsed_s"] is None for event in result["events"])
    assert result["missing_data"]


@pytest.mark.parametrize("total_km", [0, None])
def test_unknown_or_zero_route_length_never_divides_or_fabricates_times(total_km):
    result = briefing_schedule({"duration": 1000}, total_km, [], None, [])
    assert result["status"] == "unknown"
    assert result["total_s"] is None
    assert result["missing_data"]


def test_unknown_geometry_keeps_unverified_pause_location():
    result = briefing_schedule({"duration": 1000}, None, [], None,
                               [{"km": 99, "duration_s": 60}])
    assert result["pause_locations_verified"] is False
    assert result["events"][1]["km"] == 99
    assert result["events"][1]["location_status"] == "unverified"
    assert result["events"][-1]["km"] is None


def test_pause_beyond_known_route_is_rejected_not_clamped():
    with pytest.raises(ValueError, match="route length"):
        briefing_schedule({"duration": 1000}, 10, [], None,
                          [{"km": 10.001, "duration_s": 60}])


def test_invalid_supply_positions_are_reported_not_clamped():
    result = briefing_schedule({"duration": 1000}, 10,
                               [{"km": 11}, {"km": True}, {"name": "No position"}], None, [])
    assert len(result["unlocated_supplies"]) == 3
    assert len(result["events"]) == 2
    assert result["missing_data"]


def test_recorded_tour_uses_native_moving_time_before_total_duration():
    result = briefing_schedule({"type": "tour_recorded", "duration": 7200,
                                "time_in_motion": 3600}, 10, [], None, [])
    assert result["moving_s"] == 3600
    assert result["source"]["duration"] == "komoot.time_in_motion"
    assert result["duration_basis"] == "recorded_time_in_motion"


@pytest.mark.parametrize("in_motion", [None, 0, True, math.nan])
def test_recorded_total_duration_fallback_is_explicit(in_motion):
    result = briefing_schedule({"type": "tour_recorded", "duration": 7200,
                                "time_in_motion": in_motion}, 10, [], None, [])
    assert result["moving_s"] == 7200
    assert result["source"]["duration"] == "komoot.duration"
    assert result["duration_basis"] == "recorded_total_duration"
    assert any("existing stops" in limit for limit in result["limits"])
