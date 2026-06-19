from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.planning.op_catalog import get_op, is_supported
from plugins.feature_enrichment import enrich_feature_properties, join_feature_properties


def _features():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {
                    "id": "p1",
                    "distance": "125.5",
                    "__in_polygon__": True,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 1]},
                "properties": {
                    "id": "p2",
                    "distance_m": 300,
                    "__in_polygon__": False,
                },
            },
        ],
    }


def test_enrich_feature_properties_copies_and_transforms_fields():
    result = enrich_feature_properties(
        _features(),
        rules=[
            {
                "target": "distance_to_poi",
                "first_existing": ["distance_m", "distance"],
                "transform": "float",
            },
            {
                "target": "inside_buildable_zone",
                "source": "__in_polygon__",
                "transform": "bool",
            },
            {
                "target": "flood_risk",
                "value": "low",
            },
        ],
    )

    props1 = result.features[0]["properties"]
    props2 = result.features[1]["properties"]

    assert props1["distance_to_poi"] == 125.5
    assert props1["inside_buildable_zone"] is True
    assert props1["flood_risk"] == "low"

    assert props2["distance_to_poi"] == 300.0
    assert props2["inside_buildable_zone"] is False

    assert result.metadata["operation"] == "enrich_feature_properties"
    assert result.metadata["feature_count"] == 2


def test_enrich_feature_properties_supports_dotted_target():
    result = enrich_feature_properties(
        _features(),
        rules=[
            {
                "target": "scores.distance_to_poi",
                "source": "distance",
                "transform": "float",
                "default": 0,
            }
        ],
    )

    props = result.features[0]["properties"]
    assert props["scores"]["distance_to_poi"] == 125.5


def test_join_feature_properties_by_id_with_field_list():
    left = _features()

    right = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "id": "p1",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                },
            },
            {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "id": "p2",
                    "earthquake_risk": "high",
                    "fire_risk": "medium",
                },
            },
        ],
    }

    result = join_feature_properties(
        left,
        right,
        left_key="id",
        right_key="id",
        fields=["earthquake_risk", "fire_risk"],
    )

    assert result.features[0]["properties"]["earthquake_risk"] == "medium"
    assert result.features[0]["properties"]["fire_risk"] == "low"
    assert result.features[1]["properties"]["earthquake_risk"] == "high"

    assert result.metadata["matched_count"] == 2
    assert result.metadata["copied_field_count"] == 4


def test_join_feature_properties_supports_field_mapping_and_prefix():
    left = _features()

    right = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "id": "p1",
                    "risk": "low",
                },
            }
        ],
    }

    result = join_feature_properties(
        left,
        right,
        left_key="id",
        right_key="id",
        fields={"risk": "flood_risk"},
        unmatched="keep",
    )

    assert result.features[0]["properties"]["flood_risk"] == "low"
    assert "flood_risk" not in result.features[1]["properties"]
    assert result.metadata["matched_count"] == 1
    assert result.metadata["unmatched_count"] == 1


def test_op_catalog_contains_feature_enrichment_ops():
    assert is_supported("enrich_feature_properties")
    assert is_supported("join_feature_properties")

    enrich = get_op("enrich_feature_properties")
    join = get_op("join_feature_properties")

    assert enrich.capability_name == "enrich_feature_properties"
    assert enrich.input_map["vector"] == "features"

    assert join.capability_name == "join_feature_properties"
    assert join.input_map["left"] == "left_features"
    assert join.input_map["right"] == "right_features"
