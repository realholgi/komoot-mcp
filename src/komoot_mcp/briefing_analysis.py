"""Explain native route evidence and estimate a read-only tour schedule."""

import math
from datetime import datetime, timedelta
from numbers import Real
from typing import TypeGuard

from .route_sections import analyze_tour_sections
from .route_summary import route_summary


def _finite_nonnegative(value) -> TypeGuard[float]:
    return (
        isinstance(value, Real) and not isinstance(value, bool)
        and math.isfinite(value) and float(value) >= 0
    )


def briefing_sections(tour, distances):
    """Keep native warnings and labels; never infer safety or access."""
    sections = analyze_tour_sections(tour)
    if any(section[kind]["label"] is None for section in sections["sections"] for kind in ("surface", "way_type")):
        sections["issues"].append("Some native section codes have no documented label.")
        if sections["coverage"] == "complete":
            sections["coverage"] = "partial"
    summary = route_summary(tour)
    critical = []
    missing = list(sections["issues"])
    if sections["coverage"] != "complete" and not missing:
        missing.append("Some native section attributes or labels are unknown.")
    valid_distances = (
        isinstance(distances, list) and bool(distances)
        and all(_finite_nonnegative(km) for km in distances)
        and all(a <= b for a, b in zip(distances, distances[1:]))
    )
    for warning in summary["warnings"]:
        spans = []
        for span in warning["coordinate_index_spans"]:
            first, last = span["from"], span["to"]
            valid_indices = (
                isinstance(first, int) and not isinstance(first, bool)
                and isinstance(last, int) and not isinstance(last, bool)
                and 0 <= first < last
            )
            located = valid_distances and valid_indices and last < len(distances)
            spans.append({
                **span,
                "start_km": distances[first] if located else None,
                "end_km": distances[last] if located else None,
                "location_status": "located" if located else "unknown",
            })
        coverage = "complete" if spans and all(s["location_status"] == "located" for s in spans) else "partial" if any(s["location_status"] == "located" for s in spans) else "unknown"
        critical.append({**warning, "spans": spans, "coverage": coverage,
                         "source": "komoot.tour_information"})
        if coverage != "complete":
            missing.append(f"Warning {warning['code']!r}: some or all kilometre positions are unknown.")
        if not warning["known"]:
            missing.append(f"Warning {warning['code']!r}: its native meaning is unknown.")
    if not isinstance(tour.get("tour_information"), list):
        missing.append("Native tour_information is missing or invalid; warning coverage is unknown.")
    highlighted = []
    for section in sections["sections"]:
        surface = section["surface"]
        way = section["way_type"]
        surface_code = surface["native_type"]
        way_code = way["native_type"]
        reasons = []
        if surface["status"] == "known" and surface["label"] is not None and surface_code.rsplit("#", 1)[-1] in {"stone", "sand", "alpin"}:
            reasons.append("native_surface")
        if way["status"] == "known" and way["label"] is not None and (
            way_code.startswith(("wt#hike_d", "wt#trail_d"))
            or way_code == "wt#alpine_hiking_path"
        ):
            reasons.append("native_way_difficulty")
        if reasons:
            highlighted.append({**section, "highlight_reasons": reasons})
    return {
        "sections": sections,
        "surface_sections": highlighted,
        "critical_sections": critical,
        "information": summary["information"],
        "source": "komoot native section spans and tour_information",
        "coverage": "partial" if missing else "complete",
        "missing_data": missing,
        "limits": [
            "Highlights retain native labels; they are not safety or suitability ratings.",
            "Missing warnings do not establish that the route is safe or accessible.",
            "Kilometres use cumulative coordinate haversine distance, not native aggregate distance.",
        ],
    }


def validate_briefing_timing(departure_time, pauses):
    """Validate timing input before fetching route data. Keep equal-km order."""
    departure = None
    if departure_time is not None:
        if not isinstance(departure_time, str):
            raise ValueError("departure_time must be an ISO8601 timestamp with a timezone")
        try:
            departure = datetime.fromisoformat(departure_time)
        except ValueError as exc:
            raise ValueError("departure_time must be an ISO8601 timestamp with a timezone") from exc
        if departure.tzinfo is None or departure.utcoffset() is None:
            raise ValueError("departure_time must include a timezone")
    if pauses is None:
        pauses = []
    if not isinstance(pauses, list):
        raise ValueError("pauses must be a list of objects")
    normalized = []
    for pause in pauses:
        if not isinstance(pause, dict):
            raise ValueError("Each pause must be an object")
        if not _finite_nonnegative(pause.get("km")) or not _finite_nonnegative(pause.get("duration_s")):
            raise ValueError("Pause km and duration_s must be finite nonnegative numbers")
        if "name" in pause and not isinstance(pause["name"], str):
            raise ValueError("Pause name must be a string")
        normalized.append(dict(pause))
    return departure, sorted(normalized, key=lambda pause: pause["km"])


def briefing_schedule(tour, total_km, supplies, departure, pauses):
    """Estimate uniform moving pace; add pauses only after arrival."""
    _, pauses = validate_briefing_timing(None, pauses)
    geometry_known = _finite_nonnegative(total_km)
    if geometry_known and any(pause["km"] > total_km for pause in pauses):
        raise ValueError("Pause km must not exceed the route length")
    pace = route_summary(tour)["pace"]
    duration = pace["duration_s"]
    duration_source = "komoot.duration"
    duration_basis = "planned_duration" if tour.get("type") == "tour_planned" else "native_duration"
    timing_limits = []
    if tour.get("type") == "tour_recorded":
        in_motion = tour.get("time_in_motion")
        if _finite_nonnegative(in_motion) and in_motion > 0:
            duration = in_motion
            duration_source = "komoot.time_in_motion"
            duration_basis = "recorded_time_in_motion"
        else:
            duration_basis = "recorded_total_duration"
            timing_limits.append("Recorded moving time is missing or invalid; native duration can include existing stops.")
    seconds_per_km = duration / total_km if duration is not None and _finite_nonnegative(total_km) and total_km > 0 else None
    known = seconds_per_km is not None
    missing = []
    if not geometry_known:
        missing.append("Route length is unknown; pause locations cannot be verified.")
    elif total_km == 0:
        missing.append("Route length is zero; distance-based moving times are unknown.")
    if duration is None:
        missing.append(f"Native duration is {pace['duration_status']}; moving times are unknown.")
    events = [{"kind": "start", "km": 0}]
    located = []
    unlocated_supplies = []
    for supply in supplies:
        encounters = supply.get("encounters")
        positions = encounters if isinstance(encounters, list) else [{"route_km": supply.get("route_km", supply.get("km"))}]
        for encounter in positions:
            km = encounter.get("route_km") if isinstance(encounter, dict) else None
            if not _finite_nonnegative(km) or (_finite_nonnegative(total_km) and km > total_km):
                unlocated_supplies.append({"supply": supply, "encounter": encounter})
                continue
            located.append({"kind": "supply", "km": km, "supply": supply,
                            "encounter": encounter})
        if not positions:
            unlocated_supplies.append({"supply": supply, "encounter": None})
    located.extend({**pause, "kind": "pause"} for pause in pauses)
    # All supply arrivals at one point precede the pauses at that point.
    located.sort(key=lambda event: (event["km"], event["kind"] == "pause"))
    events.extend(located)
    events.append({"kind": "finish", "km": total_km if geometry_known else None})
    accumulated = 0
    timestamp_overflow = False
    for event in events:
        moving = seconds_per_km * event["km"] if seconds_per_km is not None else None
        arrival = moving + accumulated if moving is not None else None
        if event["kind"] == "pause":
            accumulated += event["duration_s"]
        leave = moving + accumulated if moving is not None else None
        event.update(arrival_elapsed_s=arrival, departure_elapsed_s=leave,
                     arrival_time=None, departure_time=None,
                     location_status="verified" if geometry_known else "unverified")
        if departure is not None and arrival is not None and leave is not None:
            try:
                event["arrival_time"] = (departure + timedelta(seconds=arrival)).isoformat()
                event["departure_time"] = (departure + timedelta(seconds=leave)).isoformat()
            except OverflowError:
                timestamp_overflow = True
    if unlocated_supplies:
        missing.append("Some supply encounter locations are missing or invalid; they have no schedule estimate.")
    if timestamp_overflow:
        missing.append("Some estimated timestamps exceed the supported datetime range.")
    return {
        "status": "estimated" if known else "unknown",
        "method": "uniform_distance_based_native_duration",
        "source": {"duration": duration_source, "distance": "coordinate_haversine"},
        "duration_basis": duration_basis,
        "moving_s": duration if known else None,
        "pause_s": accumulated,
        "total_s": duration + accumulated if known and duration is not None else None,
        "departure_time": departure.isoformat() if departure is not None else None,
        "events": events,
        "unlocated_supplies": unlocated_supplies,
        "pause_locations_verified": geometry_known,
        "missing_data": missing,
        "limits": [
            "Uniform distance-based pace is an estimate; it does not model climbs or terrain.",
            "Supply stops include no off-route travel time or opening-hours check.",
            "Only the supplied pauses add stop time; supplies do not add automatic pauses.",
        ] + timing_limits,
    }
