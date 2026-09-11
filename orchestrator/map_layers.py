"""
orchestrator.map_layers

Map layer extraction/building utilities for frontend map viewers.

MVP strategy:
    1. Try to find raw GeoJSON FeatureCollections in stored request record.
    2. If not found, derive NDVI threshold cell polygons from the original input raster.

The output is frontend-friendly and Leaflet-ready:
    - GeoJSON
    - EPSG:4326
    - feature_count
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, is_dataclass
from typing import Any

WEB_MERCATOR_RADIUS = 6378137.0


class MapLayerBuilder:
    """
    Build map layers from stored OrchestratorService request records.
    """

    def build_for_request_record(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = record.get("request_id")

        warnings: list[str] = []

        layers = self._extract_geojson_layers(record)

        if not layers:
            derived = self._derive_ndvi_cell_polygons(record)

            if derived:
                layers.append(derived)
            else:
                warnings.append(
                    "No raw GeoJSON/map layers were found and no MVP-derived layer could be built."
                )

        normalized_layers = []

        for layer in layers:
            normalized = self._normalize_layer(layer)

            if normalized is not None:
                normalized_layers.append(normalized)

        return {
            "request_id": request_id,
            "layers": normalized_layers,
            "layer_count": len(normalized_layers),
            "warnings": warnings,
        }

    def _extract_geojson_layers(
        self,
        value: Any,
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        seen_signatures: set[str] = set()

        def walk(obj: Any, path: str, depth: int) -> None:
            if depth > 10:
                return

            obj_id = id(obj)

            if obj_id in seen_ids:
                return

            seen_ids.add(obj_id)

            payload = _to_plain(obj)

            if isinstance(payload, dict):
                feature_collection = self._feature_collection_from_payload(payload)

                if feature_collection is not None:
                    signature = repr(feature_collection)[:2000]

                    if signature not in seen_signatures:
                        seen_signatures.add(signature)

                        name = (
                            payload.get("name")
                            or payload.get("layer_name")
                            or _last_path_name(path)
                            or "map_layer"
                        )

                        crs = self._extract_crs(payload)

                        found.append(
                            {
                                "name": str(name),
                                "kind": "vector",
                                "crs": crs,
                                "geojson": feature_collection,
                                "source": "raw_record",
                            }
                        )

                for key, item in payload.items():
                    walk(item, f"{path}.{key}", depth + 1)

            elif isinstance(payload, list):
                for index, item in enumerate(payload):
                    walk(item, f"{path}[{index}]", depth + 1)

        walk(value, "record", 0)

        return found

    @staticmethod
    def _feature_collection_from_payload(
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (
            payload.get("type") == "FeatureCollection"
            and isinstance(payload.get("features"), list)
        ):
            return {
                "type": "FeatureCollection",
                "features": payload["features"],
            }

        if (
            payload.get("format") == "FeatureCollection"
            and isinstance(payload.get("features"), list)
        ):
            return {
                "type": "FeatureCollection",
                "features": payload["features"],
            }

        geojson = payload.get("geojson")

        if (
            isinstance(geojson, dict)
            and geojson.get("type") == "FeatureCollection"
            and isinstance(geojson.get("features"), list)
        ):
            return {
                "type": "FeatureCollection",
                "features": geojson["features"],
            }

        return None

    @staticmethod
    def _extract_crs(
        payload: dict[str, Any],
    ) -> str:
        metadata = payload.get("metadata")

        if isinstance(metadata, dict) and metadata.get("crs"):
            return str(metadata["crs"])

        crs = payload.get("crs")

        if isinstance(crs, str):
            return crs

        if isinstance(crs, dict):
            props = crs.get("properties")

            if isinstance(props, dict) and props.get("name"):
                return str(props["name"])

        return "EPSG:4326"

    def _normalize_layer(
        self,
        layer: dict[str, Any],
    ) -> dict[str, Any] | None:
        geojson = layer.get("geojson")

        if not isinstance(geojson, dict):
            return None

        if geojson.get("type") != "FeatureCollection":
            return None

        features = geojson.get("features")

        if not isinstance(features, list):
            return None

        source_crs = str(layer.get("crs") or "EPSG:4326")
        target_crs = "EPSG:4326"

        normalized_geojson = geojson

        if not _is_epsg_4326(source_crs):
            normalized_geojson = _transform_feature_collection_to_4326(
                geojson,
                source_crs,
            )

        return {
            "name": str(layer.get("name") or "map_layer"),
            "kind": str(layer.get("kind") or "vector"),
            "crs": target_crs,
            "source_crs": source_crs,
            "feature_count": len(normalized_geojson.get("features", [])),
            "geojson": normalized_geojson,
            "source": layer.get("source") or "unknown",
        }

    def _derive_ndvi_cell_polygons(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        MVP fallback:
        Derive vegetation polygons from original raster and band_map when:
            - query asks for NDVI
            - raster input exists
            - red/nir bands exist
            - threshold can be parsed or defaults to 0.3
        """
        query = str(record.get("query") or "")

        if "ndvi" not in query.lower() and "NDVI" not in query:
            return None

        inputs = record.get("inputs")

        if not isinstance(inputs, dict):
            return None

        raster = inputs.get("raster")

        if not isinstance(raster, dict):
            return None

        data = raster.get("data")
        metadata = raster.get("metadata") or {}

        if not isinstance(data, list) or len(data) < 2:
            return None

        band_map = record.get("band_map") or {}

        if not isinstance(band_map, dict):
            return None

        red_index = int(band_map.get("red", 1)) - 1
        nir_index = int(band_map.get("nir", 2)) - 1

        if red_index < 0 or nir_index < 0:
            return None

        try:
            red_band = data[red_index]
            nir_band = data[nir_index]
        except Exception:
            return None

        if not isinstance(red_band, list) or not isinstance(nir_band, list):
            return None

        transform = metadata.get("transform") or [1, 0, 0, 0, -1, 0]
        crs = str(metadata.get("crs") or "EPSG:4326")
        threshold = _parse_threshold(query, default=0.3)

        features = []

        height = min(len(red_band), len(nir_band))

        for row in range(height):
            red_row = red_band[row]
            nir_row = nir_band[row]

            if not isinstance(red_row, list) or not isinstance(nir_row, list):
                continue

            width = min(len(red_row), len(nir_row))

            for col in range(width):
                try:
                    red = float(red_row[col])
                    nir = float(nir_row[col])
                except Exception:
                    continue

                denominator = nir + red

                if denominator == 0:
                    continue

                ndvi = (nir - red) / denominator

                if ndvi <= threshold:
                    continue

                polygon = _cell_polygon_from_transform(
                    row=row,
                    col=col,
                    transform=transform,
                )

                geometry = {
                    "type": "Polygon",
                    "coordinates": [polygon],
                }

                if not _is_epsg_4326(crs):
                    geometry = _transform_geometry_to_4326(geometry, crs)

                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "row": row,
                            "col": col,
                            "ndvi": round(ndvi, 6),
                            "threshold": threshold,
                            "source": "mvp_derived_from_input_raster",
                        },
                        "geometry": geometry,
                    }
                )

        if not features:
            return None

        return {
            "name": "vegetation_polygons",
            "kind": "vector",
            "crs": "EPSG:4326",
            "geojson": {
                "type": "FeatureCollection",
                "features": features,
            },
            "source": "mvp_derived_from_input_raster",
        }


def _parse_threshold(
    query: str,
    *,
    default: float,
) -> float:
    patterns = [
        r"بیشتر از\s*([0-9]+(?:\.[0-9]+)?)",
        r"greater than\s*([0-9]+(?:\.[0-9]+)?)",
        r">\s*([0-9]+(?:\.[0-9]+)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)

        if match:
            try:
                return float(match.group(1))
            except Exception:
                pass

    return default


def _cell_polygon_from_transform(
    *,
    row: int,
    col: int,
    transform: list[Any],
) -> list[list[float]]:
    """
    Affine transform convention:
        x = a * col + b * row + c
        y = d * col + e * row + f

    transform:
        [a, b, c, d, e, f]
    """
    a, b, c, d, e, f = [float(item) for item in transform[:6]]

    def xy(pixel_col: float, pixel_row: float) -> list[float]:
        x = a * pixel_col + b * pixel_row + c
        y = d * pixel_col + e * pixel_row + f
        return [x, y]

    p1 = xy(col, row)
    p2 = xy(col + 1, row)
    p3 = xy(col + 1, row + 1)
    p4 = xy(col, row + 1)

    return [p1, p2, p3, p4, p1]


def _is_epsg_4326(crs: str) -> bool:
    text = str(crs).upper()
    return "4326" in text or "WGS84" in text or "WGS 84" in text


def _transform_feature_collection_to_4326(
    feature_collection: dict[str, Any],
    source_crs: str,
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            _transform_feature_to_4326(feature, source_crs)
            for feature in feature_collection.get("features", [])
            if isinstance(feature, dict)
        ],
    }


def _transform_feature_to_4326(
    feature: dict[str, Any],
    source_crs: str,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": dict(feature.get("properties") or {}),
        "geometry": _transform_geometry_to_4326(
            feature.get("geometry") or {},
            source_crs,
        ),
    }


def _transform_geometry_to_4326(
    geometry: dict[str, Any],
    source_crs: str,
) -> dict[str, Any]:
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if coordinates is None:
        return geometry

    return {
        "type": geom_type,
        "coordinates": _transform_coordinates_to_4326(
            coordinates,
            source_crs,
        ),
    }


def _transform_coordinates_to_4326(
    coordinates: Any,
    source_crs: str,
) -> Any:
    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and isinstance(coordinates[0], (int, float))
        and isinstance(coordinates[1], (int, float))
    ):
        return _point_to_4326(
            float(coordinates[0]),
            float(coordinates[1]),
            source_crs,
        )

    if isinstance(coordinates, list):
        return [
            _transform_coordinates_to_4326(item, source_crs)
            for item in coordinates
        ]

    return coordinates


def _point_to_4326(
    x: float,
    y: float,
    source_crs: str,
) -> list[float]:
    text = str(source_crs).upper()

    if "3857" in text or "WEB" in text or "MERCATOR" in text:
        lon = (x / WEB_MERCATOR_RADIUS) * 180.0 / math.pi
        lat = (
            2.0 * math.atan(math.exp(y / WEB_MERCATOR_RADIUS))
            - math.pi / 2.0
        ) * 180.0 / math.pi

        return [round(lon, 8), round(lat, 8)]

    # Unknown CRS fallback:
    # Return as-is to avoid silently corrupting data.
    return [x, y]


def _to_plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return value

    if isinstance(value, list):
        return value

    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return value.to_dict()
        except Exception:
            pass

    if is_dataclass(value):
        try:
            return asdict(value)
        except Exception:
            pass

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict):
        return payload

    return value


def _last_path_name(path: str) -> str:
    cleaned = path.replace("]", "").split(".")[-1]
    cleaned = cleaned.split("[")[0]
    return cleaned or "map_layer"
