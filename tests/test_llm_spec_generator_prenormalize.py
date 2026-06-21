from orchestrator.planning.llm_spec_generator import (
    StaticLLMClient,
    LLMQuerySpecGenerator,
    query_spec_to_dict,
    build_llm_messages,
)


def test_generator_pre_normalizes_operation_inputs_array_to_object():
    client = StaticLLMClient(
        """
        {
          "goal": "nearest shopping center to metro",
          "entities": [],
          "operations": [
            {
              "op": "query_database",
              "inputs": {},
              "params": {
                "source_type": "postgis",
                "mode": "select_table",
                "schema": "public",
                "table": "planet_osm_point",
                "columns": ["osm_id", "name"],
                "geom_col": "way",
                "geom_alias": "geom",
                "where": "\\"way\\" IS NOT NULL",
                "limit": 100,
                "output_srid": 3857
              },
              "output": "metro_layer"
            },
            {
              "op": "spatial_nearest",
              "inputs": [
                {"source": "metro_layer"},
                {"target": "shopping_layer"}
              ],
              "params": [
                {"k": 1},
                {"include_target_geometry": true}
              ],
              "output": "nearest_result"
            }
          ],
          "outputs": [
            {
              "kind": "vector",
              "source": "nearest_result",
              "format": "geojson",
              "config": [
                {"display": true}
              ]
            }
          ],
          "metadata": {}
        }
        """
    )

    spec = LLMQuerySpecGenerator(client).generate(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو"
    )

    data = query_spec_to_dict(spec)

    nearest = data["operations"][1]
    assert nearest["inputs"] == {
        "source": "metro_layer",
        "target": "shopping_layer",
    }
    assert nearest["params"]["k"] == 1
    assert nearest["params"]["include_target_geometry"] is True

    assert data["outputs"][0]["config"] == {"display": True}


def test_semantic_context_prompt_explicitly_requires_inputs_and_params_objects():
    messages = build_llm_messages(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو",
        context={
            "semantic_planning_context": {
                "detected_concepts": ["metro_station", "shopping_center"],
                "guardrails": {
                    "llm_must_not_generate_raw_sql": True,
                    "llm_must_not_invent_table_names": True,
                    "llm_must_not_invent_column_names": True,
                },
            }
        },
    )

    system = messages[0]["content"]

    assert "operations[i].inputs must be a JSON object, never an array" in system
    assert "operations[i].params must be a JSON object, never an array" in system
    assert '"inputs": {"source": "metro_layer", "target": "shopping_layer"}' in system
