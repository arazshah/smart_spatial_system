# Changelog

Notable changes to Smart Spatial System. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
