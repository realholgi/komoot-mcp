"""Upload contracts: exact GPX/FIT bytes, naming, privacy and duplicate IDs."""

from unittest.mock import AsyncMock

import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootAPIError, KomootClient


GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="49" lon="8.4"><name>Fontaine</name><desc>J W: still working nicely</desc></wpt>
  <trk><name>inline-track</name><trkseg>
    <trkpt lat="49" lon="8.4"><ele>120</ele></trkpt>
    <trkpt lat="49.01" lon="8.41"><ele>125</ele></trkpt>
  </trkseg></trk>
</gpx>"""


@pytest.fixture
def client():
    client = KomootClient(AuthManager(email="test@example.invalid", password="test"), None)
    client._http_request = AsyncMock(return_value=(201, {"id": 999, "status": "private"}))
    return client


@pytest.mark.asyncio
async def test_inline_upload_preserves_poi_names_comments_and_private_visibility(client):
    result = await client.upload_tour(gpx_content=GPX_SAMPLE, sport="hike")
    sent = client._http_request.call_args.kwargs
    assert sent["data"] == GPX_SAMPLE.encode("utf-8")
    assert sent["params"] == {
        "data_type": "gpx", "sport": "hike", "name": "inline-track", "status": "private",
    }
    assert result == {"id": 999, "status": "private", "duplicate": False,
                      "created": True, "import_status": "created", "http_status": 201}


@pytest.mark.asyncio
async def test_inline_content_wins_over_nonexistent_path_and_explicit_name_wins(client, tmp_path):
    await client.upload_tour(
        gpx_content=GPX_SAMPLE, filepath=str(tmp_path / "missing.gpx"),
        tour_name="Vosges du Nord",
    )
    sent = client._http_request.call_args.kwargs
    assert sent["data"] == GPX_SAMPLE.encode("utf-8")
    assert sent["params"]["name"] == "Vosges du Nord"


@pytest.mark.asyncio
async def test_filepath_gpx_preserves_bytes_and_filename(client, tmp_path):
    path = tmp_path / "ride.gpx"
    path.write_bytes(GPX_SAMPLE.encode("utf-8"))
    await client.upload_tour(filepath=str(path))
    sent = client._http_request.call_args.kwargs
    assert sent["data"] == path.read_bytes()
    assert sent["params"]["name"] == "ride"


@pytest.mark.asyncio
@pytest.mark.parametrize("extension,payload", [("fit", b"\x00\xff.FIT\x80"), ("tcx", b"<TrainingCenterDatabase/>")])
async def test_binary_and_tcx_files_are_uploaded_without_sdk_type_conversion(client, tmp_path, extension, payload):
    path = tmp_path / f"ride.{extension}"
    path.write_bytes(payload)
    await client.upload_tour(filepath=str(path))
    sent = client._http_request.call_args.kwargs
    assert sent["data"] == payload
    assert sent["params"]["data_type"] == extension


@pytest.mark.asyncio
async def test_duplicate_is_identified_without_modifying_existing_tour(client):
    client._http_request.return_value = (202, {"id": 123, "name": "Existing tour"})
    result = await client.upload_tour(gpx_content=GPX_SAMPLE, tour_name="New name")
    assert result["id"] == 123
    assert result["name"] == "Existing tour"
    assert result["duplicate"] is True
    assert result["created"] is False
    assert result["import_status"] == "already_exists"
    assert result["http_status"] == 202
    assert client._http_request.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {}, {"filepath": "/nonexistent/ride.gpx"},
    {"gpx_content": "not FIT", "data_type": "fit"},
    {"gpx_content": GPX_SAMPLE, "status": "invalid"},
])
async def test_invalid_uploads_never_reach_komoot(client, kwargs):
    with pytest.raises((KomootAPIError, ValueError)):
        await client.upload_tour(**kwargs)
    client._http_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_without_tour_id_is_not_reported_as_created(client):
    client._http_request.return_value = (201, {})
    with pytest.raises(KomootAPIError, match="did not identify a tour"):
        await client.upload_tour(gpx_content=GPX_SAMPLE)


@pytest.mark.asyncio
async def test_http_rejection_preserves_status_for_the_caller(client):
    client._http_request.side_effect = KomootAPIError("Komoot API error: HTTP 500", status_code=500)
    with pytest.raises(KomootAPIError) as error:
        await client.upload_tour(gpx_content=GPX_SAMPLE)
    assert error.value.status_code == 500
