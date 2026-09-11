"""
Rule-based QuerySpec generator for real-estate ranking queries
(REFACTOR_PLAN.md Phase 5, step 5 -- see
docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md).

Per the plan's step 5, the existing keyword-based classifier
(real_estate_classifier.py::looks_like_real_estate_ranking_query) stays the
trigger; this module is what a matched query now builds instead of calling
the legacy direct handler -- a QuerySpec chaining the OP_CATALOG-registered
real_estate_spatial_enrich -> real_estate_score -> filter_attribute ->
rank_features -> build_report ops, handed to the same
DeterministicPlanner/DagExecutor path the general planning path uses.

This generator is deliberately NOT an LLM-based QuerySpecGenerator (unlike
LLMQuerySpecGenerator): the operations, their order, and their parameters
are fixed by the MVP scoring/eligibility rules
(real_estate_scoring.py/real_estate_context.py) -- there is nothing for an
LLM to decide here, per ADR-001's "rule-based/keyword path" allowance.
"""

from __future__ import annotations

from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec

PROPERTIES_REF = "properties"
METRO_REF = "metro"
MALLS_REF = "malls"
MAIN_ROADS_REF = "main_roads"
ALLOWED_ZONES_REF = "allowed_zones"

ENRICHED_REF = "real_estate_enriched"
SCORED_REF = "real_estate_scored"
ELIGIBLE_ONLY_REF = "real_estate_eligible"
RANKED_REF = "real_estate_ranked"
REPORT_REF = "real_estate_report"


def build_real_estate_ranking_query_spec(raw_query: str) -> QuerySpec:
    """
    Build the fixed real-estate ranking QuerySpec.

    Args:
        raw_query:
            The original natural-language query, kept for
            traceability/audit only -- it does not affect the operation
            chain, which is the same for every matched query.

    Returns:
        A QuerySpec with 5 entities (properties + the four optional
        spatial layers) and 5 chained operations, producing a single
        "report" output. Callers must provide initial_inputs for all 5
        entity refs -- an empty FeatureCollection
        ({"type": "FeatureCollection", "features": []}) for any layer
        with no real data (see real_estate_spatial_enrich's OP_CATALOG
        notes).
    """
    return QuerySpec(
        raw_query=raw_query,
        goal="Enrich, score, filter, rank and report real-estate properties.",
        entities=[
            EntitySpec(ref=PROPERTIES_REF, kind="vector"),
            EntitySpec(ref=METRO_REF, kind="vector"),
            EntitySpec(ref=MALLS_REF, kind="vector"),
            EntitySpec(ref=MAIN_ROADS_REF, kind="vector"),
            EntitySpec(ref=ALLOWED_ZONES_REF, kind="vector"),
        ],
        operations=[
            OperationSpec(
                op="real_estate_spatial_enrich",
                inputs={
                    "vector": PROPERTIES_REF,
                    "metro": METRO_REF,
                    "malls": MALLS_REF,
                    "main_roads": MAIN_ROADS_REF,
                    "allowed_zones": ALLOWED_ZONES_REF,
                },
                params={},
                output=ENRICHED_REF,
            ),
            OperationSpec(
                op="real_estate_score",
                inputs={"vector": ENRICHED_REF},
                params={},
                output=SCORED_REF,
            ),
            OperationSpec(
                op="filter_attribute",
                inputs={"vector": SCORED_REF},
                params={"where": {"eligible": True}},
                output=ELIGIBLE_ONLY_REF,
            ),
            OperationSpec(
                op="rank_features",
                inputs={"vector": ELIGIBLE_ONLY_REF},
                params={
                    "score_field": "score",
                    "rank_field": "rank",
                    "descending": True,
                },
                output=RANKED_REF,
            ),
            OperationSpec(
                op="build_report",
                inputs={"vector": RANKED_REF},
                params={
                    "score_field": "score",
                    "rank_field": "rank",
                    "name_field": "name",
                },
                output=REPORT_REF,
            ),
        ],
        outputs=[
            OutputSpec(kind="report", source=REPORT_REF, format="json"),
        ],
        source="rule_based_real_estate",
    )


def build_real_estate_ranking_initial_inputs(
    *,
    feature_collection: dict,
    spatial_context: dict[str, list[dict]] | None,
) -> dict[str, dict]:
    """
    Build the initial_inputs dict matching
    build_real_estate_ranking_query_spec's 5 entity refs.

    Args:
        feature_collection:
            Property FeatureCollection, as already extracted by
            real_estate_context.py::extract_property_feature_collection_from_inputs.
        spatial_context:
            Optional {"metro": [...], "malls": [...], "main_roads": [...],
            "allowed_zones": [...]} dict, as already extracted by
            real_estate_context.py::extract_real_estate_spatial_context_from_inputs.
            Missing or empty groups become an empty FeatureCollection.

    Returns:
        Dict keyed by the QuerySpec's entity refs, ready to pass as
        DagExecutor's initial_inputs.
    """
    context = spatial_context or {}

    def _layer_fc(group: str) -> dict:
        return {
            "type": "FeatureCollection",
            "features": list(context.get(group) or []),
        }

    return {
        PROPERTIES_REF: feature_collection,
        METRO_REF: _layer_fc("metro"),
        MALLS_REF: _layer_fc("malls"),
        MAIN_ROADS_REF: _layer_fc("main_roads"),
        ALLOWED_ZONES_REF: _layer_fc("allowed_zones"),
    }
