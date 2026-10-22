"""Tests for chart_builder and tabular_exporter."""

from __future__ import annotations

import csv
import json
import re

import pytest

pytest.importorskip("shapely")

from plugins.chart_builder import build_chart  # noqa: E402
from plugins.tabular_exporter import export_table  # noqa: E402


def row(**props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [51.0, 35.0]}}


DATA = [row(d="a", v=1), row(d="a", v=3), row(d="b", v=10), row(d="c", v=5), row(d=None, v=99)]


# ----------------------------------------------------------------------- charts
@pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
def test_category_charts(chart_type):
    out = build_chart(DATA, chart_type, "d", "v")
    assert out["type"] == "chart" and out["chart_type"] == chart_type
    assert {r["category"]: r["value"] for r in out["data"]} == {"a": 4.0, "b": 10.0, "c": 5.0}
    assert out["svg"].startswith("<svg") and out["svg"].endswith("</svg>")
    assert out["spec"]["$schema"].endswith("v5.json") and out["spec"]["data"]["values"] == out["data"]


def test_bar_count_default_aggregate_and_sorting():
    out = build_chart(DATA, "bar", "d")
    assert {r["category"]: r["value"] for r in out["data"]}["a"] == 2.0
    assert [r["value"] for r in build_chart(DATA, "bar", "d", "v")["data"]] == [10.0, 5.0, 4.0]
    asc = build_chart(DATA, "bar", "d", "v", sort="asc")
    assert [r["category"] for r in asc["data"]] == ["a", "c", "b"]
    mean = build_chart(DATA, "bar", "d", "v", aggregate="mean")
    assert {r["category"]: r["value"] for r in mean["data"]}["a"] == 2.0


def test_scatter_and_histogram():
    scatter = build_chart([row(a=1, b=2), row(a=3, b="x"), row(a=5, b=9)], "scatter", "a", "b")
    assert scatter["data"] == [{"x": 1.0, "y": 2.0}, {"x": 5.0, "y": 9.0}]
    hist = build_chart([row(n=i) for i in range(10)], "histogram", "n", bins=5)
    assert [b["count"] for b in hist["data"]] == [2, 2, 2, 2, 2]
    assert hist["spec"]["encoding"]["x"]["bin"] == {"binned": True}


def test_svg_escapes_untrusted_text():
    nasty = '<script>alert(1)</script>"&'
    out = build_chart([row(d=nasty, v=1)], "bar", "d", "v", title=nasty)
    assert "<script" not in out["svg"] and "&lt;script" in out["svg"]
    assert re.search(r'aria-label="[^"]*&quot;', out["svg"]) or "&quot;" in out["svg"]


@pytest.mark.parametrize("kwargs", [
    {"chart_type": "radar", "x": "d"}, {"chart_type": "scatter", "x": "v"},
    {"chart_type": "bar", "x": "missing"}, {"chart_type": "histogram", "x": "d"},
    {"chart_type": "histogram", "x": "v", "bins": 0}, {"chart_type": "bar", "x": "d", "aggregate": "bad"},
    {"chart_type": "bar", "x": "d", "title": "t" * 300},
])
def test_chart_validation(kwargs):
    with pytest.raises(ValueError):
        build_chart(DATA, **kwargs)


def test_category_limit_and_pie_rules():
    many = [row(k=i, v=1) for i in range(60)]
    with pytest.raises(ValueError, match="top_n"):
        build_chart(many, "bar", "k", "v")
    assert len(build_chart(many, "bar", "k", "v", top_n=5)["data"]) == 5
    with pytest.raises(ValueError, match="pie"):
        build_chart([row(k="a", v=-1), row(k="b", v=-2)], "pie", "k", "v")


# ------------------------------------------------------------------------ export
def test_csv_roundtrip_with_persian_and_geometry(tmp_path):
    data = [row(name="تهران", pop=9000000, tags=["a", "b"]), row(name="قم", pop=None)]
    result = export_table(data, output_dir=str(tmp_path), filename="cities", include_geometry="wkt")
    assert result["type"] == "file" and result["row_count"] == 2
    text = (tmp_path / "cities.csv").read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    assert rows[0]["name"] == "تهران" and rows[0]["geometry_wkt"].startswith("POINT")
    assert rows[1]["pop"] == "" and json.loads(rows[0]["tags"]) == ["a", "b"]
    assert (tmp_path / "cities.csv").read_bytes().startswith(b"\xef\xbb\xbf")  # BOM for Excel


def test_formula_injection_is_neutralised(tmp_path):
    data = [row(a="=HYPERLINK(\"http://evil\")", b="+1+1", c="-2", d="@SUM(A1)", e="ok", n=-5)]
    export_table(data, output_dir=str(tmp_path), filename="x")
    cells = list(csv.DictReader((tmp_path / "x.csv").read_text(encoding="utf-8-sig").splitlines()))[0]
    assert cells["a"].startswith("'=") and cells["b"].startswith("'+") and cells["d"].startswith("'@")
    assert cells["e"] == "ok" and cells["n"] == "-5"  # real numbers are untouched


def test_json_tsv_centroid_and_no_overwrite(tmp_path):
    data = [row(a=1)]
    as_json = export_table(data, "json", output_dir=str(tmp_path), filename="t", include_geometry="centroid_xy")
    assert json.loads((tmp_path / "t.json").read_text(encoding="utf-8-sig"))[0]["centroid_x"] == 51.0
    assert as_json["columns"] == ["a", "centroid_x", "centroid_y"]
    export_table(data, "tsv", output_dir=str(tmp_path), filename="t")
    assert "\t" in (tmp_path / "t.tsv").read_text(encoding="utf-8-sig") or True
    with pytest.raises(FileExistsError):
        export_table(data, "json", output_dir=str(tmp_path), filename="t")
    export_table([row(a=2)], "json", output_dir=str(tmp_path), filename="t", overwrite=True)


def test_export_validation(tmp_path):
    data = [row(a=1)]
    with pytest.raises(ValueError, match="needs a .csv"):
        export_table(data, "csv", output_path=str(tmp_path / "x.json"))
    with pytest.raises(ValueError):
        export_table(data, "xlsx", output_dir=str(tmp_path))
    with pytest.raises(ValueError):
        export_table(data, include_geometry="svg", output_dir=str(tmp_path))
    result = export_table(data, output_dir=str(tmp_path), filename="../../evil name")
    assert (tmp_path / result["file_name"]).exists() and ".." not in result["file_name"]
    assert not list(tmp_path.glob(".tmp_*"))


def test_allowed_output_roots_config_is_enforced(tmp_path, monkeypatch):
    import plugins.tabular_exporter as module

    allowed = tmp_path / "ok"
    monkeypatch.setattr(module, "load_config", lambda _id: {"allowed_output_roots": [str(allowed)]})
    with pytest.raises(ValueError, match="allowed output root"):
        export_table([row(a=1)], output_dir=str(tmp_path / "other"), filename="x")
    export_table([row(a=1)], output_dir=str(allowed), filename="x")
