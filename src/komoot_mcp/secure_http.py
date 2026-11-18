"""Outbound Komoot requests with explicit destinations and no ambient credentials."""

from urllib.parse import urlsplit

import requests
from requests import exceptions

_AUTH_HOSTS = frozenset({"api.komoot.de", "www.komoot.com", "weather-along-tour-api.komoot.de"})
_PUBLIC_HOSTS = _AUTH_HOSTS | {"photon.komoot.io"}


def validate_destination(url: str, authenticated: bool = True) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid outbound destination") from exc
    hosts = _AUTH_HOSTS if authenticated else _PUBLIC_HOSTS
    if (
        parsed.scheme != "https" or parsed.hostname not in hosts
        or parsed.username is not None or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("Outbound destination is not an approved HTTPS endpoint")
    if authenticated and parsed.hostname == "www.komoot.com" and not parsed.path.startswith("/api/"):
        raise ValueError("Authenticated www.komoot.com requests must use /api/")


def request(method: str, url: str, *, auth=None, headers=None, **kwargs):
    """Never follow redirects or read proxy/.netrc credentials from the environment."""
    authenticated = auth is not None or any(k.lower() == "authorization" for k in (headers or {}))
    validate_destination(url, authenticated=authenticated)
    with requests.Session() as session:
        session.trust_env = False
        response = session.request(
            method, url, auth=auth, headers=headers,
            allow_redirects=False, **kwargs,
        )
    if 300 <= response.status_code < 400:
        raise requests.RequestException("Komoot redirect refused; no credentials forwarded")
    return response


def get(url: str, **kwargs):
    return request("GET", url, **kwargs)


def post(url: str, **kwargs):
    return request("POST", url, **kwargs)
