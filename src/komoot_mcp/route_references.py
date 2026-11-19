"""Temporary snapshots of unsaved routes, scoped to credentials and transport."""

import hmac
import json
import secrets
from dataclasses import dataclass
from time import monotonic
from typing import Any

from komoot_mcp.client import KomootAPIError
from komoot_mcp.context import get_auth_manager, is_http_context

ROUTE_REF_TTL_S = 900
_MAX_ROUTE_BYTES = 8 * 1024**2
_MAX_OWNER_ENTRIES = 16
_MAX_ENTRIES = 128
_MAX_TOTAL_BYTES = 64 * 1024**2


@dataclass(frozen=True, slots=True)
class _Entry:
    payload: bytes
    owner: bytes
    expires_at: float


def _capacity_error() -> KomootAPIError:
    return KomootAPIError(
        "Route reference storage limit reached; request include_route_data=true.",
        code="route_ref_capacity", operation_state="not_attempted",
    )


class RouteReferences:
    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._entries: dict[str, _Entry] = {}

    def _owner(self) -> bytes:
        auth = get_auth_manager()
        identity = json.dumps([is_http_context(), auth.email, auth.password]).encode("utf-8")
        return hmac.digest(self._key, identity, "sha256")

    def _purge(self, now: float) -> None:
        for ref in [ref for ref, entry in self._entries.items() if entry.expires_at <= now]:
            del self._entries[ref]

    def add(self, routes: list[dict[str, Any]]) -> list[str]:
        now = monotonic()
        self._purge(now)
        owner = self._owner()
        owner_count = sum(hmac.compare_digest(entry.owner, owner) for entry in self._entries.values())
        if len(self._entries) + len(routes) > _MAX_ENTRIES or owner_count + len(routes) > _MAX_OWNER_ENTRIES:
            raise _capacity_error()

        total_bytes = sum(len(entry.payload) for entry in self._entries.values())
        payloads = []
        for route in routes:
            try:
                payload = json.dumps(route, separators=(",", ":"), allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, OverflowError, RecursionError):
                raise KomootAPIError(
                    "Komoot did not return a valid JSON route.",
                    code="route_ref_invalid", operation_state="not_attempted",
                ) from None
            total_bytes += len(payload)
            if len(payload) > _MAX_ROUTE_BYTES or total_bytes > _MAX_TOTAL_BYTES:
                raise _capacity_error()
            payloads.append(payload)

        refs = []
        for payload in payloads:
            ref = secrets.token_urlsafe(32)
            while ref in self._entries:
                ref = secrets.token_urlsafe(32)
            self._entries[ref] = _Entry(payload, owner, now + ROUTE_REF_TTL_S)
            refs.append(ref)
        return refs

    def get(self, route_ref: str) -> dict[str, Any]:
        self._purge(monotonic())
        entry = self._entries.get(route_ref)
        if entry is None or not hmac.compare_digest(entry.owner, self._owner()):
            raise KomootAPIError(
                "Route reference is unavailable; preview the route again.",
                code="route_ref_unavailable", operation_state="not_attempted",
            )
        return json.loads(entry.payload)
