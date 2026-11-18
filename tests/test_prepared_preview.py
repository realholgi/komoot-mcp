"""Read-only preflight through the same checks used before publication."""

import json
import threading
from unittest.mock import AsyncMock

import pytest

from komoot_mcp.publication import Publication
from komoot_mcp.tools import publication_tools


@pytest.fixture
def artifacts(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    manifest = root / "prepared.json"
    manifest.write_text(json.dumps({"schema_version": 1, "stage_id": 1,
        "comments_coverage": "cache-only", "routes": [{"id": "1", "source_route_id": "source-1",
        "name": "Stage 1", "description": "Notes", "gpx": "route.gpx", "details": "details.zip",
        "aliases": {"source:42": "Source"}}]}))
    (root / "route.gpx").write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1">'
        '<wpt lat="48" lon="7"><name>Source</name><desc>Water uncertain</desc><type>Source</type></wpt>'
        '<trk><trkseg><trkpt lat="48" lon="7"/><trkpt lat="48.1" lon="7.1"/></trkseg></trk></gpx>')
    (root / "details.zip").write_bytes(b"local details")
    return manifest


def change_manifest(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


def receipt(directory, artifacts, **tour_changes):
    directory.mkdir(exist_ok=True)
    tour = {"route_id": "1", "tour_id": 42, "verification": "private-planned-waypoints-verified", **tour_changes}
    path = directory / "receipt.json"
    path.write_text(json.dumps({"schema_version": 1, "prepared_path": str(artifacts), "tours": [tour]}))
    return path


def test_valid_preview_is_local_and_does_not_create_publication_directory(tmp_path, artifacts):
    directory = tmp_path / "new" / "publication"
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = Publication(directory).preview(artifacts)
    assert report["ready"] is True and report["issues"] == []
    assert report["scope"] == "local_preflight_only" and report["live_komoot_verified"] is False
    assert report["action"] == "create" and report["requested_visibility"] == "private"
    assert report["comments_coverage"] == "cache-only"
    route = report["routes"][0]
    assert (route["id"], route["name"], route["source_route_id"]) == ("1", "Stage 1", "source-1")
    assert route["source_poi_ids"] == ["source:42"]
    assert route["poi_count"] == 1 and route["track_point_count"] == 2
    assert route["text_limit_findings"] == []
    assert route["gpx_path"] == str(artifacts.parent / "route.gpx")
    assert not directory.exists()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("change,code", [
    (lambda v: v["routes"][0].update(gpx="../outside.gpx"), "artifact_path"),
    (lambda v: v["routes"][0].update(details="/outside.zip"), "artifact_path"),
    (lambda v: v["routes"][0].update(aliases={"other": "Absent"}), "alias_identity"),
    (lambda v: v["routes"][0].update(aliases={"a": "Source", "b": "Source"}), "alias_identity"),
    (lambda v: v["routes"][0].update(aliases={str(i): str(i) for i in range(513)}), "poi_limit"),
])
def test_invalid_artifacts_report_issues(tmp_path, artifacts, change, code):
    change_manifest(artifacts, change)
    report = Publication(tmp_path / "publication").preview(artifacts)
    assert not report["ready"]
    assert code in {issue["code"] for issue in report["issues"]}


@pytest.mark.parametrize("old,new,code", [
    ('lat="48"', 'lat="nan"', "invalid_coordinates"),
    ('lon="7"', 'lon="181"', "invalid_coordinates"),
    ('<name>Source</name>', '<name>' + "😀" * 16 + '</name>', "text_limit"),
    ('<desc>Water uncertain</desc>', '<desc>' + "😀" * 126 + '</desc>', "text_limit"),
    ('</gpx>', '', "invalid_gpx"),
])
def test_gpx_validation_uses_utf16_and_finite_coordinates(tmp_path, artifacts, old, new, code):
    gpx = artifacts.parent / "route.gpx"
    gpx.write_text(gpx.read_text().replace(old, new))
    report = Publication(tmp_path / "publication").preview(artifacts)
    assert not report["ready"]
    assert code in {issue["code"] for issue in report["issues"]}


def test_symlink_escape_is_rejected(tmp_path, artifacts):
    outside = tmp_path / "outside.gpx"
    outside.write_text((artifacts.parent / "route.gpx").read_text())
    (artifacts.parent / "route.gpx").unlink()
    (artifacts.parent / "route.gpx").symlink_to(outside)
    assert Publication(tmp_path / "publication").preview(artifacts)["issues"][0]["code"] == "artifact_path"


@pytest.mark.parametrize("change", [
    lambda v: v["routes"].append(dict(v["routes"][0])),
    lambda v: v.update(routes={}),
    lambda v: v["routes"][0].update(aliases=[]),
    lambda v: v["routes"][0].update(name=42),
    lambda v: v.update(comments_coverage=None),
])
def test_malformed_manifest_raises_value_error(tmp_path, artifacts, change):
    change_manifest(artifacts, change)
    with pytest.raises(ValueError):
        Publication(tmp_path / "publication").preview(artifacts)


def test_pending_and_lock_are_read_only_blockers(tmp_path, artifacts):
    directory = tmp_path / "publication"
    directory.mkdir()
    (directory / "pending.json").write_text("uncertain")
    (directory / ".lock").write_text("123")
    before = {p: p.read_bytes() for p in directory.iterdir()}
    report = Publication(directory).preview(artifacts)
    assert {i["code"] for i in report["issues"]} == {"pending_operation", "publication_locked"}
    assert {p: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize("kind,code", [
    ("missing", "missing_receipts"), ("duplicate", "unverified_receipt"),
    ("unverified", "unverified_receipt"), ("old_missing", "old_preparation"),
    ("route", "route_identity"), ("poi", "poi_identity"),
])
def test_update_blockers(tmp_path, artifacts, kind, code):
    directory = tmp_path / "publication"
    if kind != "missing":
        path = receipt(directory, artifacts, verification="pending" if kind == "unverified" else "private-planned-waypoints-verified")
        if kind == "duplicate":
            (directory / "duplicate.json").write_bytes(path.read_bytes())
        elif kind == "old_missing":
            value = json.loads(path.read_text())
            value["prepared_path"] = str(tmp_path / "absent.json")
            path.write_text(json.dumps(value))
        elif kind in {"route", "poi"}:
            old = artifacts.parent / "old.json"
            old.write_bytes(artifacts.read_bytes())
            value = json.loads(path.read_text())
            value["prepared_path"] = str(old)
            path.write_text(json.dumps(value))
            change_manifest(artifacts, lambda v: v["routes"][0].update(
                **({"id": "new"} if kind == "route" else {"aliases": {"new-source": "Source"}})))
    report = Publication(directory).preview(artifacts, update=True)
    assert not report["ready"] and report["action"] == "update"
    assert code in {issue["code"] for issue in report["issues"]}


def test_valid_update_is_not_live_verified(tmp_path, artifacts):
    directory = tmp_path / "publication"
    receipt(directory, artifacts)
    report = Publication(directory).preview(artifacts, update=True)
    assert report["ready"] and not report["live_komoot_verified"]
    assert not Publication(directory).preview(artifacts)["ready"]


@pytest.mark.asyncio
async def test_invalid_artifacts_block_all_remote_calls_before_intent(tmp_path, artifacts, monkeypatch):
    change_manifest(artifacts, lambda v: v["routes"][0].update(details="../escape.zip"))
    remote = AsyncMock()
    monkeypatch.setattr("komoot_mcp.publication.komoot_import.import_prepared", remote)
    directory = tmp_path / "publication"
    with pytest.raises(ValueError, match="manifest directory"):
        await Publication(directory).publish(artifacts, AsyncMock())
    remote.assert_not_called()
    assert not (directory / "pending.json").exists()


@pytest.mark.asyncio
async def test_preview_adapter_offloads_without_client_or_mutation(tmp_path, artifacts, monkeypatch):
    class Registry:
        def __init__(self):
            self.tools = {}
        def tool(self):
            def add(fn):
                self.tools[fn.__name__] = fn
                return fn
            return add

    registry = Registry()
    publication_tools.register(registry)
    main_thread = threading.get_ident()
    original = Publication.preview

    def preview(self, *args, **kwargs):
        assert threading.get_ident() != main_thread
        return original(self, *args, **kwargs)

    def forbidden():
        pytest.fail("Preview must not authenticate or construct a client")

    monkeypatch.setattr(Publication, "preview", preview)
    monkeypatch.setattr(publication_tools, "get_client", forbidden)
    directory = tmp_path / "publication"
    result = await registry.tools["komoot_preview_prepared"](str(artifacts), str(directory))
    assert result["ready"] and not directory.exists()


def test_duplicate_source_identity_in_manifest_json_is_rejected(tmp_path, artifacts):
    artifacts.write_text(artifacts.read_text().replace(
        '"source:42": "Source"', '"source:42": "Source", "source:42": "Other"'))
    with pytest.raises(ValueError, match="Duplicate manifest key"):
        Publication(tmp_path / "publication").preview(artifacts)


def test_duplicate_gpx_waypoints_are_rejected(tmp_path, artifacts):
    gpx = artifacts.parent / "route.gpx"
    gpx.write_text(gpx.read_text().replace(
        "<trk>", '<wpt lat="48" lon="7"><name>Source</name><type>Source</type></wpt><trk>'))
    report = Publication(tmp_path / "publication").preview(artifacts)
    assert not report["ready"]
    assert "alias_identity" in {issue["code"] for issue in report["issues"]}


def test_empty_or_invalid_receipts_are_reported(tmp_path, artifacts):
    directory = tmp_path / "publication"
    directory.mkdir()
    path = directory / "receipt.json"
    path.write_text(json.dumps({"schema_version": 1, "tours": []}))
    report = Publication(directory).preview(artifacts, update=True)
    assert "empty_receipt" in {issue["code"] for issue in report["issues"]}
    path.write_text('{"schema_version": 1, "tours": [{"route_id": "1", "tour_id": false}]}')
    report = Publication(directory).preview(artifacts, update=True)
    assert "invalid_receipt" in {issue["code"] for issue in report["issues"]}


@pytest.mark.asyncio
async def test_all_update_receipts_are_checked_before_any_remote_mutation(tmp_path, artifacts, monkeypatch):
    directory = tmp_path / "publication"
    receipt(directory, artifacts)
    (directory / "later.json").write_text(json.dumps({
        "schema_version": 1, "prepared_path": str(tmp_path / "absent.json"),
        "tours": [{"route_id": "other", "tour_id": 99,
                   "verification": "private-planned-waypoints-verified"}]}))
    remote = AsyncMock()
    client = AsyncMock()
    monkeypatch.setattr("komoot_mcp.publication.komoot_import.update_prepared", remote)
    with pytest.raises(ValueError, match="old preparation"):
        await Publication(directory).publish(artifacts, client, update=True)
    remote.assert_not_called()
    assert client.mock_calls == []
    assert not (directory / "pending.json").exists()
