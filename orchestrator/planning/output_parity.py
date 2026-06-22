"""
orchestrator.planning.output_parity

Source-agnostic parity helpers for comparing the current DAG execution outputs
with optional kernel runtime outputs.

Core principle:
The core must not depend on:
- a specific case study
- a specific language
- a specific data source
- a specific output format
- a specific UI

This module therefore compares output shapes and lightweight summaries rather
than assuming PostGIS, OSM, GeoJSON, a frontend, or a concrete deployment.
"""

from __future__ import annotations

from typing import Any


def _safe_sorted_strings(values: Any) -> list[str]:
    try:
        return sorted(str(item) for item in values)
    except Exception:
        return []


def _features_from_vector_like(value: Any) -> list[Any] | None:
    """
    Return features from common vector-like values.

    Supported shapes are intentionally generic:
      - GeoJSON-like dict with "features"
      - objects exposing .features
      - dict-like plugin outputs with a list under "features"

    This does not make the core depend on GeoJSON. GeoJSON is only treated as
    one vector-like representation.
    """
    if isinstance(value, dict):
        features = value.get("features")
        if isinstance(features, list):
            return features

    if hasattr(value, "features"):
        try:
            features = getattr(value, "features")
            if isinstance(features, list):
                return features
        except Exception:
            return None

    return None


def _geometry_types_from_features(features: list[Any]) -> list[str]:
    geometry_types: set[str] = set()

    for feature in features[:100]:
        if not isinstance(feature, dict):
            continue

        geometry = feature.get("geometry")
        if isinstance(geometry, dict):
            geom_type = geometry.get("type")
            if geom_type:
                geometry_types.add(str(geom_type))

    return sorted(geometry_types)


def _property_keys_from_features(features: list[Any]) -> list[str]:
    keys: set[str] = set()

    for feature in features[:100]:
        if not isinstance(feature, dict):
            continue

        properties = feature.get("properties")
        if isinstance(properties, dict):
            for key in properties.keys():
                keys.add(str(key))

    return sorted(keys)


def _safe_exact_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right)
    except Exception:
        return False


def summarize_output_value(value: Any) -> dict[str, Any]:
    """
    Build a lightweight, public-safe summary of an execution output value.

    The summary is intentionally shape-based and data-source agnostic.
    """
    summary: dict[str, Any] = {
        "python_type": type(value).__name__,
    }

    features = _features_from_vector_like(value)
    if features is not None:
        summary.update(
            {
                "shape": "vector_features",
                "feature_count": len(features),
                "geometry_types": _geometry_types_from_features(features),
                "property_keys": _property_keys_from_features(features),
            }
        )

        if isinstance(value, dict):
            top_level_type = value.get("type")
            if top_level_type is not None:
                summary["top_level_type"] = str(top_level_type)

        metadata = getattr(value, "metadata", None)
        if isinstance(metadata, dict):
            for key in ("feature_count", "output_feature_count", "matched_count"):
                if key in metadata:
                    summary[key] = metadata[key]

        return summary

    if isinstance(value, list):
        summary.update(
            {
                "shape": "list",
                "length": len(value),
            }
        )

        if value:
            summary["item_python_type"] = type(value[0]).__name__

        return summary

    if isinstance(value, dict):
        summary.update(
            {
                "shape": "mapping",
                "keys": _safe_sorted_strings(value.keys())[:50],
            }
        )
        return summary

    if value is None or isinstance(value, (str, int, float, bool)):
        summary.update(
            {
                "shape": "scalar",
                "value": value,
            }
        )
        return summary

    summary["shape"] = "object"
    return summary


def compare_output_value_parity(
    dag_value: Any,
    kernel_value: Any,
) -> dict[str, Any]:
    """
    Compare two output values using source-agnostic shape summaries.
    """
    dag_summary = summarize_output_value(dag_value)
    kernel_summary = summarize_output_value(kernel_value)

    mismatches: list[str] = []

    if dag_summary.get("shape") != kernel_summary.get("shape"):
        mismatches.append("shape")

    shape = dag_summary.get("shape")

    if shape == "vector_features":
        for key in ("feature_count", "geometry_types", "property_keys"):
            if dag_summary.get(key) != kernel_summary.get(key):
                mismatches.append(key)

        # If both outputs expose a top-level type, compare it. Do not require
        # all vector-like providers to expose such a field.
        if (
            "top_level_type" in dag_summary
            and "top_level_type" in kernel_summary
            and dag_summary.get("top_level_type") != kernel_summary.get("top_level_type")
        ):
            mismatches.append("top_level_type")

    elif shape == "list":
        if dag_summary.get("length") != kernel_summary.get("length"):
            mismatches.append("length")

    elif shape == "mapping":
        if dag_summary.get("keys") != kernel_summary.get("keys"):
            mismatches.append("keys")

    elif shape == "scalar":
        if dag_summary.get("value") != kernel_summary.get("value"):
            mismatches.append("value")

    exact_equal = _safe_exact_equal(dag_value, kernel_value)

    return {
        "compatible": not mismatches,
        "exact_equal": exact_equal,
        "mismatches": mismatches,
        "dag_summary": dag_summary,
        "kernel_summary": kernel_summary,
    }


def compare_output_node_parity(
    dag_output_nodes: dict[str, Any] | None,
    kernel_output_nodes: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Compare DAG output_nodes with kernel output_nodes.

    This function is intentionally generic and does not know about PostGIS,
    files, OSM, frontend maps, reports, or any concrete datasource.
    """
    dag_output_nodes = dag_output_nodes or {}
    kernel_output_nodes = kernel_output_nodes or {}

    dag_ids = {str(key) for key in dag_output_nodes.keys()}
    kernel_ids = {str(key) for key in kernel_output_nodes.keys()}

    common_ids = sorted(dag_ids & kernel_ids)
    missing_from_kernel = sorted(dag_ids - kernel_ids)
    extra_in_kernel = sorted(kernel_ids - dag_ids)

    nodes: dict[str, Any] = {}

    for node_id in common_ids:
        nodes[node_id] = compare_output_value_parity(
            dag_output_nodes[node_id],
            kernel_output_nodes[node_id],
        )

    compatible = (
        not missing_from_kernel
        and not extra_in_kernel
        and all(item.get("compatible") for item in nodes.values())
    )

    return {
        "compatible": compatible,
        "status": "compatible" if compatible else "mismatch",
        "dag_output_node_ids": sorted(dag_ids),
        "kernel_output_node_ids": sorted(kernel_ids),
        "missing_from_kernel": missing_from_kernel,
        "extra_in_kernel": extra_in_kernel,
        "nodes": nodes,
    }
