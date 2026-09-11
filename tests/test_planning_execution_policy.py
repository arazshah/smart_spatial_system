from types import SimpleNamespace

from smart_spatial_system.application.services.planning_execution_policy import (
    is_kernel_execution_enabled,
)


def _clear_kernel_env(monkeypatch) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)


def test_kernel_execution_request_disable_always_wins(monkeypatch) -> None:
    _clear_kernel_env(monkeypatch)

    config = SimpleNamespace(
        enable_kernel_execution=True,
        allow_request_kernel_execution=True,
    )

    assert is_kernel_execution_enabled(
        config=config,
        metadata={"enable_kernel_execution": False},
    ) is False


def test_kernel_execution_request_enable_requires_allow_flag(monkeypatch) -> None:
    _clear_kernel_env(monkeypatch)

    config = SimpleNamespace(
        enable_kernel_execution=False,
        allow_request_kernel_execution=False,
    )

    assert is_kernel_execution_enabled(
        config=config,
        metadata={"enable_kernel_execution": True},
    ) is False


def test_kernel_execution_request_enable_is_allowed_by_config(monkeypatch) -> None:
    _clear_kernel_env(monkeypatch)

    config = SimpleNamespace(
        enable_kernel_execution=False,
        allow_request_kernel_execution=True,
    )

    assert is_kernel_execution_enabled(
        config=config,
        metadata={"enable_kernel_execution": True},
    ) is True


def test_kernel_execution_nested_planning_request_flag(monkeypatch) -> None:
    _clear_kernel_env(monkeypatch)

    config = SimpleNamespace(
        enable_kernel_execution=False,
        allow_request_kernel_execution=True,
    )

    assert is_kernel_execution_enabled(
        config=config,
        metadata={"planning": {"use_kernel_execution": "yes"}},
    ) is True


def test_kernel_execution_environment_override(monkeypatch) -> None:
    _clear_kernel_env(monkeypatch)
    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "true")

    config = SimpleNamespace(
        enable_kernel_execution=False,
        allow_request_kernel_execution=False,
    )

    assert is_kernel_execution_enabled(config=config) is True


def test_kernel_execution_config_default(monkeypatch) -> None:
    _clear_kernel_env(monkeypatch)

    config = SimpleNamespace(
        enable_kernel_execution=True,
        allow_request_kernel_execution=False,
    )

    assert is_kernel_execution_enabled(config=config) is True


def test_query_spec_planning_enabled_defaults_false(monkeypatch) -> None:
    monkeypatch.delenv("QUERY_SPEC_PLANNING_ENABLED", raising=False)

    from smart_spatial_system.application.services.planning_execution_policy import (
        is_query_spec_planning_enabled,
    )

    assert is_query_spec_planning_enabled() is False


def test_query_spec_planning_enabled_truthy_values(monkeypatch) -> None:
    from smart_spatial_system.application.services.planning_execution_policy import (
        is_query_spec_planning_enabled,
    )

    for value in ("1", "true", "yes", "on", " TRUE "):
        monkeypatch.setenv("QUERY_SPEC_PLANNING_ENABLED", value)
        assert is_query_spec_planning_enabled() is True


def test_query_spec_planning_enabled_non_truthy_values(monkeypatch) -> None:
    from smart_spatial_system.application.services.planning_execution_policy import (
        is_query_spec_planning_enabled,
    )

    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("QUERY_SPEC_PLANNING_ENABLED", value)
        assert is_query_spec_planning_enabled() is False


def test_query_spec_planning_enabled_honors_config_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("QUERY_SPEC_PLANNING_ENABLED", raising=False)

    from smart_spatial_system.application.services.planning_execution_policy import (
        is_query_spec_planning_enabled,
    )

    assert is_query_spec_planning_enabled(
        config=SimpleNamespace(query_spec_planning_enabled=True)
    ) is True
    assert is_query_spec_planning_enabled(
        config=SimpleNamespace(query_spec_planning_enabled=False)
    ) is False


def test_query_spec_planning_enabled_env_overrides_config(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_SPEC_PLANNING_ENABLED", "true")

    from smart_spatial_system.application.services.planning_execution_policy import (
        is_query_spec_planning_enabled,
    )

    # Deployment-level env override wins even when config says otherwise -
    # REFACTOR_PLAN.md Phase 3: "Environment variables may override config,
    # but should not be the only source of truth."
    assert is_query_spec_planning_enabled(
        config=SimpleNamespace(query_spec_planning_enabled=False)
    ) is True
