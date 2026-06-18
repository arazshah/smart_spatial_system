"""
orchestrator.plugin_state

Lightweight plugin enable/disable state store for Plugin Manager.

This store is intentionally separate from per-plugin YAML config files.

State file example:
{
  "version": "1.0.0",
  "plugins": {
    "local_raster_loader": {"enabled": true},
    "local_vector_loader": {"enabled": false}
  }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLUGIN_STATE_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class PluginStateStoreConfig:
    path: str | Path = "config/plugin_state.json"
    indent: int = 2
    ensure_ascii: bool = False

    def __post_init__(self) -> None:
        if self.indent < 0:
            raise ValueError("indent must be >= 0.")


class PluginStateStoreError(RuntimeError):
    pass


class PluginStateStore:
    def __init__(
        self,
        config: PluginStateStoreConfig | None = None,
    ) -> None:
        self.config = config or PluginStateStoreConfig()
        self.path = Path(self.config.path)

    def read_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": PLUGIN_STATE_SCHEMA_VERSION,
                "plugins": {},
            }

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PluginStateStoreError(
                f"Failed to read plugin state file: {self.path}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise PluginStateStoreError("Plugin state file must contain an object.")

        plugins = payload.get("plugins")
        if not isinstance(plugins, dict):
            plugins = {}

        return {
            "version": str(payload.get("version") or PLUGIN_STATE_SCHEMA_VERSION),
            "plugins": plugins,
        }

    def is_enabled(
        self,
        plugin_id: str,
        *,
        default: bool = True,
    ) -> bool:
        state = self.read_state()
        plugins = state.get("plugins") or {}

        item = plugins.get(plugin_id)
        if not isinstance(item, dict):
            return default

        return bool(item.get("enabled", default))

    def write_state(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise PluginStateStoreError("state must be an object.")

        plugins = state.get("plugins")
        if not isinstance(plugins, dict):
            plugins = {}

        payload = {
            "version": str(state.get("version") or PLUGIN_STATE_SCHEMA_VERSION),
            "plugins": plugins,
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    payload,
                    indent=self.config.indent,
                    ensure_ascii=self.config.ensure_ascii,
                ) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            raise PluginStateStoreError(
                f"Failed to write plugin state file: {self.path}: {exc}"
            ) from exc

        return payload

    def set_enabled(
        self,
        plugin_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id:
            raise PluginStateStoreError("plugin_id is required.")

        state = self.read_state()
        plugins = dict(state.get("plugins") or {})
        item = dict(plugins.get(plugin_id) or {})
        item["enabled"] = bool(enabled)
        plugins[plugin_id] = item
        state["plugins"] = plugins
        return self.write_state(state)
