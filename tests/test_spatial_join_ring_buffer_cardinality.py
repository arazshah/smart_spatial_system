"""
Regression tests for the spatial_join cardinality / overlapping-ring-buffer
undercounting bug found in the smart-spatial-tehran-tod-gradient case
study.

spatial_join_features defaults params.cardinality to "first": a source
feature matching several target features is only joined to the first one
found, not all of them. When the target layer is a ring_buffer output (or
any layer with multiple zones generated from separate input features),
those zones routinely overlap - so a source feature inside more than one
zone is silently undercounted with the default.

Two things are tested here:
    1. The underlying plugin behavior itself: with cardinality="first"
       (the default), a point inside two overlapping station rings is
       only counted once; with cardinality="one_to_many" it is counted
       once per matching ring/station, as it should be.
    2. That LLMQuerySpecGenerator.generate() now defaults cardinality to
       "one_to_many" automatically when a spatial_join's target traces
       back to a ring_buffer op and the caller left cardinality unset -
       so this is fixed at the system level, not just documented in the
       prompt.

Run:
    pytest tests/test_spatial_join_ring_buffer_cardinality.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("shapely")

from orchestrator.planning.llm_spec_generator import (  # noqa: E402
    LLMQuerySpecGenerator,
    StaticLLMClient,
)
from plugins.ring_buffer_analysis import generate_ring_buffers  # noqa: E402
from plugins.spatial_join import spatial_join_features  # noqa: E402


def _two_overlapping_stations():
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "properties": {"station_id": "A"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1000.0, 0.0]},
            "properties": {"station_id": "B"},
        },
    ]


def _amenity_point_between_them():
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [500.0, 0.0]},
            "properties": {"amenity_id": "cafe-1"},
        }
    ]


# ------------------------------------------------------------------ #
# 1. Plugin-level behavior: cardinality="first" undercounts overlapping
#    ring-buffer zones; cardinality="one_to_many" does not.
# ------------------------------------------------------------------ #

def test_default_cardinality_undercounts_a_point_in_two_overlapping_rings():
    stations = _two_overlapping_stations()
    amenity = _amenity_point_between_them()

    rings = generate_ring_buffers(
        features=stations,
        distances=[1200],
        engine="shapely",
    )

    # Sanity check on the fixture: the amenity point (500m from each
    # station, stations 1000m apart) must fall inside BOTH 1200m rings.
    assert rings.metadata["output_feature_count"] == 2

    joined = spatial_join_features(
        source_features=amenity,
        target_features=rings,
        predicate="within",
        # cardinality intentionally omitted - exercising the plugin's
        # own default ("first").
    )

    assert joined.metadata["cardinality"] == "first"
    assert len(joined.features) == 1

    matched_station_ids = {
        f["properties"]["_joined_target_properties"]["station_id"]
        for f in joined.features
    }
    assert len(matched_station_ids) == 1


def test_one_to_many_cardinality_counts_the_point_for_every_overlapping_ring():
    stations = _two_overlapping_stations()
    amenity = _amenity_point_between_them()

    rings = generate_ring_buffers(
        features=stations,
        distances=[1200],
        engine="shapely",
    )

    joined = spatial_join_features(
        source_features=amenity,
        target_features=rings,
        predicate="within",
        cardinality="one_to_many",
        include_target_properties=True,
    )

    assert joined.metadata["cardinality"] == "one_to_many"
    assert len(joined.features) == 2

    matched_station_ids = {
        f["properties"]["_joined_target_properties"]["station_id"]
        for f in joined.features
    }
    assert matched_station_ids == {"A", "B"}


# ------------------------------------------------------------------ #
# 2. LLMQuerySpecGenerator: cardinality auto-defaults to "one_to_many"
#    when spatial_join's target traces back to ring_buffer and the
#    caller left cardinality unset.
# ------------------------------------------------------------------ #

def _tod_gradient_spec_json(*, include_cardinality: bool) -> dict:
    join_params = {"predicate": "within", "include_target_properties": True}
    if include_cardinality:
        join_params["cardinality"] = "first"

    return {
        "raw_query": "for each amenity point, which station rings does it fall into",
        "goal": "tod_gradient",
        "entities": [
            {"ref": "amenity", "kind": "vector"},
            {"ref": "stations", "kind": "vector"},
        ],
        "operations": [
            {
                "op": "crs_transform",
                "inputs": {"vector": "amenity"},
                "params": {"source_crs": "EPSG:4326", "target_crs": "EPSG:32639"},
                "output": "amenity_metric",
            },
            {
                "op": "crs_transform",
                "inputs": {"vector": "stations"},
                "params": {"source_crs": "EPSG:4326", "target_crs": "EPSG:32639"},
                "output": "stations_metric",
            },
            {
                "op": "ring_buffer",
                "inputs": {"vector": "stations_metric"},
                "params": {"distances": [200, 500, 800, 1200]},
                "output": "stations_rings",
            },
            {
                "op": "spatial_join",
                "inputs": {"source": "amenity_metric", "target": "stations_rings"},
                "params": join_params,
                "output": "amenity_with_zone",
            },
        ],
        "outputs": [
            {"kind": "vector_layer", "source": "amenity_with_zone", "config": {}}
        ],
    }


def test_generate_defaults_cardinality_to_one_to_many_for_ring_buffer_target():
    llm_json = _tod_gradient_spec_json(include_cardinality=False)

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate(
        "for each amenity point, which station rings does it fall into"
    )

    join_op = next(op for op in spec.operations if op.op == "spatial_join")
    assert join_op.params["cardinality"] == "one_to_many"


def test_generate_does_not_override_an_explicit_cardinality():
    llm_json = _tod_gradient_spec_json(include_cardinality=True)

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate(
        "for each amenity point, which station rings does it fall into"
    )

    join_op = next(op for op in spec.operations if op.op == "spatial_join")
    assert join_op.params["cardinality"] == "first"
