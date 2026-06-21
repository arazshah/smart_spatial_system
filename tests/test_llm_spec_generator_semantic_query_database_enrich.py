from orchestrator.planning.llm_spec_generator import (
    LLMQuerySpecGenerator,
    StaticLLMClient,
    query_spec_to_dict,
)


def _semantic_context():
    return {
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
                        "columns": ["osm_id", "name", "railway", "public_transport"],
                        "geom_col": "way",
                        "geom_alias": "geom",
                        "where": '"way" IS NOT NULL AND "railway" = \'station\'',
                        "limit": 5000,
                        "output_srid": 3857,
                    },
                }
            ],
            "shopping_center": [
                {
                    "op": "query_database",
                    "params": {
                        "source_type": "postgis",
                        "mode": "select_table",
                        "schema": "public",
                        "table": "planet_osm_point",
                        "columns": ["osm_id", "name", "shop"],
                        "geom_col": "way",
                        "geom_alias": "geom",
                        "where": '"way" IS NOT NULL AND "shop" = \'mall\'',
                        "limit": 5000,
                        "output_srid": 3857,
                    },
                }
            ],
        },
        "guardrails": {
            "llm_must_not_generate_raw_sql": True,
            "llm_must_not_invent_table_names": True,
            "llm_must_not_invent_column_names": True,
        },
    }


def test_incomplete_query_database_ops_are_enriched_from_semantic_context():
    client = StaticLLMClient(
        """
        {
          "goal": "nearest shopping centers",
          "entities": [],
          "operations": [
            {
              "op": "query_database",
              "inputs": {},
              "params": {
                "source_type": "postgis",
                "mode": "select_table",
                "where": "\\"way\\" IS NOT NULL AND \\"railway\\" = 'station'",
                "limit": 5000,
                "output_srid": 3857
              },
              "output": "metro_stations"
            },
            {
              "op": "query_database",
              "inputs": {},
              "params": {
                "source_type": "postgis",
                "mode": "select_table",
                "where": "\\"way\\" IS NOT NULL AND \\"shop\\" = 'mall'",
                "limit": 5000,
                "output_srid": 3857
              },
              "output": "shopping_centers"
            },
            {
              "op": "spatial_nearest",
              "inputs": {
                "source": "metro_stations",
                "target": "shopping_centers"
              },
              "params": {
                "k": 1,
                "include_target_geometry": true
              },
              "output": "nearest_result"
            }
          ],
          "outputs": [],
          "metadata": {}
        }
        """
    )

    spec = LLMQuerySpecGenerator(client).generate(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو",
        context={"semantic_planning_context": _semantic_context()},
    )

    data = query_spec_to_dict(spec)

    metro = data["operations"][0]
    shopping = data["operations"][1]

    assert metro["op"] == "query_database"
    assert metro["params"]["schema"] == "public"
    assert metro["params"]["table"] == "planet_osm_point"
    assert metro["params"]["geom_col"] == "way"
    assert metro["params"]["columns"] == ["osm_id", "name", "railway", "public_transport"]
    assert metro["params"]["geom_alias"] == "geom"

    assert shopping["op"] == "query_database"
    assert shopping["params"]["schema"] == "public"
    assert shopping["params"]["table"] == "planet_osm_point"
    assert shopping["params"]["geom_col"] == "way"
    assert shopping["params"]["columns"] == ["osm_id", "name", "shop"]


def test_entity_injection_does_not_duplicate_existing_plural_query_database_outputs():
    client = StaticLLMClient(
        """
        {
          "goal": "nearest shopping centers",
          "entities": [
            {
              "ref": "metro_station",
              "kind": "database",
              "binding": {
                "schema": "public",
                "table": "planet_osm_point",
                "columns": ["osm_id", "name"],
                "geom_col": "way"
              },
              "hints": {}
            },
            {
              "ref": "shopping_center",
              "kind": "database",
              "binding": {
                "schema": "public",
                "table": "planet_osm_point",
                "columns": ["osm_id", "name"],
                "geom_col": "way"
              },
              "hints": {}
            }
          ],
          "operations": [
            {
              "op": "query_database",
              "inputs": {},
              "params": {
                "where": "\\"way\\" IS NOT NULL"
              },
              "output": "metro_stations"
            },
            {
              "op": "query_database",
              "inputs": {},
              "params": {
                "where": "\\"way\\" IS NOT NULL"
              },
              "output": "shopping_centers"
            },
            {
              "op": "spatial_nearest",
              "inputs": {
                "source": "metro_stations",
                "target": "shopping_centers"
              },
              "params": {"k": 1},
              "output": "nearest_result"
            }
          ],
          "outputs": [],
          "metadata": {}
        }
        """
    )

    spec = LLMQuerySpecGenerator(client).generate(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو",
        context={"semantic_planning_context": _semantic_context()},
    )

    data = query_spec_to_dict(spec)

    query_database_ops = [
        op for op in data["operations"]
        if op["op"] == "query_database"
    ]

    assert len(query_database_ops) == 2
    assert query_database_ops[0]["output"] == "metro_stations"
    assert query_database_ops[1]["output"] == "shopping_centers"

    assert query_database_ops[0]["params"]["schema"] == "public"
    assert query_database_ops[1]["params"]["schema"] == "public"
