"""
orchestrator.capability_router

A simple capability router for the first natural-query runtime.

For now this router uses explicit bindings to real plugin functions.
Later it should be replaced with a registry-backed router that scores capabilities.
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
