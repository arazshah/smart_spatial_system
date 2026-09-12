
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
- Phase 2 (unified response assembler): done, per the migration steps in
  docs/PHASE2_UNIFIED_RESPONSE_PLAN.md. `orchestrator/response_assembler.py`
  (`assemble_response`) is now wired into all four response sources -
  `ProductionResponseBuilder`/legacy fallback (both the success and
  exception paths), QuerySpec planning, vector display, and real-estate
  ranking (wired last, per the plan, since it's the README's flagship demo
  workflow) - each landed as its own commit gated on
  tests/test_query_response_shape_golden.py (added first, before any
  wiring) still passing except for the specific, intentional
  normalization at each step. The concrete inconsistency the plan called
  out - top-level `"status": "succeeded"` on three of the four sources vs.
  `ProductionResponseBuilder`'s `"status": "success"` - is closed: all
  four now normalize through the same `success`/`partial_success`/`failed`
  vocabulary, and `schema_version`/`success`/`artifacts` are always
  present. `SimpleResponseBuilder` (orchestrator/response_builder.py) was
  not touched directly - it is not one of the four dispatch sources, but
  it is not dead either: `run_natural_query_with_routing_evidence` (the
  live `natural_query_runner` callable, still using
  `SimpleCapabilityRouter`'s routing-evidence sibling per Phase 7's own
  note below) builds `run_result["response"]` with it, and
  `ProductionResponseBuilder.build_dict` falls back to that field when no
  explicit `response=` is passed - so its output is already covered
  transitively by step 3.1's wiring into `ProductionResponseBuilder`, one
  layer up. What's still open, and intentionally deferred as
  optional (step 4 of the plan): whether `direct_query_dispatch.py`'s
  handlers still need to return full response dicts at all, versus a
  smaller intermediate shape the assembler builds from, plus the
  frontend's defensive fallback-chasing in `InspectorPanel.jsx` (still
  works unchanged against the now-unified backend shape, but could be
  simplified as separate follow-up work).
- Phase 3 (planning default config): done - `OrchestratorServiceConfig`
  now has `query_spec_planning_enabled`/`llm_planning_enabled` fields,
  env vars remain a deployment-level override on top of them.
- Phase 4 (migrate vector direct path): substantively done, and its
  blocking condition has since resolved. `vector_display_handler.py`
  already delegates to `inspect_vector`/`display_vector_layer`/
  `summarize_vector_layer` through the capability registry rather than
  duplicating logic - no ad-hoc business logic remains outside a
  documented fallback path. `query_spec_planning_enabled` (Phase 3) now
  defaults to `True` (flipped 2026-09, after Phase 2's unified response
  assembler made the DAG path's and this handler's response shapes
  consistent): the QuerySpec/DAG path is attempted first for every
  query now, with a verified-safe fast-fail fallback to the legacy
  runner when planning can't run (no LLM API key configured - the
  common case without a deployer-provided key - or any other planning
  error; `_try_handle_query_with_planning`'s broad except clause
  returns `None` on any failure, confirmed with a real end-to-end query
  and no LLM configured: response still succeeds via the legacy
  fallback, with `metadata.planning_attempted`/`planning_error`
  recording why). Direct-dispatch handlers (real-estate, vector
  display) still run *before* the QuerySpec/DAG attempt in
  `direct_query_dispatch.py`, so this specific vector-display query
  type is still reached via its direct handler first, not through the
  DAG, when it matches - genuinely routing it through the DAG instead
  would mean reordering or removing that handler, which is a separate,
  not-yet-done step (see Phase 7's status note on why removing
  direct-dispatch/fallback code stays gated on more than just this flag
  flip).
- Phase 5 (real estate to plugin): steps 1-5 of 6 done, per
  docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md's migration plan. The MVP
  scoring/eligibility formula (`real_estate_scoring.py`) and the spatial
  enrichment additive-fallback (`real_estate_context.py`) are now exposed
  as plugin capabilities (`plugins/real_estate_scoring.py`,
  `plugins/real_estate_spatial_enrichment.py`, unchanged formula/logic
  underneath) and registered in `OP_CATALOG` as `real_estate_score`/
  `real_estate_spatial_enrich`. Real-estate ranking queries can now be
  routed through the same QuerySpec/DAG path as other planning queries -
  `real_estate_spatial_enrich -> real_estate_score -> filter_attribute
  (eligible=true) -> rank_features -> build_report`, the last three being
  already-registered generic ops, not new real-estate-specific ones -
  gated behind `real_estate_query_spec_planning_enabled` (same
  env-override-config precedence pattern as Phase 3's flags), which has
  the same safe fallback-on-failure guarantee `query_spec_planning_enabled`
  has (raised exception or a DAG result reporting `success=False` without
  raising both trigger a fallback to the legacy handler, with the
  failure reason preserved in the fallback response's metadata rather
  than silently discarded).
  **Default stays `False`** (a 2026-09 attempt to flip it to `True` was
  reverted the same day after an automated review on the flip's PR found a
  real gap: the ranking *outcome* has verified parity with the legacy
  handler - same top score, ranked order, eligible/rejected split - but
  the *response shape* does not. `build_query_spec_planning_response`,
  the generic planning response builder this path reuses, never
  reconstructs the legacy bridge's `result.ranking`/`result.rejected`,
  `summary`, analysis inspector, ranked-vector/rejected-table outputs, or
  rendered PDF/HTML document - fields `frontend/src/components/
  InspectorPanel.jsx` reads directly. The flag can still be enabled via
  config for callers that only need the ranking outcome and consume the
  planning response shape directly; flipping the default requires either
  making the planning response reconstruct those legacy fields or
  updating the frontend to consume the planning shape instead - not yet
  attempted).
  What's NOT done: step 6, removing
  `real_estate_ranking_direct_handler.py`'s registration from
  `direct_query_dispatch.py` - blocked on the response-shape parity gap
  above, in addition to this plan's and Phase 7's "remove legacy only
  after a tested replacement exists" rule.
- Phase 6 (source abstraction): steps 1-2 of 3 done, per
  docs/PHASE6_SOURCE_ABSTRACTION_PLAN.md's migration plan (step 3,
  optional reachability notes, skipped as redundant with that doc).
  Deduplicated two pieces that were genuinely unsafe/duplicated to leave
  as-is: local_vector_loader.py/local_raster_loader.py's byte-identical
  path/allowed-roots validation (security-relevant path-traversal
  guarding, done earlier), now also wms_wfs_fetcher.py/
  geocoding_resolver.py's byte-identical int-coercion helper
  (`plugins/_shared/numeric_validation.py::to_int`) - postgis_connector.py's
  own int handling turned out NOT to share this duplication once read
  side by side (a looser, isinstance-only convention, different call
  sites), so it was correctly left untouched, correcting the plan's
  initial characterization. Closed a real error-redaction gap: only
  postgis_connector.py routed failures through
  provider_error_mapping.py's credential redaction before this;
  wms_wfs_fetcher.py's HTTP failure paths and geocoding_resolver.py's
  provider-chain error metadata (`output_metadata["provider_errors"]`,
  visible in the response) now redact URLs/credentials too, closing the
  same risk class as this session's earlier SQL-injection/SSRF
  hardening. A genuine common source-capability contract with "optional
  semantic discovery" across all five source plugins remains a
  from-scratch design question, same territory as the newer roadmap's
  Phase 7 "Multi-source Data Connectors" in
  docs/ADR-004-service-oriented-modular-backend.md (which hasn't started
  yet either) - explicitly out of scope, confirmed and documented in
  detail (including that OP_CATALOG only maps 2 of these 5 plugins' 7
  capabilities, and 3 have no production caller anywhere outside their
  own tests) rather than attempted here. Treat as its own future effort.
- Phase 7 (legacy cleanup): step 1 of 1 scheduled steps done (2026-09) -
  planned in detail in docs/PHASE7_LEGACY_CLEANUP_PLAN.md. Verified: 3 of
  the 4 named legacy items (legacy `PlanNode`/`QueryPlan` in
  orchestrator/models.py, `run_natural_query_with_routing_evidence`,
  the "routing-aware raster-only planner"/`RoutingAwarePlanBuilder`)
  form one connected live production call chain rooted at
  query_execution_service.py's fallback natural_query_runner wiring -
  this is the path most `/query` requests for real-estate ranking and
  vector display still take today, since Phase 5's
  `real_estate_query_spec_planning_enabled` defaults to `False` (Phase 4's
  `query_spec_planning_enabled` now defaults `True`, but direct-dispatch
  handlers still run before the QuerySpec/DAG attempt - see Phase 4's own
  note above - and this fallback chain is still reached for any query type
  neither Phase 4 nor Phase 5's paths cover). Removing any piece of that
  chain now would break production, not just tests (11 test files
  exercise `run_natural_query_with_routing_evidence` alone) - this is a
  Phase 4/5 production-default/coverage decision to unblock, not a
  Phase 7 code task. The 4th item, `SimpleCapabilityRouter`
  (+ its only caller, `run_natural_query`, the non-routing-evidence
  variant), was different: no confirmed production caller found anywhere
  in smart_spatial_system/application/services/... - only reachable via
  orchestrator/__init__.py's package re-export and its own test suite.
  This plan's one scheduled action - mark (not remove)
  `SimpleCapabilityRouter`/`run_natural_query` as deprecated, since "no
  caller found by grep" isn't the same certainty as "confirmed dead" and
  it's still public orchestrator package surface - is now done: both
  carry deprecation docstrings explaining the finding and what to
  re-check before removal. No behavior change (full suite: same pass
  count before/after). Nothing else in this phase is scheduled yet - see
  docs/PHASE7_LEGACY_CLEANUP_PLAN.md's "What actually unblocks the rest
  of Phase 7" section for what has to happen first.

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