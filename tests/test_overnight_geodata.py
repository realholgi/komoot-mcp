"""Incomplete OSM responses must not become complete overnight inventories."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from komoot_mcp import overnight_geodata, secure_http


def _response(payload):
    return SimpleNamespace(content=b"{}", raise_for_status=lambda: None, json=lambda: payload)


@pytest.fixture
def transport(monkeypatch):
    limiter = SimpleNamespace(acquire=AsyncMock())
    monkeypatch.setattr(overnight_geodata, "get_rate_limiter", lambda: limiter)
    post = Mock()
    monkeypatch.setattr(secure_http, "post", post)
    return post


async def test_overpass_partial_query_cannot_be_reported_as_empty_complete_inventory(transport):
    transport.return_value = _response({"elements": [], "remark": "runtime error: Query timed out"})
    result = await overnight_geodata.query_osm([[47, 10], [47.01, 10]], 100, ['["tourism"="camp_site"]'])
    assert result["status"] == "unavailable"
    assert result["elements"] == []
    assert result["coverage"]["failed"] == 1
    assert "Query timed out" in result["issues"][0]


async def test_failed_chunk_retains_successful_candidates_as_partial(transport, monkeypatch):
    monkeypatch.setattr(overnight_geodata, "_CHUNK_POINTS", 2)
    element = {"type": "node", "id": 1, "lat": 47, "lon": 10, "tags": {"tourism": "hotel"}}
    transport.side_effect = [_response({"elements": [element]}), requests.Timeout("read timeout")]
    result = await overnight_geodata.query_osm([[47, 10], [47.01, 10], [47.02, 10]], 100, ['["tourism"="hotel"]'])
    assert result["status"] == "partial"
    assert result["elements"] == [element]
    assert result["coverage"] == {"planned": 2, "completed": 1, "failed": 1}


async def test_enclosing_area_lookup_outage_does_not_establish_no_restrictions(transport):
    transport.side_effect = requests.Timeout("area lookup timeout")
    result = await overnight_geodata.query_enclosing_areas([[47, 10]], ['["access"="private"]'])
    assert result["status"] == "unavailable"
    assert result["coverage"]["completed"] == 0
    assert "area lookup timeout" in result["issues"][0]
