"""
Planning response adapter.

Small output-facing helpers for converting planning execution results into
production response payload fragments.

This module intentionally contains no query execution orchestration.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            json_safe(item)
            for item in value
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return json_safe(value.to_dict())

    if is_dataclass(value):
        return json_safe(asdict(value))

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict) and payload:
        return json_safe(payload)

    return repr(value)


def is_feature_collection(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") == "FeatureCollection"
        and isinstance(value.get("features"), list)
    )


def planning_trace_to_steps(trace: list[Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []

    for item in trace or []:
        capability_name = getattr(item, "capability_name", None)
        node_id = getattr(item, "node_id", None)
        status = getattr(item, "status", None)
        error = getattr(item, "error", None)
        output_summary = getattr(item, "output_summary", None) or {}

        if error:
            message = error
        elif isinstance(output_summary, dict) and output_summary:
            parts = [f"{k}={v}" for k, v in output_summary.items()]
            message = ", ".join(parts[:6])
        else:
            message = status or ""

        steps.append(
            {
                "label": capability_name or node_id or "step",
                "step": node_id or capability_name or "step",
                "status": status or "unknown",
                "message": message,
            }
        )

    return steps


def _as_feature_collection(value: Any) -> dict[str, Any] | None:
    if is_feature_collection(value):
        return value

    geojson = getattr(value, "geojson", None)
    if is_feature_collection(geojson):
        return geojson

    features = getattr(value, "features", None)
    if isinstance(features, list):
        return {
            "type": "FeatureCollection",
            "features": json_safe(features),
        }

    if isinstance(value, dict) and isinstance(value.get("features"), list):
        return {
            "type": "FeatureCollection",
            "features": json_safe(value.get("features") or []),
        }

    return None


def planning_outputs_to_response_payload(
    planning_result: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    from orchestrator.kernel_artifacts import (
        artifact_to_public_dict,
        output_to_artifact,
    )

    layers: list[dict[str, Any]] = []
    outputs: dict[str, Any] = {
        "files": [],
        "vectors": [],
        "tables": [],
        "rasters": [],
        "artifacts": [],
    }
    primary_report: dict[str, Any] | None = None

    for node_id, value in (getattr(planning_result, "output_nodes", None) or {}).items():
        try:
            artifact = output_to_artifact(
                value,
                source_node=node_id,
                title=node_id,
                produced_by="query_spec_planning",
                metadata={
                    "source": "planning.output_nodes",
                },
            )
            outputs["artifacts"].append(artifact_to_public_dict(artifact))
        except Exception as exc:
            outputs.setdefault("artifact_errors", []).append(
                {
                    "node_id": node_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        feature_collection = _as_feature_collection(value)

        if feature_collection is not None:
            layer = {
                "id": node_id,
                "name": node_id,
                "type": "vector",
                "format": "geojson",
                "geojson": feature_collection,
                "summary": {
                    "feature_count": len(feature_collection.get("features", [])),
                },
            }
            layers.append(layer)
            outputs["vectors"].append(layer)
            continue

        safe_value = json_safe(value)

        if isinstance(safe_value, dict):
            if any(
                key in safe_value
                for key in ("title", "summary", "sections", "rankings", "table", "rows")
            ):
                if primary_report is None:
                    primary_report = safe_value

                outputs["tables"].append(
                    {
                        "name": node_id,
                        "source": "planning.output_nodes",
                        "data": safe_value,
                    }
                )

            for key in ("path", "file_path", "output_path", "pdf_path"):
                file_path = safe_value.get(key)
                if isinstance(file_path, str) and file_path:
                    outputs["files"].append(
                        {
                            "name": safe_value.get("name") or node_id,
                            "path": file_path,
                            "source": "planning.output_nodes",
                            "format": (
                                "pdf"
                                if str(file_path).lower().endswith(".pdf")
                                else "file"
                            ),
                        }
                    )
                    break

            continue

        if isinstance(value, str) and value.lower().endswith(".pdf"):
            outputs["files"].append(
                {
                    "name": node_id,
                    "path": value,
                    "source": "planning.output_nodes",
                    "format": "pdf",
                }
            )

    return layers, outputs, primary_report
