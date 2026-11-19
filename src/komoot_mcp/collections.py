"""Own collection management using the shipped Komoot collection wire format."""

import asyncio
from urllib.parse import quote, urljoin, urlsplit

from komoot_mcp.client import KomootAPIError

_BASE = "https://api.komoot.de/v007"
_STATUSES = {"private", "friends", "public"}


def _invalid(message, collection_id=None):
    return KomootAPIError(message, collection_id=collection_id,
                          code="invalid_input", operation_state="not_attempted")


def metadata_fields(name=None, description=None, status=None):
    body = {}
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise _invalid("name must contain text")
        body["name"] = name
    if description is not None:
        if not isinstance(description, str):
            raise _invalid("description must be text")
        body["intro_plain"] = description
    if status is not None:
        if status not in _STATUSES:
            raise _invalid("status must be private, friends, or public")
        body["status"] = status
    if not body:
        raise _invalid("Supply name, description, or status")
    return body


def validate_tour_ids(tour_ids):
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in tour_ids):
        raise _invalid("tour_ids must contain positive integers")
    if len(set(tour_ids)) != len(tour_ids):
        raise _invalid("tour_ids must not contain duplicates")


def _error(message, collection_id=None, *, state, confirmed=None, cause=None, code=None):
    return KomootAPIError(
        message, collection_id=collection_id, operation_state=state,
        confirmed_changes=confirmed or {}, status_code=getattr(cause, "status_code", None), code=code,
    )


async def _username(client):
    return str((await asyncio.to_thread(client._basic_auth))[0])


async def _read(client, url, params=None):
    result = await client._http_request("GET", url, params=params, headers={"Cache-Control": "no-cache"})
    if not isinstance(result, dict):
        raise KomootAPIError("Komoot returned an invalid collection response")
    return result


def _items(data):
    items = data.get("_embedded", {}).get("items")
    if items is None and data.get("page", {}).get("totalElements") == 0:
        return []
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise KomootAPIError("Komoot returned an incomplete collection item page")
    return items


def _creator(collection):
    username = collection.get("_embedded", {}).get("creator", {}).get("username")
    if username is not None:
        return str(username)
    href = collection.get("_links", {}).get("creator", {}).get("href", "")
    parsed = urlsplit(href)
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme == "https" and parsed.netloc == "api.komoot.de"
            and len(parts) == 4 and parts[:2] == ["v007", "users"] and parts[3] == "profile_embedded"):
        return parts[2]
    return None


async def list_collections(client, *, page=0, name=None, status=None):
    username = await _username(client)
    params = {"type": "created", "page": page}
    if name is not None:
        params["name"] = name
    if status is not None:
        params["visibility"] = status
    data = await _read(client, f"{_BASE}/users/{quote(username, safe='')}/elements/", params)
    items = _items(data)
    collections = [item for item in items if str(item.get("type", "")).startswith("collection_")
                   and _creator(item) == username]
    return {"collections": collections, "page": data.get("page"), "_links": data.get("_links", {}),
            "scope": "created", "excluded_elements": len(items) - len(collections)}


async def _owned(client, collection_id, *, members=False):
    try:
        username = await _username(client)
        collection = await _read(client, f"{_BASE}/collections/{collection_id}")
    except KomootAPIError as cause:
        raise _error(str(cause), collection_id, state="not_attempted", cause=cause) from cause
    if str(collection.get("id")) != str(collection_id) or _creator(collection) != username:
        raise _error("Collection is not owned by the authenticated user; no change sent",
                     collection_id, state="not_attempted", code="permission_denied")
    if members and (collection.get("multi_day") or collection.get("type") != "collection_personal"):
        raise _invalid("Tour membership changes require an ordinary personal collection, not a multi-day route", collection_id)
    return collection


def _next_page(current_url, href, collection_id):
    if not isinstance(href, str) or not href:
        raise KomootAPIError("Invalid collection pagination link")
    url = urljoin(current_url, href)
    parsed = urlsplit(url)
    expected_path = f"/v007/collections/{collection_id}/compilation"
    if (parsed.scheme != "https" or parsed.netloc != "api.komoot.de"
            or parsed.path.rstrip("/") != expected_path or parsed.fragment):
        raise KomootAPIError("Collection pagination left the selected compilation")
    return url


async def _members(client, collection_id):
    url = f"{_BASE}/collections/{collection_id}/compilation/"
    params = {"page": 0, "limit": 100}
    result, visited = [], set()
    for _ in range(1000):
        if url in visited:
            raise KomootAPIError("Collection pagination repeated a page; no complete membership available")
        visited.add(url)
        page = await _read(client, url, params)
        for item in _items(page):
            if item.get("id") is None or not isinstance(item.get("type"), str) or not item["type"]:
                raise KomootAPIError("Collection member identity is missing")
            result.append({"id": item["id"], "type": item["type"]})
        next_link = page.get("_links", {}).get("next")
        if next_link is None:
            return result
        url = _next_page(url, next_link.get("href"), collection_id)
        params = None
    raise KomootAPIError("Collection pagination exceeded 1000 pages; no complete membership available")


async def _preflight_members(client, collection_id):
    try:
        return await _members(client, collection_id)
    except Exception as cause:
        raise _error(str(cause), collection_id, state="not_attempted", cause=cause) from cause


async def _write(client, method, url, body=None, collection_id=None):
    try:
        return await client._http_request(method, url, json_body=body, expect_json=method != "DELETE")
    except Exception as cause:
        raise _error("Collection write outcome is unknown. Inspect the collection before retrying.",
                     collection_id, state="unknown", cause=cause) from cause


async def _verify_metadata(client, collection_id, expected):
    try:
        observed = await _read(client, f"{_BASE}/collections/{collection_id}")
    except Exception as cause:
        raise _error("Collection write was accepted but readback failed. Inspect it before retrying.",
                     collection_id, state="applied", confirmed={"write_accepted": True}, cause=cause) from cause
    confirmed = {key: value for key, value in expected.items() if observed.get(key) == value}
    if len(confirmed) != len(expected):
        raise _error("Collection readback differs. Inspect it before retrying.", collection_id,
                     state="applied", confirmed=confirmed)
    return observed


async def _resolve_tours(client, tour_ids, collection_id=None):
    result = []
    for tour_id in tour_ids:
        try:
            tour = await _read(client, f"{_BASE}/tours/{tour_id}")
        except Exception as cause:
            raise _error(str(cause), collection_id, state="not_attempted", cause=cause) from cause
        if str(tour.get("id")) != str(tour_id) or not _is_tour(tour):
            raise _invalid(f"Tour {tour_id} has no supported resource identity; no change sent", collection_id)
        result.append({"id": tour["id"], "type": tour["type"]})
    return result


def _is_tour(member):
    return isinstance(member.get("type"), str) and "tour" in member["type"]


async def create_collection(client, body, tour_ids):
    members = await _resolve_tours(client, tour_ids)
    payload = {**body, "type": "collection_personal", "_embedded": {"compilation": {"_embedded": {"items": members}}}}
    saved = await _write(client, "POST", f"{_BASE}/collections/", payload)
    collection_id = saved.get("id") if isinstance(saved, dict) else None
    if (isinstance(collection_id, bool) or not isinstance(collection_id, (int, str))
            or not str(collection_id).isdigit() or int(collection_id) <= 0):
        raise _error("Collection create was accepted but returned no ID. List your collections before retrying.",
                     state="applied", confirmed={"write_accepted": True})
    collection_id = int(collection_id)
    observed = await _verify_metadata(client, collection_id, {"id": collection_id, "type": "collection_personal", **body})
    await _verify_members(client, collection_id, members)
    return {"collection_id": collection_id, "created": True, "verified": True, "collection": observed}


async def modify_collection(client, collection_id, body):
    current = await _owned(client, collection_id)
    expected = {key: current[key] for key in ("id", "name", "intro_plain", "status", "type", "multi_day") if key in current}
    expected.update(body)
    await _write(client, "PATCH", f"{_BASE}/collections/{collection_id}", body, collection_id)
    observed = await _verify_metadata(client, collection_id, expected)
    return {"collection_id": collection_id, "updated_fields": body, "verified": True, "collection": observed}


async def delete_collection(client, collection_id):
    await _owned(client, collection_id)
    await _write(client, "DELETE", f"{_BASE}/collections/{collection_id}", collection_id=collection_id)
    try:
        await _read(client, f"{_BASE}/collections/{collection_id}")
    except KomootAPIError as cause:
        if cause.status_code == 404:
            return {"collection_id": collection_id, "deleted": True, "verified": True}
        raise _error("Collection deletion was accepted but absence could not be verified.", collection_id,
                     state="applied", confirmed={"write_accepted": True}, cause=cause) from cause
    except Exception as cause:
        raise _error("Collection deletion was accepted but readback failed.", collection_id,
                     state="applied", confirmed={"write_accepted": True}, cause=cause) from cause
    raise _error("Collection still exists after deletion. Inspect it before retrying.", collection_id,
                 state="applied", confirmed={"write_accepted": True})


async def _verify_members(client, collection_id, expected):
    try:
        observed = await _members(client, collection_id)
    except Exception as cause:
        raise _error("Compilation write was accepted but readback failed. Inspect it before retrying.", collection_id,
                     state="applied", confirmed={"write_accepted": True}, cause=cause) from cause
    if observed != expected:
        raise _error("Compilation readback differs in membership or order. Inspect it before retrying.", collection_id,
                     state="applied", confirmed={"write_accepted": True})


async def change_tours(client, collection_id, tour_ids, operation):
    await _owned(client, collection_id, members=True)
    current = await _preflight_members(client, collection_id)
    selected = {str(tour_id) for tour_id in tour_ids}
    existing = {str(item["id"]) for item in current if _is_tour(item)}
    if operation == "add":
        missing = [tour_id for tour_id in tour_ids if str(tour_id) not in existing]
        updated = current + await _resolve_tours(client, missing, collection_id)
    elif operation == "remove":
        updated = [item for item in current if not (_is_tour(item) and str(item["id"]) in selected)]
    else:
        tours = [item for item in current if _is_tour(item)]
        if selected != existing or len(tours) != len(tour_ids):
            raise _invalid("tour_ids must list every current tour exactly once; highlights keep their positions", collection_id)
        by_id = {str(item["id"]): item for item in tours}
        ordered = iter(by_id[str(tour_id)] for tour_id in tour_ids)
        updated = [next(ordered) if _is_tour(item) else item for item in current]
    if updated != current:
        await _write(client, "PUT", f"{_BASE}/collections/{collection_id}/compilation/",
                     {"_embedded": {"items": updated}}, collection_id)
        await _verify_members(client, collection_id, updated)
    return {"collection_id": collection_id, "changed": updated != current, "verified": True, "members": updated}
