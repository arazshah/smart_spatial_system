# ADR-004 — Service-Oriented Modular Backend Before Microservices

## Status

Accepted / Proposed for Phase 6.

## Context

The Smart Spatial System backend has grown significantly. The current codebase
contains API routes, query orchestration, planning, execution, plugin management,
data-source connectors, uploads, projects, output storage, reports, feedback,
runtime settings, and use-case-specific logic.

A large portion of the orchestration and product logic currently lives in
`orchestrator/service.py`, which has become too large and contains multiple
responsibilities.

The project also contains runtime artifacts, generated outputs, uploads,
project state, generated reports, backup files, and frontend build artifacts in
or near the source tree. This makes audits and maintenance harder.

The long-term product goal is a professional, backend-first, plugin-based,
multi-source, language-aware smart spatial system. The system must remain
generic and should not become tied to one use case such as real-estate ranking.

## Decision

We will not split the backend into independently deployed microservices
immediately.

Instead, we will first move toward a service-oriented modular backend:

- Define clear bounded contexts.
- Extract internal service modules.
- Keep a single deployable backend process for now.
- Use explicit contracts between internal services.
- Keep FastAPI as the API edge.
- Reduce `OrchestratorService` into a facade/coordinator.
- Move use-case-specific logic out of the core orchestration layer.
- Prepare the architecture so that selected services can later become real
  microservices if needed.

This approach gives us most of the architectural benefits of microservices
without immediately introducing distributed-system complexity.

## Target Internal Service Boundaries

### API Edge

Responsibilities:

- HTTP routing
- request validation
- response serialization
- HTTP error mapping

No heavy business logic should live here.

### Query Orchestration Service

Responsibilities:

- main `/query` flow
- input reference resolution coordination
- planner/executor coordination
- production response assembly
- request recording

### Planning Service

Responsibilities:

- QuerySpec planning
- deterministic planning
- LLM spec generation
- DAG plan creation
- kernel plan adaptation
- planning trace

### Execution Service

Responsibilities:

- plan execution
- plugin/capability execution
- kernel execution bridge
- execution trace and parity metadata

### Plugin Registry Service

Responsibilities:

- plugin discovery
- plugin configuration
- plugin enable/disable state
- capability registry
- weighted routing support

### Data Connector Service

Responsibilities:

- PostGIS connector
- WFS/WMS connector
- URL connector
- CSV/table connector
- local raster/vector loader coordination
- connector registry
- normalized connector results
- source preview metadata

### Upload Service

Responsibilities:

- file upload storage
- upload metadata
- upload validation
- upload file download
- parsed JSON metadata

### Project Service

Responsibilities:

- project/workspace creation
- project metadata
- project uploads/data-sources/requests relationship

### Artifact/Output Service

Responsibilities:

- output manifests
- output file persistence
- map layer file persistence
- production response/audit file persistence
- output file download paths

### Map Layer Service

Responsibilities:

- Leaflet-ready layer generation
- GeoJSON output normalization
- map layer metadata

### Report/Document Service

Responsibilities:

- generic report/document generation
- document metadata
- secure document download policy
- PDF/HTML rendering

Use-case-specific report logic must not live in core orchestration.

### Use Case Plugins

Responsibilities:

- domain-specific analysis logic
- real-estate ranking
- specialized scoring
- specialized report templates

Use cases should be implemented as plugins or isolated domain modules.

## Current Problems to Address

1. `orchestrator/service.py` has too many responsibilities.
2. Real-estate ranking logic is currently inside the core service.
3. Runtime data exists in top-level source paths such as `outputs`, `uploads`,
   `projects`, and `artifacts/reports`.
4. Backup files exist inside source directories.
5. Upload and DataSource concepts are partially mixed.
6. External connector responses and errors are not fully normalized.
7. API and service layers sometimes contain direct connector logic.
8. The frontend directory and generated frontend artifacts add audit noise.
9. Some status and error contracts are not fully unified.
10. The system needs clearer boundaries before Phase 7 multi-source connectors.

## Migration Strategy

We will use a strangler-style refactor.

For each extracted service:

1. Keep current behavior unchanged.
2. Add focused tests if needed.
3. Create the new internal service module.
4. Move logic from `orchestrator/service.py` into the new service.
5. Make `OrchestratorService` delegate to the new service.
6. Run all relevant tests.
7. Commit.
8. Repeat.

No large rewrite should be done in one step.

## Phase Alignment

### Phase 6 — Backend Architecture Decomposition

- repository hygiene
- service boundary definition
- service extraction
- use-case isolation
- runtime data cleanup
- OrchestratorService slimming

### Phase 7 — Multi-source Data Connectors

- connector contract
- connector registry
- PostGIS/WFS/WMS/URL/CSV hardening
- connector metadata and preview normalization

### Phase 8 — Backend Packaging + CLI

- installable package
- CLI entrypoints
- runtime path configuration
- package metadata

### Phase 9 — Backend API Finalization

- public schemas
- OpenAPI finalization
- API versioning
- structured error consistency
- security and deployment readiness

### Phase 10 — Frontend Readiness

- frontend handoff
- frontend contract examples
- UI implementation readiness checklist

## Consequences

### Positive

- Lower cognitive load.
- Cleaner code ownership.
- Easier testing.
- Easier future microservice extraction.
- Better alignment with plugin-based architecture.
- Less risk of use-case-specific logic polluting the core.

### Negative

- Requires disciplined incremental refactoring.
- Adds internal service interfaces.
- May temporarily increase number of files/modules.
- Requires careful test coverage to avoid regressions.

## Rule

Until Phase 6 is complete, new functionality should avoid adding more business
logic directly into `orchestrator/service.py`.

