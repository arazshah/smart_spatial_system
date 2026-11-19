"""
s3geo inside QGIS (Python Console / Processing script) - route 1: use the library directly.

Install into the Python that QGIS itself uses (see README.md in this folder), then:

    from s3geo_qgis import run, ask, to_geojson, to_layer

    homes  = to_geojson(QgsProject.instance().mapLayersByName("homes")[0])
    metro  = to_geojson(QgsProject.instance().mapLayersByName("metro")[0])

    # Deterministic, offline, no API key: any of the 100 capabilities, by name.
    near = run("find_nearest_neighbors", metric=True,
               source_features=homes, target_features=metro, k=1)
    to_layer(near, "nearest_metro", add=True)

    # Natural language (needs an LLM key, see README.md).
    result = ask("Which homes are within 500 m of a metro station?", homes=homes, metro=metro)
    to_layer(result, "within_500m", add=True)

Everything except ``to_geojson`` / ``to_layer`` is plain Python (no QGIS import), so it also runs
and is tested outside QGIS. No analysis logic lives here: it calls the same plugin capabilities
as the API and the MCP server.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- plain-Python core


def _iter_coords(coords: Any):
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2]):
            yield coords
        else:
            for item in coords:
                yield from _iter_coords(item)


def _as_feature_collection(data: Any) -> dict[str, Any]:
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        return data
    if isinstance(data, dict) and data.get("type") == "Feature":
        return {"type": "FeatureCollection", "features": [data]}
    if isinstance(data, list):
        return {"type": "FeatureCollection", "features": data}
    if isinstance(data, dict) and isinstance(data.get("features"), list):  # VectorOut as dict
        return {"type": "FeatureCollection", "features": data["features"],
                "metadata": data.get("metadata", {})}
    raise TypeError("expected a GeoJSON FeatureCollection / Feature / list of features")


def utm_epsg(*collections: dict[str, Any]) -> str:
    """EPSG code of the UTM zone at the centroid of the lon/lat bounding box of the inputs."""
    xs: list[float] = []
    ys: list[float] = []
    for fc in collections:
        for feat in _as_feature_collection(fc)["features"]:
            for c in _iter_coords((feat.get("geometry") or {}).get("coordinates")):
                xs.append(c[0])
                ys.append(c[1])
    if not xs:
        raise ValueError("no coordinates found; cannot choose a metric CRS")
    if not (-180 <= min(xs) and max(xs) <= 180 and -90 <= min(ys) and max(ys) <= 90):
        raise ValueError("coordinates are not lon/lat degrees; pass metric=False")
    lon = (min(xs) + max(xs)) / 2
    lat = (min(ys) + max(ys)) / 2
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _call(name: str, **kwargs: Any) -> dict[str, Any]:
    import s3geo
    from smart_spatial_system.shared.jsonable import to_jsonable

    binding = s3geo.registry().resolve(name)
    out = to_jsonable(binding.callable(**kwargs))
    if isinstance(out, dict) and "features" in out and "type" not in out:
        out = {"type": "FeatureCollection", **out}  # so the result can feed the next call
    return out


def run(capability: str, *, metric: bool = False, back_to_wgs84: bool = True,
        **inputs: Any) -> dict[str, Any]:
    """
    Call one capability by name (e.g. "buffer_vector_features") on GeoJSON inputs.

    metric=True reprojects every FeatureCollection input to the UTM zone of the data first
    (distances/areas/buffers are then in metres) and, with back_to_wgs84, returns the result in
    EPSG:4326 so QGIS can show it over the original layers. Capabilities do not reproject on
    their own: a 500 m buffer on lon/lat data is 500 *degrees*.
    """
    collections = {k: _as_feature_collection(v) for k, v in inputs.items()
                   if isinstance(v, dict) and (v.get("type") in ("FeatureCollection", "Feature"))}
    if not metric:
        inputs.update(collections)
        return _call(capability, **inputs)

    crs = utm_epsg(*collections.values())
    for key, fc in collections.items():
        inputs[key] = _call("transform_vector_crs", features=fc, source_crs="EPSG:4326",
                            target_crs=crs)
    out = _call(capability, **inputs)
    if back_to_wgs84 and isinstance(out, dict) and out.get("features") is not None:
        meta = out.get("metadata", {})
        out = _call("transform_vector_crs", features=out, source_crs=crs, target_crs="EPSG:4326")
        out.setdefault("metadata", {}).update({"computed_in_crs": crs, **{
            k: v for k, v in meta.items() if k not in ("crs",)}})
    return out


def ask(question: str, **layers: Any):
    """Natural-language query via s3geo.query (needs an LLM key). Returns a GeoJSON dict."""
    import s3geo
    from smart_spatial_system.shared.jsonable import to_jsonable

    result = s3geo.query(question, layers={k: _as_feature_collection(v) for k, v in layers.items()})
    print("Goal:", result.goal)
    print("Operations:", " -> ".join(result.operations))
    return to_jsonable(result.output)


def flatten_properties(fc: dict[str, Any]) -> dict[str, Any]:
    """Nested dict/list property values (e.g. _target_properties) -> JSON strings for GIS fields."""
    fc = _as_feature_collection(fc)
    feats = []
    for f in fc["features"]:
        props = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                 for k, v in (f.get("properties") or {}).items()}
        feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": props})
    return {"type": "FeatureCollection", "features": feats}


# --------------------------------------------------------------------------- QGIS I/O (thin)


def to_geojson(layer, selected_only: bool = False) -> dict[str, Any]:
    """QgsVectorLayer -> GeoJSON FeatureCollection in EPSG:4326."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsJsonExporter

    exporter = QgsJsonExporter(layer)
    exporter.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    feats = layer.selectedFeatures() if selected_only else list(layer.getFeatures())
    return json.loads(exporter.exportFeatures(feats))


def to_layer(fc: dict[str, Any], name: str, *, add: bool = True):
    """GeoJSON FeatureCollection (EPSG:4326) -> QgsVectorLayer, optionally added to the project."""
    from qgis.core import QgsProject, QgsVectorLayer

    path = Path(tempfile.mkdtemp(prefix="s3geo_")) / f"{name}.geojson"
    path.write_text(json.dumps(flatten_properties(fc), ensure_ascii=False), encoding="utf-8")
    layer = QgsVectorLayer(str(path), name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"QGIS could not open the result as a layer: {path}")
    if add:
        QgsProject.instance().addMapLayer(layer)
    return layer
