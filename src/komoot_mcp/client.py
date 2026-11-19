"""Thin async wrapper around the ``kompy`` Komoot SDK.

NOTE — RELIANCE ON KOMPY ATTRIBUTES:
``Tour.__init__`` eagerly populates ``tour.summary`` (TourSummary),
``tour.tour_information`` (List[TourInformation]), ``tour.segments``
(List[Segment]) and ``tour.path`` (List[Waypoint]) from the underlying
API response when the corresponding keys are present. We read those
populated attributes directly rather than re-calling the private
``Tour._create_*`` static methods — those are staticmethods that take
the *raw dict slices* (e.g. ``tour['summary']``), which we don't have
once kompy has constructed the Tour object. Calling them as instance
methods (the previous shape) raised
``missing 1 required positional argument: 'tour_summary'`` /
``'tour_information_array'``. We still pin ``kompy<0.1.0`` in
``pyproject.toml`` for stability.

NOTE — ASYNC SHAPE:
All API methods are coroutines. They wrap synchronous kompy calls in
``asyncio.to_thread`` so the event loop is never blocked by blocking
HTTP. Rate limiting is awaited prior to each call.
"""
from __future__ import annotations

import asyncio
import math

import kompy
from komoot_mcp import secure_http as requests


def _iso_date(value, end_of_day=False):
    if value is None or "T" in value:
        return value
    suffix = "T23:59:59.999Z" if end_of_day else "T00:00:00.000Z"
    return value + suffix


class KomootAPIError(Exception):
    def __init__(self, message, status_code=None, body="", *, tour_id=None,
                 confirmed_changes=None, operation_state=None, code=None, place_id=None,
                 collection_id=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.tour_id = tour_id
        self.confirmed_changes = confirmed_changes
        self.operation_state = operation_state
        self.code = code
        self.place_id = place_id
        self.collection_id = collection_id

class KomootClient:
    def __init__(self, auth_manager, rate_limiter):
        self.auth = auth_manager
        self.rl = rate_limiter
        self._api = None

    def _get_api(self):
        """Lazily create the kompy connector with stored credentials."""
        if self._api is None:
            email = self.auth.email
            password = self.auth.password
            if not email or not password:
                raise KomootAPIError(
                    "KOMOOT_EMAIL and KOMOOT_PASSWORD must be set"
                )
            self._api = kompy.KomootConnector(email, password)
        return self._api

    async def _call(self, fn, *args, **kwargs):
        await self.rl.acquire()
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                raise KomootAPIError(
                    "Authentication failed. Check your credentials.",
                    status_code=401 if "401" in msg else 403,
                ) from e
            if "429" in msg:
                raise KomootAPIError(
                    "Rate limited by Komoot. Try again later.", status_code=429,
                ) from e
            if "404" in msg:
                raise KomootAPIError(
                    "Resource not found. Check the ID.", status_code=404,
                ) from e
            raise KomootAPIError(f"Komoot API error: {msg}")

    async def get_user_profile(self, user_id=None):
        if user_id is not None:
            return await self._komoot_get(
                f"https://api.komoot.de/v007/users/{user_id}"
            )
        # KomootConnector.__init__ already performs the login HTTP call
        # and populates ``self.authentication`` with the token + username
        # (see kompy.komoot_connector). The previous code constructed a
        # second ``kompy.Authentication`` directly, which has no ``.login``
        # method, so calling ``.get_username()`` raised "No username set,
        # please login first." The earlier silent ``except`` masked that
        # bug; with the swallow removed we now correctly read the username
        # off the already-authenticated connector instead.
        #
        # NOTE on the "username" field: kompy's login response stores the
        # numeric Komoot user ID under the ``username`` key, and there is
        # no public-API display name field. ``get_username`` therefore
        # returns the user ID as a string (or the placeholder "unknown"
        # if Komoot ever omits it). We expose a derived ``display_name``
        # that falls back to email, then user_id, so callers always have
        # something useful to render.
        api = self._get_api()
        username = await asyncio.to_thread(api.authentication.get_username)
        email = await asyncio.to_thread(api.authentication.get_email_address)
        # In kompy, get_username() is the user ID. Keep both names for
        # clarity and back-compat with any caller still reading "username".
        user_id = username
        if not username or username == "unknown":
            display_name = email or (str(user_id) if user_id else "unknown")
        else:
            display_name = username
        return {
            "display_name": display_name,
            "username": username,
            "user_id": user_id,
            "email": email,
        }

    async def list_tours(
        self, page=0, limit=50, sport_type=None, status=None,
        start_date=None, end_date=None, name=None, sort_field="date",
        sort_direction="desc", tour_type=None, center=None, max_distance=None,
    ):
        """List native tour records, including empty HAL result pages."""
        from komoot_mcp.routing import validate_coordinates

        if center is not None:
            center = validate_coordinates([center])[0]
        if max_distance is not None:
            if (center is None or isinstance(max_distance, bool)
                    or not isinstance(max_distance, (int, float))
                    or not math.isfinite(max_distance) or max_distance <= 0):
                raise ValueError("max_distance needs center and a finite positive distance in metres")
        if sort_field == "proximity" and center is None:
            raise ValueError("proximity sorting needs center")
        user_id, _ = await asyncio.to_thread(self._basic_auth)
        params = {
            "page": page, "limit": limit, "sport_types": sport_type,
            "type": tour_type, "name": name, "sort_field": sort_field,
            "sort_direction": sort_direction,
            "center": ",".join(str(value) for value in center) if center is not None else None,
            "max_distance": max_distance,
            "start_date": _iso_date(start_date),
            "end_date": _iso_date(end_date, end_of_day=True),
        }
        result = await self._komoot_get(
            f"https://api.komoot.de/v007/users/{user_id}/tours/",
            params={k: v for k, v in params.items() if v is not None},
        )
        tours = result.get("_embedded", {}).get("tours", [])
        if status:
            tours = [tour for tour in tours if tour.get("status") == status]
        return {
            "tours": tours, "page": result.get("page", {}),
            "total": len(tours),
        }


    async def get_tour(self, tour_id, share_token=None):
        return await self._komoot_get(
            f"https://api.komoot.de/v007/tours/{int(tour_id)}",
            params={"share_token": share_token} if share_token else None,
            no_auth=bool(share_token),
        )

    async def get_tour_coordinates(self, tour_id):
        """Read coordinates from a constructed endpoint, never an API-provided URL."""
        data = await self._komoot_get(
            f"https://api.komoot.de/v007/tours/{int(tour_id)}/coordinates",
            params={"format": "coordinate_array"},
        )
        return data.get("items", []) if isinstance(data, dict) else data

    async def get_tour_gpx(self, tour_id):
        return await self._http_request(
            "GET", f"https://api.komoot.de/v007/tours/{int(tour_id)}.gpx",
            expect_json=False,
        )

    async def get_tour_directions(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # ``Tour.__init__`` already populates ``self.segments`` via
            # ``_create_list_segments`` when the API response has a
            # 'segments' key — read it directly. Calling the static
            # ``_create_list_segments`` as a bound method previously
            # raised ``missing 1 required positional argument``.
            segments = getattr(tour, "segments", None) or []
            return [self._segment_to_dict(s) for s in segments]
        return []

    @staticmethod
    def _segment_to_dict(segment):
        boundaries = getattr(segment, "segment_boundaries", None)
        return {
            "type": getattr(segment, "segment_type", None),
            "reference": getattr(segment, "reference", None),
            "from": getattr(boundaries, "start_index_point", None) if boundaries else None,
            "to": getattr(boundaries, "end_index_point", None) if boundaries else None,
        }

    async def get_tour_way_types(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # Way-type breakdown lives on ``tour.summary.way_types``
            # (List[kompy.way_type.WayType]) — each carries ``.type`` and
            # ``.amount``. The previous code returned ``tour.path`` (a
            # list of ``Waypoint`` objects with no ``__repr__``), so the
            # MCP tool surfaced raw ``<...Waypoint object at 0x...>``
            # strings. Mirror ``get_tour_surfaces``'s serializer shape:
            # emit plain dicts the tool layer can render.
            #
            # NOTE: kompy's WayType constructor takes ``way_type=...`` but
            # stores it as ``self.type``. We accept both attribute names
            # to stay compatible with the MagicMock-based tests, which
            # set ``way_type=...`` directly on the mock.
            summary = getattr(tour, "summary", None)
            if summary is None:
                return []
            return [self._way_type_to_dict(w)
                    for w in (getattr(summary, "way_types", None) or [])]
        return []

    @staticmethod
    def _way_type_to_dict(w):
        """Serialize a ``kompy.way_type.WayType`` to ``{way_type, fraction}``.

        kompy stores the readable type string under ``.type`` (its
        constructor kwarg is ``way_type`` but the attribute is renamed).
        We fall back to ``.way_type`` for test mocks that mirror the
        kwarg name.
        """
        return {
            "way_type": KomootClient._way_type_name(w),
            "fraction": getattr(w, "amount", None),
        }

    @staticmethod
    def _surface_name(s):
        """Read the readable surface name off a ``kompy.surface.Surface``.

        Same kompy quirk as ``WayType``: the constructor kwarg is
        ``surface_type`` but the attribute is renamed to ``.type``.
        We prefer ``.type`` and fall back to ``.surface_type`` for mocks
        that mirror the kwarg name.
        """
        name = getattr(s, "type", None)
        if name is None:
            name = getattr(s, "surface_type", None)
        return name

    @staticmethod
    def _way_type_name(w):
        """Read the readable way-type name off a ``kompy.way_type.WayType``.

        Mirrors ``_surface_name`` — kompy stores it under ``.type``;
        mocks may use ``.way_type``.
        """
        name = getattr(w, "type", None)
        if name is None:
            name = getattr(w, "way_type", None)
        return name

    async def get_tour_surfaces(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # ``Tour.__init__`` populates ``self.tour_information``
            # (List[TourInformation]) via ``_create_tour_information``
            # when the API response has 'tour_information' — read it
            # directly. Returning a list (was {}) since the underlying
            # attribute is a list of TourInformation objects.
            tour_info = getattr(tour, "tour_information", None) or []
            return [
                {
                    "type": getattr(ti, "tour_information_type", None),
                    "segments": [
                        {
                            "from": getattr(s, "start_index_point", None),
                            "to": getattr(s, "end_index_point", None),
                        }
                        for s in (getattr(ti, "segments", None) or [])
                    ],
                }
                for ti in tour_info
            ]
        return []

    async def get_tour_timeline(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # ``Tour.__init__`` populates ``self.summary`` (TourSummary)
            # via ``_create_tour_summary`` when the API response has a
            # 'summary' key — read it directly. Previously we called the
            # static ``_create_tour_summary`` with no args, which raised
            # ``missing 1 required positional argument: 'tour_summary'``.
            #
            # The kompy ``TourSummary`` carries two lists (surfaces +
            # way_types). Flatten them into a single list of dicts the
            # existing data_tools renderer can iterate over — preserves
            # the public tool signature (still returns a list-like).
            summary = getattr(tour, "summary", None)
            if summary is None:
                return []
            events = []
            for s in getattr(summary, "surfaces", None) or []:
                # Real kompy stores the readable name under ``.type``
                # (constructor kwarg is ``surface_type``). Reading the
                # kwarg name directly is what produced ``? (0)`` rows in
                # production — match the ``_way_type_to_dict`` precedent
                # and prefer ``.type``, fall back for mocks.
                name = self._surface_name(s)
                events.append({
                    "type": "surface",
                    "description": f"{name if name is not None else '?'} "
                                   f"({getattr(s, 'amount', 0)})",
                })
            for w in getattr(summary, "way_types", None) or []:
                name = self._way_type_name(w)
                events.append({
                    "type": "way_type",
                    "description": f"{name if name is not None else '?'} "
                                   f"({getattr(w, 'amount', 0)})",
                })
            return events
        return []

    async def upload_tour(
        self, filepath=None, data_type=None, sport="touringbicycle",
        gpx_content=None, tour_name=None, status="private",
    ):
        """Upload an activity with its real ID and duplicate indication.

        GPX content takes precedence over filepath. Unlike the kompy boolean
        wrapper, this preserves HTTP errors and supports binary FIT/TCX input.
        For planned routes use import_gpx_route then create_planned_tour.
        """
        from pathlib import Path
        import gpxpy

        if status not in ("private", "friends", "public"):
            raise ValueError("status must be private, friends, or public")
        if gpx_content is not None:
            data_type = data_type or "gpx"
            if data_type != "gpx":
                raise KomootAPIError("gpx_content supports GPX only; use filepath for FIT/TCX")
            parsed = gpxpy.parse(gpx_content)
            body = gpx_content.encode("utf-8")
            name = tour_name or self._extract_gpx_name(parsed) or "tour"
        elif filepath is not None:
            from komoot_mcp.context import require_local_filesystem
            require_local_filesystem()
            path = Path(filepath).expanduser()
            if not path.is_file():
                raise KomootAPIError(f"File not found: {filepath}; alternatively supply gpx_content")
            data_type = data_type or path.suffix.lower().lstrip(".")
            body = await asyncio.to_thread(path.read_bytes)
            name = tour_name or path.stem
            if data_type == "gpx":
                gpxpy.parse(body)
        else:
            raise KomootAPIError("Supply gpx_content or filepath")
        if data_type not in ("gpx", "fit", "tcx"):
            raise KomootAPIError(f"Unsupported data_type: {data_type}")
        code, result = await self._http_request(
            "POST", "https://api.komoot.de/v007/tours/",
            params={"data_type": data_type, "sport": sport, "name": name, "status": status},
            data=body, headers={"Content-Type": "application/octet-stream"},
            with_status=True,
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise KomootAPIError("Upload response did not identify a tour; do not blindly retry")
        return {**result, "duplicate": code == 202, "created": code == 201,
                "import_status": "already_exists" if code == 202 else ("created" if code == 201 else "accepted"),
                "http_status": code}

    @staticmethod
    def _extract_gpx_name(gpx_obj):
        """Best-effort: pull the first track's name from a parsed GPX.

        gpxpy.gpx.GPX exposes a top-level ``name`` and a list of tracks
        each with their own ``name``. We prefer the GPX-level name, fall
        back to the first track. Returns None if neither is present.
        """
        try:
            top = getattr(gpx_obj, "name", None)
            if top:
                return top
            tracks = getattr(gpx_obj, "tracks", None) or []
            for t in tracks:
                tn = getattr(t, "name", None)
                if tn:
                    return tn
        except Exception:
            pass
        return None

    async def upload_gpx_capture_id(
        self,
        gpx_content,
        sport="touringbicycle",
        tour_name=None,
        tour_type="tour_planned",
    ):
        """Upload a GPX directly to Komoot and return the new tour ID.

        kompy's ``upload_tour`` returns only a bool — the Komoot API
        actually responds with ``{"id": <numeric_id>, ...}`` on success
        (HTTP 201) or duplicate (HTTP 202), but kompy throws it away.
        For ``komoot_plan_and_upload`` (issue #19) we need that ID to
        build a tour URL, so we POST to the same endpoint ourselves and
        capture the response body.

        Mirrors kompy's request shape (URL, auth, headers, params) so
        Komoot's server-side behaviour is unchanged, except for one
        extra knob: ``tour_type``. The Komoot ``/v007/tours/`` endpoint
        creates a ``tour_recorded`` activity by default ("I rode this
        today"), which is wrong when the user's intent is "save this
        planned route to Komoot". We pass ``type=tour_planned`` as a
        query param so the uploaded GPX lands under Planned Routes
        instead of Activities. ``tour_recorded`` remains a valid value
        for callers that genuinely uploaded recorded GPS data — they
        should use ``komoot_upload_tour`` instead, which keeps kompy's
        default ``tour_recorded`` behaviour.

        Note: the official Komoot v007 API docs only list ``sport``,
        ``time_in_motion`` and ``name`` for GPX upload; ``type`` is
        undocumented but matches the parameter Komoot's web frontend
        uses when importing a GPX as a planned tour, and matches the
        ``type`` filter on the tour-list GET endpoint.

        Returns ``{"id": <int>, "status": "uploaded"|"duplicate"}`` on
        201/202. Raises ``KomootAPIError`` on any other status code.
        """
        import gpxpy as _gpxpy

        # Parse + canonicalize GPX in memory. gpxpy round-tripping
        # normalizes whitespace/encoding, which Komoot tolerates fine.
        tour_obj = _gpxpy.parse(gpx_content)
        name = tour_name or self._extract_gpx_name(tour_obj) or "tour"

        # Use the same URL kompy uses. Hard-coded here on purpose: we
        # don't want to import a private constant from kompy that could
        # disappear in a future version.
        url = "https://api.komoot.de/v007/tours/?data_type=gpx"
        params = {
            "sport": sport,
            # Match kompy's default privacy status (FRIENDS) to avoid
            # surprising the user. Privacy override is a future tool
            # parameter (see issue #19).
            "status": "private",
            "data_type": "gpx",
            "name": name,
            "time_in_motion": None,
            # See docstring — drives whether Komoot files this under
            # Planned Routes or Activities.
            "type": tour_type,
        }
        headers = {"User-Agent": "komoot-mcp"}
        body = tour_obj.to_xml().encode("utf-8")

        await self.rl.acquire()

        def _post():
            return requests.post(
                url=url,
                auth=self._basic_auth(),
                headers=headers,
                params=params,
                data=body,
                timeout=30,
            )

        try:
            resp = await asyncio.to_thread(_post)
        except Exception as e:
            raise KomootAPIError(f"Komoot upload transport error: {e}")

        if resp.status_code in (201, 202):
            try:
                tour_id = resp.json().get("id")
            except ValueError:
                tour_id = None
            return {
                "id": tour_id,
                "status": "duplicate" if resp.status_code == 202 else "uploaded",
            }

        # Failure path — surface the real status code so the caller
        # knows whether to retry, fix the GPX, or fix credentials.
        snippet = (resp.text or "")[:300]
        raise KomootAPIError(
            f"Komoot rejected the upload (HTTP {resp.status_code}). "
            f"Common 400 cause: GPX is in route format (<rte>/<rtept>) "
            f"rather than track format (<trk>/<trkseg>/<trkpt>). "
            f"Response body (first 300 chars): {snippet}"
        )

    async def save_planned_tour(
        self,
        route_response,
        name,
        status="private",
    ):
        """Save a native planner result and verify the new planned tour.

        Uses the web planner's planned-field filter and embedded route geometry.
        Readback checks type, requested visibility, sport, fitness, and ordered
        waypoint references. A failed readback includes the saved ID and must
        not trigger an automatic retry.
        """
        url = "https://www.komoot.com/api/v007/tours/?hl=en"
        # Don't mutate the caller's dict — planner consumers might want
        # to inspect it after we return.
        if status not in ("private", "friends", "public"):
            raise ValueError("status must be private, friends, or public")
        from komoot_mcp.persistence import planned_payload

        save_body = planned_payload(route_response)
        save_body.pop("id", None)
        save_body["type"] = "tour_planned"
        save_body["status"] = status
        save_body["name"] = name
        from datetime import datetime, timezone
        save_body["date"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        save_body.setdefault("custom_tags", [])
        save_body.setdefault("_embedded", {})["save_options"] = {"origin": "route_planner"}

        auth_pair = await asyncio.to_thread(self._basic_auth)
        headers = {
            "Content-Type": "application/hal+json",
            "Accept": "application/hal+json",
            "User-Agent": "komoot-mcp",
        }

        await self.rl.acquire()

        def _post():
            return requests.post(
                url, auth=auth_pair, headers=headers, json=save_body,
                timeout=60,
            )

        try:
            resp = await asyncio.to_thread(_post)
        except Exception as e:
            raise KomootAPIError(
                f"Komoot save_planned_tour transport error: {e}"
            )

        if resp.status_code in (200, 201):
            try:
                body = resp.json()
            except ValueError:
                body = {}
            tour_id = body.get("id")
            if not tour_id:
                raise KomootAPIError("Saved tour response has no ID; do not blindly retry")
            verification = await self._verify_planned_tour(tour_id, save_body)
            return {
                "id": tour_id, "status": "saved",
                "url": f"https://www.komoot.com/tour/{tour_id}",
                "verification": verification,
                "verified_visibility": verification["observed"]["status"],
                "visibility_verified": True,
            }

        snippet = (resp.text or "")[:300]
        raise KomootAPIError(
            f"Komoot rejected save_planned_tour (HTTP {resp.status_code}). "
            f"Response body (first 300 chars): {snippet}"
        )

    async def _verify_planned_tour(self, tour_id, body, geometry=None, preserved=None):
        from komoot_mcp.persistence import (
            compare_readback, expected_readback, geometry_points,
        )

        requested = expected_readback(body)
        requested.update(preserved or {})
        requested["id"] = tour_id
        try:
            tour = await self.get_tour(tour_id)
            verification = compare_readback(requested, tour)
            if geometry is not None:
                verification["observed"]["date"] = tour.get("date")
                observed = geometry_points(await self.get_tour_coordinates(tour_id))
                # The coordinate endpoint can round geographic coordinates.
                matches = len(observed) == len(geometry) and all(
                    abs(a - b) <= 0.000001
                    for before, after in zip(geometry, observed)
                    for a, b in zip(before, after)
                )
                verification["requested"]["geometry_point_count"] = len(geometry)
                verification["observed"]["geometry_point_count"] = len(observed)
                verification["observed"]["geometry_matches"] = matches
                if not matches:
                    verification["mismatches"].append("geometry")
                verification["verified"] = not verification["mismatches"]
        except Exception as error:
            raise KomootAPIError(
                f"Tour {tour_id} write succeeded but readback failed: {error}; "
                "inspect this tour before retrying",
                tour_id=tour_id, confirmed_changes={"write_accepted": True},
                operation_state="applied",
            ) from error
        if not verification["verified"]:
            raise KomootAPIError(
                f"Tour {tour_id} write succeeded but readback differs for "
                + ", ".join(verification["mismatches"])
                + "; inspect this tour before retrying",
                tour_id=tour_id,
                confirmed_changes={key: value for key, value in verification["observed"].items()
                                   if key in requested and value == requested[key]},
                operation_state="applied",
            )
        return verification

    async def preview_tour_update(self, tour_id, route_response):
        """Compare an exact replacement with the current tour without writing."""
        from komoot_mcp.change_preview import preview_token, route_changes
        from komoot_mcp.persistence import planned_payload, validate_replacement

        if isinstance(tour_id, bool) or int(tour_id) <= 0:
            raise ValueError("tour_id must be positive")
        tour_id = int(tour_id)
        replacement = planned_payload(route_response)
        validate_replacement(replacement)
        current = await self.get_tour(tour_id)
        if current.get("type") != "tour_planned":
            raise ValueError("Only tour_planned routes can be replaced")
        coordinates = await self.get_tour_coordinates(tour_id)
        return {"tour_id": tour_id,
                "preview_token": preview_token(tour_id, current, coordinates, route_response),
                **route_changes(current, replacement)}

    async def update_planned_tour(self, tour_id, route_response, preview_token=None):
        """Replace an existing planned route with previewed geometry, without routing."""
        from komoot_mcp.persistence import planned_payload, validate_replacement

        if isinstance(tour_id, bool):
            raise ValueError("tour_id must be positive")
        tour_id = int(tour_id)
        if tour_id <= 0:
            raise ValueError("tour_id must be positive")
        body = planned_payload(route_response)
        geometry = validate_replacement(body)
        current = await self.get_tour(tour_id)
        if current.get("type") != "tour_planned":
            raise KomootAPIError("Only tour_planned routes can be replaced; no update sent")
        if current.get("status") not in {"private", "friends", "public"}:
            raise KomootAPIError("Tour visibility is missing or unsupported; no update sent")
        from komoot_mcp.change_preview import preview_token as make_token

        if not isinstance(preview_token, str) or not preview_token:
            raise ValueError("Preview this tour update first and supply preview_token; no update sent")
        coordinates = await self.get_tour_coordinates(tour_id)
        if preview_token != make_token(tour_id, current, coordinates, route_response):
            raise KomootAPIError(
                "Tour or replacement changed since preview; preview again; no update sent",
                tour_id=tour_id, confirmed_changes={}, operation_state="not_attempted",
                code="preview_conflict",
            )
        preserved = {
            key: current[key]
            for key in ("name", "status", "description", "date", "gear")
            if key in current
        }
        for key in ("name", "status", "description", "date", "gear"):
            body.pop(key, None)
        body.update(preserved)
        body.update({"id": tour_id, "type": "tour_planned"})
        # TourHal.save uses update:"put" with its planned _syncPostData body.
        # Shipped main.4aa1c01d52651411.js, offsets 1092250–1093070.
        try:
            await self._http_request(
                "PUT", f"https://api.komoot.de/v007/tours/{tour_id}",
                json_body=body, headers={"Content-Type": "application/hal+json"},
            )
        except KomootAPIError as error:
            raise KomootAPIError(
                f"Tour {tour_id} update outcome is not verified: {error}; "
                "inspect this tour before retrying",
                status_code=error.status_code, body=error.body,
                tour_id=tour_id, confirmed_changes={}, operation_state="unknown",
            ) from error
        verification = await self._verify_planned_tour(
            tour_id, body, geometry=geometry,
            preserved={key: value for key, value in preserved.items() if key != "date"},
        )
        # PUT can refresh the planning date. PATCH does not accept this field.
        metadata_changes = {
            "date": {"before": current.get("date"), "after": verification["observed"]["date"]},
        }
        verification["metadata_changes"] = metadata_changes
        return {
            "id": tour_id, "status": "updated",
            "url": f"https://www.komoot.com/tour/{tour_id}",
            "verification": verification,
            "metadata_changes": metadata_changes,
            "verified_visibility": verification["observed"]["status"],
            "visibility_verified": True,
        }

    async def modify_tour(self, tour_id, name=None, sport=None, status=None):
        api = self._get_api()
        return await self._call(
            api.change_tour,
            tour_id=int(tour_id),
            tour_name=name,
            activity_type=sport,
            status=status,
        )

    async def delete_tour(self, tour_id):
        api = self._get_api()
        return await self._call(api.delete_tour, tour_id=int(tour_id))

    # ----- Phase 2: direct REST helpers (bypass kompy) ------------------
    # The four endpoints below are not exposed by kompy. We hit them
    # directly with Basic auth, mirroring the pattern already used by
    # ``upload_gpx_capture_id`` — login via kompy (so we have a valid
    # ``api.authentication`` carrying email + token-as-password), then
    # POST/GET with ``requests`` and the same credentials.
    #
    # Host choice: ``api.komoot.de`` is the documented REST host that
    # accepts Basic auth (kompy itself uses it). The web app's
    # ``www.komoot.com/api/v007/...`` paths are equivalent — same
    # backend gateway — but Basic auth on ``api.komoot.de`` is the
    # known-working path we already rely on for uploads.

    def _basic_auth(self):
        """Return a ``(user_id, token)`` tuple usable as ``requests`` auth.

        kompy's ``Authentication`` stores the long-lived token under
        ``get_token()`` and the numeric user id under
        ``get_username()`` after login. That pair is the Basic-auth
        identity Komoot's REST API accepts (the literal email+password
        pair only works on the v006 ``/account/email/`` login endpoint).
        """
        api = self._get_api()
        return (
            api.authentication.get_username(),
            api.authentication.get_token(),
        )

    async def _http_get_json(self, url, params=None):
        """Authenticated GET that returns the parsed JSON body.

        Goes through the rate limiter and ``asyncio.to_thread`` so the
        event loop is not blocked. Raises ``KomootAPIError`` with a
        useful status code on any non-2xx, mirroring ``_call``'s
        error-message contract for 401/403/404/429 so the existing
        tool-layer ``except Exception`` blocks render the same friendly
        strings.
        """
        requests.validate_destination(url)
        auth_pair = await asyncio.to_thread(self._basic_auth)
        headers = {
            "User-Agent": "komoot-mcp",
            # Komoot's API returns HAL+JSON and rejects plain
            # application/json with HTTP 406 HttpMediaTypeNotAcceptable.
            "Accept": "application/hal+json, application/json",
        }

        def _do():
            return requests.get(
                url, auth=auth_pair, headers=headers, params=params, timeout=30,
            )

        await self.rl.acquire()
        try:
            resp = await asyncio.to_thread(_do)
        except Exception as e:
            raise KomootAPIError(f"Komoot transport error: {e}")

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                raise KomootAPIError(f"Komoot returned non-JSON: {e}")
        if resp.status_code in (401, 403):
            raise KomootAPIError(
                "Authentication failed. Check your credentials."
            )
        if resp.status_code == 404:
            raise KomootAPIError("Resource not found. Check the ID.")
        if resp.status_code == 429:
            raise KomootAPIError("Rate limited by Komoot. Try again later.")
        snippet = (resp.text or "")[:200]
        raise KomootAPIError(
            f"Komoot API error: HTTP {resp.status_code} — {snippet}"
        )

    async def get_tour_full(self, tour_id, include_coordinates=True):
        """Single-call full hydrate of a tour.

        Replaces 5+ chained kompy calls (coordinates + way_types +
        surfaces + directions + cover_images + timeline) with one
        embed-everything request. The response is HAL+JSON with all
        embeds populated under ``_embedded``.

        See ``/Users/marcodetering/komoot-endpoint-map.md`` — the URL
        below was live-tested at 200 OK via cookie session and works
        with the same Basic-auth identity kompy already uses.
        """
        url = f"https://api.komoot.de/v007/tours/{int(tour_id)}"
        params = {
            "_embedded": (
                "coordinates,way_types,surfaces,directions,participants,"
                "timeline,cover_images"
            ),
            "directions": "v2",
            "fields": "timeline",
            "format": "coordinate_array",
            "timeline_highlights_fields": "tips,recommenders",
        }
        if not include_coordinates:
            params["_embedded"] = params["_embedded"].replace("coordinates,", "")
        return await self._http_get_json(url, params=params)

    async def get_highlight(
        self, highlight_id, with_tips=False, with_recommenders=False,
    ):
        """Resolve a Komoot highlight (POI) by ID.

        Returns a dict with keys ``metadata`` (always), ``tips`` (only
        when ``with_tips=True``) and ``recommenders`` (only when
        ``with_recommenders=True``). Tour timeline entries reference
        highlights by ID — without this resolver those IDs are
        dead-ends.

        Endpoints (all live-tested 200 in the endpoint map):

        * ``GET /v007/highlights/{id}``
        * ``GET /v007/highlights/{id}/tips/?page=0``
        * ``GET /v007/highlights/{id}/recommenders/``
        """
        hid = int(highlight_id)
        base = f"https://api.komoot.de/v007/highlights/{hid}"
        out = {"metadata": await self._http_get_json(base)}
        if with_tips:
            out["tips"] = await self._http_get_json(
                f"{base}/tips/", params={"page": 0},
            )
        if with_recommenders:
            out["recommenders"] = await self._http_get_json(
                f"{base}/recommenders/",
            )
        return out

    async def get_tour_weather(self, tour_id):
        """Fetch the weather forecast along a tour.

        Hits Komoot's dedicated weather-along-tour service:
        ``GET https://weather-along-tour-api.komoot.de/v1/weather?tour_id={id}``

        The exact query-param shape is best-effort — the endpoint
        wasn't live-probed (the multi-tenant deployment doesn't have
        a shared probing account). We try ``tour_id`` first because
        that's the documented service name; if Komoot rejects with
        400/422 the tool layer surfaces the error verbatim so the
        caller can iterate.
        """
        url = "https://weather-along-tour-api.komoot.de/v1/weather"
        return await self._http_get_json(url, params={"tour_id": int(tour_id)})

    async def discover_near(self, lat, lng, sport=None, limit=10):
        """Discover tours / collections / smart tours near a point.

        Endpoint live-tested at 200:
        ``GET /v007/discover/{lat,lng}/elements/?page=0&_embedded=main_tour,summary``

        Returns the parsed JSON body. Items live under
        ``_embedded.items``. ``sport`` is forwarded as a filter when
        provided. ``limit`` is forwarded as ``limit`` though Komoot may
        clamp it server-side.
        """
        # Komoot's path is ``{lat,lng}`` literal — comma-separated in
        # the URL segment, not a JSON object.
        loc = f"{float(lat)},{float(lng)}"
        url = f"https://api.komoot.de/v007/discover/{loc}/elements/"
        params = {
            "page": 0,
            "limit": int(limit),
            "_embedded": "main_tour,summary",
        }
        if sport:
            params["sport"] = sport
        return await self._http_get_json(url, params=params)

    # ====================================================================
    # Phase 3 direct-REST helpers — extends the Phase 2 pattern.
    # ====================================================================
    # Phase 2 added ``_basic_auth`` + ``_http_get_json``. Phase 3 needs
    # POST / PATCH / DELETE on top of that. We factor the request loop
    # into ``_http_request`` which returns parsed JSON (or raw text for
    # DELETE-style 204s) and route all four verbs through it. ``_http_get_
    # json`` stays as-is — Phase 2 callers continue to work unchanged.

    async def _http_request(
        self, method, url, params=None, json_body=None, expect_json=True,
        no_auth=False, data=None, headers=None, raw=False, with_status=False,
    ):
        """Rate-limited HTTP request returning JSON, text, or raw bytes.

        HTTP errors preserve status_code and body on KomootAPIError.
        no_auth is used for public services and share-token requests.
        raw=True returns bytes without decoding binary FIT exports.
        """
        request_headers = {
            "User-Agent": "komoot-mcp",
            "Accept": "application/hal+json, application/json",
        }
        request_headers.update(headers or {})
        if method.upper() == "GET" and "/tours/" in url and not (params or {}).get("_embedded"):
            # Snapshot and write-readback callers must revalidate even fresh entries.
            request_headers["Cache-Control"] = "no-cache"
        requests.validate_destination(url, authenticated=not no_auth)
        auth_pair = None if no_auth else await asyncio.to_thread(self._basic_auth)

        def _do():
            return requests.request(
                method=method, url=url, params=params, json=json_body,
                auth=auth_pair, headers=request_headers, data=data, timeout=30,
            )

        await self.rl.acquire()
        try:
            resp = await asyncio.to_thread(_do)
        except Exception as e:
            raise KomootAPIError(f"Komoot transport error: {e}") from e

        if not resp.ok:
            if resp.status_code in (401, 403):
                message = "Authentication failed. Check your credentials."
            elif resp.status_code == 404:
                message = f"Not found: {url}"
            elif resp.status_code == 429:
                message = "Rate limited by Komoot. Try again later."
            else:
                message = f"Komoot API error: HTTP {resp.status_code} — {(resp.text or '')[:200]}"
            raise KomootAPIError(
                message, status_code=resp.status_code, body=resp.text or "",
            )

        if raw:
            result = resp.content
        elif not expect_json:
            result = resp.text or ""
        elif not resp.text:
            result = None
        else:
            try:
                result = resp.json()
            except ValueError as e:
                raise KomootAPIError(f"Komoot returned non-JSON ({url}): {e}") from e
        return (resp.status_code, result) if with_status else result

    async def _komoot_get(self, url, params=None, no_auth=False):
        return await self._http_request(
            "GET", url, params=params, no_auth=no_auth,
        )

    async def _komoot_post(self, url, json_body=None, params=None):
        return await self._http_request(
            "POST", url, params=params, json_body=json_body,
        )

    async def _komoot_patch(self, url, json_body=None, params=None):
        return await self._http_request(
            "PATCH", url, params=params, json_body=json_body,
        )

    async def _komoot_delete(self, url, params=None):
        return await self._http_request(
            "DELETE", url, params=params, expect_json=False,
        )

    # --- Tour metadata (Phase 3) -----------------------------------

    async def get_tour_photos(self, tour_id, page=0, limit=5):
        """``GET /v007/tours/{id}/cover_images/?page=N&limit=N``."""
        url = f"https://api.komoot.de/v007/tours/{int(tour_id)}/cover_images/"
        return await self._komoot_get(
            url, params={"page": int(page), "limit": int(limit)},
        )

    async def get_tour_line(self, tour_id):
        """``GET /api/v007/tours/{id}/tour_line`` — simplified line geometry.

        Live-probed 2026-05-18: the correct path is ``tour_line`` on
        ``www.komoot.com/api`` (not ``api.komoot.de/.../line``, which
        404s). Response body shape: ``{tour_id, geometry: [{lat,lng,alt},
        ...], tourID, _links}``.
        """
        url = (
            f"https://www.komoot.com/api/v007/tours/{int(tour_id)}/tour_line"
        )
        return await self._komoot_get(url)

    async def create_tour_share_link(self, tour_id):
        """``POST /v007/tours/{id}/share_token?format=v2&hl=en`` → 201."""
        url = f"https://api.komoot.de/v007/tours/{int(tour_id)}/share_token"
        return await self._komoot_post(
            url, json_body={}, params={"format": "v2", "hl": "en"},
        )

    async def revoke_tour_share_link(self, tour_id):
        """``DELETE /v007/tours/{id}/share_token``."""
        url = f"https://api.komoot.de/v007/tours/{int(tour_id)}/share_token"
        return await self._komoot_delete(url)

    async def modify_tour_extended(
        self, tour_id, description=None, gear=None,
        status=None, name=None, sport=None, speed_kmh=None,
        duration_s=None, time_in_motion_s=None, constitution=None,
    ):
        """Patch supplied fields and verify changes to timing and fitness."""
        for field, value in (("duration_s", duration_s), ("time_in_motion_s", time_in_motion_s)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{field} must be a positive integer")
        if constitution is not None and (
            isinstance(constitution, bool) or not isinstance(constitution, int)
            or not 1 <= constitution <= 5
        ):
            raise ValueError("constitution must be an integer between 1 and 5")
        if speed_kmh is not None:
            if (isinstance(speed_kmh, bool) or not isinstance(speed_kmh, (int, float))
                    or not math.isfinite(speed_kmh) or speed_kmh <= 0):
                raise ValueError("speed_kmh must be a finite positive number")
            if duration_s is not None or time_in_motion_s is not None:
                raise ValueError("Specify speed_kmh or explicit times, not both")
        if status is not None and status not in {"private", "friends", "public"}:
            raise ValueError("status must be private, friends, or public")
        url = f"https://api.komoot.de/v007/tours/{int(tour_id)}"
        body = {}
        if description is not None:
            body["description"] = description
        if gear is not None:
            body["gear"] = gear
        if status is not None:
            body["status"] = status
        if name is not None:
            body["name"] = name
        if sport is not None:
            body["sport"] = sport
        timing = {}
        if duration_s is not None:
            timing["duration"] = duration_s
        if time_in_motion_s is not None:
            timing["time_in_motion"] = time_in_motion_s
        if constitution is not None:
            timing["constitution"] = constitution
        if timing or speed_kmh is not None:
            current = await self._komoot_get(url)
            tour_type = current.get("type")
            if tour_type not in {"tour_planned", "tour_recorded"}:
                raise KomootAPIError("Tour type is missing or unsupported; no update sent")
            if constitution is not None and tour_type != "tour_planned":
                raise ValueError("constitution applies only to planned tours")
            if time_in_motion_s is not None and tour_type != "tour_recorded":
                raise ValueError("time_in_motion_s applies only to recorded tours")
            if speed_kmh is not None:
                distance = current.get("distance")
                if (isinstance(distance, bool) or not isinstance(distance, (int, float))
                        or not math.isfinite(distance) or distance <= 0):
                    raise KomootAPIError("Tour has no finite positive distance; no update sent")
                seconds = distance * 3.6 / speed_kmh
                if not math.isfinite(seconds):
                    raise ValueError("speed_kmh produces an unsupported duration")
                timing["duration" if tour_type == "tour_planned" else "time_in_motion"] = max(1, round(seconds))
            if tour_type == "tour_recorded":
                duration = timing.get("duration", current.get("duration"))
                moving = timing.get("time_in_motion", current.get("time_in_motion"))
                if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
                        or not math.isfinite(duration) or duration <= 0):
                    raise KomootAPIError("Recorded tour has no valid duration; no update sent")
                if moving is not None and moving > duration:
                    raise ValueError("time_in_motion_s must not exceed the total duration")
            body.update(timing)
        if not body:
            raise KomootAPIError("No fields to update")
        try:
            result = await self._komoot_patch(url, json_body=body)
        except Exception as error:
            raise KomootAPIError(
                f"Tour {tour_id} update outcome is unknown. Inspect the tour before retrying.",
                tour_id=int(tour_id), confirmed_changes={}, operation_state="unknown",
                status_code=getattr(error, "status_code", None),
            ) from error
        if timing:
            try:
                verified = await self._komoot_get(url)
            except Exception as error:
                raise KomootAPIError(
                    f"Tour {tour_id} update was accepted but readback failed. Inspect the tour before retrying.",
                    tour_id=int(tour_id), confirmed_changes={"write_accepted": True},
                    operation_state="applied",
                ) from error
            mismatches = [field for field, value in timing.items() if verified.get(field) != value]
            if mismatches:
                raise KomootAPIError(
                    "Tour update sent but readback differs for "
                    + ", ".join(mismatches) + "; inspect the tour before retrying",
                    tour_id=int(tour_id), operation_state="applied",
                    confirmed_changes={field: value for field, value in body.items()
                                       if verified.get(field) == value},
                )
            return verified
        return result

    # --- Highlights (Phase 3) --------------------------------------

    async def get_highlight_images(self, highlight_id, page=0):
        """``GET /v007/highlights/{id}/images/?page=N``."""
        url = (
            f"https://api.komoot.de/v007/highlights/{int(highlight_id)}/images/"
        )
        return await self._komoot_get(url, params={"page": int(page)})

    async def get_highlight_tips(self, highlight_id, page=0):
        """``GET /v007/highlights/{id}/tips/?page=N``."""
        url = (
            f"https://api.komoot.de/v007/highlights/{int(highlight_id)}/tips/"
        )
        return await self._komoot_get(url, params={"page": int(page)})

    async def list_user_highlights(self, user_id, page=0, limit=20):
        """``GET /v006/users/{user_id}/highlights/?page=N&limit=N``."""
        url = f"https://api.komoot.de/v006/users/{user_id}/highlights/"
        return await self._komoot_get(
            url, params={"page": int(page), "limit": int(limit)},
        )

    # --- Discover / Smart Tours (Phase 3) --------------------------

    async def smart_tours_near(self, lat, lng, sport, max_distance=20000):
        """Smart Tours near a point.

        Live-probed 2026-05-18: the correct path is
        ``www.komoot.com/api/v007/discover_tours/from_location/?lat&lng&sport``
        (the ``smarttour-api.main.komoot.net`` host 404s). Returns the
        standard HAL+JSON discover envelope with ``_embedded.items``.
        ``max_distance`` is forwarded in metres; Komoot may clamp it.
        """
        url = (
            "https://www.komoot.com/api/v007/discover_tours/from_location/"
        )
        params = {
            "lat": float(lat),
            "lng": float(lng),
            "sport": sport,
            "max_distance": int(max_distance),
        }
        return await self._komoot_get(url, params=params)

    async def smart_tour_for_highlight(
        self, highlight_id, lat, lng, sport=None,
    ):
        """Smart tours that pass through a highlight.

        Live-probed 2026-05-18: there is no ``for_highlight`` path —
        Komoot exposes this via the same ``from_location`` endpoint with
        a ``highlight_id`` query param:
        ``www.komoot.com/api/v007/discover_tours/from_location/?lat&lng&sport&highlight_id={id}``.
        Returns the standard discover HAL+JSON envelope.
        """
        url = (
            "https://www.komoot.com/api/v007/discover_tours/from_location/"
        )
        params = {
            "lat": float(lat),
            "lng": float(lng),
            "highlight_id": int(highlight_id),
        }
        if sport:
            params["sport"] = sport
        return await self._komoot_get(url, params=params)

    async def discover_with_attributes(
        self, lat, lng, sport=None, attributes=None, max_distance=20000,
    ):
        """Read tours with route_attributes filters, not the filter catalog."""
        url = "https://www.komoot.com/api/v007/discover_tours/from_location/"
        params = {"lat": float(lat), "lng": float(lng), "max_distance": int(max_distance)}
        if sport:
            params["sport"] = sport
        if attributes:
            params["route_attributes"] = ",".join(attributes) if isinstance(attributes, (list, tuple)) else attributes
        return await self._komoot_get(url, params=params)

    async def route_attribute_options(
        self, lat, lng, sport, max_distance=20000,
    ):
        """Legal route-attribute names accepted by discovery.

        Live-probed 2026-05-18: the endpoint
        ``www.komoot.com/api/v007/discover_tours/route_attributes/``
        REQUIRES all four query params — ``lat``, ``lng``, ``sport``,
        ``max_distance``. Anything missing returns HTTP 400
        ``MissingServletRequestParameter``. Response shape:
        ``{route_attributes: [<name>, ...]}``.
        """
        url = (
            "https://www.komoot.com/api/v007/discover_tours/"
            "route_attributes/"
        )
        params = {
            "lat": float(lat),
            "lng": float(lng),
            "sport": sport,
            "max_distance": int(max_distance),
        }
        return await self._komoot_get(url, params=params)

    # --- Collections (Phase 3) -------------------------------------

    async def get_collection(self, collection_id):
        """``GET /v007/collections/{id}``."""
        url = f"https://api.komoot.de/v007/collections/{int(collection_id)}"
        return await self._komoot_get(url)

    async def get_collection_tours(self, collection_id, page=0):
        """``GET /v007/collections/{id}/compilation/``."""
        url = (
            f"https://api.komoot.de/v007/collections/"
            f"{int(collection_id)}/compilation/"
        )
        return await self._komoot_get(url, params={"page": int(page)})

    # --- Resolvers (Phase 3) ---------------------------------------

    async def resolve_share_url(self, share_url):
        """Parse a share URL and fetch the underlying tour.

        Accepts URLs of the shape
        ``https://www.komoot.com/tour/{tour_id}?share_token={t}``. If
        ``share_token`` is present we authenticate via the query param
        (no Basic auth — the share token is the cap). Otherwise we hit
        the normal authenticated endpoint.
        """
        import re
        from urllib.parse import urlparse, parse_qs

        m = re.search(r"/tour/(\d+)", share_url)
        if not m:
            raise KomootAPIError(
                f"Could not extract tour id from share URL: {share_url}"
            )
        tour_id = int(m.group(1))
        parsed = urlparse(share_url)
        qs = parse_qs(parsed.query)
        share_token = qs.get("share_token", [None])[0]

        url = f"https://api.komoot.de/v007/tours/{tour_id}"
        params = {}
        if share_token:
            params["share_token"] = share_token
            data = await self._komoot_get(url, params=params, no_auth=True)
        else:
            data = await self._komoot_get(url, params=params)
        return {"tour_id": tour_id, "share_token": share_token, "tour": data}

