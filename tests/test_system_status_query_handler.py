from types import SimpleNamespace

from smart_spatial_system.application.services.system_status_query_handler import (
    is_system_status_query,
    try_handle_system_status_query,
)


def _json_safe(value):
    return value


class FakeRegistry:
    skipped_plugins = ["broken_plugin"]

    def bindings(self):
        return [
            SimpleNamespace(plugin_id="plugin_a"),
            SimpleNamespace(plugin_name="plugin_b"),
        ]


class FakeContext:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            plugin_modules=["plugins.a", "plugins.b"],
            resolve_upload_refs_with_plugins=True,
            raster_loader_plugin_module="plugins.raster_loader",
            vector_loader_plugin_module="plugins.vector_loader",
        )
        self.registry = FakeRegistry()
        self.remembered = []

    def get_health(self):
        return {
            "status": "ok",
            "service": "FakeService",
        }

    def _enabled_capability_names(self):
        return ["cap_b", "cap_a"]

    def _disabled_plugin_ids(self):
        return ["disabled_plugin"]

    def _remember(self, *, request_id, record):
        self.remembered.append(
            {
                "request_id": request_id,
                "record": record,
            }
        )


def test_is_system_status_query_matches_text_tokens() -> None:
    assert is_system_status_query("وضعیت سیستم را بگو") is True
    assert is_system_status_query("health") is True
    assert is_system_status_query("system status") is True


def test_is_system_status_query_matches_intent_dict() -> None:
    assert is_system_status_query(
        "anything",
        {"intent_name": "system_status"},
    ) is True


def test_is_system_status_query_returns_false_for_geospatial_query() -> None:
    assert is_system_status_query("ndvi vegetation extraction") is False


def test_try_handle_system_status_query_returns_none_for_non_status_query() -> None:
    context = FakeContext()

    result = try_handle_system_status_query(
        context,
        query="ndvi vegetation extraction",
        inputs={},
        final_request_id="req-1",
        final_metadata={},
        json_safe=_json_safe,
    )

    assert result is None
    assert context.remembered == []


def test_try_handle_system_status_query_builds_response_and_remembers() -> None:
    context = FakeContext()

    result = try_handle_system_status_query(
        context,
        query="وضعیت سیستم را بگو",
        inputs={"x": 1},
        final_request_id="req-status-1",
        final_metadata={"source": "test"},
        band_map={"red": 1},
        user_context={"user": "demo"},
        json_safe=_json_safe,
    )

    assert result is not None
    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["request_id"] == "req-status-1"
    assert result["result"]["type"] == "system_status"
    assert result["summary"]["service_status"] == "ok"
    assert result["summary"]["enabled_capability_count"] == 2
    assert result["summary"]["plugin_count"] == 2
    assert result["layers"] == []
    assert result["outputs"] == {}

    assert len(context.remembered) == 1
    assert context.remembered[0]["request_id"] == "req-status-1"
    assert context.remembered[0]["record"]["production_response"]["result"]["type"] == (
        "system_status"
    )


def test_try_handle_system_status_query_uses_runtime_diagnostics_when_available() -> None:
    class RuntimeContext(FakeContext):
        def get_runtime_diagnostics(self):
            return {
                "llm": {
                    "provider": "test-provider",
                    "default_model": "test-model",
                    "api_key_configured": True,
                },
                "plugins": {
                    "enabled_capability_count": 5,
                    "plugin_ids": ["p1", "p2", "p3"],
                },
            }

    context = RuntimeContext()

    result = try_handle_system_status_query(
        context,
        query="runtime status",
        inputs={},
        final_request_id="req-runtime-1",
        final_metadata={},
        json_safe=_json_safe,
    )

    assert result is not None
    assert result["summary"]["enabled_capability_count"] == 5
    assert result["summary"]["plugin_count"] == 3
    assert result["summary"]["llm_provider"] == "test-provider"
    assert result["summary"]["llm_model"] == "test-model"
    assert result["summary"]["llm_api_key_configured"] is True


def test_try_handle_system_status_query_includes_disabled_plugin_ids() -> None:
    context = FakeContext()

    result = try_handle_system_status_query(
        context,
        query="system status",
        inputs={},
        final_request_id="req-disabled-plugins",
        final_metadata={},
        json_safe=_json_safe,
    )

    assert result is not None
    assert result["result"]["runtime"]["plugins"]["disabled_plugin_ids"] == [
        "disabled_plugin"
    ]
