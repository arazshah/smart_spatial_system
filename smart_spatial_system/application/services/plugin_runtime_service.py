from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from orchestrator.plugin_state import PluginStateStoreError


class PluginRuntimeServiceError(RuntimeError):
    """Raised when plugin/runtime management fails."""


class PluginRuntimeService:
    def __init__(
        self,
        *,
        registry_getter: Callable[[], Any],
        plugin_state_store: Any,
        config_getter: Callable[[], Any],
        runtime_paths_getter: Callable[[], Any],
        output_storage_getter: Callable[[], Any],
        upload_storage_getter: Callable[[], Any],
        project_store_getter: Callable[[], Any],
    ) -> None:
        self._registry_getter = registry_getter
        self.plugin_state_store = plugin_state_store
        self._config_getter = config_getter
        self._runtime_paths_getter = runtime_paths_getter
        self._output_storage_getter = output_storage_getter
        self._upload_storage_getter = upload_storage_getter
        self._project_store_getter = project_store_getter

    def list_plugins(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return grouped plugin inventory for Plugin Manager.

        Read-only:
        - enabled/disabled is read from config/plugin_state.json
        - plugin-specific YAML config is not mutated here
        """
        registry = self._registry_getter()
        if registry is None:
            return []

        inventory = list(getattr(registry, "as_plugin_inventory")() or [])
        skipped_plugins = list(getattr(registry, "skipped_plugins", []) or [])

        items: list[dict[str, Any]] = []

        for item in inventory:
            plugin_id = str(item.get("plugin_id") or "")
            capabilities = list(item.get("capabilities") or [])

            module_names = sorted(
                {
                    str(cap.get("metadata", {}).get("module_name") or "")
                    for cap in capabilities
                    if isinstance(cap, dict)
                }
                - {""}
            )

            enabled = True
            try:
                enabled = self.plugin_state_store.is_enabled(plugin_id, default=True)
            except PluginStateStoreError:
                enabled = True

            has_config = False
            config_path = f"config/plugins/{plugin_id}.yaml"

            try:
                has_config = Path(config_path).exists()
            except Exception:
                has_config = False

            payload = {
                "plugin_id": plugin_id,
                "enabled": enabled,
                "state_source": "config/plugin_state.json",
                "config_path": config_path,
                "config_exists": has_config,
                "module_names": module_names,
                "capability_count": int(item.get("capability_count") or 0),
                "capabilities": capabilities,
                "skipped": False,
                "skipped_error": None,
            }

            if not module_names:
                # Try to infer from skipped plugins if available.
                for skipped in skipped_plugins:
                    if not isinstance(skipped, dict):
                        continue
                    module_name = str(skipped.get("module") or "")
                    if plugin_id and plugin_id in module_name:
                        payload["module_names"] = [module_name]
                        payload["skipped"] = True
                        payload["skipped_error"] = skipped.get("error")
                        break

            items.append(payload)

        items.sort(key=lambda x: str(x.get("plugin_id") or ""))
        return items

    def is_plugin_enabled(
        self,
        plugin_id: str,
    ) -> bool:
        """
        Return True if plugin is enabled in plugin_state.json.
        Missing state defaults to enabled.
        """
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id:
            return False

        try:
            return bool(self.plugin_state_store.is_enabled(plugin_id, default=True))
        except PluginStateStoreError:
            return True

    def disabled_plugin_ids(
        self,
    ) -> set[str]:
        """
        Return disabled plugin IDs from plugin inventory.
        """
        disabled: set[str] = set()

        for item in self.list_plugins():
            pid = str(item.get("plugin_id") or "").strip()
            if pid and not bool(item.get("enabled", True)):
                disabled.add(pid)

        return disabled

    def enabled_capability_names(
        self,
    ) -> list[str]:
        """
        Return registered capability names whose source plugin is enabled.
        """
        registry = self._registry_getter()
        bindings = getattr(registry, "_bindings", {}) or {}

        names: list[str] = []

        if not isinstance(bindings, dict):
            return names

        for capability_name, binding in bindings.items():
            plugin_id = str(getattr(binding, "plugin_id", "") or "").strip()
            if plugin_id and self.is_plugin_enabled(plugin_id):
                names.append(str(capability_name))

        return sorted(set(names))

    def assert_capability_enabled(
        self,
        capability_name: str,
    ) -> None:
        """
        Raise service error if capability exists but its plugin is disabled.
        """
        registry = self._registry_getter()
        bindings = getattr(registry, "_bindings", {}) or {}

        if not isinstance(bindings, dict):
            return

        binding = bindings.get(capability_name)
        if binding is None:
            return

        plugin_id = str(getattr(binding, "plugin_id", "") or "").strip()
        if plugin_id and not self.is_plugin_enabled(plugin_id):
            raise PluginRuntimeServiceError(
                f"Capability '{capability_name}' is disabled because plugin '{plugin_id}' is disabled."
            )

    def get_plugin(
        self,
        plugin_id: str,
    ) -> dict[str, Any]:
        """
        Return one plugin inventory item by plugin_id.
        """
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id:
            raise PluginRuntimeServiceError("plugin_id is required.")

        for item in self.list_plugins():
            if str(item.get("plugin_id")) == plugin_id:
                return item

        raise PluginRuntimeServiceError(f"Unknown plugin: {plugin_id}")

    def update_plugin_state(
        self,
        plugin_id: str,
        *,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """
        Update plugin manager state for one plugin.

        Scope:
        - supports only enabled/disabled
        - does not mutate plugin YAML config
        - does not rebuild runtime registry/router automatically
        """
        plugin = self.get_plugin(plugin_id)

        if enabled is None:
            raise PluginRuntimeServiceError("At least one mutable field is required.")

        try:
            self.plugin_state_store.set_enabled(plugin["plugin_id"], bool(enabled))
        except PluginStateStoreError as exc:
            raise PluginRuntimeServiceError(str(exc)) from exc

        return self.get_plugin(plugin["plugin_id"])

    def runtime_paths_metadata(self) -> dict[str, str]:
        """
        Return JSON-safe runtime path metadata.
        """
        runtime_paths = self._runtime_paths_getter()

        if runtime_paths is None:
            return {}

        payload = runtime_paths.as_dict()

        output_storage = self._output_storage_getter()
        upload_storage = self._upload_storage_getter()
        project_store = self._project_store_getter()

        if output_storage is not None:
            payload["outputs"] = str(getattr(output_storage, "root_dir", payload["outputs"]))

        if upload_storage is not None:
            payload["uploads"] = str(getattr(upload_storage, "root_dir", payload["uploads"]))

        if project_store is not None:
            payload["projects"] = str(getattr(project_store, "root_dir", payload["projects"]))

        return payload

    def get_runtime_settings(
        self,
    ) -> dict[str, Any]:
        """
        Return non-sensitive runtime settings for UI/debugging.

        Secrets such as API keys are never returned.
        """
        import os

        config = self._config_getter()

        plugin_modules = (
            getattr(config, "plugin_module_names", None)
            or getattr(config, "plugin_modules", None)
            or getattr(config, "plugins", None)
            or []
        )

        if isinstance(plugin_modules, tuple):
            plugin_modules = list(plugin_modules)

        if not isinstance(plugin_modules, list):
            plugin_modules = list(plugin_modules) if plugin_modules else []

        registry = self._registry_getter()
        bindings = getattr(registry, "_bindings", {}) or {}

        capability_names: list[str] = []
        plugin_ids: list[str] = []

        if isinstance(bindings, dict):
            capability_names = sorted(str(name) for name in bindings.keys())

            for binding in bindings.values():
                plugin_id = (
                    getattr(binding, "plugin_id", None)
                    or getattr(binding, "plugin_name", None)
                    or getattr(binding, "source_plugin", None)
                )

                if plugin_id:
                    plugin_ids.append(str(plugin_id))

        plugin_ids = sorted(set(plugin_ids))

        skipped_plugins = list(getattr(registry, "skipped_plugins", []) or [])
        enabled_capability_names = self.enabled_capability_names()
        disabled_plugin_ids = sorted(self.disabled_plugin_ids())

        return {
            "llm": {
                "provider": os.getenv("LLM_PROVIDER", "not_configured"),
                "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL"),
                "fast_model": os.getenv("LLM_FAST_MODEL"),
                "strong_model": os.getenv("LLM_STRONG_MODEL"),
                "default_model": os.getenv("LLM_DEFAULT_MODEL"),
                "temperature": os.getenv("LLM_TEMPERATURE"),
                "timeout_seconds": os.getenv("LLM_TIMEOUT_SECONDS"),
                "api_key_configured": bool(
                    os.getenv("OPENAI_API_KEY")
                    or os.getenv("AVALAI_API_KEY")
                    or os.getenv("LLM_API_KEY")
                ),
            },
            "plugins": {
                "module_names": plugin_modules,
                "plugin_ids": plugin_ids,
                "capabilities": capability_names,
                "capability_count": len(capability_names),
                "enabled_capabilities": enabled_capability_names,
                "enabled_capability_count": len(enabled_capability_names),
                "disabled_plugin_ids": disabled_plugin_ids,
                "skipped_plugins": skipped_plugins,
            },
            "runtime": {
                "runtime_dir": str(getattr(config, "runtime_dir", None))
                if getattr(config, "runtime_dir", None) is not None
                else None,
                "resolve_upload_refs_with_plugins": getattr(
                    config,
                    "resolve_upload_refs_with_plugins",
                    None,
                ),
                "raster_loader_plugin_module": getattr(
                    config,
                    "raster_loader_plugin_module",
                    None,
                ),
                "vector_loader_plugin_module": getattr(
                    config,
                    "vector_loader_plugin_module",
                    None,
                ),
            },
            "runtime_paths": self.runtime_paths_metadata(),
        }

    def run_llm_smoke_test(
        self,
    ) -> dict[str, Any]:
        """
        Run a non-sensitive backend LLM connectivity smoke test.
        """
        from orchestrator.llm_client import (
            LLMClientError,
            LLMConfigError,
            run_llm_smoke_test,
        )

        try:
            return run_llm_smoke_test()
        except (LLMConfigError, LLMClientError) as exc:
            raise PluginRuntimeServiceError(str(exc)) from exc
