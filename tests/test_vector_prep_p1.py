"""P1 vector tools: vector_geometry_tools and attribute_prep."""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")
from shapely.geometry import shape  # noqa: E402

from plugins.attribute_prep import (  # noqa: E402
    classify_breaks,
    deduplicate_features,
    jenks_breaks,
    map_schema,
    normalize_persian_text,
    normalize_text,
)
from plugins.vector_geometry_tools import (  # noqa: E402
    explode_multipart,
    simplify_geometries,
    voronoi_thiessen,
)


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def row(**props):
    return {"type": "Feature", "properties": props, "geometry": None}


# ------------------------------------------------------------------ geometry tools
def test_simplify_reduces_vertices_and_keeps_validity():
    wiggly = {"type": "Feature", "properties": {"n": 1}, "geometry": {
        "type": "LineString", "coordinates": [[i, 0.01 * (i % 2)] for i in range(100)]}}
    out = simplify_geometries([wiggly], 0.1)
    assert out.metadata["vertices_before"] == 100 and out.metadata["vertices_after"] < 10
    ring = {"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [10, 0.001], [20, 0], [20, 20], [0, 20], [0, 0]]]}}
    simplified = simplify_geometries([ring], 0.5)
    assert shape(simplified.features[0]["geometry"]).is_valid
    for bad in (0, -1, "x"):
        with pytest.raises(ValueError):
            simplify_geometries([ring], bad)


def test_explode_multipart_and_collections():
    multi = {"type": "Feature", "properties": {"id": 7}, "geometry": {
        "type": "MultiPoint", "coordinates": [[0, 0], [1, 1], [2, 2]]}}
    collection = {"type": "Feature", "properties": {"id": 8}, "geometry": {
        "type": "GeometryCollection", "geometries": [
            {"type": "Point", "coordinates": [5, 5]},
            {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}]}}
    out = explode_multipart([multi, collection, row(id=9)])
    assert [f["geometry"]["type"] for f in out.features] == ["Point"] * 3 + ["Point", "LineString", "LineString"]
    assert [f["properties"]["part_index"] for f in out.features] == [0, 1, 2, 0, 1, 2]
    assert {f["properties"]["id"] for f in out.features} == {7, 8}  # the null-geometry row is dropped


def test_voronoi_cells_partition_the_clip_area():
    stations = [pt(0, 0, name="a"), pt(10, 0, name="b"), pt(0, 10, name="c"), pt(10, 10, name="d")]
    out = voronoi_thiessen(stations, bbox=[-5, -5, 15, 15])
    assert len(out.features) == 4
    assert sum(f["properties"]["cell_area"] for f in out.features) == pytest.approx(400)
    for feature in out.features:  # every cell contains its own point
        name = feature["properties"]["name"]
        owner = next(s for s in stations if s["properties"]["name"] == name)
        assert shape(feature["geometry"]).contains(shape(owner["geometry"]))
    square = {"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}}
    clipped = voronoi_thiessen(stations, clip_to=[square])
    assert sum(f["properties"]["cell_area"] for f in clipped.features) == pytest.approx(100)


def test_voronoi_validation():
    with pytest.raises(ValueError, match="at least two"):
        voronoi_thiessen([pt(0, 0), pt(0, 0)])
    with pytest.raises(ValueError):
        voronoi_thiessen([pt(0, 0), pt(1, 1)], bbox=[1, 2, 3])
    out = voronoi_thiessen([pt(0, 0), pt(1, 1), pt(1, 1)])
    assert out.metadata["duplicate_points_skipped"] == 1


# ------------------------------------------------------------------- deduplicate
def test_deduplicate_by_properties_geometry_and_both():
    data = [pt(0, 0, a=1), pt(5, 5, a=1), pt(0, 0, a=2), pt(0, 0, a=1)]
    assert len(deduplicate_features(data, "properties").features) == 2
    assert len(deduplicate_features(data, "geometry").features) == 2
    both = deduplicate_features(data, "both")
    assert len(both.features) == 3 and both.metadata["duplicates_removed"] == 1
    assert len(deduplicate_features(data, "properties", fields=["a"]).features) == 2
    last = deduplicate_features([pt(0, 0, a=1, v="old"), pt(0, 0, a=1, v="new")], "geometry", keep="last")
    assert last.features[0]["properties"]["v"] == "new"
    flagged = deduplicate_features(data, "both", mark_only=True)
    assert [f["properties"]["is_duplicate"] for f in flagged.features] == [False, False, False, True]


def test_deduplicate_geometry_is_normalized():
    a = {"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}
    b = {"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[1, 1], [0, 1], [0, 0], [1, 0], [1, 1]]]}}  # same square
    assert len(deduplicate_features([a, b], "geometry").features) == 1
    with pytest.raises(ValueError):
        deduplicate_features([a], "everything")


# -------------------------------------------------------------------- map_schema
def test_map_schema_rename_cast_default_drop_keep():
    data = [pt(0, 0, old="۱۲", price="1,500.5", ok="بله", junk=1, note=None),
            pt(1, 1, old="x", price="n/a", ok="no", junk=2, note="hi")]
    out = map_schema(data, mapping={"old": "count"}, cast={"count": "int", "price": "float", "ok": "bool"},
                     defaults={"note": "-"}, drop=["junk"])
    first, second = (f["properties"] for f in out.features)
    assert first == {"count": 12, "price": 1500.5, "ok": True, "note": "-"}
    assert second == {"count": None, "price": None, "ok": False, "note": "hi"}
    assert out.metadata["cast_errors"] == 2
    only = map_schema(data, keep_only=["ok", "missing"])
    assert only.features[0]["properties"] == {"ok": "بله", "missing": None}
    with pytest.raises(ValueError, match="Feature 1"):
        map_schema(data, cast={"old": "int"}, on_error="raise")


def test_map_schema_validation():
    data = [pt(0, 0, a=1, b=2)]
    with pytest.raises(ValueError, match="already exists"):
        map_schema(data, mapping={"a": "b"})
    with pytest.raises(ValueError, match="distinct"):
        map_schema(data, mapping={"a": "x", "b": "x"})
    with pytest.raises(ValueError, match="cast"):
        map_schema(data, cast={"a": "decimal"})
    assert map_schema(data, cast={"a": "int"}).features[0]["properties"]["a"] == 1


# ------------------------------------------------------------ Persian normalization
def test_normalize_text_pieces():
    kwargs = dict(letters=True, digits="keep", diacritics=True, spaces=True, zero_width=True)
    assert normalize_text("علي  كريم", **kwargs) == "علی کریم"
    assert normalize_text("کتابٌ", **kwargs) == "کتاب"
    assert normalize_text("می‌روم", **kwargs) == "می‌روم"  # ZWNJ is kept
    assert normalize_text("می ‌ روم", **kwargs) == "می‌روم"
    assert normalize_text("a​b﻿", **kwargs) == "ab"
    assert normalize_text("پلاک 123", **{**kwargs, "digits": "persian"}) == "پلاک ۱۲۳"
    assert normalize_text("پلاک ۱۲۳٤", **{**kwargs, "digits": "english"}) == "پلاک 1234"


def test_normalize_persian_text_feature_level():
    data = [pt(0, 0, name=" بيمارستان   امام ", code=12, other="ك")]
    out = normalize_persian_text(data, fields=["name"])
    assert out.features[0]["properties"]["name"] == "بیمارستان امام"
    assert out.features[0]["properties"]["other"] == "ك"  # not selected
    assert out.metadata["values_changed"] == 1
    kept = normalize_persian_text(data, suffix="_norm")
    props = kept.features[0]["properties"]
    assert props["name"] == " بيمارستان   امام " and props["name_norm"] == "بیمارستان امام"
    assert props["other_norm"] == "ک" and props["code"] == 12
    with pytest.raises(ValueError):
        normalize_persian_text(data, digits="roman")


# ------------------------------------------------------------------ classification
def test_jenks_finds_natural_clusters():
    values = [1, 2, 3, 10, 11, 12, 50, 51, 52]
    assert jenks_breaks(values, 3, 1000) == [1, 3, 12, 52]
    assert jenks_breaks([5, 5, 5], 3, 1000) == [5]  # fewer distinct values than classes
    big = list(range(5000))
    assert len(jenks_breaks(big, 4, 1000)) == 5  # reduced input still gives classes+1 edges


def table(values, field="v"):
    return [row(**{field: v}) for v in values]


@pytest.mark.parametrize("method, expected_edges", [
    ("equal_interval", [0, 25, 50, 75, 100]),
    ("quantile", [0, 25, 50, 75, 100]),
])
def test_classification_methods(method, expected_edges):
    out = classify_breaks(table(range(101)), "v", method, 4)
    assert out.metadata["breaks"] == pytest.approx(expected_edges)
    classes = [f["properties"]["class"] for f in out.features]
    assert classes[0] == 1 and classes[-1] == 4 and set(classes) == {1, 2, 3, 4}
    assert sum(out.metadata["class_counts"].values()) == 101


def test_natural_breaks_manual_std_dev_and_nulls():
    values = [1, 2, 3, 10, 11, 12, 50, 51, 52]
    natural = classify_breaks(table(values + ["n/a"]), "v", "natural_breaks", 3)
    assert natural.metadata["breaks"] == [1, 3, 12, 52] and natural.metadata["unclassified"] == 1
    assert natural.features[-1]["properties"]["class"] is None
    assert [f["properties"]["class"] for f in natural.features[:9]] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert natural.features[0]["properties"]["class_label"] == "1 – 3"
    manual = classify_breaks(table([5, 15, 25, 500]), "v", "manual", breaks=[0, 10, 20, 30])
    assert [f["properties"]["class"] for f in manual.features] == [1, 2, 3, None]
    std = classify_breaks(table(values), "v", "std_dev")
    assert std.metadata["breaks"][0] == 1 and std.metadata["breaks"][-1] == 52
    constant = classify_breaks(table([4, 4, 4]), "v", "quantile", 3)
    assert {f["properties"]["class"] for f in constant.features} == {1}


@pytest.mark.parametrize("kwargs", [
    {"field": "v", "method": "kmeans"}, {"field": "v", "classes": 1}, {"field": "v", "classes": 13},
    {"field": "v", "method": "manual"}, {"field": "v", "method": "manual", "breaks": [3, 2, 1]},
    {"field": "missing"}, {"field": ""},
])
def test_classification_validation(kwargs):
    with pytest.raises(ValueError):
        classify_breaks(table([1, 2, 3]), **kwargs)
