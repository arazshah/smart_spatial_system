"""
LLM intent adapter helpers.

This module contains small helpers for optional LLM intent planning and
intent-to-query normalization. It intentionally contains no query orchestration
and no plugin execution.
"""

from __future__ import annotations

import os
from typing import Any


class LLMIntentAdapterError(RuntimeError):
    """Raised when LLM intent planning fails."""


def is_llm_planning_enabled(config: Any = None) -> bool:
    """
    Whether LLM-based intent planning is enabled for /query.

    Precedence (REFACTOR_PLAN.md Phase 3 - config is the source of truth,
    env is a deployment-level override on top of it, not the only source):

      1. LLM_PLANNING_ENABLED env var, if explicitly set.
      2. config.llm_planning_enabled, if config is provided and has it.
      3. False (unchanged default).
    """
    env_value = os.getenv("LLM_PLANNING_ENABLED")
    if env_value is not None:
        return env_value.strip().lower() in {"1", "true", "yes", "on"}

    if config is not None:
        configured = getattr(config, "llm_planning_enabled", None)
        if configured is not None:
            return bool(configured)

    return False


def apply_intent_to_query(
    query: str,
    intent: dict[str, Any] | None,
) -> str:
    """
    Rewrite the natural query so the deterministic parser can trigger
    the right workflow.

    Currently specialized for vegetation_extraction and raster_vectorization.
    """
    if not intent or not isinstance(intent, dict):
        return query

    intent_name = str(intent.get("intent_name") or "")

    if intent_name == "vegetation_extraction":
        params = intent.get("parameters") or {}

        try:
            threshold = float(params.get("threshold", 0.3))
        except Exception:
            threshold = 0.3

        vectorize = bool(params.get("vectorize", False))

        parts = [
            "NDVI vegetation extraction.",
            f"greater than {threshold}.",
        ]

        if vectorize:
            parts.append("polygon vectorize استخراج کن.")

        parts.append(f"original_query: {query}")

        return " ".join(parts)

    if intent_name == "raster_vectorization":
        return "NDVI raster_to_vector polygon استخراج کن. " + f"original_query: {query}"

    return query


def plan_intent_with_llm(
    *,
    query: str,
    available_capabilities: list[str],
) -> dict[str, Any]:
    """
    Plan geospatial query intent using the configured LLM.

    This function does not execute plugins.
    """
    from orchestrator.llm_client import LLMClientError, LLMConfigError
    from orchestrator.llm_intent_planner import (
        LLMIntentPlannerError,
    )
    from orchestrator.llm_intent_planner import (
        plan_intent_with_llm as _plan_intent_with_llm,
    )

    try:
        return _plan_intent_with_llm(
            query=query,
            available_capabilities=available_capabilities,
        )
    except (LLMConfigError, LLMClientError, LLMIntentPlannerError) as exc:
        raise LLMIntentAdapterError(str(exc)) from exc
