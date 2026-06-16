"""
plugins._shared.plugin_config

Shared configuration utilities for GeoChat plugins.

Purpose:
    - Load per-plugin config files from config/plugins/<plugin_id>.yaml
    - Support plugin profiles
    - Resolve environment variables
    - Keep plugin configuration outside plugin code
    - Avoid storing secrets directly in source code

Configuration lookup order:
    1. GEOCHAT_PLUGIN_CONFIG_DIR environment variable
    2. <current-working-directory>/config/plugins
    3. nearest parent directory containing config/plugins

Supported files:
    - YAML: .yaml / .yml
    - JSON: .json

Example:
    config/plugins/postgis_connector.yaml
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


class PluginConfigError(RuntimeError):
    """
    Raised when plugin configuration cannot be loaded or resolved.
    """


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def find_config_dir() -> Path:
    """
    Find the plugin configuration directory.

    Returns:
        Path to config/plugins.

    Notes:
        If the directory does not exist, it is still returned based on cwd.
        This allows callers to detect missing files gracefully.
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


def get_plugin_config_path(plugin_id: str) -> Path | None:
    """
    Return the first existing config file path for a plugin.

    Search order:
        <plugin_id>.yaml
        <plugin_id>.yml
        <plugin_id>.json
    """
    config_dir = find_config_dir()

    for suffix in (".yaml", ".yml", ".json"):
        candidate = config_dir / f"{plugin_id}{suffix}"
        if candidate.exists():
            return candidate

    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    """
    Load YAML config file.
    """
    try:
        import yaml
    except ImportError as exc:
        raise PluginConfigError(
            "PyYAML is required for YAML plugin configs. "
            "Install it with: pip install pyyaml"
        ) from exc

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        raise PluginConfigError(f"Failed to read YAML config: {path}. Error: {exc}") from exc

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise PluginConfigError(f"Plugin config root must be an object/dict: {path}")

    return data


def _load_json(path: Path) -> dict[str, Any]:
    """
    Load JSON config file.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise PluginConfigError(f"Failed to read JSON config: {path}. Error: {exc}") from exc

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise PluginConfigError(f"Plugin config root must be an object/dict: {path}")

    return data


def load_plugin_config(plugin_id: str, required: bool = False) -> dict[str, Any]:
    """
    Load config for a plugin.

    Args:
        plugin_id:
            Plugin identifier.
        required:
            If True, missing config file raises PluginConfigError.
            If False, missing config file returns {}.

    Returns:
        Config dictionary.
    """
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        raise PluginConfigError("plugin_id must be a non-empty string.")

    path = get_plugin_config_path(plugin_id)

    if path is None:
        if required:
            raise PluginConfigError(
                f"Config file not found for plugin '{plugin_id}' in {find_config_dir()}"
            )
        return {}

    if path.suffix.lower() in {".yaml", ".yml"}:
        return _load_yaml(path)

    if path.suffix.lower() == ".json":
        return _load_json(path)

    raise PluginConfigError(f"Unsupported config file extension: {path}")


def _resolve_env_string(value: str) -> str:
    """
    Resolve ${ENV_NAME} or ${ENV_NAME:default} placeholders inside strings.
    """
    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        default = match.group(2)
        env_value = os.getenv(env_name)

        if env_value is not None:
            return env_value

        if default is not None:
            return default

        raise PluginConfigError(f"Environment variable is not set: {env_name}")

    return _ENV_PATTERN.sub(replace, value)


def resolve_env_refs(value: Any) -> Any:
    """
    Recursively resolve environment references.

    Supports:
        password_env: POSTGIS_PASSWORD
            -> password: os.environ["POSTGIS_PASSWORD"]

        host: ${POSTGIS_HOST:localhost}
            -> host: env value or default

    Rules:
        - Keys ending with '_env' are converted to base key.
        - Example: password_env -> password
        - If environment variable is missing, PluginConfigError is raised.
    """
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}

        for key, item in value.items():
            if key.endswith("_env"):
                base_key = key[:-4]
                if not isinstance(item, str) or not item.strip():
                    raise PluginConfigError(f"{key} must contain an environment variable name.")

                env_name = item.strip()
                env_value = os.getenv(env_name)

                if env_value is None:
                    raise PluginConfigError(f"Environment variable is not set: {env_name}")

                resolved[base_key] = env_value
            else:
                resolved[key] = resolve_env_refs(item)

        return resolved

    if isinstance(value, list):
        return [resolve_env_refs(item) for item in value]

    if isinstance(value, str):
        return _resolve_env_string(value)

    return value


def get_profile_config(
    plugin_id: str,
    profile: str | None = None,
    profiles_key: str = "profiles",
    required: bool = False,
) -> dict[str, Any]:
    """
    Load a profile config for a plugin.

    Args:
        plugin_id:
            Plugin identifier.
        profile:
            Profile name. If None, config['default_profile'] is used.
        profiles_key:
            Name of the profiles section.
        required:
            If True, missing config raises.

    Returns:
        Resolved profile dictionary.

    Example config:
        default_profile: local
        profiles:
          local:
            host: localhost
            password_env: POSTGIS_PASSWORD
    """
    config = load_plugin_config(plugin_id, required=required)

    if not config:
        return {}

    profiles = config.get(profiles_key, {})
    if profiles is None:
        profiles = {}

    if not isinstance(profiles, dict):
        raise PluginConfigError(f"'{profiles_key}' must be a dict in plugin '{plugin_id}' config.")

    selected = profile or config.get("default_profile")

    if selected is None:
        return {}

    if selected not in profiles:
        raise PluginConfigError(
            f"Profile '{selected}' not found for plugin '{plugin_id}'. "
            f"Available profiles: {sorted(profiles.keys())}"
        )

    profile_config = profiles[selected]

    if not isinstance(profile_config, dict):
        raise PluginConfigError(
            f"Profile '{selected}' for plugin '{plugin_id}' must be a dict."
        )

    return resolve_env_refs(profile_config)


def pick_first(*values: Any, default: Any = None) -> Any:
    """
    Return first value that is not None.
    """
    for value in values:
        if value is not None:
            return value

    return default
