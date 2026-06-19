"""
orchestrator.plugin_config_store

Safe, isolated read/write for per-plugin YAML configuration files.

Design principles:
    - Never mutate plugin_state.json (enable/disable lives elsewhere).
    - Never touch orchestrator runtime objects.
    - Root of a plugin config MUST be a mapping (dict).
    - Writes are atomic (temp file + os.replace).
    - Every write creates a timestamped backup.
    - Reads expose both raw YAML text and parsed object.
    - Plugin id is validated to avoid path traversal.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



class PluginConfigStoreError(RuntimeError):
    """Raised when a plugin config cannot be read or written safely."""


def find_config_dir() -> Path:
    """
    Self-contained resolver for the plugin config directory.

    Lookup order mirrors plugins._shared.plugin_config.find_config_dir,
    but without importing the plugins package (keeps this module light and
    safe to import in isolation, e.g. in tests).
    """
    env_dir = os.getenv("GEOCHAT_PLUGIN_CONFIG_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    cwd = Path.cwd().resolve()

    direct = cwd / "config" / "plugins"
    if direct.exists():
        return direct

    for parent in [cwd, *cwd.parents]:
        candidate = parent / "config" / "plugins"
        if candidate.exists():
            return candidate

    return direct


_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")


def _require_yaml():
    try:
        import yaml  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise PluginConfigStoreError(
            "PyYAML is required for plugin config editing."
        ) from exc
    import yaml

    return yaml


def _validate_plugin_id(plugin_id: str) -> str:
    pid = str(plugin_id or "").strip()

    if not pid or not _PLUGIN_ID_PATTERN.match(pid):
        raise PluginConfigStoreError(
            f"Invalid plugin id: {plugin_id!r}. "
            "Only letters, digits and underscore are allowed."
        )

    return pid


def _config_dir() -> Path:
    return Path(find_config_dir())


def _yaml_path(plugin_id: str) -> Path:
    return _config_dir() / f"{plugin_id}.yaml"


def _example_path(plugin_id: str) -> Path:
    return _config_dir() / f"{plugin_id}.example.yaml"


def _backups_dir() -> Path:
    return _config_dir() / ".backups"


def read_plugin_config(plugin_id: str) -> dict[str, Any]:
    """
    Read a plugin config file.

    Returns:
        {
          "plugin_id": str,
          "exists": bool,
          "path": str,
          "raw_yaml": str,
          "parsed": dict,
          "example_exists": bool,
          "example_raw_yaml": str | None,
        }
    """
    yaml = _require_yaml()
    pid = _validate_plugin_id(plugin_id)

    path = _yaml_path(pid)
    example = _example_path(pid)

    exists = path.exists()
    raw_yaml = ""
    parsed: dict[str, Any] = {}

    if exists:
        try:
            raw_yaml = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise PluginConfigStoreError(
                f"Failed to read config file for plugin '{pid}': {exc}"
            ) from exc

        try:
            loaded = yaml.safe_load(raw_yaml)
        except Exception as exc:
            raise PluginConfigStoreError(
                f"Plugin '{pid}' config is not valid YAML: {exc}"
            ) from exc

        if loaded is None:
            parsed = {}
        elif isinstance(loaded, dict):
            parsed = loaded
        else:
            raise PluginConfigStoreError(
                f"Plugin '{pid}' config root must be a mapping/object."
            )

    example_exists = example.exists()
    example_raw: str | None = None
    if example_exists:
        try:
            example_raw = example.read_text(encoding="utf-8")
        except Exception:
            example_raw = None

    return {
        "plugin_id": pid,
        "exists": exists,
        "path": f"config/plugins/{pid}.yaml",
        "raw_yaml": raw_yaml,
        "parsed": parsed,
        "example_exists": example_exists,
        "example_raw_yaml": example_raw,
    }


def _validate_yaml_text(raw_yaml: str) -> dict[str, Any]:
    yaml = _require_yaml()

    if not isinstance(raw_yaml, str):
        raise PluginConfigStoreError("raw_yaml must be a string.")

    try:
        loaded = yaml.safe_load(raw_yaml)
    except Exception as exc:
        raise PluginConfigStoreError(f"Invalid YAML: {exc}") from exc

    if loaded is None:
        return {}

    if not isinstance(loaded, dict):
        raise PluginConfigStoreError(
            "Plugin config root must be a mapping/object (key: value)."
        )

    return loaded


def _make_backup(path: Path) -> str | None:
    if not path.exists():
        return None

    backups = _backups_dir()
    backups.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backups / f"{path.stem}.{timestamp}.yaml"

    try:
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as exc:
        raise PluginConfigStoreError(
            f"Failed to create backup before write: {exc}"
        ) from exc

    return f"config/plugins/.backups/{backup_path.name}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.stem}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(tmp_name, path)
    except Exception as exc:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except OSError:
            pass
        raise PluginConfigStoreError(
            f"Failed to write config file atomically: {exc}"
        ) from exc


def write_plugin_config(
    plugin_id: str,
    *,
    raw_yaml: str | None = None,
    parsed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Write a plugin config file safely.

    Exactly one of raw_yaml / parsed should be provided.
    If raw_yaml is given, it is validated and stored verbatim.
    If parsed is given, it is serialized to YAML.

    Returns metadata including the created backup path (if any).
    """
    yaml = _require_yaml()
    pid = _validate_plugin_id(plugin_id)

    if raw_yaml is None and parsed is None:
        raise PluginConfigStoreError("Either raw_yaml or parsed must be provided.")

    if raw_yaml is not None and parsed is not None:
        raise PluginConfigStoreError(
            "Provide only one of raw_yaml or parsed, not both."
        )

    if raw_yaml is not None:
        validated = _validate_yaml_text(raw_yaml)
        content = raw_yaml if raw_yaml.endswith("\n") else raw_yaml + "\n"
    else:
        if not isinstance(parsed, dict):
            raise PluginConfigStoreError("parsed must be a mapping/object.")
        validated = parsed
        content = yaml.safe_dump(
            parsed,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )

    path = _yaml_path(pid)
    backup_path = _make_backup(path)

    _atomic_write(path, content)

    return {
        "plugin_id": pid,
        "path": f"config/plugins/{pid}.yaml",
        "backup_path": backup_path,
        "keys": sorted(validated.keys()),
        "written": True,
    }
