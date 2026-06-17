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

import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter
from orchestrator.feedback import FeedbackCollector, UserFeedbackInput
from orchestrator.learning_signals import RouterLearningSignalBuilder
from orchestrator.map_layers import MapLayerBuilder
from orchestrator.input_reference_resolver import (
    UploadReferenceResolver,
    UploadReferenceResolverConfig,
    UploadReferenceResolverError,
)
from orchestrator.output_storage import (
    OutputStorage,
    OutputStorageConfig,
    OutputStorageError,
)
from orchestrator.project_store import (
    ProjectStore,
    ProjectStoreConfig,
    ProjectStoreError,
)
from orchestrator.production_response import (
    ProductionResponseBuilder,
    ProductionResponseConfig,
)
from orchestrator.routing_aware_natural_query_runner import (
    run_natural_query_with_routing_evidence,
)
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


DEFAULT_SAFE_PLUGIN_MODULES = [
    "plugins.spectral_indices",
    "plugins.raster_threshold",
    "plugins.raster_to_vector",
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
    outputs_path: str | Path = "outputs"
    uploads_path: str | Path = "uploads"
    projects_path: str | Path = "projects"
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
    """


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

        self.registry = CapabilityRegistry.from_plugin_modules(
            self.config.plugin_modules,
        )

        self.persistence = RouterWeightStorePersistence(
            WeightStorePersistenceConfig(
                path=self.config.weights_path,
            )
        )

        self.weight_store = weight_store or self._load_weight_store()

        self.map_layer_builder = MapLayerBuilder()

        self.output_storage = OutputStorage(
            OutputStorageConfig(
                root_dir=self.config.outputs_path,
            )
        )

        self.upload_storage = UploadStorage(
            UploadStorageConfig(
                root_dir=self.config.uploads_path,
            )
        )

        self.project_store = ProjectStore(
            ProjectStoreConfig(
                root_dir=self.config.projects_path,
            )
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

        if user_context:
            final_metadata["user_context"] = _json_safe(user_context)

        if metadata:
            final_metadata.update(dict(metadata))

        try:
            router = self._build_router()
            resolved_inputs = self._resolve_input_references(inputs)

            run_result = run_natural_query_with_routing_evidence(
                query,
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
                    "metadata": _json_safe(metadata or {}),
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
                        self.project_store.attach_request(
                            project_id,
                            final_request_id,
                        )
                    except Exception:
                        pass

                if self.config.persist_outputs:
                    manifest = self._persist_outputs_for_record(stored_record)

                    if project_id and isinstance(manifest, dict):
                        try:
                            self.project_store.attach_output(
                                project_id,
                                final_request_id,
                            )
                        except Exception:
                            pass

            return production_response

        except Exception as exc:
            failed_response = self.response_builder.build_dict(
                response={
                    "status": "failed",
                    "request_id": final_request_id,
                },
                error=exc,
                metadata=final_metadata,
            )

            self._remember(
                request_id=final_request_id,
                record={
                    "request_id": final_request_id,
                    "query": query,
                    "inputs": _json_safe(inputs),
                    "band_map": _json_safe(band_map or {}),
                    "user_context": _json_safe(user_context or {}),
                    "metadata": _json_safe(metadata or {}),
                    "error": repr(exc),
                    "production_response": failed_response,
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
            return self.project_store.create_project(
                name=name,
                description=description,
                metadata=metadata,
            )
        except ProjectStoreError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def list_projects(
        self,
    ) -> list[dict[str, Any]]:
        return self.project_store.list_projects()

    def get_project(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        try:
            return self.project_store.get_project(project_id)
        except ProjectStoreError as exc:
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
            payload = self.upload_storage.save_upload(
                filename=filename,
                content=content,
                content_type=content_type,
                kind=kind,
                user_context=user_context,
            )

            if project_id:
                self.project_store.attach_upload(
                    project_id,
                    payload["upload_id"],
                )
                payload["project_id"] = project_id

            return payload
        except UploadStorageError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def list_uploads(self) -> list[dict[str, Any]]:
        """
        List stored uploads.
        """
        return self.upload_storage.list_uploads()

    def get_upload_metadata(
        self,
        upload_id: str,
    ) -> dict[str, Any]:
        """
        Return upload metadata.
        """
        try:
            return self.upload_storage.read_metadata(upload_id)
        except UploadStorageError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_upload_file_path(
        self,
        upload_id: str,
    ) -> Path:
        """
        Return safe uploaded file path.
        """
        try:
            return self.upload_storage.get_file_path(upload_id)
        except UploadStorageError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_upload_file_media_type(
        self,
        upload_id: str,
    ) -> str:
        try:
            return self.upload_storage.get_media_type(upload_id)
        except UploadStorageError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

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
            raise OrchestratorServiceError(str(exc)) from exc


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
            return self.output_storage.read_manifest(request_id)
        except OutputStorageError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def list_output_files(
        self,
        request_id: str,
    ) -> list[dict[str, Any]]:
        """
        List persisted output files for a request.
        """
        try:
            return self.output_storage.list_files(request_id)
        except OutputStorageError as exc:
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
            return self.output_storage.get_file_path(
                request_id,
                filename,
            )
        except OutputStorageError as exc:
            raise OrchestratorServiceError(str(exc)) from exc

    def get_output_file_media_type(
        self,
        filename: str,
    ) -> str:
        return self.output_storage.get_media_type(filename)

    def _persist_outputs_for_record(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Internal helper to persist request outputs.
        """
        try:
            map_layers_payload = self.map_layer_builder.build_for_request_record(record)

            manifest = self.output_storage.save_request_record(
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
    ) -> dict[str, Any]:
        """
        Return Leaflet-ready map layers for a stored request.
        """
        record = self.get_request(request_id)

        if record is None:
            raise OrchestratorServiceError(
                f"Unknown request_id: {request_id}"
            )

        return self.map_layer_builder.build_for_request_record(record)

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
            "weights": self.get_weights(),
        }

    def _build_router(self) -> Any:
        base_router = KeywordScoringCapabilityRouter(
            registry=self.registry,
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
