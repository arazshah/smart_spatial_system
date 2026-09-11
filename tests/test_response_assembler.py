"""
Unit tests for orchestrator.response_assembler (REFACTOR_PLAN.md Phase 2,
step 2 -- see docs/PHASE2_UNIFIED_RESPONSE_PLAN.md).

These are independent of tests/test_query_response_shape_golden.py: they
exercise the assembler function directly, input variations to expected
normalized output, without going through any real response source.

Run:
    pytest tests/test_response_assembler.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.response_assembler import (  # noqa: E402
    SCHEMA_VERSION,
    assemble_response,
)


def test_maps_succeeded_status_to_success() -> None:
    result = assemble_response(
        source="real_estate_ranking",
        raw={"status": "succeeded"},
        request_id="req-1",
    )

    assert result["status"] == "success"
    assert result["success"] is True


def test_preserves_already_canonical_success_status() -> None:
    result = assemble_response(
        source="production_response",
        raw={"status": "success"},
        request_id="req-2",
    )

    assert result["status"] == "success"
    assert result["success"] is True


def test_preserves_failed_status_and_marks_not_success() -> None:
    result = assemble_response(
        source="planning",
        raw={"status": "failed"},
        request_id="req-3",
    )

    assert result["status"] == "failed"
    assert result["success"] is False


def test_preserves_partial_success_status_and_marks_not_success() -> None:
    result = assemble_response(
        source="production_response",
        raw={"status": "partial_success"},
        request_id="req-4",
    )

    assert result["status"] == "partial_success"
    assert result["success"] is False


def test_unknown_status_value_falls_back_to_failed() -> None:
    result = assemble_response(
        source="vector_display",
        raw={"status": "something_unexpected"},
        request_id="req-5",
    )

    assert result["status"] == "failed"
    assert result["success"] is False


def test_missing_status_falls_back_to_failed() -> None:
    result = assemble_response(
        source="vector_display",
        raw={},
        request_id="req-6",
    )

    assert result["status"] == "failed"
    assert result["success"] is False


def test_adds_empty_artifacts_list_when_absent() -> None:
    result = assemble_response(
        source="vector_display",
        raw={"status": "success"},
        request_id="req-7",
    )

    assert result["artifacts"] == []


def test_preserves_existing_artifacts_list() -> None:
    artifacts = [{"kind": "vector_layer", "id": "a1"}]

    result = assemble_response(
        source="planning",
        raw={"status": "success", "artifacts": artifacts},
        request_id="req-8",
    )

    assert result["artifacts"] == artifacts


def test_always_sets_schema_version() -> None:
    result = assemble_response(
        source="production_response",
        raw={"status": "success", "schema_version": "should-be-overwritten"},
        request_id="req-9",
    )

    assert result["schema_version"] == SCHEMA_VERSION


def test_uses_given_request_id_when_raw_has_none() -> None:
    result = assemble_response(
        source="planning",
        raw={"status": "success"},
        request_id="req-10",
    )

    assert result["request_id"] == "req-10"


def test_preserves_raws_own_request_id_over_given_one() -> None:
    result = assemble_response(
        source="planning",
        raw={"status": "success", "request_id": "req-from-source"},
        request_id="req-given",
    )

    assert result["request_id"] == "req-from-source"


def test_merges_given_metadata_when_raw_has_none() -> None:
    result = assemble_response(
        source="planning",
        raw={"status": "success"},
        request_id="req-11",
        metadata={"planner_type": "deterministic_query_spec"},
    )

    assert result["metadata"]["planner_type"] == "deterministic_query_spec"
    assert result["metadata"]["response_source"] == "planning"


def test_raws_own_metadata_wins_over_given_metadata() -> None:
    result = assemble_response(
        source="planning",
        raw={"status": "success", "metadata": {"planner_type": "from_raw"}},
        request_id="req-12",
        metadata={"planner_type": "from_param"},
    )

    assert result["metadata"]["planner_type"] == "from_raw"
    assert result["metadata"]["response_source"] == "planning"


def test_does_not_mutate_input_raw_dict() -> None:
    raw = {"status": "succeeded", "answer": "done"}

    result = assemble_response(
        source="real_estate_ranking",
        raw=raw,
        request_id="req-13",
    )

    assert raw == {"status": "succeeded", "answer": "done"}
    assert result is not raw


def test_carries_through_unrelated_keys_unchanged() -> None:
    result = assemble_response(
        source="production_response",
        raw={"status": "success", "answer": "done", "layers": [{"id": "l1"}]},
        request_id="req-14",
    )

    assert result["answer"] == "done"
    assert result["layers"] == [{"id": "l1"}]
