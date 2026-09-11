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
from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES
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
from orchestrator.feedback_proposal_service import (
    FeedbackProposalService,
    FeedbackProposalServiceError,
)
from orchestrator.plugin_runtime_service import (
    PluginRuntimeService,
    PluginRuntimeServiceError,
)
from orchestrator.request_history_service import RequestHistoryService
from orchestrator.query_execution_service import QueryExecutionService, QueryExecutionServiceError
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


# Backward-compatible re-exports for older callers/tests.
# The PostGIS planning helper implementation lives in the query execution module.
from smart_spatial_system.application.services.query_execution.postgis_planning_context import (
    _POSTGIS_CONNECTION_KEYS,
    _build_query_spec_runtime_inputs,
    _coerce_postgis_schema_context,
    _discover_postgis_schema_context_from_connection_config,
    _extract_postgis_connection_config_from_sources,
    _extract_semantic_planning_context_from_sources,
    _first_mapping_value,
    _looks_like_postgis_connection,
    _normalize_postgis_connection_config,
)



_POSTGIS_SCHEMA_COMPAT_NAMES = {
    "ColumnInfo",
    "PostGISSchemaContext",
    "PostGISTableInfo",
}


def __getattr__(name: str):
    if name in _POSTGIS_SCHEMA_COMPAT_NAMES:
        from smart_spatial_system.application.services.query_execution import (
            postgis_planning_context as postgis_context,
        )

        return getattr(postgis_context, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")



@dataclass
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

    # REFACTOR_PLAN.md Phase 3: explicit config source of truth for planning
    # flags that used to be environment-only (QUERY_SPEC_PLANNING_ENABLED,
    # LLM_PLANNING_ENABLED). The env vars still work as deployment-level
    # overrides on top of these - see planning_execution_policy.py and
    # llm_intent_adapter.py. Defaults match the env vars' prior defaults
    # (both False) so this is a config-source change only, not a behavior
    # change.
    query_spec_planning_enabled: bool = False
    llm_planning_enabled: bool = False

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

        self.plugin_runtime_service = PluginRuntimeService(
            registry_getter=lambda: getattr(self, "registry", None),
            plugin_state_store=self.plugin_state_store,
            config_getter=lambda: getattr(self, "config", None),
            runtime_paths_getter=lambda: getattr(self, "runtime_paths", None),
            output_storage_getter=lambda: getattr(self, "output_storage", None),
            upload_storage_getter=lambda: getattr(self, "upload_storage", None),
            project_store_getter=lambda: getattr(self, "project_store", None),
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
        self.query_execution_service = QueryExecutionService(self)

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

        self.feedback_proposal_service = FeedbackProposalService(
            feedback_collector=self.feedback_collector,
            learning_signal_builder=self.learning_signal_builder,
            weight_proposal_engine=self.weight_proposal_engine,
            weight_proposal_collector=self.weight_proposal_collector,
            weight_store=self.weight_store,
            persistence=self.persistence,
            config=self.config,
            get_request=self.get_request,
            get_weights=self.get_weights,
        )

        self._history: dict[str, dict[str, Any]] = {}

        self.request_history_service = RequestHistoryService(
            history=self._history,
            config_getter=lambda: getattr(self, "config", None),
            project_service_getter=lambda: getattr(self, "project_service", None),
        )

    def _llm_planning_enabled(self) -> bool:
        return self.query_execution_service._llm_planning_enabled()

    def _query_spec_planning_enabled(self) -> bool:
        return self.query_execution_service._query_spec_planning_enabled()

    def _maybe_plan_llm_intent(
        self,
        query: str,
    ) -> dict[str, Any] | None:
        try:
            return self.query_execution_service._maybe_plan_llm_intent(query)
        except QueryExecutionServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    @staticmethod
    def _apply_intent_to_query(
        query: str,
        intent: dict[str, Any] | None,
    ) -> str:
        return QueryExecutionService._apply_intent_to_query(query, intent)

    @staticmethod
    def _is_vector_display_query(
        query: str,
        intent: dict[str, Any] | None = None,
    ) -> bool:
        return QueryExecutionService._is_vector_display_query(query, intent)

    @staticmethod
    def _is_vector_summary_query(
        query: str,
        intent: dict[str, Any] | None = None,
    ) -> bool:
        return QueryExecutionService._is_vector_summary_query(query, intent)

    @staticmethod
    def _read_geojson_path_if_possible(value: Any) -> dict[str, Any] | None:
        return QueryExecutionService._read_geojson_path_if_possible(value)

    @classmethod
    def _find_geojson_like(
        cls,
        obj: Any,
        *,
        max_depth: int = 8,
    ) -> dict[str, Any] | None:
        return QueryExecutionService._find_geojson_like(
            obj,
            max_depth=max_depth,
        )

    @staticmethod
    def _summarize_feature_collection(
        feature_collection: dict[str, Any],
    ) -> dict[str, Any]:
        return QueryExecutionService._summarize_feature_collection(feature_collection)

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
        return self.query_execution_service._try_handle_vector_display_directly(
            query=query,
            inputs=inputs,
            resolved_inputs=resolved_inputs,
            final_request_id=final_request_id,
            final_metadata=final_metadata,
            band_map=band_map,
            user_context=user_context,
            llm_intent=llm_intent,
        )

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
        return self.query_execution_service._try_handle_system_status_query(
            query=query,
            inputs=inputs,
            final_request_id=final_request_id,
            final_metadata=final_metadata,
            band_map=band_map,
            user_context=user_context,
            llm_intent=llm_intent,
        )

    def _is_system_status_query(
        self,
        query: str,
        llm_intent: Any | None = None,
    ) -> bool:
        return self.query_execution_service._is_system_status_query(
            query=query,
            llm_intent=llm_intent,
        )

    def _extract_property_feature_collection_from_inputs(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._extract_property_feature_collection_from_inputs(
            *args,
            **kwargs,
        )

    def _feature_point_lonlat(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._feature_point_lonlat(
            *args,
            **kwargs,
        )

    def _point_in_ring_lonlat(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._point_in_ring_lonlat(
            *args,
            **kwargs,
        )

    def _point_in_polygon_feature_lonlat(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._point_in_polygon_feature_lonlat(
            *args,
            **kwargs,
        )

    def _lonlat_to_local_xy_m(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._lonlat_to_local_xy_m(
            *args,
            **kwargs,
        )

    def _distance_point_to_segment_m(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._distance_point_to_segment_m(
            *args,
            **kwargs,
        )

    def _distance_point_to_point_m(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._distance_point_to_point_m(
            *args,
            **kwargs,
        )

    def _distance_point_to_geometry_m(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._distance_point_to_geometry_m(
            *args,
            **kwargs,
        )

    def _nearest_distance_to_features_m(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._nearest_distance_to_features_m(
            *args,
            **kwargs,
        )

    def _has_metric_value(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._has_metric_value(
            *args,
            **kwargs,
        )

    def _has_bool_like_value(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._has_bool_like_value(
            *args,
            **kwargs,
        )

    def _enrich_property_feature_collection_with_spatial_context(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._enrich_property_feature_collection_with_spatial_context(
            *args,
            **kwargs,
        )


    def _normalize_risk_level(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._normalize_risk_level(
            *args,
            **kwargs,
        )

    def _to_float_or_none(self, *args: Any, **kwargs: Any) -> Any:
        return self.query_execution_service._to_float_or_none(
            *args,
            **kwargs,
        )


    def _planning_trace_to_steps(self, trace: list[Any]) -> list[dict[str, Any]]:
        return self.query_execution_service._planning_trace_to_steps(trace=trace)

    def _planning_outputs_to_response_payload(
        self,
        planning_result: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
        if hasattr(self, "query_execution_service"):
            return self.query_execution_service._planning_outputs_to_response_payload(planning_result=planning_result)

        return QueryExecutionService(self)._planning_outputs_to_response_payload(
            planning_result=planning_result
        )


    def _enrich_query_database_params_from_inputs(
        self,
        query_spec: Any,
        resolved_inputs: dict[str, Any],
    ) -> None:
        return self.query_execution_service._enrich_query_database_params_from_inputs(query_spec=query_spec, resolved_inputs=resolved_inputs)


    def _kernel_execution_enabled(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        final_metadata: dict[str, Any] | None = None,
    ) -> bool:
        return self.query_execution_service._kernel_execution_enabled(
            metadata=metadata,
            final_metadata=final_metadata,
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
        try:
            return self.query_execution_service._try_handle_query_with_planning(
                query=query,
                resolved_inputs=resolved_inputs,
                final_request_id=final_request_id,
                final_metadata=final_metadata,
                user_context=user_context,
                original_inputs=original_inputs,
                band_map=band_map,
                metadata=metadata,
                project_id=project_id,
            )
        except QueryExecutionServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

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
        try:
            return self.query_execution_service.handle_query(
                query=query,
                inputs=inputs,
                band_map=band_map,
                request_id=request_id,
                user_context=user_context,
                metadata=metadata,
                min_score=min_score,
                project_id=project_id,
            )
        except QueryExecutionServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

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
        try:
            return self.feedback_proposal_service.submit_feedback(
                request_id=request_id,
                rating=rating,
                issue_types=issue_types,
                expected_capability=expected_capability,
                expected_plugin_id=expected_plugin_id,
                comment=comment,
                user_context=user_context,
            )
        except FeedbackProposalServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def approve_and_apply_proposal(
        self,
        proposal: WeightProposal | dict[str, Any],
        *,
        save: bool | None = None,
    ) -> dict[str, Any]:
        try:
            return self.feedback_proposal_service.approve_and_apply_proposal(
                proposal,
                save=save,
            )
        except FeedbackProposalServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_request(
        self,
        request_id: str,
    ) -> dict[str, Any] | None:
        return self.request_history_service.get_request(request_id)

    def list_requests(self) -> list[dict[str, Any]]:
        return self.request_history_service.list_requests()


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
        return self.plugin_runtime_service.list_plugins()

    def _is_plugin_enabled(
        self,
        plugin_id: str,
    ) -> bool:
        return self.plugin_runtime_service.is_plugin_enabled(plugin_id)

    def _disabled_plugin_ids(
        self,
    ) -> set[str]:
        return self.plugin_runtime_service.disabled_plugin_ids()

    def _enabled_capability_names(
        self,
    ) -> list[str]:
        return self.plugin_runtime_service.enabled_capability_names()

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
        try:
            self.plugin_runtime_service.assert_capability_enabled(capability_name)
        except PluginRuntimeServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc


    def get_plugin(
        self,
        plugin_id: str,
    ) -> dict[str, Any]:
        try:
            return self.plugin_runtime_service.get_plugin(plugin_id)
        except PluginRuntimeServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc


    def update_plugin_state(
        self,
        plugin_id: str,
        *,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        try:
            return self.plugin_runtime_service.update_plugin_state(
                plugin_id,
                enabled=enabled,
            )
        except PluginRuntimeServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc


    def _runtime_paths_metadata(self) -> dict[str, str]:
        return self.plugin_runtime_service.runtime_paths_metadata()


    def get_runtime_settings(
        self,
    ) -> dict[str, Any]:
        return self.plugin_runtime_service.get_runtime_settings()

    def run_llm_smoke_test(
        self,
    ) -> dict[str, Any]:
        try:
            return self.plugin_runtime_service.run_llm_smoke_test()
        except PluginRuntimeServiceError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def plan_intent_with_llm(
        self,
        query: str,
    ) -> dict[str, Any]:
        try:
            return self.query_execution_service.plan_intent_with_llm(query)
        except QueryExecutionServiceError as exc:
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
        return self.query_execution_service._resolve_input_references(inputs)


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
        Reload weights from persistence while preserving store identity.
        """
        loaded_store = self._load_weight_store()
        self.weight_store.replace_with(loaded_store)
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
            "history_size": self.request_history_service.size(),
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
        self.request_history_service.remember(
            request_id=request_id,
            record=record,
        )

    def _new_request_id(self) -> str:
        return self.query_execution_service._new_request_id()

    def _ensure_proposal(
        proposal: WeightProposal | dict[str, Any],
    ) -> WeightProposal:
        return FeedbackProposalService._ensure_proposal(proposal)


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
