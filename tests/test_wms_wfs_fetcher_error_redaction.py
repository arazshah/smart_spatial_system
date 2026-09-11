"""
Regression tests for REFACTOR_PLAN.md Phase 6, step 2: wms_wfs_fetcher.py's
HTTP failure paths now redact credential-shaped text from error messages,
the same way postgis_connector.py already does via
orchestrator/provider_error_mapping.py (see
docs/PHASE6_SOURCE_ABSTRACTION_PLAN.md).

Narrowly targeted, mirroring tests/test_postgis_connector_percent_escape.py's
style: exercise the exact failure mode, not the whole plugin.

Run:
    pytest tests/test_wms_wfs_fetcher_error_redaction.py -v
"""

from __future__ import annotations

import socket
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.wms_wfs_fetcher import fetch_wfs_features, fetch_wms_map  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_dns_for_example_com(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host in {"example.com", "www.example.com"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _install_fake_requests_that_raises(monkeypatch, exc: Exception) -> None:
    fake_module = types.ModuleType("requests")

    def get(url, params=None, timeout=None, allow_redirects=True):
        raise exc

    fake_module.get = get
    monkeypatch.setitem(sys.modules, "requests", fake_module)


def test_fetch_wfs_features_redacts_credentials_in_url_on_failure(monkeypatch) -> None:
    exc = ConnectionError(
        "Failed to establish connection to "
        "https://admin:s3cr3t-pass@example.com/geoserver/wfs"
    )
    _install_fake_requests_that_raises(monkeypatch, exc)

    with pytest.raises(RuntimeError) as excinfo:
        fetch_wfs_features(
            base_url="https://example.com/geoserver/wfs",
            type_name="workspace:roads",
        )

    message = str(excinfo.value)
    assert "s3cr3t-pass" not in message
    assert "<redacted>" in message
    assert "admin:<redacted>@example.com" in message


def test_fetch_wfs_features_redacts_api_key_query_param_on_failure(monkeypatch) -> None:
    exc = ConnectionError(
        "GET https://example.com/geoserver/wfs?api_key=super-secret-value failed"
    )
    _install_fake_requests_that_raises(monkeypatch, exc)

    with pytest.raises(RuntimeError) as excinfo:
        fetch_wfs_features(
            base_url="https://example.com/geoserver/wfs",
            type_name="workspace:roads",
        )

    message = str(excinfo.value)
    assert "super-secret-value" not in message
    assert "api_key=<redacted>" in message


def test_fetch_wms_map_redacts_credentials_in_url_on_failure(monkeypatch) -> None:
    exc = ConnectionError(
        "Failed to establish connection to "
        "https://admin:s3cr3t-pass@example.com/geoserver/wms"
    )
    _install_fake_requests_that_raises(monkeypatch, exc)

    with pytest.raises(RuntimeError) as excinfo:
        fetch_wms_map(
            base_url="https://example.com/geoserver/wms",
            layer="roads",
            bbox=[50, 35, 52, 36],
        )

    message = str(excinfo.value)
    assert "s3cr3t-pass" not in message
    assert "<redacted>" in message
