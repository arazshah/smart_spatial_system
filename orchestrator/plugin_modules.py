"""
orchestrator.plugin_modules

Centralized default plugin module list for the Smart Spatial System.

This module intentionally contains only lightweight constants so it can be
safely imported by low-level modules such as capability_registry and by the
service facade without creating import cycles.
"""

from __future__ import annotations


DEFAULT_SAFE_PLUGIN_MODULES = [
    "plugins.spectral_indices",
    "plugins.raster_threshold",
    "plugins.raster_to_vector",
    "plugins.core_vector",
    "plugins.spatial_predicate",
    "plugins.feature_scoring",
    "plugins.feature_enrichment",
    "plugins.risk_enrichment",
    "plugins.report_builder",
    "plugins.pdf_renderer",
    "plugins.ndvi_calculator",
    "plugins.ndvi_analysis",
    "plugins.raster_statistics",
    "plugins.raster_reclassify",
    "plugins.band_math",
    "plugins.raster_clip_mask",
    "plugins.slope_aspect",
    "plugins.zonal_statistics",
    "plugins.buffer_analysis",
    "plugins.centroid_extractor",
    "plugins.geometry_validator",
    "plugins.spatial_query_filter",
    "plugins.spatial_intersection",
    "plugins.spatial_join",
    "plugins.nearest_neighbor",
    "plugins.distance_calculator",
    "plugins.area_perimeter_calc",
    "plugins.dissolve_aggregator",
    "plugins.attribute_statistics",
    "plugins.crs_transformer",
    "plugins.data_writer_exporter",
    "plugins.local_vector_loader",
    "plugins.postgis_connector",
]
