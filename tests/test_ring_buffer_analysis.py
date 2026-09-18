"""
Tests for ring_buffer_analysis plugin.

Run:
    pytest tests/test_ring_buffer_analysis.py -v
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.exceptions import SDKDependencyError  # noqa: E402
from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.ring_buffer_analysis import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _format_ring_label,
    _validate_distances,
    _validate_engine,
    generate_ring_buffers,
)

shapely = pytest.importorskip("shapely")
from shapely.geometry import shape  # noqa: E402


@pytest.fixture
def sample_point_feature():
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "properties": {"id": 1, "name": "Station A"},
        }
    ]


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "ring_buffer_analysis"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Ring Buffer Analysis"


def test_validate_distances_sorts_ascending() -> None:
    assert _validate_distances([800, 200, 1200, 500]) == [200.0, 500.0, 800.0, 1200.0]


def test_validate_distances_rejects_empty() -> None:
    with pytest.raises(ValueError):
        _validate_distances([])


def test_validate_distances_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        _validate_distances([200, 0, 500])

    with pytest.raises(ValueError, match="positive"):
        _validate_distances([200, -500])


def test_validate_distances_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _validate_distances([200, 500, 200])


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("shapely") == "shapely"
    assert _validate_engine("python") == "python"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_format_ring_label() -> None:
    assert _format_ring_label(0, 200, "m") == "0-200m"
    assert _format_ring_label(200, 500, "m") == "200-500m"


def test_generate_ring_buffers_python_engine_rejects() -> None:
    with pytest.raises(SDKDependencyError, match="ring buffers require shapely"):
        generate_ring_buffers(
            features=[
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                    "properties": {},
                }
            ],
            distances=[200],
            engine="python",
        )


def test_generate_ring_buffers_shapely_engine(sample_point_feature) -> None:
    result = generate_ring_buffers(
        features=sample_point_feature,
        distances=[200, 500, 800, 1200],
        engine="shapely",
    )

    assert isinstance(result, VectorOut)
    assert len(result.features) == 4

    labels = [f["properties"]["ring_label"] for f in result.features]
    assert labels == ["0-200m", "200-500m", "500-800m", "800-1200m"]

    for idx, feature in enumerate(result.features):
        props = feature["properties"]
        assert props["ring_index"] == idx
        assert props["source_feature_index"] == 0
        assert props["id"] == 1
        assert props["name"] == "Station A"

    assert result.features[0]["properties"]["ring_inner"] == 0
    assert result.features[0]["properties"]["ring_outer"] == 200
    assert result.features[-1]["properties"]["ring_inner"] == 800
    assert result.features[-1]["properties"]["ring_outer"] == 1200

    ring_geoms = [shape(f["geometry"]) for f in result.features]

    total_area = sum(g.area for g in ring_geoms)
    expected_area = math.pi * 1200.0 ** 2
    assert total_area == pytest.approx(expected_area, rel=0.01)

    for i in range(len(ring_geoms)):
        for j in range(i + 1, len(ring_geoms)):
            overlap = ring_geoms[i].intersection(ring_geoms[j]).area
            assert overlap == pytest.approx(0.0, abs=1.0)

    md = result.metadata
    assert md["source"] == "ring_buffer_analysis"
    assert md["operation"] == "ring_buffer"
    assert md["distances"] == [200.0, 500.0, 800.0, 1200.0]
    assert md["ring_count"] == 4
    assert md["input_feature_count"] == 1
    assert md["output_feature_count"] == 4


def test_generate_ring_buffers_auto_sorts_unsorted_distances(sample_point_feature) -> None:
    result = generate_ring_buffers(
        features=sample_point_feature,
        distances=[500, 200],
        engine="shapely",
    )

    labels = [f["properties"]["ring_label"] for f in result.features]
    assert labels == ["0-200m", "200-500m"]


def test_generate_ring_buffers_metadata_merge(sample_point_feature) -> None:
    result = generate_ring_buffers(
        features=sample_point_feature,
        distances=[200],
        engine="shapely",
        metadata={"analysis_id": "abc"},
    )

    assert result.metadata["analysis_id"] == "abc"


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "generate_ring_buffers" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "generate_ring_buffers")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "generate_ring_buffers"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "distances" in descriptor.required_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "ring_buffer"
