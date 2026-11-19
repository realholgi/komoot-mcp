"""Ordinary map POIs must retain their own Komoot identity."""

from unittest.mock import Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from komoot_mcp import poi_search
from komoot_mcp.geocoder import GeocoderError
from komoot_mcp.routing import waypoint_metadata
from komoot_mcp.server import create_server


@pytest.mark.asyncio
async def test_map_poi_search_returns_linkable_bus_stop_in_read_only_mode(monkeypatch):
    response = Mock()
    response.json.return_value = {"_embedded": {"items": [
        {"name": "Bühlertal Schindelpeter, Bühlertal", "poi_id": "7mn4K",
         "osm_id": "n369831410", "category": 63,
         "point": {"x": 8.1960117, "y": 48.6739408}},
        {"name": "Sportplatz", "osm_id": "w385791779",
         "point": {"x": 8.195079, "y": 48.6740459}},
    ]}}
    monkeypatch.setattr(poi_search.secure_http, "get", Mock(return_value=response))
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_find_pois_nearby", {
            "lat": 48.673941, "lng": 8.196012,
        })
    assert not result.isError
    stop, field = result.structuredContent["data"]["results"]
    assert stop["name"] == "Bühlertal Schindelpeter, Bühlertal"
    assert [stop["lat"], stop["lng"]] == [48.6739408, 8.1960117]
    assert stop["osm_id"] == "n369831410"
    assert waypoint_metadata(None, [stop["waypoint_details"]], 1) == [{"reference": "poi:7mn4K"}]
    assert field["poi_id"] is None
    assert field["waypoint_details"] is None


def test_map_poi_search_does_not_hide_bad_responses_as_no_results(monkeypatch):
    response = Mock()
    response.json.return_value = {"error": "unavailable"}
    monkeypatch.setattr(poi_search.secure_http, "get", Mock(return_value=response))
    with pytest.raises(GeocoderError, match="no item list"):
        poi_search.find_pois_nearby(48, 8)


@pytest.mark.parametrize("options", [{"radius_m": 0}, {"limit": 101}, {"lat": 91}])
def test_invalid_search_is_rejected_before_network(monkeypatch, options):
    get = Mock()
    monkeypatch.setattr(poi_search.secure_http, "get", get)
    with pytest.raises(ValueError):
        poi_search.find_pois_nearby(**({"lat": 48, "lng": 8} | options))
    get.assert_not_called()


def _photon_stop(**changes):
    return {
        "display_name": "Bühlertal Schindelpeter",
        "lat": 48.6739408, "lon": 8.1960117,
        "osm_type": "N", "osm_id": 369831410,
        "osm_key": "highway", "osm_value": "bus_stop",
        **changes,
    }


def test_resolution_matches_osm_type_and_id_not_first_nearby_name():
    nearby = [
        {"osm_id": "w369831410", "poi_id": "wrongWay"},
        {"osm_id": "n99", "poi_id": "wrongNode", "name": "Bühlertal Schindelpeter"},
        {"osm_id": "n369831410", "poi_id": "7mn4K"},
    ]
    result = poi_search.resolve_poi_candidate(_photon_stop(), nearby)
    assert result["resolution_status"] == "resolved"
    assert result["poi_id"] == "7mn4K"
    assert result["waypoint_details"] == {"poi_id": "7mn4K"}
    assert result["komoot_osm_id"] == "n369831410"
    assert result["komoot_matches"] == [nearby[2]]
    assert result["display_name"] == result["name"] == "Bühlertal Schindelpeter"
    assert result["category"] == result["osm_value"] == "bus_stop"
    assert result["osm_id"] == 369831410
    assert [result["lat"], result["lng"]] == [48.6739408, 8.1960117]


@pytest.mark.parametrize(("nearby", "status"), [
    ([], "not_found"),
    ([{"osm_id": "w369831410", "poi_id": "wrongWay"}], "not_found"),
    ([{"osm_id": "n369831410"}], "missing_poi_id"),
    ([{"osm_id": "n369831410", "poi_id": "poi:invented"}], "missing_poi_id"),
    ([{"osm_id": "n369831410", "poi_id": "one"},
      {"osm_id": "n369831410", "poi_id": "two"}], "ambiguous"),
])
def test_resolution_keeps_unresolved_candidates_without_fake_links(nearby, status):
    result = poi_search.resolve_poi_candidate(_photon_stop(), nearby)
    assert result["resolution_status"] == status
    assert result["poi_id"] is None
    assert result["waypoint_details"] is None
    assert result["name"] == "Bühlertal Schindelpeter"
    assert "komoot_matches" in result


@pytest.mark.parametrize(("osm_type", "osm_id"), [
    ("", 369831410), ("node", 369831410), ("N", ""), ("N", True),
    ("N", 369831410.0), ("N", "n369831410"),
])
def test_resolution_does_not_invent_missing_osm_identity(osm_type, osm_id):
    result = poi_search.resolve_poi_candidate(
        _photon_stop(osm_type=osm_type, osm_id=osm_id),
        [{"osm_id": "n369831410", "poi_id": "7mn4K"}],
    )
    assert result["resolution_status"] == "missing_osm_identity"
    assert result["waypoint_details"] is None


def test_duplicate_matches_with_same_poi_are_not_distinct_identities():
    item = {"osm_id": "n369831410", "poi_id": "7mn4K"}
    result = poi_search.resolve_poi_candidate(_photon_stop(), [item, dict(item)])
    assert result["resolution_status"] == "resolved"
    assert result["poi_id"] == "7mn4K"


@pytest.mark.asyncio
async def test_resolve_place_returns_bus_stop_in_read_only_mode_without_credentials(monkeypatch):
    from komoot_mcp.tools import routing_tools

    geo = Mock()
    geo.forward.return_value = [_photon_stop()]
    monkeypatch.setattr(routing_tools, "get_geocoder", lambda: geo)
    monkeypatch.setattr(
        routing_tools, "get_client", Mock(side_effect=AssertionError("account access")),
    )
    response = Mock()
    response.json.return_value = {"_embedded": {"items": [
        {"name": "Nearby first", "osm_id": "n1", "poi_id": "wrong",
         "point": {"x": 8.196, "y": 48.674}},
        {"name": "Bühlertal Schindelpeter, Bühlertal", "osm_id": "n369831410",
         "poi_id": "7mn4K", "category": 63,
         "point": {"x": 8.1960117, "y": 48.6739408}},
    ]}}
    get = Mock(return_value=response)
    monkeypatch.setattr(poi_search.secure_http, "get", get)
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        result = await session.call_tool("komoot_resolve_place", {
            "query": "Bühlertal Schindelpeter", "osm_tag": "highway:bus_stop",
        })
    assert not result.isError
    stop, = result.structuredContent["data"]["results"]
    assert stop["resolution_status"] == "resolved"
    assert waypoint_metadata(None, [stop["waypoint_details"]], 1) == [{"reference": "poi:7mn4K"}]
    geo.forward.assert_called_once_with(
        "Bühlertal Schindelpeter", limit=5, lang="de",
        near_lat=None, near_lng=None, osm_tag="highway:bus_stop",
    )
    assert get.call_args.kwargs["params"]["limit"] == 100
    assert get.call_args.kwargs["params"]["lat"] == 48.6739408
    assert get.call_args.kwargs["params"]["lng"] == 8.1960117
    assert "auth" not in get.call_args.kwargs


@pytest.mark.parametrize("payload", [None, [], {"_embedded": []}, {"_embedded": {"items": [None]}}])
def test_malformed_poi_search_is_not_reported_as_an_empty_inventory(monkeypatch, payload):
    response = Mock()
    response.json.return_value = payload
    monkeypatch.setattr(poi_search.secure_http, "get", Mock(return_value=response))
    with pytest.raises(GeocoderError):
        poi_search.find_pois_nearby(48, 8)


def test_search_cap_is_visible_and_native_poi_fields_are_preserved(monkeypatch):
    item = {"name": "WC", "category": 73, "poi_id": "Ab12", "point": {"x": 8, "y": 48},
            "address_entry": {"city": "Test"}}
    response = Mock()
    response.json.return_value = {"_embedded": {"items": [item]}}
    monkeypatch.setattr(poi_search.secure_http, "get", Mock(return_value=response))
    result = poi_search.find_pois_nearby(48, 8, limit=1)
    assert result["limit_reached"] is True
    assert result["completeness"] == "not_guaranteed"
    assert result["results"][0]["raw"]["address_entry"] == {"city": "Test"}
