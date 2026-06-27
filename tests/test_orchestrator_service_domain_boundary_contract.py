"""
Domain boundary regression tests for orchestrator.service.

orchestrator.service is the application orchestration facade. It must not expose
domain-specific real_estate helper methods. Domain-specific behavior must be
owned by plugins/capabilities or lower-level compatibility modules while the
service facade remains domain-agnostic.
"""

from __future__ import annotations

from pathlib import Path


SERVICE_PATH = Path("orchestrator/service.py")


def test_orchestrator_service_does_not_expose_real_estate_helper_wrappers() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")

    forbidden_method_defs = [
        "def _try_handle_missing_real_estate_inputs(",
        "def _is_real_estate_analysis_query(",
        "def _has_any_real_estate_payload(",
        "def _looks_like_real_estate_ranking_query(",
        "def _extract_real_estate_spatial_context_from_inputs(",
        "def _score_real_estate_property(",
        "def _evaluate_real_estate_eligibility(",
        "def _build_real_estate_pdf_report_payload(",
        "def _try_render_real_estate_ranking_document(",
        "def _build_real_estate_analysis_inspector(",
        "def _try_handle_real_estate_ranking_directly(",
    ]

    for method_def in forbidden_method_defs:
        assert method_def not in source


def test_orchestrator_service_does_not_delegate_real_estate_helpers_to_query_execution_service() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")

    forbidden_delegations = [
        "return self.query_execution_service._try_handle_missing_real_estate_inputs(",
        "return self.query_execution_service._is_real_estate_analysis_query(",
        "return self.query_execution_service._has_any_real_estate_payload(",
        "return self.query_execution_service._looks_like_real_estate_ranking_query(",
        "return self.query_execution_service._extract_real_estate_spatial_context_from_inputs(",
        "return self.query_execution_service._score_real_estate_property(",
        "return self.query_execution_service._evaluate_real_estate_eligibility(",
        "return self.query_execution_service._build_real_estate_pdf_report_payload(",
        "return self.query_execution_service._try_render_real_estate_ranking_document(",
        "return self.query_execution_service._build_real_estate_analysis_inspector(",
        "return self.query_execution_service._try_handle_real_estate_ranking_directly(",
    ]

    for delegation in forbidden_delegations:
        assert delegation not in source
