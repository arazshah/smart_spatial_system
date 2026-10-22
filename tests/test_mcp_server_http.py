"""MCP HTTP transport: auth, fail-closed exposure rules, tools/list and tools/call."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from starlette.testclient import TestClient  # noqa: E402

from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from smart_spatial_system.interfaces.mcp.config import McpConfigError, McpSettings  # noqa: E402
from smart_spatial_system.interfaces.mcp.runtime import ToolRuntime  # noqa: E402
from smart_spatial_system.interfaces.mcp.server import build_http_app, build_server  # noqa: E402

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
POINTS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [51.0, 35.0]},
        }
    ],
}


@pytest.fixture(scope="module")
def server():
    registry = CapabilityRegistry.from_plugin_modules(tolerant=True)
    return build_server(ToolRuntime(registry, McpSettings()))


def rpc(client, method, params=None, key="secret"):
    headers = dict(HEADERS)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp", json=body, headers=headers)


def test_http_requires_api_key_by_default(server):
    with pytest.raises(McpConfigError):
        build_http_app(server, McpSettings())


def test_non_loopback_requires_key_and_allowed_hosts(server):
    with pytest.raises(McpConfigError):
        build_http_app(server, McpSettings(), host="0.0.0.0", allow_unauthenticated=True)
    with pytest.raises(McpConfigError):
        build_http_app(server, McpSettings(api_key="secret"), host="0.0.0.0")
    build_http_app(
        server, McpSettings(api_key="secret", allowed_hosts=("mcp.example.org",)), host="0.0.0.0"
    )


def test_auth_is_enforced_and_tools_work(server):
    app = build_http_app(server, McpSettings(api_key="secret"))
    with TestClient(app, base_url="http://localhost") as client:
        assert rpc(client, "tools/list", key=None).status_code == 401
        assert rpc(client, "tools/list", key="wrong").status_code == 401
        assert client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**HEADERS, "X-API-Key": "secret"},
        ).status_code == 200

        listed = rpc(client, "tools/list")
        assert listed.status_code == 200
        names = {t["name"] for t in _payload(listed)["result"]["tools"]}
        assert "buffer_vector_features" in names
        assert "load_local_vector" not in names  # hidden by default policy

        called = rpc(
            client,
            "tools/call",
            {"name": "buffer_vector_features", "arguments": {"features": POINTS, "distance": 1}},
        )
        result = _payload(called)["result"]
        assert not result.get("isError")
        assert result["structuredContent"]["features"][0]["geometry"]["type"] == "Polygon"

        denied = rpc(
            client, "tools/call", {"name": "load_local_vector", "arguments": {"path": "/etc/passwd"}}
        )
        assert _payload(denied)["result"]["isError"] is True


def test_dns_rebinding_host_is_rejected(server):
    app = build_http_app(server, McpSettings(api_key="secret"))
    with TestClient(app, base_url="http://evil.example") as client:
        assert rpc(client, "tools/list").status_code in {400, 403, 421}


def _payload(response):
    import json

    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [ln[5:].strip() for ln in response.text.splitlines() if ln.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


def test_stdio_end_to_end():
    import asyncio
    import sys
    from pathlib import Path

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def main():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "smart_spatial_system", "mcp"],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            ok = await session.call_tool(
                "buffer_vector_features", {"features": POINTS, "distance": 1}
            )
            hidden = await session.call_tool("load_local_vector", {"path": "/etc/passwd"})
            return names, ok, hidden

    names, ok, hidden = asyncio.run(main())
    assert "buffer_vector_features" in names and "load_local_vector" not in names
    assert not ok.is_error
    assert hidden.is_error
