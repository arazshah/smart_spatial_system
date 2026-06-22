from __future__ import annotations

from pathlib import Path

from geochat_kernel.models import PlanStep, QueryPlan

from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.kernel_execution_bridge import (
    execute_kernel_plan_with_capabilities_sync,
)
from orchestrator.planning.op_catalog import OP_CATALOG
from orchestrator.service import (
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
)


def test_query_spec_database_ops_resolve_through_registered_provider_capabilities(
    tmp_path: Path,
) -> None:
    """
    QuerySpec operations are logical and source/provider agnostic.

    This test does not require query_database itself to be a registered plugin
    capability. Instead, OP_CATALOG maps logical operations to concrete provider
    capabilities registered by plugins.
    """
    service = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
        )
    )

    resolver = RegistryCapabilityResolver(service.registry)

    for op_name in ("query_database", "load_postgis_layer"):
        descriptor = OP_CATALOG[op_name]

        assert descriptor.capability_name
        assert descriptor.capability_name != op_name or op_name == "load_postgis_layer"

        capability_fn = resolver(descriptor.capability_name)

        assert callable(capability_fn)


def test_kernel_execution_bridge_is_source_agnostic_for_arbitrary_capability() -> None:
    """
    The kernel execution bridge must not know about PostGIS, OSM, files,
    or any concrete datasource.

    It should execute any registered/resolved capability through the generic
    CapabilityStepHandler contract.
    """

    def read_any_source(source: dict, limit: int | None = None) -> dict:
        records = list(source.get("records") or [])
        if limit is not None:
            records = records[:limit]

        return {
            "source_type": source.get("type"),
            "records": records,
            "record_count": len(records),
        }

    plan = QueryPlan(
        id="plan_source_agnostic_test",
        query_ir_id="query_ir_source_agnostic_test",
        steps=[
            PlanStep(
                id="loaded",
                type="read_any_source",
                name="read_any_source",
                datasource_ids=[],
                dependencies=[],
                input_map={},
                parameters={
                    "limit": 2,
                },
                remote=False,
                timeout_s=None,
                max_retries=0,
                cacheable=True,
                cost_estimate=None,
                metadata={
                    "capability_name": "read_any_source",
                    "produces": "json",
                    "external_input_map": {
                        "source": "input_source",
                    },
                },
            )
        ],
        parallel_execution_allowed=False,
        cache_policy="default",
        planner_name="test.source_agnostic_planner",
        metadata={
            "output_nodes": ["loaded"],
            "raw_query": "load records from any source",
            "language": "en",
        },
    )

    result = execute_kernel_plan_with_capabilities_sync(
        plan,
        capability_resolver=lambda capability_name: {
            "read_any_source": read_any_source,
        }[capability_name],
        initial_inputs={
            "input_source": {
                "type": "memory",
                "records": [
                    {"id": 1},
                    {"id": 2},
                    {"id": 3},
                ],
            }
        },
    )

    assert result.success is True
    assert result.error is None
    assert list(result.output_nodes.keys()) == ["loaded"]

    output = result.output_nodes["loaded"]

    assert output["source_type"] == "memory"
    assert output["record_count"] == 2
    assert output["records"] == [
        {"id": 1},
        {"id": 2},
    ]
