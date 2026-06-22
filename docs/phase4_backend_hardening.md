
---

## Initial Audit Findings

This section records the first Phase 4 audit findings after Phase 3 completion.
These findings are not immediate blockers. They define the hardening backlog.

### Backup / Migration Artifacts

The repository currently contains multiple backup and migration files, including:

- `*.bak`
- `*.before-*.bak`
- `*.bak.phase*`
- frontend backup files
- service/planning/plugin backup files

Decision:

- Do not delete these files in Step 1.
- Add a dedicated cleanup step in Phase 4.
- Use git history as the preferred long-term backup mechanism.
- Remove obsolete backup files only after review.

### Provider-Specific Logic in Core Areas

The audit found PostGIS-specific logic in core-adjacent modules such as:

- `orchestrator/service.py`
- `orchestrator/planning/llm_spec_generator.py`
- `orchestrator/planning/query_spec_contract.py`
- `orchestrator/planning/postgis_semantic_resolver.py`

This is acceptable as migration debt, but it should not become the long-term core architecture.

Target architecture:

- Core remains datasource-agnostic.
- Provider-specific schema discovery and semantic resolution move behind provider/plugin boundaries.
- Logical operations such as `query_database` remain provider-neutral.
- PostGIS remains one provider, not the core model.

### OSM/Table-Specific Hints

Some provider-specific semantic logic currently includes OSM-style table preferences such as:

- `planet_osm_point`
- `planet_osm_polygon`
- `planet_osm_line`
- `planet_osm_roads`

Decision:

- These hints should be treated as provider-specific heuristics.
- They should not leak into the generic core contract.
- Future hardening should isolate them behind provider configuration or provider plugins.

### Query Database Contract Scope

`query_spec_contract.py` currently focuses on `query_database/PostGIS`.

Decision:

- This is acceptable for the current provider-backed implementation.
- Phase 4 should introduce or prepare a provider-neutral query database contract layer.
- PostGIS-specific contract validation should be delegated to the PostGIS provider/adapter where possible.

### Fallbacks

The audit found multiple fallback mechanisms:

- deterministic fallback for LLM planning/routing
- JSON/adaptive loader fallback
- HTML fallback for PDF rendering
- geometry engine fallbacks
- database driver fallback
- experimental kernel execution fallback/default DAG path

Decision:

- Keep intentional fallbacks.
- Document whether each fallback is stable product behavior or migration-only behavior.
- Remove or constrain migration-only fallbacks in later hardening steps.

### Async Runtime Risk

The only direct event-loop risk found in the audit is:

- `execute_kernel_plan_with_capabilities_sync(...)` uses `asyncio.run(...)`

Decision:

- Keep the sync wrapper for tests/scripts/synchronous callers.
- Add async/runtime hardening in Phase 4 before using this path inside async web endpoints.
- Avoid calling the sync wrapper from an already-running event loop.

### Core Principle Risk Assessment

Current risk level after Phase 3:

- Case-study dependency: low to medium
- Language dependency: low
- Data-source dependency: medium
- Output-format dependency: low to medium
- UI dependency: low

Main concern:

- PostGIS/OSM-specific semantic planning exists in core-adjacent modules and should be isolated during Phase 4.


---

## Phase 4 Step 2 — Kernel Execution Configuration Policy

Kernel execution activation is hardened as part of Phase 4.

Current policy:

1. Kernel execution is disabled by default.
2. `OrchestratorServiceConfig.enable_kernel_execution=True` enables it globally.
3. Request metadata may always disable kernel execution for a request.
4. Request metadata may enable kernel execution only when
   `OrchestratorServiceConfig.allow_request_kernel_execution=True`.
5. Environment variables are treated as deployment-level overrides:
   - `SMART_SPATIAL_ENABLE_KERNEL_EXECUTION`
   - `ENABLE_KERNEL_EXECUTION`

This prevents arbitrary callers from enabling the experimental kernel execution
path unless the service explicitly allows request-level opt-in.

This keeps the backend safe while preserving the Phase 3 experimental kernel
runtime path as an explicitly controlled feature.

---

## Phase 4 Step 5 — DAG / Planning Structured Errors

DAG execution and planning-run errors are now mapped to the Phase 4 structured
error contract without removing legacy plain string errors.

Additive behavior:

- `DagExecutionResult.error` remains unchanged.
- `DagExecutionResult.structured_error` is added.
- `PlanningRunResult.structured_error` proxies the DAG execution structured
  error.
- Orchestrator planning metadata now includes:
  - `planning_summary.structured_error`
- Planning responses may also include top-level `structured_error`.

Current mappings:

- Invalid DAG plans:
  - code: `dag.validation_failed`
  - category: `validation_error`

- Unresolved DAG input references:
  - code: `dag.reference_resolution_failed`
  - category: `validation_error`

- Capability resolution failures:
  - code: `capability.resolution_failed`
  - category: `capability_resolution_error`

- Capability signature/contract failures:
  - code: `capability.contract_failed`
  - category: `capability_contract_error`

- Generic DAG execution failures:
  - code: `dag_execution.failed`
  - category: `internal_error`

Compatibility:

- Existing `error` strings remain available.
- Existing trace behavior remains unchanged.
- Structured errors are public-safe and sanitized by the shared error contract.

---

## Phase 4 Step 6 — Service-level Planning Structured Errors

Service-level planning exceptions are now mapped to the Phase 4 structured error
contract.

This covers errors that happen before or around DAG execution, including:

- LLM QuerySpec generation failures
- QuerySpec contract validation failures
- Deterministic planner failures
- DAG validation/execution exceptions raised through planning orchestration
- Runtime planning exceptions

Additive behavior:

- Existing `planning_error` metadata remains unchanged.
- New `planning_structured_error` metadata is added when
  `_try_handle_query_with_planning(...)` catches a planning exception.
- The planning fallback behavior remains unchanged; the service may still fall
  back to the non-QuerySpec routing path after recording the structured planning
  error.

Current mappings:

- `LLMSpecGenerationError`
  - code: `planning.llm_spec_generation_failed`
  - category: `planning_error`
  - retryable: true for timeout/5xx/rate-limit-like messages

- `PlanningError`
  - code: `planning.failed`
  - category: `planning_error`

- `DagValidationError`
  - code: `dag.validation_failed`
  - category: `validation_error`

- `DagExecutionError`
  - code: `dag.execution_failed`
  - category: `planning_error`

- `ValueError`
  - code: `planning.validation_failed`
  - category: `validation_error`

- `RuntimeError`
  - code: `planning.runtime_failed`
  - category: `planning_error`

- Other exceptions
  - code: `planning.unexpected_exception`
  - category: `internal_error`

Security:

- Sensitive fields in details remain redacted by the shared error contract.

---

## Phase 4 Step 7 — Provider / Plugin Structured Errors

Provider and plugin failures now have a shared structured error mapping layer.

Additions:

- `orchestrator.provider_error_mapping`
- `ProviderExecutionError`
  - subclasses `ValueError` for backward compatibility
  - carries `.structured_error`
- PostGIS query execution failures now raise provider-aware errors while keeping
  legacy ValueError compatibility.
- DAG execution preserves nested provider structured errors when a provider
  exception is raised from a capability.
- Kernel execution bridge preserves nested structured errors when exposed through
  the exception chain.

Current provider mappings:

- Connection/auth/availability failures:
  - code: `provider.connection_failed`
  - category: `provider_error`

- SQL/query failures:
  - code: `provider.query_failed`
  - category: `provider_error`

- Invalid provider configuration:
  - code: `provider.configuration_invalid`
  - category: `configuration_error`

- Unknown provider failures:
  - code: `provider.failed`
  - category: `provider_error`

Security:

- Provider error messages redact common secret patterns such as:
  - `password=...`
  - URL credentials like `postgresql://user:secret@host/db`
- Structured error details are still sanitized by the shared error contract.
