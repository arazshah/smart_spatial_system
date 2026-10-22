"""Shared outbound network policy (smart_spatial_system.shared.net_policy)."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from smart_spatial_system.shared.net_policy import (
    NetworkPolicyError,
    fetch_limited,
    host_allowed,
    is_blocked_ip,
    validate_http_url,
)


@pytest.mark.parametrize("ip, blocked", [
    ("127.0.0.1", True), ("10.1.2.3", True), ("192.168.0.9", True), ("169.254.169.254", True),
    ("172.16.0.1", True), ("0.0.0.0", True), ("::1", True), ("fe80::1", True), ("224.0.0.1", True),
    ("93.184.216.34", False), ("8.8.8.8", False), ("not-an-ip", True),
    ("100.64.0.1", True), ("100.127.255.254", True), ("198.18.0.1", True), ("192.0.2.1", True),
    ("::ffff:127.0.0.1", True), ("::ffff:8.8.8.8", False), ("2002:7f00:0001::1", True),
    ("2002:0808:0808::1", True), ("64:ff9b::7f00:1", True), ("2001:db8::1", True), ("fd00::1", True),
])
def test_is_blocked_ip(ip, blocked):
    assert is_blocked_ip(ip) is blocked


def test_host_patterns():
    assert host_allowed("Maps.Example.org", ["maps.example.org"])
    assert host_allowed("a.b.example.org", ["*.example.org"])
    assert not host_allowed("example.org", ["*.example.org"])
    assert not host_allowed("evilexample.org", ["*.example.org"])


@pytest.fixture
def public_dns(monkeypatch):
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host in {"good.example", "api.good.example"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        if host == "rebind.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_validate_http_url(public_dns):
    assert validate_http_url(" https://good.example/wfs ") == "https://good.example/wfs"
    for bad in ["", None, "ftp://good.example/", "file:///etc/passwd", "https://", "good.example",
                "https://user:pw@good.example/", "http://127.0.0.1/", "http://169.254.169.254/latest",
                "https://rebind.example/", "http://[::1]/", "https://nonexistent.invalid/"]:
        with pytest.raises(NetworkPolicyError):
            validate_http_url(bad)
    with pytest.raises(NetworkPolicyError, match="allowed hosts"):
        validate_http_url("https://good.example/", allowed_hosts=["other.example"])
    assert validate_http_url("https://api.good.example/", allowed_hosts=["*.good.example"])
    # trusted deployer config may point at a private service
    assert validate_http_url("http://127.0.0.1:8080/x", allow_private_network=True)
    with pytest.raises(NetworkPolicyError):  # but credentials are never allowed
        validate_http_url("http://u:p@127.0.0.1/", allow_private_network=True)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ok"):
            body = b'{"type":"FeatureCollection","features":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/geo+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/")
            self.end_headers()
        elif self.path.startswith("/big"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(b"x" * 100000)
        elif self.path.startswith("/stream"):  # no Content-Length: chunk until the client stops
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b"2000\r\n" + b"y" * 0x2000 + b"\r\n")
            except OSError:
                pass
        elif self.path.startswith("/html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html></html>")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def get(server, path, **kwargs):
    kwargs.setdefault("allow_private_network", True)
    return fetch_limited(server + path, timeout=5, **kwargs)


def test_fetch_limited_success_and_content_type(server):
    body, meta = get(server, "/ok", accept_content_types=["application/geo+json", "application/json"])
    assert body.startswith(b'{"type"') and meta["content_type"] == "application/geo+json"
    with pytest.raises(NetworkPolicyError, match="content type"):
        get(server, "/html", accept_content_types=["application/json"])


def test_fetch_limited_refuses_redirects_and_oversize(server):
    with pytest.raises(NetworkPolicyError, match="redirect"):
        get(server, "/redirect")
    with pytest.raises(NetworkPolicyError, match="larger"):
        get(server, "/big", max_bytes=1000)  # declared Content-Length
    with pytest.raises(NetworkPolicyError, match="larger"):
        get(server, "/stream", max_bytes=50_000)  # undeclared length, cut off while streaming


def test_fetch_limited_applies_the_url_policy_before_connecting(server):
    with pytest.raises(NetworkPolicyError):
        fetch_limited(server + "/ok", timeout=5)  # loopback without the trusted-config flag
    with pytest.raises(Exception):
        get(server, "/missing")  # HTTP errors surface
