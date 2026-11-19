"""QGIS plugin (qgis_plugin/s3geo_processing) in a real, headless QGIS.

Skipped unless PyQGIS is importable. Run it with the Python that has both QGIS and s3geo, e.g.
    QT_QPA_PLATFORM=offscreen ~/s3env/bin/python -m pytest tests/test_qgis_plugin_processing.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qgis_plugin"))
sys.path.append("/usr/share/qgis/python/plugins")  # the 'processing' module on Debian/Ubuntu

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingException,
    QgsProject,
    QgsVectorLayer,
)


def _points(name, rows, crs="EPSG:4326"):
    layer = QgsVectorLayer(f"Point?crs={crs}&field=name:string&field=value:double", name, "memory")
    feats = []
    for n, x, y, v in rows:
        f = QgsFeature(layer.fields())
        f.setAttributes([n, v])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


HOMES = [("home_A", 51.4105, 35.7570, 1.0), ("home_B", 51.4200, 35.7000, 2.0),
         ("home_C", 51.3300, 35.7800, 3.0)]
METRO = [("Vanak", 51.4108, 35.7578, 0.0), ("Enghelab", 51.4190, 35.7010, 0.0)]


def run(alg, params):
    import processing

    return processing.run(f"s3geo:{alg}", {**params, "OUTPUT": "memory:"})["OUTPUT"]


def test_provider_lists_all_algorithms(qgis_app):
    ids = {a.id() for a in QgsApplication.processingRegistry().providerById("s3geo").algorithms()}
    assert ids == {f"s3geo:{n}" for n in (
        "nearest_feature", "buffer_metres", "area_perimeter", "points_in_polygons", "hotspots",
        "dbscan_clusters", "run_capability", "ask_question")}


def test_nearest_feature_returns_metres(qgis_app):
    out = run("nearest_feature", {"INPUT": _points("homes", HOMES), "TARGET": _points("m", METRO),
                                  "K": 1, "MAX_DISTANCE": 0})
    dist = {f["name"]: f["_nearest_distance"] for f in out.getFeatures()}
    assert 80 < dist["home_A"] < 110 and 120 < dist["home_B"] < 170 and dist["home_C"] > 7000
    assert "Vanak" in next(out.getFeatures())["_target_properties"]


def test_nearest_feature_max_distance_drops_far_homes(qgis_app):
    out = run("nearest_feature", {"INPUT": _points("homes", HOMES), "TARGET": _points("m", METRO),
                                  "K": 1, "MAX_DISTANCE": 500})
    assert {f["name"] for f in out.getFeatures()} == {"home_A", "home_B"}


def test_buffer_is_metres_even_for_a_projected_input_layer(qgis_app):
    # same stations, but the layer is in UTM 39N: the plugin must still return a ~500 m buffer
    tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                QgsCoordinateReferenceSystem("EPSG:32639"), QgsProject.instance())
    rows = []
    for n, x, y, v in METRO:
        p = tr.transform(QgsPointXY(x, y))
        rows.append((n, p.x(), p.y(), v))
    out = run("buffer_metres", {"INPUT": _points("m_utm", rows, "EPSG:32639"), "DISTANCE": 500,
                                "DISSOLVE": False})
    assert out.crs().authid() == "EPSG:4326" and out.featureCount() == 2
    back = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                  QgsCoordinateReferenceSystem("EPSG:32639"), QgsProject.instance())
    geom = QgsGeometry(next(out.getFeatures()).geometry())
    geom.transform(back)
    assert 0.77e6 < geom.area() < 0.79e6


def test_area_perimeter(qgis_app):
    poly = QgsVectorLayer("Polygon?crs=EPSG:4326&field=id:integer", "p", "memory")
    f = QgsFeature(poly.fields())
    f.setGeometry(QgsGeometry.fromWkt("POLYGON((51.40 35.70,51.41 35.70,51.41 35.71,51.40 35.71,51.40 35.70))"))
    poly.dataProvider().addFeatures([f])
    out = run("area_perimeter", {"INPUT": poly})
    feat = next(out.getFeatures())
    # 0.01 deg at 35.7N is ~0.904 km x ~1.108 km (spherical estimate: 1.0007 km2, 4.02 km)
    assert 0.995e6 < feat["_area"] < 1.01e6 and 3950 < feat["_perimeter"] < 4100


def test_points_in_polygons(qgis_app):
    poly = QgsVectorLayer("Polygon?crs=EPSG:4326&field=id:integer", "p", "memory")
    f = QgsFeature(poly.fields())
    f.setGeometry(QgsGeometry.fromWkt("POLYGON((51.40 35.74,51.43 35.74,51.43 35.77,51.40 35.77,51.40 35.74))"))
    poly.dataProvider().addFeatures([f])
    out = run("points_in_polygons", {"POINTS": _points("h", HOMES), "POLYGONS": poly,
                                     "DROP_OUTSIDE": True})
    assert [x["name"] for x in out.getFeatures()] == ["home_A"]


def test_hotspots_and_clusters(qgis_app):
    rows = [(f"p{i}", 51.40 + (i % 5) * 0.001, 35.70 + (i // 5) * 0.001, float(i)) for i in range(20)]
    hot = run("hotspots", {"INPUT": _points("g", rows), "FIELD": "value", "K": 6})
    assert hot.featureCount() == 20 and len(hot.fields()) > 2
    clu = run("dbscan_clusters", {"INPUT": _points("g", rows), "EPS": 300, "MIN_SAMPLES": 3})
    assert clu.featureCount() == 20
    assert any("cluster" in n.lower() for n in clu.fields().names())


def test_run_capability_json_placeholders_and_errors(qgis_app):
    out = run("run_capability", {
        "CAPABILITY": "buffer_vector_features", "LAYER_A": _points("m", METRO), "METRIC": True,
        "PARAMETERS": '{"features": "@A", "distance": 300, "units": "meters"}'})
    assert out.featureCount() == 2
    with pytest.raises(QgsProcessingException, match="Unknown capability"):
        run("run_capability", {"CAPABILITY": "nope", "LAYER_A": _points("m", METRO),
                               "PARAMETERS": "{}", "METRIC": True})
    with pytest.raises(QgsProcessingException, match="not valid JSON"):
        run("run_capability", {"CAPABILITY": "buffer_vector_features", "LAYER_A": _points("m", METRO),
                               "PARAMETERS": "{oops", "METRIC": True})


def test_ask_question_with_a_fake_llm(qgis_app, monkeypatch):
    import s3geo
    from orchestrator.planning.llm_spec_generator import StaticLLMClient

    plan = {"raw_query": "buffer", "goal": "buffer_sites",
            "entities": [{"ref": "sites", "kind": "vector"}],
            "operations": [{"op": "buffer", "inputs": {"vector": "sites"},
                            "params": {"distance": 100, "engine": "python"}, "output": "buffered"}],
            "outputs": [{"kind": "vector_layer", "source": "buffered", "config": {}}]}
    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", lambda: StaticLLMClient(json.dumps(plan)))
    sites = _points("sites", HOMES)
    import processing

    out = processing.run("s3geo:ask_question", {"QUESTION": "buffer the sites", "LAYERS": [sites],
                                                "OUTPUT": "memory:"})["OUTPUT"]
    assert out.featureCount() == 3


def test_plugin_object_loads_and_unloads(qgis_app):
    from unittest.mock import MagicMock

    import s3geo_processing

    registry = QgsApplication.processingRegistry()
    before = registry.providerById("s3geo")
    registry.removeProvider(before)  # the fixture's provider (deleted); the plugin registers its own
    iface = MagicMock()
    iface.mainWindow.return_value = None
    plugin = s3geo_processing.classFactory(iface)
    plugin.initGui()
    assert registry.providerById("s3geo") is not None
    assert iface.addPluginToMenu.call_count == 2  # check-installation + open-chat
    iface.messageBar().pushMessage.assert_not_called()  # s3geo is installed here
    plugin.open_chat()  # what the 'S3Geo: open chat' menu entry does
    assert plugin.dock is not None and plugin.dock.windowTitle() == "S3Geo chat"
    iface.addDockWidget.assert_called_once()
    plugin.unload()
    iface.removeDockWidget.assert_called_once()
    assert plugin.dock is None
    assert registry.providerById("s3geo") is None
