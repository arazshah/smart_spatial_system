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


class OrchestratorService:
    """
    Operational service facade for the Smart Spatial System.

    Public methods:
        - handle_query(...)
        - submit_feedback(...)
        - approve_and_apply_proposal(...)
        - get_request(...)
        - get_health()
        - get_weights()
    """

    def __init__(
        self,
        config: OrchestratorServiceConfig | None = None,
        *,
        weight_store: InMemoryRouterWeightStore | None = None,
    ) -> None:
        self.config = config or OrchestratorServiceConfig()

        self.runtime_paths = RuntimePaths.from_env(self.config.runtime_dir)
        output_root = (
            self.config.outputs_path
            if self.config.outputs_path is not None
            else self.runtime_paths.outputs
        )
        upload_root = (
            self.config.uploads_path
            if self.config.uploads_path is not None
            else self.runtime_paths.uploads
        )
        project_root = (
            self.config.projects_path
            if self.config.projects_path is not None
            else self.runtime_paths.projects
        )

        self.registry = CapabilityRegistry.from_plugin_modules(
            self.config.plugin_modules,
            tolerant=True,
        )

        self.plugin_state_store = PluginStateStore(
            PluginStateStoreConfig(
                path="config/plugin_state.json",
            )
        )

        self.persistence = RouterWeightStorePersistence(
            WeightStorePersistenceConfig(
                path=self.config.weights_path,
            )
        )

        self.weight_store = weight_store or self._load_weight_store()

        self.map_layer_builder = MapLayerBuilder()
        self.map_layer_service = MapLayerService(
            self.get_request,
            self.map_layer_builder,
        )

        self.output_storage = OutputStorage(
            OutputStorageConfig(
                root_dir=output_root,
            )
        )
        self.output_service = OutputService(self.output_storage)

        self.upload_storage = UploadStorage(
            UploadStorageConfig(
                root_dir=upload_root,
            )
        )
        self.upload_service = UploadService(self.upload_storage)

        self.project_store = ProjectStore(
            ProjectStoreConfig(
                root_dir=project_root,
            )
        )

        self.project_service = ProjectService(self.project_store)
        self.data_source_service = DataSourceService(
            self.project_service,
            self.upload_service,
        )

        self.upload_reference_resolver = UploadReferenceResolver(
            self.upload_storage,
            UploadReferenceResolverConfig(
                raster_loader_plugin_module=self.config.raster_loader_plugin_module,
                vector_loader_plugin_module=self.config.vector_loader_plugin_module,
                use_plugins=self.config.resolve_upload_refs_with_plugins,
                allow_json_fallback=True,
                prefer_plugin_for_json=False,
                enforce_loader_contract=self.config.enforce_loader_contract,
                allow_adaptive_loader_fallback=self.config.allow_adaptive_loader_fallback,
            ),
        )

        self.response_builder = ProductionResponseBuilder(
            ProductionResponseConfig(
                language=self.config.response_language,
                include_debug=self.config.include_response_debug,
            )
        )

        self.feedback_collector = FeedbackCollector()
        self.learning_signal_builder = RouterLearningSignalBuilder()
        self.weight_proposal_engine = RouterWeightProposalEngine()
        self.weight_proposal_collector = RouterWeightProposalCollector()

        self._history: dict[str, dict[str, Any]] = {}

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
        except OrchestratorServiceError:
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

    @staticmethod
    def _is_vector_display_query(
        query: str,
        intent: dict[str, Any] | None = None,
    ) -> bool:
        """
        Detect simple vector-display queries.

        This is intentionally deterministic and does not depend on LLM.
        """
        q = str(query or "").strip().lower()

        display_tokens = [
            "نمایش",
            "نشان بده",
            "نشان بدهد",
            "روی نقشه",
            "نقشه",
            "display",
            "show",
            "render",
            "draw",
        ]

        vector_tokens = [
            "نقطه",
            "نقاط",
            "عارضه",
            "عوارض",
            "وکتور",
            "برداری",
            "geojson",
            "feature",
            "features",
            "point",
            "points",
            "vector",
            "layer",
            "لایه",
        ]

        has_display = any(token in q for token in display_tokens)
        has_vector = any(token in q for token in vector_tokens)

        if has_display and has_vector:
            return True

        if isinstance(intent, dict):
            name = str(intent.get("intent_name") or "").lower()
            required = intent.get("required_inputs") or {}
            output = intent.get("output_expectation") or {}
            preferred = intent.get("preferred_capabilities") or []

            if (
                name in {"vector_display", "vector_filter", "unknown"}
                and bool(required.get("vector", False))
                and not bool(required.get("raster", False))
                and bool(output.get("map_layer", False))
            ):
                return True

            if (
                bool(required.get("vector", False))
                and not bool(required.get("raster", False))
                and any(
                    str(cap) in {"filter_features", "extract_centroids", "export_vector_geojson"}
                    for cap in preferred
                )
                and bool(output.get("map_layer", False))
            ):
                return True

        return False

    @staticmethod
    def _is_vector_summary_query(
        query: str,
        intent: dict[str, Any] | None = None,
    ) -> bool:
        """
        Detect vector inspection / feature-count / summary queries.

        Examples:
        - لایه وکتور را بررسی کن و تعداد عارضه‌ها را گزارش بده
        - چند نقطه داخل فایل است؟
        - تعداد عارضه‌های فایل را بگو
        - summarize vector layer
        """
        q = str(query or "").strip().lower()

        summary_tokens = [
            "تعداد",
            "چند",
            "گزارش",
            "گزارش بده",
            "بررسی",
            "خلاصه",
            "آمار",
            "شمارش",
            "بشمار",
            "count",
            "summary",
            "summarize",
            "inspect",
            "report",
            "statistics",
            "stats",
        ]

        vector_tokens = [
            "نقطه",
            "نقاط",
            "عارضه",
            "عوارض",
            "وکتور",
            "برداری",
            "geojson",
            "feature",
            "features",
            "point",
            "points",
            "vector",
            "layer",
            "لایه",
            "فایل",
        ]

        has_summary = any(token in q for token in summary_tokens)
        has_vector = any(token in q for token in vector_tokens)

        if has_summary and has_vector:
            return True

        if isinstance(intent, dict):
            name = str(intent.get("intent_name") or "").lower()
            required = intent.get("required_inputs") or {}
            output = intent.get("output_expectation") or {}

            if (
                name in {"vector_summary", "vector_inspect", "vector_statistics"}
                and bool(required.get("vector", False))
                and not bool(required.get("raster", False))
            ):
                return True

            if (
                bool(required.get("vector", False))
                and not bool(required.get("raster", False))
                and bool(output.get("text", False))
                and not bool(output.get("map_layer", False))
            ):
                return True

        return False

    @staticmethod
    def _read_geojson_path_if_possible(value: Any) -> dict[str, Any] | None:
        """
        Read a local GeoJSON-like path if value points to one.
        """
        try:
            from pathlib import Path
            import json

            if not isinstance(value, str):
                return None

            p = Path(value)

            if not p.exists() or not p.is_file():
                return None

            if p.suffix.lower() not in {".geojson", ".json"}:
                return None

            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                return data

        except Exception:
            return None

        return None

    @classmethod
    def _find_geojson_like(
        cls,
        obj: Any,
        *,
        max_depth: int = 8,
    ) -> dict[str, Any] | None:
        """
        Recursively find a GeoJSON FeatureCollection/Feature/Geometry in inputs.

        Handles common shapes:
        - {"type": "FeatureCollection", ...}
        - {"geojson": {...}}
        - {"payload": {...}}
        - {"data": {...}}
        - {"path": "/.../file.geojson"}
        - dataclass/object with __dict__
        """
        if max_depth < 0 or obj is None:
            return None

        if isinstance(obj, dict):
            geo_type = obj.get("type")

            if geo_type == "FeatureCollection":
                features = obj.get("features")
                if isinstance(features, list):
                    return obj

            if geo_type == "Feature":
                return {
                    "type": "FeatureCollection",
                    "features": [obj],
                }

            if geo_type in {
                "Point",
                "MultiPoint",
                "LineString",
                "MultiLineString",
                "Polygon",
                "MultiPolygon",
                "GeometryCollection",
            }:
                return {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {},
                            "geometry": obj,
                        }
                    ],
                }

            for path_key in [
                "path",
                "file_path",
                "local_path",
                "stored_path",
                "absolute_path",
                "source_path",
            ]:
                loaded = cls._read_geojson_path_if_possible(obj.get(path_key))
                if loaded is not None:
                    found = cls._find_geojson_like(loaded, max_depth=max_depth - 1)
                    if found is not None:
                        return found

            priority_keys = [
                "geojson",
                "feature_collection",
                "payload",
                "data",
                "vector",
                "vector_data",
                "content",
                "result",
                "output",
                "outputs",
                "inputs",
                "active_data",
            ]

            for key in priority_keys:
                if key in obj:
                    found = cls._find_geojson_like(obj.get(key), max_depth=max_depth - 1)
                    if found is not None:
                        return found

            for value in obj.values():
                found = cls._find_geojson_like(value, max_depth=max_depth - 1)
                if found is not None:
                    return found

        if isinstance(obj, list):
            for item in obj:
                found = cls._find_geojson_like(item, max_depth=max_depth - 1)
                if found is not None:
                    return found

        if isinstance(obj, str):
            loaded = cls._read_geojson_path_if_possible(obj)
            if loaded is not None:
                return cls._find_geojson_like(loaded, max_depth=max_depth - 1)

        if hasattr(obj, "__dict__"):
            try:
                return cls._find_geojson_like(vars(obj), max_depth=max_depth - 1)
            except Exception:
                return None

        return None

    @staticmethod
    def _summarize_feature_collection(
        feature_collection: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Lightweight summary for UI/debug.
        """
        raw_features = feature_collection.get("features") or []
        features = raw_features if isinstance(raw_features, list) else []

        geometry_counts: dict[str, int] = {}
        property_keys: set[str] = set()

        for feature in features:
            if not isinstance(feature, dict):
                continue

            geometry = feature.get("geometry") or {}
            geometry_type = geometry.get("type") if isinstance(geometry, dict) else None
            geometry_type = str(geometry_type or "Unknown")
            geometry_counts[geometry_type] = geometry_counts.get(geometry_type, 0) + 1

            properties = feature.get("properties")
            if isinstance(properties, dict):
                property_keys.update(str(key) for key in properties.keys())

        return {
            "feature_count": len(features),
            "geometry_counts": geometry_counts,
            "property_keys": sorted(property_keys),
        }

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
        """
        Capability-backed bridge for simple vector display/summary queries.

        Historical note:
            This method used to build the vector display/summary response directly
            inside the service. It now keeps only the lightweight query detection
            and delegates actual work to official capabilities:

                - inspect_vector
                - display_vector_layer
                - summarize_vector_layer

        This keeps backward-compatible frontend output while making execution
        auditable and capability-based.
        """
        # Do not let simple vector display/summary swallow real-estate
        # ranking/report/PDF queries. These must be handled by the
        # real-estate ranking/report pipeline.
        _normalized_query_for_vector_guard = str(query or "").lower()
        _real_estate_ranking_terms = (
            "ملک",
            "املاک",
            "امتیاز",
            "رتبه",
            "رتبه‌بندی",
            "رتبه بندی",
            "گزارش",
            "pdf",
            "پی دی اف",
            "جدول",
        )
        if (
            self._is_real_estate_analysis_query(query)
            and any(
                term in _normalized_query_for_vector_guard
                for term in _real_estate_ranking_terms
            )
        ):
            return None

        is_vector_display = self._is_vector_display_query(query, llm_intent)
        is_vector_summary = self._is_vector_summary_query(query, llm_intent)

        if not (is_vector_display or is_vector_summary):
            return None

        feature_collection = self._find_geojson_like(resolved_inputs)
        if feature_collection is None:
            feature_collection = self._find_geojson_like(inputs)

        if feature_collection is None:
            return None

        handler_name = "vector_summary" if is_vector_summary else "vector_display"
        target_capability = (
            "summarize_vector_layer"
            if is_vector_summary
            else "display_vector_layer"
        )

        router = self._build_enabled_router()

        inspect_binding = router.resolve("inspect_vector")
        target_binding = router.resolve(target_capability)

        trace: list[dict[str, Any]] = []

        inspection = inspect_binding.callable(
            vector=feature_collection,
        )

        trace.append(
            {
                "order": 1,
                "node_id": "node_001_inspect_vector",
                "capability_name": "inspect_vector",
                "plugin_id": inspect_binding.plugin_id,
                "output_kind": inspect_binding.output_kind,
                "status": "success",
            }
        )

        if is_vector_summary:
            capability_result = target_binding.callable(
                vector=feature_collection,
            )
        else:
            capability_result = target_binding.callable(
                vector=feature_collection,
                layer_id="active_vector",
                name="active_vector",
                visible=True,
            )

        trace.append(
            {
                "order": 2,
                "node_id": (
                    "node_002_summarize_vector_layer"
                    if is_vector_summary
                    else "node_002_display_vector_layer"
                ),
                "capability_name": target_capability,
                "plugin_id": target_binding.plugin_id,
                "output_kind": target_binding.output_kind,
                "status": "success",
            }
        )

        summary = (
            capability_result.get("summary")
            if isinstance(capability_result, dict)
            else None
        )

        if not isinstance(summary, dict):
            summary = (
                inspection.get("summary")
                if isinstance(inspection, dict)
                else {}
            )

        if not isinstance(summary, dict):
            summary = self._summarize_feature_collection(feature_collection)

        if is_vector_summary:
            feature_count = summary.get("feature_count", 0)
            geometry_counts = summary.get("geometry_counts", {})
            geometry_text = ", ".join(
                f"{key}: {value}"
                for key, value in geometry_counts.items()
            ) or "No geometries"

            message = (
                capability_result.get("message")
                if isinstance(capability_result, dict)
                else None
            ) or f"Vector layer contains {feature_count} features. {geometry_text}."

            result_payload = {
                "type": "vector_summary",
                "feature_count": feature_count,
                "geometry_counts": geometry_counts,
                "property_keys": summary.get("property_keys", []),
                "summary": summary,
                "capability_result": _json_safe(capability_result),
            }

            outputs = {
                "vectors": [
                    {
                        "id": "active_vector",
                        "name": "active_vector",
                        "format": "geojson",
                        "role": "map_layer",
                        "geojson": feature_collection,
                        "summary": summary,
                    }
                ],
                "rasters": [],
                "tables": [],
            }

            layers = [
                {
                    "id": "active_vector",
                    "name": "active_vector",
                    "type": "vector",
                    "format": "geojson",
                    "visible": True,
                    "geojson": feature_collection,
                    "summary": summary,
                }
            ]

        else:
            message = (
                capability_result.get("message")
                if isinstance(capability_result, dict)
                else None
            ) or "Vector layer is ready for map display."

            result_payload = {
                "type": "vector_display",
                "layer_ids": ["active_vector"],
                "feature_count": summary.get("feature_count", 0),
                "geometry_counts": summary.get("geometry_counts", {}),
                "property_keys": summary.get("property_keys", []),
                "summary": summary,
                "capability_result": _json_safe(capability_result),
            }

            if isinstance(capability_result, dict):
                outputs = capability_result.get("outputs") or {}
                layers = capability_result.get("layers") or []
            else:
                outputs = {}
                layers = []

            if not isinstance(outputs, dict) or "vectors" not in outputs:
                outputs = {
                    "vectors": [
                        {
                            "id": "active_vector",
                            "name": "active_vector",
                            "format": "geojson",
                            "role": "map_layer",
                            "geojson": feature_collection,
                            "summary": summary,
                        }
                    ],
                    "rasters": [],
                    "tables": [],
                }

            if not isinstance(layers, list) or not layers:
                layers = [
                    {
                        "id": "active_vector",
                        "name": "active_vector",
                        "type": "vector",
                        "format": "geojson",
                        "visible": True,
                        "geojson": feature_collection,
                        "summary": summary,
                    }
                ]

        metadata = dict(final_metadata)
        metadata["execution_mode"] = "capability_bridge"
        metadata["legacy_handler_name"] = handler_name
        metadata["original_query"] = query
        metadata["capabilities"] = {
            "inspection": "inspect_vector",
            "target": target_capability,
        }

        audit_record = {
            "status": "success",
            "execution_mode": "capability_bridge",
            "reason": "simple vector display/summary query routed through official capabilities",
            "query": query,
            "request_id": final_request_id,
            "legacy_handler_name": handler_name,
            "capabilities": [
                "inspect_vector",
                target_capability,
            ],
            "trace": trace,
            "outputs": {
                "summary": _json_safe(summary),
            },
        }

        run_result = {
            "status": "succeeded",
            "execution_mode": "capability_bridge",
            "legacy_handler_name": handler_name,
            "outputs": {
                "inspection": _json_safe(inspection),
                "result": _json_safe(capability_result),
            },
            "trace": trace,
            "audit_record": audit_record,
        }

        response = {
            "ok": True,
            "status": "succeeded",
            "request_id": final_request_id,
            "query": query,
            "message": message,
            "summary": summary,
            "metadata": _json_safe(metadata),
            "outputs": outputs,
            "layers": layers,
            "result": result_payload,
            "audit_record": audit_record,
        }

        self._remember(
            request_id=final_request_id,
            record={
                "request_id": final_request_id,
                "query": query,
                "inputs": _json_safe(resolved_inputs),
                "original_inputs": _json_safe(inputs),
                "band_map": _json_safe(band_map or {}),
                "user_context": _json_safe(user_context or {}),
                "metadata": _json_safe(metadata),
                "run_result": _json_safe(run_result),
                "audit_record": _json_safe(audit_record),
                "production_response": _json_safe(response),
            },
        )

        return _json_safe(response)

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
        """
        Answer simple system-status/runtime queries directly.

        This prevents general health/status questions from being routed into
        geospatial planning pipelines such as NDVI/raster workflows.
        """
        if not self._is_system_status_query(query, llm_intent):
            return None

        try:
            health = self.get_health()
        except Exception as exc:
            health = {
                "status": "unknown",
                "error": str(exc),
            }

        try:
            if hasattr(self, "get_runtime_diagnostics") and callable(self.get_runtime_diagnostics):
                runtime = self.get_runtime_diagnostics()
            else:
                capability_names = sorted(self._enabled_capability_names())

                plugin_ids: list[str] = []

                try:
                    bindings = getattr(self.registry, "bindings", None)

                    if callable(bindings):
                        bindings = bindings()

                    if isinstance(bindings, dict):
                        iterable = bindings.values()
                    elif bindings is None:
                        iterable = []
                    else:
                        iterable = bindings

                    for binding in iterable:
                        plugin_id = (
                            getattr(binding, "plugin_id", None)
                            or getattr(binding, "plugin_name", None)
                            or getattr(binding, "source_plugin", None)
                        )

                        if plugin_id:
                            plugin_ids.append(str(plugin_id))
                except Exception:
                    plugin_ids = []

                plugin_ids = sorted(set(plugin_ids))

                runtime = {
                    "llm": {
                        "provider": os.getenv("LLM_PROVIDER", "not_configured"),
                        "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL"),
                        "fast_model": os.getenv("LLM_FAST_MODEL"),
                        "strong_model": os.getenv("LLM_STRONG_MODEL"),
                        "default_model": os.getenv("LLM_DEFAULT_MODEL") or os.getenv("LLM_MODEL"),
                        "temperature": os.getenv("LLM_TEMPERATURE"),
                        "timeout_seconds": os.getenv("LLM_TIMEOUT_SECONDS"),
                        "api_key_configured": bool(
                            os.getenv("OPENAI_API_KEY")
                            or os.getenv("AVALAI_API_KEY")
                            or os.getenv("LLM_API_KEY")
                        ),
                    },
                    "plugins": {
                        "module_names": list(getattr(self.config, "plugin_modules", []) or []),
                        "plugin_ids": plugin_ids,
                        "capabilities": capability_names,
                        "capability_count": len(capability_names),
                        "enabled_capabilities": capability_names,
                        "enabled_capability_count": len(capability_names),
                        "disabled_plugin_ids": sorted(self._disabled_plugin_ids()),
                        "skipped_plugins": list(getattr(self.registry, "skipped_plugins", []) or []),
                    },
                    "runtime": {
                        "resolve_upload_refs_with_plugins": getattr(
                            self.config,
                            "resolve_upload_refs_with_plugins",
                            None,
                        ),
                        "raster_loader_plugin_module": getattr(
                            self.config,
                            "raster_loader_plugin_module",
                            None,
                        ),
                        "vector_loader_plugin_module": getattr(
                            self.config,
                            "vector_loader_plugin_module",
                            None,
                        ),
                    },
                }
        except Exception as exc:
            runtime = {
                "error": str(exc),
            }

        plugins = runtime.get("plugins", {}) if isinstance(runtime, dict) else {}
        llm = runtime.get("llm", {}) if isinstance(runtime, dict) else {}

        enabled_count = plugins.get("enabled_capability_count")
        plugin_ids = plugins.get("plugin_ids") or []
        module_names = plugins.get("module_names") or []
        plugin_count = len(plugin_ids) if plugin_ids else len(module_names)

        answer = (
            "سیستم فعال است و سرویس ارکستریتور آماده پاسخ‌گویی است. "
            f"تعداد قابلیت‌های فعال: {enabled_count if enabled_count is not None else 'نامشخص'}، "
            f"تعداد افزونه‌های بارگذاری‌شده: {plugin_count}. "
            "اتصال LLM نیز در تنظیمات runtime قابل بررسی است."
        )

        response = {
            "ok": True,
            "status": "succeeded",
            "request_id": final_request_id,
            "query": query,
            "answer": answer,
            "message": answer,
            "summary": {
                "service_status": health.get("status") if isinstance(health, dict) else None,
                "enabled_capability_count": enabled_count,
                "plugin_count": plugin_count,
                "llm_provider": llm.get("provider") if isinstance(llm, dict) else None,
                "llm_model": llm.get("default_model") if isinstance(llm, dict) else None,
                "llm_api_key_configured": llm.get("api_key_configured") if isinstance(llm, dict) else None,
            },
            "outputs": {},
            "layers": [],
            "result": {
                "type": "system_status",
                "health": health,
                "runtime": runtime,
            },
            "warnings": [],
            "next_actions": [
                "برای مشاهده جزئیات افزونه‌ها از بخش Plugin Manager استفاده کنید.",
                "برای تست اتصال LLM از مسیر /settings/llm/smoke-test استفاده کنید.",
            ],
            "metadata": _json_safe(final_metadata),
        }

        self._remember(
            request_id=final_request_id,
            record={
                "request_id": final_request_id,
                "query": query,
                "inputs": _json_safe(inputs),
                "band_map": _json_safe(band_map or {}),
                "user_context": _json_safe(user_context or {}),
                "metadata": _json_safe(final_metadata),
                "production_response": _json_safe(response),
            },
        )

        return _json_safe(response)

    def _is_system_status_query(
        self,
        query: str,
        llm_intent: Any | None = None,
    ) -> bool:
        text = str(query or "").strip().lower()

        if not text:
            return False

        intent_name = None

        if isinstance(llm_intent, dict):
            intent_name = str(llm_intent.get("intent_name") or "").lower()
        else:
            intent_name = str(getattr(llm_intent, "intent_name", "") or "").lower()

        system_tokens = [
            "وضعیت سیستم",
            "سلامت سیستم",
            "وضعیت سرویس",
            "سلامت سرویس",
            "سیستم را بررسی",
            "بررسی سیستم",
            "health",
            "system status",
            "service status",
            "runtime status",
        ]

        if any(token in text for token in system_tokens):
            return True

        if intent_name in {"system_status", "health_check", "runtime_status"}:
            return True

        return False

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
        text = str(query or "").strip().lower()

        if not text:
            return False

        intent_name = None

        if isinstance(llm_intent, dict):
            intent_name = str(llm_intent.get("intent_name") or "").lower()
        else:
            intent_name = str(getattr(llm_intent, "intent_name", "") or "").lower()

        real_estate_tokens = [
            "ملک",
            "املاک",
            "آپارتمان",
            "ویلا",
            "زمین",
            "ساخت و ساز",
            "ساخت‌وساز",
            "real estate",
            "property",
            "properties",
        ]

        analysis_tokens = [
            "مترو",
            "مرکز خرید",
            "خیابان اصلی",
            "ریسک",
            "سیل",
            "زلزله",
            "آتش",
            "امتیاز",
            "رتبه",
            "رتبه‌بندی",
            "گزارش",
            "نزدیک",
            "۵۰۰",
            "500",
        ]

        if intent_name in {
            "real_estate_ranking",
            "property_ranking",
            "vector_filter",
            "investment_analysis",
        }:
            return any(token in text for token in real_estate_tokens)

        return (
            any(token in text for token in real_estate_tokens)
            and any(token in text for token in analysis_tokens)
        )

    def _has_any_real_estate_payload(
        self,
        resolved_inputs: dict[str, Any],
    ) -> bool:
        if not isinstance(resolved_inputs, dict) or not resolved_inputs:
            return False

        useful_keys = {
            "vector",
            "vectors",
            "properties",
            "property_layer",
            "real_estate",
            "pois",
            "poi",
            "metro",
            "shopping_centers",
            "roads",
            "main_roads",
            "risk_layers",
            "flood_risk",
            "earthquake_risk",
            "fire_risk",
            "zoning",
            "landuse",
            "land_use",
        }

        if any(key in resolved_inputs and resolved_inputs.get(key) not in (None, {}, []) for key in useful_keys):
            return True

        vector = resolved_inputs.get("vector")

        if isinstance(vector, dict):
            features = vector.get("features")
            if isinstance(features, list) and features:
                return True

        vectors = resolved_inputs.get("vectors")

        if isinstance(vectors, list) and vectors:
            return True

        return False


    def _looks_like_real_estate_ranking_query(self, query: str) -> bool:
        q = (query or "").lower()

        property_terms = [
            "ملک",
            "املاک",
            "زمین",
            "آپارتمان",
            "ویلا",
            "property",
            "real estate",
        ]
        ranking_terms = [
            "رتبه",
            "رتبه‌بندی",
            "رتبه بندی",
            "امتیاز",
            "score",
            "rank",
            "ranking",
            "گزارش",
            "report",
        ]
        constraint_terms = [
            "مترو",
            "مرکز خرید",
            "خیابان اصلی",
            "ریسک",
            "سیل",
            "زلزله",
            "آتش",
            "۵۰۰",
            "500",
            "متر",
        ]

        return (
            any(term in q for term in property_terms)
            and any(term in q for term in ranking_terms)
            and any(term in q for term in constraint_terms)
        )

    def _extract_property_feature_collection_from_inputs(self, inputs: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(inputs, dict):
            return None

        def _property_only_feature_collection(fc: dict[str, Any]) -> dict[str, Any]:
            features = fc.get("features") or []
            if not isinstance(features, list):
                features = []

            property_features: list[dict[str, Any]] = []
            for feature in features:
                if not isinstance(feature, dict):
                    continue
                props = feature.get("properties") or {}
                if not isinstance(props, dict):
                    props = {}

                layer = str(props.get("layer") or "").strip().lower()
                property_type = str(props.get("property_type") or props.get("kind") or "").strip().lower()
                has_property_identity = bool(
                    props.get("property_id")
                    or str(props.get("id") or "").startswith("prop-")
                    or layer == "property"
                    or property_type in {"apartment", "villa", "land", "house", "ملک", "آپارتمان", "ویلا", "زمین"}
                )

                if has_property_identity:
                    property_features.append(feature)

            # اگر featureهای property پیدا شد، فقط همان‌ها را برای ranking برگردان.
            if property_features:
                out = dict(fc)
                out["features"] = property_features
                return out

            return fc

        candidate_keys = [
            "properties",
            "property_layer",
            "propertyLayer",
            "real_estate_properties",
            "realEstateProperties",
            "parcels",
            "assets",
            "vector",
            "geojson",
        ]

        for key in candidate_keys:
            value = inputs.get(key)
            if isinstance(value, dict) and value.get("type") == "FeatureCollection":
                return _property_only_feature_collection(value)

            if isinstance(value, dict):
                nested = value.get("geojson") or value.get("data") or value.get("feature_collection")
                if isinstance(nested, dict) and nested.get("type") == "FeatureCollection":
                    return _property_only_feature_collection(nested)

        return None


    def _extract_real_estate_spatial_context_from_inputs(self, inputs: dict[str, Any] | None) -> dict[str, Any]:
        """
        Extract optional spatial context layers for real-estate ranking.

        Supported layer groups:
        - metro: point features
        - malls: point/polygon features
        - main_roads: line features
        - allowed_zones: polygon features

        The extractor is deliberately permissive and can work with:
        - inputs["geojson"] as one mixed FeatureCollection
        - inputs["metro_layer"], inputs["main_roads"], ...
        - nested {"geojson": FeatureCollection} wrappers
        """
        context: dict[str, list[dict[str, Any]]] = {
            "metro": [],
            "malls": [],
            "main_roads": [],
            "allowed_zones": [],
        }

        if not isinstance(inputs, dict):
            return context

        seen: set[int] = set()

        def _iter_feature_collections(value: Any, hint: str = "", depth: int = 0):
            if depth > 6:
                return

            if isinstance(value, dict):
                obj_id = id(value)
                if obj_id in seen:
                    return
                seen.add(obj_id)

                if value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list):
                    yield value, hint

                for key, nested in value.items():
                    if key in {"features", "geometry", "properties"}:
                        continue
                    next_hint = f"{hint}.{key}" if hint else str(key)
                    if isinstance(nested, (dict, list)):
                        yield from _iter_feature_collections(nested, next_hint, depth + 1)

            elif isinstance(value, list):
                for index, item in enumerate(value):
                    next_hint = f"{hint}[{index}]"
                    if isinstance(item, (dict, list)):
                        yield from _iter_feature_collections(item, next_hint, depth + 1)

        def _search_text(feature: dict[str, Any], source_hint: str) -> str:
            props = feature.get("properties") or {}
            if not isinstance(props, dict):
                props = {}

            parts = [
                source_hint,
                props.get("layer"),
                props.get("category"),
                props.get("kind"),
                props.get("type"),
                props.get("feature_type"),
                props.get("role"),
                props.get("class"),
                props.get("name"),
                props.get("title"),
                props.get("description"),
            ]
            return " ".join(str(part or "") for part in parts).strip().lower()

        def _classify_feature(feature: dict[str, Any], source_hint: str) -> str | None:
            geom = feature.get("geometry") or {}
            geom_type = str(geom.get("type") or "").lower()
            text = _search_text(feature, source_hint)

            # Property features are handled by _extract_property_feature_collection_from_inputs.
            property_terms = [
                "property",
                "properties",
                "real_estate",
                "real-estate",
                "parcel",
                "apartment",
                "villa",
                "house",
                "land",
                "ملک",
                "آپارتمان",
                "ویلا",
                "زمین",
            ]
            if any(term in text for term in property_terms):
                return "property"

            metro_terms = [
                "metro",
                "subway",
                "station",
                "metro_station",
                "ایستگاه مترو",
                "مترو",
            ]
            if any(term in text for term in metro_terms):
                return "metro"

            mall_terms = [
                "mall",
                "shopping",
                "shopping_center",
                "commercial_center",
                "مرکز خرید",
                "خرید",
                "مال",
            ]
            if any(term in text for term in mall_terms):
                return "malls"

            road_terms = [
                "main_road",
                "main-road",
                "road",
                "street",
                "highway",
                "primary",
                "خیابان اصلی",
                "جاده اصلی",
                "خیابان",
                "جاده",
            ]
            if any(term in text for term in road_terms):
                return "main_roads"

            allowed_zone_terms = [
                "allowed_zone",
                "allowed-zone",
                "construction_zone",
                "construction-zone",
                "build_zone",
                "build-zone",
                "zoning",
                "allowed construction",
                "محدوده مجاز",
                "محدوده ساخت",
                "ساخت‌وساز مجاز",
                "ساخت و ساز مجاز",
            ]
            if any(term in text for term in allowed_zone_terms):
                return "allowed_zones"

            # Geometry-based conservative hints from source key.
            if "metro" in text and geom_type == "point":
                return "metro"
            if ("mall" in text or "shopping" in text) and geom_type in {"point", "polygon", "multipolygon"}:
                return "malls"
            if ("road" in text or "street" in text or "highway" in text) and geom_type in {"linestring", "multilinestring"}:
                return "main_roads"
            if ("zone" in text or "zoning" in text) and geom_type in {"polygon", "multipolygon"}:
                return "allowed_zones"

            return None

        for fc, source_hint in _iter_feature_collections(inputs):
            for feature in fc.get("features") or []:
                if not isinstance(feature, dict):
                    continue
                group = _classify_feature(feature, source_hint)
                if group in context:
                    context[group].append(feature)

        return context

    def _feature_point_lonlat(self, feature: dict[str, Any]) -> tuple[float, float] | None:
        geom = feature.get("geometry") or {}
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            return None

        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            return None

        lon = self._to_float_or_none(coords[0])
        lat = self._to_float_or_none(coords[1])
        if lon is None or lat is None:
            return None

        return lon, lat

    def _point_in_ring_lonlat(self, point: tuple[float, float], ring: list[Any]) -> bool:
        if not isinstance(ring, list) or len(ring) < 3:
            return False

        x, y = point
        inside = False
        j = len(ring) - 1

        for i in range(len(ring)):
            pi = ring[i]
            pj = ring[j]
            if (
                isinstance(pi, list)
                and isinstance(pj, list)
                and len(pi) >= 2
                and len(pj) >= 2
            ):
                xi = self._to_float_or_none(pi[0])
                yi = self._to_float_or_none(pi[1])
                xj = self._to_float_or_none(pj[0])
                yj = self._to_float_or_none(pj[1])

                if xi is not None and yi is not None and xj is not None and yj is not None:
                    intersects = ((yi > y) != (yj > y)) and (
                        x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
                    )
                    if intersects:
                        inside = not inside

            j = i

        return inside

    def _point_in_polygon_feature_lonlat(self, point: tuple[float, float], feature: dict[str, Any]) -> bool:
        geom = feature.get("geometry") or {}
        if not isinstance(geom, dict):
            return False

        geom_type = geom.get("type")
        coords = geom.get("coordinates")

        def _inside_polygon(poly_coords: Any) -> bool:
            if not isinstance(poly_coords, list) or not poly_coords:
                return False

            outer = poly_coords[0]
            if not self._point_in_ring_lonlat(point, outer):
                return False

            # Holes: if inside any inner ring, point is outside polygon.
            for hole in poly_coords[1:]:
                if self._point_in_ring_lonlat(point, hole):
                    return False

            return True

        if geom_type == "Polygon":
            return _inside_polygon(coords)

        if geom_type == "MultiPolygon" and isinstance(coords, list):
            return any(_inside_polygon(poly) for poly in coords)

        return False

    def _lonlat_to_local_xy_m(
        self,
        point: tuple[float, float],
        *,
        ref_lat: float,
    ) -> tuple[float, float]:
        import math

        lon, lat = point
        x = lon * 111_320.0 * math.cos(math.radians(ref_lat))
        y = lat * 110_540.0
        return x, y

    def _distance_point_to_segment_m(
        self,
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        import math

        ref_lat = point[1]
        px, py = self._lonlat_to_local_xy_m(point, ref_lat=ref_lat)
        ax, ay = self._lonlat_to_local_xy_m(start, ref_lat=ref_lat)
        bx, by = self._lonlat_to_local_xy_m(end, ref_lat=ref_lat)

        dx = bx - ax
        dy = by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)

        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        cx = ax + t * dx
        cy = ay + t * dy
        return math.hypot(px - cx, py - cy)

    def _distance_point_to_point_m(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
    ) -> float:
        import math

        lon1, lat1 = a
        lon2, lat2 = b

        radius_m = 6_371_000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)

        h = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return 2 * radius_m * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))

    def _distance_point_to_geometry_m(
        self,
        point: tuple[float, float],
        feature: dict[str, Any],
    ) -> float | None:
        geom = feature.get("geometry") or {}
        if not isinstance(geom, dict):
            return None

        geom_type = geom.get("type")
        coords = geom.get("coordinates")

        def _coord_to_point(value: Any) -> tuple[float, float] | None:
            if not isinstance(value, list) or len(value) < 2:
                return None
            lon = self._to_float_or_none(value[0])
            lat = self._to_float_or_none(value[1])
            if lon is None or lat is None:
                return None
            return lon, lat

        def _line_distance(line: Any) -> float | None:
            if not isinstance(line, list) or len(line) < 2:
                return None

            best: float | None = None
            prev = _coord_to_point(line[0])
            for raw in line[1:]:
                current = _coord_to_point(raw)
                if prev is not None and current is not None:
                    dist = self._distance_point_to_segment_m(point, prev, current)
                    best = dist if best is None else min(best, dist)
                prev = current

            return best

        if geom_type == "Point":
            other = _coord_to_point(coords)
            return self._distance_point_to_point_m(point, other) if other else None

        if geom_type == "MultiPoint" and isinstance(coords, list):
            distances = []
            for raw_point in coords:
                other = _coord_to_point(raw_point)
                if other:
                    distances.append(self._distance_point_to_point_m(point, other))
            return min(distances) if distances else None

        if geom_type == "LineString":
            return _line_distance(coords)

        if geom_type == "MultiLineString" and isinstance(coords, list):
            distances = [d for line in coords if (d := _line_distance(line)) is not None]
            return min(distances) if distances else None

        if geom_type == "Polygon":
            if self._point_in_polygon_feature_lonlat(point, feature):
                return 0.0
            if isinstance(coords, list):
                distances = [d for ring in coords if (d := _line_distance(ring)) is not None]
                return min(distances) if distances else None

        if geom_type == "MultiPolygon" and isinstance(coords, list):
            if self._point_in_polygon_feature_lonlat(point, feature):
                return 0.0

            distances: list[float] = []
            for poly in coords:
                if isinstance(poly, list):
                    for ring in poly:
                        dist = _line_distance(ring)
                        if dist is not None:
                            distances.append(dist)
            return min(distances) if distances else None

        return None

    def _nearest_distance_to_features_m(
        self,
        point: tuple[float, float],
        features: list[dict[str, Any]],
    ) -> float | None:
        distances: list[float] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            dist = self._distance_point_to_geometry_m(point, feature)
            if dist is not None:
                distances.append(dist)

        return min(distances) if distances else None

    def _has_metric_value(self, props: dict[str, Any], key: str) -> bool:
        value = props.get(key)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return self._to_float_or_none(value) is not None

    def _has_bool_like_value(self, props: dict[str, Any], *keys: str) -> bool:
        for key in keys:
            if props.get(key) is not None:
                return True
        return False

    def _enrich_property_feature_collection_with_spatial_context(
        self,
        feature_collection: dict[str, Any],
        spatial_context: dict[str, list[dict[str, Any]]] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Fill missing real-estate ranking metrics from optional spatial layers.

        This is an additive fallback:
        - existing property distance/risk fields are preserved
        - only missing distance_to_* and in_allowed_zone aliases are computed
        """
        context = spatial_context or {}
        metro_features = context.get("metro") or []
        mall_features = context.get("malls") or []
        road_features = context.get("main_roads") or []
        allowed_zone_features = context.get("allowed_zones") or []

        features = feature_collection.get("features") or []
        if not isinstance(features, list):
            features = []

        enriched_features: list[dict[str, Any]] = []
        updated_distance_count = 0
        updated_allowed_zone_count = 0
        touched_property_count = 0

        for feature in features:
            if not isinstance(feature, dict):
                continue

            props = dict(feature.get("properties") or {})
            point = self._feature_point_lonlat(feature)

            touched = False

            if point is not None:
                if not self._has_metric_value(props, "distance_to_metro_m") and metro_features:
                    distance = self._nearest_distance_to_features_m(point, metro_features)
                    if distance is not None:
                        props["distance_to_metro_m"] = int(round(distance))
                        updated_distance_count += 1
                        touched = True

                if not self._has_metric_value(props, "distance_to_mall_m") and mall_features:
                    distance = self._nearest_distance_to_features_m(point, mall_features)
                    if distance is not None:
                        props["distance_to_mall_m"] = int(round(distance))
                        updated_distance_count += 1
                        touched = True

                if not self._has_metric_value(props, "distance_to_main_road_m") and road_features:
                    distance = self._nearest_distance_to_features_m(point, road_features)
                    if distance is not None:
                        props["distance_to_main_road_m"] = int(round(distance))
                        updated_distance_count += 1
                        touched = True

                if (
                    not self._has_bool_like_value(
                        props,
                        "in_allowed_zone",
                        "build_zone_allowed",
                        "construction_allowed",
                    )
                    and allowed_zone_features
                ):
                    in_zone = any(
                        self._point_in_polygon_feature_lonlat(point, zone_feature)
                        for zone_feature in allowed_zone_features
                        if isinstance(zone_feature, dict)
                    )
                    props["in_allowed_zone"] = bool(in_zone)
                    props["build_zone_allowed"] = bool(in_zone)
                    props["construction_allowed"] = bool(in_zone)
                    updated_allowed_zone_count += 1
                    touched = True

            if touched:
                touched_property_count += 1
                props["spatial_enrichment_applied"] = True

            enriched_features.append(
                {
                    **feature,
                    "properties": props,
                }
            )

        enriched_fc = dict(feature_collection)
        enriched_fc["features"] = enriched_features

        summary = {
            "applied": touched_property_count > 0,
            "property_count": len(enriched_features),
            "touched_property_count": touched_property_count,
            "updated_distance_count": updated_distance_count,
            "updated_allowed_zone_count": updated_allowed_zone_count,
            "context_counts": {
                "metro": len(metro_features),
                "malls": len(mall_features),
                "main_roads": len(road_features),
                "allowed_zones": len(allowed_zone_features),
            },
        }

        return enriched_fc, summary


    def _normalize_risk_level(self, value: Any) -> str:
        text = str(value or "").strip().lower()

        low_values = {"low", "l", "پایین", "کم", "خوب", "ایمن", "safe"}
        medium_values = {"medium", "med", "m", "متوسط", "میانه", "قابل قبول", "قابل‌قبول"}
        high_values = {"high", "h", "بالا", "زیاد", "پرخطر", "خطرناک", "unsafe"}

        if text in low_values:
            return "low"
        if text in medium_values:
            return "medium"
        if text in high_values:
            return "high"

        # اگر مقدار نامشخص بود، برای MVP آن را medium در نظر می‌گیریم تا حذف سخت‌گیرانه نشود.
        return "medium"

    def _to_float_or_none(self, value: Any) -> float | None:
        if value is None:
            return None

        try:
            return float(value)
        except Exception:
            return None

    def _score_real_estate_property(self, props: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        metro = self._to_float_or_none(props.get("distance_to_metro_m"))
        mall = self._to_float_or_none(props.get("distance_to_mall_m"))
        road = self._to_float_or_none(props.get("distance_to_main_road_m"))
        price = self._to_float_or_none(props.get("price"))

        poi_distances = [d for d in [metro, mall] if d is not None]
        best_poi = min(poi_distances) if poi_distances else None

        flood = self._normalize_risk_level(props.get("flood_risk"))
        earthquake = self._normalize_risk_level(props.get("earthquake_risk"))
        fire = self._normalize_risk_level(props.get("fire_risk"))

        risks = [flood, earthquake, fire]
        risk_penalty = 0.0
        for risk in risks:
            if risk == "medium":
                risk_penalty += 12.0
            elif risk == "high":
                risk_penalty += 35.0

        score = 100.0

        # نزدیکی به مترو/مرکز خرید؛ هرچه کمتر بهتر.
        if best_poi is None:
            score -= 20.0
        else:
            score -= min(best_poi, 1500.0) / 500.0 * 10.0

        # نزدیکی به خیابان اصلی؛ هرچه کمتر بهتر.
        if road is None:
            score -= 10.0
        else:
            score -= min(road, 500.0) / 150.0 * 6.0

        score -= risk_penalty

        # اگر خارج از محدوده مجاز باشد، جریمه سنگین می‌گیرد.
        in_allowed_zone = props.get("in_allowed_zone")
        if in_allowed_zone is None:
            in_allowed_zone = props.get("build_zone_allowed")
        if in_allowed_zone is None:
            in_allowed_zone = props.get("construction_allowed", True)

        if in_allowed_zone is False:
            score -= 30.0

        # قیمت را خیلی کم وارد می‌کنیم، چون در این query معیار اصلی مکانی/ریسک است.
        if price is not None:
            score -= min(price / 10_000_000_000.0, 5.0) * 0.8

        kind = str(props.get("kind") or props.get("property_type") or "").lower()
        if "villa" in kind or "ویلا" in kind:
            score += 1.0

        score = max(0.0, min(100.0, score))

        details = {
            "best_poi_distance_m": best_poi,
            "distance_to_metro_m": metro,
            "distance_to_mall_m": mall,
            "distance_to_main_road_m": road,
            "risk_levels": {
                "flood": flood,
                "earthquake": earthquake,
                "fire": fire,
            },
            "risk_penalty": round(risk_penalty, 2),
            "in_allowed_zone": bool(in_allowed_zone),
        }

        return round(score, 1), details

    def _evaluate_real_estate_eligibility(self, props: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
        metro = self._to_float_or_none(props.get("distance_to_metro_m"))
        mall = self._to_float_or_none(props.get("distance_to_mall_m"))
        road = self._to_float_or_none(props.get("distance_to_main_road_m"))

        poi_distances = [d for d in [metro, mall] if d is not None]
        best_poi = min(poi_distances) if poi_distances else None

        flood = self._normalize_risk_level(props.get("flood_risk"))
        earthquake = self._normalize_risk_level(props.get("earthquake_risk"))
        fire = self._normalize_risk_level(props.get("fire_risk"))

        in_allowed_zone = props.get("in_allowed_zone")
        if in_allowed_zone is None:
            in_allowed_zone = props.get("build_zone_allowed")
        if in_allowed_zone is None:
            in_allowed_zone = props.get("construction_allowed", True)

        reasons: list[str] = []

        # شرط اصلی کاربر: کمتر از ۵۰۰ متر به مترو یا مرکز خرید
        if best_poi is None:
            reasons.append("distance_to_metro_or_mall_missing")
        elif best_poi > 500:
            reasons.append("farther_than_500m_from_metro_or_mall")

        # نزدیکی به خیابان اصلی؛ برای MVP آستانه ۱۵۰ متر می‌گذاریم.
        if road is None:
            reasons.append("distance_to_main_road_missing")
        elif road > 150:
            reasons.append("far_from_main_road")

        # برای MVP فقط high را حذف می‌کنیم و medium را با جریمه امتیازی نگه می‌داریم.
        # این باعث می‌شود ملکی با earthquake_risk=medium حذف نشود ولی امتیاز پایین‌تری بگیرد.
        if flood == "high":
            reasons.append("high_flood_risk")
        if earthquake == "high":
            reasons.append("high_earthquake_risk")
        if fire == "high":
            reasons.append("high_fire_risk")

        if in_allowed_zone is False:
            reasons.append("outside_allowed_construction_zone")

        eligible = len(reasons) == 0

        metrics = {
            "best_poi_distance_m": best_poi,
            "distance_to_metro_m": metro,
            "distance_to_mall_m": mall,
            "distance_to_main_road_m": road,
            "risk_levels": {
                "flood": flood,
                "earthquake": earthquake,
                "fire": fire,
            },
            "in_allowed_zone": bool(in_allowed_zone),
        }

        return eligible, reasons, metrics


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

    def submit_feedback(
        self,
        *,
        request_id: str,
        rating: str,
        issue_types: list[str] | None = None,
        expected_capability: str | None = None,
        expected_plugin_id: str | None = None,
        comment: str | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Submit user feedback for a previous request.

        This method:
            1. Finds the stored audit record
            2. Creates FeedbackRecord
            3. Builds learning signals
            4. Builds weight proposals
            5. Stores proposals in proposal collector

        Important:
            It does NOT auto-apply proposals.
        """
        record = self.get_request(request_id)

        if record is None:
            raise OrchestratorServiceError(
                f"Unknown request_id: {request_id}"
            )

        audit_record = record.get("audit_record")

        if not isinstance(audit_record, dict):
            raise OrchestratorServiceError(
                f"Request has no audit_record: {request_id}"
            )

        feedback_input_kwargs: dict[str, Any] = {
            "rating": rating,
        }

        if issue_types is not None:
            feedback_input_kwargs["issue_types"] = issue_types

        if expected_capability is not None:
            feedback_input_kwargs["expected_capability"] = expected_capability

        if expected_plugin_id is not None:
            feedback_input_kwargs["expected_plugin_id"] = expected_plugin_id

        # Keep compatibility if UserFeedbackInput does not define comment/user_context.
        try:
            if comment is not None:
                feedback_input_kwargs["comment"] = comment
            if user_context is not None:
                feedback_input_kwargs["user_context"] = user_context

            feedback_input = UserFeedbackInput(**feedback_input_kwargs)
        except TypeError:
            feedback_input_kwargs.pop("comment", None)
            feedback_input_kwargs.pop("user_context", None)
            feedback_input = UserFeedbackInput(**feedback_input_kwargs)

        feedback_record = self.feedback_collector.submit(
            audit_record,
            feedback_input,
        )

        signals = self.learning_signal_builder.build(
            audit_record=audit_record,
            feedback_record=feedback_record,
        )

        proposals = self.weight_proposal_engine.build(
            signals,
            weight_store=self.weight_store,
        )

        self.weight_proposal_collector.ingest_many(proposals)

        feedback_payload = {
            "request_id": request_id,
            "feedback": _to_dict(feedback_record),
            "signals": [
                _to_dict(signal)
                for signal in signals
            ],
            "proposals": [
                _to_dict(proposal)
                for proposal in proposals
            ],
            "proposal_summary": self.weight_proposal_collector.summarize(),
        }

        record["feedback"] = feedback_payload

        return feedback_payload

    def approve_and_apply_proposal(
        self,
        proposal: WeightProposal | dict[str, Any],
        *,
        save: bool | None = None,
    ) -> dict[str, Any]:
        """
        Approve and apply a proposal to the active weight store.

        This is intended for admin/policy review workflows.
        """
        proposal_obj = self._ensure_proposal(proposal)

        approved = self.weight_proposal_engine.approve(proposal_obj)

        applied = self.weight_proposal_engine.apply(
            approved,
            weight_store=self.weight_store,
        )

        should_save = (
            self.config.auto_save_weights_after_apply
            if save is None
            else save
        )

        saved_payload = None

        if should_save:
            try:
                saved_payload = self.persistence.save(
                    self.weight_store,
                    metadata={
                        "source": "OrchestratorService.approve_and_apply_proposal",
                    },
                )
            except WeightStorePersistenceError as exc:
                raise OrchestratorServiceError(str(exc)) from exc

        return {
            "approved": _to_dict(approved),
            "applied": _to_dict(applied),
            "weights": self.get_weights(),
            "saved": saved_payload is not None,
            "saved_payload": saved_payload,
        }

    def get_request(
        self,
        request_id: str,
    ) -> dict[str, Any] | None:
        """
        Return stored request record.
        """
        return self._history.get(request_id)

    def list_requests(self) -> list[dict[str, Any]]:
        """
        Return lightweight request history.
        """
        items: list[dict[str, Any]] = []

        for request_id, record in self._history.items():
            response = record.get("production_response") or {}

            items.append(
                {
                    "request_id": request_id,
                    "status": response.get("status"),
                    "answer": response.get("answer"),
                    "query": record.get("query"),
                }
            )

        return items


    def create_project(
        self,
        *,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self.project_service.create_project(
                name=name,
                description=description,
                metadata=metadata,
            )
        except ProjectServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc


    def list_projects(
        self,
    ) -> list[dict[str, Any]]:
        return self.project_service.list_projects()


    def get_project(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        try:
            return self.project_service.get_project(project_id)
        except ProjectServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc


    def list_plugins(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return grouped plugin inventory for Plugin Manager.

        Read-only in phase 1:
        - enabled/disabled is read from config/plugin_state.json
        - plugin-specific YAML config is not mutated here
        """
        registry = getattr(self, "registry", None)
        if registry is None:
            return []

        inventory = list(getattr(registry, "as_plugin_inventory")() or [])
        skipped_plugins = list(getattr(registry, "skipped_plugins", []) or [])

        skipped_by_module = {
            str(item.get("module")): item
            for item in skipped_plugins
            if isinstance(item, dict) and item.get("module")
        }

        items: list[dict[str, Any]] = []

        for item in inventory:
            plugin_id = str(item.get("plugin_id") or "")
            capabilities = list(item.get("capabilities") or [])

            module_names = sorted(
                {
                    str(cap.get("metadata", {}).get("module_name") or "")
                    for cap in capabilities
                    if isinstance(cap, dict)
                }
                - {""}
            )

            enabled = True
            try:
                enabled = self.plugin_state_store.is_enabled(plugin_id, default=True)
            except PluginStateStoreError:
                enabled = True

            has_config = False
            config_path = f"config/plugins/{plugin_id}.yaml"

            try:
                from pathlib import Path as _Path
                has_config = _Path(config_path).exists()
            except Exception:
                has_config = False

            payload = {
                "plugin_id": plugin_id,
                "enabled": enabled,
                "state_source": "config/plugin_state.json",
                "config_path": config_path,
                "config_exists": has_config,
                "module_names": module_names,
                "capability_count": int(item.get("capability_count") or 0),
                "capabilities": capabilities,
                "skipped": False,
                "skipped_error": None,
            }

            if not module_names:
                # Try to infer from skipped plugins if available.
                for skipped in skipped_plugins:
                    if not isinstance(skipped, dict):
                        continue
                    module_name = str(skipped.get("module") or "")
                    if plugin_id and plugin_id in module_name:
                        payload["module_names"] = [module_name]
                        payload["skipped"] = True
                        payload["skipped_error"] = skipped.get("error")
                        break

            items.append(payload)

        items.sort(key=lambda x: str(x.get("plugin_id") or ""))
        return items

    def _is_plugin_enabled(
        self,
        plugin_id: str,
    ) -> bool:
        """
        Return True if plugin is enabled in plugin_state.json.
        Missing state defaults to enabled.
        """
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id:
            return False

        try:
            return bool(self.plugin_state_store.is_enabled(plugin_id, default=True))
        except PluginStateStoreError:
            return True

    def _disabled_plugin_ids(
        self,
    ) -> set[str]:
        """
        Return disabled plugin IDs from plugin inventory.
        """
        disabled: set[str] = set()

        for item in self.list_plugins():
            pid = str(item.get("plugin_id") or "").strip()
            if pid and not bool(item.get("enabled", True)):
                disabled.add(pid)

        return disabled

    def _enabled_capability_names(
        self,
    ) -> list[str]:
        """
        Return registered capability names whose source plugin is enabled.
        """
        registry = getattr(self, "registry", None)
        bindings = getattr(registry, "_bindings", {}) or {}

        names: list[str] = []

        if not isinstance(bindings, dict):
            return names

        for capability_name, binding in bindings.items():
            plugin_id = str(getattr(binding, "plugin_id", "") or "").strip()
            if plugin_id and self._is_plugin_enabled(plugin_id):
                names.append(str(capability_name))

        return sorted(set(names))

    def _build_enabled_registry_view(
        self,
    ) -> Any:
        """
        Build a lightweight registry-like view containing only enabled
        capabilities and descriptors.
        """
        registry = getattr(self, "registry", None)
        bindings = getattr(registry, "_bindings", {}) or {}
        descriptors = getattr(registry, "_descriptors", {}) or {}

        enabled_bindings: dict[str, Any] = {}
        enabled_descriptors: dict[str, Any] = {}

        if isinstance(bindings, dict):
            for capability_name, binding in bindings.items():
                plugin_id = str(getattr(binding, "plugin_id", "") or "").strip()
                if plugin_id and self._is_plugin_enabled(plugin_id):
                    key = str(capability_name)
                    enabled_bindings[key] = binding
                    if isinstance(descriptors, dict) and capability_name in descriptors:
                        enabled_descriptors[key] = descriptors[capability_name]

        return _EnabledOnlyRegistryView(
            enabled_bindings,
            enabled_descriptors,
        )


    def _build_enabled_router(
        self,
    ) -> Any:
        """
        Build a lightweight router containing only enabled capabilities
        from the registry bindings.
        """
        registry = getattr(self, "registry", None)
        bindings = getattr(registry, "_bindings", {}) or {}

        enabled_bindings: dict[str, Any] = {}

        if isinstance(bindings, dict):
            for capability_name, binding in bindings.items():
                plugin_id = str(getattr(binding, "plugin_id", "") or "").strip()
                if plugin_id and self._is_plugin_enabled(plugin_id):
                    enabled_bindings[str(capability_name)] = binding

        return _EnabledOnlyCapabilityRouter(enabled_bindings)


    def _assert_capability_enabled(
        self,
        capability_name: str,
    ) -> None:
        """
        Raise service error if capability exists but its plugin is disabled.
        """
        registry = getattr(self, "registry", None)
        bindings = getattr(registry, "_bindings", {}) or {}

        if not isinstance(bindings, dict):
            return

        binding = bindings.get(capability_name)
        if binding is None:
            return

        plugin_id = str(getattr(binding, "plugin_id", "") or "").strip()
        if plugin_id and not self._is_plugin_enabled(plugin_id):
            raise OrchestratorServiceError(
                f"Capability '{capability_name}' is disabled because plugin '{plugin_id}' is disabled."
            )


    def get_plugin(
        self,
        plugin_id: str,
    ) -> dict[str, Any]:
        """
        Return one plugin inventory item by plugin_id.
        """
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id:
            raise OrchestratorServiceError("plugin_id is required.")

        for item in self.list_plugins():
            if str(item.get("plugin_id")) == plugin_id:
                return item

        raise OrchestratorServiceError(f"Unknown plugin: {plugin_id}")


    def update_plugin_state(
        self,
        plugin_id: str,
        *,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """
        Update plugin manager state for one plugin.

        Phase 2 scope:
        - supports only enabled/disabled
        - does not mutate plugin YAML config
        - does not yet rebuild runtime registry/router automatically
        """
        plugin = self.get_plugin(plugin_id)

        if enabled is None:
            raise OrchestratorServiceError("At least one mutable field is required.")

        try:
            self.plugin_state_store.set_enabled(plugin["plugin_id"], bool(enabled))
        except PluginStateStoreError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

        return self.get_plugin(plugin["plugin_id"])


    def _runtime_paths_metadata(self) -> dict[str, str]:
        """
        Return JSON-safe runtime path metadata.

        RuntimePaths defines the canonical runtime layout. Some storage roots may
        be explicitly overridden by configuration, so this method reports the
        effective paths used by the service where possible.
        """
        runtime_paths = getattr(self, "runtime_paths", None)

        if runtime_paths is None:
            return {}

        payload = runtime_paths.as_dict()

        output_storage = getattr(self, "output_storage", None)
        upload_storage = getattr(self, "upload_storage", None)
        project_store = getattr(self, "project_store", None)

        if output_storage is not None:
            payload["outputs"] = str(getattr(output_storage, "root_dir", payload["outputs"]))

        if upload_storage is not None:
            payload["uploads"] = str(getattr(upload_storage, "root_dir", payload["uploads"]))

        if project_store is not None:
            payload["projects"] = str(getattr(project_store, "root_dir", payload["projects"]))

        return payload


    def get_runtime_settings(
        self,
    ) -> dict[str, Any]:
        """
        Return non-sensitive runtime settings for UI/debugging.

        Secrets such as API keys are never returned.
        """
        import os

        config = getattr(self, "config", None)

        plugin_modules = (
            getattr(config, "plugin_module_names", None)
            or getattr(config, "plugin_modules", None)
            or getattr(config, "plugins", None)
            or []
        )

        if isinstance(plugin_modules, tuple):
            plugin_modules = list(plugin_modules)

        if not isinstance(plugin_modules, list):
            plugin_modules = list(plugin_modules) if plugin_modules else []

        registry = getattr(self, "registry", None)
        bindings = getattr(registry, "_bindings", {}) or {}

        capability_names: list[str] = []
        plugin_ids: list[str] = []

        if isinstance(bindings, dict):
            capability_names = sorted(str(name) for name in bindings.keys())

            for binding in bindings.values():
                plugin_id = (
                    getattr(binding, "plugin_id", None)
                    or getattr(binding, "plugin_name", None)
                    or getattr(binding, "source_plugin", None)
                )

                if plugin_id:
                    plugin_ids.append(str(plugin_id))

        plugin_ids = sorted(set(plugin_ids))

        skipped_plugins = list(getattr(registry, "skipped_plugins", []) or [])
        enabled_capability_names = self._enabled_capability_names()
        disabled_plugin_ids = sorted(self._disabled_plugin_ids())

        return {
            "llm": {
                "provider": os.getenv("LLM_PROVIDER", "not_configured"),
                "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL"),
                "fast_model": os.getenv("LLM_FAST_MODEL"),
                "strong_model": os.getenv("LLM_STRONG_MODEL"),
                "default_model": os.getenv("LLM_DEFAULT_MODEL"),
                "temperature": os.getenv("LLM_TEMPERATURE"),
                "timeout_seconds": os.getenv("LLM_TIMEOUT_SECONDS"),
                "api_key_configured": bool(
                    os.getenv("OPENAI_API_KEY")
                    or os.getenv("AVALAI_API_KEY")
                    or os.getenv("LLM_API_KEY")
                ),
            },
            "plugins": {
                "module_names": plugin_modules,
                "plugin_ids": plugin_ids,
                "capabilities": capability_names,
                "capability_count": len(capability_names),
                "enabled_capabilities": enabled_capability_names,
                "enabled_capability_count": len(enabled_capability_names),
                "disabled_plugin_ids": disabled_plugin_ids,
                "skipped_plugins": skipped_plugins,
            },
            "runtime": {
                "runtime_dir": str(getattr(config, "runtime_dir", None))
                if getattr(config, "runtime_dir", None) is not None
                else None,
                "resolve_upload_refs_with_plugins": getattr(
                    config,
                    "resolve_upload_refs_with_plugins",
                    None,
                ),
                "raster_loader_plugin_module": getattr(
                    config,
                    "raster_loader_plugin_module",
                    None,
                ),
                "vector_loader_plugin_module": getattr(
                    config,
                    "vector_loader_plugin_module",
                    None,
                ),
            },
            "runtime_paths": self._runtime_paths_metadata(),
        }

    def run_llm_smoke_test(
        self,
    ) -> dict[str, Any]:
        """
        Run a non-sensitive backend LLM connectivity smoke test.
        """
        from orchestrator.llm_client import (
            LLMClientError,
            LLMConfigError,
            run_llm_smoke_test,
        )

        try:
            return run_llm_smoke_test()
        except (LLMConfigError, LLMClientError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc

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
            raise OrchestratorServiceError(str(exc)) from exc

    def save_upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        kind: str = "raster",
        user_context: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Save uploaded user file and return upload metadata.
        """
        try:
            payload = self.upload_service.save_upload(
                filename=filename,
                content=content,
                content_type=content_type,
                kind=kind,
                user_context=user_context,
            )

            if project_id:
                self.project_service.attach_upload(
                    project_id,
                    payload["upload_id"],
                )
                payload["project_id"] = project_id

            return payload
        except (UploadStorageError, UploadServiceError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def list_uploads(self) -> list[dict[str, Any]]:
        """
        List stored uploads.
        """
        return self.upload_service.list_uploads()

    def get_upload_metadata(
        self,
        upload_id: str,
    ) -> dict[str, Any]:
        """
        Return upload metadata.
        """
        try:
            return self.upload_service.read_metadata(upload_id)
        except (UploadStorageError, UploadServiceError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_upload_file_path(
        self,
        upload_id: str,
    ) -> Path:
        """
        Return safe uploaded file path.
        """
        try:
            return self.upload_service.get_file_path(upload_id)
        except (UploadStorageError, UploadServiceError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_upload_file_media_type(
        self,
        upload_id: str,
    ) -> str:
        try:
            return self.upload_service.get_media_type(upload_id)
        except (UploadStorageError, UploadServiceError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc



    def register_csv_table_source(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.data_source_service.register_csv_table_source(payload)
        except DataSourceServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def register_wms_source(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.data_source_service.register_wms_source(payload)
        except DataSourceServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def list_project_data_sources(
        self,
        project_id: str,
    ) -> list[dict[str, Any]]:
        try:
            return self.data_source_service.list_project_data_sources(project_id)
        except DataSourceServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_data_source(
        self,
        upload_id: str,
    ) -> dict[str, Any]:
        try:
            return self.data_source_service.get_data_source(upload_id)
        except DataSourceServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def delete_data_source(
        self,
        upload_id: str,
    ) -> dict[str, Any]:
        try:
            return self.data_source_service.delete_data_source(upload_id)
        except DataSourceServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def _normalize_data_source_metadata(
        self,
        metadata: dict[str, Any],
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.data_source_service._normalize_data_source_metadata(
            metadata,
            project_id=project_id,
        )


    def update_data_source(
        self,
        upload_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.data_source_service.update_data_source(upload_id, payload)
        except DataSourceServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def preview_data_source(
        self,
        upload_id: str,
    ) -> dict[str, Any]:
        try:
            return self.data_source_service.preview_data_source(upload_id)
        except DataSourceServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def _build_json_preview(
        self,
        payload: Any,
    ) -> dict[str, Any]:
        return self.data_source_service._build_json_preview(payload)

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


    def save_request_outputs(
        self,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Persist output files for a stored request and return manifest.
        """
        record = self.get_request(request_id)

        if record is None:
            raise OrchestratorServiceError(
                f"Unknown request_id: {request_id}"
            )

        return self._persist_outputs_for_record(record)

    def get_output_manifest(
        self,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Return persisted output manifest for a request.
        """
        record = self.get_request(request_id)

        if record is not None and isinstance(record.get("output_manifest"), dict):
            return record["output_manifest"]

        try:
            return self.output_service.read_manifest(request_id)
        except (OutputStorageError, OutputServiceError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def list_output_files(
        self,
        request_id: str,
    ) -> list[dict[str, Any]]:
        """
        List persisted output files for a request.
        """
        try:
            return self.output_service.list_files(request_id)
        except (OutputStorageError, OutputServiceError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_output_file_path(
        self,
        request_id: str,
        filename: str,
    ) -> Path:
        """
        Return safe path for a persisted output file.
        """
        try:
            return self.output_service.get_file_path(
                request_id,
                filename,
            )
        except (OutputStorageError, OutputServiceError) as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_output_file_media_type(
        self,
        filename: str,
    ) -> str:
        return self.output_service.get_media_type(filename)

    def _persist_outputs_for_record(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Internal helper to persist request outputs.
        """
        try:
            map_layers_payload = self.map_layer_builder.build_for_request_record(record)

            manifest = self.output_service.save_request_record(
                record,
                map_layers_payload=map_layers_payload,
            )

            record["output_manifest"] = manifest

            production_response = record.get("production_response")

            if isinstance(production_response, dict):
                metadata = production_response.setdefault("metadata", {})
                metadata["outputs_persisted"] = True
                metadata["output_manifest_file"] = "manifest.json"

            return manifest

        except Exception as exc:
            production_response = record.get("production_response")

            if isinstance(production_response, dict):
                warnings = production_response.setdefault("warnings", [])
                warnings.append(f"Output persistence failed: {exc}")

            raise OrchestratorServiceError(
                f"Output persistence failed: {exc}"
            ) from exc

    def get_map_layers(
        self,
        request_id: str,
    ) -> list[dict[str, Any]]:
        try:
            return self.map_layer_service.get_map_layers(request_id)
        except MapLayerServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_weights(self) -> dict[str, Any]:
        """
        Return current router weights.
        """
        return self.weight_store.to_dict()

    def save_weights(self) -> dict[str, Any]:
        """
        Persist current weights.
        """
        try:
            return self.persistence.save(
                self.weight_store,
                metadata={
                    "source": "OrchestratorService.save_weights",
                },
            )
        except WeightStorePersistenceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def reload_weights(self) -> dict[str, Any]:
        """
        Reload weights from persistence.
        """
        self.weight_store = self._load_weight_store()
        return self.get_weights()

    def get_health(self) -> dict[str, Any]:
        """
        Return service health payload for API / health endpoint.
        """
        return {
            "status": "ok",
            "service": "OrchestratorService",
            "plugin_modules": list(self.config.plugin_modules),
            "use_weighted_router": self.config.use_weighted_router,
            "weights_persistence_exists": self.persistence.exists(),
            "history_size": len(self._history),
            "runtime_paths": self._runtime_paths_metadata(),
            "weights": self.get_weights(),
        }

    def _build_router(self) -> Any:
        enabled_registry = self._build_enabled_registry_view()

        base_router = KeywordScoringCapabilityRouter(
            registry=enabled_registry,
        )

        if not self.config.use_weighted_router:
            return base_router

        return WeightedCapabilityRouter(
            base_router,
            weight_store=self.weight_store,
            config=WeightedRouterConfig(),
        )

    def _load_weight_store(self) -> InMemoryRouterWeightStore:
        default_config = WeightStoreConfig(
            default_weight=self.config.default_weight,
            min_weight=self.config.min_weight,
            max_weight=self.config.max_weight,
        )

        if not self.config.load_persisted_weights:
            return InMemoryRouterWeightStore(
                config=default_config,
            )

        try:
            return self.persistence.load_or_default(
                default_config=default_config,
            )
        except WeightStorePersistenceError:
            return InMemoryRouterWeightStore(
                config=default_config,
            )

    def _remember(
        self,
        *,
        request_id: str,
        record: dict[str, Any],
    ) -> None:
        if not self.config.keep_history:
            return

        if self.config.max_history_items == 0:
            return

        self._history[request_id] = record

        # Link this request to its project so the UI history stays persistent.
        project_id = str(record.get("project_id") or "").strip()
        if project_id:
            try:
                self.project_service.attach_request(project_id, request_id)
            except Exception:
                pass

        if len(self._history) > self.config.max_history_items:
            overflow = len(self._history) - self.config.max_history_items

            for key in list(self._history.keys())[:overflow]:
                self._history.pop(key, None)

    @staticmethod
    def _new_request_id() -> str:
        return f"req-{uuid.uuid4()}"

    @staticmethod
    def _ensure_proposal(
        proposal: WeightProposal | dict[str, Any],
    ) -> WeightProposal:
        if isinstance(proposal, WeightProposal):
            return proposal

        if not isinstance(proposal, dict):
            raise TypeError("proposal must be WeightProposal or dict.")

        required = {
            "proposal_id",
            "created_at",
            "target",
            "name",
            "current_weight",
            "proposed_weight",
            "delta",
            "evidence_count",
            "signal_ids",
            "severity_counts",
            "signal_type_counts",
        }

        missing = sorted(required - set(proposal.keys()))

        if missing:
            raise ValueError(f"proposal dict missing fields: {missing}")

        return WeightProposal(
            proposal_id=str(proposal["proposal_id"]),
            created_at=str(proposal["created_at"]),
            target=str(proposal["target"]),
            name=str(proposal["name"]),
            current_weight=float(proposal["current_weight"]),
            proposed_weight=float(proposal["proposed_weight"]),
            delta=float(proposal["delta"]),
            evidence_count=int(proposal["evidence_count"]),
            signal_ids=list(proposal["signal_ids"]),
            severity_counts=dict(proposal["severity_counts"]),
            signal_type_counts=dict(proposal["signal_type_counts"]),
            status=str(proposal.get("status", "pending_review")),
            metadata=dict(proposal.get("metadata", {})),
        )


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)

    if hasattr(value, "to_dict") and callable(value.to_dict):
        result = value.to_dict()

        if isinstance(result, dict):
            return result

        return {
            "value": result,
        }

    if is_dataclass(value):
        return asdict(value)

    payload = dict(getattr(value, "__dict__", {}) or {})

    if payload:
        return payload

    return {
        "value": value,
    }


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
