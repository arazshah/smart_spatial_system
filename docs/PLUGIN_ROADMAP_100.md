# Plugin roadmap: 50 -> 100 capabilities

Unit of count: **capabilities** (`@capability` functions), because that is what the planner
(`OP_CATALOG`) and the MCP server (one tool each) work with. A plugin file may hold several.

Status 2026-10: **67 capabilities in 49 plugin modules** (50 at the start of this roadmap).

## Definition of done for a new capability

1. `plugins/<name>.py` with `@capability` (English + Persian keywords), validation, and **hard
   size/work limits** read from `config/plugins/<name>.yaml` (+ `.example.yaml`): plugins are
   reachable from remote MCP clients, so an oversized request must fail fast, not exhaust the server.
2. Registered in `orchestrator/plugin_modules.py`.
3. `OP_CATALOG` entry and, for structured params, a value shape in `op_param_shapes.py`.
4. Tests including limits and invalid input; `ruff` clean.
5. Anything that touches files, URLs or databases is classified by
   `smart_spatial_system/interfaces/mcp/policy.py` (hidden over MCP until the operator opts in).

## Done: P0 batch (17 capabilities)

| Plugin | Capabilities |
|---|---|
| `vector_overlay` | `clip_features`, `erase_features`, `union_features` |
| `grid_generator` | `generate_grid` (square / hex) |
| `field_calculator` | `calculate_field` (safe AST evaluator, never `eval`) |
| `vector_aggregation` | `aggregate_points_to_polygons`, `aggregate_table` |
| `spatial_statistics` | `calculate_kernel_density`, `cluster_features` (DBSCAN, k-means) |
| `raster_tools` | `sample_raster_at_points`, `mosaic_rasters`, `reproject_raster`, `weighted_overlay` |
| `network_routing` | `find_shortest_path`, `calculate_service_area` |
| `chart_builder` | `build_chart` (Vega-Lite + SVG) |
| `tabular_exporter` | `export_table` (CSV/TSV/JSON, formula-injection safe) |

## Next: P0 data sources (needs the connector registry first, ADR-004 phase 7)

`csv_xy_loader`, `geojson_url_loader`, `osm_overpass_fetcher`. They are sources with network or
file access; build them on a shared connector contract instead of three one-off shapes.

## P1 (20) and later

Vector/prep: `simplify_geometries`, `voronoi_thiessen`, `explode_multipart`, `deduplicate_features`,
`schema_mapper`, `persian_text_normalizer`, `classification_breaks`.
Raster: `hillshade`, `contour_generator`, `raster_change_detection`, `focal_statistics`, `raster_to_points`.
Statistics: `hotspot_getis_ord`, `spatial_autocorrelation_moran`, `idw_interpolation`, `h3_hex_aggregator`.
Network: `od_cost_matrix`, `accessibility_2sfca`.
Sources: `dem_fetcher`, `stac_search`, `postgis_writer`.
Decision/output: `ahp_weights`, `topsis_ranking`, `static_map_renderer`, `geopackage_shapefile_exporter`,
`geotiff_cog_exporter`.

P2/P3: `convex_hull_envelope`, `line_operations`, `random_sampling`, `topology_checker`,
`outlier_detection`, `viewshed`, `flow_accumulation_watershed`, `unsupervised_classification`,
`supervised_classification`, `cost_distance`, `ogc_api_arcgis_fetcher`, `fuzzy_suitability`,
`kriging`, `map_matching`, `weather_climate_fetcher`, `kml_exporter`.

## Known limits of the P0 batch

- All geometry work is planar (no geodesic distances); inputs should be in a projected CRS.
- `network_routing`: undirected edges, nodes snap to the nearest vertex (not mid-edge).
- `raster_tools`: north-up transforms only; mosaics need identical pixel size and alignment
  (use `reproject_raster` first); `reproject_raster` needs rasterio.
- Raster data travels as nested lists; a binary raster format would be faster.
