"""Private process-local cache for JSON tour GETs, not a write transaction guard."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from hashlib import sha256
import re
from threading import RLock
import time

import requests


def _directives(value):
    result = {}
    for part in value.split(","):
        name, _, argument = part.strip().partition("=")
        name = name.lower()
        # Conflicting duplicate directives must never increase freshness.
        if name in result and result[name] != argument.strip('"'):
            result["no-cache"] = ""
        result[name] = argument.strip('"')
    return result


def _freshness(headers, now):
    policy = _directives(headers.get("Cache-Control", ""))
    if "no-cache" in policy:
        return 0
    try:
        lifetime = max(0, int(policy.get("max-age", "0")))
        age = max(0, int(headers.get("Age", "0")))
        if headers.get("Date"):
            date = parsedate_to_datetime(headers["Date"])
            if date.tzinfo is None:
                return 0
            age = max(age, now - date.timestamp())
        return max(0, lifetime - age)
    except (ValueError, TypeError, OverflowError):
        return 0


@dataclass
class _Entry:
    identity: bytes
    response: requests.Response
    expires: float
    size: int


class TourResponseCache:
    """Bounded LRU snapshots; all returned responses are caller-owned copies.

    An identity is a digest of the credentials and cookies, never a disk key.
    All request headers and the prepared URL partition representations. Writes
    evict that identity before and after transport, including transport errors.
    A process-wide generation prevents older GETs from repopulating the cache;
    it intentionally also suppresses fills during unrelated concurrent writes.
    """

    def __init__(self, max_entries=128, max_bytes=16 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        self._generation = 0
        self._writers = {}

    def _evict(self, key):
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.size

    def _invalidate(self, identity):
        self._generation += 1
        for key, entry in list(self._entries.items()):
            if identity is None or entry.identity == identity:
                self._evict(key)

    def request(self, method, url, auth, headers, options, send):
        method = method.upper()
        # Unknown auth handlers may change identity while preparing/sending.
        known_auth = auth is None or (
            isinstance(auth, tuple) and len(auth) == 2
            and all(isinstance(value, str) for value in auth)
        ) or type(auth) is requests.auth.HTTPBasicAuth
        prepared = None
        identity = None
        if known_auth:
            prepared = requests.Request(
                method, url, auth=auth, headers=headers,
                params=options.get("params"), cookies=options.get("cookies"),
            ).prepare()
            identity = sha256(repr((
                prepared.headers.get("Authorization"),
                prepared.headers.get("Cookie"),
            )).encode()).digest()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            with self._lock:
                self._invalidate(identity)
                self._writers[identity] = self._writers.get(identity, 0) + 1
            try:
                return send(headers)
            finally:
                with self._lock:
                    self._invalidate(identity)
                    self._writers[identity] -= 1
                    if not self._writers[identity]:
                        del self._writers[identity]

        eligible = (
            method == "GET" and prepared is not None
            and not options.get("cert")
            and requests.utils.urlparse(prepared.url).hostname == "api.komoot.de"
            and re.fullmatch(r"/v007/tours/[^/.]+(?:/[^.]*|)",
                             requests.utils.urlparse(prepared.url).path) is not None
            and not any(options.get(name) for name in ("stream", "hooks", "data", "json", "files"))
            and not any(name in prepared.headers for name in (
                "Range", "If-None-Match", "If-Modified-Since", "If-Match", "If-Unmodified-Since",
            ))
        )
        request_policy = _directives((headers or {}).get("Cache-Control", "")) if headers else {}
        # Prepared headers provide case-insensitive lookup for caller directives.
        if prepared is not None:
            request_policy = _directives(prepared.headers.get("Cache-Control", ""))
        if not eligible:
            return send(headers)
        key = sha256(repr((prepared.url, sorted(
            (name.lower(), value) for name, value in prepared.headers.items()
            if name.lower() != "cache-control"
        ))).encode()).digest()
        with self._lock:
            generation = self._generation
            entry = self._entries.get(key)
            if "no-store" in request_policy:
                self._evict(key)
                entry = None
            if identity in self._writers or None in self._writers:
                entry = None
            if entry is not None:
                self._entries.move_to_end(key)
                if ("no-cache" not in request_policy and "max-age" not in request_policy
                        and entry.expires > time.monotonic()):
                    return deepcopy(entry.response)

        conditional = requests.structures.CaseInsensitiveDict(headers or {})
        revalidating = False
        if entry is not None:
            if entry.response.headers.get("ETag"):
                conditional["If-None-Match"] = entry.response.headers["ETag"]
                revalidating = True
            elif entry.response.headers.get("Last-Modified"):
                conditional["If-Modified-Since"] = entry.response.headers["Last-Modified"]
                revalidating = True
        started = time.monotonic()
        response = send(conditional if revalidating else headers)
        if response.status_code == 304 and revalidating:
            merged = deepcopy(entry.response)
            # Content framing belongs to the stored body, not the empty 304.
            for name, value in response.headers.items():
                if name.lower() not in {"content-length", "transfer-encoding", "content-encoding"}:
                    merged.headers[name] = value
            # A previous Age/Date must not make a new validation look younger.
            # Without a new Date, force revalidation on its next use.
            if "Date" not in response.headers:
                merged.headers["Cache-Control"] = merged.headers.get("Cache-Control", "") + ", no-cache"
            merged.request = response.request
            merged.elapsed = response.elapsed
            merged.url = response.url or merged.url
            response = merged
        elif 300 <= response.status_code < 400:
            raise requests.RequestException("Komoot redirect refused; no credentials forwarded")

        with self._lock:
            if generation != self._generation:
                return response
            self._evict(key)
            if (not isinstance(response, requests.Response) or response.status_code != 200
                    or "no-store" in request_policy or identity in self._writers
                    or None in self._writers):
                return response
            policy = _directives(response.headers.get("Cache-Control", ""))
            if ("no-store" in policy or "*" in {
                    value.strip() for value in response.headers.get("Vary", "").split(",")}
                    or response.headers.get("Set-Cookie")
                    or response.cookies
                    or response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    not in {"application/json", "application/hal+json"}):
                return response
            ttl = _freshness(response.headers, time.time())
            if not ttl and not (response.headers.get("ETag") or response.headers.get("Last-Modified")):
                return response
            # Bound body and header storage; keys/entry metadata are count-bounded.
            size = len(response.content) + sum(len(str(k)) + len(str(v))
                                               for k, v in response.headers.items())
            size += len(prepared.url) + sum(len(k) + len(v) for k, v in prepared.headers.items())
            if size > self.max_bytes or self.max_entries <= 0:
                return response
            snapshot = deepcopy(response)
            self._entries[key] = _Entry(identity, snapshot, started + ttl, size)
            self._bytes += size
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                self._evict(next(iter(self._entries)))
        return response
