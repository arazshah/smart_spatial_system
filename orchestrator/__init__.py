"""
Natural-query orchestration layer for Smart Spatial System.

This package currently contains a simple deterministic E2E runtime.
It will evolve into registry-backed routing, DAG planning, execution tracing,
LLM fallback, audit logging, self-learning statistics, feedback loops, learning
signals, weight proposals, weighted routing, and production response building.
"""

from orchestrator.audit import AuditConfig, ExecutionAuditBuilder
from orchestrator.capability_registry import CapabilityRegistry, RegistryBackedCapabilityRouter
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter
from orchestrator.feedback import (
    FeedbackCollector,
    FeedbackConfig,
    FeedbackRecord,
    UserFeedbackInput,
)
from orchestrator.learning_signals import (
    LearningSignalConfig,
    RouterLearningSignal,
    RouterLearningSignalBuilder,
    RouterLearningSignalCollector,
)
from orchestrator.llm_gate import (
    LLMGate,
    LLMGateBlockedError,
    LLMGateResult,
    LLMBudgetPolicy,
    LLMProviderStub,
    LLMProviderUnavailableError,
)
from orchestrator.natural_query_runner import run_natural_query
from orchestrator.loader_plugin_contract import (
    LOADER_PLUGIN_CONTRACT_VERSION,
    LoaderPluginContractError,
    load_with_loader_contract,
    normalize_raster_loader_output,
    normalize_vector_loader_output,
)
from orchestrator.input_reference_resolver import (
    UploadReferenceResolver,
    UploadReferenceResolverConfig,
    UploadReferenceResolverError,
)
from orchestrator.output_storage import (
    OUTPUT_STORAGE_SCHEMA_VERSION,
    OutputStorage,
    OutputStorageConfig,
    OutputStorageError,
)
from orchestrator.project_store import (
    PROJECT_STORE_SCHEMA_VERSION,
    ProjectStore,
    ProjectStoreConfig,
    ProjectStoreError,
)
from orchestrator.production_response import (
    ProductionResponse,
    ProductionResponseBuilder,
    ProductionResponseConfig,
)
from orchestrator.router_decision import RouterDecision, RouterDecisionConfig, RouterDecisionLayer
from orchestrator.routing_aware_natural_query_runner import run_natural_query_with_routing_evidence
from orchestrator.routing_aware_plan_builder import RoutingAwarePlanBuilder
from orchestrator.service import (
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
    OrchestratorServiceError,
)
from orchestrator.statistics import ExecutionStatisticsCollector, StatisticsConfig
from orchestrator.weight_proposals import (
    InMemoryRouterWeightStore,
    RouterWeightProposalCollector,
    RouterWeightProposalEngine,
    WeightProposal,
    WeightProposalConfig,
    WeightStoreConfig,
)
from orchestrator.upload_storage import (
    UPLOAD_STORAGE_SCHEMA_VERSION,
    UploadStorage,
    UploadStorageConfig,
    UploadStorageError,
)
from orchestrator.weight_store_persistence import (
    RouterWeightStorePersistence,
    WeightStorePersistenceConfig,
    WeightStorePersistenceError,
)
from orchestrator.weighted_router import (
    WeightedCapabilityRouter,
    WeightedRouterConfig,
)

__all__ = [
    "AuditConfig",
    "ExecutionAuditBuilder",
    "CapabilityRegistry",
    "RegistryBackedCapabilityRouter",
    "KeywordScoringCapabilityRouter",
    "FeedbackCollector",
    "FeedbackConfig",
    "FeedbackRecord",
    "UserFeedbackInput",
    "LearningSignalConfig",
    "RouterLearningSignal",
    "RouterLearningSignalBuilder",
    "RouterLearningSignalCollector",
    "LLMGate",
    "LLMGateBlockedError",
    "LLMGateResult",
    "LLMBudgetPolicy",
    "LLMProviderStub",
    "LLMProviderUnavailableError",
    "OUTPUT_STORAGE_SCHEMA_VERSION",
    "LOADER_PLUGIN_CONTRACT_VERSION",
    "LoaderPluginContractError",
    "load_with_loader_contract",
    "normalize_raster_loader_output",
    "normalize_vector_loader_output",
    "UploadReferenceResolver",
    "UploadReferenceResolverConfig",
    "UploadReferenceResolverError",
    "OutputStorage",
    "OutputStorageConfig",
    "OutputStorageError",
    "PROJECT_STORE_SCHEMA_VERSION",
    "ProjectStore",
    "ProjectStoreConfig",
    "ProjectStoreError",
    "ProductionResponse",
    "ProductionResponseBuilder",
    "ProductionResponseConfig",
    "RouterDecision",
    "RouterDecisionConfig",
    "RouterDecisionLayer",
    "RoutingAwarePlanBuilder",
    "DEFAULT_SAFE_PLUGIN_MODULES",
    "OrchestratorService",
    "OrchestratorServiceConfig",
    "OrchestratorServiceError",
    "ExecutionStatisticsCollector",
    "StatisticsConfig",
    "InMemoryRouterWeightStore",
    "RouterWeightProposalCollector",
    "RouterWeightProposalEngine",
    "WeightProposal",
    "WeightProposalConfig",
    "WeightStoreConfig",
    "RouterWeightStorePersistence",
    "UPLOAD_STORAGE_SCHEMA_VERSION",
    "UploadStorage",
    "UploadStorageConfig",
    "UploadStorageError",
    "WeightStorePersistenceConfig",
    "WeightStorePersistenceError",
    "WeightedCapabilityRouter",
    "WeightedRouterConfig",
    "run_natural_query",
    "run_natural_query_with_routing_evidence",
]
