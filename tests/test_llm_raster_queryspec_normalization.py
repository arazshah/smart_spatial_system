from orchestrator.planning.llm_query_spec import (
    QuerySpecPromptBuilder,
    query_spec_from_dict as strict_query_spec_from_dict,
)
from orchestrator.planning.llm_spec_generator import (
    normalize_llm_query_spec_for_planning,
    query_spec_from_dict as generator_query_spec_from_dict,
)
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig


def test_llm_spec_generator_preserves_raster_chain_through_normalization_and_planning() -> None:
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

    spec = generator_query_spec_from_dict(data, raw_query_fallback=data["raw_query"])
    normalized = normalize_llm_query_spec_for_planning(spec)

    assert [op.op for op in normalized.operations] == [
        "ndvi",
        "raster_threshold",
        "raster_to_vector",
    ]
    assert normalized.operations[0].inputs == {"raster": "sentinel"}
    assert normalized.operations[1].inputs == {"raster": "ndvi_raster"}
    assert normalized.operations[2].inputs == {"raster": "vegetation_mask"}

    assert normalized.operations[0].params == {
        "red_band": 3,
        "nir_band": 4,
        "clip_output": True,
    }
    assert normalized.operations[1].params == {
        "operator": ">",
        "threshold": 0.3,
        "true_value": 1,
        "false_value": 0,
    }
    assert normalized.operations[2].params == {
        "include_values": [1],
        "mode": "components",
        "max_features": 1000,
    }

    plan = DeterministicPlanner(
        PlannerConfig(strict_params=True)
    ).build(normalized)

    assert [node.id for node in plan.nodes] == [
        "ndvi_raster",
        "vegetation_mask",
        "vegetation_polygons",
    ]
    assert [node.capability_name for node in plan.nodes] == [
        "calculate_ndvi",
        "threshold_raster",
        "raster_to_vector",
    ]
    assert [node.produces for node in plan.nodes] == [
        "raster",
        "raster",
        "vector",
    ]

    assert plan.nodes[0].inputs == {"raster": "$inputs.sentinel"}
    assert plan.nodes[0].static_params == {
        "red_band": 3,
        "nir_band": 4,
        "clip_output": True,
    }

    assert plan.nodes[1].inputs == {"raster": "$node.ndvi_raster"}
    assert plan.nodes[1].static_params == {
        "operator": ">",
        "threshold": 0.3,
        "true_value": 1,
        "false_value": 0,
    }

    assert plan.nodes[2].inputs == {"raster": "$node.vegetation_mask"}
    assert plan.nodes[2].static_params == {
        "include_values": [1],
        "mode": "components",
        "max_features": 1000,
    }

    assert plan.output_nodes == ["vegetation_polygons"]


def test_llm_spec_generator_removes_unsupported_raster_input_roles_but_keeps_supported_roles() -> None:
    data = {
        "raw_query": "calculate ndvi",
        "goal": "Calculate NDVI.",
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
                "inputs": {
                    "raster": "sentinel",
                    "source": "sentinel",
                    "features": "wrong_alias",
                },
                "params": {
                    "red_band": 3,
                    "nir_band": 4,
                },
                "output": "ndvi_raster",
            }
        ],
        "outputs": [
            {
                "kind": "raster",
                "source": "ndvi_raster",
                "format": "",
                "config": {},
            }
        ],
        "metadata": {},
    }

    spec = generator_query_spec_from_dict(data, raw_query_fallback=data["raw_query"])
    normalized = normalize_llm_query_spec_for_planning(spec)

    assert len(normalized.operations) == 1
    op = normalized.operations[0]

    assert op.op == "ndvi"
    assert op.inputs == {"raster": "sentinel"}
    assert op.params == {
        "red_band": 3,
        "nir_band": 4,
    }

    normalization = normalized.metadata.get("normalization")
    assert isinstance(normalization, dict)
    assert normalization["applied"] is True
    assert any("removed unsupported input role 'source'" in item for item in normalization["repairs"])
    assert any("removed unsupported input role 'features'" in item for item in normalization["repairs"])


def test_llm_query_spec_parser_accepts_supported_raster_operations() -> None:
    payload = {
        "raw_query": "extract vegetation polygons from NDVI",
        "goal": "vegetation_polygon_extraction",
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
                "params": {"red_band": 3, "nir_band": 4},
                "output": "ndvi_raster",
            },
            {
                "op": "raster_threshold",
                "inputs": {"raster": "ndvi_raster"},
                "params": {"operator": ">", "threshold": 0.3},
                "output": "vegetation_mask",
            },
            {
                "op": "raster_to_vector",
                "inputs": {"raster": "vegetation_mask"},
                "params": {"include_values": [1], "max_features": 1000},
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

    spec = strict_query_spec_from_dict(payload, validate_supported_ops=True)

    assert [op.op for op in spec.operations] == [
        "ndvi",
        "raster_threshold",
        "raster_to_vector",
    ]
    assert spec.operations[0].inputs == {"raster": "sentinel"}
    assert spec.operations[1].inputs == {"raster": "ndvi_raster"}
    assert spec.operations[2].inputs == {"raster": "vegetation_mask"}
    assert spec.operations[2].params["max_features"] == 1000


def test_query_spec_prompt_builder_exposes_core_raster_operations() -> None:
    prompt = QuerySpecPromptBuilder().build(
        "Calculate NDVI and extract vegetation polygons.",
        context={"available_layers": [{"ref": "sentinel", "kind": "raster"}]},
    )

    expected_ops = [
        "ndvi",
        "calculate_ndvi",
        "ndvi_from_bands",
        "spectral_index",
        "band_math",
        "raster_threshold",
        "raster_to_vector",
        "raster_reclassify",
        "raster_clip",
        "slope_aspect",
        "zonal_statistics",
        "raster_stats",
    ]

    for op_name in expected_ops:
        assert op_name in prompt
