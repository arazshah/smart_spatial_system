# Changelog

Notable changes to Smart Spatial System. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.2.0
[0.1.0]: https://github.com/arazshah/smart_spatial_system/releases/tag/v0.1.0
