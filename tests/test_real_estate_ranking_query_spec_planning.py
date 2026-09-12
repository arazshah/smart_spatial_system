"""
Tests for REFACTOR_PLAN.md Phase 5, step 5 -- the opt-in rule-based
QuerySpec/DAG path for real-estate ranking queries (see
docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md).

Covers:
    - build_real_estate_ranking_query_spec /
      build_real_estate_ranking_initial_inputs (pure, unit-tested directly)
    - the flag default (False -- OrchestratorService.handle_query keeps
      using the legacy direct handler, per
      tests/test_real_estate_ranking_golden.py)
    - end-to-end parity when the flag is enabled: same top-level ranking
      outcome (order, scores, eligible/rejected counts) as the legacy
      direct handler for the same input, through a real OrchestratorService
      instance and the real plugin registry.

Run:
    pytest tests/test_real_estate_ranking_query_spec_planning.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.service import (  # noqa: E402
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
)
from smart_spatial_system.application.services.query_execution.real_estate_ranking_query_spec import (  # noqa: E402
    build_real_estate_ranking_initial_inputs,
    build_real_estate_ranking_query_spec,
)

REAL_ESTATE_QUERY = (
    "ملک‌هایی را پیدا کن که کمتر از ۵۰۰ متر به مترو یا مرکز خرید نزدیک باشند، "
    "نزدیک خیابان اصلی باشند، ریسک سیل و زلزله و آتش‌سوزی پایین داشته باشند، "
    "به هر ملک امتیاز بده و گزارش رتبه‌بندی تولید کن"
)


def _sample_properties() -> dict[str, Any]:
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


def _fake_real_estate_llm_intent() -> dict[str, Any]:
    return {
        "intent_name": "real_estate_ranking",
        "language": "fa",
        "summary": "رتبه‌بندی املاک بر اساس نزدیکی، ریسک و گزارش",
        "preferred_capabilities": [
            "filter_features",
            "score_features",
            "rank_features",
            "build_report",
        ],
        "required_inputs": {"raster": False, "vector": True, "tabular": False},
        "parameters": {},
        "output_expectation": {"map_layer": True, "table": True, "text": True},
        "confidence": 1.0,
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Unit tests: the pure QuerySpec/initial_inputs builders
# ---------------------------------------------------------------------------


def test_build_real_estate_ranking_query_spec_has_five_chained_operations() -> None:
    spec = build_real_estate_ranking_query_spec("some query")

    assert [op.op for op in spec.operations] == [
        "real_estate_spatial_enrich",
        "real_estate_score",
        "filter_attribute",
        "rank_features",
        "build_report",
    ]
    assert spec.operations[2].params == {"where": {"eligible": True}}
    assert len(spec.outputs) == 1
    assert spec.outputs[0].kind == "report"


def test_build_real_estate_ranking_query_spec_declares_five_entities() -> None:
    spec = build_real_estate_ranking_query_spec("some query")

    assert {entity.ref for entity in spec.entities} == {
        "properties",
        "metro",
        "malls",
        "main_roads",
        "allowed_zones",
    }


def test_build_real_estate_ranking_initial_inputs_fills_empty_layers_when_missing() -> None:
    fc = {"type": "FeatureCollection", "features": []}

    initial_inputs = build_real_estate_ranking_initial_inputs(
        feature_collection=fc,
        spatial_context=None,
    )

    assert initial_inputs["properties"] is fc
    assert initial_inputs["metro"] == {"type": "FeatureCollection", "features": []}
    assert initial_inputs["malls"] == {"type": "FeatureCollection", "features": []}
    assert initial_inputs["main_roads"] == {"type": "FeatureCollection", "features": []}
    assert initial_inputs["allowed_zones"] == {"type": "FeatureCollection", "features": []}


def test_build_real_estate_ranking_initial_inputs_wraps_provided_layer_features() -> None:
    fc = {"type": "FeatureCollection", "features": []}
    metro_feature = {"type": "Feature", "properties": {}, "geometry": None}

    initial_inputs = build_real_estate_ranking_initial_inputs(
        feature_collection=fc,
        spatial_context={"metro": [metro_feature]},
    )

    assert initial_inputs["metro"] == {
        "type": "FeatureCollection",
        "features": [metro_feature],
    }


# ---------------------------------------------------------------------------
# Flag default: OFF (unchanged behavior, confirmed against golden test too)
# ---------------------------------------------------------------------------


def test_flag_defaults_off_and_uses_legacy_direct_handler(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")
    monkeypatch.delenv("REAL_ESTATE_QUERY_SPEC_PLANNING_ENABLED", raising=False)

    svc = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
        )
    )
    monkeypatch.setattr(
        svc, "_maybe_plan_llm_intent", lambda query: _fake_real_estate_llm_intent()
    )

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    assert response["metadata"]["execution_mode"] == "real_estate_ranking_bridge"


# ---------------------------------------------------------------------------
# Flag enabled: end-to-end parity with the legacy direct handler
# ---------------------------------------------------------------------------


def test_flag_enabled_routes_through_query_spec_planning(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")
    monkeypatch.delenv("REAL_ESTATE_QUERY_SPEC_PLANNING_ENABLED", raising=False)

    svc = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            real_estate_query_spec_planning_enabled=True,
        )
    )
    monkeypatch.setattr(
        svc, "_maybe_plan_llm_intent", lambda query: _fake_real_estate_llm_intent()
    )

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    assert response["metadata"]["execution_mode"] == (
        "real_estate_ranking_query_spec_planning"
    )
    assert response["metadata"]["planner_type"] == "rule_based_real_estate"


def test_flag_enabled_produces_same_ranking_outcome_as_legacy_handler(
    tmp_path, monkeypatch
) -> None:
    """
    Concrete parity check: same eligible/rejected split, same top score and
    ranked order as the legacy direct handler
    (tests/test_real_estate_ranking_golden.py) for the same input, now
    produced by the rule-based QuerySpec/DAG path instead.
    """
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")
    monkeypatch.delenv("REAL_ESTATE_QUERY_SPEC_PLANNING_ENABLED", raising=False)

    svc = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            real_estate_query_spec_planning_enabled=True,
        )
    )
    monkeypatch.setattr(
        svc, "_maybe_plan_llm_intent", lambda query: _fake_real_estate_llm_intent()
    )

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    assert response["ok"] is True
    assert response["status"] == "success"

    report_dict = response["report"]

    summary = report_dict["summary"]
    assert summary["total_count"] == 2
    assert summary["top_score"] == 91.0
    assert summary["top_name"] == "ویلای لوکس"

    rows = report_dict["table"]["rows"]
    assert [row["name"] for row in rows] == ["ویلای لوکس", "آپارتمان مرکز"]
    assert [row["rank"] for row in rows] == [1, 2]


def test_flag_enabled_falls_back_to_legacy_handler_when_no_properties_found(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")
    monkeypatch.delenv("REAL_ESTATE_QUERY_SPEC_PLANNING_ENABLED", raising=False)

    svc = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            real_estate_query_spec_planning_enabled=True,
        )
    )
    monkeypatch.setattr(
        svc, "_maybe_plan_llm_intent", lambda query: _fake_real_estate_llm_intent()
    )

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={},
    )

    # Missing-inputs preflight still runs first regardless of the flag.
    assert response["ok"] is False
    assert response["result"]["type"] == "missing_required_inputs"


def test_flag_enabled_falls_back_to_legacy_handler_when_dag_execution_fails(
    tmp_path, monkeypatch
) -> None:
    """
    Safety-net test: if the QuerySpec/DAG path raises for any reason (a
    real bug, a plugin error, anything unexpected -- not just the expected
    "no properties found" guard), the response still succeeds via the
    legacy direct handler instead of surfacing an error to the user. Same
    safety pattern as _try_handle_query_with_planning's broad except
    clause, now verified for the real-estate planning path too.
    """
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")
    monkeypatch.delenv("REAL_ESTATE_QUERY_SPEC_PLANNING_ENABLED", raising=False)

    svc = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            real_estate_query_spec_planning_enabled=True,
        )
    )
    monkeypatch.setattr(
        svc, "_maybe_plan_llm_intent", lambda query: _fake_real_estate_llm_intent()
    )

    import smart_spatial_system.application.services.query_execution_service as qes

    def _broken_runner_factory(registry):
        raise RuntimeError("simulated DAG runner construction failure")

    monkeypatch.setattr(qes, "make_registry_planning_runner", _broken_runner_factory)

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    # Still a full, successful response -- via the legacy direct handler,
    # not the (broken) QuerySpec/DAG path.
    assert response["ok"] is True
    assert response["status"] == "success"
    assert response["metadata"]["execution_mode"] == "real_estate_ranking_bridge"
