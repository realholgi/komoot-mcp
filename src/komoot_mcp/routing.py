import asyncio
import math
from numbers import Real

import requests

from komoot_mcp import secure_http


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


async def plan_native_route(client, coordinates, sport="hike", constitution=3, roundtrip=False):
    """Validate, authenticate off-thread, rate-limit, and plan off-thread."""
    profile = native_sport(sport)
    points = validate_coordinates(coordinates)
    if len(points) < 2:
        raise ValueError("Provide at least two [latitude, longitude] points")
    if isinstance(constitution, bool) or not isinstance(constitution, int) or not 1 <= constitution <= 5:
        raise ValueError("constitution must be an integer between 1 and 5")
    if roundtrip and points[-1] != points[0]:
        points.append(points[0].copy())
    auth = await asyncio.to_thread(client._basic_auth)
    await client.rl.acquire()
    return await asyncio.to_thread(
        KomootNativePlanner(auth).plan, points, profile, constitution,
    )


def saved_plan_result(saved, route, requested_visibility):
    """Report save data without claiming a visibility readback took place."""
    tour_id = saved.get("id")
    return {
        "tour_id": tour_id,
        "tour_url": f"https://www.komoot.com/tour/{tour_id}" if tour_id is not None else None,
        "distance_m": route.get("distance"),
        "duration_s": route.get("duration"),
        "elevation_up_m": route.get("elevation_up"),
        "requested_visibility": requested_visibility,
        "verified_visibility": None,
        "visibility_verified": False,
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

    def plan(self, waypoints, sport_komoot, constitution=3):
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
        path = [
            {"location": {"lat": float(lat), "lng": float(lon)}}
            for lat, lon in waypoints
        ]
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
            return resp.json()
        except ValueError as e:
            raise RoutingError(f"Komoot planner returned non-JSON: {e}")
