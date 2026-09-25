"""
Regression tests: s3geo.query() never told the planner what its input
layers are.

The LLM was given the question and (for vector layers only) the combined
extent, but not the layer names, whether each is a raster or a vector
layer, a raster's bands, or a vector layer's fields. It guessed entity refs
("sentinel2_image", "districts") and the plan passed validation, then failed
in DagExecutor with "Reference '$inputs.<guess>' could not be resolved" -
after planning, where the repair loop cannot help. Found in the Lake Urmia
case study (a 3-band reflectance raster + a sector polygon layer).

Now the layers are described in the system prompt, and a plan that reads a
layer that does not exist is rejected at generation time (and repaired).

Run:
    pytest tests/test_query_input_layer_facts.py -v
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

import s3geo
from orchestrator.planning.input_layers import describe_input_layers, render_input_layer_facts
from orchestrator.planning.llm_spec_generator import LLMSpecGenerationError

RASTER = {
    "data": np.ones((3, 4, 5)).tolist(),
    "metadata": {"transform": [20.0, 0.0, 500000.0, 0.0, -20.0, 4200000.0], "crs": "EPSG:32638",
                 "band_names": ["green", "nir", "swir16"]},
}
ZONES = gpd.GeoDataFrame({"sector_id": [1, 2], "name": ["a", "b"]},
                         geometry=[box(500000, 4199920, 500040, 4200000), box(500040, 4199920, 500100, 4200000)],
                         crs="EPSG:32638")


def _plan(raster_ref: str) -> dict:
    return {
        "raw_query": "mean green per sector",
        "goal": "zonal",
        "entities": [{"ref": raster_ref, "kind": "raster"}, {"ref": "sectors", "kind": "vector"}],
        "operations": [
            {"op": "zonal_statistics", "inputs": {"raster": raster_ref, "zones": "sectors"},
             "params": {"stats": ["mean"], "zone_id_field": "sector_id"}, "output": "z"},
        ],
        "outputs": [{"kind": "json", "source": "z", "config": {}}],
    }


class _Recorder:
    """LLM client double: returns the given responses in order, records the prompts."""

    def __init__(self, *responses: dict) -> None:
        self.responses = [json.dumps(r) for r in responses]
        self.messages: list[list[dict]] = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        return self.responses[min(len(self.messages), len(self.responses)) - 1]


def test_describe_raster_and_vector_layers():
    layers = describe_input_layers({"refl_2025": RASTER, "sectors": ZONES})
    raster, vector = layers
    assert raster.kind == "raster" and raster.bands == 3 and (raster.height, raster.width) == (4, 5)
    assert raster.band_names == ("green", "nir", "swir16") and raster.crs == "EPSG:32638"
    assert raster.pixel_size == (20.0, 20.0)
    assert vector.kind == "vector" and vector.feature_count == 2 and vector.crs == "EPSG:32638"
    assert dict(vector.fields) == {"sector_id": "int", "name": "str"}
    text = render_input_layer_facts(layers)
    assert '"refl_2025": raster, 3 band(s)' in text and "b2 = nir" in text
    assert '"sectors": vector, 2 feature(s) (Polygon)' in text and "sector_id (int)" in text


def test_describe_ndarray_raster():
    (layer,) = describe_input_layers({"r": {"data": np.zeros((6, 7), dtype="uint8"), "metadata": {}}})
    assert (layer.kind, layer.bands, layer.height, layer.width, layer.dtype) == ("raster", 1, 6, 7, "uint8")


def test_prompt_names_the_layers(monkeypatch):
    client = _Recorder(_plan("refl_2025"))
    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", lambda *a, **k: client)
    s3geo.query("mean green per sector", layers={"refl_2025": RASTER, "sectors": ZONES})
    system = client.messages[0][0]["content"]
    assert "Input layers available to this query" in system
    assert '"refl_2025": raster' in system and '"sectors": vector' in system


def test_unknown_layer_ref_is_repaired_before_execution(monkeypatch):
    client = _Recorder(_plan("sentinel2_image"), _plan("refl_2025"))
    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", lambda *a, **k: client)
    result = s3geo.query("mean green per sector", layers={"refl_2025": RASTER, "sectors": ZONES})
    assert result.attempt_count == 2 and result.repaired
    assert "sentinel2_image" in result.generation_attempts[0].error
    assert "neither an input layer" in result.generation_attempts[0].error
    means = {f["properties"]["zone_id"]: f["properties"]["zonal_mean"] for f in result.output.features}
    assert means == {1: 1.0, 2: 1.0}


def test_unknown_layer_ref_without_repair_raises_at_generation(monkeypatch):
    client = _Recorder(_plan("sentinel2_image"))
    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", lambda *a, **k: client)
    with pytest.raises(LLMSpecGenerationError, match="sentinel2_image"):
        s3geo.query("q", layers={"refl_2025": RASTER, "sectors": ZONES}, max_repair_attempts=0)


def test_raster_role_given_a_vector_layer_is_rejected(monkeypatch):
    client = _Recorder(_plan("sectors"))
    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", lambda *a, **k: client)
    with pytest.raises(LLMSpecGenerationError, match="is a vector layer"):
        s3geo.query("q", layers={"refl_2025": RASTER, "sectors": ZONES}, max_repair_attempts=0)
