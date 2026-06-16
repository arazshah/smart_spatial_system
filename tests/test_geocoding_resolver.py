"""
Tests for geocoding_resolver plugin.

Run:
    pytest tests/test_geocoding_resolver.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.geocoding_resolver import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _build_feature,
    _configured_precision,
    _get_path,
    _get_provider_config,
    _geometry_bbox,
    _normalize_provider_name,
    _parse_bbox,
    _provider_chain_from_config,
    _static_geocode,
    _to_float,
    _validate_limit,
    _validate_query,
    geocode_place,
    reverse_geocode_point,
)


@pytest.fixture
def geocoding_config(monkeypatch, tmp_path: Path) -> Path:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "geocoding_resolver.yaml").write_text(
        """
default_provider: static
default_provider_chain:
  - static
default_limit: 5
default_language: fa
default_timeout_seconds: 10
coordinate_precision: 6
continue_on_provider_error: true

providers:
  static:
    type: static
    places:
      tehran:
        display_name: Tehran, Iran
        lon: 51.389
        lat: 35.6892
        country: Iran
      isfahan:
        display_name: Isfahan, Iran
        lon: 51.6776
        lat: 32.6539
        country: Iran

  nominatim:
    type: nominatim
    base_url: https://nominatim.openstreetmap.org
    user_agent: GeoChatPlatformTests/1.0
    email: ""
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))
    return config_dir


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "geocoding_resolver"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Geocoding Resolver"
    assert "network" in PLUGIN.manifest.permissions


def test_validate_query() -> None:
    assert _validate_query(" Tehran ") == "Tehran"

    with pytest.raises(ValueError):
        _validate_query("")


def test_validate_limit() -> None:
    assert _validate_limit(1) == 1
    assert _validate_limit("5") == 5

    with pytest.raises(ValueError):
        _validate_limit(0)

    with pytest.raises(ValueError):
        _validate_limit(100)


def test_to_float() -> None:
    assert _to_float("51.389", "lon") == 51.389

    with pytest.raises(ValueError):
        _to_float("bad", "lon")


def test_configured_precision() -> None:
    assert _configured_precision({}) == 8
    assert _configured_precision({"coordinate_precision": None}) is None
    assert _configured_precision({"coordinate_precision": 4}) == 4


def test_normalize_provider_name() -> None:
    assert _normalize_provider_name(" static ") == "static"

    with pytest.raises(ValueError):
        _normalize_provider_name("")


def test_provider_chain_from_config() -> None:
    config = {
        "default_provider": "nominatim",
        "default_provider_chain": ["static", "nominatim"],
    }

    assert _provider_chain_from_config(
        config=config,
        provider=None,
        provider_chain=None,
    ) == ["static", "nominatim"]

    assert _provider_chain_from_config(
        config=config,
        provider="static",
        provider_chain=None,
    ) == ["static"]

    assert _provider_chain_from_config(
        config=config,
        provider=None,
        provider_chain=["a", "b"],
    ) == ["a", "b"]


def test_get_provider_config_explicit() -> None:
    config = {
        "providers": {
            "static": {
                "type": "static",
                "places": {},
            }
        }
    }

    provider_config = _get_provider_config(config, "static")

    assert provider_config["type"] == "static"
    assert provider_config["_provider_name"] == "static"


def test_get_provider_config_implicit_nominatim() -> None:
    provider_config = _get_provider_config({}, "nominatim")

    assert provider_config["type"] == "nominatim"
    assert "base_url" in provider_config


def test_get_path() -> None:
    obj = {"a": {"b": [{"c": 10}]}}
    assert _get_path(obj, "a.b.0.c") == 10
    assert _get_path(obj, "a.x", default="missing") == "missing"


def test_parse_bbox_nominatim() -> None:
    assert _parse_bbox(["35", "36", "51", "52"]) == [51.0, 35.0, 52.0, 36.0]
    assert _parse_bbox(["bad"]) is None


def test_build_feature() -> None:
    feature = _build_feature(
        lon=51.389,
        lat=35.6892,
        display_name="Tehran",
        provider="static",
        precision=4,
        properties={"x": 1},
    )

    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [51.389, 35.6892]
    assert feature["properties"]["display_name"] == "Tehran"
    assert feature["properties"]["provider"] == "static"
    assert feature["properties"]["x"] == 1


def test_geometry_bbox() -> None:
    geometry = {"type": "Point", "coordinates": [51.389, 35.6892]}
    assert _geometry_bbox(geometry) == [51.389, 35.6892, 51.389, 35.6892]


def test_static_geocode_direct() -> None:
    provider_config = {
        "type": "static",
        "places": {
            "tehran": {
                "display_name": "Tehran, Iran",
                "lon": 51.389,
                "lat": 35.6892,
            }
        },
    }

    features = _static_geocode(
        query="tehran",
        provider_name="static",
        provider_config=provider_config,
        limit=5,
        precision=6,
    )

    assert len(features) == 1
    assert features[0]["geometry"]["coordinates"] == [51.389, 35.6892]
    assert features[0]["properties"]["matched_key"] == "tehran"


def test_geocode_place_static_success(geocoding_config) -> None:
    result = geocode_place(query="tehran")

    assert len(result.features) == 1

    feature = result.features[0]
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [51.389, 35.6892]
    assert feature["properties"]["display_name"] == "Tehran, Iran"

    md = result.metadata
    assert md["source"] == "geocoding_resolver"
    assert md["operation"] == "geocode"
    assert md["query"] == "tehran"
    assert md["provider_used"] == "static"
    assert md["providers_tried"] == ["static"]
    assert md["result_count"] == 1
    assert md["feature_count"] == 1
    assert md["geometry_types"]["Point"] == 1


def test_geocode_place_static_no_result(geocoding_config) -> None:
    result = geocode_place(query="unknown place")

    assert result.features == []
    assert result.metadata["result_count"] == 0
    assert result.metadata["provider_used"] is None


def test_geocode_place_provider_override(geocoding_config) -> None:
    result = geocode_place(query="isfahan", provider="static", limit=1)

    assert len(result.features) == 1
    assert result.features[0]["properties"]["matched_key"] == "isfahan"
    assert result.metadata["limit"] == 1


def test_geocode_place_metadata_merge(geocoding_config) -> None:
    result = geocode_place(
        query="tehran",
        metadata={"request_id": "geo-1"},
    )

    assert result.metadata["request_id"] == "geo-1"


def test_geocode_place_rejects_invalid_metadata(geocoding_config) -> None:
    with pytest.raises(ValueError, match="metadata"):
        geocode_place(
            query="tehran",
            metadata="bad",
        )


def test_reverse_geocode_point_static_success(geocoding_config) -> None:
    result = reverse_geocode_point(
        lon=51.4,
        lat=35.7,
    )

    assert len(result.features) == 1

    feature = result.features[0]
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [51.4, 35.7]
    assert feature["properties"]["display_name"] == "Tehran, Iran"

    md = result.metadata
    assert md["operation"] == "reverse_geocode"
    assert md["provider_used"] == "static"
    assert md["result_count"] == 1


def test_reverse_geocode_rejects_invalid_lon_lat(geocoding_config) -> None:
    with pytest.raises(ValueError):
        reverse_geocode_point(lon=200, lat=35)

    with pytest.raises(ValueError):
        reverse_geocode_point(lon=51, lat=100)


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_geocode_place_nominatim_with_fake_http(monkeypatch, geocoding_config) -> None:
    calls = {}

    def fake_urlopen(request, timeout=10):
        calls["url"] = request.full_url
        calls["headers"] = dict(request.header_items())
        calls["timeout"] = timeout

        return FakeHTTPResponse([
            {
                "lat": "35.6892",
                "lon": "51.3890",
                "display_name": "Tehran, Iran",
                "osm_type": "relation",
                "osm_id": 123,
                "class": "place",
                "type": "city",
                "importance": 0.8,
                "boundingbox": ["35.5", "35.9", "51.1", "51.6"],
                "address": {"country": "Iran"},
            }
        ])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = geocode_place(
        query="Tehran",
        provider="nominatim",
        limit=1,
        language="en",
        country_codes="ir",
    )

    assert len(result.features) == 1
    assert result.features[0]["geometry"]["coordinates"] == [51.389, 35.6892]
    assert result.features[0]["properties"]["provider_type"] == "nominatim"
    assert result.features[0]["properties"]["bbox"] == [51.1, 35.5, 51.6, 35.9]

    parsed = urlparse(calls["url"])
    params = parse_qs(parsed.query)

    assert parsed.path.endswith("/search")
    assert params["q"] == ["Tehran"]
    assert params["countrycodes"] == ["ir"]
    assert params["accept-language"] == ["en"]
    assert calls["timeout"] == 10


def test_reverse_geocode_nominatim_with_fake_http(monkeypatch, geocoding_config) -> None:
    def fake_urlopen(request, timeout=10):
        return FakeHTTPResponse({
            "lat": "35.6892",
            "lon": "51.3890",
            "display_name": "Tehran, Iran",
            "osm_type": "relation",
            "osm_id": 123,
            "class": "place",
            "type": "city",
            "address": {"country": "Iran"},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = reverse_geocode_point(
        lon=51.389,
        lat=35.6892,
        provider="nominatim",
        language="en",
    )

    assert len(result.features) == 1
    assert result.features[0]["properties"]["provider_type"] == "nominatim"
    assert result.features[0]["properties"]["display_name"] == "Tehran, Iran"


def test_provider_chain_fallback_on_error(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "geocoding_resolver.yaml").write_text(
        """
default_provider_chain:
  - broken
  - static
continue_on_provider_error: true
default_limit: 5
coordinate_precision: 6
providers:
  broken:
    type: generic_http_json
    endpoint_url: https://broken.example/geocode
  static:
    type: static
    places:
      tehran:
        display_name: Tehran, Iran
        lon: 51.389
        lat: 35.6892
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    def fake_urlopen(request, timeout=10):
        raise RuntimeError("network failed")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = geocode_place(query="tehran")

    assert len(result.features) == 1
    assert result.metadata["provider_used"] == "static"
    assert result.metadata["providers_tried"] == ["broken", "static"]
    assert len(result.metadata["provider_errors"]) == 1


def test_generic_http_json_geocode(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "geocoding_resolver.yaml").write_text(
        """
default_provider: generic1
default_limit: 5
coordinate_precision: 6
providers:
  generic1:
    type: generic_http_json
    endpoint_url: https://example.com/geocode
    query_param: text
    limit_param: size
    results_path: results
    lon_path: location.lon
    lat_path: location.lat
    display_name_path: name
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    def fake_urlopen(request, timeout=10):
        return FakeHTTPResponse({
            "results": [
                {
                    "name": "Custom Place",
                    "location": {
                        "lon": 10.1,
                        "lat": 20.2,
                    },
                }
            ]
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = geocode_place(query="custom")

    assert len(result.features) == 1
    assert result.features[0]["geometry"]["coordinates"] == [10.1, 20.2]
    assert result.features[0]["properties"]["display_name"] == "Custom Place"
    assert result.features[0]["properties"]["provider_type"] == "generic_http_json"


def test_vectorout_to_artifact(geocoding_config) -> None:
    result = geocode_place(query="tehran")

    artifact = result.to_artifact(produced_by="test_geocoding_resolver")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_geocoding_resolver"
    assert artifact.payload["source"] == "geocoding_resolver"
    assert artifact.payload["operation"] == "geocode"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "geocode_place" in names
    assert "reverse_geocode_point" in names
    assert len(regs) >= 2


def test_geocode_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "geocode_place")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "geocode_place"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "query" in descriptor.required_inputs
    assert "provider" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["provider_based"] is True
    assert descriptor.metadata["operation"] == "geocode"


def test_reverse_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "reverse_geocode_point")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "reverse_geocode_point"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "lon" in descriptor.required_inputs
    assert "lat" in descriptor.required_inputs
    assert descriptor.metadata["operation"] == "reverse_geocode"
