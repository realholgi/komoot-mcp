"""Read-only tour search with live route-attribute filters."""


from komoot_mcp.client import KomootAPIError
from komoot_mcp.context import get_client
from komoot_mcp.routing import native_sport, validate_coordinates


def register(mcp):
    @mcp.tool()
    async def komoot_search_thematic_tours(
        lat: float, lng: float, themes: list[str] | None = None,
        sport: str = "hike", family_friendly: bool = False,
        dog_friendly: bool = False, max_distance: int = 20000,
    ) -> dict:
        """Search tours with theme and suitability filters without saving.

        themes uses names from komoot_route_attribute_options. Examples include
        waterfalls, caves, canyons_gorges, lakes_rivers, and mountain_hut.
        Set family_friendly or dog_friendly to request those filters too.
        For example, use themes=["waterfalls"] and family_friendly=true for a family hike.
        max_distance is the search distance in metres, not a route length limit.
        This tool checks live filter availability. It never drops an unavailable filter.
        Komoot's suitability tags do not guarantee safe conditions or legal dog access.
        """
        validate_coordinates([[lat, lng]])
        profile = native_sport(sport)
        if isinstance(max_distance, bool) or not isinstance(max_distance, int) or max_distance <= 0:
            raise ValueError("max_distance must be a positive integer in metres")
        filters = []
        for value in themes or []:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("themes must contain nonempty filter names")
            if value not in filters:
                filters.append(value)
        for enabled, value in ((family_friendly, "family_friendly"), (dog_friendly, "dog_friendly")):
            if enabled and value not in filters:
                filters.append(value)
        if not filters:
            raise ValueError("Supply a theme or enable a suitability filter")
        client = get_client()
        catalog = await client.route_attribute_options(lat, lng, profile, max_distance=max_distance)
        available = catalog.get("route_attributes") if isinstance(catalog, dict) else None
        if not isinstance(available, list) or any(not isinstance(value, str) for value in available):
            raise KomootAPIError("Komoot returned an invalid route attribute catalog")
        unsupported = [value for value in filters if value not in available]
        if unsupported:
            raise ValueError("Unavailable route attributes: " + ", ".join(unsupported))
        results = await client.discover_with_attributes(
            lat, lng, sport=profile, attributes=filters, max_distance=max_distance,
        )
        if not isinstance(results, dict):
            raise KomootAPIError("Komoot returned an invalid thematic tour result")
        embedded, page = results.get("_embedded"), results.get("page")
        items = embedded.get("items") if isinstance(embedded, dict) else None
        empty_page = isinstance(page, dict) and page.get("totalElements") == 0
        if not isinstance(items, list) and not empty_page:
            raise KomootAPIError("Komoot returned an invalid thematic tour result")
        return {"requested_filters": filters, "available_filters": available,
                "status": "empty" if empty_page or items == [] else "available",
                "sport": profile, "max_distance_m": max_distance, "results": results}
