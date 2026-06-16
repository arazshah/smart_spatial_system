"""
Tests for nearest_neighbor plugin.

Run:
    pytest tests/test_nearest_neighbor.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.nearest_neighbor import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _configured_precision,
    _extract_features,
    _validate_k,
    _validate_max_distance,
    find_nearest_neighbors,
)


SOURCE_POINT = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
    "properties": {"id": "s1", "name": "source_1"},
}

SOURCE_POINT_2 = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [10.0, 0.0]},
    "properties": {"id": "s2", "name": "source_2"},
}

TARGET_POINT_A = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [3.0, 4.0]},
    "properties": {"id": "t1", "name": "target_a"},
}

TARGET_POINT_B = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [1.0, 0.0]},
    "properties": {"id": "t2", "name": "target_b"},
}

TARGET_POINT_C = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [10.0, 1.0]},
    "properties": {"id": "t3", "name": "target_c"},
}

NULL_GEOMETRY_FEATURE = {
    "type": "Feature",
    "geometry": None,
    "properties": {"id": "null"},
}


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "nearest_neighbor"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Nearest Neighbor"


def test_configured_precision_defaults() -> None:
    assert _configured_precision({}) == 6


def test_configured_precision_null() -> None:
    assert _configured_precision({"coordinate_precision": None}) is None


def test_validate_k() -> None:
    assert _validate_k(1, max_k=10) == 1
    assert _validate_k("3", max_k=10) == 3

    with pytest.raises(ValueError):
        _validate_k(0, max_k=10)

    with pytest.raises(ValueError):
        _validate_k(11, max_k=10)


def test_validate_max_distance() -> None:
    assert _validate_max_distance(None) is None
    assert _validate_max_distance(10) == 10.0
    assert _validate_max_distance("2.5") == 2.5

    with pytest.raises(ValueError):
        _validate_max_distance(-1)


def test_extract_features_from_list() -> None:
    features, info = _extract_features([SOURCE_POINT, TARGET_POINT_A], label="source")

    assert len(features) == 2
    assert info["source_input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [SOURCE_POINT, TARGET_POINT_A],
    }

    features, info = _extract_features(collection, label="source")

    assert len(features) == 2
    assert info["source_input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(
        features=[SOURCE_POINT],
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector, label="source")

    assert len(features) == 1
    assert info["source_input_type"] == "VectorOut"
    assert info["source_input_metadata"]["source"] == "test"


def test_find_nearest_neighbors_k1_python_success() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A, TARGET_POINT_B],
        k=1,
        engine="python",
        precision=4,
    )

    assert len(result.features) == 1

    props = result.features[0]["properties"]

    assert props["id"] == "s1"
    assert props["_nearest_distance"] == 1.0
    assert props["_neighbor_rank"] == 1
    assert props["_source_index"] == 0
    assert props["_target_index"] == 1
    assert props["_nearest_status"] == "matched"
    assert props["_nearest_engine"] == "python"
    assert props["_target_properties"]["id"] == "t2"

    md = result.metadata
    assert md["source"] == "nearest_neighbor"
    assert md["operation"] == "nearest_neighbor"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["k"] == 1
    assert md["source_feature_count"] == 1
    assert md["target_feature_count"] == 2
    assert md["pair_count"] == 2
    assert md["match_count"] == 1
    assert md["matched_source_count"] == 1
    assert md["unmatched_source_count"] == 0


def test_find_nearest_neighbors_k2_python_success() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A, TARGET_POINT_B, TARGET_POINT_C],
        k=2,
        engine="python",
        precision=4,
    )

    assert len(result.features) == 2

    props1 = result.features[0]["properties"]
    props2 = result.features[1]["properties"]

    assert props1["_target_index"] == 1
    assert props1["_nearest_distance"] == 1.0
    assert props1["_neighbor_rank"] == 1

    assert props2["_target_index"] == 0
    assert props2["_nearest_distance"] == 5.0
    assert props2["_neighbor_rank"] == 2

    assert result.metadata["match_count"] == 2


def test_find_nearest_neighbors_multiple_sources() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT, SOURCE_POINT_2],
        target_features=[TARGET_POINT_A, TARGET_POINT_B, TARGET_POINT_C],
        k=1,
        engine="python",
        precision=4,
    )

    assert len(result.features) == 2

    first = result.features[0]["properties"]
    second = result.features[1]["properties"]

    assert first["_source_index"] == 0
    assert first["_target_index"] == 1
    assert first["_nearest_distance"] == 1.0

    assert second["_source_index"] == 1
    assert second["_target_index"] == 2
    assert second["_nearest_distance"] == 1.0

    assert result.metadata["matched_source_count"] == 2


def test_find_nearest_neighbors_max_distance_unmatched_by_default() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A],
        k=1,
        max_distance=2.0,
        engine="python",
    )

    assert len(result.features) == 1

    props = result.features[0]["properties"]
    assert props["_nearest_distance"] is None
    assert props["_neighbor_rank"] is None
    assert props["_target_index"] is None
    assert props["_nearest_status"] == "unmatched"

    assert result.metadata["match_count"] == 0
    assert result.metadata["unmatched_source_count"] == 1


def test_find_nearest_neighbors_drop_unmatched() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A],
        k=1,
        max_distance=2.0,
        engine="python",
        drop_unmatched=True,
    )

    assert len(result.features) == 0
    assert result.metadata["unmatched_source_count"] == 1
    assert result.metadata["dropped_unmatched_count"] == 1


def test_find_nearest_neighbors_handles_null_geometry_by_default() -> None:
    result = find_nearest_neighbors(
        source_features=[NULL_GEOMETRY_FEATURE],
        target_features=[TARGET_POINT_A],
        k=1,
        engine="python",
    )

    assert len(result.features) == 1

    props = result.features[0]["properties"]
    assert props["_nearest_status"] == "unmatched"
    assert props["_nearest_distance"] is None

    assert result.metadata["unmatched_source_count"] == 1
    assert result.metadata["failed_pair_count"] == 1


def test_find_nearest_neighbors_include_target_geometry() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
        k=1,
        engine="python",
        include_target_geometry=True,
    )

    props = result.features[0]["properties"]

    assert "_target_geometry" in props
    assert props["_target_geometry"]["type"] == "Point"
    assert props["_target_geometry"]["coordinates"] == [1.0, 0.0]


def test_find_nearest_neighbors_warns_for_geographic_crs() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
        k=1,
        engine="python",
        source_crs="EPSG:4326",
    )

    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_find_nearest_neighbors_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "nearest_neighbor.yaml").write_text(
        """
default_engine: python
default_k: 2
max_k: 10
coordinate_precision: 3
preserve_properties: true
drop_unmatched: false
include_target_geometry: true
source_crs: EPSG:3857
warn_if_geographic_crs: true
fields:
  distance_field: dist
  rank_field: rank
  source_index_field: src_idx
  target_index_field: tgt_idx
  status_field: status
  engine_field: engine_used
  target_properties_field: tgt_props
  target_geometry_field: tgt_geom
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A, TARGET_POINT_B, TARGET_POINT_C],
    )

    assert len(result.features) == 2

    props = result.features[0]["properties"]

    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["k"] == 2
    assert result.metadata["coordinate_precision"] == 3

    assert props["dist"] == 1.0
    assert props["rank"] == 1
    assert props["src_idx"] == 0
    assert props["tgt_idx"] == 1
    assert props["status"] == "matched"
    assert props["engine_used"] == "python"
    assert props["tgt_props"]["id"] == "t2"
    assert props["tgt_geom"]["type"] == "Point"


def test_find_nearest_neighbors_metadata_merge() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
        k=1,
        engine="python",
        metadata={"analysis_id": "nearest-1"},
    )

    assert result.metadata["analysis_id"] == "nearest-1"


def test_find_nearest_neighbors_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        find_nearest_neighbors(
            source_features=[SOURCE_POINT],
            target_features=[TARGET_POINT_B],
            k=1,
            engine="python",
            metadata="bad",
        )


def test_find_nearest_neighbors_rejects_empty_targets() -> None:
    with pytest.raises(ValueError, match="target_features"):
        find_nearest_neighbors(
            source_features=[SOURCE_POINT],
            target_features=[],
            k=1,
            engine="python",
        )


def test_find_nearest_neighbors_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        find_nearest_neighbors(
            source_features={"type": "Point", "coordinates": [1, 2]},
            target_features=[TARGET_POINT_B],
            k=1,
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
        k=1,
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_nearest_neighbor")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_nearest_neighbor"
    assert artifact.payload["source"] == "nearest_neighbor"
    assert artifact.payload["operation"] == "nearest_neighbor"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "find_nearest_neighbors" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "find_nearest_neighbors")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "find_nearest_neighbors"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "source_features" in descriptor.required_inputs
    assert "target_features" in descriptor.required_inputs
    assert "k" in descriptor.optional_inputs
    assert "max_distance" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "nearest_neighbor"
    assert descriptor.metadata["planar_only"] is True


def test_shapely_engine_if_installed() -> None:
    pytest.importorskip("shapely", reason="shapely not installed")

    result = find_nearest_neighbors(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A],
        k=1,
        engine="shapely",
        precision=4,
    )

    props = result.features[0]["properties"]

    assert result.metadata["engines_used"] == ["shapely"]
    assert props["_nearest_distance"] == 5.0
    assert props["_nearest_engine"] == "shapely"
