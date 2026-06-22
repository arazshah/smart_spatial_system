import orchestrator.service as service_module


def _fake_schema_context():
    return service_module.PostGISSchemaContext(
        tables=(
            service_module.PostGISTableInfo(
                schema="public",
                table="planet_osm_point",
                geom_col="way",
                geometry_type="POINT",
                srid=3857,
                columns=(
                    service_module.ColumnInfo("osm_id", "bigint", "int8"),
                    service_module.ColumnInfo("name", "text", "text"),
                    service_module.ColumnInfo("railway", "text", "text"),
                    service_module.ColumnInfo("public_transport", "text", "text"),
                    service_module.ColumnInfo("shop", "text", "text"),
                    service_module.ColumnInfo("amenity", "text", "text"),
                    service_module.ColumnInfo("building", "text", "text"),
                    service_module.ColumnInfo("landuse", "text", "text"),
                    service_module.ColumnInfo("tags", "USER-DEFINED", "hstore"),
                    service_module.ColumnInfo("way", "USER-DEFINED", "geometry"),
                ),
                estimated_rows=1000,
            ),
            service_module.PostGISTableInfo(
                schema="public",
                table="planet_osm_polygon",
                geom_col="way",
                geometry_type="GEOMETRY",
                srid=3857,
                columns=(
                    service_module.ColumnInfo("osm_id", "bigint", "int8"),
                    service_module.ColumnInfo("name", "text", "text"),
                    service_module.ColumnInfo("shop", "text", "text"),
                    service_module.ColumnInfo("amenity", "text", "text"),
                    service_module.ColumnInfo("building", "text", "text"),
                    service_module.ColumnInfo("landuse", "text", "text"),
                    service_module.ColumnInfo("tags", "USER-DEFINED", "hstore"),
                    service_module.ColumnInfo("way", "USER-DEFINED", "geometry"),
                ),
                estimated_rows=1000,
            ),
        )
    )


def test_extract_postgis_connection_config_from_user_context():
    config = service_module._extract_postgis_connection_config_from_sources(
        resolved_inputs={},
        user_context={
            "postgis_connection": {
                "source_type": "postgis",
                "host": "localhost",
                "port": "5433",
                "database": "osm_tehran",
                "user": "postgres",
                "password": "secret",
                "schemas": ["public"],
            }
        },
        metadata={},
    )

    assert config is not None
    assert config["host"] == "localhost"
    assert config["port"] == 5433
    assert config["database"] == "osm_tehran"
    assert config["user"] == "postgres"
    assert config["password"] == "secret"
    assert config["schemas"] == ["public"]


def test_extract_postgis_connection_config_supports_postgis_alias():
    config = service_module._extract_postgis_connection_config_from_sources(
        resolved_inputs={},
        user_context={
            "postgis": {
                "driver": "postgresql",
                "host": "localhost",
                "dbname": "osm_tehran",
                "username": "postgres",
                "schema": "public",
            }
        },
        metadata={},
    )

    assert config is not None
    assert config["database"] == "osm_tehran"
    assert config["user"] == "postgres"
    assert config["schemas"] == ["public"]


def test_service_builds_semantic_context_from_auto_discovered_postgis_schema(monkeypatch):
    calls = {}

    def fake_discover(connection_config):
        calls["connection_config"] = dict(connection_config)
        return _fake_schema_context()

    monkeypatch.setattr(
        service_module,
        "_discover_postgis_schema_context_from_connection_config",
        fake_discover,
    )

    context, error = service_module._extract_semantic_planning_context_from_sources(
        query=(
            "نزدیک‌ترین مرکز خرید به هر ایستگاه مترو را پیدا کن "
            "و ۲۰ مورد اول را روی نقشه و جدول نمایش بده"
        ),
        resolved_inputs={},
        user_context={
            "postgis_connection": {
                "source_type": "postgis",
                "host": "localhost",
                "port": 5433,
                "database": "osm_tehran",
                "user": "postgres",
                "password": "secret",
                "schemas": ["public"],
            }
        },
        metadata={},
    )

    assert error is None
    assert calls["connection_config"]["database"] == "osm_tehran"
    assert context is not None
    assert "metro_station" in context["detected_concepts"]
    assert "shopping_center" in context["detected_concepts"]
    assert "query_database" in context["recommended_ops"]
    assert "spatial_nearest" in context["recommended_ops"]
    assert "top_n" in context["recommended_ops"]
    assert context["requested_limit"] == 20
    assert context["guardrails"]["llm_must_not_generate_raw_sql"] is True


def test_auto_postgis_schema_discovery_error_is_non_fatal(monkeypatch):
    def fake_discover(connection_config):
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr(
        service_module,
        "_discover_postgis_schema_context_from_connection_config",
        fake_discover,
    )

    context, error = service_module._extract_semantic_planning_context_from_sources(
        query="پارک‌ها را پیدا کن",
        resolved_inputs={},
        user_context={
            "postgis_connection": {
                "source_type": "postgis",
                "host": "localhost",
                "database": "osm_tehran",
            }
        },
        metadata={},
    )

    assert context is None
    assert error is not None
    assert "database is unavailable" in error
