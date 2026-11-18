# Import prepared routes

[Project overview](../README.md) | [MCP setup](mcp-setup.md) | [Artifact format](prepared-artifacts.md)

## Use routes from hexatrek-exporter

[hexatrek-exporter](https://github.com/realholgi/hexatrek-exporter) collects HexaTrek stages, alternatives, POIs and community reports.
It prepares local files. `komoot-mcp` imports those files and manages the Komoot tours.

1. Collect the stage in the exporter and review its POI summaries.
2. Run `hexatrek prepare --stage 1 --summaries-file ./data/stage-01-summaries.json` in the exporter's environment.
3. Use the preparation's `prepared_path`. Transfer `prepared.json` with every referenced GPX and detail file if the server runs elsewhere.
4. Follow [Publish private planned tours](#publish-private-planned-tours) below. Keep the publication directory for later updates.

The public `exports/komoot/stage-01/` directory contains GPXs for manual import.
It does not contain the complete preparation that `komoot_publish_prepared` needs.
Use the preparation under `data/` for this tool.
Keep relative file paths intact when you copy it.

### Why use prepared routes

Prepared routes carry the collected stage geometry and reviewed POI text into Komoot.
The exporter shortens text to fit Komoot's limits and splits routes with more than 512 named POIs.
Full comments and source data stay in local detail archives.

The prepared manifest records route identities and POI aliases that a GPX-only import does not provide.
The importer uses these records to check saved tours and identify routes and POIs during updates.
It imports the existing track with `match_route=false` instead of planning a new route through Komoot's network.
Komoot can still simplify the imported geometry.

This file handoff keeps collection and account changes separate.
The exporter needs no Komoot credentials, and `komoot-mcp` needs no exporter installation or source database.

## Preview a publication

Run a local check before you approve account changes:

```text
komoot_preview_prepared(
    prepared_path="/absolute/path/to/prepared.json",
    publication_directory="/private/komoot-publications/stage-01"
)
```

The preview checks referenced files, GPX coordinates, POI identities and Komoot's text and count limits.
It lists the intended routes and reports pending operations, locks and incompatible receipts.
It does not create directories, write records, authenticate or contact Komoot.

Read `data.ready` and `data.issues` from the MCP result.
`ready=true` means that local checks passed. It does not verify the live account.
For an update, use `update=true` in both the preview and publication calls.

## Publish private planned tours

Connect `komoot-mcp`. Set Komoot credentials in its launch environment.
Use an absolute `prepared_path` from a producer that writes the supported artifact format.
The server does not need the producer's Python package or source database.

Choose a permanent publication directory for each route set.
Keep that directory for retries and updates.
The server uses its records to detect existing imports and requests with uncertain results.

Publishing changes your Komoot account.
Review and approve the prepared routes and operation before setting `confirmed=true`:

```text
komoot_publish_prepared(
    prepared_path="/absolute/path/to/prepared.json",
    publication_directory="/private/komoot-publications/stage-01",
    confirmed=true
)
```

The tool sends prepared GPX content and route descriptions to Komoot.
Detail ZIPs and photo files stay local.
Komoot can simplify imported track geometry and omit source POIs.
The importer restores omitted POIs at their source positions without adding track detours.
It reads the saved tours back to check private planned-tour status, POI text and imported geometry.

## Updates and recovery

For updates, use the same tool and directory with `update=true`.
Approve updates separately from creation.
Updates require verified tour IDs with no duplicates.
The importer also checks source POI identities.
If section membership changes, review the migration.
Verify account updates against live Komoot data.

Before sending account requests, the importer writes `pending.json`.
It records returned tour IDs before reading tours back.
If a request's result is uncertain, its pending intent blocks automatic retries.
This includes requests whose network response was lost.
Compare receipts with the actual Komoot tours before resolving an intent.
Existing receipts block another import.
Do not use an empty directory to bypass these checks.

Before removing a leftover publication `.lock`, inspect it.
Do not remove the lock of a running process.

To retain a historical publication without uploading again:

```text
komoot_adopt_receipt(
    receipt_path="/absolute/path/to/receipt.json",
    publication_directory="/private/komoot-publications/stage-01",
    prepared_path="/absolute/path/to/prepared.json"
)
```

If you do not need a replacement binding, omit `prepared_path`.
Choose the complete canonical receipt.
Do not use overlapping partial receipts for the same tours.
Adoption does not verify that historical summaries describe current reports.
The source receipt stays unchanged.
The rebound copy records its original preparation path.

## GPX category policy

Set `match_route=false` in `komoot_import_gpx_route` to select the original track.
The tool preserves generic shaping-point names.
It attaches custom waypoint text to `cnw:` path entries.
Source GPX types map to the closest current Komoot category:

| Source types | Category | ID |
| --- | --- | --- |
| Fontaine, Source, Cours d’eau | Water | `3` |
| Restaurant | Restaurant | `1001` |
| Epicerie / Artisan, Supermarché | Store | `48` |
| Toilettes | Toilet | `39` |
| Point de vue | Viewpoint | `1006` |
| Refuge Gardé, Refuge non Gardé, Chambre d'hotes | Lodging | `1010` |
| Camping, Zone de Bivouac | Campsite | `27` |
| Monument, Village de France, Immanquable | Attraction | `1008` |
| Information | Info | `53` |

Original text keeps details that these categories cannot express.
The importer identifies categories by source name, not distance from projected coordinates.
If repeated names have conflicting categories, the import fails before upload.
Unknown types keep their imported categories.
To restore an omitted POI, the importer needs a recognized source type.
Category icons do not establish water safety, access permission or available facilities.
The category IDs were checked against [Komoot's web definitions](https://www.komoot.com/assets/66569.c24e1fbb6e2224f4.js).
Legacy GPX symbol IDs, such as Shelter `36` and Scenic Area `38`, differ from current web-editor categories.

Komoot does [not support adding your own photos to planned routes](https://support.komoot.com/hc/en-us/articles/10194668744090-Trail-View-photos).
Do not convert planned tours into activities or publish Highlight photos as a workaround.
Keep original comments and photo archives with the producer's data.
