"""
orchestrator.response_assembler

REFACTOR_PLAN.md Phase 2 (unified response assembler) -- see
docs/PHASE2_UNIFIED_RESPONSE_PLAN.md for the full migration plan and the
concrete "success" vs. "succeeded" inconsistency this exists to fix.

This module is not wired into any response source yet (step 2 of the plan).
It intentionally does the *minimum* normalization for now:

    - coerce ``status`` into the ``success``/``partial_success``/``failed``
      vocabulary already used by ``orchestrator.production_response``
      (mapping the legacy past-tense ``"succeeded"`` to ``"success"``;
      anything not in that vocabulary is treated as ``"failed"``, fail-safe)
    - derive ``success`` (bool) from the normalized status
    - ensure ``schema_version`` and ``artifacts`` (defaulting to an empty
      list) are always present
    - ensure ``request_id`` and ``metadata`` are present, without
      overwriting values a response source already set

Each response source is wired through this one at a time in later steps of
the plan; this step only adds the entry point and its own unit tests.
"""

from __future__ import annotations

from typing import Any

from orchestrator.production_response import VALID_RESPONSE_STATUSES

SCHEMA_VERSION = "1.0"

_STATUS_ALIASES = {
    "succeeded": "success",
}


def _normalize_status(raw_status: Any) -> str:
    if not isinstance(raw_status, str):
        return "failed"

    mapped = _STATUS_ALIASES.get(raw_status, raw_status)
    if mapped in VALID_RESPONSE_STATUSES:
        return mapped

    return "failed"


def assemble_response(
    *,
    source: str,
    raw: dict[str, Any],
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Normalize one response source's raw dict into the shared envelope
    fields, without discarding anything that source already set.

    Args:
        source:
            Identifier for the response source being assembled (e.g.
            "production_response", "planning", "vector_display",
            "real_estate_ranking"). Recorded under
            ``metadata["response_source"]`` for debugging; never used to
            branch normalization behavior.
        raw:
            The response source's own response dict, as-is.
        request_id:
            Request id to use if ``raw`` does not already carry one.
        metadata:
            Extra metadata to merge in under keys ``raw`` does not already
            set.

    Returns:
        A new dict (``raw`` is not mutated) with ``schema_version``,
        ``status``, ``success``, ``artifacts``, ``request_id`` and
        ``metadata`` guaranteed present; every other key from ``raw`` is
        carried through unchanged.
    """
    assembled = dict(raw)

    assembled["schema_version"] = SCHEMA_VERSION
    assembled["status"] = _normalize_status(raw.get("status"))
    assembled["success"] = assembled["status"] == "success"
    assembled.setdefault("artifacts", [])
    assembled.setdefault("request_id", request_id)

    merged_metadata = dict(metadata or {})
    merged_metadata.update(assembled.get("metadata") or {})
    merged_metadata.setdefault("response_source", source)
    assembled["metadata"] = merged_metadata

    return assembled
