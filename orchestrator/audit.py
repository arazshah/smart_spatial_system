"""
orchestrator.audit

Execution audit record builder.

This module creates a compact JSON-like audit record for each natural-query
execution.

The audit record is designed for:
    - debugging
    - traceability
    - self-learning statistics
    - feedback loops
    - future persistence to file/database

It intentionally stores summaries of outputs, not full raster/vector payloads.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AuditConfig:
    """
    Audit record configuration.
    """

    include_trace: bool = True
    include_router_decision: bool = True
    include_llm_gate_result: bool = True
    include_plan_routing_evidence: bool = True
    include_output_metadata: bool = True


class ExecutionAuditBuilder:
    """
    Builds compact execution audit records.
    """

    def __init__(
        self,
        config: AuditConfig | None = None,
    ) -> None:
        self.config = config or AuditConfig()

    def build(
        self,
        *,
        query: str,
        intent: Any,
        plan: Any,
        execution_result: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Build an execution audit record.

        Args:
            query:
                Original user query.
            intent:
                Parsed intent object.
            plan:
                Query plan object.
            execution_result:
                Executor result enriched with router_decision and llm_gate_result.
            request_id:
                Optional external request id. If omitted, UUID4 is generated.
        """
        final_request_id = request_id or str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        audit_record: dict[str, Any] = {
            "request_id": final_request_id,
            "created_at": created_at,
            "status": execution_result.get("status", "unknown"),
            "query": query,
            "query_hash": self._hash_query(query),
            "intent": self._summarize_intent(intent),
            "plan_summary": self._summarize_plan(plan),
            "outputs_summary": self._summarize_outputs(
                execution_result.get("outputs", {})
            ),
        }

        if self.config.include_trace:
            audit_record["trace"] = list(execution_result.get("trace", []))

        if self.config.include_router_decision and "router_decision" in execution_result:
            audit_record["router_decision"] = execution_result["router_decision"]

        if self.config.include_llm_gate_result and "llm_gate_result" in execution_result:
            audit_record["llm_gate_result"] = execution_result["llm_gate_result"]

        return audit_record

    @staticmethod
    def _hash_query(query: str) -> str:
        """
        Return stable SHA256 hash of query.
        """
        return hashlib.sha256(query.encode("utf-8")).hexdigest()

    @staticmethod
    def _summarize_intent(intent: Any) -> dict[str, Any]:
        """
        Convert intent to compact dict.
        """
        if hasattr(intent, "__dataclass_fields__"):
            payload = asdict(intent)
        elif isinstance(intent, dict):
            payload = dict(intent)
        else:
            payload = {
                "repr": repr(intent),
            }

        index_name = payload.get("index_name")

        if isinstance(index_name, str):
            index_name = index_name.upper()

        return {
            "intent_name": payload.get("intent_name"),
            "index_name": index_name,
            "threshold_operator": payload.get("threshold_operator"),
            "threshold_value": payload.get("threshold_value"),
            "vectorize": payload.get("vectorize"),
            "output_geometry": payload.get("output_geometry"),
        }

    def _summarize_plan(self, plan: Any) -> dict[str, Any]:
        """
        Summarize query plan without large payloads.
        """
        nodes = list(getattr(plan, "nodes", []) or [])
        intent = getattr(plan, "intent", None)

        node_rows: list[dict[str, Any]] = []

        for order, node in enumerate(nodes, start=1):
            row = {
                "order": order,
                "node_id": getattr(node, "id", None),
                "capability_name": getattr(node, "capability_name", None),
                "output_key": getattr(node, "output_key", None),
            }

            routing_evidence = getattr(node, "routing_evidence", None)

            if self.config.include_plan_routing_evidence and routing_evidence is not None:
                row["routing_evidence"] = {
                    "capability_name": routing_evidence.get("capability_name"),
                    "plugin_id": routing_evidence.get("plugin_id"),
                    "output_kind": routing_evidence.get("output_kind"),
                    "score": routing_evidence.get("score"),
                    "matched_terms": list(routing_evidence.get("matched_terms", [])),
                    "reasons": list(routing_evidence.get("reasons", [])),
                }

            node_rows.append(row)

        return {
            "intent_name": getattr(intent, "intent_name", None),
            "node_count": len(nodes),
            "capabilities": [
                row["capability_name"]
                for row in node_rows
            ],
            "nodes": node_rows,
        }

    def _summarize_outputs(self, outputs: dict[str, Any]) -> dict[str, Any]:
        """
        Summarize outputs without storing full payloads.
        """
        summaries: dict[str, Any] = {}

        for key, value in outputs.items():
            summaries[key] = self._summarize_output_value(value)

        return summaries

    def _summarize_output_value(self, value: Any) -> dict[str, Any]:
        """
        Summarize one output value.
        """
        if isinstance(value, dict) and value.get("type") == "FeatureCollection":
            features = list(value.get("features", []) or [])
            metadata = dict(value.get("metadata", {}) or {})

            geometry_types = sorted(
                {
                    feature.get("geometry", {}).get("type")
                    for feature in features
                    if isinstance(feature, dict)
                    and isinstance(feature.get("geometry"), dict)
                    and feature.get("geometry", {}).get("type")
                }
            )

            summary = {
                "kind": "vector",
                "format": "FeatureCollection",
                "feature_count": len(features),
                "geometry_types": geometry_types,
            }

            if self.config.include_output_metadata:
                summary["metadata"] = metadata

            return summary

        raster_data = _get_raster_data_or_none(value)

        if raster_data is not None:
            metadata = _get_metadata_or_empty(value)
            numeric_stats = _numeric_stats(raster_data)

            summary = {
                "kind": "raster",
                "shape": _shape_of_nested_list(raster_data),
                "numeric_stats": numeric_stats,
            }

            if self.config.include_output_metadata:
                summary["metadata"] = metadata

            return summary

        if isinstance(value, dict):
            return {
                "kind": "dict",
                "keys": sorted(value.keys()),
            }

        return {
            "kind": type(value).__name__,
            "repr": repr(value)[:300],
        }


def _get_raster_data_or_none(value: Any) -> Any | None:
    """
    Extract raster-like data from known output shapes.
    """
    if hasattr(value, "data"):
        return getattr(value, "data")

    if hasattr(value, "array"):
        return getattr(value, "array")

    if hasattr(value, "payload"):
        return getattr(value, "payload")

    if isinstance(value, dict):
        if "data" in value:
            return value["data"]
        if "array" in value:
            return value["array"]
        if "payload" in value:
            return value["payload"]

    return None


def _get_metadata_or_empty(value: Any) -> dict[str, Any]:
    """
    Extract metadata from output value.
    """
    if hasattr(value, "metadata"):
        metadata = getattr(value, "metadata")
        return dict(metadata or {})

    if isinstance(value, dict):
        return dict(value.get("metadata", {}) or {})

    return {}


def _shape_of_nested_list(value: Any) -> list[int]:
    """
    Return shape of a nested list-like value.

    Examples:
        [[1, 2], [3, 4]] -> [2, 2]
        [[[...]]]        -> [bands, rows, cols]
    """
    shape: list[int] = []
    current = value

    while isinstance(current, list):
        shape.append(len(current))
        if not current:
            break
        current = current[0]

    return shape


def _numeric_stats(value: Any) -> dict[str, Any]:
    """
    Compute simple numeric stats for nested lists.
    """
    numbers: list[float] = []

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return

        if isinstance(item, (int, float)) and not isinstance(item, bool):
            numbers.append(float(item))

    walk(value)

    if not numbers:
        return {
            "count": 0,
            "min": None,
            "max": None,
        }

    return {
        "count": len(numbers),
        "min": min(numbers),
        "max": max(numbers),
    }
