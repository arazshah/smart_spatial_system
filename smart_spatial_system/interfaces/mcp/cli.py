"""
smart_spatial_system.interfaces.mcp.cli

``smart-spatial-api mcp`` entrypoint. Imports of the optional ``mcp``
dependency are lazy so the rest of the CLI works without the extra.
"""

from __future__ import annotations

import sys

from .config import McpConfigError, McpSettings


def run(*, transport: str, host: str, port: int, allow_unauthenticated: bool = False) -> None:
    try:
        import anyio
        import uvicorn

        from .server import build_http_app, build_server
    except ImportError as exc:
        sys.exit(
            f"MCP support is not installed ({exc}). "
            'Install it with: pip install "smart_spatial_system[mcp]"'
        )

    from orchestrator.capability_registry import CapabilityRegistry

    from .runtime import ToolRuntime

    try:
        settings = McpSettings.from_env()
        registry = CapabilityRegistry.from_plugin_modules(tolerant=True)
        runtime = ToolRuntime(registry, settings)
        server = build_server(runtime)
        for skipped in registry.skipped_plugins:
            print(f"skipped plugin {skipped['module']}: {skipped['error']}", file=sys.stderr)
        print(
            f"MCP: {len(runtime.specs)} tools exposed, {len(runtime.hidden)} hidden by policy.",
            file=sys.stderr,
        )
        if transport == "stdio":
            from mcp.server.stdio import stdio_server

            async def _serve_stdio() -> None:
                async with stdio_server() as (read, write):
                    await server.run(read, write, server.create_initialization_options())

            anyio.run(_serve_stdio)
        else:
            app = build_http_app(
                server, settings, host=host, allow_unauthenticated=allow_unauthenticated
            )
            uvicorn.run(app, host=host, port=port)
    except McpConfigError as exc:
        sys.exit(f"MCP configuration error: {exc}")
