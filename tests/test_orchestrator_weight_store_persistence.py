"""
Tests for router weight store persistence.

Run:
    pytest tests/test_orchestrator_weight_store_persistence.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.weight_proposals import (  # noqa: E402
    InMemoryRouterWeightStore,
    WeightStoreConfig,
)
from orchestrator.weight_store_persistence import (  # noqa: E402
    RouterWeightStorePersistence,
    WEIGHT_STORE_SCHEMA_VERSION,
    WeightStorePersistenceConfig,
    WeightStorePersistenceError,
)


def test_weight_store_persistence_saves_json_file(tmp_path: Path) -> None:
    path = tmp_path / "weights" / "router_weights.json"

    store = InMemoryRouterWeightStore(
        WeightStoreConfig(
            default_weight=1.0,
            min_weight=0.0,
            max_weight=2.0,
        ),
        capability_weights={
            "threshold_raster": 1.15,
        },
        plugin_weights={
            "raster_threshold": 1.2,
        },
    )

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
        )
    )

    payload = persistence.save(
        store,
        metadata={
            "source": "unit-test",
        },
    )

    assert path.exists()
    assert payload["schema_version"] == WEIGHT_STORE_SCHEMA_VERSION
    assert payload["metadata"]["source"] == "unit-test"

    loaded_json = json.loads(path.read_text(encoding="utf-8"))

    assert loaded_json["store"]["config"]["default_weight"] == 1.0
    assert loaded_json["store"]["config"]["max_weight"] == 2.0
    assert loaded_json["store"]["capability_weights"]["threshold_raster"] == 1.15
    assert loaded_json["store"]["plugin_weights"]["raster_threshold"] == 1.2


def test_weight_store_persistence_loads_saved_store(tmp_path: Path) -> None:
    path = tmp_path / "router_weights.json"

    original_store = InMemoryRouterWeightStore(
        WeightStoreConfig(
            default_weight=0.9,
            min_weight=0.1,
            max_weight=2.5,
        ),
        capability_weights={
            "calculate_spectral_index": 0.8,
            "threshold_raster": 1.25,
        },
        plugin_weights={
            "spectral_indices": 0.75,
            "raster_threshold": 1.3,
        },
    )

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
        )
    )

    persistence.save(original_store)

    loaded_store = persistence.load()

    assert loaded_store.config.default_weight == 0.9
    assert loaded_store.config.min_weight == 0.1
    assert loaded_store.config.max_weight == 2.5

    assert loaded_store.get_weight("capability", "calculate_spectral_index") == 0.8
    assert loaded_store.get_weight("capability", "threshold_raster") == 1.25
    assert loaded_store.get_weight("plugin", "spectral_indices") == 0.75
    assert loaded_store.get_weight("plugin", "raster_threshold") == 1.3

    assert loaded_store.get_weight("capability", "unknown") == 0.9


def test_weight_store_persistence_load_or_default_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
        )
    )

    store = persistence.load_or_default(
        default_config=WeightStoreConfig(
            default_weight=1.5,
            min_weight=0.0,
            max_weight=3.0,
        )
    )

    assert store.get_weight("capability", "anything") == 1.5
    assert not path.exists()


def test_weight_store_persistence_exists_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "router_weights.json"

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
        )
    )

    assert persistence.exists() is False

    persistence.save(InMemoryRouterWeightStore())

    assert persistence.exists() is True

    persistence.delete()

    assert persistence.exists() is False


def test_weight_store_persistence_rejects_missing_file_on_load(tmp_path: Path) -> None:
    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=tmp_path / "missing.json",
        )
    )

    with pytest.raises(WeightStorePersistenceError, match="does not exist"):
        persistence.load()


def test_weight_store_persistence_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{bad json", encoding="utf-8")

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
        )
    )

    with pytest.raises(WeightStorePersistenceError, match="Invalid weight store JSON"):
        persistence.load()


def test_weight_store_persistence_rejects_unsupported_schema_version() -> None:
    payload = {
        "schema_version": "999.0.0",
        "store": {},
    }

    with pytest.raises(WeightStorePersistenceError, match="Unsupported"):
        RouterWeightStorePersistence.from_payload(payload)


def test_weight_store_persistence_rejects_missing_store_payload() -> None:
    payload = {
        "schema_version": WEIGHT_STORE_SCHEMA_VERSION,
    }

    with pytest.raises(WeightStorePersistenceError, match="missing 'store'"):
        RouterWeightStorePersistence.from_payload(payload)


def test_weight_store_persistence_rejects_invalid_weight_values() -> None:
    payload = {
        "schema_version": WEIGHT_STORE_SCHEMA_VERSION,
        "store": {
            "config": {
                "default_weight": 1.0,
                "min_weight": 0.0,
                "max_weight": 3.0,
            },
            "capability_weights": {
                "bad": "not-a-number",
            },
            "plugin_weights": {},
        },
    }

    with pytest.raises(WeightStorePersistenceError, match="Invalid weight value"):
        RouterWeightStorePersistence.from_payload(payload)


def test_weight_store_persistence_config_rejects_invalid_indent() -> None:
    with pytest.raises(ValueError, match="indent"):
        WeightStorePersistenceConfig(indent=-1)


def test_weight_store_persistence_can_save_without_atomic_write(tmp_path: Path) -> None:
    path = tmp_path / "router_weights.json"

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
            atomic_write=False,
        )
    )

    persistence.save(InMemoryRouterWeightStore())

    assert path.exists()
