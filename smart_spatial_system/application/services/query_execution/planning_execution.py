from __future__ import annotations

from collections.abc import Callable
from typing import Any


def execute_query_spec_planning(
    *,
    query: str,
    planning_context: dict[str, Any],
    resolved_inputs: dict[str, Any],
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    final_metadata: dict[str, Any],
    build_runtime_inputs: Callable[..., tuple[dict[str, Any], bool]],
    enrich_query_database_params: Callable[[Any, dict[str, Any]], Any],
    build_enabled_registry_view: Callable[[], Any],
    kernel_execution_enabled: Callable[..., bool],
    llm_client_factory: Callable[[], Any],
    query_spec_generator_cls: Callable[[Any], Any],
    planning_runner_factory: Callable[[Any], Any],
    query_spec_contract_validator: Callable[[Any], Any],
) -> tuple[Any, Any, bool]:
    llm_client = llm_client_factory()
    generator = query_spec_generator_cls(llm_client)

    query_spec = generator.generate(
        query,
        context=planning_context,
    )

    planning_runtime_inputs, postgis_runtime_connection_injected = (
        build_runtime_inputs(
            resolved_inputs=resolved_inputs,
            user_context=user_context,
            metadata=metadata,
        )
    )

    if postgis_runtime_connection_injected:
        final_metadata["postgis_runtime_connection_injected"] = True

    enrich_query_database_params(
        query_spec,
        planning_runtime_inputs,
    )

    query_spec_contract_validator(query_spec)

    runner = planning_runner_factory(build_enabled_registry_view())

    kernel_enabled = kernel_execution_enabled(
        metadata=metadata,
        final_metadata=final_metadata,
    )
    final_metadata["kernel_execution_enabled"] = kernel_enabled

    if kernel_enabled:
        planning_result = runner.run_with_kernel_execution(
            query_spec,
            initial_inputs=resolved_inputs,
            fail_fast=True,
        )
    else:
        planning_result = runner.run(
            query_spec,
            initial_inputs=resolved_inputs,
            fail_fast=True,
        )

    return query_spec, planning_result, kernel_enabled
