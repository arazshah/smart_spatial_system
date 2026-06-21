from orchestrator.service import _extract_semantic_planning_context_from_sources


def test_service_builds_semantic_planning_context_from_postgis_schema_context():
    postgis_schema_context = {
        "tables": [
            {
                "schema": "public",
                "table": "planet_osm_point",
                "geom_col": "way",
                "geometry_type": "POINT",
                "srid": 3857,
                "columns": [
                    {"name": "osm_id", "data_type": "bigint", "udt_name": "int8"},
                    {"name": "name", "data_type": "text", "udt_name": "text"},
                    {"name": "railway", "data_type": "text", "udt_name": "text"},
                    {"name": "public_transport", "data_type": "text", "udt_name": "text"},
                    {"name": "shop", "data_type": "text", "udt_name": "text"},
                    {"name": "amenity", "data_type": "text", "udt_name": "text"},
                    {"name": "building", "data_type": "text", "udt_name": "text"},
                    {"name": "landuse", "data_type": "text", "udt_name": "text"},
                    {"name": "tags", "data_type": "USER-DEFINED", "udt_name": "hstore"},
                    {"name": "way", "data_type": "USER-DEFINED", "udt_name": "geometry"},
                ],
            },
            {
                "schema": "public",
                "table": "planet_osm_polygon",
                "geom_col": "way",
                "geometry_type": "GEOMETRY",
                "srid": 3857,
                "columns": [
                    {"name": "osm_id", "data_type": "bigint", "udt_name": "int8"},
                    {"name": "name", "data_type": "text", "udt_name": "text"},
                    {"name": "shop", "data_type": "text", "udt_name": "text"},
                    {"name": "amenity", "data_type": "text", "udt_name": "text"},
                    {"name": "building", "data_type": "text", "udt_name": "text"},
                    {"name": "landuse", "data_type": "text", "udt_name": "text"},
                    {"name": "tags", "data_type": "USER-DEFINED", "udt_name": "hstore"},
                    {"name": "way", "data_type": "USER-DEFINED", "udt_name": "geometry"},
                ],
            },
        ]
    }

    context, error = _extract_semantic_planning_context_from_sources(
        query=(
            "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو را پیدا کن "
            "و ۲۰ مورد اول را روی نقشه و جدول نمایش بده"
        ),
        resolved_inputs={},
        user_context={"postgis_schema_context": postgis_schema_context},
        metadata={},
    )

    assert error is None
    assert context is not None
    assert "metro_station" in context["detected_concepts"]
    assert "shopping_center" in context["detected_concepts"]
    assert "spatial_nearest" in context["recommended_ops"]
    assert "top_n" in context["recommended_ops"]
    assert context["requested_limit"] == 20
    assert context["guardrails"]["llm_must_not_generate_raw_sql"] is True


def test_service_forwards_existing_semantic_planning_context():
    existing = {
        "detected_concepts": ["park"],
        "semantic_layers": {},
        "guardrails": {"llm_must_not_generate_raw_sql": True},
    }

    context, error = _extract_semantic_planning_context_from_sources(
        query="پارک‌ها را پیدا کن",
        resolved_inputs={},
        user_context={"semantic_planning_context": existing},
        metadata={},
    )

    assert error is None
    assert context == existing


def test_service_semantic_context_helper_is_non_fatal_for_invalid_schema():
    context, error = _extract_semantic_planning_context_from_sources(
        query="پارک‌ها را پیدا کن",
        resolved_inputs={},
        user_context={"postgis_schema_context": {"bad": "shape"}},
        metadata={},
    )

    assert context is None
    assert error is None
