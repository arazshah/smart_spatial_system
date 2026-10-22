"""
smart_spatial_system.interfaces.mcp.server

MCP adapter over ``ToolRuntime`` (requires the optional ``mcp`` package).
Two transports share one runtime: stdio (local clients, in-app testing) and
stateless streamable HTTP (cloud deployments).
"""

from __future__ import annotations

import json
from typing import Any

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings

from .auth import ApiKeyMiddleware
from .config import McpConfigError, McpSettings
from .policy import PolicyViolation, loopback_host
from .runtime import ToolCallError, ToolNotFound, ToolRuntime

SERVER_NAME = "smart-spatial-system"
_LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "127.0.0.1:*", "localhost:*", "[::1]:*"]


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=message)], is_error=True
    )


def build_server(runtime: ToolRuntime, *, version: str = "") -> Server:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        tools = [
            types.Tool(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                annotations=types.ToolAnnotations(
                    read_only_hint="filesystem" not in spec.risks,
                    destructive_hint=False,
                    open_world_hint="network" in spec.risks,
                ),
            )
            for spec in runtime.specs.values()
        ]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            data = await runtime.call(params.name, params.arguments)
        except (ToolNotFound, PolicyViolation, ToolCallError) as exc:
            return _error(str(exc))
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(data, separators=(",", ":")))],
            structured_content=data if isinstance(data, dict) else {"result": data},
        )

    return Server(
        SERVER_NAME,
        version=version,
        instructions=(
            "Geospatial analysis tools. Vector data is exchanged as GeoJSON; "
            "feed one tool's output into the next."
        ),
        get_tool_input_schema=lambda name: (
            runtime.specs[name].input_schema if name in runtime.specs else None
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def build_http_app(
    server: Server,
    settings: McpSettings,
    *,
    host: str = "127.0.0.1",
    allow_unauthenticated: bool = False,
):
    """ASGI app for streamable HTTP. Refuses unsafe exposure (fail closed)."""
    exposed = not loopback_host(host)
    if not settings.api_key and not allow_unauthenticated:
        raise McpConfigError(
            "HTTP transport requires an API key (SMART_SPATIAL_MCP_API_KEY or "
            "SMART_SPATIAL_API_KEY). Pass allow_unauthenticated only for local tests."
        )
    if exposed and not settings.api_key:
        raise McpConfigError("Refusing to serve unauthenticated MCP on a non-loopback address.")
    if exposed and not settings.allowed_hosts:
        raise McpConfigError(
            "Binding a non-loopback address needs SMART_SPATIAL_MCP_ALLOWED_HOSTS "
            "(the Host header values clients use) for DNS-rebinding protection."
        )
    allowed_hosts = list(settings.allowed_hosts) if exposed else _LOOPBACK_HOSTS
    app = server.streamable_http_app(
        stateless_http=True,
        host=host,
        max_request_body_size=settings.max_input_bytes + 65536,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[],
        ),
    )
    return ApiKeyMiddleware(app, settings.api_key) if settings.api_key else app
