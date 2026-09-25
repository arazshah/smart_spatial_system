"""
Regression test: zonal_statistics(all_touched=True) counted pixels lying
entirely inside a polygon's hole.

_polygon_intersects_square skipped a hole whenever _ring_intersects_square
(hole, pixel) was True - but that function is also True when the pixel lies
*inside* the ring (a corner-in-ring test), which is exactly the case where the
pixel must be excluded. Found in the Lake Urmia case study, where the zones
are the coastal counties around the lake and the lake itself is a hole in
their union.

Run:
    pytest tests/test_zonal_all_touched_hole_interior.py -v
"""

from __future__ import annotations

from plugins.zonal_statistics import calculate_zonal_statistics

# 10 x 10 raster of ones, 1 m pixels, covering x 0..10, y 0..10
RASTER = {"data": [[1.0] * 10 for _ in range(10)], "metadata": {"transform": [1, 0, 0, 0, -1, 10]}}

# outer 0..10 square with a hole 2.5..7.5: the hole boundary crosses pixel
# columns/rows 2 and 7; the 4 x 4 pixels 3..6 lie entirely inside the hole.
HOLED = {
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[2.5, 2.5], [7.5, 2.5], [7.5, 7.5], [2.5, 7.5], [2.5, 2.5]],
        ],
    },
}


def _count(all_touched: bool) -> int:
    out = calculate_zonal_statistics(RASTER, zones=HOLED, stats=["count"], all_touched=all_touched,
                                     engine="python")
    return out.features[0]["properties"]["zonal_count"]


def test_all_touched_excludes_pixels_entirely_inside_hole():
    # 100 pixels minus the 16 wholly inside the hole
    assert _count(all_touched=True) == 84


def test_center_rule_unchanged():
    # pixel centres sit at .5, so rows/cols 2..7 have centres on or inside the
    # hole boundary (2.5..7.5); the ray-casting rule assigns 5 x 5 of them to the
    # hole. This path was already correct and must not change.
    assert _count(all_touched=False) == 100 - 25
