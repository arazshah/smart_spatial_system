"""
smart_spatial_system.interfaces.mcp.policy

Security policy for MCP-exposed capabilities.

Plugin config files default to *no* restriction (e.g. ``allowed_roots: []``),
which is fine for a trusted local caller but not for a remote MCP client. So
this layer never relies on plugin config for safety: it classifies each
capability by risk, hides it unless the operator enabled that class, and
validates every path/URL argument itself before the plugin runs.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

from .config import (
    RISK_DATABASE,
    RISK_FILESYSTEM,
    RISK_NETWORK,
    RISK_SQL,
    McpSettings,
)

# Capabilities known to be sensitive (current registry, 2026-10).
EXPLICIT_RISKS: dict[str, frozenset[str]] = {
    "load_local_vector": frozenset({RISK_FILESYSTEM}),
    "load_local_raster": frozenset({RISK_FILESYSTEM}),
    "export_vector_geojson": frozenset({RISK_FILESYSTEM}),
    "export_raster_copy": frozenset({RISK_FILESYSTEM}),
    "render_pdf": frozenset({RISK_FILESYSTEM}),
    "fetch_wfs_features": frozenset({RISK_NETWORK}),
    "fetch_wms_map": frozenset({RISK_NETWORK}),
    "geocode_place": frozenset({RISK_NETWORK}),
    "reverse_geocode_point": frozenset({RISK_NETWORK}),
    "fetch_postgis_layer": frozenset({RISK_DATABASE}),
    "query_database_postgis": frozenset({RISK_DATABASE}),
    "fetch_postgis_sql_layer": frozenset({RISK_DATABASE, RISK_SQL}),
}

# Capabilities that cannot work (or cannot be made safe) over MCP: never exposed.
#   ndvi_processor: async, takes SDK RasterIn objects, writes output_ndvi.tif to the
#   server's working directory; calculate_ndvi is the supported equivalent.
UNSUPPORTED_TOOLS = frozenset({"ndvi_processor"})

# Parameter-name heuristics so a future plugin that takes a path/URL/DSN is
# classified automatically (fail closed) instead of silently exposed.
FS_PARAMS = frozenset(
    {"path", "paths", "file_path", "output_path", "output_dir", "filename", "template_name"}
)
URL_PARAMS = frozenset({"url", "base_url", "endpoint"})
DB_PARAMS = frozenset({"dsn", "profile", "connection", "connection_string"})
SQL_PARAMS = frozenset({"sql", "query_sql"})
FS_OUTPUT_KINDS = frozenset({"pdf", "file"})

# Never exposed to clients, whatever is enabled: credentials and knobs that
# disable the plugin's own safety checks.
NEVER_EXPOSED = frozenset(
    {
        "dsn",
        "connection_string",
        "password",
        "passwd",
        "token",
        "api_key",
        "secret",
        "credentials",
        "headers",
        "auth",
        "strict_extensions",
        "verify_ssl",
        "verify",
    }
)


class PolicyViolation(ValueError):
    """A request was refused by policy (safe to show to the client)."""


def classify(name: str, params: Iterable[str], output_kind: str | None = None) -> frozenset[str]:
    """Risk classes of a capability, from the explicit table plus parameter names."""
    names = set(params)
    risks: set[str] = set(EXPLICIT_RISKS.get(name, ()))
    if names & FS_PARAMS or output_kind in FS_OUTPUT_KINDS:
        risks.add(RISK_FILESYSTEM)
    if names & URL_PARAMS:
        risks.add(RISK_NETWORK)
    if names & DB_PARAMS:
        risks.add(RISK_DATABASE)
    if names & SQL_PARAMS:
        risks.update({RISK_DATABASE, RISK_SQL})
    return frozenset(risks)


def is_tool_allowed(name: str, risks: frozenset[str], settings: McpSettings) -> bool:
    if name in UNSUPPORTED_TOOLS:
        return False
    if name in settings.deny_tools:
        return False
    if settings.allow_tools and name not in settings.allow_tools:
        return False
    return risks <= settings.enabled_risks


def check_path(value: object, settings: McpSettings, *, param: str) -> str:
    """Resolve ``value`` (symlinks included) and require it under an allowed root."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise PolicyViolation(f"'{param}' must be a non-empty path string.")
    resolved = Path(value).expanduser().resolve()
    for root in settings.allowed_roots:
        root_path = Path(root).expanduser().resolve()
        if resolved == root_path or root_path in resolved.parents:
            return str(resolved)
    raise PolicyViolation(f"'{param}' is outside the allowed roots of this server.")


def _host_allowed(host: str, patterns: Iterable[str]) -> bool:
    host = host.lower()
    for pattern in patterns:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_url(value: object, settings: McpSettings, *, param: str, resolve: bool = True) -> str:
    """Allow only http(s) URLs to allow-listed, publicly routable hosts (SSRF guard)."""
    if not isinstance(value, str) or not value.strip():
        raise PolicyViolation(f"'{param}' must be a non-empty URL string.")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PolicyViolation(f"'{param}' must be an http(s) URL.")
    if parsed.username or parsed.password:
        raise PolicyViolation(f"'{param}' must not embed credentials.")
    host = parsed.hostname
    if not _host_allowed(host, settings.allowed_hosts):
        raise PolicyViolation(f"Host '{host}' is not in this server's allowed hosts.")
    try:
        literal = [str(ipaddress.ip_address(host))]
    except ValueError:
        literal = []
    addresses = literal
    if not literal and resolve:
        try:
            addresses = [info[4][0] for info in socket.getaddrinfo(host, None)]
        except OSError as exc:
            raise PolicyViolation(f"Host '{host}' could not be resolved.") from exc
    if any(not _is_public_ip(address) for address in addresses):
        raise PolicyViolation(f"Host '{host}' resolves to a non-public address.")
    return value


def safe_error_text(exc: BaseException, limit: int = 400) -> str:
    """Exception text for clients: absolute paths reduced to base names, truncated."""
    import re

    text = f"{type(exc).__name__}: {exc}"
    text = re.sub(r"(?<![\w.])/(?:[^\s'\"():,]+/)+([^\s'\"():,]+)", r"<path>/\1", text)
    return text[:limit]


def loopback_host(host: str) -> bool:
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


__all__ = [
    "NEVER_EXPOSED",
    "PolicyViolation",
    "check_path",
    "check_url",
    "classify",
    "is_tool_allowed",
    "loopback_host",
    "safe_error_text",
]
