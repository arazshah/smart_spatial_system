"""
Regression test for case-study bug 002.

zonal_statistics(all_touched=True) used the zone's bounding box instead of
the actual polygon geometry to decide which pixels belong to a zone, so a
triangular zone counted every pixel of the raster's bbox as "touched".

Run:
    pytest tests/test_bug_002_zonal_statistics_all_touched_polygon.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.zonal_statistics import calculate_zonal_statistics  # noqa: E402

RASTER_10X10 = {
    "data": [[1.0] * 10 for _ in range(10)],
    "metadata": {"transform": [1, 0, 0, 0, -1, 10]},
}

TRIANGLE_ZONE = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 0], [0, 10], [0, 0]]],
    },
    "properties": {},
}


def test_all_touched_false_matches_baseline_45_pixels():
    """
    Sanity check: the already-correct center-based (all_touched=False) path
    must still select exactly 45 pixels for this triangle.
    """
    result = calculate_zonal_statistics(
        RASTER_10X10,
        [TRIANGLE_ZONE],
        all_touched=False,
    )

    assert result.features[0]["properties"]["zonal_count"] == 45


def test_all_touched_true_uses_actual_polygon_not_zone_bbox():
    """
    all_touched=True must use the actual triangle geometry, not the zone's
    bounding box (which spans the whole 10x10 raster and would wrongly
    select all 100 pixels).
    """
    result = calculate_zonal_statistics(
        RASTER_10X10,
        [TRIANGLE_ZONE],
        all_touched=True,
    )

    count = result.features[0]["properties"]["zonal_count"]

    assert count != 100
    assert count <= 65
    # Must select at least the strictly-contained pixels.
    assert count >= 45
