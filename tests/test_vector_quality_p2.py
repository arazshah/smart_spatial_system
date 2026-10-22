"""convex hull, topology check, sampling and outlier detection (the last four capabilities)."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("shapely")
from shapely.geometry import shape  # noqa: E402

import plugins.vector_geometry_tools as geometry_module  # noqa: E402
from plugins.attribute_prep import detect_outliers, sample_features  # noqa: E402
from plugins.vector_geometry_tools import calculate_convex_hull, check_topology  # noqa: E402


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def poly(coords, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [coords]}}


def box(x0, y0, x1, y1, **props):
    return poly([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]], **props)


def row(**props):
    return {"type": "Feature", "properties": props, "geometry": None}


# ------------------------------------------------------------------ convex hull
def test_hull_envelope_and_rotated_rectangle():
    points = [pt(0, 0), pt(4, 0), pt(4, 4), pt(0, 4), pt(2, 2), pt(1, 3)]
    hull = calculate_convex_hull(points)
    assert shape(hull.features[0]["geometry"]).area == pytest.approx(16) and hull.features[0]["properties"]["feature_count"] == 6
    diamond = [pt(0, 2), pt(2, 0), pt(4, 2), pt(2, 4)]
    assert shape(calculate_convex_hull(diamond, "envelope").features[0]["geometry"]).area == pytest.approx(16)
    assert shape(calculate_convex_hull(diamond, "min_rotated_rectangle").features[0]["geometry"]).area == pytest.approx(8)
    buffered = calculate_convex_hull(points, buffer=1)
    assert shape(buffered.features[0]["geometry"]).area > 16 + 4 * 4 - 1


def test_hull_groups_and_validation():
    data = [pt(0, 0, g="a"), pt(1, 0, g="a"), pt(0, 1, g="a"), pt(10, 10, g="b"), pt(12, 10, g="b"),
            pt(10, 12, g="b"), row(g="c")]
    out = calculate_convex_hull(data, group_by="g")
    areas = {f["properties"]["g"]: f["properties"]["shape_area"] for f in out.features}
    assert areas == {"a": pytest.approx(0.5), "b": pytest.approx(2.0)}
    for kwargs in ({"shape": "alpha"}, {"buffer": -1}, {"group_by": ""}):
        with pytest.raises(ValueError):
            calculate_convex_hull(data, **kwargs)
    with pytest.raises(ValueError, match="usable geometry"):
        calculate_convex_hull([row(a=1)])


# --------------------------------------------------------------------- topology
def test_topology_reports_each_issue_type_with_locations():
    bowtie = poly([[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]])
    layer = [
        box(0, 0, 10, 10, id="A"),
        box(5, 5, 15, 15, id="B"),             # overlaps A (25 sq units)
        box(0, 0, 10, 10, id="C"),             # same geometry as A
        box(100, 100, 100.05, 120, id="D"),    # thin sliver
        box(200, 200, 200.5, 200.5, id="E"),   # tiny
        bowtie,                                # invalid
        row(id="F"),                           # no geometry
    ]
    out = check_topology(layer, min_area=0.5)
    by_kind: dict[str, list] = {}
    for feature in out.features:
        by_kind.setdefault(feature["properties"]["issue"], []).append(feature["properties"])
    assert set(by_kind) == {"invalid", "empty", "duplicate", "overlap", "sliver", "small"}
    assert by_kind["duplicate"][0]["feature_index"] == 2 and by_kind["duplicate"][0]["other_index"] == 0
    assert {(p["feature_index"], p["other_index"]) for p in by_kind["overlap"]} >= {(0, 1), (1, 2)}
    assert any(p["area"] == pytest.approx(25) for p in by_kind["overlap"])
    assert by_kind["sliver"][0]["feature_index"] == 3 and by_kind["small"][0]["feature_index"] == 4
    assert by_kind["invalid"][0]["feature_index"] == 5 and "Self-intersection" in by_kind["invalid"][0]["detail"]
    assert out.metadata["clean"] is False and out.metadata["issue_total"] == sum(out.metadata["issue_counts"].values())
    located = [f for f in out.features if f["geometry"]]
    assert located and all(f["geometry"]["type"] == "Point" for f in located)


def test_topology_clean_layers_subsets_tolerance_and_cap():
    clean = [box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1)]  # touching edges are not overlaps
    out = check_topology(clean)
    assert out.features == [] and out.metadata["clean"] is True and out.metadata["checked_features"] == 3
    overlapping = [box(0, 0, 10, 10), box(9.9, 0, 20, 10)]
    assert check_topology(overlapping, ["overlap"], overlap_tolerance=5).features == []
    assert len(check_topology(overlapping, ["overlap"]).features) == 1
    assert check_topology(overlapping, ["duplicate"]).features == []
    many = [box(0, 0, 10, 10)] * 6
    capped = check_topology(many, ["duplicate"], max_issues=2)
    assert len(capped.features) == 2 and capped.metadata["truncated"] is True
    assert capped.metadata["issue_counts"]["duplicate"] == 5
    for kwargs in ({"checks": ["gaps"]}, {"min_area": -1}, {"max_issues": 0}, {"max_issues": 10**6}):
        with pytest.raises(ValueError):
            check_topology(clean, **kwargs)


def test_topology_is_bounded(monkeypatch):
    monkeypatch.setattr(geometry_module, "load_config", lambda _id: {"max_features": 2})
    with pytest.raises(ValueError, match="feature count"):
        check_topology([box(0, 0, 1, 1)] * 3)


# ------------------------------------------------------------------- sampling
def population(n=100):
    return [row(i=i, g="a" if i < 80 else "b") for i in range(n)]


def test_random_sample_is_reproducible_ordered_and_sized():
    first = sample_features(population(), size=10, seed=7)
    again = sample_features(population(), size=10, seed=7)
    other = sample_features(population(), size=10, seed=8)
    ids = [f["properties"]["i"] for f in first.features]
    assert ids == [f["properties"]["i"] for f in again.features] and ids != [f["properties"]["i"] for f in other.features]
    assert len(ids) == 10 and ids == sorted(ids) and len(set(ids)) == 10
    assert first.metadata["population_size"] == 100 and first.metadata["sample_size"] == 10
    assert len(sample_features(population(), fraction=0.25).features) == 25
    assert len(sample_features(population(10), size=50).features) == 10  # capped at the population


def test_stratified_and_systematic_sampling():
    stratified = sample_features(population(), size=10, stratify_by="g", seed=1)
    assert stratified.metadata["stratum_counts"] == {"'a'": 8, "'b'": 2}
    equal = sample_features(population(), size=3, stratify_by="g", per_stratum=True, seed=1)
    assert equal.metadata["stratum_counts"] == {"'a'": 3, "'b'": 3}
    rare = [row(g="common")] * 50 + [row(g="rare")]
    assert "'rare'" in sample_features(rare, size=5, stratify_by="g").metadata["stratum_counts"]
    assert sample_features(rare, size=5, stratify_by="g").metadata["stratum_counts"]["'rare'"] >= 1
    systematic = sample_features(population(), size=10, method="systematic", seed=3)
    gaps = {b - a for a, b in zip((f["properties"]["i"] for f in systematic.features),
                                  (f["properties"]["i"] for f in systematic.features[1:]), strict=False)}
    assert len(systematic.features) == 10 and gaps <= {9, 10, 11}


@pytest.mark.parametrize("kwargs", [
    {}, {"size": 5, "fraction": 0.5}, {"size": 0}, {"fraction": 0}, {"fraction": 1.5}, {"size": 5, "method": "cluster"},
    {"size": 5, "per_stratum": True},
])
def test_sampling_validation(kwargs):
    with pytest.raises(ValueError):
        sample_features(population(), **kwargs)
    with pytest.raises(ValueError, match="no features"):
        sample_features([], size=1)


# -------------------------------------------------------------------- outliers
def test_iqr_flags_values_beyond_the_fences():
    data = [row(v=v) for v in [10, 11, 12, 11, 10, 13, 12, 11, 100, -50]]
    out = detect_outliers(data, "v")
    flags = [f["properties"]["is_outlier"] for f in out.features]
    assert flags == [False] * 8 + [True, True]
    stats = out.metadata["statistics"]["all"]
    assert stats["lower_fence"] < 10 and stats["upper_fence"] < 100 and out.metadata["outlier_count"] == 2
    assert out.features[8]["properties"]["outlier_score"] > 0 and out.features[0]["properties"]["outlier_score"] == 0


def test_zscore_modified_zscore_groups_and_missing_values():
    base = [row(v=v) for v in [50, 51, 49, 50, 52, 48, 50, 51, 49, 50, 500]]
    assert detect_outliers(base, "v", "modified_zscore").features[-1]["properties"]["is_outlier"] is True
    z = detect_outliers(base, "v", "zscore", threshold=2.5)
    assert [f["properties"]["is_outlier"] for f in z.features].count(True) == 1
    assert z.features[-1]["properties"]["outlier_score"] > 2.5
    grouped = [row(v=v, g="low") for v in [1, 2, 1, 2, 1, 2, 1, 2]] + [row(v=v, g="high") for v in [100, 101, 100, 101, 100, 101, 100, 130]]
    out = detect_outliers(grouped, "v", group_by="g")
    assert [f["properties"]["is_outlier"] for f in out.features].count(True) == 1
    assert out.features[-1]["properties"]["is_outlier"] is True  # 130 only stands out inside its own group
    mixed = [row(v=1), row(v="x"), row(v=None), row(v=2), row(v=3)]
    flagged = detect_outliers(mixed, "v")
    assert flagged.features[1]["properties"]["is_outlier"] is None and flagged.metadata["unscored"] == 2


def test_outlier_edge_cases_and_validation():
    constant = detect_outliers([row(v=5)] * 6, "v", "zscore")
    assert not any(f["properties"]["is_outlier"] for f in constant.features)
    robust = detect_outliers([row(v=5)] * 6 + [row(v=9)], "v", "modified_zscore")
    assert robust.features[-1]["properties"]["is_outlier"] is True  # MAD = 0 but the value differs
    assert math.isfinite(robust.features[-1]["properties"]["outlier_score"])
    for kwargs in ({"method": "dbscan"}, {"threshold": 0}, {"field": ""}, {"field": "missing"}):
        with pytest.raises(ValueError):
            detect_outliers([row(v=1), row(v=2)], **{"field": "v", **kwargs})
