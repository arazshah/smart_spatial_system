# Plugin roadmap: 50 -> 100 capabilities

Unit of count: **capabilities** (`@capability` functions), because that is what the planner
(`OP_CATALOG`) and the MCP server (one tool each) work with. A plugin file may hold several.

Status 2026-10: **100 capabilities in 63 plugin modules** (50 at the start of this roadmap). **Goal reached.**

## Definition of done for a new capability

1. `plugins/<name>.py` with `@capability` (English + Persian keywords), validation, and **hard
   size/work limits** read from `config/plugins/<name>.yaml` (+ `.example.yaml`): plugins are
   reachable from remote MCP clients, so an oversized request must fail fast, not exhaust the server.
2. Registered in `orchestrator/plugin_modules.py`.
3. `OP_CATALOG` entry and, for structured params, a value shape in `op_param_shapes.py`.
4. Tests including limits and invalid input; `ruff` clean.
5. Anything that touches files, URLs or databases is classified by
   `smart_spatial_system/interfaces/mcp/policy.py` (hidden over MCP until the operator opts in).

## Done: P0 batch (20 capabilities)

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
| `geojson_url_loader` | `load_geojson_url` (connector; shared network policy) |
| `csv_xy_loader` | `load_csv_points` (connector; utf-8 / windows-1256, Persian digits, rejected-row report) |
| `osm_overpass_fetcher` | `fetch_osm_features` (connector; structured tag filters only, bounded bbox) |

## P0 data sources: done on the connector contract

`csv_xy_loader`, `geojson_url_loader` and `osm_overpass_fetcher` are built on the connector
contract (`docs/CONNECTOR_REGISTRY_DESIGN.md`). All P0 items are complete.

## Done: P1 batch (26 capabilities)

| Plugin | Capabilities |
|---|---|
| `vector_geometry_tools` | `simplify_geometries`, `explode_multipart`, `voronoi_thiessen` |
| `attribute_prep` | `deduplicate_features`, `map_schema`, `normalize_persian_text`, `classify_breaks` (Jenks, quantile, equal, std dev, manual) |
| `raster_surface` | `calculate_hillshade`, `generate_contours`, `calculate_focal_statistics`, `detect_raster_change`, `raster_to_points` |
| `spatial_pattern_analysis` | `calculate_hotspots` (Gi*), `calculate_spatial_autocorrelation` (Moran's I), `interpolate_idw`, `aggregate_to_h3` (extra `h3`) |
| `network_accessibility` | `calculate_od_cost_matrix`, `calculate_accessibility_2sfca` (binary / enhanced decay) |
| `decision_analysis` | `calculate_ahp_weights`, `rank_topsis` |
| `static_map_renderer` | `render_static_map` (SVG, attribute colouring, legend) |
| `geo_exporter` | `export_vector_file` (GeoPackage / zipped Shapefile), `export_raster_geotiff` (GeoTIFF / COG) |
| `dem_fetcher` | `fetch_dem` (connector; open Terrarium tiles, built-in PNG decoder) |
| `stac_search` | `search_stac` (connector; finds scenes, does not download imagery) |
| `postgis_writer` | `write_postgis_layer` (off by default; profile only; never drops tables; **not run against a live PostGIS in this repo's tests**) |

`write_postgis_layer` has no planner op on purpose (a natural-language prompt must not be able to
trigger database writes); it is available to library callers and, when explicitly enabled, MCP.

## Done: the last four (to reach 100)

| Plugin | Capabilities |
|---|---|
| `vector_geometry_tools` | `calculate_convex_hull` (hull / envelope / minimum rotated rectangle, per group), `check_topology` (invalid, empty, duplicate, overlap, sliver, small; one located point per issue) |
| `attribute_prep` | `sample_features` (random / stratified / systematic, seeded), `detect_outliers` (IQR, z-score, modified z-score, per group) |

## Further candidates (beyond the goal)

`line_operations`, `viewshed`, `flow_accumulation_watershed`, `unsupervised_classification`,
`supervised_classification`, `cost_distance`, `ogc_api_arcgis_fetcher`, `fuzzy_suitability`, `kriging`,
`map_matching`, `weather_climate_fetcher`, `kml_exporter`.

## Known limits (all batches)

- All geometry work is planar (no geodesic distances); inputs should be in a projected CRS.
- `network_routing` / `network_accessibility`: undirected edges; points snap to the nearest network
  node (not mid-edge) and the snap distance is reported (`snap_tolerance` refuses far snaps).
- `raster_tools`, `raster_surface`: north-up transforms only; mosaics need identical pixel size and
  alignment (use `reproject_raster` first); `reproject_raster` and `export_raster_geotiff` need rasterio.
- Raster data travels as nested lists; a binary raster format would be faster.
- Gi* p-values are not corrected for multiple testing; Moran's I uses the normality variance.
- `fetch_dem` returns Web Mercator unless `target_crs` is given: reproject before slope/hillshade.
- Terrain, STAC and Overpass data carry their own licences and attribution (in the metadata).
- `fetch_dem`, `search_stac`, `fetch_osm_features` and `write_postgis_layer` were exercised with fakes
  or recorded structures, not against the live services in this repository's tests.
- `aggregate_to_h3` needs the optional `h3` package (`smart_spatial_system[h3]`).
