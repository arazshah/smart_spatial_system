"""
Tests for nearest_neighbor's STRtree-based candidate search.

Two things are tested here, mirroring
tests/test_spatial_join_strtree_performance.py's own precedent (found
while diagnosing a downstream case study's ~120s s3geo.query() calls
against 964 polygon x 1020 point features): find_nearest_neighbors()
computed nearest neighbors with a brute-force O(source * target) nested
loop, calling _calculate_distance() once per pair with no spatial index
anywhere in the file.

    1. Correctness: the STRtree-accelerated result must be IDENTICAL to
       the original brute-force nested loop, not just faster - same
       distances, ranks, indices, statuses, engines and properties for
       every output feature, across varied geometry types/sizes and at
       least one unmatched/max_distance-excluded case.
    2. Performance: on a larger synthetic dataset, the indexed path must
       be substantially faster than the brute-force nested loop.

Run:
    pytest tests/test_nearest_neighbor_strtree_performance.py -v
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

pytest.importorskip("shapely")

import plugins.nearest_neighbor as nearest_neighbor_module  # noqa: E402
from plugins.nearest_neighbor import find_nearest_neighbors  # noqa: E402


def _make_points(count: int, *, seed: int, extent: float = 1000.0, prefix: str = "p") -> list[dict]:
    rng = random.Random(seed)
    return [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [rng.uniform(0, extent), rng.uniform(0, extent)],
            },
            "properties": {"id": f"{prefix}{i}"},
        }
        for i in range(count)
    ]


def _make_polygons(
    count: int, *, seed: int, extent: float = 1000.0, size: float = 60.0, prefix: str = "z"
) -> list[dict]:
    rng = random.Random(seed)
    polygons = []
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
        polygons.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"id": f"{prefix}{i}"},
            }
        )
    return polygons


def _make_lines(count: int, *, seed: int, extent: float = 1000.0, prefix: str = "l") -> list[dict]:
    rng = random.Random(seed)
    lines = []
    for i in range(count):
        x1, y1 = rng.uniform(0, extent), rng.uniform(0, extent)
        x2, y2 = rng.uniform(0, extent), rng.uniform(0, extent)
        lines.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[x1, y1], [x2, y2]]},
                "properties": {"id": f"{prefix}{i}"},
            }
        )
    return lines


def _force_no_index(monkeypatch):
    """
    Force find_nearest_neighbors() back onto the original per-pair
    nested loop - same technique test_spatial_join_strtree_performance.py
    uses on _get_strtree_tools, applied to _prepare_target_index here.
    """
    monkeypatch.setattr(
        nearest_neighbor_module,
        "_prepare_target_index",
        lambda target_items, final_engine: (None, None, [], []),
    )


# ------------------------------------------------------------------ #
# 1. Correctness: indexed result == brute-force reference, exactly.
# ------------------------------------------------------------------ #


def _assert_identical_results(indexed_result, naive_result):
    assert indexed_result.metadata["engines_used"] == naive_result.metadata["engines_used"]
    assert indexed_result.metadata["pair_count"] == naive_result.metadata["pair_count"]
    assert indexed_result.metadata["match_count"] == naive_result.metadata["match_count"]
    assert (
        indexed_result.metadata["matched_source_count"]
        == naive_result.metadata["matched_source_count"]
    )
    assert (
        indexed_result.metadata["unmatched_source_count"]
        == naive_result.metadata["unmatched_source_count"]
    )
    assert (
        indexed_result.metadata["failed_pair_count"] == naive_result.metadata["failed_pair_count"]
    )

    assert len(indexed_result.features) == len(naive_result.features)
    for indexed_feature, naive_feature in zip(indexed_result.features, naive_result.features):
        assert indexed_feature["geometry"] == naive_feature["geometry"]
        assert indexed_feature["properties"] == naive_feature["properties"]


def _run_indexed_and_naive(*, source_features, target_features, **kwargs):
    indexed_result = find_nearest_neighbors(
        source_features=source_features,
        target_features=target_features,
        engine="shapely",
        **kwargs,
    )
    assert indexed_result.metadata["spatial_index_used"] is True

    def _run_naive():
        with pytest.MonkeyPatch.context() as monkeypatch:
            _force_no_index(monkeypatch)
            result = find_nearest_neighbors(
                source_features=source_features,
                target_features=target_features,
                engine="shapely",
                **kwargs,
            )
            assert result.metadata["spatial_index_used"] is False
            return result

    naive_result = _run_naive()
    return indexed_result, naive_result


def test_indexed_matches_brute_force_k1_points_vs_polygons():
    sources = _make_points(150, seed=1, prefix="s")
    targets = _make_polygons(12, seed=2)

    indexed_result, naive_result = _run_indexed_and_naive(
        source_features=sources, target_features=targets, k=1
    )
    _assert_identical_results(indexed_result, naive_result)


def test_indexed_matches_brute_force_k3_points_vs_lines():
    sources = _make_points(80, seed=3, prefix="s")
    targets = _make_lines(15, seed=4)

    indexed_result, naive_result = _run_indexed_and_naive(
        source_features=sources, target_features=targets, k=3
    )
    _assert_identical_results(indexed_result, naive_result)


def test_indexed_matches_brute_force_with_max_distance_excluding_some_sources():
    # extent=1000 sources against a single tight cluster of targets in a
    # 40x40 corner - most sources end up farther than max_distance, so
    # this exercises the unmatched/dropped path on both sides.
    sources = _make_points(120, seed=5, prefix="s")
    targets = _make_polygons(5, seed=6, extent=40.0, size=10.0)

    indexed_result, naive_result = _run_indexed_and_naive(
        source_features=sources, target_features=targets, k=2, max_distance=80.0
    )

    assert naive_result.metadata["unmatched_source_count"] > 0
    _assert_identical_results(indexed_result, naive_result)


def test_indexed_matches_brute_force_with_drop_unmatched():
    sources = _make_points(60, seed=7, prefix="s")
    targets = _make_polygons(4, seed=8, extent=40.0, size=10.0)

    indexed_result, naive_result = _run_indexed_and_naive(
        source_features=sources,
        target_features=targets,
        k=1,
        max_distance=60.0,
        drop_unmatched=True,
    )

    assert naive_result.metadata["dropped_unmatched_count"] > 0
    _assert_identical_results(indexed_result, naive_result)


def test_indexed_matches_brute_force_with_missing_geometries():
    sources = _make_points(30, seed=9, prefix="s")
    sources.append({"type": "Feature", "geometry": None, "properties": {"id": "s_missing"}})
    targets = _make_polygons(6, seed=10)
    targets.append({"type": "Feature", "geometry": None, "properties": {"id": "z_missing"}})

    indexed_result, naive_result = _run_indexed_and_naive(
        source_features=sources, target_features=targets, k=1
    )
    _assert_identical_results(indexed_result, naive_result)


def test_falls_back_to_naive_loop_when_a_target_geometry_is_invalid(monkeypatch):
    sources = _make_points(20, seed=11, prefix="s")
    targets = _make_polygons(3, seed=12)
    targets.append(
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]},  # degenerate/invalid
            "properties": {"id": "broken"},
        }
    )

    result = find_nearest_neighbors(
        source_features=sources, target_features=targets, k=1, engine="shapely"
    )

    assert result.metadata["spatial_index_used"] is False
    assert result.metadata["source_feature_count"] == len(sources)


def test_python_engine_never_uses_the_index():
    sources = _make_points(20, seed=13, prefix="s")
    targets = _make_polygons(4, seed=14)

    result = find_nearest_neighbors(
        source_features=sources, target_features=targets, k=1, engine="python"
    )

    assert result.metadata["spatial_index_used"] is False
    assert result.metadata["engines_used"] == ["python"]


# ------------------------------------------------------------------ #
# 2. Performance: indexed path is substantially faster than the naive
#    nested loop, at a scale representative of the reported case study
#    (964 polygons x 1020 points).
# ------------------------------------------------------------------ #


def test_indexed_is_substantially_faster_than_the_naive_double_loop(monkeypatch):
    """
    Compares find_nearest_neighbors against itself with the spatial
    index forced off, rather than against a bare shapely loop with none
    of the plugin's own per-pair overhead - matching how the reported
    ~120s s3geo.query() call was actually measured (full plugin
    runtime). A conservative 5x threshold comfortably clears measured
    speedups at this scale while leaving headroom against timing noise.
    """
    sources = _make_points(1000, seed=15, prefix="s")
    targets = _make_polygons(1000, seed=16, size=15.0)

    start = time.perf_counter()
    indexed_result = find_nearest_neighbors(
        source_features=sources, target_features=targets, k=1, engine="shapely"
    )
    indexed_seconds = time.perf_counter() - start

    assert indexed_result.metadata["spatial_index_used"] is True

    _force_no_index(monkeypatch)

    start = time.perf_counter()
    naive_result = find_nearest_neighbors(
        source_features=sources, target_features=targets, k=1, engine="shapely"
    )
    naive_seconds = time.perf_counter() - start

    assert naive_result.metadata["spatial_index_used"] is False
    assert indexed_seconds < naive_seconds / 5
