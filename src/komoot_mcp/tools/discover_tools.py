"""Discovery tools for komoot-mcp.

Phase 2 added ``komoot_recommend_tours_near`` (wraps the
``/v007/discover/{lat,lng}/elements/`` umbrella endpoint). Phase 3
extends with Smart Tour suggestions and attribute-filtered discovery.

All Phase 3 URLs in this module were live-probed 2026-05-18 against
``www.komoot.com/api/v007/`` (the previously-guessed
``smarttour-api.main.komoot.net`` and ``search-api.main.komoot.net``
hosts return 404 anonymously). The Komoot Smart Tour service is
exposed via the same ``discover_tours/from_location/`` endpoint with
slightly different param combinations:

* No filter → all smart tours near the point.
* ``highlight_id`` → tours that pass through that POI.
* Route attributes are listed by ``route_attribute_options`` (which
  itself needs the location + sport context, not a static catalog).

A standalone ``komoot_search`` tool and Trailview photo lookup were
also probed and dropped — Komoot exposes neither publicly.
"""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_recommend_tours_near(
        lat: float,
        lng: float,
        sport: str = None,
        radius_km: float = 20,
        limit: int = 10,
    ) -> dict:
        """Recommend tours, smart tours, and collections near a point.

        Wraps Komoot's discovery surface
        (``/v007/discover/{lat,lng}/elements/``). Returns up to ``limit``
        items, each with name, type, sport, distance, and a share URL
        when one is exposed.

        Args:
            lat: Latitude of the center point
            lng: Longitude of the center point
            sport: Optional sport filter (e.g. 'hike', 'touringbicycle',
                'mountainbike', 'racebike'). When omitted, all sports
                are returned.
            radius_km: Search radius (forwarded as a hint; Komoot may
                clamp or ignore). Default 20 km.
            limit: Max items to return (default 10).
        """
        return await get_client().discover_near(lat=lat, lng=lng, sport=sport, limit=limit)

    @mcp.tool()
    async def komoot_smart_tours_near(
        lat: float,
        lng: float,
        sport: str,
        max_distance: int = 20000,
    ) -> dict:
        """Recommend Smart Tours near a point.

        Hits ``www.komoot.com/api/v007/discover_tours/from_location/``
        (live-probed 2026-05-18). For a broader mix of items
        (collections + tours + smart tours) use
        ``komoot_recommend_tours_near`` instead — it wraps the umbrella
        ``/v007/discover/{lat,lng}/elements/`` endpoint. This tool
        returns Smart Tours only.

        Example: ``komoot_smart_tours_near(lat=47.9959, lng=7.8522,
        sport='mountainbike', max_distance=20000)`` for Freiburg.

        Args:
            lat: Latitude
            lng: Longitude
            sport: Sport profile (e.g. 'hike', 'touringbicycle',
                'mountainbike', 'racebike')
            max_distance: Search radius in metres (default 20000)
        """
        return await get_client().smart_tours_near(lat, lng, sport, max_distance=max_distance)

    @mcp.tool()
    async def komoot_smart_tour_for_highlight(
        highlight_id: int,
        lat: float,
        lng: float,
        sport: str = None,
    ) -> dict:
        """Suggested smart tours that pass through a highlight (POI).

        Komoot's API exposes this via the same ``from_location``
        endpoint as ``komoot_smart_tours_near``, with a ``highlight_id``
        query param (live-probed 2026-05-18 — there is no dedicated
        ``for_highlight`` path).

        Example: ``komoot_smart_tour_for_highlight(highlight_id=15829,
        lat=47.9959, lng=7.8522, sport='hike')``.

        Args:
            highlight_id: The numeric highlight ID
            lat: Latitude near the highlight (required by the endpoint)
            lng: Longitude near the highlight (required by the endpoint)
            sport: Optional sport filter
        """
        return await get_client().smart_tour_for_highlight(highlight_id, lat, lng, sport=sport)

    @mcp.tool()
    async def komoot_discover_with_attributes(
        lat: float,
        lng: float,
        sport: str = None,
        attributes: str = None,
    ) -> dict:
        """Discover tours near a point, filtered by route attributes.

        Route attributes are tags like ``waterfalls``, ``lakes_rivers``,
        ``mountain_summits``. Use ``komoot_route_attribute_options`` to
        enumerate the legal values for a given location and sport.

        Example: ``komoot_discover_with_attributes(lat=47.9959,
        lng=7.8522, sport='mountainbike', attributes='waterfalls')``.

        Args:
            lat: Latitude
            lng: Longitude
            sport: Optional sport filter
            attributes: Comma-separated attribute names (e.g.
                ``waterfalls,lakes_rivers``)
        """
        return await get_client().discover_with_attributes(lat, lng, sport=sport, attributes=attributes)

    @mcp.tool()
    async def komoot_route_attribute_options(
        lat: float,
        lng: float,
        sport: str,
        max_distance: int = 20000,
    ) -> dict:
        """List the legal route-attribute names accepted by discovery.

        Komoot's API requires ALL four parameters (lat, lng, sport,
        max_distance) — anything missing returns HTTP 400 (live-probed
        2026-05-18). Pass these through to
        ``komoot_discover_with_attributes`` once you've picked an
        attribute.

        Example: ``komoot_route_attribute_options(lat=47.9959,
        lng=7.8522, sport='mountainbike', max_distance=20000)``.

        Args:
            lat: Latitude near the area you're searching
            lng: Longitude near the area you're searching
            sport: Sport profile (e.g. 'mountainbike', 'hike')
            max_distance: Search radius in metres (default 20000)
        """
        return await get_client().route_attribute_options(lat, lng, sport, max_distance=max_distance)
