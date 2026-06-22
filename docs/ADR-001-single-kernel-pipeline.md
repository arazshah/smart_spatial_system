
ADR-001 — Single Kernel Pipeline
Status

Accepted.

Context

The current system contains multiple execution paths:

direct real-estate handling
direct vector display
status handling inside query
QuerySpec planning
legacy natural-query routing
hardcoded capability router

This increases complexity and makes the behavior difficult to reason about.

Decision

The official execution path of the system will be:

text
KernelRequest
  -> QueryContext
  -> SemanticContext
  -> QuerySpec
  -> DeterministicPlanner
  -> DagPlan
  -> DagExecutor
  -> CapabilityRegistry
  -> Plugins
  -> Artifacts
  -> UnifiedResponse


All new functionality must enter through this pipeline unless explicitly marked as transitional legacy.

Consequences

Positive:

cleaner architecture
easier testing
stable frontend/API contract
source-independent execution
case-study independence
plugin-first design

Negative:

migration work is required
legacy paths need to be wrapped or removed gradually
response schema must be normalized
Migration Rule

No new service-level direct handler should be added for a specific use case, data source, or output format.

If a capability is needed, it should be added as:

a plugin capability
an OP_CATALOG entry
a QuerySpec/DAG workflow