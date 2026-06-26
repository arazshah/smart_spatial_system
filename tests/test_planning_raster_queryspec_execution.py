from typing import Any

from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.llm_spec_generator import (
    normalize_llm_query_spec_for_planning,
    query_spec_from_dict,
)
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig


def test_raster_queryspec_chain_executes_through_dag_executor_with_resolved_refs() -> None:
    data = {
        "raw_query": "extract vegetation polygons from NDVI",
        "goal": "Calculate NDVI, threshold vegetation, and polygonize the mask.",
        "entities": [
            {
                "ref": "sentinel",
                "kind": "raster",
                "binding": {},
                "hints": {},
            }
        ],
        "operations": [
            {
                "op": "ndvi",
                "inputs": {"raster": "sentinel"},
                "params": {
                    "red_band": 3,
                    "nir_band": 4,
                    "clip_output": True,
                },
                "output": "ndvi_raster",
            },
            {
                "op": "raster_threshold",
                "inputs": {"raster": "ndvi_raster"},
                "params": {
                    "operator": ">",
                    "threshold": 0.3,
                    "true_value": 1,
                    "false_value": 0,
                },
                "output": "vegetation_mask",
            },
            {
                "op": "raster_to_vector",
                "inputs": {"raster": "vegetation_mask"},
                "params": {
                    "include_values": [1],
                    "mode": "components",
                    "max_features": 1000,
                },
                "output": "vegetation_polygons",
            },
        ],
        "outputs": [
            {
                "kind": "vector",
                "source": "vegetation_polygons",
                "format": "geojson",
                "config": {},
            }
        ],
        "source": "llm",
        "metadata": {},
    }

    spec = query_spec_from_dict(data, raw_query_fallback=data["raw_query"])
    normalized = normalize_llm_query_spec_for_planning(spec)
    plan = DeterministicPlanner(
        PlannerConfig(strict_params=True)
    ).build(normalized)

    calls: list[tuple[str, dict[str, Any]]] = []

    def calculate_ndvi(
        raster: dict[str, Any],
        red_band: int,
        nir_band: int,
        clip_output: bool,
    ) -> dict[str, Any]:
        calls.append(
            (
                "calculate_ndvi",
                {
                    "raster": raster,
                    "red_band": red_band,
                    "nir_band": nir_band,
                    "clip_output": clip_output,
                },
            )
        )
        return {
            "kind": "raster",
            "name": "ndvi_raster",
            "source": raster["name"],
            "red_band": red_band,
            "nir_band": nir_band,
            "clip_output": clip_output,
            "values": [[0.1, 0.4], [0.6, 0.2]],
        }

    def threshold_raster(
        raster: dict[str, Any],
        operator: str,
        threshold: float,
        true_value: int,
        false_value: int,
    ) -> dict[str, Any]:
        calls.append(
            (
                "threshold_raster",
                {
                    "raster": raster,
                    "operator": operator,
                    "threshold": threshold,
                    "true_value": true_value,
                    "false_value": false_value,
                },
            )
        )
        return {
            "kind": "raster",
            "name": "vegetation_mask",
            "source": raster["name"],
            "operator": operator,
            "threshold": threshold,
            "values": [[false_value, true_value], [true_value, false_value]],
        }

    def raster_to_vector(
        raster: dict[str, Any],
        include_values: list[int],
        mode: str,
        max_features: int,
    ) -> dict[str, Any]:
        calls.append(
            (
                "raster_to_vector",
                {
                    "raster": raster,
                    "include_values": include_values,
                    "mode": mode,
                    "max_features": max_features,
                },
            )
        )
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {
                        "source_raster": raster["name"],
                        "value": include_values[0],
                    },
                }
            ],
            "metadata": {
                "mode": mode,
                "max_features": max_features,
                "feature_count": 1,
            },
        }

    capabilities = {
        "calculate_ndvi": calculate_ndvi,
        "threshold_raster": threshold_raster,
        "raster_to_vector": raster_to_vector,
    }

    executor = DagExecutor(
        capability_resolver=lambda capability_name: capabilities[capability_name]
    )

    result = executor.execute(
        plan,
        initial_inputs={
            "sentinel": {
                "kind": "raster",
                "name": "sentinel_scene",
                "bands": {
                    "red": 3,
                    "nir": 4,
                },
            }
        },
    )

    assert result.success is True
    assert result.error is None
    assert [trace.status for trace in result.trace] == ["success", "success", "success"]
    assert [trace.capability_name for trace in result.trace] == [
        "calculate_ndvi",
        "threshold_raster",
        "raster_to_vector",
    ]

    assert list(result.outputs) == [
        "ndvi_raster",
        "vegetation_mask",
        "vegetation_polygons",
    ]
    assert list(result.output_nodes) == ["vegetation_polygons"]

    final_output = result.output_nodes["vegetation_polygons"]
    assert final_output["type"] == "FeatureCollection"
    assert final_output["metadata"]["feature_count"] == 1
    assert final_output["features"][0]["properties"]["source_raster"] == "vegetation_mask"

    assert [name for name, _kwargs in calls] == [
        "calculate_ndvi",
        "threshold_raster",
        "raster_to_vector",
    ]

    ndvi_call = calls[0][1]
    assert ndvi_call["raster"]["name"] == "sentinel_scene"
    assert ndvi_call["red_band"] == 3
    assert ndvi_call["nir_band"] == 4
    assert ndvi_call["clip_output"] is True

    threshold_call = calls[1][1]
    assert threshold_call["raster"]["name"] == "ndvi_raster"
    assert threshold_call["operator"] == ">"
    assert threshold_call["threshold"] == 0.3

    vectorize_call = calls[2][1]
    assert vectorize_call["raster"]["name"] == "vegetation_mask"
    assert vectorize_call["include_values"] == [1]
    assert vectorize_call["mode"] == "components"
    assert vectorize_call["max_features"] == 1000
