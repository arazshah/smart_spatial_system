from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
import pytest

from orchestrator.planning.llm_spec_generator import (
    LLMSpecGenerationError,
    LLMQuerySpecGenerator,
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
                "params": {},
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


def test_llm_generator_adds_default_scoring_spec_when_missing():
    llm_json = {
        "raw_query": "املاک را امتیاز بده",
        "goal": "score_properties",
        "entities": [
            {"ref": "properties", "kind": "vector"}
        ],
        "operations": [
            {
                "op": "score_features",
                "inputs": {
                    "vector": "properties"
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

    spec = generator.generate("املاک را امتیاز بده")

    assert "scoring_spec" in spec.operations[0].params
    assert spec.operations[0].params["scoring_spec"]["output_field"] == "investment_score"

    plan = DeterministicPlanner().build(spec)
    assert plan.nodes[0].capability_name == "score_features"


def test_llm_normalizer_injects_enrichment_nodes_for_default_real_estate_scoring():
    """
    When LLM omits scoring_spec, normalizer should:
      - add default scoring spec
      - insert enrichment nodes after distance/polygon operations
      - rewrite downstream refs to enriched outputs
    """
    llm_json = {
        "raw_query": "املاک نزدیک مترو و خیابان اصلی را امتیاز بده",
        "goal": "rank_real_estate",
        "entities": [
            {"ref": "properties", "kind": "vector"},
            {"ref": "poi", "kind": "vector"},
            {"ref": "buildable_zone", "kind": "vector"},
            {"ref": "roads", "kind": "vector"},
        ],
        "operations": [
            {
                "op": "filter_by_distance",
                "inputs": {"vector": "properties", "reference": "poi"},
                "params": {"max_distance_m": 500, "k": 1, "drop_unmatched": True},
                "output": "near_properties",
            },
            {
                "op": "filter_points_in_polygon",
                "inputs": {"vector": "near_properties", "polygon": "buildable_zone"},
                "params": {"predicate": "within", "drop_outside": True},
                "output": "buildable_properties",
            },
            {
                "op": "filter_by_distance",
                "inputs": {"vector": "buildable_properties", "reference": "roads"},
                "params": {"max_distance_m": 500, "k": 1, "drop_unmatched": True},
                "output": "final_properties",
            },
            {
                "op": "score_features",
                "inputs": {"vector": "final_properties"},
                "params": {},
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
            {"kind": "report", "source": "ranked_properties", "format": "pdf"}
        ],
    }

    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))
    generator = LLMQuerySpecGenerator(client)

    spec = generator.generate("املاک نزدیک مترو و خیابان اصلی را امتیاز بده")

    ops = spec.operations
    op_names = [op.op for op in ops]

    # Phase 10D: build_report + render_pdf auto-injected for pdf output
    core_ops = [
        "filter_by_distance",
        "enrich_feature_properties",
        "filter_points_in_polygon",
        "enrich_feature_properties",
        "filter_by_distance",
        "enrich_feature_properties",
        "score_features",
        "rank_features",
    ]
    assert op_names[:8] == core_ops
    assert "build_report" in op_names
    assert "render_pdf" in op_names

    assert ops[1].params["rules"][0]["target"] == "distance_to_poi"
    assert ops[2].inputs["vector"] == "near_properties_enriched"

    assert ops[3].params["rules"][0]["target"] == "inside_buildable_zone"
    assert ops[4].inputs["vector"] == "buildable_properties_enriched"

    assert ops[5].params["rules"][0]["target"] == "distance_to_road"
    assert ops[6].inputs["vector"] == "final_properties_enriched"

    scoring_spec = ops[6].params["scoring_spec"]
    fields = [factor["field"] for factor in scoring_spec["factors"]]

    assert "distance_to_poi" in fields
    assert "distance_to_road" in fields
    assert "inside_buildable_zone" in fields

    assert ops[7].params["score_field"] == "investment_score"

    plan = DeterministicPlanner().build(spec)

    # Phase 10D: build_report + render_pdf auto-injected
    cap_names = [node.capability_name for node in plan.nodes]
    assert cap_names[:8] == [
        "find_nearest_neighbors",
        "enrich_feature_properties",
        "filter_points_in_polygon",
        "enrich_feature_properties",
        "find_nearest_neighbors",
        "enrich_feature_properties",
        "score_features",
        "rank_features",
    ]
    assert "build_report" in cap_names
    assert "render_pdf" in cap_names


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
                "params": {},
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

    assert "enrich_feature_properties" in op_names
    assert all(
        not (
            op.op == "enrich_feature_properties"
            and not op.params.get("rules")
        )
        for op in spec.operations
    )

    # score_features must no longer depend on removed enriched_properties.
    score_op = next(op for op in spec.operations if op.op == "score_features")
    assert score_op.inputs["vector"] != "enriched_properties"

    plan = DeterministicPlanner().build(spec)

    assert all(
        not (
            node.capability_name == "enrich_feature_properties"
            and "rules" not in node.static_params
        )
        for node in plan.nodes
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
