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


from smart_spatial_system.application.services.planning_response_adapter import (
    planning_outputs_to_response_payload,
    planning_trace_to_steps,
)


from smart_spatial_system.application.services.planning_execution_policy import (
    is_kernel_execution_enabled,
    is_query_spec_planning_enabled,
)


from smart_spatial_system.application.services.query_spec_enrichment import (
    enrich_query_database_params_from_inputs,
)


from smart_spatial_system.application.services.llm_intent_adapter import (
    LLMIntentAdapterError,
    apply_intent_to_query,
    is_llm_planning_enabled,
    plan_intent_with_llm as run_llm_intent_planner,
)


from smart_spatial_system.application.services.system_status_query_handler import (
    is_system_status_query,
    try_handle_system_status_query,
)


from smart_spatial_system.application.services.vector_geojson_helpers import (
    find_geojson_like,
    read_geojson_path_if_possible,
    summarize_feature_collection,
)


from smart_spatial_system.application.services.vector_query_classifier import (
    is_vector_display_query,
    is_vector_summary_query,
)


from smart_spatial_system.application.services.vector_display_handler import (
    try_handle_vector_display_directly,
)


from smart_spatial_system.application.services.query_execution.real_estate_classifier import (
    has_any_real_estate_payload,
    is_real_estate_analysis_query,
    looks_like_real_estate_ranking_query,
)

from smart_spatial_system.application.services.query_execution.real_estate_context import (
    enrich_property_feature_collection_with_spatial_context,
    extract_property_feature_collection_from_inputs,
    extract_real_estate_spatial_context_from_inputs,
)

from smart_spatial_system.application.services.query_execution.real_estate_scoring import (
    evaluate_real_estate_eligibility,
    score_real_estate_property,
)

from smart_spatial_system.application.services.real_estate_spatial_helpers import (
    distance_point_to_geometry_m,
    distance_point_to_point_m,
    distance_point_to_segment_m,
    feature_point_lonlat,
    has_bool_like_value,
    has_metric_value,
    lonlat_to_local_xy_m,
    nearest_distance_to_features_m,
    normalize_risk_level,
    point_in_polygon_feature_lonlat,
    point_in_ring_lonlat,
    to_float_or_none,
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
    @staticmethod
    def _llm_planning_enabled() -> bool:
        return is_llm_planning_enabled()



    @staticmethod
    @staticmethod
    def _query_spec_planning_enabled() -> bool:
        return is_query_spec_planning_enabled()



    def _kernel_execution_enabled(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        final_metadata: dict[str, Any] | None = None,
    ) -> bool:
        return is_kernel_execution_enabled(
            config=getattr(self, "config", None),
            metadata=metadata,
            final_metadata=final_metadata,
        )



    def _planning_trace_to_steps(self, trace: list[Any]) -> list[dict[str, Any]]:
        return planning_trace_to_steps(trace)



    def _planning_outputs_to_response_payload(
        self,
        planning_result: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
        return planning_outputs_to_response_payload(planning_result)



    def _enrich_query_database_params_from_inputs(
        self,
        query_spec: Any,
        resolved_inputs: dict[str, Any],
    ) -> None:
        return enrich_query_database_params_from_inputs(
            query_spec=query_spec,
            resolved_inputs=resolved_inputs,
        )



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

                runner = make_registry_planning_runner(self._build_enabled_registry_view())
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

    def _orchestrator_context(self) -> Any | None:
        """
        Return the owning orchestrator/context object when available.

        QueryExecutionService intentionally keeps backward compatibility with
        tests/extensions that monkeypatch query helper methods on
        OrchestratorService.  The exact attribute name is kept defensive because
        this service is used as an extraction layer.
        """
        for attr_name in (
            "context",
            "_context",
            "orchestrator",
            "_orchestrator",
            "owner",
            "_owner",
        ):
            owner = getattr(self, attr_name, None)
            if owner is not None and owner is not self:
                return owner

        return None

    def _maybe_plan_llm_intent(
        self,
        query: str,
    ) -> dict[str, Any] | None:
        """
        Best-effort LLM intent planning. Never breaks the pipeline.

        Backward compatibility:
        tests/extensions may monkeypatch OrchestratorService._maybe_plan_llm_intent.
        When such an override exists on the owning context, honor it before
        using the extracted service implementation.
        """
        owner = self._orchestrator_context()
        owner_method = getattr(owner, "_maybe_plan_llm_intent", None) if owner is not None else None

        if callable(owner_method):
            owner_method_func = getattr(owner_method, "__func__", None)
            current_method_func = getattr(type(owner), "_maybe_plan_llm_intent", None)

            # If the owner method is not the class-level delegating method, it
            # is likely an instance monkeypatch/override and should be honored.
            if owner_method_func is None or owner_method_func is not current_method_func:
                try:
                    planned_intent = owner_method(query)
                except Exception:
                    planned_intent = None

                if isinstance(planned_intent, dict):
                    return planned_intent

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
    @staticmethod
    def _apply_intent_to_query(
        query: str,
        intent: dict[str, Any] | None,
    ) -> str:
        return apply_intent_to_query(query, intent)



    def plan_intent_with_llm(
        self,
        query: str,
    ) -> dict[str, Any]:
        """
        Plan geospatial query intent using the configured LLM.

        This method does not execute plugins.
        """
        capability_names = self._enabled_capability_names()

        try:
            return run_llm_intent_planner(
                query=query,
                available_capabilities=capability_names,
            )
        except LLMIntentAdapterError as exc:
            raise QueryExecutionServiceError(str(exc)) from exc



    def _try_handle_system_status_query(
        self,
        *,
        query: str,
        inputs: dict[str, Any],
        final_request_id: str,
        final_metadata: dict[str, Any],
        band_map: dict[str, int] | None = None,
        user_context: dict[str, Any] | None = None,
        llm_intent: Any | None = None,
    ) -> dict[str, Any] | None:
        return try_handle_system_status_query(
            self,
            query=query,
            inputs=inputs,
            final_request_id=final_request_id,
            final_metadata=final_metadata,
            band_map=band_map,
            user_context=user_context,
            llm_intent=llm_intent,
            json_safe=_json_safe,
        )



    def _is_system_status_query(
        self,
        query: str,
        llm_intent: Any | None = None,
    ) -> bool:
        return is_system_status_query(query, llm_intent)



    @staticmethod
    def _is_vector_display_query(
        query: str,
        intent: dict[str, Any] | None = None,
    ) -> bool:
        return is_vector_display_query(query, intent)



    @staticmethod
    def _is_vector_summary_query(
        query: str,
        intent: dict[str, Any] | None = None,
    ) -> bool:
        return is_vector_summary_query(query, intent)



    @staticmethod
    @staticmethod
    def _read_geojson_path_if_possible(value: Any) -> dict[str, Any] | None:
        return read_geojson_path_if_possible(value)



    @classmethod
    @classmethod
    def _find_geojson_like(
        cls,
        obj: Any,
        *,
        max_depth: int = 8,
    ) -> dict[str, Any] | None:
        return find_geojson_like(obj, max_depth=max_depth)



    @staticmethod
    @staticmethod
    def _summarize_feature_collection(
        feature_collection: dict[str, Any],
    ) -> dict[str, Any]:
        return summarize_feature_collection(feature_collection)



    def _try_handle_vector_display_directly(
        self,
        *,
        query: str,
        inputs: dict[str, Any],
        resolved_inputs: dict[str, Any],
        final_request_id: str,
        final_metadata: dict[str, Any],
        band_map: dict[str, int] | None = None,
        user_context: dict[str, Any] | None = None,
        llm_intent: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return try_handle_vector_display_directly(
            self,
            query=query,
            inputs=inputs,
            resolved_inputs=resolved_inputs,
            final_request_id=final_request_id,
            final_metadata=final_metadata,
            band_map=band_map,
            user_context=user_context,
            llm_intent=llm_intent,
            json_safe=_json_safe,
        )



    def _try_handle_missing_real_estate_inputs(
        self,
        *,
        query: str,
        inputs: dict[str, Any],
        resolved_inputs: dict[str, Any],
        final_request_id: str,
        final_metadata: dict[str, Any],
        band_map: dict[str, int] | None = None,
        user_context: dict[str, Any] | None = None,
        llm_intent: Any | None = None,
    ) -> dict[str, Any] | None:
        """
        Return a controlled response for complex real-estate analysis requests
        when no useful spatial inputs were provided.
        """
        if not self._is_real_estate_analysis_query(query, llm_intent):
            return None

        if self._has_any_real_estate_payload(resolved_inputs):
            return None

        required_layers = [
            "لایه املاک یا نقاط/پلیگون‌های ملک‌ها",
            "لایه POI شامل ایستگاه‌های مترو و مراکز خرید",
            "لایه خیابان‌های اصلی یا شبکه معابر",
            "لایه‌های ریسک سیل، زلزله و آتش‌سوزی",
            "در صورت نیاز، لایه محدوده مجاز ساخت‌وساز یا کاربری اراضی",
        ]

        answer = (
            "برای انجام تحلیل و رتبه‌بندی املاک، داده مکانی کافی ارسال نشده است. "
            "لطفاً حداقل لایه املاک و لایه‌های مرجع مانند مترو/مرکز خرید، خیابان‌های اصلی "
            "و ریسک‌ها را در ورودی‌ها اضافه کنید."
        )

        response = {
            "ok": False,
            "status": "failed",
            "request_id": final_request_id,
            "query": query,
            "answer": answer,
            "message": answer,
            "outputs": {},
            "layers": [],
            "result": {
                "type": "missing_required_inputs",
                "domain": "real_estate_spatial_ranking",
                "required_layers": required_layers,
            },
            "confidence": {
                "level": None,
                "score": None,
                "llm_action": "input_validation_guard",
                "is_ambiguous": False,
                "competitive_gap": None,
            },
            "audit_ref": {
                "request_id": final_request_id,
                "query_hash": None,
                "status": "failed",
                "plan_steps": 0,
            },
            "warnings": [
                "درخواست تحلیل املاک تشخیص داده شد، اما ورودی مکانی کافی وجود ندارد.",
                "برای جلوگیری از اجرای pipeline اشتباه، برنامه‌ریز مکانی اجرا نشد.",
            ],
            "next_actions": [
                "لایه املاک را به صورت GeoJSON/Vector اضافه کنید.",
                "لایه ایستگاه‌های مترو و مراکز خرید را اضافه کنید.",
                "لایه خیابان‌های اصلی و لایه‌های ریسک را اضافه کنید.",
                "سپس درخواست رتبه‌بندی و تولید گزارش را دوباره اجرا کنید.",
            ],
            "metadata": _json_safe(final_metadata),
        }

        _resolved_project_id = str(final_metadata.get("project_id") or "").strip() or None

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
                "production_response": _json_safe(response),
            },
        )

        if _resolved_project_id:
            try:
                self.project_service.attach_request(
                    _resolved_project_id,
                    final_request_id,
                )
            except Exception:
                pass

        return _json_safe(response)

    def _is_real_estate_analysis_query(
        self,
        query: str,
        llm_intent: Any | None = None,
    ) -> bool:
        return is_real_estate_analysis_query(query, llm_intent)

    def _has_any_real_estate_payload(
        self,
        resolved_inputs: dict[str, Any],
    ) -> bool:
        return has_any_real_estate_payload(resolved_inputs)

    def _looks_like_real_estate_ranking_query(self, query: str) -> bool:
        return looks_like_real_estate_ranking_query(query)

    def _extract_property_feature_collection_from_inputs(self, inputs: dict[str, Any] | None) -> dict[str, Any] | None:
        return extract_property_feature_collection_from_inputs(inputs)

    def _extract_real_estate_spatial_context_from_inputs(self, inputs: dict[str, Any] | None) -> dict[str, Any]:
        return extract_real_estate_spatial_context_from_inputs(inputs)

    def _feature_point_lonlat(self, feature: dict[str, Any]) -> tuple[float, float] | None:
        return feature_point_lonlat(feature)



    def _point_in_ring_lonlat(self, point: tuple[float, float], ring: list[Any]) -> bool:
        return point_in_ring_lonlat(point, ring)



    def _point_in_polygon_feature_lonlat(
        self,
        point: tuple[float, float],
        feature: dict[str, Any],
    ) -> bool:
        return point_in_polygon_feature_lonlat(point, feature)



    def _lonlat_to_local_xy_m(
        self,
        point: tuple[float, float],
        *,
        ref_lat: float,
    ) -> tuple[float, float]:
        return lonlat_to_local_xy_m(point, ref_lat=ref_lat)



    def _distance_point_to_segment_m(
        self,
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        return distance_point_to_segment_m(point, start, end)



    def _distance_point_to_point_m(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
    ) -> float:
        return distance_point_to_point_m(a, b)



    def _distance_point_to_geometry_m(
        self,
        point: tuple[float, float],
        feature: dict[str, Any],
    ) -> float | None:
        return distance_point_to_geometry_m(point, feature)



    def _nearest_distance_to_features_m(
        self,
        point: tuple[float, float],
        features: list[dict[str, Any]],
    ) -> float | None:
        return nearest_distance_to_features_m(point, features)



    def _has_metric_value(self, props: dict[str, Any], key: str) -> bool:
        return has_metric_value(props, key)



    def _has_bool_like_value(self, props: dict[str, Any], *keys: str) -> bool:
        return has_bool_like_value(props, *keys)



    def _normalize_risk_level(self, value: Any) -> str:
        return normalize_risk_level(value)



    def _to_float_or_none(self, value: Any) -> float | None:
        return to_float_or_none(value)



    def _enrich_property_feature_collection_with_spatial_context(
        self,
        feature_collection: dict[str, Any],
        spatial_context: dict[str, list[dict[str, Any]]] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return enrich_property_feature_collection_with_spatial_context(
            feature_collection,
            spatial_context,
            feature_point_lonlat=self._feature_point_lonlat,
            has_metric_value=self._has_metric_value,
            nearest_distance_to_features_m=self._nearest_distance_to_features_m,
            has_bool_like_value=self._has_bool_like_value,
            point_in_polygon_feature_lonlat=self._point_in_polygon_feature_lonlat,
        )

    def _score_real_estate_property(self, props: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        return score_real_estate_property(props)

    def _evaluate_real_estate_eligibility(self, props: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
        return evaluate_real_estate_eligibility(props)

    def _build_real_estate_pdf_report_payload(
        self,
        *,
        report: dict[str, Any],
        table_rows: list[dict[str, Any]],
        ranked_geojson: dict[str, Any],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        pdf_rows: list[dict[str, Any]] = []
        score_values: list[float] = []

        for row in table_rows:
            pdf_row = dict(row)

            score_value = pdf_row.get("score")
            try:
                numeric_score = float(score_value)
                score_values.append(numeric_score)
            except Exception:
                numeric_score = 0.0

            # Compatibility aliases expected by the default real_estate_report.html template.
            pdf_row.setdefault("investment_score", numeric_score)
            pdf_row.setdefault("property_name", pdf_row.get("name"))
            pdf_row.setdefault("asset_type", pdf_row.get("kind"))
            pdf_row.setdefault("nearest_poi_distance_m", pdf_row.get("best_poi_distance_m"))
            pdf_row.setdefault("main_road_distance_m", pdf_row.get("distance_to_main_road_m"))
            pdf_row.setdefault("allowed_zone", pdf_row.get("in_allowed_zone"))

            pdf_rows.append(pdf_row)

        top_row = pdf_rows[0] if pdf_rows else {}
        avg_score = round(sum(score_values) / len(score_values), 2) if score_values else None
        min_score = round(min(score_values), 2) if score_values else None
        max_score = round(max(score_values), 2) if score_values else None

        pdf_summary = {
            **summary,
            "title": report.get("title") or "گزارش رتبه‌بندی املاک",
            "notes": report.get("notes") or [],
            # ReportOut/report_builder compatible fields:
            "total_count": summary.get("eligible_count", len(pdf_rows)),
            "top_name": summary.get("top_property") or top_row.get("name"),
            "top_rank": top_row.get("rank"),
            "top_score_value": summary.get("top_score") or top_row.get("score"),
            "top_score": summary.get("top_score") or max_score,
            "avg_score": avg_score,
            "min_score": min_score,
            "max_score": max_score,
            "language": "fa",
        }

        columns = [
            {"key": "rank", "field": "rank", "label": "رتبه"},
            {"key": "name", "field": "name", "label": "نام ملک"},
            {"key": "kind", "field": "kind", "label": "نوع"},
            {"key": "price", "field": "price", "label": "قیمت"},
            {"key": "score", "field": "score", "label": "امتیاز"},
            {"key": "investment_score", "field": "investment_score", "label": "امتیاز سرمایه‌گذاری"},
            {"key": "best_poi_distance_m", "field": "best_poi_distance_m", "label": "نزدیک‌ترین فاصله به مترو/مرکز خرید"},
            {"key": "distance_to_main_road_m", "field": "distance_to_main_road_m", "label": "فاصله تا خیابان اصلی"},
            {"key": "flood_risk", "field": "flood_risk", "label": "ریسک سیل"},
            {"key": "earthquake_risk", "field": "earthquake_risk", "label": "ریسک زلزله"},
            {"key": "fire_risk", "field": "fire_risk", "label": "ریسک آتش‌سوزی"},
            {"key": "in_allowed_zone", "field": "in_allowed_zone", "label": "محدوده مجاز ساخت"},
        ]

        return {
            "meta": {
                "title": report.get("title") or "گزارش رتبه‌بندی املاک",
                "language": "fa",
                "format": "pdf",
                "domain": "real_estate_spatial_ranking",
                "score_field": "score",
                "rank_field": "rank",
                "name_field": "name",
            },
            "summary": pdf_summary,
            "table": {
                "title": "جدول رتبه‌بندی املاک",
                "columns": columns,
                "rows": pdf_rows,
                "total_rows": len(pdf_rows),
            },
            "map_layers": [
                {
                    "id": "ranked_properties",
                    "name": "املاک رتبه‌بندی‌شده",
                    "label": "املاک رتبه‌بندی‌شده",
                    "type": "vector",
                    "format": "geojson",
                    "feature_count": len(ranked_geojson.get("features") or []),
                    "geojson": ranked_geojson,
                }
            ],
            "spec": {
                "report_type": "real_estate_ranking",
                "score_field": "score",
                "rank_field": "rank",
                "criteria": summary.get("criteria") or {},
            },
            "success": True,
            "errors": [],
        }

    def _try_render_real_estate_ranking_document(
        self,
        *,
        report: dict[str, Any],
        table_rows: list[dict[str, Any]],
        ranked_geojson: dict[str, Any],
        summary: dict[str, Any],
        request_id: str,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        warnings: list[str] = []
        trace_step: dict[str, Any] = {
            "order": 5,
            "node_id": "node_005_render_pdf",
            "capability_name": "render_pdf",
            "plugin_id": "pdf_renderer",
            "output_kind": "document",
            "status": "skipped",
        }

        try:
            from plugins.pdf_renderer import render_pdf
        except Exception as exc:
            warnings.append(f"PDF renderer import failed: {exc}")
            trace_step.update(
                {
                    "status": "failed",
                    "error": str(exc),
                }
            )
            return documents, warnings, trace_step

        pdf_report = self._build_real_estate_pdf_report_payload(
            report=report,
            table_rows=table_rows,
            ranked_geojson=ranked_geojson,
            summary=summary,
        )

        safe_request_id = str(request_id or "request").replace("/", "_")
        output_dir = Path("artifacts") / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"real_estate_ranking_{safe_request_id}.pdf"

        try:
            pdf_out = render_pdf(
                pdf_report,
                output_path=str(output_path),
                save_to_disk=True,
                metadata={
                    "request_id": request_id,
                    "domain": "real_estate_spatial_ranking",
                    "report_id": "real_estate_ranking_report",
                },
            )
        except Exception as exc:
            warnings.append(f"PDF render failed unexpectedly: {exc}")
            trace_step.update(
                {
                    "status": "failed",
                    "error": str(exc),
                }
            )
            return documents, warnings, trace_step

        pdf_dict = pdf_out.to_dict() if hasattr(pdf_out, "to_dict") else {}

        if getattr(pdf_out, "success", False) and getattr(pdf_out, "file_path", None):
            pdf_file_path = str(pdf_out.file_path)
            pdf_filename = Path(pdf_file_path).name
            pdf_download_url = f"/requests/{request_id}/documents/{pdf_filename}"

            documents.append(
                {
                    "id": "real_estate_ranking_pdf",
                    "name": "real_estate_ranking_report.pdf",
                    "filename": pdf_filename,
                    "format": "pdf",
                    "role": "downloadable_report",
                    "mime_type": "application/pdf",
                    "path": pdf_file_path,
                    "file_path": pdf_file_path,
                    "download_url": pdf_download_url,
                    "preview_url": pdf_download_url,
                    "size_bytes": len(getattr(pdf_out, "pdf_bytes", b"") or b""),
                    "meta": getattr(pdf_out, "meta", {}) or pdf_dict.get("meta", {}),
                }
            )
            trace_step.update(
                {
                    "status": "success",
                    "artifact_id": "real_estate_ranking_pdf",
                    "path": pdf_out.file_path,
                }
            )
            return documents, warnings, trace_step

        html = getattr(pdf_out, "html", "") or ""
        errors = getattr(pdf_out, "errors", []) or pdf_dict.get("errors", [])

        if html:
            documents.append(
                {
                    "id": "real_estate_ranking_html",
                    "name": "real_estate_ranking_report.html",
                    "format": "html",
                    "role": "printable_report_fallback",
                    "mime_type": "text/html",
                    "content": html,
                    "size_bytes": len(html.encode("utf-8")),
                    "meta": getattr(pdf_out, "meta", {}) or pdf_dict.get("meta", {}),
                    "errors": errors,
                }
            )
            warnings.append(
                "PDF rendering was not completed; HTML fallback document was returned."
            )
            trace_step.update(
                {
                    "status": "warning",
                    "artifact_id": "real_estate_ranking_html",
                    "errors": errors,
                }
            )
            return documents, warnings, trace_step

        warnings.append("PDF rendering failed and no HTML fallback was produced.")
        trace_step.update(
            {
                "status": "failed",
                "errors": errors,
            }
        )
        return documents, warnings, trace_step

    def _build_real_estate_analysis_inspector(
        self,
        *,
        title: str,
        status: str,
        summary: dict[str, Any],
        outputs: dict[str, Any],
        layers: list[dict[str, Any]],
        trace: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Build a frontend-friendly Analysis Inspector payload.

        This is intentionally additive/non-breaking:
        existing response.summary / outputs / layers / audit_record remain unchanged.
        The frontend can prefer response.inspector when available.
        """

        def _feature_count_from_vector(vector: dict[str, Any]) -> int:
            geojson = vector.get("geojson") or {}
            features = geojson.get("features") if isinstance(geojson, dict) else None
            return len(features) if isinstance(features, list) else 0

        def _row_count_from_table(table: dict[str, Any]) -> int:
            rows = table.get("rows")
            return len(rows) if isinstance(rows, list) else 0

        def _trace_label(step: dict[str, Any]) -> str:
            capability = step.get("capability_name")
            labels = {
                "filter_features": "فیلتر املاک",
                "score_features": "امتیازدهی",
                "rank_features": "رتبه‌بندی",
                "build_report": "ساخت گزارش",
                "render_pdf": "تولید PDF",
            }
            return labels.get(str(capability), str(capability or step.get("node_id") or "مرحله"))

        summary_cards = [
            {
                "id": "candidate_count",
                "label": "کل ملک‌ها",
                "value": summary.get("candidate_count", 0),
                "tone": "neutral",
                "icon": "⌂",
            },
            {
                "id": "eligible_count",
                "label": "واجد شرایط",
                "value": summary.get("eligible_count", 0),
                "tone": "success",
                "icon": "✓",
            },
            {
                "id": "rejected_count",
                "label": "رد شده",
                "value": summary.get("rejected_count", 0),
                "tone": "warning",
                "icon": "!",
            },
            {
                "id": "top_property",
                "label": "بهترین گزینه",
                "value": summary.get("top_property") or "—",
                "tone": "primary",
                "icon": "★",
            },
            {
                "id": "top_score",
                "label": "امتیاز برتر",
                "value": summary.get("top_score") if summary.get("top_score") is not None else "—",
                "tone": "primary",
                "icon": "↗",
            },
        ]

        inspector_outputs: list[dict[str, Any]] = []

        for vector in outputs.get("vectors") or []:
            if not isinstance(vector, dict):
                continue
            inspector_outputs.append(
                {
                    "id": vector.get("id") or vector.get("name"),
                    "type": "vector",
                    "label": vector.get("label") or vector.get("name") or vector.get("id") or "Vector layer",
                    "name": vector.get("name") or vector.get("id"),
                    "role": vector.get("role") or "map_layer",
                    "format": vector.get("format") or "geojson",
                    "count": _feature_count_from_vector(vector),
                    "source": "outputs.vectors",
                }
            )

        for table in outputs.get("tables") or []:
            if not isinstance(table, dict):
                continue
            inspector_outputs.append(
                {
                    "id": table.get("id") or table.get("name"),
                    "type": "table",
                    "label": table.get("label") or table.get("name") or table.get("id") or "Table",
                    "name": table.get("name") or table.get("id"),
                    "role": table.get("role") or "table",
                    "format": "table",
                    "count": _row_count_from_table(table),
                    "source": "outputs.tables",
                }
            )

        for report_item in outputs.get("reports") or []:
            if not isinstance(report_item, dict):
                continue
            inspector_outputs.append(
                {
                    "id": report_item.get("id") or report_item.get("name"),
                    "type": "report",
                    "label": report_item.get("label") or report_item.get("name") or report_item.get("id") or "Report",
                    "name": report_item.get("name") or report_item.get("id"),
                    "role": report_item.get("role") or "analysis_report",
                    "format": report_item.get("format") or "json",
                    "count": 1,
                    "source": "outputs.reports",
                }
            )

        inspector_documents: list[dict[str, Any]] = []
        primary_actions: list[dict[str, Any]] = []

        for doc in documents or []:
            if not isinstance(doc, dict):
                continue

            doc_id = doc.get("id") or doc.get("name") or f"document_{len(inspector_documents) + 1}"
            doc_format = doc.get("format") or "document"
            doc_path = (
                doc.get("download_url")
                or doc.get("preview_url")
                or doc.get("url")
                or doc.get("path")
                or doc.get("file_path")
            )

            normalized_doc = {
                "id": doc_id,
                "type": "document",
                "label": doc.get("label") or doc.get("name") or ("گزارش PDF" if doc_format == "pdf" else "سند گزارش"),
                "name": doc.get("name") or doc_id,
                "role": doc.get("role") or "document",
                "format": doc_format,
                "mime_type": doc.get("mime_type"),
                "path": doc_path,
                "file_path": doc.get("file_path"),
                "download_url": doc.get("download_url"),
                "preview_url": doc.get("preview_url"),
                "size_bytes": doc.get("size_bytes"),
                "source": "outputs.documents",
            }
            inspector_documents.append(normalized_doc)
            inspector_outputs.append(
                {
                    "id": doc_id,
                    "type": "document",
                    "label": normalized_doc["label"],
                    "name": normalized_doc["name"],
                    "role": normalized_doc["role"],
                    "format": normalized_doc["format"],
                    "path": normalized_doc["path"],
                    "download_url": normalized_doc.get("download_url"),
                    "preview_url": normalized_doc.get("preview_url"),
                    "count": 1,
                    "source": "outputs.documents",
                }
            )

            if doc_path:
                action_label = "دانلود گزارش PDF" if doc_format == "pdf" else "مشاهده سند گزارش"
                primary_actions.append(
                    {
                        "id": "download_pdf" if doc_format == "pdf" else f"open_{doc_id}",
                        "label": action_label,
                        "type": "download" if doc_format == "pdf" else "open",
                        "target_output_id": doc_id,
                        "path": doc_path,
                        "download_url": doc.get("download_url"),
                        "preview_url": doc.get("preview_url"),
                        "mime_type": doc.get("mime_type"),
                    }
                )

        inspector_layers: list[dict[str, Any]] = []
        for layer in layers or []:
            if not isinstance(layer, dict):
                continue
            inspector_layers.append(
                {
                    "id": layer.get("id") or layer.get("name"),
                    "label": layer.get("label") or layer.get("name") or layer.get("id") or "Layer",
                    "name": layer.get("name") or layer.get("id"),
                    "type": layer.get("type") or "vector",
                    "format": layer.get("format") or "geojson",
                    "visible": layer.get("visible", True),
                    "count": _feature_count_from_vector(layer),
                }
            )

        inspector_trace: list[dict[str, Any]] = []
        for step in trace or []:
            if not isinstance(step, dict):
                continue
            inspector_trace.append(
                {
                    "order": step.get("order"),
                    "id": step.get("node_id") or step.get("capability_name"),
                    "label": _trace_label(step),
                    "capability_name": step.get("capability_name"),
                    "plugin_id": step.get("plugin_id"),
                    "output_kind": step.get("output_kind"),
                    "status": step.get("status") or "unknown",
                    "artifact_id": step.get("artifact_id"),
                    "path": step.get("path"),
                    "errors": step.get("errors") or ([] if not step.get("error") else [step.get("error")]),
                }
            )

        return {
            "kind": "analysis_inspector",
            "schema_version": "1.0",
            "domain": "real_estate_spatial_ranking",
            "title": title,
            "status": status,
            "language": "fa",
            "summary_cards": summary_cards,
            "outputs": inspector_outputs,
            "tables": outputs.get("tables") or [],
            "documents": inspector_documents,
            "layers": inspector_layers,
            "trace": inspector_trace,
            "primary_actions": primary_actions,
            "warnings": warnings or [],
            "tabs": [
                {"id": "summary", "label": "خلاصه", "count": len(summary_cards)},
                {"id": "outputs", "label": "خروجی‌ها", "count": len(inspector_outputs)},
                {"id": "tables", "label": "جداول", "count": len(outputs.get("tables") or [])},
                {"id": "documents", "label": "اسناد", "count": len(inspector_documents)},
                {"id": "layers", "label": "لایه‌ها", "count": len(inspector_layers)},
                {"id": "trace", "label": "فرآیند", "count": len(inspector_trace)},
            ],
        }

    def _try_handle_real_estate_ranking_directly(
        self,
        *,
        query: str,
        inputs: dict[str, Any] | None,
        request_id: str | None = None,
        llm_intent: Any = None,
    ) -> dict[str, Any] | None:
        if not self._looks_like_real_estate_ranking_query(query):
            return None

        feature_collection = self._extract_property_feature_collection_from_inputs(inputs)
        if not isinstance(feature_collection, dict):
            return None

        spatial_context = self._extract_real_estate_spatial_context_from_inputs(inputs)
        feature_collection, spatial_enrichment_summary = (
            self._enrich_property_feature_collection_with_spatial_context(
                feature_collection,
                spatial_context,
            )
        )

        features = feature_collection.get("features") or []
        if not isinstance(features, list):
            features = []

        ranked_features: list[dict[str, Any]] = []
        rejected_rows: list[dict[str, Any]] = []

        for feature in features:
            if not isinstance(feature, dict):
                continue

            props = dict(feature.get("properties") or {})
            eligible, rejection_reasons, metrics = self._evaluate_real_estate_eligibility(props)
            score, score_details = self._score_real_estate_property(props)

            enriched_props = dict(props)
            enriched_props.update(
                {
                    "eligible": eligible,
                    "eligibility_reasons": rejection_reasons,
                    "score": score,
                    "score_details": score_details,
                    "best_poi_distance_m": metrics.get("best_poi_distance_m"),
                    "risk_summary": metrics.get("risk_levels"),
                }
            )

            enriched_feature = {
                "type": "Feature",
                "geometry": feature.get("geometry"),
                "properties": enriched_props,
            }

            if eligible:
                ranked_features.append(enriched_feature)
            else:
                rejected_rows.append(
                    {
                        "id": props.get("id"),
                        "name": props.get("name"),
                        "score": score,
                        "reasons": rejection_reasons,
                    }
                )

        ranked_features.sort(
            key=lambda f: float((f.get("properties") or {}).get("score") or 0),
            reverse=True,
        )

        table_rows: list[dict[str, Any]] = []
        for idx, feature in enumerate(ranked_features, start=1):
            props = feature.get("properties") or {}
            props["rank"] = idx

            table_rows.append(
                {
                    "rank": idx,
                    "id": props.get("id"),
                    "name": props.get("name"),
                    "kind": props.get("kind") or props.get("property_type"),
                    "price": props.get("price"),
                    "score": props.get("score"),
                    "best_poi_distance_m": props.get("best_poi_distance_m"),
                    "distance_to_metro_m": props.get("distance_to_metro_m"),
                    "distance_to_mall_m": props.get("distance_to_mall_m"),
                    "distance_to_main_road_m": props.get("distance_to_main_road_m"),
                    "flood_risk": props.get("flood_risk"),
                    "earthquake_risk": props.get("earthquake_risk"),
                    "fire_risk": props.get("fire_risk"),
                    "in_allowed_zone": (
                        props.get("in_allowed_zone")
                        if props.get("in_allowed_zone") is not None
                        else props.get("build_zone_allowed")
                        if props.get("build_zone_allowed") is not None
                        else props.get("construction_allowed")
                    ),
                }
            )

        ranked_geojson = {
            "type": "FeatureCollection",
            "features": ranked_features,
        }

        top_row = table_rows[0] if table_rows else None

        summary = {
            "candidate_count": len(features),
            "eligible_count": len(ranked_features),
            "rejected_count": len(rejected_rows),
            "top_property": top_row.get("name") if top_row else None,
            "top_score": top_row.get("score") if top_row else None,
            "criteria": {
                "max_distance_to_metro_or_mall_m": 500,
                "max_distance_to_main_road_m": 150,
                "excluded_risk_level": "high",
                "medium_risk_policy": "allowed_with_score_penalty",
                "requires_allowed_construction_zone": True,
            },
        }

        if spatial_enrichment_summary.get("applied"):
            summary["spatial_enrichment"] = spatial_enrichment_summary

        report = {
            "title": "گزارش رتبه‌بندی و تحلیل سرمایه‌گذاری املاک",
            "language": "fa",
            "summary": summary,
            "ranking": table_rows,
            "rejected": rejected_rows,
            "notes": [
                "املاک با ریسک high یا خارج از محدوده مجاز ساخت‌وساز حذف شده‌اند.",
                "ریسک medium در MVP حذف نشده و به‌صورت جریمه امتیازی اعمال شده است.",
                "امتیاز نهایی بر اساس نزدیکی به مترو/مرکز خرید، خیابان اصلی، ریسک‌ها، محدوده مجاز و قیمت محاسبه شده است.",
            ],
        }

        message = (
            f"رتبه‌بندی املاک انجام شد. از {len(features)} ملک، "
            f"{len(ranked_features)} ملک واجد شرایط بودند."
        )
        if top_row:
            message += f" بهترین گزینه: {top_row.get('name')} با امتیاز {top_row.get('score')}."

        rid = request_id or f"req-{uuid.uuid4()}"

        documents, document_warnings, render_pdf_trace_step = self._try_render_real_estate_ranking_document(
            report=report,
            table_rows=table_rows,
            ranked_geojson=ranked_geojson,
            summary=summary,
            request_id=rid,
        )

        outputs = {
            "vectors": [
                {
                    "id": "ranked_properties",
                    "name": "ranked_properties",
                    "format": "geojson",
                    "role": "map_layer",
                    "geojson": ranked_geojson,
                    "summary": summary,
                }
            ],
            "rasters": [],
            "tables": [
                {
                    "id": "property_ranking",
                    "name": "property_ranking",
                    "role": "ranking_table",
                    "columns": [
                        "rank",
                        "id",
                        "name",
                        "kind",
                        "price",
                        "score",
                        "best_poi_distance_m",
                        "distance_to_main_road_m",
                        "flood_risk",
                        "earthquake_risk",
                        "fire_risk",
                        "in_allowed_zone",
                    ],
                    "rows": table_rows,
                },
                {
                    "id": "rejected_properties",
                    "name": "rejected_properties",
                    "role": "rejected_items",
                    "columns": ["id", "name", "score", "reasons"],
                    "rows": rejected_rows,
                },
            ],
            "reports": [
                {
                    "id": "real_estate_ranking_report",
                    "name": "real_estate_ranking_report",
                    "format": "json",
                    "role": "analysis_report",
                    "data": report,
                }
            ],
            "documents": documents,
        }

        layers = [
            {
                "id": "ranked_properties",
                "name": "املاک رتبه‌بندی‌شده",
                "type": "vector",
                "format": "geojson",
                "visible": True,
                "geojson": ranked_geojson,
                "summary": summary,
            }
        ]

        trace = [
            {
                "order": 1,
                "node_id": "node_001_filter_features",
                "capability_name": "filter_features",
                "plugin_id": "real_estate_ranking_bridge",
                "output_kind": "vector",
                "status": "success",
            },
            {
                "order": 2,
                "node_id": "node_002_score_features",
                "capability_name": "score_features",
                "plugin_id": "real_estate_ranking_bridge",
                "output_kind": "vector",
                "status": "success",
            },
            {
                "order": 3,
                "node_id": "node_003_rank_features",
                "capability_name": "rank_features",
                "plugin_id": "real_estate_ranking_bridge",
                "output_kind": "table",
                "status": "success",
            },
            {
                "order": 4,
                "node_id": "node_004_build_report",
                "capability_name": "build_report",
                "plugin_id": "real_estate_ranking_bridge",
                "output_kind": "json",
                "status": "success",
            },
            render_pdf_trace_step,
        ]

        if spatial_enrichment_summary.get("applied"):
            trace.insert(
                0,
                {
                    "order": 0,
                    "node_id": "node_000_spatial_enrichment",
                    "capability_name": "feature_enrichment",
                    "plugin_id": "real_estate_spatial_enrichment",
                    "output_kind": "vector",
                    "status": "success",
                    "metrics": spatial_enrichment_summary,
                },
            )

        inspector = self._build_real_estate_analysis_inspector(
            title=report.get("title") or "گزارش رتبه‌بندی املاک",
            status="succeeded",
            summary=summary,
            outputs=outputs,
            layers=layers,
            trace=trace,
            documents=documents,
            warnings=document_warnings,
        )

        return {
            "ok": True,
            "status": "succeeded",
            "request_id": rid,
            "query": query,
            "answer": message,
            "message": message,
            "summary": summary,
            "inspector": inspector,
            "outputs": outputs,
            "layers": layers,
            "result": {
                "type": "real_estate_ranking",
                "summary": summary,
                "ranking": table_rows,
                "rejected": rejected_rows,
                "report": report,
                "layer_ids": ["ranked_properties"],
            },
            "warnings": document_warnings,
            "next_actions": [
                "برای تحلیل دقیق‌تر، فاصله‌ها می‌توانند با pluginهای nearest_neighbor و distance_calculator از لایه‌های واقعی محاسبه شوند.",
                "در صورت نیاز، خروجی PDF/HTML گزارش از outputs.documents قابل استفاده است.",
            ],
            "metadata": {
                "service": "OrchestratorService",
                "weighted_router": True,
                "llm_planning_enabled": self._llm_planning_enabled(),
                "llm_intent": llm_intent,
                "execution_mode": "real_estate_ranking_bridge",
                "capabilities": [
                    "filter_features",
                    "score_features",
                    "rank_features",
                    "build_report",
                    "render_pdf",
                ],
            },
            "audit_record": {
                "status": "success",
                "execution_mode": "real_estate_ranking_bridge",
                "reason": "real estate ranking query with property features routed through MVP ranking bridge",
                "query": query,
                "request_id": rid,
                "capabilities": [
                    "filter_features",
                    "score_features",
                    "rank_features",
                    "build_report",
                    "render_pdf",
                ],
                "trace": trace,
                "outputs": {
                    "summary": summary,
                    "ranking_table_id": "property_ranking",
                    "layer_ids": ["ranked_properties"],
                    "report_id": "real_estate_ranking_report",
                    "document_ids": [doc.get("id") for doc in documents],
                },
            },
        }

    @staticmethod
    def _new_request_id() -> str:
        return f"req-{uuid.uuid4()}"

    def _resolve_input_references(
        self,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Resolve uploaded input references through UploadReferenceResolver.

        Supported:
            {"raster_ref": "upl-..."}
            {"vector_ref": "upl-..."}
            {"raster": {"upload_id": "upl-..."}}
            {"vector": {"upload_id": "upl-..."}}
        """
        try:
            return self.upload_reference_resolver.resolve_inputs(inputs)
        except UploadReferenceResolverError as exc:
            raise _service_error_from_exception(
                exc,
                stage="resolve_input_references",
            ) from exc

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
