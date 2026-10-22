"""Artifact store and artifact-aware MCP runtime (no `mcp` package needed)."""

from __future__ import annotations

import os
import stat
import time

import anyio
import pytest

from orchestrator.capability_registry import CapabilityRegistry
from smart_spatial_system.interfaces.mcp.artifacts import ArtifactError, ArtifactStore
from smart_spatial_system.interfaces.mcp.config import McpSettings
from smart_spatial_system.interfaces.mcp.policy import PolicyViolation
from smart_spatial_system.interfaces.mcp.runtime import ToolCallError, ToolRuntime

RASTER = {
    "data": [[1, 2], [3, 4]],
    "metadata": {"crs": "EPSG:4326", "transform": [1, 0, 0, 0, -1, 2], "nodata": None},
}


def point_collection(count=1):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"i": i},
                "geometry": {"type": "Point", "coordinates": [51.0 + i * 1e-4, 35.0]},
            }
            for i in range(count)
        ],
    }


@pytest.fixture(scope="module")
def registry():
    return CapabilityRegistry.from_plugin_modules(tolerant=True)


def make_runtime(registry, tmp_path, **overrides):
    settings = McpSettings(artifacts_dir=str(tmp_path / "art"), **overrides)
    return ToolRuntime(registry, settings)


def call(runtime, name, arguments):
    return anyio.run(runtime.call, name, arguments)


# ----------------------------------------------------------------------- store
def test_store_roundtrip_and_permissions(tmp_path):
    store = ArtifactStore(tmp_path / "s")
    item = store.put("json", {"a": 1}, {"s": 1})
    assert item.artifact_id.startswith("art_")
    got = store.get(item.artifact_id)
    assert got.data == {"a": 1} and got.kind == "json"
    assert stat.S_IMODE(os.stat(tmp_path / "s").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "s" / f"{item.artifact_id}.json").st_mode) == 0o600
    assert store.delete(item.artifact_id) is True
    with pytest.raises(ArtifactError):
        store.get(item.artifact_id)


@pytest.mark.parametrize(
    "bad",
    ["../etc/passwd", "/etc/passwd", "art_short", "art_" + "a" * 31 + "/", "", None, 5,
     "art_" + "a" * 32 + ".json", "art_" + "a" * 31 + "\x00"],
)
def test_store_rejects_malformed_ids(tmp_path, bad):
    store = ArtifactStore(tmp_path / "s")
    with pytest.raises(ArtifactError):
        store.get(bad)
    with pytest.raises(ArtifactError):
        store.delete(bad)


def test_store_expiry_and_limits(tmp_path):
    store = ArtifactStore(tmp_path / "s", ttl_seconds=10, max_artifact_bytes=100, max_total_bytes=250)
    item = store.put("json", {"a": 1})
    old = time.time() - 60
    os.utime(tmp_path / "s" / f"{item.artifact_id}.json", (old, old))
    with pytest.raises(ArtifactError):
        store.get(item.artifact_id)
    with pytest.raises(ArtifactError):
        store.put("json", {"x": "y" * 500})  # per-artifact cap
    store.put("json", {"x": "y" * 40})
    store.put("json", {"x": "y" * 40})
    with pytest.raises(ArtifactError):
        store.put("json", {"x": "y" * 40})  # total cap


def test_store_ignores_foreign_files(tmp_path):
    store = ArtifactStore(tmp_path / "s")
    (tmp_path / "s" / "art_" ).write_text("x")
    (tmp_path / "secret.json").write_text('{"kind":"json","data":1}')
    with pytest.raises(ArtifactError):
        store.get("art_" + "." * 32)


# --------------------------------------------------------------------- runtime
def test_raster_results_become_handles_and_chain(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path)
    handle = call(
        runtime,
        "threshold_raster",
        {"raster": RASTER, "operator": "gt", "threshold": 2},
    )
    assert handle["type"] == "artifact" and handle["kind"] == "raster"
    assert handle["summary"]["shape"] == [2, 2]
    assert "data" not in handle

    stats = call(runtime, "calculate_raster_statistics", {"raster": {"artifact_id": handle["artifact_id"]}})
    assert stats  # the handle was resolved server-side and fed to the next tool


def test_large_vector_results_become_paged_artifacts(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path, inline_output_bytes=2000)
    small = call(runtime, "inspect_vector", {"vector": point_collection(1)})
    assert small.get("type") != "artifact"

    handle = call(
        runtime, "buffer_vector_features", {"features": point_collection(40), "distance": 1}
    )
    assert handle["kind"] == "vector" and handle["summary"]["feature_count"] == 40

    page = call(runtime, "artifact_get", {"artifact_id": handle["artifact_id"], "limit": 5, "offset": 10})
    assert page["total_features"] == 40 and len(page["features"]) == 5

    # and the handle feeds a vector tool directly
    again = call(runtime, "inspect_vector", {"vector": {"artifact_id": handle["artifact_id"]}})
    assert again["feature_count"] == 40

    assert call(runtime, "artifact_delete", {"artifact_id": handle["artifact_id"]}) == {"deleted": True}
    with pytest.raises(ToolCallError):
        call(runtime, "artifact_get", {"artifact_id": handle["artifact_id"]})


def test_kind_mismatch_and_bad_refs_are_refused(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path)
    raster = call(runtime, "threshold_raster", {"raster": RASTER, "operator": "gt", "threshold": 2})
    with pytest.raises(PolicyViolation):
        call(runtime, "inspect_vector", {"vector": {"artifact_id": raster["artifact_id"]}})
    with pytest.raises(PolicyViolation):  # refs only where data is expected
        call(runtime, "buffer_vector_features",
             {"features": point_collection(), "units": {"artifact_id": raster["artifact_id"]}})
    with pytest.raises(ToolCallError):
        call(runtime, "inspect_vector", {"vector": {"artifact_id": "art_" + "a" * 32}})
    with pytest.raises(ToolCallError):
        call(runtime, "inspect_vector", {"vector": {"artifact_id": "../../etc/passwd"}})


def test_artifacts_can_be_disabled(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path, artifacts_enabled=False)
    assert "artifact_get" not in runtime.specs
    result = call(runtime, "threshold_raster", {"raster": RASTER, "operator": "gt", "threshold": 2})
    assert result["data"] == [[0, 0], [1, 1]]
    assert "array" not in result and "payload" not in result  # alias duplicates are not sent
    with pytest.raises(Exception):  # no store: a handle is just an unusable object
        call(runtime, "inspect_vector", {"vector": {"artifact_id": "art_" + "a" * 32}})


def test_artifact_get_enforces_output_limit_for_raster_data(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path, max_output_bytes=500)
    big = {"data": [[float(i + j) for j in range(40)] for i in range(40)], "metadata": RASTER["metadata"]}
    handle = call(runtime, "threshold_raster", {"raster": big, "operator": "gt", "threshold": 2})
    plain = call(runtime, "artifact_get", {"artifact_id": handle["artifact_id"]})
    assert plain["data_included"] is False
    with pytest.raises(PolicyViolation):
        call(runtime, "artifact_get", {"artifact_id": handle["artifact_id"], "include_data": True})


def test_ndvi_processor_is_never_exposed(registry, tmp_path):
    runtime = make_runtime(
        registry, tmp_path,
        enabled_risks=frozenset({"filesystem", "network", "database", "sql"}),
        allowed_roots=("/tmp",), allowed_hosts=("example.org",),
    )
    assert "ndvi_processor" not in runtime.specs


class _FakeDescriptor:
    description = "async demo"
    required_inputs = ["features"]
    optional_inputs: list[str] = []
    output_kind = "json"
    long_running = False


class _FakeBinding:
    def __init__(self, fn):
        self.callable = fn


class _FakeRegistry:
    def __init__(self, fn):
        self._fn = fn

    def registered_capability_names(self):
        return ["async_demo"]

    def descriptor_for(self, name):
        return _FakeDescriptor()

    def resolve(self, name):
        return _FakeBinding(self._fn)


def test_async_capabilities_are_awaited(tmp_path):
    async def async_demo(features):
        return {"n": len(features["features"])}

    runtime = ToolRuntime(_FakeRegistry(async_demo), McpSettings(artifacts_dir=str(tmp_path / "a")))
    assert call(runtime, "async_demo", {"features": point_collection(3)}) == {"n": 3}


def test_output_limit_applies_to_inline_results_but_oversize_goes_to_artifact(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path, max_output_bytes=50)
    result = call(runtime, "buffer_vector_features", {"features": point_collection(), "distance": 1})
    assert result["type"] == "artifact"  # not inlined past max_output_bytes


def test_list_of_raster_handles_feeds_mosaic_and_overlay(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path)

    def tile(x0):
        return {"data": [[1, 2], [3, 4]],
                "metadata": {"crs": "EPSG:3857", "transform": [1, 0, x0, 0, -1, 2], "nodata": None}}

    handles = [
        call(runtime, "threshold_raster", {"raster": tile(0), "operator": "gt", "threshold": 0}),
        call(runtime, "threshold_raster", {"raster": tile(2), "operator": "gt", "threshold": 0}),
    ]
    mosaic = call(runtime, "mosaic_rasters",
                  {"rasters": [{"artifact_id": h["artifact_id"]} for h in handles]})
    assert mosaic["type"] == "artifact" and mosaic["summary"]["shape"] == [2, 4]
    vector = runtime.store.put("vector", point_collection())
    with pytest.raises(PolicyViolation):  # the kind is checked for every list item
        call(runtime, "mosaic_rasters", {"rasters": [
            {"artifact_id": handles[0]["artifact_id"]}, {"artifact_id": vector.artifact_id},
        ]})


def test_overlays_accept_vector_handles(registry, tmp_path):
    runtime = make_runtime(registry, tmp_path, inline_output_bytes=100)
    handle = call(runtime, "buffer_vector_features", {"features": point_collection(3), "distance": 0.001})
    assert handle["type"] == "artifact" and handle["kind"] == "vector"
    result = call(runtime, "render_static_map", {
        "features": point_collection(2), "overlays": [{"artifact_id": handle["artifact_id"]}]})
    assert result["type"] == "artifact" or [layer["name"] for layer in result["layers"]] == ["overlay_1", "main"]
    raster = call(runtime, "threshold_raster", {"raster": RASTER, "operator": "gt", "threshold": 2})
    with pytest.raises(PolicyViolation):  # kind is checked for list items too
        call(runtime, "render_static_map", {
            "features": point_collection(2), "overlays": [{"artifact_id": raster["artifact_id"]}]})
