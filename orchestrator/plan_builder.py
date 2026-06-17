"""
orchestrator.plan_builder

Builds a simple query plan from parsed intent.

For now the plan is linear.
Later it can be replaced by a true DAG builder.
"""

from __future__ import annotations

from typing import Any

from orchestrator.models import PlanNode, QueryIntent, QueryPlan


class SimplePlanBuilder:
    """
    Builds the first real workflow plan:

        input raster
        -> NDVI
        -> threshold mask
        -> polygon features
    """

    def __init__(self, router: Any) -> None:
        self.router = router

    def build(
        self,
        intent: QueryIntent,
        *,
        band_map: dict[str, int],
    ) -> QueryPlan:
        if intent.intent_name != "extract_vegetation_polygons_from_ndvi_threshold":
            raise ValueError(f"Unsupported intent: {intent.intent_name}")

        # Validate that required capabilities exist.
        self.router.resolve("calculate_spectral_index")
        self.router.resolve("threshold_raster")
        self.router.resolve("raster_to_vector")

        nodes = [
            PlanNode(
                id="node_001_calculate_ndvi",
                capability_name="calculate_spectral_index",
                output_key="ndvi_raster",
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
        )
