"""
Signature-consistency check for OP_CATALOG.param_map.

Each OpDescriptor.param_map maps a logical/LLM-facing param name to a target
keyword argument on the bound capability function. _map_params() in
planner.py passes that target name straight through as **static_params, so if
it isn't a real keyword parameter of the capability, planning succeeds (the
catalog only validates against itself) but execution raises TypeError.

This regression-tests the filter_attribute/geometry_type(s) bug: param_map
mapped "geometry_type" -> "geometry_type", but filter_features's real
parameter is "geometry_types" (plural). strict_params=True didn't catch it
because "geometry_type" genuinely is a valid *source* key in the catalog -
only the target side was wrong.
"""

from __future__ import annotations

import inspect

import pytest

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.op_catalog import OP_CATALOG
from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES


@pytest.fixture(scope="module")
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_plugin_modules(DEFAULT_SAFE_PLUGIN_MODULES)


def _accepts_kwarg(func, kwarg: str) -> bool:
    params = inspect.signature(func).parameters
    if kwarg in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def test_every_param_map_target_is_a_real_capability_kwarg(registry: CapabilityRegistry) -> None:
    mismatches: list[str] = []

    for op_name, descriptor in OP_CATALOG.items():
        try:
            binding = registry.resolve(descriptor.capability_name)
        except ValueError:
            # Not every catalog capability is reachable via DEFAULT_SAFE_PLUGIN_MODULES
            # (see op_catalog.py's "Source-plugin reachability" note); nothing to check.
            continue

        for source_key, target_key in descriptor.param_map.items():
            if not _accepts_kwarg(binding.callable, target_key):
                mismatches.append(
                    f"op '{op_name}' -> capability '{descriptor.capability_name}': "
                    f"param_map[{source_key!r}] = {target_key!r}, but "
                    f"{descriptor.capability_name}() has no such keyword parameter "
                    f"(signature: {inspect.signature(binding.callable)})"
                )

    assert not mismatches, "OP_CATALOG param_map drift found:\n" + "\n".join(mismatches)
