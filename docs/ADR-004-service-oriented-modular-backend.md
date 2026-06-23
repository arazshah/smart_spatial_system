# ADR-004 — Service-Oriented Modular Backend Before Microservices

## Status

Accepted for Phase 6 planning.

## Context

The Smart Spatial System backend has grown significantly.

The current repository includes:

- FastAPI API routes
- query orchestration
- planning and QuerySpec logic
- DAG planning
- kernel execution bridge
- plugin/capability routing
- uploads
- projects
- data sources
- external connectors
- output persistence
- map layers
- generated documents
- feedback and learning signals
- runtime settings
- use-case-specific logic such as real-estate ranking

A large part of the system currently flows through `orchestrator/service.py`.
This file has become too large and contains many responsibilities.

Current risks:

1. `OrchestratorService` is becoming a God Service.
2. Core orchestration logic is mixed with upload, project, data source, output,
   plugin, report, and use-case logic.
3. Real-estate ranking logic is currently inside the core service, although it
   should be an isolated use case or plugin.
4. Runtime data such as outputs, uploads, projects, cache, and generated reports
   exists near the source tree and makes the repository harder to audit.
5. Backup files such as `.bak` and `.before-*` files exist inside source
   directories and create noise.
6. Upload, DataSource, Connector, and Dataset/DataAsset concepts need clearer
   boundaries.
7. Phase 7 will introduce or harden multi-source data connectors, so the backend
   needs cleaner internal boundaries before that phase.
8. Moving directly to independently deployed microservices now would introduce
   operational complexity before the internal architecture is stable.

## Decision

We will not immediately split the backend into independently deployed
microservices.

Instead, we will first move toward a service-oriented modular backend.

This means:

- keep one deployable backend process for now
- define clear internal service boundaries
- extract responsibilities out of `orchestrator/service.py`
- keep FastAPI as the API edge
- make `OrchestratorService` a facade/coordinator over internal services
- move use-case-specific logic out of the core orchestration layer
- define contracts between internal services
- prepare the codebase so selected internal services can later become real
  microservices if needed

This approach gives us the structural benefits of microservices without adding
distributed-system complexity too early.

## Architecture Direction

The target architecture is a modular service-oriented backend.

Internal service boundaries should include:

### API Edge

Responsible for:

- HTTP routes
- request validation
- response serialization
- HTTP error mapping
- OpenAPI exposure

The API layer should not contain heavy business logic.

### Query Orchestration Service

Responsible for:

- main `/query` flow
- input reference resolution coordination
- planner/executor coordination
- production response assembly
- request recording

It should coordinate other services instead of implementing every detail itself.

### Planning Service

Responsible for:

- QuerySpec planning
- deterministic planning
- LLM spec generation
- DAG plan creation
- kernel plan adaptation
- planning traces
- semantic planning context

### Execution Service

Responsible for:

- plan execution
- plugin/capability execution
- kernel execution bridge
- execution trace
- parity/debug metadata

### Plugin Registry Service

Responsible for:

- plugin discovery
- plugin configuration
- plugin enable/disable state
- capability registry
- weighted routing support
- plugin health/status views

### Data Connector Service

Responsible for:

- PostGIS connector
- WFS connector
- WMS connector
- URL connector
- CSV/table connector
- local raster/vector loader coordination
- connector registry
- normalized connector results
- source metadata and preview

This boundary is required before Phase 7.

### Upload Service

Responsible for:

- file upload storage
- upload metadata
- upload validation
- upload file path/media type
- parsed JSON metadata

### Project Service

Responsible for:

- project/workspace creation
- project metadata
- project uploads/data-sources/requests relationship

### Artifact/Output Service

Responsible for:

- output manifests
- output file persistence
- production response persistence
- audit record persistence
- output file listing
- output file download paths

### Map Layer Service

Responsible for:

- Leaflet-ready map layer generation
- GeoJSON output normalization
- map layer metadata

### Report/Document Service

Responsible for:

- generic report/document generation
- document metadata
- secure document download policy
- PDF/HTML rendering

Use-case-specific report logic must not live in core orchestration.

### Use Case Modules or Plugins

Responsible for domain-specific workflows such as:

- real-estate ranking
- risk analysis
- vegetation/NDVI workflows
- other future domain workflows

These should be isolated from the core backend.

A use case may start as:

```text
orchestrator/use_cases/<use_case_name>/


and later move to:

text
plugins/use_cases/<use_case_name>/


if needed.

Important Concept Boundaries

The following concepts must be kept distinct:

Upload

A physical or logical file uploaded by a user.

Examples:

GeoJSON file
raster JSON
GeoTIFF
CSV file
DataSource

A registered source of data that can be referenced, previewed, or queried.

Examples:

PostGIS table
WFS layer
WMS layer
URL-based GeoJSON
uploaded file registered as a project source
Connector

An adapter that knows how to connect to, fetch from, validate, or preview a
specific type of source.

Examples:

PostGISConnector
WFSConnector
WMSConnector
URLConnector
CSVConnector
LocalRasterConnector
LocalVectorConnector
Dataset/DataAsset

A normalized internal representation of data that the planner/executor/plugins
can consume.

Migration Strategy

We will use a strangler-style refactor.

For each extracted internal service:

Keep current behavior unchanged.
Add or keep focused tests.
Create the new service module.
Move logic from orchestrator/service.py into the new service.
Make OrchestratorService delegate to the new service.
Run relevant tests.
Commit.
Repeat.

No large rewrite should be done in one step.

Phase Alignment
Phase 5 — API Contract Stabilization Closure

This ADR belongs to the end of Phase 5 because it defines the architectural
direction required before continuing with backend decomposition.

Phase 6 — Backend Architecture Decomposition

Phase 6 will focus on:

repository hygiene
runtime directory standardization
service boundary extraction
OrchestratorService slimming
use-case isolation
preparing clean boundaries for connectors
Phase 7 — Multi-source Data Connectors

Phase 7 will focus on:

connector contracts
connector registry
PostGIS hardening
WFS/WMS hardening
URL/CSV connectors
local raster/vector connector alignment
metadata and preview normalization
Phase 8 — Backend Packaging + CLI

Phase 8 will focus on:

installable package
CLI entrypoints
runtime path configuration
package metadata
installation docs
Phase 9 — Backend API Finalization

Phase 9 will focus on:

public Pydantic schemas
unified error contract
status normalization
OpenAPI polish
API versioning decision
security/config/deployment readiness
Phase 10 — Frontend Readiness

Phase 10 will focus on:

frontend handoff
frontend API examples
UI readiness checklist
final frontend contract smoke tests

Frontend implementation should not start before this readiness phase.

Rules Going Forward
Do not add new business logic directly to orchestrator/service.py.
Prefer adding new logic behind internal service boundaries.
Keep use-case-specific logic outside the core orchestration layer.
Keep connector logic behind the Data Connector Service boundary.
Keep runtime artifacts out of source-code paths where possible.
Keep each migration step small, tested, and committed.
Preserve current behavior while extracting services.
Do not introduce distributed microservice deployment until internal service boundaries are stable.
Consequences
Positive
lower cognitive load
cleaner ownership of logic
easier testing
easier future microservice extraction
reduced risk of use-case-specific core pollution
better alignment with plugin-based architecture
safer preparation for Phase 7 connectors
Negative
requires disciplined incremental refactoring
temporarily increases the number of modules/files
requires careful test coverage
does not immediately solve deployment-level scaling
requires clear documentation to avoid another kind of fragmentation
Decision Summary

We choose:

text
Service-oriented modular backend first.
Microservice-ready design.
No immediate distributed microservice split.
Incremental extraction from OrchestratorService.


This is the safest path to reach the target product architecture without
breaking the current working backend.
