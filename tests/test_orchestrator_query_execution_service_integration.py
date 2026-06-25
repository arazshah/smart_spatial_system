from __future__ import annotations

from pathlib import Path


def test_orchestrator_service_wires_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "from orchestrator.query_execution_service import "
        "QueryExecutionService, QueryExecutionServiceError"
    ) in source
    assert "self.query_execution_service = QueryExecutionService(self)" in source


def test_orchestrator_handle_query_delegates_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "return self.query_execution_service.handle_query(" in source


def test_orchestrator_query_planning_handler_delegates_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._try_handle_query_with_planning("
        in source
    )


def test_query_execution_service_contains_query_entrypoints() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def handle_query(" in source
    assert "def _try_handle_query_with_planning(" in source
    assert "def __getattr__(self, name: str)" in source


def test_query_execution_service_contains_pure_planning_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _planning_trace_to_steps(" in source
    assert "def _planning_outputs_to_response_payload(" in source
    assert "def _enrich_query_database_params_from_inputs(" in source


def test_orchestrator_pure_planning_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._planning_trace_to_steps("
        in source
    )
    assert (
        "return self.query_execution_service._planning_outputs_to_response_payload("
        in source
    )
    assert (
        "return self.query_execution_service._enrich_query_database_params_from_inputs("
        in source
    )


def test_query_execution_service_contains_planning_flag_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _llm_planning_enabled(" in source
    assert "def _query_spec_planning_enabled(" in source
    assert "def _kernel_execution_enabled(" in source


def test_orchestrator_planning_flag_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "return QueryExecutionService._llm_planning_enabled()" in source
    assert "return QueryExecutionService._query_spec_planning_enabled()" in source
    assert (
        "return self.query_execution_service._kernel_execution_enabled("
        in source
    )


def test_query_execution_service_contains_llm_intent_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _maybe_plan_llm_intent(" in source
    assert "def _apply_intent_to_query(" in source
    assert "def plan_intent_with_llm(" in source
    assert "QueryExecutionServiceError" in source


def test_orchestrator_llm_intent_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._maybe_plan_llm_intent(query)"
        in source
    )
    assert (
        "return QueryExecutionService._apply_intent_to_query(query, intent)"
        in source
    )
    assert (
        "return self.query_execution_service.plan_intent_with_llm(query)"
        in source
    )


def test_query_execution_service_contains_system_status_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _try_handle_system_status_query(" in source
    assert "def _is_system_status_query(" in source


def test_orchestrator_system_status_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._try_handle_system_status_query("
        in source
    )
    assert (
        "return self.query_execution_service._is_system_status_query("
        in source
    )


def test_query_execution_service_contains_vector_direct_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _is_vector_display_query(" in source
    assert "def _is_vector_summary_query(" in source
    assert "def _try_handle_vector_display_directly(" in source


def test_orchestrator_vector_direct_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return QueryExecutionService._is_vector_display_query(query, intent)"
        in source
    )
    assert (
        "return QueryExecutionService._is_vector_summary_query(query, intent)"
        in source
    )
    assert (
        "return self.query_execution_service._try_handle_vector_display_directly("
        in source
    )


def test_query_execution_service_contains_real_estate_detector_input_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _try_handle_missing_real_estate_inputs(" in source
    assert "def _is_real_estate_analysis_query(" in source
    assert "def _has_any_real_estate_payload(" in source
    assert "def _looks_like_real_estate_ranking_query(" in source
    assert "def _extract_property_feature_collection_from_inputs(" in source


def test_orchestrator_real_estate_detector_input_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._try_handle_missing_real_estate_inputs("
        in source
    )
    assert (
        "return self.query_execution_service._is_real_estate_analysis_query("
        in source
    )
    assert (
        "return self.query_execution_service._has_any_real_estate_payload("
        in source
    )
    assert (
        "return self.query_execution_service._looks_like_real_estate_ranking_query("
        in source
    )
    assert (
        "return self.query_execution_service._extract_property_feature_collection_from_inputs("
        in source
    )


def test_query_execution_service_contains_real_estate_spatial_scoring_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _extract_real_estate_spatial_context_from_inputs(" in source
    assert "def _enrich_property_feature_collection_with_spatial_context(" in source
    assert "def _score_real_estate_property(" in source
    assert "def _evaluate_real_estate_eligibility(" in source


def test_orchestrator_real_estate_spatial_scoring_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._extract_real_estate_spatial_context_from_inputs("
        in source
    )
    assert (
        "return self.query_execution_service._enrich_property_feature_collection_with_spatial_context("
        in source
    )
    assert (
        "return self.query_execution_service._score_real_estate_property("
        in source
    )
    assert (
        "return self.query_execution_service._evaluate_real_estate_eligibility("
        in source
    )


def test_query_execution_service_contains_real_estate_report_document_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _build_real_estate_pdf_report_payload(" in source
    assert "def _try_render_real_estate_ranking_document(" in source
    assert "def _build_real_estate_analysis_inspector(" in source


def test_orchestrator_real_estate_report_document_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._build_real_estate_pdf_report_payload("
        in source
    )
    assert (
        "return self.query_execution_service._try_render_real_estate_ranking_document("
        in source
    )
    assert (
        "return self.query_execution_service._build_real_estate_analysis_inspector("
        in source
    )


def test_query_execution_service_contains_real_estate_ranking_direct_handler() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _try_handle_real_estate_ranking_directly(" in source
    assert "real_estate_ranking_bridge" in source
    assert "real_estate_ranking" in source


def test_orchestrator_real_estate_ranking_direct_handler_delegates_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._try_handle_real_estate_ranking_directly("
        in source
    )


def test_query_execution_service_contains_real_estate_geometry_metric_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    helper_names = [
        "_feature_point_lonlat",
        "_point_in_ring_lonlat",
        "_point_in_polygon_feature_lonlat",
        "_lonlat_to_local_xy_m",
        "_distance_point_to_segment_m",
        "_distance_point_to_point_m",
        "_distance_point_to_geometry_m",
        "_nearest_distance_to_features_m",
        "_has_metric_value",
        "_has_bool_like_value",
        "_normalize_risk_level",
        "_to_float_or_none",
    ]

    for helper_name in helper_names:
        assert f"def {helper_name}(" in source


def test_orchestrator_real_estate_geometry_metric_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    helper_names = [
        "_feature_point_lonlat",
        "_point_in_ring_lonlat",
        "_point_in_polygon_feature_lonlat",
        "_lonlat_to_local_xy_m",
        "_distance_point_to_segment_m",
        "_distance_point_to_point_m",
        "_distance_point_to_geometry_m",
        "_nearest_distance_to_features_m",
        "_has_metric_value",
        "_has_bool_like_value",
        "_normalize_risk_level",
        "_to_float_or_none",
    ]

    for helper_name in helper_names:
        assert f"return self.query_execution_service.{helper_name}(" in source


def test_query_execution_service_contains_geojson_discovery_summary_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _read_geojson_path_if_possible(" in source
    assert "def _find_geojson_like(" in source
    assert "def _summarize_feature_collection(" in source


def test_orchestrator_geojson_discovery_summary_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "return QueryExecutionService._read_geojson_path_if_possible(value)" in source
    assert "return QueryExecutionService._find_geojson_like(" in source
    assert (
        "return QueryExecutionService._summarize_feature_collection(feature_collection)"
        in source
    )


def test_query_execution_service_contains_new_request_id_helper() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _new_request_id(" in source
    assert "uuid.uuid4()" in source


def test_orchestrator_new_request_id_delegates_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "return self.query_execution_service._new_request_id()" in source
