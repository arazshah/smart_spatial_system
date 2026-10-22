"""
geo_exporter.py

GeoChat SDK Plugin
==================

Plugin ID:
    geo_exporter

Capabilities:
    - export_vector_file:   write features to GeoPackage (.gpkg) or Shapefile (zipped).
    - export_raster_geotiff: write a raster to GeoTIFF, optionally as a Cloud-Optimized GeoTIFF.

Both write to the server filesystem, so they follow the same rules as the other exporters
(plugins/data_writer_exporter.py, tabular_exporter.py): a file name or directory resolved under
``allowed_output_roots`` of config/plugins/geo_exporter.yaml (empty = unrestricted, like the other
exporters), no overwrite unless asked, atomic rename into place, and size limits. Over MCP they are
in the ``filesystem`` risk class (hidden until the operator enables it).

Notes:
    - GeoPackage keeps full column names, mixed geometry types and a CRS.
    - Shapefile limits are real: column names are cut to 10 characters (collisions are reported),
      one geometry type per file, and nested values are written as JSON text. The result is a ZIP
      holding the .shp/.shx/.dbf/.prj/.cpg parts.
    - The CRS of the output is the ``crs`` argument (EPSG code, default 4326); coordinates are not
      transformed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect

from plugins._shared.raster_common import numpy_module, read_raster
from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    load_config,
    to_int,
    utc_now_iso,
    validate_choice,
)
from plugins.data_writer_exporter import (
    _configured_allowed_output_roots,
    _resolve_output_path,
)

PLUGIN_ID = "geo_exporter"

DEFAULTS = {"max_features": 500_000, "max_cells": 25_000_000}
VECTOR_FORMATS = {"gpkg": ".gpkg", "shp": ".zip"}
_EPSG_RE = re.compile(r"^(EPSG:)?(\d{4,6})$", re.IGNORECASE)
_LAYER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_META = {"category": "output", "access_scope": "file_export", "config_aware": True, "routable": True}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _epsg(crs: Any) -> int:
    match = _EPSG_RE.match(str(crs).strip())
    if not match:
        raise ValueError("crs must be an EPSG code such as 4326 or 'EPSG:32639'.")
    return int(match.group(2))


def _target_path(config, output_path, output_dir, filename, extension) -> Path:
    return _resolve_output_path(
        output_path=output_path,
        output_dir=output_dir,
        filename=filename,
        default_output_dir=str(config.get("default_output_dir") or "output/plugins/geo_exporter"),
        default_stem="export",
        extension=extension,
        allowed_output_roots=_configured_allowed_output_roots(config),
    )


def _prepare_destination(path: Path, extension: str, overwrite: bool) -> None:
    if path.suffix.lower() != extension:
        raise ValueError(f"The file name must end with {extension}, got '{path.suffix}'.")
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists and overwrite is False: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)


def _flatten(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list, tuple)) else value


@capability(
    name="export_vector_file",
    keywords=[
        "export geopackage", "export shapefile", "save as gpkg", "save as shp", "gis file export",
        "download shapefile", "write gpkg",
        "خروجی geopackage", "خروجی شیپ فایل", "ذخیره به صورت gpkg", "ذخیره شیپ فایل", "دانلود لایه",
    ],
    description=(
        "Write features to a GeoPackage (.gpkg) or a zipped Shapefile with the given CRS; never "
        "overwrites unless asked."
    ),
    required_inputs=["features"],
    optional_inputs=["format", "layer_name", "crs", "output_path", "output_dir", "filename", "overwrite",
                     "metadata"],
    output_kind="file",
    permissions=[],
    metadata={**_META, "data_type": "vector", "operation": "export_vector_file", "returns": "FileInfo",
              "artifact_kind": "file"},
)
def export_vector_file(
    features: Any,
    format: str = "gpkg",
    layer_name: str = "layer",
    crs: str | int = 4326,
    output_path: str | None = None,
    output_dir: str | None = None,
    filename: str | None = None,
    overwrite: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Export vector features.

    Args:
        features: Input features.
        format: ``gpkg`` or ``shp`` (written as a .zip).
        layer_name: Layer name inside a GeoPackage (letters, digits, underscore).
        crs: EPSG code of the coordinates (not transformed).
        output_path: Full target path (.gpkg or .zip).
        output_dir: Target directory when output_path is not given.
        filename: File name inside output_dir / the default directory.
        overwrite: Replace an existing file.
        metadata: Extra information echoed in the result.

    Returns:
        dict ``{"type": "file", "format", "path", "file_name", "feature_count", "crs", "size_bytes",
        "warnings": [...]}``.
    """
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise SDKDependencyError("export_vector_file requires geopandas: pip install geopandas") from exc
    config = load_config(PLUGIN_ID)
    fmt = validate_choice(format, set(VECTOR_FORMATS), "format")
    if not isinstance(layer_name, str) or not _LAYER_RE.match(layer_name):
        raise ValueError("layer_name must start with a letter or underscore and use only letters, digits, _.")
    epsg = _epsg(crs)
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")
    usable = [f for f in input_features if f.get("geometry")]
    if not usable:
        raise ValueError("No feature has a geometry; there is nothing to export as a GIS file.")

    extension = VECTOR_FORMATS[fmt]
    path = _target_path(config, output_path, output_dir, filename, extension)
    _prepare_destination(path, extension, bool(overwrite))

    warnings: list[str] = []
    skipped = len(input_features) - len(usable)
    if skipped:
        warnings.append(f"{skipped} feature(s) without geometry were not exported.")
    rows = [{"type": "Feature", "geometry": f["geometry"],
             "properties": {str(k): _flatten(v) for k, v in (f.get("properties") or {}).items()}}
            for f in usable]
    frame = gpd.GeoDataFrame.from_features(rows, crs=f"EPSG:{epsg}")

    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".tmp_") as scratch:
        scratch_dir = Path(scratch)
        if fmt == "gpkg":
            produced = scratch_dir / "out.gpkg"
            frame.to_file(produced, layer=layer_name, driver="GPKG")
            final_source = produced
        else:
            geometry_types = {t.replace("Multi", "") for t in frame.geom_type.dropna().unique()}
            if len(geometry_types) > 1:
                raise ValueError(
                    f"A shapefile holds one geometry type, but the data has {sorted(geometry_types)}; "
                    "filter or split the layer first, or export a GeoPackage."
                )
            used: dict[str, str] = {}
            renamed: dict[str, str] = {}
            for column in [c for c in frame.columns if c != "geometry"]:
                short = re.sub(r"[^A-Za-z0-9_]", "_", column)[:10] or "col"
                base, counter = short, 1
                while short in used:
                    suffix = str(counter)
                    short = base[: 10 - len(suffix)] + suffix
                    counter += 1
                used[short] = column
                if short != column:
                    renamed[column] = short
            if renamed:
                warnings.append(f"Column names were shortened to 10 characters: {renamed}")
            frame = frame.rename(columns=renamed)
            shp_dir = scratch_dir / "shp"
            shp_dir.mkdir()
            stem = re.sub(r"[^A-Za-z0-9_]", "_", path.stem)[:60] or "export"
            frame.to_file(shp_dir / f"{stem}.shp", driver="ESRI Shapefile", encoding="utf-8")
            final_source = scratch_dir / "out.zip"
            with zipfile.ZipFile(final_source, "w", zipfile.ZIP_DEFLATED) as archive:
                for part in sorted(shp_dir.iterdir()):
                    archive.write(part, part.name)
        os.replace(final_source, path)

    return {
        "type": "file",
        "format": fmt,
        "path": str(path),
        "file_name": path.name,
        "feature_count": len(rows),
        "crs": f"EPSG:{epsg}",
        "layer_name": layer_name if fmt == "gpkg" else None,
        "size_bytes": path.stat().st_size,
        "warnings": warnings,
        "metadata": {"source": PLUGIN_ID, "operation": "export_vector_file", "created_at": utc_now_iso(),
                     **source_info, **(metadata or {})},
    }


@capability(
    name="export_raster_geotiff",
    keywords=[
        "export geotiff", "save raster", "write tif", "cloud optimized geotiff", "cog", "export raster file",
        "خروجی geotiff", "ذخیره رستر", "ذخیره تیف", "خروجی رستر", "کوگ",
    ],
    description=(
        "Write a raster to a GeoTIFF (optionally a Cloud-Optimized GeoTIFF) with its CRS, transform "
        "and nodata; never overwrites unless asked. Needs rasterio."
    ),
    required_inputs=["raster"],
    optional_inputs=["cog", "dtype", "compress", "output_path", "output_dir", "filename", "overwrite",
                     "metadata"],
    output_kind="file",
    permissions=[],
    metadata={**_META, "data_type": "raster", "operation": "export_raster_geotiff", "returns": "FileInfo",
              "artifact_kind": "file"},
)
def export_raster_geotiff(
    raster: Any,
    cog: bool = False,
    dtype: str = "float32",
    compress: str = "deflate",
    output_path: str | None = None,
    output_dir: str | None = None,
    filename: str | None = None,
    overwrite: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Export a raster.

    Args:
        raster: Raster with ``transform`` and (recommended) ``crs`` in its metadata.
        cog: Write a Cloud-Optimized GeoTIFF (tiled with overviews) instead of a plain GeoTIFF.
        dtype: float32 | float64 | int16 | int32 | uint8 | uint16.
        compress: deflate | lzw | none.
        output_path: Full target path ending in .tif.
        output_dir: Target directory when output_path is not given.
        filename: File name inside output_dir / the default directory.
        overwrite: Replace an existing file.
        metadata: Extra information echoed in the result.

    Returns:
        dict ``{"type": "file", "format": "geotiff"|"cog", "path", "file_name", "width", "height",
        "band_count", "crs", "nodata", "size_bytes"}``.
    """
    try:
        import rasterio
        from rasterio.crs import CRS
        from rasterio.shutil import copy as rio_copy
        from rasterio.transform import Affine
    except ImportError as exc:
        raise SDKDependencyError("export_raster_geotiff requires rasterio: pip install rasterio") from exc
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    kind = validate_choice(dtype, {"float32", "float64", "int16", "int32", "uint8", "uint16"}, "dtype")
    codec = validate_choice(compress, {"deflate", "lzw", "none"}, "compress")
    array, meta, transform = read_raster(raster, max_cells=_limit(config, "max_cells"))
    crs_value = None
    if meta.get("crs"):
        try:
            crs_value = CRS.from_user_input(meta["crs"])
        except Exception as exc:
            raise ValueError(f"Invalid raster CRS: {exc}") from exc

    path = _target_path(config, output_path, output_dir, filename, ".tif")
    _prepare_destination(path, ".tif", bool(overwrite))

    integer = kind.startswith(("int", "uint"))
    nodata = meta.get("nodata")
    nodata_out = None
    if nodata is not None or np.isnan(array).any():
        nodata_out = float(nodata) if nodata is not None else (-9999.0 if not kind.startswith("uint") else 0.0)
    data = np.where(np.isnan(array), nodata_out if nodata_out is not None else 0.0, array)
    if integer:
        info = np.iinfo(kind)
        if data.size and (np.nanmin(data) < info.min or np.nanmax(data) > info.max):
            raise ValueError(f"Raster values do not fit {kind}; choose a wider dtype.")
        data = np.rint(data)
    data = data.astype(kind)

    a, b, c, d, e, f = transform
    profile = {
        "driver": "GTiff", "height": data.shape[1], "width": data.shape[2], "count": data.shape[0],
        "dtype": kind, "transform": Affine(a, b, c, d, e, f), "nodata": nodata_out,
        "tiled": bool(cog), "blockxsize": 256, "blockysize": 256,
    }
    if codec != "none":
        profile["compress"] = codec
    if crs_value is not None:
        profile["crs"] = crs_value
    if not cog:
        profile.pop("blockxsize"), profile.pop("blockysize")

    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".tmp_") as scratch:
        plain = Path(scratch) / "plain.tif"
        with rasterio.open(plain, "w", **profile) as dst:
            dst.write(data)
            if cog and min(data.shape[1:]) >= 512:
                dst.build_overviews([2, 4, 8], rasterio.enums.Resampling.average)
        final = plain
        if cog:
            final = Path(scratch) / "cog.tif"
            try:
                rio_copy(plain, final, driver="COG", compress=None if codec == "none" else codec.upper())
            except Exception:  # GDAL < 3.1 has no COG driver: keep the tiled GeoTIFF
                final = plain
        os.replace(final, path)

    return {
        "type": "file",
        "format": "cog" if cog else "geotiff",
        "path": str(path),
        "file_name": path.name,
        "width": int(data.shape[2]),
        "height": int(data.shape[1]),
        "band_count": int(data.shape[0]),
        "crs": crs_value.to_string() if crs_value is not None else None,
        "nodata": nodata_out,
        "dtype": kind,
        "size_bytes": path.stat().st_size,
        "metadata": {"source": PLUGIN_ID, "operation": "export_raster_geotiff", "created_at": utc_now_iso(),
                     **(metadata or {})},
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Geo Exporter",
    description="Exports features to GeoPackage / zipped Shapefile and rasters to GeoTIFF / COG.",
    author="GeoChat Platform Team",
    permissions=[],
)
