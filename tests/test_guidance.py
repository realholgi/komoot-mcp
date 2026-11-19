"""Consumer-visible discovery and side-effect-free prompt rendering."""

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
import pytest
import requests

from komoot_mcp import context
from komoot_mcp.auth import AuthManager
from komoot_mcp.routing import NATIVE_SPORT_PROFILES
from komoot_mcp.server import create_server


def _prompt_text(result):
    return "\n".join(message.content.text for message in result.messages
                     if isinstance(message.content, TextContent))


async def _read_json(session, uri):
    result = await session.read_resource(uri)
    return json.loads(result.contents[0].text)


@pytest.fixture
def forbid_side_effects(monkeypatch):
    @contextmanager
    def guard():
        forbidden = Mock(side_effect=AssertionError("Guidance must not access accounts, files or network"))
        with monkeypatch.context() as protected:
            protected.setattr(AuthManager, "__init__", forbidden)
            protected.setattr(context, "get_client", forbidden)
            protected.setattr(context, "get_auth_manager", forbidden)
            protected.setattr(requests.Session, "request", forbidden)
            for name in ("open", "read_text", "write_text", "read_bytes", "write_bytes", "resolve"):
                protected.setattr(Path, name, forbidden)
            yield
            forbidden.assert_not_called()
    return guard


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("http", [False, True])
async def test_resources_report_effective_policy_without_io(read_only, http, forbid_side_effects):
    server = create_server(read_only=read_only)
    token = context.set_http_context() if http else None
    try:
        async with create_connected_server_and_client_session(server) as session:
            with forbid_side_effects():
                resources = (await session.list_resources()).resources
                assert {str(resource.uri) for resource in resources} == {
                    "komoot://capabilities", "komoot://reference",
                }
                assert all(resource.mimeType == "application/json" for resource in resources)
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                capabilities = await _read_json(session, "komoot://capabilities")
                reference = await _read_json(session, "komoot://reference")
        assert capabilities["read_only"] is read_only
        assert capabilities["transport"] == ("http" if http else "stdio")
        assert capabilities["registered_tools"] == sorted(tools)
        assert set(capabilities["offered_tools"]) == set(tools) - set(capabilities["unavailable_tools"])
        assert capabilities["local_filesystem"]["allowed"] is not http
        if read_only:
            assert all(tool.annotations is not None and tool.annotations.readOnlyHint is True
                       for tool in tools.values())
        if http:
            assert "komoot_preview_prepared" not in capabilities["offered_tools"]
            assert "komoot_preview_prepared" in capabilities["unavailable_tools"]
        else:
            assert "komoot_preview_prepared" in capabilities["offered_tools"]
            assert capabilities["unavailable_tools"] == []
        paging = capabilities["coordinate_paging"]
        assert paging["default_limit"] == 500
        assert paging["maximum_limit"] == 1000
        assert reference["sport_profiles"] == NATIVE_SPORT_PROFILES
        assert reference["prepared_artifacts"]["maximum_named_pois_per_route"] == 512
        assert reference["prepared_artifacts"]["poi_name_maximum_utf16_units"] == 30
        assert reference["prepared_artifacts"]["poi_description_maximum_utf16_units"] == 250
        assert reference["result_schema"]["required"] == ["ok"]
    finally:
        if token is not None:
            context.reset_http_context(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
async def test_prompts_are_discoverable_and_only_render_workflow(read_only, forbid_side_effects):
    async with create_connected_server_and_client_session(create_server(read_only=read_only)) as session:
        with forbid_side_effects():
            prompts = {prompt.name: prompt for prompt in (await session.list_prompts()).prompts}
            assert set(prompts) == {"plan_route", "review_prepared_import"}
            arguments = prompts["plan_route"].arguments
            assert arguments is not None
            assert {arg.name for arg in arguments} == {"start", "end", "sport"}
            plan = _prompt_text(await session.get_prompt("plan_route", {
                "start": "Heidelberg", "end": "Neckargemünd", "sport": "gravel_ride",
            }))
            review = _prompt_text(await session.get_prompt("review_prepared_import", {
                "prepared_path": "~/unresolved/prepared.json",
                "publication_directory": "relative/publication", "update": "true",
            }))
    assert "mtb_easy" in plan
    assert "[lat, lng]" in plan
    assert "komoot_plan_native_route" in plan
    assert "komoot_preview_prepared" in review
    assert "~/unresolved/prepared.json" in review
    assert '"update": true' in review
    assert "confirmed=true" not in review
    if read_only:
        assert "komoot_create_planned_tour" not in plan
        assert "komoot_publish_prepared" not in review
        assert "Stop after the preview" in plan
        assert "Stop after the preview" in review
    else:
        assert "explicit user approval" in plan
        assert "explicit user approval" in review
        assert "not approval" in plan
        assert "grants no approval" in review
        assert "reconcile" in plan and "reconcile" in review


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("plan_route", {"start": " ", "end": "B"}),
    ("plan_route", {"start": "A", "end": ""}),
    ("plan_route", {"start": "A", "end": "B", "extra": "unexpected"}),
    ("plan_route", {"start": "A", "end": "B", "sport": "unknown"}),
    ("plan_route", {"start": "A"}),
    ("review_prepared_import", {"prepared_path": "", "publication_directory": "pub"}),
    ("review_prepared_import", {"prepared_path": "p", "publication_directory": " "}),
    ("review_prepared_import", {"prepared_path": "p", "publication_directory": "pub", "update": "yes"}),
])
async def test_prompts_reject_invalid_arguments(name, arguments, forbid_side_effects):
    async with create_connected_server_and_client_session(create_server()) as session:
        with forbid_side_effects(), pytest.raises(McpError):
            await session.get_prompt(name, arguments)


@pytest.mark.asyncio
async def test_http_prompt_never_proposes_local_prepared_operations(forbid_side_effects):
    token = context.set_http_context()
    try:
        async with create_connected_server_and_client_session(create_server()) as session:
            with forbid_side_effects():
                text = _prompt_text(await session.get_prompt("review_prepared_import", {
                    "prepared_path": "p.json", "publication_directory": "pub",
                }))
        assert "unavailable over HTTP" in text
        assert "komoot_preview_prepared" not in text
        assert "komoot_publish_prepared" not in text
    finally:
        context.reset_http_context(token)
    assert context.is_http_context() is False


@pytest.mark.asyncio
async def test_transport_reporting_is_task_local():
    async def inspect(http):
        token = context.set_http_context() if http else None
        try:
            await asyncio.sleep(0)
            return context.is_http_context()
        finally:
            if token is not None:
                context.reset_http_context(token)

    assert await asyncio.gather(inspect(True), inspect(False)) == [True, False]
    assert context.is_http_context() is False


@pytest.mark.asyncio
async def test_server_instructions_reach_initialization():
    server = create_server()
    async with create_connected_server_and_client_session(server) as session:
        initialized = await session.initialize()
    instructions = initialized.instructions
    assert instructions is not None
    assert "[lat, lng]" in instructions
    assert "untrusted data" in instructions
    assert "explicit user approval" in instructions
    assert "Preview" in instructions
    assert "uncertain" in instructions
    assert "Report only observed results" in instructions
