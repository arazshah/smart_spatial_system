from orchestrator.planning.postgis_semantic_resolver import (
    ColumnInfo,
    PostGISSchemaContext,
    PostGISTableInfo,
)
from orchestrator.planning.semantic_planning_context import (
    build_semantic_planning_context,
    extract_requested_limit,
    infer_query_intents,
    semantic_planning_context_to_dict,
)


def _schema():
    return PostGISSchemaContext(
        tables=(
            PostGISTableInfo(
                schema="public",
                table="planet_osm_point",
                geom_col="way",
                geometry_type="POINT",
                srid=3857,
                columns=(
                    ColumnInfo("osm_id", "bigint", "int8"),
                    ColumnInfo("name", "text", "text"),
                    ColumnInfo("railway", "text", "text"),
                    ColumnInfo("public_transport", "text", "text"),
                    ColumnInfo("shop", "text", "text"),
                    ColumnInfo("amenity", "text", "text"),
                    ColumnInfo("building", "text", "text"),
                    ColumnInfo("landuse", "text", "text"),
                    ColumnInfo("tags", "USER-DEFINED", "hstore"),
                    ColumnInfo("way", "USER-DEFINED", "geometry"),
                ),
                estimated_rows=1000,
            ),
            PostGISTableInfo(
                schema="public",
                table="planet_osm_polygon",
                geom_col="way",
                geometry_type="GEOMETRY",
                srid=3857,
                columns=(
                    ColumnInfo("osm_id", "bigint", "int8"),
                    ColumnInfo("name", "text", "text"),
                    ColumnInfo("shop", "text", "text"),
                    ColumnInfo("amenity", "text", "text"),
                    ColumnInfo("building", "text", "text"),
                    ColumnInfo("landuse", "text", "text"),
                    ColumnInfo("tags", "USER-DEFINED", "hstore"),
                    ColumnInfo("way", "USER-DEFINED", "geometry"),
                ),
                estimated_rows=1000,
            ),
        )
    )


def test_extract_requested_limit_supports_persian_digits():
    assert extract_requested_limit("۲۰ مورد اول را نمایش بده") == 20
    assert extract_requested_limit("top 10 nearest hospitals") == 10
    assert extract_requested_limit("فقط تحلیل کن") is None


def test_infer_query_intents_for_persian_nearest_map_table():
    intents = infer_query_intents(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو را پیدا کن، فاصله را به متر محاسبه کن و ۲۰ مورد اول را روی نقشه و جدول نمایش بده"
    )

    assert intents["nearest"] is True
    assert intents["distance"] is True
    assert intents["top_n"] is True
    assert intents["map"] is True
    assert intents["table"] is True


def test_build_semantic_planning_context_for_metro_to_shopping_center():
    context = build_semantic_planning_context(
        "در تهران نزدیک‌ترین مرکز خرید به هر ایستگاه مترو را پیدا کن، فاصله را به متر محاسبه کن و ۲۰ مورد اول را روی نقشه و جدول نمایش بده.",
        _schema(),
    )

    assert "metro_station" in context.detected_concepts
    assert "shopping_center" in context.detected_concepts

    assert "metro_station" in context.semantic_layers
    assert "shopping_center" in context.semantic_layers
    assert context.semantic_layers["metro_station"]
    assert context.semantic_layers["shopping_center"]

    assert "query_database" in context.recommended_ops
    assert "spatial_nearest" in context.recommended_ops
    assert "top_n" in context.recommended_ops
    assert "display_vector" in context.recommended_ops
    assert "summarize_vector" in context.recommended_ops

    assert context.requested_limit == 20
    assert context.intents["nearest"] is True
    assert context.intents["map"] is True
    assert context.intents["table"] is True

    metro_layer = context.semantic_layers["metro_station"][0]
    assert metro_layer.op == "query_database"
    assert metro_layer.params["source_type"] == "postgis"
    assert metro_layer.params["mode"] == "select_table"
    assert metro_layer.params["schema"] == "public"
    assert metro_layer.params["table"] == "planet_osm_point"
    assert metro_layer.params["geom_col"] == "way"
    assert metro_layer.params["output_srid"] == 3857

    # The direct missing column "station" must not be generated as an identifier.
    # It may still appear safely as an hstore tag key: ("tags" -> 'station').
    assert '"station"' not in metro_layer.params["where"]


def test_context_to_dict_is_json_friendly_and_contains_guardrails():
    context = build_semantic_planning_context(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو و ۲۰ مورد اول روی نقشه",
        _schema(),
    )

    data = semantic_planning_context_to_dict(context)

    assert isinstance(data, dict)
    assert data["guardrails"]["llm_must_not_generate_raw_sql"] is True
    assert data["guardrails"]["llm_must_not_invent_table_names"] is True
    assert data["guardrails"]["llm_must_not_invent_column_names"] is True

    assert data["detected_concepts"]
    assert "semantic_layers" in data
    assert "recommended_ops" in data
    assert "operation_hints" in data

    hint_ops = [item["op"] for item in data["operation_hints"]]
    assert "spatial_nearest" in hint_ops
    assert "top_n" in hint_ops


def test_context_without_detected_concepts_is_safe_empty_context():
    context = build_semantic_planning_context(
        "یک تحلیل خیلی کلی انجام بده",
        _schema(),
    )

    assert context.detected_concepts == tuple()
    assert context.semantic_layers == {}
    assert context.recommended_ops == tuple()
    assert "no_semantic_concepts_detected" in context.warnings

    data = context.to_dict()
    assert data["warnings"] == ["no_semantic_concepts_detected"]
