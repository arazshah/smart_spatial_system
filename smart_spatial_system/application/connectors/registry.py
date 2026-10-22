"""
smart_spatial_system.application.connectors.registry

Indexes capabilities that declare a ``connector`` metadata block and runs them
with parameter validation, credential policy, redacted errors and a normalized
result.
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from orchestrator.provider_error_mapping import redact_provider_error_message

from .result import KINDS, ConnectorResult, normalize

# Same vocabulary as smart_spatial_system.interfaces.mcp.config.ALL_RISKS (a test keeps them equal).
ACCESS_CLASSES = frozenset({"filesystem", "network", "database", "sql"})
CREDENTIAL_MODES = frozenset({"none", "profile"})
_SOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

# Connection details that must come from a server-side profile, not from a caller.
CLIENT_CONNECTION_PARAMS = frozenset(
    {"dsn", "connection_string", "host", "port", "database", "user", "password", "passwd",
     "token", "api_key"}
)


class ConnectorError(RuntimeError):
    """A connector call failed. The message is safe to show to callers (redacted)."""

    def __init__(self, message: str, *, code: str = "fetch_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ConnectorSpec:
    capability: str
    source_type: str
    kind: str
    access: frozenset[str]
    credentials: str
    limit_param: str | None
    description: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    params: frozenset[str] = field(default_factory=frozenset)

    @property
    def supports_preview(self) -> bool:
        return self.limit_param is not None

    def describe(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "source_type": self.source_type,
            "kind": self.kind,
            "access": sorted(self.access),
            "credentials": self.credentials,
            "supports_preview": self.supports_preview,
            "description": self.description,
            "required_inputs": list(self.required),
            "optional_inputs": [p for p in self.optional if p not in CLIENT_CONNECTION_PARAMS],
        }


def parse_block(capability: str, descriptor: Any, handler: Any) -> ConnectorSpec:
    block = (getattr(descriptor, "metadata", None) or {}).get("connector")
    if not isinstance(block, dict):
        raise ConnectorError(f"{capability}: no connector block.", code="invalid_connector")

    def bad(reason: str) -> ConnectorError:
        return ConnectorError(f"{capability}: invalid connector block - {reason}", code="invalid_connector")

    source_type = block.get("source_type")
    if not isinstance(source_type, str) or not _SOURCE_TYPE_RE.match(source_type):
        raise bad("source_type must be a lowercase identifier")
    kind = block.get("kind")
    if kind not in KINDS:
        raise bad(f"kind must be one of {KINDS}")
    access = block.get("access", [])
    if not isinstance(access, list) or not set(access) <= ACCESS_CLASSES:
        raise bad(f"access must be a list within {sorted(ACCESS_CLASSES)}")
    credentials = block.get("credentials", "none")
    if credentials not in CREDENTIAL_MODES:
        raise bad(f"credentials must be one of {sorted(CREDENTIAL_MODES)} (never 'client')")
    required = tuple(getattr(descriptor, "required_inputs", []) or [])
    optional = tuple(getattr(descriptor, "optional_inputs", []) or [])
    signature = inspect.signature(handler)
    limit_param = block.get("limit_param")
    if limit_param is not None and limit_param not in signature.parameters:
        raise bad(f"limit_param '{limit_param}' is not a parameter of the capability")
    if inspect.iscoroutinefunction(handler):
        raise bad("connectors must be synchronous")
    return ConnectorSpec(
        capability=capability,
        source_type=source_type,
        kind=kind,
        access=frozenset(access),
        credentials=credentials,
        limit_param=limit_param,
        description=str(getattr(descriptor, "description", "") or capability),
        required=required,
        optional=optional,
        params=frozenset(signature.parameters),
    )


class ConnectorRegistry:
    def __init__(self, registry: Any, specs: dict[str, ConnectorSpec],
                 *, allow_client_connection: bool = False) -> None:
        self._registry = registry
        self._specs = specs
        self.allow_client_connection = allow_client_connection

    @classmethod
    def from_capability_registry(
        cls, registry: Any, *, allow_client_connection: bool = False
    ) -> ConnectorRegistry:
        specs: dict[str, ConnectorSpec] = {}
        for name in sorted(registry.registered_capability_names()):
            descriptor = registry.descriptor_for(name)
            if "connector" not in (getattr(descriptor, "metadata", None) or {}):
                continue
            specs[name] = parse_block(name, descriptor, registry.resolve(name).callable)
        return cls(registry, specs, allow_client_connection=allow_client_connection)

    def list(self) -> list[ConnectorSpec]:
        return list(self._specs.values())

    def get(self, capability: str) -> ConnectorSpec:
        try:
            return self._specs[capability]
        except KeyError:
            raise ConnectorError(f"Unknown connector: {capability}", code="unknown_connector") from None

    def by_source_type(self, source_type: str) -> list[ConnectorSpec]:
        return [s for s in self._specs.values() if s.source_type == source_type]

    def describe(self) -> list[dict[str, Any]]:
        return [spec.describe() for spec in self._specs.values()]

    def validate_params(self, spec: ConnectorSpec, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise ConnectorError("Connector parameters must be an object.", code="invalid_params")
        unknown = set(params) - spec.params
        if unknown:
            raise ConnectorError(f"Unknown parameter(s): {sorted(unknown)}.", code="invalid_params")
        missing = [p for p in spec.required if params.get(p) in (None, "")]
        if missing:
            raise ConnectorError(f"Missing required parameter(s): {missing}.", code="invalid_params")
        if not self.allow_client_connection:
            supplied = sorted(p for p in params if p in CLIENT_CONNECTION_PARAMS and params[p] is not None)
            if supplied:
                raise ConnectorError(
                    f"{supplied} must come from a server-side profile; pass 'profile' instead "
                    "(or enable allow_client_connection in the plugin config).",
                    code="policy",
                )
        return dict(params)

    def fetch(self, capability: str, params: dict[str, Any] | None = None,
              *, row_limit: int | None = None) -> ConnectorResult:
        spec = self.get(capability)
        call = self.validate_params(spec, dict(params or {}))
        if row_limit is not None and spec.limit_param:
            current = call.get(spec.limit_param)
            call[spec.limit_param] = row_limit if not isinstance(current, int) else min(current, row_limit)
        handler = self._registry.resolve(capability).callable
        started = time.monotonic()
        try:
            raw = handler(**call)
        except ConnectorError:
            raise
        except Exception as exc:
            message = redact_provider_error_message(f"{type(exc).__name__}: {exc}")
            raise ConnectorError(f"{spec.source_type} fetch failed: {message}"[:500]) from None
        limit = call.get(spec.limit_param) if spec.limit_param else None
        try:
            return normalize(
                raw,
                kind=spec.kind,
                source_type=spec.source_type,
                capability=capability,
                params=call,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                limit=limit if isinstance(limit, int) else None,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        except TypeError as exc:
            raise ConnectorError(str(exc), code="invalid_result") from None

    def preview(self, capability: str, params: dict[str, Any] | None = None,
                *, limit: int = 20) -> dict[str, Any]:
        """Bounded sample: at most ``limit`` rows, plus the schema/summary."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ConnectorError("preview limit must be an integer between 1 and 1000.",
                                 code="invalid_params")
        spec = self.get(capability)
        if not spec.supports_preview:
            raise ConnectorError(f"{capability} does not support preview.", code="invalid_params")
        result = self.fetch(capability, params, row_limit=limit)
        return result.to_dict(include_data=True, max_features=limit)
