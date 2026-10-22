"""HTTP-level tests for the connector endpoints and their security defaults."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import plugins.geojson_url_loader as url_loader
from api.main import create_app
from orchestrator.service import (
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
)

COLLECTION = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"n": i}, "geometry": {"type": "Point", "coordinates": [51 + i, 35]}}
    for i in range(6)]}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/")
            self.end_headers()
            return
        body = json.dumps(COLLECTION).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/geo+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def origin():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
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
    return TestClient(create_app(service=service))


@pytest.fixture
def trusted_loopback(monkeypatch):
    """The deployer allows the loopback test server for geojson_url_loader."""
    monkeypatch.setattr(url_loader, "load_config", lambda _id: {"allow_private_network": True})


# --------------------------------------------------------------- discovery
def test_connector_listing_marks_what_the_api_may_drive(client):
    body = client.get("/connectors").json()
    by_name = {c["capability"]: c for c in body["connectors"]}
    assert by_name["fetch_wfs_features"]["available"] is True
    assert by_name["load_geojson_url"]["available"] is True
    assert by_name["load_local_vector"]["available"] is False  # filesystem
    assert by_name["fetch_postgis_sql_layer"]["available"] is False  # raw SQL
    assert sorted(body["http_access"]) == ["database", "network"]
    for connector in body["connectors"]:  # connection details are never advertised
        assert not {"dsn", "password", "host", "user"} & set(connector["optional_inputs"])


# ---------------------------------------------------------- credential policy
def test_client_supplied_connection_details_are_refused_by_default(client):
    response = client.post("/data-sources/postgis", json={
        "table": "roads", "host": "10.0.0.5", "database": "gis", "user": "postgres",
        "password": "hunter2-secret",
    })
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "profile" in detail and "hunter2-secret" not in detail and "10.0.0.5" not in detail


def test_profile_requests_pass_the_policy_and_reach_plugin_validation(client, monkeypatch):
    monkeypatch.setenv("POSTGIS_PASSWORD", "unused-validation-fails-first")
    response = client.post("/data-sources/postgis", json={
        "table": "roads", "profile": "local",
        "where": "(select 1 from pg_shadow where usename=current_user)=1",
    })
    assert response.status_code == 400
    assert "Unsafe token found in where clause" in response.json()["detail"]


def test_filesystem_and_sql_connectors_are_not_reachable_over_http(client, tmp_path):
    path = tmp_path / "x.geojson"
    path.write_text(json.dumps(COLLECTION))
    for capability, params in (("load_local_vector", {"path": str(path)}),
                               ("load_local_raster", {"path": str(path)}),
                               ("fetch_postgis_sql_layer", {"sql": "select 1", "profile": "local"}),
                               ("fetch_wms_map", {"bbox": [0, 0, 1, 1], "service": "x"})):
        response = client.post("/data-sources/connect", json={"capability": capability, "params": params})
        assert response.status_code == 403, capability
        assert "not available over the API" in response.json()["detail"]
        preview = client.post("/connectors/preview", json={"capability": capability, "params": params})
        assert preview.status_code in {400, 403}


def test_non_connector_capabilities_cannot_be_driven(client):
    response = client.post("/data-sources/connect", json={"capability": "buffer_vector_features",
                                                          "params": {"features": COLLECTION}})
    assert response.status_code == 404


# ------------------------------------------------------------- /data-sources/url
def test_url_source_blocks_internal_targets(client):
    for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/health",
                "http://localhost/x"):
        response = client.post("/data-sources/url", json={"url": url})
        assert response.status_code == 400, url
        assert "disallowed network address" in response.json()["detail"]


def test_url_source_rejects_caller_headers_and_non_vector(client):
    response = client.post("/data-sources/url", json={
        "url": "https://example.org/x.geojson", "headers": {"Authorization": "Bearer t"}})
    assert response.status_code == 400 and "profile" in response.json()["detail"]
    response = client.post("/data-sources/url", json={"url": "https://example.org/x", "kind": "raster"})
    assert response.status_code == 400


def test_url_source_end_to_end_and_provenance_is_redacted(client, origin, trusted_loopback):
    response = client.post("/data-sources/url", json={
        "url": f"{origin}/layer.geojson?api_key=TOPSECRET&v=1", "display_name": "my layer"})
    assert response.status_code == 200
    body = response.json()
    assert body["source_type"] == "url" and body["feature_count"] == 6
    assert "TOPSECRET" not in json.dumps(body)
    detail = client.get(f"/data-sources/{body['upload_id']}")
    assert detail.status_code == 200 and "TOPSECRET" not in detail.text


def test_url_source_does_not_follow_redirects(client, origin, trusted_loopback):
    response = client.post("/data-sources/url", json={"url": f"{origin}/redirect"})
    assert response.status_code == 400 and "redirect" in response.json()["detail"].lower()


# ------------------------------------------------------- generic connect + preview
def test_generic_connect_by_source_type_and_ambiguity(client, origin, trusted_loopback):
    response = client.post("/data-sources/connect", json={
        "source_type": "geojson_url", "params": {"url": f"{origin}/a.geojson"}, "display_name": "A"})
    assert response.status_code == 200
    body = response.json()
    assert body["feature_count"] == 6 and body["connector"]["source_type"] == "geojson_url"
    assert body["connector"]["bbox"] == [51.0, 35.0, 56.0, 35.0]
    ambiguous = client.post("/data-sources/connect", json={"source_type": "postgis", "params": {}})
    assert ambiguous.status_code == 400
    unknown_param = client.post("/data-sources/connect", json={
        "capability": "load_geojson_url", "params": {"url": f"{origin}/a", "headers": {"X": "1"}}})
    assert unknown_param.status_code == 400 and "Unknown parameter" in unknown_param.json()["detail"]


def test_preview_is_bounded_and_registers_nothing(client, origin, trusted_loopback):
    response = client.post("/connectors/preview", json={
        "capability": "load_geojson_url", "params": {"url": f"{origin}/a.geojson"}, "limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]["features"]) == 2 and body["metadata"]["feature_count"] == 2
    assert client.post("/connectors/preview", json={
        "capability": "load_geojson_url", "params": {"url": f"{origin}/a"}, "limit": 5000}).status_code == 400
