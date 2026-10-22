"""
smart_spatial_system.shared.net_policy

One outbound-network policy for everything that fetches from a URL on behalf of a caller
(WMS/WFS, URL and Overpass connectors, the MCP server): http(s) only, no embedded
credentials, optional host allow-list, only publicly routable addresses, no redirects,
bounded response size and time.

Known residual risk: the hostname is resolved for the check and again by the HTTP client, so
a hostile DNS server could answer differently the second time (DNS rebinding). The allow-list
is the real control for locked-down deployments; the address check stops the common cases
(loopback, RFC1918, link-local/cloud-metadata).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit


class NetworkPolicyError(ValueError):
    """The request was refused by the network policy (message is safe to show)."""


def is_blocked_ip(ip_text: str) -> bool:
    """True if an address is not a safe public-internet target.

    Anything that is not globally routable is refused (private, loopback, link-local incl. the
    169.254.169.254 metadata address, carrier-grade NAT 100.64/10, documentation and benchmark
    ranges, multicast, reserved, unspecified). IPv6 forms that embed an IPv4 address (IPv4-mapped,
    6to4, Teredo) are judged by the embedded IPv4 address.
    """
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = [ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None]
        if any(v4 is not None and is_blocked_ip(str(v4)) for v4 in embedded):
            return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


_SECRET_QUERY_RE = re.compile(r"(?i)^(key|api_?key|token|access_token|auth|password|secret)$")


def redact_url(url: str) -> str:
    """URL without credentials; secret-looking query values replaced by <redacted>."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    query = "&".join(
        f"{pair.split('=', 1)[0]}=<redacted>" if _SECRET_QUERY_RE.match(pair.split("=", 1)[0])
        else pair
        for pair in parts.query.split("&") if pair
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def host_allowed(host: str, patterns: Iterable[str]) -> bool:
    """Exact match, or ``*.example.org`` for any subdomain."""
    host = host.lower()
    for pattern in patterns:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def validate_http_url(
    url: object,
    field_name: str = "url",
    *,
    allow_private_network: bool = False,
    allowed_hosts: Iterable[str] | None = None,
    resolve: bool = True,
) -> str:
    """
    Validate a caller-supplied http(s) URL and return it stripped.

    ``allow_private_network=True`` skips the address check and is only for URLs that come
    from the deployer's own trusted config, never from a request. ``allowed_hosts`` (when
    given and non-empty) additionally restricts the host.
    """
    if not isinstance(url, str) or not url.strip():
        raise NetworkPolicyError(f"{field_name} must be a non-empty string.")
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise NetworkPolicyError(f"{field_name} must be a valid http/https URL.")
    if parsed.username or parsed.password:
        raise NetworkPolicyError(f"{field_name} must not embed credentials.")
    hostname = parsed.hostname
    if not hostname:
        raise NetworkPolicyError(f"{field_name} must include a host.")
    patterns = list(allowed_hosts or [])
    if patterns and not host_allowed(hostname, patterns):
        raise NetworkPolicyError(f"Host '{hostname}' is not in the allowed hosts.")
    if allow_private_network:
        return url
    try:
        addresses = [str(ipaddress.ip_address(hostname))]
    except ValueError:
        addresses = []
    if not addresses and resolve:
        try:
            addresses = sorted({info[4][0] for info in socket.getaddrinfo(hostname, None)})
        except OSError as exc:
            raise NetworkPolicyError(f"{field_name} host could not be resolved: {hostname!r}") from exc
        if not addresses:
            raise NetworkPolicyError(f"{field_name} host could not be resolved: {hostname!r}")
    if any(is_blocked_ip(address) for address in addresses):
        raise NetworkPolicyError(f"{field_name} resolves to a disallowed network address: {hostname!r}.")
    return url


def fetch_limited(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30,
    max_bytes: int = 50_000_000,
    allow_private_network: bool = False,
    allowed_hosts: Iterable[str] | None = None,
    accept_content_types: Iterable[str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """
    GET ``url`` under the policy: validated first, redirects refused, the body is streamed and
    aborted once it exceeds ``max_bytes``. Returns ``(body, {"content_type", "status"})``.
    """
    validate_http_url(
        url, allow_private_network=allow_private_network, allowed_hosts=allowed_hosts
    )
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is a base dependency
        raise RuntimeError("requests is required for outbound fetches.") from exc

    response = requests.get(
        url, params=dict(params or {}), headers=dict(headers or {}), timeout=timeout,
        allow_redirects=False, stream=True,
    )
    try:
        if response.is_redirect or response.is_permanent_redirect:
            raise NetworkPolicyError("The server answered with a redirect, which is not followed.")
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        accepted = [c.lower() for c in (accept_content_types or [])]
        if accepted and content_type not in accepted:
            raise NetworkPolicyError(f"Unexpected content type '{content_type or 'none'}'.")
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise NetworkPolicyError(f"Response is larger than the {max_bytes} byte limit.")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > max_bytes:
                raise NetworkPolicyError(f"Response is larger than the {max_bytes} byte limit.")
            chunks.append(chunk)
        return b"".join(chunks), {"content_type": content_type, "status": response.status_code}
    finally:
        response.close()
