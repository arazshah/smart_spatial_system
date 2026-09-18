"""
orchestrator.planning.op_catalog

Logical operation catalog.

This is the bridge between:
    QuerySpec logical operations
and
    real plugin capabilities.

Design goal
-----------
The LLM/planner should reason in semantic operations such as:
    - spatial_nearest
    - top_n
    - spatial_join
    - buffer
    - summarize_vector
    - display_vector

while the executor still calls deterministic registered capabilities such as:
    - find_nearest_neighbors
    - rank_features
    - spatial_join_features
    - buffer_vector_features
    - summarize_vector_layer
    - display_vector_layer

This catalog is intentionally explicit. It does not hard-code a single user
query. It exposes reusable spatial/data operations in a stable format.

Source-plugin reachability (REFACTOR_PLAN.md Phase 6, step 3 -
docs/PHASE6_SOURCE_ABSTRACTION_PLAN.md; verified 2026-09, re-check before
relying on it since new ops/routes can change this over time)
-----------------------------------------------------------------
Of the five source plugins' seven capabilities
(postgis_connector.py: fetch_postgis_layer, fetch_postgis_sql_layer,
query_database_postgis; local_vector_loader.py: load_local_vector;
local_raster_loader.py: load_local_raster; wms_wfs_fetcher.py:
fetch_wfs_features, fetch_wms_map; geocoding_resolver.py: geocode_place,
reverse_geocode_point), only two are mapped here and therefore reachable
through QuerySpec/DAG planning at all: load_local_vector (op
load_vector) and query_database_postgis (ops query_database /
load_postgis_layer). The other five:

    - fetch_postgis_layer and fetch_wfs_features: reachable, but only via
      direct API routes (api/routers/data_source_connectors.py), which
      resolve them through the capability registry directly, bypassing
      this catalog/the planner entirely.
    - fetch_wms_map: no production caller anywhere. register_wms_source
      (data_source_service.py) only stores WMS connection metadata and
      never actually calls it - built ahead of its caller and never
      finished being wired in.
    - fetch_postgis_sql_layer, geocode_place, reverse_geocode_point: no
      caller anywhere in api/ or orchestrator/ outside their own tests.

Not itself a bug (unreachable code isn't unsafe), but real information
for whoever designs the ADR-004 Phase 7 connector-registry - see that
plan document's "Current state" section for the full source-plugin
inventory this note summarizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OpDescriptor:
    op_name: str
    capability_name: str
    input_map: dict[str, str] = field(default_factory=dict)
    input_types: dict[str, str] = field(default_factory=dict)
    param_map: dict[str, str] = field(default_factory=dict)
    output_type: str = "json"
    notes: str = ""


_NEAREST_PARAM_MAP = {
    "k": "k",
    "max_distance_m": "max_distance",
    "max_distance": "max_distance",
    "drop_unmatched": "drop_unmatched",
    "engine": "engine",
    "precision": "precision",
    "include_target_geometry": "include_target_geometry",
    "source_crs": "source_crs",
    # Passing both source_crs and target_crs lets find_nearest_neighbors
    # catch a source/target CRS mismatch and raise instead of silently
    # computing a meaningless planar distance - see its docstring.
    "target_crs": "target_crs",
    # Lets a plan chain several nearest-neighbour steps over the same
    # features, each writing its own distance field - see the capability's
    # docstring.
    "distance_field": "distance_field",
    "metadata": "metadata",
}

_RANK_PARAM_MAP = {
    "score_field": "score_field",
    "rank_field": "rank_field",
    "descending": "descending",
    "limit": "limit",
    "metadata": "metadata",
}

_SPATIAL_JOIN_PARAM_MAP = {
    "predicate": "predicate",
    "join_type": "join_type",
    "cardinality": "cardinality",
    "engine": "engine",
    "drop_failed": "drop_failed",
    "include_target_properties": "include_target_properties",
    "flatten_target_properties": "flatten_target_properties",
    "target_property_prefix": "target_property_prefix",
    "source_crs": "source_crs",
    "metadata": "metadata",
}

_BUFFER_PARAM_MAP = {
    "distance": "distance",
    "units": "units",
    "quad_segs": "quad_segs",
    "engine": "engine",
    "dissolve": "dissolve",
    "cap_style": "cap_style",
    "join_style": "join_style",
    "mitre_limit": "mitre_limit",
    "metadata": "metadata",
}

_RING_BUFFER_PARAM_MAP = {
    "distances": "distances",
    "units": "units",
    "quad_segs": "quad_segs",
    "engine": "engine",
    "cap_style": "cap_style",
    "join_style": "join_style",
    "mitre_limit": "mitre_limit",
    "metadata": "metadata",
}


OP_CATALOG: dict[str, OpDescriptor] = {
    # ---------------------------------------------------------------------
    # Data loading / database access
    # ---------------------------------------------------------------------
    "load_vector": OpDescriptor(
        op_name="load_vector",
        capability_name="load_local_vector",
        param_map={
            "path": "path",
            "strict_extensions": "strict_extensions",
            "layer": "layer",
            "max_features": "max_features",
        },
        output_type="vector",
        notes="Load a local vector file. For PostGIS, prefer query_database.",
    ),

    "query_database": OpDescriptor(
        op_name="query_database",
        capability_name="query_database_postgis",
        param_map={
            "source_type": "source_type",
            "mode": "mode",
            "schema": "schema",
            "table": "table",
            "columns": "columns",
            "geom_col": "geom_col",
            "geom_alias": "geom_alias",
            "where": "where",
            "limit": "limit",
            "output_srid": "output_srid",
            "profile": "profile",
            "dsn": "dsn",
            "host": "host",
            "port": "port",
            "database": "database",
            "user": "user",
            "password": "password",
            "connect_timeout": "connect_timeout",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Canonical query_database/PostGIS V1. LLM must not generate raw SQL. "
            "Use source_type=postgis, mode=select_table, schema, table, columns, "
            "geom_col, geom_alias, where, limit, output_srid. "
            "For semantic user concepts, prefer schema-aware resolved layer "
            "parameters from the semantic PostGIS resolver."
        ),
    ),

    # Alias with a more explicit name for planners that reason in PostGIS terms.
    "load_postgis_layer": OpDescriptor(
        op_name="load_postgis_layer",
        capability_name="query_database_postgis",
        param_map={
            "source_type": "source_type",
            "mode": "mode",
            "schema": "schema",
            "table": "table",
            "columns": "columns",
            "geom_col": "geom_col",
            "geom_alias": "geom_alias",
            "where": "where",
            "limit": "limit",
            "output_srid": "output_srid",
            "profile": "profile",
            "dsn": "dsn",
            "host": "host",
            "port": "port",
            "database": "database",
            "user": "user",
            "password": "password",
            "connect_timeout": "connect_timeout",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Semantic alias for query_database when loading a PostGIS table/layer. "
            "Use only schema-aware table, column and predicate information."
        ),
    ),

    # ---------------------------------------------------------------------
    # Attribute filtering / sorting / limiting
    # ---------------------------------------------------------------------
    "filter_attribute": OpDescriptor(
        op_name="filter_attribute",
        capability_name="filter_features",
        input_map={"vector": "features"},
        input_types={"vector": "vector"},
        param_map={
            "where": "where",
            "case_sensitive": "case_sensitive",
            "sort_by": "sort_by",
            "sort_order": "sort_order",
            "limit": "limit",
            "offset": "offset",
            "bbox": "bbox",
            "bbox_mode": "bbox_mode",
            "geometry_type": "geometry_type",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Filter, sort, offset, and limit vector features by attributes or bbox. "
            "Use for simple attribute predicates and non-spatial top/limit operations."
        ),
    ),

    "sort_limit": OpDescriptor(
        op_name="sort_limit",
        capability_name="filter_features",
        input_map={"vector": "features"},
        input_types={"vector": "vector"},
        param_map={
            "sort_by": "sort_by",
            "sort_order": "sort_order",
            "limit": "limit",
            "offset": "offset",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Semantic alias for sorting and limiting features when no ranking field "
            "needs to be generated."
        ),
    ),

    # ---------------------------------------------------------------------
    # Nearest / distance / proximity analysis
    # ---------------------------------------------------------------------
    "spatial_nearest": OpDescriptor(
        op_name="spatial_nearest",
        capability_name="find_nearest_neighbors",
        input_map={
            "source": "source_features",
            "target": "target_features",
        },
        input_types={
            "source": "vector",
            "target": "vector",
        },
        param_map=_NEAREST_PARAM_MAP,
        output_type="vector",
        notes=(
            "Use when the user asks for nearest/closest target features for each "
            "source feature, e.g. 'nearest X to each Y', 'closest hospital to every "
            "school', 'نزدیک‌ترین X به هر Y'. Set k=1 for one closest target per "
            "source. Output preserves source properties and adds: "
            "_nearest_distance, _neighbor_rank, _source_index, _target_index, "
            "_nearest_status, _nearest_engine, _target_properties. "
            "If include_target_geometry=true, _target_geometry is also included. "
            "For top N smallest distances, follow with top_n/rank_features using "
            "score_field='_nearest_distance', descending=false, limit=N. "
            "Use a metric/projected CRS when distances must be meters."
        ),
    ),

    "nearest_neighbor": OpDescriptor(
        op_name="nearest_neighbor",
        capability_name="find_nearest_neighbors",
        input_map={
            "source": "source_features",
            "target": "target_features",
        },
        input_types={
            "source": "vector",
            "target": "vector",
        },
        param_map=_NEAREST_PARAM_MAP,
        output_type="vector",
        notes=(
            "Alias of spatial_nearest. Prefer spatial_nearest in new plans. "
            "Useful for k-nearest-neighbor / KNN / proximity requests."
        ),
    ),

    "filter_by_distance": OpDescriptor(
        op_name="filter_by_distance",
        capability_name="find_nearest_neighbors",
        input_map={
            "vector": "source_features",
            "reference": "target_features",
        },
        input_types={
            "vector": "vector",
            "reference": "vector",
        },
        param_map=_NEAREST_PARAM_MAP,
        output_type="vector",
        notes=(
            "Use for 'features nearer than X meters to reference features'. "
            "Set max_distance_m=X, k=1 or desired k, and drop_unmatched=true "
            "to remove features with no neighbor within the threshold. "
            "This uses the nearest-neighbor engine and adds _nearest_distance."
        ),
    ),

    "distance_to": OpDescriptor(
        op_name="distance_to",
        capability_name="calculate_distances",
        input_map={
            "vector": "source_features",
            "target": "target_features",
        },
        input_types={
            "vector": "vector",
            "target": "vector",
        },
        param_map={
            "mode": "mode",
            "engine": "engine",
            "precision": "precision",
            "drop_failed": "drop_failed",
            "source_crs": "source_crs",
            # Passing both source_crs and target_crs lets calculate_distances
            # catch a source/target CRS mismatch and raise instead of
            # silently computing a meaningless planar distance.
            "target_crs": "target_crs",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Calculate planar nearest or pairwise distances between source and "
            "target features. For 'nearest target per source', spatial_nearest is "
            "usually the better operation. Use a metric/projected CRS for meters."
        ),
    ),

    # ---------------------------------------------------------------------
    # Spatial predicates and overlays
    # ---------------------------------------------------------------------
    "filter_points_in_polygon": OpDescriptor(
        op_name="filter_points_in_polygon",
        capability_name="filter_points_in_polygon",
        input_map={
            "vector": "points",
            "polygon": "polygons",
        },
        input_types={
            "vector": "vector",
            "polygon": "vector",
        },
        param_map={
            "predicate": "predicate",
            "drop_outside": "drop_outside",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="True point-in-polygon predicate, not bbox approximation.",
    ),

    "intersect": OpDescriptor(
        op_name="intersect",
        capability_name="intersect_features",
        input_map={
            "source": "source_features",
            "target": "target_features",
        },
        input_types={
            "source": "vector",
            "target": "vector",
        },
        param_map={
            "mode": "mode",
            "engine": "engine",
            "precision": "precision",
            "drop_non_intersecting": "drop_non_intersecting",
            "drop_failed": "drop_failed",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Intersect source and target geometries. Use when the user asks for "
            "features that overlap/intersect/are inside another layer."
        ),
    ),

    "spatial_join": OpDescriptor(
        op_name="spatial_join",
        capability_name="spatial_join_features",
        input_map={
            "source": "source_features",
            "target": "target_features",
        },
        input_types={
            "source": "vector",
            "target": "vector",
        },
        param_map=_SPATIAL_JOIN_PARAM_MAP,
        output_type="vector",
        notes=(
            "Join target attributes into source features using spatial predicates "
            "such as intersects, within, contains, touches, etc. Use when the user "
            "asks to enrich one layer with properties from another based on "
            "location, not key equality."
        ),
    ),

    "crs_transform": OpDescriptor(
        op_name="crs_transform",
        capability_name="transform_vector_crs",
        input_map={"vector": "features"},
        input_types={"vector": "vector"},
        param_map={
            "source_crs": "source_crs",
            "target_crs": "target_crs",
            "engine": "engine",
            "precision": "precision",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Reproject features between CRS. Required before any distance or "
            "area operation whose result must be in metres: nearest_neighbor, "
            "distance_to and buffer all compute planar values in whatever "
            "units the input CRS uses, and do not reproject themselves - so "
            "WGS84 (EPSG:4326) input yields distances in DEGREES, not metres. "
            "Reproject to a projected CRS appropriate for the study area "
            "first (e.g. EPSG:3857 for web-mercator work, or a local metric "
            "CRS such as EPSG:31256 for Vienna / EPSG:32633 for UTM 33N)."
        ),
    ),

    "buffer": OpDescriptor(
        op_name="buffer",
        capability_name="buffer_vector_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map=_BUFFER_PARAM_MAP,
        output_type="vector",
        notes=(
            "Create buffer polygons around input features. Use for requests like "
            "'within X meters', 'حریم X متری', or proximity zones. Use metric CRS "
            "when distance is in meters."
        ),
    ),

    "ring_buffer": OpDescriptor(
        op_name="ring_buffer",
        capability_name="generate_ring_buffers",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map=_RING_BUFFER_PARAM_MAP,
        output_type="vector",
        notes=(
            "Create concentric ring (annulus) polygons around input features at "
            "multiple distances, e.g. params.distances=[200, 500, 800, 1200]. "
            "Returns the area BETWEEN consecutive radii (a true ring/annulus), "
            "not cumulative full-circle disks. Use this instead of chaining "
            "buffer multiple times whenever the user asks for distance bands, "
            "zonal rings, or a gradient outward from a point/line/polygon - "
            "chaining buffer only ever produces nested full disks, never the "
            "gap between two radii. Use metric CRS when distances are in "
            "meters."
        ),
    ),

    # ---------------------------------------------------------------------
    # Raster operations
    # ---------------------------------------------------------------------
    "raster_stats": OpDescriptor(
        op_name="raster_stats",
        capability_name="calculate_raster_statistics",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "stats": "stats",
            "bands": "bands",
            "nodata": "nodata",
            "histogram_bins": "histogram_bins",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="json",
    ),

    "ndvi": OpDescriptor(
        op_name="ndvi",
        capability_name="calculate_ndvi",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "red_band": "red_band",
            "nir_band": "nir_band",
            "nodata": "nodata",
            "division_by_zero_value": "division_by_zero_value",
            "clip_output": "clip_output",
            "output_min": "output_min",
            "output_max": "output_max",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Calculate NDVI from a multi-band raster. Prefer this operation for "
            "standard NDVI workflows. red_band and nir_band may be provided as "
            "band indexes when they are known."
        ),
    ),

    "calculate_ndvi": OpDescriptor(
        op_name="calculate_ndvi",
        capability_name="calculate_ndvi",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "red_band": "red_band",
            "nir_band": "nir_band",
            "nodata": "nodata",
            "division_by_zero_value": "division_by_zero_value",
            "clip_output": "clip_output",
            "output_min": "output_min",
            "output_max": "output_max",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes="Explicit alias for ndvi.",
    ),

    "ndvi_from_bands": OpDescriptor(
        op_name="ndvi_from_bands",
        capability_name="ndvi_processor",
        input_map={
            "red_band": "red_band",
            "nir_band": "nir_band",
        },
        input_types={
            "red_band": "raster",
            "nir_band": "raster",
        },
        param_map={},
        output_type="raster",
        notes=(
            "Enterprise NDVI processor that expects separate RED and NIR raster "
            "inputs. Use only when the planner has distinct red_band and nir_band "
            "artifacts."
        ),
    ),

    "spectral_index": OpDescriptor(
        op_name="spectral_index",
        capability_name="calculate_spectral_index",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "index_name": "index_name",
            "band_map": "band_map",
            "params": "params",
            "nodata": "nodata",
            "output_nodata": "output_nodata",
            "division_by_zero_value": "division_by_zero_value",
            "clip_output": "clip_output",
            "output_min": "output_min",
            "output_max": "output_max",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Calculate remote-sensing spectral indices such as NDVI, NDWI, NDBI, "
            "NDMI, MNDWI, GNDVI, SAVI, EVI, and NBR. Use index_name to select the "
            "index."
        ),
    ),

    "band_math": OpDescriptor(
        op_name="band_math",
        capability_name="calculate_band_math",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "expression": "expression",
            "preset": "preset",
            "output_dtype": "output_dtype",
            "nodata": "nodata",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Apply safe mathematical expressions to raster bands. Use for custom "
            "band formulas when no named spectral index operation is sufficient."
        ),
    ),

    "raster_threshold": OpDescriptor(
        op_name="raster_threshold",
        capability_name="threshold_raster",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "band_index": "band_index",
            "operator": "operator",
            "threshold": "threshold",
            "min_value": "min_value",
            "max_value": "max_value",
            "inclusive_min": "inclusive_min",
            "inclusive_max": "inclusive_max",
            "true_value": "true_value",
            "false_value": "false_value",
            "nodata": "nodata",
            "output_nodata": "output_nodata",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Create raster masks/classes from threshold conditions. Use after "
            "NDVI/spectral-index/band-math operations to extract vegetation, "
            "water, slope, elevation or similar masks."
        ),
    ),

    "raster_to_vector": OpDescriptor(
        op_name="raster_to_vector",
        capability_name="raster_to_vector",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "band_index": "band_index",
            "include_values": "include_values",
            "exclude_values": "exclude_values",
            "mode": "mode",
            "connectivity": "connectivity",
            "nodata": "nodata",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
            "include_pixel_properties": "include_pixel_properties",
            "include_component_cells": "include_component_cells",
            "max_features": "max_features",
        },
        output_type="vector",
        notes=(
            "Polygonize selected raster pixels/classes into vector features. "
            "Commonly used after raster_threshold or raster_reclassify."
        ),
    ),

    "raster_reclassify": OpDescriptor(
        op_name="raster_reclassify",
        capability_name="reclassify_raster",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "rules": "rules",
            "band_index": "band_index",
            "nodata": "nodata",
            "output_nodata": "output_nodata",
            "keep_unmatched": "keep_unmatched",
            "unmatched_value": "unmatched_value",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Reclassify raster values using exact, list, or range rules. Use for "
            "terrain/NDVI/class maps."
        ),
    ),

    "raster_clip": OpDescriptor(
        op_name="raster_clip",
        capability_name="clip_mask_raster",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "bbox": "bbox",
            "mask_geometry": "mask_geometry",
            "transform": "transform",
            "crop": "crop",
            "apply_mask": "apply_mask",
            "all_touched": "all_touched",
            "nodata": "nodata",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Clip or mask raster data by bbox or GeoJSON geometry. Use before "
            "analysis when the user restricts processing to an area of interest."
        ),
    ),

    "slope_aspect": OpDescriptor(
        op_name="slope_aspect",
        capability_name="calculate_slope_aspect",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "band_index": "band_index",
            "output": "output",
            "slope_unit": "slope_unit",
            "nodata": "nodata",
            "output_nodata": "output_nodata",
            "x_resolution": "x_resolution",
            "y_resolution": "y_resolution",
            "flat_aspect_value": "flat_aspect_value",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Calculate slope and aspect from DEM raster. Use for terrain analysis "
            "and elevation-derived slope/aspect requests."
        ),
    ),

    "zonal_statistics": OpDescriptor(
        op_name="zonal_statistics",
        capability_name="calculate_zonal_statistics",
        input_map={
            "raster": "raster",
            "zones": "zones",
        },
        input_types={
            "raster": "raster",
            "zones": "vector",
        },
        param_map={
            "stats": "stats",
            "band_index": "band_index",
            "zone_id_field": "zone_id_field",
            "transform": "transform",
            "nodata": "nodata",
            "all_touched": "all_touched",
            "include_zone_geometry": "include_zone_geometry",
            "stat_prefix": "stat_prefix",
            "engine": "engine",
            "precision": "precision",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Calculate raster statistics inside vector zone geometries. Use for "
            "requests like mean NDVI per district, DEM statistics per polygon, "
            "or raster summaries by administrative areas."
        ),
    ),

    # ---------------------------------------------------------------------
    # Scoring / ranking / top-N
    # ---------------------------------------------------------------------
    "score_features": OpDescriptor(
        op_name="score_features",
        capability_name="score_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "scoring_spec": "scoring_spec",
            "factors": "factors",
            "output_field": "output_field",
            "scale": "scale",
            "normalize_weights": "normalize_weights",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Weighted multi-criteria feature scoring.",
    ),

    "rank_features": OpDescriptor(
        op_name="rank_features",
        capability_name="rank_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector"},
        param_map=_RANK_PARAM_MAP,
        output_type="vector",
        notes=(
            "Rank features by a numeric score field. Use descending=true for high "
            "score first. Use descending=false for smallest distance/cost first."
        ),
    ),

    "top_n": OpDescriptor(
        op_name="top_n",
        capability_name="rank_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map=_RANK_PARAM_MAP,
        output_type="vector",
        notes=(
            "Semantic alias for returning top N features by a numeric field. "
            "For nearest-neighbor outputs, use score_field='_nearest_distance', "
            "descending=false, limit=N to get the N smallest distances."
        ),
    ),

    # ---------------------------------------------------------------------
    # Property enrichment / joins
    # ---------------------------------------------------------------------
    "enrich_feature_properties": OpDescriptor(
        op_name="enrich_feature_properties",
        capability_name="enrich_feature_properties",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "rules": "rules",
            "skip_missing": "skip_missing",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Derive/copy/rename feature properties before scoring.",
    ),

    "join_feature_properties": OpDescriptor(
        op_name="join_feature_properties",
        capability_name="join_feature_properties",
        input_map={
            "left": "left_features",
            "right": "right_features",
        },
        input_types={
            "left": "vector",
            "right": "vector",
        },
        param_map={
            "left_key": "left_key",
            "right_key": "right_key",
            "fields": "fields",
            "prefix": "prefix",
            "overwrite": "overwrite",
            "unmatched": "unmatched",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Join feature properties by matching attribute keys. "
            "For location-based joins, use spatial_join instead."
        ),
    ),

    "enrich_risk": OpDescriptor(
        op_name="enrich_risk",
        capability_name="enrich_risk",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "risk_spec": "risk_spec",
            "default_risks": "default_risks",
            "overrides": "overrides",
            "rules": "rules",
            "id_field": "id_field",
            "overwrite": "overwrite",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Add flood, earthquake and fire risk fields to features.",
    ),

    # ---------------------------------------------------------------------
    # Vector inspection / display / export / summary
    # ---------------------------------------------------------------------
    "inspect_vector": OpDescriptor(
        op_name="inspect_vector",
        capability_name="inspect_vector",
        input_map={
            "vector": "vector",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "sample_size": "sample_size",
            "include_geometry": "include_geometry",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Inspect vector schema/sample. Do not use as the only operation when "
            "the user requested analysis such as nearest, distance, intersection, "
            "ranking, or scoring."
        ),
    ),

    "summarize_vector": OpDescriptor(
        op_name="summarize_vector",
        capability_name="summarize_vector_layer",
        input_map={
            "vector": "vector",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Create a user-facing summary/table-like JSON for a vector layer. "
            "Use after analysis when the user asks for a table or summary."
        ),
    ),

    "display_vector": OpDescriptor(
        op_name="display_vector",
        capability_name="display_vector_layer",
        input_map={
            "vector": "vector",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "title": "title",
            "style": "style",
            "metadata": "metadata",
        },
        output_type="map",
        notes=(
            "Display vector features on map. Do not use as the only operation "
            "when the user requested an analytical result."
        ),
    ),

    "export_geojson": OpDescriptor(
        op_name="export_geojson",
        capability_name="export_vector_geojson",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "output_path": "output_path",
            "output_dir": "output_dir",
            "filename": "filename",
            "metadata": "metadata",
            "overwrite": "overwrite",
            "pretty": "pretty",
        },
        output_type="vector",
        notes="Export vector features to a GeoJSON/JSON file.",
    ),

    # ---------------------------------------------------------------------
    # Reports
    # ---------------------------------------------------------------------
    "build_report": OpDescriptor(
        op_name="build_report",
        capability_name="build_report",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "report_spec": "report_spec",
            "node_outputs": "node_outputs",
            "score_field": "score_field",
            "rank_field": "rank_field",
            "name_field": "name_field",
            "metadata": "metadata",
        },
        output_type="report",
        notes="Build structured report from ranked features and ReportSpec.",
    ),

    # ---------------------------------------------------------------------
    # Real estate ranking (REFACTOR_PLAN.md Phase 5)
    # ---------------------------------------------------------------------
    "real_estate_spatial_enrich": OpDescriptor(
        op_name="real_estate_spatial_enrich",
        capability_name="enrich_real_estate_spatial_context",
        input_map={
            "vector": "features",
            "metro": "metro",
            "malls": "malls",
            "main_roads": "main_roads",
            "allowed_zones": "allowed_zones",
        },
        input_types={
            "vector": "vector",
            "metro": "vector",
            "malls": "vector",
            "main_roads": "vector",
            "allowed_zones": "vector",
        },
        param_map={
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Fill missing real-estate ranking metrics (distance_to_metro_m, "
            "distance_to_mall_m, distance_to_main_road_m, in_allowed_zone) "
            "from optional metro/malls/main_roads/allowed_zones layers. "
            "All four layer roles are required node inputs (the planner has "
            "no notion of optional input_map roles today): pass an empty "
            "FeatureCollection ({'type': 'FeatureCollection', 'features': []}) "
            "entity for any layer that has no real data -- the underlying "
            "capability treats an empty/missing layer as a no-op for that "
            "layer, so this has no effect on features that already have "
            "explicit distance/zone values."
        ),
    ),

    "real_estate_score": OpDescriptor(
        op_name="real_estate_score",
        capability_name="score_real_estate_properties",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Score real-estate property features and evaluate eligibility "
            "using the MVP real-estate scoring formula. Annotates every "
            "feature with eligible, eligibility_reasons, score and "
            "score_details; does not split eligible/rejected features. "
            "Follow with filter_attribute (where eligible=true) to drop "
            "ineligible properties, then rank_features/top_n "
            "(score_field='score', descending=true) to rank the survivors "
            "-- both already-registered generic ops, so the ranking step "
            "itself does not need a dedicated real-estate op."
        ),
    ),

    "render_pdf": OpDescriptor(
        op_name="render_pdf",
        capability_name="render_pdf",
        input_map={
            "report": "report",
        },
        input_types={
            "report": "report",
        },
        param_map={
            "template_name": "template_name",
            "output_path": "output_path",
            "save_to_disk": "save_to_disk",
            "metadata": "metadata",
        },
        output_type="pdf",
        notes="Render ReportOut to PDF using Jinja2 + WeasyPrint.",
    ),
}


PENDING_OPS: set[str] = {
    "enrich_weather",
    "filter_points_on_raster",
}


def get_op(op_name: str) -> OpDescriptor:
    if op_name not in OP_CATALOG:
        pending = op_name in PENDING_OPS
        raise KeyError(f"Unknown operation: {op_name}. pending={pending}")
    return OP_CATALOG[op_name]


def is_supported(op_name: str) -> bool:
    return op_name in OP_CATALOG


def is_pending(op_name: str) -> bool:
    return op_name in PENDING_OPS


def list_supported_ops() -> list[str]:
    return sorted(OP_CATALOG)


def list_pending_ops() -> list[str]:
    return sorted(PENDING_OPS)
