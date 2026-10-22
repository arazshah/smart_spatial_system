"""Tests for grid_generator, field_calculator and vector_aggregation."""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")
from shapely.geometry import shape  # noqa: E402

from plugins.field_calculator import (  # noqa: E402
    ExpressionError,
    calculate_field,
    compile_expression,
)
from plugins.grid_generator import generate_grid  # noqa: E402
from plugins.vector_aggregation import aggregate_points_to_polygons, aggregate_table  # noqa: E402


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def box(x0, y0, x1, y1, **props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Polygon",
                     "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
    }


# ----------------------------------------------------------------------- grid
def test_square_grid_over_bbox():
    out = generate_grid(10, bbox=[0, 0, 25, 20])
    assert len(out.features) == 6  # 3 cols x 2 rows
    assert out.features[0]["properties"] == {"cell_id": "0_0", "row": 0, "col": 0, "cell_area": 100.0}
    assert all(shape(f["geometry"]).area == pytest.approx(100) for f in out.features)


def test_hex_grid_cells_are_regular_and_equal_area():
    out = generate_grid(10, bbox=[0, 0, 50, 50], grid_type="hex")
    areas = {round(shape(f["geometry"]).area, 6) for f in out.features}
    assert len(areas) == 1
    assert areas.pop() == pytest.approx(86.602540, abs=1e-4)  # sqrt(3)/2 * 10^2
    assert out.metadata["grid_type"] == "hex"


def test_grid_from_features_and_clip():
    triangle = {
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [20, 0], [0, 20], [0, 0]]]},
    }
    full = generate_grid(5, features=[triangle])
    clipped = generate_grid(5, features=[triangle], clip_to_features=True)
    assert len(full.features) == 16
    assert 0 < len(clipped.features) < len(full.features)
    assert clipped.metadata["dropped_cells"] == len(full.features) - len(clipped.features)


def test_grid_rejects_bad_input_and_enforces_cell_limit():
    with pytest.raises(ValueError):
        generate_grid(0, bbox=[0, 0, 1, 1])
    with pytest.raises(ValueError):
        generate_grid(1)  # no extent
    with pytest.raises(ValueError):
        generate_grid(1, bbox=[0, 0, 0, 5])
    with pytest.raises(ValueError):
        generate_grid(1, bbox=[0, 0, 1000, 1000])  # 1e6 cells > default limit 1e5
    with pytest.raises(ValueError):
        generate_grid(1e-9, bbox=[0, 0, 10, 10], max_cells=10)
    with pytest.raises(ValueError, match="grid_type"):
        generate_grid(1, bbox=[0, 0, 5, 5], grid_type="triangle")


# ------------------------------------------------------------ field calculator
def run(expression, features=None, **kwargs):
    features = features or [pt(0, 0, a=4, b=2, name="Tehran")]
    return [f["properties"] for f in calculate_field(features, "out", expression, **kwargs).features]


def test_arithmetic_functions_and_conditionals():
    assert run("a / b")[0]["out"] == 2
    assert run("round(a / 3, 2)")[0]["out"] == 1.33
    assert run("a * 2 if a > 3 else 0")[0]["out"] == 8
    assert run("upper(name) + '!'")[0]["out"] == "TEHRAN!"
    assert run("max(a, b) - min(a, b)")[0]["out"] == 2
    assert run("name in ['Tehran', 'Qom']")[0]["out"] is True
    assert run("prop('a') ** 2")[0]["out"] == 16


def test_geometry_measures():
    features = [box(0, 0, 10, 5, pop=500)]
    assert run("geom_area", features)[0]["out"] == 50
    assert run("pop / geom_area", features)[0]["out"] == 10
    assert run("geom_x", features)[0]["out"] == 5


def test_nulls_and_errors():
    features = [pt(0, 0, a=1), pt(0, 0, a=None), pt(0, 0, a=0, b=0)]
    out = run("a / b", features)
    assert [p["out"] for p in out] == [None, None, None]
    assert run("coalesce(b, -1)", features)[0]["out"] == -1
    assert run("is_null(a)", features)[1]["out"] is True
    with pytest.raises(ValueError, match="Feature"):
        run("a / b", features, on_error="raise")
    assert calculate_field(features, "out", "a / b").metadata["error_count"] == 1  # only a=0,b=0


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "().__class__",
        "a.__class__",
        "[x for x in range(5)]",
        "lambda: 1",
        "a[0]",
        "a is None",
        "unknown_field + 1",
        "(a := 3)",
        "print(a)",
        "round(a, n=2)",
        "a if" ,
        "",
        "1+" * 150 + "1",
    ],
)
def test_dangerous_or_invalid_expressions_are_rejected(expression):
    with pytest.raises(ExpressionError):
        compile_expression(expression, {"a", "b"})
    with pytest.raises(ValueError):
        run(expression)


def test_resource_exhaustion_guards():
    assert run("9 ** 9 ** 9")[0]["out"] is None  # exponent guard -> null, not a hang
    assert run("name * 100000")[0]["out"] is None
    assert run("10 ** 400")[0]["out"] is None
    with pytest.raises(ValueError):
        run("x", [pt(0, 0, a=1)])  # unknown field


def test_overwrite_flag_and_geometry_is_kept():
    features = [pt(1, 2, out=5)]
    with pytest.raises(ValueError, match="overwrite"):
        calculate_field(features, "out", "1", overwrite=False)
    result = calculate_field(features, "out", "out + 1")
    assert result.features[0]["properties"]["out"] == 6
    assert result.features[0]["geometry"]["coordinates"] == [1, 2]


# ----------------------------------------------------------------- aggregation
def test_points_to_polygons_counts_sums_and_density():
    polygons = [box(0, 0, 10, 10, name="A"), box(10, 0, 20, 10, name="B"), box(50, 50, 60, 60, name="C")]
    points = [pt(1, 1, v=10), pt(2, 2, v=30), pt(15, 5, v=7), pt(10, 5, v=100), pt(99, 99, v=1)]
    out = aggregate_points_to_polygons(points, polygons, value_field="v", density=True)
    by_name = {f["properties"]["name"]: f["properties"] for f in out.features}
    assert by_name["A"]["point_count"] == 3  # includes the point on the shared edge
    assert by_name["B"]["point_count"] == 2
    assert by_name["C"]["point_count"] == 0 and by_name["C"]["v_sum"] is None
    assert by_name["A"]["v_sum"] == 140 and by_name["A"]["v_mean"] == pytest.approx(140 / 3)
    assert by_name["A"]["point_count_density"] == pytest.approx(3 / 100)


def test_points_to_polygons_validation():
    polygons = [box(0, 0, 1, 1)]
    with pytest.raises(ValueError, match="value_field"):
        aggregate_points_to_polygons([pt(0, 0)], polygons, stats=["sum"])
    with pytest.raises(ValueError, match="stats"):
        aggregate_points_to_polygons([pt(0, 0, v=1)], polygons, value_field="v", stats=["median"])
    with pytest.raises(ValueError, match="Polygon"):
        aggregate_points_to_polygons([pt(0, 0)], [pt(1, 1)])


def test_aggregate_table_group_sort_limit():
    rows = [pt(0, 0, d="x", p=10), pt(0, 0, d="x", p=30), pt(0, 0, d="y", p=5),
            pt(0, 0, d="y", p=None), pt(0, 0, d="z", p="oops")]
    out = aggregate_table(rows, {"p": ["sum", "mean", "count"]}, group_by="d", sort_by="p_sum")
    tables = [f["properties"] for f in out.features]
    assert [t["d"] for t in tables] == ["x", "y", "z"]  # z has no numeric value -> null sum last
    assert tables[0]["p_sum"] == 40 and tables[0]["p_mean"] == 20 and tables[0]["count"] == 2
    assert tables[1]["p_count"] == 2
    assert all(f["geometry"] is None for f in out.features)
    assert len(aggregate_table(rows, {"p": "sum"}, group_by="d", sort_by="p_sum", limit=1).features) == 1
    total = aggregate_table(rows, {"p": "sum"})
    assert total.features[0]["properties"]["p_sum"] == 45


def test_aggregate_table_validation():
    with pytest.raises(ValueError):
        aggregate_table([pt(0, 0, a=1)], {})
    with pytest.raises(ValueError):
        aggregate_table([pt(0, 0, a=1)], {"a": "variance"})
    with pytest.raises(ValueError, match="sort_by"):
        aggregate_table([pt(0, 0, a=1)], {"a": "sum"}, sort_by="nope")
    with pytest.raises(ValueError):
        aggregate_table([pt(0, 0, a=1)], {"a": "sum"}, limit=0)
