"""Pure-Python tests for MCP settings and security policy (no `mcp` package needed)."""

from __future__ import annotations

import pytest

from smart_spatial_system.interfaces.mcp.config import McpConfigError, McpSettings
from smart_spatial_system.interfaces.mcp.policy import (
    PolicyViolation,
    check_path,
    check_url,
    classify,
    is_tool_allowed,
    safe_error_text,
)


def test_defaults_are_restrictive():
    settings = McpSettings.from_env({})
    assert settings.enabled_risks == frozenset()
    assert settings.api_key is None


def test_filesystem_without_roots_is_refused():
    with pytest.raises(McpConfigError):
        McpSettings.from_env({"SMART_SPATIAL_MCP_ENABLE": "filesystem"})


def test_network_without_hosts_is_refused():
    with pytest.raises(McpConfigError):
        McpSettings.from_env({"SMART_SPATIAL_MCP_ENABLE": "network"})


def test_unknown_risk_class_is_refused():
    with pytest.raises(McpConfigError):
        McpSettings.from_env({"SMART_SPATIAL_MCP_ENABLE": "root_shell"})


def test_api_key_falls_back_to_shared_key():
    assert McpSettings.from_env({"SMART_SPATIAL_API_KEY": "k"}).api_key == "k"
    env = {"SMART_SPATIAL_API_KEY": "k", "SMART_SPATIAL_MCP_API_KEY": "m"}
    assert McpSettings.from_env(env).api_key == "m"


def test_classification_explicit_and_by_parameter_name():
    assert classify("load_local_vector", ["path"]) == {"filesystem"}
    assert classify("fetch_postgis_sql_layer", ["sql"]) == {"database", "sql"}
    # A future plugin nobody listed is still caught by its parameter names.
    assert classify("brand_new_tool", ["features", "output_dir"]) == {"filesystem"}
    assert classify("brand_new_tool", ["base_url"]) == {"network"}
    assert classify("brand_new_tool", ["features"], "pdf") == {"filesystem"}
    assert classify("buffer_vector_features", ["features", "distance"]) == frozenset()


def test_tool_allowed_only_when_all_risks_enabled_and_not_denied():
    pure = McpSettings()
    assert is_tool_allowed("x", frozenset(), pure)
    assert not is_tool_allowed("x", frozenset({"network"}), pure)
    assert not is_tool_allowed("x", frozenset(), McpSettings(deny_tools=("x",)))
    assert not is_tool_allowed("x", frozenset(), McpSettings(allow_tools=("y",)))


def test_check_path_confines_to_roots(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.geojson").write_text("{}")
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    settings = McpSettings(enabled_risks=frozenset({"filesystem"}), allowed_roots=(str(root),))

    assert check_path(str(root / "a.geojson"), settings, param="path").endswith("a.geojson")
    with pytest.raises(PolicyViolation):
        check_path(str(outside), settings, param="path")
    with pytest.raises(PolicyViolation):
        check_path(str(root / ".." / "secret.txt"), settings, param="path")
    with pytest.raises(PolicyViolation):
        check_path("/etc/passwd", settings, param="path")
    with pytest.raises(PolicyViolation):
        check_path("a\x00b", settings, param="path")


def test_check_path_rejects_symlink_escape(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("x")
    (root / "link.geojson").symlink_to(target)
    settings = McpSettings(enabled_risks=frozenset({"filesystem"}), allowed_roots=(str(root),))
    with pytest.raises(PolicyViolation):
        check_path(str(root / "link.geojson"), settings, param="path")


def test_check_url_ssrf_guards():
    settings = McpSettings(
        enabled_risks=frozenset({"network"}),
        allowed_hosts=("example.org", "*.maps.example", "127.0.0.1", "10.0.0.5"),
    )
    assert check_url("https://example.org/wfs", settings, param="u", resolve=False)
    assert check_url("https://a.maps.example/x", settings, param="u", resolve=False)
    for bad in (
        "https://evil.test/",  # not allow-listed
        "file:///etc/passwd",
        "ftp://example.org/",
        "https://user:pw@example.org/",
        "http://127.0.0.1/",  # allow-listed but loopback
        "http://10.0.0.5/",  # allow-listed but private
        "",
    ):
        with pytest.raises(PolicyViolation):
            check_url(bad, settings, param="u", resolve=False)


def test_safe_error_text_hides_absolute_paths():
    text = safe_error_text(FileNotFoundError("No such file: /srv/app/var/uploads/secret/a.tif"))
    assert "/srv/app" not in text
    assert "a.tif" in text
    assert len(safe_error_text(ValueError("x" * 5000))) <= 400
