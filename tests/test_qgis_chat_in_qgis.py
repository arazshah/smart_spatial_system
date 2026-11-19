"""The chat inside a real (headless) QGIS: layer adapter, main-thread marshalling, the full agent
pipeline on real project layers, and the dock widget. LLM = scripted; MCP server = real."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("qgis.core")
pytest.importorskip("mcp")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qgis_plugin"))

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from s3geo_processing.chat.agent import ChatAgent  # noqa: E402
from s3geo_processing.chat.config import ChatConfig  # noqa: E402
from s3geo_processing.chat.mcp_bridge import McpBridge, default_server_command  # noqa: E402
from s3geo_processing.chat.qgis_adapter import MainThread, QgisAdapter  # noqa: E402

HOMES = [("home_A", 51.4105, 35.7570, 1.0), ("home_B", 51.4200, 35.7000, 2.0),
         ("home_C", 51.3300, 35.7800, 3.0)]
METRO = [("Vanak", 51.4108, 35.7578, 0.0), ("Enghelab", 51.4190, 35.7010, 0.0)]


def _layer(name, rows):
    lyr = QgsVectorLayer("Point?crs=EPSG:4326&field=name:string&field=value:double", name, "memory")
    feats = []
    for n, x, y, v in rows:
        f = QgsFeature(lyr.fields())
        f.setAttributes([n, v])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


@pytest.fixture()
def project(qgis_app):
    QgsProject.instance().removeAllMapLayers()
    _layer("homes", HOMES)
    _layer("metro", METRO)
    yield QgsProject.instance()
    QgsProject.instance().removeAllMapLayers()


@pytest.fixture(scope="module")
def bridge(qgis_app):
    cmd, env = default_server_command()
    b = McpBridge(cmd, env)
    b.start()
    yield b
    b.stop()


def pump(cond, timeout=90.0):
    t0 = time.time()
    while not cond():
        QgsApplication.processEvents()
        time.sleep(0.01)
        assert time.time() - t0 < timeout, "timed out"


def call(i, name, **args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class Scripted:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def chat(self, messages, tools=None):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)


def test_adapter_reads_and_writes_layers_from_a_worker_thread(project):
    adapter = QgisAdapter(MainThread())
    out = {}

    def worker():
        out["layers"] = adapter.list_layers()
        out["info"] = adapter.layer_info("homes")
        out["fc"] = adapter.layer_geojson("metro")
        out["added"] = adapter.add_layer(out["fc"], "copy of metro/1")
        try:
            adapter.layer_geojson("nope")
        except KeyError as exc:
            out["err"] = str(exc)

    t = threading.Thread(target=worker)
    t.start()
    pump(lambda: not t.is_alive())
    assert {lyr["name"] for lyr in out["layers"]} == {"homes", "metro"}
    assert out["info"]["feature_count"] == 3 and out["info"]["sample_rows"][0]["name"] == "home_A"
    assert [f["properties"]["name"] for f in out["fc"]["features"]] == ["Vanak", "Enghelab"]
    assert out["added"] == "copy of metro/1" and project.mapLayersByName("copy of metro/1")
    assert "No vector layer named 'nope'" in out["err"] and "'homes'" in out["err"]


def test_full_pipeline_on_project_layers(project, bridge):
    llm = Scripted([
        {"role": "assistant", "content": None, "tool_calls": [call(1, "qgis_list_layers")]},
        {"role": "assistant", "content": None, "tool_calls": [call(
            2, "run_tool", tool="find_nearest_neighbors", metric=True, save_as="nearest_metro",
            arguments={"source_features": {"qgis_layer": "homes"},
                       "target_features": {"qgis_layer": "metro"}, "k": 1})]},
        {"role": "assistant", "content": "home_A is 93 m from Vanak."},
    ])
    agent = ChatAgent(llm, bridge, QgisAdapter(MainThread()))
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("answer", agent.run_turn("nearest metro?")))
    t.start()
    pump(lambda: not t.is_alive())
    assert out["answer"].startswith("home_A is 93 m")
    layer = project.mapLayersByName("nearest_metro")[0]
    dist = {f["name"]: f["_nearest_distance"] for f in layer.getFeatures()}
    assert 80 < dist["home_A"] < 110 and dist["home_C"] > 7000
    assert "coordinates" not in json.dumps(llm.seen[-1])  # geodata never reached the model


def test_dock_end_to_end(project, bridge, monkeypatch):
    from s3geo_processing.chat import dock as dock_mod

    llm = Scripted([
        {"role": "assistant", "content": None, "tool_calls": [call(
            1, "run_tool", tool="buffer_vector_features", metric=True, save_as="catchment",
            arguments={"features": {"qgis_layer": "metro"}, "distance": 500, "units": "meters"})]},
        {"role": "assistant", "content": "Added 500 m catchments."},
    ])
    monkeypatch.setattr(dock_mod, "OpenAICompatClient", lambda *a, **k: llm)
    monkeypatch.setattr(dock_mod.qt_settings, "notice_acknowledged", lambda: True)
    iface = MagicMock()
    iface.mainWindow.return_value = None
    dock = dock_mod.ChatDock(iface, parent=None)
    dock.cfg = ChatConfig(base_url="http://localhost:9/v1", model="fake")
    dock.bridge = None
    # reuse the module's running MCP server instead of starting a second one
    dock.agent = ChatAgent(llm, bridge, dock.qgis, max_steps=dock.cfg.max_steps,
                           on_event=lambda kind, **d: dock._event.emit(kind, d))
    dock.input.setPlainText("buffer the metro stations by 500 m")
    dock.send()
    assert dock.busy
    pump(lambda: not dock.busy)
    text = dock.view.toPlainText()
    assert "buffer the metro stations" in text and "Added 500 m catchments." in text
    assert "catchment" in text and "added (2 features)" in text
    assert project.mapLayersByName("catchment")[0].featureCount() == 2
    dock.new_chat()
    assert len(dock.agent.messages) == 1
    dock.bridge = None  # the shared bridge belongs to the fixture
    dock.shutdown()


def test_dock_reports_missing_settings(project, monkeypatch):
    from s3geo_processing.chat import dock as dock_mod

    monkeypatch.setattr(dock_mod.qt_settings, "notice_acknowledged", lambda: True)
    opened = []
    monkeypatch.setattr(dock_mod.ChatDock, "open_settings", lambda self: opened.append(1))
    iface = MagicMock()
    iface.mainWindow.return_value = None
    dock = dock_mod.ChatDock(iface, parent=None)
    dock.cfg = ChatConfig(base_url="", model="")
    dock.input.setPlainText("hello")
    dock.send()
    assert not dock.busy and opened and "base URL" in dock.view.toPlainText()
