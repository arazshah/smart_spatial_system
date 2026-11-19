"""MCP client for the s3geo server: runs `smart-spatial-api mcp` as a subprocess (stdio) and
keeps one session open on a background asyncio loop, so Qt/worker threads can call it
synchronously."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

_ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "USER",
              "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV", "XDG_RUNTIME_DIR")


class McpBridgeError(RuntimeError):
    pass


def default_server_command(override: str = "") -> tuple[list[str], dict[str, str]]:
    """(command, extra env) to start the MCP server in the Python that has s3geo installed."""
    if override.strip():
        return shlex.split(override), {}
    exe = shutil.which("smart-spatial-api")
    if exe:
        return [exe, "mcp"], {}
    try:
        import s3geo
    except ImportError as exc:
        raise McpBridgeError("s3geo is not importable, cannot locate the MCP server.") from exc
    site = Path(s3geo.__file__).resolve().parents[1]          # .../site-packages
    for cand in (site.parents[2] / "bin" / "smart-spatial-api",
                 site.parents[2] / "Scripts" / "smart-spatial-api.exe"):
        if cand.exists():
            return [str(cand), "mcp"], {}
    # sys.executable is the QGIS binary inside QGIS, so look for a real interpreter
    py = sys.executable if Path(sys.executable).name.lower().startswith("python") else (
        shutil.which("python3") or shutil.which("python"))
    if not py:
        raise McpBridgeError("No Python interpreter found to start the MCP server; set "
                             "'MCP server command' in the chat settings.")
    return [py, "-m", "smart_spatial_system.cli", "mcp"], {"PYTHONPATH": str(site)}


class McpBridge:
    def __init__(self, command: list[str], extra_env: dict[str, str] | None = None,
                 *, call_timeout: float = 120.0) -> None:
        self.command = command
        self.call_timeout = call_timeout
        env = {k: v for k, v in os.environ.items()
               if k in _ENV_ALLOW or k.startswith("SMART_SPATIAL_")}
        env.update(extra_env or {})
        self.env = env
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._session = None
        self._tools: list[dict[str, Any]] = []
        self._error: BaseException | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self, timeout: float = 90.0) -> None:
        if self._thread is not None:
            return
        try:
            import mcp  # noqa: F401
        except ImportError as exc:
            raise McpBridgeError(
                "The 'mcp' Python package is missing in QGIS' Python. Install the extra: "
                "pip install \"smart_spatial_system[mcp]\" (then 'numpy<2' 'pandas<3' "
                "'geopandas<1.1' on Linux).") from exc
        self._thread = threading.Thread(target=self._run_loop, name="s3geo-mcp", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.stop()
            raise McpBridgeError("The MCP server did not start in time.")
        if self._error is not None:
            err = self._error
            self.stop()
            raise McpBridgeError(f"The MCP server failed to start: {err}")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()

    async def _main(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stop = asyncio.Event()
        params = StdioServerParameters(command=self.command[0], args=self.command[1:],
                                       env=self.env)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self._tools = [{"name": t.name, "description": t.description or "",
                                    "input_schema": t.input_schema} for t in listing.tools]
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            self._error = exc
        finally:
            self._session = None
            self._ready.set()

    def stop(self) -> None:
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)
        if self._thread is not None:
            self._thread.join(10)
        self._thread = None
        self._loop = None

    # ------------------------------------------------------------------ use
    def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
        """Returns (is_error, data). data is the structured result, parsed JSON, or text."""
        if self._session is None or self._loop is None:
            raise McpBridgeError("The MCP server is not running.")
        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop)
        try:
            res = fut.result(self.call_timeout)
        except FutureTimeout as exc:
            fut.cancel()
            raise McpBridgeError(f"{name} timed out after {self.call_timeout:.0f}s") from exc
        text = res.content[0].text if res.content else ""
        if res.is_error:
            return True, text
        if res.structured_content is not None:
            data = res.structured_content
            if isinstance(data, dict) and set(data) == {"result"}:
                data = data["result"]
            return False, data
        try:
            return False, json.loads(text)
        except json.JSONDecodeError:
            return False, text
