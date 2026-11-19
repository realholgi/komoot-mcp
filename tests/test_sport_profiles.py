from unittest.mock import Mock

import pytest

from komoot_mcp import routing
from komoot_mcp.route_summary import route_summary


PUBLIC_PROFILES = {
    "hike": "hike",
    "mountaineering": "mountaineering",
    "trail_run": "jogging",
    "mountain_bike": "mtb",
    "road_cycle": "racebike",
    "gravel_ride": "mtb_easy",
    "enduro": "mtb_advanced",
    "e_mountain_bike": "e_mtb",
    "e_road_cycle": "e_racebike",
    "e_gravel_ride": "e_mtb_easy",
    "e_enduro": "e_mtb_advanced",
}


@pytest.mark.parametrize("sport,profile", PUBLIC_PROFILES.items())
def test_public_sport_resolves_to_its_native_profile(sport, profile):
    assert routing.native_sport(sport) == profile


def test_public_profile_reference_has_exact_supported_map():
    assert routing.NATIVE_SPORT_PROFILES == PUBLIC_PROFILES


@pytest.mark.parametrize("sport", [
    "hike", "mountaineering", "jogging", "mtb", "mountainbike",
    "touringbicycle", "racebike", "nordicwalking", "mtb_easy", "mtb_advanced",
    "e_touringbicycle", "e_mtb", "e_racebike", "e_mtb_easy", "e_mtb_advanced",
])
def test_native_profiles_preserve_exact_identifier(sport):
    assert routing.native_sport(sport) == sport


@pytest.mark.parametrize("sport", [
    "e_mountaineering", "e_hike", "e_jogging", "unknown", "MTB_EASY", " gravel_ride",
    None, 3,
])
def test_unsupported_sports_have_no_fallback(sport):
    with pytest.raises(ValueError):
        routing.native_sport(sport)


@pytest.mark.parametrize("sport,profile", PUBLIC_PROFILES.items())
def test_native_request_uses_resolved_profile_in_query_and_body(monkeypatch, sport, profile):
    response = Mock(status_code=200)
    response.json.return_value = {"sport": profile, "distance": 1000, "duration": 600}
    post = Mock(return_value=response)
    monkeypatch.setattr(routing.secure_http, "post", post)

    route = routing.KomootNativePlanner(("user", "token")).plan(
        [(49, 8), (49.1, 8.1)], routing.native_sport(sport),
    )

    assert post.call_args.kwargs["params"]["sport"] == profile
    assert post.call_args.kwargs["json"]["sport"] == profile
    assert route["sport"] == profile


@pytest.mark.parametrize("sport", [
    "mountaineering", "mtb_advanced", "e_mtb", "e_racebike",
    "e_touringbicycle", "e_mtb_easy", "e_mtb_advanced",
])
def test_unverified_profiles_keep_pace_label_unknown(sport):
    pace = route_summary({"sport": sport, "constitution": 3})["pace"]
    assert pace["constitution"] == 3
    assert pace["constitution_label"] is None
    assert pace["constitution_label_verified"] is False
    assert pace["label_language"] is None
    assert pace["constitution_label_source"] is None


def test_gravel_keeps_observed_native_pace_label():
    pace = route_summary({"sport": routing.native_sport("gravel_ride"), "constitution": 3})["pace"]
    assert pace["constitution_label"] == "Moderate"
    assert pace["constitution_label_verified"] is True
