"""Komoot browsing, planning and artifact publication over stdio or HTTP MCP.

The HTTP gateway forwards per-user credentials through ``x-user-credentials``
and a shared ``Authorization: Bearer <INTERNAL_SECRET>`` token. See middleware.py
for the wire format. Local stdio uses credentials from the environment.
"""

import os

from mcp.server import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from komoot_mcp.protocol import KomootMCP
from komoot_mcp import guidance

from komoot_mcp.tools import (
    auth_tools,
    browse_tools,
    briefing_tools,
    collection_tools,
    data_tools,
    discover_tools,
    export_tools,
    extended_discovery_tools,
    highlight_tools,
    native_routing_tools,
    nearby_tour_tools,
    overnight_tools,
    publication_tools,
    private_place_tools,
    poi_review_tools,
    thematic_tools,
    section_tools,
    routing_tools,
    share_tools,
    write_tools,
)


def create_server(host="127.0.0.1", port=8000, *, read_only=False):
    """Create the Komoot server with per-request account state."""
    mcp = KomootMCP(
        "komoot-mcp",
        read_only=read_only,
        instructions=guidance.SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    auth_tools.register(mcp)
    browse_tools.register(mcp)
    briefing_tools.register(mcp)
    data_tools.register(mcp)
    write_tools.register(mcp)
    routing_tools.register(mcp)
    discover_tools.register(mcp)
    highlight_tools.register(mcp)
    collection_tools.register(mcp)
    share_tools.register(mcp)
    export_tools.register(mcp)
    extended_discovery_tools.register(mcp)
    native_routing_tools.register(mcp)
    nearby_tour_tools.register(mcp)
    overnight_tools.register(mcp)
    publication_tools.register(mcp)
    private_place_tools.register(mcp)
    poi_review_tools.register(mcp)
    thematic_tools.register(mcp)
    section_tools.register(mcp)
    guidance.register(mcp)
    return mcp


def _run_http(mcp: FastMCP) -> None:
    """Serve the MCP app with the existing gateway middleware stack."""
    import asyncio
    import uvicorn

    from komoot_mcp.middleware import (
        InternalSecretMiddleware,
        UserCredentialsMiddleware,
    )

    app = mcp.streamable_http_app()
    # Starlette wraps outermost-last: authenticate the gateway before parsing
    # per-user credentials.
    app.add_middleware(UserCredentialsMiddleware)
    app.add_middleware(InternalSecretMiddleware)

    config = uvicorn.Config(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


def main():
    """Run the Komoot importer over stdio or the authenticated HTTP gateway."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "3007")))
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--read-only", action="store_true",
        help="Offer only read-only tools and reject all other tool calls",
    )
    args = parser.parse_args()

    mcp = create_server(host=args.host, port=args.port, read_only=args.read_only)
    if args.transport == "http":
        _run_http(mcp)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
