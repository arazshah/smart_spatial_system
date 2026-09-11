"""
DAG-resolution test for the real-estate ranking QuerySpec chain
(REFACTOR_PLAN.md Phase 5, step 4 -- see
docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md), mirroring
tests/test_planning_registry_backed_raster_execution.py's pattern for the
raster NDVI chain.

Asserts the DAG can resolve and execute the new real_estate_spatial_enrich
and real_estate_score ops (registered in
orchestrator/planning/op_catalog.py) against the real plugin registry, in
isolation -- still not wired into the natural-language dispatch path (that
is a later step of the Phase 5 migration).

Composes real_estate_spatial_enrich -> real_estate_score ->
filter_attribute (eligible=true) -> rank_features -> build_report, using
the already-registered generic filter_attribute/rank_features/build_report
ops for the filter/rank/report steps rather than a dedicated real-estate
op for each -- see op_catalog.py's "real_estate_score" notes for why the
ranking step itself does not need its own real-estate op.
"""

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.llm_spec_generator import (
    normalize_llm_query_spec_for_planning,
    query_spec_from_dict,
)
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig


def _sample_properties_query_spec_data() -> dict:
    return {
        "raw_query": "ملک‌های واجد شرایط را رتبه‌بندی کن",
        "goal": "Enrich, score, filter, rank and report real-estate properties.",
        "entities": [
            {
                "ref": "properties",
                "kind": "vector",
                "binding": {},
                "hints": {},
            }
        ],
        "operations": [
            {
                "op": "real_estate_spatial_enrich",
                "inputs": {"vector": "properties"},
                "params": {},
                "output": "enriched",
            },
            {
                "op": "real_estate_score",
                "inputs": {"vector": "enriched"},
                "params": {},
                "output": "scored",
            },
            {
                "op": "filter_attribute",
                "inputs": {"vector": "scored"},
                "params": {"where": {"eligible": True}},
                "output": "eligible_only",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "eligible_only"},
                "params": {
                    "score_field": "score",
                    "rank_field": "rank",
                    "descending": True,
                },
                "output": "ranked",
            },
            {
                "op": "build_report",
                "inputs": {"vector": "ranked"},
                "params": {
                    "score_field": "score",
                    "rank_field": "rank",
                    "name_field": "name",
                },
                "output": "report",
            },
        ],
        "outputs": [
            {
                "kind": "report",
                "source": "report",
                "format": "json",
                "config": {},
            }
        ],
        "metadata": {},
    }


def _sample_properties() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "p1",
                    "name": "آپارتمان مرکز",
                    "price": 12000000000,
                    "kind": "apartment",
                    "distance_to_metro_m": 420,
                    "distance_to_mall_m": 620,
                    "distance_to_main_road_m": 90,
                    "flood_risk": "low",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                    "in_allowed_zone": True,
                },
                "geometry": {"type": "Point", "coordinates": [51.389, 35.689]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "p2",
                    "name": "ویلای لوکس",
                    "price": 18000000000,
                    "kind": "villa",
                    "distance_to_metro_m": 700,
                    "distance_to_mall_m": 310,
                    "distance_to_main_road_m": 60,
                    "flood_risk": "low",
                    "earthquake_risk": "low",
                    "fire_risk": "low",
                    "in_allowed_zone": True,
                },
                "geometry": {"type": "Point", "coordinates": [51.395, 35.692]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "p3",
                    "name": "زمین حاشیه‌ای",
                    "price": 7500000000,
                    "kind": "land",
                    "distance_to_metro_m": 1200,
                    "distance_to_mall_m": 950,
                    "distance_to_main_road_m": 220,
                    "flood_risk": "high",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                    "in_allowed_zone": False,
                },
                "geometry": {"type": "Point", "coordinates": [51.401, 35.684]},
            },
        ],
    }


def test_registry_backed_real_estate_queryspec_chain_executes_with_real_plugins() -> None:
    data = _sample_properties_query_spec_data()

    spec = query_spec_from_dict(data, raw_query_fallback=data["raw_query"])
    normalized = normalize_llm_query_spec_for_planning(spec)
    plan = DeterministicPlanner(PlannerConfig(strict_params=True)).build(normalized)

    registry = CapabilityRegistry.from_plugin_modules(tolerant=False)
    resolver = RegistryCapabilityResolver(registry)

    result = DagExecutor(resolver).execute(
        plan,
        initial_inputs={"properties": _sample_properties()},
    )

    assert result.success is True
    assert result.error is None

    assert [trace.status for trace in result.trace] == ["success"] * 5
    assert [trace.capability_name for trace in result.trace] == [
        "enrich_real_estate_spatial_context",
        "score_real_estate_properties",
        "filter_features",
        "rank_features",
        "build_report",
    ]

    assert list(result.output_nodes) == ["report"]

    report = result.output_nodes["report"]
    report_dict = report.to_dict() if hasattr(report, "to_dict") else report

    assert isinstance(report_dict, dict)

    # Confirms parity with the unchanged scoring formula: same top scores
    # (91.0, 75.0) and ranked order as
    # tests/test_real_estate_ranking_golden.py's direct-handler output for
    # the same input, now produced by the registered-capability DAG chain
    # instead. p3 (the ineligible property) is correctly filtered out by
    # filter_attribute, leaving 2 of 3 properties.
    summary = report_dict["summary"]
    assert summary["total_count"] == 2
    assert summary["top_score"] == 91.0
    assert summary["top_name"] == "ویلای لوکس"

    rows = report_dict["table"]["rows"]
    assert len(rows) == 2
    assert [row["name"] for row in rows] == ["ویلای لوکس", "آپارتمان مرکز"]
    assert [row["rank"] for row in rows] == [1, 2]
