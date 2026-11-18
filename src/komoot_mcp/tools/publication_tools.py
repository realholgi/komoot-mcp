"""Local importer adapters for explicit artifact publication."""

import asyncio

from komoot_mcp.context import get_client, require_local_filesystem
from komoot_mcp.publication import Publication


def register(mcp):
    @mcp.tool()
    async def komoot_preview_prepared(
        prepared_path: str, publication_directory: str, update: bool = False,
    ) -> dict:
        """Read-only local preflight; does not authenticate or verify live Komoot state.

        Reports artifact problems and existing publication blockers. ready=true
        means local checks passed, not that a live import or update will succeed.
        """
        require_local_filesystem()

        def preview():
            return Publication(publication_directory).preview(prepared_path, update=update)

        return await asyncio.to_thread(preview)

    @mcp.tool()
    async def komoot_publish_prepared(
        prepared_path: str, publication_directory: str,
        confirmed: bool = False, update: bool = False,
    ) -> dict:
        """Publish explicit prepared artifacts; requires approval for account mutations.

        Use the same publication_directory for every retry and update. An
        interrupted operation requires manual reconciliation, never a fresh import.
        """
        require_local_filesystem()
        if confirmed is not True:
            raise ValueError("Komoot publishing requires confirmed=true after explicit user approval")
        publication = Publication(publication_directory)
        return await publication.publish(prepared_path, get_client(), update=update)

    @mcp.tool()
    async def komoot_adopt_receipt(
        receipt_path: str, publication_directory: str, prepared_path: str | None = None,
    ) -> dict:
        """Preserve a historical receipt; optionally rebase to explicit local artifacts."""
        require_local_filesystem()
        publication = Publication(publication_directory)
        return await asyncio.to_thread(publication.adopt_receipt, receipt_path, prepared_path)
