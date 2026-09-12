"""
orchestrator.capability_router

A simple capability router for the first natural-query runtime.

For now this router uses explicit bindings to real plugin functions.
Later it should be replaced with a registry-backed router that scores capabilities.

DEPRECATED (REFACTOR_PLAN.md Phase 7, docs/PHASE7_LEGACY_CLEANUP_PLAN.md):
as of this investigation, `SimpleCapabilityRouter` has no confirmed
production caller. Its only user is `run_natural_query` (the plain, not
`_with_routing_evidence`, variant) in `orchestrator/natural_query_runner.py`,
and a repo-wide search found no caller of `run_natural_query` anywhere in
`smart_spatial_system/application/services/...` - i.e. nowhere in the
actual request-handling code that builds `/query` responses. Its only
production-code references are `orchestrator/__init__.py`'s package-level
re-export and its own test suite
(`tests/test_orchestrator_natural_query_pipeline.py` and
`tests/test_orchestrator_registry_router.py`, the latter exercising
`run_natural_query` with `RegistryBackedCapabilityRouter` substituted for
this router). "No caller found by
grep" is not the same certainty as "confirmed dead", so this is marked
deprecated rather than removed - re-run the same search at removal time,
since new code could start calling it between now and then.
"""

from __future__ import annotations

from typing import Any


def _default_capability_handlers() -> dict[str, Any]:
    """
    Build default handlers through the centralized capability registry.

    This avoids importing concrete plugin modules directly from core router code.
    """
    from orchestrator.capability_registry import CapabilityRegistry
    from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES

    registry = CapabilityRegistry.from_plugin_modules(DEFAULT_SAFE_PLUGIN_MODULES)

    names = (
        "calculate_spectral_index",
        "threshold_raster",
        "raster_to_vector",
    )

    handlers: dict[str, Any] = {}
    for name in names:
        try:
            handlers[name] = registry.resolve(name).callable
        except Exception:
            continue

    return handlers


from orchestrator.models import CapabilityBinding


class SimpleCapabilityRouter:
    """
    Minimal capability router.

    Maps abstract operation names to real plugin functions.

    DEPRECATED: see this module's docstring
    (docs/PHASE7_LEGACY_CLEANUP_PLAN.md) - no confirmed production caller,
    candidate for removal once that's re-confirmed at removal time.
    """

    def __init__(self) -> None:
        handlers = _default_capability_handlers()
        self._bindings: dict[str, CapabilityBinding] = {
            "calculate_spectral_index": CapabilityBinding(
                name="calculate_spectral_index",
                plugin_id="spectral_indices",
                callable=handlers["calculate_spectral_index"],
                output_kind="raster",
                keywords=[
                    "ndvi",
                    "spectral index",
                    "شاخص طیفی",
                    "شاخص پوشش گیاهی",
                ],
            ),
            "threshold_raster": CapabilityBinding(
                name="threshold_raster",
                plugin_id="raster_threshold",
                callable=handlers["threshold_raster"],
                output_kind="raster",
                keywords=[
                    "threshold",
                    "raster mask",
                    "binary mask",
                    "آستانه",
                    "ماسک",
                ],
            ),
            "raster_to_vector": CapabilityBinding(
                name="raster_to_vector",
                plugin_id="raster_to_vector",
                callable=handlers["raster_to_vector"],
                output_kind="vector",
                keywords=[
                    "polygon",
                    "vectorize",
                    "raster to vector",
                    "پلیگون",
                    "وکتور",
                ],
            ),
        }

    def resolve(self, capability_name: str) -> CapabilityBinding:
        if capability_name not in self._bindings:
            raise ValueError(f"Capability '{capability_name}' is not registered in router.")
        return self._bindings[capability_name]

    def registered_capability_names(self) -> list[str]:
        return sorted(self._bindings.keys())
