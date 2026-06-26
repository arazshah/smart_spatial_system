from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_query_spec_planning_response(
    *,
    planning_result: Any,
    final_metadata: dict[str, Any],
    final_request_id: str,
    query_spec: Any,
    kernel_execution_enabled: bool,
    planning_outputs_to_response_payload: Callable[[Any], tuple[list[dict[str, Any]], dict[str, Any], Any]],
    planning_trace_to_steps: Callable[[list[Any]], list[dict[str, Any]]],
    query_spec_to_dict_func: Callable[[Any], dict[str, Any]],
    redact_sensitive_json: Callable[[Any], Any],
) -> tuple[dict[str, Any], dict[str, Any], bool, Any, Any]:
    from orchestrator.planning.kernel_execution_bridge import (
        compare_kernel_execution_to_planning_outputs,
        kernel_execution_to_summary,
    )
    from orchestrator.planning.kernel_plan_adapter import kernel_plan_to_summary

    layers, outputs, primary_report = planning_outputs_to_response_payload(planning_result)

    kernel_plan_summary = kernel_plan_to_summary(
        getattr(planning_result, "kernel_plan", None)
    )
    kernel_execution_summary = kernel_execution_to_summary(
        getattr(planning_result, "kernel_execution", None)
    )
    kernel_execution_parity = compare_kernel_execution_to_planning_outputs(
        planning_result
    )

    steps = planning_trace_to_steps(
        getattr(planning_result, "trace", []) or []
    )

    success = bool(getattr(planning_result, "success", False))
    planning_error = getattr(planning_result, "error", None)
    planning_structured_error = getattr(
        planning_result,
        "structured_error",
        None,
    )

    answer = (
        "تحلیل با موفقیت انجام شد."
        if success
        else (planning_error or "اجرای تحلیل برنامه‌ریزی‌شده ناموفق بود.")
    )

    if success and outputs["files"]:
        answer = "تحلیل با موفقیت انجام شد و فایل خروجی آماده است."

    if success and primary_report is not None:
        answer = "تحلیل با موفقیت انجام شد و گزارش آماده است."

    planning_metadata = {
        **final_metadata,
        "query_spec_planning_enabled": True,
        "planning_attempted": True,
        "planner_type": "deterministic_query_spec",
        "execution_mode": (
            "query_spec_planning_kernel_execution"
            if kernel_execution_enabled
            else "query_spec_planning"
        ),
        "kernel_execution_enabled": kernel_execution_enabled,
        "query_spec": redact_sensitive_json(query_spec_to_dict_func(query_spec)),
        "planning_summary": {
            "success": success,
            "error": planning_error,
            "structured_error": planning_structured_error,
            "output_nodes": sorted(
                (getattr(planning_result, "output_nodes", None) or {}).keys()
            ),
            "kernel_execution_enabled": kernel_execution_enabled,
            "kernel_plan": kernel_plan_summary,
            "kernel_execution": kernel_execution_summary,
            "kernel_execution_success": (
                None
                if kernel_execution_summary is None
                else bool(kernel_execution_summary.get("success"))
            ),
            "kernel_execution_parity": kernel_execution_parity,
        },
    }

    production_response = {
        "status": "succeeded" if success else "failed",
        "request_id": final_request_id,
        "query_hash": None,
        "answer": answer,
        "message": answer,
        "structured_error": planning_structured_error,
        "outputs": outputs,
        "layers": layers,
        "artifacts": outputs.get("artifacts", []),
        "kernel_plan": kernel_plan_summary,
        "kernel_execution": kernel_execution_summary,
        "steps": steps,
        "confidence": {
            "level": None,
            "score": None,
            "llm_action": "query_spec_planning",
            "is_ambiguous": False,
            "competitive_gap": None,
        },
        "audit_ref": {
            "request_id": final_request_id,
            "query_hash": None,
            "status": "succeeded" if success else "failed",
            "plan_steps": len(steps),
        },
        "warnings": [] if success else [planning_error or "Planning execution failed."],
        "next_actions": [],
        "metadata": planning_metadata,
    }

    if primary_report is not None:
        production_response["report"] = primary_report

    return (
        production_response,
        planning_metadata,
        success,
        planning_error,
        planning_structured_error,
    )
