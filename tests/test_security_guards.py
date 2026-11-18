"""No live network: credential destinations, redirects and HTTP tenant isolation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import requests
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from komoot_mcp import context, secure_http
from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootClient
from komoot_mcp.middleware import InternalSecretMiddleware, UserCredentialsMiddleware


@pytest.mark.parametrize("url", [
    "https://evil.example/tours/1", "http://api.komoot.de/v007/tours/1",
    "https://api.komoot.de.evil.example/", "https://user:password@api.komoot.de/",
    "https://api.komoot.de:8443/", "https://www.komoot.com/redirect",
    "https://photon.komoot.io/api/",
])
def test_authenticated_requests_refuse_unapproved_destinations_without_network(monkeypatch, url):
    session = Mock()
    monkeypatch.setattr(requests, "Session", session)
    with pytest.raises(ValueError):
        secure_http.get(url, auth=("user", "test-token"))
    session.assert_not_called()


def test_public_requests_do_not_read_netrc_credentials_or_follow_redirects(monkeypatch, tmp_path):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine photon.komoot.io login stolen password secret\n")
    monkeypatch.setenv("NETRC", str(netrc))
    sent = []

    def send(self, prepared, **kwargs):
        sent.append((prepared, kwargs, self.trust_env))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    secure_http.get("https://photon.komoot.io/reverse", params={"lat": 49, "lon": 8})
    prepared, options, trust_env = sent[0]
    assert "Authorization" not in prepared.headers
    assert options["allow_redirects"] is False
    assert trust_env is False


def test_redirect_response_is_rejected_without_a_second_request(monkeypatch):
    sent = []

    def send(self, prepared, **kwargs):
        sent.append(prepared)
        response = requests.Response()
        response.status_code = 302
        response.headers["Location"] = "https://evil.example/collect"
        response._content = b""
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    with pytest.raises(requests.RequestException, match="redirect refused"):
        secure_http.get("https://api.komoot.de/v007/tours/1", auth=("user", "test-token"))
    assert len(sent) == 1


def test_basic_auth_uses_session_token_not_account_password():
    client = KomootClient(AuthManager(email="test@example.invalid", password="account-password"), None)
    client._api = SimpleNamespace(authentication=SimpleNamespace(
        get_username=lambda: "user", get_password=lambda: "account-password",
        get_token=lambda: "session-token",
    ))
    assert client._basic_auth() == ("user", "session-token")


@pytest.mark.asyncio
async def test_unapproved_client_url_does_not_even_log_in():
    client = KomootClient(AuthManager(email="test@example.invalid", password="unused"), None)
    client._basic_auth = Mock()
    with pytest.raises(ValueError):
        await client._http_request("GET", "https://evil.example/collect")
    client._basic_auth.assert_not_called()


def make_app():
    async def endpoint(request):
        return JSONResponse({"email": context.get_auth_manager().email})
    app = Starlette(routes=[Route("/mcp", endpoint), Route("/health", lambda request: JSONResponse({"ok": True}))])
    app.add_middleware(UserCredentialsMiddleware)
    app.add_middleware(InternalSecretMiddleware)
    return app


@pytest.mark.asyncio
async def test_http_fails_closed_without_gateway_secret(monkeypatch):
    monkeypatch.delenv("INTERNAL_SECRET", raising=False)
    monkeypatch.delenv("GATEWAY_SECRET", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url="http://test") as client:
        assert (await client.get("/mcp")).status_code == 401
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("creds", [None, "{broken", "[]", '{"email":"tenant"}', '{"email":"tenant","password":123}'])
async def test_http_never_falls_back_to_server_credentials(monkeypatch, creds):
    monkeypatch.setenv("INTERNAL_SECRET", "gateway")
    monkeypatch.setenv("KOMOOT_EMAIL", "server@example.invalid")
    monkeypatch.setenv("KOMOOT_PASSWORD", "server-secret")
    headers = {"Authorization": "Bearer gateway"}
    if creds is not None:
        headers["x-user-credentials"] = creds
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url="http://test") as client:
        assert (await client.get("/mcp", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_gateway_only_secret_works_and_tenants_keep_their_credentials(monkeypatch):
    monkeypatch.delenv("INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("GATEWAY_SECRET", "gateway")
    monkeypatch.setenv("KOMOOT_EMAIL", "server@example.invalid")
    monkeypatch.setenv("KOMOOT_PASSWORD", "server-secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url="http://test") as client:
        results = []
        for user in ("alice", "bob"):
            creds = {"email": user, "password": "tenant-password"}
            result = await client.get("/mcp", headers={
                "Authorization": "Bearer Internal-gateway:gateway",
                "x-user-credentials": json.dumps(creds),
            })
            assert result.status_code == 200
            results.append(result.json())
        assert results == [{"email": "alice"}, {"email": "bob"}]


@pytest.mark.asyncio
async def test_coordinate_lookup_ignores_response_supplied_links():
    client = KomootClient(AuthManager(email="test@example.invalid", password="unused"), None)
    client._komoot_get = AsyncMock(return_value={"items": [[49, 8, 120]], "_links": {"next": {"href": "https://evil.example/collect"}}})
    assert await client.get_tour_coordinates(42) == [[49, 8, 120]]
    client._komoot_get.assert_awaited_once_with(
        "https://api.komoot.de/v007/tours/42/coordinates", params={"format": "coordinate_array"},
    )


@pytest.mark.asyncio
async def test_http_cannot_read_or_write_server_files(tmp_path):
    from komoot_mcp.artifacts import write_export

    token = context.set_http_context()
    target = tmp_path / "server-secret.fit"
    client = KomootClient(AuthManager(email="tenant@example.invalid", password="unused"), None)
    client._http_request = AsyncMock()
    try:
        with pytest.raises(PermissionError):
            await client.upload_tour(filepath=str(target), data_type="fit")
        with pytest.raises(PermissionError):
            write_export(str(target), "tour.fit", b"data", overwrite=False)
        assert not target.exists()
        client._http_request.assert_not_awaited()
    finally:
        context.reset_http_context(token)
