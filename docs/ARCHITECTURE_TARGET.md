
Smart Spatial System — Target Architecture
Target Identity

Smart Spatial System should be a product/API built on top of a general plugin-based geospatial intelligence kernel.

The target separation is:

geochat_sdk
  Defines plugin authoring primitives:
  - @capability
  - auto_collect
  - plugin manifest
  - typed outputs such as VectorOut/RasterOut

geochat_kernel
  Executes intelligent geospatial workflows:
  - request context
  - semantic context
  - QuerySpec
  - planning
  - DAG execution
  - capability registry
  - artifact normalization
  - response assembly

smart_spatial_system
  Product/API layer:
  - FastAPI endpoints
  - project/upload/history/persistence
  - UI integration
  - deployment config

Core Principle

The core must not depend on:

a specific case study
a specific language
a specific data source
a specific output format
a specific UI

The core only knows:

request
context
semantic concepts
QuerySpec
operations
DAG
capability
artifact
response envelope
Official Kernel Pipeline

The target pipeline is:

text
KernelRequest
  -> QueryContext
  -> InputResolver / SourceResolver
  -> SemanticContext
  -> QuerySpec
  -> DeterministicPlanner
  -> DagPlan
  -> DagExecutor
  -> CapabilityRegistry
  -> Plugin Execution
  -> ArtifactNormalizer
  -> UnifiedResponseAssembler
  -> Persistence / History / Project Attach

QuerySpec as the Main Intent Contract

QuerySpec should represent what the user wants, not how to execute it.

It should remain declarative and safe.

The LLM may produce or help produce QuerySpec, but it must not:

execute code
directly call plugins
generate unsafe raw SQL
invent table names
invent column names
invent runtime credentials
OP_CATALOG as the Bridge

OP_CATALOG maps logical operations to real capabilities.

Example:

text
logical op: spatial_nearest
  -> capability: find_nearest_neighbors

logical op: query_database
  -> capability: query_database_postgis

logical op: display_vector
  -> capability: display_vector_layer

logical op: summarize_vector
  -> capability: summarize_vector_layer


This is a central control point and should become versioned and documented.

Plugins

Everything domain-specific, source-specific, analysis-specific, and output-specific should be represented as a capability/plugin.

Examples:

text
Source plugins:
  - postgis_connector
  - local_vector_loader
  - local_raster_loader
  - wms_wfs_fetcher

Analysis plugins:
  - nearest_neighbor
  - buffer_analysis
  - spatial_join
  - distance_calculator
  - feature_scoring

Output plugins:
  - report_builder
  - pdf_renderer
  - data_writer_exporter
  - display_vector_layer

Domain plugins:
  - real_estate_ranking
  - future domain workflows

Artifact-Based Output Model

All outputs should be normalized into artifacts.

Artifact types:

text
vector_layer
raster_layer
table
report
file
map_view
chart
text
json


Map, table, PDF, GeoJSON, and report are not separate execution paths. They are artifacts produced by capabilities.

Unified Response

The response should have one stable schema.

Recommended public response shape:

json
{
  "schema_version": "1.0",
  "status": "success",
  "success": true,
  "request_id": "string",
  "answer": "string",
  "artifacts": [],
  "layers": [],
  "outputs": {
    "files": [],
    "vectors": [],
    "tables": [],
    "rasters": []
  },
  "report": null,
  "steps": [],
  "warnings": [],
  "next_actions": [],
  "metadata": {}
}


artifacts should be the canonical output model.

layers and outputs may remain as compatibility projections for the current frontend/API.

Multilingual Semantic Target

Internal concept IDs should be English/canonical.

Examples:

text
metro_station
shopping_center
park
hospital
school
road
building
property
risk_zone


Language-specific aliases should be externalized.

Example:

text
metro_station:
  en:
    - metro station
    - subway station
  fa:
    - ایستگاه مترو
    - مترو


This allows the system to support Persian today and English/product-level usage later.

Target State for service.py

orchestrator/service.py should become a thin adapter:

validate request
build context
call kernel
persist result
return response

It should not contain:

domain-specific scoring logic
direct vector display logic
source-specific execution logic
output-specific response branches
LLM-specific execution decisions mixed with business logic
Migration Strategy

Do not rewrite everything at once.

Use gradual migration:

Document architecture decisions.
Add artifact contract.
Add unified response assembler.
Route planning response through the assembler.
Move direct vector display into QuerySpec/DAG.
Move real-estate logic into plugin/workflow.
Make QuerySpec planning the default path.
Deprecate legacy natural-query routing.
Split service responsibilities. 