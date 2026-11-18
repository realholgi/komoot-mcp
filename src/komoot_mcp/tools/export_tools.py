"""Byte-preserving tour downloads and bounded bulk exports."""

from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Any

from komoot_mcp.artifacts import atomic_write, write_export
from komoot_mcp.client import _iso_date
from komoot_mcp.context import get_client, require_local_filesystem

_API = "https://api.komoot.de/v007"




def _slug(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name, flags=re.UNICODE).strip("_")[:60] or "tour"




async def _download(client, tour_id: int, fmt: str, share_token: str | None = None) -> bytes:
    params = {"share_token": share_token} if share_token is not None else None
    return await client._http_request("GET", f"{_API}/tours/{tour_id}.{fmt}", params=params, raw=True)


def register(mcp):
    @mcp.tool()
    async def komoot_download_tour_gpx(
        tour_id: int,
        save_path: str | None = None,
        share_token: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Download GPX as inline XML or save exact bytes to a server-side file/directory.

        Existing files are refused unless overwrite=True. A share_token can grant
        access to another user's private tour. Local writes are atomic.
        """
        data = await _download(get_client(), tour_id, "gpx", share_token)
        if save_path is not None:
            return await asyncio.to_thread(write_export, save_path, f"{tour_id}.gpx", data, overwrite)
        return {"tour_id": tour_id, "gpx_content": data.decode("utf-8"), "size_bytes": len(data), "truncated": False}

    @mcp.tool()
    async def komoot_download_tour_fit(
        tour_id: int, save_path: str | None = None, overwrite: bool = False,
    ) -> dict[str, Any]:
        """Download FIT (Komoot supports planned tours only).

        Without save_path, returns all binary bytes encoded as base64, with
        filename, media_type and size_bytes. Otherwise saves exact bytes to a
        server-side file/directory, refusing existing files unless overwrite=True.
        API errors propagate; an unavailable FIT is not reported as success.
        """
        data = await _download(get_client(), tour_id, "fit")
        if save_path is not None:
            return await asyncio.to_thread(write_export, save_path, f"{tour_id}.fit", data, overwrite)
        return {
            "filename": f"{tour_id}.fit",
            "media_type": "application/octet-stream",
            "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"),
            "size_bytes": len(data),
        }

    @mcp.tool()
    async def komoot_export_all_tours(
        dest_dir: str,
        fmt: str = "gpx",
        tour_type: str | None = None,
        sport_types: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        overwrite: bool = False,
        max_tours: int = 1000,
    ) -> dict[str, Any]:
        """Export up to max_tours to '<id>_<name>.gpx' (or .fit) in dest_dir.

        sport_types is comma-separated. Date-only filters cover full UTC days;
        timestamps pass through unchanged. FIT always selects tour_planned.
        Existing files are skipped by default. Per-tour errors are returned in
        failures and do not stop other exports; listing/authentication errors
        propagate. Counts include distinct tours processed, including skips and
        failures. Files are published atomically on the server's filesystem.
        """
        require_local_filesystem()
        if fmt not in ("gpx", "fit"):
            raise ValueError("fmt must be 'gpx' or 'fit'")
        if max_tours < 0:
            raise ValueError("max_tours must be nonnegative")
        out = Path(dest_dir).expanduser()
        await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
        written: list[str] = []
        skipped: list[str] = []
        failures: dict[str, str] = {}
        seen: set[str] = set()
        page = 0
        client = get_client()
        user_id = (await asyncio.to_thread(client._basic_auth))[0] if max_tours else None
        while len(seen) < max_tours:
            params = {
                "type": "tour_planned" if fmt == "fit" else tour_type,
                "sport_types": sport_types,
                "start_date": _iso_date(start_date),
                "end_date": _iso_date(end_date, end_of_day=True),
                "page": page,
                "limit": 100,
            }
            result = await client._http_request(
                "GET", f"{_API}/users/{user_id}/tours/",
                params={key: value for key, value in params.items() if value is not None},
            )
            tours = (result.get("_embedded") or {}).get("tours") or []
            if not tours:
                break
            before = len(seen)
            for tour in tours:
                tour_id = str(tour["id"])
                if tour_id in seen:
                    continue
                if len(seen) >= max_tours:
                    break
                seen.add(tour_id)
                try:
                    # Numeric IDs cannot introduce path separators into filenames.
                    numeric_id = int(tour_id)
                    target = out / f"{numeric_id}_{_slug(tour.get('name') or '')}.{fmt}"
                    if not overwrite and os.path.lexists(target):
                        skipped.append(target.name)
                        continue
                    data = await _download(client, numeric_id, fmt)
                    try:
                        await asyncio.to_thread(atomic_write, target, data, overwrite)
                    except FileExistsError:
                        if overwrite:
                            raise
                        skipped.append(target.name)
                        continue
                    written.append(target.name)
                except Exception as exc:
                    failures[tour_id] = str(exc)
            page += 1
            pagination = result.get("page") or {}
            total_pages = pagination.get("totalPages")
            if len(seen) == before or (isinstance(total_pages, int) and total_pages > 0 and page >= total_pages):
                break
        return {
            "dest_dir": str(out),
            "written": len(written),
            "skipped_existing": len(skipped),
            "failures": failures,
            "files": written,
            "processed": len(seen),
        }
