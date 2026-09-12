"""
Tests for the rule-based accessibility QuerySpec generator.

Two things are load-bearing here and are asserted explicitly rather than
implied by an end-to-end run:

1. Determinism. The generator exists to be the reproducible arm of a
   comparison against the LLM-backed path, so identical inputs must
   produce an identical plan - not merely an equivalent one.
2. The explicit report spec. build_report silently falls back to the
   real-estate spec when none is passed, producing a table of empty
   strings next to a correct summary.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.planner import DeterministicPlanner
from smart_spatial_system.application.services.query_execution.accessibility_query_spec import (
    AmenitySpec,
    build_accessibility_initial_inputs,
    build_accessibility_query_spec,
)


def _point(name: str, lon: float, lat: float) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"name": name},
    }


def _fc(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def _amenities() -> list[AmenitySpec]:
    return [
        AmenitySpec(
            ref="metro",
            distance_field="distance_to_metro_m",
            max_distance_m=1000.0,
            weight=2.0,
        ),
        AmenitySpec(
            ref="schools",
            distance_field="distance_to_school_m",
            max_distance_m=1500.0,
        ),
    ]


# ------------------------------------------------------------------ #
# Structure
# ------------------------------------------------------------------ #

def test_chain_reprojects_every_layer_before_measuring():
    spec = build_accessibility_query_spec("q", _amenities())

    ops = [(op.op, op.output) for op in spec.operations]

    # One crs_transform for the sites and one per amenity: nearest_neighbor
    # does not reproject, so an unprojected layer would yield degrees.
    assert [name for name, _ in ops].count("crs_transform") == 3

    transform_outputs = [out for name, out in ops if name == "crs_transform"]
    nearest_ops = [op for op in spec.operations if op.op == "nearest_neighbor"]
    for op in nearest_ops:
        assert op.inputs["target"] in transform_outputs


def test_nearest_neighbor_steps_chain_and_keep_distinct_distance_fields():
    spec = build_accessibility_query_spec("q", _amenities())

    nearest_ops = [op for op in spec.operations if op.op == "nearest_neighbor"]
    assert len(nearest_ops) == 2

    assert [op.params["distance_field"] for op in nearest_ops] == [
        "distance_to_metro_m",
        "distance_to_school_m",
    ]
    # The second step reads the first step's output, so distances
    # accumulate onto the same features instead of replacing each other.
    assert nearest_ops[1].inputs["source"] == nearest_ops[0].output


def test_score_factors_mirror_the_amenity_list():
    spec = build_accessibility_query_spec("q", _amenities())

    score_op = next(op for op in spec.operations if op.op == "score_features")
    factors = score_op.params["factors"]

    assert [f["field"] for f in factors] == [
        "distance_to_metro_m",
        "distance_to_school_m",
    ]
    assert [f["weight"] for f in factors] == [2.0, 1.0]
    assert {f["type"] for f in factors} == {"inverse_distance"}
    assert [f["max_distance_m"] for f in factors] == [1000.0, 1500.0]


def test_report_spec_is_explicit_and_matches_the_produced_fields():
    spec = build_accessibility_query_spec("q", _amenities())

    report_op = next(op for op in spec.operations if op.op == "build_report")
    report_spec = report_op.params["report_spec"]

    # A dict, not a dataclass: QuerySpec params must stay serializable.
    assert isinstance(report_spec, dict)

    fields = [col["field"] for col in report_spec["tables"][0]["columns"]]
    assert fields == [
        "rank",
        "name",
        "accessibility_score",
        "distance_to_metro_m",
        "distance_to_school_m",
    ]
    assert report_spec["language"] == "en"


def test_amenity_label_defaults_to_the_ref_and_can_be_overridden():
    amenities = [
        AmenitySpec(
            ref="transit_stops",
            distance_field="d_transit",
            max_distance_m=500.0,
        ),
        AmenitySpec(
            ref="parks",
            distance_field="d_parks",
            max_distance_m=500.0,
            label="Distance to green space (m)",
        ),
    ]
    spec = build_accessibility_query_spec("q", amenities)
    report_op = next(op for op in spec.operations if op.op == "build_report")
    labels = [col["label"] for col in report_op.params["report_spec"]["tables"][0]["columns"]]

    assert "Distance to transit stops (m)" in labels
    assert "Distance to green space (m)" in labels


def test_limit_is_only_passed_when_requested():
    without = build_accessibility_query_spec("q", _amenities())
    with_limit = build_accessibility_query_spec("q", _amenities(), limit=5)

    rank_without = next(op for op in without.operations if op.op == "rank_features")
    rank_with = next(op for op in with_limit.operations if op.op == "rank_features")

    assert "limit" not in rank_without.params
    assert rank_with.params["limit"] == 5


def test_spec_is_byte_identical_for_identical_inputs():
    first = build_accessibility_query_spec("q", _amenities())
    second = build_accessibility_query_spec("q", _amenities())

    assert json.dumps(asdict(first), sort_keys=True) == json.dumps(
        asdict(second), sort_keys=True
    )


# ------------------------------------------------------------------ #
# Validation
# ------------------------------------------------------------------ #

def test_empty_amenity_list_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        build_accessibility_query_spec("q", [])


def test_duplicate_distance_fields_are_rejected():
    amenities = [
        AmenitySpec(ref="metro", distance_field="d", max_distance_m=500.0),
        AmenitySpec(ref="schools", distance_field="d", max_distance_m=500.0),
    ]
    with pytest.raises(ValueError, match="distance_field"):
        build_accessibility_query_spec("q", amenities)


def test_duplicate_refs_are_rejected():
    amenities = [
        AmenitySpec(ref="metro", distance_field="d1", max_distance_m=500.0),
        AmenitySpec(ref="metro", distance_field="d2", max_distance_m=500.0),
    ]
    with pytest.raises(ValueError, match="unique"):
        build_accessibility_query_spec("q", amenities)


def test_sites_ref_colliding_with_an_amenity_is_rejected():
    amenities = [AmenitySpec(ref="sites", distance_field="d", max_distance_m=500.0)]
    with pytest.raises(ValueError, match="must differ"):
        build_accessibility_query_spec("q", amenities)


@pytest.mark.parametrize("bad", [0.0, -100.0])
def test_non_positive_max_distance_is_rejected(bad: float):
    amenities = [AmenitySpec(ref="metro", distance_field="d", max_distance_m=bad)]
    with pytest.raises(ValueError, match="max_distance_m"):
        build_accessibility_query_spec("q", amenities)


# ------------------------------------------------------------------ #
# End to end through planner + executor
# ------------------------------------------------------------------ #

def test_chain_runs_end_to_end_and_reports_real_values():
    sites = _fc(_point("Site A", 16.370, 48.208), _point("Site B", 16.390, 48.220))
    metro = _fc(_point("Karlsplatz", 16.3712, 48.2008))
    schools = _fc(_point("School 1", 16.3800, 48.2100))

    spec = build_accessibility_query_spec("Rank sites by accessibility", _amenities())
    plan = DeterministicPlanner().build(spec)

    registry = CapabilityRegistry.from_plugin_modules(tolerant=True)
    result = DagExecutor(lambda name: registry.resolve(name).callable).execute(
        plan,
        initial_inputs=build_accessibility_initial_inputs(
            sites=sites,
            amenity_layers={"metro": metro, "schools": schools},
        ),
    )

    assert result.success, result.errors

    report = result.outputs["sites_report"]
    report = report if isinstance(report, dict) else report.__dict__

    assert report["meta"]["language"] == "en"
    assert report["summary"]["top_name"] == "Site A"

    rows = report["table"]["rows"]
    assert len(rows) == 2
    assert [row["rank"] for row in rows] == [1, 2]

    # The real-estate fallback spec would leave every one of these empty.
    for row in rows:
        assert isinstance(row["distance_to_metro_m"], (int, float))
        assert isinstance(row["distance_to_school_m"], (int, float))

    # Metres, not degrees: without the crs_transform steps these would be
    # fractions of a degree.
    assert rows[0]["distance_to_metro_m"] > 100
