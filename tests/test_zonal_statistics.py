"""
Tests for zonal_statistics plugin.

Run:
    pytest tests/test_zonal_statistics.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.zonal_statistics import (  # noqa: E402
    DEFAULT_STATS,
    PLUGIN,
    PLUGIN_ID,
    _calculate_zone_stats,
    _collect_zone_values,
    _extract_zones,
    _is_nodata,
    _is_number,
    _majority_minority,
    _normalize_stats,
    _pixel_value,
    _point_matches_zone,
    _validate_band_index,
    _validate_engine,
    _validate_precision,
    calculate_zonal_statistics,
)


RASTER_5X5 = {
    "data": [
        [1, 2, 3, 4, 5],
        [6, 7, 8, 9, 10],
        [11, 12, 13, 14, 15],
        [16, 17, 18, 19, 20],
        [21, 22, 23, 24, 25],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 5],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_5X5_WITH_NODATA = {
    "data": [
        [1, 2, 3, 4, 5],
        [6, 7, 8, 9, 10],
        [11, 12, -9999, 14, 15],
        [16, 17, 18, 19, 20],
        [21, 22, 23, 24, 25],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 5],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_2BAND_3X3 = {
    "data": [
        [
            [1, 2, 3],
            [4, 5, 6],
            [7, 8, 9],
        ],
        [
            [10, 20, 30],
            [40, 50, 60],
            [70, 80, 90],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
    },
}


ZONE_CENTER = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [1.0, 1.0],
            [4.0, 1.0],
            [4.0, 4.0],
            [1.0, 4.0],
            [1.0, 1.0],
        ]],
    },
    "properties": {
        "id": "zone-center",
        "name": "Center Zone",
        "type": "test",
    },
}


ZONE_FULL_3X3 = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [0.0, 0.0],
            [3.0, 0.0],
            [3.0, 3.0],
            [0.0, 3.0],
            [0.0, 0.0],
        ]],
    },
    "properties": {
        "id": "zone-full",
        "name": "Full 3x3",
    },
}


ZONE_OUTSIDE = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [100.0, 100.0],
            [110.0, 100.0],
            [110.0, 110.0],
            [100.0, 110.0],
            [100.0, 100.0],
        ]],
    },
    "properties": {
        "id": "zone-outside",
        "name": "Outside",
    },
}


ZONE_NULL = {
    "type": "Feature",
    "geometry": None,
    "properties": {
        "id": "zone-null",
    },
}


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "zonal_statistics"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Zonal Statistics"


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


def test_validate_band_index() -> None:
    assert _validate_band_index(1, band_count=2) == 1
    assert _validate_band_index("2", band_count=2) == 2

    with pytest.raises(ValueError):
        _validate_band_index(0, band_count=2)

    with pytest.raises(ValueError):
        _validate_band_index(3, band_count=2)


def test_normalize_stats() -> None:
    assert _normalize_stats("mean") == ["mean"]
    assert _normalize_stats(["mean", "sum", "mean"]) == ["mean", "sum"]
    assert _normalize_stats(None) == DEFAULT_STATS

    with pytest.raises(ValueError):
        _normalize_stats(["bad"])


def test_is_number() -> None:
    assert _is_number(1) is True
    assert _is_number(1.5) is True
    assert _is_number(True) is False
    assert _is_number("1") is False


def test_is_nodata() -> None:
    assert _is_nodata(None, -9999) is True
    assert _is_nodata(-9999, -9999) is True
    assert _is_nodata(10, -9999) is False


def test_majority_minority() -> None:
    majority, minority = _majority_minority([1, 1, 2, 3])

    assert majority == 1
    assert minority == 2


def test_pixel_value_2d() -> None:
    assert _pixel_value(RASTER_5X5["data"], row=2, col=2, band_index=1) == 13


def test_pixel_value_3d() -> None:
    assert _pixel_value(RASTER_2BAND_3X3["data"], row=1, col=1, band_index=1) == 5
    assert _pixel_value(RASTER_2BAND_3X3["data"], row=1, col=1, band_index=2) == 50


def test_point_matches_zone() -> None:
    assert _point_matches_zone(2.0, 2.0, ZONE_CENTER["geometry"]) is True
    assert _point_matches_zone(0.5, 0.5, ZONE_CENTER["geometry"]) is False
    assert _point_matches_zone(2.0, 2.0, None) is False


def test_calculate_zone_stats_numeric() -> None:
    stats = _calculate_zone_stats(
        [7, 8, 9, 12, 13, 14, 17, 18, 19],
        nodata=-9999,
        precision=2,
    )

    assert stats["count"] == 9
    assert stats["valid_count"] == 9
    assert stats["nodata_count"] == 0
    assert stats["numeric_count"] == 9
    assert stats["min"] == 7.0
    assert stats["max"] == 19.0
    assert stats["sum"] == 117.0
    assert stats["mean"] == 13.0
    assert stats["median"] == 13.0
    assert stats["unique_count"] == 9


def test_calculate_zone_stats_with_nodata() -> None:
    stats = _calculate_zone_stats(
        [7, 8, 9, 12, -9999, 14, 17, 18, 19],
        nodata=-9999,
        precision=2,
    )

    assert stats["count"] == 9
    assert stats["valid_count"] == 8
    assert stats["nodata_count"] == 1
    assert stats["numeric_count"] == 8
    assert stats["sum"] == 104.0
    assert stats["mean"] == 13.0


def test_extract_zones_from_list() -> None:
    zones, info = _extract_zones([ZONE_CENTER])

    assert len(zones) == 1
    assert info["zones_input_geojson_type"] == "FeatureList"


def test_extract_zones_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [ZONE_CENTER, ZONE_OUTSIDE],
    }

    zones, info = _extract_zones(collection)

    assert len(zones) == 2
    assert info["zones_input_geojson_type"] == "FeatureCollection"


def test_extract_zones_from_vectorout() -> None:
    vector = VectorOut(
        features=[ZONE_CENTER],
        metadata={"source": "test-zones"},
    )

    zones, info = _extract_zones(vector)

    assert len(zones) == 1
    assert info["zones_input_type"] == "VectorOut"
    assert info["zones_input_metadata"]["source"] == "test-zones"


def test_collect_zone_values_center_polygon() -> None:
    values = _collect_zone_values(
        data=RASTER_5X5["data"],
        transform=[1, 0, 0, 0, -1, 5],
        zone_geometry=ZONE_CENTER["geometry"],
        band_index=1,
        all_touched=False,
    )

    assert values == [7, 8, 9, 12, 13, 14, 17, 18, 19]


def test_calculate_zonal_statistics_center_zone() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_CENTER],
        stats=["count", "valid_count", "nodata_count", "min", "max", "sum", "mean", "median"],
        band_index=1,
        engine="python",
        precision=2,
    )

    assert len(result.features) == 1

    feature = result.features[0]
    props = feature["properties"]

    assert feature["geometry"]["type"] == "Polygon"

    assert props["id"] == "zone-center"
    assert props["zone_index"] == 0
    assert props["zone_id"] == "zone-center"
    assert props["status"] == "success"
    assert props["engine"] == "python"
    assert props["band_index"] == 1

    assert props["zonal_count"] == 9
    assert props["zonal_valid_count"] == 9
    assert props["zonal_nodata_count"] == 0
    assert props["zonal_min"] == 7.0
    assert props["zonal_max"] == 19.0
    assert props["zonal_sum"] == 117.0
    assert props["zonal_mean"] == 13.0
    assert props["zonal_median"] == 13.0

    md = result.metadata
    assert md["source"] == "zonal_statistics"
    assert md["operation"] == "zonal_statistics"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["raster_width"] == 5
    assert md["raster_height"] == 5
    assert md["raster_band_count"] == 1
    assert md["zone_count"] == 1
    assert md["empty_zone_count"] == 0
    assert md["total_selected_pixel_count"] == 9
    assert md["total_valid_pixel_count"] == 9


def test_calculate_zonal_statistics_with_nodata() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5_WITH_NODATA,
        zones=[ZONE_CENTER],
        stats=["count", "valid_count", "nodata_count", "sum", "mean"],
        band_index=1,
        engine="python",
        precision=2,
    )

    props = result.features[0]["properties"]

    assert props["zonal_count"] == 9
    assert props["zonal_valid_count"] == 8
    assert props["zonal_nodata_count"] == 1
    assert props["zonal_sum"] == 104.0
    assert props["zonal_mean"] == 13.0


def test_calculate_zonal_statistics_outside_zone_empty() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_OUTSIDE],
        stats=["count", "valid_count", "mean"],
        band_index=1,
        engine="python",
    )

    props = result.features[0]["properties"]

    assert props["status"] == "empty"
    assert props["zonal_count"] == 0
    assert props["zonal_valid_count"] == 0
    assert props["zonal_mean"] is None

    assert result.metadata["empty_zone_count"] == 1


def test_calculate_zonal_statistics_null_geometry_empty() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_NULL],
        stats=["count", "mean"],
        engine="python",
    )

    props = result.features[0]["properties"]

    assert props["status"] == "empty"
    assert props["zonal_count"] == 0
    assert props["zonal_mean"] is None


def test_calculate_zonal_statistics_multiple_zones() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_CENTER, ZONE_OUTSIDE],
        stats=["count", "mean"],
        engine="python",
    )

    assert len(result.features) == 2

    center = result.features[0]["properties"]
    outside = result.features[1]["properties"]

    assert center["status"] == "success"
    assert center["zonal_count"] == 9
    assert center["zonal_mean"] == 13.0

    assert outside["status"] == "empty"
    assert outside["zonal_count"] == 0
    assert outside["zonal_mean"] is None

    assert result.metadata["zone_count"] == 2
    assert result.metadata["empty_zone_count"] == 1


def test_calculate_zonal_statistics_3d_band_2() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_2BAND_3X3,
        zones=[ZONE_FULL_3X3],
        stats=["count", "sum", "mean", "min", "max"],
        band_index=2,
        engine="python",
        precision=2,
    )

    props = result.features[0]["properties"]

    assert props["band_index"] == 2
    assert props["zonal_count"] == 9
    assert props["zonal_sum"] == 450.0
    assert props["zonal_mean"] == 50.0
    assert props["zonal_min"] == 10.0
    assert props["zonal_max"] == 90.0

    assert result.metadata["raster_band_count"] == 2
    assert result.metadata["band_index"] == 2


def test_calculate_zonal_statistics_zone_id_field() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_CENTER],
        stats=["count"],
        zone_id_field="name",
        engine="python",
    )

    props = result.features[0]["properties"]

    assert props["zone_id"] == "Center Zone"


def test_calculate_zonal_statistics_without_geometry_output() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_CENTER],
        stats=["count"],
        include_zone_geometry=False,
        engine="python",
    )

    assert result.features[0]["geometry"] is None
    assert result.metadata["include_zone_geometry"] is False


def test_calculate_zonal_statistics_custom_prefix() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_CENTER],
        stats=["count", "mean"],
        stat_prefix="rs_",
        engine="python",
    )

    props = result.features[0]["properties"]

    assert props["rs_count"] == 9
    assert props["rs_mean"] == 13.0
    assert "zonal_count" not in props

    assert result.metadata["stat_prefix"] == "rs_"


def test_calculate_zonal_statistics_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "zonal_statistics.yaml").write_text(
        """
default_engine: python
default_band_index: 1
default_all_touched: false
default_include_zone_geometry: false
preserve_properties: true
stat_prefix: zs_
default_stats:
  - count
  - mean
default_nodata: -9999
coordinate_precision: 3
source_crs: EPSG:4326
warn_if_geographic_crs: true
fields:
  zone_index_field: zi
  zone_id_field: zid
  status_field: zstatus
  engine_field: zengine
  band_index_field: zband
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": RASTER_5X5["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 5],
        },
    }

    result = calculate_zonal_statistics(
        raster=raster,
        zones=[ZONE_CENTER],
    )

    feature = result.features[0]
    props = feature["properties"]

    assert feature["geometry"] is None

    assert props["zi"] == 0
    assert props["zid"] == "zone-center"
    assert props["zstatus"] == "success"
    assert props["zengine"] == "python"
    assert props["zband"] == 1
    assert props["zs_count"] == 9
    assert props["zs_mean"] == 13.0

    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_calculate_zonal_statistics_metadata_merge() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_CENTER],
        stats=["count"],
        metadata={"analysis_id": "zonal-1"},
    )

    assert result.metadata["analysis_id"] == "zonal-1"


def test_calculate_zonal_statistics_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_zonal_statistics(
            raster=RASTER_5X5,
            zones=[ZONE_CENTER],
            stats=["count"],
            metadata="bad",
        )


def test_calculate_zonal_statistics_rejects_invalid_zones() -> None:
    with pytest.raises(ValueError):
        calculate_zonal_statistics(
            raster=RASTER_5X5,
            zones={"type": "Point", "coordinates": [1, 2]},
        )


def test_calculate_zonal_statistics_rejects_invalid_band() -> None:
    with pytest.raises(ValueError, match="band_index"):
        calculate_zonal_statistics(
            raster=RASTER_2BAND_3X3,
            zones=[ZONE_FULL_3X3],
            band_index=3,
        )


def test_vectorout_to_artifact() -> None:
    result = calculate_zonal_statistics(
        raster=RASTER_5X5,
        zones=[ZONE_CENTER],
        stats=["count"],
    )

    artifact = result.to_artifact(produced_by="test_zonal_statistics")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_zonal_statistics"
    assert artifact.payload["source"] == "zonal_statistics"
    assert artifact.payload["operation"] == "zonal_statistics"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_zonal_statistics" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_zonal_statistics")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_zonal_statistics"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "raster" in descriptor.required_inputs
    assert "zones" in descriptor.required_inputs
    assert "stats" in descriptor.optional_inputs
    assert "band_index" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "zonal_statistics"
    assert descriptor.metadata["raster_vector_fusion"] is True
