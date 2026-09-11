"""
Regression tests for REFACTOR_PLAN.md Phase 6, step 2: geocoding_resolver.py's
provider-chain failure entries now redact credential-shaped text from
error messages, the same way postgis_connector.py already does via
orchestrator/provider_error_mapping.py (see
docs/PHASE6_SOURCE_ABSTRACTION_PLAN.md).

Mirrors tests/test_geocoding_resolver.py's
test_provider_chain_fallback_on_error fixture setup, narrowed to the
redaction concern.

Run:
    pytest tests/test_geocoding_resolver_error_redaction.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.geocoding_resolver import geocode_place, reverse_geocode_point  # noqa: E402


def _write_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "geocoding_resolver.yaml").write_text(
        """
default_provider_chain:
  - broken
  - static
continue_on_provider_error: true
default_limit: 5
coordinate_precision: 6
providers:
  broken:
    type: generic_http_json
    endpoint_url: https://broken.example/geocode
  static:
    type: static
    places:
      tehran:
        display_name: Tehran, Iran
        lon: 51.389
        lat: 35.6892
""",
        encoding="utf-8",
    )
    return config_dir


def test_geocode_place_redacts_credentials_in_provider_error(monkeypatch, tmp_path: Path) -> None:
    config_dir = _write_config(tmp_path)
    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    def fake_urlopen(request, timeout=10):
        raise RuntimeError(
            "Failed to connect to https://admin:s3cr3t-pass@broken.example/geocode"
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = geocode_place(query="tehran")

    assert len(result.metadata["provider_errors"]) == 1
    error_text = result.metadata["provider_errors"][0]["error"]
    assert "s3cr3t-pass" not in error_text
    assert "<redacted>" in error_text


def test_geocode_place_redacts_api_key_query_param_in_provider_error(
    monkeypatch, tmp_path: Path
) -> None:
    config_dir = _write_config(tmp_path)
    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    def fake_urlopen(request, timeout=10):
        raise RuntimeError(
            "GET https://broken.example/geocode?api_key=super-secret-value failed"
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = geocode_place(query="tehran")

    error_text = result.metadata["provider_errors"][0]["error"]
    assert "super-secret-value" not in error_text
    assert "api_key=<redacted>" in error_text


def test_reverse_geocode_point_redacts_credentials_in_provider_error(
    monkeypatch, tmp_path: Path
) -> None:
    config_dir = _write_config(tmp_path)
    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    def fake_urlopen(request, timeout=10):
        raise RuntimeError(
            "Failed to connect to https://admin:s3cr3t-pass@broken.example/reverse"
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = reverse_geocode_point(lon=51.389, lat=35.6892)

    error_text = result.metadata["provider_errors"][0]["error"]
    assert "s3cr3t-pass" not in error_text
    assert "<redacted>" in error_text
