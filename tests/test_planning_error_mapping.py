from __future__ import annotations

from orchestrator.planning.dag_executor import DagExecutionError, DagValidationError
from orchestrator.planning.error_mapping import planning_exception_to_structured_error
from orchestrator.planning.llm_spec_generator import LLMSpecGenerationError
from orchestrator.planning.planner import PlanningError


def test_planning_error_mapping_classifies_llm_spec_generation_error_as_retryable() -> None:
    payload = planning_exception_to_structured_error(
        LLMSpecGenerationError("LLM HTTP error 504 Gateway Timeout"),
        stage="query_spec_generation",
    )

    assert payload["code"] == "planning.llm_spec_generation_failed"
    assert payload["category"] == "planning_error"
    assert payload["retryable"] is True
    assert payload["source"] == "orchestrator_service"
    assert payload["details"]["stage"] == "query_spec_generation"
    assert payload["details"]["exception_type"] == "LLMSpecGenerationError"
    assert isinstance(payload["details"]["exception_chain"], list)


def test_planning_error_mapping_classifies_planning_error() -> None:
    payload = planning_exception_to_structured_error(
        PlanningError("Unsupported operation 'bad_capability'."),
        stage="planner_build",
    )

    assert payload["code"] == "planning.failed"
    assert payload["category"] == "planning_error"
    assert payload["retryable"] is False
    assert payload["source"] == "orchestrator_service"
    assert payload["details"]["stage"] == "planner_build"
    assert payload["details"]["exception_type"] == "PlanningError"


def test_planning_error_mapping_classifies_dag_validation_error() -> None:
    payload = planning_exception_to_structured_error(
        DagValidationError("Unknown output node: missing_output."),
        stage="dag_validation",
    )

    assert payload["code"] == "dag.validation_failed"
    assert payload["category"] == "validation_error"
    assert payload["retryable"] is False
    assert payload["source"] == "orchestrator_service"
    assert payload["details"]["stage"] == "dag_validation"
    assert payload["details"]["exception_type"] == "DagValidationError"


def test_planning_error_mapping_classifies_dag_execution_error() -> None:
    payload = planning_exception_to_structured_error(
        DagExecutionError("Reference '$inputs.layer' could not be resolved."),
        stage="dag_execution",
    )

    assert payload["code"] == "dag.execution_failed"
    assert payload["category"] == "planning_error"
    assert payload["retryable"] is False
    assert payload["source"] == "orchestrator_service"
    assert payload["details"]["stage"] == "dag_execution"
    assert payload["details"]["exception_type"] == "DagExecutionError"


def test_planning_error_mapping_classifies_value_error_as_validation() -> None:
    payload = planning_exception_to_structured_error(
        ValueError("Invalid QuerySpec contract."),
        stage="query_spec_contract_validation",
    )

    assert payload["code"] == "planning.validation_failed"
    assert payload["category"] == "validation_error"
    assert payload["retryable"] is False
    assert payload["source"] == "orchestrator_service"
    assert payload["details"]["stage"] == "query_spec_contract_validation"
    assert payload["details"]["exception_type"] == "ValueError"


def test_planning_error_mapping_redacts_sensitive_details() -> None:
    payload = planning_exception_to_structured_error(
        RuntimeError("Planning runtime failed."),
        stage="query_spec_planning",
        details={
            "database": "osm_tehran",
            "password": "must-not-leak",
            "dsn": "postgresql://user:secret@localhost/db",
        },
    )

    assert payload["code"] == "planning.runtime_failed"
    assert payload["category"] == "planning_error"
    assert payload["details"]["database"] == "osm_tehran"
    assert payload["details"]["password"] == "<redacted>"
    assert payload["details"]["dsn"] == "<redacted>"
