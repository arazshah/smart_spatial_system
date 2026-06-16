"""
data_writer_exporter.py

GeoChat SDK Plugin
==================

Plugin ID:
    data_writer_exporter

Purpose:
    Export spatial data produced by plugins/pipelines to local files.

Capabilities:
    - export_vector_geojson:
        Write GeoJSON FeatureCollection to disk and return VectorOut.

    - export_raster_copy:
        Copy a raster/image file to an output location and return RasterOut.

Config-aware behavior:
    Reads config/plugins/data_writer_exporter.yaml.

Important:
    This plugin writes to filesystem, so it uses allowed_output_roots to prevent
    accidental or unsafe writes outside expected directories.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.types.raster import RasterOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "data_writer_exporter"

DEFAULT_VECTOR_EXTENSIONS = {".geojson", ".json"}
DEFAULT_RASTER_EXTENSIONS = {
    ".tif",
    ".tiff",
    ".geotiff",
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".img",
    ".vrt",
}

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_\-\.]+")


def _load_exporter_config() -> dict[str, Any]:
    """
    Load config/plugins/data_writer_exporter.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    """
    Return current UTC timestamp as ISO string.
    """
    return datetime.now(timezone.utc).isoformat()


def _safe_filename_part(value: str) -> str:
    """
    Sanitize filename.
    """
    value = str(value or "").strip()
    value = value.replace(" ", "_")
    value = _SAFE_FILENAME_RE.sub("_", value)
    value = value.strip("._")
    return value[:120] or "export"


def _configured_output_dir(config: dict[str, Any]) -> str:
    """
    Return default output directory.
    """
    return str(config.get("default_output_dir") or "output/plugins/data_writer_exporter")


def _configured_overwrite(config: dict[str, Any]) -> bool:
    """
    Return default overwrite flag.
    """
    return bool(config.get("default_overwrite", True))


def _configured_pretty_json(config: dict[str, Any]) -> bool:
    """
    Return pretty_json flag.
    """
    return bool(config.get("pretty_json", True))


def _configured_allowed_output_roots(config: dict[str, Any]) -> list[str]:
    """
    Return allowed output roots.

    Empty list means unrestricted.
    """
    values = config.get("allowed_output_roots") or []

    if not isinstance(values, list):
        raise ValueError("allowed_output_roots in data_writer_exporter config must be a list.")

    return [str(item) for item in values]


def _configured_vector_extensions(config: dict[str, Any]) -> set[str]:
    """
    Return allowed vector output extensions.
    """
    section = config.get("vector") or {}
    if not isinstance(section, dict):
        raise ValueError("vector section in data_writer_exporter config must be a dict.")

    values = section.get("allowed_extensions")
    if not values:
        return set(DEFAULT_VECTOR_EXTENSIONS)

    if not isinstance(values, list):
        raise ValueError("vector.allowed_extensions must be a list.")

    return {str(item).lower() for item in values}


def _configured_raster_extensions(config: dict[str, Any]) -> set[str]:
    """
    Return allowed raster output extensions.
    """
    section = config.get("raster") or {}
    if not isinstance(section, dict):
        raise ValueError("raster section in data_writer_exporter config must be a dict.")

    values = section.get("allowed_extensions")
    if not values:
        return set(DEFAULT_RASTER_EXTENSIONS)

    if not isinstance(values, list):
        raise ValueError("raster.allowed_extensions must be a list.")

    return {str(item).lower() for item in values}


def _ensure_under_allowed_roots(path: Path, allowed_roots: list[str] | None) -> None:
    """
    Ensure output path is under one of allowed roots.

    If allowed_roots is empty or None, no restriction is applied.
    """
    if not allowed_roots:
        return

    resolved_path = path.expanduser().resolve()
    resolved_roots = [Path(root).expanduser().resolve() for root in allowed_roots]

    for root in resolved_roots:
        if resolved_path == root or root in resolved_path.parents:
            return

    raise ValueError(
        f"Output path is not under any allowed output root: {resolved_path}. "
        f"Allowed roots: {[str(r) for r in resolved_roots]}"
    )


def _resolve_output_path(
    *,
    output_path: str | None,
    output_dir: str | None,
    filename: str | None,
    default_output_dir: str,
    default_stem: str,
    extension: str,
    allowed_output_roots: list[str] | None,
) -> Path:
    """
    Resolve final output path.

    Priority:
        1. output_path
        2. output_dir + filename
        3. default_output_dir + generated filename
    """
    extension = extension.lower()
    if not extension.startswith("."):
        extension = f".{extension}"

    if output_path:
        path = Path(output_path).expanduser()
    else:
        final_output_dir = Path(output_dir or default_output_dir).expanduser()

        if filename:
            safe_name = _safe_filename_part(filename)
            if not Path(safe_name).suffix:
                safe_name = f"{safe_name}{extension}"
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_name = f"{_safe_filename_part(default_stem)}_{timestamp}{extension}"

        path = final_output_dir / safe_name

    path = path.resolve()
    _ensure_under_allowed_roots(path, allowed_output_roots)

    return path


def _ensure_can_write(path: Path, overwrite: bool) -> None:
    """
    Ensure output file can be written.
    """
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists and overwrite=False: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)


def _validate_extension(path: Path, allowed_extensions: set[str], data_type: str) -> str:
    """
    Validate output file extension.
    """
    suffix = path.suffix.lower()

    if not suffix:
        raise ValueError(f"{data_type} output path must have a file extension.")

    if suffix not in allowed_extensions:
        raise ValueError(
            f"Unsupported {data_type} output extension '{suffix}'. "
            f"Allowed extensions: {sorted(allowed_extensions)}"
        )

    return suffix


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize a GeoJSON Feature object.
    """
    if not isinstance(feature, dict):
        raise ValueError(f"Feature at index {index} must be a dict/object.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if properties is None:
        properties = {}

    if not isinstance(properties, dict):
        raise ValueError(f"Feature properties at index {index} must be dict/object or null.")

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": properties,
    }


def _extract_features(
    features: list[dict[str, Any]] | dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract features from either:
        - list[Feature]
        - FeatureCollection dict
        - single Feature dict
    """
    source_info: dict[str, Any] = {}

    if isinstance(features, dict):
        geojson_type = features.get("type")
        source_info["input_geojson_type"] = geojson_type

        if geojson_type == "FeatureCollection":
            raw_features = features.get("features", [])
            if not isinstance(raw_features, list):
                raise ValueError("FeatureCollection.features must be a list.")
        elif geojson_type == "Feature":
            raw_features = [features]
        else:
            raise ValueError("Input dict must be GeoJSON FeatureCollection or Feature.")
    elif isinstance(features, list):
        raw_features = features
        source_info["input_geojson_type"] = "FeatureList"
    else:
        raise ValueError("features must be a list, FeatureCollection dict or Feature dict.")

    normalized = [_normalize_feature(item, idx) for idx, item in enumerate(raw_features)]

    return normalized, source_info


def _is_number(value: Any) -> bool:
    """
    Return True if value is int/float but not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox from GeoJSON geometry.
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
    Merge bbox arrays into a bbox dict.
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


def _build_vector_metadata(features: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build metadata for exported vector data.
    """
    geometry_types: dict[str, int] = {}
    bboxes: list[list[float]] = []

    for feature in features:
        geometry = feature.get("geometry")

        if isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "Unknown")
            bbox = _geometry_bbox(geometry)
            if bbox is not None:
                bboxes.append(bbox)
        elif geometry is None:
            gtype = "Null"
        else:
            gtype = "Invalid"

        geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

    return {
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": _merge_bboxes(bboxes),
    }


def _feature_collection(features: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Build GeoJSON FeatureCollection.
    """
    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
    }

    if metadata:
        collection["metadata"] = metadata

    return collection


def _copy_file(src: Path, dst: Path, overwrite: bool) -> None:
    """
    Copy file from src to dst.
    """
    if not src.exists():
        raise FileNotFoundError(f"Input raster file does not exist: {src}")

    if not src.is_file():
        raise ValueError(f"Input raster path is not a file: {src}")

    _ensure_can_write(dst, overwrite=overwrite)
    shutil.copy2(src, dst)


@capability(
    name="export_vector_geojson",
    keywords=[
        "export vector",
        "write vector",
        "save geojson",
        "export geojson",
        "write geojson",
        "save features",
        "feature collection export",
        "خروجی برداری",
        "ذخیره ژئوجیسون",
        "ذخیره عوارض",
        "خروجی گرفتن بردار",
    ],
    description="Export GeoJSON-like vector features to a local GeoJSON/JSON file.",
    required_inputs=["features"],
    optional_inputs=[
        "output_path",
        "output_dir",
        "filename",
        "metadata",
        "overwrite",
        "pretty",
    ],
    output_kind="vector",
    permissions=["filesystem"],
    metadata={
        "category": "data_io",
        "data_type": "vector",
        "source_type": "exporter",
        "source_priority": 10,
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "write_vector",
        "config_aware": True,
        "routable": True,
    },
)
def export_vector_geojson(
    features: list[dict[str, Any]] | dict[str, Any],
    output_path: str | None = None,
    output_dir: str | None = None,
    filename: str | None = None,
    metadata: dict[str, Any] | None = None,
    overwrite: bool | None = None,
    pretty: bool | None = None,
) -> VectorOut:
    """
    Export vector features to a GeoJSON file.

    Args:
        features:
            list of GeoJSON Feature objects, FeatureCollection dict or single Feature dict.
        output_path:
            Exact output file path.
        output_dir:
            Output directory if output_path is not provided.
        filename:
            Output filename if output_path is not provided.
        metadata:
            Optional metadata to include in FeatureCollection and VectorOut.
        overwrite:
            Whether to overwrite existing file.
        pretty:
            Whether to pretty-print JSON.

    Returns:
        VectorOut with original features and export metadata.
    """
    config = _load_exporter_config()

    final_overwrite = bool(pick_first(overwrite, _configured_overwrite(config), default=True))
    final_pretty = bool(pick_first(pretty, _configured_pretty_json(config), default=True))

    allowed_roots = _configured_allowed_output_roots(config)
    allowed_extensions = _configured_vector_extensions(config)

    output_file = _resolve_output_path(
        output_path=output_path,
        output_dir=output_dir,
        filename=filename,
        default_output_dir=_configured_output_dir(config),
        default_stem="vector_export",
        extension=".geojson",
        allowed_output_roots=allowed_roots,
    )

    suffix = _validate_extension(output_file, allowed_extensions, "vector")

    normalized_features, source_info = _extract_features(features)
    vector_stats = _build_vector_metadata(normalized_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    export_metadata = {
        "source": "data_writer_exporter",
        "loader": PLUGIN_ID,
        "format": "geojson",
        "extension": suffix,
        "path": str(output_file),
        "filename": output_file.name,
        "exported_at": _utc_now_iso(),
        "overwrite": final_overwrite,
        **source_info,
        **vector_stats,
        **user_metadata,
    }

    collection = _feature_collection(normalized_features, metadata=export_metadata)

    _ensure_can_write(output_file, overwrite=final_overwrite)

    json_kwargs: dict[str, Any] = {
        "ensure_ascii": False,
    }

    if final_pretty:
        json_kwargs["indent"] = 2

    output_file.write_text(
        json.dumps(collection, **json_kwargs),
        encoding="utf-8",
    )

    export_metadata["file_size_bytes"] = int(output_file.stat().st_size)

    return VectorOut(
        features=normalized_features,
        metadata=export_metadata,
    )


@capability(
    name="export_raster_copy",
    keywords=[
        "export raster",
        "write raster",
        "save raster",
        "copy raster",
        "export image",
        "save map image",
        "خروجی رستر",
        "ذخیره رستر",
        "کپی رستر",
        "ذخیره تصویر نقشه",
    ],
    description="Copy/export a raster or map image file to a configured output location.",
    required_inputs=["path"],
    optional_inputs=[
        "output_path",
        "output_dir",
        "filename",
        "metadata",
        "overwrite",
    ],
    output_kind="raster",
    permissions=["filesystem"],
    metadata={
        "category": "data_io",
        "data_type": "raster",
        "source_type": "exporter",
        "source_priority": 10,
        "returns": "RasterOut",
        "artifact_kind": "raster_ref",
        "access_scope": "write_raster",
        "config_aware": True,
        "routable": True,
    },
)
def export_raster_copy(
    path: str,
    output_path: str | None = None,
    output_dir: str | None = None,
    filename: str | None = None,
    metadata: dict[str, Any] | None = None,
    overwrite: bool | None = None,
) -> RasterOut:
    """
    Copy/export a raster file to output location.

    Args:
        path:
            Input raster/image file path.
        output_path:
            Exact output file path.
        output_dir:
            Output directory if output_path is not provided.
        filename:
            Output filename if output_path is not provided.
        metadata:
            Optional metadata.
        overwrite:
            Whether to overwrite existing output.

    Returns:
        RasterOut referencing copied output path.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string.")

    config = _load_exporter_config()

    final_overwrite = bool(pick_first(overwrite, _configured_overwrite(config), default=True))
    allowed_roots = _configured_allowed_output_roots(config)
    allowed_extensions = _configured_raster_extensions(config)

    src = Path(path).expanduser().resolve()
    input_suffix = src.suffix.lower()

    if not input_suffix:
        raise ValueError("Input raster path must have a file extension.")

    if input_suffix not in allowed_extensions:
        raise ValueError(
            f"Unsupported raster input extension '{input_suffix}'. "
            f"Allowed extensions: {sorted(allowed_extensions)}"
        )

    output_file = _resolve_output_path(
        output_path=output_path,
        output_dir=output_dir,
        filename=filename,
        default_output_dir=_configured_output_dir(config),
        default_stem=src.stem or "raster_export",
        extension=input_suffix,
        allowed_output_roots=allowed_roots,
    )

    suffix = _validate_extension(output_file, allowed_extensions, "raster")

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    _copy_file(src, output_file, overwrite=final_overwrite)

    export_metadata = {
        "source": "data_writer_exporter",
        "loader": PLUGIN_ID,
        "format": suffix.lstrip("."),
        "extension": suffix,
        "input_path": str(src),
        "path": str(output_file),
        "filename": output_file.name,
        "exported_at": _utc_now_iso(),
        "overwrite": final_overwrite,
        "file_size_bytes": int(output_file.stat().st_size),
        **user_metadata,
    }

    return RasterOut(
        path=str(output_file),
        metadata=export_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Data Writer Exporter",
    description=(
        "Exports vector features to GeoJSON and copies raster/image files to configured "
        "output locations for the GeoChat spatial pipeline."
    ),
    author="GeoChat Platform Team",
    permissions=["filesystem"],
)
