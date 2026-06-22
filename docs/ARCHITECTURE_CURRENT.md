
# Smart Spatial System — Current Architecture

## Purpose

Smart Spatial System is a plugin-based intelligent geospatial system. It accepts natural-language user queries and produces geospatial outputs such as vector layers, raster layers, tables, reports, files, and map-ready results.

The current project already contains strong foundations for a general geospatial intelligence kernel, but the runtime path has become multi-path and partially inconsistent.

## Current Strong Foundations

The following components are architecturally valuable and should be preserved:

- `QuerySpec` in `orchestrator/planning/spec.py`
- `DeterministicPlanner` in `orchestrator/planning/planner.py`
- `OP_CATALOG` in `orchestrator/planning/op_catalog.py`
- `DagPlan` / `DagNode` in `orchestrator/planning/dag.py`
- `DagExecutor` in `orchestrator/planning/dag_executor.py`
- `PlanningRunner` in `orchestrator/planning/runner.py`
- `CapabilityRegistry` in `orchestrator/capability_registry.py`
- `geochat_sdk` plugin contract:
  - `@capability(...)`
  - `auto_collect(...)`
  - `PLUGIN`
- PostGIS semantic resolver and semantic planning context
- Plugin-based spatial capabilities such as:
  - PostGIS connector
  - nearest neighbor
  - vector inspection/display
  - raster operations
  - reporting/PDF
  - export plugins

## Current Official-Looking Planning Path

The cleanest existing runtime spine is:

Natural Query
  -> LLM/Rule-based QuerySpec generation
  -> DeterministicPlanner
  -> OP_CATALOG
  -> DagPlan
  -> DagExecutor
  -> CapabilityRegistry
  -> geochat_sdk plugin functions
  -> planning outputs


This path should become the official kernel path.

Current Problems

The system currently has several parallel paths:

Direct real-estate ranking path inside OrchestratorService
Direct vector-display path inside OrchestratorService
System-status handling inside /query
QuerySpec planning path
Legacy natural-query routing path
Legacy SimpleCapabilityRouter
Multiple response builders / response shapes

This creates:

multiple execution paths
multiple response schemas
service-level domain logic
source-specific coupling
output-specific branches
hard-to-control growth in orchestrator/service.py
Current Response Problem

Different paths produce different response shapes:

ProductionResponseBuilder style:

status
request_id
answer
outputs
confidence
audit_ref
warnings
metadata

planning dict style:

success
outputs
layers
steps
warnings
metadata
report

direct handler style:

custom per-path dicts

This must be unified.

Current Source/Input Situation

The project already supports or is moving toward:

local vector loaders
local raster loaders
PostGIS connector
WMS/WFS fetcher
upload/project references

However, source handling is not yet fully unified under one source-capability abstraction.

Current Semantic Situation

The current semantic layer is especially important for PostGIS and natural language. It helps avoid LLM guessing of:

table names
column names
geometry columns
SQL predicates

Current user queries and data are often Persian. However, the target product should support English and other languages. Therefore, semantic concepts must be language-neutral internally.

Current Technical Debt

Critical:

orchestrator/service.py is too large and contains many responsibilities.
There are too many execution paths.
Response schema is not unified.
Case-study logic exists inside core service.
Planning enablement depends on environment flags instead of explicit service/kernel config.

High:

SimpleCapabilityRouter is legacy and hardcoded.
models.py contains first-generation planning models that overlap with DAG models.
Plugin module defaults are duplicated in multiple places.
Loader contract is useful but should be integrated into source capabilities.
LLM is sometimes called before it is clearly needed.

Medium:

Artifact contract is not explicit.
Multilingual semantic handling is not formalized.
Legacy router and QuerySpec planner coexist without a clear deprecation boundary.
Current Strategic Conclusion

The project should not continue growing through direct handlers or case-specific logic.

The clean future path is:

text
QuerySpec -> OP_CATALOG -> DagPlan -> DagExecutor -> CapabilityRegistry -> Plugins -> Artifacts -> UnifiedResponse


Everything outside this path should be treated as legacy, temporary, or migration target.
