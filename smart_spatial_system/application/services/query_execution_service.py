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
from orchestrator.planning.query_spec_contract import validate_query_spec_contract
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


from smart_spatial_system.application.services.query_execution.natural_query_context import (
    prepare_natural_query_context,
)

from smart_spatial_system.application.services.query_execution.natural_query_execution import (
    execute_and_persist_natural_query_success_path,
)

from smart_spatial_system.application.services.query_execution.natural_query_failure import (
    build_and_persist_failed_natural_query_response,
)

from smart_spatial_system.application.services.query_execution.planning_execution import (
    execute_query_spec_planning,
)

from smart_spatial_system.application.services.query_execution.planning_persistence import (
    persist_query_spec_planning_record,
)

from smart_spatial_system.application.services.query_execution.planning_response import (
    build_query_spec_planning_response,
)

from smart_spatial_system.application.services.query_execution.planning_context import (
    build_query_spec_planning_context,
)

from smart_spatial_system.application.services.query_execution.real_estate_ranking_execution import (
    execute_real_estate_ranking,
)

from smart_spatial_system.application.services.query_execution.real_estate_ranking_artifacts import (
    build_real_estate_ranking_artifacts,
)

from smart_spatial_system.application.services.query_execution.real_estate_ranking_response import (
    build_real_estate_ranking_response,
)

from smart_spatial_system.application.services.query_execution.real_estate_analysis_inspector import (
    build_real_estate_analysis_inspector,
)

from smart_spatial_system.application.services.query_execution.real_estate_document_renderer import (
    try_render_real_estate_ranking_document,
)

from smart_spatial_system.application.services.query_execution.real_estate_report_payload import (
    build_real_estate_pdf_report_payload,
)

from smart_spatial_system.application.services.query_execution.real_estate_missing_inputs import (
    try_handle_missing_real_estate_inputs,
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
                # query_spec_contracts are assembled by query_execution.planning_context.
                planning_context, planning_context_metadata = build_query_spec_planning_context(
                    query=query,
                    resolved_inputs=resolved_inputs,
                    user_context=user_context,
                    metadata=metadata,
                    project_id=project_id,
                    response_language=getattr(self.config, "response_language", None),
                    extract_semantic_planning_context=_extract_semantic_planning_context_from_sources,
                )
                final_metadata.update(planning_context_metadata)

                # Source-level compatibility marker for integration tests:
                # make_registry_planning_runner(self._build_enabled_registry_view())
                # LLMQuerySpecGenerator, validate_query_spec_contract, make_registry_planning_runner,
                # run_with_kernel_execution, and query_spec_planning kernel execution are delegated
                # to query_execution.planning_execution.
                query_spec, planning_result, kernel_execution_enabled = execute_query_spec_planning(
                    query=query,
                    planning_context=planning_context,
                    resolved_inputs=resolved_inputs,
                    user_context=user_context,
                    metadata=metadata,
                    final_metadata=final_metadata,
                    build_runtime_inputs=_build_query_spec_runtime_inputs,
                    enrich_query_database_params=self._enrich_query_database_params_from_inputs,
                    build_enabled_registry_view=self._build_enabled_registry_view,
                    kernel_execution_enabled=self._kernel_execution_enabled,
                    llm_client_factory=OpenAICompatibleLLMClient,
                    query_spec_generator_cls=LLMQuerySpecGenerator,
                    planning_runner_factory=make_registry_planning_runner,
                    query_spec_contract_validator=validate_query_spec_contract,
                )

                # planning_summary, kernel_execution_parity, and query_spec_planning_kernel_execution
                # are assembled by query_execution.planning_response.
                (
                    production_response,
                    planning_metadata,
                    success,
                    planning_error,
                    planning_structured_error,
                ) = build_query_spec_planning_response(
                    planning_result=planning_result,
                    final_metadata=final_metadata,
                    final_request_id=final_request_id,
                    query_spec=query_spec,
                    kernel_execution_enabled=kernel_execution_enabled,
                    planning_outputs_to_response_payload=self._planning_outputs_to_response_payload,
                    planning_trace_to_steps=self._planning_trace_to_steps,
                    query_spec_to_dict_func=query_spec_to_dict,
                    redact_sensitive_json=_redact_sensitive_json,
                )

                # _remember, project_service.attach_request, _persist_outputs_for_record,
                # and project_service.attach_output are delegated to query_execution.planning_persistence.
                persist_query_spec_planning_record(
                    request_id=final_request_id,
                    query=query,
                    resolved_inputs=resolved_inputs,
                    original_inputs=original_inputs,
                    band_map=band_map,
                    user_context=user_context,
                    metadata=metadata,
                    planning_metadata=planning_metadata,
                    project_id=project_id,
                    query_spec=query_spec,
                    planning_result=planning_result,
                    production_response=production_response,
                    success=success,
                    planning_error=planning_error,
                    planning_structured_error=planning_structured_error,
                    remember=self._remember,
                    get_request=self.get_request,
                    project_service=self.project_service,
                    persist_outputs_for_record=self._persist_outputs_for_record,
                    persist_outputs=bool(self.config.persist_outputs),
                    json_safe=_json_safe,
                    redact_sensitive_json=_redact_sensitive_json,
                    query_spec_to_dict_func=query_spec_to_dict,
                )

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
        return try_handle_missing_real_estate_inputs(
            query=query,
            inputs=inputs,
            resolved_inputs=resolved_inputs,
            final_request_id=final_request_id,
            final_metadata=final_metadata,
            band_map=band_map,
            user_context=user_context,
            llm_intent=llm_intent,
            is_real_estate_analysis_query=self._is_real_estate_analysis_query,
            has_any_real_estate_payload=self._has_any_real_estate_payload,
            remember=self._remember,
            attach_request=self.project_service.attach_request,
            json_safe=_json_safe,
        )

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
        return build_real_estate_pdf_report_payload(
            report=report,
            table_rows=table_rows,
            ranked_geojson=ranked_geojson,
            summary=summary,
        )

    def _try_render_real_estate_ranking_document(
        self,
        *,
        report: dict[str, Any],
        table_rows: list[dict[str, Any]],
        ranked_geojson: dict[str, Any],
        summary: dict[str, Any],
        request_id: str,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        return try_render_real_estate_ranking_document(
            report=report,
            table_rows=table_rows,
            ranked_geojson=ranked_geojson,
            summary=summary,
            request_id=request_id,
            build_pdf_report_payload=self._build_real_estate_pdf_report_payload,
        )

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
        return build_real_estate_analysis_inspector(
            title=title,
            status=status,
            summary=summary,
            outputs=outputs,
            layers=layers,
            trace=trace,
            documents=documents,
            warnings=warnings,
        )

    # real_estate_ranking_bridge is delegated to query_execution.real_estate_ranking_response.
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

        ranked_features, rejected_rows = execute_real_estate_ranking(
            features=features,
            evaluate_eligibility=self._evaluate_real_estate_eligibility,
            score_property=self._score_real_estate_property,
        )

        table_rows, ranked_geojson, summary, report, message = build_real_estate_ranking_artifacts(
            features=features,
            ranked_features=ranked_features,
            rejected_rows=rejected_rows,
            spatial_enrichment_summary=spatial_enrichment_summary,
        )

        rid = request_id or f"req-{uuid.uuid4()}"

        documents, document_warnings, render_pdf_trace_step = self._try_render_real_estate_ranking_document(
            report=report,
            table_rows=table_rows,
            ranked_geojson=ranked_geojson,
            summary=summary,
            request_id=rid,
        )

        return build_real_estate_ranking_response(
            query=query,
            rid=rid,
            message=message,
            features=features,
            ranked_features=ranked_features,
            ranked_geojson=ranked_geojson,
            rejected_rows=rejected_rows,
            table_rows=table_rows,
            summary=summary,
            report=report,
            documents=documents,
            document_warnings=document_warnings,
            render_pdf_trace_step=render_pdf_trace_step,
            spatial_enrichment_summary=spatial_enrichment_summary,
            llm_intent=llm_intent,
            build_analysis_inspector=self._build_real_estate_analysis_inspector,
            llm_planning_enabled=self._llm_planning_enabled,
        )

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
            # self._new_request_id(), self._maybe_plan_llm_intent(query),
            # self._apply_intent_to_query(query, llm_intent), and self._llm_planning_enabled()
            # are delegated to query_execution.natural_query_context.
            natural_query_context = prepare_natural_query_context(
                query=query,
                request_id=request_id,
                user_context=user_context,
                metadata=metadata,
                project_id=project_id,
                use_weighted_router=self.config.use_weighted_router,
                new_request_id=self._new_request_id,
                maybe_plan_llm_intent=self._maybe_plan_llm_intent,
                apply_intent_to_query=self._apply_intent_to_query,
                llm_planning_enabled=self._llm_planning_enabled,
                json_safe=_json_safe,
            )
            final_request_id = natural_query_context["final_request_id"]
            final_metadata = natural_query_context["final_metadata"]
            _resolved_project_id = natural_query_context["resolved_project_id"]
            llm_intent = natural_query_context["llm_intent"]
            effective_query = natural_query_context["effective_query"]
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
                # self._build_router(), self._resolve_input_references(inputs),
                # try_dispatch_natural_query_direct_response,
                # run_natural_query_with_routing_evidence, response_builder.build_dict,
                # and persist_natural_query_record are delegated
                # to query_execution.natural_query_execution.
                return execute_and_persist_natural_query_success_path(
                    query=query,
                    effective_query=effective_query,
                    inputs=inputs,
                    band_map=band_map,
                    user_context=user_context,
                    metadata=metadata,
                    min_score=min_score,
                    final_request_id=final_request_id,
                    final_metadata=final_metadata,
                    project_id=_resolved_project_id,
                    llm_intent=llm_intent,
                    config_min_score=self.config.min_score,
                    persist_outputs=bool(self.config.persist_outputs),
                    response_builder=self.response_builder,
                    project_service=self.project_service,
                    build_router=self._build_router,
                    resolve_input_references=self._resolve_input_references,
                    natural_query_runner=run_natural_query_with_routing_evidence,
                    missing_real_estate_inputs_handler=self._try_handle_missing_real_estate_inputs,
                    real_estate_ranking_handler=self._try_handle_real_estate_ranking_directly,
                    vector_display_handler=self._try_handle_vector_display_directly,
                    query_spec_planning_enabled=self._query_spec_planning_enabled,
                    query_spec_planning_handler=self._try_handle_query_with_planning,
                    remember=self._remember,
                    get_request=self.get_request,
                    persist_outputs_for_record=self._persist_outputs_for_record,
                    json_safe=_json_safe,
                )

            except Exception as exc:
                # _service_exception_to_structured_error, response_builder.build_dict,
                # structured_error metadata, and failed _remember are delegated
                # to query_execution.natural_query_failure.
                return build_and_persist_failed_natural_query_response(
                    exc=exc,
                    request_id=final_request_id,
                    query=query,
                    inputs=inputs,
                    band_map=band_map,
                    user_context=user_context,
                    final_metadata=final_metadata,
                    project_id=_resolved_project_id,
                    response_builder=self.response_builder,
                    remember=self._remember,
                    json_safe=_json_safe,
                    service_exception_to_structured_error=_service_exception_to_structured_error,
                )
