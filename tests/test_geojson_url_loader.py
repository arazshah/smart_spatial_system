"""geojson_url_loader: connector behaviour and the SSRF / size guards."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import plugins.geojson_url_loader as module
from plugins.geojson_url_loader import load_geojson_url

COLLECTION = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"i": i}, "geometry": {"type": "Point", "coordinates": [i, i]}}
    for i in range(5)]}
seen_headers: dict[str, str] = {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, ctype="application/geo+json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        seen_headers.update({k.lower(): v for k, v in self.headers.items()})
        path = self.path.split("?")[0]
        if path == "/fc":
            self._send(COLLECTION)
        elif path == "/feature":
            self._send(COLLECTION["features"][0], "application/json")
        elif path == "/geometry":
            self._send({"type": "Point", "coordinates": [1, 2]})
        elif path == "/notgeo":
            self._send({"hello": "world"})
        elif path == "/metadata":  # what a cloud metadata service looks like
            self._send({"iam": {"secret": "AKIA..."}}, "application/json")
        elif path == "/html":
            self._send(b"<html></html>", "text/html")
        elif path == "/badjson":
            self._send(b"{not json", "application/json")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def base():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def trusted(monkeypatch):
    """Deployer config that allows the loopback test server."""
    config = {"allow_private_network": True, "max_features": 100}
    monkeypatch.setattr(module, "load_config", lambda _id: config)
    return config


def test_loads_collection_feature_and_geometry(base, trusted):
    out = load_geojson_url(base + "/fc")
    assert len(out.features) == 5 and out.metadata["crs"] == "EPSG:4326"
    assert out.metadata["truncated"] is False and out.metadata["bytes"] > 0
    assert len(load_geojson_url(base + "/feature").features) == 1
    assert load_geojson_url(base + "/geometry").features[0]["geometry"]["type"] == "Point"


def test_max_features_truncates_and_hard_cap_refuses(base, trusted):
    out = load_geojson_url(base + "/fc", max_features=2)
    assert len(out.features) == 2 and out.metadata["truncated"] is True
    trusted["max_features"] = 3
    with pytest.raises(ValueError, match="limit is 3"):
        load_geojson_url(base + "/fc")


def test_bad_documents_are_refused(base, trusted):
    for path, match in [("/notgeo", "not GeoJSON"), ("/html", "content type"),
                        ("/badjson", "valid JSON")]:
        with pytest.raises(ValueError, match=match):
            load_geojson_url(base + path)
    with pytest.raises(RuntimeError, match="Failed to fetch"):
        load_geojson_url(base + "/missing")
    with pytest.raises(ValueError):
        load_geojson_url(base + "/fc", timeout=0)
    with pytest.raises(ValueError):
        load_geojson_url(base + "/fc", max_features=0)


def test_private_targets_are_refused_unless_the_deployer_allows_them(base, monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {})
    for url in (base + "/metadata", "http://169.254.169.254/latest/meta-data/", "http://localhost/x",
                "file:///etc/passwd", "https://user:pw@example.org/x.json"):
        with pytest.raises(ValueError):
            load_geojson_url(url)


def test_allowed_hosts_and_size_limit(base, monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {
        "allow_private_network": True, "allowed_hosts": ["other.example"]})
    with pytest.raises(ValueError, match="allowed hosts"):
        load_geojson_url(base + "/fc")
    monkeypatch.setattr(module, "load_config", lambda _id: {
        "allow_private_network": True, "max_response_bytes": 50})
    with pytest.raises(ValueError, match="larger"):
        load_geojson_url(base + "/fc")


def test_headers_only_come_from_server_profiles(base, trusted, monkeypatch):
    monkeypatch.setattr(module, "get_profile_config",
                        lambda plugin_id, profile, required=False: {"headers": {"X-Token": "from-profile"}})
    seen_headers.clear()
    load_geojson_url(base + "/fc", profile="portal")
    assert seen_headers["x-token"] == "from-profile"
    with pytest.raises(TypeError):  # there is no way to pass headers as an argument
        load_geojson_url(base + "/fc", headers={"X-Token": "evil"})


def test_errors_and_metadata_do_not_leak_secrets(base, trusted):
    out = load_geojson_url(base + "/fc?api_key=SECRET123&x=1")
    assert "SECRET123" not in json.dumps(out.metadata) and "x=1" in out.metadata["url"]
    with pytest.raises(RuntimeError) as err:
        load_geojson_url(base + "/missing?token=SECRET456")
    assert "SECRET456" not in str(err.value)
