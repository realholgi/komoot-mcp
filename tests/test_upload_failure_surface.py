"""Upload failures must remain errors, while successful uploads identify the tour."""
from __future__ import annotations

import pytest

from komoot_mcp.client import KomootAPIError


GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>inline-track</name>
    <trkseg>
      <trkpt lat="49.0" lon="8.4"><ele>120</ele></trkpt>
      <trkpt lat="49.01" lon="8.41"><ele>125</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""

class TestUploadToolSurfacesFailureCleanly:
    """At the tool layer, success and failure have distinct contracts."""

    @pytest.mark.asyncio
    async def test_upload_failure_raises(self, monkeypatch):
        from komoot_mcp.tools import write_tools

        registered = {}

        class _Mcp:
            def tool(self):
                def deco(fn):
                    registered[fn.__name__] = fn
                    return fn
                return deco

        write_tools.register(_Mcp())

        class _FakeClient:
            async def upload_tour(self, **kwargs):
                raise KomootAPIError(
                    "Komoot rejected the upload (HTTP 400) — GPX is "
                    "probably in route format."
                )

        monkeypatch.setattr(write_tools, "get_client", lambda: _FakeClient())

        with pytest.raises(KomootAPIError, match="400"):
            await registered["komoot_upload_tour"](gpx_content=GPX_SAMPLE)

    @pytest.mark.asyncio
    async def test_success_with_id_returns_url(self, monkeypatch):
        from komoot_mcp.tools import write_tools

        registered = {}

        class _Mcp:
            def tool(self):
                def deco(fn):
                    registered[fn.__name__] = fn
                    return fn
                return deco

        write_tools.register(_Mcp())

        class _FakeClient:
            async def upload_tour(self, **kwargs):
                return {"id": 123456, "status": "uploaded"}

        monkeypatch.setattr(write_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_upload_tour"](
            gpx_content=GPX_SAMPLE,
        )
        assert out == {
            "id": 123456, "status": "uploaded",
            "url": "https://www.komoot.com/tour/123456",
        }
