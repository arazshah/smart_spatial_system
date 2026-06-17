"""
orchestrator.weight_store_persistence

Persistence utilities for router weight stores.

This module saves and loads InMemoryRouterWeightStore snapshots to/from JSON.

Important:
    Persistence only stores already-applied weights.
    It does not approve proposals.
    It does not modify router behavior directly.

Typical flow:
    Weight proposals
    -> approve
    -> apply to InMemoryRouterWeightStore
    -> save JSON
    -> later load JSON
    -> pass loaded store to WeightedCapabilityRouter
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.weight_proposals import (
    InMemoryRouterWeightStore,
    WeightStoreConfig,
)


WEIGHT_STORE_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class WeightStorePersistenceConfig:
    """
    Configuration for JSON persistence.
    """

    path: str | Path = "weights/router_weights.json"
    create_parent_dirs: bool = True
    indent: int = 2
    sort_keys: bool = True
    atomic_write: bool = True

    def __post_init__(self) -> None:
        if self.indent < 0:
            raise ValueError("indent must be >= 0.")


class WeightStorePersistenceError(RuntimeError):
    """
    Raised when weight-store persistence fails.
    """


class RouterWeightStorePersistence:
    """
    Save/load InMemoryRouterWeightStore using JSON files.
    """

    def __init__(
        self,
        config: WeightStorePersistenceConfig | None = None,
    ) -> None:
        self.config = config or WeightStorePersistenceConfig()
        self.path = Path(self.config.path)

    def save(
        self,
        weight_store: InMemoryRouterWeightStore,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Save weight store to JSON and return saved payload.
        """
        payload = self.to_payload(
            weight_store,
            metadata=metadata,
        )

        if self.config.create_parent_dirs:
            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        try:
            if self.config.atomic_write:
                self._atomic_write_json(payload)
            else:
                self.path.write_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=self.config.indent,
                        sort_keys=self.config.sort_keys,
                    ),
                    encoding="utf-8",
                )
        except OSError as exc:
            raise WeightStorePersistenceError(
                f"Failed to save weight store to {self.path}: {exc}"
            ) from exc

        return payload

    def load(self) -> InMemoryRouterWeightStore:
        """
        Load weight store from JSON.
        """
        if not self.path.exists():
            raise WeightStorePersistenceError(
                f"Weight store file does not exist: {self.path}"
            )

        try:
            payload = json.loads(
                self.path.read_text(
                    encoding="utf-8",
                )
            )
        except json.JSONDecodeError as exc:
            raise WeightStorePersistenceError(
                f"Invalid weight store JSON: {self.path}: {exc}"
            ) from exc
        except OSError as exc:
            raise WeightStorePersistenceError(
                f"Failed to read weight store from {self.path}: {exc}"
            ) from exc

        return self.from_payload(payload)

    def load_or_default(
        self,
        *,
        default_config: WeightStoreConfig | None = None,
    ) -> InMemoryRouterWeightStore:
        """
        Load existing store or return a default store if file is missing.
        """
        if not self.path.exists():
            return InMemoryRouterWeightStore(
                config=default_config,
            )

        return self.load()

    def exists(self) -> bool:
        return self.path.exists()

    def delete(self) -> None:
        """
        Delete persisted file if it exists.
        """
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError as exc:
            raise WeightStorePersistenceError(
                f"Failed to delete weight store file {self.path}: {exc}"
            ) from exc

    def to_payload(
        self,
        weight_store: InMemoryRouterWeightStore,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Convert weight store to JSON-like payload.
        """
        store_payload = weight_store.to_dict()

        return {
            "schema_version": WEIGHT_STORE_SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "store": store_payload,
            "metadata": dict(metadata or {}),
        }

    @staticmethod
    def from_payload(payload: dict[str, Any]) -> InMemoryRouterWeightStore:
        """
        Build InMemoryRouterWeightStore from JSON-like payload.
        """
        if not isinstance(payload, dict):
            raise WeightStorePersistenceError("Weight store payload must be a dict.")

        schema_version = payload.get("schema_version")

        if schema_version != WEIGHT_STORE_SCHEMA_VERSION:
            raise WeightStorePersistenceError(
                f"Unsupported weight store schema_version: {schema_version!r}"
            )

        store = payload.get("store")

        if not isinstance(store, dict):
            raise WeightStorePersistenceError("Weight store payload missing 'store' dict.")

        config_payload = store.get("config", {})

        if not isinstance(config_payload, dict):
            raise WeightStorePersistenceError("Weight store config must be a dict.")

        try:
            config = WeightStoreConfig(
                default_weight=float(config_payload.get("default_weight", 1.0)),
                min_weight=float(config_payload.get("min_weight", 0.0)),
                max_weight=float(config_payload.get("max_weight", 3.0)),
            )
        except (TypeError, ValueError) as exc:
            raise WeightStorePersistenceError(
                f"Invalid weight store config: {exc}"
            ) from exc

        capability_weights = store.get("capability_weights", {})
        plugin_weights = store.get("plugin_weights", {})

        if not isinstance(capability_weights, dict):
            raise WeightStorePersistenceError("capability_weights must be a dict.")

        if not isinstance(plugin_weights, dict):
            raise WeightStorePersistenceError("plugin_weights must be a dict.")

        try:
            normalized_capability_weights = {
                str(name): float(value)
                for name, value in capability_weights.items()
            }

            normalized_plugin_weights = {
                str(name): float(value)
                for name, value in plugin_weights.items()
            }
        except (TypeError, ValueError) as exc:
            raise WeightStorePersistenceError(
                f"Invalid weight value in persisted store: {exc}"
            ) from exc

        return InMemoryRouterWeightStore(
            config=config,
            capability_weights=normalized_capability_weights,
            plugin_weights=normalized_plugin_weights,
        )

    def _atomic_write_json(self, payload: dict[str, Any]) -> None:
        """
        Atomic JSON write using temp file + os.replace.
        """
        directory = self.path.parent
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        content = json.dumps(
            payload,
            ensure_ascii=False,
            indent=self.config.indent,
            sort_keys=self.config.sort_keys,
        )

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(directory),
            text=True,
        )

        temp_path = Path(temp_name)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)

            os.replace(
                temp_path,
                self.path,
            )
        except Exception:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            raise
