"""
Tests for spatial_join's STRtree-based candidate narrowing.

Two things are tested here, mirroring the smart-spatial-tehran-tod-gradient
case study's report (spatial_join_features with engine="shapely" took
several minutes on 35,225 source points against 488 target polygons - a
naive O(n*m) double loop, ~17 million predicate checks):

    1. Correctness: the STRtree-narrowed result must be IDENTICAL to a
       brute-force reference implementation, not just faster.
    2. Performance: on a larger synthetic dataset, the STRtree path must
       be substantially faster than the brute-force double loop.

Run:
    pytest tests/test_spatial_join_strtree_performance.py -v
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

shapely = pytest.importorskip("shapely")
from geochat_sdk.exceptions import SDKDependencyError  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from plugins.spatial_join import spatial_join_features  # noqa: E402


def _make_points(count: int, *, seed: int, extent: float = 1000.0) -> list[dict]:
    rng = random.Random(seed)
    return [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [rng.uniform(0, extent), rng.uniform(0, extent)],
            },
            "properties": {"id": f"p{i}"},
        }
        for i in range(count)
    ]


def _make_zones(count: int, *, seed: int, extent: float = 1000.0, size: float = 150.0) -> list[dict]:
    rng = random.Random(seed)
    zones = []
    for i in range(count):
        cx = rng.uniform(0, extent)
        cy = rng.uniform(0, extent)
        half = size / 2.0
        ring = [
            [cx - half, cy - half],
            [cx + half, cy - half],
            [cx + half, cy + half],
            [cx - half, cy + half],
            [cx - half, cy - half],
        ]
        zones.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"zone_id": f"z{i}"},
            }
        )
    return zones


def _brute_force_within_pairs(points: list[dict], zones: list[dict]) -> set[tuple[int, int]]:
    point_geoms = [shape(f["geometry"]) for f in points]
    zone_geoms = [shape(f["geometry"]) for f in zones]

    pairs: set[tuple[int, int]] = set()
    for pi, pg in enumerate(point_geoms):
        for zi, zg in enumerate(zone_geoms):
            if pg.within(zg):
                pairs.add((pi, zi))
    return pairs


# ------------------------------------------------------------------ #
# 1. Correctness: STRtree-narrowed result == brute-force reference.
# ------------------------------------------------------------------ #

def test_strtree_result_matches_brute_force_reference():
    points = _make_points(100, seed=1)
    zones = _make_zones(10, seed=2)

    expected_pairs = _brute_force_within_pairs(points, zones)

    result = spatial_join_features(
        source_features=points,
        target_features=zones,
        predicate="within",
        join_type="left",
        cardinality="one_to_many",
        engine="shapely",
    )

    assert result.metadata["spatial_index_used"] is True

    actual_pairs = {
        (f["properties"]["_source_index"], f["properties"]["_target_index"])
        for f in result.features
        if f["properties"]["_join_status"] == "matched"
    }

    assert actual_pairs == expected_pairs

    # Every source, matched or not, must appear at least once (join_type="left").
    source_indices_in_output = {f["properties"]["_source_index"] for f in result.features}
    assert source_indices_in_output == set(range(len(points)))


def test_strtree_falls_back_to_naive_loop_when_a_target_geometry_is_invalid():
    points = _make_points(20, seed=3)
    zones = _make_zones(3, seed=4)
    zones.append(
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]},  # degenerate/invalid
            "properties": {"zone_id": "broken"},
        }
    )

    result = spatial_join_features(
        source_features=points,
        target_features=zones,
        predicate="within",
        join_type="left",
        cardinality="one_to_many",
        engine="shapely",
    )

    # An unparseable target geometry must bail the whole call back to the
    # naive loop, not silently drop that target from consideration.
    assert result.metadata["spatial_index_used"] is False
    assert result.metadata["source_feature_count"] == len(points)


# ------------------------------------------------------------------ #
# 2. Performance: STRtree path is substantially faster than brute force
#    at a scale representative of the reported case study.
# ------------------------------------------------------------------ #

def test_strtree_is_substantially_faster_than_the_naive_double_loop(monkeypatch):
    """
    Compares the plugin against itself with the spatial index forced off
    (via _get_strtree_tools raising, the same path taken when shapely is
    unavailable), rather than against a bare shapely loop with none of
    spatial_join_features's own per-pair overhead (feature construction,
    property deepcopy, etc.) - that would still show a real speedup, but
    a much smaller one than what actually matters: full plugin runtime,
    matching how the case study measured "several minutes" in the first
    place. A conservative 5x threshold comfortably clears measured
    speedups in the tens-of-x range at this scale while leaving headroom
    against timing noise.
    """
    import plugins.spatial_join as spatial_join_module

    points = _make_points(3000, seed=5)
    zones = _make_zones(80, seed=6)

    start = time.perf_counter()
    indexed_result = spatial_join_features(
        source_features=points,
        target_features=zones,
        predicate="within",
        join_type="left",
        cardinality="one_to_many",
        engine="shapely",
    )
    indexed_seconds = time.perf_counter() - start

    assert indexed_result.metadata["spatial_index_used"] is True

    def _force_no_index():
        raise SDKDependencyError("forced naive fallback for benchmark")

    monkeypatch.setattr(spatial_join_module, "_get_strtree_tools", _force_no_index)

    start = time.perf_counter()
    naive_result = spatial_join_features(
        source_features=points,
        target_features=zones,
        predicate="within",
        join_type="left",
        cardinality="one_to_many",
        engine="shapely",
    )
    naive_seconds = time.perf_counter() - start

    assert naive_result.metadata["spatial_index_used"] is False
    assert indexed_seconds < naive_seconds / 5
