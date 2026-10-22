"""MCP tool runtime against the real capability registry (no `mcp` package needed)."""

from __future__ import annotations

import anyio
import pytest

from orchestrator.capability_registry import CapabilityRegistry
from smart_spatial_system.interfaces.mcp.config import McpSettings
from smart_spatial_system.interfaces.mcp.policy import PolicyViolation
from smart_spatial_system.interfaces.mcp.runtime import ToolCallError, ToolNotFound, ToolRuntime

POINTS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "a"},
            "geometry": {"type": "Point", "coordinates": [51.0, 35.0]},
        }
    ],
}
SENSITIVE = {
    "load_local_vector",
    "load_local_raster",
    "export_vector_geojson",
    "export_raster_copy",
    "export_table",
    "render_pdf",
    "fetch_wfs_features",
    "fetch_wms_map",
    "geocode_place",
    "load_geojson_url",
    "load_csv_points",
    "fetch_osm_features",
    "reverse_geocode_point",
    "fetch_postgis_layer",
    "query_database_postgis",
    "fetch_postgis_sql_layer",
}


@pytest.fixture(autouse=True)
def _isolated_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_SPATIAL_RUNTIME_DIR", str(tmp_path / "rt"))


@pytest.fixture(scope="module")
def registry():
    return CapabilityRegistry.from_plugin_modules(tolerant=True)


def call(runtime, name, arguments):
    return anyio.run(runtime.call, name, arguments)


def test_default_policy_hides_every_sensitive_capability(registry):
    runtime = ToolRuntime(registry, McpSettings())
    assert SENSITIVE.isdisjoint(runtime.specs)
    assert SENSITIVE <= set(runtime.hidden)
    assert "ndvi_processor" in runtime.hidden  # unsupported over MCP, even when risks are enabled
    registered = len(registry.registered_capability_names())
    # everything except sensitive tools and ndvi_processor, plus the 2 artifact built-ins
    assert len(runtime.specs) == registered - len(SENSITIVE) - 1 + 2


def test_hidden_tool_is_indistinguishable_from_unknown(registry):
    runtime = ToolRuntime(registry, McpSettings())
    with pytest.raises(ToolNotFound) as hidden:
        call(runtime, "load_local_vector", {"path": "/etc/passwd"})
    with pytest.raises(ToolNotFound) as unknown:
        call(runtime, "no_such_tool", {})
    assert str(hidden.value).replace("load_local_vector", "X") == str(unknown.value).replace(
        "no_such_tool", "X"
    )


def test_schemas_are_closed_and_never_expose_credentials(registry):
    runtime = ToolRuntime(
        registry,
        McpSettings(
            enabled_risks=frozenset({"filesystem", "network", "database", "sql"}),
            allowed_roots=("/tmp",),
            allowed_hosts=("example.org",),
        ),
    )
    assert SENSITIVE <= set(runtime.specs)
    for spec in runtime.specs.values():
        schema = spec.input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert not {"dsn", "password", "token", "strict_extensions"} & set(schema["properties"])


def test_buffer_roundtrip_returns_json(registry):
    runtime = ToolRuntime(registry, McpSettings())
    result = call(runtime, "buffer_vector_features", {"features": POINTS, "distance": 1})
    assert result["features"][0]["geometry"]["type"] == "Polygon"
    assert isinstance(result["features"][0]["geometry"]["coordinates"], list)


def test_unknown_and_missing_arguments_are_refused(registry):
    runtime = ToolRuntime(registry, McpSettings())
    with pytest.raises(PolicyViolation):
        call(runtime, "buffer_vector_features", {"features": POINTS, "__import__": "os"})
    with pytest.raises(PolicyViolation):
        call(runtime, "buffer_vector_features", {"distance": 1})


def test_input_and_output_limits(registry):
    runtime = ToolRuntime(registry, McpSettings(max_input_bytes=50))
    with pytest.raises(PolicyViolation):
        call(runtime, "buffer_vector_features", {"features": POINTS, "distance": 1})
    runtime = ToolRuntime(registry, McpSettings(max_output_bytes=50, artifacts_enabled=False))
    with pytest.raises(PolicyViolation):
        call(runtime, "buffer_vector_features", {"features": POINTS, "distance": 1})


def test_tool_output_is_valid_input_of_the_next_tool(registry):
    runtime = ToolRuntime(registry, McpSettings(artifacts_enabled=False))
    buffered = call(runtime, "buffer_vector_features", {"features": POINTS, "distance": 1})
    assert buffered["type"] == "FeatureCollection"
    inspected = call(runtime, "inspect_vector", {"vector": buffered})
    assert inspected["feature_count"] == 1


def test_plugin_errors_are_reported_without_leaking_paths(registry, tmp_path):
    settings = McpSettings(
        enabled_risks=frozenset({"filesystem"}), allowed_roots=(str(tmp_path),)
    )
    runtime = ToolRuntime(registry, settings)
    with pytest.raises(ToolCallError) as err:
        call(runtime, "load_local_vector", {"path": str(tmp_path / "missing.geojson")})
    assert str(tmp_path) not in str(err.value)


def test_path_outside_roots_is_refused_before_plugin_runs(registry, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    root = tmp_path / "root"
    root.mkdir()
    runtime = ToolRuntime(
        registry, McpSettings(enabled_risks=frozenset({"filesystem"}), allowed_roots=(str(root),))
    )
    with pytest.raises(PolicyViolation):
        call(runtime, "load_local_vector", {"path": str(outside / "x.geojson")})
    # strict_extensions is server-controlled, not a client knob
    with pytest.raises(PolicyViolation):
        call(
            runtime,
            "load_local_vector",
            {"path": str(root / "a.txt"), "strict_extensions": False},
        )


def test_allowed_file_loads(registry, tmp_path):
    (tmp_path / "pts.geojson").write_text(__import__("json").dumps(POINTS))
    runtime = ToolRuntime(
        registry,
        McpSettings(enabled_risks=frozenset({"filesystem"}), allowed_roots=(str(tmp_path),)),
    )
    result = call(runtime, "load_local_vector", {"path": str(tmp_path / "pts.geojson")})
    assert len(result["features"]) == 1


def test_file_names_and_template_names_must_be_bare(registry, tmp_path):
    runtime = ToolRuntime(
        registry,
        McpSettings(enabled_risks=frozenset({"filesystem"}), allowed_roots=(str(tmp_path),),
                    artifacts_enabled=False),
    )
    for bad in ("../../etc/passwd", "/etc/passwd", "a/b.csv", ".hidden", "x\x00.csv", ""):
        with pytest.raises(PolicyViolation):
            call(runtime, "export_table",
                 {"features": POINTS, "output_dir": str(tmp_path), "filename": bad})
    for bad in ("../../etc/passwd", "../report.html", "/etc/hosts"):
        with pytest.raises(PolicyViolation):
            call(runtime, "render_pdf", {"report": {"title": "t"}, "template_name": bad})
    with pytest.raises(PolicyViolation):  # output_dir must stay inside the allowed roots
        call(runtime, "export_table", {"features": POINTS, "output_dir": "/tmp/elsewhere"})
    result = call(runtime, "export_table",
                  {"features": POINTS, "output_dir": str(tmp_path), "filename": "ok"})
    assert result["file_name"] == "ok.csv" and (tmp_path / "ok.csv").exists()
