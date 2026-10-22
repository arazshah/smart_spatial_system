"""stac_search: request building, parsing, filters and safety."""

from __future__ import annotations

import json

import pytest

import plugins.stac_search as module
from plugins.stac_search import search_stac
from smart_spatial_system.shared.net_policy import NetworkPolicyError

BBOX = [51.0, 35.0, 52.0, 36.0]


def item(i, cloud=10.0, collection="sentinel-2-l2a"):
    return {
        "type": "Feature", "id": f"S2_{i}", "collection": collection,
        "geometry": {"type": "Polygon", "coordinates": [[[51, 35], [52, 35], [52, 36], [51, 36], [51, 35]]]},
        "properties": {"datetime": f"2024-06-{i:02d}T07:00:00Z", "eo:cloud_cover": cloud, "platform": "sentinel-2a"},
        "assets": {"visual": {"href": f"https://cdn.example/{i}/visual.tif"},
                   "thumbnail": {"href": f"https://cdn.example/{i}/t.jpg"}, "B04": {"href": "https://x/B04.tif"}},
    }


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(module, "load_config", lambda _id: {"endpoint": "https://stac.example/search"})

    def install(items):
        def fake(url, **kwargs):
            calls.append((url, kwargs))
            n = kwargs["params"]["limit"]
            return json.dumps({"type": "FeatureCollection", "features": items[:n]}).encode(), \
                {"content_type": "application/geo+json", "status": 200}

        monkeypatch.setattr(module, "fetch_limited", fake)
        return calls

    return install


def test_search_request_and_result_shape(served):
    calls = served([item(i) for i in range(1, 6)])
    out = search_stac(BBOX, ["sentinel-2-l2a", "landsat-c2-l2"], "2024-06-01/2024-06-30", limit=3,
                      include_assets=["visual", "missing"])
    url, kwargs = calls[0]
    assert url == "https://stac.example/search"
    assert kwargs["params"] == {"bbox": "51.0,35.0,52.0,36.0", "collections": "sentinel-2-l2a,landsat-c2-l2",
                                "limit": 4, "datetime": "2024-06-01/2024-06-30"}
    assert kwargs["allow_private_network"] is False
    assert len(out.features) == 3 and out.metadata["has_more"] is True
    props = out.features[0]["properties"]
    assert props["id"] == "S2_1" and props["cloud_cover"] == 10.0 and props["platform"] == "sentinel-2a"
    assert props["assets"] == {"visual": "https://cdn.example/1/visual.tif"}  # only what was asked for
    assert props["asset_keys"] == ["B04", "thumbnail", "visual"]
    assert out.features[0]["geometry"]["type"] == "Polygon" and out.metadata["crs"] == "EPSG:4326"


def test_cloud_filter_keeps_unknown_cloud_and_no_more_pages(served):
    served([item(1, 5.0), item(2, 80.0), {**item(3), "properties": {"datetime": "2024-06-03T00:00:00Z"}}])
    out = search_stac(BBOX, "sentinel-2-l2a", max_cloud_cover=20, limit=10)
    assert [f["properties"]["id"] for f in out.features] == ["S2_1", "S2_3"]
    assert out.metadata["has_more"] is False


@pytest.mark.parametrize("kwargs", [
    {"bbox": None}, {"bbox": [1, 2, 3]}, {"bbox": [52, 35, 51, 36]}, {"bbox": [0, 0, 20, 20]},
    {"collections": []}, {"collections": ["a b"]}, {"collections": ["x/../y"]}, {"collections": "a" * 80},
    {"datetime": "yesterday"}, {"datetime": "2024-06-01/../x"}, {"datetime": 5},
    {"max_cloud_cover": 101}, {"max_cloud_cover": -1}, {"limit": 0}, {"limit": 500},
    {"include_assets": ["a b"]}, {"include_assets": [str(i) for i in range(21)]},
])
def test_validation_rejects_bad_input_before_any_request(kwargs, served):
    calls = served([item(1)])
    with pytest.raises(ValueError):
        search_stac(**{"bbox": BBOX, "collections": ["sentinel-2-l2a"], **kwargs})
    assert calls == []


def test_valid_datetime_forms(served):
    served([item(1)])
    for value in ("2024-06-01", "2024-06-01T00:00:00Z", "2024-06-01/2024-06-30", "../2024-06-30",
                  "2024-06-01T00:00:00Z/..", "2024-06-01T00:00:00+03:30/2024-06-30T00:00:00Z"):
        assert search_stac(BBOX, "sentinel-2-l2a", value).features


def test_server_and_policy_failures(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {})

    def respond(body):
        monkeypatch.setattr(module, "fetch_limited", lambda url, **kw: (body, {"content_type": "application/json"}))

    respond(b"<html>")
    with pytest.raises(ValueError, match="valid JSON"):
        search_stac(BBOX, "c")
    respond(json.dumps({"hello": 1}).encode())
    with pytest.raises(ValueError, match="Unexpected STAC"):
        search_stac(BBOX, "c")

    def refuse(url, **kw):
        raise NetworkPolicyError("url resolves to a disallowed network address")

    monkeypatch.setattr(module, "fetch_limited", refuse)
    with pytest.raises(ValueError, match="disallowed network address"):
        search_stac(BBOX, "c")

    def explode(url, **kw):
        raise ConnectionError("failed https://u:topsecret@stac.example/?token=X")

    monkeypatch.setattr(module, "fetch_limited", explode)
    with pytest.raises(RuntimeError) as err:
        search_stac(BBOX, "c")
    assert "topsecret" not in str(err.value) and "ConnectionError" in str(err.value)


def test_endpoint_cannot_be_chosen_by_the_caller(served):
    served([item(1)])
    with pytest.raises(TypeError):
        search_stac(BBOX, "c", endpoint="http://169.254.169.254/")
