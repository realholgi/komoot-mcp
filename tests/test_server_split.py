"""The standalone package exposes the complete Komoot MCP interface."""

import subprocess
from pathlib import Path
import sys
import tomllib
from unittest.mock import Mock

import pytest

from komoot_mcp import server


@pytest.mark.asyncio
async def test_importer_preserves_exact_komoot_catalog():
    importer = server.create_server()
    importer_tools = {tool.name: tool for tool in await importer.list_tools()}
    assert importer.name == "komoot-mcp"
    assert {
        "komoot_find_pois_nearby",
        "komoot_resolve_place",
        "komoot_update_planned_tour",
        "komoot_preview_tour_update",
        "komoot_compare_route_variants",
        "komoot_login",
        "komoot_list_tours",
        "komoot_get_tour",
        "komoot_get_tour_full",
        "komoot_get_highlight",
        "komoot_tour_weather",
        "komoot_get_user_profile",
        "komoot_get_tour_photos",
        "komoot_get_tour_line",
        "komoot_list_user_highlights",
        "komoot_get_tour_coordinates",
        "komoot_get_tour_gpx",
        "komoot_get_tour_directions",
        "komoot_get_tour_way_types",
        "komoot_get_tour_surfaces",
        "komoot_get_tour_timeline",
        "komoot_upload_tour",
        "komoot_modify_tour",
        "komoot_delete_tour",
        "komoot_modify_tour_extended",
        "komoot_geocode",
        "komoot_plan_and_upload",
        "komoot_recommend_tours_near",
        "komoot_smart_tours_near",
        "komoot_smart_tour_for_highlight",
        "komoot_discover_with_attributes",
        "komoot_route_attribute_options",
        "komoot_get_highlight_images",
        "komoot_get_highlight_tips",
        "komoot_get_collection",
        "komoot_get_collection_tours",
        "komoot_create_share_link",
        "komoot_revoke_share_link",
        "komoot_resolve_share_url",
        "komoot_download_tour_gpx",
        "komoot_download_tour_fit",
        "komoot_export_all_tours",
        "komoot_find_highlights_nearby",
        "komoot_list_bookmarked_highlights",
        "komoot_bookmark_highlight",
        "komoot_unbookmark_highlight",
        "komoot_get_smart_tour",
        "komoot_download_smart_tour_gpx",
        "komoot_import_gpx_route",
        "komoot_plan_native_route",
        "komoot_create_planned_tour",
        "komoot_plan_and_save_tour",
        "komoot_reverse_geocode",
        "komoot_preview_prepared",
        "komoot_publish_prepared",
        "komoot_adopt_receipt",
    } == importer_tools.keys()


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("read_only", [False, True])
def test_importer_main_preserves_transport_options(monkeypatch, transport, read_only):
    mcp = Mock()
    create = Mock(return_value=mcp)
    run_http = Mock()
    monkeypatch.setattr(sys, "argv", [
        "komoot-mcp", "--transport", transport,
        "--host", "0.0.0.0", "--port", "4321",
    ])
    if read_only:
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--read-only"])
    monkeypatch.setattr(server, "create_server", create)
    monkeypatch.setattr(server, "_run_http", run_http)
    server.main()
    create.assert_called_once_with(host="0.0.0.0", port=4321, read_only=read_only)
    if transport == "http":
        run_http.assert_called_once_with(mcp)
        mcp.run.assert_not_called()
    else:
        run_http.assert_not_called()
        mcp.run.assert_called_once_with()


def test_standalone_install_exposes_only_komoot_entry_point():
    config = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    assert config["project"]["name"] == "komoot-mcp"
    assert config["project"]["scripts"] == {"komoot-mcp": "komoot_mcp.server:main"}


def test_fresh_process_starts_from_another_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-c", """
import asyncio
from komoot_mcp.server import create_server

server = create_server()
assert server.name == "komoot-mcp"
tools = {tool.name for tool in asyncio.run(server.list_tools())}
assert {"komoot_resolve_place", "komoot_update_planned_tour"} <= tools
"""],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
