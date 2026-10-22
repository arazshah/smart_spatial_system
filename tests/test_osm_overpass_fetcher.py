"""osm_overpass_fetcher: query building (no injection), conversion, bounds and limits."""

from __future__ import annotations

import json

import pytest

import plugins.osm_overpass_fetcher as module
from plugins.osm_overpass_fetcher import (
    build_query,
    fetch_osm_features,
    to_features,
)
from smart_spatial_system.shared.net_policy import NetworkPolicyError

BBOX = [51.30, 35.68, 51.40, 35.75]

ELEMENTS = [
    {"type": "node", "id": 1, "lat": 35.7, "lon": 51.35, "tags": {"amenity": "hospital", "name": "ب"}},
    {"type": "way", "id": 2, "tags": {"building": "yes"},
     "geometry": [{"lat": 35.7, "lon": 51.3}, {"lat": 35.7, "lon": 51.31}, {"lat": 35.71, "lon": 51.31},
                  {"lat": 35.7, "lon": 51.3}]},
    {"type": "way", "id": 3, "tags": {"highway": "primary"},
     "geometry": [{"lat": 35.7, "lon": 51.3}, {"lat": 35.71, "lon": 51.32}]},
    {"type": "way", "id": 4, "tags": {"highway": "residential", "area": "no"},
     "geometry": [{"lat": 1, "lon": 1}, {"lat": 1, "lon": 2}, {"lat": 2, "lon": 2}, {"lat": 1, "lon": 1}]},
    {"type": "relation", "id": 5, "tags": {"type": "multipolygon"}},
    {"type": "way", "id": 6, "tags": {}, "geometry": []},
]


@pytest.fixture
def served(monkeypatch):
    """Replace the network call; records the request and returns the given elements."""
    calls = []
    config = {"min_interval_seconds": 0, "endpoint": "https://overpass.example/api"}
    monkeypatch.setattr(module, "load_config", lambda _id: config)

    def install(elements=None, remark=None, body=None):
        def fake_fetch(url, **kwargs):
            calls.append((url, kwargs))
            payload = body if body is not None else json.dumps(
                {"elements": ELEMENTS if elements is None else elements,
                 **({"remark": remark} if remark else {})}).encode()
            return payload, {"content_type": "application/json", "status": 200}

        monkeypatch.setattr(module, "fetch_limited", fake_fetch)
        return calls

    install.config = config
    return install


def test_conversion_of_nodes_ways_and_relations():
    features, skipped = to_features(ELEMENTS)
    kinds = {f["properties"]["osm_id"]: f["geometry"]["type"] for f in features}
    assert kinds == {1: "Point", 2: "Polygon", 3: "LineString", 4: "LineString"}
    assert skipped == 1  # the relation
    assert features[0]["properties"]["name"] == "ب" and features[0]["properties"]["osm_type"] == "node"


def test_fetch_builds_a_structured_query_and_reports_attribution(served):
    calls = served()
    out = fetch_osm_features(BBOX, {"amenity": ["hospital", "clinic"], "building": True})
    url, kwargs = calls[0]
    assert url == "https://overpass.example/api"
    query = kwargs["params"]["data"]
    assert '["amenity"~"^(hospital|clinic)$"]' in query and '["building"]' in query
    assert "(35.6800000,51.3000000,35.7500000,51.4000000)" in query and query.endswith("out geom 20001;")
    assert kwargs["accept_content_types"] and kwargs["allow_private_network"] is False
    assert out.metadata["attribution"].startswith("© OpenStreetMap") and out.metadata["crs"] == "EPSG:4326"
    assert out.metadata["skipped_relations"] == 1 and out.metadata["feature_count"] == 4


@pytest.mark.parametrize("tags", [
    {"amenity": 'hospital"];out;//'}, {'a"]': "x"}, {"amenity": "a\\b"}, {"amenity": "x\ny"},
    {"amenity": ["ok", 'bad"']}, {"amenity": ["a.b", "c"]}, {"a": 1}, {}, None, "amenity=hospital",
    {f"k{i}": "v" for i in range(9)}, {"amenity": []}, {"": "x"},
])
def test_tag_filters_cannot_inject_query_language(tags, served):
    calls = served()
    with pytest.raises(ValueError):
        fetch_osm_features(BBOX, tags)
    assert calls == []  # rejected before any request


@pytest.mark.parametrize("bbox", [
    None, [1, 2, 3], [51.4, 35.7, 51.3, 35.8], [51, 35, 52, 36], [51, 35, 51.2, 35.2, 9],
    [-181, 0, 0, 1], [0, 0, 0.1, 91], ["a", 1, 2, 3], {"minx": 51.3}, [51.0, 35.0, 51.6, 35.1],
])
def test_bbox_is_required_valid_and_bounded(bbox, served):
    calls = served()
    with pytest.raises(ValueError):
        fetch_osm_features(bbox, {"amenity": "hospital"})
    assert calls == []


def test_limits_truncation_and_arguments(served):
    served(elements=[{"type": "node", "id": i, "lat": 35.7, "lon": 51.3, "tags": {"a": "b"}}
                     for i in range(10)])
    out = fetch_osm_features(BBOX, {"a": "b"}, max_features=4)
    assert out.metadata["truncated"] is True and len(out.features) == 4
    served.config["max_features"] = 3
    assert len(fetch_osm_features(BBOX, {"a": "b"}, max_features=100).features) == 3
    for kwargs in ({"max_features": 0}, {"timeout": 0}, {"timeout": 500}, {"element_types": ["relation"]}):
        with pytest.raises(ValueError):
            fetch_osm_features(BBOX, {"a": "b"}, **kwargs)
    with pytest.raises(TypeError):  # the endpoint is config only
        fetch_osm_features(BBOX, {"a": "b"}, endpoint="http://169.254.169.254/")


def test_bad_responses_and_network_policy_errors(served, monkeypatch):
    served(body=b"<html>busy</html>")
    with pytest.raises(ValueError, match="valid JSON"):
        fetch_osm_features(BBOX, {"a": "b"})
    served(body=json.dumps({"nothing": 1}).encode())
    with pytest.raises(ValueError, match="Unexpected"):
        fetch_osm_features(BBOX, {"a": "b"})
    served(elements=[], remark="runtime error: Query timed out")
    with pytest.raises(ValueError, match="Overpass reported"):
        fetch_osm_features(BBOX, {"a": "b"})

    def refuse(url, **kwargs):
        raise NetworkPolicyError("url resolves to a disallowed network address")

    monkeypatch.setattr(module, "fetch_limited", refuse)
    with pytest.raises(ValueError, match="disallowed network address"):
        fetch_osm_features(BBOX, {"a": "b"})

    def explode(url, **kwargs):
        raise ConnectionError("failed https://user:secret@overpass.example/?key=SECRET")

    monkeypatch.setattr(module, "fetch_limited", explode)
    with pytest.raises(RuntimeError) as err:
        fetch_osm_features(BBOX, {"a": "b"})
    assert "secret" not in str(err.value).lower() and "ConnectionError" in str(err.value)


def test_build_query_shape():
    query = build_query((1.0, 2.0, 3.0, 4.0), [("amenity", ["cafe"])], ["node", "way"], 50, 25)
    assert query == ('[out:json][timeout:25];(node["amenity"="cafe"](2.0000000,1.0000000,4.0000000,3.0000000);'
                     'way["amenity"="cafe"](2.0000000,1.0000000,4.0000000,3.0000000););out geom 50;')
