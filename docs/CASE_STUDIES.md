# Case studies

This is a catalog of concrete, runnable studies this system is a fit for — for
anyone deciding whether to build a thesis, paper, or applied project on top of
it, and to make clear what "using this system for research" concretely looks
like. If you start one, open an issue with the
[case study template](../.github/ISSUE_TEMPLATE/case_study.md) — finished or
in-progress studies get listed here.

## The reference pattern: reproducibility of LLM-generated vs. rule-based plans

The first case study built on this system —
[`smart-spatial-vienna-accessibility`](https://github.com/arazshah/smart-spatial-vienna-accessibility),
Vienna district accessibility to metro/schools/parks — is the template worth
copying regardless of domain, because it uses a capability specific to this
system: **the same analysis can be expressed two ways**, a deterministic
rule-based `QuerySpec` builder (see
`smart_spatial_system/application/services/query_execution/accessibility_query_spec.py`
for the pattern) and an LLM-backed one
(`orchestrator.planning.llm_spec_generator.LLMQuerySpecGenerator`), from the
same operation catalog. Running the LLM arm N times and measuring:

- **Plan Agreement Rate** — do repeated runs produce the same operation
  sequence?
- **Parametric variance** — when the structure agrees, do the chosen
  weights/thresholds?
- **Rank Stability** — does the final ranking change, via pairwise Spearman
  correlation?

...turns "is this LLM planner reliable?" from an anecdote into a number. See
that repository's `paper/comparison_metric.md` for the full, citable
definition. Any of the studies below can use this exact framing as its
methodology section.

That project's `smart_spatial_system/CLAUDE.md`-driven bug reports (five
correctness bugs found and fixed upstream this way, `0.2.1`–`0.2.4`) are also
the best evidence this system is under active, responsive maintenance —
worth pointing a reviewer or advisor at directly.

## In progress

- [`smart-spatial-urmia-real-estate`](https://github.com/arazshah/smart-spatial-urmia-real-estate) —
  real-estate suitability ranking in Urmia (transit/mall/main-road
  proximity, flood/earthquake/fire risk, allowed-construction zoning),
  reusing case study 2 below but against real OpenStreetMap vector data
  for the roads/transit/shopping layers instead of synthetic ones. Same
  rule-based-vs-LLM reproducibility methodology as the Vienna study
  above, applied to a workflow this system already ships end to end
  (`real_estate_spatial_enrich` → `real_estate_score` → `rank_features` →
  `build_report`, see `orchestrator/planning/op_catalog.py`) rather than
  one built from scratch for the study. **Scaffolded, not yet run** — see
  that repository's `paper/PLAN.md` for exactly what's verified so far.
- [`smart-spatial-tehran-tod-gradient`](https://github.com/arazshah/smart-spatial-tehran-tod-gradient) —
  land-use diversity around Tehran metro stations vs. distance from the
  station (a zonal gradient, not a ranking). Manual/deterministic arm
  done; LLM arm blocked on an upstream plugin — see that repository's
  `paper/PLAN.md`.

## Suggested studies

Each entry: the question, why this system's existing plugins are a real fit
(not a stretch), a public data source to start from, and rough effort.

### 1. Multi-amenity accessibility, any city (generalizes Vienna)

**Question:** rank administrative units (districts, wards, hexgrid cells) by
access to a chosen amenity set. **Fit:** `accessibility_query_spec.py`'s
pattern is domain- and city-neutral by design — swap the amenity list and the
OSM extract. **Data:** OpenStreetMap via Overpass, any city with reasonable
POI coverage. **Effort:** low if replicating Vienna's method on a new city;
medium if adding a new amenity-weighting scheme.

### 2. Flood/earthquake risk-aware site ranking

**Question:** rank candidate sites (housing, facilities) by suitability,
combining proximity (amenities, roads) with hazard exposure. **Fit:** exactly
what `real_estate_scoring`/`risk_enrichment`/`real_estate_spatial_enrichment`
plugins and the `enrich_risk` operation exist for — this is closer to a
worked example than a new capability. **Data:** national hazard maps (FEMA
flood maps for the US, national geological survey earthquake zonation
elsewhere) plus OSM for the amenity layers. **Effort:** medium — the main
work is sourcing and reprojecting a hazard raster/vector layer per region.
In progress: [`smart-spatial-urmia-real-estate`](https://github.com/arazshah/smart-spatial-urmia-real-estate)
(see "In progress" above).

### 3. Facility siting / service-coverage gaps

**Question:** where should a new clinic, school, or fire station go to
minimize the population outside a walking/driving-time threshold?
**Fit:** `nearest_neighbor`/`spatial_join`/`zonal_statistics` cover the
core computation; the gap (population NOT within threshold) is a
`spatial_predicate` + `dissolve_aggregator` combination away. **Data:**
WorldPop or national census grids for population, OSM for existing
facilities. **Effort:** medium-high — needs a new small workflow wiring
these together, a good first "new capability" contribution.

### 4. Vegetation/land-cover change over time

**Question:** how has vegetation health or extent changed in a region across
two or more dates (urban heat islands, deforestation, agricultural stress).
**Fit:** `ndvi_calculator`/`spectral_indices`/`raster_reclassify`/
`zonal_statistics`/`raster_to_vector` is a complete pipeline for this
already — no new plugin needed, only a workflow that runs it twice and
diffs. **Data:** Sentinel-2 (Copernicus Open Access Hub) or Landsat
(USGS EarthExplorer), both free. **Effort:** medium — the raster download/
preprocessing is the real work, the analysis chain already exists.

### 5. Zoning / land-use compliance screening

**Question:** which parcels violate a zoning rule (inside a protected
buffer, outside a permitted-use polygon, exceeding a density threshold)?
**Fit:** `geometry_validator` + `spatial_predicate` + `buffer_analysis` +
`attribute_statistics`, no new plugin. **Data:** varies heavily by
jurisdiction — a municipal open-data portal with parcel + zoning layers is
the prerequisite; this is the hardest part of proposing this study, not the
analysis. **Effort:** low once data is sourced.

### 6. Cross-domain replication of the reproducibility methodology

**Question:** does the LLM-vs-rule-based reliability gap found in the Vienna
study (bugs in chaining, CRS handling, scoring-factor typing) hold for a
structurally different analysis — say, raster-based (case study 4) instead
of vector distance-based? **Fit:** this is a methodology paper, not a new
capability — it reuses `comparison_metric.md`'s definitions verbatim.
**Effort:** low to set up, but needs real LLM API budget for N repeated
runs, same as the original.

## What isn't a good fit yet

Be upfront about this rather than let someone discover it mid-study:
non-Latin-script geocoding beyond what `geocoding_resolver` already handles,
3D/BIM data, and anything needing sub-daily temporal resolution are outside
what the plugin set covers today. Propose the plugin in an issue first if
your study needs one of these — see
[CONTRIBUTING.md](../CONTRIBUTING.md) and
[docs/PLUGIN_FACTORY_AGENT_GUIDE.md](PLUGIN_FACTORY_AGENT_GUIDE.md).
