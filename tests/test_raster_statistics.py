"""
Tests for raster_statistics plugin.

Run:
    pytest tests/test_raster_statistics.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.raster_statistics import (  # noqa: E402
    DEFAULT_STATS,
    PLUGIN,
    PLUGIN_ID,
    _band_values,
    _calculate_stats_for_values,
    _histogram,
    _is_nodata,
    _is_number,
    _majority_minority,
    _normalize_bands,
    _normalize_stats,
    _percentile,
    _validate_band_index,
    _validate_engine,
    _validate_precision,
    calculate_raster_statistics,
)


RASTER_2D = {
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


RASTER_2D_WITH_NODATA = {
    "data": [
        [1, 2, -9999],
        [4, None, 6],
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


RASTER_CATEGORICAL = {
    "data": [
        [1, 1, 2],
        [2, 3, 3],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "raster_statistics"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Raster Statistics"


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


def test_normalize_stats() -> None:
    assert _normalize_stats("mean") == ["mean"]
    assert _normalize_stats(["mean", "sum", "mean"]) == ["mean", "sum"]
    assert _normalize_stats(None) == DEFAULT_STATS

    with pytest.raises(ValueError):
        _normalize_stats(["bad"])


def test_validate_band_index() -> None:
    assert _validate_band_index(1, 2) == 1
    assert _validate_band_index("2", 2) == 2

    with pytest.raises(ValueError):
        _validate_band_index(0, 2)

    with pytest.raises(ValueError):
        _validate_band_index(3, 2)


def test_normalize_bands() -> None:
    assert _normalize_bands(None, 3) == [1, 2, 3]
    assert _normalize_bands(2, 3) == [2]
    assert _normalize_bands(["1", 2, 2], 3) == [1, 2]

    with pytest.raises(ValueError):
        _normalize_bands([4], 3)


def test_band_values_2d() -> None:
    assert _band_values(RASTER_2D["data"], 1) == [1, 2, 3, 4, 5, 6]


def test_band_values_3d() -> None:
    assert _band_values(RASTER_2BAND["data"], 1) == [1, 2, 3, 4]
    assert _band_values(RASTER_2BAND["data"], 2) == [10, 20, 30, 40]


def test_percentile() -> None:
    values = [1.0, 2.0, 3.0, 4.0]

    assert _percentile(values, 0) == 1.0
    assert _percentile(values, 25) == 1.75
    assert _percentile(values, 50) == 2.5
    assert _percentile(values, 75) == 3.25
    assert _percentile(values, 100) == 4.0


def test_majority_minority() -> None:
    majority, minority = _majority_minority([1, 1, 2, 3])

    assert majority == 1
    assert minority == 2


def test_histogram() -> None:
    result = _histogram([1, 2, 3, 4], bins=2, precision=2)

    assert result == [
        {"bin": 0, "min": 1.0, "max": 2.5, "count": 2},
        {"bin": 1, "min": 2.5, "max": 4.0, "count": 2},
    ]


def test_calculate_stats_for_values_numeric() -> None:
    result = _calculate_stats_for_values(
        [1, 2, 3, 4],
        nodata=-9999,
        requested_stats=[
            "count",
            "valid_count",
            "nodata_count",
            "numeric_count",
            "min",
            "max",
            "sum",
            "mean",
            "median",
            "p25",
            "p75",
        ],
        histogram_bins=2,
        precision=2,
    )

    assert result["count"] == 4
    assert result["valid_count"] == 4
    assert result["nodata_count"] == 0
    assert result["numeric_count"] == 4
    assert result["min"] == 1.0
    assert result["max"] == 4.0
    assert result["sum"] == 10.0
    assert result["mean"] == 2.5
    assert result["median"] == 2.5
    assert result["p25"] == 1.75
    assert result["p75"] == 3.25


def test_calculate_stats_for_values_with_nodata() -> None:
    result = _calculate_stats_for_values(
        [1, 2, -9999, None, 5],
        nodata=-9999,
        requested_stats=[
            "count",
            "valid_count",
            "nodata_count",
            "numeric_count",
            "sum",
            "mean",
        ],
        histogram_bins=2,
        precision=2,
    )

    assert result["count"] == 5
    assert result["valid_count"] == 3
    assert result["nodata_count"] == 2
    assert result["numeric_count"] == 3
    assert result["sum"] == 8.0
    assert result["mean"] == pytest.approx(2.67)


def test_calculate_raster_statistics_2d_basic() -> None:
    result = calculate_raster_statistics(
        raster=RASTER_2D,
        stats=["count", "valid_count", "nodata_count", "min", "max", "sum", "mean", "median"],
        engine="python",
        precision=2,
    )

    assert "statistics" in result
    assert "summary" in result
    assert "metadata" in result

    stats = result["statistics"]
    assert len(stats) == 1

    band = stats[0]

    assert band["band_index"] == 1
    assert band["count"] == 6
    assert band["valid_count"] == 6
    assert band["nodata_count"] == 0
    assert band["min"] == 1.0
    assert band["max"] == 6.0
    assert band["sum"] == 21.0
    assert band["mean"] == 3.5
    assert band["median"] == 3.5

    md = result["metadata"]
    assert md["source"] == "raster_statistics"
    assert md["operation"] == "raster_statistics"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["width"] == 3
    assert md["height"] == 2
    assert md["input_band_count"] == 1
    assert md["selected_bands"] == [1]

    summary = result["summary"]
    assert summary["band_result_count"] == 1
    assert summary["total_count"] == 6
    assert summary["total_valid_count"] == 6
    assert summary["global_min"] == 1.0
    assert summary["global_max"] == 6.0


def test_calculate_raster_statistics_2d_with_nodata() -> None:
    result = calculate_raster_statistics(
        raster=RASTER_2D_WITH_NODATA,
        stats=["count", "valid_count", "nodata_count", "numeric_count", "sum", "mean"],
        engine="python",
        precision=2,
    )

    band = result["statistics"][0]

    assert band["count"] == 6
    assert band["valid_count"] == 4
    assert band["nodata_count"] == 2
    assert band["numeric_count"] == 4
    assert band["sum"] == 13.0
    assert band["mean"] == 3.25

    assert result["summary"]["total_nodata_count"] == 2


def test_calculate_raster_statistics_2band_all_bands() -> None:
    result = calculate_raster_statistics(
        raster=RASTER_2BAND,
        stats=["count", "min", "max", "sum", "mean"],
        engine="python",
        precision=2,
    )

    assert len(result["statistics"]) == 2

    band1 = result["statistics"][0]
    band2 = result["statistics"][1]

    assert band1["band_index"] == 1
    assert band1["min"] == 1.0
    assert band1["max"] == 4.0
    assert band1["sum"] == 10.0
    assert band1["mean"] == 2.5

    assert band2["band_index"] == 2
    assert band2["min"] == 10.0
    assert band2["max"] == 40.0
    assert band2["sum"] == 100.0
    assert band2["mean"] == 25.0

    assert result["summary"]["band_result_count"] == 2
    assert result["summary"]["global_min"] == 1.0
    assert result["summary"]["global_max"] == 40.0


def test_calculate_raster_statistics_selected_band() -> None:
    result = calculate_raster_statistics(
        raster=RASTER_2BAND,
        bands=2,
        stats=["count", "min", "max", "mean"],
        engine="python",
    )

    assert len(result["statistics"]) == 1
    assert result["statistics"][0]["band_index"] == 2
    assert result["statistics"][0]["mean"] == 25.0
    assert result["metadata"]["selected_bands"] == [2]


def test_calculate_raster_statistics_histogram() -> None:
    result = calculate_raster_statistics(
        raster=RASTER_2D,
        stats=["count", "histogram"],
        histogram_bins=3,
        engine="python",
        precision=2,
    )

    band = result["statistics"][0]

    assert band["count"] == 6
    assert "histogram" in band
    assert len(band["histogram"]) == 3
    assert sum(item["count"] for item in band["histogram"]) == 6

    assert result["metadata"]["histogram_bins"] == 3
    assert "histogram" in result["metadata"]["stats"]


def test_calculate_raster_statistics_majority_minority_unique() -> None:
    result = calculate_raster_statistics(
        raster=RASTER_CATEGORICAL,
        stats=["count", "unique_count", "majority", "minority"],
        engine="python",
    )

    band = result["statistics"][0]

    assert band["count"] == 6
    assert band["unique_count"] == 3
    assert band["majority"] == 1
    assert band["minority"] == 1


def test_calculate_raster_statistics_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "raster_statistics.yaml").write_text(
        """
default_engine: python
default_stats:
  - count
  - mean
  - histogram
default_bands:
  - 2
default_nodata: -9999
histogram:
  enabled: true
  bins: 2
coordinate_precision: 3
preserve_metadata: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": RASTER_2BAND["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 2],
        },
    }

    result = calculate_raster_statistics(
        raster=raster,
    )

    assert len(result["statistics"]) == 1

    band = result["statistics"][0]

    assert band["band_index"] == 2
    assert band["count"] == 4
    assert band["mean"] == 25.0
    assert len(band["histogram"]) == 2

    assert result["metadata"]["selected_bands"] == [2]
    assert result["metadata"]["coordinate_precision"] == 3
    assert result["metadata"]["source_crs"] == "EPSG:4326"
    assert result["metadata"]["warning"] is not None
    assert "geographic CRS" in result["metadata"]["warning"]


def test_calculate_raster_statistics_metadata_merge() -> None:
    result = calculate_raster_statistics(
        raster=RASTER_2D,
        stats=["count"],
        metadata={"analysis_id": "rstats-1"},
    )

    assert result["metadata"]["analysis_id"] == "rstats-1"


def test_calculate_raster_statistics_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_raster_statistics(
            raster=RASTER_2D,
            stats=["count"],
            metadata="bad",
        )


def test_calculate_raster_statistics_rejects_invalid_stats() -> None:
    with pytest.raises(ValueError):
        calculate_raster_statistics(
            raster=RASTER_2D,
            stats=["bad"],
        )


def test_calculate_raster_statistics_rejects_invalid_band() -> None:
    with pytest.raises(ValueError, match="out of range"):
        calculate_raster_statistics(
            raster=RASTER_2BAND,
            bands=3,
        )


def test_calculate_raster_statistics_rejects_invalid_histogram_bins() -> None:
    with pytest.raises(ValueError, match="histogram_bins"):
        calculate_raster_statistics(
            raster=RASTER_2D,
            stats=["histogram"],
            histogram_bins=0,
        )


def test_calculate_raster_statistics_rejects_invalid_raster() -> None:
    with pytest.raises(ValueError):
        calculate_raster_statistics(
            raster={"metadata": {}},
            stats=["count"],
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_raster_statistics" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_raster_statistics")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_raster_statistics"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "json"
    assert "raster" in descriptor.required_inputs
    assert "stats" in descriptor.optional_inputs
    assert "bands" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "statistics"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "raster_statistics"
    assert descriptor.metadata["histogram_supported"] is True
    assert descriptor.metadata["multi_band_supported"] is True
