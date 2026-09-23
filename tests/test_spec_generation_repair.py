"""
enhancements/003 items 1 and 3: one repair attempt after a validator
rejects a plan, and a full audit record (plan + attempts) on the result
and on every error.

Run:
    pytest tests/test_spec_generation_repair.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import s3geo  # noqa: E402
from orchestrator.planning.llm_spec_generator import (  # noqa: E402
    LLMQuerySpecGenerator,
    LLMSpecGenerationError,
    SpecGenerationAttempt,
)

AREAS = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"id": 1}, "geometry": {"type": "Point", "coordinates": [28.95, 41.00]}},
        {"type": "Feature", "properties": {"id": 2}, "geometry": {"type": "Point", "coordinates": [29.20, 41.15]}},
    ],
}
CLINICS = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"id": 9}, "geometry": {"type": "Point", "coordinates": [28.96, 41.00]}},
    ],
}


def _plan(nn_params: dict) -> dict:
    crs = "EPSG:32635"
    return {
        "goal": "underserved_areas",
        "entities": [{"ref": "areas", "kind": "vector"}, {"ref": "clinics", "kind": "vector"}],
        "operations": [
            {"op": "crs_transform", "inputs": {"vector": "areas"},
             "params": {"source_crs": "EPSG:4326", "target_crs": crs}, "output": "areas_m"},
            {"op": "crs_transform", "inputs": {"vector": "clinics"},
             "params": {"source_crs": "EPSG:4326", "target_crs": crs}, "output": "clinics_m"},
            {"op": "spatial_nearest", "inputs": {"source": "areas_m", "target": "clinics_m"},
             "params": {"k": 1, "source_crs": crs, "target_crs": crs, **nn_params}, "output": "nearest"},
            {"op": "filter_attribute", "inputs": {"vector": "nearest"},
             "params": {"where": {"field": "_nearest_distance", "op": "gt", "value": 1000}},
             "output": "underserved"},
        ],
        "outputs": [{"kind": "vector_layer", "source": "underserved"}],
    }


BAD = _plan({"max_distance_m": 1000})  # provably empty - rejected
GOOD = _plan({})


class ScriptedLLM:
    """Returns the scripted responses in order and records every call."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return self.responses[len(self.calls) - 1]


# --------------------------------------------------------------------- #
# LLMQuerySpecGenerator
# --------------------------------------------------------------------- #


def test_default_generator_does_not_repair():
    llm = ScriptedLLM(json.dumps(BAD), json.dumps(GOOD))

    with pytest.raises(LLMSpecGenerationError) as excinfo:
        LLMQuerySpecGenerator(llm).generate("find underserved areas")

    assert len(llm.calls) == 1
    assert excinfo.value.plan == BAD
    assert len(excinfo.value.attempts) == 1


def test_rejected_plan_is_repaired_once_with_the_validator_message():
    llm = ScriptedLLM(json.dumps(BAD), json.dumps(GOOD))
    generator = LLMQuerySpecGenerator(llm, max_repair_attempts=1)

    spec = generator.generate("find underserved areas")

    assert [op.op for op in spec.operations][-1] == "filter_attribute"
    assert len(llm.calls) == 2
    repair_messages = llm.calls[1]
    # Original conversation, then the rejected plan, then the repair ask.
    assert repair_messages[:2] == llm.calls[0]
    assert repair_messages[2] == {"role": "assistant", "content": json.dumps(BAD)}
    assert repair_messages[3]["role"] == "user"
    assert "Your previous plan was rejected" in repair_messages[3]["content"]
    assert "always returns zero features" in repair_messages[3]["content"]

    first, second = generator.last_attempts
    assert first == SpecGenerationAttempt(1, BAD, json.dumps(BAD), first.error)
    assert "always returns zero features" in first.error
    assert second.number == 2 and second.error is None and second.plan == GOOD
    assert generator.last_plan == GOOD


def test_second_rejection_raises_the_second_error_with_both_attempts():
    still_bad = _plan({"max_distance": 1000.0})
    llm = ScriptedLLM(json.dumps(BAD), json.dumps(still_bad))

    with pytest.raises(LLMSpecGenerationError) as excinfo:
        LLMQuerySpecGenerator(llm, max_repair_attempts=1).generate("find underserved areas")

    error = excinfo.value
    assert "max_distance=1000.0" in str(error)
    assert error.plan == still_bad
    assert error.raw_response == json.dumps(still_bad)
    assert [a.number for a in error.attempts] == [1, 2]
    assert all(a.error for a in error.attempts)
    assert "max_distance_m=1000" in error.attempts[0].error


def test_unparseable_response_is_not_repaired():
    llm = ScriptedLLM("sorry, I can't help with that", json.dumps(GOOD))

    with pytest.raises(LLMSpecGenerationError) as excinfo:
        LLMQuerySpecGenerator(llm, max_repair_attempts=3).generate("q")

    assert len(llm.calls) == 1
    assert excinfo.value.plan is None
    assert excinfo.value.raw_response == "sorry, I can't help with that"
    assert len(excinfo.value.attempts) == 1


def test_llm_transport_errors_are_not_repaired():
    class Failing:
        calls = 0

        def complete(self, messages, **kwargs):
            Failing.calls += 1
            raise LLMSpecGenerationError("LLM HTTP error 401: unauthorized")

    with pytest.raises(LLMSpecGenerationError, match="401"):
        LLMQuerySpecGenerator(Failing(), max_repair_attempts=2).generate("q")
    assert Failing.calls == 1


@pytest.mark.parametrize("bad", [-1, 1.5, True, "1"])
def test_max_repair_attempts_must_be_a_non_negative_int(bad):
    with pytest.raises(ValueError):
        LLMQuerySpecGenerator(ScriptedLLM(), max_repair_attempts=bad)


# --------------------------------------------------------------------- #
# s3geo.query()
# --------------------------------------------------------------------- #


def _patch_llm(monkeypatch, llm: ScriptedLLM) -> None:
    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", lambda *a, **k: llm)


def test_query_repairs_by_default_and_records_every_attempt(monkeypatch):
    pytest.importorskip("pyproj", reason="pyproj not installed")
    llm = ScriptedLLM(json.dumps(BAD), json.dumps(GOOD))
    _patch_llm(monkeypatch, llm)

    result = s3geo.query("find underserved areas", layers={"areas": AREAS, "clinics": CLINICS})

    assert result.attempt_count == 2
    assert result.repaired is True
    assert result.generation_attempts[0].plan == BAD
    assert "always returns zero features" in result.generation_attempts[0].error
    assert result.plan == GOOD
    # The executed spec, params and all.
    filter_op = result.query_spec["operations"][-1]
    assert filter_op["params"]["where"] == {"field": "_nearest_distance", "op": "gt", "value": 1000}
    assert "max_distance_m" not in result.query_spec["operations"][2]["params"]
    # Area 2 is ~22 km from the only clinic; area 1 is ~840 m.
    assert [f["properties"]["id"] for f in result.output.features] == [2]


def test_query_first_attempt_success_is_distinguishable(monkeypatch):
    pytest.importorskip("pyproj", reason="pyproj not installed")
    _patch_llm(monkeypatch, ScriptedLLM(json.dumps(GOOD)))

    result = s3geo.query("find underserved areas", layers={"areas": AREAS, "clinics": CLINICS})

    assert result.attempt_count == 1
    assert result.repaired is False
    assert result.generation_attempts[0].error is None


def test_query_max_repair_attempts_zero_disables_repair(monkeypatch):
    llm = ScriptedLLM(json.dumps(BAD), json.dumps(GOOD))
    _patch_llm(monkeypatch, llm)

    with pytest.raises(LLMSpecGenerationError) as excinfo:
        s3geo.query(
            "find underserved areas",
            layers={"areas": AREAS, "clinics": CLINICS},
            max_repair_attempts=0,
        )

    assert len(llm.calls) == 1
    assert excinfo.value.plan == BAD


def test_execution_failure_carries_the_plan(monkeypatch):
    # Resolves at generation (valid CRS) but the buffer input ref is a
    # layer that was never passed, so execution fails.
    plan = {
        "goal": "buffer",
        "entities": [{"ref": "missing_layer", "kind": "vector"}],
        "operations": [
            {"op": "buffer", "inputs": {"vector": "missing_layer"},
             "params": {"distance": 100, "engine": "python"}, "output": "buffered"}
        ],
        "outputs": [{"kind": "vector_layer", "source": "buffered"}],
    }
    _patch_llm(monkeypatch, ScriptedLLM(json.dumps(plan)))

    with pytest.raises(RuntimeError) as excinfo:
        s3geo.query("buffer it", layers={"areas": AREAS})

    error = excinfo.value
    assert isinstance(error, s3geo.S3GeoExecutionError)
    assert error.plan == plan
    assert error.query_spec["operations"][0]["op"] == "buffer"
    assert len(error.generation_attempts) == 1
