import pytest

from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig, PlanningError
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec


def test_planner_maps_ndvi_threshold_vectorize_chain_from_op_catalog() -> None:
    spec = QuerySpec(
        raw_query="extract vegetation polygons from NDVI",
        goal="Calculate NDVI, threshold vegetation, and polygonize the mask.",
        entities=[
            EntitySpec(ref="sentinel", kind="raster"),
        ],
        operations=[
            OperationSpec(
                op="ndvi",
                inputs={"raster": "sentinel"},
                params={
                    "red_band": 3,
                    "nir_band": 4,
                    "clip_output": True,
                },
                output="ndvi_raster",
            ),
            OperationSpec(
                op="raster_threshold",
                inputs={"raster": "ndvi_raster"},
                params={
                    "operator": ">",
                    "threshold": 0.3,
                    "true_value": 1,
                    "false_value": 0,
                },
                output="vegetation_mask",
            ),
            OperationSpec(
                op="raster_to_vector",
                inputs={"raster": "vegetation_mask"},
                params={
                    "include_values": [1],
                    "mode": "components",
                    "max_features": 1000,
                },
                output="vegetation_polygons",
            ),
        ],
        outputs=[
            OutputSpec(kind="vector", source="vegetation_polygons", format="geojson"),
        ],
    )

    plan = DeterministicPlanner(
        PlannerConfig(strict_params=True)
    ).build(spec)

    assert plan.output_nodes == ["vegetation_polygons"]
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

    ndvi_node = plan.nodes[0]
    assert ndvi_node.inputs == {"raster": "$inputs.sentinel"}
    assert ndvi_node.static_params == {
        "red_band": 3,
        "nir_band": 4,
        "clip_output": True,
    }
    assert ndvi_node.needs == []
    assert ndvi_node.metadata["logical_op"] == "ndvi"
    assert ndvi_node.metadata["logical_output"] == "ndvi_raster"

    threshold_node = plan.nodes[1]
    assert threshold_node.inputs == {"raster": "$node.ndvi_raster"}
    assert threshold_node.static_params == {
        "operator": ">",
        "threshold": 0.3,
        "true_value": 1,
        "false_value": 0,
    }
    assert threshold_node.needs == ["ndvi_raster"]
    assert threshold_node.metadata["logical_op"] == "raster_threshold"

    vector_node = plan.nodes[2]
    assert vector_node.inputs == {"raster": "$node.vegetation_mask"}
    assert vector_node.static_params == {
        "include_values": [1],
        "mode": "components",
        "max_features": 1000,
    }
    assert vector_node.needs == ["vegetation_mask"]
    assert vector_node.metadata["logical_op"] == "raster_to_vector"


@pytest.mark.parametrize(
    ("op_name", "inputs", "params", "expected_capability", "expected_output_type"),
    [
        (
            "calculate_ndvi",
            {"raster": "src"},
            {"red_band": 3, "nir_band": 4},
            "calculate_ndvi",
            "raster",
        ),
        (
            "spectral_index",
            {"raster": "src"},
            {"index_name": "ndwi", "band_map": {"green": 2, "nir": 4}},
            "calculate_spectral_index",
            "raster",
        ),
        (
            "band_math",
            {"raster": "src"},
            {"expression": "(b4 - b3) / (b4 + b3)"},
            "calculate_band_math",
            "raster",
        ),
        (
            "raster_reclassify",
            {"raster": "src"},
            {"rules": [{"min": 0.0, "max": 0.3, "value": 1}]},
            "reclassify_raster",
            "raster",
        ),
        (
            "raster_clip",
            {"raster": "src"},
            {"bbox": [0, 0, 1, 1], "crop": True},
            "clip_mask_raster",
            "raster",
        ),
        (
            "slope_aspect",
            {"raster": "src"},
            {"output": "both", "slope_unit": "degree"},
            "calculate_slope_aspect",
            "json",
        ),
        (
            "zonal_statistics",
            {"raster": "src", "zones": "zones"},
            {"stats": ["mean", "min", "max"]},
            "calculate_zonal_statistics",
            "vector",
        ),
        (
            "ndvi_from_bands",
            {"red_band": "red", "nir_band": "nir"},
            {},
            "ndvi_processor",
            "raster",
        ),
    ],
)
def test_planner_maps_core_raster_ops_from_op_catalog(
    op_name: str,
    inputs: dict[str, str],
    params: dict[str, object],
    expected_capability: str,
    expected_output_type: str,
) -> None:
    entity_refs = sorted(set(inputs.values()))

    spec = QuerySpec(
        raw_query=f"test {op_name}",
        goal=f"Plan {op_name}",
        entities=[
            EntitySpec(ref=ref, kind="raster" if ref != "zones" else "vector")
            for ref in entity_refs
        ],
        operations=[
            OperationSpec(
                op=op_name,
                inputs=inputs,
                params=params,
                output="result",
            ),
        ],
        outputs=[
            OutputSpec(kind=expected_output_type, source="result"),
        ],
    )

    plan = DeterministicPlanner(
        PlannerConfig(strict_params=True)
    ).build(spec)

    assert len(plan.nodes) == 1
    node = plan.nodes[0]

    assert node.id == "result"
    assert node.capability_name == expected_capability
    assert node.produces == expected_output_type
    assert node.needs == []
    assert node.metadata["logical_op"] == op_name
    assert node.metadata["logical_output"] == "result"
    assert plan.output_nodes == ["result"]

    for logical_role, source_ref in inputs.items():
        capability_param = node.inputs.get(logical_role)
        assert capability_param == f"$inputs.{source_ref}"


def test_planner_rejects_missing_required_raster_input_role() -> None:
    spec = QuerySpec(
        raw_query="bad ndvi",
        goal="Missing raster input should fail.",
        operations=[
            OperationSpec(
                op="ndvi",
                inputs={},
                params={"red_band": 3, "nir_band": 4},
                output="ndvi_raster",
            ),
        ],
    )

    with pytest.raises(PlanningError, match="missing input role"):
        DeterministicPlanner().build(spec)


def test_planner_rejects_unknown_raster_param_in_strict_mode() -> None:
    spec = QuerySpec(
        raw_query="bad ndvi param",
        goal="Unknown strict param should fail.",
        entities=[
            EntitySpec(ref="sentinel", kind="raster"),
        ],
        operations=[
            OperationSpec(
                op="ndvi",
                inputs={"raster": "sentinel"},
                params={"unknown_raster_param": True},
                output="ndvi_raster",
            ),
        ],
    )

    with pytest.raises(PlanningError, match="unsupported parameter"):
        DeterministicPlanner(
            PlannerConfig(strict_params=True)
        ).build(spec)
