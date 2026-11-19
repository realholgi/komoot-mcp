"""Offline raster/exclusion checks; these tests never contact external services."""

from io import BytesIO

from PIL import Image, UnidentifiedImageError
import pytest

from komoot_mcp import bivouac


_POINTS = [[47.0, 10.0], [47.0002, 10.0]]
_DISTANCES = [0.0, 0.02224]


@pytest.fixture
def terrain(monkeypatch):
    elements = []

    async def osm(points, radius_m, selectors):
        assert '["highway"]' in selectors
        assert '["access"~"^(private|no)$"]' in selectors
        return {"status": "ok", "elements": elements, "issues": [],
                "coverage": {"complete": True}, "source": {"name": "Synthetic OSM"}}

    monkeypatch.setattr(bivouac, "query_osm", osm)
    monkeypatch.setattr(bivouac, "_fetch_tile", lambda x, y: [100.0] * (256 * 256))
    async def enclosing(points, selectors):
        return {"status": "ok", "elements": [], "coverage": {"complete": True}, "issues": [],
                "source": {"name": "Synthetic containing areas"}}

    monkeypatch.setattr(bivouac, "query_enclosing_areas", enclosing)
    return elements


async def _find(**kwargs):
    return await bivouac.find_bivouac_candidates(
        _POINTS, _DISTANCES, min_path_distance_m=10, max_path_distance_m=35, **kwargs)


def _way(identifier, tags, coords):
    return {"type": "way", "id": identifier, "tags": tags,
            "geometry": [{"lat": lat, "lon": lng} for lat, lng in coords]}


async def test_flat_patches_report_unknown_footprint_and_real_spacing(terrain):
    result = await _find()
    assert result["status"] == "ok"
    assert result["items"]
    item = result["items"][0]
    assert item["permission"]["status"] == "unknown"
    assert item["terrain"]["usable_sleeping_area_m2"] == "unknown"
    assert item["terrain"]["dem_source_resolution_m"] == "unknown"
    assert 3 < item["terrain"]["raster_spacing_m"]["min"] < 4
    assert item["terrain"]["max_sampled_slope_deg"] == 0
    assert item["area_bounds"]["south"] < item["area_bounds"]["north"]
    assert item["encounters"]
    assert item["id"] == (await _find())["items"][0]["id"]


async def test_all_mapped_paths_not_only_source_route_are_excluded(terrain):
    assert (await _find())["items"]
    # Dense unrelated north/south footways cover this small inspection corridor.
    for index in range(-8, 9):
        lng = 10 + index * 0.0001
        terrain.append(_way(index, {"highway": "footway"}, [(46.999, lng), (47.001, lng)]))
    result = await _find()
    assert result["status"] == "ok"
    assert result["items"] == []


@pytest.mark.parametrize("tags", [
    {"access": "private"}, {"building": "yes"}, {"natural": "water"},
    {"highway": "pedestrian", "area": "yes"},
    {"boundary": "protected_area"}, {"leisure": "nature_reserve"},
])
async def test_enclosing_exclusion_polygons_remove_flat_patches(terrain, tags):
    terrain.append(_way(1, tags, [(46.999, 9.999), (46.999, 10.001),
                                (47.001, 10.001), (47.001, 9.999), (46.999, 9.999)]))
    assert (await _find())["items"] == []


async def test_malformed_exclusion_geometry_fails_closed(terrain):
    terrain.append({"type": "way", "tags": {"highway": "path"}})
    result = await _find()
    assert result["status"] == "unavailable"
    assert result["items"] == []
    assert "exclusions unavailable" in result["issues"][0]


async def test_osm_outage_prevents_dem_requests(monkeypatch):
    async def unavailable(*args):
        return {"status": "partial", "elements": [], "issues": ["OSM timeout"]}

    def forbidden(*args):
        pytest.fail("DEM must not be queried without complete exclusions")

    monkeypatch.setattr(bivouac, "query_osm", unavailable)
    monkeypatch.setattr(bivouac, "_fetch_tile", forbidden)
    result = await _find()
    assert result["status"] == "unavailable"
    assert result["items"] == []
    assert "OSM timeout" in result["issues"]


async def test_steep_neighboring_cells_rejected(terrain, monkeypatch):
    center_x = int(bivouac._pixel(*_POINTS[0])[0])
    monkeypatch.setattr(bivouac, "_fetch_tile", lambda tx, ty: [
        100 + (tx * 256 + x - center_x) * 3 for y in range(256) for x in range(256)])
    result = await _find()
    assert result["status"] == "ok"
    assert result["items"] == []


async def test_tile_seam_has_no_fabricated_flat_bridge(terrain, monkeypatch):
    tile_x = int(bivouac._pixel(*_POINTS[0])[0]) // 256
    # Place the route directly on a tile seam. Opposite sides are separately flat,
    # but neighboring support at the seam must reject the 100m discontinuity.
    lng = (tile_x * 256) / bivouac._WORLD * 360 - 180
    monkeypatch.setattr(bivouac, "_fetch_tile", lambda tx, ty: [0.0 if tx < tile_x else 100.0] * 65536)
    result = await bivouac.find_bivouac_candidates(
        [[47, lng], [47.0002, lng]], _DISTANCES,
        min_path_distance_m=0, max_path_distance_m=35)
    assert len(result["items"]) == 2
    for item in result["items"]:
        bounds = item["area_bounds"]
        assert bounds["east"] <= lng or bounds["west"] >= lng


@pytest.mark.parametrize("height", [None, float("nan"), float("inf")])
async def test_missing_or_nonfinite_elevations_are_not_flat(terrain, monkeypatch, height):
    monkeypatch.setattr(bivouac, "_fetch_tile", lambda *args: [height] * 65536)
    result = await _find()
    assert result["status"] == "partial"
    assert not result["items"]
    assert result["coverage"]["missing_elevation_neighborhoods"] > 0


async def test_dem_outage_is_explicit(terrain, monkeypatch):
    def unavailable(*args):
        raise OSError("synthetic outage")

    monkeypatch.setattr(bivouac, "_fetch_tile", unavailable)
    result = await _find()
    assert result["status"] == "unavailable"
    assert not result["items"]
    assert result["coverage"]["failed_tiles"] > 0


async def test_budget_refuses_before_network(monkeypatch):
    async def forbidden(*args):
        pytest.fail("Budget excess must be detected before requesting OSM")

    monkeypatch.setattr(bivouac, "query_osm", forbidden)
    result = await bivouac.find_bivouac_candidates([[47, 10], [47.5, 10.5]], [0, 65])
    assert result["status"] == "unavailable"
    assert "budget exceeded" in result["issues"][0]


@pytest.mark.parametrize("point", [[81, 10], [0, 180], [0, -180]])
async def test_unsupported_coordinates_fail_explicitly(point):
    result = await bivouac.find_bivouac_candidates([point], [0])
    assert result["status"] == "unavailable"
    assert "unsupported" in result["issues"][0]


async def test_person_area_requirement_filters_raster_not_footprint(terrain):
    result = await _find(min_area_m2=1_000_000, persons=2)
    assert result["status"] == "ok"
    assert not result["items"]


def _png(color, mode="RGB"):
    buffer = BytesIO()
    Image.new(mode, (256, 256), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_terrarium_decoding_and_void_values():
    assert bivouac._decode_tile(_png((128, 100, 128)))[0] == 100.5
    assert bivouac._decode_tile(_png((0, 0, 0)))[0] is None
    assert bivouac._decode_tile(_png((255, 255, 255)))[0] is None
    assert bivouac._decode_tile(_png((128, 0, 0, 0), "RGBA"))[0] is None
    # Frequently used numerical no-data sentinel, encoded as Terrarium RGB.
    assert bivouac._decode_tile(_png((88, 241, 0)))[0] is None  # -9999
    with pytest.raises(UnidentifiedImageError):
        bivouac._decode_tile(b"not a PNG")


async def test_loop_encounters_preserved(terrain):
    # Restrict one representative patch to a tiny corridor in a repeating route.
    points = [[47, 10], [47.0002, 10], [47, 10], [47.0002, 10]]
    result = await bivouac.find_bivouac_candidates(points, [0, 0.02224, 0.04448, 0.06672],
                                                 min_path_distance_m=0, max_path_distance_m=35)
    assert result["items"]
    assert any(len(item["encounters"]) >= 2 for item in result["items"])


async def test_containing_area_outage_fails_closed(terrain, monkeypatch):
    async def unavailable(*args):
        return {"status": "unavailable", "elements": [], "issues": ["area lookup timeout"]}

    monkeypatch.setattr(bivouac, "query_enclosing_areas", unavailable)
    result = await _find()
    assert result["status"] == "unavailable"
    assert result["items"] == []
    assert "area lookup timeout" in result["issues"]


async def test_generated_containing_area_excludes_candidate(terrain, monkeypatch):
    async def enclosing(*args):
        return {"status": "ok", "elements": [_way(7, {"access": "private"}, [
            (46.99, 9.99), (46.99, 10.01), (47.01, 10.01), (47.01, 9.99), (46.99, 9.99)])],
                "issues": [], "coverage": {"complete": True}}

    monkeypatch.setattr(bivouac, "query_enclosing_areas", enclosing)
    result = await _find()
    assert result["status"] == "ok"
    assert result["items"] == []
