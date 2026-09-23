"""
orchestrator.planning.op_param_shapes

Worked value-shape examples for every structured OP_CATALOG param.

_op_param_reference() (llm_spec_generator.py) teaches the LLM every
operation's accepted params *keys*, generated from OP_CATALOG.param_map.
A key name is enough for a plain scalar (``limit: 10``,
``sort_order: "desc"``), but not for a param whose value is itself
structured: the model was told ``filter_attribute`` accepts ``where`` and
nothing about what a ``where`` value looks like, so it guessed a SQL-ish
string (``"amenity = hospital"``) and the plan failed at execution with
``where must be a dict/object or None.``. ``enrich_feature_properties.rules``
failed the same way (``rules[0].target is required.``).

A value shape can't be introspected from a signature the way a key name
can (``dict[str, Any]`` says nothing about ``{"field": ..., "op": ...}``),
so this is a maintained table. It is keyed by the real capability keyword
(``(capability_name, target_kwarg)``, the right-hand side of a param_map
entry), not by logical op name, so ops that share a capability
(``filter_attribute``/``sort_limit``, ``ndvi``/``calculate_ndvi``) share
one entry. Two tests keep it from drifting:

- tests/test_op_param_shapes.py asserts every param_map target whose
  capability annotation is not a plain ``str``/``int``/``float``/``bool``
  (optionally ``| None``) has an entry here, and that no entry here is
  stale. A new structured param added to the catalog without an entry
  fails that test instead of silently reopening this gap.
- The same test feeds the ``where``/``rules``/``factors``/... examples
  below to the real plugins, so an example that the plugin would reject
  fails the suite rather than teaching the model a wrong shape.

Examples are real JSON values (rendered with ``json.dumps``), not
hand-formatted strings, so they are guaranteed to be valid JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from orchestrator.planning.op_catalog import get_op, list_supported_ops

# Wildcard capability key: applies to this target kwarg on every capability.
ANY_CAPABILITY = "*"


@dataclass(frozen=True)
class ParamShape:
    description: str
    examples: tuple[Any, ...] = field(default_factory=tuple)


_NODATA = ParamShape(
    "a single number marking missing pixels; usually omit (read from the raster's own metadata)",
    (-9999,),
)
_OUTPUT_NODATA = ParamShape(
    "a single number written to output pixels that are nodata/invalid; usually omit",
    (-9999,),
)
_DIVISION_BY_ZERO = ParamShape(
    "a single number used where the denominator is zero; usually omit", (0,),
)
_RASTER_STATS = ParamShape(
    "one statistic name or a list of them "
    "(count, valid_count, nodata_count, min, max, sum, mean, median, "
    "sample_stdev, population_stdev, unique_count, majority, minority); omit for the defaults",
    (["min", "max", "mean"], "mean"),
)
_TRANSFORM = ParamShape(
    "affine transform [a, b, c, d, e, f] (pixel_width, 0, origin_x, 0, -pixel_height, origin_y); "
    "usually omit (read from the raster's metadata)",
    ([10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0],),
)
_BBOX = ParamShape(
    "[minx, miny, maxx, maxy] in the layer's CRS, or the same as an object",
    ([51.2, 35.6, 51.5, 35.8], {"minx": 51.2, "miny": 35.6, "maxx": 51.5, "maxy": 35.8}),
)

FILTER_WHERE_EXAMPLES: tuple[Any, ...] = (
    {"field": "amenity", "op": "eq", "value": "hospital"},
    {"amenity": "hospital"},
    {"beds": {"gte": 100}},
    {"and": [
        {"field": "amenity", "op": "in", "value": ["hospital", "clinic"]},
        {"not": {"field": "emergency", "op": "eq", "value": "no"}},
    ]},
)

ENRICH_RULES_EXAMPLES: tuple[Any, ...] = (
    [
        {"target": "distance_to_metro", "source": "distance", "transform": "float", "default": 0},
        {"target": "inside_buildable_zone", "source": "__in_polygon__", "transform": "bool"},
        {"target": "flood_risk", "value": "low"},
    ],
)

SCORING_FACTORS_EXAMPLES: tuple[Any, ...] = (
    [
        {"name": "near_metro", "field": "distance_to_metro", "type": "inverse_distance",
         "max_distance": 800, "weight": 1},
        {"name": "low_flood_risk", "field": "flood_risk", "type": "risk_level", "weight": 0.5},
    ],
)

RISK_RULES_EXAMPLES: tuple[Any, ...] = (
    [{"target": "flood_risk", "source": "flood_zone",
      "mapping": {"A": "low", "B": "medium", "C": "high"}, "default": "low"}],
)

RECLASSIFY_RULES_EXAMPLES: tuple[Any, ...] = (
    [
        {"min": 0.0, "max": 0.2, "value": 1, "label": "low"},
        {"min": 0.2, "max": 1.0, "value": 2, "label": "high"},
        {"equals": 5, "value": 10},
        {"values": [1, 2, 3], "value": 100},
    ],
)


PARAM_SHAPES: dict[tuple[str, str], ParamShape] = {
    # --- filter_features (filter_attribute, sort_limit) ----------------
    ("filter_features", "where"): ParamShape(
        "a JSON OBJECT, never a string (unlike query_database's SQL-style where). "
        "Canonical form {\"field\": <property>, \"op\": <operator>, \"value\": <value>}; "
        "operators: eq, ne, gt, gte, lt, lte, in, not_in (value is a list), between "
        "(value is [low, high]), contains, startswith, endswith, regex, exists, is_null "
        "(value true/false). Shortcuts: {<property>: <value>} means eq, "
        "{<property>: {<operator>: <value>}}. Combine with {\"and\": [...]}, "
        "{\"or\": [...]}, {\"not\": <condition>}",
        FILTER_WHERE_EXAMPLES,
    ),
    ("filter_features", "bbox"): _BBOX,
    ("filter_features", "geometry_types"): ParamShape(
        "one GeoJSON geometry type or a list of them",
        ("Point", ["Polygon", "MultiPolygon"]),
    ),

    ("enrich_feature_properties", "rules"): ParamShape(
        "REQUIRED non-empty list of rule objects; every rule needs \"target\" (the new "
        "property name) plus ONE of \"source\" (an existing property, or __in_polygon__), "
        "\"first_existing\" (list of properties, first present wins) or \"value\" (a literal); "
        "optional \"transform\" (float, int, str, bool, lower, upper) and \"default\"",
        ENRICH_RULES_EXAMPLES,
    ),

    # --- sources ------------------------------------------------------
    ("query_database_postgis", "columns"): ParamShape(
        "list of column names to select; omit for all columns",
        (["name", "amenity"],),
    ),

    # --- vector analysis ----------------------------------------------
    ("generate_ring_buffers", "distances"): ParamShape(
        "list of ring radii (numbers, in `units`), ascending",
        ([200, 500, 800],),
    ),
    ("score_features", "factors"): ParamShape(
        "list of factor objects; every factor needs \"field\", an explicit \"type\" "
        "(boolean, boolean_bonus, inverse_distance, risk_level, inverse_level, threshold, "
        "condition, direct, numeric, inverse_numeric) and \"weight\"; inverse_distance "
        "also needs \"max_distance\"",
        SCORING_FACTORS_EXAMPLES,
    ),
    ("score_features", "scoring_spec"): ParamShape(
        "object with \"factors\" (same shape as the factors param) plus optional "
        "\"output_field\", \"scale\", \"normalize_weights\"; pass either this or factors",
        ({"factors": SCORING_FACTORS_EXAMPLES[0], "output_field": "score", "scale": 100},),
    ),
    ("join_feature_properties", "fields"): ParamShape(
        "list of right-layer property names to copy, or an object mapping "
        "right_property -> new_property_name; omit to copy all",
        (["population", "district"], {"pop_total": "population"}),
    ),
    ("enrich_risk", "default_risks"): ParamShape(
        "object mapping risk property -> level (very_low, low, medium, high, very_high, critical)",
        ({"flood_risk": "low", "earthquake_risk": "medium", "fire_risk": "low"},),
    ),
    ("enrich_risk", "overrides"): ParamShape(
        "object mapping a feature's id_field value -> {risk property: level}",
        ({"p1": {"flood_risk": "high"}},),
    ),
    ("enrich_risk", "rules"): ParamShape(
        "list of rule objects {\"target\": <risk property>, \"source\": <existing property>, "
        "\"mapping\": {<source value>: <level>}, \"default\": <level>}",
        RISK_RULES_EXAMPLES,
    ),
    ("enrich_risk", "risk_spec"): ParamShape(
        "object bundling \"default_risks\", \"overrides\" and \"rules\" (same shapes as those "
        "params); pass either this or the individual params",
        ({"default_risks": {"flood_risk": "low"}, "rules": RISK_RULES_EXAMPLES[0]},),
    ),

    # --- raster -------------------------------------------------------
    ("calculate_raster_statistics", "stats"): _RASTER_STATS,
    ("calculate_raster_statistics", "bands"): ParamShape(
        "1-based band index or a list of them; omit for all bands",
        (1, [1, 2]),
    ),
    ("calculate_raster_statistics", "nodata"): _NODATA,
    ("calculate_ndvi", "nodata"): _NODATA,
    ("calculate_ndvi", "division_by_zero_value"): _DIVISION_BY_ZERO,
    ("calculate_spectral_index", "band_map"): ParamShape(
        "object mapping band name (blue, green, red, nir, swir1, swir2) -> 1-based band index",
        ({"red": 3, "nir": 4},),
    ),
    ("calculate_spectral_index", "params"): ParamShape(
        "optional index constants object (savi_l, evi_g, evi_c1, evi_c2, evi_l)",
        ({"savi_l": 0.5},),
    ),
    ("calculate_spectral_index", "nodata"): _NODATA,
    ("calculate_spectral_index", "output_nodata"): _OUTPUT_NODATA,
    ("calculate_spectral_index", "division_by_zero_value"): _DIVISION_BY_ZERO,
    ("calculate_band_math", "nodata"): _NODATA,
    ("threshold_raster", "true_value"): ParamShape(
        "a single number written where the condition holds", (1,),
    ),
    ("threshold_raster", "false_value"): ParamShape(
        "a single number written where the condition fails", (0,),
    ),
    ("threshold_raster", "nodata"): _NODATA,
    ("threshold_raster", "output_nodata"): _OUTPUT_NODATA,
    ("raster_to_vector", "include_values"): ParamShape(
        "a pixel value or list of pixel values to vectorize; omit for all valid values",
        ([1], 1),
    ),
    ("raster_to_vector", "exclude_values"): ParamShape(
        "a pixel value or list of pixel values to skip", ([0],),
    ),
    ("raster_to_vector", "nodata"): _NODATA,
    ("reclassify_raster", "rules"): ParamShape(
        "ordered list of rule objects, first match wins: range {\"min\", \"max\", \"value\"}, "
        "exact {\"equals\", \"value\"}, or set {\"values\": [...], \"value\"}; optional \"label\"",
        RECLASSIFY_RULES_EXAMPLES,
    ),
    ("reclassify_raster", "nodata"): _NODATA,
    ("reclassify_raster", "output_nodata"): _OUTPUT_NODATA,
    ("reclassify_raster", "unmatched_value"): ParamShape(
        "a single number for pixels no rule matched (with keep_unmatched=false)", (0,),
    ),
    ("clip_mask_raster", "bbox"): _BBOX,
    ("clip_mask_raster", "mask_geometry"): ParamShape(
        "a literal GeoJSON geometry, Feature or FeatureCollection object (not a layer ref)",
        ({"type": "Polygon", "coordinates": [[[51.2, 35.6], [51.5, 35.6], [51.5, 35.8], [51.2, 35.6]]]},),
    ),
    ("clip_mask_raster", "transform"): _TRANSFORM,
    ("clip_mask_raster", "nodata"): _NODATA,
    ("calculate_slope_aspect", "nodata"): _NODATA,
    ("calculate_slope_aspect", "output_nodata"): _OUTPUT_NODATA,
    ("calculate_slope_aspect", "flat_aspect_value"): ParamShape(
        "a single number written as the aspect of flat cells", (-1,),
    ),
    ("calculate_zonal_statistics", "stats"): _RASTER_STATS,
    ("calculate_zonal_statistics", "transform"): _TRANSFORM,
    ("calculate_zonal_statistics", "nodata"): _NODATA,

    # --- reporting ----------------------------------------------------
    ("build_report", "report_spec"): ParamShape(
        "object with optional \"title\", \"language\" (en|fa), \"format\" (pdf|html|json), "
        "\"map_layers\": [{\"source\": <ref>, \"label\": ...}], "
        "\"tables\": [{\"source\": <ref>, \"columns\": [{\"field\": ..., \"label\": ...}], "
        "\"sort_by\": ..., \"max_rows\": ...}]; omit for the default report",
        ({"title": "Candidate ranking", "language": "en",
          "map_layers": [{"source": "ranked_sites", "label": "Sites"}],
          "tables": [{"source": "ranked_sites",
                      "columns": [{"field": "name", "label": "Name"},
                                  {"field": "score", "label": "Score"}]}]},),
    ),
    ("build_report", "node_outputs"): ParamShape(
        "filled in at execution time; always omit",
    ),

    # Last: rendered once as "<any op>.metadata".
    (ANY_CAPABILITY, "metadata"): ParamShape(
        "optional free-form object merged into the output metadata; normally omit",
        ({"note": "..."},),
    ),
}


def get_param_shape(capability_name: str, target: str) -> ParamShape | None:
    return PARAM_SHAPES.get((capability_name, target)) or PARAM_SHAPES.get((ANY_CAPABILITY, target))


def _render_shape(shape: ParamShape) -> str:
    text = shape.description
    if shape.examples:
        rendered = " | ".join(json.dumps(example, ensure_ascii=False) for example in shape.examples)
        text += f". e.g. {rendered}"
    return text


def op_param_shape_reference() -> str:
    """
    Prompt section listing a worked value shape for every structured param
    of every supported op, one line per distinct shape. Ops sharing a
    capability (and so a shape) are grouped on one line, e.g.
    ``- filter_attribute.where: ...``. Wildcard entries (``metadata``) are
    rendered once as ``<any op>.<param>`` instead of per op. Lines follow
    PARAM_SHAPES order, so the most commonly mis-shaped params (where,
    rules) come first.
    """
    names: dict[int, list[str]] = {id(shape): [] for shape in PARAM_SHAPES.values()}
    for (capability, target), shape in PARAM_SHAPES.items():
        if capability == ANY_CAPABILITY:
            names[id(shape)].append(f"<any op>.{target}")

    for name in list_supported_ops():
        descriptor = get_op(name)
        for logical, target in descriptor.param_map.items():
            shape = PARAM_SHAPES.get((descriptor.capability_name, target))
            if shape is not None:
                names[id(shape)].append(f"{name}.{logical}")

    lines: list[str] = []
    seen: set[int] = set()
    for shape in PARAM_SHAPES.values():
        key = id(shape)
        if key in seen or not names[key]:
            continue
        seen.add(key)
        lines.append(f"- {' / '.join(names[key])}: {_render_shape(shape)}")
    return "\n".join(lines)
