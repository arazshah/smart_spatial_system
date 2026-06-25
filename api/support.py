from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import Request

from orchestrator.service import OrchestratorService


def http_error_detail(exc: Exception) -> Any:
    """
    Convert service/application exceptions to FastAPI HTTPException detail.
    """
    detail = getattr(exc, "detail", None)

    if detail is not None:
        return json_safe(detail)

    return str(exc)


def service(request: Request) -> OrchestratorService:
    """
    Return the app-level OrchestratorService from FastAPI state.
    """
    return request.app.state.service


def json_safe(value: Any) -> Any:
    """
    Convert application objects to JSON-safe values.

    This intentionally accepts common DTO shapes used across the orchestrator:
    - dataclasses
    - pathlib.Path
    - mappings/lists/tuples
    - objects exposing to_dict()
    - pydantic-like objects exposing model_dump()
    - lightweight SDK objects exposing __dict__
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))

    if isinstance(value, Path):
        return str(value)

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return json_safe(value.to_dict())

    if hasattr(value, "model_dump") and callable(value.model_dump):
        return json_safe(value.model_dump())

    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            json_safe(item)
            for item in value
        ]

    if hasattr(value, "__dict__"):
        return {
            str(key): json_safe(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }

    return value
