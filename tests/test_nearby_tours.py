"""Search account routes through the public read-only MCP interface."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp.client import KomootAPIError
from komoot_mcp.server import create_server
from komoot_mcp.tools import nearby_tour_tools


@pytest.fixture
def account(monkeypatch):
    client = SimpleNamespace(list_tours=AsyncMock(), get_tour_coordinates=AsyncMock())
    monkeypatch.setattr(nearby_tour_tools, "get_client", lambda: client)
    return client


async def search(arguments):
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_find_my_tours_near", arguments)
    assert not result.isError, result
    assert result.structuredContent is not None
    return result.structuredContent["data"]


@pytest.mark.asyncio
async def test_finds_edelfuchs_destination_despite_distant_forbach_start(account):
    account.list_tours.return_value = {"tours": [{
        "id": 3396576187, "name": "von Forbach nach Edelfuxlodge", "type": "tour_planned",
        "start_point": {"lat": 48.680343, "lng": 8.361452},
    }], "page": {"totalPages": 1}}
    account.get_tour_coordinates.return_value = [[48.680343, 8.361452], [48.643932, 8.233631]]
    data = await search({"center": [48.643932, 8.233631], "radius_m": 100})
    assert data["status"] == "ok"
    match = data["tours"][0]
    assert match["id"] == 3396576187
    assert match["proximity"]["distance_m"] == pytest.approx(0, abs=0.001)
    assert match["proximity"]["start_distance_m"] > 9000
    assert match["proximity"]["near"] == ["route", "end"]
    assert data["scan"]["coverage"] == "complete"
    account.list_tours.assert_awaited_once_with(
        page=0, limit=50, tour_type="tour_planned", sport_type=None,
        sort_field="date", sort_direction="desc",
    )


@pytest.mark.asyncio
async def test_finds_segment_midpoint_and_sorts_all_matches_before_output_limit(account):
    account.list_tours.return_value = {"tours": [{"id": n} for n in [1, 2, 3]], "page": {"totalPages": 1}}
    account.get_tour_coordinates.side_effect = [
        [[0.002, 0]], [[0.001, 0]], [[0, -1], [0, 1]],
    ]
    data = await search({"center": [0, 0], "radius_m": 300, "limit": 1})
    assert [tour["id"] for tour in data["tours"]] == [3]
    assert data["tours"][0]["proximity"]["near"] == ["route"]
    assert data["tours"][0]["proximity"]["route_km"] == pytest.approx(111.195, abs=0.01)
    assert data["matched_tours"] == 3
    assert data["results_truncated"] is True
    assert data["scan"]["checked_tours"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("center,points", [
    ([0.001, 180], [[0, 179], [0, -179]]),
    ([85, 0], [[85, 0]]),
    ([0, 0], [[0, 0], [0, 0], [0, 1], [0, 0]]),
])
async def test_wrap_single_point_and_repeated_geometry_are_searchable(account, center, points):
    account.list_tours.return_value = {"tours": [{"id": 7}], "page": {"totalPages": 1}}
    account.get_tour_coordinates.return_value = points
    data = await search({"center": center, "radius_m": 120})
    assert data["tours"][0]["id"] == 7
    assert data["tours"][0]["proximity"]["distance_m"] < 112


@pytest.mark.asyncio
async def test_budget_resume_preserves_mid_page_position(account):
    first = {"tours": [{"id": n} for n in range(1, 51)], "page": {"totalPages": 2}}
    second = {"tours": [{"id": 51}, {"id": 52}], "page": {"totalPages": 2}}
    account.list_tours.side_effect = [first, second]
    account.get_tour_coordinates.return_value = [[0, 0], [0, 1]]
    data = await search({"center": [0, 0], "max_tours": 51, "limit": 100})
    assert data["status"] == "partial"
    assert data["scan"]["scanned_records"] == 51
    assert data["scan"]["next_offset"] == 51
    assert data["scan"]["exhausted"] is False
    account.list_tours.side_effect = [second]
    account.get_tour_coordinates.reset_mock()
    resumed = await search({"center": [0, 0], "offset": 51})
    assert [tour["id"] for tour in resumed["tours"]] == [52]
    assert resumed["scan"]["next_offset"] is None
    assert resumed["scan"]["coverage"] == "partial"
    account.get_tour_coordinates.assert_awaited_once_with(52)


@pytest.mark.asyncio
async def test_unknown_routes_are_reported_not_counted_as_outside_radius(account):
    account.list_tours.return_value = {"tours": [{"id": n} for n in range(1, 5)], "page": {"totalPages": 1}}
    account.get_tour_coordinates.side_effect = [
        [], [[0, 0], [float("nan"), 0]], KomootAPIError("Not available", status_code=404), [[0, 0]],
    ]
    data = await search({"center": [0, 0]})
    assert [tour["id"] for tour in data["tours"]] == [4]
    assert data["status"] == "partial"
    assert [entry["tour_id"] for entry in data["scan"]["failures"]] == [1, 2, 3]
    assert data["scan"]["checked_tours"] == 1
    assert data["scan"]["exhausted"] is True


@pytest.mark.asyncio
async def test_ambiguous_place_does_not_choose_first_or_access_account(monkeypatch, account):
    geocoder = SimpleNamespace(forward=Mock(return_value=[
        {"display_name": "Hundsbach", "lat": 48.63, "lon": 8.3},
        {"display_name": "Hundsbach", "lat": 49.72, "lon": 7.55},
    ]))
    monkeypatch.setattr(nearby_tour_tools, "get_geocoder", lambda: geocoder)
    monkeypatch.setattr(nearby_tour_tools, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))
    data = await search({"query": "Hundsbach"})
    assert data["status"] == "ambiguous_place"
    assert data["places"] == geocoder.forward.return_value
    assert data["scan"]["coverage"] == "not_started"
    account.list_tours.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolved_name_searches_coordinates_without_tour_name_filter(monkeypatch, account):
    place = {"display_name": "Edelfuchs-Lodge", "lat": 48.643932, "lon": 8.233631}
    monkeypatch.setattr(nearby_tour_tools, "get_geocoder", lambda: SimpleNamespace(forward=Mock(return_value=[place])))
    monkeypatch.setattr(nearby_tour_tools, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))
    account.list_tours.return_value = {"tours": [{"id": 42, "name": "Weekend"}], "page": {"totalPages": 1}}
    account.get_tour_coordinates.return_value = [[48.6, 8.3], [48.643932, 8.233631]]
    data = await search({"query": "Edelfuchs-Lodge", "sport_type": "hike", "tour_type": None})
    assert data["place"] == place
    assert data["tours"][0]["id"] == 42
    assert "name" not in account.list_tours.call_args.kwargs
    assert account.list_tours.call_args.kwargs["tour_type"] is None
    assert account.list_tours.call_args.kwargs["sport_type"] == "hike"


@pytest.mark.asyncio
async def test_repeated_pages_do_not_claim_complete_account_coverage(account):
    account.list_tours.return_value = {"tours": [{"id": 42}]}
    account.get_tour_coordinates.return_value = [[0, 0]]
    data = await search({"center": [0, 0]})
    assert data["status"] == "partial"
    assert data["scan"]["issues"]
    assert data["scan"]["checked_tours"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {}, {"center": [0, 0], "query": "Hundsbach"}, {"query": " "},
    {"center": [91, 0]}, {"center": [0, 0], "radius_m": 0},
    {"center": [0, 0], "max_tours": 0}, {"center": [0, 0], "offset": -1},
    {"center": [0, 0], "limit": 101}, {"center": [0, 0], "tour_type": "anything"},
])
async def test_invalid_search_never_accesses_account(account, arguments):
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_find_my_tours_near", arguments)
    assert result.isError
    account.list_tours.assert_not_awaited()
