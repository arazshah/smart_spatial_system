"""
orchestrator.response_builder

Builds user-facing response from pipeline outputs.

The response contains:
    - text answer
    - map spec
    - artifacts
    - metadata
    - trace
    - router decision when available
    - LLM gate result when available
    - audit record when available
"""

from __future__ import annotations

from typing import Any

from orchestrator.models import QueryIntent


class SimpleResponseBuilder:
    """
    Builds user-facing response from pipeline outputs.
    """

    def build(self, execution_result: dict[str, Any]) -> dict[str, Any]:
        outputs = execution_result["outputs"]
        trace = execution_result["trace"]
        intent: QueryIntent = execution_result["intent"]

        router_decision = execution_result.get("router_decision")
        llm_gate_result = execution_result.get("llm_gate_result")
        audit_record = execution_result.get("audit_record")

        vector = outputs["vegetation_polygons"]
        features = vector["features"]
        vector_metadata = vector["metadata"]

        feature_count = len(features)

        response = {
            "status": "success",
            "answer": (
                f"تحلیل انجام شد. شاخص NDVI محاسبه شد، سپس پیکسل‌های با مقدار "
                f"بیشتر از {intent.threshold_value} استخراج شدند و به {feature_count} "
                f"پلیگون تبدیل شدند."
            ),
            "map": {
                "layers": [
                    {
                        "id": "vegetation_polygons",
                        "title": "Vegetation polygons from NDVI threshold",
                        "type": "vector",
                        "source_kind": "inline_geojson",
                        "feature_count": feature_count,
                        "data": vector,
                        "style": {
                            "fillColor": "#22c55e",
                            "fillOpacity": 0.45,
                            "strokeColor": "#166534",
                            "strokeWidth": 1,
                        },
                    }
                ],
                "viewport": {
                    "strategy": "fit_layers",
                    "layer_ids": ["vegetation_polygons"],
                },
            },
            "artifacts": [
                {
                    "id": "ndvi_raster",
                    "kind": "raster",
                    "description": "Calculated NDVI raster.",
                },
                {
                    "id": "vegetation_mask",
                    "kind": "raster",
                    "description": "Binary vegetation mask from NDVI threshold.",
                },
                {
                    "id": "vegetation_polygons",
                    "kind": "vector",
                    "description": "Vector polygons generated from vegetation mask.",
                    "feature_count": feature_count,
                },
            ],
            "metadata": {
                "intent_name": intent.intent_name,
                "final_output": "vegetation_polygons",
                "feature_count": feature_count,
                "selected_pixel_count": vector_metadata["selected_pixel_count"],
            },
            "trace": trace,
        }

        if router_decision is not None:
            response["router_decision"] = router_decision
            response["metadata"]["router_decision"] = {
                "level": router_decision["level"],
                "llm_action": router_decision["llm_action"],
                "route_without_llm": router_decision["route_without_llm"],
                "is_ambiguous": router_decision["is_ambiguous"],
                "top_score": router_decision["top_score"],
                "competitive_gap": router_decision["competitive_gap"],
            }

        if llm_gate_result is not None:
            response["llm_gate_result"] = llm_gate_result
            response["metadata"]["llm_gate"] = {
                "llm_action": llm_gate_result["llm_action"],
                "status": llm_gate_result["status"],
                "provider_called": llm_gate_result["provider_called"],
                "blocked": llm_gate_result["blocked"],
                "fallback_to_deterministic": llm_gate_result["fallback_to_deterministic"],
            }

        if audit_record is not None:
            response["audit_record"] = audit_record
            response["metadata"]["audit"] = {
                "request_id": audit_record["request_id"],
                "created_at": audit_record["created_at"],
                "status": audit_record["status"],
                "query_hash": audit_record["query_hash"],
            }

        return response
