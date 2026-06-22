# Smart Spatial System Backend Roadmap & Checklist

This document is the operational checklist for building the backend of
`smart_spatial_system`.

It must be used as the shared reference during development so that the work does
not drift into unrelated paths.

The primary goal is to build a complete, professional, general-purpose backend
package for a web-based intelligent spatial system.

---

## 0. Final Product Goal

The final backend product should be installable as a Python package:

pip install smart_spatial_system


After installation, the user should be able to run a backend web service, for
example:

smart-spatial-api serve --host 0.0.0.0 --port 8000


or:


python -m smart_spatial_system serve --host 0.0.0.0 --port 8000


The backend should expose APIs for:


- project creation and management
- data source registration and inspection
- uploads
- PostGIS connection
- vector/raster/file/API source handling
- plugin discovery and plugin state management
- natural-language query execution
- LLM-controlled planning
- semantic schema/context discovery
- safe QuerySpec generation and validation
- plugin-based spatial processing
- map/table/report/document outputs
- execution trace and audit
- output persistence and download


The frontend will be developed separately.

The backend must be usable by any frontend through HTTP APIs.

1. Product Principles

The system must remain:

text
[ ] General-purpose, not hardcoded for one dataset or one query
[ ] Plugin-based
[ ] Multi-source
[ ] LLM-connected but controlled
[ ] Multilingual at query/semantic level
[ ] English-coded internally
[ ] Persian-friendly for current tests and data
[ ] Backend-first
[ ] Frontend-independent
[ ] Packageable and installable
[ ] Professional and testable

2. Architecture Principles

The intended architecture is:

text
geochat_kernel
  Core runtime, contracts, registries, execution pipeline.

geochat_sdk
  SDK for writing kernel-native plugins.

smart_spatial_system
  Product backend, API, project/data/plugin management,
  semantic planning, source connectors, and product response projection.


Rules:

text
[ ] Do not build a second permanent kernel inside smart_spatial_system.
[ ] Use geochat_kernel contracts as the runtime source of truth.
[ ] Use geochat_sdk for plugin authoring.
[ ] Keep QuerySpec/semantic planning because it controls the LLM.
[ ] Do not allow raw uncontrolled LLM execution.
[ ] Do not hardcode OSM Tehran assumptions into core logic.
[ ] Do not create new direct execution paths unless documented as temporary.

3. Current Status

Completed:

text
[x] Kernel/SDK audit completed
[x] SDKPlugin confirmed as kernel-native BasePlugin
[x] Kernel QueryPipeline successfully tested with SDK plugin
[x] QuerySpec planning path audited
[x] OrchestratorService multi-path complexity identified
[x] Kernel/SDK/Product alignment document created
[x] docs/KERNEL_SDK_PRODUCT_ALIGNMENT.md committed


Important findings:

text
[x] geochat_kernel and geochat_sdk are compatible
[x] smart_spatial_system currently has parallel runtime paths
[x] QuerySpec, op_catalog, semantic resolver are valuable
[x] Production response should move toward artifact/GeoResponse alignment
[x] OrchestratorService must eventually become thinner

4. Phase 0 - Audit and Documentation

Goal:

text
Understand the current system and freeze architectural direction.


Checklist:

text
[x] Audit geochat_kernel
[x] Audit geochat_sdk
[x] Audit SDK plugin execution path
[x] Audit OrchestratorService
[x] Audit QuerySpec planning
[x] Audit op_catalog
[x] Audit capability registry
[x] Audit production response
[x] Create kernel/sdk/product alignment document
[x] Commit alignment document
[x] Create backend roadmap checklist document
[x] Create backup of current project state


Exit criteria:

text
[x] Roadmap checklist exists
[x] Backup exists
[x] git working tree status is known

5. Phase 1 - Artifact and Response Alignment

Goal:

text
Make planning outputs artifact-aware without changing the whole runtime.


Current problem:

text
planning_result.output_nodes are converted manually to outputs/layers/tables/files.


Target:

text
planning_result.output_nodes
  -> output_to_artifact(...)
  -> GeoArtifact-compatible public artifact
  -> existing frontend/API outputs


Checklist:

text
[x] Audit orchestrator/kernel_artifacts.py
[x] Audit tests related to artifacts/output handling
[x] Identify current planning response shape
[x] Add artifacts to planning response without removing existing outputs/layers
[x] Preserve frontend-compatible response fields
[x] Add/adjust tests
[x] Run targeted tests
[x] Commit Phase 1 step


Exit criteria:

text
[ ] Planning responses include normalized artifacts
[ ] Existing outputs/layers still work
[ ] Tests are green

6. Phase 2 - QuerySpec to Kernel QueryPlan Mapping

Goal:

text
Prepare QuerySpec planning for kernel-native execution.


Current path:

text
QuerySpec
  -> DeterministicPlanner
  -> DagPlan / DagNode
  -> DagExecutor


Target path:

text
QuerySpec
  -> QueryPlan / PlanStep


Checklist:

text
[ ] Design mapping: DagPlan -> QueryPlan
[ ] Design mapping: DagNode -> PlanStep
[ ] Decide whether to build adapter or replace DeterministicPlanner output
[ ] Implement minimal adapter
[ ] Add tests for simple QuerySpec
[ ] Add tests for multi-step spatial QuerySpec
[ ] Validate metadata preservation
[ ] Commit Phase 2 step


Exit criteria:

text
[ ] QuerySpec can produce a kernel QueryPlan
[ ] Existing DAG path still works during migration
[ ] Tests are green

7. Phase 3 - Kernel-native Planning Execution

Goal:

text
Execute QuerySpec-derived plans through geochat_kernel runtime.


Target:

text
QuerySpec
  -> Kernel QueryPlan
  -> Kernel PlanExecutor
  -> StepHandlerRegistry
  -> ExecutionArtifact


Checklist:

text
[ ] Audit kernel PlanExecutor expectations
[ ] Audit SDKStepHandler parameter/input behavior
[ ] Ensure product plugins register StepHandlers correctly
[ ] Execute a simple QuerySpec through kernel runtime
[ ] Execute a vector operation through kernel runtime
[ ] Execute a PostGIS QuerySpec through kernel runtime
[ ] Compare old DagExecutor output with kernel output
[ ] Keep fallback temporarily
[ ] Add tests
[ ] Commit Phase 3 step


Exit criteria:

text
[ ] QuerySpec path can execute through kernel runtime
[ ] ExecutionArtifact outputs are produced
[ ] Existing API behavior remains compatible

8. Phase 4 - Product-aware GeoResponse Projection

Goal:

text
Make response creation consistent, artifact-based, and frontend-friendly.


Target:

text
ExecutionArtifact / GeoArtifact
  -> GeoResponse
  -> Product API response


Checklist:

text
[ ] Define product API response projection from GeoResponse
[ ] Preserve outputs/layers/tables/documents/inspector fields
[ ] Support vector layers
[ ] Support tables
[ ] Support reports
[ ] Support PDF/documents
[ ] Support raster references
[ ] Support generic payloads
[ ] Support warnings and trace
[ ] Add tests
[ ] Commit Phase 4 step


Exit criteria:

text
[ ] Main response shape is consistent
[ ] Product response comes from artifacts/GeoResponse
[ ] Existing frontend expectations are preserved

9. Phase 5 - Plugin Runtime Consolidation

Goal:

text
Move plugin loading and execution toward geochat_kernel/geochat_sdk.


Checklist:

text
[ ] Audit all plugins
[ ] Identify SDK-based plugins
[ ] Identify old/direct capability assumptions
[ ] Ensure PostGIS connector is a first-class plugin
[ ] Use kernel container as source of plugin runtime truth
[ ] Keep product PluginStateStore for enable/disable policy
[ ] Expose plugin inventory through API
[ ] Add tests for plugin loading
[ ] Commit Phase 5 step


Exit criteria:

text
[ ] Plugin runtime is kernel-aligned
[ ] Product layer manages plugin state but does not duplicate kernel runtime

10. Phase 6 - Semantic and LLM Control Layer

Goal:

text
Allow natural-language queries while preventing uncontrolled LLM behavior.


Checklist:

text
[ ] Strengthen QuerySpec contract
[ ] Strengthen validator
[ ] Improve semantic planning context
[ ] Improve PostGIS schema discovery
[ ] Improve semantic aliases for Persian and English
[ ] Normalize Persian text
[ ] Prevent raw SQL unless explicitly safe and controlled
[ ] Inject runtime connection details only from backend
[ ] Add ambiguity handling
[ ] Add tests with real Persian queries
[ ] Add tests with English queries
[ ] Commit Phase 6 step


Exit criteria:

text
[ ] LLM emits controlled QuerySpec
[ ] Backend validates and executes safely
[ ] Persian and English natural queries are supported

11. Phase 7 - Multi-source Data Connectors

Goal:

text
Support different spatial and non-spatial data sources through general connectors.


Target sources:

text
[ ] PostGIS
[ ] GeoJSON
[ ] Shapefile
[ ] GeoPackage
[ ] CSV with coordinates
[ ] Raster / GeoTIFF
[ ] WMS
[ ] WFS
[ ] XYZ tiles
[ ] REST API sources
[ ] Uploaded project files
[ ] Remote URLs


Checklist:

text
[ ] Define source connector interface
[ ] Standardize source inspection result
[ ] Standardize schema/layer metadata
[ ] Standardize source-to-artifact outputs
[ ] Implement or stabilize PostGIS connector
[ ] Implement file/vector connector
[ ] Implement raster connector
[ ] Add API endpoints for data source management
[ ] Add tests
[ ] Commit Phase 7 step


Exit criteria:

text
[ ] User can register data sources
[ ] System can inspect them
[ ] Semantic context can use them
[ ] Queries can run against them

12. Phase 8 - Backend Packaging and CLI

Goal:

text
Make smart_spatial_system installable and runnable as a backend service.


Target:

bash
pip install smart_spatial_system
smart-spatial-api serve --host 0.0.0.0 --port 8000


Checklist:

text
[ ] Audit package layout
[ ] Decide final Python package name
[ ] Prepare pyproject.toml
[ ] Define dependencies
[ ] Define optional extras: postgis, raster, dev, llm
[ ] Add CLI entrypoint
[ ] Add server entrypoint
[ ] Add environment/config loading
[ ] Add production-ready settings
[ ] Add health endpoint
[ ] Add OpenAPI docs
[ ] Test pip install locally
[ ] Test CLI command
[ ] Commit Phase 8 step


Exit criteria:

text
[ ] Package installs in a clean virtualenv
[ ] Web service starts from CLI
[ ] API endpoints are reachable

13. Phase 9 - Backend API Finalization

Goal:

text
Expose a clean HTTP API for any separate frontend.


Expected API areas:

text
[ ] Health/status
[ ] Runtime settings
[ ] Projects
[ ] Uploads
[ ] Data sources
[ ] Data source preview/inspection
[ ] Plugins
[ ] Query execution
[ ] Request history
[ ] Output manifests/files
[ ] Map layers
[ ] LLM smoke test/settings
[ ] Feedback


Checklist:

text
[ ] Audit current API routes
[ ] Remove duplicate/inconsistent routes
[ ] Standardize response envelopes
[ ] Add OpenAPI tags
[ ] Add error response format
[ ] Add request IDs
[ ] Add tests
[ ] Commit Phase 9 step


Exit criteria:

text
[ ] Frontend can be built independently against stable API
[ ] OpenAPI docs are usable

14. Phase 10 - Frontend Readiness

Goal:

text
Prepare backend for separate frontend development.


Checklist:

text
[ ] Stable API contract
[ ] Stable query response format
[ ] Stable map layer format
[ ] Stable table/document output format
[ ] Stable plugin manager API
[ ] Stable data source manager API
[ ] Stable project API
[ ] Example frontend integration requests
[ ] Example Persian query scenarios
[ ] Example PostGIS scenarios
[ ] Commit frontend readiness docs


Exit criteria:

text
[ ] Backend is ready for frontend development
[ ] UI can consume backend without hidden assumptions

15. Current Immediate Next Steps

The next immediate work items are:

text
[ ] Create this roadmap checklist document
[ ] Create project backup
[ ] Inspect orchestrator/kernel_artifacts.py
[ ] Inspect related tests
[ ] Start Phase 1: artifact and response alignment

16. Definition of Done for Each Step

Every implementation step should follow this process:

text
[ ] Read relevant code before changing it
[ ] Explain intended change
[ ] Make small change
[ ] Run targeted tests
[ ] Run broader tests if needed
[ ] Check git diff
[ ] Commit with clear message
[ ] Update this checklist if a milestone is completed

17. Do Not Forget

Important permanent reminders:

text
[ ] Do not use applypatch for this project.
[ ] Prefer direct file creation with cat or Python scripts.
[ ] Keep code clean and avoid multiple competing paths.
[ ] Keep backend general and plugin-based.
[ ] Keep Persian support, but internal identifiers should remain English.
[ ] Keep PostGIS general, not Tehran-specific.
[ ] Keep LLM controlled by contracts and validators.
[ ] Backend completeness has priority before frontend rebuild.
