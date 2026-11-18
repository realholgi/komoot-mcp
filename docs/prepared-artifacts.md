# Prepared artifact format, version 1

[Project overview](../README.md) | [Prepared-route import](prepared-import.md) | [Reference](reference.md)

Producers pass prepared files to the Komoot MCP importer through this format.
They do not need a shared Python package, source checkout or running exporter process.
The filename is `prepared.json`. Its `schema_version` selects the format.
Consumers reject unsupported schema versions.

## Files to transfer

Transfer `prepared.json` with every referenced GPX and detail file.
Keep their relative paths intact.
Do not transfer the exporter's account credentials or entire source database.
A public GPX alone does not provide the prepared-artifact format.
The lower-level GPX import tool accepts standalone GPX content.

```text
prepared.json
stage-01.gpx
stage-01-details.zip
```

## Manifest example

```json
{
  "schema_version": 1,
  "stage_id": 1,
  "comments_coverage": "cache-only",
  "routes": [
    {
      "id": "1",
      "source_route_id": "1",
      "name": "Stage 1",
      "gpx": "stage-01.gpx",
      "details": "stage-01-details.zip",
      "description": "Source notes for the tour",
      "aliases": {"1:1:42": "Source"}
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `stage_id` | Positive stage number used in publication record names |
| `comments_coverage` | Source coverage label. Only `cloud-complete` suppresses the cache-only tour notice |
| `routes` | Prepared main routes, alternatives or adjoining sections |
| `routes[].id` | Stable route or section identity, recorded in publication receipts |
| `routes[].source_route_id` | Original route identity for split sections and legacy stage naming |
| `routes[].name` | Planned-tour title |
| `routes[].gpx` | Relative path to the prepared GPX |
| `routes[].details` | Relative path to a local detail file. The exporter writes a ZIP |
| `routes[].description` | Tour description sent to Komoot |
| `routes[].aliases` | Stable source POI IDs mapped to their prepared waypoint names |

Historical version 1 files can omit `stage_id`.
Without `stage_id`, the importer uses the first route's numeric `source_route_id`.
If that value is unavailable, it uses the route's `id`, which can have an optional `-part-N` suffix.
New producers should write `stage_id` explicitly.
Keep route IDs stable across updates.
Do not infer POI identity from distance or a shortened name.

GPX and detail paths must point to existing files inside the manifest directory.
The importer rejects parent traversal and symlinks that escape that directory.
It checks that detail files exist but does not interpret or upload their contents.
Each route's aliases correspond to the named POIs in its GPX.

Names must fit 30 UTF-16 units. Descriptions must fit 250 UTF-16 units.
Each planned tour supports at most 512 named POIs.
Before publication, producers split larger routes into adjoining sections.

## Optional producer metadata

Producers can keep extra fields that record the data's source. Current exports include:

- `source_manifest`: the original manifest path, for attribution only.
- `source_manifest_sha256`: the SHA-256 of the exact original manifest bytes.
- `photo_mode`: whether local photos were included in detail archives.
- Route `kind`, `section`, `poi_ids` and `source_sha256` fields.

`source_sha256` identifies the original route GPX, not the prepared GPX.
The importer does not need access to `source_manifest` or the producer's inventory, snapshots or stage state.
After you copy the prepared directory to another machine, these paths can still refer to the original machine.
Unknown metadata fields do not select code or change the importer tool interface.

## Content and update guarantees

Prepared GPXs contain the selected short texts.
Original names, complete comments and source bytes stay with the producer's data and detail archives.
The current exporter includes unchanged `source.gpx` and `source-manifest.json` files.
Photos stay local. The importer does not upload them to planned Komoot tours.

After approval, publication sends GPX content and tour descriptions to Komoot.
It reads the saved tours back to check private planned-tour status, named waypoint text and imported geometry.
Komoot's file import can simplify geometry.
These checks do not prove that every point matches the original source track.

Updates use the previous receipt and preparation to identify owned tours and POIs.
Keep both accessible.
For each updated route, the new preparation must preserve source POI identities.
The importer blocks updates if section membership changes, unknown account points have new names, or concurrent edits occur.
It does not migrate those changes automatically.
The importer manages publication receipts, pending intents and locks separately from this producer format.
