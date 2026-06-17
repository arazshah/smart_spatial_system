"""
orchestrator.pipeline_executor

Executes a simple linear query plan by calling real plugin functions.
"""

from __future__ import annotations

from typing import Any

from orchestrator.models import QueryPlan


class SimplePipelineExecutor:
    """
    Executes a simple linear query plan.

    It calls real plugin functions through the capability router.
    """

    def __init__(self, router: Any) -> None:
        self.router = router

    def execute(
        self,
        plan: QueryPlan,
        *,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []

        for order, node in enumerate(plan.nodes, start=1):
            binding = self.router.resolve(node.capability_name)

            resolved_params = {
                key: self._resolve_reference(value, inputs=inputs, outputs=outputs)
                for key, value in node.params.items()
            }

            result = binding.callable(**resolved_params)
            outputs[node.output_key] = result

            trace_item = {
                "order": order,
                "node_id": node.id,
                "capability_name": node.capability_name,
                "plugin_id": binding.plugin_id,
                "output_key": node.output_key,
                "output_kind": binding.output_kind,
                "status": "success",
            }

            if node.routing_evidence is not None:
                trace_item["routing_evidence"] = node.routing_evidence

            trace.append(trace_item)

        return {
            "status": "success",
            "intent": plan.intent,
            "plan": plan,
            "outputs": outputs,
            "trace": trace,
        }

    @staticmethod
    def _resolve_reference(
        value: Any,
        *,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> Any:
        if isinstance(value, str):
            if value.startswith("$inputs."):
                key = value.replace("$inputs.", "", 1)
                return inputs[key]

            if value.startswith("$outputs."):
                key = value.replace("$outputs.", "", 1)
                return outputs[key]

        return value
