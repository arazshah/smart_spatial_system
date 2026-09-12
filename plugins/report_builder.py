"""
report_builder.py

Report Builder Plugin
=====================

Plugin ID:
    report_builder

Capability:
    - build_report

Purpose:
    Convert ranked/scored vector features + ReportSpec into a structured
    report data object (JSON-serializable dict) ready for:
        - HTML rendering
        - PDF generation (Phase 10C)
        - API response

Output:
    ReportOut — a structured dict containing:
        meta:       title, language, generated_at, format
        summary:    total_count, top_score, avg_score, top_name, ...
        table:      rows list, column specs
        map_layers: list of layer data for map rendering
        raw_spec:   original ReportSpec as dict
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from orchestrator.planning.report_spec import (
    ReportSpec,
    SummarySpec,
    TableSpec,
    default_real_estate_report_spec,
    report_spec_from_dict,
)

PLUGIN_ID = "report_builder"


# ------------------------------------------------------------------ #
# ReportOut dataclass
# ------------------------------------------------------------------ #

@dataclass
class ReportOut:
    """
    Structured report output.

    Attributes:
        meta:           Report metadata (title, language, format, generated_at).
        summary:        Aggregate statistics computed from features.
        table:          Table data with columns and rows.
        map_layers:     Map layer descriptors with feature data.
        spec:           Original ReportSpec as dict.
        success:        Whether report was built successfully.
        errors:         List of non-fatal errors/warnings encountered.
    """

    meta: dict[str, Any]
    summary: dict[str, Any]
    table: dict[str, Any]
    map_layers: list[dict[str, Any]]
    spec: dict[str, Any]
    success: bool = True
    errors: list[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "summary": self.summary,
            "table": self.table,
            "map_layers": self.map_layers,
            "spec": self.spec,
            "success": self.success,
            "errors": self.errors,
        }


# ------------------------------------------------------------------ #
# Feature extraction
# ------------------------------------------------------------------ #

def _extract_features(input_data: Any, label: str = "features") -> list[dict[str, Any]]:
    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw = getattr(input_data, "features")
    elif isinstance(input_data, dict):
        gtype = input_data.get("type")
        if gtype == "FeatureCollection":
            raw = input_data.get("features", [])
        elif gtype == "Feature":
            raw = [input_data]
        else:
            raise ValueError(f"{label} must be FeatureCollection or Feature.")
    elif isinstance(input_data, list):
        raw = input_data
    else:
        raise ValueError(
            f"{label} must be VectorOut-like, FeatureCollection, Feature, or list[Feature]."
        )

    if not isinstance(raw, list):
        raise ValueError(f"{label} features must be a list.")

    return raw


def _get_props(feature: Any) -> dict[str, Any]:
    if isinstance(feature, dict):
        props = feature.get("properties")
        return props if isinstance(props, dict) else {}
    return {}


# ------------------------------------------------------------------ #
# ReportSpec resolution
# ------------------------------------------------------------------ #

def _resolve_report_spec(
    report_spec: Any,
    ranked_source: str,
) -> ReportSpec:
    if report_spec is None:
        return default_real_estate_report_spec(ranked_source=ranked_source)

    if isinstance(report_spec, ReportSpec):
        return report_spec

    if isinstance(report_spec, dict):
        return report_spec_from_dict(report_spec)

    raise ValueError(
        f"report_spec must be ReportSpec, dict, or None. Got: {type(report_spec).__name__}"
    )


# ------------------------------------------------------------------ #
# Summary computation
# ------------------------------------------------------------------ #

def _compute_summary(
    features: list[dict[str, Any]],
    summary_spec: SummarySpec | None,
    score_field: str = "investment_score",
    rank_field: str = "rank",
    name_field: str = "name",
    language: str = "en",
) -> dict[str, Any]:
    if not features:
        return {
            "total_count": 0,
            "top_score": None,
            "avg_score": None,
            "min_score": None,
            "max_score": None,
            "top_name": None,
            "top_rank": None,
            "language": language,
        }

    all_props = [_get_props(f) for f in features]

    scores: list[float] = []
    for props in all_props:
        v = props.get(score_field)
        if v is not None:
            try:
                scores.append(float(v))
            except (TypeError, ValueError):
                pass

    top_feature_props: dict[str, Any] = {}
    for props in all_props:
        rank_val = props.get(rank_field)
        if rank_val is not None:
            try:
                if int(rank_val) == 1:
                    top_feature_props = props
                    break
            except (TypeError, ValueError):
                pass

    if not top_feature_props and all_props:
        top_feature_props = all_props[0]

    result: dict[str, Any] = {
        "total_count": len(features),
        "language": language,
    }

    if scores:
        result["top_score"] = round(max(scores), 2)
        result["min_score"] = round(min(scores), 2)
        result["max_score"] = round(max(scores), 2)
        result["avg_score"] = round(statistics.mean(scores), 2)
        if len(scores) > 1:
            result["median_score"] = round(statistics.median(scores), 2)
    else:
        result["top_score"] = None
        result["min_score"] = None
        result["max_score"] = None
        result["avg_score"] = None

    result["top_name"] = top_feature_props.get(name_field)
    result["top_rank"] = top_feature_props.get(rank_field)
    result["top_score_value"] = top_feature_props.get(score_field)

    return result


# ------------------------------------------------------------------ #
# Table builder
# ------------------------------------------------------------------ #

def _format_value(value: Any, fmt: str) -> Any:
    if value is None:
        return ""

    if not fmt:
        return value

    fmt = str(fmt).strip()

    if fmt.startswith(".") and fmt.endswith("f"):
        try:
            decimal_places = int(fmt[1:-1])
            return round(float(value), decimal_places)
        except (TypeError, ValueError):
            return value

    if fmt in {"bool", "boolean", "✓/✗"}:
        if isinstance(value, bool):
            return "✓" if value else "✗"
        if isinstance(value, str):
            return "✓" if value.lower() in {"true", "1", "yes", "داخل", "مجاز"} else "✗"
        return "✓" if value else "✗"

    # "risk_fa"/"risk_persian" are the historical spelling of this format;
    # they are still accepted so any stored report spec keeps working.
    if fmt in {"risk", "risk_label", "risk_fa", "risk_persian"}:
        mapping = {
            "very_low": "very low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "very_high": "very high",
            "critical": "critical",
        }
        return mapping.get(str(value).lower(), str(value))

    return value


def _build_table(
    features: list[dict[str, Any]],
    table_spec: TableSpec | None,
    score_field: str = "investment_score",
    rank_field: str = "rank",
    errors: list[str] | None = None,
) -> dict[str, Any]:
    errors = errors or []

    if not table_spec:
        columns = [
            {"field": rank_field, "label": "Rank", "align": "center"},
            {"field": score_field, "label": "Score", "align": "center"},
        ]
        rows = [
            {
                rank_field: _get_props(f).get(rank_field),
                score_field: _get_props(f).get(score_field),
            }
            for f in features
        ]
        return {
            "title": "Ranking table",
            "columns": columns,
            "rows": rows,
            "total_rows": len(rows),
        }

    columns_info = [
        {
            "field": col.field,
            "label": col.label or col.field,
            "format": col.format,
            "align": col.align,
            "width": col.width,
        }
        for col in table_spec.columns
    ]

    sort_by = table_spec.sort_by or rank_field
    sort_order = table_spec.sort_order or "asc"
    max_rows = table_spec.max_rows or 50

    sorted_features = list(features)

    try:
        reverse = sort_order.lower() == "desc"
        sorted_features.sort(
            key=lambda f: (
                (lambda v: (v is None, v))(
                    _get_props(f).get(sort_by)
                )
            ),
            reverse=reverse,
        )
    except Exception as exc:
        errors.append(f"Table sort failed: {exc}")

    rows: list[dict[str, Any]] = []
    for feature in sorted_features[:max_rows]:
        props = _get_props(feature)
        row: dict[str, Any] = {}

        for col in table_spec.columns:
            raw = props.get(col.field)
            row[col.field] = _format_value(raw, col.format)

        rows.append(row)

    return {
        "title": table_spec.title or "Ranking table",
        "columns": columns_info,
        "rows": rows,
        "total_rows": len(rows),
        "sort_by": sort_by,
        "sort_order": sort_order,
    }


# ------------------------------------------------------------------ #
# Map layer builder
# ------------------------------------------------------------------ #

def _build_map_layers(
    features: list[dict[str, Any]],
    spec: ReportSpec,
    resolved_node_outputs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    resolved_node_outputs = resolved_node_outputs or {}
    result: list[dict[str, Any]] = []

    for layer_spec in spec.map_layers:
        source = layer_spec.source

        # Resolve actual feature data if available.
        layer_features: list[dict[str, Any]] | None = None

        if source in resolved_node_outputs:
            raw = resolved_node_outputs[source]
            try:
                layer_features = _extract_features(raw, label=f"layer:{source}")
            except Exception:
                layer_features = None
        elif source == spec.tables[0].source if spec.tables else None:
            layer_features = features
        else:
            layer_features = None

        layer_data: dict[str, Any] = {
            "source": source,
            "kind": layer_spec.kind,
            "label": layer_spec.label,
            "visible": layer_spec.visible,
            "style": dict(layer_spec.style),
            "feature_count": len(layer_features) if layer_features else 0,
        }

        if layer_features:
            layer_data["features"] = layer_features

        result.append(layer_data)

    if not result and features:
        result.append({
            "source": "ranked",
            "kind": "features",
            "label": "Ranked properties",
            "visible": True,
            "style": {},
            "feature_count": len(features),
            "features": features,
        })

    return result


# ------------------------------------------------------------------ #
# Capability: build_report
# ------------------------------------------------------------------ #

@capability(
    name="build_report",
    keywords=[
        "build report",
        "generate report",
        "create report",
        "report builder",
        "ranking report",
        "spatial report",
        "گزارش",
        "تولید گزارش",
        "ساخت گزارش",
        "گزارش رتبه‌بندی",
        "خروجی گزارش",
    ],
    description=(
        "Build a structured report from ranked/scored features and a ReportSpec. "
        "Output is a JSON-serializable ReportOut ready for HTML/PDF rendering."
    ),
    required_inputs=["features"],
    optional_inputs=[
        "report_spec",
        "node_outputs",
        "score_field",
        "rank_field",
        "name_field",
        "metadata",
    ],
    output_kind="report",
    permissions=[],
    metadata={
        "category": "report",
        "data_type": "report",
        "operation": "build_report",
        "returns": "ReportOut",
        "artifact_kind": "report",
        "access_scope": "reporting",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.report_builder",
    },
)
def build_report(
    features: Any,
    report_spec: ReportSpec | dict[str, Any] | None = None,
    node_outputs: dict[str, Any] | None = None,
    score_field: str = "investment_score",
    rank_field: str = "rank",
    name_field: str = "name",
    metadata: dict[str, Any] | None = None,
) -> ReportOut:
    """
    Build structured report from ranked vector features.

    Args:
        features:
            Ranked/scored VectorOut, FeatureCollection, or feature list.

        report_spec:
            ReportSpec dataclass, dict, or None.
            If None, default real-estate spec is used.

        node_outputs:
            Dict of {ref: VectorOut} for other DAG nodes, used to resolve
            map layer sources (e.g. poi, roads, buildable_zone).

        score_field:
            Name of the score field on features.

        rank_field:
            Name of the rank field on features.

        name_field:
            Name of the name/label field on features.

        metadata:
            Extra metadata to include in report meta.
    """
    errors: list[str] = []

    try:
        input_features = _extract_features(features)
    except Exception as exc:
        return ReportOut(
            meta={
                "title": "Report",
                "language": "en",
                "format": "pdf",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "feature_count": 0,
            },
            summary={},
            table={"title": "Table", "columns": [], "rows": [], "total_rows": 0},
            map_layers=[],
            spec={},
            success=False,
            errors=[f"Feature extraction failed: {exc}"],
        )

    spec = _resolve_report_spec(
        report_spec,
        ranked_source=rank_field,
    )

    language = spec.language or "en"

    summary = _compute_summary(
        input_features,
        summary_spec=spec.summary,
        score_field=score_field,
        rank_field=rank_field,
        name_field=name_field,
        language=language,
    )

    table_spec = spec.tables[0] if spec.tables else None
    table = _build_table(
        input_features,
        table_spec=table_spec,
        score_field=score_field,
        rank_field=rank_field,
        errors=errors,
    )

    map_layers = _build_map_layers(
        input_features,
        spec=spec,
        resolved_node_outputs=node_outputs or {},
    )

    try:
        spec_dict = {
            "title": spec.title,
            "language": spec.language,
            "format": spec.format,
            "config": spec.config,
            "map_layers": [
                {
                    "source": layer.source,
                    "kind": layer.kind,
                    "label": layer.label,
                    "visible": layer.visible,
                    "style": layer.style,
                }
                for layer in spec.map_layers
            ],
            "tables": [
                {
                    "source": t.source,
                    "title": t.title,
                    "sort_by": t.sort_by,
                    "sort_order": t.sort_order,
                    "max_rows": t.max_rows,
                    "columns": [
                        {
                            "field": c.field,
                            "label": c.label,
                            "format": c.format,
                            "align": c.align,
                            "width": c.width,
                        }
                        for c in t.columns
                    ],
                }
                for t in spec.tables
            ],
            "summary": {
                "source": spec.summary.source,
                "stats": spec.summary.stats,
                "language": spec.summary.language,
            } if spec.summary else None,
        }
    except Exception as exc:
        spec_dict = {}
        errors.append(f"Spec serialization failed: {exc}")

    report_meta: dict[str, Any] = {
        "title": spec.title,
        "language": language,
        "format": spec.format,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feature_count": len(input_features),
        "score_field": score_field,
        "rank_field": rank_field,
        "plugin": PLUGIN_ID,
    }

    if metadata:
        report_meta.update(metadata)

    return ReportOut(
        meta=report_meta,
        summary=summary,
        table=table,
        map_layers=map_layers,
        spec=spec_dict,
        success=True,
        errors=errors,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Report Builder",
    description="Build structured JSON report from ranked spatial features.",
    author="GeoChat Platform Team",
    permissions=[],
)
