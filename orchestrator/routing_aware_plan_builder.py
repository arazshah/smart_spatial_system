"""
orchestrator.routing_aware_plan_builder

Builds a plan using routing evidence from KeywordScoringCapabilityRouter.

This is the first explainable planning layer:

    query
    -> parser intent
    -> keyword scoring router
    -> selected capabilities with scores/reasons
    -> plan nodes with routing evidence
    -> executor trace with routing evidence
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from orchestrator.models import PlanNode, QueryIntent, QueryPlan, ScoredCapability


class RoutingAwarePlanBuilder:
    """
    Builds the first routing-aware workflow plan:

        input raster
        -> NDVI
        -> threshold mask
        -> polygon features

    Unlike SimplePlanBuilder, this builder asks the router to score the query
    and attaches routing evidence to every selected plan node.
    """

    REQUIRED_CAPABILITIES = [
        "calculate_spectral_index",
        "threshold_raster",
        "raster_to_vector",
    ]

    def __init__(
        self,
        router: Any,
        *,
        min_score: float = 0.2,
    ) -> None:
        self.router = router
        self.min_score = min_score

    def build(
        self,
        intent: QueryIntent,
        *,
        band_map: dict[str, int],
    ) -> QueryPlan:
        if intent.intent_name != "extract_vegetation_polygons_from_ndvi_threshold":
            raise ValueError(f"Unsupported intent: {intent.intent_name}")

        # 1. Ask router to score the raw query.
        candidates = self.router.select_relevant(
            intent.raw_query,
            min_score=self.min_score,
        )

        by_name = {
            candidate.capability_name: candidate
            for candidate in candidates
        }

        missing = [
            capability_name
            for capability_name in self.REQUIRED_CAPABILITIES
            if capability_name not in by_name
        ]

        if missing:
            raise ValueError(
                "Routing-aware planner could not find required capabilities: "
                + ", ".join(missing)
            )

        # 2. Validate exact resolution still works.
        for capability_name in self.REQUIRED_CAPABILITIES:
            self.router.resolve(capability_name)

        # 3. Build linear plan with evidence per node.
        nodes = [
            PlanNode(
                id="node_001_calculate_ndvi",
                capability_name="calculate_spectral_index",
                output_key="ndvi_raster",
                routing_evidence=self._evidence_dict(by_name["calculate_spectral_index"]),
                params={
                    "raster": "$inputs.raster",
                    "index_name": intent.index_name,
                    "band_map": band_map,
                    "nodata": -9999,
                    "output_nodata": -9999,
                    "precision": 3,
                    "metadata": {
                        "pipeline_node": "node_001_calculate_ndvi",
                    },
                },
            ),
            PlanNode(
                id="node_002_threshold_ndvi",
                capability_name="threshold_raster",
                output_key="vegetation_mask",
                routing_evidence=self._evidence_dict(by_name["threshold_raster"]),
                params={
                    "raster": "$outputs.ndvi_raster",
                    "operator": intent.threshold_operator,
                    "threshold": intent.threshold_value,
                    "true_value": 1,
                    "false_value": 0,
                    "output_nodata": -9999,
                    "precision": 3,
                    "metadata": {
                        "pipeline_node": "node_002_threshold_ndvi",
                    },
                },
            ),
            PlanNode(
                id="node_003_polygonize_mask",
                capability_name="raster_to_vector",
                output_key="vegetation_polygons",
                routing_evidence=self._evidence_dict(by_name["raster_to_vector"]),
                params={
                    "raster": "$outputs.vegetation_mask",
                    "include_values": [1],
                    "mode": "cells",
                    "precision": 3,
                    "metadata": {
                        "pipeline_node": "node_003_polygonize_mask",
                    },
                },
            ),
        ]

        return QueryPlan(
            intent=intent,
            nodes=nodes,
            routing_evidence=candidates,
        )

    @staticmethod
    def _evidence_dict(candidate):
        """
        Convert routing evidence/candidate to a JSON-like dict.

        Supports:
            - dict / WeightedEvidence
            - dataclass objects such as ScoredCapability
            - objects exposing to_dict()
            - objects with __dict__
            - mapping-like objects exposing keys()
        """
        if isinstance(candidate, dict):
            return dict(candidate)

        if hasattr(candidate, "to_dict") and callable(candidate.to_dict):
            return candidate.to_dict()

        if is_dataclass(candidate):
            return asdict(candidate)

        if hasattr(candidate, "keys"):
            try:
                return {
                    key: candidate[key]
                    for key in candidate.keys()
                }
            except Exception:
                pass

        payload = dict(getattr(candidate, "__dict__", {}) or {})

        for key in (
            "score",
            "capability_name",
            "name",
            "plugin_id",
            "plugin_name",
            "plugin",
            "output_kind",
            "matched_terms",
            "reasons",
            "base_score",
            "weighted_score",
            "capability_weight",
            "plugin_weight",
            "score_weighted",
            "weighted_score_metadata",
        ):
            if key not in payload:
                try:
                    payload[key] = getattr(candidate, key)
                except Exception:
                    pass

        return payload
