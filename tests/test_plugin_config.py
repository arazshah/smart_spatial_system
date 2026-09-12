from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins._shared.plugin_config import (
    PluginConfigError,
    find_config_dir,
    get_profile_config,
    load_plugin_config,
    resolve_env_refs,
)


def test_resolve_env_refs_from_env_key(monkeypatch) -> None:
    monkeypatch.setenv("TEST_PLUGIN_PASSWORD", "secret")

    value = {
        "host": "localhost",
        "password_env": "TEST_PLUGIN_PASSWORD",
    }

    resolved = resolve_env_refs(value)

    assert resolved["host"] == "localhost"
    assert resolved["password"] == "secret"
    assert "password_env" not in resolved


def test_resolve_env_refs_from_placeholder(monkeypatch) -> None:
    monkeypatch.setenv("TEST_PLUGIN_HOST", "127.0.0.1")

    resolved = resolve_env_refs({
        "host": "${TEST_PLUGIN_HOST}",
        "port": "${TEST_PLUGIN_PORT:5432}",
    })

    assert resolved["host"] == "127.0.0.1"
    assert resolved["port"] == "5432"


def test_resolve_env_refs_missing_env_raises() -> None:
    with pytest.raises(PluginConfigError):
        resolve_env_refs({"password_env": "THIS_ENV_SHOULD_NOT_EXIST_123"})


def test_load_plugin_config_from_custom_dir(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    config_file = config_dir / "sample_plugin.yaml"
    config_file.write_text(
        """
default_profile: local
profiles:
  local:
    host: localhost
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    config = load_plugin_config("sample_plugin")

    assert config["default_profile"] == "local"
    assert config["profiles"]["local"]["host"] == "localhost"


def test_get_profile_config_from_custom_dir(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    config_file = config_dir / "sample_plugin.yaml"
    config_file.write_text(
        """
default_profile: local
profiles:
  local:
    host: localhost
    password_env: SAMPLE_PLUGIN_PASSWORD
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SAMPLE_PLUGIN_PASSWORD", "secret")

    profile = get_profile_config("sample_plugin")

    assert profile["host"] == "localhost"
    assert profile["password"] == "secret"


def test_find_config_dir_falls_back_to_packaged_config_outside_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    """
    REFACTOR_PLAN Phase 8 step 2: with no GEOCHAT_PLUGIN_CONFIG_DIR set and
    no config/plugins reachable by walking up from cwd (simulated here by
    running from an isolated tmp_path outside the repo checkout), resolution
    falls back to the default config shipped as package data
    (config/plugins, made importable via config/__init__.py +
    config/plugins/__init__.py) instead of silently returning a
    non-existent path.
    """
    monkeypatch.delenv("GEOCHAT_PLUGIN_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    config_dir = find_config_dir()

    assert config_dir.exists()
    assert config_dir.name == "plugins"
    assert (config_dir / "local_vector_loader.yaml").exists()
