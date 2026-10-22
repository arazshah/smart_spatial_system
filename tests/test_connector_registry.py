"""Connector contract: registry, normalized results, policy, redaction (docs/CONNECTOR_REGISTRY_DESIGN.md)."""

from __future__ import annotations

import json

import pytest

from orchestrator.capability_registry import CapabilityRegistry
from smart_spatial_system.application.connectors import (
    ConnectorError,
    ConnectorRegistry,
)
from smart_spatial_system.application.connectors.registry import ACCESS_CLASSES
from smart_spatial_system.application.connectors.result import redact_ref, vector_summary
from smart_spatial_system.interfaces.mcp.config import ALL_RISKS
from smart_spatial_system.interfaces.mcp.policy import classify

EXPECTED_CONNECTORS = {
    "fetch_postgis_layer", "fetch_postgis_sql_layer", "query_database_postgis",
    "load_local_vector", "load_local_raster", "fetch_wfs_features", "fetch_wms_map",
    "geocode_place", "reverse_geocode_point", "load_geojson_url",
}


@pytest.fixture(scope="module")
def real_registry():
    return CapabilityRegistry.from_plugin_modules(tolerant=True)


@pytest.fixture(scope="module")
def connectors(real_registry):
    return ConnectorRegistry.from_capability_registry(real_registry)


# ------------------------------------------------- contract over every real connector
def test_all_source_capabilities_declare_a_connector_block(connectors):
    assert {s.capability for s in connectors.list()} == EXPECTED_CONNECTORS


def test_access_vocabulary_matches_the_mcp_policy():
    assert ACCESS_CLASSES == ALL_RISKS


def test_declared_access_is_never_weaker_than_the_policy_heuristics(connectors):
    """A connector must not claim less access than its parameter names imply."""
    for spec in connectors.list():
        implied = classify(spec.capability, spec.params)
        assert implied <= spec.access, (spec.capability, implied - spec.access)


def test_contract_fields(connectors):
    for spec in connectors.list():
        assert spec.credentials in {"none", "profile"}
        assert spec.kind in {"vector", "raster", "table"}
        assert spec.limit_param is None or spec.limit_param in spec.params
        description = spec.describe()
        assert description["capability"] == spec.capability
        assert not {"dsn", "password", "host", "port", "user"} & set(description["optional_inputs"])
        json.dumps(description)  # JSON-safe


def test_grouping_by_source_type(connectors):
    assert {s.capability for s in connectors.by_source_type("postgis")} == {
        "fetch_postgis_layer", "fetch_postgis_sql_layer", "query_database_postgis"}
    assert connectors.by_source_type("nope") == []
    with pytest.raises(ConnectorError) as err:
        connectors.get("buffer_vector_features")
    assert err.value.code == "unknown_connector"


# --------------------------------------------------------------- real local loader
def test_local_vector_fetch_and_preview(connectors, tmp_path):
    points = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": f"p{i}", "n": i},
         "geometry": {"type": "Point", "coordinates": [51 + i, 35 + i]}} for i in range(30)]}
    path = tmp_path / "pts.geojson"
    path.write_text(json.dumps(points))

    result = connectors.fetch("load_local_vector", {"path": str(path)})
    assert result.kind == "vector" and result.source_type == "local_vector"
    meta = result.metadata
    assert meta["feature_count"] == 30 and meta["geometry_types"] == {"Point": 30}
    assert meta["bbox"] == [51.0, 35.0, 80.0, 64.0]
    assert {f["name"]: f["type"] for f in meta["fields"]} == {"name": "string", "n": "number"}
    assert meta["truncated"] is False and meta["source_ref"]["path"] == str(path)

    preview = connectors.preview("load_local_vector", {"path": str(path)}, limit=5)
    assert len(preview["data"]["features"]) == 5 and preview["data"]["type"] == "FeatureCollection"
    assert preview["metadata"]["truncated"] is True  # hit the row limit

    with pytest.raises(ConnectorError) as err:
        connectors.preview("load_local_raster", {"path": str(path)})
    assert err.value.code == "invalid_params"


# ------------------------------------------------------------------- fake registry
class _Descriptor:
    def __init__(self, block, required=("x",), optional=("limit", "profile", "dsn", "base_url")):
        self.metadata = {"connector": block} if block is not None else {}
        self.required_inputs = list(required)
        self.optional_inputs = list(optional)
        self.description = "fake"


class _Binding:
    def __init__(self, fn):
        self.callable = fn


class _FakeRegistry:
    def __init__(self, entries):
        self._entries = entries  # name -> (descriptor, fn)

    def registered_capability_names(self):
        return list(self._entries)

    def descriptor_for(self, name):
        return self._entries[name][0]

    def resolve(self, name):
        return _Binding(self._entries[name][1])


class _Out:
    def __init__(self, features, metadata=None):
        self.features = features
        self.metadata = metadata or {}


def feature(i):
    return {"type": "Feature", "properties": {"i": i},
            "geometry": {"type": "Point", "coordinates": [i, i]}}


BLOCK = {"source_type": "fake_src", "kind": "vector", "access": ["network"],
         "credentials": "profile", "limit_param": "limit"}


def fake(fn, block=BLOCK, **kwargs):
    entries = {"fake_cap": (_Descriptor(block, **kwargs), fn)}
    return ConnectorRegistry.from_capability_registry(_FakeRegistry(entries))


def test_row_limit_is_injected_and_never_raised():
    seen = {}

    def fn(x, limit=None, profile=None, dsn=None, base_url=None):
        seen["limit"] = limit
        return _Out([feature(i) for i in range(limit or 100)])

    registry = fake(fn)
    registry.fetch("fake_cap", {"x": 1}, row_limit=7)
    assert seen["limit"] == 7
    registry.fetch("fake_cap", {"x": 1, "limit": 3}, row_limit=7)
    assert seen["limit"] == 3  # the caller's smaller limit wins


def test_validation_and_client_connection_policy():
    def fn(x, limit=None, profile=None, dsn=None, base_url=None):
        return _Out([])

    registry = fake(fn)
    with pytest.raises(ConnectorError, match="Unknown"):
        registry.fetch("fake_cap", {"x": 1, "evil": 2})
    with pytest.raises(ConnectorError, match="Missing"):
        registry.fetch("fake_cap", {})
    with pytest.raises(ConnectorError) as err:
        registry.fetch("fake_cap", {"x": 1, "dsn": "postgresql://u:p@internal:5432/db"})
    assert err.value.code == "policy" and "internal" not in str(err.value)
    registry.fetch("fake_cap", {"x": 1, "profile": "main"})  # a named profile is fine
    permissive = ConnectorRegistry.from_capability_registry(
        _FakeRegistry({"fake_cap": (_Descriptor(BLOCK), fn)}), allow_client_connection=True)
    permissive.fetch("fake_cap", {"x": 1, "dsn": "postgresql://u:p@h/db"})


def test_errors_are_redacted_and_chained_text_is_not_leaked():
    def boom(x, limit=None, profile=None, dsn=None, base_url=None):
        raise RuntimeError("could not connect to postgresql://admin:s3cret@10.0.0.5/db password=hunter2")

    with pytest.raises(ConnectorError) as err:
        fake(boom).fetch("fake_cap", {"x": 1})
    text = str(err.value)
    assert "s3cret" not in text and "hunter2" not in text and "<redacted>" in text
    assert err.value.__cause__ is None  # the raw exception is not attached


def test_provenance_redaction():
    ref = redact_ref({
        "dsn": "postgresql://u:p@h/db", "password": "x", "table": "roads",
        "base_url": "https://user:pw@maps.example/wfs?key=ABC&service=WFS&token=T",
        "bbox": [1, 2, 3, 4], "limit": 5,
    })
    assert ref["dsn"] == ref["password"] == "<redacted>"
    assert "pw" not in ref["base_url"] and "ABC" not in ref["base_url"] and "T" != ref["base_url"]
    assert "service=WFS" in ref["base_url"] and ref["table"] == "roads" and ref["bbox"] == "<list>"


def test_raster_and_table_normalization():
    class _Raster:
        data = [[1, 2, 3], [4, 5, 6]]
        path = "/srv/x.tif"
        metadata = {"crs": "EPSG:3857", "transform": [1, 0, 0, 0, -1, 2], "nodata": None}

    raster = fake(lambda x: _Raster(), {**BLOCK, "kind": "raster", "limit_param": None})
    result = raster.fetch("fake_cap", {"x": 1})
    assert result.metadata["shape"] == [2, 3] and result.metadata["crs"] == "EPSG:3857"
    assert result.metadata["has_file"] is True
    assert "/srv" not in json.dumps(result.to_dict(include_data=False))

    table = fake(lambda x: [{"a": 1}, {"a": 2}], {**BLOCK, "kind": "table", "limit_param": None})
    assert table.fetch("fake_cap", {"x": 1}).metadata["row_count"] == 2

    bad = fake(lambda x, limit=None, profile=None, dsn=None, base_url=None: object(), BLOCK)
    with pytest.raises(ConnectorError) as err:
        bad.fetch("fake_cap", {"x": 1})
    assert err.value.code == "invalid_result"


@pytest.mark.parametrize("block, reason", [
    ({**BLOCK, "kind": "pointcloud"}, "kind"),
    ({**BLOCK, "access": ["root"]}, "access"),
    ({**BLOCK, "credentials": "client"}, "credentials"),
    ({**BLOCK, "source_type": "Bad Name"}, "source_type"),
    ({**BLOCK, "limit_param": "nonexistent"}, "limit_param"),
])
def test_invalid_blocks_are_rejected_at_build_time(block, reason):
    with pytest.raises(ConnectorError, match=reason):
        fake(lambda x, limit=None, profile=None, dsn=None, base_url=None: _Out([]), block)


def test_async_connectors_are_rejected():
    async def fn(x, limit=None, profile=None, dsn=None, base_url=None):
        return _Out([])

    with pytest.raises(ConnectorError, match="synchronous"):
        fake(fn)


def test_vector_summary_handles_nulls_and_nested_coordinates():
    summary = vector_summary([
        {"type": "Feature", "properties": {"a": None, "b": True}, "geometry": None},
        {"type": "Feature", "properties": {"a": 1.5},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 3], [0, 0]]]}},
    ])
    assert summary["bbox"] == [0.0, 0.0, 4.0, 3.0]
    assert summary["geometry_types"] == {"Null": 1, "Polygon": 1}
    assert {f["name"]: f["type"] for f in summary["fields"]} == {"a": "number", "b": "boolean"}
    assert vector_summary([])["bbox"] is None
