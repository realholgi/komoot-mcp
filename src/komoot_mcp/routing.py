import asyncio
import math
from numbers import Real
from typing import Any, Literal, get_args

import requests
from pydantic import BaseModel, ConfigDict, Field, model_validator

from komoot_mcp import secure_http
from komoot_mcp.route_summary import route_summary
from komoot_mcp.planning_insights import planning_insights, validate_waypoint_stays
from komoot_mcp.persistence import roundtrip_path


class RoutingError(Exception):
    pass


# Public sport identifiers → Komoot's native-planner sport vocabulary.
# The names diverge from kompy's upload activity strings.
# Komoot's own planner accepts these on the
# ``/api/routing/tour`` endpoint:
#
#   mtb, mountainbike, touringbicycle, racebike, e_racebike, e_mtb,
#   jogging, hike, nordicwalking, e_touringbicycle
#
# Komoot does not have a separate "gravel" profile — its tour planner
# folds gravel routing into ``touringbicycle`` (the all-rounder bike
# profile). We document that in the tool docstring.
NATIVE_SPORT_PROFILES = {
    "hike": "hike",
    "trail_run": "jogging",
    "mountain_bike": "mtb",
    "road_cycle": "racebike",
    "gravel_ride": "touringbicycle",
}


_NATIVE_SPORT_ALIASES = {
    "mtb", "mountainbike", "touringbicycle", "racebike", "e_racebike",
    "e_mtb", "jogging", "nordicwalking", "e_touringbicycle",
}


def native_sport(sport: str) -> str:
    """Resolve public sports or explicit native aliases; never guess a profile."""
    if not isinstance(sport, str):
        raise ValueError("sport must be a supported sport name")
    if sport in NATIVE_SPORT_PROFILES:
        return NATIVE_SPORT_PROFILES[sport]
    if sport in _NATIVE_SPORT_ALIASES:
        return sport
    raise ValueError(f"Unsupported sport: {sport}")


def validate_coordinates(coordinates):
    """Validate ordered latitude/longitude pairs without coercing strings."""
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        raise ValueError("Coordinates must be latitude/longitude pairs")
    points = []
    for point in coordinates:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Coordinates must be latitude/longitude pairs")
        lat, lng = point
        if any(isinstance(value, bool) or not isinstance(value, Real)
               or not math.isfinite(value) for value in point):
            raise ValueError("Coordinates must contain finite numbers")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("Coordinates must be valid latitude/longitude pairs")
        points.append([lat, lng])
    return points


RouteType = Literal["best", "quiet", "scenic", "shortest", "less_elevation", "technical"]
Avoidance = Literal["ferries", "steps", "steep_hills", "rough_terrain"]
_AVOIDANCE_TAGS = {
    "ferries": "no_ferry", "steps": "no_steps",
    "steep_hills": "no_steep", "rough_terrain": "no_poor",
}


def routing_tags(route_type="best", avoid=None):
    """Map public preferences to the web planner's custom tags."""
    if route_type not in get_args(RouteType):
        raise ValueError("Unsupported route_type")
    if avoid is not None and (
        not isinstance(avoid, list)
        or any(not isinstance(value, str) or value not in _AVOIDANCE_TAGS for value in avoid)
    ):
        raise ValueError("avoid must list ferries, steps, steep_hills, or rough_terrain")
    tags = [] if route_type == "best" else [route_type]
    for value in avoid or []:
        tag = _AVOIDANCE_TAGS[value]
        if tag not in tags:
            tags.append(tag)
    return tags


def validate_waypoint_names(waypoint_names, count) -> list[str | None]:
    """Keep optional names aligned with the caller's ordered input points."""
    if waypoint_names is None:
        return [None] * count
    if not isinstance(waypoint_names, (list, tuple)) or len(waypoint_names) != count:
        raise ValueError("waypoint_names must contain one name or null per input point")
    if any(name is not None and (not isinstance(name, str) or not name.strip())
           for name in waypoint_names):
        raise ValueError("waypoint_names entries must be nonblank strings or null")
    return list(waypoint_names)


class WaypointDetails(BaseModel):
    """A custom name or a reference to an existing Komoot highlight or map POI."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, strict=True)
    highlight_id: int | None = Field(default=None, gt=0, strict=True)
    poi_id: str | None = Field(default=None, strict=True, pattern=r"^[A-Za-z0-9]+$")
    highlight_type: Literal["point", "segment"] = "point"

    @model_validator(mode="after")
    def validate_identity(self):
        if self.name is not None and not self.name.strip():
            raise ValueError("Waypoint name must not be blank")
        if sum(value is not None for value in (self.name, self.highlight_id, self.poi_id)) > 1:
            raise ValueError("Use only one of name, highlight_id, or poi_id")
        if self.highlight_type != "point" and self.highlight_id is None:
            raise ValueError("highlight_type needs a highlight_id")
        return self


def waypoint_metadata(waypoint_names, waypoint_details, count):
    names = validate_waypoint_names(waypoint_names, count)
    if waypoint_details is None:
        details = [None] * count
    elif not isinstance(waypoint_details, list) or len(waypoint_details) != count:
        raise ValueError("waypoint_details must contain one object or null per input point")
    else:
        details = waypoint_details
    metadata = []
    for index, (name, detail) in enumerate(zip(names, details)):
        item = WaypointDetails.model_validate(detail) if detail is not None else WaypointDetails()
        if item.highlight_id is not None:
            if name is not None:
                raise ValueError("A highlight waypoint cannot also have a custom name")
            prefix = "hlp" if item.highlight_type == "point" else "hls"
            metadata.append({"reference": f"{prefix}:{item.highlight_id}"})
        elif item.poi_id is not None:
            if name is not None:
                raise ValueError("A POI waypoint cannot also have a custom name")
            metadata.append({"reference": f"poi:{item.poi_id}"})
        else:
            if name is not None and item.name is not None and name != item.name:
                raise ValueError("Conflicting waypoint names")
            resolved_name = item.name if item.name is not None else name
            metadata.append(
                {"name": resolved_name, "reference": f"cnw:{index + 1}"}
                if resolved_name is not None else {}
            )
    return metadata


async def plan_native_route(
    client, coordinates, sport="hike", constitution=3, roundtrip=False, waypoint_names=None,
    route_type="best", avoid=None, waypoint_details=None, waypoint_stays_s=None,
):
    """Validate, authenticate off-thread, rate-limit, and plan off-thread."""
    profile = native_sport(sport)
    points = validate_coordinates(coordinates)
    if len(points) < 2:
        raise ValueError("Provide at least two [latitude, longitude] points")
    if isinstance(constitution, bool) or not isinstance(constitution, int) or not 1 <= constitution <= 5:
        raise ValueError("constitution must be an integer between 1 and 5")
    names = validate_waypoint_names(waypoint_names, len(points))
    waypoint_metadata(names, waypoint_details, len(points))
    tags = routing_tags(route_type, avoid)
    stays = validate_waypoint_stays(waypoint_stays_s, len(points))
    if roundtrip and points[-1] != points[0]:
        points.append(points[0].copy())
        names.append(names[0])
        stays.append(0)
        if waypoint_details is not None:
            waypoint_details = [*waypoint_details, waypoint_details[0]]
    auth = await asyncio.to_thread(client._basic_auth)
    await client.rl.acquire()
    options: dict[str, Any] = {"waypoint_names": names} if any(name is not None for name in names) else {}
    if tags:
        options.update(route_type=route_type, avoid=avoid)
    if waypoint_details is not None:
        options["waypoint_details"] = waypoint_details
    route = await asyncio.to_thread(
        KomootNativePlanner(auth).plan, points, profile, constitution, **options,
    )
    route.setdefault("planning_summary", {}).update(planning_insights(route, points, stays))
    return route


async def compare_route_variants(
    client, coordinates, sport="hike", constitution=3, roundtrip=False,
    waypoint_names=None, avoid=None, waypoint_details=None, waypoint_stays_s=None,
):
    """Plan three preference variants without saving or guaranteeing preferences."""
    variants = {}
    for route_type in ("shortest", "scenic", "quiet"):
        route = await plan_native_route(
            client, coordinates, sport, constitution, roundtrip, waypoint_names,
            route_type, avoid, waypoint_details, waypoint_stays_s,
        )
        variants[route_type] = {"route": route}
    baseline = variants["shortest"]["route"]
    for variant in variants.values():
        route = variant["route"]
        deltas = {}
        for key in ("distance", "duration", "elevation_up", "elevation_down"):
            value, reference = route.get(key), baseline.get(key)
            valid = all(
                not isinstance(v, bool) and isinstance(v, Real) and math.isfinite(v) and float(v) >= 0
                for v in (value, reference)
            )
            deltas[key + ("_s" if key == "duration" else "_m")] = value - reference if valid else None
        variant["deltas_vs_shortest"] = deltas
    return {
        "baseline": "shortest", "variants": variants,
        "limitation": "These are requested routing preferences, not guarantees of shortest, scenic, or quiet conditions.",
    }


def saved_plan_result(saved, route, requested_visibility):
    """Report explicit readback evidence and the route's derived summary."""
    tour_id = saved.get("id")
    return {
        "tour_id": tour_id,
        "status": saved.get("status"),
        "tour_url": f"https://www.komoot.com/tour/{tour_id}" if tour_id is not None else None,
        "distance_m": route.get("distance"),
        "duration_s": route.get("duration"),
        "elevation_up_m": route.get("elevation_up"),
        "requested_visibility": requested_visibility,
        "verified_visibility": saved.get("verified_visibility") if saved.get("visibility_verified") is True else None,
        "visibility_verified": saved.get("visibility_verified") is True,
        "planning_summary": {
            **(route.get("planning_summary") or {}),
            **route_summary(
                route, constitution=(route.get("planning_summary") or {}).get("pace", {}).get("constitution"),
            ),
        },
        "verification": saved.get("verification"),
        "save_result": saved,
    }


class KomootNativePlanner:
    """Calls Komoot's own planner endpoint.

    Use ``POST /api/routing/tour`` to plan, then save the returned route
    blob to ``POST /api/v007/tours/?hl=en`` with ``type: tour_planned``
    inside the JSON body to create a planned tour.

    Auth: Basic ``(user_id, long_lived_token)`` — the same Basic pair
    that already authenticates ``upload_gpx_capture_id`` and all the
    other v007 calls. Confirmed working with a live probe (status 200
    on the planning call, 201 on the save call).
    """

    BASE = "https://www.komoot.com/api/routing/tour"
    # Embed the full route detail: coordinates (for surface/way overlays),
    # way_types + surfaces (for the per-segment composition), directions
    # (for turn-by-turn). These are what Komoot's own web frontend
    # requests, so the saved tour ends up indistinguishable from one
    # planned in the browser.
    _EMBED = "coordinates,way_types,surfaces,directions"

    def __init__(self, auth_pair):
        """``auth_pair``: ``(user_id, token)`` tuple for HTTP Basic auth.

        The token is the long-lived password-equivalent returned by
        Komoot's v006 login (see :class:`AuthManager.login`), NOT the
        user's actual password. ``KomootClient._basic_auth`` builds
        this tuple from the kompy connector.
        """
        self._auth = auth_pair

    def plan(
        self, waypoints, sport_komoot, constitution=3, waypoint_names=None,
        route_type="best", avoid=None, waypoint_details=None,
    ):
        """Plan a route through the supplied waypoints.

        Args:
            waypoints: List of ``(lat, lon)`` tuples — at least two
                (start + end). Intermediate points become routing
                via-points. Roundtrips: pass ``[start, start]`` plus
                whatever extra via-points you want.
            sport_komoot: One of the Komoot-vocabulary sport names
                (e.g. ``"mtb"``, ``"touringbicycle"``, ``"hike"``).
                Use :data:`NATIVE_SPORT_PROFILES` to map from our
                public vocab.
            constitution: Fitness self-rating 1-5; affects duration
                estimates but not the geometry. Default 3 (average).

        Returns the parsed route response dict — the same object you
        pass to :meth:`KomootClient.save_planned_tour` to commit it.
        Includes ``distance`` (m), ``duration`` (s), ``path``,
        ``segments``, ``elevation_up`` / ``elevation_down``, ``query``
        (the planner's own token), and an ``_embedded`` block with
        coordinates, way_types, surfaces, and directions.
        """
        if not waypoints or len(waypoints) < 2:
            raise RoutingError(
                "Komoot's native planner needs at least 2 waypoints "
                "(start + end). For a roundtrip pass the same point "
                "twice."
            )
        metadata = waypoint_metadata(waypoint_names, waypoint_details, len(waypoints))
        tags = routing_tags(route_type, avoid)
        path: list[dict[str, Any]] = [
            {"location": {"lat": float(lat), "lng": float(lon)}}
            for lat, lon in waypoints
        ]
        for point, fields in zip(path, metadata):
            point.update(fields)
        path = roundtrip_path(path)
        if path[-1].get("reference") == "special:back":
            metadata[-1] = {"reference": "special:back"}
        body = {
            "constitution": int(constitution),
            "sport": sport_komoot,
            "path": path,
            # Komoot requires len(segments) == len(path) - 1 — one
            # "Routed" segment per leg between waypoints. We're not
            # importing manually-drawn geometry, so each segment's
            # ``geometry`` stays empty and Komoot fills it in via its
            # own routing engine.
            "segments": [
                {"geometry": [], "type": "Routed"} for _ in range(len(path) - 1)
            ],
        }
        if tags:
            body["custom_tags"] = tags
        params = {"sport": sport_komoot, "_embedded": self._EMBED}
        try:
            resp = secure_http.post(
                self.BASE,
                params=params,
                auth=self._auth,
                json=body,
                headers={
                    "User-Agent": "komoot-mcp",
                    "Accept": "application/json",
                },
                timeout=60,
            )
        except requests.exceptions.Timeout as e:
            raise RoutingError(f"Komoot planner timed out: {e}")
        except requests.exceptions.RequestException as e:
            raise RoutingError(f"Komoot planner transport error: {e}")

        if resp.status_code != 200:
            snippet = (resp.text or "")[:400]
            raise RoutingError(
                f"Komoot planner request failed "
                f"(HTTP {resp.status_code}): {snippet}"
            )
        try:
            route = resp.json()
        except ValueError as e:
            raise RoutingError(f"Komoot planner returned non-JSON: {e}")
        if tags:
            # Keep requested preferences when the router omits them from its result.
            route["custom_tags"] = tags
        if any(metadata):
            routed_path = route.get("path") if isinstance(route, dict) else None
            if not isinstance(routed_path, list) or len(routed_path) != len(path):
                raise RoutingError("Komoot returned a different waypoint count; cannot preserve waypoint metadata")
            # Routing can snap locations. Use ordered path identity, never nearest coordinates.
            for point, fields in zip(routed_path, metadata):
                if fields:
                    if "name" not in fields and point.get("reference") != fields["reference"]:
                        point.pop("name", None)
                        point.pop("description", None)
                        point.pop("category", None)
                    point.update(fields)
        route["planning_summary"] = {
            **route_summary(route, constitution=constitution),
            **planning_insights(route, waypoints),
        }
        return route
