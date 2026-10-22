"""
Who may choose the database a /query talks to.

The workbench and API let a caller attach PostGIS connection details to a query
(``user_context.postgis_connection`` and friends), and the LLM planner could also write
``host``/``dsn``/``password`` into a ``query_database`` step. Either way the server would open a
connection to a host picked by the caller (or by a prompt-injected model): internal network
scanning, credential relay. This module is the single place that decides:

* Connection *details* (dsn, host, port, database, user, password) from a request are refused
  unless the deployer set ``allow_client_connection: true`` in
  ``config/plugins/postgis_connector.yaml``. The default is false.
* A request may always name a server-side ``profile`` (``{"profile": "local"}``); the server
  resolves host/credentials from its own configuration.
* The LLM never chooses connection parameters or the profile: they are removed from the
  generated QuerySpec and only trusted runtime inputs are injected afterwards.

Same flag and semantics as the connector API (docs/CONNECTOR_REGISTRY_DESIGN.md).
"""

from __future__ import annotations

import os
import re
from typing import Any

from orchestrator.plugin_config_store import read_plugin_config

PLUGIN_ID = "postgis_connector"
CONNECTION_KEYS = ("dsn", "host", "port", "database", "user", "password")
DATABASE_OPS = ("query_database", "load_postgis_layer")
_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class ClientConnectionNotAllowed(ValueError):
    """The request carries database connection details but the server does not accept them."""


def _plugin_config() -> dict[str, Any]:
    try:
        return read_plugin_config(PLUGIN_ID).get("parsed") or {}
    except Exception:
        return {}


def client_connection_allowed() -> bool:
    return _plugin_config().get("allow_client_connection") is True


def check_client_connection(connection: dict[str, Any]) -> None:
    """Raise unless the request may carry connection details (a profile name is always fine)."""
    supplied = sorted(k for k in CONNECTION_KEYS if connection.get(k) not in (None, ""))
    if supplied and not client_connection_allowed():
        raise ClientConnectionNotAllowed(
            f"Database connection details {supplied} cannot be supplied with a query; "
            "name a server-side 'profile' instead (or set allow_client_connection: true in "
            "config/plugins/postgis_connector.yaml)."
        )


def scrub_connection_inputs(inputs: dict[str, Any] | None) -> dict[str, Any]:
    """Copy of ``inputs`` without connection details (used when the client may not send them)."""
    return {k: v for k, v in (inputs or {}).items() if k not in CONNECTION_KEYS}


def strip_llm_connection_params(query_spec: Any) -> int:
    """Remove connection parameters and ``profile`` the model wrote into database steps."""
    removed = 0
    for operation in getattr(query_spec, "operations", None) or []:
        if getattr(operation, "op", None) not in DATABASE_OPS:
            continue
        params = getattr(operation, "params", None)
        if not isinstance(params, dict):
            continue
        for key in (*CONNECTION_KEYS, "profile"):
            if key in params:
                del params[key]
                removed += 1
    return removed


def _resolve(value: Any) -> Any:
    if isinstance(value, str):
        match = _ENV_REF_RE.match(value)
        if match:
            return os.environ.get(match.group(1))
    return value


def resolve_profile_connection(profile: str | None) -> dict[str, Any]:
    """Connection settings of a server-side profile (secrets resolved from the environment)."""
    config = _plugin_config()
    name = profile or config.get("default_profile")
    profiles = config.get("profiles") or {}
    if not name or name not in profiles or not isinstance(profiles[name], dict):
        raise ValueError(f"Unknown PostGIS profile: {name!r}.")
    raw = profiles[name]
    resolved = {k: _resolve(v) for k, v in raw.items()}
    if resolved.get("password") in (None, "") and raw.get("password_env"):
        resolved["password"] = os.environ.get(str(raw["password_env"]))
    connection = {k: resolved[k] for k in (*CONNECTION_KEYS, "connect_timeout") if resolved.get(k) not in (None, "")}
    if connection.get("dsn") is None and not connection.get("database"):
        raise ValueError(f"PostGIS profile {name!r} has no database configured.")
    return connection
