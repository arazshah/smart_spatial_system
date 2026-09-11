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

    assert "return self.query_execution_service._llm_planning_enabled()" in source
    assert "return self.query_execution_service._query_spec_planning_enabled()" in source
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


def test_query_execution_service_contains_real_estate_spatial_scoring_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _extract_real_estate_spatial_context_from_inputs(" in source
    assert "def _enrich_property_feature_collection_with_spatial_context(" in source
    assert "def _score_real_estate_property(" in source
    assert "def _evaluate_real_estate_eligibility(" in source


def test_query_execution_service_contains_real_estate_report_document_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _build_real_estate_pdf_report_payload(" in source
    assert "def _try_render_real_estate_ranking_document(" in source
    assert "def _build_real_estate_analysis_inspector(" in source


def test_query_execution_service_contains_real_estate_ranking_direct_handler() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _try_handle_real_estate_ranking_directly(" in source
    assert "real_estate_ranking_bridge" in source
    assert "real_estate_ranking" in source


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


def test_query_execution_service_contains_resolve_input_references() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _resolve_input_references(" in source
    assert "self.upload_reference_resolver.resolve_inputs(inputs)" in source
    assert "stage=\"resolve_input_references\"" in source


def test_orchestrator_resolve_input_references_delegates_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "return self.query_execution_service._resolve_input_references(inputs)" in source


def test_query_execution_service_has_only_expected_orchestrator_context_dependencies() -> None:
    import ast

    query_path = Path("smart_spatial_system/application/services/query_execution_service.py")
    orch_path = Path("orchestrator/service.py")

    query_source = query_path.read_text(encoding="utf-8")
    orch_source = orch_path.read_text(encoding="utf-8")

    query_tree = ast.parse(query_source)
    orch_tree = ast.parse(orch_source)

    query_methods = set()
    orch_methods = set()

    for node in query_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "QueryExecutionService":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    query_methods.add(item.name)

    for node in orch_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "OrchestratorService":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    orch_methods.add(item.name)

    self_calls = set()
    for node in ast.walk(query_tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr.startswith("_")
        ):
            self_calls.add(node.attr)

    external_dependencies = {
        name
        for name in self_calls
        if name not in query_methods
        and name in orch_methods
    }

    expected_dependencies = {
        "_build_enabled_registry_view",
        "_build_router",
        "_enabled_capability_names",
        "_persist_outputs_for_record",
        "_remember",
    }

    assert external_dependencies == expected_dependencies


def test_query_spec_planning_uses_enabled_registry_view() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "make_registry_planning_runner(self._build_enabled_registry_view())" in source
    assert "make_registry_planning_runner(self.registry)" not in source
