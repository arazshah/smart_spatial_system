from orchestrator.planning.llm_spec_generator import (
    LLMQuerySpecGenerator,
    StaticLLMClient,
    query_spec_to_dict,
)


def test_query_database_params_are_moved_from_inputs_to_params():
    client = StaticLLMClient(
        """
        {
          "goal": "load metro",
          "entities": [],
          "operations": [
            {
              "op": "query_database",
              "inputs": {
                "source_type": "postgis",
                "schema": "public",
                "table": "planet_osm_point",
                "columns": ["osm_id", "name"],
                "geom_col": "way",
                "where": "\\"way\\" IS NOT NULL"
              },
              "params": {},
              "output": "metro_station"
            }
          ],
          "outputs": [],
          "metadata": {}
        }
        """
    )

    spec = LLMQuerySpecGenerator(client).generate("متروها را بارگذاری کن")
    data = query_spec_to_dict(spec)

    op = data["operations"][0]
    assert op["op"] == "query_database"
    assert op["inputs"] == {}
    assert op["params"]["source_type"] == "postgis"
    assert op["params"]["mode"] == "select_table"
    assert op["params"]["schema"] == "public"
    assert op["params"]["table"] == "planet_osm_point"
    assert op["params"]["geom_col"] == "way"
    assert op["params"]["geom_alias"] == "geom"
    assert op["params"]["limit"] == 5000


def test_database_entities_without_load_operations_get_query_database_injected():
    client = StaticLLMClient(
        """
        {
          "goal": "nearest shopping center to metro",
          "entities": [
            {
              "ref": "metro_station",
              "kind": "database",
              "binding": {
                "source_type": "postgis",
                "schema": "public",
                "table": "planet_osm_point",
                "columns": ["osm_id", "name", "railway"],
                "geom_col": "way",
                "where": "\\"way\\" IS NOT NULL AND \\"railway\\" = 'station'"
              },
              "hints": {}
            },
            {
              "ref": "shopping_center",
              "kind": "database",
              "binding": {
                "source_type": "postgis",
                "schema": "public",
                "table": "planet_osm_polygon",
                "columns": ["osm_id", "name", "shop"],
                "geom_col": "way",
                "where": "\\"way\\" IS NOT NULL AND \\"shop\\" = 'mall'"
              },
              "hints": {}
            }
          ],
          "operations": [
            {
              "op": "spatial_nearest",
              "inputs": {
                "source": "metro_station",
                "target": "shopping_center"
              },
              "params": {
                "k": 1,
                "include_target_geometry": true
              },
              "output": "nearest_result"
            },
            {
              "op": "top_n",
              "inputs": {
                "source": "nearest_result"
              },
              "params": {
                "score_field": "_nearest_distance",
                "descending": false,
                "limit": 20
              },
              "output": "top_result"
            }
          ],
          "outputs": [],
          "metadata": {}
        }
        """
    )

    spec = LLMQuerySpecGenerator(client).generate(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو"
    )
    data = query_spec_to_dict(spec)

    ops = data["operations"]

    assert [op["op"] for op in ops[:2]] == ["query_database", "query_database"]
    assert ops[0]["output"] == "metro_station"
    assert ops[1]["output"] == "shopping_center"

    assert ops[0]["params"]["schema"] == "public"
    assert ops[0]["params"]["table"] == "planet_osm_point"
    assert ops[0]["params"]["geom_col"] == "way"
    assert ops[0]["params"]["mode"] == "select_table"

    assert ops[2]["op"] == "spatial_nearest"
    assert ops[2]["inputs"] == {
        "source": "metro_station",
        "target": "shopping_center",
    }

    assert ops[3]["op"] == "top_n"
    assert ops[3]["inputs"]["vector"] == "nearest_result"


def test_vector_ops_accept_source_alias_before_catalog_input_filtering():
    client = StaticLLMClient(
        """
        {
          "goal": "display top result",
          "entities": [],
          "operations": [
            {
              "op": "top_n",
              "inputs": {
                "source": "nearest_result"
              },
              "params": {
                "score_field": "_nearest_distance",
                "descending": false,
                "limit": 20
              },
              "output": "top_result"
            },
            {
              "op": "display_vector",
              "inputs": {
                "source": "top_result"
              },
              "params": {},
              "output": "map_display"
            },
            {
              "op": "summarize_vector",
              "inputs": {
                "source": "top_result"
              },
              "params": {},
              "output": "summary_table"
            }
          ],
          "outputs": [],
          "metadata": {}
        }
        """
    )

    spec = LLMQuerySpecGenerator(client).generate("۲۰ مورد اول را روی نقشه و جدول نمایش بده")
    data = query_spec_to_dict(spec)

    assert data["operations"][0]["inputs"]["vector"] == "nearest_result"
    assert data["operations"][1]["inputs"]["vector"] == "top_result"
    assert data["operations"][2]["inputs"]["vector"] == "top_result"
