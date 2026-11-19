"""Read collections and manage owned collections without changing tour data."""

from typing import Annotated, Literal

from pydantic import Field

from komoot_mcp import collections
from komoot_mcp.context import get_client

_Id = Annotated[int, Field(gt=0, strict=True)]
_Page = Annotated[int, Field(ge=0, strict=True)]
_Status = Literal["private", "friends", "public"]
_TourIds = Annotated[list[_Id], Field(min_length=1)]


def register(mcp):
    @mcp.tool()
    async def komoot_get_collection(collection_id: _Id) -> dict:
        """Read a collection's metadata by ID without changing the account."""
        return await get_client().get_collection(collection_id)

    @mcp.tool()
    async def komoot_get_collection_tours(collection_id: _Id, page: _Page = 0) -> dict:
        """Read one native collection-member page, including Highlights.

        Follow returned pagination links for additional pages. Native member
        types and ordering remain unchanged. This tool does not change the account.
        """
        return await get_client().get_collection_tours(collection_id, page=page)

    @mcp.tool()
    async def komoot_list_collections(
        page: _Page = 0, name: str | None = None, status: _Status | None = None,
    ) -> dict:
        """List your own created collections, not saved community collections.

        Optional name and visibility filters are sent to Komoot. Native page
        totals cover created elements, which can also include excluded guides.
        Inspect each page before concluding that a collection is absent.
        """
        return await collections.list_collections(get_client(), page=page, name=name, status=status)

    @mcp.tool()
    async def komoot_create_collection(
        name: str, description: str = "", status: _Status = "private",
        tour_ids: list[_Id] | None = None,
    ) -> dict:
        """Create an ordinary personal collection, optionally with ordered tours.

        Defaults to private. Requires approval for the exact name, description,
        visibility and initial tour list. Does not create tours or copy photos.
        Readback verifies metadata, visibility and membership. An uncertain
        result must be reconciled by listing collections, not blindly retried.
        """
        body = collections.metadata_fields(name, description, status)
        ids = tour_ids if tour_ids is not None else []
        collections.validate_tour_ids(ids)
        return await collections.create_collection(get_client(), body, ids)

    @mcp.tool()
    async def komoot_modify_collection(
        collection_id: _Id, name: str | None = None,
        description: str | None = None, status: _Status | None = None,
    ) -> dict:
        """Change supplied metadata fields of a collection you own.

        An empty description clears the introduction. Requires exact approval.
        Does not issue visibility updates for contained tours. Readback verifies
        changed and preserved metadata. Inspect uncertain outcomes before retrying.
        """
        body = collections.metadata_fields(name, description, status)
        return await collections.modify_collection(get_client(), collection_id, body)

    @mcp.tool()
    async def komoot_delete_collection(collection_id: _Id) -> dict:
        """Delete an owned collection, not its contained tours or Highlights.

        Requires explicit approval for this collection ID. Does not issue
        deletion requests for contained resources. Readback checks absence.
        Inspect uncertain outcomes before retrying.
        """
        return await collections.delete_collection(get_client(), collection_id)

    @mcp.tool()
    async def komoot_add_collection_tours(collection_id: _Id, tour_ids: _TourIds) -> dict:
        """Append tours to an owned ordinary collection in the supplied order.

        Requires approval for the exact IDs. Already present tours stay in place.
        Reads every member page and preserves existing Highlights and tour types.
        Rejects special multi-day routes. Rewrites the complete compilation and
        verifies its order. Concurrent edits can race; inspect uncertain outcomes.
        """
        collections.validate_tour_ids(tour_ids)
        return await collections.change_tours(get_client(), collection_id, tour_ids, "add")

    @mcp.tool()
    async def komoot_remove_collection_tours(collection_id: _Id, tour_ids: _TourIds) -> dict:
        """Remove selected tour memberships without deleting the actual tours.

        Requires approval for the exact IDs. Removes all occurrences of selected
        tours, not Highlights with matching IDs. Preserves all other members.
        Rejects special multi-day routes. Reads all pages, replaces the complete
        compilation and verifies readback. Inspect uncertain outcomes before retrying.
        """
        collections.validate_tour_ids(tour_ids)
        return await collections.change_tours(get_client(), collection_id, tour_ids, "remove")

    @mcp.tool()
    async def komoot_reorder_collection_tours(collection_id: _Id, tour_ids: list[_Id]) -> dict:
        """Set the complete tour order in an owned ordinary collection.

        Supply every current tour ID exactly once in the approved order.
        Highlights keep their existing positions. Rejects special multi-day
        routes. Reads all pages and verifies the full compilation after saving.
        Concurrent edits can race; inspect uncertain outcomes before retrying.
        """
        collections.validate_tour_ids(tour_ids)
        return await collections.change_tours(get_client(), collection_id, tour_ids, "reorder")
