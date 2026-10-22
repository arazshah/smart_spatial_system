"""static_map_renderer and geo_exporter."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

pytest.importorskip("shapely")
np = pytest.importorskip("numpy")

import plugins.geo_exporter as exporter  # noqa: E402
import plugins.static_map_renderer as renderer  # noqa: E402
from plugins.geo_exporter import export_raster_geotiff, export_vector_file  # noqa: E402
from plugins.static_map_renderer import render_static_map  # noqa: E402


def square(x, y, size=0.01, **props):
    return {"type": "Feature", "properties": props, "geometry": {
        "type": "Polygon", "coordinates": [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]}}


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def line(coords, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": coords}}


def parse(svg):
    return ET.fromstring(svg)  # raises if the SVG is not well-formed XML


# ------------------------------------------------------------------------- map
def test_map_is_well_formed_svg_with_all_geometry_kinds():
    data = [square(51.3, 35.6, v=1), pt(51.35, 35.65, v=2), line([[51.3, 35.6], [51.4, 35.7]], v=3)]
    out = render_static_map(data, title="Tehran", width=400, height=300)
    root = parse(out["svg"])
    assert root.tag.endswith("svg") and out["projection"] == "web_mercator"
    tags = {el.tag.split("}")[1] for el in root.iter()}
    assert {"path", "circle", "polyline", "text", "rect"} <= tags
    assert out["bbox"] == [51.3, 35.6, 51.4, 35.7] and out["layers"] == [{"name": "main", "feature_count": 3}]
    assert (out["width"], out["height"]) == (400, 300)


def test_numeric_and_categorical_colouring_with_legend():
    cells = [square(51 + i * 0.02, 35, v=float(i), kind="a" if i % 2 else "b") for i in range(10)]
    numeric = render_static_map(cells, color_field="v", classes=4, palette="reds")
    assert len(numeric["legend"]) == 4 and numeric["legend"][0]["label"].startswith("0 –")
    fills = {el.get("fill") for el in parse(numeric["svg"]).iter() if el.tag.endswith("path")}
    assert len(fills) == 4
    categorical = render_static_map(cells, color_field="kind")
    assert {item["label"] for item in categorical["legend"]} == {"a", "b"}
    assert len({i["color"] for i in categorical["legend"]}) == 2
    assert "legend" not in parse(render_static_map(cells, color_field="v", legend=False)["svg"]).attrib
    no_legend = render_static_map(cells, color_field="v", legend=False)
    assert len([el for el in parse(no_legend["svg"]).iter() if el.tag.endswith("rect")]) == 1  # background only


def test_planar_projection_overlays_and_holes():
    projected = [square(500000, 3950000, 1000), square(502000, 3950000, 1000)]
    out = render_static_map(projected, overlays=[[line([[499000, 3949000], [504000, 3952000]])]])
    assert out["projection"] == "planar" and [layer["name"] for layer in out["layers"]] == ["overlay_1", "main"]
    donut = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]]]}}
    path = next(el for el in parse(render_static_map([donut])["svg"]).iter() if el.tag.endswith("path"))
    assert path.get("d").count("M") == 2 and path.get("fill-rule") == "evenodd"


def test_untrusted_text_cannot_break_the_svg():
    nasty = '"><script>alert(1)</script>'
    data = [square(51.3, 35.6, name=nasty)]
    out = render_static_map(data, color_field="name", title=nasty)
    root = parse(out["svg"])  # still well-formed
    assert not any(el.tag.endswith("script") for el in root.iter())
    assert "<script" not in out["svg"] and "&lt;script" in out["svg"]
    for bad in ('red" onload="x', "javascript:alert(1)", "#12", "url(#x)", None, 5):
        with pytest.raises(ValueError):
            render_static_map(data, fill=bad)


def test_map_validation_and_limits(monkeypatch):
    data = [square(51.3, 35.6)]
    for kwargs in ({"width": 50}, {"height": 5000}, {"opacity": 2}, {"classes": 1}, {"palette": "rainbow"},
                   {"title": "x" * 200}, {"point_radius": 0}, {"stroke_width": 99}):
        with pytest.raises(ValueError):
            render_static_map(data, **kwargs)
    with pytest.raises(ValueError, match="nothing to draw"):
        render_static_map([{"type": "Feature", "properties": {}, "geometry": None}])
    monkeypatch.setattr(renderer, "load_config", lambda _id: {"max_vertices": 3})
    with pytest.raises(ValueError, match="vertex count"):
        render_static_map(data)
    monkeypatch.setattr(renderer, "load_config", lambda _id: {"max_features": 1})
    with pytest.raises(ValueError, match="feature count"):
        render_static_map([square(1, 1), square(2, 2)])


# ----------------------------------------------------------------- vector files
@pytest.fixture
def open_config(monkeypatch):
    monkeypatch.setattr(exporter, "load_config", lambda _id: {})


def sample_features():
    return [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
         "properties": {"name": "تهران", "population_estimate": 9000000, "tags": ["a", "b"], "n": None}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [52.0, 36.0]},
         "properties": {"name": "ری", "population_estimate": 1, "tags": [], "n": 5}},
        {"type": "Feature", "geometry": None, "properties": {"name": "no geometry"}},
    ]


def test_geopackage_roundtrip(tmp_path, open_config):
    gpd = pytest.importorskip("geopandas")
    result = export_vector_file(sample_features(), "gpkg", "cities", output_dir=str(tmp_path), filename="c")
    assert result["file_name"] == "c.gpkg" and result["feature_count"] == 2 and result["crs"] == "EPSG:4326"
    assert "without geometry" in result["warnings"][0]
    frame = gpd.read_file(tmp_path / "c.gpkg", layer="cities")
    assert list(frame["name"]) == ["تهران", "ری"] and frame.crs.to_epsg() == 4326
    assert "population_estimate" in frame.columns  # long names survive in GeoPackage
    assert json.loads(frame["tags"][0]) == ["a", "b"]
    assert not list(tmp_path.glob(".tmp_*"))


def test_shapefile_zip_with_short_names_and_single_geometry_type(tmp_path, open_config):
    pytest.importorskip("geopandas")
    result = export_vector_file(sample_features()[:2], "shp", output_dir=str(tmp_path), filename="s", crs=32639)
    assert result["file_name"] == "s.zip" and result["crs"] == "EPSG:32639"
    with zipfile.ZipFile(tmp_path / "s.zip") as archive:
        names = set(archive.namelist())
    assert {"s.shp", "s.shx", "s.dbf", "s.prj"} <= names
    assert "shortened" in " ".join(result["warnings"]) and "population_estimate" in " ".join(result["warnings"])
    mixed = sample_features()[:1] + [line([[0, 0], [1, 1]])]
    with pytest.raises(ValueError, match="one geometry type"):
        export_vector_file(mixed, "shp", output_dir=str(tmp_path), filename="m")
    export_vector_file(mixed, "gpkg", output_dir=str(tmp_path), filename="m")  # fine in a GeoPackage


def test_export_vector_validation_and_overwrite(tmp_path, open_config):
    pytest.importorskip("geopandas")
    data = sample_features()[:1]
    export_vector_file(data, output_dir=str(tmp_path), filename="x")
    with pytest.raises(FileExistsError):
        export_vector_file(data, output_dir=str(tmp_path), filename="x")
    export_vector_file(data, output_dir=str(tmp_path), filename="x", overwrite=True)
    for kwargs in ({"format": "kml"}, {"layer_name": "bad name"}, {"layer_name": "1x"}, {"crs": "WGS84"},
                   {"output_path": str(tmp_path / "x.shp")}):
        with pytest.raises(ValueError):
            export_vector_file(data, **{"output_dir": str(tmp_path), "filename": "y", **kwargs})
    with pytest.raises(ValueError, match="geometry"):
        export_vector_file([{"type": "Feature", "geometry": None, "properties": {}}], output_dir=str(tmp_path))


def test_allowed_roots_apply_to_both_exporters(tmp_path, monkeypatch):
    allowed = tmp_path / "ok"
    monkeypatch.setattr(exporter, "load_config", lambda _id: {"allowed_output_roots": [str(allowed)]})
    with pytest.raises(ValueError, match="allowed output root"):
        export_vector_file(sample_features()[:1], output_dir=str(tmp_path / "elsewhere"), filename="a")
    with pytest.raises(ValueError, match="allowed output root"):
        export_raster_geotiff(_raster(), output_dir=str(tmp_path / "elsewhere"), filename="a")


# ----------------------------------------------------------------------- GeoTIFF
def _raster(data=None, nodata=-9999, crs="EPSG:32639"):
    data = np.arange(12, dtype=float).reshape(3, 4) if data is None else data
    return {"data": np.asarray(data).tolist(),
            "metadata": {"crs": crs, "transform": [10.0, 0, 500000.0, 0, -10.0, 3950000.0], "nodata": nodata}}


def test_geotiff_roundtrip(tmp_path, open_config):
    rasterio = pytest.importorskip("rasterio")
    data = np.arange(12, dtype=float).reshape(3, 4)
    data[1, 1] = -9999
    result = export_raster_geotiff(_raster(data), output_dir=str(tmp_path), filename="r")
    assert result["format"] == "geotiff" and (result["width"], result["height"]) == (4, 3)
    with rasterio.open(tmp_path / "r.tif") as src:
        assert src.crs.to_epsg() == 32639 and src.nodata == -9999 and src.dtypes[0] == "float32"
        assert tuple(src.transform)[:6] == (10.0, 0.0, 500000.0, 0.0, -10.0, 3950000.0)
        read = src.read(1)
    assert read[0, 0] == 0 and read[1, 1] == -9999 and read[2, 3] == 11


def test_geotiff_nan_integer_cog_and_validation(tmp_path, open_config):
    rasterio = pytest.importorskip("rasterio")
    nan_raster = _raster(np.array([[1.0, np.nan], [3.0, 4.0]]), nodata=None)
    result = export_raster_geotiff(nan_raster, dtype="int16", output_dir=str(tmp_path), filename="n")
    assert result["nodata"] == -9999.0
    with rasterio.open(tmp_path / "n.tif") as src:
        assert src.dtypes[0] == "int16" and src.read(1)[0, 1] == -9999
    cog = export_raster_geotiff(_raster(np.zeros((600, 600))), cog=True, output_dir=str(tmp_path), filename="c")
    assert cog["format"] == "cog"
    with rasterio.open(tmp_path / "c.tif") as src:
        assert src.profile.get("tiled") is True
    with pytest.raises(ValueError, match="fit"):
        export_raster_geotiff(_raster(np.full((2, 2), 70000.0)), dtype="int16", output_dir=str(tmp_path))
    with pytest.raises(FileExistsError):
        export_raster_geotiff(_raster(), output_dir=str(tmp_path), filename="r2") and \
            export_raster_geotiff(_raster(), output_dir=str(tmp_path), filename="r2")
    for kwargs in ({"dtype": "complex"}, {"compress": "zstd"}, {"output_path": str(tmp_path / "x.png")}):
        with pytest.raises(ValueError):
            export_raster_geotiff(_raster(), **{"output_dir": str(tmp_path), "filename": "z", **kwargs})
    with pytest.raises(ValueError, match="CRS"):
        export_raster_geotiff(_raster(crs="not a crs"), output_dir=str(tmp_path), filename="bad")
