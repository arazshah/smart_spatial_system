"""QGIS plugin packaging and metadata (plain Python: runs in the normal CI, no QGIS needed)."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_qgis_plugin", ROOT / "scripts" / "package_qgis_plugin.py")
pkg = importlib.util.module_from_spec(spec)
sys.modules["package_qgis_plugin"] = pkg
spec.loader.exec_module(pkg)


def test_metadata_is_valid_for_development():
    assert pkg.validate(pkg.read_metadata(), release=False) == []


def test_release_build_refuses_the_placeholder_email():
    errors = pkg.validate(pkg.read_metadata(), release=True)
    meta = pkg.read_metadata()
    assert bool(errors) == ("CHANGE_ME" in meta["email"])


def test_metadata_matches_the_package_minimum():
    meta = pkg.read_metadata()
    assert meta["hasProcessingProvider"] == "yes" and meta["icon"] == "icon.svg"
    assert (pkg.PLUGIN_DIR / meta["icon"]).exists()


def test_zip_layout(tmp_path):
    target = pkg.build(tmp_path, release=False)
    names = zipfile.ZipFile(target).namelist()
    assert {n.split("/")[0] for n in names} == {"s3geo_processing"}
    for needed in ("metadata.txt", "__init__.py", "plugin.py", "provider.py", "algorithms.py",
                   "core.py", "icon.svg", "LICENSE"):
        assert f"s3geo_processing/{needed}" in names
    assert not any("__pycache__" in n for n in names)


def test_core_plain_python_helpers():
    sys.path.insert(0, str(ROOT / "qgis_plugin"))
    from s3geo_processing import core

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
         "properties": {"a": {"x": 1}}}]}
    assert core.utm_epsg(fc) == "EPSG:32639"
    assert isinstance(core.flatten_properties(fc)["features"][0]["properties"]["a"], str)
    with pytest.raises(ValueError):
        core.utm_epsg({"type": "FeatureCollection", "features": []})
