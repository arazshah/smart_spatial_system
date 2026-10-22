"""
tabular_exporter.py

GeoChat SDK Plugin
==================

Plugin ID:
    tabular_exporter

Capability:
    - export_table:
        Write feature attributes to a CSV, TSV or JSON file (optionally with the
        geometry as WKT or centroid x/y columns).

Config-aware behavior:
    Reads config/plugins/tabular_exporter.yaml - default_output_dir,
    allowed_output_roots (empty = unrestricted, as for data_writer_exporter),
    max_rows and file encoding. This plugin writes to the filesystem: when it is
    reached through MCP, the MCP layer additionally confines output_path /
    output_dir to its own allowed roots.

Safety:
    - CSV/TSV cells that start with = + - @ (or tab/CR) are prefixed with an
      apostrophe so spreadsheets do not run them as formulas (CSV injection);
      switch off with escape_formulas: false in the config.
    - Existing files are never overwritten unless overwrite=true.
    - File names are sanitised and the extension must match the format.
    - The file is written to a temporary name first and renamed into place.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    get_shapely,
    load_config,
    to_int,
    utc_now_iso,
    validate_choice,
)
from plugins.data_writer_exporter import (
    _configured_allowed_output_roots,
    _resolve_output_path,
)

PLUGIN_ID = "tabular_exporter"

FORMATS = {"csv": ".csv", "tsv": ".tsv", "json": ".json"}
GEOMETRY_MODES = {"none", "wkt", "centroid_xy"}
ENCODINGS = {"utf-8", "utf-8-sig"}
DEFAULT_MAX_ROWS = 1_000_000
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any, escape_formulas: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if escape_formulas and isinstance(value, str) and text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


@capability(
    name="export_table",
    keywords=[
        "export table", "export csv", "save as csv", "download table", "export attributes",
        "spreadsheet", "tsv", "export json table",
        "خروجی csv", "ذخیره جدول", "خروجی جدول", "اکسل", "دانلود جدول", "ذخیره به صورت csv",
    ],
    description=(
        "Export feature attributes to a CSV, TSV or JSON file, optionally with geometry as WKT "
        "or centroid coordinates. Spreadsheet-safe (formula escaping) and never overwrites "
        "unless asked."
    ),
    required_inputs=["features"],
    optional_inputs=[
        "format", "output_path", "output_dir", "filename", "include_geometry", "overwrite",
        "metadata",
    ],
    output_kind="file",
    permissions=[],
    metadata={
        "category": "output",
        "data_type": "vector",
        "operation": "export_table",
        "returns": "FileInfo",
        "artifact_kind": "file",
        "access_scope": "file_export",
        "config_aware": True,
        "routable": True,
    },
)
def export_table(
    features: Any,
    format: str = "csv",
    output_path: str | None = None,
    output_dir: str | None = None,
    filename: str | None = None,
    include_geometry: str = "none",
    overwrite: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Write a table file.

    Args:
        features: Input features (VectorOut, FeatureCollection, list...).
        format: csv | tsv | json.
        output_path: Full target path (extension must match the format).
        output_dir: Target directory when output_path is not given.
        filename: File name inside output_dir / the default directory.
        include_geometry: none | wkt | centroid_xy (adds ``geometry_wkt`` or ``centroid_x``/``centroid_y``).
        overwrite: Replace an existing file.
        metadata: Extra information echoed in the result.

    Returns:
        dict with ``type: "file"``, ``format``, ``path``, ``file_name``, ``row_count``,
        ``columns``, ``size_bytes``.
    """
    config = load_config(PLUGIN_ID)
    fmt = validate_choice(format, set(FORMATS), "format")
    geometry_mode = validate_choice(include_geometry, GEOMETRY_MODES, "include_geometry")
    encoding = str(config.get("encoding", "utf-8-sig"))
    if encoding not in ENCODINGS:
        raise ValueError(f"encoding in the tabular_exporter config must be one of {sorted(ENCODINGS)}.")
    escape_formulas = bool(config.get("escape_formulas", True))

    input_features, source_info = extract_features(features)
    check_limit(len(input_features), to_int(config.get("max_rows", DEFAULT_MAX_ROWS), "max_rows"),
                "row count")

    path = _resolve_output_path(
        output_path=output_path,
        output_dir=output_dir,
        filename=filename,
        default_output_dir=str(config.get("default_output_dir") or "output/plugins/tabular_exporter"),
        default_stem="table",
        extension=FORMATS[fmt],
        allowed_output_roots=_configured_allowed_output_roots(config),
    )
    if path.suffix.lower() != FORMATS[fmt]:
        raise ValueError(f"A {fmt} export needs a {FORMATS[fmt]} file name, got '{path.suffix}'.")
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists and overwrite is False: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)

    shape = get_shapely(PLUGIN_ID)[0] if geometry_mode != "none" else None
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    seen: set[str] = set()
    for feature in input_features:
        row = dict(feature.get("properties") or {})
        if shape is not None:
            geometry = feature.get("geometry")
            geom = shape(geometry) if geometry else None
            if geometry_mode == "wkt":
                row["geometry_wkt"] = geom.wkt if geom is not None and not geom.is_empty else None
            else:
                centroid = geom.centroid if geom is not None and not geom.is_empty else None
                row["centroid_x"] = centroid.x if centroid is not None else None
                row["centroid_y"] = centroid.y if centroid is not None else None
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(str(key))
        rows.append(row)

    if fmt == "json":
        payload = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t" if fmt == "tsv" else ",", lineterminator="\n")
        writer.writerow([_cell(c, escape_formulas) for c in columns])
        for row in rows:
            writer.writerow([_cell(row.get(c), escape_formulas) for c in columns])
        payload = buffer.getvalue()

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    return {
        "type": "file",
        "format": fmt,
        "path": str(path),
        "file_name": path.name,
        "row_count": len(rows),
        "columns": columns,
        "size_bytes": path.stat().st_size,
        "metadata": {
            "source": PLUGIN_ID,
            "operation": "export_table",
            "include_geometry": geometry_mode,
            "created_at": utc_now_iso(),
            **source_info,
            **(metadata or {}),
        },
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Tabular Exporter",
    description="Exports feature attributes to CSV, TSV or JSON files (spreadsheet-safe).",
    author="GeoChat Platform Team",
    permissions=[],
)
