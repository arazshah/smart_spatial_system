"""
orchestrator.service

Service facade for Smart Spatial System.

This module is the operational boundary between:
    - API / Frontend
    - internal orchestration modules

The frontend/API should call this service instead of directly using:
    - routers
    - registries
    - runners
    - production response builder
    - feedback / learning internals

Main responsibilities:
    1. Load plugin registry
    2. Load persisted router weights
    3. Build weighted router
    4. Run natural query pipeline
    5. Build production user response
    6. Keep request/audit history for feedback
    7. Convert feedback into learning signals and weight proposals
"""

from __future__ import annotations
import os

import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path

from orchestrator.runtime_paths import RuntimePaths
from typing import Any

from orchestrator.error_contract import CATEGORY_INTERNAL, exception_to_error


class _EnabledOnlyRegistryView:
    """
    Lightweight registry-like wrapper that exposes only enabled capabilities.

    Compatible with routers that expect a registry object implementing:
      - resolve(capability_name)
      - descriptor_for(capability_name)
      - registered_capability_names()
    """

    def __init__(self, bindings: dict[str, Any], descriptors: dict[str, Any]) -> None:
        self._bindings = dict(bindings or {})
        self._descriptors = dict(descriptors or {})

    def resolve(self, capability_name: str) -> Any:
        if capability_name not in self._bindings:
            raise ValueError(f"Capability '{capability_name}' is not registered.")
        return self._bindings[capability_name]

    def descriptor_for(self, capability_name: str) -> Any:
        if capability_name not in self._descriptors:
            raise ValueError(f"Capability '{capability_name}' has no descriptor.")
        return self._descriptors[capability_name]

    def registered_capability_names(self) -> list[str]:
        return sorted(self._bindings.keys())



class _EnabledOnlyCapabilityRouter:
    """
    Lightweight router wrapper exposing only enabled capability bindings.
    Compatible with plan builders/executors that expect:
      - resolve(name)
      - registered_capability_names()
    """

    def __init__(self, bindings: dict[str, Any]) -> None:
        self._bindings = dict(bindings or {})

    def resolve(self, capability_name: str) -> Any:
        if capability_name not in self._bindings:
            raise ValueError(
                f"Capability '{capability_name}' is not registered in enabled router."
            )
        return self._bindings[capability_name]

    def registered_capability_names(self) -> list[str]:
        return sorted(self._bindings.keys())


from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.plugin_state import (
    PluginStateStore,
    PluginStateStoreConfig,
    PluginStateStoreError,
)
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter
from orchestrator.feedback import FeedbackCollector, UserFeedbackInput
from orchestrator.learning_signals import RouterLearningSignalBuilder
from orchestrator.map_layers import MapLayerBuilder
from orchestrator.input_reference_resolver import (
    UploadReferenceResolver,
    UploadReferenceResolverConfig,
    UploadReferenceResolverError,
)
from orchestrator.data_source_service import DataSourceService, DataSourceServiceError
from orchestrator.map_layer_service import MapLayerService, MapLayerServiceError
from orchestrator.output_service import OutputService, OutputServiceError
from orchestrator.output_storage import (
    OutputStorage,
    OutputStorageConfig,
    OutputStorageError,
)
from orchestrator.project_store import (
    ProjectStore,
    ProjectStoreConfig,
)
from orchestrator.project_service import (
    ProjectService,
    ProjectServiceError,
)
from orchestrator.production_response import (
    ProductionResponseBuilder,
    ProductionResponseConfig,
)
from orchestrator.routing_aware_natural_query_runner import (
    run_natural_query_with_routing_evidence,
)
from orchestrator.upload_service import UploadService, UploadServiceError
from orchestrator.upload_storage import (
    UploadStorage,
    UploadStorageConfig,
    UploadStorageError,
)
from orchestrator.weight_proposals import (
    InMemoryRouterWeightStore,
    RouterWeightProposalCollector,
    RouterWeightProposalEngine,
    WeightProposal,
    WeightStoreConfig,
)
from orchestrator.weight_store_persistence import (
    RouterWeightStorePersistence,
    WeightStorePersistenceConfig,
    WeightStorePersistenceError,
)
from orchestrator.weighted_router import WeightedCapabilityRouter, WeightedRouterConfig
from orchestrator.planning.dag_executor import DagExecutionError, DagValidationError
from orchestrator.planning.llm_spec_generator import (
    LLMQuerySpecGenerator,
    LLMSpecGenerationError,
    OpenAICompatibleLLMClient,
    query_spec_to_dict,
)
from orchestrator.planning.planner import PlanningError
from orchestrator.planning.runner import make_registry_planning_runner
from orchestrator.planning.postgis_semantic_resolver import (
    ColumnInfo,
    PostGISSchemaContext,
    PostGISTableInfo,
    discover_postgis_schema,
)
from orchestrator.planning.semantic_planning_context import (
    build_semantic_planning_context,
)


def _first_mapping_value(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return value
    return None


def _coerce_postgis_schema_context(value: Any) -> PostGISSchemaContext | None:
    """
    Convert a JSON-friendly schema context into PostGISSchemaContext.

    Accepted shape:
      {
        "tables": [
          {
            "schema": "public",
            "table": "planet_osm_point",
            "geom_col": "way",
            "geometry_type": "POINT",
            "srid": 3857,
            "estimated_rows": 1000,
            "columns": [
              {"name": "osm_id", "data_type": "bigint", "udt_name": "int8"},
              ...
            ]
          }
        ]
      }

    If value is already PostGISSchemaContext, it is returned as-is.
    """
    if isinstance(value, PostGISSchemaContext):
        return value

    if not isinstance(value, dict):
        return None

    raw_tables = value.get("tables")
    if not isinstance(raw_tables, list):
        return None

    tables: list[PostGISTableInfo] = []

    for raw_table in raw_tables:
        if not isinstance(raw_table, dict):
            continue

        schema = str(raw_table.get("schema") or "").strip()
        table = str(raw_table.get("table") or "").strip()
        geom_col = str(raw_table.get("geom_col") or raw_table.get("geometry_column") or "").strip()

        if not schema or not table or not geom_col:
            continue

        raw_columns = raw_table.get("columns") or []
        columns: list[ColumnInfo] = []

        if isinstance(raw_columns, list):
            for raw_col in raw_columns:
                if isinstance(raw_col, dict):
                    name = str(raw_col.get("name") or "").strip()
                    if not name:
                        continue
                    columns.append(
                        ColumnInfo(
                            name=name,
                            data_type=str(raw_col.get("data_type") or ""),
                            udt_name=str(raw_col.get("udt_name") or ""),
                        )
                    )
                elif isinstance(raw_col, str) and raw_col.strip():
                    columns.append(ColumnInfo(name=raw_col.strip()))

        srid_value = raw_table.get("srid")
        try:
            srid = int(srid_value) if srid_value is not None else None
        except Exception:
            srid = None

        estimated_rows_value = raw_table.get("estimated_rows")
        try:
            estimated_rows = (
                int(estimated_rows_value)
                if estimated_rows_value is not None
                else None
            )
        except Exception:
            estimated_rows = None

        tables.append(
            PostGISTableInfo(
                schema=schema,
                table=table,
                geom_col=geom_col,
                geometry_type=str(raw_table.get("geometry_type") or ""),
                srid=srid,
                columns=tuple(columns),
                estimated_rows=estimated_rows,
            )
        )

    if not tables:
        return None

    return PostGISSchemaContext(tables=tuple(tables))


_POSTGIS_CONNECTION_KEYS = (
    "postgis_connection",
    "postgis",
    "database_connection",
    "db_connection",
    "connection",
)


def _looks_like_postgis_connection(value: Any) -> bool:
    if not isinstance(value, dict):
        return False

    lowered = {
        str(k).lower(): v
        for k, v in value.items()
    }

    for key in ("source_type", "type", "driver", "dialect", "engine"):
        raw = lowered.get(key)
        if raw is not None and str(raw).strip().lower() in {
            "postgis",
            "postgres",
            "postgresql",
        }:
            return True

    if lowered.get("dsn"):
        dsn = str(lowered.get("dsn") or "").lower()
        if "postgres" in dsn or "postgis" in dsn or "dbname=" in dsn:
            return True

    has_database = any(k in lowered for k in ("database", "dbname", "db_name"))
    has_host = "host" in lowered or "hostname" in lowered
    has_user = "user" in lowered or "username" in lowered

    # Host+database is usually enough for explicit postgis_connection containers.
    return bool(has_database and (has_host or has_user))


def _normalize_postgis_connection_config(value: Any) -> dict[str, Any] | None:
    """
    Normalize a PostGIS/PostgreSQL connection config.

    Returns a safe internal dict with only connection-related fields:
      dsn, host, port, database, user, password, connect_timeout, schemas

    It intentionally ignores arbitrary extra fields.
    """
    if not _looks_like_postgis_connection(value):
        return None

    raw = dict(value)

    def pick(*names: str) -> Any:
        for name in names:
            if name in raw and raw.get(name) not in (None, ""):
                return raw.get(name)
        return None

    config: dict[str, Any] = {}

    dsn = pick("dsn", "url", "uri")
    if dsn:
        config["dsn"] = str(dsn)

    host = pick("host", "hostname")
    if host:
        config["host"] = str(host)

    port = pick("port")
    if port not in (None, ""):
        try:
            config["port"] = int(port)
        except Exception:
            config["port"] = str(port)

    database = pick("database", "dbname", "db_name")
    if database:
        config["database"] = str(database)

    user = pick("user", "username")
    if user:
        config["user"] = str(user)

    password = pick("password", "pass")
    if password:
        config["password"] = str(password)

    timeout = pick("connect_timeout", "timeout")
    if timeout not in (None, ""):
        try:
            config["connect_timeout"] = int(timeout)
        except Exception:
            config["connect_timeout"] = timeout

    schemas = pick("schemas", "schema")
    if schemas:
        if isinstance(schemas, str):
            config["schemas"] = [schemas]
        elif isinstance(schemas, (list, tuple, set)):
            config["schemas"] = [str(s) for s in schemas if str(s).strip()]

    if not config.get("dsn") and not config.get("database"):
        return None

    return config


def _extract_postgis_connection_config_from_sources(
    *,
    resolved_inputs: dict[str, Any] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Find PostGIS connection configuration in known request/context locations.

    Priority:
      user_context → metadata → resolved_inputs
    """
    sources = (
        user_context or {},
        metadata or {},
        resolved_inputs or {},
    )

    # First check known container keys.
    for source in sources:
        if not isinstance(source, dict):
            continue

        for key in _POSTGIS_CONNECTION_KEYS:
            candidate = source.get(key)
            normalized = _normalize_postgis_connection_config(candidate)
            if normalized is not None:
                return normalized

    # Then allow the source object itself to be a connection config.
    for source in sources:
        normalized = _normalize_postgis_connection_config(source)
        if normalized is not None:
            return normalized

    return None


def _discover_postgis_schema_context_from_connection_config(
    connection_config: dict[str, Any],
) -> PostGISSchemaContext:
    """
    Open a psycopg2 connection and discover PostGIS schema.

    The caller receives exceptions; higher-level planning should handle them
    non-fatally.
    """
    import psycopg2

    schemas = connection_config.get("schemas") or None

    connect_timeout = connection_config.get("connect_timeout", 5)

    if connection_config.get("dsn"):
        conn = psycopg2.connect(
            connection_config["dsn"],
            connect_timeout=connect_timeout,
        )
    else:
        kwargs: dict[str, Any] = {
            "host": connection_config.get("host"),
            "port": connection_config.get("port"),
            "dbname": connection_config.get("database"),
            "user": connection_config.get("user"),
            "password": connection_config.get("password"),
            "connect_timeout": connect_timeout,
        }
        kwargs = {k: v for k, v in kwargs.items() if v not in (None, "")}
        conn = psycopg2.connect(**kwargs)

    try:
        return discover_postgis_schema(
            conn,
            schemas=schemas,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _build_query_spec_runtime_inputs(
    *,
    resolved_inputs: dict[str, Any] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """
    Build runtime inputs used to enrich executable QuerySpec operation params.

    Important:
    LLM/semantic planning creates safe logical query_database params:
      schema/table/columns/geom_col/where

    Runtime execution also needs connection params:
      host/port/database/user/password/connect_timeout/dsn/profile

    UI often sends those params nested under:
      user_context.postgis_connection
      user_context.postgis
      metadata.postgis_connection
      resolved_inputs.postgis_connection

    `_enrich_query_database_params_from_inputs` expects top-level keys, so this
    helper flattens the normalized connection config into runtime_inputs.

    Password is included only in runtime inputs for execution; response metadata
    must continue using redaction helpers.
    """
    runtime_inputs: dict[str, Any] = dict(resolved_inputs or {})

    connection_config = _extract_postgis_connection_config_from_sources(
        resolved_inputs=resolved_inputs,
        user_context=user_context,
        metadata=metadata,
    )

    if connection_config is None:
        return runtime_inputs, False

    runtime_inputs.update(connection_config)
    runtime_inputs.setdefault("postgis_connection", connection_config)
    runtime_inputs.setdefault("database_connection", connection_config)

    return runtime_inputs, True


def _extract_semantic_planning_context_from_sources(
    *,
    query: str,
    resolved_inputs: dict[str, Any] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Build or forward semantic_planning_context for LLM planning.

    Priority:
    1. Existing semantic_planning_context from user_context/metadata/resolved_inputs.
    2. Build from postgis_schema_context if provided.

    This function is intentionally non-fatal: errors are returned as strings and
    must not break the existing planning path.
    """
    try:
        user_context = user_context or {}
        metadata = metadata or {}
        resolved_inputs = resolved_inputs or {}

        existing = _first_mapping_value(
            user_context.get("semantic_planning_context"),
            metadata.get("semantic_planning_context"),
            resolved_inputs.get("semantic_planning_context"),
        )
        if existing is not None:
            return existing, None

        schema_source = _first_mapping_value(
            user_context.get("postgis_schema_context"),
            user_context.get("postgis_schema"),
            metadata.get("postgis_schema_context"),
            metadata.get("postgis_schema"),
            resolved_inputs.get("postgis_schema_context"),
            resolved_inputs.get("postgis_schema"),
        )

        schema_context = _coerce_postgis_schema_context(schema_source)

        if schema_context is None:
            connection_config = _extract_postgis_connection_config_from_sources(
                resolved_inputs=resolved_inputs,
                user_context=user_context,
                metadata=metadata,
            )

            if connection_config is None:
                return None, None

            schema_context = _discover_postgis_schema_context_from_connection_config(
                connection_config
            )

        explicit_concepts = (
            user_context.get("semantic_concepts")
            or metadata.get("semantic_concepts")
            or resolved_inputs.get("semantic_concepts")
            or None
        )

        context = build_semantic_planning_context(
            query,
            schema_context,
            explicit_concepts=explicit_concepts,
        )

        return context.to_dict(), None

    except Exception as exc:
        return None, str(exc)


def _is_feature_collection(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") == "FeatureCollection"
        and isinstance(value.get("features"), list)
    )


_SENSITIVE_METADATA_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
}


def _redact_sensitive_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in _SENSITIVE_METADATA_KEYS or any(
                marker in key_text
                for marker in ("password", "secret", "token", "api_key", "apikey")
            ):
                redacted[key] = "***"
            else:
                redacted[key] = _redact_sensitive_json(item)
        return redacted

    if isinstance(value, list):
        return [_redact_sensitive_json(item) for item in value]

    return value


DEFAULT_SAFE_PLUGIN_MODULES = [
    "plugins.spectral_indices",
    "plugins.raster_threshold",
    "plugins.raster_to_vector",
    "plugins.core_vector",
    "plugins.spatial_predicate",
    "plugins.feature_scoring",
    "plugins.feature_enrichment",
    "plugins.risk_enrichment",
    "plugins.report_builder",
    "plugins.pdf_renderer",
    "plugins.ndvi_calculator",
    "plugins.ndvi_analysis",
    "plugins.raster_statistics",
    "plugins.raster_reclassify",
    "plugins.band_math",
    "plugins.raster_clip_mask",
    "plugins.slope_aspect",
    "plugins.zonal_statistics",
    "plugins.buffer_analysis",
    "plugins.centroid_extractor",
    "plugins.geometry_validator",
    "plugins.spatial_query_filter",
    "plugins.spatial_intersection",
    "plugins.spatial_join",
    "plugins.nearest_neighbor",
    "plugins.distance_calculator",
    "plugins.area_perimeter_calc",
    "plugins.dissolve_aggregator",
    "plugins.attribute_statistics",
    "plugins.crs_transformer",
    "plugins.data_writer_exporter",
    "plugins.local_vector_loader",
    "plugins.postgis_connector",
]


@dataclass(frozen=True)
class OrchestratorServiceConfig:
    """
    Configuration for OrchestratorService.
    """

    plugin_modules: list[str] = field(default_factory=lambda: list(DEFAULT_SAFE_PLUGIN_MODULES))

    use_weighted_router: bool = True
    load_persisted_weights: bool = True
    weights_path: str | Path = "weights/router_weights.json"

    # Runtime root for generated local state.
    # If outputs/uploads/projects paths are not provided explicitly, they are
    # resolved from RuntimePaths using this value, SMART_SPATIAL_RUNTIME_DIR, or
    # the default runtime root.
    runtime_dir: str | Path | None = None
    outputs_path: str | Path | None = None
    uploads_path: str | Path | None = None
    projects_path: str | Path | None = None
    resolve_upload_refs_with_plugins: bool = True
    raster_loader_plugin_module: str = "plugins.local_raster_loader"
    vector_loader_plugin_module: str = "plugins.local_vector_loader"
    enforce_loader_contract: bool = True
    allow_adaptive_loader_fallback: bool = True
    persist_outputs: bool = True

    default_weight: float = 1.0
    min_weight: float = 0.0
    max_weight: float = 3.0

    min_score: float = 0.01

    response_language: str = "fa"

    # Experimental opt-in: execute QuerySpec plans through the
    # geochat_kernel execution bridge in addition to the current DAG path.
    # Default is False to keep production behavior unchanged.
    enable_kernel_execution: bool = False

    # Phase 4 hardening:
    # When False (default), request-level metadata may DISABLE kernel execution
    # but may NOT enable it. This prevents arbitrary callers from turning on the
    # experimental kernel path. When True, request metadata may also enable it.
    allow_request_kernel_execution: bool = False

    include_response_debug: bool = False

    keep_history: bool = True
    max_history_items: int = 1000

    auto_save_weights_after_apply: bool = True

    def __post_init__(self) -> None:
        if not self.plugin_modules:
            raise ValueError("plugin_modules must not be empty.")

        if self.min_score < 0:
            raise ValueError("min_score must be >= 0.")

        if self.max_history_items < 0:
            raise ValueError("max_history_items must be >= 0.")

        if self.default_weight < 0:
            raise ValueError("default_weight must be >= 0.")

        if self.min_weight < 0:
            raise ValueError("min_weight must be >= 0.")

        if self.max_weight < self.min_weight:
            raise ValueError("max_weight must be >= min_weight.")

        if self.response_language not in {"fa", "en"}:
            raise ValueError("response_language must be one of: fa, en.")


class OrchestratorServiceError(RuntimeError):
    """
    Service-level error.

    The legacy message remains unchanged; structured_error is additive.
    """

    def __init__(
        self,
        message: str,
        *,
        structured_error: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.structured_error = structured_error


def _service_exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)

        if isinstance(cause, BaseException):
            current = cause
        elif isinstance(context, BaseException):
            current = context
        else:
            current = None

    return chain


def _find_structured_error_in_exception_chain(
    exc: BaseException,
) -> dict[str, Any] | None:
    for item in _service_exception_chain(exc):
        structured_error = getattr(item, "structured_error", None)

        if isinstance(structured_error, dict):
            return structured_error

    return None


def _service_exception_to_structured_error(
    exc: BaseException,
    *,
    stage: str | None = None,
    source: str = "orchestrator_service",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Preserve an existing structured_error from the exception chain when present.
    Otherwise create a generic service-level structured error.
    """
    existing = _find_structured_error_in_exception_chain(exc)

    if isinstance(existing, dict):
        payload = dict(existing)
        payload_details = dict(payload.get("details") or {})

        if stage is not None:
            payload_details.setdefault("service_stage", stage)

        if details:
            payload_details.update(details)

        payload["details"] = payload_details
        return payload

    merged_details: dict[str, Any] = {
        "exception_chain": [
            {
                "type": type(item).__name__,
                "message": str(item) or type(item).__name__,
            }
            for item in _service_exception_chain(exc)
        ],
    }

    if stage is not None:
        merged_details["stage"] = stage

    if details:
        merged_details.update(details)

    return exception_to_error(
        exc,
        code="service.unexpected_exception",
        category=CATEGORY_INTERNAL,
        retryable=False,
        source=source,
        details=merged_details,
    ).to_dict()


def _service_error_from_exception(
    exc: BaseException,
    *,
    stage: str | None = None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> OrchestratorServiceError:
    final_message = str(exc) if message is None else message

    return OrchestratorServiceError(
        final_message,
        structured_error=_service_exception_to_structured_error(
            exc,
            stage=stage,
            details=details,
        ),
    )



def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())

    if is_dataclass(value):
        return _json_safe(asdict(value))

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict) and payload:
        return _json_safe(payload)

    return repr(value)

class QueryExecutionServiceError(RuntimeError):
    """Raised when a query execution service operation fails."""


class QueryExecutionService:
    """Application service boundary for query execution operations."""

    def __init__(self, context: Any) -> None:
        if context is None:
            raise QueryExecutionServiceError("Orchestrator context dependency is required.")
        self._context = context

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    @staticmethod
    def _llm_planning_enabled() -> bool:
        """
        Whether LLM-based intent planning is enabled for /query.
        """
        import os

        value = os.getenv("LLM_PLANNING_ENABLED", "false").strip().lower()
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def _query_spec_planning_enabled() -> bool:
        """
        Whether QuerySpec-based planning is enabled for /query.

        This is separate from legacy LLM intent planning.
        """
        import os

        value = os.getenv("QUERY_SPEC_PLANNING_ENABLED", "false").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _kernel_execution_enabled(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        final_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Return whether experimental kernel execution should be enabled for
        QuerySpec planning.

        Phase 4 hardening precedence:

          1. If a request explicitly DISABLES kernel execution, it is disabled.
             A request may always disable it for safety.

          2. If a request explicitly ENABLES kernel execution, it is enabled
             ONLY when the service allows request-level enabling
             (config.allow_request_kernel_execution is True). Otherwise the
             request enable flag is ignored.

          3. Otherwise, a deployment-level environment variable is honored.

          4. Otherwise, the service config default
             (config.enable_kernel_execution) is used.

          5. Otherwise it defaults to False.

        Accepted truthy values:
          true, 1, yes, y, on, enabled

        Accepted falsy values:
          false, 0, no, n, off, disabled
        """
        import os

        truthy = {"true", "1", "yes", "y", "on", "enabled"}
        falsy = {"false", "0", "no", "n", "off", "disabled", ""}

        def _coerce(value: Any) -> bool | None:
            if isinstance(value, bool):
                return value

            if value is None:
                return None

            if isinstance(value, (int, float)):
                return bool(value)

            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in truthy:
                    return True
                if normalized in falsy:
                    return False

            return None

        def _request_flag() -> bool | None:
            for source in (metadata, final_metadata):
                if not isinstance(source, dict):
                    continue

                for key in (
                    "enable_kernel_execution",
                    "kernel_execution",
                    "use_kernel_execution",
                ):
                    parsed = _coerce(source.get(key))
                    if parsed is not None:
                        return parsed

                planning_options = source.get("planning")
                if isinstance(planning_options, dict):
                    for key in (
                        "enable_kernel_execution",
                        "kernel_execution",
                        "use_kernel_execution",
                    ):
                        parsed = _coerce(planning_options.get(key))
                        if parsed is not None:
                            return parsed

            return None

        config = getattr(self, "config", None)
        allow_request_enable = bool(
            getattr(config, "allow_request_kernel_execution", False)
        )

        request_flag = _request_flag()

        # Request-level override policy.
        if request_flag is not None:
            if request_flag is False:
                # A request may always disable kernel execution.
                return False

            # request_flag is True.
            if allow_request_enable:
                return True

            # Request tried to enable but is not allowed to.
            # Ignore the enable request and fall through to deployment/config
            # defaults.

        # Deployment-level environment override.
        for env_name in (
            "SMART_SPATIAL_ENABLE_KERNEL_EXECUTION",
            "ENABLE_KERNEL_EXECUTION",
        ):
            parsed = _coerce(os.getenv(env_name))
            if parsed is not None:
                return parsed

        # Service config default.
        parsed = _coerce(getattr(config, "enable_kernel_execution", None))
        if parsed is not None:
            return parsed

        # Safe default.
        return False

    def _planning_trace_to_steps(self, trace: list[Any]) -> list[dict[str, Any]]:
            steps: list[dict[str, Any]] = []

            for item in trace or []:
                capability_name = getattr(item, "capability_name", None)
                node_id = getattr(item, "node_id", None)
                status = getattr(item, "status", None)
                error = getattr(item, "error", None)
                output_summary = getattr(item, "output_summary", None) or {}

                if error:
                    message = error
                elif isinstance(output_summary, dict) and output_summary:
                    parts = [f"{k}={v}" for k, v in output_summary.items()]
                    message = ", ".join(parts[:6])
                else:
                    message = status or ""

                steps.append(
                    {
                        "label": capability_name or node_id or "step",
                        "step": node_id or capability_name or "step",
                        "status": status or "unknown",
                        "message": message,
                    }
                )

            return steps

    def _planning_outputs_to_response_payload(
            self,
            planning_result: Any,
        ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
            from orchestrator.kernel_artifacts import (
                artifact_to_public_dict,
                output_to_artifact,
            )

            layers: list[dict[str, Any]] = []
            outputs: dict[str, Any] = {
                "files": [],
                "vectors": [],
                "tables": [],
                "rasters": [],
                "artifacts": [],
            }
            primary_report: dict[str, Any] | None = None

            def _as_feature_collection(value: Any) -> dict[str, Any] | None:
                if _is_feature_collection(value):
                    return value

                geojson = getattr(value, "geojson", None)
                if _is_feature_collection(geojson):
                    return geojson

                features = getattr(value, "features", None)
                if isinstance(features, list):
                    return {
                        "type": "FeatureCollection",
                        "features": _json_safe(features),
                    }

                if isinstance(value, dict) and isinstance(value.get("features"), list):
                    return {
                        "type": "FeatureCollection",
                        "features": _json_safe(value.get("features") or []),
                    }

                return None

            for node_id, value in (getattr(planning_result, "output_nodes", None) or {}).items():
                try:
                    artifact = output_to_artifact(
                        value,
                        source_node=node_id,
                        title=node_id,
                        produced_by="query_spec_planning",
                        metadata={
                            "source": "planning.output_nodes",
                        },
                    )
                    outputs["artifacts"].append(artifact_to_public_dict(artifact))
                except Exception as exc:
                    outputs.setdefault("artifact_errors", []).append(
                        {
                            "node_id": node_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

                feature_collection = _as_feature_collection(value)

                if feature_collection is not None:
                    layer = {
                        "id": node_id,
                        "name": node_id,
                        "type": "vector",
                        "format": "geojson",
                        "geojson": feature_collection,
                        "summary": {
                            "feature_count": len(feature_collection.get("features", [])),
                        },
                    }
                    layers.append(layer)
                    outputs["vectors"].append(layer)
                    continue

                safe_value = _json_safe(value)

                if isinstance(safe_value, dict):
                    if any(
                        key in safe_value
                        for key in ("title", "summary", "sections", "rankings", "table", "rows")
                    ):
                        if primary_report is None:
                            primary_report = safe_value

                        outputs["tables"].append(
                            {
                                "name": node_id,
                                "source": "planning.output_nodes",
                                "data": safe_value,
                            }
                        )

                    for key in ("path", "file_path", "output_path", "pdf_path"):
                        file_path = safe_value.get(key)
                        if isinstance(file_path, str) and file_path:
                            outputs["files"].append(
                                {
                                    "name": safe_value.get("name") or node_id,
                                    "path": file_path,
                                    "source": "planning.output_nodes",
                                    "format": (
                                        "pdf"
                                        if str(file_path).lower().endswith(".pdf")
                                        else "file"
                                    ),
                                }
                            )
                            break

                    continue

                if isinstance(value, str) and value.lower().endswith(".pdf"):
                    outputs["files"].append(
                        {
                            "name": node_id,
                            "path": value,
                            "source": "planning.output_nodes",
                            "format": "pdf",
                        }
                    )

            return layers, outputs, primary_report

    def _enrich_query_database_params_from_inputs(
            self,
            query_spec: Any,
            resolved_inputs: dict[str, Any],
        ) -> None:
            """
            Inject runtime database connection parameters into query_database ops.

            The LLM should describe *what* to query, not invent secrets or runtime
            connection details. This method copies safe runtime inputs into the
            executable QuerySpec before DAG planning.
            """
            if not resolved_inputs:
                return

            runtime_keys = (
                "host",
                "port",
                "database",
                "user",
                "password",
                "connect_timeout",
                "profile",
                "dsn",
                "schema",
                "table",
                "geom_col",
                "limit",
                "output_srid",
            )

            operations = getattr(query_spec, "operations", None) or []

            for operation in operations:
                if getattr(operation, "op", None) != "query_database":
                    continue

                params = getattr(operation, "params", None)

                if not isinstance(params, dict):
                    continue

                for key in runtime_keys:
                    value = resolved_inputs.get(key)

                    if value is None:
                        continue

                    if key in params and params.get(key) not in (None, "", "<provided-at-runtime>"):
                        continue

                    params[key] = value

                # SQL mode normally exposes geometry as "AS geom".
                # If the LLM generated SQL and no geom_col is present, use "geom".
                if isinstance(params.get("sql"), str) and params.get("sql", "").strip():
                    params.setdefault("geom_col", "geom")

    def _try_handle_query_with_planning(
            self,
            *,
            query: str,
            resolved_inputs: dict[str, Any],
            final_request_id: str,
            final_metadata: dict[str, Any],
            user_context: dict[str, Any] | None = None,
            original_inputs: dict[str, Any] | None = None,
            band_map: dict[str, int] | None = None,
            metadata: dict[str, Any] | None = None,
            project_id: str | None = None,
        ) -> dict[str, Any] | None:
            if not self._query_spec_planning_enabled():
                return None

            try:
                llm_client = OpenAICompatibleLLMClient()
                generator = LLMQuerySpecGenerator(llm_client)

                semantic_planning_context, semantic_planning_context_error = (
                    _extract_semantic_planning_context_from_sources(
                        query=query,
                        resolved_inputs=resolved_inputs,
                        user_context=user_context,
                        metadata=metadata,
                    )
                )

                planning_context: dict[str, Any] = {
                    "available_inputs": sorted((resolved_inputs or {}).keys()),
                    "response_language": getattr(self.config, "response_language", None),
                    "project_id": project_id,
                    "query_spec_contracts": {
                        "query_database": {
                            "contract": "query_database.postgis.v1",
                            "required_format": {
                                "source_type": "postgis",
                                "mode": "select_table",
                                "schema": "public",
                                "table": "table_name_without_schema",
                                "columns": ["property_column_1", "property_column_2"],
                                "geom_col": "real_geometry_column",
                                "geom_alias": "geom",
                                "where": "optional safe where clause",
                                "limit": 1000,
                                "output_srid": 4326
                            },
                            "rules": [
                                "Do not use sql.",
                                "Do not use select.",
                                "Do not use fields.",
                                "Do not use projection.",
                                "Do not invent parameter names.",
                                "columns must contain only property column names.",
                                "Do not put geometry expressions like 'way AS geom' in columns.",
                                "Use geom_col for the real geometry column and geom_alias for the output geometry alias."
                            ],
                            "valid_example": {
                                "op": "query_database",
                                "inputs": {},
                                "params": {
                                    "source_type": "postgis",
                                    "mode": "select_table",
                                    "schema": "public",
                                    "table": "osm_tehran_parks",
                                    "columns": ["osm_id", "name"],
                                    "geom_col": "way",
                                    "geom_alias": "geom",
                                    "where": "way IS NOT NULL",
                                    "limit": 10,
                                    "output_srid": 4326
                                },
                                "output": "parks_layer"
                            }
                        }
                    },
                }

                if semantic_planning_context is not None:
                    planning_context["semantic_planning_context"] = semantic_planning_context
                    final_metadata["semantic_planning_context_attached"] = True

                if semantic_planning_context_error:
                    final_metadata["semantic_planning_context_error"] = (
                        semantic_planning_context_error
                    )

                query_spec = generator.generate(
                    query,
                    context=planning_context,
                )

                planning_runtime_inputs, postgis_runtime_connection_injected = (
                    _build_query_spec_runtime_inputs(
                        resolved_inputs=resolved_inputs,
                        user_context=user_context,
                        metadata=metadata,
                    )
                )

                if postgis_runtime_connection_injected:
                    final_metadata["postgis_runtime_connection_injected"] = True

                self._enrich_query_database_params_from_inputs(
                    query_spec,
                    planning_runtime_inputs,
                )

                from orchestrator.planning.query_spec_contract import validate_query_spec_contract

                validate_query_spec_contract(query_spec)

                runner = make_registry_planning_runner(self.registry)
                kernel_execution_enabled = self._kernel_execution_enabled(
                    metadata=metadata,
                    final_metadata=final_metadata,
                )
                final_metadata["kernel_execution_enabled"] = kernel_execution_enabled

                if kernel_execution_enabled:
                    planning_result = runner.run_with_kernel_execution(
                        query_spec,
                        initial_inputs=resolved_inputs,
                        fail_fast=True,
                    )
                else:
                    planning_result = runner.run(
                        query_spec,
                        initial_inputs=resolved_inputs,
                        fail_fast=True,
                    )

                from orchestrator.planning.kernel_execution_bridge import (
                    compare_kernel_execution_to_planning_outputs,
                    kernel_execution_to_summary,
                )
                from orchestrator.planning.kernel_plan_adapter import kernel_plan_to_summary

                layers, outputs, primary_report = self._planning_outputs_to_response_payload(
                    planning_result
                )
                kernel_plan_summary = kernel_plan_to_summary(
                    getattr(planning_result, "kernel_plan", None)
                )
                kernel_execution_summary = kernel_execution_to_summary(
                    getattr(planning_result, "kernel_execution", None)
                )
                kernel_execution_parity = compare_kernel_execution_to_planning_outputs(
                    planning_result
                )
                steps = self._planning_trace_to_steps(
                    getattr(planning_result, "trace", []) or []
                )

                success = bool(getattr(planning_result, "success", False))
                planning_error = getattr(planning_result, "error", None)
                planning_structured_error = getattr(
                    planning_result,
                    "structured_error",
                    None,
                )

                answer = (
                    "تحلیل با موفقیت انجام شد."
                    if success
                    else (planning_error or "اجرای تحلیل برنامه‌ریزی‌شده ناموفق بود.")
                )

                if success and outputs["files"]:
                    answer = "تحلیل با موفقیت انجام شد و فایل خروجی آماده است."

                if success and primary_report is not None:
                    answer = "تحلیل با موفقیت انجام شد و گزارش آماده است."

                planning_metadata = {
                    **final_metadata,
                    "query_spec_planning_enabled": True,
                    "planning_attempted": True,
                    "planner_type": "deterministic_query_spec",
                    "execution_mode": (
                        "query_spec_planning_kernel_execution"
                        if kernel_execution_enabled
                        else "query_spec_planning"
                    ),
                    "kernel_execution_enabled": kernel_execution_enabled,
                    "query_spec": _redact_sensitive_json(query_spec_to_dict(query_spec)),
                    "planning_summary": {
                        "success": success,
                        "error": planning_error,
                        "structured_error": planning_structured_error,
                        "output_nodes": sorted(
                            (getattr(planning_result, "output_nodes", None) or {}).keys()
                        ),
                        "kernel_execution_enabled": kernel_execution_enabled,
                        "kernel_plan": kernel_plan_summary,
                        "kernel_execution": kernel_execution_summary,
                        "kernel_execution_success": (
                            None
                            if kernel_execution_summary is None
                            else bool(kernel_execution_summary.get("success"))
                        ),
                        "kernel_execution_parity": kernel_execution_parity,
                    },
                }

                production_response = {
                    "status": "succeeded" if success else "failed",
                    "request_id": final_request_id,
                    "query_hash": None,
                    "answer": answer,
                    "message": answer,
                    "structured_error": planning_structured_error,
                    "outputs": outputs,
                    "layers": layers,
                    "artifacts": outputs.get("artifacts", []),
                    "kernel_plan": kernel_plan_summary,
                    "kernel_execution": kernel_execution_summary,
                    "steps": steps,
                    "confidence": {
                        "level": None,
                        "score": None,
                        "llm_action": "query_spec_planning",
                        "is_ambiguous": False,
                        "competitive_gap": None,
                    },
                    "audit_ref": {
                        "request_id": final_request_id,
                        "query_hash": None,
                        "status": "succeeded" if success else "failed",
                        "plan_steps": len(steps),
                    },
                    "warnings": [] if success else [planning_error or "Planning execution failed."],
                    "next_actions": [],
                    "metadata": planning_metadata,
                }

                if primary_report is not None:
                    production_response["report"] = primary_report

                self._remember(
                    request_id=final_request_id,
                    record={
                        "request_id": final_request_id,
                        "query": query,
                        "inputs": _json_safe(resolved_inputs),
                        "original_inputs": _json_safe(original_inputs or {}),
                        "band_map": _json_safe(band_map or {}),
                        "user_context": _json_safe(user_context or {}),
                        "metadata": _json_safe(metadata or {}),
                        "final_metadata": _json_safe(planning_metadata),
                        "project_id": project_id,
                        "query_spec": _redact_sensitive_json(query_spec_to_dict(query_spec)),
                        "planning_result": {
                            "success": success,
                            "error": planning_error,
                            "structured_error": _json_safe(planning_structured_error),
                            "outputs": _json_safe(getattr(planning_result, "outputs", {})),
                            "output_nodes": _json_safe(
                                getattr(planning_result, "output_nodes", {})
                            ),
                            "trace": _json_safe(
                                [
                                    {
                                        "node_id": getattr(t, "node_id", None),
                                        "capability_name": getattr(t, "capability_name", None),
                                        "status": getattr(t, "status", None),
                                        "started_at": getattr(t, "started_at", None),
                                        "finished_at": getattr(t, "finished_at", None),
                                        "error": getattr(t, "error", None),
                                        "input_keys": getattr(t, "input_keys", None),
                                        "output_summary": getattr(t, "output_summary", None),
                                    }
                                    for t in (getattr(planning_result, "trace", []) or [])
                                ]
                            ),
                        },
                        "production_response": production_response,
                    },
                )

                stored_record = self.get_request(final_request_id)

                if stored_record is not None:
                    stored_project_id = stored_record.get("project_id")

                    if stored_project_id:
                        try:
                            self.project_service.attach_request(
                                stored_project_id,
                                final_request_id,
                            )
                        except Exception:
                            pass

                    if self.config.persist_outputs:
                        manifest = self._persist_outputs_for_record(stored_record)

                        if stored_project_id and isinstance(manifest, dict):
                            try:
                                self.project_service.attach_output(
                                    stored_project_id,
                                    final_request_id,
                                )
                            except Exception:
                                pass

                return production_response

            except (
                LLMSpecGenerationError,
                PlanningError,
                DagValidationError,
                DagExecutionError,
                ValueError,
                RuntimeError,
                Exception,
            ) as exc:
                from orchestrator.planning.error_mapping import (
                    planning_exception_to_structured_error,
                )

                planning_structured_error = planning_exception_to_structured_error(
                    exc,
                    source="orchestrator_service",
                    stage="query_spec_planning",
                )

                final_metadata["query_spec_planning_enabled"] = True
                final_metadata["planning_attempted"] = True
                final_metadata["planning_error"] = str(exc)
                final_metadata["planning_structured_error"] = planning_structured_error
                return None

    def _maybe_plan_llm_intent(
        self,
        query: str,
    ) -> dict[str, Any] | None:
        """
        Best-effort LLM intent planning. Never breaks the pipeline.
        """
        if not self._llm_planning_enabled():
            return None

        try:
            planned = self.plan_intent_with_llm(query)
        except QueryExecutionServiceError:
            return None
        except Exception:
            return None

        if not isinstance(planned, dict):
            return None

        return planned.get("intent")

    @staticmethod
    def _apply_intent_to_query(
        query: str,
        intent: dict[str, Any] | None,
    ) -> str:
        """
        Rewrite the natural query so the current deterministic parser
        can trigger the right workflow.

        Currently specialized for vegetation_extraction (NDVI pipeline).
        """
        if not intent or not isinstance(intent, dict):
            return query

        intent_name = str(intent.get("intent_name") or "")

        if intent_name == "vegetation_extraction":
            params = intent.get("parameters") or {}

            try:
                threshold = float(params.get("threshold", 0.3))
            except Exception:
                threshold = 0.3

            vectorize = bool(params.get("vectorize", False))

            parts = [
                "NDVI vegetation extraction.",
                f"greater than {threshold}.",
            ]

            if vectorize:
                parts.append("polygon vectorize استخراج کن.")

            parts.append(f"original_query: {query}")

            return " ".join(parts)

        if intent_name == "raster_vectorization":
            return "NDVI raster_to_vector polygon استخراج کن. " + f"original_query: {query}"

        return query

    def plan_intent_with_llm(
        self,
        query: str,
    ) -> dict[str, Any]:
        """
        Plan geospatial query intent using the configured LLM.

        This method does not execute plugins.
        """
        from orchestrator.llm_client import LLMClientError, LLMConfigError
        from orchestrator.llm_intent_planner import (
            LLMIntentPlannerError,
            plan_intent_with_llm,
        )

        capability_names = self._enabled_capability_names()

        try:
            return plan_intent_with_llm(
                query=query,
                available_capabilities=capability_names,
            )
        except (LLMConfigError, LLMClientError, LLMIntentPlannerError) as exc:
            raise QueryExecutionServiceError(str(exc)) from exc

    def handle_query(
            self,
            *,
            query: str,
            inputs: dict[str, Any],
            band_map: dict[str, int] | None = None,
            request_id: str | None = None,
            user_context: dict[str, Any] | None = None,
            metadata: dict[str, Any] | None = None,
            min_score: float | None = None,
            project_id: str | None = None,
        ) -> dict[str, Any]:
            """
            Execute a real user query and return production response dict.

            This is the main method API/Frontend should use.
            """
            final_request_id = request_id or self._new_request_id()

            final_metadata = {
                "service": "OrchestratorService",
                "weighted_router": self.config.use_weighted_router,
            }

            # Propagate project_id so _remember can link this request to its project.
            _resolved_project_id = str(project_id or "").strip() or None
            if _resolved_project_id:
                final_metadata["project_id"] = _resolved_project_id

            if user_context:
                final_metadata["user_context"] = _json_safe(user_context)

            if metadata:
                final_metadata.update(dict(metadata))

            llm_intent = self._maybe_plan_llm_intent(query)
            real_estate_ranking_response = self._try_handle_real_estate_ranking_directly(
                query=query,
                inputs=inputs,
                request_id=locals().get("request_id"),
                llm_intent=llm_intent,
            )
            if real_estate_ranking_response is not None:
                return real_estate_ranking_response

            effective_query = self._apply_intent_to_query(query, llm_intent)

            final_metadata["llm_planning_enabled"] = self._llm_planning_enabled()
            if llm_intent is not None:
                final_metadata["llm_intent"] = _json_safe(llm_intent)
                final_metadata["original_query"] = query
                final_metadata["effective_query"] = effective_query

            status_guard_response = self._try_handle_system_status_query(
                query=query,
                inputs=inputs,
                final_request_id=final_request_id,
                final_metadata=final_metadata,
                band_map=band_map,
                user_context=user_context,
                llm_intent=llm_intent,
            )

            if status_guard_response is not None:
                return status_guard_response

            try:
                router = self._build_router()
                resolved_inputs = self._resolve_input_references(inputs)

                missing_real_estate_inputs_response = self._try_handle_missing_real_estate_inputs(
                    query=query,
                    inputs=inputs,
                    resolved_inputs=resolved_inputs,
                    final_request_id=final_request_id,
                    final_metadata=final_metadata,
                    band_map=band_map,
                    user_context=user_context,
                    llm_intent=llm_intent,
                )

                if missing_real_estate_inputs_response is not None:
                    return missing_real_estate_inputs_response

                # Try real-estate ranking again after upload/input references are resolved.
                # UI auto_project_data often provides only upload refs at first.
                real_estate_ranking_response = self._try_handle_real_estate_ranking_directly(
                    query=query,
                    inputs=resolved_inputs,
                    request_id=final_request_id,
                    llm_intent=llm_intent,
                )
                if real_estate_ranking_response is not None:
                    return real_estate_ranking_response

                direct_vector_response = self._try_handle_vector_display_directly(
                    query=query,
                    inputs=inputs,
                    resolved_inputs=resolved_inputs,
                    final_request_id=final_request_id,
                    final_metadata=final_metadata,
                    band_map=band_map,
                    user_context=user_context,
                    llm_intent=llm_intent,
                )

                if direct_vector_response is not None:
                    return direct_vector_response

                query_spec_planning_enabled = self._query_spec_planning_enabled()
                final_metadata["query_spec_planning_enabled"] = query_spec_planning_enabled

                if query_spec_planning_enabled:
                    planning_response = self._try_handle_query_with_planning(
                        query=effective_query,
                        resolved_inputs=resolved_inputs,
                        final_request_id=final_request_id,
                        final_metadata=final_metadata,
                        user_context=user_context,
                        original_inputs=inputs,
                        band_map=band_map,
                        metadata=metadata,
                        project_id=_resolved_project_id,
                    )

                    if planning_response is not None:
                        return planning_response

                run_result = run_natural_query_with_routing_evidence(
                    effective_query,
                    inputs=resolved_inputs,
                    band_map=band_map or {},
                    router=router,
                    min_score=self.config.min_score if min_score is None else min_score,
                    request_id=final_request_id,
                )

                production_response = self.response_builder.build_dict(
                    run_result=run_result,
                    metadata=final_metadata,
                )

                self._remember(
                    request_id=final_request_id,
                    record={
                        "request_id": final_request_id,
                        "query": query,
                        "inputs": _json_safe(resolved_inputs),
                        "original_inputs": _json_safe(inputs),
                        "band_map": _json_safe(band_map or {}),
                        "user_context": _json_safe(user_context or {}),
                        "metadata": _json_safe(final_metadata),
                        "project_id": _resolved_project_id,
                        "run_result": run_result,
                        "audit_record": run_result.get("audit_record"),
                        "production_response": production_response,
                    },
                )

                stored_record = self.get_request(final_request_id)

                if stored_record is not None:
                    project_id = stored_record.get("project_id")

                    if project_id:
                        try:
                            self.project_service.attach_request(
                                project_id,
                                final_request_id,
                            )
                        except Exception:
                            pass

                    if self.config.persist_outputs:
                        manifest = self._persist_outputs_for_record(stored_record)

                        if project_id and isinstance(manifest, dict):
                            try:
                                self.project_service.attach_output(
                                    project_id,
                                    final_request_id,
                                )
                            except Exception:
                                pass

                return production_response

            except Exception as exc:
                service_structured_error = _service_exception_to_structured_error(
                    exc,
                    stage="handle_query",
                )
                final_metadata["structured_error"] = service_structured_error
                final_metadata["service_structured_error"] = service_structured_error

                failed_response = self.response_builder.build_dict(
                    response={
                        "status": "failed",
                        "request_id": final_request_id,
                    },
                    error=exc,
                    metadata=final_metadata,
                )

                failed_response["structured_error"] = _json_safe(service_structured_error)
                failed_metadata = failed_response.setdefault("metadata", {})
                if isinstance(failed_metadata, dict):
                    failed_metadata["structured_error"] = _json_safe(service_structured_error)
                    failed_metadata["service_structured_error"] = _json_safe(service_structured_error)

                self._remember(
                    request_id=final_request_id,
                    record={
                        "request_id": final_request_id,
                        "query": query,
                        "inputs": _json_safe(inputs),
                        "band_map": _json_safe(band_map or {}),
                        "user_context": _json_safe(user_context or {}),
                        "metadata": _json_safe(final_metadata),
                        "error": repr(exc),
                        "production_response": failed_response,
                        "project_id": _resolved_project_id,
                    },
                )

                return failed_response
