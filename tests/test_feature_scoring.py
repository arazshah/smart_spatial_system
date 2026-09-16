import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.planning.op_catalog import get_op, is_supported
from plugins.feature_scoring import rank_features, score_features


def _sample_features():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {
                    "name": "A",
                    "distance_to_poi": 100,
                    "__in_polygon__": True,
                    "flood_risk": "low",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 1]},
                "properties": {
                    "name": "B",
                    "distance_to_poi": 500,
                    "__in_polygon__": False,
                    "flood_risk": "high",
                },
            },
        ],
    }


def test_score_features_weighted_spec():
    spec = {
        "output_field": "investment_score",
        "scale": 100,
        "factors": [
            {
                "name": "near_poi",
                "field": "distance_to_poi",
                "type": "inverse_distance",
                "max_distance": 500,
                "weight": 0.5,
            },
            {
                "name": "buildable",
                "field": "__in_polygon__",
                "type": "boolean",
                "weight": 0.3,
            },
            {
                "name": "risk",
                "field": "flood_risk",
                "type": "risk_level",
                "weight": 0.2,
            },
        ],
    }

    result = score_features(_sample_features(), scoring_spec=spec)

    assert len(result.features) == 2

    a = result.features[0]["properties"]
    b = result.features[1]["properties"]

    assert "investment_score" in a
    assert "investment_score" in b
    assert a["investment_score"] > b["investment_score"]
    assert "__score_details__" in a
    assert result.metadata["operation"] == "score_features"


def test_rank_features_descending():
    scored = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"name": "B", "investment_score": 20},
            },
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"name": "A", "investment_score": 90},
            },
        ],
    }

    result = rank_features(
        scored,
        score_field="investment_score",
        rank_field="investment_rank",
    )

    assert result.features[0]["properties"]["name"] == "A"
    assert result.features[0]["properties"]["investment_rank"] == 1
    assert result.features[1]["properties"]["investment_rank"] == 2


def test_op_catalog_contains_feature_scoring_ops():
    assert is_supported("score_features")
    assert is_supported("rank_features")

    score_op = get_op("score_features")
    rank_op = get_op("rank_features")

    assert score_op.capability_name == "score_features"
    assert rank_op.capability_name == "rank_features"


# ------------------------------------------------------------------ #
# Missing "type" on a scoring factor: no silent default
# ------------------------------------------------------------------ #

def test_score_features_raises_when_a_factor_has_no_type():
    """
    Regression test: a missing "type" used to default to "boolean"
    silently. For a distance field, _truthy(0.0) is False and
    _truthy(any nonzero distance) is True, so every feature ended up with
    a near-constant, meaningless score instead of an error - exactly how
    a full batch of LLM-generated scoring specs that omitted "type" on a
    distance factor came out 0.0 for every feature.
    """
    spec = {
        "output_field": "score",
        "factors": [
            {"field": "distance_to_poi", "weight": 1},
        ],
    }

    with pytest.raises(ValueError, match="distance_to_poi"):
        score_features(_sample_features(), scoring_spec=spec)


def test_score_features_boolean_type_still_works_when_explicit():
    """
    "boolean" itself is a perfectly valid, still-supported type - only an
    UNSTATED type is rejected.
    """
    spec = {
        "output_field": "score",
        "factors": [
            {"field": "__in_polygon__", "type": "boolean", "weight": 1},
        ],
    }

    result = score_features(_sample_features(), scoring_spec=spec)
    a = result.features[0]["properties"]
    b = result.features[1]["properties"]
    assert a["score"] > b["score"]
