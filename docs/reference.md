# Reference

[Project overview](../README.md) | [MCP setup](mcp-setup.md) | [Prepared-route import](prepared-import.md)

Use MCP tool discovery for current argument schemas and side-effect hints.
The full catalog has 51 tools. Startup policy can hide tools.
Unknown parameter names fail instead of being ignored.

## Tool groups

### Account and tour browsing (10)

- `komoot_login`
- `komoot_list_tours`
- `komoot_get_tour`
- `komoot_get_tour_full`
- `komoot_get_highlight`
- `komoot_tour_weather`
- `komoot_get_user_profile`
- `komoot_get_tour_photos`
- `komoot_get_tour_line`
- `komoot_list_user_highlights`

Tour weather is experimental. Verify it at runtime.

### Tour data (6)

- `komoot_get_tour_coordinates`
- `komoot_get_tour_gpx`
- `komoot_get_tour_directions`
- `komoot_get_tour_way_types`
- `komoot_get_tour_surfaces`
- `komoot_get_tour_timeline`

`komoot_get_tour_gpx` returns complete XML in `gpx_content`.
`size_bytes` counts UTF-8 bytes. `truncated` is always `false`.
The server does not silently shorten GPX content.

### Discovery, highlights and collections (14)

- `komoot_recommend_tours_near`
- `komoot_smart_tours_near`
- `komoot_smart_tour_for_highlight`
- `komoot_discover_with_attributes`
- `komoot_route_attribute_options`
- `komoot_find_highlights_nearby`
- `komoot_list_bookmarked_highlights`
- `komoot_bookmark_highlight`
- `komoot_unbookmark_highlight`
- `komoot_get_smart_tour`
- `komoot_get_highlight_images`
- `komoot_get_highlight_tips`
- `komoot_get_collection`
- `komoot_get_collection_tours`

Bookmark tools change the account.

### Downloads and exports (4)

- `komoot_download_tour_gpx`
- `komoot_download_tour_fit`
- `komoot_download_smart_tour_gpx`
- `komoot_export_all_tours`

Download tools support inline results or local files, as their schemas specify.
File output preserves download bytes. Existing files require `overwrite=true` for replacement.
FIT downloads support planned tours. There is no `komoot_get_tour_fit` tool.
Downloads count as write tools because they can create local files, even when a call requests inline content.

### Planning and geocoding (7)

- `komoot_geocode`
- `komoot_reverse_geocode`
- `komoot_import_gpx_route`
- `komoot_plan_native_route`
- `komoot_create_planned_tour`
- `komoot_plan_and_save_tour`
- `komoot_plan_and_upload`

Planning uses Komoot's native planner, the shared rate limiter and worker threads for blocking requests.
Geocoding uses public Photon.

| Tool | Behavior |
| --- | --- |
| `komoot_plan_native_route` | Plan through ordered coordinates without saving. Return full route data |
| `komoot_import_gpx_route` | Import GPX into route data for a separate save step |
| `komoot_create_planned_tour` | Save route data from planning or GPX import |
| `komoot_plan_and_save_tour` | Plan from coordinates and save |
| `komoot_plan_and_upload` | Plan from place names or `lat,lng` strings and save |

For a roundtrip, supply intermediate points. The planner returns to the start.
It does not generate a loop for a target distance.

Save results include `tour_id`, `tour_url`, `distance_m`, `duration_s` and `elevation_up_m`.
They separate `requested_visibility` from `verified_visibility`.
These planning save tools do not read visibility back.
Their `visibility_verified` is `false` and `verified_visibility` is `null`.
Prepared publication has separate read-back checks. See the [import guide](prepared-import.md).

Set `prepare_descriptions=true` in `komoot_import_gpx_route` to use MCP client sampling.
Sampling needs client approval and uses the client's model.
It needs no server model API key. Prepared-artifact import does not use sampling.

### Tour changes and sharing (7)

- `komoot_upload_tour`
- `komoot_modify_tour`
- `komoot_delete_tour`
- `komoot_modify_tour_extended`
- `komoot_create_share_link`
- `komoot_revoke_share_link`
- `komoot_resolve_share_url`

Resolving a share URL reads data. Creating or revoking a link changes sharing.

### Prepared publication (3)

- `komoot_preview_prepared`
- `komoot_publish_prepared`
- `komoot_adopt_receipt`

See [Prepared-route import](prepared-import.md) for preview, approval, publication records and recovery.
See [Artifact format](prepared-artifacts.md) for the file contract.

## Common values

Use numeric tour, Highlight and collection IDs where tool schemas require them.
Planning coordinates use `[latitude, longitude]` in degrees.
Tour coordinate arrays contain latitude, longitude and altitude.
Fields ending in `_m` use meters. Fields ending in `_s` use seconds.
Fitness, `constitution`, is an integer from 1 to 5.

| Sport | Komoot profile |
| --- | --- |
| `hike` | `hike` |
| `trail_run` | `jogging` |
| `mountain_bike` | `mtb` |
| `road_cycle` | `racebike` |
| `gravel_ride` | `touringbicycle` |

Planning tools also accept native names such as `racebike` and `e_mtb`.
Their descriptions list accepted names. Unknown names fail before network access.

### Pagination

`komoot_list_tours` uses a zero-based `page` and a `limit` of at most 50.
Other list tools have their own schemas. Do not assume that all lists use coordinate offsets.

`komoot_get_tour_coordinates(tour_id, offset=0, limit=500)` returns these fields in `data`:

| Field | Meaning |
| --- | --- |
| `tour_id` | Requested tour ID |
| `coordinates` | Coordinate records for this page |
| `offset` | Zero-based start offset, at least `0` |
| `limit` | Requested page size, from `1` to `1000` |
| `total` | Full coordinate count from this fetch |
| `next_offset` | Start offset for the next page, or `null` when complete |
| `truncated` | `true` when more coordinates follow this page |

Follow `next_offset` until it is `null`.
Each call still fetches the full upstream geometry. Only the MCP response is bounded.
Pages are not snapshot-isolated. Edits between calls can change offsets and totals.
Use a complete GPX when you need one geometry document.

## Tool results and errors

Each tool returns JSON in `structuredContent` and the same JSON in text content.
Successful calls put the tool payload in `data`:

```json
{"ok": true, "data": {"id": 42, "status": "private"}}
```

Failed calls set MCP `isError=true` and return `error` instead of `data`:

```json
{"ok": false, "error": {"code": "invalid_input", "message": "Invalid tool arguments", "retry_safe": false, "operation_state": "not_attempted"}}
```

Messages explain the specific failure. They are not stable identifiers.
Use `code` for program logic. `status_code` appears when an upstream HTTP status is available.

| Stable code | Meaning |
| --- | --- |
| `unknown_tool` | The tool name is not registered |
| `unknown_parameters` | The call contains unsupported parameter names |
| `invalid_input` | Caller input fails schema or explicit input checks |
| `permission_denied` | Startup policy, transport policy, local access or upstream permissions refuse the call |
| `not_found` | A requested local file or upstream object is missing |
| `timeout` | A request times out |
| `transport_error` | A request fails at the transport layer |
| `authentication_failed` | Upstream authentication fails |
| `rate_limited` | Upstream returns HTTP 429 |
| `upstream_error` | Another upstream failure occurs |
| `internal_error` | The handler or its internal data validation fails |

| `operation_state` | Meaning and response |
| --- | --- |
| `not_attempted` | Dispatch or argument checks refused the call before the handler ran |
| `unchanged` | A read-only operation failed without an account or file change |
| `unknown` | A write-capable operation failed. Account or file changes might have occurred |

Unknown parameters, pre-execution argument validation and read-only policy rejection report `not_attempted`.
A handler's internal Pydantic validation failure is `internal_error`, not a caller argument error.
Its state is `unchanged` for reads and `unknown` for writes.
An input failure inside a write handler does not by itself prove that no change occurred.

`retry_safe=true` permits retry after transient read failures such as timeouts, transport errors, HTTP 429 or upstream server errors.
Other failures keep `retry_safe=false`.
Never retry an uncertain write automatically. Reconcile saved tours, files and publication records first.

Tool annotations include read-only, destructive, idempotent and external-access hints.
Hints help clients request approval. They do not grant permission or replace startup policy.

## Resources

| URI | Content |
| --- | --- |
| `komoot://capabilities` | Active startup/transport policy, offered and unavailable tools, units and coordinate paging |
| `komoot://reference` | Sport mappings, units, coordinate paging, artifact limits and result schema |

Resources provide guidance. Reading a resource does not run a tool or approve account changes.
The resource inventory and this file complement tool discovery. Use the advertised schemas for exact arguments.

## Prompts

| Name | Arguments | Purpose |
| --- | --- | --- |
| `plan_route` | `start`, `end`, optional `sport="hike"` | Guide route planning and separate planning from saving |
| `review_prepared_import` | `prepared_path`, `publication_directory`, optional `update="false"` | Guide artifact preview, review and publication recovery |

Prompts provide instructions for the client. They do not grant approval or perform publication.
Get explicit user approval for each account change and for local file writes.
