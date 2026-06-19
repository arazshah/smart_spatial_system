"""
orchestrator.planning.op_catalog

Logical operation catalog.

This is the bridge between:
    QuerySpec logical operations
and
    real plugin capabilities.

For MVP, this catalog is intentionally explicit and deterministic.
Later it can be extended with capability discovery, scoring, fallback, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OpDescriptor:
    op_name: str
    capability_name: str
    input_map: dict[str, str] = field(default_factory=dict)
    input_types: dict[str, str] = field(default_factory=dict)
    param_map: dict[str, str] = field(default_factory=dict)
    output_type: str = "json"
    notes: str = ""


OP_CATALOG: dict[str, OpDescriptor] = {
    "load_vector": OpDescriptor(
        op_name="load_vector",
        capability_name="load_local_vector",
        param_map={
            "path": "path",
            "strict_extensions": "strict_extensions",
            "layer": "layer",
            "max_features": "max_features",
        },
        output_type="vector",
    ),

    "query_database": OpDescriptor(
        op_name="query_database",
        capability_name="fetch_postgis_layer",
        param_map={
            "table": "table",
            "profile": "profile",
            "dsn": "dsn",
            "schema": "schema",
            "geom_col": "geom_col",
            "where": "where",
            "limit": "limit",
            "output_srid": "output_srid",
        },
        output_type="vector",
    ),

    "filter_attribute": OpDescriptor(
        op_name="filter_attribute",
        capability_name="filter_features",
        input_map={"vector": "features"},
        input_types={"vector": "vector"},
        param_map={
            "where": "where",
            "case_sensitive": "case_sensitive",
            "sort_by": "sort_by",
            "sort_order": "sort_order",
            "limit": "limit",
            "offset": "offset",
        },
        output_type="vector",
    ),

    "filter_by_distance": OpDescriptor(
        op_name="filter_by_distance",
        capability_name="find_nearest_neighbors",
        input_map={
            "vector": "source_features",
            "reference": "target_features",
        },
        input_types={
            "vector": "vector",
            "reference": "vector",
        },
        param_map={
            "k": "k",
            "max_distance_m": "max_distance",
            "drop_unmatched": "drop_unmatched",
            "engine": "engine",
            "precision": "precision",
            "include_target_geometry": "include_target_geometry",
            "source_crs": "source_crs",
        },
        output_type="vector",
        notes=(
            "For 'nearer than X meters', use max_distance_m=X and "
            "drop_unmatched=True."
        ),
    ),

    "distance_to": OpDescriptor(
        op_name="distance_to",
        capability_name="calculate_distances",
        input_map={
            "vector": "source_features",
            "target": "target_features",
        },
        input_types={
            "vector": "vector",
            "target": "vector",
        },
        param_map={
            "mode": "mode",
            "engine": "engine",
            "precision": "precision",
            "drop_failed": "drop_failed",
            "source_crs": "source_crs",
        },
        output_type="vector",
    ),

    "filter_points_in_polygon": OpDescriptor(
        op_name="filter_points_in_polygon",
        capability_name="filter_points_in_polygon",
        input_map={
            "vector": "points",
            "polygon": "polygons",
        },
        input_types={
            "vector": "vector",
            "polygon": "vector",
        },
        param_map={
            "predicate": "predicate",
            "drop_outside": "drop_outside",
        },
        output_type="vector",
        notes="True point-in-polygon predicate, not bbox approximation.",
    ),

    "intersect": OpDescriptor(
        op_name="intersect",
        capability_name="intersect_features",
        input_map={
            "source": "source_features",
            "target": "target_features",
        },
        input_types={
            "source": "vector",
            "target": "vector",
        },
        param_map={
            "mode": "mode",
            "engine": "engine",
            "precision": "precision",
            "drop_non_intersecting": "drop_non_intersecting",
            "drop_failed": "drop_failed",
            "source_crs": "source_crs",
        },
        output_type="vector",
    ),

    "raster_stats": OpDescriptor(
        op_name="raster_stats",
        capability_name="calculate_raster_statistics",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "stats": "stats",
            "bands": "bands",
            "nodata": "nodata",
            "histogram_bins": "histogram_bins",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
        },
        output_type="json",
    ),

    "score_features": OpDescriptor(
        op_name="score_features",
        capability_name="score_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "scoring_spec": "scoring_spec",
            "factors": "factors",
            "output_field": "output_field",
            "scale": "scale",
            "normalize_weights": "normalize_weights",
        },
        output_type="vector",
        notes="Weighted multi-criteria feature scoring.",
    ),

    "rank_features": OpDescriptor(
        op_name="rank_features",
        capability_name="rank_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "score_field": "score_field",
            "rank_field": "rank_field",
            "descending": "descending",
            "limit": "limit",
        },
        output_type="vector",
        notes="Rank features by score field.",
    ),


    "enrich_feature_properties": OpDescriptor(
        op_name="enrich_feature_properties",
        capability_name="enrich_feature_properties",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "rules": "rules",
            "skip_missing": "skip_missing",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Derive/copy/rename feature properties before scoring.",
    ),

    "join_feature_properties": OpDescriptor(
        op_name="join_feature_properties",
        capability_name="join_feature_properties",
        input_map={
            "left": "left_features",
            "right": "right_features",
        },
        input_types={
            "left": "vector",
            "right": "vector",
        },
        param_map={
            "left_key": "left_key",
            "right_key": "right_key",
            "fields": "fields",
            "prefix": "prefix",
            "overwrite": "overwrite",
            "unmatched": "unmatched",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Join feature properties by matching keys.",
    ),


    "enrich_risk": OpDescriptor(
        op_name="enrich_risk",
        capability_name="enrich_risk",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "risk_spec": "risk_spec",
            "default_risks": "default_risks",
            "overrides": "overrides",
            "rules": "rules",
            "id_field": "id_field",
            "overwrite": "overwrite",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Add flood, earthquake and fire risk fields to features.",
    ),


    "build_report": OpDescriptor(
        op_name="build_report",
        capability_name="build_report",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "report_spec": "report_spec",
            "node_outputs": "node_outputs",
            "score_field": "score_field",
            "rank_field": "rank_field",
            "name_field": "name_field",
            "metadata": "metadata",
        },
        output_type="report",
        notes="Build structured report from ranked features and ReportSpec.",
    ),

}


PENDING_OPS: set[str] = {
    "enrich_weather",
    "render_pdf",
    "filter_points_on_raster",
}


def get_op(op_name: str) -> OpDescriptor:
    if op_name not in OP_CATALOG:
        pending = op_name in PENDING_OPS
        raise KeyError(f"Unknown operation: {op_name}. pending={pending}")
    return OP_CATALOG[op_name]


def is_supported(op_name: str) -> bool:
    return op_name in OP_CATALOG


def is_pending(op_name: str) -> bool:
    return op_name in PENDING_OPS


def list_supported_ops() -> list[str]:
    return sorted(OP_CATALOG)


def list_pending_ops() -> list[str]:
    return sorted(PENDING_OPS)
