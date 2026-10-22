"""
csv_xy_loader.py

GeoChat SDK Plugin
==================

Plugin ID:
    csv_xy_loader

Capability:
    - load_csv_points:
        Read a CSV/TSV table with coordinate columns (or a WKT column) and return point
        features, with a report of the rows that could not be used.

Connector:
    source_type ``csv_points`` (vector, filesystem access, no credentials).

Real-world CSV handling:
    - Encoding: a BOM means UTF-8; otherwise strict UTF-8 is tried first and then
      windows-1256 (Persian/Arabic Excel exports); override with ``encoding``.
    - Delimiter: sniffed from `,` `;` tab `|` unless given.
    - Persian/Arabic-Indic digits and separators in numbers are understood
      (``۳۵٫۶۸۹۲`` -> 35.6892).
    - Coordinate columns are auto-detected from common names (lon/lat, x/y, longitude/latitude,
      easting/northing, طول/عرض جغرافیایی...) or given as ``x_field`` / ``y_field``;
      ``wkt_field`` reads POINT/any WKT geometry instead.
    - Rows with missing or invalid coordinates are skipped and counted
      (``on_invalid="raise"`` to fail instead); ``sample_rejected`` rows are listed in metadata.

Safety:
    ``path`` goes through the shared local-path validation (allowed_roots, extension);
    file size, row count and field size are capped from config/plugins/csv_xy_loader.yaml.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.local_path_validation import validate_local_path
from plugins._shared.vector_common import (
    get_shapely,
    load_config,
    to_int,
    utc_now_iso,
    validate_choice,
)

PLUGIN_ID = "csv_xy_loader"

DEFAULTS = {"max_rows": 500_000, "max_bytes": 100_000_000, "max_text_chars": 1_000_000}
EXTENSIONS = {".csv", ".tsv", ".txt"}
ENCODINGS = {"utf-8", "utf-8-sig", "windows-1256", "cp1256", "cp1252", "latin-1"}
DELIMITERS = [",", ";", "\t", "|"]
X_NAMES = ["lon", "lng", "long", "longitude", "x", "easting", "lon_dd", "طول", "طول_جغرافیایی",
           "طول جغرافیایی", "longitude_deg"]
Y_NAMES = ["lat", "latitude", "y", "northing", "lat_dd", "عرض", "عرض_جغرافیایی",
           "عرض جغرافیایی", "latitude_deg"]
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_EPSG_RE = re.compile(r"^(EPSG:)?\d{4,6}$", re.IGNORECASE)


def parse_number(value: Any) -> float | None:
    """Parse a number written with ASCII, Persian or Arabic-Indic digits; None if not numeric."""
    if value is None:
        return None
    text = str(value).strip().translate(_DIGITS)
    text = text.replace("٫", ".").replace("٬", "").replace("٬", "").replace(" ", "")
    if text.count(",") == 1 and "." not in text:  # decimal comma, e.g. 35,6892
        text = text.replace(",", ".")
    elif "," in text:
        text = text.replace(",", "")
    if not _NUMBER_RE.match(text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


_ARABIC_TO_PERSIAN = str.maketrans("يك", "یک")


def _persianize(text: str) -> str:
    """windows-1256 has no Persian yeh/kaf, so Iranian exports use the Arabic letters; map them
    back so headers and values match their Persian spelling."""
    return text.translate(_ARABIC_TO_PERSIAN)


def decode_bytes(raw: bytes, encoding: str | None) -> tuple[str, str]:
    """Decode with an explicit encoding, or detect: BOM -> utf-8-sig, strict utf-8, windows-1256."""
    if encoding:
        choice = validate_choice(encoding, ENCODINGS, "encoding")
        text = raw.decode(choice)
        return (_persianize(text) if choice in {"windows-1256", "cp1256"} else text), choice
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return _persianize(raw.decode("windows-1256")), "windows-1256"
    except UnicodeDecodeError as exc:
        raise ValueError("Could not decode the file as UTF-8 or windows-1256; pass encoding.") from exc


def sniff_delimiter(text: str) -> str:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS))
        return dialect.delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample.splitlines() else ""
        return max(DELIMITERS, key=first.count)


def _pick(header: list[str], explicit: str | None, candidates: list[str], what: str) -> str:
    lookup = {h.strip().lower().replace(" ", "_"): h for h in header}
    if explicit:
        key = explicit.strip().lower().replace(" ", "_")
        if key in lookup:
            return lookup[key]
        raise ValueError(f"{what} column '{explicit}' not found. Columns: {header}")
    for candidate in candidates:
        key = candidate.lower().replace(" ", "_")
        if key in lookup:
            return lookup[key]
    raise ValueError(f"Could not detect the {what} column; pass it explicitly. Columns: {header}")


def _typed_columns(rows: list[dict[str, str]], header: list[str]) -> set[str]:
    """Columns whose non-empty values are all numbers (and not zero-padded identifiers)."""
    numeric: set[str] = set()
    for name in header:
        seen = False
        ok = True
        for row in rows:
            value = (row.get(name) or "").strip()
            if not value:
                continue
            seen = True
            ascii_value = value.translate(_DIGITS)
            if (parse_number(value) is None
                    or (len(ascii_value) > 1 and ascii_value[0] == "0" and ascii_value[1].isdigit())):
                ok = False
                break
        if seen and ok:
            numeric.add(name)
    return numeric


@capability(
    name="load_csv_points",
    keywords=[
        "csv", "load csv", "csv points", "xy data", "coordinates table", "lat lon table", "tsv",
        "spreadsheet points", "import table", "wkt column",
        "فایل csv", "بارگذاری csv", "جدول مختصات", "نقاط از csv", "طول و عرض جغرافیایی", "اکسل",
    ],
    description=(
        "Read a CSV/TSV table with longitude/latitude (or x/y, or WKT) columns into point "
        "features. Detects encoding (UTF-8 / windows-1256) and delimiter, understands Persian "
        "digits, and reports the rows it could not use."
    ),
    required_inputs=["path"],
    optional_inputs=[
        "x_field", "y_field", "wkt_field", "crs", "delimiter", "encoding", "max_rows",
        "on_invalid", "infer_types", "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "connector": {
            "source_type": "csv_points",
            "kind": "vector",
            "access": ["filesystem"],
            "credentials": "none",
            "limit_param": "max_rows",
        },
        "category": "data_io",
        "data_type": "vector",
        "operation": "load_csv_points",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "file_import",
        "config_aware": True,
        "routable": True,
    },
)
def load_csv_points(
    path: str,
    x_field: str | None = None,
    y_field: str | None = None,
    wkt_field: str | None = None,
    crs: str | int = "EPSG:4326",
    delimiter: str | None = None,
    encoding: str | None = None,
    max_rows: int | None = None,
    on_invalid: str = "skip",
    infer_types: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Load a CSV/TSV file as point features.

    Args:
        path: Local file (.csv/.tsv/.txt) under the configured allowed roots.
        x_field: Longitude/easting column (auto-detected when omitted).
        y_field: Latitude/northing column (auto-detected when omitted).
        wkt_field: Column holding WKT geometries; replaces x_field/y_field.
        crs: CRS label of the coordinates (EPSG code). WGS84 values are range-checked.
        delimiter: One character; sniffed when omitted.
        encoding: utf-8, utf-8-sig, windows-1256, cp1252 or latin-1; detected when omitted.
        max_rows: Keep at most this many valid rows (flagged ``truncated``).
        on_invalid: ``skip`` rows with bad coordinates (counted in metadata) or ``raise``.
        infer_types: Turn all-numeric columns into numbers (zero-padded ids stay text).
        metadata: Extra output metadata.

    Returns:
        VectorOut of Point (or WKT) features; metadata lists rejected rows.
    """
    config = load_config(PLUGIN_ID)
    mode = validate_choice(on_invalid, {"skip", "raise"}, "on_invalid")
    crs_text = str(crs).strip()
    if not _EPSG_RE.match(crs_text):
        raise ValueError("crs must be an EPSG code such as 'EPSG:4326' or 32639.")
    crs_label = crs_text.upper() if crs_text.upper().startswith("EPSG:") else f"EPSG:{crs_text}"
    geographic = crs_label == "EPSG:4326"

    file_path: Path = validate_local_path(
        path,
        label="CSV",
        default_extensions=EXTENSIONS,
        strict_extensions=True,
        allowed_extensions={str(e).lower() for e in config.get("allowed_extensions", EXTENSIONS)},
        allowed_roots=config.get("allowed_roots") or None,
    )
    size_limit = to_int(config.get("max_bytes", DEFAULTS["max_bytes"]), "max_bytes")
    if file_path.stat().st_size > size_limit:
        raise ValueError(f"The file is {file_path.stat().st_size} bytes; the limit is {size_limit}.")
    text, used_encoding = decode_bytes(file_path.read_bytes(), encoding)

    row_cap = to_int(config.get("max_rows", DEFAULTS["max_rows"]), "max_rows")
    keep = row_cap
    if max_rows is not None:
        keep = min(row_cap, to_int(max_rows, "max_rows"))
        if keep < 1:
            raise ValueError("max_rows must be >= 1.")

    if delimiter is not None and (not isinstance(delimiter, str) or len(delimiter) != 1):
        raise ValueError("delimiter must be a single character.")
    used_delimiter = delimiter or sniff_delimiter(text)
    csv.field_size_limit(1_000_000)
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=used_delimiter)
    header = [h.strip() for h in (reader.fieldnames or []) if h is not None]
    if not header:
        raise ValueError("The file has no header row.")
    reader.fieldnames = header

    if wkt_field:
        wkt_column = _pick(header, wkt_field, [], "WKT")
        x_column = y_column = None
    else:
        wkt_column = None
        x_column = _pick(header, x_field, X_NAMES, "x/longitude")
        y_column = _pick(header, y_field, Y_NAMES, "y/latitude")

    rows: list[dict[str, str]] = []
    for row_number, row in enumerate(reader, start=2):
        if len(rows) >= row_cap:
            raise ValueError(f"The file has more than {row_cap} rows (configured limit).")
        rows.append({**row, "__row__": row_number})
    numeric_columns = (
        _typed_columns(rows, [h for h in header if h not in {x_column, y_column, wkt_column}])
        if infer_types else set()
    )

    shapely_loads = None
    if wkt_column:
        get_shapely(PLUGIN_ID)
        from shapely import wkt as shapely_wkt
        from shapely.geometry import mapping

        shapely_loads = shapely_wkt.loads

    features: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    rejected_count = 0
    truncated = False
    for row in rows:
        row_number = row.pop("__row__")
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue  # blank line
        geometry: dict[str, Any] | None = None
        reason = None
        if wkt_column:
            try:
                geom = shapely_loads((row.get(wkt_column) or "").strip())
                geometry = None if geom.is_empty else dict(mapping(geom))
                reason = "empty geometry" if geometry is None else None
            except Exception:
                reason = "invalid WKT"
        else:
            x = parse_number(row.get(x_column))
            y = parse_number(row.get(y_column))
            if x is None or y is None:
                reason = "missing or non-numeric coordinate"
            elif geographic and not (-180 <= x <= 180 and -90 <= y <= 90):
                reason = "coordinate outside WGS84 range (are x and y swapped?)"
            else:
                geometry = {"type": "Point", "coordinates": [x, y]}
        if reason:
            if mode == "raise":
                raise ValueError(f"Row {row_number}: {reason}.")
            rejected_count += 1
            if len(rejected) < int(config.get("sample_rejected", 20)):
                rejected.append({"row": row_number, "reason": reason})
            continue
        if len(features) >= keep:
            truncated = True
            break
        properties: dict[str, Any] = {}
        for name in header:
            if name in {x_column, y_column, wkt_column}:
                continue
            value = row.get(name)
            if value is None:
                properties[name] = None
                continue
            value = value.strip()
            if value == "":
                properties[name] = None
            elif name in numeric_columns:
                number = parse_number(value)
                properties[name] = int(number) if number is not None and number.is_integer() \
                    and "." not in value.translate(_DIGITS) and "e" not in value.lower() else number
            else:
                properties[name] = value
        features.append({"type": "Feature", "geometry": geometry, "properties": properties})

    warnings = []
    if rows and not features and rejected_count:
        warnings.append("No usable rows; check the coordinate columns, crs and the swapped x/y case.")
    return VectorOut(
        features=features,
        metadata={
            "source": PLUGIN_ID,
            "loader": PLUGIN_ID,
            "operation": "load_csv_points",
            "file_name": file_path.name,
            "encoding": used_encoding,
            "delimiter": used_delimiter,
            "crs": crs_label,
            "x_field": x_column,
            "y_field": y_column,
            "wkt_field": wkt_column,
            "numeric_columns": sorted(numeric_columns),
            "row_count": len(rows),
            "feature_count": len(features),
            "rejected_count": rejected_count,
            "rejected_sample": rejected,
            "truncated": truncated,
            "warnings": warnings,
            "created_at": utc_now_iso(),
            **(metadata or {}),
        },
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="CSV XY Loader",
    description="Loads CSV/TSV tables with coordinate or WKT columns as point features.",
    author="GeoChat Platform Team",
    permissions=[],
)
