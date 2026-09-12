#!/usr/bin/env python3
"""
Drive the system over HTTP instead of importing the library.

Start a server first:

    smart-spatial-api serve --port 8000
    # or, from a checkout: uvicorn api.main:app --reload

Then:

    python examples/query_via_http.py
    SMART_SPATIAL_API_KEY=... python examples/query_via_http.py   # if the server requires a key

This is the same surface the React workbench uses, so anything the UI can
do is scriptable. Only the standard library is needed - there is no client
SDK to install.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("SMART_SPATIAL_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.environ.get("SMART_SPATIAL_API_KEY")

DATA_DIR = Path(__file__).parent / "accessibility"


def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if API_KEY:
        # Every route except / and /health requires this header when the
        # server has SMART_SPATIAL_API_KEY set. See docs/DEPLOYMENT.md.
        req.add_header("X-API-Key", API_KEY)

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach {BASE_URL}: {exc.reason}. Is the server running?"
        ) from exc


def ask(question: str, inputs: dict) -> dict:
    """
    Send one question to /query.

    `inputs` is required and must be an object, even when empty: it is
    where the data the question refers to is passed in, keyed by role
    ("vector", "raster", or a named layer).
    """
    return request("POST", "/query", {"query": question, "inputs": inputs})


def main() -> None:
    health = request("GET", "/health")
    print(f"Server: {health['status']} · {len(health['plugin_modules'])} plugin modules")

    sites = json.loads((DATA_DIR / "sites.geojson").read_text())

    response = ask("Display the sites on the map", {"vector": sites})

    print(f"\nRequest id: {response.get('request_id')}")
    print(f"Status:     {response.get('status')}")
    # Not every path fills the same field yet: the direct display path
    # answers in "message", the planning path in "answer". Response
    # unification is an open migration (docs/ADR-002-artifact-based-response.md).
    print(f"Answer:     {response.get('answer') or response.get('message')}")

    # Layers come back as GeoJSON ready to hand to a map client.
    for layer in response.get("layers") or []:
        summary = layer.get("summary") or {}
        print(
            f"  layer {layer.get('name')}: {layer.get('type')}"
            f" · {summary.get('feature_count', '?')} features"
        )

    for warning in response.get("warnings") or []:
        print(f"  warning: {warning}")

    # Outputs are grouped by kind: files, vectors, tables, rasters.
    # Generated files are fetched back from
    # /requests/{request_id}/outputs/files/{name}.
    outputs = response.get("outputs") or {}
    for kind, items in outputs.items():
        if items:
            print(f"  {kind}: {len(items)}")

    # The trace of what ran is kept per request and can be fetched later.
    request_id = response.get("request_id")
    if request_id:
        detail = request("GET", f"/requests/{request_id}")
        print(f"\nStored request {request_id}: {detail.get('status', 'recorded')}")


if __name__ == "__main__":
    main()
