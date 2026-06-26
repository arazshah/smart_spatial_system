from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.llm_spec_generator import (
    normalize_llm_query_spec_for_planning,
    query_spec_from_dict,
)
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig


def test_registry_backed_raster_queryspec_chain_executes_with_real_plugins() -> None:
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
                    "red_band": 1,
                    "nir_band": 2,
                    "clip_output": True,
                    "precision": 6,
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
                    "mode": "cells",
                    "max_features": 10,
                    "precision": 6,
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
        "metadata": {},
    }

    raster = {
        "data": [
            [
                [0.2, 0.4],
                [0.6, 0.1],
            ],
            [
                [0.8, 0.3],
                [0.9, 0.1],
            ],
        ],
        "metadata": {
            "width": 2,
            "height": 2,
            "band_count": 2,
            "crs": "EPSG:4326",
            "transform": [0, 1, 0, 2, 0, -1],
            "nodata": None,
        },
    }

    spec = query_spec_from_dict(data, raw_query_fallback=data["raw_query"])
    normalized = normalize_llm_query_spec_for_planning(spec)
    plan = DeterministicPlanner(
        PlannerConfig(strict_params=True)
    ).build(normalized)

    registry = CapabilityRegistry.from_plugin_modules(tolerant=False)
    resolver = RegistryCapabilityResolver(registry)

    result = DagExecutor(resolver).execute(
        plan,
        initial_inputs={"sentinel": raster},
    )

    assert result.success is True
    assert result.error is None

    assert [trace.status for trace in result.trace] == [
        "success",
        "success",
        "success",
    ]
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

    assert isinstance(final_output, dict)
    assert final_output["type"] == "FeatureCollection"
    assert isinstance(final_output.get("features"), list)
    assert len(final_output["features"]) >= 1

    first_feature = final_output["features"][0]
    assert first_feature["type"] == "Feature"
    assert "geometry" in first_feature
    assert "properties" in first_feature
