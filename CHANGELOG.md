# Changelog

Notable changes to Smart Spatial System. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.1] - 2026-09-19

### Fixed

- **`OP_CATALOG`'s `filter_attribute` (and its `load_postgis_layer` alias
  pair) mapped a `param_map` key to a target keyword the bound plugin
  function doesn't have, so a plan that used the exact parameter name
  `_op_param_reference()` (added in `0.4.2`) advertises to the LLM still
  failed at execution.** `filter_attribute`'s `param_map` had
  `"geometry_type": "geometry_type"`, but `filter_features()`'s real
  keyword is `geometry_types` (plural, used consistently elsewhere in
  `spatial_query_filter.py`) - `_map_params()` passed `geometry_type=`
  straight through to `filter_features(**static_params)`, raising
  `TypeError: filter_features() got an unexpected keyword argument
  'geometry_type'`. `strict_params=True` (also `0.4.2`) does not catch
  this class of bug: `geometry_type` is a genuinely valid *source* key in
  `filter_attribute`'s own `param_map`, so strictness against the catalog
  can't detect that the catalog's *target* side disagrees with the
  plugin it describes. Fixed the mapping to
  `"geometry_type": "geometry_types"`.
- Auditing every other `param_map` target against its bound capability's
  real signature (see "Added" below) turned up four more of the same
  class of drift, all dead/always-`TypeError`-on-use rather than
  reachable through today's planner defaults: `query_database` and
  `load_postgis_layer` (alias) both advertised a `metadata` param that
  `query_database_postgis()` doesn't accept; `inspect_vector` advertised
  `sample_size`/`include_geometry`/`metadata` that `inspect_vector()`
  doesn't accept; `summarize_vector` advertised `metadata` that
  `summarize_vector_layer()` doesn't accept; `display_vector` advertised
  `title`/`style`/`metadata` that `display_vector_layer()` doesn't
  accept (it only takes `layer_id`/`name`/`visible`, none of which were
  mapped either). Removed all of these from `param_map` rather than
  guessing at an intended target keyword for each - `_op_param_reference()`
  no longer advertises any of them to the LLM, and `strict_params=True`
  now correctly rejects them at planning time instead of letting them
  reach the plugin and fail there.

### Added

- `tests/test_op_catalog_param_map_signatures.py`: builds the real
  `CapabilityRegistry` from `DEFAULT_SAFE_PLUGIN_MODULES` and asserts,
  for every `OP_CATALOG` entry whose capability is registered, that
  every `param_map` *target* is either a real keyword parameter of the
  bound capability function (via `inspect.signature`) or the function
  accepts `**kwargs`. This is the systematic version of the fix above:
  it catches this class of catalog/plugin drift across all operations at
  once, at test time, rather than relying on a downstream LLM run
  happening to hit the exact wrong param.

## [0.5.0] - 2026-09-19

### Changed

- **`plugins/nearest_neighbor.py::find_nearest_neighbors` no longer
  scans every target for every source.** It computed nearest neighbors
  with a brute-force `O(source * target)` nested loop - one
  `_calculate_distance()` call per pair, no spatial index anywhere in
  the file - found while diagnosing a downstream case study's
  `s3geo.query()` calls taking ~120-130s for a 964-polygon x
  1020-point nearest-facility query. `spatial_join.py` already
  indexes target geometries in an STRtree for this exact reason
  (`spatial_index_used` in its own metadata); `find_nearest_neighbors`
  just hadn't followed that precedent. It now does the same, with a
  growing-radius STRtree search (bbox-query on a buffered source
  geometry, doubling the radius until enough confirmed candidates are
  found - see `_strtree_candidates()`'s docstring for the correctness
  argument) so `k`-nearest-with-`max_distance` still comes out exactly
  right, not just `k=1`. On a synthetic 1000x1000 benchmark
  (`tests/test_nearest_neighbor_strtree_performance.py`), this took the
  plugin's own runtime from ~33s to ~0.2s (~150x) with identical
  output; on the reported 964x1020 case-study shape, ~32s to ~0.2s.
  - The indexed path is shapely-only, matching `spatial_join.py`'s own
    engine boundary: `engine="python"` always uses the original nested
    loop unchanged, and `engine="auto"`/`"shapely"` fall back to it too
    whenever shapely is unavailable or any target geometry fails to
    parse (rather than approximating that rare case through the index).
  - Every existing output contract is unchanged: same fields
    (`_nearest_distance`, `_neighbor_rank`, `_source_index`,
    `_target_index`, `_nearest_status`, `_nearest_engine`,
    `_target_properties`, `_target_geometry`), same `k`/`max_distance`/
    `drop_unmatched`/`precision`/`distance_field`/
    `include_target_geometry` semantics, same tie-break order
    (distance, then target index). `metadata.pair_count` and
    `failed_pair_count` are also kept numerically identical to the old
    nested loop (not just the per-feature output), even though the
    indexed path doesn't literally evaluate every pair. New metadata
    field: `spatial_index_used` (bool), mirroring `spatial_join.py`'s
    field of the same name.
  - Regression tests compare the indexed and brute-force paths on the
    same datasets (points/polygons/lines, `k=1` and `k>1`, `max_distance`
    exclusion, `drop_unmatched`, missing geometries, an invalid target
    geometry forcing full fallback) and assert byte-for-byte identical
    output, plus a performance test asserting the indexed path is at
    least 5x faster at 1000x1000.

Bumped to `0.5.0` (minor, per semver): `find_nearest_neighbors`'s
observable output contract is unchanged, but `engine="auto"`'s runtime
behavior and code path materially changed internally, and a new
metadata field (`spatial_index_used`) was added.

## [0.4.2] - 2026-09-19

### Fixed

- **`s3geo.query()` let an LLM-generated plan reach a plugin with a wrong
  parameter name, failing late with a misleading raw `TypeError` instead
  of a clear planning-time error.** `DeterministicPlanner`'s
  `PlannerConfig.strict_params` defaults to `False`, and `query()` built
  its plan with a bare `DeterministicPlanner()`, so an operation
  `params` key not in `OP_CATALOG`'s `param_map` for that operation was
  silently passed straight through to the plugin function instead of
  being rejected at planning time - e.g. `filter_attribute`'s real
  params are `where`/`case_sensitive`/`sort_by`/`sort_order`/`limit`/
  `offset`/`bbox`/`bbox_mode`/`geometry_type`/`metadata`, so
  `{"attribute": "amenity"}` reached `filter_features()` unchanged and
  failed deep in execution with `filter_features() got an unexpected
  keyword argument 'attribute'` - naming the internal plugin function
  and the wrong keyword, not the op name or the right one. Every
  `PlannerConfig` this codebase's own test suite constructs already
  passes `strict_params=True` explicitly; `query()` now does too by
  default, and takes a `strict_params: bool = True` parameter so a
  caller can opt back into permissive pass-through.
- **The LLM system prompt never documented operations' real parameter
  names**, mirroring the exact gap `_op_input_roles_reference()`
  already closed for input roles (see `0.2.2`'s docstring). Added
  `_op_param_reference()` alongside it, generated directly from
  `OP_CATALOG`'s `param_map` rather than hand-written per-operation
  examples, and included in the system prompt - so the model has the
  real parameter names (e.g. `filter_attribute`'s `where`) available
  instead of guessing a plausible but wrong one (`attribute`), and a
  wrong guess is now also caught before execution by the planner
  change above. Per this changelog's own established pattern (`0.2.2`),
  both the validator and the prompt are fixed together, not just one.

## [0.4.1] - 2026-09-19

### Fixed

- **`s3geo.query()` crashed on every call, including 100%-vector queries,
  in any environment without `rasterio` installed.** It built its
  `CapabilityRegistry` with `CapabilityRegistry.from_plugin_modules()`
  (implicit `tolerant=False`), which imports all `DEFAULT_SAFE_PLUGIN_MODULES`
  up front and aborts on the first import failure -
  `plugins/ndvi_analysis.py`'s unconditional top-level `import rasterio`
  (rasterio is gated behind the optional `raster` extra, not a base
  dependency) meant any query, whatever it asked for, raised
  `ModuleNotFoundError: No module named 'rasterio'` before the LLM's plan
  ever ran. `query()` now builds its registry with `tolerant=True` by
  default - the same pattern `OrchestratorService` and `s3geo.registry()`
  already use - and takes a `tolerant: bool = True` parameter so a caller
  can opt back into strict, fail-fast plugin imports.
- **`plugins/ndvi_analysis.py`** no longer imports `rasterio` at module
  top level; the import now happens lazily inside `process_ndvi()`,
  matching its sibling raster plugins (`raster_threshold`,
  `raster_to_vector`, `band_math`, etc.), none of which need `rasterio`
  importable just to be registered.

## [0.4.0] - 2026-09-19

`s3geo` (added in `0.3.0`) exposed only `query()` and `S3GeoResult` - the
one-call path. Reaching the pipeline it wires up for finer-grained control
(inspecting a plan before executing it, resolving one plugin capability
directly, using a different LLM client) still meant importing from
`orchestrator.*`, undercutting the point of a single top-level module to
import.

### Added

- **`s3geo` now re-exports the classes `query()` wires up internally**, so
  `import s3geo` alone reaches them: `CapabilityRegistry`,
  `DeterministicPlanner`, `DagExecutor`, `RegistryCapabilityResolver`,
  `LLMQuerySpecGenerator`, `OpenAICompatibleLLMClient`, `StaticLLMClient`,
  and the errors `query()` can raise - `LLMSpecGenerationError`,
  `PlanningError`, `DagExecutionError`, `CapabilityResolutionError`. Every
  re-exported name is the exact same object defined in
  `orchestrator.planning` / `orchestrator.capability_registry`, not a
  duplicate - `tests/test_s3geo.py` asserts this with `is` identity
  checks, so this is additive only and cannot silently drift from the
  originals.
- **New `s3geo.registry(tolerant=True)`** - the same
  `CapabilityRegistry.from_plugin_modules()` call `query()` makes
  internally, exposed directly for resolving or inspecting a specific
  plugin capability without a full `query()` call:
  ```python
  import s3geo

  binding = s3geo.registry().resolve("buffer_vector_features")
  binding.callable(...)
  ```

This is still a thin wrapper only, per `s3geo`'s original design - no new
analysis logic, just a second, shorter spelling for reaching existing
classes. `orchestrator.planning` and `orchestrator.capability_registry`
remain directly usable and unchanged; nothing pinned to a specific commit
of this package (the Vienna/Tehran/Urmia case studies) imports from
`s3geo`, so none of them are affected by this addition.

Bumped to `0.4.0` (minor, per semver): new public API surface, nothing
existing changed or removed.

## [0.3.0] - 2026-09-18

A usability finding from the `smart-spatial-tehran-tod-gradient` case
study: writing a minimal "ask a question, get an answer" example required
manually wiring five separate classes (`OpenAICompatibleLLMClient` +
`LLMQuerySpecGenerator` + `DeterministicPlanner` + `CapabilityRegistry` +
`RegistryCapabilityResolver` + `DagExecutor`). That level of control is
the right shape for advanced use or debugging, but too much ceremony for
a first example or a simple script.

### Added

- **New top-level `s3geo` module** with a single public entry point,
  `s3geo.query(raw_query, *, layers, context=None, system_hints=None)`,
  collapsing the manual pipeline above into one call:
  ```python
  import s3geo

  result = s3geo.query(
      "For every station, find amenity points within 300 meters, "
      "reproject to a metric CRS first.",
      layers={"stations": stations_gdf, "amenity": amenity_gdf},
  )

  result.goal          # str - the LLM-identified analysis goal
  result.operations    # list[str] - operation names, in order
  result.output        # the final DAG output
  ```
  `layers` accepts either GeoJSON `FeatureCollection` dicts or
  `geopandas.GeoDataFrame` objects (detected via `hasattr(layer,
  "to_json")`, converted with `layer.to_json(default=str)`).
  `LLMSpecGenerationError` and `PlanningError` propagate unchanged if
  generation or planning fails; a DAG plan that builds but fails during
  execution raises `RuntimeError` with the executor's own error message
  rather than returning an invalid result silently.
  This is a thin wrapper only - no new analysis logic. Every class it
  wires up (`orchestrator.planning`/`orchestrator.capability_registry`)
  remains directly usable and unchanged; the acceptance test
  (`tests/test_s3geo.py`) proves this by injecting a fixed LLM response
  via `StaticLLMClient` and asserting `s3geo.query()`'s result matches
  the manual Registry+Planner+Executor path exactly.
  Registered as an installable top-level package in `pyproject.toml`, so
  `import s3geo` works directly after `pip install smart-spatial-system`
  - not `smart_spatial_system.s3geo` or `orchestrator.s3geo`.

Bumped to `0.3.0` (minor, per semver) rather than a patch release: this
adds a new public API surface without changing or removing any existing
one - `LLMQuerySpecGenerator`, `DeterministicPlanner`, `DagExecutor` and
the rest of `orchestrator.planning` are untouched and still directly
usable.

## [0.2.9] - 2026-09-18

A performance finding from the same `smart-spatial-tehran-tod-gradient`
case study, in `plugins/spatial_join.py` alongside `0.2.8`'s cardinality
fix: `spatial_join_features` with `engine="shapely"` checked every source
feature against every target feature - a naive O(n\*m) double loop. At
the case study's real scale (35,225 amenity points against 488 station
ring/zone polygons, ~17 million predicate checks), this took several
minutes.

### Changed

- **`spatial_join_features` (shapely engine) now uses an STRtree to
  narrow candidates before evaluating the exact predicate**, instead of
  checking every source against every target. Shapely already ships
  `shapely.strtree.STRtree` (an R-tree) - no new dependency. Target
  geometries are indexed once per call; each source feature's candidates
  are narrowed to the target geometries whose bounding box could
  possibly satisfy `intersects`/`within`/`contains` (a necessary
  precondition for all three) before the existing exact-predicate logic
  runs unchanged on the survivors. Result: turns the join from O(n\*m)
  into roughly O((n+m) \* log(m)) for the common case where every target
  geometry is valid.
  - Falls back automatically to the previous full double loop when
    `engine="python"` (unchanged, still the documented approximate
    fallback), when shapely is unavailable, or when any single target
    geometry can't be parsed by shapely - that edge case keeps the
    previous per-pair error handling (`failed_pair_count`) rather than
    silently dropping the bad geometry from consideration.
  - New `spatial_index_used` boolean in the output metadata reports
    which path actually ran.
  - Correctness is unaffected by design, not just by testing: bounding-box
    overlap is a necessary condition for all three supported predicates,
    so narrowing candidates by bbox can only ever discard true non-matches,
    never a real match. Verified directly:
    `tests/test_spatial_join_strtree_performance.py::test_strtree_result_matches_brute_force_reference`
    asserts the indexed result is bit-for-bit identical (same matched
    source/target index pairs) to a brute-force reference on the same
    100-point/10-zone dataset.
  - Measured speedup on a synthetic 3,000-point/80-zone dataset (this
    plugin's own full runtime, indexed vs. the same code with the index
    forced off - not a bare geometry-check comparison):
    `test_strtree_is_substantially_faster_than_the_naive_double_loop`
    measured roughly 25-30x faster in this sandbox; the same comparison
    at 4,000/100 and 10,000/200 during manual verification for this fix
    measured similar ratios, consistent with the case study's report of
    several minutes dropping to tens of seconds at its 35,225/488 scale.

## [0.2.8] - 2026-09-18

A third distinct finding from the `smart-spatial-tehran-tod-gradient`
case study, and unlike the first two this one isn't about which operation
gets picked at all: `0.2.5`-`0.2.7` fixed planning (`ring_buffer` chosen
correctly, `spatial_join` chosen instead of `filter_points_in_polygon`
for "which zone" queries) and the resulting plan runs cleanly, but its
*numbers* diverged from a GIS analyst's manual reference implementation
of the same query - a genuine correctness bug in a parameter default, not
a silent planning failure.

### Fixed

- **`spatial_join`'s `cardinality` default (`"first"`) silently
  undercounts overlapping ring/zone matches.** `plugins/spatial_join.py`
  keeps only the first target match per source feature unless
  `cardinality="one_to_many"` is set explicitly. That's the right default
  for a simple containment join, but wrong whenever the target layer's
  zones can overlap - most commonly a `spatial_join` whose target is a
  `ring_buffer` output: two stations close enough together have
  overlapping rings, and a point inside both was being credited to only
  one of them (whichever station happened to come first in the target
  list), not both - a systematic undercount, not a rounding difference,
  confirmed directly:
  `tests/test_spatial_join_ring_buffer_cardinality.py::test_default_cardinality_undercounts_a_point_in_two_overlapping_rings`
  shows a point equidistant from two overlapping 1200m station rings
  producing only 1 joined feature with the default, 2 with
  `cardinality="one_to_many"`.

  Same two-part shape as the `filter_points_in_polygon` fix, but this
  time leading with the hard fix given what `0.2.6` already taught this
  project about relying on prompt text alone against cheaper models:
  - `orchestrator/planning/llm_spec_generator.py`'s
    `normalize_llm_query_spec_for_planning()` now defaults a
    `spatial_join`'s `cardinality` to `"one_to_many"` automatically
    whenever its target ref traces back (through `crs_transform` or any
    other chain) to a `ring_buffer` op and the caller left `cardinality`
    unset - fixed at the system level regardless of what the LLM writes.
    An explicit `cardinality` (including an explicit `"first"`) is never
    overridden. `_OVERLAP_PRONE_ZONE_OPS` names the ops this applies to
    (currently just `ring_buffer`) so a future op with the same shape can
    be added in one place.
  - `_domain_guidance()` also documents the default's undercounting
    behavior explicitly and tells the LLM to set
    `cardinality="one_to_many"` itself whenever a query means for a
    feature matching several zones to be counted under each of them.

## [0.2.7] - 2026-09-18

`0.2.6`'s fix for the `filter_points_in_polygon` vs. `spatial_join`
mix-up turned out to be incomplete: a second real run of the exact
reported query against `gpt-4o-mini` from the
`smart-spatial-tehran-tod-gradient` case study still picked
`filter_points_in_polygon`, even though `_domain_guidance()`'s new text
covered this precise scenario ("group points by distance band/ring"
verbatim). Soft prompt guidance alone is not reliable enough here - the
same conclusion this project already reached for `score_features` field
chaining and CRS symmetry, both of which are hard generation-time checks
rather than prompt text alone.

### Fixed

- **`filter_points_in_polygon` chosen for a query that needs to know
  WHICH zone matched, again.** Added
  `_validate_filter_points_in_polygon_usage` to
  `orchestrator/planning/llm_spec_generator.py`, wired into
  `LLMQuerySpecGenerator.generate()` alongside the other `_validate_*`
  checks (after normalization, before the spec is returned). It scans
  `raw_query` for explicit zone/polygon/ring-identity language ("which
  zone", "which ring", "falls into", "group points", "label each", "for
  each point", etc.) and, when present, raises `LLMSpecGenerationError`
  if the plan uses `filter_points_in_polygon` without also using
  `spatial_join` - naming exactly why and what to use instead. A plan
  with no such signal in `raw_query` (a genuine boolean keep/drop filter)
  is left unvalidated, same conservative under-catching posture as the
  other checks in this module.

  This turns the previous silent failure (a plan that ran and produced
  output with no zone information, indistinguishable from success) into
  either a corrected plan (if the caller retries generation against the
  error) or, at minimum, a clear rejection at generation time instead of
  a wrong answer delivered with no warning.

## [0.2.6] - 2026-09-18

A sixth correctness bug, found in the `smart-spatial-tehran-tod-gradient`
case study while exercising the new `0.2.5` `ring_buffer` capability
through the LLM planning arm: a query like "for each point, tell me
which zone it falls into" was often planned with
`op="filter_points_in_polygon"` instead of `op="spatial_join"`. The plan
ran without error, but `filter_points_in_polygon` only ever returns a
boolean `__in_polygon__` membership flag - it has no way to record WHICH
polygon a point matched. The result was a silent failure: a successful,
crash-free plan whose output carried no zone/ring identity at all,
useless for exactly the "label each point by its zone" use case the
query asked for.

### Fixed

- **`filter_points_in_polygon` chosen for queries that actually need
  `spatial_join`.** Root cause: the capability's keywords/description
  ("points inside", "filter inside", "Keep only point features that fall
  inside...") overlap heavily with the surface language of "which
  zone/ring does each point fall into" queries, and nothing anywhere
  drew the distinction that only `spatial_join` retains the matched
  polygon's identity.

  Same two-part shape as earlier fixes in this series: protect at the
  capability-description layer and teach the LLM-prompt layer.
  `plugins/spatial_predicate.py`'s `filter_points_in_polygon` description
  now states explicitly that it returns a boolean flag only and does NOT
  retain which polygon matched, pointing to `spatial_join` for that case.
  `orchestrator/planning/llm_spec_generator.py`'s `_domain_guidance()`
  now spells out both shapes side by side - a boolean keep/drop filter
  uses `filter_points_in_polygon`; "which zone does this point belong
  to" uses `op="spatial_join"` with
  `params={"include_target_properties": true}`, which is what copies the
  matched zone's own properties onto each output point.

## [0.2.5] - 2026-09-18

A new capability requested for an external case study (a land-use
diversity gradient study around Tehran metro stations) that needed real
concentric ring/annulus polygons - e.g. the area between 200m and 500m
from a station - which the existing `buffer` operation cannot produce.
Chaining `buffer_vector_features` at increasing distances only ever
yields nested full-circle disks (each one containing all the smaller
ones), never the gap between two radii.

### Added

- **New plugin `plugins/ring_buffer_analysis.py`, capability
  `generate_ring_buffers`.** Given a list of positive distances (e.g.
  `[200, 500, 800, 1200]`, automatically sorted ascending so callers don't
  need to pre-sort), it builds a real shapely `buffer()` at each radius
  and takes the `difference()` between consecutive buffers to produce
  true annulus geometry - one output feature per `(input feature × ring)`,
  each carrying the original feature's properties plus `ring_index`,
  `ring_inner`, `ring_outer`, `ring_label` (e.g. `"200-500m"`) and
  `source_feature_index`. Modeled on `plugins/buffer_analysis.py`
  (shares its `_extract_features`/`_get_shapely_tools`/
  `_build_vector_metadata`/geometry-type-validation helpers) and
  config-aware via `config/plugins/ring_buffer_analysis.yaml`. There is no
  pure-python fallback - polygon difference has no simple pure-python
  approximation the way a single circle buffer does - so `engine="python"`
  (and `"auto"` when shapely isn't installed) raises `SDKDependencyError`
  with a message naming exactly what to install.
- Registered in `orchestrator/plugin_modules.py`'s
  `DEFAULT_SAFE_PLUGIN_MODULES` and reachable through planning as the new
  `ring_buffer` op in `orchestrator/planning/op_catalog.py`.
  `orchestrator/planning/llm_spec_generator.py`'s `_domain_guidance()` now
  tells the LLM to use `ring_buffer` (not chained `buffer` calls) whenever
  a request asks for multiple rings, distance bands, or a gradient
  outward from a feature.

## [0.2.4] - 2026-09-16

A fifth correctness bug from the same case study, found directly in
committed run data: after `0.2.3` fixed the CRS-mismatch bug, the case
study bumped its pin and re-ran - the plan came back completely correct
(four layers reprojected to the same CRS, three `spatial_nearest` steps
chained properly) and still 20/20 degenerate, every feature scoring
`0.0`.

### Fixed

- **A scoring factor with no `"type"` silently defaulted to `"boolean"`.**
  For a distance field, `_truthy(0.0)` is `False` and `_truthy(any nonzero
  distance)` is `True`, so every factor missing `"type"` produced a
  near-constant, meaningless score instead of an error - exactly how a
  full batch of otherwise-correct LLM-generated plans came out `0.0` for
  every feature with no error anywhere in the pipeline.

  `plugins/feature_scoring.py`'s `_score_factor` no longer defaults a
  missing `"type"` - it raises, naming the field and the full list of
  valid types (`"boolean"` remains fully supported, just no longer
  implicit). `_domain_guidance()` now states explicitly that every
  scoring factor requires a `"type"` with no default, and
  `LLMQuerySpecGenerator.generate()` validates this before returning a
  plan (`_validate_score_features_factor_types`), mirroring `0.2.2`'s and
  `0.2.3`'s checks.

- **A false positive in `0.2.3`'s CRS-symmetry check**, found while
  verifying this fix against the reported plan's exact shape: a chain of
  more than one `spatial_nearest` step (the normal shape for scoring
  against several amenities) only has its FIRST step's source ref
  directly as a `crs_transform` output - every later step's source is the
  previous step's own output. The check previously recognized only a
  direct `crs_transform` output as "reprojected", so it rejected the
  second and third steps of an otherwise completely correct chain -
  structurally identical to `accessibility_query_spec.py`'s own generated
  plan, which had never been tested against this validator directly.
  `_crs_transform_target_crs` now walks back through a chain of distance
  operations on their source side (which don't reproject, so they
  preserve the CRS) to find the originating `crs_transform`, rather than
  only checking the immediate ref.

## [0.2.3] - 2026-09-13

A fourth correctness bug from the same case study, surfaced in the very
batch that confirmed `0.2.2`'s fix working: all 20 runs called
`crs_transform` on the sites layer only, never on the amenity layers, and
every district got a near-identical, obviously-bogus distance (~6.4
million or ~340,000 "meters", depending on which CRS variant the LLM
picked) instead of a real value.

### Fixed

- **`find_nearest_neighbors` and `calculate_distances` had no CRS
  awareness at all.** Distance was computed from raw geometry coordinates
  with zero comparison between the source and target layers' actual CRS -
  a `source_crs` param existed but was used only for a one-sided
  geographic-CRS warning, and there was no `target_crs` parameter at all.
  Reprojecting only one of the two layers feeding a distance call (a
  plausible LLM mistake, and an easy human one too) silently produced a
  number - not an error - computed between a point in metres and a point
  in degrees.

  Both capabilities now accept a `target_crs` parameter alongside the
  existing `source_crs`. When both are supplied and don't match, the
  capability raises instead of returning a meaningless distance. This is
  opt-in (checked only when both hints are given) so callers that pass
  neither keep today's behaviour unchanged; `accessibility_query_spec.py`
  now passes both, since it already reprojects every layer to the same
  target CRS and gets this protection for free. `distance_to`,
  `spatial_nearest`, `nearest_neighbor` and `filter_by_distance` all
  expose the new parameter through `OP_CATALOG`.

- **The LLM prompt never said a distance operation needs BOTH its layers
  reprojected**, only the general "use a metric CRS" framing - nothing
  called out that the target/amenity layer needs its own `crs_transform`
  too, not just the source/site layer. `_domain_guidance()` now spells
  this out with a worked example (correct chained-and-matching case next
  to the exact wrong pattern this bug's report reproduced). A new
  generation-time check, `_validate_distance_op_crs_symmetry`, flags a
  distance/nearest-neighbor operation whose two vector inputs were
  reprojected to different CRSs - or where only one side was reprojected
  at all - raising `LLMSpecGenerationError` before the plan is even
  returned, mirroring `0.2.2`'s field-chaining check.

## [0.2.2] - 2026-09-13

A third correctness bug found by the same case study, evidenced across
two independent 20-run batches of LLM-generated plans (40 generations),
all making the exact same structural mistake.

### Fixed

- **The LLM prompt never taught how to combine more than one computed
  field into a single scoring step**, so every plan requiring distance to
  several different amenities (metro/schools/parks) computed each
  distance as its own operation off the *original* vector (a fan-out)
  instead of chaining each one onto the previous computation's output (an
  accumulating chain). The resulting plan validated and executed without
  error - every required input role was present - but every scoring
  factor referencing a field that landed on a sibling branch instead of
  the scored ref silently scored 0, producing an all-zero/degenerate
  score with no exception pointing at the cause. `smart_spatial_system`'s
  own non-LLM code
  (`accessibility_query_spec.py`) already implements the correct chaining
  pattern; the LLM-facing prompt simply never surfaced it.

  `_domain_guidance()` now includes a worked multi-factor example showing
  the wrong (fan-out) and right (chained) shape side by side, plus
  `join_feature_properties` as the documented alternative when two
  branches genuinely can't be chained (different row semantics - merge by
  key instead). `LLMQuerySpecGenerator.generate()` also now validates,
  before returning, that every field a `score_features` op's factors
  reference is actually reachable through that op's own input chain -
  turning the previously-silent all-zero-score failure into a specific,
  catchable `LLMSpecGenerationError` naming the missing field and where
  it was actually produced.

## [0.2.1] - 2026-09-12

Two correctness bugs found while running the LLM-backed planning path
against a non-real-estate query (Vienna accessibility scoring case
study), both in `orchestrator/planning/llm_spec_generator.py`.

### Fixed

- **`score_features` normalization no longer injects a hardcoded
  real-estate scoring spec.** Any `score_features` operation missing
  `scoring_spec`/`factors` - regardless of the query's actual domain -
  used to get a real-estate scoring spec silently substituted in
  (`output_field: "investment_score"`, factors referencing
  `inside_buildable_zone`, `flood_risk`, etc.). For any other domain this
  either crashed downstream (the factor fields don't exist on the input
  layer) or silently produced a nonsense `investment_score` column,
  indistinguishable from a real real-estate query's own valid output.
  There is no domain-neutral default to substitute, so this now raises a
  specific `LLMSpecGenerationError` instead.
- **Auto-injected `build_report` no longer hardcodes `score_field:
  "investment_score"`.** It now reuses the score/rank field names the
  plan's own `rank_features` operation actually uses, so a report for a
  non-real-estate plan references the column that plan actually produced.
- **The LLM-facing prompt now documents every operation's required input
  roles**, generated directly from `OP_CATALOG`'s `input_map` rather than
  a hand-written, incomplete list. This is why `distance_to`'s `target`
  role went undocumented and the LLM repeatedly omitted it: the prompt's
  "Important mappings" section spelled out `filter_by_distance`,
  `filter_points_in_polygon` and `enrich_risk` by hand, but nothing kept
  it in sync with the catalog as operations were added.
- `LLMQuerySpecGenerator.generate()` now validates every operation's
  inputs against `OP_CATALOG` before returning, raising a specific
  `LLMSpecGenerationError` naming the missing role - the same failure
  `DeterministicPlanner.build()` already caught as a generic
  `PlanningError`, now surfaced earlier and more specifically.

## [0.2.0] - 2026-09-12

First release that is usable outside its original Persian-language,
real-estate context.

### Added

- **Accessibility analysis workflow.** `build_accessibility_query_spec`
  builds a "score these sites by how close they are to these amenities"
  chain from a caller-supplied amenity list, using the generic operation
  catalog. Rule-based rather than LLM-backed: the chain follows
  mechanically from the inputs, so the same inputs always produce the same
  plan and the same numbers.
- **`crs_transform` operation.** Plans can now reproject. See *Fixed* -
  this closes a correctness hole, not just a missing feature.
- **`distance_field` on nearest-neighbour search**, so successive searches
  write to separate fields and distances to several amenities accumulate
  on the same features.
- **Startup warning when the API is unauthenticated.** Leaving
  `SMART_SPATIAL_API_KEY` unset still starts the server, but no longer
  silently; the warning escalates when an LLM key is also configured,
  since anyone who can reach the instance can then spend that credit.
- **Runnable examples** (`examples/accessibility_analysis.py`,
  `examples/query_via_http.py`) covering the library and HTTP paths, with
  sample data.

### Changed

- **User-facing output is English by default** (`response_language`,
  report specs and the report template). Persian remains fully supported -
  set `response_language="fa"` to restore it.
- The API reports the installed package version instead of a hardcoded
  one, so `/docs` cannot drift from the distribution.

### Fixed

- **Planned distances were computed in the input CRS's units.** With no
  `crs_transform` operation in the catalog, no plan could reproject, so
  every distance over EPSG:4326 data came back in degrees and no plan
  could express a threshold in metres. Distances are now measured in a
  projected CRS.
- **Chained nearest-neighbour searches overwrote each other**, so an
  analysis over several amenities silently scored one distance twice.
- **English questions were routed differently from their Persian
  equivalents.** "display the sites on the map" fell through to the legacy
  planner and failed asking for raster capabilities, while
  "نمایش نقاط روی نقشه" was handled correctly; the same asymmetry affected
  real-estate ranking questions. Both classifiers now match equivalent
  English and Persian phrasings.
- Accessibility reports rendered the real-estate table (`investment_score`,
  `flood_risk`, ...) as empty cells beside a correct summary. Reports now
  carry a spec built from the fields the analysis actually produced.

## [0.1.0] - 2026-09-12

First public release on PyPI. Plugin-based GeoAI backend: natural-language
question to QuerySpec to DAG plan to plugin execution, returning map
layers, tables, PDF/HTML reports and files with a full execution trace.
Ships the FastAPI service, the `smart-spatial-api` CLI, 36 registered
plugins, and the React/Leaflet workbench.

[0.2.4]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.2.4
[0.2.3]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.2.3
[0.2.2]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.2.2
[0.2.1]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.2.1
[0.2.0]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.2.0
[0.1.0]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.1.0
