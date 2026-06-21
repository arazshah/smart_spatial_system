import json

from orchestrator.planning.llm_spec_generator import build_llm_messages


def test_llm_messages_include_semantic_planning_context_guardrails():
    semantic_context = {
        "detected_concepts": ["metro_station", "shopping_center"],
        "semantic_layers": {
            "metro_station": [
                {
                    "op": "query_database",
                    "params": {
                        "source_type": "postgis",
                        "mode": "select_table",
                        "schema": "public",
                        "table": "planet_osm_point",
                        "columns": ["osm_id", "name", "railway"],
                        "geom_col": "way",
                        "geom_alias": "geom",
                        "where": '"way" IS NOT NULL AND "railway" = \'station\'',
                        "limit": 5000,
                        "output_srid": 3857,
                    },
                }
            ]
        },
        "recommended_ops": [
            "query_database",
            "spatial_nearest",
            "top_n",
            "display_vector",
            "summarize_vector",
        ],
        "requested_limit": 20,
        "guardrails": {
            "llm_must_not_generate_raw_sql": True,
            "llm_must_not_invent_table_names": True,
            "llm_must_not_invent_column_names": True,
            "use_semantic_layer_candidates_first": True,
        },
    }

    messages = build_llm_messages(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو را پیدا کن",
        context={"semantic_planning_context": semantic_context},
    )

    system = messages[0]["content"]
    user_payload = json.loads(messages[1]["content"])

    assert "Semantic Planning Context Guardrails" in system
    assert "Do not invent PostGIS table names" in system
    assert "Do not invent PostGIS column names" in system
    assert "Do not generate raw SQL" in system
    assert 'op="spatial_nearest"' in system
    assert "_nearest_distance" in system

    assert user_payload["context"]["semantic_planning_context"] == semantic_context
    assert user_payload["context"]["semantic_planning_context"]["guardrails"][
        "llm_must_not_generate_raw_sql"
    ] is True
