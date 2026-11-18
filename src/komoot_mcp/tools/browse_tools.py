"""Browse and search tools for komoot-mcp."""


from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_list_tours(
        page: int = 0,
        limit: int = 50,
        sport_type: str = None,
        status: str = None,
        name: str = None,
        sort_field: str = "date",
        sort_direction: str = "desc",
        tour_type: str = None,
        start_date: str = None,
        end_date: str = None,
        compact: bool = True,
    ) -> dict:
        """List your Komoot tours with filters.

        Args:
            page: Page number (0-indexed)
            limit: Results per page (max 50)
            sport_type: Filter by sport (e.g. 'hike', 'touringbicycle', 'mountainbike', 'racebike', 'run')
            status: Filter by visibility ('public', 'private', 'friends')
            name: Search by tour name (case-insensitive substring)
            sort_field: Sort by ('date', 'name', 'elevation', 'duration')
            sort_direction: Sort order ('asc' or 'desc')
            tour_type: 'tour_planned', 'tour_recorded', or all tours.
            start_date: Inclusive ISO date or timestamp lower bound.
            end_date: Inclusive ISO date or timestamp upper bound.
            compact: False returns full tour records and pagination as JSON.
        """
        result = await get_client().list_tours(
            page=page, limit=limit, sport_type=sport_type, status=status, name=name,
            sort_field=sort_field, sort_direction=sort_direction,
            tour_type=tour_type, start_date=start_date, end_date=end_date,
        )
        if not compact:
            return result
        return {**{key: value for key, value in result.items() if key != "tours"},
                "tours": [{key: tour[key] for key in ("id", "name", "sport", "status", "distance", "elevation_up") if key in tour}
                          for tour in result.get("tours", [])]}

    @mcp.tool()
    async def komoot_get_tour(
        tour_id: int, share_token: str = None, compact: bool = True,
    ) -> dict:
        """Get full details of a specific Komoot tour by ID.

        Args:
            tour_id: The numeric tour ID
            share_token: Optional access token for a shared private tour.
            compact: False returns the entire native tour JSON, including path and description.
        """
        tour = await get_client().get_tour(tour_id, share_token=share_token)
        if not compact:
            return tour
        return {key: tour[key] for key in (
            "id", "name", "sport", "status", "distance", "elevation_up", "elevation_down",
            "duration", "date", "difficulty_grade", "difficulty_fitness", "difficulty_technical",
        ) if key in tour}

    @mcp.tool()
    async def komoot_get_tour_full(
        tour_id: int, include_coordinates: bool = True, compact: bool = True,
    ) -> dict:
        """Hydrate a tour in ONE call (replaces 5+ chained calls).

        Compact mode returns scalar metadata, start point, difficulty, the
        five largest surface and way-type records, embedded resource counts,
        and up to five highlight IDs and names. Full mode returns native data.

        Args:
            tour_id: The numeric tour ID
            include_coordinates: Include the full coordinate array.
            compact: False returns all requested embedded data as JSON.
        """
        data = await get_client().get_tour_full(tour_id, include_coordinates=include_coordinates)
        if not compact:
            return data
        summary = {key: data[key] for key in (
            "id", "name", "sport", "status", "date", "distance", "duration",
            "elevation_up", "elevation_down", "difficulty", "start_point",
        ) if key in data}
        embedded = data.get("_embedded") or {}
        for resource in ("coordinates", "directions", "timeline", "cover_images"):
            block = embedded.get(resource) or {}
            items = block.get("items") if isinstance(block, dict) else None
            if isinstance(items, list):
                summary[f"{resource}_count"] = len(items)
        for resource in ("way_types", "surfaces"):
            block = embedded.get(resource) or {}
            items = block.get("items") if isinstance(block, dict) else None
            if isinstance(items, list):
                summary[resource] = sorted(
                    (item for item in items if isinstance(item, dict)),
                    key=lambda item: item.get("amount", 0), reverse=True,
                )[:5]
        timeline = (embedded.get("timeline") or {}).get("items") or []
        highlights = []
        for entry in timeline:
            if not isinstance(entry, dict):
                continue
            reference = (entry.get("_embedded") or {}).get("reference") or entry.get("reference")
            if isinstance(reference, dict) and reference.get("id") is not None:
                highlights.append({key: reference[key] for key in ("id", "name") if key in reference})
        summary["highlights"] = highlights[:5]
        return summary

    @mcp.tool()
    async def komoot_get_highlight(
        highlight_id: int,
        include_tips: bool = False,
        include_recommenders: bool = False,
    ) -> dict:
        """Resolve a Komoot highlight (POI) by ID.

        Tour timelines reference highlight IDs. Without this resolver
        those IDs are dead-ends. Returns metadata (name, category,
        sport, score, location); optionally also community tips and a
        recommender count.

        Args:
            highlight_id: The numeric highlight ID
            include_tips: Fetch the community tips list (extra HTTP call)
            include_recommenders: Fetch the recommenders list (extra HTTP call)
        """
        data = await get_client().get_highlight(
            highlight_id, with_tips=include_tips, with_recommenders=include_recommenders,
        )
        return data

    @mcp.tool()
    async def komoot_tour_weather(tour_id: int) -> dict:
        """Weather forecast along a planned tour (EXPERIMENTAL).

        Hits Komoot's dedicated weather-along-tour service at
        ``weather-along-tour-api.komoot.de/v1/weather?tour_id={id}``.

        The exact request signature wasn't live-probed for this
        multi-tenant deployment, so this tool ships as best-effort: if
        Komoot rejects the request shape, the error is surfaced
        verbatim so the caller can iterate. The most likely fix is
        passing the tour's start coordinates inline instead of an ID —
        a future revision may switch to that signature.

        Args:
            tour_id: The numeric tour ID (must be a tour you can read)
        """
        return await get_client().get_tour_weather(tour_id)

    @mcp.tool()
    async def komoot_get_user_profile(user_id: str = None, compact: bool = True) -> dict:
        """Get your profile, or another user's public profile by user_id.

        compact=False returns full profile JSON rather than a display summary.
        """
        client = get_client()
        profile = await client.get_user_profile(user_id) if user_id else await client.get_user_profile()
        if not compact:
            return profile
        return {key: profile[key] for key in ("display_name", "email", "user_id", "username") if key in profile}

    @mcp.tool()
    async def komoot_get_tour_photos(
        tour_id: int, page: int = 0, limit: int = 5,
    ) -> dict:
        """Get the cover/photo images attached to a tour.

        Returns the native image records and pagination, including URL templates.

        Example: ``komoot_get_tour_photos(tour_id=2614957086, page=0,
        limit=5)``.

        Args:
            tour_id: The numeric tour ID
            page: Page number (0-indexed)
            limit: Results per page
        """
        return await get_client().get_tour_photos(tour_id, page=page, limit=limit)

    @mcp.tool()
    async def komoot_get_tour_line(tour_id: int) -> dict:
        """Get a tour's simplified line geometry (lightweight alt to coordinates).

        Returns the complete native simplified geometry payload.
        Use ``komoot_get_tour_coordinates`` for unsimplified coordinates.

        Example: ``komoot_get_tour_line(tour_id=2614957086)``.

        Args:
            tour_id: The numeric tour ID
        """
        return await get_client().get_tour_line(tour_id)

    @mcp.tool()
    async def komoot_list_user_highlights(
        user_id: str, page: int = 0, limit: int = 20,
    ) -> dict:
        """List a user's saved highlights (POIs).

        Example: ``komoot_list_user_highlights(user_id="2069076024",
        page=0, limit=20)``.

        Args:
            user_id: The Komoot user_id (numeric, as string)
            page: Page number (0-indexed)
            limit: Max items per page
        """
        return await get_client().list_user_highlights(user_id, page=page, limit=limit)


