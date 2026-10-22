"""
smart_spatial_system.interfaces.mcp.config

Operator-facing MCP settings, read from the environment. Every default is the
restrictive one: no sensitive capability class enabled, bounded request and
response sizes, bounded runtime and concurrency.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

ENV_PREFIX = "SMART_SPATIAL_MCP_"

RISK_FILESYSTEM = "filesystem"
RISK_NETWORK = "network"
RISK_DATABASE = "database"
RISK_SQL = "sql"
ALL_RISKS = frozenset({RISK_FILESYSTEM, RISK_NETWORK, RISK_DATABASE, RISK_SQL})


class McpConfigError(ValueError):
    """Raised for an unsafe or inconsistent MCP configuration (fail closed)."""


def _split(value: str | None, sep: str = ",") -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(sep) if part.strip())


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(ENV_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise McpConfigError(f"{ENV_PREFIX}{name} must be an integer.") from exc
    if value < minimum:
        raise McpConfigError(f"{ENV_PREFIX}{name} must be >= {minimum}.")
    return value


@dataclass(frozen=True)
class McpSettings:
    enabled_risks: frozenset[str] = frozenset()
    allowed_roots: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    allow_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()
    max_input_bytes: int = 5_000_000
    max_output_bytes: int = 5_000_000
    timeout_seconds: int = 60
    max_concurrency: int = 4
    artifacts_enabled: bool = True
    artifacts_dir: str | None = None
    artifact_ttl_seconds: int = 3600
    max_artifact_bytes: int = 50_000_000
    max_artifact_total_bytes: int = 1_000_000_000
    inline_output_bytes: int = 262_144
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        unknown = set(self.enabled_risks) - ALL_RISKS
        if unknown:
            raise McpConfigError(
                f"Unknown risk class(es) {sorted(unknown)}; valid: {sorted(ALL_RISKS)}."
            )
        if RISK_FILESYSTEM in self.enabled_risks and not self.allowed_roots:
            raise McpConfigError(
                "The 'filesystem' risk class needs at least one allowed root "
                f"({ENV_PREFIX}ALLOWED_ROOTS); refusing to allow unrestricted file access."
            )
        if RISK_NETWORK in self.enabled_risks and not self.allowed_hosts:
            raise McpConfigError(
                "The 'network' risk class needs at least one allowed host "
                f"({ENV_PREFIX}ALLOWED_HOSTS); refusing to allow unrestricted outbound requests."
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> McpSettings:
        env = os.environ if env is None else env
        return cls(
            enabled_risks=frozenset(_split(env.get(ENV_PREFIX + "ENABLE"))),
            allowed_roots=_split(env.get(ENV_PREFIX + "ALLOWED_ROOTS"), os.pathsep),
            allowed_hosts=_split(env.get(ENV_PREFIX + "ALLOWED_HOSTS")),
            allow_tools=_split(env.get(ENV_PREFIX + "ALLOW_TOOLS")),
            deny_tools=_split(env.get(ENV_PREFIX + "DENY_TOOLS")),
            max_input_bytes=_int(env, "MAX_INPUT_BYTES", 5_000_000),
            max_output_bytes=_int(env, "MAX_OUTPUT_BYTES", 5_000_000),
            timeout_seconds=_int(env, "TIMEOUT_SECONDS", 60),
            max_concurrency=_int(env, "MAX_CONCURRENCY", 4),
            artifacts_enabled=env.get(ENV_PREFIX + "ARTIFACTS", "on").strip().lower()
            not in {"off", "0", "false", "no"},
            artifacts_dir=env.get(ENV_PREFIX + "ARTIFACTS_DIR") or None,
            artifact_ttl_seconds=_int(env, "ARTIFACT_TTL_SECONDS", 3600),
            max_artifact_bytes=_int(env, "MAX_ARTIFACT_BYTES", 50_000_000),
            max_artifact_total_bytes=_int(env, "MAX_ARTIFACT_TOTAL_BYTES", 1_000_000_000),
            inline_output_bytes=_int(env, "INLINE_OUTPUT_BYTES", 262_144),
            api_key=env.get(ENV_PREFIX + "API_KEY") or env.get("SMART_SPATIAL_API_KEY") or None,
        )
