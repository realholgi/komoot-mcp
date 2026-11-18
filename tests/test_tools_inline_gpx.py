"""Tool regressions for complete inline GPX and semantic way-type data."""
from __future__ import annotations

import pytest

from komoot_mcp.context import clear_request_state


@pytest.fixture(autouse=True)
def _reset():
    clear_request_state()
    yield
    clear_request_state()


def _build_tool_registry(module):
    registered: dict[str, callable] = {}

    class _Mcp:
        def tool(self):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    module.register(_Mcp())
    return registered


class TestGetTourGpxToolReturnsInline:
    """Issue #9: GPX content must come back in the tool response."""

    @pytest.mark.asyncio
    async def test_returns_gpx_as_semantic_data(self, monkeypatch):
        from komoot_mcp.tools import data_tools
        registered = _build_tool_registry(data_tools)

        # Monkeypatch the client.get_tour_gpx to return a known string.
        gpx_payload = "<gpx><trk><name>x</name></trk></gpx>"

        from komoot_mcp import context as ctx_mod

        class _FakeClient:
            async def get_tour_gpx(self, tour_id):
                return gpx_payload

        monkeypatch.setattr(ctx_mod, "get_client", lambda: _FakeClient())
        # Tool module imports get_client at module load — patch there too.
        monkeypatch.setattr(data_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_get_tour_gpx"](tour_id=42)
        assert out == {
            "tour_id": 42, "gpx_content": gpx_payload,
            "size_bytes": len(gpx_payload.encode("utf-8")), "truncated": False,
        }

    @pytest.mark.asyncio
    async def test_returns_large_gpx_in_full(self, monkeypatch):
        """Real-world planned routes are 300–500 KB. The tool must
        return the full content — no truncation, no cap. Regression
        test for the bug where PR #12's 200 KB cap silently chopped
        real routes in half.
        """
        from komoot_mcp.tools import data_tools

        registered = _build_tool_registry(data_tools)

        # Fabricate a >400 KB GPX body — well above the old 200 KB
        # cap and roughly the size of a typical 70 km planned route.
        body = "x" * 400_000
        gpx_payload = f"<gpx>{body}</gpx>"
        assert len(gpx_payload) > 300_000  # sanity check

        class _FakeClient:
            async def get_tour_gpx(self, tour_id):
                return gpx_payload

        monkeypatch.setattr(data_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_get_tour_gpx"](tour_id=42)
        assert out["truncated"] is False
        assert out["size_bytes"] == len(gpx_payload.encode("utf-8"))
        assert out["gpx_content"] == gpx_payload


class TestGetTourWayTypesData:
    """Way-type fractions retain their semantic values."""

    @pytest.mark.asyncio
    async def test_returns_way_type_fractions(self, monkeypatch):
        from komoot_mcp.tools import data_tools
        registered = _build_tool_registry(data_tools)

        class _FakeClient:
            async def get_tour_way_types(self, tour_id):
                return [
                    {"way_type": "trail", "fraction": 0.42},
                    {"way_type": "road", "fraction": 0.58},
                ]

        monkeypatch.setattr(data_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_get_tour_way_types"](tour_id=42)
        assert out == {"tour_id": 42, "way_types": [
            {"way_type": "trail", "fraction": 0.42},
            {"way_type": "road", "fraction": 0.58},
        ]}
