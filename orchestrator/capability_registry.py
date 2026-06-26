"""
orchestrator.capability_registry

Registry-backed capability discovery for plugins.

This module discovers capabilities from plugin modules by reading:

    PLUGIN._capabilities_regs

and converting each capability registration into a runtime CapabilityBinding.

This is the first step toward a real router:

    plugin modules
        -> capability descriptors
        -> capability registry
        -> registry-backed router
        -> plan builder/executor

Important:
    We do not auto-scan all plugins yet because some plugins may require optional
    dependencies, environment variables, or external services.

    For now, modules are explicitly provided.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

from orchestrator.models import CapabilityBinding
from orchestrator.plugin_error_mapping import plugin_exception_to_structured_error
from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES


class CapabilityRegistry:
    """
    Runtime capability registry built from plugin modules.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, CapabilityBinding] = {}
        self._descriptors: dict[str, Any] = {}
        self._plugin_ids: set[str] = set()
        self.skipped_plugins: list[dict[str, Any]] = []

    @classmethod
    def from_plugin_modules(
        cls,
        plugin_module_names: list[str] | None = None,
        *,
        tolerant: bool = False,
    ) -> "CapabilityRegistry":
        """
        Build registry from plugin module names.

        Args:
            plugin_module_names:
                List like:
                    ["plugins.spectral_indices", "plugins.raster_threshold"]

                If None, DEFAULT_SAFE_PLUGIN_MODULES is used.
            tolerant:
                If True, plugins that fail to import/register are skipped
                instead of raising. Skipped plugins are recorded in
                registry.skipped_plugins.
        """
        registry = cls()
        registry.skipped_plugins = []

        module_names = plugin_module_names or DEFAULT_SAFE_PLUGIN_MODULES

        for module_name in module_names:
            try:
                module = importlib.import_module(module_name)
                registry.register_plugin_module(module)
            except Exception as exc:
                if not tolerant:
                    raise
                registry.skipped_plugins.append(
                    {
                        "module": module_name,
                        "error": f"{type(exc).__name__}: {exc}",
                        "structured_error": plugin_exception_to_structured_error(
                            exc,
                            module_name=module_name,
                            stage="plugin_import_or_registration",
                            source="capability_registry",
                        ),
                    }
                )

        return registry

    def register_plugin_module(self, module: ModuleType) -> None:
        """
        Register all capabilities from a plugin module.
        """
        plugin = getattr(module, "PLUGIN", None)

        if plugin is None:
            raise ValueError(f"Module '{module.__name__}' does not define PLUGIN.")

        manifest = getattr(plugin, "manifest", None)

        if manifest is None:
            raise ValueError(f"PLUGIN in module '{module.__name__}' has no manifest.")

        plugin_id = getattr(manifest, "id", None)

        if not plugin_id:
            raise ValueError(f"PLUGIN manifest in module '{module.__name__}' has no id.")

        regs = getattr(plugin, "_capabilities_regs", [])

        if not regs:
            raise ValueError(f"PLUGIN '{plugin_id}' has no registered capabilities.")

        self._plugin_ids.add(plugin_id)

        for reg in regs:
            capability_name = getattr(reg, "name", None)

            if not capability_name:
                raise ValueError(f"Invalid capability registration in plugin '{plugin_id}'.")

            if capability_name in self._bindings:
                existing = self._bindings[capability_name]
                raise ValueError(
                    f"Duplicate capability '{capability_name}' found in plugin '{plugin_id}'. "
                    f"Already registered by plugin '{existing.plugin_id}'."
                )

            descriptor = reg.build_descriptor(plugin_id=plugin_id)

            func = getattr(module, capability_name, None)

            if func is None or not callable(func):
                raise ValueError(
                    f"Capability function '{capability_name}' was registered by plugin "
                    f"'{plugin_id}' but no callable with the same name exists in module "
                    f"'{module.__name__}'."
                )

            output_kind = getattr(descriptor, "output_kind", "json")
            keywords = list(getattr(descriptor, "keywords", []) or [])

            binding = CapabilityBinding(
                name=capability_name,
                plugin_id=plugin_id,
                callable=func,
                output_kind=output_kind,
                keywords=keywords,
            )

            self._bindings[capability_name] = binding
            self._descriptors[capability_name] = descriptor

    def resolve(self, capability_name: str) -> CapabilityBinding:
        """
        Resolve capability by exact name.
        """
        if capability_name not in self._bindings:
            raise ValueError(f"Capability '{capability_name}' is not registered.")
        return self._bindings[capability_name]

    def descriptor_for(self, capability_name: str) -> Any:
        """
        Return descriptor for capability.
        """
        if capability_name not in self._descriptors:
            raise ValueError(f"Capability '{capability_name}' has no descriptor.")
        return self._descriptors[capability_name]

    def registered_capability_names(self) -> list[str]:
        """
        Return sorted registered capability names.
        """
        return sorted(self._bindings.keys())

    def registered_plugin_ids(self) -> list[str]:
        """
        Return sorted registered plugin IDs.
        """
        return sorted(self._plugin_ids)

    def as_debug_inventory(self) -> list[dict[str, Any]]:
        """
        Return lightweight inventory useful for debugging/router inspection.
        """
        rows: list[dict[str, Any]] = []

        for capability_name in self.registered_capability_names():
            binding = self._bindings[capability_name]
            descriptor = self._descriptors[capability_name]

            rows.append(
                {
                    "capability_name": capability_name,
                    "plugin_id": binding.plugin_id,
                    "output_kind": binding.output_kind,
                    "keywords": binding.keywords,
                    "required_inputs": list(getattr(descriptor, "required_inputs", []) or []),
                    "optional_inputs": list(getattr(descriptor, "optional_inputs", []) or []),
                    "metadata": dict(getattr(descriptor, "metadata", {}) or {}),
                }
            )

        return rows

    def as_plugin_inventory(self) -> list[dict[str, Any]]:
        """
        Return grouped plugin inventory for Plugin Manager UI/API.
        """
        grouped: dict[str, dict[str, Any]] = {}

        for capability_name in self.registered_capability_names():
            binding = self._bindings[capability_name]
            descriptor = self._descriptors[capability_name]

            plugin_id = str(binding.plugin_id)

            row = grouped.setdefault(
                plugin_id,
                {
                    "plugin_id": plugin_id,
                    "capabilities": [],
                    "capability_count": 0,
                },
            )

            row["capabilities"].append(
                {
                    "name": capability_name,
                    "output_kind": binding.output_kind,
                    "keywords": list(binding.keywords or []),
                    "required_inputs": list(
                        getattr(descriptor, "required_inputs", []) or []
                    ),
                    "optional_inputs": list(
                        getattr(descriptor, "optional_inputs", []) or []
                    ),
                    "metadata": dict(getattr(descriptor, "metadata", {}) or {}),
                }
            )

        items = list(grouped.values())

        for item in items:
            capabilities = sorted(
                item["capabilities"],
                key=lambda x: str(x.get("name") or ""),
            )
            item["capabilities"] = capabilities
            item["capability_count"] = len(capabilities)

        items.sort(key=lambda x: str(x.get("plugin_id") or ""))
        return items



class RegistryBackedCapabilityRouter:
    """
    Capability router backed by CapabilityRegistry.

    For now this router resolves by exact capability name.
    Later it will support:
        - keyword scoring
        - semantic scoring
        - ambiguity detection
        - LLM fallback
    """

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        plugin_module_names: list[str] | None = None,
    ) -> None:
        self.registry = registry or CapabilityRegistry.from_plugin_modules(plugin_module_names)

    def resolve(self, capability_name: str) -> CapabilityBinding:
        """
        Resolve capability by exact name.
        """
        return self.registry.resolve(capability_name)

    def descriptor_for(self, capability_name: str) -> Any:
        """
        Return descriptor for capability.
        """
        return self.registry.descriptor_for(capability_name)

    def registered_capability_names(self) -> list[str]:
        """
        Return sorted registered capability names.
        """
        return self.registry.registered_capability_names()

    def registered_plugin_ids(self) -> list[str]:
        """
        Return sorted registered plugin IDs.
        """
        return self.registry.registered_plugin_ids()

    def as_debug_inventory(self) -> list[dict[str, Any]]:
        """
        Return registry debug inventory.
        """
        return self.registry.as_debug_inventory()
