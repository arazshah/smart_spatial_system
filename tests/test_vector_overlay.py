"""Tests for the vector_overlay plugin (clip_features, erase_features, union_features)."""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")
from shapely.geometry import shape  # noqa: E402

from plugins.vector_overlay import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    clip_features,
    erase_features,
    union_features,
)


def box(x0, y0, x1, y1, **props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
        },
    }


def area(feature):
    return shape(feature["geometry"]).area


def test_manifest():
    assert PLUGIN.manifest.id == PLUGIN_ID == "vector_overlay"


def test_clip_cuts_at_mask_and_keeps_attributes():
    out = clip_features([box(0, 0, 10, 10, name="a")], [box(5, 5, 20, 20)])
    assert len(out.features) == 1
    assert area(out.features[0]) == pytest.approx(25)
    assert out.features[0]["properties"]["name"] == "a"
    assert out.metadata["operation"] == "clip"


def test_clip_drops_features_outside_and_edge_touching_leftovers():
    features = [box(0, 0, 10, 10, name="in"), box(100, 100, 110, 110, name="out"),
                box(-10, 0, 0, 10, name="edge")]
    out = clip_features(features, [box(0, 0, 10, 10)])
    assert [f["properties"]["name"] for f in out.features] == ["in"]


def test_clip_points_by_polygon():
    points = [
        {"type": "Feature", "properties": {"id": 1}, "geometry": {"type": "Point", "coordinates": [1, 1]}},
        {"type": "Feature", "properties": {"id": 2}, "geometry": {"type": "Point", "coordinates": [50, 50]}},
    ]
    out = clip_features(points, [box(0, 0, 10, 10)])
    assert [f["properties"]["id"] for f in out.features] == [1]


def test_clip_with_empty_mask_keeps_nothing_and_erase_keeps_everything():
    assert clip_features([box(0, 0, 1, 1)], []).features == []
    assert len(erase_features([box(0, 0, 1, 1)], []).features) == 1


def test_erase_removes_covered_area():
    out = erase_features([box(0, 0, 10, 10)], [box(0, 0, 5, 10)])
    assert area(out.features[0]) == pytest.approx(50)
    assert erase_features([box(0, 0, 10, 10)], [box(-1, -1, 11, 11)]).features == []


def test_invalid_bowtie_is_repaired_by_default():
    bowtie = {
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon",
                     "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]},
    }
    out = clip_features([bowtie], [box(-1, -1, 11, 11)])
    assert out.features and all(shape(f["geometry"]).is_valid for f in out.features)


def test_union_pieces_and_attributes():
    a = [box(0, 0, 10, 10, name="A")]
    b = [box(5, 0, 15, 10, name="B")]
    out = union_features(a, b)
    by_origin = {f["properties"]["overlay_origin"]: f for f in out.features}
    assert set(by_origin) == {"source", "both", "target"}
    assert area(by_origin["both"]) == pytest.approx(50)
    assert by_origin["both"]["properties"]["source_name"] == "A"
    assert by_origin["both"]["properties"]["target_name"] == "B"
    assert "target_name" not in by_origin["source"]["properties"]
    assert sum(area(f) for f in out.features) == pytest.approx(150)


def test_union_leaves_holes_out_and_validates_input():
    ring_a = [box(0, 0, 4, 4)]
    ring_b = [box(10, 10, 14, 14)]
    assert len(union_features(ring_a, ring_b).features) == 2
    point = [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}}]
    with pytest.raises(ValueError):
        union_features(point, ring_b)
    with pytest.raises(ValueError):
        union_features(ring_a, ring_b, source_prefix="x_", target_prefix="x_")


def test_limits_are_enforced(monkeypatch):
    import plugins.vector_overlay as module

    monkeypatch.setattr(module, "load_config", lambda _id: {"max_features": 1})
    with pytest.raises(ValueError, match="exceeds"):
        clip_features([box(0, 0, 1, 1), box(2, 2, 3, 3)], [box(0, 0, 5, 5)])
