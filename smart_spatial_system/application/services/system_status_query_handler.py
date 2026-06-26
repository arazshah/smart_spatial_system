"""
System status query handler.

Handles simple health/runtime/status questions before they enter geospatial
planning or plugin execution pipelines.
"""

from __future__ import annotations

import os
from typing import Any, Callable


JsonSafe = Callable[[Any], Any]


def is_system_status_query(
    query: str,
    llm_intent: Any | None = None,
) -> bool:
    text = str(query or "").strip().lower()

    if not text:
        return False

    if isinstance(llm_intent, dict):
        intent_name = str(llm_intent.get("intent_name") or "").lower()
    else:
        intent_name = str(getattr(llm_intent, "intent_name", "") or "").lower()

    system_tokens = [
        "وضعیت سیستم",
        "سلامت سیستم",
        "وضعیت سرویس",
        "سلامت سرویس",
        "سیستم را بررسی",
        "بررسی سیستم",
        "health",
        "system status",
        "service status",
        "runtime status",
    ]

    if any(token in text for token in system_tokens):
        return True

    if intent_name in {"system_status", "health_check", "runtime_status"}:
        return True

    return False


def _collect_plugin_ids(context: Any) -> list[str]:
    plugin_ids: list[str] = []

    try:
        registry = getattr(context, "registry", None)
        bindings = getattr(registry, "bindings", None)

        if callable(bindings):
            bindings = bindings()

        if isinstance(bindings, dict):
            iterable = bindings.values()
        elif bindings is None:
            iterable = []
        else:
            iterable = bindings

        for binding in iterable:
            plugin_id = (
                getattr(binding, "plugin_id", None)
                or getattr(binding, "plugin_name", None)
                or getattr(binding, "source_plugin", None)
            )

            if plugin_id:
                plugin_ids.append(str(plugin_id))
    except Exception:
        plugin_ids = []

    return sorted(set(plugin_ids))


def _build_runtime_snapshot(context: Any) -> dict[str, Any]:
    capability_names = sorted(context._enabled_capability_names())
    plugin_ids = _collect_plugin_ids(context)
    config = getattr(context, "config", None)
    registry = getattr(context, "registry", None)

    return {
        "llm": {
            "provider": os.getenv("LLM_PROVIDER", "not_configured"),
            "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL"),
            "fast_model": os.getenv("LLM_FAST_MODEL"),
            "strong_model": os.getenv("LLM_STRONG_MODEL"),
            "default_model": os.getenv("LLM_DEFAULT_MODEL") or os.getenv("LLM_MODEL"),
            "temperature": os.getenv("LLM_TEMPERATURE"),
            "timeout_seconds": os.getenv("LLM_TIMEOUT_SECONDS"),
            "api_key_configured": bool(
                os.getenv("OPENAI_API_KEY")
                or os.getenv("AVALAI_API_KEY")
                or os.getenv("LLM_API_KEY")
            ),
        },
        "plugins": {
            "module_names": list(getattr(config, "plugin_modules", []) or []),
            "plugin_ids": plugin_ids,
            "capabilities": capability_names,
            "capability_count": len(capability_names),
            "enabled_capabilities": capability_names,
            "enabled_capability_count": len(capability_names),
            "disabled_plugin_ids": sorted(context._disabled_plugin_ids()),
            "skipped_plugins": list(getattr(registry, "skipped_plugins", []) or []),
        },
        "runtime": {
            "resolve_upload_refs_with_plugins": getattr(
                config,
                "resolve_upload_refs_with_plugins",
                None,
            ),
            "raster_loader_plugin_module": getattr(
                config,
                "raster_loader_plugin_module",
                None,
            ),
            "vector_loader_plugin_module": getattr(
                config,
                "vector_loader_plugin_module",
                None,
            ),
        },
    }


def _get_runtime_status(context: Any) -> dict[str, Any]:
    try:
        runtime_diagnostics = getattr(context, "get_runtime_diagnostics", None)

        if callable(runtime_diagnostics):
            return runtime_diagnostics()

        return _build_runtime_snapshot(context)
    except Exception as exc:
        return {
            "error": str(exc),
        }


def _get_health_status(context: Any) -> dict[str, Any]:
    try:
        return context.get_health()
    except Exception as exc:
        return {
            "status": "unknown",
            "error": str(exc),
        }


def try_handle_system_status_query(
    context: Any,
    *,
    query: str,
    inputs: dict[str, Any],
    final_request_id: str,
    final_metadata: dict[str, Any],
    json_safe: JsonSafe,
    band_map: dict[str, int] | None = None,
    user_context: dict[str, Any] | None = None,
    llm_intent: Any | None = None,
) -> dict[str, Any] | None:
    """
    Answer simple system-status/runtime queries directly.

    This prevents general health/status questions from being routed into
    geospatial planning pipelines such as NDVI/raster workflows.
    """
    if not is_system_status_query(query, llm_intent):
        return None

    health = _get_health_status(context)
    runtime = _get_runtime_status(context)

    plugins = runtime.get("plugins", {}) if isinstance(runtime, dict) else {}
    llm = runtime.get("llm", {}) if isinstance(runtime, dict) else {}

    enabled_count = plugins.get("enabled_capability_count")
    plugin_ids = plugins.get("plugin_ids") or []
    module_names = plugins.get("module_names") or []
    plugin_count = len(plugin_ids) if plugin_ids else len(module_names)

    answer = (
        "سیستم فعال است و سرویس ارکستریتور آماده پاسخ‌گویی است. "
        f"تعداد قابلیت‌های فعال: {enabled_count if enabled_count is not None else 'نامشخص'}، "
        f"تعداد افزونه‌های بارگذاری‌شده: {plugin_count}. "
        "اتصال LLM نیز در تنظیمات runtime قابل بررسی است."
    )

    response = {
        "ok": True,
        "status": "succeeded",
        "request_id": final_request_id,
        "query": query,
        "answer": answer,
        "message": answer,
        "summary": {
            "service_status": health.get("status") if isinstance(health, dict) else None,
            "enabled_capability_count": enabled_count,
            "plugin_count": plugin_count,
            "llm_provider": llm.get("provider") if isinstance(llm, dict) else None,
            "llm_model": llm.get("default_model") if isinstance(llm, dict) else None,
            "llm_api_key_configured": (
                llm.get("api_key_configured") if isinstance(llm, dict) else None
            ),
        },
        "outputs": {},
        "layers": [],
        "result": {
            "type": "system_status",
            "health": health,
            "runtime": runtime,
        },
        "warnings": [],
        "next_actions": [
            "برای مشاهده جزئیات افزونه‌ها از بخش Plugin Manager استفاده کنید.",
            "برای تست اتصال LLM از مسیر /settings/llm/smoke-test استفاده کنید.",
        ],
        "metadata": json_safe(final_metadata),
    }

    context._remember(
        request_id=final_request_id,
        record={
            "request_id": final_request_id,
            "query": query,
            "inputs": json_safe(inputs),
            "band_map": json_safe(band_map or {}),
            "user_context": json_safe(user_context or {}),
            "metadata": json_safe(final_metadata),
            "production_response": json_safe(response),
        },
    )

    return json_safe(response)
