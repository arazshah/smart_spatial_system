"""
Tests for config-aware wms_wfs_fetcher plugin.

Run:
    pytest tests/test_wms_wfs_fetcher.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.wms_wfs_fetcher import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _bbox_to_dict_or_none,
    _bbox_to_string,
    _build_wfs_params,
    _build_wms_params,
    _extension_from_image_format,
    _geometry_bbox,
    _get_layer_config,
    _get_service_config,
    _merge_bboxes,
    _validate_url,
    fetch_wfs_features,
    fetch_wms_map,
)


class FakeJsonResponse:
    def __init__(self, data, url="http://example.com/wfs"):
        self._data = data
        self.url = url
        self.headers = {"Content-Type": "application/json"}
        self.content = b""

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeBytesResponse:
    def __init__(self, content=b"FAKE_TIFF_BYTES", url="http://example.com/wms"):
        self.content = content
        self.url = url
        self.headers = {"Content-Type": "image/geotiff"}

    def raise_for_status(self):
        return None


def install_fake_requests_json(monkeypatch, data, call_store=None):
    fake_module = types.ModuleType("requests")

    def get(url, params=None, timeout=None):
        if call_store is not None:
            call_store["url"] = url
            call_store["params"] = params
            call_store["timeout"] = timeout
        return FakeJsonResponse(data=data, url=url)

    fake_module.get = get
    monkeypatch.setitem(sys.modules, "requests", fake_module)


def install_fake_requests_bytes(monkeypatch, content=b"FAKE_TIFF_BYTES", call_store=None):
    fake_module = types.ModuleType("requests")

    def get(url, params=None, timeout=None):
        if call_store is not None:
            call_store["url"] = url
            call_store["params"] = params
            call_store["timeout"] = timeout
        return FakeBytesResponse(content=content, url=url)

    fake_module.get = get
    monkeypatch.setitem(sys.modules, "requests", fake_module)


@pytest.fixture
def sample_wfs_geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
                "properties": {"id": 1, "name": "A"},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [51.0, 35.0],
                            [52.0, 35.0],
                            [52.0, 36.0],
                            [51.0, 36.0],
                            [51.0, 35.0],
                        ]
                    ],
                },
                "properties": {"id": 2, "name": "B"},
            },
        ],
    }


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "wms_wfs_fetcher"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "WMS/WFS Fetcher"
    assert "network" in PLUGIN.manifest.permissions
    assert "filesystem" in PLUGIN.manifest.permissions


def test_validate_url_success() -> None:
    assert _validate_url("https://example.com/geoserver/wfs") == "https://example.com/geoserver/wfs"
    assert _validate_url("http://localhost:8080/geoserver/wms") == "http://localhost:8080/geoserver/wms"


@pytest.mark.parametrize("url", ["", " ", "ftp://example.com", "example.com/wfs", "http:///bad"])
def test_validate_url_rejects_invalid(url: str) -> None:
    with pytest.raises(ValueError):
        _validate_url(url)


def test_bbox_to_string_from_list() -> None:
    assert _bbox_to_string([1, 2, 3, 4]) == "1.0,2.0,3.0,4.0"


def test_bbox_to_string_from_string() -> None:
    assert _bbox_to_string("1,2,3,4") == "1,2,3,4"


@pytest.mark.parametrize("bbox", [[1, 2, 3], "1,2,3", "a,b,c,d", None])
def test_bbox_to_string_rejects_invalid(bbox) -> None:
    with pytest.raises(ValueError):
        _bbox_to_string(bbox)


def test_bbox_to_dict_or_none() -> None:
    assert _bbox_to_dict_or_none([1, 2, 3, 4]) == {
        "minx": 1.0,
        "miny": 2.0,
        "maxx": 3.0,
        "maxy": 4.0,
    }
    assert _bbox_to_dict_or_none(None) is None


def test_geometry_bbox_point() -> None:
    geometry = {"type": "Point", "coordinates": [51.4, 35.7]}
    assert _geometry_bbox(geometry) == [51.4, 35.7, 51.4, 35.7]


def test_merge_bboxes() -> None:
    merged = _merge_bboxes([
        [51.4, 35.7, 51.4, 35.7],
        [51.0, 35.0, 52.0, 36.0],
    ])
    assert merged == {
        "minx": 51.0,
        "miny": 35.0,
        "maxx": 52.0,
        "maxy": 36.0,
    }


def test_get_service_config_default() -> None:
    config = {
        "default_service": "local",
        "services": {
            "local": {"wfs_url": "http://localhost/wfs"},
        },
    }

    name, svc = _get_service_config(config)

    assert name == "local"
    assert svc["wfs_url"] == "http://localhost/wfs"


def test_get_layer_config() -> None:
    service_config = {
        "layers": {
            "roads": {
                "type_name": "workspace:roads",
                "wms_layer": "workspace:roads",
            }
        }
    }

    layer_config = _get_layer_config(service_config, "roads")

    assert layer_config["type_name"] == "workspace:roads"
    assert layer_config["wms_layer"] == "workspace:roads"


def test_build_wfs_params_version_2() -> None:
    params = _build_wfs_params(
        type_name="workspace:roads",
        version="2.0.0",
        output_format="application/json",
        srs_name="EPSG:4326",
        bbox=[1, 2, 3, 4],
        max_features=10,
        property_name=None,
        extra_params=None,
    )

    assert params["service"] == "WFS"
    assert params["request"] == "GetFeature"
    assert params["typeNames"] == "workspace:roads"
    assert params["count"] == 10
    assert params["bbox"] == "1.0,2.0,3.0,4.0"


def test_build_wfs_params_version_1() -> None:
    params = _build_wfs_params(
        type_name="roads",
        version="1.1.0",
        output_format="application/json",
        srs_name="EPSG:4326",
        bbox=None,
        max_features=5,
        property_name="id,name",
        extra_params={"CQL_FILTER": "id > 0"},
    )

    assert params["typeName"] == "roads"
    assert params["maxFeatures"] == 5
    assert params["propertyName"] == "id,name"
    assert params["CQL_FILTER"] == "id > 0"


def test_build_wms_params_version_13() -> None:
    params = _build_wms_params(
        layers="workspace:roads",
        bbox=[1, 2, 3, 4],
        width=256,
        height=256,
        crs="EPSG:4326",
        version="1.3.0",
        styles="",
        image_format="image/png",
        transparent=True,
        extra_params=None,
    )

    assert params["service"] == "WMS"
    assert params["request"] == "GetMap"
    assert params["layers"] == "workspace:roads"
    assert params["crs"] == "EPSG:4326"
    assert "srs" not in params
    assert params["transparent"] == "TRUE"


def test_build_wms_params_version_111() -> None:
    params = _build_wms_params(
        layers="roads",
        bbox=[1, 2, 3, 4],
        width=256,
        height=256,
        crs="EPSG:4326",
        version="1.1.1",
        styles="",
        image_format="image/png",
        transparent=False,
        extra_params={"tiled": "true"},
    )

    assert params["srs"] == "EPSG:4326"
    assert "crs" not in params
    assert params["transparent"] == "FALSE"
    assert params["tiled"] == "true"


def test_extension_from_image_format() -> None:
    assert _extension_from_image_format("image/geotiff") == ".tif"
    assert _extension_from_image_format("image/png") == ".png"
    assert _extension_from_image_format("image/jpeg") == ".jpg"


def test_fetch_wfs_features_direct_success(monkeypatch, sample_wfs_geojson) -> None:
    call_store = {}
    install_fake_requests_json(monkeypatch, sample_wfs_geojson, call_store=call_store)

    result = fetch_wfs_features(
        base_url="https://example.com/geoserver/wfs",
        type_name="workspace:roads",
        bbox=[50, 35, 52, 36],
        max_features=100,
    )

    assert result is not None
    assert len(result.features) == 2
    assert result.features[0]["properties"]["name"] == "A"

    assert call_store["url"] == "https://example.com/geoserver/wfs"
    assert call_store["params"]["typeNames"] == "workspace:roads"

    md = result.metadata
    assert md["source"] == "wfs"
    assert md["loader"] == "wms_wfs_fetcher"
    assert md["type_name"] == "workspace:roads"
    assert md["feature_count"] == 2
    assert md["geometry_types"]["Point"] == 1
    assert md["geometry_types"]["Polygon"] == 1
    assert md["bounds"]["minx"] == pytest.approx(51.0)


def test_fetch_wfs_features_config_service_layer(monkeypatch, tmp_path: Path, sample_wfs_geojson) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    config_file = config_dir / "wms_wfs_fetcher.yaml"
    config_file.write_text(
        """
default_service: local
services:
  local:
    wfs_url: https://example.com/geoserver/wfs
    wms_url: https://example.com/geoserver/wms
    default_crs: EPSG:4326
    timeout: 12
    default_wfs_version: 2.0.0
    default_wfs_output_format: application/json
    default_max_features: 1
    layers:
      roads:
        type_name: workspace:roads
        wms_layer: workspace:roads
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    call_store = {}
    install_fake_requests_json(monkeypatch, sample_wfs_geojson, call_store=call_store)

    result = fetch_wfs_features(
        service="local",
        layer="roads",
        bbox=[50, 35, 52, 36],
    )

    assert len(result.features) == 1
    assert result.metadata["service"] == "local"
    assert result.metadata["layer_alias"] == "roads"
    assert result.metadata["type_name"] == "workspace:roads"
    assert result.metadata["max_features"] == 1
    assert call_store["timeout"] == 12


def test_fetch_wfs_features_rejects_missing_source(monkeypatch, tmp_path: Path) -> None:
    empty_config_dir = tmp_path / "config" / "plugins"
    empty_config_dir.mkdir(parents=True)
    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(empty_config_dir))

    with pytest.raises(ValueError, match="base_url"):
        fetch_wfs_features(type_name="roads")


def test_fetch_wfs_features_rejects_invalid_geojson(monkeypatch) -> None:
    install_fake_requests_json(monkeypatch, {"type": "Point", "coordinates": [1, 2]})

    with pytest.raises(ValueError, match="Unsupported WFS response"):
        fetch_wfs_features(
            base_url="https://example.com/geoserver/wfs",
            type_name="roads",
        )


def test_vectorout_to_artifact(monkeypatch, sample_wfs_geojson) -> None:
    install_fake_requests_json(monkeypatch, sample_wfs_geojson)

    result = fetch_wfs_features(
        base_url="https://example.com/geoserver/wfs",
        type_name="roads",
    )

    artifact = result.to_artifact(produced_by="test_wfs")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_wfs"
    assert len(artifact.payload["features"]) == 2
    assert artifact.payload["source"] == "wfs"


def test_fetch_wms_map_direct_success(monkeypatch, tmp_path: Path) -> None:
    call_store = {}
    install_fake_requests_bytes(monkeypatch, content=b"FAKE_TIFF_BYTES", call_store=call_store)

    output_path = tmp_path / "wms_result.tif"

    result = fetch_wms_map(
        base_url="https://example.com/geoserver/wms",
        layers="workspace:roads",
        bbox=[50, 35, 52, 36],
        output_path=str(output_path),
        width=512,
        height=256,
        image_format="image/geotiff",
    )

    assert result is not None
    assert Path(result.path).exists()
    assert Path(result.path).read_bytes() == b"FAKE_TIFF_BYTES"

    assert call_store["url"] == "https://example.com/geoserver/wms"
    assert call_store["params"]["layers"] == "workspace:roads"

    md = result.metadata
    assert md["source"] == "wms"
    assert md["loader"] == "wms_wfs_fetcher"
    assert md["layers"] == "workspace:roads"
    assert md["width"] == 512
    assert md["height"] == 256
    assert md["format"] == "image/geotiff"
    assert md["file_size_bytes"] > 0


def test_fetch_wms_map_config_service_layer(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "outputs"

    config_file = config_dir / "wms_wfs_fetcher.yaml"
    config_file.write_text(
        f"""
default_service: local
services:
  local:
    wfs_url: https://example.com/geoserver/wfs
    wms_url: https://example.com/geoserver/wms
    default_crs: EPSG:4326
    timeout: 15
    default_wms_version: 1.3.0
    default_wms_image_format: image/png
    default_width: 300
    default_height: 200
    output_dir: {str(output_dir)}
    layers:
      roads:
        type_name: workspace:roads
        wms_layer: workspace:roads
        style: default
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    call_store = {}
    install_fake_requests_bytes(monkeypatch, content=b"PNG_BYTES", call_store=call_store)

    result = fetch_wms_map(
        service="local",
        layer="roads",
        bbox=[50, 35, 52, 36],
    )

    assert Path(result.path).exists()
    assert Path(result.path).suffix == ".png"
    assert Path(result.path).read_bytes() == b"PNG_BYTES"

    md = result.metadata
    assert md["service"] == "local"
    assert md["layer_alias"] == "roads"
    assert md["layers"] == "workspace:roads"
    assert md["width"] == 300
    assert md["height"] == 200
    assert md["format"] == "image/png"
    assert md["styles"] == "default"
    assert call_store["timeout"] == 15


def test_fetch_wms_map_rejects_missing_source(monkeypatch, tmp_path: Path) -> None:
    empty_config_dir = tmp_path / "config" / "plugins"
    empty_config_dir.mkdir(parents=True)
    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(empty_config_dir))

    with pytest.raises(ValueError, match="base_url"):
        fetch_wms_map(
            layer="roads",
            bbox=[1, 2, 3, 4],
        )


def test_rasterout_to_artifact(monkeypatch, tmp_path: Path) -> None:
    install_fake_requests_bytes(monkeypatch, content=b"FAKE_TIFF_BYTES")

    output_path = tmp_path / "wms_result.tif"

    result = fetch_wms_map(
        base_url="https://example.com/geoserver/wms",
        layers="roads",
        bbox=[1, 2, 3, 4],
        output_path=str(output_path),
    )

    artifact = result.to_artifact(produced_by="test_wms")

    assert artifact.kind == "raster_ref"
    assert artifact.produced_by == "test_wms"
    assert artifact.payload["path"] == str(output_path.resolve())
    assert artifact.payload["source"] == "wms"


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "fetch_wfs_features" in names
    assert "fetch_wms_map" in names
    assert len(regs) >= 2


def test_wfs_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "fetch_wfs_features")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "fetch_wfs_features"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "service" in descriptor.optional_inputs
    assert "base_url" in descriptor.optional_inputs
    assert "type_name" in descriptor.optional_inputs
    assert "layer" in descriptor.optional_inputs
    assert "network" in descriptor.requires_permissions
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True


def test_wms_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "fetch_wms_map")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "fetch_wms_map"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "bbox" in descriptor.required_inputs
    assert "service" in descriptor.optional_inputs
    assert "base_url" in descriptor.optional_inputs
    assert "layer" in descriptor.optional_inputs
    assert "layers" in descriptor.optional_inputs
    assert "network" in descriptor.requires_permissions
    assert "filesystem" in descriptor.requires_permissions
    assert descriptor.metadata["artifact_kind"] == "raster_ref"
    assert descriptor.metadata["config_aware"] is True
