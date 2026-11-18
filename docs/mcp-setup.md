# MCP setup

[Project overview](../README.md) | [Prepared-route import](prepared-import.md) | [Artifact format](prepared-artifacts.md)

Configure `komoot-mcp` in your MCP client.
If the client does not inherit your virtual environment's `PATH`, use an absolute command path.

```json
{
  "mcpServers": {
    "komoot-mcp": {
      "command": "/absolute/path/to/komoot-mcp/.venv/bin/komoot-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

## Account access and local files

Set these variables in the server's launch environment as needed:

| Variable | Purpose |
| --- | --- |
| `KOMOOT_EMAIL` | Komoot account email |
| `KOMOOT_PASSWORD` | Komoot account password |
| `KOMOOT_RATE_LIMIT` | Outbound Komoot requests per second, default `2` |

`KOMOOT_DATA_DIR` is unused. Prepared-route publication uses an explicit `publication_directory`.
Keep credentials out of Git, GPXs and publication receipts.

The stdio server uses the launching user's privileges and environment.
For stronger isolation, use a dedicated environment or account.
Starting the server or listing tools does not change accounts.
Get explicit user approval before account changes, including route planning tools that save tours.

After approval, set `confirmed=true` for `komoot_publish_prepared`.
For updates, also set `update=true`.
`komoot_preview_prepared`, `komoot_publish_prepared` and `komoot_adopt_receipt` reject HTTP requests before reading local files or getting account credentials.

## Tours, downloads and planning

The server has tools for tours, GPX/FIT downloads, highlights, collections, sharing, metadata changes and route planning.
Use the client's tool discovery for current names and schemas.
`komoot_get_tour_gpx` returns inline text and can truncate large responses.
For complete export bytes, use the download tools.
Use `komoot_download_tour_fit` for FIT files. `komoot_get_tour_fit` is not available.
Tour weather is experimental. Verify it at runtime.

Geocoding uses public Photon.

Set `prepare_descriptions=true` in `komoot_import_gpx_route` to use MCP client sampling.
Sampling needs client approval and uses the client's model.
It does not need a server model API key.
Prepared-artifact import does not use sampling.

## Plan routes

All planning tools use Komoot's native planner, the shared rate limiter and worker threads for blocking requests.
They accept the same sport names:

| Sport | Komoot profile |
| --- | --- |
| `hike` | `hike` |
| `trail_run` | `jogging` |
| `mountain_bike` | `mtb` |
| `road_cycle` | `racebike` |
| `gravel_ride` | `touringbicycle` |

They also accept native names such as `racebike` and `e_mtb`.
Tool descriptions list all accepted names. Unknown names fail before network access.
Coordinates use `[latitude, longitude]`. Fitness, `constitution`, is an integer from 1 to 5.

| Tool | Input and result |
| --- | --- |
| `komoot_plan_native_route` | Plan through ordered coordinates without saving. Returns the full route data |
| `komoot_create_planned_tour` | Save route data from planning or GPX import |
| `komoot_plan_and_save_tour` | Plan from coordinates and save |
| `komoot_plan_and_upload` | Plan from place names or `lat,lng` strings and save |

For a roundtrip, supply intermediate points. The planner closes the route by returning to the start.
It does not generate a loop for a target distance.

Save results include `tour_id`, `tour_url`, `distance_m`, `duration_s` and `elevation_up_m`.
They separate `requested_visibility` from `verified_visibility`.
These tools do not read visibility back, so `visibility_verified` is `false` and `verified_visibility` is `null`.

## Tool results and errors

Each MCP tool returns structured JSON in `structuredContent`.
The text content contains the same JSON.
On success, read the tool's payload from `data`:

```json
{"ok": true, "data": {"id": 42, "status": "private"}}
```

Failures set MCP `isError=true` and return an `error` object instead of `data`.
The object contains `code`, `message`, `retry_safe` and `operation_state`.
Upstream failures include `status_code` when available.
Argument validation errors report `operation_state=not_attempted`.
Failures from read-only tools report `unchanged`.
For account or file changes, a failed call can leave the result `unknown`.
Do not retry those changes automatically.

Tool discovery includes read-only, destructive, idempotent and external-access hints.
File downloads count as writes because they create or replace local files.
Hints help clients request approval. They do not grant permission.

## HTTP gateway mode

Run HTTP mode for gateway integrations without local filesystem tools:

```bash
komoot-mcp --transport http --host 127.0.0.1 --port 3007
```

The MCP endpoint is `/mcp`. The health endpoint is `/health`.
`MCP_HOST` and `MCP_PORT` set launch defaults.
Expose HTTP only behind TLS.
Every request except `/health` needs both headers:

| Header | Value |
| --- | --- |
| `Authorization` | `Bearer <INTERNAL_SECRET>` or `Bearer Internal-gateway:<GATEWAY_SECRET>` |
| `X-User-Credentials` | JSON with nonempty `email` and `password` |

Configure at least one gateway secret in the server environment.
Requests are stateless. The server keeps each request's credentials in its own context.
Missing or invalid credentials never fall back to server Komoot credentials.

Direct Komoot REST/native requests use a user ID and session token.
Legacy kompy calls use account credentials with fixed Komoot endpoints.
Direct transports check HTTPS destinations and refuse redirects.
They ignore ambient proxies and `.netrc` credentials.
Geometry endpoints use numeric IDs instead of links from API responses.
