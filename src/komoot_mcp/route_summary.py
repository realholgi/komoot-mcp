"""Derived route explanations kept separate from Komoot's native payload."""

import math
from numbers import Real


_WARNING_MESSAGES = {
    "MISPLACED_WAYPOINT_CHECK": (
        "Check the waypoint placement: the route may make a detour or U-turn near a waypoint."
    ),
    "FERRY": "The route includes a ferry. Check its operating times.",
    "BICYCLE_DISMOUNT": "You may need to dismount and push your bicycle on this section.",
    "MOVABLE_BRIDGE": "The route crosses a movable bridge. Check its operating times.",
    "UNSUITABLE": "Komoot marks this section as unsuitable for the selected sport.",
    "RESTRICTED": "Access is restricted on this section. Check the local access rules.",
    "OFF_GRID": "This section is off-grid and does not follow Komoot's routed network.",
    "TUNNEL": "The route passes through a tunnel.",
    "STEPS": "The route includes steps.",
    "HEAVY_TRAFFIC": "Komoot reports heavy traffic on this section.",
    "STEEP_UPHILL": "The route includes a steep uphill section.",
    "PRIVATE_LAND": "The route crosses private land. Check access permission.",
    "NO_ACCESS": "Komoot reports no access on this section.",
    "HIGH_DANGER": "Komoot reports high danger on this section.",
    "MODERATE_DANGER": "Komoot reports moderate danger on this section.",
    "WILDFIRE_ACTIVE": "Komoot reports an active wildfire affecting this section.",
    "WILDFIRE_RECENT": "Komoot reports a recent wildfire affecting this section.",
}
_INFORMATION_MESSAGES = {
    "CONSERVATION_AREA": "The route crosses a conservation area. Follow the local protection rules.",
    "OFFICIAL_MTB": "Komoot identifies this as an official mountain-bike route.",
    "OFFICIAL_BICYCLE": "Komoot identifies this as an official bicycle route.",
    "OFFICIAL_HIKING": "Komoot identifies this as an official hiking route.",
}


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        return None
    return value if (value > 0 if positive else value >= 0) else None


def route_summary(route, *, constitution=None):
    """Explain native values without changing them or assigning unverified UI labels."""
    distance = _number(route.get("distance"))
    raw_duration = route.get("duration")
    duration = _number(raw_duration, positive=True)
    minutes = int(duration / 60 + 0.5) if duration is not None else None
    fitness = route.get("constitution", constitution)
    # Public English Hiking planner: selecting Fast sent and returned constitution=4.
    # Observed 2026-11-18; other numeric label mappings are not established.
    label_verified = (
        route.get("sport") == "hike"
        and not isinstance(fitness, bool) and isinstance(fitness, Real) and fitness == 4
    )
    pace = {
        "distance_m": distance,
        "duration_s": duration,
        "duration_hm": f"{minutes // 60} h {minutes % 60:02d} min" if minutes is not None else None,
        "duration_status": "valid" if duration is not None else ("missing" if raw_duration is None else "invalid"),
        "average_speed_kmh": distance * 3.6 / duration if distance is not None and duration is not None else None,
        "constitution": fitness,
        "constitution_label": "Fast" if label_verified else None,
        "constitution_label_verified": label_verified,
        "label_language": "en" if label_verified else None,
        "constitution_label_source": {
            "url": "https://www.komoot.com/plan",
            "observed_on": "2026-11-18",
            "sport": "hike",
            "method": "Selecting Fast in the public English planner sent and returned constitution=4.",
        } if label_verified else None,
    }
    warnings = []
    information = []
    raw_entries = route.get("tour_information")
    entries = raw_entries if isinstance(raw_entries, list) else []
    for entry in entries:
        segments = entry.get("segments") if isinstance(entry, dict) else None
        segments = segments if isinstance(segments, list) else []
        code = entry.get("type") if isinstance(entry, dict) else None
        is_information = isinstance(code, str) and code in _INFORMATION_MESSAGES
        message = (_INFORMATION_MESSAGES if is_information else _WARNING_MESSAGES).get(code) if isinstance(code, str) else None
        target = information if is_information else warnings
        target.append({
            "code": code,
            "known": message is not None,
            "message": message or f"Unknown Komoot route warning type: {code!r}.",
            "coordinate_index_spans": [
                {"from": s.get("from"), "to": s.get("to")}
                for s in segments if isinstance(s, dict)
            ],
            "raw": entry,
        })
    return {
        "pace": pace,
        "warnings": warnings,
        "information": information,
        "raw_tour_information": raw_entries,
    }
