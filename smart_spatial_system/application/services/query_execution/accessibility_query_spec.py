"""
Rule-based QuerySpec generator for multi-amenity accessibility analysis.

This is the domain-neutral counterpart of
real_estate_ranking_query_spec.py. Where that module encodes one fixed
chain for one domain, this one builds a chain from a caller-supplied list
of amenities, so the same generic OP_CATALOG operations express "score
these sites by how close they are to these things" for any domain -
15-minute-city accessibility, facility siting, service-coverage gaps.

Like the real-estate generator, and unlike LLMQuerySpecGenerator, this is
deliberately NOT LLM-backed: the operation chain follows mechanically from
the amenity list, so there is nothing for a model to decide, and the
resulting plan is byte-identical for identical inputs. That is what makes
it usable as the deterministic arm of a reproducibility comparison against
the LLM-generated path.

Chain built per call:

    crs_transform(sites)                        -> sites in a metric CRS
    crs_transform(amenity_i)                    -> amenity in the same CRS
    nearest_neighbor(sites, amenity_i)          -> distance field per amenity
      ... repeated, each writing its own field ...
    score_features(inverse_distance factors)    -> weighted accessibility score
    rank_features                               -> rank
    build_report                                -> report

Why the crs_transform steps are not optional:
nearest_neighbor computes planar distance in whatever units the input CRS
uses and does not reproject. WGS84 input therefore yields distances in
DEGREES. Every amenity layer is reprojected to the same metric CRS as the
sites so the distances, the max_distance thresholds and the resulting
scores are all in metres.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from orchestrator.planning.report_spec import default_accessibility_report_spec
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec

SITES_REF = "sites"

DEFAULT_SOURCE_CRS = "EPSG:4326"
DEFAULT_TARGET_CRS = "EPSG:3857"

SCORE_FIELD = "accessibility_score"
RANK_FIELD = "rank"


@dataclass(frozen=True)
class AmenitySpec:
    """
    One amenity layer to measure distance to.

    Attributes:
        ref:
            Entity ref for the layer; also the key the caller must supply
            in initial_inputs (e.g. "metro", "schools", "parks").
        distance_field:
            Property the measured distance is written to. Must be unique
            across the amenity list - nearest_neighbor steps are chained
            over the same features, so a repeated field name would silently
            overwrite an earlier distance.
        max_distance_m:
            Distance at which this amenity contributes nothing. The factor
            scores 1.0 at distance 0 and falls linearly to 0.0 here.
        weight:
            Relative importance in the weighted sum.
        label:
            Column heading for this amenity's distance in the report.
            Defaults to a heading derived from `ref`.
    """

    ref: str
    distance_field: str
    max_distance_m: float
    weight: float = 1.0
    label: str = ""

    def column_label(self) -> str:
        """Report column heading for this amenity's distance field."""
        if self.label:
            return self.label
        return f"Distance to {self.ref.replace('_', ' ')} (m)"


def build_accessibility_query_spec(
    raw_query: str,
    amenities: Sequence[AmenitySpec],
    *,
    sites_ref: str = SITES_REF,
    source_crs: str = DEFAULT_SOURCE_CRS,
    target_crs: str = DEFAULT_TARGET_CRS,
    score_field: str = SCORE_FIELD,
    rank_field: str = RANK_FIELD,
    name_field: str = "name",
    limit: int | None = None,
) -> QuerySpec:
    """
    Build an accessibility-scoring QuerySpec for the given amenities.

    Args:
        raw_query:
            The original natural-language query, carried for audit only -
            it does not influence the chain.
        amenities:
            Amenity layers to measure to, in the order they should be
            chained. Must be non-empty and use distinct refs and distinct
            distance fields.
        sites_ref:
            Entity ref of the layer being scored.
        source_crs / target_crs:
            CRS of the supplied data, and the metric CRS distances are
            computed in. The EPSG:3857 default is a safe global fallback,
            but its metres are inflated by 1/cos(latitude) - about 1.5x at
            Vienna - so pass a local projected CRS (EPSG:31256 for Vienna,
            a UTM zone elsewhere) whenever the distances themselves are
            reported rather than only compared.
        score_field / rank_field / name_field:
            Output property names for the score, the rank, and the label
            used in the report.
        limit:
            Optional cap on ranked results.

    Returns:
        A QuerySpec producing a single "report" output. Callers must supply
        initial_inputs for `sites_ref` and for every amenity ref.

    Raises:
        ValueError: on an empty amenity list, duplicate refs, duplicate
            distance fields, or a non-positive max_distance_m.
    """
    if not amenities:
        raise ValueError("amenities must contain at least one AmenitySpec.")

    refs = [a.ref for a in amenities]
    if len(set(refs)) != len(refs):
        raise ValueError(f"amenity refs must be unique. Got: {refs}")

    distance_fields = [a.distance_field for a in amenities]
    if len(set(distance_fields)) != len(distance_fields):
        raise ValueError(
            "amenity distance_field values must be unique, otherwise chained "
            f"nearest_neighbor steps overwrite each other. Got: {distance_fields}"
        )

    if sites_ref in refs:
        raise ValueError(f"sites_ref {sites_ref!r} must differ from every amenity ref.")

    for amenity in amenities:
        if amenity.max_distance_m <= 0:
            raise ValueError(
                f"max_distance_m must be > 0 for amenity {amenity.ref!r}, "
                f"got {amenity.max_distance_m}."
            )

    entities = [EntitySpec(ref=sites_ref, kind="vector")]
    entities.extend(EntitySpec(ref=amenity.ref, kind="vector") for amenity in amenities)

    operations: list[OperationSpec] = []

    sites_metric_ref = f"{sites_ref}_metric"
    operations.append(
        OperationSpec(
            op="crs_transform",
            inputs={"vector": sites_ref},
            params={"source_crs": source_crs, "target_crs": target_crs},
            output=sites_metric_ref,
        )
    )

    # Each amenity is reprojected, then measured against the running
    # enriched site layer, so distances accumulate onto the same features.
    current_sites_ref = sites_metric_ref
    for index, amenity in enumerate(amenities):
        amenity_metric_ref = f"{amenity.ref}_metric"
        operations.append(
            OperationSpec(
                op="crs_transform",
                inputs={"vector": amenity.ref},
                params={"source_crs": source_crs, "target_crs": target_crs},
                output=amenity_metric_ref,
            )
        )

        enriched_ref = f"{sites_ref}_with_{amenity.ref}"
        operations.append(
            OperationSpec(
                op="nearest_neighbor",
                inputs={"source": current_sites_ref, "target": amenity_metric_ref},
                # Both sides were just reprojected to target_crs above, so
                # these are always equal here - passed anyway so
                # find_nearest_neighbors' own source/target CRS mismatch
                # check (plugins/distance_calculator.py) protects this
                # call too, not only hand-written or LLM-generated plans.
                params={
                    "k": 1,
                    "distance_field": amenity.distance_field,
                    "source_crs": target_crs,
                    "target_crs": target_crs,
                },
                output=enriched_ref,
            )
        )
        current_sites_ref = enriched_ref
        del index

    scored_ref = f"{sites_ref}_scored"
    operations.append(
        OperationSpec(
            op="score_features",
            inputs={"vector": current_sites_ref},
            params={
                "factors": [
                    {
                        "name": amenity.ref,
                        "field": amenity.distance_field,
                        "type": "inverse_distance",
                        "max_distance_m": amenity.max_distance_m,
                        "weight": amenity.weight,
                    }
                    for amenity in amenities
                ],
                "output_field": score_field,
                "normalize_weights": True,
            },
            output=scored_ref,
        )
    )

    ranked_ref = f"{sites_ref}_ranked"
    rank_params: dict[str, object] = {
        "score_field": score_field,
        "rank_field": rank_field,
        "descending": True,
    }
    if limit is not None:
        rank_params["limit"] = limit

    operations.append(
        OperationSpec(
            op="rank_features",
            inputs={"vector": scored_ref},
            params=rank_params,
            output=ranked_ref,
        )
    )

    # An explicit report spec is not optional here. build_report falls back
    # to the real-estate default when none is given, which asks for columns
    # (investment_score, flood_risk, ...) that this chain never produces -
    # the report then renders a full table of empty strings while its
    # summary stays correct, so the failure is easy to miss.
    report_spec = default_accessibility_report_spec(
        ranked_source=ranked_ref,
        amenity_columns=[
            (amenity.distance_field, amenity.column_label()) for amenity in amenities
        ],
        score_field=score_field,
        rank_field=rank_field,
        name_field=name_field,
    )

    report_ref = f"{sites_ref}_report"
    operations.append(
        OperationSpec(
            op="build_report",
            inputs={"vector": ranked_ref},
            params={
                "score_field": score_field,
                "rank_field": rank_field,
                "name_field": name_field,
                "report_spec": asdict(report_spec),
            },
            output=report_ref,
        )
    )

    return QuerySpec(
        raw_query=raw_query,
        goal=(
            "Reproject, measure distance to each amenity, score, rank and "
            "report site accessibility."
        ),
        entities=entities,
        operations=operations,
        outputs=[OutputSpec(kind="report", source=report_ref, format="json")],
        source="rule_based_accessibility",
    )


def build_accessibility_initial_inputs(
    *,
    sites: dict,
    amenity_layers: dict[str, dict],
    sites_ref: str = SITES_REF,
) -> dict[str, dict]:
    """
    Build the initial_inputs dict matching build_accessibility_query_spec.

    Args:
        sites:
            FeatureCollection of the sites being scored.
        amenity_layers:
            {amenity_ref: FeatureCollection}, one per AmenitySpec.
        sites_ref:
            Must match the value passed to build_accessibility_query_spec.

    Returns:
        Dict keyed by entity ref, ready to pass as initial_inputs.

    Note:
        Unlike the real-estate generator, missing amenity layers are NOT
        silently replaced with empty collections: nearest_neighbor raises
        on an empty target, and a silently-dropped amenity would change the
        score without changing the plan - which would quietly break the
        reproducibility guarantee this generator exists to provide.
    """
    initial_inputs: dict[str, dict] = {sites_ref: sites}
    initial_inputs.update(amenity_layers)
    return initial_inputs
