"""QGIS chat agent: scripted LLM + fake QGIS, but the REAL MCP server over stdio.

Plain Python (no QGIS needed): checks the tool loop, layer references, metric reprojection through
MCP, that geodata never reaches the LLM, error recovery and cancellation.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("mcp")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qgis_plugin"))

from s3geo_processing.chat.agent import ChatAgent, compact_schema, summarize_fc  # noqa: E402
from s3geo_processing.chat.config import ChatConfig  # noqa: E402
from s3geo_processing.chat.mcp_bridge import McpBridge, default_server_command  # noqa: E402


def _pt(lon, lat, **p):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": p}


LAYERS = {
    "homes": {"type": "FeatureCollection", "features": [
        _pt(51.4105, 35.7570, name="home_A", price=100), _pt(51.4200, 35.7000, name="home_B", price=200),
        _pt(51.3300, 35.7800, name="home_C", price=300)]},
    "metro": {"type": "FeatureCollection", "features": [
        _pt(51.4108, 35.7578, name="Vanak"), _pt(51.4190, 35.7010, name="Enghelab")]},
}


class FakeQgis:
    def __init__(self):
        self.added: dict[str, dict] = {}
        self.zoomed: list[str] = []

    def list_layers(self):
        return [{"name": n, "geometry": "Point", "crs": "EPSG:4326",
                 "feature_count": len(fc["features"])} for n, fc in LAYERS.items()]

    def layer_info(self, name):
        if name not in LAYERS:
            raise KeyError(f"No vector layer named {name!r}")
        return {"name": name, "fields": ["name"], "sample_rows": [LAYERS[name]["features"][0]["properties"]]}

    def layer_geojson(self, name, selected_only=False):
        if name not in LAYERS:
            raise KeyError(f"No vector layer named {name!r}")
        return LAYERS[name]

    def add_layer(self, fc, name):
        self.added[name] = fc
        return name

    def zoom_to(self, name):
        self.zoomed.append(name)


class ScriptedLLM:
    """Plays a fixed list of assistant messages; records what it was shown."""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)


def call(i, name, **args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def say(text):
    return {"role": "assistant", "content": text}


def use(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


@pytest.fixture(scope="module")
def bridge():
    cmd, env = default_server_command()
    b = McpBridge(cmd, env)
    b.start()
    yield b
    b.stop()


def make(bridge, script, **kw):
    qgis = FakeQgis()
    events = []
    llm = ScriptedLLM(script)
    agent = ChatAgent(llm, bridge, qgis, on_event=lambda k, **d: events.append((k, d)), **kw)
    return agent, llm, qgis, events


def test_bridge_lists_the_mcp_tools(bridge):
    names = {t["name"] for t in bridge.list_tools()}
    assert len(names) >= 80 and "find_nearest_neighbors" in names and "artifact_get" in names


def test_metric_nearest_chain_keeps_geodata_away_from_the_llm(bridge):
    agent, llm, qgis, events = make(bridge, [
        use(call(1, "qgis_list_layers")),
        use(call(2, "describe_tool", tools=["find_nearest_neighbors"])),
        use(call(3, "run_tool", tool="find_nearest_neighbors", metric=True, save_as="nearest",
                 arguments={"source_features": {"qgis_layer": "homes"},
                            "target_features": {"qgis_layer": "metro"}, "k": 1})),
        use(call(4, "run_tool", tool="buffer_vector_features", metric=True, save_as="catchment",
                 arguments={"features": {"result": "nearest"}, "distance": 100, "units": "meters"})),
        say("Done: 3 homes, buffers added."),
    ])
    assert agent.run_turn("nearest metro for every home, in metres") == "Done: 3 homes, buffers added."
    # 1) real metric result reached QGIS in EPSG:4326
    dist = {f["properties"]["name"]: f["properties"]["_nearest_distance"]
            for f in qgis.added["nearest"]["features"]}
    assert 80 < dist["home_A"] < 110 and 120 < dist["home_B"] < 170 and dist["home_C"] > 7000
    x, y = qgis.added["nearest"]["features"][0]["geometry"]["coordinates"]
    assert 51 < x < 52 and 35 < y < 36
    assert set(qgis.added) == {"nearest", "catchment"}
    # 2) the LLM never saw coordinates (summaries only) but did see numeric stats
    shown = json.dumps(llm.seen[-1])
    assert "coordinates" not in shown and "_nearest_distance" in shown
    # 3) it saw the catalog (all tools, one line each) but not 23k tokens of schemas
    system = llm.seen[0][0]["content"]
    assert "find_nearest_neighbors:" in system and len(system) < 20_000
    assert [k for k, _ in events].count("layer_added") == 2


def test_errors_go_back_to_the_model_which_can_recover(bridge):
    agent, llm, qgis, _ = make(bridge, [
        use(call(1, "run_tool", tool="buffer_vectors", arguments={})),             # unknown tool
        use(call(2, "run_tool", tool="buffer_vector_features",
                 arguments={"features": {"qgis_layer": "nope"}, "distance": 1})),   # unknown layer
        use(call(3, "run_tool", tool="buffer_vector_features",
                 arguments={"features": {"qgis_layer": "metro"}, "distance": 1, "bogus": 1})),
        say("I could not do that."),
    ])
    agent.run_turn("buffer")
    tool_msgs = [m["content"] for m in llm.seen[-1] if m["role"] == "tool"]
    assert "did_you_mean" in tool_msgs[0] and "buffer_vector_features" in tool_msgs[0]
    assert "nope" in tool_msgs[1]
    assert "Unknown argument" in tool_msgs[2]
    assert not qgis.added


def test_cancel_and_step_limit(bridge):
    cancel = threading.Event()
    cancel.set()
    agent, llm, *_ = make(bridge, [say("never used")])
    assert agent.run_turn("hi", cancel) == "Stopped." and llm.script  # model was not called

    loop = [use(call(i, "qgis_list_layers")) for i in range(5)]
    agent, *_ = make(bridge, loop, max_steps=3)
    assert "stopped after 3 steps" in agent.run_turn("loop forever")


def test_sample_rows_can_be_switched_off(bridge):
    agent, llm, *_ = make(bridge, [
        use(call(1, "run_tool", tool="buffer_vector_features", save_as="b",
                 arguments={"features": {"qgis_layer": "metro"}, "distance": 0.001, "units": "degrees"})),
        say("ok")], share_sample_rows=False)
    agent.run_turn("x")
    tool_out = [m["content"] for m in llm.seen[-1] if m["role"] == "tool"][0]
    assert "sample_rows" not in tool_out and '"feature_count": 2' in tool_out


def test_helpers_and_config():
    fc = LAYERS["homes"]
    s = summarize_fc(fc, sample_rows=True)
    assert s["feature_count"] == 3 and s["fields"]["price"] == {"min": 100, "max": 300, "mean": 200}
    tool = {"name": "t", "description": "Does x. More.", "input_schema": {
        "properties": {"features": {"description": "GeoJSON FeatureCollection ...", "anyOf": [
            {"type": "object", "properties": {"artifact_id": {"type": "string"}}}, {"type": "object"}]},
            "k": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}, "required": ["features"]}}
    c = compact_schema(tool)
    assert c["parameters"]["features"] == {"type": "layer", "required": True}
    assert c["parameters"]["k"]["type"] == "integer"
    cfg = ChatConfig(base_url="https://x/v1", model="m", api_key="")
    assert any("API key" in p for p in cfg.problems())
    assert ChatConfig(base_url="http://localhost:11434/v1", model="m").problems() == []
    assert ChatConfig.from_dict(cfg.to_dict()).model == "m"
