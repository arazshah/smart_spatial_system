from orchestrator.planning.postgis_semantic_resolver import (
    ColumnInfo,
    PostGISSchemaContext,
    PostGISTableInfo,
    build_safe_semantic_predicate,
    infer_semantic_concepts,
    resolve_query_semantic_layers,
    resolve_semantic_layer,
)


def _osm_like_schema_without_station_column():
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
                    ColumnInfo("amenity", "text", "text"),
                    ColumnInfo("shop", "text", "text"),
                    ColumnInfo("tags", "USER-DEFINED", "hstore"),
                    ColumnInfo("way", "USER-DEFINED", "geometry"),
                ),
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
            ),
        )
    )


def test_missing_rule_column_is_skipped_not_generated():
    schema = _osm_like_schema_without_station_column()
    table = schema.find_table(schema="public", table="planet_osm_point", geom_col="way")
    assert table is not None

    rule = {
        "any": [
            {"column": "railway", "op": "eq", "value": "station"},
            {"column": "station", "op": "eq", "value": "subway"},
        ]
    }

    result = build_safe_semantic_predicate(table, rule)

    assert result.resolved
    assert '"railway" = ' in result.where
    assert '"station"' not in result.where
    assert any(item["term"].get("column") == "station" for item in result.skipped_terms)


def test_hstore_tag_fallback_is_generated_when_direct_column_missing():
    table = PostGISTableInfo(
        schema="public",
        table="some_poi_table",
        geom_col="geom",
        geometry_type="POINT",
        srid=4326,
        columns=(
            ColumnInfo("name", "text", "text"),
            ColumnInfo("tags", "USER-DEFINED", "hstore"),
            ColumnInfo("geom", "USER-DEFINED", "geometry"),
        ),
    )

    rule = {
        "any": [
            {"column": "railway", "op": "eq", "value": "station"},
            {"tag": "railway", "op": "eq", "value": "station"},
        ]
    }

    result = build_safe_semantic_predicate(table, rule)

    assert result.resolved
    assert '"railway"' not in result.where
    assert '"tags" -> ' in result.where
    assert "'railway'" in result.where
    assert "'station'" in result.where


def test_resolve_metro_station_uses_existing_columns_only():
    schema = _osm_like_schema_without_station_column()

    candidates = resolve_semantic_layer("metro_station", schema)

    assert candidates
    best = candidates[0]
    assert best.schema == "public"
    assert best.table == "planet_osm_point"
    assert best.geom_col == "way"
    assert '"way" IS NOT NULL' in best.where
    assert '"station"' not in best.where
    assert '"railway"' in best.where or '"public_transport"' in best.where


def test_resolve_shopping_center_polygon_candidate():
    schema = _osm_like_schema_without_station_column()

    candidates = resolve_semantic_layer("shopping_center", schema)

    assert candidates
    assert candidates[0].table == "planet_osm_polygon"
    assert '"shop"' in candidates[0].where or '"amenity"' in candidates[0].where


def test_infer_persian_query_concepts():
    concepts = infer_semantic_concepts(
        "در تهران نزدیک‌ترین مرکز خرید به هر ایستگاه مترو را پیدا کن"
    )

    assert "metro_station" in concepts
    assert "shopping_center" in concepts


def test_resolve_query_semantic_layers_returns_candidate_dict():
    schema = _osm_like_schema_without_station_column()

    resolved = resolve_query_semantic_layers(
        "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو را پیدا کن و فاصله را حساب کن",
        schema,
    )

    assert "metro_station" in resolved
    assert "shopping_center" in resolved
    assert resolved["metro_station"]
    assert resolved["shopping_center"]
