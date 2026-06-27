"""
Regression tests for the plugin/capability boundary.

Production layers should not import concrete plugin implementation modules
directly. They should depend on registries, routers, capability resolvers, or
plugin module name configuration instead.
"""

from __future__ import annotations

import ast
from pathlib import Path


PRODUCTION_ROOTS = [
    Path("api"),
    Path("orchestrator"),
    Path("smart_spatial_system"),
]


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []

    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _direct_plugin_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "plugins" or name.startswith("plugins."):
                    violations.append(
                        f"{path}:{node.lineno}: import {name}"
                    )

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "plugins" or module.startswith("plugins."):
                imported = ", ".join(alias.name for alias in node.names)
                violations.append(
                    f"{path}:{node.lineno}: from {module} import {imported}"
                )

    return violations


def test_production_layers_do_not_import_concrete_plugin_modules_directly() -> None:
    violations: list[str] = []

    for root in PRODUCTION_ROOTS:
        for path in _python_files(root):
            violations.extend(_direct_plugin_imports(path))

    assert violations == []


def test_plugin_module_names_are_configured_in_dedicated_module() -> None:
    source = Path("orchestrator/plugin_modules.py").read_text(encoding="utf-8")

    assert "DEFAULT_SAFE_PLUGIN_MODULES" in source
    assert '"plugins.ndvi_analysis"' in source
    assert '"plugins.postgis_connector"' in source


def test_service_uses_centralized_default_plugin_modules() -> None:
    service_source = Path("orchestrator/service.py").read_text(encoding="utf-8")
    query_service_source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES" in service_source

    # QueryExecutionService should not own the concrete default plugin list.
    assert "DEFAULT_SAFE_PLUGIN_MODULES = [" not in query_service_source
