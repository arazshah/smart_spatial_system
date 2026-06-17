"""
Tests for raster_reclassify plugin.

Run:
    pytest tests/test_raster_reclassify.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.raster_reclassify import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _band_value,
    _find_matching_rule,
    _is_nodata,
    _is_number,
    _matches_rule,
    _normalize_rule,
    _normalize_rules,
    _reclassify_value,
    _validate_band_index,
    _validate_engine,
    _validate_precision,
    reclassify_raster,
)


RASTER_NDVI = {
    "data": [
        [0.10, 0.25, 0.60],
        [0.00, 0.40, None],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_CATEGORICAL = {
    "data": [
        [1, 2, 3],
        [4, 5, 6],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_2BAND = {
    "data": [
        [
            [1, 2],
            [3, 4],
        ],
        [
            [10, 20],
            [30, 40],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


NDVI_RULES = [
    {
        "min": -1.0,
        "max": 0.2,
        "value": 1,
        "label": "low",
        "inclusive_min": True,
        "inclusive_max": False,
    },
    {
        "min": 0.2,
        "max": 0.5,
        "value": 2,
        "label": "medium",
        "inclusive_min": True,
        "inclusive_max": False,
    },
    {
        "min": 0.5,
        "max": 1.0,
        "value": 3,
        "label": "high",
        "inclusive_min": True,
        "inclusive_max": True,
    },
]


def _get_data(result):
    if hasattr(result, "data"):
        return result.data
    if hasattr(result, "array"):
        return result.array
    if hasattr(result, "payload"):
        return result.payload
    raise AssertionError("RasterOut has no data/array/payload attribute")


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "raster_reclassify"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Raster Reclassify"


def test_validate_engine() -> None:
    assert _validate_engine("python") == "python"
    assert _validate_engine("auto") == "auto"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_precision() -> None:
    assert _validate_precision(None) is None
    assert _validate_precision(3) == 3

    with pytest.raises(ValueError):
        _validate_precision(-1)


def test_is_number() -> None:
    assert _is_number(1) is True
    assert _is_number(1.5) is True
    assert _is_number(True) is False
    assert _is_number("1") is False


def test_is_nodata() -> None:
    assert _is_nodata(None, -9999) is True
    assert _is_nodata(-9999, -9999) is True
    assert _is_nodata(10, -9999) is False


def test_validate_band_index() -> None:
    assert _validate_band_index(1, 2) == 1
    assert _validate_band_index("2", 2) == 2

    with pytest.raises(ValueError):
        _validate_band_index(0, 2)

    with pytest.raises(ValueError):
        _validate_band_index(3, 2)


def test_band_value_2d() -> None:
    assert _band_value(RASTER_NDVI["data"], band_index=1, row=0, col=2) == 0.60


def test_band_value_3d() -> None:
    assert _band_value(RASTER_2BAND["data"], band_index=1, row=1, col=1) == 4
    assert _band_value(RASTER_2BAND["data"], band_index=2, row=1, col=1) == 40


def test_normalize_range_rule() -> None:
    rule = _normalize_rule(
        {
            "min": 0,
            "max": 10,
            "value": 1,
            "label": "low",
        },
        0,
    )

    assert rule["type"] == "range"
    assert rule["min"] == 0.0
    assert rule["max"] == 10.0
    assert rule["value"] == 1
    assert rule["label"] == "low"


def test_normalize_exact_rule() -> None:
    rule = _normalize_rule(
        {
            "equals": 5,
            "value": 50,
            "label": "five",
        },
        0,
    )

    assert rule["type"] == "equals"
    assert rule["equals"] == 5
    assert rule["value"] == 50


def test_normalize_values_rule() -> None:
    rule = _normalize_rule(
        {
            "values": [1, 2, 3],
            "value": 100,
            "label": "group",
        },
        0,
    )

    assert rule["type"] == "values"
    assert rule["values"] == [1, 2, 3]
    assert rule["value"] == 100


def test_normalize_list_rule() -> None:
    rule = _normalize_rule([0, 10, 1], 0)

    assert rule["type"] == "range"
    assert rule["min"] == 0.0
    assert rule["max"] == 10.0
    assert rule["value"] == 1


def test_normalize_rules_rejects_empty() -> None:
    with pytest.raises(ValueError):
        _normalize_rules([])


def test_matches_range_rule() -> None:
    rules = _normalize_rules(NDVI_RULES)

    assert _matches_rule(0.10, rules[0]) is True
    assert _matches_rule(0.25, rules[1]) is True
    assert _matches_rule(0.60, rules[2]) is True
    assert _matches_rule(2.0, rules[2]) is False


def test_matches_exact_rule() -> None:
    rules = _normalize_rules([
        {"equals": 5, "value": 50, "label": "five"},
    ])

    assert _matches_rule(5, rules[0]) is True
    assert _matches_rule(4, rules[0]) is False


def test_matches_values_rule() -> None:
    rules = _normalize_rules([
        {"values": [1, 2, 3], "value": 100, "label": "group"},
    ])

    assert _matches_rule(2, rules[0]) is True
    assert _matches_rule(4, rules[0]) is False


def test_find_matching_rule_first_match_wins() -> None:
    rules = _normalize_rules([
        {"min": 0, "max": 10, "value": 1, "label": "first"},
        {"min": 5, "max": 15, "value": 2, "label": "second"},
    ])

    rule = _find_matching_rule(7, rules)

    assert rule is not None
    assert rule["label"] == "first"


def test_reclassify_value_matched() -> None:
    rules = _normalize_rules(NDVI_RULES)

    value, status, rule = _reclassify_value(
        0.25,
        rules=rules,
        nodata=-9999,
        output_nodata=-9999,
        keep_unmatched=True,
        unmatched_value=None,
        precision=2,
    )

    assert value == 2
    assert status == "matched"
    assert rule is not None
    assert rule["label"] == "medium"


def test_reclassify_value_input_nodata() -> None:
    rules = _normalize_rules(NDVI_RULES)

    value, status, rule = _reclassify_value(
        None,
        rules=rules,
        nodata=-9999,
        output_nodata=-9999,
        keep_unmatched=True,
        unmatched_value=None,
        precision=2,
    )

    assert value == -9999
    assert status == "input_nodata"
    assert rule is None


def test_reclassify_value_unmatched_keep() -> None:
    rules = _normalize_rules(NDVI_RULES)

    value, status, rule = _reclassify_value(
        2.5,
        rules=rules,
        nodata=-9999,
        output_nodata=-9999,
        keep_unmatched=True,
        unmatched_value=0,
        precision=2,
    )

    assert value == 2.5
    assert status == "unmatched_kept"
    assert rule is None


def test_reclassify_value_unmatched_default() -> None:
    rules = _normalize_rules(NDVI_RULES)

    value, status, rule = _reclassify_value(
        2.5,
        rules=rules,
        nodata=-9999,
        output_nodata=-9999,
        keep_unmatched=False,
        unmatched_value=0,
        precision=2,
    )

    assert value == 0
    assert status == "unmatched_default"
    assert rule is None


def test_reclassify_raster_ndvi_classes() -> None:
    result = reclassify_raster(
        raster=RASTER_NDVI,
        rules=NDVI_RULES,
        output_nodata=-9999,
        keep_unmatched=False,
        unmatched_value=0,
        engine="python",
        precision=2,
    )

    data = _get_data(result)

    assert data == [
        [1, 2, 3],
        [1, 2, -9999],
    ]

    md = result.metadata
    assert md["source"] == "raster_reclassify"
    assert md["operation"] == "raster_reclassify"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["input_band_count"] == 1
    assert md["selected_band_index"] == 1
    assert md["output_band_count"] == 1
    assert md["width"] == 3
    assert md["height"] == 2
    assert md["rule_count"] == 3
    assert md["matched_pixel_count"] == 5
    assert md["input_nodata_pixel_count"] == 1
    assert md["unmatched_pixel_count"] == 0
    assert md["rule_match_counts"]["low"] == 2
    assert md["rule_match_counts"]["medium"] == 2
    assert md["rule_match_counts"]["high"] == 1


def test_reclassify_raster_exact_rule() -> None:
    rules = [
        {"equals": 1, "value": 10, "label": "one"},
        {"equals": 2, "value": 20, "label": "two"},
    ]

    result = reclassify_raster(
        raster=RASTER_CATEGORICAL,
        rules=rules,
        keep_unmatched=True,
        output_nodata=-9999,
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [10, 20, 3],
        [4, 5, 6],
    ]

    assert result.metadata["matched_pixel_count"] == 2
    assert result.metadata["unmatched_pixel_count"] == 4
    assert result.metadata["unmatched_kept_pixel_count"] == 4


def test_reclassify_raster_values_rule() -> None:
    rules = [
        {"values": [1, 2, 3], "value": 100, "label": "group_a"},
        {"values": [4, 5, 6], "value": 200, "label": "group_b"},
    ]

    result = reclassify_raster(
        raster=RASTER_CATEGORICAL,
        rules=rules,
        keep_unmatched=False,
        unmatched_value=0,
        output_nodata=-9999,
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [100, 100, 100],
        [200, 200, 200],
    ]

    assert result.metadata["matched_pixel_count"] == 6
    assert result.metadata["rule_match_counts"]["group_a"] == 3
    assert result.metadata["rule_match_counts"]["group_b"] == 3


def test_reclassify_raster_3d_selected_band() -> None:
    rules = [
        {"min": 0, "max": 15, "value": 1, "label": "low"},
        {"min": 15, "max": 50, "value": 2, "label": "high", "inclusive_min": False},
    ]

    result = reclassify_raster(
        raster=RASTER_2BAND,
        rules=rules,
        band_index=2,
        keep_unmatched=False,
        unmatched_value=0,
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [1, 2],
        [2, 2],
    ]

    assert result.metadata["input_band_count"] == 2
    assert result.metadata["selected_band_index"] == 2
    assert result.metadata["matched_pixel_count"] == 4


def test_reclassify_raster_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "raster_reclassify.yaml").write_text(
        """
default_engine: python
default_band_index: 1
default_nodata: -9999
default_output_nodata: 0
default_keep_unmatched: false
default_unmatched_value: 99
coordinate_precision: 3
preserve_metadata: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
default_rules:
  - min: -1.0
    max: 0.2
    value: 1
    label: low
    inclusive_min: true
    inclusive_max: false
  - min: 0.2
    max: 0.5
    value: 2
    label: medium
    inclusive_min: true
    inclusive_max: false
  - min: 0.5
    max: 1.0
    value: 3
    label: high
    inclusive_min: true
    inclusive_max: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": RASTER_NDVI["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 2],
        },
    }

    result = reclassify_raster(
        raster=raster,
    )

    data = _get_data(result)

    assert data == [
        [1, 2, 3],
        [1, 2, 0],
    ]

    assert result.metadata["output_nodata"] == 0
    assert result.metadata["keep_unmatched"] is False
    assert result.metadata["unmatched_value"] == 99
    assert result.metadata["coordinate_precision"] == 3
    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_reclassify_raster_metadata_merge() -> None:
    result = reclassify_raster(
        raster=RASTER_NDVI,
        rules=NDVI_RULES,
        metadata={"analysis_id": "reclass-1"},
    )

    assert result.metadata["analysis_id"] == "reclass-1"


def test_reclassify_raster_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        reclassify_raster(
            raster=RASTER_NDVI,
            rules=NDVI_RULES,
            metadata="bad",
        )


def test_reclassify_raster_rejects_missing_rules() -> None:
    with pytest.raises(ValueError, match="rules"):
        reclassify_raster(
            raster=RASTER_NDVI,
            rules=None,
        )


def test_reclassify_raster_rejects_invalid_rule() -> None:
    with pytest.raises(ValueError):
        reclassify_raster(
            raster=RASTER_NDVI,
            rules=[{"min": 0, "max": 1}],
        )


def test_reclassify_raster_rejects_invalid_band() -> None:
    with pytest.raises(ValueError, match="band_index"):
        reclassify_raster(
            raster=RASTER_2BAND,
            rules=NDVI_RULES,
            band_index=3,
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "reclassify_raster" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "reclassify_raster")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "reclassify_raster"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "raster" in descriptor.required_inputs
    assert "rules" in descriptor.required_inputs
    assert "band_index" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "raster"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "raster_reclassify"
    assert descriptor.metadata["classification_supported"] is True
    assert descriptor.metadata["threshold_supported"] is True
