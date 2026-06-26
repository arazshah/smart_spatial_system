import pytest

from smart_spatial_system.application.services.llm_intent_adapter import (
    LLMIntentAdapterError,
    apply_intent_to_query,
    is_llm_planning_enabled,
    plan_intent_with_llm,
)


def test_is_llm_planning_enabled_defaults_false(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PLANNING_ENABLED", raising=False)

    assert is_llm_planning_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
def test_is_llm_planning_enabled_truthy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("LLM_PLANNING_ENABLED", value)

    assert is_llm_planning_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_is_llm_planning_enabled_non_truthy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("LLM_PLANNING_ENABLED", value)

    assert is_llm_planning_enabled() is False


def test_apply_intent_to_query_returns_original_query_without_intent() -> None:
    query = "نمایش پوشش گیاهی"

    assert apply_intent_to_query(query, None) == query
    assert apply_intent_to_query(query, {}) == query


def test_apply_intent_to_query_rewrites_vegetation_extraction() -> None:
    result = apply_intent_to_query(
        "پوشش گیاهی را استخراج کن",
        {
            "intent_name": "vegetation_extraction",
            "parameters": {
                "threshold": 0.42,
                "vectorize": True,
            },
        },
    )

    assert "NDVI vegetation extraction." in result
    assert "greater than 0.42." in result
    assert "polygon vectorize" in result
    assert "original_query: پوشش گیاهی را استخراج کن" in result


def test_apply_intent_to_query_uses_default_threshold_on_invalid_value() -> None:
    result = apply_intent_to_query(
        "پوشش گیاهی را استخراج کن",
        {
            "intent_name": "vegetation_extraction",
            "parameters": {
                "threshold": "bad",
            },
        },
    )

    assert "greater than 0.3." in result


def test_apply_intent_to_query_rewrites_raster_vectorization() -> None:
    result = apply_intent_to_query(
        "پلیگون بساز",
        {
            "intent_name": "raster_vectorization",
        },
    )

    assert result.startswith("NDVI raster_to_vector polygon استخراج کن.")
    assert "original_query: پلیگون بساز" in result


def test_plan_intent_with_llm_delegates_to_orchestrator_planner(monkeypatch) -> None:
    calls = {}

    def fake_plan_intent_with_llm(*, query, available_capabilities):
        calls["query"] = query
        calls["available_capabilities"] = available_capabilities
        return {
            "intent": {
                "intent_name": "vegetation_extraction",
            }
        }

    monkeypatch.setattr(
        "orchestrator.llm_intent_planner.plan_intent_with_llm",
        fake_plan_intent_with_llm,
    )

    result = plan_intent_with_llm(
        query="ndvi",
        available_capabilities=["ndvi_processor"],
    )

    assert result["intent"]["intent_name"] == "vegetation_extraction"
    assert calls == {
        "query": "ndvi",
        "available_capabilities": ["ndvi_processor"],
    }


def test_plan_intent_with_llm_wraps_planner_errors(monkeypatch) -> None:
    from orchestrator.llm_intent_planner import LLMIntentPlannerError

    def fake_plan_intent_with_llm(*, query, available_capabilities):
        raise LLMIntentPlannerError("planner failed")

    monkeypatch.setattr(
        "orchestrator.llm_intent_planner.plan_intent_with_llm",
        fake_plan_intent_with_llm,
    )

    with pytest.raises(LLMIntentAdapterError, match="planner failed"):
        plan_intent_with_llm(
            query="ndvi",
            available_capabilities=["ndvi_processor"],
        )
