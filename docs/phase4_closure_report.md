# Phase 4 Closure Report — Backend Hardening and Structured Error Contract

## Status

Phase 4 is complete.

This phase focused on backend hardening, structured error normalization,
runtime execution reliability, plugin/provider error visibility, and
service/API boundary consistency.

## Goals

The main goals of Phase 4 were:

- Make backend failures structured, inspectable, and production-friendly.
- Preserve legacy behavior while adding structured error payloads.
- Improve planning, DAG, kernel, provider, plugin, input, and loader error paths.
- Ensure failed query responses expose actionable error metadata.
- Prepare the backend for frontend/API product integration.

## Completed Work

### 1. Shared Structured Error Contract

A common structured error contract was introduced and used across backend layers.

Structured errors include fields such as:

- `code`
- `category`
- `message`
- `retryable`
- `source`
- `details`

Sensitive values are sanitized/redacted before being exposed.

### 2. Planning and QuerySpec Error Handling

Planning-related exceptions are now mapped to structured errors.

Covered areas:

- QuerySpec generation
- QuerySpec contract validation
- planning build failures
- DAG validation
- DAG execution
- planning runtime failures

### 3. DAG Execution Structured Errors

DAG execution now preserves and exposes structured errors for:

- capability execution failure
- capability contract failure
- unresolved input/reference failure
- DAG validation failure
- provider-originated failures

### 4. Kernel Execution Structured Errors

Kernel execution bridge now exposes structured errors for:

- missing external inputs
- capability contract failures
- runtime execution failures
- kernel/planning result parity metadata

Kernel execution remains opt-in and safe by default.

### 5. Provider/PostGIS Error Wrapping

Provider errors, especially PostGIS execution failures, are now wrapped into
structured errors.

Covered examples:

- provider connection failure
- query execution failure
- provider operation metadata
- retryability flags
- secret redaction for DSN/password-like values

### 6. Plugin Registry / Loading Structured Errors

Plugin loading and registry registration failures now expose structured errors.

Covered cases:

- plugin import failure
- invalid plugin contract/manifest
- duplicate capability registration
- missing capability callable
- unexpected plugin registry failure

Tolerant plugin loading keeps `skipped_plugins[*].error` and adds
`skipped_plugins[*].structured_error`.

### 7. Input Reference / Upload Resolver Structured Errors

Upload/input reference resolution now carries structured errors.

Covered cases:

- invalid input payload
- missing upload reference
- unsupported reference kind
- unresolved upload reference
- plugin-based loader resolution failures

### 8. Loader Plugin Contract Structured Errors

Loader plugin contract failures now expose structured errors.

Covered cases:

- loader plugin import failure
- missing canonical callable
- invalid loader output
- loader execution failure
- unsupported loader kind

Both raster and vector loader contracts are covered.

### 9. Service/API Boundary Normalization

Service-level errors now preserve structured errors from inner exception chains.

Failed query responses expose structured errors at:

- top-level `structured_error`
- `metadata.structured_error`
- `metadata.service_structured_error`

API HTTP errors can expose structured error details when the underlying
service exception carries `.structured_error`.

### 10. Backward Compatibility

Phase 4 was implemented additively.

Preserved behaviors:

- legacy exception messages
- existing plain `error` fields
- tolerant plugin loading behavior
- non-tolerant plugin loading behavior
- default kernel execution disabled
- existing query response shape
- existing API behavior where structured errors are not available

## Major Files Added or Updated

Representative files:

- `orchestrator/error_contract.py`
- `orchestrator/provider_error_mapping.py`
- `orchestrator/plugin_error_mapping.py`
- `orchestrator/input_error_mapping.py`
- `orchestrator/service.py`
- `orchestrator/input_reference_resolver.py`
- `orchestrator/loader_plugin_contract.py`
- `orchestrator/planning/error_mapping.py`
- `orchestrator/planning/dag_executor.py`
- `orchestrator/planning/kernel_execution_bridge.py`
- `api/main.py`

Representative tests:

- `tests/test_error_contract.py`
- `tests/test_provider_error_mapping.py`
- `tests/test_plugin_error_mapping.py`
- `tests/test_input_error_mapping.py`
- `tests/test_planning_error_mapping.py`
- `tests/test_planning_dag_executor.py`
- `tests/test_kernel_execution_bridge.py`
- `tests/test_loader_plugin_contract.py`
- `tests/test_input_reference_resolver.py`
- `tests/test_service_structured_error_normalization.py`
- `tests/test_api_structured_error_normalization.py`

## Result

The backend now has a much more professional error-handling foundation.

The system can now explain failures across:

- planning
- DAG execution
- kernel execution
- provider execution
- PostGIS access
- plugin loading
- loader plugins
- upload/input resolution
- service response generation
- API boundary

This significantly improves debuggability, frontend integration readiness,
and production hardening.

## Remaining Non-Blocking Items

The following are not blockers for closing Phase 4, but can be handled in the
next phase:

- Full normalization of all admin/storage/project/plugin-state HTTP errors.
- Dedicated OpenAPI schema documentation for structured errors.
- Public API response contract documentation.
- More frontend-oriented examples for failed responses.
- Optional observability/logging correlation fields.
- Optional trace/span IDs for production deployments.

## Phase 4 Closure Criteria

- Structured error contract exists.
- Critical backend execution paths use structured errors.
- Structured errors are preserved through service failed responses.
- API boundary can expose structured errors.
- Tests were added for all critical paths.
- Targeted tests pass.
- Regression tests pass.
- Full test suite passes.
- Documentation updated.

