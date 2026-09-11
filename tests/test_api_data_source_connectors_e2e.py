"""
End-to-end HTTP tests for the /data-sources/* connector endpoints.

These exercise the full real wiring - FastAPI router -> OrchestratorService
-> capability resolution -> plugin -> validation - rather than calling the
plugin functions directly, so they catch integration bugs across those
layers. They specifically cover the two connectors hardened against
SSRF/SQL-injection: the request must be rejected with a 4xx before any
external fetch/DB connection is attempted, and a legitimate request must
still work end to end.

Network calls are stubbed (fake `requests` module, stubbed DNS) so this
suite has no dependency on live network/DNS access or a running database -
see plugins/wms_wfs_fetcher.py::_get_requests and plugins/postgis_connector.py
for why validation runs before any connection is opened.

Run:
    pytest tests/test_api_data_source_connectors_e2e.py -v
"""

from __future__ import annotations

import socket
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from api.main import create_app  # noqa: E402
from orchestrator.service import (  # noqa: E402
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
)


def _client(tmp_path: Path) -> TestClient:
    service = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            outputs_path=tmp_path / "outputs",
            uploads_path=tmp_path / "uploads",
            persist_outputs=True,
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )

    app = create_app(service=service)
    return TestClient(app)


@pytest.fixture
def stub_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    example.com only needs to resolve to *some* non-private address for
    _validate_url's SSRF check to allow it through - stub it so this
    doesn't depend on real DNS/network access.
    """
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host in {"example.com", "www.example.com"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _install_fake_requests_geojson(monkeypatch: pytest.MonkeyPatch, feature_collection: dict) -> None:
    fake_module = types.ModuleType("requests")

    class _FakeResponse:
        def __init__(self) -> None:
            self.headers = {"Content-Type": "application/json"}
            self.content = b""
            self.is_redirect = False
            self.is_permanent_redirect = False

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return feature_collection

    def get(url, params=None, timeout=None, allow_redirects=True):
        return _FakeResponse()

    fake_module.get = get
    monkeypatch.setitem(sys.modules, "requests", fake_module)


def test_wfs_source_registration_rejects_ssrf_target(tmp_path: Path) -> None:
    """
    A caller-supplied base_url pointing at an internal/loopback address
    must be rejected before the server ever makes the request, not
    silently fetched or a 500 crash.
    """
    client = _client(tmp_path)

    response = client.post(
        "/data-sources/wfs",
        json={
            "base_url": "http://169.254.169.254/latest/meta-data/",
            "type_name": "workspace:roads",
        },
    )

    assert response.status_code == 400
    assert "disallowed network address" in response.json()["detail"]


def test_wfs_source_registration_success_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_public_dns
) -> None:
    """
    A legitimate WFS registration should flow all the way through:
    router -> service -> capability resolution -> plugin -> validated
    HTTP fetch -> GeoJSON parsed -> saved as a project upload.
    """
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
                "properties": {"name": "A"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.5, 35.6]},
                "properties": {"name": "B"},
            },
        ],
    }
    _install_fake_requests_geojson(monkeypatch, feature_collection)

    client = _client(tmp_path)

    response = client.post(
        "/data-sources/wfs",
        json={
            "base_url": "https://example.com/geoserver/wfs",
            "type_name": "workspace:roads",
            "display_name": "roads_e2e",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_id"].startswith("upl-")

    # The registered source is retrievable through the API like any other
    # upload, and carries the real fetched feature data.
    detail_response = client.get(f"/data-sources/{body['upload_id']}")
    assert detail_response.status_code == 200


def test_postgis_source_registration_rejects_sql_injection_where_clause(
    tmp_path: Path,
) -> None:
    """
    A malicious `where` filter must be rejected before any database
    connection is attempted - no PostGIS instance is needed for this
    request to fail correctly.
    """
    client = _client(tmp_path)

    response = client.post(
        "/data-sources/postgis",
        json={
            "table": "roads",
            "where": "(select 1 from pg_shadow where usename=current_user)=1",
            "host": "localhost",
            "database": "gis",
            "user": "postgres",
            "password": "secret",
        },
    )

    assert response.status_code == 400
    assert "Unsafe token found in where clause" in response.json()["detail"]


def test_postgis_source_registration_rejects_unsafe_table_identifier(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/data-sources/postgis",
        json={
            "table": "roads; DROP TABLE users",
            "host": "localhost",
            "database": "gis",
            "user": "postgres",
            "password": "secret",
        },
    )

    assert response.status_code == 400
