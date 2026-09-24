"""
Regression/guard test for case-study bug 008.

The wheel installs generic top-level packages (`api`, `config`,
`orchestrator`, `plugins`, `templates`) alongside `smart_spatial_system`
and `s3geo`. Because these are plain top-level package names (not nested
under a namespace like `smart_spatial_system.plugins`), any other
installed/importable project that also happens to define a top-level
`config` or `plugins` package can collide with this one, with the winner
decided by import/sys.path order rather than anything explicit.

This is a documentation-and-guard test, not a "fails then passes" bug fix:
see docs/PACKAGE_NAMESPACE_COLLISION.md for the full writeup and the
existing docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md "Open design questions"
section, which already flagged this and deliberately deferred the fix (a
full namespace migration) out of scope for a first packaging pass.

Run:
    pytest tests/test_bug_008_package_namespace_collision.py -v
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def dummy_config_package_ahead_on_path(tmp_path: Path):
    """
    Simulate a working directory that has its OWN top-level `config`
    package, placed ahead of this project on sys.path - e.g. a user's own
    project directory added by their own tooling, or a pip-installed
    dependency shipping a `config` package of its own.
    """
    dummy_root = tmp_path / "unrelated_project"
    dummy_config_pkg = dummy_root / "config"
    dummy_config_pkg.mkdir(parents=True)
    (dummy_config_pkg / "__init__.py").write_text(
        textwrap.dedent(
            """
            MARKER = "this-is-the-unrelated-projects-config-package"
            """
        )
    )

    sys.path.insert(0, str(dummy_root))
    for name in list(sys.modules):
        if name == "config" or name.startswith("config."):
            del sys.modules[name]

    try:
        yield dummy_root
    finally:
        sys.path.remove(str(dummy_root))
        for name in list(sys.modules):
            if name == "config" or name.startswith("config."):
                del sys.modules[name]


def test_top_level_config_package_name_can_be_shadowed(dummy_config_package_ahead_on_path):
    """
    Demonstrates the collision risk described in bug #008: because this
    project's plugin-config package is named plainly `config` (not
    `smart_spatial_system.config`), an unrelated `config` package earlier
    on sys.path silently wins the import, instead of either package being
    unambiguously reachable.
    """
    import config  # noqa: PLC0415 - deliberately late import for the test

    assert config.__file__.startswith(str(dummy_config_package_ahead_on_path))
    assert getattr(config, "MARKER", None) == "this-is-the-unrelated-projects-config-package"

    # The real, intended config package is not what got imported.
    assert not config.__file__.startswith(str(PROJECT_ROOT))


def test_migration_plan_is_documented():
    """
    Bug #008 is scoped as documentation + a guard test for 0.5.7 (see the
    task's "Definitions of done"), not a full namespace-migration code
    change. Make sure the write-up actually exists and mentions the
    colliding package names, so this isn't just a dangling promise.
    """
    doc_path = PROJECT_ROOT / "docs" / "PACKAGE_NAMESPACE_COLLISION.md"
    assert doc_path.exists(), "Expected docs/PACKAGE_NAMESPACE_COLLISION.md to document bug #008."

    text = doc_path.read_text()
    for package_name in ("api", "config", "orchestrator", "plugins", "templates"):
        assert package_name in text
