"""MCP result envelopes and explicit tool side-effect hints."""

import json

from mcp import types
from mcp.server import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.exceptions import UrlElicitationRequiredError
from pydantic import ValidationError
import requests

from komoot_mcp.client import KomootAPIError
from komoot_mcp.routing import RoutingError


_READ_ONLY = {
    "komoot_list_tours", "komoot_get_tour", "komoot_get_tour_full",
    "komoot_get_highlight", "komoot_tour_weather", "komoot_get_user_profile",
    "komoot_get_tour_photos", "komoot_get_tour_line", "komoot_list_user_highlights",
    "komoot_get_tour_coordinates", "komoot_get_tour_gpx", "komoot_get_tour_directions",
    "komoot_get_tour_way_types", "komoot_get_tour_surfaces", "komoot_get_tour_timeline",
    "komoot_geocode", "komoot_recommend_tours_near", "komoot_smart_tours_near",
    "komoot_smart_tour_for_highlight", "komoot_discover_with_attributes",
    "komoot_route_attribute_options", "komoot_get_highlight_images",
    "komoot_get_highlight_tips", "komoot_get_collection", "komoot_get_collection_tours",
    "komoot_resolve_share_url", "komoot_find_highlights_nearby",
    "komoot_list_bookmarked_highlights", "komoot_get_smart_tour",
    "komoot_plan_native_route", "komoot_reverse_geocode",
}
_CREATE = {
    "komoot_login", "komoot_upload_tour", "komoot_plan_and_upload",
    "komoot_create_share_link", "komoot_bookmark_highlight", "komoot_import_gpx_route",
    "komoot_create_planned_tour", "komoot_plan_and_save_tour",
}
_CHANGE = {
    "komoot_modify_tour", "komoot_delete_tour", "komoot_modify_tour_extended",
    "komoot_revoke_share_link", "komoot_unbookmark_highlight", "komoot_publish_prepared",
    "komoot_download_tour_gpx", "komoot_download_tour_fit", "komoot_export_all_tours",
    "komoot_download_smart_tour_gpx",
}

# Explicit membership prevents a new write tool from inheriting read-only hints.
ANNOTATIONS = {
    **{name: types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                  idempotentHint=True, openWorldHint=True) for name in _READ_ONLY},
    **{name: types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                  idempotentHint=False, openWorldHint=True) for name in _CREATE},
    **{name: types.ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                  idempotentHint=False, openWorldHint=True) for name in _CHANGE},
    "komoot_preview_prepared": types.ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    "komoot_adopt_receipt": types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "data": {"type": "object", "additionalProperties": True},
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "retry_safe": {"type": "boolean"},
                "operation_state": {"enum": ["unchanged", "not_attempted", "unknown"]},
                "status_code": {"type": "integer"},
            },
            "required": ["code", "message", "retry_safe", "operation_state"],
            "additionalProperties": False,
        },
    },
    "required": ["ok"],
    "oneOf": [
        {"properties": {"ok": {"const": True}}, "required": ["data"], "not": {"required": ["error"]}},
        {"properties": {"ok": {"const": False}}, "required": ["error"], "not": {"required": ["data"]}},
    ],
    "additionalProperties": False,
}


def _result(payload, *, is_error=False):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, allow_nan=False))],
        structuredContent=payload,
        isError=is_error,
    )


def _rejected(code, message):
    return _result({"ok": False, "error": {
        "code": code, "message": message, "retry_safe": False,
        "operation_state": "not_attempted",
    }}, is_error=True)


def _failure(error, *, read_only):
    while isinstance(error, ToolError) and error.__cause__ is not None:
        error = error.__cause__
    status = getattr(error, "status_code", None)
    retry_safe = False
    state = "unchanged" if read_only else "unknown"
    if isinstance(error, ValidationError):
        code = "internal_error"
    elif isinstance(error, PermissionError):
        code = "permission_denied"
    elif isinstance(error, FileNotFoundError):
        code = "not_found"
    elif isinstance(error, requests.Timeout):
        code = "timeout"
        retry_safe = read_only
    elif isinstance(error, requests.RequestException):
        code = "transport_error"
        retry_safe = read_only
    elif isinstance(error, (KomootAPIError, RoutingError)):
        codes = {401: "authentication_failed", 403: "permission_denied",
                 404: "not_found", 429: "rate_limited"}
        code = codes.get(status, "upstream_error") if isinstance(status, int) else "upstream_error"
        retry_safe = read_only and (status == 429 or isinstance(status, int) and status >= 500)
    elif isinstance(error, ValueError):
        code = "invalid_input"
    else:
        code = "internal_error"
    message = "Tool execution failed" if code == "internal_error" else str(error)
    details = {"code": code, "message": message, "retry_safe": retry_safe,
               "operation_state": state}
    if isinstance(status, int):
        details["status_code"] = status
    return _result({"ok": False, "error": details}, is_error=True)


class KomootMCP(FastMCP):
    """Expose one result contract without changing internal handler payloads."""

    def __init__(self, *args, read_only=False, **kwargs):
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        self.read_only = read_only
        super().__init__(*args, **kwargs)

    def _setup_handlers(self):
        super()._setup_handlers()
        self._mcp_server.call_tool(validate_input=False)(self._call_structured_tool)

    async def list_tools(self):
        tools = await super().list_tools()
        if self.read_only:
            tools = [tool for tool in tools if ANNOTATIONS[tool.name].readOnlyHint is True]
        for tool in tools:
            tool.inputSchema["additionalProperties"] = False
            tool.annotations = ANNOTATIONS[tool.name]
            tool.outputSchema = RESULT_SCHEMA
        return tools

    async def _call_structured_tool(self, name, arguments):
        policy = ANNOTATIONS.get(name)
        tool = self._tool_manager.get_tool(name)
        if policy is None or tool is None:
            return _rejected("unknown_tool", f"Unknown tool: {name}")
        if self.read_only and policy.readOnlyHint is not True:
            return _rejected("permission_denied", f"Tool is disabled in read-only mode: {name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _rejected("invalid_input", "Tool arguments must be an object")
        unknown = set(arguments) - set(tool.parameters.get("properties", {}))
        if unknown:
            return _rejected("unknown_parameters", "Unknown parameters: " + ", ".join(sorted(unknown)))
        try:
            # Validate before entering the handler, where failures can follow account changes.
            tool.fn_metadata.arg_model.model_validate(tool.fn_metadata.pre_parse_json(arguments))
        except ValidationError as error:
            message = "; ".join(
                ".".join(str(part) for part in item["loc"]) + ": " + item["msg"]
                for item in error.errors(include_input=False, include_context=False, include_url=False)
            )
            return _rejected("invalid_input", message)
        try:
            # Execute before SDK content conversion so geometry is serialized only once.
            data = await self._tool_manager.call_tool(
                name, arguments, context=self.get_context(), convert_result=False,
            )
            if not isinstance(data, dict):
                raise TypeError("Tool handler must return structured dictionary data")
            return _result({"ok": True, "data": data})
        except UrlElicitationRequiredError:
            raise
        except Exception as error:
            return _failure(error, read_only=policy.readOnlyHint is True)
