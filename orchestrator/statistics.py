"""
orchestrator.statistics

In-memory statistics collector / self-learning seed.

This module consumes audit records and produces compact statistics that can
later feed:

    - router tuning
    - self-learning feedback loops
    - cost tracking
    - operational dashboards
    - plugin usage analytics
    - low-confidence query review
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StatisticsConfig:
    """
    Statistics collector configuration.
    """

    max_low_confidence_queries: int = 20
    max_ambiguous_queries: int = 20
    include_query_text: bool = False

    def __post_init__(self) -> None:
        if self.max_low_confidence_queries < 0:
            raise ValueError("max_low_confidence_queries must be >= 0.")

        if self.max_ambiguous_queries < 0:
            raise ValueError("max_ambiguous_queries must be >= 0.")


class ExecutionStatisticsCollector:
    """
    Collects aggregate statistics from execution audit records.

    The collector stores only compact counters and review lists, not full raster
    or vector payloads.
    """

    def __init__(
        self,
        config: StatisticsConfig | None = None,
    ) -> None:
        self.config = config or StatisticsConfig()
        self.reset()

    def reset(self) -> None:
        """
        Reset all collected statistics.
        """
        self.total_runs = 0

        self.status_counts: Counter[str] = Counter()
        self.confidence_level_counts: Counter[str] = Counter()
        self.llm_action_counts: Counter[str] = Counter()
        self.llm_gate_status_counts: Counter[str] = Counter()

        self.capability_usage: Counter[str] = Counter()
        self.plugin_usage: Counter[str] = Counter()

        self.capability_score_sum: defaultdict[str, float] = defaultdict(float)
        self.capability_score_count: Counter[str] = Counter()

        self.output_kind_counts: Counter[str] = Counter()
        self.output_key_counts: Counter[str] = Counter()

        self.ambiguity_count = 0
        self.llm_provider_called_count = 0
        self.llm_blocked_count = 0
        self.deterministic_fallback_count = 0

        self.vector_feature_total = 0

        self.low_confidence_queries: list[dict[str, Any]] = []
        self.ambiguous_queries: list[dict[str, Any]] = []

    def ingest(self, audit_record: dict[str, Any]) -> None:
        """
        Ingest one execution audit record.
        """
        if not isinstance(audit_record, dict):
            raise TypeError("audit_record must be a dict.")

        self.total_runs += 1

        status = audit_record.get("status", "unknown")
        self.status_counts[str(status)] += 1

        self._ingest_router_decision(audit_record)
        self._ingest_llm_gate_result(audit_record)
        self._ingest_plan_summary(audit_record)
        self._ingest_trace_fallback(audit_record)
        self._ingest_outputs_summary(audit_record)

    def ingest_many(self, audit_records: list[dict[str, Any]]) -> None:
        """
        Ingest multiple audit records.
        """
        for audit_record in audit_records:
            self.ingest(audit_record)

    def summarize(self) -> dict[str, Any]:
        """
        Return a JSON-like statistics snapshot.
        """
        return {
            "total_runs": self.total_runs,
            "status_counts": dict(self.status_counts),
            "router": {
                "confidence_level_counts": dict(self.confidence_level_counts),
                "llm_action_counts": dict(self.llm_action_counts),
                "ambiguity_count": self.ambiguity_count,
                "low_confidence_query_count": len(self.low_confidence_queries),
                "ambiguous_query_count": len(self.ambiguous_queries),
                "low_confidence_queries": list(self.low_confidence_queries),
                "ambiguous_queries": list(self.ambiguous_queries),
            },
            "llm_gate": {
                "status_counts": dict(self.llm_gate_status_counts),
                "provider_called_count": self.llm_provider_called_count,
                "blocked_count": self.llm_blocked_count,
                "deterministic_fallback_count": self.deterministic_fallback_count,
            },
            "capabilities": {
                "usage_counts": dict(self.capability_usage),
                "average_scores": self._average_scores(),
            },
            "plugins": {
                "usage_counts": dict(self.plugin_usage),
            },
            "outputs": {
                "kind_counts": dict(self.output_kind_counts),
                "output_key_counts": dict(self.output_key_counts),
                "vector_feature_total": self.vector_feature_total,
            },
        }

    def _ingest_router_decision(self, audit_record: dict[str, Any]) -> None:
        router_decision = audit_record.get("router_decision")

        if not isinstance(router_decision, dict):
            return

        level = router_decision.get("level")
        llm_action = router_decision.get("llm_action")

        if level:
            self.confidence_level_counts[str(level)] += 1

        if llm_action:
            self.llm_action_counts[str(llm_action)] += 1

        is_ambiguous = bool(router_decision.get("is_ambiguous"))

        if is_ambiguous:
            self.ambiguity_count += 1
            self._append_limited(
                self.ambiguous_queries,
                self._query_review_item(audit_record, router_decision),
                self.config.max_ambiguous_queries,
            )

        if level == "low":
            self._append_limited(
                self.low_confidence_queries,
                self._query_review_item(audit_record, router_decision),
                self.config.max_low_confidence_queries,
            )

    def _ingest_llm_gate_result(self, audit_record: dict[str, Any]) -> None:
        llm_gate_result = audit_record.get("llm_gate_result")

        if not isinstance(llm_gate_result, dict):
            return

        status = llm_gate_result.get("status")

        if status:
            self.llm_gate_status_counts[str(status)] += 1

        if bool(llm_gate_result.get("provider_called")):
            self.llm_provider_called_count += 1

        if bool(llm_gate_result.get("blocked")):
            self.llm_blocked_count += 1

        if bool(llm_gate_result.get("fallback_to_deterministic")):
            self.deterministic_fallback_count += 1

    def _ingest_plan_summary(self, audit_record: dict[str, Any]) -> None:
        plan_summary = audit_record.get("plan_summary")

        if not isinstance(plan_summary, dict):
            return

        nodes = plan_summary.get("nodes", [])

        if not isinstance(nodes, list):
            return

        for node in nodes:
            if not isinstance(node, dict):
                continue

            capability_name = node.get("capability_name")

            if capability_name:
                self.capability_usage[str(capability_name)] += 1

            routing_evidence = node.get("routing_evidence")

            if isinstance(routing_evidence, dict):
                plugin_id = routing_evidence.get("plugin_id")
                score = routing_evidence.get("score")

                if plugin_id:
                    self.plugin_usage[str(plugin_id)] += 1

                if capability_name and isinstance(score, (int, float)):
                    self.capability_score_sum[str(capability_name)] += float(score)
                    self.capability_score_count[str(capability_name)] += 1

    def _ingest_trace_fallback(self, audit_record: dict[str, Any]) -> None:
        """
        Fallback plugin/capability usage extraction from trace.

        If plan routing evidence was excluded from audit config, plugin_id can
        still be available in trace.

        To avoid double counting, this fallback only runs when plan_summary
        nodes are missing or empty.
        """
        plan_summary = audit_record.get("plan_summary")

        if isinstance(plan_summary, dict) and plan_summary.get("nodes"):
            return

        trace = audit_record.get("trace")

        if not isinstance(trace, list):
            return

        for item in trace:
            if not isinstance(item, dict):
                continue

            capability_name = item.get("capability_name")
            plugin_id = item.get("plugin_id")

            if capability_name:
                self.capability_usage[str(capability_name)] += 1

            if plugin_id:
                self.plugin_usage[str(plugin_id)] += 1

    def _ingest_outputs_summary(self, audit_record: dict[str, Any]) -> None:
        outputs_summary = audit_record.get("outputs_summary")

        if not isinstance(outputs_summary, dict):
            return

        for output_key, summary in outputs_summary.items():
            self.output_key_counts[str(output_key)] += 1

            if not isinstance(summary, dict):
                continue

            kind = summary.get("kind")

            if kind:
                self.output_kind_counts[str(kind)] += 1

            if kind == "vector":
                feature_count = summary.get("feature_count", 0)

                if isinstance(feature_count, int):
                    self.vector_feature_total += feature_count

    def _average_scores(self) -> dict[str, float]:
        averages: dict[str, float] = {}

        for capability_name, score_sum in self.capability_score_sum.items():
            count = self.capability_score_count[capability_name]

            if count:
                averages[capability_name] = round(score_sum / count, 4)

        return averages

    def _query_review_item(
        self,
        audit_record: dict[str, Any],
        router_decision: dict[str, Any],
    ) -> dict[str, Any]:
        item = {
            "request_id": audit_record.get("request_id"),
            "query_hash": audit_record.get("query_hash"),
            "level": router_decision.get("level"),
            "llm_action": router_decision.get("llm_action"),
            "top_score": router_decision.get("top_score"),
            "competitive_gap": router_decision.get("competitive_gap"),
            "is_ambiguous": router_decision.get("is_ambiguous"),
        }

        top_candidate = router_decision.get("top_candidate")

        if isinstance(top_candidate, dict):
            item["top_capability"] = top_candidate.get("capability_name")
            item["top_plugin_id"] = top_candidate.get("plugin_id")

        if self.config.include_query_text:
            item["query"] = audit_record.get("query")

        return item

    @staticmethod
    def _append_limited(
        items: list[dict[str, Any]],
        item: dict[str, Any],
        limit: int,
    ) -> None:
        if limit == 0:
            return

        items.append(item)

        if len(items) > limit:
            del items[0 : len(items) - limit]
