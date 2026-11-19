"""End-to-end: spawn the real MCP server over stdio and drive it with a real MCP client.

Covers discovery, a chained metric-CRS workflow, and the fail-closed security model.
Skipped when the optional ``mcp`` extra or the console script is missing.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
anyio = pytest.importorskip("anyio")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _pt(lon, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


HOMES = {"type": "FeatureCollection", "features": [
    _pt(51.4105, 35.7570, name="home_A"), _pt(51.4200, 35.7000, name="home_B"), _pt(51.3300, 35.7800, name="home_C")]}
METRO = {"type": "FeatureCollection", "features": [
    _pt(51.4108, 35.7578, name="Vanak"), _pt(51.4190, 35.7010, name="Enghelab")]}


def _params() -> StdioServerParameters:
    script = shutil.which("smart-spatial-api")
    if script:
        return StdioServerParameters(command=script, args=["mcp"], cwd=str(ROOT))
    return StdioServerParameters(command=sys.executable, args=["-m", "smart_spatial_system.cli", "mcp"], cwd=str(ROOT))


def _run(coro_fn):
    result = {}

    async def runner():
        async with stdio_client(_params()) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result["v"] = await coro_fn(session)

    anyio.run(runner)
    return result["v"]


def test_discovery_and_hidden_tools():
    async def go(s):
        return {t.name for t in (await s.list_tools()).tools}

    names = _run(go)
    assert len(names) >= 80
    assert {"buffer_vector_features", "find_nearest_neighbors", "artifact_get"} <= names
    # risky classes are hidden by default
    assert "load_local_vector" not in names and "fetch_postgis_layer" not in names


def test_chained_metric_workflow():
    async def go(s):
        async def utm(fc):
            res = await s.call_tool("transform_vector_crs", {"features": fc, "source_crs": "EPSG:4326", "target_crs": "EPSG:32639"})
            assert not res.is_error
            return res.structured_content

        homes, metro = await utm(HOMES), await utm(METRO)
        nn = (await s.call_tool("find_nearest_neighbors", {"source_features": homes, "target_features": metro, "k": 1})).structured_content
        dist = {f["properties"]["name"]: f["properties"]["_nearest_distance"] for f in nn["features"]}
        buf = (await s.call_tool("buffer_vector_features", {"features": metro, "distance": 500, "units": "meters"})).structured_content
        area = (await s.call_tool("calculate_area_perimeter", {"features": buf})).structured_content
        inside = await s.call_tool("filter_points_in_polygon", {"points": homes, "polygons": buf})
        return dist, buf, area, inside

    dist, buf, area, inside = _run(go)
    assert 80 < dist["home_A"] < 110 and 120 < dist["home_B"] < 170 and dist["home_C"] > 7000
    assert "warnings" not in buf["metadata"]
    for f in area["features"]:
        assert 0.77e6 < f["properties"]["_area"] < 0.79e6  # ~ pi * 500^2 m2
    assert [f["properties"]["name"] for f in inside.structured_content["features"]] == ["home_A", "home_B"]


def test_buffer_on_degrees_warns_instead_of_silently_wrong():
    async def go(s):
        return (await s.call_tool("buffer_vector_features", {"features": METRO, "distance": 500, "units": "meters"})).structured_content

    out = _run(go)
    assert any("degrees" in w for w in out["metadata"]["warnings"])


def test_security_fail_closed():
    async def go(s):
        hidden = await s.call_tool("load_local_vector", {"path": "/etc/passwd"})
        unknown = await s.call_tool("no_such_tool", {})
        extra = await s.call_tool("calculate_area_perimeter", {"features": METRO, "dsn": "postgresql://x"})
        return hidden, unknown, extra

    hidden, unknown, extra = _run(go)
    assert hidden.is_error and unknown.is_error and extra.is_error
    assert hidden.content[0].text.replace("load_local_vector", "X") == unknown.content[0].text.replace("no_such_tool", "X")
    assert "dsn" in extra.content[0].text
