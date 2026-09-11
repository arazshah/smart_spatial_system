
Refactor Plan — Architecture Stabilization

Status update (2026-09, verified against actual code - this doc predates
the ADR-004 service-oriented decomposition work and was not kept current
phase-by-phase; treat the per-phase notes below as the source of truth over
the original phase text where they disagree):

- Phase 0: done (docs exist).
- Phase 1 (artifact contract): substantively done, under a different name
  than planned - `orchestrator/kernel_artifacts.py` bridges current
  planning/direct outputs to `geochat_kernel.models.GeoArtifact`, not a new
  `orchestrator/artifacts.py`.
- Phase 2 (unified response assembler): NOT done. `SimpleResponseBuilder`
  (orchestrator/response_builder.py) and `ProductionResponseBuilder`
  (orchestrator/production_response.py) are still separate. Highest-risk
  remaining phase - touches every response shape the frontend depends on.
  Do this as its own dedicated effort with golden tests, not bundled with
  anything else.
- Phase 3 (planning default config): done - `OrchestratorServiceConfig`
  now has `query_spec_planning_enabled`/`llm_planning_enabled` fields,
  env vars remain a deployment-level override on top of them.
- Phase 4 (migrate vector direct path): substantively done.
  `smart_spatial_system/application/services/vector_display_handler.py`
  already delegates to `inspect_vector`/`display_vector_layer`/
  `summarize_vector_layer` through the capability registry rather than
  duplicating logic - verified no ad-hoc business logic remains outside
  a documented fallback path. What's NOT done: this is still a direct-
  dispatch shortcut rather than routed through QuerySpec/DAG, because
  `query_spec_planning_enabled` defaults to False (Phase 3) and OP_CATALOG
  already has `inspect_vector`/`display_vector_layer`/
  `summarize_vector_layer` entries capable of handling it - flipping the
  default or routing this specific query type through the DAG needs the
  Phase 2 unified response first, since the DAG path and this direct
  handler currently return different response shapes.
- Phase 5 (real estate to plugin): NOT done and larger than the text
  below suggests. Real-estate ranking is a fully separate direct-dispatch
  path (`direct_response_handler`/`preflight_direct_response_handler` in
  query_execution_service.py) with ZERO representation in OP_CATALOG -
  there is no `real_estate_rank` op today, so this phase requires
  designing the op/capability contract from scratch, not just moving
  code. This is also the real-estate site-ranking + PDF report workflow,
  one of the two workflows the README documents as a primary demo
  feature - real regression risk. Treat as its own planned effort.
- Phase 6 (source abstraction): partial - PostGIS/local vector/local
  raster/WMS/WFS connectors all exist as plugins, but are not unified
  behind a common source-capability contract.
- Phase 7 (legacy cleanup): NOT done. `SimpleCapabilityRouter` is still
  live (`orchestrator/capability_router.py`, used by
  `orchestrator/natural_query_runner.py`). Per this plan's own rule,
  remove only after Phases 4-6 give it a tested replacement path - it
  isn't safe to touch in isolation.

Goal

Make the system simpler, more general, more professional, and easier to control before it grows further.

Primary direction:

text
QuerySpec -> DAG -> Registry -> Plugin -> Artifact -> UnifiedResponse

Non-Goals for This Phase
No frontend rewrite
No new case-study feature
No query-specific patch
No source-specific shortcut
No output-specific response branch
Phase 0 — Documentation and Decisions

Status: current phase.

Create:

ARCHITECTURE_CURRENT.md
ARCHITECTURE_TARGET.md
ADR-001-single-kernel-pipeline.md
ADR-002-artifact-based-response.md
ADR-003-multilingual-semantic-layer.md
REFACTOR_PLAN.md

No runtime behavior changes.

Phase 1 — Artifact Contract

Add:

orchestrator/artifacts.py
tests for artifact normalization

Define canonical artifact types:

vector_layer
raster_layer
table
report
file
map_view
chart
text
json
Phase 2 — Unified Response Assembler

Add:

orchestrator/response_assembler.py
tests for response schema

The assembler should produce one response schema for:

planning results
legacy results
direct results
failure results
Phase 3 — Planning Default Config

Move planning flags from hidden environment-only behavior into explicit service/kernel config.

Target:

python
query_spec_planning_enabled: bool = True
llm_planning_enabled: bool = False


Environment variables may override config, but should not be the only source of truth.

Phase 4 — Migrate Vector Direct Path

Use existing plugin capabilities:

inspect_vector
display_vector_layer
summarize_vector_layer

Replace direct vector display handling with a QuerySpec/DAG path.

Phase 5 — Move Real Estate Logic to Plugin

Move real-estate ranking from service-level code into a domain plugin/workflow.

Target capability:

text
rank_real_estate_properties


Target op:

text
real_estate_rank

Phase 6 — Source Abstraction

Unify source plugins:

PostGIS
local vector
local raster
WMS/WFS
future sources

Each source should expose capabilities and optionally semantic discovery.

Phase 7 — Legacy Cleanup

Mark or move legacy modules:

SimpleCapabilityRouter
legacy PlanNode / QueryPlan
run_natural_query_with_routing_evidence
routing-aware raster-only planner

Remove only after replacement paths are tested.

Testing Strategy

Before each migration:

add contract tests
add golden tests when behavior may change
keep all existing tests green

Recommended command:

bash
pytest


For targeted phases:

bash
pytest tests/test_planning_runner.py tests/test_planning_dag_executor.py
pytest tests/test_orchestrator_service.py tests/test_orchestrator_service_integration.py

Commit Strategy

Use small commits:

docs only
artifact contract only
response assembler only
planning response migration
vector direct migration
config cleanup
real-estate plugin migration 