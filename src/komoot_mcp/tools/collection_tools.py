"""Collection tools for komoot-mcp.

Komoot collections are curated bundles of tours (think "Best gravel
rides in Bavaria"). These tools expose the collection metadata, list a
user's saved collections, and enumerate the tours inside a collection.
"""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_get_collection(collection_id: int) -> dict:
        """Get a Komoot collection's metadata.

        Example: ``komoot_get_collection(collection_id=947498)``.

        Args:
            collection_id: The numeric collection ID
        """
        return await get_client().get_collection(collection_id)

    @mcp.tool()
    async def komoot_get_collection_tours(
        collection_id: int, page: int = 0,
    ) -> dict:
        """List the tours inside a collection (compilation).

        Example: ``komoot_get_collection_tours(collection_id=947498,
        page=0)``.

        Args:
            collection_id: The numeric collection ID
            page: Page number (0-indexed)
        """
        return await get_client().get_collection_tours(collection_id, page=page)
