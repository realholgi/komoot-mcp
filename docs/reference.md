# Reference

[Project overview](../README.md) | [MCP setup](mcp-setup.md) | [Prepared-route import](prepared-import.md)

Use MCP tool discovery for current argument schemas and side-effect hints.
Startup policy can hide tools.
Unknown parameter names fail instead of being ignored.

## Tool groups

### Account and tour browsing (11)

- `komoot_login`
- `komoot_list_tours`
- `komoot_find_my_tours_near`
- `komoot_get_tour`
- `komoot_get_tour_full`
- `komoot_get_highlight`
- `komoot_tour_weather`
- `komoot_get_user_profile`
- `komoot_get_tour_photos`
- `komoot_get_tour_line`
- `komoot_list_user_highlights`

Tour weather is experimental. Verify it at runtime.

`komoot_list_tours` can search your own tour start locations.
Set `center: [lat, lng]`, `max_distance` in metres, and `sort_field: "proximity"`.
Proximity sorts nearest first unless you set `sort_direction: "desc"`.
This does not search every point along each route.
Compact results keep the start coordinates and pagination.

The `name` filter searches tour titles only.
For requests such as "my Hundsbach tour", use `komoot_find_my_tours_near` even if the title search returns nothing.
Keep its default 5000-metre radius unless the user specifies a distance.
An empty search covers only the selected place, radius, tour type and scanned records.
Use a supplied tour URL or ID directly.

`komoot_find_my_tours_near` searches your full tour routes, including start and end.
Supply `query` with a place name, or `center: [lat, lng]`. Do not supply both.
Photon resolves place names. Ambiguous names return `places` without reading the account.
Select a place and call again with its coordinates.
`radius_m` sets a positive straight-line radius. The default is 5000 metres.
The search defaults to planned tours. Set `tour_type: null` to include recorded tours.
Use `sport_type` to filter by sport.
The search applies no tour-name or start-location filter.
It checks complete coordinate segments, not only route vertices or simplified lines.
Results include the nearest distance, route kilometre, and distances to both endpoints.
`proximity.near` identifies nearby route, start, and end positions.
These distances do not establish a reachable walking detour.

Each tour needs a coordinate request. `max_tours` limits scanned records to 1–1000.
The default is 200. `limit` bounds returned matches to 1–100 and defaults to 20.
The search sorts all matches in the scanned slice before applying `limit`.
`matched_tours` counts matches in that slice. `results_truncated` reports omitted matches.
`scan.coverage` reports whether the whole filtered account listing was checked.
Unavailable geometry appears in `scan.failures`. It does not count as outside the radius.
Use `scan.next_offset` as `offset` to continue a bounded scan.
Continuation results rank that slice only. Merge slices to rank a larger search.
Account edits can shift page positions between calls.

### Tour data (8)

- `komoot_get_tour_coordinates`
- `komoot_get_tour_gpx`
- `komoot_get_tour_directions`
- `komoot_get_tour_way_types`
- `komoot_get_tour_surfaces`
- `komoot_get_tour_timeline`
- `komoot_analyze_tour_sections`
- `komoot_get_tour_briefing`

`komoot_get_tour_gpx` returns complete XML in `gpx_content`.
`size_bytes` counts UTF-8 bytes. `truncated` is always `false`.
The server does not silently shorten GPX content.

`komoot_analyze_tour_sections` joins coordinate-index spans for way types, surfaces, and directions.
Each section reports its start and end in kilometres from the tour start.
Use `start_km` and `end_km` to inspect part of the route.
Distances come from the coordinates, not whole-tour percentages.
Documented English labels accompany native codes. Unknown codes keep a null label.
Missing or conflicting spans stay unknown. Directions retain their original fields.
This can locate a documented stone surface at a specific kilometre.
It does not infer terrain, difficulty, or access that Komoot did not report.

`komoot_get_tour_briefing` reads supplies, documented sections, and an estimated schedule.
It does not change the tour or save pauses.
It searches Komoot map POIs through overlapping circles along the complete route.
Supply searches contact only Komoot. They use no account credentials or external fallback.
`corridor_m` sets a straight-line corridor of 1–1000 metres. The default is 300.
Supply results cover water points, food, groceries, and public toilets.
Each result keeps its native Komoot category, documented label, IDs, and raw fields.
Categories use Komoot's shipped map labels, not import or custom-waypoint category IDs.
Water candidates include springs and mapped drinking-water points. Their category does not verify current drinking safety.
The result includes a fetch time. Komoot's POI inventory can be incomplete or outdated.
Opening hours, current availability, access, and drinking safety remain unverified.
Offsets are straight-line distances. They do not measure a reachable detour.
Repeated route encounters remain visible for loops and out-and-back tours.
Search failures, result caps, and unknown categories appear in the coverage report.
They do not mean that no supplies exist.
Each circle requests up to 100 results. A reached limit makes supply coverage partial.
The search needs at most 200 circles. Longer routes return unavailable coverage before any POI request.
Routes over 20000 coordinates also return unavailable coverage without a shortened search.

Pass `departure_time` as an ISO 8601 timestamp with a timezone.
For example, use `2026-11-19T09:00:00+01:00`.
Add pauses with `pauses: [{"km": 8.4, "duration_s": 1200, "name": "Lunch"}]`.
The schedule uses uniform distance-based pace from the tour duration.
For recorded tours, it uses `time_in_motion` when available. Otherwise, native duration can include existing stops.
It adds pauses after arrival and excludes travel away from the route.
Missing duration or geometry leaves timing unknown.
Critical sections retain native warning codes and valid kilometre spans.
Missing spans remain unlocated. Surface labels do not establish difficulty or safe access.

### Discovery, highlights and collections (17)

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
- `komoot_get_poi_external_reviews`
- `komoot_get_highlight_content`
- `komoot_search_thematic_tours`

Bookmark tools change the account.

`komoot_get_poi_external_reviews` reads Komoot's existing external review data for a map POI.
It keeps provider IDs, ratings, counts, and source links unchanged.
An empty `reviews` list returns `status: "empty"`. It does not mean the POI was not found.
No reviews or scores are invented. Use a Komoot POI ID, not a Highlight or OSM ID.

`komoot_get_highlight_content` reads editorial `text`, `details`, and `features`.
It differs from community tips. `available_fields` lists fields with content.
Missing fields remain null, and an empty result returns `status: "empty"`.
The complete native response stays in `content`. Text can contain HTML.

`komoot_search_thematic_tours` checks live filter names before searching.
Examples include `waterfalls`, `caves`, `canyons_gorges`, `lakes_rivers`, and `mountain_hut`.
For a family hike with waterfalls, set `themes: ["waterfalls"]` and `family_friendly: true`.
Set `dog_friendly: true` to request that filter too.
Unavailable filters fail instead of being ignored. Search results retain native pagination.
Suitability tags do not guarantee safe conditions or legal access with a dog.
Filtered discovery sends `route_attributes` to the tour result endpoint.
The `/route_attributes/` endpoint returns available filters and counts, not tour records.

### Private places (4)

- `komoot_create_private_place`
- `komoot_rename_private_place`
- `komoot_list_private_places`
- `komoot_delete_private_place`

These tools manage private custom places without creating a tour or public Highlight.
Create a place with `name`, `lat`, and `lng`.
Use its returned `place_id` to rename or delete it.
Listing excludes the home address, bookmarked Highlights, and map POIs.
Renaming preserves coordinates and address data. These tools do not change the home address.
Create and rename read back the name and coordinates. Delete checks that the ID no longer appears.
After an uncertain write, list places before retrying. Errors retain `place_id` when known.

Komoot's [Saved places documentation](https://support.komoot.com/hc/en-us/articles/10194677488026-Places-and-Saved-places)
states that these locations are visible only to their owner.
The web planner uses `/v007/users/{username}/addresses`, `type=other`, and `is_home_address=false` for custom places.
This contract comes from shipped `60817.edf235f94138ffc7.js`, module `51393`, inspected on 2026-11-18.
No live account create, rename, or delete was used to test these tools.

### Downloads and exports (4)

- `komoot_download_tour_gpx`
- `komoot_download_tour_fit`
- `komoot_download_smart_tour_gpx`
- `komoot_export_all_tours`

Download tools support inline results or local files, as their schemas specify.
File output preserves download bytes. Existing files require `overwrite=true` for replacement.
FIT downloads support planned tours. There is no `komoot_get_tour_fit` tool.
Downloads count as write tools because they can create local files, even when a call requests inline content.
File upload results preserve the HTTP status and distinguish new tours from duplicates.
HTTP 201 returns `created: true` and `import_status: "created"`.
HTTP 202 returns `duplicate: true`, `created: false`, and `import_status: "already_exists"`.
The duplicate keeps its existing tour ID. The tool does not rename it or change its visibility.

### Planning and geocoding (11)

- `komoot_geocode`
- `komoot_reverse_geocode`
- `komoot_find_pois_nearby`
- `komoot_resolve_place`
- `komoot_import_gpx_route`
- `komoot_plan_native_route`
- `komoot_compare_route_variants`
- `komoot_preview_tour_update`
- `komoot_create_planned_tour`
- `komoot_plan_and_save_tour`
- `komoot_plan_and_upload`

Planning uses Komoot's native planner, the shared rate limiter and worker threads for blocking requests.
Geocoding uses public Photon.
For place-name queries, `komoot_geocode` accepts `osm_tag` to filter by an OSM tag.
Use `highway:bus_stop` for bus stops or `amenity:restaurant` for restaurants.
Coordinate queries use reverse geocoding and reject `osm_tag`.
Geocode results include `osm_id`, `osm_type`, `osm_key`, and `osm_value` when Photon supplies them.
Use the OSM tags to distinguish places such as bus stops and restaurants.
For example, `osm_key: "highway"` with `osm_value: "bus_stop"` identifies a bus stop.
These OSM identifiers are not Komoot `poi_id` values.

Use `komoot_find_pois_nearby` to get ordinary Komoot map POIs near coordinates.
Supply `lat`, `lng`, and optional `radius_m` (1–5000), `limit` (1–100), and `lang`.
The search needs no account. Results include names, coordinates, OSM IDs, categories, and available Komoot `poi_id` values.
Match Photon `osm_type: "N"` and `osm_id: 369831410` to the result's `osm_id: "n369831410"`.
Do not select the first nearby result without checking its identity.
Pass its `waypoint_details` into a planning tool to preserve the named POI.
Results without a Komoot POI ID have `waypoint_details: null`.

For example, search for `Schindelpeter` with `osm_tag: "highway:bus_stop"` near Bühlertal.
Then call `komoot_find_pois_nearby` at the returned coordinates.
The stop has Komoot `poi_id: "7mn4K"` and planning details `{"poi_id": "7mn4K"}`.

`komoot_resolve_place` combines name search and POI identity lookup in one call.
It accepts `query`, `limit` (1–10), `lang`, optional `near_lat` and `near_lng`, and optional `osm_tag`.
It returns all candidates with their original location and category.
Each candidate reports `resolution_status` and genuine POI planning details when resolved.
Statuses include `resolved`, `not_found`, `ambiguous`, `missing_poi_id`, `missing_osm_identity`, and `invalid_location`.
Unresolved candidates remain in the result. The tool does not choose a place for you or invent an ID.

| Tool | Behavior |
| --- | --- |
| `komoot_plan_native_route` | Plan through ordered coordinates without saving. Return full route data |
| `komoot_import_gpx_route` | Import GPX into route data for a separate save step |
| `komoot_create_planned_tour` | Save route data from planning or GPX import |
| `komoot_plan_and_save_tour` | Plan from coordinates and save |
| `komoot_plan_and_upload` | Plan from place names or `lat,lng` strings and save |

For a roundtrip, supply intermediate points and set `roundtrip: true` to return to the start.
If start and end already have identical latitude and longitude, native roundtrip mode activates automatically, even with `roundtrip: false`.
The MCP uses Komoot's `special:back` closing marker to enable "Hin- und Rückweg" in the planner.
It replaces the duplicate endpoint reference without adding a point, segment, or extra return leg.
This also applies to GPX imports and route data passed to create or update tools.
Height differences do not affect this check. Nearby but different coordinates do not count as equal.
The planner does not generate a loop for a target distance.

Use `waypoint_names` on the coordinate planning tools and `komoot_plan_and_upload`.
Supply one name or `null` per input point, in order: start, via points, end.
The closing roundtrip point uses `special:back` instead of a duplicate named waypoint.
Place-name planning uses resolved geocoder names unless you supply explicit names.
Coordinate-only points stay unnamed unless you supply names.

All three planning tools also accept `waypoint_details`.
Supply one object or `null` per input point, including start and end.
Each object accepts these fields:

| Field | Meaning |
| --- | --- |
| `name` | A custom waypoint name |
| `highlight_id` | A positive numeric Komoot highlight ID |
| `highlight_type` | `point` (default) or `segment` |
| `poi_id` | A Komoot map POI ID containing only ASCII letters and digits |

Use only one of `name`, `highlight_id`, or `poi_id` for each point.
Highlight and POI references cannot also have a name in `waypoint_names`.
Komoot resolves the name of a referenced highlight or map POI.
Custom names do not rename these references.
Coordinates or place locations remain required. An ID alone does not supply a location.
Named points use `cnw:` references. Highlight points use `hlp:` and segments use `hls:`.
Ordinary map POIs use `poi:` references. Supply the ID without the `poi:` prefix.
Use an ID returned by Komoot. OSM IDs cannot be converted into Komoot POI IDs.
The closing roundtrip point keeps the start location, not a duplicate POI or highlight reference.
The planner keeps references when Komoot snaps coordinates, and the save step preserves them.
Conflicting names or a different returned waypoint count fail before saving.

```json
{
  "coordinates": [[49.0, 8.0], [49.1, 8.1], [49.2, 8.2]],
  "waypoint_details": [
    {"name": "Station"},
    {"highlight_id": 123, "highlight_type": "point"},
    {"name": "Trailhead"}
  ]
}
```

For `komoot_plan_and_upload`, details follow start, each via point, then end if supplied.
Explicit details take precedence over geocoder names. `null` allows the resolved place name.


GPX imports preserve named POIs and named track or route endpoints.
The import rejects ambiguous source geometry instead of assigning names to another track.

Save results include `tour_id`, `tour_url`, `distance_m`, `duration_s` and `elevation_up_m`.
Each save reads the new tour back to check its ID, planned type, visibility, sport, fitness, and ordered waypoint references.
The result includes `verification` with requested values, observed values, and mismatches.
Successful checks set `visibility_verified: true` and report `verified_visibility`.
If readback fails or values differ, the tool returns an error with the saved tour ID.
Inspect that tour before retrying. The server does not retry writes automatically.
Prepared publication has additional read-back checks. See the [import guide](prepared-import.md).

Previews, save results, and compact or full tour details include `planning_summary`.
Its `pace` reports distance, duration in seconds, a readable duration, average speed in km/h, and fitness.
Missing or invalid durations produce no calculated speed.
The public English planner verified five presets for six sports on 2026-11-18.
See [pace label verification](pace-verification.md) for the mappings and observations.
Readable `warnings` retain their native code, raw entry, and coordinate-index spans.
`information` contains protection-area notices separately from warnings.
Unknown warning codes stay visible and are marked unknown.
These summaries do not change the route or claim that current access restrictions were checked.

`komoot_compare_route_variants` calculates shortest, scenic, and quiet routes without saving.
Each variant keeps the same input points, sport, fitness, avoidance, and pauses.
Results include full routes and distance, elevation, and duration differences from shortest.
These preferences do not guarantee scenery, low traffic, or the shortest possible route.

`komoot_plan_native_route`, `komoot_plan_and_save_tour`, and the variant tool accept `waypoint_stays_s`.
Supply one pause in seconds per input point. The default is zero.
`planning_summary.timing` reports moving time, pause time, and total time separately.
An automatically added roundtrip endpoint adds no pause.
Pauses do not change the native duration sent to Komoot.
They remain local planning data and are not stored as POI stays in the account.

`planning_summary.access_diagnostics` reports each requested point's distance from its indexed route coordinate.
It flags offsets over 50 metres.
A leg is unusual when its route exceeds three times the straight-line distance and adds over 500 metres.
Missing geometry indices produce an unknown result. Loops with identical endpoints have no detour ratio.
These checks do not prove legal or physical access to a POI.

Set `prepare_descriptions=true` in `komoot_import_gpx_route` to use MCP client sampling.
Sampling needs client approval and uses the client's model.
It needs no server model API key. Prepared-artifact import does not use sampling.

### Tour changes and sharing (8)

- `komoot_update_planned_tour`
- `komoot_upload_tour`
- `komoot_modify_tour`
- `komoot_delete_tour`
- `komoot_modify_tour_extended`
- `komoot_create_share_link`
- `komoot_revoke_share_link`
- `komoot_resolve_share_url`

Resolving a share URL reads data. Creating or revoking a link changes sharing.

`komoot_update_planned_tour(tour_id, route_data, preview_token)` replaces a planned route under its existing ID.
Plan or import the replacement first. Then call `komoot_preview_tour_update(tour_id, route_data)`.
The preview shows signed distance, elevation, and duration differences, plus ordered POI changes.
Pass its `preview_token` and the same full `route_data` to the update tool.
The client reads tour metadata and coordinates again before writing.
If the tour, target ID, or replacement changed, it returns `preview_conflict` without writing.
Review a new preview before approving the update.
This check is not an atomic server-side conditional write. An edit during the final PUT can still race.
The update preserves the current name, visibility, description, and gear.
Komoot may reset the tour date during a route update.
The result reports this as `metadata_changes.date`, with the before and after values.
It rejects recorded activities and incomplete geometry before writing.
It does not plan again, create a copy, or delete the original tour.
Readback checks geometry, ordered references, sport, fitness, visibility, and preserved metadata.
If the update cannot be verified, inspect the reported tour ID before retrying.
Write errors include `tour_id` when known and `confirmed_changes` when available.
`operation_state: applied` means Komoot accepted the write, even if readback failed.
`unknown` means the write outcome is uncertain. Neither state permits an automatic retry.

`komoot_modify_tour_extended` changes supplied fields without replacing route geometry.
It accepts name, sport, visibility, description, gear and date, plus these values:

| Argument | Behavior |
| --- | --- |
| `speed_kmh` | Set average speed using the current tour distance |
| `duration_s` | Set total duration in whole seconds |
| `time_in_motion_s` | Set moving time in whole seconds, for recorded tours only |
| `constitution` | Store fitness from 1 to 5, for planned tours only |

Speed changes set duration for planned tours and moving time for recorded tours.
The calculated time rounds to a whole second, with a minimum of one second.
Use speed or explicit times in one call, not both.
Moving time must not exceed total duration.
Fitness changes do not recalculate duration.
The tool reads timing and fitness fields back after the update.
If Komoot returns different values, the tool raises an error. Inspect the tour before retrying.

For example, `komoot_modify_tour_extended(tour_id=42, speed_kmh=4.5)` requests an average speed of 4.5 km/h.

### Route preferences

`komoot_plan_native_route`, `komoot_plan_and_save_tour` and `komoot_plan_and_upload` accept these options:

| Argument | Values |
| --- | --- |
| `route_type` | `best` (default), `quiet`, `scenic`, `shortest`, `less_elevation`, `technical` |
| `avoid` | A list of `ferries`, `steps`, `steep_hills` and `rough_terrain` |

Combine one route type with any avoidance options.
For example, use `route_type="quiet", avoid=["ferries", "steps"]`.
The planner sends these preferences as `custom_tags` when it calculates geometry.
Saved routes keep the tags. `komoot_create_planned_tour` also preserves tags from a separate planning step.
These options do not edit or recalculate an existing saved tour.
Avoidance is a preference, not a guarantee that the route excludes those features.
`technical` permits highly technical paths: T4 to T6 for hiking and S4 to S5 for biking.

The internal tags match the [web planner bundle](https://www.komoot.com/assets/4357.fdfb3f876f388c4d.js).
Route types use their public names, except `best`, which has no tag.
Avoidance maps to `no_ferry`, `no_steps`, `no_steep` and `no_poor`, respectively.

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
| `gravel_ride` | `mtb_easy` |
| `enduro` | `mtb_advanced` |
| `mountaineering` | `mountaineering` |
| `e_mountain_bike` | `e_mtb` |
| `e_road_cycle` | `e_racebike` |
| `e_gravel_ride` | `e_mtb_easy` |
| `e_enduro` | `e_mtb_advanced` |

Planning tools also accept native names such as `racebike` and `e_mtb`.
Their descriptions list accepted names. Unknown names fail before network access.

### HTTP caching

Successful JSON tour reads on `api.komoot.de/v007/tours/` can use a process-local cache.
It holds at most 128 responses and 16 MiB.
Entries stay separate by credentials, query parameters, and request representation headers.
Nothing is written to disk.
The cache uses `Cache-Control` freshness, `Date`, and `Age`.
Expired entries use `If-None-Match` with an ETag, or `If-Modified-Since` with Last-Modified.
A valid HTTP 304 reuses the saved body.
`no-store` responses, wildcard Vary, cookies, streams, and non-JSON downloads bypass storage.
Writes invalidate that account's cached tours before and after the request, including uncertain failures.
Tour snapshots and write readbacks force revalidation even when an entry is fresh.
This saves repeated body transfers. It does not provide atomic write protection.

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
