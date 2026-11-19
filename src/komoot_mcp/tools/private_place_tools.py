"""Manage private saved places without creating tours or public Highlights."""

import asyncio
import re
from urllib.parse import quote

from komoot_mcp.client import KomootAPIError
from komoot_mcp.context import get_client
from komoot_mcp.routing import validate_coordinates


# Web planner 60817.edf235f94138ffc7.js, module 51393.
# Custom places use the addresses API with is_home_address=false and type=other.
async def _account():
    client = get_client()
    username = (await asyncio.to_thread(client._basic_auth))[0]
    return client, f"https://api.komoot.de/v007/users/{quote(str(username), safe='')}/addresses"


def _name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must contain text")
    return name


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(value)):
        raise ValueError("place_id must contain only letters, digits, underscores, or hyphens")
    return str(value)


async def _list(client, url):
    places = await client._http_request("GET", url, params={"type": "other"})
    if not isinstance(places, list) or any(not isinstance(p, dict) or p.get("id") is None for p in places):
        raise KomootAPIError("Komoot returned an invalid private place list")
    return places


def _find(places, place_id):
    return next((p for p in places if str(p["id"]) == place_id and p.get("is_home_address") is not True), None)


def _write_error(message, place_id, *, state, confirmed=None):
    error = KomootAPIError(message, operation_state=state, confirmed_changes=confirmed or {}, place_id=place_id)
    return error


async def _write(client, method, url, body=None, place_id=None):
    try:
        return await client._http_request(method, url, json_body=body, expect_json=method != "DELETE")
    except Exception as cause:
        error = _write_error("The private place write outcome is unknown. List places before retrying.",
                             place_id, state="unknown")
        error.status_code = getattr(cause, "status_code", None)
        raise error from cause


async def _verify(client, url, place_id, expected=None, deleted=False):
    try:
        observed = _find(await _list(client, url), place_id)
    except Exception as cause:
        raise _write_error("Komoot accepted the write, but readback failed. List places before retrying.",
                           place_id, state="applied", confirmed={"write_accepted": True}) from cause
    if deleted:
        if observed is not None:
            raise _write_error("Komoot still lists the place after deletion. Inspect it before retrying.",
                               place_id, state="applied", confirmed={"write_accepted": True})
        return None
    expected = expected or {}
    confirmed = {key: value for key, value in expected.items() if observed is not None and observed.get(key) == value}
    # Compare numeric coordinates with the rounding tolerance used for tour readback.
    point = observed.get("point") if observed else None
    expected_point = expected.get("point")
    if isinstance(point, dict) and isinstance(expected_point, dict):
        try:
            if all(abs(point[axis] - expected_point[axis]) <= 0.000001 for axis in ("x", "y")):
                confirmed["point"] = expected_point
        except (KeyError, TypeError):
            pass
    if any(key not in confirmed for key in expected):
        raise _write_error("Private place readback differs. Inspect the place before retrying.",
                           place_id, state="applied", confirmed=confirmed)
    return observed


def register(mcp):
    @mcp.tool()
    async def komoot_list_private_places() -> dict:
        """List your private custom places in Komoot. Do not change the account.

        This reads saved addresses of type other. It excludes your home address,
        bookmarked Highlights, and map POIs. Coordinates use point.x for longitude
        and point.y for latitude in Komoot's raw place records.
        """
        client, url = await _account()
        return {"places": [p for p in await _list(client, url) if p.get("is_home_address") is not True]}

    @mcp.tool()
    async def komoot_create_private_place(name: str, lat: float, lng: float) -> dict:
        """Create a named private place in your Komoot account.

        Use decimal degrees. This saves a custom place, not a tour or public Highlight.
        It does not change your home address. Readback checks the name and coordinates.
        If a write fails, list places before retrying to avoid duplicates.
        """
        _name(name)
        validate_coordinates([[lat, lng]])
        client, url = await _account()
        body = {"name": name, "point": {"x": lng, "y": lat},
                "address_entry": {}, "is_home_address": False}
        saved = await _write(client, "POST", url + "/", body)
        place_id = _id(saved["id"]) if isinstance(saved, dict) and saved.get("id") is not None else None
        if place_id is None:
            raise _write_error("Komoot accepted the create request but returned no place ID. List places before retrying.",
                               None, state="applied", confirmed={"write_accepted": True})
        place = await _verify(client, url, place_id, expected={"name": name, "point": body["point"]})
        return {"place_id": place_id, "created": True, "verified": True, "place": place}

    @mcp.tool()
    async def komoot_rename_private_place(place_id: str, name: str) -> dict:
        """Rename one of your private custom places in Komoot.

        Use a place_id from komoot_list_private_places. Keep its coordinates and address.
        This does not change a tour, Highlight, map POI, or home address.
        Readback checks the new name and unchanged coordinates.
        """
        place_id, name = _id(place_id), _name(name)
        client, url = await _account()
        place = _find(await _list(client, url), place_id)
        if place is None:
            raise ValueError("Private place not found. No change was sent.")
        body = {"name": name, "point": place["point"],
                "address_entry": place.get("address_entry", {}), "is_home_address": False}
        await _write(client, "PATCH", f"{url}/{place_id}", body, place_id)
        observed = await _verify(client, url, place_id, expected={"name": name, "point": body["point"]})
        return {"place_id": place_id, "renamed": True, "verified": True, "place": observed}

    @mcp.tool()
    async def komoot_delete_private_place(place_id: str) -> dict:
        """Delete one private custom place from your Komoot account.

        Use a place_id from komoot_list_private_places. This removes the saved place only.
        It does not delete a tour, Highlight, map POI, or home address.
        Readback checks that the private place list no longer contains this ID.
        """
        place_id = _id(place_id)
        client, url = await _account()
        if _find(await _list(client, url), place_id) is None:
            raise ValueError("Private place not found. No deletion was sent.")
        await _write(client, "DELETE", f"{url}/{place_id}", place_id=place_id)
        await _verify(client, url, place_id, deleted=True)
        return {"place_id": place_id, "deleted": True, "verified": True}
