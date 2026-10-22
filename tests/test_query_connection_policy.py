"""Who may choose the database a /query talks to (connection_policy)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from smart_spatial_system.application.services.query_execution import (
    connection_policy as policy,
)
from smart_spatial_system.application.services.query_execution.planning_execution import (
    execute_query_spec_planning,
)
from smart_spatial_system.application.services.query_execution.postgis_planning_context import (
    _build_query_spec_runtime_inputs,
    _discover_postgis_schema_context_from_connection_config,
    _extract_postgis_connection_config_from_sources,
    _extract_semantic_planning_context_from_sources,
)

CLIENT_CONNECTION = {"host": "10.0.0.5", "database": "gis", "user": "u", "password": "hunter2"}


@pytest.fixture
def config(monkeypatch):
    data = {"profiles": {"local": {"host": "db.internal", "port": 5432, "database": "osm",
                                  "user": "ro", "password_env": "TEST_PG_PASSWORD"}},
            "default_profile": "local"}
    monkeypatch.setattr(policy, "_plugin_config", lambda: data)
    return data


def test_default_refuses_client_connection_details_everywhere(config):
    for sources in ({"user_context": {"postgis_connection": CLIENT_CONNECTION}},
                    {"metadata": {"postgis": CLIENT_CONNECTION}},
                    {"resolved_inputs": {"database_connection": CLIENT_CONNECTION}},
                    {"resolved_inputs": CLIENT_CONNECTION}):
        with pytest.raises(policy.ClientConnectionNotAllowed) as err:
            _build_query_spec_runtime_inputs(
                **{"resolved_inputs": {}, "user_context": {}, "metadata": {}, **sources})
        assert "hunter2" not in str(err.value) and "10.0.0.5" not in str(err.value)
        assert "profile" in str(err.value)
    with pytest.raises(policy.ClientConnectionNotAllowed):
        _extract_postgis_connection_config_from_sources(
            resolved_inputs={}, metadata={}, user_context={"postgis_connection": {"dsn": "postgresql://u:p@h/db"}})


def test_semantic_context_never_connects_to_a_client_host(config, monkeypatch):
    def must_not_connect(_config):
        raise AssertionError("connected to a client-chosen host")

    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution.postgis_planning_context."
        "_discover_postgis_schema_context_from_connection_config", must_not_connect)
    context, error = _extract_semantic_planning_context_from_sources(
        query="x", resolved_inputs={}, metadata={}, user_context={"postgis_connection": CLIENT_CONNECTION})
    assert context is None and "profile" in error and "hunter2" not in error


def test_stray_connection_keys_in_inputs_are_scrubbed(config):
    inputs, injected = _build_query_spec_runtime_inputs(
        resolved_inputs={"host": "evil", "password": "p", "table": "roads", "limit": 5},
        user_context={}, metadata={})
    assert inputs == {"table": "roads", "limit": 5} and injected is False


def test_profile_only_connection_is_always_accepted(config):
    inputs, injected = _build_query_spec_runtime_inputs(
        resolved_inputs={}, metadata={},
        user_context={"postgis_connection": {"profile": "local", "schemas": ["public"]}})
    assert injected is True and inputs["profile"] == "local" and inputs["schemas"] == ["public"]
    assert "host" not in inputs and "password" not in inputs


def test_a_profile_cannot_smuggle_connection_details(config):
    with pytest.raises(policy.ClientConnectionNotAllowed):
        _build_query_spec_runtime_inputs(
            resolved_inputs={}, metadata={},
            user_context={"postgis_connection": {"profile": "local", "host": "evil", "database": "x"}})


def test_opt_in_restores_client_supplied_connections(config):
    config["allow_client_connection"] = True
    inputs, injected = _build_query_spec_runtime_inputs(
        resolved_inputs={}, metadata={}, user_context={"postgis_connection": CLIENT_CONNECTION})
    assert injected is True and inputs["host"] == "10.0.0.5" and inputs["password"] == "hunter2"


def test_profile_schema_discovery_uses_server_side_settings(config, monkeypatch):
    monkeypatch.setenv("TEST_PG_PASSWORD", "from-env")
    seen = {}

    class _Conn:
        def close(self):
            pass

    def connect(*args, **kwargs):
        seen.update(kwargs)
        return _Conn()

    monkeypatch.setitem(__import__("sys").modules, "psycopg2", SimpleNamespace(connect=connect))
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution.postgis_planning_context."
        "_discover_postgis_schema", lambda conn, schemas=None: "SCHEMA")
    result = _discover_postgis_schema_context_from_connection_config({"profile": "local", "schemas": ["public"]})
    assert result == "SCHEMA"
    assert seen["host"] == "db.internal" and seen["password"] == "from-env" and seen["dbname"] == "osm"
    with pytest.raises(ValueError, match="Unknown PostGIS profile"):
        policy.resolve_profile_connection("nope")


def test_the_model_cannot_choose_connection_parameters(config):
    operations = [
        SimpleNamespace(op="query_database", params={
            "table": "roads", "host": "evil", "password": "p", "dsn": "postgresql://x", "profile": "admin"}),
        SimpleNamespace(op="load_postgis_layer", params={"table": "t", "user": "root"}),
        SimpleNamespace(op="buffer", params={"host": "kept-for-other-ops"}),
    ]
    spec = SimpleNamespace(operations=operations)
    assert policy.strip_llm_connection_params(spec) == 5
    assert operations[0].params == {"table": "roads"} and operations[1].params == {"table": "t"}
    assert operations[2].params == {"host": "kept-for-other-ops"}


def test_planning_removes_model_connection_params_before_injecting_trusted_ones(config):
    spec = SimpleNamespace(operations=[SimpleNamespace(
        op="query_database", params={"table": "roads", "host": "evil", "profile": "admin"})])
    captured = {}

    def enrich(query_spec, runtime_inputs):
        captured["params_before"] = dict(query_spec.operations[0].params)
        captured["inputs"] = runtime_inputs

    class _Runner:
        def run(self, *a, **k):
            return "result"

    final_metadata: dict = {}
    execute_query_spec_planning(
        query="q", planning_context={}, resolved_inputs={"profile": "local"},
        user_context={}, metadata={}, final_metadata=final_metadata,
        build_runtime_inputs=lambda **kw: ({"profile": "local"}, True),
        enrich_query_database_params=enrich,
        build_enabled_registry_view=lambda: None,
        kernel_execution_enabled=lambda **kw: False,
        llm_client_factory=lambda: None,
        query_spec_generator_cls=lambda client: SimpleNamespace(generate=lambda q, context=None: spec),
        planning_runner_factory=lambda view: _Runner(),
        query_spec_contract_validator=lambda s: None,
    )
    assert captured["params_before"] == {"table": "roads"}
    assert final_metadata["llm_connection_params_removed"] == 2
