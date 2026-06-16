"""
local_vector_loader.py

GeoChat SDK Plugin
==================

Plugin ID:
    local_vector_loader

Purpose:
    Load and validate local vector files such as GeoJSON, JSON, Shapefile,
    GeoPackage, KML and FlatGeobuf, then return a standard VectorOut object.

Design:
    - GeoJSON/JSON are loaded using Python standard library.
    - Other vector formats are loaded lazily using geopandas if installed.
    - No heavy GIS dependency is imported at module import time.
    - Output is always VectorOut so SDK converts it to ExecutionArtifact(kind="features").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.exceptions import SDKDependencyError

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "local_vector_loader"

ALLOWED_VECTOR_EXTENSIONS: set[str] = {
    ".geojson",
    ".json",
    ".shp",
    ".gpkg",
    ".kml",
    ".fgb",
}


GEOPANDAS_REQUIRED_EXTENSIONS: set[str] = {
    ".shp",
    ".gpkg",
    ".kml",
    ".fgb",
}



def _load_loader_config() -> dict[str, Any]:
    """
    Load config/plugins/local_vector_loader.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _configured_allowed_extensions(config: dict[str, Any]) -> set[str]:
    """
    Return allowed vector extensions from config or module defaults.
    """
    values = config.get("allowed_extensions")
    if not values:
        return set(ALLOWED_VECTOR_EXTENSIONS)

    if not isinstance(values, list):
        raise ValueError("allowed_extensions in local_vector_loader config must be a list.")

    return {str(item).lower() for item in values}


def _configured_allowed_roots(config: dict[str, Any]) -> list[str]:
    """
    Return allowed roots from config.
    Empty list means no root restriction.
    """
    values = config.get("allowed_roots") or []

    if not isinstance(values, list):
        raise ValueError("allowed_roots in local_vector_loader config must be a list.")

    return [str(item) for item in values]


def _ensure_under_allowed_roots(path: Path, allowed_roots: list[str] | None) -> None:
    """
    Ensure path is under one of allowed_roots.

    If allowed_roots is empty or None, no restriction is applied.
    """
    if not allowed_roots:
        return

    resolved_path = path.resolve()
    resolved_roots = [Path(root).expanduser().resolve() for root in allowed_roots]

    for root in resolved_roots:
        if resolved_path == root or root in resolved_path.parents:
            return

    raise ValueError(
        f"Vector path is not under any allowed root: {resolved_path}. "
        f"Allowed roots: {[str(r) for r in resolved_roots]}"
    )


def _validate_path(
    path: str,
    strict_extensions: bool = True,
    allowed_extensions: set[str] | None = None,
    allowed_roots: list[str] | None = None,
) -> Path:
    """
    Validate a local vector file path.

    Args:
        path:
            Local vector file path.
        strict_extensions:
            If True, file extension must be one of ALLOWED_VECTOR_EXTENSIONS.

    Returns:
        Resolved pathlib.Path.

    Raises:
        ValueError:
            If path is empty, not a string, not a file or has invalid extension.
        FileNotFoundError:
            If file does not exist.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string.")

    vector_path = Path(path).expanduser().resolve()

    if not vector_path.exists():
        raise FileNotFoundError(f"Vector file not found: {vector_path}")

    if not vector_path.is_file():
        raise ValueError(f"Vector path is not a file: {vector_path}")

    _ensure_under_allowed_roots(vector_path, allowed_roots)

    suffix = vector_path.suffix.lower()
    effective_extensions = allowed_extensions or ALLOWED_VECTOR_EXTENSIONS

    if strict_extensions and suffix not in effective_extensions:
        raise ValueError(
            "Unsupported vector extension "
            f"'{suffix}'. Allowed extensions: {sorted(effective_extensions)}"
        )

    return vector_path


def _is_number(value: Any) -> bool:
    """
    Return True if value is int/float but not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate a simple bbox from a GeoJSON geometry.

    Returns:
        [minx, miny, maxx, maxy] or None.
    """
    if not geometry:
        return None

    coords = geometry.get("coordinates")
    if coords is None:
        return None

    xs: list[float] = []
    ys: list[float] = []

    def walk(obj: Any) -> None:
        if (
            isinstance(obj, (list, tuple))
            and len(obj) >= 2
            and _is_number(obj[0])
            and _is_number(obj[1])
        ):
            xs.append(float(obj[0]))
            ys.append(float(obj[1]))
            return

        if isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(coords)

    if not xs or not ys:
        return None

    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_bboxes(bboxes: list[list[float]]) -> dict[str, float] | None:
    """
    Merge multiple bbox arrays into one bbox dict.
    """
    valid = [b for b in bboxes if b and len(b) == 4]
    if not valid:
        return None

    return {
        "minx": min(b[0] for b in valid),
        "miny": min(b[1] for b in valid),
        "maxx": max(b[2] for b in valid),
        "maxy": max(b[3] for b in valid),
    }


def _normalize_geojson_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize a GeoJSON Feature.

    Ensures:
        - type == "Feature"
        - properties is a dict
        - geometry key exists
    """
    if not isinstance(feature, dict):
        raise ValueError(f"GeoJSON feature at index {index} must be an object.")

    if feature.get("type") != "Feature":
        raise ValueError(f"GeoJSON item at index {index} is not a Feature.")

    properties = feature.get("properties")
    if properties is None:
        properties = {}
    if not isinstance(properties, dict):
        raise ValueError(f"Feature properties at index {index} must be an object or null.")

    if "geometry" not in feature:
        raise ValueError(f"Feature at index {index} does not contain geometry.")

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": properties,
    }


def _load_geojson(vector_path: Path, max_features: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Load GeoJSON/JSON using standard library.

    Supported input objects:
        - FeatureCollection
        - Single Feature
    """
    try:
        with vector_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON/GeoJSON file: {vector_path}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to read vector file: {vector_path}. Error: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("GeoJSON root must be an object.")

    geojson_type = data.get("type")

    if geojson_type == "FeatureCollection":
        raw_features = data.get("features", [])
        if not isinstance(raw_features, list):
            raise ValueError("GeoJSON FeatureCollection.features must be a list.")
    elif geojson_type == "Feature":
        raw_features = [data]
    else:
        raise ValueError(
            "Unsupported GeoJSON type. Expected 'FeatureCollection' or 'Feature'."
        )

    if max_features is not None:
        if not isinstance(max_features, int) or max_features < 0:
            raise ValueError("max_features must be a non-negative integer or None.")
        raw_features = raw_features[:max_features]

    features: list[dict[str, Any]] = []
    geometry_types: dict[str, int] = {}
    bboxes: list[list[float]] = []

    for idx, raw_feature in enumerate(raw_features):
        feature = _normalize_geojson_feature(raw_feature, idx)
        features.append(feature)

        geometry = feature.get("geometry")
        if isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "Unknown")
        elif geometry is None:
            gtype = "Null"
        else:
            gtype = "Invalid"

        geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

        bbox = _geometry_bbox(geometry if isinstance(geometry, dict) else None)
        if bbox is not None:
            bboxes.append(bbox)

    crs = "EPSG:4326"
    crs_obj = data.get("crs")
    if isinstance(crs_obj, dict):
        props = crs_obj.get("properties")
        if isinstance(props, dict) and props.get("name"):
            crs = str(props["name"])

    metadata: dict[str, Any] = {
        "source": "local_file",
        "loader": PLUGIN_ID,
        "format": "geojson",
        "geojson_type": geojson_type,
        "path": str(vector_path),
        "filename": vector_path.name,
        "extension": vector_path.suffix.lower(),
        "file_size_bytes": int(vector_path.stat().st_size),
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": _merge_bboxes(bboxes),
        "crs": crs,
    }

    return features, metadata


def _load_with_geopandas(
    vector_path: Path,
    layer: str | None = None,
    max_features: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Load non-GeoJSON vector formats using geopandas.

    Supported by this path:
        - Shapefile
        - GeoPackage
        - KML
        - FlatGeobuf
    """
    try:
        import json as _json
        import geopandas as gpd
    except ImportError as exc:
        raise SDKDependencyError(
            "This vector format requires 'geopandas' and its IO dependencies. "
            "Install it with: pip install geopandas"
        ) from exc

    try:
        if layer:
            gdf = gpd.read_file(str(vector_path), layer=layer)
        else:
            gdf = gpd.read_file(str(vector_path))

        if max_features is not None:
            if not isinstance(max_features, int) or max_features < 0:
                raise ValueError("max_features must be a non-negative integer or None.")
            gdf = gdf.head(max_features)

        geojson = _json.loads(gdf.to_json())
        features = geojson.get("features", [])
        if not isinstance(features, list):
            features = []

        bounds = None
        if not gdf.empty:
            total_bounds = gdf.total_bounds
            bounds = {
                "minx": float(total_bounds[0]),
                "miny": float(total_bounds[1]),
                "maxx": float(total_bounds[2]),
                "maxy": float(total_bounds[3]),
            }

        geometry_types = {}
        if "geometry" in gdf:
            for gtype in gdf.geometry.geom_type.fillna("Null").astype(str).tolist():
                geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

        crs = None
        if gdf.crs is not None:
            crs = gdf.crs.to_string()

        metadata: dict[str, Any] = {
            "source": "local_file",
            "loader": PLUGIN_ID,
            "format": vector_path.suffix.lower().lstrip("."),
            "path": str(vector_path),
            "filename": vector_path.name,
            "extension": vector_path.suffix.lower(),
            "file_size_bytes": int(vector_path.stat().st_size),
            "feature_count": len(features),
            "geometry_types": geometry_types,
            "bounds": bounds,
            "crs": crs,
            "layer": layer,
            "columns": [str(c) for c in gdf.columns],
        }

        return features, metadata

    except Exception as exc:
        raise RuntimeError(
            f"Failed to load vector file with geopandas: {vector_path}. Error: {exc}"
        ) from exc


@capability(
    name="load_local_vector",
    keywords=[
        # English keywords
        "vector",
        "geojson",
        "json vector",
        "shapefile",
        "shape file",
        "shp",
        "gpkg",
        "geopackage",
        "kml",
        "flatgeobuf",
        "fgb",
        "local vector",
        "load vector",
        "open vector",
        "read vector",
        "features",
        "feature collection",

        # Persian keywords
        "بردار",
        "وکتور",
        "لایه برداری",
        "داده برداری",
        "فایل برداری",
        "ژئوجیسون",
        "جئوجیسون",
        "شیپ فایل",
        "شیپ‌فایل",
        "ژئوپکیج",
        "بارگذاری بردار",
        "خواندن بردار",
        "باز کردن بردار",
        "عارضه",
        "عوارض",
    ],
    description=(
        "Load a local vector file such as GeoJSON, Shapefile or GeoPackage, "
        "validate it, extract spatial metadata and return VectorOut features."
    ),
    required_inputs=["path"],
    optional_inputs=["strict_extensions", "layer", "max_features"],
    output_kind="vector",
    permissions=["filesystem"],
    metadata={
        "category": "data_io",
        "data_type": "vector",
        "source_type": "local_file",
        "source_priority": 1,
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "read_vector",
        "config_aware": True,
        "routable": True,
    },
)
def load_local_vector(
    path: str,
    strict_extensions: bool | None = None,
    layer: str | None = None,
    max_features: int | None = None,
) -> VectorOut:
    """
    Load a local vector file and expose it as standard SDK VectorOut.

    Args:
        path:
            Path to a local vector file.
        strict_extensions:
            If True, only known vector file extensions are accepted.
        layer:
            Optional layer name for multi-layer formats such as GeoPackage.
        max_features:
            Optional maximum number of features to load.

    Returns:
        VectorOut:
            GeoJSON-like features and metadata.

    Raises:
        ValueError:
            Invalid input path, unsupported extension or malformed GeoJSON.
        FileNotFoundError:
            File does not exist.
        SDKDependencyError:
            geopandas is required for non-GeoJSON vector formats.
        RuntimeError:
            Vector file cannot be opened or converted.
    """
    config = _load_loader_config()

    final_strict_extensions = pick_first(
        strict_extensions,
        config.get("default_strict_extensions"),
        default=True,
    )

    final_max_features = pick_first(
        max_features,
        config.get("default_max_features"),
        default=None,
    )

    vector_path = _validate_path(
        path=path,
        strict_extensions=bool(final_strict_extensions),
        allowed_extensions=_configured_allowed_extensions(config),
        allowed_roots=_configured_allowed_roots(config),
    )
    suffix = vector_path.suffix.lower()
    effective_extensions = _configured_allowed_extensions(config)

    # GeoJSON-like text formats:
    # - Built-in .geojson/.json
    # - Any configured extension that is not a geopandas-dependent binary/native format
    geojson_like_extensions = {".geojson", ".json"} | (
        effective_extensions - GEOPANDAS_REQUIRED_EXTENSIONS
    )

    if suffix in geojson_like_extensions:
        features, metadata = _load_geojson(
            vector_path=vector_path,
            max_features=final_max_features,
        )
    elif suffix in GEOPANDAS_REQUIRED_EXTENSIONS:
        features, metadata = _load_with_geopandas(
            vector_path=vector_path,
            layer=layer,
            max_features=final_max_features,
        )
    else:
        raise ValueError(
            f"Unsupported vector extension '{suffix}'. "
            f"Allowed extensions: {sorted(effective_extensions)}"
        )

    return VectorOut(
        features=features,
        metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Local Vector Loader",
    description=(
        "Loads local vector files such as GeoJSON, Shapefile and GeoPackage, "
        "and exposes them as features artifacts for the GeoChat spatial pipeline."
    ),
    author="GeoChat Platform Team",
    permissions=["filesystem"],
)
