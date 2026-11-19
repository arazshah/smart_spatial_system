"""Bridge between QGIS features and the s3geo capabilities.

``utm_epsg``, ``flatten_properties`` and the GeoJSON helpers are plain Python (tested without
QGIS). Everything that touches ``qgis.core`` is imported lazily inside the function that needs it.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

MIN_PACKAGE_VERSION = (0, 6, 2)
INSTALL_HELP = (
    "The Python package 'smart_spatial_system' (>= 0.6.2) is not available to QGIS. Install it "
    "into the Python QGIS uses. Windows (OSGeo4W Shell): python -m pip install -U "
    "smart_spatial_system. Linux: python3 -m venv --system-site-packages ~/s3env && "
    "~/s3env/bin/pip install smart_spatial_system && ~/s3env/bin/pip install 'numpy<2' "
    "'pandas<3' 'geopandas<1.1', then make QGIS see ~/s3env/lib/python3.X/site-packages "
    "(PYTHONPATH or python/startup.py). Full steps: https://github.com/arazshah/"
    "smart_spatial_system/blob/main/docs/QGIS_PLUGIN.md"
)

_REGISTRY = None


class S3GeoUnavailable(RuntimeError):
    pass


def package_version() -> tuple[int, ...] | None:
    try:
        from importlib.metadata import version

        return tuple(int(p) for p in version("smart_spatial_system").split(".")[:3] if p.isdigit())
    except Exception:
        return None


def require_s3geo():
    """Return the s3geo module or raise S3GeoUnavailable with install instructions."""
    try:
        import s3geo
    except ImportError as exc:
        raise S3GeoUnavailable(f"{INSTALL_HELP} (import error: {exc})") from exc
    ver = package_version()
    if ver is not None and ver < MIN_PACKAGE_VERSION:
        raise S3GeoUnavailable(INSTALL_HELP)
    return s3geo


def registry():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = require_s3geo().registry()
    return _REGISTRY


# ----------------------------------------------------------------------------- plain Python


def _iter_coords(coords: Any):
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2]):
            yield coords
        else:
            for item in coords:
                yield from _iter_coords(item)


def as_feature_collection(data: Any) -> dict[str, Any]:
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        return data
    if isinstance(data, dict) and data.get("type") == "Feature":
        return {"type": "FeatureCollection", "features": [data]}
    if isinstance(data, list):
        return {"type": "FeatureCollection", "features": data}
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        return {"type": "FeatureCollection", "features": data["features"],
                "metadata": data.get("metadata", {})}
    raise TypeError("expected a GeoJSON FeatureCollection / Feature / list of features")


def is_feature_collection(value: Any) -> bool:
    return isinstance(value, dict) and (
        value.get("type") in ("FeatureCollection", "Feature")
    )


def utm_epsg(*collections: dict[str, Any]) -> str:
    """UTM zone (EPSG) at the centre of the lon/lat bounding box of the inputs."""
    xs: list[float] = []
    ys: list[float] = []
    for fc in collections:
        for feat in as_feature_collection(fc)["features"]:
            for c in _iter_coords((feat.get("geometry") or {}).get("coordinates")):
                xs.append(c[0])
                ys.append(c[1])
    if not xs:
        raise ValueError("The input layers have no geometries, so no metric CRS can be chosen.")
    if not (-180 <= min(xs) and max(xs) <= 180 and -90 <= min(ys) and max(ys) <= 90):
        raise ValueError("Coordinates are not longitude/latitude degrees.")
    lon = (min(xs) + max(xs)) / 2
    lat = (min(ys) + max(ys)) / 2
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def flatten_properties(fc: dict[str, Any]) -> dict[str, Any]:
    """Nested dict/list property values -> JSON strings, so they fit GIS attribute fields."""
    fc = as_feature_collection(fc)
    feats = []
    for f in fc["features"]:
        props = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                 for k, v in (f.get("properties") or {}).items()}
        feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def call(name: str, **kwargs: Any) -> dict[str, Any]:
    from smart_spatial_system.shared.jsonable import to_jsonable

    out = to_jsonable(registry().resolve(name).callable(**kwargs))
    if isinstance(out, dict) and "features" in out and "type" not in out:
        out = {"type": "FeatureCollection", **out}
    return out


def run_capability(capability: str, *, metric: bool, **inputs: Any) -> dict[str, Any]:
    """Run one capability. metric=True: inputs -> UTM, call, result -> EPSG:4326."""
    layers = {k: as_feature_collection(v) for k, v in inputs.items() if is_feature_collection(v)}
    if not metric:
        inputs.update(layers)
        return call(capability, **inputs)
    crs = utm_epsg(*layers.values())
    for key, fc in layers.items():
        inputs[key] = call("transform_vector_crs", features=fc, source_crs="EPSG:4326",
                           target_crs=crs)
    out = call(capability, **inputs)
    if isinstance(out, dict) and out.get("features") is not None:
        meta = dict(out.get("metadata") or {})
        out = call("transform_vector_crs", features=out, source_crs=crs, target_crs="EPSG:4326")
        out["metadata"] = {**meta, "computed_in_crs": crs}
    return out


# ----------------------------------------------------------------------------- QGIS I/O


def source_to_geojson(source) -> dict[str, Any]:
    """QgsProcessingFeatureSource -> GeoJSON FeatureCollection in EPSG:4326."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsJsonExporter

    exporter = QgsJsonExporter()
    exporter.setSourceCrs(source.sourceCrs())
    exporter.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    return json.loads(exporter.exportFeatures(list(source.getFeatures())))


def geojson_to_layer(fc: dict[str, Any], name: str = "s3geo_result"):
    """GeoJSON FeatureCollection -> in-memory-readable QgsVectorLayer (temp file) + temp dir."""
    from qgis.core import QgsVectorLayer

    tmp = Path(tempfile.mkdtemp(prefix="s3geo_"))
    path = tmp / f"{name}.geojson"
    path.write_text(json.dumps(flatten_properties(fc), ensure_ascii=False), encoding="utf-8")
    layer = QgsVectorLayer(str(path), name, "ogr")
    if not layer.isValid():
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("QGIS could not read the analysis result as a layer.")
    return layer, tmp


def write_sink(algorithm, parameters, name, context, fc: dict[str, Any]) -> str | None:
    """Copy a GeoJSON result into the Processing output ``name``; returns the output id."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsFeatureSink

    layer, tmp = geojson_to_layer(fc)
    try:
        sink, dest_id = algorithm.parameterAsSink(
            parameters, name, context, layer.fields(), layer.wkbType(),
            QgsCoordinateReferenceSystem("EPSG:4326"))
        if sink is None:
            return None
        for feature in layer.getFeatures():
            sink.addFeature(feature, QgsFeatureSink.FastInsert)
        return dest_id
    finally:
        del layer
        shutil.rmtree(tmp, ignore_errors=True)
