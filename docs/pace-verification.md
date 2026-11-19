# Pace label verification

Observed on 2026-11-18 in the public [Komoot planner](https://www.komoot.com/plan/), using its English interface in Chromium.

## Verified mappings

Each sport below used all five labels. Each UI selection sent the listed `constitution` value. Every response returned the same value with HTTP 200.

| English UI label | Sent `constitution` | Returned `constitution` |
| --- | --- | --- |
| Relaxed | 1 | 1 |
| Steady | 2 | 2 |
| Moderate | 3 | 3 |
| Fast | 4 | 4 |
| Very fast | 5 | 5 |

| English sport label | Sent and returned `sport` | Captured request IDs for levels 1, 2, 3, 4, 5 |
| --- | --- | --- |
| Hiking | `hike` | 421, 317, 320, 321, 324 |
| Running | `jogging` | 334, 336, 339, 341, 343 |
| Cycling | `touringbicycle` | 349, 354, 357, 360, 362 |
| Mountain biking | `mtb` | 366, 369, 371, 374, 377 |
| Road cycling | `racebike` | 381, 385, 389, 392, 395 |
| Gravel riding | `mtb_easy` | 400, 403, 406, 408, 412 |

Request IDs are browser-session identifiers (`request-<number>`), not permanent server identifiers.

## Method

1. Open the public planner without signing in.
2. Select Hiking. Search for Karlsruhe Hauptbahnhof and Karlsruhe Palace, then select them as the start and destination.
3. Open the route's speed control. Select each named preset through the visible menu.
4. Capture each resulting `POST /api/routing/tour?sport=<sport>&_embedded=coordinates%2Cway_types%2Csurfaces%2Cdirections` request.
5. Read `sport` and `constitution` from the JSON request body and JSON response. Confirm the selected label appears in the updated route summary.
6. Repeat with Running, Cycling, Mountain biking, Road cycling and Gravel riding.

The preview used routed coordinates near `[48.993624, 8.402047]` and `[49.013486, 8.404369]`. Browser request and response listeners captured the JSON fields. Browser text reads confirmed the labels after each selection. Scripts did not change page state or supply numeric fitness values.

For example, selecting Running and Steady sent `{"sport":"jogging","constitution":2,...}`. The HTTP 200 response returned `sport: "jogging"` and `constitution: 2`. The updated UI displayed `Steady: 10:47 /mi`.

No tour was saved. No account mutation, login, terms acceptance or credential access was needed. The browser tab was closed afterward.

## Limits

These observations cover the six listed sports and the English preset labels on this date. They do not verify translations, e-bike variants, other sports, legacy fitness labels or custom pace semantics. Numeric API acceptance alone does not establish a UI label mapping. Displayed speeds and running paces depend on the selected route and are not universal preset speeds.
