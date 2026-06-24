"""Runtime path configuration helpers.

This module defines the canonical local runtime directory layout for the
backend.

The runtime directory is where generated state should live, such as:

- outputs
- uploads
- projects
- reports
- cache

This module is intentionally small and side-effect free by default. It does not
create directories unless `ensure()` is called.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


ENV_RUNTIME_DIR = "SMART_SPATIAL_RUNTIME_DIR"
DEFAULT_RUNTIME_DIR = "var"


@dataclass(frozen=True)
class RuntimePaths:
    """Canonical runtime paths for local backend execution."""

    root: Path
    outputs: Path
    uploads: Path
    projects: Path
    reports: Path
    cache: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "RuntimePaths":
        """Build runtime paths from an explicit root directory."""

        root_path = Path(root).expanduser()
        return cls(
            root=root_path,
            outputs=root_path / "outputs",
            uploads=root_path / "uploads",
            projects=root_path / "projects",
            reports=root_path / "reports",
            cache=root_path / "cache",
        )

    @classmethod
    def from_env(
        cls,
        runtime_dir: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> "RuntimePaths":
        """Build runtime paths from explicit input, environment, or default.

        Priority:

        1. explicit `runtime_dir`
        2. `SMART_SPATIAL_RUNTIME_DIR`
        3. default `var`
        """

        source_env = env if env is not None else os.environ

        if runtime_dir is not None:
            return cls.from_root(runtime_dir)

        configured = source_env.get(ENV_RUNTIME_DIR)
        if configured:
            return cls.from_root(configured)

        return cls.from_root(DEFAULT_RUNTIME_DIR)

    def ensure(self) -> "RuntimePaths":
        """Create runtime directories if they do not exist."""

        self.root.mkdir(parents=True, exist_ok=True)
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.projects.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        return self

    def as_dict(self) -> dict[str, str]:
        """Return paths as strings for metadata, diagnostics, or APIs."""

        return {
            "root": str(self.root),
            "outputs": str(self.outputs),
            "uploads": str(self.uploads),
            "projects": str(self.projects),
            "reports": str(self.reports),
            "cache": str(self.cache),
        }
