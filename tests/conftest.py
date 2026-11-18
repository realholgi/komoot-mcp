"""Keep the offline suite from making unmocked HTTP requests."""

import pytest
import requests


@pytest.fixture(autouse=True)
def block_unmocked_http(monkeypatch):
    def refuse_network(self, request, **kwargs):
        raise AssertionError(f"Unmocked HTTP request in offline test: {request.method} {request.url}")

    monkeypatch.setattr(requests.Session, "send", refuse_network)
