from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.op_catalog import get_op, is_supported
from orchestrator.planning.planner import DeterministicPlanner
from orchestrator.planning.report_spec import (
    MapLayerSpec,
    ReportSpec,
    TableColumnSpec,
    TableSpec,
    default_real_estate_report_spec,
)
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec
from plugins.feature_scoring import rank_features, score_features
from plugins.report_builder import ReportOut, build_report
from plugins.risk_enrichment import enrich_risk


def _ranked_features():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
                "properties": {
                    "id": "p1",
                    "name": "ویلای لوکس شمال",
                    "rank": 1,
                    "investment_score": 87.5,
                    "distance_to_poi": 120.0,
                    "distance_to_road": 80.0,
                    "inside_buildable_zone": True,
                    "flood_risk": "low",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.5, 35.6]},
                "properties": {
                    "id": "p2",
                    "name": "آپارتمان مرکز شهر",
                    "rank": 2,
                    "investment_score": 71.2,
                    "distance_to_poi": 340.0,
                    "distance_to_road": 210.0,
                    "inside_buildable_zone": True,
                    "flood_risk": "low",
                    "earthquake_risk": "low",
                    "fire_risk": "low",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.3, 35.5]},
                "properties": {
                    "id": "p3",
                    "name": "زمین بایر جنوب",
                    "rank": 3,
                    "investment_score": 52.0,
                    "distance_to_poi": 480.0,
                    "distance_to_road": 450.0,
                    "inside_buildable_zone": False,
                    "flood_risk": "medium",
                    "earthquake_risk": "low",
                    "fire_risk": "low",
                },
            },
        ],
    }


def test_build_report_returns_report_out():
    report = build_report(_ranked_features())

    assert isinstance(report, ReportOut)
    assert report.success is True
    assert report.errors == []


def test_build_report_summary_is_correct():
    report = build_report(_ranked_features())

    assert report.summary["total_count"] == 3
    assert report.summary["top_score"] == 87.5
    assert report.summary["avg_score"] == pytest.approx(70.23, abs=0.1)
    assert report.summary["top_name"] == "ویلای لوکس شمال"
    assert report.summary["top_rank"] == 1


def test_build_report_table_has_correct_rows():
    spec = default_real_estate_report_spec(ranked_source="ranked")

    report = build_report(_ranked_features(), report_spec=spec)

    table = report.table
    assert table["total_rows"] == 3
    assert table["rows"][0]["rank"] == 1
    assert table["rows"][0]["investment_score"] == 87.5
    assert table["rows"][1]["rank"] == 2


def test_build_report_table_format_values():
    spec = ReportSpec(
        title="تست",
        tables=[
            TableSpec(
                source="ranked",
                columns=[
                    TableColumnSpec(
                        field="investment_score",
                        label="امتیاز",
                        format=".1f",
                    ),
                    TableColumnSpec(
                        field="inside_buildable_zone",
                        label="مجاز",
                        format="bool",
                    ),
                ],
                sort_by="rank",
                sort_order="asc",
            )
        ],
        map_layers=[
            MapLayerSpec(source="ranked", kind="choropleth", label="ملک‌ها"),
        ],
    )

    report = build_report(_ranked_features(), report_spec=spec)

    first_row = report.table["rows"][0]
    assert first_row["investment_score"] == 87.5
    assert first_row["inside_buildable_zone"] == "✓"

    third_row = report.table["rows"][2]
    assert third_row["inside_buildable_zone"] == "✗"


def test_build_report_map_layers():
    spec = default_real_estate_report_spec(
        ranked_source="ranked",
        map_sources={
            "poi": "poi",
            "roads": "roads",
            "buildable_zone": "buildable_zone",
        },
    )

    report = build_report(_ranked_features(), report_spec=spec)

    assert len(report.map_layers) == 4
    kinds = {layer["kind"] for layer in report.map_layers}
    assert "choropleth" in kinds


def test_build_report_from_dict_spec():
    spec_dict = {
        "title": "گزارش دیکشنری",
        "language": "fa",
        "format": "pdf",
        "config": {},
        "map_layers": [
            {"source": "ranked", "kind": "features", "label": "ملک‌ها",
             "visible": True, "style": {}}
        ],
        "tables": [
            {
                "source": "ranked",
                "title": "جدول",
                "sort_by": "rank",
                "sort_order": "asc",
                "max_rows": 50,
                "columns": [
                    {"field": "rank", "label": "رتبه", "format": "",
                     "align": "center", "width": 60},
                    {"field": "investment_score", "label": "امتیاز",
                     "format": ".1f", "align": "center", "width": 80},
                ],
            }
        ],
        "summary": {
            "source": "ranked",
            "stats": ["total_count", "top_score"],
            "template": "",
            "language": "fa",
        },
    }

    report = build_report(_ranked_features(), report_spec=spec_dict)

    assert report.success is True
    assert report.meta["title"] == "گزارش دیکشنری"
    assert report.summary["total_count"] == 3


def test_op_catalog_contains_build_report():
    assert is_supported("build_report")

    op = get_op("build_report")
    assert op.capability_name == "build_report"
    assert op.input_map["vector"] == "features"
    assert op.output_type == "report"


def test_planner_builds_plan_with_build_report_node():
    spec = QuerySpec(
        raw_query="گزارش PDF رتبه‌بندی تولید کن",
        goal="report_ranked_properties",
        entities=[
            EntitySpec(ref="ranked", kind="vector"),
        ],
        operations=[
            OperationSpec(
                op="build_report",
                inputs={"vector": "ranked"},
                params={
                    "report_spec": {
                        "title": "گزارش تست",
                        "language": "fa",
                        "format": "pdf",
                        "config": {},
                        "map_layers": [],
                        "tables": [],
                        "summary": None,
                    },
                    "score_field": "investment_score",
                    "rank_field": "rank",
                },
                output="report",
            ),
        ],
        outputs=[
            OutputSpec(kind="report", source="report", format="pdf"),
        ],
    )

    plan = DeterministicPlanner().build(spec)
    assert plan.nodes[0].capability_name == "build_report"


def test_full_risk_score_rank_report_chain():
    """
    End-to-end: enrich_risk → score_features → rank_features → build_report
    """
    raw_features = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
                "properties": {
                    "id": "p1",
                    "name": "ملک الف",
                    "distance_to_poi": 100.0,
                    "distance_to_road": 80.0,
                    "inside_buildable_zone": True,
                    "flood_zone": "A",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.5, 35.6]},
                "properties": {
                    "id": "p2",
                    "name": "ملک ب",
                    "distance_to_poi": 400.0,
                    "distance_to_road": 300.0,
                    "inside_buildable_zone": True,
                    "flood_zone": "C",
                },
            },
        ],
    }

    scoring_spec = {
        "output_field": "investment_score",
        "scale": 100,
        "factors": [
            {"name": "near_poi", "field": "distance_to_poi",
             "type": "inverse_distance", "max_distance": 500, "weight": 0.4},
            {"name": "near_road", "field": "distance_to_road",
             "type": "inverse_distance", "max_distance": 1000, "weight": 0.3},
            {"name": "buildable", "field": "inside_buildable_zone",
             "type": "boolean", "weight": 0.2},
            {"name": "flood", "field": "flood_risk",
             "type": "risk_level", "weight": 0.1},
        ],
    }

    report_spec = default_real_estate_report_spec(ranked_source="ranked")

    spec = QuerySpec(
        raw_query="ملک‌ها را تحلیل و گزارش بده",
        goal="full_report",
        entities=[EntitySpec(ref="properties", kind="vector")],
        operations=[
            OperationSpec(
                op="enrich_risk",
                inputs={"vector": "properties"},
                params={
                    "rules": [
                        {
                            "target": "flood_risk",
                            "source": "flood_zone",
                            "mapping": {"A": "low", "B": "medium", "C": "high"},
                        }
                    ],
                    "default_risks": {
                        "flood_risk": "low",
                        "earthquake_risk": "low",
                        "fire_risk": "low",
                    },
                    "overwrite": True,
                },
                output="risk_enriched",
            ),
            OperationSpec(
                op="score_features",
                inputs={"vector": "risk_enriched"},
                params={"scoring_spec": scoring_spec},
                output="scored",
            ),
            OperationSpec(
                op="rank_features",
                inputs={"vector": "scored"},
                params={"score_field": "investment_score", "rank_field": "rank"},
                output="ranked",
            ),
            OperationSpec(
                op="build_report",
                inputs={"vector": "ranked"},
                params={"score_field": "investment_score", "rank_field": "rank"},
                output="report",
            ),
        ],
        outputs=[
            OutputSpec(kind="report", source="report", format="pdf"),
        ],
    )

    plan = DeterministicPlanner().build(spec)

    assert [n.capability_name for n in plan.nodes] == [
        "enrich_risk",
        "score_features",
        "rank_features",
        "build_report",
    ]

    capabilities = {
        "enrich_risk": enrich_risk,
        "score_features": score_features,
        "rank_features": rank_features,
        "build_report": build_report,
    }

    result = DagExecutor(lambda name: capabilities[name]).execute(
        plan,
        initial_inputs={"properties": raw_features},
    )

    assert result.success is True

    report = result.output_nodes["report"]
    assert isinstance(report, ReportOut)
    assert report.success is True
    assert report.summary["total_count"] == 2
    assert report.summary["top_rank"] == 1
    assert report.summary["top_score"] is not None
    assert report.table["total_rows"] == 2
    assert report.table["rows"][0]["rank"] == 1

    top_props = raw_features["features"][0]["properties"]
    assert report.summary["top_name"] == "ملک الف"
