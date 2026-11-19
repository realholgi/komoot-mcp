"""Write operation tools for komoot-mcp."""

from typing import Literal

from komoot_mcp.client import KomootAPIError
from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_upload_tour(
        gpx_content: str = None,
        filepath: str = None,
        data_type: str = None,
        sport: str = "touringbicycle",
        tour_name: str = None,
        status: Literal["private", "friends", "public"] = "private",
    ) -> dict:
        """Upload a tour to Komoot from GPX content or a file path.

        Prefer ``gpx_content`` — that's the only mode that works under
        the multi-tenant gateway, where the MCP server cannot read the
        caller's local filesystem. ``filepath`` is still supported for
        stdio / local-dev use, and is the only way to upload binary
        FIT/TCX files.

        If both are provided, ``gpx_content`` wins. If neither is
        provided you get a clear error.

        Args:
            gpx_content: GPX XML as a string. Preferred under the
                gateway — pass the body of a .gpx file directly.
            filepath: Path to a GPX/FIT/TCX file on the MCP server's
                filesystem. Only useful in stdio / local-dev mode.
            data_type: File format ('gpx', 'fit', 'tcx'). Auto-detected
                from extension when ``filepath`` is used; defaults to
                'gpx' when ``gpx_content`` is used.
            sport: Komoot activity type (e.g. 'touringbicycle', 'hike',
                'mountainbike', 'racebike', 'jogging'). Defaults to
                'touringbicycle'.
            tour_name: Optional display name for the uploaded tour.
                If omitted, the GPX track name (or the filename, for
                the filepath path) is used.
            status: Requested visibility, private by default. The returned
                tour should be read back to verify Komoot honored it.

        Import results distinguish HTTP 201 Created from HTTP 202 duplicates.
        import_status is created or already_exists. A duplicate keeps its existing ID.
        This tool does not rename or change the visibility of an existing duplicate.
        """
        result = await get_client().upload_tour(
            filepath=filepath, data_type=data_type, sport=sport, gpx_content=gpx_content,
            tour_name=tour_name, status=status,
        )
        return {**result, "url": f"https://www.komoot.com/tour/{result['id']}"}

    @mcp.tool()
    async def komoot_modify_tour(
        tour_id: int,
        name: str = None,
        sport: str = None,
        status: str = None,
    ) -> dict:
        """Modify a Komoot tour's metadata.

        Args:
            tour_id: The numeric tour ID to modify
            name: New name for the tour
            sport: New sport type (e.g. 'hike', 'touringbicycle', 'mountainbike')
            status: New visibility ('public', 'private', 'friends')
        """
        updated = await get_client().modify_tour(tour_id, name=name, sport=sport, status=status)
        if updated is False:
            raise KomootAPIError(f"Komoot rejected the update for tour {tour_id}")
        return {"tour_id": tour_id, "updated": updated}

    @mcp.tool()
    async def komoot_delete_tour(tour_id: int) -> dict:
        """Delete a Komoot tour permanently.

        Args:
            tour_id: The numeric tour ID to delete
        """
        deleted = await get_client().delete_tour(tour_id)
        if deleted is False:
            raise KomootAPIError(f"Komoot rejected deletion of tour {tour_id}")
        return {"tour_id": tour_id, "deleted": True}

    @mcp.tool()
    async def komoot_modify_tour_extended(
        tour_id: int,
        description: str = None,
        gear: str = None,
        status: str = None,
        name: str = None,
        sport: str = None,
        speed_kmh: float | None = None,
        duration_s: int | None = None,
        time_in_motion_s: int | None = None,
        constitution: int | None = None,
    ) -> dict:
        """Change tour metadata, average speed, timing, or fitness.

        Changes the existing tour in the account. Only supplied fields change.
        speed_kmh sets average speed from the current distance. For planned
        tours it changes duration. For recorded tours it changes time in motion,
        which must not exceed total duration. Times round to whole seconds.
        Use speed_kmh or explicit times, not both. Geometry stays unchanged.
        Timing and fitness changes are read back. A mismatch raises an error
        after the write, so inspect the tour before retrying.
        constitution stores fitness only. It does not recalculate duration.
        Existing tour dates cannot be changed with this tool. Correct recorded
        timestamps in the source GPX and import it as a new activity instead.

        Args:
            tour_id: The numeric tour ID
            description: Long-form description text
            gear: Gear / equipment used (string)
            status: Visibility ('public', 'private', 'friends')
            name: New tour name
            sport: Sport type (e.g. 'hike', 'touringbicycle')
            speed_kmh: Average speed in km/h, greater than zero
            duration_s: Total duration in seconds, a positive integer
            time_in_motion_s: Moving time in seconds, recorded tours only
            constitution: Fitness from 1 to 5, planned tours only
        """
        result = await get_client().modify_tour_extended(
            tour_id, description=description, gear=gear,
            status=status, name=name, sport=sport,
            speed_kmh=speed_kmh, duration_s=duration_s,
            time_in_motion_s=time_in_motion_s, constitution=constitution,
        )
        return {"tour_id": tour_id, "updated_fields": {
            key: value for key, value in (
                ("description", description), ("gear", gear),
                ("status", status), ("name", name), ("sport", sport),
                ("speed_kmh", speed_kmh), ("duration_s", duration_s),
                ("time_in_motion_s", time_in_motion_s), ("constitution", constitution),
            ) if value is not None
        }, "result": result}
