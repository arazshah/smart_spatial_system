from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



def test_default_safe_plugin_modules_are_centralized() -> None:
    from orchestrator.capability_registry import DEFAULT_SAFE_PLUGIN_MODULES as registry_default
    from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES as centralized_default
    from orchestrator.service import DEFAULT_SAFE_PLUGIN_MODULES as service_default

    assert registry_default is centralized_default
    assert service_default is centralized_default
    assert len(centralized_default) >= 30


def test_registry_default_plugin_modules_cover_op_catalog() -> None:
    from orchestrator.capability_registry import CapabilityRegistry
    from orchestrator.planning.op_catalog import OP_CATALOG

    registry = CapabilityRegistry.from_plugin_modules(tolerant=True)

    registered = set(registry.registered_capability_names())
    needed = {desc.capability_name for desc in OP_CATALOG.values()}

    assert needed <= registered


def test_default_plugin_modules_register_strictly_without_skips() -> None:
    from orchestrator.capability_registry import CapabilityRegistry

    registry = CapabilityRegistry.from_plugin_modules(tolerant=False)

    assert registry.skipped_plugins == []
    assert "ndvi_processor" in registry.registered_capability_names()
