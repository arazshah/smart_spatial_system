"""
orchestrator.kernel_artifacts

Compatibility helpers between the current smart_spatial_system orchestrator
and geochat_kernel artifact models.

Important
---------
This module does not replace the current runtime.
It provides a small, tested bridge so current planning/direct outputs can be
normalized toward the geochat_kernel artifact contract.

The canonical model is geochat_kernel.models.GeoArtifact.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from geochat_kernel.models import ArtifactKind, GeoArtifact


_PUBLIC_TYPE_BY_KERNEL_KIND = {
    ArtifactKind.FEATURES.value: "vector_layer",
    ArtifactKind.MAP_LAYER.value: "map_view",
    ArtifactKind.RASTER_REF.value: "raster_layer",
    ArtifactKind.TABLE.value: "table",
    ArtifactKind.CHART.value: "chart",
    ArtifactKind.REPORT.value: "report",
    ArtifactKind.DOWNLOAD.value: "file",
    ArtifactKind.SCALAR.value: "json",
    ArtifactKind.ROUTE.value: "route",
    ArtifactKind.ISOCHRONE.value: "isochrone",
}


def _kind_value(kind: str | ArtifactKind) -> str:
    if isinstance(kind, ArtifactKind):
        return kind.value
    return str(kind)


def _public_type_for_kind(kind: str | ArtifactKind) -> str:
    return _PUBLIC_TYPE_BY_KERNEL_KIND.get(_kind_value(kind), "json")


def _model_to_dict(value: Any) -> dict[str, Any]:
    """
    Convert Pydantic v1/v2-like kernel models to a plain dict.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


def make_artifact(
    *,
    kind: str | ArtifactKind,
    payload: dict[str, Any] | None = None,
    title: str | None = None,
    description: str | None = None,
    source_node: str | None = None,
    produced_by: str | None = None,
    primary: bool = False,
    priority: int = 100,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
    artifact_id: str | None = None,
) -> GeoArtifact:
    """
    Create a geochat_kernel GeoArtifact with safe defaults.

    This is the preferred constructor inside smart_spatial_system adapters.
    """
    return GeoArtifact(
        id=artifact_id or f"art_{uuid4().hex}",
        kind=_kind_value(kind),
        title=title,
        description=description,
        payload=payload or {},
        ref_id=source_node,
        priority=priority,
        primary=primary,
        produced_by=produced_by,
        confidence=confidence,
        metadata=metadata or {},
    )


def artifact_to_public_dict(artifact: GeoArtifact | dict[str, Any]) -> dict[str, Any]:
    """
    Convert a kernel GeoArtifact into the public artifact shape expected by the
    product/API layer.

    The canonical kernel field remains `kind`.
    The product-friendly field is `type`.
    """
    data = _model_to_dict(artifact)

    kind = data.get("kind") or "scalar"
    payload = data.get("payload") or {}
    metadata = data.get("metadata") or {}

    return {
        "id": data.get("id"),
        "type": _public_type_for_kind(kind),
        "kind": kind,
        "title": data.get("title"),
        "description": data.get("description"),
        "payload": payload,
        "source_node": data.get("ref_id"),
        "produced_by": data.get("produced_by"),
        "primary": bool(data.get("primary", False)),
        "priority": data.get("priority", 100),
        "confidence": data.get("confidence"),
        "metadata": metadata,
    }


def artifacts_to_public_list(
    artifacts: list[GeoArtifact] | tuple[GeoArtifact, ...],
) -> list[dict[str, Any]]:
    return [artifact_to_public_dict(artifact) for artifact in artifacts]


def _is_feature_collection(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") == "FeatureCollection"
        and isinstance(value.get("features"), list)
    )


def _feature_collection_feature_count(value: dict[str, Any]) -> int:
    features = value.get("features")
    return len(features) if isinstance(features, list) else 0


def _first_non_empty_string(value: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _file_format_from_path(path: str) -> str:
    lowered = path.lower()

    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith(".geojson") or lowered.endswith(".json"):
        return "geojson" if lowered.endswith(".geojson") else "json"
    if lowered.endswith(".tif") or lowered.endswith(".tiff"):
        return "geotiff"
    if lowered.endswith(".gpkg"):
        return "gpkg"
    if lowered.endswith(".zip"):
        return "zip"
    if lowered.endswith(".csv"):
        return "csv"

    return "file"


def _looks_like_raster_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".tif", ".tiff", ".vrt", ".img"))


def _as_feature_collection_dict(value: dict[str, Any]) -> dict[str, Any] | None:
    if _is_feature_collection(value):
        return value

    geojson = value.get("geojson")
    if _is_feature_collection(geojson):
        return geojson

    data = value.get("data")
    if _is_feature_collection(data):
        return data

    payload = value.get("payload")
    if isinstance(payload, dict):
        payload_data = payload.get("data")
        if _is_feature_collection(payload_data):
            return payload_data

    features = value.get("features")
    if isinstance(features, list):
        return {
            "type": "FeatureCollection",
            "features": features,
        }

    return None


def _rows_from_table_dict(value: dict[str, Any]) -> list[Any] | None:
    rows = value.get("rows")
    if isinstance(rows, list):
        return rows

    table = value.get("table")
    if isinstance(table, list):
        return table

    if isinstance(table, dict):
        table_rows = table.get("rows")
        if isinstance(table_rows, list):
            return table_rows

    data = value.get("data")
    if isinstance(data, dict):
        data_rows = data.get("rows")
        if isinstance(data_rows, list):
            return data_rows

    return None


def _looks_like_report_dict(value: dict[str, Any]) -> bool:
    if isinstance(value.get("sections"), list):
        return True

    if isinstance(value.get("rankings"), list):
        return True

    if isinstance(value.get("report"), dict):
        return True

    if value.get("type") in {"report", "analysis_report", "summary_report"}:
        return True

    if isinstance(value.get("title"), str) and (
        "summary" in value or "sections" in value or "findings" in value
    ):
        return True

    return False


def output_to_artifact(
    value: Any,
    *,
    source_node: str | None = None,
    title: str | None = None,
    produced_by: str | None = None,
    primary: bool = False,
    metadata: dict[str, Any] | None = None,
) -> GeoArtifact:
    """
    Best-effort normalization of current plugin/orchestrator outputs to a
    geochat_kernel GeoArtifact.

    This intentionally stays conservative:
    - GeoJSON FeatureCollection -> FEATURES artifact
    - VectorOut-like object with .features -> FEATURES artifact
    - list[dict] -> TABLE artifact
    - dict -> SCALAR artifact
    - other values -> SCALAR artifact
    """
    base_metadata = dict(metadata or {})

    if _is_feature_collection(value):
        base_metadata.setdefault("format", "geojson")
        base_metadata.setdefault("feature_count", _feature_collection_feature_count(value))
        return make_artifact(
            kind=ArtifactKind.FEATURES,
            title=title,
            payload={
                "format": "geojson",
                "data": value,
            },
            source_node=source_node,
            produced_by=produced_by,
            primary=primary,
            metadata=base_metadata,
        )

    if hasattr(value, "features") and not isinstance(value, (dict, list)):
        raw_features = getattr(value, "features") or []
        if not isinstance(raw_features, list):
            raw_features = list(raw_features)

        input_metadata = getattr(value, "metadata", None)
        if isinstance(input_metadata, dict):
            base_metadata.update(input_metadata)

        feature_collection = {
            "type": "FeatureCollection",
            "features": raw_features,
        }

        base_metadata.setdefault("format", "geojson")
        base_metadata.setdefault("feature_count", len(raw_features))
        base_metadata.setdefault("input_type", type(value).__name__)

        return make_artifact(
            kind=ArtifactKind.FEATURES,
            title=title,
            payload={
                "format": "geojson",
                "data": feature_collection,
            },
            source_node=source_node,
            produced_by=produced_by,
            primary=primary,
            metadata=base_metadata,
        )

    if isinstance(value, list):
        base_metadata.setdefault("row_count", len(value))
        return make_artifact(
            kind=ArtifactKind.TABLE,
            title=title,
            payload={
                "format": "json",
                "rows": value,
            },
            source_node=source_node,
            produced_by=produced_by,
            primary=primary,
            metadata=base_metadata,
        )

    if isinstance(value, dict):
        feature_collection = _as_feature_collection_dict(value)
        if feature_collection is not None:
            base_metadata.setdefault("format", "geojson")
            base_metadata.setdefault(
                "feature_count",
                _feature_collection_feature_count(feature_collection),
            )
            base_metadata.setdefault("input_type", "dict")

            return make_artifact(
                kind=ArtifactKind.FEATURES,
                title=title or value.get("name") or value.get("title"),
                payload={
                    "format": "geojson",
                    "data": feature_collection,
                },
                source_node=source_node,
                produced_by=produced_by,
                primary=primary,
                metadata=base_metadata,
            )

        rows = _rows_from_table_dict(value)
        if rows is not None:
            columns = value.get("columns") or value.get("fields")
            if columns is not None:
                base_metadata.setdefault("columns", columns)

            base_metadata.setdefault("row_count", len(rows))
            base_metadata.setdefault("input_type", "dict")

            return make_artifact(
                kind=ArtifactKind.TABLE,
                title=title or value.get("name") or value.get("title"),
                payload={
                    "format": "json",
                    "rows": rows,
                    "columns": columns or [],
                },
                source_node=source_node,
                produced_by=produced_by,
                primary=primary,
                metadata=base_metadata,
            )

        if _looks_like_report_dict(value):
            base_metadata.setdefault("input_type", "dict")
            base_metadata.setdefault(
                "report_keys",
                sorted(str(key) for key in value.keys())[:30],
            )

            return make_artifact(
                kind=ArtifactKind.REPORT,
                title=title or value.get("title") or value.get("name"),
                payload={
                    "format": "json",
                    "data": value,
                },
                source_node=source_node,
                produced_by=produced_by,
                primary=primary,
                metadata=base_metadata,
            )

        file_path = _first_non_empty_string(
            value,
            (
                "path",
                "file_path",
                "output_path",
                "pdf_path",
                "download_path",
                "url",
                "download_url",
                "raster_ref",
                "raster_path",
            ),
        )

        if file_path is not None:
            file_format = _file_format_from_path(file_path)
            artifact_kind = (
                ArtifactKind.RASTER_REF
                if _looks_like_raster_path(file_path)
                or "raster_ref" in value
                or "raster_path" in value
                else ArtifactKind.DOWNLOAD
            )

            base_metadata.setdefault("format", file_format)
            base_metadata.setdefault("input_type", "dict")

            return make_artifact(
                kind=artifact_kind,
                title=title or value.get("name") or value.get("title"),
                payload={
                    "format": file_format,
                    "path": file_path,
                    "data": value,
                },
                source_node=source_node,
                produced_by=produced_by,
                primary=primary,
                metadata=base_metadata,
            )

        return make_artifact(
            kind=ArtifactKind.SCALAR,
            title=title,
            payload={
                "format": "json",
                "data": value,
            },
            source_node=source_node,
            produced_by=produced_by,
            primary=primary,
            metadata=base_metadata,
        )

    return make_artifact(
        kind=ArtifactKind.SCALAR,
        title=title,
        payload={
            "format": "text",
            "value": str(value),
        },
        source_node=source_node,
        produced_by=produced_by,
        primary=primary,
        metadata=base_metadata,
    )
