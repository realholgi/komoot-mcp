"""Consumer-visible export contracts; all network access is replaced offline."""

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from komoot_mcp.tools import export_tools


@pytest.fixture
def tools(monkeypatch):
    registry = {}

    class MCP:
        def tool(self):
            def register(fn):
                registry[fn.__name__] = fn
                return fn
            return register

    client = SimpleNamespace(_http_request=AsyncMock(), _basic_auth=Mock(return_value=("42", "secret")))
    monkeypatch.setattr(export_tools, "get_client", lambda: client)
    export_tools.register(MCP())
    return registry, client


@pytest.mark.asyncio
async def test_gpx_inline_and_shared_request(tools):
    registry, client = tools
    payload = '<?xml version="1.0"?><gpx>Grüße</gpx>\r\n'
    client._http_request.return_value = payload.encode()
    result = await registry["komoot_download_tour_gpx"](17, share_token="shared")
    assert result == {"tour_id": 17, "gpx_content": payload, "size_bytes": len(payload.encode("utf-8")), "truncated": False}
    client._http_request.assert_awaited_once_with(
        "GET", "https://api.komoot.de/v007/tours/17.gpx", params={"share_token": "shared"}, raw=True,
    )


@pytest.mark.asyncio
async def test_inline_fit_contains_every_byte(tools):
    registry, client = tools
    payload = bytes(range(256)) + b"\x00\xff.FIT"
    client._http_request.return_value = payload
    result = await registry["komoot_download_tour_fit"](17)
    assert result["encoding"] == "base64"
    assert base64.b64decode(result["data"], validate=True) == payload
    assert result["size_bytes"] == len(payload)
    assert result["filename"] == "17.fit"
    assert result["media_type"] == "application/octet-stream"
    assert client._http_request.call_args.args[1] == "https://api.komoot.de/v007/tours/17.fit"
    assert client._http_request.call_args.kwargs["raw"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,payload", [("gpx", b"<gpx>\r\n</gpx>"), ("fit", bytes(range(256)))])
async def test_saved_bytes_no_clobber_and_explicit_overwrite(tools, tmp_path, fmt, payload):
    registry, client = tools
    download = registry[f"komoot_download_tour_{fmt}"]
    client._http_request.return_value = payload
    directory = tmp_path / "new"
    result = await download(17, save_path=str(directory) + "/")
    target = directory / f"17.{fmt}"
    assert result == {"path": str(target), "size_bytes": len(payload)}
    assert target.read_bytes() == payload
    client._http_request.return_value = b"replacement"
    with pytest.raises(FileExistsError):
        await download(17, save_path=str(target))
    assert target.read_bytes() == payload
    await download(17, save_path=str(target), overwrite=True)
    assert target.read_bytes() == b"replacement"
    assert list(directory.iterdir()) == [target]


@pytest.mark.asyncio
async def test_existing_directory_uses_default_name(tools, tmp_path):
    registry, client = tools
    client._http_request.return_value = b"fit"
    result = await registry["komoot_download_tour_fit"](5, save_path=str(tmp_path))
    assert Path(result["path"]) == tmp_path / "5.fit"
    assert (tmp_path / "5.fit").read_bytes() == b"fit"


@pytest.mark.asyncio
async def test_single_api_errors_propagate(tools):
    registry, client = tools
    client._http_request.side_effect = RuntimeError("FIT unavailable")
    with pytest.raises(RuntimeError, match="FIT unavailable"):
        await registry["komoot_download_tour_fit"](5)


@pytest.mark.asyncio
async def test_bulk_paging_skips_failures_and_dates(tools, tmp_path):
    registry, client = tools
    (tmp_path / "1_Already_here.gpx").write_bytes(b"keep")
    pages = [
        {"_embedded": {"tours": [{"id": 1, "name": "Already here"}, {"id": 2, "name": "River / trail"}]}, "page": {"totalPages": 2}},
        {"_embedded": {"tours": [{"id": 3, "name": "Bad"}, {"id": 4, "name": "Good"}]}, "page": {"totalPages": 2}},
    ]

    async def request(method, url, **kwargs):
        if url.endswith("/tours/"):
            return pages[kwargs["params"]["page"]]
        if url.endswith("/3.gpx"):
            raise RuntimeError("export denied")
        return b"\x00exact\r\n"

    client._http_request.side_effect = request
    result = await registry["komoot_export_all_tours"](
        str(tmp_path), tour_type="tour_recorded", sport_types="hike,bike",
        start_date="2026-01-01", end_date="2026-01-31",
    )
    assert result["written"] == 2
    assert result["skipped_existing"] == 1
    assert result["processed"] == 4
    assert result["failures"] == {"3": "export denied"}
    assert result["files"] == ["2_River_trail.gpx", "4_Good.gpx"]
    assert (tmp_path / "1_Already_here.gpx").read_bytes() == b"keep"
    assert (tmp_path / "2_River_trail.gpx").read_bytes() == b"\x00exact\r\n"
    calls = client._http_request.call_args_list
    listings = [call for call in calls if call.args[1].endswith("/tours/")]
    assert [call.kwargs["params"]["page"] for call in listings] == [0, 1]
    assert listings[0].args == ("GET", "https://api.komoot.de/v007/users/42/tours/")
    assert listings[0].kwargs["params"] == {
        "type": "tour_recorded", "sport_types": "hike,bike", "start_date": "2026-01-01T00:00:00.000Z",
        "end_date": "2026-01-31T23:59:59.999Z", "page": 0, "limit": 100,
    }
    assert not any(call.args[1].endswith("/1.gpx") for call in calls)
    client._basic_auth.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [{}, {"_embedded": None}, {"_embedded": {}}, {"_embedded": {"tours": None}}, {"_embedded": {"tours": []}, "page": None}])
async def test_bulk_empty_hal(tools, tmp_path, empty):
    registry, client = tools
    client._http_request.return_value = empty
    result = await registry["komoot_export_all_tours"](str(tmp_path))
    assert result["processed"] == result["written"] == result["skipped_existing"] == 0
    assert result["failures"] == {}
    client._http_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_bulk_missing_page_counts_continues_until_empty(tools, tmp_path):
    registry, client = tools
    pages = [{"_embedded": {"tours": [{"id": 1}]}}, {"_embedded": {"tours": [{"id": 2}]}}, {}]

    async def request(method, url, **kwargs):
        return pages[kwargs["params"]["page"]] if url.endswith("/tours/") else b"data"

    client._http_request.side_effect = request
    result = await registry["komoot_export_all_tours"](str(tmp_path))
    assert result["processed"] == result["written"] == 2
    assert client._http_request.await_count == 5


@pytest.mark.asyncio
async def test_bulk_fit_limit_deduplicates_and_overwrites(tools, tmp_path):
    registry, client = tools
    (tmp_path / "1_tour.fit").write_bytes(b"old")
    pages = [
        {"_embedded": {"tours": [{"id": 1}, {"id": 1}]}, "page": {"totalPages": 0}},
        {"_embedded": {"tours": [{"id": 1}, {"id": 2}, {"id": 3}]}},
    ]

    async def request(method, url, **kwargs):
        if url.endswith("/tours/"):
            assert kwargs["params"]["type"] == "tour_planned"
            assert kwargs["params"]["start_date"] == "2026-01-01T12:00:00Z"
            return pages[kwargs["params"]["page"]]
        assert not url.endswith("/3.fit")
        return b"\xff\x00new"

    client._http_request.side_effect = request
    result = await registry["komoot_export_all_tours"](
        str(tmp_path), fmt="fit", tour_type="tour_recorded", overwrite=True,
        max_tours=2, start_date="2026-01-01T12:00:00Z",
    )
    assert result["processed"] == result["written"] == 2
    assert result["skipped_existing"] == 0
    assert (tmp_path / "1_tour.fit").read_bytes() == b"\xff\x00new"
    assert not (tmp_path / "3_tour.fit").exists()


@pytest.mark.asyncio
async def test_bulk_listing_failure_propagates(tools, tmp_path):
    registry, client = tools
    client._http_request.side_effect = RuntimeError("listing denied")
    with pytest.raises(RuntimeError, match="listing denied"):
        await registry["komoot_export_all_tours"](str(tmp_path))


@pytest.mark.asyncio
async def test_failed_publication_preserves_destination_and_cleans_temporary(tools, tmp_path, monkeypatch):
    registry, client = tools
    target = tmp_path / "tour.fit"
    target.write_bytes(b"original")
    client._http_request.return_value = b"new"

    def fail_replace(source, dest):
        assert Path(source).read_bytes() == b"new"
        raise OSError("publication failed")

    monkeypatch.setattr(export_tools.os, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failed"):
        await registry["komoot_download_tour_fit"](1, save_path=str(target), overwrite=True)
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.asyncio
async def test_bulk_repeated_page_stops_without_double_counting(tools, tmp_path):
    registry, client = tools

    async def request(method, url, **kwargs):
        return {"_embedded": {"tours": [{"id": 1}]}} if url.endswith("/tours/") else b"data"

    client._http_request.side_effect = request
    result = await registry["komoot_export_all_tours"](str(tmp_path))
    assert result["written"] == result["processed"] == 1
    assert client._http_request.await_count == 3


@pytest.mark.asyncio
async def test_zero_limit_and_invalid_arguments_do_not_request(tools, tmp_path):
    registry, client = tools
    result = await registry["komoot_export_all_tours"](str(tmp_path), max_tours=0)
    assert result["processed"] == 0
    with pytest.raises(ValueError, match="fmt"):
        await registry["komoot_export_all_tours"](str(tmp_path), fmt="zip")
    with pytest.raises(ValueError, match="nonnegative"):
        await registry["komoot_export_all_tours"](str(tmp_path), max_tours=-1)
    client._http_request.assert_not_awaited()
    client._basic_auth.assert_not_called()
