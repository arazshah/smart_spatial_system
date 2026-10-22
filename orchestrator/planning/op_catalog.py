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
            "geometry_type": "geometry_types",
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

    "connect_nearest_lines": OpDescriptor(
        op_name="connect_nearest_lines",
        capability_name="build_connection_lines",
        input_map={"vector": "features"},
        input_types={"vector": "vector"},
        param_map={
            "source_name_field": "source_name_field",
            "target_name_field": "target_name_field",
            "distance_field": "distance_field",
            "source_output_field": "source_output_field",
            "target_output_field": "target_output_field",
            "distance_output_field": "distance_output_field",
            "keep_source_properties": "keep_source_properties",
            "source_crs": "source_crs",
            "output_crs": "output_crs",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Draw a straight LineString from each source feature to its nearest "
            "target. Input MUST be the output of spatial_nearest run with "
            "include_target_geometry=true (and k=1). Output has only the line "
            "geometry plus source_name, target_name and distance_m columns "
            "(taken from _nearest_distance, so project to a metric CRS BEFORE "
            "spatial_nearest). Set source_name_field to the source name "
            "property and target_name_field to the target name property. If "
            "the data was reprojected to a metric CRS, set source_crs to that "
            "CRS and output_crs='EPSG:4326' so the lines can be drawn on a "
            "map. Use this for 'connect/draw a line between X and its nearest "
            "Y'; do NOT use raster_to_vector for lines."
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
            "first - e.g. the UTM zone of the data's centroid, which "
            "s3geo.query() computes from the input layers. Web Mercator is "
            "not one for distances: its scale factor is 1/cos(latitude)."
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
    # Vector metrics / attribute statistics
    # ---------------------------------------------------------------------
    "area_perimeter": OpDescriptor(
        op_name="area_perimeter",
        capability_name="calculate_area_perimeter",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "engine": "engine",
            "precision": "precision",
            "drop_failed": "drop_failed",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Add planar area (_area, square CRS units) and perimeter/length "
            "(_perimeter, _length) to every feature. Use for 'how large is each "
            "polygon / the largest water body / total area'; the layer must be "
            "in a projected (metric) CRS - crs_transform first if it is not. "
            "Typically after raster_to_vector(mode='components') or dissolve."
        ),
    ),

    "dissolve": OpDescriptor(
        op_name="dissolve",
        capability_name="dissolve_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "group_by": "group_by",
            "aggregate_fields": "aggregate_fields",
            "engine": "engine",
            "precision": "precision",
            "drop_failed": "drop_failed",
            "source_crs": "source_crs",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Merge features into one geometry per group_by value (or a single "
            "geometry when group_by is omitted) with optional attribute "
            "aggregation, e.g. aggregate_fields={'population': 'sum'}."
        ),
    ),

    "centroids": OpDescriptor(
        op_name="centroids",
        capability_name="extract_centroids",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "engine": "engine",
            "precision": "precision",
            "drop_failed": "drop_failed",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Replace each feature geometry with its centroid Point.",
    ),

    "validate_geometries": OpDescriptor(
        op_name="validate_geometries",
        capability_name="validate_geometries",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "engine": "engine",
            "drop_invalid": "drop_invalid",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Flag (or drop) features with invalid geometries.",
    ),

    "repair_geometries": OpDescriptor(
        op_name="repair_geometries",
        capability_name="repair_geometries",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "engine": "engine",
            "drop_invalid": "drop_invalid",
            "metadata": "metadata",
        },
        output_type="vector",
        notes="Repair invalid geometries (requires shapely); run before overlay ops.",
    ),

    "clip": OpDescriptor(
        op_name="clip",
        capability_name="clip_features",
        input_map={
            "vector": "features",
            "mask": "mask",
        },
        input_types={
            "vector": "vector",
            "mask": "vector",
        },
        param_map={
            "keep_geom_type": "keep_geom_type",
            "repair_invalid": "repair_invalid",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Clip features to the area of a mask layer (cut at the boundary, "
            "keep attributes). Use for 'only the part inside <area>' / 'clip X "
            "by Y'."
        ),
    ),

    "erase": OpDescriptor(
        op_name="erase",
        capability_name="erase_features",
        input_map={
            "vector": "features",
            "erase": "erase",
        },
        input_types={
            "vector": "vector",
            "erase": "vector",
        },
        param_map={
            "keep_geom_type": "keep_geom_type",
            "repair_invalid": "repair_invalid",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Remove the area covered by the erase layer from the features "
            "(difference). Use for 'excluding / outside / minus <area>'."
        ),
    ),

    "union": OpDescriptor(
        op_name="union",
        capability_name="union_features",
        input_map={
            "source": "source_features",
            "target": "target_features",
        },
        input_types={
            "source": "vector",
            "target": "vector",
        },
        param_map={
            "source_prefix": "source_prefix",
            "target_prefix": "target_prefix",
            "repair_invalid": "repair_invalid",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Union overlay of two polygon layers; pieces keep the attributes of "
            "the covering source and/or target polygon (overlay_origin = "
            "both/source/target)."
        ),
    ),

    "generate_grid": OpDescriptor(
        op_name="generate_grid",
        capability_name="generate_grid",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "cell_size": "cell_size",
            "bbox": "bbox",
            "grid_type": "grid_type",
            "clip_to_features": "clip_to_features",
            "max_cells": "max_cells",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Create a square or hex polygon grid over bbox or over the extent "
            "of the input layer (cell_size in layer units - use a metric CRS). "
            "Base for binning/density by cell."
        ),
    ),

    "calculate_field": OpDescriptor(
        op_name="calculate_field",
        capability_name="calculate_field",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "field": "field",
            "expression": "expression",
            "on_error": "on_error",
            "overwrite": "overwrite",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Add/overwrite an attribute from a safe expression over properties "
            "and geom_area/geom_length/geom_x/geom_y, e.g. 'population / "
            "geom_area'. Use for ratios, densities, flags, renamed/derived "
            "columns."
        ),
    ),

    "aggregate_points": OpDescriptor(
        op_name="aggregate_points",
        capability_name="aggregate_points_to_polygons",
        input_map={
            "points": "points",
            "polygons": "polygons",
        },
        input_types={
            "points": "vector",
            "polygons": "vector",
        },
        param_map={
            "value_field": "value_field",
            "stats": "stats",
            "density": "density",
            "count_field": "count_field",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Count points per polygon (and optionally sum/mean/min/max of a "
            "point attribute, density per area). Use for 'how many X in each "
            "district'."
        ),
    ),

    "aggregate_table": OpDescriptor(
        op_name="aggregate_table",
        capability_name="aggregate_table",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "aggregations": "aggregations",
            "group_by": "group_by",
            "sort_by": "sort_by",
            "descending": "descending",
            "limit": "limit",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Group by attribute(s) and summarise numeric fields "
            "(count/sum/mean/median/min/max/std...); output is a table without "
            "geometry. Use for 'total X per Y' summaries."
        ),
    ),

    "kernel_density": OpDescriptor(
        op_name="kernel_density",
        capability_name="calculate_kernel_density",
        input_map={
            "points": "points",
        },
        input_types={
            "points": "vector",
        },
        param_map={
            "cell_size": "cell_size",
            "bandwidth": "bandwidth",
            "weight_field": "weight_field",
            "kernel": "kernel",
            "bbox": "bbox",
            "crs": "crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Kernel density / heat map raster from points (optionally weighted "
            "by a numeric field). cell_size and bandwidth are in layer units - "
            "use a metric CRS."
        ),
    ),

    "cluster": OpDescriptor(
        op_name="cluster",
        capability_name="cluster_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "method": "method",
            "eps": "eps",
            "min_samples": "min_samples",
            "k": "k",
            "max_iterations": "max_iterations",
            "seed": "seed",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Spatial clustering: dbscan (needs eps, finds clusters and "
            "noise=-1) or kmeans (needs k). Adds cluster_id and cluster_size. "
            "eps is in layer units."
        ),
    ),

    "sample_raster": OpDescriptor(
        op_name="sample_raster",
        capability_name="sample_raster_at_points",
        input_map={
            "raster": "raster",
            "points": "points",
        },
        input_types={
            "raster": "raster",
            "points": "vector",
        },
        param_map={
            "band_index": "band_index",
            "field_name": "field_name",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Read the raster value under each point and store it as a property "
            "(elevation/NDVI/density at locations). Points must be in the "
            "raster CRS."
        ),
    ),

    "mosaic": OpDescriptor(
        op_name="mosaic",
        capability_name="mosaic_rasters",
        input_map={
            "rasters": "rasters",
        },
        input_types={
            "rasters": "raster",
        },
        param_map={
            "method": "method",
            "band_index": "band_index",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Merge several same-CRS, same-pixel-size rasters into one mosaic "
            "(first/last/mean/min/max on overlaps)."
        ),
    ),

    "reproject_raster": OpDescriptor(
        op_name="reproject_raster",
        capability_name="reproject_raster",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "target_crs": "target_crs",
            "resolution": "resolution",
            "resampling": "resampling",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Reproject a raster to another CRS and/or resample to a new pixel "
            "size (needs rasterio). Use before combining rasters of different "
            "grids."
        ),
    ),

    "weighted_overlay": OpDescriptor(
        op_name="weighted_overlay",
        capability_name="weighted_overlay",
        input_map={
            "rasters": "rasters",
        },
        input_types={
            "rasters": "raster",
        },
        param_map={
            "weights": "weights",
            "directions": "directions",
            "band_index": "band_index",
            "scale_max": "scale_max",
            "missing": "missing",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Multi-criteria suitability: normalise rasters to 0-1 "
            "(benefit/cost), weight and sum. weights has one number per raster; "
            "rasters must share one grid."
        ),
    ),

    "shortest_path": OpDescriptor(
        op_name="shortest_path",
        capability_name="find_shortest_path",
        input_map={
            "network": "network",
            "origin": "origin",
            "destination": "destination",
        },
        input_types={
            "network": "vector",
            "origin": "vector",
            "destination": "vector",
        },
        param_map={
            "weight_field": "weight_field",
            "snap_tolerance": "snap_tolerance",
            "node_crossings": "node_crossings",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Least-cost route over a line network (roads) between two points; "
            "cost = length x optional per-length weight_field. Network must be "
            "a loaded line layer. Output: one route LineString with cost and "
            "length."
        ),
    ),

    "service_area": OpDescriptor(
        op_name="service_area",
        capability_name="calculate_service_area",
        input_map={
            "network": "network",
            "origins": "origins",
        },
        input_types={
            "network": "vector",
            "origins": "vector",
        },
        param_map={
            "max_cost": "max_cost",
            "weight_field": "weight_field",
            "output": "output",
            "buffer_distance": "buffer_distance",
            "snap_tolerance": "snap_tolerance",
            "node_crossings": "node_crossings",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Service area / isochrone: network part reachable from each origin "
            "within max_cost (number or list of bands), as polygons, lines or "
            "both. Cost units = layer length units unless weight_field converts "
            "them (e.g. 1/speed for travel time)."
        ),
    ),

    "build_chart": OpDescriptor(
        op_name="build_chart",
        capability_name="build_chart",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "chart_type": "chart_type",
            "x": "x",
            "y": "y",
            "aggregate": "aggregate",
            "title": "title",
            "sort": "sort",
            "top_n": "top_n",
            "bins": "bins",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Chart from attributes: bar/line/pie (x = category field, y = "
            "numeric field summarised per x, or omit y to count), scatter (x,y "
            "numeric), histogram (x numeric, bins). Returns a Vega-Lite spec "
            "plus SVG."
        ),
    ),

    "export_table": OpDescriptor(
        op_name="export_table",
        capability_name="export_table",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "format": "format",
            "filename": "filename",
            "include_geometry": "include_geometry",
            "overwrite": "overwrite",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Save the attribute table as csv/tsv/json (optionally with wkt or "
            "centroid columns). Use when the user asks for a CSV/spreadsheet "
            "export."
        ),
    ),

    "load_url": OpDescriptor(
        op_name="load_url",
        capability_name="load_geojson_url",
        param_map={
            "url": "url",
            "profile": "profile",
            "timeout": "timeout",
            "max_features": "max_features",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Load a GeoJSON document from a public http(s) URL (no redirects, "
            "size limited). Use only when the user gives an explicit URL."
        ),
    ),

    "load_wfs": OpDescriptor(
        op_name="load_wfs",
        capability_name="fetch_wfs_features",
        param_map={
            "service": "service",
            "base_url": "base_url",
            "type_name": "type_name",
            "layer": "layer",
            "version": "version",
            "output_format": "output_format",
            "srs_name": "srs_name",
            "bbox": "bbox",
            "max_features": "max_features",
            "property_name": "property_name",
            "timeout": "timeout",
        },
        output_type="vector",
        notes=(
            "Fetch features from an OGC WFS service: a configured service name, "
            "or base_url plus type_name. Public hosts only."
        ),
    ),

    "geocode": OpDescriptor(
        op_name="geocode",
        capability_name="geocode_place",
        param_map={
            "query": "query",
            "provider": "provider",
            "limit": "limit",
            "language": "language",
            "country_codes": "country_codes",
            "timeout_seconds": "timeout_seconds",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Resolve a place name or address to point locations (WGS84) with "
            "the configured geocoding provider."
        ),
    ),

    "load_csv": OpDescriptor(
        op_name="load_csv",
        capability_name="load_csv_points",
        param_map={
            "path": "path",
            "x_field": "x_field",
            "y_field": "y_field",
            "wkt_field": "wkt_field",
            "crs": "crs",
            "delimiter": "delimiter",
            "encoding": "encoding",
            "max_rows": "max_rows",
            "on_invalid": "on_invalid",
            "infer_types": "infer_types",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Load a CSV/TSV file with longitude/latitude (or x/y, or WKT) "
            "columns as points; handles Persian digits and windows-1256. "
            "Reports rejected rows."
        ),
    ),

    "load_osm": OpDescriptor(
        op_name="load_osm",
        capability_name="fetch_osm_features",
        param_map={
            "bbox": "bbox",
            "tags": "tags",
            "element_types": "element_types",
            "max_features": "max_features",
            "timeout": "timeout",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Download OpenStreetMap features in a required WGS84 bbox [min_lon, "
            "min_lat, max_lon, max_lat] filtered by tags such as "
            "{'amenity': 'hospital'}; small areas only. Never pass raw Overpass QL."
        ),
    ),

    "simplify": OpDescriptor(
        op_name="simplify",
        capability_name="simplify_geometries",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "tolerance": "tolerance",
            "preserve_topology": "preserve_topology",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Reduce vertices (Douglas-Peucker); tolerance is in layer units. "
            "Use before heavy overlays or to lighten map layers."
        ),
    ),

    "explode": OpDescriptor(
        op_name="explode",
        capability_name="explode_multipart",
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
            "Split multipart geometries into one feature per part (adds "
            "source_feature_index, part_index)."
        ),
    ),

    "voronoi": OpDescriptor(
        op_name="voronoi",
        capability_name="voronoi_thiessen",
        input_map={
            "points": "points",
            "clip_to": "clip_to",
        },
        input_types={
            "points": "vector",
            "clip_to": "vector",
        },
        param_map={
            "bbox": "bbox",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Thiessen/Voronoi polygons: the area closest to each point "
            "(optionally clipped to a boundary layer or bbox)."
        ),
    ),

    "deduplicate": OpDescriptor(
        op_name="deduplicate",
        capability_name="deduplicate_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "by": "by",
            "fields": "fields",
            "keep": "keep",
            "mark_only": "mark_only",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Remove or flag duplicate features by attributes, geometry or both."
        ),
    ),

    "map_schema": OpDescriptor(
        op_name="map_schema",
        capability_name="map_schema",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "mapping": "mapping",
            "cast": "cast",
            "defaults": "defaults",
            "drop": "drop",
            "keep_only": "keep_only",
            "on_error": "on_error",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Rename/cast/drop/keep/default attribute columns in one step (cast "
            "types: int, float, str, bool)."
        ),
    ),

    "normalize_persian_text": OpDescriptor(
        op_name="normalize_persian_text",
        capability_name="normalize_persian_text",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "fields": "fields",
            "digits": "digits",
            "arabic_to_persian": "arabic_to_persian",
            "remove_diacritics": "remove_diacritics",
            "clean_spaces": "clean_spaces",
            "remove_zero_width": "remove_zero_width",
            "suffix": "suffix",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Clean Persian/Arabic text attributes (yeh/kaf, digits, diacritics, "
            "spaces); use before matching names or joining tables."
        ),
    ),

    "classify": OpDescriptor(
        op_name="classify",
        capability_name="classify_breaks",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "field": "field",
            "method": "method",
            "classes": "classes",
            "breaks": "breaks",
            "class_field": "class_field",
            "label_field": "label_field",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Class breaks for a numeric field (equal_interval, quantile, "
            "natural_breaks, std_dev, manual) and a class number and label per "
            "feature; for choropleth maps."
        ),
    ),

    "hillshade": OpDescriptor(
        op_name="hillshade",
        capability_name="calculate_hillshade",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "azimuth": "azimuth",
            "altitude": "altitude",
            "z_factor": "z_factor",
            "band_index": "band_index",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Shaded relief (0-255) from a DEM raster; azimuth/altitude set the "
            "sun position. Use for terrain visualisation."
        ),
    ),

    "contours": OpDescriptor(
        op_name="contours",
        capability_name="generate_contours",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "interval": "interval",
            "levels": "levels",
            "base": "base",
            "band_index": "band_index",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Contour lines from a surface raster at a fixed interval or "
            "explicit levels (adds level). Give interval OR levels."
        ),
    ),

    "focal_statistics": OpDescriptor(
        op_name="focal_statistics",
        capability_name="calculate_focal_statistics",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "statistic": "statistic",
            "radius": "radius",
            "shape": "shape",
            "band_index": "band_index",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Moving-window statistic (mean, median, min, max, sum, std, range) "
            "over a radius in cells; smooths a raster or measures local "
            "variation."
        ),
    ),

    "raster_change": OpDescriptor(
        op_name="raster_change",
        capability_name="detect_raster_change",
        input_map={
            "before": "before",
            "after": "after",
        },
        input_types={
            "before": "raster",
            "after": "raster",
        },
        param_map={
            "method": "method",
            "threshold": "threshold",
            "band_index": "band_index",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Change between two co-registered rasters: difference, ratio or "
            "percent_change; with threshold also a -1/0/+1 class raster."
        ),
    ),

    "raster_to_points": OpDescriptor(
        op_name="raster_to_points",
        capability_name="raster_to_points",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "band_index": "band_index",
            "min_value": "min_value",
            "max_value": "max_value",
            "step": "step",
            "value_field": "value_field",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Pixel centres as points carrying the pixel value; filter by value "
            "or thin with step. Refused above the point limit."
        ),
    ),

    "hotspots": OpDescriptor(
        op_name="hotspots",
        capability_name="calculate_hotspots",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "field": "field",
            "weights": "weights",
            "k": "k",
            "distance": "distance",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Getis-Ord Gi* hot/cold spot analysis of a numeric field (needs >= "
            "8 features); adds gi_z, gi_p, gi_bin (-3..+3) and gi_label. "
            "weights = knn (k) or distance_band (distance in layer units)."
        ),
    ),

    "spatial_autocorrelation": OpDescriptor(
        op_name="spatial_autocorrelation",
        capability_name="calculate_spatial_autocorrelation",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "field": "field",
            "weights": "weights",
            "k": "k",
            "distance": "distance",
            "row_standardize": "row_standardize",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Global Moran's I of a numeric field: is it clustered, dispersed or "
            "random? Returns moran_i, z_score, p_value and an interpretation (a "
            "table/summary, not a layer)."
        ),
    ),

    "idw": OpDescriptor(
        op_name="idw",
        capability_name="interpolate_idw",
        input_map={
            "points": "points",
        },
        input_types={
            "points": "vector",
        },
        param_map={
            "value_field": "value_field",
            "cell_size": "cell_size",
            "power": "power",
            "neighbors": "neighbors",
            "search_radius": "search_radius",
            "bbox": "bbox",
            "crs": "crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Inverse-distance-weighted interpolation of a numeric point "
            "attribute into a raster surface (cell_size in layer units)."
        ),
    ),

    "h3_aggregate": OpDescriptor(
        op_name="h3_aggregate",
        capability_name="aggregate_to_h3",
        input_map={
            "points": "points",
        },
        input_types={
            "points": "vector",
        },
        param_map={
            "resolution": "resolution",
            "value_field": "value_field",
            "stats": "stats",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Aggregate WGS84 points into H3 hexagons (resolution 0-12; 8-9 is "
            "neighbourhood scale): count plus optional sum/mean/min/max of "
            "value_field. Needs the h3 package."
        ),
    ),

    "od_matrix": OpDescriptor(
        op_name="od_matrix",
        capability_name="calculate_od_cost_matrix",
        input_map={
            "network": "network",
            "origins": "origins",
            "destinations": "destinations",
        },
        input_types={
            "network": "vector",
            "origins": "vector",
            "destinations": "vector",
        },
        param_map={
            "weight_field": "weight_field",
            "max_cost": "max_cost",
            "origin_name_field": "origin_name_field",
            "destination_name_field": "destination_name_field",
            "include_lines": "include_lines",
            "snap_tolerance": "snap_tolerance",
            "node_crossings": "node_crossings",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Network cost between every origin and destination (a table; null "
            "cost = unreachable). Needs a loaded line network layer."
        ),
    ),

    "accessibility_2sfca": OpDescriptor(
        op_name="accessibility_2sfca",
        capability_name="calculate_accessibility_2sfca",
        input_map={
            "network": "network",
            "supply": "supply",
            "demand": "demand",
        },
        input_types={
            "network": "vector",
            "supply": "vector",
            "demand": "vector",
        },
        param_map={
            "catchment_cost": "catchment_cost",
            "supply_field": "supply_field",
            "demand_field": "demand_field",
            "decay": "decay",
            "weight_field": "weight_field",
            "output": "output",
            "per": "per",
            "snap_tolerance": "snap_tolerance",
            "node_crossings": "node_crossings",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "2SFCA / enhanced 2SFCA accessibility: capacity of supply points "
            "(supply_field) reachable per `per` people from each demand "
            "location (demand_field) within catchment_cost along the network; "
            "decay = binary, gaussian or linear."
        ),
    ),

    "ahp_weights": OpDescriptor(
        op_name="ahp_weights",
        capability_name="calculate_ahp_weights",
        param_map={
            "criteria": "criteria",
            "comparisons": "comparisons",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Criteria weights from pairwise comparisons (AHP, Saaty 1-9 scale) "
            "with a consistency ratio. comparisons is an n x n reciprocal "
            "matrix or a list of {a, b, value} judgements for every pair."
        ),
    ),

    "topsis": OpDescriptor(
        op_name="topsis",
        capability_name="rank_topsis",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "criteria": "criteria",
            "score_field": "score_field",
            "rank_field": "rank_field",
            "missing": "missing",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Multi-criteria ranking (TOPSIS) of features: criteria = [{field, "
            "weight, direction benefit|cost}]; adds topsis_score (0-1) and "
            "topsis_rank (1 = best)."
        ),
    ),

    "render_map": OpDescriptor(
        op_name="render_map",
        capability_name="render_static_map",
        input_map={
            "vector": "features",
            "overlays": "overlays",
        },
        input_types={
            "vector": "vector",
            "overlays": "vector",
        },
        param_map={
            "color_field": "color_field",
            "palette": "palette",
            "classes": "classes",
            "fill": "fill",
            "stroke": "stroke",
            "stroke_width": "stroke_width",
            "point_radius": "point_radius",
            "opacity": "opacity",
            "title": "title",
            "width": "width",
            "height": "height",
            "legend": "legend",
            "background": "background",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Render layers as an SVG map image (for reports): optionally colour "
            "by color_field (numeric ramp or categories) with a legend; "
            "overlays are background layers."
        ),
    ),

    "export_gis_file": OpDescriptor(
        op_name="export_gis_file",
        capability_name="export_vector_file",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "format": "format",
            "layer_name": "layer_name",
            "crs": "crs",
            "filename": "filename",
            "overwrite": "overwrite",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Save a layer as a GeoPackage (gpkg) or zipped Shapefile (shp). Use "
            "when the user asks for a GIS file download."
        ),
    ),

    "export_geotiff": OpDescriptor(
        op_name="export_geotiff",
        capability_name="export_raster_geotiff",
        input_map={
            "raster": "raster",
        },
        input_types={
            "raster": "raster",
        },
        param_map={
            "cog": "cog",
            "dtype": "dtype",
            "compress": "compress",
            "filename": "filename",
            "overwrite": "overwrite",
            "metadata": "metadata",
        },
        output_type="json",
        notes=(
            "Save a raster as GeoTIFF or Cloud-Optimized GeoTIFF with CRS, "
            "transform and nodata. Use when the user asks for a raster file "
            "download."
        ),
    ),

    "load_dem": OpDescriptor(
        op_name="load_dem",
        capability_name="fetch_dem",
        param_map={
            "bbox": "bbox",
            "zoom": "zoom",
            "target_crs": "target_crs",
            "metadata": "metadata",
        },
        output_type="raster",
        notes=(
            "Download an elevation model (metres) for a small WGS84 bbox "
            "[min_lon, min_lat, max_lon, max_lat] from open terrain tiles. Pass "
            "target_crs (e.g. a UTM zone) before slope/hillshade analysis."
        ),
    ),

    "search_scenes": OpDescriptor(
        op_name="search_scenes",
        capability_name="search_stac",
        param_map={
            "bbox": "bbox",
            "collections": "collections",
            "datetime": "datetime",
            "max_cloud_cover": "max_cloud_cover",
            "limit": "limit",
            "include_assets": "include_assets",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Search a STAC catalogue for satellite scenes (e.g. sentinel-2-l2a) "
            "over a bbox and date range; returns footprints with date, cloud "
            "cover and asset links. It finds scenes, it does not download "
            "imagery."
        ),
    ),

    "convex_hull": OpDescriptor(
        op_name="convex_hull",
        capability_name="calculate_convex_hull",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "shape": "shape",
            "group_by": "group_by",
            "buffer": "buffer",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Convex hull, bounding box (envelope) or minimum rotated rectangle "
            "around all features or around each group_by value, optionally "
            "buffered. Use for 'the area covered by these points'."
        ),
    ),

    "check_topology": OpDescriptor(
        op_name="check_topology",
        capability_name="check_topology",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "checks": "checks",
            "min_area": "min_area",
            "sliver_ratio": "sliver_ratio",
            "overlap_tolerance": "overlap_tolerance",
            "max_issues": "max_issues",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Quality report for a layer: invalid, empty, duplicate, "
            "overlapping, sliver and tiny geometries, one point per issue. Use "
            "before overlays or when the user asks whether the data is clean."
        ),
    ),

    "sample": OpDescriptor(
        op_name="sample",
        capability_name="sample_features",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "size": "size",
            "fraction": "fraction",
            "method": "method",
            "stratify_by": "stratify_by",
            "per_stratum": "per_stratum",
            "seed": "seed",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Reproducible random, stratified or systematic sample of features "
            "(size or fraction, optional seed)."
        ),
    ),

    "outliers": OpDescriptor(
        op_name="outliers",
        capability_name="detect_outliers",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "field": "field",
            "method": "method",
            "threshold": "threshold",
            "group_by": "group_by",
            "outlier_field": "outlier_field",
            "score_field": "score_field",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Flag unusual values of a numeric field (iqr, zscore or "
            "modified_zscore), optionally per group; adds is_outlier and "
            "outlier_score."
        ),
    ),

    "attribute_statistics": OpDescriptor(
        op_name="attribute_statistics",
        capability_name="calculate_attribute_statistics",
        input_map={
            "vector": "features",
        },
        input_types={
            "vector": "vector",
        },
        param_map={
            "fields": "fields",
            "group_by": "group_by",
            "include_nulls": "include_nulls",
            "numeric_only": "numeric_only",
            "max_top_values": "max_top_values",
            "precision": "precision",
            "metadata": "metadata",
        },
        output_type="vector",
        notes=(
            "Descriptive statistics (count, min, max, sum, mean, median, stdev, "
            "top values) of feature properties, optionally per group_by field. "
            "Use to total/average a numeric field such as _area or pixel_count "
            "across features, or per class/category."
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
        param_map={},
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
        param_map={},
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
        param_map={},
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
