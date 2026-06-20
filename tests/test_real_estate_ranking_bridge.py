from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.service import OrchestratorService


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
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.389, 35.689],
                },
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
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.395, 35.692],
                },
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
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.401, 35.684],
                },
            },
        ],
    }


def _fake_llm_intent() -> dict[str, Any]:
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
        "required_inputs": {
            "raster": False,
            "vector": True,
            "tabular": False,
        },
        "parameters": {},
        "output_expectation": {
            "map_layer": True,
            "table": True,
            "text": True,
        },
        "confidence": 1.0,
        "warnings": [],
    }


def _service_without_real_llm(monkeypatch) -> OrchestratorService:
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")

    svc = OrchestratorService()

    # تست نباید به AvalAI/OpenAI وصل شود.
    monkeypatch.setattr(
        svc,
        "_maybe_plan_llm_intent",
        lambda query: _fake_llm_intent(),
    )

    return svc


def test_real_estate_ranking_query_routes_to_ranking_bridge(monkeypatch):
    svc = _service_without_real_llm(monkeypatch)

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    assert response["ok"] is True
    assert response["status"] == "succeeded"
    assert response["result"]["type"] == "real_estate_ranking"
    assert response["metadata"]["execution_mode"] == "real_estate_ranking_bridge"

    summary = response["summary"]
    assert summary["candidate_count"] == 3
    assert summary["eligible_count"] == 2
    assert summary["rejected_count"] == 1
    assert summary["top_property"] == "ویلای لوکس"

    ranking = response["result"]["ranking"]
    assert len(ranking) == 2
    assert ranking[0]["rank"] == 1
    assert ranking[0]["name"] == "ویلای لوکس"
    assert ranking[1]["rank"] == 2
    assert ranking[1]["name"] == "آپارتمان مرکز"
    assert ranking[0]["score"] > ranking[1]["score"]

    rejected = response["result"]["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["name"] == "زمین حاشیه‌ای"
    assert "high_flood_risk" in rejected[0]["reasons"]
    assert "outside_allowed_construction_zone" in rejected[0]["reasons"]


def test_real_estate_ranking_outputs_tables_layers_and_report(monkeypatch):
    svc = _service_without_real_llm(monkeypatch)

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    outputs = response["outputs"]

    assert outputs["vectors"]
    assert outputs["vectors"][0]["id"] == "ranked_properties"
    assert outputs["vectors"][0]["role"] == "map_layer"
    assert len(outputs["vectors"][0]["geojson"]["features"]) == 2

    assert response["layers"]
    assert response["layers"][0]["id"] == "ranked_properties"
    assert response["layers"][0]["type"] == "vector"

    table_by_id = {table["id"]: table for table in outputs["tables"]}
    assert "property_ranking" in table_by_id
    assert "rejected_properties" in table_by_id

    assert len(table_by_id["property_ranking"]["rows"]) == 2
    assert len(table_by_id["rejected_properties"]["rows"]) == 1

    assert outputs["reports"]
    assert outputs["reports"][0]["id"] == "real_estate_ranking_report"
    assert outputs["reports"][0]["format"] == "json"
    assert outputs["reports"][0]["data"]["title"]


def test_real_estate_ranking_audit_trace_contains_expected_capabilities(monkeypatch):
    svc = _service_without_real_llm(monkeypatch)

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    audit = response["audit_record"]
    assert audit["status"] == "success"
    assert audit["execution_mode"] == "real_estate_ranking_bridge"

    trace_capabilities = [
        step["capability_name"]
        for step in audit["trace"]
    ]

    assert trace_capabilities == [
        "filter_features",
        "score_features",
        "rank_features",
        "build_report",
        "render_pdf",
    ]

    assert all(step["status"] == "success" for step in audit["trace"][:4])
    assert audit["trace"][4]["status"] in {"success", "warning", "failed", "skipped"}


def test_real_estate_ranking_without_inputs_returns_controlled_failure(monkeypatch):
    svc = _service_without_real_llm(monkeypatch)

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={},
    )

    assert response["ok"] is False
    assert response["status"] == "failed"
    assert response["result"]["type"] == "missing_required_inputs"
    assert response["result"]["domain"] == "real_estate_spatial_ranking"
    assert response["outputs"] == {}
    assert response["layers"] == []


def test_simple_vector_display_is_not_hijacked_by_ranking_bridge(monkeypatch):
    monkeypatch.setenv("LLM_PLANNING_ENABLED", "false")
    svc = OrchestratorService()

    monkeypatch.setattr(
        svc,
        "_maybe_plan_llm_intent",
        lambda query: {
            "intent_name": "vector_display",
            "language": "fa",
            "summary": "نمایش لایه برداری روی نقشه",
            "preferred_capabilities": ["inspect_vector", "display_vector_layer"],
            "required_inputs": {
                "raster": False,
                "vector": True,
                "tabular": False,
            },
            "parameters": {},
            "output_expectation": {
                "map_layer": True,
                "table": False,
                "text": True,
            },
            "confidence": 1.0,
            "warnings": [],
        },
    )

    response = svc.handle_query(
        query="این ملک‌ها را روی نقشه نمایش بده و خلاصه کن",
        inputs={"vector": _sample_properties()},
    )

    assert response["ok"] is True
    assert response["result"]["type"] != "real_estate_ranking"
    assert response["metadata"]["execution_mode"] == "capability_bridge"


def test_real_estate_ranking_response_contains_analysis_inspector(monkeypatch):
    svc = _service_without_real_llm(monkeypatch)

    response = svc.handle_query(
        query=REAL_ESTATE_QUERY,
        inputs={"properties": _sample_properties()},
    )

    inspector = response.get("inspector")

    assert isinstance(inspector, dict)
    assert inspector["kind"] == "analysis_inspector"
    assert inspector["domain"] == "real_estate_spatial_ranking"
    assert inspector["status"] == "succeeded"

    assert isinstance(inspector.get("summary_cards"), list)
    assert inspector["summary_cards"]
    assert {card["id"] for card in inspector["summary_cards"]} >= {
        "candidate_count",
        "eligible_count",
        "rejected_count",
        "top_property",
        "top_score",
    }

    assert isinstance(inspector.get("outputs"), list)
    assert any(item.get("type") == "vector" for item in inspector["outputs"])
    assert any(item.get("type") == "table" for item in inspector["outputs"])
    assert any(item.get("type") == "report" for item in inspector["outputs"])

    assert isinstance(inspector.get("documents"), list)
    assert inspector["documents"] == [
        item for item in inspector["documents"]
    ]

    if response.get("outputs", {}).get("documents"):
        assert inspector["documents"]
        assert any(item.get("type") == "document" for item in inspector["outputs"])
        assert inspector.get("primary_actions")

    assert isinstance(inspector.get("layers"), list)
    assert inspector["layers"]
    assert inspector["layers"][0]["id"] == "ranked_properties"

    assert isinstance(inspector.get("trace"), list)
    assert [step["capability_name"] for step in inspector["trace"]] == [
        "filter_features",
        "score_features",
        "rank_features",
        "build_report",
        "render_pdf",
    ]

    audit_outputs = response["audit_record"]["outputs"]
    assert "document_ids" in audit_outputs
