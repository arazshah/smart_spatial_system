"""
orchestrator.planning.report_spec

ReportSpec — Declarative specification for spatial analysis reports.

This spec is produced by:
    - LLM QuerySpec generator (as part of outputs)
    - Manual configuration

And consumed by:
    - ReportBuilder plugin (Phase 10B)
    - PDF renderer (Phase 10C)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MapLayerSpec:
    """
    A single layer to include in the report map.

    kind:
        features | heatmap | cluster | choropleth

    source:
        ref to a DAG node output or entity ref.

    style:
        Optional styling hints, e.g. color, radius, fill_opacity.
    """
    source: str
    kind: str = "features"
    label: str = ""
    visible: bool = True
    style: dict[str, Any] = field(default_factory=dict)


@dataclass
class TableColumnSpec:
    """
    A single column in the ranking/summary table.
    """
    field: str
    label: str = ""
    format: str = ""
    align: str = "right"
    width: int = 0


@dataclass
class TableSpec:
    """
    Ranking/summary table specification.
    """
    source: str
    columns: list[TableColumnSpec] = field(default_factory=list)
    sort_by: str = ""
    sort_order: str = "asc"
    max_rows: int = 50
    title: str = ""


@dataclass
class SummarySpec:
    """
    Summary section of the report.

    Fields can reference computed feature properties or aggregate stats.
    """
    source: str
    stats: list[str] = field(default_factory=list)
    template: str = ""
    language: str = "en"


@dataclass
class ReportSpec:
    """
    Full declarative specification for a spatial analysis report.

    title:
        Report title.

    language:
        fa | en

    map_layers:
        Ordered list of layers to render on the map.

    tables:
        Ordered list of tables.

    summary:
        Optional summary section.

    format:
        pdf | html | json

    config:
        Renderer-specific settings.
    """

    title: str = "Spatial Analysis Report"
    language: str = "en"
    map_layers: list[MapLayerSpec] = field(default_factory=list)
    tables: list[TableSpec] = field(default_factory=list)
    summary: SummarySpec | None = None
    format: str = "pdf"
    config: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ #
# Default factory for real-estate use case
# ------------------------------------------------------------------ #

def default_real_estate_report_spec(
    ranked_source: str = "ranked",
    *,
    map_sources: dict[str, str] | None = None,
    language: str = "en",
    format: str = "pdf",
    title: str = "Property Ranking and Investment Analysis Report",
) -> ReportSpec:
    """
    Default report spec for the real-estate property analysis use case.
    """
    map_sources = map_sources or {}

    map_layers: list[MapLayerSpec] = []

    if "buildable_zone" in map_sources:
        map_layers.append(MapLayerSpec(
            source=map_sources["buildable_zone"],
            kind="features",
            label="Permitted construction zone",
            style={"color": "#22c55e", "fill_opacity": 0.15},
        ))

    if "poi" in map_sources:
        map_layers.append(MapLayerSpec(
            source=map_sources["poi"],
            kind="features",
            label="Points of interest (metro / shopping centres)",
            style={"color": "#3b82f6", "radius": 8},
        ))

    if "roads" in map_sources:
        map_layers.append(MapLayerSpec(
            source=map_sources["roads"],
            kind="features",
            label="Main roads",
            style={"color": "#f59e0b", "weight": 2},
        ))

    map_layers.append(MapLayerSpec(
        source=ranked_source,
        kind="choropleth",
        label="Ranked properties",
        style={"color_field": "investment_score", "radius": 10},
    ))

    columns = [
        TableColumnSpec(field="rank", label="Rank", align="center", width=60),
        TableColumnSpec(field="name", label="Property name", align="left"),
        TableColumnSpec(
            field="investment_score",
            label="Score",
            format=".1f",
            align="center",
            width=80,
        ),
        TableColumnSpec(
            field="distance_to_poi",
            label="Distance to POI (m)",
            format=".0f",
            align="center",
        ),
        TableColumnSpec(
            field="distance_to_road",
            label="Distance to road (m)",
            format=".0f",
            align="center",
        ),
        TableColumnSpec(
            field="inside_buildable_zone",
            label="In permitted zone",
            align="center",
            width=100,
        ),
        TableColumnSpec(field="flood_risk", label="Flood risk", align="center"),
        TableColumnSpec(field="earthquake_risk", label="Earthquake risk", align="center"),
        TableColumnSpec(field="fire_risk", label="Fire risk", align="center"),
    ]

    tables = [
        TableSpec(
            source=ranked_source,
            columns=columns,
            sort_by="rank",
            sort_order="asc",
            max_rows=50,
            title="Property ranking table",
        )
    ]

    summary = SummarySpec(
        source=ranked_source,
        stats=[
            "total_count",
            "top_score",
            "avg_score",
            "top_name",
        ],
        language=language,
    )

    return ReportSpec(
        title=title,
        language=language,
        map_layers=map_layers,
        tables=tables,
        summary=summary,
        format=format,
        config={},
    )


# ------------------------------------------------------------------ #
# Default factory for generic accessibility analysis
# ------------------------------------------------------------------ #

def default_accessibility_report_spec(
    ranked_source: str,
    amenity_columns: Sequence[tuple[str, str]],
    *,
    score_field: str = "accessibility_score",
    rank_field: str = "rank",
    name_field: str = "name",
    language: str = "en",
    format: str = "pdf",
    title: str = "Accessibility Analysis Report",
) -> ReportSpec:
    """
    Report spec for a multi-amenity accessibility analysis.

    The real-estate default cannot stand in for this one: its columns
    (investment_score, flood_risk, inside_buildable_zone, ...) do not exist
    on accessibility features, so every such cell renders as an empty
    string while the summary silently stays correct - a table that looks
    populated but carries no data.

    Args:
        ranked_source:
            Ref of the ranked feature set the table and map read from.
        amenity_columns:
            (distance_field, label) per amenity, in report column order.
            Built from the amenity list, so the table always matches the
            fields the chain actually produced.
    """
    columns = [
        TableColumnSpec(field=rank_field, label="Rank", align="center", width=60),
        TableColumnSpec(field=name_field, label="Name", align="left"),
        TableColumnSpec(
            field=score_field,
            label="Accessibility score",
            format=".1f",
            align="center",
            width=100,
        ),
    ]
    columns.extend(
        TableColumnSpec(field=field_name, label=label, format=".0f", align="center")
        for field_name, label in amenity_columns
    )

    return ReportSpec(
        title=title,
        language=language,
        map_layers=[
            MapLayerSpec(
                source=ranked_source,
                kind="choropleth",
                label="Ranked sites",
                style={"color_field": score_field, "radius": 10},
            )
        ],
        tables=[
            TableSpec(
                source=ranked_source,
                columns=columns,
                sort_by=rank_field,
                sort_order="asc",
                max_rows=50,
                title="Site accessibility ranking",
            )
        ],
        summary=SummarySpec(
            source=ranked_source,
            stats=["total_count", "top_score", "avg_score", "top_name"],
            language=language,
        ),
        format=format,
        config={},
    )


# ------------------------------------------------------------------ #
# dict <-> ReportSpec conversion
# ------------------------------------------------------------------ #

def report_spec_from_dict(data: dict[str, Any]) -> ReportSpec:
    if not isinstance(data, dict):
        raise ValueError("ReportSpec data must be a dict.")

    map_layers = [
        MapLayerSpec(**layer) if isinstance(layer, dict) else layer
        for layer in data.get("map_layers", [])
    ]

    tables_raw = data.get("tables", [])
    tables: list[TableSpec] = []
    for t in tables_raw:
        if not isinstance(t, dict):
            continue
        columns_raw = t.get("columns", [])
        columns = [
            TableColumnSpec(**c) if isinstance(c, dict) else c
            for c in columns_raw
        ]
        tables.append(TableSpec(
            source=str(t.get("source") or ""),
            columns=columns,
            sort_by=str(t.get("sort_by") or ""),
            sort_order=str(t.get("sort_order") or "asc"),
            max_rows=int(t.get("max_rows") or 50),
            title=str(t.get("title") or ""),
        ))

    summary_raw = data.get("summary")
    summary: SummarySpec | None = None
    if isinstance(summary_raw, dict):
        summary = SummarySpec(
            source=str(summary_raw.get("source") or ""),
            stats=list(summary_raw.get("stats") or []),
            template=str(summary_raw.get("template") or ""),
            language=str(summary_raw.get("language") or "en"),
        )

    return ReportSpec(
        title=str(data.get("title") or "Spatial Analysis Report"),
        language=str(data.get("language") or "en"),
        map_layers=map_layers,
        tables=tables,
        summary=summary,
        format=str(data.get("format") or "pdf"),
        config=dict(data.get("config") or {}),
    )
