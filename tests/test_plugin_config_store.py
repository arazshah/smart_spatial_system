import os
from pathlib import Path

import pytest

import importlib.util
from pathlib import Path as _Path

_STORE_PATH = _Path(__file__).resolve().parents[1] / "orchestrator" / "plugin_config_store.py"
_spec = importlib.util.spec_from_file_location("plugin_config_store_isolated", _STORE_PATH)
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)
PluginConfigStoreError = store.PluginConfigStoreError


@pytest.fixture
def plugin_config_dir(tmp_path, monkeypatch):
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))
    return config_dir


def test_read_missing_config_returns_empty(plugin_config_dir):
    result = store.read_plugin_config("sample_plugin")

    assert result["plugin_id"] == "sample_plugin"
    assert result["exists"] is False
    assert result["parsed"] == {}
    assert result["raw_yaml"] == ""


def test_read_existing_config(plugin_config_dir):
    (plugin_config_dir / "sample_plugin.yaml").write_text(
        "alpha: 1\nbeta: hello\n", encoding="utf-8"
    )

    result = store.read_plugin_config("sample_plugin")

    assert result["exists"] is True
    assert result["parsed"] == {"alpha": 1, "beta": "hello"}
    assert "alpha: 1" in result["raw_yaml"]


def test_read_example_config_is_exposed(plugin_config_dir):
    (plugin_config_dir / "sample_plugin.example.yaml").write_text(
        "alpha: 1\n", encoding="utf-8"
    )

    result = store.read_plugin_config("sample_plugin")

    assert result["example_exists"] is True
    assert "alpha: 1" in result["example_raw_yaml"]


def test_write_raw_yaml_creates_file(plugin_config_dir):
    result = store.write_plugin_config(
        "sample_plugin",
        raw_yaml="alpha: 2\nbeta: world\n",
    )

    assert result["written"] is True
    assert result["backup_path"] is None  # no previous file

    path = plugin_config_dir / "sample_plugin.yaml"
    assert path.exists()
    assert "alpha: 2" in path.read_text(encoding="utf-8")


def test_write_creates_backup_when_file_exists(plugin_config_dir):
    path = plugin_config_dir / "sample_plugin.yaml"
    path.write_text("alpha: 1\n", encoding="utf-8")

    result = store.write_plugin_config(
        "sample_plugin",
        raw_yaml="alpha: 99\n",
    )

    assert result["backup_path"] is not None

    backups = list((plugin_config_dir / ".backups").glob("sample_plugin.*.yaml"))
    assert len(backups) == 1
    assert "alpha: 1" in backups[0].read_text(encoding="utf-8")
    assert "alpha: 99" in path.read_text(encoding="utf-8")


def test_write_parsed_serializes_yaml(plugin_config_dir):
    store.write_plugin_config(
        "sample_plugin",
        parsed={"alpha": 5, "nested": {"x": True}},
    )

    result = store.read_plugin_config("sample_plugin")
    assert result["parsed"] == {"alpha": 5, "nested": {"x": True}}


def test_invalid_yaml_is_rejected(plugin_config_dir):
    with pytest.raises(PluginConfigStoreError):
        store.write_plugin_config("sample_plugin", raw_yaml="alpha: [unclosed")


def test_non_dict_root_is_rejected(plugin_config_dir):
    with pytest.raises(PluginConfigStoreError):
        store.write_plugin_config("sample_plugin", raw_yaml="- just\n- a\n- list\n")


def test_invalid_plugin_id_is_rejected(plugin_config_dir):
    with pytest.raises(PluginConfigStoreError):
        store.read_plugin_config("../escape")

    with pytest.raises(PluginConfigStoreError):
        store.write_plugin_config("bad/id", raw_yaml="alpha: 1\n")


def test_write_requires_exactly_one_input(plugin_config_dir):
    with pytest.raises(PluginConfigStoreError):
        store.write_plugin_config("sample_plugin")

    with pytest.raises(PluginConfigStoreError):
        store.write_plugin_config(
            "sample_plugin",
            raw_yaml="alpha: 1\n",
            parsed={"alpha": 1},
        )
