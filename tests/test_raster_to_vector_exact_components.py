"""
Regression test: raster_to_vector(mode="components") returned each
component's bounding box as its geometry, so a component's polygon area was
the bbox area, not the area of its cells (an L-shaped lake arm, or a ring of
salt crust around open water, was reported as a filled rectangle). Found in
the Lake Urmia case study, where the vectorized open-water body is the
measured quantity.

The geometry must be the exact union of the component's cells, holes
included.

Run:
    pytest tests/test_raster_to_vector_exact_components.py -v
"""

from __future__ import annotations

import random

import pytest
import shapely
from shapely.geometry import box, shape
from shapely.ops import unary_union

from plugins.raster_to_vector import raster_to_vector

T = [10.0, 0.0, 1000.0, 0.0, -10.0, 5000.0]


def _run(data, connectivity=4, include_values=None):
    return raster_to_vector(
        {"data": data, "metadata": {"transform": T}},
        mode="components",
        connectivity=connectivity,
        include_values=include_values if include_values is not None else [1],
        include_component_cells=True,
    )


def _cells_union(cells):
    return unary_union([box(1000 + 10 * c, 5000 - 10 * (r + 1), 1000 + 10 * (c + 1), 5000 - 10 * r)
                        for r, c in cells])


def test_l_shape_area_is_cells_not_bbox():
    data = [[1, 0, 0],
            [1, 0, 0],
            [1, 1, 1]]
    feat = _run(data)["features"][0]
    geom = shape(feat["geometry"])
    assert geom.area == pytest.approx(5 * 100.0)  # 5 cells, not the 9-cell bbox
    assert feat["properties"]["bbox_mode"] is False


def test_ring_component_has_hole():
    data = [[1, 1, 1],
            [1, 0, 1],
            [1, 1, 1]]
    geom = shape(_run(data)["features"][0]["geometry"])
    assert geom.geom_type == "Polygon" and len(geom.interiors) == 1
    assert geom.area == pytest.approx(8 * 100.0)
    assert geom.is_valid


def test_exterior_counter_clockwise_holes_clockwise():
    data = [[1, 1, 1], [1, 0, 1], [1, 1, 1]]
    coords = _run(data)["features"][0]["geometry"]["coordinates"]
    assert shapely.LinearRing(coords[0]).is_ccw
    assert not shapely.LinearRing(coords[1]).is_ccw


@pytest.mark.parametrize("connectivity", [4, 8])
@pytest.mark.parametrize("seed", range(12))
def test_random_masks_equal_union_of_cells(connectivity, seed):
    rnd = random.Random(seed)
    h, w = rnd.randint(3, 14), rnd.randint(3, 14)
    p = rnd.uniform(0.3, 0.8)
    data = [[1 if rnd.random() < p else rnd.choice([0, 2]) for _ in range(w)] for _ in range(h)]
    out = _run(data, connectivity=connectivity, include_values=[1, 2])
    total = 0
    for feat in out["features"]:
        cells = [(c["row"], c["col"]) for c in feat["properties"]["cells"]]
        geom = shape(feat["geometry"])
        assert geom.is_valid, feat["geometry"]
        expected = _cells_union(cells)
        assert geom.symmetric_difference(expected).area == pytest.approx(0.0, abs=1e-6)
        assert geom.area == pytest.approx(100.0 * feat["properties"]["pixel_count"])
        total += feat["properties"]["pixel_count"]
    assert total == sum(1 for row in data for v in row if v in (1, 2))
