"""
Regression test for case-study bug 003.

raster_to_vector(mode="components") merged different classes into one
component: _connected_components grew a component across ANY selected
neighbor, regardless of pixel value, instead of only across neighbors with
the SAME value.

Run:
    pytest tests/test_bug_003_raster_to_vector_components_by_value.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.raster_to_vector import raster_to_vector  # noqa: E402


def test_components_mode_does_not_merge_different_classes():
    raster = {
        "data": [[1, 1, 2, 2], [1, 1, 2, 2]],
        "metadata": {"transform": [1, 0, 0, 0, -1, 2]},
    }

    result = raster_to_vector(
        raster,
        include_values=[1, 2],
        mode="components",
    )

    features = result["features"]

    assert len(features) == 2

    by_value = {feature["properties"]["value"]: feature for feature in features}
    assert set(by_value.keys()) == {1, 2}
    assert by_value[1]["properties"]["pixel_count"] == 4
    assert by_value[2]["properties"]["pixel_count"] == 4
