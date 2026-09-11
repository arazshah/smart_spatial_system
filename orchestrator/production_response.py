"""
orchestrator.production_response

Production response builder for Smart Spatial System.

This module converts internal execution artifacts into a stable user-facing
response envelope.

It is intentionally deterministic and does not call an LLM.

Typical input artifacts:
    - runner result dict
    - audit_record
    - response dict
    - plan object
    - outputs / outputs_summary

Typical output:
    {
        "status": "success",
        "request_id": "...",
        "query_hash": "...",
        "answer": "...",
        "outputs": {...},
        "confidence": {...},
        "audit_ref": {...},
        "warnings": [],
        "next_actions": [],
        "metadata": {...}
    }
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

VALID_RESPONSE_STATUSES = {
    "success",
    "partial_success",
    "failed",
}


@dataclass(frozen=True)
class ProductionResponseConfig:
    """
    Configuration for user-facing response generation.
    """

    include_outputs: bool = True
    include_metadata: bool = True
    include_debug: bool = False
    language: str = "fa"

    max_warnings: int = 20
    max_next_actions: int = 20

    low_confidence_levels: tuple[str, ...] = ("low",)
    ambiguous_warning_enabled: bool = True

    def __post_init__(self) -> None:
        if self.max_warnings < 0:
            raise ValueError("max_warnings must be >= 0.")

        if self.max_next_actions < 0:
            raise ValueError("max_next_actions must be >= 0.")

        if self.language not in {"fa", "en"}:
            raise ValueError("language must be one of: fa, en.")


@dataclass(frozen=True)
class ProductionResponse:
    """
    Stable user-facing response envelope.
    """

    status: str
    request_id: str | None
    query_hash: str | None

    answer: str

    outputs: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] = field(default_factory=dict)
    audit_ref: dict[str, Any] = field(default_factory=dict)

    warnings: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProductionResponseBuilder:
    """
    Build production-ready responses from internal orchestration outputs.
    """

    def __init__(
        self,
        config: ProductionResponseConfig | None = None,
    ) -> None:
        self.config = config or ProductionResponseConfig()

    def build(
        self,
        *,
        run_result: dict[str, Any] | None = None,
        audit_record: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        plan: Any | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProductionResponse:
        """
        Build a production response.

        Args:
            run_result:
                Full result returned by natural query runner.
            audit_record:
                Execution audit record.
            response:
                Internal response dict.
            plan:
                Plan object, if available.
            outputs:
                Explicit outputs override.
            error:
                Explicit error message/exception.
            metadata:
                Additional user-facing metadata.
        """
        artifacts = self._normalize_artifacts(
            run_result=run_result,
            audit_record=audit_record,
            response=response,
            plan=plan,
            outputs=outputs,
        )

        final_audit = artifacts["audit_record"]
        final_response = artifacts["response"]
        final_plan = artifacts["plan"]
        final_outputs = artifacts["outputs"]

        status = self._status(
            audit_record=final_audit,
            response=final_response,
            error=error,
        )

        request_id = self._request_id(
            audit_record=final_audit,
            response=final_response,
        )

        query_hash = self._query_hash(
            audit_record=final_audit,
            response=final_response,
        )

        outputs_summary = self._outputs_summary(
            audit_record=final_audit,
            response=final_response,
            outputs=final_outputs,
        )

        confidence = self._confidence(
            audit_record=final_audit,
            plan=final_plan,
        )

        audit_ref = self._audit_ref(
            audit_record=final_audit,
            plan=final_plan,
            query_hash=query_hash,
            status=status,
        )

        warnings = self._warnings(
            status=status,
            confidence=confidence,
            outputs_summary=outputs_summary,
            error=error,
            audit_record=final_audit,
        )

        next_actions = self._next_actions(
            status=status,
            confidence=confidence,
            warnings=warnings,
            outputs_summary=outputs_summary,
        )

        answer = self._answer(
            status=status,
            outputs_summary=outputs_summary,
            confidence=confidence,
            error=error,
        )

        safe_outputs = {}

        if self.config.include_outputs:
            safe_outputs = _json_safe(final_outputs or {})

            if not safe_outputs and outputs_summary:
                safe_outputs = {
                    "summary": _json_safe(outputs_summary),
                }

        response_metadata = {}

        if self.config.include_metadata:
            response_metadata = {
                "builder": "ProductionResponseBuilder",
                "language": self.config.language,
            }

            response_metadata.update(dict(metadata or {}))

            if self.config.include_debug:
                response_metadata["debug"] = {
                    "raw_response": _json_safe(final_response),
                    "outputs_summary": _json_safe(outputs_summary),
                }

        return ProductionResponse(
            status=status,
            request_id=request_id,
            query_hash=query_hash,
            answer=answer,
            outputs=safe_outputs,
            confidence=confidence,
            audit_ref=audit_ref,
            warnings=warnings[: self.config.max_warnings],
            next_actions=next_actions[: self.config.max_next_actions],
            metadata=response_metadata,
        )

    def build_dict(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Build and return JSON-like dict.

        The dataclass model intentionally keeps the historical production
        response shape.  The dict contract is enriched here for API/frontend
        consumers so natural-query responses expose the same top-level fields
        as direct and planning responses.
        """
        production_response = self.build(**kwargs).to_dict()

        normalized_artifacts = self._normalize_artifacts(
            run_result=kwargs.get("run_result"),
            audit_record=kwargs.get("audit_record"),
            response=kwargs.get("response"),
            plan=kwargs.get("plan"),
            outputs=kwargs.get("outputs"),
        )

        final_audit = normalized_artifacts["audit_record"]
        final_response = normalized_artifacts["response"]
        final_outputs = normalized_artifacts["outputs"]

        raw_status = production_response.get("status")
        production_response.setdefault(
            "ok",
            raw_status not in {"failed", "error", "failure"},
        )
        production_response.setdefault("message", production_response.get("answer"))

        layers: list[Any] = []
        response_map = final_response.get("map") if isinstance(final_response, dict) else None
        if isinstance(final_response.get("layers"), list):
            layers = final_response["layers"]
        elif isinstance(final_outputs.get("layers"), list):
            layers = final_outputs["layers"]
        elif isinstance(response_map, dict) and isinstance(response_map.get("layers"), list):
            layers = response_map["layers"]

        documents: list[Any] = []
        if isinstance(final_response.get("documents"), list):
            documents = final_response["documents"]
        elif isinstance(final_outputs.get("documents"), list):
            documents = final_outputs["documents"]

        artifacts: list[Any] = []
        if isinstance(final_response.get("artifacts"), list):
            artifacts = final_response["artifacts"]
        elif isinstance(final_outputs.get("artifacts"), list):
            artifacts = final_outputs["artifacts"]

        trace: list[Any] = []
        if isinstance(final_response.get("trace"), list):
            trace = final_response["trace"]
        elif isinstance(final_audit.get("trace"), list):
            trace = final_audit["trace"]

        steps: list[Any] = []
        if isinstance(final_response.get("steps"), list):
            steps = final_response["steps"]
        else:
            steps = trace

        production_response.setdefault("layers", _json_safe(layers))
        production_response.setdefault("documents", _json_safe(documents))
        production_response.setdefault("artifacts", _json_safe(artifacts))
        production_response.setdefault("trace", _json_safe(trace))
        production_response.setdefault("steps", _json_safe(steps))

        return production_response

    @staticmethod
    def _normalize_artifacts(
        *,
        run_result: dict[str, Any] | None,
        audit_record: dict[str, Any] | None,
        response: dict[str, Any] | None,
        plan: Any | None,
        outputs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        run_result = run_result or {}

        final_audit = audit_record or run_result.get("audit_record") or {}
        final_response = response or run_result.get("response") or {}
        final_plan = plan or run_result.get("plan")
        final_outputs = (
            outputs
            if outputs is not None
            else run_result.get("outputs")
            or final_response.get("outputs")
            or {}
        )

        if not isinstance(final_audit, dict):
            final_audit = _object_to_dict(final_audit)

        if not isinstance(final_response, dict):
            final_response = _object_to_dict(final_response)

        if not isinstance(final_outputs, dict):
            final_outputs = {
                "result": final_outputs,
            }

        return {
            "audit_record": final_audit,
            "response": final_response,
            "plan": final_plan,
            "outputs": final_outputs,
        }

    @staticmethod
    def _status(
        *,
        audit_record: dict[str, Any],
        response: dict[str, Any],
        error: str | Exception | None,
    ) -> str:
        if error is not None:
            return "failed"

        raw_status = (
            response.get("status")
            or audit_record.get("status")
            or "success"
        )

        if raw_status in VALID_RESPONSE_STATUSES:
            return raw_status

        if raw_status in {"error", "failure"}:
            return "failed"

        return "success"

    @staticmethod
    def _request_id(
        *,
        audit_record: dict[str, Any],
        response: dict[str, Any],
    ) -> str | None:
        return (
            response.get("request_id")
            or audit_record.get("request_id")
        )

    @staticmethod
    def _query_hash(
        *,
        audit_record: dict[str, Any],
        response: dict[str, Any],
    ) -> str | None:
        return (
            response.get("query_hash")
            or audit_record.get("query_hash")
        )

    @staticmethod
    def _outputs_summary(
        *,
        audit_record: dict[str, Any],
        response: dict[str, Any],
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        summary = (
            audit_record.get("outputs_summary")
            or response.get("outputs_summary")
            or {}
        )

        if isinstance(summary, dict) and summary:
            return summary

        if outputs:
            return _summarize_outputs(outputs)

        return {}

    @staticmethod
    def _confidence(
        *,
        audit_record: dict[str, Any],
        plan: Any | None,
    ) -> dict[str, Any]:
        router_decision = audit_record.get("router_decision")

        if not isinstance(router_decision, dict):
            router_decision = {}

        score = router_decision.get("top_score")
        level = router_decision.get("level")
        llm_action = router_decision.get("llm_action")
        is_ambiguous = router_decision.get("is_ambiguous")
        competitive_gap = router_decision.get("competitive_gap")

        if score is None:
            score = _score_from_plan(plan)

        if level is None:
            level = _level_from_score(score)

        return {
            "level": level,
            "score": score,
            "llm_action": llm_action,
            "is_ambiguous": bool(is_ambiguous) if is_ambiguous is not None else False,
            "competitive_gap": competitive_gap,
        }

    @staticmethod
    def _audit_ref(
        *,
        audit_record: dict[str, Any],
        plan: Any | None,
        query_hash: str | None,
        status: str,
    ) -> dict[str, Any]:
        plan_summary = audit_record.get("plan_summary")

        plan_steps = None

        if isinstance(plan_summary, dict):
            nodes = plan_summary.get("nodes")

            if isinstance(nodes, list):
                plan_steps = len(nodes)

        if plan_steps is None:
            plan_steps = _plan_steps(plan)

        return {
            "request_id": audit_record.get("request_id"),
            "query_hash": query_hash,
            "status": audit_record.get("status") or status,
            "plan_steps": plan_steps,
        }

    def _warnings(
        self,
        *,
        status: str,
        confidence: dict[str, Any],
        outputs_summary: dict[str, Any],
        error: str | Exception | None,
        audit_record: dict[str, Any],
    ) -> list[str]:
        warnings: list[str] = []

        if status == "failed":
            warnings.append(self._text("execution_failed"))

        if error is not None:
            warnings.append(f"{self._text('error_detail')}: {error}")

        confidence_level = confidence.get("level")

        if confidence_level in self.config.low_confidence_levels:
            warnings.append(self._text("low_confidence"))

        if (
            self.config.ambiguous_warning_enabled
            and confidence.get("is_ambiguous") is True
        ):
            warnings.append(self._text("ambiguous_routing"))

        if status == "success" and not outputs_summary:
            warnings.append(self._text("no_output_summary"))

        audit_warnings = audit_record.get("warnings")

        if isinstance(audit_warnings, list):
            warnings.extend(str(item) for item in audit_warnings)

        return _dedupe_preserve_order(warnings)

    def _next_actions(
        self,
        *,
        status: str,
        confidence: dict[str, Any],
        warnings: list[str],
        outputs_summary: dict[str, Any],
    ) -> list[str]:
        actions: list[str] = []

        if status == "failed":
            actions.append(self._text("retry_or_review"))
            actions.append(self._text("check_inputs"))

        if confidence.get("level") in self.config.low_confidence_levels:
            actions.append(self._text("ask_user_clarification"))

        if confidence.get("is_ambiguous") is True:
            actions.append(self._text("review_route"))

        if outputs_summary:
            actions.append(self._text("inspect_outputs"))

        if warnings:
            actions.append(self._text("review_warnings"))

        return _dedupe_preserve_order(actions)

    def _answer(
        self,
        *,
        status: str,
        outputs_summary: dict[str, Any],
        confidence: dict[str, Any],
        error: str | Exception | None,
    ) -> str:
        if status == "failed":
            if error is not None:
                return self._text("failed_with_error").format(error=error)

            return self._text("failed")

        if status == "partial_success":
            return self._text("partial_success")

        if not outputs_summary:
            return self._text("success_generic")

        return self._summarize_outputs_for_user(outputs_summary)

    def _summarize_outputs_for_user(
        self,
        outputs_summary: dict[str, Any],
    ) -> str:
        if self.config.language == "en":
            return _english_output_answer(outputs_summary)

        return _persian_output_answer(outputs_summary)

    def _text(self, key: str) -> str:
        if self.config.language == "en":
            return _EN_TEXTS[key]

        return _FA_TEXTS[key]


_FA_TEXTS = {
    "execution_failed": "اجرای درخواست ناموفق بود.",
    "error_detail": "جزئیات خطا",
    "low_confidence": "اطمینان سیستم پایین است و ممکن است نتیجه نیاز به بازبینی داشته باشد.",
    "ambiguous_routing": "چند مسیر پردازشی نزدیک به هم تشخیص داده شد؛ نتیجه ممکن است نیاز به بازبینی داشته باشد.",
    "no_output_summary": "خلاصه خروجی در گزارش اجرا موجود نیست.",
    "retry_or_review": "درخواست را دوباره اجرا کنید یا گزارش خطا را بررسی کنید.",
    "check_inputs": "ورودی‌ها، پارامترها و داده‌های مکانی را بررسی کنید.",
    "ask_user_clarification": "در صورت نیاز از کاربر سؤال تکمیلی بپرسید.",
    "review_route": "مسیر انتخاب‌شده توسط Router را بازبینی کنید.",
    "inspect_outputs": "خروجی‌های تولیدشده را روی نقشه یا در ابزار تحلیلی بررسی کنید.",
    "review_warnings": "هشدارهای پاسخ را بررسی کنید.",
    "failed_with_error": "در اجرای درخواست خطا رخ داد: {error}",
    "failed": "در اجرای درخواست خطا رخ داد.",
    "partial_success": "درخواست به صورت ناقص انجام شد و بخشی از خروجی‌ها آماده است.",
    "success_generic": "درخواست با موفقیت انجام شد.",
}


_EN_TEXTS = {
    "execution_failed": "Request execution failed.",
    "error_detail": "Error detail",
    "low_confidence": "System confidence is low; the result may need review.",
    "ambiguous_routing": "Multiple close processing routes were detected; review may be needed.",
    "no_output_summary": "No output summary is available in the execution report.",
    "retry_or_review": "Retry the request or review the error report.",
    "check_inputs": "Check inputs, parameters, and geospatial data.",
    "ask_user_clarification": "Ask the user for clarification if needed.",
    "review_route": "Review the selected router path.",
    "inspect_outputs": "Inspect generated outputs on a map or analysis tool.",
    "review_warnings": "Review response warnings.",
    "failed_with_error": "Request execution failed: {error}",
    "failed": "Request execution failed.",
    "partial_success": "Request partially succeeded and some outputs are available.",
    "success_generic": "Request completed successfully.",
}


def _persian_output_answer(outputs_summary: dict[str, Any]) -> str:
    parts: list[str] = []

    for name, summary in outputs_summary.items():
        if not isinstance(summary, dict):
            continue

        kind = summary.get("kind")
        feature_count = summary.get("feature_count")
        cell_count = summary.get("cell_count")
        valid_cell_count = summary.get("valid_cell_count")
        index_name = summary.get("index_name")

        label = _friendly_output_name(name)

        if kind == "vector":
            if isinstance(feature_count, int):
                parts.append(f"{feature_count} عارضه برداری برای «{label}» تولید شد.")
            else:
                parts.append(f"خروجی برداری «{label}» تولید شد.")
            continue

        if kind == "raster":
            if index_name:
                parts.append(f"رستر {index_name} برای «{label}» تولید شد.")
            elif isinstance(valid_cell_count, int):
                parts.append(f"رستر «{label}» با {valid_cell_count} سلول معتبر تولید شد.")
            elif isinstance(cell_count, int):
                parts.append(f"رستر «{label}» با {cell_count} سلول تولید شد.")
            else:
                parts.append(f"خروجی رستری «{label}» تولید شد.")
            continue

        parts.append(f"خروجی «{label}» تولید شد.")

    if not parts:
        return "درخواست با موفقیت انجام شد."

    return " ".join(parts)


def _english_output_answer(outputs_summary: dict[str, Any]) -> str:
    parts: list[str] = []

    for name, summary in outputs_summary.items():
        if not isinstance(summary, dict):
            continue

        kind = summary.get("kind")
        feature_count = summary.get("feature_count")
        valid_cell_count = summary.get("valid_cell_count")
        index_name = summary.get("index_name")

        label = _friendly_output_name(name)

        if kind == "vector":
            if isinstance(feature_count, int):
                parts.append(f"{feature_count} vector features were generated for '{label}'.")
            else:
                parts.append(f"Vector output '{label}' was generated.")
            continue

        if kind == "raster":
            if index_name:
                parts.append(f"{index_name} raster was generated for '{label}'.")
            elif isinstance(valid_cell_count, int):
                parts.append(f"Raster '{label}' was generated with {valid_cell_count} valid cells.")
            else:
                parts.append(f"Raster output '{label}' was generated.")
            continue

        parts.append(f"Output '{label}' was generated.")

    if not parts:
        return "Request completed successfully."

    return " ".join(parts)


def _friendly_output_name(name: str) -> str:
    mapping = {
        "vegetation_polygons": "پلیگون‌های پوشش گیاهی",
        "thresholded_raster": "رستر آستانه‌گذاری‌شده",
        "spectral_index": "شاخص طیفی",
        "ndvi": "NDVI",
    }

    return mapping.get(name, str(name).replace("_", " "))


def _summarize_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}

    for name, value in outputs.items():
        payload = _object_to_dict(value)

        kind = payload.get("kind")

        row: dict[str, Any] = {}

        if kind:
            row["kind"] = kind

        features = payload.get("features")
        data = payload.get("data")

        if isinstance(features, list):
            row["kind"] = row.get("kind", "vector")
            row["feature_count"] = len(features)

        if data is not None:
            row["kind"] = row.get("kind", "raster")

        if not row:
            row["kind"] = type(value).__name__

        summary[str(name)] = row

    return summary


def _score_from_plan(plan: Any | None) -> float | None:
    if plan is None:
        return None

    evidence = getattr(plan, "routing_evidence", None)

    if not isinstance(evidence, list) or not evidence:
        return None

    scores = []

    for item in evidence:
        score = _field(item, "score")

        if isinstance(score, (int, float)):
            scores.append(float(score))

    if not scores:
        return None

    return max(scores)


def _level_from_score(score: Any) -> str | None:
    if not isinstance(score, (int, float)):
        return None

    score = float(score)

    if score >= 0.75:
        return "high"

    if score >= 0.45:
        return "medium"

    return "low"


def _plan_steps(plan: Any | None) -> int | None:
    if plan is None:
        return None

    nodes = getattr(plan, "nodes", None)

    if isinstance(nodes, list):
        return len(nodes)

    steps = getattr(plan, "steps", None)

    if isinstance(steps, list):
        return len(steps)

    return None


def _field(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)

    try:
        return getattr(value, key)
    except Exception:
        return None


def _object_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)

    if hasattr(value, "to_dict") and callable(value.to_dict):
        result = value.to_dict()

        if isinstance(result, dict):
            return result

        return {
            "value": result,
        }

    if is_dataclass(value):
        return asdict(value)

    payload = dict(getattr(value, "__dict__", {}) or {})

    if payload:
        return payload

    return {
        "value": value,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())

    if is_dataclass(value):
        return _json_safe(asdict(value))

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict) and payload:
        return _json_safe(payload)

    return repr(value)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        text = str(item)

        if text in seen:
            continue

        seen.add(text)
        result.append(text)

    return result
