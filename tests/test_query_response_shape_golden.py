"""
Golden tests for the four independently-shaped `/query` response sources
(REFACTOR_PLAN.md Phase 2, step 1 -- see docs/PHASE2_UNIFIED_RESPONSE_PLAN.md).

These capture full response dicts, field by field, from each of:

    1. Legacy fallback (ProductionResponseBuilder), via
       OrchestratorService.handle_query with a raster query that has no
       direct-dispatch handler.
    2. Real-estate ranking direct response, via
       OrchestratorService.handle_query with a real-estate ranking query
       (reuses the fixtures from tests/test_real_estate_ranking_bridge.py).
    3. Vector display direct response, via
       try_handle_vector_display_directly directly (reuses the fixture
       pattern from tests/test_vector_display_handler.py).
    4. QuerySpec planning response, via
       OrchestratorService._try_handle_query_with_planning directly
       (reuses the fixture pattern from tests/test_orchestrator_service.py).

Purpose: this is the regression net for REFACTOR_PLAN.md Phase 2's response
assembler migration. No production code is changed by this file -- only
capture-and-assert. Any *intentional* normalization in a later step must
update the specific asserted value here, not silently pass.

Run:
    pytest tests/test_query_response_shape_golden.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.service import (  # noqa: E402
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
)
from smart_spatial_system.application.services.vector_display_handler import (  # noqa: E402
    try_handle_vector_display_directly,
)


def _make_service(tmp_path: Path, **kwargs: Any) -> OrchestratorService:
    return OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            **kwargs,
        )
    )


# ---------------------------------------------------------------------------
# Source 1: legacy fallback (ProductionResponseBuilder)
# ---------------------------------------------------------------------------

SATELLITE_RASTER_2BAND = {
    "data": [
        [
            [1, 1, 1],
            [1, 1, 1],
        ],
        [
            [2, 1, 4],
            [1, 3, 0.5],
        ],
    ],
    "metadata": {
        "transform": [10, 0, 100, 0, -10, 200],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}

NDVI_QUERY = (
    "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است "
    "را به پلیگون تبدیل کن"
)


def test_golden_legacy_production_response_shape(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    payload = service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={"nir": 2, "red": 1},
    )

    # Top-level envelope -- present-tense "success" per ProductionResponseBuilder.
    assert payload["status"] == "success"
    assert payload["ok"] is True
    assert isinstance(payload["request_id"], str) and payload["request_id"]
    assert isinstance(payload["answer"], str) and payload["answer"]
    assert isinstance(payload["confidence"], dict)
    assert set(payload["confidence"].keys()) == {
        "level",
        "score",
        "llm_action",
        "is_ambiguous",
        "competitive_gap",
    }
    assert isinstance(payload["audit_ref"], dict)
    assert set(payload["audit_ref"].keys()) >= {
        "request_id",
        "query_hash",
        "status",
        "plan_steps",
    }
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload.get("next_actions", []), list)
    assert isinstance(payload["outputs"], dict)
    assert "summary" in payload["outputs"]
    assert isinstance(payload["artifacts"], list)
    assert isinstance(payload["layers"], list)
    # Phase 2 step 3.1: wired through orchestrator.response_assembler --
    # schema_version and success are now always present (status was
    # already "success", so success here is just its bool projection).
    assert payload["schema_version"] == "1.0"
    assert payload["success"] is True
    assert set(payload.keys()) == {
        "answer",
        "artifacts",
        "audit_ref",
        "confidence",
        "documents",
        "layers",
        "message",
        "metadata",
        "next_actions",
        "ok",
        "outputs",
        "query_hash",
        "request_id",
        "schema_version",
        "status",
        "steps",
        "success",
        "trace",
        "warnings",
    }


# ---------------------------------------------------------------------------
# Source 2: real-estate ranking direct response
# ---------------------------------------------------------------------------

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


def test_golden_real_estate_ranking_response_shape(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")
    svc = OrchestratorService()
    monkeypatch.setattr(
        svc, "_maybe_plan_llm_intent", lambda query: _fake_real_estate_llm_intent()
    )

    payload = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    # Top-level envelope -- past-tense "succeeded", NOT "success" (the
    # documented inconsistency this phase exists to fix).
    assert payload["ok"] is True
    assert payload["status"] == "succeeded"
    assert "confidence" not in payload
    assert "audit_ref" not in payload
    assert isinstance(payload["result"], dict)
    assert payload["result"]["type"] == "real_estate_ranking"
    assert isinstance(payload["summary"], dict)
    assert isinstance(payload["outputs"], dict)
    assert set(payload["outputs"].keys()) >= {"vectors", "tables", "reports"}
    assert isinstance(payload["layers"], list)
    assert payload["metadata"]["execution_mode"] == "real_estate_ranking_bridge"

    # Nested per-output entries use present-tense "success", even though the
    # top-level status above is "succeeded" -- the inconsistency is *within*
    # this one response, not just across sources.
    assert payload["outputs"]["vectors"][0]["role"] == "map_layer"
    assert isinstance(payload["audit_record"], dict)
    assert payload["audit_record"]["status"] == "success"
    assert set(payload.keys()) == {
        "answer",
        "artifacts",
        "audit_record",
        "documents",
        "files",
        "inspector",
        "layers",
        "message",
        "metadata",
        "next_actions",
        "ok",
        "outputs",
        "query",
        "report",
        "request_id",
        "result",
        "status",
        "summary",
        "trace",
        "warnings",
    }


# ---------------------------------------------------------------------------
# Source 3: vector display direct response
# ---------------------------------------------------------------------------

FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "A"},
            "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
        }
    ],
}


class _FakeVectorRouter:
    def resolve(self, capability_name: str):
        if capability_name == "inspect_vector":
            return SimpleNamespace(
                plugin_id="vector_tools",
                output_kind="summary",
                callable=lambda *, vector: {
                    "summary": {
                        "feature_count": 1,
                        "geometry_counts": {"Point": 1},
                        "property_keys": ["name"],
                    }
                },
            )
        if capability_name == "display_vector_layer":
            return SimpleNamespace(
                plugin_id="vector_tools",
                output_kind="map_layer",
                callable=lambda *, vector, layer_id, name, visible: {
                    "message": "Vector layer is ready.",
                    "summary": {
                        "feature_count": 1,
                        "geometry_counts": {"Point": 1},
                        "property_keys": ["name"],
                    },
                    "outputs": {
                        "vectors": [
                            {
                                "id": layer_id,
                                "name": name,
                                "format": "geojson",
                                "role": "map_layer",
                                "geojson": vector,
                            }
                        ],
                        "rasters": [],
                        "tables": [],
                    },
                    "layers": [
                        {
                            "id": layer_id,
                            "name": name,
                            "type": "vector",
                            "format": "geojson",
                            "visible": visible,
                            "geojson": vector,
                        }
                    ],
                },
            )
        raise KeyError(capability_name)


class _FakeVectorContext:
    def __init__(self) -> None:
        self.remembered: list[dict[str, Any]] = []

    def _is_real_estate_analysis_query(self, query: str) -> bool:
        return False

    def _build_enabled_router(self):
        return _FakeVectorRouter()

    def _remember(self, *, request_id, record):
        self.remembered.append({"request_id": request_id, "record": record})


def test_golden_vector_display_response_shape() -> None:
    context = _FakeVectorContext()

    payload = try_handle_vector_display_directly(
        context,
        query="show vector layer",
        inputs={},
        resolved_inputs={"geojson": FEATURE_COLLECTION},
        final_request_id="req-golden-vector-1",
        final_metadata={"source": "test"},
        band_map={"red": 1},
        user_context={"user": "demo"},
        json_safe=lambda value: value,
    )

    assert payload is not None
    # Top-level envelope -- present-tense "succeeded" here too, matching
    # real-estate's inconsistency, distinct from ProductionResponseBuilder's
    # present-tense "success".
    assert payload["ok"] is True
    assert payload["status"] == "succeeded"
    assert "confidence" not in payload
    assert "audit_ref" not in payload
    assert payload["request_id"] == "req-golden-vector-1"
    assert payload["result"]["type"] == "vector_display"
    assert isinstance(payload["summary"], dict)
    assert payload["outputs"]["vectors"][0]["id"] == "active_vector"
    assert payload["layers"][0]["id"] == "active_vector"
    assert payload["metadata"]["execution_mode"] == "capability_bridge"
    assert payload["metadata"]["legacy_handler_name"] == "vector_display"
    # Notably, this source has NO "answer"/"warnings"/"next_actions" keys at
    # all -- unlike the legacy, real-estate, and planning sources, which all
    # include them (even when empty).
    assert set(payload.keys()) == {
        "artifacts",
        "audit_record",
        "documents",
        "files",
        "layers",
        "message",
        "metadata",
        "ok",
        "outputs",
        "query",
        "request_id",
        "result",
        "status",
        "summary",
        "trace",
    }


# ---------------------------------------------------------------------------
# Source 4: QuerySpec planning response
# ---------------------------------------------------------------------------


def test_golden_query_spec_planning_response_shape(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.planning.runner import make_static_planning_runner
    from orchestrator.planning.spec import (
        EntitySpec,
        OperationSpec,
        OutputSpec,
        QuerySpec,
    )
    from plugins.feature_scoring import rank_features, score_features

    service = _make_service(tmp_path, allow_request_kernel_execution=True)

    monkeypatch.setattr(service, "_query_spec_planning_enabled", lambda: True)
    monkeypatch.setattr(
        service.query_execution_service, "_query_spec_planning_enabled", lambda: True
    )

    class FakeLLMClient:
        pass

    class FakeQuerySpecGenerator:
        def __init__(self, llm_client):
            self.llm_client = llm_client

        def generate(self, query: str, context=None) -> QuerySpec:
            return QuerySpec(
                raw_query=query,
                goal="rank_properties",
                entities=[EntitySpec(ref="properties", kind="vector")],
                operations=[
                    OperationSpec(
                        op="score_features",
                        inputs={"vector": "properties"},
                        params={
                            "scoring_spec": {
                                "output_field": "investment_score",
                                "scale": 100,
                                "factors": [
                                    {
                                        "name": "near_poi",
                                        "field": "distance_to_poi",
                                        "type": "inverse_distance",
                                        "max_distance": 500,
                                        "weight": 0.7,
                                    },
                                    {
                                        "name": "buildable",
                                        "field": "__in_polygon__",
                                        "type": "boolean",
                                        "weight": 0.3,
                                    },
                                ],
                            }
                        },
                        output="scored",
                    ),
                    OperationSpec(
                        op="rank_features",
                        inputs={"vector": "scored"},
                        params={
                            "score_field": "investment_score",
                            "rank_field": "investment_rank",
                        },
                        output="ranked",
                    ),
                ],
                outputs=[OutputSpec(kind="vector", source="ranked")],
            )

    def fake_make_registry_planning_runner(registry):
        return make_static_planning_runner(
            {
                "score_features": score_features,
                "rank_features": rank_features,
            }
        )

    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.OpenAICompatibleLLMClient",
        FakeLLMClient,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.LLMQuerySpecGenerator",
        FakeQuerySpecGenerator,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.make_registry_planning_runner",
        fake_make_registry_planning_runner,
    )

    resolved_inputs = {
        "properties": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0, 0]},
                    "properties": {
                        "name": "A",
                        "distance_to_poi": 100,
                        "__in_polygon__": True,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [1, 1]},
                    "properties": {
                        "name": "B",
                        "distance_to_poi": 500,
                        "__in_polygon__": False,
                    },
                },
            ],
        }
    }

    payload = service._try_handle_query_with_planning(
        query="املاک را امتیاز بده و رتبه‌بندی کن",
        resolved_inputs=resolved_inputs,
        final_request_id="req-golden-planning-1",
        final_metadata={},
        user_context=None,
        original_inputs=resolved_inputs,
        band_map=None,
        metadata=None,
        project_id=None,
    )

    assert payload is not None
    assert payload["ok"] is True
    # Phase 2 step 3.2: wired through orchestrator.response_assembler --
    # top-level status is now normalized to "success" (was "succeeded"),
    # matching ProductionResponseBuilder's vocabulary. Nothing else changed.
    assert payload["status"] == "success"
    assert payload["success"] is True
    assert payload["schema_version"] == "1.0"
    assert "confidence" in payload
    assert set(payload["confidence"].keys()) == {
        "level",
        "score",
        "llm_action",
        "is_ambiguous",
        "competitive_gap",
    }
    assert payload["confidence"]["llm_action"] == "query_spec_planning"
    assert "audit_ref" in payload
    assert set(payload["audit_ref"].keys()) == {
        "request_id",
        "query_hash",
        "status",
        "plan_steps",
    }
    assert payload["request_id"] == "req-golden-planning-1"
    assert isinstance(payload["outputs"], dict)
    assert isinstance(payload["layers"], list)
    assert isinstance(payload["trace"], list)
    assert isinstance(payload["steps"], list)
    assert payload["metadata"]["execution_mode"] in {
        "query_spec_planning",
        "query_spec_planning_kernel_execution",
    }
    assert payload["metadata"]["planner_type"] == "deterministic_query_spec"
    assert set(payload.keys()) == {
        "answer",
        "artifacts",
        "audit_ref",
        "confidence",
        "documents",
        "kernel_execution",
        "kernel_plan",
        "layers",
        "message",
        "metadata",
        "next_actions",
        "ok",
        "outputs",
        "query_hash",
        "request_id",
        "schema_version",
        "status",
        "steps",
        "structured_error",
        "success",
        "trace",
        "warnings",
    }
