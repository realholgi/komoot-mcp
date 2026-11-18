"""Offline, consumer-visible contracts for highlight and Smart Tour tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from komoot_mcp.client import KomootAPIError
from komoot_mcp.tools import extended_discovery_tools


@pytest.fixture
def tools(monkeypatch):
    registry = {}

    class Recorder:
        def tool(self):
            def register(fn):
                registry[fn.__name__] = fn
                return fn
            return register

    client = SimpleNamespace(
        _basic_auth=Mock(return_value=("user-123", "unused")),
        _komoot_get=AsyncMock(return_value=[]),
        _komoot_delete=AsyncMock(return_value=""),
        _http_request=AsyncMock(return_value=b"<gpx/>"),
    )
    monkeypatch.setattr(extended_discovery_tools, "get_client", lambda: client)
    extended_discovery_tools.register(Recorder())
    return registry, client


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,end", [(30, 29), (0, 0), (-5, 0), (200, 99)])
async def test_nearby_radius_limit_and_sport_filter(tools, limit, end):
    registry, client = tools
    client._komoot_get.return_value = [
        {"id": 1, "sport": "hike"}, {"id": 2, "sport": "mtb"}, {"id": 3},
    ]
    result = await registry["komoot_find_highlights_nearby"](
        48.1, 11.5, radius_m=1234, sport="mtb", limit=limit,
    )
    assert result == {"highlights": [{"id": 2, "sport": "mtb"}]}
    client._komoot_get.assert_awaited_once_with(
        "https://api.komoot.de/v006/user_highlights/",
        params={"lat": 48.1, "lon": 11.5, "max_distance": 1234,
                "items_range": f"0-{end}", "srid": 4326},
    )


@pytest.mark.asyncio
async def test_nearby_default_parameters_and_empty_lists(tools):
    registry, client = tools
    assert await registry["komoot_find_highlights_nearby"](1, 2) == {"highlights": []}
    assert client._komoot_get.call_args.kwargs["params"]["max_distance"] == 5000
    client._komoot_get.return_value = [{"id": 1, "sport": "hike"}]
    assert await registry["komoot_find_highlights_nearby"](1, 2) == {"highlights": [{"id": 1, "sport": "hike"}]}
    assert await registry["komoot_find_highlights_nearby"](1, 2, sport="mtb") == {"highlights": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,end", [(100, 99), (200, 99), (1, 0), (0, 0)])
async def test_recent_bookmarks_endpoint_and_limit(tools, limit, end):
    registry, client = tools
    assert await registry["komoot_list_bookmarked_highlights"](limit=limit) == {"highlights": []}
    client._komoot_get.assert_awaited_once_with(
        "https://api.komoot.de/v006/users/user-123/bookmarked_user_highlights/recent/",
        params={"srid": 4326, "items_range": f"0-{end}",
                "fields": "start_point,mid_point,images"},
    )
    client._basic_auth.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {}, {"items": []}, "invalid", [1]])
@pytest.mark.parametrize("name,args", [
    ("komoot_find_highlights_nearby", (1, 2)),
    ("komoot_list_bookmarked_highlights", ()),
])
async def test_highlight_unexpected_shapes_are_errors(tools, response, name, args):
    registry, client = tools
    client._komoot_get.return_value = response
    with pytest.raises(ValueError, match="unexpected highlight list"):
        await registry[name](*args)


@pytest.mark.asyncio
async def test_bookmark_and_unbookmark_side_effect_endpoints(tools):
    registry, client = tools
    assert await registry["komoot_bookmark_highlight"](42) == {"highlight_id": 42, "bookmarked": True}
    client._http_request.assert_awaited_once_with(
        "POST", "https://api.komoot.de/v006/users/user-123/bookmarked_user_highlights/42",
        expect_json=False,
    )
    assert await registry["komoot_unbookmark_highlight"](42) == {"highlight_id": 42, "bookmarked": False}
    client._komoot_delete.assert_awaited_once_with(
        "https://api.komoot.de/v006/users/user-123/bookmarked_user_highlights/42",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(409, "conflict"), (400, "already bookmarked")])
async def test_duplicate_bookmark_is_idempotent(tools, status, body):
    registry, client = tools
    client._http_request.side_effect = KomootAPIError("duplicate", status_code=status, body=body)
    assert await registry["komoot_bookmark_highlight"](42) == {
        "highlight_id": 42, "bookmarked": True, "already": True,
    }


@pytest.mark.asyncio
async def test_missing_bookmark_is_already_removed(tools):
    registry, client = tools
    client._komoot_delete.side_effect = KomootAPIError("missing", status_code=404)
    assert await registry["komoot_unbookmark_highlight"](42) == {"highlight_id": 42, "bookmarked": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("name,method,args", [
    ("komoot_bookmark_highlight", "_http_request", (42,)),
    ("komoot_unbookmark_highlight", "_komoot_delete", (42,)),
    ("komoot_find_highlights_nearby", "_komoot_get", (1, 2)),
    ("komoot_list_bookmarked_highlights", "_komoot_get", ()),
    ("komoot_get_smart_tour", "_komoot_get", (42,)),
    ("komoot_download_smart_tour_gpx", "_http_request", (42,)),
])
async def test_unexpected_api_errors_propagate(tools, name, method, args):
    registry, client = tools
    error = KomootAPIError("unavailable", status_code=503, body="unavailable")
    getattr(client, method).side_effect = error
    with pytest.raises(KomootAPIError) as caught:
        await registry[name](*args)
    assert caught.value is error


@pytest.mark.asyncio
async def test_smart_tour_details_are_not_summarized(tools):
    registry, client = tools
    data = {"id": 42, "distance": 12000, "waypoints": [{"lat": 1, "lng": 2}]}
    client._komoot_get.return_value = data
    assert await registry["komoot_get_smart_tour"](42) == data
    client._komoot_get.assert_awaited_once_with("https://api.komoot.de/v007/discover_tours/42")


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, [], "not a tour"])
async def test_smart_tour_malformed_response_is_error(tools, response):
    registry, client = tools
    client._komoot_get.return_value = response
    with pytest.raises(ValueError, match="unexpected Smart Tour"):
        await registry["komoot_get_smart_tour"](42)


@pytest.mark.asyncio
async def test_smart_gpx_inline_is_utf8(tools):
    registry, client = tools
    xml = '<gpx><name>Höhe</name></gpx>'
    client._http_request.return_value = xml.encode("utf-8")
    assert await registry["komoot_download_smart_tour_gpx"](42) == {
        "smart_tour_id": 42, "gpx_content": xml, "size_bytes": len(xml.encode("utf-8")), "truncated": False,
    }
    client._http_request.assert_awaited_once_with(
        "GET", "https://api.komoot.de/v007/smart_tours/42.gpx", raw=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["<gpx/>", None, {}])
async def test_smart_gpx_requires_bytes(tools, response):
    registry, client = tools
    client._http_request.return_value = response
    with pytest.raises(ValueError, match="unexpected GPX"):
        await registry["komoot_download_smart_tour_gpx"](42)


@pytest.mark.asyncio
async def test_smart_gpx_creates_parent_directories(tools, tmp_path):
    registry, client = tools
    path = tmp_path / "nested" / "route.gpx"
    result = await registry["komoot_download_smart_tour_gpx"](42, save_path=str(path))
    assert result == {"path": str(path), "size_bytes": len(b"<gpx/>")}
    assert path.read_bytes() == b"<gpx/>"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.asyncio
async def test_smart_gpx_directory_and_explicit_overwrite(tools, tmp_path):
    registry, client = tools
    download = registry["komoot_download_smart_tour_gpx"]
    await download(42, save_path=str(tmp_path))
    path = tmp_path / "smart_42.gpx"
    assert path.read_bytes() == b"<gpx/>"
    client._http_request.return_value = b"<gpx><name>New</name></gpx>"
    with pytest.raises(FileExistsError):
        await download(42, save_path=str(tmp_path))
    assert path.read_bytes() == b"<gpx/>"
    assert list(tmp_path.iterdir()) == [path]
    await download(42, save_path=str(tmp_path), overwrite=True)
    assert path.read_bytes() == b"<gpx><name>New</name></gpx>"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_smart_gpx_trailing_slash_creates_directory(tools, tmp_path):
    registry, _ = tools
    directory = tmp_path / "exports"
    await registry["komoot_download_smart_tour_gpx"](42, save_path=f"{directory}/")
    assert (directory / "smart_42.gpx").read_bytes() == b"<gpx/>"


@pytest.mark.asyncio
async def test_failed_atomic_publish_leaves_original_intact(tools, tmp_path, monkeypatch):
    registry, _ = tools
    path = tmp_path / "route.gpx"
    path.write_bytes(b"original")

    def fail_replace(*args):
        raise OSError("publish failed")

    monkeypatch.setattr("komoot_mcp.artifacts.os.replace", fail_replace)
    with pytest.raises(OSError, match="publish failed"):
        await registry["komoot_download_smart_tour_gpx"](42, save_path=str(path), overwrite=True)
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]
