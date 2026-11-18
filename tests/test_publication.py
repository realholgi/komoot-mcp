"""Importer-owned publication safety without exporter state or MCP dispatch."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from komoot_mcp.context import reset_http_context, set_http_context
from komoot_mcp.publication import Publication
from komoot_mcp.tools import publication_tools


@pytest.fixture
def prepared(tmp_path):
    path = tmp_path / "artifacts" / "prepared.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1, "stage_id": 1, "comments_coverage": "cache-only", "routes": [
        {"id": "1", "name": "Stage 1", "description": "", "gpx": "stage-01.gpx",
         "details": "details.zip", "aliases": {}}]}))
    (path.parent / "stage-01.gpx").write_text(
        '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        '<trkpt lat="48" lon="7"/><trkpt lat="48.1" lon="7.1"/>'
        '</trkseg></trk></gpx>')
    (path.parent / "details.zip").write_bytes(b"immutable details")
    return path


def receipt_file(path, tour_id=123, verified=True, **extra):
    value = {"schema_version": 1, "prepared_path": "/old/prepared.json", "tours": [
        {"route_id": "1", "tour_id": tour_id,
         "verification": "private-planned-waypoints-verified" if verified else "unverified"}], **extra}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.mark.asyncio
async def test_uncertain_publication_is_not_retried(tmp_path, prepared, monkeypatch):
    publication = Publication(tmp_path / "publications")
    calls = []

    async def interrupted(prepared_path, receipt_path, client):
        calls.append((prepared_path, receipt_path, client))
        assert (publication.directory / "pending.json").exists()
        raise ConnectionError("lost response")

    monkeypatch.setattr("komoot_mcp.publication.komoot_import.import_prepared", interrupted)
    with pytest.raises(ConnectionError):
        await publication.publish(prepared, object())
    with pytest.raises(ValueError, match="Unresolved Komoot operation"):
        await publication.publish(prepared, object())
    assert len(calls) == 1
    assert not (publication.directory / ".lock").exists()


@pytest.mark.asyncio
async def test_historical_receipt_blocks_duplicate_import(tmp_path, prepared):
    publication = Publication(tmp_path / "publications")
    source = receipt_file(tmp_path / "receipt.json")
    original = source.read_bytes()
    publication.adopt_receipt(source)
    with pytest.raises(ValueError, match="Existing Komoot receipts"):
        await publication.publish(prepared, object())
    assert source.read_bytes() == original


@pytest.mark.asyncio
async def test_existing_directory_receipts_are_discovered_without_stage_state(tmp_path, prepared):
    directory = tmp_path / "publications"
    receipt_file(directory / "2026-11-17-stage-01-komoot-importbeleg.json")
    (directory / "completed.json").write_text(json.dumps({"prepared_path": "/old", "update": False, "receipts": []}))
    with pytest.raises(ValueError, match="Existing Komoot receipts"):
        await Publication(directory).publish(prepared, object())


@pytest.mark.asyncio
async def test_publish_ignores_completed_intents_and_uses_readable_names(tmp_path, prepared, monkeypatch):
    directory = tmp_path / "publications"
    directory.mkdir()
    (directory / "completed.json").write_text(json.dumps({"prepared_path": "/old", "update": False, "receipts": []}))
    captured = []
    client = object()

    async def imported(prepared_path, receipt_path, supplied_client):
        captured.append(receipt_path)
        assert supplied_client is client
        assert prepared_path == str(prepared)
        receipt_file(Path(receipt_path))
        return {"receipt_path": receipt_path}

    monkeypatch.setattr("komoot_mcp.publication.komoot_import.import_prepared", imported)
    result = await Publication(directory).publish(prepared, client)
    assert result == {"receipt_path": captured[0]}
    assert Path(captured[0]).name.endswith("-stage-01-komoot-importbeleg.json")
    assert not (directory / "pending.json").exists()
    assert len(list(directory.glob("*-stage-01-komoot-import-abgeschlossen.json"))) == 1
    with pytest.raises(ValueError, match="Existing Komoot receipts"):
        await Publication(directory).publish(prepared, client)


def test_adoption_preserves_hash_identity_source_and_collision_names(tmp_path):
    publication = Publication(tmp_path / "publications")
    source = receipt_file(tmp_path / "receipt.json", imported_at="2026-11-17T09:00:00Z")
    original = source.read_bytes()
    first = publication.adopt_receipt(source)
    stored = json.loads(Path(first["receipt_path"]).read_text())
    assert Path(first["receipt_path"]).name == "2026-11-17-stage-01-komoot-importbeleg.json"
    assert stored["receipt_id"] == hashlib.sha256(original).hexdigest()[:24]
    assert stored["prepared_path"] == "/old/prepared.json"
    assert publication.adopt_receipt(source) == first
    assert source.read_bytes() == original
    receipt_file(source, 456, imported_at="2026-11-17T09:00:00Z")
    second = publication.adopt_receipt(source)
    assert Path(second["receipt_path"]).name == "2026-11-17-stage-01-komoot-importbeleg-02.json"


def test_adoption_rebases_without_changing_original_artifacts(tmp_path, prepared):
    publication = Publication(tmp_path / "publications")
    source = receipt_file(tmp_path / "receipt.json")
    original = source.read_bytes()
    artifacts = {path: path.read_bytes() for path in prepared.parent.iterdir()}
    result = publication.adopt_receipt(source, prepared)
    stored = json.loads(Path(result["receipt_path"]).read_text())
    assert stored["original_prepared_path"] == "/old/prepared.json"
    assert stored["prepared_path"] == str(prepared)
    assert source.read_bytes() == original
    assert all(path.read_bytes() == value for path, value in artifacts.items())
    assert publication.adopt_receipt(source, prepared) == result
    with pytest.raises(ValueError, match="different bindings"):
        publication.adopt_receipt(source)


def test_adoption_rejects_mismatched_routes(tmp_path, prepared):
    source = receipt_file(tmp_path / "receipt.json")
    value = json.loads(source.read_text())
    value["tours"][0]["route_id"] = "another-route"
    source.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="routes do not match"):
        Publication(tmp_path / "publications").adopt_receipt(source, prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unverified", "duplicate", "empty", "missing"])
async def test_update_requires_verified_unique_receipts(tmp_path, prepared, kind):
    directory = tmp_path / "publications"
    directory.mkdir()
    if kind != "missing":
        path = receipt_file(directory / "first.json", verified=kind != "unverified")
        if kind == "duplicate":
            receipt_file(directory / "second.json", tour_id="123")
        if kind == "empty":
            path.write_text(json.dumps({"schema_version": 1, "tours": []}))
    with pytest.raises(ValueError, match="unverified|empty historical|No imported"):
        await Publication(directory).publish(prepared, object(), update=True)
    assert not (directory / "pending.json").exists()


@pytest.mark.asyncio
async def test_updates_call_plain_function_and_collect_results(tmp_path, prepared, monkeypatch):
    directory = tmp_path / "publications"
    paths = [receipt_file(directory / "first.json", prepared_path=str(prepared))]
    calls = []
    client = object()

    async def update(receipt_path, prepared_path, supplied_client):
        assert supplied_client is client
        assert prepared_path == str(prepared)
        assert (directory / "pending.json").exists()
        calls.append(receipt_path)
        return {"receipt_path": receipt_path}

    monkeypatch.setattr("komoot_mcp.publication.komoot_import.update_prepared", update)
    result = await Publication(directory).publish(prepared, client, update=True)
    assert calls == [str(path) for path in paths]
    assert result == {"updates": [{"receipt_path": path} for path in calls]}
    assert not (directory / "pending.json").exists()


@pytest.mark.asyncio
async def test_publication_directory_lock_prevents_concurrent_operations(tmp_path, prepared, monkeypatch):
    publication = Publication(tmp_path / "publications")
    entered, release = asyncio.Event(), asyncio.Event()

    async def imported(prepared_path, receipt_path, client):
        entered.set()
        await release.wait()
        receipt_file(Path(receipt_path))
        return {"receipt_path": receipt_path}

    monkeypatch.setattr("komoot_mcp.publication.komoot_import.import_prepared", imported)
    task = asyncio.create_task(publication.publish(prepared, object()))
    await entered.wait()
    try:
        with pytest.raises(ValueError, match="locked"):
            await publication.publish(prepared, object())
        with pytest.raises(ValueError, match="locked"):
            publication.adopt_receipt(tmp_path / "not-read.json")
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_import_rejects_more_than_512_aliases_before_intent(tmp_path, prepared):
    value = json.loads(prepared.read_text())
    value["routes"][0]["aliases"] = {str(i): str(i) for i in range(513)}
    prepared.write_text(json.dumps(value))
    publication = Publication(tmp_path / "publications")
    with pytest.raises(ValueError, match="512"):
        await publication.publish(prepared, object())
    assert not (publication.directory / "pending.json").exists()


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def add(fn):
            self.tools[fn.__name__] = fn
            return fn
        return add


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("komoot_preview_prepared", {"prepared_path": "/not/read", "publication_directory": "/not/read"}),
    ("komoot_publish_prepared", {"prepared_path": "/not/read", "publication_directory": "/not/read", "confirmed": True}),
    ("komoot_publish_prepared", {"prepared_path": "/not/read", "publication_directory": "/not/read"}),
    ("komoot_adopt_receipt", {"receipt_path": "/not/read", "publication_directory": "/not/read"}),
])
async def test_http_rejected_before_paths_or_client(monkeypatch, name, arguments):
    registry = Registry()
    publication_tools.register(registry)

    def forbidden(*args, **kwargs):
        pytest.fail("HTTP publication must not resolve paths or credentials")

    monkeypatch.setattr(publication_tools, "Publication", forbidden)
    monkeypatch.setattr(publication_tools, "get_client", forbidden)
    token = set_http_context()
    try:
        with pytest.raises(PermissionError, match="disabled over HTTP"):
            await registry.tools[name](**arguments)
    finally:
        reset_http_context(token)


@pytest.mark.asyncio
async def test_unconfirmed_publish_rejected_before_paths_or_client(monkeypatch):
    registry = Registry()
    publication_tools.register(registry)

    def forbidden(*args, **kwargs):
        pytest.fail("Unconfirmed publication must not resolve paths or credentials")

    monkeypatch.setattr(publication_tools, "Publication", forbidden)
    monkeypatch.setattr(publication_tools, "get_client", forbidden)
    with pytest.raises(ValueError, match="confirmed=true"):
        await registry.tools["komoot_publish_prepared"]("/not/read", "/not/read")


@pytest.mark.asyncio
async def test_update_interruption_preserves_intent_and_receipts(tmp_path, prepared, monkeypatch):
    directory = tmp_path / "publications"
    receipt = receipt_file(directory / "receipt.json", prepared_path=str(prepared))
    original = receipt.read_bytes()
    calls = []

    async def interrupted(*args):
        calls.append(args)
        raise ConnectionError("uncertain update")

    monkeypatch.setattr("komoot_mcp.publication.komoot_import.update_prepared", interrupted)
    publication = Publication(directory)
    with pytest.raises(ConnectionError):
        await publication.publish(prepared, object(), update=True)
    with pytest.raises(ValueError, match="Unresolved Komoot operation"):
        await publication.publish(prepared, object(), update=True)
    assert len(calls) == 1
    assert receipt.read_bytes() == original


@pytest.mark.asyncio
async def test_legacy_stage_identity_and_512_aliases_remain_importable(tmp_path, prepared, monkeypatch):
    value = json.loads(prepared.read_text())
    del value["stage_id"]
    value["routes"][0]["id"] = "1-part-1"
    value["routes"][0]["source_route_id"] = "1"
    value["routes"][0]["aliases"] = {str(i): str(i) for i in range(512)}
    prepared.write_text(json.dumps(value))
    (prepared.parent / "stage-01.gpx").write_text(
        '<gpx xmlns="http://www.topografix.com/GPX/1/1">'
        + "".join(f'<wpt lat="48" lon="7"><name>{i}</name><type>Source</type></wpt>' for i in range(512))
        + '<trk><trkseg><trkpt lat="48" lon="7"/></trkseg></trk></gpx>')

    async def imported(prepared_path, receipt_path, client):
        assert "-stage-01-" in Path(receipt_path).name
        receipt_file(Path(receipt_path))
        return {"receipt_path": receipt_path}

    monkeypatch.setattr("komoot_mcp.publication.komoot_import.import_prepared", imported)
    await Publication(tmp_path / "publications").publish(prepared, object())


@pytest.mark.asyncio
async def test_confirmed_adapter_passes_explicit_artifacts_and_client(monkeypatch, tmp_path, prepared):
    registry = Registry()
    publication_tools.register(registry)
    client = object()
    calls = []

    async def publish(self, prepared_path, supplied_client, update=False):
        calls.append((self.directory, prepared_path, supplied_client, update))
        return {"updates": []}

    monkeypatch.setattr(publication_tools.Publication, "publish", publish)
    monkeypatch.setattr(publication_tools, "get_client", lambda: client)
    directory = tmp_path / "publications"
    result = await registry.tools["komoot_publish_prepared"](
        str(prepared), str(directory), confirmed=True, update=True)
    assert result == {"updates": []}
    assert calls == [(directory, str(prepared), client, True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [False, True])
async def test_incompatible_prepared_schema_rejected_before_publication(tmp_path, prepared, update):
    from unittest.mock import AsyncMock

    value = json.loads(prepared.read_text())
    value["schema_version"] = 2
    prepared.write_text(json.dumps(value))
    publication = Publication(tmp_path / "publications")
    client = AsyncMock()
    with pytest.raises(ValueError, match="schema_version 1"):
        await publication.publish(prepared, client, update=update)
    assert not (publication.directory / "pending.json").exists()
    assert client.mock_calls == []
