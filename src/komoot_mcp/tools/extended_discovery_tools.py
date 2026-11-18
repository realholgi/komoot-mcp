"""Additional highlight bookmarks and Smart Tour detail/export tools."""

from __future__ import annotations

import asyncio
from typing import Any

from komoot_mcp.client import KomootAPIError
from komoot_mcp.context import get_client
from komoot_mcp.artifacts import write_export

_API_V006 = "https://api.komoot.de/v006"
_API_V007 = "https://api.komoot.de/v007"


def _highlight_list(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError("Komoot returned an unexpected highlight list response")
    return data




def register(mcp):
    @mcp.tool()
    async def komoot_find_highlights_nearby(
        lat: float,
        lng: float,
        radius_m: int = 5000,
        sport: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Find community highlights near a point without changing the account.

        radius_m is in metres. limit is clamped to 1–100. The optional exact
        sport filter (e.g. hike, touringbicycle, mtb) is applied locally after
        fetching that many results, so the returned list can be shorter or empty.
        """
        data = await get_client()._komoot_get(
            f"{_API_V006}/user_highlights/",
            params={"lat": lat, "lon": lng, "max_distance": radius_m,
                    "items_range": f"0-{min(max(limit, 1), 100) - 1}", "srid": 4326},
        )
        items = _highlight_list(data)
        return {"highlights": [item for item in items if item.get("sport") == sport] if sport else items}

    @mcp.tool()
    async def komoot_list_bookmarked_highlights(limit: int = 100) -> dict[str, Any]:
        """Read the logged-in user's recently bookmarked highlights; no mutation.

        limit is clamped to 1–100, the endpoint's maximum. This recent-results
        endpoint has no page parameter and is not a complete bookmark export.
        """
        client = get_client()
        user_id = (await asyncio.to_thread(client._basic_auth))[0]
        data = await client._komoot_get(
            f"{_API_V006}/users/{user_id}/bookmarked_user_highlights/recent/",
            params={"srid": 4326, "items_range": f"0-{min(max(limit, 1), 100) - 1}",
                    "fields": "start_point,mid_point,images"},
        )
        return {"highlights": _highlight_list(data)}

    @mcp.tool()
    async def komoot_bookmark_highlight(highlight_id: int) -> dict[str, Any]:
        """Save a highlight to the logged-in user's Komoot bookmarks.

        Side effect: changes the user's saved highlights on Komoot. An already
        bookmarked highlight is reported as success with already=True.
        """
        client = get_client()
        user_id = (await asyncio.to_thread(client._basic_auth))[0]
        try:
            await client._http_request(
                "POST", f"{_API_V006}/users/{user_id}/bookmarked_user_highlights/{highlight_id}",
                expect_json=False,
            )
        except KomootAPIError as exc:
            if exc.status_code != 409 and "already" not in (exc.body or ""):
                raise
            return {"highlight_id": highlight_id, "bookmarked": True, "already": True}
        return {"highlight_id": highlight_id, "bookmarked": True}

    @mcp.tool()
    async def komoot_unbookmark_highlight(highlight_id: int) -> dict[str, Any]:
        """Remove a highlight from the logged-in user's Komoot bookmarks.

        Side effect: changes the user's saved highlights on Komoot. A missing
        bookmark (HTTP 404) is already removed and is reported as success.
        """
        client = get_client()
        user_id = (await asyncio.to_thread(client._basic_auth))[0]
        try:
            await client._komoot_delete(
                f"{_API_V006}/users/{user_id}/bookmarked_user_highlights/{highlight_id}",
            )
        except KomootAPIError as exc:
            if exc.status_code != 404:
                raise
        return {"highlight_id": highlight_id, "bookmarked": False}

    @mcp.tool()
    async def komoot_get_smart_tour(smart_tour_id: int) -> dict[str, Any]:
        """Read Smart Tour details by its discovery ID; no account mutation."""
        data = await get_client()._komoot_get(f"{_API_V007}/discover_tours/{smart_tour_id}")
        if not isinstance(data, dict):
            raise ValueError("Komoot returned an unexpected Smart Tour response")
        return data

    @mcp.tool()
    async def komoot_download_smart_tour_gpx(
        smart_tour_id: int, save_path: str | None = None, overwrite: bool = False,
    ) -> dict[str, Any]:
        """Download a Smart Tour GPX as inline UTF-8 XML or save it locally.

        No Komoot account mutation. Side effect with save_path: creates parent
        directories and atomically writes a file. An existing directory (or path
        ending in /) receives smart_{id}.gpx. Existing files are never replaced
        unless overwrite=True. Filesystem, decoding and API errors propagate.
        """
        data = await get_client()._http_request(
            "GET", f"{_API_V007}/smart_tours/{smart_tour_id}.gpx", raw=True,
        )
        if not isinstance(data, bytes):
            raise ValueError("Komoot returned an unexpected GPX download response")
        if save_path:
            path = await asyncio.to_thread(
                write_export, save_path, f"smart_{smart_tour_id}.gpx", data, overwrite,
            )
            return path
        return {"smart_tour_id": smart_tour_id, "gpx_content": data.decode("utf-8"), "size_bytes": len(data), "truncated": False}
