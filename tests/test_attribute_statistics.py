"""
Tests for attribute_statistics plugin.

Run:
    pytest tests/test_attribute_statistics.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.attribute_statistics import (  # noqa: E402
    MISSING,
    PLUGIN,
    PLUGIN_ID,
    _calculate_field_stats,
    _calculate_numeric_stats,
    _extract_features,
    _get_path,
    _infer_fields,
    _is_number,
    _normalize_fields,
    _normalize_group_by,
    _validate_max_top_values,
    _validate_precision,
    calculate_attribute_statistics,
)


FEATURES = [
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [51.0, 35.0]},
        "properties": {
            "id": 1,
            "name": "Tehran",
            "type": "city",
            "population": 9000000,
            "risk": "high",
            "active": True,
            "address": {"country": "Iran", "province": "Tehran"},
        },
    },
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [51.67, 32.65]},
        "properties": {
            "id": 2,
            "name": "Isfahan",
            "type": "city",
            "population": 2000000,
            "risk": "medium",
            "active": True,
            "address": {"country": "Iran", "province": "Isfahan"},
        },
    },
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [59.6, 36.3]},
        "properties": {
            "id": 3,
            "name": "Mashhad",
            "type": "city",
            "population": 3000000,
            "risk": "medium",
            "active": False,
            "address": {"country": "Iran", "province": "Khorasan"},
        },
    },
    {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "id": 4,
            "name": None,
            "type": "unknown",
            "population": None,
            "risk": "unknown",
            "active": False,
        },
    },
]


def _find_stat(result, field: str, group_value=None):
    for feature in result.features:
        props = feature["properties"]
        if props["_stat_field"] != field:
            continue

        if group_value is None:
            return props

        if props["_group_value"] == group_value:
            return props

    raise AssertionError(f"stat not found: {field=} {group_value=}")


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "attribute_statistics"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Attribute Statistics"


def test_is_number() -> None:
    assert _is_number(1) is True
    assert _is_number(1.5) is True
    assert _is_number(True) is False
    assert _is_number("1") is False


def test_get_path() -> None:
    props = FEATURES[0]["properties"]

    assert _get_path(props, "name") == "Tehran"
    assert _get_path(props, "address.country") == "Iran"
    assert _get_path(props, "missing") is MISSING


def test_normalize_fields() -> None:
    assert _normalize_fields("population") == ["population"]
    assert _normalize_fields(["population", "name", "population"]) == ["population", "name"]
    assert _normalize_fields(None) is None

    with pytest.raises(ValueError):
        _normalize_fields(123)


def test_normalize_group_by() -> None:
    assert _normalize_group_by("type") == ["type"]
    assert _normalize_group_by(["type", "risk"]) == ["type", "risk"]
    assert _normalize_group_by(None) == []

    with pytest.raises(ValueError):
        _normalize_group_by(123)


def test_validate_precision() -> None:
    assert _validate_precision(None) is None
    assert _validate_precision(3) == 3

    with pytest.raises(ValueError):
        _validate_precision(-1)


def test_validate_max_top_values() -> None:
    assert _validate_max_top_values(10) == 10
    assert _validate_max_top_values("5") == 5

    with pytest.raises(ValueError):
        _validate_max_top_values(-1)


def test_calculate_numeric_stats() -> None:
    stats = _calculate_numeric_stats([1.0, 2.0, 3.0])

    assert stats["min"] == 1.0
    assert stats["max"] == 3.0
    assert stats["sum"] == 6.0
    assert stats["mean"] == 2.0
    assert stats["median"] == 2.0
    assert stats["population_stdev"] == pytest.approx(0.8164965809)


def test_calculate_field_stats_with_nulls() -> None:
    stats = _calculate_field_stats(
        values=[1, 2, None, 2],
        include_nulls=True,
        max_top_values=10,
    )

    assert stats["count"] == 4
    assert stats["non_null_count"] == 3
    assert stats["null_count"] == 1
    assert stats["unique_count"] == 3
    assert stats["numeric_count"] == 3
    assert stats["mean"] == pytest.approx(1.6666666667)


def test_infer_fields() -> None:
    fields = _infer_fields(FEATURES, preserve_field_order=False)

    assert "population" in fields
    assert "name" in fields
    assert "address.country" in fields


def test_extract_features_from_list() -> None:
    features, info = _extract_features(FEATURES)

    assert len(features) == 4
    assert info["input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": FEATURES,
    }

    features, info = _extract_features(collection)

    assert len(features) == 4
    assert info["input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(
        features=FEATURES,
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector)

    assert len(features) == 4
    assert info["input_type"] == "VectorOut"
    assert info["input_metadata"]["source"] == "test"


def test_calculate_attribute_statistics_numeric_field() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["population"],
        precision=2,
    )

    assert len(result.features) == 1

    props = result.features[0]["properties"]

    assert props["_stat_field"] == "population"
    assert props["_group_value"] == "__all__"
    assert props["_count"] == 4
    assert props["_non_null_count"] == 3
    assert props["_null_count"] == 1
    assert props["_numeric_count"] == 3
    assert props["_min"] == 2000000.0
    assert props["_max"] == 9000000.0
    assert props["_sum"] == 14000000.0
    assert props["_mean"] == pytest.approx(4666666.67)
    assert props["_median"] == 3000000.0
    assert props["_statistics_status"] == "success"

    md = result.metadata
    assert md["source"] == "attribute_statistics"
    assert md["operation"] == "attribute_statistics"
    assert md["input_feature_count"] == 4
    assert md["output_feature_count"] == 1
    assert md["fields"] == ["population"]
    assert md["group_count"] == 1


def test_calculate_attribute_statistics_categorical_field() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["type"],
        max_top_values=5,
    )

    props = result.features[0]["properties"]

    assert props["_stat_field"] == "type"
    assert props["_count"] == 4
    assert props["_unique_count"] == 2
    assert props["_numeric_count"] == 0
    assert props["_mean"] is None
    assert props["_top_values"][0]["value"] == "city"
    assert props["_top_values"][0]["count"] == 3


def test_calculate_attribute_statistics_group_by() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["population"],
        group_by="type",
        precision=2,
    )

    assert len(result.features) == 2

    city = _find_stat(result, "population", "city")
    unknown = _find_stat(result, "population", "unknown")

    assert city["_count"] == 3
    assert city["_numeric_count"] == 3
    assert city["_mean"] == pytest.approx(4666666.67)

    assert unknown["_count"] == 1
    assert unknown["_numeric_count"] == 0
    assert unknown["_mean"] is None

    assert result.metadata["group_by"] == ["type"]
    assert result.metadata["group_count"] == 2


def test_calculate_attribute_statistics_group_by_multiple_fields() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["id"],
        group_by=["type", "risk"],
    )

    assert result.metadata["group_by"] == ["type", "risk"]
    assert result.metadata["group_count"] == 3

    group_values = [feature["properties"]["_group_value"] for feature in result.features]
    assert ["city", "high"] in group_values
    assert ["city", "medium"] in group_values
    assert ["unknown", "unknown"] in group_values


def test_calculate_attribute_statistics_numeric_only() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["population", "name"],
        numeric_only=True,
    )

    assert len(result.features) == 1
    assert result.features[0]["properties"]["_stat_field"] == "population"


def test_calculate_attribute_statistics_infers_fields() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=None,
        numeric_only=True,
    )

    fields = [feature["properties"]["_stat_field"] for feature in result.features]

    assert "id" in fields
    assert "population" in fields
    assert "name" not in fields


def test_calculate_attribute_statistics_nested_field() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["address.country"],
    )

    props = result.features[0]["properties"]

    assert props["_stat_field"] == "address.country"
    assert props["_non_null_count"] == 3
    assert props["_null_count"] == 1
    assert props["_unique_count"] == 2


def test_calculate_attribute_statistics_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "attribute_statistics.yaml").write_text(
        """
default_fields:
  - population
default_group_by: type
include_nulls: true
numeric_only: false
max_top_values: 3
preserve_field_order: true
fields:
  stat_field_field: stat_field
  group_field_field: group_field
  group_value_field: group_value
  count_field: count
  non_null_count_field: non_null_count
  null_count_field: null_count
  unique_count_field: unique_count
  numeric_count_field: numeric_count
  min_field: min_value
  max_field: max_value
  sum_field: sum_value
  mean_field: mean_value
  median_field: median_value
  sample_stdev_field: sample_stdev
  population_stdev_field: population_stdev
  top_values_field: top_values
  status_field: status
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = calculate_attribute_statistics(
        features=FEATURES,
    )

    assert result.metadata["fields"] == ["population"]
    assert result.metadata["group_by"] == ["type"]

    props = result.features[0]["properties"]

    assert "stat_field" in props
    assert "group_value" in props
    assert "mean_value" in props
    assert props["status"] == "success"


def test_calculate_attribute_statistics_metadata_merge() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["population"],
        metadata={"analysis_id": "stats-1"},
    )

    assert result.metadata["analysis_id"] == "stats-1"


def test_calculate_attribute_statistics_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_attribute_statistics(
            features=FEATURES,
            fields=["population"],
            metadata="bad",
        )


def test_calculate_attribute_statistics_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        calculate_attribute_statistics(
            features={"type": "Point", "coordinates": [1, 2]},
        )


def test_vectorout_to_artifact() -> None:
    result = calculate_attribute_statistics(
        features=FEATURES,
        fields=["population"],
    )

    artifact = result.to_artifact(produced_by="test_attribute_statistics")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_attribute_statistics"
    assert artifact.payload["source"] == "attribute_statistics"
    assert artifact.payload["operation"] == "attribute_statistics"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_attribute_statistics" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_attribute_statistics")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_attribute_statistics"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "fields" in descriptor.optional_inputs
    assert "group_by" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "attribute_statistics"
    assert descriptor.metadata["group_by_supported"] is True
