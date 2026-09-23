"""
Tests for orchestrator/planning/op_param_shapes.py and the generation-time
structured-param validator in llm_spec_generator.py.

Regression for: LLM plans using filter_attribute with a SQL-style string
``where`` (``"amenity = hospital"``) failed at execution with
``where must be a dict/object or None.``, and enrich_feature_properties
plans failed with ``rules[0].target is required.``. The prompt taught the
param *keys* (_op_param_reference) but never what a structured *value*
looks like.

Mirrors test_op_catalog_param_map_signatures.py: the capability signatures
are the source of truth for which params are structured, so a new
non-scalar param_map target without a shape entry fails here instead of
silently reopening the gap.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.llm_spec_generator import (
    _FILTER_WHERE_OPERATORS,
    LLMQuerySpecGenerator,
    LLMSpecGenerationError,
    StaticLLMClient,
    _where_shape_error,
    build_llm_messages,
)
from orchestrator.planning.op_catalog import OP_CATALOG
from orchestrator.planning.op_param_shapes import (
    ANY_CAPABILITY,
    ENRICH_RULES_EXAMPLES,
    FILTER_WHERE_EXAMPLES,
    PARAM_SHAPES,
    RECLASSIFY_RULES_EXAMPLES,
    RISK_RULES_EXAMPLES,
    SCORING_FACTORS_EXAMPLES,
    get_param_shape,
    op_param_shape_reference,
)
from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES

_SCALAR_NAMES = {"str", "int", "float", "bool", "None"}
_SCALAR_TYPES = {str, int, float, bool, type(None)}


@pytest.fixture(scope="module")
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_plugin_modules(DEFAULT_SAFE_PLUGIN_MODULES)


def _is_plain_scalar(annotation) -> bool:
    """
    True for str/int/float/bool, optionally unioned with each other or None.
    Plugins use ``from __future__ import annotations`` so annotations are
    usually strings; an unannotated param counts as structured (unknown).
    """
    if annotation is inspect.Parameter.empty:
        return False
    if isinstance(annotation, str):
        parts = [part.strip() for part in annotation.split("|")]
        return all(part in _SCALAR_NAMES for part in parts)
    if annotation in _SCALAR_TYPES:
        return True
    args = getattr(annotation, "__args__", None)
    return bool(args) and all(arg in _SCALAR_TYPES for arg in args)


def _catalog_targets(registry):
    for op_name, descriptor in OP_CATALOG.items():
        try:
            binding = registry.resolve(descriptor.capability_name)
        except ValueError:
            continue
        signature = inspect.signature(binding.callable)
        for source_key, target_key in descriptor.param_map.items():
            parameter = signature.parameters.get(target_key)
            annotation = parameter.annotation if parameter else inspect.Parameter.empty
            yield op_name, descriptor.capability_name, source_key, target_key, annotation


def test_is_plain_scalar_classifier():
    assert _is_plain_scalar("str")
    assert _is_plain_scalar("int | None")
    assert _is_plain_scalar("bool | str | None")
    assert not _is_plain_scalar("dict[str, Any] | None")
    assert not _is_plain_scalar("str | list[str] | None")
    assert not _is_plain_scalar("Any")
    assert not _is_plain_scalar(inspect.Parameter.empty)


def test_every_structured_param_has_a_shape_entry(registry):
    missing = [
        f"op '{op}' param '{source}' -> {capability}({target}: {annotation})"
        for op, capability, source, target, annotation in _catalog_targets(registry)
        if not _is_plain_scalar(annotation) and get_param_shape(capability, target) is None
    ]
    assert not missing, (
        "Structured OP_CATALOG params with no worked value shape in "
        "orchestrator/planning/op_param_shapes.py::PARAM_SHAPES - the LLM is "
        "told the key but not what a valid value looks like:\n" + "\n".join(missing)
    )


def test_no_stale_shape_entries(registry):
    live = {(capability, target) for _, capability, _, target, _ in _catalog_targets(registry)}
    live_targets = {target for _, target in live}
    stale = [
        key for key in PARAM_SHAPES
        if (key[0] == ANY_CAPABILITY and key[1] not in live_targets)
        or (key[0] != ANY_CAPABILITY and key not in live)
    ]
    assert not stale, f"PARAM_SHAPES entries with no matching OP_CATALOG param_map target: {stale}"


def test_every_shape_example_is_json_serializable():
    for key, shape in PARAM_SHAPES.items():
        for example in shape.examples:
            assert json.loads(json.dumps(example)) == example, key


def test_prompt_contains_where_and_rules_shapes():
    system = build_llm_messages("filter hospitals")[0]["content"]
    reference = op_param_shape_reference()

    assert reference in system
    where_line = next(line for line in reference.splitlines() if "filter_attribute.where" in line)
    assert json.dumps(FILTER_WHERE_EXAMPLES[0]) in where_line
    assert "never a string" in where_line

    rules_line = next(
        line for line in reference.splitlines() if "enrich_feature_properties.rules" in line
    )
    assert '"target"' in rules_line
    assert json.dumps(ENRICH_RULES_EXAMPLES[0]) in rules_line
    assert 'sort_limit has no "where" param' in system


def test_prompt_lists_every_structured_supported_op_param(registry):
    from orchestrator.planning.op_catalog import list_supported_ops

    supported = set(list_supported_ops())
    reference = op_param_shape_reference()
    for op, capability, source, target, annotation in _catalog_targets(registry):
        if op not in supported or _is_plain_scalar(annotation):
            continue
        if (capability, target) not in PARAM_SHAPES:
            continue  # wildcard entry, rendered once as <any op>.<param>
        assert re.search(rf"(^|[ /-]){re.escape(op)}\.{re.escape(source)}[: ]", reference, re.M), (
            f"{op}.{source} missing from the prompt's structured param section"
        )


# --------------------------------------------------------------------- #
# The examples must be accepted by the real plugins - an example the
# plugin would reject would teach the model a wrong shape.
# --------------------------------------------------------------------- #

def _fc(**properties):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "p1",
            "geometry": {"type": "Point", "coordinates": [51.3, 35.7]},
            "properties": {"id": "p1", **properties},
        }],
    }


@pytest.mark.parametrize("where", FILTER_WHERE_EXAMPLES)
def test_filter_where_examples_run_through_filter_features(where):
    from plugins.spatial_query_filter import filter_features

    result = filter_features(
        features=_fc(amenity="hospital", beds=120, emergency="yes"),
        where=where,
    )
    assert len(result.features) == 1  # every example matches this hospital
    assert _where_shape_error(where, "where") is None


def test_filter_bbox_and_geometry_type_examples_run_through_filter_features():
    from plugins.spatial_query_filter import filter_features

    for bbox in PARAM_SHAPES[("filter_features", "bbox")].examples:
        filter_features(features=_fc(), bbox=bbox)
    for geometry_types in PARAM_SHAPES[("filter_features", "geometry_types")].examples:
        filter_features(features=_fc(), geometry_types=geometry_types)


def test_reported_string_where_still_fails_at_plugin():
    # Documents the original failure mode the prompt guidance addresses.
    from plugins.spatial_query_filter import filter_features

    with pytest.raises(ValueError, match="where must be a dict/object or None"):
        filter_features(features=_fc(), where="amenity = hospital")


def test_enrich_rules_example_runs_through_enrich_feature_properties():
    from plugins.feature_enrichment import enrich_feature_properties

    result = enrich_feature_properties(
        features=_fc(distance="12.5", __in_polygon__=1),
        rules=ENRICH_RULES_EXAMPLES[0],
    )
    props = result.features[0]["properties"]
    assert props["distance_to_metro"] == 12.5
    assert props["inside_buildable_zone"] is True
    assert props["flood_risk"] == "low"


def test_join_fields_examples_run_through_join_feature_properties():
    from plugins.feature_enrichment import join_feature_properties

    right = _fc(population=10, district="d1", pop_total=10)
    for fields in PARAM_SHAPES[("join_feature_properties", "fields")].examples:
        join_feature_properties(left_features=_fc(), right_features=right, fields=fields)


def test_scoring_examples_run_through_score_features():
    from plugins.feature_scoring import score_features

    features = _fc(distance_to_metro=200.0, flood_risk="low")
    score_features(features=features, factors=SCORING_FACTORS_EXAMPLES[0])
    score_features(
        features=features,
        scoring_spec=PARAM_SHAPES[("score_features", "scoring_spec")].examples[0],
    )


def test_risk_examples_run_through_enrich_risk():
    from plugins.risk_enrichment import enrich_risk

    features = _fc(flood_zone="C")
    enrich_risk(
        features=features,
        default_risks=PARAM_SHAPES[("enrich_risk", "default_risks")].examples[0],
        overrides=PARAM_SHAPES[("enrich_risk", "overrides")].examples[0],
        rules=RISK_RULES_EXAMPLES[0],
    )
    enrich_risk(features=features, risk_spec=PARAM_SHAPES[("enrich_risk", "risk_spec")].examples[0])


def test_reclassify_rules_example_runs_through_reclassify_raster():
    from plugins.raster_reclassify import reclassify_raster

    raster = {
        "data": [[0.1, 0.5], [5, 2]],
        "metadata": {"transform": [1.0, 0.0, 0.0, 0.0, -1.0, 2.0], "crs": "EPSG:3857"},
    }
    reclassify_raster(raster=raster, rules=RECLASSIFY_RULES_EXAMPLES[0])


def test_report_spec_example_parses():
    from orchestrator.planning.report_spec import report_spec_from_dict

    spec = report_spec_from_dict(PARAM_SHAPES[("build_report", "report_spec")].examples[0])
    assert spec.tables[0].columns[1].field == "score"


def test_where_operator_set_matches_plugin():
    from plugins.spatial_query_filter import VALID_OPERATORS

    assert set(_FILTER_WHERE_OPERATORS) == set(VALID_OPERATORS)


# --------------------------------------------------------------------- #
# Generation-time validator (_validate_structured_param_shapes)
# --------------------------------------------------------------------- #

def _plan(op: str, params: dict) -> str:
    return json.dumps({
        "raw_query": "q",
        "goal": "g",
        "entities": [{"ref": "hospitals", "kind": "vector", "binding": {}, "hints": {}}],
        "operations": [{"op": op, "inputs": {"vector": "hospitals"}, "params": params, "output": "out"}],
        "outputs": [],
        "metadata": {},
    })


@pytest.mark.parametrize("where", [
    "amenity = hospital",
    ["amenity", "hospital"],
    {"field": "", "op": "eq", "value": "x"},
    {"field": "amenity", "op": "=", "value": "hospital"},
    {"and": {"field": "amenity", "value": "hospital"}},
    {"or": [{"field": "a", "op": "eq", "value": 1}, "b = 2"]},
    {"not": {"beds": {"greater": 3}}},
])
def test_invalid_where_rejected_at_generation(where):
    generator = LLMQuerySpecGenerator(StaticLLMClient(_plan("filter_attribute", {"where": where})))
    with pytest.raises(LLMSpecGenerationError, match="invalid where"):
        generator.generate("hospitals")


@pytest.mark.parametrize("where", [*FILTER_WHERE_EXAMPLES, None, {}])
def test_valid_where_accepted_at_generation(where):
    generator = LLMQuerySpecGenerator(StaticLLMClient(_plan("filter_attribute", {"where": where})))
    spec = generator.generate("hospitals")
    assert spec.operations[0].params["where"] == where


def test_sort_limit_where_also_validated():
    generator = LLMQuerySpecGenerator(StaticLLMClient(_plan("sort_limit", {"where": "a = 1"})))
    with pytest.raises(LLMSpecGenerationError, match="invalid where"):
        generator.generate("hospitals")


@pytest.mark.parametrize("rules", [
    [{"source": "distance", "transform": "float"}],
    [{"target": "", "value": 1}],
    ["distance_to_metro"],
    [{"target": "d", "source": "distance", "transform": "decimal"}],
])
def test_invalid_enrich_rules_rejected_at_generation(rules):
    generator = LLMQuerySpecGenerator(
        StaticLLMClient(_plan("enrich_feature_properties", {"rules": rules}))
    )
    with pytest.raises(LLMSpecGenerationError, match="invalid rules"):
        generator.generate("enrich")


# A non-list/empty rules value never reaches the validator: the Phase 8.2
# normalizer already drops such enrich_feature_properties nodes and rewires
# their consumers, so the plan still succeeds without the no-op node.


def test_valid_enrich_rules_accepted_at_generation():
    generator = LLMQuerySpecGenerator(
        StaticLLMClient(_plan("enrich_feature_properties", {"rules": ENRICH_RULES_EXAMPLES[0]}))
    )
    spec = generator.generate("enrich")
    assert spec.operations[0].params["rules"] == ENRICH_RULES_EXAMPLES[0]
