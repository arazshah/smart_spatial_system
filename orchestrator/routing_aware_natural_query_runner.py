"""
orchestrator.routing_aware_natural_query_runner

High-level runner for explainable natural-query execution.

This runner uses:

    SimpleNaturalLanguageParser
    KeywordScoringCapabilityRouter
    RoutingAwarePlanBuilder
    RouterDecisionLayer
    LLMGate
    ExecutionAuditBuilder
    SimplePipelineExecutor
    SimpleResponseBuilder

The runner attaches:
    - router_decision
    - llm_gate_result
    - audit_record

to execution result and final response.
"""

from __future__ import annotations

from typing import Any

from orchestrator.audit import AuditConfig, ExecutionAuditBuilder
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter
from orchestrator.llm_gate import LLMGate, LLMGateBlockedError
from orchestrator.pipeline_executor import SimplePipelineExecutor
from orchestrator.query_parser import SimpleNaturalLanguageParser
from orchestrator.response_builder import SimpleResponseBuilder
from orchestrator.router_decision import RouterDecisionConfig, RouterDecisionLayer
from orchestrator.routing_aware_plan_builder import RoutingAwarePlanBuilder


def run_natural_query_with_routing_evidence(
    query: str,
    *,
    inputs: dict[str, Any],
    band_map: dict[str, int],
    router: Any | None = None,
    min_score: float = 0.2,
    decision_config: RouterDecisionConfig | None = None,
    llm_gate: LLMGate | None = None,
    enforce_llm_gate: bool = False,
    audit_config: AuditConfig | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute a natural-language query with routing evidence, router decision,
    LLM gate result, and audit record.

    This function does not require a real LLM provider.

    If enforce_llm_gate is False:
        blocked LLM gate results are recorded but execution continues.

    If enforce_llm_gate is True:
        blocked LLM gate results raise LLMGateBlockedError.
    """
    parser = SimpleNaturalLanguageParser(strict=False)
    final_router = router or KeywordScoringCapabilityRouter()

    planner = RoutingAwarePlanBuilder(
        final_router,
        min_score=min_score,
    )

    decision_layer = RouterDecisionLayer(decision_config)
    final_llm_gate = llm_gate or LLMGate()
    audit_builder = ExecutionAuditBuilder(audit_config)

    executor = SimplePipelineExecutor(final_router)
    response_builder = SimpleResponseBuilder()

    intent = parser.parse(query)

    plan = planner.build(
        intent,
        band_map=band_map,
    )

    router_decision = decision_layer.decide(plan.routing_evidence).to_dict()

    llm_gate_result = final_llm_gate.evaluate(
        router_decision,
        context={
            "query": query,
            "intent_name": intent.intent_name,
            "plan_node_count": len(plan.nodes),
            "candidate_count": len(plan.routing_evidence),
        },
    ).to_dict()

    if enforce_llm_gate and llm_gate_result["blocked"]:
        raise LLMGateBlockedError(
            f"LLM gate blocked execution: {llm_gate_result['status']}"
        )

    execution_result = executor.execute(
        plan,
        inputs=inputs,
    )

    execution_result["router_decision"] = router_decision
    execution_result["llm_gate_result"] = llm_gate_result

    audit_record = audit_builder.build(
        query=query,
        intent=intent,
        plan=plan,
        execution_result=execution_result,
        request_id=request_id,
    )

    execution_result["audit_record"] = audit_record

    response = response_builder.build(execution_result)

    return {
        "intent": intent,
        "plan": plan,
        "router_decision": router_decision,
        "llm_gate_result": llm_gate_result,
        "audit_record": audit_record,
        "execution": execution_result,
        "response": response,
    }
