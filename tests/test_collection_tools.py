"""Collection management through the MCP interface, without account mutations."""

import json
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, Mock

from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from komoot_mcp import client as client_module
from komoot_mcp.client import KomootClient
from komoot_mcp.server import create_server
from komoot_mcp.tools import collection_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("komoot_create_collection", {"name": " "}),
    ("komoot_modify_collection", {"collection_id": 42}),
    ("komoot_delete_collection", {"collection_id": 0}),
    ("komoot_add_collection_tours", {"collection_id": 42, "tour_ids": [-1]}),
    ("komoot_reorder_collection_tours", {"collection_id": 42, "tour_ids": [1, 1]}),
])
async def test_invalid_collection_changes_never_reach_account(monkeypatch, name, arguments):
    forbidden = Mock(side_effect=AssertionError("Invalid collection change reached account code"))
    monkeypatch.setattr(collection_tools, "get_client", forbidden)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool(name, arguments)
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert result.structuredContent["error"]["operation_state"] == "not_attempted"
    forbidden.assert_not_called()


_BASE = "https://api.komoot.de/v007"
_COLLECTION = f"{_BASE}/collections/42"
_COMPILATION = f"{_COLLECTION}/compilation/"
_NEXT = f"{_COMPILATION}?cursor=next%2Bpage&limit=2&sort=manual"
_MEMBERS = [
    {"id": 11, "type": "tour_planned"},
    {"id": 90, "type": "highlight"},
    {"id": 12, "type": "tour_recorded"},
    {"id": 91, "type": "highlight"},
    {"id": 13, "type": "tour_planned"},
]
_WRITES = {
    "komoot_create_collection": {"name": "Weekend"},
    "komoot_modify_collection": {"collection_id": 42, "name": "Weekend"},
    "komoot_delete_collection": {"collection_id": 42},
    "komoot_add_collection_tours": {"collection_id": 42, "tour_ids": [14]},
    "komoot_remove_collection_tours": {"collection_id": 42, "tour_ids": [11]},
    "komoot_reorder_collection_tours": {"collection_id": 42, "tour_ids": [13, 12, 11]},
}


class CollectionHTTP:
    """Small remote account: writes change subsequent, independently paged GETs."""

    def __init__(self):
        self.collection = {
            "id": 42, "name": "Original", "intro_plain": "Old introduction",
            "status": "private", "type": "collection_personal", "multi_day": False,
            "_embedded": {"creator": {"username": "7"}},
        }
        self.members = deepcopy(_MEMBERS)
        self.tours = {
            11: {"id": 11, "type": "tour_planned"},
            12: {"id": 12, "type": "tour_recorded"},
            13: {"id": 13, "type": "tour_planned"},
            14: {"id": 14, "type": "tour_recorded"},
        }
        self.calls = []
        self.next_href = _NEXT
        self.broken_page = False
        self.effect = "apply"
        self.created_id = 42
        self.deleted = False

    @property
    def writes(self):
        return [call for call in self.calls if call["method"] != "GET"]

    @staticmethod
    def response(body, status=200):
        snapshot = deepcopy(body)
        return SimpleNamespace(
            ok=status < 400, status_code=status,
            text=json.dumps(snapshot), json=lambda: deepcopy(snapshot),
        )

    def __call__(self, *, method, url, params=None, json=None, auth=None, **_kwargs):
        assert auth == ("7", "test-token")
        self.calls.append({"method": method, "url": url,
                           "params": deepcopy(params), "body": deepcopy(json)})
        path = urlsplit(url).path
        if method != "GET":
            if self.effect == "timeout":
                raise TimeoutError("Connection lost after request transmission")
            if self.effect != "ignore":
                if method in {"POST", "PATCH", "PUT"}:
                    assert isinstance(json, dict)
                if method == "POST" and path == "/v007/collections/":
                    self.collection.update({key: value for key, value in json.items() if key != "_embedded"})
                    self.members = deepcopy(json["_embedded"]["compilation"]["_embedded"]["items"])
                elif method == "PATCH" and url == _COLLECTION:
                    assert isinstance(json, dict)
                    self.collection.update(deepcopy(json))
                elif method == "PUT" and url == _COMPILATION:
                    self.members = deepcopy(json["_embedded"]["items"])
                elif method == "DELETE" and url == _COLLECTION:
                    self.deleted = True
                else:
                    raise AssertionError(f"Unexpected account mutation: {method} {url}")
            return self.response({"id": self.created_id} if method == "POST" else {})
        if self.writes and self.effect == "readback_failure":
            return self.response({"message": "Unavailable"}, 503)
        if url == _COLLECTION:
            return self.response({"message": "Not found"}, 404) if self.deleted else self.response(self.collection)
        if path == "/v007/collections/42/compilation/":
            if url == _COMPILATION:
                page = {
                    "_embedded": {"items": self.members[:2]},
                    "page": {"number": 0, "totalPages": 1, "totalElements": 2},
                    "_links": {"next": {"href": self.next_href}},
                }
            else:
                assert url == _NEXT, f"Native next query changed: {url}"
                assert params is None, "Initial pagination parameters leaked into native next link"
                page = {
                    "_embedded": {"items": self.members[2:]},
                    "page": {"number": 1, "totalPages": 999, "totalElements": 99999},
                }
                if self.broken_page:
                    del page["_embedded"]
            return self.response(page)
        if path.startswith("/v007/tours/") and method == "GET":
            return self.response(self.tours[int(path.rsplit("/", 1)[1])])
        raise AssertionError(f"Unexpected HTTP request: {method} {url}")


@pytest.fixture
def remote(monkeypatch):
    remote = CollectionHTTP()
    account = KomootClient(SimpleNamespace(), SimpleNamespace(acquire=AsyncMock()))
    monkeypatch.setattr(account, "_basic_auth", lambda: ("7", "test-token"))
    monkeypatch.setattr(client_module.requests, "request", remote)
    monkeypatch.setattr(collection_tools, "get_client", lambda: account)
    return remote


async def _call(name, arguments, *, read_only=False):
    async with create_connected_server_and_client_session(create_server(read_only=read_only)) as session:
        return await session.call_tool(name, arguments)


def _data(result):
    assert result.isError is False
    assert result.structuredContent["ok"] is True
    return result.structuredContent["data"]


def _error(result, state, code="upstream_error"):
    assert result.isError is True
    error = result.structuredContent["error"]
    assert error["code"] == code
    assert error["operation_state"] == state
    assert error["retry_safe"] is False
    assert error["collection_id"] == 42
    return error


@pytest.mark.asyncio
async def test_multipage_add_reorder_remove_preserves_exact_types_and_highlight_slots(remote):
    added = _data(await _call("komoot_add_collection_tours", {"collection_id": 42, "tour_ids": [12, 14]}))
    assert added["members"] == [
        {"id": 11, "type": "tour_planned"}, {"id": 90, "type": "highlight"},
        {"id": 12, "type": "tour_recorded"}, {"id": 91, "type": "highlight"},
        {"id": 13, "type": "tour_planned"}, {"id": 14, "type": "tour_recorded"},
    ]
    reordered = _data(await _call("komoot_reorder_collection_tours", {
        "collection_id": 42, "tour_ids": [14, 13, 11, 12],
    }))
    assert reordered["members"] == [
        {"id": 14, "type": "tour_recorded"}, {"id": 90, "type": "highlight"},
        {"id": 13, "type": "tour_planned"}, {"id": 91, "type": "highlight"},
        {"id": 11, "type": "tour_planned"}, {"id": 12, "type": "tour_recorded"},
    ]
    removed = _data(await _call("komoot_remove_collection_tours", {"collection_id": 42, "tour_ids": [13, 12]}))
    assert removed["members"] == [
        {"id": 14, "type": "tour_recorded"}, {"id": 90, "type": "highlight"},
        {"id": 91, "type": "highlight"}, {"id": 11, "type": "tour_planned"},
    ]
    assert len(remote.writes) == 3
    assert all(call["method"] == "PUT" and call["url"] == _COMPILATION for call in remote.writes)
    assert remote.writes[-1]["body"] == {"_embedded": {"items": removed["members"]}}
    assert sum(call["url"] == _NEXT for call in remote.calls) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", [
    "incomplete", "cycle", "https://evil.example/compilation/",
    f"{_BASE}/collections/99/compilation/?page=1",
    "http://api.komoot.de/v007/collections/42/compilation/?page=1",
    f"{_NEXT}#fragment",
])
async def test_untrustworthy_membership_never_sends_replacement(remote, problem):
    if problem == "incomplete":
        remote.broken_page = True
    elif problem == "cycle":
        # The first native page points to itself rather than exhausting.
        original = remote.response
        def cyclic_response(body, status=200):
            if body.get("page", {}).get("number") == 1:
                body["_links"] = {"next": {"href": _NEXT}}
            return original(body, status)
        remote.response = cyclic_response
    else:
        remote.next_href = problem
    _error(await _call("komoot_remove_collection_tours", {"collection_id": 42, "tour_ids": [11]}), "not_attempted")
    assert remote.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tour_ids", [[11, 12], [11, 12, 14]])
async def test_reorder_rejects_incomplete_or_stale_full_order(remote, tour_ids):
    _error(await _call("komoot_reorder_collection_tours", {"collection_id": 42, "tour_ids": tour_ids}),
           "not_attempted", "invalid_input")
    assert remote.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(_WRITES)[1:])
async def test_foreign_collection_is_never_mutated(remote, name):
    remote.collection["_embedded"]["creator"]["username"] = "8"
    _error(await _call(name, _WRITES[name]), "not_attempted", "permission_denied")
    assert remote.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "komoot_add_collection_tours", "komoot_remove_collection_tours", "komoot_reorder_collection_tours",
])
async def test_multi_day_collection_rejects_membership_changes(remote, name):
    remote.collection["multi_day"] = True
    _error(await _call(name, _WRITES[name]), "not_attempted", "invalid_input")
    assert remote.writes == []
    assert len(remote.calls) == 1


@pytest.mark.asyncio
async def test_create_defaults_private_and_verifies_initial_ordered_tours(remote):
    data = _data(await _call("komoot_create_collection", {"name": "Weekend", "tour_ids": [12, 11]}))
    assert data["collection_id"] == 42 and data["verified"] is True
    assert data["collection"]["status"] == "private"
    assert remote.writes == [{
        "method": "POST", "url": f"{_BASE}/collections/", "params": None,
        "body": {
            "name": "Weekend", "intro_plain": "", "status": "private", "type": "collection_personal",
            "_embedded": {"compilation": {"_embedded": {"items": [
                {"id": 12, "type": "tour_recorded"}, {"id": 11, "type": "tour_planned"},
            ]}}},
        },
    }]
    assert remote.calls[-1]["url"] == _NEXT


@pytest.mark.asyncio
async def test_multi_day_metadata_description_clear_and_visibility_do_not_change_tours(remote):
    remote.collection["multi_day"] = True
    changed = _data(await _call("komoot_modify_collection", {
        "collection_id": 42, "description": "New introduction", "status": "public",
    }))
    assert changed["collection"]["intro_plain"] == "New introduction"
    assert changed["collection"]["status"] == "public"
    cleared = _data(await _call("komoot_modify_collection", {"collection_id": 42, "description": ""}))
    assert cleared["collection"]["intro_plain"] == ""
    assert cleared["collection"]["name"] == "Original"
    assert cleared["collection"]["multi_day"] is True
    assert [call["body"] for call in remote.writes] == [
        {"intro_plain": "New introduction", "status": "public"}, {"intro_plain": ""},
    ]
    assert all(call["url"] == _COLLECTION for call in remote.calls)


@pytest.mark.asyncio
async def test_delete_only_collection_and_verify_absence(remote):
    data = _data(await _call("komoot_delete_collection", {"collection_id": 42}))
    assert data == {"collection_id": 42, "deleted": True, "verified": True}
    assert [(call["method"], call["url"]) for call in remote.calls] == [
        ("GET", _COLLECTION), ("DELETE", _COLLECTION), ("GET", _COLLECTION),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "komoot_modify_collection", "komoot_add_collection_tours", "komoot_delete_collection",
])
@pytest.mark.parametrize("effect,state", [
    ("ignore", "applied"), ("timeout", "unknown"), ("readback_failure", "applied"),
])
async def test_unverified_writes_report_typed_target_and_never_retry(remote, name, effect, state):
    remote.effect = effect
    error = _error(await _call(name, _WRITES[name]), state)
    assert len(remote.writes) == 1
    if effect == "timeout":
        assert remote.calls[-1] == remote.writes[0]
    elif effect == "readback_failure":
        assert error["confirmed_changes"] == {"write_accepted": True}
        assert error["status_code"] == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("created_id", [None, True, 0, -1, "not-an-id"])
async def test_create_without_usable_id_reports_accepted_not_retryable(remote, created_id):
    remote.created_id = created_id
    result = await _call("komoot_create_collection", {"name": "Weekend"})
    assert result.isError is True
    assert result.structuredContent is not None
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_error"
    assert error["operation_state"] == "applied"
    assert error["confirmed_changes"] == {"write_accepted": True}
    assert error["retry_safe"] is False
    assert len(remote.writes) == 1
    assert len(remote.calls) == 1


@pytest.mark.asyncio
async def test_read_only_hides_and_rejects_every_collection_write_before_http(remote):
    async with create_connected_server_and_client_session(create_server(read_only=True)) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"komoot_list_collections", "komoot_get_collection", "komoot_get_collection_tours"} <= names
        assert names.isdisjoint(_WRITES)
        for name, arguments in _WRITES.items():
            result = await session.call_tool(name, arguments)
            assert result.isError is True
            assert result.structuredContent is not None
            assert result.structuredContent["error"]["code"] == "permission_denied"
            assert result.structuredContent["error"]["operation_state"] == "not_attempted"
    assert remote.calls == []
