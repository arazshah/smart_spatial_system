import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json

import pytest

from orchestrator.planning.llm_spec_generator import (
    LLMQuerySpecGenerator,
    LLMSpecGenerationError,
    StaticLLMClient,
    build_llm_messages,
    extract_json_object,
    query_spec_from_dict,
)
from orchestrator.planning.planner import DeterministicPlanner


def test_extract_json_object_from_plain_json():
    data = extract_json_object('{"goal": "x", "operations": [{"op": "rank_features"}]}')
    assert data["goal"] == "x"


def test_extract_json_object_from_fenced_json():
    text = """

{
  "goal": "rank",
  "operations": [
    {
      "op": "rank_features",
      "inputs": {"vector": "properties"},
      "params": {},
      "output": "ranked"
    }
  ]
}

"""
    data = extract_json_object(text)
    assert data["goal"] == "rank"
    assert data["operations"][0]["op"] == "rank_features"


def test_query_spec_from_dict_converts_valid_json():
    data = {
        "raw_query": "املاک را رتبه‌بندی کن",
        "goal": "rank_properties",
        "entities": [
            {"ref": "properties", "kind": "vector"}
        ],
        "operations": [
            {
                "op": "rank_features",
                "inputs": {"vector": "properties"},
                "params": {"score_field": "score"},
                "output": "ranked",
            }
        ],
        "outputs": [
            {"kind": "vector", "source": "ranked"}
        ],
    }

    spec = query_spec_from_dict(data)

    assert spec.goal == "rank_properties"
    assert spec.entities[0].ref == "properties"
    assert spec.operations[0].op == "rank_features"
    assert spec.outputs[0].source == "ranked"
    assert spec.source == "llm"


def test_query_spec_from_dict_rejects_empty_operations():
    with pytest.raises(LLMSpecGenerationError):
        query_spec_from_dict(
            {
                "goal": "bad",
                "operations": [],
            }
        )


def test_llm_query_spec_generator_with_static_client_and_planner():
    llm_json = {
        "raw_query": "املاک نزدیک مترو را امتیاز بده و رتبه‌بندی کن",
        "goal": "rank_real_estate",
        "entities": [
            {"ref": "properties", "kind": "vector"},
            {"ref": "poi", "kind": "vector"},
            {"ref": "buildable_zone", "kind": "vector"}
        ],
        "operations": [
            {
                "op": "filter_by_distance",
                "inputs": {
                    "vector": "properties",
                    "reference": "poi"
                },
                "params": {
                    "max_distance_m": 500,
                    "k": 1,
                    "drop_unmatched": True
                },
                "output": "near_poi"
            },
            {
                "op": "filter_points_in_polygon",
                "inputs": {
                    "vector": "near_poi",
                    "polygon": "buildable_zone"
                },
                "params": {
                    "predicate": "within",
                    "drop_outside": True
                },
                "output": "buildable_near_poi"
            },
            {
                "op": "score_features",
                "inputs": {
                    "vector": "buildable_near_poi"
                },
                "params": {
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near_poi",
                                "field": "distance",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 0.5
                            },
                            {
                                "name": "buildable",
                                "field": "in_polygon",
                                "type": "boolean",
                                "weight": 0.5
                            }
                        ]
                    }
                },
                "output": "scored"
            },
            {
                "op": "rank_features",
                "inputs": {
                    "vector": "scored"
                },
                "params": {
                    "score_field": "investment_score",
                    "rank_field": "investment_rank"
                },
                "output": "ranked"
            }
        ],
        "outputs": [
            {
                "kind": "report",
                "source": "ranked",
                "format": "pdf",
                "config": {
                    "title": "گزارش رتبه‌بندی املاک"
                }
            }
        ],
        "metadata": {
            "language": "fa"
        }
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate("املاک نزدیک مترو را امتیاز بده و رتبه‌بندی کن")

    assert spec.goal == "rank_real_estate"

    # Phase 10D: build_report + render_pdf are auto-injected
    # because output kind="report" format="pdf"
    op_names = [op.op for op in spec.operations]
    assert "filter_by_distance" in op_names
    assert "filter_points_in_polygon" in op_names
    assert "score_features" in op_names
    assert "rank_features" in op_names
    assert "build_report" in op_names
    assert "render_pdf" in op_names
    assert len(spec.operations) == 6
    assert spec.operations[0].op == "filter_by_distance"
    assert spec.operations[2].params["scoring_spec"]["output_field"] == "investment_score"
    assert spec.outputs[0].kind == "report"
    assert spec.outputs[0].format == "pdf"

    plan = DeterministicPlanner().build(spec)

    # Phase 10D: build_report + render_pdf auto-injected
    cap_names = [node.capability_name for node in plan.nodes]
    assert cap_names[:4] == [
        "find_nearest_neighbors",
        "filter_points_in_polygon",
        "score_features",
        "rank_features",
    ]
    assert "build_report" in cap_names
    assert "render_pdf" in cap_names


def test_build_llm_messages_contains_supported_ops_and_query():
    messages = build_llm_messages(
        "املاک داخل محدوده مجاز را پیدا کن",
        context={"available_entities": ["properties", "buildable_zone"]},
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Supported operations" in messages[0]["content"]

    assert messages[1]["role"] == "user"
    assert "املاک داخل محدوده مجاز" in messages[1]["content"]




def test_llm_generator_normalizes_unsupported_score_features_input_role():
    """
    Real LLMs may incorrectly attach risk_api/external_api directly to score_features.
    Guardrail should remove unsupported input roles and keep the spec plannable.
    """
    llm_json = {
        "raw_query": "املاک را با ریسک کم امتیاز بده",
        "goal": "rank_real_estate",
        "entities": [
            {"ref": "properties", "kind": "vector"},
            {"ref": "risk_api", "kind": "external_api"}
        ],
        "operations": [
            {
                "op": "score_features",
                "inputs": {
                    "vector": "properties",
                    "external_api": "risk_api"
                },
                # Explicit scoring_spec: this test is about the
                # unsupported-input-role guardrail, not about what happens
                # when scoring_spec is missing (see
                # test_llm_generator_raises_when_scoring_spec_and_factors_are_missing
                # for that).
                "params": {
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near_poi",
                                "field": "distance_to_poi",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 1.0,
                            }
                        ],
                    }
                },
                "output": "scored_properties"
            },
            {
                "op": "rank_features",
                "inputs": {
                    "vector": "scored_properties"
                },
                "params": {},
                "output": "ranked_properties"
            }
        ],
        "outputs": [
            {
                "kind": "report",
                "source": "ranked_properties",
                "format": "pdf",
                "config": {}
            }
        ],
        "metadata": {
            "language": "fa"
        }
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate("املاک را با ریسک کم امتیاز بده")

    score_op = spec.operations[0]
    rank_op = spec.operations[1]

    assert score_op.op == "score_features"
    assert score_op.inputs == {"vector": "properties"}
    assert "scoring_spec" in score_op.params
    assert score_op.params["scoring_spec"]["output_field"] == "investment_score"

    assert rank_op.params["score_field"] == "investment_score"

    assert spec.metadata["normalization"]["applied"] is True
    assert any(
        "removed unsupported input role" in item
        for item in spec.metadata["normalization"]["repairs"]
    )

    plan = DeterministicPlanner().build(spec)

    # Phase 10D: build_report + render_pdf auto-injected for pdf output
    capability_names = [node.capability_name for node in plan.nodes]
    assert capability_names[0] == "score_features"
    assert capability_names[1] == "rank_features"
    assert "build_report" in capability_names
    assert "render_pdf" in capability_names


def test_llm_generator_raises_when_scoring_spec_and_factors_are_missing():
    """
    score_features with neither scoring_spec nor factors must fail loudly,
    not fall back to a hardcoded real-estate default.

    This normalizer used to inject a fixed real-estate scoring_spec
    (output_field="investment_score", factors referencing
    inside_buildable_zone/flood_risk/etc.) into ANY score_features op that
    omitted one - silently corrupting any non-real-estate query's output
    (a nonsense score column, or a crash downstream when those fields
    don't exist on the data) with no way to tell it apart from a real
    real-estate query's own valid output. There is no domain-neutral
    default to substitute, so this must now raise instead.
    """
    llm_json = {
        "raw_query": "sites را امتیاز بده",
        "goal": "score_sites",
        "entities": [
            {"ref": "sites", "kind": "vector"}
        ],
        "operations": [
            {
                "op": "score_features",
                "inputs": {
                    "vector": "sites"
                },
                "params": {},
                "output": "scored"
            }
        ],
        "outputs": [
            {
                "kind": "vector",
                "source": "scored"
            }
        ]
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    with pytest.raises(LLMSpecGenerationError, match="scoring_spec"):
        generator.generate("sites را امتیاز بده")


def test_llm_generator_raises_even_when_distance_and_polygon_ops_precede_scoring():
    """
    Same missing-scoring_spec case as
    test_llm_generator_raises_when_scoring_spec_and_factors_are_missing,
    but with filter_by_distance/filter_points_in_polygon operations ahead
    of score_features in the plan.

    This used to be the trigger for injecting real-estate-specific
    enrichment nodes (distance_to_poi, inside_buildable_zone,
    distance_to_road) ahead of the equally real-estate-specific default
    scoring_spec - i.e. two compounding assumptions about the query's
    domain, for a query that might not be about real estate at all. Both
    are gone now: this must raise before any of that injection happens,
    for any domain.
    """
    llm_json = {
        "raw_query": "sites نزدیک مترو و خیابان اصلی را امتیاز بده",
        "goal": "rank_sites",
        "entities": [
            {"ref": "sites", "kind": "vector"},
            {"ref": "poi", "kind": "vector"},
            {"ref": "buildable_zone", "kind": "vector"},
            {"ref": "roads", "kind": "vector"},
        ],
        "operations": [
            {
                "op": "filter_by_distance",
                "inputs": {"vector": "sites", "reference": "poi"},
                "params": {"max_distance_m": 500, "k": 1, "drop_unmatched": True},
                "output": "near_sites",
            },
            {
                "op": "filter_points_in_polygon",
                "inputs": {"vector": "near_sites", "polygon": "buildable_zone"},
                "params": {"predicate": "within", "drop_outside": True},
                "output": "buildable_sites",
            },
            {
                "op": "filter_by_distance",
                "inputs": {"vector": "buildable_sites", "reference": "roads"},
                "params": {"max_distance_m": 500, "k": 1, "drop_unmatched": True},
                "output": "final_sites",
            },
            {
                "op": "score_features",
                "inputs": {"vector": "final_sites"},
                "params": {},
                "output": "scored_sites",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "scored_sites"},
                "params": {},
                "output": "ranked_sites",
            },
        ],
        "outputs": [
            {"kind": "report", "source": "ranked_sites", "format": "pdf"}
        ],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    with pytest.raises(LLMSpecGenerationError, match="scoring_spec"):
        generator.generate("sites نزدیک مترو و خیابان اصلی را امتیاز بده")


def test_llm_normalizer_does_not_inject_enrichment_when_scoring_spec_is_explicit():
    """
    If LLM provides explicit scoring_spec, keep its operation chain stable.
    """
    llm_json = {
        "raw_query": "املاک را با scoring مشخص امتیاز بده",
        "goal": "score_properties",
        "entities": [
            {"ref": "properties", "kind": "vector"},
            {"ref": "poi", "kind": "vector"},
        ],
        "operations": [
            {
                "op": "filter_by_distance",
                "inputs": {"vector": "properties", "reference": "poi"},
                "params": {"max_distance_m": 500, "k": 1},
                "output": "near_properties",
            },
            {
                "op": "score_features",
                "inputs": {"vector": "near_properties"},
                "params": {
                    "scoring_spec": {
                        "output_field": "custom_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near",
                                "field": "distance",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 1,
                            }
                        ],
                    }
                },
                "output": "scored",
            },
        ],
        "outputs": [
            {"kind": "vector", "source": "scored"}
        ],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate("املاک را با scoring مشخص امتیاز بده")

    assert [op.op for op in spec.operations] == [
        "filter_by_distance",
        "score_features",
    ]

    plan = DeterministicPlanner().build(spec)
    assert [node.capability_name for node in plan.nodes] == [
        "find_nearest_neighbors",
        "score_features",
    ]


def test_llm_normalizer_removes_invalid_empty_enrichment_node_and_rewrites_refs():
    """
    Real LLM may generate enrich_feature_properties without rules.
    This node is not executable and must be removed safely.
    """
    llm_json = {
        "raw_query": "املاک را امتیاز بده",
        "goal": "rank_real_estate",
        "entities": [
            {"ref": "properties", "kind": "vector"},
            {"ref": "poi", "kind": "vector"},
        ],
        "operations": [
            {
                "op": "filter_by_distance",
                "inputs": {"vector": "properties", "reference": "poi"},
                "params": {"max_distance_m": 500, "k": 1, "drop_unmatched": True},
                "output": "near_properties",
            },
            {
                "op": "enrich_feature_properties",
                "inputs": {"vector": "near_properties"},
                "params": {},
                "output": "enriched_properties",
            },
            {
                "op": "score_features",
                "inputs": {"vector": "enriched_properties"},
                # Explicit scoring_spec: this test is about removing the
                # invalid empty enrich_feature_properties node, not about
                # missing scoring_spec (see
                # test_llm_generator_raises_when_scoring_spec_and_factors_are_missing
                # for that case).
                "params": {
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near_poi",
                                "field": "distance_to_poi",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 1.0,
                            }
                        ],
                    }
                },
                "output": "scored_properties",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "scored_properties"},
                "params": {},
                "output": "ranked_properties",
            },
        ],
        "outputs": [
            {"kind": "vector", "source": "ranked_properties"}
        ],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate("املاک را امتیاز بده")

    op_names = [op.op for op in spec.operations]

    # With an explicit scoring_spec (see above), nothing auto-injects a
    # second, valid enrich_feature_properties node here - the LLM's own
    # invalid one (no rules) is the only one in the spec, so removing it
    # leaves none at all, rather than leaving a differently-sourced one
    # behind.
    assert "enrich_feature_properties" not in op_names

    # score_features must no longer depend on removed enriched_properties.
    score_op = next(op for op in spec.operations if op.op == "score_features")
    assert score_op.inputs["vector"] != "enriched_properties"
    assert score_op.inputs["vector"] == "near_properties"

    plan = DeterministicPlanner().build(spec)

    assert all(
        node.capability_name != "enrich_feature_properties" for node in plan.nodes
    )

    assert spec.metadata["normalization"]["applied"] is True
    assert any(
        "removed invalid enrich_feature_properties without rules" in item
        for item in spec.metadata["normalization"]["repairs"]
    )


def test_normalizer_auto_injects_enrich_risk_when_scoring_uses_risk_fields():
    """
    When LLM explicitly provides scoring_spec with risk fields (flood_risk,
    earthquake_risk, fire_risk) but no enrich_risk node exists,
    normalizer must inject enrich_risk before score_features.

    Note: This test must NOT trigger default scoring_spec addition.
    The LLM provides scoring_spec explicitly.
    """
    llm_json = {
        "raw_query": "ملک‌ها را با ریسک امتیاز بده",
        "goal": "score_with_risk",
        "entities": [
            {"ref": "properties", "kind": "vector"},
        ],
        "operations": [
            {
                "op": "score_features",
                "inputs": {"vector": "properties"},
                "params": {
                    # LLM explicitly provides scoring_spec with risk fields.
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near_poi",
                                "field": "distance_to_poi",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 0.5,
                            },
                            {
                                "name": "flood",
                                "field": "flood_risk",
                                "type": "risk_level",
                                "weight": 0.3,
                            },
                            {
                                "name": "quake",
                                "field": "earthquake_risk",
                                "type": "risk_level",
                                "weight": 0.2,
                            },
                        ],
                    }
                },
                "output": "scored",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "scored"},
                "params": {
                    "score_field": "investment_score",
                    "rank_field": "rank",
                },
                "output": "ranked",
            },
        ],
        "outputs": [{"kind": "vector", "source": "ranked"}],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    spec = LLMQuerySpecGenerator(client).generate("ملک‌ها را با ریسک امتیاز بده")

    op_names = [op.op for op in spec.operations]

    # enrich_risk must be injected.
    assert "enrich_risk" in op_names

    enrich_idx = op_names.index("enrich_risk")
    score_idx = op_names.index("score_features")

    # enrich_risk must come before score_features.
    assert enrich_idx < score_idx

    enrich_op = spec.operations[enrich_idx]
    assert enrich_op.params.get("default_risks") is not None

    # score_features must read from risk-enriched output.
    score_op = spec.operations[score_idx]
    assert score_op.inputs["vector"] != "properties"

    repairs = spec.metadata.get("normalization", {}).get("repairs", [])
    assert any("auto-injected enrich_risk" in r for r in repairs)

    plan = DeterministicPlanner().build(spec)
    cap_names = [n.capability_name for n in plan.nodes]
    assert "enrich_risk" in cap_names
    assert cap_names.index("enrich_risk") < cap_names.index("score_features")


def test_normalizer_auto_injects_build_report_and_render_pdf():
    """
    When output kind is 'report' with format 'pdf' but no build_report exists,
    normalizer must inject build_report + render_pdf.
    """
    llm_json = {
        "raw_query": "گزارش PDF ملک‌ها بده",
        "goal": "pdf_report",
        "entities": [{"ref": "properties", "kind": "vector"}],
        "operations": [
            {
                "op": "score_features",
                "inputs": {"vector": "properties"},
                "params": {
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "score",
                                "field": "distance_to_poi",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 1.0,
                            }
                        ],
                    }
                },
                "output": "scored",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "scored"},
                "params": {},
                "output": "ranked",
            },
        ],
        "outputs": [
            {"kind": "report", "source": "ranked", "format": "pdf"}
        ],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    spec = LLMQuerySpecGenerator(client).generate("گزارش PDF ملک‌ها بده")

    op_names = [op.op for op in spec.operations]
    assert "build_report" in op_names
    assert "render_pdf" in op_names

    rank_idx = op_names.index("rank_features")
    report_idx = op_names.index("build_report")
    pdf_idx = op_names.index("render_pdf")

    assert rank_idx < report_idx < pdf_idx

    output = spec.outputs[0]
    assert output.source == "pdf_report"

    repairs = spec.metadata.get("normalization", {}).get("repairs", [])
    assert any("build_report" in r for r in repairs)
    assert any("render_pdf" in r for r in repairs)

    plan = DeterministicPlanner().build(spec)
    capability_names = [n.capability_name for n in plan.nodes]
    assert "build_report" in capability_names
    assert "render_pdf" in capability_names


def test_llm_normalizer_never_defaults_score_field_to_investment_score_for_other_domains():
    """
    Regression test for the real-estate-default bug: a non-real-estate
    query with its own explicit scoring_spec must keep its own field
    names throughout - normalization must not reintroduce
    investment_score/buildable_zone/flood_risk anywhere in the pipeline.
    """
    llm_json = {
        "raw_query": "rank sites by accessibility",
        "goal": "score_accessibility",
        "entities": [{"ref": "sites", "kind": "vector"}],
        "operations": [
            {
                "op": "score_features",
                "inputs": {"vector": "sites"},
                "params": {
                    "scoring_spec": {
                        "output_field": "accessibility_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near_metro",
                                "field": "distance_to_metro",
                                "type": "inverse_distance",
                                "max_distance": 800,
                                "weight": 1.0,
                            }
                        ],
                    }
                },
                "output": "scored_sites",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "scored_sites"},
                "params": {},
                "output": "ranked_sites",
            },
        ],
        "outputs": [
            {"kind": "report", "source": "ranked_sites", "format": "pdf"}
        ],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    spec = LLMQuerySpecGenerator(client).generate("rank sites by accessibility")

    spec_json = json.dumps(asdict(spec))

    for banned in ("investment_score", "buildable_zone", "flood_risk", "earthquake_risk", "fire_risk"):
        assert banned not in spec_json, f"{banned!r} leaked into a non-real-estate QuerySpec"

    rank_op = next(op for op in spec.operations if op.op == "rank_features")
    assert rank_op.params["score_field"] == "accessibility_score"

    report_op = next(op for op in spec.operations if op.op == "build_report")
    assert report_op.params["score_field"] == "accessibility_score"


def test_build_report_auto_injection_uses_the_actual_score_field_not_investment_score():
    """
    build_report's auto-injected score_field/rank_field must come from the
    plan's own rank_features op, not a hardcoded "investment_score"/"rank"
    - previously any non-real-estate score field name was silently
    replaced with "investment_score" in the injected build_report node,
    producing a report that referenced a column the data never had.
    """
    llm_json = {
        "raw_query": "گزارش PDF سایت‌ها بده",
        "goal": "pdf_report",
        "entities": [{"ref": "sites", "kind": "vector"}],
        "operations": [
            {
                "op": "score_features",
                "inputs": {"vector": "sites"},
                "params": {
                    "scoring_spec": {
                        "output_field": "accessibility_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near_metro",
                                "field": "distance_to_metro",
                                "type": "inverse_distance",
                                "max_distance": 800,
                                "weight": 1.0,
                            }
                        ],
                    }
                },
                "output": "scored",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "scored"},
                "params": {"rank_field": "site_rank"},
                "output": "ranked",
            },
        ],
        "outputs": [
            {"kind": "report", "source": "ranked", "format": "pdf"}
        ],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    spec = LLMQuerySpecGenerator(client).generate("گزارش PDF سایت‌ها بده")

    report_op = next(op for op in spec.operations if op.op == "build_report")
    assert report_op.params["score_field"] == "accessibility_score"
    assert report_op.params["rank_field"] == "site_rank"


def test_generate_raises_a_specific_error_when_distance_to_is_missing_its_target_role():
    """
    Regression test for the distance_to omitted-target-role case: the LLM
    occasionally emits distance_to with only {"vector": ...}, omitting
    "target". This must fail as a specific LLMSpecGenerationError naming
    the operation and the missing role, not surface later as a generic
    PlanningError several frames away in DeterministicPlanner.build().
    """
    llm_json = {
        "raw_query": "distance to metro",
        "goal": "compute_distance",
        "entities": [
            {"ref": "sites", "kind": "vector"},
            {"ref": "metro", "kind": "vector"},
        ],
        "operations": [
            {
                "op": "distance_to",
                "inputs": {"vector": "sites"},
                "params": {},
                "output": "distances",
            }
        ],
        "outputs": [{"kind": "vector", "source": "distances"}],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    with pytest.raises(LLMSpecGenerationError, match="target"):
        generator.generate("distance to metro")


def test_domain_guidance_documents_every_operations_required_input_roles():
    """
    The per-operation input-role reference in the system prompt must be
    generated from OP_CATALOG (so a future operation can't go
    undocumented the way distance_to's "target" role did), and must in
    particular cover distance_to explicitly.
    """
    system_prompt = build_llm_messages("q")[0]["content"]

    assert "distance_to: inputs keys = {" in system_prompt
    assert "target" in system_prompt.split("distance_to: inputs keys = {", 1)[1].split("}", 1)[0]


# ------------------------------------------------------------------ #
# Bug 4: fan-out instead of chaining across multiple computed fields
# ------------------------------------------------------------------ #

def _fanout_scoring_operations(sites_ref_for_score: str) -> list[dict]:
    """
    Three distance computations, each reading the original "sites" vector
    (a fan-out) rather than each other's output (a chain) - the exact
    structural mistake reported from the Vienna accessibility case study:
    every LLM-generated plan across two independent 20-run batches made
    this same mistake, with score_features left pointed at a ref that
    carries none of the three computed distance fields.
    """
    return [
        {
            "op": "spatial_nearest",
            "inputs": {"source": "sites", "target": "metro"},
            "params": {"k": 1, "distance_field": "distance_to_metro"},
            "output": "sites_with_metro",
        },
        {
            "op": "spatial_nearest",
            "inputs": {"source": "sites", "target": "schools"},
            "params": {"k": 1, "distance_field": "distance_to_schools"},
            "output": "sites_with_schools",
        },
        {
            "op": "spatial_nearest",
            "inputs": {"source": "sites", "target": "parks"},
            "params": {"k": 1, "distance_field": "distance_to_parks"},
            "output": "sites_with_parks",
        },
        {
            "op": "score_features",
            "inputs": {"vector": sites_ref_for_score},
            "params": {
                "scoring_spec": {
                    "output_field": "accessibility_score",
                    "factors": [
                        {
                            "name": "metro",
                            "field": "distance_to_metro",
                            "type": "inverse_distance",
                            "max_distance": 800,
                            "weight": 1,
                        },
                        {
                            "name": "schools",
                            "field": "distance_to_schools",
                            "type": "inverse_distance",
                            "max_distance": 1200,
                            "weight": 1,
                        },
                        {
                            "name": "parks",
                            "field": "distance_to_parks",
                            "type": "inverse_distance",
                            "max_distance": 1000,
                            "weight": 1,
                        },
                    ],
                }
            },
            "output": "scored",
        },
        {
            "op": "rank_features",
            "inputs": {"vector": "scored"},
            "params": {},
            "output": "ranked",
        },
    ]


def _fanout_spec_json(sites_ref_for_score: str) -> dict:
    return {
        "raw_query": "rank sites by accessibility to metro, schools and parks",
        "goal": "score_accessibility",
        "entities": [
            {"ref": "sites", "kind": "vector"},
            {"ref": "metro", "kind": "vector"},
            {"ref": "schools", "kind": "vector"},
            {"ref": "parks", "kind": "vector"},
        ],
        "operations": _fanout_scoring_operations(sites_ref_for_score),
        "outputs": [{"kind": "vector", "source": "ranked"}],
    }


def test_generate_raises_when_score_features_is_not_fed_the_chained_output():
    """
    Regression test for the fan-out bug: score_features pointed at the
    original "sites" vector, while the three distance fields its factors
    reference were each computed onto a separate sibling branch
    ("sites_with_metro"/"_schools"/"_parks") instead of being chained onto
    one another. This must raise, naming a missing field, rather than
    silently plan and later execute to an all-zero score.
    """
    llm_json = _fanout_spec_json(sites_ref_for_score="sites")

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    with pytest.raises(LLMSpecGenerationError, match="distance_to_"):
        generator.generate("rank sites by accessibility to metro, schools and parks")


def test_generate_raises_when_score_features_reads_only_one_of_three_branches():
    """
    Same fan-out shape, but score_features reads one of the three sibling
    branches (sites_with_metro) instead of the original vector - still
    missing the other two computed fields, so this must raise too, not
    just the "reads the original vector" case.
    """
    llm_json = _fanout_spec_json(sites_ref_for_score="sites_with_metro")

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    with pytest.raises(LLMSpecGenerationError, match="distance_to_"):
        generator.generate("rank sites by accessibility to metro, schools and parks")


def test_generate_accepts_the_correctly_chained_equivalent():
    """
    The correct fix for the fan-out case: each distance computation reads
    the previous one's output, so score_features's input ref alone carries
    all three fields. Must plan successfully with no repairs masking a
    real issue.
    """
    llm_json = _fanout_spec_json(sites_ref_for_score="sites_with_parks")
    llm_json["operations"][1]["inputs"]["source"] = "sites_with_metro"
    llm_json["operations"][2]["inputs"]["source"] = "sites_with_schools"

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate("rank sites by accessibility to metro, schools and parks")

    assert [op.op for op in spec.operations] == [
        "spatial_nearest",
        "spatial_nearest",
        "spatial_nearest",
        "score_features",
        "rank_features",
    ]

    plan = DeterministicPlanner().build(spec)
    assert [node.capability_name for node in plan.nodes] == [
        "find_nearest_neighbors",
        "find_nearest_neighbors",
        "find_nearest_neighbors",
        "score_features",
        "rank_features",
    ]


def test_field_chaining_check_does_not_flag_fields_it_cannot_trace():
    """
    A factor referencing a field with no known producer (e.g. an
    attribute already present on the raw uploaded data) must not be
    flagged - under-catching is the deliberately safe failure mode here,
    since this module has no way to know the raw data's own schema.
    """
    llm_json = {
        "raw_query": "score sites by an existing attribute",
        "goal": "score_sites",
        "entities": [{"ref": "sites", "kind": "vector"}],
        "operations": [
            {
                "op": "score_features",
                "inputs": {"vector": "sites"},
                "params": {
                    "scoring_spec": {
                        "output_field": "score",
                        "factors": [
                            {
                                "name": "existing",
                                "field": "pre_existing_attribute",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 1,
                            }
                        ],
                    }
                },
                "output": "scored",
            },
            {
                "op": "rank_features",
                "inputs": {"vector": "scored"},
                "params": {},
                "output": "ranked",
            },
        ],
        "outputs": [{"kind": "vector", "source": "ranked"}],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    spec = LLMQuerySpecGenerator(client).generate("score sites by an existing attribute")

    assert [op.op for op in spec.operations] == ["score_features", "rank_features"]


def test_domain_guidance_documents_multi_factor_chaining_and_join_alternative():
    """
    The chaining-vs-fan-out guidance and the join_feature_properties
    escape hatch for genuinely unchainable branches must both be present
    in the LLM-facing prompt - neither existed before this fix, which is
    exactly why the model had no way to get this right.
    """
    system_prompt = build_llm_messages("q")[0]["content"]

    assert "MORE THAN ONE computed field" in system_prompt
    assert "join_feature_properties" in system_prompt
