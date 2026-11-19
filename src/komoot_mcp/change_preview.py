"""Read-only route comparisons and snapshot fingerprints."""

import hashlib
import json
import math


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def tour_snapshot(tour, coordinates):
    # Links and embedded resources can change independently of editable tour state.
    return {"tour": {key: value for key, value in tour.items() if key not in {"_links", "_embedded"}},
            "coordinates": coordinates}


def preview_token(tour_id, tour, coordinates, route):
    from komoot_mcp.persistence import planned_payload

    return fingerprint({"tour_id": tour_id, "before": tour_snapshot(tour, coordinates),
                        "replacement": planned_payload(route)})


def route_changes(before, after):
    metrics = {}
    for field, unit in (("distance", "m"), ("elevation_up", "m"), ("elevation_down", "m"), ("duration", "s")):
        old, new = before.get(field), after.get(field)
        valid = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in (old, new))
        metrics[f"{field}_{unit}"] = {"before": old, "after": new, "delta": new - old if valid else None}
    def waypoints(route):
        return [{key: point.get(key) for key in ("reference", "name", "description", "location")}
                for point in route.get("path", [])]
    old, new = waypoints(before), waypoints(after)
    return {"metrics": metrics, "pois": {"before": old, "after": new,
            "changed": [{"position": i, "before": old[i] if i < len(old) else None,
                         "after": new[i] if i < len(new) else None}
                        for i in range(max(len(old), len(new)))
                        if (old[i] if i < len(old) else None) != (new[i] if i < len(new) else None)]}}
