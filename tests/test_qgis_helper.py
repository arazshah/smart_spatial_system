"""examples/qgis/s3geo_qgis.py: the plain-Python core (QGIS I/O needs QGIS and is not tested here)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "qgis"))

import s3geo_qgis as q  # noqa: E402


def _pt(lon, lat, **p):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": p}


HOMES = {"type": "FeatureCollection", "features": [
    _pt(51.4105, 35.7570, name="A"), _pt(51.4200, 35.7000, name="B"), _pt(51.3300, 35.7800, name="C")]}
METRO = {"type": "FeatureCollection", "features": [
    _pt(51.4108, 35.7578, name="Vanak"), _pt(51.4190, 35.7010, name="Enghelab")]}


def test_utm_zone_for_tehran_and_southern_hemisphere():
    assert q.utm_epsg(HOMES, METRO) == "EPSG:32639"
    assert q.utm_epsg({"type": "FeatureCollection", "features": [_pt(151.2, -33.9)]}) == "EPSG:32756"


def test_utm_rejects_projected_input():
    with pytest.raises(ValueError):
        q.utm_epsg({"type": "FeatureCollection", "features": [_pt(537110.0, 3957074.0)]})


def test_run_metric_nearest_returns_metres_in_wgs84():
    out = q.run("find_nearest_neighbors", metric=True, source_features=HOMES,
                target_features=METRO, k=1)
    dist = {f["properties"]["name"]: f["properties"]["_nearest_distance"] for f in out["features"]}
    assert 80 < dist["A"] < 110 and 120 < dist["B"] < 170 and dist["C"] > 7000
    x, y = out["features"][0]["geometry"]["coordinates"]
    assert 51 < x < 52 and 35 < y < 36  # back in lon/lat


def test_run_metric_buffer_is_500_metres_not_degrees():
    out = q.run("buffer_vector_features", metric=True, features=METRO, distance=500, units="meters")
    xs = [c[0] for c in out["features"][0]["geometry"]["coordinates"][0]]
    assert max(xs) - min(xs) < 0.02  # ~1 km wide in degrees, not 1000 degrees
    assert "warnings" not in out["metadata"]


def test_flatten_properties_makes_gis_friendly_fields():
    out = q.flatten_properties({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"a": 1, "_t": {"name": "ونک"}}}]})
    props = out["features"][0]["properties"]
    assert props["a"] == 1 and isinstance(props["_t"], str) and "ونک" in props["_t"]
