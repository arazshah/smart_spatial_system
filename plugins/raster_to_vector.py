"""
raster_to_vector.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_to_vector

Purpose:
    Convert selected raster pixels/classes into vector polygon features.

Capability:
    - raster_to_vector

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Vectorization modes:
    - cells:
        each selected raster cell becomes one polygon feature.

    - components:
        connected selected cells become grouped features.
        In this dependency-free implementation, each component geometry is
        represented by a bounding-box polygon.

No external dependency is required.
"""

from __future__ import annotations

import math
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs
from plugins.raster_clip_mask import (
    _array_shape,
    _extract_raster,
    _is_geographic_crs,
)


PLUGIN_ID = "raster_to_vector"

VALID_ENGINES = {"python", "auto"}
VALID_MODES = {"cells", "components"}
VALID_CONNECTIVITY = {4, 8}

EPSILON = 1e-12


def _load_raster_to_vector_config() -> dict[str, Any]:
    """
    Load config/plugins/raster_to_vector.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    """
    Return current UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def _validate_engine(engine: str) -> str:
    """
    Validate execution engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    value = engine.strip().lower()

    if value not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return value


def _validate_mode(mode: str) -> str:
    """
    Validate vectorization mode.
    """
    if not isinstance(mode, str) or not mode.strip():
        raise ValueError("mode must be a non-empty string.")

    value = mode.strip().lower()

    if value not in VALID_MODES:
        raise ValueError(f"Unsupported mode '{mode}'. Valid modes: {sorted(VALID_MODES)}")

    return value


def _validate_connectivity(value: Any) -> int:
    """
    Validate connectivity value.
    """
    if isinstance(value, bool):
        raise ValueError("connectivity must be 4 or 8.")

    try:
        connectivity = int(value)
    except Exception as exc:
        raise ValueError("connectivity must be 4 or 8.") from exc

    if connectivity not in VALID_CONNECTIVITY:
        raise ValueError("connectivity must be 4 or 8.")

    return connectivity


def _validate_precision(value: Any) -> int | None:
    """
    Validate coordinate precision.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("precision must be an integer or None.")

    try:
        precision = int(value)
    except Exception as exc:
        raise ValueError("precision must be an integer or None.") from exc

    if precision < 0 or precision > 15:
        raise ValueError("precision must be between 0 and 15.")

    return precision


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return configured precision.
    """
    return _validate_precision(config.get("coordinate_precision", 6))


def _round_value(value: Any, precision: int | None) -> Any:
    """
    Round float values.
    """
    if value is None:
        return None

    if not isinstance(value, float):
        return value

    if precision is None:
        return value

    return round(value, precision)


def _round_coord_pair(pair: tuple[float, float], precision: int | None) -> list[float]:
    """
    Round coordinate pair.
    """
    return [
        _round_value(float(pair[0]), precision),
        _round_value(float(pair[1]), precision),
    ]


def _is_number(value: Any) -> bool:
    """
    Return True for finite numbers excluding bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_nan(value: Any) -> bool:
    """
    Return True if value is NaN.
    """
    try:
        return isinstance(value, float) and math.isnan(value)
    except Exception:
        return False


def _is_nodata(value: Any, nodata: Any) -> bool:
    """
    Check nodata logic.
    """
    if value is None:
        return True

    if _is_nan(value):
        return True

    if nodata is None:
        return False

    if _is_nan(nodata):
        return _is_nan(value)

    try:
        return value == nodata
    except Exception:
        return False


def _values_equal(a: Any, b: Any) -> bool:
    """
    Equality helper with numeric tolerance.
    """
    if _is_number(a) and _is_number(b):
        return abs(float(a) - float(b)) <= EPSILON

    try:
        return a == b
    except Exception:
        return False


def _normalize_value_list(values: Any, *, name: str, allow_none: bool) -> list[Any] | None:
    """
    Normalize include/exclude value list.
    """
    if values is None:
        if allow_none:
            return None
        return []

    if isinstance(values, (str, bytes)):
        return [values]

    if isinstance(values, (list, tuple, set)):
        return list(values)

    return [values]


def _validate_band_index(value: Any, band_count: int) -> int:
    """
    Validate 1-based band index.
    """
    if isinstance(value, bool):
        raise ValueError("band_index must be a positive integer.")

    try:
        band_index = int(value)
    except Exception as exc:
        raise ValueError("band_index must be a positive integer.") from exc

    if band_index <= 0:
        raise ValueError("band_index must be >= 1.")

    if band_index > band_count:
        raise ValueError(f"band_index {band_index} is out of range. Raster has {band_count} band(s).")

    return band_index


def _validate_resolution(value: Any, *, name: str) -> float:
    """
    Validate positive pixel resolution.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive number.")

    try:
        result = float(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a positive number.") from exc

    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number.")

    return result


def _validate_max_features(value: Any) -> int | None:
    """
    Validate max_features.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("max_features must be a positive integer or None.")

    try:
        result = int(value)
    except Exception as exc:
        raise ValueError("max_features must be a positive integer or None.") from exc

    if result <= 0:
        raise ValueError("max_features must be a positive integer or None.")

    return result


def _as_bool(value: Any, default: bool) -> bool:
    """
    Convert common bool-like values.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False

    return bool(value)


def _band_value(data: Any, *, band_index: int, row: int, col: int) -> Any:
    """
    Read 1-based raster band value.
    """
    bands, _height, _width = _array_shape(data)

    if bands == 1:
        # 2D raster
        if data and isinstance(data[0], list) and (not data[0] or not isinstance(data[0][0], list)):
            return data[row][col]

        # 3D single-band raster
        return data[0][row][col]

    return data[band_index - 1][row][col]


def _extract_transform(
    metadata: dict[str, Any],
    *,
    default_origin_x: Any,
    default_origin_y: Any,
    default_x_resolution: Any,
    default_y_resolution: Any,
) -> tuple[list[float], str]:
    """
    Extract affine-like transform.

    Expected transform:
        [a, b, c, d, e, f]

    Coordinate:
        x = a * col + b * row + c
        y = d * col + e * row + f

    If transform is missing, a north-up default transform is built:
        [x_res, 0, origin_x, 0, -y_res, origin_y]
    """
    transform = metadata.get("transform")

    if isinstance(transform, (list, tuple)) and len(transform) >= 6:
        try:
            values = [float(transform[i]) for i in range(6)]
            return values, "metadata_transform"
        except Exception:
            pass

    x_res = _validate_resolution(default_x_resolution, name="x_resolution")
    y_res = _validate_resolution(default_y_resolution, name="y_resolution")

    try:
        origin_x = float(default_origin_x)
        origin_y = float(default_origin_y)
    except Exception as exc:
        raise ValueError("default origin values must be numeric.") from exc

    return [x_res, 0.0, origin_x, 0.0, -y_res, origin_y], "default_transform"


def _pixel_corner(
    *,
    transform: list[float],
    row: int,
    col: int,
) -> tuple[float, float]:
    """
    Convert pixel corner row/col to map coordinate.
    """
    a, b, c, d, e, f = transform
    x = a * col + b * row + c
    y = d * col + e * row + f
    return x, y


def _cell_polygon(
    *,
    transform: list[float],
    row: int,
    col: int,
    precision: int | None,
) -> dict[str, Any]:
    """
    Build GeoJSON polygon for one raster cell.
    """
    p1 = _pixel_corner(transform=transform, row=row, col=col)
    p2 = _pixel_corner(transform=transform, row=row, col=col + 1)
    p3 = _pixel_corner(transform=transform, row=row + 1, col=col + 1)
    p4 = _pixel_corner(transform=transform, row=row + 1, col=col)

    ring = [
        _round_coord_pair(p1, precision),
        _round_coord_pair(p2, precision),
        _round_coord_pair(p3, precision),
        _round_coord_pair(p4, precision),
        _round_coord_pair(p1, precision),
    ]

    return {
        "type": "Polygon",
        "coordinates": [ring],
    }


def _bbox_polygon_for_cells(
    *,
    transform: list[float],
    cells: list[tuple[int, int]],
    precision: int | None,
) -> dict[str, Any]:
    """
    Build bounding-box polygon for a set of cells.
    """
    min_row = min(row for row, _col in cells)
    max_row = max(row for row, _col in cells)
    min_col = min(col for _row, col in cells)
    max_col = max(col for _row, col in cells)

    p1 = _pixel_corner(transform=transform, row=min_row, col=min_col)
    p2 = _pixel_corner(transform=transform, row=min_row, col=max_col + 1)
    p3 = _pixel_corner(transform=transform, row=max_row + 1, col=max_col + 1)
    p4 = _pixel_corner(transform=transform, row=max_row + 1, col=min_col)

    ring = [
        _round_coord_pair(p1, precision),
        _round_coord_pair(p2, precision),
        _round_coord_pair(p3, precision),
        _round_coord_pair(p4, precision),
        _round_coord_pair(p1, precision),
    ]

    return {
        "type": "Polygon",
        "coordinates": [ring],
    }


def _value_is_selected(
    value: Any,
    *,
    include_values: list[Any] | None,
    exclude_values: list[Any],
    nodata: Any,
) -> bool:
    """
    Check whether a raster value should be vectorized.
    """
    if _is_nodata(value, nodata):
        return False

    for excluded in exclude_values:
        if _values_equal(value, excluded):
            return False

    if include_values is None:
        return True

    return any(_values_equal(value, included) for included in include_values)


def _selected_grid(
    data: Any,
    *,
    band_index: int,
    height: int,
    width: int,
    include_values: list[Any] | None,
    exclude_values: list[Any],
    nodata: Any,
) -> list[list[bool]]:
    """
    Build boolean selected grid.
    """
    grid: list[list[bool]] = []

    for row in range(height):
        out_row: list[bool] = []

        for col in range(width):
            value = _band_value(data, band_index=band_index, row=row, col=col)
            out_row.append(
                _value_is_selected(
                    value,
                    include_values=include_values,
                    exclude_values=exclude_values,
                    nodata=nodata,
                )
            )

        grid.append(out_row)

    return grid


def _neighbors(
    *,
    row: int,
    col: int,
    height: int,
    width: int,
    connectivity: int,
) -> list[tuple[int, int]]:
    """
    Return valid neighboring cells.
    """
    deltas_4 = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    deltas_8 = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ]

    deltas = deltas_8 if connectivity == 8 else deltas_4

    result: list[tuple[int, int]] = []

    for d_row, d_col in deltas:
        n_row = row + d_row
        n_col = col + d_col

        if 0 <= n_row < height and 0 <= n_col < width:
            result.append((n_row, n_col))

    return result


def _connected_components(
    selected: list[list[bool]],
    *,
    connectivity: int,
) -> list[list[tuple[int, int]]]:
    """
    Extract connected components from selected grid.
    """
    height = len(selected)
    width = len(selected[0]) if height else 0

    visited = [[False for _ in range(width)] for _ in range(height)]
    components: list[list[tuple[int, int]]] = []

    for row in range(height):
        for col in range(width):
            if visited[row][col] or not selected[row][col]:
                continue

            component: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque()
            queue.append((row, col))
            visited[row][col] = True

            while queue:
                cur_row, cur_col = queue.popleft()
                component.append((cur_row, cur_col))

                for n_row, n_col in _neighbors(
                    row=cur_row,
                    col=cur_col,
                    height=height,
                    width=width,
                    connectivity=connectivity,
                ):
                    if visited[n_row][n_col] or not selected[n_row][n_col]:
                        continue

                    visited[n_row][n_col] = True
                    queue.append((n_row, n_col))

            components.append(component)

    return components


def _make_feature(
    *,
    feature_id: int,
    geometry: dict[str, Any],
    properties: dict[str, Any],
) -> dict[str, Any]:
    """
    Build GeoJSON-like Feature.
    """
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": geometry,
        "properties": properties,
    }


def _make_vector_output(
    *,
    features: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """
    Dependency-free vector output.

    Kept as a plain dict to avoid coupling with a specific VectorOut constructor.
    """
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": metadata,
    }


@capability(
    name="raster_to_vector",
    keywords=[
        "raster to vector",
        "polygonize raster",
        "raster polygonize",
        "mask to polygon",
        "mask to vector",
        "ndvi polygon",
        "water mask polygon",
        "vectorize raster",
        "تبدیل رستر به وکتور",
        "پلیگون سازی رستر",
        "تبدیل ماسک به پلیگون",
        "وکتور سازی رستر",
    ],
    description="Convert selected raster pixels/classes into vector polygon features.",
    required_inputs=["raster"],
    optional_inputs=[
        "band_index",
        "include_values",
        "exclude_values",
        "mode",
        "connectivity",
        "nodata",
        "engine",
        "precision",
        "source_crs",
        "metadata",
        "include_pixel_properties",
        "include_component_cells",
        "max_features",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "conversion",
        "data_type": "raster_vector",
        "operation": "raster_to_vector",
        "returns": "FeatureCollection",
        "artifact_kind": "vector",
        "access_scope": "raster_conversion",
        "config_aware": True,
        "polygonize_supported": True,
        "component_mode_supported": True,
        "routable": True,
    },
)
def raster_to_vector(
    raster: Any,
    band_index: int | None = None,
    include_values: list[Any] | Any | None = None,
    exclude_values: list[Any] | Any | None = None,
    mode: str | None = None,
    connectivity: int | None = None,
    nodata: Any = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
    include_pixel_properties: bool | None = None,
    include_component_cells: bool | None = None,
    max_features: int | None = None,
) -> dict[str, Any]:
    """
    Convert selected raster pixels/classes to vector polygon features.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        band_index:
            1-based band index.
        include_values:
            Values to vectorize. If None, config default_include_values is used.
            If final include_values is None, all valid non-nodata values are vectorized.
        exclude_values:
            Values to exclude from vectorization.
        mode:
            cells | components.
        connectivity:
            4 | 8 for component mode.
        nodata:
            Input nodata value.
        engine:
            python | auto.
        precision:
            Coordinate rounding precision.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.
        include_pixel_properties:
            Add row/col/value to cell features.
        include_component_cells:
            Add component cell list to component properties.
        max_features:
            Optional maximum number of output features.

    Returns:
        GeoJSON-like FeatureCollection dict:
            {
                "type": "FeatureCollection",
                "features": [...],
                "metadata": {...}
            }
    """
    config = _load_raster_to_vector_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    final_mode = _validate_mode(
        str(pick_first(mode, config.get("default_mode"), default="cells"))
    )

    final_connectivity = _validate_connectivity(
        pick_first(connectivity, config.get("default_connectivity"), default=4)
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_band_index = _validate_band_index(
        pick_first(band_index, config.get("default_band_index"), default=1),
        band_count=band_count,
    )

    final_nodata = pick_first(
        nodata,
        input_metadata.get("nodata"),
        config.get("default_nodata"),
        default=None,
    )

    include_candidate = include_values
    if include_candidate is None and "default_include_values" in config:
        include_candidate = config.get("default_include_values")

    final_include_values = _normalize_value_list(
        include_candidate,
        name="include_values",
        allow_none=True,
    )

    exclude_candidate = exclude_values
    if exclude_candidate is None:
        exclude_candidate = config.get("default_exclude_values", [])

    final_exclude_values = _normalize_value_list(
        exclude_candidate,
        name="exclude_values",
        allow_none=False,
    ) or []

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    transform, transform_source = _extract_transform(
        input_metadata,
        default_origin_x=config.get("default_origin_x", 0.0),
        default_origin_y=config.get("default_origin_y", 0.0),
        default_x_resolution=config.get("default_x_resolution", 1.0),
        default_y_resolution=config.get("default_y_resolution", 1.0),
    )

    preserve_metadata = bool(config.get("preserve_metadata", True))

    final_source_crs = pick_first(
        source_crs,
        input_metadata.get("crs"),
        config.get("source_crs"),
        default=None,
    )

    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    final_include_pixel_properties = _as_bool(
        pick_first(include_pixel_properties, config.get("include_pixel_properties"), default=True),
        default=True,
    )

    final_include_component_cells = _as_bool(
        pick_first(include_component_cells, config.get("include_component_cells"), default=False),
        default=False,
    )

    final_max_features = _validate_max_features(
        pick_first(max_features, config.get("max_features"), default=None)
    )

    selected = _selected_grid(
        data,
        band_index=final_band_index,
        height=height,
        width=width,
        include_values=final_include_values,
        exclude_values=final_exclude_values,
        nodata=final_nodata,
    )

    selected_pixel_count = sum(1 for row in selected for value in row if value)

    features: list[dict[str, Any]] = []
    feature_id = 1
    truncated = False

    if final_mode == "cells":
        for row in range(height):
            for col in range(width):
                if not selected[row][col]:
                    continue

                if final_max_features is not None and len(features) >= final_max_features:
                    truncated = True
                    break

                value = _band_value(data, band_index=final_band_index, row=row, col=col)

                properties: dict[str, Any] = {
                    "value": value,
                    "class_value": value,
                }

                if final_include_pixel_properties:
                    properties.update(
                        {
                            "row": row,
                            "col": col,
                            "pixel_id": f"r{row}_c{col}",
                        }
                    )

                features.append(
                    _make_feature(
                        feature_id=feature_id,
                        geometry=_cell_polygon(
                            transform=transform,
                            row=row,
                            col=col,
                            precision=final_precision,
                        ),
                        properties=properties,
                    )
                )
                feature_id += 1

            if truncated:
                break

    else:
        components = _connected_components(selected, connectivity=final_connectivity)

        for component_index, cells in enumerate(components, start=1):
            if final_max_features is not None and len(features) >= final_max_features:
                truncated = True
                break

            first_row, first_col = cells[0]
            first_value = _band_value(
                data,
                band_index=final_band_index,
                row=first_row,
                col=first_col,
            )

            properties = {
                "component_id": component_index,
                "value": first_value,
                "class_value": first_value,
                "pixel_count": len(cells),
                "bbox_mode": True,
            }

            if final_include_component_cells:
                properties["cells"] = [
                    {"row": row, "col": col}
                    for row, col in cells
                ]

            features.append(
                _make_feature(
                    feature_id=feature_id,
                    geometry=_bbox_polygon_for_cells(
                        transform=transform,
                        cells=cells,
                        precision=final_precision,
                    ),
                    properties=properties,
                )
            )
            feature_id += 1

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Raster-to-vector conversion is being performed on a geographic CRS. "
            "Generated coordinates are valid, but area/length calculations may require reprojection."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    output_metadata = {
        **base_metadata,
        "source": "raster_to_vector",
        "loader": PLUGIN_ID,
        "operation": "raster_to_vector",
        "engine_requested": final_engine,
        "engine_used": "python",
        "input_band_count": band_count,
        "selected_band_index": final_band_index,
        "width": width,
        "height": height,
        "mode": final_mode,
        "connectivity": final_connectivity,
        "include_values": final_include_values,
        "exclude_values": final_exclude_values,
        "nodata": final_nodata,
        "selected_pixel_count": selected_pixel_count,
        "feature_count": len(features),
        "truncated": truncated,
        "max_features": final_max_features,
        "transform": transform,
        "transform_source": transform_source,
        "coordinate_precision": final_precision,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "created_at": _utc_now_iso(),
        **source_info,
        **user_metadata,
    }

    return _make_vector_output(
        features=features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Raster To Vector",
    description=(
        "Converts selected raster pixels/classes into GeoJSON-like polygon features. "
        "Useful for turning raster masks such as NDVI, NDWI, slope, and classified rasters into vectors."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
