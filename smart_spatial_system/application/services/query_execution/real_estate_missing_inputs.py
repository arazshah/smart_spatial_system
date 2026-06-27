from __future__ import annotations

from collections.abc import Callable
from typing import Any

from smart_spatial_system.application.services.query_execution.real_estate_classifier import (
    has_any_real_estate_payload as default_has_any_real_estate_payload,
    is_real_estate_analysis_query as default_is_real_estate_analysis_query,
)


def try_handle_missing_real_estate_inputs(
    *,
    query: str,
    inputs: dict[str, Any],
    resolved_inputs: dict[str, Any],
    final_request_id: str,
    final_metadata: dict[str, Any],
    remember: Callable[..., Any],
    attach_request: Callable[[str, str], Any] | None,
    json_safe: Callable[[Any], Any],
    band_map: dict[str, int] | None = None,
    user_context: dict[str, Any] | None = None,
    llm_intent: Any | None = None,
    is_real_estate_analysis_query: Callable[[str, Any | None], bool] | None = None,
    has_any_real_estate_payload: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any] | None:
    """
    Return a controlled response for complex real-estate analysis requests
    when no useful spatial inputs were provided.

    The classifier callbacks are optional for backward compatibility. If they
    are not provided, the real-estate classifier module is used directly.
    """
    analysis_query_checker = (
        is_real_estate_analysis_query or default_is_real_estate_analysis_query
    )
    payload_checker = (
        has_any_real_estate_payload or default_has_any_real_estate_payload
    )

    if not analysis_query_checker(query, llm_intent):
        return None

    if payload_checker(resolved_inputs):
        return None

    required_layers = [
        "لایه املاک یا نقاط/پلیگون‌های ملک‌ها",
        "لایه POI شامل ایستگاه‌های مترو و مراکز خرید",
        "لایه خیابان‌های اصلی یا شبکه معابر",
        "لایه‌های ریسک سیل، زلزله و آتش‌سوزی",
        "در صورت نیاز، لایه محدوده مجاز ساخت‌وساز یا کاربری اراضی",
    ]

    answer = (
        "برای انجام تحلیل و رتبه‌بندی املاک، داده مکانی کافی ارسال نشده است. "
        "لطفاً حداقل لایه املاک و لایه‌های مرجع مانند مترو/مرکز خرید، خیابان‌های اصلی "
        "و ریسک‌ها را در ورودی‌ها اضافه کنید."
    )

    response = {
        "ok": False,
        "status": "failed",
        "request_id": final_request_id,
        "query": query,
        "answer": answer,
        "message": answer,
        "outputs": {},
        "layers": [],
        "documents": [],
        "trace": [],
        "result": {
            "type": "missing_required_inputs",
            "domain": "real_estate_spatial_ranking",
            "required_layers": required_layers,
        },
        "confidence": {
            "level": None,
            "score": None,
            "llm_action": "input_validation_guard",
            "is_ambiguous": False,
            "competitive_gap": None,
        },
        "audit_ref": {
            "request_id": final_request_id,
            "query_hash": None,
            "status": "failed",
            "plan_steps": 0,
        },
        "warnings": [
            "درخواست تحلیل املاک تشخیص داده شد، اما ورودی مکانی کافی وجود ندارد.",
            "برای جلوگیری از اجرای pipeline اشتباه، برنامه‌ریز مکانی اجرا نشد.",
        ],
        "next_actions": [
            "لایه املاک را به صورت GeoJSON/Vector اضافه کنید.",
            "لایه ایستگاه‌های مترو و مراکز خرید را اضافه کنید.",
            "لایه خیابان‌های اصلی و لایه‌های ریسک را اضافه کنید.",
            "سپس درخواست رتبه‌بندی و تولید گزارش را دوباره اجرا کنید.",
        ],
        "metadata": json_safe(final_metadata),
    }

    resolved_project_id = str(final_metadata.get("project_id") or "").strip() or None

    remember(
        request_id=final_request_id,
        record={
            "request_id": final_request_id,
            "query": query,
            "inputs": json_safe(resolved_inputs),
            "original_inputs": json_safe(inputs),
            "band_map": json_safe(band_map or {}),
            "user_context": json_safe(user_context or {}),
            "metadata": json_safe(final_metadata),
            "project_id": resolved_project_id,
            "production_response": json_safe(response),
        },
    )

    if resolved_project_id and attach_request is not None:
        try:
            attach_request(
                resolved_project_id,
                final_request_id,
            )
        except Exception:
            pass

    return json_safe(response)
