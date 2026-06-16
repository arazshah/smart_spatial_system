"""
Tests for spatial_join plugin.

Run:
    pytest tests/test_spatial_join.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.spatial_join import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _bbox_within,
    _bboxes_intersect,
    _evaluate_predicate,
    _extract_features,
    _geometry_bbox,
    _is_geographic_crs,
    _python_predicate,
    _validate_cardinality,
    _validate_engine,
    _validate_join_type,
    _validate_predicate,
    spatial_join_features,
)


SOURCE_POINT_INSIDE_A = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [5.0, 5.0]},
    "properties": {"id": "s1", "name": "source_inside_a"},
}

SOURCE_POINT_INSIDE_B = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [25.0, 25.0]},
    "properties": {"id": "s2", "name": "source_inside_b"},
}

SOURCE_POINT_OUTSIDE = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [100.0, 100.0]},
    "properties": {"id": "s3", "name": "source_outside"},
}

SOURCE_POLYGON_BIG = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [0.0, 0.0],
            [30.0, 0.0],
            [30.0, 30.0],
            [0.0, 30.0],
            [0.0, 0.0],
        ]],
    },
    "properties": {"id": "src_poly", "name": "big_polygon"},
}

TARGET_ZONE_A = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [0.0, 0.0],
            [10.0, 0.0],
            [10.0, 10.0],
            [0.0, 10.0],
            [0.0, 0.0],
        ]],
    },
    "properties": {"zone_id": "a", "zone_name": "Zone A"},
}

TARGET_ZONE_B = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [20.0, 20.0],
            [30.0, 20.0],
            [30.0, 30.0],
            [20.0, 30.0],
            [20.0, 20.0],
        ]],
    },
    "properties": {"zone_id": "b", "zone_name": "Zone B"},
}

TARGET_ZONE_OVERLAP = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [4.0, 4.0],
            [8.0, 4.0],
            [8.0, 8.0],
            [4.0, 8.0],
            [4.0, 4.0],
        ]],
    },
    "properties": {"zone_id": "overlap", "zone_name": "Overlap Zone"},
}

NULL_GEOMETRY_FEATURE = {
    "type": "Feature",
    "geometry": None,
    "properties": {"id": "null"},
}


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "spatial_join"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Spatial Join"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("python") == "python"
    assert _validate_engine("shapely") == "shapely"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_predicate() -> None:
    assert _validate_predicate("intersects") == "intersects"
    assert _validate_predicate("within") == "within"
    assert _validate_predicate("contains") == "contains"

    with pytest.raises(ValueError):
        _validate_predicate("touches")


def test_validate_join_type() -> None:
    assert _validate_join_type("inner") == "inner"
    assert _validate_join_type("left") == "left"

    with pytest.raises(ValueError):
        _validate_join_type("right")


def test_validate_cardinality() -> None:
    assert _validate_cardinality("first") == "first"
    assert _validate_cardinality("one_to_many") == "one_to_many"

    with pytest.raises(ValueError):
        _validate_cardinality("many_to_one")


def test_geometry_bbox_polygon() -> None:
    bbox = _geometry_bbox(TARGET_ZONE_A["geometry"])
    assert bbox == [0.0, 0.0, 10.0, 10.0]


def test_bboxes_intersect_and_within() -> None:
    assert _bboxes_intersect([0, 0, 10, 10], [5, 5, 15, 15]) is True
    assert _bboxes_intersect([0, 0, 10, 10], [20, 20, 30, 30]) is False

    assert _bbox_within([1, 1, 2, 2], [0, 0, 10, 10]) is True
    assert _bbox_within([1, 1, 20, 2], [0, 0, 10, 10]) is False


def test_python_predicate_intersects() -> None:
    assert _python_predicate(
        SOURCE_POINT_INSIDE_A["geometry"],
        TARGET_ZONE_A["geometry"],
        "intersects",
    ) is True

    assert _python_predicate(
        SOURCE_POINT_OUTSIDE["geometry"],
        TARGET_ZONE_A["geometry"],
        "intersects",
    ) is False


def test_python_predicate_within() -> None:
    assert _python_predicate(
        SOURCE_POINT_INSIDE_A["geometry"],
        TARGET_ZONE_A["geometry"],
        "within",
    ) is True

    assert _python_predicate(
        SOURCE_POLYGON_BIG["geometry"],
        TARGET_ZONE_A["geometry"],
        "within",
    ) is False


def test_python_predicate_contains() -> None:
    assert _python_predicate(
        SOURCE_POLYGON_BIG["geometry"],
        TARGET_ZONE_A["geometry"],
        "contains",
    ) is True

    assert _python_predicate(
        TARGET_ZONE_A["geometry"],
        SOURCE_POLYGON_BIG["geometry"],
        "contains",
    ) is False


def test_evaluate_predicate_python() -> None:
    matched, engine_used = _evaluate_predicate(
        SOURCE_POINT_INSIDE_A["geometry"],
        TARGET_ZONE_A["geometry"],
        predicate="within",
        engine="python",
    )

    assert matched is True
    assert engine_used == "python"


def test_extract_features_from_list() -> None:
    features, info = _extract_features([SOURCE_POINT_INSIDE_A], label="source")

    assert len(features) == 1
    assert info["source_input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [SOURCE_POINT_INSIDE_A],
    }

    features, info = _extract_features(collection, label="source")

    assert len(features) == 1
    assert info["source_input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(
        features=[SOURCE_POINT_INSIDE_A],
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector, label="source")

    assert len(features) == 1
    assert info["source_input_type"] == "VectorOut"
    assert info["source_input_metadata"]["source"] == "test"


def test_is_geographic_crs() -> None:
    assert _is_geographic_crs("EPSG:4326") is True
    assert _is_geographic_crs("CRS:84") is True
    assert _is_geographic_crs("EPSG:3857") is False


def test_spatial_join_inner_first_python_success() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A, SOURCE_POINT_OUTSIDE],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        join_type="inner",
        cardinality="first",
        engine="python",
    )

    assert len(result.features) == 1

    props = result.features[0]["properties"]

    assert props["id"] == "s1"
    assert props["_source_index"] == 0
    assert props["_target_index"] == 0
    assert props["_join_status"] == "matched"
    assert props["_join_engine"] == "python"
    assert props["_join_predicate"] == "within"
    assert props["_join_type"] == "inner"
    assert props["_joined_count"] == 1
    assert props["_joined_target_properties"]["zone_id"] == "a"

    md = result.metadata
    assert md["source"] == "spatial_join"
    assert md["operation"] == "spatial_join"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["predicate"] == "within"
    assert md["join_type"] == "inner"
    assert md["cardinality"] == "first"
    assert md["source_feature_count"] == 2
    assert md["target_feature_count"] == 1
    assert md["pair_count"] == 2
    assert md["matched_pair_count"] == 1
    assert md["matched_source_count"] == 1
    assert md["unmatched_source_count"] == 1
    assert md["output_feature_count"] == 1


def test_spatial_join_left_keeps_unmatched() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A, SOURCE_POINT_OUTSIDE],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        join_type="left",
        cardinality="first",
        engine="python",
    )

    assert len(result.features) == 2

    matched = result.features[0]["properties"]
    unmatched = result.features[1]["properties"]

    assert matched["_join_status"] == "matched"
    assert unmatched["_join_status"] == "unmatched"
    assert unmatched["_target_index"] is None
    assert unmatched["_joined_count"] == 0


def test_spatial_join_one_to_many() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A, TARGET_ZONE_OVERLAP],
        predicate="within",
        join_type="inner",
        cardinality="one_to_many",
        engine="python",
    )

    assert len(result.features) == 2

    props1 = result.features[0]["properties"]
    props2 = result.features[1]["properties"]

    assert props1["_target_index"] == 0
    assert props2["_target_index"] == 1
    assert props1["_joined_count"] == 2
    assert props2["_joined_count"] == 2
    assert props1["_joined_target_properties"]["zone_id"] == "a"
    assert props2["_joined_target_properties"]["zone_id"] == "overlap"


def test_spatial_join_first_returns_only_first_match() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A, TARGET_ZONE_OVERLAP],
        predicate="within",
        join_type="inner",
        cardinality="first",
        engine="python",
    )

    assert len(result.features) == 1
    props = result.features[0]["properties"]
    assert props["_target_index"] == 0
    assert props["_joined_count"] == 2


def test_spatial_join_contains_polygon() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POLYGON_BIG],
        target_features=[TARGET_ZONE_A, TARGET_ZONE_B],
        predicate="contains",
        join_type="inner",
        cardinality="one_to_many",
        engine="python",
    )

    assert len(result.features) == 2
    assert result.metadata["matched_pair_count"] == 2


def test_spatial_join_flatten_target_properties() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        join_type="inner",
        cardinality="first",
        engine="python",
        flatten_target_properties=True,
        target_property_prefix="zone_",
    )

    props = result.features[0]["properties"]

    assert props["zone_zone_id"] == "a"
    assert props["zone_zone_name"] == "Zone A"


def test_spatial_join_without_nested_target_properties() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        join_type="inner",
        cardinality="first",
        engine="python",
        include_target_properties=False,
        flatten_target_properties=True,
        target_property_prefix="t_",
    )

    props = result.features[0]["properties"]

    assert "_joined_target_properties" not in props
    assert props["t_zone_id"] == "a"


def test_spatial_join_null_geometry_left() -> None:
    result = spatial_join_features(
        source_features=[NULL_GEOMETRY_FEATURE],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        join_type="left",
        engine="python",
    )

    assert len(result.features) == 1
    assert result.features[0]["properties"]["_join_status"] == "unmatched"


def test_spatial_join_rejects_empty_targets() -> None:
    with pytest.raises(ValueError, match="target_features"):
        spatial_join_features(
            source_features=[SOURCE_POINT_INSIDE_A],
            target_features=[],
            predicate="within",
            engine="python",
        )


def test_spatial_join_warns_for_geographic_crs(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spatial_join.yaml").write_text(
        """
default_engine: python
default_predicate: within
default_join_type: inner
default_cardinality: first
preserve_properties: true
include_target_properties: true
flatten_target_properties: false
target_property_prefix: target_
drop_failed: false
source_crs: EPSG:4326
warn_if_geographic_crs: true
fields:
  source_index_field: _source_index
  target_index_field: _target_index
  status_field: _join_status
  engine_field: _join_engine
  predicate_field: _join_predicate
  join_type_field: _join_type
  joined_count_field: _joined_count
  target_properties_field: _joined_target_properties
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A],
    )

    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_spatial_join_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spatial_join.yaml").write_text(
        """
default_engine: python
default_predicate: within
default_join_type: left
default_cardinality: first
preserve_properties: true
include_target_properties: true
flatten_target_properties: true
target_property_prefix: joined_
drop_failed: false
source_crs: EPSG:3857
warn_if_geographic_crs: false
fields:
  source_index_field: src_idx
  target_index_field: tgt_idx
  status_field: join_status
  engine_field: engine_used
  predicate_field: predicate_used
  join_type_field: join_type_used
  joined_count_field: joined_count
  target_properties_field: target_props
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A],
    )

    props = result.features[0]["properties"]

    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["predicate"] == "within"
    assert result.metadata["join_type"] == "left"

    assert props["src_idx"] == 0
    assert props["tgt_idx"] == 0
    assert props["join_status"] == "matched"
    assert props["engine_used"] == "python"
    assert props["predicate_used"] == "within"
    assert props["join_type_used"] == "left"
    assert props["joined_count"] == 1
    assert props["target_props"]["zone_id"] == "a"
    assert props["joined_zone_id"] == "a"


def test_spatial_join_metadata_merge() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        engine="python",
        metadata={"analysis_id": "join-1"},
    )

    assert result.metadata["analysis_id"] == "join-1"


def test_spatial_join_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        spatial_join_features(
            source_features=[SOURCE_POINT_INSIDE_A],
            target_features=[TARGET_ZONE_A],
            predicate="within",
            engine="python",
            metadata="bad",
        )


def test_spatial_join_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        spatial_join_features(
            source_features={"type": "Point", "coordinates": [1, 2]},
            target_features=[TARGET_ZONE_A],
            predicate="within",
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_spatial_join")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_spatial_join"
    assert artifact.payload["source"] == "spatial_join"
    assert artifact.payload["operation"] == "spatial_join"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "spatial_join_features" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "spatial_join_features")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "spatial_join_features"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "source_features" in descriptor.required_inputs
    assert "target_features" in descriptor.required_inputs
    assert "predicate" in descriptor.optional_inputs
    assert "join_type" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "spatial_join"
    assert descriptor.metadata["requires_shapely_for_exact_predicates"] is True


def test_shapely_engine_if_installed() -> None:
    pytest.importorskip("shapely", reason="shapely not installed")

    result = spatial_join_features(
        source_features=[SOURCE_POINT_INSIDE_A],
        target_features=[TARGET_ZONE_A],
        predicate="within",
        join_type="inner",
        cardinality="first",
        engine="shapely",
    )

    assert len(result.features) == 1
    assert result.metadata["engines_used"] == ["shapely"]
    assert result.features[0]["properties"]["_join_engine"] == "shapely"
    assert result.features[0]["properties"]["_joined_target_properties"]["zone_id"] == "a"
