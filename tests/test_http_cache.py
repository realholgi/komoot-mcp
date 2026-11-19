"""Consumer-visible caching through secure_http, without live network calls."""

from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from threading import Event
from typing import Any

import pytest
import requests

from komoot_mcp import secure_http
from komoot_mcp.http_cache import TourResponseCache


URL = "https://api.komoot.de/v007/tours/123"
AUTH = ("alice", "token")


def response(body=b'{"name":"original"}', status=200, /, **headers: str):
    result = requests.Response()
    result.status_code = status
    result._content = body
    result.headers.update({"Content-Type": "application/json", **headers})
    return result


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(secure_http, "_TOUR_CACHE", TourResponseCache())
    calls = []
    replies: list[Any] = []

    def send(session, prepared, **options):
        calls.append((prepared, options, session.trust_env))
        assert replies, "unexpected network request"
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(prepared)
        item.request = prepared
        item.url = prepared.url
        return item

    monkeypatch.setattr(requests.Session, "send", send)
    return calls, replies


def test_fresh_private_response_is_copied_and_process_scoped(transport):
    calls, replies = transport
    replies.append(response(**{"Cache-Control": "private, max-age=60"}))
    first = secure_http.get(URL, auth=AUTH)
    first.headers["X-Corruption"] = "caller"
    first._content = b"changed"
    first.request.headers["Accept"] = "caller"
    second = secure_http.get(URL, auth=AUTH)
    assert second.json() == {"name": "original"}
    assert "X-Corruption" not in second.headers
    assert second.request.headers["Accept"] != "caller"
    assert second.status_code == 200
    assert list(second.iter_content())
    assert second is not first
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][2] is False


def test_credentials_queries_and_representation_headers_are_isolated(transport):
    calls, replies = transport
    variants = [
        {"auth": AUTH, "params": {"page": 1}},
        {"auth": ("bob", "token"), "params": {"page": 1}},
        {"auth": ("alice", "new-token"), "params": {"page": 1}},
        {"auth": AUTH, "params": {"page": 2}},
        {"auth": AUTH, "params": {"page": 1}, "headers": {"Accept-Language": "de"}},
        {"auth": AUTH, "params": {"page": 1}, "cookies": {"session": "one"}},
        {"auth": AUTH, "params": {"page": 1}, "cookies": {"session": "two"}},
    ]
    for index, options in enumerate(variants):
        replies.append(response(str(index).encode(), **{"Cache-Control": "max-age=60"}))
        assert secure_http.get(URL, **options).json() == index
    for index, options in enumerate(variants):
        assert secure_http.get(URL, **options).json() == index
    assert len(calls) == len(variants)


def test_etag_revalidation_returns_body_and_updated_headers(transport):
    calls, replies = transport
    replies.extend([
        response(**{"ETag": '"v1"', "Last-Modified": formatdate(usegmt=True)}),
        response(b"", 304, **{"Date": formatdate(usegmt=True), "Cache-Control": "max-age=60",
                                "ETag": '"v2"', "Content-Length": "0"}),
    ])
    secure_http.get(URL, auth=AUTH)
    checked = secure_http.get(URL, auth=AUTH)
    assert calls[1][0].headers["If-None-Match"] == '"v1"'
    assert "If-Modified-Since" not in calls[1][0].headers
    assert checked.status_code == 200
    assert checked.json() == {"name": "original"}
    assert checked.headers["ETag"] == '"v2"'
    assert "Content-Length" not in checked.headers
    assert secure_http.get(URL, auth=AUTH).json() == checked.json()
    assert len(calls) == 2


def test_last_modified_and_caller_no_cache_force_validation(transport):
    calls, replies = transport
    modified = formatdate(usegmt=True)
    replies.extend([
        response(**{"Cache-Control": "max-age=600", "Last-Modified": modified}),
        response(b"", 304, **{"Date": formatdate(usegmt=True)}),
    ])
    secure_http.get(URL, auth=AUTH)
    checked = secure_http.get(URL, auth=AUTH, headers={"cache-control": "no-cache"})
    assert checked.json() == {"name": "original"}
    assert calls[1][0].headers["If-Modified-Since"] == modified


@pytest.mark.parametrize("headers", [
    {"Cache-Control": "no-cache, max-age=600"},
    {"Cache-Control": "max-age=60", "Age": "60"},
    {"Cache-Control": "max-age=60", "Date": formatdate(0, usegmt=True)},
    {"Cache-Control": "max-age=60", "Age": "invalid"},
    {"Cache-Control": "max-age=60", "Date": "invalid"},
    {"Cache-Control": "max-age=60, max-age=600"},
])
def test_conservative_freshness_requires_validation(transport, headers):
    calls, replies = transport
    replies.extend([response(**{**headers, "ETag": '"v1"'}), response()])
    secure_http.get(URL, auth=AUTH)
    secure_http.get(URL, auth=AUTH)
    assert calls[1][0].headers["If-None-Match"] == '"v1"'


@pytest.mark.parametrize("headers", [
    {"Cache-Control": "no-store, max-age=600", "ETag": '"v1"'},
    {"Cache-Control": "max-age=600", "Vary": "Accept, * "},
    {"Cache-Control": "max-age=600", "Set-Cookie": "session=secret"},
    {"Cache-Control": "max-age=600", "Content-Type": "text/html"},
    {},
])
def test_uncacheable_responses_are_not_reused(transport, headers):
    calls, replies = transport
    replies.extend([response(**headers), response()])
    secure_http.get(URL, auth=AUTH)
    secure_http.get(URL, auth=AUTH)
    assert len(calls) == 2
    assert "If-None-Match" not in calls[1][0].headers


@pytest.mark.parametrize("status", [201, 204, 400, 404, 500])
def test_only_successful_complete_200_responses_are_cached(transport, status):
    calls, replies = transport
    replies.extend([response(b"{}", status, **{"Cache-Control": "max-age=600"}), response()])
    assert secure_http.get(URL, auth=AUTH).status_code == status
    secure_http.get(URL, auth=AUTH)
    assert len(calls) == 2


@pytest.mark.parametrize("suffix", ["/coordinates", "/surfaces", ""])
def test_json_tour_resources_are_eligible(transport, suffix):
    calls, replies = transport
    replies.append(response(**{"Cache-Control": "max-age=600"}))
    secure_http.get(URL + suffix, auth=AUTH)
    secure_http.get(URL + suffix, auth=AUTH)
    assert len(calls) == 1


@pytest.mark.parametrize("url", [URL + ".gpx", URL + "/track.gpx", "https://api.komoot.de/v007/users/123"])
def test_non_json_downloads_and_other_resources_bypass_cache(transport, url):
    calls, replies = transport
    replies.extend([response(**{"Cache-Control": "max-age=600"}) for _ in range(2)])
    secure_http.get(url, auth=AUTH)
    secure_http.get(url, auth=AUTH)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [301, 302, 307, 308, 304])
def test_redirects_and_unsolicited_304_remain_rejected(transport, status):
    calls, replies = transport
    replies.append(response(b"", status, Location="https://evil.example/"))
    with pytest.raises(requests.RequestException, match="redirect refused"):
        secure_http.get(URL, auth=AUTH)
    assert len(calls) == 1


def test_request_no_store_evicts_and_never_stores(transport):
    calls, replies = transport
    replies.extend([response(**{"Cache-Control": "max-age=600"}) for _ in range(3)])
    secure_http.get(URL, auth=AUTH)
    secure_http.get(URL, auth=AUTH, headers={"Cache-Control": "no-store"})
    secure_http.get(URL, auth=AUTH)
    assert len(calls) == 3


@pytest.mark.parametrize("failure", [False, True])
def test_write_invalidates_all_identity_resources_even_on_uncertain_failure(transport, failure):
    calls, replies = transport
    for url in (URL, URL + "/coordinates"):
        replies.append(response(**{"Cache-Control": "max-age=600"}))
        secure_http.get(url, auth=AUTH)
    replies.append(response(**{"Cache-Control": "max-age=600"}))
    secure_http.get(URL, auth=("bob", "token"))
    replies.append(requests.Timeout("unknown outcome") if failure else response())
    if failure:
        with pytest.raises(requests.Timeout):
            secure_http.request("PATCH", URL, auth=AUTH, json={"name": "new"})
    else:
        secure_http.request("PATCH", URL, auth=AUTH, json={"name": "new"})
    for url in (URL, URL + "/coordinates"):
        replies.append(response())
        secure_http.get(url, auth=AUTH)
    secure_http.get(URL, auth=("bob", "token"))
    assert len(calls) == 6


def test_inflight_get_cannot_resurrect_after_write(transport):
    calls, replies = transport
    entered = Event()
    release = Event()

    def old_get(prepared):
        entered.set()
        assert release.wait(5)
        return response(**{"Cache-Control": "max-age=600"})

    replies.append(old_get)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(secure_http.get, URL, auth=AUTH)
        try:
            assert entered.wait(5)
            replies.append(response())
            secure_http.request("PATCH", URL, auth=AUTH)
        finally:
            release.set()
        assert pending.result().status_code == 200
    replies.append(response(b'{"name":"new"}'))
    assert secure_http.get(URL, auth=AUTH).json() == {"name": "new"}
    assert len(calls) == 3


def test_get_during_write_is_not_stored_after_write_finishes(transport):
    calls, replies = transport
    entered = Event()
    release = Event()

    def writing(prepared):
        entered.set()
        assert release.wait(5)
        return response()

    replies.append(writing)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(secure_http.request, "PATCH", URL, auth=AUTH)
        try:
            assert entered.wait(5)
            replies.append(response(**{"Cache-Control": "max-age=600"}))
            secure_http.get(URL, auth=AUTH)
        finally:
            release.set()
        pending.result()
    replies.append(response())
    secure_http.get(URL, auth=AUTH)
    assert len(calls) == 3


@pytest.mark.parametrize("max_entries,max_bytes", [(1, 4096), (128, 1)])
def test_cache_count_and_byte_limits(transport, monkeypatch, max_entries, max_bytes):
    calls, replies = transport
    monkeypatch.setattr(secure_http, "_TOUR_CACHE", TourResponseCache(max_entries, max_bytes))
    for url in (URL, URL + "/coordinates", URL):
        replies.append(response(**{"Cache-Control": "max-age=600"}))
        secure_http.get(url, auth=AUTH)
    assert len(calls) == 3


def test_no_store_on_304_discards_stored_body_even_without_date(transport):
    calls, replies = transport
    replies.extend([
        response(**{"ETag": '"old"'}),
        response(b"", 304, **{"Cache-Control": "no-store"}),
        response(),
    ])
    secure_http.get(URL, auth=AUTH)
    assert secure_http.get(URL, auth=AUTH).json() == {"name": "original"}
    secure_http.get(URL, auth=AUTH)
    assert "If-None-Match" not in calls[2][0].headers


def test_failed_revalidation_returns_error_not_stale_body(transport):
    calls, replies = transport
    replies.extend([response(**{"ETag": '"old"'}), response(b"failure", 503), response()])
    secure_http.get(URL, auth=AUTH)
    assert secure_http.get(URL, auth=AUTH).status_code == 503
    secure_http.get(URL, auth=AUTH)
    assert "If-None-Match" not in calls[2][0].headers


def test_authorization_header_identity_isolated_and_invalidated(transport):
    calls, replies = transport
    for token in ("Bearer alice", "Bearer bob"):
        replies.append(response(**{"Cache-Control": "max-age=600"}))
        secure_http.get(URL, headers={"Authorization": token})
    replies.append(response())
    secure_http.post(URL, headers={"Authorization": "Bearer alice"})
    secure_http.get(URL, headers={"Authorization": "Bearer bob"})
    replies.append(response())
    secure_http.get(URL, headers={"Authorization": "Bearer alice"})
    assert len(calls) == 4
