# Kernel / SDK / Product Alignment

This document records the current architectural understanding of the
`smart_spatial_system` backend and defines the alignment direction between:

- `geochat_kernel`
- `geochat_sdk`
- `smart_spatial_system`

The goal is to prevent additional parallel runtime paths and to guide the
backend toward a clean, product-ready, kernel-native architecture before major
frontend work continues.

---

## 1. Original System Philosophy

The intended architecture is:

```text
geochat_kernel
  Core runtime, contracts, registries, execution pipeline.
  It should remain generic, plugin-based, and independent of product concerns.

geochat_sdk
  Plugin authoring layer.
  It provides decorators, input/output wrappers, and SDKPlugin abstractions
  that speak the language of the kernel.

smart_spatial_system
  Product/application layer.
  It provides APIs, UI integration, project/upload/output management,
  product plugins, semantic planning, persistence, and frontend-facing
  response projection.


The product must use the kernel and SDK instead of becoming a second kernel.

2. Confirmed Kernel/SDK Compatibility

Audit confirmed that geochat_sdk.SDKPlugin is kernel-native:

text
issubclass(SDKPlugin, geochat_kernel.contracts.plugin.BasePlugin) == True


Also confirmed:

text
geochat_sdk PluginManifest == geochat_kernel PluginManifest
geochat_sdk CapabilityRegistration builds geochat_kernel CapabilityDescriptor
SDKPlugin registers:
  - CapabilityDescriptor
  - SDKPlanner
  - SDKStepHandler
  - SDKResultFusion


A minimal diagnostic plugin was successfully executed through:

text
KernelAppContainer
  -> QueryPipeline
  -> KeywordRouter
  -> PlannerRegistry
  -> PlanExecutor
  -> StepHandlerRegistry
  -> FusionRegistry
  -> GeoResponse


Therefore, no SDKPluginAdapter(BasePlugin) is required.

3. Kernel Contracts That Should Be Source of Truth

The following kernel concepts should be treated as source-of-truth runtime
contracts:

text
QueryIR
QueryPlan
PlanStep
ExecutionArtifact
GeoArtifact
GeoResponse
CapabilityDescriptor
PluginManifest
ExecutionContext
KernelAppContainer
PluginLoader
QueryPipeline
PlanExecutor
CapabilityRegistry
PlannerRegistry
StepHandlerRegistry
FusionRegistry
RouterRegistry


New product code should not introduce competing equivalents unless it is an
explicit temporary adapter.

4. Current Product Runtime Situation

smart_spatial_system currently contains several runtime paths:

text
OrchestratorService.handle_query
  -> real_estate_ranking_bridge
  -> system_status_guard
  -> missing_real_estate_inputs_guard
  -> vector_display capability_bridge
  -> query_spec_planning
  -> legacy natural query routing
  -> fallback error response


This explains why the backend has become large and hard to reason about.

The current orchestrator/service.py acts as a God Object. It currently owns:

text
- query handling
- LLM intent planning
- LLM QuerySpec generation
- semantic PostGIS context discovery
- direct vector display/summary bridge
- direct real-estate ranking bridge
- plugin enable/disable logic
- routing
- feedback and learning signals
- project/upload/data-source management
- output persistence
- map layer serving
- response building


This should be reduced over time.

5. Current QuerySpec Planning Path

The current controlled planning path is:

text
LLMQuerySpecGenerator
  -> QuerySpec
  -> validate_query_spec_contract
  -> DeterministicPlanner
  -> DagPlan
  -> DagExecutor
  -> output_nodes
  -> manual production response dict


This path is valuable because it constrains the LLM:

text
The LLM describes what to do.
The backend validates the contract.
Runtime secrets and connection details are injected by the backend.
The deterministic planner maps logical operations to registered capabilities.


However, this path is not kernel-native yet:

text
It does not produce QueryPlan.
It does not use PlanStep.
It does not use PlanExecutor.
It does not produce ExecutionArtifact as the primary output.
It does not compose GeoResponse as the primary response.


Migration direction:

text
QuerySpec
  -> ProductQuerySpecPlanner(BasePlanner)
  -> QueryPlan / PlanStep
  -> PlanExecutor
  -> ExecutionArtifact
  -> Product-aware Fusion/Composer
  -> GeoResponse
  -> Product API projection

6. Value of QuerySpec, Semantic Context, and Op Catalog

The following product-side planning components are valuable and should not be
discarded:

text
QuerySpec
OperationSpec
OutputSpec
LLMQuerySpecGenerator
QuerySpec contract validator
SemanticPlanningContext
PostGIS semantic resolver
DeterministicPlanner
op_catalog


Their role is to prevent free-form LLM execution and to provide a controlled
semantic planning layer.

The op_catalog is especially important because it maps logical operations to
real capabilities:

text
query_database       -> query_database_postgis
load_postgis_layer   -> query_database_postgis
spatial_nearest      -> find_nearest_neighbors
nearest_neighbor     -> find_nearest_neighbors
filter_attribute     -> filter_features
sort_limit           -> filter_features
distance_to          -> calculate_distances
display_vector       -> display_vector_layer
summarize_vector     -> summarize_vector_layer


This allows the LLM to emit stable logical operations instead of guessing
plugin implementation details.

7. Current Duplicate / Temporary Components

The following product components duplicate or partially overlap kernel runtime
concepts and should be treated as temporary or migration targets:

text
orchestrator.capability_registry.CapabilityRegistry
orchestrator.models.QueryPlan
orchestrator.models.PlanNode
orchestrator.planning.DagPlan
orchestrator.planning.DagNode
orchestrator.planning.DagExecutor
orchestrator.production_response.ProductionResponseBuilder
legacy routing-aware natural query runner
direct vector display bridge
direct real-estate ranking bridge


They should not be expanded into a second permanent kernel.

8. Product-Owned Components That Should Remain in smart_spatial_system

The following components are product/application concerns and should remain in
smart_spatial_system, although they may be reorganized:

text
ProjectStore
UploadStorage
OutputStorage
PluginStateStore
FeedbackCollector
Router weight persistence
Runtime settings
Request history
Output file serving
Map layer serving
Project data-source management
Frontend/API response projection
Product-specific configuration


These are not kernel responsibilities.

9. Response and Artifact Direction

Current response construction is mixed:

text
Some paths build response dictionaries manually.
Some paths use ProductionResponseBuilder.
Planning converts output_nodes to layers/outputs manually.
Kernel-native QueryPipeline returns GeoResponse.


Desired direction:

text
Execution outputs
  -> ExecutionArtifact
  -> GeoArtifact
  -> GeoResponse
  -> Product/API projection


The product may still expose frontend-friendly fields such as:

text
outputs
layers
tables
documents
inspector
metadata
audit_record


But these should be projected from a stable artifact/response model rather than
rebuilt differently in every execution path.

10. SDK Fusion Gaps Found During Audit

The default SDK fusion works for simple cases but is not yet sufficient for a
full product response.

Observed gaps:

text
payload artifacts are executed but not surfaced in the final GeoResponse.
features are handled.
raster_ref is handled.
tables/reports/documents/charts/downloads/map layers are not generally handled.


Also observed:

text
SDKResultFusion imports FeatureGroup from:
  geochat_kernel.models.feature_group

but FeatureGroup is available from:
  geochat_kernel.models.geo_response
  geochat_kernel.models


This should be fixed in the SDK/kernel package when appropriate.

Until then, product-level response composition should not rely solely on the
default SDKResultFusion for complex outputs.

11. PostGIS as a First-Class Product Plugin

The planning catalog maps:

text
query_database -> query_database_postgis


Therefore, the PostGIS connector must be registered as a first-class safe
plugin when PostGIS workflows are enabled.

The solution must remain general:

text
not specific to OSM Tehran
not specific to one query
not dependent on hardcoded table names
not raw-SQL-first


The desired contract is:

text
source_type: postgis
mode: select_table
schema: public
table: table_name
columns: property columns only
geom_col: real geometry column
geom_alias: output geometry alias
where: safe predicate
limit: safe limit
output_srid: target SRID


Runtime connection details must be injected by backend runtime, not invented by
the LLM.

12. Language and Semantic Rules

The product may receive:

text
Persian queries
English queries
other natural-language queries
Persian data values
English data values
mixed-language metadata


Internal concepts should be canonical and language-neutral:

text
metro_station
shopping_center
park
road
building
parcel


Natural-language aliases should map to canonical concepts:

text
metro_station:
  fa:
    - ایستگاه مترو
    - مترو
  en:
    - metro station
    - subway station

shopping_center:
  fa:
    - مرکز خرید
    - پاساژ
  en:
    - shopping center
    - mall


Persian normalization should be applied in semantic matching:

text
ي -> ی
ك -> ک
Arabic/Persian digit normalization
ZWNJ normalization
case/spacing normalization where applicable


Code, contracts, and internal identifiers should remain English.

13. Migration Strategy

Do not rewrite the backend in one step.

Recommended migration:

Phase 0 - Documentation and audit
text
Record architecture.
Record duplicate components.
Record source-of-truth contracts.
Avoid new runtime paths.

Phase 1 - Artifact alignment
text
Convert planning outputs to GeoArtifact-compatible public artifacts.
Use kernel artifact models where possible.
Keep frontend response shape stable.

Phase 2 - QuerySpec to Kernel plan adapter
text
Map:
  DagPlan  -> QueryPlan
  DagNode  -> PlanStep

or replace DeterministicPlanner output with QueryPlan directly.

Phase 3 - Kernel-native planning execution path
text
Execute QuerySpec-derived plans through kernel PlanExecutor.
Use StepHandlerRegistry instead of direct callable resolver where possible.

Phase 4 - Product-aware fusion/projection
text
Create product fusion/composer that preserves:
  features
  tables
  reports
  documents
  payloads
  map layers
  audit metadata

Return GeoResponse first, then project to frontend response.

Phase 5 - Slim OrchestratorService
text
Move domain bridges and product services out of service.py.
Keep OrchestratorService as a facade.
Remove or deprecate legacy runtime paths after tests cover kernel-native path.

14. Rules Going Forward

Do not add a new parallel execution path unless it is explicitly temporary and
documented.

Do not create new response contracts that compete with GeoResponse and
GeoArtifact.

Do not let LLM-generated specs execute directly without deterministic contract
validation.

Do not hardcode OSM Tehran assumptions into core planning.

Do not make Persian-specific concepts internal identifiers.

Do preserve frontend compatibility during migration.

Do keep tests green after each small step.

15. Current Recommended Next Technical Step

The next code step should be small and reversible.

Preferred next step:

text
Integrate planning outputs with kernel artifact normalization.


Concretely:

text
planning_result.output_nodes
  -> output_to_artifact(...)
  -> GeoArtifact list
  -> existing frontend outputs/layers projection


This improves response consistency without replacing the whole planner/runtime
at once.

Alternative next step:

text
Create a DagPlan/DagNode -> QueryPlan/PlanStep adapter


This prepares the system for kernel PlanExecutor but is slightly more invasive.

The safer first implementation is artifact alignment.
